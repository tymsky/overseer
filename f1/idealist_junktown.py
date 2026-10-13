"""Idealist route tasks for junktown."""

import time

from f1 import dialogue, geometry, nav, quests, win32
from f1.actions import Actor
from f1.agent_junktown import (
    armor_and_gun,
    by_day,
    gang_loot,
    gizmos_confession,
    kenji,
    killians_job,
    recorder_in_hand,
    the_evidence,
    the_raid,
)
from f1.idealist_support import (
    beside,
    day,
    karma,
    rest_until,
    scripted,
)
from f1.route_support import (
    Step,
    hour,
    in_town,
    on_map,
    settle,
    travel,
)

# Junktown (stopping Gizmo, the Skulz; LARS, SHERRY, KILDOOR1, KILLIAN, KENJI scripts)
KILLIAN_FRONT_DOOR = 27483  # JUNKKILL: KILDOOR1 shuts it on every map entry; Kenji comes in through it

# LARS.MSG: Lars00 107 -> Lars05 120 (IQ 6) -> Lars07 123 -> Lars08 sets BUST_SKULZ 1. Only before the confession or
# after the raid: while CAPTURE_GIZMO is 1 his talk is about the raid.
LARS_SKULZ = [
    "#107",
    "#120",
    "#123",
]

# SHERRY.MSG: the first talk ends at once and safely (Sherry02's challenge line is a fight); the second, Sherry15:
# 138 (IQ 5) -> 142 (IQ 6) -> 145 (IQ 6) -> a Speech roll -> Sherry19 147 -> Sherry21 sets SHERRY_TURNS 1 and her
# local_var 6 to the day; more than a day later (critter_p_proc) she leaves the gang, SHERRY_TURNS 2.
SHERRY_MEET = ["@105", "#110"]

SHERRY_TURN = [
    "#138",
    "#142",
    "#145",
    "#147",
]

# Sherry22: 166 (BUST_SKULZ set) -> Sherry27 163 (IQ 6) -> Sherry28 sets GENERIC_FILLER_20 (her testimony); then
# Lars12 139 -> Lars14 145 -> Lars16 sets BUST_SKULZ 2, and his talk gives 500 XP and karma +3
SHERRY_TESTIFY = ["#166", "#163"]

LARS_TESTIMONY = ["#139", "#145"]


def lars_skulz(actor: Actor) -> bool:
    if quests.gvar(actor, "BUST_SKULZ") == 0:
        actor.holster(True)
        quests.talk(actor, "Lars", LARS_SKULZ)
    return quests.gvar(actor, "BUST_SKULZ") >= 1


def sherry_turned(actor: Actor) -> bool:
    """Sherry in the Skulz's back rooms (JUNKKILL): a first talk that only says who the Idealist is, then the one that
    turns her (a Speech roll at 0; a failed one leaves her talk for another try: Sherry20)."""
    if quests.gvar(actor, "SHERRY_TURNS") >= 1:
        return True
    if not on_map(actor, "JUNKKILL"):
        quests.take_exit(actor, "JUNKKILL")
    actor.holster(True)
    for plan in (SHERRY_MEET, SHERRY_TURN, SHERRY_TURN):
        quests.talk(actor, "sherry", plan, strict=True)
        settle(actor)
        if quests.gvar(actor, "SHERRY_TURNS") >= 1:
            return True
    return False


def killians_door(actor: Actor) -> bool:
    """Killian's front door open before his first talk: KILDOOR1 shuts it on every entry, Kenji is placed outside it
    and walks in to 26281, and a talk with the door shut never brought him (the quest to stop Gizmo; the spoilt checkpoint)."""
    if not on_map(actor, "JUNKKILL"):
        quests.take_exit(actor, "JUNKKILL")
    s = actor.snap()
    if KILLIAN_FRONT_DOOR not in nav.obstacles(actor.mem, s.elevation, s.dude.address).doors:
        return True
    beside(actor, KILLIAN_FRONT_DOOR)  # a click from Sherry's rooms, 74 hexes off, walked nowhere (4 tries)
    return nav.open_door(actor, KILLIAN_FRONT_DOOR)


# Junktown's other business (its other quests and Neal's urn)
PID_URN = 112

URN_BAR = 19873  # JUNKCSNO: the Bar runs TROPHY.INT; Steal on it takes the urn

TRISH_HELLO: list[str] = []  # TRISH.INT Trish25 in the bar (16:00-03:30, closed 13:00-16:00) sets TRISH_STATUS 4

