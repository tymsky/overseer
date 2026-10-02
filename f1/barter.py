"""Buying with caps, the way inventry.c's barter screen works (Fallout 1 CE, 0x467AD0 barter_compute_value).

The screen is a 480 x 180 panel (at (80, 290) at 640x480, at (720, 540) at 1920x1080 where HRP centres the dialogue's
window) with four columns of three 64 x 48 slots, centres from the panel's corner: the player's list (x 61), the
player's table (x 197), the merchant's table (x 282), the merchant's list (x 420); list slots at y 54 + 48 i, table
slots at y 44 + 48 i. Lists show inventory items in order from their scroll: the player's with Up/Down
(`stack_offset[0]`), the merchant's with Ctrl+Up/Down (`target_stack_offset[0]`). An item is dragged from a list
onto a table; a stack asks how many (digits up to 999, Enter). M offers: the deal goes when the player's table is
worth at least the merchant's (goods at 100 x cost / mod, mod = 100 + the player's Barter - the merchant's + the
talk's barter modifier, clamped to 10..300; caps at 1) and gives no change. T goes back to the talk and returns
what is left on the tables.

    python -m f1.barter NAME "plan|phrases" PID [QTY]     buy from the merchant called NAME (e.g. Killian Darkwater);
                                                          an empty plan ("") opens trade by the dialogue's B key
"""

import math
import sys
import time

from f1 import chargen, dialogue, knowledge, paths, quests, session, state, win32
from f1 import engine_map as em
from f1.actions import Actor
from f1.memory import GameMemory
from f1.telemetry import EventLog

WINDOW_SIZE = (480, 180)  # the barter panel, told by its size: its place moves with the resolution
PLAYER_LIST, PLAYER_TABLE, MERCHANT_TABLE, MERCHANT_LIST = 61, 197, 282, 420  # from the panel's corner
LIST_Y, TABLE_Y, SLOTS = 54, 44, 3
PID_CAPS = 0x29
MOVE_MAX = 999  # the quantity window's limit


