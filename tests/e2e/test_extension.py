"""Browser tests: the real extension + the real native host, against mock pages.

Chromium is started with the unpacked extension. The mock pages are served AS
https://chatgpt.com/ and https://www.perplexity.ai/ (request routing, nothing
leaves the machine), so the content scripts run exactly as on the real sites.
The native host is the real one, registered in the test profile; only its
dialog is scripted (UPLOAD_GUARD_DIALOG=auto-file:...), so each test decides
whether the "user" clicks Continue or Cancel.

Skipped when Playwright's Chromium isn't installed (`uv run playwright install chromium`).
"""

from __future__ import annotations

import base64
import glob
import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from upload_guard.config import HOST_NAME  # noqa: E402
from upload_guard.install import extension_id, extension_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
MOCKS = ROOT / "tests" / "mock_sites"
FIX = ROOT / "tests" / "fixtures"
MARKER_PROMPT = "please summarise the WALLED_SENSITIVE board notes"

SITES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "mock": "chatgpt.html",
        "label": "ChatGPT",
        "composer": "#prompt-textarea",
        "send": '[data-testid="send-button"]',
        "response": '[data-message-author-role="assistant"] p',
        "not_response": '[data-message-author-role="user"] div',
        "color": "rgb(15, 118, 110)",  # light theme
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "mock": "perplexity.html",
        "label": "Perplexity",
        "composer": "#ask-input",
        "send": 'button[aria-label="Submit"]',
        "response": "#markdown-content-0 li",
        "not_response": "h1",
        "color": "rgb(94, 234, 212)",  # dark theme
    },
}


def find_chromium() -> str | None:
    if os.environ.get("UPLOAD_GUARD_CHROMIUM"):
        return os.environ["UPLOAD_GUARD_CHROMIUM"]
    try:
        with sync_playwright() as p:
            if Path(p.chromium.executable_path).exists():
                return p.chromium.executable_path
    except Exception:  # noqa: BLE001
        pass
    pattern = str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*")
    found = sorted(glob.glob(pattern), key=lambda p: int(p.split("chromium-")[1].split("/")[0]))
    found += sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    return found[-1] if found else None


CHROMIUM = find_chromium()
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Playwright Chromium not installed")


def _serve(mock: Path):
    return lambda route: route.fulfill(path=str(mock), content_type="text/html")


