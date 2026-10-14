"""The radscorpion caves' first visit (f1/idealist_sands.py thin_the_caves): what the gate's refusals leave."""

from types import SimpleNamespace

from f1 import idealist_sands as sands


def visit(monkeypatch, why: str, left: int) -> tuple[bool, list]:
    events = []
    actor = SimpleNamespace(log=SimpleNamespace(emit=lambda kind, **kw: events.append((kind, kw))))
    counts = iter([9, left])  # before the hunt, after it
    monkeypatch.setattr(sands.quests, "gvar", lambda a, name: next(counts))
    monkeypatch.setattr(sands.quests, "hunt", lambda a, kind: {"why": why})
    monkeypatch.setattr(sands, "into_the_caves", lambda a: None)
    monkeypatch.setattr(sands, "on_map", lambda a, name: True)
    return sands.thin_the_caves(actor), events


def test_the_gate_refusing_the_first_radscorpion_leaves_all_for_the_second_visit(monkeypatch):
    # an ironman run: 28 % to hit in the dark, a death bound of 0.97; the retry met the same and the run stopped
    ok, events = visit(monkeypatch, "odds 6% (death bound 0.97): not a fight to begin", 9)
    assert ok and events == [("caves_left", {"left": 9, "why": "odds 6% (death bound 0.97): not a fight to begin"})]


def test_a_refusal_after_a_kill_stays_for_its_tail(monkeypatch):
    ok, events = visit(monkeypatch, "odds 2% (death bound 0.99): not a fight to begin", 7)
    assert ok and events[0][0] == "caves_left"


def test_a_hunt_that_ended_otherwise_is_tried_again(monkeypatch):
    assert visit(monkeypatch, "time up", 9) == (False, [])


def test_tandi_returned_home_does_not_suppress_her_reward(monkeypatch):
    actor = SimpleNamespace(tandi_later=False)
    monkeypatch.setattr(sands.quests, "gvar", lambda a, name: 2)
    collected = []
    collect = lambda a: collected.append(True) or True
    assert sands.now_or_later(collect, reward=True)(actor)
    assert sands.after_the_caves(collect, last=True)(actor)
    assert collected == [True, True]


def test_fled_caves_do_not_get_reentered_just_for_a_tail(monkeypatch):
    actor = SimpleNamespace(caves_deferred=True, _count=lambda pid: 0)
    entered = []
    monkeypatch.setattr(sands, "into_the_caves", lambda a: entered.append(True))
    result = sands.scorpion_tail(actor)
    assert result.status == "deferred" and entered == []


def test_the_second_visit_fled_waits_instead_of_stopping_the_run(monkeypatch):
    # an ironman run: Seth's trip reached the caves, a fight on the walk in was refused (death bound 1.0) and fled to
    # the world map; "not reached" was retried at once, met the same and the run stopped for no progress
    actor = SimpleNamespace(caves_deferred=False)
    monkeypatch.setattr(sands.quests, "gvar", lambda a, name: 5)
    monkeypatch.setattr(sands, "on_map", lambda a, name: False)
    result = sands.remaining_cave_outcome(actor)
    assert result.status == "deferred" and result.obligation == "sands.caves"
