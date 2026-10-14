"""Doors, the engine's path finder and the ways between levels and maps (f1/doors.py, f1/pathfind.py, f1/ways.py)."""

import pytest
from conftest import needs_knowledge

from f1 import doors, pathfind, ways
from f1.geometry import Camera, distance, tile_num_in_direction
from f1.scene import Obj, Snapshot

CAM = Camera(offx=304, offy=182, tile_x=100, tile_y=100, view_width=1920, view_height=981)
DOOR_PID, WALL_PID, DUDE_PID, RAT_PID, ROBOT_PID = 0x02000001, 0x03000001, 0x01000001, 0x01000002, 0x01000003


def obj(address: int, tile: int, pid: int, flags: int = 0) -> Obj:
    return Obj(address, address, tile, 0, 0, 0, 0, 0, 0, pid & 0x0F000000 | 1, flags, 0, 0, pid, 0)


def snap(objs: list[Obj], dude: Obj) -> Snapshot:
    by_tile: dict[int, list[Obj]] = {}
    for o in [dude, *objs]:
        by_tile.setdefault(o.tile, []).append(o)
    return Snapshot(CAM, 0, 20100, dude, None, by_tile, 200, (frozenset(), frozenset()))


@pytest.fixture(autouse=True)
def _fake_protos(monkeypatch):
    monkeypatch.setattr(doors, "is_door", lambda pid: pid == DOOR_PID)
    monkeypatch.setattr(doors, "body_type", {DUDE_PID: 0, RAT_PID: 1, ROBOT_PID: 2}.get)


def corridor(start: int, length: int) -> tuple[list[int], list[Obj]]:
    """A straight row of tiles east from START with walls on every other tile round it: the only way is the row."""
    row = [start]
    for _ in range(length):
        row.append(tile_num_in_direction(row[-1], 1))
    walls, address = [], 0x1000
    for t in row:
        for r in range(6):
            n = tile_num_in_direction(t, r)
            if n not in row and all(w.tile != n for w in walls):
                walls.append(obj(address, n, WALL_PID))
                address += 0x84
    return row, walls


def test_idist_is_the_exe_formula():
    assert pathfind.idist(0, 0, 10, 4) == 10 + 4 - 2
    assert pathfind.idist(5, 5, 5, 5) == 0
    assert pathfind.idist(0, 0, -3, -7) == 3 + 7 - 1


def test_a_free_way_is_found_and_ends_on_the_goal():
    dude = obj(0x100, 20100, DUDE_PID)
    path = pathfind.find(snap([], dude), dude, 20100, 20110)
    assert path is not None and path[-1] == 20110 and 20100 not in path
    assert all(distance(a, b) == 1 for a, b in zip([20100, *path], path, strict=False))


def test_a_closed_door_walls_the_player_but_not_once_planned_open():
    row, walls = corridor(20100, 6)
    door = obj(0x500, row[3], DOOR_PID)
    dude = obj(0x100, row[0], DUDE_PID)
    s = snap([*walls, door], dude)
    assert pathfind.find(s, dude, row[0], row[-1]) is None  # anim_can_use_door: never the player
    path = pathfind.find(s, dude, row[0], row[-1], doors_open=frozenset({row[3]}))
    assert path == row[1:]
    assert pathfind.doors_on(s, path, 0) == [row[3]]
    opened = obj(0x500, row[3], DOOR_PID, doors.NO_BLOCK)  # OBJECT_OPEN_DOOR's no-block: no wall, no door on it
    s2 = snap([*walls, opened], dude)
    assert pathfind.find(s2, dude, row[0], row[-1]) == row[1:]
    assert pathfind.doors_on(s2, row[1:], 0) == []


