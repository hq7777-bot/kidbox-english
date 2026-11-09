"""第三步（v2）：按类型定位报幕结束点，重新裁剪。

三类音频的开头结构不一样（实测）：
  Story      : 报幕(Pupil's book / Unit / Page / 标题) → BGM 歌曲前奏 → 故事正文
  Vocab      : 报幕 → 指令(Listen and do the actions / Then sing the song) → **歌曲正文**
                （BGM 就是正文，不能删）
  Literature : 报幕 → 标题 → 故事正文（无 BGM）

规则：
  1. 找最后一个报幕块（pupil's book|unit N|page N|track N）
  2. 跳过其后的 1 个短块（标题/序号，如 "The first day." / "1."）
  3. Story：若后面还有 BGM 块，切在最后一段 BGM 之后的第一个内容块
  4. 其余：切在第 2 步定位的块处
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
        info = []
        for b in blocks:
            raw = texts.get(f"{seq:02d}/{b['file']}", "")
            info.append({**b, "raw": raw, "text": clean(raw), "bgm": "<|BGM|>" in (raw or "")})

        # 1) 最后一个报幕块
        ann = [i for i, b in enumerate(info[:8]) if ANN.search(b["text"])]
        base = (ann[-1] + 1) if ann else 0

        # 2) 跳过 1 个短块（标题 / 序号）
        skipped = ""
        if (base < len(info) and info[base]["text"]
                and len(info[base]["text"].split()) <= 4
                and base + 1 < len(info) and info[base + 1]["text"]):
            skipped = info[base]["text"]
            base += 1

        why = f"报幕到 {info[ann[-1]]['end']:.1f}s" if ann else "无报幕"
        if skipped:
            why += f"，跳过标题「{skipped[:16]}」"

        if base < len(info):
            cut = max(0.0, info[base]["start"] - 0.2)
        else:
            cut = 2.0
            why += "，块用尽→回退2s"

        # 3) Story：再跳过 BGM 歌曲前奏
        if kind == "Story":
            bgm = [i for i, b in enumerate(info) if b["bgm"] and i >= base]
            if bgm:
                last = bgm[-1]
                nxt = next((b for b in info[last + 1:] if b["text"] and not b["bgm"]), None)
                if nxt:
                    cut = max(0.0, nxt["start"] - 0.2)
                    why += f"；BGM 到 {info[last]['end']:.1f}s→正文 {nxt['start']:.1f}s「{nxt['text'][:20]}」"

        cut = min(cut, 50.0, max(0.0, m["dur"] - 10))
        name = f"P1-{seq:02d}-U{m['unit_no']:02d}-{kind}.mp3"
        print(f"{seq:3d} {kind:11s} {cut:6.1f} {m['dur']:6.1f} {m['dur']-cut:6.1f}  {why}")

        if not DRY:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cut:.2f}", "-i", m["src"],
                            "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", "64k",
                            os.path.join(OUT, name)], check=True)
            done += 1

    print(f"\n{'计划' if DRY else '已生成'} {done if not DRY else len(man)} 个 → {OUT}")


if __name__ == "__main__":
    main()
