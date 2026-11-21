"""Chrome native messaging: framing and message shapes.

Each message is a 4-byte native-endian length followed by UTF-8 JSON.
Host → browser messages are capped at 1 MB by Chrome; browser → host at 64 MiB.

Requests (browser → host):
    {"id", "type": "ping"}
    {"id", "type": "prompt", "site", "text"}
    {"id", "type": "files", "site", "files": [{"name", "mime", "size", "b64"}]}
        b64 may be null with "skipped": "too_large" when the extension didn't send the bytes.

Replies (host → browser), always carrying the request id:
    {"id", "ok": true, "version", "max_inspect_mb"}
    {"id", "decision": "continue"|"cancel", "found": bool}
    {"id", "decision", "results": [{"name", "found", "uninspectable", "where", "decision"}]}
    {"id", "error": "..."}
"""

from __future__ import annotations

import json
import struct
import threading
from typing import Any, BinaryIO

MAX_TO_BROWSER = 1024 * 1024
MAX_FROM_BROWSER = 64 * 1024 * 1024

CONTINUE, CANCEL = "continue", "cancel"
SITE_NAMES = {"chatgpt": "ChatGPT", "perplexity": "Perplexity"}


class ProtocolError(Exception):
    pass


def read_message(f: BinaryIO) -> dict | None:
    """Read one message. Returns None when the browser closes the pipe."""
    header = f.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("@I", header)
    if length > MAX_FROM_BROWSER:
        raise ProtocolError(f"message too large ({length} bytes)")
    body = f.read(length)
    if len(body) < length:
        return None
    msg = json.loads(body.decode("utf-8"))
    if not isinstance(msg, dict):
        raise ProtocolError("message is not a JSON object")
    return msg


def encode_message(obj: Any) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_TO_BROWSER:
        raise ProtocolError("reply larger than Chrome's 1 MB limit")
    return struct.pack("@I", len(body)) + body


class Writer:
    """Thread-safe writer: the reader thread and the main thread both reply."""

    def __init__(self, f: BinaryIO):
        self._f = f
        self._lock = threading.Lock()

    def send(self, obj: Any) -> None:
        data = encode_message(obj)
        with self._lock:
            self._f.write(data)
            self._f.flush()


def site_name(site: str | None) -> str:
    return SITE_NAMES.get((site or "").lower(), site or "the site")
