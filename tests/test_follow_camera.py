"""A copy whose view follows the player by itself (session.camera_follows): the play code does not pan it."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from f1 import action_movement, actions, geometry, session


def centred(tile: int) -> geometry.Camera:
    """tile_set_center's camera for TILE in a 1920 x 981 view."""
    tx, offx = 199 - tile % 200, (1920 - 32) // 2
    if tx & 1:
        tx, offx = tx - 1, offx - 32
    return geometry.Camera(offx, (981 - 16) // 2, tx, tile // 200, 1920, 981)


@pytest.fixture
def actor(monkeypatch):
    pressed: list = []
    monkeypatch.setattr(session, "press", lambda key, *a, **k: pressed.append(key))
    monkeypatch.setattr(session, "press_keys", lambda keys, *a, **k: pressed.extend(keys))
    monkeypatch.setattr(action_movement.time, "sleep", lambda s: None)
    a = object.__new__(actions.Actor)
    a.pressed = pressed
    session.use_copy(Path("copy/FALLOUTW.EXE"), None, headless=True, follows=True)
    yield a
    session.use_copy(None)


def snaps(a, view: int, player: int) -> None:
    a.snap = lambda: SimpleNamespace(screen="map", dude=SimpleNamespace(tile=player), camera=centred(view))


def test_a_following_view_on_the_player_is_not_panned(actor) -> None:
    assert session.camera_follows()
    snaps(actor, 20100, 20102)
    actor.center_view()
    actor.center_view(20300)  # a leg's end: the view follows the player there by itself
    assert actor.pressed == []
    assert actor.still_view()


def test_a_following_view_left_far_from_the_player_is_brought_back_by_home(actor) -> None:
    snaps(actor, 20100, 20100 + 30 * 200)  # 360 px below the centre: moved away by someone else
    actor.center_view()
    assert actor.pressed == ["home"]


def test_the_instance_is_panned_as_before(monkeypatch) -> None:
    session.use_copy(None)
    assert not session.camera_follows()
