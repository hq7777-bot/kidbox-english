"""第三步（v3）：按类型定位切点 + 清洗 ID3 标签。

相比 v2 的三处修改（按用户反馈）：
  1. Song（原 Vocab 轨）：一直切到 "Here's our song for today."，之前的指令不要
  2. Literature：**保留标题**（不再跳过标题块）
  3. 输出不再继承源文件的 GBK 标签（那会把 title/artist 显示成乱码），
     统一写成干净的英文标签

三类切点：
  Story      报幕 → BGM 歌曲 → 故事正文      切在 BGM 之后
  Song       报幕 → 指令 → "Here's our song for today." → 歌曲    切在该句
  Literature 报幕 → 标题 → 故事               切在标题（保留）
"""
import os
import re
import json
import sys
import subprocess

WORK = r"G:\xiaozhi-server\data\blocks"
ASR = r"G:\xiaozhi-server\data\blocks_asr.txt"
OUT = r"F:\英语学习资料\POWER UP\熟听剪辑\POWER UP 1"
DRY = "--dry" in sys.argv

ANN = re.compile(r"(pupil.?s book|unit\s*\d|page[s]?\s*\d|track\s*\d)", re.I)
SONG = re.compile(r"(here'?s our song|our song for today)", re.I)
SONG_ALT = re.compile(r"(sing the song)", re.I)


def clean(t):
    return re.sub(r"<\|[^|]*\|>", "", t or "").strip()


def main():
    man = json.load(open(os.path.join(WORK, "manifest.json"), encoding="utf-8"))
    texts = {}
    for line in open(ASR, encoding="utf-8", errors="replace"):
        if "\t" in line:
            k, v = line.rstrip("\n").split("\t", 1)
            texts[k] = v

    if not DRY:
        os.makedirs(OUT, exist_ok=True)

    print(f"{'#':>3s} {'类型':11s} {'切点':>6s} {'原长':>6s} {'新长':>6s}  依据")
    done = 0
    for m in man:
        seq, blocks, kind = m["seq"], m["blocks"], m["kind"]
        if kind == "Vocab":
            kind = "Song"   # 旧 manifest 里叫 Vocab，统一成 Song
        info = []
        for b in blocks:
            raw = texts.get(f"{seq:02d}/{b['file']}", "")
            info.append({**b, "raw": raw, "text": clean(raw), "bgm": "<|BGM|>" in (raw or "")})

        ann = [i for i, b in enumerate(info[:8]) if ANN.search(b["text"])]
        base = (ann[-1] + 1) if ann else 0
        why = f"报幕到 {info[ann[-1]]['end']:.1f}s" if ann else "无报幕"

        if kind == "Story":
            # 跳过一个短块（标题），再跳过 BGM
            if (base < len(info) and info[base]["text"]
                    and len(info[base]["text"].split()) <= 4
                    and base + 1 < len(info) and info[base + 1]["text"]):
                why += f"，跳过标题「{info[base]['text'][:14]}」"
                base += 1
            cut = max(0.0, info[base]["start"] - 0.2) if base < len(info) else 2.0
            bgm = [i for i, b in enumerate(info) if b["bgm"] and i >= base]
            if bgm:
                last = bgm[-1]
                nxt = next((b for b in info[last + 1:] if b["text"] and not b["bgm"]), None)
                if nxt:
                    cut = max(0.0, nxt["start"] - 0.2)
                    why += f"；BGM 到 {info[last]['end']:.1f}s→正文 {nxt['start']:.1f}s「{nxt['text'][:18]}」"

        elif kind == "Song":
            # 切到 "Here's our song for today."（含这一句）
            hit = next((b for b in info if SONG.search(b["text"])), None)
            if hit is None:
                hit = next((b for b in info if SONG_ALT.search(b["text"])), None)
            if hit is not None:
                idx = info.index(hit)
                nxt = info[idx + 1] if idx + 1 < len(info) else None
                if nxt is not None:
                    cut = max(0.0, nxt["start"] - 0.2)
                    why += (f"；去掉「{hit['text'][:22]}」，从 {nxt['start']:.1f}s"
                            f"「{nxt['text'][:16]}」开始")
                else:
                    cut = max(0.0, hit["end"] + 0.3)
                    why += f"；「{hit['text'][:22]}」后无内容块"
            else:
                if (base < len(info) and info[base]["text"]
                        and len(info[base]["text"].split()) <= 4
                        and base + 1 < len(info) and info[base + 1]["text"]):
                    base += 1
                cut = max(0.0, info[base]["start"] - 0.2) if base < len(info) else 2.0
                why += "；未找到歌曲提示句，回退报幕后切"

        else:  # Literature：保留标题，不跳块
            cut = max(0.0, info[base]["start"] - 0.2) if base < len(info) else 2.0
            if base < len(info):
                why += f"；保留标题「{info[base]['text'][:18]}」@{info[base]['start']:.1f}s"

        cut = min(cut, 50.0, max(0.0, m["dur"] - 10))
        name = f"P1-{seq:02d}-U{m['unit_no']:02d}-{kind}.mp3"
        print(f"{seq:3d} {kind:11s} {cut:6.1f} {m['dur']:6.1f} {m['dur']-cut:6.1f}  {why}")

        if not DRY:
            title = f"P1 U{m['unit_no']:02d} {kind}"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-ss", f"{cut:.2f}", "-i", m["src"],
                "-map_metadata", "-1",                    # 丢掉源文件里的 GBK 标签
                "-metadata", f"title={title}",
                "-metadata", "artist=POWER UP 1",
                "-metadata", "album=POWER UP 1 熟听剪辑",
                "-metadata", f"track={seq}",
                "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", "64k",
                "-id3v2_version", "3",                    # 3 = 兼容性最好
                os.path.join(OUT, name)], check=True)
            done += 1

    print(f"\n{'计划' if DRY else '已生成'} {done if not DRY else len(man)} 个 → {OUT}")


if __name__ == "__main__":
    main()
