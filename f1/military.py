"""The Idealist from the Boneyard to the Brotherhood's report (planned from the scripts, without the Hub
and the first Brotherhood stop: the Radio is already carried, Mathia's weapons add nothing to the Turbo). Run from the
kept save idealist-boneyard-done (LAFOLLWR, day 81.7).

    python -m f1.routes idealist_military [--from N] [--until N] [--clock K]

Katja joins on LAFOLLWR (only before day 90) for one thing: the base's door, which she unlocks with no roll (the
Idealist's Lockpick ~45 gives the Electronic Lock Pick ~5 % a try, and the third failure alerts the base).
She is sent away right there, so the Cathedral stays solo. In the base: Robes and empty hands from
the arrival to the exit (Krupper and Flip fight anyone not robed, every mutant fights a drawn gun), the radio trick,
the vats' computer by Science, the two logs (archive 411, 412) before the code (karma +5). The game goes on after the
vats while the Master lives (CE CheckEvents: the movie, +10 000 XP, karma +5). Then Maxson's report: 1500 XP.
"""

import time
from collections.abc import Callable

from f1 import dialogue, geometry, nav, quests, win32, worldmap
from f1.actions import Actor
from f1.boneyard import INVADED_DAY, to_map
from f1.idealist import bos_floor, day, experience, hands_empty, karma
from f1.routes import (
    BY_VCONCOMP,
    GATE_PASS,
    KRUPPER_TALK,
    MB_DOOR,
    PID_ROBES,
    VCONCOMP,
    Step,
    guarded_walk,
    heal_up,
    in_town,
    on_map,
    out_of_the_base,
    out_of_the_bunker,
    past_krupper,
    settle,
    to_the_vats,
    travel,
    vats_computer,
    vats_interface,
    wearing,
)

PID_POWER_ARMOR, PID_RADIO = 3, 100
KATJA_JOIN = [
    "i'm a traveler.",
    "first you tell me who you are.",
    "i'm looking for information. can you help?",
    "i need to know about the area around this city.",
    "i don't plan to stay in this place too much longer, myself.",
    "you're a welcome addition, but the desert's not much more fun.",
]  # KATJA.MSG 105, 109, 116, 130, 145, 149: Katja20, KATJA_STATUS 2, +200 XP
KATJA_LOCK = ["can you give this lock a try?"]  # KATJA.MSG 164 (Katja24 on MBENT): she unlocks MBOUT2IN, no roll
KATJA_LEAVE = ["thanks for your help. you can go now."]  # KATJA.MSG 167: KatjaLeave, KATJA_STATUS 3
RADIO_TRICK = ["help! unknown attackers!"]  # RADIO.MSG 105 (RadioEnt -> Radio07): MBENT map_var 0, +1500 XP
LOG_GREY = ["search logs.", "grey", "yes."]  # VCONCOMP.MSG 104, 129, 134: MASTER_FILLER_7 (archive 411)
LOG_MAXSON = ["search logs.", "maxson", "yes."]  # VCONCOMP.MSG 104, 131, 134: MASTER_FILLER_8 (archive 412)
VATS_CODE = ["display security codes.", "31914-1041-1251514"]  # VCONCOMP.MSG 103, 110: VATS_BLOWN, karma +5
MAXSON_REPORT = [
    "i saw their base. it's crawling with mutants.",
    "i saw the mutant base to the north.",
    "you can't play defense on this one maxson.",
    "i'm not quite sure what we should do about it right now.",
    "thank you.",
]  # MAXSON.MSG 338/339, 340, 342, 350: Maxson23, +1500 XP at the talk's end; never 341 (the Elders)
BASE_SECONDS = 300  # MBENT/MBSTRONG/MBVATS map_update: the player dies at 300 s after the code
FOLLOWERS_SECTION = worldmap.section("boneyard", "LAFOLLWR")


def katja_joins(actor: Actor) -> bool:
    if quests.gvar(actor, "KATJA_STATUS") == 2:
        return True
    if day(actor) >= INVADED_DAY:
        return False
    # chained after the Boneyard's route (a full run) this starts on the world map: back in by the Followers'
    # own entrance (seen by then)
    if actor.snap().screen == "worldmap" and not in_town(actor, travel(actor, "boneyard", FOLLOWERS_SECTION)):
        return False
    if not to_map("LAFOLLWR")(actor):
        return False
    xp0 = experience(actor)
    hands_empty(actor)
    quests.talk(actor, "katja", KATJA_JOIN, strict=True)
    settle(actor, KATJA_JOIN)
    actor.log.emit("katja", status=quests.gvar(actor, "KATJA_STATUS"), xp=experience(actor) - xp0)
    return quests.gvar(actor, "KATJA_STATUS") == 2


