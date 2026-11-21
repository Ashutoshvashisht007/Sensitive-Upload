import shutil
import sys
from pathlib import Path

import pytest

from upload_guard.config import Config
from upload_guard.detect import filetype, inspect_file, inspect_text, ocr

FIX = Path(__file__).parent / "fixtures"

FLAGGED = {
    "marker.txt": "line 2",
    "marker_utf16.txt": "line 2",
    "marker_zero_width.txt": "line 1",
    "marker.pdf": "page 2",
    "scanned_marker.pdf": "page 2 (scanned text)",
    "marker.png": "text in the image",
    "marker_photo.jpg": "text in the image",
    "marker_meta.png": "image metadata",
    "marker.docx": "document text",
}
CLEAN = ["clean.txt", "clean.pdf", "scanned_clean.pdf", "clean.png"]


def inspect(name: str, **kw):
    return inspect_file(name, "", (FIX / name).read_bytes(), **kw)


@pytest.mark.parametrize("name,where", FLAGGED.items())
def test_flagged_fixture(name, where):
    found = inspect(name)
    assert found is not None and not found.uninspectable
    assert found.where == where


@pytest.mark.parametrize("name", CLEAN)
def test_clean_fixture(name):
    assert inspect(name) is None


def test_excerpt_contains_marker():
    found = inspect("marker.pdf")
    assert "WALLED_SENSITIVE" in found.excerpt and len(found.excerpt) < 120


def test_inspect_text():
    assert inspect_text("hello") is None
    assert inspect_text("hi walled_sensitive there").where == "prompt"


def test_too_large_is_uninspectable():
    cfg = Config(max_inspect_mb=1)
    found = inspect_file("big.bin", "", b"x" * (2 * 1024 * 1024), config=cfg)
    assert found.uninspectable and "1 MB" in found.reason


def test_encrypted_pdf_is_uninspectable(tmp_path):
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    for p in PdfReader(FIX / "marker.pdf").pages:
        w.add_page(p)
    w.encrypt(user_password="secret", owner_password="owner")
    out = tmp_path / "locked.pdf"
    with open(out, "wb") as f:
        w.write(f)
    found = inspect_file("locked.pdf", "application/pdf", out.read_bytes())
    assert found.uninspectable and "password" in found.reason


def test_corrupt_pdf_is_uninspectable():
    found = inspect_file("broken.pdf", "application/pdf", b"%PDF-1.7\n garbage \x00\x01\x02")
    assert found is not None and found.uninspectable


def test_text_mentioning_pdf_header_is_still_text():
    data = b"notes about the %PDF-1.4 header\nWALLED_SENSITIVE\n"
    assert inspect_file("notes.txt", "text/plain", data).where == "line 2"


def test_unknown_binary_raw_scan():
    assert inspect_file("x.bin", "", b"\x00\x01 WALLED_SENSITIVE \x02").where == "file contents"
    assert inspect_file("x.bin", "", b"\x00\x01\x02\x03") is None


def test_zip_with_nested_text(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.md", "fine")
        z.writestr("data/secret.txt", "row 1\nWALLED_SENSITIVE\n")
    found = inspect_file("bundle.zip", "", buf.getvalue())
    assert found.where == "data/secret.txt (line 2)"


@pytest.mark.parametrize(
    "head,kind",
    [
        (b"%PDF-1.7", filetype.PDF),
        (b"\x89PNG\r\n\x1a\n", filetype.IMAGE),
        (b"\xff\xd8\xff\xe0", filetype.IMAGE),
        (b"GIF89a", filetype.IMAGE),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", filetype.IMAGE),
        (b"\x00\x00\x00\x18ftypheic", filetype.IMAGE),
        (b"PK\x03\x04", filetype.ZIP),
        (b"plain words", filetype.TEXT),
        (b"\x00\x01\x02\x03binary", filetype.BINARY),
    ],
)
def test_sniff(head, kind):
    assert filetype.sniff(head + b"\x00" * 16 if kind != filetype.TEXT else head) == kind


@pytest.mark.skipif(sys.platform != "darwin", reason="Vision is macOS only")
def test_vision_reads_marker():
    text = ocr._vision((FIX / "marker.png").read_bytes())
    assert "WALLED_SENSITIVE" in text


@pytest.mark.skipif(not (shutil.which("tesseract") or Path("/opt/homebrew/bin/tesseract").exists()),
                    reason="tesseract not installed")
def test_tesseract_fallback(monkeypatch):
    def broken(_data):
        raise RuntimeError("vision disabled for this test")

    monkeypatch.setattr(ocr, "_vision", broken)
    found = inspect("marker.png")
    assert found is not None and found.where == "text in the image"


def test_no_ocr_engine_means_uninspectable(monkeypatch):
    def broken(_data):
        raise RuntimeError("no engine")

    monkeypatch.setattr(ocr, "_vision", broken)
    monkeypatch.setattr(ocr, "_tesseract", broken)
    found = inspect("clean.png")
    assert found is not None and found.uninspectable
