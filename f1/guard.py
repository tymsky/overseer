"""The presence guard: no agent input while a person uses the mouse or keyboard, nor for a while after.

A rule proved in unattended sessions. Windows reports only when the last input came, anyone's, so the guard
remembers when its own input ended: newer input than that is a person's. Every input the agent
sends goes through `Guard.acting()`.
"""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from f1 import win32


class OwnerActive(RuntimeError):
    """A person used the mouse or keyboard recently; the agent must not act now."""


def _newer(a: int, b: int) -> bool:
    """Tick a is later than tick b (32-bit wraparound)."""
    return a != b and ((a - b) & 0xFFFFFFFF) < 0x80000000


class Guard:
    """`state` keeps the end of the agent's own input between processes (one CLI command after another)."""

    def __init__(self, pause_s: float = 30.0, state: Path | None = None) -> None:
        self.pause_s = pause_s
        self.state = state
        self._owner_until = 0.0  # time.monotonic() until which the agent refuses to act
        own = self._load()
        last = win32.last_input_tick()
        if own is None or _newer(last, own):
            # Input since the agent's last action (or no record of one): a person's, `idle` seconds ago.
            idle = ((win32.tick() - last) & 0xFFFFFFFF) / 1000
            if idle < pause_s:
                self._owner_until = time.monotonic() + pause_s - idle
            own = last
        self._own_last = own

    def _load(self) -> int | None:
        if self.state is None or not self.state.exists():
            return None
        try:
            return int(json.loads(self.state.read_text(encoding="utf-8"))["own_last"])
        except (ValueError, KeyError):
            return None

    def _save(self) -> None:
        if self.state is not None:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps({"own_last": self._own_last}), encoding="utf-8")

    def _poll(self) -> None:
        own = self._load()
        if own is not None and _newer(own, self._own_last):
            self._own_last = own  # input another guard sent (a helper in this process, or another command)
        last = win32.last_input_tick()
        if _newer(last, self._own_last):
            self._owner_until = time.monotonic() + self.pause_s
            self._own_last = last

    def owner_idle_for(self) -> float:
        """Seconds until the agent may act again (0 when it may act now)."""
        self._poll()
        return max(0.0, self._owner_until - time.monotonic())

    def check(self) -> None:
        wait = self.owner_idle_for()
        if wait > 0:
            raise OwnerActive(f"the user used the mouse or keyboard; pausing {wait:.0f} s more")

    def wait_until_free(self, timeout_s: float) -> bool:
        """Block until the agent may act (True) or the timeout passes (False)."""
        end = time.monotonic() + timeout_s
        while (wait := self.owner_idle_for()) > 0:
            if time.monotonic() + min(wait, 1.0) > end:
                return False
            time.sleep(min(wait, 1.0))
        return True

    @contextmanager
    def acting(self) -> Iterator[None]:
        """Wrap agent input: refuse if a person is active, then count the input sent inside as the agent's."""
        self.check()
        try:
            yield
        finally:
            time.sleep(0.03)  # let the input thread stamp the last input time
            self._own_last = win32.last_input_tick()
            self._save()
