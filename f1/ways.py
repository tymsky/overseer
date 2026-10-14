"""Ways between levels and maps: stairs and ladders, elevators, and exit grids, read from the game's files; a plan
across them; and each walked with its doors opened, checked against memory.

How each is known (fallout1-ce, read against the 1.1 exe):
- Stairs and ladders: the engine moves no one by itself (obj_use 0x48B6C4 hands non-door scenery to its script; the
  stairs' and ladders' data in the map files hold -1 or 0). Each one's script does it in its use procedure (or a
  timer it starts there): move_to(dude_obj() or source_obj(), TILE, ELEVATION) on the same map, or
  load_map(NAME, ENTRANCE) to another, which puts the player on the new map's entering tile and elevation. So a level
  way is a scenery object whose script holds such a call: the destination read from the script (f1.intdump).
- Elevators: a spatial script on the car's floor (radius 0) calls metarule(15, TYPE) when the player steps on it
  (op_metarule 0x44FA00: rule 15 calls scripts_request_elevator 0x492828), and the engine opens the panel,
  elevator_select 0x437E8C. The panel's keys and where each level is are the exe's tables: retvals 0x437C1C (12
  elevators x 4 levels of {map index, elevation, tile}) and keytable 0x437E5C (12 x 4 chars), read here from the
  running game or the exe file.
- Exit grids: misc objects 0x05000010..17 whose data holds the map (-1 the world map, -2 the town map), tile and
  elevation they lead to; walking onto one changes the map. A walk to one destination's grids keeps off every other
  destination's grids (Fallout 2's lesson: a walk to one map's band crossed another's and left by it).

The plan is a search over (map, elevation) nodes joined by these ways, from the map files; walking a way opens the
closed doors on the engine's own path to it first (f1.pathfind with the doors taken as open, f1.doors' walls kept
off), each door's opening read back from its open flags.
"""

import re
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from f1 import doors, geometry, knowledge
from f1.action_shared import alive

TILE_MASK = 0x3FFFFFF
ELEVATOR_VA, KEYTABLE_VA = 0x437C1C, 0x437E5C  # retvals, keytable (elevator.cc; the exe's bytes read)
ELEVATORS, LEVELS = 12, 4
METARULE_ELEVATOR = 15
WORLD, TOWN = -1, -2

MOVE = re.compile(r"move_to\((?:dude_obj|source_obj)\(\), (\d+), (\d+)\)")
LOAD = re.compile(r"load_map\('([^']+)', (-?\d+)\)")
ELEVATOR_CALL = re.compile(r"metarule\(15, (\d+)\)")


@dataclass(frozen=True)
class Way:
    kind: str  # "stairs", "ladder", "elevator", "exit"
    map: str  # where it is used: the map file's name without .MAP
    elevation: int
    tile: int  # the scenery's tile, the elevator car's floor, or one of the exit's grid tiles
    to_map: str  # "WORLD MAP" / "TOWN MAP" for an exit out
    to_elevation: int
    to_tile: int
    key: str = ""  # an elevator panel's key
    script: str = ""
    tiles: tuple[int, ...] = ()  # an exit's grid tiles

    @property
    def out(self) -> bool:
        return self.to_map in ("WORLD MAP", "TOWN MAP")

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("kind", "map", "elevation", "tile", "to_map", "to_elevation", "to_tile")}
        if self.key:
            d["key"] = self.key
        return d


def _name(map_name: str) -> str:
    return map_name.upper().split(".")[0]


@cache
def script_moves(script: str) -> tuple[tuple[str, int, int], ...]:
    """Where SCRIPT moves the player: ("", tile, elevation) for a move_to on the same map, (MAP, -1, -1) for a
    load_map (the map's entering tile and elevation are filled in by the caller)."""
    from f1 import intdump

    try:
        text = intdump.dump(script)
    except (KeyError, ValueError):
        return ()
    out = [("", int(t), int(e)) for t, e in MOVE.findall(text)]
    out += [(_name(m), -1, -1) for m, _entrance in LOAD.findall(text)]
    return tuple(dict.fromkeys(out))


