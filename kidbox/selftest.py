"""不依赖任何 API、麦克风、音频设备的逻辑自测。

跑：python -m kidbox.selftest
它会建一个临时音频库，验证：扫描排序、进度存取、继续上次、下一课、
Leitner 复习、工具分发、搜索降级。
"""
import json
import tempfile
import traceback
from pathlib import Path

from . import config, db, library, player, tools


def build_library(root: Path):
    plan = {
        "L1": [("001_hello", "Hello", ["hello", "hi"]),
               ("002_colors", "Colors", ["red", "blue"])],
        "L2": [("001_animals", "Animals", ["cat", "dog"])],
    }
    for level, items in plan.items():
        for ordn, (d, title, words) in enumerate(items, start=1):
            p = root / level / d
            p.mkdir(parents=True, exist_ok=True)
            for i in (1, 2):
                (p / f"{i:02d}.mp3").write_bytes(b"\x00" * 64)
            (p / "meta.json").write_text(json.dumps(
                {"title": title, "text": f"{title} text", "words": words},
                ensure_ascii=False), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="kidbox_selftest_"))
    config.LIBRARY = tmp / "library"
    config.DB_PATH = tmp / "test.db"
    build_library(config.LIBRARY)

    fails = []

    def check(name, cond, extra=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    conn = db.connect()
    lessons = library.sync(conn, config.LIBRARY)
    check("扫描到 3 课", len(lessons) == 3, f"实际 {len(lessons)}")
    check("级别按 L1→L2 排序", [l.level for l in lessons] == ["L1", "L1", "L2"])
    check("课内音频按文件名排序",
          lessons[0].files[0].endswith("01.mp3") and lessons[0].files[1].endswith("02.mp3"))
    check("meta 里的单词进了词库", len(db.due_words(conn, 99)) == 6)

    pl = player.NullPlayer()
    ctx = tools.Ctx(conn, pl)
    ctx.state["level"] = "L1"

    r, brief = tools.dispatch("play_next", {}, ctx)
    check("play_next 播第一课", "Hello" in r and pl.is_playing(), r)
    check("当前课写入状态", ctx.state["lesson_id"] == "L1/001_hello")

    # 模拟听了 3 秒第 2 个音频
    db.save_progress(conn, "L1/001_hello", 1, 3.0)
    prog = db.last_played(conn)
    check("进度落库", prog and prog["file_index"] == 1 and abs(prog["position"] - 3.0) < 0.01)

    pl.stop()
    r, _ = tools.dispatch("play_resume", {}, ctx)
    check("play_resume 从上次位置继续",
          "3 秒" in r and pl.current()[0] == 1, r)

    r, _ = tools.dispatch("play_next", {}, ctx)
    check("play_next 到第 2 课", ctx.state["lesson_id"] == "L1/002_colors", r)

    r, _ = tools.dispatch("play_next", {}, ctx)
    check("L1 最后一课之后回到第一课", "重新开始" in r, r)

    r, _ = tools.dispatch("set_level", {"level": "L2"}, ctx)
    check("切换级别", ctx.state["level"] == "L2", r)
    r, _ = tools.dispatch("set_level", {"level": "L9"}, ctx)
    check("非法级别被拒绝", "没有这个级别" in r, r)

    r, _ = tools.dispatch("play_lesson", {"keyword": "animals"}, ctx)
    check("按关键词找课", "Animals" in r, r)
    r, _ = tools.dispatch("play_lesson", {"order": 99}, ctx)
    check("找不到时给出可选项", "没找到" in r, r)

    r, _ = tools.dispatch("next_quiz_word", {}, ctx)
    check("取复习单词", "复习单词" in r, r)
    word = "hello"
    before = db.grade_word(conn, word, True)
    after = db.grade_word(conn, word, False)
    check("答对进盒、答错回落第1盒", before == 2 and after == 1, f"{before}->{after}")

    r, _ = tools.dispatch("set_speed", {"rate": 0.75}, ctx)
    check("调速", "0.75" in r, r)
    r, _ = tools.dispatch("set_volume", {"percent": 200}, ctx)
    check("音量被限幅", f"{config.MAX_VOLUME}%" in r, r)
    r, _ = tools.dispatch("repeat_audio", {}, ctx)
    check("重播", "再放一遍" in r, r)
    r, _ = tools.dispatch("stop_playback", {}, ctx)
    check("停止", not pl.is_playing(), r)

    r, _ = tools.dispatch("explain", {"question": "恐龙为什么灭绝"}, ctx)
    check("没配搜索时明确降级", "未配置联网搜索" in r, r[:40])

    r, _ = tools.dispatch("progress_report", {}, ctx)
    check("进度报告", "L1" in r and "待复习单词" in r, r)

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：{fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
