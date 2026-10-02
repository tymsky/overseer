"""Routes: a quest as data, run unattended. Each step is a verified action with its own check; after a checkpoint
step the game is saved (slot 1), and a failed step (or a death) loads the last checkpoint and goes on from the step
after it, a few times at most.

python -m f1.routes start           from the game's start (the kept save agent-start): out of the cave, Shady
                                    Sands' radscorpion quest (Aradesh, Seth, the caves), level 2's points, the
                                    Overseer's supplies (the kept save v13-supplies was made there)
python -m f1.routes water_chip      from Vault 13 (after the Overseer's supplies, or any point before Necropolis):
                                    Necropolis, the sewers, past Harry, Vault 12's third floor, the chip, back to
                                    the Overseer. Events in runs/<time>-route/events.jsonl
python -m f1.routes junktown        from anywhere after the chip: Kenji, Killian's job, Gizmo's confession on tape,
                                    leather armor, the raid with Lars (500 caps)
python -m f1.routes tandi           Aradesh's missing daughter: Garl talked down at the Khans', Tandi home (500 caps)
python -m f1.routes cathedral       in Robes with Vree's disk: Lasher's badge, the towers, Morpheus, the Master
                                    talked into his end, the timed escape (240 game seconds)
python -m f1.routes vats            in Robes with the Electronic Lock Pick: the Military Base's door, the lifts,
                                    Krupper, the vats' computer (Science, then the code), the escape (300 s)
python -m f1.routes idealist_start  the best-outcome run's first route, from the kept save idealist-start (f1/idealist.py)
python -m f1.routes start water_chip       routes run one after another, as one list of steps
python -m f1.routes ROUTE... --from N      resume at step N (slot 1 must hold the checkpoint before it)
python -m f1.routes ROUTE... --until N     stop before step N (its checkpoint then in slot 1: a save to keep)
python -m f1.routes ROUTE... --clock K     a test run: the game K times as fast while the agent waits (f1.clock)
"""

import contextlib
import datetime
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from f1 import (
    chargen,
    clock,
    dialogue,
    flows,
    geometry,
    knowledge,
    loot,
    nav,
    paths,
    quests,
    scripts,
    session,
    state,
    watchdog,
    win32,
    world,
)
from f1.actions import PID_STIMPAK, Actor
from f1.engine_map import Obj
from f1.memory import ReadError
from f1.telemetry import EventLog

PID_WATER_CHIP = 0x37
GVAR_FIND_WATER_CHIP = 101
CELL_MANHOLE = 18658  # the Watershed cell whose manhole leads to the isolated sewer part above Vault 12
WORKING_COMPUTER = 25270  # Vault 12, third floor: the only computer with the use flag
V12_DOORS = {0: 13504, 2: 17108}  # Vault 12's elevator doors by elevation (the cab floor is the door tile - 200)
HARRY_PEACE = ["but i am a ghoul", "obvious", "i'll be leaving now", "just passing through", "have a good day"]
OVERSEER_CHIP = ["yes.", "it was nothing", "you're welcome", "ok.", "fair enough"]
GVAR_NUM_RADSCORPIONS, GVAR_RADSCORPION_SEED = 2, 43
SETH_POST = 14108  # Shady Sands' gate (SHADYW), where Seth stands; his proto is a plain "Peasant"
# ARADESH.MSG: the first talk ends after the greeting (104, 115: "I shall believe you...for now"); the next one
# asks (153 or 160), then 166 -> Ara_28 -> 197 -> Ara_28a -> 199 (the quest)
ARADESH_SCORPIONS = [
    "mean you no harm",
    "missed my tiny village",
    "it was pretty tough",
    "what's going on around here",
    "go on.",
    "i'll help you get rid of these things",
    "can i ask you some questions",
    "ask you a few questions",
]
SETH_CAVES = ["take me to the radscorpion caves", "okay, take me to the radscorpions", "yes."]  # SETH.MSG 118, 323, 126
ARADESH_NEST = ["i destroyed the nest"]  # ARADESH.MSG 209 -> Ara_32 "You are truly a hero!"
# OVER.MSG: Over_12 "do you have the chip?" only sighs (122); Over_19a leads to Over_22 -> 139 -> the supplies
OVERSEER_SUPPLIES = ["sorry, haven't been able to track it down", "i could use some more stuff", "not yet. sorry"]


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[Actor], bool]
    checkpoint: bool = False  # save after it succeeds
    tries: int = 3  # tries in place after failures, and loads of the last checkpoint after deaths
    final: bool = False  # the game's end: the main menu after it is no death
    optional: bool = False  # its failure stands (a roll's first outcome): skipped, and the route goes on
    exploit: bool = False  # a trick past the game's rules; run only for a character whose build allows exploits


def heal_up(actor: Actor, below: float = 0.75) -> bool:
    """Rest until healed when HP is low and the Agent is on a map (not near foes: the game refuses then). Long
    trips at level 3 lost four fights in a row on the way from Necropolis to Vault 13 after Vault 12."""
    s = actor.snap()
    low = s.screen == "map" and s.dude is not None and s.dude.hp < below * actor.max_hp and not actor.in_combat()
    if low:  # the spare stimpaks first: a rest costs days at a low healing rate (quests.spare_stimpaks)
        quests.spare_stimpaks(actor, actor.max_hp)
        low = actor.snap().dude.hp < below * actor.max_hp
    if low and not actor.rest("until healed").ok:  # no rest inside the Military Base (the Pip-Boy has no button)
        win32.wait_for(lambda: actor.snap().screen == "map", 5, 0.2)
        heal_below(actor, int(below * actor.max_hp))
        if actor.snap().dude.hp < below * actor.max_hp and actor._count(PID_FIRST_AID_KIT):
            actor.use_on_self(PID_FIRST_AID_KIT)
    return True


def travel(actor: Actor, town: str, section: int = 0, flee_all: bool = False) -> str:
    """quests.go without its own reloads: a death on the road fails the step and the runner reloads (and redoes
    the steps since its last checkpoint). Heals first while still on a map, and takes up the best weapon (one bought
    or looted since, or the other when this one has no rounds left)."""
    heal_up(actor)
    if actor.snap().screen == "map" and not actor.in_combat():
        actor.ready_weapon()
    if not flee_all:  # nothing to shoot with (a spear, fists): the road's fights are fled, not fought
        from f1 import weapons

        best = next(iter(actor.weapon_options()), None)
        flee_all = best is None or not weapons.uses_ammo(best.pid) or not best.rounds
    return quests.go(actor, town, section, recover=False, flee_all=flee_all)


def on_map(actor: Actor, name: str) -> bool:
    return name.upper() in actor.snap().map_name


def elevation_becomes(actor: Actor, what: str, names: tuple[str, ...], elevation: int, map_name: str = "") -> bool:
    done = lambda: actor.snap().elevation == elevation and (not map_name or on_map(actor, map_name))
    return done() or quests.use_scenery(actor, names, done, what)


def past_harry(actor: Actor) -> bool:
    """Walk to the cell manhole; Harry gets only peaceful answers; a fight means failure (the runner reloads)."""
    for _ in range(6):
        s = actor.snap()
        if actor.in_combat():
            return False
        if s.screen == "dialogue":
            steps = dialogue.converse_strict(actor.mem, HARRY_PEACE, log=actor.log)
            time.sleep(2.0)
            if actor.in_combat() or steps is None:
                return False
            continue
        if geometry.distance(s.dude.tile, CELL_MANHOLE) <= 1:
            return True
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        goals = {t for t in geometry.ring(CELL_MANHOLE, 1) if t not in obs.blocked}
        nav.go_to(actor, goals, max_steps=40, fight=False)
    return geometry.distance(actor.snap().dude.tile, CELL_MANHOLE) <= 1 and not actor.in_combat()


TREAD_REACH = 7  # TREAD.INT: a Glowing One attacks one it sees within 6 hexes ("You tread without permission.")


