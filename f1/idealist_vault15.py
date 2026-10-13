"""Idealist route tasks for vault15."""

from f1 import geometry, loot, nav, quests, win32, world
from f1.actions import Actor
from f1.idealist_support import (
    DAY,
    beside,
    experience,
)
from f1.route_support import (
    Step,
    hour,
    on_map,
    travel,
)


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
