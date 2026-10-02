"""World-map travel time, by the 1.1 exe's own rules and tables.

The world map's main loop (in the exe; fallout1-ce's worldmap.c documents it) moves the party toward its target one Bresenham step (a pixel)
per iteration on desert (terrain 0), one step every second iteration on mountains (1), and five steps in four
iterations, three of them with time, in the city squares (2); the ocean (3) is not walked. Each iteration with time
adds time_adder ticks, which CalcTimeAdder (0x4AEA88, disassembled from the instance's FALLOUTW.EXE)
sets to int(864000 / wmap_day) x (1 - 0.25 x Pathfinder), where wmap_day = int(min(Outdoorsman, 100) x 0.01 x 60 +
60): a day of travel is 60 map steps at Outdoorsman 0 and 120 at 100, and two Pathfinder ranks halve the time again.
Every wmap_day iterations the party heals 8 x its healing rate and a random encounter is rolled: 3d6 below 6, 7, 9 or
10 by the square's encounter class. Both tables are read from the exe (WorldTerraTable 0x4A948C and
WorldEcountChanceTable 0x4A97D4, 30 rows of 28 bytes; they match fallout1-ce's).

Live check: Vault 13 to Shady Sands in 4.121 game days for the Idealist on Easy (Outdoorsman 53), the
model's 4.121, once worldmap.recentre stopped the view's scrolling from moving the first click off the line. Before
that fix every trip took 10-14 % more than this model (the Agent's 6.77 days against 6.15).

    python -m f1.travel [OUTDOORSMAN [PATHFINDER]]     days between every two towns
"""

import functools
import sys

from f1 import instance, verify_exe, worldmap

TERRA_TABLE, ENCOUNTER_TABLE = 0x4A948C, 0x4A97D4
ROWS, COLUMNS, SQUARE = 30, 28, 50
DESERT, MOUNTAIN, CITY = 0, 1, 2
TICKS_PER_DAY = 864000
# 3d6 below 6, 7, 9, 10 (worldmap.c): 10, 20, 56 and 81 of the 216 throws
ENCOUNTER_CHANCE = {0: 10 / 216, 1: 20 / 216, 2: 56 / 216, 3: 81 / 216}


@functools.cache
def tables() -> tuple[tuple[tuple[int, ...], ...], tuple[tuple[int, ...], ...]]:
    """(terrain, encounter class) per 50 x 50 square, [row][column], from the instance's exe."""
    data = (instance.INSTANCE_DIR / "FALLOUTW.EXE").read_bytes()
    sections = verify_exe._sections(data)

    def grid(va: int) -> tuple[tuple[int, ...], ...]:
        off = verify_exe._file_offset(sections, va)
        if off is None:
            raise ValueError(f"0x{va:X} is not in the exe's file")
        return tuple(tuple(data[off + COLUMNS * r : off + COLUMNS * (r + 1)]) for r in range(ROWS))

    return grid(TERRA_TABLE), grid(ENCOUNTER_TABLE)


def rates(outdoorsman: int, pathfinder: int = 0) -> tuple[int, int]:
    """(wmap_day, time_adder): map iterations between two rolls, and the ticks one iteration with time adds."""
    wmap_day = int(min(outdoorsman, 100) / 100 * 60 + 60)
    time_adder = int(TICKS_PER_DAY / wmap_day)
    return wmap_day, int(time_adder * (1 - 0.25 * pathfinder))


def line(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """The world-map pixels a walk from `start` to `end` passes, `start` left out: one per step of the longer axis
    (y when the two are equal). After i steps the shorter axis has moved ceil(i * short / long) pixels when y is the
    longer, one fewer (never below 0) when x is: the exe's line, in closed form."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x1 >= x0 else -1), (1 if y1 >= y0 else -1)
    if dx <= dy:
        return [(x0 + sx * -(-i * dx // dy), y0 + sy * i) for i in range(1, dy + 1)]
    return [(x0 + sx * i, y0 + sy * max(0, -(-i * dy // dx) - 1)) for i in range(1, dx + 1)]


# What one turn of the world map's loop does on each terrain, by its countdown (the turns left before the next
# pixel on mountains, or before the city's double step): (pixels to walk, whether the turn costs time, the next
# countdown). Desert: a pixel and time every turn. Mountains: a pixel every second turn, time every turn. City
# squares: a pixel a turn with time, and every fourth turn two pixels without time.
def _turn(kind: int, countdown: int) -> tuple[int, bool, int]:
    if kind == MOUNTAIN:
        return (1, True, 2) if countdown <= 1 else (0, True, countdown - 1)
    if kind == CITY:
        return (2, False, 4) if countdown <= 1 else (1, True, countdown - 1)
    return 1, True, 0


def leg(start: tuple[int, int], end: tuple[int, int], outdoorsman: int, pathfinder: int = 0) -> tuple[float, float]:
    """Game days and the expected number of random encounters for a straight walk from world point `start` to `end`:
    turn by turn along `line`, each turn by the terrain under the party (`_turn`), a roll every wmap_day turns. The
    loop ends on the turn that finds no pixel left, and that turn counts too."""
    terra, classes = tables()
    wmap_day, time_adder = rates(outdoorsman, pathfinder)
    path = line(start, end)
    here, walked = start, 0
    countdown = turns = timed = 0
    encounters = 0.0
    while True:
        want, costs, countdown = _turn(terra[here[1] // SQUARE][here[0] // SQUARE], countdown)
        took = min(want, len(path) - walked)
        if path[walked : walked + took]:
            here = path[walked + took - 1]
        walked += took
        timed += costs or took == 0  # a turn that finds no pixel left costs time on any terrain
        turns += 1
        if turns % wmap_day == 0:
            encounters += ENCOUNTER_CHANCE[classes[here[1] // SQUARE][here[0] // SQUARE]]
        if took < want:
            break
    return timed * time_adder / TICKS_PER_DAY, encounters


def town_leg(a: str, b: str, outdoorsman: int, pathfinder: int = 0) -> tuple[float, float]:
    return leg(worldmap.town_xy(a), worldmap.town_xy(b), outdoorsman, pathfinder)


def main(argv: list[str]) -> int:
    if len(argv) > 2 or not all(a.isdigit() for a in argv):
        print(__doc__)
        return 2
    outdoorsman = int(argv[0]) if argv else 0
    pathfinder = int(argv[1]) if len(argv) > 1 else 0
    _wmap_day, time_adder = rates(outdoorsman, pathfinder)
    print(f"Outdoorsman {outdoorsman}, Pathfinder {pathfinder}: {TICKS_PER_DAY / time_adder:.0f} steps a day")
    towns = list(worldmap.TOWNS)
    print(" " * 14 + "".join(f"{t[:6]:>7}" for t in towns))
    for a in towns:
        cells = "".join(f"{town_leg(a, b, outdoorsman, pathfinder)[0]:7.1f}" if a != b else "      -" for b in towns)
        print(f"{a[:13]:>13} {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
