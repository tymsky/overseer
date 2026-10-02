"""The Idealist's Boneyard visit (planned from the scripts; run from the
kept save idealist-hub2-chain): Adytum's farm and the Turbo Plasma Rifle, the Blades' disk, the deathclaw lair for
the Gun Runners, the Blades' takeover of Adytum without a war, Mike's thanks, the Followers' Robes.

    python -m f1.routes idealist_boneyard [--from N] [--until N] [--clock K]

Day 90 is checked on map entry only (FOLLMAP, LABLADES, LAGUNRUN map_enter: FOLLOWERS_INVADED): every step that
enters one of those three maps refuses on day 90 or later. LARIPPER (the lair) lies between the Blades and the Gun
Runners; its map_enter places 2-3 roaming deathclaws while the mother lives, and they attack on sight. The Plasma
Rifle kills the roamers but not the mother (live): the Junk on the first entry, then the farm and Smitty's
Turbo in Adytum, then the lair again for the mother. Crossings with nothing to fight walk through (cross_lair).
"""

import time
from collections.abc import Callable

from f1 import dialogue, geometry, knowledge, loot, nav, odds, quests, scripts, state, win32
from f1.actions import PID_STIMPAK, Actor
from f1.idealist import day, experience, hands_empty, karma, rearm
from f1.routes import (
    PID_ROBES,
    ZACK_TRADE,
    Step,
    heal_below,
    in_town,
    on_map,
    robes_from_the_floor,
    settle,
    stairs,
    travel,
)

INVADED_DAY = 90  # GVAR 148 FOLLOWERS_INVADED_DATE, read on entering LAFOLLWR, LABLADES, LAGUNRUN
PID_JUNK, PID_TRANSMISSION, PID_TURBO, PID_PLASMA_RIFLE = 98, 238, 233, 15
PID_POWER_ARMOR, PID_LASER_PISTOL, PID_MFC = 3, 16, 39
STAT_LK, STAT_DR = 6, 24  # stat.c: luck, damage resistance (normal)
JUNK_BODY = 16676  # LARIPPER e0: the dead Merchant (team 7, HP 0) carries Leather Armor and the Junk (map file)
RIPPER_DOWN, RIPPER_UP = "ripup2dn", "ripdn2up"  # LARIPPER stairs e0 13877 / e1 13878
MFC_WANTED = 2  # spare Micro Fusion Cells (50 charges each) kept for the Plasma and Turbo rifles

# REGULATR.MSG 104: the gate guard's one-time talk at the arrival (routes.TOWN_GREETINGS has it too)
MILES_JOB = [
    "that smock makes you look like a scientist.",
    "so you make bullets for adytum.",
    "but the hub merchants make a profit off of you and the town.",
    "i could try to get the parts to fix the hydroponic farms.",
]  # MILES.MSG 104, 108, 113, 120: FIX_FARM 1
MILES_PARTS = ["yes, here they are.", "sure, i'll be back after he's fixed them."]  # MILES.MSG 124, 129: 9302
MILES_ARMOR = ["sounds good. i'll go talk to mrs. stapleton."]  # MILES.MSG 139: CHEMISTRY_BOOK 1
SMITTY_FIX = ["i need you to fix these parts for me"]  # SMITTY.MSG 105: 1 game hour, FIX_FARM 9303
SMITTY_TURBO = ["well, i have one right here."]  # SMITTY.MSG 126: 2 game hours, the Plasma Rifle -> the Turbo
CHUCK_READING = [
    "i'm looking for a little advice.",
    "why do you assume that i need something?",
    "now that you mention it, perhaps you can give me some advice.",
    "[more]",
    "hmm. thanks for the advice.",
]  # CHUCK.MSG 171/106, 119, 125, 126: one card a talk, the Fool (LK +1) last
RAZOR_DISK = [
    "are you the leader of this gang?",
    "if you're not a gang, then what are you?",
    "defend yourselves from who?",
    "zimmerman's son?",
    "what did he do?",
    "why haven't you given this to josh's father?",
    "why don't i give him the disk?",
    "anything to help.",
    "well, where are these gun runners?",
    "okay, give me the holodisk",
    "see you later.",
]  # RAZOR.MSG 102, 111, 124, 138, 144, 147, 149, 152, 185, 188, 155: BLADES 9101, the Regulator Transmission
RAZOR_PEACE = ["no thanks, i've seen enough action this week."]  # RAZOR.MSG 198: BLADES 9104, 8 game hours
MOAT_IN = [
    "i want to buy some weapons.",
    "i want to buy weapons from zack.",
    "i need to speak to your boss.",
]  # MOATGRD.MSG 104, 119, 115; never 102, 103, 111 (a timer, then the Gun Runners attack)
GABRIEL_REWARD = [
    "i have some friends who can really use some of your weapons.",
    "get many visitors",
    "i've killed all the deathclaws.",
    "thank you.",
]  # GABRIEL.MSG 170, 103, 141/144, 154: GUN_RUNNER 9202, karma +1, 1000 XP; never 148 (the arc lost)
MIKE_THANKS = ["you are welcome, and keep the supplies for yourself."]  # BYMIKE.MSG 113: karma +1, 4 Stimpaks


