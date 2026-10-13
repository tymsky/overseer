"""Agent route tasks for junktown."""

import time

from f1 import clock, quests, win32
from f1.actions import PID_STIMPAK, Actor
from f1.route_support import (
    TOWN_GREETINGS,
    Step,
    hour,
    in_town,
    on_map,
    settle,
    travel,
)

PID_LEATHER_ARMOR, PID_BUG, PID_TAPE_RECORDER, PID_CAPS = 0x01, 0x39, 0x68, 0x29

KILLIAN_JOB = ["#106", "#201", "#211", "#215"]  # KILLIAN.MSG

GIZMO_CONFESSION = [  # GIZMO.MSG, with the tape recorder in hand
    "#169",
    "#171",
    "#177",
    "#181",
    "#184",
    "#187",
    "#192",
    "#195",
]

KILLIAN_EVIDENCE = [
    "#221",
    "#225",
    "#277",
    "#232",
    "#236",
]

LARS_RAID = ["#160", "#160/161", "#156"]  # LARS.MSG 160, 156

GIZMO_GANG = ("Gizmo", "Izo", "Thug")

ARADESH_TANDI = ["#229", "#232/239", "#236"]  # ARADESH.MSG 229-236

GARL_FREE = ["#131", "#141"]  # GARL.MSG 131, 141

ARADESH_REWARD = ["#244/151/167/181/182/184/187/195/211/212/217/240/243"]  # after ARADESH.MSG 242, the reward


def into_killians_store(actor: Actor) -> bool:
    """Through Junktown's gate (by night its guard stops strangers: the polite line) into Killian's store, by day: at
    night Killian, seeing the player within 12 hexes, starts his intruder talk himself (KILLIAN.INT critter_p_proc,
    19:00 to 6:00), and after a night entry (a full run at Full HD) his first talk by day never brought Kenji
    (Killian01 sets map_var 5, and Kenji comes only with local_var 7 clear; which one the night spoiled: not read)."""
    if not on_map(actor, "JUNKKILL"):
        by_day(actor)
    for _ in range(4):
        if on_map(actor, "JUNKKILL"):
            return True
        if actor.snap().screen == "dialogue" and not settle(actor, TOWN_GREETINGS):
            return False
        actor.holster(True)
        if actor.snap().screen == "map":
            quests.take_exit(actor, "JUNKKILL")
    return on_map(actor, "JUNKKILL")


def kenji(actor: Actor) -> bool:
    """By day (the store is locked at night, and Killian sleeps: by_day), Killian's first talk; Gizmo's assassin Kenji
    comes in shooting after it: the gun out, a fight against him alone. Done once Kenji came and is gone. At 6 in the
    morning the talk failed (Killian asleep), Kenji never came, and "no Kenji here" passed the step (a full run at
    Full HD): without the fight Killian never thanks the Agent and offers the job (KILLIAN.INT Killian47)."""
    by_day(actor)
    quests.talk(actor, "Killian Darkwater", ["#107/121"])
    actor.holster(False)
    fought = win32.wait_for(actor.in_combat, 45, 0.25)
    if fought:
        actor.fight(only=("Kenji",))
    time.sleep(1.5)
    settle(actor, KILLIAN_JOB)  # Killian speaks up after the fight and offers the job (a chain run)
    return fought and quests.find_critter(actor, "Kenji") is None and not actor.in_combat()


def killians_job(actor: Actor) -> bool:
    if quests.gvar(actor, "HIRED_BY_KILLIAN") == 0:
        actor.holster(True)
        quests.talk(actor, "Killian Darkwater", KILLIAN_JOB, strict=True)
    return quests.gvar(actor, "HIRED_BY_KILLIAN") != 0 and actor._count(PID_TAPE_RECORDER) > 0


def recorder_in_hand(actor: Actor) -> bool:
    return actor.equip(PID_BUG, "left").ok and actor.equip(PID_TAPE_RECORDER, "right").ok


def gizmos_confession(actor: Actor) -> bool:
    if quests.gvar(actor, "GOT_CONFESSION") == 0:
        recorder_in_hand(actor)  # a fight on the way readies a gun in their place
        quests.talk(actor, "Gizmo", GIZMO_CONFESSION, strict=True)
    return quests.gvar(actor, "GOT_CONFESSION") != 0


def the_evidence(actor: Actor) -> bool:
    if actor._count(PID_LEATHER_ARMOR) == 0:
        by_day(actor)
        quests.talk(actor, "Killian Darkwater", KILLIAN_EVIDENCE, strict=True)
    return actor._count(PID_LEATHER_ARMOR) > 0


def armor_and_gun(actor: Actor) -> bool:
    return actor.equip(PID_LEATHER_ARMOR, "armor").ok and actor.ready_weapon().ok