# SAUL.MSG: Saul07 107 -> Saul08 113 -> Saul11 136 -> Saul12 142 (IQ 5) -> Saul15 151 -> Saul17 159 (IQ 5, Trish met) ->
# Saul20 rolls Speech: 172 (IQ 6) sets TRISH_STATUS 8, else 173 and another roll (Saul22). The talk's end then adds
# 16 and 250 XP.
SAUL_TRISH = [
    "#107",
    "#113",
    "#136",
    "#142",
    "#151",
    "#159",
    "#172",
    "#173",
]

CRASH_ROOM = (19089, 19289, 19489)  # JUNKKILL: CRASHRM's spatial hexes, room #1

MARCELLE_ROOM = ["#121", "#135/156"]  # 25 caps: RENT_TIME = tomorrow

ROOM_REST = ["#101"]  # CRASHRM: rest until 10:00; CrashRm02 then creates the raider and sets map_var 3 to 1

MARCELLE_HELP = ["#104"]  # Marcelles00 sets SAVE_SINTHIA 1

# JTRAIDER.MSG: phase 1 (he talks on sight): Raider0 105 -> Raider4 111 -> Raider5 114 -> Raider6 (after it, a player
# within 4 hexes is attacked); phase 2, the next talk: Raider9 123 -> Raider14 132 -> a Speech +20 roll sets var6;
# phase 3: Raider21 146 -> Raider22 149 -> Raider24 154, 100 caps -> safe(): map_var 4 (JUNKKILL's
# map_update gives 1000 XP), SAVE_SINTHIA 2. var1, var2 and var6 are script variables: no load between the phases.
HOSTAGE_PHASES = [
    ["#105", "#111", "#114"],
    ["#123", "#132"],
    ["#146", "#149", "#154"],
]

NEAL_URN = ["#144"]  # NEAL.MSG 144 -> Neal11: karma +2


def junktown_map(actor: Actor, name: str) -> bool:
    """To one of Junktown's three maps. The gate (JUNKENT) and the casino (JUNKCSNO) meet only through Killian's
    street (JUNKKILL): from Lars's gate the urn's step asked for the casino and found no exit (five tries)."""
    if not on_map(actor, name) and "JUNKKILL" not in (name, actor.snap().map_name.removesuffix(".SAV")):
        quests.take_exit(actor, "JUNKKILL")
    if not on_map(actor, name):
        quests.take_exit(actor, name)
    return on_map(actor, name)


def trish(actor: Actor) -> bool:
    """Trish at the Skum Pitt's bar, from 16:00: her first words there set TRISH_STATUS bit 4 (Saul's line 159)."""
    if quests.gvar(actor, "TRISH_STATUS") & 5:
        return True
    if not junktown_map(actor, "JUNKCSNO"):
        return False
    if not (hour(actor) >= 16 or hour(actor) < 3) and not rest_until(actor, 16, 20):
        return False
    actor.holster(True)
    quests.talk(actor, "trish", TRISH_HELLO)
    settle(actor)
    return quests.gvar(actor, "TRISH_STATUS") & 5 != 0


def saul(actor: Actor) -> bool:
    """Saul (07:00-20:00), holstered: the talk about Trish; a failed Speech roll leaves him for the next talk. On
    every third day (game days divisible by 3) SAUL.INT's map_update puts him in the ring at 15094, where no path
    leads (a full run, day 21; other days 16892): the next day comes first."""
    for _ in range(3):
        if quests.gvar(actor, "TRISH_STATUS") & 16:
            return True
        for _ in range(5):
            if day(actor) % 3 != 0:
                break
            if not actor.rest("6 hours").ok:
                return False
        if not 7 <= hour(actor) < 20 and not rest_until(actor, 7, 20):
            return False
        actor.holster(True)
        quests.talk(actor, "saul", SAUL_TRISH, strict=True)
        settle(actor)
    return quests.gvar(actor, "TRISH_STATUS") & 16 != 0


def a_room_for_the_night(actor: Actor) -> bool:
    """Room #1 from Marcelles (25 caps), for the night that begins the hostage scene (CRASHRM)."""
    if quests.gvar(actor, "SAVE_SINTHIA") or quests.gvar(actor, "RENT_TIME") > day(actor):
        return True
    if not junktown_map(actor, "JUNKKILL"):
        return False
    actor.holster(True)
    quests.talk(actor, "marcelle", MARCELLE_ROOM, strict=True)
    settle(actor)
    return quests.gvar(actor, "RENT_TIME") > day(actor)


