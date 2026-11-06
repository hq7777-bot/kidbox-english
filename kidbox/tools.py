"""工具集：AI 通过 function calling 操控硬件的"手脚"。

每个工具都返回一句中文结果给模型，模型再决定怎么跟孩子说。
播放类工具会直接开动播放器，模型只该说一句很短的确认语。
"""
import json

from . import config, db, library, search


TOOLS = [
    {"type": "function", "function": {
        "name": "play_next",
        "description": "播放当前级别的下一课。孩子说“下一课/继续学新的/播放下一级”时用。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "play_resume",
        "description": "继续上次没听完的那一课，从上次停下的地方接着播。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "play_lesson",
        "description": "播放指定的课。可用 order（第几课）或 keyword（课文关键词，如 colors/hello）。",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "string", "description": "级别，如 L1/L2；不填用当前级别"},
            "order": {"type": "integer", "description": "该级别的第几课，从1开始"},
            "keyword": {"type": "string", "description": "课文标题或内容关键词"}}}}},
    {"type": "function", "function": {
        "name": "set_level",
        "description": "切换级别，用于“我想听第二级/回到第一级/复习前面的”。",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "string", "description": "级别名，如 L1"}},
            "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "repeat_audio",
        "description": "把正在播的这一个音频从头再放一遍（孩子说“再听一遍/没听清”）。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "stop_playback",
        "description": "停止播放。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_speed",
        "description": "调整播放速度，0.75=慢一点，1.0=正常，1.25=快一点。",
        "parameters": {"type": "object", "properties": {
            "rate": {"type": "number", "description": "0.5 到 1.5 之间"}},
            "required": ["rate"]}}},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "调整音量（百分比，儿童上限由设备限制）。",
        "parameters": {"type": "object", "properties": {
            "percent": {"type": "integer"}}, "required": ["percent"]}}},
    {"type": "function", "function": {
        "name": "next_quiz_word",
        "description": "取出一个该复习的单词，用来带孩子记单词。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "grade_word",
        "description": "给孩子刚才的回答打分并记录：答对 correct=true，答错/不会 correct=false。",
        "parameters": {"type": "object", "properties": {
            "word": {"type": "string"},
            "correct": {"type": "boolean"}},
            "required": ["word", "correct"]}}},
    {"type": "function", "function": {
        "name": "explain",
        "description": "需要查资料才能回答的科普问题（动物、太空、为什么……）调用它，会联网取资料。",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "要检索的问题，用简洁关键词"}},
            "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "progress_report",
        "description": "查看学习进度：有哪些级别、当前在哪一课、待复习单词数。",
        "parameters": {"type": "object", "properties": {}}}},
]


class Ctx:
    """工具运行需要的上下文。"""

    def __init__(self, conn, player):
        self.conn = conn
        self.player = player
        self.state = {
            "level": db.kv_get(conn, "current_level", config.DEFAULT_LEVEL),
            "lesson_id": db.kv_get(conn, "current_lesson", None),
        }

    # ---- 内部：真正启动播放并记住状态 ----
    def start(self, lesson, index=0, pos=0.0):
        self.player.play(lesson["files"], start_index=index, start_sec=pos)
        self.state["lesson_id"] = lesson["id"]
        self.state["level"] = lesson["level"]
        db.kv_set(self.conn, "current_lesson", lesson["id"])
        db.kv_set(self.conn, "current_level", lesson["level"])
        db.save_progress(self.conn, lesson["id"], index, pos, finished=False)

    def snapshot(self):
        """把播放器当前位置写回 DB —— 主循环每 2 秒调一次，断电最多丢 2 秒。"""
        cur = self.player.current()
        lid = self.state.get("lesson_id")
        if cur and lid:
            db.save_progress(self.conn, lid, cur[0], cur[1], finished=False)


def _row_to_lesson(row):
    row = dict(row)
    row["files"] = json.loads(row["files"])
    return row


