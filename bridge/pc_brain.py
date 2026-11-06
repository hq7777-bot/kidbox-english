"""PC 端大脑：收到一句话 → 决定做什么 → 命令设备去执行 → 给孩子一句回复。

两种后端：
  KIDBOX_BRAIN=deepseek  直连 DeepSeek，工具直接 HTTP 打设备接口（快，约 1~2 秒）
  KIDBOX_BRAIN=hermes    调 hermes chat -q，让它自己用 terminal/curl 操作设备（慢，约 7 秒）

跑：
    KIDBOX_BRAIN=deepseek python pc_brain.py
    POST /ask_text {"text":"复习上两个英语音频，然后再播放下一篇"}
    POST /ask      （body 是 raw wav）
    GET  /audio/<file>.mp3
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

DEVICE = os.getenv("KIDBOX_DEVICE", "http://127.0.0.1:8788").rstrip("/")
# 设备要能把这个地址拿到手才能下载 TTS 音频，所以填 PC 的局域网 IP
PUBLIC = os.getenv("KIDBOX_PUBLIC", "http://127.0.0.1:8787").rstrip("/")
BRAIN = os.getenv("KIDBOX_BRAIN", "deepseek")
PORT = int(os.getenv("KIDBOX_BRAIN_PORT", "8787"))
HERE = Path(__file__).resolve().parent
AUDIO_DIR = HERE / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


def _load_key():
    global DEEPSEEK_KEY
    if DEEPSEEK_KEY:
        return DEEPSEEK_KEY
    env = Path(os.path.expanduser("~")) / "AppData/Local/hermes/.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                DEEPSEEK_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
    return DEEPSEEK_KEY


SYSTEM = """你在一个儿童英语学习盒子上当"大脑"。孩子 6 岁，只能语音交流，你只能说话。
盒子本身只会四件事（通过工具调用）：列出音频库、播放某些音频文件、停止、查状态。

规则：
1. 上下文（音频库、当前课、最近播放记录）已经在消息里直接给你了，不要再调工具去查。
2. 孩子说"复习上两个音频"→ 从 recent_audio 取最后两条，各带自己的 sec（上次听到的秒数）。
   如果孩子还有后续动作（"然后再播下一篇""再听个新的"），**把所有要播的文件按顺序放进同一次 device_play**，一条调用搞定，不要分两次。
