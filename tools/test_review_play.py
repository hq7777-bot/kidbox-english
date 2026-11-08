import sys, os, json, queue, types
sys.path.insert(0, "/opt/xiaozhi-esp32-server")

import plugins_func.functions.review_play as rp

# 从干净状态开始
open(rp.STATE_FILE, "w").write("{}")


class L:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        print("  ERR", *a)


def call(**kw):
    conn = types.SimpleNamespace()
    conn.sentence_id = "t"
    conn.logger = L()
    conn.tts = types.SimpleNamespace(tts_text_queue=queue.Queue())
    r = rp.review_play(conn, **kw)
    q, files, label = conn.tts.tts_text_queue, [], None
    while not q.empty():
        m = q.get()
        if "FILE" in str(m.content_type):
            files.append(os.path.basename(m.content_file))
        if m.content_detail and "熟听" in str(m.content_detail):
            label = m.content_detail
    action = getattr(r, "action", None)
    resp = getattr(r, "result", None) or getattr(r, "response", None)
    return label, files, action, resp


print("== 连续 14 次「熟听 3-3」（看窗口滑动 + 到顶）==")
for i in range(1, 15):
    label, files, action, resp = call(unit="3-3")
    st = json.load(open(rp.STATE_FILE)).get("3-3", {})
    uniq = sorted({f[:3] for f in files})
    print(f"  第{i:2d}次: {label or '—'}  | {len(files)}个文件 | 窗口{uniq} | next={st.get('next')}"
          + (f" | 回复:{resp}" if resp else ""))

print("\n== 第 15 次（应提示放完了）==")
label, files, action, resp = call(unit="3-3")
print(f"  label={label} files={len(files)} resp={resp}")

print("\n== 「从 3-3 U5 开始熟听」==")
label, files, action, resp = call(unit="3-3", start=5)
print(f"  {label} | 窗口={sorted({f[:3] for f in files})} | next={json.load(open(rp.STATE_FILE))['3-3']['next']}")

print("\n== 「熟听 3-3 重头」==")
label, files, action, resp = call(unit="3-3", reset=True)
print(f"  {label} | 窗口={sorted({f[:3] for f in files})} | next={json.load(open(rp.STATE_FILE))['3-3']['next']}")

print("\n== 书号/起点解析 ==")
for t in ["3-3", "3.3", "三杠三", "book3-3"]:
    print(f"  {t!r:12} -> {rp._parse_book(t)}")
for t in ["从3-3 U5开始熟听", "从第7单元开始", "熟听3-3"]:
    print(f"  {t!r:20} -> start={rp._parse_start(t)}")

# 复位，免得真机第一次就说"放完了"
json.dump({}, open(rp.STATE_FILE, "w"))
print("\n状态已复位")
