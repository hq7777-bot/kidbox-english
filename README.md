# 豆豆盒子 · 一年级英语学习 AI 硬件

> 用一块 ESP-VoCat（小智固件）+ 一台旧笔记本当"大脑"，做一个**只有语音、没有屏幕干扰**的英语学习机：
> 一句一句放教材音频、等孩子跟读、记进度、按遗忘曲线复习、还能回答"恐龙为什么灭绝"。

---

## 1. 现在到哪一步了

| 项 | 状态 |
|---|---|
| 硬件选型 | ✅ 已购 **ESP-VoCat 1.0**（ESP32-S3，1.85" 圆屏，双麦阵列，官方适配 `espressif/esp-vocat`） |
| 官方账号 | ✅ 已注册 xiaozhi.me |
| 架构决策 | ✅ 设备 = 瘦终端；服务端 = 家里旧笔记本自建；大脑 = DeepSeek |
| 本地原型 | ✅ `kidbox/` 可跑（22 项自检通过），验证"语音 → 决策 → 播放 → 记进度"整条链 |
| 混合链路验证 | ✅ `bridge/` 两个进程模拟"设备 + 大脑"，端到端 2~3 秒 |
| 服务端插件 | ✅ `plugin/play_music.py` 已实测（重写匹配+播报）、`plugin/follow_reading.py` 跟读 v1 已跑通 |
| 切句工具 | ✅ `tools/split_audio.py`、`tools/split_lessons.py`（1356 句实测） |
| 部署 | ✅ 已完成（2026-11-08，见 `docs/04_本机部署实录.md`） |

---

## 2. 需求清单（带验收标准）

| ID | 需求 | 验收标准（可测） | 现状 |
|---|---|---|---|
| **R1** | 语音唤醒、24h 待机 | 3 米内唤醒率 ≥ 90%；误唤醒 ≤ 1 次/2h；断电恢复自动重连 | 固件自带（ESP-SR WakeNet，本地） |
| **R2** | 逐句播放教材音频 | 说"第三课"→ ≤ 3 秒出声，播的是对的文件 | 待开发（服务端插件） |
| **R3** | 断点续播 | 拔电重启 → 说"接着听"→ 回到上次那一句 | 待开发（进度 JSON） |
| **R4** | 分级 + 复习 | 级别可切换；错句按 1/2/4/7/15 天排复习 | 待开发（Leitner，kidbox 已验证算法） |
| **R5** | 跟读模式 | 一句播完停住等孩子；读完 ≤ 2 秒接下一句；读错重播 | 待开发（见 `02_跟读模式设计.md`） |
| **R6** | 科普问答 | 问"为什么天会下雨"→ 用孩子能懂的话回答；不知道就说不知道 | 官方服务器可；自建需加搜索工具 |
| **R7** | 学习记录 | 家长能看：今天听了哪几课、哪几句老读错 | 待开发（日志 + 周报） |
| **R8** | 儿童安全 | 音量硬上限；单次 25 分钟提醒；无屏幕菜单 | 部分（固件 + 音量限幅） |
| **R9** | 成本与延迟 | 端到端 ≤ 4 秒；月成本 ≤ 30 元 | 实测 2~3 秒（不含 ASR）；约 10~30 元/月 |

**明确不做的**：音素级发音打分（需第三方评测 API，v3 再说）、屏幕菜单/游戏、摄像头。

---

## 3. 架构

```
      客厅                          书房（旧笔记本，24h 开）
┌──────────────┐   WiFi    ┌─────────────────────────────────┐
│  ESP-VoCat   │           │  xiaozhi-esp32-server（Docker）  │
│  · 唤醒(本地) │──Opus────▶│   ① ASR  SenseVoice（本地免费）  │
│  · 录音       │           │   ② LLM  DeepSeek               │
│  · 喇叭/圆屏  │◀─音频帧───│   ③ TTS  edge-tts               │
└──────────────┘           │   ④ 工具 play_lesson / play_music│
                           │   ⑤ 进度 + 复习（JSON/SQLite）   │
                           │   ⑥ 教材音频 data/lessons/       │
                           └─────────────────────────────────┘
```