@cache
def elevator_type(script: str) -> int | None:
    from f1 import intdump

    try:
        found = ELEVATOR_CALL.findall(intdump.dump(script))
    except (KeyError, ValueError):
        return None
    return int(found[0]) if found else None


def _pe_read(path: Path, va: int, size: int) -> bytes:
    """SIZE bytes at virtual address VA of the PE file PATH (its section table mapped by hand)."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    sections, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
    table = pe + 24 + opt_size
    for i in range(sections):
        vsize, vaddr, rsize, raddr = struct.unpack_from("<4I", data, table + 40 * i + 8)
        if vaddr <= va - base < vaddr + max(vsize, rsize):
            off = raddr + va - base - vaddr
            return data[off : off + size]
    raise ValueError(f"{va:#x} not in {path.name}")


@dataclass(frozen=True)
class Level:
    key: str
    map: str
    elevation: int
    tile: int


def elevator_table(mem=None, exe: Path | None = None) -> list[list[Level]]:
    """The exe's elevator tables: per elevator type its levels (key, map, elevation, tile); from the running game's
    memory when MEM is given, else from the exe file (the instance's by default)."""
    size, keys = ELEVATORS * LEVELS * 12, ELEVATORS * LEVELS
    if mem is not None:
        raw, chars = mem.read(ELEVATOR_VA, size), mem.read(KEYTABLE_VA, keys)
    else:
        from f1 import session

        path = exe or session.EXE_PATH
        raw, chars = _pe_read(path, ELEVATOR_VA, size), _pe_read(path, KEYTABLE_VA, keys)
    table = []
    for e in range(ELEVATORS):
        levels = []
        for lv in range(LEVELS):
            m, el, tile = struct.unpack_from("<3i", raw, (e * LEVELS + lv) * 12)
            key = chars[e * LEVELS + lv]
            if tile == -1 or key == 0:
                continue
            levels.append(Level(chr(key), _name(knowledge.map_name(m)), el, tile))
        table.append(levels)
    return table


@cache
def _table() -> tuple[tuple[Level, ...], ...]:
    return tuple(tuple(levels) for levels in elevator_table())


def _mapfile(map_name: str):
    from f1 import mapfile

    return mapfile.read(_name(map_name))


@cache
def ways_of(map_name: str) -> tuple[Way, ...]:
    """Every way off each elevation of MAP_NAME: its stairs and ladders, elevators and exit grids."""
    m = _mapfile(map_name)
    here = _name(map_name)
    out: list[Way] = []
    for o in m.objects:
        if not o.script or o.pid >> 24 != 2 or o.kind == "door":
            continue
        for to_map, tile, elevation in script_moves(o.script):
            kind = "stairs" if o.kind == "stairs" else "ladder"
            if to_map:
                entering = _mapfile(to_map).entering
                out.append(Way(kind, here, o.elevation, o.tile, to_map, entering[1], entering[0], script=o.script))
            else:
                out.append(Way(kind, here, o.elevation, o.tile, here, elevation, tile, script=o.script))
    for script, built, _radius in m.spatial:
        etype = elevator_type(script) if script.lower().startswith("elev") else None
        if etype is None or not 0 <= etype < ELEVATORS:
            continue
        tile, elevation = built & TILE_MASK, (built >> 29) & 7
        for lv in _table()[etype]:
            if (lv.map, lv.elevation) != (here, elevation):
                out.append(Way("elevator", here, elevation, tile, lv.map, lv.elevation, lv.tile, lv.key, script))
    grids: dict[tuple[int, int, int, int], list[int]] = {}
    for o in m.objects:
        if o.kind == "exit grid":
            d = o.data
            grids.setdefault((o.elevation, d["map"], d["tile"], d["elevation"]), []).append(o.tile)
    for (elevation, to, tile, to_elevation), tiles in grids.items():
        name = _name(knowledge.map_name(to)) if to >= 0 else knowledge.map_name(to)
        out.append(Way("exit", here, elevation, min(tiles), name, to_elevation, tile, tiles=tuple(sorted(tiles))))
    return tuple(out)


def plan(start: tuple[str, int], goal: tuple[str, int] | str, limit: int = 12) -> list[Way] | None:
    """The fewest ways from (map, elevation) START to GOAL: a (map, elevation), a map name (any elevation), or "WORLD
    MAP" (any exit out). None when the map files hold no way within LIMIT ways."""

    def done(node: tuple[str, int]) -> bool:
        if isinstance(goal, tuple):
            return (node[0], node[1]) == (_name(goal[0]), goal[1])
        if goal.upper() in ("WORLD MAP", "TOWN MAP"):
            return node[0] in ("WORLD MAP", "TOWN MAP")
        return node[0] == _name(goal)

    first = (_name(start[0]), start[1])
    came: dict[tuple[str, int], tuple[tuple[str, int], Way] | None] = {first: None}
    queue = deque([(first, 0)])
    while queue:
        node, depth = queue.popleft()
        if done(node):
            path = []
            while came[node] is not None:
                node, way = came[node]
                path.append(way)
            return path[::-1]
        if depth >= limit or node[0] in ("WORLD MAP", "TOWN MAP"):
            continue
        try:
            ways = ways_of(node[0])
        except (KeyError, ValueError):
            continue
        for w in ways:
            if w.elevation != node[1]:
                continue
            nxt = (w.to_map, w.to_elevation if not w.out else 0)
            if nxt not in came:
                came[nxt] = (node, w)
                queue.append((nxt, depth + 1))
    return None


def _dest(index: int) -> str:
    return _name(knowledge.map_name(index)) if index >= 0 else knowledge.map_name(index)


def exit_walls(exits: dict[int, tuple[int, int, int]], to: set[int]) -> frozenset[int]:
    """The exit grids that lead elsewhere than the maps TO (map indices): walls for a walk to TO's grids."""
    return frozenset(t for t, (m, _t, _e) in exits.items() if m not in to)


# --- walking them ----------------------------------------------------------------------------------------------


@dataclass
class Result:
    ok: bool
    why: str
    detail: dict = field(default_factory=dict)


def where(actor) -> dict:
    s = actor.snap()
    return {"map": _name(s.map_name), "elevation": s.elevation, "tile": s.dude.tile if s.dude else None,
            "screen": s.screen}  # fmt: skip


def door_way(actor, goal: int, walls: doors.Walls | None = None, log=None) -> tuple[list[int] | None, list[int]]:
    """The engine's path from the player to GOAL with the closed doors taken as open (the walled ones kept shut),
    and the closed doors on it in order. (None, []) when there is no way even through the doors."""
    from f1 import pathfind, scene, scripts

    walls = walls or doors.WALLS
    snap = scene.snapshot(actor.mem)
    s = actor.snap()
    closed = doors.closed(actor.mem, snap.elevation)
    for tile, d in closed.items():  # a door the player's hand cannot open: walled until it stands open
        if tile not in walls.on(s.map_name, snap.elevation) and doors.hand_shut(
            scripts.script_of(actor.mem, d.address)
        ):
            w = walls.wall(s.map_name, snap.elevation, tile, "its script keeps the player's hand off",
                           doors.door_open(tile, snap.elevation))  # fmt: skip
            (log or actor.log.emit)("milestone", what="a door walled", tile=tile, why=w.why, by=w.by)
    walled = walls.on(s.map_name, snap.elevation)
    shut = frozenset(closed) - walled
    path = pathfind.find(snap, snap.dude, snap.dude.tile, goal, doors_open=shut, avoid=walled)
    if path is None:
        # the engine's search stops at 2000 nodes: from Killian's counter (JUNKKILL 25883) to the exit to Gizmo's
        # casino it finds none even with his front door taken as open (JUNKKILL.MAP, offline), while the bot's own
        # search walks 120 hexes through it; ironman runs stood there three times, the door shut by a long rest
        path = own_door_way(actor, goal, walls)
    if path is None:
        return None, []
    return path, pathfind.doors_on(snap, path, snap.elevation)


def own_door_way(actor, goal: int, walls: doors.Walls) -> list[int] | None:
    """The bot's own search (no node cap) to GOAL with the closed doors taken as open (the walled ones kept shut) and
    the other exit grids kept off; the start left out, as in the engine's path."""
    from f1 import nav

    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address, walls.on(s.map_name, s.elevation))
    keep_off = frozenset(t for t in obs.exits if t != goal)
    path = nav.astar(s.dude.tile, {goal}, obs.passable_doors | keep_off)
    return None if path is None else path[1:]


