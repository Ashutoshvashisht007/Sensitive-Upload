"""End to end: Chromium → (our PAC) → the real guard → a local stand-in for the sites.

No extension, no page scripts of ours. Chromium's only setting is the PAC data URL
that `upload-guard install` puts in the system proxy settings, so these tests
exercise the same path a real browser takes. The local Upstream records what
actually arrived, which is how "blocked before it was sent" is checked.

Skipped when Playwright's Chromium isn't installed (`uv run playwright install chromium`).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import socket
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from upload_guard.config import Config  # noqa: E402
from upload_guard.decisions import Decider, ThreadRunner  # noqa: E402
from upload_guard.dialog.auto import FileDialog  # noqa: E402
from upload_guard.proxy.addon import BLOCK_MESSAGE, GuardAddon  # noqa: E402
from upload_guard.proxy.pac import pac_data_url  # noqa: E402
from upload_guard.proxy.server import GuardProxy  # noqa: E402
from upstream import RedirectUpstream, Upstream  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures"

SITES = {
    "chatgpt": dict(url="https://chatgpt.com/", composer='[data-composer-input] [contenteditable="true"]',
                    files='input[aria-label="Attach files"]', send="/backend-api/f/conversation",
                    storage="sdmntprnortheu.oaiusercontent.com", label="ChatGPT",
                    response='[data-markdown-text-style="assistant-message"] p',
                    user="[data-user-message-bubble] div", color="rgb(15, 118, 110)"),
    "perplexity": dict(url="https://www.perplexity.ai/", composer='[role="textbox"]', files='input[type="file"]',
                       send="/rest/sse/perplexity_ask", storage="ppl-ai-file-upload.s3.amazonaws.com",
                       label="Perplexity", response=".prose li", user="h1", color="rgb(94, 234, 212)"),
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
    found = glob.glob(str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*"))
    found += glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    found.sort(key=lambda p: int(p.split("chromium-")[1].split("/")[0]))
    return found[-1] if found else None


CHROMIUM = find_chromium()
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Playwright Chromium not installed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Env:
    def __init__(self, home: Path):
        self.home = home
        self.answer_file = home / "answer"
        self.upstream = Upstream()
        self.decider = Decider(ThreadRunner(FileDialog(str(self.answer_file))))
        self.port = free_port()
        self.proxy = GuardProxy([GuardAddon(self.decider, Config(max_inspect_mb=2)), RedirectUpstream(self.upstream.port)],
                                self.port)
        self.proxy.start()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROMIUM, args=[
            f"--proxy-pac-url={pac_data_url(self.port)}",
            "--host-resolver-rules=MAP direct.test 127.0.0.1",
        ])

    def answer(self, value: str):
        self.answer_file.write_text(value)

    def dialogs(self) -> list[dict]:
        log = Path(f"{self.answer_file}.log")
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def wait_dialogs(self, n: int, timeout=20.0) -> list[dict]:
        end = time.time() + timeout
        while time.time() < end:
            if len(self.dialogs()) >= n:
                return self.dialogs()
            time.sleep(0.1)
        raise AssertionError(f"expected {n} dialogs, saw {len(self.dialogs())}")

    def open(self, site: str):
        ctx = self.browser.new_context(ignore_https_errors=True)  # the test CA isn't in this Chromium's store
        page = ctx.new_page()
        page.goto(SITES[site]["url"])
        page.wait_for_selector(SITES[site]["composer"])
        return page

    def close(self):
        self.browser.close()
        self.pw.stop()
        self.proxy.stop()
        self.upstream.shutdown()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("guard-home")
    old = os.environ.get("UPLOAD_GUARD_HOME")
    os.environ["UPLOAD_GUARD_HOME"] = str(home)  # the CA for this run lives here, not in ~/.upload-guard
    e = Env(home)
    yield e
    e.close()
    if old is None:
        os.environ.pop("UPLOAD_GUARD_HOME", None)
    else:
        os.environ["UPLOAD_GUARD_HOME"] = old


@pytest.fixture(autouse=True)
def fresh(env):
    env.decider.forget()
    env.upstream.clear()
    env.answer("cancel")
    Path(f"{env.answer_file}.log").unlink(missing_ok=True)


def items(page, list_id):
    return page.locator(f"#{list_id} li").all_inner_texts()


def wait_items(page, list_id, n, timeout=20.0):
    # polled from Python: the pages' CSP (like the real sites') forbids eval-based waiting
    end = time.time() + timeout
    while time.time() < end:
        if page.locator(f"#{list_id} li").count() >= n:
            return items(page, list_id)
        page.wait_for_timeout(100)
    raise AssertionError(f"#{list_id} never reached {n} items: {items(page, list_id)}")


def type_and_send(page, site, text):
    page.click(SITES[site]["composer"])
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def sent_texts(env, site) -> list[str]:
    out = []
    for r in env.upstream.received("POST", path=SITES[site]["send"]):
        out.append(r["body"].decode())
    return out


# ---------------------------------------------------------------- prompts

@pytest.mark.parametrize("site", SITES)
def test_clean_prompt_goes_through_without_dialog(env, site):
    page = env.open(site)
    type_and_send(page, site, f"what is data loss prevention {site}")
    assert wait_items(page, "sent-log", 1) == [f"what is data loss prevention {site}"]
    assert any(f"what is data loss prevention {site}" in b for b in sent_texts(env, site))
    assert env.dialogs() == []


@pytest.mark.parametrize("site", SITES)
def test_marker_prompt_cancel_blocks_continue_sends(env, site):
    page = env.open(site)
    n = len(env.dialogs())
    type_and_send(page, site, f"summarise the WALLED_SENSITIVE notes for {site}")
    (err,) = wait_items(page, "error-log", 1)
    assert err == f"403 {BLOCK_MESSAGE}"
    d = env.dialogs()[n]
    assert f"Your prompt to {SITES[site]['label']} contains WALLED_SENSITIVE." in d["body"]
    assert "summarise the WALLED_SENSITIVE notes" in d["body"]
    assert not any("WALLED_SENSITIVE" in b for b in sent_texts(env, site))  # never left the Mac

    env.answer("continue")
    type_and_send(page, site, f"please summarise the WALLED_SENSITIVE notes for {site}")
    assert wait_items(page, "sent-log", 1) == [f"please summarise the WALLED_SENSITIVE notes for {site}"]
    (body,) = [b for b in sent_texts(env, site) if "WALLED_SENSITIVE" in b]
    assert f"please summarise the WALLED_SENSITIVE notes for {site}" in body  # unchanged


def test_typeahead_never_leaks_the_marker(env):
    page = env.open("perplexity")
    page.click(SITES["perplexity"]["composer"])
    page.keyboard.type("plan WALLED_SENSITIVE x")
    page.wait_for_timeout(800)
    from urllib.parse import unquote

    seen = [unquote(r["path"]) for r in env.upstream.received("GET", path="/rest/autosuggest")]
    assert seen and all("WALLED_SENSITIVE" not in p for p in seen)  # partial words are fine; the marker isn't
    assert any(p.endswith("plan WALLED_SENSITIV") for p in seen)  # everything before it still went through
    assert env.dialogs() == []


# ---------------------------------------------------------------- files

def storage_bodies(env, site):
    return [r["body"] for r in env.upstream.received(host=SITES[site]["storage"]) if r["method"] in ("PUT", "POST")]


@pytest.mark.parametrize("site", SITES)
def test_clean_file_uploads_without_dialog(env, site):
    page = env.open(site)
    page.set_input_files(SITES[site]["files"], str(FIX / "clean.png"))
    assert wait_items(page, "upload-log", 1) == ["clean.png"]
    data = (FIX / "clean.png").read_bytes()
    assert any(data in b for b in storage_bodies(env, site))
    assert env.dialogs() == []


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("fixture,where,answer", [
    ("marker.txt", "line 2", "cancel"),
    ("marker.pdf", "page 2", "continue"),
    ("scanned_marker.pdf", "scanned page 2", "cancel"),
    ("marker.png", "text in the image", "continue"),
])
def test_marker_file(env, site, fixture, where, answer):
    env.answer(answer)
    page = env.open(site)
    n = len(env.dialogs())
    page.set_input_files(SITES[site]["files"], str(FIX / fixture))
    d = env.wait_dialogs(n + 1)[n]
    assert d["body"].startswith(f"“{fixture}” ({where}) contains WALLED_SENSITIVE and is about to be uploaded to {SITES[site]['label']}.")
    data = (FIX / fixture).read_bytes()
    if answer == "cancel":
        assert wait_items(page, "upload-error-log", 1) == [f"{fixture} 403"]
        assert not any(data in b for b in storage_bodies(env, site))  # the bytes never left
    else:
        assert wait_items(page, "upload-log", 1) == [fixture]
        assert any(data in b for b in storage_bodies(env, site))  # arrived byte for byte


def test_too_large_file_asks_first(env, tmp_path):
    big = tmp_path / "recording.mov"
    big.write_bytes(os.urandom(3 * 1024 * 1024))
    page = env.open("chatgpt")
    page.set_input_files(SITES["chatgpt"]["files"], str(big))
    d = env.wait_dialogs(1)[0]
    assert "“recording.mov” couldn't be inspected (it's larger than 2 MB)" in d["body"]
    assert wait_items(page, "upload-error-log", 1) == ["recording.mov 403"]
    assert storage_bodies(env, "chatgpt") == []


# ---------------------------------------------------------------- colour, routing, failure

@pytest.mark.parametrize("site", SITES)
def test_responses_recoloured_including_under_csp(env, site):
    page = env.open(site)
    colour = page.evaluate(f"getComputedStyle(document.querySelector('{SITES[site]['response']}')).color")
    assert colour == SITES[site]["color"]
    assert page.evaluate(f"getComputedStyle(document.querySelector('{SITES[site]['user']}')).color") != colour
    streamed = page.evaluate(f"""() => {{ const p = document.createElement('p'); p.textContent = 'more';
        document.querySelector('{SITES[site]['response']}').parentElement.appendChild(p); return getComputedStyle(p).color; }}""")
    assert streamed == SITES[site]["color"]


def test_other_sites_go_direct(env):
    ctx = env.browser.new_context()
    page = ctx.new_page()
    page.goto(f"http://direct.test:{env.upstream.port}/")
    assert page.inner_text("body") == "direct"  # went straight to the server; the guard would have failed to resolve it


def test_ai_sites_fail_closed_when_the_guard_is_not_running(env):
    dead = free_port()
    browser = env.pw.chromium.launch(executable_path=CHROMIUM, args=[
        f"--proxy-pac-url={pac_data_url(dead)}", "--host-resolver-rules=MAP direct.test 127.0.0.1"])
    try:
        page = browser.new_context(ignore_https_errors=True).new_page()
        with pytest.raises(Exception, match="ERR_PROXY_CONNECTION_FAILED|ERR_TUNNEL|ERR_CONNECTION_REFUSED"):
            page.goto("https://chatgpt.com/", timeout=10000)
        other = browser.new_context().new_page()
        other.goto(f"http://direct.test:{env.upstream.port}/")
        assert other.inner_text("body") == "direct"  # everything else keeps working
    finally:
        browser.close()


def test_recolour_survives_a_reload(env):
    """Pages are fetched in full every time, so a cached copy without the style is never used."""
    page = env.open("perplexity")
    page.reload()
    page.wait_for_selector(SITES["perplexity"]["composer"])
    colour = page.evaluate(f"getComputedStyle(document.querySelector('{SITES['perplexity']['response']}')).color")
    assert colour == SITES["perplexity"]["color"]
    pages = env.upstream.received("GET", host="www.perplexity.ai", path="/")
    assert pages and all("if-none-match" not in r["headers"] for r in pages if r["path"] == "/")
