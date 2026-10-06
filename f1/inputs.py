"""The bot's input: either Windows' own (the desktop cursor and SendInput, the game in front, the presence guard
waiting for the user), or the background way through the DINPUT.DLL proxy in the instance (f1/dinput.py): the game
fed directly, behind other windows, with the user's mouse and keyboard left alone.

The proxy is the default: `f1.play setup` (and `f1.instance build`) build it with zig and put it in the instance;
`--input direct`, or `python -m f1.instance input direct` later, takes it out. The mode is the instance's (its
folder holds DINPUT.DLL or not). Every executor goes through the functions here, so routes run the
same either way.
"""

import contextlib
import time

from f1 import instance, win32
from f1.dinput import EXTENDED, Feed, FeedError
from f1.memory import GameMemory

WM_ACTIVATEAPP = 0x001C
DLL_NAME = "DINPUT.DLL"
_feed: Feed | None = None


def background() -> bool:
    """The instance plays through the proxy."""
    return (instance.INSTANCE_DIR / DLL_NAME).exists()


def feed() -> Feed:
    global _feed
    if _feed is None:
        try:
            _feed = Feed()
        except FeedError as e:
            raise FeedError(f"{e}: was the game started by f1.session with the proxy in the instance?") from e
    return _feed


def _game():
    from f1 import session  # session imports this module

    pid = session.game_pid()
    hwnd = session.game_window(pid) if pid else None
    if not hwnd:
        raise session.SessionError("the game is not running")
    return pid, hwnd


def set_cursor(sx: int, sy: int) -> None:
    """The cursor to screen point (sx, sy). In the background the engine's own cursor is moved by relative motion
    until its mouse_x/y are there, and HRP is shown the same point as the desktop's cursor."""
    if not background():
        win32.SetCursorPos(sx, sy)
        return
    pid, hwnd = _game()
    r = win32.client_rect(hwnd)
    x, y = sx - r.left, sy - r.top
    f = feed()
    f.point(sx, sy)
    with GameMemory(pid) as mem:
        for _ in range(15):
            cx, cy = mem.glob("mouse_x"), mem.glob("mouse_y")
            if (cx, cy) == (x, y):
                return
            f.nudge(x - cx, y - cy)
            if not f.wait_reads(2):
                wake(pid, hwnd)


def cursor_pos() -> tuple[int, int]:
    if not background():
        return win32.cursor_pos()
    pid, hwnd = _game()
    r = win32.client_rect(hwnd)
    with GameMemory(pid) as mem:
        return r.left + mem.glob("mouse_x"), r.top + mem.glob("mouse_y")


def mouse_button(down: bool, right: bool = False) -> None:
    if not background():
        win32.mouse_button(down=down, right=right)
        return
    f = feed()
    f.buttons(left=down and not right, right=down and right)
    f.wait_reads(2)  # the game sees the state for at least a frame


def key(scan: int, down: bool, extended: bool = False) -> None:
    if not background():
        win32.key(scan, down=down, extended=extended)
        return
    feed().key(scan | (EXTENDED if extended else 0), down)


def wake(pid: int, hwnd: int, timeout_s: float = 2.0) -> bool:
    """The game's input loop stalls while it thinks it lost the focus (its flag set from WM_ACTIVATEAPP's wParam);
    posted WM_ACTIVATEAPP(1) sets the flag back. True when the game is awake."""
    with GameMemory(pid) as mem:
        if mem.flag("GNW95_isActive"):
            return True
        win32.PostMessageW(hwnd, WM_ACTIVATEAPP, 1, 0)
        return win32.wait_for(lambda: mem.flag("GNW95_isActive"), timeout_s, 0.05)


def ready(pid: int, hwnd: int, guard, wait_owner_s: float, error: type[Exception], busy: type[Exception]) -> None:
    """Before input: in the background, the game awake; otherwise the user idle and the game the window in front."""
    if background():
        if not wake(pid, hwnd):
            raise error("the game did not wake (WM_ACTIVATEAPP)")
        return
    if not guard.wait_until_free(wait_owner_s):
        raise busy("the user kept using the computer; no input sent")
    if win32.foreground_pid() != pid:
        win32.SetForegroundWindow(hwnd)
        win32.wait_for(lambda: win32.foreground_pid() == pid, 2)
    if win32.foreground_pid() != pid:
        raise error("the game is not the window in front; no input sent")


def acting(guard):
    """The guard's watch over input sent through Windows; nothing to watch in the background."""
    return contextlib.nullcontext() if background() else guard.acting()


def prepare_start() -> Feed | None:
    """Before the game starts: the memory the proxy will look for (background only)."""
    global _feed
    if not background():
        return None
    _feed = Feed(create=True)
    return _feed


def wait_devices(timeout_s: float = 30.0) -> bool:
    """After the start: the game has created its DirectInput devices through the proxy."""
    if not background():
        return True
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if feed().devices != (0, 0):
            return True
        time.sleep(0.1)
    return False
