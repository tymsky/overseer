"""Verified actions: input, then a wait on engine state, then a checked result. The unit of reliability.

python -m f1.actions walktest [N]   M1's check: N walk commands (default 20) to tiles near the player, each
                                    confirmed by the player's tile read from memory; events in runs/<id>/
python -m f1.actions fight          fight adjacent enemies until combat is over (a first, crude M4 executor)
"""

import datetime
import random
import struct
import sys
import time
from dataclasses import dataclass
from typing import NamedTuple

from f1 import (
    art,
    chargen,
    clock,
    dialogue,
    floats,
    geometry,
    inputs,
    instance,
    knowledge,
    nav,
    odds,
    paths,
    scripts,
    session,
    state,
    ui,
    watchdog,
    weapons,
    win32,
)
from f1 import engine_map as em
from f1.engine_map import Obj
from f1.guard import Guard, OwnerActive
from f1.memory import GameMemory, ReadError
from f1.telemetry import EventLog

COMBAT_ACTIVE = 0x01  # combat_state bit (isInCombat)
COMBAT_TURN_OBJ = em.GLOBALS["combat_turn_obj"].address
MOUSE_CROSSHAIR = 2  # gmouse_3d_current_mode
MOUSE_ARROW = 1
MOUSE_USE_CROSSHAIR = 3  # an item to use on something (gmouse.h GAME_MOUSE_MODE_USE_CROSSHAIR)
LAST_OBJECT = em.GLOBALS["last_object"].address
# game.c: keys 2-8 put a skilldex skill on the mouse (key, gmouse.h mode); 1 toggles sneaking
SKILL_KEYS = {
    "lockpick": ("2", 6), "steal": ("3", 7), "traps": ("4", 8), "first aid": ("5", 4), "doctor": ("6", 5),
    "science": ("7", 9), "repair": ("8", 10),
}  # fmt: skip
# The loot screen (inventry.c, INVENTORY_LOOT_*): 537 x 376; slot centres inside it
LOOT_WINDOW, LOOT_BODY_SLOT, LOOT_PLAYER_LIST = (537, 376), (424 + 32, 35 + 24), (46 + 32, 35 + 24 + 96)
LOOT_AIMS = ((-12, -12), (-12, -6), (0, -12), (-24, -12), (12, -12), (0, -6), (-6, -18), (0, 0), (-18, -6))
CONTAINER_AIMS = ((0, -12), (0, -24), (0, -36), (-8, -24), (8, -24), (0, 0), (0, -48), (-12, -12), (12, -12))
OBJECT_IN_LEFT_HAND, OBJECT_IN_RIGHT_HAND, OBJECT_WORN = 0x01000000, 0x02000000, 0x04000000
ITEM_EQUIPPED = OBJECT_IN_LEFT_HAND | OBJECT_IN_RIGHT_HAND | OBJECT_WORN
PID_10MM_PISTOL, PID_STIMPAK, PID_10MM_JHP = 0x08, 0x28, 0x1D
HOSTILE_KINDS = (  # wild kinds and the mutant army, by prototype name (substring)
    "Rat",
    "Radscorpion",
    "Mantis",
    "Mole",
    "Raider",
    "Deathclaw",
    "Floater",
    "Centaur",
    "Mutant",
    "Nightkin",
    "Coyote",
    "Ghoul Attacker",
)
HOSTILE_EXACT = ("Dog",)  # wild dogs; not "Dogmeat"
REST = {"10 minutes": 0, "30 minutes": 1, "1 hour": 2, "2 hours": 3, "3 hours": 4, "4 hours": 5, "5 hours": 6,
        "6 hours": 7, "until morning": 8, "until noon": 9, "until evening": 10, "until midnight": 11,
        "until healed": 12}  # pipboy.c rest durations, in their order  # fmt: skip
ITEM_STATE_SIZE, ITEM_STATE_ACTION = 0x18, 0x10  # intface.c InterfaceItemState
ITEM_ACTION_PRIMARY, ITEM_ACTION_RELOAD = 1, 5
PROTO_BASE_STATS, STAT_COUNT, STAT_MAX_HP, STAT_MAX_AP = 0x24, 35, 7, 8  # critter proto: base, bonus stats
PROTO_CRITTER_FLAGS = 0x20  # the player prototype's critter flags: bit 0 sneaking
INVENTORY_CURSOR_HAND, INVENTORY_CURSOR_ARROW = 0, 1  # immode: 0 hand (drag), 1 arrow (action menu), 4 blank
INVENTORY_AP = 4  # the inventory screen's cost in combat
INVENTORY_ROWS = 6  # the inventory screen's list shows six items (inventry.c inven_cur_disp)
LIST_HOLD_S, LIST_GAP_S = 0.05, 0.03  # a burst of Down keys for the list: held past the ~50 ms the game may lose
# Interface bar buttons in game-screen pixels (intface.c positions, bar at y=381 on a 640x480 screen).
ITEM_BUTTON = (361, 440)  # the active item at 640x480 (the bar at (0, 380)): a click enters attack mode
ITEM_BUTTON_CODE = 110  # its right click sends 'n' (the next action): how it is found among the bar's buttons
AIM_OFFSETS = ((0, -4), (0, 0), (0, -10), (-5, -6), (5, -6))
TALK_AIMS = AIM_OFFSETS + ((0, -22), (0, -32), (0, -42))  # a figure on a raised platform (the Overseer: -30)
GOOD_SHOT = 50  # percent: below it a gun closes in on a foe that has no gun (f1.odds: fights on computed odds)
HOPELESS_SHOT = 20  # percent: below it, and no way closer this turn, the round is not spent
LONG_SHOT = 5  # percent: after STALL_TURNS turns with nothing done a shot this poor is taken all the same
STALL_TURNS = 2  # turns without a shot, a step or a heal before the fight takes long shots, then steps back
AMBIENT = ("you have received a", "dose of radiation")  # lines of the place, not a use's answer (the Glow)
# round a hex's centre, nearest first (from the top row, a target low beside its hex came after most of the 121 points)
AIM_GRID = tuple(
    sorted(
        ((dx, dy) for dy in range(-64, 17, 8) for dx in range(-40, 41, 8)),
        key=lambda d: (d[0] * d[0] + d[1] * d[1], d[1], d[0]),
    )
)
# a lying body: its picture spreads round its hex, never far above it
BODY_GRID = tuple(d for d in AIM_GRID if -40 <= d[1] <= 16)
# Aim first where the target's picture shows (f1.art), then the grid: the WATRSHD ladder in 20.0 s against 23.5 s
ART_AIMS = True
OBJECT_FLAT = 0x08  # object_types.h: drawn first, under everything else
# A click on an object within ~0.3 s of the engine naming it under the resting cursor does nothing; after 1 s it acts
# (the WATRSHD ladder: 0.3 s no, 1.0 s and 2.0 s climbed; every aimed click there had failed for 40 s)
LOOK_SETTLE_S = 1.0
# screens an Escape takes off the map (on the map itself an Escape opens the options menu)
CLOSABLE = ("options", "pipboy", "skilldex", "character", "inventory", "loot")
# Movies, the ending's slides and the credits are skipped (the bot is about the play, not the
# story; they took 13 of the 93 minutes of a full run). Space, not Escape: any key ends them, and Space does
# nothing on the map or in the main menu, where Escape opens the options menu or quits the game.
CINEMA_KEY = "space"
# False lets them play whole (for a recording of the whole game: a skipped slide is lost).
SKIP_CINEMA = True
CINEMA_LIMIT_S = 1200  # a cinema left to play: the longest, the credits, ran nine minutes in the first full run


def cinema(screen: str) -> bool:
    """A movie (a 0x10 window from the screen's corner), the ending's slides, or the credits (a 0x14 window from the
    corner as big as the screen: 640x480 at 640x480, 1920x1080 at Full HD; measured in full runs)."""
    if screen == "endgame" or state.window_kind(screen) == "movie":
        return True
    m = state.WINDOW_NAME.fullmatch(screen)
    return m is not None and int(m[1], 16) == 0x14 and (int(m[2]), int(m[3])) == (0, 0) and int(m[4]) >= 640


PAN_STEP = (32, 24)  # px the view scrolls a press of an arrow key (map.c map_scroll: dx * 32, dy * 24)
# A pan's key is held, then released for a pause: the game lost faster presses (6 right keys counted 6 at 20 + 30 ms,
# 3-4 at 15 + 20 ms, 2-3 at 10 + 10 ms; measured in HUBDWNTN at 1920x1080). 60 ms a step is ~17 steps
# (540 px) a second, a slide in a 15 fps recording.
PAN_HOLD_S, PAN_GAP_S = 0.03, 0.03
# A walk whose player keeps animating on one hex this long has stopped (MOUNTN2: a leg that never left
# its hex ran its whole 23.5 s timeout, the frames still changing; a door on the way opens in about 2 s)
TILE_STALL_S = 4.0
PAN_MAX_KEYS = 30  # a longer way (a camera far from the player) is jumped with Home


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


def retreat_hex(
    at: int, foes: list[int], blocked: set[int] | frozenset[int], max_hexes: int, cam: geometry.Camera
) -> int | None:
    """The free hex on the view within `max_hexes` of `at` that lies farthest from the nearest foe (the nearer of
    equals), or None when none is farther than `at` itself."""
    if not foes:
        return None
    here = min(geometry.distance(at, f) for f in foes)
    best = None
    for r in range(1, max_hexes + 1):
        for t in geometry.ring(at, r):
            if t in blocked or not geometry.on_view(t, cam, 24):
                continue
            key = (min(geometry.distance(t, f) for f in foes), -r)
            if key[0] > here and (best is None or key > best[0]):
                best = (key, t)
    return best[1] if best else None


