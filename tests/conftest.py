import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.upload-guard (audit log, CA, config)."""
    monkeypatch.setenv("UPLOAD_GUARD_HOME", str(tmp_path / "ug-home"))
    monkeypatch.delenv("UPLOAD_GUARD_DIALOG", raising=False)
    return tmp_path / "ug-home"


@pytest.fixture(autouse=True)
def no_spotlight(monkeypatch):
    """Never search the real home folder from tests; test_locate patches its own results."""
    monkeypatch.setattr("upload_guard.locate._spotlight", lambda size, home, timeout: [])
