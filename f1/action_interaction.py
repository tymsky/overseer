"""Object targeting, dialogue clicks and skill use for the public Actor."""

import struct
import time

from f1 import art, dialogue, floats, geometry, hit, knowledge, lof, nav, scene, session, state, win32
from f1 import engine_map as em
from f1.action_shared import MOUSE_ARROW, Outcome, alive
from f1.engine_map import Obj
from f1.memory import ReadError

LAST_OBJECT = em.GLOBALS["last_object"].address
# game.c: keys 2-8 put a skilldex skill on the mouse (key, gmouse.h mode); 1 toggles sneaking
SKILL_KEYS = {
    "lockpick": ("2", 6), "steal": ("3", 7), "traps": ("4", 8), "first aid": ("5", 4), "doctor": ("6", 5),
    "science": ("7", 9), "repair": ("8", 10),
}  # fmt: skip
AIM_OFFSETS = ((0, -4), (0, 0), (0, -10), (-5, -6), (5, -6))
TALK_AIMS = AIM_OFFSETS + ((0, -22), (0, -32), (0, -42))  # a figure on a raised platform (the Overseer: -30)
AMBIENT = (("MISC", 1007),)  # lines of the place, not a use's answer: the Glow's radiation doses (MISC.MSG)
# round a hex's centre, nearest first (from the top row, a target low beside its hex came after most of the 121 points)
AIM_GRID = tuple(
    sorted(
        ((dx, dy) for dy in range(-64, 17, 8) for dx in range(-40, 41, 8)),
        key=lambda d: (d[0] * d[0] + d[1] * d[1], d[1], d[0]),
    )
)
# a lying body: its picture spreads round its hex, never far above it
BODY_GRID = tuple(d for d in AIM_GRID if -40 <= d[1] <= 16)
OBJECT_FLAT = 0x08  # object_types.h: drawn first, under everything else
# A click on an object within ~0.3 s of the engine naming it under the resting cursor does nothing; after 1 s it acts
# (the WATRSHD ladder: 0.3 s no, 1.0 s and 2.0 s climbed; every aimed click there had failed for 40 s)
LOOK_SETTLE_S = 1.0


# Where each object was last named under the cursor, from the centre its search went round (object address -> dx,
# dy): the Heights' strongbox was found at (-16, -16) after 70 grid points, 28 s, on each of three lockpick tries
# (the 1x chain)
_AIMED: dict[int, tuple[int, int]] = {}
_CLICKED: dict[int, tuple[int, int]] = {}  # click_object's own: where a click on the object did what it was for


def aim_order(
    spots: list[tuple[int, int]],
    centre: tuple[int, int],
    grid: tuple[tuple[int, int], ...],
    remembered: list[tuple[int, int] | None] = (),
) -> list[tuple[int, int]]:
    """The points _aim_at rests the cursor on, in order, each once: where the target was named before (offsets from
    `centre`), then `spots`, then the grid round `centre` nearest first. A grid swept from its top row reached a
    target low beside its hex only after most of the 121 points at 0.4 s each (the strongbox, Sinthia: 30-50 s)."""
    first = [(centre[0] + d[0], centre[1] + d[1]) for d in remembered if d is not None]
    ring = sorted(grid, key=lambda d: (d[0] * d[0] + d[1] * d[1], d[1], d[0]))
    out: list[tuple[int, int]] = []
    for p in first + list(spots) + [(centre[0] + dx, centre[1] + dy) for dx, dy in ring]:
        if p not in out:
            out.append(p)
    return out


