"""OCR: macOS Vision first (offline, accurate), tesseract as a fallback."""

from __future__ import annotations

import io
import os
import logging
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger(__name__)


class OcrUnavailable(Exception):
    """No OCR engine could read the image."""


def ocr_bytes(data: bytes) -> str:
    """Return the text recognised in an encoded image (PNG, JPEG, HEIC...)."""
    errors = []
    if sys.platform == "darwin":
        try:
            return _vision(data)
        except Exception as e:  # noqa: BLE001 - any Vision failure falls through to tesseract
            errors.append(f"vision: {e}")
            log.warning("Vision OCR failed: %s", e)
    try:
        return _tesseract(data)
    except Exception as e:  # noqa: BLE001
        errors.append(f"tesseract: {e}")
    raise OcrUnavailable("; ".join(errors))


def _vision(data: bytes) -> str:
    import Quartz
    import Vision
    from Foundation import NSData

    src = Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(data, len(data)), None)
    if src is None or Quartz.CGImageSourceGetCount(src) == 0:
        raise ValueError("image could not be decoded")
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        raise ValueError("image could not be decoded")
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)  # keep WALLED_SENSITIVE intact
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(str(err))
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if cands:
            lines.append(cands[0].string())
    return "\n".join(lines)


def _tesseract(data: bytes) -> str:
    exe = shutil.which("tesseract") or (
        "/opt/homebrew/bin/tesseract" if os.path.exists("/opt/homebrew/bin/tesseract") else None
    )
    if not exe:
        raise FileNotFoundError("tesseract not installed")
    from PIL import Image

    # tesseract can't read every format (HEIC, WebP on some builds): hand it a PNG.
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            img.save(tmp.name)
            out = subprocess.run(
                [exe, tmp.name, "stdout"], capture_output=True, text=True, timeout=60, check=True
            )
    return out.stdout
