"""Where a critter notices the player, so that a walk can keep out of it. The engine's rules (as fallout1-ce documents
them: combatai.cc `is_within_perception`, actions.cc `can_see`, tile.cc `tile_dir`):

- A critter notices the player within PE x 5 hexes when the player stands in its front half. That is `can_see`: the
  direction to the player is its facing or one beside it, three of six.
- Elsewhere it notices within PE hexes, x 2 in combat.
- Either reach is a quarter while the player's sneak works.
- Walls do not enter `can_see` in 1.1.

A radscorpion (PE 2) notices at 10 hexes ahead, 2 behind, 2 while the player sneaks.

    python -m f1.perception      how many hexes of this floor the foes in sight watch
"""

import math
import sys

from f1 import geometry, knowledge, paths
from f1.geometry import Camera

FRAME = Camera(0, 0, 0, 0)  # any fixed frame: tile_dir needs only the difference of two tiles' screen points


def tile_dir(a: int, b: int) -> int:
    """The direction (0 NE, 1 E, 2 SE, 3 SW, 4 W, 5 NW) from tile a to tile b as the engine judges it: the bearing
    of b's screen point from a's, clockwise from straight up, in 60-degree sectors. The engine takes the angle in
    whole degrees (cut toward zero, counter-clockwise from the right); a point straight above is NE, straight below
    SE."""
    x1, y1 = geometry.tile_coord(a, FRAME)
    x2, y2 = geometry.tile_coord(b, FRAME)
    dx, dy = x2 - x1, y2 - y1
    if dx == 0:
        return 0 if dy < 0 else 2
    bearing = (90 - math.trunc(math.degrees(math.atan2(-dy, dx)))) % 360
    return min(bearing // 60, 5)


def sees(rotation: int, direction: int) -> bool:
    """actions.cc can_see: the direction is the facing, or one beside it."""
    return abs(rotation - direction) in (0, 1, 5)


def reach(pe: int, seen: bool, in_combat: bool = False, sneaking: bool = False) -> int:
    r = pe * 5 if seen else pe * (2 if in_combat else 1)
    return r // 4 if sneaking else r


def notices(foe_tile: int, rotation: int, pe: int, tile: int, in_combat: bool = False, sneaking: bool = False) -> bool:
    d = geometry.distance(foe_tile, tile)
    if d == 0:
        return True
    return d <= reach(pe, sees(rotation, tile_dir(foe_tile, tile)), in_combat, sneaking)


def perception_of(pid: int) -> int:
    return (knowledge.proto(pid) or {}).get("special", [5] * 7)[1]


def zone(foes, in_combat: bool = False, sneaking: bool = False) -> frozenset[int]:
    """Every hex where one of `foes` (live critters: tile, rotation, pid) would notice the player."""
    out: set[int] = set()
    for c in foes:
        pe = perception_of(c.pid)
        for d in range(reach(pe, True, in_combat, sneaking) + 1):
            for t in geometry.ring(c.tile, d) if d else [c.tile]:
                if notices(c.tile, c.rotation, pe, t, in_combat, sneaking):
                    out.add(t)
    return frozenset(out)


def main(argv: list[str]) -> int:
    from f1 import session
    from f1.actions import Actor
    from f1.telemetry import EventLog

    actor = Actor(session.game_pid(), EventLog(paths.RUNS / "perception" / "events.jsonl"))
    try:
        s = actor.snap()
        foes = actor.enemies()
        watched = zone(foes, actor.in_combat(), actor.sneaking())
        for c in foes:
            pe = perception_of(c.pid)
            print(
                f"{knowledge.proto_name(c.pid)} at {c.tile} facing {c.rotation}, PE {pe}: "
                f"{geometry.distance(c.tile, s.dude.tile)} hexes off, "
                f"notices the player now: {notices(c.tile, c.rotation, pe, s.dude.tile, actor.in_combat())}"
            )
        print(f"{len(watched)} hexes watched; the player's hex {'is' if s.dude.tile in watched else 'is not'} one")
    finally:
        actor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
