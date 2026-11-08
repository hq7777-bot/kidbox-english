import os
import re
import time
import random
import difflib
import traceback
from pathlib import Path
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

MUSIC_CACHE = {}

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "播放教材/课程音频库里的音频。用户说\"播放 XXX\"时调用。"
                       "song_name 必须原样保留用户说的书号、单元号和类型（课文/单词），不要翻译或改写："
                       "用户说\"播放三杠二第五单元的课文\"→参数：三杠二第五单元的课文；"
                       "用户说\"播放3-1第12课\"→参数：3-1第12课；"
                       "用户说\"播放hello\"→参数：hello；"
                       "用户只说\"播放音乐\"没说播哪个→参数：random",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "要播放的音频名称，原样保留用户说法（可含书号/单元号/课文或单词）；未指定则填 random",
                }
            },
            "required": ["song_name"],
        },
    },
}


def _make_label(path):
    """从文件路径生成播放标识，例如 '3杠3 U15 课文'。"""
    if not path:
        return None
    book, unit, kind = _parse_file_ref(path)
    if not book and not unit:
        return None
    parts = []
    if book:
        parts.append(book.replace("-", "杠"))
    if unit:
        parts.append(f"U{unit:02d}" if unit < 10 else f"U{unit}")
    parts.append("单词" if kind == "keywords" else "课文")
    return " ".join(parts)


@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
async def play_music(conn: "ConnectionHandler", song_name: str):
    try:
        # 先算一遍匹配，拿到要播的文件，用于生成简短播报标识
        label = None
        try:
            matched = _find_best_match(song_name, MUSIC_CACHE.get("music_files", []))
            label = _make_label(matched)
        except Exception:
            label = None

        music_intent = (
            f"播放音乐 {song_name}" if song_name != "random" else "随机播放音乐"
        )
        await handle_music_command(conn, music_intent)

        # 播报语由 play_local_music 内部发出（只说序号），这里返回 RECORD 避免重复播报
        return ActionResponse(
            action=Action.RECORD, result="指令已接收", response=label or "好"
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"处理音乐意图错误: {e}")
        return ActionResponse(
            action=Action.RESPONSE, result=str(e), response="播放出错了"
        )


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    for keyword in ["播放音乐"]:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                return parts[1].strip()
    return None


_CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
           "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20}


