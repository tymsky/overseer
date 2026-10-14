"""The cursor rests off a target before a hover confirms it: the engine names only a new object, so a rest that
leaves the target named makes every check after it read "unchanged" (Vault 15's first shaft: the rope never
clicked)."""

from types import SimpleNamespace

from f1 import action_interaction
from f1.actions import Actor

SHAFT, DUDE = 0x5000, 0x6000
ON_HIM, ABOVE_HEX, ON_SHAFT = (400, 300), (410, 290), (533, 464)


def actor_beside_the_shaft(monkeypatch, predicted=ON_HIM):
    named = {"last": SHAFT}
    names = {ON_HIM: DUDE, ON_SHAFT: SHAFT}  # the point above his hex is drawn over by the shaft, or empty: no change
    actor = Actor.__new__(Actor)
    actor.mem = SimpleNamespace(u32=lambda address: named["last"])
    actor.point = lambda x, y: named.update(last=names.get((x, y), named["last"]))
    actor.snap = lambda: SimpleNamespace(dude=SimpleNamespace(address=DUDE, tile=1), camera=None)
    actor.predict = lambda address, *a: predicted
    actor.log = SimpleNamespace(emit=lambda *a, **kw: None)
    monkeypatch.setattr(action_interaction.geometry, "tile_center", lambda tile, camera: (ABOVE_HEX[0], 314))
    monkeypatch.setattr(action_interaction.win32, "wait_for", lambda predicate, *a: predicate())
    return actor


def test_a_confirm_rests_on_the_player_first_and_then_names_the_shaft(monkeypatch):
    actor = actor_beside_the_shaft(monkeypatch)
    assert actor.confirm(ON_SHAFT, frozenset({SHAFT}), "use_item_on") == SHAFT


def test_with_no_point_on_him_the_hex_rest_is_still_tried(monkeypatch):
    actor = actor_beside_the_shaft(monkeypatch, predicted=None)
    assert actor.confirm(ON_SHAFT, frozenset({SHAFT}), "use_item_on") == 0  # unchanged: reported, not clicked
