"""Traps: the spatial scripts that hurt whoever walks into their area, where they lie (the map file, or the game's own
script list), whether they are still armed, and the tiles a path must keep off.

The trigger (fallout1-ce scripts.cc scr_chk_spatials_in 0x4947E4, called by anim.cc after every hex an object
walks): a spatial script runs for any object (not hidden, not flat) that enters a tile at hex distance <= its radius
from its tile (radius 0: the tile itself), on its elevation. So the tiles to keep off are a disk.

The game's list (`scriptlists` 0x507860, the spatial list is the second, f1.scripts): records of 208 bytes, 16 to an
extent, the extent's length and next after them; a record keeps its sid at +0, its built tile at +0x08 (the tile in
the low 26 bits, the elevation from bit 29), its radius at +0x0C, its SCRIPTS.LST index at +0x14, its local
variables' offset at +0x20 and count at +0x24. A local variable is `map_local_vars` (0x505AE8, int*)[offset + n]
(map_get_local_var 0x473E78); an offset of -1 or a count of 0 means none was set yet (0).

What each trap does (decompiled with f1.intdump; damage by the game's own random()):

- trapflor (the Glow, the Hub's Old Town below), vexdtrap (the Vats, while their alarm is up): every entry rolls the
  walker's Traps. A success stops the walk and says a trap is near (25 XP); the player's third success disarms it
  (local 0 set). A failure while armed sets local 0 and blows: an explosion of 10-30 at the walker's tile.
- velctrap, vplstrap (the Vats, while their alarm is up): the same rolls; a failure does 15-40 electrical or plasma
  damage to the walker.
- gastrap, lazrtrap (the Glow, while its power is on and its weapons armed): once (local 0), 1-6 damage to the player,
  no roll.
- painfeld (the Military Base): 10-30 electrical damage to whoever enters, every time, no roll.
- hotspot (the Glow): 20-50 rads to the player every time.

So an armed trap is avoided; when the way leads only through it, every step inside is a Traps roll (trapflor and
the Vats' traps), or certain damage.
"""

import struct
from dataclasses import dataclass
from fractions import Fraction

from f1 import engine_map as em
from f1 import geometry, knowledge
from f1.memory import GameMemory

SPATIAL = 1
LIST_SIZE, SCRIPT_SIZE, EXTENT_SCRIPTS = 16, 208, 16
SID, BUILT, RADIUS, INDEX, LVAR_OFFSET, LVAR_COUNT = 0x00, 0x08, 0x0C, 0x14, 0x20, 0x24
MAP_LOCAL_VARS = 0x505AE8  # int*, map_get_local_var
TILE_MASK, ELEVATION_SHIFT = 0x3FFFFFF, 29


@dataclass(frozen=True)
class Kind:
    damage: str
    rolled: bool  # a Traps roll on every entry (a success stops the walk)
    once: bool  # local 0 set once it fired (or was disarmed)
    condition: str = ""  # a global variable the trap needs, when it has one


TRAPS = {
    "trapflor": Kind("explosion 10-30", True, True),
    "vexdtrap": Kind("explosion 10-30", True, True, "VATS_ALERT"),
    "velctrap": Kind("electrical 15-40", True, True, "VATS_ALERT"),
    "vplstrap": Kind("plasma 15-40", True, True, "VATS_ALERT"),
    "gastrap": Kind("1-6", False, True, "START_POWER, WEAPONS_ARMED"),
    "lazrtrap": Kind("1-6", False, True, "START_POWER, WEAPONS_ARMED"),
    "painfeld": Kind("electrical 10-30", False, False),
    "hotspot": Kind("radiation 20-50", False, False),
}


@dataclass(frozen=True)
class Spatial:
    sid: int
    tile: int
    elevation: int
    radius: int
    name: str  # the script's name in SCRIPTS.LST
    local0: int = 0  # its first local variable (a trap's fired-or-disarmed mark)

    @property
    def armed(self) -> bool:
        kind = TRAPS.get(self.name)
        return kind is not None and not (kind.once and self.local0)

    def area(self) -> set[int]:
        """The tiles whose entry runs the script."""
        return disk(self.tile, self.radius)


def disk(tile: int, radius: int) -> set[int]:
    """The tiles at hex distance <= RADIUS (tile_in_tile_bound)."""
    return {t for r in range(radius + 1) for t in geometry.ring(tile, r) if 0 <= t < geometry.GRID_SIZE}


def _name(index: int) -> str:
    names = knowledge.load().get("scripts", [])
    return names[index] if 0 <= index < len(names) else ""


def local_var(mem: GameMemory, record: int, n: int = 0) -> int:
    """A script's local variable, as scr_get_local_var reads it (0 before the script set one)."""
    offset, count = mem.i32(record + LVAR_OFFSET), mem.i32(record + LVAR_COUNT)
    if offset < 0 or n >= count:
        return 0
    return mem.i32(mem.u32(MAP_LOCAL_VARS) + 4 * (offset + n))


def spatial(mem: GameMemory, limit: int = 64) -> list[Spatial]:
    """The loaded map's spatial scripts, in the list's order."""
    extent = mem.u32(em.GLOBALS["scriptlists"].address + LIST_SIZE * SPATIAL)
    out = []
    for _ in range(limit):
        if not extent:
            break
        count = mem.i32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE)
        for k in range(max(0, min(count, EXTENT_SCRIPTS))):
            record = extent + SCRIPT_SIZE * k
            sid, _next, built, radius = struct.unpack("<4i", mem.read(record, 16))
            index = mem.i32(record + INDEX)
            tile, elevation = built & TILE_MASK, (built >> ELEVATION_SHIFT) & 7
            out.append(Spatial(sid, tile, elevation, radius, _name(index), local_var(mem, record)))
        extent = mem.u32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE + 4)
    return out


def from_map(name: str) -> list[Spatial]:
    """A map file's traps (statically: every one armed, as the map is first entered)."""
    from f1 import mapfile

    out = []
    for script, built, radius in mapfile.read(name).spatial:
        if script in TRAPS:
            out.append(Spatial(-1, built & TILE_MASK, (built >> ELEVATION_SHIFT) & 7, radius, script))
    return out


def armed(mem: GameMemory, elevation: int) -> list[Spatial]:
    """The loaded map's armed traps on this elevation."""
    return [s for s in spatial(mem) if s.elevation == elevation and s.armed]


def avoid(traps: list[Spatial], margin: int = 0) -> frozenset[int]:
    """The tiles a path keeps off: every trap's area, MARGIN hexes wider."""
    out: set[int] = set()
    for s in traps:
        out |= disk(s.tile, s.radius + margin)
    return frozenset(out)


def step_odds(traps_skill: int) -> Fraction:
    """The chance that one entry into an armed rolled trap's area does not set it off (roll_vs_skill at +0: a d100 at
    most the skill succeeds, no cap); the walk stops either way."""
    return Fraction(max(0, min(traps_skill, 100)), 100)
