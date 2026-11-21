"""Work out what a file really is: magic bytes first, then extension and MIME."""

from __future__ import annotations

import os

PDF, IMAGE, ZIP, TEXT, BINARY = "pdf", "image", "zip", "text", "binary"

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
_TEXT_EXT = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".sh", ".sql", ".ini",
}


def sniff(data: bytes, name: str = "", mime: str = "") -> str:
    head = data[:32]
    if head.startswith(b"%PDF") or b"%PDF-" in data[:1024]:
        return PDF
    if (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")  # JPEG
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or (head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif"))
        or head[:4] in (b"II*\x00", b"MM\x00*")  # TIFF
        or head[:2] == b"BM"
    ):
        return IMAGE
    if head.startswith(b"PK\x03\x04"):
        return ZIP

    ext = os.path.splitext(name.lower())[1]
    mime = (mime or "").lower()
    if mime == "application/pdf" or ext == ".pdf":
        return PDF  # claims to be a PDF but has no header: let the PDF parser decide
    if ext in _TEXT_EXT or mime.startswith("text/") or looks_like_text(data):
        return TEXT
    if ext in _IMAGE_EXT or mime.startswith("image/"):
        return IMAGE
    return BINARY


def looks_like_text(data: bytes) -> bool:
    sample = data[:8192]
    if not sample:
        return True
    if sample.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # a multi-byte character cut off at the end of the sample is fine
        return e.start >= len(sample) - 3
