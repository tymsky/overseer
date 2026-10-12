"""f1.traps, the walk's leg rule (f1.nav lens, clear_leg) and f1.explosives offline."""

import struct
from fractions import Fraction

import pytest

from f1 import engine_map as em
from f1 import explosives, geometry, knowledge, nav, traps

SCRIPTS = em.GLOBALS["scriptlists"].address
EXTENT, LOCALS = 0x01000000, 0x02000000


class FakeMem:
    def __init__(self, blocks: dict[int, bytearray]) -> None:
        self.blocks = blocks

    def read(self, address: int, size: int) -> bytes:
        for base, data in self.blocks.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base : address - base + size])
        raise OSError(f"unmapped {address:#x}")

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]


def spatial_list(records: list[tuple[int, int, int, int, int, int]], local0: list[int]) -> FakeMem:
    """A spatial list of one extent: (sid, tile, elevation, radius, script index, local offset) records."""
    lists = bytearray(16 * 5)
    struct.pack_into("<I", lists, 16 * traps.SPATIAL, EXTENT)
    extent = bytearray(traps.SCRIPT_SIZE * 16 + 8)
    for k, (sid, tile, elevation, radius, index, offset) in enumerate(records):
        base = traps.SCRIPT_SIZE * k
        struct.pack_into("<4i", extent, base, sid, 0, tile | elevation << 29, radius)
        struct.pack_into("<i", extent, base + traps.INDEX, index)
        struct.pack_into("<2i", extent, base + traps.LVAR_OFFSET, offset, 1 if offset >= 0 else 0)
    struct.pack_into("<iI", extent, traps.SCRIPT_SIZE * 16, len(records), 0)
    return FakeMem({
        SCRIPTS: lists,
        EXTENT: extent,
        traps.MAP_LOCAL_VARS: bytearray(struct.pack("<I", LOCALS)),
        LOCALS: bytearray(struct.pack(f"<{len(local0)}i", *local0)),
    })  # fmt: skip


def test_the_list_read_and_what_is_armed(monkeypatch) -> None:
    names = ["ratpit", "trapflor", "hotspot"]
    monkeypatch.setattr(traps, "_name", lambda i: names[i])
    mem = spatial_list(
        [(0x01000000, 21080, 1, 2, 1, 0), (0x01000001, 23680, 1, 1, 1, 1), (0x01000002, 500, 0, 3, 0, -1),
         (0x01000003, 600, 1, 2, 2, -1)],
        [0, 1],
    )  # fmt: skip
    found = traps.spatial(mem)
    assert [(s.tile, s.elevation, s.radius, s.name, s.local0) for s in found] == [
        (21080, 1, 2, "trapflor", 0), (23680, 1, 1, "trapflor", 1), (500, 0, 3, "ratpit", 0),
        (600, 1, 2, "hotspot", 0),
    ]  # fmt: skip
    armed = traps.armed(mem, 1)
    assert [s.tile for s in armed] == [21080, 600]  # the second trapflor fired or was disarmed; ratpit is no trap
    tiles = traps.avoid(armed)
    assert len(tiles) == 2 * 19 and 21080 in tiles and geometry.in_direction(21080, 1, 2) in tiles
    assert geometry.in_direction(21080, 1, 3) not in tiles


def test_disk_sizes() -> None:
    assert [len(traps.disk(20100, r)) for r in range(4)] == [1, 7, 19, 37]
    assert traps.step_odds(23) == Fraction(23, 100) and traps.step_odds(140) == 1


@pytest.mark.skipif(not knowledge.DB_PATH.exists(), reason="no knowledge base")
def test_the_hubs_old_town_traps_from_its_map() -> None:
    found = traps.from_map("HUBOLDTN")
    assert [(s.tile, s.elevation, s.radius, s.name) for s in found] == [
        (21080, 1, 2, "trapflor"), (23680, 1, 1, "trapflor"),
    ]  # fmt: skip
    assert all(s.armed for s in found)


def test_lens_is_every_shortest_way() -> None:
    a = 20100
    b = geometry.in_direction(a, 1, 4)
    assert nav.lens(a, b) == {geometry.in_direction(a, 1, k) for k in range(5)}  # a straight row: one way
    c = geometry.in_direction(geometry.in_direction(a, 1, 2), 2, 2)
    assert len(nav.lens(a, c)) == 9  # a 2 x 2 parallelogram of ways
    for t in nav.lens(a, c):
        assert geometry.distance(a, t) + geometry.distance(t, c) == 4


def test_a_leg_stops_short_of_a_lens_that_crosses_the_area() -> None:
    a = 20100
    path = [a] + [geometry.in_direction(a, 1, k) for k in range(1, 9)]
    trap = traps.disk(geometry.in_direction(geometry.in_direction(a, 1, 6), 0, 2), 1)
    assert not set(path) & trap
    i = nav.clear_leg(path, 8, frozenset(trap))
    assert i == 8  # a straight row has one way only
    bent = [a, geometry.in_direction(a, 0, 1), geometry.in_direction(geometry.in_direction(a, 0, 1), 1, 1)]
    blocker = frozenset({geometry.in_direction(a, 1, 1)})
    assert nav.clear_leg(bent, 2, blocker) == 1  # the engine could cut through the other way round


def test_explosives() -> None:
    assert explosives.presses(60) == ("plus", 0) and explosives.presses(10) == ("minus", 5)
    with pytest.raises(ValueError):
        explosives.presses(65)
    full, half, at_once = explosives.arming_odds(30)
    assert full == Fraction(30, 100) and full + half + at_once == 1
    assert explosives.arming_odds(30, criticals=False)[2] == 0
    e = explosives.Event(1000 + 300, explosives.EXPLOSION_FAILURE, 1, 0)
    assert explosives.outcome(e, 1000, 60) == "failure"
    assert explosives.in_blast(3) and not explosives.in_blast(4)
