"""The Idealist's best-outcome routes (the quests from the game's scripts and dialogue files): steps checked against
the game's variables, XP and karma, run by routes.run like the Agent's (python -m f1.routes ROUTE, from the kept save
idealist-start).

    idealist_start    Vault 13: day 1 reached on the world map (the vault lets no one back in on day 0,
                      V13COMP.INT), the library computers, the Overseer's supplies, Theresa. Shady Sands: a Scout
                      Handbook, Katrina's questions, the cook's compliment, the radscorpions, a tail for Razlo's
                      antidote, Jarvis cured, Curtis's crop rotation, the second Scout Handbook, two days away until
                      the raiders take Tandi, then Garl talked down and Tandi home.
    idealist_junktown Junktown: Lars's Skulz job, Sherry turned, Killian's front door open before his first talk,
                      Kenji, the job, Gizmo's confession, the raid; Trish and Saul; a night in the Crash House and
                      Sinthia's hostage-taker talked down; Neal's urn taken while he sleeps and given back; Sherry's
                      testimony to Lars.
"""

import time
from collections.abc import Callable

from f1 import (
    clock,
    dialogue,
    geometry,
    knowledge,
    loot,
    nav,
    perception,
    quests,
    scripts,
    session,
    state,
    ui,
    watchdog,
    win32,
    world,
    worldmap,
)
from f1.actions import ITEM_MENU_DROP, ITEM_MENU_USE, PID_STIMPAK, Actor
from f1.engine_map import Obj
from f1.routes import (
    BOS_ENTRY_LIFT,
    BOS_MAIN_LIFT,
    BOS_MAIN_LIFT_UPPER,
    BOS_UP_LIFT_34,
    CELL_MANHOLE,
    GLOW_BEAM_AIMS,
    GLOW_BODY,
    PID_BROTHERHOOD_TAPE,
    SETH_POST,
    WATER_CHIP,
    Step,
    ammo_from_rutger,
    armor_and_gun,
    by_day,
    clear_the_caves,
    gang_loot,
    gizmos_confession,
    hand_in,
    hand_in_the_tape,
    heal_below,
    heal_up,
    hour,
    in_town,
    kenji,
    killians_job,
    nest_reported,
    on_map,
    out_of_the_bunker,
    pick_lock,
    radiation,
    recorder_in_hand,
    rope_on_the_beam,
    scorpion_quest,
    seth_to_the_caves,
    settle,
    stimpaks_from_kane,
    supplies,
    tandi_quest,
    tandi_reward,
    the_evidence,
    the_glow_quest,
    the_raid,
    the_tape,
    travel,
    up_the_rubble,
    zone,
)

DAY = 864000  # game ticks
PID_ANTIDOTE, PID_SCOUT_HANDBOOK, PID_SCORPION_TAIL = 49, 86, 92
LIBRARY_COMPUTERS = (17533, 21134)  # VAULT13 e2, LIBCOMP.INT: Science on one rolls IN (1d10 <= 10): 350 XP, 6 h
BOOKSHELF_SHADYW, BOOKCASE_SHADYE = 15931, 13097  # unscripted containers, a Scout Handbook each (SHADYE: and a Rope)
# Free loot on the route's maps (f1.atlas: no script, not locked), taken in steps of their own:
SHADYW_223 = 14287  # a Bookcase: two boxes of .223 FMJ (50 rounds each) and BB's, for the Hunting Rifle below
JUNKKILL_RIFLE = 24903  # Shelves in Killian's store: a Hunting Rifle (8-20); not taken: a use of them is a theft (live)
JARVIS_BED = 22728  # SHADYW: JARVIS.INT use_obj_on: an Antidote cures him, 400 XP, karma +1
# THERESA.MSG path A (reaction level 2): 103 -> 110 (IQ 6) -> a Speech roll, once (local_var 7) -> Theresa08 sets
# CALM_REBELS 2 -> 123; 750 XP when the talk ends
THERESA_CALM = [
    "it's going pretty well, thanks",
    "the overseer is simply trying to protect us",
    "don't worry, i'll take care of it",
]
# SSGUIDE.MSG: all seven of DialogMain's questions in one talk give 250 XP (local_var 5); a drawn weapon only gets
# "put that away" (DialogWeapon)
KATRINA_QUESTIONS = [
    "please help me",
    "what should i do if i get hurt",
    "where can i get some better equipment",
    "tell me more about bartering",
    "tell me about this place",
    "tell me about the rest of the world",
    "tell me about yourself",
    "where was this vault of yours",
]
COOK_COMPLIMENT = ["hmmm! that smells really good"]  # COOK.MSG 102: only at the very first talk, after an LK check
CURTIS_ROTATION = ["what are you doing", "you have all of your fields planted", "you only plant some of your fields"]
RAZLO_ANTIDOTE = ["i have a sample of the radscorpion poison"]  # RAZLO.MSG 146, 06:00-19:00: 4 h, 250 XP
RAZLO_GIFT = ["tandi has been kidnapped", "i think that she was kidnapped", "many people, including myself"]
# GARL.MSG 133 (IQ 5) reaches garl15 with no roll, then 141 (IQ 6, Speech >= 45): Speech +10 or a CH check
GARL_TALKED_DOWN = ["i've come in peace to negotiate", "i represent a threat you don't even understand"]


def karma(actor: Actor) -> int:
    return quests.gvar(actor, "PLAYER_REPUATION")


def experience(actor: Actor) -> int:
    return actor.snap().experience


def gained(actor: Actor, xp0: int, amount: int) -> bool:
    """At least `amount` XP since `xp0` (Swift Learner only adds)."""
    return experience(actor) - xp0 >= amount


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


def scripted(actor: Actor, tile: int, script: str) -> frozenset[int]:
    """The address of the object on `tile` (this elevation) that runs `script`: the click aims where the engine names
    it. Aimed at the hex alone, Science on the library's first computer hit the Central Core in front of it ("You
    cannot get there.", four tries)."""
    s = actor.snap()
    return frozenset(
        t.address
        for t in world.things(actor.mem)
        if t.tile == tile and t.elevation == s.elevation and scripts.script_of(actor.mem, t.address) == script
    )


def take_from(tile: int) -> Callable[[Actor], bool]:
    """A step's run: the free container or item on `tile` (f1.atlas) taken, on a path out of the foes' notice. Done
    too when it is gone already (taken before, or never there)."""

    def run(actor: Actor) -> bool:
        f = next((f for f in loot.finds(actor, town_containers=True) if f.tile == tile), None)
        return f is None or loot.take(actor, f, loot.danger(actor)) == loot.TAKEN

    return run


def the_floor(actor: Actor) -> bool:
    """Everything free that is worth it on this floor (loot.sweep, the whole floor, out of the foes' notice): before a
    fight, the rounds that lie about. The radscorpion caves' file puts two boxes of 10mm JHP and one of 10mm AP (30
    rounds each) and a Stimpak there; a run found one box and ran dry after four kills."""
    loot.sweep(actor, max_hexes=200, budget_s=180)
    return True


def library(actor: Actor) -> bool:
    """Science on each library computer: 350 XP and 6 hours each. LIBCOMP.INT use_skill_on_p_proc answers "You use
    the Vault computer teaching system." at once, then, after a fade and 6 hours, "...researching some important
    information." with the XP (its do_check(IN) passes at IN 10), or "...doing nothing of importance" when that check
    fails; a computer used before, "...learn nothing of importance". Every one of those ends it: the lines since the
    use, joined (the box wraps them), are read for them. Read three lines back only, the answer after the fade was
    missed and a run skipped the step."""
    for tile in LIBRARY_COMPUTERS:
        xp0, n0 = experience(actor), actor.mem.glob("disp_start")

        def done(xp0=xp0, n0=n0) -> bool:
            text = " ".join(state.messages_since(actor.mem, n0))
            return experience(actor) > xp0 or "nothing of importance" in text or "important information" in text

        beside(actor, tile)
        actor.use_skill_on("science", tile, done, targets=scripted(actor, tile, "libcomp"))
        if not win32.wait_for(done, 12, 0.25):  # the answer follows a fade and six hours passing
            return False
    return True


