"""Kenji's fight stays: the gate weighs him alone (he shoots at Killian first, who shoots back) and refused him at
full HP; fleeing from Killian's store ended in a corner and a death, where every fight he began was fought out."""

from types import SimpleNamespace

from f1 import agent_junktown
from f1.actions import Actor


def test_the_kenji_fight_is_fought_not_left(monkeypatch):
    seen = []
    actor = SimpleNamespace(
        leave_refused=Actor.leave_refused,
        holster=lambda *a: None,
        in_combat=lambda: False,
        fight=lambda only: seen.append((only, actor.leave_refused)),
    )
    monkeypatch.setattr(agent_junktown, "by_day", lambda a: True)
    monkeypatch.setattr(agent_junktown.quests, "talk", lambda *a: True)
    monkeypatch.setattr(agent_junktown.win32, "wait_for", lambda *a: True)
    monkeypatch.setattr(agent_junktown.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent_junktown, "settle", lambda *a: True)
    monkeypatch.setattr(agent_junktown.quests, "find_critter", lambda a, name: None)
    assert agent_junktown.kenji(actor)
    assert seen == [(("Kenji",), False)]
    assert actor.leave_refused is True  # later fights are weighed again


def test_the_route_still_leaves_other_refused_fights():
    assert Actor.leave_refused is True
