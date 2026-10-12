"""What a click at a map-view point hits, computed as Fallout 1's engine computes it, and the one point to click an
object at. Ported from fallout1-ce (gmouse.cc object_under_mouse 0x444958, object.cc obj_create_intersect_list
0x47DCC0 and obj_intersects_with 0x47DA30, tile.cc square_roof_intersect 0x49F5B4), each function's code read in the
1.1 exe with capstone: the constants (-320 and -240, rows < 30, columns < 20, the translucent flags 0xFC000 and
TRANS_NONE 0x8000, the scenery proto's extended flags at +0x18) are the exe's.

The engine's walk, for a point (x, y) on the map's elevation:
1. With any type wanted: a visible roof's pixel at the point, where the egg (the see-through circle round the
   player, interface art 2) has none, hides everything: nothing is hit.
2. The candidates: the tiles of a window of 20 x 30 hexes from the tile at (x - 320, y - 240), in ascending tile
   order (the order table sorts its offsets), each tile's objects in its list's order, of this elevation, not the
   egg. An object qualifies with a non-transparent pixel of its current frame at the point (flag 0x01, or 0x02 for a
   translucent one); a wall or scenery the egg shows through at the point also gets 0x04.
3. From the last candidate back: the result is each one passed (the player too when wanted) until one has 0x01 and
   not 0x04 and is no dead or knocked-out critter; so with none such, the result is the first one passed last.

With the high-resolution patch (f1_res) the list is the patch's own (f1/scene.py whole_grid): every tile of the
grid, no window; the rest is the same.

Where 1.1 differs from the port: its window does not check a tile against the grid (a tile off the grid reads past
objectTable); the port skips such tiles. Its roof test reads the roof's frame without a bounds check; the port
answers no pixel outside the frame.

`aim` picks, among the pixels of a target's frame where the walk ends on the target, the one deepest inside that
set, away from the view's edges and the windows over the view: one point, never a search.
"""

import struct
from dataclasses import dataclass
from functools import cache, lru_cache

from f1 import art, knowledge
from f1.geometry import (
    GRID_SIZE,
    Camera,
    in_front_of,
    roof_square_at,
    roof_square_to_screen,
    tile_coord,
    tile_num,
    to_right_of,
)
from f1.scene import HIDDEN, SCENERY, TILE, TRANS_NONE, TRANSLUCENT, WALL, Obj, Snapshot


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int  # inclusive
    bottom: int

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@cache
def extended_flags(pid: int) -> int:
    """A scenery or wall proto's extended flags (+0x18 of its file, as the exe reads proto + 0x18); 0 without one."""
    if (pid >> 24) & 0xF not in (SCENERY, WALL):
        return 0
    entry = knowledge.proto(pid) or {}
    try:
        data = art._files().read(entry["file"])
    except KeyError:
        return 0
    return struct.unpack(">I", data[0x18:0x1C])[0] if len(data) >= 0x1C else 0


@lru_cache(maxsize=8192)
def placed(obj: Obj, cam: Camera) -> tuple[Rect, art.Frame] | None:
    """The object's frame and its rectangle on the view (obj_intersects_with's bounds), None without art."""
    a = art.load(obj.fid)
    if a is None:
        return None
    f = a.frame(obj.frame, obj.rotation)
    if f is None:
        return None
    if obj.tile == -1:
        left, top = obj.sx, obj.sy
    else:
        x, y = tile_coord(obj.tile, cam)
        x += 16 + a.shift_x[obj.rotation] + obj.x
        y += 8 + a.shift_y[obj.rotation] + obj.y
        left, top = x - f.width // 2, y - f.height + 1
    return Rect(left, top, left + f.width - 1, top + f.height - 1), f


def is_egg(obj: Obj, snap: Snapshot) -> bool:
    return snap.egg is not None and obj.address == snap.egg.address


