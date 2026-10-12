"""Dialogue: what the other one says and the options on screen, read from gdialog.c's dialogBlock; keys 1..9 choose.

GameDialogBlock (fallout1-ce gdialog.c): program (+0x00), reply message list/id (+0x04/+0x08), offset (+0x0C),
replyText[900] (+0x10), 1800 unused bytes, then options[30] (+0xA9C), each 924 bytes: message list id, message id,
reaction, proc, button, field, text[900] (+0x18). gdNumOptions says how many are on screen.

Checked in FALLOUTW.EXE (capstone): gDialogReply (0x43E7E0) stores the program at 0x58DAC8, the reply's list and
message at 0x58DACC and 0x58DAD0 and clears gdNumOptions (0x505198); gdAddOption (0x43E5F8) fills entry n at
0x58E564 + 924 n (list +0, message +4, reaction +8, button +0x10); gDialogOptionProc (0x43E758) its procedure at
+0x0C. An option is known by its message (list, number) and the procedure it runs by its index in the script
(f1.talktree's nodes); a program's first field points at its file's name (intrpret.c allocateProgram), so the
talk's script is read from memory too. A gsay_message line shows one option of list -2 (its [Done]).
"""

import re
import struct
import time
from dataclasses import dataclass

from f1 import engine_map as em
from f1 import knowledge, session, state
from f1.engine_map import Obj
from f1.memory import GameMemory

REPLY_LIST, REPLY_TEXT = 0x04, 0x10
OPTIONS = 0xA9C
OPTION_SIZE = 0x18 + 900
OPTION_TEXT = 0x18
BULLET = "\x95 "  # each option starts with a bullet (0x95) and a space
DONE_LIST = -2


@dataclass(frozen=True)
class Option:
    index: int
    list_id: int
    message: int
    proc: int  # the procedure it runs: its index in the talk's script (0: none)
    text: str


@dataclass(frozen=True)
class Dialogue:
    active: bool
    speaker: str
    reply: str
    options: list[str]
    reply_list: int = 0
    reply_message: int = 0
    entries: tuple[Option, ...] = ()
    script: str = ""  # the talk's script, e.g. "aradesh"
    target: int = 0  # the critter talked to (its object's address)

    @property
    def messages(self) -> tuple[int, ...]:
        return tuple(o.message for o in self.entries)

    @property
    def procs(self) -> tuple[int, ...]:
        return tuple(o.proc for o in self.entries)

    @property
    def is_line(self) -> bool:
        """A gsay_message line: its only option is the [Done] (list -2)."""
        return len(self.entries) == 1 and self.entries[0].list_id == DONE_LIST

    def index_of(self, message: int, list_id: int | None = None) -> int | None:
        return next((o.index for o in self.entries
                     if o.message == message and (list_id is None or o.list_id == list_id)), None)  # fmt: skip


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin1").strip()


def program_name(mem: GameMemory, program: int) -> str:
    """The script a program runs, from its name field (a path to the .int), without folder and extension."""
    name_at = mem.u32(program) if program else 0
    if not name_at:
        return ""
    name = _text(mem.read(name_at, 64)).replace("/", "\\")
    return name.rsplit("\\", 1)[-1].rsplit(".", 1)[0].lower()


def read(mem: GameMemory) -> Dialogue:
    active = state.detect_screen(mem) == "dialogue"
    base = em.GLOBALS["dialogBlock"].address
    n = max(0, min(mem.glob("gdNumOptions"), 9))
    reply = _text(mem.read(base + REPLY_TEXT, 900)) if active else ""
    options, entries = [], []
    if active:
        for i in range(n):
            raw = mem.read(base + OPTIONS + i * OPTION_SIZE, OPTION_SIZE)
            list_id, message, _reaction, proc = struct.unpack_from("<4i", raw, 0)
            text = _text(raw[OPTION_TEXT:]).lstrip(BULLET)
            options.append(text)
            entries.append(Option(i, list_id, message, proc, text))
    target = mem.u32(em.GLOBALS["dialog_target"].address)
    speaker = knowledge.proto_name(mem.u32(target + Obj.PID)) if active and target else ""
    reply_list, reply_message = struct.unpack("<2i", mem.read(base + REPLY_LIST, 8))
    script = program_name(mem, mem.u32(base)) if active else ""
    return Dialogue(active, speaker, reply, options, reply_list, reply_message, tuple(entries), script,
                    target if active else 0)  # fmt: skip


