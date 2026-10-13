"""Buying steps of the Idealist's route (f1/idealist_trading.py)."""

from types import SimpleNamespace

from f1 import idealist_trading, trade
from f1.idealist_trading import PID_RAD_X, from_merchant


def actor_with(count: int) -> SimpleNamespace:
    events = []
    return SimpleNamespace(
        _count=lambda pid: count, holster=lambda on: None, log=SimpleNamespace(emit=lambda *a, **k: events.append(a))
    )


def test_a_step_passes_with_enough_when_the_rest_is_unaffordable(monkeypatch) -> None:
    """Vance sold three Rad-X of four wanted (the fourth unaffordable): the Glow takes two, so the step passes."""
    monkeypatch.setattr(idealist_trading, "hub_map", lambda actor, where: True)
    monkeypatch.setattr(idealist_trading, "settle", lambda actor: None)
    monkeypatch.setattr(trade, "visit", lambda *a, **k: {"bought": {}})
    assert from_merchant("vance", "HUBOLDTN", PID_RAD_X, 4, enough=2)(actor_with(3))
    assert not from_merchant("vance", "HUBOLDTN", PID_RAD_X, 4)(actor_with(3))
    assert not from_merchant("vance", "HUBOLDTN", PID_RAD_X, 4, enough=2)(actor_with(1))
