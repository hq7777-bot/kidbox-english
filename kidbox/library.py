"""音频库扫描。

目录约定（自己往里丢音频即可，扫描是幂等的）：

library/
  L1/
    001_hello/
      meta.json
      01.mp3  02.mp3 ...
    002_colors/
  L2/
    001_...

meta.json:
{
  "title": "Hello",
  "text": "Hello! Hi! I am Anna.",
  "words": ["hello", "hi", "am"]
}
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


@dataclass
class Lesson:
    id: str
    level: str
    ord: int
    title: str
    dir: str
    files: list = field(default_factory=list)
    text: str = ""
    words: list = field(default_factory=list)

    def as_row(self):
        return (self.id, self.level, self.ord, self.title, self.dir,
                json.dumps(self.files, ensure_ascii=False), self.text,
                json.dumps(self.words, ensure_ascii=False), 0.0)


def _natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def scan(root):
    root = Path(root)
    out = []
    if not root.exists():
        return out
    for level_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: _natural(p.name)):
        for lesson_dir in sorted([p for p in level_dir.iterdir() if p.is_dir()], key=lambda p: _natural(p.name)):
            files = sorted([p for p in lesson_dir.iterdir()
                            if p.suffix.lower() in AUDIO_EXT and p.is_file()],
                           key=lambda p: _natural(p.name))
            if not files:
                continue
            meta = {}
            mp = lesson_dir / "meta.json"
            if mp.exists():
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            ordv = None
            m = re.match(r"^(\d+)", lesson_dir.name)
            if m:
                ordv = int(m.group(1))
            out.append(Lesson(
                id=f"{level_dir.name}/{lesson_dir.name}",
                level=level_dir.name,
                ord=ordv if ordv is not None else 9999,
                title=meta.get("title") or lesson_dir.name,
                dir=str(lesson_dir),
                files=[str(f) for f in files],
                text=meta.get("text", ""),
                words=meta.get("words", []) or [],
            ))
    return out


def sync(conn, root):
    """把库目录同步进 DB；已存在的保留原有进度。"""
    lessons = scan(root)
    for l in lessons:
        conn.execute("""INSERT INTO lessons(id,level,ord,title,dir,files,text,words,duration)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                          level=excluded.level, ord=excluded.ord, title=excluded.title,
                          dir=excluded.dir, files=excluded.files,
                          text=excluded.text, words=excluded.words""", l.as_row())
        for w in l.words:
            conn.execute("""INSERT INTO word_review(word,level,box,due) VALUES(?,?,1,?)
                            ON CONFLICT(word) DO NOTHING""",
                         (w, l.level, datetime.now().astimezone().isoformat(timespec="seconds")))
    if lessons:
        conn.execute("DELETE FROM lessons WHERE id NOT IN (%s)" % ",".join("?" * len(lessons)),
                     [l.id for l in lessons])
    conn.commit()
    return lessons


def get_lesson(conn, lesson_id):
    row = conn.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["files"] = json.loads(d["files"])
    d["words"] = json.loads(d["words"])
    return d


def levels(conn):
    rows = conn.execute("SELECT level, COUNT(*) n FROM lessons GROUP BY level ORDER BY level").fetchall()
    return [(r["level"], r["n"]) for r in rows]


def next_lesson(conn, level, after_id=None):
    """after_id 为空 → 返回该级别第一课；有 after_id → 只找它之后的，找不到返回 None。"""
    if after_id:
        row = conn.execute("SELECT ord FROM lessons WHERE id=?", (after_id,)).fetchone()
        if row:
            nxt = conn.execute(
                "SELECT * FROM lessons WHERE level=? AND ord>? ORDER BY ord LIMIT 1",
                (level, row["ord"])).fetchone()
            return dict(nxt) if nxt else None
    row = conn.execute("SELECT * FROM lessons WHERE level=? ORDER BY ord LIMIT 1", (level,)).fetchone()
    return dict(row) if row else None


def first_of_level(conn, level):
    row = conn.execute("SELECT * FROM lessons WHERE level=? ORDER BY ord LIMIT 1", (level,)).fetchone()
    return dict(row) if row else None


def find(conn, keyword):
    kw = f"%{keyword}%"
    rows = conn.execute("SELECT * FROM lessons WHERE title LIKE ? OR text LIKE ? OR id LIKE ? LIMIT 8",
                        (kw, kw, kw)).fetchall()
    return [dict(r) for r in rows]


def all_levels_sorted(conn):
    rows = conn.execute("SELECT * FROM lessons ORDER BY level, ord").fetchall()
    return [dict(r) for r in rows]
