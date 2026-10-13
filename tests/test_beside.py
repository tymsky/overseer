"""idealist_support.beside: a walk that finds no way is tried again with the obstacles read anew (a walk with no
path twice ended an ironman run at the library's computers)."""

from types import SimpleNamespace

from f1 import geometry, idealist_support


def test_a_blocked_walk_is_tried_again(monkeypatch) -> None:
    walks = []
    monkeypatch.setattr(idealist_support.time, "sleep", lambda s: None)
    monkeypatch.setattr(idealist_support.watchdog, "quiet", lambda s: None)
    monkeypatch.setattr(idealist_support.nav, "obstacles", lambda mem, e, a: SimpleNamespace(blocked=set()))

    def go_to(actor, goals, max_steps):
        walks.append(sorted(goals))
        return (len(walks) == 2, "arrived" if len(walks) == 2 else "no path")

    monkeypatch.setattr(idealist_support.nav, "go_to", go_to)
    actor = SimpleNamespace(
        mem=None, snap=lambda: SimpleNamespace(elevation=0, dude=SimpleNamespace(tile=17312, address=1))
    )
    assert idealist_support.beside(actor, 17533)
    assert len(walks) == 2 and walks[0] == sorted(geometry.ring(17533, 1))
