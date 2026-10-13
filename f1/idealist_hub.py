"""Idealist route tasks for hub."""

import time

from f1 import dialogue, geometry, knowledge, loot, nav, quests, session, state, trade, win32, world
from f1.actions import Actor
from f1.agent_military import (
    pick_lock,
)
from f1.engine_map import Obj
from f1.idealist_support import (
    DAY,
    beside,
    experience,
    gained,
    karma,
    rest_until,
    take_from,
)
from f1.route_support import (
    Step,
    heal_below,
    in_town,
    on_map,
    settle,
    travel,
)

# The Hub (planned from the scripts and maps). First part: the
# arrival, Deputy Fry, Stapleton's disk, the shopping, the Junk for Necropolis' pump.
FRY_HELLO = ["#103", "#116", "#109"]  # FRY.MSG 103, 116, 109: DECKER_KNOWN 1

# STAPLE.MSG 105, 110, 114: 750 caps, no roll; only while FIND_WATER_CHIP != 2 (before the chip's hand-in). Never
# 116, the blowtorch.
STAPLETON_DISK = ["#105", "#110/112", "#114"]

PID_VAULT_RECORDS, PID_JUNK = 230, 0x62

OLDTOWN_JUNK = 15671  # HUBOLDTN e0: an unscripted crate by Jacob's shop, the Junk Necropolis' pump wants


def fry(actor: Actor) -> bool:
    if quests.gvar(actor, "DECKER_KNOWN"):
        return True
    rest_until(actor, 7, 17)  # FryDialog: he sleeps from 18:00 to 06:00 (his snore floats); the Hub by day
    actor.holster(True)
    quests.talk(actor, "fry", FRY_HELLO, strict=True)
    settle(actor)
    return quests.gvar(actor, "DECKER_KNOWN") != 0


def stapletons_disk(actor: Actor) -> bool:
    """The Vault 13 records from Mrs. Stapleton for 750 caps, then read: MASTER_FILLER_10 1, 100 XP (VALTDISK)."""
    if quests.gvar(actor, "MASTER_FILLER_10"):
        return True
    if not actor._count(PID_VAULT_RECORDS):
        if actor.snap().dude is None or quests.gvar(actor, "FIND_WATER_CHIP") == 2:
            return True  # after the hand-in she no longer sells it
        actor.holster(True)
        quests.talk(actor, "staple", STAPLETON_DISK, strict=True)
        settle(actor)
    if actor._count(PID_VAULT_RECORDS):
        actor.use_on_self(PID_VAULT_RECORDS)
        win32.wait_for(lambda: quests.gvar(actor, "MASTER_FILLER_10") != 0, 5, 0.25)
    return quests.gvar(actor, "MASTER_FILLER_10") != 0


def hub_rest_until(actor: Actor, start: float, end: float) -> bool:
    """rest_until in the Hub: Old Town and the Circle offer no rest (no rest buttons, live), Downtown does."""
    if rest_until(actor, start, end):
        return True
    return hub_map(actor, "HUBDWNTN") and rest_until(actor, start, end)


def hub_map(actor: Actor, name: str) -> bool:
    if on_map(actor, name):
        return True
    if on_map(actor, "HUBOLDTN") and actor.snap().elevation == 1:  # in the Circle: its stairs up first
        quests.use_scenery(
            actor, ("thfd2u", "Staircase"), lambda: actor.snap().elevation == 0, "the Circle's stairs up"
        )
    return quests.goto_map(actor, name)


# The Thieves' Circle under Old Town (HUBOLDTN e1): the stairs THFU2D at 18878, three locked doors on the way to
# Loxley (19482, 22684, 25882: DOOR.INT, Lockpick 0, a critical failure jams; 22684 keeps off the floor trap at
# 21080), Loxley's test (LOXLEY.MSG 114, 167, 127, 131: STEAL_NECKLACE 1, 900 XP; a phrase alone would match 121, the
# jail line), Jasmine's kit (JASMINE.MSG 111: Lock Picks, 2 Flares). Nothing may be picked up in the Circle: Cleo,
# Smitty and Jasmine attack on a pickup.
CIRCLE_STAIRS = "thfu2d"

CIRCLE_DOORS = (19482, 22684, 25882)

LOXLEY_TEST = ["#108/114/174", "#167", "#127", "#131"]

JASMINE_KIT = ["#111"]

PID_LOCK_PICKS, PID_NECKLACE, PID_ELECTRONIC_LOCKPICK = 84, 119, 77


def door_at(actor: Actor, tile: int):
    s = actor.snap()
    return next(
        (t for t in world.things(actor.mem) if t.tile == tile and t.elevation == s.elevation and "Door" in t.name),
        None,
    )


