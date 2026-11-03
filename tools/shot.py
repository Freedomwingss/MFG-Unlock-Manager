"""极简截屏（ctypes + GDI，无第三方依赖）。
用法:
  python shot.py out.bmp                        # 全屏
  python shot.py out.bmp --win "标题"            # 指定窗口（屏幕区域）
  python shot.py out.bmp --win "标题" --print    # 指定窗口（PrintWindow，抗遮挡）
  python shot.py out.bmp --win "标题" --half     # 缩小一半
  python shot.py out.bmp --like "关键词"         # 模糊匹配窗口标题
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

u = ctypes.windll.user32
g = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020

args = sys.argv[1:]
if not args:
    raise SystemExit(__doc__)
out = args[0]
title = args[args.index("--win") + 1] if "--win" in args else None
like = args[args.index("--like") + 1] if "--like" in args else None
half = "--half" in args
use_print = "--print" in args

if like:
    part = like.lower()
    found = []

    def _cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n and u.IsWindowVisible(h):
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if part in b.value.lower():
                found.append(b.value)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows(WNDENUMPROC(_cb), 0)
    if not found:
        raise SystemExit("no window matching: " + like)
    title = found[0]
    print("matched:", title)

if title:
    # 同名窗口可能不止一个，优先取当前前台的那个，否则取最后枚举到的
    found_hwnds = []

    def _cb2(h, l):
        n = u.GetWindowTextLengthW(h)
        if n and u.IsWindowVisible(h):
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value == title:
                found_hwnds.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb2), 0)
    if not found_hwnds:
        raise SystemExit(f"window not found: {title}")
    fg = u.GetForegroundWindow()
    hwnd = fg if fg in found_hwnds else found_hwnds[-1]
    u.ShowWindow(hwnd, 9)                      # SW_RESTORE
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # HWND_TOPMOST
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    rect = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    x = max(0, rect.left)
    y = max(0, rect.top)
    w = min(rect.right, sw) - x
    h = min(rect.bottom, sh) - y
    if w <= 0 or h <= 0:
        raise SystemExit("window off-screen")
else:
    hwnd = None
    x = y = 0
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)

hdc = u.GetDC(None)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)

if use_print and hwnd:
    u.PrintWindow(hwnd, mdc, 2)      # PW_RENDERFULLCONTENT
else:
    time.sleep(0.45)                  # 等窗口置前生效
    g.BitBlt(mdc, 0, 0, w, h, hdc, x, y, SRCCOPY)

out_w, out_h = w, h
if half:
    out_w, out_h = max(1, w // 2), max(1, h // 2)
    mdc2 = g.CreateCompatibleDC(hdc)
    bmp2 = g.CreateCompatibleBitmap(hdc, out_w, out_h)
    g.SelectObject(mdc2, bmp2)
    g.SetStretchBltMode(mdc2, 4)
    g.StretchBlt(mdc2, 0, 0, out_w, out_h, mdc, 0, 0, w, h, SRCCOPY)
    mdc, bmp = mdc2, bmp2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


bi = BITMAPINFOHEADER()
bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.biWidth = out_w
bi.biHeight = -out_h
bi.biPlanes = 1
bi.biBitCount = 32
bi.biCompression = 0

stride = out_w * 4
buf = ctypes.create_string_buffer(stride * out_h)
g.GetDIBits(mdc, bmp, 0, out_h, buf, ctypes.byref(bi), 0)

pixels = bytes(buf)
header = b"BM" + struct.pack("<IHHI", 14 + 40 + len(pixels), 0, 0, 14 + 40)
info = struct.pack("<IiiHHIIiiII", 40, out_w, -out_h, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
with open(out, "wb") as f:
    f.write(header + info + pixels)

if hwnd:
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # HWND_NOTOPMOST

u.ReleaseDC(None, hdc)
g.DeleteObject(bmp)
g.DeleteDC(mdc)
print("saved", out, out_w, out_h)