def out_of_the_boneyard(actor: Actor) -> bool:
    actor.holster(True)
    return actor.snap().screen == "worldmap" or quests.leave_map(actor)


def to_the_base(actor: Actor) -> bool:
    """Powered Armor on and the Turbo ready on the road (the base's mutants are not met before MBENT)."""
    if on_map(actor, "MBENT"):
        return True
    if actor.snap().screen == "map" and not wearing(actor, PID_POWER_ARMOR) and actor._count(PID_POWER_ARMOR):
        actor.equip(PID_POWER_ARMOR, "armor")
    return travel(actor, "military base", 0).startswith("MBENT")


def robed(actor: Actor) -> bool:
    """Robes in the armour slot and both hands empty, before any mutant comes within 12 hexes."""
    if not wearing(actor, PID_ROBES):
        actor.equip(PID_ROBES, "armor")
    hands_empty(actor)
    actor.log.emit("robed", robes=wearing(actor, PID_ROBES), dialogue=actor.snap().screen == "dialogue")
    if actor.snap().screen == "dialogue":
        dialogue.converse_strict(actor.mem, GATE_PASS, log=actor.log)
    return wearing(actor, PID_ROBES)


def radio_trick(actor: Actor) -> bool:
    """The Radio used on MBENT: 'Help! Unknown attackers!...' sends three of the four guards away, +1500 XP."""
    if quests.mvar(actor, 0) or not actor._count(PID_RADIO):
        return True
    xp0 = experience(actor)
    actor.use_on_self(PID_RADIO)
    if win32.wait_for(lambda: actor.snap().screen == "dialogue", 5, 0.2):
        win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1)
        dialogue.converse_strict(actor.mem, RADIO_TRICK, log=actor.log)
    settle(actor, RADIO_TRICK)
    time.sleep(1.0)
    actor.log.emit(
        "radio", mvar0=quests.mvar(actor, 0), xp=experience(actor) - xp0, alert=quests.gvar(actor, "VATS_ALERT")
    )
    return quests.mvar(actor, 0) == 1 and not quests.gvar(actor, "VATS_ALERT")


def door_locked(actor: Actor) -> bool | None:
    from f1 import world
    from f1.engine_map import Obj

    door = next((t for t in world.things(actor.mem) if t.tile == MB_DOOR and t.type == "scenery"), None)
    if door is None:
        return None
    return bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)


def katja_lock(actor: Actor) -> bool:
    """Beside the door (the mutants answered on the way), then Katja's talk: she walks to the door and unlocks it."""
    if on_map(actor, "MBSTRG12") or door_locked(actor) is False:
        return True
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if not guarded_walk(actor, {t for t in geometry.ring(MB_DOOR, 2) if t not in obs.blocked}):
        return False
    look_at_the_door(actor)
    quests.talk(actor, "katja", KATJA_LOCK, strict=True)
    settle(actor, KATJA_LOCK)
    win32.wait_for(lambda: door_locked(actor) is False, 20, 0.5)
    actor.log.emit("katja_lock", locked=door_locked(actor), alert=quests.gvar(actor, "VATS_ALERT"))
    return door_locked(actor) is False


def look_at_the_door(actor: Actor) -> bool:
    """MBOUT2IN's look_at_p_proc sets MBENT map_var(3) to the door, and Katja offers the lock only with it set
    (Katja24): the arrow cursor rested on the door until the engine looks at it (its "You see" line). Live,
    with the door never looked at, her talk had no lock line."""
    from f1 import geometry as geo
    from f1 import world
    from f1.actions import AIM_GRID, MOUSE_ARROW

    if quests.mvar(actor, 3):
        return True
    door = next((t for t in world.things(actor.mem) if t.tile == MB_DOOR and t.type == "scenery"), None)
    if door is None or not actor.set_mouse_mode(MOUSE_ARROW):
        return False
    actor.center_view(MB_DOOR)
    s = actor.snap()
    cx, cy = geo.tile_center(MB_DOOR, s.camera)
    targets = frozenset({door.address})
    for dx, dy in actor._art_aims(MB_DOOR, targets) + list(AIM_GRID):
        if actor.hovering(cx + dx, cy + dy) == door.address and win32.wait_for(lambda: quests.mvar(actor, 3), 1.5, 0.1):
            break
        actor.hovering(*geo.tile_center(s.dude.tile, s.camera), dy=-24)  # a rest names only a new object
    actor.log.emit("door_looked_at", mvar3=quests.mvar(actor, 3))
    return bool(quests.mvar(actor, 3))