def npc_skill(pid: int, skill: str) -> int:
    """skill.c skill_level for a critter that is not the player: default + stat bonus + points (no tags, no traits),
    from its proto file (knowledge)."""
    proto = knowledge.load()["protos"].get(f"0x{pid & 0xFFFFFFFF:08X}", {})
    special, points = proto.get("special", [5] * 7), proto.get("skill_points", [0] * 18)
    i = chargen.SKILL_NAMES.index(skill)
    default, modifier, stats = chargen.SKILL_FORMULA[i]
    return min(default + sum(special[k] for k in stats) * modifier // len(stats) + points[i], chargen.SKILL_MAX)


def price(cost: int, player_barter: int, merchant_barter: int, barter_mod: int = 0) -> int:
    """What goods of this base cost need on the player's table (caps count at 1)."""
    mod = max(10, min(300, 100 + player_barter - merchant_barter + barter_mod))
    return math.ceil(100 * cost / mod)


def inventory(mem: GameMemory, obj: int) -> list[tuple[int, int, int]]:
    """(item address, pid, quantity) of any object's inventory, in order (length +0x2C, items +0x34)."""
    n, arr = mem.i32(obj + 0x2C), mem.u32(obj + 0x34)
    out = []
    for i in range(max(0, min(n, 500))):
        item, qty = mem.u32(arr + 8 * i), mem.i32(arr + 8 * i + 4)
        out.append((item, mem.u32(item + 0x64) if item else 0, qty))
    return out


def panel(mem: GameMemory) -> tuple[int, int] | None:
    """The barter panel's corner on the screen, when it is open."""
    return next(((w[2], w[3]) for w in state.live_windows(mem) if (w[4], w[5]) == WINDOW_SIZE), None)


def screen_open(mem: GameMemory) -> bool:
    return panel(mem) is not None


def _scroll_to(mem: GameMemory, index: int, offset_global: str, key: str) -> int | None:
    """Scroll a list until `index` is on it; the slot it shows in."""
    for _ in range(60):
        offset = mem.i32(em.GLOBALS[offset_global].address)
        if offset <= index < offset + SLOTS:
            return index - offset
        session.press(f"{key}up" if index < offset else f"{key}down")
        time.sleep(0.12)
    return None


def _move(mem: GameMemory, start: tuple[int, int], end: tuple[int, int], quantity: int | None) -> None:
    """Drag a slot's item onto a table (points from the panel's corner); a stack asks how many: type it and Enter."""
    before = len(state.live_windows(mem))
    x0, y0 = panel(mem) or (80, 290)
    session.drag(x0 + start[0], y0 + start[1], x0 + end[0], y0 + end[1])
    if quantity is not None and win32.wait_for(lambda: len(state.live_windows(mem)) > before, 1.5, 0.05):
        time.sleep(0.2)
        session.type_text(str(quantity))
        session.press("enter")
        win32.wait_for(lambda: len(state.live_windows(mem)) == before, 2, 0.05)
    time.sleep(0.3)


def buy(
    actor: Actor,
    merchant: str,
    plan: list[str],
    pid: int,
    quantity: int = 1,
    tries: int = 3,
    pay_with: tuple[int, ...] = (),
) -> dict:
    """Talk to `merchant` by `plan` (its last phrase opens the barter screen), buy `quantity` of item `pid` with caps,
    go back to the talk and leave it. Checked by the item count."""
    log = actor.log
    who = quests.find_critter(actor, merchant)
    if who is None:
        return {"ok": False, "why": f"no {merchant} here"}
    have = actor._count(pid)
    mem = actor.mem
    # Walk up and talk; an empty plan stops at the first node with the talk open, where B opens the barter screen
    # (gdialog.c talk_to_pressed_barter). A plan may reach the screen itself (Killian's "I want to buy something.").
    quests.talk(actor, merchant, plan, strict=True)
    if not win32.wait_for(lambda: screen_open(mem), 3, 0.1) and dialogue.read(mem).active:
        session.press("b")  # the plan left the talk open without trading: the dialogue window's Barter
    if not win32.wait_for(lambda: screen_open(mem), 5, 0.1):
        return {"ok": False, "why": "no barter screen"}
    time.sleep(0.5)
    goods = inventory(mem, who.address)
    index = next((i for i, (_item, p, _q) in enumerate(goods) if p == pid), None)
    if index is None:
        session.press("t")
        return {"ok": False, "why": f"{merchant} has no {knowledge.proto_name(pid)}"}
    stack = goods[index][2]
    slot = _scroll_to(mem, index, "target_stack_offset", "ctrl+")
    if slot is None:
        return {"ok": False, "why": "the merchant's list did not scroll"}
    _move(mem, (MERCHANT_LIST, LIST_Y + 48 * slot), (MERCHANT_TABLE, TABLE_Y), quantity if stack > 1 else None)
    cost = knowledge.item_cost(pid) * quantity
    player_barter = chargen.skill_values(mem)["barter"]
    merchant_barter = npc_skill(who.pid, "barter")
    need = price(cost, player_barter, merchant_barter, mem.glob("barter_mod"))
    log.emit("barter", merchant=merchant, item=knowledge.proto_name(pid), quantity=quantity, cost=cost, offer=need,
             player_barter=player_barter, merchant_barter=merchant_barter, barter_mod=mem.glob("barter_mod"))  # fmt: skip
    offered = 0
    dude = mem.u32(em.GLOBALS["obj_dude"].address)
    for good in pay_with:  # goods count at their full base cost on the player's table; caps make up the rest
        mine = inventory(mem, dude)
        at = next((i for i, (_item, p, _q) in enumerate(mine) if p == good), None)
        slot = _scroll_to(mem, at, "stack_offset", "") if at is not None else None
        if slot is None:
            continue
        stack = mine[at][2]
        _move(mem, (PLAYER_LIST, LIST_Y + 48 * slot), (PLAYER_TABLE, TABLE_Y), 1 if stack > 1 else None)
        offered += knowledge.item_cost(good)
    for attempt in range(tries):
        dude = mem.u32(em.GLOBALS["obj_dude"].address)
        mine = inventory(mem, dude)
        caps_at = next((i for i, (_item, p, _q) in enumerate(mine) if p == PID_CAPS), None)
        caps = mine[caps_at][2] if caps_at is not None else 0
        add = max(0, need - offered) if attempt == 0 else max(1, need // 20)  # a refusal: 5 % more
        if add and (caps_at is None or caps < add):
            break
        while add > 0:
            part = min(add, MOVE_MAX)
            slot = _scroll_to(mem, caps_at, "stack_offset", "")
            if slot is None:
                break
            _move(mem, (PLAYER_LIST, LIST_Y + 48 * slot), (PLAYER_TABLE, TABLE_Y), part)
            add -= part
            offered += part
            mine = inventory(mem, dude)
            caps_at = next((i for i, (_item, p, _q) in enumerate(mine) if p == PID_CAPS), None)
            if caps_at is None:
                break
        session.press("m")
        if win32.wait_for(lambda: actor._count(pid) > have, 3, 0.1):
            break
    got = actor._count(pid) - have
    session.press("t")  # back to the talk; anything left on the tables goes home
    time.sleep(1.0)
    for _ in range(6):
        d = dialogue.read(mem)
        if not d.active or not d.options:
            break
        i = dialogue.leave(d.options)
        dialogue.choose(mem, i if i is not None else len(d.options) - 1)
    result = {"ok": got >= quantity, "got": got, "offered": offered, "estimate": need}
    log.emit("barter_end", **result)
    return result


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    name, plan, pid = argv[0], [p.strip().lower() for p in argv[1].split("|") if p.strip()], int(argv[2], 0)
    quantity = int(argv[3]) if len(argv) == 4 else 1
    actor = Actor(session.game_pid(), EventLog(paths.RUNS / time.strftime("%Y%m%d-%H%M%S-barter") / "events.jsonl"))
    try:
        print(buy(actor, name, plan, pid, quantity))
    finally:
        actor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
