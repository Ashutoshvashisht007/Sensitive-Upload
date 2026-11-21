"""End-to-end tests of the native host process, speaking the real framing."""

import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from upload_guard.dialog import FlaggedFile, files_text, prompt_text
from upload_guard.protocol import encode_message

FIX = Path(__file__).parent / "fixtures"


class HostProcess:
    def __init__(self, tmp_path: Path, dialog: str):
        env = dict(os.environ, UPLOAD_GUARD_DIALOG=dialog, UPLOAD_GUARD_HOME=str(tmp_path / "home"))
        self.home = tmp_path / "home"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "upload_guard.native_host", "chrome-extension://test/"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )

    def ask(self, msg: dict) -> dict:
        self.proc.stdin.write(encode_message(msg))
        self.proc.stdin.flush()
        header = self.proc.stdout.read(4)
        assert len(header) == 4, self.proc.stderr.read().decode()
        (n,) = struct.unpack("@I", header)
        return json.loads(self.proc.stdout.read(n))

    def close(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(timeout=10)

    def audit(self) -> list[dict]:
        path = self.home / "audit.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def host_factory(tmp_path):
    hosts = []

    def make(dialog="auto-continue"):
        h = HostProcess(tmp_path, dialog)
        hosts.append(h)
        return h

    yield make
    for h in hosts:
        if h.proc.poll() is None:
            h.proc.kill()


def file_entry(name, mime=""):
    data = (FIX / name).read_bytes()
    return {"name": name, "mime": mime, "size": len(data), "b64": base64.b64encode(data).decode()}


def test_ping(host_factory):
    h = host_factory()
    r = h.ask({"id": 1, "type": "ping"})
    assert r["id"] == 1 and r["ok"] is True and r["max_inspect_mb"] == 25
    assert h.close() == 0


def test_prompt_clean_needs_no_dialog(host_factory):
    h = host_factory("auto-cancel")
    r = h.ask({"id": 2, "type": "prompt", "site": "chatgpt", "text": "hello there"})
    assert r == {"id": 2, "decision": "continue", "found": False}
    assert h.audit() == []


@pytest.mark.parametrize("dialog,decision", [("auto-continue", "continue"), ("auto-cancel", "cancel")])
def test_prompt_with_marker(host_factory, dialog, decision):
    h = host_factory(dialog)
    r = h.ask({"id": 3, "type": "prompt", "site": "chatgpt", "text": "summarise WALLED_SENSITIVE notes"})
    assert r == {"id": 3, "decision": decision, "found": True}
    (entry,) = h.audit()
    assert entry["kind"] == "prompt" and entry["decision"] == decision
    assert entry["name"] == "prompt (32 chars)"
    assert "WALLED" not in json.dumps(entry)  # no content in the audit log


def test_clean_files_pass_without_dialog(host_factory):
    h = host_factory("auto-cancel")  # would cancel if a dialog appeared
    r = h.ask({"id": 4, "type": "files", "site": "perplexity",
               "files": [file_entry("clean.txt"), file_entry("clean.png", "image/png")]})
    assert r["decision"] == "continue" and r["dialog"] is False
    assert [x["decision"] for x in r["results"]] == ["continue", "continue"]


def test_flagged_file_cancel_keeps_clean_ones(host_factory):
    h = host_factory("auto-cancel")
    r = h.ask({"id": 5, "type": "files", "site": "chatgpt",
               "files": [file_entry("clean.txt"), file_entry("marker.pdf", "application/pdf"),
                         file_entry("marker.png", "image/png")]})
    assert r["decision"] == "cancel" and r["dialog"] is True
    assert [(x["name"], x["found"], x["decision"]) for x in r["results"]] == [
        ("clean.txt", False, "continue"),
        ("marker.pdf", True, "cancel"),
        ("marker.png", True, "cancel"),
    ]
    assert r["results"][1]["where"] == "page 2"
    assert [e["name"] for e in h.audit()] == ["marker.pdf", "marker.png"]


def test_flagged_file_continue(host_factory):
    h = host_factory("auto-continue")
    r = h.ask({"id": 6, "type": "files", "site": "chatgpt", "files": [file_entry("marker.txt")]})
    assert r["decision"] == "continue" and r["results"][0]["decision"] == "continue"


def test_skipped_large_file_asks(host_factory):
    h = host_factory("auto-cancel")
    r = h.ask({"id": 7, "type": "files", "site": "chatgpt",
               "files": [{"name": "huge.mov", "mime": "video/quicktime", "size": 30 << 20, "b64": None,
                          "skipped": "too_large"}]})
    assert r["dialog"] is True and r["results"][0]["uninspectable"] is True
    assert r["results"][0]["decision"] == "cancel"


def test_dialog_queue_and_ids(host_factory):
    """Requests are answered one at a time, each with its own id."""
    h = host_factory("auto-continue")
    for i in range(3):
        h.proc.stdin.write(encode_message({"id": 100 + i, "type": "prompt", "site": "chatgpt",
                                           "text": f"WALLED_SENSITIVE {i}"}))
    h.proc.stdin.flush()
    ids = []
    for _ in range(3):
        (n,) = struct.unpack("@I", h.proc.stdout.read(4))
        ids.append(json.loads(h.proc.stdout.read(n))["id"])
    assert ids == [100, 101, 102]


def test_unknown_type_and_bad_base64(host_factory):
    h = host_factory("auto-cancel")
    assert "error" in h.ask({"id": 8, "type": "nope"})
    r = h.ask({"id": 9, "type": "files", "site": "chatgpt",
               "files": [{"name": "x.txt", "mime": "text/plain", "size": 3, "b64": "@@not base64@@"}]})
    assert r["results"][0]["uninspectable"] is True


def test_exits_when_browser_closes(host_factory):
    h = host_factory()
    assert h.close() == 0


def test_dialog_wording():
    t = prompt_text("chatgpt", "x WALLED_SENSITIVE y")
    assert "Your prompt to ChatGPT contains WALLED_SENSITIVE." in t.body
    assert "Cancel keeps it in the box" in t.body
    one = files_text("perplexity", [FlaggedFile("contract.pdf", "page 2")], 0)
    assert one.body.startswith("“contract.pdf” (page 2) contains WALLED_SENSITIVE and is about to be uploaded to Perplexity.")
    assert "Continue uploads it unchanged. Cancel stops this upload." in one.body
    many = files_text("chatgpt", [FlaggedFile("a.pdf", "page 1"), FlaggedFile("b.png", "", True, "too big")], 2)
    assert "Cancel removes these files; the other 2 files upload normally." in many.body
    assert "couldn't be inspected (too big)" in many.body


def test_osascript_passes_text_as_argv(monkeypatch):
    """File names and prompts must never end up inside the AppleScript source."""
    import subprocess as sp

    from upload_guard.dialog.base import DialogText
    from upload_guard.dialog.osascript import OsascriptDialog

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0, stdout="Continue\n", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    evil = 'x" & (do shell script "touch /tmp/pwned") & "'
    assert OsascriptDialog().confirm(DialogText("Title", evil)) is True
    cmd = seen["cmd"]
    sep = cmd.index("--")
    assert cmd[sep + 1:] == ["Title", evil]
    assert all(evil not in part for part in cmd[:sep])
