"""Agent route tasks for cathedral."""

import time

from f1 import dialogue, geometry, nav, quests, win32, world
from f1.actions import Actor
from f1.agent_boneyard import (
    PID_ROBES,
)
from f1.engine_map import Obj
from f1.memory import ReadError
from f1.route_support import (
    Step,
    hands_empty,
    heal_below,
    on_map,
    travel,
    wearing,
)

# The Cathedral and the Master: in Robes, both hands empty
# (the nightkin and the Master's guards look at the hand slots), alone. A cocdoor takes the player to the hall (e1);
# Lasher gives the red COC badge; the badge opens the red door; the towers' stairs lead to Morpheus, who takes a
# player with a Vault's location to the Master (MSTRLR34 e0 13655).
PID_COC_BADGE = 0x8E  # 142, red

LASHER_BADGE = ["#109", "#149/195"]  # LASHER.MSG 109, 149

RED_DOOR = 13888  # CHILDRN1 e1, creddoor: pid 141/142 used on it unlocks it

MORPHEUS_TAKE_ME = [  # MORPH.MSG 103 (a Speech roll), 123, 128 (or 175 after a failed roll), 141: morphx2
    "morph#103",
    "morph#123",
    "morph#128",
    "morph#145/166/175",
    "morph#141",
]

MASTER_TALK = [  # MASTER.MSG: the way to master19 (his suicide) with Vree's word; rolls at 120, 127 and 150
    "master#103",
    "master#116",
    "master#118",
    "master#120",
    "master#123",
    "master#127",
    "master#133",
    "master#137",
    "master#156",
    "master#163",
    "master#150",
]


# After master19: 240 s of game time to leave the Cathedral's maps (MASTER1/2, CHILDRN1/2 map_update; worldmap.c).
# MSTRLR34 e0: south along x 57 past seven psychic triggers (revulse: up to 27 damage each; healing after the 3rd and
# the 4th), doors, the lift's trigger 16912 ('1': MSTRLR12 e0 12498); MSTRLR12 e0: the airlock guard, then the stairs
# up (mas2chid) to CHILDRN1 e1; the hall's doors down (chocdoor: e0 19701), out by the exit grids.
LAIR_LIFT = 16912

CORRIDOR_STOPS = (19057, 20457)  # (57, 95), (57, 102)

AIRGRD_PASS = [  # AIRGRD.MSG 114 (a Speech roll), 121; 118 when asked again
    "#114",
    "#121",
    "#118",
]

LAIR_STAIRS = (20536, 20732, 20734)  # MSTRLR12 e0, mas2chid

PID_ELECTRONIC_LOCKPICK = 0x4D  # 77: MBOUT2IN's door takes it at Lockpick -40, the bare skill at -60

TOP_FLOOR_SHELVES = (18911, 20891)  # CHILDRN2 e2 (map file): the Electronic Lock Pick; 2 stimpaks, a first aid kit


def out_of_the_lair(actor: Actor, heal_at: int = 30) -> bool:
    start = time.monotonic()

    def mark(what: str) -> None:
        s = actor.snap()
        actor.log.emit("escape", what=what, seconds=round(time.monotonic() - start, 1), map=s.map_name,
                       hp=s.dude.hp if s.dude else None)  # fmt: skip

    if on_map(actor, "MSTRLR34"):
        for stop in CORRIDOR_STOPS:
            nav.go_to(actor, {stop, *geometry.ring(stop, 1)}, 60, fight=False)
            heal_below(actor, heal_at)
            mark(f"corridor {stop}")
        if not quests.ride_from(actor, LAIR_LIFT, "1"):
            mark("no lift")
            return False
        mark("lift")
    for _ in range(8):
        if not on_map(actor, "MSTRLR12"):
            break
        if actor.snap().screen == "dialogue":  # the airlock guard
            dialogue.converse_strict(actor.mem, AIRGRD_PASS, log=actor.log)
            continue
        s = actor.snap()
        if min(geometry.distance(s.dude.tile, t) for t in LAIR_STAIRS) > 4:
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            nav.go_to(actor, {t for g in LAIR_STAIRS for t in geometry.ring(g, 2) if t not in obs.blocked}, 60)
            continue
        stairs(actor, "mas2chid", lambda: on_map(actor, "CHILDRN1"))
    mark("stairs")
    if on_map(actor, "CHILDRN1") and actor.snap().elevation == 1:
        quests.use_scenery(actor, ("chocdoor",), lambda: actor.snap().elevation == 0, "the hall's door")
        mark("down")
    ok = quests.leave_map(actor)
    mark("out" if ok else "not out")
    return ok


