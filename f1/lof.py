"""Lines of fire as Fallout 1's engine walks them, over a map snapshot (f1/scene.py; no memory reading here).

Ported from fallout1-ce, each function read against the 1.1 exe: anim.cc make_straight_path_func 0x415E28 (the
straight line between two tiles' centres, walked pixel by pixel along the longer screen axis, every new tile handed
to obj_blocking_at, the walk stopping at the first object it returns), object.cc obj_blocking_at, combat.cc
combat_is_shot_blocked 0x4242B4 and check_ranged_miss 0x420ED4, tile.cc tile_num_beyond 0x49E680.

How 1.1 differs from Fallout 2 with sfall (f2's port): a shot's walk tests obj_blocking_at (there is no
obj_shoot_blocking_at), so a corpse or a knocked-down critter on the line stops it like a standing one (each is a
critter: the walk goes on from its tile); the critters met are all counted; a multihex obstacle is not stepped past.
A missed single shot (check_ranged_miss) walks the line from the shooter to the weapon's range through the target:
each critter on it other than the target, dead or alive, is hit on a roll of 1..100 at most the shooter's chance at it
(determine_to_hit_func with its last argument 1), and the first thing that is no critter ends the walk (hit, to no
effect on a wall). The exe's code, read at 0x420F66..0x420FBA: no third of the chance and no 5 % for the dead (CE has
both), and the stray hit lands when the object found is NOT shoot-through (CE has the test inverted).

The walk: an object found on the first tile counts unless it is the `obstacle` passed in (the caller's last one: the
shooter, or the critter the previous walk stopped at); with step 32 (shots) objects flagged shoot-through are passed
over; a walk that reaches the destination's centre without a find ends with no obstacle.
"""

from dataclasses import dataclass

from f1.geometry import Camera, tile_coord, tile_is_valid, tile_num, tile_num_in_direction, tile_on_edge
from f1.scene import CRITTER, DEAD, HIDDEN, MULTIHEX, NO_BLOCK, SCENERY, SHOOT_THRU, WALL, Obj, Snapshot

SHOT = 32  # the step count a shot's walk passes (shoot-through objects passed over)


def same(a: Obj | None, b: Obj | None) -> bool:
    return a is not None and b is not None and a.address == b.address


def _blocks(o: Obj, exclude: Obj | None, elevation: int) -> bool:
    return (
        o.elevation == elevation
        and not o.flags & (HIDDEN | NO_BLOCK)
        and not same(o, exclude)
        and o.kind in (CRITTER, SCENERY, WALL)
    )


def blocking_at(snap: Snapshot, exclude: Obj | None, tile: int, elevation: int) -> Obj | None:
    """obj_blocking_at: the first critter, scenery or wall on the tile (not hidden, not no-block, not EXCLUDE), then
    a multihex one on a neighbouring tile (rotations 0-5 in order)."""
    if not tile_is_valid(tile):
        return None
    for o in snap.by_tile.get(tile, []):
        if _blocks(o, exclude, elevation):
            return o
    for rotation in range(6):
        near = tile_num_in_direction(tile, rotation)
        if tile_is_valid(near):
            for o in snap.by_tile.get(near, []):
                if o.flags & MULTIHEX and _blocks(o, exclude, elevation):
                    return o
    return None


@dataclass(frozen=True)
class Path:
    obstacle: Obj | None  # what the walk stopped at (None: it reached the destination)
    tiles: tuple[int, ...]  # the first tile, then each change of tile the walk tests


def straight_path(snap: Snapshot, obj: Obj, from_tile: int, to_tile: int, obstacle: Obj | None = None,
                  step: int = SHOT, blocking=blocking_at, camera: Camera | None = None) -> Path:  # fmt: skip
    """make_straight_path_func with an obstacle pointer (its value on entry: OBSTACLE) and the blocking test
    BLOCKING(snap, obj, tile, elevation) on OBJ's elevation."""
    cam = camera or snap.camera
    elevation = obj.elevation
    tiles = [from_tile]

    def stops(found: Obj | None) -> bool:
        return found is not None and not same(found, obstacle) and (step != SHOT or not found.flags & SHOOT_THRU)

    first = blocking(snap, obj, from_tile, elevation)
    if stops(first):
        return Path(first, tuple(tiles))
    fx, fy = tile_coord(from_tile, cam)
    fx, fy = fx + 16, fy + 8
    tx, ty = tile_coord(to_tile, cam)
    tx, ty = tx + 16, ty + 8
    step_x = (tx > fx) - (tx < fx)
    step_y = (ty > fy) - (ty < fy)
    ddx, ddy = 2 * abs(tx - fx), 2 * abs(ty - fy)
    x, y, prev = fx, fy, from_tile
    y_major = ddx <= ddy
    middle = ddx - ddy // 2 if y_major else ddy - ddx // 2
    while True:
        tile = tile_num(x, y, cam)
        if (y == ty) if y_major else (x == tx):
            if tile != prev:
                tiles.append(tile)
            return Path(None, tuple(tiles))
        if y_major:
            if middle >= 0:
                x += step_x
                middle -= ddy
            y += step_y
            middle += ddx
        else:
            if middle >= 0:
                y += step_y
                middle -= ddx
            x += step_x
            middle += ddy
        if tile != prev:
            tiles.append(tile)
            o = blocking(snap, obj, tile, elevation)
            if stops(o):
                return Path(o, tuple(tiles))
            prev = tile


