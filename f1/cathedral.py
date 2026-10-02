"""The Idealist's Cathedral, the game's last stop: the Master talked into his end (master19), the escape from the lair,
then the ending (the vats went first: VATS_BLOWN 1 since idealist_military, so the lair's explosion on the world map
takes the party to Vault 13 and the slides). Run from the kept save idealist-watershed-done (HALLDED, day 92.75).

    python -m f1.routes idealist_cathedral [--from N] [--until N] [--clock K]

The Agent's Cathedral steps (routes.CATHEDRAL, live) with three changes for the Idealist:
- the road in the Powered Armor with the Turbo ready, the Robes and empty hands only on CHILDRN1 (in Robes a road pack
  killed the Idealist after the base);
- the top floor's shelves only for stimpaks, and optional (the base is behind it: no Electronic Lock Pick needed);
- the psychic corridor heals below 60 HP, not 30 (the Agent left it at HP 10).
Solo (Katja left at the base, KATJA_STATUS 3); Vree's word is carried (DESTROY_MASTER_6 1), which the Master's
sterility line (IN 7) needs. Speech is 110+: Morpheus's roll (103) and the Master's (120, 127, 150).
"""

from f1 import quests
from f1.actions import PID_STIMPAK, Actor
from f1.idealist import experience, hands_empty, karma
from f1.routes import (
    MASTER_TALK,
    MORPHEUS_TAKE_ME,
    PID_ROBES,
    TOP_FLOOR_SHELVES,
    Step,
    lashers_badge,
    on_map,
    out_of_the_lair,
    red_door_open,
    stairs,
    the_ending,
    travel,
    wearing,
)

PID_POWER_ARMOR = 3
SHELF_STIMPAKS = 6  # below this many the top floor's shelves are looted (2 stimpaks and a first aid kit there)
LAIR_HEAL_AT = 60


def out_of_necropolis(actor: Actor) -> bool:
    actor.holster(True)
    return actor.snap().screen == "worldmap" or quests.leave_map(actor)


def to_the_cathedral(actor: Actor) -> bool:
    if on_map(actor, "CHILDRN"):
        return True
    if actor.snap().screen == "map" and not wearing(actor, PID_POWER_ARMOR) and actor._count(PID_POWER_ARMOR):
        actor.equip(PID_POWER_ARMOR, "armor")
    return travel(actor, "cathedral", 0).startswith("CHILDRN")


def robed(actor: Actor) -> bool:
    """Robes in the armour slot and both hands empty (the nightkin and the Master's guards look at both)."""
    if not wearing(actor, PID_ROBES):
        actor.equip(PID_ROBES, "armor")
    hands_empty(actor)
    actor.log.emit("robed", robes=wearing(actor, PID_ROBES), stimpaks=actor._count(PID_STIMPAK))
    return wearing(actor, PID_ROBES)


def into_the_hall(actor: Actor) -> bool:
    return actor.snap().elevation == 1 or quests.use_scenery(
        actor, ("cocdoor",), lambda: actor.snap().elevation == 1, "the Cathedral's door"
    )


def shelves(actor: Actor) -> bool:
    n0 = actor._count(PID_STIMPAK)
    if n0 < SHELF_STIMPAKS:
        for tile in TOP_FLOOR_SHELVES:
            actor.loot(tile)
    actor.log.emit("shelves", stimpaks=actor._count(PID_STIMPAK), before=n0)
    return actor._count(PID_STIMPAK) >= min(n0 + 1, SHELF_STIMPAKS) or n0 >= SHELF_STIMPAKS


def the_master(actor: Actor) -> bool:
    """Morpheus takes the player down (morphx2), the Master talked into master19: MASTER_BLOWN 1 and the countdown
    (240 game seconds). A failed last roll (master17_1) is a fight with the Master: the runner's load after a death."""
    if quests.gvar(actor, "MASTER_BLOWN") == 1:
        return True
    xp0, k0 = experience(actor), karma(actor)
    quests.talk(actor, "morph", MORPHEUS_TAKE_ME + MASTER_TALK, strict=True)
    actor.log.emit(
        "the_master", blown=quests.gvar(actor, "MASTER_BLOWN"), destroy_master_5=quests.gvar(actor, "DESTROY_MASTER_5"),
        xp=experience(actor) - xp0, karma=karma(actor) - k0, map=actor.snap().map_name,
    )  # fmt: skip
    return quests.gvar(actor, "MASTER_BLOWN") == 1


def escape(actor: Actor) -> bool:
    k0 = karma(actor)
    ok = out_of_the_lair(actor, heal_at=LAIR_HEAL_AT)
    actor.log.emit("lair_escape", ok=ok, karma=karma(actor) - k0, screen=actor.snap().screen)
    return ok


IDEALIST_CATHEDRAL = [
    Step("out of Necropolis", out_of_necropolis),
    Step("to the Cathedral", to_the_cathedral, checkpoint=True, tries=6),
    Step("robes on, hands empty", robed),
    Step("into the hall", into_the_hall, checkpoint=True),
    Step("Lasher: the badge", lashers_badge, checkpoint=True),
    Step("the red door", red_door_open, checkpoint=True),
    Step("up the towers", lambda a: stairs(a, "chid2twr", lambda: on_map(a, "CHILDRN2")), checkpoint=True),
    Step("the second floor", lambda a: stairs(a, "ctower2", lambda: a.snap().elevation >= 1)),
    Step("the top floor", lambda a: stairs(a, "ctower3", lambda: a.snap().elevation == 2), checkpoint=True),
    Step("the top floor's shelves", shelves, checkpoint=True, optional=True),
    Step("Morpheus, then the Master", the_master, tries=6),
    Step("out of the lair", escape),
    Step("the ending", the_ending, final=True),
]

ROUTES = {"idealist_cathedral": IDEALIST_CATHEDRAL}
