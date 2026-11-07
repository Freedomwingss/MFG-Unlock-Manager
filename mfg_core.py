#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mfg_core.py — MFG Unlock Manager 核心逻辑
扫描 Steam 库 / 分析游戏是否支持 DLSS 多帧生成解锁 / 拉取最新 release / 安装与卸载
纯标准库，无第三方依赖。
"""

import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

APP_NAME = "MFG Unlock Manager"
def _read_version():
    """优先读 exe 旁的 VERSION；打包后读内嵌副本，都没有则用兜底值"""
    bases = [_base_dir(), getattr(sys, "_MEIPASS", ""),
             os.path.dirname(os.path.abspath(__file__))]
    for base in bases:
        if not base:
            continue
        try:
            with open(os.path.join(base, "VERSION"), "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return "1.0.0"

REPO = "mavismmg/MFGAdaUnlock-RenoDx"
GH_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ADDON_NAME = "renodx-mfgunlock.addon64"

def _base_dir():
    """便携式：数据放在程序旁边，不占 C 盘"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DATA = os.path.join(_base_dir(), "data")
APP_VERSION = _read_version()
CACHE_DIR = os.path.join(APP_DATA, "cache")
# 改动主程序/dll 识别逻辑时 +1，旧缓存自动失效
CACHE_VERSION = 5
BACKUP_DIR = os.path.join(APP_DATA, "backup")
CONFIG_PATH = os.path.join(APP_DATA, "config.json")

DEFAULT_LIBS = [
    r"F:\Steam\steamapps\common",
    r"G:\SteamLibrary\steamapps\common",
]

# 卸载/备份时纳入的文件（只移入备份，不硬删）
MOD_FILES = (
    ADDON_NAME,
    "MFGAdaUnlock.ini",
    "MFGAdaUnlock-XeMFG.log",
    "MFGAdaUnlock-XeMFG-compatibility.json",
    "MFGAdaUnlock.profiles.json",
)

DLL_TARGETS = (
    "nvngx_dlssg.dll",
    "sl.dlss_g.dll",
    "sl.interposer.dll",
    "libxess_fg.dll",
    "libxell.dll",
)

# MFG 1.8 的 XMX 模式要求的 Intel 运行时版本（见上游 README）
INTEL_XESS_VER = (1, 3, 1, 78)
INTEL_XELL_VER = (1, 3, 2, 10)

# 作者 README 中标记为 Working 的游戏（小写关键词匹配）
VERIFIED_DLSSG = [
    "stalker 2", "s.t.a.l.k.e.r", "god of war ragnar", "death stranding 2",
    "clair obscur", "last of us part ii", "resident evil requiem", "pragmata",
    "cyberpunk 2077", "alan wake 2", "dragon's dogma 2", "dragons dogma 2",
    "starfield", "spider-man 2", "spiderman 2", "mortal shell", "black myth",
    "assetto corsa", "indiana jones", "silent hill 2", "forza horizon",
    "assassin's creed shadows", "assassins creed shadows", "stellar blade",
    "doom the dark ages", "horizon forbidden west", "avatar frontiers",
    "borderlands 4", "payday 3", "ghost of tsushima", "gothic 1 remake",
    "dying light the beast", "onimusha", "monster hunter wilds",
    "star wars outlaws", "phantom blade zero", "影之刃零", "control resonant",
]
VERIFIED_XEMFG = [
    "witcher 3", "red dead redemption 2", "elden ring", "metro exodus",
    "ghostrunner 2", "darktide", "kingdom come", "mafia the old country",
]

MAX_SCAN_FILES = 400000


# --------------------------------------------------------------------------
# Win32 小工具
# --------------------------------------------------------------------------