def locked(actor: Actor, door) -> bool:
    return bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)


def into_the_circle(actor: Actor) -> bool:
    if on_map(actor, "HUBOLDTN") and actor.snap().elevation == 1:
        return True
    # use_scenery walks right beside the one staircase first: routes.stairs' click from 2 hexes never took
    return quests.use_scenery(
        actor, (CIRCLE_STAIRS, "Staircase"), lambda: actor.snap().elevation == 1, "the Circle's stairs down"
    )


def circle_doors(actor: Actor) -> bool:
    """The three doors on the way to Loxley unlocked by the Lockpick skill, tried again in place (the game's own
    retry; a jam ends it)."""
    for tile in CIRCLE_DOORS:
        door = door_at(actor, tile)
        if door is None or not locked(actor, door):
            continue
        beside(actor, tile)
        # DOOR.INT's unlock message (DOOR.MSG 100) only sets its local_var(0); the engine's lock bit goes at the next use,
        # which opens it (routes.pick_lock): so the door is used first, and picked only when that does not open it
        if not nav.open_door(actor, tile):
            pick_lock(actor, tile, door.address, tries=8)
            if not nav.open_door(actor, tile):
                # a critical failure jams the lock (the jammed-lock message, the 1x chain) until midnight
                # (CE scripts.c gtime_q_process: obj_unjam_all_locks): a rest past it, and the skill again
                # midnight unjams only the map loaded then, and neither the Circle nor Old Town offers a rest (no rest
                # buttons, live); the other way (CE map.c): a map re-entered 24 hours after it was left unjams them
                actor.log.emit("circle_door", tile=tile, why="jammed: 25 hours away in Downtown")
                hub_map(actor, "HUBDWNTN")
                until = actor.snap().game_time + DAY + DAY // 24
                for _ in range(8):
                    if actor.snap().game_time >= until or not actor.rest("6 hours").ok:
                        break
                hub_map(actor, "HUBOLDTN")
                into_the_circle(actor)
                beside(actor, tile)
                pick_lock(actor, tile, door.address, tries=8)
                nav.open_door(actor, tile)
        actor.log.emit("circle_door", tile=tile, locked=locked(actor, door))
        if locked(actor, door):
            return False
    return True


def loxleys_test(actor: Actor) -> bool:
    if quests.gvar(actor, "STEAL_NECKLACE") >= 1:
        return True
    actor.holster(True)
    quests.talk(actor, "loxley", LOXLEY_TEST, strict=True)
    settle(actor)
    return quests.gvar(actor, "STEAL_NECKLACE") >= 1


def jasmines_kit(actor: Actor) -> bool:
    if actor._count(PID_LOCK_PICKS):
        return True
    quests.talk(actor, "jasmine", JASMINE_KIT, strict=True)
    settle(actor)
    return actor._count(PID_LOCK_PICKS) > 0


# The Heights by day (HTWRGRGE opens the front door 21928 by day while STEAL_NECKLACE is 1; the guards are passive
# from 07:00 to 19:00 unless hurt): the strongbox HTWRBOX at 24516 behind the wooden doors 24322/25120. Its own lock:
# the Lockpick skill at -20 (107 unlocked, 110 failed, 109 jammed; the third failed try sets off its trap, 108);
# opening it rolls Traps (104: the trap goes off, 6-36). Then the Necklace (pid 119) inside.
HEIGHTS_BOX = 24516

GEORGE_APPOINTMENT = ["#104"]  # HTWRGRGE.MSG 104

LEON_PASS = ["#102", "#106"]  # HTWRLEON.MSG 102 (Speech 0), 106

HIGHTOWER_BYE = ["@104"]  # HIGHTOWR.MSG 104, the player's name, 105: Daren04, shown out

BOX_ENDS = (107, 106, 108, 109)  # HTWRBOX.MSG: unlocked, already unlocked, the trap, the lock jammed


HIGHTOWERS_HEX = (
    24522  # HIGHTOWR critter_p_proc: by day he starts his talk (every answer ends in him showing the player out) there
)