def beside(actor: Actor, tile: int) -> bool:
    """Onto a free hex next to `tile`. The engine's own walk to an object can fail where ours does not: Science on
    the library's computer at 17533 from two hexes away answered "You cannot get there." every time, and from the
    hex beside it (17534) it worked."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    free = {t for t in geometry.ring(tile, 1) if t not in obs.blocked}
    return s.dude.tile in free or (bool(free) and nav.go_to(actor, free, max_steps=80)[0])


def theresa(actor: Actor) -> bool:
    """One floor up (e1), Theresa (07:15-19:15): the rebels calmed by one Speech roll, or a reload for another try."""
    if quests.gvar(actor, "CALM_REBELS") == 2:
        return True
    if actor.snap().elevation != 1 and not quests.use_elevator(actor, "2"):
        return False
    actor.holster(True)
    quests.talk(actor, "theresa", THERESA_CALM, strict=True)
    return quests.gvar(actor, "CALM_REBELS") == 2 and not actor.in_combat()


def read_book(actor: Actor, pid: int) -> bool:
    """A book read from the inventory: it goes (obj_use_item destroys it) and its skill gets (100 - skill) / 10
    points, twice as many percent when tagged (protinst.c obj_use_book), in (11 - IN) hours."""
    if not actor._count(pid):
        return True
    return actor.use_on_self(pid).ok


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
    praised = karma(actor) > k0 or any("smells really good" in chosen.lower() for _r, _o, chosen in steps)
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


def two_days_away(actor: Actor) -> bool:
    """SHADYWST.INT takes Tandi on an entry when days_since_visited() > 1 (whole days since the map was left) and the
    radscorpions are done: two days and a bit since Shady Sands was left (the Vault 15 trip), rested out where
    the trip was shorter; without a record of the leaving, two days from now."""
    target = (_left_shady or actor.snap().game_time) + 2 * DAY + DAY // 10
    for _ in range(12):
        if actor.snap().game_time >= target:
            return True
        if not actor.rest("until morning" if 6 <= hour(actor) < 18 else "until noon").ok:
            actor.rest("6 hours")
    return actor.snap().game_time >= target


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
    """The town map's fourth entrance: VAULT13's third floor (the Overseer, the library), known from day 1."""
    return travel(actor, "vault 13", 3).startswith("VAULT13") and actor.snap().elevation == 2


def tandi_home(actor: Actor) -> bool:
    """SHADYWST.INT turns TANDI_STATUS 5 (freed, following) into 2 on entering Shady Sands West, not East."""
    return travel(actor, "shady sands", 0).startswith("SHADYW") and quests.gvar(actor, "TANDI_STATUS") == 2


def garl(actor: Actor) -> bool:
    if quests.gvar(actor, "TANDI_STATUS") != 5:
        actor.holster(True)
        quests.talk(actor, "Garl", GARL_TALKED_DOWN, strict=True)
    return quests.gvar(actor, "TANDI_STATUS") == 5 and not actor.in_combat()


# Vault 15 (checked in the scripts). VAULTENT's ladder (bv2vault) leads to
# VAULTBUR e0. Each elevator shaft is descended by a rope used on it (BVELV1W/BVELV2E use_obj_on_p_proc: pid 127, the
# shaft is replaced by a roped one), then used: e0 13904 -> e1 23118, e1 22702 -> e2 17108. Up: e2 16708 (BVELV3) ->
# e1 23102, e1 22718 (BVELV2W) -> e0 14304, the ladder at 20519 (BVLAD, an AG roll: a fall only hurts) to VAULTENT.
# RUBCHIP spatials on e2: 23099 (radius 6) and 22701 (radius 8), +500 XP each once. One rope comes from Shady Sands
# East's bookcase, the other from the locker on e1 (25099, with the Leather Jacket for Dogmeat); e2's lockers hold the
# 10mm SMG (20500) and the Dynamite with two Frag Grenades (20502).
PID_ROPE, PID_DYNAMITE = 0x7F, 0x33
V15_SHAFTS_DOWN = {0: (13904, 1), 1: (22702, 2)}  # elevation: (shaft, the elevation it leads to)
V15_SHAFTS_UP = {2: (16708, 1), 1: (22718, 0)}
V15_LOCKERS = {0: (13678,), 1: (25099,), 2: (20500, 20502)}
V15_RUBBLE = ((23099, 6), (22701, 8))
V15_LADDER_UP = 20519
_left_shady: int | None = None  # the game time Shady Sands was left for Vault 15 (two_days_away)


def to_vault_15(actor: Actor) -> bool:
    global _left_shady
    if on_map(actor, "VAULTENT") or on_map(actor, "VAULTBUR"):
        return True
    _left_shady = actor.snap().game_time
    return travel(actor, "vault 15", 0).startswith("VAULT")


def down_the_ladder(actor: Actor) -> bool:
    return on_map(actor, "VAULTBUR") or quests.use_scenery(
        actor, ("bv2vault", "Ladder"), lambda: on_map(actor, "VAULTBUR"), "the ladder down"
    )


PID_ROPED_SHAFT = 0x02000386  # the "Elevator Shaft" BVELV1W/BVELV2E create in place of the bare one


def shaft_object(actor: Actor, tile: int):
    """The shaft on `tile` of this elevation (the roped one replaces the bare one on the same hex)."""
    s = actor.snap()
    here = [
        t
        for t in world.things(actor.mem)
        if t.tile == tile and t.elevation == s.elevation and t.name == "Elevator Shaft"
    ]
    return next((t for t in here if t.pid == PID_ROPED_SHAFT), here[0] if here else None)


def v15_lockers(actor: Actor) -> bool:
    """This floor's lockers emptied (the rope and the dynamite matter; loot.sweep might leave a rope as not worth
    it), then whatever else is worth it on the floor. Fails while a locker still holds what the way on needs: e1's
    rope, e2's dynamite (a fight had kept e1's locker shut)."""
    e = actor.snap().elevation
    for tile in V15_LOCKERS.get(e, ()):
        beside(actor, tile)
        actor.loot(tile)
    loot.sweep(actor, max_hexes=200, budget_s=120)
    if e == 1:
        return actor._count(PID_ROPE) > 0 or not shaft_needs_rope(actor, V15_SHAFTS_DOWN[1][0])
    if e == 2:
        return actor._count(PID_DYNAMITE) > 0
    return True


def shaft_needs_rope(actor: Actor, tile: int) -> bool:
    shaft = shaft_object(actor, tile)
    return shaft is not None and shaft.pid != PID_ROPED_SHAFT


def v15_down(actor: Actor) -> bool:
    """A rope on this floor's shaft, then down it."""
    s = actor.snap()
    if s.elevation not in V15_SHAFTS_DOWN:
        return s.elevation == 2
    shaft, below = V15_SHAFTS_DOWN[s.elevation]
    if shaft_needs_rope(actor, shaft) and actor._count(PID_ROPE) == 0:
        actor.log.emit("v15", why="no rope", elevation=s.elevation)
        return False
    beside(actor, shaft)
    if shaft_needs_rope(actor, shaft):
        target = shaft_object(actor, shaft)
        roped = lambda: not shaft_needs_rope(actor, shaft)
        if not actor.use_item_on(PID_ROPE, shaft, roped, targets=frozenset({target.address})).ok and not roped():
            return False
        actor.set_mouse_mode(0)  # out of the use cursor the rope left
        win32.wait_for(lambda: False, 0.5)
    target = shaft_object(actor, shaft)
    targets = frozenset({target.address}) if target else frozenset()
    down = lambda: actor.snap().elevation == below
    return actor.click_object(shaft, down, 30, aims=quests.SCENERY_AIMS, targets=targets) or down()


def v15_up(actor: Actor) -> bool:
    """Up this floor's shaft (already roped from above), or the ladder out of the vault on e0."""
    s = actor.snap()
    if on_map(actor, "VAULTENT"):
        return True
    if s.elevation == 0:
        beside(actor, V15_LADDER_UP)
        return quests.use_scenery(actor, ("bvlad", "Ladder"), lambda: on_map(actor, "VAULTENT"), "the ladder up")
    shaft, above = V15_SHAFTS_UP[s.elevation]
    beside(actor, shaft)
    target = shaft_object(actor, shaft)
    targets = frozenset({target.address}) if target else frozenset()
    up = lambda: actor.snap().elevation == above
    return actor.click_object(shaft, up, 30, aims=quests.SCENERY_AIMS, targets=targets) or up()


def the_rubble(actor: Actor) -> bool:
    """Into both RUBCHIP triggers on e2: 500 XP each, once."""
    xp0 = experience(actor)
    for centre, radius in V15_RUBBLE:
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        inside = {t for k in range(radius) for t in geometry.ring(centre, k) if t not in obs.blocked}
        nav.go_to(actor, inside, max_steps=80)
        win32.wait_for(lambda: False, 1.5)  # the spatial fires on the move into it
    actor.log.emit("v15_rubble", xp=experience(actor) - xp0)
    return experience(actor) > xp0


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
SETH_BACK_TO_CAVES = ["i want to know about the radscorpions", "take me to the radscorpion caves", "yes."]


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
    # soon, a collapse looked like +0 karma, +0 XP ("...sealed in their cave", +300, +3)
    win32.wait_for(lambda: karma(actor) >= k0 + 3, 15, 0.25)
    actor.log.emit("cave_wall", down=down, karma=karma(actor) - k0, xp=experience(actor) - xp0)
    return down and karma(actor) >= k0 + 3


VAULT_15 = [
    Step("to Vault 15", to_vault_15, checkpoint=True, tries=6),
    Step("Vault 15: down the ladder", down_the_ladder, checkpoint=True),
    Step("Vault 15: the first floor's lockers", v15_lockers),
    Step("Vault 15: down the first shaft", v15_down, checkpoint=True),
    Step("Vault 15: the second floor's lockers", v15_lockers),
    Step("Vault 15: down the second shaft", v15_down, checkpoint=True),
    Step("Vault 15: the rubble (the chip is buried)", the_rubble, checkpoint=True),
    Step("Vault 15: the third floor's lockers", v15_lockers, checkpoint=True),
    Step("Vault 15: up to the second floor", v15_up),
    Step("Vault 15: up to the first floor", v15_up),
    Step("Vault 15: up the ladder", v15_up, checkpoint=True),
    Step("out of Vault 15", lambda a: quests.leave_map(a)),
    Step(
        # East: SHADYWST.INT counts the days since West was left and takes Tandi on the next entry to West
        "back to Shady Sands East",
        lambda a: travel(a, "shady sands", 1).startswith("SHADYE"),
        checkpoint=True,
        tries=6,
    ),
]


# Junktown (stopping Gizmo, the Skulz; LARS, SHERRY, KILDOOR1, KILLIAN, KENJI scripts)
KILLIAN_FRONT_DOOR = 27483  # JUNKKILL: KILDOOR1 shuts it on every map entry; Kenji comes in through it
# LARS.MSG: Lars00 107 -> Lars05 120 (IQ 6) -> Lars07 123 -> Lars08 sets BUST_SKULZ 1. Only before the confession or
# after the raid: while CAPTURE_GIZMO is 1 his talk is about the raid.
LARS_SKULZ = [
    "can you give me the big picture on junktown",
    "so, what keeps you lawboys from just busting gizmo and the skulz",
    "maybe i can help you with that",
]
# SHERRY.MSG: the first talk ends at once and safely ("Bring it on!" in Sherry02 is a fight); the second, Sherry15:
# 138 (IQ 5) -> 142 (IQ 6) -> 145 (IQ 6) -> a Speech roll -> Sherry19 147 -> Sherry21 sets SHERRY_TURNS 1 and her
# local_var 6 to the day; more than a day later (critter_p_proc) she leaves the gang, SHERRY_TURNS 2.
SHERRY_MEET = ["i'm new to this town", "sorry, i thought this was the bathroom"]
SHERRY_TURN = [
    "what have you been up to lately",
    "you could always try doing something else",
    "but what will you do when you get older",
    "you should probably get out while you still can",
]
# Sherry22: 166 (BUST_SKULZ set) -> Sherry27 163 (IQ 6) -> Sherry28 sets GENERIC_FILLER_20 (her testimony); then
# Lars12 139 -> Lars14 145 -> Lars16 sets BUST_SKULZ 2, and his talk gives 500 XP and karma +3
SHERRY_TESTIFY = ["i need your testimony to put away the skulz", "they have been hurting people"]
LARS_TESTIMONY = ["i have testimony about the illegal activities", "i've convinced one of them, sherry"]


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
    "what do you do here",
    "hmm. what's the deal",
    "how did you become a boxer",
    "what happened to your brothers",
    "why do you stay in junktown",
    "yes, i've met trish",
    "she's concerned for your welfare",
    "i think she's worried about you",
]
CRASH_ROOM = (19089, 19289, 19489)  # JUNKKILL: CRASHRM's spatial hexes, room #1
MARCELLE_ROOM = ["i need a good night's sleep", "just one night"]  # 25 caps: RENT_TIME = tomorrow
ROOM_REST = ["[yes]"]  # CRASHRM: rest until 10:00; CrashRm02 then creates the raider and sets map_var 3 to 1
MARCELLE_HELP = ["hmm. i'll check it out"]  # Marcelles00 sets SAVE_SINTHIA 1
# JTRAIDER.MSG: phase 1 (he talks on sight): Raider0 105 -> Raider4 111 -> Raider5 114 -> Raider6 (after it, a player
# within 4 hexes is attacked); phase 2, the next talk: Raider9 123 -> Raider14 132 -> a Speech +20 roll sets var6;
# phase 3: Raider21 146 -> Raider22 149 "Sure." -> Raider24 154, 100 caps -> safe(): map_var 4 (JUNKKILL's
# map_update gives 1000 XP), SAVE_SINTHIA 2. var1, var2 and var6 are script variables: no load between the phases.
HOSTAGE_PHASES = [
    ["there is no need for violence", "we can talk this over", "by holding this woman hostage"],
    ["why do you want to hurt her", "i trust you. let's work through this"],
    ["you tell me. you're in charge", "sure.", "here's your money"],
]
NEAL_URN = ["i've got your urn back for you"]  # NEAL.MSG 144 -> Neal11: karma +2


def day(actor: Actor) -> int:
    return actor.snap().game_time // DAY


def junktown_map(actor: Actor, name: str) -> bool:
    """To one of Junktown's three maps. The gate (JUNKENT) and the casino (JUNKCSNO) meet only through Killian's
    street (JUNKKILL): from Lars's gate the urn's step asked for the casino and found no exit (five tries)."""
    if not on_map(actor, name) and "JUNKKILL" not in (name, actor.snap().map_name.removesuffix(".SAV")):
        quests.take_exit(actor, "JUNKKILL")
    if not on_map(actor, name):
        quests.take_exit(actor, name)
    return on_map(actor, name)