def _cn_to_int(s):
    """把'一''十二''3'之类转成整数，转不了返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def _parse_ref(text):
    """从一句话里解析出 (书号, 单元号, 类型)，能解析多少算多少。

    要能吃下这些说法（含 ASR 常见误识别）：
      "播放book三点三15课课文"  / "3杠三unit15 passenge" / "播放文章book三点三15单元"
      "3-3 第15单元 passage"  / "播放3.3 u15 keywords" / "第15课" / "unit 15 passage"
    """
    s = (text or "").lower()
    # 顺序很重要：先把"点/杠/之/book"归一，并把 3.3 / 三.三 这类转成 3-3，
    # 之后再清标点（否则 "3.3" 里的点会被当标点吃掉）
    s = s.replace("book", " ").replace("点", ".").replace("杠", "-").replace("之", "-")
    s = re.sub(r"([0-9一二三四五六])\s*[.．]\s*([1-3一二三])", r"\1-\2", s)
    s = re.sub(r"[，。！？、,.!?]+", " ", s)
    s = re.sub(r"[—–]", "-", s)
    s = re.sub(r"\s+", " ", s)
    book = unit = kind = None

    # 书号：3.3 / 3-3 / 三.三 / 三-三；匹配后把这段抹掉，避免"三.三15课"里的 15 干扰
    m = re.search(r"([1-6一二三四五六])\s*[.\-]\s*([1-3一二三])", s)
    if m:
        a, b = _cn_to_int(m.group(1)), _cn_to_int(m.group(2))
        if a and b:
            book = f"{a}-{b}"
            s = s[:m.start()] + " " + s[m.end():]

    # 类型（容忍 ASR 把 passage 识别成 passenge / passagen）
    if re.search(r"pass|课文|短文|文章|阅读|正文", s):
        kind = "passage"
    elif re.search(r"key\s*words?|keywords|单词|词汇|生词", s):
        kind = "keywords"

    # 单元号：u15 / unit 15 / unit15 / 第15单元 / 15课 / 第十五课
    m2 = re.search(r"\bu\s*0*(\d{1,2})\b", s) or re.search(r"unit\s*0*(\d{1,2})", s)
    if m2:
        unit = int(m2.group(1))
    else:
        m3 = re.search(r"(?:第\s*)?([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:单元|课)", s)
        if m3:
            unit = _cn_to_int(m3.group(1))

    return book, unit, kind


def _parse_file_ref(path):
    """从 'SmartReading3/3-2/U05-KeyWords.mp3' 解析出 (书号, 单元号, 类型)。"""
    s = path.lower()
    book = unit = kind = None
    m = re.search(r"(\d)\s*-\s*(\d)[/\\]", s)
    if m:
        book = f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"[/\\]u(\d{1,2})[-_ ]", s)
    if m:
        unit = int(m.group(1))
    if "passage" in s:
        kind = "passage"
    elif "keyword" in s:
        kind = "keywords"
    return book, unit, kind


def _find_best_match(potential_song, music_files):
    """查找最匹配的音频文件。

    改动（相对官方 v0.9.8，多次迭代后）：
    1. 忽略大小写；
    2. 显式解析"书号-单元号-类型"（如 3-2 / U05 / Passage）做硬过滤：
       否则 96 个教材音频里 "3-3 u16 passage" 会错配到 "3-1/U16-Passage"（书号被拆散后撞车）；
    3. 没解析出结构信息时，退回按"词覆盖 + 相似度"排序（这样"播放 001""hello"仍能用）；
    4. 完全相等直接返回。
    """
    query = (potential_song or "").strip().lower()
    if not query:
        return None

    q_book, q_unit, q_kind = _parse_ref(query)
    structured = any((q_book, q_unit, q_kind))

    def tokenize(s):
        return set(t for t in re.split(r"[\s\-_/,.]+", s.lower()) if t)

    q_tokens = tokenize(query)
    best_file, best_score = None, -1.0
    best_cover = 0.0

    for music_file in music_files:
        stem = os.path.splitext(music_file)[0].lower()
        if stem == query:
            return music_file

        f_book, f_unit, f_kind = _parse_file_ref(music_file)

        if structured:
            # 只要查询里给了明确条件，文件必须逐条满足
            if q_book and f_book != q_book:
                continue
            if q_unit and f_unit != q_unit:
                continue
            if q_kind and f_kind != q_kind:
                continue

        norm = re.sub(r"[\-_/]+", " ", stem).strip()
        ratio = difflib.SequenceMatcher(None, query, norm).ratio()
        cover = (len(q_tokens & tokenize(stem)) / len(q_tokens)) if q_tokens else 0.0
        score = cover * 2.0 + ratio
        # 没说书号时，优先孩子正在学的那本（SmartReading3），避免"第15课"随机跳到 1-1/2-1
        if structured and not q_book and "smartreading3" in music_file.lower():
            score += 0.6
        if score > best_score:
            best_file, best_score, best_cover = music_file, score, cover

    if best_file is None:
        return None
    if structured:
        return best_file          # 结构条件已经过滤过，剩下的就是候选
    return best_file if best_cover >= 0.5 else None


def get_music_files(music_dir, music_ext):
    music_dir = Path(music_dir)
    music_files = []
    music_file_names = []
    for file in music_dir.rglob("*"):
        # 判断是否是文件
        if file.is_file():
            # 获取文件扩展名
            ext = file.suffix.lower()
            # 判断扩展名是否在列表中
            if ext in music_ext:
                # 添加相对路径
                music_files.append(str(file.relative_to(music_dir)))
                music_file_names.append(
                    os.path.splitext(str(file.relative_to(music_dir)))[0]
                )
    return music_files, music_file_names


def initialize_music_handler(conn: "ConnectionHandler"):
    global MUSIC_CACHE
    if MUSIC_CACHE == {}:
        plugins_config = conn.config.get("plugins", {})
        if "play_music" in plugins_config:
            MUSIC_CACHE["music_config"] = plugins_config["play_music"]
            MUSIC_CACHE["music_dir"] = os.path.abspath(
                MUSIC_CACHE["music_config"].get("music_dir", "./music")  # 默认路径修改
            )
            MUSIC_CACHE["music_ext"] = MUSIC_CACHE["music_config"].get(
                "music_ext", (".mp3", ".wav", ".p3")
            )
            MUSIC_CACHE["refresh_time"] = MUSIC_CACHE["music_config"].get(
                "refresh_time", 60
            )
        else:
            MUSIC_CACHE["music_dir"] = os.path.abspath("./music")
            MUSIC_CACHE["music_ext"] = (".mp3", ".wav", ".p3")
            MUSIC_CACHE["refresh_time"] = 60
        # 获取音乐文件列表
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
        )
        MUSIC_CACHE["scan_time"] = time.time()
    return MUSIC_CACHE


async def handle_music_command(conn: "ConnectionHandler", text):
    initialize_music_handler(conn)
    global MUSIC_CACHE

    """处理音乐播放指令"""
    # 只剥掉标点，**必须保留 . - _ / 和数字**，否则 "3-3" 会被削成 "33"，书号直接丢失
    clean_text = re.sub(r"[^\w\s.\-/]", "", text).strip()
    conn.logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

    # 尝试匹配具体歌名
    if os.path.exists(MUSIC_CACHE["music_dir"]):
        if time.time() - MUSIC_CACHE["scan_time"] > MUSIC_CACHE["refresh_time"]:
            # 刷新音乐文件列表
            MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = (
                get_music_files(MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"])
            )
            MUSIC_CACHE["scan_time"] = time.time()

        potential_song = _extract_song_name(clean_text)
        if potential_song:
            best_match = _find_best_match(potential_song, MUSIC_CACHE["music_files"])
            if best_match:
                conn.logger.bind(tag=TAG).info(f"找到最匹配的歌曲: {best_match}")
                await play_local_music(conn, specific_file=best_match)
                return True
    # 检查是否是通用播放音乐命令
    await play_local_music(conn)
    return True


def _get_random_play_prompt(song_name):
    """播放前的播报语：只说序号，例如「3杠3 U15 课文」，不要任何铺垫。"""
    label = _make_label(song_name)
    return label if label else "好"


async def play_local_music(conn: "ConnectionHandler", specific_file=None):
    global MUSIC_CACHE
    """播放本地音乐文件"""
    try:
        if not os.path.exists(MUSIC_CACHE["music_dir"]):
            conn.logger.bind(tag=TAG).error(
                f"音乐目录不存在: " + MUSIC_CACHE["music_dir"]
            )
            return

        # 确保路径正确性
        if specific_file:
            selected_music = specific_file
            music_path = os.path.join(MUSIC_CACHE["music_dir"], specific_file)
        else:
            if not MUSIC_CACHE["music_files"]:
                conn.logger.bind(tag=TAG).error("未找到MP3音乐文件")
                return
            selected_music = random.choice(MUSIC_CACHE["music_files"])
            music_path = os.path.join(MUSIC_CACHE["music_dir"], selected_music)

        if not os.path.exists(music_path):
            conn.logger.bind(tag=TAG).error(f"选定的音乐文件不存在: {music_path}")
            return
        text = _get_random_play_prompt(selected_music)
        conn.tts.store_tts_text(conn.sentence_id, text)
        # conn.dialogue.put(Message(role="assistant", content=text))

        if conn.intent_type == "intent_llm":
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=conn.sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
            )
        )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.FILE,
                content_file=music_path,
            )
        )
        if conn.intent_type == "intent_llm":
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=conn.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"播放音乐失败: {str(e)}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