def stat(actor: Actor, index: int) -> int:
    from f1 import chargen

    base = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS)
    bonus = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS + 35 * 4)
    return base[index] + bonus[index]


def before_the_invasion(actor: Actor) -> bool:
    ok = day(actor) < INVADED_DAY
    if not ok:
        actor.log.emit("boneyard", why="day 90 reached: no entry to the Blades, the Gun Runners, the Followers")
    return ok


def critter_by_script(actor: Actor, script: str):
    s = actor.snap()
    return next(
        (
            c
            for c in state.critters(actor.mem)
            if not c.dead and c.elevation == s.elevation and scripts.script_of(actor.mem, c.address) == script
        ),
        None,
    )


def talk(actor: Actor, name: str, plan: list[str], done: Callable[[Actor], bool]) -> bool:
    """A talk by plan with both hands empty (Adytum's guards stop a drawn gun); done() checked after it."""
    if done(actor):
        return True
    hands_empty(actor)
    quests.talk(actor, name, plan, strict=True)
    settle(actor, plan)
    return done(actor)


def to_map(name: str) -> Callable[[Actor], bool]:
    """Across the Boneyard's maps by their exits (quests.goto_map); the three day-90 maps only before day 90. The
    lair on the way is crossed without a fight (cross_lair)."""

    def go(actor: Actor) -> bool:
        if on_map(actor, name):
            return True
        here = actor.snap().map_name.split(".")[0]
        path = quests.map_path(here, name) or []
        actor.log.emit("boneyard_walk", to=name, path=path, day=day(actor))
        for step in path[1:]:
            if step in ("LABLADES", "LAGUNRUN", "LAFOLLWR") and not before_the_invasion(actor):
                return False
            if on_map(actor, "LARIPPER"):
                if not cross_lair(actor, step):
                    return False
            elif not quests.take_exit(actor, step):
                return False
        settle(actor)
        actor.holster(True)
        return on_map(actor, name)

    return go


