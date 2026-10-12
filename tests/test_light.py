"""f1.light and f1.lightmap offline: the engine's formulas on made-up maps."""

import struct

from f1 import geometry, light, lightmap, odds
from f1.lightmap import Thing


class FakeMem:
    def __init__(self, blocks: dict[int, bytes]) -> None:
        self.blocks = blocks

    def read(self, address: int, size: int) -> bytes:
        for base, data in self.blocks.items():
            if base <= address and address + size <= base + len(data):
                return data[address - base : address - base + size]
        raise OSError(f"unmapped {address:#x}")

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]


def test_set_light_level_and_the_shot() -> None:
    assert light.from_level(100) == 65536
    assert light.from_level(50) == 40960
    assert light.from_level(40) == 26214  # the outdoor maps' night: the shot's -40 exactly
    assert light.from_level(1) == 16629
    assert light.from_level(40, night_vision=1) == 26214 + 0x1999
    assert light.penalty(26214) == -40 and light.penalty(26215) == -25
    assert light.penalty(39321) == -25 and light.penalty(52428) == -10 and light.penalty(52429) == 0
    assert odds.light_penalty(26214) == -40  # odds keeps its interface


def test_visible_light_takes_the_ambient_as_a_floor() -> None:
    assert light.visible(26214, 655) == 26214
    assert light.visible(26214, 70000) == 65536
    assert light.visible(16384, 65536 + 655, own=65536) == 16384


def test_reads_from_memory() -> None:
    grid = [655] * (3 * light.TILES)
    grid[light.TILES + 123] = 30000
    mem = FakeMem({
        light.AMBIENT: struct.pack("<i", 26214),
        light.TILE_INTENSITY: struct.pack(f"<{len(grid)}i", *grid),
    })  # fmt: skip
    assert light.ambient(mem) == 26214
    assert light.tile(mem, 1, 123) == 30000 and light.tile(mem, 3, 0) == 0
    assert light.of_object(mem, 1, 123) == 30000 and light.of_object(mem, 0, 123) == 26214
    assert odds.visible_light(mem, 1, 123) == 30000


def test_offsets_are_the_wedges() -> None:
    for parity in (0, 1):
        flat = [d for row in lightmap.OFFSETS[parity] for d in row]
        assert len(flat) == 216 and len(set(flat)) == 216  # every tile within 8 hexes once
        src = 20100 + parity
        dists = sorted(geometry.distance(src, src + d) for d in flat)
        assert dists == sorted(lightmap.DISTANCE * 6)


def test_the_players_light_on_an_empty_floor() -> None:
    src = 20100
    out = lightmap.spread({}, src, 0)
    step = (65536 - 655) // 5
    assert out[src] == 65536
    assert len(out) == 1 + 6 * (1 + 2 + 3 + 4)  # 4 hexes
    for t, v in out.items():
        assert v == 65536 - step * geometry.distance(src, t)


def test_a_blocker_shades_the_tiles_behind_it() -> None:
    src = 20100
    east = geometry.neighbour(src, lightmap.E)
    rock = Thing(1, east, 0, 0, 2, 0x02000001)  # scenery, not light-thru
    out = lightmap.spread({east: [rock]}, src, 0)
    assert east not in out  # E..SW: the blocker's own tile stays dark
    assert geometry.in_direction(src, lightmap.E, 2) not in out
    glass = Thing(2, east, 0, lightmap.LIGHT_THRU, 2, 0x02000001)
    assert lightmap.spread({east: [glass]}, src, 0) == lightmap.spread({}, src, 0)
    hidden = Thing(3, east, 0, lightmap.HIDDEN, 2, 0x02000001)
    assert lightmap.spread({east: [hidden]}, src, 0) == lightmap.spread({}, src, 0)


def test_rebuild_and_the_moved_player() -> None:
    src = 20100
    dude = Thing(10, src, 0, lightmap.LIGHTING, 1, 0x01000000, 4, 65536)
    lamp = Thing(11, src + 10, 0, lightmap.LIGHTING, 2, 0x02000002, 2, 30000)
    by_tile = {src: [dude], src + 10: [lamp]}
    grid = lightmap.rebuild(by_tile, 0, walls=lambda pid: 0)
    assert grid[0] == 655 and grid[src] == 655 + 65536 + lightmap.spread({}, src + 10, 0, 2, 30000).get(src, 0)
    seen = lightmap.PlayerLight(by_tile, dude, grid, 16384, walls=lambda pid: 0)
    far = src - 2000
    assert seen(src, src) == 65536
    assert seen(far, src) == light.visible(16384, grid[src] - 65536)
