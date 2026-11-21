"""A small macOS notification after Cancel, so the block is visible even when a site shows nothing."""

from __future__ import annotations

import os
import subprocess
import sys
import threading


def notify_blocked(site_label: str, kind: str) -> None:
    if sys.platform != "darwin" or os.environ.get("UPLOAD_GUARD_DIALOG"):
        return  # tests and non-macOS: no notifications
    what = "Your prompt was" if kind == "prompt" else "The file was"
    body = f"{what} not sent to {site_label}."
    # Text goes in as argv, never into the script source.
    cmd = ["/usr/bin/osascript", "-e", "on run argv", "-e",
           "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
           "--", "Blocked by Sensitive Upload Guard", body]
    threading.Thread(target=subprocess.run, args=(cmd,), kwargs={"capture_output": True}, daemon=True).start()
