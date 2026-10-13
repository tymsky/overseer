"""Agent route tasks for glow."""

from f1 import geometry, nav, quests
from f1.actions import Actor
from f1.route_support import (
    Step,
    in_town,
    on_map,
    travel,
    zone,
)

# The Brotherhood: the Master's ending by talk needs Vree's disk or her word (MASTER.INT master09), Vree is inside the
# bunker, and its door opens for initiates, made by the Glow's tape (scripts and maps read, the log).
PID_ROPE, PID_BROTHERHOOD_TAPE, PID_VREES_DISK = 0x7F, 0xA4, 0xC2

# A rope in a bookcase in Shady Sands East, behind a curtain (f1.mapfile; taken live). Killian's
# shelves hold one behind his locked storeroom door (25690 stayed shut); one lying at SHADYE 27694 was not reached.
SHADY_BOOKCASE = 13097

# CABBOT.MSG 105, 116, 125 (cabbot09 sets BECOME_AN_INITIATE 1); 145 when he was met before
CABBOT_JOIN = ["#105/111", "#145/154", "#116", "#125/151/161"]


def the_glow_quest(actor: Actor) -> bool:
    if quests.gvar(actor, "BECOME_AN_INITIATE") == 0:
        actor.holster(True)
        quests.talk(actor, "Cabbot", CABBOT_JOIN, strict=True)
    return quests.gvar(actor, "BECOME_AN_INITIATE") >= 1


# The Glow (GLOWENT, GLOW1: maps and scripts read). A rope used on the beam over the pit, then the beam,
# takes the player down (gent2lv1); the proof Cabbot wants is the Brotherhood Tape on a dead paladin; the rubble
# leads back up (glv12ent). No live foes on the first level; radiation everywhere, hot spots and floor traps.
GLOW_BEAM = 21316  # GLOWENT e0

# The beam is drawn east of its hex, over the pit; the player standing beside it covers the hex itself (live:
# a click on the hex used the rope on the player (no effect); (40, -6) tied it and went down).
GLOW_BEAM_AIMS = ((40, -6), (60, -8), (25, -4), (80, -10), (50, 0))

GLOW_BODY = 14304  # GLOW1 e0: a dead Brotherhood paladin: the Brotherhood Tape and the Yellow Pass Key

GLOW1_HOT_SPOTS = ((20909, 2), (20316, 2), (16108, 3), (16719, 2))  # HOTSPOT: 20-50 rads on each entry

GLOW1_FLOOR_TRAPS = ((23522, 6),)  # TRAPFLOR: a Traps roll per step inside, one 10-30 blast (the body's is unavoidable)

CABBOT_TAPE = ["#158", "#125/151/161", "#164"]  # CABBOT.MSG 158, 161, 164: the tape taken, BECOME_AN_INITIATE 2


def glow_walk(actor: Actor, goals: set[int]) -> bool:
    """Across GLOW1 round its hot spots and floor traps (round the hot spots alone when that finds no way)."""
    for avoid in (zone(GLOW1_HOT_SPOTS + GLOW1_FLOOR_TRAPS), zone(GLOW1_HOT_SPOTS)):
        ok, why = nav.go_to(actor, goals - avoid, max_steps=80, fight=True, avoid=avoid - goals)
        actor.log.emit("glow_walk", ok=ok, why=why, tile=actor.snap().dude.tile)
        if ok:
            return True
        if why != "no path":
            return False
    return False


def rope_on_the_beam(actor: Actor) -> bool:
    if on_map(actor, "GLOW1") or actor._count(PID_ROPE) == 0:
        return on_map(actor, "GLOW1") or actor.snap().map_name.startswith("GLOWENT")
    before = actor._count(PID_ROPE)
    return actor.use_item_on(PID_ROPE, GLOW_BEAM, lambda: actor._count(PID_ROPE) < before, aims=GLOW_BEAM_AIMS).ok


def the_tape(actor: Actor) -> bool:
    if actor._count(PID_BROTHERHOOD_TAPE):
        return True
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    near = {t for r in (2, 3) for t in geometry.ring(GLOW_BODY, r) if t not in obs.blocked}
    if not glow_walk(actor, near):
        return False
    actor.loot(GLOW_BODY)
    return actor._count(PID_BROTHERHOOD_TAPE) > 0


def up_the_rubble(actor: Actor) -> bool:
    if on_map(actor, "GLOWENT"):
        return True
    thing = quests.nearest_thing(actor, ("glv12ent", "Rubble"))
    if thing is None:
        return False
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_walk(actor, {t for t in geometry.ring(thing.tile, 1) if t not in obs.blocked})
    return quests.use_scenery(actor, ("glv12ent", "Rubble"), lambda: on_map(actor, "GLOWENT"), "the rubble up")


def hand_in_the_tape(actor: Actor) -> bool:
    if quests.gvar(actor, "BECOME_AN_INITIATE") != 2:
        actor.holster(True)
        quests.talk(actor, "Cabbot", CABBOT_TAPE, strict=True)
    return quests.gvar(actor, "BECOME_AN_INITIATE") == 2


GLOW = [
    Step("to the Glow", lambda a: travel(a, "the glow", 0).startswith("GLOW"), checkpoint=True, tries=6),
    Step("the rope on the beam", rope_on_the_beam),
    Step(
        "down the beam",
        lambda a: (
            on_map(a, "GLOW1")
            or quests.use_scenery(a, ("gent2lv1", "Beam"), lambda: on_map(a, "GLOW1"), "the beam", aims=GLOW_BEAM_AIMS)
        ),
        checkpoint=True,
    ),
    Step("the Brotherhood Tape", the_tape, checkpoint=True, tries=4),
    Step("up the rubble", up_the_rubble, checkpoint=True),
    Step("out of the Glow", lambda a: quests.leave_map(a)),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("Cabbot: the tape", hand_in_the_tape, checkpoint=True),
]
