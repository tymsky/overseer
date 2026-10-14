"""Regressions for failed resources, deferred work and optional quest dependencies."""

from types import SimpleNamespace

import pytest

from f1 import ending_goals, idealist_necropolis, route_runner
from f1.objectives import ObjectiveOutcome, identified_steps, route_manifest
from f1.route_support import Step


class Actor:
    def __init__(self):
        self.events = []
        self.log = SimpleNamespace(emit=lambda kind, **data: self.events.append((kind, data)))
        self.count = 0
        self.hp = 30

    def snap(self):
        return SimpleNamespace(
            screen="map", map_name="TEST.MAP", elevation=0, level=2, dude=SimpleNamespace(tile=20000, hp=self.hp)
        )

    def game_alive(self):
        return True

    def items(self):
        return [(1, 98, self.count)]


@pytest.fixture
def actor(monkeypatch):
    monkeypatch.setattr(route_runner, "prepare", lambda a, carried: None)
    return Actor()


def test_unavailable_optional_parent_suppresses_dependent_actions(actor):
    called = []
    steps = [
        Step("turn", lambda a: ObjectiveOutcome.unavailable("failed lawful roll"), optional=True, id="turn"),
        Step("testimony", lambda a: called.append(True) or True, optional=True, requires=("turn",)),
    ]
    result = route_runner.run(actor, steps, ironman=True)
    assert result["ended"] == "done" and result["skipped"] == ["turn", "testimony"]
    assert called == []
    assert not result["best_ending"]


def test_deferred_work_must_be_resolved_by_a_later_verified_step(actor):
    deferred = Step("first visit", lambda a: ObjectiveOutcome.deferred("need better equipment", "caves"))
    unresolved = route_runner.run(actor, [deferred], ironman=True)
    assert unresolved["ended"] == "failed" and "caves" in unresolved["pending"]
    later = Step("second visit", lambda a: ObjectiveOutcome.achieved(resolves=("caves",)))
    assert route_runner.run(actor, [deferred, later], ironman=True)["ended"] == "done"
    assert any(kind == "obligation_resolved" for kind, _ in actor.events)


def test_resource_progress_prevents_a_false_unchanged_state_failure(actor):
    def acquire(a):
        a.count += 1
        return a.count >= 3

    result = route_runner.run(actor, [Step("acquire parts", acquire, tries=3)], ironman=True)
    assert result["ended"] == "done" and result["fails"] == {"acquire parts": 2}


def test_irrecoverable_missing_parts_do_not_execute_the_successor(actor, monkeypatch):
    monkeypatch.setattr(idealist_necropolis.quests, "gvar", lambda a, name: 0)
    monkeypatch.setattr(idealist_necropolis, "spare_junk", lambda a: False)
    actor._count = lambda pid: 0
    successors = []
    result = route_runner.run(
        actor,
        [
            Step("pump", idealist_necropolis.the_pump, id="pump"),
            Step("chip", lambda a: successors.append(True) or True),
        ],
        ironman=True,
    )
    assert result["ended"] == "failed" and result["status"] == "unavailable"
    assert successors == []


def test_returning_to_the_pump_floor_is_not_spare_acquisition(actor, monkeypatch):
    actor.elevation = 1
    actor._count = lambda pid: 1
    actor.snap = lambda: SimpleNamespace(elevation=actor.elevation)
    monkeypatch.setattr(idealist_necropolis, "take_from", lambda *args, **kw: lambda a: True)

    def move(a, names, done, label):
        actor.elevation = 0 if label == "the sewer hole" else 1
        return True

    monkeypatch.setattr(idealist_necropolis.quests, "use_scenery", move)
    assert not idealist_necropolis.spare_junk(actor)
    assert actor.events[-1] == ("spare_junk", {"before": 1, "after": 1, "acquired": False})


def test_chip_retrieval_checks_the_pump_game_predicate_before_input(actor, monkeypatch):
    monkeypatch.setattr(idealist_necropolis.quests, "gvar", lambda a, name: 0)
    result = idealist_necropolis.take_chip_after_repair(actor)
    assert result.status == "unavailable"


def good_ending():
    values = dict.fromkeys(ending_goals.VARIABLES, 0)
    values.update(
        MASTER_BLOWN=1,
        VATS_BLOWN=1,
        NECROP_WATER_CHIP_TAKEN=1,
        NECROP_WATER_PUMP_FIXED=2,
        TANDI_STATUS=2,
        CAPTURE_GIZMO=2,
        BECOME_AN_INITIATE=2,
        RAIDERS=2,
        GARL_DEAD=1,
        TOTAL_RAIDERS=7,
    )
    return values


@pytest.mark.parametrize(
    "changes,missing",
    [
        ({"NECROP_WATER_PUMP_FIXED": 0}, "necropolis"),
        ({"CAPTURE_GIZMO": 1}, "junktown"),
        ({"FOLLOWERS_INVADED": 1}, "followers"),
        ({"KILLIAN_DEAD": 1}, "junktown"),
        ({"TOTAL_RAIDERS": 8}, "raiders"),
        ({"MASTER_BLOWN": 0}, "main_quests"),
    ],
)
def test_reaching_the_ending_cannot_override_lost_town_or_main_quest_goals(changes, missing):
    values = good_ending() | changes
    audit = ending_goals.evaluate(values)
    assert not audit["ok"] and missing in audit["missing"]


