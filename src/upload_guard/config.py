"""Paths and settings. Everything lives in ~/.upload-guard (mode 0700)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

HOST_NAME = "com.walled.upload_guard"


def home_dir() -> Path:
    """~/.upload-guard, or $UPLOAD_GUARD_HOME (used by tests)."""
    override = os.environ.get("UPLOAD_GUARD_HOME")
    return Path(override) if override else Path.home() / ".upload-guard"


def ensure_home() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


@dataclass
class Config:
    max_inspect_mb: int = 25  # larger files aren't inspected; the user is asked
    ocr_max_pages: int = 15  # PDF pages that may be rendered and OCR'd
    fail_mode: str = "closed"  # "closed": files we can't inspect need confirmation

    @property
    def max_inspect_bytes(self) -> int:
        return self.max_inspect_mb * 1024 * 1024


def load_config() -> Config:
    """Read config.json if present. Unknown keys and bad values are ignored."""
    cfg = Config()
    path = home_dir() / "config.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return cfg
    for f in fields(Config):
        value = data.get(f.name)
        if isinstance(value, type(getattr(cfg, f.name))):
            setattr(cfg, f.name, value)
    if cfg.fail_mode not in ("closed", "open"):
        cfg.fail_mode = "closed"
    return cfg
