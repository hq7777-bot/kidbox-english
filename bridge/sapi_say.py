"""命令行 TTS：Windows SAPI 合成到 wav。

为什么单独一个进程：
pyttsx3 的 runAndWait() 在 HTTP 服务器的工作线程里会死锁（实测 180 秒不返回），
必须跑在主线程。所以每次用一个短命子进程，代价约 0.9 秒启动，但绝不会卡住服务。

用法：python sapi_say.py out.wav "要念的中文"
"""
import sys
import pyttsx3


def main():
    if len(sys.argv) < 3:
        print("usage: sapi_say.py out.wav text")
        return 2
    out, text = sys.argv[1], sys.argv[2]
    eng = pyttsx3.init()
    eng.setProperty("rate", 175)
    want = "EN" if sum(1 for ch in text if ch.isascii() and ch.isalpha()) > max(8, len(text) * 0.6) else "ZH"
    for v in eng.getProperty("voices"):
        vid = ((v.id or "") + " " + (getattr(v, "name", "") or "")).upper()
        if want in vid:
            eng.setProperty("voice", v.id)
            break
    eng.save_to_file(text, out)
    eng.runAndWait()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
