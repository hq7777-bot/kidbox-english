"""连续播放整册课文（磨耳朵用）。

和 play_music 的区别：
- play_music：播**一个**单元，播完就停（适合精听）
- loop_play：把整册 16 篇**连着播**，可以循环 N 遍（适合熟听/背景音）

用法（孩子直接说）：
    循环播放 3-3 课文          → 3-3 的 16 篇连播一遍（23 分钟）
    循环播放 3-3 课文 两遍      → 连播两遍（46 分钟）
    循环播放 3-3 单词          → 16 个 KeyWords 单元连播

中断：直接喊唤醒词（"你好心心"）即可打断，框架会清空队列。
"""

import re
import os
import glob
import traceback

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType

TAG = "LoopPlay"
LESSON_ROOT = "/opt/xiaozhi-esp32-server/data/lessons/SmartReading3"

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

LOOP_PLAY_FUNCTION = {
    "type": "function",
    "function": {
        "name": "loop_play",
        "description": "整册连续播放。用户说「循环播放/连着放/一遍一遍放/磨耳朵」+ 书号时用本工具，"
                       "不要用 play_music（play_music 只播单个单元）。"
                       "参数 unit 填书号如 3-3；kind 填 课文 或 单词；"
                       "用户说了遍数（两遍、三遍）就填 times，没说就默认 1。"
                       "用户只说「播放 3-3 U15」这种单个单元时不要用本工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {"type": "string", "description": "书号，如 3-3、3.3、3杠3"},
                "kind": {"type": "string", "description": "课文 或 单词，默认 课文"},
                "times": {"type": "integer", "description": "循环遍数，默认 1，最大 5"},
            },
            "required": ["unit"],
        },
    },
}


def _parse_book(text):
    """'3-3' / '3.3' / '3杠3' / '三杠三' / 'book3-3' → '3-3'"""
    s = (text or "").lower().replace("book", " ").replace("杠", "-").replace("之", "-")
    s = s.replace("点", "-").replace("－", "-").replace("—", "-")
    # 中文数字 → 阿拉伯数字（"三杠三" → "3-3"）
    for cn, d in (("一", "1"), ("二", "2"), ("三", "3"), ("四", "4"),
                  ("五", "5"), ("六", "6"), ("七", "7"), ("八", "8"),
                  ("九", "9"), ("十", "10")):
        s = s.replace(cn, d)
    s = re.sub(r"(\d)\s*[.．]\s*(\d)", r"\1-\2", s)
    m = re.search(r"(\d)\s*[-_ ]\s*(\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _parse_times(text, default=1):
    """从原话里抠遍数：两遍 / 2遍 / 三次 / 循环3遍"""
    s = text or ""
    m = re.search(r"(\d+)\s*(?:遍|次|回|轮)", s)
    if m:
        return max(1, min(5, int(m.group(1))))
    m = re.search(r"([一二两三四五六七八九十])\s*(?:遍|次|回|轮)", s)
    if m:
        return max(1, min(5, _CN_NUM.get(m.group(1), default)))
    return default


def _collect(book, kind):
    """返回 [(path, 标签)]，按 U01→U16 排序"""
    kw = "KeyWords" if ("单" in (kind or "") or "word" in (kind or "").lower()) else "Passage"
    files = glob.glob(os.path.join(LESSON_ROOT, book, f"U*-{kw}.mp3"))
    out = []
    for f in sorted(files):
        m = re.search(r"U(\d+)-", os.path.basename(f))
        if m:
            out.append((f, f"U{int(m.group(1)):02d}"))
    return out


@register_function("loop_play", LOOP_PLAY_FUNCTION, ToolType.SYSTEM_CTL)
def loop_play(conn, unit="3-3", kind="课文", times=1, **_):
    try:
        book = _parse_book(unit)
        if not book:
            return ActionResponse(Action.REQLLM,
                                  "没听清是哪一本，请说 3杠1 / 3杠2 / 3杠3。", None)

        items = _collect(book, kind)
        if not items:
            return ActionResponse(Action.REQLLM,
                                  f"没找到 {book} 的音频，可能还没下载。", None)

        try:
            times = max(1, min(5, int(times)))
        except Exception:
            times = 1

        kind_cn = "单词" if "KeyWords" in items[0][0] else "课文"
        label = f"{book.replace('-', '杠')} {kind_cn}"
        if times > 1:
            label += f" {times}遍"

        # 队列：FIRST(ACTION) → TEXT(一行播报) → FILE × N → LAST(ACTION)
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
        total = 0
        for _ in range(times):
            for path, _tag in items:
                conn.tts.tts_text_queue.put(
                    TTSMessageDTO(sentence_id=conn.sentence_id,
                                  sentence_type=SentenceType.MIDDLE,
                                  content_type=ContentType.FILE,
                                  content_file=path)
                )
                total += 1
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(sentence_id=conn.sentence_id,
                          sentence_type=SentenceType.LAST,
                          content_type=ContentType.ACTION)
        )

        conn.logger.bind(tag=TAG).info(
            f"循环播放: {book} {kind_cn} × {len(items)} 篇 × {times} 遍 = {total} 个文件"
        )
        # 音频已入队，别再让模型说话
        return ActionResponse(Action.RECORD, None, None)

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"循环播放失败: {e}")
        conn.logger.bind(tag=TAG).error(traceback.format_exc())
        return ActionResponse(Action.REQLLM, "播放出问题了。", None)
