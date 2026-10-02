"""Fallout 1's compiled scripts (SCRIPTS\\*.INT) read back as procedures of statements: the conditions of dialogue
options, skill checks and quest steps, from the game's own files.

The format is fallout1-ce's interpreter (src/int/intrpret.cc): big-endian; the procedure table at 42 (a count, then
24-byte records: name, flags, time, condition, body, argument count), the identifiers (a length, then the names the
records point into), the static strings (a length, then strings at +4 + offset). Code is 16-bit opcodes with the top
bit set; opcode & 0x3FF picks the handler; a push (index 1) carries its value's type in the high bits (0xC001 int,
0xA001 float, 0x9001 string) and 32 bits of value. Expressions are rebuilt on a stack; statements are printed with
their offsets, branches as gotos. A message list id N is SCRIPTS.LST line N - 1 (scripts.c scr_get_dialog_msg_file),
its texts in DIALOG\\<name>.MSG, shown next to the number.

    python -m f1.intdump extract           the scripts out of the DATs (extracted/f1/scripts/int, not in git)
    python -m f1.intdump NAME [PROC ...]   e.g. master: every procedure, or the named ones
    python -m f1.intdump grep REGEX        matching statements in every script
    python -m f1.intdump index             every statement that sets or reads a global variable, one grep pass,
                                           into extracted/f1/research/gvar_uses.txt (the quest notes are built on it)
"""

import struct
import sys
from dataclasses import dataclass
from functools import cache

from f1 import chargen, knowledge, paths
from f1.intops import GAME_FUNCTIONS

SCRIPTS_DIR = paths.EXTRACTED / "scripts" / "int"
PUSH, PUSH_INT, PUSH_FLOAT, PUSH_STRING = 0x8001, 0xC001, 0xA001, 0x9001
BINARY = {
    0x8033: "==", 0x8034: "!=", 0x8035: "<=", 0x8036: ">=", 0x8037: "<", 0x8038: ">", 0x8039: "+", 0x803A: "-",
    0x803B: "*", 0x803C: "/", 0x803D: "%", 0x803E: "and", 0x803F: "or", 0x8040: "bwand", 0x8041: "bwor",
    0x8042: "bwxor",
}  # fmt: skip
UNARY = {0x8043: "bwnot ", 0x8044: "floor ", 0x8045: "not ", 0x8046: "-"}
STATS = (
    "ST", "PE", "EN", "CH", "IN", "AG", "LK", "max HP", "max AP", "AC", "unarmed damage", "melee damage",
    "carry weight", "sequence", "healing rate", "critical chance", "better criticals", "DT", "DT laser", "DT fire",
    "DT plasma", "DT electrical", "DT EMP", "DT explosion", "DR", "DR laser", "DR fire", "DR plasma",
    "DR electrical", "DR EMP", "DR explosion", "radiation resistance", "poison resistance", "age", "gender", "HP",
    "poison", "radiation",
)  # fmt: skip  # stat_defs.h
# Where a function's argument is a stat, a skill or a GVAR number (by position), to name it.
NAMED_ARGS = {
    "get_critter_stat": {1: "stat"}, "do_check": {1: "stat"}, "has_skill": {1: "skill"},
    "roll_vs_skill": {1: "skill"}, "global_var": {0: "gvar"}, "set_global_var": {0: "gvar"},
}  # fmt: skip
# Message arguments (list, number) by position, and procedure arguments, of the dialogue functions.
MESSAGE_ARGS = {"gsay_reply": 0, "gsay_option": 0, "giq_option": 1, "gsay_message": 0, "message_str": 0}
PROC_ARGS = {"gsay_option": 2, "giq_option": 3}


@dataclass(frozen=True)
class Procedure:
    name: str
    flags: int
    body: int
    args: int


class Script:
    def __init__(self, data: bytes) -> None:
        self.data = data
        count = self.long(42)
        self.identifiers = 46 + 24 * count
        self.strings = self.identifiers + self.long(self.identifiers) + 4
        self.procs = []
        for i in range(count):
            name, flags, _time, _cond, body, args = struct.unpack(">6i", data[46 + 24 * i : 70 + 24 * i])
            self.procs.append(Procedure(self.cstr(self.identifiers + name), flags, body, args))

    def long(self, at: int) -> int:
        return struct.unpack(">i", self.data[at : at + 4])[0]

    def cstr(self, at: int) -> str:
        end = self.data.index(b"\0", at)
        return self.data[at:end].decode("latin1")

    def string(self, offset: int) -> str:
        return self.cstr(self.strings + 4 + offset)

    def instructions(self, start: int, end: int):
        """(offset, opcode, value) from start to end; value is the pushed constant, else None."""
        at = start
        while at + 2 <= end:
            op = struct.unpack(">H", self.data[at : at + 2])[0]
            if not op & 0x8000:
                return
            if op & 0x3FF == PUSH & 0x3FF:
                raw = self.data[at + 2 : at + 6]
                value = struct.unpack(">f", raw)[0] if op == PUSH_FLOAT else struct.unpack(">i", raw)[0]
                yield at, op, value
                at += 6
            else:
                yield at, op, None
                at += 2


