"""Loot: what the map offers that can be taken without consequences, and taking it (a rule: not
collecting such things makes the game needlessly harder). The first cave's Bones (V13ENT tile 17488,
no script) hold a Knife and a box of 10mm AP.

Safe, by the game's files and memory:
- a body: a dead critter's inventory; Fallout 1 has no owners, so nothing reacts;
- an item lying loose, or a container, that runs no script: nothing runs when it is taken;
- anything scripted is left alone. A pickup or use procedure may be a theft, a trap or a quest: Neal's urn on the
  bar runs TROPHY.INT.

Worth taking (`worth`):
- money, every drug, the five skill books, flares;
- ammunition that fits a gun carried or lying with it;
- a weapon that would become the best way to fight (weapons.choose);
- armour dearer than what is worn.

Nothing goes past the carry limit (25 + 25 x ST, stat.c). A body or a container is emptied whole (Actor.loot drags
each item), so it is taken only when something in it is worth it and all of it fits.

    python -m f1.loot            what the running game's floor offers, and why
    python -m f1.loot take       and take it
"""

import json
import sys
import time
from dataclasses import dataclass

from f1 import geometry, knowledge, nav, paths, perception, scripts, state, weapons, world
from f1.engine_map import Obj

PID_MONEY = 0x29  # Bottle Caps
DROPPED_FILE = paths.RUNS / "dropped.json"
PID_FLARES = (0x4F, 0xCD)
BOOKS = (0x49, 0x4C, 0x50, 0x56, 0x66)  # Big Book of Science, Dean's Electronics, First Aid Book, Scout Handbook, Guns
CARRY_BASE, CARRY_PER_ST = 25, 25  # stat.c: STAT_CARRY_WEIGHT
OBJECT_WORN = 0x04000000
BETTER = 1.15  # a weapon is worth carrying when it beats the best way to fight by this much
# Calibers of the guns a character's route will pick up (f1.atlas): their ammunition is worth taking before the gun.
# The Idealist: small energy cells (3) and micro fusion cells (4) for the energy weapons the build is for
# (chargen.IDEALIST). The .223 (5) for the Hunting Rifle on Killian's shelves went with that step (the shelves are
# his stock).
PLANNED_CALIBERS = {"Idealist": frozenset({3, 4})}
_tried: set[tuple[str, int, int]] = set()  # (map, tile, pid) given up on since the last load
# pids lighten() let go: the sweep leaves them (they came back at once, live); kept across runs, since each
# route run is a new process whose first sweep took the dropped armour back
try:
    _dropped: set[int] = set(json.loads(DROPPED_FILE.read_text(encoding="utf-8")))
except (OSError, ValueError):
    _dropped = set()


def forget() -> None:
    """A load put the finds back where the map has them: those given up on are tried again."""
    _tried.clear()


@dataclass(frozen=True)
class Find:
    address: int
    tile: int
    kind: str  # "floor" (a loose item), "container", "body"
    name: str
    items: tuple[tuple[int, int], ...]  # (pid, quantity)
    worth: tuple[str, ...]  # why it is taken; empty: it is not
    weight: int


def inventory(mem, address: int) -> list[tuple[int, int, int]]:
    """(item address, pid, quantity) of an object's inventory (+0x2C length, +0x34 entries of pointer and count)."""
    n = mem.i32(address + Obj.INV_LENGTH)
    if not 0 < n < 500:
        return []
    arr = mem.u32(address + Obj.INV_ITEMS)
    out = []
    for i in range(n):
        item, qty = mem.u32(arr + 8 * i), mem.i32(arr + 8 * i + 4)
        if item:
            out.append((item, mem.u32(item + Obj.PID), qty))
    return out


def weight(pid: int) -> int:
    return (knowledge.proto(pid) or {}).get("weight", 0)


