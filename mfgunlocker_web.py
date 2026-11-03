#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MFG Unlock Manager（Web 版）入口"""
import os
import sys

# windowed 打包后 stdout/stderr 为 None，任何 print / traceback 都会崩
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mfg_web  # noqa: E402

if __name__ == "__main__":
    mfg_web.main()
