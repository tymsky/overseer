"""The light every lit object spreads over the tiles around it (fallout1-ce object.cc obj_adjust_light 0x47F30C,
ported; its blocking cases generated from the source's switch, not written by hand), so that a map's light can be
computed from its objects and the player's own light moved in a plan.

The engine: light_reset_tiles sets every tile to 655 (f1.light.BASE); obj_rebuild_all_light then adds, for every
object on the map in objectTable's order, its light: lit (flag 0x20), not hidden, intensity above 0 (clamped to
65536, the distance to 8). The source's own tile gets the full intensity; then ring by ring (36 tiles in each of the
six directions' wedges, `light_distance` (0x505C54) hexes out, those within the light's distance), a tile gets the
intensity less (intensity - 655) / (distance + 1) a hex, unless the tiles nearer the source along its wedge block it
(the cases below, over `blocked`), or a wall's facing turns it away. A tile blocks when an object on it (hidden ones
skipped, up to the source's elevation, in the tile's list order) lacks the light-thru flag (0x20000000).
`light_blocked` (0x637730) is a static array in the engine: the cases read only entries set earlier in the same call.
An object that moves takes its light off (the same walk, subtracting) and puts it back at its new tile; the other
lights stay as they were spread, so a critter that walked into a light's wedge after it was spread does not darken
the tiles behind it until that light is spread again (a map's load, an elevation's change).

The player's light: 4 hexes of 65536 (object.cc obj_init, map.cc). The offsets (`light_offsets` 0x637A90) are made
from tile_center_tile when the objects are initialised; from any tile away from the grid's edge they are the same
for the same parity.
"""

import struct
from dataclasses import dataclass
from functools import cache

from f1 import engine_map as em
from f1 import geometry, light
from f1.engine_map import Obj

DISTANCE = (1, 2, 3, 4, 5, 6, 7, 8, 2, 3, 4, 5, 6, 7, 8, 3, 4, 5, 6, 7, 8, 4, 5, 6, 7, 8, 5, 6, 7, 8, 6, 7, 8, 7, 8, 8)
HIDDEN, FLAT, LIGHTING, LIGHT_THRU = 0x01, 0x08, 0x20, 0x20000000
WALL = 3  # an object's fid type
NE, E, SE, SW, W, NW = range(6)
DUDE_DISTANCE, DUDE_INTENSITY = 4, 0x10000
CENTER = 20100  # a tile away from the grid's edge: the offsets depend only on the tile's parity
LIGHT_OFFSETS = 0x637A90  # int[2][6][36], read back live against OFFSETS


def _offsets(parity: int) -> list[list[int]]:
    """obj_light_table_init: for each direction the 36 tiles of its wedge, as differences from the source."""
    v4 = CENTER + parity
    out = []
    for i in range(6):
        row, v15 = [], 8
        for j in range(8):
            tile = geometry.in_direction(v4, (i + 1) % 6, j)
            for m in range(v15):
                row.append(geometry.in_direction(tile, i, m + 1) - v4)
            v15 -= 1
        out.append(row)
    return out


OFFSETS = (_offsets(0), _offsets(1))