def away_from_treaders(actor: Actor) -> frozenset[int]:
    """The hexes within reach of Vault 12's guarding Glowing One (script tread). Its fight draws in the Mad Glowing
    One (VALTGLO: 60 HP, ignores the player out of combat): a full run lost four tries at HP 22 there."""
    s = actor.snap()
    guards = [
        c
        for c in state.critters(actor.mem)
        if not c.dead and c.elevation == s.elevation and scripts.script_of(actor.mem, c.address) == "tread"
    ]
    return frozenset(t for c in guards for r in range(TREAD_REACH + 1) for t in geometry.ring(c.tile, r))


def rested_above(actor: Actor) -> bool:
    """Vault 12's ground floor below 75 % HP: the stimpaks there are, if any. No rest is possible in Necropolis'
    underground: the Pip-Boy says "You can not rest at this location!" (PIPBOY.MSG 215) in Vault 12, in the sewer
    above it and in the Watershed's cell level (all three measured live in a full run). So the healing has
    to come before the sewers (the runner's checkpoints: a pistol in hand, not a knife, costs fewer HP there), and a
    wounded Agent fights the guarding Glowing One as it is (the Mad Glowing One its fight draws in: 60 HP)."""
    s = actor.snap()
    if on_map(actor, "VAULTNEC") and s.elevation == 0 and s.dude.hp < 0.75 * actor.max_hp:
        heal_below(actor, int(0.75 * actor.max_hp))
        actor.log.emit("rested_above", hp=actor.snap().dude.hp, stimpaks=actor._count(PID_STIMPAK))
    return True


def take_chip(actor: Actor) -> bool:
    if actor._count(PID_WATER_CHIP):
        return True
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    nav.go_to(actor, {t for r in (1, 2) for t in geometry.ring(WORKING_COMPUTER, r) if t not in obs.blocked}, 60)
    aims = [(0, -10), (0, -20), (0, 0), (8, -15), (-8, -15), (0, -30), (12, -10), (-12, -10), (0, -40)]
    return actor.click_object(WORKING_COMPUTER, lambda: actor._count(PID_WATER_CHIP) > 0, 25, aims=aims)


def out_of_watershed(actor: Actor) -> bool:
    for _ in range(6):
        s = actor.snap()
        if s.screen == "worldmap":
            return True
        if actor.in_combat():
            return False
        if s.screen == "dialogue":
            if dialogue.converse_strict(actor.mem, HARRY_PEACE, log=actor.log) is None:
                return False
            time.sleep(2.0)
            continue
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t, d in obs.exits.items() if d[0] < 0}, max_steps=40, fight=False)
        win32.wait_for(lambda: actor.snap().screen == "worldmap", 10, 0.25)
    return actor.snap().screen == "worldmap"


def hand_in(actor: Actor) -> bool:
    quests.talk(actor, "Vault Overseer", OVERSEER_CHIP)
    return quests.gvar(actor, GVAR_FIND_WATER_CHIP) == 2


def scorpion_quest(actor: Actor) -> bool:
    """Aradesh's first talk only lets the stranger in; the quest comes in the next one."""
    for _ in range(3):
        if quests.gvar(actor, GVAR_RADSCORPION_SEED) >= 1:
            return True
        quests.talk(actor, "Aradesh", ARADESH_SCORPIONS)
    return quests.gvar(actor, GVAR_RADSCORPION_SEED) >= 1


def seth_to_the_caves(actor: Actor) -> bool:
    if not on_map(actor, "CAVES"):
        quests.talk(actor, "Peasant", SETH_CAVES, near=SETH_POST)
    return win32.wait_for(lambda: on_map(actor, "CAVES") and actor.snap().screen == "map", 30, 0.5)


def clear_the_caves(actor: Actor) -> bool:
    if quests.gvar(actor, GVAR_NUM_RADSCORPIONS) > 0:
        quests.hunt(actor, "Radscorpion")
    return quests.gvar(actor, GVAR_NUM_RADSCORPIONS) == 0


def nest_reported(actor: Actor) -> bool:
    """The nest's reward from Aradesh. Once the raiders have taken Tandi he talks of nothing else, and the talk takes
    up her rescue (RESCUE_TANDI; the tandi route goes on from there): the road's mantises took every stimpak, the
    rest after the caves ran 102 h, and she was gone (a full run at Full HD)."""
    if quests.gvar(actor, "RESCUE_TANDI"):
        return True
    steps = quests.talk(actor, "Aradesh", ARADESH_NEST) or []
    return any("truly a hero" in reply for reply, _options, _chosen in steps) or quests.gvar(actor, "RESCUE_TANDI") != 0


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


PID_LEATHER_ARMOR, PID_BUG, PID_TAPE_RECORDER, PID_CAPS = 0x01, 0x39, 0x68, 0x29
DAY = 864000  # game_time ticks (0.1 s) a day
JUNKTOWN_GATE = ["i'm sorry to disturb you, sir"]  # ASSBLOW.MSG 159: the night guard lets the polite one in
# A guard who sees a drawn gun: "You'd better put that away..." (the gun goes away right after)
TOWN_GREETINGS = JUNKTOWN_GATE + ["okay, bye", "i'd like to enter."]  # REGULATR.MSG 104: Adytum's gate
KILLIAN_JOB = ["help you get rid of gizmo", "i'm in", "fair enough, i'll do it", "yeah. i'll do it"]  # KILLIAN.MSG
GIZMO_CONFESSION = [  # GIZMO.MSG, with the tape recorder in hand
    "attempt on killian's life",
    "of course you do",
    "i'm here to help you",
    "you need someone who can do the job",
    "for a price",
    "you need someone from out of town",
    "why you want him dead",
    "sure. it's a job",
]
KILLIAN_EVIDENCE = [
    "i sure did",
    "the confession",
    "suit of leather armor",
    "kinda depends on what you're willing",
    "done.",
]
LARS_RAID = ["killian sent me to help run gizmo", "killian sent me", "let's nail that tub"]  # LARS.MSG 160, 156
GIZMO_GANG = ("Gizmo", "Izo", "Thug")
ARADESH_TANDI = ["have you tried to save her", "who could have taken her", "i'll check it out"]  # ARADESH.MSG 229-236
GARL_FREE = ["i want you to set the girl free", "i represent a threat you don't even understand"]  # GARL.MSG 131, 141
ARADESH_REWARD = ["thanks."]  # ARADESH.MSG 242 "Here is your reward."


def hour(actor: Actor) -> float:
    return actor.snap().game_time % DAY / 36000


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
    quests.talk(actor, "Killian Darkwater", ["i'd better go"])
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
    paid = any("here is your reward" in reply.lower() for reply, _options, _chosen in steps)
    return actor._count(PID_CAPS) >= caps + 500 or paid


