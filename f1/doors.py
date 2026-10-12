"""Doors: which scenery objects are doors, their state read from memory, and the doors a walk treats as walls until
the game's state gives them back.

Static (fallout1-ce, each function read against the 1.1 exe): a scenery prototype's type is the int at 0x20 of its
.PRO file (0 a door; proto.cc), and only that type makes a door: obj_use 0x48B6C4 hands a door to obj_use_door, any
other scenery to its script's use procedure. A door object keeps its open flags at +0x3C (protinst.cc obj_is_locked
0x48BE9C): 0x01 open (set_door_state_open 0x48B7FC, set when the opening animation ends), 0x02000000 locked; and
check_door_state 0x48B81C puts OBJECT_OPEN_DOOR (0x10 no-block, 0x20000000 light-through, 0x80000000 shoot-through)
into the object's flags when the open bit is set, so an open door blocks no path and no line of fire.

The player's path never passes a closed door: anim_can_use_door 0x415940 answers no for obj_dude before anything else
(its first test, cmp eax, [obj_dude 0x65F618]), so make_path walks the player round every closed door or finds no
path. Other critters pass one when their body is a biped or a robot and the door is not locked. Fallout 2's walk-
through doors (a proto flag that lets the player's path cross a closed door) do not exist here.

Using a door (obj_use_door 0x48B9C0): its script's use procedure first; a script that overrides (a code door, Vault 13's
outer door) keeps the door as it is; otherwise the door animates open, or shut when it stands open.

Walls: a door the bot could not open (locked, or its script keeps it shut) is a wall for the walks on that map and
elevation until a test of the game's state gives it back (the door's own open bit read from memory, a map or global
variable). The tests are asked before every way is planned, so a door freed in an earlier process is given back
again (Fallout 2's lesson: a run restarted with its stuck doors walled found no way at all).
"""

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache

from f1 import knowledge
from f1.engine_map import Obj as O

DOOR = 0  # SCENERY_TYPE_DOOR
SCENERY_TYPE = 0x20  # in a scenery .PRO file (big-endian)
OPEN, LOCKED, JAMMED = 0x01, 0x02000000, 0x04000000  # the object's open flags (object_types.h DOOR_FLAG_*)
OPEN_FLAGS = O.DOOR_OPEN_FLAGS  # 0x3C
NO_BLOCK = 0x10
OPEN_DOOR = 0x20000000 | 0x80000000 | NO_BLOCK  # object_types.h OBJECT_OPEN_DOOR
BODY_TYPE = 0x190  # a critter .PRO's body type (after 11 header ints, flags, 35 + 35 stats, 18 skills)
BIPED, QUADRUPED, ROBOTIC = 0, 1, 2


@cache
def _proto_bytes(pid: int) -> bytes | None:
    entry = knowledge.proto(pid)
    if not entry or "file" not in entry:
        return None
    from f1.dat import GameFiles

    try:
        return GameFiles(knowledge.INSTANCE).read(entry["file"])
    except KeyError:
        return None


def scenery_type(pid: int) -> int | None:
    """The scenery prototype's type (0 door, 1 stairs, 2 elevator, 3 ladder up, 4 ladder down, 5 generic)."""
    pid &= 0xFFFFFFFF
    if pid >> 24 != 2:
        return None
    entry = knowledge.proto(pid) or {}
    kinds = ("door", "stairs", "elevator", "ladder up", "ladder down", "generic")
    if entry.get("kind") in kinds:
        return kinds.index(entry["kind"])
    raw = _proto_bytes(pid)
    if raw is None or len(raw) < SCENERY_TYPE + 4:
        return None
    return struct.unpack_from(">i", raw, SCENERY_TYPE)[0]


def is_door(pid: int) -> bool:
    """A door as the engine counts one: scenery of the door type, not by its name (a door's entry computer, and the
    big picture beside Vault 13's outer door, are generic scenery whose names hold the word)."""
    return scenery_type(pid) == DOOR


def body_type(pid: int) -> int | None:
    raw = _proto_bytes(pid)
    if raw is None or (pid >> 24) & 0xFF != 1 or len(raw) < BODY_TYPE + 4:
        return None
    return struct.unpack_from(">i", raw, BODY_TYPE)[0]


@dataclass(frozen=True)
class State:
    address: int
    tile: int
    elevation: int
    frame: int
    flags: int
    open_flags: int
    pid: int

    @property
    def open(self) -> bool:
        return bool(self.open_flags & OPEN)

    @property
    def locked(self) -> bool:
        return bool(self.open_flags & LOCKED)

    @property
    def jammed(self) -> bool:
        return bool(self.open_flags & JAMMED)

    @property
    def passable(self) -> bool:
        """No path is blocked by it (its flags carry OBJECT_OPEN_DOOR's no-block)."""
        return bool(self.flags & NO_BLOCK)

    def to_dict(self) -> dict:
        return {"tile": self.tile, "elevation": self.elevation, "frame": self.frame, "open": self.open,
                "locked": self.locked, "passable": self.passable, "flags": hex(self.flags),
                "open_flags": hex(self.open_flags)}  # fmt: skip


