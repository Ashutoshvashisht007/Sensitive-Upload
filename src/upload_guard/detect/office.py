"""ZIP containers: Office files (docx/xlsx/pptx) and plain archives.

XML parts are checked with tags stripped (Word splits text into runs, so the
marker can be spread over several <w:t> elements). Other members go through
the normal detectors, so a picture inside a .docx is OCR'd too.
"""

from __future__ import annotations

import io
import re
import zipfile

from . import marker
from .result import Finding

MAX_MEMBERS = 500
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # zip-bomb guard
_TAG = re.compile(rb"<[^>]+>")
_XML_SUFFIXES = (".xml", ".rels", ".vml")


def inspect(data: bytes, inspect_member, depth: int = 0) -> Finding | None:
    """`inspect_member(name, data, depth)` handles non-XML members."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        return Finding.cannot_inspect(f"the archive is damaged ({e})")
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            return Finding.cannot_inspect(f"the archive has more than {MAX_MEMBERS} files")
        if sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED:
            return Finding.cannot_inspect("the archive is too large to unpack")
        for info in infos:
            if info.flag_bits & 0x1:
                return Finding.cannot_inspect("the archive is password protected")
            try:
                member = zf.read(info)
            except Exception as e:  # noqa: BLE001
                return Finding.cannot_inspect(f"{info.filename} couldn't be unpacked ({e})")
            if info.filename.lower().endswith(_XML_SUFFIXES):
                text = _TAG.sub(b"", member).decode("utf-8", "replace")
                m = marker.find(text)
                if m:
                    return Finding(where=_describe(info.filename), excerpt=m.excerpt())
                continue
            found = inspect_member(info.filename, member, depth + 1)
            if found:
                if found.uninspectable:
                    return Finding.cannot_inspect(f"{info.filename}: {found.reason}")
                return Finding(where=f"{info.filename} ({found.where})", excerpt=found.excerpt)
    return None


def _describe(member: str) -> str:
    if member.startswith("word/"):
        return "document text"
    if member.startswith("ppt/slides/"):
        return f"slide {re.sub(r'[^0-9]', '', member.rsplit('/', 1)[-1]) or '?'}"
    if member.startswith("xl/"):
        return "spreadsheet"
    return member