def top_floor_finds(actor: Actor) -> bool:
    """The top floor's bookcases, on the way to Morpheus: the Electronic Lock Pick for the Military Base's door and
    stimpaks for the psychic corridor."""
    if not actor._count(PID_ELECTRONIC_LOCKPICK):
        for tile in TOP_FLOOR_SHELVES:
            actor.loot(tile)
    return actor._count(PID_ELECTRONIC_LOCKPICK) > 0


def lashers_badge(actor: Actor) -> bool:
    """Lasher speaks up when he sees the player (lasher.int); else he is talked to."""
    if actor._count(PID_COC_BADGE):
        return True
    if not win32.wait_for(lambda: actor.snap().screen == "dialogue", 15, 0.25):
        quests.talk(actor, "lasher", LASHER_BADGE, strict=True)
    else:
        dialogue.converse_strict(actor.mem, LASHER_BADGE, log=actor.log)
    time.sleep(1.0)
    return actor._count(PID_COC_BADGE) > 0


def red_door_open(actor: Actor) -> bool:
    door = next((t for t in world.things(actor.mem) if t.tile == RED_DOOR and t.type == "scenery"), None)
    if door is None:
        return False
    unlocked = lambda: not actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED
    ok = unlocked() or actor.use_item_on(PID_COC_BADGE, RED_DOOR, unlocked).ok
    actor.unequip("right")  # the badge left in the active hand turned the next fight's attacks into "use" clicks
    return ok


def stairs(actor: Actor, script: str, arrived) -> bool:
    """Up or down by a staircase: several objects carry its script; a click on one the player stands beside hits
    the player (CHILDRN2 e1, live: a look-at line), so the ones 2+ hexes away go first. From afar the player
    walks up first: the engine's own walk to a clicked staircase stops at a closed door (the cannot-reach message,
    the Cathedral's red door), go_to opens it."""
    from f1 import scripts

    tried: set[int] = set()
    for _ in range(5):
        if arrived():
            return True
        s = actor.snap()
        pieces = [
            t
            for t in world.things(actor.mem)
            if t.type == "scenery" and t.elevation == s.elevation and scripts.script_of(actor.mem, t.address) == script
        ]
        here = [t for t in pieces if t.tile not in tried]
        if not here:
            break
        nearest = min(geometry.distance(t.tile, s.dude.tile) for t in pieces)
        if nearest > 3:
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            ring = {g for t in pieces for g in geometry.ring(t.tile, 2) if g not in obs.blocked}
            nav.go_to(actor, ring, 60)
            s = actor.snap()
        pick = min(
            here, key=lambda t: (geometry.distance(t.tile, s.dude.tile) < 2, geometry.distance(t.tile, s.dude.tile))
        )
        tried.add(pick.tile)
        actor.log.emit("stairs", script=script, tile=pick.tile, at=s.dude.tile)
        targets = frozenset(t.address for t in pieces)  # any piece of the staircase does (MSTRLR12: under a bookcase)
        actor.click_object(pick.tile, arrived, 15, aims=quests.SCENERY_AIMS, targets=targets)
        actor.set_mouse_mode(0)
    return arrived()


