"""Recolour AI answers: add one <style> to the site's HTML as it passes through.

If the page has a Content-Security-Policy that would block an inline <style>,
the style gets a nonce and only `style-src-elem` is extended. Nothing else in
the policy changes (adding a nonce to a directive that relies on
'unsafe-inline' would switch that off, so those are left alone).
"""

from __future__ import annotations

import re
import secrets

from ..sites import Site

STYLE_ID = "upload-guard-response-color"
DEFAULT_LIGHT = "#0f766e"
DEFAULT_DARK = "#5eead4"
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def css_for(site: Site, light: str = DEFAULT_LIGHT, dark: str = DEFAULT_DARK) -> str:
    light = light if _COLOR.match(light or "") else DEFAULT_LIGHT
    dark = dark if _COLOR.match(dark or "") else DEFAULT_DARK
    targets = ",\n".join(f"{s}, {s} *" for s in site.responses)
    return (
        f":root {{ --upload-guard-response: {light}; }}\n"
        "@media (prefers-color-scheme: dark) {\n"
        '  :root:not(.light):not([data-theme="light"]):not([data-color-scheme="light"]) '
        f"{{ --upload-guard-response: {dark}; }}\n}}\n"
        ':root.dark, :root[data-theme="dark"], :root[data-color-scheme="dark"] '
        f"{{ --upload-guard-response: {dark}; }}\n"
        f"{targets} {{\n"
        "  color: var(--upload-guard-response) !important;\n"
        "  -webkit-text-fill-color: var(--upload-guard-response) !important;\n}\n"
    )


def _directives(policy: str) -> list[list[str]]:
    return [d.split() for d in policy.split(";") if d.strip()]


def _join(directives: list[list[str]]) -> str:
    return "; ".join(" ".join(d) for d in directives)


def _allows_inline(sources: list[str]) -> bool:
    has_nonce_or_hash = any(s.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-")) for s in sources)
    return "'unsafe-inline'" in sources and not has_nonce_or_hash


def patch_policy(policy: str, nonce: str) -> tuple[str, bool]:
    """Return (new policy, nonce_needed) so that <style nonce=…> is allowed."""
    ds = _directives(policy)
    by_name = {d[0].lower(): d for d in ds if d}
    if "style-src-elem" in by_name:
        d = by_name["style-src-elem"]
        if _allows_inline(d[1:]):
            return policy, False
        d.append(f"'nonce-{nonce}'")
        return _join(ds), True
    base = by_name.get("style-src") or by_name.get("default-src")
    if base is None or _allows_inline(base[1:]):
        return policy, False
    ds.append(["style-src-elem", *base[1:], f"'nonce-{nonce}'"])
    return _join(ds), True


def patch_headers(headers) -> str | None:
    """Patch every enforcing CSP header in place. Returns the nonce to use, or None."""
    values = headers.get_all("content-security-policy")
    if not values:
        return None
    nonce = secrets.token_urlsafe(16)
    patched, needed = [], False
    for v in values:
        new, n = patch_policy(v, nonce)
        patched.append(new)
        needed = needed or n
    if needed:
        headers.set_all("content-security-policy", patched)
        return nonce
    return None


def inject(html: str, css: str, nonce: str | None = None) -> str:
    if STYLE_ID in html:
        return html
    attr = f' nonce="{nonce}"' if nonce else ""
    tag = f'<style id="{STYLE_ID}"{attr}>\n{css}</style>'
    m = re.search(r"</head\s*>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + tag + html[m.start():]
    m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if m:
        return html[: m.end()] + tag + html[m.end():]
    return tag + html
