"""quests.mend_on_the_road: after a road fight, the poison rested out before more travel (a full run's 33 -> 6 HP);
the HP itself is left to the travel days (a rest until healed took 70 game hours)."""

from types import SimpleNamespace

import pytest

from f1 import quests
from f1.actions import PID_STIMPAK


class Mender:
    """The poison, HP and items that mend_on_the_road reads. Rests as the game's: each poison tick takes 1 HP and 2
    poison and a tick at 5 HP or less ends the rest (critter.cc); "until healed" fills the HP and outlasts the poison,
    and passes no time at full HP (pipboy.cc)."""

    max_hp = 48

    def __init__(self, hp: int, poison: int, antidotes: int = 0, stimpaks: int = 4) -> None:
        self.hp, self.poison = hp, poison
        self.items = {quests.PID_ANTIDOTE: antidotes, PID_STIMPAK: stimpaks}
        self.rests: list[str] = []
        self.used: list[int] = []
        self.log = SimpleNamespace(emit=lambda kind, **kw: setattr(self, "mended", kw))

    def snap(self):
        return SimpleNamespace(dude=SimpleNamespace(hp=self.hp))

    def in_combat(self) -> bool:
        return False

    def _count(self, pid: int) -> int:
        return self.items.get(pid, 0)

    def use_on_self(self, pid: int):
        self.items[pid] -= 1
        self.used.append(pid)
        if pid == quests.PID_ANTIDOTE:
            self.poison = max(0, self.poison - 50)
        else:
            self.hp = min(self.max_hp, self.hp + 15)
        return SimpleNamespace(ok=True)

    def rest(self, how: str):
        self.rests.append(how)
        if how == "until healed":
            if self.hp < self.max_hp:
                self.hp, self.poison = self.max_hp, 0
            return SimpleNamespace(ok=True)
        while self.poison >= 2:
            self.poison -= 2
            self.hp -= 1
            if self.hp <= 5:
                break
        if self.poison == 1:
            self.poison = 0
        return SimpleNamespace(ok=True)


@pytest.fixture(autouse=True)
def poison_from_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quests, "poison", lambda actor: actor.poison)


def test_the_live_case_rests_only_the_poison_out() -> None:
    a = Mender(hp=27, poison=39, stimpaks=3)  # the live trip: "until healed" took 70 h here
    quests.mend_on_the_road(a)
    assert a.rests == [quests.POISON_REST] and a.poison == 0
    assert a.hp == 27 - 19 + 2 * 15 and a.used == [PID_STIMPAK] * 2  # 8, then 23: under half, the kept ones


def test_the_film_runs_case_heals_when_the_poison_stops_the_rest() -> None:
    a = Mender(hp=33, poison=60, stimpaks=4)  # a tick at 5 HP ends the rest; stimpaks, then the rest again
    quests.mend_on_the_road(a)
    assert a.rests == [quests.POISON_REST, quests.POISON_REST] and a.poison == 0
    assert a.hp >= quests.MEND_FLOOR * a.max_hp


def test_poisoned_at_full_hp_rests_without_stimpaks() -> None:
    a = Mender(hp=48, poison=30)
    quests.mend_on_the_road(a)
    assert a.rests == [quests.POISON_REST] and a.poison == 0 and a.hp == 33 and a.used == []


def test_a_poison_of_one_is_left() -> None:
    a = Mender(hp=48, poison=1, antidotes=1)  # one tick, 1 HP at most: a live trip's leftover
    quests.mend_on_the_road(a)
    assert a.rests == [] and a.used == []


def test_an_antidote_comes_first() -> None:
    a = Mender(hp=44, poison=20, antidotes=1)
    quests.mend_on_the_road(a)
    assert a.used == [quests.PID_ANTIDOTE] and a.rests == []


def test_healthy_and_clean_goes_on() -> None:
    a = Mender(hp=40, poison=0)
    quests.mend_on_the_road(a)
    assert a.rests == [] and a.used == []


def test_wounded_uses_spare_stimpaks_and_no_rest() -> None:
    a = Mender(hp=20, poison=0, stimpaks=6)  # two above the four kept for fights
    quests.mend_on_the_road(a)
    assert a.used == [PID_STIMPAK, PID_STIMPAK] and a.rests == []  # 20 -> 35 -> 48


def test_low_with_no_stimpaks_rests_until_healed() -> None:
    a = Mender(hp=15, poison=0, stimpaks=0)
    quests.mend_on_the_road(a)
    assert a.rests == ["until healed"] and a.hp == 48
