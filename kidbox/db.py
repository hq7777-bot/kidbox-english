"""SQLite 存储：课程索引、播放进度、单词复习、对话日志、键值状态。

为什么用 SQLite 而不是 JSON 文件：
- 断电不会写坏（WAL 模式），24h 待机设备必须考虑这个
- 复习算法要按 due 日期查询，SQL 比手写过滤省事
"""
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id        TEXT PRIMARY KEY,      -- 例：L1/001_hello
    level     TEXT NOT NULL,
    ord       INTEGER NOT NULL,
    title     TEXT,
    dir       TEXT NOT NULL,
    files     TEXT NOT NULL,         -- JSON 数组，按顺序
    text      TEXT,                  -- 课文原文，供"重播这一句"和跟读用
    words     TEXT,                   -- JSON 数组
    duration  REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS progress (
    lesson_id  TEXT PRIMARY KEY,
    file_index INTEGER DEFAULT 0,
    position   REAL DEFAULT 0,       -- 秒
    finished   INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS word_review (
    word     TEXT PRIMARY KEY,
    level    TEXT,
    box      INTEGER DEFAULT 1,       -- Leitner 盒子 1..5
    due      TEXT,
    right    INTEGER DEFAULT 0,
    wrong    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dialog (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   TEXT,
    role TEXT,
    content TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(path=None):
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


# ---------------- kv ----------------
def kv_get(conn, k, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else default


def kv_set(conn, k, v):
    conn.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, str(v)))
    conn.commit()


# ---------------- 进度 ----------------
def save_progress(conn, lesson_id, file_index, position, finished=False):
    conn.execute(
        """INSERT INTO progress(lesson_id,file_index,position,finished,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(lesson_id) DO UPDATE SET
             file_index=excluded.file_index, position=excluded.position,
             finished=excluded.finished, updated_at=excluded.updated_at""",
        (lesson_id, int(file_index), float(position), 1 if finished else 0, now()))
    conn.commit()


def get_progress(conn, lesson_id):
    row = conn.execute("SELECT * FROM progress WHERE lesson_id=?", (lesson_id,)).fetchone()
    return dict(row) if row else None


def last_played(conn):
    """最近一条未听完的进度，用于"继续上次"。"""
    row = conn.execute(
        "SELECT * FROM progress WHERE finished=0 ORDER BY updated_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ---------------- 单词（Leitner）----------------
INTERVALS = {1: 1, 2: 2, 3: 4, 4: 7, 5: 15}   # 盒子 -> 几天后再复习


def add_word(conn, word, level=None):
    conn.execute("""INSERT INTO word_review(word,level,box,due)
                    VALUES(?,?,1,?)
                    ON CONFLICT(word) DO NOTHING""",
                 (word, level, now()))
    conn.commit()


def due_words(conn, limit=10):
    rows = conn.execute(
        "SELECT * FROM word_review WHERE due<=? ORDER BY box ASC, due ASC LIMIT ?",
        (now(), limit)).fetchall()
    return [dict(r) for r in rows]


def grade_word(conn, word, correct):
    row = conn.execute("SELECT * FROM word_review WHERE word=?", (word,)).fetchone()
    if not row:
        add_word(conn, word)
        row = conn.execute("SELECT * FROM word_review WHERE word=?", (word,)).fetchone()
    box = row["box"] or 1
    box = min(5, box + 1) if correct else 1
    due = (datetime.now().astimezone() + timedelta(days=INTERVALS[box])).isoformat(timespec="seconds")
    conn.execute("""UPDATE word_review SET box=?, due=?,
                    right=right+?, wrong=wrong+? WHERE word=?""",
                 (box, due, 1 if correct else 0, 0 if correct else 1, word))
    conn.commit()
    return box


# ---------------- 对话日志 ----------------
def log(conn, role, content):
    conn.execute("INSERT INTO dialog(ts,role,content) VALUES(?,?,?)", (now(), role, content))
    conn.commit()


def recent_dialog(conn, limit=6):
    rows = conn.execute("SELECT role,content FROM dialog ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
