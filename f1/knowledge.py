"""Knowledge extracted from the game's own files into a local database (extracted/f1/knowledge.json, not in git).

    python -m f1.knowledge build        read the instance's DATs and DATA\\ patches, write the database
    python -m f1.knowledge name PID     a prototype's name, e.g. 0x01000030

v1 holds: every prototype (items, critters, scenery, walls, tiles, misc) with its name and description, and the
global variables' names in GVAR order. Facts come from the files: a pid's index N is line N of its type's .LST file
(PROTO\\ITEMS\\ITEMS.LST ...), which names the .PRO file; the .PRO's second big-endian int is its message number in
TEXT\\ENGLISH\\GAME\\PRO_*.MSG (name at that number, description at number + 1).
"""

import json
import re
import struct
import sys
from functools import cache
from pathlib import Path

from f1 import paths
from f1.dat import GameFiles

INSTANCE = paths.INSTANCE
DB_PATH = paths.EXTRACTED / "knowledge.json"

PROTO_TYPES = {  # pid >> 24: (folder and list name, message file)
    0: ("ITEMS", "PRO_ITEM"),
    1: ("CRITTERS", "PRO_CRIT"),
    2: ("SCENERY", "PRO_SCEN"),
    3: ("WALLS", "PRO_WALL"),
    4: ("TILES", "PRO_TILE"),
    5: ("MISC", "PRO_MISC"),
}
TYPE_NAMES = {0: "item", 1: "critter", 2: "scenery", 3: "wall", 4: "tile", 5: "misc"}


def parse_msg(data: bytes) -> dict[int, str]:
    """A .MSG file: entries {number}{sound}{text}; text may span lines. Latin-1, CRLF."""
    text = data.decode("latin1")
    return {
        int(m.group(1)): m.group(2).replace("\r\n", "\n") for m in re.finditer(r"\{(\d+)\}\{[^}]*\}\{([^}]*)\}", text)
    }


def parse_gvars(data: bytes) -> list[dict]:
    """DATA\\VAULT13.GAM: after GAME_GLOBAL_VARS:, one `NAME :=value;` per GVAR, in order. Two of the 618 lines lack
    the ";" (BAD_MONSTER, MARK_SHADY_1) and still count, as in the engine (it holds 618 GVARs)."""
    out = []
    started = False
    for line in data.decode("latin1").splitlines():
        stripped = line.strip()
        if stripped.startswith("GAME_GLOBAL_VARS:"):
            started = True
            continue
        if not started or stripped.startswith("//") or not stripped:
            continue
        m = re.match(r"(\w+)\s*:=\s*(-?\d+)\s*;?\s*(?://(.*))?", stripped)
        if m:
            out.append(
                {"index": len(out), "name": m.group(1), "initial": int(m.group(2)), "note": (m.group(3) or "").strip()}
            )
    return out


ITEM_TYPES = {0: "armor", 1: "container", 2: "drug", 3: "weapon", 4: "ammo", 5: "misc", 6: "key"}
SCENERY_KINDS = {0: "door", 1: "stairs", 2: "elevator", 3: "ladder up", 4: "ladder down", 5: "generic"}
# An item proto (big-endian ints): pid, message, fid, light distance and intensity, flags, extended flags (+24: the
# primary attack mode in the low nibble, the secondary in the next, 0x100 big gun, 0x200 two-handed), script, type
# (+32), material, size, weight, cost (+48), inventory fid, a sound byte (+56); the type's data follows at +57.
# Checked: all 43 weapon files are 122 bytes and all 16 ammo files 81; the 10mm Pistol reads AP 5 (the interface bar
# showed "AP 5" live), 25 hexes, ammo 10mm JHP (pid 0x1D, the box the Overseer gives) in boxes of 24.
ITEM_DATA = 57
WEAPON_FIELDS = (
    "anim", "min_damage", "max_damage", "damage_type", "range1", "range2", "projectile", "min_st", "ap1", "ap2",
    "crit_fail", "perk", "burst", "caliber", "ammo_pid", "capacity",
)  # fmt: skip
AMMO_FIELDS = ("caliber", "quantity", "ac_mod", "dr_mod", "damage_mult", "damage_div")
# A critter proto file (big-endian ints): the common eight, head fid, AI packet, team, flags, then baseStats[35]
# (index 12), bonusStats[35] (47), skills[18] (82). Checked: the radscorpion's 34 max HP - 8 = the 26 measured live.
CRITTER_BASE, CRITTER_BONUS, CRITTER_SKILLS = 12, 47, 82


def item_cost(pid: int) -> int:
    return load()["protos"].get(f"0x{pid & 0xFFFFFFFF:08X}", {}).get("cost", 0)


