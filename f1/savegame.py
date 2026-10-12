"""A savegame's global variables (quest state, karma), read from its SAVE.DAT without the game.

    python -m f1.savegame SAVE [NAME ...]    SAVE: a SAVE.DAT, a SLOTnn folder or a folder of them; NAMEs are
                                             VAULT13.GAM names (without names: every GVAR that is not 0)

Measured on every kept save: SAVE.DAT holds the GVARs twice, as big-endian ints in VAULT13.GAM's
order, identical, the first block always at byte 30055 (after the 30000-byte header), the second at an offset that
varies with the save. The reader anchors on GVARs 148-149, the Followers' and Necropolis' invasion dates (90, 110):
no script changes those two (checked over every script). The save's description is the 30 bytes at 0x3D; header()
reads the rest of what the save screen lists.
"""

import struct
import sys
from pathlib import Path

from f1 import knowledge

COUNT = 618  # GVARs in VAULT13.GAM, as many as the engine holds in memory (tests/test_knowledge.py)
ANCHOR = 148  # FOLLOWERS_INVADED_DATE, followed by NECROPOLIS_INVADED_DATE
ANCHOR_VALUES = (90, 110)
HEADER = 30000


def gvars(data: bytes) -> list[int]:
    """Every GVAR of a SAVE.DAT's contents, from the first block after the header."""
    at = data.find(struct.pack(">2i", *ANCHOR_VALUES), HEADER)
    base = at - ANCHOR * 4
    if at < 0 or base < HEADER or base + COUNT * 4 > len(data):
        raise ValueError("no GVAR block in this SAVE.DAT")
    return list(struct.unpack(f">{COUNT}i", data[base : base + COUNT * 4]))


def description(data: bytes) -> str:
    return data[0x3D : 0x3D + 30].split(b"\0")[0].decode("latin-1")


def _text(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("latin-1")


def header(data: bytes) -> dict:
    """What the save screen lists (loadsave.c SaveHeader, big-endian): the character, the description, the game's date
    and time, and where the save was made (the elevation, the map's index and its file, e.g. "V13ENT.SAV")."""
    if len(data) < 131 or not data.startswith(b"FALLOUT SAVE FILE"):
        raise ValueError("not a SAVE.DAT")
    month, day, year = struct.unpack(">3h", data[101:107])
    elevation, map_index = struct.unpack(">2h", data[111:115])
    return {
        "character": _text(data[29:61]),
        "description": _text(data[61:91]),
        "game_date": (year, month, day),
        "game_time": struct.unpack(">I", data[107:111])[0],
        "elevation": elevation,
        "map_index": map_index,
        "map_file": _text(data[115:131]),
    }


def save_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if (path / "SAVE.DAT").is_file():
        return [path / "SAVE.DAT"]
    return sorted(path.glob("SLOT*/SAVE.DAT"))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    files = save_files(Path(argv[0]))
    if not files:
        print(f"no SAVE.DAT in {argv[0]}")
        return 1
    names = {g["index"]: g["name"] for g in knowledge.load()["gvars"]}
    wanted = [knowledge.gvar_index(name) for name in argv[1:]]
    for f in files:
        data = f.read_bytes()
        values = gvars(data)
        print(f"{f.parent.name} ({description(data)})")
        for i in wanted or [i for i, v in enumerate(values) if v]:
            print(f"  {i:3} {names.get(i, '?'):28} {values[i]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
