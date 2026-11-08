"""把 ASR 原始结果清洗 + 用 LLM 校对，生成 lesson.json。

在宿主机跑（需要 DeepSeek key）：
    python build_lesson_json.py 3-3/U15-Passage
    python build_lesson_json.py --all
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\xiaozhi-server\data\follow\SmartReading3")
API = "https://api.deepseek.com/chat/completions"
KEY = os.environ.get("DEEPSEEK_API_KEY", "")

TAG_RE = re.compile(r"<\|[^|>]*\|>")


def clean_raw(t):
    t = TAG_RE.sub("", t or "").strip()
    return re.sub(r"\s+", " ", t)


def llm_fix(sentences, context):
    """让 LLM 只修明显的识别错误，不改句子数量与顺序。"""
    prompt = f"""下面是一套少儿英语分级读物（{context}）的逐句语音识别结果，按音频顺序排列。
请把明显的识别错误改正（同音词、拼写、标点、大小写），保持句子数量和顺序完全不变，不要合并或拆分句子。
要求：
1. 输出 JSON 数组，元素是修正后的句子字符串，长度必须和输入完全一致；
2. 只输出 JSON，不要解释；
3. 如果某句听起来是残缺片段（比如只有 "the cat"），保留原样不要补全。

输入（{len(sentences)} 句）：
{json.dumps(sentences, ensure_ascii=False)}"""
    body = json.dumps({
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4000,
        "stream": False,
        # flash 是推理模型，不关掉思考会吃掉输出预算导致 content 为空
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEY}",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    fixed = json.loads(text)
    if not isinstance(fixed, list) or len(fixed) != len(sentences):
        raise ValueError(f"LLM 返回长度不匹配: {len(fixed)} vs {len(sentences)}")
    return [str(s).strip() for s in fixed]


def min_score_for(text):
    words = len(re.findall(r"[A-Za-z']+", text))
    if words <= 3:
        return 0.70
    if words <= 6:
        return 0.60
    return 0.50


def build(rel):
    d = ROOT / rel
    raw_path = d / "raw.json"
    if not raw_path.exists():
        print(f"  [X] 缺 raw.json: {d}")
        return
    # 已经有 LLM 校对结果就跳过（可反复重跑，只补失败/缺失的）
    lj = d / "lesson.json"
    if lj.exists():
        try:
            if json.loads(lj.read_text(encoding="utf-8")).get("source") == "llm":
                print(f"  {rel}: 已有 LLM 校对，跳过")
                return
        except Exception:
            pass
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    cleaned = [clean_raw(item.get("raw", "")) for item in raw]
    context = f"Smart Reading Book {rel.split('/')[0].replace('-', ' ')}，{rel.split('/')[1]}"
    try:
        fixed = llm_fix(cleaned, context)
        src = "llm"
    except Exception as e:
        print(f"  [!] LLM 校对失败（{e}），落回原始 ASR 文本")
        fixed = cleaned
        src = "raw"

    sentences = []
    for item, text in zip(raw, fixed):
        sentences.append({
            "file": item["file"],
            "text": text,
            "asr_raw": clean_raw(item.get("raw", "")),
            "min_score": min_score_for(text),
        })
    payload = {"title": f"{rel}", "source": src, "sentences": sentences}
    (d / "lesson.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {rel}: {len(sentences)} 句 ({src})")
    for s in sentences[:5]:
        mark = "" if s["text"] == s["asr_raw"] else "  ← 已修正"
        print(f"    {s['file']} [{s['min_score']}] {s['text']}{mark}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("unit", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if not KEY:
        print("需要环境变量 DEEPSEEK_API_KEY")
        return 1
    if a.all:
        units = sorted(p.parent.relative_to(ROOT).as_posix() for p in ROOT.rglob("raw.json"))
        print(f"共 {len(units)} 个单元")
        for i, rel in enumerate(units, 1):
            print(f"[{i}/{len(units)}] ", end="")
            build(rel)
        return 0
    if a.unit:
        build(a.unit)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
