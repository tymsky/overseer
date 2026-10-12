"""The critical hit tables of FALLOUTW.EXE, read from the exe itself (static; the file is only read).

fallout1-ce combat.c holds them as crit_succ_eff[15 kill types][9 hit locations][6 rows] and pc_crit_succ_eff[9][6]
(the player as the defender), right after hit_location_penalty[9] (-40, -30, -30, 0, -20, -20, -60, -30, 0). The exe
keeps the same order in its data section: the penalties, then the 810 rows of the critters' table, then the 54 of the
player's. Each row is seven little-endian ints: (damage multiplier in halves, flags, massive stat (-1 none), massive
stat modifier, massive flags, message id, massive message id); the flags are DAM_* bits (object_types.h).

The table is found by its neighbour: the penalty array followed by the man's first head row. `parse` checks that the
rows look like rows (multipliers 2..10, stats -1..6, flags under 2^24, message ids 0..6999) and refuses otherwise.
Message ids are mostly 5000..6999; six rows of the exe's own data hold others (the child's legs: a flag mask where a
message id would be, two rows with 1000), the same in Fallout 2's tables, and are kept as they are.

The rows an attack uses (combat.c attack_crit_success): d100 plus the attacker's Better Criticals stat picks row 0 at
<= 20, 1 at <= 45, 2 at <= 70, 3 at <= 90, 4 at <= 100, else 5; the defender's kill type picks the table (the player's
own table when the player is hit); an uncalled shot uses the torso's rows (compute_attack sets the location before).
The massive part applies when a roll of the massive stat fails: d10 above stat + modifier (stat.c stat_result).
"""

import struct
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from f1 import paths

KILL_TYPES = ("man", "woman", "child", "super mutant", "ghoul", "brahmin", "radscorpion", "rat", "floater", "centaur",
              "robot", "dog", "mantis", "death claw", "plant")  # fmt: skip
HEAD, LEFT_ARM, RIGHT_ARM, TORSO, RIGHT_LEG, LEFT_LEG, EYES, GROIN, UNCALLED = range(9)
LOCATIONS = 9
ROWS = 6
LOCATION_PENALTY = (-40, -30, -30, 0, -20, -20, -60, -30, 0)  # combat.c hit_location_penalty
FIRST_ROW = (4, 0, -1, 0, 0, 5001, 5000)  # the man's head, row 0
ROW_SIZE = 28
DAM_KNOCKED_OUT, DAM_KNOCKED_DOWN, DAM_DEAD, DAM_BYPASS, DAM_LOSE_TURN = 0x01, 0x02, 0x80, 0x800, 0x8000
ROW_LIMITS = (20, 45, 70, 90, 100)  # the d100 + Better Criticals bounds of rows 0-4 (row 5 above)


@dataclass(frozen=True)
class Row:
    multiplier: int  # in halves: 4 is x2
    flags: int
    massive_stat: int  # -1: none
    massive_modifier: int
    massive_flags: int
    message: int
    massive_message: int


@dataclass(frozen=True)
class Tables:
    critters: tuple[tuple[tuple[Row, ...], ...], ...]  # [kill type][location][row]
    player: tuple[tuple[Row, ...], ...]  # [location][row]
    address: int  # the critters' table's virtual address in the exe

    def rows(self, kill_type: int | None, location: int) -> tuple[Row, ...]:
        """The rows of a hit at LOCATION (uncalled: the torso) on a critter of KILL_TYPE (None: the player)."""
        loc = TORSO if location == UNCALLED else location
        return self.player[loc] if kill_type is None else self.critters[kill_type][loc]


def _sections(exe: bytes) -> list[tuple[int, int, int, int]]:
    """The PE sections as (virtual address, raw offset, raw size) with the image base added."""
    pe = struct.unpack_from("<I", exe, 0x3C)[0]
    count, optional = struct.unpack_from("<H", exe, pe + 6)[0], struct.unpack_from("<H", exe, pe + 20)[0]
    base = struct.unpack_from("<I", exe, pe + 24 + 28)[0]
    out = []
    for i in range(count):
        at = pe + 24 + optional + 40 * i
        _vsize, va, rsize, roff = struct.unpack_from("<IIII", exe, at + 8)
        out.append((base + va, roff, rsize, i))
    return out


def virtual_address(exe: bytes, offset: int) -> int:
    if exe[:2] != b"MZ":
        return -1
    for va, roff, rsize, _ in _sections(exe):
        if roff <= offset < roff + rsize:
            return va + offset - roff
    return -1


def _check(row: Row) -> bool:
    return (
        2 <= row.multiplier <= 10
        and 0 <= row.message <= 6999
        and 0 <= row.massive_message <= 6999
        and -1 <= row.massive_stat <= 6
        and 0 <= row.flags < 1 << 24
        and 0 <= row.massive_flags < 1 << 24
    )


def parse(exe: bytes) -> Tables:
    anchor = struct.pack("<9i", *LOCATION_PENALTY) + struct.pack("<7i", *FIRST_ROW)
    at = exe.find(anchor)
    if at < 0 or exe.find(anchor, at + 1) >= 0:
        raise ValueError("the critical tables' anchor is not in the exe exactly once")
    start = at + 36
    count = len(KILL_TYPES) * LOCATIONS * ROWS + LOCATIONS * ROWS
    if start + count * ROW_SIZE > len(exe):
        raise ValueError("the critical tables run past the end of the exe")
    rows = [Row(*struct.unpack_from("<7i", exe, start + ROW_SIZE * k)) for k in range(count)]
    bad = [k for k, r in enumerate(rows) if not _check(r)]
    if bad:
        raise ValueError(f"{len(bad)} critical rows do not look like rows (first: {bad[0]})")

    def block(first: int) -> tuple[tuple[Row, ...], ...]:
        return tuple(tuple(rows[first + loc * ROWS + r] for r in range(ROWS)) for loc in range(LOCATIONS))

    critters = tuple(block(k * LOCATIONS * ROWS) for k in range(len(KILL_TYPES)))
    player = block(len(KILL_TYPES) * LOCATIONS * ROWS)
    return Tables(critters, player, virtual_address(exe, start))


@cache
def tables(exe: Path | None = None) -> Tables:
    return parse((exe or paths.INSTANCE / "FALLOUTW.EXE").read_bytes())


def row_odds(better_criticals: int) -> tuple[Fraction, ...]:
    """The chance of each of the six rows: d100 + Better Criticals against ROW_LIMITS."""
    counts = [0] * ROWS
    for d in range(1, 101):
        v = d + better_criticals
        counts[next((i for i, limit in enumerate(ROW_LIMITS) if v <= limit), ROWS - 1)] += 1
    return tuple(Fraction(c, 100) for c in counts)


def massive_odds(row: Row, stat_value: int | None) -> Fraction:
    """The chance the row's massive part applies: a d10 above the stat plus the modifier (stat_result fails)."""
    if row.massive_stat == -1 or stat_value is None:
        return Fraction(0)
    value = stat_value + row.massive_modifier
    return Fraction(sum(1 for d in range(1, 11) if d > value), 10)
