"""唤醒与"播放中打断"。

唤醒词两种路线：
A. openWakeWord（英文预训练模型开箱可用：hey_jarvis / alexa / hey_mycroft）
   装：pip install openwakeword onnxruntime sounddevice
   缺点：官方预训练只有英文。对学英语的孩子其实不算缺点——"Hey Jarvis" 本身就是英语输入。
B. sherpa-onnx KWS（支持自定义中文唤醒词，如"小豆小豆"）
   装：pip install sherpa-onnx
   模型与用法：https://k2-fsa.github.io/sherpa/onnx/kws/index.html
   需要在 keywords.txt 里写唤醒词并转拼音 token，稍麻烦但可控。

没有麦克风/没装库时自动退化成"回车触发"，保证代码在任何电脑上都能跑通逻辑。
"""
import time

from . import config

_MODEL = None


def _oww_model(threshold=None):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from openwakeword.model import Model
    _MODEL = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    _MODEL._threshold = threshold or config.WAKE_THRESHOLD
    return _MODEL


def wait_for_wakeword(threshold=None, hint=True):
    """阻塞到检测到唤醒词，返回 True。"""
    threshold = threshold or config.WAKE_THRESHOLD
    try:
        import numpy as np
        import sounddevice as sd
        model = _oww_model(threshold)
        if hint:
            print('[唤醒] 等待 "Hey Jarvis" …（或按 Ctrl+C 退出）')
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280) as s:
            while True:
                data, _ = s.read(1280)
                pcm = np.asarray(data).reshape(-1)
                pred = model.predict(pcm)
                score = max(pred.values()) if pred else 0.0
                if score >= threshold:
                    return True
    except Exception as e:
        if hint:
            print(f"[唤醒] 语音唤醒不可用（{e}），改为回车触发。")
        try:
            input("按回车 = 唤醒豆豆 > ")
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        return True


class BargeIn:
    """播放中"有人在说话"检测。

    儿童设备上真正可靠的打断需要带回声消除（AEC）的麦克风（会议全向麦）。
    用普通 USB 音箱时，喇叭的声音会被麦克风收进去 → 必须靠两点降低误触发：
      1) 门限偏高（KIDBOX_BARGEIN_RMS），只有靠近说话才触发
      2) 触发后先暂停，再听一小段，只认"停/继续/下一课"这类短命令，认不出就继续播
    """

    def __init__(self, threshold=None):
        self.threshold = threshold or config.BARGEIN_RMS
        self.enabled = False
        self._sd = None
        self._stream = None
        self._loud_hits = 0
        try:
            import sounddevice as sd
            self._sd = sd
            self.enabled = True
        except Exception:
            self.enabled = False

    def reset(self):
        self._loud_hits = 0

    def triggered(self):
        """非阻塞地抓一小段音频，返回是否检测到持续说话。"""
        if not self.enabled:
            return False
        import numpy as np

        from .asr import _rms
        try:
            chunk, _ = self._sd.read(0)  # 占位，避免 IDE 提示未使用
        except Exception:
            pass
        try:
            data = self._sd.rec(int(16000 * 0.3), samplerate=16000, channels=1, dtype="int16")
            self._sd.wait()
        except Exception:
            return False
        level = _rms(memoryview(data.tobytes()))
        if level > self.threshold:
            self._loud_hits += 1
        else:
            self._loud_hits = 0
        return self._loud_hits >= 2   # 连续两次（约0.6秒）才算真的在说话
