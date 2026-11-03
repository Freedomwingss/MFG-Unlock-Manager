#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mfg_web.py — MFG Unlock Manager 的本地 Web 服务
启动后开一个 127.0.0.1 的 HTTP 服务，用浏览器界面操作。
"""

import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import mfg_core as core
import mfg_dlssg
import mfg_icons
import mfg_names
import mfg_reshade as reshade

VERDICT_TEXT = {
    core.V_FULL: "支持 · 原生 3–6×",
    core.V_NEED_DLL: "需替换 DLSS-G",
    core.V_XEMFG: "仅 XeMFG 后端",
    core.V_NATIVE: "50 系原生支持",
    core.V_NO: "不支持",
}

APP_DATA = core.APP_DATA


BROWSERS = [
    (r"C://Program Files (x86)//Microsoft//Edge//Application//msedge.exe", "Edge"),
    (r"C://Program Files//Microsoft//Edge//Application//msedge.exe", "Edge"),
    (r"C://Program Files//Google//Chrome//Application//chrome.exe", "Chrome"),
    (r"C://Program Files (x86)//Google//Chrome//Application//chrome.exe", "Chrome"),
]


def find_browser():
    for path, name in BROWSERS:
        if os.path.isfile(path):
            return path, name
    return None, ""


def open_shell(url, as_app=True):
    """优先用内置 WebView2 宿主（无边框 + Mica）；否则退回浏览器 --app 窗口"""
    use_host = os.environ.get("MFG_HOST", "") == "1"
    if as_app and use_host:
        host = resource_path(os.path.join("webhost", "MFGWebHost.exe"))
        if os.path.isfile(host):
            try:
                proc = subprocess.Popen([host, url], cwd=os.path.dirname(host))
                # 窗口关掉后，本地服务一起退出
                def _wait():
                    proc.wait()
                    ST.exiting = True
                    os._exit(0)
                threading.Thread(target=_wait, daemon=True).start()
                return "内置窗口"
            except Exception:
                pass

    exe, name = find_browser()
    if exe and as_app:
        try:
            # 不指定 profile：复用用户默认配置文件，最稳
            subprocess.Popen([exe, f"--app={url}",
                              "--window-size=1500,940", "--window-position=120,60",
                              "--no-first-run", "--no-default-browser-check"],
                             creationflags=0x08000000)

            # 兜底：4 秒内页面没发请求，说明 app 窗口没起来，改用默认浏览器
            stamp = ST.last_seen

            def _fallback():
                time.sleep(4)
                if ST.last_seen == stamp:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        pass

            threading.Thread(target=_fallback, daemon=True).start()
            return f"{name} 应用窗口"
        except Exception:
            pass
    webbrowser.open(url)
    return "浏览器"


def watchdog():
    """窗口关闭后自动退出。

    阈值放到 75 秒：浏览器会把后台标签页的定时器节流到约每分钟一次，
    阈值太小会被误判成"窗口已关"而把服务杀掉（表现为点按钮 Failed to fetch）。
    """
    boot = time.time()
    while not ST.exiting:
        time.sleep(5)
        if getattr(ST, "native_shell", False):
            continue          # 自己是窗口，关掉窗口进程自然结束，不用心跳判断
        idle = time.time() - ST.last_seen
        if time.time() - boot > 120 and idle > 75:
            ST.exiting = True
            os._exit(0)


NVIDIA_API = ("https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/"
              "services/AjaxDriverService.php?func=DriverManualLookup"
              "&psid={psid}&osID={osid}&languageCode=1033&isWHQL=1&dch=1"
              "&sort1=0&numberOfResults=1")

# GeForce 产品系列 ID（NVIDIA 驱动查询接口用）
SERIES_PSID = (("rtx 50", 130), ("rtx 40", 129), ("rtx 30", 128),
               ("rtx 20", 127), ("gtx 16", 126), ("gtx 10", 101))
_driver_cache = {"ts": 0, "ver": ""}


def _os_id():
    """NVIDIA 接口的 osID：57=Win10 64 位，135=Win11"""
    try:
        return 135 if sys.getwindowsversion().build >= 22000 else 57
    except Exception:
        return 57


def _psid(gpu_name):
    low = (gpu_name or "").lower()
    for key, pid in SERIES_PSID:
        if key in low:
            return pid
    return 129


def latest_driver(timeout=20):
    """NVIDIA 官方接口查 GeForce 最新驱动版本（缓存 1 小时）"""
    now = time.time()
    if _driver_cache["ver"] and now - _driver_cache["ts"] < 3600:
        return _driver_cache["ver"]
    url = NVIDIA_API.format(psid=_psid(ST.gpu.name), osid=_os_id())
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
                           capture_output=True, timeout=timeout + 5,
                           creationflags=0x08000000)
        data = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        ids = data.get("IDS") or []
        ver = ids[0]["downloadInfo"].get("Version", "") if ids else ""
    except Exception:
        ver = ""
    if ver:
        _driver_cache["ver"] = ver
        _driver_cache["ts"] = now
    return ver


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def vstr(v):
    return core.version_str(v) if v else ""


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.games = []
        self.by_path = {}
        self.names = {}
        self.scanning = False
        self.scanned = 0
        self.total = 0
        self.gpu = core.GPU()
        self.reshade_ver = ""
        self.plugin = None
        self.dlssg = None
        self.tier = 1.0
        self.progress = {"kind": "", "done": 0, "total": 0, "text": "", "busy": False}
        self.last_seen = time.time()
        self.exiting = False
        self.cfg = core.load_config()

    # ---------- 进度 ----------
    def set_progress(self, kind="", text="", done=0, total=0, busy=False):
        with self.lock:
            self.progress = {"kind": kind, "text": text, "done": done,
                             "total": total, "busy": busy}

    def push_progress(self, done, total):
        with self.lock:
            self.progress["done"] = done
            self.progress["total"] = total

    # ---------- 序列化 ----------
    def game_json(self, g):
        latest = self.plugin.tag if self.plugin else ""
        cur = vstr(g.installed_version)
        up_to_date = bool(g.installed and latest and cur == latest)
        return {
            "latest": latest,
            "upToDate": up_to_date,
            "xmxReady": bool(g.xmx_ready),
            "path": g.path,
            "dir": g.name,
            "name": self.names.get(g.path) or mfg_names.zh_name(g.name),
            "verdict": g.verdict,
            "verdictText": VERDICT_TEXT.get(g.verdict, "—"),
            "reason": g.reason,
            "exe": g.exe,
            "exeDir": g.exe_dir,
            "arch": g.arch,
            "dlssg": vstr(g.dlls.get("nvngx_dlssg.dll", {}).get("ver")),
            "sl": vstr((g.dlls.get("sl.interposer.dll") or {}).get("ver")),
            "xess": vstr((g.dlls.get("libxess_fg.dll") or {}).get("ver")),
            "reshade": bool(g.reshade),
            "reshadeVer": vstr(g.reshade_ver),
            "installed": bool(g.installed),
            "installedVer": vstr(g.installed_version),
            "verified": g.verified,
            "icon": bool(g.exe),
        }

    def state_json(self):
        with self.lock:
            return {
                "gpu": {"name": self.gpu.name, "driver": self.gpu.driver,
                        "family": self.gpu.family},
                "reshadeVer": self.reshade_ver,
                "plugin": self.plugin.tag if self.plugin else "",
                "pluginDate": self.plugin.published if self.plugin else "",
                "dlssgVer": self.dlssg.get("version", "") if self.dlssg else "",
                "tier": self.tier,
                "mfgOutdated": self._mfg_outdated(),
                "reshadeOutdated": self._reshade_outdated(),
                "driverLatest": latest_driver(),
                "libraries": self.cfg.get("libraries", []),
                "scanning": self.scanning,
                "scanned": self.scanned,
                "total": self.total,
                "progress": self.progress,
                "version": core.APP_VERSION,
            }

    def _mfg_current(self):
        vers = [vstr(g.installed_version) for g in self.games if g.installed]
        if not vers:
            return ""
        return max(vers, key=lambda v: [int(x) for x in v.split(".") if x.isdigit()] or [0])

    def _reshade_current(self):
        for g in self.games:
            if g.reshade and g.reshade_ver:
                return vstr(g.reshade_ver)
        return ""

    def _mfg_outdated(self):
        latest = self.plugin.tag if self.plugin else ""
        if not latest:
            return False
        for g in self.games:
            if g.installed and vstr(g.installed_version) != latest:
                return True
        return False

    def _reshade_outdated(self):
        latest = self.reshade_ver
        if not latest:
            return True
        for g in self.games:
            if g.reshade:
                if vstr(g.reshade_ver) != latest:
                    return True
        return False

    def games_json(self):
        with self.lock:
            return {"scanning": self.scanning, "scanned": self.scanned,
                    "total": self.total,
                    "items": [self.game_json(g) for g in self.games]}


ST = State()


# --------------------------------------------------------------------------
# 后台任务
# --------------------------------------------------------------------------

def _retry(fn, n=2, delay=1.0):
    for i in range(n):
        try:
            return fn()
        except Exception:
            if i == n - 1:
                return None
            time.sleep(delay)


WEBVIEW_JUNK = (
    "Crashpad", "component_crx_cache", "WidevineCdm", "Subresource Filter",
    "GrShaderCache", "ShaderCache", "Speech Recognition", "hyphen-data",
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "Default/DawnWebGPUCache", "Default/DawnGraphiteCache",
    "Default/Service Worker/CacheStorage", "Default/Service Worker/ScriptCache",
)


def prune_storage(keep_backups=2):
    """启动时清掉过期缓存，把占用压住（插件包 / 备份 / WebView2 缓存）"""
    freed = 0

    def size_of(path):
        total = 0
        for r, _d, fs in os.walk(path):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(r, f))
                except OSError:
                    pass
        return total

    # ① 插件包缓存只留最新版本
    try:
        d = os.path.join(core.APP_DATA, "cache")
        vers = sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
        for v in vers[:-1]:
            p_ = os.path.join(d, v)
            freed += size_of(p_)
            shutil.rmtree(p_, ignore_errors=True)
    except OSError:
        pass

    # ② 备份：每个游戏只留最近 keep_backups 份（目录名形如 20261103-151505_PBZ）
    try:
        d = os.path.join(core.APP_DATA, "backup")
        groups = {}
        for it in os.listdir(d):
            full = os.path.join(d, it)
            if not os.path.isdir(full):
                continue
            game = it.split("_", 1)[1] if "_" in it else it
            groups.setdefault(game, []).append(it)
        for _game, lst in groups.items():
            lst.sort()
            for it in lst[:-keep_backups] if len(lst) > keep_backups else []:
                p_ = os.path.join(d, it)
                freed += size_of(p_)
                shutil.rmtree(p_, ignore_errors=True)
    except OSError:
        pass

    # ③ ReShade 安装包：dll 已解出来就不用留着那个 exe 了
    try:
        d = os.path.join(core.APP_DATA, "reshade")
        for f in os.listdir(d):
            if f.lower().endswith(".exe"):
                p_ = os.path.join(d, f)
                try:
                    freed += os.path.getsize(p_)
                    os.remove(p_)
                except OSError:
                    pass
    except OSError:
        pass

    # ④ WebView2 的崩溃报告 / 组件 / 着色器缓存
    base = os.path.join(core.APP_DATA, "webview", "EBWebView")
    for t in WEBVIEW_JUNK:
        p_ = os.path.join(base, t)
        if os.path.isdir(p_):
            freed += size_of(p_)
            shutil.rmtree(p_, ignore_errors=True)

    if freed > 8 * 1048576:
        try:
            with open(os.path.join(core.APP_DATA, "shell.log"), "a",
                      encoding="utf-8") as f:
                f.write("%s pruned %.1f MB\n"
                        % (time.strftime("%m-%d %H:%M:%S"), freed / 1048576.0))
        except OSError:
            pass
    return freed


def boot_env():
    try:
        prune_storage()
    except Exception:
        pass
    ST.gpu = core.detect_gpu()
    for _ in range(4):          # nvidia-smi 偶尔第一次读不出来，重试几次
        if ST.gpu.family in ("ada", "ampere", "turing", "blackwell"):
            break
        time.sleep(1.2)
        ST.gpu = core.detect_gpu()
    ST.plugin = _retry(core.fetch_latest_release) or ST.plugin
    ST.reshade_ver = _retry(reshade.latest_version, 2) or ST.reshade_ver
    ST.dlssg = _retry(mfg_dlssg.latest, 2) or ST.dlssg
    latest_driver()

    # 只在装好后第一次启动时扫盘；之后直接读上次的快照，保护硬盘也更快
    snap = ST.cfg.get("snapshot")
    if snap and ST.cfg.get("snapshot_gpu") and ST.cfg.get("snapshot_gpu") != ST.gpu.family:
        snap = None            # 换了显卡 / 上次没读到显卡，快照不可信
    if snap:
        games = _restore(snap)
        with ST.lock:
            ST.games = games
            ST.by_path = {g.path: g for g in games}
            ST.names = {g.path: mfg_names.zh_name(g.name) for g in games}
            ST.scanned = len(games)
            ST.total = len(games)
        ST.set_progress("", f"就绪 · {len(games)} 个游戏（上次扫描结果）", 0, 0, False)
    else:
        do_scan()


def _snapshot():
    out = []
    for g in ST.games:
        out.append({
            "name": g.name, "path": g.path, "library": g.library, "appid": g.appid,
            "exe": g.exe, "exe_dir": g.exe_dir, "arch": g.arch, "exe_size": g.exe_size,
            "verdict": g.verdict, "reason": g.reason, "verified": g.verified,
            "xmx_ready": g.xmx_ready,
            "reshade": g.reshade,
            "dlls": {k: {"path": v.get("path", ""),
                         "ver": list(v["ver"]) if v.get("ver") else None}
                     for k, v in g.dlls.items()},
            "reshade_ver": list(g.reshade_ver) if g.reshade_ver else None,
            "installed_version": list(g.installed_version) if g.installed_version else None,
        })
    return out


def _restore(snap):
    """用上次的快照重建列表，完全不读硬盘"""
    games = []
    for d in snap:
        g = core.Game(name=d.get("name", ""), path=d.get("path", ""),
                      library=d.get("library", ""))
        g.appid = d.get("appid", "")
        g.exe = d.get("exe", "")
        g.exe_dir = d.get("exe_dir", "")
        g.arch = d.get("arch", "")
        g.exe_size = d.get("exe_size", 0)
        g.verdict = d.get("verdict", "no")
        g.reason = d.get("reason", "")
        g.verified = d.get("verified", "")
        g.xmx_ready = bool(d.get("xmx_ready"))
        g.reshade = bool(d.get("reshade"))
        g.dlls = {k: {"path": v.get("path", ""),
                      "ver": tuple(v["ver"]) if v.get("ver") else None}
                  for k, v in (d.get("dlls") or {}).items()}
        g.reshade_ver = tuple(d["reshade_ver"]) if d.get("reshade_ver") else None
        g.installed_version = (tuple(d["installed_version"])
                               if d.get("installed_version") else None)
        if os.path.isdir(g.path):
            games.append(g)
    return games


GPU_OK = ("ada", "ampere", "turing", "blackwell")


def do_scan():
    # boot_env 是后台线程，扫描可能先到；等它把显卡读完
    for _ in range(24):
        if ST.gpu.family in GPU_OK:
            break
        time.sleep(0.5)
    else:
        ST.set_progress("error", "显卡还没就绪，稍后再扫描", 0, 0, False)
        return
    with ST.lock:
        if ST.scanning:
            return
        ST.scanning = True
        ST.games = []
        ST.by_path = {}
        ST.names = {}
        ST.scanned = 0
    ST.set_progress("scan", "正在枚举游戏库…", 0, 0, True)

    try:
        cache = ST.cfg.get("cache", {})
        games = core.enumerate_games(ST.cfg.get("libraries", []))
        with ST.lock:
            ST.total = len(games)
        for i, g in enumerate(games, 1):
            try:
                core.analyze(g, ST.gpu, cache)
            except Exception as e:
                g.reason = f"分析失败：{e}"
            name = mfg_names.zh_name(g.name)
            with ST.lock:
                ST.games.append(g)
                ST.by_path[g.path] = g
                ST.names[g.path] = name
                ST.scanned = i
            if g.exe:
                try:
                    mfg_icons.icon_png(g.exe, 32)
                except Exception:
                    pass
        # 手动添加的游戏也一并纳入
        for mp in ST.cfg.get("manual", []):
            if not os.path.isdir(mp):
                continue
            try:
                mg = core.Game(name=os.path.basename(mp), path=mp, library="手动")
                core.analyze(mg, ST.gpu, cache)
                with ST.lock:
                    ST.games.append(mg)
                    ST.by_path[mg.path] = mg
                    ST.names[mg.path] = mfg_names.zh_name(mg.name)
            except Exception:
                pass

        ST.cfg["cache"] = cache
        ST.cfg["snapshot"] = _snapshot()
        ST.cfg["snapshot_gpu"] = ST.gpu.family
        core.save_config(ST.cfg)
    except Exception:
        traceback.print_exc()
    finally:
        with ST.lock:
            ST.scanning = False
        ST.set_progress("", "", 0, 0, False)


def job_install(path, with_reshade):
    g = ST.by_path.get(path)
    if not g:
        ST.set_progress("error", "找不到该游戏", 0, 0, False)
        return
    notes = []
    try:
        # ① 需要换 DLL 的游戏，先自动把 DLSS-G 升到 310.x
        need_dlssg = (g.verdict == core.V_NEED_DLL)
        if not need_dlssg:
            dg = g.dlls.get("nvngx_dlssg.dll")
            need_dlssg = bool(dg and dg.get("ver") and dg["ver"] < (310, 0))
        if need_dlssg:
            ST.set_progress("dlssg", "正在更新 DLSS-G…", 0, 0, True)
            try:
                entry = ST.dlssg or mfg_dlssg.latest()
                ST.dlssg = entry
                ok, msg = mfg_dlssg.install(g, entry, ST.push_progress)
                if ok:
                    notes.append(msg)
                    g.dlls = core.scan_dlls(g.path)
                    core.judge(ST.gpu, g)
                else:
                    notes.append("DLSS-G 更新失败：" + msg)
            except Exception as e:
                notes.append(f"DLSS-G 更新失败：{e}")

        if not g.reshade:
            ST.set_progress("reshade", "正在部署 ReShade 宿主…", 0, 0, True)
            rv = ST.reshade_ver or reshade.latest_version()
            ST.reshade_ver = rv
            ok, msg = reshade.deploy(g.exe_dir, g.exe, rv, ST.push_progress)
            if not ok:
                ST.set_progress("error", msg, 0, 0, False)
                return
            g.reshade = True
            try:
                g.reshade_ver = tuple(int(x) for x in rv.split("."))
            except Exception:
                g.reshade_ver = None
            notes.append(msg)

        ST.set_progress("plugin", "正在下载解锁插件…", 0, 0, True)
        rel = ST.plugin or core.fetch_latest_release()
        ST.plugin = rel
        src = core.download_release(rel, ST.push_progress)
        ok, msg, _ = core.install(g, src)
        if not ok:
            ST.set_progress("error", msg, 0, 0, False)
            return
        notes.append(msg)

        # XMX 需要 exe 目录里有 Intel 运行时；游戏里别处有合适版本就顺手搬过来
        if g.verdict == core.V_FULL and not core.intel_ready(g.exe_dir):
            ST.set_progress("intel", "正在补齐 Intel 运行时…", 0, 0, True)
            ok_i, msg_i = core.place_intel_files(g)
            notes.append("Intel 运行时：" + msg_i)
            g.xmx_ready = core.intel_ready(g.exe_dir)

        ST.set_progress("done", "安装完成", 0, 0, False)
        with ST.lock:
            ST.last_msg = "\n\n".join(notes)
    except Exception as e:
        ST.set_progress("error", f"安装失败：{e}", 0, 0, False)


def job_uninstall(path):
    g = ST.by_path.get(path)
    if not g:
        ST.set_progress("error", "找不到该游戏", 0, 0, False)
        return
    notes = []
    ok, msg = core.uninstall(g)
    if msg:
        notes.append(msg)
    if g.reshade:
        rok, rmsg = reshade.uninstall(g.exe_dir)
        if rok:
            g.reshade = False
            g.reshade_ver = None
            notes.append(rmsg)
    ST.set_progress("done" if ok else "error", "\n".join(notes) or "没有可卸载的内容", 0, 0, False)


def job_reshade(path, action):
    g = ST.by_path.get(path)
    if not g:
        ST.set_progress("error", "找不到该游戏", 0, 0, False)
        return
    if action == "remove":
        ok, msg = reshade.uninstall(g.exe_dir)
        if ok:
            g.reshade = False
            g.reshade_ver = None
        ST.set_progress("done" if ok else "error", msg, 0, 0, False)
        return
    try:
        ST.set_progress("reshade", "正在部署 ReShade 宿主…", 0, 0, True)
        rv = ST.reshade_ver or reshade.latest_version()
        ST.reshade_ver = rv
        ok, msg = reshade.deploy(g.exe_dir, g.exe, rv, ST.push_progress)
        if ok:
            g.reshade = True
            try:
                g.reshade_ver = tuple(int(x) for x in rv.split("."))
            except Exception:
                g.reshade_ver = None
            # 重新扫描 dll 状态
            g.dlls = core.scan_dlls(g.path)
            core.judge(ST.gpu, g)
        ST.set_progress("done" if ok else "error", msg, 0, 0, False)
    except Exception as e:
        ST.set_progress("error", f"ReShade 安装失败：{e}", 0, 0, False)


def job_dlssg(path):
    g = ST.by_path.get(path)
    if not g:
        ST.set_progress("error", "找不到该游戏", 0, 0, False)
        return
    try:
        ST.set_progress("dlssg", "正在获取 DLSS-G 最新版…", 0, 0, True)
        entry = mfg_dlssg.latest()
        ST.dlssg = entry
        ok, msg = mfg_dlssg.install(g, entry, ST.push_progress)
        if ok:
            g.dlls = core.scan_dlls(g.path)
            core.judge(ST.gpu, g)
        ST.set_progress("done" if ok else "error", msg, 0, 0, False)
    except Exception as e:
        ST.set_progress("error", f"DLSS-G 更新失败：{e}", 0, 0, False)


def job_local_install(path, src):
    g = ST.by_path.get(path)
    if not g:
        ST.set_progress("error", "找不到该游戏", 0, 0, False)
        return
    try:
        ok, msg, _ = core.install(g, src)
        ST.set_progress("done" if ok else "error", msg, 0, 0, False)
    except Exception as e:
        ST.set_progress("error", f"安装失败：{e}", 0, 0, False)


APP_TITLE = "多帧生成解锁管理器"


def app_hwnd():
    """本程序窗口句柄；对话框挂在它上面，否则会缩到任务栏"""
    try:
        import ctypes
        from ctypes import wintypes as wt

        u = ctypes.windll.user32
        found = []

        def cb(h, l):
            n = u.GetWindowTextLengthW(h)
            if n and u.IsWindowVisible(h):
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value == APP_TITLE:
                    found.append(h)
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        return found[0] if found else None
    except Exception:
        return None


def _guid(text):
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(text)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def pick_folder(title="选择游戏目录（含 exe 的那一层）"):
    return _ifile_dialog(title, folders=True)


def pick_file(title="选择 renodx-mfgunlock.addon64"):
    return _ifile_dialog(title, folders=False)


def _ifile_dialog(title="选择", folders=True):
    """Vista+ 现代选择框（IFileOpenDialog），挂在主窗口上并置前"""
    try:
        import ctypes
        from ctypes import POINTER, byref, c_void_p, c_wchar_p, c_ulong

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)

        clsid = _guid("{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}")
        iid = _guid("{d57c7288-d4ad-4768-be02-9d969532d960}")
        pfd = c_void_p()
        if ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid), byref(pfd)) != 0:
            return ""

        def method(ptr, idx, restype, *argtypes):
            vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
            return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[idx])

        release = method(pfd, 2, ctypes.c_ulong)
        FOS_PICKFOLDERS = 0x00000020
        FOS_FORCEFILESYSTEM = 0x00000040
        FOS_PATHMUSTEXIST = 0x00000800
        FOS_FILEMUSTEXIST = 0x00001000

        set_options = method(pfd, 9, ctypes.c_long, c_ulong)
        get_options = method(pfd, 10, ctypes.c_long, POINTER(c_ulong))
        set_title = method(pfd, 17, ctypes.c_long, c_wchar_p)
        show = method(pfd, 3, ctypes.c_long, c_void_p)
        get_result = method(pfd, 20, ctypes.c_long, POINTER(c_void_p))

        flags = FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
        flags |= FOS_PICKFOLDERS if folders else FOS_FILEMUSTEXIST
        opts = c_ulong()
        if get_options(pfd, byref(opts)) == 0:
            set_options(pfd, opts.value | flags)
        set_title(pfd, title)

        owner = app_hwnd()
        if owner:
            ctypes.windll.user32.SetForegroundWindow(owner)

        if show(pfd, owner) != 0:
            release(pfd)
            return ""

        item = c_void_p()
        if get_result(pfd, byref(item)) != 0:
            release(pfd)
            return ""

        SIGDN_FILESYSPATH = 0x80058000
        get_name = method(item, 5, ctypes.c_long, c_ulong, POINTER(ctypes.c_void_p))
        psz = ctypes.c_void_p()
        path = ""
        if get_name(item, SIGDN_FILESYSPATH, byref(psz)) == 0 and psz.value:
            path = ctypes.wstring_at(psz)
            ctypes.windll.ole32.CoTaskMemFree(psz)
        method(item, 2, ctypes.c_ulong)(item)
        release(pfd)
        return path
    except Exception:
        return ""


def add_manual_game(path):
    """把任意目录加入列表"""
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return None, "目录不存在"
    exe, _sz = core.find_main_exe(path)
    if not exe:
        return None, "这个目录里没找到 .exe，请选游戏 exe 所在的那一层"

    g = core.Game(name=os.path.basename(path), path=path, library="手动")
    try:
        core.analyze(g, ST.gpu, ST.cfg.get("cache"))
    except Exception as e:
        return None, f"分析失败：{e}"

    with ST.lock:
        if ST.by_path.get(g.path):
            ST.games = [x for x in ST.games if x.path != g.path]
        ST.games.append(g)
        ST.by_path[g.path] = g
        ST.names[g.path] = mfg_names.zh_name(g.name)
    manual = ST.cfg.setdefault("manual", [])
    if g.path not in manual:
        manual.append(g.path)
        core.save_config(ST.cfg)
    return g, ""


def _desktop():
    for cand in (os.path.join(os.path.expanduser("~"), "Desktop"),
                 os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                 os.path.join(os.environ.get("USERPROFILE", ""), "桌面")):
        if cand and os.path.isdir(cand):
            return cand
    return os.path.expanduser("~")


def _tail(path, n=15):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).rstrip()
    except Exception:
        return ""


def _reshade_logs(g):
    """返回 [(显示名, 路径)]，含游戏目录与全局位置"""
    out = []
    exe_dir = g.exe_dir or ""
    for name in ("ReShade.log", "ReShade64.log"):
        p = os.path.join(exe_dir, name)
        if os.path.isfile(p):
            out.append((name, p))
    gdir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ReShade")
    if os.path.isdir(gdir):
        for f in os.listdir(gdir):
            if f.lower().endswith(".log"):
                out.append(("全局 ReShade/" + f, os.path.join(gdir, f)))
    return out


def _parse_reshade_log(text, addon_name):
    """从 ReShade 日志提取注入 / 插件加载 / 错误行"""
    import re as _re

    info = {"init": None, "addon_loaded": None, "errors": [], "addons": []}
    for line in text.splitlines():
        low = line.lower()
        if "initialized" in low or "initializing" in low:
            info["init"] = True
        if "add-on" in low or "addon" in low:
            if "loading" in low or "loaded" in low or "register" in low:
                info["addons"].append(line.strip())
                if addon_name.lower() in low:
                    info["addon_loaded"] = True
        if any(k in low for k in ("failed", "error", "exception", "could not")):
            info["errors"].append(line.strip()[:200])
    if info["addon_loaded"] is None and info["addons"]:
        info["addon_loaded"] = False
    return info


def _game_crashes(g):
    """UE 等引擎的崩溃目录与日志文件"""
    hits = []
    for sub in ("Saved/Crashes", "Saved/Logs", "CrashDumps"):
        d = os.path.join(g.path, sub)
        if not os.path.isdir(d):
            continue
        try:
            items = sorted(os.listdir(d), reverse=True)[:5]
        except OSError:
            continue
        for it in items:
            fp = os.path.join(d, it)
            try:
                t = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(fp)))
            except OSError:
                t = ""
            hits.append((sub + "/" + it, t))
    return hits


def _recent_events(keywords, hours=72):
    """Windows 应用错误日志中与游戏相关的条目（失败则返回空）"""
    try:
        pat = "|".join(k for k in keywords if k)
        if not pat:
            return []
        ps = (
            "$s=(Get-Date).AddHours(-%d);"
            "Get-WinEvent -FilterHashtable @{LogName='Application';Level=1,2;StartTime=$s} "
            "-MaxEvents 40 -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Message -match '%s' } | "
            "Select-Object -First 6 | ForEach-Object { "
            "$_.TimeCreated.ToString('MM-dd HH:mm') + '  ' + "
            "(($_.Message -split [char]10)[0]).Trim() }"
        ) % (hours, pat.replace("'", ""))
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "Windows" + "PowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(exe):
            exe = "powershell"
        r = subprocess.run([exe, "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=25,
                           creationflags=0x08000000)
        out = r.stdout.decode("utf-8", "ignore").strip()
        return [x.strip() for x in out.splitlines() if x.strip()]
    except Exception:
        return []


def mfg_dlssg_safe():
    """报告里现场再取一次 DLSS-G 版本，失败返回空"""
    e = _retry(mfg_dlssg.latest, 2)
    if e:
        ST.dlssg = e
        return e.get("version", "")
    return ""


def build_report(game):
    """针对单个游戏生成运行诊断，固定文件名，覆盖上一份"""
    L = []
    add = L.append
    name = ST.names.get(game.path, game.name)

    logs = _reshade_logs(game)
    parsed = []
    for label, lp in logs:
        try:
            txt = open(lp, "r", encoding="utf-8", errors="ignore").read()
            mt = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(lp)))
        except OSError:
            continue
        parsed.append((label, mt, txt, _parse_reshade_log(txt, core.ADDON_NAME)))

    addon = os.path.join(game.exe_dir or "", core.ADDON_NAME)
    has_addon = os.path.isfile(addon)
    addon_ver = core.version_str(core.file_version(addon)) if has_addon else ""
    latest_tag = ST.plugin.tag if ST.plugin else ""
    proxy, rs_ver = reshade.find_deployed(game.exe_dir or "")
    dg = game.dlls.get("nvngx_dlssg.dll")
    crashes = _game_crashes(game)

    # ---- 结论 ----
    verdict_lines = []
    if not parsed:
        verdict_lines.append("未检测到运行记录 —— 装完后还没启动过这款游戏。")
        verdict_lines.append("进游戏跑一次再生成，才能看到注入与插件加载结果。")
        mark = "[无记录]"
    else:
        label, mt, txt, info = parsed[0]
        if info["addon_loaded"]:
            if info["errors"]:
                mark = "[有告警]"
                verdict_lines.append(f"插件已加载，但日志里有 {len(info['errors'])} 条错误/警告，见下方明细。")
            else:
                mark = "[正常]"
                verdict_lines.append("插件已加载，日志无报错，运行正常。")
        elif info["init"]:
            mark = "[异常]"
            verdict_lines.append("ReShade 已注入，但没有加载本插件。")
            if not has_addon:
                verdict_lines.append("原因：游戏目录里没有 " + core.ADDON_NAME + "，插件文件不存在。")
            else:
                verdict_lines.append("可能原因：插件版本与 ReShade 不匹配、被 [ADDON] 段禁用、或加载时抛错（见下方错误行）。")
        else:
            mark = "[异常]"
            verdict_lines.append("ReShade 似乎没有成功注入（日志里没有 Initialized）。")
            verdict_lines.append("检查：代理 dll 是否被同名文件替换、是否被其他 overlay 工具占用。")
        if crashes:
            verdict_lines.append(f"另发现 {len(crashes)} 条崩溃记录，时间见下方。")

    add("=" * 64)
    add(f"  MFG 运行诊断 — {name}   {mark}")
    add("  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 64)
    add("")
    add("[结论]")
    for line in verdict_lines:
        add("  " + line)
    add("")

    add("[游戏]")
    add(f"  名称        {name}")
    add(f"  目录        {game.path}")
    if game.exe:
        try:
            rel = os.path.relpath(game.exe, game.path)
        except ValueError:
            rel = game.exe
        size = core.human_size(game.exe_size) if game.exe_size else "-"
        add(f"  主程序      {rel}   ({game.arch or '?'}, {size})")
    else:
        add("  主程序      未找到")
    add(f"  判定        {VERDICT_TEXT.get(game.verdict, game.verdict)}")
    add(f"  依据        {game.reason}")
    add("")

    add("[运行记录]")
    if parsed:
        for label, mt, txt, info in parsed:
            add(f"  {label}   （{mt} 更新，{len(txt.splitlines())} 行）")
            add(f"    注入      {'成功' if info['init'] else '未确认'}")
            if info["addons"]:
                for a in info["addons"][:6]:
                    add(f"    插件      {a}")
            else:
                add("    插件      日志中没有 add-on 加载记录")
            if info["errors"]:
                add(f"    错误      {len(info['errors'])} 条")
                for e in info["errors"][:10]:
                    add("      " + e)
            else:
                add("    错误      无")
            add("")
    else:
        add("  没有任何 ReShade 日志。可能原因：")
        add("    · 装完后还没启动过游戏")
        add("    · 游戏用 Vulkan，ReShade 不是以代理 dll 方式注入的")
        add("    · 日志被关掉了（ReShade.ini 里 NoDebugInfo=1 只影响调试信息，不影响日志）")
        add("")

    add("[部署状态]")
    if proxy:
        add(f"  ReShade     {os.path.basename(proxy)}  v{vstr(rs_ver) or '?'}")
    else:
        add("  ReShade     未部署（游戏目录没有代理 dll）")
    ini = os.path.join(game.exe_dir or "", "ReShade.ini")
    if os.path.isfile(ini):
        add("  ReShade.ini")
        for line in _tail(ini, 14).splitlines():
            add("    " + line)
        try:
            itxt = open(ini, "r", encoding="utf-8", errors="ignore").read().lower()
            if "disabledaddons" in itxt and core.ADDON_NAME.lower() in itxt:
                add("    ⚠ 配置里可能禁用了本插件，检查 [ADDON] 段的 DisabledAddons")
        except OSError:
            pass
    else:
        add("  ReShade.ini  不存在")
    if has_addon:
        note = ""
        if latest_tag and addon_ver != latest_tag:
            note = f"   ← 可更新到 {latest_tag}"
        add(f"  插件        {core.ADDON_NAME}  v{addon_ver or '?'}{note}")
    else:
        add(f"  插件        未安装（缺少 {core.ADDON_NAME}）")
    add(f"  DLSS-G      {vstr(dg['ver']) if dg else '未找到'}")
    sl = (game.dlls.get("sl.interposer.dll") or {}).get("ver")
    add(f"  Streamline  {vstr(sl) if sl else '-'}")
    xf = (game.dlls.get("libxess_fg.dll") or {}).get("ver")
    add(f"  XeSS-FG     {vstr(xf) if xf else '未找到'}")
    add("")

    add("[崩溃与系统事件]")
    if crashes:
        for cn, ct in crashes:
            add(f"  {ct}  {cn}")
    else:
        add("  游戏目录内无崩溃转储")
    kws = [os.path.splitext(os.path.basename(game.exe))[0]] if game.exe else []
    kws = [k for k in kws if len(k) >= 4]
    events = _recent_events(kws, hours=72)
    add("")
    if events:
        add("  Windows 应用错误日志（近 72 小时）：")
        for e in events[:8]:
            add("    · " + e)
        add("    若时间与游戏退出吻合，多半是注入或插件冲突")
    else:
        add("  Windows 应用错误日志：未发现相关记录")
    add("")

    add("[建议]")
    tips = []
    if not proxy:
        tips.append("先点「一键安装」，部署 ReShade 宿主 + 插件")
    if has_addon and latest_tag and addon_ver != latest_tag:
        tips.append(f"插件 {addon_ver} 落后最新 {latest_tag}，点「一键安装」更新")
    if dg and dg.get("ver") and dg["ver"] < (310, 0):
        tips.append(f"DLSS-G {vstr(dg['ver'])} 低于 310.x，点「一键安装」会自动替换")
    if not dg and game.verdict == core.V_NEED_DLL:
        tips.append("游戏未自带 DLSS-G，点「一键安装」会自动放入 310.x")
    if parsed and parsed[0][3]["init"] and not parsed[0][3]["addon_loaded"] and has_addon:
        tips.append("ReShade 起来了但插件没加载：先把 ReShade 更新到最新，再确认 addon64 未被杀软隔离")
    if not tips:
        tips.append("未发现需要处理的问题")
    for t in tips:
        add("  · " + t)
    add("")

    add("[环境]")
    try:
        v = sys.getwindowsversion()
        label = "11" if v.build >= 22000 else ("10" if v.build >= 10240 else f"{v.major}.{v.minor}")
        add(f"  系统        Windows {label} (build {v.build})")
    except Exception:
        add("  系统        未知")
    add(f"  显卡        {ST.gpu.name or '未检测到'}")
    drv_latest = latest_driver()
    note = ""
    if drv_latest and ST.gpu.driver:
        note = "（已最新）" if ST.gpu.driver == drv_latest else f"（可更新到 {drv_latest}）"
    add(f"  驱动        {ST.gpu.driver or '-'}{note}")
    add(f"  工具版本    {core.APP_VERSION}")
    add(f"  MFG 最新    {latest_tag or '获取失败'}    ReShade 最新 {ST.reshade_ver or '获取失败'}")
    add("=" * 64)

    path = os.path.join(_desktop(), "MFG-诊断报告.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(L))
    return path


def _url_ok(url, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MFGUnlocker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def env_check():
    """运行环境体检：缺什么就告诉用户去哪下载"""
    items = []

    def add(name, level, detail, fix="", fix_text=""):
        items.append({"name": name, "level": level, "detail": detail,
                      "fix": fix, "fixText": fix_text})

    # 系统
    try:
        v = sys.getwindowsversion()
        if v.build >= 22000:
            add("系统", "ok", f"Windows 11 · build {v.build}")
        elif v.build >= 10240:
            add("系统", "ok", f"Windows 10 · build {v.build}")
        else:
            add("系统", "err", "需要 Windows 10 或更高版本", "https://www.microsoft.com/windows", "下载 Windows")
    except Exception:
        add("系统", "warn", "无法读取系统版本")

    # 显卡与驱动
    gpu = ST.gpu
    if gpu.name:
        add("显卡", "ok", gpu.name)
        if gpu.driver:
            latest = latest_driver()
            if latest and gpu.driver != latest:
                add("驱动", "warn", f"{gpu.driver}，最新 {latest}",
                    "https://www.nvidia.com/Download/index.aspx", "下载驱动")
            else:
                add("驱动", "ok", gpu.driver)
        else:
            add("驱动", "warn", "读不到驱动版本，请确认已安装 NVIDIA 驱动",
                "https://www.nvidia.com/Download/index.aspx", "下载驱动")
    else:
        add("显卡", "err", "未检测到 NVIDIA 显卡，本工具依赖 N 卡驱动功能",
            "https://www.nvidia.com/Download/index.aspx", "下载驱动")

    # 窗口环境（Edge / Chrome）
    exe, name = find_browser()
    if exe:
        add("窗口环境", "ok", name)
    else:
        add("窗口环境", "warn", "没找到 Edge / Chrome，会用默认浏览器打开页面",
            "https://www.microsoft.com/edge", "安装 Edge")

    # 网络
    net_gh = _url_ok("https://api.github.com")
    net_nv = _url_ok("https://gfwsl.geforce.com")
    if net_gh and net_nv:
        add("网络", "ok", "GitHub 与 NVIDIA 均可访问")
    elif net_gh:
        add("网络", "warn", "GitHub 通，NVIDIA 驱动接口不通（不影响使用）")
    elif net_nv:
        add("网络", "warn", "NVIDIA 通，GitHub 不通（拿不到插件最新版）",
            "https://github.com/mavismmg/MFGAdaUnlock-RenoDx/releases", "手动下载")
    else:
        add("网络", "err", "外网不通，无法获取插件与 ReShade",
            "https://github.com/mavismmg/MFGAdaUnlock-RenoDx/releases", "手动下载")

    # Steam 库
    if ST.cfg.get("libraries"):
        add("Steam 游戏库", "ok", f"{len(ST.cfg['libraries'])} 个库")
    else:
        add("Steam 游戏库", "err", "没找到 Steam 库，用左侧「添加游戏」手动指定目录")

    # 磁盘
    try:
        free = shutil.disk_usage(core.APP_DATA).free / 1073741824
        if free >= 1:
            add("磁盘空间", "ok", f"可用 {free:.1f} GB")
        else:
            add("磁盘空间", "warn", f"只剩 {free:.1f} GB，装插件和缓存可能不够")
    except Exception:
        pass

    # 管理员
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        elevated = False
    if elevated:
        add("权限", "ok", "管理员运行")
    else:
        add("权限", "ok", "普通权限（Steam 库通常可直接写入）")

    bad = [i for i in items if i["level"] == "err"]
    warn = [i for i in items if i["level"] == "warn"]
    return {"items": items, "errors": len(bad), "warnings": len(warn)}


def spawn(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

TOKEN = secrets.token_hex(8)


class Handler(BaseHTTPRequestHandler):
    server_version = "MFGUnlocker/1.2"

    def log_message(self, fmt, *args):
        pass

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _touch(self):
        ST.last_seen = time.time()

    def _ok(self):
        q = parse_qs(urlparse(self.path).query)
        return (self.headers.get("X-Token") == TOKEN) or (q.get("t", [""])[0] == TOKEN)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        self._touch()
        u = urlparse(self.path)
        path = u.path

        if path in ("/", "/index.html"):
            try:
                html = open(resource_path(os.path.join("web", "index.html")),
                            "r", encoding="utf-8").read()
            except OSError:
                self._send(500, "index.html missing", "text/plain; charset=utf-8")
                return
            html = html.replace("__TOKEN__", TOKEN)
            self._send(200, html, "text/html; charset=utf-8")
            return

        if not self._ok():
            self._json({"error": "forbidden"}, 403)
            return

        if path == "/api/state":
            self._json(ST.state_json())
        elif path == "/api/games":
            self._json(ST.games_json())
        elif path == "/api/icon":
            q = parse_qs(u.query)
            p = unquote(q.get("p", [""])[0])
            g = ST.by_path.get(p)
            png = None
            if g and g.exe:
                try:
                    png = mfg_icons.icon_png(g.exe, 32)
                except Exception:
                    png = None
            if png:
                self._send(200, png, "image/png")
            else:
                self._send(404, b"", "image/png")
        elif path == "/api/open":
            q = parse_qs(u.query)
            gp = unquote(q.get("p", [""])[0])
            g = ST.by_path.get(gp)
            if g:
                core.open_folder(g.exe_dir or g.path)
            self._json({"ok": True})
        elif path == "/api/refresh":
            spawn(boot_env)
            self._json({"ok": True})
        elif path == "/api/envcheck":
            try:
                self._json(env_check())
            except Exception as e:
                self._json({"items": [], "errors": 1, "warnings": 0,
                            "error": str(e)}, 500)
        elif path == "/api/report":
            q = parse_qs(u.query)
            gp = unquote(q.get("p", [""])[0])
            g = ST.by_path.get(gp)
            if not g:
                self._json({"ok": False, "error": "先选中一个游戏再生成报告"}, 400)
                return
            try:
                fp = build_report(g)
                self._json({"ok": True, "path": fp, "game": ST.names.get(g.path, g.name)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/openurl":
            q = parse_qs(u.query)
            target = unquote(q.get("u", [""])[0])
            allowed = ("https://reshade.me", "https://github.com/",
                       "https://www.nvidia.com", "https://www.nvidia.cn",
                       "https://www.microsoft.com")
            if not target.startswith(allowed):
                self._json({"ok": False, "error": "链接不在白名单"}, 400)
                return
            ok, how = False, ""
            # ① 交给系统 shell（会走默认浏览器）
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                rc = ctypes.windll.shell32.ShellExecuteW(None, "open", target, None, None, 1)
                if int(rc) > 32:
                    ok, how = True, "shell"
            except Exception:
                pass
            # ② 用浏览器可执行文件直接开（避免 app 窗口吞掉新标签）
            if not ok:
                try:
                    exe, _name = find_browser()
                    if exe:
                        subprocess.Popen([exe, target],
                                         creationflags=0x08000000)
                        ok, how = True, "browser"
                except Exception:
                    pass
            # ③ 兜底
            if not ok:
                try:
                    ok = bool(webbrowser.open(target))
                    how = "webbrowser"
                except Exception:
                    ok = False
            self._json({"ok": ok, "url": target, "via": how})
        elif path == "/api/ping":
            self._json({"ok": True})
        elif path == "/api/backup":
            core.open_folder(os.path.join(APP_DATA, "backup"))
            self._json({"ok": True})
        elif path == "/api/pickfolder":
            fp = pick_folder()
            self._json({"ok": bool(fp), "path": fp or ""})
        elif path == "/api/pickfile":
            fp = pick_file()
            self._json({"ok": bool(fp), "path": fp or ""})
        elif path == "/api/autolibs":
            found = core.discover_steam_libraries()
            added = 0
            for lib in found:
                if lib not in ST.cfg["libraries"]:
                    ST.cfg["libraries"].append(lib)
                    added += 1
            if added:
                core.save_config(ST.cfg)
            self._json({"ok": True, "added": added, "libraries": ST.cfg["libraries"]})
        elif path == "/api/exit":
            self._json({"ok": True})
            ST.exiting = True
            threading.Timer(0.3, lambda: os._exit(0)).start()
        elif path == "/api/lastmsg":
            self._json({"text": getattr(ST, "last_msg", "")})
        else:
            self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        self._touch()
        if not self._ok():
            self._json({"error": "forbidden"}, 403)
            return
        u = urlparse(self.path)
        body = self._body()
        path = body.get("path", "")

        try:
            if u.path == "/api/scan":
                spawn(do_scan)
                self._json({"ok": True})
            elif u.path == "/api/install":
                if ST.progress.get("busy"):
                    self._json({"ok": False, "error": "有任务正在执行"}, 409)
                    return
                spawn(job_install, path, bool(body.get("reshade", True)))
                self._json({"ok": True})
            elif u.path == "/api/uninstall":
                spawn(job_uninstall, path)
                self._json({"ok": True})
            elif u.path == "/api/reshade":
                spawn(job_reshade, path, body.get("action", "install"))
                self._json({"ok": True})
            elif u.path == "/api/dlssg":
                spawn(job_dlssg, path)
                self._json({"ok": True})
            elif u.path == "/api/addgame":
                target = body.get("path") or pick_folder()
                if not target:
                    self._json({"ok": False, "error": "未选择目录"}, 400)
                    return
                g, err = add_manual_game(target)
                if not g:
                    self._json({"ok": False, "error": err}, 400)
                    return
                self._json({"ok": True, "game": ST.game_json(g)})
            elif u.path == "/api/localinstall":
                src = body.get("src", "")
                if not src or not os.path.isfile(src):
                    self._json({"ok": False, "error": "文件不存在"}, 400)
                    return
                spawn(job_local_install, path, src)
                self._json({"ok": True})
            elif u.path == "/api/settings":
                tier = float(body.get("tier", 1.0))
                ST.tier = max(1.0, min(2.0, tier))
                self._json({"ok": True, "tier": ST.tier})
            elif u.path == "/api/exit":
                self._json({"ok": True})
                threading.Timer(0.4, lambda: os._exit(0)).start()
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


def _shell_log(msg):
    try:
        with open(os.path.join(APP_DATA, "shell.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def _native_window(url):
    """用原生壳开窗口；不支持时返回 False 让调用方退回浏览器"""
    try:
        import shell_window
    except Exception:
        return False
    try:
        icon = resource_path(os.path.join("assets", "icon.ico"))
        ST.native_shell = True
        _shell_log("native window start | icon=" + icon)
        shell_window.run(url, icon=icon,
                         storage=os.path.join(APP_DATA, "webview"))
        _shell_log("native window returned")
        return True
    except Exception:
        traceback.print_exc()
        return False


def main(port=0, open_browser=True, as_app=True):
    spawn(boot_env)
    threading.Timer(1.0, lambda: spawn(do_scan)).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual = httpd.server_address[1]
    ST.port = actual
    url = f"http://127.0.0.1:{actual}/?t={TOKEN}"

    try:
        print(f"MFG Unlock Manager  ->  {url}")   # windowed 模式下 stdout 为 None
    except Exception:
        pass
    try:                                          # 同时写一份，方便排查/截图
        with open(os.path.join(APP_DATA, "url.txt"), "w", encoding="utf-8") as f:
            f.write(url)
    except OSError:
        pass
    ST.last_seen = time.time()
    threading.Thread(target=watchdog, daemon=True).start()

    # HTTP 服务放后台线程，主线程留给窗口的消息循环
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if not open_browser:
        while not ST.exiting:
            time.sleep(0.4)
        return

    # ① 自己的窗口（pywebview / WebView2）：标题栏与任务栏图标是本程序的
    if as_app and _native_window(url):
        _shell_log("main exits after window closed")
        return
    # ② 退回 Edge --app 或默认浏览器
    open_shell(url, as_app)
    while not ST.exiting:
        time.sleep(0.4)


if __name__ == "__main__":
    browser_mode = "--browser" in sys.argv
    main(as_app=not browser_mode)
