"""Windows dialog through MessageBoxW. Optional and untested."""

from __future__ import annotations

from .base import DialogText

MB_OKCANCEL = 0x1
MB_ICONWARNING = 0x30
MB_DEFBUTTON2 = 0x100  # second button (Cancel) is the default
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDOK = 1


class WindowsDialog:
    name = "messagebox"

    def confirm(self, text: DialogText) -> bool:
        import ctypes

        body = text.body + "\n\nOK = Continue, Cancel = Cancel"
        flags = MB_OKCANCEL | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TOPMOST | MB_SETFOREGROUND
        return ctypes.windll.user32.MessageBoxW(None, body, text.title, flags) == IDOK
