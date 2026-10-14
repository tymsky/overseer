"""The route runner's bookkeeping with a fake actor: failures tried again in place, optional steps skipped,
only a death or a stuck game loading the last checkpoint, giving up."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from f1 import route_runner as routes
from f1 import watchdog
from f1.route_support import Step


@dataclass
class Dude:
    hp: int = 30
    tile: int = 20100


@dataclass
class Snap:
    screen: str = "map"
    dude: Dude | None = None
    level: int = 1
    map_name: str = "TEST.MAP"
    elevation: int = 0


class Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, **fields) -> None:
        self.events.append((kind, fields))

    def kinds(self, kind: str) -> list[dict]:
        return [f for k, f in self.events if k == kind]


class FakeActor:
    """Stands in for Actor: the runner only snaps, quicksaves, closes and re-inits it. A load (re-init) brings the
    player back to life."""

    def __init__(self, pid: int = 1, log: Log | None = None) -> None:
        self.pid = pid
        self.log = log if log is not None else Log()  # the runner re-inits the actor after a reload: keep the log
        self.hp = 30

    level = 1

    def snap(self) -> Snap:
        return Snap(dude=Dude(self.hp), level=self.level)

    def in_combat(self) -> bool:
        return False

    def quicksave(self, slot: int, description: str) -> SimpleNamespace:
        self.log.emit("save", slot=slot, description=description)
        return SimpleNamespace(ok=True)

    def close(self) -> None:
        pass

    def game_alive(self) -> bool:
        return getattr(self, "alive", True)


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(routes.flows, "load_from_menu", lambda pid, slot: calls.append(slot))
    monkeypatch.setattr(routes, "settle", lambda actor, plan=None: True)  # no dialogue in a fake game
    return calls


def scripted(ran: list[str], outcomes: dict[str, list]):
    """Steps that record their runs and give their scripted outcomes: True, False, or "die"."""

    def step(name: str):
        def run(actor) -> bool:
            ran.append(name)
            out = outcomes[name].pop(0) if outcomes.get(name) else True
            if out == "die":
                actor.hp = 0
                return False
            return out

        return run

    return step


def test_a_failed_step_is_tried_again_in_place_without_a_load(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {"c": [False, True]})  # c fails once (a click that missed), then works
    steps = [Step("a", step("a")), Step("b", step("b"), checkpoint=True), Step("c", step("c")), Step("d", step("d"))]
    actor = FakeActor()
    result = routes.run(actor, steps)
    assert result == {"ok": True, "loads": {}, "fails": {"c": 1}, "skipped": [], "ended": "done"}
    assert ran == ["a", "b", "c", "c", "d"]
    assert loads == []  # no load for a failure
    assert [f["description"] for f in actor.log.kinds("save")] == ["route 2"]


def test_a_death_loads_the_checkpoint_and_goes_on_after_it(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {"c": ["die", True]})
    steps = [Step("a", step("a")), Step("b", step("b"), checkpoint=True), Step("c", step("c")), Step("d", step("d"))]
    result = routes.run(FakeActor(), steps)
    assert result == {"ok": True, "loads": {"c": 1}, "fails": {}, "skipped": [], "ended": "done"}
    assert ran == ["a", "b", "c", "c", "d"]  # after the load: from the step after the checkpoint (c), not from a
    assert loads == [1]


class DeathScreenActor(FakeActor):
    """After a death the game resets the player (level 1, no XP, HP back) under the death picture's window."""

    screen = "map"

    def snap(self) -> Snap:
        snap = Snap(screen=self.screen, dude=Dude(self.hp), level=self.level)
        snap.experience = 0 if self.screen.startswith("window") else 100
        return snap


def test_the_death_picture_counts_as_a_death(loads: list[int]) -> None:
    ran: list[str] = []
    actor = DeathScreenActor()

    def mother(a) -> bool:
        ran.append("mother")
        if ran.count("mother") == 1:
            a.screen = "window 1 (0x4 0,0 1920x1080)"
            return False
        a.screen = "map"
        return True

    steps = [Step("in", lambda a: True, checkpoint=True), Step("mother", mother), Step("out", lambda a: True)]
    result = routes.run(actor, steps)
    assert result["loads"] == {"mother": 1} and result["ok"]
    assert loads == [1]


