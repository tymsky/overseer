"""Agent route tasks for necropolis."""

import time

from f1 import dialogue, geometry, nav, quests, scripts, state, win32
from f1.actions import PID_STIMPAK, Actor
from f1.route_support import (
    Step,
    elevation_becomes,
    heal_below,
    heal_up,
    on_map,
    travel,
)

PID_WATER_CHIP = 0x37

GVAR_FIND_WATER_CHIP = 101

CELL_MANHOLE = 18658  # the Watershed cell whose manhole leads to the isolated sewer part above Vault 12

WORKING_COMPUTER = 25270  # Vault 12, third floor: the only computer with the use flag

V12_DOORS = {0: 13504, 2: 17108}  # Vault 12's elevator doors by elevation (the cab floor is the door tile - 200)

HARRY_PEACE = ["#103", "#114", "#118", "#150", "#137"]

OVERSEER_CHIP = ["#151", "#157", "#158", "#160/162/188/237/118", "#164"]


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


TREAD_REACH = 7  # TREAD.INT: a Glowing One attacks one it sees within 6 hexes (its warning float)


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
    underground: the Pip-Boy refuses (PIPBOY.MSG 215) in Vault 12, in the sewer
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