def item_weight(mem, item: int, pid: int, depth: int = 0) -> int:
    """One item's weight as the engine counts it (item.c item_weight): a weapon with its loaded rounds, by the boxes
    they would fill, a container with what is in it. Kyle's Powered Armor was refused ("...maximum weight
    capacity") at 88 lb by the prototypes alone, of 175: the loaded cells were missing."""
    p = knowledge.proto(pid) or {}
    w = p.get("weight", 0) or 0
    if mem is None:
        return w
    if p.get("item_type") == "weapon":
        rounds, ammo = mem.i32(item + Obj.ITEM_AMMO), mem.i32(item + Obj.ITEM_AMMO_PID)
        box = (knowledge.proto(ammo) or {}) if ammo > 0 and rounds > 0 else {}
        per = (box.get("ammo") or {}).get("quantity") or 0
        if per > 0:
            w += (box.get("weight", 0) or 0) * ((rounds - 1) // per + 1)
    elif p.get("item_type") == "container" and depth < 3:
        w += sum(item_weight(mem, i, ip, depth + 1) * q for i, ip, q in inventory(mem, item))
    return w


def carried_weight(actor) -> int:
    """item.c item_total_weight: every item in the inventory, worn and held ones too, times its count."""
    mem = getattr(actor, "mem", None)
    return sum(item_weight(mem, item, pid) * qty for item, pid, qty in actor.items())


STAT_CARRY_WEIGHT = 12


def carry_limit(actor) -> int:
    """The character's carry weight stat, bonuses in (the Powered Armor's ST +3 made it 250, live); the
    formula from base ST when the stat cannot be read."""
    from f1 import chargen

    try:
        base = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS)
        bonus = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS + 35 * 4)
        if base[STAT_CARRY_WEIGHT] + bonus[STAT_CARRY_WEIGHT] > 0:
            return base[STAT_CARRY_WEIGHT] + bonus[STAT_CARRY_WEIGHT]
    except (AttributeError, TypeError, ValueError, OSError):  # a fake actor in tests has no memory: the formula
        return CARRY_BASE + CARRY_PER_ST * actor.rules().strength
    return CARRY_BASE + CARRY_PER_ST * actor.rules().strength


@dataclass(frozen=True)
class Wants:
    """What the character has, against which a find is judged."""

    guns: tuple[weapons.Carried, ...]
    best: float  # the best way to fight's expected damage a turn
    skills: dict
    rules: weapons.Rules
    max_ap: int
    worn_cost: int
    have: frozenset[int]  # pids carried
    ammo: tuple[tuple[int, int], ...] = ()  # (pid, rounds) carried
    calibers: frozenset[int] = frozenset()  # of guns the route will pick up (PLANNED_CALIBERS)


def rounds_in(pid: int, boxes: int, top: int | None = None) -> int:
    """Rounds in a stack of ammunition boxes: the top one holds `top` (the object's count), the others are full."""
    full = (knowledge.proto(pid) or {}).get("ammo", {}).get("quantity", 0)
    return (boxes - 1) * full + (full if top is None else top) if boxes > 0 else 0


def wants(actor) -> Wants:
    guns = tuple(actor.carried_weapons())
    skills, rules, max_ap = actor.skills(), actor.rules(), actor.max_ap
    best = max((o.score for o in weapons.choose(list(guns), skills, rules, max_ap)), default=0.0)
    items = actor.items()
    worn = [pid for item, pid, _ in items if actor.mem.u32(item + Obj.FLAGS) & OBJECT_WORN]
    worn_cost = max(((knowledge.proto(p) or {}).get("cost", 0) for p in worn), default=0)
    ammo = tuple(
        (pid, rounds_in(pid, qty, actor.mem.i32(item + Obj.ITEM_AMMO)))
        for item, pid, qty in items
        if "ammo" in (knowledge.proto(pid) or {})
    )
    planned = PLANNED_CALIBERS.get(state.character_name(actor.mem), frozenset())
    return Wants(guns, best, skills, rules, max_ap, worn_cost, frozenset(p for _, p, _ in items), ammo, planned)


def worth(pid: int, w: Wants, beside: tuple[int, ...] = ()) -> str | None:
    """Why item `pid` is worth taking, or None. `beside`: the pids lying with it (a gun and its ammunition)."""
    p = knowledge.proto(pid) or {}
    kind = p.get("item_type")
    if pid in _dropped:
        return None
    if pid == PID_MONEY:
        return "money"
    if kind == "drug":
        return "medicine"
    if pid in BOOKS:
        return "book"
    if pid in PID_FLARES:
        return "light"
    if kind == "ammo":
        guns = [g.pid for g in w.guns if weapons.uses_ammo(g.pid)] + [b for b in beside if weapons.uses_ammo(b)]
        if any(weapons.fits(g, pid) for g in guns):
            return "ammo"
        return "ammo for later" if p.get("ammo", {}).get("caliber") in w.calibers else None
    if kind == "weapon" and pid not in w.have:
        rounds = sum(rounds_in(a, 1) for a in beside if weapons.fits(pid, a))
        rounds += sum(n for a, n in w.ammo if weapons.fits(pid, a))
        option = weapons.option(weapons.Carried(pid, 0, -1, rounds), w.skills, w.rules, w.max_ap)
        return "weapon" if option is not None and option.score > BETTER * w.best else None
    if kind == "armor" and pid not in w.have and p.get("cost", 0) > w.worn_cost:
        return "armour"
    return None


