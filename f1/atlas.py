"""The loot atlas: every item a map places and where it lies, read from the map files, for planning the routes'
collecting (items are always in the same places, so collecting is planned in the steps, towns and other
places too, not only caves).

A map's objects are fixed until the player changes them; the map's .SAV keeps the state after a visit. The
radscorpion caves' file holds the four finds the hand run picked up live: 10mm AP at 20257, two boxes of 10mm JHP at
30124 and 30144, and a Stimpak at 20456.

What is not in a map file:
- random encounters' critters and their things;
- what scripts create: rewards, merchants' stock;
- what critters carry. That is theirs, and taken from bodies after a fight (f1.loot).

A spot is free when it runs no script (a pickup or use procedure may be a theft, a trap or a quest) and a container is
not locked (object_types.h CONTAINER_FLAG_LOCKED 0x02000000). What lies behind a locked or scripted door shows only
live: its path does not open.

    python -m f1.atlas MAP [ELEVATION]      one map's loot, free or not
    python -m f1.atlas all                  every map's free loot, by place, in the values the item protos give
"""

import sys
from dataclasses import dataclass
from functools import cache

from f1 import knowledge, mapfile

CONTAINER_LOCKED = 0x02000000
KINDS_OF_WORTH = (
    "ammo",
    "drug",
    "weapon",
    "armor",
)  # what the atlas counts as worth planning for, with money and books
PID_MONEY = 0x29
BOOKS = frozenset({0x49, 0x4C, 0x50, 0x56, 0x66})


@dataclass(frozen=True)
class Spot:
    map: str
    elevation: int
    tile: int
    kind: str  # "floor" or "container"
    name: str
    pid: int
    items: tuple[tuple[int, int], ...]  # (pid, count): a floor item is itself
    script: str
    locked: bool

    @property
    def stored(self) -> bool:
        """Tile 0, off the grid's playable part: where maps keep what scripts hand out (the Hub's old town keeps three
        Flamers there, the Gun Runners a Powered Armor)."""
        return self.tile <= 0

    @property
    def free(self) -> bool:
        return not self.script and not self.locked and not self.stored

    @property
    def value(self) -> int:
        """The items' base prices (the protos' cost), a rough measure of what the spot is worth."""
        return sum(knowledge.item_cost(p) * n for p, n in self.items)


def worth_planning(pid: int) -> bool:
    p = knowledge.proto(pid) or {}
    return pid == PID_MONEY or pid in BOOKS or p.get("item_type") in KINDS_OF_WORTH


@cache
def spots(map_name: str) -> tuple[Spot, ...]:
    m = mapfile.read(map_name)
    out = []
    for o in m.objects:
        if (knowledge.proto(o.pid) or {}).get("type") != "item":
            continue
        if o.kind == "container":
            items = tuple((i.pid, n) for n, i in o.inventory)
            if not items:
                continue
            locked = bool(o.data.get("flags", 0) & CONTAINER_LOCKED)
            out.append(
                Spot(m.name or map_name, o.elevation, o.tile, "container", o.name, o.pid, items, o.script, locked)
            )
        else:
            out.append(
                Spot(m.name or map_name, o.elevation, o.tile, "floor", o.name, o.pid, ((o.pid, 1),), o.script, False)
            )
    return tuple(out)


def map_files() -> list[str]:
    return [e["file"].removesuffix(".MAP") for e in knowledge.load().get("maps", {}).values()]


def main(argv: list[str]) -> int:
    if argv == ["all"]:
        by_place: dict[str, list[str]] = {}
        for name in map_files():
            try:
                found = [s for s in spots(name) if s.free and any(worth_planning(p) for p, _ in s.items)]
            except (KeyError, ValueError):
                continue
            if found:
                place = next((e["place"] for e in knowledge.load()["maps"].values() if e["file"] == f"{name}.MAP"), "")
                by_place.setdefault(place, []).append(f"{name} {len(found)} spots, {sum(s.value for s in found)}$")
        for place, lines in by_place.items():
            print(f"{place}: " + "; ".join(lines))
        return 0
    if not argv:
        print(__doc__)
        return 2
    elevation = int(argv[1]) if len(argv) > 1 else None
    for s in spots(argv[0]):
        if elevation is not None and s.elevation != elevation:
            continue
        what = ", ".join(f"{knowledge.proto_name(p)}{'' if n == 1 else f' x{n}'}" for p, n in s.items)
        flags = (("free", s.free), (f"script {s.script}", bool(s.script)), ("locked", s.locked), ("stored", s.stored))
        tags = [t for t, on in flags if on]
        print(f"e{s.elevation} {s.tile:5} {s.kind:9} {s.name:18} {what} [{', '.join(tags)}] {s.value}$")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