def katja_leaves(actor: Actor) -> bool:
    """Sent away a few hexes from the door: after the lock she stands on its only front hex (21272), and dismissed
    there she stays, and the door answers "You cannot get there." (live). The player walks off first and
    she follows."""
    if quests.gvar(actor, "KATJA_STATUS") != 2:
        return True
    katja = quests.find_critter(actor, "katja")
    if katja is not None and geometry.distance(katja.tile, MB_DOOR) <= 2:
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        spots = {t for r in (6, 7) for t in geometry.ring(MB_DOOR, r) if t not in obs.blocked}
        guarded_walk(actor, spots)
        win32.wait_for(
            lambda: (k := quests.find_critter(actor, "katja")) is None or geometry.distance(k.tile, MB_DOOR) > 2,
            10,
            0.5,
        )
    quests.talk(actor, "katja", KATJA_LEAVE, strict=True)
    settle(actor, KATJA_LEAVE)
    k = quests.find_critter(actor, "katja")
    actor.log.emit("katja_left", status=quests.gvar(actor, "KATJA_STATUS"), tile=k.tile if k else None)
    return quests.gvar(actor, "KATJA_STATUS") == 3


def through_the_door(actor: Actor) -> bool:
    from f1 import world

    if on_map(actor, "MBSTRG12"):
        return True
    door = next((t for t in world.things(actor.mem) if t.tile == MB_DOOR and t.type == "scenery"), None)
    if door is None:
        return False
    through = lambda: on_map(actor, "MBSTRG12")
    actor.click_object(MB_DOOR, through, 20, aims=nav.DOOR_AIMS, targets=frozenset({door.address}))
    return win32.wait_for(through, 10, 0.25)


def the_computer_talk(actor: Actor, plan: list[str], done: Callable[[], bool], steps: int = 3) -> bool:
    """The vats' computer clicked (its talk opens once the interface is up), one plan followed for `steps` nodes."""
    if done():
        return True
    comp = vats_computer(actor)
    if comp is None:
        return False
    talking = lambda: actor.snap().screen == "dialogue"
    if not talking() and not actor.click_object(
        VCONCOMP, talking, 60, aims=quests.SCENERY_AIMS, targets=frozenset({comp.address})
    ):
        return False
    win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1)  # "Command?" before its options (live)
    dialogue.converse_strict(actor.mem, plan, max_steps=steps, log=actor.log)
    time.sleep(0.5)
    return done()


def the_logs(actor: Actor) -> bool:
    """Grey's and Maxson's logs (archive 411, 412), each in its own pass: one plan for both would pick Grey twice."""
    if not on_map(actor, "MBVATS12") or actor.snap().elevation != 1:
        return False
    if actor.snap().dude.tile not in BY_VCONCOMP:
        nav.go_to(actor, BY_VCONCOMP, 80, fight=False)
    grey = lambda: quests.gvar(actor, "MASTER_FILLER_7") == 1
    maxson = lambda: quests.gvar(actor, "MASTER_FILLER_8") == 1
    the_computer_talk(actor, LOG_GREY, grey)
    the_computer_talk(actor, LOG_MAXSON, maxson)
    actor.log.emit("logs", grey=grey(), maxson=maxson())
    return grey() and maxson()


def the_code(actor: Actor) -> bool:
    if quests.gvar(actor, "VATS_BLOWN") == 1:
        return True
    k0 = karma(actor)
    heal_up(actor, 0.95)  # the way out crosses two pain fields against the clock
    the_computer_talk(actor, VATS_CODE, lambda: quests.gvar(actor, "VATS_BLOWN") == 1, steps=4)
    if dialogue.read(actor.mem).active:
        dialogue.close(actor.mem)
    actor.log.emit("vats_code", blown=quests.gvar(actor, "VATS_BLOWN"), countdown=quests.gvar(actor, "VATS_COUNTDOWN"),
                   karma=karma(actor) - k0)  # fmt: skip
    return quests.gvar(actor, "VATS_BLOWN") == 1


def seconds_left(actor: Actor) -> float:
    """VCONCOMP stores the code's moment in game seconds (game_time / 10); the map scripts kill at 300 after it."""
    return BASE_SECONDS - (actor.snap().game_time / 10 - quests.gvar(actor, "VATS_COUNTDOWN"))


def escape(actor: Actor) -> bool:
    """Out of the base by the Agent's walks; on MBENT the Powered Armor goes back on when the clock allows: in Robes
    a road pack of five (four deadly) killed the Idealist while it fled (live; 94 s were left on MBENT
    and the armour waited for 120)."""
    start_left = seconds_left(actor)
    _up_to_mbent(actor)
    if on_map(actor, "MBENT") and actor.snap().screen == "map":
        left = seconds_left(actor)
        if left > 60 and actor._count(PID_POWER_ARMOR) and not wearing(actor, PID_POWER_ARMOR):
            actor.equip(PID_POWER_ARMOR, "armor")
        actor.log.emit("mbent", left=round(left), armor=wearing(actor, PID_POWER_ARMOR))
    out_of_the_base(actor)
    actor.log.emit("escape_done", left_at_start=round(start_left), left=round(seconds_left(actor)),
                   screen=actor.snap().screen)  # fmt: skip
    return actor.snap().screen != "map"


