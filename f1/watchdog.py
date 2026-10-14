"""The route's watchdog: when nothing in the game moves for longer than the screen on top allows, the route's next
look at the game (Actor.snap) raises Stuck; the step fails like a crashed one, and its checkpoint takes over
(routes.run). Added after a hunt waited minutes on a load screen F7 had left open (597 s in the first
full run): the timeouts must be much shorter.

It reads the game from its own thread, once a second. Progress is what a watcher would see change: the screen, the
map and elevation, the player's tile, HP, AP, XP and level, whose combat turn it is, a new line in the message box,
the party's place on the world map, the dialogue's reply and options, and the game clock jumping (a rest; the clock's
own run on the map, about ten ticks a second, is not progress). Movies and the ending's slides are never stuck. Time
while a person uses the mouse or keyboard does not count (the agent waits for them then).
"""

import struct
import threading
import time
from dataclasses import dataclass, field

from f1 import clock as game_clock
from f1 import dialogue, session, state, win32
from f1 import engine_map as em
from f1.guard import Guard
from f1.memory import GameMemory, ReadError
from f1.telemetry import EventLog

# Seconds without progress each screen allows. The map's 90: Kenji takes up to 45 s to walk in and shoot while the
# player stands (routes.kenji). A save or a load takes a few seconds; a level-up's spending about 15.
LIMITS = {
    "map": 90, "worldmap": 60, "dialogue": 60, "barter": 45, "character": 45, "inventory": 30, "loot": 30,
    "pipboy": 30, "loadsave": 20, "options": 20, "preferences": 20, "skilldex": 20, "elevator": 20,
    "called_shot": 20, "main_menu": 60, "select_character": 30, "none": 30,
}  # fmt: skip
SEEN_MAX = 2000  # looks kept (a long walk is all new ones)
UNNAMED_LIMIT = 5  # a message box no module claims (fail fast: 20 s held a refused rest's box)
CLOCK_JUMP = 100  # game ticks (0.1 s) between two looks a second apart: a rest or a trip, not the clock's run
PERIOD_S = 1.0


class Stuck(RuntimeError):
    """Nothing moved in the game for longer than its screen allows."""


_tripped: str | None = None
_lock = threading.Lock()
_watched: int | None = None  # the game's pid; a restarted game (routes.reload_checkpoint) has a new one


_quiet_until = 0.0  # time.monotonic() until which nothing moving is expected (a wait the route chose)


def quiet(seconds: float) -> None:
    """Nothing will move for `seconds`, by the route's choice (waiting for the game's own Sneak re-roll)."""
    global _quiet_until
    _quiet_until = max(_quiet_until, time.monotonic() + seconds)


def unquiet() -> None:
    """The chosen wait is over before its time."""
    global _quiet_until
    _quiet_until = 0.0


def watch(pid: int) -> None:
    """Look at game `pid` from the next look on (a game restarted after a crash)."""
    global _watched
    _watched = pid


def check() -> None:
    """Raise Stuck, once, when the watchdog tripped since the last check or clear."""
    global _tripped
    if _tripped is None:
        return
    with _lock:
        why, _tripped = _tripped, None
    if why is not None:
        raise Stuck(why)


def clear() -> None:
    """Forget a trip: a new step starts, or a reload put the game back on its checkpoint."""
    global _tripped
    with _lock:
        _tripped = None


def _trip(why: str) -> None:
    global _tripped
    with _lock:
        _tripped = why


_cap: float | None = None  # an ironman run's ceiling on every screen's allowance (set_cap)


def set_cap(seconds: float | None) -> None:
    """At most `seconds` without progress on any screen (an ironman run: one minute, the owner's rule); None: the
    screens' own allowances."""
    global _cap
    _cap = seconds


def limit_for(screen: str) -> float | None:
    """The screen's allowance in seconds; None where waiting is the game's own business: movies, the ending's slides,
    and any big window no module claims (the credits: a 0x14 window 640x480 at (0, 0), nine minutes in the first full
    run). A small one is a message box."""
    kind = state.window_kind(screen)
    if screen == "endgame" or kind == "movie":
        return None
    if screen.startswith("window "):
        return UNNAMED_LIMIT if kind == "box" else None
    limit = LIMITS.get(screen, 60)
    return limit if _cap is None else min(limit, _cap)


