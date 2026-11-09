"""熟听（磨耳朵）—— 多书库 + 进度落盘 + 播放历史。

两个书库：
  smart_reading  3-1 / 3-2 / 3-3，每次 3 个单元 × 2 遍（Passage）
  power_up       1 级，每次 1 个单元：故事 / 歌曲 / 文学 各 2 遍

规则：
  · 每次自动往前滑一格（smart_reading 滑 1 个单元，power_up 滑 1 单元）
  · 进度落盘 data/review_progress.json（按书库+书号独立，容器重启不丢）
  · 每次播放追加一行到 data/play_history.jsonl（时间/书库/单元/文件/遍数）
  · 用户没说清是哪个书库 → 反问确认（不猜）
  · 到顶不循环，提示"这册放完了，要重头吗"

用法：
    熟听 3-3                  → Smart Reading 3-3，接着上次进度
    熟听 POWER UP 1            → POWER UP 1 级，接着上次进度
    从 3-3 U5 开始熟听          → 指定起点
    熟听 POWER UP 1 重头        → 进度归 1
"""

import os
import re
import json
import time
import glob
import traceback

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType

TAG = "ReviewPlay"
BASE = "/opt/xiaozhi-esp32-server/data/lessons"
ROOTS = {
    "smart_reading": os.path.join(BASE, "SmartReading3"),
    "power_up": os.path.join(BASE, "PowerUp1"),
}
PROGRESS_FILE = "/opt/xiaozhi-esp32-server/data/review_progress.json"
HISTORY_FILE = "/opt/xiaozhi-esp32-server/data/play_history.jsonl"

SR_WINDOW = 3      # smart reading：每次 3 个单元
PU_KINDS = ("Story", "Song", "Literature")   # power up：一单元三篇
TIMES = 2          # 每个音频播几遍

_CN = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
       "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

REVIEW_PLAY_FUNCTION = {
    "type": "function",
    "function": {
        "name": "review_play",
        "description": "熟听（磨耳朵）。用户说「熟听」/「磨耳朵」+ 素材名时调用。"
                       "两个书库：library=\"smart_reading\"（说 3-1/3-2/3-3 时）或 "
                       "library=\"power_up\"（说 POWER UP / 剑桥英语时，unit 填级别如 \"1\"）。"
                       "**用户只说了「熟听」没说哪套素材时，不要自己猜，把 library 留空**，"
                       "工具会反问用户确认。"
                       "说「从 U5 开始」就把 start 填 5；说「重头」就把 reset 填 true。",
        "parameters": {
            "type": "object",
            "properties": {
                "library": {"type": "string", "enum": ["smart_reading", "power_up"],
                            "description": "书库；不确定就别传"},
                "unit": {"type": "string", "description": "smart_reading 填书号 3-3；power_up 填级别 1"},
                "start": {"type": "integer", "description": "从第几个单元开始，用户没指定就不传"},
                "reset": {"type": "boolean", "description": "用户说重头开始时传 true"},
            },
            "required": [],
        },
    },
}


# ---------------------------------------------------------------- 工具函数
def _norm(text):
    s = (text or "").lower()
    for cn, d in _CN.items():
        s = s.replace(cn, d)
    return s


def _parse_book(text):
    """'3-3' / '3.3' / '3杠3' / '三杠三' → '3-3'"""
    s = _norm(text).replace("杠", "-").replace("之", "-").replace("点", "-")
    s = re.sub(r"(\d)\s*[.．－—_]\s*(\d)", r"\1-\2", s)
    m = re.search(r"(\d)\s*-\s*(\d)", s)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _parse_start(text):
    s = _norm(text)
    m = re.search(r"(?:u|unit|第)\s*(\d{1,2})", s)
    return int(m.group(1)) if m else None


def _detect_library(library, unit):
    """明确传了就用；否则从原话里判断；判断不了返回 None（要反问）"""
    if library in ROOTS:
        return library
    text = _norm(f"{library or ''} {unit or ''}")
    if "power" in text or re.search(r"\bp\s*1\b", text) or "剑桥" in text or "powerup" in text:
        return "power_up"
    if re.search(r"\d\s*-\s*\d", text) or "smart" in text or "reading" in text:
        return "smart_reading"
    return None


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _log_history(entry):
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _sr_files(book, unit):
    p = os.path.join(ROOTS["smart_reading"], book, f"U{unit:02d}-Passage.mp3")
    return p if os.path.exists(p) else None


def _pu_units():
    """{unit_no: {kind: path}}，文件名形如 P1-03-U01-Song.mp3"""
    out = {}
    for f in glob.glob(os.path.join(ROOTS["power_up"], "*.mp3")):
        m = re.search(r"U(\d+)-(Story|Song|Literature)\.mp3$", os.path.basename(f), re.I)
        if not m:
            continue
        out.setdefault(int(m.group(1)), {})[m.group(2).capitalize()] = f
    return out


