"""Saving and loading through Fallout 1's own screens, every step checked from memory; and checkpoints, slots kept
outside the game's folder.

The save screen (loadsave.c SaveGame): F4 opens it (F6, the quick save, opens it too until a slot was saved or
loaded, then saves to the slot under the cursor at once). Home puts the cursor on the first slot, Down moves it
(slot_cursor, 0..9, kept from screen to screen); Enter takes the slot: an occupied one (LSstatus) first asks
whether to overwrite (Y), then a box asks for the description, which starts with the slot's old one (Backspace
clears it; Enter takes it). The game then writes DATA\\SAVEGAME\\SLOTnn\\ (SAVE.DAT and the maps' states) and closes
the screen. The load screen (LoadGame): F5 in the game (F7, the quick load, opens it too), L in the main menu; the
same cursor; Enter loads. Each screen reads the slot list anew when it opens (GetSlotList), so a slot's files may
change while the game runs, only not while a screen is open.

Measured on Steam's 1.1 (a headless copy and the windowed instance): F4 and F5 open the screens; slot_cursor follows
Home and Down one step a key; LSstatus reads 0 for an empty slot, 1 for a saved one; the description in LSData
(slots 1-3) equals the one typed and the one in SAVE.DAT; a checkpoint installed into a slot while the game ran
was listed and loaded; after a load the map's name reads "<MAP>.SAV" (after a new game "<MAP>.MAP"), so maps are
compared by their stem.

Slots here are numbered as the game names their folders, 1..10. Every step returns a row (what, wanted, seen, ok);
a step that fails raises SaveError with the rows so far.

A checkpoint is a slot's folder copied to <saves>/<name>/SLOT01/ with <name>/save.json (what it holds), installed
back into any copy's slot to start from it.

    python -m f1.saves save SLOT DESCRIPTION | load SLOT [--menu] | keep SLOT NAME | install NAME SLOT | list
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

from f1 import clock, paths, savegame, session, state
from f1 import engine_map as em
from f1.memory import GameMemory

SLOTS = 10
EMPTY, OCCUPIED = 0, 1
OVERWRITE_BOX = (0x14, 302, 127)  # the overwrite question (measured at 640x480 and after HRP's centring)
DESCRIPTION_BOX = (0x14, 290, 85)
DESCRIPTION_MAX = 29  # get_input_str2's maxLength: LOAD_SAVE_DESCRIPTION_LENGTH - 1
SCREEN_WAIT_S, SAVE_WAIT_S, LOAD_WAIT_S = 5.0, 20.0, 60.0
STILL_S = 2.0  # a slot's files stand still this long before a copy (a save renames them to .BAK, then rewrites them)


class SaveError(RuntimeError):
    def __init__(self, message: str, steps: list[dict] | None = None) -> None:
        super().__init__(message)
        self.steps = steps or []


def slot_dir(slot: int, game_dir: Path | None = None) -> Path:
    if not 1 <= slot <= SLOTS:
        raise SaveError(f"a slot is 1..{SLOTS}")
    return (game_dir or session.game_dir()) / "DATA" / "SAVEGAME" / f"SLOT{slot:02d}"


def typeable(text: str) -> str:
    """What the description box takes from session.type_text: letters, digits and spaces, at most DESCRIPTION_MAX."""
    return "".join(ch if ch.isascii() and ch.isalnum() else " " for ch in text).strip()[:DESCRIPTION_MAX]


def slot_state(mem: GameMemory, slot: int) -> int:
    return mem.i32(em.GLOBALS["LSstatus"].address + 4 * (slot - 1))


def slot_description(mem: GameMemory, slot: int) -> str:
    at = em.GLOBALS["LSData"].address + em.LS_SLOT_SIZE * (slot - 1) + em.LS_DESCRIPTION
    return mem.read(at, 30).split(b"\0")[0].decode("latin-1")


def top_box(mem: GameMemory) -> tuple[int, int, int] | None:
    windows = state.live_windows(mem)
    if not windows:
        return None
    _wid, flags, _x, _y, w, h = windows[-1]
    return (flags & 0xFF, w, h)


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while True:
        if cond():
            return True
        if time.monotonic() > end:
            return False
        time.sleep(0.05)


class _Steps:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, what: str, wanted, seen, ok: bool | None = None) -> dict:
        row = {"what": what, "wanted": wanted, "seen": seen, "ok": seen == wanted if ok is None else ok}
        self.rows.append(row)
        if not row["ok"]:
            raise SaveError(f"{what}: wanted {wanted!r}, seen {seen!r}", self.rows)
        return row


def _open(mem: GameMemory, key: str, steps: _Steps, tries: int = 3) -> None:
    """Open the save or load screen by its key; a key during an animation is lost, so up to `tries` presses."""
    for _ in range(tries):
        session.press(key)
        if _wait(lambda: state.detect_screen(mem) == "loadsave", SCREEN_WAIT_S):
            break
    steps.check(f"{key} opens the screen", "loadsave", state.detect_screen(mem))
    time.sleep(0.5)  # the list is drawn


def _to_slot(mem: GameMemory, slot: int, steps: _Steps) -> None:
    session.press("home")
    _wait(lambda: mem.glob("slot_cursor") == 0, SCREEN_WAIT_S)
    steps.check("home: the cursor on slot 1", 1, mem.glob("slot_cursor") + 1)
    for n in range(2, slot + 1):
        session.press("down")
        _wait(lambda n=n: mem.glob("slot_cursor") == n - 1, SCREEN_WAIT_S)
        steps.check(f"down: the cursor on slot {n}", n, mem.glob("slot_cursor") + 1)


def _stood_still(folder: Path, seconds: float = STILL_S, timeout: float = 30.0) -> None:
    def look() -> tuple:
        return tuple(sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in folder.iterdir()))

    seen, since, end = look(), time.monotonic(), time.monotonic() + timeout
    while time.monotonic() - since < seconds:
        if time.monotonic() > end:
            raise SaveError(f"{folder} kept changing for {timeout} s")
        time.sleep(0.25)
        now = look()
        if now != seen:
            seen, since = now, time.monotonic()


def save(pid: int, slot: int, description: str, key: str = "f4") -> list[dict]:
    """Save into SLOT with DESCRIPTION by the save screen, from the map; the checked steps. Done when the screen has
    closed and SAVE.DAT, written anew, holds the description and the map the player is on."""
    text = typeable(description)
    target = slot_dir(slot) / "SAVE.DAT"
    old = target.stat().st_mtime_ns if target.exists() else 0
    steps = _Steps()
    with clock.held(pid), GameMemory(pid) as mem:
        snap = state.read(mem)
        steps.check("on the map", "map", snap.screen)
        _open(mem, key, steps)
        _to_slot(mem, slot, steps)
        occupied = slot_state(mem, slot) == OCCUPIED
        steps.check("the slot's state agrees with its file", target.exists(), occupied)
        session.press("enter")
        if occupied:
            _wait(lambda: top_box(mem) == OVERWRITE_BOX, SCREEN_WAIT_S)
            steps.check("enter: asked to overwrite", OVERWRITE_BOX, top_box(mem))
            session.press("y")
        _wait(lambda: top_box(mem) == DESCRIPTION_BOX, SCREEN_WAIT_S)
        steps.check("the description box", DESCRIPTION_BOX, top_box(mem))
        for _ in range(DESCRIPTION_MAX + 1):  # the box starts with the slot's old description
            session.press("backspace")
        if text:
            session.type_text(text)
        session.press("enter")
        _wait(lambda: target.exists() and target.stat().st_mtime_ns > old, SAVE_WAIT_S)
        steps.check("SAVE.DAT written", True, target.exists() and target.stat().st_mtime_ns > old)
        steps.check("the slot list holds the description", text, slot_description(mem, slot))
        _wait(lambda: state.detect_screen(mem) == "map", SCREEN_WAIT_S)
        steps.check("the screen closed", "map", state.detect_screen(mem))
    _stood_still(target.parent)
    head = savegame.header(target.read_bytes())
    steps.check("the file's description", text, head["description"])
    steps.check("the file's map", Path(snap.map_name).stem.upper(), Path(head["map_file"]).stem.upper())
    steps.check("the file's elevation", snap.elevation, head["elevation"])
    return steps.rows


def load(pid: int, slot: int, from_menu: bool = False, key: str = "f5") -> list[dict]:
    """Load SLOT by the load screen (KEY in the game, L in the main menu); the checked steps. Done when the game is on
    the save's map and elevation with the player on a tile."""
    target = slot_dir(slot) / "SAVE.DAT"
    if not target.exists():
        raise SaveError(f"no save in {target.parent}")
    head = savegame.header(target.read_bytes())
    steps = _Steps()
    with clock.held(pid), GameMemory(pid) as mem:
        steps.check("the screen", "main_menu" if from_menu else "map", state.detect_screen(mem))
        _open(mem, "l" if from_menu else key, steps)
        _to_slot(mem, slot, steps)
        steps.check("the slot is occupied", OCCUPIED, slot_state(mem, slot))
        steps.check("the slot list's description is the file's", head["description"], slot_description(mem, slot))
        session.press("enter")
        # a save made on the world map loads there
        _wait(lambda: state.detect_screen(mem) in ("map", "worldmap"), LOAD_WAIT_S)
        steps.check("on a map", True, state.detect_screen(mem) in ("map", "worldmap"))

        def placed() -> bool:
            s = state.read(mem)
            return s.dude is not None and s.dude.tile > 0 and bool(s.map_name)

        _wait(placed, SCREEN_WAIT_S)
        time.sleep(1.0)  # the fade-in
        snap = state.read(mem)
    steps.check("the save's map", Path(head["map_file"]).stem.upper(), Path(snap.map_name).stem.upper())
    steps.check("the save's elevation", head["elevation"], snap.elevation)
    steps.check("the player on a tile", True, snap.dude is not None and snap.dude.tile > 0)
    return steps.rows


