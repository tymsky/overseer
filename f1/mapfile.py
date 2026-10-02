"""Fallout 1's map files (MAPS\\*.MAP) read statically: every object on every elevation with its script, what doors,
stairs, elevators, ladders and exit grids lead to, and what containers and critters carry.

The layout is fallout1-ce's loaders (map.cc map_load_file, scripts.cc scr_load, object.cc obj_load_func, proto.cc
proto_read_protoUpdateData), big-endian: a 236-byte header (version 19, name, entering tile, elevation, rotation,
local and global variable counts, script, flags...), the map's global then local variables, 100 x 100 squares per
elevation present (flags 2, 4, 8 mark elevations 0, 1, 2 absent), the scripts (five lists: a count, then extents of
16 records, each sid, next, 2 more ints for a spatial script or 1 for a timed one, then 14 ints with the SCRIPTS.LST
index second; the extent ends with its length and a link), then the objects: a total, then per elevation a count
and the objects. An object is 18 ints (id, tile, x, y, sx, sy, frame, rotation, fid, flags, elevation, pid, cid,
light distance and intensity, one unused, sid, script index) and its type's data: an inventory head (length,
capacity, a stale pointer); a critter 11 more ints (reaction, damage last turn, maneuver, AP, results, AI packet,
team, who hit it, HP, radiation, poison); anything else a flags int and, by its prototype, a weapon's rounds and
ammunition pid, an ammo box's rounds, a misc item's charges, a key's code, a door's flags, stairs' map and tile, an
elevator's type and level, a ladder's tile, or an exit grid's map, tile, elevation and rotation. The inventory
follows: per entry a count and an object in the same form.

    python -m f1.mapfile NAME [WORD ...]    e.g. MBENT, or GLOWENT exit door: objects whose name, type or script
                                            has one of the words
"""

import struct
import sys
from dataclasses import dataclass, field

from f1 import knowledge
from f1.dat import GameFiles

HEADER_SIZE = 236
ELEVATION_ABSENT = (2, 4, 8)
SQUARES = 100 * 100
SCRIPT_SPATIAL, SCRIPT_TIMED = 1, 2
SCENERY_KINDS = ("door", "stairs", "elevator", "ladder up", "ladder down", "generic")
EXIT_GRIDS = range(0x5000010, 0x5000018)


@dataclass
class MapObject:
    pid: int
    tile: int
    elevation: int
    name: str
    kind: str  # item type, scenery kind, "critter", "wall", "misc", "exit grid"
    script: str = ""
    data: dict = field(default_factory=dict)
    flags: int = 0  # object_types.h: 0x10 OBJECT_NO_BLOCK, 0x800 OBJECT_MULTIHEX ...
    inventory: list[tuple[int, "MapObject"]] = field(default_factory=list)


@dataclass
class MapFile:
    name: str
    entering: tuple[int, int, int]  # tile, elevation, rotation
    script: str
    objects: list[MapObject]
    spatial: list[tuple[str, int, int]]  # script, tile (with elevation in the top bits), radius


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def ints(self, n: int) -> tuple[int, ...]:
        values = struct.unpack(f">{n}i", self.data[self.at : self.at + 4 * n])
        self.at += 4 * n
        return values

    def int(self) -> int:
        return self.ints(1)[0]


@dataclass(frozen=True)
class Protos:
    item_types: dict[int, str]
    scenery_kinds: dict[int, str]


def _protos(files: GameFiles) -> Protos:
    items, scenery = {}, {}
    for key, e in knowledge.load()["protos"].items():
        pid = int(key, 16)
        if e["type"] == "item":
            items[pid] = e.get("item_type", "")
        elif e["type"] == "scenery":
            raw = files.read(e["file"])
            scenery[pid] = SCENERY_KINDS[struct.unpack(">i", raw[32:36])[0]] if len(raw) >= 36 else "generic"
    return Protos(items, scenery)


def _script_name(index: int) -> str:
    names = knowledge.load()["scripts"]
    return names[index] if 0 <= index < len(names) else ""


def _object(r: Reader, protos: Protos, sids: dict[int, int]) -> MapObject:
    head = r.ints(18)
    tile, elevation, pid, sid = head[1], head[10], head[11] & 0xFFFFFFFF, head[16]
    inv_length = r.ints(3)[0]
    kind, data = {1: "critter", 3: "wall", 4: "tile", 5: "misc"}.get(pid >> 24, ""), {}
    if pid >> 24 == 1:
        values = r.ints(11)
        data = {"team": values[6], "hp": values[8], "ai": values[5]}
    else:
        item_flags = r.int()  # a container's lock is its 0x02000000 (object_types.h CONTAINER_FLAG_LOCKED)
        if pid >> 24 == 0:
            kind = protos.item_types.get(pid, "item")
            if kind == "container":
                data = {"flags": item_flags & 0xFFFFFFFF}
            if kind == "weapon":
                data = dict(zip(("rounds", "ammo_pid"), r.ints(2), strict=True))
            elif kind in ("ammo", "misc"):
                data = {"rounds" if kind == "ammo" else "charges": r.int()}
            elif kind == "key":
                data = {"key": r.int()}
        elif pid >> 24 == 2:
            kind = protos.scenery_kinds.get(pid, "generic")
            if kind == "door":
                data = {"open_flags": r.int()}
            elif kind == "stairs":
                data = dict(zip(("map", "tile"), r.ints(2), strict=True))
            elif kind == "elevator":
                data = dict(zip(("elevator", "level"), r.ints(2), strict=True))
            elif kind.startswith("ladder"):
                data = {"tile": r.int()}
        elif pid in EXIT_GRIDS:
            kind = "exit grid"
            data = dict(zip(("map", "tile", "elevation", "rotation"), r.ints(4), strict=True))
    script = _script_name(sids[sid]) if sid != -1 and sid in sids else ""
    name = knowledge.proto_name(pid)
    obj = MapObject(pid, tile, elevation, name, kind, script, data, head[9] & 0xFFFFFFFF)
    for _ in range(inv_length):
        count = r.int()
        obj.inventory.append((count, _object(r, protos, sids)))
    return obj


