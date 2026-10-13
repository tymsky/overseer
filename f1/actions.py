"""Verified actions: input, then a wait on engine state, then a checked result. The unit of reliability.

python -m f1.actions walktest [N]   M1's check: N walk commands (default 20) to tiles near the player, each
                                    confirmed by the player's tile read from memory; events in runs/<id>/
python -m f1.actions fight          fight adjacent enemies until combat is over (a first, crude M4 executor)
"""

import datetime
import random
import sys
import time

from f1 import clock, geometry, inputs, paths, session, state, watchdog, win32
from f1.action_combat import (
    AIM_BLOCKED,
    COMBAT_TURN_OBJ,
    FORCED_FLOOR,
    GOOD_SHOT,
    HOPELESS_SHOT,
    HOSTILE_EXACT,
    HOSTILE_KINDS,
    LONG_SHOT,
    RIGHT_CLICKS_TO_CROSSHAIR,
    STALL_TURNS,
    CombatActions,
    retreat_hex,
)
from f1.action_interaction import (
    _AIMED,
    _CLICKED,
    AIM_GRID,
    AIM_OFFSETS,
    AMBIENT,
    BODY_GRID,
    LAST_OBJECT,
    LOOK_SETTLE_S,
    OBJECT_FLAT,
    SKILL_KEYS,
    TALK_AIMS,
    InteractionActions,
    aim_order,
)
from f1.action_inventory import (
    BUTTON_FLAG_DISABLED,
    CONTAINER_AIMS,
    INVENTORY_AP,
    INVENTORY_CURSOR_ARROW,
    INVENTORY_CURSOR_HAND,
    INVENTORY_ROWS,
    ITEM_ACTION_PRIMARY,
    ITEM_ACTION_RELOAD,
    ITEM_BUTTON,
    ITEM_BUTTON_CODE,
    ITEM_EQUIPPED,
    ITEM_MENU_DROP,
    ITEM_MENU_DROP_UNUSABLE,
    ITEM_MENU_USE,
    ITEM_STATE_ACTION,
    ITEM_STATE_DISABLED,
    ITEM_STATE_SIZE,
    LIST_GAP_S,
    LIST_HOLD_S,
    LOOT_AIMS,
    LOOT_BODY_SLOT,
    LOOT_PLAYER_LIST,
    LOOT_TOO_HEAVY,
    LOOT_WINDOW,
    OBJECT_IN_LEFT_HAND,
    OBJECT_IN_RIGHT_HAND,
    OBJECT_WORN,
    PICKUP_TOO_HEAVY,
    PID_10MM_JHP,
    PID_10MM_PISTOL,
    PID_STIMPAK,
    PROTO_BASE_STATS,
    STAT_COUNT,
    STAT_MAX_AP,
    STAT_MAX_HP,
    InventoryActions,
)
from f1.action_movement import (
    FOLLOW_SLACK,
    PAN_GAP_S,
    PAN_HOLD_S,
    PAN_MAX_KEYS,
    PAN_STEP,
    PROTO_CRITTER_FLAGS,
    STILL_READS,
    TILE_STALL_S,
    MovementActions,
    interleave,
)
from f1.action_shared import COMBAT_ACTIVE, MOUSE_ARROW, MOUSE_CROSSHAIR, MOUSE_USE_CROSSHAIR, Held, Outcome
from f1.action_ui import CINEMA_KEY, CINEMA_LIMIT_S, CLOSABLE, MESSAGE_BOX, REST, ScreenActions, cinema
from f1.guard import Guard, OwnerActive
from f1.memory import GameMemory
from f1.telemetry import EventLog