def rest_until(actor: Actor, start: float, end: float) -> bool:
    """Rest, in the steps the Pip-Boy offers, until the hour is in [start, end)."""
    for _ in range(8):
        h = hour(actor)
        if start <= h < end:
            return True
        if 0 < start - h <= 1:
            how = "1 hour"
        elif h < start <= 18:
            how = "until evening" if start > 12 else "until noon" if start > 6 else "until morning"
        else:
            how = "6 hours"
        if not actor.rest(how).ok:
            return False
    return start <= hour(actor) < end


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
    a failure, "You swipe the urn, and hope that nobody saw...", sets map_var 4, caught stealing, and the step fails
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
    """Neal talks from 16:00 to 04:10: "I've got your urn back for you." -> karma +2. NEAL.INT talk_p_proc floats
    "Zzzz" from 04:10 to 13:00 and "We're closed. Come back around four o'clock." from 13:00 to 16:00; both read live
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


# The Hub (planned from the scripts and maps). First part: the
# arrival, Deputy Fry, Stapleton's disk, the shopping, the Junk for Necropolis' pump.
FRY_HELLO = ["what is this place?", "that's it, thanks.", "no."]  # FRY.MSG 103, 116, 109: DECKER_KNOWN 1
# STAPLE.MSG 105, 110, 114: 750 caps, no roll; only while FIND_WATER_CHIP != 2 (before the chip's hand-in). Never
# 116, the blowtorch.
STAPLETON_DISK = ["i am looking for a water chip.", "can i have the holodisk?", "sounds good. here's the money."]
PID_VAULT_RECORDS, PID_JUNK = 230, 0x62
OLDTOWN_JUNK = 15671  # HUBOLDTN e0: an unscripted crate by Jacob's shop, the Junk Necropolis' pump wants


def fry(actor: Actor) -> bool:
    if quests.gvar(actor, "DECKER_KNOWN"):
        return True
    rest_until(
        actor, 7, 17
    )  # FryDialog: he sleeps from 18:00 to 06:00 ("Pfffftttt . . . Chrrrr . . ."); the Hub by day
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
# 21080), Loxley's test (LOXLEY.MSG 114, 167, 127, 131: STEAL_NECKLACE 1, 900 XP; "i'm " alone would match 121, the
# jail line), Jasmine's kit (JASMINE.MSG 111: Lock Picks, 2 Flares). Nothing may be picked up in the Circle: Cleo,
# Smitty and Jasmine attack on a pickup.
CIRCLE_STAIRS = "thfu2d"
CIRCLE_DOORS = (19482, 22684, 25882)
LOXLEY_TEST = ["what is this place?", "okay, what do i have to do?", "what's the test?", "i'm ready!"]
JASMINE_KIT = ["no problem. i'm outta here."]
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
        # DOOR.INT's "You unlock the door." only sets its local_var(0); the engine's lock bit goes at the next use,
        # which opens it (routes.pick_lock): so the door is used first, and picked only when that does not open it
        if not nav.open_door(actor, tile):
            pick_lock(actor, tile, door.address, tries=8)
            if not nav.open_door(actor, tile):
                # a critical failure jams the lock ("The lock is jammed.", the 1x chain) until midnight
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
GEORGE_APPOINTMENT = ["i have an appointment with mr. hightower"]  # HTWRGRGE.MSG 104
LEON_PASS = ["i must speak with mr. hightower.", "thank you."]  # HTWRLEON.MSG 102 (Speech 0), 106
HIGHTOWER_BYE = ["my name is "]  # HIGHTOWR.MSG 104 "My name is <name>.  Leon let me in.": Daren04, shown out
BOX_UNLOCKED = ("successfully unlocked the strongbox", "already unlocked")
BOX_TRAP = "set off a trap"


HIGHTOWERS_HEX = 24522  # HIGHTOWR critter_p_proc: by day he starts his talk (every answer ends in "the door") there


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
        # George01b, Speech +10 (121 for the Idealist), George05 sets map_var 3 ("...I'll let him know you're here")
        actor.holster(True)
        quests.talk(actor, "htwrgrge", GEORGE_APPOINTMENT, strict=True)
        settle(actor)
        if quests.mvar(actor, 3) == 0:
            return False
    heal_below(actor, 40)  # the box's trap: 6-36 damage
    # Leon (HTWRLEON, "Mysterious Stranger") starts a talk once within 6 hexes: 102 "I must speak with Mr.
    # Hightower." is a Speech roll at 0 (111 here), then 106 "Thank you."; left unanswered, the skill keys sent into
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
        # the lock's own answer: the aim's hovers print "You see a locked strongbox." too, and that ended each try
        # before its click had answered (three tries, 33 s each, the 1x chain)
        said = lambda n0=n0: " ".join(
            m for m in state.messages_since(actor.mem, n0) if not m.lower().startswith("you see")
        ).lower()
        answered = lambda said=said: "strongbox" in said()
        actor.use_skill_on("lockpick", HEIGHTS_BOX, answered, targets=targets)
        win32.wait_for(answered, 3, 0.1)
        text = said()
        actor.log.emit("strongbox", said=text[-160:])
        if any(k in text for k in BOX_UNLOCKED) or BOX_TRAP in text or "jammed" in text:
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


LOXLEY_NECKLACE = ["here's the necklace"]  # LOXLEY.MSG 188: STEAL_NECKLACE 2, 500 XP (loxley29)


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
    "do you have a water chip?",
    "my vault's chip broke",
    "how much are we talking about?",
    "alright, here is the money.",
    "yes, the vault needs water.",
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


# Irwin (HUBDWNTN e0 29124, IRWIN): from level 5, "Problems? Maybe I can help you." (935:102) then "Just tell me
# where..." (935:107): an hour passes and the player is on his farm, HUBMIS1, where seven raiders ("Mercenary",
# 30 HP, FARMRAID) attack on sight. DESTROY_MASTER_3 turns 2 once all are dead; back in town "It sure is, buddy."
# (935:113): 500 XP, karma +2, a .223 Pistol (DESTROY_MASTER_3 501). The farm: Metal Armor on the floor at 18492,
# stimpaks at 21304 and in the bookcases 19691, 21489.
IRWIN_JOB = ["problems? maybe i can help you.", "just tell me where and i'll take care of the rest."]
IRWIN_PAID = ["it sure is, buddy."]
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


# Necropolis (planned from the scripts): the ghoul
# leader's three books (LEADER leader19a, Repair < 60), the Watershed's spare Junk, past Harry by sneaking (no Speech
# line is sure below 120: "But I am a ghoul!" is -20), the pump fixed with the Junk before the chip (Repair -5, plain
# failures retried; NH2OPUMP), then the chip by the Agent's steps, and out past Harry again.
LEADER_BOOKS = [
    "yes, i'm looking for water",
    "who is this set",
    "why does he let you survive",
    "where does all of this water come from",
    "how do i get to the watershed",
    "thanks.",
    "my people need the water chip to survive",
    "your pump could be fixed",
    "do you mean these parts",
    "anything else i should know",
]
HARRY_GHOUL = ["but i am a ghoul", "just passing through"]  # HARRY.MSG 103 (Speech -20), 150 (taken, stands)
PID_DEANS_ELECTRONICS = 76
WATRSHD_SPARE_JUNK = 16658  # WATRSHD e0, sewer region C (the Cover and Sewer Hole at e1 17290, the ladder up at 15888)
WATRSHD_PUMP = 12257  # WATRSHD e1, NH2OPUMP: the Junk used on it, Repair -5
HARRY_WAIT = 14891  # 10 hexes from Harry, out of his zone (static, the plan)
HARRY_FAR_SIDE = 13268
SNEAK_WAIT_S = 300  # at most 5 game minutes of the game's own Sneak re-rolls (one each 60 game s)


def leaders_books(actor: Actor) -> bool:
    if actor._count(PID_DEANS_ELECTRONICS) >= 3 or actor.skills().get("repair", 0) >= 60:
        return True
    if not on_map(actor, "HALLDED"):
        return False
    actor.holster(True)
    quests.talk(actor, "leader", LEADER_BOOKS, strict=True)
    settle(actor)
    return actor._count(PID_DEANS_ELECTRONICS) > 0


def read_the_books(actor: Actor) -> bool:
    for _ in range(4):
        if not actor._count(PID_DEANS_ELECTRONICS):
            break
        read_book(actor, PID_DEANS_ELECTRONICS)
    return actor._count(PID_DEANS_ELECTRONICS) == 0


def spare_junk(actor: Actor) -> bool:
    """The second Junk, from the Watershed's sewer region C (a critical failure at the pump destroys the parts)."""
    if actor._count(PID_JUNK) >= 2:
        return True
    s = actor.snap()
    if s.elevation == 1:
        quests.use_scenery(actor, ("nwup2dn2", "Sewer Hole"), lambda: actor.snap().elevation == 0, "the sewer hole")
    if actor.snap().elevation == 0:
        take_from(WATRSHD_SPARE_JUNK)(actor)
        quests.use_scenery(actor, ("nwdn2up1", "Ladder"), lambda: actor.snap().elevation == 1, "the ladder up")
    return actor.snap().elevation == 1  # the spare is a precaution: without it the route goes on


def harry(actor: Actor):
    return quests.find_critter(actor, "harry")


def harry_zone(actor: Actor) -> frozenset[int]:
    h = harry(actor)
    if h is None:
        return frozenset()
    near = {t for d in range(5) for t in (geometry.ring(h.tile, d) if d else [h.tile])}
    return perception.zone([h], sneaking=True) | frozenset(near)


def sneak_ready(actor: Actor) -> bool:
    return actor.sneaking() and actor.mem.glob("sneak_working") != 0


def past_harry_sneaking(actor: Actor, goals: set[int]) -> bool:
    """To `goals` round Harry's notice while sneaking. The game re-rolls the sneak each 60 game s: the crossing
    waits for a working one, at most SNEAK_WAIT_S game seconds, then goes anyway. If he talks: "But I am a ghoul!"."""
    s = actor.snap()
    if s.dude.tile in goals:
        return True
    if dialogue.read(actor.mem).active:  # a talk he opened first: keys sent now would pick its options
        dialogue.converse_strict(actor.mem, HARRY_GHOUL, log=actor.log)
        time.sleep(1.0)
        if actor.in_combat() or dialogue.read(actor.mem).active:
            return False
    actor.holster(True)
    actor.sneak(True)
    t0 = s.game_time
    with clock.fast(actor.pid):  # a test run's wait at its speed; a full run's at the game's own
        while not sneak_ready(actor) and actor.snap().game_time - t0 < SNEAK_WAIT_S * 10:
            watchdog.quiet(15)
            win32.wait_for(lambda: sneak_ready(actor), 5, 0.25)
    actor.log.emit("harry", sneak_working=sneak_ready(actor), waited_s=(actor.snap().game_time - t0) / 10)
    for _ in range(4):
        if dialogue.read(actor.mem).active:
            dialogue.converse_strict(actor.mem, HARRY_GHOUL, log=actor.log)
            time.sleep(1.0)
        if actor.in_combat():
            actor.fight()
            if actor.in_combat() or actor.snap().dude.hp <= 0:
                return False
        if actor.snap().dude.tile in goals:
            return True
        avoid = harry_zone(actor) - goals
        ok, why = nav.go_to(actor, goals, max_steps=80, fight=False, avoid=avoid)
        actor.log.emit("harry", walk=ok, why=why, at=actor.snap().dude.tile)
        if not ok and why == "no path":
            nav.go_to(actor, goals, max_steps=80, fight=False)  # no way round his notice: straight on
    return actor.snap().dude.tile in goals


def to_the_far_side(actor: Actor) -> bool:
    if quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2 or not on_map(actor, "WATRSHD"):
        return True
    obs = nav.obstacles(actor.mem, actor.snap().elevation, actor.snap().dude.address)
    if actor.snap().dude.tile != HARRY_WAIT and geometry.distance(actor.snap().dude.tile, HARRY_FAR_SIDE) > 6:
        nav.go_to(actor, {HARRY_WAIT}, max_steps=60, fight=False)
    goals = {t for d in range(3) for t in (geometry.ring(HARRY_FAR_SIDE, d) if d else [HARRY_FAR_SIDE])}
    return past_harry_sneaking(actor, {t for t in goals if t not in obs.blocked})


