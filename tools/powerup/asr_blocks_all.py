"""容器内：把 data/blocks 下所有块跑一遍 SenseVoice，输出 seq/block<TAB>text。"""
import os
import sys
import glob
from funasr import AutoModel

root = sys.argv[1] if len(sys.argv) > 1 else "/opt/xiaozhi-esp32-server/data/blocks"
model = AutoModel(
    model="/opt/xiaozhi-esp32-server/models/SenseVoiceSmall",
    device="cpu",
    disable_update=True,
)

files = sorted(glob.glob(os.path.join(root, "*", "b*.wav")))
print(f"共 {len(files)} 个块", flush=True)
for f in files:
    rel = os.path.relpath(f, root).replace("\\", "/")
    try:
        res = model.generate(input=f, cache={}, language="en", use_itn=True, batch_size_s=30)
        txt = res[0]["text"] if res else ""
    except Exception as e:
        txt = f"<ERR {e}>"
    print(f"{rel}\t{txt}", flush=True)
