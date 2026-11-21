"""The native messaging host. Chrome starts it when the extension connects.

    reader thread ── reads framed requests from stdin
        ├─ ping → answered right away
        └─ everything else → queue
    main thread ── takes one request at a time: inspect, show the dialog
                   (AppKit needs the main thread), reply, write the audit entry

stdout carries the protocol and nothing else; logs go to ~/.upload-guard/host.log.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import queue
import sys
import threading

from . import __version__, audit
from .config import Config, ensure_home, load_config
from .detect import Finding, inspect_file, inspect_text
from .dialog import FlaggedFile, files_text, get_backend, prompt_text
from .protocol import CANCEL, CONTINUE, ProtocolError, Writer, read_message

log = logging.getLogger("upload_guard.host")
_STOP = object()


class Host:
    def __init__(self, config: Config | None = None, dialog=None):
        self.config = config or load_config()
        self._dialog = dialog  # created lazily, on the main thread

    # -- dispatch ---------------------------------------------------------

    def handle(self, msg: dict) -> dict:
        req_id = msg.get("id")
        kind = msg.get("type")
        try:
            if kind == "ping":
                return self.pong(req_id)
            if kind == "prompt":
                return {"id": req_id, **self.handle_prompt(msg)}
            if kind == "files":
                return {"id": req_id, **self.handle_files(msg)}
            return {"id": req_id, "error": f"unknown request type {kind!r}"}
        except Exception as e:  # noqa: BLE001 - the extension blocks on any error
            log.exception("request %s failed", kind)
            return {"id": req_id, "error": f"{type(e).__name__}: {e}"}

    def pong(self, req_id) -> dict:
        return {"id": req_id, "ok": True, "version": __version__, "max_inspect_mb": self.config.max_inspect_mb}

    # -- prompts ----------------------------------------------------------

    def handle_prompt(self, msg: dict) -> dict:
        site = str(msg.get("site") or "")
        text = str(msg.get("text") or "")
        found = inspect_text(text)
        if found is None:
            # The extension only asks when its own check fires; if we disagree, say so in the log.
            log.warning("extension flagged a prompt the host considers clean")
            return {"decision": CONTINUE, "found": False}
        ok = self.confirm(prompt_text(site, found.excerpt))
        decision = CONTINUE if ok else CANCEL
        audit.record(site, "prompt", f"prompt ({len(text)} chars)", found.where, decision, "marker")
        return {"decision": decision, "found": True}

    # -- files ------------------------------------------------------------

    def handle_files(self, msg: dict) -> dict:
        site = str(msg.get("site") or "")
        files = msg.get("files") or []
        findings: list[Finding | None] = []
        names: list[str] = []
        for i, f in enumerate(files):
            name = str(f.get("name") or f"file {i + 1}")
            names.append(name)
            findings.append(self._inspect_one(name, f))

        flagged_idx = [i for i, fd in enumerate(findings) if fd is not None]
        if flagged_idx:
            flagged = [
                FlaggedFile(names[i], findings[i].where, findings[i].uninspectable, findings[i].reason)
                for i in flagged_idx
            ]
            ok = self.confirm(files_text(site, flagged, len(files) - len(flagged_idx)))
            decision = CONTINUE if ok else CANCEL
        else:
            decision = CONTINUE

        results = []
        for i, (name, fd) in enumerate(zip(names, findings)):
            file_decision = decision if fd is not None else CONTINUE
            results.append({
                "index": i,
                "name": name,
                "found": bool(fd and not fd.uninspectable),
                "uninspectable": bool(fd and fd.uninspectable),
                "where": fd.where if fd else "",
                "decision": file_decision,
            })
            if fd is not None:
                reason = f"uninspectable: {fd.reason}" if fd.uninspectable else "marker"
                audit.record(site, "file", name, fd.where, file_decision, reason)
        return {"decision": decision, "dialog": bool(flagged_idx), "results": results}

    def _inspect_one(self, name: str, f: dict) -> Finding | None:
        b64 = f.get("b64")
        if b64 is None:
            reason = f"it's larger than {self.config.max_inspect_mb} MB"
            if f.get("skipped") not in (None, "too_large"):
                reason = str(f.get("skipped"))
            finding = Finding.cannot_inspect(reason)
        else:
            try:
                data = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                finding = Finding.cannot_inspect("the file data was damaged in transit")
            else:
                finding = inspect_file(name, str(f.get("mime") or ""), data, self.config)
        if finding is not None and finding.uninspectable and self.config.fail_mode == "open":
            log.info("fail_mode=open: passing a file that couldn't be inspected")
            return None
        return finding

    # -- dialogs ----------------------------------------------------------

    def confirm(self, text) -> bool:
        if self._dialog is None:
            self._dialog = get_backend()
        try:
            return self._dialog.confirm(text)
        except Exception:  # noqa: BLE001
            log.exception("dialog backend %s failed", getattr(self._dialog, "name", "?"))
            if sys.platform == "darwin" and getattr(self._dialog, "name", "") != "osascript":
                from .dialog.osascript import OsascriptDialog

                self._dialog = OsascriptDialog()
                return self._dialog.confirm(text)
            raise


def _setup_logging() -> None:
    try:
        path = ensure_home() / "host.log"
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _warm_up_ocr() -> None:
    """The first Vision request in a process is slower; pay for it before an upload does."""
    try:
        import io

        from PIL import Image

        from .detect import ocr

        buf = io.BytesIO()
        Image.new("RGB", (64, 32), "white").save(buf, format="PNG")
        ocr.ocr_bytes(buf.getvalue())
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _setup_logging()
    log.info("host %s started (pid %d, caller %s)", __version__, os.getpid(), argv[1] if len(argv) > 1 else "?")

    # Keep the real stdout for protocol frames; anything else printed goes to stderr.
    proto_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    writer = Writer(proto_out)
    stdin = sys.stdin.buffer
    host = Host()
    requests: queue.Queue = queue.Queue()

    def reader() -> None:
        while True:
            try:
                msg = read_message(stdin)
            except ProtocolError as e:
                log.error("protocol error, closing: %s", e)
                break
            except ValueError as e:
                log.error("bad JSON from browser: %s", e)
                writer.send({"error": "bad_json"})
                continue
            if msg is None:
                break
            if msg.get("type") == "ping":
                writer.send(host.pong(msg.get("id")))
                continue
            requests.put(msg)
        requests.put(_STOP)

    threading.Thread(target=reader, name="reader", daemon=True).start()
    if sys.platform == "darwin":
        threading.Thread(target=_warm_up_ocr, name="ocr-warmup", daemon=True).start()

    while True:
        msg = requests.get()
        if msg is _STOP:
            break
        reply = host.handle(msg)
        try:
            writer.send(reply)
        except (BrokenPipeError, OSError):
            break
    log.info("browser closed the connection, exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