# --- checkpoints ---------------------------------------------------------------------------------------------------


def _kept(name: str) -> Path:
    if not name or name in (".", "..") or any(c in name for c in '/\\:*?"<>|\0') or name.endswith((".", " ")):
        raise SaveError("a checkpoint's name must be one plain folder name")
    return paths.SAVES / name


def _plain(path: Path) -> None:
    """Refuse every link, Windows junctions too, on the way to a save before copying or removing it."""
    for part in (*reversed(path.absolute().parents), path.absolute()):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SaveError(f"a save path holds a link: {part}")


def _tree(path: Path) -> None:
    _plain(path)
    if path.is_dir():
        for entry in path.iterdir():
            _tree(entry)


def _remove(path: Path, root: Path) -> None:
    """Remove a checked plain tree strictly below `root`, file by file; never through a link."""
    _tree(path)
    base, target = root.resolve(), path.resolve()
    if target == base or not target.is_relative_to(base):
        raise SaveError(f"will not remove {path}: not inside {root}")
    if path.is_dir():
        for entry in path.iterdir():
            _remove(entry, root)
        path.rmdir()
    else:
        path.unlink(missing_ok=True)


def _replace(src: Path, dst: Path, root: Path) -> Path:
    """`dst` (inside `root`) becomes a copy of the slot folder `src` without .BAK files; the old `dst` goes only once
    the copy stands beside it."""
    _tree(src)
    _tree(dst)
    if not (src / "SAVE.DAT").is_file():
        raise SaveError(f"{src} holds no save")
    root.mkdir(parents=True, exist_ok=True)
    if dst.resolve() == root.resolve() or not dst.resolve().is_relative_to(root.resolve()):
        raise SaveError(f"a save must stay inside {root}")
    staging = Path(tempfile.mkdtemp(prefix=".save-", dir=root))
    incoming, previous = staging / "incoming", staging / "previous"
    try:
        shutil.copytree(src, incoming, ignore=shutil.ignore_patterns("*.BAK"))
        if dst.exists():
            os.replace(dst, previous)
        try:
            os.replace(incoming, dst)
        except BaseException:
            if previous.exists():
                os.replace(previous, dst)
            raise
    finally:
        _remove(staging, root)
    return dst


