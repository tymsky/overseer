"""A home-map visit followed by death/menu must not impersonate the slideshow."""

from types import SimpleNamespace

from f1 import agent_cathedral, ending_goals


def actor_with_screens(monkeypatch, screens):
    actor = SimpleNamespace(
        ironman=True, log=SimpleNamespace(emit=lambda *args, **kwargs: None), skip_cinema=lambda: False
    )
    snapshots = iter(SimpleNamespace(screen=s, map_name=m, experience=1000, level=5) for s, m in screens)
    actor.snap = lambda: next(snapshots)
    monkeypatch.setattr(ending_goals, "audit", lambda a: {"ok": True, "missing": []})
    monkeypatch.setattr(agent_cathedral.time, "sleep", lambda seconds: None)
    return actor


def test_home_cave_and_main_menu_without_slides_are_not_ending_evidence(monkeypatch):
    actor = actor_with_screens(monkeypatch, [("map", "V13ENT.MAP"), ("main_menu", "V13ENT.MAP")])
    assert not agent_cathedral.the_ending(actor, limit_s=20)
    assert not actor.ending_audit["ok"] and not actor.ending_audit["ending_seen"]


def test_observed_endgame_followed_by_menu_preserves_the_audited_finish(monkeypatch):
    actor = actor_with_screens(monkeypatch, [("endgame", "V13ENT.MAP"), ("main_menu", "V13ENT.MAP")])
    assert agent_cathedral.the_ending(actor, limit_s=20)
    assert actor.ending_audit == {"ok": True, "missing": [], "ending_seen": True}
