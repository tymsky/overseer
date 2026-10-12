"""A talk's tree from its decompiled script (f1.intdump): the nodes (the script's procedures), what each shows
(gsay_reply: a reply with options; gsay_message: a line with a [Done]), its options (giq_option, gsay_option: the
message, the node it leads to, the IQ it asks, the reaction), the nodes it calls, and the conditions that guard
each of them where they can be read (the if/else ranges of the procedure's code).

The engine's side (fallout1-ce gdialog.c, intextra.c; the addresses checked in FALLOUTW.EXE, f1.dialogue): a reply
sets the reply's message and clears the options; each option shown keeps its message and its procedure's index
(gDialogOptionProc); taking it runs that procedure (gDialogProcessChoice), and the talk ends when the procedure
leaves no option. A giq_option is shown when the player's IN (plus Smooth Talker) is at least its IQ, or, for a
negative IQ, at most its opposite. A gsay_message blocks inside its procedure until its [Done] (option list -2),
then the procedure goes on.

So a place in a talk is a screen: (the callers to return to, the node, the item that shows it), and what comes
next is computed from the code: after an option, the first screens its procedure reaches; after a [Done], the
screens after that line in its node, then in its callers. Where a branch's condition is not read, every branch's
screen is a candidate (a set); END stands for the talk's end, and OPEN for a return into a caller not known (a talk
located mid-way).

    python -m f1.talktree NAME [NODE ...]    the nodes (every one, or the named), their screens and options
"""

import ast
import operator
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from f1 import intdump

DONE_LIST, EMPTY_LIST, STRING_LIST = -2, -1, -4  # gsay_message's [Done]; the engine's blank option; text options
END, OPEN = "END", "OPEN"  # predictions: the talk ends; a return into a caller not known
SCREENS = ("reply", "say")
MAX_DEPTH = 16


@dataclass(frozen=True)
class Item:
    """One step of a node, in code order: a reply or a line (message), an option (message, target, iq, reaction),
    a call of another node (target), or a variable set (text)."""

    kind: str  # "reply", "say", "option", "call", "set"
    offset: int
    when: tuple[str, ...] = ()  # the conditions read around it (empty: always)
    list_id: int | None = None
    message: int | None = None
    target: str = ""
    iq: int | None = None
    reaction: int | None = None
    text: str = ""


@dataclass(frozen=True)
class Node:
    name: str
    index: int
    items: tuple[Item, ...]

    def of(self, kind: str) -> tuple[Item, ...]:
        return tuple(i for i in self.items if i.kind == kind)

    @property
    def options(self) -> tuple[Item, ...]:
        return self.of("option")


Position = tuple[tuple[tuple[str, int], ...], str, int]  # (callers: (node, next item)), node, item index


def _value(e) -> int | None:
    """A constant argument's value; a negated constant (the IQ -3 of a dumb option) is pushed then negated."""
    v = getattr(e, "value", None)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    text = str(e)
    return int(text) if text.lstrip("-").isdigit() and text.count("-") <= 1 else None


def _guards(events: list) -> list[tuple[int, int, str]]:
    """(start, end, condition) ranges: an `if not C goto T` holds C from it to T; a `goto G` just before T (an else)
    holds not C from T to G."""
    gotos = {at: target for kind, at, *rest in events if kind == "goto" for target in rest}
    out = []
    for kind, at, *rest in events:
        if kind != "if":
            continue
        cond, target = rest
        out.append((at, target, cond))
        jump = gotos.get(target - 2)
        if jump is not None and jump > target:
            out.append((target - 1, jump, f"not {cond}"))
    return out


def _node(script: intdump.Script, index: int, end: int) -> Node:
    proc = script.procs[index]
    events: list = []
    intdump.decompile(script, proc, end, events)
    guards = _guards(events)
    names = {p.name for p in script.procs}

    def when(at: int) -> tuple[str, ...]:
        return tuple(c for s, e, c in guards if s < at < e)

    def proc_name(e) -> str:
        v = _value(e)
        if v is not None and 0 < v < len(script.procs):
            return script.procs[v].name
        return ""

    items = []
    for kind, at, *rest in events:
        if kind == "call" and rest[0] in names:
            items.append(Item("call", at, when(at), target=rest[0]))
        if kind != "fn":
            continue
        fname, args = rest
        if fname == "gsay_reply" and len(args) == 2:
            items.append(Item("reply", at, when(at), _value(args[0]), _value(args[1])))
        elif fname == "gsay_message" and len(args) == 3:
            items.append(Item("say", at, when(at), _value(args[0]), _value(args[1]), reaction=_value(args[2])))
        elif fname == "giq_option" and len(args) == 5:
            items.append(Item("option", at, when(at), _value(args[1]), _value(args[2]), proc_name(args[3]),
                              _value(args[0]), _value(args[4])))  # fmt: skip
        elif fname == "gsay_option" and len(args) == 4:
            items.append(Item("option", at, when(at), _value(args[0]), _value(args[1]), proc_name(args[2]),
                              None, _value(args[3])))  # fmt: skip
        elif fname in ("set_global_var", "set_local_var", "set_map_var") and len(args) == 2:
            var = intdump.name_arg("gvar", args[0]) if fname == "set_global_var" else args[0]  # as conditions read
            items.append(Item("set", at, when(at), text=f"{fname}({var}, {args[1]})"))
    return Node(proc.name, index, tuple(items))


