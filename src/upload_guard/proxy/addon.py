"""The guard: a mitmproxy add-on that holds outgoing AI-site requests until they're checked.

Every request to a routed host (ChatGPT, Perplexity and their upload storage) passes through here
before it leaves the Mac:

  uploads   file bytes (raw PUT/POST to storage, or multipart parts with a filename)
            → inspected with the detectors (text, PDF, OCR) → dialog if flagged
  sends     requests on a site's send endpoints, or any POST/PUT whose text contains the marker
            → dialog with an excerpt of the prompt
  other     background requests carrying the marker (e.g. type-ahead) → blocked, no dialog
  clean     forwarded untouched

Continue forwards the request byte for byte. Cancel answers it locally with
403, so nothing reaches the site. On the way back, HTML pages of the two sites
get the recolouring <style>, and upload set-up replies teach us which file a
storage URL belongs to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib import parse as urllib_parse
from urllib.parse import urlsplit

from mitmproxy import http

from .. import audit, locate
from ..config import Config, LiveConfig, ensure_home, home_dir
from ..decisions import FILE_CANCEL_TTL, Decider, content_key
from ..detect import filetype, inspect_file, marker
from ..dialog import FlaggedFile, files_text, prompt_text
from ..sites import Site, by_key, host_matches, routed_host_patterns, site_for_host, site_for_origin
from . import bodies, recolor

log = logging.getLogger("upload_guard.proxy")

# Never put the marker itself in here: sites echo errors into their telemetry.
BLOCK_MESSAGE = "Blocked by Sensitive Upload Guard. Nothing was sent."
WRITE_METHODS = ("POST", "PUT", "PATCH")
NAME_KEYS = ("file_name", "filename", "fileName", "name")
SIZE_KEYS = ("file_size", "fileSize", "size", "content_length", "contentLength", "byte_size")
NAME_TTL = 3600.0
_FILE_NAME = re.compile(r"^[^/\\\n]{1,255}\.[A-Za-z0-9]{1,10}$")
_DISPOSITION_NAME = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", re.I)


class GuardAddon:
    def __init__(self, decider: Decider, config: Config | None = None):
        self.decider = decider
        self._fixed = config
        self._live = None if config else LiveConfig()
        self._names: dict[str, tuple[str, float]] = {}  # "host/path" of an upload URL → file name
        self._by_size: dict[tuple[str, int], tuple[str, float]] = {}  # (site, announced size) → file name

    @property
    def config(self) -> Config:
        return self._fixed or self._live.get()

    # ------------------------------------------------------------------ hooks

    async def requestheaders(self, flow: http.HTTPFlow) -> None:
        site = self._site(flow)
        if site is None:
            return
        flow.metadata["ug_site"] = site.key
        req = flow.request
        length = _int(req.headers.get("content-length"))
        if req.method in WRITE_METHODS and length > self.config.max_inspect_bytes:
            name = self._name_for(flow)
            flagged = [FlaggedFile(name or "a file", uninspectable=True, named=bool(name),
                                   reason=f"it's larger than {self.config.max_inspect_mb} MB")]
            name = name or "unnamed file"
            allowed, asked = await self.decider.decide(
                content_key("large", f"{req.pretty_url}:{length}"), lambda: files_text(site.key, flagged, 0))
            if asked:
                audit.record(site.key, "file", name, "", _word(allowed), "uninspectable: too large")
            self._log(flow, site, "upload (too large)", marker_found=None)
            if allowed:
                req.stream = True
                flow.metadata["ug_checked"] = True
            else:
                self._block(flow)

    async def request(self, flow: http.HTTPFlow) -> None:
        site = by_key(flow.metadata.get("ug_site", ""))
        if site is None or flow.response is not None or flow.metadata.get("ug_checked"):
            return
        req = flow.request
        body = req.get_content(strict=False) or b""
        ctype = req.headers.get("content-type", "")
        path = urlsplit(req.path).path

        if self._is_page_request(flow, site):
            # Always fetch the full page so the colour style can be added: a 304 from the
            # browser's cache would bring back a copy without it.
            for h in ("if-none-match", "if-modified-since"):
                req.headers.pop(h, None)
            # (no return: a page URL can carry a question, e.g. /search?q=..., and is still checked)

        if req.method in WRITE_METHODS and ("json" in ctype or site.is_setup(path) or body[:1] in (b"{", b"[")):
            # An upload is usually announced with a JSON body naming the file (path varies by
            # site and over time); its reply carries the storage URL. Remember the name for that
            # URL, and by announced size (telemetry often carries both, whatever its content type).
            data = bodies.parse_json(body)
            name = bodies.find_key(data, NAME_KEYS) if data is not None else None
            if name and (site.is_setup(path) or _FILE_NAME.match(name)):
                if site.is_setup(path) or "json" in ctype:
                    flow.metadata["ug_setup_name"] = name
                size = bodies.find_value(data, SIZE_KEYS)
                if isinstance(size, (int, float)) or (isinstance(size, str) and size.isdigit()):
                    self._by_size[(site.key, int(size))] = (name, time.monotonic())
            if site.is_setup(path):
                self._log_line({"site": site.key, "kind": "setup", "path": path,
                                "fields": bodies.key_names(data) if data is not None else "not json",
                                "name_found": bool(name)})

        files = self._files_in(flow, site, ctype, body)
        if files:
            await self._check_files(flow, site, files)
            return

        texts = bodies.text_strings(ctype, body, req.pretty_url)
        hit = next(((t, m) for t in texts if (m := marker.find(t))), None)
        if hit is None:
            self._log(flow, site, "send" if site.is_send(path) else "other", marker_found=False)
            return
        text, m = hit
        key = content_key("prompt", text)
        if site.is_quiet(req.pretty_host, path) and not site.is_send(path):
            # telemetry/analytics copying what you typed: let it through only if you already said Continue
            self._log(flow, site, "quiet", marker_found=True)
            if self.decider.remembered(key) is not True:
                self._block(flow)
            return
        if site.is_send(path) or req.method in WRITE_METHODS or self._is_page_request(flow, site):
            self._log(flow, site, "send", marker_found=True)
            allowed, asked = await self.decider.decide(key, lambda: prompt_text(site.key, m.excerpt()))
            if asked:
                audit.record(site.key, "prompt", f"prompt ({len(text)} chars)", "prompt", _word(allowed), "marker")
            if not allowed:
                self._block(flow)
        else:
            # e.g. type-ahead suggestions sent while typing: never let the marker out, don't nag
            self._log(flow, site, "background", marker_found=True)
            if self.decider.remembered(key) is not True:
                self._block(flow)

    async def websocket_message(self, flow: http.HTTPFlow) -> None:
        site = by_key(flow.metadata.get("ug_site", ""))
        if site is None or flow.websocket is None:
            return
        msg = flow.websocket.messages[-1]
        if not msg.from_client:
            return
        text = msg.text if msg.is_text else msg.content.decode("utf-8", "replace")
        m = marker.find(text)
        if m is None:
            return
        allowed, asked = await self.decider.decide(
            content_key("prompt", text), lambda: prompt_text(site.key, m.excerpt()))
        if asked:
            audit.record(site.key, "prompt", f"prompt ({len(text)} chars)", "websocket", _word(allowed), "marker")
        if not allowed:
            msg.drop()

    def response(self, flow: http.HTTPFlow) -> None:
        site = by_key(flow.metadata.get("ug_site", ""))
        if site is None or flow.response is None:
            return
        if flow.metadata.get("ug_setup_name"):
            self._learn_upload_urls(flow, flow.metadata["ug_setup_name"])
        if self.config.recolor and self._is_page(flow, site):
            try:
                nonce = recolor.patch_headers(flow.response.headers)
                css = recolor.css_for(site, self.config.color_light, self.config.color_dark)
                flow.response.text = recolor.inject(flow.response.text or "", css, nonce)
                for h in ("etag", "last-modified"):  # no validators: the next load is a full page again
                    flow.response.headers.pop(h, None)
                self._log_line({"site": site.key, "kind": "page", "path": urlsplit(flow.request.path).path,
                                "status": flow.response.status_code, "styled": True})
            except Exception:  # noqa: BLE001 - never break the page over colours
                log.exception("recolouring failed")
        elif self._is_page_request(flow, site) or "text/html" in flow.response.headers.get("content-type", ""):
            self._log_line({"site": site.key, "kind": "page", "path": urlsplit(flow.request.path).path,
                            "status": flow.response.status_code, "styled": False,
                            "dest": flow.request.headers.get("sec-fetch-dest", ""),
                            "type": flow.response.headers.get("content-type", "").split(";")[0]})

    # ------------------------------------------------------------------ files

    def _files_in(self, flow, site: Site, ctype: str, body: bytes) -> list[tuple[str, str, bytes]]:
        req = flow.request
        if req.method not in WRITE_METHODS or not body:
            return []
        parts = bodies.multipart_parts(ctype, body)
        files = [(p.filename or None, p.content_type, p.data) for p in parts if p.filename is not None]
        if files:
            key_field = next((p.data.decode("utf-8", "replace") for p in parts if p.name == "key" and not p.filename), "")
            if key_field and all(name in ("file", None) for name, _m, _d in files):
                files = [(os.path.basename(key_field) or None, m, d) for _n, m, d in files]
            return files
        if host_matches(req.pretty_host, site.upload_hosts):
            name = (self._name_for(flow) or self._name_by_size(site, len(body))
                    or _name_in_request(req) or _guess_name(req.path))
            return [(name, ctype, body)]
        return []

    async def _check_files(self, flow, site: Site, files) -> None:
        findings = []
        for name, mime, data in files:
            finding = await asyncio.to_thread(inspect_file, name or "", mime, data, self.config)
            findings.append((name, mime, data, finding))
        flagged = []
        for name, mime, data, f in findings:
            if f is None:
                continue
            if name is None:
                # the site hasn't said what the file is called (yet): find it on this Mac by its bytes
                name = await asyncio.to_thread(locate.name_for_bytes, data)
            if name:
                flagged.append(FlaggedFile(name, f.where, f.uninspectable, f.reason))
            else:
                flagged.append(FlaggedFile(_describe(data, mime), f.where, f.uninspectable, f.reason, named=False))
        self._log(flow, site, "upload", marker_found=bool(flagged))
        if not flagged:
            return
        if not self.config.fail_mode == "closed" and all(f.uninspectable for f in flagged):
            return
        key = content_key("file", b"".join(content_key("f", d).encode() for _n, _m, d, _f in findings))
        clean = len(files) - len(flagged)
        allowed, asked = await self.decider.decide(key, lambda: files_text(site.key, flagged, clean),
                                                   cancel_ttl=FILE_CANCEL_TTL)
        if asked:
            for f in flagged:
                reason = f"uninspectable: {f.reason}" if f.uninspectable else "marker"
                audit.record(site.key, "file", f.name, f.where, _word(allowed), reason)
        if not allowed:
            self._block(flow)

    def _learn_upload_urls(self, flow, name: str) -> None:
        resp = flow.response
        data = bodies.parse_json(resp.get_content(strict=False) or b"")
        self._log_line({"kind": "setup-reply", "path": urlsplit(flow.request.path).path, "status": resp.status_code,
                        "type": resp.headers.get("content-type", ""), "encoding": resp.headers.get("content-encoding", ""),
                        "urls": [(urlsplit(u).hostname or "") + urlsplit(u).path for u in bodies.urls_in(data)] if data is not None else "not json"})
        if data is None:
            return
        routed = routed_host_patterns()
        now = time.monotonic()
        for url in bodies.urls_in(data):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            self._names[host + parts.path] = (name, now)
            if not host_matches(host, tuple(routed)):
                log.warning("upload URL host %s is not routed through the guard; add it to sites.py", host)
                self._log_line({"warning": "upload host not routed", "host": host})

    def _name_for(self, flow) -> str | None:
        req = flow.request
        hit = self._names.get(req.pretty_host.lower() + urlsplit(req.path).path)
        if hit and time.monotonic() - hit[1] < NAME_TTL:
            return hit[0]
        return None

    def _name_by_size(self, site: Site, size: int) -> str | None:
        """Fallback: the file this site announced most recently with exactly this size."""
        hit = self._by_size.get((site.key, size))
        if hit and time.monotonic() - hit[1] < NAME_TTL:
            return hit[0]
        return None

    # ------------------------------------------------------------------ helpers

    def _site(self, flow) -> Site | None:
        req = flow.request
        site = site_for_host(req.pretty_host)
        if site is None:
            return None
        if host_matches(req.pretty_host, site.upload_hosts):
            return site_for_origin(req.headers.get("origin") or req.headers.get("referer")) or site
        return site

    @staticmethod
    def _is_page_request(flow, site: Site) -> bool:
        """A request for one of the site's HTML pages. Besides plain navigations this covers a
        service worker fetching the page for the tab (no `document` destination, but still
        asks for text/html)."""
        req = flow.request
        dest = req.headers.get("sec-fetch-dest", "")
        return (req.method == "GET"
                and (dest in ("document", "iframe")
                     or (dest in ("", "empty") and "text/html" in req.headers.get("accept", "")))
                and host_matches(req.pretty_host, site.hosts))

    @staticmethod
    def _is_page(flow, site: Site) -> bool:
        req, resp = flow.request, flow.response
        return (
            req.method == "GET"
            and resp.status_code == 200
            and "text/html" in resp.headers.get("content-type", "")
            and req.headers.get("sec-fetch-dest", "document") in ("document", "iframe", "empty")
            and host_matches(req.pretty_host, site.hosts)
        )

    @staticmethod
    def _block(flow) -> None:
        if flow.request.headers.get("sec-fetch-dest") == "document":
            page = (f"<!doctype html><meta charset=utf-8><title>Blocked</title>"
                    f"<body style='font:16px -apple-system,sans-serif;margin:15vh auto;max-width:520px'>"
                    f"<h2>Sensitive Upload Guard</h2><p>{BLOCK_MESSAGE}</p></body>")
            flow.response = http.Response.make(403, page.encode(), {"Content-Type": "text/html; charset=utf-8"})
            return
        origin = flow.request.headers.get("origin")
        headers = {"Content-Type": "application/json", "X-Upload-Guard": "blocked"}
        if origin:
            headers.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"})
        body = json.dumps({"detail": BLOCK_MESSAGE, "error": {"message": BLOCK_MESSAGE}}).encode()
        flow.response = http.Response.make(403, body, headers)

    def _log(self, flow, site: Site, kind: str, marker_found: bool | None) -> None:
        if not self.config.log_requests:
            return
        req = flow.request
        entry = {
            "site": site.key, "kind": kind, "method": req.method, "host": req.pretty_host,
            "path": urlsplit(req.path).path, "type": req.headers.get("content-type", "").split(";")[0],
            "size": len(req.raw_content or b""), "marker": marker_found,
        }
        if req.method == "GET":
            entry["dest"] = req.headers.get("sec-fetch-dest", "")
            entry["accepts_html"] = "text/html" in req.headers.get("accept", "")
        if kind.startswith("upload"):  # names only, never values
            entry["header_names"] = sorted({k.lower() for k in req.headers.keys()} - {"cookie", "authorization"})
            entry["query_names"] = sorted({k for k, _v in urllib_parse.parse_qsl(urlsplit(req.path).query)})
        self._log_line(entry)

    def _log_line(self, entry: dict) -> None:
        if not self.config.log_requests:
            return
        entry = {"ts": time.strftime("%H:%M:%S"), **entry}
        ensure_home()
        path = home_dir() / "requests.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry) + "\n")


def _guess_name(path: str) -> str | None:
    last = os.path.basename(urlsplit(path).path)
    return last if _FILE_NAME.match(last) else None


def _name_in_request(req) -> str | None:
    """A file name in a Content-Disposition-style header or query value (some storage APIs take one)."""
    values = [v for k, v in req.headers.items() if "disposition" in k.lower() or "filename" in k.lower()]
    values += [v for k, v in urllib_parse.parse_qsl(urlsplit(req.path).query) if k.lower() in ("rscd", "filename", "name")]
    for v in values:
        m = _DISPOSITION_NAME.search(v)
        name = os.path.basename(urllib_parse.unquote(m.group(1) if m else v).strip())
        if _FILE_NAME.match(name):
            return name
    return None


_KIND_WORDS = {filetype.TEXT: "a text file", filetype.PDF: "a PDF", filetype.IMAGE: "an image",
               filetype.ZIP: "a zip or Office file", filetype.BINARY: "a file"}


def _describe(data: bytes, mime: str) -> str:
    """'a text file of 224 bytes', for an upload whose name we couldn't find."""
    kind = _KIND_WORDS.get(filetype.sniff(data, "", mime), "a file")
    n = len(data)
    size = f"{n} bytes" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"
    return f"{kind} of {size}"


def _int(v) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def _word(allowed: bool) -> str:
    return "continue" if allowed else "cancel"
