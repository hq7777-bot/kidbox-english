#!/usr/bin/env python3
"""apply_hybrid_patch.py —— 给 xiaozhi-esp32 打两个补丁（1. 并联唤醒  2. 纯文字界面）

用法（在任意目录）：
    python apply_hybrid_patch.py --check  D:\\xiaozhi-esp32     # 只检查能不能打
    python apply_hybrid_patch.py          D:\\xiaozhi-esp32     # 真的改
    python apply_hybrid_patch.py --revert D:\\xiaozhi-esp32     # 撤销（从 .bak 恢复）

改了哪些文件：
    main/Kconfig.projbuild                     + 一个字符串配置项
    main/audio/wake_words/custom_wake_word.h    + 成员
    main/audio/wake_words/custom_wake_word.cc   + WakeNet 并联 + 去重
    main/boards/espressif/esp-vocat/config.json + 一个 build profile

所有改动都是"插入/替换整段"，锚点找不到就报错退出，不会写坏文件。
每个被改的文件会留一份 .bak（只留第一次的原始版本）。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

MARK = "/* ==== HYBRID_WAKE_WORD_PATCH ==== */"

# ---------------------------------------------------------------- 1. Kconfig
KCONFIG_ANCHOR = "config SEND_WAKE_WORD_DATA\n"
KCONFIG_ADD = """config CUSTOM_WAKE_WORD_PRIMARY_DISPLAY
    string "Parallel WakeNet wake word (display name)"
    default "你好小鑫"
    depends on USE_CUSTOM_WAKE_WORD
    help
        Non-empty = run a WakeNet model (found in assets) in parallel with MultiNet.
        WakeNet is the fast path (~200ms), MultiNet keeps the aliases.
        Leave empty to disable the parallel path.

"""

# ---------------------------------------------------------------- 2. header
H_INCLUDE_ANCHOR = "#include <esp_mn_models.h>\n"
H_INCLUDE_ADD = """#include <esp_mn_models.h>
#include <esp_wn_iface.h>
#include <esp_wn_models.h>
#include <esp_timer.h>
"""

H_MEMBER_ANCHOR = "    void FeedSamples(const int16_t* data, size_t samples, bool mono);\n"
H_MEMBER_ADD = """    // ---- 并联 WakeNet（主唤醒词，快路径）----
    esp_wn_iface_t* wakenet_iface_ = nullptr;
    model_iface_data_t* wakenet_model_data_ = nullptr;
    char* wn_name_ = nullptr;
    std::string primary_word_;
    std::vector<int16_t> wn_buffer_;
    int64_t last_wake_us_ = 0;
    static constexpr int64_t kDedupWindowUs = 1200 * 1000;  // 1.2 秒内不重复唤醒
    void InitWakenet();
    void TryEmitWake(const std::string& word);

    void FeedSamples(const int16_t* data, size_t samples, bool mono);