@cache
def tree(name: str) -> dict[str, Node]:
    """Every procedure of the script NAME as a node, by name (the first of a name kept: the unnamed entry repeats
    `start`)."""
    script = intdump.load(name)
    bodies = sorted({p.body for p in script.procs} | {len(script.data)})
    out: dict[str, Node] = {}
    for i, p in enumerate(script.procs):
        if p.name in out:
            continue
        end = next(b for b in bodies if b > p.body)
        out[p.name] = _node(script, i, end)
    return out


def by_index(nodes: dict[str, Node]) -> dict[int, Node]:
    return {n.index: n for n in nodes.values()}


def shown(item: Item, intelligence: int) -> bool:
    """giq_option's IQ test (intextra.c op_giq_option); a gsay_option always shows."""
    if item.iq is None:
        return True
    return intelligence <= -item.iq if item.iq < 0 else intelligence >= item.iq


class Unknown(Exception):
    pass


Reader = Callable[[str, int | str], int | None]  # ("local_var" | "global_var" | "map_var", n or a GVAR's name)
VARIABLES = ("local_var", "global_var", "map_var")
_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Mod: operator.mod,
           ast.FloorDiv: operator.floordiv, ast.Div: operator.floordiv}  # fmt: skip
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge}  # fmt: skip
_SETS = re.compile(r"set_(local_var|global_var|map_var)\(([^,]+),")


def evaluate(cond: str, read: Reader) -> bool | None:
    """A condition's truth where every part of it is read: local_var(n), global_var(NAME), map_var(n) by `read`,
    constants, arithmetic, comparisons, and/or/not. Anything else (a script variable, another function, a value
    `read` does not give) leaves it unknown: None."""
    try:
        expr = ast.parse(cond, mode="eval").body
    except SyntaxError:
        return None

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.BoolOp):
            values = [ev(v) for v in n.values]  # every part evaluated: an unknown part leaves it unknown
            return all(values) if isinstance(n.op, ast.And) else any(values)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _BINARY:
            right = ev(n.right)
            if isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise Unknown
            return _BINARY[type(n.op)](ev(n.left), right)
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _COMPARE:
            return int(_COMPARE[type(n.ops[0])](ev(n.left), ev(n.comparators[0])))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in VARIABLES and len(n.args) == 1:
            arg = n.args[0]
            key = arg.value if isinstance(arg, ast.Constant) else arg.id if isinstance(arg, ast.Name) else None
            value = read(n.func.id, key) if key is not None else None
            if value is None:
                raise Unknown
            return value
        raise Unknown

    try:
        return bool(ev(expr))
    except (Unknown, TypeError, ValueError):
        return None


def _holds(it: Item, read: Reader | None, dirty: set[str]) -> bool | None:
    """Whether an item's conditions hold: True, False, or None when one is not read (or reads a variable that a
    procedure run on the way sets: `dirty`)."""
    if not it.when:
        return True
    if read is None:
        return None
    out: bool | None = True
    for cond in it.when:
        if any(v in cond for v in dirty):
            out = None
            continue
        value = evaluate(cond, read)
        if value is False:
            return False
        if value is None:
            out = None
    return out


def _covered(ends: set[tuple[str, ...]], when: tuple[str, ...]) -> bool:
    """Adds WHEN to ENDS; True once the branches in ENDS cover every case: an if's branch (p, C) and its else's
    (p, not C) together stand for p, and the empty condition for always (if/else chains of replies by reaction)."""
    ends.add(when)
    changed = True
    while changed and () not in ends:
        changed = False
        for t in list(ends):
            if t and t[-1].startswith("not ") and (*t[:-1], t[-1][4:]) in ends and t[:-1] not in ends:
                ends.add(t[:-1])
                changed = True
    return () in ends


