"""生成示例教材音频（不需要任何 API key，用 edge-tts 本地合成）。

用途：服务端刚部署好时，用它生成两课示例音频，验证"说播放 001 → 听到声音"。

    python make_sample_lessons.py --out ../data/lessons

输出：
    lessons/L1/001_hello/{001,002,003}.mp3 + lesson.json
    lessons/L1/002_colors/{001,002}.mp3   + lesson.json
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

VOICE = "en-US-AnaNeural"   # 童声，语速适中

LESSONS = {
    "L1/001_hello": {
        "title": "Hello",
        "sentences": [
            "Hello! How are you?",
            "I am fine, thank you.",
            "Goodbye! See you tomorrow.",
        ],
    },
    "L1/002_colors": {
        "title": "Colors",
        "sentences": [
            "Look at the red apple.",
            "The sky is blue.",
        ],
    },
}


def tts_candidates():
    """edge-tts 可能装在别的 Python 里，按顺序试。"""
    out = []
    env = __import__("os").getenv("KIDBOX_TTS_CMD")
    if env:
        out.append(env.split())
    if sys.platform.startswith("win"):
        out += [["py", "-3.13", "-m", "edge_tts"], ["python", "-m", "edge_tts"], ["edge-tts"]]
    else:
        out += [[sys.executable, "-m", "edge_tts"], ["edge-tts"]]
    return out


def synth(text, out_path):
    for cmd in tts_candidates():
        try:
            subprocess.run(cmd + ["--voice", VOICE, "--text", text,
                                  "--write-media", str(out_path)],
                           check=True, capture_output=True, timeout=120)
            if out_path.exists() and out_path.stat().st_size > 1000:
                return True
        except Exception:
            continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./lessons")
    a = ap.parse_args()
    root = Path(a.out)

    total = 0
    for lesson, info in LESSONS.items():
        d = root / lesson
        d.mkdir(parents=True, exist_ok=True)
        meta = {"title": info["title"], "sentences": []}
        for i, text in enumerate(info["sentences"], 1):
            name = f"{i:03d}.mp3"
            p = d / name
            if not (p.exists() and p.stat().st_size > 1000):
                if not synth(text, p):
                    print(f"合成失败：{text}\n  提示：pip install edge-tts", file=sys.stderr)
                    return 1
            meta["sentences"].append({"file": name, "text": text, "min_score": 0.55})
            total += 1
            print(f"  {lesson}/{name}  {text}")

        (d / "lesson.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：{len(LESSONS)} 课 / {total} 句 → {root.resolve()}")
    print("下一步：对设备说「播放 001」，应该听到 Hello! How are you?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
