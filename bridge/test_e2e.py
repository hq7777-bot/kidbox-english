"""端到端测试：设备进程 + 大脑进程，跑真实链路（DeepSeek 真调用）。

    python test_e2e.py              # 用 deepseek 后端
    python test_e2e.py hermes       # 用 hermes 后端（慢，约 7 秒/句）
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
LIB = HERE / "library"
STATE = HERE / "progress.json"
DEV = "http://127.0.0.1:8788"
PC = "http://127.0.0.1:8787"


def build():
    if LIB.exists():
        shutil.rmtree(LIB)
    plan = {"L1": [("001_hello", "Hello", 2), ("002_colors", "Colors", 2),
                   ("003_animals", "Animals", 3), ("004_family", "Family", 2)],
            "L2": [("001_school", "School", 2)]}
    for lv, items in plan.items():
        for d, title, n in items:
            p = LIB / lv / d
            p.mkdir(parents=True, exist_ok=True)
            for i in range(1, n + 1):
                (p / f"{i:02d}.mp3").write_bytes(b"\x00" * 32)
            (p / "meta.json").write_text(json.dumps(
                {"title": title, "text": f"{title} lesson", "words": []},
                ensure_ascii=False), encoding="utf-8")
    STATE.write_text(json.dumps({
        "current_lesson": "L1/003_animals",
        "recent_audio": [
            {"file": "L1/003_animals/02.mp3", "lesson": "L1/003_animals",
             "pos": 4.2, "at": "2026-11-03T19:41:00"},
            {"file": "L1/003_animals/03.mp3", "lesson": "L1/003_animals",
             "pos": 9.8, "at": "2026-11-03T19:43:00"},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_ok(url, n=50):
    for _ in range(n):
        try:
            if requests.get(url, timeout=1).status_code == 200:
                return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    brain = sys.argv[1] if len(sys.argv) > 1 else "deepseek"
    build()

    denv = dict(os.environ, KIDBOX_FAKE="1", KIDBOX_LIBRARY=str(LIB),
                KIDBOX_STATE=str(STATE), KIDBOX_PORT="8788")
    dev = subprocess.Popen([sys.executable, "device_agent.py"], cwd=str(HERE), env=denv,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    benv = dict(os.environ, KIDBOX_BRAIN=brain, KIDBOX_DEVICE=DEV,
                KIDBOX_BRAIN_PORT="8787", PYTHONIOENCODING="utf-8")
    pc = subprocess.Popen([sys.executable, "pc_brain.py"], cwd=str(HERE), env=benv,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    ok = wait_ok(DEV + "/health") and wait_ok(PC + "/health")
    print(f"[测试] 两个服务就绪={ok}  后端={brain}")
    if not ok:
        dev.kill(); pc.kill()
        return 2

    fails = []

    def check(name, cond, extra=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    try:
        # 1) 设备接口本身
        lib = requests.get(DEV + "/library").json()["library"]
        check("设备能列出音频库", "001_hello" in lib and "003_animals" in lib)

        st0 = requests.get(DEV + "/state").json()
        check("设备能报出最近播放记录", len(st0["recent_audio"]) == 2,
              f"pos={st0['recent_audio'][0]['pos']}")

        # 2) 真实语音指令（核心用例）
        t0 = time.time()
        r = requests.post(PC + "/ask_text",
                          json={"text": "复习上两个英语音频，然后再播放下一篇"},
                          timeout=180).json()
        ms = r.get("ms", int((time.time() - t0) * 1000))
        print(f"[回复] {r.get('reply')}   ({ms} ms, say_url={r.get('say_url')})")
        check("大脑给了回复", bool(r.get("reply")))
        if r.get("say_url"):
            au = requests.get(r["say_url"], timeout=30)
            check("TTS 音频可下载", au.status_code == 200 and len(au.content) > 1000,
                  f"{len(au.content)} bytes")
        else:
            check("TTS 音频可下载", False, "say_url 为空")

        time.sleep(0.5)
        st = requests.get(DEV + "/state").json()
        played = [x["file"] for x in st["recent_audio"]]
        print(f"[设备已播] {played}")
        check("重播了'上两个'音频",
              "L1/003_animals/02.mp3" in played and "L1/003_animals/03.mp3" in played)
        check("接着播了'下一篇'", any("004_family" in f for f in played))

        # 3) 停止
        requests.post(DEV + "/stop")
        time.sleep(0.2)
        check("能停止", requests.get(DEV + "/state").json()["playing"] is False)

        # 4) 知识类问题不应触发播放
        before = len(requests.get(DEV + "/state").json()["recent_audio"])
        r2 = requests.post(PC + "/ask_text",
                           json={"text": "恐龙为什么会灭绝"}, timeout=180).json()
        print(f"[回复] {r2.get('reply')}   ({r2.get('ms')} ms)")
        after = len(requests.get(DEV + "/state").json()["recent_audio"])
        check("知识问题没有乱放音频", after == before, f"{before}->{after}")
    finally:
        dev.terminate(); pc.terminate()
        try:
            out, _ = dev.communicate(timeout=5)
            if out:
                print("\n[设备日志]")
                for line in out.splitlines()[-12:]:
                    print("  " + line)
        except Exception:
            dev.kill()

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：{fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
