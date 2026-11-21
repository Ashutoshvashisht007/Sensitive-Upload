"""The desktop app: a menu-bar item that runs the guard.

Main thread: AppKit (menu bar and the native dialogs).
Background thread: the proxy (mitmproxy on its own asyncio loop).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__, audit
from .config import ensure_home, home_dir, load_config, save_setting
from .decisions import Decider, MainThreadRunner, ThreadRunner
from .proxy.addon import GuardAddon
from .proxy.server import GuardProxy

log = logging.getLogger("upload_guard.app")
ASSETS = Path(__file__).resolve().parent / "assets"


def setup_logging() -> None:
    path = ensure_home() / "app.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ("mitmproxy", "hpack", "h2", "passlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def run(headless: bool = False) -> int:
    setup_logging()
    cfg = load_config()
    log.info("Sensitive Upload Guard %s starting on 127.0.0.1:%d", __version__, cfg.proxy_port)
    if headless or sys.platform != "darwin" or os.environ.get("UPLOAD_GUARD_DIALOG"):
        return _run_headless(cfg)
    return _run_menubar(cfg)


def _run_headless(cfg) -> int:
    proxy = GuardProxy([GuardAddon(Decider(ThreadRunner()))], cfg.proxy_port)
    proxy.start()
    print(f"Sensitive Upload Guard is running on 127.0.0.1:{cfg.proxy_port}. Ctrl+C to stop.", flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    proxy.stop()
    return 0


def _run_menubar(cfg) -> int:
    import objc
    from AppKit import (
        NSApplication,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
        NSWorkspace,
    )
    from Foundation import NSURL, NSObject
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # accessory: menu bar only, no Dock icon

    proxy = GuardProxy([GuardAddon(Decider(MainThreadRunner()))], cfg.proxy_port)
    error = None
    try:
        proxy.start()
    except RuntimeError as e:
        error = str(e)
        log.error("proxy failed to start: %s", e)

    class Controller(NSObject):
        def init(self):
            self = objc.super(Controller, self).init()
            self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            icon = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "menubar.png"))
            if icon is not None:
                icon.setTemplate_(True)
                icon.setSize_((18, 18))
                self.item.button().setImage_(icon)
            else:
                self.item.button().setTitle_("UG")
            self.item.button().setToolTip_("Sensitive Upload Guard")
            self.menu = NSMenu.alloc().init()
            self.menu.setDelegate_(self)
            self.item.setMenu_(self.menu)
            self.rebuild()
            return self

        def rebuild(self):
            m = self.menu
            m.removeAllItems()

            def info(title):
                it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                it.setEnabled_(False)
                m.addItem_(it)

            info(f"Sensitive Upload Guard {__version__}")
            if error:
                info("Not protecting: " + error[:80])
                info("ChatGPT and Perplexity are blocked until this is fixed")
            else:
                info("Protecting ChatGPT and Perplexity")
            m.addItem_(NSMenuItem.separatorItem())
            entries = audit.tail(5)
            info("Recent decisions" if entries else "No decisions yet")
            for e in reversed(entries):
                info(f"  {e['ts'][11:16]}  {e['site']:<10} {e['name'][:28]} → {e['decision'].title()}")
            m.addItem_(NSMenuItem.separatorItem())
            logging_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Log request details (troubleshooting)", "toggleLogging:", "")
            logging_item.setTarget_(self)
            logging_item.setState_(1 if load_config().log_requests else 0)
            m.addItem_(logging_item)
            folder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Open log folder", "openFolder:", "")
            folder.setTarget_(self)
            m.addItem_(folder)
            m.addItem_(NSMenuItem.separatorItem())
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit (ChatGPT and Perplexity stay blocked)", "quit:", "q")
            quit_item.setTarget_(self)
            m.addItem_(quit_item)

        def menuNeedsUpdate_(self, _menu):
            self.rebuild()

        def toggleLogging_(self, _sender):
            save_setting("log_requests", not load_config().log_requests)

        def openFolder_(self, _sender):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(home_dir())))

        def quit_(self, _sender):
            proxy.stop()
            NSApplication.sharedApplication().terminate_(None)

    controller = Controller.alloc().init()  # noqa: F841 - kept alive by the run loop
    AppHelper.runEventLoop(installInterrupt=True)
    proxy.stop()
    return 0
