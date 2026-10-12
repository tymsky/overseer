"""The hit test, the screen-to-tile walk and the line of fire (f1/hit.py, f1/geometry.py, f1/lof.py) on made-up maps:
pictures stand in for the game's art, so nothing here needs the game's files."""

import random

import pytest

from f1 import art, geometry, hit, lof
from f1.geometry import Camera, tile_coord, tile_num
from f1.scene import CRITTER, DEAD, SCENERY, WALL, Obj, Snapshot

CAM = Camera(offx=304, offy=182, tile_x=100, tile_y=100, view_width=1920, view_height=981)


def test_tile_num_finds_every_pixel_of_a_hex_centre_and_is_the_inverse_of_tile_coord() -> None:
    rng = random.Random(3)
    for cam in (CAM, Camera(944, 482, 140, 88, 1920, 981), Camera(-17, 33, 60, 120)):
        for _ in range(400):
            t = rng.randrange(40, 199) * 200 + rng.randrange(40, 160)
            x, y = tile_coord(t, cam)
            assert tile_num(x + 16, y + 8, cam) == t
            # the hex's inner diamond: a few pixels round the centre all belong to it
            for dx, dy in ((-8, 0), (8, 0), (0, -4), (0, 4)):
                assert tile_num(x + 16 + dx, y + 8 + dy, cam) == t


def test_tile_num_covers_the_view_without_gaps() -> None:
    """Every pixel of a strip belongs to some hex, and each hex it names has its centre nearby (no stray tile)."""
    for y in range(60):
        for x in range(200):
            t = tile_num(x, y, CAM)
            cx, cy = tile_coord(t, CAM)
            assert abs(cx + 16 - x) <= 24 and abs(cy + 8 - y) <= 16


def test_off_the_grid_is_minus_one_unless_asked() -> None:
    far = Camera(0, 0, 0, 0)
    assert tile_num(-500, -500, far) == -1
    assert tile_num(-500, -500, far, ignore_bounds=True) != -1


def test_tile_num_beyond_walks_on_along_the_line() -> None:
    a = 100 * 200 + 100
    b = geometry.in_direction(a, 1, 3)  # three hexes east
    assert lof.tile_num_beyond(a, b, 3, CAM) == b
    assert lof.tile_num_beyond(a, b, 6, CAM) == geometry.in_direction(a, 1, 6)


# ---- a made-up map: block pictures for the art ----

SOLID = {}  # fid -> (width, height, shift_y)


def block(width: int, height: int) -> art.Art:
    f = art.Frame(width, height, 0, 0, bytes([1]) * (width * height))
    return art.Art(10, 1, (0,) * 6, (0,) * 6, tuple((f,) for _ in range(6)))


@pytest.fixture(autouse=True)
def _fake_art(monkeypatch):
    shapes = {0x1000001: block(20, 40), 0x2000001: block(60, 80), 0x3000001: block(32, 60), 0x6000002: block(100, 80)}
    monkeypatch.setattr(art, "load", lambda fid: shapes.get(fid & 0x0F00FFFF))
    monkeypatch.setattr(hit, "extended_flags", lambda pid: 0)
    hit.placed.cache_clear()


def obj(address: int, tile: int, fid: int, flags: int = 0, results: int = 0, order: int = 0) -> Obj:
    return Obj(address, address, tile, 0, 0, 0, 0, 0, 0, fid, flags, 0, results, fid & 0x0F000000 | 1, order)


def snap(objs: list[Obj], dude: Obj | None = None, egg: Obj | None = None) -> Snapshot:
    by_tile: dict[int, list[Obj]] = {}
    for o in objs:
        by_tile.setdefault(o.tile, []).append(o)
    window = frozenset(r * 200 + c for r in range(-40, 80) for c in range(-40, 80))  # wide: any test point's tiles
    return Snapshot(CAM, 0, CAM.tile_y * 200 + 199 - CAM.tile_x, dude, egg, by_tile, 200, (window, window))


def centre(tile: int) -> tuple[int, int]:
    x, y = tile_coord(tile, CAM)
    return x + 16, y + 8


def test_the_last_candidate_in_tile_order_wins() -> None:
    t = 100 * 200 + 100
    behind, front = obj(1, t, 0x2000001), obj(2, t + 200, 0x1000001)  # a row down: drawn later, in front
    s = snap([behind, front])
    x, y = centre(front.tile)
    assert hit.under(x, y - 10, s).address == front.address
    # a pixel of the scenery only
    bx, by = centre(behind.tile)
    assert hit.under(bx - 25, by - 70, s).address == behind.address


def test_a_dead_critter_lets_the_one_behind_be_hit_but_is_hit_alone() -> None:
    t = 100 * 200 + 100
    behind, body = obj(1, t, 0x2000001), obj(2, t + 200, 0x1000001, results=DEAD)
    s = snap([behind, body])
    x, y = centre(body.tile)
    assert hit.under(x, y - 20, s).address == behind.address
    alone = snap([body])
    assert hit.under(x, y - 20, alone).address == body.address