CATHEDRAL = [
    Step("robes on", lambda a: wearing(a, PID_ROBES) or a.equip(PID_ROBES, "armor").ok),
    Step("to the Cathedral", lambda a: travel(a, "cathedral", 0).startswith("CHILDRN"), checkpoint=True, tries=6),
    Step("hands empty", hands_empty),
    Step(
        "into the hall",
        lambda a: (
            a.snap().elevation == 1
            or quests.use_scenery(a, ("cocdoor",), lambda: a.snap().elevation == 1, "the Cathedral's door")
        ),
        checkpoint=True,
    ),
    Step("Lasher: the badge", lashers_badge, checkpoint=True),
    Step("the red door", red_door_open, checkpoint=True),
    Step("up the towers", lambda a: stairs(a, "chid2twr", lambda: on_map(a, "CHILDRN2")), checkpoint=True),
    Step("the second floor", lambda a: stairs(a, "ctower2", lambda: a.snap().elevation >= 1)),
    Step("the top floor", lambda a: stairs(a, "ctower3", lambda: a.snap().elevation == 2), checkpoint=True),
    Step("the top floor's shelves", top_floor_finds, checkpoint=True),
    Step(
        "Morpheus, then the Master",
        lambda a: (
            quests.gvar(a, "MASTER_BLOWN") == 1
            or bool(quests.talk(a, "morph", MORPHEUS_TAKE_ME + MASTER_TALK, strict=True))
            and quests.gvar(a, "MASTER_BLOWN") == 1
        ),
        tries=6,  # the last answer is a Speech roll (master17_1); a failed one is a fight with the Master
    ),
    Step("out of the lair", out_of_the_lair, checkpoint=True),
]


OVERSEER_FAREWELL = ["modreact#106"]  # OVER.INT over81: four [More], then [Done]


def the_ending(actor: Actor, limit_s: float | None = None) -> bool:
    """Both blown, on the world map: the game plays the vats' movie and takes the party to Vault 13 by itself (17
    days), V13ENT gives 10000 XP, the slides play (V13CAVE endgame_part1), the Overseer comes out (over81: four [More],
    one [Done]), then the walk away, the credits and the main menu. The Overseer is answered; the movies, the slides
    and the credits are skipped with Space (a key ends each: a Home once cut the vats' movie), 13 minutes
    of a full run (a run may let them play: actions.SKIP_CINEMA). Done at the main menu after the ending
    (a final step: the runner takes that menu for no death)."""
    from f1 import actions

    limit_s = limit_s or (900 if actions.SKIP_CINEMA else 2400)
    from f1 import ending_goals

    if getattr(actor, "ironman", False):
        actor.ending_audit = ending_goals.audit(actor)
        if not actor.ending_audit["ok"]:
            from f1.objectives import ObjectiveOutcome

            return ObjectiveOutcome.unavailable("ending requirements lost: " + ", ".join(actor.ending_audit["missing"]))
    end, last, seen_ending = time.monotonic() + limit_s, None, False
    while time.monotonic() < end:
        try:
            s = actor.snap()
        except ReadError:  # a map loading under the read
            time.sleep(0.5)
            continue
        if (s.screen, s.map_name) != last:
            last = (s.screen, s.map_name)
            actor.log.emit("ending", screen=s.screen, map=s.map_name, xp=s.experience, level=s.level)
        seen_ending = seen_ending or s.screen == "endgame"
        if s.screen == "main_menu":
            if getattr(actor, "ironman", False):
                actor.ending_audit["ending_seen"] = seen_ending
                actor.ending_audit["ok"] = actor.ending_audit["ok"] and seen_ending
                actor.log.emit("ending_evidence", ending_seen=seen_ending, goal_ok=actor.ending_audit["ok"])
            return seen_ending
        if actor.skip_cinema():
            continue
        if s.screen == "dialogue" and win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1):
            dialogue.converse_strict(actor.mem, OVERSEER_FAREWELL, log=actor.log)
        time.sleep(0.5)
    return False