def _enqueue(conn, label, files):
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id, sentence_type=SentenceType.FIRST,
        content_type=ContentType.ACTION))
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id, sentence_type=SentenceType.MIDDLE,
        content_type=ContentType.TEXT, content_detail=label))
    for _ in range(TIMES):
        for p in files:
            conn.tts.tts_text_queue.put(TTSMessageDTO(
                sentence_id=conn.sentence_id, sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.FILE, content_file=p))
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST,
        content_type=ContentType.ACTION))


# ---------------------------------------------------------------- 主逻辑
@register_function("review_play", REVIEW_PLAY_FUNCTION, ToolType.SYSTEM_CTL)
def review_play(conn, unit=None, library=None, start=None, reset=False, **_):
    try:
        # 0) 没指定书库 → 反问（用户说"熟听"但没说哪套素材）
        lib = _detect_library(library, unit)
        if lib is None:
            hint = f"你说的「{unit}」" if unit else "熟听"
            return ActionResponse(
                Action.REQLLM,
                f"要听哪一套？{hint}——说「熟听 3-3」放 Smart Reading，或者「熟听 POWER UP 1」放剑桥英语。",
                None,
            )

        # 兼容模型把起点塞在 unit 里（"从3-3 U5开始"）
        if start is None:
            start = _parse_start(unit)
        if isinstance(start, str):
            start = _parse_start(start)
        try:
            start = int(start) if start is not None else None
        except Exception:
            start = None

        state = _load_json(PROGRESS_FILE, {})

        if lib == "power_up":
            files_map = _pu_units()
            if not files_map:
                return ActionResponse(Action.REQLLM, "POWER UP 的音频还没放进来。", None)
            last = max(files_map)
            key = "power_up:1"
            saved = int(state.get(key, {}).get("next", 1))

            if reset:
                begin = 1
            elif start:
                begin = max(0, min(last, start))
            elif saved > last:
                return ActionResponse(Action.REQLLM,
                                      "POWER UP 1 已经听完了，要重头来一遍吗？", None)
            else:
                begin = max(0, min(last, saved))

            if begin not in files_map:
                return ActionResponse(Action.REQLLM, f"POWER UP 没有第 {begin} 单元。", None)

            kinds = files_map[begin]
            files = [kinds[k] for k in PU_KINDS if k in kinds]
            if not files:
                return ActionResponse(Action.REQLLM, f"第 {begin} 单元没有音频。", None)

            label = f"熟听 POWER UP 第{begin}单元"
            state.setdefault(key, {})["next"] = begin + 1
            state[key]["updated"] = time.strftime("%Y-%m-%d %H:%M")
            _save_json(PROGRESS_FILE, state)

            _enqueue(conn, label, files)
            _log_history({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "library": "power_up",
                          "level": 1, "unit": begin,
                          "files": [os.path.basename(f) for f in files], "times": TIMES,
                          "next": begin + 1})
            conn.logger.bind(tag=TAG).info(f"{label}: {len(files)} 篇 × {TIMES} 遍，下次 U{begin+1}")
            return ActionResponse(Action.RECORD, None, None)

        # ---------------- Smart Reading ----------------
        book = _parse_book(unit) or _parse_book(library) or "3-3"
        key = f"smart_reading:{book}"
        saved = int(state.get(key, {}).get("next", 1))
        last = 16
        tail = last - SR_WINDOW + 1          # 14

        if reset:
            begin = 1
        elif start:
            begin = max(1, min(last, start))
        elif saved > tail:
            return ActionResponse(Action.REQLLM,
                                  f"{book.replace('-', '杠')} 这册已经听完了，要重头来一遍吗？", None)
        else:
            begin = max(1, saved)
        begin = min(begin, tail)

        units = list(range(begin, min(begin + SR_WINDOW - 1, last) + 1))
        files = [f for f in (_sr_files(book, u) for u in units) if f]
        if not files:
            return ActionResponse(Action.REQLLM, f"没找到 {book} 的课文音频。", None)

        label = "熟听 " + " ".join(f"U{u:02d}" for u in units)
        state.setdefault(key, {})["next"] = begin + 1
        state[key]["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _save_json(PROGRESS_FILE, state)

        _enqueue(conn, label, files)
        _log_history({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "library": "smart_reading",
                      "book": book, "units": units,
                      "files": [os.path.basename(f) for f in files], "times": TIMES,
                      "next": begin + 1})
        conn.logger.bind(tag=TAG).info(f"{label}: {len(files)} 篇 × {TIMES} 遍，下次 U{begin+1:02d}")
        return ActionResponse(Action.RECORD, None, None)

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"熟听失败: {e}")
        conn.logger.bind(tag=TAG).error(traceback.format_exc())
        return ActionResponse(Action.REQLLM, "播放出问题了。", None)
