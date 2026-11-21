"""Register the native messaging host with Chrome-family browsers.

Writes a small launcher script to ~/.upload-guard/bin and a host manifest
into each installed browser's NativeMessagingHosts folder. The extension ID
is derived from the "key" in extension/manifest.json, so it's never
hard-coded twice.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from .config import HOST_NAME, ensure_home, home_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = REPO_ROOT / "extension"

# browser folder → its NativeMessagingHosts subfolder is written only if the browser folder exists
if sys.platform == "darwin":
    _BASE = Path("Library/Application Support")
    BROWSER_DIRS = {
        "Chrome": _BASE / "Google/Chrome",
        "Chromium": _BASE / "Chromium",
        "Edge": _BASE / "Microsoft Edge",
        "Brave": _BASE / "BraveSoftware/Brave-Browser",
    }
else:
    BROWSER_DIRS = {
        "Chrome": Path(".config/google-chrome"),
        "Chromium": Path(".config/chromium"),
        "Edge": Path(".config/microsoft-edge"),
        "Brave": Path(".config/BraveSoftware/Brave-Browser"),
    }


def extension_key(extension_dir: Path = EXTENSION_DIR) -> str:
    manifest = json.loads((extension_dir / "manifest.json").read_text())
    return manifest["key"]


def extension_id(key: str) -> str:
    """Chrome's ID for an extension: first 32 hex chars of sha256(public key), mapped 0-f → a-p."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def launcher_path() -> Path:
    return home_dir() / "bin" / "upload-guard-host"


def manifest_dirs(extra: list[Path] | None = None) -> dict[str, Path]:
    home = Path.home()
    dirs = {name: home / rel / "NativeMessagingHosts"
            for name, rel in BROWSER_DIRS.items() if (home / rel).is_dir()}
    for i, d in enumerate(extra or []):
        dirs[f"extra{i + 1}"] = Path(d)
    return dirs


def host_manifest(ext_id: str) -> dict:
    return {
        "name": HOST_NAME,
        "description": "Sensitive Upload Guard",
        "path": str(launcher_path()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }


def write_launcher(python: str | None = None) -> Path:
    python = python or sys.executable
    path = launcher_path()
    ensure_home()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "# Written by `upload-guard install`. Chrome runs this to start the native host.\n"
        f'exec "{python}" -I -m upload_guard.native_host "$@"\n'
    )
    os.chmod(path, 0o755)
    return path


def install(extra_dirs: list[Path] | None = None, python: str | None = None,
            extension_dir: Path = EXTENSION_DIR) -> dict:
    ext_id = extension_id(extension_key(extension_dir))
    launcher = write_launcher(python)
    manifest = json.dumps(host_manifest(ext_id), indent=2) + "\n"
    written = {}
    for browser, d in manifest_dirs(extra_dirs).items():
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"{HOST_NAME}.json"
        target.write_text(manifest)
        written[browser] = target
    return {"extension_id": ext_id, "launcher": launcher, "manifests": written, "extension_dir": extension_dir}


def uninstall(extra_dirs: list[Path] | None = None) -> list[Path]:
    removed = []
    for d in manifest_dirs(extra_dirs).values():
        target = d / f"{HOST_NAME}.json"
        if target.exists():
            target.unlink()
            removed.append(target)
    launcher = launcher_path()
    if launcher.exists():
        launcher.unlink()
        removed.append(launcher)
    return removed


def status(extension_dir: Path = EXTENSION_DIR) -> dict:
    ext_id = extension_id(extension_key(extension_dir))
    manifests = {}
    for browser, d in manifest_dirs().items():
        target = d / f"{HOST_NAME}.json"
        ok = False
        if target.exists():
            try:
                m = json.loads(target.read_text())
                ok = m.get("path") == str(launcher_path()) and f"chrome-extension://{ext_id}/" in m.get("allowed_origins", [])
            except ValueError:
                ok = False
        manifests[browser] = {"path": target, "present": target.exists(), "valid": ok}
    launcher = launcher_path()
    return {
        "extension_id": ext_id,
        "extension_dir": extension_dir,
        "launcher": launcher,
        "launcher_ok": launcher.exists() and os.access(launcher, os.X_OK),
        "manifests": manifests,
    }
