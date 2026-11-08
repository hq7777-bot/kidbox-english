"""对切好的句子音频跑 SenseVoice ASR，产出 raw.json（每单元一份）。

在容器内运行：
    docker exec xiaozhi-esp32-server python /tmp/asr_segments.py 3-3/U15-Passage
    docker exec xiaozhi-esp32-server python /tmp/asr_segments.py --all
"""
import json
import sys
from pathlib import Path

ROOT = Path("/opt/xiaozhi-esp32-server/data/follow/SmartReading3")
MODEL_DIR = "/opt/xiaozhi-esp32-server/models/SenseVoiceSmall"

_model = None


def get_model():
    global _model
    if _model is None:
        from funasr import AutoModel
        print("加载 SenseVoice…", flush=True)
        _model = AutoModel(model=MODEL_DIR, disable_update=True, disable_pbar=True,
                           disable_log=True, trust_remote_code=False)
    return _model


def asr_batch(paths, language="en"):
    m = get_model()
    out = []
    for p in paths:
        try:
            res = m.generate(input=str(p), cache={}, language=language, use_itn=True)
            text = ""
            if res and isinstance(res, list):
                text = (res[0].get("text") or "").strip()
            out.append(text)
        except Exception as e:
            print(f"    [X] {p.name}: {e}", flush=True)
            out.append("")
    return out


def do_unit(rel):
    d = ROOT / rel
    files = sorted(d.glob("*.mp3"))
    if not files:
        print(f"  [X] 没有句子文件: {d}")
        return
    print(f"{rel}: {len(files)} 句", flush=True)
    texts = asr_batch(files)
    data = [{"file": f.name, "raw": t} for f, t in zip(files, texts)]
    (d / "raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in data[:8]:
        print(f"    {item['file']}  {item['raw']}", flush=True)
    if len(data) > 8:
        print(f"    … 共 {len(data)} 句", flush=True)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    if sys.argv[1] == "--all":
        units = sorted(p.parent.relative_to(ROOT).as_posix()
                       for p in ROOT.rglob("001.mp3"))
        print(f"共 {len(units)} 个单元", flush=True)
        for i, rel in enumerate(units, 1):
            print(f"[{i}/{len(units)}] ", end="", flush=True)
            do_unit(rel)
        return 0
    do_unit(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
