"""Linux dialog through zenity. Optional and untested on real desktops."""

from __future__ import annotations

import shutil
import subprocess

from .base import DialogText


class ZenityDialog:
    name = "zenity"

    def __init__(self) -> None:
        self._exe = shutil.which("zenity")
        if not self._exe:
            raise RuntimeError("zenity not installed")

    def confirm(self, text: DialogText) -> bool:
        result = subprocess.run(
            [self._exe, "--question", "--title", text.title, "--text", text.body,
             "--ok-label", "Continue", "--cancel-label", "Cancel", "--default-cancel", "--no-markup"],
            capture_output=True,
        )
        return result.returncode == 0
