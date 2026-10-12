"""Talks by the scripts' nodes: the tree read from a script, conditions read where they can be, the screens predicted
after an option and after a line, a talk followed against them, plans by message number, and a talk that opens on a
walk answered."""

import pytest
from conftest import needs_knowledge

from f1 import chargen, dialogue, intdump, knowledge, nav, talk, talktree
from f1.dialogue import Dialogue, Option
from f1.talktree import END, Item, Node

INSTANCE_BUILT = (knowledge.INSTANCE / "MASTER.DAT").exists()
needs_game = pytest.mark.skipif(not INSTANCE_BUILT, reason="instance not built")


def test_conditions_are_read_where_every_part_is_known() -> None:
    values = {("local_var", 1): 2, ("global_var", "SEED"): 0, ("map_var", 3): 5}
    read = lambda kind, key: values.get((kind, key))
    assert talktree.evaluate("(local_var(1) >= 2)", read) is True
    assert talktree.evaluate("not (local_var(1) >= 3)", read) is True
    assert talktree.evaluate("((global_var(SEED) == 0) and (map_var(3) > 4))", read) is True
    assert talktree.evaluate("(local_var(7) == 0)", read) is None  # not read
    assert talktree.evaluate("(var3 and (local_var(1) == 2))", read) is None  # a script variable
    assert talktree.evaluate("obj_can_see_obj(self_obj(), dude_obj())", read) is None
    assert talktree.evaluate("(local_var(1) / 0)", read) is None


def test_an_if_holds_its_condition_and_the_else_its_opposite() -> None:
    # if not C goto 0x30 at 0x10; the then-block ends by a goto 0x40 at 0x2E (the target less the jump's 2 bytes)
    events = [("if", 0x10, "C", 0x30), ("goto", 0x2E, 0x40)]
    guards = talktree._guards(events)
    inside = lambda at: tuple(c for s, e, c in guards if s < at < e)
    assert inside(0x20) == ("C",)
    assert inside(0x30) == ("not C",)
    assert inside(0x40) == ()


def _nodes() -> dict[str, Node]:
    """N1 asks (5:100): 101 -> N2, 102 (IQ <= 3) -> N3. N2 says 110 or 111 by local 0, then N4's reply 120. N3 sets
    local 0 and ends the talk."""
    return {
        "N1": Node("N1", 1, (
            Item("reply", 1, (), 5, 100),
            Item("option", 2, (), 5, 101, "N2", 4),
            Item("option", 3, (), 5, 102, "N3", -3),
        )),
        "N2": Node("N2", 2, (
            Item("say", 1, ("(local_var(0) == 1)",), 5, 110),
            Item("say", 2, ("not (local_var(0) == 1)",), 5, 111),
            Item("call", 3, (), target="N4"),
        )),
        "N3": Node("N3", 3, (Item("set", 1, (), text="set_local_var(0, 1)"),)),
        "N4": Node("N4", 4, (Item("reply", 1, (), 5, 120), Item("option", 2, (), 5, 121, "N3", 4))),
    }  # fmt: skip


def test_the_screens_after_an_option_and_after_a_line() -> None:
    nodes = _nodes()
    assert talktree.first(nodes, "N2") == {((), "N2", 0), ((), "N2", 1)}  # local 0 not read: both lines
    one = lambda kind, key: 1 if (kind, key) == ("local_var", 0) else None
    assert talktree.first(nodes, "N2", one) == {((), "N2", 0)}
    assert talktree.after_line(nodes, ((), "N2", 0), True, one) == {((("N2", 3),), "N4", 0)}
    assert talktree.first(nodes, "N3") == {END}  # no screen: the talk ends
    assert talktree.expected_options(nodes, ((), "N1", 0), 5) == (101,)
    assert talktree.expected_options(nodes, ((), "N1", 0), 2) == (102,)


def test_a_variable_set_on_the_way_is_not_trusted() -> None:
    nodes = _nodes()
    nodes["N5"] = Node("N5", 5, (Item("set", 1, (), text="set_local_var(0, 0)"), Item("call", 2, (), target="N2")))
    one = lambda kind, key: 1 if (kind, key) == ("local_var", 0) else None
    assert talktree.first(nodes, "N5", one) == {((("N5", 2),), "N2", 0), ((("N5", 2),), "N2", 1)}


def _screen(reply: int, options: list[tuple[int, int, int]]) -> Dialogue:
    entries = tuple(Option(i, lst, msg, proc, f"o{msg}") for i, (lst, msg, proc) in enumerate(options))
    return Dialogue(True, "Someone", f"r{reply}", [o.text for o in entries], 5, reply, entries, "fake", 0x1000)


class _Talk:
    """A fake talk: the screens in order, the next one shown when an option is chosen."""

    def __init__(self, screens: list[Dialogue]) -> None:
        self.screens, self.chosen = screens, []

    def read(self, _mem) -> Dialogue:
        return self.screens[0] if self.screens else Dialogue(False, "", "", [])

    def choose(self, _mem, index: int) -> Dialogue:
        self.chosen.append(self.screens[0].entries[index].message)
        self.screens.pop(0)
        return self.read(None)


