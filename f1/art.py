"""The game's pictures (ART\\<TYPE>\\*.FRM) placed where the engine draws them, to aim a click where an object shows.

A WATRSHD ladder (tile 23858) shares its hex with a Cave Wall whose picture covers all of it but a strip along its
right rail, 16..20 px right of the hex centre (read from the two FRMs): a grid of aims 8 px apart never
landed there, and a use of the ladder took 61 s. Here each object's picture is put where CE's obj_bound puts it: its
bottom centre at the hex centre plus the art's shift for the rotation and the object's own pixel offset (obj->x, y);
a picture's pixel shows where its colour index is not 0 (transparent). The points to aim at are the target's pixels
that no other nearby picture covers, those with visible neighbours first (a pixel off still lands on it).

FRM (big-endian): version, fps, action frame, frames per direction (u16), x shifts and y shifts per direction (6 x
i16 each), offsets of each direction's frames (6 x u32), the frames' size; frames from 0x3E: width, height (u16),
size (u32), x and y offset (i16), then width x height colour indexes.
"""

import struct
from dataclasses import dataclass
from functools import cache

from f1 import knowledge
from f1.dat import GameFiles

TYPES = {0: "ITEMS", 1: "CRITTERS", 2: "SCENERY", 3: "WALLS", 4: "TILES", 5: "MISC", 6: "INTRFACE", 7: "INVEN"}
FRAMES_AT = 0x3E


@dataclass(frozen=True)
class Picture:
    width: int
    height: int
    shift_x: int  # the art's shift for the direction (FRM header)
    shift_y: int
    pixels: bytes  # width x height colour indexes, 0 transparent

    def opaque(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and self.pixels[y * self.width + x] != 0


@cache
def _files() -> GameFiles:
    return GameFiles(knowledge.INSTANCE)


@cache
def _listing(kind: str) -> tuple[str, ...]:
    return tuple(_files().read(f"ART/{kind}/{kind}.LST").decode("latin1").split())


@cache
def picture(fid: int, rotation: int = 0) -> Picture | None:
    """Frame 0 of the art `fid` in direction `rotation` (single-direction art has only 0); None for art this does
    not place (critters: their name also holds the animation)."""
    kind = TYPES.get((fid >> 24) & 0xF)
    if kind is None or kind == "CRITTERS":
        return None
    names = _listing(kind)
    index = fid & 0xFFF
    if index >= len(names):
        return None
    try:
        data = _files().read(f"ART/{kind}/{names[index]}")
    except KeyError:
        return None
    shifts_x, shifts_y = struct.unpack(">6h", data[10:22]), struct.unpack(">6h", data[22:34])
    offsets = struct.unpack(">6I", data[34:58])
    direction = rotation if 0 <= rotation < 6 and (rotation == 0 or offsets[rotation] != offsets[0]) else 0
    at = FRAMES_AT + offsets[direction]
    width, height = struct.unpack(">HH", data[at : at + 4])
    pixels = data[at + 12 : at + 12 + width * height]
    return Picture(width, height, shifts_x[direction], shifts_y[direction], pixels)


def corner(p: Picture, obj_x: int = 0, obj_y: int = 0) -> tuple[int, int]:
    """The picture's top-left pixel relative to its hex centre (obj_bound)."""
    return p.shift_x + obj_x - p.width // 2, p.shift_y + obj_y - p.height + 1


@dataclass(frozen=True)
class Placed:
    """A picture on the screen relative to some origin (the target's hex centre)."""

    picture: Picture
    x0: int
    y0: int

    def opaque(self, x: int, y: int) -> bool:
        return self.picture.opaque(x - self.x0, y - self.y0)


def visible_points(target: Placed, others: list[Placed], step: int = 2, limit: int = 12) -> list[tuple[int, int]]:
    """Points (relative to the origin) where the target's picture shows and no other covers it, best first: those
    whose neighbours 2 px away show too, nearest the visible part's middle; at most `limit`."""
    p = target.picture

    def shows(x: int, y: int) -> bool:
        return target.opaque(x, y) and not any(o.opaque(x, y) for o in others)

    seen = [
        (x, y)
        for y in range(target.y0, target.y0 + p.height, step)
        for x in range(target.x0, target.x0 + p.width, step)
        if shows(x, y)
    ]
    if not seen:
        return []
    mx = sorted(x for x, _ in seen)[len(seen) // 2]
    my = sorted(y for _, y in seen)[len(seen) // 2]

    def solid(pt: tuple[int, int]) -> int:
        x, y = pt
        return sum(shows(x + dx, y + dy) for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)))

    seen.sort(key=lambda pt: (-solid(pt), abs(pt[0] - mx) + abs(pt[1] - my)))
    out: list[tuple[int, int]] = []
    for pt in seen:  # spread out: a point every 6 px at least
        if all(abs(pt[0] - q[0]) + abs(pt[1] - q[1]) >= 6 for q in out):
            out.append(pt)
        if len(out) == limit:
            break
    return out
