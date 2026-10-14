"""Idealist route tasks for necropolis."""

import time

from f1 import clock, dialogue, geometry, nav, perception, quests, watchdog, win32
from f1.actions import Actor
from f1.agent_necropolis import (
    CELL_MANHOLE,
    WATER_CHIP,
)
from f1.idealist_hub import (
    PID_JUNK,
)
from f1.idealist_support import (
    experience,
    gained,
    read_book,
    take_from,
)
from f1.route_support import (
    Step,
    heal_up,
    on_map,
    settle,
)

# Necropolis (planned from the scripts): the ghoul
# leader's three books (LEADER leader19a, Repair < 60), the Watershed's spare Junk, past Harry by sneaking (no Speech
# line is sure below 120: HARRY.MSG 103 is -20), the pump fixed with the Junk before the chip (Repair -5, plain
# failures retried; NH2OPUMP), then the chip by the Agent's steps, and out past Harry again.
LEADER_BOOKS = [
    "#102",
    "#112",
    "#118",
    "#120",
    "#126/175",
    "#129",
    "#134",
    "#138",
    "#145",
    "#166",
]

HARRY_GHOUL = ["#103", "#150"]  # HARRY.MSG 103 (Speech -20), 150 (taken, stands)

PID_DEANS_ELECTRONICS = 76

WATRSHD_SPARE_JUNK = 16658  # WATRSHD e0, sewer region C (the Cover and Sewer Hole at e1 17290, the ladder up at 15888)

WATRSHD_PUMP = 12257  # WATRSHD e1, NH2OPUMP: the Junk used on it, Repair -5
PUMP_TRIES = 30

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
    waits for a working one, at most SNEAK_WAIT_S game seconds, then goes anyway. If he talks: HARRY.MSG 103."""
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
    """The Junk used on the pump: Repair -5 (NH2OPUMP.INT). A plain failure keeps the parts (tried again: the game's
    own retry, up to PUMP_TRIES: on Hard Repair is about 40 here); a critical failure destroys them (the spare then,
    if spare_junk found it)."""
    if quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2:
        return True
    xp0 = experience(actor)

    def fixed() -> bool:
        return quests.gvar(actor, "NECROP_WATER_PUMP_FIXED") == 2

    for _ in range(PUMP_TRIES):
        if not actor._count(PID_JUNK):
            break
        # beside the pump round Harry's notice (a plain walk there crossed his talk hex 14093, live);
        # a talk he opens is answered by HARRY.MSG 103
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
    """Out of the Watershed by its exits, round Harry while sneaking; at Speech 121 (level 6) HARRY.MSG 103
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


IDEALIST_NECROPOLIS = [
    *WATER_CHIP[:4],  # out, to Necropolis, the Hotel's sewer hole, the sewers to the Hall of the Dead
    Step("the ghoul leader's books", leaders_books, checkpoint=True),
    Step("the books read", read_the_books),
    *WATER_CHIP[4:6],  # the sewers to the Watershed, up to its south
    Step("the spare Junk", spare_junk, checkpoint=True, optional=True, tries=2),
    Step("past Harry, sneaking", to_the_far_side, checkpoint=True),
    # its failure stands: a critical failure destroys the only Junk when the spare was not found (an ironman run: one
    # plain failure, then the parts destroyed; the spare's step had found nothing at its tile, in every run so far)
    Step("the pump fixed", the_pump, checkpoint=True, optional=True),
    Step("to the cell manhole", to_the_cell, checkpoint=True),
    *WATER_CHIP[7:15],  # down the manhole, Vault 12, the cab, the chip, back up to the cell
    Step("heal before the road", heal_up),
    Step("out past Harry", out_past_harry, tries=4),
]
