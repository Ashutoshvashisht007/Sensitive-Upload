import json
import os
import stat
from pathlib import Path

import pytest

from upload_guard import install
from upload_guard.config import HOST_NAME


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("UPLOAD_GUARD_HOME", raising=False)
    # pretend Chrome and Brave are installed, Edge and Chromium aren't
    (tmp_path / install.BROWSER_DIRS["Chrome"]).mkdir(parents=True)
    (tmp_path / install.BROWSER_DIRS["Brave"]).mkdir(parents=True)
    return tmp_path


def test_extension_id_matches_chrome_algorithm():
    # Chrome's algorithm: sha256 of the public key → first 32 hex chars → mapped to a-p.
    key = install.extension_key()
    ext_id = install.extension_id(key)
    assert len(ext_id) == 32 and set(ext_id) <= set("abcdefghijklmnop")
    # The ID Chromium assigned when it loaded extension/ (checked in the browser tests).
    assert ext_id == "okhhjehfojbhfinihjkgjjecmapclkan"


def test_install_writes_launcher_and_manifests(fake_home):
    r = install.install(python="/opt/fake/python")
    launcher = fake_home / ".upload-guard" / "bin" / "upload-guard-host"
    assert r["launcher"] == launcher
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    assert '"/opt/fake/python" -I -m upload_guard.native_host "$@"' in launcher.read_text()
    assert stat.S_IMODE((fake_home / ".upload-guard").stat().st_mode) == 0o700

    assert set(r["manifests"]) == {"Chrome", "Brave"}
    for path in r["manifests"].values():
        m = json.loads(path.read_text())
        assert path.name == f"{HOST_NAME}.json"
        assert m["name"] == HOST_NAME and m["type"] == "stdio"
        assert m["path"] == str(launcher)
        assert m["allowed_origins"] == [f"chrome-extension://{r['extension_id']}/"]
    assert not (fake_home / install.BROWSER_DIRS["Edge"]).exists()


def test_status_and_uninstall(fake_home):
    install.install(python="/opt/fake/python")
    s = install.status()
    assert s["launcher_ok"] and s["manifests"]["Chrome"]["valid"]
    removed = install.uninstall()
    assert len(removed) == 3  # two manifests + launcher
    s = install.status()
    assert not s["launcher_ok"] and not s["manifests"]["Chrome"]["present"]


def test_extra_dir(fake_home, tmp_path):
    extra = tmp_path / "profile" / "NativeMessagingHosts"
    r = install.install(extra_dirs=[extra], python="/opt/fake/python")
    assert (extra / f"{HOST_NAME}.json").exists()
    assert r["manifests"]["extra1"] == extra / f"{HOST_NAME}.json"


def test_launcher_runs_host(fake_home):
    """The launcher really starts the host: ping through it."""
    import struct
    import subprocess
    import sys

    from upload_guard.protocol import encode_message

    launcher = install.write_launcher(sys.executable)
    p = subprocess.run([str(launcher), "chrome-extension://x/"], input=encode_message({"id": 1, "type": "ping"}),
                       capture_output=True, timeout=20, env=dict(os.environ, HOME=str(fake_home)))
    (n,) = struct.unpack("@I", p.stdout[:4])
    assert json.loads(p.stdout[4:4 + n])["ok"] is True
