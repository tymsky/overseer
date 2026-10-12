"""What loot is worth taking, judged against what the character has (the prototypes from the knowledge base)."""

import pytest

from f1 import knowledge, loot, weapons

PISTOL, KNIFE, AP_10MM, JHP_10MM = 0x08, 0x04, 0x1E, 0x1D
RIFLE, AMMO_223, SHOTGUN, SHELLS = 0x0A, 0x22, 0x5E, 0x5F
LEATHER, METAL = 0x01, 0x02


@pytest.fixture(scope="module", autouse=True)
def protos() -> None:
    try:
        knowledge.load()
    except (OSError, KeyError, ValueError):
        pytest.skip("no knowledge base here (python -m f1.knowledge build)")


def the_idealist(**changes) -> loot.Wants:
    """Level 1: a 10mm pistol with 12 rounds, a knife, Small Guns 43, Fast Shot, 9 AP, no armour."""
    guns = (weapons.Carried(PISTOL, 12, JHP_10MM, 24), weapons.Carried(KNIFE))
    skills = {"small guns": 43, "melee weapons": 48, "unarmed": 48}
    rules = weapons.Rules(strength=6, fast_shot=True)
    best = max(o.score for o in weapons.choose(list(guns), skills, rules, 9))
    base = {"guns": guns, "best": best, "skills": skills, "rules": rules, "max_ap": 9, "worn_cost": 0,
            "have": frozenset({PISTOL, KNIFE, JHP_10MM}), "ammo": ((JHP_10MM, 24),)}  # fmt: skip
    return loot.Wants(**(base | changes))


def test_money_medicine_books_and_light_are_always_worth_it() -> None:
    w = the_idealist()
    assert loot.worth(loot.PID_MONEY, w) == "money"
    assert loot.worth(0x28, w) == "medicine"  # Stimpak
    assert loot.worth(0x56, w) == "book"  # Scout Handbook
    assert loot.worth(0x4F, w) == "light"  # Flare


def test_ammunition_counts_when_it_fits_a_gun_carried_or_lying_with_it() -> None:
    w = the_idealist()
    assert loot.worth(AP_10MM, w) == "ammo"  # the cave's Bones: a box for the pistol
    assert loot.worth(SHELLS, w) is None
    assert loot.worth(SHELLS, w, beside=(SHOTGUN, SHELLS)) == "ammo"


def test_a_weapon_counts_only_when_it_would_be_the_best_way_to_fight() -> None:
    w = the_idealist()
    assert loot.worth(KNIFE, w) is None  # carried already
    assert loot.worth(RIFLE, w) is None  # no rounds for it
    assert loot.worth(RIFLE, w, beside=(RIFLE, AMMO_223)) == "weapon"  # 8-20 damage, its ammunition with it


def test_armour_counts_when_it_is_dearer_than_what_is_worn() -> None:
    assert loot.worth(LEATHER, the_idealist()) == "armour"
    assert loot.worth(LEATHER, the_idealist(worn_cost=1100)) is None
    assert loot.worth(METAL, the_idealist(worn_cost=800)) == "armour"


def test_junk_is_left() -> None:
    assert loot.worth(0x5C, the_idealist()) is None  # a Scorpion Tail: 20 lbs, taken by its own step


def test_rounds_in_a_stack_of_boxes() -> None:
    assert loot.rounds_in(JHP_10MM, 2, 12) == 24 + 12
    assert loot.rounds_in(JHP_10MM, 1) == 24
    assert loot.rounds_in(JHP_10MM, 0) == 0


def test_a_knife_takes_no_ammunition() -> None:
    """BB's are caliber 0, as is every melee weapon: only a gun (capacity > 0) makes ammunition worth it."""
    assert loot.worth(0xA3, the_idealist()) is None


def test_ammunition_for_a_gun_the_route_will_pick_up_counts() -> None:
    """.223 FMJ lies in Shady Sands; the Hunting Rifle on Junktown's shelves (the Idealist's PLANNED_CALIBERS)."""
    assert loot.worth(AMMO_223, the_idealist()) is None
    assert loot.worth(AMMO_223, the_idealist(calibers=frozenset({5}))) == "ammo for later"


def test_drop_entry_follows_the_use_flags():
    """The item menu's Drop is second behind Use: drugs, Use items (a Flare) and Use On items (a Water Flask, live);
    first for weapons, ammunition and armour."""
    water, stimpak, flare, combat_armor, plasma_rifle, ammo = 126, 40, 79, 17, 15, 29
    assert [loot.drop_entry(p) for p in (water, stimpak, flare)] == [2, 2, 2]
    assert [loot.drop_entry(p) for p in (combat_armor, plasma_rifle, ammo)] == [1, 1, 1]
    assert loot.drop_entry(plasma_rifle, loaded=10) == 2  # Look, Unload, Drop: a loaded gun's Drop is second


class WeaponMem:
    """A weapon object's loaded rounds (+0x3C) and their ammunition pid (+0x40)."""

    def __init__(self, rounds: int, ammo: int) -> None:
        self.values = {0x3C: rounds, 0x40: ammo}

    def i32(self, address: int) -> int:
        return self.values[address]


