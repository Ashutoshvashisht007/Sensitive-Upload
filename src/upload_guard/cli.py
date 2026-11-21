"""upload-guard run | install | uninstall | status | scan <file>... | dialog-test | log-requests on|off"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from . import __version__, audit


def cmd_run(args) -> int:
    from .app import run

    return run(headless=args.headless)


def _print_steps(steps) -> bool:
    all_ok = True
    for title, ok, detail in steps:
        mark = "ok " if ok else "FAILED"
        print(f"[{mark}] {title}" + (f"  ({detail})" if detail and not ok else ""))
        all_ok = all_ok and ok
    return all_ok


def cmd_install(args) -> int:
    from . import install

    print("Installing Sensitive Upload Guard. macOS will ask for your password.\n")
    ok = _print_steps(install.install(trust=not args.no_trust, system_proxy=not args.no_proxy, agent=not args.no_agent))
    print()
    if ok:
        print("Done. The shield in the menu bar means the guard is running.")
        print("Quit and reopen Chrome so it picks up the new proxy settings.")
    else:
        print("Some steps failed; see above. `upload-guard status` shows the current state.")
    return 0 if ok else 1


def cmd_uninstall(args) -> int:
    from . import install

    ok = _print_steps(install.uninstall())
    print("\nQuit and reopen Chrome so it stops using the guard.")
    return 0 if ok else 1


def cmd_status(args) -> int:
    from . import install

    s = install.status()
    yes = lambda b: "yes" if b else "NO"  # noqa: E731
    print(f"App running (127.0.0.1:{s['port']}): {yes(s['listening'])}")
    print(f"Starts at login:                  {yes(s['agent'])}")
    print(f"Local CA created:                 {yes(s['ca_exists'])}  ({s['ca']})")
    print(f"Local CA trusted:                 {yes(s['ca_trusted'])}")
    for service, on in s["pac"].items():
        print(f"Proxy auto-config on {service + ':':<12} {yes(on)}")
    entries = audit.tail(args.lines)
    print()
    print(f"Last {len(entries)} decisions ({audit.audit_path()}):" if entries else "No decisions yet.")
    for e in entries:
        print(f"  {e['ts']}  {e['site']:<10} {e['kind']:<6} {e['decision']:<8} {e['name']} [{e['where']}]")
    healthy = s["listening"] and s["ca_trusted"] and any(s["pac"].values())
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
        text = files_text("chatgpt", [FlaggedFile("contract.pdf", "page 2"),
                                      FlaggedFile("whiteboard.png", "text in the image")], 1)
    print("Continue" if get_backend().confirm(text) else "Cancel")
    return 0


def cmd_log_requests(args) -> int:
    from .config import home_dir, save_setting

    on = args.state == "on"
    save_setting("log_requests", on)
    print(f"Request logging {'on' if on else 'off'}"
          + (f": method, host, path and size go to {home_dir() / 'requests.log'} (never content)" if on else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="upload-guard", description="Sensitive Upload Guard desktop app.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("run", help="run the app (menu bar + guard); install sets this up to start at login")
    s.add_argument("--headless", action="store_true", help="no menu bar; dialogs still appear")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("install", help="trust the local CA, route the AI sites through the guard, start at login")
    s.add_argument("--no-trust", action="store_true", help="don't add the CA to the login keychain")
    s.add_argument("--no-proxy", action="store_true", help="don't change the system proxy settings")
    s.add_argument("--no-agent", action="store_true", help="don't start the app at login")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall", help="undo everything install did")
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

    s = sub.add_parser("log-requests", help="troubleshooting: log request metadata (never content)")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(func=cmd_log_requests)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