DOORS_ON_A_WAY = 8
CROWD_WAITS = 8  # tries 2 s apart while no way is found: a critter in the only way moves on


def open_way(actor, goal: int, walls: doors.Walls) -> bool:
    """A way to GOAL with every closed door shut and the other exit grids kept off, by the bot's own search (no node
    cap)."""
    from f1 import nav

    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address, walls.on(s.map_name, s.elevation))
    keep_off = frozenset(t for t in obs.exits if t != goal)
    return nav.astar(s.dude.tile, {goal}, obs.blocked | keep_off) is not None


def open_doors(actor, goal: int, walls: doors.Walls | None = None, log=None) -> Result:
    """Every closed door on the way to GOAL opened in turn (each read back open from memory); a door that stays shut
    is walled (given back once its lock clears or it opens) and the way planned again."""
    from f1 import nav

    walls = walls or doors.WALLS
    opened, walled, asides = [], [], 0
    initial = actor.snap()
    start = (initial.map_name, initial.elevation)
    for _ in range(DOORS_ON_A_WAY):
        s = actor.snap()
        if not alive(s) or s.screen != "map" or (s.map_name, s.elevation) != start or actor.in_combat():
            return Result(False, "not on a safe map", {"opened": opened, "walled": walled})
        for w in walls.give_back(actor.mem, s.map_name):
            (log or actor.log.emit)("milestone", what="a door given back", tile=w.tile, by=w.by)
        path, shut = door_way(actor, goal, walls, log)
        if path is None and asides < 2:
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address, walls.on(s.map_name, s.elevation))
            avoid = set(obs.exits) - {goal}
            mates = nav.mates_in_the_way(actor, s.dude.tile, {goal}, obs.passable_doors | avoid)
            mates = mates or nav.crowd_in_the_way(s.dude.tile, {goal}, obs.passable_doors | avoid, obs.critters)
            if mates:
                actor.log.emit(
                    "navigation_blocked",
                    **nav.blocked_details(actor, s, obs, {goal}, obs.passable_doors | avoid),
                    recovering=True,
                )
                aside = nav.aside_tile(
                    s.dude.tile, mates, obs.blocked | set(obs.exits) | nav.trap_tiles(actor, s.elevation)
                )
                asides += 1
                actor.log.emit("make_way", at=s.dude.tile, mates=sorted(mates), to=aside)
                if aside is not None and actor.walk_to(aside, timeout_s=10).ok:
                    now = actor.snap()
                    if (
                        alive(now)
                        and now.screen == "map"
                        and (now.map_name, now.elevation) == (s.map_name, s.elevation)
                        and now.dude.tile == aside
                    ):
                        actor.companions_settle(4.0)
                        continue
        if path is None and open_way(actor, goal, walls):
            # the engine's search stops at 2000 nodes: from Shady Sands West's east edge to its exit 7106 a walk round
            # the walls is longer (an ironman run stood there, the way refused twice); nav.go_to walks it in legs
            return Result(
                True, "no closed door on a way past the engine's search", {"opened": opened, "walled": walled}
            )
        for _ in range(CROWD_WAITS if path is None else 0):
            # someone stands in the only way (Shady Sands East, an ironman run: in the Scout Handbook's house, a
            # villager by the inner doorway; the step and its retry failed within 1.2 s and the run counted as
            # stuck, while from the same tile another run walked out): they wander on, wait for it
            time.sleep(2.0)
            now = actor.snap()
            if (
                not alive(now)
                or now.screen != "map"
                or (now.map_name, now.elevation) != (s.map_name, s.elevation)
                or actor.in_combat()
            ):
                return Result(False, "passage recovery interrupted", {"opened": opened, "walled": walled})
            path, shut = door_way(actor, goal, walls, log)
            if path is not None or open_way(actor, goal, walls):
                (log or actor.log.emit)("milestone", what="the way cleared", tile=actor.snap().dude.tile)
                break
        if path is None and open_way(actor, goal, walls):
            return Result(
                True, "no closed door on a way past the engine's search", {"opened": opened, "walled": walled}
            )
        if path is None:
            return Result(False, "no way even through the doors", {"opened": opened, "walled": walled})
        if not shut:
            return Result(True, "no closed door on the way", {"opened": opened, "walled": walled, "path": len(path)})
        door = shut[0]
        before = path[path.index(door) - 1] if path.index(door) > 0 else s.dude.tile
        if before != s.dude.tile:
            reached, why = nav.go_to(actor, {before}, max_steps=60, fight=True, avoid=_keep_off(walls, s))
            if not reached:
                return Result(False, f"could not approach door: {why}", {"opened": opened, "walled": walled})
        ok = nav.open_door(actor, door)
        st = doors.at(actor.mem, door, s.elevation)
        row = {"tile": door, "clicked": ok, **(st.to_dict() if st else {"missing": True})}
        if st is not None and st.open and st.passable:
            opened.append(row)
        else:
            walls.wall(s.map_name, s.elevation, door, "did not open")
            walled.append(row)
    return Result(False, "too many doors", {"opened": opened, "walled": walled})