TANDI = [
    Step("to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: Tandi is missing", tandi_quest),
    Step("to the Khans", lambda a: travel(a, "raiders", 0).startswith("RAIDERS"), checkpoint=True, tries=6),
    Step("Garl lets her go", garl_lets_her_go, checkpoint=True, tries=5),
    Step("Tandi home", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: the reward", tandi_reward, checkpoint=True),
]


def by_day(actor: Actor) -> bool:
    """Shops are locked at night (Killian's door), from within too: a checkpoint's rest to heal (39 h at level 5)
    left the Agent in Killian's store at 2 in the night, the way to Gizmo's casino shut (a full run). Rest
    until morning first. Killian keeps to his bed from 19:00 to 7:30 (KILLIAN.INT critter_p_proc) and walks to his
    counter after: at 6 a click on him only looked at him (twice, once at Full HD), at 8:01 he was still by
    his bed. So a morning rests until noon."""
    ok = True
    if hour(actor) < 6 or hour(actor) >= 19:
        ok = actor.rest("until morning").ok
    if 6 <= hour(actor) < 9:
        ok = actor.rest("until noon").ok and ok
    return ok


def stimpaks_from_killian(actor: Actor, quantity: int = 3) -> bool:
    from f1 import barter

    have = actor._count(PID_STIMPAK)
    out = barter.buy(actor, "Killian Darkwater", ["i want to buy something"], PID_STIMPAK, quantity)
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

PID_HUNTING_RIFLE, PID_10MM_JHP = 0x0A, 0x1D


def affordable(actor: Actor, merchant: str, pid: int, goods: tuple[int, ...] = ()) -> int:
    """How many of `pid` the merchant has and the caps (plus `goods` at full cost) pay for, priced by
    barter_compute_value with the talk's modifier taken as 0 (Rutger's and Kane's, live)."""
    from f1 import barter

    who = quests.find_critter(actor, merchant)
    if who is None:
        return 0
    stock = sum(q for _i, p, q in barter.inventory(actor.mem, who.address) if p == pid)
    player = chargen.skill_values(actor.mem)["barter"]
    each = barter.price(knowledge.item_cost(pid), player, barter.npc_skill(who.pid, "barter"))
    budget = actor._count(PID_CAPS) + sum(knowledge.item_cost(g) for g in goods)
    return min(stock, budget // max(1, each))


def ammo_from_rutger(actor: Actor) -> bool:
    """The Far Go Traders' Rutger keeps a few boxes of 10mm JHP (two, live): buy what the caps allow."""
    from f1 import barter

    quantity = affordable(actor, "Rutger", PID_10MM_JHP)
    if quantity == 0:
        return True  # none left, or no caps: not a reason to stop the route
    out = barter.buy(actor, "Rutger", [], PID_10MM_JHP, quantity)
    actor.log.emit("shopping", item="10mm JHP", **out)
    return out.get("ok", False)


def stimpaks_from_kane(actor: Actor) -> bool:
    """Kane sells stimpaks (nine, live): a carried Hunting Rifle pays at its full cost, caps pay for the rest."""
    from f1 import barter

    goods = (PID_HUNTING_RIFLE,) if actor._count(PID_HUNTING_RIFLE) else ()
    quantity = affordable(actor, "Kane", PID_STIMPAK, goods)
    if quantity == 0:
        return True
    out = barter.buy(actor, "Kane", [], PID_STIMPAK, quantity, pay_with=goods)
    actor.log.emit("shopping", item="Stimpak", **out)
    return out.get("ok", False)


HUB_SUPPLIES = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("downtown", lambda a: on_map(a, "HUBDWNTN") or quests.take_exit(a, "HUBDWNTN"), checkpoint=True),
    Step("10mm from Rutger", ammo_from_rutger, checkpoint=True),
    Step("stimpaks from Kane", stimpaks_from_kane, checkpoint=True),
]

# JTRAIDER.MSG: the crazed raider holding Sinthia in the Crash House; the talking way out (Speech lines), no fight
HOSTAGE_TALK = [
    "there is no need for violence",
    "we can talk this over",
    "you do. by holding this woman hostage",
    "i trust you",
    "you have to hear me out",
    "just relax. i won't harm you",
    "where do we go from here",
    "no money, but you can just walk away",
    "i won't shoot you",
]


MARCELLE_HELP = ["hmm. i'll check it out", "all right, don't harp on me. i'll look into it"]  # MARCELLE.MSG 104, 109


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


# The Brotherhood: the Master's ending by talk needs Vree's disk or her word (MASTER.INT master09), Vree is inside the
# bunker, and its door opens for initiates, made by the Glow's tape (scripts and maps read, the log).
PID_ROPE, PID_BROTHERHOOD_TAPE, PID_VREES_DISK = 0x7F, 0xA4, 0xC2
# A rope in a bookcase in Shady Sands East, behind a curtain (f1.mapfile; taken live). Killian's
# shelves hold one behind his locked storeroom door (25690 stayed shut); one lying at SHADYE 27694 was not reached.
SHADY_BOOKCASE = 13097
# CABBOT.MSG 105, 116, 125 (cabbot09 sets BECOME_AN_INITIATE 1); 145 when he was met before
CABBOT_JOIN = ["i'd like to join.", "i've changed my mind. i want to join the brotherhood.", "like what?", "okay."]


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
# a click on the hex used the rope on the player, "That does nothing."; (40, -6) tied it and went down).
GLOW_BEAM_AIMS = ((40, -6), (60, -8), (25, -4), (80, -10), (50, 0))
GLOW_BODY = 14304  # GLOW1 e0: "Person In Power Armor", dead: the Brotherhood Tape and the Yellow Pass Key
GLOW1_HOT_SPOTS = ((20909, 2), (20316, 2), (16108, 3), (16719, 2))  # HOTSPOT: 20-50 rads on each entry
GLOW1_FLOOR_TRAPS = ((23522, 6),)  # TRAPFLOR: a Traps roll per step inside, one 10-30 blast (the body's is unavoidable)
CABBOT_TAPE = ["yep, sure did.", "okay.", "ok."]  # CABBOT.MSG 158, 161, 164: the tape taken, BECOME_AN_INITIATE 2


def zone(centres) -> frozenset[int]:
    """The hexes within each (centre, radius). With a hex of margin GLOW1 had no way through (live); the
    walk keeps its steps short (go_to with `avoid`), so the engine's own path finder keeps to ours."""
    return frozenset(t for c, r in centres for k in range(r + 1) for t in geometry.ring(c, k))


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


# Inside the Brotherhood: its elevators (elevator.c retvals and keytable) open on spatial triggers: at the entrance
# elev1 (BROHDENT 20102: 'G' the entrance, '1' BROHD12 e0), inside elev0 (BROHD12 18540: '1' BROHD12 e0, '2' e1, '3'
# BROHD34 e0, '4' e1). Vree (BROHD34 e0) hands over her autopsy disk and her word (vree29: pid 194, DESTROY_MASTER_6)
# to a player of IN 7 whose reaction with her is 2 or more (her get_reaction: 40 + karma with CH 3).
BOS_ENTRY_LIFT, BOS_MAIN_LIFT, BOS_MAIN_LIFT_UPPER = 20102, 18540, 18536  # elev1; elev0 on BROHD12 e0 and e1
BOS_UP_LIFT_34, BOS_UP_LIFT_12 = 20940, 14512  # elev0 on BROHD34 e0 ('1' BROHD12 e0); elev1 on BROHD12 e0 ('G')
DOCTOR_RADS = ["i'm radiated."]  # BOSLORI.MSG 104 (IN 5): every rad taken away, a day passes
# VREE.MSG 112, 123 (IN 7), 140, 145 (vree29), 148
VREE_DISK_TALK = [
    "i need some technical information.",
    "what's causing all the mutations?",
    "why do you say that?",
    "interesting theory. any proof?",
    "thanks.",
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


# The Cathedral and the Master: in Robes, both hands empty
# (the nightkin and the Master's guards look at the hand slots), alone. A cocdoor takes the player to the hall (e1);
# Lasher gives the red COC badge; the badge opens the red door; the towers' stairs lead to Morpheus, who takes a
# player "with information about a Vault" to the Master (MSTRLR34 e0 13655).
PID_COC_BADGE = 0x8E  # 142, red
LASHER_BADGE = ["i just want to speak with the man in charge.", "thank you, sir."]  # LASHER.MSG 109, 149
RED_DOOR = 13888  # CHILDRN1 e1, creddoor: pid 141/142 used on it unlocks it
MORPHEUS_TAKE_ME = [  # MORPH.MSG 103 (a Speech roll), 123, 128 (or 175 after a failed roll), 141: morphx2
    "because i have such a deal for you...",
    "i'm from a vault. you know what that is, right?",
    "i have some information for the master about my vault.",
    "i'm from a vault, and i'm willing to give the master the location.",
    "okay, fine.",
]
MASTER_TALK = [  # MASTER.MSG: the way to master19 (his suicide) with Vree's word; rolls at 120, 127 and 150
    "if you can prove to me that your unity is the best course for humanity",
    "i can't know you represent the best future, unless you prove it to me.",
    "so tell me.",
    "that race being the mutants, of course.",
    "you mean to change all the others into mutants, as well.",
    "you've got a problem with your master plan.",
    "i happen to know that your mutants are sterile.",
    "have you talked to any of your mutants about this?",
    "did you think to ask a female mutant?",
    "sorry, your race is doomed.",
    "sorry, this isn't an option for you.",
]


# After master19: 240 s of game time to leave the Cathedral's maps (MASTER1/2, CHILDRN1/2 map_update; worldmap.c).
# MSTRLR34 e0: south along x 57 past seven psychic triggers (revulse: up to 27 damage each; healing after the 3rd and
# the 4th), doors, the lift's trigger 16912 ('1': MSTRLR12 e0 12498); MSTRLR12 e0: the airlock guard, then the stairs
# up (mas2chid) to CHILDRN1 e1; the hall's doors down (chocdoor: e0 19701), out by the exit grids.
LAIR_LIFT = 16912
CORRIDOR_STOPS = (19057, 20457)  # (57, 95), (57, 102)
AIRGRD_PASS = [  # AIRGRD.MSG 114 (a Speech roll), 121; 118 when asked again
    "i'm on important business. you have no right to stop and question me.",
    "ok, thanks. see you around.",
    "i'm bringing a message to our leader.",
]
LAIR_STAIRS = (20536, 20732, 20734)  # MSTRLR12 e0, mas2chid
PID_FIRST_AID_KIT = 0x2F
PID_ELECTRONIC_LOCKPICK = 0x4D  # 77: MBOUT2IN's door takes it at Lockpick -40, the bare skill at -60
TOP_FLOOR_SHELVES = (18911, 20891)  # CHILDRN2 e2 (map file): the Electronic Lock Pick; 2 stimpaks, a first aid kit


def heal_below(actor: Actor, hp: int) -> None:
    for _ in range(3):
        s = actor.snap()
        if s.dude is None or s.dude.hp >= hp or not actor._count(PID_STIMPAK) or actor.in_combat():
            return
        actor.use_on_self(PID_STIMPAK)


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


def wearing(actor: Actor, pid: int) -> bool:
    from f1.actions import OBJECT_WORN

    return any(p == pid and actor.mem.u32(i + Obj.FLAGS) & OBJECT_WORN for i, p, _ in actor.items())


def hands_empty(actor: Actor) -> bool:
    return actor.unequip("left").ok and actor.unequip("right").ok


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
    the player (CHILDRN2 e1, live: "You see: Staircase."), so the ones 2+ hexes away go first. From afar the player
    walks up first: the engine's own walk to a clicked staircase stops at a closed door ("You cannot get there.",
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


# The Military Base and the vats: the scripts named below, read with f1.intdump; tiles from f1.mapfile.
GATE_PASS = [  # the base's mutants (GENSUPR/VGATEMUT, 433): 111 needs the Robes (Speech +25); a failure is a fight
    "i'm with the cathedral. let me by.",
    "i'm a special mutant on a mission for your master.",
]
MB_DOOR = 21271  # MBENT e0, MBOUT2IN: the Electronic Lock Pick at Lockpick -40; the third failure alerts the base
MB_LIFT = 14520  # MBSTRG12 e0/e1, elev4 (elevator type 4: '1' MBSTRG12 e0 14920, '2' e1, '3' MBVATS12 e0 12944)
MB_PAIN = frozenset({21068, 25088, 15520, 26710})  # MBSTRG12 e0, painfeld: 10-30 damage on stepping there
VATS_LIFT = 24120  # MBVATS12 e0/e1, elev5 (type 5: '3' e0 24520, '4' e1 24520)
VATS_LIFT_UP = 12544  # MBVATS12 e0, elev4: '1' back to MBSTRG12 e0
VATS_PAIN = frozenset({13544})  # MBVATS12 e0, painfeld
KRUPPER_TALK = ["why? what is this place?", "ok, thanks."]  # KRUPPER 103, 109: then 20 s to be out of his sight
VCONCOMP = 12325  # MBVATS12 e1: Science (0) gives the interface; a failure costs 300 game seconds, nothing else
VATS_CODE = ["display security codes.", "31914-1041-1251514"]  # VCONCOMP 103, 110: VATS_BLOWN, 300 game seconds
BY_VCONCOMP = {12324, 12326}  # where the Agent stood for the Science tries
LIEUTENANT = 23872  # MBVATS12 e1: he talks within 12 hexes, his guards (ltguard) fight within 6
VATS_CELL_DOOR = 25740  # MBVATS12 e0, the script `door`: locked while its local_var(0) is 0
VATS_CELL_BONES = 25951  # behind that door (map file): 3 stimpaks and a 10mm pistol
PID_LOCK_PICKS = 0x54  # 84: DOOR.INT's use_obj_on rolls Lockpick +20 for them, the bare skill +0
# DOOR.INT's answers to a lock pick try, with message 110 when the skill's critical failure jams the lock
LOCK_RESULTS = ("you unlock the door", "not able to pick the lock", "broke your lockpicks", "beyond your ability")


def said_since(actor: Actor, n0: int, text: str) -> bool:
    """The message box got `text` since its line counter (disp_start) read `n0`; wrapped lines are joined first."""
    return text.lower() in " ".join(" ".join(state.messages_since(actor.mem, n0)).split()).lower()


def guarded_walk(actor: Actor, goals: set[int], avoid: frozenset[int] = frozenset(), plan=None, legs: int = 12) -> bool:
    """Walk to `goals` in the Military Base, where a mutant stops the player to talk (within 12 hexes, until one
    talk passed: `ignoring_dude`, which a load forgets): each talk is answered strictly by `plan`. A fight fails the
    step (the runner reloads). True once on a goal, or once an elevator's panel opened there."""
    plan = plan or GATE_PASS
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if avoid and nav.astar(s.dude.tile, goals, obs.passable_doors | avoid) is None:
        # the pain fields are the gates of MBSTRG12's corridors: the ones on the only way are crossed (15520 before
        # the lift), the others still avoided
        avoid = avoid - set(nav.astar(s.dude.tile, goals, obs.passable_doors) or [])
    for _ in range(legs):
        s = actor.snap()
        if s.screen == "dialogue":
            if dialogue.converse_strict(actor.mem, plan, log=actor.log) is None:
                return False
            time.sleep(0.5)
            continue
        if s.screen == "elevator" or (s.screen == "map" and s.dude is not None and s.dude.tile in goals):
            return True
        if s.screen != "map" or actor.in_combat():
            return False
        nav.go_to(actor, goals, max_steps=40, fight=False, avoid=avoid)
    s = actor.snap()
    return s.screen == "elevator" or (s.dude is not None and s.dude.tile in goals)


def the_base_door(actor: Actor) -> bool:
    """MBOUT2IN: the Electronic Lock Pick on the door, two tries (a third failure alerts the base; the runner's reload
    gives two more), then through the door to MBSTRG12 (27092)."""
    if on_map(actor, "MBSTRG12"):
        return True
    door = next((t for t in world.things(actor.mem) if t.tile == MB_DOOR and t.type == "scenery"), None)
    if door is None:
        return False
    locked = lambda: bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if not guarded_walk(actor, {t for t in geometry.ring(MB_DOOR, 1) if t not in obs.blocked}):
        return False
    for _ in range(2):
        if not locked():
            break
        time.sleep(1.5)  # the last try's message comes in two lines: a late one ended the next try at once (live)
        n0 = actor.mem.glob("disp_start")
        tried = lambda n0=n0: not locked() or said_since(actor, n0, "unable")
        actor.use_item_on(PID_ELECTRONIC_LOCKPICK, MB_DOOR, tried)
        win32.wait_for(tried, 8, 0.2)
        actor.log.emit("lockpick", tile=MB_DOOR, unlocked=not locked(), failed=said_since(actor, n0, "unable"))
    actor.unequip("right")
    if locked():
        return False
    through = lambda: on_map(actor, "MBSTRG12")
    return actor.click_object(MB_DOOR, through, 20, aims=nav.DOOR_AIMS, targets=frozenset({door.address})) or through()


def to_the_vats(actor: Actor) -> bool:
    """MBSTRG12 e0 to the lift's trigger round the pain fields, the level's guards answered; '3' to MBVATS12 e0."""
    if on_map(actor, "MBVATS12"):
        return True
    heal_up(actor)  # the lift's pain field (15520) takes 10-30
    return guarded_walk(actor, {MB_LIFT}, avoid=MB_PAIN, legs=16) and quests.ride_from(actor, MB_LIFT, "3")


def past_krupper(actor: Actor) -> bool:
    """MBVATS12 e0: Krupper stops a Robe-wearer (103, 109, "You need to leave.", then 20 game seconds to be out of his
    sight or he fights): straight on to the lift's trigger, '4' to e1."""
    if on_map(actor, "MBVATS12") and actor.snap().elevation == 1:
        return True
    return guarded_walk(actor, {VATS_LIFT}, VATS_PAIN, KRUPPER_TALK + GATE_PASS, 16) and quests.ride_from(
        actor, VATS_LIFT, "4"
    )


def vats_computer(actor: Actor):
    """VCONCOMP's object: a Computer on MBVATS12 e1 12325 under two Monitors (scenery without scripts) on that hex."""
    return next(
        (
            t
            for t in world.things(actor.mem)
            if t.tile == VCONCOMP and t.elevation == 1 and actor.mem.i32(t.address + Obj.SID) != -1
        ),
        None,
    )


def vats_interface(actor: Actor) -> bool:
    """VCONCOMP, MBVATS12 e1: Science until "You manage to get an interface screen" (33 % a try with Science 33).
    Its technicians stand in front of it: the clicks are aimed where the engine names the computer."""
    comp = vats_computer(actor)
    targets = frozenset({comp.address}) if comp else frozenset()
    for _ in range(15):
        n0 = actor.mem.glob("disp_start")
        actor.use_skill_on(
            "science",
            VCONCOMP,
            lambda n0=n0: said_since(actor, n0, "interface screen") or said_since(actor, n0, "too"),
            targets=targets,
        )
        if said_since(actor, n0, "interface screen"):
            return True
    return False


def pick_lock(actor: Actor, tile: int, door: int, tries: int = 5) -> bool:
    """A door scripted by DOOR.INT: the Lock Picks used on it (Lockpick +20), or the bare skill once they broke. Its
    answer is read from the message box after the click. "You unlock the door." sets the script's local_var(0); the
    engine's lock bit stays set until the door is next used, and that use unlocks and opens it."""
    got = None
    for _ in range(tries):
        n0 = actor.mem.glob("disp_start")
        answered = lambda n0=n0: next((r for r in LOCK_RESULTS if said_since(actor, n0, r)), None) is not None
        if actor._count(PID_LOCK_PICKS):
            actor.use_item_on(PID_LOCK_PICKS, tile, answered, targets=frozenset({door}))
        else:
            actor.use_skill_on("lockpick", tile, answered, targets=frozenset({door}))
        win32.wait_for(answered, 3, 0.1)
        got = next((r for r in LOCK_RESULTS if said_since(actor, n0, r)), None)
        actor.log.emit("lock_pick", tile=tile, picks=actor._count(PID_LOCK_PICKS), result=got)
        if got in (LOCK_RESULTS[0], LOCK_RESULTS[3]) or said_since(actor, n0, "is jammed"):  # unlocked, or jammed
            break
        time.sleep(1.0)
    actor.unequip("right")  # the picks left in the active hand would turn an attack into a use
    return got == LOCK_RESULTS[0]


def cell_stimpaks(actor: Actor) -> bool:
    """MBVATS12 e0's cell: the Bones behind the locked door 25740 hold three stimpaks, for an escape across two pain
    fields (10-30 each) begun at HP 32 of 59. Down by the lift ('3'), the lock picked, the door opened, the Bones
    looted; HP made up with the stimpaks; back up ('4') and beside the computer, where the code is entered."""
    things = world.things(actor.mem)
    bones = next((t for t in things if t.tile == VATS_CELL_BONES and t.elevation == 0 and t.type == "item"), None)
    door = next((t for t in things if t.tile == VATS_CELL_DOOR and t.elevation == 0 and t.type == "scenery"), None)
    if bones is None or door is None:
        return False
    if any(t.address == bones.address for t, _ in world.holders_of(actor.mem, PID_STIMPAK)):
        if actor.snap().elevation == 1 and not quests.ride_from(actor, VATS_LIFT, "3"):
            return False
        locked = lambda: bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(VATS_CELL_DOOR, 1) if t not in obs.blocked}, 60, fight=False)
        if locked() and not pick_lock(actor, VATS_CELL_DOOR, door.address):
            return False

        def is_open() -> bool:
            s = actor.snap()
            return VATS_CELL_DOOR not in nav.obstacles(actor.mem, s.elevation, s.dude.address).doors

        targets = frozenset({door.address})
        if not (is_open() or actor.click_object(VATS_CELL_DOOR, is_open, 10, aims=nav.DOOR_AIMS, targets=targets)):
            return False
        actor.set_mouse_mode(0)
        if not actor.loot(VATS_CELL_BONES).ok:
            return False
    heal_below(actor, int(0.95 * actor.max_hp))
    if actor.snap().elevation == 0 and not quests.ride_from(actor, VATS_LIFT, "4"):
        return False
    nav.go_to(actor, BY_VCONCOMP, 80, fight=False)
    s = actor.snap()
    healed = s.dude.hp >= int(0.95 * actor.max_hp) or not actor._count(PID_STIMPAK)
    return healed and s.elevation == 1 and s.dude.tile in BY_VCONCOMP


