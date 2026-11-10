"""get_weather —— 中国天气网（weather.com.cn）版本，免 API key。

为什么换掉官方实现：
    官方插件用的是和风天气（QWeather）+ 内置示例 key，该 key 已失效（HTTP 401），
    导致两条路径同时坏掉：
      ① prompt_manager 连上来时预取本地天气 → 失败 → 上下文里没有天气
      ② 模型主动调用 get_weather → 同样 401
    而工具说明又写着"本地天气已在上下文中，不要调用本工具"，于是模型既没数据也不能查，
    只能回答"查不到"。

本实现：
    · 数据源：d1.weather.com.cn/sk_2d（实况）+ www.weather.com.cn/weather（7 天）
    · 默认地点：北京海淀（101010200），可在 config 里改
    · IP 定位兜底：IP 库常把"海淀"判成"杭州"，命中 ip_city_overrides 时改用默认地点
    · 城市名 → 城市 ID：内置常用表，找不到走 toy1.weather.com.cn 搜索接口
    · 10 分钟缓存，避免每次唤醒都打接口
"""

import re
import time

import httpx
from bs4 import BeautifulSoup

from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "http://www.weather.com.cn/",
}

# 常用城市 → 中国天气网 city id（够用即可，其它走搜索接口）
CITY_ID = {
    "北京": "101010100", "北京海淀": "101010200", "海淀": "101010200",
    "朝阳": "101010300", "西城": "101010400", "东城": "101010500",
    "上海": "101020100", "天津": "101030100", "重庆": "101040100",
    "杭州": "101210101", "富阳": "101210107", "宁波": "101210401",
    "南京": "101190101", "苏州": "101190401", "广州": "101280101",
    "深圳": "101280601", "成都": "101270101", "武汉": "101200101",
    "西安": "101110101", "青岛": "101120201", "厦门": "101230201",
    "长沙": "101250101", "郑州": "101180101", "沈阳": "101070101",
}

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "查询天气。默认地点是北京海淀：用户没提城市时**不要传 location**，直接调用即可。"
            "用户明确说了别的城市（如上海、杭州）才传该城市名。"
            "返回实况 + 今明后三天预报。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "城市名，如 上海；默认北京海淀，用户没说就别传"},
                "lang": {"type": "string", "description": "语言，默认 zh_CN"},
            },
            "required": [],
        },
    },
}


def _decode(resp: httpx.Response) -> str:
    """中国天气网的 d1 接口是 UTF-8 但常被当 latin-1 读，这里兜一下"""
    text = resp.text
    try:
        text = resp.content.decode("utf-8")
    except Exception:
        pass
    return text


async def _resolve_city_id(location: str) -> tuple[str, str]:
    """城市名 → (city_id, 显示名)"""
    name = (location or "").strip()
    if name in CITY_ID:
        return CITY_ID[name], name
    # 去掉"市/区/县"再试
    short = re.sub(r"(市|区|县)$", "", name)
    if short in CITY_ID:
        return CITY_ID[short], short
    # 搜索接口兜底
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("http://toy1.weather.com.cn/search",
                                 params={"cityname": short or name}, headers=HEADERS)
            m = re.search(r"(\d{9})~([^~]*)~([^~]*)~", _decode(r))
            if m:
                return m.group(1), m.group(3) or short
    except Exception as e:
        logger.bind(tag=TAG).warning(f"城市搜索失败 {name}: {e}")
    return "", name


