"""Run mitmproxy in a background thread with the guard add-on."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from pathlib import Path

from mitmproxy import certs, options
from mitmproxy.tools.dump import DumpMaster

from ..config import home_dir
from ..sites import routed_host_patterns

log = logging.getLogger(__name__)

CA_ORG = "Sensitive Upload Guard"
CA_CN = "Sensitive Upload Guard local CA"


def ca_dir() -> Path:
    return home_dir() / "ca"


def ca_cert_path() -> Path:
    return ca_dir() / "mitmproxy-ca-cert.pem"


def ensure_ca() -> Path:
    """Create this Mac's own CA on first use. The private key never leaves ~/.upload-guard/ca."""
    d = ca_dir()
    if not (d / "mitmproxy-ca.pem").exists():
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d.parent, 0o700)
        certs.CertStore.create_store(d, "mitmproxy", 2048, organization=CA_ORG, cn=CA_CN)
    os.chmod(d, 0o700)
    return ca_cert_path()


def allow_host_regexes(patterns: list[str] | None = None) -> list[str]:
    """Only these hosts are decrypted; anything else that reaches the proxy is tunnelled as is."""
    out = []
    for p in patterns or routed_host_patterns():
        if p.startswith("*."):
            out.append(r"(^|\.)" + re.escape(p[2:]) + r"(:\d+)?$")
        else:
            out.append("^" + re.escape(p) + r"(:\d+)?$")
    return out


class _Ready:
    def __init__(self, event: threading.Event):
        self.event = event

    def running(self):
        self.event.set()


class GuardProxy:
    def __init__(self, addons: list, port: int, listen_host: str = "127.0.0.1", extra_options: dict | None = None):
        self.addons = addons
        self.port = port
        self.listen_host = listen_host
        self.extra_options = extra_options or {}
        self.master: DumpMaster | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error: str | None = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 15.0) -> None:
        ensure_ca()
        self._thread = threading.Thread(target=self._thread_main, name="guard-proxy", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or self.error:
            raise RuntimeError(self.error or "proxy did not start in time")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as e:  # noqa: BLE001 - report startup failures to start()
            self.error = self.error or f"{type(e).__name__}: {e}"
        finally:
            self._ready.set()

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        opts = options.Options(listen_host=self.listen_host, listen_port=self.port, confdir=str(ca_dir()))
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        master.options.update(
            http3=False,
            allow_hosts=allow_host_regexes(),
            stream_large_bodies="26m",
            **self.extra_options,
        )
        master.addons.add(*self.addons, _Ready(self._ready))
        self.master = master
        await master.run()
        if not self._ready.is_set():
            self.error = f"could not listen on {self.listen_host}:{self.port} (is something else using it?)"

    def stop(self) -> None:
        if self.master and self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.master.shutdown)
        if self._thread:
            self._thread.join(timeout=5)