def test_the_crosshair_sees_only_critters_and_not_the_player() -> None:
    t = 100 * 200 + 100
    dude, wall = obj(1, t, 0x1000001), obj(2, t + 200, 0x3000001)
    s = snap([dude, wall], dude=dude)
    x, y = centre(dude.tile)
    assert hit.under(x, y - 5, s, CRITTER, with_dude=False) is None
    assert hit.under(x, y - 5, s, CRITTER, with_dude=True).address == dude.address


def test_the_egg_shows_the_player_through_a_wall_to_his_right() -> None:
    dude_tile = 100 * 200 + 100
    dude = obj(1, dude_tile, 0x1000001)
    wall = obj(2, dude_tile + 200, 0x3000001)  # a row down: to the right of the player by tile_to_right_of
    egg = Obj(3, 3, dude_tile, 0, 0, 0, 0, 0, 0, 0x6000002, 0x01, 0, 0, 0x6000002, 0)
    s = snap([dude, wall], dude=dude, egg=egg)
    x, y = centre(dude.tile)
    assert geometry.to_right_of(dude.tile, wall.tile, CAM)
    assert hit.intersects(wall, x, y - 5, s) & 0x04
    assert hit.under(x, y - 5, s).address == dude.address


def test_aim_finds_the_visible_part() -> None:
    t = 100 * 200 + 100
    back, front = obj(1, t, 0x2000001), obj(2, t + 200, 0x3000001)
    s = snap([back, front])
    a = hit.aim(back, s)
    assert a.point is not None and hit.under(*a.point, s).address == back.address
    assert a.pixels < 60 * 80


def test_aim_keeps_off_the_windows_over_the_view() -> None:
    t = 100 * 200 + 100
    o = obj(1, t, 0x2000001)
    s = snap([o])
    whole = hit.aim(o, s)
    rect = whole.rect
    s.covers = ((rect.left, rect.top, 1920, (rect.bottom - rect.top) // 2),)
    half = hit.aim(o, s)
    assert half.point[1] >= rect.top + (rect.bottom - rect.top) // 2
    assert half.pixels < whole.pixels


# ---- lines of fire ----


def test_a_wall_blocks_a_shot_and_a_critter_is_counted() -> None:
    a = 100 * 200 + 100
    target_tile = geometry.in_direction(a, 1, 6)
    shooter, target = obj(1, a, 0x1000001), obj(2, target_tile, 0x1000001)
    clear = snap([shooter, target])
    assert not lof.shot_blocked(clear, shooter, a, target_tile, target).blocked
    wall = obj(3, geometry.in_direction(a, 1, 3), 0x3000001)
    walled = snap([shooter, target, wall])
    line = lof.shot_blocked(walled, shooter, a, target_tile, target)
    assert line.blocked and line.obstacles[-1].address == wall.address
    other = obj(4, geometry.in_direction(a, 1, 3), 0x1000001)
    crowded = snap([shooter, target, other])
    line = lof.shot_blocked(crowded, shooter, a, target_tile, target)
    assert not line.blocked and line.critters == 1


def test_shoot_through_and_no_block_are_passed() -> None:
    a = 100 * 200 + 100
    target_tile = geometry.in_direction(a, 1, 6)
    shooter, target = obj(1, a, 0x1000001), obj(2, target_tile, 0x1000001)
    glass = obj(3, geometry.in_direction(a, 1, 3), 0x3000001, flags=lof.SHOOT_THRU)
    door = obj(4, geometry.in_direction(a, 1, 2), 0x2000001, flags=0x10)
    assert not lof.shot_blocked(snap([shooter, target, glass, door]), shooter, a, target_tile, target).blocked


def test_a_missed_shot_rolls_for_the_critters_past_the_target_up_to_a_wall() -> None:
    a = 100 * 200 + 100
    target_tile = geometry.in_direction(a, 1, 3)
    shooter, target = obj(1, a, 0x1000001), obj(2, target_tile, 0x1000001)
    past = obj(3, geometry.in_direction(a, 1, 5), 0x1000001)
    wall = obj(4, geometry.in_direction(a, 1, 7), 0x3000001)
    beyond = obj(5, geometry.in_direction(a, 1, 9), 0x1000001)
    s = snap([shooter, target, past, wall, beyond])
    miss = lof.miss_line(s, shooter, target, 20)
    assert [c.address for c in miss.critters] == [past.address]
    assert miss.wall.address == wall.address
    assert lof.in_miss_line(s, shooter, target, 20, {past.address})
    assert not lof.in_miss_line(s, shooter, target, 20, {beyond.address})
    assert not lof.in_miss_line(s, shooter, target, 3, {past.address})  # out of the weapon's reach


def test_kinds_are_the_engines() -> None:
    assert (CRITTER, SCENERY, WALL) == (1, 2, 3)


def test_actor_predict_logs_its_aim() -> None:
    """Actor.predict over a made-up map (a keyword named like EventLog.emit's first parameter once raised)."""
    from types import SimpleNamespace

    from f1.actions import Actor

    t = 100 * 200 + 100
    o = obj(1, t, 0x2000001)
    events = []
    log = SimpleNamespace(emit=lambda kind, **fields: events.append((kind, fields)))
    fake = SimpleNamespace(scene=lambda: snap([o]), log=log)
    point = Actor.predict(fake, o.address)
    assert point is not None and events[0][0] == "aim"