__all__ = [
    "AIM_BLOCKED",
    "AIM_GRID",
    "AIM_OFFSETS",
    "AMBIENT",
    "ART_AIMS",
    "BODY_GRID",
    "BUTTON_FLAG_DISABLED",
    "CINEMA_KEY",
    "CINEMA_LIMIT_S",
    "CLOSABLE",
    "COMBAT_ACTIVE",
    "COMBAT_TURN_OBJ",
    "CONTAINER_AIMS",
    "FOLLOW_SLACK",
    "FORCED_FLOOR",
    "GOOD_SHOT",
    "HOPELESS_SHOT",
    "HOSTILE_EXACT",
    "HOSTILE_KINDS",
    "INVENTORY_AP",
    "INVENTORY_CURSOR_ARROW",
    "INVENTORY_CURSOR_HAND",
    "INVENTORY_ROWS",
    "ITEM_ACTION_PRIMARY",
    "ITEM_ACTION_RELOAD",
    "ITEM_BUTTON",
    "ITEM_BUTTON_CODE",
    "ITEM_EQUIPPED",
    "ITEM_MENU_DROP",
    "ITEM_MENU_DROP_UNUSABLE",
    "ITEM_MENU_USE",
    "ITEM_STATE_ACTION",
    "ITEM_STATE_DISABLED",
    "ITEM_STATE_SIZE",
    "LAST_OBJECT",
    "LIST_GAP_S",
    "LIST_HOLD_S",
    "LONG_SHOT",
    "LOOK_SETTLE_S",
    "LOOT_AIMS",
    "LOOT_BODY_SLOT",
    "LOOT_PLAYER_LIST",
    "LOOT_TOO_HEAVY",
    "LOOT_WINDOW",
    "MESSAGE_BOX",
    "MOUSE_ARROW",
    "MOUSE_CROSSHAIR",
    "MOUSE_USE_CROSSHAIR",
    "OBJECT_FLAT",
    "OBJECT_IN_LEFT_HAND",
    "OBJECT_IN_RIGHT_HAND",
    "OBJECT_WORN",
    "PAN_GAP_S",
    "PAN_HOLD_S",
    "PAN_MAX_KEYS",
    "PAN_STEP",
    "PICKUP_TOO_HEAVY",
    "PID_10MM_JHP",
    "PID_10MM_PISTOL",
    "PID_STIMPAK",
    "PROTO_BASE_STATS",
    "PROTO_CRITTER_FLAGS",
    "REST",
    "RIGHT_CLICKS_TO_CROSSHAIR",
    "SKILL_KEYS",
    "SKIP_CINEMA",
    "STALL_TURNS",
    "STAT_COUNT",
    "STAT_MAX_AP",
    "STAT_MAX_HP",
    "STILL_READS",
    "TALK_AIMS",
    "TILE_STALL_S",
    "_AIMED",
    "_CLICKED",
    "Actor",
    "Held",
    "Outcome",
    "aim_order",
    "cinema",
    "fight",
    "interleave",
    "retreat_hex",
    "walktest",
]

# Aim first where the target's picture shows (f1.art), then the grid: the WATRSHD ladder in 20.0 s against 23.5 s
ART_AIMS = True
# False lets them play whole (for a recording of the whole game: a skipped slide is lost).
SKIP_CINEMA = True


class Actor(MovementActions, InteractionActions, InventoryActions, CombatActions, ScreenActions):
    """Sends input to the running game through the guard and checks results in its memory."""

    # a fight a foe started that the gate refuses: left by the nearest exit (a route that must stay sets it False)
    leave_refused = True

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
        """Put the cursor on game-screen pixel (x, y) and confirm it with the engine's own cursor (inputs.engine_cursor)."""
        self._front()
        r = win32.client_rect(self.hwnd)
        with inputs.acting(self.guard):
            inputs.set_cursor(r.left + x, r.top + y)
        return win32.wait_for(lambda: inputs.engine_cursor(self.mem) == (x, y), 1.0, 0.02)

    def click(self, right: bool = False) -> None:
        self._front()
        with clock.held(self.pid), inputs.acting(self.guard):  # the click at the game's own speed (f1.clock)
            inputs.mouse_button(down=True, right=right)
            time.sleep(0.06)
            inputs.mouse_button(down=False, right=right)

    def _end(self, ok: bool, reason: str, detail: dict, action: str = "walk_to") -> Outcome:
        self.log.emit("action_end", action=action, ok=ok, reason=reason, **detail)
        return Outcome(ok, reason, detail)


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
