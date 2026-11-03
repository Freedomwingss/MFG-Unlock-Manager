#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原生窗口外壳：用 pywebview（Windows 上是 WebView2）开一个自己的窗口

好处是窗口标题、任务栏图标都是本程序的，不再是浏览器的地球仪。
"""
import os


def run(url, icon=None, storage=None):
    # 关掉崩溃报告、限制磁盘缓存，避免 WebView2 目录越用越大
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-crash-reporter --disable-features=WidevineCdm,OptimizationHints "
        "--disk-cache-size=33554432 --media-cache-size=8388608")

    import webview

    webview.create_window(
        "多帧生成解锁管理器",
        url,
        width=1500,
        height=940,
        min_size=(980, 640),
        background_color="#202020",
        text_select=False,
        easy_drag=False,
        confirm_close=False,
    )
    kwargs = {"debug": False, "private_mode": False}
    if icon and os.path.isfile(icon):
        kwargs["icon"] = icon
    if storage:
        os.makedirs(storage, exist_ok=True)
        kwargs["storage_path"] = storage
    webview.start(**kwargs)
