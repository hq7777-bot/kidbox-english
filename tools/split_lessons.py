r"""按静音把整篇教材音频切成一句一个文件。

输出：G:/xiaozhi-server/data/follow/SmartReading3/<3-x>/<Uxx-Passage|KeyWords>/001.mp3 ...
用法：
    python split_lessons.py --one 3-3/U15-Passage     # 冒烟测试
    python split_lessons.py --all                     # 全量 Book 3
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SRC_ROOT = Path(r"G:\xiaozhi-server\data\lessons\SmartReading3")
DST_ROOT = Path(r"G:\xiaozhi-server\data\follow\SmartReading3")

NOISE = "-35dB"
MIN_SIL = 0.35      # 判定为静音的最短时长
MIN_SEG = 0.60      # 丢弃过短的碎片
MERGE_GAP = 0.25    # 相邻片段间隔小于此值就合并
PAD = 0.15          # 每句前后留白，避免削掉首尾音


def run(cmd, text=True):
    return subprocess.run(cmd, capture_output=True, text=text, encoding="utf-8", errors="replace")


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def detect_silence(path):
    r = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
             f"silencedetect=noise={NOISE}:d={MIN_SIL}", "-f", "null", "-"])
    out = (r.stderr or "")
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", out)]
    sil = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None:
            continue
        sil.append((max(0.0, s), e))
    return sil


def segments(path):
    total = duration(path)
    sil = detect_silence(path)
    segs = []
    cursor = 0.0
    for s, e in sil:
        if s - cursor >= MIN_SEG:
            segs.append((cursor, s))
        cursor = max(cursor, e)
    if total - cursor >= MIN_SEG:
        segs.append((cursor, total))

    # 合并过短的碎片
    merged = []
    for seg in segs:
        if merged and (seg[0] - merged[-1][1]) < MERGE_GAP and (seg[1] - seg[0]) < MIN_SEG:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(list(seg) if isinstance(seg, list) else list(seg))
            merged[-1] = list(seg)
    # 再过滤一遍过短
    out = [(a, b) for a, b in merged if (b - a) >= MIN_SEG]
    return [(round(a, 2), round(b, 2)) for a, b in out], total, len(sil)


def cut(src, out_dir, segs):
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, (a, b) in enumerate(segs, 1):
        ss = max(0.0, a - PAD)
        to = b + PAD
        dst = out_dir / f"{i:03d}.mp3"
        r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{ss:.2f}", "-to", f"{to:.2f}", "-i", str(src),
                 "-c", "copy", str(dst)])
        if dst.exists() and dst.stat().st_size > 2000:
            made.append((dst.name, round(to - ss, 2)))
        else:
            print(f"    [X] 切片失败 {dst.name}: {(r.stderr or '')[:120]}")
    return made


def process(rel):
    src = SRC_ROOT / f"{rel}.mp3"
    if not src.exists():
        print(f"  [X] 源文件不存在: {src}")
        return None
    book, name = rel.split("/")
    segs, total, n_sil = segments(src)
    made = cut(src, DST_ROOT / book / name, segs)
    print(f"  {rel}: {total:.1f}s, 静音点 {n_sil} 个 → 切成 {len(made)} 句")
    for n, d in made[:6]:
        print(f"      {n}  {d}s")
    if len(made) > 6:
        print(f"      ... 共 {len(made)} 句")
    return {"source": rel, "total": total, "segments": made}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", help="单个相对路径，如 3-3/U15-Passage")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    if a.one:
        process(a.one)
        return 0
    if a.all:
        files = sorted(p.relative_to(SRC_ROOT).with_suffix("").as_posix() for p in SRC_ROOT.rglob("*.mp3"))
        print(f"共 {len(files)} 个文件")
        for i, rel in enumerate(files, 1):
            print(f"[{i}/{len(files)}] ", end="")
            process(rel)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
