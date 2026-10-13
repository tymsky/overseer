"""Agent route tasks for hub."""

from f1 import chargen, knowledge, quests
from f1.actions import PID_STIMPAK, Actor
from f1.agent_junktown import (
    PID_CAPS,
)
from f1.route_support import (
    Step,
    in_town,
    on_map,
    travel,
)

PID_HUNTING_RIFLE, PID_10MM_JHP = 0x0A, 0x1D


def affordable(actor: Actor, merchant: str, pid: int, goods: tuple[int, ...] = ()) -> int:
    """How many of `pid` the merchant has and the caps (plus `goods` at full cost) pay for, priced by
    barter_compute_value with the talk's modifier taken as 0 (Rutger's and Kane's, live)."""
    from f1 import barter

    who = quests.find_critter(actor, merchant)
    if who is None:
        return 0
    stock = sum(q for _i, p, q in barter.inventory(actor.mem, who.address) if p == pid)
    player = chargen.skill_values(actor.mem)["barter"]
    each = barter.price(knowledge.item_cost(pid), player, barter.npc_skill(who.pid, "barter"))
    budget = actor._count(PID_CAPS) + sum(knowledge.item_cost(g) for g in goods)
    return min(stock, budget // max(1, each))


def ammo_from_rutger(actor: Actor) -> bool:
    """The Far Go Traders' Rutger keeps a few boxes of 10mm JHP (two, live): buy what the caps allow."""
    from f1 import barter

    quantity = affordable(actor, "Rutger", PID_10MM_JHP)
    if quantity == 0:
        return True  # none left, or no caps: not a reason to stop the route
    out = barter.buy(actor, "Rutger", [], PID_10MM_JHP, quantity)
    actor.log.emit("shopping", item="10mm JHP", **out)
    return out.get("ok", False)


def stimpaks_from_kane(actor: Actor) -> bool:
    """Kane sells stimpaks (nine, live): a carried Hunting Rifle pays at its full cost, caps pay for the rest."""
    from f1 import barter

    goods = (PID_HUNTING_RIFLE,) if actor._count(PID_HUNTING_RIFLE) else ()
    quantity = affordable(actor, "Kane", PID_STIMPAK, goods)
    if quantity == 0:
        return True
    out = barter.buy(actor, "Kane", [], PID_STIMPAK, quantity, pay_with=goods)
    actor.log.emit("shopping", item="Stimpak", **out)
    return out.get("ok", False)


HUB_SUPPLIES = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("downtown", lambda a: on_map(a, "HUBDWNTN") or quests.take_exit(a, "HUBDWNTN"), checkpoint=True),
    Step("10mm from Rutger", ammo_from_rutger, checkpoint=True),
    Step("stimpaks from Kane", stimpaks_from_kane, checkpoint=True),
]
