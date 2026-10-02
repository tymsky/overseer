"""The current map's objects, named from the knowledge database: what is where.

python -m f1.world [TYPE ...]     dump the map's objects (types: item critter scenery wall tile misc), nearest first
python -m f1.world stock          what each critter here carries, with base prices (the merchants' goods)
"""

import sys
from dataclasses import dataclass

from f1 import knowledge, session, state
from f1.engine_map import Obj
from f1.memory import GameMemory

TYPES = {0: "item", 1: "critter", 2: "scenery", 3: "wall", 4: "tile", 5: "misc"}


@dataclass(frozen=True)
class Thing:
    address: int
    pid: int
    type: str
    name: str
    tile: int
    elevation: int
    flags: int
    hp: int | None = None
    team: int | None = None
    dead: bool | None = None


def things(mem: GameMemory) -> list[Thing]:
    out = []
    for addr in state.objects(mem):
        raw = mem.read(addr, Obj.SIZE)

        def i32(off: int, raw: bytes = raw) -> int:
            return int.from_bytes(raw[off : off + 4], "little", signed=True)

        pid = i32(Obj.PID) & 0xFFFFFFFF
        kind = TYPES.get(pid >> 24, "system")
        extra = {}
        if kind == "critter":
            extra = {
                "hp": i32(Obj.CRITTER_HP),
                "team": i32(Obj.CRITTER_TEAM),
                "dead": bool(i32(Obj.CRITTER_RESULTS) & 0x80),
            }
        name = "the player" if pid == 0x01000000 else knowledge.proto_name(pid) if kind != "system" else "system object"
        out.append(
            Thing(addr, pid, kind, name, i32(Obj.TILE), i32(Obj.ELEVATION), i32(Obj.FLAGS) & 0xFFFFFFFF, **extra)
        )
    return out


def holders_of(mem: GameMemory, pid: int) -> list[tuple[Thing, int]]:
    """Objects on this map whose inventory holds item `pid` (containers, critters, the player), with the count.
    An inventory: length at +0x2C, items at +0x34 (8 bytes each: item pointer, quantity)."""
    out = []
    for t in things(mem):
        n = mem.i32(t.address + 0x2C)
        if not 0 < n < 500:
            continue
        arr = mem.u32(t.address + 0x34)
        for i in range(n):
            item, qty = mem.u32(arr + 8 * i), mem.i32(arr + 8 * i + 4)
            if item and mem.u32(item + Obj.PID) == pid:
                out.append((t, qty))
    return out


def stock(mem: GameMemory) -> list[tuple[Thing, list[tuple[str, int, int]]]]:
    """What each live critter on this floor carries (the merchants' goods): (name, quantity, base price) per item."""
    snap = state.read(mem)
    out = []
    for t in things(mem):
        if t.type != "critter" or t.dead or t.elevation != snap.elevation or t.pid == 0x01000000:
            continue
        n = mem.i32(t.address + 0x2C)
        if not 0 < n < 500:
            continue
        arr = mem.u32(t.address + 0x34)
        goods = []
        for i in range(n):
            item, qty = mem.u32(arr + 8 * i), mem.i32(arr + 8 * i + 4)
            pid = mem.u32(item + Obj.PID) if item else 0
            goods.append((knowledge.proto_name(pid), qty, knowledge.item_cost(pid)))
        out.append((t, goods))
    return out


def main(argv: list[str]) -> int:
    pid = session.game_pid()
    if not pid:
        print("the game is not running")
        return 2
    if argv == ["stock"]:
        with GameMemory(pid) as mem:
            for t, goods in sorted(stock(mem), key=lambda x: -sum(q * c for _, q, c in x[1])):
                worth = sum(q * c for name, q, c in goods if name != "Bottle Caps")
                print(f"{t.name} (tile {t.tile}): goods worth {worth}")
                for name, qty, cost in goods:
                    print(f"    {name:<28} x{qty:<5} price {cost}")
        return 0
    from f1 import scripts

    wanted = set(argv) or {"critter", "item", "scenery", "misc"}
    with GameMemory(pid) as mem:
        snap = state.read(mem)
        items = [t for t in things(mem) if t.type in wanted and t.elevation == snap.elevation]
        who = {t.address: scripts.script_of(mem, t.address) for t in items if t.type in ("critter", "scenery")}
    here = snap.dude.tile if snap.dude else 0
    items.sort(key=lambda t: abs(t.tile % 200 - here % 200) + abs(t.tile // 200 - here // 200))
    print(f"{snap.map_name} elevation {snap.elevation}: {len(items)} objects of {sorted(wanted)}")
    for t in items[:60]:
        extra = f" hp {t.hp} team {t.team}{' dead' if t.dead else ''}" if t.type == "critter" else ""
        script = f" [{who[t.address]}]" if who.get(t.address) else ""
        print(f"  {t.type:<8} {t.name:<28} tile {t.tile:>6}{extra}{script}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
