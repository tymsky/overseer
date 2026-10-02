"""The bot's own copy of Fallout 1 (paths.INSTANCE): built from the Steam install, never the other way round.

    python -m f1.instance build [--force] [--game DIR] [--size WxH]
                                            copy the install (f1/steam.py finds it; without its saves), make the
                                            copy's FALLOUTW.EXE load the bundled High Resolution Patch, set it up (a
                                            window at the game's resolution, mouse from Windows messages), write
                                            INSTANCE.json
    python -m f1.instance configure [--size WxH]   the bot's settings again; the game's resolution (by default the
                                            largest of SIZES whose window fits the screen)
    python -m f1.instance check             the instance matches its manifest, and the install's game files still
                                            match the fingerprint taken at build time (the bot did not touch them)

The Steam install is only read: files are copied out of it and hashed.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from f1 import engine_map as em
from f1 import paths, steam, win32
from f1.verify_exe import verify

INSTANCE_DIR = paths.INSTANCE
MANIFEST = INSTANCE_DIR / "INSTANCE.json"
BACKUPS = paths.SAVES
EXE = "FALLOUTW.EXE"
SAVEGAME = Path("DATA") / "SAVEGAME"

# Not copied: the 1.2 engine and its launcher, installers, manuals, and the CDPLC ddraw.dll wrapper (HRP renders
# without it, and HRP's DirectDraw 7 mode fails with it). The install's saves are never copied either.
SKIP_TOP = {
    "Manual",
    "Extras",
    "falloutwHR.exe",
    "FalloutLauncher.exe",
    "GameuxInstallHelper.dll",
    "Fallout1_High_Resolution_Patch_4.1.8.exe",
    "ddraw.dll",
}

# The game's resolution, windowed, largest first: the default is the first whose window fits the screen's work area.
# 1920x1080 and 640x480 (the original) carried whole runs; 1280x720 is not yet measured.
SIZES = ((1920, 1080), (1280, 720), (640, 480))

# How the agent runs HRP: (section, key, value) in f1_res.ini.
HRP_SETTINGS = (
    ("MAIN", "UAC_AWARE", "0"),  # read this ini, not a per-user copy under %APPDATA%
    # DirectDraw 7. With the 1.1 exe on this PC, mode 2 (DirectX 9) leaves the window white (WGC), while mode 1
    # draws the movies and the menu. Mode 1 fails with the CDPLC ddraw.dll ("QueryInterface Direct
    # Draw 7 Failed"), which is why the instance leaves that wrapper out.
    ("MAIN", "GRAPHICS_MODE", "1"),
    ("MAIN", "SCALE_2X", "0"),  # one game pixel is one screen pixel: every offset round a hex depends on it
    ("MAIN", "WINDOWED", "1"),
    ("MAIN", "WINDOWED_FULLSCREEN", "0"),  # else the window takes the desktop's size
    ("INPUT", "ALT_MOUSE_INPUT", "1"),  # the mouse from Windows messages instead of DirectInput
    ("INPUT", "EXTRA_WIN_MSG_CHECKS", "1"),
    ("MAPS", "IGNORE_PLAYER_SCROLL_LIMITS", "1"),  # the camera may leave the player (nav.scroll_towards)
    ("IFACE", "IFACE_BAR_MODE", "0"),  # the map view above the interface bar, not under it
)


WINDOW_BORDER = (8, 31, 8, 8)  # left, top (title bar), right, bottom: the client measured at (8, 31) in the window


def win_data(size: tuple[int, int]) -> str:
    """HRP's WIN_DATA for a window whose client is `size`: a Win32 WINDOWPLACEMENT (length 44, flags 0, shown normal,
    min and max positions -1, the normal rect at (0, 0)) in hex, and a checksum byte, the sum of its bytes. Read off
    the instance's own value for 640x480 (outer 656x519), which this reproduces. In a window HRP takes the game's
    screen from this, not from SCR_WIDTH/SCR_HEIGHT (fullscreen only: at 1920x1080 with WIN_DATA=0 the game ran at
    640x480)."""
    left, top, right, bottom = WINDOW_BORDER
    body = struct.pack("<11i", 44, 0, 1, -1, -1, -1, -1, 0, 0, size[0] + left + right, size[1] + top + bottom)
    return body.hex().upper() + f"{sum(body) % 256:02X}"


def size_settings(size: tuple[int, int]) -> tuple[tuple[str, str, str], ...]:
    """The resolution: SCR_WIDTH/SCR_HEIGHT for fullscreen, WIN_DATA's window for the windowed game."""
    return (
        ("MAIN", "SCR_WIDTH", str(size[0])),
        ("MAIN", "SCR_HEIGHT", str(size[1])),
        ("MAIN", "WIN_DATA", win_data(size)),
    )