def the_vats_code(actor: Actor) -> bool:
    """The computer used (its talk opens once the interface is up): 103, then 110 sets VATS_BLOWN and the 300 s."""
    if quests.gvar(actor, "VATS_BLOWN") == 1:
        return True
    heal_up(actor, 0.95)  # the way out crosses two pain fields against the clock
    comp = vats_computer(actor)
    if comp is None:
        return False
    talking = lambda: actor.snap().screen == "dialogue"
    if not actor.click_object(VCONCOMP, talking, 60, aims=quests.SCENERY_AIMS, targets=frozenset({comp.address})):
        return False
    win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1)  # "Command?" shows before its options (live)
    dialogue.converse_strict(actor.mem, VATS_CODE, log=actor.log)
    time.sleep(1.0)
    return quests.gvar(actor, "VATS_BLOWN") == 1


def out_of_the_base(actor: Actor) -> bool:
    """300 game seconds from the code (the base's map scripts: then the movie and the end): e1's lift ('3'), e0 to the
    lift up (12544, '1'), MBSTRG12 round the pain fields to its exits south (to MBENT), MBENT's exits east."""
    start = time.monotonic()

    def mark(what: str) -> None:
        s = actor.snap()
        actor.log.emit("escape", what=what, seconds=round(time.monotonic() - start, 1), map=s.map_name,
                       hp=s.dude.hp if s.dude else None)  # fmt: skip

    if on_map(actor, "MBVATS12") and actor.snap().elevation == 1:
        guarded_walk(actor, {VATS_LIFT}, plan=KRUPPER_TALK + GATE_PASS)
        quests.ride_from(actor, VATS_LIFT, "3")
        mark("vats e0")
    if on_map(actor, "MBVATS12"):
        guarded_walk(actor, {VATS_LIFT_UP}, VATS_PAIN, KRUPPER_TALK + GATE_PASS, 16)
        quests.ride_from(actor, VATS_LIFT_UP, "1")
        mark("storage")
    for name, avoid in (("MBSTRG12", MB_PAIN), ("MBENT", frozenset())):
        if on_map(actor, name):
            s = actor.snap()
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            exits = {t for t, (to_map, _t, _e) in obs.exits.items() if to_map == 30 or to_map < 0}  # 30: MBENT
            guarded_walk(actor, exits, avoid, legs=16)
            win32.wait_for(lambda name=name: not on_map(actor, name), 20, 0.25)
            mark(f"out of {name}")
    ok = actor.snap().screen != "map"
    mark("out" if ok else "not out")
    return ok


