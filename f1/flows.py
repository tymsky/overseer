"""Multi-step ways through the game's screens, each step confirmed from memory before the next."""

import time
from pathlib import Path

from f1 import clock, frame, paths, session, state, ui
from f1 import engine_map as em
from f1.memory import GameMemory


class FlowError(RuntimeError):
    pass


def wait_screen(mem: GameMemory, screens: set[str], timeout_s: float, poke: bool = False) -> state.Snapshot:
    """Wait until the game shows one of `screens`; with `poke`, click every 2 s (skips movies meanwhile)."""
    end = time.monotonic() + timeout_s
    last_poke = time.monotonic()
    while True:
        snap = state.read(mem)
        if snap.screen in screens:
            return snap
        if time.monotonic() > end:
            raise FlowError(f"still on {snap.screen!r} after {timeout_s} s, waiting for {sorted(screens)}")
        if poke and time.monotonic() - last_poke > 2:
            session.click(320, 240)
            last_poke = time.monotonic()
        time.sleep(0.25)


def to_main_menu(pid: int, tries: int = 6) -> state.Snapshot:
    """To the main menu from wherever: out of a game by F10 and Y, or past the start-up movies with clicks. In
    combat the keys only count in the player's turn (or once the player is dead), so it waits for that and tries
    again."""
    with GameMemory(pid) as mem:
        for _ in range(tries):
            screen = state.detect_screen(mem)
            if screen == "main_menu":
                return state.read(mem)
            if screen in ("none",) or screen.startswith("window"):
                return wait_screen(mem, {"main_menu"}, 60, poke=True)
            if screen in ("barter", "dialogue"):  # F10 does nothing there
                from f1 import dialogue

                dialogue.close(mem)
                continue
            if mem.glob("combat_state") & 0x01:  # someone else's turn: wait for ours (or the end)
                dude = mem.u32(em.GLOBALS["obj_dude"].address)
                end = time.monotonic() + 60
                while time.monotonic() < end and mem.glob("combat_state") & 0x01:
                    turn = mem.u32(em.GLOBALS["combat_turn_obj"].address)
                    if turn == dude and mem.glob("combat_turn_running") == 0:
                        break
                    if state.detect_screen(mem) == "main_menu":
                        return state.read(mem)
                    time.sleep(0.2)
            session.press("f10")
            time.sleep(0.8)
            session.press("y")
            try:
                return wait_screen(mem, {"main_menu"}, 12)
            except FlowError:
                continue
        return wait_screen(mem, {"main_menu"}, 30)


def new_game(pid: int, premade_steps: int = 0) -> state.Snapshot:
    """Main menu -> New Game -> a premade character (Take) -> skip the Overseer's movie -> on the first map.

    `premade_steps` presses the right arrow that many times first (0 = the first premade the screen shows).
    """
    with GameMemory(pid) as mem:
        wait_screen(mem, {"main_menu"}, 5)
        session.press("n")
        wait_screen(mem, {"select_character"}, 15)
        time.sleep(1.0)  # the screen fades in
        for _ in range(premade_steps):
            session.press("right")
            time.sleep(0.4)
        session.press("t")
        snap = wait_screen(mem, {"map"}, 90, poke=True)
        # The map is loaded when the player stands on a real tile; give the fade-in a moment.
        end = time.monotonic() + 20
        while (snap.dude is None or snap.dude.tile <= 0) and time.monotonic() < end:
            time.sleep(0.5)
            snap = state.read(mem)
        time.sleep(1.5)
        return state.read(mem)


GOLDEN = paths.SAVES


def restore_golden(name: str, slot: int = 1) -> Path:
    """Copy a kept save (saves/<name>/SLOT01) into the instance's slot; the slot's old files are backed
    up next to it first. The game must not be running (it caches the slot list)."""
    import shutil

    from f1 import instance

    src = GOLDEN / name / "SLOT01"
    dst = instance.INSTANCE_DIR / "DATA" / "SAVEGAME" / f"SLOT{slot:02d}"
    if not (src / "SAVE.DAT").exists():
        raise FlowError(f"no kept save {src}")
    if session.game_pid():
        raise FlowError("the game is running: it caches the slot list, and its slot must not change under it")
    if dst.exists():
        shutil.copytree(dst, GOLDEN / f"replaced-{time.strftime('%Y%m%d-%H%M%S')}" / dst.name)
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


