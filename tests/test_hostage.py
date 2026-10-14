"""The raider's talks (JTRAIDER.INT) on a made-up screen: the step answers the talk he opens, not a count it keeps.

Measured live: a retry that started over at the first talk's answers met his second talk (#121); a polite
exit then picked the answers, the talk went on, and the route ended as "the game did not change"."""

from types import SimpleNamespace

from f1 import dialogue, idealist_junktown, intdump, knowledge
from f1.dialogue import Dialogue, Option

# made-up texts (the step answers by message number, and knows his talks by their numbers)
TEXTS = {
    n: f"raider message {n}."
    for n in (
        101,
        105,
        110,
        111,
        112,
        113,
        114,
        115,
        116,
        121,
        122,
        123,
        131,
        132,
        133,
        136,
        145,
        146,
        147,
        148,
        149,
        153,
        154,
        155,
        168,
        169,
    )
}
# node: (his message, [(answer, next node)]); a node without answers is a gsay_message ([Done])
NODES = {
    "Raider0": (101, [(105, "Raider4")]),
    "Raider4": (110, [(111, "Raider5"), (112, "Raider7")]),
    "Raider5": (113, [(114, "Raider6")]),
    "Raider6": (115, []),
    "Raider7": (116, []),
    "Raider9": (121, [(122, "Raider10"), (123, "Raider14")]),
    "Raider14": (131, [(132, "Raider14a")]),
    "Raider15": (133, []),
    "Raider16": (136, []),
    "Raider21": (145, [(146, "Raider22")]),
    "Raider22": (147, [(148, "Raider23"), (149, "Raider24")]),
    "Raider24": (153, [(154, "Raider24a"), (155, "Raider25")]),
    "Raider31": (168, [(169, "Raider32")]),
}


class Raider:
    def __init__(self, *, var1=0, map_var3=1, roll=True, caps=150):
        self.var1, self.var6, self.map_var3, self.roll, self.caps = var1, 0, map_var3, roll, caps
        self.save, self.node, self.chosen, self.talks = 1, None, [], []

    def talk(self) -> None:  # talk_p_proc
        if not self.var1:
            self.var1, node = 1, "Raider0"
        elif self.map_var3 == 1:
            self.map_var3, node = 2, "Raider9"
        else:
            node = "Raider21" if self.var6 else "Raider31"
        self.talks.append(node)
        self.node = node

    def screen(self) -> Dialogue:
        if self.node is None:
            return Dialogue(False, "", "", [])
        message, answers = NODES[self.node]
        entries = tuple(Option(i, 337, n, 0, TEXTS[n]) for i, (n, _next) in enumerate(answers))
        if not answers:
            entries = (Option(0, 337, 0, 0, "[Done]"),)
        return Dialogue(True, "Traveler", TEXTS[message], [o.text for o in entries], 337, message, entries, "jtraider")

    def choose(self, index: int) -> Dialogue:
        _message, answers = NODES[self.node]
        if not answers:
            self.node = None
            return self.screen()
        number, node = answers[index]
        self.chosen.append(number)
        if node == "Raider14a":
            self.var6, self.node = (1, "Raider16") if self.roll else (0, "Raider15")  # the Speech +20 roll
            return self.screen()
        if node == "Raider24a":
            self.caps -= 100
            self.save, self.node = 2, None  # safe()
            return self.screen()
        self.node = node
        return self.screen()


def setup(monkeypatch, raider: Raider):
    monkeypatch.setattr(knowledge, "load", lambda: {"scripts": ["x"] * 336 + ["jtraider"]})
    monkeypatch.setattr(intdump, "messages", lambda list_id: TEXTS if list_id == 337 else {})
    monkeypatch.setattr(dialogue, "read", lambda _mem: raider.screen())
    monkeypatch.setattr(dialogue, "choose", lambda _mem, i: raider.choose(i))
    monkeypatch.setattr(idealist_junktown.win32, "wait_for", lambda predicate, *a: predicate())
    monkeypatch.setattr(idealist_junktown, "settle", lambda a, plan=None: True)
    monkeypatch.setattr(idealist_junktown, "clear_line", lambda *a: True)
    monkeypatch.setattr(idealist_junktown.geometry, "ring", lambda tile, r: [tile])
    monkeypatch.setattr(idealist_junktown.nav, "obstacles", lambda *a: SimpleNamespace(blocked=frozenset()))
    values = {"SAVE_SINTHIA": lambda: raider.save}
    monkeypatch.setattr(idealist_junktown.quests, "gvar", lambda a, name: values[name]())
    monkeypatch.setattr(idealist_junktown.quests, "mvar", lambda a, n: raider.map_var3 if n == 3 else 0)
    monkeypatch.setattr(idealist_junktown.quests, "find_critter", lambda a, name: SimpleNamespace(tile=5))
    events = []
    actor = SimpleNamespace(
        mem=None,
        holster=lambda *a: None,
        _count=lambda pid: raider.caps,
        in_combat=lambda: False,
        snap=lambda: SimpleNamespace(
            screen="dialogue" if raider.node else "game", elevation=0, dude=SimpleNamespace(address=0, tile=5)
        ),
        talk_to=lambda who: (raider.talk(), SimpleNamespace(ok=True))[1],
        log=SimpleNamespace(emit=lambda kind, **kw: events.append((kind, kw))),
    )
    return actor, events


def test_a_retry_after_the_first_talk_answers_his_second_and_third(monkeypatch):
    raider = Raider(var1=1)  # the measured case: the first talk ended on his backing off (#112) in the failed try
    actor, events = setup(monkeypatch, raider)
    before = idealist_junktown.hostage_progress(actor)
    assert idealist_junktown.the_hostage(actor).status == "achieved"
    assert raider.talks == ["Raider9", "Raider21"]
    assert raider.chosen == [123, 132, 146, 149, 154]
    assert [kw["talk"] for kind, kw in events if kind == "hostage"] == [2, 3]
    assert idealist_junktown.hostage_progress(actor) != before


def test_he_speaks_first_on_sight_and_all_three_talks_follow(monkeypatch):
    raider = Raider()
    raider.talk()  # critter_p_proc: dialogue_system_enter on sight
    actor, _ = setup(monkeypatch, raider)
    assert idealist_junktown.the_hostage(actor).status == "achieved"
    assert raider.talks == ["Raider0", "Raider9", "Raider21"]
    assert raider.chosen == [105, 111, 114, 123, 132, 146, 149, 154]


def test_a_lost_speech_roll_is_reported_and_his_third_talk_is_not_opened(monkeypatch):
    raider = Raider(var1=1, roll=False)
    actor, _ = setup(monkeypatch, raider)
    result = idealist_junktown.the_hostage(actor)
    assert result.status == "unavailable"
    assert raider.talks == ["Raider9"]


def test_the_last_talk_waits_for_100_caps(monkeypatch):
    raider = Raider(var1=1, map_var3=2, caps=40)
    raider.var6 = 1
    actor, _ = setup(monkeypatch, raider)
    assert idealist_junktown.the_hostage(actor).status == "recoverable"
    assert raider.talks == [] and raider.chosen == []
