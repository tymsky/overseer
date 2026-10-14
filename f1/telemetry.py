"""Machine-readable events: one JSON object per line, appended, flushed at once."""

import datetime
import json
import threading
import time
from pathlib import Path
from typing import Any


def last_seq(path: Path) -> int:
    """The number of the last whole event in an existing log (0 for none)."""
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, 2) - 65536))
            tail = f.read()
    except OSError:
        return 0
    for line in reversed(tail.splitlines()):
        try:
            return int(json.loads(line)["seq"])
        except (ValueError, KeyError, TypeError):
            continue
    return 0


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = last_seq(path)  # a driver that takes over a run's log carries its numbering on
        self._lock = threading.Lock()  # a watchdog thread writes too
        self.last_failed: tuple[int, str] | None = None  # the last failed action_end: (seq, "action: reason")

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "t": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "mono": round(time.monotonic(), 3),
                "seq": self._seq,
                "kind": kind,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            if kind == "action_end" and fields.get("ok") is False:
                self.last_failed = (self._seq, f"{fields.get('action')}: {fields.get('reason')}")
        return event
