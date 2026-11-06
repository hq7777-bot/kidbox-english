"""设备端（跑在手机 Termux / 树莓派上）：只做四件事
   ① 列出音频库 ② 播放/停止  ③ 报状态和进度  ④ 播一段 mp3（TTS 结果）

它不知道"什么是英语课"，也不调用大模型。所有判断都在 PC 上的大脑里。

用法：
    python device_agent.py                    # 真播放（需要 ffplay）
    KIDBOX_FAKE=1 python device_agent.py      # 假播放，只记日志（测试用）
    KIDBOX_PORT=8788 python device_agent.py

接口（给大脑用 curl 调）：
    GET  /state                          -> {playing, file, pos, recent_audio, level}
    GET  /library                        -> 文本树
    POST /play   {"files":[..],"sec":0}   -> 开始播放（files 是相对音频库的路径）
    POST /stop                            -> 停止并保存进度
    POST /volume {"percent":70}
    POST /speak  {"url":"http://pc/audio/x.mp3"}
"""
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.getenv("KIDBOX_LIBRARY", str(Path(__file__).resolve().parent / "library")))
STATE_FILE = Path(os.getenv("KIDBOX_STATE", str(Path(__file__).resolve().parent / "progress.json")))
FAKE = os.getenv("KIDBOX_FAKE", "0") == "1"
PORT = int(os.getenv("KIDBOX_PORT", "8788"))
HAS_FFPLAY = shutil.which("ffplay") is not None and not FAKE

_LOCK = threading.Lock()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Player:
    def __init__(self):
        self.files = []
        self.secs = []
        self.index = 0
        self.sec = 0.0
        self.t0 = 0.0
        self.proc = None
        self.volume = 70
        self.thread = None
        self.running = False
        self.current_lesson = None
        self.recent = []          # [{file, lesson, pos, at}]
        self._load()

    # ---------- 进度 ----------
    def _load(self):
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.current_lesson = d.get("current_lesson")
                self.recent = d.get("recent_audio", [])[-10:]
            except Exception:
                pass

    def save(self):
        STATE_FILE.write_text(json.dumps({
            "current_lesson": self.current_lesson,
            "recent_audio": self.recent[-10:],
            "saved_at": now(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def state(self):
        pos = self.sec
        if self.proc and self.proc.poll() is None:
            pos = self.sec + (time.time() - self.t0)
        f = self.files[self.index] if 0 <= self.index < len(self.files) else None
        return {
            "playing": bool(self.proc and self.proc.poll() is None) or (FAKE and self.running),
            "file": f, "pos": round(pos, 1), "index": self.index,
            "total": len(self.files),
            "current_lesson": self.current_lesson,
            "recent_audio": self.recent[-6:],
            "fake": FAKE, "ffplay": HAS_FFPLAY,
        }

    # ---------- 播放 ----------
    def play(self, files, sec=0.0):
        """files 允许三种写法，统一归一化：
           ["a.mp3"] / [{"file":"a.mp3","sec":4.2}] / "a.mp3"
           第二种可以给每条音频指定不同的起始秒数（复习多条时很有用）。
        """
        if isinstance(files, str):
            files = [files]
        norm, secs = [], []
        default_sec = float(sec or 0)
        for x in files:
            if isinstance(x, dict):
                f = x.get("file") or x.get("path") or ""
                norm.append(str(f))
                secs.append(float(x.get("sec", default_sec) or 0))
            else:
                norm.append(str(x))
                secs.append(default_sec)
        norm = [f for f in norm if f]
        if not norm:
            return {"ok": False, "error": "files 为空或不合法"}
        self.stop(save=False)
        self.files = norm
        self.secs = secs
        self.index = 0
        self.sec = secs[0]
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return {"ok": True, "queue": list(zip(norm, secs))}

    def _run(self):
        while self.index < len(self.files) and self.running:
            f = self.files[self.index]
            lesson = "/".join(str(f).split("/")[:2])
            self.current_lesson = lesson
            self.recent.append({"file": f, "lesson": lesson, "pos": self.sec, "at": now()})
            self.save()
            path = ROOT / str(f)
            if not path.exists():
                self.index += 1
                self.sec = 0.0
                continue
            self.t0 = time.time()
            if HAS_FFPLAY:
                self.proc = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                     "-volume", str(self.volume), "-ss", f"{self.sec:.2f}", str(path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                self.proc.wait()
            else:
                print(f"[fake] PLAY {f} from {self.sec:.1f}s")
                time.sleep(1.0)     # 假装播了 1 秒
            self.index += 1
            self.sec = self.secs[self.index] if self.index < len(self.secs) else 0.0
        self.running = False
        self.proc = None

    def stop(self, save=True):
        self.running = False
        if self.proc and self.proc.poll() is None:
            try:
                self.sec = self.sec + (time.time() - self.t0)
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                pass
        self.proc = None
        if save:
            self.save()
        return {"ok": True}

    def speak(self, url):
        """播一段网络 mp3（TTS 结果）。"""
        def _go():
            path = url
            if url.startswith("http"):
                tmp = Path("/tmp") / f"say_{int(time.time())}.mp3"
                try:
                    import urllib.request
                    urllib.request.urlretrieve(url, tmp)
                    path = str(tmp)
                except Exception as e:
                    print("[speak] 下载失败", e)
                    return
            if HAS_FFPLAY:
                subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                                "-volume", str(self.volume), path],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            else:
                print(f"[fake] SPEAK {path}")
        threading.Thread(target=_go, daemon=True).start()
        return {"ok": True}


P = Player()


def library_text():
    if not ROOT.exists():
        return "(音频库为空)"
    lines = []
    for lv in sorted([p for p in ROOT.iterdir() if p.is_dir()]):
        lines.append(f"{lv.name}/")
        for les in sorted([p for p in lv.iterdir() if p.is_dir()]):
            files = sorted([f.name for f in les.iterdir() if f.suffix.lower() in
                            (".mp3", ".wav", ".m4a", ".ogg")])
            meta = les / "meta.json"
            title = les.name
            if meta.exists():
                try:
                    title = json.loads(meta.read_text(encoding="utf-8")).get("title", title)
                except Exception:
                    pass
            lines.append(f"  {les.name}  [{title}]  {len(files)} 个音频")
    return "\n".join(lines)


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
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path).path
        with _LOCK:
            if u == "/state":
                return self._json(P.state())
            if u == "/context":
                return self._json({"library": library_text(), "state": P.state()})
            if u == "/library":
                return self._json({"library": library_text()})
            if u == "/health":
                return self._json({"ok": True, "fake": FAKE})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path).path
        b = self._body()
        with _LOCK:
            if u == "/play":
                files = b.get("files") or ([b["file"]] if b.get("file") else [])
                if not files:
                    return self._json({"error": "缺少 files"}, 400)
                return self._json(P.play(files, b.get("sec", 0)))
            if u == "/stop":
                return self._json(P.stop())
            if u == "/volume":
                P.volume = max(0, min(100, int(b.get("percent", 70))))
                return self._json({"ok": True, "volume": P.volume})
            if u == "/speak":
                return self._json(P.speak(b.get("url", "")))
        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"[device] 音频库 {ROOT}  假播放={FAKE}  ffplay={HAS_FFPLAY}  端口={PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
