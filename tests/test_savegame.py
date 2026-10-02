"""The savegame reader on a synthetic SAVE.DAT laid out like the measured ones."""

import struct

import pytest

from f1 import savegame


def fake_save(values: list[int], name: bytes = b"route 90") -> bytes:
    header = bytearray(savegame.HEADER)
    header[:17] = b"FALLOUT SAVE FILE"
    header[0x3D : 0x3D + len(name)] = name
    block = struct.pack(f">{savegame.COUNT}i", *values)
    return bytes(header) + b"\x07" * 55 + block + b"\x00" * 300 + block


def test_reads_the_first_block_by_its_anchor() -> None:
    values = list(range(savegame.COUNT))
    values[148:150] = [90, 110]
    values[155] = 30  # PLAYER_REPUATION
    data = fake_save(values)
    assert savegame.gvars(data) == values
    assert savegame.description(data) == "route 90"


def test_a_save_without_the_invasion_dates_is_refused() -> None:
    with pytest.raises(ValueError):
        savegame.gvars(fake_save([0] * savegame.COUNT))
