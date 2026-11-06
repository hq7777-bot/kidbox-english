# 豆豆 kidbox — 一年级英语语音盒子（可运行骨架）

> **状态：可移植、可离线跑通。** 不需要 API key、不需要麦克风、不需要买硬件，
> 整个文件夹拷到另一台电脑也能验证完整链路（播放 → 断点 → 重播 → 复习 → 打断）。
>
> ```bash
> python -m kidbox.doctor                                  # 体检：缺什么、怎么修
> python -m kidbox.main --script demo_script.txt --no-tts   # 无人值守跑一遍（先看这个）
> python -m kidbox.main --text                             # 打字对话
> ```
> Windows 双击：`setup.bat` → `start_script.bat` → `start_text.bat`。
> 详细说明见项目根目录 [`快速开始.md`](../快速开始.md)。

## 0. 三种大脑 / 三种播放（改 `.env` 即可，不动代码）

| 开关 | 取值 | 说明 |
| --- | --- | --- |
| `KIDBOX_BRAIN` | `auto`（默认）/ `deepseek` / `mock` | auto = 有 `DEEPSEEK_API_KEY` 就用大模型，没有就用离线关键词规则 |
| `KIDBOX_PLAYER` | `auto` / `null` / `ffplay` | null = 静默只跑逻辑，适合在别人电脑上验收 |
| `KIDBOX_TTS` | `1` / `0` | 关掉只打印文字，不联网合成 |

`mock` 不是"降级 AI"，而是用同一批工具（`play_next` / `play_resume` / `repeat_audio` /
`next_quiz_word` / `explain`…）把真实链路跑一遍——能验证进度落库、断点续播、复习盒子，
只是不会自由对话。接上 DeepSeek 后，同一套工具改由模型调度。

一个"语音进、语音出"的英语学习盒子：叫一声就醒，说"下一课"就播音频，说"为什么天会下雨"就联网查了用孩子能懂的话讲。

**设计原则：音频在本地，决策在云端。** 断网时音频照样能播，大模型只是"大脑"，不是"喇叭"。

## 1. 先在本机跑通（不买硬件、不接麦克风）

```bash
# 需要 Python 3.10+、ffmpeg（提供 ffplay）
pip install -r kidbox/requirements.txt
cd D:/研究产出/儿童英语AI硬件

# 1) 纯逻辑自测：不需要 API、不需要麦克风、不出声
python -m kidbox.selftest

# 2) 键盘对话：需要 DEEPSEEK_API_KEY（不配也能跑，只是会回"转不过来"）
python -m kidbox.main --text

# 3) 只跑一条指令，看有没有按预期调工具
python -m kidbox.main --once "播放第一课"
```

`--text` 模式下 TTS 仍然出声（装了 `edge-tts` 就会说话），方便听语气。

## 2. 配置

```bash
cp kidbox/config.example.env kidbox/config.env   # 改成你的 key
# Windows(git-bash)
set -a; . kidbox/config.env; set +a
# Linux / 树莓派
export $(grep -v '^#' kidbox/config.env | xargs)
```

必填只有两个：`DEEPSEEK_API_KEY`（大脑）、`SILICONFLOW_API_KEY`（耳朵）。
`KIDBOX_SEARCH` 默认 `none`——**故意不默认联网**，因为搜索 API 要钱且需要挑供应商；不配时模型会明说"我没查到"，不会编。

## 3. 放音频（这是内容工作，不是编程工作）

```
library/
  L1/
    001_hello/
      meta.json
      01.mp3         # 一句话一条，方便"重播这句"
      02.mp3
    002_colors/
  L2/
    001_animals/
```

`meta.json`：

```json
{
  "title": "Hello",
  "text": "Hello! Hi! I am Anna.",
  "words": ["hello", "hi", "am"]
}
```

- 目录名以数字开头 → `ord`（第几课）。**级别用 L1/L2/L3 命名**，扫描按名字自然排序。
- 一条音频 = 一句/一段。**别把一课压成一个 10 分钟大文件**，否则"重播这句""断点续播"都没法做。
- `words` 会自动进单词库，按 Leitner 盒子排复习（答对进下一盒，答错回第 1 盒，间隔 1/2/4/7/15 天）。

## 4. 代码结构

