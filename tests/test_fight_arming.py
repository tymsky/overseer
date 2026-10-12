"""A fight's hand made able to attack (Actor.rearm, arm_crosshair) and a fight a foe started weighed before it is
fought (Actor.fight forced, weigh_forced), offline with a bare Actor."""

from types import SimpleNamespace

from f1 import actions, odds, weapons

PISTOL, KNUCKLES = 0x08, 0x15


class Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, **data) -> dict:
        self.events.append((kind, data))
        return data


def option(pid: int | None, name: str, score: float) -> weapons.Option:
    return weapons.Option(pid, name, "", 0, 3, 1, 2, 0.5, 2.0, None, score)


def bare_actor(monkeypatch, ap: int = 6) -> actions.Actor:
    a = object.__new__(actions.Actor)
    a.log, a.pid = Log(), 1
    a.calls = []
    a.snap_ = SimpleNamespace(dude=SimpleNamespace(ap=ap, tile=100, hp=40), screen="map")
    a.snap = lambda: a.snap_
    monkeypatch.setattr(actions.win32, "wait_for", lambda cond, *x, **k: cond())
    return a


def test_a_weapon_dropped_by_a_critical_miss_is_replaced_from_the_pack(monkeypatch) -> None:
    """The measured case: the pistol dropped, both hands empty (the bar showed the empty hand), Brass Knuckles and a
    Knife in the pack, 6 AP. The old check drew from the pack only with an item in the active hand: it punched."""
    a = bare_actor(monkeypatch)
    hand: list = [None]
    a.holster = lambda on=True: a.calls.append(("holster", on)) or (hand[0] is None) == on
    a.active_item = lambda: None
    a.weapon = lambda: hand[0]
    a.weapon_options = lambda: [option(KNUCKLES, "Brass Knuckles", 4.9), option(None, "fists", 2.1)]

    def ready():
        a.calls.append(("ready",))
        hand[0] = actions.Held(KNUCKLES, 0, -1)
        return actions.Outcome(True, "ready", {})

    a.ready_weapon = ready
    acted, held, drew = a.rearm(a.snap(), None, False)
    assert acted and drew and held == actions.Held(KNUCKLES, 0, -1)
    assert a.calls == [("holster", False), ("ready",)]
    # once a fight: the next turn punches if the draw failed
    a.calls.clear()
    assert a.rearm(a.snap(), None, True) == (False, None, True) and a.calls == []


def test_fists_best_or_too_few_ap_punch_without_the_inventory(monkeypatch) -> None:
    a = bare_actor(monkeypatch, ap=3)
    a.holster = lambda on=True: on is True
    a.weapon = lambda: None
    a.weapon_options = lambda: [option(KNUCKLES, "Brass Knuckles", 4.9), option(None, "fists", 2.1)]
    a.ready_weapon = lambda: (_ for _ in ()).throw(AssertionError("the inventory at 3 AP"))
    assert a.rearm(a.snap(), None, False) == (False, None, True)
    a.snap_.dude.ap = 6
    a.weapon_options = lambda: [option(None, "fists", 2.1)]
    assert a.rearm(a.snap(), None, False) == (False, None, True)


def test_an_empty_gun_with_no_rounds_gives_way_to_the_next_weapon_else_the_fists(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    monkeypatch.setattr(weapons, "uses_ammo", lambda pid: pid == PISTOL)
    empty = actions.Held(PISTOL, 0, -1)
    hand: list = [empty]
    a.weapon = lambda: hand[0]
    a.reload = lambda: actions.Outcome(False, "no ammo", {})

    def ready():
        hand[0] = actions.Held(KNUCKLES, 0, -1)
        return actions.Outcome(True, "ready", {})

    a.ready_weapon = ready
    assert a.rearm(a.snap(), empty, True) == (True, actions.Held(KNUCKLES, 0, -1), True)
    hand[0] = empty
    a.ready_weapon = lambda: actions.Outcome(True, "fists are best", {})
    a.holster = lambda on=True: hand.__setitem__(0, None) or True
    assert a.rearm(a.snap(), empty, True) == (False, None, True)


class Mouse:
    """gmouse_3d_current_mode: the item button's click does nothing (the measured stuck button); a right click cycles
    move, arrow, crosshair (gmouse_3d_toggle_mode)."""

    def __init__(self) -> None:
        self.mode = 0

    def glob(self, name: str) -> int:
        assert name == "gmouse_3d_current_mode"
        return self.mode


def test_no_crosshair_from_the_item_button_is_taken_by_right_clicks_on_the_map(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    a.mem = Mouse()
    points: list = []
    a.item_button = lambda: (1080, 1040)
    a.point = lambda x, y: points.append((x, y)) or True
    a.item_state = lambda: {"button_disabled": True}

    def click(right: bool = False) -> None:
        if right:
            a.mem.mode = (a.mem.mode + 1) % 3

    a.click = click
    assert a.arm_crosshair(900, 500)
    assert points == [(1080, 1040), (900, 500)] and a.mem.mode == actions.MOUSE_CROSSHAIR
    kind, data = a.log.events[-1]
    assert kind == "crosshair" and data["how"] == "right click" and data["clicks"] == 2


def test_a_fight_a_foe_started_is_weighed_and_a_refused_one_left(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    a.in_combat = lambda: True
    foe = SimpleNamespace(address=1, tile=104, pid=0x0100000B, rotation=0, hp=26)
    a.forced_group = lambda: ([foe], [])
    weighed: list = []
    refused = odds.Odds(0.82, 21.7, 10.1, 8.4, 34, 63, death_high=0.18, accept=False)
    monkeypatch.setattr(odds, "assess", lambda actor, foes, others=(), rule=None: weighed.append(foes) or refused)
    from f1 import quests

    monkeypatch.setattr(quests, "flee", lambda actor: True)
    out = a.fight(forced=True)
    assert (out.ok, out.reason) == (False, "refused; fled") and weighed == [[foe]]
    logged = [d for k, d in a.log.events if k == "odds"]
    assert logged and logged[0]["forced"] is True and logged[0]["accept"] is False and logged[0]["death_high"] == 0.18


def test_an_accepted_or_unforced_fight_is_not_left(monkeypatch) -> None:
    a = bare_actor(monkeypatch)
    combat = [True]
    a.in_combat = lambda: combat.pop(0) if combat else False
    a.forced_group = lambda: ([SimpleNamespace()], [])
    ok = odds.Odds(0.99, 30.0, 4.0, 3.0, 60, 40, death_high=0.004, accept=True)
    monkeypatch.setattr(odds, "assess", lambda *x, **k: ok)
    from f1 import quests

    monkeypatch.setattr(quests, "flee", lambda actor: (_ for _ in ()).throw(AssertionError("left")))
    assert a.fight(forced=True).reason == "combat over"
    monkeypatch.setattr(odds, "assess", lambda *x, **k: (_ for _ in ()).throw(AssertionError("weighed")))
    assert a.fight().reason == "combat over"