def read(name: str, files: GameFiles | None = None, protos: Protos | None = None) -> MapFile:
    files = files or GameFiles(knowledge.INSTANCE)
    protos = protos or _protos(files)
    r = Reader(files.read(f"MAPS/{name.upper().removesuffix('.MAP')}.MAP"))
    version = r.int()
    if version != 19:
        raise ValueError(f"map version {version}, not 19")
    title = r.data[4:20].split(b"\0", 1)[0].decode("latin1")
    r.at = 20
    tile, elevation, rotation, local_count, script_index, flags, _dark, global_count = r.ints(8)
    r.at = HEADER_SIZE
    r.ints(global_count)
    r.ints(local_count)
    for e in range(3):
        if not flags & ELEVATION_ABSENT[e]:
            r.at += 4 * SQUARES
    sids: dict[int, int] = {}
    spatial = []
    for _kind in range(5):
        count = r.int()
        for _extent in range((count + 15) // 16):
            for _ in range(16):
                sid, _next = r.ints(2)
                extra = r.ints(2) if sid >> 24 == SCRIPT_SPATIAL else r.ints(1) if sid >> 24 == SCRIPT_TIMED else ()
                rest = r.ints(14)
                sids[sid] = rest[1]
                if sid >> 24 == SCRIPT_SPATIAL and sid != -1:
                    spatial.append((_script_name(rest[1]), extra[0], extra[1]))
            r.ints(2)  # the extent's length and link
    r.int()  # all objects
    objects = []
    for _e in range(3):
        for _ in range(r.int()):
            objects.append(_object(r, protos, sids))
    # the header's script number counts from 1 (MBENT names 443, MBEnt.int is line 442)
    return MapFile(title, (tile, elevation, rotation), _script_name(script_index - 1), objects, spatial)


def describe(o: MapObject) -> str:
    extra = ""
    if o.kind == "exit grid" or o.kind == "stairs":
        dest = knowledge.map_name(o.data.get("map", -3))
        extra = f" -> {dest} tile {o.data.get('tile')} elevation {o.data.get('elevation', '')}".rstrip()
    elif o.kind == "elevator":
        extra = f" elevator {o.data['elevator']} level {o.data['level']}"
    elif o.kind.startswith("ladder"):
        extra = f" -> tile {o.data['tile'] & 0xFFFF} (elevation {o.data['tile'] >> 29})"
    elif o.data:
        extra = " " + " ".join(f"{k} {v}" for k, v in o.data.items())
    goods = ", ".join(f"{c} x {i.name}" for c, i in o.inventory)
    return (
        f"e{o.elevation} tile {o.tile:>5} {o.kind:<10} {o.name:<28}"
        + (f" [{o.script}]" if o.script else "")
        + extra
        + (f"  carries: {goods}" if goods else "")
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    m = read(argv[0])
    words = [w.lower() for w in argv[1:]]
    print(f"{m.name}: entering tile {m.entering[0]} elevation {m.entering[1]}, map script {m.script or '-'}")
    for script, tile, radius in m.spatial:
        if not words or any(w in script for w in words):
            print(f"  spatial [{script}] tile {tile & 0xFFFF} elevation {tile >> 29} radius {radius}")
    grids: dict[tuple, list[int]] = {}
    for o in sorted(m.objects, key=lambda o: (o.elevation, o.tile)):
        if o.kind == "exit grid":  # a grid is a row of hexes: one line per destination
            dest = (o.elevation, o.data["map"], o.data["tile"], o.data["elevation"])
            grids.setdefault(dest, []).append(o.tile)
            continue
        if o.kind in ("wall", "tile") or (o.kind in ("generic", "misc") and not o.script and not words):
            continue  # walls, floor tiles, blockers and plain scenery: asked for by a word only
        text = describe(o)
        if not words or any(w in text.lower() for w in words):
            print("  " + text)
    for (elevation, dest, tile, dest_elevation), tiles in grids.items():
        text = (
            f"e{elevation} exit grid x{len(tiles)} (tiles {min(tiles)}..{max(tiles)}) -> {knowledge.map_name(dest)}"
            f" tile {tile} elevation {dest_elevation}"
        )
        if not words or any(w in text.lower() for w in words):
            print("  " + text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
