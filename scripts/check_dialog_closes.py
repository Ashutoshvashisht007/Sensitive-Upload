"""Check that the native dialog really disappears once it's answered.

    uv run python scripts/check_dialog_closes.py

The host goes back to waiting on its request queue right after a dialog,
without running the AppKit run loop. If the dialog doesn't flush its own
close, the window stays frozen on screen. This shows the real dialog in a
child process, clicks Cancel from a timer, then blocks like the host does and
checks (via CGWindowList) that no window is left.
"""

from __future__ import annotations

import subprocess
import sys
import time


def child() -> None:
    from AppKit import NSApp, NSModalPanelRunLoopMode, NSRunLoop, NSTimer
    from Foundation import NSObject

    from upload_guard.dialog.base import DialogText
    from upload_guard.dialog.macos import MacDialog

    class Clicker(NSObject):
        def click_(self, timer):
            window = NSApp.modalWindow()
            if window is None:
                return  # not up yet; the timer repeats
            stack = [window.contentView()]
            while stack:
                v = stack.pop()
                if hasattr(v, "performClick_") and callable(getattr(v, "title", None)) and v.title() == "Cancel":
                    timer.invalidate()
                    v.performClick_(None)
                    return
                stack.extend(v.subviews() or [])

    clicker = Clicker.alloc().init()
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.5, clicker, "click:", None, True)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)
    result = MacDialog().confirm(DialogText("Dialog close check", "This closes itself in a moment."))
    print("continue" if result else "cancel", flush=True)
    time.sleep(5)  # block without a run loop, like the host


def main() -> int:
    import Quartz

    p = subprocess.Popen([sys.executable, __file__, "child"], stdout=subprocess.PIPE, text=True)
    try:
        answer = p.stdout.readline().strip()
        time.sleep(1.0)
        infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        left = [w for w in infos or [] if w.get("kCGWindowOwnerPID") == p.pid]
        print(f"dialog answered: {answer}; windows left on screen: {len(left)}")
        ok = answer == "cancel" and not left
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        p.kill()


if __name__ == "__main__":
    if sys.argv[1:] == ["child"]:
        child()
    else:
        raise SystemExit(main())
