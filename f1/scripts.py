"""Which script drives an object: the critter "Peasant" at Shady Sands' gate is Seth by its script, SETH.INT.

scripts.c (CE): `scriptlists` (0x507860) holds one list per script type (the type is the sid's top byte; critters
are 4); a list is {head, tail, extents, next id}; an extent is 16 Script records of 208 bytes (measured live: CE's
field names suggest 224), then its length and the next extent. A record starts with its sid; +20 holds the index
into SCRIPTS/SCRIPTS.LST. An object's sid is at +0x78.
"""

from f1 import engine_map as em
from f1 import knowledge
from f1.engine_map import Obj
from f1.memory import GameMemory

LIST_SIZE, SCRIPT_SIZE, EXTENT_SCRIPTS, SCRIPT_INDEX = 16, 208, 16, 20


def script_index(mem: GameMemory, sid: int) -> int | None:
    """The SCRIPTS.LST index of the script with this sid, or None."""
    sid &= 0xFFFFFFFF
    if sid == 0xFFFFFFFF:
        return None
    extent = mem.u32(em.GLOBALS["scriptlists"].address + LIST_SIZE * ((sid >> 24) & 0xFF))
    for _ in range(64):
        if not extent:
            return None
        count = mem.i32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE)
        for i in range(max(0, min(count, EXTENT_SCRIPTS))):
            record = extent + SCRIPT_SIZE * i
            if mem.u32(record) == sid:
                return mem.i32(record + SCRIPT_INDEX)
        extent = mem.u32(extent + EXTENT_SCRIPTS * SCRIPT_SIZE + 4)
    return None


def script_of(mem: GameMemory, address: int) -> str:
    """The script name (e.g. "seth") of the object at `address`; "" when it has none."""
    index = script_index(mem, mem.i32(address + Obj.SID))
    names = knowledge.load().get("scripts", [])
    return names[index] if index is not None and 0 <= index < len(names) else ""