OVERSEER_FAREWELL = ["[more]", "[done]"]  # OVER.INT over81: four [More], then [Done]


def the_ending(actor: Actor, limit_s: float | None = None) -> bool:
    """Both blown, on the world map: the game plays the vats' movie and takes the party to Vault 13 by itself (17
    days), V13ENT gives 10000 XP, the slides play (V13CAVE endgame_part1), the Overseer comes out (over81: four [More],
    one [Done]), then the walk away, the credits and the main menu. The Overseer is answered; the movies, the slides
    and the credits are skipped with Space (a key ends each: a Home once cut the vats' movie), 13 minutes
    of a full run (a run may let them play: actions.SKIP_CINEMA). Done at the main menu after the ending
    (a final step: the runner takes that menu for no death)."""
    from f1 import actions

    limit_s = limit_s or (900 if actions.SKIP_CINEMA else 2400)
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
        seen_ending = seen_ending or s.screen == "endgame" or "V13ENT" in s.map_name
        if s.screen == "main_menu":
            return seen_ending
        if actor.skip_cinema():
            continue
        if s.screen == "dialogue" and win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1):
            dialogue.converse_strict(actor.mem, OVERSEER_FAREWELL, log=actor.log)
        time.sleep(0.5)
    return False


ZACK_TRADE = ["ok."]  # ZACK.MSG 104: straight to the barter screen (gdialog_barter); his stock is the guncache locker


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


