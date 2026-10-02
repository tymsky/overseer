"""The live smoke test, run at the start of a session: the whole loop from start to a clean exit.

    python -m f1.smoke [--require HOOK ...]

Starts the instance (and the session hooks, f1/hooks.py), skips to the main menu with clicks, confirms the menu from memory,
composes its picture from memory, leaves with the game's own Escape (keyboard), and checks that the Steam install
is untouched, then runs the hooks' own checks (a recorder's, for instance: the session was recorded with sound).
`--require` fails the test when a named hook is not registered. Exit code 0 when every step holds. The picture goes to
captures/<date>/smoke-menu.png. (Windows capture is not used: it shows the DirectDraw 7 window white.)
"""

import argparse
import datetime
import sys
import time

import numpy as np

from f1 import frame, hooks, instance, paths, session, win32
from f1.memory import GameMemory


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m f1.smoke")
    ap.add_argument("--require", nargs="*", default=[], metavar="HOOK", help="session hooks that must be registered")
    args = ap.parse_args(argv)
    steps: list[tuple[str, bool, str]] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append((name, ok, detail))
        print(f"[{'ok' if ok else 'FAIL'}] {name} {detail}", flush=True)
        return ok

    loaded = sorted(hooks.load())
    step(f"session hooks {loaded}", set(args.require) <= set(loaded), f"required {args.require}")
    ev = session.start()
    pid = ev["pid"]
    client = ev.get("client", [0, 0, 0, 0])[2:]
    size = list(instance.configured_size())
    step(
        f"started, not elevated, window {size[0]}x{size[1]}", client == size and not ev["elevated"], f"client {client}"
    )
    time.sleep(5)

    with GameMemory(pid) as mem:
        for _ in range(4):  # the logo, then the intro: each click skips one
            if mem.flag("in_main_menu"):
                break
            session.click(320, 240)
            time.sleep(2)
        step("main menu up (in_main_menu)", mem.flag("in_main_menu"), f"main_window={mem.glob('main_window')}")
        time.sleep(1)
        picture = frame.compose(mem)
    folder = paths.CAPTURES / datetime.datetime.now().astimezone().date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    picture.save(folder / "smoke-menu.png")
    colours = len(np.unique(np.asarray(picture).reshape(-1, 3), axis=0))
    step("main menu composed from memory", colours > 50, f"{colours} colours, {folder / 'smoke-menu.png'}")

    session.press("esc")
    by_key = win32.wait_for(lambda: not win32.is_alive(pid), 10)
    step("Escape leaves the game (keyboard reaches it)", by_key)
    stopped = session.stop()  # its own exit or WM_CLOSE if Escape did not work; always re-checks the Steam install
    step("game gone", not win32.is_alive(pid), f"how={'escape' if by_key else stopped.get('how')}")
    step("Steam install untouched", stopped["steam_untouched"], "; ".join(stopped.get("problems", [])))

    for name, ok, detail in hooks.checks(ev):
        step(name, ok, detail)

    failed = [name for name, ok, _ in steps if not ok]
    print("=> PASS" if not failed else f"=> FAIL: {', '.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
