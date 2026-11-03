"""探测 ReShade Setup 安装器内的内嵌资源（PE RCData）。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

k = ctypes.windll.kernel32
LOAD_LIBRARY_AS_DATAFILE = 0x00000002

ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, wt.HMODULE, wt.HMODULE, wt.LPARAM)

k.LoadLibraryExW.argtypes = [wt.LPCWSTR, wt.HANDLE, wt.DWORD]
k.LoadLibraryExW.restype = wt.HMODULE
k.FindResourceW.argtypes = [wt.HMODULE, wt.HMODULE, wt.HMODULE]
k.FindResourceW.restype = wt.HANDLE
k.SizeofResource.argtypes = [wt.HMODULE, wt.HANDLE]
k.SizeofResource.restype = wt.DWORD
k.LoadResource.argtypes = [wt.HMODULE, wt.HANDLE]
k.LoadResource.restype = wt.HANDLE
k.LockResource.argtypes = [wt.HANDLE]
k.LockResource.restype = ctypes.c_void_p
k.EnumResourceNamesW.argtypes = [wt.HMODULE, wt.HMODULE, ENUMPROC, wt.LPARAM]
k.EnumResourceNamesW.restype = wt.BOOL


def mid(i):
    """MAKEINTRESOURCE"""
    return ctypes.cast(ctypes.c_void_p(i), wt.HMODULE)


path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Administrator\MFG-Unlocker\tools\setup.exe"
outdir = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\Administrator\MFG-Unlocker\tools\rsdump"
os.makedirs(outdir, exist_ok=True)

h = k.LoadLibraryExW(path, None, LOAD_LIBRARY_AS_DATAFILE)
if not h:
    raise SystemExit("load failed %d" % ctypes.get_last_error())

found = []


def res_name_cb(hmod, typ, name, lp):
    try:
        tv = typ if typ is not None else 0
        tval = tv if isinstance(tv, int) else 0
    except Exception:
        tval = 0
    hres = k.FindResourceW(hmod, name, typ)
    if not hres:
        return True
    size = k.SizeofResource(hmod, hres)
    data = k.LockResource(k.LoadResource(hmod, hres))
    if not data or not size:
        return True
    buf = ctypes.string_at(data, size)
    kind = "raw"
    if buf[:2] == b"MZ":
        kind = "PE"
    elif buf[:2] == b"PK":
        kind = "ZIP"
    elif buf[:4] == b"\x28\xb5\x2f\xfd":
        kind = "ZSTD"
    try:
        nval = name if isinstance(name, int) else 0
    except Exception:
        nval = 0
    found.append((tval, nval, size, kind, buf))
    return True


cb = ENUMPROC(res_name_cb)
for t in (10, 23, 256, 257, 258, 259, 260):
    k.EnumResourceNamesW(h, mid(t), cb, 0)

for t, n, size, kind, buf in found:
    fn = os.path.join(outdir, f"res_{t}_{n}_{kind}_{size}.bin")
    with open(fn, "wb") as f:
        f.write(buf)
    print(f"type={t} name={n} size={size:>9} kind={kind}")

print("total", len(found))
