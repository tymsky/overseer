"""The Heights strongbox's lock (idealist_hub.pick_the_box): a jam stops the tries, offline with a bare actor."""

from types import SimpleNamespace

from f1 import dialogue, idealist_hub, state, watchdog, win32

# made-up message lines: a failed try on the box (HTWRBOX), the one that jams it, the engine's answer after a jam
FAILED, FAILED_AND_JAMMED, JAMMED = "strongbox: not open", "strongbox: not open, jammed", "lock: jammed"


class Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, **data) -> dict:
        self.events.append((kind, data))
        return data


def box_actor(monkeypatch, answers: list[str]) -> SimpleNamespace:
    """Each try prints the next answer; the last one repeats."""
    lines: list[str] = []
    a = SimpleNamespace(log=Log(), tries=0, mem=SimpleNamespace(glob=lambda name: len(lines)))

    def use_skill_on(skill, tile, answered, targets=frozenset()):
        lines.append(answers[min(a.tries, len(answers) - 1)])
        a.tries += 1

    a.use_skill_on = use_skill_on
    monkeypatch.setattr(state, "messages_since", lambda mem, n0: lines[n0:])
    monkeypatch.setattr(idealist_hub, "heal_below", lambda actor, hp: None)
    monkeypatch.setattr(win32, "wait_for", lambda cond, *x, **k: cond())
    monkeypatch.setattr(dialogue, "holds", lambda text, script, n: False)
    quiet: list = []
    monkeypatch.setattr(watchdog, "quiet", lambda s: quiet.append(s))
    monkeypatch.setattr(watchdog, "unquiet", lambda: quiet.append("off"))
    a.quiet = quiet
    return a


def test_a_jammed_lock_stops_the_tries(monkeypatch) -> None:
    """The measured case (an ironman run, live): the second try jammed the lock (HTWRBOX 109), and every try after
    answered only the engine's jam message, with no "strongbox" in it: six tries, 13 s each, until the watchdog
    ended the run. The lines here are made up; the step reads only those two words in them."""
    a = box_actor(monkeypatch, [FAILED, FAILED_AND_JAMMED, JAMMED])
    assert idealist_hub.pick_the_box(a, frozenset()) == "jammed"
    assert a.tries == 2
    assert a.quiet == [idealist_hub.BOX_TRIES * 6, "off"]
    a = box_actor(monkeypatch, [JAMMED])  # jammed before the first try
    assert idealist_hub.pick_the_box(a, frozenset()) == "jammed" and a.tries == 1


def test_failed_tries_go_on_to_the_limit(monkeypatch) -> None:
    a = box_actor(monkeypatch, [FAILED])
    assert idealist_hub.pick_the_box(a, frozenset()) == "tries" and a.tries == idealist_hub.BOX_TRIES


def test_the_way_out_crosses_hightowers_hex_in_combat_mode_first(monkeypatch) -> None:
    """The measured case (an ironman run, live): a jam waited out, the box opened on the way back in, and the walk out
    stepped on Hightower's hex: his second talk is "Guards! Assassin!" (HIGHTOWR.INT talk_p_proc, local_var 4), and
    the household killed the player. Out of the Heights, his hex is crossed in combat mode before any walk."""
    calls: list = []
    where = {"map": "HUBHEIGT"}
    a = SimpleNamespace(mem=None, snap=lambda: SimpleNamespace(elevation=0))
    monkeypatch.setattr(idealist_hub, "on_map", lambda actor, name: where["map"] == name)
    monkeypatch.setattr(dialogue, "read", lambda mem: SimpleNamespace(active=False))
    monkeypatch.setattr(idealist_hub, "past_hightower", lambda actor, free: calls.append(("past", free)) or True)

    def hub_map(actor, name):
        calls.append(("map", name))
        where["map"] = name
        return True

    monkeypatch.setattr(idealist_hub, "hub_map", hub_map)
    assert idealist_hub.out_of_the_heights(a, "HUBDWNTN")
    assert calls == [("past", set(idealist_hub.FRONT_INSIDE)), ("map", "HUBDWNTN")]