def cross_lair(actor: Actor, to: str) -> bool:
    """Through LARIPPER to the exit for `to` without fighting: a walk, and when combat starts each turn's AP go
    into steps toward that exit (standing on the grid ends the combat and leaves, CE map_leave_map)."""
    start = time.monotonic()
    for _ in range(60):
        s = actor.snap()
        if to.upper() in s.map_name:
            break
        if s.screen != "map" or s.dude is None or s.dude.hp <= 0:
            if s.screen == "main_menu" or (s.dude is not None and s.dude.hp <= 0):
                return False
            time.sleep(0.5)
            continue
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        goals = {t for t, (m, _t, _e) in obs.exits.items() if to.upper() in knowledge.map_name(m)}
        if not goals:
            actor.log.emit("lair", why=f"no exit to {to} on this level", elevation=s.elevation)
            return False
        if not actor.in_combat():
            nav.go_to(actor, goals, max_steps=40, fight=False)
            win32.wait_for(lambda: to.upper() in actor.snap().map_name, 3, 0.25)
            continue
        if not actor.my_turn():
            win32.wait_for(lambda: actor.my_turn() or not actor.in_combat() or actor.snap().screen != "map", 30, 0.1)
            continue
        if s.dude.ap > 0:
            actor.combat_walk(goals, s.dude.ap)
        if actor.my_turn() and actor.in_combat():
            heal_below(actor, actor.max_hp // 2)
            actor.end_turn()
    win32.wait_for(lambda: actor.snap().screen == "map", 20, 0.25)
    time.sleep(1.0)
    s = actor.snap()
    claws = [c for c in state.critters(actor.mem) if knowledge.proto_name(c.pid) == "Deathclaw" and not c.dead]
    actor.log.emit(
        "lair_crossed", to=to, ok=to.upper() in s.map_name, seconds=round(time.monotonic() - start, 1),
        hp=s.dude.hp if s.dude else None, claws=len(claws),
    )  # fmt: skip
    return to.upper() in s.map_name


def the_junk(actor: Actor) -> bool:
    """The dead Merchant's Junk on LARIPPER, 9 hexes off the Gun Runners' side: the farm's only part."""
    if actor._count(PID_JUNK) or quests.gvar(actor, "FIX_FARM") >= 9302:
        return True
    if not on_map(actor, "LARIPPER") and not to_map("LARIPPER")(actor):
        return False
    need = loot.weight(PID_JUNK)  # 12 lb: refused at 0 lb spare after the fight's sweep (live)
    if loot.spare_weight(actor) < need + loot.WEIGHT_SLACK:
        loot.lighten(actor, need)
    for _ in range(2):
        if actor._count(PID_JUNK) or actor.in_combat():
            break
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(JUNK_BODY, 2) if t not in obs.blocked}, max_steps=30, fight=False)
        if not actor.in_combat():
            actor.loot(JUNK_BODY)
    actor.log.emit("junk", carried=actor._count(PID_JUNK), combat=actor.in_combat())
    return actor._count(PID_JUNK) > 0


def disk_read(actor: Actor) -> bool:
    if quests.gvar(actor, "DESTROY_MASTER_7"):
        return True
    if not actor._count(PID_TRANSMISSION):
        return False
    xp0 = experience(actor)
    actor.use_on_self(PID_TRANSMISSION)
    win32.wait_for(lambda: quests.gvar(actor, "DESTROY_MASTER_7") != 0, 5, 0.25)
    actor.log.emit("disk", destroy_master_7=quests.gvar(actor, "DESTROY_MASTER_7"), xp=experience(actor) - xp0)
    return bool(quests.gvar(actor, "DESTROY_MASTER_7"))


def chuck(actor: Actor) -> bool:
    """One card a talk; the Fool (LK +1) ends the readings. Optional: no XP, karma or game time."""
    lk = stat(actor, STAT_LK)
    hands_empty(actor)
    quests.talk(actor, "chuck", CHUCK_READING, strict=True)
    settle(actor, CHUCK_READING)
    actor.log.emit("chuck", luck_before=lk, luck=stat(actor, STAT_LK))
    return True


def chuck_until_the_fool(actor: Actor) -> bool:
    lk = stat(actor, STAT_LK)
    for _ in range(4):
        if stat(actor, STAT_LK) > lk:
            break
        chuck(actor)
    return stat(actor, STAT_LK) > lk


def moat_guard(actor: Actor) -> bool:
    """The Senior Knight by the moat: only the buying lines (MOAT_IN); the talk is not on sight."""
    guard = critter_by_script(actor, "moatgrd")
    if guard is None:
        return True
    hands_empty(actor)
    quests.talk(actor, "moatgrd", MOAT_IN, strict=True)
    settle(actor, MOAT_IN)
    actor.log.emit("moat_guard", tile=critter_by_script(actor, "moatgrd").tile, enemy=quests.gvar(actor, "ENEMY_BLADE"))
    return not dialogue.read(actor.mem).active