def fitting_size(area: tuple[int, int], sizes: tuple[tuple[int, int], ...] = SIZES) -> tuple[int, int]:
    """The largest size whose window (client and WINDOW_BORDER) fits `area`; the smallest when none does."""
    left, top, right, bottom = WINDOW_BORDER
    fits = [s for s in sizes if s[0] + left + right <= area[0] and s[1] + top + bottom <= area[1]]
    return max(fits, key=lambda s: s[0] * s[1]) if fits else min(sizes, key=lambda s: s[0] * s[1])


def default_size() -> tuple[int, int]:
    area = win32.work_area()
    return fitting_size((area.width, area.height))


def configured_size() -> tuple[int, int]:
    """SCR_WIDTH x SCR_HEIGHT as the instance's f1_res.ini has them."""
    text = (INSTANCE_DIR / "f1_res.ini").read_bytes().decode("latin1")
    w = re.search(r"^\s*SCR_WIDTH\s*=\s*(\d+)", text, re.MULTILINE)
    h = re.search(r"^\s*SCR_HEIGHT\s*=\s*(\d+)", text, re.MULTILINE)
    return (int(w.group(1)), int(h.group(1))) if w and h else (640, 480)


# The game's own settings for the agent: (section, key, value) in fallout.cfg.
CFG_SETTINGS = (
    # Sound on at the game's own default volume (a recorder registered as a session hook takes it with the picture).
    ("sound", "initialize", "1"),
    ("sound", "master_volume", "22281"),
    ("sound", "music_volume", "22281"),
    ("sound", "sndfx_volume", "22281"),
    ("sound", "speech_volume", "22281"),
    # Combat difficulty "Wimpy" (0): foes get -20 to hit and 75 % damage. The routes are made at this setting; it is
    # the game's own preference, set in the instance only (and in every save, flows.set_preferences).
    ("preferences", "combat_difficulty", "0"),
)

# f1_res_patcher.exe's message boxes (its own strings) and what they mean.
PATCHER_OK = "PATCHED Successfully"
PATCHER_FAILURES = ("NOT been patched", "NOT been installed", "may not be the correct version", "Unable to open")
PATCHER_UNDO = ("Do you wish to UNINSTALL", "already INSTALLED")


class InstanceError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(root: Path) -> dict[str, str]:
    """Relative path -> SHA-256 of every file under root."""
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()}


def is_copied(rel: str) -> bool:
    parts = Path(rel).parts
    return parts[0] not in SKIP_TOP and Path(*parts[:2]) != SAVEGAME


def set_ini_values(text: str, settings: tuple[tuple[str, str, str], ...]) -> str:
    """Set keys in an ini text in place, keeping comments, spacing and line endings. Each key must exist once."""
    wanted = {(s.upper(), k.upper()): v for s, k, v in settings}
    seen: set[tuple[str, str]] = set()
    section = ""
    out = []
    for line in text.splitlines(keepends=True):
        if m := re.match(r"\s*\[([^\]]+)\]", line):
            section = m.group(1).strip().upper()
        elif m := re.match(r"(\s*)([A-Za-z0-9_]+)(\s*=\s*)([^;\r\n]*?)([ \t]*(?:;[^\r\n]*)?)(\r?\n)?$", line):
            key = (section, m.group(2).upper())
            if key in wanted:
                if key in seen:
                    raise InstanceError(f"{key} appears twice")
                seen.add(key)
                line = f"{m.group(1)}{m.group(2)}{m.group(3)}{wanted[key]}{m.group(5)}{m.group(6) or ''}"
        out.append(line)
    if missing := set(wanted) - seen:
        raise InstanceError(f"keys not found: {sorted(missing)}")
    return "".join(out)


def byte_diff(a: bytes, b: bytes) -> list[tuple[int, int]]:
    """(offset, length) of every run of differing bytes; a length difference counts as one run at the end."""
    runs = []
    i, n = 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((n, abs(len(a) - len(b))))
    return runs