def intersects(obj: Obj, x: int, y: int, snap: Snapshot) -> int:
    """obj_intersects_with: 0, or 0x01 (a pixel), 0x02 (a translucent one), | 0x04 (seen through the egg)."""
    if not is_egg(obj, snap) and obj.flags & HIDDEN:
        return 0
    p = placed(obj, snap.camera)
    if p is None:
        return 0
    rect, frame = p
    if not rect.contains(x, y) or not frame.opaque(x - rect.left, y - rect.top):
        return 0
    if obj.flags & TRANSLUCENT:
        return 0x01 if obj.flags & TRANS_NONE else 0x02
    flags = 0x01
    if obj.kind in (SCENERY, WALL) and snap.dude is not None:
        ext, cam, dude = extended_flags(obj.pid), snap.camera, snap.dude.tile
        if ext & 0x8000000 or ext & 0x80000000:
            egg_rule = in_front_of(obj.tile, dude, cam)
        elif ext & 0x10000000:
            egg_rule = in_front_of(obj.tile, dude, cam) or to_right_of(dude, obj.tile, cam)
        elif ext & 0x20000000:
            egg_rule = in_front_of(obj.tile, dude, cam) and to_right_of(dude, obj.tile, cam)
        else:
            egg_rule = to_right_of(dude, obj.tile, cam)
        if egg_rule and snap.egg is not None and intersects(snap.egg, x, y, snap):
            flags |= 0x04
    return flags


def roof_hides(x: int, y: int, snap: Snapshot) -> bool:
    """square_roof_intersect: a visible roof's pixel at the point."""
    if not snap.roofs_visible or not snap.roof_squares:
        return False
    w = snap.square_width
    sx, sy = roof_square_at(x, y, snap.camera, w)
    index = w * sy + sx
    if not 0 <= index < len(snap.roof_squares) or not 0 <= sx < w:
        return False
    upper = snap.roof_squares[index] >> 16
    if (upper & 0xFFF) == 1 or (upper >> 12) & 1:  # no roof, or this one hidden over the player's building
        return False
    a = art.load((TILE << 24) | (upper & 0xFFF))
    f = a.frame(0, 0) if a else None
    if f is None:
        return False
    rx, ry = roof_square_to_screen(index, snap.camera, w)
    return f.opaque(x - rx, y - ry)


def candidates(x: int, y: int, snap: Snapshot, kind: int = -1, pool: list[Obj] | None = None) -> list[tuple[Obj, int]]:
    """obj_create_intersect_list: (object, flags) in the engine's order. `pool` limits the objects tested; it must be
    in tile order, each tile's list order (the window is the engine's either way)."""
    ox, oy = snap.window_origin
    upper_left = tile_num(x - ox, y - oy, snap.camera, ignore_bounds=True)
    window = snap.window_offsets[snap.center_tile & 1]
    inside = (lambda t: True) if snap.whole_grid else (lambda t: t - upper_left in window)
    if pool is None:
        tiles = sorted(t for t in snap.by_tile if inside(t))
        pool = [o for t in tiles for o in snap.by_tile[t]]
    out = []
    for obj in pool:
        if not 0 <= obj.tile < GRID_SIZE or not inside(obj.tile):
            continue
        if obj.elevation != snap.elevation or (kind != -1 and obj.kind != kind) or is_egg(obj, snap):
            continue
        flags = intersects(obj, x, y, snap)
        if flags:
            out.append((obj, flags))
    return out


def under(x: int, y: int, snap: Snapshot, kind: int = -1, with_dude: bool = True, pool=None) -> Obj | None:
    """object_under_mouse(kind, with_dude): the arrow cursor's object (-1, True); the crosshair's (CRITTER, False)."""
    if kind == -1 and roof_hides(x, y, snap) and not (snap.egg and intersects(snap.egg, x, y, snap)):
        return None
    found = None
    for obj, flags in reversed(candidates(x, y, snap, kind, pool)):
        if not with_dude and snap.dude is not None and obj.address == snap.dude.address:
            continue
        found = obj
        if flags & 0x01 and not flags & 0x04 and not obj.down:
            break
    return found


