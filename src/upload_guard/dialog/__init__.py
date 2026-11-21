"""Pick a dialog backend.

UPLOAD_GUARD_DIALOG chooses explicitly:
    nsalert | osascript | zenity | messagebox            real dialogs
    auto-continue | auto-cancel | auto-file:<path>       tests only (see auto.py)
Unset: NSAlert on macOS (osascript if PyObjC is missing), zenity on Linux,
MessageBox on Windows.
"""

from __future__ import annotations

import logging
import os
import sys

from .base import DialogBackend, DialogText, FlaggedFile, files_text, prompt_text

__all__ = ["get_backend", "DialogBackend", "DialogText", "FlaggedFile", "files_text", "prompt_text"]

log = logging.getLogger(__name__)


def get_backend() -> DialogBackend:
    choice = os.environ.get("UPLOAD_GUARD_DIALOG", "").strip()
    if choice in ("auto-continue", "auto-cancel"):
        from .auto import AutoDialog

        return AutoDialog(choice.split("-", 1)[1])
    if choice.startswith("auto-file:"):
        from .auto import FileDialog

        return FileDialog(choice.split(":", 1)[1])
    if choice == "osascript":
        from .osascript import OsascriptDialog

        return OsascriptDialog()
    if choice == "zenity":
        from .linux import ZenityDialog

        return ZenityDialog()
    if choice == "messagebox":
        from .windows import WindowsDialog

        return WindowsDialog()

    if sys.platform == "darwin":
        try:
            from .macos import MacDialog

            return MacDialog()
        except ImportError as e:
            log.warning("PyObjC unavailable (%s), using osascript", e)
            from .osascript import OsascriptDialog

            return OsascriptDialog()
    if sys.platform.startswith("win"):
        from .windows import WindowsDialog

        return WindowsDialog()
    from .linux import ZenityDialog

    return ZenityDialog()
