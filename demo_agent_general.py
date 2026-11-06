"""验证"通用 agent"路线：只给 run_shell / read_file / write_file 三个通用工具，
不给任何 play_next / play_resume 之类的场景函数，看它能不能自己把
"复习上两个英语音频，然后再播放下一篇" 理解成具体命令。

音频是假的：bin/ffplay 被替换成一个只打印 PLAYING 的脚本，不会真出声。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent / "demo_agent"
LIB = ROOT / "library"
BIN = ROOT / "bin"


def load_key():
    env = Path(os.path.expanduser("~")) / "AppData/Local/hermes/.env"
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("找不到 DEEPSEEK_API_KEY")


def build():
    if ROOT.exists():
        import shutil
        shutil.rmtree(ROOT)
    plan = [
        ("L1", "001_hello", "Hello", 2),
        ("L1", "002_colors", "Colors", 2),
        ("L1", "003_animals", "Animals", 3),
        ("L1", "004_family", "Family", 2),
    ]
    for lv, d, title, n in plan:
        p = LIB / lv / d
        p.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (p / f"{i:02d}.mp3").write_bytes(b"\x00" * 32)
        (p / "meta.json").write_text(json.dumps(
            {"title": title, "text": f"{title} lesson", "words": ["hello"]},
            ensure_ascii=False), encoding="utf-8")

    (ROOT / "progress.json").write_text(json.dumps({
        "current_lesson": "L1/003_animals",
        "recent_audio": [
            {"file": "L1/003_animals/02.mp3", "lesson": "L1/003_animals", "pos": 4.2,
             "at": "2026-11-02T19:41:00+08:00"},
            {"file": "L1/003_animals/03.mp3", "lesson": "L1/003_animals", "pos": 9.8,
             "at": "2026-11-02T19:43:00+08:00"},
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    BIN.mkdir(parents=True, exist_ok=True)
    ff = BIN / "ffplay"
    ff.write_text(
        '#!/usr/bin/env bash\n'
        'echo "[喇叭] 正在播放 $*"\n'
        'echo "$(date +%H:%M:%S) $*" >> "$(dirname "$0")/../played.log"\n',
        encoding="utf-8", newline="\n")
    ff.chmod(0o755)
    (ROOT / "played.log").write_text("", encoding="utf-8")


SYSTEM = """你在一个儿童英语学习盒子上运行，用户是 6 岁小孩。你没有屏幕，只有麦克风和喇叭。

设备上的约定：
- 音频库在 ./library，结构是 library/<级别>/<课号_课名>/<序号>.mp3
- meta.json 里有 title / text / words
- 播放必须用 ffplay 命令，例如：ffplay -nodisp -autoexit -ss 4.2 library/L1/003_animals/02.mp3
- 播放进度记在 ./progress.json，字段 current_lesson、recent_audio（最近播放的音频，按时间从旧到新）
  recent_audio 里每一项是 {file, lesson, pos, at}，pos 是上次听到的秒数
- "上一篇/下一篇"指 lesson 顺序里的下一课；"上两个音频"指 recent_audio 里最后两条

规则：
1. 用 run_shell 执行命令来真正操作硬件（播放、停止用 pkill ffplay）。
2. 改进度要真的写回 progress.json，不要只在回答里说。
3. 一次最多连续调用 6 个工具，然后必须用一两句中文告诉孩子你做了什么。
4. 回复是给孩子听的：口语、短句、不要符号和表情。"""

TOOLS = [
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "在设备上执行一条 shell 命令（播放音频、列出目录、查看文件等）",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读一个文本文件的内容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "把内容完整写入一个文本文件（覆盖）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}}, "required": ["path", "content"]}}},
]


def run_shell(cmd, timeout=6):
    env = dict(os.environ)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return out.strip()[:800] or f"(无输出，退出码 {p.returncode})"
    except subprocess.TimeoutExpired:
        return "(命令超时)"
    except Exception as e:
        return f"(执行失败：{e})"


def tool_call(name, args):
    if name == "run_shell":
        return run_shell(args.get("command", ""))
    if name == "read_file":
        p = ROOT / args.get("path", "")
        return p.read_text(encoding="utf-8", errors="ignore")[:2000] if p.exists() else "(文件不存在)"
    if name == "write_file":
        p = ROOT / args.get("path", "")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
        return "已写入"
    return "未知工具"


def ask(key, text, verbose=True):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": text}]
    if verbose:
        print(f"\n{'='*70}\n孩子说：{text}\n{'-'*70}")
    for rnd in range(8):
        r = requests.post("https://api.deepseek.com/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": "deepseek-chat", "messages": msgs,
                                "tools": TOOLS, "tool_choice": "auto",
                                "temperature": 0.2, "max_tokens": 500},
                          timeout=90)
        if r.status_code != 200:
            print("API 错误", r.status_code, r.text[:300])
            return
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            if verbose:
                print(f"\n[豆豆] {msg.get('content','').strip()}")
            return msg.get("content", "")
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": calls})
        for c in calls:
            fn = c["function"]["name"]
            args = json.loads(c["function"].get("arguments") or "{}")
            short = args.get("command") or args.get("path") or ""
            if verbose:
                print(f"  → 调 {fn}: {short}")
            res = tool_call(fn, args)
            if verbose and res:
                for line in res.splitlines()[:6]:
                    print(f"     {line}")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})


if __name__ == "__main__":
    build()
    key = load_key()
    qs = [sys.argv[1]] if len(sys.argv) > 1 else [
        "复习上两个英语音频，然后再播放下一篇",
        "刚才那个太快了，慢点重放一遍",
    ]
    for t in qs:
        ask(key, t)
        time.sleep(0.5)
    print(f"\n{'='*70}\n实际执行的播放记录（played.log）：")
    log = ROOT / "played.log"
    print(log.read_text(encoding="utf-8").strip() or "(空)")