KEEP_GUNS = 2  # the best ways to fight kept with their ammunition when the load is lightened
# What goes when weapons, ammunition and armour were not enough (the Powered Armor weighs 85 lb): food and drink, the
# water flasks, a second Geiger Counter, the Motion Sensor, flares past four, Tools past one: pid -> how many stay.
JUNK = {126: 0, 106: 0, 103: 0, 71: 0, 124: 0, 52: 1, 59: 0, 79: 4, 75: 1}
ITEM_ACTION_USE, ITEM_ACTION_USE_ON = 0x0800, 0x1000  # proto flags_ext: either puts Use in the item's menu


def drop_entry(pid: int) -> int:
    """The Drop entry of the item's menu (inven_action_cursor: Look, [Use], Drop, ...): Use is there for drugs and
    for items flagged Use or Use On (a Water Flask, 0xb000, showed Use: its Drop is the second entry, live;
    at the first it did nothing)."""
    p = knowledge.proto(pid) or {}
    use = p.get("item_type") == "drug" or p.get("flags_ext", 0) & (ITEM_ACTION_USE | ITEM_ACTION_USE_ON)
    return 2 if use else 1


MOVE_ITEMS_WINDOW = (259, 162)  # the how-many window over the inventory (inventry.c setup_move_timer_win, live)
WEIGHT_SLACK = 2  # lb kept spare past the need: the engine's own count is used now (Total Wt read 113 where the
# prototypes alone said 105: a gun's loaded rounds, item_weight)


def spare_weight(actor) -> int:
    return carry_limit(actor) - carried_weight(actor)


def droppable(actor, coming: int = 0, junk: bool = False) -> list[tuple[int, int, int]]:
    """(weight, pid, quantity) of what can go, heaviest first: weapons past the best KEEP_GUNS (grenades stay),
    ammunition none of those takes, armour not worn. Nothing worn or in a hand, nothing else (keys, disks, tools,
    drugs, books and quest items stay). `coming`: the cost of armour about to be put on (cheaper armour may go);
    `junk`: JUNK too, after the rest."""
    from f1.actions import ITEM_EQUIPPED

    w = wants(actor)
    kept = [o.pid for o in weapons.choose(list(w.guns), w.skills, w.rules, w.max_ap) if o.pid is not None][:KEEP_GUNS]
    out = []
    for item, pid, qty in actor.items():
        if actor.mem.u32(item + Obj.FLAGS) & ITEM_EQUIPPED:
            continue
        p = knowledge.proto(pid) or {}
        kind = p.get("item_type")
        if kind == "weapon":
            if pid in kept or weapons.attack_type(pid) == "throw":
                continue
        elif kind == "ammo":
            if any(weapons.fits(g, pid) for g in kept) or p.get("ammo", {}).get("caliber") in w.calibers:
                continue
        elif kind != "armor" or p.get("cost", 0) > max(w.worn_cost, coming):
            continue  # armour better than what is worn is about to be worn (the Glow's Combat Armor went, live)
        out.append((weight(pid) * qty, pid, qty))
    out.sort(reverse=True)
    if junk:
        extra = []
        for pid, keep in JUNK.items():
            n = actor._count(pid) - keep
            if n > 0:
                extra.append((weight(pid) * n, pid, n))
        out += sorted(extra, reverse=True)
    return out