def test_an_optional_step_that_keeps_failing_is_skipped(loads: list[int]) -> None:
    """The cook's compliment: one luck check at the first talk; its outcome stands (no reloading for luck)."""
    ran: list[str] = []
    step = scripted(ran, {"luck": [False]})
    steps = [Step("a", step("a")), Step("luck", step("luck"), tries=1, optional=True), Step("b", step("b"))]
    result = routes.run(FakeActor(), steps)
    assert result == {"ok": True, "loads": {}, "fails": {"luck": 1}, "skipped": ["luck"], "ended": "done"}
    assert ran == ["a", "luck", "b"] and loads == []


def test_a_required_step_ends_the_route_after_its_tries(loads: list[int]) -> None:
    steps = [Step("a", lambda _a: True, checkpoint=True), Step("b", lambda _a: False, tries=2)]
    result = routes.run(FakeActor(), steps)
    assert result["ok"] is False and result["at"] == "b" and result["fails"] == {"b": 2}
    assert loads == []


def test_deaths_past_the_tries_give_up(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {"b": ["die"] * 5})
    steps = [Step("a", step("a"), checkpoint=True), Step("b", step("b"), tries=2)]
    result = routes.run(FakeActor(), steps)
    assert result["ok"] is False and result["loads"] == {"b": 3}
    assert len(loads) == 2


def test_a_stuck_game_loads_the_checkpoint(loads: list[int]) -> None:
    hung = [True]

    def hangs(_actor) -> bool:
        if hung:
            hung.pop()
            raise watchdog.Stuck("nothing moved for 60 s")
        return True

    steps = [Step("a", lambda _a: True, checkpoint=True), Step("b", hangs)]
    result = routes.run(FakeActor(), steps)
    assert result["ok"] is True and result["loads"] == {"b": 1} and loads == [1]


def test_starting_mid_way_a_death_goes_back_to_the_checkpoint_before_the_start(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {"c": ["die", True]})
    steps = [Step("a", step("a"), checkpoint=True), Step("b", step("b")), Step("c", step("c"))]
    result = routes.run(FakeActor(), steps, start=3)
    assert result["ok"] is True
    assert ran == ["c", "b", "c"]  # the load goes back to a's checkpoint: b runs again, then c


def test_a_checkpoint_spends_a_level_up_once(loads: list[int]) -> None:
    actor = FakeActor()
    spent: list[int] = []

    def level_up(pid: int) -> dict:
        spent.append(pid)
        return {"ok": True}

    def gain_a_level(a) -> bool:
        a.level = 2
        return True

    steps = [Step("a", gain_a_level, checkpoint=True), Step("b", lambda _a: True, checkpoint=True)]
    assert routes.run(actor, steps, level_up=level_up)["ok"] is True
    assert spent == [1]  # at a's checkpoint (level 2 > 0), not again at b's


def test_an_unsaved_checkpoint_is_not_resumed_from(loads: list[int]) -> None:
    """Junktown: a guard's talk blocked the save; the load went to the checkpoint before it."""
    ran: list[str] = []

    class NoSave(FakeActor):
        def quicksave(self, slot: int, description: str) -> SimpleNamespace:
            return SimpleNamespace(ok=description != "route 2")

    step = scripted(ran, {"c": ["die", True]})
    steps = [
        Step("a", step("a"), checkpoint=True),
        Step("b", step("b"), checkpoint=True),
        Step("c", step("c")),
    ]
    assert routes.run(NoSave(), steps)["ok"] is True
    assert ran == ["a", "b", "c", "b", "c"]  # back to a's checkpoint: b again, not straight to c


def test_the_main_menu_after_the_final_step_is_no_death(loads: list[int]) -> None:
    """The ending returns to the main menu after the credits: a final step's success ends the route there."""

    class Ended(FakeActor):
        menu = False

        def snap(self) -> Snap:
            return Snap(screen="main_menu" if self.menu else "map", dude=Dude(), level=self.level)

    def ending(a) -> bool:
        a.menu = True
        return True

    actor = Ended()
    assert routes.run(actor, [Step("out", lambda _a: True), Step("the ending", ending, final=True)])["ok"] is True
    assert loads == []
    failed = Ended()
    assert routes.run(failed, [Step("a", lambda _a: True, checkpoint=True), Step("b", ending)], start=2)["ok"] is False


def test_a_step_that_fails_again_with_nothing_changed_ends_the_route_at_once(loads: list[int]) -> None:
    """Fail fast: the hunt's odds gate refused six times in a second, the game unchanged."""
    ran: list[str] = []
    step = scripted(ran, {"b": [False] * 6})
    result = routes.run(FakeActor(), [Step("a", step("a")), Step("b", step("b"), tries=6)])
    assert result["ok"] is False and result["why"].startswith("no progress") and ran == ["a", "b", "b"]


