"""主程序：状态机 = 等唤醒 → 听一句 → 想 → 说/播 → 回到等唤醒。

运行方式：
    python -m kidbox.main              # 真实语音（需要麦克风 + openwakeword）
    python -m kidbox.main --text       # 键盘输入代替说话（调程序时最省事）
    python -m kidbox.main --no-wake    # 每次回车触发一次对话
    python -m kidbox.main --once "下一课"   # 跑一条指令就退出（冒烟测试用）
    python -m kidbox.main --script demo.txt # 按文件里的指令逐条跑（无人值守测试）
"""
import argparse
import sys
import time
from pathlib import Path

from . import asr, brain, config, db, library, listen, player, tools, tts

SNAP_EVERY = 2.0
EXIT_WORDS = ("退出", "再见", "拜拜", "结束", "不玩了", "晚安")


def boot_greeting(ctx):
    lv = ctx.state["level"]
    prog = db.last_played(ctx.conn)
    if prog:
        msg = "豆豆在呢。上次我们没听完，要不要接着说？"
    else:
        msg = "豆豆在呢，我们一起来听英语吧。"
    return msg


def handle_text(ctx, br, text, speak_out=True):
    """处理一句用户输入：思考 → 执行工具 → 必要时说话。"""
    t0 = time.time()
    try:
        reply = br.chat(text)
    except Exception as e:
        print(f"[brain] 出错：{e}")
        reply = "我这会儿有点转不过来，你再说一次好吗？"
    dt = time.time() - t0
    print(f"[豆豆] {reply}   ({dt:.1f}s)")

    # 工具已经把音频放起来了，就别用 TTS 盖住它；
    # TTS 关闭时不调用，避免和上面那行 print 重复输出
    if speak_out and not ctx.player.is_playing() and config.TTS_ENABLED and tts.available():
        tts.say(reply)
    return reply


def run(args):
    config.LIBRARY.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    lessons = library.sync(conn, config.LIBRARY)
    print(f"[库] {config.LIBRARY}")
    print(f"[库] 共 {len(lessons)} 课；级别 {[f'{a}({n})' for a, n in library.levels(conn)]}")

    pl = player.make(volume=65)
    print(f"[播放] 后端 = {pl.name}" + ("" if pl.name != "null" else "（静默模式：只跑逻辑，不出声）"))
    ctx = tools.Ctx(conn, pl)
    br = brain.make(ctx)
    print(f"[大脑] 后端 = {getattr(br, 'name', 'deepseek')}   ({config.summary()})")
    ear = listen.BargeIn()

    if args.once:
        handle_text(ctx, br, args.once)
        return 0

    # 无人值守：按脚本文件逐条执行，适合在别的电脑上做验收
    if args.script:
        path = Path(args.script)
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]
        print(f"[脚本] {path} 共 {len(lines)} 条指令\n")
        for i, line in enumerate(lines, 1):
            print(f"--- {i}/{len(lines)} ---")
            if any(w in line for w in EXIT_WORDS):
                print("[脚本] 收到结束词，提前结束")
                break
            print(f"[你] {line}")
            handle_text(ctx, br, line)
            # 给播放器一点时间前进，这样"断点续播"能验证出真实秒数
            time.sleep(float(getattr(args, "step", 0.0) or 0.0))
            ctx.snapshot()
        pl.stop()
        ctx.snapshot()
        print("\n[脚本] 结束，进度已保存")
        return 0

    tts.say(boot_greeting(ctx))
    last_snap = 0.0
    session_start = time.time()
    warned_break = False

    while True:
        # 1) 等唤醒
        if args.text:
            try:
                text = input("\n你说 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if any(w in text for w in EXIT_WORDS):
                print("[豆豆] 好，下次再玩。")
                break
        else:
            if args.no_wake:
                try:
                    input("\n按回车唤醒 > ")
                except (EOFError, KeyboardInterrupt):
                    break
            else:
                try:
                    listen.wait_for_wakeword()
                except KeyboardInterrupt:
                    break
                print("[唤醒] 我在听…")

            # 2) 录音识别
            text = asr.listen_once()
            if not text:
                print("[asr] 没听清，再叫一次豆豆。")
                continue
            print(f"[你] {text}")

        # 3) 想 + 做
        handle_text(ctx, br, text)

        # 4) 播放期间：定时存进度 + 检测打断
        while pl.is_playing():
            time.sleep(0.2)
            pl.tick()
            if time.time() - last_snap > SNAP_EVERY:
                ctx.snapshot()
                last_snap = time.time()
            if time.time() - session_start > config.SESSION_MAX_MINUTES * 60 and not warned_break:
                tts.say("我们听了挺久啦，先休息一下眼睛好不好？")
                warned_break = True
            if ear.triggered():
                pl.pause()
                ctx.snapshot()
                print("[打断] 检测到说话，暂停播放")
                heard = "" if args.text else asr.listen_once()
                if args.text:
                    heard = input("（打断）你说 > ").strip()
                print(f"[打断] 听到：{heard}")
                if heard and any(k in heard for k in ("停", "别放", "关机", "安静")):
                    pl.stop()
                    tts.say("好的，停下了。")
                elif heard and any(k in heard for k in ("下一", "换一个")):
                    handle_text(ctx, br, "下一课")
                elif heard and any(k in heard for k in ("再听", "重放", "没听清")):
                    handle_text(ctx, br, "再听一遍")
                else:
                    pl.resume()
                ear.reset()

        # 5) 播完了：标记完成
        if ctx.state.get("lesson_id") and not pl.is_playing():
            cur = db.get_progress(conn, ctx.state["lesson_id"])
            if cur and not cur["finished"]:
                last = library.get_lesson(conn, ctx.state["lesson_id"])
                if last and cur["file_index"] >= len(last["files"]) - 1:
                    db.save_progress(conn, ctx.state["lesson_id"], cur["file_index"],
                                     cur["position"], finished=True)

    pl.stop()
    ctx.snapshot()
    print("\n[退出] 进度已保存。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="键盘输入代替说话")
    ap.add_argument("--no-wake", action="store_true", help="回车触发，不用唤醒词")
    ap.add_argument("--once", default="", help="只处理一条指令就退出")
    ap.add_argument("--script", default="", help="按文件逐条执行指令（无人值守验收）")
    ap.add_argument("--no-tts", action="store_true", help="只打印，不做语音合成")
    ap.add_argument("--step", type=float, default=0.4, help="脚本模式下每条指令之间等待的秒数")
    args = ap.parse_args()
    if args.no_tts:
        config.TTS_ENABLED = False
    sys.exit(run(args))


if __name__ == "__main__":
    main()
