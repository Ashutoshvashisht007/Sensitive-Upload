"""upload-guard install | uninstall | status | scan <file>... | dialog-test"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from . import __version__, audit, install as installer


def cmd_install(args) -> int:
    r = installer.install(extra_dirs=args.browser_dir)
    print(f"Launcher:     {r['launcher']}")
    if r["manifests"]:
        for browser, path in r["manifests"].items():
            print(f"Manifest:     {path}  ({browser})")
    else:
        print("Manifest:     no Chrome, Chromium, Edge or Brave profile folder found.")
    print(f"Extension ID: {r['extension_id']}")
    print()
    print("Next: open chrome://extensions, turn on Developer mode,")
    print(f"      click 'Load unpacked' and pick {r['extension_dir']}")
    print("      The toolbar shield turns green once the extension can reach this app.")
    return 0


def cmd_uninstall(args) -> int:
    removed = installer.uninstall(extra_dirs=args.browser_dir)
    for p in removed:
        print(f"Removed {p}")
    if not removed:
        print("Nothing to remove.")
    print("Remove the extension itself from chrome://extensions.")
    return 0


def cmd_status(args) -> int:
    s = installer.status()
    print(f"Extension ID: {s['extension_id']}")
    print(f"Extension:    {s['extension_dir']}")
    print(f"Launcher:     {s['launcher']}  ({'ok' if s['launcher_ok'] else 'missing'})")
    for browser, m in s["manifests"].items():
        state = "ok" if m["valid"] else ("present but stale; run install again" if m["present"] else "missing")
        print(f"{browser + ':':13} {m['path']}  ({state})")
    entries = audit.tail(args.lines)
    print()
    print(f"Last {len(entries)} audit entries ({audit.audit_path()}):" if entries else "No audit entries yet.")
    for e in entries:
        print(f"  {e['ts']}  {e['site']:<10} {e['kind']:<6} {e['decision']:<8} {e['name']} [{e['where']}]")
    healthy = s["launcher_ok"] and any(m["valid"] for m in s["manifests"].values())
    return 0 if healthy else 1


def cmd_scan(args) -> int:
    from .detect import inspect_file

    worst = 0
    for name in args.files:
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"{name}: can't read ({e.strerror})")
            worst = max(worst, 2)
            continue
        mime = mimetypes.guess_type(path.name)[0] or ""
        found = inspect_file(path.name, mime, data)
        if found is None:
            print(f"{name}: clean")
        elif found.uninspectable:
            print(f"{name}: can't inspect ({found.reason}); the user would be asked")
            worst = max(worst, 1)
        else:
            print(f"{name}: WALLED_SENSITIVE found, {found.where}")
            if found.excerpt:
                print(f"    {found.excerpt}")
            worst = max(worst, 1)
    return worst


def cmd_dialog_test(args) -> int:
    from .dialog import FlaggedFile, files_text, get_backend, prompt_text

    if args.kind == "prompt":
        text = prompt_text("chatgpt", "please summarise the WALLED_SENSITIVE board notes for me")
    elif args.kind == "file":
        text = files_text("perplexity", [FlaggedFile("contract.pdf", "page 2")], 0)
    else:
        text = files_text(
            "chatgpt",
            [FlaggedFile("contract.pdf", "page 2"), FlaggedFile("whiteboard.png", "text in the image")],
            1,
        )
    backend = get_backend()
    result = backend.confirm(text)
    print("Continue" if result else "Cancel")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="upload-guard", description="Sensitive Upload Guard desktop app.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("install", help="register the native host with Chrome, Chromium, Edge and Brave")
    s.add_argument("--browser-dir", action="append", type=Path, default=[],
                   help="also write the host manifest into this NativeMessagingHosts folder")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall", help="remove the host manifests and launcher")
    s.add_argument("--browser-dir", action="append", type=Path, default=[])
    s.set_defaults(func=cmd_uninstall)

    s = sub.add_parser("status", help="show what's installed and recent decisions")
    s.add_argument("-n", "--lines", type=int, default=10)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("scan", help="check files for the marker, as uploads are checked")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("dialog-test", help="show a sample confirmation dialog")
    s.add_argument("--kind", choices=["prompt", "file", "files"], default="file")
    s.set_defaults(func=cmd_dialog_test)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
