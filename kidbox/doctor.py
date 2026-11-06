"""环境体检：把「拷到另一台电脑能不能跑」这件事一次问清楚。

    python -m kidbox.doctor
    python -m kidbox.doctor --tts     # 额外做一次联网 TTS 测试

检查项：Python 版本 / 依赖 / ffplay / 配置 / 音频库 / 数据库可写 / 大脑后端。
任何一项 ✗ 都会给出可执行的修复建议，退出码 1。
"""
import argparse
import importlib.util
import platform
import shutil
import sys
from pathlib import Path

from . import config, db, library

OK = "✓"
NO = "✗"
WARN = "!"

problems = []


def line(mark, title, detail=""):
    print(f"  {mark} {title}{('  —  ' + detail) if detail else ''}")
    if mark == NO:
        problems.append(title)


def check_python():
    v = sys.version_info
    line(OK if v >= (3, 10) else NO, f"Python {platform.python_version()}",
         sys.executable)
    if v < (3, 10):
        line(WARN, "需要 Python 3.10 以上")


def check_module(mod, required=True, hint=""):
    has = importlib.util.find_spec(mod) is not None
    if has:
        line(OK, f"依赖 {mod}")
    else:
        line(NO if required else WARN, f"依赖 {mod} 缺失", hint)
    return has


def check_ffmpeg():
    for exe in ("ffplay", "ffmpeg"):
        path = shutil.which(exe)
        line(OK if path else WARN, f"命令 {exe}", path or "没找到（不影响静默测试，只是不出声）")


def check_config():
    print("\n[配置]")
    print(f"  项目根目录：{config.PROJECT_ROOT}")
    print(f"  音频库：    {config.LIBRARY}")
    print(f"  数据库：    {config.DB_PATH}")
    print(f"  缓存：      {config.CACHE_DIR}")
    has_key = bool(config.DEEPSEEK_API_KEY)
    brain = config.BRAIN_MODE
    if brain == "deepseek" and not has_key:
        line(NO, "KIDBOX_BRAIN=deepseek 但没有 DEEPSEEK_API_KEY")
    elif brain == "mock":
        line(OK, "大脑 = mock（离线关键词规则，不花钱）")
    elif has_key:
        line(OK, "大脑 = DeepSeek", config.DEEPSEEK_MODEL)
    else:
        line(OK, "大脑 = mock（没配 key，自动离线）", "要接大模型：在 kidbox/.env 填 DEEPSEEK_API_KEY")
    line(OK if config.PLAYER_MODE in ("auto", "null", "ffplay") else WARN,
         f"播放模式 = {config.PLAYER_MODE}")
    line(OK, f"TTS = {'开' if config.TTS_ENABLED else '关'}",
         "edge-tts " + ("可用" if _has_edge() else "不可用"))


def _has_edge():
    return importlib.util.find_spec("edge_tts") is not None or shutil.which("edge-tts") is not None


def check_library():
    print("\n[音频库]")
    config.LIBRARY.mkdir(parents=True, exist_ok=True)
    lessons = library.scan(config.LIBRARY)
    if lessons:
        by_level = {}
        for l in lessons:
            by_level.setdefault(l.level, []).append(l)
        for lv, items in sorted(by_level.items()):
            print(f"  {OK} {lv}：{len(items)} 课（{items[0].title} … {items[-1].title}）")
            missing = [i for i in items if not all(Path(f).exists() for f in i.files)]
            if missing:
                line(NO, f"{lv} 有 {len(missing)} 课的音频文件找不到")
        total_files = sum(len(l.files) for l in lessons)
        print(f"  合计 {len(lessons)} 课 / {total_files} 个音频文件")
    else:
        line(WARN, "音频库是空的",
             "先跑：python tools/make_demo_library.py 生成一套示例课")


def check_db():
    print("\n[数据库]")
    try:
        conn = db.connect()
        db.kv_set(conn, "doctor_ping", "ok")
        v = db.kv_get(conn, "doctor_ping")
        line(OK if v == "ok" else NO, "能写入 SQLite", str(config.DB_PATH))
        conn.close()
    except Exception as e:
        line(NO, "数据库不可写", str(e))


def check_tts_live():
    print("\n[TTS 联网测试]")
    from . import tts
    if not tts.available():
        line(WARN, "edge-tts 不可用，跳过", "pip install edge-tts")
        return
    p = tts.synthesize("Hello, 我们开始学英语吧。")
    line(OK if p else WARN, "合成一句测试语音",
         str(p) if p else "失败（可能是网络问题，不影响文字模式测试）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", action="store_true", help="额外做一次联网 TTS 测试")
    args = ap.parse_args()

    print("=" * 62)
    print("kidbox 环境体检")
    print("=" * 62)
    print("\n[Python 与依赖]")
    check_python()
    check_module("requests", True, "pip install requests")
    for m in ("edge_tts", "sounddevice", "numpy", "openwakeword", "onnxruntime"):
        check_module(m, False, "可选：pip install -r requirements-audio.txt")
    check_ffmpeg()
    check_config()
    check_library()
    check_db()
    if args.tts:
        check_tts_live()

    print("\n" + "=" * 62)
    if problems:
        print(f"发现 {len(problems)} 个必须修的问题：")
        for p in problems:
            print(f"  - {p}")
        print("\nWindows 上最快的修法：双击 setup.bat，它会建虚拟环境并装依赖。")
        return 1
    print("全部通过。可以这样试：")
    print("  python -m kidbox.main --once 下一课")
    print("  python -m kidbox.main --script demo_script.txt")
    print("  python -m kidbox.main --text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
