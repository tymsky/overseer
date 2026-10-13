"""Agent route tasks for sands."""

from f1 import dialogue, quests, win32, worldmap
from f1.actions import PID_STIMPAK, Actor
from f1.agent_junktown import (
    ARADESH_REWARD,
    ARADESH_TANDI,
    GARL_FREE,
    PID_CAPS,
)
from f1.route_support import (
    Step,
    on_map,
    travel,
)

GVAR_NUM_RADSCORPIONS, GVAR_RADSCORPION_SEED = 2, 43

SETH_POST = 14108  # Shady Sands' gate (SHADYW), where Seth stands; his proto is a plain "Peasant"

# ARADESH.MSG: the first talk ends after the greeting (104, 115); the next one
# asks (153 or 160), then 166 -> Ara_28 -> 197 -> Ara_28a -> 199 (the quest)
ARADESH_SCORPIONS = [
    "#104/124/146",
    "#115",
    "#117",
    "#150/166/194/216",
    "#197",
    "#199",
    "#153",
    "#160",
]

SETH_CAVES = ["#118", "#323", "#126"]  # SETH.MSG 118, 323, 126

ARADESH_NEST = ["#209"]  # ARADESH.MSG 209 -> Ara_32, his thanks (210)

# OVER.MSG: Over_12, asking about the chip, only sighs (122); Over_19a leads to Over_22 -> 139 -> the supplies
OVERSEER_SUPPLIES = ["#132", "#139", "#122"]


def scorpion_quest(actor: Actor) -> bool:
    """Aradesh's first talk only lets the stranger in; the quest comes in the next one."""
    for _ in range(3):
        if quests.gvar(actor, GVAR_RADSCORPION_SEED) >= 1:
            return True
        quests.talk(actor, "Aradesh", ARADESH_SCORPIONS)
    return quests.gvar(actor, GVAR_RADSCORPION_SEED) >= 1


CAVES_SECTION = 2  # Shady Sands' town map: the third entrance, the caves once seen (unmeasured)


def seth_to_the_caves(actor: Actor) -> bool:
    if not on_map(actor, "CAVES"):
        quests.talk(actor, "Peasant", SETH_CAVES, near=SETH_POST)
    return win32.wait_for(lambda: on_map(actor, "CAVES") and actor.snap().screen == "map", 30, 0.5)


def clear_the_caves(actor: Actor) -> bool:
    """Every radscorpion killed. A fight the odds gate refused is fled to the world map, where the party stands on
    Shady Sands' spot (a click on it entered SHADYW, not the caves): the next try enters again by the town map's third
    entrance, else walks to Seth, who leads to the caves again; the hunt heals before its next fight."""
    if quests.gvar(actor, GVAR_NUM_RADSCORPIONS) > 0 and not on_map(actor, "CAVES"):
        if actor.snap().screen == "worldmap":
            worldmap.enter_here(actor.pid, CAVES_SECTION)
            win32.wait_for(lambda: actor.snap().screen == "map", 30, 0.5)
        entered = actor.snap().map_name
        if not on_map(actor, "CAVES"):
            seth_to_the_caves(actor)
        actor.log.emit("caves_again", entered=entered, now=actor.snap().map_name)
    if quests.gvar(actor, GVAR_NUM_RADSCORPIONS) > 0 and on_map(actor, "CAVES"):
        quests.hunt(actor, "Radscorpion")
    return quests.gvar(actor, GVAR_NUM_RADSCORPIONS) == 0


def nest_reported(actor: Actor) -> bool:
    """The nest's reward from Aradesh. Once the raiders have taken Tandi he talks of nothing else, and the talk takes
    up her rescue (RESCUE_TANDI; the tandi route goes on from there): the road's mantises took every stimpak, the
    rest after the caves ran 102 h, and she was gone (a full run at Full HD)."""
    if quests.gvar(actor, "RESCUE_TANDI"):
        return True
    steps = quests.talk(actor, "Aradesh", ARADESH_NEST) or []
    return (
        any(dialogue.holds(reply, "aradesh", 210) for reply, _options, _chosen in steps)
        or quests.gvar(actor, "RESCUE_TANDI") != 0
    )


def supplies(actor: Actor) -> bool:
    """The Overseer's first greeting after the start only sighs; the next one offers equipment."""
    before = actor._count(PID_STIMPAK)
    for _ in range(3):
        quests.talk(actor, "Vault Overseer", OVERSEER_SUPPLIES)
        if actor._count(PID_STIMPAK) > before:
            return True
    return False


START = [
    Step("a weapon in hand", lambda a: a.ready_weapon().ok),
    Step("out of the cave", lambda a: quests.leave_map(a)),
    Step("to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: the radscorpions", scorpion_quest),
    Step("Seth: to the caves", seth_to_the_caves, checkpoint=True),
    Step("the radscorpions", clear_the_caves, checkpoint=True, tries=6),
    Step("out of the caves", lambda a: quests.leave_map(a)),
    Step("back to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: the nest destroyed", nest_reported),
    Step("to Vault 13", lambda a: travel(a, "vault 13", 3).endswith((".MAP", ".SAV")), checkpoint=True, tries=6),
    Step("the Overseer's supplies", supplies, checkpoint=True),
]


def tandi_quest(actor: Actor) -> bool:
    if quests.gvar(actor, "RESCUE_TANDI") == 0:
        actor.holster(True)
        quests.talk(actor, "Aradesh", ARADESH_TANDI, strict=True)
    return quests.gvar(actor, "RESCUE_TANDI") != 0


def garl_lets_her_go(actor: Actor) -> bool:
    """Garl talked down (a Speech line; his metal armor makes a fistfight hopeless for a Small Frame)."""
    if quests.gvar(actor, "TANDI_STATUS") != 5:
        actor.holster(True)
        quests.talk(actor, "Garl", GARL_FREE, strict=True)
    return quests.gvar(actor, "TANDI_STATUS") == 5 and not actor.in_combat()


def tandi_reward(actor: Actor) -> bool:
    """RESCUE_TANDI turns 2 when she is home; the 500 caps come only from Aradesh's talk (checked by the caps)."""
    caps = actor._count(PID_CAPS)
    actor.holster(True)
    steps = quests.talk(actor, "Aradesh", ARADESH_REWARD) or []
    paid = any(dialogue.holds(reply, "aradesh", 242) for reply, _options, _chosen in steps)
    return actor._count(PID_CAPS) >= caps + 500 or paid


TANDI = [
    Step("to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: Tandi is missing", tandi_quest),
    Step("to the Khans", lambda a: travel(a, "raiders", 0).startswith("RAIDERS"), checkpoint=True, tries=6),
    Step("Garl lets her go", garl_lets_her_go, checkpoint=True, tries=5),
    Step("Tandi home", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: the reward", tandi_reward, checkpoint=True),
]