EDGE = 2  # a point on the screen's edge scrolls the view


def allowed(x: int, y: int, snap: Snapshot) -> bool:
    """A point the map gets: inside the view, off its edges, under no window drawn over it."""
    cam = snap.camera
    if not (EDGE <= x < cam.view_width - EDGE and EDGE <= y < cam.view_height - EDGE):
        return False
    return not any(left <= x < left + w and top <= y < top + h for left, top, w, h in snap.covers)


@dataclass(frozen=True)
class Aim:
    point: tuple[int, int] | None
    pixels: int  # how many of the target's pixels the walk ends on (within the allowed area)
    depth: int  # the point's distance to the set's border (steps of one pixel, 8 ways)
    rect: Rect | None
    reason: str = ""


def aim(target: Obj, snap: Snapshot, kind: int = -1, with_dude: bool = True) -> Aim:
    """The one point to click `target` at: of its pixels where `under` gives the target, the deepest inside."""
    p = placed(target, snap.camera)
    if p is None:
        return Aim(None, 0, 0, None, "no art")
    rect, _ = p
    pool = [o for o in snap.objects() if not is_egg(o, snap) and (q := placed(o, snap.camera)) and _overlap(q[0], rect)]
    pool.sort(key=lambda o: (o.tile, o.order))
    w, h = rect.right - rect.left + 1, rect.bottom - rect.top + 1
    hit = [[False] * w for _ in range(h)]
    count = 0
    for j in range(h):
        y = rect.top + j
        for i in range(w):
            x = rect.left + i
            o = under(x, y, snap, kind, with_dude, pool) if allowed(x, y, snap) else None
            if o is not None and o.address == target.address:
                hit[j][i] = True
                count += 1
    if not count:
        return Aim(None, 0, 0, rect, "no pixel of it is hit (covered, under a roof, off the view)")
    depth = _depth(hit, w, h)
    cells = [(i, j) for j in range(h) for i in range(w) if hit[j][i]]
    cx = sum(i for i, _ in cells) / count
    cy = sum(j for _, j in cells) / count
    best = max(cells, key=lambda ij: (depth[ij[1]][ij[0]], -abs(ij[0] - cx) - abs(ij[1] - cy)))
    return Aim((rect.left + best[0], rect.top + best[1]), count, depth[best[1]][best[0]], rect)


def aim_tile(tile: int, snap: Snapshot) -> tuple[int, int] | None:
    """The point to click a hex at in move mode (the hex cursor goes to tile_num of the point): its centre, when
    the map gets it there."""
    x, y = tile_coord(tile, snap.camera)
    x, y = x + 16, y + 8
    if not allowed(x, y, snap) or tile_num(x, y, snap.camera) != tile:
        return None
    return x, y


def _overlap(a: Rect, b: Rect) -> bool:
    return a.left <= b.right and b.left <= a.right and a.top <= b.bottom and b.top <= a.bottom


def _depth(cells: list[list[bool]], w: int, h: int) -> list[list[int]]:
    """Each set cell's distance (8 ways) to the nearest unset cell or the rectangle's outside."""
    inf = w + h
    d = [[inf if cells[j][i] else 0 for i in range(w)] for j in range(h)]
    for j in range(h):
        for i in range(w):
            if d[j][i]:
                up = min((d[j - 1][k] for k in (i - 1, i, i + 1) if 0 <= k < w), default=0) if j else 0
                left = d[j][i - 1] if i else 0
                d[j][i] = min(d[j][i], up + 1, left + 1)
    for j in range(h - 1, -1, -1):
        for i in range(w - 1, -1, -1):
            if d[j][i]:
                down = min((d[j + 1][k] for k in (i - 1, i, i + 1) if 0 <= k < w), default=0) if j < h - 1 else 0
                right = d[j][i + 1] if i < w - 1 else 0
                d[j][i] = min(d[j][i], down + 1, right + 1)
    return d
