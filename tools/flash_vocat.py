"""一键给 ESP-VoCat 刷小智固件。

自动找 COM 口 → 认出 ESP32-S3 → 烧 merged-binary.bin → 校验。

用法：
    py -3.13 flash_vocat.py                 # 自动找口并烧
    py -3.13 flash_vocat.py --port COM5     # 指定口
    py -3.13 flash_vocat.py --list          # 只看有哪些串口
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
FIRMWARE = HERE / "merged-binary.bin"
BAUD = "460800"
SUBCMD = "write-flash"   # esptool v5 用连字符；v4 用下划线，下面会自适应


def run(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def list_ports():
    try:
        from serial.tools import list_ports as lp
        return [p.device for p in lp.comports()]
    except Exception as e:
        print(f"枚举串口失败: {e}")
        return []


def probe(port):
    """返回芯片型号（能连上就是 ESP 芯片），失败返回 None。"""
    for cmd in (["chip-id"], ["chip_id"]):
        try:
            r = run([sys.executable, "-m", "esptool", "-p", port] + cmd, timeout=25)
        except subprocess.TimeoutExpired:
            continue
        out = (r.stdout or "") + (r.stderr or "")
        low = out.lower()
        if "esp32-s3" in low:
            return "ESP32-S3"
        if "esp32" in low and "connecting" in low:
            return "ESP32(其他型号)"
    return None


def flash(port):
    for cmd in (SUBCMD, "write_flash"):
        print(f"\n烧录中（{port}, 0x0）… 约 30~60 秒，别拔线")
        r = run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "-p", port,
                 "-b", BAUD, cmd, "0x0", str(FIRMWARE)], timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        print(out[-1500:])
        if "Hash of data verified" in out or "hash of data verified" in out.lower():
            return True
        if "invalid choice" in out.lower() or "no such command" in out.lower():
            continue
        return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    ports = list_ports()
    print(f"检测到串口: {ports or '无'}")
    if a.list:
        return 0
    if not FIRMWARE.exists():
        print(f"[X] 找不到 {FIRMWARE}，请先解压 release zip")
        return 1
    print(f"固件: {FIRMWARE}  ({FIRMWARE.stat().st_size/1048576:.1f} MB)")

    target = a.port
    if not target:
        if not ports:
            print("\n[X] 没有串口。检查：① 用的是数据线不是充电线 ② 换 USB 口 ③ 装 CH340/CP210x 驱动")
            return 1
        for p in ports:
            print(f"  探测 {p} …", end="", flush=True)
            chip = probe(p)
            print(f" {chip}" if chip else " 无响应")
            if chip and "S3" in chip:
                target = p
                break
        if not target:
            print("\n[X] 没找到 ESP32-S3。按住 BOOT 键再插 USB（进下载模式），然后重跑本脚本。")
            return 1

    print(f"\n目标串口: {target}")
    ok = flash(target)
    if ok:
        print("\n✅ 烧录成功，板子会自动重启。")
        print("下一步：手机连 Xiaozhi-XXXX → 开 192.168.4.1 → 高级 → OTA 填")
        print("        http://192.168.1.10:8003/xiaozhi/ota/")
        return 0
    print("\n[X] 失败。常见原因：没进下载模式（按住 BOOT 再插线）、线是充电线、串口被其他程序占用。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
