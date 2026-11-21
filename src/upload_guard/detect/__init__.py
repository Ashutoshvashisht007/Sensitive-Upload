"""Detection entry points used by the native host and the CLI.

inspect_text(text)              -> Finding | None   (prompts)
inspect_file(name, mime, data)  -> Finding | None   (uploads)

None means clean. A Finding either says where the marker is, or that the file
couldn't be inspected (the user is then asked instead of passing it silently).
"""

from __future__ import annotations

import logging

from ..config import Config, load_config
from . import filetype, image, office, pdf, text
from .marker import find as find_marker
from .result import Finding

__all__ = ["Finding", "inspect_text", "inspect_file"]

log = logging.getLogger(__name__)
MAX_DEPTH = 2  # archives inside archives


def inspect_text(prompt: str) -> Finding | None:
    m = find_marker(prompt)
    return Finding(where="prompt", excerpt=m.excerpt()) if m else None


def inspect_file(name: str, mime: str, data: bytes, config: Config | None = None) -> Finding | None:
    cfg = config or load_config()
    if len(data) > cfg.max_inspect_bytes:
        return Finding.cannot_inspect(f"it's larger than {cfg.max_inspect_mb} MB")
    try:
        return _inspect(name, mime, data, cfg, depth=0)
    except Exception as e:  # noqa: BLE001 - never let a parser bug pass a file silently
        log.exception("inspection failed for a %d-byte file", len(data))
        return Finding.cannot_inspect(f"inspection failed ({type(e).__name__})")


def _inspect(name: str, mime: str, data: bytes, cfg: Config, depth: int) -> Finding | None:
    kind = filetype.sniff(data, name, mime)
    if kind == filetype.PDF:
        found = pdf.inspect(data, cfg.ocr_max_pages)
        if found and found.uninspectable and filetype.looks_like_text(data):
            return text.inspect(data)  # mentioned "%PDF-" but is really text
        return found
    if kind == filetype.IMAGE:
        return image.inspect(data)
    if kind == filetype.ZIP:
        if depth >= MAX_DEPTH:
            return Finding.cannot_inspect("archives are nested too deeply")
        return office.inspect(data, lambda n, d, dep: _inspect(n, "", d, cfg, dep), depth)
    if kind == filetype.TEXT:
        return text.inspect(data)
    # Unknown binary: the raw scan is all we can do.
    return Finding(where="file contents") if text.scan_raw(data) else None
