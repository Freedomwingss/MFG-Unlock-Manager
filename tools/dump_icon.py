#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 exe 的图标导成 PNG，用来确认图标真的嵌进去了

用法：python tools/dump_icon.py <exe> <out.png>
"""
import ctypes
import ctypes.wintypes as wt
import sys

from PIL import Image

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
DIB_RGB_COLORS = 0
BI_RGB = 0


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wt.HICON), ("iIcon", ctypes.c_int),
                ("dwAttributes", wt.DWORD),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD),
                ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def dump(exe, out):
    info = SHFILEINFOW()
    got = ctypes.windll.shell32.SHGetFileInfoW(
        exe, 0, ctypes.byref(info), ctypes.sizeof(info),
        SHGFI_ICON | SHGFI_LARGEICON)
    if not got or not info.hIcon:
        raise SystemExit("no icon resource in exe")

    ii = ICONINFO()
    ctypes.windll.user32.GetIconInfo(info.hIcon, ctypes.byref(ii))

    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    gdi.GetObjectW.restype = ctypes.c_int
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]

    bm = BITMAP()
    gdi.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
    w, h = bm.bmWidth, bm.bmHeight

    hdc = ctypes.windll.user32.GetDC(None)
    mem = gdi.CreateCompatibleDC(hdc)
    gdi.SelectObject(mem, ii.hbmColor)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # 负值 = 从上到下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = BI_RGB

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, ii.hbmColor, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    img.save(out)

    gdi.DeleteDC(mem)
    ctypes.windll.user32.ReleaseDC(None, hdc)
    print(f"{w}x{h} -> {out}")


if __name__ == "__main__":
    dump(sys.argv[1], sys.argv[2])
