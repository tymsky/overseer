"""Where the bot keeps its files: the game copy, the knowledge extracted from it, runs, saves and captures.

HOME is, in this order: the environment variable OVERSEER_HOME; a folder another installed package registers under
the entry-point group `overseer.home` (a string path); this repo's own folder. Under HOME:

    instance/    the copy of the game the bot plays (f1.instance build); never the Steam install itself
    extracted/   knowledge.json and the scripts, taken from the game's files (f1.knowledge build, f1.intdump extract)
    runs/        one folder per run: events.jsonl and what the run wrote
    saves/       kept saves (flows.keep_save, flows load NAME) and the save backups taken before each session
    captures/    pictures (session shot, smoke)

None of it goes into git: it is the game's own material or made from it.
"""

import os
from importlib.metadata import entry_points
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _home() -> Path:
    if env := os.environ.get("OVERSEER_HOME"):
        return Path(env)
    found = list(entry_points(group="overseer.home"))
    if len(found) > 1:
        raise RuntimeError(f"more than one overseer.home registered: {[ep.value for ep in found]}")
    return Path(found[0].load()) if found else REPO


HOME = _home()
INSTANCE = HOME / "instance"
EXTRACTED = HOME / "extracted"
RUNS = HOME / "runs"
SAVES = HOME / "saves"
CAPTURES = HOME / "captures"