# The agent's preferences (development runs: every speed at its maximum, combat difficulty Wimpy). Each
# save holds its own copy (options.c save_options/load_options): loading a save brings back that save's settings,
# so they are set in the game's Preferences screen and a save made afterwards keeps them.
# The music off: recordings carry only the game's effects and speech; music can be laid under
# them later from the game's files. Movies keep their sound: session.start puts fallout.cfg's
# music volume back before every launch, and a movie's volume is the one read at startup (gmovie.cc gmovie_init).
AGENT_PREFS = {"combat_difficulty": 0, "combat_speed": 50, "prf_running": 1, "player_speedup": 1, "music_volume": 0}
# The Idealist's (the game difficulty Easy, for now): +20 to the
# twelve non-combat skills (skill.c skill_game_difficulty) and nothing else a script reads.
IDEALIST_PREFS = AGENT_PREFS | {"game_difficulty": 0}
PREFS = {"Agent": AGENT_PREFS, "Idealist": IDEALIST_PREFS}
BY_CHARACTER = "character"  # as `prefs`: the preferences of the character in the game (PREFS by its name)


def prefs_for(mem: GameMemory) -> dict[str, int]:
    """The preferences of the character in the game; the Agent's for a name PREFS does not know."""
    return PREFS.get(state.character_name(mem), AGENT_PREFS)


