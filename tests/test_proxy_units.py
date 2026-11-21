"""Sites, body parsing, recolouring/CSP, PAC and the Decider."""

import asyncio
import base64
import json
import shutil
import subprocess

import pytest

from upload_guard import sites
from upload_guard.decisions import Decider, content_key
from upload_guard.dialog.base import DialogText
from upload_guard.proxy import bodies, pac, recolor


# ---------------------------------------------------------------- sites

@pytest.mark.parametrize("host,key", [
    ("chatgpt.com", "chatgpt"), ("ab.chatgpt.com", "chatgpt"), ("chat.openai.com", "chatgpt"),
    ("files.oaiusercontent.com", "chatgpt"), ("www.perplexity.ai", "perplexity"),
    ("perplexity.ai", "perplexity"), ("ppl-ai-file-upload.s3.amazonaws.com", "perplexity"),
    ("example.com", None), ("notchatgpt.com", None), ("s3.amazonaws.com", None),
])
def test_site_for_host(host, key):
    s = sites.site_for_host(host)
    assert (s.key if s else None) == key


def test_send_and_setup_paths():
    assert sites.CHATGPT.is_send("/backend-api/f/conversation")
    assert sites.CHATGPT.is_send("/backend-api/conversation")
    assert not sites.CHATGPT.is_send("/backend-api/conversations")
    assert sites.CHATGPT.is_setup("/backend-api/files")
    assert sites.PERPLEXITY.is_send("/rest/sse/perplexity_ask")
    assert sites.PERPLEXITY.is_setup("/rest/uploads/create_upload_url")


def test_site_for_origin():
    assert sites.site_for_origin("https://chatgpt.com/c/123").key == "chatgpt"
    assert sites.site_for_origin("https://www.perplexity.ai").key == "perplexity"
    assert sites.site_for_origin("https://evil.example") is None


# ---------------------------------------------------------------- bodies

def test_json_strings_and_query():
    body = json.dumps({"messages": [{"content": {"parts": ["hello WALLED_SENSITIVE"]}}], "model": "x"}).encode()
    texts = bodies.text_strings("application/json", body, "https://chatgpt.com/x?q=abc")
    assert "hello WALLED_SENSITIVE" in texts and "abc" in texts


def test_form_and_plain_text():
    assert "a b" in bodies.text_strings("application/x-www-form-urlencoded", b"q=a+b", "https://x/")
    assert bodies.text_strings("text/plain", b"plain words", "https://x/") == ["plain words"]


def test_multipart_parts():
    boundary = "XyZ"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"key\"\r\n\r\nuploads/u1/notes.txt\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"notes.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\nline WALLED_SENSITIVE\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    parts = bodies.multipart_parts(f"multipart/form-data; boundary={boundary}", body)
    assert [(p.name, p.filename) for p in parts] == [("key", None), ("file", "notes.txt")]
    assert parts[1].data == b"line WALLED_SENSITIVE"


def test_find_key_and_urls():
    data = {"upload": {"file_name": "a.pdf"}, "upload_url": "https://files.oaiusercontent.com/f-1?sig=x"}
    assert bodies.find_key(data, ("file_name",)) == "a.pdf"
    assert bodies.urls_in(data) == ["https://files.oaiusercontent.com/f-1?sig=x"]


# ---------------------------------------------------------------- recolour / CSP

def test_css_targets_only_responses():
    css = recolor.css_for(sites.CHATGPT)
    assert '[data-markdown-text-style="assistant-message"], [data-markdown-text-style="assistant-message"] *' in css
    assert "#0f766e" in css and "#5eead4" in css
    assert "#0f766e" in recolor.css_for(sites.CHATGPT, light="not-a-colour")


@pytest.mark.parametrize("policy,needed,expect", [
    ("default-src 'self'", True, "style-src-elem 'self' 'nonce-N'"),
    ("style-src 'self' 'nonce-abc'", True, "style-src-elem 'self' 'nonce-abc' 'nonce-N'"),
    ("style-src 'self' 'unsafe-inline'", False, None),
    ("style-src-elem 'self' 'sha256-x'", True, "style-src-elem 'self' 'sha256-x' 'nonce-N'"),
    ("script-src 'self'", False, None),
])
def test_patch_policy(policy, needed, expect):
    new, n = recolor.patch_policy(policy, "N")
    assert n is needed
    if expect:
        assert expect in new
    else:
        assert new == policy