def interleave(a: list[str], b: list[str]) -> list[str]:
    """Both lists' items in one, each spread evenly over the whole: a diagonal pan steps across and down in turn."""
    keyed = [((i + 0.5) / len(a), 0, k) for i, k in enumerate(a)] + [
        ((i + 0.5) / len(b), 1, k) for i, k in enumerate(b)
    ]
    return [k for _, _, k in sorted(keyed)]


# The save screen's boxes by shape (flags, width, height), measured live; their place moves with the resolution.
OVERWRITE_BOX = (0x14, 302, 127)  # "Save game already exists, overwrite?"
DESCRIPTION_BOX = (0x14, 290, 85)


ITEM_MENU_USE, ITEM_MENU_DROP = 1, 2
ITEM_MENU_DROP_UNUSABLE = 1  # an item with no Use (a weapon, ammunition, armour): Look, Drop, ...  # entries of an item's action menu below Look (inven_action_cursor act_use)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    reason: str
    detail: dict


class Held(NamedTuple):
    """The weapon in the active hand: its pid, the rounds in it and which ammunition they are."""

    pid: int
    loaded: int
    ammo_pid: int


class Actor:
    """Sends input to the running game through the guard and checks results in its memory."""

    def __init__(self, pid: int, log: EventLog) -> None:
        self.pid = pid
        self.log = log
        self.mem = GameMemory(pid)
        self.hwnd = session.game_window(pid)
        self.guard = Guard(state=session.GUARD_STATE)
        watchdog.clear()  # a new handle comes with a new game (a reload): a trip before it is moot

    def close(self) -> None:
        self.mem.close()

    def game_alive(self) -> bool:
        """The game's process still runs (a crash or a close ends it)."""
        return win32.is_alive(self.pid)

    def snap(self) -> state.Snapshot:
        """The game now. Raises watchdog.Stuck once the watchdog saw nothing move too long (routes.run)."""
        watchdog.check()
        return state.read(self.mem)

    def _front(self) -> None:
        inputs.ready(self.pid, self.hwnd, self.guard, 120, session.SessionError, OwnerActive)

    def point(self, x: int, y: int) -> bool:
        """Put the cursor on game-screen pixel (x, y) and confirm it with the engine's own mouse_x/y."""
        self._front()
        r = win32.client_rect(self.hwnd)
        with inputs.acting(self.guard):
            inputs.set_cursor(r.left + x, r.top + y)
        return win32.wait_for(lambda: (self.mem.glob("mouse_x"), self.mem.glob("mouse_y")) == (x, y), 1.0, 0.02)

    def click(self, right: bool = False) -> None:
        self._front()
        with clock.held(self.pid), inputs.acting(self.guard):  # the click at the game's own speed (f1.clock)
            inputs.mouse_button(down=True, right=right)
            time.sleep(0.06)
            inputs.mouse_button(down=False, right=right)

    def walk_to(self, tile: int, timeout_s: float = 12.0) -> Outcome:
        """Walk the player to `tile` by a click on its hex in move mode; done when the player stands there, still."""
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
        stillness a leg, and a slide of the view after it (Shady Sands: 9 of 23 s)."""
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
        t0 = time.monotonic()
        self.click()
        if pan:
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

    def _end(self, ok: bool, reason: str, detail: dict, action: str = "walk_to") -> Outcome:
        self.log.emit("action_end", action=action, ok=ok, reason=reason, **detail)
        return Outcome(ok, reason, detail)

    # --- talking ------------------------------------------------------------------------------------------------

    def center_view(self, tile: int | None = None) -> None:
        """The view onto the player (or `tile`: a leg's end while the player walks there); waits until the camera
        settles. The view drifts (edge scrolling, earlier steps),
        and every click is computed from it. It slides there by the game's own scrolling, arrow keys in passes read
        back, so a recording pans where Home jumped; Home (game.c) only for a way longer than
        PAN_MAX_KEYS presses. A map's edge stops both (HRP's edges clamp a Full HD view sooner): an axis that fell
        short of its keys' way by more than half a step met one and is not asked again, the view stays where it got."""
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
        spot = self._aim_at(
            frozenset({critter.address}), [(cx + dx, cy + dy) for dx, dy in aims], (cx, cy - 28), budget_s=10
        )
        if spot is not None:
            aims = [(spot[0] - cx, spot[1] - cy), *aims]
        talking = lambda: self.snap().screen == "dialogue"
        for dx, dy in aims:
            now = state.read_critter(self.mem, critter.address)
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
        if not self.set_mouse_mode(MOUSE_ARROW):
            return False
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 150):  # well inside the view already: no slide for it
            self.center_view()
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 48):
            nav.scroll_towards(self, tile)
            s = self.snap()
        cx, cy = geometry.tile_center(tile, s.camera)
        end = time.monotonic() + timeout_s
        if targets and self.mem.u32(LAST_OBJECT) in targets:  # a rest names only a new object: start from another
            self.hovering(*geometry.tile_center(s.dude.tile, s.camera), dy=-24)
        points = list(aims or ((0, 0), (0, -6), (0, -12), (5, -4), (-5, -4), (0, 4)))
        if targets:  # then a grid round the hex, as _aim_at: two monitors drawn over the vats computer took every aim
            front = self._art_aims(tile, targets) if ART_AIMS else []
            # where a click of this kind named it last time first (Vault 15's ladder: 34-46 s of search, each run). Its
            # own memory: a spot found for a key's use cursor (GLOW2's red lift door, 6 px up) took every arrow click
            # for the whole 10 s, and the door stayed shut where it had opened in 2.5 s (the chain)
            front = [(m[0], m[1] + lift) for m in (_CLICKED.get(t) for t in targets) if m is not None] + front
            points = front + [p for p in points if p not in front]
            points += [p for p in AIM_GRID if p not in points]
        for dx, dy in points:
            if done():  # a click whose effect came late (the vats computer's talk after the use animation)
                return True
            if time.monotonic() > end:
                break
            x, y = cx + dx, cy + dy - lift
            if targets and not (0 <= x < s.camera.view_width and 0 <= y < s.camera.view_height):
                continue
            if targets and self.mem.u32(LAST_OBJECT) in targets:
                # a rest names only a new object: after a click that did nothing the target is still the last one
                # named, and every further point read 0 and was passed by: GLOW2's red lift door got one click in
                # 10 s, and stayed shut (the chain; one aimed click by hand opened it)
                self.hovering(*geometry.tile_center(s.dude.tile, s.camera), dy=-24)
            named = self.hovering(x, y) if targets else 0
            if targets and named not in targets:
                continue
            if targets:
                time.sleep(LOOK_SETTLE_S)
            if not targets:
                self.point(cx + dx, cy + dy - lift)
            start = self.snap().dude.tile
            self.click()
            moved = win32.wait_for(lambda start=start: done() or self.snap().dude.tile != start, 1.5, 0.1)
            if moved and win32.wait_for(done, max(1.0, end - time.monotonic()), 0.1):
                if targets:
                    _CLICKED[named] = (dx, dy - lift)
                return True
            if self.snap().screen == "loot" and not done():
                session.press("esc")
                win32.wait_for(lambda: self.snap().screen == "map", 3, 0.1)
            if time.monotonic() > end:
                break
        return bool(done())

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

    def pick_up(self, tile: int, pid: int, timeout_s: float = 25.0) -> Outcome:
        """Pick up the item `pid` lying on `tile`, or take everything from the container there (the loot screen:
        Shift+A takes all, Escape closes it). Confirmed by the item's count going up."""
        from f1 import world

        before = self._count(pid)
        self.log.emit("action_start", action="pick_up", pid=pid, tile=tile, count=before)
        # aimed where the engine names the item itself: in the caves a radscorpion's body lay over the Stimpak and
        # took every blind click (live)
        s = self.snap()
        items = frozenset(t.address for t in world.things(self.mem) if t.tile == tile and t.pid == pid)
        items = frozenset(a for a in items if s.elevation == self.mem.i32(a + Obj.ELEVATION))
        line = self.mem.glob("disp_start")
        # the game's refusal ends it at once: Kyle's Powered Armor (85 lb) was clicked for 25 s a try, twice, against
        # "You cannot pick up that item. You are at your maximum weight capacity." (the bunker)
        heavy = lambda: any("maximum weight" in m.lower() for m in state.messages_since(self.mem, line))
        done = lambda: self._count(pid) > before or self.snap().screen != "map" or heavy()
        if not (items and self.click_object(tile, done, timeout_s, targets=items)) and not heavy():
            self.click_object(tile, done, timeout_s)
        if self.snap().screen != "map":  # a container's loot screen
            time.sleep(0.5)
            session.type_text("A")
            time.sleep(0.6)
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen == "map", 5, 0.1)
        self.set_mouse_mode(0)
        after = self._count(pid)
        why = "picked up" if after > before else "too heavy" if heavy() else "not picked up"
        return self._end(after > before, why, {"count": [before, after]}, "pick_up")

    def loot(self, tile: int, timeout_s: float = 25.0) -> Outcome:
        """Take everything from the body or the container (shelves, a locker) on `tile`. A click on it (arrow mode)
        walks the Agent there and opens the loot screen (gmouse.c action_loot_container); a lying body answers up and
        left of its hex, and the Agent's own sprite over it wins the click, so it is clicked from a few hexes away.
        The 1.1 exe has no take-all key (CE's Shift+A did nothing live): each item is dragged from the other side's
        list (the window's x 424, first slot) to the player's (x 46); a stack asks how many. Confirmed by the other
        side's inventory emptying."""
        from f1 import knowledge, world

        # on the Agent's own floor: GLOW1 has a Locker on 12925 on two floors, and the other floor's count (1 item,
        # unchanged) made a loot of this one "nothing taken" after 62 s
        elevation = self.snap().elevation
        body = next(
            (c for c in state.critters(self.mem) if c.dead and c.tile == tile and c.elevation == elevation), None
        )
        if body is None:  # a container: an item object of the container kind
            body = next(
                (
                    t
                    for t in world.things(self.mem)
                    if t.tile == tile
                    and t.elevation == elevation
                    and t.type == "item"
                    and (knowledge.proto(t.pid) or {}).get("item_type") == "container"
                ),
                None,
            )
        if body is None:
            return self._end(False, "no body or container there", {"tile": tile}, "loot")
        before = self.mem.i32(body.address + Obj.INV_LENGTH)
        self.log.emit("action_start", action="loot", tile=tile, items=before)
        if before <= 0:  # nothing inside: the Glow's empty lockers took 30 s each of clicks for no loot screen
            return self._end(False, "empty", {"items": [0, 0]}, "loot")
        opened = lambda: self._loot_window() is not None
        aims, targets = LOOT_AIMS, frozenset()
        if isinstance(body, state.Critter):
            # where the engine names the body under the cursor: the blind aims rested on "Wall." and "Door." over a
            # captor who died in a doorway, and on the Agent's own Power Armor over a raider at its feet, for 25 s a
            # body (the Hub). From a few hexes off when the near view found nothing.
            spot = self._body_spot(body, tile)
            if spot is None and geometry.distance(self.snap().dude.tile, tile) <= 3:
                s = self.snap()
                obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
                away = {t for r in (3, 4) for t in geometry.ring(tile, r) if t not in obs.blocked}
                if away and nav.go_to(self, away, 20)[0]:
                    spot = self._body_spot(body, tile)
            if spot is not None:
                cx, cy = geometry.tile_center(tile, self.snap().camera)
                aims, targets = ((spot[0] - cx, spot[1] - cy), *aims), frozenset({body.address})
                time.sleep(LOOK_SETTLE_S)
                self.click()
                win32.wait_for(opened, timeout_s, 0.1)
        if not isinstance(body, state.Critter):  # a container stands: its sprite rises above the hex
            aims = CONTAINER_AIMS
            s = self.snap()
            if geometry.distance(s.dude.tile, tile) > 1:  # inside first: a roof hides what stands under it
                obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
                nav.go_to(self, {t for t in geometry.ring(tile, 1) if t not in obs.blocked} or {tile}, 60)
            # a point where the engine names the container, as for skills (a pile of bones lies flat on its hex):
            # first where its picture shows (f1.art), then the usual offsets, then the grid. A bookcase took 17 s of
            # misses at 0.4 s each with the offsets alone, and the same search again in click_object (live)
            s = self.snap()
            if not geometry.on_view(tile, s.camera, 150):
                self.center_view()
                s = self.snap()
            cx, cy = geometry.tile_center(tile, s.camera)
            front = self._art_aims(tile, frozenset({body.address})) if ART_AIMS else []
            offsets = front + [a for a in aims if a not in front]
            spot = self._aim_at(frozenset({body.address}), [(cx + dx, cy + dy) for dx, dy in offsets], (cx, cy))
            if spot is not None:
                aims, targets = ((spot[0] - cx, spot[1] - cy), *aims), frozenset({body.address})
                # the cursor rests there and the engine names the container: the click after the settle, no search
                time.sleep(LOOK_SETTLE_S)
                self.click()
                win32.wait_for(opened, timeout_s, 0.1)
        if not opened():
            self.click_object(tile, opened, timeout_s, aims=aims, targets=targets) or (
                targets and self.click_object(tile, opened, timeout_s, aims=aims)
            )
        origin = self._loot_window()
        if origin is None:
            if self.snap().screen in ("barter", "dialogue"):  # a shopkeeper's shelf: his barter, not a loot screen
                dialogue.close(self.mem)
                self.set_mouse_mode(0)
                return self._end(False, "a shop's shelf", {"items": before}, "loot")
            self.set_mouse_mode(0)
            return self._end(False, "no loot screen", {"items": before}, "loot")
        ox, oy = origin
        line = self.mem.glob("disp_start")
        heavy = lambda: any("maximum weight" in m.lower() for m in state.messages_since(self.mem, line))
        for _ in range(before + 2):
            left = self.mem.i32(body.address + 0x2C)
            if left <= 0 or heavy():  # "You cannot pick that up. You are at your maximum weight capacity." (the Glow)
                break
            arr = self.mem.u32(body.address + 0x34)
            quantity = self.mem.i32(arr + 4)
            windows = len(state.live_windows(self.mem))
            self.drag(
                ox + LOOT_BODY_SLOT[0], oy + LOOT_BODY_SLOT[1], ox + LOOT_PLAYER_LIST[0], oy + LOOT_PLAYER_LIST[1]
            )
            if quantity > 1 and win32.wait_for(lambda n=windows: len(state.live_windows(self.mem)) > n, 1.5, 0.05):
                time.sleep(0.2)
                session.type_text(str(min(quantity, 999)))
                session.press("enter")
            win32.wait_for(lambda left=left: self.mem.i32(body.address + 0x2C) < left, 2, 0.05)
        after = self.mem.i32(body.address + 0x2C)
        if self.snap().screen == "loot":  # on the map an Escape opens the options menu (the rest's)
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen == "map", 5, 0.1)
        self.set_mouse_mode(0)
        why = "looted" if after < before else "too heavy" if heavy() else "nothing taken"
        return self._end(after < before, why, {"items": [before, after]}, "loot")

    def _body_spot(self, body: state.Critter, tile: int) -> tuple[int, int] | None:
        """A point where the engine names the dead `body` under the resting cursor: what a click there acts on
        (gmouse.c object_under_mouse looks past a dead critter at whatever lies under it, and the hover names the
        same object). The usual aims first, then the low grid round its hex, for at most 8 s."""
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 150):
            self.center_view()
            s = self.snap()
        cx, cy = geometry.tile_center(tile, s.camera)
        spots = [(cx + dx, cy + dy) for dx, dy in LOOT_AIMS]
        return self._aim_at(frozenset({body.address}), spots, (cx, cy), grid=BODY_GRID, budget_s=8)

    def _loot_window(self) -> tuple[int, int] | None:
        for w in state.live_windows(self.mem):
            if w[4:] == LOOT_WINDOW:
                return w[2], w[3]
        return None

    # --- inventory ----------------------------------------------------------------------------------------------

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Press on (x0, y0), move in steps to (x1, y1), release: the game's drag and drop."""
        self.point(x0, y0)
        self._front()
        r = win32.client_rect(self.hwnd)
        with clock.held(self.pid), inputs.acting(self.guard):
            inputs.mouse_button(down=True)
            time.sleep(0.15)
            steps = 12
            for k in range(1, steps + 1):
                inputs.set_cursor(r.left + x0 + (x1 - x0) * k // steps, r.top + y0 + (y1 - y0) * k // steps)
                time.sleep(0.03)
            time.sleep(0.15)
            inputs.mouse_button(down=False)
        time.sleep(0.3)

    def items(self) -> list[tuple[int, int, int]]:
        """(item address, pid, quantity) of the player's inventory, in its order."""
        dude = self.mem.u32(em.GLOBALS["obj_dude"].address)
        n, arr = self.mem.i32(dude + Obj.INV_LENGTH), self.mem.u32(dude + Obj.INV_ITEMS)
        out = []
        for i in range(max(0, min(n, 200))):
            item, qty = self.mem.u32(arr + 8 * i), self.mem.i32(arr + 8 * i + 4)
            out.append((item, self.mem.u32(item + Obj.PID), qty))
        return out

    def _inventory_window(self) -> tuple[int, int] | None:
        wid = self.mem.glob("i_wid")
        for w in state.live_windows(self.mem):
            if w[0] == wid:
                return w[2], w[3]
        return None

    def open_inventory(self) -> tuple[int, int] | None:
        """The inventory screen, with the hand cursor (drag and drop); its window's origin."""
        for _ in range(3):  # a key during the holster animation is lost (after a readying, a full run in the Glow)
            if self.snap().screen == "inventory":
                break
            session.press("i")
            win32.wait_for(lambda: self.snap().screen == "inventory", 2, 0.1)
        time.sleep(0.4)
        if self.snap().screen != "inventory":
            return None
        origin = self._inventory_window()
        if origin and self.mem.glob("immode") != INVENTORY_CURSOR_HAND:  # a right click toggles hand and arrow
            self.point(origin[0] + 300, origin[1] + 20)
            self.click(right=True)
            win32.wait_for(lambda: self.mem.glob("immode") == INVENTORY_CURSOR_HAND, 1.0, 0.05)
        return origin

    def close_inventory(self) -> None:
        if self.snap().screen == "inventory":
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen != "inventory", 3, 0.1)

    def _scroll_list(self, slot: int) -> int | None:
        """Scroll the inventory screen's list (six rows; Down moves it one item, inventry.c) until `slot` shows; its
        row, or None. The keys go in one burst and the engine's stack_offset[0] says where the list stands: one key at
        a time, 0.15 s apart and each through the presence guard, took 6-10 s for a holodisk at the end of a long
        list (the Glow's four disks: 39 s)."""
        now = self.mem.glob("stack_offset")
        want = (
            now if now <= slot < now + INVENTORY_ROWS else max(0, slot - (INVENTORY_ROWS - 1)) if slot > now else slot
        )
        for _ in range(4):
            now = self.mem.glob("stack_offset")
            if now == want:
                break
            session.press_keys(["down" if want > now else "up"] * abs(want - now), LIST_GAP_S, LIST_HOLD_S)
            win32.wait_for(lambda: self.mem.glob("stack_offset") == want, 1.0, 0.03)
        row = slot - self.mem.glob("stack_offset")
        return row if 0 <= row < INVENTORY_ROWS else None

    def _list_slot(self, pid: int) -> int | None:
        """Where an item stands in the inventory screen's list: equipped items are not listed."""
        listed = [p for item, p, _ in self.items() if not self.mem.u32(item + Obj.FLAGS) & ITEM_EQUIPPED]
        return listed.index(pid) if pid in listed else None

    def equip(self, pid: int, hand: str = "left") -> Outcome:
        """Drag an item from the inventory list into a hand slot or the armor slot ("armor"); confirmed by the item's
        flag (in the left/right hand, worn)."""
        self.log.emit("action_start", action="equip", pid=pid, hand=hand)
        origin = self.open_inventory()
        if origin is None:
            return self._end(False, "no inventory screen", {}, "equip")
        slot = self._list_slot(pid)
        if slot is None:
            self.close_inventory()
            return self._end(False, "item not in the list", {}, "equip")
        shown = self._scroll_list(slot)
        if shown is None:
            self.close_inventory()
            return self._end(False, "list not scrolled", {"slot": slot}, "equip")
        ox, oy = origin
        sx, sy = ox + 46 + 32, oy + 35 + 48 * shown + 24
        targets = {"left": (154, 286), "right": (245, 286), "armor": (154, 183)}  # inven_pickup's slots (inventry.c)
        tx, ty = ox + targets[hand][0] + 45, oy + targets[hand][1] + 30
        self.drag(sx, sy, tx, ty)
        self.close_inventory()
        flag = {"left": OBJECT_IN_LEFT_HAND, "right": OBJECT_IN_RIGHT_HAND, "armor": OBJECT_WORN}[hand]
        ok = any(p == pid and self.mem.u32(item + Obj.FLAGS) & flag for item, p, _ in self.items())
        return self._end(ok, "equipped" if ok else "not in the hand", {"slot": slot}, "equip")

    def unequip(self, hand: str = "left") -> Outcome:
        """Drag whatever is in a hand slot (or the armor slot) back to the inventory list; confirmed by nothing
        carrying that slot's flag. Scripts that check a hand slot (the Cathedral's nightkin) see an empty hand only
        so: a holstered gun still sits in its slot."""
        flag = {"left": OBJECT_IN_LEFT_HAND, "right": OBJECT_IN_RIGHT_HAND, "armor": OBJECT_WORN}[hand]
        held = lambda: [p for item, p, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & flag]
        self.log.emit("action_start", action="unequip", hand=hand, held=held())
        if not held():
            return self._end(True, "empty already", {}, "unequip")
        origin = self.open_inventory()
        if origin is None:
            return self._end(False, "no inventory screen", {}, "unequip")
        ox, oy = origin
        slots = {"left": (154, 286), "right": (245, 286), "armor": (154, 183)}  # inven_pickup's slots (inventry.c)
        sx, sy = ox + slots[hand][0] + 45, oy + slots[hand][1] + 30
        self.drag(sx, sy, ox + 46 + 32, oy + 35 + 24)  # onto the list
        self.close_inventory()
        ok = not held()
        return self._end(ok, "emptied" if ok else "still held", {}, "unequip")

    def use_on_self(self, pid: int) -> Outcome:
        """Use an item (a stimpak) from the inventory screen. In Fallout 1 a drop on the player's picture only fills
        containers (inven_pickup); use goes through the item's action menu (inven_action_cursor): arrow cursor (a
        right click), hold the left button on the item until the menu opens, move down one entry (Look, Use, ...),
        release. Confirmed by the count going down, or HP going up. In combat the inventory screen itself costs 4 AP."""
        self.log.emit("action_start", action="use_on_self", pid=pid)
        hp0 = self.snap().dude.hp
        count0 = sum(q for _, p, q in self.items() if p == pid)
        why = self.item_menu(pid, ITEM_MENU_USE)
        if why:
            return self._end(False, why, {}, "use_on_self")
        time.sleep(0.6)
        self.close_inventory()
        hp1 = self.snap().dude.hp
        count1 = sum(q for _, p, q in self.items() if p == pid)
        # used up, or HP up: the First Aid Kit stays (HP 17 -> 22 in Vault 15 was logged "not used")
        ok = count1 < count0 or hp1 > hp0
        return self._end(ok, "used" if ok else "not used", {"hp": [hp0, hp1], "count": [count0, count1]}, "use_on_self")

    def item_menu(self, pid: int, entry: int) -> str | None:
        """An entry of the item's action menu in the inventory screen (inven_action_cursor): 1 Use, 2 Drop for an
        item that can be used (Look, Use, Drop, Cancel). The screen is left open for the caller (who closes it).
        None when the entry was chosen, else why not."""
        origin = self.open_inventory()
        slot = self._list_slot(pid)
        if origin is None or slot is None:
            self.close_inventory()
            return "item not in the list"
        shown = self._scroll_list(slot)  # looted stimpaks come last (slot 19, MBVATS12's cell)
        if shown is None:
            self.close_inventory()
            return "list not scrolled"
        ox, oy = origin
        sx, sy = ox + 44 + 32, oy + 35 + 48 * shown + 24
        self.point(sx, sy)
        for _ in range(2):
            if self.mem.glob("immode") == INVENTORY_CURSOR_ARROW:
                break
            self.click(right=True)
            win32.wait_for(lambda: self.mem.glob("immode") == INVENTORY_CURSOR_ARROW, 1.0, 0.05)
        if self.mem.glob("immode") != INVENTORY_CURSOR_ARROW:
            self.close_inventory()
            return f"no arrow cursor in the inventory (immode {self.mem.glob('immode')})"
        r = win32.client_rect(self.hwnd)
        self._front()
        with clock.held(self.pid), inputs.acting(self.guard):
            inputs.mouse_button(down=True)
            time.sleep(0.8)  # held past the button repeat time: the action menu opens under the cursor
            for k in range(1, 4 * entry + 1):
                inputs.set_cursor(r.left + sx, r.top + sy + 4 * k)  # 16 px down an entry ("Use" is one down)
                time.sleep(0.05)
            time.sleep(0.3)
            inputs.mouse_button(down=False)
        return None

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
        if self.mem.u32(LAST_OBJECT) in targets:  # a rest names only a new object: start from another
            self.hovering(*geometry.tile_center(s.dude.tile, s.camera), dy=-24)
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

    def _use_in_mode(
        self, tile: int, arm, done, timeout_s: float, aims, action: str, detail: dict, targets=frozenset()
    ) -> Outcome:
        """Put the cursor in a use mode with `arm()`, click the object on `tile` (the player walks up to it and acts)
        until done() holds; the message box's new lines go into the outcome. A far object is walked up to first:
        the Glow's beam lay beyond where the camera would scroll (live), and it keeps the aim on view."""
        before = state.messages(self.mem, 3)
        s = self.snap()
        if geometry.distance(s.dude.tile, tile) > 3:
            obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
            near = {t for r in (1, 2) for t in geometry.ring(tile, r) if t not in obs.blocked}
            nav.go_to(self, near or {tile}, 80)
        self.center_view()
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 48):
            nav.scroll_towards(self, tile)
            s = self.snap()
        cx, cy = geometry.tile_center(tile, s.camera)
        end = time.monotonic() + timeout_s

        def said() -> bool:  # a new line that is not the place's own (the Glow's doses came in the rope's wait)
            new = [m for m in state.messages(self.mem, 3) if m not in before]
            return any(not any(a in m.lower() for a in AMBIENT) for m in new)

        offsets = list(aims or ((0, -6), (0, 0), (0, -12), (5, -4), (-5, -4), (0, -20)))
        if targets:  # a point on the target itself, found in arrow mode, then the use cursor clicks there
            spot = self._aim_at(targets, [(cx + dx, cy + dy) for dx, dy in offsets], (cx, cy))
            if spot is not None:
                offsets = [(spot[0] - cx, spot[1] - cy)] * 3
            detail = detail | {"aimed": spot is not None}
            time.sleep(LOOK_SETTLE_S)
            before = state.messages(self.mem, 3)  # the rests' looks ("The door is locked.") are not the result
        for dx, dy in offsets:
            if not arm():
                return self._end(False, "no use cursor", detail, action)
            self.point(cx + dx, cy + dy)
            start = self.snap().dude.tile
            self.click()
            if win32.wait_for(lambda start=start: done() or said() or self.snap().dude.tile != start, 2.0, 0.1):
                win32.wait_for(lambda: done() or said(), max(1.0, end - time.monotonic()), 0.1)
            if done() or said() or time.monotonic() > end:
                break
        time.sleep(0.5)
        ok = bool(done())
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

    def use_item_on(
        self, pid: int, tile: int, done, timeout_s: float = 25.0, aims=None, targets=frozenset()
    ) -> Outcome:
        """An item used on the object on `tile` (a rope on a hole, a radio on a console): the item into the right hand,
        that hand active, and the item button, which for an item made to be used on things gives the use cursor
        (intface.c intface_use_item); then a click on the object. The weapon stays in the left hand."""
        detail = {"pid": pid, "tile": tile}
        self.log.emit("action_start", action="use_item_on", **detail)
        in_right = any(p == pid and self.mem.u32(i + Obj.FLAGS) & OBJECT_IN_RIGHT_HAND for i, p, _ in self.items())
        # an item left there by the last use (a Glow key) is swapped by the drop; if the game keeps it, the hand is
        # emptied first
        if (
            not in_right
            and not self.equip(pid, "right").ok
            and not (self.unequip("right").ok and self.equip(pid, "right").ok)
        ):
            return self._end(False, "not in the right hand", detail, "use_item_on")
        for _ in range(2):
            if self.mem.glob("itemCurrentItem") == 1:
                break
            session.press("b")
            time.sleep(0.5)  # the swap's animation (holster)

        def arm() -> bool:
            if self.mem.glob("gmouse_3d_current_mode") != MOUSE_USE_CROSSHAIR:
                self.point(*self.item_button())
                self.click()
            return win32.wait_for(lambda: self.mem.glob("gmouse_3d_current_mode") == MOUSE_USE_CROSSHAIR, 1.5, 0.05)

        return self._use_in_mode(tile, arm, done, timeout_s, aims, "use_item_on", detail, targets)

    def item_action(self) -> int:
        """The interface bar's action for the active hand (intface.c): 1 single, 2 aimed, 5 reload, 0 use."""
        hand = self.mem.glob("itemCurrentItem")
        return self.mem.i32(em.GLOBALS["itemButtonItems"].address + ITEM_STATE_SIZE * hand + ITEM_STATE_ACTION)

    def _set_item_action(self, action: int) -> bool:
        """Cycle the item button's action with N until it is `action` (reload is offered only when not full)."""
        for _ in range(7):
            if self.item_action() == action:
                return True
            session.press("n")
            time.sleep(0.2)
        return self.item_action() == action

    def reload(self) -> Outcome:
        """Reload the weapon in the active hand: N until the item button says reload, click it, back to single shot.
        Only with ammunition that fits (weapons.fits: the caliber, and the same kind while not empty). Confirmed by
        the loaded count. In combat it costs 2 AP."""
        held = self.weapon()
        if held is None or not weapons.uses_ammo(held.pid):
            return self._end(False, "no gun", {}, "reload")
        before = held.loaded
        if not any(weapons.fits(held.pid, p, before, held.ammo_pid) for _, p, _ in self.items()):
            return self._end(False, "no ammo", {"loaded": before}, "reload")
        self.log.emit("action_start", action="reload", loaded=before)
        if not self._set_item_action(ITEM_ACTION_RELOAD):
            self._set_item_action(ITEM_ACTION_PRIMARY)
            return self._end(False, "no reload action on the item button", {"loaded": before}, "reload")
        self.point(*self.item_button())
        self.click()
        win32.wait_for(lambda: (self.weapon() or held).loaded > before, 3, 0.1)
        after = (self.weapon() or held).loaded
        self._set_item_action(ITEM_ACTION_PRIMARY)
        return self._end(
            after > before, "reloaded" if after > before else "not reloaded", {"loaded": [before, after]}, "reload"
        )

    # --- weapons ------------------------------------------------------------------------------------------------

    def rules(self) -> weapons.Rules:
        """What the character brings to an attack, from memory: ST, the Fast Shot trait, the perks that change AP."""
        perks = chargen.ints(self.mem, "perk_lev", 64)
        return weapons.Rules(
            strength=chargen.special(self.mem)[0],
            fast_shot=weapons.TRAIT_FAST_SHOT in chargen.ints(self.mem, "pc_trait", 2),
            bonus_hth_attacks=perks[weapons.PERK_BONUS_HTH_ATTACKS] > 0,
            bonus_rate_of_fire=perks[weapons.PERK_BONUS_RATE_OF_FIRE] > 0,
            heave_ho=perks[weapons.PERK_HEAVE_HO],
        )

    def skills(self) -> dict[str, int]:
        return chargen.skill_values(self.mem)

    def carried_weapons(self) -> list[weapons.Carried]:
        """Every weapon the player has (in hand or not), with its rounds and the carried rounds that fit it: an ammo
        entry is a stack of boxes whose top box holds the object's count, the others are full."""
        entries = self.items()
        boxes = []
        for item, pid, qty in entries:
            p = knowledge.proto(pid)
            if p and "ammo" in p and qty > 0:
                boxes.append((pid, (qty - 1) * p["ammo"]["quantity"] + self.mem.i32(item + Obj.ITEM_AMMO)))
        out = []
        for item, pid, _qty in entries:
            if weapons.weapon(pid) is None:
                continue
            loaded, loaded_pid = self.mem.i32(item + Obj.ITEM_AMMO), self.mem.i32(item + Obj.ITEM_AMMO_PID)
            spare = sum(n for a, n in boxes if weapons.fits(pid, a, loaded, loaded_pid))
            out.append(weapons.Carried(pid, loaded, loaded_pid, spare))
        return out

    def weapon_options(self) -> list[weapons.Option]:
        return weapons.choose(self.carried_weapons(), self.skills(), self.rules(), self.max_ap)

    def ready_weapon(self) -> Outcome:
        """The best way to fight (weapons.choose) in the left hand, which is made the active one; fists mean nothing
        to do. Outside combat: the inventory screen costs AP in a fight."""
        options = self.weapon_options()
        best = options[0] if options else None
        detail = {"best": best.name if best else None, "score": round(best.score, 1) if best else 0}
        self.log.emit("action_start", action="ready_weapon", options=[(o.name, round(o.score, 1)) for o in options])
        if best is None or best.pid is None:
            return self._end(True, "fists are best", detail, "ready_weapon")
        left = next((p for item, p, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & OBJECT_IN_LEFT_HAND), None)
        if left != best.pid and not self.equip(best.pid, "left").ok:
            return self._end(False, "could not equip", detail, "ready_weapon")
        self.holster(False)  # the weapon's hand active
        held = self.weapon()
        ok = held is not None and held.pid == best.pid
        return self._end(ok, "ready" if ok else "not in the active hand", detail, "ready_weapon")

    def attack_cost(self, held: Held | None) -> int:
        """AP of an attack with what is in the active hand, in the item button's mode (primary, aimed ...)."""
        action = self.item_action()
        secondary, aimed = action in weapons.SECONDARY_ACTIONS, action in weapons.AIMED_ACTIONS
        return weapons.ap_cost(held.pid if held else None, self.rules(), secondary and bool(held), aimed)

    def attack_reach(self, held: Held | None) -> int:
        secondary = self.item_action() in weapons.SECONDARY_ACTIONS and bool(held)
        return weapons.reach(held.pid if held else None, self.rules(), secondary)

    # --- time -----------------------------------------------------------------------------------------------------

    def rest(self, how: str = "until morning", timeout_s: float = 90) -> Outcome:
        """Pass time with the Pip-Boy's alarm clock (pipboy.c): P, the clock button (code 504), then the rest line
        (codes from 510: 510 + the duration's index), wait until the game time stops moving, close. Refused near
        enemies ("You cannot rest at this location!")."""
        t0 = self.snap().game_time
        self.log.emit("action_start", action="rest", how=how, game_time=t0)
        if self.in_combat():  # the Pip-Boy does not open in combat; its refusal box stayed up in the caves (16:3x)
            return self._end(False, "in combat", {}, "rest")
        session.press("p")
        if not win32.wait_for(lambda: self.snap().screen == "pipboy", 4, 0.1):
            for _ in range(3):  # a box said why not (a message window over the map): it takes Enter
                if self.snap().screen in ("map", "pipboy"):
                    break
                session.press("enter")
                win32.wait_for(lambda: self.snap().screen in ("map", "pipboy"), 1.5, 0.1)
            if self.snap().screen == "pipboy":
                session.press("esc")
            return self._end(False, "no Pip-Boy", {"screen": self.snap().screen}, "rest")
        for code in (504, 510 + REST[how]):
            # its buttons are there once the screen is drawn (the clock's rest lines after the clock's button)
            win32.wait_for(lambda code=code: ui.find(self.mem, self.mem.glob("pip_win"), code) is not None, 1.5, 0.05)
            b = ui.find(self.mem, self.mem.glob("pip_win"), code)
            if b is None:
                for _ in range(3):  # a refusal's message box takes the first Escape, the Pip-Boy the next (live)
                    session.press("esc")
                    if win32.wait_for(lambda: self.snap().screen == "map", 1.5, 0.1):
                        break
                return self._end(False, f"no button {code}", {}, "rest")
            session.click(*b.center)
        time.sleep(0.3)
        last, still = self.snap().game_time, 0
        end = time.monotonic() + timeout_s
        with clock.fast(self.pid):
            while time.monotonic() < end and still < 4:  # the clock runs; done when the game time holds for ~1 s
                time.sleep(0.25)
                now = self.snap().game_time
                still = still + 1 if now == last else 0
                last = now
        # the Pip-Boy may close by itself (a 24 h rest "until healed" did): Escape on the map opens the options menu,
        # which no F6 gets past (two saves failed, the watchdog loaded the checkpoint: a full run at Full HD)
        for _ in range(2):
            if self.snap().screen != "pipboy":
                break
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen != "pipboy", 4, 0.1)
        s = self.snap()
        hours = (s.game_time - t0) / 36000
        return self._end(s.game_time > t0, f"rested {hours:.1f} h", {"game_time": s.game_time, "hp": s.dude.hp}, "rest")

    # --- saving and loading -------------------------------------------------------------------------------------

    def skip_cinema(self) -> bool:
        """Space while a movie, a slide of the ending or the credits run; True when it pressed (then the screen had up
        to 2 s to change, so the next look sees what came after; the slides keep one name, 0.7 s each: the vats' ending
        took eight Spaces). With SKIP_CINEMA off it waits them out instead, True once they are over."""
        screen = self.snap().screen
        if not cinema(screen):
            return False
        if not SKIP_CINEMA:  # played whole; True once it is over, so the callers' loops look again as after a skip
            self.log.emit("cinema", screen=screen)
            win32.wait_for(lambda: not self._in_cinema(), CINEMA_LIMIT_S, 0.5)
            return True
        self.log.emit("skip", screen=screen)
        session.press(CINEMA_KEY)
        win32.wait_for(lambda: self.snap().screen != screen, 0.7 if screen == "endgame" else 2, 0.1)
        return True

    def _in_cinema(self) -> bool:
        try:
            return cinema(self.snap().screen)
        except ReadError:  # a map loading between the slides and the vault
            return True

    def to_map(self, tries: int = 3) -> bool:
        """Close a screen left open over the map (Escape closes each of these): F6 does nothing under the options
        menu (a full run at Full HD). Escape is pressed only while such a screen is up: on the map it would
        open the options menu itself."""
        for _ in range(tries):
            screen = self.snap().screen
            if screen == "map":
                return True
            if screen in ("barter", "dialogue"):
                dialogue.close(self.mem)
                continue
            if screen not in CLOSABLE:
                return False
            session.press("esc")
            win32.wait_for(lambda screen=screen: self.snap().screen != screen, 3, 0.1)
        return self.snap().screen == "map"

    def quicksave(self, slot: int = 1, description: str = "agent") -> Outcome:
        """F6. The first time in a session the game asks for a slot and a description; later F6s save straight away."""
        save = instance.INSTANCE_DIR / "DATA" / "SAVEGAME" / f"SLOT{slot:02d}" / "SAVE.DAT"
        before = save.stat().st_mtime if save.exists() else 0.0
        self.to_map()
        s = self.snap()
        tile = s.dude.tile
        self.log.emit("action_start", action="quicksave", slot=slot, tile=tile)
        if s.screen == "worldmap":  # F6 saves nothing there: two tries cost 69 s after the lair (a full run)
            return self._end(False, "on the world map", {"tile": tile}, "quicksave")
        written = lambda: save.exists() and save.stat().st_mtime > before
        for _ in range(
            3
        ):  # an F6 during an animation is lost: a full run's checkpoints waited 27 s for such, ten times
            session.press("f6")
            if win32.wait_for(lambda: self.snap().screen == "loadsave" or written(), 3, 0.1):
                break
        if self.snap().screen == "loadsave":
            for _ in range(slot - 1):
                session.press("down")
            session.press("enter")  # DONE: a used slot asks to overwrite first, then the description box
            time.sleep(0.8)
            if self._top_box() == OVERWRITE_BOX:
                session.press("y")
                time.sleep(0.8)
            if self._top_box() != DESCRIPTION_BOX:
                return self._end(False, f"no description box ({self._top_box()})", {"tile": tile}, "quicksave")
            for _ in range(30):  # the box keeps the slot's old description
                session.press("backspace")
            session.type_text("".join(ch for ch in description if ch.isalnum() or ch == " ")[:28])  # typeable only
            session.press("enter")
        saved = win32.wait_for(written, 15, 0.2)
        back = win32.wait_for(lambda: self.snap().screen == "map", 10, 0.2)
        return self._end(saved and back, "saved" if saved else "no save file written", {"tile": tile}, "quicksave")

    def _top_box(self) -> tuple[int, int, int]:
        """The topmost window's shape: (flags, width, height); its place depends on the resolution."""
        _wid, flags, _x, _y, w, h = state.live_windows(self.mem)[-1]
        return (flags & 0xFF, w, h)

    def item_button(self) -> tuple[int, int]:
        """The active item's button on the interface bar: at 1920x1080 the 800-wide bar sits at (560, 980) and the
        button at (987..1174, 1006..1072) (measured); found by its right click's code, else 640x480's."""
        bar = self.mem.glob("interfaceWindow")
        button = next((b for b in ui.buttons(self.mem, bar) if b.codes[5] == ITEM_BUTTON_CODE), None)
        return button.center if button else ITEM_BUTTON

    def quickload(self, expect_tile: int | None = None) -> Outcome:
        """F7 opens the load screen (it loads nothing by itself: a hunt that waited for the map after F7 alone stood
        there until a person saw it), Enter loads the slot under its cursor, the one last saved or
        loaded (slot 1 for the routes; 0.3 s live in the caves). Done when the map is back (at `expect_tile`, if
        given). Fails fast without the load screen, so the caller can go by the main menu."""
        self.log.emit("action_start", action="quickload", expect_tile=expect_tile)
        session.press("f7")
        if not win32.wait_for(lambda: self.snap().screen == "loadsave", 3, 0.1):
            return self._end(False, "no load screen", {}, "quickload")
        session.press("enter")

        def loaded() -> bool:
            s = self.snap()
            return s.screen == "map" and s.dude is not None and (expect_tile is None or s.dude.tile == expect_tile)

        ok = win32.wait_for(loaded, 15, 0.25)
        if ok:
            watchdog.clear()
        s = self.snap()
        tile = s.dude.tile if s.dude else None
        return self._end(ok, "loaded" if ok else f"not back on the map ({s.screen})", {"tile": tile}, "quickload")

    # --- combat --------------------------------------------------------------------------------------------------

    def in_combat(self) -> bool:
        return bool(self.mem.glob("combat_state") & COMBAT_ACTIVE)

    def my_turn(self) -> bool:
        dude = self.mem.u32(em.GLOBALS["obj_dude"].address)
        return self.in_combat() and self.mem.u32(COMBAT_TURN_OBJ) == dude and self.mem.glob("combat_turn_running") == 0

    def enemies(self, only: tuple[str, ...] | None = None) -> list[state.Critter]:
        """Critters to fight: wild kinds (by name) or anyone who hit the player. Never other teams by team alone:
        townsfolk are another team and must not be shot."""
        dude = self.snap().dude
        out = []
        for c in state.critters(self.mem):
            if c.address == dude.address or c.dead or c.hp <= 0:
                continue
            if c.team == dude.team or c.elevation != dude.elevation:  # companions; other floors of the map
                continue
            name = knowledge.proto_name(c.pid)
            if only is not None:  # a fight with named opponents only (Garl's fistfight among neutral raiders)
                # "script:NAME" picks by the critter's script: the Raiders' Gwen and Alya are "Citizen" like the
                # plain raiders, and must be left for the fight in Garl's room (the raiders plan)
                script = scripts.script_of(self.mem, c.address) if any(k.startswith("script:") for k in only) else ""
                if any(k[7:] == script if k.startswith("script:") else k in name for k in only):
                    out.append(c)
                continue
            who_hit = self.mem.u32(c.address + Obj.CRITTER_WHO_HIT_ME)
            if any(k in name for k in HOSTILE_KINDS) or name in HOSTILE_EXACT or who_hit == dude.address:
                out.append(c)
        return out

    def weapon(self) -> Held | None:
        """The weapon in the active hand (itemCurrentItem: 0 left, 1 right), if any: any weapon prototype. With the
        other, empty hand active the player punches: that is how a weapon is holstered in Fallout 1."""
        hand_flag = OBJECT_IN_LEFT_HAND if self.mem.glob("itemCurrentItem") == 0 else OBJECT_IN_RIGHT_HAND
        for item, pid, _ in self.items():
            if self.mem.u32(item + Obj.FLAGS) & hand_flag and weapons.weapon(pid) is not None:
                return Held(pid, self.mem.i32(item + Obj.ITEM_AMMO), self.mem.i32(item + Obj.ITEM_AMMO_PID))
        return None

    def active_item(self) -> int | None:
        """The pid of whatever the active hand holds (itemCurrentItem: 0 left, 1 right), a weapon or not."""
        flag = OBJECT_IN_LEFT_HAND if self.mem.glob("itemCurrentItem") == 0 else OBJECT_IN_RIGHT_HAND
        return next((pid for item, pid, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & flag), None)

    def holster(self, on: bool = True) -> bool:
        """Put the gun away (make the empty hand active) or draw it again: the B key swaps the active hand
        (game.c). Raiders attack a visitor with a weapon out."""
        for _ in range(2):
            if (self.weapon() is None) == on:
                return True
            session.press("b")
            time.sleep(0.6)  # the swap's animation: a key pressed during it is lost (a wait on the hand flag was not
            # enough: "not in the active hand" after a readying)
        return (self.weapon() is None) == on

    def attack(self, target: state.Critter) -> Outcome:
        """One attack with the active item on a target (adjacent for fists, in range for a gun); done when over."""
        s = self.snap()
        pre = {"target": target.address, "target_tile": target.tile, "target_hp": target.hp, "ap": s.dude.ap}
        self.log.emit("action_start", action="attack", **pre)
        if not self.my_turn():
            return self._end(False, "not my turn", pre, "attack")
        held = self.active_item()
        if held is not None and weapons.weapon(held) is None:
            # an item used on something stays in its hand (the COC badge after the red door): its button gives the
            # use cursor, not the crosshair (live, a road fight lost that way); B swaps the active hand
            session.press("b")
            armed = lambda: (h := self.active_item()) is None or weapons.weapon(h) is not None
            win32.wait_for(armed, 1.0, 0.05)
        if s.mouse_mode != MOUSE_CROSSHAIR:
            self.point(*self.item_button())
            self.click()
            if not win32.wait_for(lambda: self.mem.glob("gmouse_3d_current_mode") == MOUSE_CROSSHAIR, 1.5, 0.05):
                return self._end(False, "no crosshair after the item button", pre, "attack")
        cx, cy = geometry.tile_center(target.tile, s.camera)
        line = self.mem.glob("disp_start")
        blocked = lambda: "aim is blocked" in " ".join(state.messages_since(self.mem, line)).lower()
        with clock.fast(self.pid):  # the swing's animation (the clicks themselves go in at 1x: clock.held)
            for dx, dy in AIM_OFFSETS:  # the sprite stands above its hex; try a few points on it
                self.point(cx + dx, cy + dy)
                self.click()
                if win32.wait_for(lambda: self.snap().dude.ap < s.dude.ap or blocked(), 1.0, 0.05) and not blocked():
                    break
                if blocked():  # COMBAT.MSG 104: no other point on the target helps (a full run's rats, 34 times)
                    return self._end(False, "aim blocked", pre, "attack")
            else:
                return self._end(False, "no swing: the clicks missed the target", pre, "attack")
            win32.wait_for(lambda: self.mem.glob("combat_turn_running") == 0, 10, 0.05)
        after = state.read_critter(self.mem, target.address)
        detail = pre | {
            "aim": [dx, dy],
            "target_hp_after": after.hp,
            "killed": after.dead,
            "ap_after": self.snap().dude.ap,
        }
        return self._end(True, "swung", detail, "attack")

    def end_turn(self) -> Outcome:
        self.log.emit("action_start", action="end_turn", ap=self.snap().dude.ap)
        session.press("space")
        with clock.fast(self.pid):
            gone = win32.wait_for(lambda: not self.my_turn(), 3, 0.05)
        return self._end(gone, "turn passed" if gone else "still my turn", {}, "end_turn")

    def fight(
        self,
        max_rounds: int = 60,
        min_hp: int = 8,
        only: tuple[str, ...] | None = None,
        spare: frozenset[str] = frozenset(),
    ) -> Outcome:
        """Fight until combat is over: shoot the nearest foe in range with a gun (or punch an adjacent one), heal
        with a stimpak below 45 % HP, end the turn when nothing more can be done. `spare`: scripts of critters that
        must not be hit: a foe with one of them beyond it on the line of fire is not shot (a missed shot flies on
        and hits the first critter past its target with no roll, CE compute_attack: the Raiders' captive women)."""
        self.log.emit("action_start", action="fight")
        rounds, turn_of_enter, drew = 0, -1, False
        acted = 0  # the last round with a shot, a step or a heal: the turns since are a stall (foes out of reach)
        missed: dict[int, int] = {}  # target -> attacks whose clicks did not land
        stuck_at: set[int] = set()  # targets with no way to close in on them (tried last)
        # targets whose shot was blocked in an earlier turn: tried after the others (the Hub's captors, the chain:
        # a Guard behind a door frame came first every turn, "aim blocked", while Vinnie in the doorway was
        # never shot at; 60 turns)
        blocked_before: set[int] = set()
        for _ in range(max_rounds * 4):
            if rounds >= max_rounds:
                break
            now = self.snap()
            if now.screen == "main_menu" or now.dude is None or now.dude.hp <= 0:
                return self._end(False, "died", {"screen": now.screen}, "fight")
            if not self.in_combat():
                return self._end(True, "combat over", {"hp": now.dude.hp}, "fight")
            # my turn, or the end of combat (after the last kill the foes' turns ran out and the wait lasted 45 s)
            with clock.fast(self.pid):  # the foes' turns
                came = win32.wait_for(lambda: self.my_turn() or not self.in_combat(), 45, 0.1)
            if not came:
                return self._end(False, "my turn did not come", {}, "fight")
            if not self.in_combat():
                continue
            s = self.snap()
            low = s.dude.hp < 0.5 * self.max_hp and s.dude.ap >= 4 and self._count(PID_STIMPAK) > 0
            if low and self.use_on_self(PID_STIMPAK).ok:  # heal first: 4 AP for the screen still leaves a shot
                acted = rounds
                continue
            if s.dude.hp < min_hp:
                return self._end(False, "hp low; stopping to keep the character alive", {"hp": s.dude.hp}, "fight")
            held = self.weapon()
            if held is None and not drew:  # holstered for a town: B brings the weapon from the other hand (once)
                drew = True
                if self.holster(False):
                    continue
                # neither hand holds a weapon (the bug and the recorder for Gizmo, Junktown: every attack
                # was "no crosshair after the item button" while a guard shot HP 31 to 4): the best one, from the
                # inventory (4 AP)
                if self.active_item() is not None and s.dude.ap >= INVENTORY_AP and self.ready_weapon().ok:
                    continue
            if held and weapons.uses_ammo(held.pid) and held.loaded == 0 and s.dude.ap >= weapons.RELOAD_AP:
                loaded = self.reload()
                if loaded.ok:
                    continue
                if loaded.reason == "no ammo":  # another weapon with rounds (the inventory costs AP), else fists
                    if s.dude.ap >= INVENTORY_AP and self.ready_weapon().ok and self.weapon() not in (None, held):
                        continue
                    if self.holster(True):  # an empty gun cannot even punch
                        held = None
            cost, reach = self.attack_cost(held), self.attack_reach(held)
            # combat.c obj_dist: a big body (multihex) is one hex nearer than its tile
            here = s.dude.tile
            gaps = {e.address: geometry.distance(e.tile, here) - (1 if e.multihex else 0) for e in self.enemies(only)}
            foes = sorted(self.enemies(only), key=lambda e: gaps.get(e.address, 99))
            near = [e for e in foes if gaps.get(e.address, 99) <= reach]
            if near and not geometry.on_view(near[0].tile, s.camera, 24):
                nav.scroll_towards(self, near[0].tile)  # the camera does not follow the player: bring it over
                s = self.snap()
            in_reach = [e for e in near if geometry.on_view(e.tile, s.camera, 16) and missed.get(e.address, 0) < 2]
            if spare and held and weapons.attack_type(held.pid) == "ranged":
                kept = [e for e in in_reach if not self._spared_behind(e, spare, s)]
                if len(kept) < len(in_reach):
                    self.log.emit("tactic", move="spare", held_back=len(in_reach) - len(kept))
                in_reach = kept
            # the weakest first: one foe less hits back sooner (Vault 12, a full run: the Agent turned to the Mad
            # Glowing One's 60 HP while the guard it fought had 9 left)
            in_reach.sort(key=lambda e: (e.address in blocked_before, e.hp, gaps.get(e.address, 99)))
            if in_reach and s.dude.ap >= cost and held and weapons.attack_type(held.pid) == "ranged":
                # the tactics: a gun gains 4 a hex closer (combat.c); against a foe without a gun of its own, close
                # in before a poor shot, and let a hopeless one go (the AP left over is armour class on its turn).
                # The weakest foe first only among those worth a shot: in Vault 15 the weakest was a rat
                # 16 hexes off with no way to it (-5 %), held fire at for 12 turns while another bit HP 38 to 4.
                chances = {e.address: odds.shot_chance(self, e, held.pid, gaps.get(e.address, 99)) for e in in_reach}
                in_reach.sort(
                    key=lambda e: (
                        e.address in blocked_before,
                        e.address in stuck_at,
                        chances[e.address] < HOPELESS_SHOT,
                        e.hp,
                        gaps.get(e.address, 99),
                    )
                )
                target, gap = in_reach[0], gaps.get(in_reach[0].address, 99)
                chance = chances[target.address]
                if chance < GOOD_SHOT and gap > 1 and not odds.armed_at_range(self.mem, target):
                    closer = min(gap - 1, -(-(GOOD_SHOT - chance) // 4), s.dude.ap - cost)
                    self.log.emit("tactic", move="close in", chance=chance, gap=gap, hexes=closer)
                    if closer > 0:
                        moved = self.combat_move_towards(target.tile, closer)
                        if moved.ok:
                            acted = rounds
                            continue
                        if moved.reason == "no path":
                            stuck_at.add(target.address)
                    # Vault 15's rubble, the 1x chain: rats 12-16 hexes off with no way to or from them, 11 % at
                    # best, and Enter could not end the fight (they still saw the Agent): 60 turns held, 545 s. Two
                    # turns of that and a poor shot is better than none; nothing to shoot at, and the Agent steps
                    # back out of their sight.
                    stalled = rounds - acted
                    if chance < HOPELESS_SHOT and stalled >= STALL_TURNS and chance >= LONG_SHOT:
                        self.log.emit("tactic", move="long shot", chance=chance, gap=gap, stalled=stalled)
                    elif chance < HOPELESS_SHOT:
                        self.log.emit("tactic", move="hold fire", chance=chance, gap=gap)
                        # a foe with no way to the player keeps its AP, and combat.c combat_end lets Enter end the
                        # fight when every foe wants to stop (combatai_want_to_stop: full AP, or out of perception).
                        # Vault 15: a rat with no path, 120 turns of holding fire at 0-3 %.
                        if turn_of_enter != rounds:
                            turn_of_enter = rounds
                            session.press("enter")
                            if win32.wait_for(lambda: not self.in_combat(), 3, 0.1):
                                continue
                        if stalled > STALL_TURNS:  # a step back is no progress: the next stalled turn steps on
                            self.retreat(foes, s.dude.ap)
                        self.end_turn()
                        rounds += 1
                        continue
            if in_reach and s.dude.ap >= cost:
                out = self.attack(in_reach[0])
                if out.ok:
                    acted = rounds
                    continue
                # no line of fire, or hidden; the game's "Your aim is blocked." says so at once: move now
                missed[in_reach[0].address] = (
                    2 if out.reason == "aim blocked" else missed.get(in_reach[0].address, 0) + 1
                )
                if out.reason == "aim blocked":
                    blocked_before.add(in_reach[0].address)
                    if len(in_reach) > 1:
                        continue  # the next foe this same turn
            unhittable = [e for e in near if missed.get(e.address, 0) >= 2]
            if unhittable and s.dude.ap > cost and self.combat_move_towards(unhittable[0].tile, 3).ok:
                missed[unhittable[0].address] = 0  # from closer the clicks may land (Vault 12: a glowing one, 7 HP)
                acted = rounds
                continue
            if not in_reach and s.dude.ap == self.max_ap and turn_of_enter != rounds:
                turn_of_enter = rounds  # once a turn: Enter ends combat when no foe is close (combat_should_end)
                session.press("enter")
                if win32.wait_for(lambda: not self.in_combat(), 3, 0.1):
                    continue
            far = foes and not in_reach and s.dude.ap > cost  # foes too far: close in, keeping AP for a shot
            if far and self.combat_move_towards(foes[0].tile, s.dude.ap - cost).ok:
                acted = rounds
                continue
            if far and rounds - acted > STALL_TURNS and s.dude.ap == self.max_ap:
                self.retreat(foes, s.dude.ap)
            self.end_turn()
            rounds += 1
            # a new turn tries every foe again: a guard in a doorway marked "aim blocked" once was never shot at
            # again, and the fight walked into "no path" for turns (the Hub's captors, live)
            missed.clear()
        return self._end(False, "too many rounds", {"hp": self.snap().dude.hp}, "fight")

    def _spared_behind(self, target: state.Critter, spare: frozenset[str], s: state.Snapshot) -> bool:
        """A spared critter past `target` on the screen line from the player, within a hex of it and the weapon's
        reach (35 hexes for a rifle: measured in hex widths of 32 px)."""
        x0, y0 = geometry.tile_center(s.dude.tile, s.camera)
        x1, y1 = geometry.tile_center(target.tile, s.camera)
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        for c in state.critters(self.mem):
            if c.dead or c.elevation != s.elevation or scripts.script_of(self.mem, c.address) not in spare:
                continue
            cx, cy = geometry.tile_center(c.tile, s.camera)
            along = ((cx - x0) * dx + (cy - y0) * dy) / length
            off = abs((cx - x0) * dy - (cy - y0) * dx) / length
            if length - 8 < along < length + 35 * 32 and off < 28:
                return True
        return False

    def retreat(self, foes: list[state.Critter], max_hexes: int) -> Outcome:
        """In my combat turn: up to `max_hexes` away from foes that can neither be reached nor shot, to the hex on the
        view farthest from the nearest of them: out of their sight, Enter can end the fight (combatai_want_to_stop)."""
        s = self.snap()
        obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
        to = retreat_hex(s.dude.tile, [f.tile for f in foes], obs.blocked | set(obs.exits), max_hexes, s.camera)
        self.log.emit("tactic", move="retreat", to=to, foes=len(foes))
        if to is None:
            return Outcome(False, "nowhere farther", {})
        return self.combat_walk({to}, max_hexes)

    def combat_move_towards(self, tile: int, max_hexes: int) -> Outcome:
        """In my combat turn: walk up to `max_hexes` along our path towards `tile` (a click in move mode; each hex
        costs 1 AP). Done when the player stands still on the chosen hex or stops early."""
        s = self.snap()
        pre = {"from": s.dude.tile, "towards": tile, "max": max_hexes, "ap": s.dude.ap}
        self.log.emit("action_start", action="combat_move", **pre)
        refused: set[int] = set()  # hexes the game would not move to (a red X: taken, or out of reach)
        target, after = s.dude.tile, s
        big = any(c.tile == tile and c.multihex and not c.dead for c in state.critters(self.mem))
        beside = set(geometry.ring(tile, 2 if big else 1))  # a big body takes the ring round its tile itself
        for _ in range(3):
            obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
            blocked = obs.blocked | refused
            path = nav.astar(s.dude.tile, beside - blocked or {tile}, blocked)
            if not path or len(path) < 2:
                return self._end(False, "no path", pre | {"refused": sorted(refused)}, "combat_move")
            target = path[min(max_hexes, len(path) - 1)]
            if not geometry.on_view(target, s.camera, 24):
                nav.scroll_towards(self, target)
                s = self.snap()
            if not self.set_mouse_mode(0):
                return self._end(False, "no move cursor", pre, "combat_move")
            self.point(*geometry.tile_center(target, s.camera))
            self.click()
            with clock.fast(self.pid):
                win32.wait_for(lambda target=target: self.snap().dude.tile == target, 8, 0.1)
                still = lambda: self.mem.glob("combat_turn_running") == 0 and self._still(self.snap().dude.tile)
                win32.wait_for(still, 4)
            after = self.snap()
            if after.dude.tile != s.dude.tile:
                break
            refused.add(target)  # mole rats round the Agent: the hex beside them was taken
        moved = after.dude.tile != s.dude.tile
        detail = pre | {"to": target, "at": after.dude.tile, "ap_after": after.dude.ap, "refused": sorted(refused)}
        return self._end(moved, "moved" if moved else "did not move", detail, "combat_move")

    def combat_walk(self, goals: set[int], max_hexes: int) -> Outcome:
        """In my combat turn: up to `max_hexes` along our path onto one of `goals` (an exit grid: standing on it ends
        the combat and leaves the map, CE map_leave_map), to the farthest of those hexes on the view."""
        s = self.snap()
        pre = {"from": s.dude.tile, "goals": len(goals), "max": max_hexes, "ap": s.dude.ap}
        self.log.emit("action_start", action="combat_walk", **pre)
        obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
        path = nav.astar(s.dude.tile, goals, obs.passable_doors)
        if not path or len(path) < 2:
            return self._end(False, "no path", pre, "combat_walk")
        i = nav.next_step(path, s.camera, max_hexes=max_hexes, margin=8)
        if i is None:
            nav.scroll_towards(self, path[min(max_hexes, len(path) - 1)])
            s = self.snap()
            i = nav.next_step(path, s.camera, max_hexes=max_hexes, margin=8)
        if i is None or not self.set_mouse_mode(0):
            return self._end(False, "no step on the view", pre, "combat_walk")
        # the farthest step first; a hex the game will not walk to (its own path differs, or runs past a foe) takes
        # a nearer one: the same refused hex stood thirty turns of a flee in a row (a full run at Full HD)
        for k in sorted({i, max(1, i // 2), 1}, reverse=True):
            self.point(*geometry.tile_center(path[k], s.camera))
            self.click()
            if win32.wait_for(lambda: self.snap().dude.tile != s.dude.tile or self.snap().screen != "map", 1.5, 0.1):
                win32.wait_for(lambda k=k: self.snap().dude.tile == path[k] or self.snap().screen != "map", 8, 0.1)
                i = k
                break
        win32.wait_for(lambda: self.mem.glob("combat_turn_running") == 0, 4, 0.05)
        after = self.snap()
        moved = after.screen != "map" or (after.dude is not None and after.dude.tile != s.dude.tile)
        detail = pre | {"to": path[i], "at": after.dude.tile if after.dude else None}
        return self._end(moved, "moved" if moved else "did not move", detail, "combat_walk")

    def armed(self) -> bool:
        """A weapon that reaches past the next hex and has rounds to fire: fists and a knife did nothing to armoured
        road foes (26 HP each, 0 damage a punch)."""
        best = next(iter(self.weapon_options()), None)
        return best is not None and best.pid is not None and best.reach > 1 and bool(best.rounds)

    @property
    def max_ap(self) -> int:
        """Action points at the start of a turn: base plus bonus of stat 8 (the Agent's 10)."""
        base = em.GLOBALS["pc_proto"].address + PROTO_BASE_STATS
        return self.mem.i32(base + 4 * STAT_MAX_AP) + self.mem.i32(base + 4 * (STAT_COUNT + STAT_MAX_AP))

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

    def _count(self, pid: int) -> int:
        return sum(q for _, p, q in self.items() if p == pid)

    @property
    def max_hp(self) -> int:
        """The player's maximum HP: base plus bonus of stat 7 in the player's prototype (level-ups raise the base)."""
        base = em.GLOBALS["pc_proto"].address + PROTO_BASE_STATS
        return self.mem.i32(base + 4 * STAT_MAX_HP) + self.mem.i32(base + 4 * (STAT_COUNT + STAT_MAX_HP))

    def enemies_close(self, s: state.Snapshot, radius: int = 8) -> bool:
        close = set()
        frontier = {s.dude.tile}
        for _ in range(radius):
            frontier = {geometry.neighbour(t, r) for t in frontier for r in range(6)} - close
            close |= frontier
        return any(e.tile in close for e in self.enemies())


def walktest(n: int) -> int:
    pid = session.game_pid()
    if not pid:
        print("the game is not running (python -m f1.flows newgame first)")
        return 2
    run = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-walktest"
    log = EventLog(run / "events.jsonl")
    actor = Actor(pid, log)
    try:
        start = actor.snap()
        log.emit("run_start", test="walktest", n=n, map=start.map_name, dude=start.dude.tile)
        # Find reachable tiles near the start first; the test then walks among them, so it measures control.
        home = start.dude.tile
        candidates = [t for r in (2, 3) for t in geometry.ring(home, r) if geometry.on_view(t, start.camera, 24)]
        random.Random(1).shuffle(candidates)
        reachable = [home]
        for t in candidates[:10]:
            if actor.walk_to(t).ok:
                reachable.append(t)
            if actor.walk_to(home).ok is False:
                break
        log.emit("reachable", tiles=reachable)
        print(f"reachable near {home}: {reachable}")
        if len(reachable) < 3:
            print("too few reachable tiles to test")
            return 1
        rng = random.Random(2)
        results = []
        at = actor.snap().dude.tile
        for i in range(n):
            target = rng.choice([t for t in reachable if t != at])
            out = actor.walk_to(target)
            results.append(out.ok)
            at = actor.snap().dude.tile
            print(
                f"{i + 1:>2}. -> {target}: {'ok' if out.ok else 'FAIL'} {out.reason} {out.detail.get('seconds', '')}s"
            )
            if out.detail.get("combat"):
                print("combat started; stopping")
                break
        ok = sum(results)
        log.emit("run_end", test="walktest", ok=ok, of=len(results))
        print(f"=> {ok}/{len(results)} walk commands ended on their tile   (events: {run / 'events.jsonl'})")
        return 0 if ok == n else 1
    finally:
        actor.close()


def fight() -> int:
    pid = session.game_pid()
    run = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-fight"
    actor = Actor(pid, EventLog(run / "events.jsonl"))
    try:
        out = actor.fight()
        print(out, f"(events: {run / 'events.jsonl'})")
        return 0 if out.ok else 1
    finally:
        actor.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["walktest"]:
        sys.exit(walktest(int(args[1]) if len(args) > 1 else 20))
    if args[:1] == ["fight"]:
        sys.exit(fight())
    print(__doc__)
    sys.exit(2)