def gabriel(plan: list[str], done) -> Callable[[Actor], bool]:
    def go(actor: Actor) -> bool:
        if done(actor):
            return True
        xp0, k0 = experience(actor), karma(actor)
        hands_empty(actor)
        quests.talk(actor, "gabriel", plan, strict=True)
        settle(actor, plan)
        actor.log.emit(
            "gabriel", gun_runner=quests.gvar(actor, "GUN_RUNNER"), water_chip_8=quests.gvar(actor, "WATER_CHIP_8"),
            xp=experience(actor) - xp0, karma=karma(actor) - k0,
        )  # fmt: skip
        return done(actor)

    return go


def zack_cells(want: int) -> Callable[[Actor], bool]:
    def buy(actor: Actor) -> bool:
        from f1 import barter

        if actor._count(PID_MFC) >= want:
            return True
        hands_empty(actor)
        goods = tuple(p for p in (PID_LASER_PISTOL,) if actor._count(p))
        out = barter.buy(actor, "zack", ZACK_TRADE, PID_MFC, want - actor._count(PID_MFC), pay_with=goods)
        actor.log.emit("shopping", item="Micro Fusion Cell", **out)
        settle(actor)
        return actor._count(PID_MFC) >= want

    return buy


def miles_paid(actor: Actor) -> bool:
    """Miles19 (messages only): the Junk taken, 250 caps and 6 Stimpaks, FIX_FARM 9304."""
    if quests.gvar(actor, "FIX_FARM") >= 9304:
        return True
    stims = actor._count(PID_STIMPAK)
    hands_empty(actor)
    quests.talk(actor, "miles", [], strict=True)
    settle(actor)
    actor.log.emit("miles_paid", fix_farm=quests.gvar(actor, "FIX_FARM"), stimpaks=actor._count(PID_STIMPAK) - stims)
    return quests.gvar(actor, "FIX_FARM") >= 9304


def the_turbo(actor: Actor) -> bool:
    """Smitty's Turbo (2 game hours): the Plasma Rifle given up; his box move strips the armour and the hand
    (move_obj_inven_to_obj clears OBJECT_EQUIPPED, unverified on the 1.1 exe): re-armed after."""
    if actor._count(PID_TURBO):
        return True
    if not actor._count(PID_PLASMA_RIFLE):
        return False
    ac0 = actor.snap().dude
    talk(actor, "smitty", SMITTY_TURBO, lambda a: a._count(PID_TURBO) > 0)
    actor.log.emit(
        "turbo", carried=actor._count(PID_TURBO), rifle=actor._count(PID_PLASMA_RIFLE),
        dr=stat(actor, STAT_DR), hp=ac0.hp if ac0 else None,
    )  # fmt: skip
    return actor._count(PID_TURBO) > 0


def rearmed(actor: Actor) -> bool:
    dr0 = stat(actor, STAT_DR)
    rearm(actor)
    actor.log.emit("rearmed", dr_before=dr0, dr=stat(actor, STAT_DR), weapon=getattr(actor.weapon(), "pid", None))
    return True


def the_roamers(actor: Actor) -> bool:
    """The roaming deathclaws on LARIPPER e0, one at a time where possible (quests.hunt: healed first, lured)."""
    if not on_map(actor, "LARIPPER"):
        return False
    if actor.in_combat():  # they attack on sight as the map opens
        actor.fight(only=("Deathclaw",))
    actor.ready_weapon()
    result = quests.hunt(actor, "Deathclaw", minutes=25)
    left = [c for c in state.critters(actor.mem) if knowledge.proto_name(c.pid) == "Deathclaw" and not c.dead]
    actor.log.emit("roamers", left=len(left), mvar1=quests.mvar(actor, 1), hp=actor.snap().dude.hp, **result)
    return not left


def down_to_the_mother(actor: Actor) -> bool:
    if actor.snap().elevation == 1:
        return True
    actor.holster(True)
    return stairs(actor, RIPPER_DOWN, lambda: actor.snap().elevation == 1)


