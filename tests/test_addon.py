"""The guard add-on against mitmproxy flows, with a scripted dialog."""

import asyncio
import json
from pathlib import Path

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from upload_guard import audit
from upload_guard.config import Config
from upload_guard.decisions import Decider, ThreadRunner
from upload_guard.dialog.auto import AutoDialog
from upload_guard.proxy.addon import BLOCK_MESSAGE, GuardAddon

FIX = Path(__file__).parent / "fixtures"
CONV = "https://chatgpt.com/backend-api/f/conversation"


def make_guard(answer="continue", **cfg):
    dialog = AutoDialog(answer)
    return GuardAddon(Decider(ThreadRunner(dialog)), Config(**cfg)), dialog


def flow(method, url, body=b"", headers=None):
    h = {"content-type": "application/json"} | (headers or {})
    if body:
        h["content-length"] = str(len(body))
    return tflow.tflow(req=http.Request.make(method, url, body, h))


def run(guard, *flows):
    async def go():
        for f in flows:
            await guard.requestheaders(f)
            await guard.request(f)
    asyncio.run(go())


def prompt_body(text):
    return json.dumps({"action": "next", "messages": [{"content": {"content_type": "text", "parts": [text]}}]}).encode()


def blocked(f):
    return f.response is not None and f.response.status_code == 403 and BLOCK_MESSAGE.encode() in f.response.content


# ---------------------------------------------------------------- prompts

def test_clean_prompt_passes_without_dialog():
    guard, dialog = make_guard("cancel")
    f = flow("POST", CONV, prompt_body("what is DLP?"))
    run(guard, f)
    assert f.response is None and dialog.shown == []


@pytest.mark.parametrize("answer", ["continue", "cancel"])
def test_marker_prompt_asks(answer):
    guard, dialog = make_guard(answer)
    body = prompt_body("summarise the WALLED_SENSITIVE notes")
    f = flow("POST", CONV, body, {"origin": "https://chatgpt.com"})
    run(guard, f)
    (shown,) = dialog.shown
    assert "Your prompt to ChatGPT contains WALLED_SENSITIVE." in shown.body
    assert "summarise the WALLED_SENSITIVE notes" in shown.body
    if answer == "continue":
        assert f.response is None and f.request.content == body  # forwarded unchanged
    else:
        assert blocked(f)
        assert f.response.headers["access-control-allow-origin"] == "https://chatgpt.com"
    (entry,) = audit.tail()
    assert entry["kind"] == "prompt" and entry["decision"] == answer
    assert "WALLED" not in json.dumps(entry)


def test_same_prompt_in_two_requests_asks_once():
    guard, dialog = make_guard("continue")
    body = prompt_body("WALLED_SENSITIVE once")
    run(guard, flow("POST", "https://chatgpt.com/backend-api/f/conversation/prepare", body), flow("POST", CONV, body))
    assert len(dialog.shown) == 1


def test_perplexity_prompt_and_unknown_post_endpoint():
    guard, dialog = make_guard("cancel")
    f1 = flow("POST", "https://www.perplexity.ai/rest/sse/perplexity_ask", json.dumps({"query_str": "x WALLED_SENSITIVE"}).encode())
    f2 = flow("POST", "https://www.perplexity.ai/rest/something/new", json.dumps({"q": "y WALLED_SENSITIVE"}).encode())
    run(guard, f1, f2)
    assert blocked(f1) and blocked(f2)
    assert "Your prompt to Perplexity" in dialog.shown[0].body and len(dialog.shown) == 2


def test_typeahead_get_is_blocked_silently():
    guard, dialog = make_guard("continue")
    f = flow("GET", "https://www.perplexity.ai/rest/autosuggest/list?query=WALLED_SENSITIVE%20plan")
    run(guard, f)
    assert blocked(f) and dialog.shown == []


def test_other_hosts_are_ignored():
    guard, dialog = make_guard("cancel")
    f = flow("POST", "https://example.com/api", prompt_body("WALLED_SENSITIVE"))
    run(guard, f)
    assert f.response is None and dialog.shown == []


# ---------------------------------------------------------------- uploads

def chatgpt_upload(guard, name, data):
    """The ChatGPT flow: announce the file, get a storage URL, PUT the bytes there."""
    setup = flow("POST", "https://chatgpt.com/backend-api/files",
                 json.dumps({"file_name": name, "file_size": len(data), "use_case": "my_files"}).encode())
    run(guard, setup)
    setup.response = http.Response.make(200, json.dumps({
        "file_id": "file-1", "upload_url": "https://files.oaiusercontent.com/file-1?se=1&sig=abc"}).encode(),
        {"content-type": "application/json"})
    guard.response(setup)
    put = flow("PUT", "https://files.oaiusercontent.com/file-1?se=1&sig=abc", data,
               {"content-type": "application/octet-stream", "origin": "https://chatgpt.com"})
    run(guard, put)
    return put


@pytest.mark.parametrize("fixture,where", [
    ("marker.txt", "line 2"), ("marker.pdf", "page 2"), ("marker.png", "text in the image"),
    ("scanned_marker.pdf", "scanned page 2"),
])
def test_flagged_upload_names_the_file(fixture, where):
    guard, dialog = make_guard("cancel")
    put = chatgpt_upload(guard, fixture, (FIX / fixture).read_bytes())
    assert blocked(put)
    (shown,) = dialog.shown
    assert shown.body.startswith(f"“{fixture}” ({where}) contains WALLED_SENSITIVE and is about to be uploaded to ChatGPT.")


