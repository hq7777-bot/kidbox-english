"""逐句跟读插件（v1）。

流程：孩子说「跟读 3-3 U15」→ 播第 1 句 → 停下等 → 孩子跟读 → ASR→比对 → 下一句 / 重播。

设计要点：
- 状态存内存（key = 设备 id），进程重启会清空（v1.5 再落 SQLite）
- 判定只用文本相似度（difflib），不接评分 API
- 连续 2 次不达标 → 不卡住，直接下一句（挫败感 > 准确性）
- 播报极简：通过=「好」，不过=「再读一次」，读完=「读完啦」
"""
import difflib
import json
import re
import traceback
from pathlib import Path

from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from plugins_func.register import Action, ActionResponse, ToolType, register_function

TAG = "FollowReading"
FOLLOW_ROOT = Path("/opt/xiaozhi-esp32-server/data/follow/SmartReading3")
STATE = {}   # {key: {"dir": Path, "data": {...}, "index": int, "retries": int}}
MAX_RETRY = 2

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
       "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16}


def _to_int(s):
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    return _CN.get(s)


def _key(conn):
    return getattr(conn, "device_id", None) or f"conn-{id(conn)}"


def _norm(text):
    t = (text or "").lower().strip()
    t = re.sub(r"[^a-z0-9' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _score(said, target):
    a, b = _norm(said), _norm(target)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _parse_unit(text):
    """'3-3 U15' / '3杠3第十五课' / '3.3 unit15 passage' → (book, unit, kind)"""
    s = (text or "").lower()
    s = s.replace("book", " ").replace("杠", "-").replace("之", "-").replace("点", "-")
    s = re.sub(r"[—–]", "-", s)
    # "3.3" 这种点分书号也要认（先转成 3-3，再做其他解析）
    s = re.sub(r"([1-6])\s*[.．]\s*([1-3])(?!\d)", r"\1-\2", s)
    book = unit = kind = None
    m2 = re.search(r"([1-6])\s*-\s*([1-3])", s)
    if m2:
        book = f"{m2.group(1)}-{m2.group(2)}"
    if re.search(r"pass|课文|短文|文章", s):
        kind = "Passage"
    elif re.search(r"key\s*words?|keywords|单词|词汇", s):
        kind = "KeyWords"
    mu = re.search(r"\bu\s*0*(\d{1,2})\b", s) or re.search(r"unit\s*0*(\d{1,2})", s)
    if mu:
        unit = int(mu.group(1))
    else:
        mc = re.search(r"(?:第\s*)?([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:单元|课)", s)
        if mc:
            unit = _to_int(mc.group(1))
    return book, unit, kind


def _resolve(unit_text):
    """找到 lesson.json 所在目录。"""
    book, unit, kind = _parse_unit(unit_text)
    book = book or "3-1"
    kind = kind or "Passage"
    if unit is None:
        return None
    d = FOLLOW_ROOT / book / f"U{unit:02d}-{kind}"
    return d if (d / "lesson.json").exists() else None


def _push_sentence(conn, d, item):
    """先播一句提示（可选），再推句子音频。"""
    path = str(d / item["file"])
    conn.tts.store_tts_text(conn.sentence_id, item.get("text", ""))
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id, sentence_type=SentenceType.MIDDLE,
        content_type=ContentType.FILE, content_file=path))
    if conn.intent_type == "intent_llm":
        conn.tts.tts_text_queue.put(TTSMessageDTO(
            sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION))


def _say(conn, text):
    conn.tts.store_tts_text(conn.sentence_id, text)
    conn.tts.tts_text_queue.put(TTSMessageDTO(
        sentence_id=conn.sentence_id, sentence_type=SentenceType.MIDDLE,
        content_type=ContentType.TEXT, content_detail=text))


def _play_current(conn, st, say_text=None):
    d = st["dir"]
    item = st["data"]["sentences"][st["index"]]
    if say_text:
        _say(conn, say_text)
    _push_sentence(conn, d, item)
    total = len(st["data"]["sentences"])
    conn.logger.bind(tag=TAG).info(
        f"跟读进度 {st['index'] + 1}/{total}: {item['text']}  (重试 {st['retries']})")


