"""Find the name of an uploaded file when the site doesn't send it before the bytes.

ChatGPT's newer upload flow reserves a storage URL first, uploads the bytes, and only
then says what the file was called. To name the file in the dialog, ask Spotlight for
files of exactly that size in the home folder and compare SHA-256 hashes. Only the
file name is used, and nothing leaves the Mac.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_CANDIDATES = 400
MAX_READ_BYTES = 256 * 1024 * 1024
TIMEOUT = 2.0
_SKIP_PARTS = ("/node_modules/", "/.git/", "/site-packages/", "/.venv/", "/Caches/")
_LIBRARY_KEEP = ("Mobile Documents", "CloudStorage")  # iCloud Drive, Dropbox, Google Drive …


def name_for_bytes(data: bytes, home: Path | None = None, timeout: float = TIMEOUT) -> str | None:
    """File name of a file under `home` with exactly these bytes, or None."""
    if sys.platform != "darwin" or not data:
        return None
    home = home or Path.home()
    deadline = time.monotonic() + timeout
    return _match(data, _rank(_spotlight(len(data), home, timeout), home), deadline)


def _spotlight(size: int, home: Path, timeout: float) -> list[str]:
    """Paths of files under `home` that Spotlight knows with exactly `size` bytes."""
    try:
        out = subprocess.run(
            ["/usr/bin/mdfind", "-onlyin", str(home), f"kMDItemFSSize == {int(size)}"],
            capture_output=True, text=True, timeout=timeout, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return out.splitlines()


def _rank(paths: list[str], home: Path) -> list[str]:
    library = str(home / "Library") + "/"
    keep = []
    for p in paths:
        if not p or any(s in p for s in _SKIP_PARTS) or "/." in p:
            continue
        if p.startswith(library) and not any(f"/{k}/" in p for k in _LIBRARY_KEEP):
            continue
        keep.append(p)
    # files people pick usually sit in a few well-known folders
    likely = tuple(str(home / d) + "/" for d in ("Desktop", "Downloads", "Documents"))
    keep.sort(key=lambda p: (not p.startswith(likely), p.count("/")))
    return keep[:MAX_CANDIDATES]


def _match(data: bytes, candidates: list[str], deadline: float) -> str | None:
    want, size, read = hashlib.sha256(data).digest(), len(data), 0
    for p in candidates:
        if time.monotonic() > deadline or read > MAX_READ_BYTES:
            break
        try:
            if os.path.getsize(p) != size:
                continue
            with open(p, "rb") as f:
                got = hashlib.sha256(f.read()).digest()
            read += size
        except OSError:
            continue
        if got == want:
            return os.path.basename(p)
    return None
