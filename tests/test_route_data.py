"""The routes' hand-typed data read back against the game's own files: every dialogue plan phrase
must be a line some character can say, and in the .MSG file its comment names when it names one. A phrase typed
wrong, or copied from a walkthrough instead of the game, is otherwise found only live, when the talk takes the wrong
turn. (Tile constants are not checked: many are hexes to stand on, a cab or a room, not objects.)"""

import ast
import re
from pathlib import Path

import pytest

from f1 import paths
from f1.dat import GameFiles
from f1.knowledge import parse_msg

ROOT = Path(__file__).resolve().parent.parent
INSTANCE = paths.INSTANCE
ROUTE_MODULES = (
    "f1/routes.py",
    "f1/idealist.py",
    "f1/boneyard.py",
    "f1/military.py",
    "f1/raiders.py",
    "f1/watershed.py",
)


def plan_lists(path: Path) -> list[tuple[int, str, list[str], list[str]]]:
    """(line, name, phrases, .MSG files named on its lines) of every module-level UPPER_CASE list or tuple of
    lower-case strings: the dialogue plans (dialogue.pick matches them against the options, case aside)."""
    src = path.read_text(encoding="utf-8")
    lines = src.split("\n")
    out = []
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        name = getattr(node.targets[0], "id", "")
        values = node.value.elts
        if not name.isupper() or not values:
            continue
        if not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in values):
            continue
        phrases = [v.value for v in values]
        if any(p != p.lower() for p in phrases):  # names of maps or critters, not plans
            continue
        text = " ".join(lines[node.lineno - 1 : node.end_lineno])
        out.append((node.lineno, name, phrases, [f.upper() for f in re.findall(r"([A-Za-z0-9_]+)\.MSG", text)]))
    return out


def test_the_plan_finder_sees_the_plans() -> None:
    names = {name for path in ROUTE_MODULES for _l, name, _p, _f in plan_lists(ROOT / path)}
    assert {"HARRY_PEACE", "GARL_FREE", "THERESA_CALM", "KATRINA_QUESTIONS"} <= names
    assert "TOWN_MAPS" not in names and "GIZMO_GANG" not in names


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
def test_every_plan_phrase_is_a_line_of_the_game() -> None:
    files = GameFiles(INSTANCE)
    said: dict[str, str] = {}
    for name in files.names("TEXT/ENGLISH/DIALOG/"):
        msgs = parse_msg(files.read(name.replace("\\", "/")))
        said[Path(name.replace("\\", "/")).stem.upper()] = "\n".join(
            re.sub(r"\s+", " ", t.lower()) for t in msgs.values()
        )
    everything = "\n".join(said.values())
    wrong = []
    for path in ROUTE_MODULES:
        for line, name, phrases, named in plan_lists(ROOT / path):
            for phrase in phrases:
                if phrase not in everything:
                    wrong.append(f"{path}:{line} {name}: {phrase!r} is in no dialogue file")
                elif named and not any(phrase in said.get(f, "") for f in named):
                    wrong.append(f"{path}:{line} {name}: {phrase!r} is not in {', '.join(named)}")
    assert wrong == []