def _blocked(index: int, b: list[list[int]], r: int, n: int) -> int:
    """object.cc obj_adjust_light's switch (generated from it): whether the tiles nearer the source cut the light
    off from this one."""
    v14 = eax = ebx = edx = esi = edi = 0
    if index == 0:
        v14 = 0
    elif index == 1:
        v14 = b[r][0]
    elif index == 2:
        v14 = b[r][1]
    elif index == 3:
        v14 = b[r][2]
    elif index == 4:
        v14 = b[r][3]
    elif index == 5:
        v14 = b[r][4]
    elif index == 6:
        v14 = b[r][5]
    elif index == 7:
        v14 = b[r][6]
    elif index == 8:
        v14 = b[r][0] & b[n][0]
    elif index == 9:
        v14 = b[r][1] & b[r][8]
    elif index == 10:
        v14 = b[r][2] & b[r][9]
    elif index == 11:
        v14 = b[r][3] & b[r][10]
    elif index == 12:
        v14 = b[r][4] & b[r][11]
    elif index == 13:
        v14 = b[r][5] & b[r][12]
    elif index == 14:
        v14 = b[r][6] & b[r][13]
    elif index == 15:
        v14 = b[r][8] & b[n][1]
    elif index == 16:
        v14 = b[r][8] | (b[r][9] & b[r][15])
    elif index == 17:
        edx = b[r][9]
        edx |= b[r][10]
        ebx = b[r][8]
        esi = b[r][16]
        ebx &= edx
        edx &= esi
        edi = b[r][15]
        ebx |= edx
        edx = b[r][10]
        eax = b[r][9]
        edx |= edi
        eax &= edx
        v14 = ebx | eax
    elif index == 18:
        edx = b[r][0]
        ebx = b[r][9]
        esi = b[r][10]
        edx |= ebx
        edi = b[r][11]
        edx |= esi
        ebx = b[r][17]
        edx |= edi
        ebx &= edx
        edx = esi
        esi = b[r][16]
        edi = b[r][9]
        edx &= esi
        edx |= edi
        edx |= ebx
        v14 = edx
    elif index == 19:
        edx = b[r][17]
        edi = b[r][18]
        ebx = b[r][11]
        edx |= edi
        esi = b[r][10]
        ebx &= edx
        edx = b[r][9]
        edx |= esi
        ebx |= edx
        edx = b[r][12]
        edx &= edi
        ebx |= edx
        v14 = ebx
    elif index == 20:
        edx = b[r][2]
        esi = b[r][11]
        edi = b[r][12]
        ebx = b[r][8]
        edx |= esi
        esi = b[r][9]
        edx |= edi
        edi = b[r][10]
        ebx &= edx
        edx &= esi
        esi = b[r][17]
        ebx |= edx
        edx = b[r][16]
        ebx |= edi
        edi = b[r][18]
        edx |= esi
        esi = b[r][19]
        edx |= edi
        eax = b[r][11]
        edx |= esi
        eax &= edx
        ebx |= eax
        v14 = ebx
    elif index == 21:
        v14 = (b[r][8] & b[n][1]) | (b[r][15] & b[n][2])
    elif index == 22:
        edx = b[n][1]
        ebx = b[r][15]
        esi = b[r][21]
        edx |= ebx
        ebx = b[r][8]
        edx |= esi
        ebx &= edx
        edx = b[r][9]
        edi = esi
        edx |= esi
        esi = b[r][15]
        edx &= esi
        ebx |= edx
        edx = esi
        esi = b[r][16]
        edx |= edi
        edx &= esi
        ebx |= edx
        v14 = ebx
    elif index == 23:
        edx = b[r][3]
        ebx = b[r][16]
        esi = b[r][15]
        ebx |= edx
        edx = b[r][9]
        edx &= esi
        edi = b[r][22]
        ebx |= edx
        edx = b[r][17]
        edx &= edi
        ebx |= edx
        v14 = ebx
    elif index == 24:
        edx = b[r][0]
        edi = b[r][9]
        ebx = b[r][10]
        edx |= edi
        esi = b[r][17]
        edx |= ebx
        edi = b[r][18]
        edx |= esi
        ebx = b[r][16]
        edx |= edi
        esi = b[r][16]
        ebx &= edx
        edx = b[r][15]
        edi = b[r][23]
        edx |= esi
        esi = b[r][9]
        edx |= edi
        edi = b[r][8]
        edx &= esi
        edx |= edi
        esi = b[r][22]
        ebx |= edx
        edx = b[r][15]
        edi = b[r][23]
        edx |= esi
        esi = b[r][17]
        edx |= edi
        edx &= esi
        ebx |= edx
        edx = b[r][18]
        edx &= edi
        ebx |= edx
        v14 = ebx
    elif index == 25:
        edx = b[r][8]
        edi = b[r][15]
        ebx = b[r][16]
        edx |= edi
        esi = b[r][23]
        edx |= ebx
        edi = b[r][24]
        edx |= esi
        ebx = b[r][9]
        edx |= edi
        esi = b[r][1]
        ebx &= edx
        edx = b[r][8]
        edx &= esi
        edi = b[r][16]
        ebx |= edx
        edx = b[r][8]
        esi = b[r][17]
        edx |= edi
        edi = b[r][24]
        esi |= edx
        esi |= edi
        esi &= b[r][10]
        edi = b[r][23]
        ebx |= esi
        esi = b[r][17]
        edx |= edi
        ebx |= esi
        esi = b[r][24]
        edi = b[r][18]
        edx |= esi
        edx &= edi
        esi = b[r][19]
        ebx |= edx
        edx = b[r][0]
        eax = b[r][24]
        edx |= esi
        eax &= edx
        ebx |= eax
        v14 = ebx
    elif index == 26:
        ebx = b[r][8]
        esi = b[n][1]
        edi = b[n][2]
        esi &= ebx
        ebx = b[r][15]
        ebx &= edi
        eax = b[r][21]
        ebx |= esi
        eax &= b[n][3]
        ebx |= eax
        v14 = ebx
    elif index == 27:
        edx = b[n][0]
        edi = b[r][15]
        esi = b[r][21]
        edx |= edi
        edi = b[r][26]
        edx |= esi
        esi = b[r][22]
        edx |= edi
        edi = b[n][1]
        esi &= edx
        edx = b[r][8]
        ebx = b[r][15]
        edx &= edi
        edx |= ebx
        edi = b[r][16]
        esi |= edx
        edx = b[r][8]
        eax = b[r][21]
        edx |= edi
        eax &= edx
        esi |= eax
        v14 = esi
    elif index == 28:
        ebx = b[r][9]
        edi = b[r][16]
        esi = b[r][23]
        edx = b[n][0]
        ebx |= edi
        edi = b[r][15]
        ebx |= esi
        esi = b[r][8]
        ebx &= edi
        edi = b[r][21]
        ebx |= esi
        esi = b[r][22]
        edx |= edi
        edi = b[r][27]
        edx |= esi
        esi = b[r][16]
        edx |= edi
        edx &= esi
        edi = b[r][17]
        ebx |= edx
        edx = b[r][9]
        esi = b[r][23]
        edx |= edi
        edi = b[r][22]
        edx |= esi
        edx &= edi
        ebx |= edx
        edx = esi
        edx &= b[r][27]
        ebx |= edx
        v14 = ebx
    elif index == 29:
        edx = b[r][8]
        edi = b[r][16]
        ebx = b[r][23]
        edx |= edi
        esi = b[r][15]
        ebx |= edx
        edx = b[r][9]
        edx &= esi
        edi = b[r][22]
        ebx |= edx
        edx = b[r][17]
        edx &= edi
        esi = b[r][28]
        ebx |= edx
        edx = b[r][24]
        edx &= esi
        ebx |= edx
        v14 = ebx
    elif index == 30:
        ebx = b[r][8]
        esi = b[n][1]
        edi = b[n][2]
        esi &= ebx
        ebx = b[r][15]
        ebx &= edi
        edi = b[n][3]
        esi |= ebx
        ebx = b[r][21]
        ebx &= edi
        eax = b[r][26]
        ebx |= esi
        eax &= b[n][4]
        ebx |= eax
        v14 = ebx
    elif index == 31:
        edx = b[r][8]
        esi = b[n][1]
        edi = b[r][15]
        edx &= esi
        ebx = b[r][21]
        edx |= edi
        esi = b[r][22]
        ebx |= edx
        edx = b[r][8]
        edi = b[r][27]
        edx |= esi
        esi = b[r][26]
        edx |= edi
        edx &= esi
        ebx |= edx
        edx = edi
        edx &= b[r][30]
        ebx |= edx
        v14 = ebx
    elif index == 32:
        ebx = b[r][8]
        edi = b[r][9]
        esi = b[r][16]
        ebx |= edi
        edi = b[r][23]
        ebx |= esi
        esi = b[r][28]
        ebx |= edi
        ebx |= esi
        esi = b[r][15]
        esi &= ebx
        edx = b[r][8]
        edx &= b[n][1]
        ebx = b[r][16]
        esi |= edx
        edx = b[r][8]
        edx |= ebx
        ebx = b[r][28]
        edi = b[r][21]
        ebx |= edx
        ebx &= edi
        edi = b[r][23]
        ebx |= esi
        esi = b[r][22]
        edx |= edi
        ebx |= esi
        esi = b[r][28]
        edi = b[r][27]
        edx |= esi
        edx &= edi
        esi = b[r][31]
        ebx |= edx
        edx = b[r][0]
        edi = b[r][28]
        edx |= esi
        edx &= edi
        ebx |= edx
        v14 = ebx
    elif index == 33:
        esi = b[r][8]
        edi = b[n][1]
        ebx = b[r][15]
        esi &= edi
        ebx &= b[n][2]
        edi = b[n][3]
        esi |= ebx
        ebx = b[r][21]
        ebx &= edi
        edi = b[n][4]
        esi |= ebx
        ebx = b[r][26]
        ebx &= edi
        eax = b[r][30]
        ebx |= esi
        eax &= b[n][5]
        ebx |= eax
        v14 = ebx
    elif index == 34:
        edx = b[n][2]
        edi = b[r][26]
        ebx = b[r][30]
        edx |= edi
        esi = b[r][15]
        edx |= ebx
        ebx = b[r][8]
        edi = b[r][21]
        ebx &= edx
        edx &= esi
        esi = b[r][22]
        ebx |= edx
        edx = b[r][16]
        ebx |= edi
        edi = b[r][27]
        edx |= esi
        esi = b[r][31]
        edx |= edi
        eax = b[r][26]
        edx |= esi
        eax &= edx
        ebx |= eax
        v14 = ebx
    elif index == 35:
        ebx = b[r][8]
        esi = b[n][1]
        edi = b[n][2]
        esi &= ebx
        ebx = b[r][15]
        ebx &= edi
        edi = b[n][3]
        esi |= ebx
        ebx = b[r][21]
        ebx &= edi
        edi = b[n][4]
        esi |= ebx
        ebx = b[r][26]
        ebx &= edi
        edi = b[n][5]
        esi |= ebx
        ebx = b[r][30]
        ebx &= edi
        eax = b[r][33]
        ebx |= esi
        eax &= b[n][6]
        ebx |= eax
        v14 = ebx
    return v14


