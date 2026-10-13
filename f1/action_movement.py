"""Walking, camera settling and stealth for the public Actor."""

import time

from f1 import clock, geometry, session, win32
from f1 import engine_map as em
from f1.action_shared import COMBAT_ACTIVE, Outcome

PROTO_CRITTER_FLAGS = 0x20  # the player prototype's critter flags: bit 0 sneaking


PAN_STEP = (32, 24)  # px the view scrolls a press of an arrow key (map.c map_scroll: dx * 32, dy * 24)
# A pan's key is held, then released for a pause: the game lost faster presses (6 right keys counted 6 at 20 + 30 ms,
# 3-4 at 15 + 20 ms, 2-3 at 10 + 10 ms; measured in HUBDWNTN at 1920x1080). 60 ms a step is ~17 steps
# (540 px) a second, a slide in a 15 fps recording.
PAN_HOLD_S, PAN_GAP_S = 0.03, 0.03
# A walk whose player keeps animating on one hex this long has stopped (MOUNTN2: a leg that never left
# its hex ran its whole 23.5 s timeout, the frames still changing; a door on the way opens in about 2 s)
TILE_STALL_S = 4.0
STILL_READS = 3  # a following view at rest: this many equal reads in a row
FOLLOW_SLACK = (0.19, 0.21)  # a following view resting farther from the player (share of its size) was moved away
PAN_MAX_KEYS = 30  # a longer way (a camera far from the player) is jumped with Home


def interleave(a: list[str], b: list[str]) -> list[str]:
    """Both lists' items in one, each spread evenly over the whole: a diagonal pan steps across and down in turn."""
    keyed = [((i + 0.5) / len(a), 0, k) for i, k in enumerate(a)] + [
        ((i + 0.5) / len(b), 1, k) for i, k in enumerate(b)
    ]
    return [k for _, _, k in sorted(keyed)]


