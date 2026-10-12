"""f1.saves offline: the save header, the description the box takes, checkpoints kept and installed on plain folders."""

import json
import struct
from pathlib import Path

import pytest

from f1 import paths, savegame, saves, session


def fake_header(description: bytes = b"v13 start", map_file: bytes = b"V13ENT.SAV", elevation: int = 0) -> bytes:
    data = bytearray(30000)
    data[:17] = b"FALLOUT SAVE FILE"
    data[29:33] = b"Name"
    data[61 : 61 + len(description)] = description
    data[101:107] = struct.pack(">3h", 12, 5, 2161)
    data[107:111] = struct.pack(">I", 302400)
    data[111:115] = struct.pack(">2h", elevation, 11)
    data[115 : 115 + len(map_file)] = map_file
    return bytes(data)


def test_header_reads_what_the_screen_lists() -> None:
    head = savegame.header(fake_header(elevation=1))
    assert head == {"character": "Name", "description": "v13 start", "game_date": (2161, 12, 5),
                    "game_time": 302400, "elevation": 1, "map_index": 11, "map_file": "V13ENT.SAV"}  # fmt: skip


def test_header_refuses_another_file() -> None:
    with pytest.raises(ValueError):
        savegame.header(b"\0" * 200)


def test_typeable_keeps_letters_digits_spaces() -> None:
    assert saves.typeable("v13-start") == "v13 start"
    assert len(saves.typeable("x" * 40)) == saves.DESCRIPTION_MAX


def test_slot_numbers() -> None:
    with pytest.raises(saves.SaveError):
        saves.slot_dir(0, Path("."))
    with pytest.raises(saves.SaveError):
        saves.slot_dir(11, Path("."))
    assert saves.slot_dir(3, Path("g")) == Path("g") / "DATA" / "SAVEGAME" / "SLOT03"


@pytest.fixture
def game(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(paths, "SAVES", tmp_path / "saves")
    monkeypatch.setattr(saves, "STILL_S", 0.0)
    monkeypatch.setattr(session, "game_dir", lambda: tmp_path / "game")
    slot = tmp_path / "game" / "DATA" / "SAVEGAME" / "SLOT02"
    slot.mkdir(parents=True)
    (slot / "SAVE.DAT").write_bytes(fake_header())
    (slot / "V13ENT.SAV").write_bytes(b"map")
    (slot / "V13ENT.BAK").write_bytes(b"old")
    return tmp_path


def test_keep_then_install(game: Path) -> None:
    folder = saves.keep(2, "v13-start", {"tile": 17690})
    assert sorted(p.name for p in (folder / "SLOT01").iterdir()) == ["SAVE.DAT", "V13ENT.SAV"]  # no .BAK
    info = json.loads((folder / "save.json").read_text(encoding="utf-8"))
    assert info["map_file"] == "V13ENT.SAV" and info["tile"] == 17690 and info["from_slot"] == 2
    old = game / "game" / "DATA" / "SAVEGAME" / "SLOT05"
    old.mkdir()
    (old / "STALE.SAV").write_bytes(b"x")
    dst = saves.install("v13-start", 5)
    assert sorted(p.name for p in dst.iterdir()) == ["SAVE.DAT", "V13ENT.SAV"]  # the slot's old files replaced
    assert [k["checkpoint"] for k in saves.kept()] == ["v13-start"]
    assert not list((game / "saves").glob(".save-*"))  # no staging left behind


def test_keep_again_replaces(game: Path) -> None:
    saves.keep(2, "cp")
    (game / "game" / "DATA" / "SAVEGAME" / "SLOT02" / "SAVE.DAT").write_bytes(fake_header(b"second"))
    folder = saves.keep(2, "cp")
    assert json.loads((folder / "save.json").read_text(encoding="utf-8"))["description"] == "second"


@pytest.mark.parametrize("name", ["", "..", "a/b", "a\\b", "c:", "x."])
def test_checkpoint_names_are_plain(game: Path, name: str) -> None:
    with pytest.raises(saves.SaveError):
        saves.keep(2, name)


def test_install_without_checkpoint(game: Path) -> None:
    with pytest.raises(saves.SaveError):
        saves.install("none", 1)


def test_remove_stays_inside_its_root(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(saves.SaveError):
        saves._remove(tmp_path / "a", tmp_path / "a")
    with pytest.raises(saves.SaveError):
        saves._remove(tmp_path, tmp_path / "a")
