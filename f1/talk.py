"""Talks followed by their scripts' nodes, and talks that open by themselves on a walk answered by a plan.

A talk is followed by a route of option messages (numbers in the talk's message list, from the decompiled script:
`python -m f1.talktree NAME`). At every screen the place in the talk is read from memory (the script, the reply's
message, the options' messages and procedures: f1.dialogue) and held against the place the script predicts
(f1.talktree): after an option, the first screens its procedure reaches; after a line's [Done], the rest of its node.
The script's conditions are read where they can be (its local variables, the global and map variables), so a branch
on them is predicted exactly; the options a reply should show are checked too (their IQ against the player's IN).
The option taken is the one whose message the route names, and the procedure it runs (read from memory) must be the
node the script gives it.

A talk on sight: a critter's script opens a talk when it sees the player (critter_p_proc: dialogue_system_enter),
which stops a walk (f1.nav.go_to returns "left the map (dialogue, ...)"). ON_SIGHT names, by script and by the
reply that opened it, the route that answers it and what to do after; `walk` answers such a talk and walks on. A
talk ON_SIGHT does not name, or a reply it does not name, is never answered blind: the walk stops with it open.

Static evidence for ON_SIGHT (f1.intdump): Junktown's gate guards (assblow, on its entrance map) open their talk in
critter_p_proc when they see the player with a weapon in either hand (item subtype 3), their local 4 at 0,
HIRED_BY_KILLIAN and CAPTURE_GIZMO at 0, once until their check resets; the talk's first node (135:110) asks for the
weapon put away, and a timed event 5 ticks after the talk opened marks the guard to attack if a weapon is still in
hand (it resets the check when none is). Killian's street's guards (jtgengrd) do the same on map 12 (JUNKKILL) within
12 hexes (37:110). The routes 111 -> 116 -> 119 end both talks at their end node.

The first screen of a talk can be predicted too, before it opens: `entry` is the procedure the talk starts in (the
script's talk procedure, or the one it calls), its conditions read before the click.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from f1 import chargen, dialogue, knowledge, nav, scripts, talktree, win32
from f1 import engine_map as em
from f1.memory import GameMemory
from f1.talktree import END, OPEN

OPTIONS_S = 4.0  # a talk's first options drawn after its window opens
MAX_STEPS = 40


def reader(mem: GameMemory, target: int) -> talktree.Reader:
    """The variables a talk's conditions read: the speaker's local_var(n), global_var(NAME), map_var(n)."""

    def read(kind: str, key) -> int | None:
        if kind == "local_var" and isinstance(key, int):
            return scripts.local_var(mem, target, key) if target else None
        if kind == "global_var":
            try:
                index = key if isinstance(key, int) else knowledge.gvar_index(str(key))
            except KeyError:
                return None
            if not 0 <= index < mem.i32(em.GLOBALS["num_game_global_vars"].address):
                return None
            return mem.i32(mem.u32(em.GLOBALS["game_global_vars"].address) + 4 * index)
        if kind == "map_var" and isinstance(key, int):
            if not 0 <= key < mem.i32(em.GLOBALS["num_map_global_vars"].address):
                return None
            return mem.i32(mem.u32(em.GLOBALS["map_global_vars"].address) + 4 * key)
        return None

    return read


def _name(position) -> str:
    return position if position in (END, OPEN) else f"{position[1]}:{position[2]}"


def _names(positions) -> list[str]:
    return sorted(_name(p) for p in positions) if positions is not None else []


def follow(mem: GameMemory, route: Sequence[int] | Mapping[int, int], log=None, intelligence: int | None = None,
           close: bool = True, choose=dialogue.choose, max_steps: int = MAX_STEPS,
           first: set | None = None) -> dict:  # fmt: skip
    """The open talk followed by ROUTE: option messages in order, or a plan by reply (the reply's message -> the
    option's message, for talks whose replies branch on what is not read); each line's [Done] taken on the way.
    Every step: the screen read from memory against the predicted one. Ends when the talk ends, or when the route is
    done and a reply waits (closed by its polite exit when `close`). The result: ok (a route taken to its end, a plan
    to the talk's end), steps, and the predictions' count."""
    by_reply = route if isinstance(route, Mapping) else None
    d = dialogue.read(mem)
    if not d.active:
        return {"ok": False, "why": "no talk", "steps": []}
    nodes = talktree.tree(d.script)
    read = reader(mem, d.target)
    iq = chargen.special(mem)[4] if intelligence is None else intelligence
    left = [] if by_reply is not None else list(route)
    steps: list[dict] = []
    predicted: set | None = first  # None: the talk located afresh (its first screen not predicted: `entry`)
    rooted = False  # the callers of the screen are known (it was reached from an option)
    out = {"script": d.script, "steps": steps}
    for _ in range(max_steps):
        d = dialogue.read(mem)
        if not d.active:
            step = {"screen": END, "predicted": _names(predicted)}
            if predicted is not None and OPEN not in predicted:
                step["ok"] = END in predicted
            steps.append(step)
            break
        screen = (d.reply_list, d.reply_message)
        here = [p for p in predicted or () if p not in (END, OPEN) and talktree.screen_message(nodes, p) == screen]
        step = {"screen": [*screen], "predicted": _names(predicted), "options": [[o.list_id, o.message, o.proc]
                for o in d.entries]}  # fmt: skip
        if predicted is not None and (here or OPEN not in predicted):
            step["ok"] = bool(here)
        if not here:
            here = talktree.locate(nodes, d.reply_list, d.reply_message, d.procs)
            rooted = False
        step["node"] = _names(here)
        steps.append(step)
        if not here:
            out["why"] = "a screen the script does not have"
            break
        if d.is_line:
            step["chose"] = "done"
            predicted = set().union(*(talktree.after_line(nodes, p, rooted, read) for p in here))
            if log:
                log.emit("talk_step", **step)
            choose(mem, 0)
            continue
        expected = talktree.expected_options(nodes, here[0], iq, read)
        if expected is not None:
            step["options_expected"] = list(expected)
            step["options_ok"] = tuple(o.message for o in d.entries if o.list_id > 0) == expected
        message = by_reply.get(d.reply_message) if by_reply is not None else left.pop(0) if left else None
        if message is None:
            out["why"] = "the route done, a reply waits" if by_reply is None else "a reply the plan does not name"
            if log:
                log.emit("talk_step", **step)
            break
        i = d.index_of(message)
        if i is None:
            out["why"] = f"option {message} not on screen"
            if log:
                log.emit("talk_step", **step)
            break
        option = d.entries[i]
        items = [o for p in here for o in talktree.options_after(nodes, p)
                 if (o.list_id, o.message) == (option.list_id, option.message)]  # fmt: skip
        target = items[0].target if items else ""
        step["chose"] = message
        step["leads_to"] = target
        step["proc_ok"] = bool(target) and nodes[target].index == option.proc
        predicted = talktree.first(nodes, target, read) if target else {END}
        rooted = True
        if log:
            log.emit("talk_step", **step)
        choose(mem, i)
    checked = [s for s in steps if "ok" in s]
    out["predicted"] = f"{sum(s['ok'] for s in checked)}/{len(checked)}"
    out["exact"] = sum(s["ok"] and len(s["predicted"]) == 1 for s in checked)  # one screen predicted, and it came
    taken = [s["proc_ok"] for s in steps if "proc_ok" in s]
    out["procs"] = f"{sum(taken)}/{len(taken)}"
    done = (None,) if by_reply is not None else (None, "the route done, a reply waits")
    out["ok"] = not left and out.get("why") in done
    if close and dialogue.read(mem).active and out["ok"]:
        out["closed"] = dialogue.close(mem)
    return out


def entry(mem: GameMemory, script: str, procedure: str, target: int) -> set:
    """The first screens a talk that starts in PROCEDURE of SCRIPT will show, its conditions read now (before the
    talk opens) from the critter at TARGET; for follow's `first`."""
    return talktree.first(talktree.tree(script), procedure, reader(mem, target))


@dataclass(frozen=True)
class OnSight:
    """A talk that opens by itself: the route for each reply it may open with ((list, message) -> option messages),
    and what the player does once it is over ("holster": the weapon put away)."""

    routes: dict[tuple[int, int], tuple[int, ...]]
    then: str = ""


# Junktown's gate guards (assblow): guard00 (135:110, a weapon in hand) -> 111 -> guard01 (115) -> 116 -> guard02
# (118) -> 119 -> guardend: the talk ends; the weapon put away before the guard's timed check
# [static: assblow critter_p_proc, pre_dialogue, do_dialogue, guard00..02, timed_event_p_proc]
# Killian's street's guards (jtgengrd): Node00 (37:110) -> 111 -> Node01 (115) -> 116 -> Node02 (118) -> 119 ->
# JTGenGrdEnd [static: jtgengrd critter_p_proc, talk_p_proc, JTGenGrd00, 01, 02, timed_event_p_proc]
ON_SIGHT: dict[str, OnSight] = {
    "assblow": OnSight({(135, 110): (111, 116, 119)}, then="holster"),
    "jtgengrd": OnSight({(37, 110): (111, 116, 119)}, then="holster"),
}
ON_SIGHT_TALKS = 3  # such talks answered on one walk at most


def answer_on_sight(actor, log=None) -> dict:
    """The talk that stopped a walk, answered by ON_SIGHT (its options awaited first); refused for any other."""
    mem = actor.mem
    win32.wait_for(lambda: bool(dialogue.read(mem).entries) or not dialogue.read(mem).active, OPTIONS_S)
    d = dialogue.read(mem)
    plan = ON_SIGHT.get(d.script)
    route = plan.routes.get((d.reply_list, d.reply_message)) if plan else None
    seen = {"script": d.script, "reply": [d.reply_list, d.reply_message], "options": list(d.messages)}
    if route is None:
        if log:
            log.emit("check_failed", what="a talk on the way not answered", **seen)
        return {"ok": False, "why": f"a talk on the way: {d.script or d.speaker}", **seen}
    if log:
        log.emit("milestone", what="a talk on the way answered", route=list(route), **seen)
    result = follow(mem, route, log)
    if plan.then == "holster":
        result["holstered"] = actor.holster(True)
    return {**seen, **result, "ok": result["ok"] and not dialogue.read(mem).active}


def walk(actor, goals: set[int], max_steps: int = 80, fight=True, log=None) -> tuple[bool, str, list[dict]]:
    """nav.go_to, with the talks that open on the way (or are open when it starts) answered by ON_SIGHT and the walk
    then taken up again (ON_SIGHT_TALKS at most). Returns go_to's (reached, why) and the talks answered."""
    talks: list[dict] = []
    while True:
        if actor.snap().screen == "dialogue":  # opened on the way, or before the walk began (a weapon drawn in sight)
            if len(talks) == ON_SIGHT_TALKS:
                return False, "too many talks on the way", talks
            talks.append(answer_on_sight(actor, log))
            if not talks[-1]["ok"]:
                return False, talks[-1].get("why") or "a talk on the way stayed open", talks
            time.sleep(0.3)
        ok, why = nav.go_to(actor, goals, max_steps=max_steps, fight=fight)
        if actor.snap().screen != "dialogue":
            return ok, why, talks
