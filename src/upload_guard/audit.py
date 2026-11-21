"""Append-only audit log of decisions. Never stores prompt text or file contents."""

from __future__ import annotations

import json
import os
import time

from .config import ensure_home, home_dir


def audit_path():
    return home_dir() / "audit.jsonl"


def record(site: str, kind: str, name: str, where: str, decision: str, reason: str = "") -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "site": site,
        "kind": kind,  # "prompt" or "file"
        "name": name,  # file name, or "prompt (N chars)"
        "where": where,
        "decision": decision,
        "reason": reason,
    }
    ensure_home()
    path = audit_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def tail(n: int = 10) -> list[dict]:
    try:
        lines = audit_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
