from types import SimpleNamespace

from f1 import hooks


class EntryPoint:
    def __init__(self, name, obj):
        self.name, self.obj = name, obj

    def load(self):
        return self.obj


def registered(monkeypatch, **objs):
    eps = [EntryPoint(name, obj) for name, obj in objs.items()]
    monkeypatch.setattr(hooks, "entry_points", lambda group: eps if group == hooks.GROUP else [])


def test_no_hooks_add_nothing(monkeypatch):
    registered(monkeypatch)
    assert hooks.load() == {} and hooks.call("start", {}) == {} and hooks.checks({}) == []


def test_hooks_merge_in_name_order_and_skip_missing_functions(monkeypatch):
    a = SimpleNamespace(start=lambda env: {"x": 1, "who": "a"}, checks=lambda ev: [("a ok", True, "")])
    b = SimpleNamespace(start=lambda env: {"who": "b"})
    c = SimpleNamespace()
    registered(monkeypatch, b=b, c=c, a=a)
    assert list(hooks.load()) == ["a", "b", "c"]
    assert hooks.call("start", {}) == {"x": 1, "who": "b"}
    assert hooks.call("status") == {}
    assert hooks.checks({}) == [("a ok", True, "")]
