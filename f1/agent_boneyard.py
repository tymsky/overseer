"""Agent route tasks for boneyard."""

from f1 import geometry, nav, quests
from f1.actions import Actor
from f1.agent_brotherhood import (
    out_of_the_bunker,
)
from f1.agent_hub import (
    PID_10MM_JHP,
)
from f1.agent_junktown import (
    PID_LEATHER_ARMOR,
)
from f1.route_support import (
    Step,
    in_town,
    on_map,
    travel,
)

# Robes (pid 113, the Children of the Cathedral's): worn, they pass the Military Base's and the Cathedral's guards
# (MB guard option 111, the nightkin's and Krupper's checks, from the scripts). Three lie on the floor of the
# Followers' library (LAFOLLWR e0, f1.mapfile); no talk there (Nicole's help would add fighters to the party).
PID_ROBES = 0x71

FOLLOWERS_ROBES = (18892, 19092, 19291)


def robes_from_the_floor(actor: Actor) -> bool:
    for tile in FOLLOWERS_ROBES:
        if actor._count(PID_ROBES):
            break
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(tile, 1) if t not in obs.blocked} or {tile}, 80)
        actor.pick_up(tile, PID_ROBES)
    return actor._count(PID_ROBES) > 0


ROBES = [
    Step("out of the bunker", lambda a: not on_map(a, "BROHD") or out_of_the_bunker(a), checkpoint=True),
    Step(
        "to the Followers",
        lambda a: in_town(a, travel(a, "boneyard", 2)) and (on_map(a, "LAFOLLWR") or quests.goto_map(a, "LAFOLLWR")),
        checkpoint=True,
        tries=6,
    ),
    Step("robes from the library floor", robes_from_the_floor, checkpoint=True),
]


ZACK_TRADE = ["#104"]  # ZACK.MSG 104: straight to the barter screen (gdialog_barter); his stock is the guncache locker


def gun_runners_ammo(actor: Actor) -> bool:
    """Four boxes of 10mm JHP from Zack for the spare Leather Armor (800 on the table against 652 at Barter 16 to his
    70): the Agent came out of the Cathedral with a 10mm pistol, no rounds and fists that did nothing to 26-HP road
    foes (a death on the way to the Military Base)."""
    from f1 import barter

    if actor._count(PID_10MM_JHP) >= 2:
        return True
    result = barter.buy(actor, "zack", ZACK_TRADE, PID_10MM_JHP, 4, pay_with=(PID_LEATHER_ARMOR,))
    actor.log.emit("gun_runners", **result)
    return actor._count(PID_10MM_JHP) > 0


FOLLOWERS_CHEST = 18295  # LAFOLLWR e0, no script (map file): 10mm JHP x2, 10mm AP x2, a Desert Eagle, .44 JHP x2


# The way to the Military Base after the Cathedral: its road fights want a gun with rounds. The Gun Runners (above)
# sit behind the Rippers' map, whose map script places up to three roaming deathclaws (225 HP) at random spots by
# the way out; the Followers' ice chest is on a map the town map opens straight away.
FOLLOWERS_GUNS = [
    Step(
        "to the Followers",
        lambda a: in_town(a, travel(a, "boneyard", 2)) and (on_map(a, "LAFOLLWR") or quests.goto_map(a, "LAFOLLWR")),
        checkpoint=True,
        tries=6,
    ),
    Step("the ice chest", lambda a: a._count(PID_10MM_JHP) > 0 or a.loot(FOLLOWERS_CHEST).ok, checkpoint=True),
    Step("a gun ready", lambda a: a.ready_weapon().ok),
]


GUN_RUNNERS = [
    Step("to the Boneyard", lambda a: travel(a, "boneyard", 0).startswith("LA"), checkpoint=True, tries=6),
    Step("to the Gun Runners", lambda a: on_map(a, "LAGUNRUN") or quests.goto_map(a, "LAGUNRUN"), checkpoint=True),
    Step("ammunition from Zack", gun_runners_ammo, checkpoint=True),
    Step("the pistol loaded", lambda a: a.ready_weapon().ok),
]
