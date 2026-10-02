"""The guard's tick arithmetic and its refusal rule, with the Windows clock faked."""

from pathlib import Path

import pytest

from f1 import guard as guard_mod
from f1.guard import Guard, OwnerActive, _newer


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    c = {"last": 1_000, "now": 100_000}  # last input long ago
    monkeypatch.setattr(guard_mod.win32, "last_input_tick", lambda: c["last"])
    monkeypatch.setattr(guard_mod.win32, "tick", lambda: c["now"])
    monkeypatch.setattr(guard_mod.time, "sleep", lambda _s: None)
    return c


def test_newer_handles_wraparound() -> None:
    assert _newer(10, 5)
    assert not _newer(5, 10)
    assert not _newer(7, 7)
    assert _newer(3, 0xFFFFFFF0)  # the counter wrapped


def test_owner_input_pauses_the_agent(clock: dict[str, int]) -> None:
    g = Guard(pause_s=30)
    g.check()  # the last input was 99 s ago

    with g.acting():
        clock["last"] = 1_500  # the agent's own input
    g.check()  # still free: that input was ours

    clock["last"] = 2_000  # a person moved the mouse
    with pytest.raises(OwnerActive):
        g.check()
    assert 29 < g.owner_idle_for() <= 30


def test_recent_input_before_the_guard_started_is_the_owners(clock: dict[str, int]) -> None:
    clock["last"] = clock["now"] - 5_000  # 5 s ago, and no record of the agent's own input
    g = Guard(pause_s=30)
    assert 24 < g.owner_idle_for() <= 25


def test_the_agents_own_input_is_remembered_across_processes(clock: dict[str, int], tmp_path: Path) -> None:
    state = tmp_path / "guard.json"
    first = Guard(pause_s=30, state=state)
    with first.acting():
        clock["last"] = clock["now"] - 1_000  # the agent pressed a key a second ago
    second = Guard(pause_s=30, state=state)  # the next CLI command
    second.check()  # that key was ours, not a person's


def test_a_long_lived_guard_counts_input_sent_by_another_guard_as_the_agents(
    clock: dict[str, int], tmp_path: Path
) -> None:
    state = tmp_path / "guard.json"
    actor = Guard(pause_s=30, state=state)  # e.g. the Actor's guard, alive for a whole routine
    helper = Guard(pause_s=30, state=state)  # e.g. session.press(), a new guard per key
    with helper.acting():
        clock["last"] = clock["now"] - 500
    actor.check()  # the helper's key was the agent's: no pause

    clock["last"] = clock["now"] - 100  # then a person
    with pytest.raises(OwnerActive):
        actor.check()
