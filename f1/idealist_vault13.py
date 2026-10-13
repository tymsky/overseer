"""Idealist route tasks for vault13."""

import time

from f1 import clock, dialogue, loot, nav, quests, watchdog, win32
from f1.actions import Actor
from f1.agent_necropolis import (
    hand_in,
)
from f1.idealist_sands import (
    theresa,
)
from f1.idealist_support import (
    DAY,
    experience,
    rest_until,
)
from f1.route_support import (
    Step,
    hour,
    settle,
    travel,
)

# Vault 13 with the chip (planned with Necropolis): Lyle starts the water thief (day 30 or later, 07:10-19:20),
# Theresa calms the rebels (Speech 0: sure at 121; not 17:00-17:10, her meeting), the thief caught at midnight on
# the Overseer's floor (WTRTHIEF: he walks from 16912 east and back, taking a flask by the box 20506; the search
# is a CH check, 30 %, a failure is his fight: either way WATER_THIEF 2), and the hand-in last (OVER over28 sets CALM_REBELS and
# WATER_THIEF to 3 unless each is 2).
LYLE_THIEF = ["#105", "#110"]  # LYLE.MSG 105, 110

THIEF_TALK = [
    "#109",
    "#114",
    "#124",
    "#121",
]  # WTRTHIEF.MSG 109, 114, 124, 121: the search, do_check(CH): its outcome stands

THIEF_POST, THIEF_BOX = 16912, 20506  # the footlocker; he turns back at 22728 and holds the flask from 20706 (live)

PID_WATER_FLASK = 126

# e2, beside 20706 where he takes the flask, 24+ hexes from 16912 (out of his notice, PE 4): the talk needs no
# chase (a click on him walking by did not open it in 8 s, live)
THIEF_HIDE = (20506, 20705, 20707, 20905, 20906, 20907)


def lyles_worry(actor: Actor) -> bool:
    if quests.gvar(actor, "WATER_THIEF") >= 1:
        return True
    if actor.snap().game_time < 30 * DAY:
        return False
    if actor.snap().elevation != 1 and not quests.use_elevator(actor, "2"):
        return False
    rest_until(actor, 7.5, 16.9)
    actor.holster(True)
    quests.talk(actor, "lyle", LYLE_THIEF, strict=True)
    settle(actor)
    return quests.gvar(actor, "WATER_THIEF") >= 1


def theresa_now(actor: Actor) -> bool:
    if quests.gvar(actor, "CALM_REBELS") == 2:
        return True
    rest_until(actor, 7.5, 16.9)  # her hours, and not her rebels' meeting at 17:00
    return theresa(actor)


def carrying(actor: Actor, critter) -> bool:
    """The thief holds a Water Flask: he has taken from the box (WTRTHIEF: add_obj_to_inven pid 126)."""
    return any(p == PID_WATER_FLASK for _, p, _ in loot.inventory(actor.mem, critter.address))


def the_water_thief(actor: Actor) -> bool:
    """On the Overseer's floor by midnight, out of the thief's sight; talked to while he carries the flask.

    Watched live (runs/thief-watch): at 00:00 he appears at 17512, walks to 22728 and back; passing
    20706 (the box 20506 within 4) he takes a Water Flask; about 14 game seconds later he is at 16912 and gone to
    7000. Searched before the flask: an empty find. So the watch runs at the game's own speed after midnight,
    and the talk is clicked the moment he holds the flask."""
    if quests.gvar(actor, "WATER_THIEF") >= 2:
        return True
    if actor.snap().elevation != 2 and not quests.use_elevator(actor, "3"):
        return False
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    nav.go_to(actor, {t for t in THIEF_HIDE if t not in obs.blocked}, max_steps=60)
    if not (0 <= hour(actor) < 0.5 or hour(actor) >= 21):
        # his script forgets last night's theft (var3, var4) only in a tick after 06:00, and a rest runs no ticks
        if not 7 <= hour(actor) < 18:
            actor.rest("until noon")
        with clock.fast(actor.pid):
            time.sleep(3.0)
    if hour(actor) >= 0.5:
        actor.rest("until midnight")  # rest_until's 6-hour steps leapt over the half hour (live)
    x0 = experience(actor)
    end = actor.snap().game_time + 36000 // 2  # half an hour of game time, at the game's own speed
    thief = None
    while actor.snap().game_time < end:
        watchdog.quiet(15)
        thief = quests.find_critter(actor, "wtrthief")
        if thief is not None and carrying(actor, thief):
            break
        time.sleep(0.1)
    actor.log.emit("thief", seen=thief is not None, at=thief.tile if thief else None)
    if thief is None or not carrying(actor, thief):
        return False
    actor.holster(True)
    talked = actor.talk_to(thief, timeout_s=8).ok
    if talked:
        dialogue.converse_strict(actor.mem, THIEF_TALK, log=actor.log)
    settle(actor)
    if actor.in_combat():  # the search failed (CH 3: 30 %): his fight, which completes it too (500 XP)
        actor.ready_weapon()
        actor.fight()
    win32.wait_for(lambda: quests.gvar(actor, "WATER_THIEF") == 2, 10, 0.25)
    actor.log.emit("thief", talked=talked, water_thief=quests.gvar(actor, "WATER_THIEF"), xp=experience(actor) - x0)
    return quests.gvar(actor, "WATER_THIEF") == 2


def chip_to_the_overseer(actor: Actor) -> bool:
    if quests.gvar(actor, "FIND_WATER_CHIP") == 2:
        return True
    if actor.snap().elevation != 2 and not quests.use_elevator(actor, "3"):
        return False
    hour_now = hour(actor)
    if not 7 <= hour_now < 19:
        rest_until(actor, 8, 18)
    actor.holster(True)
    return hand_in(actor)


IDEALIST_VAULT13 = [
    Step("to Vault 13", lambda a: travel(a, "vault 13", 3).startswith("VAULT13"), checkpoint=True, tries=6),
    Step("Lyle: the water thief", lyles_worry, checkpoint=True),
    Step("Theresa calms the rebels", theresa_now, checkpoint=True, tries=1, optional=True),  # one roll, sure at 121
    # not optional: the hand-in after it ends the thief for good (WATER_THIEF 3); a search that fails is his fight,
    # which completes it too
    Step("the water thief at midnight", the_water_thief, checkpoint=True, tries=2),
    Step("the chip to the Overseer", chip_to_the_overseer, checkpoint=True),
]
