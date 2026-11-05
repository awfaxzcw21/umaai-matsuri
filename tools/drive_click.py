#!/usr/bin/env python3
"""接管用的最小点击件：绝对屏幕坐标，只允许 move / click 两种动作。

为什么不用截图找按钮：状态一律从报文读（`live_state.json` 每 1.5 秒被 receiver 刷新），
所以这里只负责「把手放到哪个坐标」，不负责识别。
右键 / 拖拽 / 滚轮一律不提供——剧本里只有点按钮这一种交互，缩小误操作面。

    python tools/drive_click.py --move 1764 1122      # 自检：只挪光标，读回位置
    python tools/drive_click.py --click 1764 1122     # 真点一下
"""
import argparse
import ctypes
import sys
import time

user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--move", nargs=2, type=int, metavar=("X", "Y"))
    g.add_argument("--click", nargs=2, type=int, metavar=("X", "Y"))
    ap.add_argument("--settle", type=float, default=0.05, help="按下与抬起之间的间隔秒")
    a = ap.parse_args()
    # 不做 DPI 适配的隐式假设：调用方给什么坐标就点什么，缩放由上层从截图尺寸推
    x, y = a.move if a.move else a.click
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    if a.click:
        user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
        time.sleep(a.settle)
        user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
        print("clicked", flush=True)
    print("target=%d,%d cursor=%d,%d mode=%s"
          % (x, y, p.x, p.y, "click" if a.click else "move"))
    # 光标落点和目标差太多说明 DPI/缩放不对，直接失败，别让上层继续乱点
    if abs(p.x - x) > 3 or abs(p.y - y) > 3:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
