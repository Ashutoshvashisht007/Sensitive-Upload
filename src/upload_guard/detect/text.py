"""Plain-text files, plus a raw-bytes scan that every detector reuses."""

from __future__ import annotations

import codecs
import re

from . import marker
from .result import Finding

# The marker as it would appear in raw bytes: ASCII/UTF-8, UTF-16 LE and BE.
_RAW = [
    re.compile(re.escape(marker.MARKER.encode(enc)), re.IGNORECASE)
    for enc in ("ascii", "utf-16-le", "utf-16-be")
]


def decode(data: bytes) -> str:
    for bom, enc in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(enc, errors="replace")
    return data.decode("utf-8", errors="replace")


def scan_raw(data: bytes) -> bool:
    """Cheap check for the marker anywhere in the bytes (catches metadata too)."""
    return any(p.search(data) for p in _RAW)


def find_in_text(text: str, where_prefix: str = "line", ocr: bool = False) -> Finding | None:
    m = marker.find(text, ocr=ocr)
    if m is None:
        return None
    if where_prefix == "line":
        where = f"line {m.text.count(chr(10), 0, m.start) + 1}"
    else:
        where = where_prefix
    return Finding(where=where, excerpt=m.excerpt())


def inspect(data: bytes) -> Finding | None:
    found = find_in_text(decode(data))
    if found:
        return found
    if scan_raw(data):
        return Finding(where="file contents")
    return None