def the_pump(actor: Actor) -> bool:
    """The Junk used on the pump: Repair -5. A plain failure keeps the parts (tried again: the game's own retry);
    a critical failure destroys them (the spare then)."""
    if quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2:
        return True
    xp0 = experience(actor)

    def fixed() -> bool:
        return quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2

    for _ in range(8):
        if not actor._count(PID_JUNK):
            break
        # beside the pump round Harry's notice (a plain walk there crossed his talk hex 14093, live);
        # a talk he opens is answered by "But I am a ghoul!"
        obs = nav.obstacles(actor.mem, actor.snap().elevation, actor.snap().dude.address)
        past_harry_sneaking(actor, {t for t in geometry.ring(WATRSHD_PUMP, 1) if t not in obs.blocked})
        if actor.in_combat() or dialogue.read(actor.mem).active:
            return False
        n0 = actor._count(PID_JUNK)
        actor.use_item_on(PID_JUNK, WATRSHD_PUMP, lambda n0=n0: fixed() or actor._count(PID_JUNK) < n0)
        actor.unequip("right")
        win32.wait_for(fixed, 5, 0.25)
        actor.log.emit("pump", fixed=fixed(), junk=actor._count(PID_JUNK))
        if fixed():
            break
        time.sleep(1.0)
    return quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2 and gained(actor, xp0, 1000)


def to_the_cell(actor: Actor) -> bool:
    if geometry.distance(actor.snap().dude.tile, CELL_MANHOLE) <= 1 or actor.snap().elevation == 0:
        return True
    obs = nav.obstacles(actor.mem, actor.snap().elevation, actor.snap().dude.address)
    return past_harry_sneaking(actor, {t for t in geometry.ring(CELL_MANHOLE, 1) if t not in obs.blocked})


def out_past_harry(actor: Actor) -> bool:
    """Out of the Watershed by its exits, round Harry while sneaking; at Speech 121 (level 6) "But I am a ghoul!"
    cannot fail if he talks."""
    if actor.snap().screen == "worldmap":
        return True
    obs = nav.obstacles(actor.mem, actor.snap().elevation, actor.snap().dude.address)
    exits = {t for t, d in obs.exits.items() if d[0] < 0}
    past_harry_sneaking(actor, exits)
    win32.wait_for(lambda: actor.snap().screen == "worldmap", 10, 0.25)
    if actor.snap().screen != "worldmap":
        quests.leave_map(actor)
    actor.sneak(False)
    return actor.snap().screen == "worldmap"


# Vault 13 with the chip (planned with Necropolis): Lyle starts the water thief (day 30 or later, 07:10-19:20),
# Theresa calms the rebels (Speech 0: sure at 121; not 17:00-17:10, her meeting), the thief caught at midnight on
# the Overseer's floor (WTRTHIEF: he walks from 16912 east and back, taking a flask by the box 20506; the search
# is a CH check, 30 %, a failure is his fight: either way WATER_THIEF 2), and the hand-in last (OVER over28 sets CALM_REBELS and
# WATER_THIEF to 3 unless each is 2).
LYLE_THIEF = ["hello, lyle. how are you holding up?", "i'll take a look and see what i can do."]  # LYLE.MSG 105, 110
THIEF_TALK = [
    "what are you doing here at this time",
    "looking for the water thief",
    "bullet in your head",
    "[search him]",
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
    7000. Searched before the flask: "You find nothing.". So the watch runs at the game's own speed after midnight,
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


IDEALIST_HUB = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("Fry: the Hub", fry, checkpoint=True),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("Stapleton's disk", stapletons_disk, checkpoint=True),
    Step("10mm from Rutger", ammo_from_rutger, checkpoint=True),
    Step("stimpaks from Kane", stimpaks_from_kane, checkpoint=True),
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


IDEALIST_NECROPOLIS = [
    *WATER_CHIP[:4],  # out, to Necropolis, the Hotel's sewer hole, the sewers to the Hall of the Dead
    Step("the ghoul leader's books", leaders_books, checkpoint=True),
    Step("the books read", read_the_books),
    *WATER_CHIP[4:6],  # the sewers to the Watershed, up to its south
    Step("the spare Junk", spare_junk, checkpoint=True, optional=True, tries=2),
    Step("past Harry, sneaking", to_the_far_side, checkpoint=True),
    Step("the pump fixed", the_pump, checkpoint=True),
    Step("to the cell manhole", to_the_cell, checkpoint=True),
    *WATER_CHIP[7:15],  # down the manhole, Vault 12, the cab, the chip, back up to the cell
    Step("heal before the road", heal_up),
    Step("out past Harry", out_past_harry, tries=4),
]

IDEALIST_VAULT13 = [
    Step("to Vault 13", lambda a: travel(a, "vault 13", 3).startswith("VAULT13"), checkpoint=True, tries=6),
    Step("Lyle: the water thief", lyles_worry, checkpoint=True),
    Step("Theresa calms the rebels", theresa_now, checkpoint=True, tries=1, optional=True),  # one roll, sure at 121
    # not optional: the hand-in after it ends the thief for good (WATER_THIEF 3); a search that fails is his fight,
    # which completes it too
    Step("the water thief at midnight", the_water_thief, checkpoint=True, tries=2),
    Step("the chip to the Overseer", chip_to_the_overseer, checkpoint=True),
]


# The Brotherhood and the Glow (planned from the scripts, within the game's time limits).
# Cabbot's talk puts the Glow on the world map (cabbot06: THE_GLOW 1). The beam down needs a rope, and Vault 15 took
# both of the route's ropes; Mitch in Hub Downtown sells them (ALLNONE, his box MITCHBOX at 27850 holds 5 Rope, a
# Tool, 3 RadAway, a Big Book of Science; his stock reaches his inventory only in a talk, Get_Stuff). Brotherhood to
# the Hub 2.4 days, the Hub to the Glow 5.3, the Brotherhood to the Glow 7.5 (f1.travel 100 1).
MITCH = "allnone"  # a "Merchant" by name: found by his script
PID_TOOL, PID_RAD_X, PID_RADAWAY, PID_10MM_SMG = 75, 109, 48, 9
# Vance (HUBOLDTN e0 16059, VANCE; his box 12846 holds 26 Rad-X): he sells only to who "knows" (MASTER_FILLER_9) or
# after Vance02a, Speech -30 or a CH -3 roll (VANCE.MSG 103, 109; a failure sends you off, the next talk starts
# over). His trade runs at barter mod -20: a Rad-X (cost 300) is 313 caps at Barter 46. The plan: two Rad-X make
# the Glow rad-free for 24 game hours (resistance 12 + 2 x 50, capped at 100: the Glow plan), four make two windows.
VANCE_SELL = [
    "i would like to buy some more stuff.",
    "who are you?",
    "good to meet you vance. what do you have to sell?",
]
# VANCE.MSG 122 (Vance06, a later talk: live), 103, 109


def from_merchant(
    merchant: str,
    where: str,
    pid: int,
    quantity: int = 1,
    plan: list[str] | None = None,
    pay_with: tuple[int, ...] = (),
) -> Callable[[Actor], bool]:
    def buy(actor: Actor) -> bool:
        from f1 import barter

        if actor._count(pid) >= quantity:
            return True
        if not hub_map(actor, where):
            return False
        actor.holster(True)
        goods = tuple(g for g in pay_with if actor._count(g))
        out = barter.buy(actor, merchant, plan or [], pid, quantity - actor._count(pid), pay_with=goods)
        actor.log.emit("shopping", item=knowledge.proto_name(pid), **out)
        settle(actor)
        return actor._count(pid) >= quantity

    return buy


def from_mitch(pid: int, quantity: int = 1) -> Callable[[Actor], bool]:
    return from_merchant(MITCH, "HUBDWNTN", pid, quantity)


IDEALIST_BROTHERHOOD = [
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("Cabbot: the Glow", the_glow_quest, checkpoint=True),
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("a rope from Mitch", from_mitch(PID_ROPE), checkpoint=True),
    Step("a Tool from Mitch", from_mitch(PID_TOOL), checkpoint=True, optional=True),
    Step("Old Town", lambda a: hub_map(a, "HUBOLDTN"), checkpoint=True),
    Step(
        "Rad-X from Vance",
        from_merchant("vance", "HUBOLDTN", PID_RAD_X, 4, VANCE_SELL, pay_with=(PID_10MM_SMG,)),
        checkpoint=True,
        tries=3,
    ),
]


# The Glow, deep (planned from the scripts and maps). Two Rad-X cap the
# radiation resistance at 100 for 24 game hours (proto 109: +50 each; stat.c's cap: measured live by the step).
# Lifts: a key used on a lift's door turns off its electric field (GLOYLDOR/GLORDDOR/GLOBLDOR use_obj_on: "...
# Authorization granted"; a plain use of an armed door rolls Traps -20 and shocks); the trigger is in the cab
# behind the door, the arrival before it. Robots (GSENROB) act only at GLOW_POWER 2 with WEAPONS_ARMED 0: primary
# power is switched on at Zax's Power Terminus, where no robot sees, then Zax turns them off (Science -25).
PID_YELLOW_KEY, PID_RED_KEY, PID_BLUE_KEY = 223, 96, 97
PID_FEV_DISK, PID_ALPHA_DISK, PID_DELTA_DISK = 190, 192, 193
PID_COMBAT_ARMOR = 17
STAT_RAD_RESIST = 31
YELLOW_DOOR, YELLOW_CAB = 22326, 22126  # GLOW1 e0..e2 (e2's door is a plain METLDOOR)
RED_DOOR, RED_CAB = 13886, 13686  # GLOW1 e2, GLOW2 e0, e2
BLUE_DOOR, BLUE_CAB = 13930, 13730  # GLOW2 e0..e2
GLOW1_CHEMS = (21305, 18324)  # e0: the Technician (Rad-X, RadAway), a Guard (RadAway)
RED_KEY_BODY = 14486  # GLOW1 e1: a dead Male Guard
LEVEL4_LOCKERS = (12920, 23108)  # GLOW2 e0: Tool, Rad-X, RadAway, Dean's; Tool, Big Book of Science, Dean's
BLUE_KEY_BODY = 22926  # GLOW2 e0
GENERATOR = 19685  # GLOW2 e2, GLOWGEN: a Tool on it, Repair +10; a failure costs 20 game minutes
# GLOW2 e1: Alpha disk, RadAway, Combat Armor, Delta disk, Plasma Rifle, Stealth Boy, FEV disk
LEVEL5_LOCKERS = (18482, 18882, 20889, 20882, 22689, 22882, 18126)
HOT_SPOTS = {  # HOTSPOT spatials: 20-50 rads for every step inside
    ("GLOW1", 0): ((20909, 2), (20316, 2), (16108, 3), (16719, 2)),
    ("GLOW1", 1): ((17105, 3), (20707, 3), (19119, 2)),
    ("GLOW1", 2): ((20107, 4),),
}
ZAX_TRIES = 4  # deactivation tries (10 game minutes each, +5 h a failure): inside one Rad-X window
ZAX_RECORDS = (
    ("research division employees records", "WATER_CHIP_3"),
    ("power armor. status", "WATER_CHIP_2"),
    ("fev (force evolutionary virus)", "WATER_CHIP_1"),
)
DISKS = (
    (PID_FEV_DISK, "FEV_DISK"),
    (PID_ALPHA_DISK, "ALPHA_DISK"),
    (PID_DELTA_DISK, "DELTA_DISK"),
    (PID_BROTHERHOOD_TAPE, "ARTIFACT_DISK"),
)


def rad_resist(actor: Actor) -> int:
    from f1 import chargen

    base = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS)
    bonus = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS + 35 * 4)
    return base[STAT_RAD_RESIST] + bonus[STAT_RAD_RESIST]


def rad_x(actor: Actor) -> bool:
    """Rad-X until the resistance reads 100 (two pills), logged with the rads: the plan's claim measured."""
    before = rad_resist(actor)
    for _ in range(2):
        if rad_resist(actor) >= 100 or not actor._count(PID_RAD_X):
            break
        actor.use_on_self(PID_RAD_X)
    left = actor._count(PID_RAD_X)
    actor.log.emit("rad_x", resist_before=before, resist=rad_resist(actor), rads=radiation(actor), left=left)
    return rad_resist(actor) >= 100 or not left


