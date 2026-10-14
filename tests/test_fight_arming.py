"""A fight's hand made able to attack (Actor.rearm, arm_crosshair) and a fight a foe started weighed before it is
fought (Actor.fight forced, weigh_forced), offline with a bare Actor."""

from types import SimpleNamespace

from f1 import actions, odds, weapons, win32

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
    a.pick_up_dropped = lambda s: False
    monkeypatch.setattr(win32, "wait_for", lambda cond, *x, **k: cond())
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


def test_a_dropped_pistol_is_picked_up_before_the_pack_is_drawn_from(monkeypatch) -> None:
    """The measured case with Ian: the pistol dropped and lay on the player's hex; the Knife from the pack put the
    player in melee, in Ian's line of fire. Picked up first, the draw comes on the next pass (`drew` stays False)."""
    a = bare_actor(monkeypatch)
    a.holster = lambda on=True: a.calls.append(("holster", on)) or False
    a.weapon = lambda: None
    a.pick_up_dropped = lambda s: a.calls.append(("pick",)) or True
    a.ready_weapon = lambda: (_ for _ in ()).throw(AssertionError("the pack before the floor"))
    assert a.rearm(a.snap(), None, False) == (True, None, False)
    assert a.calls == [("holster", False), ("pick",)]


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
    a.forced_group = lambda: ([SimpleNamespace(address=1)], [])
    ok = odds.Odds(0.99, 30.0, 4.0, 3.0, 60, 40, death_high=0.004, accept=True)
    monkeypatch.setattr(odds, "assess", lambda *x, **k: ok)
    from f1 import quests

    monkeypatch.setattr(quests, "flee", lambda actor: (_ for _ in ()).throw(AssertionError("left")))
    assert a.fight(forced=True).reason == "combat over"
    monkeypatch.setattr(odds, "assess", lambda *x, **k: (_ for _ in ()).throw(AssertionError("weighed")))
    assert a.fight().reason == "combat over"


def test_a_shot_worth_taking_comes_before_a_hopeless_one_even_once_blocked() -> None:
    """Vault 15, an ironman run: a mole rat beside the player, its shot blocked once, was never tried again while
    the fight held fire at one 21 hexes off (-25 %); four stimpaks and HP 48 to 7 later the run stood."""
    from types import SimpleNamespace

    from f1.action_combat import target_order

    near = SimpleNamespace(address=1, hp=6)
    far = SimpleNamespace(address=2, hp=6)
    order = target_order([far, near], {1: 95, 2: -25}, {1: 1, 2: 21}, blocked_before={1}, stuck_at=set())
    assert order == [near, far]
    other = SimpleNamespace(address=3, hp=6)  # among shots worth taking, the blocked one still waits
    order = target_order([near, other], {1: 95, 3: 60}, {1: 1, 3: 5}, blocked_before={1}, stuck_at=set())
    assert order == [other, near]


class Fighter(actions.Actor):
    max_hp, max_ap, flare_prep = 40, 9, None


def test_a_foe_whose_aim_was_blocked_is_shot_at_next_turn_after_a_held_fire_turn(monkeypatch) -> None:
    """The measured case (the radscorpion caves, an ironman run): the weighed radscorpion's aim was blocked in the
    first turn, the fight held fire at an onlooker 14 hexes off (7 %) and ended the turn without trying every foe
    again; the first walked up and stung beside the player for seven turns, never shot at."""
    from contextlib import nullcontext

    from f1 import clock, geometry, risk, session

    a = object.__new__(Fighter)
    a.log, a.pid, a.forced_prep, a.mem = Log(), 1, None, None
    here = 20100
    weighed = SimpleNamespace(address=1, tile=geometry.ring(here, 7)[0], hp=26, multihex=True, pid=0)
    onlooker = SimpleNamespace(address=2, tile=geometry.ring(here, 15)[0], hp=26, multihex=True, pid=0)
    combat, turns, shots = [True], [], []
    dude = SimpleNamespace(ap=9, tile=here, hp=30)
    a.snap = lambda: SimpleNamespace(dude=dude, screen="map", camera=None)
    a.in_combat = lambda: combat[0]
    a.my_turn = lambda: True
    a.weigh_forced = lambda: None
    a.forced_group = lambda: ([weighed], [onlooker])
    a.enemies = lambda only=None: [weighed, onlooker]
    a._count = lambda pid: 0
    a.rearm = lambda s, held, drew: (False, actions.Held(PISTOL, 12, -1), drew)
    a.weapon = lambda: actions.Held(PISTOL, 12, -1)
    a.attack_cost = lambda held: 4
    a.attack_reach = lambda held: 30
    a.retreat = lambda foes, ap: None
    a.combat_move_towards = lambda tile, ap: actions.Outcome(False, "no path", {})

    def attack(target):
        shots.append((len(turns), target.address))
        if not turns:
            return actions.Outcome(False, "aim blocked", {})
        combat[0] = False
        return actions.Outcome(True, "swung", {})

    def end_turn():
        turns.append(1)
        weighed.tile = geometry.ring(here, 2)[0]  # it walks up beside the player

    a.attack, a.end_turn = attack, end_turn
    monkeypatch.setattr(win32, "wait_for", lambda cond, *x, **k: cond())
    monkeypatch.setattr(clock, "fast", lambda pid: nullcontext())
    monkeypatch.setattr(session, "press", lambda key: None)
    monkeypatch.setattr(geometry, "on_view", lambda tile, cam, margin=8: True)
    monkeypatch.setattr(risk, "heal_below", lambda max_hp, danger: 0)
    monkeypatch.setattr(risk, "danger", lambda actor, foes, tile: 0)
    monkeypatch.setattr(odds, "armed_at_range", lambda mem, c: False)
    monkeypatch.setattr(odds, "assess", lambda *args: odds.Odds(1, 30, 1, 1, 81, 40, accept=True))
    monkeypatch.setattr(odds, "shot_chance", lambda actor, e, pid, gap: 81 if gap <= 6 else 7)
    out = a.fight(forced=True)
    assert out.reason == "combat over"
    assert shots == [(0, 1), (1, 1)]
    assert not any(d["move"] == "close in" for k, d in a.log.events if k == "tactic")