"""

# ---------------------------------------------------------------- 3. .cc
C_DTOR_ANCHOR = "CustomWakeWord::~CustomWakeWord() {\n"
C_DTOR_ADD = """CustomWakeWord::~CustomWakeWord() {
    if (wakenet_model_data_ != nullptr && wakenet_iface_ != nullptr) {
        wakenet_iface_->destroy(wakenet_model_data_);
        wakenet_model_data_ = nullptr;
    }
"""

C_INIT_ANCHOR = "    multinet_->print_active_speech_commands(multinet_model_data_);\n"
C_INIT_ADD = """    multinet_->print_active_speech_commands(multinet_model_data_);

    InitWakenet();
"""

C_STOP_OLD = """void CustomWakeWord::Stop() {
    running_ = false;

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    input_buffer_.clear();
}"""

C_STOP_NEW = """void CustomWakeWord::Stop() {
    running_ = false;

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    input_buffer_.clear();
    wn_buffer_.clear();
}"""

FEED_START = "void CustomWakeWord::FeedSamples(const int16_t* data, size_t samples, bool mono) {"
FEED_END = "size_t CustomWakeWord::GetFeedSize() {"

_BODY = (Path(__file__).parent / "patch_assets" / "wake_word_body.cc")
FEED_NEW = MARK + _BODY.read_text(encoding="utf-8")

GETWORDS_OLD = """std::vector<std::string> CustomWakeWord::GetWakeWords() const {
    std::vector<std::string> words;
    if (multinet_model_data_ == nullptr) {
        return words;
    }"""

GETWORDS_NEW = """std::vector<std::string> CustomWakeWord::GetWakeWords() const {
    std::vector<std::string> words;
    if (!primary_word_.empty()) {
        words.push_back(primary_word_);
    }
    if (multinet_model_data_ == nullptr) {
        return words;
    }"""

# ---------------------------------------------------------------- 4. config.json
BUILD_PROFILE = {
    "name": "esp-vocat-hybrid-text",
    "sdkconfig_append": [
        # ---- 界面：关表情，用气泡聊天（能看出谁说的） ----
        "CONFIG_USE_EMOTE_MESSAGE_STYLE=n",
        "CONFIG_USE_WECHAT_MESSAGE_STYLE=y",
        "CONFIG_USE_MULTILINE_CHAT_MESSAGE=y",
        "CONFIG_FLASH_EXPRESSION_ASSETS=n",
        "CONFIG_MMAP_FILE_NAME_LENGTH=32",
        # ---- 唤醒：MultiNet 走别名，WakeNet 走主词 ----
        "CONFIG_WAKE_WORD_DISABLED=n",
        "CONFIG_USE_ESP_WAKE_WORD=n",
        "CONFIG_USE_AFE_WAKE_WORD=n",
        "CONFIG_USE_CUSTOM_WAKE_WORD=y",
        'CONFIG_CUSTOM_WAKE_WORD="hai xiao xin|xiao xin tong xue|ha lou xiao xin"',
        'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="嗨小鑫|小鑫同学|哈喽小鑫"',
        'CONFIG_CUSTOM_WAKE_WORD_PRIMARY_DISPLAY="你好小鑫"',
        "CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=15",
        "CONFIG_SEND_WAKE_WORD_DATA=y",
        "CONFIG_USE_DEVICE_AEC=y",
        "CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y",
        # ---- 模型：WakeNet 主词 + MultiNet 中文 ----
        "CONFIG_SR_WN_WN9_NIHAOXIAOXIN_TTS=y",
        "CONFIG_SR_MN_CN_MULTINET7_AC_QUANT=y",
        "CONFIG_SR_MN_CN_MULTINET7_QUANT=n",
        "CONFIG_SR_MN_CN_MULTINET6_QUANT=n",
    ],
}


def die(msg):
    print(f"\n[失败] {msg}", file=sys.stderr)
    sys.exit(1)


def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(p, bak)
    return bak


def read_raw(p: Path) -> str:
    # newline="" —— 关键：不做换行符转换，避免把整个文件变成 CRLF（diff 会全红）
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_raw(p: Path, text: str):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def patch_text(p: Path, pairs, check_only):
    """pairs: [(旧串, 新串, 说明)]，每条都必须恰好出现一次"""
    t = read_raw(p)
    if MARK in t:
        print(f"  [跳过] {p.name} 已经打过补丁")
        return False
    for old, new, note in pairs:
        n = t.count(old)
        if n != 1:
            die(f"{p} 里找不到唯一锚点（{note}），出现 {n} 次。\n"
                f"你的仓库版本可能和我核对的不一致，请把这段发我：\n{old[:200]}")
        t = t.replace(old, new, 1)
    if not check_only:
        backup(p)
        write_raw(p, t)
    print(f"  [{'检查通过' if check_only else '已修改'}] {p}")
    return True


def patch_feed(p: Path, check_only):
    t = read_raw(p)
    if MARK in t:
        print(f"  [跳过] {p.name} 的 FeedSamples 已打过补丁")
        return
    i = t.find(FEED_START)
    j = t.find(FEED_END)
    if i < 0 or j < 0 or j <= i:
        die(f"{p} 里找不到 FeedSamples / GetFeedSize 的边界")
    t = t[:i] + FEED_NEW + t[j:]
    if not check_only:
        write_raw(p, t)


def build_profile_text(indent: str, nl: str) -> str:
    """按目标文件的缩进/换行风格，拼出要插进 builds 数组的那一段"""
    def q(v):
        return json.dumps(v, ensure_ascii=False)

    lines = [indent + "{"]
    lines.append(f'{indent}    "name": {q(BUILD_PROFILE["name"])},')
    lines.append(f'{indent}    "sdkconfig_append": [')
    for i, opt in enumerate(BUILD_PROFILE["sdkconfig_append"]):
        comma = "," if i < len(BUILD_PROFILE["sdkconfig_append"]) - 1 else ""
        lines.append(f"{indent}        {q(opt)}{comma}")
    lines.append(f"{indent}    ]")
    lines.append(f"{indent}}}")
    return nl.join(lines)


def patch_config_json(p: Path, check_only):
    """文本插入（不重排 JSON），保持原文件风格，diff 只有新增那几行"""
    t = read_raw(p)
    if BUILD_PROFILE["name"] in t:
        print(f"  [跳过] config.json 已有 {BUILD_PROFILE['name']}")
        return
    nl = "\r\n" if "\r\n" in t else "\n"
    anchor = '"builds": ['
    i = t.find(anchor)
    if i < 0:
        die(f"{p} 里找不到 {anchor}")
    # 定位 builds 数组里第一层的缩进（通常是 8 个空格）
    line_start = t.rfind(nl, 0, i) + len(nl)
    base_indent = t[line_start:i]              # 例如 "    "
    indent = base_indent + "    "
    insert_at = i + len(anchor)
    block = nl + build_profile_text(indent, nl) + ","
    new = t[:insert_at] + block + t[insert_at:]
    if not check_only:
        backup(p)
        write_raw(p, new)
    # 无论哪种模式都做一次 JSON 合法性校验
    json.loads(new)
    print(f"  [{'检查通过' if check_only else '已添加'}] config.json → {BUILD_PROFILE['name']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="xiaozhi-esp32 仓库根目录")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    ap.add_argument("--revert", action="store_true", help="从 .bak 恢复")
    a = ap.parse_args()

    root = Path(a.repo).resolve()
    if not (root / "main" / "audio").is_dir():
        die(f"{root} 看起来不是 xiaozhi-esp32 仓库根目录（没有 main/audio）")

    targets = {
        "kconfig": root / "main" / "Kconfig.projbuild",
        "header": root / "main" / "audio" / "wake_words" / "custom_wake_word.h",
        "source": root / "main" / "audio" / "wake_words" / "custom_wake_word.cc",
        "board": root / "main" / "boards" / "espressif" / "esp-vocat" / "config.json",
    }
    for k, p in targets.items():
        if not p.exists():
            die(f"缺少文件：{p}")

    if a.revert:
        for p in targets.values():
            bak = p.with_suffix(p.suffix + ".bak")
            if bak.exists():
                shutil.copy2(bak, p)
                print(f"  [已恢复] {p.name}")
        return

    print(f"目标仓库：{root}")
    print(f"{'检查模式' if a.check else '写入模式'}\n")

    patch_text(targets["kconfig"], [(KCONFIG_ANCHOR, KCONFIG_ADD + KCONFIG_ANCHOR, "Kconfig")], a.check)
    patch_text(targets["header"], [
        (H_INCLUDE_ANCHOR, H_INCLUDE_ADD, "头文件 include"),
        (H_MEMBER_ANCHOR, H_MEMBER_ADD, "头文件成员"),
    ], a.check)

    src = targets["source"]
    if MARK not in src.read_text(encoding="utf-8"):
        # 顺序很重要：先做小的替换，再整段换 FeedSamples
        patch_text(src, [
            (C_DTOR_ANCHOR, C_DTOR_ADD, "析构函数"),
            (C_INIT_ANCHOR, C_INIT_ADD, "Initialize 尾部"),
            (C_STOP_OLD, C_STOP_NEW, "Stop()"),
            (GETWORDS_OLD, GETWORDS_NEW, "GetWakeWords()"),
        ], a.check)
        patch_feed(src, a.check)
        print(f"  [{'检查通过' if a.check else '已修改'}] custom_wake_word.cc（FeedSamples 整段替换）")
    else:
        print("  [跳过] custom_wake_word.cc 已打过补丁")

    patch_config_json(targets["board"], a.check)

    print("\n完成。下一步：")
    print("  1) 编译： python scripts/build.py espressif/esp-vocat --name esp-vocat-hybrid-text")
    print("     （board 是位置参数，--name 对应 config.json 里 build.name）")
    print("  2) 烧录（只刷 app+assets，保住 WiFi）：")
    print("     py -3.13 -m esptool --chip esp32s3 -p COM3 -b 460800 write-flash \\")
    print("        0x20000 build/xiaozhi.bin 0x800000 build/configured_assets.bin")
    print("  3) 看串口，应出现：并联 WakeNet 已启用: model=wn9_nihaoxiaoxin_tts word=你好小鑫")
    print("\n想撤销： python apply_hybrid_patch.py --revert " + str(root))


if __name__ == "__main__":
    main()
