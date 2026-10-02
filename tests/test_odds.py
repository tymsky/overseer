"""A fight's odds by the 1.1 formulas (fallout1-ce combat.c), offline: to-hit, light, range, the Monte Carlo."""

import pytest

from f1 import odds


def test_poor_light_costs_the_players_shot_40_25_or_10() -> None:
    assert [odds.light_penalty(v) for v in (0, 26214, 26215, 39321, 39322, 52428, 52429, 65536)] == [
        -40, -40, -25, -25, -10, -10, 0, 0,
    ]  # fmt: skip


def test_a_gun_gains_close_in_and_loses_far_off() -> None:
    assert odds.gun_range(5, 6) == 28  # 7 hexes inside 2 x PE
    assert odds.gun_range(0, 6) == 48  # at most 8 x PE
    assert odds.gun_range(12, 6) == 0
    assert odds.gun_range(20, 6) == -32


def test_the_idealists_shot_at_a_radscorpion_in_the_dark_and_in_the_light() -> None:
    """Small Guns 55, the 10mm pistol at 5 hexes, PE 6; the radscorpion's AC 15, a big body (+15)."""
    dark = odds.to_hit(55, 15, ranged=True, dist=5, pe=6, big=True, light=20000)
    lit = odds.to_hit(55, 15, ranged=True, dist=5, pe=6, big=True, light=65536)
    assert (dark, lit) == (55 - 15 + 15 + 28 - 40, 83)


def test_a_foes_skill_and_its_hit_on_wimpy() -> None:
    radscorpion = {"special": [7, 2, 6, 1, 1, 5, 2], "skill_points": [0, 0, 0, 19] + [0] * 14}
    unarmed = odds.foe_skill(radscorpion, 3)
    assert unarmed == 65 + (5 + 7) // 2 + 19
    assert odds.to_hit(unarmed, 8, player=False, difficulty=0) == 90 - 8 - 20
    assert odds.to_hit(200, 0, player=False, difficulty=2) == 95  # at most 95


def radscorpion(hit: int = 62) -> odds.Attacker:
    return odds.Attacker(hp=26, ap=7, hit=hit, dmg=(1, 8), cost=3, dt=2, dr=0, mult=0.75)


def idealist(hit: int, hp: int = 38, mag: int | None = 12, spare: int = 36) -> odds.Attacker:
    return odds.Attacker(hp=hp, ap=9, hit=hit, dmg=(5, 12), cost=4, dt=0, dr=0, mag=mag, spare=spare, capacity=12)


def test_light_decides_the_radscorpion_fight() -> None:
    dark = odds.fight(idealist(43), [radscorpion()])
    lit = odds.fight(idealist(83), [radscorpion()])
    assert lit.win > 0.95 and lit.win > dark.win
    assert lit.turns < dark.turns and lit.rounds < dark.rounds


def test_hurt_and_in_the_dark_against_two_is_a_losing_fight() -> None:
    assert odds.fight(idealist(43, hp=17), [radscorpion(), radscorpion()]).win < 0.5


def test_no_rounds_no_fight() -> None:
    assert odds.fight(idealist(83, mag=0, spare=0), [radscorpion()]).win == 0.0


@pytest.mark.parametrize("seed", [1, 2])
def test_the_same_seed_gives_the_same_odds(seed: int) -> None:
    a = odds.fight(idealist(60), [radscorpion()], seed=seed)
    b = odds.fight(idealist(60), [radscorpion()], seed=seed)
    assert a == b