@dataclass(frozen=True)
class LineOfFire:
    blocked: bool
    critters: int  # critters met other than the target (combat_is_shot_blocked's count)
    obstacles: tuple[Obj, ...]  # what each walk stopped at, in order


def shot_blocked(snap: Snapshot, source: Obj, from_tile: int, to_tile: int, target: Obj | None) -> LineOfFire:
    """combat_is_shot_blocked: walk from FROM_TILE to TO_TILE; anything but a critter blocks ("Your aim is
    blocked."); each critter met counts and the walk goes on from its tile."""
    critters, obstacle, current, met = 0, source, from_tile, []
    for _ in range(1000):  # the engine has no bound; a walk always moves on, the bound only guards this port
        if obstacle is None or current == to_tile:
            break
        obstacle = straight_path(snap, source, current, to_tile, obstacle).obstacle
        if obstacle is None:
            break
        met.append(obstacle)
        if obstacle.kind != CRITTER:
            return LineOfFire(True, critters, tuple(met))
        if not same(obstacle, target):
            critters += 1
        current = obstacle.tile
    return LineOfFire(False, critters, tuple(met))


def tile_num_beyond(from_tile: int, to_tile: int, distance: int, cam: Camera) -> int:
    """tile_num_beyond: the tile DISTANCE new tiles along the straight screen line from FROM_TILE through TO_TILE
    (each change of tile counts; it stops early on the grid's edge)."""
    if distance <= 0 or from_tile == to_tile:
        return from_tile
    fx, fy = tile_coord(from_tile, cam)
    fx, fy = fx + 16, fy + 8
    tx, ty = tile_coord(to_tile, cam)
    tx, ty = tx + 16, ty + 8
    dx, dy = tx - fx, ty - fy
    ddx, ddy = 2 * abs(dx), 2 * abs(dy)
    step_x, step_y = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
    x, y, prev, count = fx, fy, from_tile, 0
    x_major = ddx > ddy
    middle = ddy - ddx // 2 if x_major else ddx - ddy // 2
    for _ in range(100_000):  # the engine has no bound: a line always reaches the grid's edge
        tile = tile_num(x, y, cam, ignore_bounds=True)
        if tile != prev:
            count += 1
            if count == distance or tile_on_edge(tile) or not tile_is_valid(tile):
                return tile
            prev = tile
        if x_major:
            if middle >= 0:
                middle -= ddx
                y += step_y
            middle += ddy
            x += step_x
        else:
            if middle >= 0:
                middle -= ddy
                x += step_x
            middle += ddx
            y += step_y
    return prev


@dataclass(frozen=True)
class Miss:
    critters: tuple[Obj, ...]  # each rolled for in turn at the shooter's chance at it
    wall: Obj | None  # the first thing on the line that is no critter: hit for sure if every roll before failed


def miss_line(snap: Snapshot, shooter: Obj, target: Obj, reach: int) -> Miss:
    """check_ranged_miss's walk: from the shooter to REACH tiles along the line through the target. The target is
    passed over; a critter met is rolled for; the walk goes on from its tile; the first scenery or wall ends it."""
    end = tile_num_beyond(shooter.tile, target.tile, reach, snap.camera)
    found, current, met = shooter, shooter.tile, []
    for _ in range(1000):
        if current == end:
            break
        found = straight_path(snap, shooter, current, end, found).obstacle
        if found is None:
            break
        if found.kind != CRITTER:
            return Miss(tuple(met), found)
        if not same(found, target):
            met.append(found)
        current = found.tile
    return Miss(tuple(met), None)


def in_miss_line(snap: Snapshot, shooter: Obj, target: Obj, reach: int, spared: set[int] | frozenset[int]) -> bool:
    """A critter whose address is in SPARED could be hit by a missed shot at TARGET (it is on the miss line before
    any wall)."""
    return any(c.address in spared for c in miss_line(snap, shooter, target, reach).critters)


def dead(o: Obj) -> bool:
    return o.kind == CRITTER and bool(o.results & DEAD)