def the_mother(actor: Actor) -> bool:
    """The Mother Deathclaw (320 HP, attacks on sight) and then her four eggs; map_var 0 and 2 read after."""
    if actor.snap().elevation != 1:
        return False
    quests.heal(actor, 0.95)  # the spare stimpaks, then a rest (no live foe on e1 sees the player yet)
    actor.ready_weapon()
    xp0 = experience(actor)
    mother = quests.find_critter(actor, "Mother Deathclaw")
    if mother is not None and not actor.in_combat():  # a fight on computed odds (f1.odds, from the stairs)
        chance = odds.against(actor, [mother])
        actor.log.emit(
            "odds", foes=1, win=round(chance.win, 3), hp_left=round(chance.hp_left, 1), turns=round(chance.turns, 1),
            hit=chance.hit, foe_hit=chance.foe_hit, weapon=getattr(actor.weapon(), "pid", None),
        )  # fmt: skip
        if chance.win < quests.ODDS_TO_FIGHT:
            return False
    for _ in range(8):
        if actor.in_combat():
            actor.fight(only=("Mother Deathclaw",))
            continue
        mother = quests.find_critter(actor, "Mother Deathclaw")
        if mother is None:
            break
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(mother.tile, 6) if t not in obs.blocked}, max_steps=10)
        win32.wait_for(actor.in_combat, 4, 0.25)
    actor.log.emit("mother", dead=quests.find_critter(actor, "Mother Deathclaw") is None, mvar0=quests.mvar(actor, 0),
                   xp=experience(actor) - xp0, hp=actor.snap().dude.hp)  # fmt: skip
    return quests.mvar(actor, 0) == 1


def the_eggs(actor: Actor) -> bool:
    if actor.snap().elevation != 1:
        return False
    for _ in range(8):
        eggs = [c for c in state.critters(actor.mem) if knowledge.proto_name(c.pid) == "Egg" and not c.dead]
        if not eggs:
            break
        if not actor.in_combat():
            s = actor.snap()
            egg = min(eggs, key=lambda c: geometry.distance(c.tile, s.dude.tile))
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            nav.go_to(actor, {t for t in geometry.ring(egg.tile, 2) if t not in obs.blocked}, max_steps=10)
            from f1 import session

            session.press("a")
            win32.wait_for(actor.in_combat, 4, 0.25)
        actor.fight(only=("Egg",))
    actor.log.emit("eggs", mvar2=quests.mvar(actor, 2))
    return quests.mvar(actor, 2) == 0 and not [
        c for c in state.critters(actor.mem) if knowledge.proto_name(c.pid) == "Egg" and not c.dead
    ]


def up_the_stairs(actor: Actor) -> bool:
    if actor.snap().elevation == 0:
        return True
    actor.holster(True)
    return stairs(actor, RIPPER_UP, lambda: actor.snap().elevation == 0)


def razor_peace(actor: Actor) -> bool:
    if quests.gvar(actor, "BLADES") in (9104, 2):
        return True
    return talk(actor, "razor", RAZOR_PEACE, lambda a: quests.gvar(a, "BLADES") in (9104, 2))


def adytum_taken(actor: Actor) -> bool:
    """On entering Adytum after 9104: the Regulators gone, BLADES 2, karma +2, 2000 XP (LAADYTUM map update)."""
    xp0, k0 = experience(actor), karma(actor)
    ok = to_map("LAADYTUM")(actor)
    win32.wait_for(lambda: quests.gvar(actor, "BLADES") == 2, 10, 0.5)
    alive = {
        name: critter_by_script(actor, name) is not None for name in ("miles", "smitty", "chuck", "jon", "regguard")
    }
    actor.log.emit(
        "adytum_taken", blades=quests.gvar(actor, "BLADES"), xp=experience(actor) - xp0, karma=karma(actor) - k0,
        alive=alive,
    )  # fmt: skip
    return ok and quests.gvar(actor, "BLADES") == 2


def mike(actor: Actor) -> bool:
    k0 = karma(actor)
    talk(actor, "bymike", MIKE_THANKS, lambda a: False)
    actor.log.emit("mike", karma=karma(actor) - k0)
    return True


def out_of_the_boneyard(actor: Actor) -> bool:
    actor.holster(True)
    return quests.leave_map(actor)


