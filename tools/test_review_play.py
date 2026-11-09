import sys, os, json, queue, types
sys.path.insert(0, "/opt/xiaozhi-esp32-server")

import plugins_func.functions.review_play as rp


class L:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        print("   LOG", *a)

    def error(self, *a, **k):
        print("   ERR", *a)


def call(**kw):
    conn = types.SimpleNamespace()
    conn.sentence_id = "t"
    conn.logger = L()
    conn.tts = types.SimpleNamespace(tts_text_queue=queue.Queue())
    r = rp.review_play(conn, **kw)
    files, label = [], None
    q = conn.tts.tts_text_queue
    while not q.empty():
        m = q.get()
        if "FILE" in str(m.content_type):
            files.append(os.path.basename(m.content_file))
        if m.content_detail and "熟听" in str(m.content_detail):
            label = m.content_detail
    resp = getattr(r, "result", None)
    return label, files, resp


# 干净状态
open(rp.PROGRESS_FILE, "w").write("{}")
if os.path.exists(rp.HISTORY_FILE):
    os.remove(rp.HISTORY_FILE)

print("== 1) 熟听 3-3（Smart Reading）==")
label, files, resp = call(unit="3-3")
print(f"  {label} | {len(files)} 个文件 | {files[:3]}")

print("\n== 2) 熟听 POWER UP 1 ==")
label, files, resp = call(unit="POWER UP 1")
print(f"  {label} | {len(files)} 个文件")
print(f"  {files}")

print("\n== 3) 只说「熟听」（应反问）==")
label, files, resp = call()
print(f"  label={label} files={len(files)} resp={resp}")

print("\n== 4) 只说了个「熟听」字样 ==")
label, files, resp = call(unit="熟听")
print(f"  resp={resp}")

print("\n== 5) 从 3-3 U5 开始 ==")
label, files, resp = call(unit="3-3 U5 开始熟听")
print(f"  {label} | {files[:2]}")

print("\n== 6) POWER UP 再听一次（应滑到第 2 单元）==")
label, files, resp = call(unit="power up 1")
print(f"  {label} | {files}")

print("\n== 进度文件 ==")
print("  ", json.dumps(json.load(open(rp.PROGRESS_FILE)), ensure_ascii=False))

print("\n== 播放历史 ==")
for line in open(rp.HISTORY_FILE, encoding="utf-8"):
    d = json.loads(line)
    print(f"  {d['ts']} {d['library']:14s} {d.get('book') or d.get('level')} "
          f"{d.get('units') or d.get('unit')} → {len(d['files'])} 个 ×{d['times']}")

# 复位，别影响真机第一次
open(rp.PROGRESS_FILE, "w").write("{}")
print("\n状态已复位")
