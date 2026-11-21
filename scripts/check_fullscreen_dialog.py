"""Check that the native dialog appears over a full-screen app without a Space switch.

    uv run python scripts/check_fullscreen_dialog.py

1. Opens a Tk window in native full screen (its own Space).
2. Starts `upload-guard dialog-test`.
3. Uses CGWindowList (no screen-recording permission needed) to confirm the
   dialog has an on-screen window while the full-screen window is still on
   screen, i.e. macOS didn't switch Spaces away from it.
Both windows are closed afterwards; nothing is clicked.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import Quartz

TK_SCRIPT = """
import tkinter as tk
root = tk.Tk()
root.configure(bg="#1e293b")
tk.Label(root, text="full-screen test window", fg="white", bg="#1e293b", font=("Helvetica", 40)).pack(expand=True)
root.after(300, lambda: root.attributes("-fullscreen", True))
root.mainloop()
"""


def windows_of(pid: int) -> list[dict]:
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    out = []
    for w in infos or []:
        if w.get("kCGWindowOwnerPID") == pid:
            b = w.get("kCGWindowBounds", {})
            out.append({"layer": w.get("kCGWindowLayer"), "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0)),
                        "x": int(b.get("X", 0)), "y": int(b.get("Y", 0)), "name": w.get("kCGWindowName")})
    return out


def wait_for(pred, timeout=10.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def main() -> int:
    screen = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    sw, sh = int(screen.size.width), int(screen.size.height)
    tk = subprocess.Popen([sys.executable, "-c", TK_SCRIPT])
    dlg = None
    try:
        full = wait_for(lambda: [w for w in windows_of(tk.pid) if w["w"] >= sw and w["h"] >= sh - 40], 15)
        if not full:
            print("FAIL: the Tk window never went full screen", windows_of(tk.pid))
            return 1
        print(f"full-screen window on screen: {full[0]}")
        time.sleep(1.5)  # let the full-screen animation finish

        env = dict(os.environ, UPLOAD_GUARD_ANCHOR_PID=str(tk.pid))  # stands in for Chrome
        env.pop("UPLOAD_GUARD_DIALOG", None)
        dlg = subprocess.Popen([sys.executable, "-m", "upload_guard", "dialog-test"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        shown = wait_for(lambda: [w for w in windows_of(dlg.pid) if w["w"] > 200], 15)
        time.sleep(0.5)  # give the level fix-up timer a moment
        shown = [w for w in windows_of(dlg.pid) if w["w"] > 200] or shown
        still_full = [w for w in windows_of(tk.pid) if w["w"] >= sw]
        fs = full[0]
        print(f"dialog windows on screen: {shown}")
        print(f"full-screen window still on screen: {bool(still_full)}")
        ok = bool(shown) and bool(still_full)
        if shown:
            d = shown[0]
            same_display = fs["x"] <= d["x"] and d["x"] + d["w"] <= fs["x"] + fs["w"] and fs["y"] <= d["y"] <= fs["y"] + fs["h"]
            print(f"dialog on the full-screen window's display: {same_display}")
            print(f"dialog window layer: {d['layer']} (want 25, status-bar level)")
            ok = ok and same_display and d["layer"] == 25
        from AppKit import NSWorkspace
        from Foundation import NSDate, NSRunLoop

        # NSWorkspace only refreshes while a run loop runs, so spin ours briefly.
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))
        front_pid = NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
        print(f"frontmost (keyboard focus): pid {front_pid}; dialog pid {dlg.pid}, full-screen pid {tk.pid}")
        print(f"dialog has keyboard focus: {front_pid == dlg.pid}")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        for p in (dlg, tk):
            if p and p.poll() is None:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
