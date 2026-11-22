"""Pull user content out of HTTP requests: text strings and uploaded files."""

from __future__ import annotations

import email.parser
import email.policy
import json
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

MAX_STRINGS = 5000


@dataclass
class Part:
    name: str  # form field name
    filename: str | None
    content_type: str
    data: bytes


def json_strings(data, limit: int = MAX_STRINGS) -> list[str]:
    """Every string value (and key) in a JSON document, depth-first."""
    out: list[str] = []
    stack = [data]
    while stack and len(out) < limit:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(reversed(node))
    return out


def parse_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def query_strings(url: str) -> list[str]:
    return [v for _k, v in parse_qsl(urlsplit(url).query, keep_blank_values=False)]


def multipart_parts(content_type: str, body: bytes) -> list[Part]:
    if "multipart/" not in (content_type or "").lower():
        return []
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    try:
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    except Exception:  # noqa: BLE001
        return []
    parts = []
    for p in msg.iter_parts() if msg.is_multipart() else []:
        name = p.get_param("name", header="content-disposition") or ""
        parts.append(Part(
            name=str(name),
            filename=p.get_filename(),
            content_type=p.get_content_type(),
            data=p.get_payload(decode=True) or b"",
        ))
    return parts


def text_strings(content_type: str, body: bytes, url: str) -> list[str]:
    """Strings a user could have typed: JSON values, form fields, plain text, URL query values."""
    ct = (content_type or "").lower()
    out = query_strings(url)
    if not body:
        return out
    if "json" in ct or body[:1] in (b"{", b"["):
        data = parse_json(body)
        if data is not None:
            return out + json_strings(data)
    if "x-www-form-urlencoded" in ct:
        try:
            return out + [v for _k, v in parse_qsl(body.decode("utf-8", "replace"))]
        except ValueError:
            pass
    if "multipart/" in ct:
        return out + [p.data.decode("utf-8", "replace") for p in multipart_parts(ct, body) if not p.filename]
    if ct.startswith("text/") or not ct:
        return out + [body.decode("utf-8", "replace")]
    return out


def find_key(data, keys: tuple[str, ...]) -> str | None:
    """First string value whose key is one of `keys`, anywhere in a JSON document."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and isinstance(v, str) and v:
                    return v
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def urls_in(data) -> list[str]:
    return [s for s in json_strings(data) if s.startswith(("https://", "http://"))]


def find_value(data, keys: tuple[str, ...]):
    """First value (any type) whose key is one of `keys`, anywhere in a JSON document."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and v not in (None, ""):
                    return v
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def key_names(data, limit: int = 40) -> list[str]:
    """Field names only (never values), for troubleshooting logs."""
    out, stack = [], [data]
    while stack and len(out) < limit:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return out
