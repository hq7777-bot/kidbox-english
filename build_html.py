"""把 md 转成带样式的单文件 html（离线可看、可直接打印）。"""
import re
import sys
from pathlib import Path

import markdown

CSS = """
:root{--fg:#1a1a1a;--muted:#5f6b7a;--line:#e3e8ef;--accent:#c0392b;--accent2:#0f766e;
--bg:#fbfcfd;--card:#fff;--warn:#fff8e6;--warnline:#f0c36d;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.75 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:1000px;margin:0 auto;padding:48px 28px 96px;background:var(--card);
box-shadow:0 0 0 1px var(--line);}
h1{font-size:30px;line-height:1.35;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:22px;margin:44px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--line);}
h3{font-size:17px;margin:26px 0 10px;color:var(--accent2)}
p,li{color:#243040}
blockquote{margin:16px 0;padding:12px 16px;background:var(--warn);border-left:4px solid var(--warnline);
border-radius:0 6px 6px 0;color:#5a4a1f}
blockquote p{margin:0;color:#5a4a1f}
table{width:100%;border-collapse:collapse;margin:16px 0;font-size:14.5px;display:table}
th,td{border:1px solid var(--line);padding:9px 11px;vertical-align:top;text-align:left}
th{background:#f4f7fa;font-weight:600;white-space:nowrap}
tr:nth-child(even) td{background:#fafbfc}
code{background:#f2f5f9;border:1px solid var(--line);border-radius:4px;padding:1px 5px;
font:13.5px/1.5 "Cascadia Code",Consolas,monospace;color:#b03a2e}
pre{background:#1f2430;color:#dfe6f3;padding:16px 18px;border-radius:8px;overflow:auto;
font:13.5px/1.6 "Cascadia Code",Consolas,monospace;margin:16px 0}
pre code{background:none;border:0;color:inherit;padding:0;font-size:13.5px}
a{color:#0b6bcb;text-decoration:none;border-bottom:1px solid #bcd7f4}
hr{border:0;border-top:1px solid var(--line);margin:34px 0}
ul,ol{padding-left:22px}
li{margin:4px 0}
.meta{color:var(--muted);font-size:14px;margin-bottom:26px}
h2:first-of-type{margin-top:32px}
@media print{body{background:#fff}.wrap{box-shadow:none;max-width:none;padding:0}
h2{page-break-after:avoid}table{page-break-inside:avoid}}
"""


def convert(src: Path, dst: Path, title: str):
    md = src.read_text(encoding="utf-8")
    # 去掉文首 H1 与紧随的引用块，单独排版
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="wrap">
{body}
</div></body></html>"""
    dst.write_text(html, encoding="utf-8")
    return len(html)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    print(convert(src, dst, src.stem), "bytes ->", dst)
