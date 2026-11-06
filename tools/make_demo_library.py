"""生成一套可直接试听的示例课程库（kidbox/library/L1、L2）。

    python tools/make_demo_library.py            # 用 edge-tts 合成英文童声
    python tools/make_demo_library.py --silent   # 网络不通时生成静音占位（只跑流程）

设计原则：一条音频 = 一句话。不要一课压成一个长文件，
否则"重播这一句 / 断点续播 / 跟读"全都做不了。
"""
import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kidbox import config, tts  # noqa: E402

LESSONS = [
    ("L1", "001_hello", "Hello",
     ["Hello.", "Hi! I am Anna.", "Hello! I am Anna."],
     ["hello", "hi", "anna"],
     "Hello! Hi! I am Anna."),
    ("L1", "002_colors", "Colors",
     ["Red.", "Blue.", "Green.", "Red and blue."],
     ["red", "blue", "green"],
     "Red. Blue. Green. Red and blue."),
    ("L2", "001_animals", "Animals",
     ["A cat.", "A dog.", "A bird.", "The cat is small."],
     ["cat", "dog", "bird"],
     "A cat. A dog. A bird. The cat is small."),
]


def silence_wav(path: Path, seconds: float = 1.2, rate: int = 16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def silence_mp3(path: Path, seconds: float = 1.2):
    """有 ffmpeg 就生成真静音 mp3，否则返回 False。"""
    if not shutil.which("ffmpeg"):
        return False
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=24000:cl=mono", "-t", str(seconds),
                        "-q:a", "9", str(path)],
                       check=True, timeout=30)
        return path.exists()
    except Exception:
        return False


def build(silent=False):
    made = 0
    for level, dirname, title, sentences, words, text in LESSONS:
        d = config.LIBRARY / level / dirname
        d.mkdir(parents=True, exist_ok=True)
        for i, sent in enumerate(sentences, 1):
            mp3 = d / f"{i:02d}.mp3"
            if mp3.exists() and mp3.stat().st_size > 800:
                made += 1
                continue
            path = None if silent else tts.synthesize(sent, voice=config.TTS_VOICE_EN)
            if path and Path(path).exists():
                shutil.copyfile(path, mp3)
            elif silence_mp3(mp3):
                pass
            else:
                silence_wav(d / f"{i:02d}.wav")
                if mp3.exists():
                    mp3.unlink()
            made += 1
            print(f"  {level}/{dirname}/{i:02d} <- {sent}")
        meta = d / "meta.json"
        meta.write_text(
            '{\n  "title": "%s",\n  "text": "%s",\n  "words": %s\n}\n'
            % (title, text, str(words).replace("'", '"')),
            encoding="utf-8")
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--silent", action="store_true", help="不联网，只生成静音占位")
    args = ap.parse_args()
    print(f"目标音频库：{config.LIBRARY}")
    n = build(silent=args.silent)
    # 顺手扫一遍让 DB 建好
    from kidbox import db, library
    conn = db.connect()
    lessons = library.sync(conn, config.LIBRARY)
    print(f"\n完成：{len(lessons)} 课 / {n} 个音频；级别 {library.levels(conn)}")
    print("试听一条：ffplay -nodisp -autoexit \"" +
          lessons[0].files[0] + "\"" if lessons else "")


if __name__ == "__main__":
    main()