def wall_turns_away(ext: int, r: int, index: int) -> bool:
    """A wall's facing (its proto's extended flags) keeps the light off its tile from these directions."""
    if ext & 0x8000000 or ext & 0x40000000:
        return r not in (W, NW) and (r != NE or index >= 8) and (r != SW or index <= 15)
    if ext & 0x10000000:
        return r not in (NE, NW)
    if ext & 0x20000000:
        return r not in (NE, E, W, NW) and (r != SW or index <= 15)
    return r not in (NE, E) and (r != NW or index <= 7)


@cache
def _files():
    from f1 import knowledge
    from f1.dat import GameFiles

    return GameFiles(knowledge.INSTANCE)


@cache
def wall_flags(pid: int) -> int:
    """A wall proto's extended flags (its .PRO file, +24 big-endian; 0 when unknown)."""
    from f1 import knowledge

    entry = knowledge.proto(pid)
    if not entry or "file" not in entry:
        return 0
    raw = _files().read(entry["file"])
    return struct.unpack(">I", raw[24:28])[0] if len(raw) >= 28 else 0


@dataclass(frozen=True)
class Thing:
    """What the light's walk reads of an object."""

    address: int
    tile: int
    elevation: int
    flags: int
    kind: int  # the fid's type
    pid: int
    distance: int = 0
    intensity: int = 0


