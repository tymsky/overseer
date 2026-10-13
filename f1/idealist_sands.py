"""Idealist route tasks for sands."""

import time

from f1 import dialogue, geometry, nav, quests, session, state, ui, win32, worldmap
from f1.actions import ITEM_MENU_DROP, ITEM_MENU_USE, PID_STIMPAK, Actor
from f1.agent_junktown import (
    by_day,
)
from f1.agent_sands import (
    SETH_POST,
    clear_the_caves,
    nest_reported,
    scorpion_quest,
    seth_to_the_caves,
    supplies,
    tandi_quest,
    tandi_reward,
)
from f1.idealist_support import (
    DAY,
    beside,
    experience,
    gained,
    karma,
    read_book,
    scripted,
    take_from,
    the_floor,
)
from f1.idealist_vault15 import (
    PID_DYNAMITE,
    VAULT_15,
    two_days_away,
)
from f1.route_support import (
    Step,
    hour,
    on_map,
    settle,
    travel,
)

PID_ANTIDOTE, PID_SCOUT_HANDBOOK, PID_SCORPION_TAIL = 49, 86, 92

RATIONS = (7, 9)  # WTRGRD.INT: the water line's hours, the corridor to the library full
WAIT_STEP_PX = 60  # a walk east of the vault on the world map, stopped when those hours are over
LIBRARY_COMPUTERS = (17533, 21134)  # VAULT13 e2, LIBCOMP.INT: Science on one rolls IN (1d10 <= 10): 350 XP, 6 h

BOOKSHELF_SHADYW, BOOKCASE_SHADYE = 15931, 13097  # unscripted containers, a Scout Handbook each (SHADYE: and a Rope)

# Free loot on the route's maps (f1.atlas: no script, not locked), taken in steps of their own:
SHADYW_223 = 14287  # a Bookcase: two boxes of .223 FMJ (50 rounds each) and BB's, for the Hunting Rifle below

JUNKKILL_RIFLE = 24903  # Shelves in Killian's store: a Hunting Rifle (8-20); not taken: a use of them is a theft (live)

JARVIS_BED = 22728  # SHADYW: JARVIS.INT use_obj_on: an Antidote cures him, 400 XP, karma +1

# THERESA.MSG path A (reaction level 2): 103 -> 110 (IQ 6) -> a Speech roll, once (local_var 7) -> Theresa08 sets
# CALM_REBELS 2 -> 123; 750 XP when the talk ends
THERESA_CALM = [
    "#103",
    "#110",
    "#123",
]

# SSGUIDE.MSG: all seven of DialogMain's questions in one talk give 250 XP (local_var 5); a drawn weapon only gets
# the drawn-weapon warning (DialogWeapon)
KATRINA_QUESTIONS = [
    "#114",
    "#126",
    "#127",
    "#134",
    "#128",
    "#129",
    "#130",
    "#138",
]

COOK_COMPLIMENT = ["#102"]  # COOK.MSG 102: only at the very first talk, after an LK check

CURTIS_ROTATION = ["#108", "#114", "#117"]

RAZLO_ANTIDOTE = ["#146"]  # RAZLO.MSG 146, 06:00-19:00: 4 h, 250 XP

RAZLO_GIFT = ["#105", "#110/194", "#114"]

# GARL.MSG 133 (IQ 5) reaches garl15 with no roll, then 141 (IQ 6, Speech >= 45): Speech +10 or a CH check
GARL_TALKED_DOWN = ["#133", "#141"]


def day_one(actor: Actor) -> bool:
    """Vault 13 lets no one back in on day 0: V13COMP.INT opens its door only once game_time / day is not 0, and
    worldmap.c's town map knows the vault's inner entrances only then. No rest in the cave: critter.c lets the player
    rest on V13ENT only with no living critter of another team on the level, and its 20 Cave Rats are slow to kill
    in the dark (SG 43 less the darkness: 9 of them in 11 minutes). So the time passes on the world map:
    18 px of mountain off the vault and back, 72 units of time (a mountain pixel costs two), 0.8 of a day at
    Outdoorsman 53 (91 units a day), again until day 1. 35 px each way reached day 1.88."""
    home = worldmap.town_xy("vault 13")
    away = (home[0], home[1] + 18)
    for _ in range(6):
        if actor.snap().game_time >= DAY:
            break
        if actor.snap().screen == "map" and not quests.leave_map(actor):
            return False
        if worldmap.town_map_buttons(actor.mem):  # V13ENT's exits lead to the town map
            worldmap.to_world_map(actor.mem)
        for point in (away, home):
            if worldmap.travel(actor.pid, *point).startswith("interrupted"):  # an encounter: out by the map's edge
                quests.leave_map(actor)
                break
    return actor.snap().game_time >= DAY


