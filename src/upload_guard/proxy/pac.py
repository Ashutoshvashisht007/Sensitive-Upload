"""Proxy auto-config: send only the AI sites through the guard.

Everything else goes DIRECT and never touches the proxy. The AI hosts have no
DIRECT fallback, so if the guard isn't running they don't load (fail closed)
while the rest of the web keeps working.

The PAC is installed as a data: URL, so it doesn't depend on a server being up.
"""

from __future__ import annotations

import base64

from ..sites import routed_host_patterns


def pac_script(port: int, patterns: list[str] | None = None) -> str:
    patterns = patterns or routed_host_patterns()
    checks = []
    for p in patterns:
        p = p.lower()
        if p.startswith("*."):
            checks.append(f'dnsDomainIs(h, "{p[1:]}")')
        else:
            checks.append(f'h == "{p}"')
    cond = " ||\n      ".join(checks)
    return (
        "// Sensitive Upload Guard: only these hosts go through the local guard.\n"
        "function FindProxyForURL(url, host) {\n"
        "  var h = host.toLowerCase();\n"
        f"  if ({cond})\n"
        f'    return "PROXY 127.0.0.1:{port}";\n'
        '  return "DIRECT";\n'
        "}\n"
    )


def pac_data_url(port: int) -> str:
    raw = pac_script(port).encode()
    return "data:application/x-ns-proxy-autoconfig;base64," + base64.b64encode(raw).decode()
