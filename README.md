# overseer

A bot that plays **Fallout (1997)** on Windows, on your own copy of the game. It reads the game's memory to know
exactly what is going on (the map, the player, every critter, the dialogue, the quest variables) and plays through
the game's own window with ordinary mouse and keyboard input. There is no AI model in the loop: the quests are routes
written as data, and every action is checked against the game's memory before the next one.

The Idealist, its main character, plays the whole game from character creation to the end: the water chip, the
towns' quests on their good paths, the Military Base and the Cathedral.

Videos of it playing: [Who Plays It Anyway?](https://www.youtube.com/@whoplaysitanyway) on YouTube.

> **A hobby project, best effort.** It is tested on one PC, with one version of the game. Bug reports are welcome,
> but there is no promise of support.

## What you need

- **Windows 10 or 11**, 64-bit, display scaling at 100 % (other scaling is untested).
- **Python 3.12 or newer**, 64-bit.
- **Fallout from Steam** (app 38400). The bot knows one build of the game exactly: Steam's `FALLOUTW.EXE`,
  version 1.1 (SHA-256 `444257e5fe91e6c333261aecadacb1f46dbba0319fa0b187de89f36914f32272`), with the High
  Resolution Patch 4.1.8 that Steam ships next to it. Other builds (GOG, other languages, 1.2) are refused, because
  every memory address the bot uses belongs to that one build.
- About 600 MB of disk for the bot's copy of the game.

## Install

```
git clone https://github.com/tymsky/overseer.git
cd overseer
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
```

## Set up and play

```
.venv\Scripts\python -m f1.play setup
.venv\Scripts\python -m f1.play
```

`setup` finds the game through Steam (or give its folder: `--game "D:\SteamLibrary\steamapps\common\Fallout"`),
copies it into `instance/`, makes the copy load the High Resolution Patch in a window, and reads the game's
prototypes, variables and maps into `extracted/knowledge.json`. The window is the largest of 1920x1080, 1280x720 and
640x480 that fits your screen (`--size WxH` to choose; 1920x1080 and 640x480 are the sizes whole runs were made
at).

`play` creates the Idealist in the game's character editor, sets the bot's preferences, saves on the first map
(kept as `saves/idealist-start`), and then plays its routes one after another. `play agent` plays the first
character's shorter chain (Shady Sands, the water chip, Junktown, Tandi). `--until N` stops before step N.

**While it plays, leave the mouse and keyboard alone.** The bot sends real input to the game's window, so the
window must stay in front and the PC unlocked and awake. When you touch the mouse or keyboard, the presence guard
pauses the bot until you stop; it gives up after two minutes of use.

## What it does to your PC

- It **never writes to your Steam install**. It copies it into `instance/` (without your saves) and plays only the
  copy; `python -m f1.instance check` confirms the install's game files are as they were.
- In the copy, Steam's own `f1_res_patcher.exe` makes `FALLOUTW.EXE` load the High Resolution Patch, and the copy's
  `f1_res.ini` and `fallout.cfg` get a window, mouse input from Windows messages, and the easiest combat difficulty (the Idealist also plays at the Easy game difficulty).
- It reads the game process's memory (and writes it only for `--clock`, below). It starts and stops the game itself.
- `--clock K` (a development option) makes the game run K times as fast by redirecting its clock function: it
  patches the process's import table and starts a thread in it. Security software may flag that. The bot never
  does it unless asked.
- Everything it makes stays in this folder: `instance/`, `extracted/`, `runs/` (event logs), `saves/` (kept saves
  and backups), `captures/`. Set `OVERSEER_HOME` to keep them elsewhere.

## Other commands

| Command | What it does |
|---|---|
| `python -m f1.verify_exe [EXE]` | checks an exe against the memory map, without running it |
| `python -m f1.instance build\|configure\|check` | the game copy: build it, set it up again (`--size`), check it |
| `python -m f1.smoke` | a short live check: start the game, read its menu from memory, quit |
| `python -m f1.session start\|status\|state\|shot NAME\|stop` | start the game, look at it, stop it |
| `python -m f1.flows load NAME` | load a kept save from `saves/` in a new session |
| `python -m f1.routes ROUTE... [--from N] [--until N]` | run routes by name (see `f1/routes.py`) |
| `python -m f1.chargen [idealist\|agent]` | enter a character in the game's editor |
| `python -m f1.travel [OUTDOORSMAN [PATHFINDER]]` | days of world-map travel between the towns, by the game's rules |
| `python -m f1.savegame SAVE [GVAR ...]` | a save's quest state, offline |

## How it works, briefly

- `f1/engine_map.py` holds the addresses and structure layouts of the 1.1 exe; `f1/state.py` reads a snapshot of
  the game from them; `python -m f1.verify_exe` shows the evidence for each address.
  [docs/FORMATS.md](docs/FORMATS.md) has the game's file formats as the readers use them.
- `f1/actions.py` has the verified actions (walk to a tile, talk, choose a reply, use an item, attack), each with a
  precondition, input, a wait on the game's state and a check. `f1/nav.py` finds paths on the hex grid.
- `f1/routes.py` runs a route: steps with their own checks, a save at each checkpoint, a reload and retry when a step
  fails or the character dies. The routes themselves are in `f1/routes.py`, `f1/idealist.py` and the late-game
  modules.

## Credits

- [fallout1-ce](https://github.com/alexbatalov/fallout1-ce) by Alexander Batalov, a re-implementation of the
  Fallout 1 engine that notes the original binary's address above each function and global. overseer's memory map
  was built by checking those addresses against the 1.1 exe, and its source was the documentation for the engine's
  rules (the comments that name an engine file or function point there). No code is taken from it.
- The [High Resolution Patch](https://github.com/mattwells77/Fallout-High-Resolution-Patches) by Mash (Matt Wells),
  which Steam ships with the game: it gives the bot its window and its mouse input.

## Legal

overseer is not affiliated with or endorsed by Bethesda Softworks, ZeniMax, Microsoft or Interplay. Fallout is
their trademark. This repository contains no game files and nothing extracted from them; you need your own, legally
obtained copy of the game.

## License

[GPL-3.0-or-later](LICENSE).