def file_version(path):
    """读取 PE 文件版本号 -> (a, b, c, d) 或 None"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        n = ctypes.wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)):
            return None
        data = ctypes.cast(ptr, ctypes.POINTER(ctypes.wintypes.DWORD * 4)).contents
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS, dwFileVersionLS
        if data[0] != 0xFEEF04BD:
            return None
        ms, ls = data[2], data[3]
        return (ms >> 16 & 0xFFFF, ms & 0xFFFF, ls >> 16 & 0xFFFF, ls & 0xFFFF)
    except Exception:
        return None


def version_str(v):
    if not v:
        return "—"
    return ".".join(str(x) for x in v[:3])


def pe_machine(path):
    """'x64' / 'x86' / 'arm64' / None"""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            off = struct.unpack("<I", f.read(4))[0]
            f.seek(off)
            if f.read(4) != b"PE\0\0":
                return None
            machine = struct.unpack("<H", f.read(2))[0]
        return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(machine)
    except Exception:
        return None


def file_desc(path):
    """PE 文件的产品名/描述，用于判断代理 dll 是不是 ReShade"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        n = ctypes.wintypes.UINT()

        lang = "040904b0"
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                              ctypes.byref(ptr), ctypes.byref(n)):
            try:
                words = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
                lang = "%04x%04x" % (words[0], words[1])
            except Exception:
                pass

        out = []
        for key in ("ProductName", "FileDescription", "CompanyName",
                    "OriginalFilename", "InternalName"):
            if ver.VerQueryValueW(buf, f"\\StringFileInfo\\{lang}\\{key}",
                                  ctypes.byref(ptr), ctypes.byref(n)):
                try:
                    out.append(ctypes.wstring_at(ptr.value, n.value))
                except Exception:
                    pass
        return " ".join(out)
    except Exception:
        return ""


def human_size(num):
    num = float(num)
    for unit in ("B", "KB", "MB"):
        if num < 1024:
            return f"{num:.0f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


def sha256_of(path, progress=None):
    h = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 256)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


# --------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------

@dataclass
class GPU:
    name: str = ""
    driver: str = ""
    family: str = ""      # blackwell / ada / ampere / turing / other

    @property
    def label(self):
        if not self.name:
            return "未检测到 NVIDIA 显卡"
        return f"{self.name}  ·  {self.driver}" if self.driver else self.name


def detect_gpu():
    gpu = GPU()
    exe_candidates = [
        r"C:\Windows\System32\nvidia-smi.exe",
        "nvidia-smi",
    ]
    for exe in exe_candidates:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=12,
                creationflags=0x08000000,
            )
            line = (out.stdout or "").strip().splitlines()
            if line:
                parts = [p.strip() for p in line[0].split(",")]
                gpu.name = parts[0]
                gpu.driver = parts[1] if len(parts) > 1 else ""
                break
        except Exception:
            continue

    n = gpu.name.upper()
    if "RTX 50" in n or "B100" in n or "BLACKWELL" in n:
        gpu.family = "blackwell"
    elif "RTX 40" in n:
        gpu.family = "ada"
    elif "RTX 30" in n:
        gpu.family = "ampere"
    elif "RTX 20" in n:
        gpu.family = "turing"
    elif "RTX" in n:
        gpu.family = "ada"
    return gpu


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

V_FULL = "full"
V_NEED_DLL = "needdll"
V_XEMFG = "xemfg"
V_NATIVE = "native"
V_NO = "no"


@dataclass
class Game:
    name: str
    path: str
    library: str
    appid: str = ""
    exe: str = ""
    exe_dir: str = ""
    arch: str = ""
    exe_size: int = 0
    dlls: dict = field(default_factory=dict)       # name -> {"path","ver"}
    reshade: bool = False
    reshade_ver: tuple = None
    installed_version: tuple = None
    verdict: str = "no"
    reason: str = ""
    verified: str = ""                              # "" / "dlssg" / "xemfg"
    xmx_ready: bool = False                         # Intel 运行时文件是否满足 XMX

    @property
    def installed(self):
        return bool(self.installed_version)

    @property
    def addon_path(self):
        return os.path.join(self.exe_dir, ADDON_NAME) if self.exe_dir else ""

    def dlssg_version(self):
        d = self.dlls.get("nvngx_dlssg.dll")
        return d["ver"] if d else None


