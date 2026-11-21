"""Per-site rules. The only file that knows anything about ChatGPT or Perplexity.

hosts          the site itself (pages and API); routed through the guard and decrypted
upload_hosts   where the browser sends file bytes (storage buckets); routed too
send_paths     requests that carry a prompt the user sent → dialog when the marker is in them
setup_paths    requests that announce an upload; their JSON names the file, and the
               reply holds the URL the bytes will go to (so the dialog can name the file)
quiet          telemetry / analytics (hosts or paths). Both sites copy what you type into
               these; carrying the marker they're blocked without a dialog, unless you
               already chose Continue for that text
responses      CSS selectors of AI answers, for recolouring

Endpoints change; anything not listed is still checked (see proxy/addon.py).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    hosts: tuple[str, ...]
    upload_hosts: tuple[str, ...]
    send_paths: tuple[str, ...]
    setup_paths: tuple[str, ...]
    responses: tuple[str, ...]
    quiet_hosts: tuple[str, ...] = ()
    quiet_paths: tuple[str, ...] = ()

    def is_send(self, path: str) -> bool:
        return any(re.search(p, path) for p in self.send_paths)

    def is_setup(self, path: str) -> bool:
        return any(re.search(p, path) for p in self.setup_paths)

    def is_quiet(self, host: str, path: str) -> bool:
        return (host_matches(host, self.quiet_hosts)
                or any(re.search(p, path) for p in self.quiet_paths)
                or bool(_TELEMETRY.search(path)))

    def all_host_patterns(self) -> tuple[str, ...]:
        return self.hosts + self.upload_hosts


CHATGPT = Site(
    key="chatgpt",
    label="ChatGPT",
    hosts=("chatgpt.com", "*.chatgpt.com", "chat.openai.com"),
    upload_hosts=("*.oaiusercontent.com",),
    # /backend-api/f/conversation is the current send endpoint; the others are older or variants.
    send_paths=(r"^/backend-api/(f/)?conversation(/prepare)?$", r"^/backend-api/(f/)?conversation/[^/]+$"),
    setup_paths=(r"^/backend-api/files(/upload_reservations)?$", r"^/backend-api/files/process_upload_stream"),
    responses=(
        '[data-markdown-text-style="assistant-message"]',
        "[data-response-body]",
        '[data-message-author-role="assistant"]',
    ),
    quiet_hosts=("ab.chatgpt.com",),
    quiet_paths=(r"^/ces/",),
)

PERPLEXITY = Site(
    key="perplexity",
    label="Perplexity",
    hosts=("perplexity.ai", "*.perplexity.ai"),
    upload_hosts=("ppl-ai-file-upload.s3.amazonaws.com", "*.ppl-ai-file-upload.s3.amazonaws.com"),
    send_paths=(r"^/rest/sse/perplexity_ask", r"^/rest/.*/ask"),
    setup_paths=(r"^/rest/uploads/",),
    responses=(".prose", '[id^="markdown-content-"]'),
    quiet_hosts=("count.perplexity.ai",),
    quiet_paths=(r"^/rest/event/", r"^/cdn-cgi/rum"),
)

# Generic telemetry-looking paths, for endpoints the lists above don't know yet.
_TELEMETRY = re.compile(r"(telemetry|analytics|/rum\b|/metrics|sdk_exception|/track|/collect|/rgstr|/intake)", re.I)

SITES = (CHATGPT, PERPLEXITY)
_BY_KEY = {s.key: s for s in SITES}


def host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(fnmatch.fnmatchcase(host, p) for p in patterns)


def site_for_host(host: str) -> Site | None:
    for s in SITES:
        if host_matches(host, s.all_host_patterns()):
            return s
    return None


def site_for_origin(origin_or_referer: str | None) -> Site | None:
    if not origin_or_referer:
        return None
    host = urlsplit(origin_or_referer).hostname or ""
    for s in SITES:
        if host_matches(host, s.hosts):
            return s
    return None


def by_key(key: str) -> Site | None:
    return _BY_KEY.get(key)


def site_label(key: str | None) -> str:
    s = _BY_KEY.get((key or "").lower())
    return s.label if s else (key or "the site")


def routed_host_patterns() -> list[str]:
    out: list[str] = []
    for s in SITES:
        out.extend(s.all_host_patterns())
    return out
