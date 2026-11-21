"""PDFs: extract each page's text; render and OCR pages that look scanned."""

from __future__ import annotations

import io
import logging

from . import marker, ocr
from .result import Finding
from .text import scan_raw

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 20  # fewer than this on a page → probably a scan


def inspect(data: bytes, ocr_max_pages: int = 15) -> Finding | None:
    try:
        pages, meta = _extract_pypdf(data)
    except _Encrypted:
        return Finding.cannot_inspect("the PDF is password protected")
    except Exception as e:  # noqa: BLE001 - malformed PDF: try a second parser
        log.info("pypdf failed (%s), trying pdfium", e)
        try:
            pages, meta = _extract_pdfium(data), ""
        except Exception as e2:  # noqa: BLE001
            return Finding.cannot_inspect(f"the PDF couldn't be read ({e2})")

    m = marker.find(meta)
    if m:
        return Finding(where="document metadata", excerpt=m.excerpt())
    for i, (text, has_images) in enumerate(pages, start=1):
        m = marker.find(text)
        if m:
            return Finding(where=f"page {i}", excerpt=m.excerpt())
    if scan_raw(data):  # annotations, form fields, uncompressed metadata...
        return Finding(where="document data")

    # Second pass: OCR pages that are scans or carry images.
    to_ocr = [i for i, (text, has_images) in enumerate(pages) if has_images or len(text.strip()) < MIN_TEXT_CHARS]
    skipped = max(0, len(to_ocr) - ocr_max_pages)
    for idx in to_ocr[:ocr_max_pages]:
        try:
            ocr_text = ocr.ocr_bytes(_render_page_png(data, idx))
        except ocr.OcrUnavailable as e:
            return Finding.cannot_inspect(f"scanned pages couldn't be read ({e})")
        except Exception as e:  # noqa: BLE001
            return Finding.cannot_inspect(f"page {idx + 1} couldn't be rendered ({e})")
        m = marker.find(ocr_text, ocr=True)
        if m:
            return Finding(where=f"scanned page {idx + 1}", excerpt=m.excerpt())
    if skipped:
        return Finding.cannot_inspect(f"{skipped} scanned pages are beyond the OCR limit of {ocr_max_pages}")
    return None


class _Encrypted(Exception):
    pass


def _extract_pypdf(data: bytes) -> tuple[list[tuple[str, bool]], str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise _Encrypted()
        except _Encrypted:
            raise
        except Exception as e:  # noqa: BLE001 - unsupported encryption
            raise _Encrypted() from e
    meta_parts = []
    try:
        meta_parts = [str(v) for v in (reader.metadata or {}).values()]
        xmp = reader.xmp_metadata
        if xmp is not None:
            meta_parts.append(xmp.stream.get_data().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        pass
    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a broken page shouldn't hide the others
            text = ""
        pages.append((text, _page_has_images(page)))
    return pages, "\n".join(meta_parts)


def _page_has_images(page) -> bool:
    """True if the page (or a form XObject on it) draws a raster image."""
    try:
        xobjects = page.get("/Resources", {}).get_object().get("/XObject")
        if not xobjects:
            return False
        stack, seen = [xobjects.get_object()], 0
        while stack and seen < 50:
            xo = stack.pop()
            for ref in xo.values():
                obj = ref.get_object()
                seen += 1
                if obj.get("/Subtype") == "/Image":
                    return True
                if obj.get("/Subtype") == "/Form":
                    inner = obj.get("/Resources", {}).get_object().get("/XObject")
                    if inner:
                        stack.append(inner.get_object())
    except Exception:  # noqa: BLE001
        return True  # unsure → let OCR have a look
    return False


def _extract_pdfium(data: bytes) -> list[tuple[str, bool]]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            out.append((textpage.get_text_range(), True))
            textpage.close()
            page.close()
        return out
    finally:
        pdf.close()


def _render_page_png(data: bytes, index: int, scale: float = 2.0) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[index]
        img = page.render(scale=scale).to_pil()
        page.close()
    finally:
        pdf.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