@dataclass
class Tracker:
    """The watchdog's reasoning, apart from reading the game: feed it a look a second, it says when to trip."""

    last: tuple | None = None
    since: float = 0.0
    clock: int | None = None
    seen: set = field(default_factory=set)  # the looks since the last reset: progress is a look not among them
    screen: str | None = None
    screen_since: float = 0.0  # when the current screen came up

    def reset(self, now: float) -> None:
        """Movement the look cannot see (a map loading, a wait the route asked for): a fresh start."""
        self.last, self.since = None, now
        self.seen.clear()

    def update(
        self, look: tuple, screen: str, clock: int, now: float, owner_active: bool, speed: float = 1.0
    ) -> str | None:
        # a faster clock (f1.clock) runs the game's time up to `speed` times as fast: its own run is no jump
        jumped = self.clock is not None and clock - self.clock > CLOCK_JUMP * max(1.0, speed)
        self.clock = clock
        if screen != self.screen:
            self.screen, self.screen_since = screen, now
        if owner_active or jumped:
            self.seen.clear()
        if owner_active or jumped or look not in self.seen:
            # a look seen before is no progress: a route that steps to and fro (a door a dweller keeps open, opened
            # again and again, the player's tile and the last line taking turns) went on for minutes while every look
            # differed from the one before it
            if len(self.seen) >= SEEN_MAX:
                self.seen.clear()
            self.seen.add(look)
            self.last, self.since = look, now
            return None
        limit = limit_for(screen)
        # a message box's allowance runs from its own opening: a second locker beside the first gave the same looks
        # (the tile, the search message, the how-many box), and its how-many box, answered in a second, tripped
        # 9 s after the last new look (the Glow's lockers, two ironman runs)
        start = max(self.since, self.screen_since) if state.window_kind(screen) == "box" else self.since
        if limit is None or now - start <= limit:
            return None
        self.since = now  # the route gets a whole allowance to recover before the next trip
        return f"nothing moved for {limit:.0f} s on {screen}"


def look(mem: GameMemory) -> tuple[tuple, str, int]:
    """What progress is measured on, the screen, and the game clock."""
    s = state.read(mem)
    dude = s.dude
    player = (dude.tile, dude.hp, dude.ap) if dude else None
    turn = mem.u32(em.GLOBALS["combat_turn_obj"].address) if mem.glob("combat_state") & 1 else 0
    last_line = state.messages(mem, 1)
    extra: tuple = ()
    if s.screen == "worldmap":
        extra = (mem.glob("world_xpos"), mem.glob("world_ypos"))
    elif s.screen == "dialogue":
        d = dialogue.read(mem)
        extra = (d.reply, tuple(d.options))
    inventory: tuple = ()
    if dude:
        count, items = mem.i32(dude.address + em.Obj.INV_LENGTH), mem.u32(dude.address + em.Obj.INV_ITEMS)
        if items and 0 < count <= 200:
            pairs = struct.iter_unpack("<Ii", mem.read(items, 8 * count))
            inventory = tuple((mem.u32(item + em.Obj.PID), qty, mem.u32(item + em.Obj.FLAGS)) for item, qty in pairs)
    count, values = mem.glob("num_game_global_vars"), mem.glob("game_global_vars")
    quest_state = mem.read(values, 4 * count) if values and 0 < count <= 4096 else b""
    view = (
        s.screen,
        s.map_name,
        s.elevation,
        player,
        s.level,
        s.experience,
        turn,
        tuple(last_line),
        extra,
        inventory,
        quest_state,
    )
    return view, s.screen, s.game_time


class Watchdog(threading.Thread):
    """Watches the game while a route runs; `stop()` ends it."""

    def __init__(self, pid: int, log: EventLog) -> None:
        super().__init__(name="watchdog", daemon=True)
        self.log = log
        self._stop_event = threading.Event()
        watch(pid)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        guard = Guard(state=session.GUARD_STATE)
        tracker = Tracker(since=time.monotonic())
        pid, mem, gone, errors, unreadable_since = None, None, False, set(), None
        try:
            while not self._stop_event.wait(PERIOD_S):
                if _watched != pid:  # the first look, or a game restarted after a crash
                    if mem is not None:
                        mem.close()
                    pid, mem, gone, tracker, unreadable_since = (
                        _watched,
                        None,
                        False,
                        Tracker(since=time.monotonic()),
                        None,
                    )
                if gone:
                    continue
                try:
                    mem = mem or GameMemory(pid)
                    view, screen, clock = look(mem)
                except ReadError:
                    if not win32.is_alive(pid):  # a crash, or the game was closed: once, then quiet
                        gone = True
                        _trip("the game is gone")
                        self.log.emit("stuck", screen=None, why="the game is gone")
                    if not gone:
                        now = time.monotonic()
                        if unreadable_since is None:
                            unreadable_since = now
                            tracker.reset(now)  # a transient rebuild gets one loading allowance
                        elif now - unreadable_since > limit_for("none"):
                            gone = True
                            _trip("the game remained unreadable")
                            self.log.emit("stuck", screen=None, why="the game remained unreadable")
                    continue
                except (ValueError, KeyError, IndexError, AttributeError) as e:  # a bug in the look: said once
                    if repr(e) not in errors:
                        errors.add(repr(e))
                        self.log.emit("watchdog_error", error=repr(e)[:300])
                    now = time.monotonic()
                    unreadable_since = now if unreadable_since is None else unreadable_since
                    if now - unreadable_since > limit_for("none"):
                        gone = True
                        _trip("the game state could not be interpreted")
                        self.log.emit("stuck", screen=None, why="the game state could not be interpreted")
                    continue
                unreadable_since = None
                if time.monotonic() < _quiet_until:  # a wait the route asked for: still is not stuck
                    tracker.reset(time.monotonic())
                    continue
                speed = game_clock.speed_of(mem)
                why = tracker.update(view, screen, clock, time.monotonic(), guard.owner_idle_for() > 0, speed)
                if why:
                    _trip(why)
                    self.log.emit("stuck", screen=screen, why=why)
        finally:
            if mem is not None:
                mem.close()