def test_other_critters_pass_unlocked_doors_by_their_body():
    row, walls = corridor(20100, 5)
    door = obj(0x500, row[2], DOOR_PID)
    robot, rat = obj(0x200, row[0], ROBOT_PID), obj(0x300, row[0], RAT_PID)
    s = snap([*walls, door], robot)
    assert pathfind.find(s, robot, row[0], row[-1], is_player=False) == row[1:]
    assert pathfind.find(s, robot, row[0], row[-1], is_player=False, locked=frozenset({0x500})) is None
    assert pathfind.find(snap([*walls, door], rat), rat, row[0], row[-1], is_player=False) is None


def test_avoid_and_the_goal_itself():
    row, walls = corridor(20100, 4)
    dude = obj(0x100, row[0], DUDE_PID)
    s = snap(walls, dude)
    assert pathfind.find(s, dude, row[0], row[-1], avoid=frozenset({row[2]})) is None
    blocked_goal = snap([*walls, obj(0x600, row[-1], WALL_PID)], dude)
    assert pathfind.find(blocked_goal, dude, row[0], row[-1]) == row[1:]  # the goal is not tested
    assert pathfind.find(blocked_goal, dude, row[0], row[-1], check_goal=True) is None


def test_walls_given_back_by_their_test():
    walls = doors.Walls()
    state = {"open": False}
    walls.wall("vault13.map", 0, 13904, "did not open", lambda mem: state["open"], "a test")
    walls.wall("V13ENT", 0, 16290, "hand shut", lambda mem: True)
    assert walls.on("VAULT13", 0) == frozenset({13904})
    assert walls.on("VAULT13", 1) == frozenset()
    assert walls.give_back(None, "VAULT13") == []  # V13ENT's wall is not asked on another map
    state["open"] = True
    assert [w.tile for w in walls.give_back(None, "Vault13.MAP")] == [13904]
    assert walls.on("VAULT13", 0) == frozenset()
    assert walls.on("V13ENT", 0) == frozenset({16290})


def test_exit_walls_are_the_other_destinations_grids():
    exits = {100: (6, 1, 0), 101: (6, 1, 0), 200: (-2, -1, 0), 201: (-2, -1, 0)}
    assert ways.exit_walls(exits, {6}) == frozenset({200, 201})
    assert ways.exit_walls(exits, {-1, -2}) == frozenset({100, 101})


# --- from the game's files ---

needs_files = pytest.mark.skipif(not ways.knowledge.INSTANCE.exists(), reason="no instance")


@needs_knowledge
@needs_files
def test_the_exe_elevator_tables():
    table = ways.elevator_table()
    assert len(table) == ways.ELEVATORS
    # Vault 13's (type 8: elev6's metarule(15, 8)): keys 1-3, one level per elevation of VAULT13
    assert [(lv.key, lv.map, lv.elevation) for lv in table[8]] == [("1", "VAULT13", 0), ("2", "VAULT13", 1),
                                                                  ("3", "VAULT13", 2)]  # fmt: skip
    assert table[1][0].key == "G"  # the one ground-floor key


@needs_knowledge
@needs_files
def test_vault_13_ways_and_a_plan_across_them():
    v13 = ways.ways_of("VAULT13")
    lifts = {(w.elevation, w.key): (w.tile, w.to_elevation) for w in v13 if w.kind == "elevator"}
    assert lifts[(0, "2")] == (13704, 1)
    assert lifts[(1, "3")] == (22104, 2)
    assert all(w.to_map == "VAULT13" for w in v13 if w.kind == "elevator")
    plan = ways.plan(("V13ENT", 0), ("VAULT13", 1))
    assert [(w.kind, w.to_map, w.to_elevation) for w in plan] == [("exit", "VAULT13", 0), ("elevator", "VAULT13", 1)]
    out = ways.plan(("VAULT13", 2), "WORLD MAP")
    assert [w.kind for w in out] == ["elevator", "exit", "exit"] and out[-1].out


