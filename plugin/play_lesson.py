"""play_lesson.py —— 给 xiaozhi-esp32-server 用的"播放英语课"插件。

放在：main/xiaozhi-server/plugins_func/functions/play_lesson.py
（与 play_music.py 同目录；如果你的版本用自动扫描注册，落盘即可，
  否则在 plugins_func/loadplugins.py 里加一行 import）

配置（data/.config.yaml）：
```yaml
plugins:
  play_lesson:
    lesson_dir: /home/pi/lessons     # 目录规范见下
    refresh_time: 30
```

目录规范（和本文的 kidbox 完全一致）：
```
lessons/
  L1/
    001_hello/
      meta.json        # {"title":"Hello","text":"...","words":["hello"]}
      01.mp3  02.mp3
    002_colors/
  L2/
    001_animals/
```

工具能力：
  play_lesson(level="L1", order=3)        -> 播放 L1 第 3 课全部音频
  play_lesson(level="L1", order=3, part=2) -> 只播第 2 条
  play_lesson(resume=True)                 -> 从上次停下的课/条继续
  lesson_progress()                        -> 报进度

⚠️ 本文件依赖服务端运行时（ConnectionHandler / tts_text_queue），无法脱离服务器单独运行。
   逻辑照抄自官方 play_music.py 的已验证写法：往 tts_text_queue 塞 ContentType.FILE 即可，
   分帧、Opus 编码、发送都由服务端 TTS 管线负责，插件不用自己编码音频。
"""
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
CACHE = {}
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".pcm", ".opus", ".ogg")

# ---------------------------------------------------------------- 扫描与索引
def _scan(root: str):
    out = []
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            out.append(str(p.relative_to(root)).replace("\\", "/"))
    return out


def _init(conn):
    if CACHE:
        return CACHE
    cfg = ((conn.config or {}).get("plugins") or {}).get("play_lesson") or {}
    CACHE["dir"] = os.path.abspath(cfg.get("lesson_dir", "./lessons"))
    CACHE["refresh"] = float(cfg.get("refresh_time", 30))
    CACHE["files"] = _scan(CACHE["dir"]) if os.path.isdir(CACHE["dir"]) else []
    CACHE["scan_time"] = time.time()
    CACHE["progress_file"] = os.path.join(CACHE["dir"], ".progress.json")
    CACHE["progress"] = _load_progress()
    return CACHE


def _load_progress():
    try:
        with open(CACHE["progress_file"], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"current": None, "history": [], "words": {}}


def _save_progress():
    try:
        with open(CACHE["progress_file"], "w", encoding="utf-8") as f:
            json.dump(CACHE["progress"], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[play_lesson] 保存进度失败:", e)


def _maybe_refresh():
    if time.time() - CACHE.get("scan_time", 0) > CACHE.get("refresh", 30):
        CACHE["files"] = _scan(CACHE["dir"])
        CACHE["scan_time"] = time.time()


# ---------------------------------------------------------------- 定位某一课
def _level_lessons(level):
    """返回该级别下所有课目录，形如 ['001_hello', '002_colors']"""
    names, seen = [], set()
    for f in CACHE["files"]:
        parts = f.split("/")
        if len(parts) >= 3 and parts[0] == level:
            if parts[1] not in seen:
                seen.add(parts[1])
                names.append(parts[1])
    return names


def _find_lesson(level, order):
    for name in _level_lessons(level):
        m = re.match(r"^0*(\d+)", name)
        if m and int(m.group(1)) == int(order):
            return f"{level}/{name}"
    return None


def _lesson_audio(lesson_id):
    return [f for f in CACHE["files"] if f.startswith(lesson_id + "/")]


def _lesson_meta(lesson_id):
    p = Path(CACHE["dir"]) / lesson_id / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------- 塞进 TTS 通道
async def _queue_say(conn, text, first=False):
    """让设备说一句话（正常合成）"""
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id,
        sentence_type=SentenceType.FIRST if first else SentenceType.MIDDLE,
        content_type=ContentType.TEXT,
        content_detail=text,
    ))


async def _queue_file(conn, path, first=False):
    """让设备直接播一个音频文件 —— 关键就这一句"""
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id,
        sentence_type=SentenceType.LAST if first else SentenceType.MIDDLE,
        content_type=ContentType.FILE,
        content_file=path,
    ))