def _answer_patcher(proc: subprocess.Popen, timeout_s: float = 60) -> list[dict]:
    """Answer f1_res_patcher.exe's message boxes through window messages (no mouse): patch, never undo."""
    log: list[dict] = []
    handled: set[int] = set()
    end = time.monotonic() + timeout_s
    while proc.poll() is None and time.monotonic() < end:
        for hwnd in win32.top_windows(proc.pid):
            if hwnd in handled or win32.window_class(hwnd) != "#32770":
                continue
            kids = win32.child_windows(hwnd)
            text = " ".join(win32.window_text(k) for k in kids if win32.window_class(k) == "Static").strip()
            buttons = {win32.window_text(k).replace("&", ""): k for k in kids if win32.window_class(k) == "Button"}
            if not text or not buttons:
                continue  # not drawn yet
            if any(s in text for s in PATCHER_UNDO):
                choice = next(b for b in ("No", "Cancel") if b in buttons)
            else:
                choice = next(b for b in ("OK", "Yes") if b in buttons)
            log.append({"text": text, "buttons": sorted(buttons), "pressed": choice})
            win32.press_dialog_button(hwnd, buttons[choice])
            handled.add(hwnd)
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
        raise InstanceError(f"f1_res_patcher.exe did not finish; dialogs seen: {log}")
    return log


def patch_exe() -> tuple[list[dict], bytes, bytes]:
    """Run the bundled patcher on the instance's exe; return its dialogs and the exe before and after."""
    exe = INSTANCE_DIR / EXE
    before = exe.read_bytes()
    env = dict(os.environ, __COMPAT_LAYER="RunAsInvoker")  # its name says "patch": keep Windows from elevating it
    proc = subprocess.Popen([str(INSTANCE_DIR / "f1_res_patcher.exe")], cwd=INSTANCE_DIR, env=env)
    dialogs = _answer_patcher(proc)
    after = exe.read_bytes()
    texts = " ".join(d["text"] for d in dialogs)
    if any(s in texts for s in PATCHER_FAILURES) or PATCHER_OK not in texts:
        raise InstanceError(f"the patcher did not patch: {dialogs}")
    return dialogs, before, after


def running_from_instance() -> list[int]:
    """Processes started from any exe inside the instance (they hold its files open)."""
    root = str(INSTANCE_DIR).lower() + "\\"
    return [pid for pid in win32.all_pids() if (img := win32.process_image(pid)) and img.lower().startswith(root)]


