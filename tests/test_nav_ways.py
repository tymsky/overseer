"""The walking (f1/nav.py) and the quest moves (f1/quests.py) on f1.doors and f1.ways: walled doors block, an exit walk
keeps off the other destinations' grids, and the elevator's level comes from the ways."""

import struct
from types import SimpleNamespace

from f1 import doors, nav, quests, ways
from f1.engine_map import Obj

DOOR_PID, EXIT_PID = 0x02000001, 0x05000010


def raw(tile: int, pid: int, data: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    b = bytearray(Obj.SIZE)
    struct.pack_into("<i", b, Obj.TILE, tile)
    struct.pack_into("<I", b, Obj.PID, pid)
    struct.pack_into("<iii", b, 0x3C, *data)
    return bytes(b)


class Mem:
    def __init__(self, objects: dict[int, bytes]):
        self.objects = objects

    def read(self, addr: int, size: int) -> bytes:
        return self.objects[addr][:size]


def test_a_walled_door_blocks_and_is_no_door_to_open(monkeypatch):
    mem = Mem({0x100: raw(5000, DOOR_PID), 0x200: raw(6000, DOOR_PID)})
    monkeypatch.setattr(nav.state, "objects", lambda m: list(m.objects))
    monkeypatch.setattr(doors, "is_door", lambda pid: pid == DOOR_PID)
    free = nav.obstacles(mem, 0, 0x999)
    assert free.doors == {5000, 6000} and free.passable_doors == frozenset()
    walled = nav.obstacles(mem, 0, 0x999, walls=frozenset({5000}))
    assert walled.doors == {6000} and 5000 in walled.blocked and walled.passable_doors == {5000}


class Actor:
    def __init__(self):
        self.mem = None
        self.log = SimpleNamespace(emit=lambda *a, **k: None)

    def snap(self):
        return SimpleNamespace(map_name="V13ENT.MAP", elevation=0, dude=SimpleNamespace(tile=1, address=9))


def test_an_exit_walk_keeps_off_the_other_destinations_grids(monkeypatch):
    exits = {100: (-1, 0, 0), 101: (-1, 0, 0), 200: (7, 1, 0)}  # two grids out, one to map 7
    monkeypatch.setattr(nav, "obstacles", lambda *a, **k: nav.Obstacles(frozenset(), exits))
    seen = {}

    def walk(actor, goals, max_steps, fight, avoid, plan):
        seen["avoid"] = avoid
        return True, "arrived"

    monkeypatch.setattr(nav, "_walk", walk)
    nav.go_to(Actor(), {100}, avoid=frozenset({50}))
    assert seen["avoid"] == {50, 200}  # the other world-map grid is not walled: it leads to the same place
    nav.go_to(Actor(), {200})
    assert seen["avoid"] == {100, 101}
    nav.go_to(Actor(), {300})  # not an exit walk: nothing added
    assert seen["avoid"] == frozenset()


def test_use_elevator_goes_to_the_level_on_its_key(monkeypatch):
    car = ways.Way("elevator", "V13ENT", 0, 1, "VAULT13", 1, 2, key="2")
    monkeypatch.setattr(ways, "ways_of", lambda m: (car,))
    goals = []

    def go(actor, goal, walls=None):
        goals.append(goal)
        return ways.Result(True, "there", {"ways": [{}]})

    monkeypatch.setattr(ways, "go", go)
    monkeypatch.setattr(ways, "where", lambda a: {})
    actor = Actor()
    actor._end = lambda ok, why, detail, action: SimpleNamespace(ok=ok)
    assert quests.use_elevator(actor, "2") and goals == [("VAULT13", 1)]
    assert not quests.use_elevator(actor, "3") and len(goals) == 1
    assert quests.take_exit(actor, "vault13.map") and goals[-1] == "VAULT13"
    assert quests.leave_map(actor) and goals[-1] == "WORLD MAP"
