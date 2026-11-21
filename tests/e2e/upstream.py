"""A local stand-in for chatgpt.com, www.perplexity.ai and their storage hosts.

The guard forwards allowed requests here (see RedirectUpstream), so the tests can
check exactly what would have reached the real sites.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MOCKS = Path(__file__).resolve().parents[1] / "mock_sites"
CHATGPT_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'nonce-sitenonce'; "
               "connect-src 'self' https://*.oaiusercontent.com")
STORAGE = "sdmntprnortheu.oaiusercontent.com"


class Upstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self._n = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def next_id(self) -> int:
        with self.lock:
            self._n += 1
            return self._n

    def received(self, method=None, host=None, path=None) -> list[dict]:
        with self.lock:
            return [r for r in self.requests
                    if (method is None or r["method"] == method)
                    and (host is None or r["host"] == host)
                    and (path is None or r["path"].startswith(path))]

    def clear(self):
        with self.lock:
            self.requests.clear()


class Handler(BaseHTTPRequestHandler):
    server: Upstream

    def log_message(self, *a):
        pass

    def _host(self):
        return self.headers.get("Host", "").split(":")[0]

    def _record(self, body=b""):
        self.server.requests.append({
            "method": self.command, "host": self._host(), "path": self.path, "body": body,
            "sha": hashlib.sha256(body).hexdigest(), "headers": {k.lower(): v for k, v in self.headers.items()},
        })

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_OPTIONS(self):
        self._send(204, extra={"Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                               "Access-Control-Allow-Headers": self.headers.get("Access-Control-Request-Headers", "*")})

    def do_GET(self):
        self._record()
        host = self._host()
        if self.path == "/" and host == "chatgpt.com":
            self._send(200, (MOCKS / "chatgpt.html").read_bytes(), "text/html; charset=utf-8",
                       {"Content-Security-Policy": CHATGPT_CSP})
        elif self.path == "/" and host == "www.perplexity.ai":
            # cacheable like the real site: a revalidation would come back 304 with no body
            if self.headers.get("If-None-Match") == '"pplx-v1"':
                self._send(304, b"", "text/html", {"ETag": '"pplx-v1"'})
            else:
                self._send(200, (MOCKS / "perplexity.html").read_bytes(), "text/html; charset=utf-8",
                           {"ETag": '"pplx-v1"', "Cache-Control": "no-cache"})
        elif self.path.startswith("/rest/autosuggest"):
            self._send(200, b"[]")
        elif host == "direct.test":
            self._send(200, b"<html><body>direct</body></html>", "text/html")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        body = self._body()
        self._record(body)
        host, path = self._host(), self.path
        if path.startswith("/backend-api/f/conversation") or path.startswith("/rest/sse/perplexity_ask"):
            self._send(200, b'data: {"ok": true}\n\n', "text/event-stream")
        elif path == "/backend-api/files/upload_reservations":
            n = self.server.next_id()
            self._send(200, json.dumps({"file_id": f"file_{n:04d}",
                                        "upload_url": f"https://{STORAGE}/files/{n:04d}/raw?se=2026&sig=abc"}).encode())
        elif path.startswith("/backend-api/files/upload_reservations/") and path.endswith("/claim_and_finish"):
            self._send(200, b'{"status": "success"}')
        elif path.startswith("/rest/uploads/create_upload_url"):
            n = self.server.next_id()
            self._send(200, json.dumps({"s3_bucket_url": "https://ppl-ai-file-upload.s3.amazonaws.com/",
                                        "fields": {"key": f"uploads/u{n}/${{filename}}", "policy": "p"}}).encode())
        elif host == "ppl-ai-file-upload.s3.amazonaws.com":
            self._send(204)
        else:
            self._send(404, b"{}")

    def do_PUT(self):
        body = self._body()
        self._record(body)
        self._send(201 if self._host().endswith(".oaiusercontent.com") else 404)


class RedirectUpstream:
    """mitmproxy add-on (tests only): send whatever the guard allowed to the local Upstream."""

    def __init__(self, port: int):
        self.port = port

    def request(self, flow):
        if flow.response is not None:
            return
        host = flow.request.pretty_host
        flow.request.scheme = "http"
        flow.request.host = "127.0.0.1"
        flow.request.port = self.port
        flow.request.host_header = host