def choose(mem: GameMemory, index: int, timeout_s: float = 8.0) -> Dialogue:
    """Pick option `index` (0-based) with its number key; wait until the reply or the options change, or it ends."""
    before = read(mem)
    if not before.active or index >= len(before.options):
        raise ValueError(f"no option {index} in {before.options}")
    session.press(str(index + 1))
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        time.sleep(0.05)  # the next node's text comes within a frame or two
        now = read(mem)
        if not now.active or _screen(now) != _screen(before):
            if now.active and not now.options:  # a closing option: the windows go a moment later
                for _ in range(20):
                    time.sleep(0.25)
                    if not read(mem).active:
                        break
            time.sleep(0.4)
            return read(mem)
    return read(mem)


def _screen(d: Dialogue) -> tuple:
    return d.reply, tuple(d.options), d.reply_list, d.reply_message, d.messages


EXITS = ("bye", "leaving", "i'll be going", "goodbye", "farewell", "that's all")


def match(options: list[str], phrase: str) -> int | None:
    """The option that is `phrase` (case aside), else the first that contains it: "ab." picks "Ab." over
    "Ab. Cd ef?"."""
    lowered = [o.lower().strip() for o in options]
    exact = next((i for i, o in enumerate(lowered) if o == phrase), None)
    return exact if exact is not None else next((i for i, o in enumerate(lowered) if phrase in o), None)


def _plain(text: str) -> str:
    return " ".join(text.lower().split())


def option_texts(d: Dialogue) -> list[str]:
    """Each option's text as the talk's message file has it, found by the option's list and number; the screen's
    text for an option without one (a text option, list -4, or a list not read). Plan phrases are matched against
    these, so the option taken is the message the phrase names."""
    from f1 import intdump

    out = []
    for i, text in enumerate(d.options):
        entry = d.entries[i] if i < len(d.entries) else None
        known = intdump.messages(entry.list_id).get(entry.message) if entry and entry.list_id > 0 else None
        out.append(_plain(known if known is not None else text))
    return out


PLAN_ID = re.compile(r"([a-z0-9_]*)([#@])(\d+(?:/\d+)*)")


def is_id(item: str) -> bool:
    """A plan item that names messages by number (plan_index), not by a phrase."""
    return PLAN_ID.fullmatch(item) is not None


def list_name(list_id: int) -> str:
    names = knowledge.load()["scripts"]
    return names[list_id - 1] if 1 <= list_id <= len(names) else ""


def message_text(script: str, number: int) -> str:
    """Message NUMBER of SCRIPT's message file (TEXT/ENGLISH/DIALOG/SCRIPT.MSG), lowered with its whitespace
    folded; "" when there is none."""
    from f1 import intdump

    names = knowledge.load()["scripts"]
    return _plain(intdump.messages(names.index(script) + 1).get(number, "")) if script in names else ""


def holds(text: str, script: str, number: int) -> bool:
    """TEXT (a reply, an option, the message box's lines) holds message NUMBER of SCRIPT's message file."""
    wanted = message_text(script, number)
    return bool(wanted) and wanted in _plain(text)


def plan_index(d: Dialogue, phrase: str) -> int | None:
    """The option a plan item names. By message number:
    "#N"           the option that is message N (of its own list); "#N/M/..." the first of these on screen;
    "SCRIPT#N"     the same, only in the list of the script SCRIPT's message file (talks that change lists:
                   one script's options under another's, a talk that hands over to the next speaker);
    "@N"           an option whose text holds message N of the talk's script (options built from a message and
                   the player's name: text options, list -4); "SCRIPT@N" in SCRIPT's message file.
    Else a phrase matched as `match` does against the options' message texts (option_texts)."""
    m = PLAN_ID.fullmatch(phrase)
    if m is None:
        return match(option_texts(d), _plain(phrase))
    script, kind, numbers = m.group(1), m.group(2), [int(n) for n in m.group(3).split("/")]
    if kind == "#":
        for n in numbers:
            i = next((o.index for o in d.entries
                      if o.message == n and (not script or list_name(o.list_id) == script)), None)  # fmt: skip
            if i is not None:
                return i
        return None

    for n in numbers:
        text = message_text(script or d.script, n)
        i = match(option_texts(d), text) if text else None
        if i is not None:
            return i
    return None


