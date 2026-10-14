"""A hunt heals again after a fight fought inside its walk (quests.hunt), offline with a bare actor."""

from types import SimpleNamespace

from f1 import nav, quests


def test_a_hurt_from_a_fight_inside_the_walk_is_healed_before_the_next(monkeypatch) -> None:
    """The measured case (the radscorpion caves, an ironman run): healed 27 to 38, then two fights a radscorpion began
    on the hunt's walk (fought inside nav.go_to) left 22; no heal followed with four stimpaks carried, and the next
    radscorpion's turn took 27."""
    hp = [27]
    foe = SimpleNamespace(tile=100 + 200 * 20, address=7)
    events: list = []
    actor = SimpleNamespace(
        snap=lambda: SimpleNamespace(
            screen="map", elevation=0, camera=None, dude=SimpleNamespace(hp=hp[0], tile=100, address=1)
        ),
        max_hp=38,
        mem=None,
        in_combat=lambda: False,
        line_blocked=lambda address: False,
        _count=lambda pid: 4,
        log=SimpleNamespace(emit=lambda kind, **d: events.append((kind, d))),
    )
    monkeypatch.setattr(quests, "foes_of", lambda actor, kind: [foe])
    monkeypatch.setattr(quests, "poison", lambda actor: 0)
    monkeypatch.setattr(nav, "obstacles", lambda mem, elevation, address: SimpleNamespace(blocked=frozenset()))
    heals: list = []
    monkeypatch.setattr(quests, "heal", lambda actor: heals.append(hp[0]) or hp.__setitem__(0, 38))
    walks = iter([22, 0])  # a fight inside the first walk leaves 22; the second ends the test

    def go_to(actor, goals, max_steps=80, fight=True):
        hp[0] = next(walks)
        return False, "too many steps"

    monkeypatch.setattr(nav, "go_to", go_to)
    assert quests.hunt(actor, minutes=1)["why"] == "died"
    assert heals == [27, 22]
