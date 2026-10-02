"""Who notices the player where, by fallout1-ce's rules: the directions, the front half, the reaches."""

from dataclasses import dataclass

import pytest

from f1 import geometry, perception

CENTRE = 20100  # a tile in the middle of the grid


@pytest.mark.parametrize("rotation", range(6))
@pytest.mark.parametrize("distance", [1, 2, 7])
def test_the_direction_to_a_tile_straight_along_a_facing_is_that_facing(rotation: int, distance: int) -> None:
    assert perception.tile_dir(CENTRE, geometry.in_direction(CENTRE, rotation, distance)) == rotation


def test_the_front_half_is_the_facing_and_its_two_neighbours() -> None:
    assert [perception.sees(0, d) for d in range(6)] == [True, True, False, False, False, True]
    assert [perception.sees(3, d) for d in range(6)] == [False, False, True, True, True, False]


def test_a_radscorpions_reach() -> None:
    assert perception.reach(2, seen=True) == 10
    assert perception.reach(2, seen=False) == 2
    assert perception.reach(2, seen=False, in_combat=True) == 4
    assert perception.reach(2, seen=True, sneaking=True) == 2


def test_ahead_it_notices_far_behind_only_close() -> None:
    ahead = geometry.in_direction(CENTRE, 1, 9)  # facing east, 9 hexes east
    behind = geometry.in_direction(CENTRE, 4, 3)  # 3 hexes west
    assert perception.notices(CENTRE, 1, 2, ahead)
    assert not perception.notices(CENTRE, 1, 2, behind)
    assert perception.notices(CENTRE, 1, 2, geometry.in_direction(CENTRE, 4, 2))
    assert not perception.notices(CENTRE, 1, 2, ahead, sneaking=True)


@dataclass
class Foe:
    tile: int
    rotation: int
    pid: int


def test_a_zone_covers_the_front_half_to_pe_x_5_and_a_small_circle_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perception, "perception_of", lambda pid: 2)
    z = perception.zone([Foe(CENTRE, 1, 0)])
    assert geometry.in_direction(CENTRE, 1, 10) in z and geometry.in_direction(CENTRE, 1, 11) not in z
    assert geometry.in_direction(CENTRE, 4, 2) in z and geometry.in_direction(CENTRE, 4, 3) not in z
    assert CENTRE in z