def ids(d: Dialogue) -> dict:
    """A screen's ids for a log: the script, the reply's message, the options' messages and procedures."""
    return {"script": d.script, "reply_id": [d.reply_list, d.reply_message],
            "option_ids": [[o.list_id, o.message, o.proc] for o in d.entries]}  # fmt: skip


def pick_by_plan(d: Dialogue, plan: list[str], avoid=("bye", "leaving", "lost", "what's it to you")) -> int:
    """`pick` on a screen read with its ids: the first plan item an option's message is (plan_index), else the
    first option that is not an exit or a rude one, else the first."""
    for phrase in plan:
        i = plan_index(d, phrase)
        if i is not None:
            return i
    return pick(d.options, [], avoid)


def pick(
    options: list[str], plan: list[str], avoid: tuple[str, ...] = ("bye", "leaving", "lost", "what's it to you")
) -> int:
    """The option to take: the first plan phrase an option is or contains (in plan order), else the first option
    that is not an exit or a rude one, else the first."""
    lowered = [o.lower() for o in options]
    for phrase in plan:
        i = match(options, phrase)
        if i is not None:
            return i
    for i, o in enumerate(lowered):
        if not any(a in o for a in avoid):
            return i
    return 0


def leave(options: list[str]) -> int | None:
    """An option that ends the talk politely, if there is one."""
    return next((i for i, o in enumerate(options) if any(e in o.lower() for e in EXITS)), None)


def close(mem: GameMemory, tries: int = 6) -> bool:
    """Out of a barter screen (Escape leaves it for its talk: inventry.c barter_inventory) and out of the talk by
    its polite exit, or its last option. True when neither is left. A shopkeeper's shelf used by a loot sweep opened
    Killian's barter in his store, and a checkpoint's save stood before it (Junktown)."""
    for _ in range(tries):
        screen = state.detect_screen(mem)
        if screen == "barter":
            session.press("esc")
            time.sleep(0.6)
            continue
        d = read(mem)
        if not d.active:
            return True
        if not d.options:
            time.sleep(0.4)
            continue
        i = leave(d.options)
        choose(mem, i if i is not None else len(d.options) - 1)
    return state.detect_screen(mem) != "barter" and not read(mem).active


def converse(mem: GameMemory, plan: list[str], max_steps: int = 15, log=None) -> list[tuple[str, list[str], str]]:
    """Follow a dialogue by plan until it ends; returns (reply, options, chosen) per step."""
    steps = []
    left = list(plan)  # plan phrases not chosen yet; when none are left, the talk is steered to its end
    seen: set[tuple[str, tuple[str, ...]]] = set()  # a node met again means the plan is going round: leave
    d = read(mem)
    for _ in range(max_steps):
        if not d.active or not d.options:
            break
        node = (d.reply, tuple(d.options))
        exit_option = leave(d.options) if not left or node in seen else None
        seen.add(node)
        i = exit_option if exit_option is not None else pick_by_plan(d, left or plan)
        chosen = option_texts(d)[i]
        left = [p for p in left if (plan_index(d, p) != i if is_id(p) else _plain(p) not in chosen)]
        steps.append((d.reply, d.options, d.options[i]))
        if log:
            log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i], **ids(d),
                     chosen_id=d.messages[i] if i < len(d.entries) else None)  # fmt: skip
        d = choose(mem, i)
    if d.active and d.reply:
        steps.append((d.reply, d.options, ""))
    return steps


def converse_strict(mem: GameMemory, plan: list[str], max_steps: int = 30, log=None):
    """Like converse(), for talks where a wrong answer is costly (Harry, the Lieutenant): only options that contain
    a plan phrase are taken (or the only option there is). A node without one stops the talk: None. `max_steps` only
    guards against a loop: Morpheus and the Master are 16 nodes in one talk (12 cut it short)."""
    steps = []
    d = read(mem)
    for _ in range(max_steps):
        if not d.active or not d.options:
            return steps
        i = next((k for k in (plan_index(d, p) for p in plan) if k is not None), None)
        if i is None and len(d.options) == 1:
            i = 0
        if i is None:
            if log:
                log.emit("dialogue_stop", speaker=d.speaker, reply=d.reply, options=d.options, **ids(d))
            return None
        steps.append((d.reply, d.options, d.options[i]))
        if log:
            log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i], **ids(d),
                     chosen_id=d.messages[i] if i < len(d.entries) else None)  # fmt: skip
        d = choose(mem, i)
    return steps
