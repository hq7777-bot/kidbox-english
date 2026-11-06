"""split_audio.py —— 把一段长音频切成"一句一文件"，或只生成时间轴 JSON。

两种用法：

  1) 静音切分（不需要任何模型，靠 ffmpeg 找停顿）
     python split_audio.py lesson.mp3 --out ./out
     python split_audio.py lesson.mp3 --out ./out --json-only

  2) 带文本的时间轴（需要 faster-whisper，能同时出文字，最准）
     pip install faster-whisper
     python split_audio.py lesson.mp3 --out ./out --whisper

输出：
  out/timeline.json   [{"index":1,"start":0.0,"end":2.4,"text":"..."}]
  out/001.mp3 ...     一句一个文件（--json-only 时不生成）

设计取舍：
  - 静音切分简单但会把"句中的停顿"也当断点（尤其逗号处），准确率约 80~90%，
    所以**必须人工听一遍**，改 timeline.json 里的数字比重新切便宜得多。
  - 有原文时用 --whisper：whisper 出词级时间戳，再按标点合并成句，准确得多。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PAD = 0.12          # 每句前后各留 120ms，避免吃掉首尾音
MIN_SEG = 0.55      # 短于这个的片段并入相邻句（防止把 "a" 这种切出来）


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout + p.stderr


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(out.strip().splitlines()[0])
    except Exception:
        return 0.0


def detect_silence(path, noise="-35dB", min_sil=0.35):
    """返回 [(start, end), ...] 静音区间"""
    out = run(["ffmpeg", "-hide_banner", "-i", str(path),
               "-af", f"silencedetect=noise={noise}:d={min_sil}",
               "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", out)]
    pairs = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration(path)
        pairs.append((s, e))
    return pairs


def segments_from_silence(path, silence, total):
    """静音区间 → 语句区间"""
    segs = []
    cursor = 0.0
    for s, e in silence:
        if s - cursor > 0.15:
            segs.append((cursor, s))
        cursor = e
    if total - cursor > 0.15:
        segs.append((cursor, total))
    # 合并过短片段
    merged = []
    for seg in segs:
        if merged and (seg[1] - seg[0] < MIN_SEG):
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)
    # 前后留白
    out = []
    for i, (s, e) in enumerate(merged):
        out.append({"index": i + 1,
                    "start": round(max(0.0, s - PAD), 2),
                    "end": round(min(total, e + PAD), 2),
                    "text": ""})
    return out


def from_whisper(path):
    """词级时间戳 → 按标点合并成句"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("没装 faster-whisper：pip install faster-whisper", file=sys.stderr)
        return None
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), language="en", word_timestamps=True,
                               vad_filter=True)
    sentences, cur = [], {"start": None, "end": None, "text": ""}
    for s in segs:
        for w in (s.words or []):
            if cur["start"] is None:
                cur["start"] = w.start
            cur["text"] = (cur["text"] + " " + w.word).strip()
            cur["end"] = w.end
            if re.search(r"[.!?]$", w.word.strip()):
                sentences.append(cur)
                cur = {"start": None, "end": None, "text": ""}
    if cur["text"]:
        sentences.append(cur)
    for i, s in enumerate(sentences):
        s["index"] = i + 1
        s["start"] = round(max(0.0, s["start"] - PAD), 2)
        s["end"] = round(s["end"] + PAD, 2)
    return sentences


def cut(src, out_dir, timeline):
    out_dir.mkdir(parents=True, exist_ok=True)
    for it in timeline:
        dst = out_dir / f"{it['index']:03d}.mp3"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{it['start']:.2f}", "-to", f"{it['end']:.2f}",
             "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", str(dst)])
        if not dst.exists() or dst.stat().st_size < 500:
            print(f"警告：第 {it['index']} 句切分失败", file=sys.stderr)
    return len(timeline)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--whisper", action="store_true", help="用 faster-whisper 出文本+时间轴")
    ap.add_argument("--json-only", action="store_true", help="只生成时间轴，不切文件")
    ap.add_argument("--noise", default="-35dB")
    ap.add_argument("--min-sil", type=float, default=0.35)
    a = ap.parse_args()

    src = Path(a.audio)
    if not src.exists():
        sys.exit(f"找不到 {src}")
    if not shutil.which("ffmpeg"):
        sys.exit("需要 ffmpeg：winget install Gyan.FFmpeg 或 apt install ffmpeg")

    out = Path(a.out)
    total = duration(src)
    print(f"[输入] {src.name}  时长 {total:.1f}s")

    if a.whisper:
        timeline = from_whisper(src)
        if timeline is None:
            sys.exit(1)
    else:
        sil = detect_silence(src, a.noise, a.min_sil)
        print(f"[静音] 找到 {len(sil)} 处停顿")
        timeline = segments_from_silence(src, sil, total)

    out.mkdir(parents=True, exist_ok=True)
    (out / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[句子] {len(timeline)} 句 → {out/'timeline.json'}")
    for it in timeline[:8]:
        print(f"   {it['index']:02d}  {it['start']:6.2f} → {it['end']:6.2f}s  {it['text']}")
    if len(timeline) > 8:
        print(f"   … 还有 {len(timeline) - 8} 句")

    if not a.json_only:
        n = cut(src, out, timeline)
        print(f"[切分] 生成 {n} 个 mp3 → {out}")
    print("\n下一步：听一遍，把切错的边界在 timeline.json 里改数字，"
          "再用 --json-only 重跑一遍覆盖文件（或直接手改）。")


if __name__ == "__main__":
    main()