@dataclass
class Release:
    tag: str
    title: str
    url: str
    size: int
    digest: str
    published: str


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    libs = [p for p in cfg.get("libraries", []) if os.path.isdir(p)]
    if not libs:
        # 没有任何有效库：注册表 + vdf + 全盘浅扫
        libs = discover_steam_libraries()
    cfg["libraries"] = libs
    cfg.setdefault("cache", {})
    return cfg


def save_config(cfg):
    os.makedirs(APP_DATA, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _reg_steam_roots():
    """注册表里登记的 Steam 安装目录（HKLM + HKCU 都看）"""
    roots = []
    try:
        import winreg
    except Exception:
        return roots
    probes = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key, name in probes:
        try:
            with winreg.OpenKey(hive, key) as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
        if v and os.path.isdir(v):
            v = os.path.normpath(v)
            if v not in roots:
                roots.append(v)
    return roots


def list_drives():
    """所有可用盘符（固定盘 + 移动盘 + 网络盘，跳过光驱）"""
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return out
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = chr(ord("A") + i) + ":\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3, 4):
                out.append(root)
        except Exception:
            out.append(root)
    return out


def discover_steam_libraries(deep=True):
    """注册表 → libraryfolders.vdf → 常见布局 → 全盘浅扫，返回 steamapps\\common 列表"""
    found = []
    seen = set()

    def add_common(base):
        c = os.path.normpath(os.path.join(base, "steamapps", "common"))
        key = os.path.normcase(c)
        if os.path.isdir(c) and key not in seen:
            seen.add(key)
            found.append(c)

    roots = _reg_steam_roots()

    # vdf 里登记的其它库
    queue = list(roots)
    for r in queue:
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        try:
            text = open(vdf, "r", encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for m in re.findall(r'"path"\s+"([^"]+)"', text):
            p = os.path.normpath(m.replace("\\\\", "\\"))
            if os.path.isdir(p) and p not in roots:
                roots.append(p)
    for r in roots:
        add_common(r)

    # 常见安装位置
    names = ("Steam", "SteamLibrary", "SteamGames", "Games\\Steam",
             "Games\\SteamLibrary", "Program Files (x86)\\Steam", "Program Files\\Steam")
    for d in list_drives():
        for n in names:
            add_common(os.path.join(d, n))

    # 兜底：每个盘的顶层目录里找 steamapps\libraryfolders.vdf
    if deep:
        for d in list_drives():
            try:
                subs = os.listdir(d)
            except OSError:
                continue
            for sub in subs:
                if sub.lower() in ("$recycle.bin", "system volume information",
                                   "windows", "programdata"):
                    continue
                base = os.path.join(d, sub)
                try:
                    if not os.path.isdir(base):
                        continue
                except OSError:
                    continue
                if os.path.isfile(os.path.join(base, "steamapps", "libraryfolders.vdf")):
                    add_common(base)
    return found


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

SKIP_DIRS = {"steamworks shared", "steam controller configs", "steamvr", "steamlinuxruntime_sniper"}
BAD_EXE = ("unins", "setup", "redist", "vc_redist", "dxsetup", "dxwebsetup", "physx",
           "crash", "easyanticheat", "eac_", "unitycrashhandler", "cleanup", "report",
           "dotnet", "installer", "launcher_installer", "benchmark", "editor",
           "createdump", "crashpad", "oalinst", "csc.exe", "msbuild", "uninstall")


def parse_appmanifests(steamapps_dir):
    """installdir(小写) -> appid"""
    mapping = {}
    try:
        names = os.listdir(steamapps_dir)
    except OSError:
        return mapping
    for n in names:
        if not (n.startswith("appmanifest_") and n.endswith(".acf")):
            continue
        try:
            text = open(os.path.join(steamapps_dir, n), "r", encoding="utf-8", errors="ignore").read()
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            installdir = re.search(r'"installdir"\s+"([^"]+)"', text)
            if appid and installdir:
                mapping[installdir.group(1).lower()] = appid.group(1)
        except Exception:
            continue
    return mapping


def enumerate_games(libraries):
    games = []
    seen = set()
    for lib in libraries:
        if not os.path.isdir(lib):
            continue
        steamapps = os.path.dirname(lib)
        appids = parse_appmanifests(steamapps)
        try:
            entries = list(os.scandir(lib))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            if e.name.lower() in SKIP_DIRS or e.name.startswith("."):
                continue
            if not any(os.scandir(e.path)):
                continue
            key = os.path.normcase(e.path)
            if key in seen:
                continue
            seen.add(key)
            games.append(Game(name=e.name, path=e.path, library=lib,
                              appid=appids.get(e.name.lower(), "")))
    games.sort(key=lambda g: g.name.lower())
    return games


def _exe_ok(path, size):
    low = os.path.basename(path).lower()
    if any(b in low for b in BAD_EXE):
        return False
    return size >= 48 * 1024


def _name_score(exe_name, dir_name):
    """exe 名与目录名的相似度加分"""
    a = re.sub(r"[^a-z0-9]", "", os.path.splitext(exe_name)[0].lower())
    b = re.sub(r"[^a-z0-9]", "", dir_name.lower())
    if not a or not b:
        return 0
    if a == b:
        return 60 * 1024 * 1024
    if a.startswith(b) or b.startswith(a):
        return 40 * 1024 * 1024
    if a in b or b in a:
        return 20 * 1024 * 1024
    return 0


def _exe_rank(fname, path, size, game_dir):
    """给候选 exe 打分：名字匹配 > UE Shipping > Binaries 目录 > 体积"""
    low = fname.lower()
    name = re.sub(r"[^a-z0-9]", "", os.path.splitext(low)[0])
    dnorm = re.sub(r"[^a-z0-9]", "", os.path.basename(game_dir).lower())
    M = 1024 * 1024
    score = float(size)

    if name and dnorm:
        if name.startswith(dnorm) or dnorm.startswith(name):
            score += 400 * M
        elif len(name) >= 6 and name[:6] in dnorm:
            score += 200 * M

    if "-shipping" in low:
        score += 300 * M
    plow = path.lower()
    if "binaries" in plow or "win64" in plow or "bin64" in plow:
        score += 120 * M

    # 明显的工具/安装器降权
    if any(k in low for k in ("mod", "merger", "unins", "setup", "install",
                              "crash", "report", "launcher_helper")):
        score -= 900 * M
    return score


def find_main_exe(game_dir):
    """返回 (exe_path, size)"""
    dirs = ["", "Binaries/Win64", "bin", "bin/x64", "bin/win64",
            "platform/steam", "game/binaries/win64", "x64", "win64",
            "build/binaries/win64"]
    # 一层子目录里的 UE 布局（如 Stalker2\Binaries\Win64）
    try:
        for sub in os.listdir(game_dir):
            cand = os.path.join(game_dir, sub, "Binaries", "Win64")
            if os.path.isdir(cand):
                dirs.append(os.path.relpath(cand, game_dir))
    except OSError:
        pass

    best = None  # (path, rank, size)
    for pat in dirs:
        d = os.path.join(game_dir, pat.replace("/", os.sep))
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            if not f.lower().endswith(".exe"):
                continue
            p = os.path.join(d, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if not _exe_ok(p, sz):
                continue
            r = _exe_rank(f, p, sz, game_dir)
            if best is None or r > best[1]:
                best = (p, r, sz)
    if best:
        return best[0], best[2]

    # 深度兜底
    best = None
    count = 0
    skip = ("content", "paks", "saved", "movies", "localization", "redist",
            "support", "data", "assets", "gfx", "gui", "music", "sound",
            "textures", "shaders", "dlc", "mods")
    for root, dnames, files in os.walk(game_dir):
        depth = root[len(game_dir):].count(os.sep)
        if depth >= 3:
            dnames[:] = []
        dnames[:] = [x for x in dnames if x.lower() not in skip]
        for f in files:
            count += 1
            if count > 40000:
                break
            if not f.lower().endswith(".exe"):
                continue
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if not _exe_ok(p, sz):
                continue
            r = _exe_rank(f, p, sz, game_dir)
            if best is None or r > best[2]:
                best = (p, sz, r)
        if count > 40000:
            break

    return (best[0], best[1]) if best else ("", 0)


def scan_dlls(game_dir):
    found = {}
    count = 0
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if d.lower() not in
                   ("content", "paks", "saved", "movies", "localization", "config", ".git")]
        for f in files:
            count += 1
            if count > MAX_SCAN_FILES:
                return found
            low = f.lower()
            if low in DLL_TARGETS and low not in found:
                p = os.path.join(root, f)
                found[low] = {"path": p, "ver": file_version(p)}
    return found


def detect_reshade(exe_dir):
    """返回 (installed, version)"""
    if not exe_dir or not os.path.isdir(exe_dir):
        return False, None
    for name in ("dxgi.dll", "d3d12.dll", "d3d11.dll", "d3d9.dll",
                 "opengl32.dll", "ReShade64.dll"):
        p = os.path.join(exe_dir, name)
        if os.path.isfile(p):
            if "reshade" in file_desc(p).lower():
                return True, file_version(p)
    if os.path.isfile(os.path.join(exe_dir, "ReShade.ini")):
        return True, None
    return False, None


def intel_ready(folder):
    """exe 目录下的 Intel 运行时是否满足 XMX"""
    if not folder:
        return False
    for name, need in (("libxess_fg.dll", INTEL_XESS_VER), ("libxell.dll", INTEL_XELL_VER)):
        p = os.path.join(folder, name)
        if not os.path.isfile(p):
            return False
        v = file_version(p)
        if not v or v < need:
            return False
    return True


def find_intel_sources(game):
    """在游戏目录里找版本达标的 Intel 运行时副本（用于搬到 exe 目录）"""
    need = {"libxess_fg.dll": INTEL_XESS_VER, "libxell.dll": INTEL_XELL_VER}
    found = {}
    skip = {"content", "paks", "saved", "movies", "localization", "config", ".git"}
    for root, dirs, files in os.walk(game.path):
        dirs[:] = [d for d in dirs if d.lower() not in skip]
        for f in files:
            low = f.lower()
            if low in need and low not in found:
                p = os.path.join(root, f)
                v = file_version(p)
                if v and v >= need[low]:
                    found[low] = p
        if len(found) == len(need):
            break
    return found


def place_intel_files(game):
    """把游戏内已有的 Intel 运行时复制到 exe 目录，返回 (ok, msg)"""
    if not game.exe_dir:
        return False, "找不到 exe 目录"
    if intel_ready(game.exe_dir):
        return True, "Intel 运行时已就位"
    src = find_intel_sources(game)
    if len(src) < 2:
        miss = [n for n in ("libxess_fg.dll", "libxell.dll") if n not in src]
        return False, "游戏目录里找不到可用版本：" + "、".join(miss)
    for name, sp in src.items():
        dp = os.path.join(game.exe_dir, name)
        try:
            if os.path.isfile(dp):
                os.replace(dp, dp + ".bak")
            shutil.copy2(sp, dp)
        except OSError as e:
            return False, f"写入 {name} 失败：{e}"
    return (intel_ready(game.exe_dir),
            "已把 Intel 运行时复制到 exe 目录" if intel_ready(game.exe_dir)
            else "复制完成但校验未通过")


def judge(gpu, game):
    """填 game.verdict / game.reason"""
    has_g = "nvngx_dlssg.dll" in game.dlls
    has_sl = ("sl.interposer.dll" in game.dlls) or ("sl.dlss_g.dll" in game.dlls)
    has_x = "libxess_fg.dll" in game.dlls

    # XMX（1.8 新增）只认「运行中 exe 所在目录」里的 Intel 文件，别处的副本不算
    game.xmx_ready = intel_ready(game.exe_dir)
    gver = game.dlssg_version()
    fam = gpu.family

    if game.arch == "x86":
        game.verdict = V_NO
        game.reason = "32 位游戏 —— 插件只有 64 位版本"
        return
    if not game.exe:
        game.verdict = V_NO
        game.reason = "未找到主程序 exe"
        return
    if fam == "blackwell":
        game.verdict = V_NATIVE
        game.reason = "RTX 50 系原生支持多帧生成，无需解锁"
        return
    if fam == "ada":
        if has_g and gver and gver >= (310, 0):
            game.verdict = V_FULL
            slv = (game.dlls.get("sl.interposer.dll") or {}).get("ver")
            if slv and slv < (2, 10):
                game.reason = ("原生倍率选项可用；Streamline %s 偏旧，实际倍率可能被限制"
                               % version_str(slv))
            else:
                game.reason = "原生倍率选择器可直接出现 3x / 4x / 6x"
            game.reason += ("；XMX 已就绪，面板里选「XeMFG via DLSS-G」延迟更低"
                            if game.xmx_ready
                            else "；XMX 需先在插件面板补 Intel 文件（libxess_fg + libxell）")
            return
        if has_g or has_sl:
            game.verdict = V_NEED_DLL
            if has_g:
                game.reason = "DLSS-G 版本偏旧（%s），需替换为 310.x 再安装" % version_str(gver)
            else:
                game.reason = "游戏未自带 nvngx_dlssg.dll，需先放入 310.x 版本"
            return
        if has_x:
            game.verdict = V_XEMFG
            game.reason = ("无 DLSS-G，走 XeMFG 后端（需面板内装 Intel 运行时）；"
                           "若游戏后续加入 DLSS 帧生成，1.8+ 建议改用 XMX")
            return
        game.verdict = V_NO
        game.reason = "未检测到 DLSS 帧生成组件"
        return
    if fam in ("ampere", "turing"):
        if has_g or has_sl or has_x:
            game.verdict = V_XEMFG
            game.reason = ("20/30 系走 XeMFG 后端（2x–6x），需面板内装 Intel 运行时；"
                           "1.8.3 起也能通过游戏自己的 DLSS-FG 菜单尝试 XMX（实验性）")
        else:
            game.verdict = V_NO
            game.reason = "未检测到 DLSS / XeSS 帧生成组件"
        return
    game.verdict = V_NO
    game.reason = "未检测到可用的 NVIDIA 显卡"


def analyze(game, gpu, cache=None):
    """完整分析一个游戏（带目录 mtime 缓存）"""
    key = os.path.normcase(game.path)
    try:
        mtime = os.path.getmtime(game.path)
    except OSError:
        mtime = 0

    if cache is not None:
        cached = cache.get(key)
        if cached and cached.get("v") != CACHE_VERSION:
            cached = None
        if cached and cached.get("mtime") == mtime and cached.get("exe"):
            game.exe = cached.get("exe", "")
            game.exe_dir = cached.get("exe_dir", "")
            game.arch = cached.get("arch", "")
            game.exe_size = cached.get("exe_size", 0)
            game.dlls = {k: {"path": v.get("path", ""), "ver": tuple(v["ver"]) if v.get("ver") else None}
                         for k, v in cached.get("dlls", {}).items()}
            _finish(game, gpu)
            return game

    exe, size = find_main_exe(game.path)
    game.exe = exe
    game.exe_dir = os.path.dirname(exe) if exe else ""
    game.exe_size = size
    game.arch = pe_machine(exe) if exe else ""
    game.dlls = scan_dlls(game.path)
    _finish(game, gpu)

    if cache is not None:
        cache[key] = {
            "v": CACHE_VERSION,
            "mtime": mtime, "exe": game.exe, "exe_dir": game.exe_dir,
            "arch": game.arch, "exe_size": game.exe_size,
            "dlls": {k: {"path": v["path"], "ver": list(v["ver"]) if v["ver"] else None}
                     for k, v in game.dlls.items()},
        }
    return game


def _finish(game, gpu):
    game.reshade, game.reshade_ver = detect_reshade(game.exe_dir)
    p = game.addon_path
    if p and os.path.isfile(p):
        game.installed_version = file_version(p) or (0, 0, 0, 0)
    else:
        game.installed_version = None
    low = game.name.lower()
    if any(k in low for k in VERIFIED_DLSSG):
        game.verified = "dlssg"
    elif any(k in low for k in VERIFIED_XEMFG):
        game.verified = "xemfg"
    judge(gpu, game)


# --------------------------------------------------------------------------
# GitHub / 下载
# --------------------------------------------------------------------------

def fetch_latest_release(timeout=25):
    req = urllib.request.Request(
        GH_API,
        headers={"User-Agent": f"MFGUnlocker/{APP_VERSION}",
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    asset = None
    for a in data.get("assets", []):
        if a.get("name", "").lower().endswith(".addon64"):
            asset = a
            break
    if not asset:
        raise RuntimeError("release 中未找到 .addon64 资产")
    return Release(
        tag=data.get("tag_name", "?"),
        title=data.get("name") or data.get("tag_name", "?"),
        url=asset["browser_download_url"],
        size=asset.get("size", 0),
        digest=(asset.get("digest") or "").replace("sha256:", ""),
        published=(data.get("published_at") or "")[:10],
    )


# GitHub 在国内直连常常只有十几 KB/s，按实测速度自动挑选源
MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
)

UA = f"MFGUnlocker/{APP_VERSION}"


def _candidate_urls(url):
    return [url] + [m + url for m in MIRRORS]


def _probe_speed(url, timeout=9, sample=262144):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Range": f"bytes=0-{sample - 1}"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if getattr(r, "status", 200) not in (200, 206):
                return 0
            got = 0
            while got < sample:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
        dt = max(time.time() - t0, 0.01)
        return got / dt if got > 16384 else 0
    except Exception:
        return 0


def rank_sources(url):
    """并发测速，返回 [(url, bytes_per_sec), ...] 由快到慢"""
    urls = _candidate_urls(url)
    speeds = [0] * len(urls)
    threads = []
    import threading as _t

    def work(i, u):
        speeds[i] = _probe_speed(u)

    for i, u in enumerate(urls):
        th = _t.Thread(target=work, args=(i, u), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(timeout=12)
    pairs = [(u, s) for u, s in zip(urls, speeds) if s > 0]
    pairs.sort(key=lambda x: -x[1])
    return pairs


def _fetch(url, tmp, expected, progress):
    have = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        status = getattr(r, "status", 200)
        if have and status != 206:
            have = 0  # 服务器不支持续传
        total = int(r.headers.get("Content-Length") or 0) + have
        if not total:
            total = expected
        mode = "ab" if have else "wb"
        done = have
        stall_since = time.time()
        last_report = 0
        with open(tmp, mode) as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if progress and now - last_report > 0.12:
                    progress(done, total)
                    last_report = now
                if now - stall_since > 20:
                    raise RuntimeError("下载停滞")
                stall_since = now
        if progress:
            progress(done, total)


def _fetch_chunked(url, tmp, total, progress, nchunks=6):
    """多连接分段下载（利用 Range 并发提速）"""
    import threading as _t

    if not total:
        raise RuntimeError("未知文件大小")
    part = (total + nchunks - 1) // nchunks
    ranges = []
    for i in range(nchunks):
        s = i * part
        e = min(total - 1, s + part - 1)
        if s <= e:
            ranges.append((i, s, e))

    state = {"done": 0}
    lock = _t.Lock()
    errors = []

    def worker(i, start, end):
        fn = f"{tmp}.{i}"
        try:
            have = os.path.getsize(fn) if os.path.isfile(fn) else 0
            s = start + have
            if s > end:
                with lock:
                    state["done"] += (end - start + 1)
                return
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Range": f"bytes={s}-{end}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if getattr(r, "status", 200) != 206:
                    raise RuntimeError("服务器不支持分段")
                with open(fn, "ab") as f:
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        with lock:
                            state["done"] += len(chunk)
                            d = state["done"]
                        if progress:
                            progress(d, total)
        except Exception as e:
            errors.append(e)

    threads = [_t.Thread(target=worker, args=r, daemon=True) for r in ranges]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if errors:
        raise errors[0]

    with open(tmp, "wb") as out:
        for i, _s, _e in ranges:
            fn = f"{tmp}.{i}"
            with open(fn, "rb") as f:
                shutil.copyfileobj(f, out)
            os.remove(fn)


def _cleanup_parts(tmp, nchunks=6):
    for i in range(nchunks):
        p = f"{tmp}.{i}"
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _fetch_any(url, tmp, expected, progress):
    try:
        _fetch_chunked(url, tmp, expected, progress)
        return
    except Exception:
        _cleanup_parts(tmp)
    _fetch(url, tmp, expected, progress)


def download_release(rel, progress=None):
    """下载到缓存，返回本地路径。progress(done, total)"""
    cache_dir = os.path.join(CACHE_DIR, rel.tag)
    os.makedirs(cache_dir, exist_ok=True)
    dst = os.path.join(cache_dir, ADDON_NAME)
    if os.path.isfile(dst) and (not rel.size or os.path.getsize(dst) == rel.size):
        return dst

    tmp = dst + ".part"
    ranked = rank_sources(rel.url)
    if not ranked:
        raise RuntimeError("所有下载源都不可达，请检查网络或改用「本地安装」")
    last_err = None
    for url, _speed in ranked:
        try:
            _fetch_any(url, tmp, rel.size, progress)
            if rel.size and os.path.getsize(tmp) != rel.size:
                raise RuntimeError("文件大小不符")
            if rel.digest:
                if sha256_of(tmp) != rel.digest:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise RuntimeError("校验失败（该源文件被改动）")
            os.replace(tmp, dst)
            return dst
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"下载失败：{last_err}")


# --------------------------------------------------------------------------
# 安装 / 卸载
# --------------------------------------------------------------------------

def _backup_dir_for(game):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r'[<>:"/\\|?*]', "_", game.name)[:40]
    d = os.path.join(BACKUP_DIR, f"{stamp}_{safe}")
    os.makedirs(d, exist_ok=True)
    return d


def install(game, src_addon):
    """复制 addon 到游戏 exe 目录，返回 (ok, message, backup_dir)"""
    if not game.exe_dir or not os.path.isdir(game.exe_dir):
        return False, "游戏主程序目录不存在", ""
    dst = os.path.join(game.exe_dir, ADDON_NAME)
    backup = ""
    if os.path.isfile(dst):
        backup = _backup_dir_for(game)
        shutil.copy2(dst, os.path.join(backup, ADDON_NAME))
    shutil.copy2(src_addon, dst)
    game.installed_version = file_version(dst) or (0, 0, 0, 0)
    msg = f"已安装到 {game.exe_dir}"
    if not game.reshade:
        msg += "\n\n注意：未检测到 ReShade（add-on 版），插件暂时不会加载。"
    return True, msg, backup


def uninstall(game):
    """把插件文件移入备份目录（不硬删），返回 (ok, message)"""
    if not game.exe_dir:
        return False, "未找到游戏目录"
    existing = [n for n in MOD_FILES if os.path.isfile(os.path.join(game.exe_dir, n))]
    if not existing:
        game.installed_version = None
        return False, "该目录下没有找到解锁插件文件"
    backup = _backup_dir_for(game)
    moved = []
    for n in existing:
        try:
            shutil.move(os.path.join(game.exe_dir, n), os.path.join(backup, n))
            moved.append(n)
        except Exception as e:
            return False, f"移动 {n} 失败：{e}"
    game.installed_version = None
    return True, "已卸载 %d 个文件，备份位于：\n%s" % (len(moved), backup)


def open_folder(path):
    """打开资源管理器，并让它到前台"""
    if not path or not os.path.exists(path):
        return
    try:
        # 允许接下来启动的进程获得前台权限，否则窗口会在后台
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        pass
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, None, 1)
    except Exception:
        try:
            os.startfile(path)  # noqa: S606
        except Exception:
            pass
