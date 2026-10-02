"""Live sessions with the instance: start the game, look at it, press a key, stop it.

    python -m f1.session start        check the instance and the Steam install, back up the instance's saves,
                                      start the game detached, wait for its window
    python -m f1.session status       running or not, its window, elevation, whether it is in front
    python -m f1.session shot NAME    capture the game window to captures/<date>/NAME.png
    python -m f1.session key NAME     press a key in the game (esc, enter, space, a-z, 0-9), through the guard
    python -m f1.session stop         close the game (WM_CLOSE; a kill only if it does not go), then check again
                                      that the Steam install is untouched

Every command appends to runs/sessions.jsonl.
"""

import argparse
import contextlib
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from f1 import capture, clock, hooks, instance, paths, state, win32
from f1 import engine_map as em
from f1.guard import Guard, OwnerActive
from f1.memory import GameMemory
from f1.telemetry import EventLog

EXE_PATH = instance.INSTANCE_DIR / instance.EXE
CAPTURES = paths.CAPTURES
BACKUPS = paths.SAVES
GUARD_STATE = paths.RUNS / "guard.json"
DRIVER_LOCK = paths.RUNS / "driver.lock"  # the process that drives the game now (routes, quests, other drivers)
LOG = EventLog(paths.RUNS / "sessions.jsonl")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Key name -> (scan code, extended). Scan codes are what the game's DirectInput keyboard reads.
KEYS = {"esc": (0x01, False), "enter": (0x1C, False), "space": (0x39, False), "tab": (0x0F, False)}
KEYS |= {"backspace": (0x0E, False)}
KEYS |= {c: (sc, False) for c, sc in zip("1234567890", range(0x02, 0x0C), strict=True)}
KEYS |= {c: (sc, False) for c, sc in zip("qwertyuiop", range(0x10, 0x1A), strict=True)}
KEYS |= {c: (sc, False) for c, sc in zip("asdfghjkl", range(0x1E, 0x27), strict=True)}
KEYS |= {c: (sc, False) for c, sc in zip("zxcvbnm", range(0x2C, 0x33), strict=True)}
KEYS |= {f"f{n}": (sc, False) for n, sc in zip(range(1, 11), range(0x3B, 0x45), strict=True)}  # F6 save, F7 load
KEYS |= {"up": (0x48, True), "left": (0x4B, True), "right": (0x4D, True), "down": (0x50, True)}
KEYS |= {"home": (0x47, True)}  # centres the view on the player
SHIFT, CTRL = 0x2A, 0x1D


class SessionError(RuntimeError):
    pass


def game_pid() -> int | None:
    pids = win32.pids_of(str(EXE_PATH))
    return pids[0] if pids else None


def python_alive(pid: int) -> bool:
    """A live Python process with this pid (not a later process that got the number of a dead one)."""
    image = win32.process_image(pid) if win32.is_alive(pid) else None
    return bool(image) and Path(image).name.lower().startswith("python")


