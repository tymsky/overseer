"""Which script drives an object: the critter "Peasant" at Shady Sands' gate is Seth by its script, SETH.INT.

scripts.c (CE): `scriptlists` (0x507860) holds one list per script type (the type is the sid's top byte; critters
are 4); a list is {head, tail, extents, next id}; an extent is 16 Script records of 208 bytes (measured live: CE's
field names suggest 224), then its length and the next extent. A record starts with its sid; +20 holds the index
into SCRIPTS/SCRIPTS.LST. An object's sid is at +0x78.

A script's local variables (local_var(n)) live in the map's array `map_local_vars` (0x505AE8; its length
`num_map_local_vars` 0x505AF0) from the record's offset (+0x20); +0x24 holds how many it has. Checked in FALLOUTW.EXE
(capstone): scr_get_local_var (0x494EA4) reads the record's +0x24 and +0x20 and calls map_get_local_var (0x473E78),
which bounds the index by 0x505AF0 and reads [0x505AE8] + 4 n.
"""

from f1 import engine_map as em
from f1 import knowledge
from f1.engine_map import Obj
from f1.memory import GameMemory

LIST_SIZE, SCRIPT_SIZE, EXTENT_SCRIPTS, SCRIPT_INDEX = 16, 208, 16, 20
LOCAL_OFFSET, LOCAL_COUNT = 0x20, 0x24
MAP_LOCAL_VARS, NUM_MAP_LOCAL_VARS = 0x505AE8, 0x505AF0


def record(mem: GameMemory, sid: int) -> int | None:
    """The address of the Script record with this sid, or None."""
    sid &= 0xFFFFFFFF
    if sid == 0xFFFFFFFF:
        return None
    extent = mem.u32(em.GLOBALS["scriptlists"].address + LIST_SIZE * ((sid >> 24) & 0xFF))
    for _ in range(64):
        if not extent:
            return None
        count = mem.i32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE)
        for i in range(max(0, min(count, EXTENT_SCRIPTS))):
            at = extent + SCRIPT_SIZE * i
            if mem.u32(at) == sid:
                return at
        extent = mem.u32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE + 4)
    return None


def script_index(mem: GameMemory, sid: int) -> int | None:
    """The SCRIPTS.LST index of the script with this sid, or None."""
    at = record(mem, sid)
    return mem.i32(at + SCRIPT_INDEX) if at is not None else None


def local_var(mem: GameMemory, address: int, n: int) -> int | None:
    """The object's script's local_var(n), or None when it has no such variable (yet: the engine gives a script its
    variables the first time it reads one)."""
    at = record(mem, mem.i32(address + Obj.SID))
    if at is None or not 0 <= n < mem.i32(at + LOCAL_COUNT):
        return None
    index = mem.i32(at + LOCAL_OFFSET) + n
    if mem.i32(at + LOCAL_OFFSET) < 0 or not 0 <= index < mem.i32(NUM_MAP_LOCAL_VARS):
        return None
    return mem.i32(mem.u32(MAP_LOCAL_VARS) + 4 * index)


def script_of(mem: GameMemory, address: int) -> str:
    """The script name (e.g. "seth") of the object at `address`; "" when it has none."""
    index = script_index(mem, mem.i32(address + Obj.SID))
    names = knowledge.load().get("scripts", [])
    return names[index] if index is not None and 0 <= index < len(names) else ""