VATS = [
    Step("robes on", lambda a: wearing(a, PID_ROBES) or a.equip(PID_ROBES, "armor").ok),
    Step(  # the road's fights near the Boneyard beat the Agent even with a Desert Eagle (two of two): fled instead
        "to the Military Base",
        lambda a: travel(a, "military base", 0, flee_all=True).startswith("MBENT"),
        checkpoint=True,
        tries=6,
    ),
    Step("hands empty", hands_empty, tries=12),  # the loads after the arrival count here: the door's tries
    Step("the base's door", the_base_door, checkpoint=True),  # 33 % a try, two tries a load
    Step("to the vats", to_the_vats, checkpoint=True),
    Step("past Krupper", past_krupper, checkpoint=True),
    Step("the vats' computer", vats_interface, checkpoint=True),
    Step("the cell's stimpaks", cell_stimpaks, checkpoint=True),
    Step("the code", the_vats_code),
    # no checkpoint after it: the world map plays the explosion and the ending, which the keys of a save would skip
    Step("out of the base", out_of_the_base),
    Step("the ending", the_ending, final=True),
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


WATER_CHIP = [
    Step("out to the world map", lambda a: quests.leave_map(a)),
    Step("to Necropolis", lambda a: travel(a, "necropolis", 0).endswith((".MAP", ".SAV")), checkpoint=True, tries=6),
    Step("down a Hotel sewer hole", lambda a: elevation_becomes(a, "sewer hole", ("Sewer Hole",), 0, "HOTEL")),
    Step("the sewers to the Hall of the Dead", lambda a: quests.take_exit(a, "HALLDED")),
    Step("the sewers to the Watershed", lambda a: quests.take_exit(a, "WATRSHD"), checkpoint=True),
    Step(
        "up to the Watershed's south",
        lambda a: elevation_becomes(a, "ladder up", ("Ladder",), 1, "WATRSHD"),
        checkpoint=True,
    ),
    # two Speech - 20 rolls a try (HARRY.INT harry00_3, harry03_5); the first full run failed nine tries in a row
    Step("past Harry to the cell manhole", past_harry, checkpoint=True, tries=25),
    Step("down the cell manhole", lambda a: elevation_becomes(a, "cell manhole", ("Manhole",), 0, "WATRSHD")),
    Step(
        "down to Vault 12",
        lambda a: (
            on_map(a, "VAULTNEC")
            or quests.use_scenery(a, ("Sewer Hole",), lambda: on_map(a, "VAULTNEC"), "sewer hole to Vault 12")
        ),
        checkpoint=True,
    ),
    Step("rested above", rested_above, checkpoint=True),
    Step(
        "the cab to the third floor",
        lambda a: a.snap().elevation == 2 or quests.ride_cab(a, V12_DOORS[0], "3", away_from_treaders(a)),
    ),
    Step("the water chip", take_chip, checkpoint=True),
    Step("the cab to the first floor", lambda a: a.snap().elevation == 0 or quests.ride_cab(a, V12_DOORS[2], "1")),
    Step(
        "up to the Watershed sewers",
        lambda a: on_map(a, "WATRSHD") or quests.use_scenery(a, ("Ladder",), lambda: on_map(a, "WATRSHD"), "ladder up"),
    ),
    Step("up to the cell", lambda a: elevation_becomes(a, "ladder up", ("Ladder",), 1, "WATRSHD"), checkpoint=True),
    Step("heal before the road home", heal_up),
    Step("out of the Watershed", out_of_watershed, tries=6),
    Step("to Vault 13", lambda a: travel(a, "vault 13", 3).endswith((".MAP", ".SAV")), checkpoint=True, tries=6),
    Step("the chip to the Overseer", hand_in, checkpoint=True),
]


def settle(actor: Actor, plan: list[str] | None = None) -> bool:
    """Close a talk someone started (a guard about the drawn gun, say): the plan's lines, else a polite exit. True
    when no dialogue is left open."""
    for _ in range(4):
        d = dialogue.read(actor.mem)
        if not d.active:
            return True
        if plan and dialogue.converse_strict(actor.mem, plan, log=actor.log) is not None:
            continue
        d = dialogue.read(actor.mem)
        if not d.active or not d.options:
            continue
        i = dialogue.leave(d.options)
        i = i if i is not None else len(d.options) - 1
        actor.log.emit(
            "dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i], settle=True
        )
        dialogue.choose(actor.mem, i)
    return not dialogue.read(actor.mem).active


def in_town(actor: Actor, arrived: str) -> bool:
    """Arrived in a peaceful town: settle a talk begun at the gate, then the gun away (guards stop the armed)."""
    if not arrived.endswith((".MAP", ".SAV")):
        return False
    time.sleep(1.0)
    settle(actor, TOWN_GREETINGS)
    actor.holster(True)
    return settle(actor, TOWN_GREETINGS)


UNARMED_MAPS = ("CHILDRN", "MSTRLR", "MBENT", "MBSTRG", "MBVATS")  # a weapon in a hand slot turns their people hostile
TOWN_SWEEP = 20  # hexes round the Agent that a checkpoint's loot sweep covers in a town
# towns whose guards walk up to a drawn gun: the talk blocks the save (Junktown's gate, a full run, twice 27 s)
TOWN_MAPS = (
    "SHADYW", "SHADYE", "JUNKENT", "JUNKKILL", "JUNKCSNO", "VAULT13", "HUBENT", "HUBDWNTN", "HUBHEIGT", "HUBOLDTN",
    "HUBWATER", "BROHDENT", "BROHD12", "BROHD34", "LAADYTUM", "LABLADES", "LAFOLLWR", "LAGUNRUN", "CHILDEAD",
)  # fmt: skip


def loadout(actor: Actor) -> tuple:
    """The kinds of weapon and ammunition carried: when they change, the best weapon may be another (the rounds a
    fight spends do not count: a readying at every checkpoint drew the gun in Junktown)."""
    kinds = ("weapon", "ammo")
    return tuple(
        sorted({p for _, p, q in actor.items() if q > 0 and (knowledge.proto(p) or {}).get("item_type") in kinds})
    )


def prepare(actor: Actor, carried: list) -> None:
    """At the route's start, after each reload and at each checkpoint (so that a reload comes back to it): HP made up
    by rest or stimpaks below 75 %, and the best weapon taken up when the weapons or ammunition carried changed,
    then holstered (a fight draws it; guards talk to a drawn gun). A full run went into Vault 12 with a knife, two
    boxes of 10mm in the pack and HP 22 of 35: the Overseer's ammunition came after the last readying, and nothing
    healed after the sewers. Not where a weapon in a hand slot makes enemies (UNARMED_MAPS)."""
    if not hasattr(actor, "mem"):
        return
    s = actor.snap()
    if s.screen != "map" or s.dude is None or actor.in_combat():
        return
    # what lies about free for the taking, before the save so that the checkpoint keeps it. Not on the base's and
    # the Cathedral's floors (no place to roam), and in a town only near and by day: people keep their houses at night
    # (Killian's intruder talk from 19:00 spoiled Kenji's coming, a full run at Full HD), and walking into places can
    # start what a route does later.
    if not any(m in s.map_name for m in UNARMED_MAPS):
        in_town = any(m in s.map_name for m in TOWN_MAPS)
        if not in_town:
            loot.sweep(actor)
        elif 7 <= hour(actor) < 19:
            loot.sweep(actor, max_hexes=TOWN_SWEEP)
        s = actor.snap()
    heal_up(actor, 0.75)
    now = loadout(actor)
    if any(m in s.map_name for m in UNARMED_MAPS + TOWN_MAPS):
        # put away (a fight drew it: after Kenji a townsman came to the gun, and his talk blocked the next steps'
        # inventory, a full run); readied at the next checkpoint out of town
        if actor.weapon() is not None:
            actor.holster(True)
        return
    if now != (carried[0] if carried else None):
        if actor.ready_weapon().ok:
            actor.holster(True)
        carried[:] = [now]


def ensure_preferences(actor: Actor, prefs: dict[str, int] | str | None) -> None:
    """A save brings back its own preferences (options.c load_options): set ours when they differ (flows.BY_CHARACTER:
    those of the character in the game)."""
    if prefs == flows.BY_CHARACTER:
        prefs = flows.prefs_for(actor.mem)
    if prefs and any(actor.mem.glob(name) != value for name, value in prefs.items()):
        actor.log.emit("preferences", **flows.set_preferences(actor.pid, prefs))


# a step that raised one of these failed: the checkpoint takes over (OwnerActive is not caught: it stops the run)
# Fail fast: only the game's own troubles fail a step: memory gone mid-read, a flow or session that would not
# go, the watchdog's hang. A KeyError, IndexError, AttributeError, ValueError or TypeError is a bug in our code: it is
# not caught, the run stops at once with its traceback and the game's state (main).
STEP_ERRORS = (
    ReadError,
    flows.FlowError,
    session.SessionError,
    watchdog.Stuck,
)


def death_screen(s: state.Snapshot) -> bool:
    """The death picture: a full-screen window over a game already reset (level 1, no XP, the player's HP back to
    the start's). It stays some seconds before the main menu: the Mother Deathclaw's kill ended a step on it, with HP
    30 read, and the runner took it for a plain failure and gave up instead of loading."""
    return s.screen.startswith("window") and s.level == 1 and getattr(s, "experience", None) == 0


def try_step(actor: Actor, step: Step, number: int) -> tuple[bool, bool, state.Snapshot | None, bool]:
    """One try of step `number` (1-based), logged and timed: (its own verdict, whether the player died, the game
    after it, whether the game got stuck: the watchdog saw nothing move, or the game is gone, then with no
    snapshot). A step that crashed failed."""
    log = actor.log
    log.emit("objective", text=step.name, step=number)
    t0 = time.monotonic()
    watchdog.clear()
    stuck = False
    try:
        ok = step.run(actor)
    except STEP_ERRORS as e:
        log.emit("step_error", step=step.name, error=repr(e)[:300])
        ok, stuck = False, isinstance(e, watchdog.Stuck)
    watchdog.clear()  # a trip after the step's last look belongs to no one now
    if not actor.game_alive():  # a crash, or the game closed: a stuck game, restarted by the reload
        log.emit("step_end", step=step.name, ok=False, seconds=round(time.monotonic() - t0, 1), crashed=True)
        return False, False, None, True
    s = actor.snap()
    died = not (ok and step.final) and (s.screen == "main_menu" or s.dude is None or s.dude.hp <= 0 or death_screen(s))
    rads = actor.mem.i32(s.dude.address + Obj.CRITTER_RADIATION) if s.dude and hasattr(actor, "mem") else None
    log.emit(
        "step_end", step=step.name, ok=ok and not died, seconds=round(time.monotonic() - t0, 1),
        hp=s.dude.hp if s.dude else None, rads=rads,
    )  # fmt: skip
    return ok, died, s, stuck


def checkpoint(
    actor: Actor, number: int, level: int, leveled: int, level_up: Callable[[int], dict] | None, carried: list
) -> tuple[bool, int]:
    """After checkpoint step `number`: a talk left open settled (someone walked up; it blocks the character screen
    and the save), the level-up spent when the level rose past `leveled`, prepare, then the quicksave to slot 1.
    (saved, the level spent up to). Raises watchdog.Stuck when its own screens hang."""
    settle(actor)
    if level_up is not None and level > leveled and not actor.in_combat():
        try:
            spent = level_up(actor.pid)
        except (chargen.ChargenError, ReadError, session.SessionError) as e:  # never stops the route
            spent = {"ok": False, "why": repr(e)[:200]}
        actor.log.emit("level_up", level=level, **spent)
        leveled = level if spent.get("ok") else leveled
    prepare(actor, carried)
    name = f"route {number}"
    saved = actor.quicksave(1, name).ok or (settle(actor) and actor.quicksave(1, name).ok)
    return saved, leveled


def reload_checkpoint(actor: Actor, prefs: dict[str, int] | str | None, carried: list) -> bool:
    """Slot 1 loaded from the main menu (three tries: once the load came back to the world map after 60 s),
    the actor attached to the new game, the preferences and prepare again. False: it would not load."""
    log = actor.log
    actor.close()
    loot.forget()  # the load puts the finds back
    if not actor.game_alive():  # a crash: the game again, then the checkpoint
        try:
            actor.pid = session.start()["pid"]
        except session.SessionError as e:
            log.emit("restart_failed", error=repr(e)[:300])
            return False
        watchdog.watch(actor.pid)
        log.emit("restarted", pid=actor.pid)
    for attempt in range(3):
        try:
            flows.load_from_menu(actor.pid, 1)
            break
        except (flows.FlowError, session.SessionError) as e:
            log.emit("load_failed", attempt=attempt + 1, error=repr(e)[:200])
    else:
        return False
    actor.__init__(actor.pid, log)
    carried.clear()  # the reloaded game carries what the checkpoint did
    try:
        ensure_preferences(actor, prefs)
        prepare(actor, carried)
    except (
        watchdog.Stuck,
        ReadError,
    ) as e:  # the next step starts from wherever this left the game (a crash: the next step's own check sees it)
        log.emit("step_error", step="after the reload", error=repr(e)[:300])
    return True


def run(
    actor: Actor,
    steps: list[Step],
    start: int = 1,
    level_up: Callable[[int], dict] | None = None,
    prefs: dict[str, int] | str | None = None,
    exploits: bool | None = None,
    on_step: Callable[[int], None] | None = None,
) -> dict:
    """Run steps from `start` (1-based). `on_step(i)` is told each step's index (0-based) before it runs. Starting mid-way assumes slot 1 holds the checkpoint before it. With
    `level_up`, a checkpoint first spends a level-up when the level rose (the game adds the skill points only when
    the character screen opens, so the level is what tells). With `prefs`, the game's preferences are made so at the
    start and after every reload.

    No reloading for luck: a step that fails is tried again where the game stands, never loaded for (a roll's outcome
    stands); past its tries an optional step is skipped and a required one ends the route. Only a death or a stuck
    game loads the last checkpoint.

    A step marked `exploit` runs only when `exploits` is true; None takes it from the build of the character in the
    game (chargen.Build.exploits, off by default)."""
    log, i, loads, leveled = actor.log, start - 1, {}, 0
    fails: dict[str, int] = {}
    skipped: list[str] = []
    last_failure: dict[str, tuple] = {}  # where the game stood after a step's last failure (fail fast)
    last_checkpoint = max((k for k in range(start - 1) if steps[k].checkpoint), default=-1)
    if exploits is None:  # the character's build decides (off for a name that is not ours, or a fake game)
        mem = getattr(actor, "mem", None)
        build = chargen.build_of(state.character_name(mem)) if mem is not None else None
        exploits = bool(build and build.exploits)
    log.emit("route_start", steps=len(steps), exploits=exploits)
    carried: list = []  # the loadout the weapon was last readied for
    try:
        ensure_preferences(actor, prefs)
        prepare(actor, carried)
    except (
        watchdog.Stuck,
        ReadError,
    ) as e:  # the first step starts from wherever this left the game (a crash: the next step's own check sees it)
        log.emit("step_error", step="the start", error=repr(e)[:300])
    while i < len(steps):
        step = steps[i]
        if step.exploit and not exploits:  # not for this character
            log.emit("skipped", step=step.name, why="exploit")
            skipped.append(step.name)
            i += 1
            continue
        if on_step:
            on_step(i)
        ok, died, s, stuck = try_step(actor, step, i + 1)
        if ok and not died and step.checkpoint:
            try:
                saved, leveled = checkpoint(actor, i + 1, s.level, leveled, level_up, carried)
            except (watchdog.Stuck, ReadError) as e:  # its screens hung, or the game crashed under it: back to the last
                # one (the Hub's farm: the game's process ended during the checkpoint's loot sweep, and the
                # ReadError stopped the route instead of the restart a crash in a step gets)
                if isinstance(e, ReadError) and actor.game_alive():
                    raise
                log.emit("step_error", step=step.name, error=repr(e)[:300])
                ok, stuck = False, True
            else:
                if saved:  # an unsaved checkpoint must not become the place a reload resumes from
                    last_checkpoint = i
                else:
                    log.emit("checkpoint_failed", step=step.name)
        if ok and not died:
            i += 1
            continue
        if not died and not stuck:  # the failure stands; tried again in place, never loaded for
            fails[step.name] = fails.get(step.name, 0) + 1
            where = (s.map_name, s.elevation, s.dude.tile if s.dude else None, s.dude.hp if s.dude else None)
            if where == last_failure.get(step.name) and not step.optional:  # fail fast: again, and nothing changed
                why = "no progress: failed again and the game did not change"
                log.emit("route_end", ok=False, at=step.name, why=why, loads=loads, fails=fails, skipped=skipped)
                return {"ok": False, "at": step.name, "why": why, "loads": loads, "fails": fails, "skipped": skipped}
            last_failure[step.name] = where
            if fails[step.name] < step.tries:
                log.emit("retry", step=step.name, tries=fails[step.name])
                continue
            if step.optional:
                log.emit("skipped", step=step.name, tries=fails[step.name])
                skipped.append(step.name)
                i += 1
                continue
            log.emit("route_end", ok=False, at=step.name, loads=loads, fails=fails, skipped=skipped)
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped}
        failed = steps[last_checkpoint + 1] if last_checkpoint + 1 < len(steps) else step
        loads[failed.name] = loads.get(failed.name, 0) + 1
        if loads[failed.name] > failed.tries:
            log.emit("route_end", ok=False, at=step.name, loads=loads, fails=fails, skipped=skipped)
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped}
        log.emit("recover", why="died" if died else f"stuck: {step.name}", loads=loads[failed.name])
        if not reload_checkpoint(actor, prefs, carried):
            log.emit("route_end", ok=False, at=step.name, loads=loads, why="the checkpoint would not load")
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped}
        i = last_checkpoint + 1
    log.emit("route_end", ok=True, loads=loads, fails=fails, skipped=skipped)
    return {"ok": True, "loads": loads, "fails": fails, "skipped": skipped}


