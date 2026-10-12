"""The map as the hit test (f1/hit.py) and the line of fire (f1/lof.py) see it, read from memory at one moment: the
camera (the hexes' and the roof squares'), the objects tile by tile in the engine's list order, the player and the
egg, the engine's own tables of the order it tests tiles in, the roofs, and the windows over the map view.

The view must stand still while it is read and until the click it aims: the camera is read before and after the
snapshot, at least STILL_S apart (longer than two of the engine's timer ticks; a view that scrolls, by Home, a screen
edge or a camera that follows the player, moves within it), and the snapshot is taken again until both agree.

Nothing here assumes the view's size or origin: the camera globals are what the running game's tile_set_center left
(the High Resolution Patch's version shifts them at a map's edges), the view's size is buf_width x buf_length."""

import struct
import time
from dataclasses import dataclass, field

from f1 import engine_map as em
from f1.engine_map import Obj as O
from f1.geometry import GRID_SIZE, Camera
from f1.memory import GameMemory, ReadError

ITEM, CRITTER, SCENERY, WALL, TILE, MISC = range(6)  # fid bits 24-27
HIDDEN, FLAT, NO_BLOCK, MULTIHEX = 0x01, 0x08, 0x10, 0x800
TRANS_NONE, TRANSLUCENT = 0x8000, 0xFC000  # object_types.h OBJECT_TRANS_NONE, OBJECT_FLAG_0xFC000
LIGHT_THRU, SHOOT_THRU = 0x20000000, 0x80000000
KNOCKED_OUT, KNOCKED_DOWN, DEAD = 0x01, 0x02, 0x80  # a critter's combat results
WINDOW_HIDDEN = 0x08


@dataclass(frozen=True)
class Obj:
    address: int
    id: int
    tile: int
    x: int
    y: int
    sx: int
    sy: int
    frame: int
    rotation: int
    fid: int
    flags: int
    elevation: int
    results: int  # a critter's combat results (another field for other kinds: read it only for critters)
    pid: int
    order: int = 0  # place in its tile's list

    @property
    def kind(self) -> int:
        return (self.fid >> 24) & 0xF

    @property
    def animation(self) -> int:
        return (self.fid >> 16) & 0xFF

    @property
    def down(self) -> bool:
        """A critter knocked out or dead (the hit test passes over it)."""
        return self.kind == CRITTER and bool(self.results & (KNOCKED_OUT | DEAD))


def parse_object(address: int, raw: bytes, order: int = 0) -> Obj:
    i = lambda off: struct.unpack_from("<i", raw, off)[0]
    return Obj(address, i(O.ID), i(O.TILE), i(O.X), i(O.Y), i(O.SX), i(O.SY), i(O.FRAME), i(O.ROTATION),
               i(O.FID) & 0xFFFFFFFF, i(O.FLAGS) & 0xFFFFFFFF, i(O.ELEVATION), i(O.CRITTER_RESULTS),
               i(O.PID) & 0xFFFFFFFF, order)  # fmt: skip


@dataclass
class Snapshot:
    camera: Camera
    elevation: int
    center_tile: int
    dude: Obj | None
    egg: Obj | None
    by_tile: dict[int, list[Obj]]  # every elevation, in list order
    update_width: int
    window_offsets: tuple[frozenset[int], frozenset[int]]  # by the centre's parity: tile offsets the hit test visits
    roofs_visible: bool = False
    roof_squares: tuple[int, ...] = ()  # the elevation's 10000 squares (roof in the high 16 bits)
    square_width: int = 100
    covers: tuple[tuple[int, int, int, int], ...] = ()  # visible windows above the map view: (left, top, w, h)
    notes: list[str] = field(default_factory=list)
    window_origin: tuple[int, int] = (320, 240)  # the window's upper-left tile is at (x - this[0], y - this[1])
    whole_grid: bool = False  # the hit test visits every tile (f1_res's replacement of the list), no window
    _objects: dict = field(default_factory=dict, repr=False)

    def objects(self, elevation: int | None = None) -> list[Obj]:
        """The elevation's objects in tile order, each tile's list order."""
        e = self.elevation if elevation is None else elevation
        if e not in self._objects:
            self._objects[e] = [o for t in sorted(self.by_tile) for o in self.by_tile[t] if o.elevation == e]
        return self._objects[e]

    def find(self, address: int) -> Obj | None:
        return next((o for objs in self.by_tile.values() for o in objs if o.address == address), None)


def read_object(mem: GameMemory, address: int, order: int = 0) -> Obj:
    return parse_object(address, mem.read(address, O.SIZE), order)


def window_offsets(order: list[int], offsets: list[int], div: list[int], mod: list[int], rows: int = 30,
                   columns: int = 20) -> frozenset[int]:  # fmt: skip
    """The tile offsets (from the upper-left tile) obj_create_intersect_list visits: rows < 30, columns < 20."""
    return frozenset(offsets[i] for i in order if div[i] < rows and mod[i] < columns)


