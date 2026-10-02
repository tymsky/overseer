"""Machine-readable events: one JSON object per line, appended, flushed at once."""

import datetime
import json
import threading
import time
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._lock = threading.Lock()  # a watchdog thread writes too

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
        return event
