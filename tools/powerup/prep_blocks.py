"""第一步：把 28 个目标音频的开头切成说话块（供 ASR 判断报幕/BGM）。

输出：G:\\xiaozhi-server\\data\\blocks\\<序号>\\bXX.wav  和 manifest.json
"""
import os
import re
import glob
import json
import shutil
import subprocess

BASE = r"F:\英语学习资料\POWER UP\POWER UP 1级 资料\1级别音频\学生用书音频"
WORK = r"G:\xiaozhi-server\data\blocks"
SEC = 60.0

KINDS = [
    ("Story", lambda n: "friendly farm" in n.lower() or "family farm" in n.lower()),
    ("Vocab", lambda n: "vocabulary2-1" in n.lower()),
    ("Literature", lambda n: "literature" in n.lower()),
]


def unit_no(folder):
    m = re.match(r"Unit\s*(\d+)", folder)
    return int(m.group(1)) if m else 0


def dur(f):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


def silences(wav):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af", "silencedetect=noise=-35dB:d=0.25",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out, cur = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1))))
            cur = None
    if cur is not None:
        out.append((cur, SEC))
    return out


shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

units = sorted([d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d))],
               key=lambda x: (unit_no(x), x))
manifest, seq = [], 0
for u in units:
    d = os.path.join(BASE, u)
    files = sorted(glob.glob(os.path.join(d, "*.mp3")))
    for kind, match in KINDS:
        hit = next((f for f in files if match(os.path.basename(f))), None)
        if not hit:
            continue
        seq += 1
        sub = os.path.join(WORK, f"{seq:02d}")
        os.makedirs(sub, exist_ok=True)
        head = os.path.join(sub, "head.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", str(SEC), "-i", hit,
                        "-ac", "1", "-ar", "16000", head], check=True)
        sil = silences(head)
        blocks, t = [], 0.0
        for s, e in sil:
            if s - t > 0.35:
                blocks.append((t, s))
            t = e
        if SEC - t > 0.35:
            blocks.append((t, SEC))
        keep = []
        for i, (s, e) in enumerate(blocks[:12]):
            out = os.path.join(sub, f"b{i:02d}.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.2f}", "-to", f"{e:.2f}",
                            "-i", head, out], check=True)
            keep.append({"i": i, "start": round(s, 2), "end": round(e, 2), "file": f"b{i:02d}.wav"})
        manifest.append({"seq": seq, "unit": u, "kind": kind, "unit_no": unit_no(u),
                         "src": hit, "dur": round(dur(hit), 1), "blocks": keep})
        print(f"  {seq:02d} {u[:22]:24s} {kind:11s} {len(keep)} 块  {os.path.basename(hit)}")

json.dump(manifest, open(os.path.join(WORK, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n共 {len(manifest)} 个文件，块目录 {WORK}")
