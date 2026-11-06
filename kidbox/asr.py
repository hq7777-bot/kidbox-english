"""录音 + 语音识别。

录音用 sounddevice（PortAudio），VAD 用最朴素的"响度门限 + 静音停"：
在儿童场景里，能量 VAD 比 WebRTC VAD 更宽容（孩子会拖长音、突然停顿），
代价是安静环境下需要把门限调准（KIDBOX_SILENCE_MS / rms_threshold）。

识别走 OpenAI 兼容的 /audio/transcriptions，默认硅基流动 SenseVoice。
"""
import io
import struct
import time
import wave

import requests

from . import config


def _rms(block):
    if not block:
        return 0.0
    n = len(block) // 2
    if n == 0:
        return 0.0
    vals = struct.unpack("<%dh" % n, block[:n * 2])
    s = sum(v * v for v in vals) / n
    return (s ** 0.5) / 32768.0


def record_utterance(samplerate=16000, threshold=0.02, silence_ms=None,
                     max_sec=12, wait_speech_sec=6, verbose=False):
    """录一句话，返回 wav bytes（16k/16bit/mono）或 None。"""
    import sounddevice as sd
    silence_ms = silence_ms or config.QUIET_SILENCE_MS
    block = 512
    silence_blocks = int(samplerate / block * silence_ms / 1000)
    max_blocks = int(samplerate / block * max_sec)
    wait_blocks = int(samplerate / block * wait_speech_sec)

    frames, voiced, quiet, started = [], 0, 0, False
    with sd.RawInputStream(samplerate=samplerate, blocksize=block, channels=1,
                           dtype="int16") as stream:
        for i in range(max_blocks + wait_blocks):
            data, _ = stream.read(block)
            b = bytes(data)
            level = _rms(b)
            if not started:
                if level > threshold:
                    started = True
                    frames.append(b)
                elif i > wait_blocks:
                    return None
                continue
            frames.append(b)
            if level > threshold * 0.7:
                quiet = 0
                voiced += 1
            else:
                quiet += 1
            if quiet >= silence_blocks and voiced > 3:
                break
    if not frames:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def transcribe(wav_bytes):
    """语音识别。

    注意：DeepSeek 的 API **只有文本**（chat/completions + search/fetch），
    没有 /audio/transcriptions 这类语音接口，别把 DeepSeek 当 ASR 用。
    """
    if config.ASR_PROVIDER == "siliconflow":
        url = config.SILICONFLOW_BASE.rstrip("/") + "/audio/transcriptions"
        key = config.SILICONFLOW_API_KEY
        model = config.SILICONFLOW_ASR_MODEL
    else:
        # 任何 OpenAI 兼容的 ASR 端点（阿里百炼 / Groq / 本地 faster-whisper 服务）
        url = config.ASR_BASE_URL.rstrip("/") + "/audio/transcriptions"
        key = config.ASR_API_KEY
        model = config.ASR_MODEL
    if not key:
        raise RuntimeError("没有配置 ASR 的 API Key（DeepSeek 不提供语音识别）")
    files = {"file": ("a.wav", wav_bytes, "audio/wav")}
    data = {"model": model}
    r = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                      files=files, data=data, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"ASR HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    return (j.get("text") or "").strip()


def listen_once(verbose=False):
    """录一句话并识别。返回文本，超时/无语音返回空串。"""
    t0 = time.time()
    wav = record_utterance(verbose=verbose)
    if not wav:
        return ""
    if verbose:
        print(f"[asr] 录音 {time.time() - t0:.1f}s，识别中…")
    try:
        return transcribe(wav)
    except Exception as e:
        print(f"[asr] 识别失败：{e}")
        return ""
