"""Talk plans name options by message number, never by the game's text: a guard over every plan in f1/, and plans
by number resolved on made-up screens (plan_index, converse_strict, converse)."""

import ast
from pathlib import Path

from f1 import dialogue, intdump, knowledge
from f1.dialogue import Dialogue, Option

F1 = Path(dialogue.__file__).parent
# the calls that take a plan, and the module-level names that hold plans
PLAN_CALLS = {"talk", "converse", "converse_strict", "settle", "guarded_walk", "buy", "far_go"}


def _constants() -> dict[str, ast.expr]:
    out = {}
    for path in F1.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
            if isinstance(target, ast.Name) and target.id.isupper():
                out[f"{path.stem}.{target.id}"] = node.value
    return out


def _strings(expr: ast.expr, module: str, constants: dict[str, ast.expr], seen: set) -> list[str]:
    """The string items of a plan expression: a list literal, a constant's name (this module's or an imported one),
    or their sum."""
    if isinstance(expr, ast.List | ast.Tuple):
        return [e.value for e in expr.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _strings(expr.left, module, constants, seen) + _strings(expr.right, module, constants, seen)
    if isinstance(expr, ast.Name) and expr.id.isupper() and expr.id not in seen:
        seen.add(expr.id)
        keys = [f"{module}.{expr.id}"] + [k for k in constants if k.endswith(f".{expr.id}")]
        value = next((constants[k] for k in keys if k in constants), None)
        return _strings(value, module, constants, seen) if value is not None else []
    return []


def _plans() -> list[tuple[str, str]]:
    constants, out = _constants(), []
    for path in F1.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in PLAN_CALLS:
                continue
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                where = f"{path.name}:{node.lineno}"
                out += [(where, s) for s in _strings(arg, path.stem, constants, set())]
    return out


def test_every_talk_plan_names_messages_by_number() -> None:
    plans = _plans()
    assert len(plans) > 250  # the guard sees the plans (Shady Sands to the Cathedral)
    phrases = [(where, item) for where, item in plans if not dialogue.is_id(item)]
    assert phrases == []


def _fake_lists(monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "load", lambda: {"scripts": ["alpha", "beta", "gamma"]})
    texts = {1: {101: "First line.", 102: "Second line."}, 2: {101: "Other list.", 104: "My name is "}}
    monkeypatch.setattr(intdump, "messages", lambda list_id: texts.get(list_id, {}))


def _screen(options: list[tuple[int, int, str]], script: str = "beta") -> Dialogue:
    entries = tuple(Option(i, lst, msg, 0, text) for i, (lst, msg, text) in enumerate(options))
    return Dialogue(True, "Someone", "r", [o.text for o in entries], 2, 100, entries, script, 0x1000)


def test_plan_items_by_number_on_a_made_up_screen(monkeypatch) -> None:
    _fake_lists(monkeypatch)
    d = _screen([(1, 101, "First line."), (2, 101, "Other list."), (-4, 0, "My name is Ann.  Hi."), (1, 102, "x")])
    assert dialogue.plan_index(d, "#102") == 3
    assert dialogue.plan_index(d, "#999/102/101") == 3  # the first of the group that is on screen
    assert dialogue.plan_index(d, "#101") == 0  # any list: the first on screen
    assert dialogue.plan_index(d, "beta#101") == 1  # only beta's message file
    assert dialogue.plan_index(d, "gamma#101") is None
    assert dialogue.plan_index(d, "@104") == 2  # a text option built from the talk's script's message 104
    assert dialogue.plan_index(d, "alpha@104") is None
    assert dialogue.plan_index(d, "#103") is None
    assert [dialogue.is_id(p) for p in ("#1", "#1/2", "morph#103", "@7", "x@7", "thanks.", "#", "#a")] == [
        True, True, True, True, True, False, False, False,
    ]  # fmt: skip


def test_converse_strict_and_converse_follow_plans_by_number(monkeypatch) -> None:
    _fake_lists(monkeypatch)
    first = _screen([(1, 101, "First line."), (1, 102, "x")])
    second = _screen([(2, 101, "Other list."), (2, 104, "y")])
    for converse in (dialogue.converse_strict, dialogue.converse):
        screens, chosen = [first, second], []

        def read(_mem, screens=screens) -> Dialogue:
            return screens[0] if screens else Dialogue(False, "", "", [])

        def choose(_mem, index: int, screens=screens, chosen=chosen) -> Dialogue:
            chosen.append((screens[0].entries[index].list_id, screens[0].entries[index].message))
            screens.pop(0)
            return read(None)

        monkeypatch.setattr(dialogue, "read", read)
        monkeypatch.setattr(dialogue, "choose", choose)
        converse(None, ["#102", "beta#104"])
        assert chosen == [(1, 102), (2, 104)], converse.__name__
