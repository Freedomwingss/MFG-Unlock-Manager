"""UI 冒烟测试：启动后可自动选中游戏、设置字号倍率，便于截图检查。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctypes  # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import mfg_ui  # noqa: E402

TARGET = sys.argv[1] if len(sys.argv) > 1 else "PBZ"
SCALE = float(sys.argv[2]) if len(sys.argv) > 2 else None

app = mfg_ui.App()


def pick():
    g = next((x for x in app.games if x.name == TARGET), None)
    if not g:
        app.after(2000, pick)
        return
    app.tree.selection_set(g.path)
    app.tree.see(g.path)
    app.on_select()
    app.lbl_status.configure(text=f"测试选中：{g.name}")


def apply_scale():
    if SCALE:
        app._set_fs(SCALE)


app.after(15000, pick)
if SCALE:
    app.after(20000, apply_scale)
app.mainloop()