| 文件 | 作用 | 关键点 |
|---|---|---|
| `config.py` | 全部配置走环境变量 | 换机器/换 key 不用改代码 |
| `db.py` | SQLite：课程、进度、单词、日志、kv | WAL 模式，断电不写坏 |
| `library.py` | 扫目录 → 入库 | 幂等，改了目录重跑即可 |
| `player.py` | ffplay 播放 + 进度 | 暂停=杀掉记住秒数，继续=从该秒重开 |
| `asr.py` | 录音（响度 VAD）+ 识别 | 门限对童声宽容一点 |
| `tts.py` | edge-tts 合成 + 缓存 | 固定话术不重复请求 |
| `brain.py` | DeepSeek function calling 循环 | 最多 4 轮工具调用，超时兜底 |
| `tools.py` | 12 个工具 = AI 的"手脚" | 播放类返回 `brief=True`，避免 TTS 盖住音频 |
| `listen.py` | 唤醒词 + 播放中打断 | 没麦自动退化成回车 |
| `main.py` | 状态机主循环 | 每 2 秒快照进度 |
| `selftest.py` | 22 项逻辑自检 | 不需要任何外部服务 |

## 5. AI 能调的工具

| 工具 | 孩子会怎么说 | 说明 |
|---|---|---|
| `play_next` | 下一课 / 学新的 | 当前级别下一课，学完自动回第一课 |
| `play_resume` | 接着听 / 继续上次 | 从上次停下的音频+秒数继续 |
| `play_lesson` | 听 colors / 第五课 | 按关键词或序号定位 |
| `set_level` | 我想听第二级 / 复习前面的 | 切级别 |
| `repeat_audio` | 再听一遍 / 没听清 | 当前这条重播 |
| `stop_playback` | 别放了 | 停 |
| `set_speed` | 慢一点 | 0.5~1.5 倍（ffplay atempo） |
| `set_volume` | 大声点 | 硬上限 `KIDBOX_MAX_VOLUME` |
| `next_quiz_word` | 我们记单词 | 取到期单词 |
| `grade_word` | —（模型自己判） | 记录对错，更新复习间隔 |
| `explain` | 恐龙为什么灭绝 | 先检索，再让孩子能懂的话讲 |
| `progress_report` | 我学到哪了 | 级别/当前课/待复习数 |

> 关键设计：**工具不是关键词匹配**。孩子说"这个我听不懂，慢一点再来一次"，模型会组合 `set_speed` + `repeat_audio`；说"我们换个动物听听"它会 `play_lesson(keyword=动物)`。这是拿 DeepSeek 换掉"指令词表"的真正收益。

## 6. 声卡 / 麦克风

```bash
# 看设备名
python -c "import sounddevice; print(sounddevice.query_devices())"
```

- **强烈建议用带回声消除（AEC）的 USB 会议全向麦**（自带喇叭的那种）。普通 USB 音箱 + 独立麦，喇叭声音会被自己收进去 → 播放中打不断、或者自己把自己打断。
- 树莓派上先 `alsamixer` 设好默认声卡，再用 `speaker-test -c1 -twav` 确认。

## 7. 上机（树莓派/迷你主机）

```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip portaudio19-dev libsndfile1
cd ~/kidbox && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install edge-tts
python -m kidbox.selftest          # 先确认逻辑没问题
python -m kidbox.main              # 再跑语音
```

开机自启（含崩溃重启）：

```ini
# /etc/systemd/system/kidbox.service
[Unit]
Description=kidbox voice tutor
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/kidbox
EnvironmentFile=/home/pi/kidbox/config.env
ExecStart=/home/pi/kidbox/.venv/bin/python -m kidbox.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now kidbox && journalctl -u kidbox -f
```

## 8. 已知限制（诚实清单）

1. **唤醒词是英文**（`Hey Jarvis`）。想用"小豆小豆"要换 sherpa-onnx KWS，见 `listen.py` 顶部注释。对学英语的孩子，英文唤醒词其实算"顺带的英语输入"。
2. **打断依赖 AEC 麦克风**，代码里用"响度门限 + 二次确认"降低误触发，不是真正的全双工。
3. **没有口语发音打分**。`grade_word` 是让大模型判断"意思对不对"，不是音素级评测。要打分得接专门的口语评测 API（讯飞/微软/ELSA），这一段没写。
4. **流式没做**。现在是"录完→识别→回答→合成"，端到端 2~4 秒。要更快得把 ASR/TTS 换成流式。
5. **单用户**。没做声纹，全家说话都会被当成孩子。