def dispatch(name, args, ctx: Ctx):
    """执行一个工具调用，返回 (给模型看的字符串, 是否需要模型少说话)。"""
    a = args or {}
    p = ctx.player

    if name == "play_next":
        lv = ctx.state["level"]
        nxt = library.next_lesson(ctx.conn, lv, after_id=ctx.state.get("lesson_id"))
        if not nxt:
            first = library.first_of_level(ctx.conn, lv)
            if not first:
                return f"{lv} 还没有音频，请先把音频放进 library/{lv}/。", False
            ctx.start(_row_to_lesson(first))
            return f"{lv} 已经学完了，从第一课 {first['title']} 重新开始。", True
        ctx.start(_row_to_lesson(nxt))
        return f"开始播放 {nxt['level']} 第 {nxt['ord']} 课《{nxt['title']}》。", True

    if name == "play_resume":
        prog = db.last_played(ctx.conn)
        if not prog:
            return "还没有未听完的内容，可以说“下一课”开始新的。", False
        lesson = library.get_lesson(ctx.conn, prog["lesson_id"])
        if not lesson:
            return "上次那课的音频找不到了，可能被移动过。", False
        ctx.start(lesson, prog["file_index"], prog["position"])
        return (f"继续播放《{lesson['title']}》，从第 {prog['file_index'] + 1} 个音频的 "
                f"{int(prog['position'])} 秒接着听。"), True

    if name == "play_lesson":
        lv = a.get("level") or ctx.state["level"]
        rows = [dict(r) for r in ctx.conn.execute(
            "SELECT * FROM lessons WHERE level=? ORDER BY ord", (lv,)).fetchall()]
        if not rows:
            return f"没有找到级别 {lv} 的音频。", False
        target = None
        if a.get("keyword"):
            hits = library.find(ctx.conn, a["keyword"])
            target = next((h for h in hits if h["level"] == lv), hits[0] if hits else None)
        elif a.get("order"):
            target = next((r for r in rows if r["ord"] == int(a["order"])), None)
        if not target:
            titles = "、".join(f"{r['ord']}.{r['title']}" for r in rows[:6])
            return f"没找到那一课。{lv} 里有：{titles}。", False
        lesson = library.get_lesson(ctx.conn, target["id"])
        if not lesson:
            return "那一课的音频不见了。", False
        ctx.start(lesson)
        return f"开始播放 {lesson['level']} 第 {lesson['ord']} 课《{lesson['title']}》。", True

    if name == "set_level":
        lv = (a.get("level") or "").strip()
        existing = [x for x, _ in library.levels(ctx.conn)]
        if lv not in existing:
            return f"没有这个级别。现有级别：{'、'.join(existing) or '（空）'}。", False
        db.kv_set(ctx.conn, "current_level", lv)
        ctx.state["level"] = lv
        return f"已经切换到 {lv}。", False

    if name == "repeat_audio":
        cur = p.current()
        if not cur or not ctx.state.get("lesson_id"):
            return "现在没有在播放内容。", False
        lesson = library.get_lesson(ctx.conn, ctx.state["lesson_id"])
        ctx.start(lesson, cur[0], 0.0)
        return "从头再放一遍。", True

    if name == "stop_playback":
        ctx.snapshot()
        p.stop()
        return "已经停止播放。", False

    if name == "set_speed":
        rate = float(a.get("rate", 1.0))
        rate = max(0.5, min(1.5, rate))
        cur = p.current()
        lesson = library.get_lesson(ctx.conn, ctx.state.get("lesson_id") or "")
        p.set_speed(rate)
        if cur and lesson:
            ctx.start(lesson, cur[0], cur[1])
        return f"播放速度调整为 {rate:g} 倍。", False

    if name == "set_volume":
        v = max(0, min(config.MAX_VOLUME, int(a.get("percent", 60))))
        p.set_volume(v)
        return f"音量设为 {v}%（设备上限 {config.MAX_VOLUME}%）。", False

    if name == "next_quiz_word":
        words = db.due_words(ctx.conn, 1)
        if not words:
            row = ctx.conn.execute("SELECT * FROM word_review ORDER BY box ASC, due ASC LIMIT 1").fetchone()
            if not row:
                return "词库里还没有单词，请先放带 words 的 meta.json。", False
            words = [dict(row)]
        w = words[0]
        return (f"复习单词：{w['word']}（第{w['box']}盒，已对{w['right']}次错{w['wrong']}次）。"
                f"请用中文问孩子它是什么意思，或让孩子跟读。"), False

    if name == "grade_word":
        word = a.get("word", "")
        if not word:
            return "缺少单词。", False
        box = db.grade_word(ctx.conn, word, bool(a.get("correct")))
        return f"已记录：{word} {'答对' if a.get('correct') else '需要再练'}，下次复习在第 {box} 盒。", False

    if name == "explain":
        q = a.get("question", "").strip()
        if not q:
            return "缺少要查的问题。", False
        ok, res = search.search(q)
        if not ok:
            return f"{res}\n请基于你已有的知识回答，并用孩子能听懂的话说明这是常识性解释。", False
        if not res:
            return "搜索没有返回结果，请直接说明你不确定。", False
        body = "\n".join(f"- {x['title']}：{x['snippet']}" for x in res)
        return ("以下是联网查到的资料，请用一年级孩子能懂的话概括成两三句，不要念网址：\n" + body), False

    if name == "progress_report":
        lv = library.levels(ctx.conn)
        lvs = "、".join(f"{a_}（{n}课）" for a_, n in lv) or "暂无"
        prog = db.last_played(ctx.conn)
        due = len(db.due_words(ctx.conn, 99))
        tail = (f"上次听到《{prog['lesson_id'].split('/')[-1]}》第 {prog['file_index'] + 1} 个音频"
                if prog else "还没开始听")
        return f"级别：{lvs}；当前级别 {ctx.state['level']}；{tail}；待复习单词 {due} 个。", False

    return f"未知工具 {name}。", False
