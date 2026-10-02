"""The memory map holds for the Steam 1.1 exe and not for the 1.2 one (skipped where the game is not installed)."""

import pytest

from f1 import engine_map as em
from f1.verify_exe import steam_exe, verify

STEAM_EXE = steam_exe()
HR_EXE = STEAM_EXE.with_name("falloutwHR.exe") if STEAM_EXE else None


@pytest.mark.skipif(STEAM_EXE is None, reason="Steam install not present")
def test_map_matches_the_steam_1_1_exe() -> None:
    report = verify(STEAM_EXE)
    assert report.sha256 == em.STEAM_EXE_SHA256
    assert report.ok, [c.text for c in report.checks if not c.ok]
    assert len(report.checks) == 1 + len(em.INITIAL_VALUES) + len(em.GLOBALS) + len(em.FUNCTIONS)


@pytest.mark.skipif(HR_EXE is None or not HR_EXE.exists(), reason="Steam install not present")
def test_map_does_not_match_the_1_2_exe() -> None:
    report = verify(HR_EXE)
    assert not report.ok


def test_the_map_tables_are_consistent() -> None:
    code_start, code_end = 0x410000, 0x4F0000  # BEGTEXT of the 1.1 exe
    assert set(em.INITIAL_VALUES) <= set(em.GLOBALS)
    assert all(code_start <= va < code_end for va, _ in em.FUNCTIONS.values())
    assert all(not code_start <= g.address < code_end for g in em.GLOBALS.values())
