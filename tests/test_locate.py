"""Naming an upload from the file on disk (Spotlight is replaced by a fixed list)."""

from upload_guard import locate


def use(monkeypatch, tmp_path, paths):
    monkeypatch.setattr(locate, "_spotlight", lambda size, home, timeout: [str(p) for p in paths])
    monkeypatch.setattr(locate.sys, "platform", "darwin")


def test_matches_bytes_not_just_size(monkeypatch, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"hello world 1")
    b.write_bytes(b"hello world 2")
    use(monkeypatch, tmp_path, [a, b])
    assert locate.name_for_bytes(b"hello world 2", home=tmp_path) == "b.txt"
    assert locate.name_for_bytes(b"hello world 3", home=tmp_path) is None


def test_skips_app_data_and_dependencies(monkeypatch, tmp_path):
    hidden = [tmp_path / "Library" / "App" / "x.txt", tmp_path / "p" / "node_modules" / "x.txt", tmp_path / ".cache" / "x.txt"]
    for p in hidden:
        p.parent.mkdir(parents=True)
        p.write_bytes(b"same")
    use(monkeypatch, tmp_path, hidden)
    assert locate.name_for_bytes(b"same", home=tmp_path) is None
    icloud = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "notes.txt"
    icloud.parent.mkdir(parents=True)
    icloud.write_bytes(b"same")
    use(monkeypatch, tmp_path, [*hidden, icloud])
    assert locate.name_for_bytes(b"same", home=tmp_path) == "notes.txt"


def test_usual_folders_are_tried_first(tmp_path):
    deep = str(tmp_path / "a" / "b" / "c" / "x.txt")
    desk = str(tmp_path / "Desktop" / "x.txt")
    assert locate._rank([deep, desk], tmp_path) == [desk, deep]


def test_does_nothing_off_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(locate.sys, "platform", "linux")
    assert locate.name_for_bytes(b"data", home=tmp_path) is None