@contextlib.contextmanager
def driver_lock(what: str):
    """One driver of the game at a time: a second route, quest command or other driver on the same game would
    interleave its input with the first (a timed-out shell had left a route playing). A lock whose
    process is gone is taken over."""
    try:
        other = json.loads(DRIVER_LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        other = None
    if other and other.get("pid") != os.getpid() and python_alive(int(other.get("pid", 0))):
        raise SessionError(f"another driver has the game: {other}")
    DRIVER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    mine = {"pid": os.getpid(), "what": what, "since": datetime.datetime.now().astimezone().isoformat()}
    DRIVER_LOCK.write_text(json.dumps(mine), encoding="utf-8")
    try:
        yield
    finally:
        with contextlib.suppress(OSError, ValueError):
            if json.loads(DRIVER_LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
                DRIVER_LOCK.unlink()


def game_window(pid: int) -> int | None:
    """The game's main window: its largest visible top-level window."""
    windows = win32.top_windows(pid)
    if not windows:
        return None
    return max(windows, key=lambda h: win32.client_rect(h).width * win32.client_rect(h).height)


def describe(pid: int) -> dict:
    hwnd = game_window(pid)
    info: dict = {"pid": pid, "elevated": win32.is_elevated(pid), "in_front": win32.foreground_pid() == pid}
    if hwnd:
        r = win32.client_rect(hwnd)
        info |= {"window": hex(hwnd), "title": win32.window_text(hwnd), "class": win32.window_class(hwnd)}
        info |= {"client": [r.left, r.top, r.width, r.height]}
    return info


def _backup_saves() -> str | None:
    saves = instance.INSTANCE_DIR / instance.SAVEGAME
    if not saves.exists() or not any(saves.iterdir()):
        return None
    dst = BACKUPS / f"session-{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}"
    shutil.copytree(saves, dst)
    return str(dst)


def start(run_hooks: bool = True) -> dict:
    """Start the instance; then the session hooks (f1/hooks.py), unless `run_hooks` is off (tests)."""
    if pid := game_pid():
        return LOG.emit("start", already_running=True, **describe(pid))
    if problems := instance.check():
        raise SessionError("; ".join(problems))
    if instance.sha256(EXE_PATH) != em.INSTANCE_EXE_SHA256:
        raise SessionError("the instance's exe is not the pinned HRP-patched 1.1 build")
    backup = _backup_saves()
    instance.music_on()
    env = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    def launch(creationflags: int) -> subprocess.Popen:
        null = subprocess.DEVNULL
        return subprocess.Popen(
            [str(EXE_PATH)], cwd=instance.INSTANCE_DIR, env=env, close_fds=True, creationflags=creationflags,
            stdin=null, stdout=null, stderr=null,
        )  # fmt: skip

    try:  # out of any job the caller runs in, so the game outlives this command
        proc = launch(flags | CREATE_BREAKAWAY_FROM_JOB)
        breakaway = True
    except OSError:
        proc = launch(flags)
        breakaway = False
    if not win32.wait_for(lambda: proc.poll() is not None or game_window(proc.pid) is not None, 30):
        raise SessionError("no game window within 30 s")
    if proc.poll() is not None:
        raise SessionError(f"the game exited at once (code {proc.returncode}); another copy running?")
    time.sleep(1.0)
    names = sorted(hooks.load()) if run_hooks else []
    extra = hooks.call("start", env) if run_hooks else {}
    return LOG.emit("start", backup=backup, breakaway=breakaway, hooks=names, **extra, **describe(proc.pid))


def status() -> dict:
    pid = game_pid()
    return LOG.emit("status", running=pid is not None, **hooks.call("status"), **(describe(pid) if pid else {}))


def shot(name: str) -> dict:
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    folder = CAPTURES / datetime.datetime.now().astimezone().date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    img = capture.grab(hwnd)
    path = folder / f"{name}.png"
    img.save(path)
    lo, hi = img.convert("L").getextrema()
    return LOG.emit("shot", name=name, path=str(path), size=list(img.size), blank=lo == hi, **describe(pid))


def press(name: str, wait_owner_s: float = 120) -> dict:
    """A key by name; "ctrl+down" holds Ctrl with it (the barter screen scrolls the merchant's list so)."""
    mods, _, base = name.rpartition("+")
    if base not in KEYS or mods not in ("", "ctrl"):
        raise SessionError(f"unknown key {name!r}; known: {', '.join(KEYS)}, ctrl+KEY")
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with clock.held(pid), guard.acting():  # input at the game's own speed (f1.clock)
        scan, extended = KEYS[base]
        if mods:
            win32.key(CTRL, down=True)
            time.sleep(0.03)
        win32.key(scan, down=True, extended=extended)
        time.sleep(0.06)
        win32.key(scan, down=False, extended=extended)
        if mods:
            time.sleep(0.03)
            win32.key(CTRL, down=False)
    return LOG.emit("key", key=name, **describe(pid))


def press_keys(names: list[str], gap_s: float = 0.03, hold_s: float = 0.02, wait_owner_s: float = 120) -> dict:
    """Keys one after another under one guard check, each held `hold_s`, `gap_s` apart (a pan of the view by
    arrow keys). The game loses presses much shorter than 50 ms (actions.PAN_HOLD_S)."""
    unknown = [name for name in names if name not in KEYS]
    if unknown:
        raise SessionError(f"unknown keys {unknown}; known: {', '.join(KEYS)}")
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with clock.held(pid), guard.acting():  # input at the game's own speed (f1.clock)
        for name in names:
            scan, extended = KEYS[name]
            win32.key(scan, down=True, extended=extended)
            time.sleep(hold_s)
            win32.key(scan, down=False, extended=extended)
            time.sleep(gap_s)
    return LOG.emit("key", key=" ".join(names), **describe(pid))


def type_text(text: str, wait_owner_s: float = 120) -> dict:
    """Type letters, digits and spaces into the game; capitals with Shift."""
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    names = [("space" if ch == " " else ch.lower()) for ch in text]
    if unknown := [n for n in names if n not in KEYS]:
        raise SessionError(f"cannot type {unknown}")
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with clock.held(pid), guard.acting():  # input at the game's own speed (f1.clock)
        for ch, name in zip(text, names, strict=True):
            scan, extended = KEYS[name]
            if ch.isupper():
                win32.key(SHIFT, down=True)
            win32.key(scan, down=True, extended=extended)
            time.sleep(0.04)
            win32.key(scan, down=False, extended=extended)
            if ch.isupper():
                win32.key(SHIFT, down=False)
            time.sleep(0.06)
    return LOG.emit("type", text=text, **describe(pid))


def click(x: int, y: int, right: bool = False, wait_owner_s: float = 120) -> dict:
    """Click at game-screen pixel (x, y) of the client area (the game's resolution, one pixel for one)."""
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    r = win32.client_rect(hwnd)
    if not (0 <= x < r.width and 0 <= y < r.height):
        raise SessionError(f"({x}, {y}) is outside the {r.width}x{r.height} game screen")
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with clock.held(pid), guard.acting():  # input at the game's own speed (f1.clock)
        win32.SetCursorPos(r.left + x, r.top + y)
        time.sleep(0.05)
        win32.mouse_button(down=True, right=right)
        time.sleep(0.06)
        win32.mouse_button(down=False, right=right)
    return LOG.emit("click", x=x, y=y, right=right, **describe(pid))


def move(x: int, y: int, wait_owner_s: float = 120) -> dict:
    """Put the cursor on game-screen pixel (x, y) without a click (the world map scrolls under a cursor left outside
    its window)."""
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    r = win32.client_rect(hwnd)
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with guard.acting():
        win32.SetCursorPos(r.left + x, r.top + y)
    return LOG.emit("move", x=x, y=y, **describe(pid))


def drag(x0: int, y0: int, x1: int, y1: int, steps: int = 12, wait_owner_s: float = 120) -> dict:
    """Press the left button at (x0, y0), move to (x1, y1) in steps, release (a slider's knob)."""
    pid = game_pid()
    hwnd = game_window(pid) if pid else None
    if not hwnd:
        raise SessionError("the game is not running")
    r = win32.client_rect(hwnd)
    guard = _ready_to_act(pid, hwnd, wait_owner_s)
    with clock.held(pid), guard.acting():  # input at the game's own speed (f1.clock)
        win32.SetCursorPos(r.left + x0, r.top + y0)
        time.sleep(0.08)
        win32.mouse_button(down=True)
        for k in range(1, steps + 1):
            time.sleep(0.04)
            win32.SetCursorPos(r.left + x0 + (x1 - x0) * k // steps, r.top + y0 + (y1 - y0) * k // steps)
        time.sleep(0.15)
        win32.mouse_button(down=False)
    return LOG.emit("drag", start=[x0, y0], end=[x1, y1], **describe(pid))


def _ready_to_act(pid: int, hwnd: int, wait_owner_s: float) -> Guard:
    """The guard, once the user is idle and the game is the window in front; raises otherwise."""
    guard = Guard(state=GUARD_STATE)
    if not guard.wait_until_free(wait_owner_s):
        raise OwnerActive("the user kept using the computer; no input sent")
    if win32.foreground_pid() != pid:
        win32.SetForegroundWindow(hwnd)
        win32.wait_for(lambda: win32.foreground_pid() == pid, 2)
    if win32.foreground_pid() != pid:
        raise SessionError("the game is not the window in front; no input sent")
    _off_the_edges(hwnd, guard)
    return guard


EDGE = 4  # a cursor this close to the window's border (or outside it) scrolls the map, and then keys are ignored


def _off_the_edges(hwnd: int, guard: Guard) -> None:
    """The engine scrolls the map while the mouse sits on the screen's edge and ignores keys meanwhile (game.c:
    `if (gmouse_is_scrolling()) return 0;`). A cursor left there (or outside the window) goes to the view's middle."""
    r = win32.client_rect(hwnd)
    x, y = win32.cursor_pos()
    if r.left + EDGE <= x < r.left + r.width - EDGE and r.top + EDGE <= y < r.top + r.height - EDGE:
        return
    with guard.acting():
        win32.SetCursorPos(r.left + r.width // 2, r.top + (r.height - 100) // 2)  # the map view's middle
    time.sleep(0.1)


def snapshot() -> dict:
    """Everything f1.state reads, as one event."""
    pid = game_pid()
    if not pid:
        raise SessionError("the game is not running")
    with GameMemory(pid) as mem:
        snap = state.read(mem)
    return LOG.emit("state", pid=pid, **snap.to_dict())


def probe() -> dict:
    """A few engine values, read from memory: where the game is."""
    pid = game_pid()
    if not pid:
        raise SessionError("the game is not running")
    with GameMemory(pid) as mem:
        values = {name: mem.glob(name) for name in ("main_window", "mouse_x", "mouse_y", "map_elevation")}
        values |= {name: mem.flag(name) for name in ("in_main_menu", "main_menu_created", "GNW95_isActive")}
        values["game_time"] = mem.glob("fallout_game_time")
        values["gvars"] = mem.glob("num_game_global_vars")
    return LOG.emit("probe", pid=pid, **values)


def _own_exit(pid: int) -> str | None:
    """Leave by the game's own way: from a map F10 and Y (back to the main menu), then Escape. None if it fails."""
    try:
        with GameMemory(pid) as mem:
            screen = state.detect_screen(mem)
            if screen != "main_menu":
                press("f10")
                time.sleep(0.8)
                press("y")
                if not win32.wait_for(lambda: state.detect_screen(mem) == "main_menu", 20, 0.25):
                    return None
            press("esc")
    except (SessionError, OwnerActive, OSError):
        return None
    return "own exit" if win32.wait_for(lambda: not win32.is_alive(pid), 10) else None


def stop(timeout_s: float = 15) -> dict:
    pid = game_pid()
    if not pid:
        return LOG.emit("stop", running=False, steam_untouched=not instance.check())
    how = _own_exit(pid)
    if how is None:
        how = "wm_close"  # a window message, not input: fine while the user uses the computer
        if hwnd := game_window(pid):
            win32.close_window(hwnd)
    if not win32.wait_for(lambda: not win32.is_alive(pid), timeout_s):
        how = "kill"
        win32.kill(pid)
        win32.wait_for(lambda: not win32.is_alive(pid), 5)
    problems = instance.check()
    return LOG.emit(
        "stop", pid=pid, how=how, alive=win32.is_alive(pid), steam_untouched=not problems, problems=problems
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m f1.session")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start").add_argument("--no-hooks", action="store_true", help="no session hooks (tests)")
    sub.add_parser("status")
    sub.add_parser("shot").add_argument("name")
    sub.add_parser("key").add_argument("name")
    c = sub.add_parser("click")
    c.add_argument("x", type=int)
    c.add_argument("y", type=int)
    c.add_argument("--right", action="store_true")
    sub.add_parser("probe")
    sub.add_parser("state")
    sub.add_parser("stop")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "start":
            event = start(run_hooks=not args.no_hooks)
        elif args.cmd == "status":
            event = status()
        elif args.cmd == "shot":
            event = shot(args.name)
        elif args.cmd == "key":
            event = press(args.name)
        elif args.cmd == "click":
            event = click(args.x, args.y, args.right)
        elif args.cmd == "probe":
            event = probe()
        elif args.cmd == "state":
            event = snapshot()
        else:
            event = stop()
    except (SessionError, OwnerActive) as e:
        LOG.emit("error", cmd=args.cmd, error=str(e))
        print(f"error: {e}", file=sys.stderr)
        return 1
    print({k: v for k, v in event.items() if k not in ("t", "mono", "seq")})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
