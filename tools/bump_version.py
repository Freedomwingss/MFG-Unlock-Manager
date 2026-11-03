#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本号管理：读 VERSION，写入 PyInstaller 的 version_info.txt

用法：
  python tools/bump_version.py            # 打印当前版本
  python tools/bump_version.py --bump     # 末位 +1，并重新生成 version_info.txt
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VER_FILE = os.path.join(ROOT, "VERSION")
OUT = os.path.join(ROOT, "tools", "version_info.txt")

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver4},
    prodvers={ver4},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'MFG Unlock Manager'),
        StringStruct('FileDescription', '多帧生成解锁管理器'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', 'MFGUnlockManager'),
        StringStruct('LegalCopyright', ''),
        StringStruct('OriginalFilename', 'MFGUnlockManager.exe'),
        StringStruct('ProductName', 'MFG Unlock Manager'),
        StringStruct('ProductVersion', '{ver}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def read_version():
    with open(VER_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_version(v):
    with open(VER_FILE, "w", encoding="utf-8") as f:
        f.write(v + "\n")


def bump(v):
    parts = [int(x) for x in v.split(".")]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(x) for x in parts[:3])


def render(v):
    nums = [int(x) for x in v.split(".")]
    while len(nums) < 4:
        nums.append(0)
    ver4 = tuple(nums[:4])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(ver=v, ver4=ver4))
    return OUT


def main():
    v = read_version()
    if "--bump" in sys.argv:
        v = bump(v)
        write_version(v)
    out = render(v)
    print(f"version {v} -> {out}")


if __name__ == "__main__":
    main()
