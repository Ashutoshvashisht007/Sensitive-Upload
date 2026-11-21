"""Generate test fixtures (tests/fixtures) and demo files (demo/).

    uv run python scripts/make_fixtures.py

Needs the dev dependencies (reportlab) and Pillow. Uses macOS system fonts
when present and falls back to Pillow's built-in font elsewhere.
"""

from __future__ import annotations

import io
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.PngImagePlugin import PngInfo
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
DEMO = ROOT / "demo"
MARKER = "WALLED_SENSITIVE"

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
MARKER_FELT = "/System/Library/Fonts/MarkerFelt.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    for p in (path, ARIAL):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# ---------- text ----------

def write_texts() -> None:
    (FIX / "marker.txt").write_text(f"Quarterly numbers\nInternal only: {MARKER}\nDo not share.\n")
    (FIX / "marker_utf16.txt").write_bytes(f"UTF-16 file\n{MARKER} inside\n".encode("utf-16"))
    zw = "WALL​ED_SEN‍SITIVE"
    (FIX / "marker_zero_width.txt").write_text(f"Looks harmless: {zw}\n")
    (FIX / "clean.txt").write_text("Shopping list\n- eggs\n- milk\n")

    (DEMO / "notes.txt").write_text(
        "Project Falcon - sync notes\n"
        "\n"
        "- Launch moved to the second week of March.\n"
        "- Pricing tiers still open; finance wants one more pass.\n"
        f"- Classification: {MARKER} (customer list attached separately)\n"
        "- Next sync: Thursday.\n"
    )


# ---------- PDF ----------

def _pdf(pages: list[list[str]], title: str = "Document") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    w, h = A4
    for lines in pages:
        y = h - 30 * mm
        for i, line in enumerate(lines):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 16 if i == 0 else 11)
            c.drawString(25 * mm, y, line)
            y -= 10 * mm if i == 0 else 7 * mm
        c.showPage()
    c.save()
    return buf.getvalue()