def _run(nodes, callers, name: str, start: int, pop: bool, read=None, dirty=None, depth: int = 0):
    """The screens reached running node NAME from item START: (positions, whether it may run off its end). With
    `pop`, running off the end returns into the callers (the last first). With `read`, a condition read true or
    false picks its branch; one not read leaves every branch a candidate."""
    dirty = set() if dirty is None else dirty
    node = nodes.get(name)
    out: set = set()
    if node is None or depth > MAX_DEPTH:
        return out, True
    ends: set[tuple[str, ...]] = set()  # the conditions under which a screen already met stops the run
    for k in range(start, len(node.items)):
        it = node.items[k]
        holds = _holds(it, read, dirty)
        if holds is False:
            continue
        if it.kind == "set":
            if m := _SETS.match(it.text):
                dirty.add(f"{m.group(1)}({m.group(2).strip()})")
        elif it.kind in SCREENS:
            out.add((callers, name, k))
            if holds or _covered(ends, it.when):
                return out, False
        elif it.kind == "call" and it.target in nodes:
            sub, through = _run(nodes, (*callers, (name, k + 1)), it.target, 0, False, read, dirty, depth + 1)
            out |= sub
            if not through and (holds or _covered(ends, it.when)):
                return out, False
    if pop and callers:
        (caller, at), rest = callers[-1], callers[:-1]
        sub, through = _run(nodes, rest, caller, at, True, read, dirty, depth + 1)
        return out | sub, through
    return out, True


def first(nodes: dict[str, Node], name: str, read: Reader | None = None) -> set:
    """The screens a node shows first when an option leads to it, END when it may show none (the talk ends)."""
    out, through = _run(nodes, (), name, 0, False, read)
    return out | {END} if through else out


def after_line(nodes: dict[str, Node], position: Position, known_callers: bool = True, read=None) -> set:
    """The screens after a line's [Done]: the rest of its node, then its callers'. OPEN when the line's callers are
    not known and the node may run off its end; END when they are known and it may."""
    callers, name, k = position
    out, through = _run(nodes, callers, name, k + 1, True, read)
    if through:
        out |= {END} if known_callers else {OPEN}
    return out


def expected_options(nodes: dict[str, Node], position: Position, intelligence: int, read=None):
    """The messages a reply's options should show: those whose IQ the player meets and whose conditions hold; None
    when a condition is not read."""
    out = []
    for it in options_after(nodes, position):
        holds = _holds(it, read, set())
        if holds is None:
            return None
        if holds and shown(it, intelligence):
            out.append(it.message)
    return tuple(out)


def screen_message(nodes: dict[str, Node], position) -> tuple[int | None, int | None]:
    if position in (END, OPEN):
        return None, None
    _callers, name, k = position
    it = nodes[name].items[k]
    return it.list_id, it.message


def options_after(nodes: dict[str, Node], position: Position) -> tuple[Item, ...]:
    """A reply's options: the option items after it in its node up to the next screen (a reply's options follow it in
    code)."""
    _callers, name, k = position
    out = []
    for it in nodes[name].items[k + 1 :]:
        if it.kind in SCREENS:
            break
        if it.kind == "option":
            out.append(it)
    return tuple(out)


def locate(nodes: dict[str, Node], reply_list: int, reply_message: int, procs: tuple[int, ...] = ()) -> list:
    """The positions whose screen is this reply (or line), callers unknown; narrowed by the on-screen options'
    procedures when they tell nodes apart."""
    found = []
    for node in nodes.values():
        for k, it in enumerate(node.items):
            if it.kind in SCREENS and (it.list_id, it.message) == (reply_list, reply_message):
                found.append(((), node.name, k))
    if len(found) > 1 and procs:
        index = {n.name: n.index for n in nodes.values()}
        narrowed = [
            p for p in found if {index.get(o.target) for o in options_after(nodes, p)} >= {x for x in procs if x}
        ]
        found = narrowed or found
    return found


def describe(nodes: dict[str, Node], wanted: list[str] | None = None) -> str:
    lines = []
    for node in nodes.values():
        if wanted and node.name.lower() not in wanted:
            continue
        if not any(i.kind in (*SCREENS, "option", "call") for i in node.items):
            continue
        lines.append(f"{node.name} (#{node.index})")
        for it in node.items:
            cond = f"  [{' & '.join(it.when)}]" if it.when else ""
            if it.kind in SCREENS:
                lines.append(f"  {it.kind} {it.list_id}:{it.message}{cond}")
            elif it.kind == "option":
                lines.append(f"    -> {it.list_id}:{it.message} iq {it.iq} => {it.target or '-'}{cond}")
            elif it.kind == "call":
                lines.append(f"  call {it.target}{cond}")
            else:
                lines.append(f"  {it.text}{cond}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    print(describe(tree(argv[0].lower()), [a.lower() for a in argv[1:]] or None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