def test_a_bug_in_a_step_is_not_a_failure_but_stops_the_run(loads: list[int]) -> None:
    """Fail fast: a KeyError is our bug, not the game's: it goes up, it is not retried."""

    def buggy(_actor) -> bool:
        raise KeyError("no such thing")

    with pytest.raises(KeyError):
        routes.run(FakeActor(), [Step("a", buggy, tries=3)])


def test_a_crashed_game_is_started_again_and_the_route_goes_on_from_its_checkpoint(
    loads: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash is a stuck game; the reload starts the game again, then loads the checkpoint."""
    started: list[int] = []
    watched: list[int] = []
    monkeypatch.setattr(routes.session, "start", lambda: started.append(1) or {"pid": 77})
    monkeypatch.setattr(routes.watchdog, "watch", watched.append)
    ran: list[str] = []

    def crash(actor) -> bool:
        ran.append("c")
        if len(ran) == 1:
            actor.alive = False  # the game's process ends mid-step
            raise routes.ReadError("the game is gone")
        return True

    steps = [Step("a", lambda _a: True, checkpoint=True), Step("c", crash), Step("d", lambda _a: True)]
    actor = FakeActor()
    original = FakeActor.__init__

    def reattach(self, pid: int = 1, log=None) -> None:  # a new game runs: the re-attached actor sees it alive
        original(self, pid, log)
        self.alive = True

    monkeypatch.setattr(FakeActor, "__init__", reattach)
    result = routes.run(actor, steps)
    assert result["ok"] and result["loads"] == {"c": 1}
    assert started == [1] and watched == [77] and actor.pid == 77 and loads == [1]
    assert ran == ["c", "c"]
    assert [e["crashed"] for e in actor.log.kinds("step_end") if "crashed" in e] == [True]
    assert actor.log.kinds("restarted") == [{"pid": 77}]


def test_a_crash_the_game_cannot_come_back_from_ends_the_route(
    loads: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse():
        raise routes.session.SessionError("the instance changed")

    monkeypatch.setattr(routes.session, "start", refuse)

    def crash(actor) -> bool:
        actor.alive = False
        return False

    steps = [Step("a", lambda _a: True, checkpoint=True), Step("c", crash)]
    actor = FakeActor()
    result = routes.run(actor, steps)
    assert result["ok"] is False and result["at"] == "c" and loads == []
    assert actor.log.kinds("restart_failed")


def test_a_load_forgets_the_finds_given_up_on(loads: list[int]) -> None:
    from f1 import loot

    loot._tried.add(("TEST.MAP", 100, 41))
    ran: list[str] = []
    step = scripted(ran, {"c": ["die", True]})
    routes.run(FakeActor(), [Step("a", step("a"), checkpoint=True), Step("c", step("c"))])
    assert loot._tried == set()


def test_exploit_steps_are_skipped_unless_the_character_allows_them(loads: list[int]) -> None:
    """Exploits are an option of the character, off by default."""
    ran: list[str] = []
    step = scripted(ran, {})
    steps = [Step("a", step("a")), Step("trick", step("trick"), exploit=True), Step("b", step("b"))]
    actor = FakeActor()
    result = routes.run(actor, steps)
    assert result["ok"] and result["skipped"] == ["trick"] and ran == ["a", "b"]
    assert actor.log.kinds("skipped") == [{"step": "trick", "why": "exploit"}]
    assert actor.log.kinds("route_start")[0]["exploits"] is False
    ran.clear()
    assert routes.run(FakeActor(), steps, exploits=True)["skipped"] == [] and ran == ["a", "trick", "b"]


def test_our_characters_play_without_exploits_and_the_urn_is_one() -> None:
    from f1 import chargen, idealist

    assert not any(b.exploits for b in chargen.BUILDS.values())
    assert chargen.build_of("Idealist") is chargen.IDEALIST and chargen.build_of("Max Stone") is None
    marked = {s.name for s in idealist.ROUTES["idealist_junktown"] if s.exploit}
    assert marked == {"Neal's urn off the bar", "Neal's urn returned"}


def test_a_crash_during_a_checkpoint_restarts_the_game_too(loads: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    """The Hub's farm: the game's process ended in the checkpoint's loot sweep."""
    monkeypatch.setattr(routes.session, "start", lambda: {"pid": 88})
    monkeypatch.setattr(routes.watchdog, "watch", lambda pid: None)
    calls = {"n": 0}

    def crashing_checkpoint(actor, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            actor.alive = False
            raise routes.ReadError("the game is gone")
        return True, 0

    monkeypatch.setattr(routes, "checkpoint", crashing_checkpoint)
    original = FakeActor.__init__

    def reattach(self, pid: int = 1, log=None) -> None:
        original(self, pid, log)
        self.alive = True

    monkeypatch.setattr(FakeActor, "__init__", reattach)
    steps = [Step("a", lambda _a: True, checkpoint=True), Step("b", lambda _a: True, checkpoint=True)]
    actor = FakeActor()
    result = routes.run(actor, steps)
    assert result["ok"] and actor.pid == 88 and loads == [1]


def test_an_ironman_run_ends_at_a_death_and_never_saves_or_loads(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {"c": ["die", True]})
    steps = [Step("a", step("a")), Step("b", step("b"), checkpoint=True), Step("c", step("c")), Step("d", step("d"))]
    actor = FakeActor()
    result = routes.run(actor, steps, ironman=True)
    assert result["ok"] is False and result["ended"] == "death" and result["step"] == 3 and result["at"] == "c"
    assert ran == ["a", "b", "c"]
    assert loads == []
    assert actor.log.kinds("save") == []  # the checkpoint only prepared


def test_an_ironman_run_without_trouble_ends_done(loads: list[int]) -> None:
    ran: list[str] = []
    step = scripted(ran, {})
    steps = [Step("a", step("a"), checkpoint=True), Step("b", step("b"))]
    actor = FakeActor()
    assert routes.run(actor, steps, ironman=True)["ended"] == "done"
    assert actor.log.kinds("save") == [] and loads == []


def racing(ran: list[str], errors: int):
    """A door step whose object scan raced the game the first `errors` times: the map's object lists rewired under
    the read while critters walked (a read of an Object at 0), the game alive and the player well."""

    def run(actor) -> bool:
        ran.append("door")
        if len(ran) <= errors:
            raise routes.ReadError("read of 132 bytes at 0x00000000 failed")
        return True

    return run


def test_an_ironman_step_that_raced_a_read_is_tried_once_more_where_the_game_stands(loads: list[int]) -> None:
    ran: list[str] = []
    steps = [Step("a", lambda _a: True), Step("the doors", racing(ran, 1)), Step("b", lambda _a: True)]
    actor = FakeActor()
    result = routes.run(actor, steps, ironman=True)
    assert result["ended"] == "done" and ran == ["door", "door"] and loads == []
    (retry,) = actor.log.kinds("retry")
    assert retry["step"] == "the doors" and retry["why"].startswith("ReadError(")
    assert result["fails"] == {}  # the race is no failure of the step's own: its tries stay whole


def test_a_second_read_error_in_the_same_step_ends_the_ironman_run(loads: list[int]) -> None:
    ran: list[str] = []
    steps = [Step("the doors", racing(ran, 2), tries=3), Step("b", lambda _a: True)]
    result = routes.run(FakeActor(), steps, ironman=True)
    assert result["ended"] == "error" and result["status"] == "technical_error" and ran == ["door", "door"]


def test_each_objective_of_a_repeated_name_gets_its_own_read_retry(loads: list[int]) -> None:
    """Names repeat on a route ("to the Hub" four times): a race on the first trip leaves the later ones their try."""
    first: list[str] = []
    later: list[str] = []
    steps = [
        Step("to the Hub", racing(first, 1)),
        Step("downtown", lambda _a: True),
        Step("to the Hub", racing(later, 1)),
    ]
    actor = FakeActor()
    result = routes.run(actor, steps, ironman=True)
    assert result["ended"] == "done" and first == ["door", "door"] and later == ["door", "door"]
    assert [r["tries"] for r in actor.log.kinds("retry")] == [1, 1]  # the film's caption: "Try 2."


def test_a_step_of_one_try_is_not_run_again_after_a_read_error(loads: list[int]) -> None:
    """Its roll's first outcome stands, and the read may have failed after the roll."""
    ran: list[str] = []
    result = routes.run(FakeActor(), [Step("Sherry turned", racing(ran, 1), tries=1)], ironman=True)
    assert result["ended"] == "error" and ran == ["door"]


def test_a_driver_taking_over_carries_the_read_retries_spent(loads: list[int]) -> None:
    ran: list[str] = []
    seen: list[dict] = []
    actor = FakeActor()
    steps = [Step("the doors", racing(ran, 1)), Step("b", lambda _a: True)]
    routes.run(actor, steps, ironman=True, on_step=lambda i: seen.append(dict(actor.objective_progress)))
    assert seen[-1]["read_retried"] == ["route.the-doors"]
    again: list[str] = []
    handed = dict(seen[1])  # handed over before the retry of the doors: their read retry is spent
    result = routes.run(FakeActor(), [Step("the doors", racing(again, 1))], ironman=True, progress=handed)
    assert result["ended"] == "error" and again == ["door"]


def test_a_handoff_written_before_read_retries_counts_none_spent(loads: list[int]) -> None:
    """A driver taking over from older code: its progress has no read_retried, so the step keeps its retry."""
    ran: list[str] = []
    handed = {"statuses": {}, "pending": {}, "leveled": 0, "fails": {}, "skipped": []}
    result = routes.run(FakeActor(), [Step("the doors", racing(ran, 1))], ironman=True, progress=handed)
    assert result["ended"] == "done" and ran == ["door", "door"]


def test_a_game_gone_mid_read_is_no_race_to_try_again(loads: list[int]) -> None:
    ran: list[str] = []

    def crash(actor) -> bool:
        ran.append("c")
        actor.alive = False
        raise routes.ReadError("the game is gone")

    actor = FakeActor()
    result = routes.run(actor, [Step("c", crash)], ironman=True)
    assert result["ended"] == "stuck" and ran == ["c"] and actor.log.kinds("retry") == []


def test_a_failed_step_names_the_last_action_that_failed_in_it(tmp_path, loads: list[int]) -> None:
    """A guard's refusal ended a step whose own verdict said nothing: the step's end and the route's name the
    refusal ("visit: unsafe to travel while poisoned") beside the empty reason."""
    import json

    from f1.telemetry import EventLog

    def to_town(actor) -> bool:
        actor.log.emit("action_end", action="visit", ok=False, reason="unsafe to travel while poisoned")
        return False

    def fine_after_a_miss(actor) -> bool:
        actor.log.emit("action_end", action="walk_to", ok=False, reason="no path")  # missed, then got there
        return True

    def quiet(actor) -> bool:
        return False

    actor = FakeActor(log=EventLog(tmp_path / "events.jsonl"))
    steps = [Step("a", fine_after_a_miss), Step("to town", to_town, tries=3), Step("b", quiet)]
    result = routes.run(actor, steps, ironman=True)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    ends = [e for e in events if e["kind"] == "step_end"]
    assert result["ended"] == "failed" and result["at"] == "to town"
    refused = "visit: unsafe to travel while poisoned"
    assert [e.get("failed_action") for e in ends] == [None, refused, refused]  # the try, then the fail-fast's
    (end,) = [e for e in events if e["kind"] == "route_end"]
    assert end["failed_action"] == refused


def test_a_step_with_no_failed_action_of_its_own_names_none(tmp_path, loads: list[int]) -> None:
    from f1.telemetry import EventLog

    def missed_once(actor) -> bool:
        actor.log.emit("action_end", action="walk_to", ok=False, reason="no path")
        return True

    actor = FakeActor(log=EventLog(tmp_path / "events.jsonl"))
    result = routes.run(actor, [Step("a", missed_once), Step("b", lambda _a: False, tries=1)], ironman=True)
    assert result["ended"] == "failed" and actor.failed_action is None


def test_the_ironman_cap_bounds_every_screen() -> None:
    try:
        watchdog.set_cap(60)
        assert watchdog.limit_for("map") == 60
        watchdog.set_cap(None)
        assert watchdog.limit_for("map") == watchdog.LIMITS["map"]
    finally:
        watchdog.set_cap(None)


def test_an_ironman_run_raises_the_fight_gates_bound() -> None:
    from f1 import risk

    try:
        risk.set_bound(risk.IRONMAN_RISK_MAX)
        assert risk.acceptable(risk.Risk(**{**_risk_fields(), "high": 0.167}))
        risk.set_bound(None)
        assert not risk.acceptable(risk.Risk(**{**_risk_fields(), "high": 0.167}))
    finally:
        risk.set_bound(None)


def _risk_fields() -> dict:
    return {"deaths": 115, "trials": 1000, "low": 0.09, "high": 0.0, "capped": 0, "wins": 885, "hp_left": 25.2,
            "turns": 8.1, "rounds": 7.6, "hit": 28, "foe_hit": 95}  # fmt: skip
