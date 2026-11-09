# POWER UP 1 熟听素材剪裁

> 目标：把《POWER UP》1 级学生用书音频里"报幕 + 音乐前奏"剪掉，只留正文，转成熟听用的短音频。

---

## 1. 源素材

```
F:\英语学习资料\POWER UP\POWER UP 1级 资料\1级别音频\学生用书音频\
├─ Hello Unit\                     ※ 只有 Story（The family farm），无 Vocab/Literature
├─ Unit1单元 Our new school\       1.07 … 1.19
├─ Unit2单元 All about us\         …
└─ … Unit9单元 Happy holidays\
```

每个单元挑 3 条：

| 标签 | 源文件名 | 说明 |
|---|---|---|
| `Story` | `The friendly farm.mp3` / `The family farm.mp3` | 故事 |
| `Vocab` | `Vocabulary2-1.mp3` | 词汇＋跟唱引导（注意：**纯歌曲是 `Sing the song.mp3`**，本批按你点的文件名取的） |
| `Literature` | `Literature.mp3` | 文学阅读 |

---

## 2. 开头结构（实测，28 条全部一致）

```
[1.2s 静音] [报幕 4 短块] [BGM 歌曲] [正文]
             Pupil's book.
             Unit 1.
             Page 8.
             The friendly farm.      ← 标题块
```

各类型差异：

| 类型 | 报幕 | 后面是什么 | 切点 |
|---|---|---|---|
| Story | 4 短块 | **BGM 歌曲 ~17~38 秒** → 故事 | 歌曲结束处（26~42 秒） |
| Vocab | 4 短块 | 指令（Listen and do the actions…）→ **歌曲正文** | 报幕结束（7~10 秒），歌曲保留 |
| Literature | 4 短块（含 Pages xx and yy） | 标题 → 故事（**无 BGM**） | 报幕+标题后（8~12 秒） |

> 一句话：**只有 Story 的 BGM 是前奏，Vocab 的 BGM 就是正文（要跟唱的那首歌）**。

---

## 3. 输出

```
F:\英语学习资料\POWER UP\熟听剪辑\POWER UP 1\
P1-01-U00-Story.mp3        ← Hello Unit
P1-02-U01-Story.mp3
P1-03-U01-Vocab.mp3
P1-04-U01-Literature.mp3
… 共 28 个
```

规格：单声道 / 22050Hz / 64kbps MP3，合计 **67.8 分钟，31.1 MB**（原 28 条共约 82 分钟）。

---

## 4. 三步流水线（可复跑）

```bash
# ① 切块：每条取前 60 秒，按静音切成"说话块"
python tools/powerup/prep_blocks.py
#   → G:\xiaozhi-server\data\blocks\<序号>\bXX.wav + manifest.json

# ② 逐块 ASR（容器内，用 SenseVoice 判断哪块是报幕、哪块带 BGM）
docker cp tools/powerup/asr_blocks_all.py xiaozhi-esp32-server:/tmp/
docker exec xiaozhi-esp32-server python /tmp/asr_blocks_all.py /opt/xiaozhi-esp32-server/data/blocks \
    > G:/xiaozhi-server/data/blocks_asr.txt

# ③ 定位切点并生成
python tools/powerup/trim_powerup2.py --dry   # 先看切点
python tools/powerup/trim_powerup2.py         # 真生成
```

### 判定逻辑（`trim_powerup2.py`）

1. 在前 8 个块里找**最后一个报幕块**（正则 `pupil's book|unit N|page N|track N`）
2. 跳过其后 1 个短块（≤4 词，通常是标题 `The first day.` 或序号 `1.`）
3. 若类型是 `Story` 且后面还有带 `<|BGM|>` 标签的块 → 切在**最后一段 BGM 之后的第一个内容块**
4. 否则切在第 2 步定位处

---

## 5. 踩坑记录

| 坑 | 现象 | 解决 |
|---|---|---|
| `ffmpeg -v error` 吞掉静音检测 | `silencedetect` 一条都不输出，误判"没有静音" | 用 `-hide_banner`（默认 info 级），不要压到 error |
| 中文路径 | ffmpeg 直接报 `Illegal byte sequence` | 用 Python `subprocess` 传参（内部是宽字符），不要用 bash 拼路径 |
| 静音检测抓不到音乐前奏 | 只切掉 1.2 秒静音和报幕，BGM 留着 → 用户听感"前奏没删" | 必须靠 ASR 的 `<|BGM|>` 标签判断，不能靠能量/静音 |
| Vocab 一刀切会误删歌曲 | 把 `Sing the song` 那段当"前奏"删了 | 按类型分开：Vocab 不切 BGM |

---

## 6. 待办

- [ ] 接入设备熟听模式：`review_play` 目前写死 `SmartReading3` + 16 单元，需要支持"多书库 + 任意条数"
- [ ] 若确认 `Vocabulary2-1` 不是你要的"歌曲"，改 `KINDS` 里的匹配串为 `sing the song` 重跑