def box_side(actor: Actor) -> bool:
    """Beside the strongbox. The only way there crosses Hightower's hex, so that hex is crossed in combat mode, where
    no critter_p_proc runs (players' advice: "combat mode, 3-4 steps, don't stand in the doorway"): up to two
    hexes before it on foot, A, a combat walk past it, Enter to end the combat."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    free = {t for t in geometry.ring(HEIGHTS_BOX, 1) if t not in obs.blocked and t != HIGHTOWERS_HEX}
    if s.dude.tile in free:
        return True
    path = nav.astar(s.dude.tile, free, obs.passable_doors)
    if not path:
        return False
    if HIGHTOWERS_HEX in path:
        k = path.index(HIGHTOWERS_HEX)
        before, after = path[max(0, k - 2)], path[min(len(path) - 1, k + 3)]
        if s.dude.tile != before and not nav.go_to(actor, {before}, max_steps=60)[0]:
            return False
        for door in [t for t in path[k - 2 : k + 4] if t in obs.doors]:  # no door opens by a combat walk
            nav.open_door(actor, door)
        session.press("a")  # combat mode
        win32.wait_for(lambda: actor.in_combat() and actor.my_turn(), 10, 0.1)
        actor.combat_walk({after}, 6)
        for _ in range(3):  # Enter ends it when nobody wants to fight (combat.c combat_end)
            if not actor.in_combat():
                break
            session.press("enter")
            if not win32.wait_for(lambda: not actor.in_combat(), 3, 0.1):
                actor.end_turn()
        actor.log.emit("strongbox", said=f"crossed in combat mode, at {actor.snap().dude.tile}")
        if actor.in_combat():
            return False
    return nav.go_to(actor, free, max_steps=30, avoid=frozenset({HIGHTOWERS_HEX}))[0]


def the_necklace(actor: Actor) -> bool:
    if actor._count(PID_NECKLACE):
        return True
    if not hub_map(actor, "HUBHEIGT"):
        return False
    if actor.snap().dude.tile in geometry.ring(HEIGHTS_BOX, 1):  # a retry beside the box: the way back in passes
        return loot_the_box(actor)  # Hightower's hex, and his talk shows the player out for good (a full run)
    if quests.mvar(actor, 3) == 0:  # the front door (HTWRDOOR) opens for the player only with map_var 3 set
        # HTWRGRGE's daytime opening never ran live (its critter_p_proc waits on a script variable); his talk does:
        # George01b, Speech +10 (121 for the Idealist), George05 sets map_var 3 (his announcing line)
        actor.holster(True)
        quests.talk(actor, "htwrgrge", GEORGE_APPOINTMENT, strict=True)
        settle(actor)
        if quests.mvar(actor, 3) == 0:
            return False
    heal_below(actor, 40)  # the box's trap: 6-36 damage
    # Leon (HTWRLEON) starts a talk once within 6 hexes: 102 is a Speech roll at 0
    # (111 here), then 106; left unanswered, the skill keys sent into
    # it ended in a fight with the household, four deaths. Every walk inside ends with any talk settled.
    nav.open_door(actor, 21928)  # in by the front door first: the way to the box plans through it only once open
    nav.go_to(actor, {22325, 22326}, max_steps=20)
    at_box = False
    for _ in range(3):
        win32.wait_for(lambda: dialogue.read(actor.mem).active, 3, 0.2)  # Leon speaks up within 6 hexes
        if dialogue.read(actor.mem).active:
            settle(actor, LEON_PASS)
        at_box = not dialogue.read(actor.mem).active and box_side(actor)
        if at_box and not dialogue.read(actor.mem).active:
            break
    if dialogue.read(actor.mem).active and not settle(actor, LEON_PASS):
        return False
    if not at_box:  # a skill used from afar walks by itself, into Leon's talk or onto Hightower's hex
        actor.log.emit("strongbox", said="not beside the box")
        return False
    box = next((t for t in world.things(actor.mem) if t.tile == HEIGHTS_BOX and t.type != "critter"), None)
    targets = frozenset({box.address}) if box else frozenset()
    for _ in range(6):
        n0 = actor.mem.glob("disp_start")
        # the lock's own answer: the aim's hovers print the box's look-at line too, and that ended each try
        # before its click had answered (three tries, 33 s each, the 1x chain)
        said = lambda n0=n0: " ".join(
            m for m in state.messages_since(actor.mem, n0) if not m.lower().startswith("you see")
        ).lower()
        answered = lambda said=said: "strongbox" in said()
        actor.use_skill_on("lockpick", HEIGHTS_BOX, answered, targets=targets)
        win32.wait_for(answered, 3, 0.1)
        text = said()
        actor.log.emit("strongbox", said=text[-160:])
        if any(dialogue.holds(text, "htwrbox", n) for n in BOX_ENDS):
            break
    return loot_the_box(actor)


def loot_the_box(actor: Actor) -> bool:
    """Opening the box rolls Traps (104): a trap that goes off takes the click, no loot screen (a full run:
    unlocked at the first try, the open cost 15 HP and no screen), so the open is tried again."""
    for _ in range(3):
        heal_below(actor, 30)
        if actor.loot(HEIGHTS_BOX).ok or actor._count(PID_NECKLACE):
            break
    return actor._count(PID_NECKLACE) > 0


LOXLEY_NECKLACE = ["#188"]  # LOXLEY.MSG 188: STEAL_NECKLACE 2, 500 XP (loxley29)


def necklace_to_loxley(actor: Actor) -> bool:
    if quests.gvar(actor, "STEAL_NECKLACE") >= 2:
        return True
    for _ in range(3):  # out of the Heights: the way out crosses Hightower's hex, and his talk starts there
        if on_map(actor, "HUBOLDTN"):
            break
        if dialogue.read(actor.mem).active:  # Daren04 (582:104): Leon shows the player out, politely (no threat)
            settle(actor, HIGHTOWER_BYE)
            win32.wait_for(lambda: actor.snap().screen == "map" and not dialogue.read(actor.mem).active, 8, 0.25)
            time.sleep(2.0)  # LeonPerformDump fades the player out to 21931
        if hub_map(actor, "HUBOLDTN"):
            break
    if not (on_map(actor, "HUBOLDTN") and into_the_circle(actor)):
        return False
    actor.holster(True)
    quests.talk(actor, "loxley", LOXLEY_NECKLACE, strict=True)
    settle(actor)
    return quests.gvar(actor, "STEAL_NECKLACE") >= 2


def jasmines_reward(actor: Actor) -> bool:
    """Jasmine pays the 3000 caps and the Electronic Lock Pick once the necklace is Loxley's."""
    if actor._count(PID_ELECTRONIC_LOCKPICK):
        return True
    quests.talk(actor, "jasmine", [], strict=True)
    settle(actor)
    return actor._count(PID_ELECTRONIC_LOCKPICK) > 0


