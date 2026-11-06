"""设备端语音循环：录一句话 → 发给 PC 大脑 → 播回答。

跑在设备上（手机 Termux 或 树莓派）：

    # 树莓派 / Linux（用 sounddevice 自动判断说完了没）
    python voice_loop.py --pc http://192.168.1.10:8787

    # 安卓 Termux（termux-api 录音，按回车录固定 6 秒）
    pkg install python termux-api ffmpeg
    python voice_loop.py --pc http://192.168.1.10:8787 --termux

说明：这里没有做唤醒词。
- 树莓派：可以接 openWakeWord（见 kidbox/listen.py）
- 手机：Android 会杀后台麦克风，最稳的是"按一下说话"——对 6 岁孩子其实也不难教
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import requests


def record_sounddevice(max_sec=10, silence_ms=800, threshold=0.02):
    """录音到静音为止，返回 wav 路径。"""
    import io
    import wave

    import numpy as np
    import sounddevice as sd

    sr, block = 16000, 512
    sil = int(sr / block * silence_ms / 1000)
    frames, started, quiet = [], False, 0
    with sd.RawInputStream(samplerate=sr, blocksize=block, channels=1, dtype="int16") as st:
        for _ in range(int(sr / block * max_sec)):
            data, _ = st.read(block)
            buf = bytes(data)
            a = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            level = float(np.sqrt((a ** 2).mean())) if a.size else 0.0
            if not started:
                if level > threshold:
                    started = True
                    frames.append(buf)
                continue
            frames.append(buf)
            quiet = 0 if level > threshold * 0.7 else quiet + 1
            if quiet >= sil:
                break
    if not frames:
        return None
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(frames))
    return path


def record_termux(seconds=6):
    path = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["termux-microphone-record", "-f", path, "-l", str(seconds)],
                       check=True, timeout=seconds + 10)
    except Exception as e:
        print("[录音] termux-api 调用失败：", e)
        return None
    time.sleep(0.5)
    return path if os.path.exists(path) and os.path.getsize(path) > 1000 else None


def play(url, local_player="/speak"):
    """让本机的 device_agent 去播这段 TTS（它知道自己怎么出声）。"""
    r = requests.post(DEV + local_player, json={"url": url}, timeout=15)
    return r.ok


def main():
    global DEV
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", default="http://127.0.0.1:8787", help="PC 大脑地址")
    ap.add_argument("--device", default="http://127.0.0.1:8788", help="本机 device_agent 地址")
    ap.add_argument("--termux", action="store_true", help="安卓 Termux 模式")
    ap.add_argument("--seconds", type=int, default=6, help="Termux 固定录音秒数")
    ap.add_argument("--auto", action="store_true", help="树莓派：一直听，不用按键")
    args = ap.parse_args()

    PC, DEV = args.pc.rstrip("/"), args.device.rstrip("/")
    try:
        requests.get(DEV + "/health", timeout=3)
        requests.get(PC + "/health", timeout=3)
    except Exception as e:
        print("服务没起来：", e)
        return 2

    print(f"[就绪] 设备={DEV}  大脑={PC}  " + ("Termux 模式" if args.termux else "回车说话"))
    while True:
        try:
            if not args.auto:
                input("\n按回车说话（Ctrl+C 退出）> ")
            t0 = time.time()
            wav = record_termux(args.seconds) if args.termux else record_sounddevice()
            if not wav:
                print("[录音] 没听到声音")
                continue
            with open(wav, "rb") as f:
                r = requests.post(PC + "/ask", data=f.read(),
                                  headers={"Content-Type": "audio/wav"}, timeout=180)
            os.unlink(wav)
            j = r.json()
            print(f"[你] {j.get('text') or '（没识别出来）'}")
            print(f"[豆豆] {j.get('reply')}   ({int((time.time() - t0) * 1000)} ms)")
            if j.get("say_url"):
                play(j["say_url"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[循环] 出错：", e)
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