# obj_create_intersect_list's window constants, as instructions of the exe: (address, the bytes before the
# immediate, the immediate's size). Read from the running game's code: a patch over the exe may change them.
WINDOW_CODE = {
    "x": (0x47DCE4, bytes.fromhex("2d"), 4),  # sub eax, 320
    "y": (0x47DCE9, bytes.fromhex("81ea"), 4),  # sub edx, 240
    "rows": (0x47DD40, bytes.fromhex("833c03"), 1),  # cmp dword [ebx + eax], 30
    "columns": (0x47DD50, bytes.fromhex("833c03"), 1),  # cmp dword [ebx + eax], 20
}
WINDOW_DEFAULTS = {"x": 320, "y": 240, "rows": 30, "columns": 20}


# object_under_mouse's call of obj_create_intersect_list. The high-resolution patch (f1_res) redirects it to its own
# list: every tile of the grid in ascending order, each tile's objects in list order (stopping at a higher
# elevation), of the type wanted, not the egg, each tested with the exe's obj_intersects_with; with its fog of war
# off its filter passes everything (static, the dll read with capstone; measured live: 16 of 16 points where the
# window disagreed with the game).
INTERSECT_CALL = 0x4449C6
INTERSECT_LIST = 0x47DCC0


def whole_grid(mem: GameMemory) -> bool:
    """object_under_mouse calls another list than the exe's (the call's target read from the running code)."""
    raw = mem.read(INTERSECT_CALL, 5)
    if raw[0] != 0xE8:
        return True
    return INTERSECT_CALL + 5 + int.from_bytes(raw[1:], "little", signed=True) != INTERSECT_LIST


def window_constants(mem: GameMemory) -> tuple[dict[str, int], list[str]]:
    """The window's constants as the running code holds them (the exe's where an instruction is not the expected
    one: noted)."""
    out, notes = dict(WINDOW_DEFAULTS), []
    for name, (address, head, size) in WINDOW_CODE.items():
        raw = mem.read(address, len(head) + size)
        if raw[: len(head)] != head:
            notes.append(f"obj_create_intersect_list: the code at {address:#x} is not the exe's ({raw.hex()})")
            continue
        out[name] = int.from_bytes(raw[len(head) :], "little", signed=True)
    return out, notes


