"""播放器：ffplay 后端 + 空后端（无音频设备/自测时用）。

设计要点（这是"记录播放进度"的基础）：
- 用 ffplay 起子进程播单个文件，`-ss` 实现从任意秒开始
- 暂停 = 杀进程并记住已播秒数；继续 = 带 `-ss` 重开
- tick() 每 200ms 被主循环调用一次，负责"这个文件播完了→自动下一个"
- 主程序随时可以读 current() 拿到 (第几个文件, 第几秒) 落库，断电不丢
"""
import shutil
import subprocess
import time


class NullPlayer:
    """没有 ffplay 时用：不发声，只推进状态，便于在 PC 上做逻辑自测。"""
    name = "null"

    def __init__(self, volume=70, speed=1.0):
        self.volume = volume
        self.speed = speed
        self._files = []
        self._index = 0
        self._offset = 0.0
        self._t0 = 0.0
        self._paused = False
        self._finished = True
        self._auto_advance_sec = None

    def play(self, files, start_index=0, start_sec=0.0):
        self._files = list(files)
        self._index = max(0, min(start_index, len(self._files) - 1))
        self._offset = float(start_sec)
        self._t0 = time.time()
        self._paused = False
        self._finished = False

    def tick(self):
        pass

    def current(self):
        if self._finished or not self._files:
            return None
        elapsed = self._offset + (0 if self._paused else time.time() - self._t0)
        return self._index, elapsed

    def is_playing(self):
        return bool(self._files) and not self._finished

    def pause(self):
        self._paused = True

    def resume(self):
        self._t0 = time.time()
        self._paused = False

    def stop(self):
        self._finished = True
        self._files = []

    def set_volume(self, v):
        self.volume = v

    def set_speed(self, s):
        self.speed = s


class FfplayPlayer(NullPlayer):
    """用 ffplay 真正出声。需要 PATH 里有 ffplay（apt install ffmpeg）。"""
    name = "ffplay"

    def __init__(self, volume=70, speed=1.0):
        super().__init__(volume, speed)
        self._proc = None

    @staticmethod
    def available():
        return shutil.which("ffplay") is not None

    def _spawn(self, sec):
        f = self._files[self._index]
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
               "-volume", str(int(self.volume)), "-ss", f"{max(0.0, sec):.2f}", f]
        if abs(self.speed - 1.0) > 1e-3:
            # atempo 支持 0.5~2.0，刚好覆盖儿童慢速需求
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                   "-volume", str(int(self.volume)),
                   "-af", f"atempo={self.speed:.2f}",
                   "-ss", f"{max(0.0, sec):.2f}", f]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play(self, files, start_index=0, start_sec=0.0):
        self.stop()
        self._files = list(files)
        self._index = max(0, min(start_index, len(self._files) - 1))
        self._offset = float(start_sec)
        self._t0 = time.time()
        self._paused = False
        self._finished = False
        self._spawn(self._offset)

    def _kill(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def tick(self):
        if self._finished or self._paused or not self._files:
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        # 当前文件播完了 → 下一个
        if self._index + 1 < len(self._files):
            self._index += 1
            self._offset = 0.0
            self._t0 = time.time()
            self._spawn(0.0)
        else:
            self._finished = True

    def pause(self):
        if self._finished or self._paused:
            return
        cur = self.current()
        self._offset = cur[1] if cur else self._offset
        self._kill()
        self._paused = True

    def resume(self):
        if not self._paused:
            return
        self._paused = False
        self._t0 = time.time()
        self._spawn(self._offset)

    def stop(self):
        self._kill()
        self._finished = True
        self._files = []
        self._index = 0
        self._offset = 0.0


def make(volume=70):
    """按 KIDBOX_PLAYER 选择后端：auto / null / ffplay。"""
    from . import config
    mode = getattr(config, "PLAYER_MODE", "auto")
    if mode == "null":
        return NullPlayer(volume=volume)
    if mode == "ffplay":
        return FfplayPlayer(volume=volume)
    return FfplayPlayer(volume=volume) if FfplayPlayer.available() else NullPlayer(volume=volume)


def play_file_blocking(path, volume=80):
    """播一段提示音/TTS，不参与课程进度。"""
    if not FfplayPlayer.available():
        return
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                    "-volume", str(int(volume)), str(path)],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
