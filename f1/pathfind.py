"""The path the game itself walks: fallout1-ce's make_path_func (anim.cc) ported line by line and read against the 1.1
exe (make_path 0x4159D4, make_path_func 0x4159E8, idist 0x415D9C, EST 0x415DC0, anim_can_use_door 0x415940), so the
tiles a click on a far tile will cross are known before the click, over a map snapshot (f1/scene.py).

The engine's A*: the open list is an array of 2000 slots (20-byte nodes: cmp 0x9C40), a new node takes the first free
slot, the next node taken is the first slot with the least estimate + cost (strictly less replaces); a tile is marked
seen when it enters the open list; a step costs 50 (add 0x32), plus 10 out of combat when it turns from the step before
(add 0xA); the estimate is idist (dx + dy - min(dx, dy) / 2) of the tiles' screen points under the current camera (the
exe calls EST, tile_coord of both tiles: the differences depend on the camera, as the engine's do); a neighbour other
than the goal is skipped when obj_blocking_at finds something on it, unless it is a door the mover may use
(anim_can_use_door: never the player; other critters with a biped or robot body, the door not locked). 2000 closed
nodes or 2000 open ones end the search with no path (cmp 0x7D0). The path is the chain back from the goal, at most
800 steps (cmp 0x320).

So for the player every closed door is a wall: a way through one is planned here with `doors_open` (the doors taken as
open, as they will be once opened), and walked after each door on it is opened.
"""

from f1 import doors, lof
from f1.geometry import Camera, tile_coord, tile_num_in_direction
from f1.scene import CRITTER, SCENERY, Obj, Snapshot

SLOTS = 2000
MAX_STEPS = 800


def idist(x1: int, y1: int, x2: int, y2: int) -> int:
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    return dx + dy - min(dx, dy) // 2


def can_use_door(mover: Obj, door: Obj, is_player: bool, locked: frozenset[int] = frozenset()) -> bool:
    """anim_can_use_door: never the player; a critter with a biped or robot body; a door-type scenery object whose
    lock bit is clear (LOCKED: the locked doors' addresses, read by the caller from their open flags)."""
    if is_player or mover.kind != CRITTER or door.kind != SCENERY:
        return False
    if doors.body_type(mover.pid) not in (doors.BIPED, doors.ROBOTIC):
        return False
    return doors.is_door(door.pid) and door.address not in locked


def find(snap: Snapshot, mover: Obj, start: int, goal: int, camera: Camera | None = None, in_combat: bool = False,
         check_goal: bool = False, is_player: bool = True, avoid: frozenset[int] = frozenset(),
         doors_open: frozenset[int] = frozenset(), locked: frozenset[int] = frozenset()) -> list[int] | None:  # fmt: skip
    """The tiles the game's walk from START to GOAL steps on (START left out), None when it finds no path.
    AVOID: tiles treated as blocked; DOORS_OPEN: tiles of closed doors treated as open (neither is the engine's: the
    bot's own, to plan a way it must not take, or one it will open)."""
    cam = camera or snap.camera
    elevation = mover.elevation

    def blocker(tile: int) -> Obj | None:
        o = lof.blocking_at(snap, mover, tile, elevation)
        if o is not None and o.tile in doors_open and o.kind == SCENERY and doors.is_door(o.pid):
            return None
        return o

    if check_goal and blocker(goal) is not None:
        return None
    gx, gy = tile_coord(goal, cam)
    sx, sy = tile_coord(start, cam)
    seen = {start}
    # a node: [tile, from, rotation, estimate, cost]; a free slot: None
    open_nodes: list[list[int] | None] = [None] * SLOTS
    open_nodes[0] = [start, -1, 0, idist(sx, sy, gx, gy), 0]
    open_count, closed = 1, []
    found = None
    while True:
        best, best_index, counted, index = None, -1, 0, 0
        while counted < open_count:
            node = open_nodes[index]
            if node is not None:
                counted += 1
                if best is None or node[3] + node[4] < best[3] + best[4]:
                    best, best_index = node, index
            index += 1
        temp = best
        open_count -= 1
        open_nodes[best_index] = None
        if temp[0] == goal:
            found = temp
            break
        closed.append(temp)
        if len(closed) == SLOTS:
            return None
        for rotation in range(6):
            tile = tile_num_in_direction(temp[0], rotation)
            if tile in seen:
                continue
            if tile != goal:
                if tile in avoid:
                    continue
                o = blocker(tile)
                if o is not None and not can_use_door(mover, o, is_player, locked):
                    continue
            free = open_nodes.index(None)
            open_count += 1
            if open_count == SLOTS:
                return None
            seen.add(tile)
            x, y = tile_coord(tile, cam)
            cost = temp[4] + 50 + (10 if not in_combat and temp[2] != rotation else 0)
            open_nodes[free] = [tile, temp[0], rotation, idist(x, y, gx, gy), cost]
        if open_count == 0:
            return None
    by_tile: dict[int, list[int]] = {}
    for node in closed:
        by_tile.setdefault(node[0], node)
    path = []
    node = found
    for _ in range(MAX_STEPS):
        if node[0] == start:
            break
        path.append(node[0])
        node = by_tile[node[1]]
    return path[::-1]


def doors_on(snap: Snapshot, path: list[int], elevation: int) -> list[int]:
    """The closed doors' tiles on PATH, in walking order."""
    out = []
    for tile in path:
        for o in snap.by_tile.get(tile, []):
            if (o.elevation == elevation and o.kind == SCENERY and not o.flags & doors.NO_BLOCK
                    and doors.is_door(o.pid)):  # fmt: skip
                out.append(tile)
                break
    return out