def test_best_reachable_ending_and_prerequisite_audits():
    assert ending_goals.evaluate(good_ending())["ok"]
    assert ending_goals.evaluate(good_ending() | {"MASTER_BLOWN": 0}, final=False)["ok"]


def test_objective_identity_survives_an_unrelated_insertion():
    steps = [Step("prepare", lambda a: True), Step("pump", lambda a: True)]
    before = identified_steps(steps, "necropolis")
    after = identified_steps([Step("bonus", lambda a: True), *steps], "necropolis")
    assert before[1].id == after[2].id
    assert route_manifest(before)["route_hash"] != route_manifest(after)["route_hash"]


def test_optional_deferral_cannot_weaken_an_existing_required_obligation(actor):
    steps = [
        Step("required", lambda a: ObjectiveOutcome.deferred("needed", "parts")),
        Step("bonus", lambda a: ObjectiveOutcome.deferred("still needed", "parts"), optional=True),
    ]
    result = route_runner.run(actor, steps, ironman=True)
    assert result["ended"] == "failed" and result["pending"]["parts"]["required"]


def test_loaded_segment_reconstructs_its_verified_predecessor(actor):
    actor.count = 1
    steps = [
        Step("pump", lambda a: True, id="pump", verify=lambda a: a.count == 1),
        Step("chip", lambda a: True, requires=("pump",)),
    ]
    assert route_runner.run(actor, steps, start=2, ironman=True)["ended"] == "done"
    actor.count = 0
    assert route_runner.run(actor, steps, start=2, ironman=True)["ended"] == "failed"


def test_reload_restores_the_saved_objective_ledger(actor, monkeypatch):
    monkeypatch.setattr(route_runner, "checkpoint", lambda *args, **kw: (True, 2))

    def reload(a, prefs, carried):
        a.hp = 30
        return True

    monkeypatch.setattr(route_runner, "reload_checkpoint", reload)
    seen, dies = [], [True]

    def resolve(a):
        seen.append("caves" in a.pending_objectives)
        return ObjectiveOutcome.achieved(resolves=("caves",))

    def death(a):
        if dies:
            dies.pop()
            a.hp = 0
            return False
        return True

    steps = [
        Step("defer", lambda a: ObjectiveOutcome.deferred("later", "caves")),
        Step("save", lambda a: True, checkpoint=True),
        Step("resolve", resolve),
        Step("fight", death),
    ]
    assert route_runner.run(actor, steps)["ended"] == "done"
    assert seen == [True, True]


def test_loaded_game_predicate_overrules_a_stale_achieved_ledger(actor):
    called = []
    steps = [
        Step("pump", lambda a: True, id="pump", verify=lambda a: False, obligation="pump"),
        Step("chip", lambda a: called.append(True) or True, requires=("pump",)),
    ]
    result = route_runner.run(actor, steps, start=2, ironman=True, progress={"statuses": {"pump": "achieved"}})
    assert result["ended"] == "failed" and called == []
    assert actor.pending_objectives["pump"]["required"]


def test_loaded_game_clears_a_stale_pending_obligation(actor):
    steps = [Step("pump", lambda a: True, id="pump", verify=lambda a: True, obligation="pump")]
    progress = {"statuses": {"pump": "deferred"}, "pending": {"pump": {"required": True}}}
    assert route_runner.run(actor, steps, start=2, ironman=True, progress=progress)["ended"] == "done"
    assert progress["pending"]  # the caller's checkpoint remains immutable


def test_a_previous_run_cannot_supply_this_runs_ending_audit(actor):
    actor.ending_audit = {"ok": True, "ending_seen": True}
    result = route_runner.run(actor, [Step("prefix", lambda a: True)], ironman=True)
    assert not result["best_ending"]


def test_resolving_metadata_cannot_clear_an_unverified_required_obligation(actor):
    steps = [
        Step("defer", lambda a: ObjectiveOutcome.deferred("not done", "sands.tandi")),
        Step("conditional no-op", lambda a: True, resolves=("sands.tandi",), verify=lambda a: False),
    ]
    result = route_runner.run(actor, steps, ironman=True)
    assert result["ended"] == "failed" and actor.tandi_later


def test_unverified_success_cannot_enable_a_dependent_action(actor):
    called = []
    steps = [
        Step("parts", lambda a: True, id="parts", verify=lambda a: False, optional=True, tries=1),
        Step("use parts", lambda a: called.append(True) or True, requires=("parts",)),
    ]
    assert route_runner.run(actor, steps, ironman=True)["ended"] == "failed"
    assert called == []


def test_optional_memory_error_is_not_an_optional_roll_failure(actor):
    def unreadable(a):
        raise route_runner.ReadError("cannot read objective state")

    result = route_runner.run(actor, [Step("bonus", unreadable, optional=True)], ironman=True)
    assert result["ended"] == "error" and result["status"] == "technical_error"
