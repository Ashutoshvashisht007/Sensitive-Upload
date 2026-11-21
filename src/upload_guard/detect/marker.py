"""Marker rules: the single source of truth for what counts as WALLED_SENSITIVE.

`extension/content/marker.js` is a line-for-line port. Both are run against
`tests/vectors/marker_cases.json` so they can't drift apart.

Two modes:
- exact (prompts, text files, metadata): NFKC-normalise, drop invisible
  characters, then match WALLED_SENSITIVE case-insensitively.
- ocr (text that came out of OCR): also accept the usual OCR confusions
  (1/l/|/! for I and L, 5/$ for S, 4 for A, 3 for E, 7 for T, VV for W) and
  stray punctuation or spaces between letters.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MARKER = "WALLED_SENSITIVE"
EXCERPT_RADIUS = 40

# Zero-width and other invisible characters that could hide the marker.
_INVISIBLE = re.compile("[­᠎​-‏⁠-⁤﻿]")

_EXACT = re.compile("WALLED_SENSITIVE", re.IGNORECASE)

# Per-letter character classes for OCR mode.
_OCR_CLASS = {
    "W": "(?:W|VV)",
    "A": "[A4]",
    "L": "[L1I|!]",
    "E": "[E3]",
    "D": "D",
    "S": "[S5$]",
    "N": "N",
    "I": "[I1L|!]",
    "T": "[T7]",
    "V": "V",
}
_GAP = "[^A-Za-z0-9]?"  # one optional stray character between letters
_WORD_GAP = "[^A-Za-z0-9]{0,3}"  # underscore read as space, dash, nothing...


def _ocr_word(word: str) -> str:
    return _GAP.join(_OCR_CLASS[c] for c in word)


_OCR = re.compile(_ocr_word("WALLED") + _WORD_GAP + _ocr_word("SENSITIVE"), re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    start: int  # offsets into the normalised text
    end: int
    text: str  # the normalised text that was searched
    matched: str

    def excerpt(self, radius: int = EXCERPT_RADIUS) -> str:
        return make_excerpt(self.text, self.start, self.end, radius)


def normalize(text: str) -> str:
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


def find(text: str, ocr: bool = False) -> Match | None:
    """Return the first marker hit in `text`, or None."""
    if not text:
        return None
    norm = normalize(text)
    m = (_OCR if ocr else _EXACT).search(norm)
    if m is None:
        return None
    return Match(m.start(), m.end(), norm, m.group(0))


def has(text: str, ocr: bool = False) -> bool:
    return find(text, ocr) is not None


def make_excerpt(text: str, start: int, end: int, radius: int = EXCERPT_RADIUS) -> str:
    """Up to `radius` characters either side of the hit, whitespace collapsed."""
    a, b = max(0, start - radius), min(len(text), end + radius)
    snippet = " ".join(text[a:b].split())
    return ("…" if a > 0 else "") + snippet + ("…" if b < len(text) else "")
