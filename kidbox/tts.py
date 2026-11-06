"""语音合成：edge-tts（免费、音色好、中英都自然）。

中文用 zh-CN-XiaoyiNeural（偏童声、语气柔和），英文用 en-US-AnaNeural（儿童音色）。
生成结果按文本 hash 缓存到 .cache/，重复的固定话术不再请求网络（也更快）。
"""
import hashlib
import importlib.util
import shutil
import subprocess
import sys

from . import config

CACHE = None


def _cache_dir():
    global CACHE
    if CACHE is None:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE = config.CACHE_DIR
    return CACHE


def _module_ok():
    return importlib.util.find_spec("edge_tts") is not None


def available():
    """装了 edge-tts 模块，或 PATH 里有 edge-tts 命令，都算可用。"""
    return _module_ok() or shutil.which("edge-tts") is not None


def _cmd(text, voice, out):
    """优先用当前解释器 -m edge_tts（避免 PATH 找不到脚本），退回 CLI。"""
    base = (["-m", "edge_tts"] if _module_ok() else [])
    if base:
        return [sys.executable] + base + ["--voice", voice, "--rate", config.TTS_RATE,
                                         "--text", text, "--write-media", str(out)]
    return ["edge-tts", "--voice", voice, "--rate", config.TTS_RATE,
            "--text", text, "--write-media", str(out)]


def _pick_voice(text):
    # 只要句子里有较长的英文段，就用英文童声；否则中文
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return config.TTS_VOICE_EN if ascii_letters > max(6, len(text) * 0.6) else config.TTS_VOICE_ZH


def synthesize(text, voice=None):
    """返回 mp3 路径；失败返回 None。"""
    if not text or not config.TTS_ENABLED or not available():
        return None
    voice = voice or _pick_voice(text)
    key = hashlib.md5((voice + "|" + config.TTS_RATE + "|" + text).encode("utf-8")).hexdigest()
    out = _cache_dir() / f"{key}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return str(out)
    cmd = _cmd(text, voice, out)
    try:
        subprocess.run(cmd, check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"[tts] 合成失败（不影响继续）：{e}")
        return None
    return str(out) if out.exists() else None


def say(text, volume=80):
    """合成并播放，阻塞到播完。没有 TTS 就打印。"""
    from . import player
    path = synthesize(text)
    if not path:
        print(f"[豆豆] {text}")
        return
    player.play_file_blocking(path, volume=volume)
