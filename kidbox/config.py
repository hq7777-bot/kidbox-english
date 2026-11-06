"""全局配置：全部从环境变量读，方便在树莓派/别的电脑上改。

优先读取 kidbox/.env（同目录），其次读取项目根目录 .env。
这样把整个目录拷到另一台电脑，只要带上 .env 就能跑，不用改代码。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent


def _load_env():
    """极简 .env 解析（不引入 python-dotenv 依赖）。已存在的环境变量优先。"""
    for cand in (ROOT / ".env", PROJECT_ROOT / ".env"):
        if not cand.exists():
            continue
        try:
            text = cand.read_text(encoding="utf-8")
        except Exception:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)


_load_env()

LIBRARY = Path(os.getenv("KIDBOX_LIBRARY", str(ROOT / "library")))
DB_PATH = Path(os.getenv("KIDBOX_DB", str(ROOT / "kidbox.db")))
CACHE_DIR = Path(os.getenv("KIDBOX_CACHE", str(ROOT / ".cache")))

# ---------- 大模型 ----------
# auto = 有 key 用 DeepSeek，没 key 用离线 mock（保证任何电脑都能跑通流程）
BRAIN_MODE = os.getenv("KIDBOX_BRAIN", "auto").strip().lower()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---------- 语音识别 ----------
# siliconflow = 硅基流动 SenseVoice（便宜、中英混识、对童声一般）
# openai     = 任意 OpenAI 兼容 /audio/transcriptions 端点
ASR_PROVIDER = os.getenv("KIDBOX_ASR", "siliconflow")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE = os.getenv("SILICONFLOW_BASE", "https://api.siliconflow.cn/v1")
SILICONFLOW_ASR_MODEL = os.getenv("SILICONFLOW_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall")
ASR_LANGUAGE = os.getenv("KIDBOX_ASR_LANG", "zh")  # 童声说中文提问，英文单词也能混识
# 通用 OpenAI 兼容 ASR（阿里百炼 / Groq / 本地服务）
ASR_BASE_URL = os.getenv("KIDBOX_ASR_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
ASR_API_KEY = os.getenv("KIDBOX_ASR_KEY", "")
ASR_MODEL = os.getenv("KIDBOX_ASR_MODEL", "paraformer-realtime-v2")

# ---------- 语音合成 ----------
TTS_VOICE_ZH = os.getenv("KIDBOX_TTS_ZH", "zh-CN-XiaoyiNeural")
TTS_VOICE_EN = os.getenv("KIDBOX_TTS_EN", "en-US-AnaNeural")  # Ana 是儿童音色
TTS_RATE = os.getenv("KIDBOX_TTS_RATE", "+0%")
TTS_ENABLED = os.getenv("KIDBOX_TTS", "1") not in ("0", "false", "off")

# ---------- 播放器 ----------
# auto = 有 ffplay 就出声，没有就静默（只跑逻辑）；null = 强制静默；ffplay = 强制出声
PLAYER_MODE = os.getenv("KIDBOX_PLAYER", "auto").strip().lower()

# ---------- 联网搜索（科普问答用）----------
# bocha | searxng | none  （none = 只靠模型自身知识，会明确告诉孩子"我查不到"）
SEARCH_PROVIDER = os.getenv("KIDBOX_SEARCH", "none")
BOCHA_KEY = os.getenv("BOCHA_API_KEY", "")
SEARXNG_URL = os.getenv("KIDBOX_SEARXNG", "")

# ---------- 行为参数 ----------
WAKE_THRESHOLD = float(os.getenv("KIDBOX_WAKE_THRESHOLD", "0.55"))
MAX_VOLUME = int(os.getenv("KIDBOX_MAX_VOLUME", "78"))       # 儿童音量上限
BARGEIN_RMS = float(os.getenv("KIDBOX_BARGEIN_RMS", "0.045"))  # 播放中"有人说话"的响度门限
SESSION_MAX_MINUTES = int(os.getenv("KIDBOX_SESSION_MAX", "25"))  # 单次连续使用时长提醒
QUIET_SILENCE_MS = int(os.getenv("KIDBOX_SILENCE_MS", "800"))

# 当前级别（可在运行时被工具改写，存 DB 的 kv 表，这里只是默认值）
DEFAULT_LEVEL = os.getenv("KIDBOX_LEVEL", "L1")


def summary():
    """给 doctor / 启动日志用的一句话摘要。"""
    return (f"brain={BRAIN_MODE} key={'有' if DEEPSEEK_API_KEY else '无'} "
            f"player={PLAYER_MODE} tts={TTS_ENABLED} library={LIBRARY}")