def check_tables(order: list[int], offsets: list[int], width: int, div: list[int], mod: list[int]) -> list[str]:
    """What is wrong with the engine's tables as read (empty when they are what object.cc builds)."""
    problems = []
    n = len(order)
    if sorted(order) != list(range(n)):
        problems.append("the order table is not a permutation")
    elif any(offsets[order[i]] > offsets[order[i + 1]] for i in range(n - 1)):
        problems.append("the order table is not sorted by the offsets")
    if div != [i // width for i in range(n)] or mod != [i % width for i in range(n)]:
        problems.append("the div/mod tables are not index / width, index % width")
    return problems


STILL_S = 0.035
STILL_TIMEOUT_S = 3.0
READ_AGAIN_S = 0.2


def read_camera(mem: GameMemory) -> Camera:
    g = mem.glob
    return Camera(g("tile_offx"), g("tile_offy"), g("tile_x"), g("tile_y"), g("buf_width"), g("buf_length"),
                  g("square_offx"), g("square_offy"), g("square_x"), g("square_y"))  # fmt: skip


def snapshot(mem: GameMemory, still_s: float = STILL_S, timeout: float = STILL_TIMEOUT_S) -> Snapshot:
    """The map with a camera that stood still while it was read (after TIMEOUT: the last reading, a note says the
    view moved). A read that fails while the map's lists change under it is read again until TIMEOUT."""
    end = time.monotonic() + timeout
    while True:
        start, cam = time.monotonic(), read_camera(mem)
        try:
            snap = _snapshot(mem, cam)
        except ReadError:
            if time.monotonic() > end:
                raise
            time.sleep(READ_AGAIN_S)
            continue
        rest = still_s - (time.monotonic() - start)
        if rest > 0:
            time.sleep(rest)
        if read_camera(mem) == cam:
            return snap
        if time.monotonic() > end:
            snap.notes.append("the view moved while it was read")
            return snap


def _ints(mem: GameMemory, address: int, n: int) -> list[int]:
    return list(struct.unpack(f"<{n}i", mem.read(address, 4 * n))) if n > 0 else []


_TABLES: dict[tuple, tuple] = {}  # the order tables, read once per process and size (obj_init builds them once)


def _windows(mem: GameMemory) -> tuple[int, tuple[frozenset[int], frozenset[int]], list[str], tuple[int, int]]:
    g = mem.glob
    width, size = g("updateHexWidth"), g("updateHexArea")
    order_ptrs = struct.unpack("<II", mem.read(em.GLOBALS["orderTable"].address, 8))
    offset_ptrs = struct.unpack("<II", mem.read(em.GLOBALS["offsetTable"].address, 8))
    consts, const_notes = window_constants(mem)
    key = (getattr(mem, "pid", 0), width, size, order_ptrs, offset_ptrs, tuple(consts.values()))
    if key not in _TABLES:
        div = _ints(mem, g("offsetDivTable"), size)
        mod = _ints(mem, g("offsetModTable"), size)
        notes, windows = [], []
        for parity in (0, 1):
            order = _ints(mem, order_ptrs[parity], size)
            offsets = _ints(mem, offset_ptrs[parity], size)
            notes += [f"parity {parity}: {p}" for p in check_tables(order, offsets, width, div, mod)]
            windows.append(window_offsets(order, offsets, div, mod, consts["rows"], consts["columns"]))
        _TABLES[key] = (width, (windows[0], windows[1]), notes + const_notes, (consts["x"], consts["y"]))
    width, windows, notes, origin = _TABLES[key]
    return width, windows, list(notes), origin


def covers(mem: GameMemory) -> tuple[tuple[int, int, int, int], ...]:
    """The visible GNW windows drawn above the map view (the interface bar, the indicator boxes, a message box): a
    point there goes to them, not to the map."""
    from f1 import state

    display = mem.glob("display_win")
    wins = state.live_windows(mem)
    ids = [w[0] for w in wins]
    if display not in ids:
        return ()
    above = wins[ids.index(display) + 1 :]
    return tuple((x, y, w, h) for _wid, flags, x, y, w, h in above if not flags & WINDOW_HIDDEN)


def _snapshot(mem: GameMemory, cam: Camera) -> Snapshot:
    g = mem.glob
    elevation = g("map_elevation")
    heads = struct.unpack(f"<{GRID_SIZE}I", mem.read(em.GLOBALS["objectTable"].address, 4 * GRID_SIZE))
    by_tile: dict[int, list[Obj]] = {}
    for tile, node in enumerate(heads):
        n = 0
        while node and n < 1000:
            obj_addr, nxt = struct.unpack("<II", mem.read(node, 8))
            by_tile.setdefault(tile, []).append(read_object(mem, obj_addr, n))
            node, n = nxt, n + 1
    dude = read_object(mem, g("obj_dude")) if g("obj_dude") else None
    egg = read_object(mem, g("obj_egg")) if g("obj_egg") else None
    width, windows, notes, origin = _windows(mem)
    square_width, square_length = g("square_width"), g("square_length")
    squares: tuple[int, ...] = ()
    roofs = bool(mem.u8(em.GLOBALS["show_roof"].address))
    if g("squares") and square_width * square_length == 10000 and 0 <= elevation < 3:
        per_elevation = mem.u32(g("squares") + 4 * elevation)
        squares = struct.unpack("<10000I", mem.read(per_elevation, 40000)) if per_elevation else ()
    return Snapshot(cam, elevation, g("tile_center_tile"), dude, egg, by_tile, width, windows, roofs, squares,
                    square_width or 100, covers(mem), notes, origin, whole_grid(mem))  # fmt: skip


def to_json(snap: Snapshot) -> dict:
    """The snapshot as plain data (a misclick's record, an offline test's input)."""
    import dataclasses

    return {
        "camera": dataclasses.asdict(snap.camera),
        "elevation": snap.elevation,
        "center_tile": snap.center_tile,
        "dude": dataclasses.asdict(snap.dude) if snap.dude else None,
        "egg": dataclasses.asdict(snap.egg) if snap.egg else None,
        "objects": [dataclasses.asdict(o) for t in sorted(snap.by_tile) for o in snap.by_tile[t]],
        "update_width": snap.update_width,
        "window_offsets": [sorted(w) for w in snap.window_offsets],
        "roofs_visible": snap.roofs_visible,
        "roof_squares": list(snap.roof_squares),
        "square_width": snap.square_width,
        "covers": [list(c) for c in snap.covers],
        "notes": snap.notes,
        "window_origin": list(snap.window_origin),
        "whole_grid": snap.whole_grid,
    }


def from_json(d: dict) -> Snapshot:
    by_tile: dict[int, list[Obj]] = {}
    for o in d["objects"]:
        by_tile.setdefault(o["tile"], []).append(Obj(**o))
    return Snapshot(
        Camera(**d["camera"]), d["elevation"], d["center_tile"], Obj(**d["dude"]) if d["dude"] else None,
        Obj(**d["egg"]) if d["egg"] else None, by_tile, d["update_width"],
        (frozenset(d["window_offsets"][0]), frozenset(d["window_offsets"][1])), d["roofs_visible"],
        tuple(d["roof_squares"]), d["square_width"], tuple(tuple(c) for c in d.get("covers", [])), d["notes"],
        tuple(d.get("window_origin", (320, 240))), d.get("whole_grid", False),
    )  # fmt: skip
