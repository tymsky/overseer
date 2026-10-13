"""Actor's takes weighed before any click, its saves through f1.saves (the slot cursor, the driven copy's folder), and
a following view never scrolled by arrow keys."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from f1 import actions, geometry, loot, nav, saves, session, state, ui, watchdog, win32, world
from f1.engine_map import Obj

KNIFE = 0x04


class Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, **data) -> dict:
        self.events.append((kind, data))
        return data


class Mem:
    """An object's inventory length (INV_LENGTH) and elevation: both 0 but the body's 2 items."""

    def __init__(self, items: int = 2) -> None:
        self.n = items

    def i32(self, address: int) -> int:
        return self.n if address == 0x5000 + Obj.INV_LENGTH else 0

    def glob(self, name: str) -> int:
        return 0


def bare_actor(monkeypatch) -> actions.Actor:
    a = object.__new__(actions.Actor)
    a.log, a.mem, a.pid = Log(), Mem(), 1
    a.snap = lambda: SimpleNamespace(elevation=0, screen="map", dude=SimpleNamespace(tile=100))
    clicks: list = []
    a.click = lambda *x, **k: clicks.append(x)
    a.click_object = lambda *x, **k: clicks.append(x) or False
    a.clicks = clicks
    return a


def test_a_body_too_heavy_is_refused_before_any_step_or_click(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    body = SimpleNamespace(address=0x5000, pid=0x01000010, tile=17488, elevation=0, dead=True)
    monkeypatch.setattr(state, "critters", lambda mem: [body])
    weighed: list = []
    monkeypatch.setattr(loot, "weigh_object", lambda actor, address, pid: weighed.append((address, pid)) or False)
    monkeypatch.setattr(nav, "go_to", lambda *x, **k: pytest.fail("a step before the weighing"))
    out = a.loot(17488)
    assert (out.ok, out.reason) == (False, "too heavy")
    assert weighed == [(0x5000, 0x01000010)] and a.clicks == []


def test_an_item_too_heavy_is_refused_before_the_click(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    a._count = lambda pid: 0
    monkeypatch.setattr(world, "things", lambda mem: [SimpleNamespace(address=0x7000, tile=200, pid=KNIFE)])
    monkeypatch.setattr(loot, "weigh_object", lambda actor, address, pid: (address, pid) != (0x7000, KNIFE))
    out = a.pick_up(200, KNIFE)
    assert (out.ok, out.reason) == (False, "too heavy") and a.clicks == []


def test_quicksave_and_quickload_go_through_the_save_screens(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    a.to_map = lambda: True
    calls: list = []
    monkeypatch.setattr(saves, "save", lambda pid, slot, text: calls.append(("save", slot, text)) or [{}] * 9)
    monkeypatch.setattr(saves, "load", lambda pid, slot: calls.append(("load", slot)) or [{}] * 9)
    monkeypatch.setattr(watchdog, "clear", lambda: None)
    assert a.quicksave(3, "agent start").ok
    assert a.quickload(expect_tile=100, slot=3).ok
    assert not a.quickload(expect_tile=101, slot=3).ok  # loaded, but not where the save stood
    assert calls == [("save", 3, "agent start"), ("load", 3), ("load", 3)]


def test_a_failed_save_step_is_a_failed_quicksave(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    a.to_map = lambda: True

    def refuse(pid, slot, text):
        raise saves.SaveError("home: the cursor on slot 1: wanted 1, seen 4", [{}])

    monkeypatch.setattr(saves, "save", refuse)
    out = a.quicksave(1)
    assert not out.ok and "cursor" in out.reason


def test_a_following_view_is_not_scrolled_by_arrow_keys(monkeypatch) -> None:
    pressed: list = []
    monkeypatch.setattr(session, "press", lambda key, *x, **k: pressed.append(key))
    a = object.__new__(actions.Actor)
    cam = geometry.Camera(944, 482, 100, 100, 1920, 981)
    a.snap = lambda: SimpleNamespace(camera=cam, center_tile=20100)
    rests: list = []
    a.still_view = lambda: rests.append(1) or True
    session.use_copy(Path("copy/FALLOUTW.EXE"), None, headless=True, follows=True)
    try:
        far = 100 * 200 + 199 - 100 + 60  # 60 hexes across: off the view
        assert not nav.scroll_towards(a, far)
        assert pressed == [] and rests == [1]
    finally:
        session.use_copy(None)


def test_a_rest_the_place_refuses_is_told_apart_from_a_missing_button(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    screens = ["pipboy"]
    a.snap = lambda: SimpleNamespace(screen=screens[-1], game_time=265418)
    a.in_combat = lambda: False
    a.mem.glob = lambda name: 1
    pressed: list = []
    monkeypatch.setattr(
        session, "press", lambda key, *x, **k: pressed.append(key) or (key == "esc" and screens.append("map"))
    )
    monkeypatch.setattr(session, "click", lambda *x, **k: None)
    monkeypatch.setattr(win32, "wait_for", lambda cond, *x, **k: cond())
    clock = ui.Button(11, 0, 764, 263, 791, 286, (-1, -1, -1, 504, -1, -1))
    monkeypatch.setattr(ui, "find", lambda mem, win, code: clock if code == 504 else None)
    box = [(6, 0x14, 809, 427, 302, 127)]  # the clock's button answered with the engine's message box (measured)
    monkeypatch.setattr(state, "live_windows", lambda mem: [(1, 16, 640, 250, 640, 480), *box])
    assert a.rest("until morning").reason == "refused here"
    box.clear()
    screens[:] = ["pipboy"]
    assert a.rest("until morning").reason == "no button 518"


def test_a_game_line_is_known_by_its_message_id_and_fields(monkeypatch) -> None:
    from f1 import knowledge

    fake = {("MISC", 1007): ("a", "b c"), ("COMBAT", 104): ("x y.",)}  # parts between %-fields, lowered
    monkeypatch.setattr(knowledge, "game_message", lambda name, n: fake.get((name, n), ()))
    assert knowledge.says("A  strong B C", "MISC", 1007)
    assert not knowledge.says("b c", "MISC", 1007)
    assert knowledge.says("X Y.", *actions.AIM_BLOCKED)
    assert not knowledge.says("anything", "COMBAT", 1)  # no such message: nothing matches