3. 孩子说"下一篇"→ 找当前课在顺序里的下一课，从第一个音频开始播。
4. 播放类工具调用后，只回一句很短的口语确认，不要长篇大论。工具调用最多 1~2 次，越快越好。
5. 孩子问知识（为什么/是什么/动物/太空）→ 直接讲，**绝对不要播放音频，也不要调任何工具**。
6. 不知道就说"这个我不太确定"，不许编。
7. 回答只说口语短句，两句以内，不要符号、表情、列表。"""

TOOLS = [
    {"type": "function", "function": {
        "name": "device_context",
        "description": "一次拿到全部上下文：音频库结构 + 当前状态 + 最近播放记录（含每条上次听到的秒数 pos）。每次对话第一步都先调它。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "device_play",
        "description": "播放一个或多个音频（按顺序）。files 是列表，每项给 file（相对音频库的路径，如 L1/001_hello/01.mp3）和可选的 sec（从第几秒开始）。",
        "parameters": {"type": "object", "properties": {
            "files": {"type": "array", "items": {"type": "object", "properties": {
                "file": {"type": "string"},
                "sec": {"type": "number", "description": "从第几秒开始，默认 0"}},
                "required": ["file"]}}},
            "required": ["files"]}}},
    {"type": "function", "function": {
        "name": "device_stop",
        "description": "停止播放",
        "parameters": {"type": "object", "properties": {}}}},
]


# ---------------- 工具：直接打设备 HTTP ----------------
def call_device(path, payload=None, method="POST", timeout=10):
    try:
        if method == "GET":
            r = requests.get(DEVICE + path, timeout=timeout)
        else:
            r = requests.post(DEVICE + path, json=payload or {}, timeout=timeout)
        return r.json()
    except Exception as e:
        return {"error": f"设备不可达：{e}"}


def run_tool(name, args):
    if name == "device_context":
        d = call_device("/context", method="GET")
        if "error" in d:
            return str(d)
        st = d["state"]
        recent = "；".join(f"{x['file']}（上次到 {x['pos']} 秒）" for x in st.get("recent_audio", []))
        return (f"音频库：\n{d['library']}\n\n"
                f"当前课：{st.get('current_lesson')}；正在播：{st.get('file')}\n"
                f"最近播放：{recent or '（无）'}")
    if name == "device_play":
        files = args.get("files") or []
        r = call_device("/play", {"files": files})
        if not r.get("ok"):
            return f"播放失败：{r}"
        q = r.get("queue") or []
        return "已开始播放：" + "、".join(f"{f}@{s}秒" for f, s in q[:4])
    if name == "device_stop":
        call_device("/stop")
        return "已停止"
    return "未知工具"


# ---------------- 后端 1：DeepSeek 直连 ----------------
def prefetch():
    """预取设备上下文塞进提示词 —— 少一轮工具调用就少约 0.8 秒。"""
    d = call_device("/context", method="GET")
    if "error" in d:
        return ""
    st = d["state"]
    recent = "\n".join(
        f"  - {x['file']}（上次听到 {x['pos']} 秒）" for x in st.get("recent_audio", []))
    return (f"【设备上下文（刚刚读取）】\n音频库：\n{d['library']}\n"
            f"当前课：{st.get('current_lesson')}；正在播：{st.get('file')}\n"
            f"recent_audio（旧→新）：\n{recent or '  （空）'}")


def ask_deepseek(text):
    key = _load_key()
    if not key:
        return "没有配置 DeepSeek Key。"
    ctx = prefetch()
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": (ctx + "\n\n" if ctx else "") + "孩子说：" + text}]
    for rnd in range(4):
        t1 = time.time()
        r = requests.post(DEEPSEEK_BASE.rstrip("/") + "/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": DEEPSEEK_MODEL, "messages": msgs,
                                "tools": TOOLS, "tool_choice": "auto",
                                "temperature": 0.4, "max_tokens": 300},
                          timeout=60)
        if r.status_code != 200:
            return f"大脑出错 HTTP {r.status_code}"
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            print(f"    [轮 {rnd + 1}] 出回复 {int((time.time() - t1) * 1000)} ms")
            return (msg.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": calls})
        names = []
        for c in calls:
            names.append(c["function"]["name"])
            res = run_tool(c["function"]["name"],
                           json.loads(c["function"].get("arguments") or "{}"))
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": str(res)})
        print(f"    [轮 {rnd + 1}] {','.join(names)} {int((time.time() - t1) * 1000)} ms")
    return "好，我们先听这个。"


# ---------------- 后端 2：交给 Hermes ----------------
def ask_hermes(text):
    """让 Hermes 自己用 terminal/curl 操作设备。契约写在 DEVICE_MANUAL.md，拼进提示词。"""
    manual = ""
    mp = HERE / "DEVICE_MANUAL.md"
    if mp.exists():
        manual = mp.read_text(encoding="utf-8")
    prompt = (manual + "\n\n---\n\n孩子说：" + text +
              "\n\n请直接动手（必要时用 curl 调设备接口），最后只用一两句中文口语回复孩子。")
    cmd = ["hermes", "chat", "-q", prompt, "-Q", "-t", "terminal", "--max-turns", "6"]
    try:
        p = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, timeout=180)
    except Exception as e:
        return f"Hermes 调用失败：{e}"
    out = [l for l in (p.stdout or "").splitlines()
           if l.strip() and not l.startswith("session_id:")]
    return out[-1] if out else "我这会儿没想好。"


# ---------------- TTS ----------------
# 注意：edge-tts 版本旧了会被微软返回 403（本机实测 Hermes venv 里那份就废了）。
# 这里按候选顺序试，谁先成功就记住谁。
_TTS_CMD = None


def _candidates():
    out = []
    if _TTS_CMD:
        out.append(_TTS_CMD)
    env = os.getenv("KIDBOX_TTS_CMD")
    if env:
        out.append(env.split())
    if sys.platform.startswith("win"):
        out += [["py", "-3.13", "-m", "edge_tts"], ["edge-tts"]]
    else:
        out += [[sys.executable, "-m", "edge_tts"], ["edge-tts"]]
    seen, uniq = set(), []
    for c in out:
        k = tuple(c)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def tts(text):
    """返回相对 URL（/audio/xxx），失败返回 None。

    本机实测（同一句话）：
      Windows SAPI（本地，pyttsx3）  0.25 秒
      edge-tts（云端，微软）          3.0 秒   ← 明显瓶颈
    用 KIDBOX_TTS=sapi|edge 切换，默认 Windows 用 sapi、其它平台用 edge。
    """
    global _TTS_CMD
    if not text:
        return None
    provider = os.getenv("KIDBOX_TTS", "sapi" if sys.platform.startswith("win") else "edge")

    if provider == "sapi":
        name = f"{uuid.uuid4().hex[:10]}.wav"
        out = AUDIO_DIR / name
        if _sapi_say(text, out):
            return f"/audio/{name}"

    name = f"{uuid.uuid4().hex[:10]}.mp3"
    out = AUDIO_DIR / name
    voice = "zh-CN-XiaoyiNeural"
    asc = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if asc > max(8, len(text) * 0.6):
        voice = "en-US-AnaNeural"
    for cmd in _candidates():
        try:
            subprocess.run(cmd + ["--voice", voice, "--text", text,
                                  "--write-media", str(out)],
                           check=True, timeout=60, capture_output=True)
            if out.exists() and out.stat().st_size > 500:
                _TTS_CMD = cmd
                return f"/audio/{name}"
        except Exception:
            continue
    print("[tts] 所有 TTS 后端都失败了")
    return None


_sapi_lock = threading.Lock()


def _sapi_say(text, out_path):
    """Windows 本地语音合成（离线，约 1.1 秒含进程启动）。

    用子进程：pyttsx3 的 runAndWait 在 HTTP 工作线程里会死锁，必须在主线程跑。
    """
    script = HERE / "sapi_say.py"
    if not script.exists():
        return False
    with _sapi_lock:
        try:
            p = subprocess.run([sys.executable, str(script), str(out_path), text],
                               capture_output=True, text=True, timeout=30)
            return (p.returncode == 0 and out_path.exists()
                    and out_path.stat().st_size > 1000)
        except Exception as e:
            print("[tts:sapi]", e)
            return False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urlparse(self.path).path
        if u == "/health":
            return self._json({"ok": True, "brain": BRAIN, "device": DEVICE})
        if u.startswith("/audio/"):
            f = AUDIO_DIR / Path(u).name
            if not f.exists():
                return self._json({"error": "no file"}, 404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",
                             "audio/wav" if f.suffix.lower() == ".wav" else "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path).path
        raw = self._body()
        t0 = time.time()
        if u == "/ask_text":
            text = json.loads(raw or b"{}").get("text", "").strip()
        elif u == "/ask":
            text = stt(raw)
            if not text:
                return self._json({"text": "", "reply": "我没听清，你再说一次好吗？",
                                   "say_url": None, "ms": int((time.time() - t0) * 1000)})
        else:
            return self._json({"error": "not found"}, 404)

        reply = ask_hermes(text) if BRAIN == "hermes" else ask_deepseek(text)
        t1 = time.time()
        say_url = tts(reply)
        tts_ms = int((time.time() - t1) * 1000)
        full = (PUBLIC.rstrip("/") + say_url) if say_url else None
        ms = int((time.time() - t0) * 1000)
        print(f"[brain:{BRAIN}] {text} -> {reply}  (总 {ms} ms，其中 TTS {tts_ms} ms)")
        self._json({"text": text, "reply": reply, "say_url": full,
                    "ms": ms, "tts_ms": tts_ms})


def stt(wav_bytes):
    """语音识别：优先本地 faster-whisper，其次硅基流动。没配就返回空。"""
    tmp = Path("/tmp") / f"in_{uuid.uuid4().hex[:8]}.wav"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(wav_bytes)
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(os.getenv("KIDBOX_WHISPER", "base"), device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(tmp), language="zh", vad_filter=True)
        return "".join(s.text for s in segs).strip()
    except Exception:
        pass
    key = os.getenv("SILICONFLOW_API_KEY", "")
    if key:
        try:
            r = requests.post("https://api.siliconflow.cn/v1/audio/transcriptions",
                              headers={"Authorization": f"Bearer {key}"},
                              files={"file": ("a.wav", wav_bytes, "audio/wav")},
                              data={"model": "FunAudioLLM/SenseVoiceSmall"}, timeout=45)
            return (r.json().get("text") or "").strip()
        except Exception as e:
            print("[asr] 失败", e)
    return ""


if __name__ == "__main__":
    _load_key()
    print(f"[brain] 后端={BRAIN}  设备={DEVICE}  端口={PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
