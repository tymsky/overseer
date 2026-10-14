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