def test_unsafe_inline_policy_is_left_alone():
    # adding a nonce next to 'unsafe-inline' would turn 'unsafe-inline' off for the site
    policy = "default-src 'self'; style-src 'self' 'unsafe-inline'"
    assert recolor.patch_policy(policy, "N") == (policy, False)


def test_inject():
    html = "<html><head><title>x</title></head><body>hi</body></html>"
    out = recolor.inject(html, "a{}", "N")
    assert out.index('<style id="upload-guard-response-color" nonce="N">') < out.index("</head>")
    assert recolor.inject(out, "a{}", "N") == out  # idempotent
    assert recolor.inject("<p>no head</p>", "a{}").startswith("<style")


# ---------------------------------------------------------------- PAC

def test_pac_script_contents():
    script = pac.pac_script(8899)
    assert 'return "PROXY 127.0.0.1:8899"' in script and 'return "DIRECT"' in script
    assert "DIRECT" not in script.split('return "PROXY')[0]  # no DIRECT fallback for AI hosts
    url = pac.pac_data_url(8899)
    assert url.startswith("data:application/x-ns-proxy-autoconfig;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).decode() == script


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("host,expect", [
    ("chatgpt.com", "PROXY 127.0.0.1:8899"), ("ab.chatgpt.com", "PROXY 127.0.0.1:8899"),
    ("files.oaiusercontent.com", "PROXY 127.0.0.1:8899"), ("www.perplexity.ai", "PROXY 127.0.0.1:8899"),
    ("ppl-ai-file-upload.s3.amazonaws.com", "PROXY 127.0.0.1:8899"),
    ("example.com", "DIRECT"), ("mail.google.com", "DIRECT"), ("s3.amazonaws.com", "DIRECT"),
    ("notchatgpt.com", "DIRECT"),
])
def test_pac_routes_only_ai_hosts(host, expect):
    js = pac.pac_script(8899) + (
        "function dnsDomainIs(h, d) { return h.length >= d.length && h.substring(h.length - d.length) === d; }\n"
        f"console.log(FindProxyForURL('https://{host}/', '{host}'));"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.strip()
    assert out == expect


# ---------------------------------------------------------------- Decider

class CountingRunner:
    def __init__(self, answer=True, delay=0.05):
        self.answer, self.delay, self.calls = answer, delay, 0

    def confirm(self, text):
        import concurrent.futures
        import threading
        import time

        self.calls += 1
        f = concurrent.futures.Future()
        threading.Thread(target=lambda: (time.sleep(self.delay), f.set_result(self.answer))).start()
        return f


def test_decider_asks_once_for_parallel_duplicates():
    runner = CountingRunner(answer=True)
    d = Decider(runner)
    key = content_key("prompt", "x")

    async def main():
        return await asyncio.gather(*[d.decide(key, lambda: DialogText("t", "b")) for _ in range(3)])

    results = asyncio.run(main())
    assert runner.calls == 1
    assert [r[0] for r in results] == [True, True, True]
    assert sum(1 for r in results if r[1]) == 1  # only one was actually asked


def test_decider_remembers_continue_but_cancel_only_briefly():
    now = [0.0]
    runner = CountingRunner(answer=False, delay=0)
    d = Decider(runner, clock=lambda: now[0])
    key = content_key("prompt", "y")
    assert asyncio.run(d.decide(key, lambda: DialogText("t", "b"))) == (False, True)
    assert asyncio.run(d.decide(key, lambda: DialogText("t", "b"))) == (False, False)
    now[0] += 10  # past the short cancel memory: ask again
    runner.answer = True
    assert asyncio.run(d.decide(key, lambda: DialogText("t", "b"))) == (True, True)
    now[0] += 300  # Continue is remembered longer
    assert asyncio.run(d.decide(key, lambda: DialogText("t", "b"))) == (True, False)


def test_decider_blocks_when_dialog_fails():
    import concurrent.futures

    class Broken:
        def confirm(self, text):
            f = concurrent.futures.Future()
            f.set_exception(RuntimeError("no display"))
            return f

    assert asyncio.run(Decider(Broken()).decide("k", lambda: DialogText("t", "b"))) == (False, True)
