"""Native macOS dialog (NSAlert) that also appears over full-screen apps.

A full-screen Chrome window lives in its own Space, and normal windows from
other apps don't show there. What makes the alert appear anyway:
  1. run as an accessory (agent) app: no Dock icon, allowed into other Spaces;
  2. give the alert window CanJoinAllSpaces + FullScreenAuxiliary and a high
     level. runModal lowers the level to the modal-panel level, so a timer in
     the modal run loop puts it back once the alert is up;
  3. centre it on the display showing the browser's front window (Chrome is
     our parent process), falling back to the display under the mouse.
Must be called on the main thread.
"""

from __future__ import annotations

import os

import objc
import Quartz
from AppKit import (
    NSAlert,
    NSAlertStyleWarning,
    NSApplication,
    NSEvent,
    NSImage,
    NSModalPanelRunLoopMode,
    NSRunLoop,
    NSScreen,
    NSTimer,
)
from Foundation import NSObject

from .base import DialogText

NS_ALERT_FIRST_BUTTON = 1000  # Cancel: added first, so it's the default (Return); Esc maps to it too
NS_ALERT_SECOND_BUTTON = 1001  # Continue

CAN_JOIN_ALL_SPACES = 1 << 0
FULL_SCREEN_AUXILIARY = 1 << 8
STATUS_WINDOW_LEVEL = 25


class _Raiser(NSObject):
    """Re-applies level and Space behaviour after runModal has shown the window."""

    def initWithWindow_(self, window):
        self = objc.super(_Raiser, self).init()
        if self is None:
            return None
        self.window = window
        return self

    def raise_(self, _timer):
        w = self.window
        w.setCollectionBehavior_(CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY)
        w.setLevel_(STATUS_WINDOW_LEVEL)
        w.orderFrontRegardless()
        w.makeKeyWindow()


class MacDialog:
    name = "nsalert"

    def __init__(self) -> None:
        self._app = NSApplication.sharedApplication()
        self._app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    def confirm(self, text: DialogText) -> bool:
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_(text.title)
        alert.setInformativeText_(text.body)
        alert.addButtonWithTitle_("Cancel")
        alert.addButtonWithTitle_("Continue")
        caution = NSImage.imageNamed_("NSCaution")
        if caution is not None:
            alert.setIcon_(caution)
        alert.layout()

        window = alert.window()
        window.setCollectionBehavior_(CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY)
        window.setLevel_(STATUS_WINDOW_LEVEL)
        _centre_on(window, _target_screen())

        raiser = _Raiser.alloc().initWithWindow_(window)
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.05, raiser, "raise:", None, False)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

        self._activate()
        return alert.runModal() == NS_ALERT_SECOND_BUTTON

    def _activate(self) -> None:
        try:
            self._app.activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001 - deprecated on newer macOS; the level still keeps it on top
            try:
                self._app.activate()
            except Exception:  # noqa: BLE001
                pass


def _anchor_pid() -> int:
    """The browser that launched us. UPLOAD_GUARD_ANCHOR_PID overrides it (used by tests)."""
    try:
        return int(os.environ.get("UPLOAD_GUARD_ANCHOR_PID", "")) or os.getppid()
    except ValueError:
        return os.getppid()


def _target_screen():
    """The screen showing the anchor app's front window, else the one under the mouse."""
    screens = list(NSScreen.screens() or [])
    if not screens:
        return None
    main_height = screens[0].frame().size.height  # Cocoa y-axis is flipped relative to CG
    point = None
    pid = _anchor_pid()
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    for w in windows:  # front to back
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
            b = w.get("kCGWindowBounds", {})
            if b.get("Width", 0) > 100 and b.get("Height", 0) > 100:
                point = (b["X"] + b["Width"] / 2, main_height - (b["Y"] + b["Height"] / 2))
                break
    if point is None:
        loc = NSEvent.mouseLocation()
        point = (loc.x, loc.y)
    for s in screens:
        f = s.frame()
        if f.origin.x <= point[0] < f.origin.x + f.size.width and f.origin.y <= point[1] < f.origin.y + f.size.height:
            return s
    return screens[0]


def _centre_on(window, screen) -> None:
    if screen is None:
        window.center()
        return
    vf = screen.visibleFrame()
    wf = window.frame()
    x = vf.origin.x + (vf.size.width - wf.size.width) / 2
    y = vf.origin.y + (vf.size.height - wf.size.height) * 0.6  # a little above centre, like system alerts
    window.setFrameOrigin_((x, y))
