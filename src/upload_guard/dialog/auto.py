"""Non-interactive backends for tests.

UPLOAD_GUARD_DIALOG=auto-continue    always Continue
UPLOAD_GUARD_DIALOG=auto-cancel      always Cancel
UPLOAD_GUARD_DIALOG=auto-file:<path> read "continue"/"cancel" from <path> each time
                                     (lets a browser test switch answers), and append
                                     every dialog to <path>.log so the test can check it
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import DialogText


class AutoDialog:
    def __init__(self, answer: str):
        self.name = f"auto-{answer}"
        self._answer = answer
        self.shown: list[DialogText] = []

    def confirm(self, text: DialogText) -> bool:
        self.shown.append(text)
        return self._answer == "continue"


class FileDialog:
    def __init__(self, path: str):
        self.name = "auto-file"
        self._path = Path(path)

    def confirm(self, text: DialogText) -> bool:
        try:
            answer = self._path.read_text().strip().lower()
        except OSError:
            answer = "cancel"
        with open(f"{self._path}.log", "a", encoding="utf-8") as log:
            log.write(json.dumps({"title": text.title, "body": text.body, "answer": answer}) + "\n")
        return answer == "continue"
