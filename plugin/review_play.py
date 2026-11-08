"""熟听（磨耳朵）—— 三单元滚动窗口，每次 2 遍，进度可续。

和另外两个播放工具的分工：
- play_music   放**一个**单元（精听）
- loop_play    整册 16 篇**连播**（周末/长时段）
- review_play  每次 3 个单元 ×2 遍，下次自动往前滑一个（每天日常）

为什么窗口=3、每次滑 1：
中间单元会被 3 次会话覆盖 → 2 遍 × 3 次 = 6 次接触，踩在"6 次才可能记住"的门槛上；
开头两个单元是昨天听过的，孩子一进来就有熟悉感。

用法：
    熟听 3-3                → 接着上次的进度放（首次 = U01 U02 U03）
    从 3-3 U3 开始熟听        → 进度设到 U03，放 U03 U04 U05
    熟听 3-3 重头             → 进度归 U01
    循环播放 3-3 课文         → 那是 loop_play，整册连播

进度按册独立存 data/loop_progress.json，容器重启不丢。
**下令时前进**（不是播完才记账）——被叫停也算学过一遍，否则进度永远推不动。
"""

import os
import re
import json
import time
import traceback

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType

TAG = "ReviewPlay"
LESSON_ROOT = "/opt/xiaozhi-esp32-server/data/lessons/SmartReading3"
STATE_FILE = "/opt/xiaozhi-esp32-server/data/loop_progress.json"

WINDOW = 3          # 每次播几个单元
TIMES = 2           # 每个单元播几遍
LAST_UNIT = 16      # 每册单元数

_CN = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
       "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

REVIEW_PLAY_FUNCTION = {
    "type": "function",
    "function": {
        "name": "review_play",
        "description": "熟听（磨耳朵）。用户说「熟听」/「磨耳朵」+ 书号时用本工具："
                       "每次播 3 个单元、每单元 2 遍，下次自动往前滑一个单元。"
                       "用户说「从 3-3 U5 开始熟听」就把 start 填 5；"
                       "说「重头/从头开始」就把 reset 填 true。"
                       "用户说「循环播放/整册连播」时不要用本工具（那是 loop_play）。",
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {"type": "string", "description": "书号，如 3-3、3.3、三杠三"},
                "start": {"type": "integer", "description": "从第几个单元开始（1-16），用户没指定就别填"},
                "reset": {"type": "boolean", "description": "用户说重头开始时填 true"},
            },
            "required": ["unit"],
        },
    },
}


def _parse_book(text):
    s = (text or "").lower().replace("book", " ").replace("杠", "-").replace("之", "-")
    s = s.replace("点", "-").replace("－", "-").replace("—", "-")
    for cn, d in _CN.items():
        s = s.replace(cn, d)
    s = re.sub(r"(\d)\s*[.．]\s*(\d)", r"\1-\2", s)
    m = re.search(r"(\d)\s*[-_ ]\s*(\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _parse_start(text):
    """'从3-3 U5开始熟听' → 5；'第5单元' → 5；没有 → None"""
    s = (text or "").lower()
    m = re.search(r"u\s*(\d{1,2})", s)
    if m:
        return int(m.group(1))
    m = re.search(r"第\s*(\d{1,2})\s*(?:单元|课)", s)
    if m:
        return int(m.group(1))
    return None


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception:
        return False


def _path(book, unit):
    for kind in ("Passage",):  # 熟听只放课文
        p = os.path.join(LESSON_ROOT, book, f"U{unit:02d}-{kind}.mp3")
        if os.path.exists(p):
            return p
    return None


@register_function("review_play", REVIEW_PLAY_FUNCTION, ToolType.SYSTEM_CTL)
def review_play(conn, unit="3-3", start=None, reset=False, **_):
    try:
        book = _parse_book(unit)
        if not book:
            return ActionResponse(Action.REQLLM, "没听清是哪一本，请说 3杠1 / 3杠2 / 3杠3。", None)

        # start 可能是字符串（模型有时传 "U5"），统一成 int
        if start is not None:
            if isinstance(start, str):
                start = _parse_start(start)
            try:
                start = int(start)
            except Exception:
                start = None

        state = _load_state()
        saved = state.get(book, {}).get("next", 1)
        try:
            saved = int(saved)
        except Exception:
            saved = 1

        if reset:
            begin = 1
        elif start:
            begin = max(1, min(LAST_UNIT, start))
        else:
            # 已经放过最后一窗（next 会被推到 15）→ 提示重头，别死循环放最后 3 篇
            if saved > LAST_UNIT - WINDOW + 1:
                return ActionResponse(
                    Action.REQLLM,
                    f"{book.replace('-', '杠')} 这册已经放完了，要重头来一遍吗？",
                    None,
                )
            begin = max(1, saved)

        # 到顶收缩：窗口不越过 U16，也不循环回 U01
        begin = min(begin, LAST_UNIT - WINDOW + 1)

        units = [u for u in range(begin, min(begin + WINDOW - 1, LAST_UNIT) + 1)]
        files = [(u, _path(book, u)) for u in units]
        files = [(u, p) for u, p in files if p]
        if not files:
            return ActionResponse(Action.REQLLM, f"没找到 {book} 的课文音频。", None)

        # 下令即前进（next 会到 15，下次触发"放完了"提示）
        state.setdefault(book, {})["next"] = begin + 1
        state[book]["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _save_state(state)

        label = "熟听 " + " ".join(f"U{u:02d}" for u, _ in files)
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(sentence_id=conn.sentence_id,
                          sentence_type=SentenceType.FIRST,
                          content_type=ContentType.ACTION)
        )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(sentence_id=conn.sentence_id,
                          sentence_type=SentenceType.MIDDLE,
                          content_type=ContentType.TEXT,
                          content_detail=label)
        )
        for _ in range(TIMES):
            for _u, p in files:
                conn.tts.tts_text_queue.put(
                    TTSMessageDTO(sentence_id=conn.sentence_id,
                                  sentence_type=SentenceType.MIDDLE,
                                  content_type=ContentType.FILE,
                                  content_file=p)
                )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(sentence_id=conn.sentence_id,
                          sentence_type=SentenceType.LAST,
                          content_type=ContentType.ACTION)
        )

        conn.logger.bind(tag=TAG).info(
            f"熟听: {book} 窗口 {[u for u, _ in files]} × {TIMES} 遍，"
            f"下次从 U{state[book]['next']:02d} 开始"
        )
        return ActionResponse(Action.RECORD, None, None)

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"熟听失败: {e}")
        conn.logger.bind(tag=TAG).error(traceback.format_exc())
        return ActionResponse(Action.REQLLM, "播放出问题了。", None)