def the_raid(actor: Actor) -> bool:
    """Lars takes the guards (and the Agent) into Gizmo's casino; the fight is against Gizmo's people only. Lars
    pays 500 caps afterwards."""
    if quests.gvar(actor, "GIZMO_DEAD") == 0:
        if not on_map(actor, "JUNKENT"):
            quests.take_exit(actor, "JUNKENT")
        quests.talk(actor, "Lars", LARS_RAID, strict=True)
        actor.holster(False)
        if win32.wait_for(actor.in_combat, 20, 0.25):
            actor.fight(only=GIZMO_GANG)
    return quests.gvar(actor, "GIZMO_DEAD") != 0 and not actor.in_combat()


def gang_loot(actor: Actor) -> bool:
    """Gizmo's people carried guns and ammunition (the routes use up theirs): into the casino, take it all."""
    for hop in ("JUNKKILL", "JUNKCSNO"):  # the gate has no exit to the casino: through Killian's street
        if not on_map(actor, "JUNKCSNO") and not on_map(actor, hop):
            quests.take_exit(actor, hop)
    taken = quests.loot_the_dead(actor, GIZMO_GANG)
    actor.log.emit("loot", bodies=taken)
    return on_map(actor, "JUNKCSNO")


JUNKTOWN = [
    Step("to Junktown", lambda a: in_town(a, travel(a, "junktown", 0)), checkpoint=True, tries=6),
    Step("into Killian's store", into_killians_store, checkpoint=True),
    Step("Kenji", kenji, checkpoint=True, tries=4),
    Step("Kenji's things", lambda a: quests.loot_the_dead(a, ("Kenji",)) >= 0),  # his Hunting Rifle
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
    Step("leather armor on, the gun in hand", armor_and_gun, checkpoint=True),
    Step("the raid on Gizmo", the_raid, checkpoint=True, tries=4),
    Step("the gang's things", gang_loot, checkpoint=True),
]


def by_day(actor: Actor) -> bool:
    """Shops are locked at night (Killian's door), from within too: a checkpoint's rest to heal (39 h at level 5)
    left the Agent in Killian's store at 2 in the night, the way to Gizmo's casino shut (a full run). Rest
    until morning first. Killian keeps to his bed from 19:00 to 7:30 (KILLIAN.INT critter_p_proc) and walks to his
    counter after: at 6 a click on him only looked at him (twice, once at Full HD), at 8:01 he was still by
    his bed. So a morning rests until noon."""
    ok, rested = True, False
    if hour(actor) < 6 or hour(actor) >= 19:
        ok, rested = actor.rest("until morning").ok, True
    if 6 <= hour(actor) < 9:
        ok, rested = actor.rest("until noon").ok and ok, True
    if rested:
        # the doors unlock only in a map update after 6:00 (KILDOOR1/2 map_update_p_proc), and a rest that ends at
        # noon runs none: an ironman run stood locked in at noon, two tries 20 s apart. The updates come with the game
        # time queue (a game minute apart, as read from CE; not measured here): 65 game seconds pass first
        t0 = actor.snap().game_time
        with clock.fast(actor.pid):
            win32.wait_for(lambda: actor.snap().game_time >= t0 + 650, 90, 0.5)
    return ok


def stimpaks_from_killian(actor: Actor, quantity: int = 3) -> bool:
    from f1 import barter

    have = actor._count(PID_STIMPAK)
    out = barter.buy(actor, "Killian Darkwater", ["#104/118"], PID_STIMPAK, quantity)
    actor.log.emit("shopping", **out)
    return actor._count(PID_STIMPAK) > have


SHOPPING = [
    Step(
        "to Killian's store",
        lambda a: in_town(a, travel(a, "junktown", 1)) and on_map(a, "JUNKKILL"),
        checkpoint=True,
        tries=6,
    ),
    Step("by day", by_day),
    Step("stimpaks from Killian", stimpaks_from_killian, checkpoint=True),
]


# JTRAIDER.MSG: the crazed raider holding Sinthia in the Crash House; the talking way out (Speech lines), no fight
HOSTAGE_TALK = [
    "#105",
    "#111",
    "#114",
    "#132",
    "#140",
    "#141",
    "#150",
    "#159",
]


MARCELLE_HELP = ["#104", "#109"]  # MARCELLE.MSG 104, 109


def sinthia(actor: Actor) -> bool:
    """Marcelle (the Crash House's owner) asks for help, then the raider with the JTRAIDER script is talked down."""
    before = quests.gvar(actor, "SAVE_SINTHIA")
    actor.holster(True)
    if quests.find_critter(actor, "marcelle") is not None:
        quests.talk(actor, "marcelle", MARCELLE_HELP, strict=True)
    steps = quests.talk(actor, "jtraider", HOSTAGE_TALK, strict=True)
    time.sleep(2.0)
    actor.log.emit("sinthia", before=before, after=quests.gvar(actor, "SAVE_SINTHIA"), talked=steps is not None)
    return quests.gvar(actor, "SAVE_SINTHIA") != before and not actor.in_combat()


SINTHIA = [
    Step("to Junktown", lambda a: in_town(a, travel(a, "junktown", 0)), checkpoint=True, tries=6),
    Step("the hostage talked down", sinthia, checkpoint=True, tries=4),
]
