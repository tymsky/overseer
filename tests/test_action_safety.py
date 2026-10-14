"""Living destinations, interrupted target clicks and bounded passage recovery."""

from types import SimpleNamespace

import pytest

from f1 import actions, dialogue, doors, nav, quests, ways
from f1.action_shared import Outcome
from f1.geometry import Camera


class Actor:
    def __init__(self):
        self.dude = SimpleNamespace(tile=20100, hp=40, address=1, elevation=0, team=0, ap=5, dead=False)
        self.screen, self.map_name, self.elevation = "map", "START.MAP", 0
        self.mem, self.camera = None, Camera(304, 182, 100, 100, 640, 380)
        self.combat = False
        self.events, self.walks = [], []
        self.log = SimpleNamespace(emit=lambda kind, **fields: self.events.append((kind, fields)))

    def snap(self):
        return SimpleNamespace(
            screen=self.screen,
            map_name=self.map_name,
            elevation=self.elevation,
            dude=self.dude,
            camera=self.camera,
            center_tile=20100,
            game_time=1000,
        )

    def in_combat(self):
        return self.combat

    def _end(self, ok, reason, detail, action):
        return Outcome(ok, reason, detail)

    def walk_to(self, tile, **kwargs):
        self.walks.append(tile)
        self.dude.tile = tile
        return Outcome(True, "arrived", {})

    walk_leg = walk_to