async def _fetch_now(city_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        r = await client.get(f"http://d1.weather.com.cn/sk_2d/{city_id}.html")
        text = _decode(r)
    m = re.search(r"var dataSK\s*=\s*(\{.*?\})\s*;?\s*$", text, re.S)
    if not m:
        return {}
    import json
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


async def _fetch_today(city_id: str) -> dict:
    """今日预报（高低温/更详细天气/风向），用于补 HTML 页面里"今天"缺失的温度"""
    try:
        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            r = await client.get(f"http://d1.weather.com.cn/dingzhi/{city_id}.html")
            text = _decode(r)
        m = re.search(r"var cityDZ\w*\s*=\s*\{\s*\"weatherinfo\"\s*:\s*(\{.*?\})\s*\}", text, re.S)
        if m:
            import json
            return json.loads(m.group(1))
    except Exception as e:
        logger.bind(tag=TAG).warning(f"取今日预报失败 {city_id}: {e}")
    return {}


def _wind_text(ws: str) -> str:
    """'<3级' → 微风；'3-4级' → 3-4级；'0级' → 0级"""
    s = (ws or "").strip().replace("级风", "").replace("级", "")
    if not s:
        return ""
    if s.startswith("<"):
        return "微风"
    return f"{s.lstrip('>')}级"


def _join_wind(direction: str, ws: str) -> str:
    """风向 + 风力拼成一句口语：北风0级 / 西南风转北风，微风"""
    d = (direction or "").strip()
    w = _wind_text(ws)
    if d and w:
        return f"{d}，{w}" if w == "微风" else f"{d}{w}"
    return d or w


async def _fetch_days(city_id: str, n: int = 3) -> list[dict]:
    """中国天气网 7 天页面，取前 n 天"""
    days = []
    try:
        async with httpx.AsyncClient(timeout=12, headers=HEADERS) as client:
            r = await client.get(f"https://www.weather.com.cn/weather/{city_id}.shtml",
                                 follow_redirects=True)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            ul = soup.select_one("ul.t.clearfix")
            for li in (ul.select("li") if ul else [])[:n]:
                date = li.select_one("h1")
                wea = li.select_one(".wea")
                tem = li.select_one(".tem")
                win = li.select_one(".win")
                hi = tem.select_one("span") if tem else None
                lo = tem.select_one("i") if tem else None
                days.append({
                    "date": date.get_text(strip=True) if date else "",
                    "weather": wea.get_text(strip=True) if wea else "",
                    "high": hi.get_text(strip=True) if hi else "",
                    "low": lo.get_text(strip=True) if lo else "",
                    "wind": win.get_text(strip=True) if win else "",
                })
    except Exception as e:
        logger.bind(tag=TAG).warning(f"取预报失败 {city_id}: {e}")
    return days


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    from core.utils.cache.manager import cache_manager, CacheType

    cfg = (conn.config.get("plugins", {}) or {}).get("get_weather", {}) or {}
    default_location = cfg.get("default_location", "北京海淀")
    ip_overrides = cfg.get("ip_city_overrides",
                           ["杭州", "杭州市", "富阳", "杭州富阳", "浙江", "未知位置", ""])

    # IP 库常把海淀判成杭州 → 命中就直接用默认地点
    loc = (location or "").strip()
    if not loc or loc in ip_overrides:
        loc = default_location

    cache_key = f"cnweather_{loc}"
    cached = cache_manager.get(CacheType.WEATHER, cache_key)
    if cached:
        return ActionResponse(Action.REQLLM, cached, None)

    city_id, city_name = await _resolve_city_id(loc)
    if not city_id:
        return ActionResponse(Action.REQLLM, f"暂时查不到 {loc} 的天气。", None)

    now = await _fetch_now(city_id)
    days = await _fetch_days(city_id, 3)
    today = await _fetch_today(city_id)

    if not now and not days:
        return ActionResponse(Action.REQLLM, "天气服务暂时没通，稍后再试。", None)

    # 今日补全：HTML 页面的"今天"常缺高温，用定值预报的 temp/tempn/weather 补
    if days and today:
        d0 = days[0]
        if not d0.get("high") and today.get("temp"):
            d0["high"] = today["temp"]
        if not d0.get("low") and today.get("tempn"):
            d0["low"] = today["tempn"]
        if today.get("weather"):
            d0["weather"] = today["weather"]
        if today.get("wd") or today.get("ws"):
            d0["wind"] = _join_wind(today.get("wd", ""), today.get("ws", ""))
            d0["wind_final"] = True

    parts = []
    if now:
        bits = []
        if now.get("temp"):
            bits.append(f"{now['temp']}度")
        if now.get("weather"):
            bits.append(now["weather"])
        line = f"{city_name}现在" + ("，".join(bits) if bits else "暂无实况")
        extra = []
        wind_now = _join_wind(now.get("WD", ""), now.get("WS", ""))
        if wind_now:
            extra.append(wind_now)
        if now.get("SD"):
            extra.append(f"湿度{now['SD']}")
        if now.get("aqi"):
            extra.append(f"空气{now['aqi']}")
        if extra:
            line += "，" + "，".join(extra)
        if now.get("time"):
            line += f"（{now['time']} 更新）"
        parts.append(line + "。")

    for d in days:
        label = d["date"] or ""
        seg = [f"{label}{d['weather']}"]
        if d["low"] and d["high"]:
            seg.append(f"{d['low']}到{d['high']}")
        elif d["high"]:
            seg.append(d["high"])
        wind_day = d["wind"] if d.get("wind_final") else _wind_text(d["wind"])
        if wind_day:
            seg.append(wind_day)
        parts.append("，".join(seg) + "。")

    report = "\n".join(parts)
    cache_manager.set(CacheType.WEATHER, cache_key, report)
    logger.bind(tag=TAG).info(f"天气查询成功: {loc} → {city_id} / {len(days)} 天")
    return ActionResponse(Action.REQLLM, report, None)
