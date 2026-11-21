// Marker rules. A line-for-line port of src/upload_guard/detect/marker.py.
// Both run against tests/vectors/marker_cases.json, so keep them in step.
(function (root) {
  "use strict";

  const MARKER = "WALLED_SENSITIVE";
  const EXCERPT_RADIUS = 40;

  // Zero-width and other invisible characters that could hide the marker.
  const INVISIBLE = /[­᠎​-‏⁠-⁤﻿]/gu;

  const EXACT = /WALLED_SENSITIVE/iu;

  // Per-letter character classes for OCR mode.
  const OCR_CLASS = {
    W: "(?:W|VV)",
    A: "[A4]",
    L: "[L1I|!]",
    E: "[E3]",
    D: "D",
    S: "[S5$]",
    N: "N",
    I: "[I1L|!]",
    T: "[T7]",
    V: "V",
  };
  const GAP = "[^A-Za-z0-9]?";
  const WORD_GAP = "[^A-Za-z0-9]{0,3}";
  const ocrWord = (word) => [...word].map((c) => OCR_CLASS[c]).join(GAP);
  const OCR = new RegExp(ocrWord("WALLED") + WORD_GAP + ocrWord("SENSITIVE"), "iu");

  function normalize(text) {
    return String(text).normalize("NFKC").replace(INVISIBLE, "");
  }

  // Returns {start, end, text, matched} (offsets into the normalised text) or null.
  function find(text, ocr = false) {
    if (!text) return null;
    const norm = normalize(text);
    const m = (ocr ? OCR : EXACT).exec(norm);
    if (!m) return null;
    return { start: m.index, end: m.index + m[0].length, text: norm, matched: m[0] };
  }

  function has(text, ocr = false) {
    return find(text, ocr) !== null;
  }

  function makeExcerpt(text, start, end, radius = EXCERPT_RADIUS) {
    const a = Math.max(0, start - radius);
    const b = Math.min(text.length, end + radius);
    const snippet = text.slice(a, b).split(/\s+/).filter(Boolean).join(" ");
    return (a > 0 ? "…" : "") + snippet + (b < text.length ? "…" : "");
  }

  const api = { MARKER, normalize, find, has, makeExcerpt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.UploadGuardMarker = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
