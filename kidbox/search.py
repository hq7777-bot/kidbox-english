"""联网检索（科普问答用）。没有 key 时明确降级，不让模型瞎编。
"""
import json
import urllib.parse

import requests

from . import config


def _bocha(q, n=4):
    r = requests.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {config.BOCHA_KEY}",
                 "Content-Type": "application/json"},
        json={"query": q, "count": n, "summary": True},
        timeout=15)
    r.raise_for_status()
    data = r.json()
    pages = (((data.get("data") or {}).get("webPages") or {}).get("value") or [])
    out = []
    for p in pages[:n]:
        out.append({"title": p.get("name", ""), "url": p.get("url", ""),
                    "snippet": (p.get("summary") or p.get("snippet") or "")[:600]})
    return out


def _searxng(q, n=4):
    r = requests.get(config.SEARXNG_URL.rstrip("/") + "/search",
                     params={"q": q, "format": "json", "language": "zh-CN"},
                     timeout=15)
    r.raise_for_status()
    out = []
    for p in (r.json().get("results") or [])[:n]:
        out.append({"title": p.get("title", ""), "url": p.get("url", ""),
                    "snippet": (p.get("content") or "")[:600]})
    return out


def search(q, n=4):
    """返回 (ok, list_or_message)"""
    if config.SEARCH_PROVIDER == "bocha" and config.BOCHA_KEY:
        try:
            return True, _bocha(q, n)
        except Exception as e:
            return False, f"搜索服务调用失败：{e}"
    if config.SEARCH_PROVIDER == "searxng" and config.SEARXNG_URL:
        try:
            return True, _searxng(q, n)
        except Exception as e:
            return False, f"搜索服务调用失败：{e}"
    return False, "未配置联网搜索（KIDBOX_SEARCH=none），只能用我已有的知识回答，可能不是最新的。"