def build(force: bool, game: str | Path | None = None, size: tuple[int, int] | None = None) -> dict:
    if pids := running_from_instance():
        raise InstanceError(f"processes run from the instance ({pids}); stop them first (python -m f1.session stop)")
    if (INSTANCE_DIR / EXE).exists() or (INSTANCE_DIR / "INSTANCE.json").exists():
        if not force:
            raise InstanceError(f"{INSTANCE_DIR} exists; --force rebuilds it (its saves are backed up first)")
        saves = INSTANCE_DIR / SAVEGAME
        if saves.exists() and any(saves.iterdir()):
            backup = BACKUPS / f"instance-{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}"
            shutil.copytree(saves, backup)
            print(f"instance saves backed up to {backup}")
        shutil.rmtree(INSTANCE_DIR)

    try:
        install = steam.find(game)
    except steam.GameNotFound as e:
        raise InstanceError(str(e)) from e
    source = install.folder
    exe_report = verify(source / EXE)
    if exe_report.sha256 != em.STEAM_EXE_SHA256 or not exe_report.ok:
        raise InstanceError(
            f"{source / EXE} is not the build the bot knows (Steam's FALLOUTW.EXE 1.1, sha256 "
            f"{em.STEAM_EXE_SHA256[:16]}...; this one {exe_report.sha256[:16]}...); see python -m f1.verify_exe"
        )
    print(f"fingerprinting {source} ...")
    fingerprinted = fingerprint(source)

    copied = {rel: digest for rel, digest in fingerprinted.items() if is_copied(rel)}
    print(f"copying {len(copied)} files ...")
    for rel, digest in copied.items():
        dst = INSTANCE_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dst)
        if sha256(dst) != digest:
            raise InstanceError(f"copy of {rel} does not match the original")
    (INSTANCE_DIR / SAVEGAME).mkdir(parents=True, exist_ok=True)

    print("patching the copy's FALLOUTW.EXE to load f1_res.dll ...")
    dialogs, before, after = patch_exe()
    patched = verify(INSTANCE_DIR / EXE)

    configure(size)

    manifest = {
        "built": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "steam_dir": str(source),
        "steam_build": steam.build_id(install.manifest),
        "steam_fingerprint": fingerprinted,
        "copied": copied,
        "exe": {
            "original_sha256": hashlib.sha256(before).hexdigest(),
            "patched_sha256": patched.sha256,
            "patch_runs": [[f"0x{off:X}", length] for off, length in byte_diff(before, after)],
            "map_checks_after_patch": [c.text for c in patched.checks if not c.ok] or "all pass",
        },
        "patcher_dialogs": dialogs,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def configure(size: tuple[int, int] | None = None) -> dict[str, str]:
    """Apply the bot's settings to the instance's f1_res.ini and fallout.cfg (idempotent), the resolution too (by
    default the largest that fits the screen)."""
    size = size or default_size()
    applied = {}
    for name, settings in (("f1_res.ini", HRP_SETTINGS + size_settings(size)), ("fallout.cfg", CFG_SETTINGS)):
        path = INSTANCE_DIR / name
        path.write_bytes(set_ini_values(path.read_bytes().decode("latin1"), settings).encode("latin1"))
        applied |= {f"{name}:{s}.{k}": v for s, k, v in settings}
    return applied


def music_on() -> None:
    """fallout.cfg's music volume back to the game's default before a launch: the Preferences' Done writes the
    bot's music-off into it, and a game started so would play its movies silent (their volume is the background
    volume at startup, gmovie.cc gmovie_init); the loaded save's preferences turn the music off again."""
    path = INSTANCE_DIR / "fallout.cfg"
    settings = tuple(s for s in CFG_SETTINGS if s[1] == "music_volume")
    path.write_bytes(set_ini_values(path.read_bytes().decode("latin1"), settings).encode("latin1"))


def is_game_file(rel: str) -> bool:
    """A file of the game itself, which nothing should change: not a save, not the settings playing the game writes."""
    return Path(*Path(rel).parts[:2]) != SAVEGAME and Path(rel).name.lower() not in ("fallout.cfg", "f1_res.ini")


def check() -> list[str]:
    """Problems found; empty means the instance and the install's game files are as recorded."""
    if not MANIFEST.exists():
        return [f"no instance manifest at {MANIFEST}; run python -m f1.instance build"]
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = []
    now = {rel: d for rel, d in fingerprint(Path(m["steam_dir"])).items() if is_game_file(rel)}
    then = {rel: d for rel, d in m["steam_fingerprint"].items() if is_game_file(rel)}
    for rel in sorted(set(now) | set(then)):
        if now.get(rel) != then.get(rel):
            problems.append(f"the install changed since the build: {rel}")
    if sha256(INSTANCE_DIR / EXE) != m["exe"]["patched_sha256"]:
        problems.append("the instance's FALLOUTW.EXE is not the patched build recorded at build time")
    for rel in (r for r in m["copied"] if r != EXE and r.endswith((".DAT", ".dll", ".exe"))):
        path = INSTANCE_DIR / rel
        if not path.exists():
            problems.append(f"instance file missing: {rel}")
        elif sha256(path) != m["copied"][rel]:
            problems.append(f"instance file changed: {rel}")
    return problems


def parse_size(text: str) -> tuple[int, int]:
    w, h = (int(v) for v in text.lower().split("x"))
    return w, h


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m f1.instance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--force", action="store_true")
    b.add_argument("--game", help="the game's folder (default: found through Steam)")
    b.add_argument("--size", type=parse_size, help="the game's resolution WxH (default: the largest that fits)")
    sub.add_parser("configure").add_argument("--size", type=parse_size)
    sub.add_parser("check")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "build":
            m = build(args.force, args.game, args.size)
            print(json.dumps({k: v for k, v in m.items() if k not in ("steam_fingerprint", "copied")}, indent=1))
            return 0
        if args.cmd == "configure":
            print(json.dumps(configure(args.size), indent=1))
            return 0
        problems = check()
        print("\n".join(problems) if problems else "instance and install as recorded")
        return 1 if problems else 0
    except InstanceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
