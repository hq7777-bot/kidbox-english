"""DeepSeek 大脑：一次对话 = 模型决定调哪个工具 → 我们执行 → 把结果回灌 → 模型说话。

DeepSeek 的 chat/completions 与 OpenAI 兼容，tools 用 function calling 标准格式。
注意：deepseek-chat 支持 tools；deepseek-reasoner 对 tools 支持较弱，别混用。
"""
import json

import requests

from . import config, db, tools

SYSTEM = """你是"豆豆"，一个陪 6-7 岁中国一年级小朋友学英语的语音小伙伴。你没有屏幕，只能说话。

说话规则（很重要）：
1. 只用口语短句，每次最多两句，不要用任何符号、表情、列表、括号。
2. 解释用中文，示范用简单英文；英文句子要短，三到六个词。
3. 播放类工具调用完之后，只回一句"好，我们听这个"这类话，然后停下来让孩子听，不要继续讲课。
4. 孩子说得不清楚或答错时，先鼓励，再重复一遍正确的，不要说"错了""不对"。
5. 不知道就说"这个我不太确定，我们下次问问老师"，绝不编造。
6. 涉及动物、太空、自然现象、"为什么"的问题，先调用 explain 查资料再回答。
7. 不谈论暴力、恐怖、成人话题；孩子提到就温和地转回英语学习。
8. 孩子说"再听一遍""没听懂"就调用 repeat_audio；说"下一课"就 play_next；说"接着听"就 play_resume。

孩子的水平：一年级，认识字母和少量单词，中文表达为主。"""


class Brain:
    def __init__(self, ctx):
        self.ctx = ctx
        self.history = []   # 只放 user/assistant 的自然语言，工具轮次不跨请求保留

    def _post(self, messages, use_tools=True):
        payload = {
            "model": config.DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 300,
        }
        if use_tools:
            payload["tools"] = tools.TOOLS
            payload["tool_choice"] = "auto"
        r = requests.post(config.DEEPSEEK_BASE.rstrip("/") + "/chat/completions",
                          headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"DeepSeek HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]

    def chat(self, user_text, max_rounds=4):
        """返回要对孩子说的话。可能过程中已经启动了播放。"""
        msgs = [{"role": "system", "content": SYSTEM}]
        for h in self.history[-6:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": user_text})
        spoke = []

        for _ in range(max_rounds):
            msg = self._post(msgs)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = (msg.get("content") or "").strip()
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": reply})
                db.log(self.ctx.conn, "user", user_text)
                db.log(self.ctx.conn, "assistant", reply)
                return reply
            msgs.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
            for c in calls:
                fn = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                try:
                    result, brief = tools.dispatch(fn, args, self.ctx)
                except Exception as e:
                    result, brief = f"工具 {fn} 执行失败：{e}", False
                if brief:
                    spoke.append(True)
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": result})

        # 工具调用打满还没收尾，兜一句
        fallback = "好，我们先听这个。" if spoke else "我们再说一次好吗？"
        db.log(self.ctx.conn, "user", user_text)
        db.log(self.ctx.conn, "assistant", fallback)
        return fallback


# ---------------------------------------------------------------- 离线模式
# 没有 API key、或者在没有网络/没有麦克风的电脑上做流程测试时用。
# 它不是"降级版 AI"，而是把同一批工具用关键词规则驱动一遍，
# 目的：验证播放、进度、复习、打断这些真实链路，不依赖任何外部服务。
RULES = [
    (("停", "别放", "安静", "暂停", "不听了"), "stop_playback", {}, "好，停下了。"),
    (("下一", "换一个", "新课", "新的"), "play_next", {}, None),
    (("接着", "继续", "上次", "没听完"), "play_resume", {}, None),
    (("再听", "重放", "没听清", "没听懂", "再来一遍", "重复"), "repeat_audio", {}, "好，我们再听一遍。"),
    (("慢",), "set_speed", {"rate": 0.75}, "我读慢一点。"),
    (("快",), "set_speed", {"rate": 1.25}, "那我读快一点点。"),
    (("单词", "背单词", "考我", "复习"), "next_quiz_word", {}, "我们来玩个单词游戏。"),
    (("第几课", "进度", "学到哪", "学了"), "progress_report", {}, None),
    (("为什么", "是什么", "怎么", "科普", "恐龙", "太空", "月亮", "动物", "海洋"),
     "explain", None, None),
]


class MockBrain:
    """关键词规则大脑：不联网、不花钱，用来验证整条链路。"""
    name = "mock"

    def __init__(self, ctx):
        self.ctx = ctx
        self.history = []

    def _reply_for(self, fn, result, brief):
        import re
        if fn in ("play_next", "play_resume", "repeat_audio"):
            return "好，我们听这个。"
        if fn == "progress_report":
            title = re.search(r"《([^》]+)》", result)
            due = re.search(r"待复习单词 (\d+) 个", result)
            parts = []
            if title:
                parts.append(f"我们上次学到《{title.group(1)}》啦")
            else:
                parts.append("我们还没开始听呢")
            if due and due.group(1) != "0":
                parts.append(f"还有 {due.group(1)} 个单词要复习")
            return "，".join(parts) + "。"
        if fn == "next_quiz_word":
            return "这个词你还记得吗？"
        if fn == "explain":
            if "未配置联网搜索" in result:
                return "这个我不太确定，我们下次问问老师吧。"
            return "我查到了，我用简单的话说给你听。"
        return result

    def chat(self, user_text, max_rounds=1):
        text = (user_text or "").strip()
        for kws, fn, args, say in RULES:
            if not any(k in text for k in kws):
                continue
            call_args = dict(args or {})
            if fn == "explain":
                call_args = {"question": text}
            try:
                result, brief = tools.dispatch(fn, call_args, self.ctx)
            except Exception as e:
                result, brief = f"工具 {fn} 出错：{e}", False
            print(f"[mock] {fn} -> {result}")
            reply = say or self._reply_for(fn, result, brief)
            db.log(self.ctx.conn, "user", text)
            db.log(self.ctx.conn, "assistant", reply)
            return reply
        reply = "我听懂啦。你可以说，下一课、接着听，或者问我一个为什么。"
        db.log(self.ctx.conn, "user", text)
        db.log(self.ctx.conn, "assistant", reply)
        return reply


def make(ctx):
    """按配置返回大脑：auto = 有 key 用 DeepSeek，没 key 用 mock。"""
    mode = getattr(config, "BRAIN_MODE", "auto")
    if mode == "mock":
        return MockBrain(ctx)
    if mode == "deepseek":
        if not config.DEEPSEEK_API_KEY:
            raise SystemExit("KIDBOX_BRAIN=deepseek 但没有配置 DEEPSEEK_API_KEY")
        return Brain(ctx)
    return Brain(ctx) if config.DEEPSEEK_API_KEY else MockBrain(ctx)
