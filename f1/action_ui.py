"""Rest, screen recovery and verified save/load actions for the public Actor."""

import time

from f1 import clock, dialogue, session, state, ui, watchdog, win32
from f1.action_shared import Outcome
from f1.memory import ReadError

REST = {"10 minutes": 0, "30 minutes": 1, "1 hour": 2, "2 hours": 3, "3 hours": 4, "4 hours": 5, "5 hours": 6,
        "6 hours": 7, "until morning": 8, "until noon": 9, "until evening": 10, "until midnight": 11,
        "until healed": 12}  # pipboy.c rest durations, in their order  # fmt: skip
# screens an Escape takes off the map (on the map itself an Escape opens the options menu)
CLOSABLE = ("options", "pipboy", "skilldex", "character", "inventory", "loot")
# Movies, the ending's slides and the credits are skipped (the bot is about the play, not the
# story; they took 13 of the 93 minutes of a full run). Space, not Escape: any key ends them, and Space does
# nothing on the map or in the main menu, where Escape opens the options menu or quits the game.
CINEMA_KEY = "space"
CINEMA_LIMIT_S = 1200  # a cinema left to play: the longest, the credits, ran nine minutes in the first full run


def cinema(screen: str) -> bool:
    """A movie (a 0x10 window from the screen's corner), the ending's slides, or the credits (a 0x14 window from the
    corner as big as the screen: 640x480 at 640x480, 1920x1080 at Full HD; measured in full runs)."""
    if screen == "endgame" or state.window_kind(screen) == "movie":
        return True
    m = state.WINDOW_NAME.fullmatch(screen)
    return m is not None and int(m[1], 16) == 0x14 and (int(m[2]), int(m[3])) == (0, 0) and int(m[4]) >= 640


MESSAGE_BOX = (0x14, 302, 127)  # the engine's dialog_out box: (flags, width, height), measured live (a refused rest)


class ScreenActions:
    """Screen actions using the Actor input, snapshots and outcome events."""

    def rest(self, how: str = "until morning", timeout_s: float = 90) -> Outcome:
        """Pass time with the Pip-Boy's alarm clock (pipboy.c): P, the clock button (code 504), then the rest line
        (codes from 510: 510 + the duration's index), wait until the game time stops moving, close. Where the game
        allows no rest (near enemies: V13ENT's cave, a worker and the instance alike) the clock's button answers with
        a message box (MESSAGE_BOX) instead of the rest lines: "refused here", told apart from a button not found."""
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
            there = lambda code=code: ui.find(self.mem, self.mem.glob("pip_win"), code) is not None
            win32.wait_for(lambda there=there: there() or self._box_up(), 1.5, 0.05)
            b = ui.find(self.mem, self.mem.glob("pip_win"), code)
            if b is None:
                why = "refused here" if self._box_up() else f"no button {code}"
                for _ in range(3):  # a refusal's message box takes the first Escape, the Pip-Boy the next (live)
                    session.press("esc")
                    if win32.wait_for(lambda: self.snap().screen == "map", 1.5, 0.1):
                        break
                return self._end(False, why, {"game_time": self.snap().game_time}, "rest")
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

    def _box_up(self) -> bool:
        """The engine's message box (dialog_out) is the topmost window."""
        windows = state.live_windows(self.mem)
        return bool(windows) and (windows[-1][1] & 0xFF, *windows[-1][4:]) == MESSAGE_BOX

    def skip_cinema(self) -> bool:
        """Space while a movie, a slide of the ending or the credits run; True when it pressed (then the screen had up
        to 2 s to change, so the next look sees what came after; the slides keep one name, 0.7 s each: the vats' ending
        took eight Spaces). With SKIP_CINEMA off it waits them out instead, True once they are over."""
        from f1 import actions

        screen = self.snap().screen
        if not cinema(screen):
            return False
        if (
            not actions.SKIP_CINEMA
        ):  # played whole; True once it is over, so the callers' loops look again as after a skip
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
        """Save into `slot` through the game's own save screen (f1.saves.save: F4, the slot cursor put on the slot and
        read back, the overwrite and description boxes, SAVE.DAT read back), in the folder of the copy being driven
        (session.game_dir). F6, the quick save, saved to whatever slot its cursor held and into the instance's
        folder only: on a worker it failed."""
        from f1 import saves

        self.to_map()
        s = self.snap()
        tile = s.dude.tile if s.dude else None
        self.log.emit("action_start", action="quicksave", slot=slot, tile=tile)
        if s.screen == "worldmap":  # the save screen does not open there: two tries cost 69 s after the lair
            return self._end(False, "on the world map", {"tile": tile}, "quicksave")
        try:
            rows = saves.save(self.pid, slot, description)
        except saves.SaveError as e:
            self.to_map()
            return self._end(False, str(e), {"tile": tile, "steps": len(e.steps)}, "quicksave")
        return self._end(True, "saved", {"tile": tile, "steps": len(rows)}, "quicksave")

    def quickload(self, expect_tile: int | None = None, slot: int = 1) -> Outcome:
        """Load `slot` through the game's own load screen (f1.saves.load: F5, the slot cursor put on the slot and read
        back, the save's map and elevation checked), from the copy being driven. Done when the map is back (at
        `expect_tile`, if given). Fails fast, so the caller can go by the main menu."""
        from f1 import saves

        self.log.emit("action_start", action="quickload", slot=slot, expect_tile=expect_tile)
        try:
            saves.load(self.pid, slot)
        except saves.SaveError as e:
            s = self.snap()
            return self._end(False, str(e), {"tile": s.dude.tile if s.dude else None}, "quickload")
        watchdog.clear()
        s = self.snap()
        tile = s.dude.tile if s.dude else None
        ok = s.screen == "map" and tile is not None and (expect_tile is None or tile == expect_tile)
        return self._end(
            ok, "loaded" if ok else f"not at {expect_tile} ({s.screen}, {tile})", {"tile": tile}, "quickload"
        )
