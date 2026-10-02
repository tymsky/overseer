"""Instance helpers that do not need the game: ini editing, byte diffs, what gets copied."""

import pytest

from f1.instance import HRP_SETTINGS, InstanceError, byte_diff, is_copied, set_ini_values, size_settings, win_data

INI = (
    "; comment\r\n[MAIN]\r\nUAC_AWARE=1\r\nGRAPHICS_MODE=2\r\nSCALE_2X=0\r\nSCR_WIDTH=1024\r\nSCR_HEIGHT=768\r\n"
    "COLOUR_BITS=32\t\t\r\n; Set WINDOWED=1 to enable windowed mode.\r\nWINDOWED=0\r\nWINDOWED_FULLSCREEN=0\r\n"
    "WIN_DATA=2C00B8\t\r\n[INPUT]\r\nALT_MOUSE_INPUT=0\r\nEXTRA_WIN_MSG_CHECKS=1\r\n[MAPS]\r\n"
    "SCROLL_DIST_X=HALF_SCRN ; ORIGINAL 480\r\nIGNORE_PLAYER_SCROLL_LIMITS=0\r\n[IFACE]\r\nIFACE_BAR_MODE=1\r\n"
)


def test_ini_values_change_and_nothing_else() -> None:
    out = set_ini_values(INI, HRP_SETTINGS + size_settings((1920, 1080)))
    assert "UAC_AWARE=0\r\n" in out
    assert "WINDOWED=1\r\n" in out
    assert "SCR_WIDTH=1920\r\n" in out and "SCR_HEIGHT=1080\r\n" in out
    assert "WIN_DATA=" + win_data((1920, 1080)) + "\t\r\n" in out  # the window for that client
    assert "IFACE_BAR_MODE=0\r\n" in out and "IGNORE_PLAYER_SCROLL_LIMITS=1\r\n" in out
    assert "ALT_MOUSE_INPUT=1\r\n" in out
    assert "; Set WINDOWED=1 to enable windowed mode.\r\n" in out  # comments untouched
    assert "COLOUR_BITS=32\t\t\r\n" in out
    assert "SCROLL_DIST_X=HALF_SCRN ; ORIGINAL 480\r\n" in out
    assert out.count("\r\n") == INI.count("\r\n")


def test_ini_trailing_comment_is_kept() -> None:
    out = set_ini_values("[MAPS]\nSCROLL_DIST_X=HALF_SCRN ; ORIGINAL 480\n", (("MAPS", "SCROLL_DIST_X", "480"),))
    assert out == "[MAPS]\nSCROLL_DIST_X=480 ; ORIGINAL 480\n"


def test_ini_missing_key_is_an_error() -> None:
    with pytest.raises(InstanceError):
        set_ini_values("[MAIN]\nA=1\n", (("MAIN", "B", "2"),))


def test_byte_diff() -> None:
    assert byte_diff(b"abcdef", b"abXXef") == [(2, 2)]
    assert byte_diff(b"abc", b"abc") == []
    assert byte_diff(b"abc", b"abcde") == [(3, 2)]


def test_owner_saves_and_the_1_2_engine_are_not_copied() -> None:
    assert is_copied("FALLOUTW.EXE")
    assert is_copied("DATA/MAPS/V13ENT.MAP")
    assert not is_copied("DATA/SAVEGAME/SLOT01/SAVE.DAT")
    assert not is_copied("DATA/SAVEGAME/steam_autocloud.vdf")
    assert not is_copied("falloutwHR.exe")
    assert not is_copied("Manual/manual.pdf")


def test_win_data_reproduces_hrps_own_for_640x480() -> None:
    measured = "2C0000000000000001000000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000000000009002000007020000B8"
    assert win_data((640, 480)) == measured
