"""The Heights strongbox's lock (idealist_hub.pick_the_box): a jam stops the tries, offline with a bare actor."""

from types import SimpleNamespace

from f1 import dialogue, idealist_hub, state, watchdog, win32


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
    answered only the engine's "The lock is jammed." with no "strongbox" in it: six tries, 13 s each, until the
    watchdog ended the run."""
    a = box_actor(
        monkeypatch,
        [
            "You were unable to unlock the strongbox.",
            "You were unable to unlock the strongbox, and the  lock has become jammed.",
            "The lock is jammed.",
        ],
    )
    assert idealist_hub.pick_the_box(a, frozenset()) == "jammed"
    assert a.tries == 2
    assert a.quiet == [idealist_hub.BOX_TRIES * 6, "off"]
    a = box_actor(monkeypatch, ["The lock is jammed."])  # jammed before the first try
    assert idealist_hub.pick_the_box(a, frozenset()) == "jammed" and a.tries == 1


def test_failed_tries_go_on_to_the_limit(monkeypatch) -> None:
    a = box_actor(monkeypatch, ["You were unable to unlock the strongbox."])
    assert idealist_hub.pick_the_box(a, frozenset()) == "tries" and a.tries == idealist_hub.BOX_TRIES
