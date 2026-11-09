void CustomWakeWord::InitWakenet() {
#ifdef CONFIG_CUSTOM_WAKE_WORD_PRIMARY_DISPLAY
    if (models_ == nullptr || models_->num <= 0) {
        ESP_LOGW(TAG, "没有模型列表，跳过并联 WakeNet");
        return;
    }
    wn_name_ = esp_srmodel_filter(models_, ESP_WN_PREFIX, language_.c_str());
    if (wn_name_ == nullptr) {
        wn_name_ = esp_srmodel_filter(models_, ESP_WN_PREFIX, nullptr);
    }
    if (wn_name_ == nullptr) {
        ESP_LOGW(TAG, "assets 里没有 WakeNet 模型，退化为纯 MultiNet（唤醒会慢一点）");
        return;
    }
    wakenet_iface_ = (esp_wn_iface_t*)esp_wn_handle_from_name(wn_name_);
    if (wakenet_iface_ == nullptr) {
        ESP_LOGW(TAG, "拿不到 WakeNet 接口: %s", wn_name_);
        return;
    }
    wakenet_model_data_ = wakenet_iface_->create(wn_name_, DET_MODE_95);
    if (wakenet_model_data_ == nullptr) {
        ESP_LOGW(TAG, "WakeNet 模型创建失败: %s", wn_name_);
        return;
    }
    primary_word_ = CONFIG_CUSTOM_WAKE_WORD_PRIMARY_DISPLAY;
    if (primary_word_.empty()) {
        wakenet_iface_->destroy(wakenet_model_data_);
        wakenet_model_data_ = nullptr;
        ESP_LOGI(TAG, "CUSTOM_WAKE_WORD_PRIMARY_DISPLAY 为空，只用 MultiNet");
        return;
    }
    wn_buffer_.reserve(wakenet_iface_->get_samp_chunksize(wakenet_model_data_));
    ESP_LOGI(TAG, "并联 WakeNet 已启用: model=%s word=%s chunk=%d",
             wn_name_, primary_word_.c_str(),
             wakenet_iface_->get_samp_chunksize(wakenet_model_data_));
#else
    ESP_LOGW(TAG, "未定义 CUSTOM_WAKE_WORD_PRIMARY_DISPLAY，只用 MultiNet");
#endif
}

void CustomWakeWord::TryEmitWake(const std::string& word) {
    int64_t now = esp_timer_get_time();
    if (last_wake_us_ != 0 && (now - last_wake_us_) < kDedupWindowUs) {
        ESP_LOGD(TAG, "去重：距上次唤醒 %lld ms，忽略重复命中(%s)",
                 (long long)((now - last_wake_us_) / 1000), word.c_str());
        return;
    }
    last_wake_us_ = now;
    last_detected_wake_word_ = word;
    running_ = false;
    input_buffer_.clear();
    wn_buffer_.clear();
    ESP_LOGI(TAG, "唤醒：%s", word.c_str());
    if (wake_word_detected_callback_) {
        wake_word_detected_callback_(last_detected_wake_word_);
    }
}

void CustomWakeWord::FeedSamples(const int16_t* data, size_t samples, bool mono) {
    if (data == nullptr || samples == 0) {
        return;
    }

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    if (!running_) {
        return;
    }

    const bool need_downmix = (!mono && codec_ != nullptr && codec_->input_channels() > 1);
    const int ch = need_downmix ? codec_->input_channels() : 1;

    // 统一成单声道（多声道取左声道），两个模型共用同一份数据
    std::vector<int16_t> mono_buf;
    mono_buf.reserve(samples / ch + 1);
    if (need_downmix) {
        for (size_t i = 0; i + (size_t)ch <= samples; i += (size_t)ch) {
            mono_buf.push_back(data[i]);
        }
    } else {
        mono_buf.assign(data, data + samples);
    }
    if (mono_buf.empty()) {
        return;
    }

#if CONFIG_SEND_WAKE_WORD_DATA
    // 连续缓存：唤醒后要把"喊的那一句"上传给服务端。
    // 必须在两个模型之前写入，否则 WakeNet 命中时缓存里还没有这段音频。
    wake_word_audio_cache_.Store(mono_buf.data(), mono_buf.size());
#endif

    // ---------- 快路径：WakeNet（主词，~200ms）----------
    if (wakenet_model_data_ != nullptr && wakenet_iface_ != nullptr) {
        const int wn_chunk = wakenet_iface_->get_samp_chunksize(wakenet_model_data_);
        if (wn_chunk > 0) {
            wn_buffer_.insert(wn_buffer_.end(), mono_buf.begin(), mono_buf.end());
            while ((int)wn_buffer_.size() >= wn_chunk) {
                int r = wakenet_iface_->detect(wakenet_model_data_, wn_buffer_.data());
                if (r > 0) {
                    ESP_LOGI(TAG, "WakeNet 命中: %s", primary_word_.c_str());
                    TryEmitWake(primary_word_);
                    return;  // 已唤醒，本轮不再喂 MultiNet（避免同一句话二次唤醒）
                }
                wn_buffer_.erase(wn_buffer_.begin(), wn_buffer_.begin() + wn_chunk);
            }
        }
    }

    // ---------- 慢路径：MultiNet（别名，~600ms）----------
    if (multinet_model_data_ == nullptr) {
        return;
    }

    input_buffer_.insert(input_buffer_.end(), mono_buf.begin(), mono_buf.end());

    const int chunksize = multinet_->get_samp_chunksize(multinet_model_data_);
    if (chunksize <= 0) {
        return;
    }
    while (input_buffer_.size() >= (size_t)chunksize) {
        esp_mn_state_t mn_state = multinet_->detect(multinet_model_data_, input_buffer_.data());

        if (mn_state == ESP_MN_STATE_DETECTED) {
            esp_mn_results_t *mn_result = multinet_->get_results(multinet_model_data_);
            for (int i = 0; i < mn_result->num && running_; i++) {
                ESP_LOGI(TAG, "MultiNet 命中: command_id=%d, string=%s, prob=%f",
                        mn_result->command_id[i], mn_result->string, mn_result->prob[i]);
                int idx = mn_result->command_id[i] - 1;
                if (idx >= 0 && idx < (int)commands_.size()) {
                    auto& command = commands_[idx];
                    if (command.action == "wake") {
                        TryEmitWake(command.text);
                    }
                }
            }
            multinet_->clean(multinet_model_data_);
        } else if (mn_state == ESP_MN_STATE_TIMEOUT) {
            ESP_LOGD(TAG, "Command word detection timeout, cleaning state");
            multinet_->clean(multinet_model_data_);
        }

        if (!running_) {
            break;
        }
        input_buffer_.erase(input_buffer_.begin(), input_buffer_.begin() + chunksize);
    }
}