# The Water Merchants (HUBWATER e0 25083, MSTMERCH): 1000 XP, 100 more days of water, FIND_WATER_CHIP 1, for 2000 caps.
# Safe before the chip's hand-in (checked in the scripts). The Barter -15 line for 1000 caps is one try at about 17 %
# (not taken: the route takes only one-roll checks that cannot fail).
WATER_DEAL = [
    "#104/109",
    "#114",
    "#118/135",
    "#141",
    "#149",
]


def water_merchants(actor: Actor) -> bool:
    if quests.gvar(actor, "FIND_WATER_CHIP") != 0:
        return True  # already bought, or the chip is found or handed in: the offer is not for now
    if actor.snap().dude is None or caps(actor) < 2000:
        actor.log.emit("water_merchants", why="not enough caps", caps=caps(actor))
        return True
    if not hub_map(actor, "HUBWATER"):
        return False
    actor.holster(True)
    xp0 = experience(actor)
    quests.talk(actor, "mstmerch", WATER_DEAL, strict=True)
    settle(actor)
    return gained(actor, xp0, 1000)


def caps(actor: Actor) -> int:
    return actor._count(PID_CAPS)


# Irwin (HUBDWNTN e0 29124, IRWIN): from level 5, IRWIN.MSG 102 (935:102) then 107
# where..." (935:107): an hour passes and the player is on his farm, HUBMIS1, where seven raiders ("Mercenary",
# 30 HP, FARMRAID) attack on sight. DESTROY_MASTER_3 turns 2 once all are dead; back in town IRWIN.MSG 113
# (935:113): 500 XP, karma +2, a .223 Pistol (DESTROY_MASTER_3 501). The farm: Metal Armor on the floor at 18492,
# stimpaks at 21304 and in the bookcases 19691, 21489.
IRWIN_JOB = ["#102", "#107"]

IRWIN_PAID = ["#113"]

PID_METAL_ARMOR, PID_CAPS = 2, 0x29

FARM_ARMOR = 18492

# IRWIN takes level 5; the farm's seven raiders took HP 53 to 4 at level 5 in leather armor: a later visit
IRWIN_LEVEL = 8


def irwins_job(actor: Actor) -> bool:
    if on_map(actor, "HUBMIS1") or quests.gvar(actor, "DESTROY_MASTER_3") >= 1:
        return True
    if actor.snap().level < IRWIN_LEVEL:
        actor.log.emit("irwin", why=f"level < {IRWIN_LEVEL}", level=actor.snap().level)
        return True
    if not hub_map(actor, "HUBDWNTN"):
        return False
    heal_below(actor, actor.max_hp - 5)
    actor.holster(True)
    quests.talk(actor, "irwin", IRWIN_JOB, strict=True)
    return win32.wait_for(lambda: on_map(actor, "HUBMIS1") and actor.snap().screen == "map", 20, 0.5)


