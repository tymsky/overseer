"""World-map travel time by the exe's tables and CalcTimeAdder (needs the instance's FALLOUTW.EXE)."""

import pytest

from f1 import paths, travel, worldmap

EXE = paths.INSTANCE / "FALLOUTW.EXE"
needs_exe = pytest.mark.skipif(not EXE.exists(), reason="instance not built")


def test_outdoorsman_and_pathfinder_set_the_steps_of_a_day() -> None:
    assert travel.rates(2) == (61, 14163)  # the Agent
    assert travel.rates(33) == (79, 10936)  # the Idealist at the start
    assert travel.rates(100) == travel.rates(150) == (120, 7200)  # the skill counts up to 100
    assert travel.rates(100, 2) == (120, 3600)


@needs_exe
def test_the_tables_read_from_the_exe_have_the_towns_on_land() -> None:
    terra, classes = travel.tables()
    assert len(terra) == len(classes) == 30 and all(len(row) == 28 for row in terra + classes)
    for column, row in worldmap.TOWNS.values():
        assert terra[row][column] != 3  # not the ocean
    assert terra[1][16:21] == (1, 1, 1, 0, 0)  # Vault 13 sits in the mountains, Shady Sands in the desert


@needs_exe
def test_vault_13_to_shady_sands_as_measured() -> None:
    """125 mountain pixels (2 units each) and 125 desert ones: 375 units. Live, the Idealist on Easy (Outdoorsman 53)
    took 4.121 days once the first click stayed on the line."""
    easy, _ = travel.town_leg("vault 13", "shady sands", 53)
    assert round(easy, 3) == 4.121
    agent, _ = travel.town_leg("vault 13", "shady sands", 2)
    assert round(agent, 2) == 6.15
    fastest, _ = travel.town_leg("vault 13", "shady sands", 100, 2)
    assert fastest == pytest.approx(agent * 61 / 240, rel=0.02)