def test_clean_upload_passes():
    guard, dialog = make_guard("cancel")
    put = chatgpt_upload(guard, "clean.png", (FIX / "clean.png").read_bytes())
    assert put.response is None and dialog.shown == []


def test_continue_forwards_bytes_unchanged():
    guard, _ = make_guard("continue")
    data = (FIX / "marker.pdf").read_bytes()
    put = chatgpt_upload(guard, "marker.pdf", data)
    assert put.response is None and put.request.content == data


def test_multipart_upload_to_storage():
    guard, dialog = make_guard("cancel")
    boundary = "b0"
    data = (FIX / "marker.txt").read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"key\"\r\n\r\nuploads/u1/${{filename}}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"notes.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    f = flow("POST", "https://ppl-ai-file-upload.s3.amazonaws.com/", body,
             {"content-type": f"multipart/form-data; boundary={boundary}", "origin": "https://www.perplexity.ai"})
    run(guard, f)
    assert blocked(f)
    assert dialog.shown[0].body.startswith("“notes.txt” (line 2) contains WALLED_SENSITIVE and is about to be uploaded to Perplexity.")


def test_too_large_asks_before_reading():
    guard, dialog = make_guard("cancel", max_inspect_mb=1)
    f = flow("PUT", "https://files.oaiusercontent.com/file-9", b"x" * (2 * 1024 * 1024),
             {"content-type": "application/octet-stream"})
    asyncio.run(guard.requestheaders(f))
    assert blocked(f)
    assert "couldn't be inspected (it's larger than 1 MB)" in dialog.shown[0].body


def test_too_large_continue_streams_without_inspecting():
    guard, _ = make_guard("continue", max_inspect_mb=1)
    f = flow("PUT", "https://files.oaiusercontent.com/file-9", b"x" * (2 * 1024 * 1024),
             {"content-type": "application/octet-stream"})
    run(guard, f)
    assert f.response is None and f.request.stream is True


# ---------------------------------------------------------------- recolouring

def page_flow(host, html, csp=None):
    f = flow("GET", f"https://{host}/", b"", {"sec-fetch-dest": "document", "content-type": ""})
    headers = {"content-type": "text/html; charset=utf-8"}
    if csp:
        headers["content-security-policy"] = csp
    f.response = http.Response.make(200, html.encode(), headers)
    return f


def test_page_gets_style_with_csp_nonce():
    guard, _ = make_guard()
    f = page_flow("chatgpt.com", "<html><head></head><body></body></html>", csp="style-src 'self' 'nonce-site'")
    run(guard, f)
    guard.response(f)
    csp = f.response.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[-1].split("'")[0]
    assert f'<style id="upload-guard-response-color" nonce="{nonce}">' in f.response.text
    assert "'nonce-site'" in csp  # the site's own nonce still works


def test_recolor_can_be_turned_off_and_other_pages_untouched():
    guard, _ = make_guard(recolor=False)
    f = page_flow("chatgpt.com", "<html><head></head></html>")
    run(guard, f)
    guard.response(f)
    assert "upload-guard" not in f.response.text
    guard2, _ = make_guard()
    g = page_flow("example.com", "<html><head></head></html>")
    run(guard2, g)
    guard2.response(g)
    assert "upload-guard" not in g.response.text


# ---------------------------------------------------------------- telemetry copies of what you typed

def telemetry(url, text):
    return flow("POST", url, json.dumps({"events": [{"props": {"query": text}}]}).encode(), {"content-type": "text/plain"})


@pytest.mark.parametrize("url", ["https://chatgpt.com/ces/v1/telemetry/intake",
                                 "https://www.perplexity.ai/rest/event/analytics",
                                 "https://ab.chatgpt.com/v1/rgstr"])
def test_telemetry_copy_is_blocked_without_a_second_dialog(url):
    guard, dialog = make_guard("cancel")
    text = "summarise the WALLED_SENSITIVE notes"
    send, tele = flow("POST", CONV, prompt_body(text)), telemetry(url, text)
    run(guard, send, tele)
    assert blocked(send) and blocked(tele)
    assert len(dialog.shown) == 1


def test_telemetry_copy_passes_after_continue():
    guard, dialog = make_guard("continue")
    text = "summarise the WALLED_SENSITIVE notes"
    send, tele = flow("POST", CONV, prompt_body(text)), telemetry("https://chatgpt.com/ces/v1/telemetry/intake", text)
    run(guard, send, tele)
    assert send.response is None and tele.response is None and len(dialog.shown) == 1


def test_telemetry_with_marker_but_no_decision_is_blocked_quietly():
    guard, dialog = make_guard("continue")
    tele = telemetry("https://www.perplexity.ai/rest/event/analytics", "draft WALLED_SENSITIVE")
    run(guard, tele)
    assert blocked(tele) and dialog.shown == []


def test_block_message_never_contains_the_marker():
    # sites copy error messages into their telemetry; the marker in it would trigger another dialog
    assert "WALLED" not in BLOCK_MESSAGE.upper()