def lighten(actor, free: int, coming: int = 0) -> dict:
    """Drop what is not needed (`droppable`) until `free` pounds can be picked up: the route's own finds (a key, a
    disk, the Glow's armour and rifle) were refused at 169 of 175 lb ("You cannot pick that up. You are at your
    maximum weight capacity.", live). Each drop goes through the item's menu in the inventory screen
    (Look, Drop, ... for what has no Use: weapons, ammunition, armour); a stack asks how many."""
    dropped = []
    for _w, pid, qty in droppable(actor, coming, junk=True):
        if spare_weight(actor) >= free + WEIGHT_SLACK:
            break
        had = actor._count(pid)
        drop(actor, pid, qty)
        dropped.append((knowledge.proto_name(pid), had - actor._count(pid)))
        _dropped.add(pid)  # not swept up again (the bunker's flares came back on the way out, live)
    try:
        DROPPED_FILE.write_text(json.dumps(sorted(_dropped)), encoding="utf-8")
    except OSError:
        pass
    out = {"dropped": dropped, "spare": spare_weight(actor), "limit": carry_limit(actor)}
    actor.log.emit("lighten", free=free, **out)
    return out


def move_items_up(actor) -> bool:
    """The how-many window over the inventory is up."""
    return any(w[4:] == MOVE_ITEMS_WINDOW for w in state.live_windows(actor.mem))


def drop(actor, pid: int, qty: int) -> int:
    """Drop `qty` of `pid` through its item menu; how many went. A stack asks how many in a Move Items window
    (259 x 162): it can come later than 1.5 s, and one left open held the Glow's Level 5 until the watchdog's loads
    (the chain): waited for longer, and whenever it is up, answered (the count typed, Enter)."""
    from f1 import session, win32

    asks = lambda: move_items_up(actor)
    had = actor._count(pid)
    gone = lambda: actor._count(pid) < had
    why = actor.item_menu(pid, drop_entry(pid))
    if why is None and (qty > 1 or asks()):
        win32.wait_for(lambda: asks() or gone(), 4, 0.05)
    if why is None and asks():
        time.sleep(0.2)
        session.type_text(str(min(max(qty, 1), 999)))
        session.press("enter")
        win32.wait_for(lambda: not asks(), 2, 0.05)
    win32.wait_for(gone, 2, 0.05)
    if asks():  # still up: Escape cancels it, so the inventory below can close
        session.press("esc")
        win32.wait_for(lambda: not asks(), 2, 0.05)
    actor.close_inventory()
    return had - actor._count(pid)


def finds(actor, w: Wants | None = None, town_containers: bool = False) -> list[Find]:
    """What this floor offers: bodies, and loose items and containers that run no script (in a town, containers
    only when a route step names one: `town_containers`)."""
    mem, s = actor.mem, actor.snap()
    w = w or wants(actor)
    out = []
    from f1 import routes

    # In towns a container is someone's: a Bookshelf in Killian's store opened his barter as a theft, and he and a
    # guard attacked (Junktown). There the sweep takes bodies and loose items only; a town container the
    # route wants is its own step (idealist.take_from).
    town = any(m in s.map_name for m in routes.TOWN_MAPS)
    for t in world.things(mem):
        if t.elevation != s.elevation or s.dude is None or t.address == s.dude.address or t.tile <= 0:
            continue  # tile 0: a map's store for what scripts hand out (f1.atlas)
        if t.type == "critter":
            if not t.dead:
                continue
            kind, items = "body", [(pid, q) for _, pid, q in inventory(mem, t.address)]
        elif t.type == "item":
            container = (knowledge.proto(t.pid) or {}).get("item_type") == "container"
            if container and town and not town_containers:
                continue
            kind = "container" if container else "floor"
            items = [(pid, q) for _, pid, q in inventory(mem, t.address)] if container else [(t.pid, 1)]
        else:
            continue
        if not items:
            continue
        pids = tuple(pid for pid, _ in items)
        why = tuple(dict.fromkeys(r for pid in pids if (r := worth(pid, w, pids))))
        if why and t.type == "item" and scripts.script_of(mem, t.address):  # looked up only for what is wanted
            continue
        out.append(Find(t.address, t.tile, kind, t.name, tuple(items), why, sum(weight(p) * q for p, q in items)))
    return out


def _key(actor, f: Find) -> tuple[str, int, int]:
    return actor.snap().map_name, f.tile, f.items[0][0] if f.kind == "floor" else -f.address


TAKEN, FAILED, NO_WAY = "taken", "failed", "no way"