def hot(actor: Actor) -> frozenset[int]:
    s = actor.snap()
    return zone(HOT_SPOTS.get((s.map_name[:5], s.elevation), ()))


def glow_go(actor: Actor, goals: set[int]) -> bool:
    """To `goals` round this floor's hot spots (straight on when that finds no way). A floor trap's passed Traps
    roll stops the walk (TRAPFLOR), so it is issued again."""
    avoid = hot(actor) - goals
    for _ in range(4):
        if actor.snap().dude.tile in goals:
            return True
        ok, why = nav.go_to(actor, goals, max_steps=120, fight=True, avoid=avoid)
        actor.log.emit("glow_walk", ok=ok, why=why, tile=actor.snap().dude.tile)
        if ok:
            return True
        if why == "no path":
            avoid = frozenset()
    return actor.snap().dude.tile in goals


GLOW_ROOM = 45  # lb kept free for the Glow's finds: Combat Armor 20, the Plasma Rifle 12, cells, disks, keys


def glow_loot(actor: Actor, tile: int) -> bool:
    if loot.spare_weight(actor) < GLOW_ROOM:
        loot.lighten(actor, GLOW_ROOM)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for r in (1, 2) for t in geometry.ring(tile, r) if t not in obs.blocked})
    return actor.loot(tile).ok


def loot_all(tiles: tuple[int, ...]) -> Callable[[Actor], bool]:
    def run(actor: Actor) -> bool:
        for tile in tiles:
            glow_loot(actor, tile)
        return True

    return run


def key_in_door(actor: Actor, key: int, tile: int) -> bool:
    """The key used on the lift's door: its field off ("Authorization granted"), as the door script says."""
    s = actor.snap()
    door = next(
        (t for t in world.things(actor.mem) if t.tile == tile and t.elevation == s.elevation and "Door" in t.name),
        None,
    )
    if door is None or not actor._count(key):
        actor.log.emit("glow_door", tile=tile, why="no door" if door is None else "no key")
        return False
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for t in geometry.ring(tile, 1) if t not in obs.blocked})
    line = actor.mem.glob("disp_start")

    def granted() -> bool:
        return any("authorization granted" in m.lower() for m in state.messages_since(actor.mem, line))

    # the key stays in the right hand: the next use of it skips the inventory, and a fight's first attack swaps to
    # the gun by B (seen: the weapon came off and on all the time; an equip and an unequip cost
    # about 6 s a door in the chain, five doors a Glow run)
    out = actor.use_item_on(key, tile, granted, targets=frozenset({door.address}))
    actor.log.emit("glow_door", tile=tile, granted=granted(), how=out.reason)
    return granted()


def lift(key: int, door: int, cab: int, button: str, arrived) -> Callable[[Actor], bool]:
    def ride(actor: Actor) -> bool:
        if arrived(actor):
            return True
        if not key_in_door(actor, key, door):
            return False
        quests.ride_from(actor, cab, button, avoid=hot(actor))
        return arrived(actor)

    return ride


def at(name: str, elevation: int) -> Callable[[Actor], bool]:
    return lambda a: on_map(a, name) and a.snap().elevation == elevation


def zax_next(actor: Actor, goal: str, d, tries: int, most: int) -> tuple[int | None, int]:
    """The option to take at this node of Zax's talk for `goal`, and the deactivation tries so far."""
    reply = (d.reply or "").lower()

    def pick(*phrases: str) -> int | None:
        return next((i for i in (dialogue.match(d.options, p) for p in phrases) if i is not None), None)

    pending = [opt for opt, g in ZAX_RECORDS if not quests.gvar(actor, g)]
    power = quests.gvar(actor, "GLOW_POWER") == 2
    armed = bool(quests.gvar(actor, "WEAPONS_ARMED"))
    robots_left = goal == "robots" and power and not armed and tries < most
    if "how may i be of assistance" in reply:
        done = (
            (goal == "downloads" and not pending)
            or (goal == "power" and power)
            or (goal == "robots" and not robots_left)
        )
        return pick("uh, never mind.") if done else pick("grant me access to the mainframe."), tries
    if "select option" in reply:
        if goal == "downloads" and pending:
            return pick("research information"), tries
        if goal == "power" and not power:
            return pick("power terminus"), tries
        if robots_left:
            return pick("security information"), tries
        return pick("exit mainframe"), tries
    if reply.startswith("research:"):
        return (pick(pending[0]) if pending else pick("terminate program")), tries
    if reply.startswith("security:"):
        i = pick("deactivate security robots") if robots_left else None
        return (i, tries + 1) if i is not None else (pick("terminate program"), tries)
    if goal == "power" and not power:
        return pick("power management", "primary power", "reinitialize primary power", "main menu"), tries
    return pick("download records to pipboy", "main menu", "terminate program", "exit mainframe"), tries


def zax(actor: Actor, goal: str, most: int = ZAX_TRIES) -> None:
    """One talk with Zax (the "Terminal" ZAX on GLOW2 e0), steered node by node: its menus come back to themselves
    (a download returns to Research), so a phrase list would loop. `goal`: "downloads", "power" or "robots".
    ZAX.MSG / GPWRTERM.MSG (list 828); never 105/111/112 (ZaxClearance arms the robots again), never chess."""
    mem = actor.mem
    quests.use_scenery(actor, ("zax",), lambda: dialogue.read(mem).active, "Zax")
    tries = 0
    for _ in range(40):
        d = dialogue.read(mem)
        if not d.active:
            break
        if not d.options:
            time.sleep(0.3)
            continue
        i, tries = zax_next(actor, goal, d, tries, most)
        if i is None and len(d.options) == 1:
            i = 0
        if i is None:
            actor.log.emit("dialogue_stop", speaker=d.speaker, reply=d.reply, options=d.options)
            break
        actor.log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i])
        dialogue.choose(mem, i)
        time.sleep(0.2)
    settle(actor)
    actor.log.emit(
        "zax",
        goal=goal,
        tries=tries,
        glow_power=quests.gvar(actor, "GLOW_POWER"),
        weapons_armed=quests.gvar(actor, "WEAPONS_ARMED"),
        downloads=[quests.gvar(actor, g) for _, g in ZAX_RECORDS],
    )


def zax_downloads(actor: Actor) -> bool:
    if all(quests.gvar(actor, g) for _, g in ZAX_RECORDS):
        return True
    zax(actor, "downloads")
    return all(quests.gvar(actor, g) for _, g in ZAX_RECORDS)


def zax_power(actor: Actor) -> bool:
    if quests.gvar(actor, "GLOW_POWER") == 2:
        return True
    zax(actor, "power")
    return quests.gvar(actor, "GLOW_POWER") == 2


def zax_robots(actor: Actor) -> bool:
    """The robots off (Science -25; each failure 5 game hours), at most ZAX_TRIES times; Level 5 waits on it."""
    if quests.gvar(actor, "WEAPONS_ARMED") or quests.gvar(actor, "GLOW_POWER") != 2:
        return True
    zax(actor, "robots")
    return bool(quests.gvar(actor, "WEAPONS_ARMED"))


def the_generator(actor: Actor) -> bool:
    """A Tool on the generator (Repair +10); a failure costs 20 game minutes and is tried again (the game's own
    retry); "You lack the knowledge" ends it."""
    if quests.gvar(actor, "START_POWER") == 2:
        return True
    if not actor._count(PID_TOOL):
        return False
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for t in geometry.ring(GENERATOR, 1) if t not in obs.blocked})
    xp0 = experience(actor)
    for _ in range(6):
        line = actor.mem.glob("disp_start")

        def said(*words: str, line: int = line) -> bool:
            return any(w in m.lower() for m in state.messages_since(actor.mem, line) for w in words)

        actor.use_item_on(
            PID_TOOL, GENERATOR, lambda: quests.gvar(actor, "START_POWER") == 2 or said("fail", "knowledge")
        )
        actor.log.emit(
            "generator", start_power=quests.gvar(actor, "START_POWER"), msgs=state.messages_since(actor.mem, line)
        )
        if quests.gvar(actor, "START_POWER") == 2 or said("knowledge"):
            break
    return quests.gvar(actor, "START_POWER") == 2 and gained(actor, xp0, 1000)  # the Tool stays in hand (key_in_door)


def read_disks(actor: Actor) -> bool:
    """The holodisks used (each sets its GVAR, +100 XP, an archive entry; the disk stays)."""
    for pid, gvar in DISKS:
        if actor._count(pid) and not quests.gvar(actor, gvar):
            actor.use_on_self(pid)
            win32.wait_for(lambda g=gvar: quests.gvar(actor, g) != 0, 5, 0.25)
    actor.log.emit("disks", **{g: quests.gvar(actor, g) for _, g in DISKS})
    return True


def glow_gear(actor: Actor) -> bool:
    """The Combat Armor on, the best weapon ready (the Plasma Rifle at Energy Weapons 130)."""
    if actor._count(PID_COMBAT_ARMOR):
        actor.equip(PID_COMBAT_ARMOR, "armor")
    actor.ready_weapon()
    actor.holster(True)
    return True


def rads_down(actor: Actor) -> bool:
    """RadAway while the rads are 200 or more (the plan: a midnight at 600+ kills a CH 3 character)."""
    for _ in range(4):
        if radiation(actor) < 200 or not actor._count(PID_RADAWAY):
            break
        actor.use_on_self(PID_RADAWAY)
    actor.log.emit("rads", rads=radiation(actor), radaway=actor._count(PID_RADAWAY))
    return True


def key_and_tape(actor: Actor) -> bool:
    if not actor._count(PID_BROTHERHOOD_TAPE) or not actor._count(PID_YELLOW_KEY):
        the_tape(actor)
        if not actor._count(PID_YELLOW_KEY):
            glow_loot(actor, GLOW_BODY)
    return actor._count(PID_BROTHERHOOD_TAPE) > 0 and actor._count(PID_YELLOW_KEY) > 0


def take_key(pid: int, body: int) -> Callable[[Actor], bool]:
    return lambda a: a._count(pid) > 0 or (glow_loot(a, body) and a._count(pid) > 0)


def to_level_1(actor: Actor) -> bool:
    """Down by the yellow lift from Level 3: its e2 door is a plain metal door (no key needed)."""
    if at("GLOW1", 0)(actor):
        return True
    quests.ride_from(actor, YELLOW_CAB, "1", avoid=hot(actor))
    return at("GLOW1", 0)(actor)


def to_level_5(actor: Actor) -> bool:
    if not quests.gvar(actor, "WEAPONS_ARMED"):
        return True  # the robots stayed on: Level 5 is left out
    return lift(PID_BLUE_KEY, BLUE_DOOR, BLUE_CAB, "5", at("GLOW2", 1))(actor)


def level_5(actor: Actor) -> bool:
    return not at("GLOW2", 1)(actor) or loot_all(LEVEL5_LOCKERS)(actor)


def back_to_4(actor: Actor) -> bool:
    return not at("GLOW2", 1)(actor) or lift(PID_BLUE_KEY, BLUE_DOOR, BLUE_CAB, "4", at("GLOW2", 0))(actor)


