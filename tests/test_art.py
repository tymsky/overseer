"""Pictures placed as the engine draws them: the Watershed ladder behind its Cave Wall shows only along its right rail.
Reads the instance's DATs (skipped where the instance is not built)."""

import struct

import pytest

from f1 import art, knowledge

pytestmark = pytest.mark.skipif(not (knowledge.INSTANCE / "MASTER.DAT").exists(), reason="no instance")

LADDER, CAVE_WALL = 0x200008B, 0x30000B0  # WATRSHD tile 23858, elevation 0 (map file)


def proto_fid(pid: int) -> int:
    folder = art.TYPES[pid >> 24]
    listing = art._files().read(f"PROTO/{folder}/{folder}.LST").decode("latin1").split()
    return struct.unpack(">i", art._files().read(f"PROTO/{folder}/{listing[(pid & 0xFFFFFF) - 1]}")[8:12])[0]


def placed(pid: int) -> art.Placed:
    p = art.picture(proto_fid(pid))
    assert p is not None
    return art.Placed(p, *art.corner(p))


def test_the_ladder_shows_only_right_of_the_wall() -> None:
    ladder, wall = placed(LADDER), placed(CAVE_WALL)
    assert (ladder.picture.width, ladder.picture.height) == (27, 134)
    points = art.visible_points(ladder, [wall])
    assert points and all(16 <= x <= 22 for x, _ in points)  # the rail's strip
    assert all(ladder.opaque(x, y) and not wall.opaque(x, y) for x, y in points)
    # why the aimed pass ran out: actions.AIM_GRID touches the ladder at two rungs only, 60 points in (after the 33
    # SCENERY_AIMS), and at 0.5 s a point its 40 s were gone first
    grid = [(dx, dy) for dy in range(-64, 17, 8) for dx in range(-40, 41, 8)]
    hits = [i for i, (x, y) in enumerate(grid) if ladder.opaque(x, y) and not wall.opaque(x, y)]
    assert len(hits) == 2 and 33 + hits[0] > 40 / 0.5


def test_critters_are_not_placed() -> None:
    assert art.picture(0x01000000) is None