@cache
def messages(list_id: int) -> dict[int, str]:
    names = knowledge.load()["scripts"]
    if not 1 <= list_id <= len(names):
        return {}
    try:
        from f1.dat import GameFiles

        return knowledge.parse_msg(GameFiles(knowledge.INSTANCE).read(f"TEXT/ENGLISH/DIALOG/{names[list_id - 1]}.MSG"))
    except KeyError:
        return {}


def load(name: str) -> Script:
    return Script((SCRIPTS_DIR / f"{name.upper()}.INT").read_bytes())


class Expr(str):
    """An expression's text, with its constant value when it is one (and a pushed string's raw offset)."""

    value: int | float | str | None = None
    offset: int | None = None


def const(text: str, value: float | str) -> Expr:
    e = Expr(text)
    e.value = value
    return e


def name_arg(kind: str, e: Expr) -> Expr:
    if not isinstance(e.value, int):
        return e
    if kind == "stat" and 0 <= e.value < len(STATS):
        return Expr(STATS[e.value])
    if kind == "skill" and 0 <= e.value < len(chargen.SKILL_NAMES):
        return Expr(chargen.SKILL_NAMES[e.value].upper().replace(" ", "_"))
    if kind == "gvar":
        g = knowledge.load()["gvars"]
        return Expr(g[e.value]["name"]) if 0 <= e.value < len(g) else e
    return e


def external_name(script: Script, e: Expr) -> str:
    """An exported variable's name: the pushed offset read in the identifier table (`ignoring_dude`)."""
    if e.offset is None:
        return f"external({e})"
    try:
        return script.cstr(script.identifiers + e.offset)
    except ValueError:
        return f"external({e})"


def decompile(script: Script, proc: Procedure, end: int) -> list[str]:
    """The procedure's statements, one line each: '0xOFFSET: statement'."""
    stack: list[Expr] = []
    lines: list[str] = []

    def pop() -> Expr:
        return stack.pop() if stack else Expr("?")

    def emit(at: int, text: str) -> None:
        lines.append(f"  {at:#07x}: {text}")

    for at, op, value in script.instructions(proc.body, end):
        if value is not None:
            if op == PUSH_STRING:
                e = const(repr(script.string(value)), script.string(value))
                e.offset = value  # an external variable's name is this offset into the identifiers instead
                stack.append(e)
            else:
                stack.append(const(f"{value:g}" if isinstance(value, float) else str(value), value))
        elif op in BINARY:
            b, a = pop(), pop()
            stack.append(Expr(f"({a} {BINARY[op]} {b})"))
        elif op in UNARY:
            stack.append(Expr(f"{UNARY[op]}{pop()}"))
        elif op == 0x802F:  # if: the condition, then the address to go to when it is false
            cond, target = pop(), pop()
            emit(at, f"if not {cond} goto {target.value:#07x}" if isinstance(target.value, int) else f"if not {cond}")
        elif op == 0x8030:
            cond, target = pop(), pop()
            emit(at, f"while {cond} else goto {target}")
        elif op == 0x8004:
            target = pop()
            emit(at, f"goto {target.value:#07x}" if isinstance(target.value, int) else f"goto {target}")
        elif op == 0x8005:  # call a procedure of this script: its index, then the argument count and arguments
            index = pop()
            callee = script.procs[index.value].name if isinstance(index.value, int) else str(index)
            count = pop()
            n = count.value if isinstance(count.value, int) and 0 <= count.value < 16 else 0
            args = [pop() for _ in range(n)][::-1]
            stack.append(Expr(f"{callee}({', '.join(args)})"))
        elif op in (0x8012, 0x8032):  # fetch a script variable / a local
            index = pop()
            stack.append(Expr(f"{'var' if op == 0x8012 else 'local'}{index}"))
        elif op in (0x8013, 0x8031):
            index, v = pop(), pop()
            emit(at, f"{'var' if op == 0x8013 else 'local'}{index} := {v}")
        elif op == 0x8014:  # an exported variable, by its name in the identifier table (not the strings)
            stack.append(Expr(external_name(script, pop())))
        elif op == 0x8015:
            n, v = pop(), pop()
            emit(at, f"{external_name(script, n)} := {v}")
        elif op == 0x801A:  # pop: an expression evaluated for its effect (a call whose value is dropped)
            emit(at, str(pop()))
        elif op == 0x801B:
            top = pop()
            stack += [top, top]
        elif op == 0x8018:
            b, a = pop(), pop()
            stack += [b, a]
        elif op in (0x801C, 0x801D, 0x8020, 0x8021, 0x8022, 0x8023, 0x8024, 0x8025, 0x8026):
            emit(at, "return" if op in (0x801C, 0x8020, 0x8022, 0x8024) else "exit")
        elif op == 0x8027:  # check_arg_count: expected count, procedure index
            pop(), pop()
        elif op == 0x802B:  # push_base: the argument count
            pop()
        elif op == 0x802D:
            index = pop()
            stack.append(Expr(script.procs[index.value].name if isinstance(index.value, int) else f"proc({index})"))
        elif op == 0x800D:  # d_to_a: a call's return address put aside (a constant), or a value
            v = pop()
            if not isinstance(v.value, int):
                emit(at, f"result := {v}")
        elif op == 0x800C:
            stack.append(Expr("result"))
        elif op in (0x8006, 0x8007):
            b, a = pop(), pop()
            emit(at, f"{'call_at' if op == 0x8006 else 'call_when'}({a}, {b})")
        elif op in GAME_FUNCTIONS:
            fname, n, returns = GAME_FUNCTIONS[op]
            args = [pop() for _ in range(n)][::-1]
            for i, kind in NAMED_ARGS.get(fname, {}).items():
                if i < len(args):
                    args[i] = name_arg(kind, args[i])
            if fname in MESSAGE_ARGS:
                i = MESSAGE_ARGS[fname]
                if i + 1 < len(args) and isinstance(args[i].value, int) and isinstance(args[i + 1].value, int):
                    text = messages(args[i].value).get(args[i + 1].value)
                    if text is not None:
                        args[i + 1] = Expr(f"{args[i + 1]} {text[:90]!r}")
            if fname in PROC_ARGS and PROC_ARGS[fname] < len(args):
                p = args[PROC_ARGS[fname]]
                if isinstance(p.value, int) and 0 <= p.value < len(script.procs):
                    args[PROC_ARGS[fname]] = Expr(script.procs[p.value].name)
            call = Expr(f"{fname}({', '.join(args)})")
            if returns:
                stack.append(call)
            else:
                emit(at, call)
        elif op in (0x8000, 0x8002, 0x8003, 0x8029, 0x802A, 0x802C, 0x804A, 0x804B, 0x801E, 0x801F):
            pass  # no-ops and frame bookkeeping
        else:
            emit(at, f"op {op:#06x}")
    return lines