class MovementActions:
    """Walking and view control using the Actor input and snapshot primitives."""

    def walk_to(self, tile: int, timeout_s: float = 12.0) -> Outcome:
        """Walk the player to `tile` by a click on its hex in move mode; done when the player stands there, still."""
        self.still_view()  # a following view still gliding would carry the hex away from the click
        before = self.snap()
        pre = {"from": before.dude.tile if before.dude else None, "to": tile, "mode": before.mouse_mode}
        self.log.emit("action_start", action="walk_to", **pre)
        if before.screen != "map" or before.dude is None:
            return self._end(False, "not on a map", pre)
        if self.mem.glob("combat_state") & COMBAT_ACTIVE:
            return self._end(False, "in combat", pre)
        if before.mouse_mode != 0 and not self.set_mouse_mode(0):  # e.g. left in arrow mode by a talk
            return self._end(False, f"mouse mode {before.mouse_mode}, not move", pre)
        if not geometry.on_view(tile, before.camera):
            return self._end(False, "tile not on the view", pre)
        cx, cy = geometry.tile_center(tile, before.camera)
        if not self.point(cx, cy):
            return self._end(False, "cursor did not land", pre | {"cursor": [cx, cy]})
        self.check_tile(tile, (cx, cy))
        t0 = time.monotonic()
        self.click()
        with clock.fast(self.pid):  # a test run's walk at its speed
            arrived = self._walk_until(tile, timeout_s)
            still = arrived and win32.wait_for(lambda: self._still(tile), 3.0, 0.05)
        after = self.snap()
        detail = pre | {"cursor": [cx, cy], "at": after.dude.tile, "seconds": round(time.monotonic() - t0, 2)}
        detail["combat"] = bool(self.mem.glob("combat_state") & COMBAT_ACTIVE)
        return self._end(bool(arrived and still), "arrived" if arrived else "did not arrive", detail)

    def walk_leg(self, tile: int, pan: bool = False, lead_s: float = 0.35, timeout_s: float = 12.0) -> Outcome:
        """One leg of a longer way (nav.go_to) that the next leg takes over without a stop. A click on `tile`; with
        `pan`, the view slides on to the leg's end while the player walks; back when the rest of the leg is less
        than `lead_s` of walking at the pace seen, so the next click finds the player still going. Ok when the
        player got there or is that near and moving. A way walked in stops lost a stop and a 0.3 s look for
        stillness a leg, and a slide of the view after it (Shady Sands: 9 of 23 s). A view that follows the player
        by itself (session.camera_follows) is let come to rest before the click, and never panned."""
        self.still_view()
        before = self.snap()
        pre = {"from": before.dude.tile if before.dude else None, "to": tile, "mode": before.mouse_mode, "leg": True}
        self.log.emit("action_start", action="walk_to", **pre)
        if before.screen != "map" or before.dude is None:
            return self._end(False, "not on a map", pre)
        if self.mem.glob("combat_state") & COMBAT_ACTIVE:
            return self._end(False, "in combat", pre)
        if before.mouse_mode != 0 and not self.set_mouse_mode(0):
            return self._end(False, f"mouse mode {before.mouse_mode}, not move", pre)
        if not geometry.on_view(tile, before.camera):
            return self._end(False, "tile not on the view", pre)
        cx, cy = geometry.tile_center(tile, before.camera)
        if not self.point(cx, cy):
            return self._end(False, "cursor did not land", pre | {"cursor": [cx, cy]})
        self.check_tile(tile, (cx, cy))
        t0 = time.monotonic()
        self.click()
        if pan and not session.camera_follows():
            self.center_view(tile)  # while the player walks: the view is still for the next leg's click
        start, got, why = before.dude.tile, False, "did not arrive"
        with clock.fast(self.pid):
            end = time.monotonic() + timeout_s
            last, since = None, time.monotonic()
            last_tile, on_tile = None, time.monotonic()
            while time.monotonic() < end:
                s = self.snap()
                if s.screen != "map" or s.map_name != before.map_name or s.dude is None:
                    got, why = True, "left the map"
                    break
                if self.mem.glob("combat_state") & COMBAT_ACTIVE:
                    why = "combat"
                    break
                d = s.dude
                if d.tile == tile:
                    got, why = True, "arrived"
                    break
                now = (d.tile, d.fid, d.frame)
                if now != last:
                    last, since = now, time.monotonic()
                elif time.monotonic() - since > 1.5:
                    break
                if d.tile != last_tile:
                    last_tile, on_tile = d.tile, time.monotonic()
                elif time.monotonic() - on_tile > TILE_STALL_S:
                    break
                walked, left = geometry.distance(start, d.tile), geometry.distance(d.tile, tile)
                pace = walked / (time.monotonic() - t0) if walked else 0.0  # hexes a second so far
                if pace and left <= 3 and left / pace < lead_s:
                    got, why = True, "on its way"
                    break
                time.sleep(0.02)
        after = self.snap()
        detail = pre | {"cursor": [cx, cy], "at": after.dude.tile if after.dude else None}
        detail |= {"seconds": round(time.monotonic() - t0, 2), "combat": bool(self.in_combat())}
        return self._end(got, why, detail)

    def _walk_until(self, tile: int, timeout_s: float, stall_s: float = 1.5) -> bool:
        """Wait until the player stands on `tile`. False at the timeout, or as soon as the player has not moved for
        `stall_s`: a trigger stops a walk (MSTRLR34's psychic corridor, live: two stops waited out their
        16 s timeouts while the Master's countdown ran)."""
        end = time.monotonic() + timeout_s
        last, since = None, time.monotonic()
        last_tile, on_tile = None, time.monotonic()
        while time.monotonic() < end:
            d = self.snap().dude
            if d.tile == tile:
                return True
            now = (d.tile, d.fid, d.frame)
            if now != last:
                last, since = now, time.monotonic()
            elif time.monotonic() - since > stall_s:
                return False
            if d.tile != last_tile:
                last_tile, on_tile = d.tile, time.monotonic()
            elif time.monotonic() - on_tile > TILE_STALL_S:
                return False
            time.sleep(0.05)
        return False

    def _still(self, tile: int) -> bool:
        a = self.snap().dude
        time.sleep(0.15)  # a walk's frame lasts 100 ms at 10 fps: a frame held longer is a stop
        b = self.snap().dude
        return a.tile == b.tile == tile and (a.fid, a.frame) == (b.fid, b.frame)

    def center_view(self, tile: int | None = None) -> None:
        """The view onto the player (or `tile`: a leg's end while the player walks there); waits until the camera
        settles. The view drifts (edge scrolling, earlier steps),
        and every click is computed from it. It slides there by the game's own scrolling, arrow keys in passes read
        back, so a recording pans where Home jumped; Home (game.c) only for a way longer than
        PAN_MAX_KEYS presses. A map's edge stops both (HRP's edges clamp a Full HD view sooner): an axis that fell
        short of its keys' way by more than half a step met one and is not asked again, the view stays where it got.
        A copy whose view follows the player by itself (session.camera_follows) is not panned: the view is let come
        to rest, and Home is pressed only when it rests away from the player (moved by someone else: a dialog, the
        screen's edge), never for a leg's end."""
        if session.camera_follows():
            self.still_view()
            s = self.snap()
            if tile is None and s.dude is not None and s.screen == "map":
                x, y = geometry.tile_center(s.dude.tile, s.camera)
                far_x, far_y = FOLLOW_SLACK[0] * s.camera.view_width, FOLLOW_SLACK[1] * s.camera.view_height
                if abs(x - s.camera.view_width / 2) > far_x or abs(y - s.camera.view_height / 2) > far_y:
                    session.press("home")
                    self.still_view()
            return
        stuck_x = stuck_y = False
        for _ in range(3):
            s = self.snap()
            if s.dude is None or s.screen != "map":
                return
            x, y = geometry.tile_center(s.dude.tile if tile is None else tile, s.camera)
            nx = 0 if stuck_x else round((x - s.camera.view_width / 2) / PAN_STEP[0])
            ny = 0 if stuck_y else round((y - s.camera.view_height / 2) / PAN_STEP[1])
            if nx == 0 and ny == 0:
                return
            if abs(nx) + abs(ny) > PAN_MAX_KEYS:
                session.press("home")
                self._camera_settled()
                return
            across, down = ["right" if nx > 0 else "left"] * abs(nx), ["down" if ny > 0 else "up"] * abs(ny)
            session.press_keys(interleave(across, down), PAN_GAP_S, PAN_HOLD_S)
            self._camera_settled()
            after = self.snap()
            if after.dude is None:
                return
            x2, y2 = geometry.tile_center(after.dude.tile if tile is None else tile, after.camera)
            stuck_x = stuck_x or abs(x2 - x) < (abs(nx) - 0.5) * PAN_STEP[0]
            stuck_y = stuck_y or abs(y2 - y) < (abs(ny) - 0.5) * PAN_STEP[1]

    def still_view(self, timeout_s: float = 3.0) -> bool:
        """On a copy whose view follows the player (session.camera_follows): wait until the view rests (unchanged
        over STILL_READS reads 30 ms apart), so a click computed from it lands where aimed. True at once elsewhere."""
        if not session.camera_follows():
            return True
        last, same, end = None, 0, time.monotonic() + timeout_s
        while time.monotonic() < end:
            c = self.snap().camera
            now = (c.offx, c.offy, c.tile_x, c.tile_y)
            same = same + 1 if now == last else 0
            if same >= STILL_READS:
                return True
            last = now
            time.sleep(0.03)
        return False

    def _camera_settled(self) -> int:
        """The camera's tile once it stopped moving (queued keys scroll on for a moment)."""
        last = None
        for _ in range(12):
            time.sleep(0.12)
            now = self.snap().center_tile
            if now == last:
                break
            last = now
        return last

    def sneaking(self) -> bool:
        """PC_FLAG_SNEAKING: bit 0 of the player prototype's critter flags (critter.c is_pc_flag)."""
        return bool(self.mem.u32(em.GLOBALS["pc_proto"].address + PROTO_CRITTER_FLAGS) & 1)

    def sneak(self, on: bool = True) -> bool:
        """Key 1 toggles sneaking (game.c); True once the flag says `on`."""
        for _ in range(2):
            if self.sneaking() == on:
                return True
            session.press("1")
            win32.wait_for(lambda: self.sneaking() == on, 1.5, 0.05)
        return self.sneaking() == on