class Harness:
    def __init__(self, pw, tmp: Path, with_host: bool = True):
        self.tmp = tmp
        self.answer_file = tmp / "answer"
        self.dialog_log = tmp / "answer.log"
        profile = tmp / "profile"
        hosts_dir = profile / "NativeMessagingHosts"
        hosts_dir.mkdir(parents=True)
        if with_host:
            launcher = tmp / "host.sh"
            launcher.write_text(
                "#!/bin/sh\n"
                f'export UPLOAD_GUARD_DIALOG="auto-file:{self.answer_file}"\n'
                f'export UPLOAD_GUARD_HOME="{tmp / "home"}"\n'
                f'exec "{ROOT / ".venv/bin/python"}" -m upload_guard.native_host "$@"\n'
            )
            os.chmod(launcher, 0o755)
            manifest = {
                "name": HOST_NAME, "description": "test", "path": str(launcher), "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id(extension_key())}/"],
            }
            (hosts_dir / f"{HOST_NAME}.json").write_text(json.dumps(manifest))
        self.ctx = pw.chromium.launch_persistent_context(
            str(profile), executable_path=CHROMIUM, headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        for site in SITES.values():
            self.ctx.route(site["url"] + "**", _serve(MOCKS / site["mock"]))
        self.sw = self.ctx.service_workers[0] if self.ctx.service_workers else self.ctx.wait_for_event("serviceworker")

    def answer(self, value: str) -> None:
        self.answer_file.write_text(value)

    def dialogs(self) -> list[dict]:
        if not self.dialog_log.exists():
            return []
        return [json.loads(line) for line in self.dialog_log.read_text().splitlines()]

    def open(self, name: str):
        page = self.ctx.new_page()
        page.goto(SITES[name]["url"])
        page.wait_for_selector(SITES[name]["composer"])
        return page

    def close(self) -> None:
        self.ctx.close()


@pytest.fixture(scope="module")
def pw():
    p = sync_playwright().start()
    yield p
    p.stop()


@pytest.fixture(scope="module")
def harness(pw, tmp_path_factory):
    h = Harness(pw, tmp_path_factory.mktemp("e2e"))
    assert h.sw.evaluate("ping()")["ok"] is True
    yield h
    h.close()


@pytest.fixture(params=list(SITES))
def site(request, harness):
    page = harness.open(request.param)
    yield request.param, page
    page.close()


# ---------------------------------------------------------------- helpers

def sent(page) -> list[str]:
    return page.locator("#sent-log li").all_inner_texts()


def uploads(page) -> list[str]:
    return page.locator("#upload-log li").all_inner_texts()


def digest(name: str) -> str:
    return hashlib.sha256((FIX / name).read_bytes()).hexdigest()[:12]


def type_prompt(page, name, text):
    page.click(SITES[name]["composer"])
    page.keyboard.type(text)


def wait_dialogs(h, n, page, timeout=10000):
    page.wait_for_function("1", timeout=50)  # flush
    deadline = timeout / 100
    for _ in range(int(deadline)):
        if len(h.dialogs()) >= n:
            return h.dialogs()
        page.wait_for_timeout(100)
    raise AssertionError(f"expected {n} dialogs, saw {len(h.dialogs())}")


def wait_count(page, selector, n, timeout=10000):
    page.wait_for_function(f"document.querySelectorAll('{selector}').length >= {n}", timeout=timeout)


def js_file(name: str, mime: str = "") -> list:
    return [name, base64.b64encode((FIX / name).read_bytes()).decode(), mime]


DISPATCH_DROP = """async ([files, selector]) => {
  const dt = new DataTransfer();
  for (const [name, b64, mime] of files) {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    dt.items.add(new File([bytes], name, { type: mime }));
  }
  const target = document.querySelector(selector);
  for (const type of ["dragenter", "dragover", "drop"]) {
    target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, composed: true, dataTransfer: dt }));
  }
}"""

DISPATCH_PASTE = """async ([files, selector]) => {
  const dt = new DataTransfer();
  for (const [name, b64, mime] of files) {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    dt.items.add(new File([bytes], name, { type: mime }));
  }
  document.querySelector(selector).dispatchEvent(
    new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: dt }));
}"""


def pick_files(page, name, paths):
    """Use each site's own attach flow: ChatGPT's hidden input, Perplexity's detached one."""
    if name == "chatgpt":
        page.set_input_files('input[type="file"]', paths)
    else:
        with page.expect_file_chooser() as fc:
            page.click('button[aria-label="Attach"]')
        fc.value.set_files(paths)


# ---------------------------------------------------------------- prompts

def test_clean_prompt_sends_immediately(harness, site):
    name, page = site
    before = len(harness.dialogs())
    type_prompt(page, name, "hello, what's the weather like")
    page.keyboard.press("Enter")
    assert sent(page) == ["hello, what's the weather like"]  # no waiting needed: nothing was held
    assert len(harness.dialogs()) == before


def test_marker_prompt_cancel_then_continue(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("cancel")
    type_prompt(page, name, MARKER_PROMPT)
    page.keyboard.press("Enter")
    d = wait_dialogs(harness, n + 1, page)[-1]
    assert f"Your prompt to {SITES[name]['label']} contains WALLED_SENSITIVE." in d["body"]
    assert "WALLED_SENSITIVE board notes" in d["body"]
    page.wait_for_timeout(300)
    assert sent(page) == []
    assert MARKER_PROMPT in page.inner_text(SITES[name]["composer"])  # still there to edit

    harness.answer("continue")
    page.click(SITES[name]["composer"])
    page.keyboard.press("Enter")
    wait_count(page, "#sent-log li", 1)
    assert sent(page) == [MARKER_PROMPT]  # unchanged
    assert len(harness.dialogs()) == n + 2


def test_marker_prompt_via_send_button(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("continue")
    type_prompt(page, name, MARKER_PROMPT)
    page.click(SITES[name]["send"])
    wait_count(page, "#sent-log li", 1)
    assert sent(page) == [MARKER_PROMPT]
    assert len(harness.dialogs()) == n + 1


def test_shift_enter_is_not_a_submit(harness, site):
    name, page = site
    n = len(harness.dialogs())
    type_prompt(page, name, MARKER_PROMPT)
    page.keyboard.press("Shift+Enter")
    page.wait_for_timeout(300)
    assert len(harness.dialogs()) == n and sent(page) == []


# ---------------------------------------------------------------- file picker

def test_clean_file_uploads_without_dialog(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("cancel")  # would block if a dialog appeared
    pick_files(page, name, [str(FIX / "clean.pdf"), str(FIX / "clean.png")])
    wait_count(page, "#upload-log li", 2)
    assert [u.split("|")[0] for u in uploads(page)] == ["clean.pdf", "clean.png"]
    assert len(harness.dialogs()) == n


def test_marker_file_cancel_then_reselect_and_continue(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("cancel")
    pick_files(page, name, [str(FIX / "marker.txt")])
    d = wait_dialogs(harness, n + 1, page)[-1]
    assert d["body"].startswith(f"“marker.txt” (line 2) contains WALLED_SENSITIVE and is about to be uploaded to {SITES[name]['label']}.")
    page.wait_for_timeout(300)
    assert uploads(page) == []

    harness.answer("continue")
    pick_files(page, name, [str(FIX / "marker.txt")])  # same file again: change must fire again
    wait_count(page, "#upload-log li", 1)
    assert uploads(page) == [f"marker.txt|{(FIX / 'marker.txt').stat().st_size}|{digest('marker.txt')}|picker"]


@pytest.mark.parametrize("fixture", ["marker.pdf", "marker.png", "scanned_marker.pdf"])
def test_pdf_and_image_markers_ask_first(harness, site, fixture):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("continue")
    pick_files(page, name, [str(FIX / fixture)])
    d = wait_dialogs(harness, n + 1, page, timeout=20000)[-1]
    assert f"“{fixture}”" in d["body"]
    wait_count(page, "#upload-log li", 1)
    assert uploads(page)[0].split("|")[2] == digest(fixture)  # bytes unchanged


def test_mixed_batch_cancel_keeps_clean_files(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("cancel")
    pick_files(page, name, [str(FIX / "clean.txt"), str(FIX / "marker.pdf")])
    d = wait_dialogs(harness, n + 1, page)[-1]
    assert "Cancel removes this file; the other 1 file uploads normally." in d["body"]
    wait_count(page, "#upload-log li", 1)
    page.wait_for_timeout(300)
    assert [u.split("|")[0] for u in uploads(page)] == ["clean.txt"]


# ---------------------------------------------------------------- drop and paste

def test_drop_marker_cancel_and_continue(harness, site):
    name, page = site
    target = SITES[name]["composer"]
    n = len(harness.dialogs())
    harness.answer("cancel")
    page.evaluate(DISPATCH_DROP, [[js_file("marker.png", "image/png")], target])
    wait_dialogs(harness, n + 1, page)
    page.wait_for_timeout(300)
    assert uploads(page) == []
    if name == "chatgpt":
        assert "show" not in page.get_attribute("#drop-overlay", "class")  # overlay cleared

    harness.answer("continue")
    page.evaluate(DISPATCH_DROP, [[js_file("marker.png", "image/png")], target])
    wait_count(page, "#upload-log li", 1)
    assert uploads(page)[0].endswith(f"{digest('marker.png')}|drop")


def test_drop_clean_file_passes(harness, site):
    name, page = site
    n = len(harness.dialogs())
    page.evaluate(DISPATCH_DROP, [[js_file("clean.txt", "text/plain")], SITES[name]["composer"]])
    wait_count(page, "#upload-log li", 1)
    assert len(harness.dialogs()) == n


def test_paste_file_cancel_and_continue(harness, site):
    name, page = site
    target = SITES[name]["composer"]
    n = len(harness.dialogs())
    harness.answer("cancel")
    page.evaluate(DISPATCH_PASTE, [[js_file("marker.pdf", "application/pdf")], target])
    wait_dialogs(harness, n + 1, page)
    page.wait_for_timeout(300)
    assert uploads(page) == []

    harness.answer("continue")
    page.evaluate(DISPATCH_PASTE, [[js_file("marker.pdf", "application/pdf")], target])
    wait_count(page, "#upload-log li", 1)
    assert uploads(page)[0].endswith(f"{digest('marker.pdf')}|paste")


# ---------------------------------------------------------------- response colour

def test_responses_recoloured(harness, site):
    name, page = site
    colour = page.evaluate(f"getComputedStyle(document.querySelector('{SITES[name]['response']}')).color")
    assert colour == SITES[name]["color"]
    other = page.evaluate(f"getComputedStyle(document.querySelector('{SITES[name]['not_response']}')).color")
    assert other != SITES[name]["color"]  # user messages keep the site's colour


def test_streamed_response_is_recoloured(harness, site):
    """Text added after load (streaming) picks up the colour too."""
    name, page = site
    sel = SITES[name]["response"].split(" ")[0]
    colour = page.evaluate(f"""() => {{
        const p = document.createElement('p'); p.textContent = 'streamed token';
        document.querySelector('{sel}').appendChild(p);
        return getComputedStyle(p).color;
    }}""")
    assert colour == SITES[name]["color"]


# ---------------------------------------------------------------- app not running

def test_fail_closed_when_host_missing(pw, tmp_path):
    h = Harness(pw, tmp_path, with_host=False)
    try:
        page = h.open("chatgpt")
        type_prompt(page, "chatgpt", "a clean prompt")
        page.keyboard.press("Enter")
        assert sent(page) == ["a clean prompt"]  # clean prompts never depend on the app

        type_prompt(page, "chatgpt", MARKER_PROMPT)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        assert sent(page) == ["a clean prompt"]  # marker prompt blocked

        page.set_input_files('input[type="file"]', [str(FIX / "clean.txt")])
        page.wait_for_timeout(1000)
        assert uploads(page) == []  # files can't be inspected → blocked
        assert h.sw.evaluate("state.connected") is False
    finally:
        h.close()


def test_cancel_then_edit_and_send_clean(harness, site):
    name, page = site
    n = len(harness.dialogs())
    harness.answer("cancel")
    type_prompt(page, name, MARKER_PROMPT)
    page.keyboard.press("Enter")
    wait_dialogs(harness, n + 1, page)
    page.click(SITES[name]["composer"])
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("please summarise the board notes")
    page.keyboard.press("Enter")
    assert sent(page) == ["please summarise the board notes"]
    assert len(harness.dialogs()) == n + 1