def dump(name: str, wanted: list[str] | None = None) -> str:
    script = load(name)
    bodies = sorted({p.body for p in script.procs} | {len(script.data)})
    out = [f"{name.upper()}.INT: {len(script.procs)} procedures"]
    for p in script.procs:
        if wanted and p.name.lower() not in wanted:
            continue
        end = next((b for b in bodies if b > p.body), len(script.data))
        out.append(f"procedure {p.name}({p.args}) @{p.body:#07x} flags {p.flags:#x}")
        out += decompile(script, p, end)
    return "\n".join(out)


def extract() -> int:
    from f1.dat import GameFiles

    files = GameFiles(knowledge.INSTANCE)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in files.names():
        up = path.upper().replace("\\", "/")
        if up.startswith("SCRIPTS/") and up.endswith(".INT"):
            (SCRIPTS_DIR / up.split("/")[-1]).write_bytes(files.read(up))
            count += 1
    return count


def grep(pattern: str) -> list[str]:
    """Statements matching a regular expression in every script: 'SCRIPT proc 0xOFFSET: statement'."""
    import re

    rx = re.compile(pattern, re.IGNORECASE)
    out = []
    for path in sorted(SCRIPTS_DIR.glob("*.INT")):
        try:
            script = Script(path.read_bytes())
            bodies = sorted({p.body for p in script.procs} | {len(script.data)})
            for p in script.procs[1:]:  # the first record repeats `start`
                if p.flags & 0x04 or not 0 < p.body < len(script.data):  # imported: no body here
                    continue
                end = next((b for b in bodies if b > p.body), len(script.data))
                out += [f"{path.stem} {p.name}{line[1:]}" for line in decompile(script, p, end) if rx.search(line)]
        except (ValueError, IndexError, struct.error) as e:
            out.append(f"{path.stem}: unreadable ({e!r})")
    return out


def main(argv: list[str]) -> int:
    if argv == ["extract"]:
        print(f"{extract()} scripts in {SCRIPTS_DIR}")
        return 0
    if len(argv) == 2 and argv[0] == "grep":
        print("\n".join(grep(argv[1])))
        return 0
    if argv == ["index"]:
        out = paths.EXTRACTED / "research" / "gvar_uses.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = grep(r"set_global_var\(|global_var\(")
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"{len(lines)} statements in {out}")
        return 0
    if not argv:
        print(__doc__)
        return 2
    print(dump(argv[0], [a.lower() for a in argv[1:]] or None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