def library(actor: Actor) -> bool:
    """Science on each library computer: 350 XP and 6 hours each. LIBCOMP.INT use_skill_on_p_proc answers "You use
    the Vault computer teaching system." at once, then, after a fade and 6 hours, "...researching some important
    information." with the XP (its do_check(IN) passes at IN 10), or LIBCOMP.MSG 104 when that check
    fails; a computer used before, LIBCOMP.MSG 102. Every one of those ends it: the lines since the
    use, joined (the box wraps them), are read for them. Read three lines back only, the answer after the fade was
    missed and a run skipped the step.

    The water line comes first: WTRGRD.INT hands out the rations from 07:00 to 09:00 (game_time_hour > 700 and
    < 900) and the dwellers queue in the corridor to the library; a new game starts inside that window, and three
    ironman runs found no way past the queue (a capture: "Here's your ration for today", the corridor full), a
    fourth got through on the walk round by the south door. Resting it away is refused on day 1 in the vault (the
    Pip-Boy's clock did not open: "no Pip-Boy", live)."""
    for tile in LIBRARY_COMPUTERS:
        xp0, n0 = experience(actor), actor.mem.glob("disp_start")

        def done(xp0=xp0, n0=n0) -> bool:
            text = " ".join(state.messages_since(actor.mem, n0))
            return experience(actor) > xp0 or any(dialogue.holds(text, "libcomp", n) for n in (102, 103, 104))

        beside(actor, tile)
        actor.use_skill_on("science", tile, done, targets=scripted(actor, tile, "libcomp"))
        if not win32.wait_for(done, 12, 0.25):  # the answer follows a fade and six hours passing
            return False
    return True


def theresa(actor: Actor) -> bool:
    """One floor up (e1), Theresa (07:15-19:15): the rebels calmed by one Speech roll, or a reload for another try."""
    if quests.gvar(actor, "CALM_REBELS") == 2:
        return True
    if actor.snap().elevation != 1 and not quests.use_elevator(actor, "2"):
        return False
    actor.holster(True)
    quests.talk(actor, "theresa", THERESA_CALM, strict=True)
    return quests.gvar(actor, "CALM_REBELS") == 2 and not actor.in_combat()


def book_from(actor: Actor, tile: int) -> bool:
    """The Scout Handbook from the shelf on `tile`, read at once."""
    if actor._count(PID_SCOUT_HANDBOOK) == 0:
        actor.loot(tile)
    return read_book(actor, PID_SCOUT_HANDBOOK)


def daylight(actor: Actor) -> bool:
    """The town's people keep hours (Razlo 06:00-19:00, Curtis sleeps by night): a night arrival rests until morning.
    routes.by_day also rests on to noon, which only Killian's counter needs."""
    if hour(actor) >= 19 or hour(actor) < 6:
        actor.rest("until morning")
    return 6 <= hour(actor) < 19


def katrina(actor: Actor) -> bool:
    xp0 = experience(actor)
    actor.holster(True)
    quests.talk(actor, "ssguide", KATRINA_QUESTIONS)
    return gained(actor, xp0, 250)


def cook(actor: Actor) -> bool:
    """The compliment (karma +1) is offered at the first talk only, when do_check(LK, 0) passes: one time in ten at LK
    1 (COOK.INT talk_p_proc). One talk, and its outcome stands: no load for another first talk. An optional
    step: without the compliment the route goes on."""
    k0 = karma(actor)
    actor.holster(True)
    steps = quests.talk(actor, "cook", COOK_COMPLIMENT, strict=True) or []
    if actor.snap().screen == "dialogue":  # no compliment offered: whatever else there is ends the talk
        settle(actor)
    praised = karma(actor) > k0 or any(dialogue.holds(chosen, "cook", 102) for _r, _o, chosen in steps)
    actor.log.emit("cook", praised=praised)
    return praised


