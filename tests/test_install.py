"""Installer logic with networksetup / security / launchctl mocked: nothing on this Mac changes."""

import json
import plistlib
import stat
import subprocess

import pytest
from cryptography import x509

from upload_guard import install
from upload_guard.proxy import server
from upload_guard.proxy.pac import pac_data_url


class FakeShell:
    """Records commands; answers networksetup like macOS does."""

    def __init__(self, services=("Wi-Fi", "Thunderbolt Bridge"), autoproxy=None, fail=()):
        self.calls = []
        self.services = list(services)
        self.autoproxy = autoproxy or {}
        self.fail = set(fail)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out, code = "", 0
        if cmd[:2] == ["networksetup", "-listallnetworkservices"]:
            out = "An asterisk (*) denotes that a network service is disabled.\n" + "\n".join(self.services) + "\n*Old VPN\n"
        elif cmd[:2] == ["networksetup", "-getautoproxyurl"]:
            url, enabled = self.autoproxy.get(cmd[2], (None, False))
            out = f"URL: {url or '(null)'}\nEnabled: {'Yes' if enabled else 'No'}\n"
        elif cmd[:2] == ["networksetup", "-setautoproxyurl"]:
            code = 1 if "setautoproxyurl" in self.fail else 0
            if not code:
                self.autoproxy[cmd[2]] = (cmd[3], True)
        elif cmd[:2] == ["networksetup", "-setautoproxystate"]:
            if cmd[2] in self.autoproxy:
                self.autoproxy[cmd[2]] = (self.autoproxy[cmd[2]][0], cmd[3] == "on")
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="")


@pytest.fixture
def shell(monkeypatch):
    fake = FakeShell(autoproxy={"Wi-Fi": ("http://corp/proxy.pac", True)})
    monkeypatch.setattr(install, "_run", fake)
    return fake


def test_ca_is_created_once_with_our_name(isolated_home):
    cert = server.ensure_ca()
    assert cert.exists() and (isolated_home / "ca" / "mitmproxy-ca.pem").exists()
    assert stat.S_IMODE((isolated_home / "ca").stat().st_mode) == 0o700
    c = x509.load_pem_x509_certificate(cert.read_bytes())
    assert "Sensitive Upload Guard local CA" in c.subject.rfc4514_string()
    before = cert.read_bytes()
    server.ensure_ca()
    assert cert.read_bytes() == before


def test_allow_host_regexes():
    import re

    rx = [re.compile(r) for r in server.allow_host_regexes(["chatgpt.com", "*.oaiusercontent.com"])]
    match = lambda h: any(r.search(h) for r in rx)  # noqa: E731
    assert match("chatgpt.com:443") and match("files.oaiusercontent.com:443")
    assert not match("notchatgpt.com:443") and not match("example.com:443")


def test_network_services_and_autoproxy_parsing(shell):
    assert install.network_services() == ["Wi-Fi", "Thunderbolt Bridge"]
    assert install.get_autoproxy("Wi-Fi") == {"url": "http://corp/proxy.pac", "enabled": True}
    assert install.get_autoproxy("Thunderbolt Bridge") == {"url": None, "enabled": False}


def test_set_pac_backs_up_and_restore_puts_it_back(shell):
    url = pac_data_url(8899)
    ok, _err, services = install.set_pac(url)
    assert ok and services == ["Wi-Fi", "Thunderbolt Bridge"]
    assert all(v == (url, True) for v in shell.autoproxy.values())
    backup = json.loads(install.backup_path().read_text())
    assert backup["Wi-Fi"] == {"url": "http://corp/proxy.pac", "enabled": True}

    install.set_pac(url)  # running install twice must not back up our own PAC
    assert json.loads(install.backup_path().read_text())["Wi-Fi"]["url"] == "http://corp/proxy.pac"

    ok, _ = install.restore_pac()
    assert ok
    assert shell.autoproxy["Wi-Fi"] == ("http://corp/proxy.pac", True)
    assert shell.autoproxy["Thunderbolt Bridge"][1] is False
    assert not install.backup_path().exists()


def test_networksetup_asks_for_admin_once_when_needed(monkeypatch):
    fake = FakeShell(fail={"setautoproxyurl"})
    monkeypatch.setattr(install, "_run", fake)
    install.set_pac(pac_data_url(8899))
    admin = [c for c in fake.calls if c[0] == "osascript"]
    assert len(admin) == 1 and "with administrator privileges" in admin[0][-1]
    assert admin[0][-1].count("-setautoproxyurl") == 2  # both services in one prompt


def test_agent_plist():
    p = install.agent_plist("/opt/venv/bin/python")
    assert p["ProgramArguments"] == ["/opt/venv/bin/python", "-I", "-m", "upload_guard", "run"]
    assert p["KeepAlive"] == {"SuccessfulExit": False} and p["RunAtLoad"] is True
    plistlib.dumps(p)  # serialisable


def test_install_agent_writes_plist_and_bootstraps(shell, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, _ = install.install_agent("/opt/venv/bin/python")
    assert ok and install.agent_path().exists()
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in shell.calls)


def test_trust_uses_login_keychain(shell):
    cert = server.ensure_ca()
    install.trust_ca(cert)
    (cmd,) = [c for c in shell.calls if c[:2] == ["security", "add-trusted-cert"]]
    assert "-k" in cmd and cmd[cmd.index("-k") + 1].endswith("login.keychain-db") and cmd[-1] == str(cert)
