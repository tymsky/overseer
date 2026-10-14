"""The Glow's mainframe (f1/idealist_glow.py)."""

from f1 import idealist_glow as glow
from f1 import quests


def test_a_denied_mainframe_is_asked_again(monkeypatch):
    # the access request denied twice (ZAX.INT Mainframe00, Science -10) and the route ended there
    state = {"talks": 0, "WATER_CHIP_3": 0}

    def zax(actor, goal, most=glow.ZAX_TRIES):
        state["talks"] += 1
        if state["talks"] < 3:
            return True  # denied
        for _, g in glow.ZAX_RECORDS:
            state[g] = 1
        return False

    monkeypatch.setattr(glow, "zax", zax)
    monkeypatch.setattr(quests, "gvar", lambda actor, name: state.get(name, 0))
    assert glow.zax_downloads(None) and state["talks"] == 3


def test_a_mainframe_denied_every_time_is_asked_a_bounded_number_of_times(monkeypatch):
    talks = []
    monkeypatch.setattr(glow, "zax", lambda actor, goal, most=glow.ZAX_TRIES: talks.append(goal) or True)
    monkeypatch.setattr(quests, "gvar", lambda actor, name: 0)
    assert not glow.zax_power(None) and len(talks) == glow.ZAX_ACCESS_TALKS