def scorpion_tail(actor: Actor) -> bool:
    """A Scorpion Tail from a radscorpion's body (each of the nine carries one), for Razlo's antidote."""
    if not actor._count(PID_SCORPION_TAIL):
        quests.loot_the_dead(actor, ("Radscorpion",), max_bodies=1)
    return actor._count(PID_SCORPION_TAIL) > 0


def razlo_antidote(actor: Actor) -> bool:
    """Razlo makes an Antidote from the tail by day (06:00-19:00; the night talk has no such line): 4 hours, 250 XP."""
    if actor._count(PID_ANTIDOTE):
        return True
    by_day(actor)
    actor.holster(True)
    quests.talk(actor, "razlo", RAZLO_ANTIDOTE)
    return actor._count(PID_ANTIDOTE) > 0


def jarvis(actor: Actor) -> bool:
    """The Antidote used on Jarvis, lying ill: 400 XP and karma +1 (JARVIS.INT use_obj_on_p_proc)."""
    k0, count0 = karma(actor), actor._count(PID_ANTIDOTE)
    if not count0:
        return False
    target = quests.find_critter(actor, "jarvis", JARVIS_BED)
    if target is None:
        return False
    # He lies in bed: a click above his hex named the Rug and the Curtain (three tries); aim where the
    # engine names him.
    beside(actor, target.tile)
    done = lambda: actor._count(PID_ANTIDOTE) < count0
    actor.use_item_on(PID_ANTIDOTE, target.tile, done, targets=frozenset({target.address}))
    return karma(actor) > k0


def curtis(actor: Actor) -> bool:
    """Crop rotation (Science >= 40, IQ 5): karma +2, and 500 XP when the talk ends. Curtis sleeps by night."""
    k0 = karma(actor)
    by_day(actor)
    actor.holster(True)
    quests.talk(actor, "curtis", CURTIS_ROTATION)
    return karma(actor) >= k0 + 2


def tandi_taken(actor: Actor) -> bool:
    if not on_map(actor, "SHADYW"):
        quests.take_exit(actor, "SHADYW")
    return on_map(actor, "SHADYW") and quests.gvar(actor, "TANDI_STATUS") == 1


def razlo_gift(actor: Actor) -> bool:
    """While Tandi is gone, Razlo gives 2 Stimpaks and a Fruit (and an Antidote once he has made one). Optional."""
    before = actor._count(PID_STIMPAK)
    by_day(actor)
    actor.holster(True)
    quests.talk(actor, "razlo", RAZLO_GIFT)
    actor.log.emit("razlo_gift", stimpaks=[before, actor._count(PID_STIMPAK)])
    return True


def into_the_vault(actor: Actor) -> bool:
    """The town map's fourth entrance: VAULT13's third floor (the Overseer, the library), known from day 1.

    During the water line's hours (RATIONS, WTRGRD.INT) the queue fills the corridor to the library, and the vault
    refuses a rest on day 1 ("no Pip-Boy"): the hours are spent on the world map instead, a walk east that stops at
    09:00, and back (f1.travel.leg: 25 px from the vault is 0.6 game day, expected encounters 0.0 on that leg). A
    target a few pixels away was no walk (0.0 hours, live, three times): the click fell on the vault's own circle and
    opened its town map."""
    if RATIONS[0] <= hour(actor) < RATIONS[1]:
        if actor.snap().screen == "map":
            quests.leave_map(actor)
        vx, vy = actor.mem.glob("world_xpos"), actor.mem.glob("world_ypos")
        t0, out = actor.snap().game_time, "no walk"
        if worldmap.town_map_buttons(actor.mem):
            worldmap.to_world_map(actor.mem)
        if actor.snap().screen == "worldmap":
            worldmap.aim(actor.mem, vx + WAIT_STEP_PX, vy)  # a walk away, stopped once the hours are over
            done = lambda: hour(actor) >= RATIONS[1] or actor.snap().screen != "worldmap"
            out = "over" if win32.wait_for(done, 120, 0.25) else "timeout"
        actor.log.emit("waited", why="the water line", hours=round((actor.snap().game_time - t0) / 36000, 2),
                       hour=round(hour(actor), 2), screen=actor.snap().screen, out=out)  # fmt: skip
    return travel(actor, "vault 13", 3).startswith("VAULT13") and actor.snap().elevation == 2


def tandi_home(actor: Actor) -> bool:
    """SHADYWST.INT turns TANDI_STATUS 5 (freed, following) into 2 on entering Shady Sands West, not East."""
    return travel(actor, "shady sands", 0).startswith("SHADYW") and quests.gvar(actor, "TANDI_STATUS") == 2


