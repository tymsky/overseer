"""The hex math agrees with the engine's own tables (off_tile) and with itself."""

import pytest

from f1.geometry import OFF_TILE, Camera, distance, in_direction, neighbour, neighbours, ring, tile_coord

CAM = Camera(offx=304, offy=182, tile_x=100, tile_y=100)  # camera centred mid-map, as tile_set_center() would


@pytest.mark.parametrize("tile", [20100, 20101, 19902, 20299, 15050, 15051])
@pytest.mark.parametrize("rotation", range(6))
def test_neighbour_offsets_match_the_engines_off_tile_table(tile: int, rotation: int) -> None:
    x0, y0 = tile_coord(tile, CAM)
    x1, y1 = tile_coord(neighbour(tile, rotation), CAM)
    assert (x1 - x0, y1 - y0) == (OFF_TILE[0][rotation], OFF_TILE[1][rotation])


def test_opposite_steps_cancel() -> None:
    for tile in (20100, 20101):
        for r in range(6):
            assert neighbour(neighbour(tile, r), (r + 3) % 6) == tile


def test_distance_matches_steps() -> None:
    for tile in (20100, 20101, 15050, 15051):
        assert distance(tile, tile) == 0
        for r in range(6):
            assert distance(tile, neighbour(tile, r)) == 1
        for radius in (2, 3, 5):
            assert all(distance(tile, t) == radius for t in ring(tile, radius))


def test_neighbours_stay_on_the_grid() -> None:
    assert len(neighbours(20100)) == 6
    assert all(0 <= n < 40000 for n in neighbours(0))
    assert all(abs(n % 200 - 199) <= 1 for n in neighbours(199))


def test_ring_has_six_times_radius_distinct_tiles() -> None:
    for radius in (1, 2, 3):
        tiles = ring(20100, radius)
        assert len(tiles) == len(set(tiles)) == 6 * radius
    assert in_direction(20100, 1, 3) in ring(20100, 3)


def test_astar_without_goals_finds_no_path() -> None:
    from f1 import nav

    assert nav.astar(100, set(), frozenset()) is None
