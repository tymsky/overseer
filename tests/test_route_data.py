"""The routes' hand-typed data read back against the game's own files: every dialogue plan item names messages
(f1.dialogue.plan_index: "#N", "SCRIPT#N", "@N") that exist, in the .MSG file its comment names when it names one.
A number typed wrong is otherwise found only live, when the talk takes the wrong turn. (Tile constants are not
checked: many are hexes to stand on, a cab or a room, not objects.)"""

import ast
import re
from pathlib import Path

import pytest

from f1 import dialogue, paths
from f1.dat import GameFiles
from f1.knowledge import parse_msg

ROOT = Path(__file__).resolve().parent.parent
INSTANCE = paths.INSTANCE
ROUTE_MODULES = (
    "f1/routes.py",
    "f1/idealist.py",
    "f1/route_support.py",
    "f1/route_runner.py",
    "f1/agent_sands.py",
    "f1/agent_junktown.py",
    "f1/agent_hub.py",
    "f1/agent_necropolis.py",
    "f1/agent_glow.py",
    "f1/agent_brotherhood.py",
    "f1/agent_boneyard.py",
    "f1/agent_cathedral.py",
    "f1/agent_military.py",
    "f1/idealist_support.py",
    "f1/idealist_sands.py",
    "f1/idealist_vault15.py",
    "f1/idealist_junktown.py",
    "f1/idealist_hub.py",
    "f1/idealist_necropolis.py",
    "f1/idealist_vault13.py",
    "f1/idealist_trading.py",
    "f1/idealist_glow.py",
    "f1/idealist_brotherhood.py",
    "f1/idealist_hub_rescue.py",
    "f1/boneyard.py",
    "f1/military.py",
    "f1/raiders.py",
    "f1/watershed.py",
    "f1/cathedral.py",
)


def plan_lists(path: Path) -> list[tuple[int, str, list[str], list[str]]]:
    """(line, name, items, .MSG files named on its lines) of every module-level UPPER_CASE list or tuple of
    lower-case strings: the dialogue plans."""
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


def test_every_regional_module_scope_is_checked() -> None:
    regional = {
        path.relative_to(ROOT).as_posix()
        for pattern in ("agent_*.py", "idealist*.py", "route_*.py")
        for path in (ROOT / "f1").glob(pattern)
    }
    assert regional <= set(ROUTE_MODULES)


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
def test_every_plan_item_is_a_message_of_the_game() -> None:
    files = GameFiles(INSTANCE)
    said: dict[str, dict[int, str]] = {}
    for name in files.names("TEXT/ENGLISH/DIALOG/"):
        said[Path(name.replace("\\", "/")).stem.upper()] = parse_msg(files.read(name.replace("\\", "/")))
    wrong = []
    for path in ROUTE_MODULES:
        for line, name, items, named in plan_lists(ROOT / path):
            for item in items:
                m = dialogue.PLAN_ID.fullmatch(item)
                if m is None:
                    wrong.append(f"{path}:{line} {name}: {item!r} is not a message number")
                    continue
                where = [m.group(1).upper()] if m.group(1) else named or list(said)
                for n in (int(k) for k in m.group(3).split("/")):
                    if not any(n in said.get(f, {}) for f in where):
                        wrong.append(f"{path}:{line} {name}: {item!r}: no message {n} in {', '.join(where[:3])}")
    assert wrong == []
