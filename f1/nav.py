"""Getting around a map: what blocks, a path by A*, and walking it in steps the engine's own path finder can take.

The engine walks the player to a clicked hex by itself, but only to what is on the screen. So a long way is walked
as a series of verified walk_to() steps to the farthest point of our own path that is on the view, scrolling the
camera with the arrow keys when the next point is off it, and fighting when combat interrupts.
"""

import heapq
import time
from dataclasses import dataclass

from f1 import geometry, knowledge, state, win32
from f1.engine_map import Obj

OBJECT_HIDDEN, OBJECT_NO_BLOCK = 0x01, 0x10
BLOCKING_TYPES = {1, 2, 3}  # critters, scenery, walls; items, tiles and misc (exit grids, scroll blockers) do not


@dataclass(frozen=True)
class Obstacles:
    blocked: frozenset[int]
    exits: dict[int, tuple[int, int, int]]  # exit-grid tile -> (map, tile, elevation) it leads to
    doors: frozenset[int] = frozenset()  # closed doors (in `blocked` too): a path may cross them after opening
    locked: frozenset[int] = frozenset()  # closed doors with the lock bit set (in `doors` too)
    critters: frozenset[int] = frozenset()  # living critters' hexes (in `blocked` too): they move on

    @property
    def passable_doors(self) -> frozenset[int]:
        """What blocks when doors may be opened on the way."""
        return self.blocked - self.doors


OBJECT_MULTIHEX = 0x800  # object_types.h