ROUTES = {
    "start": START,
    "water_chip": WATER_CHIP,
    "junktown": JUNKTOWN,
    "tandi": TANDI,
    "shopping": SHOPPING,
    "hub_supplies": HUB_SUPPLIES,
    "sinthia": SINTHIA,
    "brotherhood": BROTHERHOOD,
    "glow": GLOW,
    "vree": VREE,
    "robes": ROBES,
    "cathedral": CATHEDRAL,
    "gun_runners": GUN_RUNNERS,
    "followers_guns": FOLLOWERS_GUNS,
    "vats": VATS,
}


def all_routes() -> dict[str, list[Step]]:
    """The Agent's routes and the Idealist's (f1/idealist.py, which builds on this module's steps)."""
    from f1 import boneyard, cathedral, idealist, military, raiders, watershed

    late = boneyard.ROUTES | military.ROUTES | raiders.ROUTES | watershed.ROUTES | cathedral.ROUTES
    return ROUTES | idealist.ROUTES | late


def fail_fast(actor: Actor, error: BaseException) -> None:
    """Fail fast: the error, its traceback and the game as it stood, in the run's events."""
    import traceback

    try:
        s = actor.snap()
        where = {"screen": s.screen, "map": s.map_name, "tile": s.dude.tile if s.dude else None}
    except (ReadError, watchdog.Stuck, OSError) as e:  # the game may be what failed
        where = {"unreadable": repr(e)[:200]}
    actor.log.emit(
        "route_error", error=repr(error)[:300], traceback=traceback.format_exc()[-3000:], **where
    )  # fmt: skip