def read_objects(mem) -> dict[int, list[Thing]]:
    """Every object on the map by tile, in objectTable's list order."""
    heads = struct.unpack("<40000I", mem.read(em.GLOBALS["objectTable"].address, 4 * 40000))
    out: dict[int, list[Thing]] = {}
    for tile, node in enumerate(heads):
        hops = 0
        while node and hops < 1000:
            obj, node = struct.unpack("<II", mem.read(node, 8))
            raw = mem.read(obj, Obj.SIZE)
            v = struct.unpack_from("<33i", raw)
            out.setdefault(tile, []).append(
                Thing(
                    obj,
                    v[Obj.TILE // 4],
                    v[Obj.ELEVATION // 4],
                    v[Obj.FLAGS // 4] & 0xFFFFFFFF,
                    (v[Obj.FID // 4] >> 24) & 0xF,
                    v[Obj.PID // 4] & 0xFFFFFFFF,
                    v[Obj.LIGHT_DISTANCE // 4],
                    v[Obj.LIGHT_INTENSITY // 4],
                )
            )
            hops += 1
    return out


def spread(by_tile: dict[int, list[Thing]], tile: int, elevation: int, distance: int = DUDE_DISTANCE,
           intensity: int = DUDE_INTENSITY, walls=wall_flags, ignore: frozenset[int] = frozenset()) -> dict[int, int]:  # fmt: skip
    """The intensity a light at TILE adds to each tile (the source's tile included). WALLS(pid): a wall proto's
    extended flags. IGNORE: objects (addresses) not where BY_TILE has them (the player, moved to TILE)."""
    if intensity <= 0 or not 0 <= tile < light.TILES:
        return {}
    distance, intensity = min(distance, 8), min(intensity, 65536)
    step = int((intensity - 655) / (distance + 1))  # C division
    out = {tile: intensity}
    offsets = OFFSETS[tile & 1]
    b = [[0] * 36 for _ in range(6)]
    for index in range(36):
        if distance < DISTANCE[index]:
            continue
        for r in range(6):
            v14 = _blocked(index, b, r, (r + 1) % 6)
            if v14 == 0:
                t = tile + offsets[r][index]
                if 0 <= t < light.TILES:
                    lit = True
                    for o in by_tile.get(t, ()):
                        if o.flags & HIDDEN or o.address in ignore:
                            continue
                        if o.elevation > elevation:
                            break
                        if o.elevation != elevation:
                            continue
                        v14 = 0 if o.flags & LIGHT_THRU else 1
                        if o.kind == WALL:
                            if not o.flags & FLAT and wall_turns_away(walls(o.pid), r, index):
                                lit = False
                        elif v14 and E <= r <= SW:
                            lit = False
                        if v14:
                            break
                    if lit:
                        out[t] = out.get(t, 0) + intensity - step * DISTANCE[index]
            b[r][index] = v14
    return out


def sources(by_tile: dict[int, list[Thing]], elevation: int) -> list[Thing]:
    """The objects whose light obj_adjust_light spreads on this elevation, in objectTable's order."""
    return [
        o
        for tile in sorted(by_tile)
        for o in by_tile[tile]
        if o.elevation == elevation and o.flags & LIGHTING and not o.flags & HIDDEN and o.intensity > 0
    ]


def rebuild(by_tile: dict[int, list[Thing]], elevation: int, walls=wall_flags) -> list[int]:
    """obj_rebuild_all_light for one elevation: 655 everywhere plus every lit object's spread."""
    grid = [light.BASE] * light.TILES
    for o in sources(by_tile, elevation):
        for t, v in spread(by_tile, o.tile, elevation, o.distance, o.intensity, walls).items():
            grid[t] += v
    return grid


class PlayerLight:
    """The visible light at a tile with the player moved (for a plan): the grid as read, less the player's light from
    where he stands, plus his light from STAND; then as obj_get_visible_light takes it for a critter other than the
    player (at least the ambient, at most 65536)."""

    def __init__(self, by_tile: dict[int, list[Thing]], dude: Thing, grid, ambient: int, walls=wall_flags) -> None:
        self.by_tile, self.dude, self.grid, self.ambient, self.walls = by_tile, dude, grid, ambient, walls
        self.now = self._spread(dude.tile, frozenset())
        self.cache: dict[int, dict[int, int]] = {dude.tile: self.now}

    def _spread(self, stand: int, ignore: frozenset[int]) -> dict[int, int]:
        d = self.dude
        return spread(self.by_tile, stand, d.elevation, d.distance, d.intensity, self.walls, ignore)

    def __call__(self, stand: int, target: int) -> int:
        if stand not in self.cache:
            self.cache[stand] = self._spread(stand, frozenset({self.dude.address}))
        value = self.grid[target] - self.now.get(target, 0) + self.cache[stand].get(target, 0)
        return light.visible(self.ambient, value)
