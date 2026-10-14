"""Objective results and stable route identities, independent of game input."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ObjectiveOutcome:
    status: str
    reason: str = ""
    obligation: str = ""
    resolves: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"achieved", "recoverable", "deferred", "unavailable", "technical_error"}:
            raise ValueError(f"Unknown objective status: {self.status}")

    def __bool__(self) -> bool:
        return self.status == "achieved"

    @classmethod
    def achieved(cls, *, resolves: tuple[str, ...] = ()) -> "ObjectiveOutcome":
        return cls("achieved", resolves=resolves)

    @classmethod
    def recoverable(cls, reason: str) -> "ObjectiveOutcome":
        return cls("recoverable", reason)

    @classmethod
    def deferred(cls, reason: str, obligation: str = "") -> "ObjectiveOutcome":
        return cls("deferred", reason, obligation)

    @classmethod
    def unavailable(cls, reason: str) -> "ObjectiveOutcome":
        return cls("unavailable", reason)


def outcome(value: bool | ObjectiveOutcome) -> ObjectiveOutcome:
    return value if isinstance(value, ObjectiveOutcome) else ObjectiveOutcome("achieved" if value else "recoverable")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def identified_steps(steps: list, scope: str = "route") -> list:
    """Ordinals may change; an objective's route/name identity survives unrelated insertions."""
    seen: Counter = Counter()
    result = []
    for step in steps:
        key = slug(step.name)
        seen[key] += 1
        suffix = f".{seen[key]}" if seen[key] > 1 else ""
        result.append(step if step.id else replace(step, id=f"{scope}.{key}{suffix}"))
    ids = [s.id for s in result]
    if len(ids) != len(set(ids)):
        raise ValueError("A route contains duplicate objective IDs")
    return result


def route_manifest(steps: list) -> dict:
    rows = [
        {
            "id": s.id,
            "name": s.name,
            "requires": list(s.requires),
            "resolves": list(s.resolves),
            "optional": s.optional,
            "tries": s.tries,
            "final": s.final,
            "exploit": s.exploit,
            "obligation": s.obligation,
            "verified_predicate": s.verify is not None,
            "loaded_predicate": s.satisfied is not None,
        }
        for s in identified_steps(steps)
    ]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"objectives": [r["id"] for r in rows], "route_hash": digest, "steps": rows}