IDEALIST_GLOW = [
    Step("to the Glow", lambda a: on_map(a, "GLOW") or travel(a, "the glow", 0).startswith("GLOW"), tries=6),
    Step("Rad-X", rad_x, checkpoint=True),
    Step("room for the finds", lambda a: loot.lighten(a, GLOW_ROOM)["spare"] >= GLOW_ROOM),
    Step("the rope on the beam", rope_on_the_beam),
    Step(
        "down the beam",
        lambda a: (
            on_map(a, "GLOW1")
            or quests.use_scenery(a, ("gent2lv1", "Beam"), lambda: on_map(a, "GLOW1"), "the beam", aims=GLOW_BEAM_AIMS)
        ),
        checkpoint=True,
    ),
    Step("the Brotherhood Tape and the Yellow key", key_and_tape, checkpoint=True, tries=3),
    Step("Level 1's chems", loot_all(GLOW1_CHEMS), optional=True),
    Step("yellow lift to Level 2", lift(PID_YELLOW_KEY, YELLOW_DOOR, YELLOW_CAB, "2", at("GLOW1", 1)), checkpoint=True),
    Step("the Red key", take_key(PID_RED_KEY, RED_KEY_BODY), checkpoint=True, tries=3),
    Step("yellow lift to Level 3", lift(PID_YELLOW_KEY, YELLOW_DOOR, YELLOW_CAB, "3", at("GLOW1", 2)), checkpoint=True),
    Step("red lift to Level 4", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "4", at("GLOW2", 0)), checkpoint=True),
    Step("Level 4's lockers", loot_all(LEVEL4_LOCKERS), checkpoint=True),
    Step("Zax: the downloads", zax_downloads, checkpoint=True, tries=3),
    Step("the Blue key", take_key(PID_BLUE_KEY, BLUE_KEY_BODY), checkpoint=True, tries=3),
    Step("the Blue key in its door", lambda a: key_in_door(a, PID_BLUE_KEY, BLUE_DOOR), tries=2),
    Step("red lift to Level 6", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "6", at("GLOW2", 2)), checkpoint=True),
    Step("the generator", the_generator, checkpoint=True, tries=2),
    Step("red lift to Level 4 again", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "4", at("GLOW2", 0)), checkpoint=True),
    Step("Zax: primary power", zax_power, checkpoint=True, tries=2),
    Step("Zax: the robots off", zax_robots, checkpoint=True, tries=1, optional=True),
    Step("blue lift to Level 5", to_level_5, checkpoint=True),
    Step("Level 5's lockers", level_5, checkpoint=True),
    Step("the disks read", read_disks),
    Step("the armor and the rifle", glow_gear),
    Step("blue lift to Level 4", back_to_4, checkpoint=True),
    Step("the rads", rads_down),
    Step("red lift to Level 3", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "3", at("GLOW1", 2)), checkpoint=True),
    Step("down to Level 1", to_level_1, checkpoint=True, tries=3),
    Step("up the rubble", up_the_rubble, checkpoint=True),
    Step("out of the Glow", lambda a: a.snap().screen == "worldmap" or quests.leave_map(a)),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("the tape read", read_disks),
    Step("Cabbot: the tape", hand_in_the_tape, checkpoint=True),
    Step("the rads after the road", rads_down),
]


# Inside the Brotherhood's bunker, initiated (planned from the scripts).
# Talus refuses anyone with a weapon in a hand slot (TALUS weapon_check): both hands emptied first. Michael hands out
# what Talus's requisition grants, one item a pass. Kyle's armor job: his talk, Michael's motivator (offered once, on a
# second talk: Speech -10, sure at 121), the install, then a Tool on the parts (Repair +10): a Powered Armor on the
# floor. Vree: the disk, Rad-X and her terminal lesson (+15 Science) in one talk. Sophia's and Maxson's histories.
# Never: Maxson's money lines (thrown out), the supply room (SUPGRD shoot on sight), Steal, Rhombus's quarters.
TALUS_REQUISITION = [
    "how do i get better weapons and equipment?",
    "how can i get some better equipment and weapons?",
    "thanks, what about some high-tech weapons?",
    "i really need some better firepower.",
    "sure. what's the problem?",
    "i'll check it out.",
    "okay, bye.",
]  # TALUS.MSG 104/134, 118, 109, 176, 182, 193
# TALUS.MSG 205, 213: the Laser Pistol (a second Powered Armor, 85 lb, adds nothing worn; the hub2 plan)
TALUS_REWARD = ["you're welcome.", "laser pistol."]
MICHAEL_PICKUP = [
    "i have been given authorization",
    "i have something to pick up.",
    "just give me what i have to pick up",
    "yes, i have something else to pick up.",
    "shotgun shells.",  # no energy cells on his list: the dearest box, for trade (225 caps each)
    ".223 full metal jacket.",  # shells were not on his list live: the next dearest (200)
    "no, that's it. thanks.",
]  # MICHAEL.MSG 146, 185, goto48, goto49 passes, 221
MICHAEL_MOTIVATOR = [
    "i'm looking for a systolic motivator.",
    "yes. i was sent up here to get a systolic motivator.",
    "you don't? ordinance was suppose to send one down yesterday.",
    "you know michael, you're probably one of the brightest guys",
    "thank you. bye.",
]  # MICHAEL.MSG 240, 242, 250 (Speech -10 for a man), 254, 252
KYLE_PART = [
    "well, who are you?",
    "what do you do here?",
    'what kind of "stuff"?',
    "how can i get my hands on some power armor?",
    "where could someone get one of those?",
    "so if i brought you a systolic motivator",
    "that's fair. i'll be back.",
]  # KYLE.MSG 104, 139, 115, 118, 120, 124, 130: CALM_REBELS_7 1
KYLE_INSTALL = ["yes. here it is.", "[more]"]  # KYLE.MSG 164, 300: CALM_REBELS_7 2, Deans Electronics
VREE_ALL = [
    "i need some technical information.",
    "what's causing all the mutations?",
    "why do you say that?",
    "interesting theory. any proof?",
    "can i ask something else?",
    "how can i prevent radiation poisoning?",
    "can i ask you something else?",
    "i heard something about holo-discs",
    "where can i learn the computer skills?",
]  # VREE.MSG 112, 123, 140, 145 (the disk), 149, 122 (Rad-X), 137, 121, 129 (TERM1)
SOPHIA_HISTORY = [
    "i am looking for vree.",
    "what is it you do as vree's assistant?",
    "what can you tell me about the brotherhood's history?",
    "could you tell me about the brotherhood's history.",
    "i was sent here to ask vree about the history",
    "well, then who can i talk to about the history",
    "thank you.",
]  # SOPHIA.MSG 304, 316, 332, 366, 330, 360, 349: Brotherhood History (pid 215)
MAXSON_HISTORY = [
    "can i ask you another question?",
    "i need to ask you some questions.",
    "can i ask you some questions?",
    "what can you tell me about the brotherhood's history?",
    "nevermind.",
]  # MAXSON.MSG 349/332/335, 344, 348: Maxson's History (pid 216)
PID_BOS_ARMOR, PID_POWER_ARMOR, PID_MOTIVATOR = 239, 3, 229
PID_VREES_DISK, PID_SOPHIAS_DISK, PID_MAXSONS_DISK = 194, 215, 216
PID_BIG_BOOK = 73
ARMOR_PARTS, NEW_POWER_ARMOR = 23277, 22475  # BROHD34 e0: ARMOR (Repair; a Tool +10), the armor KYLE drops
TERM1 = 20104  # BROHD34 e0: open once Vree has used it (vree22's timer)
BOS_DISKS = ((PID_VREES_DISK, "VREE_DISK"), (PID_SOPHIAS_DISK, "SOPHIA_DISK"), (PID_MAXSONS_DISK, "MAXSON_DISK"))


def bos_floor(name: str, elevation: int | None = None) -> Callable[[Actor], bool]:
    """To a floor of the bunker by its lifts (elev0/elev1: BROHD12 e0 '1', e1 '2', BROHD34 e0 '3', e1 '4')."""
    key = {("BROHD12", 0): "1", ("BROHD12", 1): "2", ("BROHD34", 0): "3", ("BROHD34", 1): "4"}[(name, elevation or 0)]

    def go(actor: Actor) -> bool:
        def there() -> bool:
            return on_map(actor, name) and actor.snap().elevation == (elevation or 0)

        for _ in range(3):
            if there():
                return True
            s = actor.snap()
            if on_map(actor, "BROHDENT"):
                quests.ride_from(actor, BOS_ENTRY_LIFT, "1")
            elif on_map(actor, "BROHD34"):
                quests.ride_from(actor, BOS_UP_LIFT_34, key)
            elif s.elevation == 1:
                quests.ride_from(actor, BOS_MAIN_LIFT_UPPER, key)
            else:
                quests.ride_from(actor, BOS_MAIN_LIFT, key)
        return there()

    return go


def hands_empty(actor: Actor) -> bool:
    from f1.actions import OBJECT_IN_LEFT_HAND, OBJECT_IN_RIGHT_HAND

    for hand, flag in (("left", OBJECT_IN_LEFT_HAND), ("right", OBJECT_IN_RIGHT_HAND)):
        held = next((p for i, p, _ in actor.items() if actor.mem.u32(i + Obj.FLAGS) & flag), None)
        if held is not None:
            actor.unequip(hand)
    return not any(
        actor.mem.u32(i + Obj.FLAGS) & (OBJECT_IN_LEFT_HAND | OBJECT_IN_RIGHT_HAND) for i, _, _ in actor.items()
    )


def talus(actor: Actor) -> bool:
    """The requisition (and the initiate's quest) in one talk; his reward first when the initiate is already free."""
    xp0 = experience(actor)
    if quests.gvar(actor, "FIND_LOST_INITIATE") == 2:
        quests.talk(actor, "talus", TALUS_REWARD, strict=True)
        settle(actor)
    quests.talk(actor, "talus", TALUS_REQUISITION, strict=True)
    settle(actor)
    actor.log.emit("talus", find=quests.gvar(actor, "FIND_LOST_INITIATE"), xp=experience(actor) - xp0)
    return True


def michael(actor: Actor) -> bool:
    """Everything owed handed over (one item a pass, the plan asks again while he has more)."""
    before = len(actor.items())
    for _ in range(2):
        quests.talk(actor, "michael", MICHAEL_PICKUP, strict=True)
        settle(actor)
    actor.log.emit("michael", armor=actor._count(PID_BOS_ARMOR), power=actor._count(PID_POWER_ARMOR))
    return actor._count(PID_BOS_ARMOR) > 0 or len(actor.items()) > before


def rearm(actor: Actor) -> bool:
    """The best armour carried worn (Power, Brotherhood, Combat), the best weapon ready, holstered."""
    for pid in (PID_POWER_ARMOR, PID_BOS_ARMOR, PID_COMBAT_ARMOR):
        if actor._count(pid):
            actor.equip(pid, "armor")
            break
    actor.ready_weapon()
    actor.holster(True)
    return True


def read_books(actor: Actor) -> bool:
    """The Big Book of Science and the Dean's Electronics carried (1 game hour each at IN 10; points while low)."""
    for pid in (PID_BIG_BOOK, PID_DEANS_ELECTRONICS):
        for _ in range(3):
            if not actor._count(pid):
                break
            read_book(actor, pid)
    actor.log.emit("books", science=actor.skills().get("science"), repair=actor.skills().get("repair"))
    return True


def use_disks(actor: Actor) -> bool:
    for pid, gvar in BOS_DISKS:
        if actor._count(pid) and not quests.gvar(actor, gvar):
            actor.use_on_self(pid)
            win32.wait_for(lambda g=gvar: quests.gvar(actor, g) != 0, 5, 0.25)
    actor.log.emit("disks", **{g: quests.gvar(actor, g) for _, g in BOS_DISKS})
    return True


def sophia(actor: Actor) -> bool:
    if not actor._count(PID_SOPHIAS_DISK) and not quests.gvar(actor, "SOPHIA_DISK"):
        quests.talk(actor, "sophia", SOPHIA_HISTORY, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "SOPHIA_DISK"))


def vree(actor: Actor) -> bool:
    if (
        not quests.gvar(actor, "DESTROY_MASTER_6")
        or not actor._count(PID_VREES_DISK)
        and not quests.gvar(actor, "VREE_DISK")
    ):
        quests.talk(actor, "vree", VREE_ALL, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "DESTROY_MASTER_6")) and bool(quests.gvar(actor, "VREE_DISK"))