**三条铁律**（都是查源码/实测确认的）：

1. **设备不读 SD 卡** → 音频必须在服务端，用 `ContentType.FILE` 推流；
2. **一台设备同一时刻只连一个服务端** → 官方与自建二选一（改 OTA 地址切换，2 分钟）；
3. **官方服务器不开放自定义工具** → 要"播我的音频"，必须自建。

---

## 4. 里程碑

| 里程碑 | 内容 | 工期 | 验收 |
|---|---|---|---|
| **M0** | 刷官方固件、连官方服务器 | 1 小时 | 孩子能跟它对话 |
| **M1** | 旧笔记本跑起自建服务端，播一个自己的 mp3 | 1~2 小时 | 说"播放 001"听到自己的文件 |
| **M2** | `play_lesson` 插件 + 分级目录 + 进度 | 2~3 天 | 说"第三课"播对；拔电后"接着听"回到原处 |
| **M3** | 跟读模式 | 2~3 天 | 一句一停，读完接下一句，读错重播 |
| **M4** | 内容流水线（切句 + 导入 + 校验） | 2 天 | 丢一个 5 分钟音频进去，自动出 40~60 句 |

**M1 是最关键的关卡**：它证明"服务端能把任意本地 mp3 推给设备播"。过了这一关，后面都是写判断。

---

## 5. 代码地图

| 路径 | 是什么 | 状态 |
|---|---|---|
| `kidbox/` | **本地原型**：唤醒→录音→ASR→DeepSeek→播放→进度，全套 Python | ✅ 22 项自检通过 |
| `kidbox/db.py` | SQLite：课程、进度、单词 Leitner、对话日志 | ✅ |
| `kidbox/tools.py` | 12 个场景工具（play_next / repeat / quiz…） | ✅ |
| `bridge/device_agent.py` | 设备端 HTTP 服务（/play /stop /context /speak） | ✅ 端到端测过 |
| `bridge/pc_brain.py` | PC 大脑（DeepSeek 工具调用 + TTS） | ✅ 2~3 秒 |
| `bridge/test_e2e.py` | 端到端自动测试（8 项断言） | ✅ 通过 |
| `bridge/xiaozhi_plugin/play_lesson.py` | **小智服务端插件（旧稿）**，已被 `plugin/` 下实测版本取代 | 🟡 保留参考 |
| `plugin/play_music.py` | **生产用**：口语化匹配（`book三点三15课课文`）+ 只播序号播报 | ✅ 实测 |
| `plugin/follow_reading.py` | **生产用**：逐句跟读状态机（v1） | ✅ 实测中 |
| `plugin/loop_play.py` | **生产用**：整册 16 篇连播 / 循环 N 遍（磨耳朵背景音） | ✅ 逻辑已验 |
| `tools/split_lessons.py` | 教材整篇 → 逐句切分（ffmpeg 静音检测） | ✅ 1356 句 |
| `tools/asr_segments.py` | 逐句 ASR，产出 `raw.json`（容器内跑） | ✅ 96 单元 |
| `tools/build_lesson_json.py` | ASR 结果 LLM 校对 → `lesson.json` | ✅ 96 单元 |
| `tools/flash_vocat.py` | 一键刷机（自动找串口/识别 ESP32-S3） | ✅ |
| `patches/xiaozhi-esp32-custom-wakeword.patch` | 上游固件自定义唤醒词补丁（构建脚本+板级配置） | ✅ |
| `bridge/sapi_say.py` | Windows 本地 TTS（子进程，避免 pyttsx3 死锁） | ✅ |
| `tools/split_audio.py` | 长音频切句 / 生成时间轴 | ✅ 实测 |
| `build_html.py` | md → 带样式 html（打印用） | ✅ |