def garl(actor: Actor) -> bool:
    if quests.gvar(actor, "TANDI_STATUS") != 5:
        actor.holster(True)
        quests.talk(actor, "Garl", GARL_TALKED_DOWN, strict=True)
    return quests.gvar(actor, "TANDI_STATUS") == 5 and not actor.in_combat()


# The cave wall: the radscorpions all dead, Vault 15's Dynamite blows the weak wall for the second reward
# (CAVEWALL damage_p_proc, once by map_var 0: karma +3, 300 XP with none alive). The explosion reaches a scripted
# scenery within 3 hexes (scripts.c scr_explode_scenery) and hurts its own hex and the six round it (actions.c
# action_explode; dynamite 30-50). Armed from the inventory (Use: the timer window, 60 s by default; a Traps roll:
# a failure halves the time, a critical failure sets it off at once; protinst.c obj_use_explosive), dropped where the
# player stands, and left. When the wall comes down the player must be within 15 hexes of 21155, the entrance side,
# or metarule(13) runs. Seth still takes the player there after the nest (SETH.INT Seth10: 115 -> 118).
PID_DYNAMITE_ARMED = 0xCE

CAVE_ROCKS = (21343, 21345)

CAVE_SAFE = 21155

SETH_BACK_TO_CAVES = ["#115", "#118", "#126"]


def back_to_the_caves(actor: Actor) -> bool:
    """Seth to the caves, with the dynamite; without it (Vault 15 gave none) the trip is not made."""
    if on_map(actor, "CAVES") or not actor._count(PID_DYNAMITE):
        return True
    if not on_map(actor, "SHADYW"):
        quests.take_exit(actor, "SHADYW")
    actor.holster(True)
    quests.talk(actor, "Peasant", SETH_BACK_TO_CAVES, near=SETH_POST)
    return win32.wait_for(lambda: on_map(actor, "CAVES") and actor.snap().screen == "map", 30, 0.5)


TIMER_DOWN = 7000  # the set-timer window's minus button: 10 s off a press (inventry.c do_move_timer: 60 s, min 10)


def short_fuse(actor: Actor) -> int:
    """The dynamite's timer down from its 60 s to 20 s by the window's minus button (digits are not typed there):
    the Agent stood a minute by the rocks for the blast (31-64 s, the 1x chain). 20, not the least 10: the way out
    of its reach must still fit in a test run's fast clock (f1.clock). The seconds it shows after."""
    button = None
    for wid, *_ in reversed(state.live_windows(actor.mem)):
        button = next((b for b in ui.buttons(actor.mem, wid) if b.answers(TIMER_DOWN)), None)
        if button:
            break
    if button is None:
        return 60
    for _ in range(4):
        session.click(*button.center)
        time.sleep(0.15)
    return 20


def the_cave_wall(actor: Actor) -> bool:
    """The dynamite armed and dropped beside the rocks, the player back toward the entrance, the wall down."""
    if not on_map(actor, "CAVES") or not (actor._count(PID_DYNAMITE) or actor._count(PID_DYNAMITE_ARMED)):
        return True  # no dynamite: nothing to blow
    if quests.mvar(actor, 0):
        return True
    k0, xp0 = karma(actor), experience(actor)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    spots = {
        t
        for rock in CAVE_ROCKS
        for r in (1, 2)
        for t in geometry.ring(rock, r)
        if t not in obs.blocked and geometry.distance(t, CAVE_SAFE) <= 12
    }
    if not nav.go_to(actor, spots, max_steps=60)[0]:
        return False
    drop = actor.snap().dude.tile
    if not actor._count(PID_DYNAMITE_ARMED):
        why = actor.item_menu(PID_DYNAMITE, ITEM_MENU_USE)
        if why:
            actor.log.emit("dynamite", why=why)
            return False
        time.sleep(0.8)  # the timer window (INVENTORY_WINDOW_TYPE_SET_TIMER): Enter takes what it shows
        fuse = short_fuse(actor)
        session.press("enter")
        armed = win32.wait_for(lambda: actor._count(PID_DYNAMITE_ARMED) > 0, 3, 0.1)
        actor.log.emit("dynamite", armed=armed, tile=drop, fuse=fuse)
        if not armed:
            actor.close_inventory()
            return False
    why = actor.item_menu(PID_DYNAMITE_ARMED, ITEM_MENU_DROP)
    time.sleep(0.6)
    actor.close_inventory()
    dropped = win32.wait_for(lambda: actor._count(PID_DYNAMITE_ARMED) == 0, 3, 0.1)
    actor.log.emit("dynamite", dropped=dropped, why=why)
    if not dropped:
        return False  # still carried: it goes off on the player; the step fails and the fight/heal logic takes over
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    away = {
        t
        for r in (3, 4, 5)
        for t in geometry.ring(drop, r)
        if t not in obs.blocked and geometry.distance(t, CAVE_SAFE) <= 12
    }
    nav.go_to(actor, away, max_steps=40, fight=False)
    down = win32.wait_for(lambda: quests.mvar(actor, 0) != 0, 75, 0.5)
    # CAVEWALL sets map_var 0 first, then fades out (600) and only after it gives the karma and the XP: read too
    # soon, a collapse looked like +0 karma, +0 XP (the cave sealed, +300, +3)
    win32.wait_for(lambda: karma(actor) >= k0 + 3, 15, 0.25)
    actor.log.emit("cave_wall", down=down, karma=karma(actor) - k0, xp=experience(actor) - xp0)
    return down and karma(actor) >= k0 + 3


