"""Fallback native dialog through `osascript`.

The text is passed as arguments to `on run argv`, never spliced into the
script source, so file names and prompts can't inject AppleScript.
"""

from __future__ import annotations

import subprocess

from .base import DialogText

_SCRIPT = [
    "on run argv",
    'display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Continue"} '
    'default button "Cancel" cancel button "Cancel" with icon caution',
    'return button returned of result',
    "end run",
]


class OsascriptDialog:
    name = "osascript"

    def confirm(self, text: DialogText) -> bool:
        cmd = ["/usr/bin/osascript"]
        for line in _SCRIPT:
            cmd += ["-e", line]
        cmd += ["--", text.title, text.body]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Cancel exits with error -128 (non-zero), Continue prints the button name.
        return result.returncode == 0 and result.stdout.strip() == "Continue"