**为什么有两套（kidbox / bridge）**：`kidbox` 是验证"逻辑对不对"的沙盘，不依赖任何硬件；`bridge` 是验证"设备+大脑分离"这条链；最终的正式形态是 `xiaozhi_plugin`。前者是设计依据，后者是交付物。

---

## 6. 需要的 GitHub 吗

**技术上不需要**：所有东西都在本地跑，GitHub 不参与运行。

**但强烈建议用**，五个理由：

| 理由 | 场景 |
|---|---|
| 备份 | 旧笔记本坏了、硬盘挂了，代码和配置还在 |
| 多机同步 | 笔记本部署、台式机当备份、以后换 NAS |
| 版本历史 | 改 `config.yaml` 改坏了能回滚 |
| 保护插件 | 服务端升级会覆盖文件，插件用 git 管着不丢 |
| 以后开源 | 想分享或卖，仓库现成 |

### 一定要遵守的两条

1. **用私有仓库**（Private）。不要公开——教材音频和孩子的录音不能外传。
2. **绝不提交密钥**：`api_key` 只放本地 `.config.yaml`，仓库里放 `.config.yaml.example` 占位。

### 仓库结构

```
kidbox-english/
├─ README.md                 ← 本文
├─ docs/
│   ├─ 01_开箱手册_VoCat.md
│   ├─ 02_跟读模式设计.md
│   ├─ 03_服务端部署清单.md
│   └─ 方案与DIY教程.md
├─ plugin/
│   └─ play_lesson.py        ← 丢进服务端的插件
├─ tools/
│   └─ split_audio.py
├─ proto/                    ← 本地原型（可选保留）
│   └─ kidbox/
├─ deploy/
│   ├─ docker-compose.yml
│   └─ .config.yaml.example  ← 只有占位符，没有真 key
└─ .gitignore
```

`.gitignore` 必须包含：

```gitignore
# 密钥与本地配置
.config.yaml
.env
*key*.txt
# 音频与生成物（版权 + 体积）
*.mp3
*.wav
lessons/
library/
# 数据
*.db
progress.json
.cache/
audio/
# Python
__pycache__/
*.pyc
# 系统
.DS_Store
Thumbs.db
```

### 上传命令（三步）

```bash
cd D:\研究产出\儿童英语AI硬件
git init
git add .
git commit -m "init: 需求、设计、插件、工具"
# 在 GitHub 建一个 Private 仓库，然后：
git remote add origin https://github.com/<你的账号>/kidbox-english.git
git push -u origin main
```

> 不想用 GitHub 也行：本地 `git init` + 定期把 `data/.config.yaml` 和 `data/lessons/` 拷到网盘，效果差不多。

---

## 7. 已知限制（别踩）

| 限制 | 影响 | 对策 |
|---|---|---|
| 设备不读 SD | 音频必须放服务端 | 服务端推流（已定方案） |
| 一机只连一个服务端 | 官方/自建二选一 | 改 OTA 地址切换 |
| 官方服务器不开放工具 | 官方播不了你的音频 | 自建 |
| 跟读判定只能做到"文本相似度" | 不评音素 | v1 宽松判定，v3 接评测 API |
| 童声识别 | 决定体验上限 | 先实测；不行换讯飞/阿里 |
| 服务端要 24h 在线 | 笔记本不能睡 | 电源设置 + 自带电池当 UPS |

---

## 8. 文档索引

| 文档 | 什么时候看 |
|---|---|
| `docs/01_开箱手册_VoCat.md` | 板子到手，从拆箱到能对话 |
| `docs/03_服务端部署清单.md` | 要把大脑搬到自己电脑 |
| `docs/02_跟读模式设计.md` | 做跟读功能前 |
| `docs/儿童英语AI硬件_方案与DIY教程.md` | 想搞清"为什么这么选"（20 节，含市面产品对比、选板、协议细节） |