def keep(slot: int, name: str, about: dict | None = None) -> Path:
    """Copy SLOT (once its files stand still) to the checkpoint NAME, replacing one of that name; its folder."""
    src = slot_dir(slot)
    if not (src / "SAVE.DAT").is_file():
        raise SaveError(f"no save in {src}")
    _stood_still(src)
    folder = _kept(name)
    _plain(folder)
    folder.mkdir(parents=True, exist_ok=True)
    _replace(src, folder / "SLOT01", folder)
    head = savegame.header((folder / "SLOT01" / "SAVE.DAT").read_bytes())
    info = {"checkpoint": name, "from_slot": slot, **head, **(about or {})}
    (folder / "save.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return folder


def install(name: str, slot: int) -> Path:
    """The checkpoint NAME into SLOT of the copy the play code drives (the slot's old files replaced). The game may
    run, only not with the save or load screen open."""
    src = _kept(name) / "SLOT01"
    if not (src / "SAVE.DAT").is_file():
        raise SaveError(f"no checkpoint {name} ({src})")
    dst = slot_dir(slot)
    return _replace(src, dst, dst.parent)


def clear(slot: int) -> None:
    """Empty SLOT of the copy the play code drives (not with the save or load screen open)."""
    folder = slot_dir(slot)
    if folder.exists():
        _remove(folder, folder.parent)


def kept() -> list[dict]:
    out = []
    for info in sorted(paths.SAVES.glob("*/save.json")):
        try:
            out.append(json.loads(info.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def main(argv: list[str]) -> int:
    pid = session.game_pid()
    if argv[:1] == ["list"]:
        for k in kept():
            print(f"{k['checkpoint']:24} {k['map_file']:14} {k['description']}")
        return 0
    if len(argv) == 3 and argv[0] == "keep":
        print(keep(int(argv[1]), argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "install":
        print(install(argv[1], int(argv[2])))
        return 0
    if not pid:
        print("the game is not running")
        return 1
    try:
        if len(argv) == 3 and argv[0] == "save":
            rows = save(pid, int(argv[1]), argv[2])
        elif argv[:1] == ["load"] and len(argv) in (2, 3):
            rows = load(pid, int(argv[1]), from_menu="--menu" in argv)
        else:
            print(__doc__)
            return 2
    except SaveError as e:
        rows = e.steps
        print(f"failed: {e}")
    for r in rows:
        print(json.dumps(r))
    good = sum(r["ok"] for r in rows)
    print(f"=> {good}/{len(rows)} steps verified")
    return 0 if rows and good == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