def set_preferences(pid: int, wanted: dict[str, int] = AGENT_PREFS) -> dict:
    """The options menu (O), its Preferences (P), each control changed until memory shows the wanted value, then
    Done (Enter: options.c SavePrefs(1) also writes fallout.cfg) and back to the map. Controls (options.c btndat, in
    the window, which is at (0, 0) at 640x480 and centred at bigger resolutions): a difficulty knob (the game's at
    (76, 71), combat's at (76, 149)) turns one value a click (Normal, Rough, Normal, Wimpy...), the running knob
    toggles, the player-speed checkbox (code 524) toggles, combat speed is a slider (x 384..603 for 0..50 at y 50)
    dragged to its place, the music volume a slider on the same x span for 0..32767 at y 247."""
    knobs = {
        "game_difficulty": (76 + 23, 71 + 21),
        "combat_difficulty": (76 + 23, 149 + 21),
        "prf_running": (299 + 11, 271 + 12),
    }
    with GameMemory(pid) as mem:
        session.press("o")
        wait_screen(mem, {"options"}, 5)
        session.press("p")
        wait_screen(mem, {"preferences"}, 5)
        time.sleep(0.5)
        x0, y0 = ui.origin(mem, mem.glob("prfwin")) or (0, 0)
        for name, want in wanted.items():
            for _ in range(4):
                if mem.glob(name) == want:
                    break
                if name in knobs:
                    session.click(x0 + knobs[name][0], y0 + knobs[name][1])
                elif name == "player_speedup":
                    box = ui.find(mem, mem.glob("prfwin"), 524)
                    session.click(*(box.center if box else (x0 + 390, y0 + 75)))
                elif name == "combat_speed":
                    now, y = mem.glob(name), y0 + 50
                    session.drag(x0 + 384 + now * 219 // 50 + 10, y, x0 + 384 + want * 219 // 50 + 6, y)
                elif name == "music_volume":
                    now, y = mem.glob(name), y0 + 247
                    to = x0 + 380 if want == 0 else x0 + 384 + want * 219 // 32767 + 6  # past the left end: 0
                    session.drag(x0 + 384 + now * 219 // 32767 + 10, y, to, y)
                time.sleep(0.5)
        got = {name: mem.glob(name) for name in wanted}
        session.press("enter")  # Done
        wait_screen(mem, {"options"}, 5)
        session.press("esc")
        wait_screen(mem, {"map"}, 5)
        return {"ok": got == wanted, "prefs": got}


def keep_save(name: str, slot: int = 1) -> Path:
    """Copy the instance's slot to saves/<name>/SLOT01, the other way round from restore_golden."""
    import shutil

    from f1 import instance

    src = instance.INSTANCE_DIR / "DATA" / "SAVEGAME" / f"SLOT{slot:02d}"
    dst = GOLDEN / name / "SLOT01"
    if not (src / "SAVE.DAT").exists():
        raise FlowError(f"no save in {src}")
    if dst.exists():
        raise FlowError(f"kept save {name} exists already")

    # a save renames the slot's files to .BAK and writes them anew: a copy made right after the quicksave held
    # SAVE.DAT at 29 696 of 40 105 bytes and seven maps only as .BAK, and would not load ("Error loading game!").
    # Copied once the folder has stood still for 2 s, the .BAK files left out
    def look() -> tuple:
        return tuple(sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in src.iterdir()))

    seen, since = look(), time.monotonic()
    while time.monotonic() - since < 2.0:
        time.sleep(0.25)
        now = look()
        if now != seen:
            seen, since = now, time.monotonic()
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.BAK"))
    return dst


def load_from_menu(pid: int, slot: int = 1, prefs: dict[str, int] | str | None = BY_CHARACTER) -> state.Snapshot:
    """Main menu -> Load Game -> the slot -> on its map; then our preferences (by default those of the character the
    save holds), when the save brought back others."""
    # The menus at the game's own speed: at a faster clock the main menu's idle wait for its intro movie runs out
    # in seconds (50x: the movie in turns with the menu).
    with clock.held(pid):
        to_main_menu(pid)
        with GameMemory(pid) as mem:
            session.press("l")
            wait_screen(mem, {"loadsave"}, 10)
            time.sleep(0.8)
            for _ in range(slot - 1):
                session.press("down")
            session.press("enter")
            wait_screen(mem, {"map", "worldmap"}, 60)  # a save made on the world map loads there
            time.sleep(1.5)
    with GameMemory(pid) as mem:
        if prefs == BY_CHARACTER:
            prefs = prefs_for(mem)
        differ = prefs and any(mem.glob(name) != value for name, value in prefs.items())
    if differ:
        set_preferences(pid, prefs)
    with GameMemory(pid) as mem:
        return state.read(mem)


# M2's screen tour from the map: key that opens the screen, the screen detect_screen() must report, key that closes.
TOUR = (
    ("c", "character", "esc"),
    ("i", "inventory", "esc"),
    ("p", "pipboy", "esc"),
    ("s", "skilldex", "esc"),
    ("o", "options", "esc"),
)


def tour(pid: int, out_dir: Path) -> list[dict]:
    """Open each TOUR screen from the map, confirm it from memory, compose its picture, close it, confirm the map."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with GameMemory(pid) as mem:
        for key, expected, close in TOUR:
            wait_screen(mem, {"map"}, 5)
            if mem.glob("combat_state") & 0x01:
                raise FlowError("in combat: the tour needs the map at peace")
            session.press(key)
            try:
                seen, ok = wait_screen(mem, {expected}, 4).screen, True
            except FlowError:
                seen, ok = state.read(mem).screen, False
            time.sleep(0.5)
            frame.compose(mem).save(out_dir / f"tour-{expected}.png")
            session.press(close)
            try:
                wait_screen(mem, {"map"}, 4)
                closed = True
            except FlowError:
                closed = False
            results.append({"key": key, "expected": expected, "seen": seen, "ok": ok, "closed": closed})
    return results


def start_from(name: str) -> state.Snapshot:
    """A kept save played from its start: the game stopped by its own exit, the save into slot 1, a new session
    (recorded) and the save loaded from the main menu with the agent's preferences."""
    if session.game_pid() and session.stop().get("alive"):
        raise FlowError("the game would not stop")
    restore_golden(name)
    pid = session.start()["pid"]
    return load_from_menu(pid)


def main(argv: list[str]) -> int:
    """python -m f1.flows newgame | agentstart | load NAME | tour"""
    if len(argv) == 2 and argv[0] == "load":
        snap = start_from(argv[1])
        print(snap.to_dict())
        return 0 if snap.screen == "map" else 1
    if argv == ["tour"]:
        pid = session.game_pid()
        out = paths.CAPTURES / time.strftime("%Y-%m-%d")
        results = tour(pid, out)
        for r in results:
            print(r)
        good = sum(r["ok"] and r["closed"] for r in results)
        print(f"=> {good}/{len(results)} screens detected and closed (pictures in {out})")
        return 0 if good == len(results) else 1
    if argv[:1] == ["agentstart"]:  # the Agent at the start of the game
        snap = start_from("agent-start")
        print(snap.to_dict())
        return 0 if snap.screen == "map" else 1
    if argv != ["newgame"]:
        print("usage: python -m f1.flows newgame | agentstart | load NAME | tour")
        return 2
    pid = session.game_pid() or session.start()["pid"]
    to_main_menu(pid)
    snap = new_game(pid)
    print(snap.to_dict())
    return 0 if snap.screen == "map" and snap.dude and snap.dude.tile > 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