def the_farm(actor: Actor) -> bool:
    """The seven raiders fought out (the step's fight code; they come on sight), then the farm's things."""
    if not on_map(actor, "HUBMIS1"):
        return quests.gvar(actor, "DESTROY_MASTER_3") != 1
    actor.ready_weapon()
    for _ in range(4):
        if quests.gvar(actor, "DESTROY_MASTER_3") >= 2:
            break
        if actor.in_combat():
            actor.fight(only=("Mercenary",))  # every Hub critter is team 1: named foes only (the hub2 plan)
        else:
            foes = [c for c in actor.enemies(only=("Mercenary",)) if not c.dead]
            if not foes:
                break
            # FARMRAID attacks once obj_can_see_obj: two hexes away behind the house's wall it never did (a full
            # run, 04:30 game time: three walks "arrived", no fight), so beside one, and combat by hand if he still
            # has not seen the player
            here = actor.snap().dude.tile
            foe = min(foes, key=lambda c: geometry.distance(c.tile, here))
            nav.go_to(actor, set(geometry.ring(foe.tile, 1)), max_steps=40)
            if not win32.wait_for(actor.in_combat, 3, 0.2):
                session.press("a")
                win32.wait_for(lambda: actor.in_combat() and actor.my_turn(), 10, 0.1)
    if quests.gvar(actor, "DESTROY_MASTER_3") < 2:
        return False
    quests.loot_the_dead(actor, ("Mercenary",), max_bodies=7)
    if worn_cost(actor) < knowledge.item_cost(PID_METAL_ARMOR):  # never in place of the Powered Armor (hub2 plan)
        if not actor._count(PID_METAL_ARMOR):
            beside(actor, FARM_ARMOR)
            actor.pick_up(FARM_ARMOR, PID_METAL_ARMOR)
        if actor._count(PID_METAL_ARMOR):
            actor.equip(PID_METAL_ARMOR, "armor")
    loot.sweep(actor, max_hexes=200, budget_s=120)
    return True


def worn_cost(actor: Actor) -> int:
    from f1.actions import OBJECT_WORN

    worn = [p for i, p, _ in actor.items() if actor.mem.u32(i + Obj.FLAGS) & OBJECT_WORN]
    return max((knowledge.item_cost(p) for p in worn), default=0)


def irwin_paid(actor: Actor) -> bool:
    if quests.gvar(actor, "DESTROY_MASTER_3") in (0, 501):
        return True
    if on_map(actor, "HUBMIS1") and not quests.take_exit(actor, "HUBDWNTN"):
        return False
    k0 = karma(actor)
    actor.holster(True)
    quests.talk(actor, "irwin", IRWIN_PAID, strict=True)
    settle(actor)
    return quests.gvar(actor, "DESTROY_MASTER_3") == 501 and karma(actor) >= k0 + 2


IDEALIST_HUB = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("Fry: the Hub", fry, checkpoint=True),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("Stapleton's disk", stapletons_disk, checkpoint=True),
    Step("Rutger: trade", trade.stop("Rutger"), checkpoint=True, optional=True),
    Step("Kane: trade", trade.stop("Kane"), checkpoint=True, optional=True),
    Step("Old Town", lambda a: hub_map(a, "HUBOLDTN"), checkpoint=True),
    Step("the Junk for the pump", take_from(OLDTOWN_JUNK), checkpoint=True),
    Step("into the Thieves' Circle", into_the_circle, checkpoint=True),
    Step("the Circle's doors", circle_doors, checkpoint=True, tries=2),
    Step("Loxley's test", loxleys_test, checkpoint=True),
    Step("Jasmine's kit", jasmines_kit),
    Step(
        "up to Old Town",
        lambda a: (
            a.snap().elevation == 0
            or quests.use_scenery(a, ("thfd2u", "Staircase"), lambda: a.snap().elevation == 0, "the Circle's stairs up")
        ),
    ),
    Step("by day for the Heights", lambda a: hub_rest_until(a, 8, 16), checkpoint=True),
    Step("the necklace from the Heights", the_necklace, checkpoint=True),
    Step("Loxley: the necklace", necklace_to_loxley, checkpoint=True),
    Step("Jasmine: the reward", jasmines_reward, checkpoint=True),
    Step("the Water Merchants", water_merchants, checkpoint=True),
    Step("Irwin: the farm", irwins_job, checkpoint=True),
    Step("the farm's raiders", the_farm, checkpoint=True, tries=4),
    Step("Irwin: paid", irwin_paid, checkpoint=True),
]