def _image_pdf(images: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    first, rest = images[0], images[1:]
    first.save(buf, format="PDF", save_all=True, append_images=rest, resolution=150)
    return buf.getvalue()


def _scan_page(lines: list[str]) -> Image.Image:
    """A 'scanned' A4 page at 150 dpi: text rendered as pixels, slightly noisy and tilted."""
    img = Image.new("L", (1240, 1754), 250)
    d = ImageDraw.Draw(img)
    f = font(ARIAL, 34)
    y = 160
    for line in lines:
        d.text((140, y), line, fill=25, font=f)
        y += 64
    rng = random.Random(7)
    px = img.load()
    for _ in range(25000):
        x, yy = rng.randrange(img.width), rng.randrange(img.height)
        px[x, yy] = rng.randrange(170, 250)
    return img.rotate(0.6, fillcolor=250, resample=Image.BICUBIC).convert("RGB")


CONTRACT_P1 = [
    "Services Agreement",
    "This agreement is made between Northwind Ltd and Contoso Analytics.",
    "1. Scope. Contoso will provide data analysis services as described",
    "   in Schedule A.",
    "2. Term. The agreement runs for twelve months from signature.",
    "3. Fees. Fees are payable monthly in arrears within 30 days.",
]
CONTRACT_P2 = [
    "Schedule A - Confidential terms",
    f"Classification: {MARKER}",
    "4. Data. Customer data stays in the EU and is deleted after 90 days.",
    "5. Liability. Liability is capped at the fees paid in the prior year.",
    "6. Termination. Either party may end the agreement with 60 days notice.",
]
CONTRACT_P3 = [
    "Signatures",
    "Signed for Northwind Ltd: ____________________",
    "Signed for Contoso Analytics: ____________________",
]
CLEAN_PDF = [
    "Team offsite agenda",
    "09:30  Coffee and intros",
    "10:00  Roadmap review",
    "12:30  Lunch",
    "14:00  Workshops",
    "16:30  Wrap-up",
]


def write_pdfs() -> None:
    (FIX / "marker.pdf").write_bytes(_pdf([CONTRACT_P1, CONTRACT_P2, CONTRACT_P3], "Services Agreement"))
    (FIX / "clean.pdf").write_bytes(_pdf([CLEAN_PDF], "Agenda"))
    scanned = _image_pdf([
        _scan_page(["Scanned memo", "Page one has nothing special on it."]),
        _scan_page(["Scanned memo, page 2", f"Reference: {MARKER}", "Please file this away."]),
    ])
    (FIX / "scanned_marker.pdf").write_bytes(scanned)
    (FIX / "scanned_clean.pdf").write_bytes(_image_pdf([_scan_page(["Scanned memo", "Nothing sensitive here."])]))

    (DEMO / "contract.pdf").write_bytes(_pdf([CONTRACT_P1, CONTRACT_P2, CONTRACT_P3], "Services Agreement"))
    (DEMO / "clean.pdf").write_bytes(_pdf([CLEAN_PDF], "Agenda"))
    (DEMO / "scanned.pdf").write_bytes(scanned)


# ---------- images ----------

def _text_image(lines: list[str], size=(1000, 360), fnt=None, bg=(255, 255, 255), fg=(20, 20, 20)) -> Image.Image:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    f = fnt or font(ARIAL, 48)
    y = 60
    for line in lines:
        d.text((50, y), line, fill=fg, font=f)
        y += int(f.size * 1.5)
    return img


def _whiteboard() -> Image.Image:
    """A whiteboard-ish photo: off-white board, marker handwriting font, a little blur."""
    w, h = 1400, 900
    img = Image.new("RGB", (w, h), (236, 238, 234))
    d = ImageDraw.Draw(img)
    rng = random.Random(3)
    for _ in range(40):  # faint smudges of old ink
        x, y = rng.randrange(w), rng.randrange(h)
        d.ellipse((x, y, x + rng.randrange(40, 160), y + rng.randrange(10, 40)), fill=(226, 229, 225))
    d.rectangle((0, 0, w - 1, h - 1), outline=(150, 150, 150), width=18)
    f_title = font(MARKER_FELT, 72)
    f_body = font(MARKER_FELT, 52)
    d.text((90, 80), "Q3 launch plan", fill=(25, 60, 150), font=f_title)
    body = [
        ("- beta to 50 customers", (30, 30, 30)),
        ("- pricing: 3 tiers", (30, 30, 30)),
        (f"- tag: {MARKER}", (170, 25, 25)),
        ("- owner: platform team", (30, 30, 30)),
    ]
    y = 230
    for text, colour in body:
        d.text((110, y), text, fill=colour, font=f_body)
        y += 120
    d.line((90, 190, 700, 190), fill=(25, 60, 150), width=6)
    return img.rotate(-1.5, fillcolor=(236, 238, 234), resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.8))


def _noisy_photo() -> Image.Image:
    rng = random.Random(11)
    w, h = 900, 500
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            base = 150 + (x * 60) // w
            n = rng.randrange(-35, 35)
            px[x, y] = (base + n, base + n - 10, base + n - 20)
    d = ImageDraw.Draw(img)
    f = font(ARIAL, 28)
    d.text((60, 200), f"label: {MARKER}", fill=(10, 10, 10), font=f)
    d.text((60, 260), "photo taken on site, row 4", fill=(10, 10, 10), font=f)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def write_images() -> None:
    _text_image(["Screenshot of a report", f"Status: {MARKER}"]).save(FIX / "marker.png")
    _noisy_photo().save(FIX / "marker_photo.jpg", quality=80)
    meta = PngInfo()
    meta.add_text("Comment", f"classification={MARKER}")
    _text_image(["An ordinary picture"]).save(FIX / "marker_meta.png", pnginfo=meta)
    clean = _text_image(["Holiday photo", "Beach, sunset, 7pm"])
    clean.save(FIX / "clean.png")

    _whiteboard().save(DEMO / "whiteboard.png")
    clean.save(DEMO / "clean.png")


# ---------- office ----------

def write_docx() -> None:
    """A minimal .docx where the marker is split across two runs, as Word does."""
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>Board pack, item 3: </w:t></w:r><w:r><w:t>WALLED_</w:t></w:r>"
        "<w:r><w:t>SENSITIVE</w:t></w:r></w:p></w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(FIX / "marker.docx", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("word/document.xml", doc)


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    DEMO.mkdir(parents=True, exist_ok=True)
    write_texts()
    write_pdfs()
    write_images()
    write_docx()
    for d in (FIX, DEMO):
        print(d.relative_to(ROOT), sorted(p.name for p in d.iterdir()))


if __name__ == "__main__":
    main()