@needs_knowledge
@needs_files
def test_stairs_and_ladders_from_their_scripts():
    junk = [w for w in ways.ways_of("JUNKENT") if w.kind == "ladder"]
    assert {(w.elevation, w.tile, w.to_elevation, w.to_tile) for w in junk} == {(0, 13099, 1, 12498),
                                                                                (1, 12298, 0, 12898)}  # fmt: skip
    childrn = [w for w in ways.ways_of("CHILDRN1") if w.script == "chid2twr"]
    assert childrn and all(w.to_map == "CHILDRN2" for w in childrn)  # a load_map: the map's entering place
    assert ways.plan(("LARIPPER", 0), ("LARIPPER", 1))[0].tile == 13877


@needs_knowledge
@needs_files
def test_a_door_the_player_cannot_open_by_hand():
    assert doors.hand_shut("v13door")
    assert not doors.hand_shut("metldoor") and not doors.hand_shut("")


@needs_files
def test_own_door_way_past_the_engines_node_cap(monkeypatch):
    """From Killian's counter (JUNKKILL 25883) to the exit to Gizmo's casino (11071) the engine's search ends at its
    node cap even with the front door (27483) taken as open; the bot's own search plans the way through the door."""
    from types import SimpleNamespace

    from f1 import mapfile, nav

    start, goal, door = 25883, 11071, 27483
    objects = mapfile.read("JUNKKILL").objects
    monkeypatch.setattr(doors, "is_door", {o.pid for o in objects if o.kind == "door"}.__contains__)
    by_tile, blocked, shut = {}, set(), set()
    for n, o in enumerate(objects, 1):
        if o.elevation != 0 or o.tile < 0 or o.pid >> 24 not in (2, 3):
            continue
        by_tile.setdefault(o.tile, []).append(obj(n, o.tile, o.pid, o.flags))
        if not o.flags & doors.NO_BLOCK:
            blocked.add(o.tile)
            if doors.is_door(o.pid):
                shut.add(o.tile)
    dude = obj(0, start, DUDE_PID)
    by_tile.setdefault(start, []).append(dude)
    s = Snapshot(CAM, 0, start, dude, None, by_tile, 200, (frozenset(), frozenset()))
    assert door in shut
    assert pathfind.find(s, dude, start, goal, doors_open=frozenset(shut)) is None

    obs = nav.Obstacles(frozenset(blocked), {goal: (0, 0, 0)}, frozenset(shut))
    monkeypatch.setattr(nav, "obstacles", lambda *a, **k: obs)
    actor = SimpleNamespace(mem=None, snap=lambda: SimpleNamespace(elevation=0, map_name="JUNKKILL.MAP", dude=dude))
    path = ways.own_door_way(actor, goal, doors.Walls())
    assert path is not None and path[-1] == goal and start not in path and door in path


def test_a_fight_a_foe_began_on_the_way_is_fought_and_the_way_goes_on(monkeypatch):
    """Vault 13's cave: a rat began a fight as the bot walked out, the way ended in combat, the step's retry found it
    still on ("not on a safe map") and the run stopped. The fight is fought, then the way planned again."""
    from types import SimpleNamespace

    from f1 import quests

    state = {"combat": False, "screen": "map", "taken": 0}
    dude = SimpleNamespace(hp=30, tile=17090, dead=False)
    fights = []

    def take(actor, way, walls):
        state["taken"] += 1
        if state["taken"] == 1:
            state["combat"] = True
            return ways.Result(False, "unexpected state", {})
        state["screen"] = "worldmap"
        return ways.Result(True, "used", {})

    def fight(forced=False):
        fights.append(forced)
        state["combat"] = False
        return SimpleNamespace(ok=True)

    actor = SimpleNamespace(
        snap=lambda: SimpleNamespace(screen=state["screen"], map_name="V13ENT.SAV", elevation=0, dude=dude),
        in_combat=lambda: state["combat"],
        fight=fight,
    )
    monkeypatch.setattr(ways, "plan", lambda here, goal: [] if state["screen"] == "worldmap" else ["exit"])
    monkeypatch.setattr(ways, "take", take)
    monkeypatch.setattr(quests, "prepare_after_fight", lambda a: True)
    r = ways.go(actor, "WORLD MAP")
    assert r.ok and fights == [True] and state["taken"] == 2