def kyle_part(actor: Actor) -> bool:
    if quests.gvar(actor, "CALM_REBELS_7") >= 1:
        return True
    quests.talk(actor, "kyle", KYLE_PART, strict=True)
    settle(actor)
    return quests.gvar(actor, "CALM_REBELS_7") >= 1


def the_motivator(actor: Actor) -> bool:
    if actor._count(PID_MOTIVATOR) or quests.gvar(actor, "CALM_REBELS_7") >= 2:
        return True
    quests.talk(actor, "michael", MICHAEL_MOTIVATOR, strict=True)
    settle(actor)
    return actor._count(PID_MOTIVATOR) > 0


def kyle_install(actor: Actor) -> bool:
    if quests.gvar(actor, "CALM_REBELS_7") >= 2:
        return True
    quests.talk(actor, "kyle", KYLE_INSTALL, strict=True)
    settle(actor)
    return quests.gvar(actor, "CALM_REBELS_7") >= 2


def the_repair(actor: Actor) -> bool:
    """The Repair skill on the armour's parts; a plain failure is tried again while Repair is 75 or more."""
    if quests.gvar(actor, "CALM_REBELS_7") >= 4:
        return True
    xp0 = experience(actor)
    for _ in range(5):
        if quests.gvar(actor, "CALM_REBELS_7") >= 4:
            break
        line = actor.mem.glob("disp_start")

        def tried(line: int = line) -> bool:  # a hover's "You see: ..." is not an answer
            said = [m for m in state.messages_since(actor.mem, line) if not m.startswith("You see")]
            return quests.gvar(actor, "CALM_REBELS_7") >= 3 or bool(said)

        beside(actor, ARMOR_PARTS)
        s = actor.snap()
        parts = next(
            (t.address for t in world.things(actor.mem) if t.tile == ARMOR_PARTS and t.elevation == s.elevation
             and scripts.script_of(actor.mem, t.address) == "armor"),
            None,
        )  # fmt: skip
        on = frozenset({parts}) if parts else frozenset()  # the parts lie on a Table on the same hex
        # the Repair skill, not the Tool: the Tool used on the parts answered "That does nothing." ten times, the
        # skill repaired them at once (Repair 81, live)
        actor.use_skill_on("repair", ARMOR_PARTS, tried, targets=on)
        actor.log.emit(
            "repair", calm_rebels_7=quests.gvar(actor, "CALM_REBELS_7"), msgs=state.messages_since(actor.mem, line)
        )
        time.sleep(0.5)
    return quests.gvar(actor, "CALM_REBELS_7") >= 4 and gained(actor, xp0, 500)


def the_new_armor(actor: Actor) -> bool:
    """The Powered Armor Kyle left on the floor, put on (ST +3: carry 250, live). When lighten() has
    nothing more to let go, the heaviest single items are set down for a moment and taken back once it is worn."""
    aside: list[int] = []
    spot = actor.snap().dude.tile
    if not actor._count(PID_POWER_ARMOR):
        need = loot.weight(PID_POWER_ARMOR)  # 85 lb: the worn armour comes off first so it may go too
        if loot.spare_weight(actor) < need + loot.WEIGHT_SLACK:
            actor.unequip("armor")
            loot.lighten(actor, need, coming=knowledge.item_cost(PID_POWER_ARMOR))
        spot = actor.snap().dude.tile
        aside = set_aside(actor, need + loot.WEIGHT_SLACK)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(
            actor, {t for t in geometry.ring(NEW_POWER_ARMOR, 1) if t not in obs.blocked} or {NEW_POWER_ARMOR}, 60
        )
        actor.pick_up(NEW_POWER_ARMOR, PID_POWER_ARMOR)
    if actor._count(PID_POWER_ARMOR):
        actor.equip(PID_POWER_ARMOR, "armor")
    if aside and actor.snap().dude.tile == spot:  # off the pile: the Agent's own sprite would take the clicks
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(spot, 2) if t not in obs.blocked}, 20)
    back = [pid for pid in aside if actor.pick_up(spot, pid).ok]
    if aside:
        actor.log.emit("set_aside", tile=spot, items=[knowledge.proto_name(p) for p in aside], back=len(back))
    return actor._count(PID_POWER_ARMOR) > 0


def set_aside(actor: Actor, need: int) -> list[int]:
    """Single items (no stacks: a stack's pick-up from the floor is unmeasured), heaviest first, dropped where the
    Agent stands until `need` lb are spare: Kyle's Powered Armor was refused with lighten() done and 87 lb spare by
    the prototypes (the bunker). Their pids, to be taken back."""
    from f1.actions import ITEM_EQUIPPED

    items = [
        (loot.item_weight(actor.mem, item, pid), pid)
        for item, pid, qty in actor.items()
        if qty == 1 and not actor.mem.u32(item + Obj.FLAGS) & ITEM_EQUIPPED and pid != PID_POWER_ARMOR
    ]
    aside = []
    for w, pid in sorted(items, reverse=True):
        if loot.spare_weight(actor) >= need or w <= 0:
            break
        if loot.drop(actor, pid, 1):
            aside.append(pid)
    return aside


def maxson(actor: Actor) -> bool:
    if not actor._count(PID_MAXSONS_DISK) and not quests.gvar(actor, "MAXSON_DISK"):
        quests.talk(actor, "maxson", MAXSON_HISTORY, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "MAXSON_DISK"))


IDEALIST_BOS = [
    Step("into the bunker", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("the books read", read_books),
    Step("hands empty for Talus", hands_empty),
    Step("Talus: the requisition", talus, checkpoint=True),
    Step("Michael: the armor and the ammo", michael, checkpoint=True),
    Step("re-armed", rearm),
    Step("to the library", bos_floor("BROHD34"), checkpoint=True, tries=3),
    Step("Sophia: the history", sophia, checkpoint=True),
    Step("Vree: the disk, Rad-X, the lesson", vree, checkpoint=True),
    # Vree's terminal (+15 Science) is left out: her line 129 shows once, and her use of TERM1 after it did not
    # happen live ("denied" in 9 tries, the line gone); Science stood at 110 after the books anyway
    Step("Kyle: the armor job", kyle_part, checkpoint=True),
    Step("up to Michael", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("Michael: the motivator", the_motivator, checkpoint=True),
    Step("down to Kyle", bos_floor("BROHD34"), checkpoint=True, tries=3),
    Step("Kyle: the install", kyle_install, checkpoint=True),
    Step("the armor repaired", the_repair, checkpoint=True),
    # Kyle's Powered Armor weighs 85 lb: the worn armour off, lighten() (the old armour, junk), then picked up and
    # worn: ST 9, carry 250, AC 33 (live)
    Step("the Powered Armor on", the_new_armor, checkpoint=True),
    Step("to the Elders", bos_floor("BROHD34", 1), checkpoint=True, tries=3),
    Step("Maxson: the history", maxson, checkpoint=True),
    Step("re-armed again", rearm),
    Step("out of the bunker", out_of_the_bunker, checkpoint=True, tries=3),
]


# The Hub, second visit (planned from the scripts): the farm, Decker by the
# Sheriff's raid, the lost initiate's captors, the Deathclaw chain (Rutger, Butch, Beth, Harold, Slappy, the lair,
# Butch, Rutger), then Talus's reward. Every Hub critter is team 1 and a hit on a citizen sets ENEMY_HUB: each fight
# names its foes (only=). The Powered Armor (DT 12) stops every gun in the Hub but crits; Kane (four punches a turn)
# goes before Decker in the raid.
RUTGER_WORK = ["looking for work.", "about the job that was posted."]  # RUTGER.MSG 296/307, 295/306
BUTCH_JOB = [
    "i need to ask you a few questions.",
    "what job?",
    "so what do you want me to do?",
    "any clues on who's doing this?",
    "well, i do. what is it?",
    "how do i find out about the death claw?",
    "i'll go check it out.",
]  # BUTCH.MSG 107, 111, 134, 137, 144, 149, 152: map_var 41 1, HUB_FILLER_29 1
BETH_DEATHCLAW = [
    "can i ask you some questions?",
    "what do you know about the deathclaw?",
    "what exactly is a deathclaw?",
    "wow! do you know where it is?",
    "thanks. i'll check it out. bye.",
]  # HBETH.MSG 102, 110, 209, 244, 260: HUB_FILLER_29 2
BETH_DECKER = [
    "can i ask you some questions?",
    "what can you tell me about decker?",
    "yeah, he deserved it",
]  # 6 Stimpaks
HAROLD_DEATHCLAW = [
    "i need info on the deathclaw",
    "gonna kill it.",
    "any weaknesses?",
    "what kind of problem?",
    "great, thanks.",
    "could be.",
    "hi.",
]  # HAROLD.MSG 225, 231, 234, 236, 238, 240, 220: HUB_FILLER_29 3
SLAPPY_LAIR = [
    "uh, i talked with harold.",
    "come on, what do you know about the deathclaw?",
    "you can take me to the deathclaw?",
    "just shut up and take me there.",
]  # SLAPPY.MSG 109, 117, 126, 129: +800 XP, DETHCLAW
MUTANT = ["who are you?", "where did you come from?", "who sent you?"]  # DCMUTANT.MSG 105, 107, 109: MISSING_CARAVAN 2
BUTCH_REPORT = [
    "it was mutants.",
    "been there, saw that, killed it.",
    "i don't know yet. but they have an outpost in the mountains.",
]  # BUTCH.MSG 264, 222, 225: map_var 41 3
RUTGER_REWARD = [
    "i found out what happened to the missing caravans.",
    "actually, it was a group of these huge mutants.",
    "well, check this holodisk out.",
]  # RUTGER.MSG 241, 197, 214: 1000 XP, 800 caps, karma +5
KANE_TO_DECKER = [
    "i need to talk to decker.",
    "a job.",
    "loxley sent me.",
    "me? work for those thieves, never.",
    "i'm sorry, i misunderstood you.",
    "i'm sorry, i just wanted a job.",
]  # KANE.MSG 146, 215, 228, 240, 313 (Speech 0), 331
DECKER_REFUSE = ["i am ", "that's why i'm here.", "and the job?", "i can't do that!"]  # DECKER.MSG 104, 108, 111, 115
JUSTIN_REPORT = [
    "i have a crime to report!",
    "decker tried to hire me to kill some merchant",
    "yes, he did.",
    "it'll be a pleasure.",
    "i'm ready.",
]  # JUSTIN.MSG 161, 105, 171, 117, 124: +300 caps, the raid
JONATHAN = ["you're welcome. bye."]  # MISSBRO.MSG 106: FIND_LOST_INITIATE 2 as the talk opens
PID_MUTANT_DISK = 196
CAPTORS_DOOR, JONATHANS_DOOR = 19528, 20140  # HUBOLDTN e0: WOODDOOR (open), DOOR.INT (locked)
HUB2_STIMPAKS = 20  # carried before the captors and the Deathclaw (Vance: 105 caps each)


def kane_to_decker(actor: Actor) -> bool:
    """Kane takes the player to Decker; Decker's job refused (map_var 46 1). Kane's map_var 11/44 at 1 mean he
    attacks on the fourth talk: the Decker part is closed then."""
    if quests.mvar(actor, 46) >= 1 or quests.gvar(actor, "DECKER_STATUS"):
        return True
    if quests.mvar(actor, 11) or quests.mvar(actor, 44):
        actor.log.emit("decker", why="kane closed", mvar11=quests.mvar(actor, 11), mvar44=quests.mvar(actor, 44))
        return True
    actor.log.emit("decker", mvar45=quests.mvar(actor, 45))
    hands_empty(actor)
    quests.talk(actor, "kane", KANE_TO_DECKER, strict=True)
    win32.wait_for(lambda: actor.snap().elevation == 1 or dialogue.read(actor.mem).active, 15, 0.25)
    for _ in range(3):
        if quests.mvar(actor, 46) >= 1:
            break
        quests.talk(actor, "decker", DECKER_REFUSE, strict=True)
        settle(actor)
    win32.wait_for(lambda: actor.snap().elevation == 0, 15, 0.25)
    return quests.mvar(actor, 46) >= 1 and actor.snap().elevation == 0


def the_raid(actor: Actor) -> bool:
    """The Sheriff told, the raid on Decker's floor: Kane first, then Decker; the guards surrender."""
    if quests.gvar(actor, "DECKER_STATUS") == 1:
        return True
    if quests.mvar(actor, 46) < 1:
        return True  # Decker's part closed
    if actor.snap().elevation == 0:
        heal_below(actor, actor.max_hp - 5)
        hands_empty(actor)
        quests.talk(actor, "justin", JUSTIN_REPORT, strict=True)
        win32.wait_for(lambda: actor.snap().elevation == 1, 15, 0.25)
    actor.ready_weapon()
    for foes in (("Kane",), ("Decker",)):
        win32.wait_for(actor.in_combat, 10, 0.25)
        if actor.in_combat():
            actor.fight(only=foes)
    # the combat ended with Kane dead and Decker at 50 HP, standing back (live): combat mode by hand
    # ("a") and Decker alone, until he is down
    for _ in range(3):
        if quests.gvar(actor, "DECKER_STATUS") == 1 or actor.snap().elevation != 1:
            break
        if not any(
            scripts.script_of(actor.mem, c.address) == "decker" and not c.dead for c in state.critters(actor.mem)
        ):
            break
        if not actor.in_combat():
            session.press("a")
            win32.wait_for(actor.in_combat, 5, 0.2)
        actor.fight(only=("Decker",))
    actor.log.emit("raid", decker=quests.gvar(actor, "DECKER_STATUS"), enemy_hub=quests.gvar(actor, "ENEMY_HUB"))
    win32.wait_for(lambda: actor.snap().elevation == 0, 30, 0.5)  # the map update takes everyone back down
    actor.holster(True)
    return quests.gvar(actor, "DECKER_STATUS") == 1 and not quests.gvar(actor, "ENEMY_HUB")


def sheriffs_reward(actor: Actor) -> bool:
    if quests.gvar(actor, "DECKER_STATUS") != 1:
        return True
    xp0 = experience(actor)
    hands_empty(actor)
    who = "kenny" if quests.gvar(actor, "GREENE_DEAD") else "justin"
    quests.talk(actor, who, [], strict=True)
    settle(actor)
    return gained(actor, xp0, 1400) or quests.mvar(actor, 53) == 0


def stimpaks_to(n: int) -> Callable[[Actor], bool]:
    def buy(actor: Actor) -> bool:
        if actor._count(PID_STIMPAK) >= n:
            return True
        from_merchant("vance", "HUBOLDTN", PID_STIMPAK, n, VANCE_SELL)(actor)
        return True  # what the caps allowed

    return buy


def the_captors(actor: Actor) -> bool:
    """Their door opened from outside by day, the four fought in the doorway (they attack on sight)."""
    if quests.gvar(actor, "FIND_LOST_INITIATE") >= 2:
        return True
    left = [
        c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
    ]
    if not left:
        return True
    heal_below(actor, actor.max_hp - 5)
    actor.ready_weapon()
    foes = ("Rutger", "Guard", "Vinnie")
    for _ in range(6):
        if actor.in_combat():
            actor.fight(only=foes)
            continue
        left = [
            c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
        ]
        if not left:
            break
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        if CAPTORS_DOOR in obs.doors:
            beside(actor, CAPTORS_DOOR)
            nav.open_door(actor, CAPTORS_DOOR)
        else:
            nav.go_to(actor, {t for t in geometry.ring(left[0].tile, 2) if t not in obs.blocked}, max_steps=20)
        win32.wait_for(actor.in_combat, 6, 0.25)
    left = [
        c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
    ]
    actor.log.emit("captors", left=len(left), enemy_hub=quests.gvar(actor, "ENEMY_HUB"), hp=actor.snap().dude.hp)
    actor.holster(True)
    return not left and not quests.gvar(actor, "ENEMY_HUB")


def jonathan(actor: Actor) -> bool:
    """His room's door picked (DOOR.INT), then his talk: FIND_LOST_INITIATE 2 as it opens."""
    if quests.gvar(actor, "FIND_LOST_INITIATE") >= 2:
        return True
    door = door_at(actor, JONATHANS_DOOR)
    if door is not None and locked(actor, door):
        beside(actor, JONATHANS_DOOR)
        if not nav.open_door(actor, JONATHANS_DOOR):
            pick_lock(actor, JONATHANS_DOOR, door.address, tries=8)
            nav.open_door(actor, JONATHANS_DOOR)
    quests.talk(actor, "missbro", JONATHAN, strict=True)
    settle(actor)
    return quests.gvar(actor, "FIND_LOST_INITIATE") >= 2


def the_deathclaw(actor: Actor) -> bool:
    """The lair's Deathclaw (225 HP, three claws a turn): it comes on sight; met at full HP."""
    if not on_map(actor, "DETHCLAW"):
        return quests.gvar(actor, "HUB_FILLER_29") >= 5 or actor._count(PID_MUTANT_DISK) > 0
    heal_below(actor, actor.max_hp - 5)
    actor.ready_weapon()
    for _ in range(8):
        claw = quests.find_critter(actor, "dethclaw")
        if claw is None:
            break
        if actor.in_combat():
            actor.fight(only=("Deathclaw",))
            continue
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(claw.tile, 8) if t not in obs.blocked}, max_steps=12)
        win32.wait_for(actor.in_combat, 4, 0.25)
    claw = quests.find_critter(actor, "dethclaw")
    actor.log.emit("deathclaw", dead=claw is None, hp=actor.snap().dude.hp)
    return claw is None


