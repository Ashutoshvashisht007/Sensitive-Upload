"""Install the guard on this Mac, and undo it.

install:
  1. create this Mac's own CA (key stays in ~/.upload-guard/ca) and trust it in
     your login keychain, so Chrome accepts the guard's certificates for the AI sites
  2. set a proxy auto-config (PAC) on every network service: only ChatGPT,
     Perplexity and their upload hosts go to 127.0.0.1; everything else DIRECT.
     Previous settings are saved and restored by uninstall
  3. add a login item (LaunchAgent) that starts the app and restarts it if it crashes
uninstall reverses all three.

macOS asks for your password for steps 1 and 2.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from .config import APP_ID, ensure_home, home_dir, load_config
from .proxy.pac import pac_data_url
from .proxy.server import CA_CN, ca_cert_path, ensure_ca


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- certificate

def login_keychain() -> str:
    return str(Path.home() / "Library/Keychains/login.keychain-db")


def is_ca_trusted(cert: Path | None = None) -> bool:
    cert = cert or ca_cert_path()
    if not cert.exists():
        return False
    return _run(["security", "verify-cert", "-c", str(cert), "-p", "ssl", "-k", login_keychain()]).returncode == 0 or \
        _run(["security", "verify-cert", "-c", str(cert)]).returncode == 0


def trust_ca(cert: Path) -> tuple[bool, str]:
    r = _run(["security", "add-trusted-cert", "-r", "trustRoot", "-p", "ssl", "-k", login_keychain(), str(cert)])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def untrust_ca(cert: Path) -> None:
    if cert.exists():
        _run(["security", "remove-trusted-cert", str(cert)])
    while _run(["security", "delete-certificate", "-c", CA_CN, login_keychain()]).returncode == 0:
        pass


# ---------------------------------------------------------------- proxy auto-config

def backup_path() -> Path:
    return home_dir() / "proxy-backup.json"


def network_services() -> list[str]:
    r = _run(["networksetup", "-listallnetworkservices"])
    out = []
    for line in r.stdout.splitlines()[1:]:  # first line explains the asterisk
        line = line.strip()
        if line and not line.startswith("*"):
            out.append(line)
    return out


def get_autoproxy(service: str) -> dict:
    r = _run(["networksetup", "-getautoproxyurl", service])
    url, enabled = None, False
    for line in r.stdout.splitlines():
        if line.startswith("URL:"):
            v = line.split(":", 1)[1].strip()
            url = None if v in ("", "(null)") else v
        elif line.startswith("Enabled:"):
            enabled = line.split(":", 1)[1].strip().lower() == "yes"
    return {"url": url, "enabled": enabled}


def _networksetup(commands: list[list[str]]) -> tuple[bool, str]:
    """Run networksetup commands; if macOS wants an admin, ask once for all of them."""
    failures = [c for c in commands if _run(c).returncode != 0]
    if not failures:
        return True, ""
    script = " && ".join(shlex.join(c) for c in failures)
    r = _run(["osascript", "-e", f"do shell script {json.dumps(script)} with administrator privileges"])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def set_pac(url: str) -> tuple[bool, str, list[str]]:
    services = network_services()
    ensure_home()
    backup = {}
    if backup_path().exists():
        backup = json.loads(backup_path().read_text())
    for s in services:
        current = get_autoproxy(s)
        if s not in backup and not (current["url"] or "").startswith("data:application/x-ns-proxy-autoconfig"):
            backup[s] = current
    backup_path().write_text(json.dumps(backup, indent=2))
    cmds = []
    for s in services:
        cmds += [["networksetup", "-setautoproxyurl", s, url], ["networksetup", "-setautoproxystate", s, "on"]]
    ok, err = _networksetup(cmds)
    return ok, err, services


def restore_pac() -> tuple[bool, str]:
    backup = json.loads(backup_path().read_text()) if backup_path().exists() else {}
    cmds = []
    for s in network_services():
        prev = backup.get(s) or {"url": None, "enabled": False}
        if prev["url"]:
            cmds.append(["networksetup", "-setautoproxyurl", s, prev["url"]])
        cmds.append(["networksetup", "-setautoproxystate", s, "on" if prev["enabled"] else "off"])
    ok, err = _networksetup(cmds)
    if ok and backup_path().exists():
        backup_path().unlink()
    return ok, err


def pac_installed(url: str) -> dict[str, bool]:
    return {s: (lambda c: c["enabled"] and c["url"] == url)(get_autoproxy(s)) for s in network_services()}


# ---------------------------------------------------------------- login item

def agent_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{APP_ID}.plist"


def agent_plist(python: str) -> dict:
    home = home_dir()
    return {
        "Label": APP_ID,
        "ProgramArguments": [python, "-I", "-m", "upload_guard", "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after Quit
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardOutPath": str(home / "app.out"),
        "StandardErrorPath": str(home / "app.out"),
    }


def install_agent(python: str | None = None) -> tuple[bool, str]:
    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(agent_plist(python or sys.executable)))
    domain = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", domain, str(path)])
    r = _run(["launchctl", "bootstrap", domain, str(path)])
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def remove_agent() -> None:
    path = agent_path()
    _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)])
    if path.exists():
        path.unlink()


def agent_loaded() -> bool:
    return _run(["launchctl", "print", f"gui/{os.getuid()}/{APP_ID}"]).returncode == 0


def app_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- entry points

def install(trust: bool = True, system_proxy: bool = True, agent: bool = True) -> list[tuple[str, bool, str]]:
    cfg = load_config()
    steps = []
    cert = ensure_ca()
    steps.append(("Local certificate authority created", True, str(cert)))
    if trust:
        if is_ca_trusted(cert):
            steps.append(("Certificate trusted in your login keychain", True, "already trusted"))
        else:
            ok, err = trust_ca(cert)
            steps.append(("Certificate trusted in your login keychain", ok, err))
    if system_proxy:
        ok, err, services = set_pac(pac_data_url(cfg.proxy_port))
        steps.append((f"Proxy auto-config set on: {', '.join(services) or 'no services'}", ok, err))
    if agent:
        ok, err = install_agent()
        steps.append(("App starts at login (LaunchAgent)", ok, err))
    return steps


def uninstall() -> list[tuple[str, bool, str]]:
    steps = []
    remove_agent()
    steps.append(("Login item removed and app stopped", True, ""))
    ok, err = restore_pac()
    steps.append(("Previous proxy settings restored", ok, err))
    untrust_ca(ca_cert_path())
    steps.append(("Certificate removed from your login keychain", True, ""))
    return steps


def status() -> dict:
    cfg = load_config()
    url = pac_data_url(cfg.proxy_port)
    return {
        "port": cfg.proxy_port,
        "listening": app_listening(cfg.proxy_port),
        "agent": agent_loaded(),
        "ca": ca_cert_path(),
        "ca_exists": ca_cert_path().exists(),
        "ca_trusted": is_ca_trusted(),
        "pac": pac_installed(url),
    }
