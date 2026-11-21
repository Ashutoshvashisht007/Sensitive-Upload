"""Paths and settings. Everything lives in ~/.upload-guard (mode 0700)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_ID = "com.walled.upload-guard"


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
    proxy_port: int = 8899  # the guard listens on 127.0.0.1:<port>
    recolor: bool = True  # recolour AI answers
    color_light: str = "#0f766e"
    color_dark: str = "#5eead4"
    log_requests: bool = False  # troubleshooting: log request metadata (never content)

    @property
    def max_inspect_bytes(self) -> int:
        return self.max_inspect_mb * 1024 * 1024


def config_path() -> Path:
    return home_dir() / "config.json"


def load_config() -> Config:
    """Read config.json if present. Unknown keys and values of the wrong type are ignored."""
    cfg = Config()
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return cfg
    for f in fields(Config):
        value = data.get(f.name)
        if isinstance(value, type(getattr(cfg, f.name))):
            setattr(cfg, f.name, value)
    if cfg.fail_mode not in ("closed", "open"):
        cfg.fail_mode = "closed"
    return cfg


def save_setting(name: str, value) -> None:
    ensure_home()
    path = config_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data[name] = value
    path.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(path, 0o600)


class LiveConfig:
    """A Config that re-reads config.json when the file changes (cheap: one stat per access)."""

    def __init__(self):
        self._mtime = None
        self._cfg = load_config()

    def get(self) -> Config:
        try:
            mtime = config_path().stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._mtime = mtime
            self._cfg = load_config()
        return self._cfg


def as_dict(cfg: Config) -> dict:
    return asdict(cfg)
