"""Images: OCR the pixels, and check text metadata (PNG text chunks, EXIF, XMP)."""

from __future__ import annotations

import io

from . import marker, ocr
from .result import Finding
from .text import scan_raw

# Small images OCR better when enlarged; this is the size we scale up to.
_UPSCALE_BELOW = 1200


def inspect(data: bytes) -> Finding | None:
    meta = _metadata_text(data)
    if marker.has(meta) or scan_raw(data):
        return Finding(where="image metadata")

    try:
        text = ocr.ocr_bytes(data)
    except ocr.OcrUnavailable as e:
        return Finding.cannot_inspect(f"text in the image couldn't be read ({e})")
    m = marker.find(text, ocr=True)
    if m is None:
        enlarged = _upscaled_png(data)
        if enlarged is not None:
            try:
                m = marker.find(ocr.ocr_bytes(enlarged), ocr=True)
            except ocr.OcrUnavailable:
                m = None
    if m is None:
        return None
    return Finding(where="text in the image", excerpt=m.excerpt())


def _metadata_text(data: bytes) -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            parts = [str(v) for v in img.info.values() if isinstance(v, (str, bytes))]
            exif = img.getexif()
            for v in exif.values():
                if isinstance(v, bytes):
                    parts.append(v.decode("utf-8", "replace").replace("\x00", ""))
                elif isinstance(v, str):
                    parts.append(v)
            return "\n".join(parts)
    except Exception:  # noqa: BLE001 - unreadable metadata is not a finding by itself
        return ""


def _upscaled_png(data: bytes) -> bytes | None:
    """A 2-3x enlarged copy for small images, or None if it's big enough already."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            longest = max(img.size)
            if longest >= _UPSCALE_BELOW or longest == 0:
                return None
            factor = 3 if longest < _UPSCALE_BELOW / 2 else 2
            big = img.convert("RGB").resize((img.width * factor, img.height * factor), Image.LANCZOS)
            buf = io.BytesIO()
            big.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
