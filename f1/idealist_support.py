"""Shared Idealist measurements, inventory tasks and timed rest."""

import time
from collections.abc import Callable

from f1 import geometry, loot, nav, quests, scripts, watchdog, world
from f1.actions import Actor
from f1.engine_map import Obj
from f1.route_support import (
    hour,
)

DAY = 864000  # game ticks


def karma(actor: Actor) -> int:
    return quests.gvar(actor, "PLAYER_REPUATION")


def experience(actor: Actor) -> int:
    return actor.snap().experience


def gained(actor: Actor, xp0: int, amount: int) -> bool:
    """At least `amount` XP since `xp0` (Swift Learner only adds)."""
    return experience(actor) - xp0 >= amount


def scripted(actor: Actor, tile: int, script: str) -> frozenset[int]:
    """The address of the object on `tile` (this elevation) that runs `script`: the click aims where the engine names
    it. Aimed at the hex alone, Science on the library's first computer hit the Central Core in front of it ("You
    cannot get there.", four tries)."""
    s = actor.snap()
    return frozenset(
        t.address
        for t in world.things(actor.mem)
        if t.tile == tile and t.elevation == s.elevation and scripts.script_of(actor.mem, t.address) == script
    )


def take_from(tile: int) -> Callable[[Actor], bool]:
    """A step's run: the free container or item on `tile` (f1.atlas) taken, on a path out of the foes' notice. Done
    too when it is gone already (taken before, or never there)."""

    def run(actor: Actor) -> bool:
        f = next((f for f in loot.finds(actor, town_containers=True) if f.tile == tile), None)
        got = "none there" if f is None else loot.take(actor, f, loot.danger(actor))
        actor.log.emit("take_from", tile=tile, got=got)
        return f is None or got == loot.TAKEN

    return run


def the_floor(actor: Actor) -> bool:
    """Everything free that is worth it on this floor (loot.sweep, the whole floor, out of the foes' notice): before a
    fight, the rounds that lie about. The radscorpion caves' file puts two boxes of 10mm JHP and one of 10mm AP (30
    rounds each) and a Stimpak there; a run found one box and ran dry after four kills."""
    loot.sweep(actor, max_hexes=200, budget_s=180)
    return True


def beside(actor: Actor, tile: int, tries: int = 5, pause: float = 5.0) -> bool:
    """Onto a free hex next to `tile`. The engine's own walk to an object can fail where ours does not: Science on
    the library's computer at 17533 from two hexes away answered that it could not be reached every time, and from the
    hex beside it (17534) it worked. A walk that finds no way is tried again after `pause`, the obstacles read anew:
    on a new game's day 1 the walk found no path twice (from 22140 and 17731) and the run ended there, while from the
    same start another time it arrived (17312 to 17534): something on the way that moves (not measured: the dwellers
    walk that floor). A second run: the walk found no path three times in 30 s (a dweller stood in the door at 21924,
    capture) and the engine's walk then went round to 17731 and failed: up to five tries, 5 s apart."""
    for attempt in range(tries):
        if attempt:  # the dwellers move on: a wait of the route's choosing, not a stuck game
            watchdog.quiet(pause + 20)
            time.sleep(pause)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        free = {t for t in geometry.ring(tile, 1) if t not in obs.blocked}
        if s.dude.tile in free or (free and nav.go_to(actor, free, max_steps=80)[0]):
            watchdog.unquiet()
            return True
    watchdog.unquiet()
    return False


def read_book(actor: Actor, pid: int) -> bool:
    """A book read from the inventory: it goes (obj_use_item destroys it) and its skill gets (100 - skill) / 10
    points, twice as many percent when tagged (protinst.c obj_use_book), in (11 - IN) hours."""
    if not actor._count(pid):
        return True
    return actor.use_on_self(pid).ok


def day(actor: Actor) -> int:
    return actor.snap().game_time // DAY


def rest_until(actor: Actor, start: float, end: float) -> bool:
    """Rest, in the steps the Pip-Boy offers, until the hour is in [start, end)."""
    for _ in range(8):
        h = hour(actor)
        if start <= h < end:
            return True
        if 0 < start - h <= 1:
            how = "1 hour"
        elif h < start <= 18:
            how = "until evening" if start > 12 else "until noon" if start > 6 else "until morning"
        else:
            how = "6 hours"
        if not actor.rest(how).ok:
            return False
    return start <= hour(actor) < end


PID_COMBAT_ARMOR = 17

PID_BOS_ARMOR, PID_POWER_ARMOR, PID_MOTIVATOR = 239, 3, 229
PID_HARDENED_POWER_ARMOR = 232  # Miles's work on pid 3 (MILES.INT Miles28; f1.hardened)


def hands_empty(actor: Actor) -> bool:
    from f1.actions import OBJECT_IN_LEFT_HAND, OBJECT_IN_RIGHT_HAND

    for hand, flag in (("left", OBJECT_IN_LEFT_HAND), ("right", OBJECT_IN_RIGHT_HAND)):
        held = next((p for i, p, _ in actor.items() if actor.mem.u32(i + Obj.FLAGS) & flag), None)
        if held is not None:
            actor.unequip(hand)
    return not any(
        actor.mem.u32(i + Obj.FLAGS) & (OBJECT_IN_LEFT_HAND | OBJECT_IN_RIGHT_HAND) for i, _, _ in actor.items()
    )


def rearm(actor: Actor) -> bool:
    """The best armour carried worn (Hardened Power, Power, Brotherhood, Combat), the best weapon ready, holstered."""
    for pid in (PID_HARDENED_POWER_ARMOR, PID_POWER_ARMOR, PID_BOS_ARMOR, PID_COMBAT_ARMOR):
        if actor._count(pid):
            actor.equip(pid, "armor")
            break
    actor.ready_weapon()
    actor.holster(True)
    return True