def _keep_off(walls: doors.Walls, s) -> frozenset[int]:
    """The walled doors a walk must be told of: none for the session's walls (nav.go_to blocks those itself, as
    walls: the player's path never crosses a closed door, so they need no short legs round them)."""
    return frozenset() if walls is doors.WALLS else walls.on(s.map_name, s.elevation)


def walk(actor, goal: int, walls: doors.Walls | None = None, avoid: frozenset[int] = frozenset()) -> Result:
    """To GOAL with the doors on the way opened first; the walled doors and AVOID kept off."""
    from f1 import nav

    walls = walls or doors.WALLS
    r = open_doors(actor, goal, walls)
    if not r.ok:
        return r
    s = actor.snap()
    keep_off = _keep_off(walls, s) | avoid
    reached, why = nav.go_to(actor, {goal}, max_steps=120, fight=True, avoid=keep_off)
    return Result(reached, why, {**r.detail, **where(actor)})


def arrived(actor, way: Way) -> bool:
    s = actor.snap()
    if not alive(s) or actor.in_combat():
        return False
    if way.out:
        return s.screen == "worldmap"
    return s.screen == "map" and _name(s.map_name) == way.to_map and s.elevation == way.to_elevation


def take(actor, way: Way, walls: doors.Walls | None = None, timeout_s: float = 25.0) -> Result:
    """Walk WAY: to its tile through the doors, then use it (stairs and ladders by an arrow click on them, an
    elevator by its panel's key, an exit by stepping on its grid). Done when the map and elevation read back are
    the way's destination."""
    from f1 import quests, win32

    walls = walls or doors.WALLS
    actor.log.emit("action_start", action="take_way", way=way.to_dict())
    if not alive(actor.snap()) or actor.snap().screen != "map":
        return _end(actor, False, "not on a living map", way, {})
    if way.kind == "exit":
        s = actor.snap()
        from f1 import nav

        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        # every grid here to the way's destination (the nearest one taken), not only the planned band
        mine = {t for t, (m, _t, e) in obs.exits.items()
                if (m < 0 if way.out else (_dest(m), e) == (way.to_map, way.to_elevation))}  # fmt: skip
        mine = mine or {t for t in way.tiles if t in obs.exits} or set(way.tiles)
        others = frozenset(t for t in obs.exits if t not in mine)
        r = open_doors(actor, min(mine, key=lambda t: geometry.distance(t, s.dude.tile)), walls)
        if not r.ok:
            return _end(actor, False, r.why, way, {"doors": r.detail})
        _reached, why = nav.go_to(actor, mine, max_steps=150, fight=True, avoid=others | _keep_off(walls, s))
        if why.startswith("fight:") or not alive(actor.snap()) or actor.snap().screen not in {"map", "worldmap"}:
            return _end(actor, False, why, way, {"doors": r.detail})
        ok = win32.wait_for(lambda: arrived(actor, way), timeout_s, 0.25)
        time.sleep(1.0)
        return _end(actor, ok, why, way, {"doors": r.detail})
    if way.kind == "elevator":
        r = open_doors(actor, way.tile, walls)
        if not r.ok:
            return _end(actor, False, r.why, way, {"doors": r.detail})
        ok = quests.ride_from(actor, way.tile, way.key) and arrived(actor, way)
        return _end(actor, ok, "rode" if ok else "not there", way, {"doors": r.detail})
    r = open_doors(actor, way.tile, walls)
    if not r.ok:
        return _end(actor, False, r.why, way, {"doors": r.detail})
    from f1 import world

    target = next((t.address for t in world.things(actor.mem)
                   if (t.tile, t.elevation) == (way.tile, way.elevation) and t.pid >> 24 == 2), None)  # fmt: skip
    targets = frozenset({target}) if target else frozenset()
    ok = actor.click_object(way.tile, lambda: arrived(actor, way), timeout_s, aims=quests.SCENERY_AIMS,
                            targets=targets)  # fmt: skip
    actor.set_mouse_mode(0)
    win32.wait_for(lambda: actor.snap().screen == "map", 10, 0.25)
    return _end(actor, ok and arrived(actor, way), "used" if ok else "no effect", way, {"doors": r.detail})