def _up_to_mbent(actor: Actor) -> bool:
    """out_of_the_base up to MBENT only: its MBVATS12 and MBSTRG12 parts (the same walks)."""
    from f1.routes import MB_PAIN, VATS_LIFT, VATS_LIFT_UP, VATS_PAIN

    if on_map(actor, "MBVATS12") and actor.snap().elevation == 1:
        guarded_walk(actor, {VATS_LIFT}, plan=KRUPPER_TALK + GATE_PASS)
        quests.ride_from(actor, VATS_LIFT, "3")
    if on_map(actor, "MBVATS12"):
        guarded_walk(actor, {VATS_LIFT_UP}, VATS_PAIN, KRUPPER_TALK + GATE_PASS, 16)
        quests.ride_from(actor, VATS_LIFT_UP, "1")
    if on_map(actor, "MBSTRG12"):
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        exits = {t for t, (to_map_, _t, _e) in obs.exits.items() if to_map_ == 30}  # 30: MBENT
        guarded_walk(actor, exits, MB_PAIN, legs=16)
        win32.wait_for(lambda: on_map(actor, "MBENT"), 20, 0.25)
        time.sleep(1.0)
    return on_map(actor, "MBENT")


def the_explosion(actor: Actor) -> bool:
    """On the world map with the countdown set: the vats' movie, +10 000 XP (Swift Learner: 10 500), karma +5, the
    countdown cleared (CE CheckEvents; the game goes on while the Master lives). The movie is left to play."""
    xp0, k0 = experience(actor), karma(actor)
    done = lambda: quests.gvar(actor, "VATS_COUNTDOWN") == 0 and quests.gvar(actor, "VATS_BLOWN") == 1
    win32.wait_for(done, 120, 0.5)
    win32.wait_for(lambda: actor.snap().screen == "worldmap", 60, 0.5)
    actor.log.emit(
        "vats_explosion", countdown=quests.gvar(actor, "VATS_COUNTDOWN"), xp=experience(actor) - xp0,
        karma=karma(actor) - k0, destroy_master_4=quests.gvar(actor, "DESTROY_MASTER_4"), screen=actor.snap().screen,
    )  # fmt: skip
    return done() and actor.snap().screen == "worldmap"


def to_the_brotherhood(actor: Actor) -> bool:
    return actor.snap().map_name.startswith("BROH") or in_town(actor, travel(actor, "brotherhood", 0))


def maxson_report(actor: Actor) -> bool:
    xp0 = experience(actor)
    hands_empty(actor)
    quests.talk(actor, "maxson", MAXSON_REPORT, strict=True)
    settle(actor, MAXSON_REPORT)
    gained = experience(actor) - xp0
    actor.log.emit("maxson_report", xp=gained, invasion=quests.gvar(actor, "BROTHERHOOD_INVASION"))
    return gained >= 1500


def armor_back(actor: Actor) -> bool:
    if actor._count(PID_POWER_ARMOR) and not wearing(actor, PID_POWER_ARMOR):
        actor.equip(PID_POWER_ARMOR, "armor")
    actor.ready_weapon()
    actor.holster(True)
    return wearing(actor, PID_POWER_ARMOR) or not actor._count(PID_POWER_ARMOR)


IDEALIST_MILITARY = [
    Step("Katja joins", katja_joins, checkpoint=True),
    Step("out of the Boneyard", out_of_the_boneyard),
    Step("to the Military Base", to_the_base, checkpoint=True, tries=6),
    Step("robes on, hands empty", robed),
    Step("the radio trick", radio_trick, checkpoint=True, optional=True),
    Step("Katja: the lock", katja_lock, checkpoint=True),
    Step("Katja leaves", katja_leaves),
    Step("through the door", through_the_door, checkpoint=True),
    Step("to the vats", to_the_vats, checkpoint=True),
    Step("past Krupper", past_krupper, checkpoint=True),
    Step("the vats' computer", vats_interface),
    Step("the logs", the_logs, checkpoint=True),
    Step("the code", the_code),
    Step("out of the base", escape),
    Step("the vats explode", the_explosion),
    Step("to the Brotherhood", to_the_brotherhood, checkpoint=True, tries=6),
    Step("the Powered Armor on", armor_back),
    Step("to the Elders", bos_floor("BROHD34", 1), checkpoint=True, tries=3),
    Step("Maxson: the report", maxson_report, checkpoint=True),
    Step("out of the bunker", out_of_the_bunker, checkpoint=True, tries=3),
]

ROUTES = {"idealist_military": IDEALIST_MILITARY}
