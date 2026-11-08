"""用 Windows SAPI (Microsoft Zira) + ffmpeg 生成示例教材音频。
edge-tts 被微软 403 拦了，本地 SAPI 不需要网络。
输出结构与 make_sample_lessons.py 一致：L1/001_hello/{001,002,003}.mp3 + lesson.json
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

VOICE = "Microsoft Zira Desktop"

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

PS = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.SelectVoice('{voice}'); "
    "$s.Rate = -1; "
    "$s.SetOutputToWaveFile('{out}'); "
    "$s.Speak('{text}'); "
    "$s.Dispose()"
)


def synth(text: str, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        script = PS.format(voice=VOICE, out=str(wav).replace("\\", "/"), text=text.replace("'", "''"))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True)
        if not wav.exists() or wav.stat().st_size < 1000:
            print(f"  SAPI 失败: {r.stderr.strip()[:200]}", file=sys.stderr)
            return False
        r2 = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                             "-ar", "24000", "-ac", "1", "-b:a", "48k", str(out_path)],
                            capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"  ffmpeg 失败: {r2.stderr.strip()[:200]}", file=sys.stderr)
            return False
    return out_path.exists() and out_path.stat().st_size > 1000


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "./lessons")
    total = 0
    for lesson, info in LESSONS.items():
        d = root / lesson
        meta = {"title": info["title"], "sentences": []}
        for i, text in enumerate(info["sentences"], 1):
            name = f"{i:03d}.mp3"
            p = d / name
            if not (p.exists() and p.stat().st_size > 1000):
                if not synth(text, p):
                    print(f"合成失败：{text}", file=sys.stderr)
                    return 1
            meta["sentences"].append({"file": name, "text": text, "min_score": 0.55})
            total += 1
            print(f"  {lesson}/{name}  {text}  ({p.stat().st_size} B)")
        (d / "lesson.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：{len(LESSONS)} 课 / {total} 句 → {root.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