def _end(actor, ok: bool, why: str, way: Way, detail: dict) -> Result:
    seen = where(actor)
    planned = {"map": way.to_map, "elevation": way.to_elevation, "tile": way.to_tile}
    row = {"planned": planned, "seen": seen, **detail}
    actor.log.emit("action_end", action="take_way", ok=ok, reason=why, **row)
    return Result(ok, why, row)


def go(actor, goal: tuple[str, int] | str, walls: doors.Walls | None = None) -> Result:
    """Across the planned ways to GOAL, each checked on arrival; the plan made again from wherever a way ended."""
    done: list[dict] = []
    for _ in range(12):
        s = actor.snap()
        if alive(s) and s.screen == "map" and actor.in_combat():
            fight_through(actor)
            s = actor.snap()
        if not alive(s) or s.screen not in {"map", "worldmap"} or actor.in_combat():
            return Result(False, "not on a safe map", {"ways": done, **where(actor)})
        if s.screen == "worldmap" and isinstance(goal, str) and goal.upper() in ("WORLD MAP", "TOWN MAP"):
            return Result(True, "there", {"ways": done})
        ways = plan((s.map_name, s.elevation), goal)
        if ways is None:
            return Result(False, "no plan", {"ways": done, **where(actor)})
        if not ways:
            return Result(True, "there", {"ways": done})
        r = take(actor, ways[0], walls)
        done.append(r.detail)
        if not r.ok and not (alive(actor.snap()) and actor.in_combat()):
            return Result(False, r.why, {"ways": done})
    return Result(False, "too many ways", {"ways": done})


def fight_through(actor) -> bool:
    """A fight a foe began on the way, weighed and fought as go_to's walk fights it (a refused one fled when the actor
    flees refused fights). An ironman run: a rat in Vault 13's cave began one as the bot walked out, the way ended
    and the step's retry found the fight still on and stopped the run. True once it is over and safe to go on."""
    from f1 import quests

    out = actor.fight(forced=True)
    return out.ok and not actor.in_combat() and quests.prepare_after_fight(actor)