def the_mutant(actor: Actor) -> bool:
    """The dying mutant's talk gives his disk (MISSING_CARAVAN 2); his body holds it if he died."""
    if not actor._count(PID_MUTANT_DISK):
        actor.holster(True)
        if quests.find_critter(actor, "dcmutant") is not None:
            quests.talk(actor, "dcmutant", MUTANT, strict=True)
            settle(actor)
        if not actor._count(PID_MUTANT_DISK):
            body = next(
                (
                    c
                    for c in state.critters(actor.mem)
                    if c.dead and knowledge.proto_name(c.pid) == "Super Mutant Guard"  # a dead one keeps no script
                ),
                None,
            )
            if body is not None:
                # over the limit the loot screen refuses each drag without a word (a full run: 258 of 250 lb after
                # the Deathclaw, "nothing taken" twice)
                if loot.spare_weight(actor) < loot.weight(PID_MUTANT_DISK):
                    loot.lighten(actor, loot.weight(PID_MUTANT_DISK))
                beside(actor, body.tile)
                actor.loot(body.tile)
    if actor._count(PID_MUTANT_DISK) and not quests.gvar(actor, "MUTANT_DISK"):
        actor.use_on_self(PID_MUTANT_DISK)
        win32.wait_for(lambda: quests.gvar(actor, "MUTANT_DISK") != 0, 5, 0.25)
    return actor._count(PID_MUTANT_DISK) > 0


def far_go(name: str, plan: list[str], done) -> Callable[[Actor], bool]:
    def talk(actor: Actor) -> bool:
        if done(actor):
            return True
        if not hub_map(actor, "HUBDWNTN"):
            return False
        hands_empty(actor)
        quests.talk(actor, name, plan, strict=True)
        settle(actor)
        return done(actor)

    return talk


def hub_talk(where: str, name: str, plan: list[str], done) -> Callable[[Actor], bool]:
    def talk(actor: Actor) -> bool:
        if done(actor):
            return True
        if not hub_map(actor, where):
            return False
        hands_empty(actor)
        quests.talk(actor, name, plan, strict=True)
        settle(actor)
        return done(actor)

    return talk


def hub2_ready(actor: Actor) -> bool:
    actor.ready_weapon()
    actor.holster(True)
    return True


IDEALIST_HUB2 = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 1)), checkpoint=True, tries=6),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step(
        "Rutger: work",
        far_go("rutger", RUTGER_WORK, lambda a: quests.mvar(a, 34) >= 1 or quests.mvar(a, 41) >= 1),
        checkpoint=True,
    ),
    Step("Butch: the job", far_go("butch", BUTCH_JOB, lambda a: quests.mvar(a, 41) >= 1), checkpoint=True),
    Step("Irwin: the farm", irwins_job, checkpoint=True),
    Step("the farm's raiders", the_farm, checkpoint=True, tries=3),
    Step("Irwin: paid", irwin_paid, checkpoint=True),
    Step("downtown again", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("Kane: to Decker", kane_to_decker, checkpoint=True, tries=2),
    Step("the Sheriff's raid", the_raid, checkpoint=True, tries=2),
    Step("the Sheriff's reward", sheriffs_reward, checkpoint=True),
    Step("by day for Beth", lambda a: hub_rest_until(a, 7, 15), checkpoint=True),
    Step(
        "Beth: the Deathclaw",
        far_go("hbeth", BETH_DEATHCLAW, lambda a: quests.gvar(a, "HUB_FILLER_29") >= 2),
        checkpoint=True,
    ),
    # Beth's six stimpaks once (Beth67); her later talks only say "I already told you" (live): one try, done either way
    Step("Beth: Decker's death", lambda a: far_go("hbeth", BETH_DECKER, lambda _a: False)(a) or True, tries=1),
    Step("Old Town", lambda a: hub_map(a, "HUBOLDTN"), checkpoint=True),
    Step("stimpaks from Vance", stimpaks_to(HUB2_STIMPAKS), optional=True),
    Step(
        "Harold: the Deathclaw",
        hub_talk("HUBOLDTN", "harold", HAROLD_DEATHCLAW, lambda a: quests.gvar(a, "HUB_FILLER_29") >= 3),
        checkpoint=True,
    ),
    Step("the captors", the_captors, checkpoint=True, tries=2),
    Step("Brother Jonathan", jonathan, checkpoint=True, tries=2),
    Step(
        "Slappy: to the lair",
        hub_talk(
            "HUBOLDTN", "slappy", SLAPPY_LAIR, lambda a: on_map(a, "DETHCLAW") or quests.gvar(a, "HUB_FILLER_29") >= 5
        ),
        checkpoint=True,
    ),
    Step("the Deathclaw", the_deathclaw, checkpoint=True, tries=2),
    Step("the dying mutant", the_mutant, checkpoint=True),
    Step("out of the lair", lambda a: not on_map(a, "DETHCLAW") or quests.leave_map(a)),
    Step(
        "back to Downtown", lambda a: on_map(a, "HUB") or in_town(a, travel(a, "the hub", 1)), checkpoint=True, tries=6
    ),
    Step("Butch: the report", far_go("butch", BUTCH_REPORT, lambda a: quests.mvar(a, 41) >= 3), checkpoint=True),
    Step("Rutger: the reward", far_go("rutger", RUTGER_REWARD, lambda a: quests.mvar(a, 41) >= 4), checkpoint=True),
    Step("ready", hub2_ready),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("into the bunker", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("hands empty for Talus", hands_empty),
    Step("Talus: the reward", talus, checkpoint=True),
    Step("Michael: the reward", michael, checkpoint=True, optional=True),
    Step("re-armed", hub2_ready),
    Step("out of the bunker", out_of_the_bunker, checkpoint=True, tries=3),
]

ROUTES = {
    "idealist_start": IDEALIST_START,
    "idealist_junktown": IDEALIST_JUNKTOWN,
    "idealist_hub": IDEALIST_HUB,
    "idealist_necropolis": IDEALIST_NECROPOLIS,
    "idealist_vault13": IDEALIST_VAULT13,
    "idealist_brotherhood": IDEALIST_BROTHERHOOD,
    "idealist_glow": IDEALIST_GLOW,
    "idealist_bos": IDEALIST_BOS,
    "idealist_hub2": IDEALIST_HUB2,
}