def _fake(monkeypatch, screens: list[Dialogue], nodes: dict[str, Node], local0: int | None) -> _Talk:
    fake = _Talk(screens)
    monkeypatch.setattr(dialogue, "read", fake.read)
    monkeypatch.setattr(talktree, "tree", lambda _name: nodes)
    monkeypatch.setattr(talk, "reader", lambda _mem, _t: lambda kind, key: local0 if kind == "local_var" else None)
    monkeypatch.setattr(chargen, "special", lambda _mem: [5, 5, 5, 5, 5, 5, 5])
    return fake


def test_a_talk_followed_by_its_nodes(monkeypatch) -> None:
    done = (dialogue.DONE_LIST, dialogue.DONE_LIST, 0)
    screens = [_screen(100, [(5, 101, 2)]), _screen(110, [done]), _screen(120, [(5, 121, 3)])]
    fake = _fake(monkeypatch, screens, _nodes(), 1)
    out = talk.follow(None, (101, 121), choose=fake.choose)
    assert fake.chosen == [101, dialogue.DONE_LIST, 121]
    assert [s.get("node") for s in out["steps"]] == [["N1:0"], ["N2:0"], ["N4:0"], None]
    assert out["predicted"] == "3/3"  # 110 (local 0 read), then 120, then the end
    assert out["procs"] == "2/2"
    assert out["ok"]


def test_an_unpredicted_screen_is_counted_and_located(monkeypatch) -> None:
    done = (dialogue.DONE_LIST, dialogue.DONE_LIST, 0)
    screens = [_screen(100, [(5, 101, 2)]), _screen(111, [done]), _screen(120, [(5, 121, 3)])]
    fake = _fake(monkeypatch, screens, _nodes(), 1)  # local 0 read as 1, yet the other line shows
    out = talk.follow(None, {100: 101, 120: 121}, choose=fake.choose)
    assert out["steps"][1]["ok"] is False and out["steps"][1]["node"] == ["N2:1"]
    assert out["predicted"] == "2/3"  # located afresh at 111: what follows in its node is still predicted
    assert out["ok"]


def test_a_plan_names_options_by_message_number_or_by_their_text(monkeypatch) -> None:
    d = _screen(100, [(5, 101, 2), (5, 102, 3)])
    monkeypatch.setattr(
        intdump, "messages", lambda list_id: {101: "Tell me\n more.", 102: "Bye."} if list_id == 5 else {}
    )
    assert dialogue.plan_index(d, "#102") == 1
    assert dialogue.plan_index(d, "tell me more") == 0  # the message file's text, its line break as a space
    assert dialogue.plan_index(d, "bye.") == 1
    assert dialogue.pick_by_plan(d, ["nothing like it"]) == 0


class _Actor:
    def __init__(self, screens: list[str]) -> None:
        self.screens, self.mem, self.holstered = screens, None, 0

    def snap(self):
        return type("S", (), {"screen": self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]})()


def test_a_walk_answers_a_talk_on_sight_and_walks_on(monkeypatch) -> None:
    walks = iter([(True, "left the map (dialogue, JUNKENT.MAP)"), (True, "arrived")])
    monkeypatch.setattr(nav, "go_to", lambda *a, **k: next(walks))
    monkeypatch.setattr(talk, "answer_on_sight", lambda actor, log=None: {"ok": True, "script": "jtgengrd"})
    ok, why, talks = talk.walk(_Actor(["dialogue", "map"]), {1})
    assert (ok, why, [t["script"] for t in talks]) == (True, "arrived", ["jtgengrd"])


def test_a_talk_on_sight_not_named_stops_the_walk(monkeypatch) -> None:
    monkeypatch.setattr(nav, "go_to", lambda *a, **k: (True, "left the map (dialogue, X)"))
    monkeypatch.setattr(talk, "answer_on_sight", lambda actor, log=None: {"ok": False, "why": "a talk on the way: x"})
    ok, why, talks = talk.walk(_Actor(["dialogue"]), {1})
    assert (ok, why, len(talks)) == (False, "a talk on the way: x", 1)


@needs_game
@needs_knowledge
def test_the_junktown_guards_talk_on_sight_by_the_script() -> None:
    nodes = talktree.tree("jtgengrd")
    route = talk.ON_SIGHT["jtgengrd"].routes[(37, 110)]
    at = talktree.locate(nodes, 37, 110)
    assert [p[1] for p in at] == ["JTGenGrd00"]
    for message in route:  # each option leads to the next reply, the last one ends the talk
        option = next(o for o in talktree.options_after(nodes, at[0]) if o.message == message)
        at = sorted(talktree.first(nodes, option.target), key=str)
    assert at == [END]


@needs_game
@needs_knowledge
def test_aradesh_s_quest_branch_is_predicted_from_his_reaction() -> None:
    nodes = talktree.tree("aradesh")
    reaction = lambda level: lambda kind, key: level if (kind, key) == ("local_var", 1) else 0
    as_screens = lambda ps: sorted(talktree.screen_message(nodes, p)[1] for p in ps)
    assert as_screens(talktree.first(nodes, "aradesh19", reaction(2))) == [163]
    assert as_screens(talktree.first(nodes, "aradesh19", reaction(1))) == [164]
    assert as_screens(talktree.first(nodes, "aradesh19")) == [163, 164]
    # the quest's node says its line and sets the seed (talked into it: 199 -> aradesh29)
    sets = [i.text for i in nodes["aradesh29"].items if i.kind == "set"]
    assert sets == ["set_global_var(RADSCORPION_SEED, 1)"]
