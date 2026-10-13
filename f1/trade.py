"""A trade stop: at a merchant, buy what the character needs, pay with what it can do without, then sell the rest of
that for the merchant's caps (the rule from Fallout 2's run: every merchant on the way is a chance to turn the pack's
extra weight into Stimpaks and ammunition).

What is wanted, in this order (`wants`): the route's own item, when the stop has one; ammunition for the main gun
(the best way to fight, weapons.choose) up to MAGAZINES of its capacity; Stimpaks up to STIMPAKS. What can go (`spare`):
loot.droppable with the junk, at a cost above 0: weapons past the best two (grenades stay), ammunition no kept gun
takes, armour cheaper than the worn one, the junk past its counts. Keys, disks, tools, drugs, books and quest items
are never offered. The plan is made before the talk: while the barter screen is open the engine takes the worn and
held items out of the inventory (inventry.c setup_inventory), so a read then would miss the gun in the hand.

The deal (inventry.c barter_compute_value, f1.barter): the merchant's goods are worth 100 x cost / mod, his caps
their face; the player's goods their full base cost, caps their face; the deal goes when the player's table is worth
at least the merchant's, and gives no change. So the goods pay first, never more than the price (the least left
over), and caps make up the rest; a sale asks the merchant's caps up to the goods' cost. Caps the route will need
later are kept back (`reserve`): Mrs. Stapleton's records and journals, 750 each.
"""

import time
from dataclasses import dataclass

from f1 import barter, chargen, dialogue, knowledge, loot, quests, session, weapons, win32
from f1 import engine_map as em
from f1.actions import PID_STIMPAK, Actor

PID_CAPS = barter.PID_CAPS
STIMPAKS = 10
MAGAZINES = 6  # of the main gun's capacity
STAPLETON_COST = 750  # STAPLE.INT: the Vault 13 records (Staple06a) and the Chemistry Journals (Staple15a)
PID_HARDENED_POWER_ARMOR, PID_JOURNALS = 232, 237


@dataclass(frozen=True)
class Want:
    pids: tuple[int, ...]  # any of these
    count: int  # carried (ammunition in boxes)


def reserve(actor: Actor) -> int:
    """Caps the route spends later on a fixed price: the records while they are for sale (before the chip's
    hand-in, MASTER_FILLER_10 0), the journals once Miles asked for them (CHEMISTRY_BOOK 1)."""
    out = 0
    if not quests.gvar(actor, "MASTER_FILLER_10") and quests.gvar(actor, "FIND_WATER_CHIP") != 2:
        out += STAPLETON_COST
    if quests.gvar(actor, "CHEMISTRY_BOOK") == 1 and not actor._count(PID_JOURNALS):
        out += STAPLETON_COST
    return out


def main_gun(actor: Actor) -> int | None:
    guns = list(actor.carried_weapons())
    options = weapons.choose(guns, actor.skills(), actor.rules(), actor.max_ap)
    return next((o.pid for o in options if o.pid is not None and weapons.uses_ammo(o.pid)), None)