@pytest.fixture
def walking(monkeypatch):
    monkeypatch.setattr(nav, "trap_tiles", lambda *args: frozenset())
    monkeypatch.setattr(nav, "keep_centred", lambda *args: None)
    monkeypatch.setattr(nav.geometry, "on_view", lambda *args: True)
    monkeypatch.setattr(nav.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(nav.win32, "wait_for", lambda check, *args: bool(check()))
    monkeypatch.setattr(nav.knowledge, "map_name", lambda index: "DEST.MAP" if index == 7 else "OTHER.MAP")


@pytest.mark.parametrize("screen,hp", [("main_menu", 40), ("map", 0), ("inventory", 40), ("dialogue", 40)])
def test_a_menu_death_or_unexpected_window_never_completes_a_walk(monkeypatch, walking, screen, hp):
    actor = Actor()
    actor.screen, actor.dude.hp = screen, hp
    monkeypatch.setattr(nav.dialogue, "read", lambda mem: SimpleNamespace(active=False, target=0))
    monkeypatch.setattr(nav.state, "critters", lambda mem: [])
    reached, _ = nav._walk(actor, {actor.dude.tile}, 2, True, frozenset(), [])
    assert not reached and not actor.walks


@pytest.mark.parametrize(
    "screen,map_name,elevation,hp,expected",
    [
        ("map", "DEST.MAP", 1, 40, True),
        ("worldmap", "START.MAP", 0, 40, False),
        ("map", "OTHER.MAP", 1, 40, False),
        ("map", "DEST.MAP", 0, 40, False),
        ("main_menu", "DEST.MAP", 1, 40, False),
        ("map", "DEST.MAP", 1, 0, False),
    ],
)
def test_an_exit_requires_its_living_destination(monkeypatch, walking, screen, map_name, elevation, hp, expected):
    actor = Actor()
    monkeypatch.setattr(nav, "obstacles", lambda *args: nav.Obstacles(frozenset(), {20101: (7, 30100, 1)}))

    def walk(tile, **kwargs):
        actor.screen, actor.map_name, actor.elevation, actor.dude.hp = screen, map_name, elevation, hp
        return Outcome(True, "left the map", {})

    actor.walk_to = walk
    assert nav._walk(actor, {20101}, 1, True, frozenset(), [])[0] is expected


@pytest.mark.parametrize("screen,hp", [("main_menu", 40), ("map", 0), ("dialogue", 40), ("inventory", 40)])
def test_flee_does_not_report_a_dead_or_interrupted_player_safe(monkeypatch, walking, screen, hp):
    actor = Actor()
    actor.combat = True
    actor.my_turn = lambda: True
    actor.end_turn = lambda: pytest.fail("ended a turn after escape interruption")
    monkeypatch.setattr(nav, "obstacles", lambda *args: nav.Obstacles(frozenset(), {20101: (-1, 0, 0)}))

    def walk(goals, ap):
        actor.screen, actor.dude.hp = screen, hp
        return Outcome(True, "walked", {})

    actor.combat_walk = walk
    assert not quests.flee(actor, turns=1)


def test_flee_verifies_a_living_exit_out_of_combat(monkeypatch, walking):
    actor = Actor()
    actor.combat, actor.my_turn = True, lambda: True
    monkeypatch.setattr(nav, "obstacles", lambda *args: nav.Obstacles(frozenset(), {20101: (-1, 0, 0)}))

    def walk(goals, ap):
        actor.screen, actor.combat = "worldmap", False
        return Outcome(True, "walked", {})

    actor.combat_walk = walk
    assert quests.flee(actor, turns=1)


def test_a_fight_on_the_final_navigation_step_is_prepared_before_success(monkeypatch, walking):
    actor = Actor()
    monkeypatch.setattr(nav, "obstacles", lambda *args: nav.Obstacles(frozenset(), {}))
    calls = []

    def walk(tile, **kwargs):
        actor.dude.tile, actor.dude.hp, actor.combat = tile, 12, True
        return Outcome(False, "combat", {})

    def fight(forced):
        calls.append(("fight", forced))
        actor.combat = False
        return Outcome(True, "won", {})

    def prepare(current):
        calls.append(("prepare", current.dude.hp))
        current.dude.hp = 40
        return True

    actor.walk_to, actor.fight = walk, fight
    monkeypatch.setattr(quests, "prepare_after_fight", prepare)
    assert nav._walk(actor, {20101}, 1, True, frozenset(), [])[0]
    assert calls == [("fight", True), ("prepare", 12)]


@pytest.mark.parametrize("team", [0, 1])
def test_a_blocked_actor_passage_reobserves_after_a_step_aside(monkeypatch, walking, team):
    actor = Actor()
    actor.dude.tile = 20099
    mate = SimpleNamespace(tile=20100, address=2, team=team, elevation=0, dead=False)
    barrier = {y * 200 + 100 for y in range(200)} - {20100}

    def obstacles(*args):
        blocked = frozenset(barrier | {mate.tile})
        return nav.Obstacles(blocked, {}, critters=frozenset({mate.tile}))

    def settle(seconds):
        mate.tile = 20200

    actor.companions_settle = settle
    monkeypatch.setattr(nav, "obstacles", obstacles)
    monkeypatch.setattr(nav.state, "critters", lambda mem: [mate])
    assert nav._walk(actor, {20105}, 6, True, frozenset(), [])[0]
    assert actor.dude.tile == 20105
    assert len([kind for kind, fields in actor.events if kind == "make_way"]) == 1
    blocked = next(fields for kind, fields in actor.events if kind == "navigation_blocked")
    assert blocked["path_without_actors"] is not None
    assert blocked["actors"] == [{"address": 2, "tile": 20100, "team": team, "hp": None}]


def test_an_intercepted_object_click_closes_the_companion_and_verifies_the_requested_target(monkeypatch, walking):
    actor = Actor()
    actor.mem = SimpleNamespace(u32=lambda address: 0)
    actor.set_mouse_mode = lambda mode: True
    actor.center_view = actor.point = lambda *args: True
    actor.companions_settle = lambda *args: None
    actor.predict = lambda address: None
    actor._art_aims = lambda *args: []
    actor.hovering = lambda *args, **kwargs: 77
    actor._interaction_done = lambda done, targets: actions.Actor._interaction_done(actor, done, targets)
    talk = SimpleNamespace(active=False, target=0, is_line=True, options=[])
    mate = SimpleNamespace(address=2, tile=20101, team=0, elevation=0, dead=False)
    clicks = []

    def click():
        clicks.append(talk.target)
        actor.screen = "dialogue"
        talk.active, talk.target = True, 2 if len(clicks) == 1 else 77

    def choose(mem, index):
        actor.screen, talk.active = "map", False

    actor.click = click
    monkeypatch.setattr(nav.state, "critters", lambda mem: [mate])
    monkeypatch.setattr(dialogue, "read", lambda mem: talk)
    monkeypatch.setattr(dialogue, "choose", choose)
    assert actions.Actor.click_object(
        actor, 20101, lambda: actor.screen == "dialogue", aims=[(0, 0), (1, 0)], targets=frozenset({77})
    )
    assert len(clicks) == 2 and talk.target == 77


def test_companion_recovery_does_not_choose_an_unverified_exit(monkeypatch, walking):
    actor = Actor()
    actor.screen = "dialogue"
    mate = SimpleNamespace(address=2, tile=20101, team=0, elevation=0, dead=False)
    monkeypatch.setattr(nav.state, "critters", lambda mem: [mate])
    monkeypatch.setattr(
        dialogue,
        "read",
        lambda mem: SimpleNamespace(active=True, target=2, is_line=False, options=["Change formation"]),
    )
    monkeypatch.setattr(dialogue, "choose", lambda *args: pytest.fail("changed companion state"))
    assert not nav.recover_companion_dialogue(actor, (actor.map_name, actor.elevation))


@pytest.mark.parametrize("screen,hp", [("main_menu", 40), ("dialogue", 40), ("map", 0)])
def test_stale_map_data_does_not_complete_a_planned_way(screen, hp):
    actor = Actor()
    actor.screen, actor.dude.hp = screen, hp
    assert not ways.go(actor, "START").ok
    assert not ways.arrived(actor, ways.Way("exit", "SOURCE", 0, 10, "START", 0, 20100))


@pytest.mark.parametrize("screen,hp", [("main_menu", 40), ("map", 0), ("inventory", 40)])
def test_rest_never_sends_recovery_keys_to_a_dead_or_unexpected_screen(monkeypatch, screen, hp):
    actor = Actor()
    actor.screen, actor.dude.hp = screen, hp
    monkeypatch.setattr(actions.session, "press", lambda *args: pytest.fail("rest sent input without a playable map"))
    assert not actions.Actor.rest(actor).ok


def test_a_completed_item_predicate_does_not_accept_an_unexpected_inventory_window():
    actor = Actor()
    actor.screen = "inventory"
    assert not actions.Actor._interaction_done(actor, lambda: True, frozenset())


def test_a_routine_rest_also_refuses_a_poison_emergency(monkeypatch):
    actor = Actor()
    actor.dude.hp = 3
    actor.mem = SimpleNamespace(i32=lambda address: 20)
    actor._count = lambda pid: 0
    monkeypatch.setattr(actions.session, "press", lambda *args: pytest.fail("routine rest advanced lethal poison"))
    assert not actions.Actor.rest(actor, "until morning").ok


@pytest.mark.parametrize("changed_map,expected", [(False, True), (True, False)])
def test_door_planning_recovers_a_companion_passage_on_the_original_map(monkeypatch, walking, changed_map, expected):
    actor = Actor()
    actor.dude.tile = 20099
    mate = SimpleNamespace(tile=20100, address=2, team=0, elevation=0, dead=False)
    barrier = {y * 200 + 100 for y in range(200)} - {20100}
    observed = []

    def obstacles(*args):
        return nav.Obstacles(frozenset(barrier | {mate.tile}), {}, critters=frozenset({mate.tile}))

    def plan(current, goal, *args):
        observed.append((current.map_name, current.dude.tile, mate.tile))
        return nav.astar(current.dude.tile, {goal}, obstacles().blocked), []

    def settle(seconds):
        mate.tile = 20200
        if changed_map:
            actor.map_name = "OTHER.MAP"

    actor.companions_settle = settle
    monkeypatch.setattr(nav, "obstacles", obstacles)
    monkeypatch.setattr(nav.state, "critters", lambda mem: [mate])
    monkeypatch.setattr(ways, "door_way", plan)
    monkeypatch.setattr(ways, "open_way", lambda current, goal, walls: plan(current, goal)[0] is not None)
    assert ways.open_doors(actor, 20105, walls=doors.Walls()).ok is expected
    assert len(actor.walks) == 1
    if expected:
        assert observed[-1][1:] == (actor.dude.tile, 20200)
    else:
        assert all(map_name == "START.MAP" for map_name, tile, mate_tile in observed)


def test_a_crowd_recovery_does_not_claim_a_way_through_a_static_wall():
    wall = frozenset(y * 200 + 100 for y in range(200))
    assert nav.crowd_in_the_way(20099, {20105}, wall | {20101}, frozenset({20101})) == set()


@pytest.mark.parametrize("requested_elevation", [None, 1])
def test_an_unavailable_town_entrance_reaches_the_requested_map_by_local_ways(monkeypatch, requested_elevation):
    actor = Actor()
    actor.map_name = "HOTEL.MAP"
    called = []

    def go(current, goal):
        called.append(goal)
        current.map_name, current.elevation = "WATRSHD.MAP", 1
        return ways.Result(True, "there")

    monkeypatch.setattr(ways, "go", go)
    assert quests._finish_visit_entry(actor, "necropolis", 2, ("WATRSHD", requested_elevation)) == "WATRSHD.MAP"
    assert called == [("WATRSHD", 1) if requested_elevation is not None else "WATRSHD"]


@pytest.mark.parametrize(
    "screen,map_name,hp", [("map", "OTHER.MAP", 40), ("main_menu", "HOTEL.MAP", 40), ("map", "HOTEL.MAP", 0)]
)
def test_a_town_entry_cannot_recover_from_an_unrelated_map_or_dead_state(monkeypatch, screen, map_name, hp):
    actor = Actor()
    actor.screen, actor.map_name, actor.dude.hp = screen, map_name, hp
    monkeypatch.setattr(ways, "go", lambda *args: pytest.fail("navigated from an unverified town entry"))
    assert quests._finish_visit_entry(actor, "necropolis", 2, ("WATRSHD", 1)) == "entry destination not verified"


def test_entry_recovery_rechecks_the_requested_elevation(monkeypatch):
    actor = Actor()
    actor.map_name = "HOTEL.MAP"

    def go(current, goal):
        current.map_name, current.elevation = "WATRSHD.MAP", 0
        return ways.Result(True, "there")

    monkeypatch.setattr(ways, "go", go)
    assert quests._finish_visit_entry(actor, "necropolis", 2, ("WATRSHD", 1)) == "entry destination not verified"


def test_visit_handles_the_first_available_entrance_instead_of_rejecting_the_town(monkeypatch, walking):
    actor = Actor()
    actor.screen, actor.pid = "worldmap", 1
    actor.skip_cinema = lambda: False
    monkeypatch.setattr(quests, "_visit_destination", lambda town, section: ("WATRSHD", 1))
    monkeypatch.setattr(quests, "poison", lambda current: 0)
    monkeypatch.setattr(quests.worldmap, "travel", lambda *args: "arrived")

    def enter(pid, section):
        actor.screen, actor.map_name, actor.elevation = "map", "HOTEL.MAP", 1
        return "map"

    def go(current, goal):
        current.map_name = "WATRSHD.MAP"
        return ways.Result(True, "there")

    monkeypatch.setattr(quests.worldmap, "enter_here", enter)
    monkeypatch.setattr(ways, "go", go)
    assert quests.visit(actor, "necropolis", 2, tries=1) == "WATRSHD.MAP"
    assert any(kind == "visit_entry_fallback" for kind, fields in actor.events)
