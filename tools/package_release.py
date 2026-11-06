"""打成可以拷走的 zip：排除虚拟环境、缓存、数据库、真实 .env。

    python tools/package_release.py
产物：release/kidbox_<YYYYMMDD_HHMM>.zip
"""
import datetime
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "release"

EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", ".cache", "release", ".git", ".idea", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}
EXCLUDE_NAMES = {".env", ".DS_Store", "Thumbs.db"}
TOP_DIRS = ["kidbox", "tools"]
TOP_FILES = ["setup.bat", "start_text.bat", "start_script.bat", "doctor.bat",
             "requirements.txt", "requirements-core.txt", "requirements-audio.txt",
             "demo_script.txt", "README.md", "快速开始.md", "儿童英语AI硬件_方案与DIY教程.md"]


# 压缩包里用纯 ASCII 名，避免不同 Windows 解压工具把中文名解成乱码
NAME_MAP = {
    "快速开始.md": "QUICKSTART.md",
    "儿童英语AI硬件_方案与DIY教程.md": "DESIGN_AND_DIY_GUIDE.md",
}


def arcname(p: Path) -> str:
    rel = p.relative_to(ROOT).as_posix()
    name = p.name
    if name in NAME_MAP:
        rel = rel[: -len(name)] + NAME_MAP[name]
    return rel


def wanted(p: Path) -> bool:
    if p.name in EXCLUDE_NAMES or p.suffix in EXCLUDE_SUFFIX:
        return False
    if any(part in EXCLUDE_DIRS for part in p.parts):
        return False
    return p.is_file()


def main():
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out = OUT_DIR / f"kidbox_{stamp}.zip"

    files = []
    for d in TOP_DIRS:
        base = ROOT / d
        if base.exists():
            files += [p for p in base.rglob("*") if wanted(p)]
    for f in TOP_FILES:
        p = ROOT / f
        if p.exists() and wanted(p):
            files.append(p)
    # .env.example 是模板，要带上
    env_example = ROOT / "kidbox" / ".env.example"
    if env_example.exists():
        files.append(env_example)

    files = sorted(set(files))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname(p))

    # 自检：能不能正常读回来
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        bad = z.testzip()
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"打包完成：{out}")
    print(f"  文件数 {len(names)}；大小 {size_mb:.2f} MB；损坏项 {bad or '无'}")
    print(f"  含音频 {sum(1 for n in names if n.endswith('.mp3'))} 个")
    print(f"  解开后目录：kidbox/ tools/ *.bat requirements*.txt demo_script.txt 文档")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