def boxes_for(gun: int, magazines: int = MAGAZINES) -> tuple[tuple[int, ...], int]:
    """The ammunition that loads `gun`, and how many boxes hold `magazines` of its capacity."""
    w = weapons.weapon(gun) or {}
    capacity = w.get("weapon", {}).get("capacity", 0)
    pids = tuple(
        int(k, 16) for k, p in knowledge.load()["protos"].items() if "ammo" in p and weapons.fits(gun, int(k, 16))
    )
    per_box = max(((knowledge.proto(p) or {}).get("ammo", {}).get("quantity", 1) for p in pids), default=1)
    return pids, -(-capacity * magazines // max(1, per_box))


def wants(actor: Actor, extra: tuple[Want, ...] = ()) -> list[Want]:
    out = list(extra)
    gun = main_gun(actor)
    if gun is not None:
        pids, boxes = boxes_for(gun)
        if pids:
            out.append(Want(pids, boxes))
    out.append(Want((PID_STIMPAK,), STIMPAKS))
    return out


def spare(actor: Actor) -> list[tuple[int, int, int]]:
    """(pid, quantity, cost of one) of what the character can do without and a merchant pays for."""
    out = []
    for _w, pid, qty in loot.droppable(actor, junk=True):
        cost = knowledge.item_cost(pid)
        if cost > 0 and pid != PID_HARDENED_POWER_ARMOR:
            out.append((pid, qty, cost))
    return out


def buys(stock: dict[int, int], have: dict[int, int], want: list[Want], unit_price: dict[int, int], budget: int
         ) -> list[tuple[int, int]]:  # fmt: skip
    """(pid, quantity) to take from the merchant's stock: each want in order, the cheapest unit that fits it first,
    while the budget (the player's caps less the reserve, plus the spare goods' cost) pays."""
    out = []
    for w in want:
        missing = w.count - sum(have.get(p, 0) for p in w.pids)
        for pid in sorted((p for p in w.pids if stock.get(p, 0) > 0), key=lambda p: unit_price.get(p, 0)):
            if missing <= 0:
                break
            each = max(1, unit_price.get(pid, 0))
            n = min(missing, stock[pid], budget // each)
            if n > 0:
                out.append((pid, n))
                budget -= n * each
                missing -= n
    return out


def cover(goods: list[tuple[int, int, int]], need: int) -> list[tuple[int, int]]:
    """(pid, quantity) of goods worth at most `need` (no change is given), the dearest units first."""
    out, left = [], need
    for pid, qty, cost in sorted(goods, key=lambda g: -g[2]):
        n = min(qty, left // cost) if cost > 0 else 0
        if n > 0:
            out.append((pid, n))
            left -= n * cost
    return out


def _player_list(actor: Actor) -> list[tuple[int, int, int]]:
    return barter.inventory(actor.mem, actor.mem.u32(em.GLOBALS["obj_dude"].address))


def _put(actor: Actor, side: str, pid: int, quantity: int, who: int = 0) -> int:
    """Drag `quantity` of `pid` from a list onto its table (side "player" or "merchant"); how many moved."""
    moved = 0
    while moved < quantity:
        items = _player_list(actor) if side == "player" else barter.inventory(actor.mem, who)
        at = next((i for i, (_a, p, _q) in enumerate(items) if p == pid), None)
        if at is None:
            break
        stack = items[at][2]
        part = min(quantity - moved, stack, barter.MOVE_MAX)
        if side == "player":
            slot = barter._scroll_to(actor.mem, at, "stack_offset", "")
            start, end = (barter.PLAYER_LIST, barter.LIST_Y + 48 * (slot or 0)), (barter.PLAYER_TABLE, barter.TABLE_Y)
        else:
            slot = barter._scroll_to(actor.mem, at, "target_stack_offset", "ctrl+")
            start = (barter.MERCHANT_LIST, barter.LIST_Y + 48 * (slot or 0))
            end = (barter.MERCHANT_TABLE, barter.TABLE_Y)
        if slot is None:
            break
        barter._move(actor.mem, start, end, part if stack > 1 else None)
        moved += part
    return moved


def _deal(actor: Actor, done, tries: int = 3, top_up: int = 0) -> bool:
    """M offers; a refusal adds caps (5 % of `top_up` each time) when the player has them."""
    for attempt in range(tries):
        if attempt and top_up:
            _put(actor, "player", PID_CAPS, max(1, top_up // 20))
        session.press("m")
        if win32.wait_for(done, 3, 0.1):
            return True
    return False


def _leave(actor: Actor) -> None:
    session.press("t")  # back to the talk; what is left on the tables goes home
    time.sleep(1.0)
    for _ in range(6):
        d = dialogue.read(actor.mem)
        if not d.active or not d.options:
            break
        i = dialogue.leave(d.options)
        dialogue.choose(actor.mem, i if i is not None else len(d.options) - 1)


def visit(actor: Actor, merchant: str, plan: list[str] | None = None, extra: tuple[Want, ...] = (),
          sell: bool = True) -> dict:  # fmt: skip
    """Buy the wants, paying with spare goods first, then sell the rest of the spare goods for his caps."""
    log = actor.log
    who = quests.find_critter(actor, merchant)
    if who is None:
        return {"ok": False, "why": f"no {merchant} here"}
    want, goods, keep = wants(actor, extra), spare(actor), reserve(actor)
    caps0 = actor._count(PID_CAPS)
    quests.talk(actor, merchant, plan or [], strict=True)
    mem = actor.mem
    if not win32.wait_for(lambda: barter.screen_open(mem), 3, 0.1) and dialogue.read(mem).active:
        session.press("b")
    if not win32.wait_for(lambda: barter.screen_open(mem), 5, 0.1):
        return {"ok": False, "why": "no barter screen"}
    time.sleep(0.5)
    player_barter = chargen.skill_values(mem)["barter"]
    merchant_barter, mod = barter.npc_skill(who.pid, "barter"), mem.glob("barter_mod")
    stock: dict[int, int] = {}
    for _a, p, q in barter.inventory(mem, who.address):
        stock[p] = stock.get(p, 0) + q
    have = {p: actor._count(p) for w in want for p in w.pids}
    unit = {p: barter.price(knowledge.item_cost(p), player_barter, merchant_barter, mod) for p in stock}
    budget = max(0, caps0 - keep) + sum(q * c for _p, q, c in goods)
    picks = buys(stock, have, want, unit, budget)
    out: dict = {"merchant": merchant, "reserve": keep, "caps_before": caps0, "bought": {}, "sold": {}}
    if picks:
        for pid, n in picks:
            _put(actor, "merchant", pid, n, who.address)
        cost = sum(knowledge.item_cost(p) * n for p, n in picks)
        need = barter.price(cost, player_barter, merchant_barter, mod)
        paid = cover(goods, need)
        for pid, n in paid:
            _put(actor, "player", pid, n)
        rest = need - sum(knowledge.item_cost(p) * n for p, n in paid)
        if rest > 0:
            _put(actor, "player", PID_CAPS, rest)
        before = {p: actor._count(p) for p, _ in picks}
        ok = _deal(actor, lambda: any(actor._count(p) > before[p] for p, _ in picks), top_up=need)
        out["bought"] = {knowledge.proto_name(p): actor._count(p) - before[p] for p, _ in picks} if ok else {}
        out["paid_goods"] = {knowledge.proto_name(p): n for p, n in paid} if ok else {}
        goods = [(p, q - dict(paid).get(p, 0), c) for p, q, c in goods if q > dict(paid).get(p, 0)] if ok else goods
        if not ok:
            _leave(actor)
            out.update(ok=False, why="the purchase was refused", caps=actor._count(PID_CAPS))
            log.emit("trade", **out)
            return out
    if sell and goods:
        theirs = sum(q for _a, p, q in barter.inventory(mem, who.address) if p == PID_CAPS)
        sale = cover(goods, theirs)
        if sale:
            for pid, n in sale:
                _put(actor, "player", pid, n)
            ask = sum(knowledge.item_cost(p) * n for p, n in sale)
            caps1 = actor._count(PID_CAPS)
            _put(actor, "merchant", PID_CAPS, ask, who.address)
            if _deal(actor, lambda: actor._count(PID_CAPS) > caps1, tries=1):
                out["sold"] = {knowledge.proto_name(p): n for p, n in sale}
                out["sale_caps"] = actor._count(PID_CAPS) - caps1
        out["merchant_caps"] = theirs
    _leave(actor)
    out.update(ok=True, caps=actor._count(PID_CAPS), spare_lb=loot.spare_weight(actor))
    log.emit("trade", **out)
    return out


def stop(merchant: str, plan: list[str] | None = None, extra: tuple[Want, ...] = ()):
    """A route step: a trade stop at `merchant` (already on his map). Done when the trade ran or he is not here: a
    stop that cannot trade is no reason to stop the route (its step is optional), its event says why."""

    def go(actor: Actor) -> bool:
        from f1.route_support import settle

        actor.holster(True)
        out = visit(actor, merchant, plan, extra)
        settle(actor)
        return out.get("ok", False) or out.get("why", "").startswith("no ")

    return go