def main(argv: list[str]) -> int:
    routes = all_routes()
    start, until, speed = 1, None, None
    while len(argv) >= 3 and argv[-2] in ("--from", "--until", "--clock"):
        if argv[-2] == "--from":
            start = int(argv[-1])
        elif argv[-2] == "--until":
            until = int(argv[-1])
        else:
            speed = float(argv[-1])
        argv = argv[:-2]
    if not argv or any(name not in routes for name in argv):
        print(__doc__)
        return 2
    steps = [step for name in argv for step in routes[name]]
    steps = steps[: until - 1] if until else steps
    run_dir = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-route"
    with session.driver_lock("routes " + " ".join(argv)):
        return _drive(steps, start, speed, run_dir)


def _drive(
    steps: list[Step],
    start: int,
    speed: float | None,
    run_dir: Path,
    on_step: Callable[[Actor, int], None] | None = None,
) -> int:
    actor = Actor(session.game_pid(), EventLog(run_dir / "events.jsonl"))
    if speed:  # a test run's waits at a faster clock, none at the end
        actor.log.emit("clock", **clock.set_fast(actor.pid, speed).__dict__)
    elif left := clock.reset(actor.pid):  # a killed test run left its speed in the game
        actor.log.emit("clock_reset", **left.__dict__)
    dog = watchdog.Watchdog(actor.pid, actor.log)
    dog.start()
    try:
        try:
            hook = (lambda i: on_step(actor, i)) if on_step else None
            result = run(actor, steps, start, level_up=chargen.level_up, prefs=flows.BY_CHARACTER, on_step=hook)
        except Exception as e:  # fail fast: a bug stops the run at once; what it saw goes in the events first
            fail_fast(actor, e)
            raise
        print(result, f"(events: {run_dir / 'events.jsonl'})")
        return 0 if result["ok"] else 1
    finally:
        dog.stop()
        if speed:
            with contextlib.suppress(clock.ClockError):  # the game may be gone
                clock.set_fast(actor.pid, 1)
        actor.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
