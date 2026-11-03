#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mfg_dlssg.py — 一键更新 DLSS 帧生成 DLL（nvngx_dlssg.dll）

数据源：DLSS Swapper 的公开 manifest（按官方 SDK 版本索引，带 md5）
下载单个 zip → 解出 nvngx_dlssg.dll → 校验 → 备份原文件后替换
"""

import hashlib
import io
import json
import os
import sys
import shutil
import tempfile
import time
import urllib.request
import zipfile

def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DATA = os.path.join(_base_dir(), "data")
CACHE = os.path.join(APP_DATA, "dlssg")
BACKUP_DIR = os.path.join(APP_DATA, "backup")

MANIFEST_URL = ("https://raw.githubusercontent.com/beeradmoore/"
                "dlss-swapper-manifest-builder/master/manifest.json")
UA = "MFGUnlocker/1.2"
DLL_NAME = "nvngx_dlssg.dll"


MIRRORS = ("", "https://gh-proxy.com/")


def _get(url, timeout=25):
    """直连失败时走镜像重试"""
    last = None
    for prefix in MIRRORS:
        try:
            req = urllib.request.Request(prefix + url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if data:
                return data
        except Exception as e:
            last = e
    raise last if last else RuntimeError("下载失败")


def latest(timeout=30):
    """返回最新 dlss_g 条目 dict"""
    data = json.loads(_get(MANIFEST_URL, timeout).decode("utf-8"))
    items = [e for e in data.get("dlss_g", []) if not e.get("is_dev_file")]
    if not items:
        raise RuntimeError("manifest 里没有可用的 dlss_g 条目")
    return max(items, key=lambda e: e.get("version_number", 0))


def _cache_path(entry):
    return os.path.join(CACHE, f"dlss_g_{entry['version']}_{entry['zip_md5_hash']}.zip")


def download_zip(entry, progress=None):
    os.makedirs(CACHE, exist_ok=True)
    dst = _cache_path(entry)
    if os.path.isfile(dst):
        try:
            if hashlib.md5(open(dst, "rb").read()).hexdigest().upper() == entry["zip_md5_hash"]:
                return dst
        except OSError:
            pass

    url = entry["download_url"]
    tmp = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or entry.get("zip_file_size") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if entry.get("zip_md5_hash"):
        if hashlib.md5(open(tmp, "rb").read()).hexdigest().upper() != entry["zip_md5_hash"]:
            os.remove(tmp)
            raise RuntimeError("zip 校验失败，下载可能被篡改")
    os.replace(tmp, dst)
    return dst


def extract_dll(entry, progress=None):
    """解出 dll 并校验，返回本地路径"""
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{DLL_NAME}.{entry['version']}")
    if os.path.isfile(out):
        try:
            if hashlib.md5(open(out, "rb").read()).hexdigest().upper() == entry["md5_hash"]:
                return out
        except OSError:
            pass

    zpath = download_zip(entry, progress)
    with zipfile.ZipFile(zpath) as z:
        target = None
        for n in z.namelist():
            if os.path.basename(n).lower() == DLL_NAME:
                target = n
                break
        if not target:
            raise RuntimeError("压缩包里没有 nvngx_dlssg.dll")
        data = z.read(target)
    if hashlib.md5(data).hexdigest().upper() != entry["md5_hash"]:
        raise RuntimeError("dll 校验失败")
    with open(out, "wb") as f:
        f.write(data)
    return out


def targets_for_game(game):
    """返回应该在哪些位置放置/替换 dll（列表中优先真实路径）"""
    paths = []
    d = game.dlls.get(DLL_NAME)
    if d and d.get("path"):
        paths.append(d["path"])
    if game.exe_dir:
        p = os.path.join(game.exe_dir, DLL_NAME)
        if p not in paths:
            paths.append(p)
    return paths


def install(game, entry, progress=None):
    """更新游戏的 nvngx_dlssg.dll，返回 (ok, msg)"""
    src = extract_dll(entry, progress)
    targets = targets_for_game(game)
    if not targets:
        return False, "找不到该游戏的安装位置"

    # 优先替换游戏自带的那份；若不存在则放到 exe 同级
    d = game.dlls.get(DLL_NAME)
    target = d["path"] if (d and d.get("path")) else targets[-1]

    backup = ""
    if os.path.isfile(target):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch if ch.isalnum() else "_" for ch in game.name)[:30]
        backup = os.path.join(BACKUP_DIR, f"{stamp}_dlssg_{safe}")
        os.makedirs(backup, exist_ok=True)
        shutil.copy2(target, os.path.join(backup, DLL_NAME))
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)

    shutil.copy2(src, target)
    return True, (f"已更新 {DLL_NAME} → {entry['version']}\n"
                  f"位置：{target}" + (f"\n原文件备份：{backup}" if backup else ""))


def restore(game):
    """把备份里的 dll 还回去（找最新一份该游戏的备份）"""
    if not os.path.isdir(BACKUP_DIR):
        return False, "没有备份"
    safe = "".join(ch if ch.isalnum() else "_" for ch in game.name)[:30]
    cands = sorted([d for d in os.listdir(BACKUP_DIR) if d.endswith("_dlssg_" + safe)], reverse=True)
    if not cands:
        return False, "没有该游戏的 DLSS-G 备份"
    src = os.path.join(BACKUP_DIR, cands[0], DLL_NAME)
    if not os.path.isfile(src):
        return False, "备份文件夹里没有 dll"
    d = game.dlls.get(DLL_NAME)
    target = d["path"] if (d and d.get("path")) else os.path.join(game.exe_dir, DLL_NAME)
    shutil.copy2(src, target)
    return True, f"已还原到备份版本：{src}"
