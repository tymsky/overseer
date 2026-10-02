"""Dialogue: what the other one says and the options on screen, read from gdialog.c's dialogBlock; keys 1..9 choose.

GameDialogBlock (fallout1-ce gdialog.c): program (+0x00), reply message list/id (+0x04/+0x08), offset (+0x0C),
replyText[900] (+0x10), 1800 unused bytes, then options[30] (+0xA9C), each 924 bytes: message list id, message id,
reaction, proc, button, field, text[900] (+0x18). gdNumOptions says how many are on screen.
"""

import time
from dataclasses import dataclass

from f1 import engine_map as em
from f1 import knowledge, session, state
from f1.engine_map import Obj
from f1.memory import GameMemory

REPLY_TEXT = 0x10
OPTIONS = 0xA9C
OPTION_SIZE = 0x18 + 900
OPTION_TEXT = 0x18
BULLET = "\x95 "  # each option starts with a bullet (0x95) and a space


@dataclass(frozen=True)
class Dialogue:
    active: bool
    speaker: str
    reply: str
    options: list[str]


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin1").strip()


def read(mem: GameMemory) -> Dialogue:
    active = state.detect_screen(mem) == "dialogue"
    base = em.GLOBALS["dialogBlock"].address
    n = max(0, min(mem.glob("gdNumOptions"), 9))
    reply = _text(mem.read(base + REPLY_TEXT, 900)) if active else ""
    options = []
    if active:
        for i in range(n):
            options.append(_text(mem.read(base + OPTIONS + i * OPTION_SIZE + OPTION_TEXT, 900)).lstrip(BULLET))
    target = mem.u32(em.GLOBALS["dialog_target"].address)
    speaker = knowledge.proto_name(mem.u32(target + Obj.PID)) if active and target else ""
    return Dialogue(active, speaker, reply, options)


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
        if not now.active or (now.reply, now.options) != (before.reply, before.options):
            if now.active and not now.options:  # a closing option: the windows go a moment later
                for _ in range(20):
                    time.sleep(0.25)
                    if not read(mem).active:
                        break
            time.sleep(0.4)
            return read(mem)
    return read(mem)


EXITS = ("bye", "leaving", "i'll be going", "goodbye", "farewell", "that's all")


def match(options: list[str], phrase: str) -> int | None:
    """The option that is `phrase` (case aside), else the first that contains it: "thanks." picks "Thanks." over
    "Thanks. Can I ask you a few more questions, first?"."""
    lowered = [o.lower().strip() for o in options]
    exact = next((i for i, o in enumerate(lowered) if o == phrase), None)
    return exact if exact is not None else next((i for i, o in enumerate(lowered) if phrase in o), None)


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
        i = exit_option if exit_option is not None else pick(d.options, left or plan)
        chosen = d.options[i].lower()
        left = [p for p in left if p not in chosen]
        steps.append((d.reply, d.options, d.options[i]))
        if log:
            log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i])
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
        i = next((k for k in (match(d.options, p) for p in plan) if k is not None), None)
        if i is None and len(d.options) == 1:
            i = 0
        if i is None:
            if log:
                log.emit("dialogue_stop", speaker=d.speaker, reply=d.reply, options=d.options)
            return None
        steps.append((d.reply, d.options, d.options[i]))
        if log:
            log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i])
        d = choose(mem, i)
    return steps
