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
