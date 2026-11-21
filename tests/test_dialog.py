"""Dialog wording and the osascript fallback."""

from upload_guard.dialog import FlaggedFile, files_text, prompt_text


def test_dialog_wording():
    t = prompt_text("chatgpt", "x WALLED_SENSITIVE y")
    assert "Your prompt to ChatGPT contains WALLED_SENSITIVE." in t.body
    assert "Cancel stops it here: nothing is sent" in t.body
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
