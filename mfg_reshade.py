#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mfg_reshade.py — 自动下载并部署「精简版」ReShade（仅作 add-on 宿主）

做法：从官方安装器 ReShade_Setup_x.y.z_Addon.exe 里解出内嵌的 ReShade64.dll，
只把这一个 dll 以代理 DLL 形式放进游戏目录，不装任何 shader / 预设 / 效果包。
"""

import io
import mmap
import os
import sys
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile

from mfg_core import file_desc, file_version, version_str

PAGE = "https://reshade.me/"
SETUP_URL = "https://reshade.me/downloads/ReShade_Setup_{v}_Addon.exe"

def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DATA = os.path.join(_base_dir(), "data")
RS_DIR = os.path.join(APP_DATA, "reshade")
CACHE_DLL = os.path.join(RS_DIR, "dll")
BACKUP_DIR = os.path.join(APP_DATA, "backup")

UA = "MFGUnlocker/1.0"

# 代理 DLL 名（按优先级）
PROXY_NAMES = ("dxgi.dll", "d3d12.dll", "d3d11.dll", "d3d9.dll", "opengl32.dll")

INIS = """[GENERAL]
EffectSearchPaths=
TextureSearchPaths=
PerformanceMode=1
NoDebugInfo=1
"""


# --------------------------------------------------------------------------
# 版本 / 下载
# --------------------------------------------------------------------------

def latest_version(timeout=20):
    req = urllib.request.Request(PAGE, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "ignore")
    vers = re.findall(r"ReShade_Setup_([\d.]+)_Addon\.exe", html)
    if not vers:
        raise RuntimeError("未能从 reshade.me 解析出版本号")
    return max(vers, key=lambda s: tuple(int(x) for x in s.split(".") if x.isdigit()))


def _setup_path(version):
    return os.path.join(RS_DIR, f"ReShade_Setup_{version}_Addon.exe")


def download_setup(version, progress=None):
    os.makedirs(RS_DIR, exist_ok=True)
    dst = _setup_path(version)
    if os.path.isfile(dst) and os.path.getsize(dst) > 1024 * 1024:
        return dst
    url = SETUP_URL.format(v=version)
    tmp = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dst)
    return dst


# --------------------------------------------------------------------------
# 解包
# --------------------------------------------------------------------------

def _find_zip(blob):
    """安装器把 dll 打包成 zip 附在尾部，从前向后找第一个可读的 zip"""
    i = 0
    while True:
        i = blob.find(b"PK\x03\x04", i)
        if i < 0:
            return None
        try:
            z = zipfile.ZipFile(io.BytesIO(blob[i:]))
            names = [n for n in z.namelist() if n.lower().endswith(".dll")]
            if names:
                return z
        except Exception:
            pass
        i += 4


def extract_dll(version, progress=None):
    """从安装器解出 ReShade64.dll，返回缓存路径"""
    os.makedirs(CACHE_DLL, exist_ok=True)
    out = os.path.join(CACHE_DLL, f"ReShade64_{version}.dll")
    if os.path.isfile(out) and file_version(out):
        return out

    setup = download_setup(version, progress)
    with open(setup, "rb") as f:
        blob = f.read()
    z = _find_zip(blob)
    if not z:
        raise RuntimeError("安装器里没找到内嵌的 dll 包（版本格式可能变了）")
    target = None
    for name in z.namelist():
        if name.lower() == "reshade64.dll":
            target = name
            break
    if not target:
        raise RuntimeError("内嵌包里没有 ReShade64.dll")
    data = z.read(target)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if file_version(tmp) is None:
        os.remove(tmp)
        raise RuntimeError("解出的 dll 无效")
    os.replace(tmp, out)
    return out


# --------------------------------------------------------------------------
# API 检测
# --------------------------------------------------------------------------

_API_HINTS = (
    ("d3d12.dll", "d3d12"),
    ("d3d11.dll", "d3d11"),
    ("dxgi.dll", "dxgi"),
    ("d3d9.dll", "d3d9"),
    ("opengl32.dll", "opengl32"),
    ("vulkan-1.dll", "vulkan"),
)


def detect_api(exe_path):
    """粗判图形 API：在 exe 里搜 dll 名字（含 UTF-16）"""
    if not exe_path or not os.path.isfile(exe_path):
        return "unknown", []
    hits = []
    try:
        with open(exe_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for name, api in _API_HINTS:
                    if mm.find(name.encode()) >= 0 or mm.find(name.encode("utf-16-le")) >= 0:
                        hits.append(api)
    except Exception:
        return "unknown", []
    if "vulkan" in hits and not any(h in hits for h in ("d3d12", "d3d11", "dxgi", "d3d9")):
        return "vulkan", hits
    for api in ("d3d12", "d3d11", "dxgi", "d3d9", "opengl32"):
        if api in hits:
            return api, hits
    return "unknown", hits


def proxy_for(api):
    return {
        "d3d12": "dxgi.dll",   # DXGI 是 DX12 的基础，代理 dxgi 最通用
        "d3d11": "dxgi.dll",
        "dxgi": "dxgi.dll",
        "d3d9": "d3d9.dll",
        "opengl32": "opengl32.dll",
    }.get(api, "dxgi.dll")


# --------------------------------------------------------------------------
# 部署 / 卸载
# --------------------------------------------------------------------------

def is_reshade_dll(path):
    if not os.path.isfile(path):
        return False
    if "reshade" in file_desc(path).lower():
        return True
    # 回退：dll 里一定带有 ReShade 的版本/产品字符串
    try:
        with open(path, "rb") as f:
            head = f.read(6 * 1024 * 1024)
        return (b"ReShade" in head) and (b"reshade-shaders" in head or b"crosire" in head.lower())
    except Exception:
        return False


def find_deployed(exe_dir):
    """返回 (proxy_dll_path, version) 或 (None, None)"""
    for name in PROXY_NAMES:
        p = os.path.join(exe_dir, name)
        if is_reshade_dll(p):
            return p, file_version(p)
    return None, None


def _backup_dir(tag):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(BACKUP_DIR, f"{stamp}_{tag}")
    os.makedirs(d, exist_ok=True)
    return d


def deploy(exe_dir, exe_path, version, progress=None):
    """部署精简 ReShade。返回 (ok, msg)"""
    if not exe_dir or not os.path.isdir(exe_dir):
        return False, "游戏目录不存在"
    api, hits = detect_api(exe_path)
    if api == "vulkan":
        return False, ("该游戏只检出了 Vulkan —— ReShade 需要全局 Vulkan 层，\n"
                       "不能只放一个 dll，请在游戏里手动安装。")

    src = extract_dll(version, progress)
    proxy = proxy_for(api)
    target = os.path.join(exe_dir, proxy)

    if os.path.isfile(target) and not is_reshade_dll(target):
        # 已被别的工具占用（DXVK / Special K 等），换一个没被占用的代理名
        alt = None
        for name in PROXY_NAMES:
            p = os.path.join(exe_dir, name)
            if not os.path.isfile(p):
                alt = (name, p)
                break
        if not alt:
            return False, f"{proxy} 已被其他工具占用，且没有可用的替代名字"
        proxy, target = alt

    backup = ""
    if os.path.isfile(target):
        backup = _backup_dir("reshade")
        shutil.copy2(target, os.path.join(backup, proxy))

    shutil.copy2(src, target)

    ini = os.path.join(exe_dir, "ReShade.ini")
    wrote_ini = False
    if not os.path.isfile(ini):
        with open(ini, "w", encoding="utf-8") as f:
            f.write(INIS)
        wrote_ini = True

    v = version_str(file_version(target))
    msg = (f"已部署精简版 ReShade {v} → {proxy}\n"
           f"（API 判定：{api}）\n\n"
           f"未安装任何 shader / 预设 / 效果包，只作为 add-on 宿主。\n"
           f"启动游戏后按 Home 可打开 ReShade 面板。")
    if wrote_ini:
        msg += "\n已写入最小 ReShade.ini（关闭效果搜索路径）。"
    if backup:
        msg += f"\n原 {proxy} 已备份到：{backup}"
    return True, msg


def uninstall(exe_dir):
    """移除 ReShade 代理 dll 与 ini（移入备份），返回 (ok, msg)"""
    if not exe_dir or not os.path.isdir(exe_dir):
        return False, "游戏目录不存在"
    found, _v = find_deployed(exe_dir)
    if not found:
        return False, "未在该目录检测到 ReShade"

    backup = _backup_dir("reshade_remove")
    moved = []
    try:
        shutil.move(found, os.path.join(backup, os.path.basename(found)))
        moved.append(os.path.basename(found))
    except Exception as e:
        return False, f"移动 {os.path.basename(found)} 失败：{e}"
    for name in ("ReShade.ini", "ReShadePreset.ini"):
        p = os.path.join(exe_dir, name)
        if os.path.isfile(p):
            try:
                shutil.move(p, os.path.join(backup, name))
                moved.append(name)
            except Exception:
                pass
    return True, "已移除 %s\n备份：%s" % ("、".join(moved), backup)


def addon_ready(exe_dir):
    """是否已具备运行插件的条件：ReShade 存在"""
    p, _ = find_deployed(exe_dir)
    return bool(p)