follow_desc = {
    "type": "function",
    "function": {
        "name": "follow_reading",
        "description": "逐句跟读工具。只要用户话里出现「跟读」两个字，必须调用本工具，不要用 play_music。"
                       "用户说「跟读 3-3 U15」用 action=start（unit 填用户原话）；"
                       "跟读模式下孩子每说一句，都要用 action=check 并把孩子原话放进 said（原样，别改写）；"
                       "孩子说「再读一次/没听清」用 action=repeat；说「不读了/停」用 action=stop。"
                       "除 start 之外，正常聊天不要调用本工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "check", "repeat", "stop", "status"]},
                "unit": {"type": "string", "description": "如 3-3 U15 或 3-3 U15 KeyWords；start 时必填"},
                "said": {"type": "string", "description": "check 时填孩子刚才说的原话"},
            },
            "required": ["action"],
        },
    },
}


@register_function("follow_reading", follow_desc, ToolType.SYSTEM_CTL)
async def follow_reading(conn, action="start", unit="", said=""):
    try:
        k = _key(conn)
        st = STATE.get(k)

        if action == "stop":
            STATE.pop(k, None)
            return ActionResponse(action=Action.RESPONSE, result="已退出跟读", response="拜拜")

        if action == "status":
            if not st:
                return ActionResponse(action=Action.RESPONSE, result="未在跟读", response="现在没在跟读")
            total = len(st["data"]["sentences"])
            return ActionResponse(action=Action.RECORD, result="ok",
                                  response=f"第{st['index'] + 1}句，共{total}句")

        if action == "start" or (action == "check" and not st):
            d = _resolve(unit or said)
            if not d:
                return ActionResponse(action=Action.RESPONSE, result="找不到单元",
                                      response="没找到这一课")
            data = json.loads((d / "lesson.json").read_text(encoding="utf-8"))
            if not data.get("sentences"):
                return ActionResponse(action=Action.RESPONSE, result="空", response="这课没有句子")
            st = {"dir": d, "data": data, "index": 0, "retries": 0}
            STATE[k] = st
            _play_current(conn, st)
            return ActionResponse(action=Action.RECORD, result="跟读开始", response="")

        if action == "repeat":
            if not st:
                return ActionResponse(action=Action.RESPONSE, result="未在跟读", response="先说要跟读哪一课")
            _play_current(conn, st)
            return ActionResponse(action=Action.RECORD, result="重播", response="")

        if action == "check":
            item = st["data"]["sentences"][st["index"]]
            s = _score(said, item.get("text", ""))
            need = float(item.get("min_score", 0.5))
            total = len(st["data"]["sentences"])
            conn.logger.bind(tag=TAG).info(f"跟读判定: said={said!r} target={item['text']!r} score={s:.2f} need={need}")

            if s >= need:
                st["retries"] = 0
                st["index"] += 1
                if st["index"] >= total:
                    STATE.pop(k, None)
                    return ActionResponse(action=Action.RESPONSE, result="完成", response="读完啦")
                _play_current(conn, st, say_text="好")
                return ActionResponse(action=Action.RECORD, result="通过", response="")

            st["retries"] += 1
            if st["retries"] >= MAX_RETRY:
                st["retries"] = 0
                st["index"] += 1
                if st["index"] >= total:
                    STATE.pop(k, None)
                    return ActionResponse(action=Action.RESPONSE, result="完成", response="读完啦")
                _play_current(conn, st, say_text="下一个")
                return ActionResponse(action=Action.RECORD, result="跳过", response="")

            _play_current(conn, st, say_text="再读一次")
            return ActionResponse(action=Action.RECORD, result="未通过", response="")

        return ActionResponse(action=Action.RESPONSE, result="未知动作", response="")
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"跟读异常: {e}\n{traceback.format_exc()}")
        return ActionResponse(action=Action.RESPONSE, result=str(e), response="跟读出错了")
