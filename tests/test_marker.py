import json
from pathlib import Path

import pytest

from upload_guard.detect import marker

VECTORS = json.loads((Path(__file__).parent / "vectors" / "marker_cases.json").read_text())


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_marker_case(case):
    m = marker.find(case["text"], ocr=case.get("ocr", False))
    assert (m is not None) == case["found"]
    if case["found"]:
        assert m.matched == case["matched"]


@pytest.mark.parametrize("case", VECTORS["excerpts"], ids=lambda c: c["name"])
def test_excerpt(case):
    m = marker.find(case["text"])
    assert m is not None
    assert m.excerpt() == case["excerpt"]
