"""Agent route tasks for brotherhood."""

from f1 import quests
from f1.actions import Actor
from f1.agent_glow import (
    PID_ROPE,
    PID_VREES_DISK,
    SHADY_BOOKCASE,
    the_glow_quest,
)
from f1.engine_map import Obj
from f1.route_support import (
    Step,
    in_town,
    on_map,
    travel,
)

# Inside the Brotherhood: its elevators (elevator.c retvals and keytable) open on spatial triggers: at the entrance
# elev1 (BROHDENT 20102: 'G' the entrance, '1' BROHD12 e0), inside elev0 (BROHD12 18540: '1' BROHD12 e0, '2' e1, '3'
# BROHD34 e0, '4' e1). Vree (BROHD34 e0) hands over her autopsy disk and her word (vree29: pid 194, DESTROY_MASTER_6)
# to a player of IN 7 whose reaction with her is 2 or more (her get_reaction: 40 + karma with CH 3).
BOS_ENTRY_LIFT, BOS_MAIN_LIFT, BOS_MAIN_LIFT_UPPER = 20102, 18540, 18536  # elev1; elev0 on BROHD12 e0 and e1

BOS_UP_LIFT_34, BOS_UP_LIFT_12 = 20940, 14512  # elev0 on BROHD34 e0 ('1' BROHD12 e0); elev1 on BROHD12 e0 ('G')

DOCTOR_RADS = ["#104"]  # BOSLORI.MSG 104 (IN 5): every rad taken away, a day passes

# VREE.MSG 112, 123 (IN 7), 140, 145 (vree29), 148
VREE_DISK_TALK = [
    "#112",
    "#123",
    "#140",
    "#145",
    "#130/148",
]


def radiation(actor: Actor) -> int:
    s = actor.snap()
    return actor.mem.i32(s.dude.address + Obj.CRITTER_RADIATION) if s.dude else 0


def out_of_the_bunker(actor: Actor) -> bool:
    """Up from the Brotherhood's bunker by its lifts' triggers (no exit grid below the entrance): BROHD34 or BROHD12
    e1 to BROHD12 e0, then 'G' to the entrance."""
    for _ in range(3):
        s = actor.snap()
        if s.map_name.startswith("BROHDENT") or s.screen == "worldmap":
            return True
        if s.map_name.startswith("BROHD34"):
            quests.ride_from(actor, BOS_UP_LIFT_34, "1")
        elif s.elevation == 1:
            quests.ride_from(actor, BOS_MAIN_LIFT_UPPER, "1")
        else:
            quests.ride_from(actor, BOS_UP_LIFT_12, "G")
    return actor.snap().map_name.startswith("BROHDENT")


def rads_away(actor: Actor) -> bool:
    """The Brotherhood's doctor (BROHD12 e1, script boslori) takes the Glow's rads away."""
    before = radiation(actor)
    if before == 0:
        return True
    actor.holster(True)
    quests.talk(actor, "boslori", DOCTOR_RADS)
    actor.log.emit("rads", before=before, after=radiation(actor))
    return radiation(actor) < before


def vrees_disk(actor: Actor) -> bool:
    if actor._count(PID_VREES_DISK) == 0 or quests.gvar(actor, "DESTROY_MASTER_6") == 0:
        actor.holster(True)
        quests.talk(actor, "Vree", VREE_DISK_TALK, strict=True)
    return actor._count(PID_VREES_DISK) > 0 and quests.gvar(actor, "DESTROY_MASTER_6") == 1


VREE = [
    Step(
        "into the bunker",
        lambda a: on_map(a, "BROHD") and not on_map(a, "BROHDENT") or quests.ride_from(a, BOS_ENTRY_LIFT, "1"),
        checkpoint=True,
    ),
    Step(
        "to the doctor's floor",
        lambda a: on_map(a, "BROHD34") or a.snap().elevation == 1 or quests.ride_from(a, BOS_MAIN_LIFT, "2"),
        checkpoint=True,
    ),
    Step("the doctor: the rads", lambda a: on_map(a, "BROHD34") or rads_away(a), checkpoint=True),
    Step(
        "down to the third level",
        lambda a: on_map(a, "BROHD34") or quests.ride_from(a, BOS_MAIN_LIFT_UPPER, "3"),
        checkpoint=True,
    ),
    Step("Vree: the autopsies", vrees_disk, checkpoint=True),
]


BROTHERHOOD = [
    Step(
        "to Shady Sands East",
        lambda a: in_town(a, travel(a, "shady sands", 1)) and (on_map(a, "SHADYE") or quests.take_exit(a, "SHADYE")),
        checkpoint=True,
        tries=6,
    ),
    Step(
        "the rope in the bookcase",
        lambda a: a._count(PID_ROPE) > 0 or a.loot(SHADY_BOOKCASE).ok,
        checkpoint=True,
    ),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("Cabbot: the Glow", the_glow_quest, checkpoint=True),
]
