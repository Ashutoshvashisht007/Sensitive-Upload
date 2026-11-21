"""Ask the user, once per piece of content.

The proxy runs on an asyncio loop in a background thread; AppKit dialogs must
run on the main thread. A Runner moves the dialog to the right thread, and the
Decider makes sure that:
  * the same content (same prompt text, same file bytes) is asked about once,
    even if the site sends it in several requests at the same moment;
  * Continue is remembered for a while (retries and follow-up requests pass);
  * Cancel is remembered briefly, so parallel duplicates are dropped silently.
Dialogs are shown one at a time.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import threading
import time
from typing import Callable

from .dialog import DialogText, get_backend

log = logging.getLogger(__name__)

CONTINUE_TTL = 600.0
CANCEL_TTL = 1.5  # just long enough to swallow parallel duplicates


class ThreadRunner:
    """Runs dialogs on one worker thread (tests, headless mode, non-AppKit backends)."""

    def __init__(self, backend=None):
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="dialog")
        self._backend = backend

    def confirm(self, text: DialogText) -> concurrent.futures.Future:
        return self._pool.submit(self._run, text)

    def _run(self, text: DialogText) -> bool:
        if self._backend is None:
            self._backend = get_backend()
        return self._backend.confirm(text)


class MainThreadRunner:
    """Runs dialogs on the AppKit main thread of the menu-bar app."""

    def __init__(self):
        self._backend = None
        self._lock = threading.Lock()

    def confirm(self, text: DialogText) -> concurrent.futures.Future:
        from PyObjCTools import AppHelper

        fut: concurrent.futures.Future = concurrent.futures.Future()

        def run():
            try:
                if self._backend is None:
                    self._backend = get_backend()
                fut.set_result(self._backend.confirm(text))
            except Exception as e:  # noqa: BLE001
                log.exception("dialog failed")
                fut.set_exception(e)

        AppHelper.callAfter(run)
        return fut


def content_key(kind: str, payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return f"{kind}:{hashlib.sha256(data).hexdigest()}"


class Decider:
    def __init__(self, runner, clock: Callable[[], float] = time.monotonic):
        self._runner = runner
        self._clock = clock
        self._remembered: dict[str, tuple[bool, float]] = {}
        self._pending: dict[str, asyncio.Future] = {}

    def forget(self) -> None:
        self._remembered.clear()

    def remembered(self, key: str) -> bool | None:
        hit = self._remembered.get(key)
        if hit and hit[1] > self._clock():
            return hit[0]
        self._remembered.pop(key, None)
        return None

    async def decide(self, key: str, make_text: Callable[[], DialogText]) -> tuple[bool, bool]:
        """Return (allowed, asked). `asked` is False when an earlier answer was reused."""
        known = self.remembered(key)
        if known is not None:
            return known, False
        if key in self._pending:
            return await asyncio.shield(self._pending[key]), False
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[key] = fut
        try:
            try:
                allowed = bool(await asyncio.wrap_future(self._runner.confirm(make_text())))
            except Exception:  # noqa: BLE001 - no dialog means no consent
                log.exception("could not show the dialog; blocking")
                allowed = False
            ttl = CONTINUE_TTL if allowed else CANCEL_TTL
            self._remembered[key] = (allowed, self._clock() + ttl)
            fut.set_result(allowed)
            return allowed, True
        finally:
            self._pending.pop(key, None)
