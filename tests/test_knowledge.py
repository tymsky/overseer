"""Parsers of the game's file formats, on small inputs, and against the installed game when present."""

import pytest
from conftest import needs_knowledge

from f1 import paths
from f1.dat import GameFiles, lzss_decode
from f1.knowledge import parse_gvars, parse_msg

INSTANCE = paths.INSTANCE


def test_lzss_literals_and_a_back_reference() -> None:
    # flags 0b11111110: first item a reference into the space-filled ring, then 7 literals
    assert lzss_decode(bytes([0x03, ord("A"), ord("B")])) == b"AB"
    # reference to ring offset 4078 (0xFEE) length 3 right after writing "AB": copies "AB" and the first copied "A"
    data = bytes([0b00000011, ord("A"), ord("B"), 0xEE, 0xF0])
    assert lzss_decode(data) == b"ABABA"


def test_msg_entries_and_multiline_text() -> None:
    msgs = parse_msg(b"{100}{}{Knife}\r\n{101}{}{A sharp\r\nknife.}\r\n{200}{snd}{Club}\r\n")
    assert msgs == {100: "Knife", 101: "A sharp\nknife.", 200: "Club"}


def test_gvars_count_lines_without_semicolon() -> None:
    text = b"GAME_GLOBAL_VARS:\r\n// c\r\nA :=0; // (0)\r\nB :=1 // (1)\r\nC := -5;\r\n"
    assert [(g["name"], g["initial"]) for g in parse_gvars(text)] == [("A", 0), ("B", 1), ("C", -5)]


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
def test_real_files_read() -> None:
    files = GameFiles(INSTANCE)
    msgs = parse_msg(files.read("TEXT/ENGLISH/GAME/PRO_ITEM.MSG"))
    assert msgs[100] == "Leather Armor"
    assert len(parse_gvars(files.read("DATA/VAULT13.GAM"))) == 618  # as many as the engine holds in memory


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
def test_map_indexes_name_the_maps_exit_grids_lead_to() -> None:
    msgs = parse_msg(GameFiles(INSTANCE).read("TEXT/ENGLISH/GAME/MAP.MSG"))
    assert (msgs[9], msgs[16], msgs[35]) == ("VAULTNEC.MAP", "CAVES.MAP", "V13ENT.MAP")  # as worldmap.c lists them
    assert msgs[109] == "Necropolis"


@needs_knowledge
def test_gvars_by_name() -> None:
    """Numbers measured live: RESCUE_TANDI 103, TANDI_STATUS 26; past the ;-less BAD_MONSTER (160) the count holds."""
    from f1.knowledge import gvar_index

    assert (gvar_index("TANDI_STATUS"), gvar_index("RESCUE_TANDI"), gvar_index("GIZMO_STATUS")) == (26, 103, 170)


@needs_knowledge
def test_script_names_by_index() -> None:
    """SCRIPTS.LST lines, as the engine indexes them (live: Killian's critter runs index 46, Kenji's 509)."""
    from f1.knowledge import load

    names = load()["scripts"]
    assert (names[46], names[509], names[336]) == ("killian", "kenji", "jtraider")
