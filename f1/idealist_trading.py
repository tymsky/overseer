"""Merchant supplies for the Idealist journey to the Glow."""

from collections.abc import Callable

from f1 import knowledge
from f1.actions import Actor
from f1.idealist_hub import (
    hub_map,
)
from f1.route_support import (
    settle,
)

# The Brotherhood and the Glow (planned from the scripts, within the game's time limits).
# Cabbot's talk puts the Glow on the world map (cabbot06: THE_GLOW 1). The beam down needs a rope, and Vault 15 took
# both of the route's ropes; Mitch in Hub Downtown sells them (ALLNONE, his box MITCHBOX at 27850 holds 5 Rope, a
# Tool, 3 RadAway, a Big Book of Science; his stock reaches his inventory only in a talk, Get_Stuff). Brotherhood to
# the Hub 2.4 days, the Hub to the Glow 5.3, the Brotherhood to the Glow 7.5 (f1.travel 100 1).
MITCH = "allnone"  # a "Merchant" by name: found by his script

PID_TOOL, PID_RAD_X, PID_RADAWAY, PID_10MM_SMG = 75, 109, 48, 9

# Vance (HUBOLDTN e0 16059, VANCE; his box 12846 holds 26 Rad-X): he sells only to who "knows" (MASTER_FILLER_9) or
# after Vance02a, Speech -30 or a CH -3 roll (VANCE.MSG 103, 109; a failure sends you off, the next talk starts
# over). His trade runs at barter mod -20: a Rad-X (cost 300) is 313 caps at Barter 46. The plan: two Rad-X make
# the Glow rad-free for 24 game hours (resistance 12 + 2 x 50, capped at 100: the Glow plan), four make two windows.
VANCE_SELL = [
    "#122",
    "#103",
    "#109",
]

# VANCE.MSG 122 (Vance06, a later talk: live), 103, 109


def from_merchant(
    merchant: str,
    where: str,
    pid: int,
    quantity: int = 1,
    plan: list[str] | None = None,
    pay_with: tuple[int, ...] = (),
) -> Callable[[Actor], bool]:
    def buy(actor: Actor) -> bool:
        from f1 import barter

        if actor._count(pid) >= quantity:
            return True
        if not hub_map(actor, where):
            return False
        actor.holster(True)
        goods = tuple(g for g in pay_with if actor._count(g))
        out = barter.buy(actor, merchant, plan or [], pid, quantity - actor._count(pid), pay_with=goods)
        actor.log.emit("shopping", item=knowledge.proto_name(pid), **out)
        settle(actor)
        return actor._count(pid) >= quantity

    return buy


def from_mitch(pid: int, quantity: int = 1) -> Callable[[Actor], bool]:
    return from_merchant(MITCH, "HUBDWNTN", pid, quantity)