IDEALIST_START = [
    Step("a weapon in hand", lambda a: a.ready_weapon().ok),
    Step("day 1: the vault opens", day_one),
    Step("into the vault (the Overseer's floor)", into_the_vault, checkpoint=True, tries=4),
    Step("the library computers", library, checkpoint=True),
    Step("the Overseer's supplies", supplies, checkpoint=True),
    Step("out of the vault", lambda a: quests.leave_map(a)),
    Step("to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("morning", daylight, checkpoint=True),
    Step("a Scout Handbook (West)", lambda a: book_from(a, BOOKSHELF_SHADYW)),
    Step("the .223 rounds (West)", take_from(SHADYW_223), optional=True, tries=2),
    Step("Katrina's questions", katrina),
    Step("Aradesh: the radscorpions", scorpion_quest, checkpoint=True),
    Step("the cook's compliment", cook, checkpoint=True, tries=1, optional=True),
    Step("Seth: to the caves", seth_to_the_caves, checkpoint=True),
    Step("the caves' floor: rounds and a stimpak", the_floor),
    Step("the radscorpions", clear_the_caves, checkpoint=True, tries=6),
    Step("a Scorpion Tail", scorpion_tail),
    Step("out of the caves", lambda a: quests.leave_map(a)),
    Step("back to Shady Sands", lambda a: travel(a, "shady sands", 0).startswith("SHADY"), checkpoint=True, tries=6),
    Step("Aradesh: the nest destroyed", nest_reported),
    Step("Razlo's antidote", razlo_antidote, checkpoint=True),
    Step("Jarvis cured", jarvis, checkpoint=True),
    Step("to Shady Sands East", lambda a: quests.take_exit(a, "SHADYE")),
    Step("Curtis's crop rotation", curtis, checkpoint=True),
    Step("a Scout Handbook (East)", lambda a: book_from(a, BOOKCASE_SHADYE)),
    # the days away that SHADYWST.INT wants before it takes Tandi are spent in Vault 15
    *VAULT_15,
    Step("two days away", two_days_away, checkpoint=True),
    Step("back west: Tandi taken", tandi_taken, checkpoint=True),
    Step("Aradesh: Tandi is missing", tandi_quest),
    Step("Razlo's gift", razlo_gift, checkpoint=True),
    Step("to the Khans", lambda a: travel(a, "raiders", 0).startswith("RAIDERS"), checkpoint=True, tries=6),
    Step("Garl lets her go", garl, checkpoint=True, tries=5),
    Step("Tandi home", tandi_home, checkpoint=True, tries=6),
    Step("Aradesh: the reward", tandi_reward, checkpoint=True),
    # after Tandi is home: while she is gone Seth talks only of her (SETH.INT TanSeth), no trip to the caves
    Step("Seth: back to the caves (the dynamite)", back_to_the_caves, checkpoint=True),
    # a Traps roll arms it: its outcome stands (no reloading for luck)
    Step("the cave wall blown", the_cave_wall, checkpoint=True, tries=1, optional=True),
    Step("out of the caves again", lambda a: not on_map(a, "CAVES") or quests.leave_map(a)),
]
