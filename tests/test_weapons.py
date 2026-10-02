"""Weapon rules from the prototypes (item.c) and the choice of weapon. Needs extracted/f1/knowledge.json."""

from conftest import needs_knowledge

from f1 import weapons
from f1.weapons import Carried, Rules

pytestmark = needs_knowledge

PISTOL, SMG, RIFLE, MINIGUN, LASER_PISTOL, KNIFE, SPEAR, ROCK = 0x08, 0x09, 0x0A, 0x0C, 0x10, 0x04, 0x07, 0x13
JHP, AP_10MM, FMJ_223 = 0x1D, 0x1E, 0x22
AGENT = {"small guns": 101, "big guns": 20, "energy weapons": 20, "unarmed": 70, "melee weapons": 60, "throwing": 50}


def test_skill_by_mode_damage_and_flags() -> None:
    assert weapons.skill(PISTOL) == weapons.skill(RIFLE) == "small guns"
    assert weapons.skill(MINIGUN) == "big guns"  # flags_ext 0x100
    assert weapons.skill(LASER_PISTOL) == "energy weapons"  # laser damage
    assert weapons.skill(KNIFE) == "melee weapons" and weapons.skill(None) == "unarmed"
    assert weapons.attack_type(SPEAR) == "melee" and weapons.attack_type(SPEAR, secondary=True) == "throw"


def test_ap_cost_follows_traits_and_perks() -> None:
    assert weapons.ap_cost(PISTOL, Rules()) == 5  # the interface bar showed "AP 5" live
    assert weapons.ap_cost(PISTOL, Rules(bonus_rate_of_fire=True)) == 4
    assert weapons.ap_cost(PISTOL, Rules(fast_shot=True)) == 4
    assert weapons.ap_cost(PISTOL, Rules(), aimed=True) == 6
    assert weapons.ap_cost(KNIFE, Rules(bonus_rate_of_fire=True)) == 3  # the perk is for guns only
    assert weapons.ap_cost(KNIFE, Rules(bonus_hth_attacks=True)) == 2
    assert weapons.ap_cost(None, Rules(fast_shot=True)) == 3  # Fast Shot needs a weapon


def test_reach() -> None:
    assert weapons.reach(PISTOL, Rules()) == 25 and weapons.reach(RIFLE, Rules()) == 40
    assert weapons.reach(KNIFE, Rules()) == 1 and weapons.reach(None, Rules()) == 1
    assert weapons.reach(SPEAR, Rules(strength=2), secondary=True) == 6  # thrown: 3 x ST, below the proto's 8


def test_ammunition_that_fits() -> None:
    assert weapons.fits(PISTOL, JHP) and weapons.fits(PISTOL, AP_10MM)
    assert not weapons.fits(PISTOL, FMJ_223) and weapons.fits(RIFLE, FMJ_223)
    assert not weapons.fits(PISTOL, AP_10MM, loaded=5, loaded_pid=JHP)  # not empty: only the same kind
    assert weapons.uses_ammo(PISTOL) and not weapons.uses_ammo(KNIFE) and not weapons.uses_ammo(None)


def test_choice_prefers_a_loaded_stronger_gun() -> None:
    rules = Rules(strength=6)
    both = [Carried(PISTOL, 12, JHP, 48), Carried(RIFLE, 10, FMJ_223, 0)]
    assert [o.name for o in weapons.choose(both, AGENT, rules, 10)][:2] == ["Hunting Rifle", "10mm Pistol"]
    dry_rifle = [Carried(PISTOL, 12, JHP, 48), Carried(RIFLE, 0, FMJ_223, 0)]
    assert [o.name for o in weapons.choose(dry_rifle, AGENT, rules, 10)] == ["10mm Pistol", "fists"]
    assert [o.name for o in weapons.choose([Carried(ROCK)], AGENT, rules, 10)] == ["fists"]  # thrown: left out


def test_short_strength_lowers_the_guess() -> None:
    weak, strong = Rules(strength=3), Rules(strength=6)
    rifle = [Carried(RIFLE, 10, FMJ_223, 0)]
    assert weapons.choose(rifle, AGENT, weak, 10)[0].hit < weapons.choose(rifle, AGENT, strong, 10)[0].hit
