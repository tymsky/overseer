"""The normal job may be unavailable after an NPC kills the assassin; the script provides another path."""

from types import SimpleNamespace

from f1 import agent_junktown as town
from f1 import idealist_junktown


def setup(monkeypatch, *, killian_dead=0):
    values = {
        "KILLIAN_DEAD": killian_dead,
        "HIRED_BY_KILLIAN": 0,
        "HIRED_BY_GIZMO": 0,
        "GIZMO_DEAD": 0,
        "GOT_CONFESSION": 0,
    }
    calls = []
    actor = SimpleNamespace(
        holster=lambda *args: None,
        _count=lambda pid: int(values["HIRED_BY_KILLIAN"]),
        log=SimpleNamespace(emit=lambda *args, **kw: None),
    )
    monkeypatch.setattr(town.quests, "gvar", lambda a, name: values[name])
    monkeypatch.setattr(town.quests, "take_exit", lambda a, dest: calls.append(dest) or True)
    monkeypatch.setattr(town, "settle", lambda a: True)
    monkeypatch.setattr(town, "by_day", lambda a: True)

    def talk(a, name, plan, **kwargs):
        calls.append(plan)
        if plan == town.GIZMO_HIRE:
            values["HIRED_BY_GIZMO"] = 1
        if plan == town.KILLIAN_ALTERNATE_JOB:
            values["HIRED_BY_KILLIAN"] = 1
        if plan == town.GIZMO_ALTERNATE_CONFESSION:
            values["GOT_CONFESSION"] = 1
        return True

    monkeypatch.setattr(town.quests, "talk", talk)
    return actor, values, calls


def test_assassin_killed_without_player_damage_uses_the_guarded_alternate_job(monkeypatch):
    actor, values, calls = setup(monkeypatch)
    assert town.killians_job(actor)
    assert calls == [town.KILLIAN_JOB, "JUNKCSNO", town.GIZMO_HIRE, "JUNKKILL", town.KILLIAN_ALTERNATE_JOB]
    assert values["HIRED_BY_KILLIAN"] == 1
    monkeypatch.setattr(town, "recorder_in_hand", lambda a: True)
    assert town.gizmos_confession(actor)
    assert calls[-1] == town.GIZMO_ALTERNATE_CONFESSION


def test_lost_ending_precondition_prevents_an_alternate_job_attempt(monkeypatch):
    actor, _, calls = setup(monkeypatch, killian_dead=1)
    assert town.killians_job(actor).status == "unavailable"
    assert calls == []


def test_unavailable_alternate_hiring_does_not_repeat_a_missing_normal_job(monkeypatch):
    actor, values, calls = setup(monkeypatch)
    values["GIZMO_DEAD"] = 1
    assert town.killians_job(actor).status == "unavailable"
    assert calls == [town.KILLIAN_JOB]


def test_sherry_turn_is_attempted_once_internally(monkeypatch):
    calls = []
    actor = SimpleNamespace(holster=lambda value: None)
    monkeypatch.setattr(idealist_junktown.quests, "gvar", lambda a, name: 0)
    monkeypatch.setattr(idealist_junktown, "on_map", lambda a, name: True)
    monkeypatch.setattr(idealist_junktown, "settle", lambda a: None)
    monkeypatch.setattr(idealist_junktown.quests, "talk", lambda a, name, plan, **kw: calls.append(plan))
    assert not idealist_junktown.sherry_turned(actor)
    assert calls.count(idealist_junktown.SHERRY_TURN) == 1
