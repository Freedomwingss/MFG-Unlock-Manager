#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mfg_icons.py — 从 exe 提取图标并编码为 PNG（纯标准库）"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import sys
import struct
import tempfile
import zlib

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ICON_CACHE = os.path.join(
    _base_dir(), "data", "icons")

BI_RGB = 0
DIB_RGB_COLORS = 0


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD),
                ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


# --- Win32 声明（64 位下句柄必须显式声明，否则 OverflowError）---
user32.PrivateExtractIconsW.argtypes = [
    wt.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(wt.HICON), ctypes.POINTER(wt.UINT), wt.UINT, wt.UINT]
user32.PrivateExtractIconsW.restype = wt.UINT
user32.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.DestroyIcon.argtypes = [wt.HICON]
user32.DestroyIcon.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteObject.restype = wt.BOOL


def _png(rgba, w, h):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _load_icon(path, size):
    hicons = (wt.HICON * 1)()
    n = user32.PrivateExtractIconsW(str(path), 0, size, size,
                                    hicons, None, 1, 0)
    if not n or not hicons[0]:
        return None
    return hicons[0]


def extract_png(exe_path, size=32):
    """返回 PNG 字节；失败返回 None"""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    hicon = _load_icon(exe_path, size)
    if not hicon:
        return None
    try:
        info = ICONINFO()
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None
        try:
            bm = BITMAP()
            gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bm))
            w, h = int(bm.bmWidth), int(bm.bmHeight)
            if w <= 0 or h <= 0:
                return None
            bmi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, BI_RGB, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            hdc = user32.GetDC(None)
            try:
                got = gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf,
                                      ctypes.byref(bmi), DIB_RGB_COLORS)
            finally:
                user32.ReleaseDC(None, hdc)
            if not got:
                return None
            data = bytearray(buf.raw[:w * h * 4])
            has_alpha = any(data[3::4])
            if not has_alpha:
                data[3::4] = b"\xff" * (w * h)
                # BGRA -> RGBA
                data[0::4], data[2::4] = data[2::4], data[0::4]
            else:
                data[0::4], data[2::4] = data[2::4], data[0::4]
            return _png(bytes(data), w, h)
        finally:
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)
    finally:
        user32.DestroyIcon(hicon)


def blank_png(size=24):
    """全透明占位图标，用于没有图标的行保持对齐"""
    return _png(b"\x00" * (size * size * 4), size, size)


def icon_png(exe_path, size=32):
    """带磁盘缓存"""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    try:
        st = os.stat(exe_path)
    except OSError:
        return None
    key = f"{exe_path.lower()}|{st.st_size}|{int(st.st_mtime)}|{size}"
    name = hashlib.md5(key.encode("utf-8", "ignore")).hexdigest() + ".png"
    cache = os.path.join(ICON_CACHE, name)
    if os.path.isfile(cache):
        try:
            return open(cache, "rb").read()
        except OSError:
            pass
    png = extract_png(exe_path, size)
    if png:
        try:
            os.makedirs(ICON_CACHE, exist_ok=True)
            with open(cache, "wb") as f:
                f.write(png)
        except OSError:
            pass
    return png


if __name__ == "__main__":
    import sys
    p = sys.argv[1]
    out = extract_png(p, 48)
    print("png bytes:", len(out) if out else None)
    if out:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_test.png"), "wb") as f:
            f.write(out)