# ---------------------------------------------------------------- 工具实现
desc_play = {
    "type": "function",
    "function": {
        "name": "play_lesson",
        "description": (
            "播放孩子的英语课音频。孩子说'学新课/听第三课/再听一遍/接着上次'时调用。"
            "level 是级别如 L1、L2；order 是第几课（从1开始）；part 是第几条音频（不填=整课全播）。"
            "resume=true 表示从上次停下的地方继续。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "description": "级别，如 L1"},
                "order": {"type": "integer", "description": "第几课，从1开始"},
                "part": {"type": "integer", "description": "只播第几条音频，从1开始"},
                "resume": {"type": "boolean", "description": "true=接着上次听"},
            },
        },
    },
}


@register_function("play_lesson", desc_play, ToolType.SYSTEM_CTL)
async def play_lesson(conn: "ConnectionHandler", level: str = "", order: int = 0,
                      part: int = 0, resume: bool = False):
    try:
        _init(conn)
        _maybe_refresh()
        prog = CACHE["progress"]

        # ---- 1. 决定播哪一课 ----
        if resume:
            cur = prog.get("current")
            if not cur:
                return ActionResponse(action=Action.RESPONSE, result="",
                                      response="我们还没开始听呢，说'第一课'就行。")
            lesson_id = cur["lesson"]
            start_index = int(cur.get("index", 0))
        else:
            if not level:
                level = (prog.get("current") or {}).get("lesson", "L1/").split("/")[0]
            lesson_id = _find_lesson(level, order) if order else None
            if not lesson_id:
                lv = _level_lessons(level)
                head = "、".join(x.split("_", 1)[-1] for x in lv[:6])
                return ActionResponse(action=Action.RESPONSE, result="",
                                      response=f"没找到这一课。{level} 里有：{head}。")
            start_index = 0

        files = _lesson_audio(lesson_id)
        if not files:
            return ActionResponse(action=Action.RESPONSE, result="",
                                  response="这一课还没有音频文件。")
        if part:
            if part > len(files):
                return ActionResponse(action=Action.RESPONSE, result="",
                                      response=f"这一课只有 {len(files)} 条音频。")
            files, start_index = [files[part - 1]], 0
        else:
            files = files[start_index:]

        meta = _lesson_meta(lesson_id)
        title = meta.get("title") or lesson_id.split("/")[-1]

        # ---- 2. 记进度（播之前就记，被打断也不丢） ----
        prog["current"] = {"lesson": lesson_id, "index": start_index, "at": time.strftime("%F %T")}
        hist = prog.get("history", [])
        hist = [h for h in hist if h.get("lesson") != lesson_id] + [
            {"lesson": lesson_id, "index": start_index, "at": time.strftime("%F %T")}]
        prog["history"] = hist[-20:]
        _save_progress()

        # ---- 3. 先说话，再推文件 ----
        await _queue_say(conn, f"好，我们听{title}。", first=True)
        for i, rel in enumerate(files):
            await _queue_file(conn, os.path.join(CACHE["dir"], rel),
                              first=(i == 0))
        return ActionResponse(action=Action.RECORD, result="正在播放",
                              response="")  # response 留空，别让模型再啰嗦

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"play_lesson 出错: {e}")
        return ActionResponse(action=Action.RESPONSE, result=str(e),
                              response="播放出错了，我们再来一次好不好。")


desc_prog = {
    "type": "function",
    "function": {
        "name": "lesson_progress",
        "description": "查看孩子学到哪一课了、上次听到第几条。孩子问'我学到哪了'时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}


@register_function("lesson_progress", desc_prog, ToolType.SYSTEM_CTL)
async def lesson_progress(conn: "ConnectionHandler"):
    _init(conn)
    prog = CACHE["progress"]
    cur = prog.get("current")
    if not cur:
        return ActionResponse(action=Action.RESPONSE, result="",
                              response="还没开始听呢，我们从第一课开始吧。")
    lv = cur["lesson"].split("/")[0]
    return ActionResponse(action=Action.RESPONSE, result=cur["lesson"],
                          response=f"上次听到{lv}的第{cur['index'] + 1}条，要不要接着听？")