def build(game_dir: Path = INSTANCE) -> dict:
    files = GameFiles(game_dir)
    protos: dict[str, dict] = {}
    for ptype, (folder, msgname) in PROTO_TYPES.items():
        msgs = parse_msg(files.read(f"TEXT/ENGLISH/GAME/{msgname}.MSG"))
        listing = files.read(f"PROTO/{folder}/{folder}.LST").decode("latin1").split()
        for index, fname in enumerate(listing, start=1):
            try:
                raw = files.read(f"PROTO/{folder}/{fname}")
            except KeyError:
                continue
            pid, message = struct.unpack(">ii", raw[:8])
            if pid != (ptype << 24) | index:
                continue  # a list line and its file disagree: leave it out rather than guess
            entry = {
                "type": TYPE_NAMES[ptype],
                "name": msgs.get(message, ""),
                "description": msgs.get(message + 1, ""),
                "file": f"PROTO/{folder}/{fname}",
            }
            if ptype == 0 and len(raw) >= 52:  # an item: its kind at +32, its base price at +48 (Killian's goods)
                kind, cost = struct.unpack(">i", raw[32:36])[0], struct.unpack(">i", raw[48:52])[0]
                entry |= {"item_type": ITEM_TYPES.get(kind, str(kind)), "cost": cost}
                entry |= {"flags_ext": struct.unpack(">I", raw[24:28])[0], "weight": struct.unpack(">i", raw[44:48])[0]}
                for fields, name, want in ((WEAPON_FIELDS, "weapon", 3), (AMMO_FIELDS, "ammo", 4)):
                    if kind == want and len(raw) >= ITEM_DATA + 4 * len(fields):
                        values = struct.unpack(f">{len(fields)}i", raw[ITEM_DATA : ITEM_DATA + 4 * len(fields)])
                        entry[name] = dict(zip(fields, values, strict=True))
            if ptype == 2 and len(raw) >= 36:  # scenery: its kind at +32 (a curtain is a door; f1/FORMATS.md)
                entry["kind"] = SCENERY_KINDS.get(struct.unpack(">i", raw[32:36])[0], "generic")
            if ptype == 1 and len(raw) >= 4 * (CRITTER_SKILLS + 18):  # a critter: stats, bonuses, skill points
                ints = struct.unpack(f">{CRITTER_SKILLS + 18}i", raw[: 4 * (CRITTER_SKILLS + 18)])
                base, bonus = ints[CRITTER_BASE : CRITTER_BASE + 35], ints[CRITTER_BONUS : CRITTER_BONUS + 35]
                entry |= {
                    "special": [b + x for b, x in zip(base[:7], bonus[:7], strict=True)],
                    "hp": base[7] + bonus[7],
                    "skill_points": list(ints[CRITTER_SKILLS : CRITTER_SKILLS + 18]),
                    # all 35 (stat.h): 8 AP, 9 AC, 11 melee damage, 13 sequence, 15 critical chance, 17-23 DT and
                    # 24-30 DR by damage type (normal first)
                    "stats": [b + x for b, x in zip(base, bonus, strict=True)],
                }
            protos[f"0x{pid & 0xFFFFFFFF:08X}"] = entry
    gvars = parse_gvars(files.read("DATA/VAULT13.GAM"))
    # SCRIPTS.LST: line i is script index i ("Killian.int ; Killian, law enforcement in Junktown"): the name only
    scripts = [
        line.split(".")[0].strip().lower() for line in files.read("SCRIPTS/SCRIPTS.LST").decode("latin1").splitlines()
    ]
    # MAP.MSG: message N is map index N's file (exit grids and the engine refer to maps by index), N + 100 its town.
    map_msgs = parse_msg(files.read("TEXT/ENGLISH/GAME/MAP.MSG"))
    maps = {str(n): {"file": f, "place": map_msgs.get(n + 100, "")} for n, f in map_msgs.items() if n < 100}
    return {"source": str(game_dir), "protos": protos, "gvars": gvars, "maps": maps, "scripts": scripts}


@cache
def load() -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH} missing: run python -m f1.knowledge build")
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def proto(pid: int) -> dict | None:
    return load()["protos"].get(f"0x{pid & 0xFFFFFFFF:08X}")


def proto_name(pid: int) -> str:
    entry = proto(pid)
    return entry["name"] if entry else f"pid {pid & 0xFFFFFFFF:#010x}"


def gvar_index(name: str) -> int:
    """A global variable's number by its VAULT13.GAM name (e.g. "RESCUE_TANDI" -> 103)."""
    for entry in load().get("gvars", []):
        if entry["name"] == name:
            return entry["index"]
    raise KeyError(f"no GVAR {name}")


def map_name(index: int) -> str:
    """The map file for a map index; exit grids lead out with -1 (the world map) or -2 (the town's map)."""
    if index in (-1, -2):
        return "WORLD MAP" if index == -1 else "TOWN MAP"
    entry = load().get("maps", {}).get(str(index))
    return entry["file"] if entry else f"map {index}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["build"]:
        db = build()
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH.write_text(json.dumps(db, indent=1, ensure_ascii=False), encoding="utf-8")
        by_type: dict[str, int] = {}
        for p in db["protos"].values():
            by_type[p["type"]] = by_type.get(p["type"], 0) + 1
        print(
            f"wrote {DB_PATH}: {len(db['protos'])} prototypes {by_type}, {len(db['gvars'])} global variables, "
            f"{len(db['maps'])} maps"
        )
        return 0
    if argv[:1] == ["name"] and len(argv) == 2:
        print(proto_name(int(argv[1], 0)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