def the_night_in_room_one(actor: Actor) -> bool:
    """Onto room #1's hexes: CRASHRM asks, [Yes] rests until 10:00, and the raider appears beside Sinthia; then
    Marcelles asks for help (she comes to the player once she sees them within 12 hexes)."""
    if quests.gvar(actor, "SAVE_SINTHIA"):
        return True
    nav.go_to(actor, set(CRASH_ROOM), max_steps=80)
    if win32.wait_for(lambda: actor.snap().screen == "dialogue", 6, 0.2):
        dialogue.converse_strict(actor.mem, ROOM_REST, log=actor.log)
    win32.wait_for(lambda: actor.snap().screen == "map", 20, 0.25)
    if win32.wait_for(lambda: actor.snap().screen == "dialogue", 20, 0.25):
        dialogue.converse_strict(actor.mem, MARCELLE_HELP, log=actor.log)
    elif quests.find_critter(actor, "marcelle") is not None:
        quests.talk(actor, "marcelle", MARCELLE_HELP, strict=True)
    settle(actor)
    return quests.gvar(actor, "SAVE_SINTHIA") == 1


def clear_line(a: int, b: int, blocked: frozenset[int]) -> bool:
    """No blocking hex strictly between a and b on the hex line (cube coordinates, rounded)."""
    (q1, r1), (q2, r2) = geometry.axial(a), geometry.axial(b)
    n = geometry.distance(a, b)
    for i in range(1, n):
        q, r = q1 + (q2 - q1) * i / n, r1 + (r2 - r1) * i / n
        s = -q - r
        rq, rr, rs = round(q), round(r), round(s)
        dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
        if dq > dr and dq > ds:
            rq = -rr - rs
        elif dr > ds:
            rr = -rq - rs
        tile = (rr + (rq + (rq & 1)) // 2) * geometry.GRID_WIDTH + rq
        if tile in blocked:
            return False
    return True


def talk_from_afar(actor: Actor, who, plan: list[str], near: int = 5, far: int = 7) -> list | None:
    """Talk to `who` from `near`..`far` hexes on a clear line: inside 9 hexes with nothing in the way the engine
    talks at once, without walking up (fallout1-ce action_talk_to)."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    blocked = obs.blocked - {who.tile}
    spots = {
        t
        for r in range(near, far + 1)
        for t in geometry.ring(who.tile, r)
        if t not in obs.blocked and clear_line(t, who.tile, blocked)
    }
    if s.dude.tile not in spots:
        nav.go_to(actor, spots, max_steps=60)
    if not actor.talk_to(who).ok:
        return None
    return dialogue.converse_strict(actor.mem, plan, log=actor.log)


def the_hostage(actor: Actor) -> bool:
    """The three talks with the raider, from 5-7 hexes, in one go (his phase flags are not saved)."""
    if quests.gvar(actor, "SAVE_SINTHIA") == 2:
        return True
    actor.holster(True)
    for phase, plan in enumerate(HOSTAGE_PHASES):
        who = quests.find_critter(actor, "jtraider")
        if who is None:
            break
        if phase == 0 and win32.wait_for(lambda: actor.snap().screen == "dialogue", 1, 0.2):
            steps = dialogue.converse_strict(actor.mem, plan, log=actor.log)  # he spoke first, on sight
        else:
            steps = talk_from_afar(actor, who, plan)
        actor.log.emit("hostage", phase=phase + 1, talked=steps is not None)
        settle(actor)
        if actor.in_combat() or steps is None:
            return False
        time.sleep(1.0)
    win32.wait_for(lambda: quests.gvar(actor, "SAVE_SINTHIA") == 2, 10, 0.25)
    return quests.gvar(actor, "SAVE_SINTHIA") == 2 and not actor.in_combat()


def neals_urn(actor: Actor) -> bool:
    """The urn off the bar by the Steal skill (TROPHY.INT use_skill_on: the urn always comes, then a Steal roll at 0;
    a failure (its own message) sets map_var 4, caught stealing, and the step fails
    for a reload). A click on the Bar only uses it: the pickup handler that the catalogue read as "by hand, while Neal
    sleeps" runs for items only (gmouse.c), five tries showed it. Neal sleeps 04:10-13:00 (NEAL.INT):
    the try is made before noon."""
    if quests.gvar(actor, "DESTROY_VATS_14") or actor._count(PID_URN):
        return actor._count(PID_URN) > 0 and quests.mvar(actor, 4) == 0
    if not junktown_map(actor, "JUNKCSNO"):
        return False
    if not 4.2 <= hour(actor) < 12 and not rest_until(actor, 4.2, 12):
        return False
    targets = scripted(actor, URN_BAR, "trophy")
    actor.use_skill_on("steal", URN_BAR, lambda: actor._count(PID_URN) > 0, targets=targets)
    caught = quests.mvar(actor, 4) != 0
    actor.log.emit("urn", taken=actor._count(PID_URN) > 0, caught=caught)
    return actor._count(PID_URN) > 0 and not caught


def urn_returned(actor: Actor) -> bool:
    """Neal talks from 16:00 to 04:10: NEAL.MSG 144 -> karma +2. NEAL.INT talk_p_proc floats
    "Zzzz" from 04:10 to 13:00 and its closing-hours line from 13:00 to 16:00; both read live
    by f1.floats, where this step had "after 12:00" and failed five times. A later run got
    past it from those words, resting to 14:00, 16:00, then 18:00."""
    k0 = karma(actor)
    if not actor._count(PID_URN):
        return False
    if not junktown_map(actor, "JUNKCSNO"):
        return False
    if not 16 <= hour(actor) < 23 and not rest_until(actor, 16, 23):
        return False
    actor.holster(True)
    quests.talk(actor, "neal", NEAL_URN, strict=True)
    settle(actor)
    return karma(actor) >= k0 + 2


def sherry_testimony(actor: Actor) -> bool:
    """Two days after she turned, Sherry has left the gang (SHERRY_TURNS 2) and testifies (GENERIC_FILLER_20)."""
    if quests.gvar(actor, "GENERIC_FILLER_20"):
        return True
    if not junktown_map(actor, "JUNKKILL"):
        return False
    for _ in range(4):
        if quests.gvar(actor, "SHERRY_TURNS") == 2:
            break
        actor.rest("6 hours")
    rest_until(actor, 7, 19)  # she is awake 06:30-20:00: three rests of 6 h had left 04:01
    actor.holster(True)
    quests.talk(actor, "sherry", SHERRY_TESTIFY, strict=True)
    settle(actor)
    return quests.gvar(actor, "GENERIC_FILLER_20") != 0


def lars_testimony(actor: Actor) -> bool:
    """Lars takes Sherry's testimony: BUST_SKULZ 2, then 500 XP and karma +3 at his talk."""
    k0 = karma(actor)
    if not junktown_map(actor, "JUNKENT"):
        return False
    actor.holster(True)
    quests.talk(actor, "Lars", LARS_TESTIMONY, strict=True)
    settle(actor)
    if quests.gvar(actor, "BUST_SKULZ") == 2 and karma(actor) < k0 + 3:  # the reward may wait for his next talk
        quests.talk(actor, "Lars", [], strict=True)
        settle(actor)
    return quests.gvar(actor, "BUST_SKULZ") == 2 and karma(actor) >= k0 + 3


IDEALIST_JUNKTOWN = [
    Step("to Junktown", lambda a: in_town(a, travel(a, "junktown", 0)), checkpoint=True, tries=6),
    Step("by day", by_day, checkpoint=True),
    Step("Lars: the Skulz", lars_skulz),
    Step("Sherry turned", sherry_turned, checkpoint=True, tries=5),
    Step("Killian's front door open", killians_door),
    Step("Kenji", kenji, checkpoint=True, tries=4),
    Step("Kenji's things", lambda a: quests.loot_the_dead(a, ("Kenji",)) >= 0),
    Step("Killian's job", killians_job),
    Step("the bug and the recorder in hand", recorder_in_hand),
    Step(
        "to Gizmo's casino",
        lambda a: on_map(a, "JUNKCSNO") or (by_day(a) and quests.take_exit(a, "JUNKCSNO")),
        checkpoint=True,
    ),
    Step("Gizmo's confession", gizmos_confession, checkpoint=True),
    Step("back to Killian", lambda a: on_map(a, "JUNKKILL") or (by_day(a) and quests.take_exit(a, "JUNKKILL"))),
    Step("Killian: the evidence", the_evidence),
    # not the Hunting Rifle off Killian's shelves: his store's shelves are his stock; a use of them opens his barter as
    # for a theft, and he and the gate guards attack (twice: HP 42 to 3)
    Step("leather armor on, the gun in hand", armor_and_gun, checkpoint=True),
    Step("the raid on Gizmo", the_raid, checkpoint=True, tries=4),
    Step("the gang's things", gang_loot, checkpoint=True),
    Step("Trish at the bar", trish, checkpoint=True),
    Step("Saul and Trish", saul, checkpoint=True, tries=4),
    Step("a room for the night", a_room_for_the_night),
    Step("the night in room #1", the_night_in_room_one, checkpoint=True, tries=4),
    Step("Sinthia: the hostage talked down", the_hostage, checkpoint=True, tries=5),
    # stealing Neal's urn to give it back is +2 karma for nothing (Junktown's scripts): an exploit
    Step("Neal's urn off the bar", neals_urn, checkpoint=True, tries=1, optional=True, exploit=True),
    Step("Sherry testifies", sherry_testimony, checkpoint=True, tries=4),
    Step("Lars: the Skulz busted", lars_testimony, checkpoint=True, tries=4),
    Step("Neal's urn returned", urn_returned, checkpoint=True, tries=4, exploit=True),
]