def test_a_gun_weighs_with_the_boxes_its_rounds_would_fill() -> None:
    plasma_pistol, cell = 0x18, 0x26  # 4 lb; Small Energy Cells: 3 lb a box of 40
    assert loot.item_weight(None, 0, plasma_pistol) == 4
    assert loot.item_weight(WeaponMem(0, cell), 0, plasma_pistol) == 4
    assert loot.item_weight(WeaponMem(16, cell), 0, plasma_pistol) == 7  # item.c item_weight: (16 - 1) // 40 + 1
    assert loot.item_weight(WeaponMem(41, cell), 0, plasma_pistol) == 10


def test_the_carry_limit_as_stat_level_counts_it() -> None:
    base, bonus = [6] + [0] * 11 + [175], [0] * 13
    assert loot.limit_of(base, bonus, set()) == 175  # 25 + 25 x ST 6
    assert loot.limit_of(base, bonus[:12] + [75], set()) == 250  # a bonus (the Powered Armor's ST +3)
    assert loot.limit_of(base, bonus, {2}) == 115  # Small Frame: 10 x base ST off
    assert loot.limit_of(base, bonus[:12] + [-500], set()) == 0  # stat.c's range


class Pack:
    """A player carrying ITEMS (address, pid, count), the prototypes' weights (no memory)."""

    mem = None

    def __init__(self, items: list[tuple[int, int, int]]) -> None:
        self._items = items
        self.log = self

    def items(self) -> list[tuple[int, int, int]]:
        return self._items

    def emit(self, *_a, **_k) -> None:
        pass


def test_an_item_fits_up_to_the_limit_itself(monkeypatch) -> None:
    monkeypatch.setattr(loot, "carried_weight", lambda a: 170)
    monkeypatch.setattr(loot, "carry_limit", lambda a: 175)
    assert loot.check(None, 5)["fits"]  # item.c item_add_mult refuses only load + weight > limit
    assert not loot.check(None, 6)["fits"] and loot.check(None, 6)["need"] == 1


def test_room_is_made_the_least_worth_a_pound_first(monkeypatch) -> None:
    pack = Pack([(1, RIFLE, 1), (2, LEATHER, 1), (3, SHELLS, 3)])
    w = {p: knowledge.proto(p)["weight"] for p in (RIFLE, LEATHER, SHELLS)}
    c = {p: knowledge.proto(p)["cost"] for p in (RIFLE, LEATHER, SHELLS)}
    order = sorted((RIFLE, LEATHER, SHELLS), key=lambda p: c[p] / w[p])
    monkeypatch.setattr(loot, "droppable", lambda a, coming=0, junk=False: [(w[RIFLE], RIFLE, 1),
                        (w[LEATHER], LEATHER, 1), (3 * w[SHELLS], SHELLS, 3)])  # fmt: skip
    assert loot.room_plan(pack, 0) == []
    assert loot.room_plan(pack, 1)[0][0] == order[0]
    everything = w[RIFLE] + w[LEATHER] + 3 * w[SHELLS]
    assert sorted(loot.room_plan(pack, everything)) == sorted([(RIFLE, 1), (LEATHER, 1), (SHELLS, 3)])
    assert loot.room_plan(pack, everything + 1) is None  # cannot be freed
    assert loot.room_plan(pack, 1, worth=1) is None  # what would go is worth more than the find


def test_a_find_that_cannot_fit_is_refused_before_any_step(monkeypatch) -> None:
    monkeypatch.setattr(loot, "carried_weight", lambda a: 170)
    monkeypatch.setattr(loot, "carry_limit", lambda a: 175)
    monkeypatch.setattr(loot, "droppable", lambda a, coming=0, junk=False: [])
    f = loot.Find(1, 17488, "body", "Bones", ((KNIFE, 1), (AP_10MM, 1)), ("ammo",), 6, 100)
    steps = []
    monkeypatch.setattr(loot.nav, "go_to", lambda *a, **k: steps.append(a) or (True, ""))
    assert loot.take(Pack([]), f) == loot.TOO_HEAVY and not steps
    assert not loot.fits_or_room(Pack([]), f)


class FloorMem:
    """A loose item's object: no inventory, no loaded rounds."""

    def i32(self, address: int) -> int:
        return 0

    def u32(self, address: int) -> int:
        return 0


def test_a_direct_take_is_weighed_too(monkeypatch) -> None:
    monkeypatch.setattr(loot, "carried_weight", lambda a: 175)
    monkeypatch.setattr(loot, "carry_limit", lambda a: 175)
    monkeypatch.setattr(loot, "droppable", lambda a, coming=0, junk=False: [])
    pack = Pack([])
    pack.mem = FloorMem()
    assert not loot.weigh_object(pack, 0x1000, KNIFE)  # 1 lb past a full pack, nothing can go
    monkeypatch.setattr(loot, "carried_weight", lambda a: 174)
    assert loot.weigh_object(pack, 0x1000, KNIFE)
