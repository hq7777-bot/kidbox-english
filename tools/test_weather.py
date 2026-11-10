import asyncio
import sys
import types

sys.path.insert(0, "/opt/xiaozhi-esp32-server")

import yaml
from plugins_func.functions.get_weather import get_weather


class L:
    def bind(self, **k):
        return self
    def info(self, *a, **k): print("  LOG", *a)
    def warning(self, *a, **k): print("  WARN", *a)
    def error(self, *a, **k): print("  ERR", *a)


class Cache:
    def __init__(self): self.d = {}
    def get(self, t, k): return self.d.get((t, k))
    def set(self, t, k, v): self.d[(t, k)] = v


async def main():
    cfg = yaml.safe_load(open("/opt/xiaozhi-esp32-server/data/.config.yaml", encoding="utf-8"))
    # 用真实 cache_manager，保证和线上一致
    conn = types.SimpleNamespace()
    conn.config = cfg
    conn.logger = L()
    conn.client_ip = None

    for loc in [None, "杭州", "上海", "北京海淀"]:
        print(f"\n===== location={loc!r} =====")
        r = await get_weather(conn, location=loc, lang="zh_CN")
        print("  action:", getattr(r.action, "name", r.action))
        print("  " + (r.result or "").replace("\n", "\n  "))


asyncio.run(main())
