# 路线 C 落地：设备当手和嘴，PC 当脑子

```
┌──────── 设备（手机 Termux / 树莓派 / 旧笔记本）────────┐
│  device_agent.py  :8788                              │
│    · /context  音频库 + 当前课 + 最近播放（含秒数）     │
│    · /play     播放任意音频（可指定每条从第几秒开始）   │
│    · /stop     停止并保存进度                         │
│    · /speak    播一段 TTS mp3/wav                     │
│  voice_loop.py                                        │
│    · 录音 → POST 给 PC → 播放回来的回答                │
└───────────────────────┬───────────────────────────────┘
                        │ 局域网 HTTP
┌───────────────────────▼───────────────────────────────┐
│  pc_brain.py  :8787                                   │
│    · 预算上下文（少一轮工具调用，省 0.8 秒）            │
│    · DeepSeek function calling（严格 JSON Schema）     │
│    · TTS：本地 SAPI 1.1 秒 / edge-tts 3.0 秒           │
└───────────────────────────────────────────────────────┘
```

## 实测数据（本机，2026-11-04）

| 环节 | 实测 | 备注 |
|---|---|---|
| DeepSeek 单轮往返 | **0.70 ~ 0.83 s** | `deepseek-chat`，杭州家宽 |
| 一次多动作指令（2 轮工具调用 + 回复） | **1.3 ~ 1.7 s** | 不含 TTS |
| TTS：Windows SAPI（本地） | **1.1 s** | 含 0.9 s 进程启动 |
| TTS：edge-tts（云端） | **3.0 s** | 音质更好，但慢一倍 |
| **端到端（不含 ASR）** | **2.0 ~ 3.0 s** | 播放类 / 知识类 |
| 端到端（如果大脑用 `hermes chat -q`） | **48.3 s** ❌ | 见下 |

> **为什么大脑不用 `hermes chat -q`**：实测 48.3 秒/句，而且它会把 `files` 传成
> `[{"file": ...}]` 这种结构（终端 + curl 没有参数校验），把设备线程搞崩。
> Hermes 适合"不赶时间"的活（夜间整理词表、生成听力材料、复盘对话），
> 不适合逐句对话。逐句对话必须是有 schema 校验的工具调用。

## 跑起来（Windows，先在本机验证）

```bash
cd bridge
pip install requests pyttsx3          # pyttsx3 只 Windows 需要
# DEEPSEEK_API_KEY 会从 ~/AppData/Local/hermes/.env 自动读

# 终端 1：设备（假播放，不出声，只看日志）
set KIDBOX_FAKE=1
python device_agent.py

# 终端 2：大脑
python pc_brain.py

# 终端 3：发一句话
curl -s -X POST http://127.0.0.1:8787/ask_text -H "Content-Type: application/json" \
     -d "{\"text\":\"复习上两个英语音频，然后再播放下一篇\"}"
```

想真出声：去掉 `KIDBOX_FAKE`，设备端要装 ffmpeg（提供 `ffplay`）。

## 跑起来（设备端）

### 树莓派 / Linux 小主机
```bash
sudo apt install -y ffmpeg python3-venv portaudio19-dev
pip install requests sounddevice numpy
export KIDBOX_LIBRARY=/home/pi/kidbox/library
python device_agent.py &
python voice_loop.py --pc http://<PC的局域网IP>:8787 --device http://127.0.0.1:8788
```

### 安卓手机（Termux）
```bash
pkg install python ffmpeg termux-api
pip install requests
termux-wake-lock                       # 防止后台被杀
python device_agent.py &
python voice_loop.py --pc http://<PC的局域网IP>:8787 --termux
```

手机端注意：**先在系统设置里给 Termux 关掉电池优化**，否则锁屏几分钟就断。

## 用一台闲置笔记本同时当"设备 + 大脑"（最快跑通）

先用一台机器，把三个进程都跑起来，别急着拆：

```bat
start_windows.bat          :: 双击，会开两个黑窗 + 一个对话窗
```

它等价于：

```bash
set KIDBOX_DEVICE=http://127.0.0.1:8788
set KIDBOX_PUBLIC=http://127.0.0.1:8787
python device_agent.py     # 设备（喇叭+麦克风+音频库）
python pc_brain.py         # 大脑
python voice_loop.py --pc http://127.0.0.1:8787 --device http://127.0.0.1:8788
```

### 笔记本当设备必须改的 6 个设置（Windows）

| # | 设置 | 在哪 | 为什么 |
|---|---|---|---|
| 1 | 电源计划 → 永不睡眠、关闭硬盘=从不 | 控制面板 → 电源选项 | 一睡服务就断，孩子喊不应 |
| 2 | 合盖时"不采取任何操作" | 电源选项 → 选择关闭盖子的功能 | 合盖当无屏设备用 |
| 3 | 关自动更新重启 | 设置 → Windows 更新 → 活动时间 | 半夜重启 = 早上哑巴 |
| 4 | 麦克风开"回声消除/降噪" | 设置 → 声音 → 输入设备属性 | 喇叭声别被自己收进去，否则永远打不断 |
| 5 | 屏幕亮度调 0 / 合盖 | — | 有屏会分心，但调试期留着看日志很方便 |
| 6 | 关掉睡眠时的"快速启动" | 电源选项 → 选择电源按钮功能 | 快速启动会让音频设备偶发失灵 |

> 实测：这台机器的输入设备是 `麦克风阵列（英特尔智音技术）`，双通道，自带阵列降噪——
> 这种麦克风比 DIY 的单个 INMP441 更适合做"播放中打断"。笔记本反而比树莓派省事。

### 什么时候才需要换成树莓派

- 要**孩子拿不走、没有屏幕、巴掌大**的最终形态；
- 要 5W 低功耗 24h 常开（笔记本 15~45W）；
- 不想听风扇声。

迁移成本几乎为零：代码一样，只改 `--pc http://<笔记本IP>:8787` 这一处。所以先用笔记本，最后再搬。

## 自动测试

```bash
python test_e2e.py            # DeepSeek 后端，8 项断言
python test_e2e.py hermes     # Hermes 后端（慢，用来对照）
```

自测会在 `bridge/library/` 造 5 课假音频，跑完可以删。

## 已知限制

1. **ASR 还没接**：`pc_brain.py` 的 `stt()` 支持本地 faster-whisper（`pip install faster-whisper`，零 key）或硅基流动；没配就返回空。
2. **没有唤醒词**：设备端是"按回车/按一下说话"。要做唤醒得在设备本地跑 openWakeWord（树莓派可行，手机受后台限制）。
3. **TTS 音色**：SAPI 的 Huihui 清晰但不像小孩；edge-tts 的 `zh-CN-XiaoyiNeural` 更柔和，慢 2 秒。可以按场景混用：确认语用 SAPI，讲故事用 edge。
4. **单用户**：没有声纹，全家说话都当孩子。