def take(actor, f: Find, watched: frozenset[int] = frozenset()) -> str:
    """A loose item picked up; a body or a container emptied: TAKEN, FAILED, or NO_WAY when no path keeps out of the
    hexes foes watch (`watched`, f1.perception) for now. The way there keeps out of them as far as the last hexes,
    which the engine walks by itself for the click; a body is clicked from near it (the Agent's own sprite over it
    takes the click, quests.loot_the_dead)."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    near = (3, 4) if f.kind == "body" else (1, 2)
    goals = {t for r in near for t in geometry.ring(f.tile, r) if t not in obs.blocked and t not in watched}
    if not goals:
        return NO_WAY
    if geometry.distance(f.tile, s.dude.tile) > max(near):
        reached, _why = nav.go_to(actor, goals, max_steps=60, fight=True, avoid=watched)
        if not reached:
            return NO_WAY if watched else FAILED
    if f.kind == "floor":
        return TAKEN if actor.pick_up(f.tile, f.items[0][0]).ok else FAILED
    return TAKEN if actor.loot(f.tile).ok else FAILED


def danger(actor) -> frozenset[int]:
    """The hexes foes would notice the player on (f1.perception), outside towns: a town's dogs and people do not start
    fights, and their notice blocked Shady Sands' .223 bookcase (no path round the dogs)."""
    from f1 import routes

    if any(m in actor.snap().map_name for m in routes.TOWN_MAPS):
        return frozenset()
    return perception.zone(actor.enemies())


def sweep(actor, max_hexes: int = 200, budget_s: float = 150.0) -> dict:
    """Take what this floor offers that is worth it, nearest first, within `max_hexes` of the Agent (by default the
    whole floor: items lie where the map file put them, f1.atlas) and `budget_s` seconds: not in a fight, nothing
    where a foe would notice the player and no way through there (f1.perception), nothing past the carry limit, each
    find given up once it failed (a find taken is gone from the floor anyway; a reload brings it back, to be taken
    again)."""
    taken: list[str] = []
    later: set[tuple[str, int, int]] = set()  # no way round the foes' notice now: another sweep may find one
    t0 = time.monotonic()
    while time.monotonic() - t0 < budget_s:
        s = actor.snap()
        if s.screen != "map" or s.dude is None or actor.in_combat():
            break
        watched = danger(actor)
        room = carry_limit(actor) - carried_weight(actor)
        todo = [
            f
            for f in finds(actor)
            if f.worth
            and f.weight <= room
            and _key(actor, f) not in _tried
            and _key(actor, f) not in later
            and geometry.distance(f.tile, s.dude.tile) <= max_hexes
            and f.tile not in watched
        ]
        if not todo:
            break
        f = min(todo, key=lambda f: geometry.distance(f.tile, s.dude.tile))
        status = take(actor, f, watched)
        if status == FAILED:
            _tried.add(_key(actor, f))
        elif status == NO_WAY:
            later.add(_key(actor, f))
        actor.log.emit(
            "loot", find=f.kind, name=f.name, tile=f.tile, worth=list(f.worth), ok=status == TAKEN, status=status,
            items=[(knowledge.proto_name(p), q) for p, q in f.items],
        )  # fmt: skip
        if status == TAKEN:
            taken.append(f.name)
    return {"taken": taken, "seconds": round(time.monotonic() - t0, 1)}


def main(argv: list[str]) -> int:
    from f1 import session
    from f1.actions import Actor
    from f1.telemetry import EventLog

    actor = Actor(session.game_pid(), EventLog(paths.RUNS / "loot" / "events.jsonl"))
    try:
        w = wants(actor)
        print(f"carried {carried_weight(actor)} of {carry_limit(actor)} lbs; best way to fight scores {w.best:.1f}")
        s = actor.snap()
        for f in sorted(finds(actor, w), key=lambda f: geometry.distance(f.tile, s.dude.tile)):
            items = ", ".join(f"{knowledge.proto_name(p)} x{q}" for p, q in f.items)
            print(f"{f.kind:9} {f.name:20} tile {f.tile} ({geometry.distance(f.tile, s.dude.tile)} hexes), "
                  f"{f.weight} lbs: {items} -> {', '.join(f.worth) or 'nothing worth it'}")  # fmt: skip
        if argv[:1] == ["take"]:
            print(sweep(actor))
    finally:
        actor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