def obstacles(mem, elevation: int, dude_address: int) -> Obstacles:
    blocked, exits, doors, locked, critters = set(), {}, set(), set(), set()
    for addr in state.objects(mem):
        raw = mem.read(addr, Obj.SIZE)

        def i32(off: int, raw: bytes = raw) -> int:
            return int.from_bytes(raw[off : off + 4], "little", signed=True)

        if i32(Obj.ELEVATION) != elevation or addr == dude_address:
            continue
        pid = i32(Obj.PID) & 0xFFFFFFFF
        kind = pid >> 24
        flags = i32(Obj.FLAGS) & 0xFFFFFFFF
        tile = i32(Obj.TILE)
        if kind == 5 and 0x05000010 <= pid <= 0x05000017:  # Exit Grid: the misc data holds where it leads
            exits[tile] = (i32(0x3C), i32(0x40), i32(0x44))
        # CE obj_blocking_at: a hidden object does not block (MBSTRG12's force fields that are off are hidden;
        # counted as walls they closed every way to the lift)
        if kind in BLOCKING_TYPES and not flags & (OBJECT_NO_BLOCK | OBJECT_HIDDEN):
            if kind == 1 and i32(Obj.CRITTER_RESULTS) & 0x80:  # dead critters do not block
                continue
            body = {tile, *geometry.ring(tile, 1)} if flags & OBJECT_MULTIHEX else {tile}  # a big body: the ring
            blocked.update(body)  # (radscorpions: flags 0x20002800)
            if kind == 1:
                critters.update(body)
            if kind == 2 and is_door(pid):  # an open door has OBJECT_NO_BLOCK (measured), a closed one blocks
                doors.add(tile)
                if i32(Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED:
                    locked.add(tile)
    return Obstacles(frozenset(blocked), exits, frozenset(doors), frozenset(locked), frozenset(critters))


def is_door(pid: int) -> bool:
    """A door by its prototype's kind (Shady Sands' curtains are doors), else by name ("Elevator Door" too: Vault 12's
    open like any door, measured)."""
    p = knowledge.proto(pid) or {}
    return p.get("kind") == "door" or "Door" in p.get("name", "")


def astar(start: int, goals: set[int], blocked: frozenset[int], limit: int = 60000) -> list[int] | None:
    """Shortest hex path from start to any goal tile, avoiding blocked tiles (goals may be blocked: exits, doors).
    No goals, no path (the Overseer's desk took the whole first ring round him: min() of nothing, a full run)."""
    if not goals:
        return None
    if start in goals:
        return [start]
    goal_list = list(goals)

    def h(t: int) -> int:
        return min(geometry.distance(t, g) for g in goal_list)

    frontier = [(h(start), 0, start)]
    came: dict[int, int] = {start: -1}
    cost = {start: 0}
    expanded = 0
    while frontier and expanded < limit:
        _, g, t = heapq.heappop(frontier)
        if t in goals:
            path = [t]
            while came[path[-1]] != -1:
                path.append(came[path[-1]])
            return path[::-1]
        if g > cost.get(t, 1 << 30):
            continue
        expanded += 1
        for n in geometry.neighbours(t):
            if n in blocked and n not in goals:
                continue
            ng = g + 1
            if ng < cost.get(n, 1 << 30):
                cost[n] = ng
                came[n] = t
                heapq.heappush(frontier, (ng + h(n), ng, n))
    return None


def scroll_towards(actor, tile: int, max_presses: int = 40) -> bool:
    """Arrow keys until `tile` is well inside the view (each press moves the camera one hex)."""
    from f1 import session

    for _ in range(max_presses):
        cam = actor.snap().camera
        if geometry.on_view(tile, cam, 48):
            return True
        x, y = geometry.tile_center(tile, cam)
        key = None
        if x < 48:
            key = "left"
        elif x > cam.view_width - 48:
            key = "right"
        elif y < 48:
            key = "up"
        elif y > cam.view_height - 48:
            key = "down"
        if key is None:
            return True
        before = actor.snap().center_tile
        session.press(key)
        time.sleep(0.15)
        if actor.snap().center_tile == before:
            return False  # the camera would not move that way (map edge)
    return geometry.on_view(tile, actor.snap().camera, 48)


DOOR_AIMS = ((0, -20), (0, -30), (0, -10), (8, -25), (-8, -25), (0, -40))  # the door picture stands above its hex


def _door_object(actor, tile: int, elevation: int) -> int | None:
    from f1 import world

    return next(
        (t.address for t in world.things(actor.mem) if t.tile == tile and t.elevation == elevation and is_door(t.pid)),
        None,
    )


def open_door(actor, tile: int) -> bool:
    """Arrow-click a closed door on `tile`; True once it no longer blocks (OBJECT_NO_BLOCK), as measured."""

    def is_open() -> bool:
        s = actor.snap()
        return tile not in obstacles(actor.mem, s.elevation, s.dude.address).doors

    actor.log.emit("action_start", action="open_door", tile=tile)
    s = actor.snap()
    if tile in obstacles(actor.mem, s.elevation, s.dude.address).locked:
        # its lock bit is set: one use (a script may open it: a code, a key), not 16 s of clicks (MBVATS12's cell)
        ok = actor.click_object(tile, is_open, 4, aims=DOOR_AIMS[:2]) or is_open()
        actor.set_mouse_mode(0)
        return actor._end(ok, "open" if ok else "locked", {"tile": tile}, "open_door").ok
    # a door opens late when the player walks round to its far side first (the Brotherhood's 17120 read "locked"
    # and stood open a moment later, live): a last look before calling it shut. Clicked where the cursor
    # names the door: Talus stands before BROHD12's 21080, and every blind click there opened his talk (live)
    door = _door_object(actor, tile, s.elevation)
    targets = frozenset({door}) if door else frozenset()
    ok = actor.click_object(tile, is_open, 10, aims=DOOR_AIMS, targets=targets) or win32.wait_for(is_open, 4, 0.2)
    if not ok and targets and actor.snap().screen == "dialogue":
        # a talk opened by a click meant for the door: blind clicks then. Not otherwise: a blind click acts on what
        # the hover named there, and Old Town's doors under a roof (gmouse.c object_under_mouse names nothing under
        # one) took 10 s more of them each, 26 s a door (the Hub)
        from f1 import dialogue

        dialogue.close(actor.mem)
        ok = actor.click_object(tile, is_open, 6, aims=DOOR_AIMS) or win32.wait_for(is_open, 4, 0.2)
    actor.set_mouse_mode(0)
    return actor._end(ok, "open" if ok else "still closed (locked?)", {"tile": tile}, "open_door").ok


# The share of the view's width and height the player may drift from its centre before Home brings the view back:
# 120 x 80 px of the 640 x 380 view at 640x480, 365 x 206 px of the 1920 x 981 view at 1920x1080
CENTRE_SLACK = (0.19, 0.21)
CENTRE = True  # keep_centred() on; off only to measure its cost


def keep_centred(actor, clamped: set[int]) -> None:
    """Home when the player drifted from the view's centre. The game's camera follows the player only across a
    change of elevation (CE obj_move_to_tile), so every walk leaves the player nearer the border: a recording shows
    the Agent at the edge, and the next step's reach shrinks on that side. Where HRP's
    camera stops at a map's edge Home cannot centre: `clamped` keeps such tiles, so it is not pressed again there."""
    if not CENTRE:
        return
    s = actor.snap()
    if s.screen != "map" or s.dude is None:
        return
    x, y = geometry.tile_center(s.dude.tile, s.camera)
    dx, dy = abs(x - s.camera.view_width // 2), abs(y - s.camera.view_height // 2)
    slack_x, slack_y = CENTRE_SLACK[0] * s.camera.view_width, CENTRE_SLACK[1] * s.camera.view_height
    if (dx <= slack_x and dy <= slack_y) or any(geometry.distance(s.dude.tile, t) <= 4 for t in clamped):
        return
    actor.center_view()
    if actor.snap().center_tile == s.center_tile:
        clamped.add(s.dude.tile)


def off_centre(cam: geometry.Camera, tile: int) -> bool:
    """`tile` lies past the slack round the view's centre (CENTRE_SLACK): a leg ending there slides the view on while
    the player walks (Actor.walk_leg)."""
    if not CENTRE:
        return False
    x, y = geometry.tile_center(tile, cam)
    dx, dy = abs(x - cam.view_width // 2), abs(y - cam.view_height // 2)
    return dx > CENTRE_SLACK[0] * cam.view_width or dy > CENTRE_SLACK[1] * cam.view_height


def next_step(path: list[int], cam: geometry.Camera, max_hexes: int = 10, margin: int = 24) -> int | None:
    """Index of the farthest path tile, at most `max_hexes` along, whose hex is on the view."""
    best = None
    for i in range(1, min(len(path), max_hexes + 1)):
        if geometry.on_view(path[i], cam, margin):
            best = i
    return best


def edge_step(at: int, goal: int, cam: geometry.Camera, blocked: frozenset[int], reach: int) -> int | None:
    """The hex on the view (8 px in) within `reach` of the player that is nearest `goal`, if two hexes nearer than
    the player: the way along a map's edge when the path itself leaves the view there."""
    here, best = geometry.distance(at, goal), None
    for r in range(1, reach + 1):
        for t in geometry.ring(at, r):
            if t in blocked or not geometry.on_view(t, cam):
                continue
            key = (geometry.distance(t, goal), r)
            if key[0] <= here - 2 and (best is None or key < best[0]):
                best = (key, t)
    return best[1] if best else None


def go_to(
    actor, goals: set[int], max_steps: int = 80, fight=True, avoid: frozenset[int] = frozenset()
) -> tuple[bool, str]:
    """Walk the player to any of `goals` on this map. Returns (reached, why). An exit grid ends the map: reached.
    `avoid` holds hexes the path must not cross (a radiation hot spot and a margin round it): planned around like
    walls, and walked in short steps, as the engine finds its own way between two points of ours. Logged as an
    action with the length of its first plan, which a review of the run can hold the walked hexes against."""
    s = actor.snap()
    actor.log.emit(
        "action_start", action="go_to", **{"from": s.dude.tile if s.dude else None}, goals=len(goals),
        max_steps=max_steps,
    )  # fmt: skip
    plan: list[int] = []
    ok, why = _walk(actor, set(goals), max_steps, fight, avoid, plan)
    s = actor.snap()
    actor.log.emit(
        "action_end", action="go_to", ok=ok, reason=why, planned=plan[0] if plan else None,
        at=s.dude.tile if s.dude else None,
    )  # fmt: skip
    return ok, why


def _walk(actor, goals: set[int], max_steps: int, fight, avoid: frozenset[int], plan: list[int]) -> tuple[bool, str]:
    """go_to's walk; `plan` gets the first path's length in hexes."""
    reach = 4 if avoid else 16
    start_map = actor.snap().map_name
    refused: set[int] = set()  # tiles the engine would not walk to (twice from the same place): planned around
    misses: dict[tuple[int, int], int] = {}
    clamped: set[int] = set()  # where Home could not centre the view (a map's edge)
    homed: set[int] = set()  # where the player was off the view and Home was pressed
    for step in range(max_steps):
        s = actor.snap()
        if s.screen != "map" or s.map_name != start_map:
            return True, f"left the map ({s.screen}, {s.map_name})"
        if actor.in_combat():
            if not fight:
                return False, "combat"
            out = actor.fight()
            if not out.ok:
                return False, f"fight: {out.reason}"
            continue
        if s.dude.tile in goals:
            return True, "arrived"
        obs = obstacles(actor.mem, s.elevation, s.dude.address)
        # locked doors only when there is no way round them (MSTRLR34's 21492 cost 16 s of the Master's countdown)
        path = astar(s.dude.tile, goals - refused or goals, obs.passable_doors | obs.locked | refused | avoid)
        path = path or astar(s.dude.tile, goals - refused or goals, obs.passable_doors | refused | avoid)
        if path is None:  # once right after saving it failed and a second later it did not: look again
            time.sleep(1.0)
            s = actor.snap()
            obs = obstacles(actor.mem, s.elevation, s.dude.address)
            path = astar(s.dude.tile, goals - refused or goals, obs.passable_doors | refused | avoid)
        # someone stands in the only way (after a reload, again and again: the same save, the same place; the rope in
        # Shady Sands East failed four tries, a full run at Full HD, and 75 s later the way was free): they
        # wander on, wait for it
        for _ in range(6 if path is None else 0):
            if (
                astar(s.dude.tile, goals - refused or goals, (obs.passable_doors | refused | avoid) - obs.critters)
                is None
            ):
                break
            time.sleep(2.0)
            s = actor.snap()
            obs = obstacles(actor.mem, s.elevation, s.dude.address)
            path = astar(s.dude.tile, goals - refused or goals, obs.passable_doors | refused | avoid)
            if path is not None:
                break
        if path is None:
            return False, "no path"
        if not plan:
            plan.append(len(path) - 1)
        door = next((k for k, t in enumerate(path[1:], start=1) if t in obs.doors), None)
        if door == 1:  # a closed door right ahead: open it (its default action), then look again
            if not open_door(actor, path[1]):
                refused.add(path[1])  # locked (the Brotherhood's 16318, live): plan round it; fail only without a way
                if (
                    astar(actor.snap().dude.tile, goals - refused or goals, obs.passable_doors | refused | avoid)
                    is None
                ):
                    return False, f"door at {path[1]} did not open"
            continue
        if door is not None:
            path = path[:door]  # walk up to the door first
        i = next_step(path, s.camera, max_hexes=reach)
        if i is None and not geometry.on_view(s.dude.tile, s.camera, 24) and s.dude.tile not in homed:
            # the view is elsewhere: Home first, once a tile: at a map's edge HRP's camera leaves the player by the
            # border and Home changes nothing (CHILDEAD's south: Home every 0.9 s for minutes)
            from f1 import session

            homed.add(s.dude.tile)
            session.press("home")
            time.sleep(0.8)
            continue
        if i is None:  # a step near the view's border first: scrolling for a path that changes (Aradesh walking
            i = next_step(path, s.camera, max_hexes=reach, margin=8)  # about his house) swung the view for minutes
        if i is None:
            if not scroll_towards(actor, path[min(len(path) - 1, 6)]):
                # the camera stops at the map's edge, where exit grids lie: take hexes nearer the view's border
                cam = actor.snap().camera
                i = next_step(path, cam, max_hexes=reach, margin=8)  # walk_to accepts 8
                if i is None and path[-1] in goals:
                    # the path leaves the view where HRP's camera stops at the map's edge (CHILDEAD's south exits
                    # from x 84): first a hex on the view nearer the goal, along the edge
                    step = edge_step(
                        s.dude.tile, path[-1], cam, obs.passable_doors | obs.locked | refused | avoid, reach
                    )
                    if step is not None:
                        path, i = [s.dude.tile, step], 1
                if i is None and path[-1] in goals:
                    # the goal lies past that edge (CHILDRN1's west exits: the player stood two hexes short for 53 s
                    # while the Master's countdown ran out): give up the goals out of view round it, take the others
                    refused |= {
                        g for g in goals if geometry.distance(g, path[-1]) <= 12 and not geometry.on_view(g, cam)
                    }
                    refused.add(path[-1])
                    if goals - refused:
                        continue
                if i is None:
                    return False, "cannot bring the path into view"
            else:
                continue
        before = actor.snap().dude.tile
        # A leg that ends the way (a goal, an exit grid, the hex before a door, the path's end, the last step allowed)
        # or keeps out of the foes' notice stops there: the caller then finds the player standing (the hunt walks
        # four steps and looks again). The others hand over to the next leg while the player walks (walk_leg).
        final = i == len(path) - 1 or path[i] in goals or path[i] in obs.exits or door is not None or bool(avoid)
        final = final or step == max_steps - 1
        if final:
            out = actor.walk_to(path[i], timeout_s=6 + i)
        else:
            near_edge = any(geometry.distance(path[i], t) <= 4 for t in clamped)
            pan = off_centre(s.camera, path[i]) and not near_edge
            out = actor.walk_leg(path[i], pan=pan, timeout_s=6 + i)
            if pan and actor.snap().center_tile == s.center_tile:
                clamped.add(path[i])  # the view would not slide there: a map's edge
        after = actor.snap()
        # onto an exit grid: the map is going, and a key now reaches what comes next (the Home of keep_centred skipped
        # the vats' explosion movie on the world map, live). Only a step that got there: a refused
        # exit hex must still count its misses (MOUNTN2's 17715: 541 walks in ten minutes, a full run)
        leaving = after.screen != "map" or after.map_name != start_map or (out.ok and path[i] in obs.exits)
        if final and not leaving and (out.ok or after.dude.tile != before):
            keep_centred(actor, clamped)
        if not out.ok and out.reason == "did not arrive" and actor.snap().dude.tile == before:
            misses[(before, path[i])] = misses.get((before, path[i]), 0) + 1
            if misses[(before, path[i])] >= 2:  # MOUNTN2: one hex the engine never walked to, retried for minutes
                refused.add(path[i])
        elif not out.ok:
            time.sleep(0.3)  # e.g. combat began, or the map changed under the step
    return False, "too many steps"
