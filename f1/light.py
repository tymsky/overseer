"""Light, as Fallout 1 keeps it (fallout1-ce light.cc, object.cc obj_get_visible_light, combat.cc
determine_to_hit_func), each address and constant read in the 1.1 exe's code with capstone:

- `ambient_light` (0x5057E0, written by light_set_ambient 0x46CA88): the map's ambient light, 16384..65536. A map's
  load sets 65536; map scripts set it by `set_light_level(level)` (intextra.cc op_set_light_level: the table at
  0x44B570 = 16384, 40960, 65536; level 50 gives 40960, above 50 40960 + level x 24576 / 100, below
  16384 + level x 24576 / 100), and light_set_ambient adds 0x1999 per rank of Night Vision (perk 9) and clamps.
  The outdoor maps' scripts follow the clock: level 40 at night (26214: the shot's -40), 100 by day.
- `tile_intensity` (0x59CF1C, int[3][40000], light_get_tile_true 0x46CB2C): each tile's light, 655 after
  light_reset_tiles, plus what every lit object spreads over it (f1.lightmap).
- obj_get_visible_light (0x47C8EC): the tile's light (less the player's own light, for the player), at least the
  ambient, at most 65536.
- The player's shot (0x422061..0x422085): -40 at a visible light <= 0x6666 (26214), -25 at <= 0x9999 (39321), -10 at
  <= 0xCCCC (52428). Foes' shots take no light penalty.
"""

import struct

AMBIENT = 0x5057E0
TILE_INTENSITY = 0x59CF1C
MAX, MIN = 0x10000, 0x4000
BASE = 655  # light_reset_tiles
NIGHT_VISION = 0x1999  # per rank, light_set_ambient
LEVELS = (0x4000, 0xA000, 0x10000)  # op_set_light_level's table (0x44B570)
PENALTIES = ((26214, -40), (39321, -25), (52428, -10))
TILES, ELEVATIONS = 40000, 3


def ambient(mem) -> int:
    return mem.i32(AMBIENT)


def tile(mem, elevation: int, tile_number: int) -> int:
    """light_get_tile_true: 0 off the grid."""
    if not (0 <= elevation < ELEVATIONS and 0 <= tile_number < TILES):
        return 0
    return mem.i32(TILE_INTENSITY + 4 * (TILES * elevation + tile_number))


def grid(mem, elevation: int) -> tuple[int, ...]:
    """The 40000 tile intensities of one elevation."""
    return struct.unpack(f"<{TILES}i", mem.read(TILE_INTENSITY + 4 * TILES * elevation, 4 * TILES))


def visible(ambient_light: int, tile_light: int, own: int = 0) -> int:
    """obj_get_visible_light from its two reads (OWN: the player's own light intensity, for the player)."""
    value = tile_light - own
    return min(value, MAX) if value >= ambient_light else ambient_light


def of_object(mem, elevation: int, tile_number: int, own: int = 0) -> int:
    """obj_get_visible_light of an object standing there, read from the running game."""
    return visible(ambient(mem), tile(mem, elevation, tile_number), own)


def penalty(intensity: int) -> int:
    """The player's to-hit change against a target in this visible light."""
    return next((p for limit, p in PENALTIES if intensity <= limit), 0)


def from_level(level: int, night_vision: int = 0) -> int:
    """The ambient light a map script's set_light_level(LEVEL) leaves (int division as the exe's)."""
    low, mid, high = LEVELS
    if level == 50:
        value = mid
    elif level > 50:
        value = mid + level * (high - mid) // 100
    else:
        value = low + level * (mid - low) // 100
    return max(MIN, min(MAX, value + NIGHT_VISION * night_vision))