def parse(address: int, raw: bytes) -> State:
    i = lambda at: struct.unpack_from("<i", raw, at)[0]
    return State(address, i(O.TILE), i(O.ELEVATION), i(O.FRAME), i(O.FLAGS) & 0xFFFFFFFF,
                 i(OPEN_FLAGS) & 0xFFFFFFFF, i(O.PID) & 0xFFFFFFFF)  # fmt: skip


def state(mem, address: int) -> State:
    return parse(address, mem.read(address, O.SIZE))


def doors(mem, elevation: int | None = None) -> list[State]:
    """Every door object on the map (on ELEVATION), read from the engine's object lists."""
    from f1 import state as game_state

    out = []
    for address in game_state.objects(mem):
        raw = mem.read(address, O.SIZE)
        pid = struct.unpack_from("<i", raw, O.PID)[0] & 0xFFFFFFFF
        if not is_door(pid):
            continue
        s = parse(address, raw)
        if elevation is None or s.elevation == elevation:
            out.append(s)
    return out


def at(mem, tile: int, elevation: int) -> State | None:
    return next((d for d in doors(mem, elevation) if d.tile == tile), None)


def closed(mem, elevation: int) -> dict[int, State]:
    """The elevation's doors that block a path, by tile."""
    return {d.tile: d for d in doors(mem, elevation) if not d.passable}


# --- walls: doors a walk keeps off until the game's state gives them back ---

Test = Callable[[object], bool]  # (mem) -> True once the door is free


def door_open(tile: int, elevation: int) -> Test:
    """A give-back test: the door on TILE stands open (its open bit and no-block, read from memory)."""

    def test(mem) -> bool:
        d = at(mem, tile, elevation)
        return d is not None and d.open and d.passable

    test.__doc__ = f"the door {tile} open"
    return test


def door_unlocked(tile: int, elevation: int) -> Test:
    """A give-back test: the door's lock bit cleared (a key, a lockpick, a script), or the door open."""

    def test(mem) -> bool:
        d = at(mem, tile, elevation)
        return d is not None and (not d.locked or d.passable)

    test.__doc__ = f"the door {tile} unlocked"
    return test


@dataclass
class Wall:
    map: str  # the map file's name without .MAP, upper case
    elevation: int
    tile: int
    why: str
    give_back: Test
    by: str = ""  # what gives it back, for the log


@dataclass
class Walls:
    """The doors walled for the walks, by (map, elevation, tile)."""

    walls: dict[tuple[str, int, int], Wall] = field(default_factory=dict)

    def wall(self, map_name: str, elevation: int, tile: int, why: str, give_back: Test | None = None,
             by: str = "") -> Wall:  # fmt: skip
        """Wall the door (by default given back once its lock bit clears or it stands open)."""
        key = (_map(map_name), elevation, tile)
        test = give_back or door_unlocked(tile, elevation)
        w = Wall(key[0], elevation, tile, why, test, by or (test.__doc__ or ""))
        self.walls[key] = w
        return w

    def on(self, map_name: str, elevation: int) -> frozenset[int]:
        m = _map(map_name)
        return frozenset(t for (wm, e, t) in self.walls if (wm, e) == (m, elevation))

    def give_back(self, mem, map_name: str) -> list[Wall]:
        """The walls on MAP_NAME whose test now holds, removed and returned (asked only on their own map: a map
        variable or a door is the current map's)."""
        m = _map(map_name)
        freed = [w for (wm, _e, _t), w in self.walls.items() if wm == m and w.give_back(mem)]
        for w in freed:
            del self.walls[(w.map, w.elevation, w.tile)]
        return freed


def _map(name: str) -> str:
    return name.upper().split(".")[0]


WALLS = Walls()  # the session's walls


@cache
def hand_shut(script: str) -> bool:
    """A door whose script keeps the player's hand off it: its use procedure begins with "if the user is the
    player: script_overrides()" (obj_use_door then leaves the door as it is). Such a door opens only by something
    else (a terminal's script uses it), so a walk walls it from the start and takes it back once it stands open."""
    from f1 import intdump

    if not script:
        return False
    try:
        text = intdump.dump(script, ["use_p_proc"]).splitlines()
    except (KeyError, ValueError):
        return False
    body = [line.split(": ", 1)[1] for line in text[2:] if ": " in line]
    return (
        len(body) >= 2
        and body[0].startswith("if not (source_obj() == dude_obj()) goto ")
        and body[1] == "script_overrides()"
    )