class InteractionActions:
    """Targeting and interaction using the Actor input and movement actions."""

    def _interaction_done(self, done, targets: frozenset[int]) -> bool:
        s = self.snap()
        if not alive(s) or s.screen not in {"map", "worldmap", "dialogue", "elevator", "loot"}:
            return False
        if s.screen == "dialogue" and targets:
            d = dialogue.read(self.mem)
            if not d.active or d.target not in targets:
                return False
        return bool(done())

    def set_mouse_mode(self, mode: int) -> bool:
        """Toggle the cursor with M until the engine's gmouse_3d_current_mode is `mode` (0 move, 1 arrow)."""
        for _ in range(4):
            if self.mem.glob("gmouse_3d_current_mode") == mode:
                return True
            session.press("m")
            win32.wait_for(lambda: self.mem.glob("gmouse_3d_current_mode") == mode, 0.4, 0.02)
        return self.mem.glob("gmouse_3d_current_mode") == mode

    def talk_to(self, critter: state.Critter, timeout_s: float = 15) -> Outcome:
        """Click the critter in arrow mode (its default action is talk); done when the dialogue screen is up."""
        self.log.emit("action_start", action="talk_to", target=critter.address, tile=critter.tile)
        if not self.set_mouse_mode(MOUSE_ARROW):
            return self._end(False, "no arrow cursor", {}, "talk_to")
        s = self.snap()
        if s.screen != "map" or not alive(s):
            return self._end(False, "not on a living map", {}, "talk_to")
        start_map = (s.map_name, s.elevation)
        if not geometry.on_view(critter.tile, s.camera, 150):  # well inside the view already: no slide for it
            self.center_view()
        s = self.snap()
        if not geometry.on_view(critter.tile, s.camera, 48):
            nav.scroll_towards(self, critter.tile)
            s = self.snap()
        if not geometry.on_view(critter.tile, s.camera, 24):
            return self._end(False, "not on the view", {}, "talk_to")
        cx, cy = geometry.tile_center(critter.tile, s.camera)
        aims = [(dx, dy - 8) for dx, dy in TALK_AIMS]  # a standing figure: aim at the body, above the hex
        # first where the engine names the critter itself: a counter or a desk in front takes a blind click (Killian
        # behind his, a full run: no talk in 17 s of clicks). At most 10 s: Sinthia was never named and the
        # whole grid cost 48 s before a blind aim opened her talk (the 1x chain)
        # The grid goes out from the figure's middle, 28 px above its hex: Beth behind her counter answered at 48 px
        # up, after 19 s of points (the Hub).
        self.companions_settle()
        spot = self.predict(critter.address)
        if spot is not None and self.confirm(spot, frozenset({critter.address}), "talk_to") != critter.address:
            spot = None  # the computed point is not named (logged as a disagreement): the search as before
        if spot is None:
            spot = self._aim_at(
                frozenset({critter.address}), [(cx + dx, cy + dy) for dx, dy in aims], (cx, cy - 28), budget_s=10
            )
        if spot is not None:
            aims = [(spot[0] - cx, spot[1] - cy), *aims]
        talking = lambda: self.snap().screen == "dialogue" and dialogue.read(self.mem).target == critter.address
        recovered = 0
        for dx, dy in aims:
            if not alive(self.snap()):
                return self._end(False, "no living player", {}, "talk_to")
            now = state.read_critter(self.mem, critter.address)
            if now.dead or now.hp <= 0:
                return self._end(False, "target is no longer living", {}, "talk_to")
            if now.tile != critter.tile and geometry.on_view(now.tile, s.camera, 24):
                # the townsfolk walk about: the aims follow the critter's hex (Curtis walked off while every aim
                # clicked his old hex for 19 s)
                critter = now
                cx, cy = geometry.tile_center(critter.tile, s.camera)
            said = floats.keys(self.mem, critter.address)
            floated = lambda said=said, who=critter.address: bool(floats.keys(self.mem, who) - said)
            self.point(cx + dx, cy + dy)
            self.click()
            win32.wait_for(lambda floated=floated: talking() or floated(), timeout_s / len(TALK_AIMS), 0.1)
            if not talking() and floated():
                win32.wait_for(talking, 1.5, 0.1)  # a script may float a line and then start the talk
            if talking():
                # The dialogue block still holds the last talk until the first node is shown: wait for options.
                win32.wait_for(lambda: self.mem.glob("gdNumOptions") > 0, 5, 0.1)
                time.sleep(0.3)
                return self._end(True, "talking", {"aim": [dx, dy], "aimed": spot is not None}, "talk_to")
            if self.snap().screen == "dialogue":
                if recovered >= 2 or not nav.recover_companion_dialogue(self, start_map):
                    return self._end(False, "wrong dialogue target", {}, "talk_to")
                recovered += 1
                self.companions_settle()
                continue
            if spot is not None and floated():
                # an aimed click answered by a float over their head instead of a talk: the cook's LK check failed
                # (COOK.INT talk_p_proc), and every further aim only floated it again for 15 s
                text = [f.text for f in floats.floats(self.mem) if f.owner == critter.address]
                return self._end(False, "answered by a float", {"aimed": True, "text": text[-1:]}, "talk_to")
        return self._end(False, "no dialogue", {"aimed": spot is not None}, "talk_to")

    def click_object(
        self, tile: int, done, timeout_s: float = 20.0, lift: int = 0, aims=None, targets: frozenset[int] = frozenset()
    ) -> bool:
        """Arrow-mode click on whatever stands on `tile` (its default action: pick up an item, open a container or a
        door, use stairs or an elevator); True once `done()` holds. The player walks there first, so each aim gets
        time to show movement before the next aim is tried. With `targets` (object addresses), an aim is clicked
        only after the cursor rested there names one of them (hovering()): MSTRLR12's stairs lie under a bookcase,
        whose loot screen took every blind click for seven minutes. A loot screen opened by a wrong
        click is closed."""
        from f1 import actions

        if not self.set_mouse_mode(MOUSE_ARROW):
            return False
        s = self.snap()
        if not alive(s) or s.screen != "map":
            return False
        start_map = (s.map_name, s.elevation)
        checked = lambda: self._interaction_done(done, targets)
        if not geometry.on_view(tile, s.camera, 150):  # well inside the view already: no slide for it
            self.center_view()
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 48):
            nav.scroll_towards(self, tile)
            s = self.snap()
        if targets:  # Vault 15's ladder, an ironman run: Ian walked up as the named point was clicked, and his talk
            self.companions_settle()  # opened instead (60 s in it, stuck)
        cx, cy = geometry.tile_center(tile, s.camera)
        end = time.monotonic() + timeout_s
        if targets:  # a rest names only a new object: start from another
            self.rest_off(targets)
        points = list(aims or ((0, 0), (0, -6), (0, -12), (5, -4), (-5, -4), (0, 4)))
        predicted = None
        if targets:  # f1.hit's computed point first: the hover after it confirms it
            predicted = next((p for t in sorted(targets) if (p := self.predict(t)) is not None), None)
        if targets:  # then a grid round the hex, as _aim_at: two monitors drawn over the vats computer took every aim
            front = self._art_aims(tile, targets) if actions.ART_AIMS else []
            # where a click of this kind named it last time first (Vault 15's ladder: 34-46 s of search, each run). Its
            # own memory: a spot found for a key's use cursor (GLOW2's red lift door, 6 px up) took every arrow click
            # for the whole 10 s, and the door stayed shut where it had opened in 2.5 s (the chain)
            front = [(m[0], m[1] + lift) for m in (_CLICKED.get(t) for t in targets) if m is not None] + front
            if predicted is not None:
                front = [(predicted[0] - cx, predicted[1] - cy + lift), *front]
            points = front + [p for p in points if p not in front]
            points += [p for p in AIM_GRID if p not in points]
        recovered = 0
        for dx, dy in points:
            if self.snap().screen == "dialogue" and nav.companion_talk(self, self.snap()):
                if recovered >= 2 or not nav.recover_companion_dialogue(self, start_map):
                    return False
                recovered += 1
                self.companions_settle()
                if not self.set_mouse_mode(MOUSE_ARROW):
                    return False
            if checked():  # a click whose effect came late (the vats computer's talk after the use animation)
                return True
            s = self.snap()
            if not alive(s) or s.screen != "map" or (s.map_name, s.elevation) != start_map:
                return False
            cx, cy = geometry.tile_center(tile, s.camera)
            if time.monotonic() > end:
                break
            x, y = cx + dx, cy + dy - lift
            if targets and not (0 <= x < s.camera.view_width and 0 <= y < s.camera.view_height):
                continue
            if targets:
                # a rest names only a new object: after a click that did nothing the target is still the last one
                # named, and every further point read 0 and was passed by: GLOW2's red lift door got one click in
                # 10 s, and stayed shut (the chain; one aimed click by hand opened it)
                self.rest_off(targets)
            named = self.hovering(x, y) if targets else 0
            if targets and (x, y) == predicted:
                self.log.emit("aim_check", what="click_object", point=[x, y], named=named, agree=named in targets)
            if targets and named not in targets:
                continue
            if targets:
                time.sleep(LOOK_SETTLE_S)
            if not targets:
                self.point(cx + dx, cy + dy - lift)
            start = self.snap().dude.tile
            self.click()
            moved = win32.wait_for(
                lambda start=start: (
                    checked() or self.snap().screen != "map" or not alive(self.snap()) or self.snap().dude.tile != start
                ),
                1.5,
                0.1,
            )
            if (
                moved
                and win32.wait_for(
                    lambda: checked() or self.snap().screen != "map", max(1.0, end - time.monotonic()), 0.1
                )
                and checked()
            ):
                if targets:
                    _CLICKED[named] = (dx, dy - lift)
                return True
            if self.snap().screen == "loot" and not checked():
                session.press("esc")
                win32.wait_for(lambda: self.snap().screen == "map", 3, 0.1)
            if time.monotonic() > end:
                break
        if recovered < 2 and nav.companion_talk(self, self.snap()):
            nav.recover_companion_dialogue(self, start_map)
        return checked()

    def _art_aims(self, tile: int, targets: frozenset[int]) -> list[tuple[int, int]]:
        """Points round the hex centre of `tile` where one of `targets` shows in its picture and no picture of a
        nearby object covers it (f1.art: the WATRSHD ladder shows only along its rail beside a Cave Wall); none for a
        critter or art it cannot place."""
        s = self.snap()
        near = self._objects_near(tile, 3)
        target = next((o for o in near if o in targets), None)
        placed = self._placed(target, tile, s.camera) if target is not None else None
        if placed is None:
            return []
        others = []
        for o in near:
            flat = self.mem.i32(o + Obj.FLAGS) & OBJECT_FLAT
            if o == target or flat or self.mem.i32(o + Obj.ELEVATION) != s.elevation:
                continue
            if (p := self._placed(o, tile, s.camera)) is not None:
                others.append(p)
        return art.visible_points(placed, others)

    def _placed(self, obj: int, origin: int, cam: geometry.Camera) -> art.Placed | None:
        """An object's picture placed relative to the hex centre of `origin`."""
        pic = art.picture(self.mem.u32(obj + Obj.FID), self.mem.i32(obj + Obj.ROTATION))
        if pic is None:
            return None
        ox, oy = geometry.tile_center(self.mem.i32(obj + Obj.TILE), cam)
        cx, cy = geometry.tile_center(origin, cam)
        x0, y0 = art.corner(pic, self.mem.i32(obj + Obj.X), self.mem.i32(obj + Obj.Y))
        return art.Placed(pic, ox - cx + x0, oy - cy + y0)

    def _objects_near(self, tile: int, radius: int) -> list[int]:
        """Objects on `tile` and the hexes within `radius`, every elevation (objectTable's per-tile lists)."""
        table = em.GLOBALS["objectTable"].address
        out = []
        for t in {tile, *(x for r in range(1, radius + 1) for x in geometry.ring(tile, r))}:
            node = self.mem.u32(table + 4 * t) if 0 <= t < 40000 else 0
            for _ in range(100):
                if not node:
                    break
                obj, node = struct.unpack("<II", self.mem.read(node, 8))
                out.append(obj)
        return out

    def hovering(self, x: int, y: int, dy: int = 0, wait_s: float = 0.5) -> int:
        """Rest the arrow cursor on (x, y + dy) and return the object the engine names there: gmouse.c looks at the
        object under a cursor that rested 250 ms and keeps it in last_object, which changes only when the cursor
        rests on another object (an empty spot leaves it). Returns 0 when it did not change."""
        before = self.mem.u32(LAST_OBJECT)
        self.point(x, y + dy)
        if win32.wait_for(lambda: self.mem.u32(LAST_OBJECT) != before, wait_s, 0.03):
            return self.mem.u32(LAST_OBJECT)
        return 0

    def scene(self) -> scene.Snapshot | None:
        """The map as the hit test sees it (None when it could not be read while the map's lists changed)."""
        try:
            return scene.snapshot(self.mem)
        except ReadError:
            return None

    def predict(self, address: int, kind: int = -1, with_dude: bool = True) -> tuple[int, int] | None:
        """f1.hit's one point to click the object at (the arrow cursor's walk; `kind` CRITTER and no player for the
        crosshair's), None when no pixel of it is hit on this view."""
        snap = self.scene()
        target = snap.find(address) if snap else None
        if target is None:
            return None
        a = hit.aim(target, snap, kind, with_dude)
        self.log.emit("aim", target=address, of_kind=kind, point=a.point, pixels=a.pixels, depth=a.depth, why=a.reason)
        return a.point

    def rest_off(self, targets: frozenset[int]) -> None:
        """A rest names only a new object, so with one of `targets` named the cursor first rests on the player: on a
        point the hit test finds on him, then above his hex. With the point above his hex alone, beside Vault 15's
        first shaft every check read "unchanged" and the rope was never clicked (two ironman scouts, three checks
        each try); that the shaft stayed named there is inferred, not read."""
        if self.mem.u32(LAST_OBJECT) not in targets:
            return
        s = self.snap()
        on_him = self.predict(s.dude.address)
        above = geometry.tile_center(s.dude.tile, s.camera)
        for x, y in [p for p in (on_him, (above[0], above[1] - 24)) if p is not None]:
            self.hovering(x, y)
            if self.mem.u32(LAST_OBJECT) not in targets:
                return

    def confirm(self, point: tuple[int, int], targets: frozenset[int], what: str) -> int:
        """The old check after the computed point: rest the arrow cursor there and read the object the engine names
        (hovering). A disagreement is logged ("aim_check"); returns the object named (0: none or unchanged)."""
        self.rest_off(targets)
        named = self.hovering(*point, wait_s=0.5)
        self.log.emit("aim_check", what=what, point=list(point), named=named, agree=named in targets)
        return named

    def check_tile(self, tile: int, point: tuple[int, int]) -> bool:
        """After the cursor was put on a hex's point in move mode: the hex cursor's tile is the one aimed at (a
        window over the view, or a view that moved, takes the click elsewhere). Logged when it is not."""
        mouse = self.mem.u32(em.GLOBALS["obj_mouse_flat"].address)
        seen = self.mem.i32(mouse + Obj.TILE) if mouse else -1
        if seen != tile:
            self.log.emit("aim_check", what="tile", point=list(point), wanted=tile, seen=seen, agree=False)
        return seen == tile

    def line_blocked(self, target: int) -> bool | None:
        """f1.lof: a shot from the player at `target` is blocked by something that is no critter (the game's
        AIM_BLOCKED); None when the map could not be read."""
        snap = self.scene()
        obj = snap.find(target) if snap else None
        if obj is None or snap.dude is None:
            return None
        line = lof.shot_blocked(snap, snap.dude, snap.dude.tile, obj.tile, obj)
        self.log.emit("lof", target=target, blocked=line.blocked, critters=line.critters,
                      obstacles=[o.address for o in line.obstacles])  # fmt: skip
        return line.blocked

    def _aim_at(
        self,
        targets: frozenset[int],
        spots: list[tuple[int, int]],
        centre: tuple[int, int],
        grid: tuple[tuple[int, int], ...] = AIM_GRID,
        budget_s: float | None = None,
    ):
        """A point where the engine names one of `targets` under a resting arrow cursor (last_object): the point that
        named it last time first, then `spots`, then `grid` round `centre`, nearest first, for at most `budget_s`.
        Whatever stands in front takes a click aimed at the hex (the vats' technicians before their computer:
        fourteen Science clicks on them, live)."""
        if not self.set_mouse_mode(MOUSE_ARROW):
            return None
        s = self.snap()
        self.rest_off(targets)
        end = None if budget_s is None else time.monotonic() + budget_s
        for x, y in aim_order(spots, centre, grid, [_AIMED.get(t) for t in targets]):
            if end is not None and time.monotonic() > end:
                break
            if not (0 <= x < s.camera.view_width and 0 <= y < s.camera.view_height):
                continue
            named = self.hovering(x, y, wait_s=0.4)
            if named in targets:
                _AIMED[named] = (x - centre[0], y - centre[1])
                return x, y
        return None

    def companions_settle(self, timeout_s: float = 3.0) -> None:
        """Wait (at most `timeout_s`) until no companion moves: one following the player walks up after he stops, and
        a click aimed at a target meanwhile lands on him (ironman runs: Ian stepped by Jarvis's bed as the Antidote's
        click came, the Antidote was gone and Jarvis not cured; Vault 15's ladder opened Ian's talk)."""

        def tiles() -> dict[int, int]:
            dude = self.snap().dude
            return {
                c.address: c.tile
                for c in state.critters(self.mem)
                if c.team == dude.team and c.address != dude.address and not c.dead
            }

        last, end = tiles(), time.monotonic() + timeout_s
        while last and time.monotonic() < end:
            time.sleep(0.5)
            now = tiles()
            if now == last:
                return
            last = now

    def _use_in_mode(
        self, tile: int, arm, done, timeout_s: float, aims, action: str, detail: dict, targets=frozenset()
    ) -> Outcome:
        """Put the cursor in a use mode with `arm()`, click the object on `tile` (the player walks up to it and acts)
        until done() holds; the message box's new lines go into the outcome. A far object is walked up to first:
        the Glow's beam lay beyond where the camera would scroll (live), and it keeps the aim on view."""
        before = state.messages(self.mem, 3)
        s = self.snap()
        if not alive(s) or s.screen != "map":
            return self._end(False, "not on a living map", detail, action)
        start_map = (s.map_name, s.elevation)
        checked = lambda: self._interaction_done(done, targets)
        if geometry.distance(s.dude.tile, tile) > 3:
            obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
            near = {t for r in (1, 2) for t in geometry.ring(tile, r) if t not in obs.blocked}
            reached, why = nav.go_to(self, near or {tile}, 80)
            if not reached:
                return self._end(False, f"could not approach: {why}", detail, action)
        if (
            not alive(self.snap())
            or self.snap().screen != "map"
            or (self.snap().map_name, self.snap().elevation) != start_map
        ):
            return self._end(False, "approach left the target map", detail, action)
        self.center_view()
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 48):
            nav.scroll_towards(self, tile)
            s = self.snap()
        cx, cy = geometry.tile_center(tile, s.camera)
        end = time.monotonic() + timeout_s

        def said() -> bool:  # a new line that is not the place's own (the Glow's doses came in the rope's wait)
            new = [m for m in state.messages(self.mem, 3) if m not in before]
            return any(not any(knowledge.says(m, *a) for a in AMBIENT) for m in new)

        offsets = list(aims or ((0, -6), (0, 0), (0, -12), (5, -4), (-5, -4), (0, -20)))
        if targets:  # a point on the target itself, found in arrow mode, then the use cursor clicks there
            self.companions_settle()
            spot = self._aim_at(targets, [(cx + dx, cy + dy) for dx, dy in offsets], (cx, cy))
            if spot is not None:
                offsets = [(spot[0] - cx, spot[1] - cy)] * 3
            detail = detail | {"aimed": spot is not None}
            time.sleep(LOOK_SETTLE_S)
            before = state.messages(self.mem, 3)  # the rests' looks (a door's locked line) are not the result
        recovered = 0
        for dx, dy in offsets:
            if self.snap().screen == "dialogue" and nav.companion_talk(self, self.snap()):
                if recovered >= 2 or not nav.recover_companion_dialogue(self, start_map):
                    return self._end(False, "companion dialogue did not close", detail, action)
                recovered += 1
                self.companions_settle()
            if checked():
                break
            s = self.snap()
            if not alive(s) or s.screen != "map" or (s.map_name, s.elevation) != start_map:
                return self._end(False, "interaction interrupted", detail, action)
            cx, cy = geometry.tile_center(tile, s.camera)
            if targets:
                if (
                    not self.set_mouse_mode(MOUSE_ARROW)
                    or self.confirm((cx + dx, cy + dy), targets, action) not in targets
                ):
                    continue
                time.sleep(LOOK_SETTLE_S)
            if not arm():
                return self._end(False, "no use cursor", detail, action)
            self.point(cx + dx, cy + dy)
            start = self.snap().dude.tile
            self.click()
            if win32.wait_for(
                lambda start=start: (
                    checked()
                    or said()
                    or self.snap().screen != "map"
                    or not alive(self.snap())
                    or self.snap().dude.tile != start
                ),
                2.0,
                0.1,
            ):
                win32.wait_for(
                    lambda: checked() or said() or self.snap().screen != "map", max(1.0, end - time.monotonic()), 0.1
                )
            if self.snap().screen == "dialogue" and nav.companion_talk(self, self.snap()):
                continue
            if checked() or said() or time.monotonic() > end:
                break
        time.sleep(0.5)
        if recovered < 2 and nav.companion_talk(self, self.snap()):
            nav.recover_companion_dialogue(self, start_map)
        ok = checked()
        new = [m for m in state.messages(self.mem, 3) if m not in before]
        return self._end(ok, "done" if ok else "no effect", detail | {"aim": [dx, dy], "messages": new}, action)

    def use_skill_on(
        self, skill: str, tile: int, done, timeout_s: float = 25.0, aims=None, targets=frozenset()
    ) -> Outcome:
        """A skill used on the object on `tile`: keys 2-8 put the skilldex's skills on the mouse (game.c; gmouse.h
        modes 4 first aid ... 10 repair), then a click on the object. done() says it worked (a door's lock, a GVAR)."""
        key, mode = SKILL_KEYS[skill]
        self.log.emit("action_start", action="use_skill_on", skill=skill, tile=tile)

        def arm() -> bool:
            if self.mem.glob("gmouse_3d_current_mode") != mode:
                session.press(key)
            return win32.wait_for(lambda: self.mem.glob("gmouse_3d_current_mode") == mode, 1.5, 0.05)

        detail = {"skill": skill, "tile": tile}
        return self._use_in_mode(tile, arm, done, timeout_s, aims, "use_skill_on", detail, targets)