def gvar_at_least(name: str, value: int) -> Callable[[Actor], bool]:
    return lambda a: quests.gvar(a, name) >= value


IDEALIST_BONEYARD = [
    Step(
        "to the Boneyard",
        lambda a: a.snap().map_name.startswith("LA") or in_town(a, travel(a, "boneyard", 0)),
        checkpoint=True,
        tries=6,
    ),
    Step("Miles: the job", lambda a: talk(a, "miles", MILES_JOB, gvar_at_least("FIX_FARM", 1)), checkpoint=True),
    Step("Chuck: a reading", chuck, optional=True),
    Step("to the Blades", to_map("LABLADES"), checkpoint=True),
    Step(
        "Razor: the disk",
        lambda a: talk(a, "razor", RAZOR_DISK, lambda b: quests.gvar(b, "BLADES") >= 9101),
        checkpoint=True,
    ),
    Step("the disk read", disk_read),
    # the roamers attack on sight; two fell to the Plasma Rifle in one fight, HP 73 -> 45 (live)
    Step("into the lair", to_map("LARIPPER"), checkpoint=True),
    Step("the roamers", the_roamers, checkpoint=True, tries=2),
    Step("the Junk", the_junk, checkpoint=True),
    # the mother killed the Agent with the Plasma Rifle (her 320 HP at 168, about 45 a turn against her 20-36,
    # live): the farm and Smitty's Turbo first, then the lair again
    Step("back to Adytum", to_map("LAADYTUM"), checkpoint=True),
    Step("Miles: the parts", lambda a: talk(a, "miles", MILES_PARTS, gvar_at_least("FIX_FARM", 9302)), checkpoint=True),
    Step("Smitty: the parts", lambda a: talk(a, "smitty", SMITTY_FIX, gvar_at_least("FIX_FARM", 9303))),
    Step("Miles: paid", miles_paid, checkpoint=True),
    Step("Smitty: the Turbo", the_turbo),
    Step("re-armed", rearmed, checkpoint=True),
    Step("back to the lair", to_map("LARIPPER"), checkpoint=True),
    Step("the roamers again", the_roamers, checkpoint=True, tries=2),  # placed again while the mother lives
    Step("down to the mother", down_to_the_mother),
    Step("the mother", the_mother, checkpoint=True, tries=2),
    Step("the eggs", the_eggs),
    Step("up the stairs", up_the_stairs),
    Step("to the Gun Runners", to_map("LAGUNRUN"), checkpoint=True),
    Step("the moat guard", moat_guard),
    # with WATER_CHIP_8 9250 Gabriel's first talk goes Gab17 -> Gab18 -> Gab20 -> Gab22 (GABRIEL.INT talk_p_proc)
    Step(
        "Gabriel: the reward",
        gabriel(GABRIEL_REWARD, lambda a: quests.gvar(a, "GUN_RUNNER") >= 9202),
        checkpoint=True,
    ),
    Step("Zack: cells", zack_cells(MFC_WANTED), optional=True),
    Step("back to the Blades", to_map("LABLADES"), checkpoint=True),
    Step("Razor: no thanks", razor_peace, checkpoint=True),
    Step("Adytum taken", adytum_taken, checkpoint=True),
    Step("Miles: the armour offer", lambda a: talk(a, "miles", MILES_ARMOR, gvar_at_least("CHEMISTRY_BOOK", 1))),
    Step("Chuck: the Fool", chuck_until_the_fool, optional=True),
    Step("to the Blades again", to_map("LABLADES"), checkpoint=True),
    Step("Mike: the supplies", mike),
    # MacRae's lesson is left out: MacRae15 only reads DR and melee damage into a local and advances 24 hours
    # (MACRAE.INT; live: DR 40 -> 40, melee 4 -> 4)
    Step("to the Followers", to_map("LAFOLLWR"), checkpoint=True),
    Step("the robes", lambda a: a._count(PID_ROBES) > 0 or robes_from_the_floor(a), checkpoint=True),
    Step("out of the Boneyard", out_of_the_boneyard),  # on the world map: F6 saves nothing there
]

ROUTES = {"idealist_boneyard": IDEALIST_BONEYARD}
