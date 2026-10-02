"""Finding the game: Steam's library list and app manifest as Steam writes them, and the window size per screen."""

from pathlib import Path

from f1 import steam
from f1.instance import fitting_size

VDF = r"""
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"apps"		{ "228980"		"0" }
	}
	"1"
	{
		"path"		"E:\\SteamLibrary"
		"apps"		{ "38400"		"620000000" }
	}
}
"""

ACF = """
"AppState"
{
	"appid"		"38400"
	"name"		"Fallout: A Post Nuclear Role Playing Game"
	"installdir"		"Fallout"
	"buildid"		"300289"
}
"""


def test_library_folders_unescape_the_paths() -> None:
    assert steam.library_folders(VDF) == [Path(r"C:\Program Files (x86)\Steam"), Path(r"E:\SteamLibrary")]


def test_install_dir_and_build_id(tmp_path: Path) -> None:
    assert steam.install_dir(ACF) == "Fallout"
    manifest = tmp_path / "appmanifest_38400.acf"
    manifest.write_text(ACF, encoding="utf-8")
    assert steam.build_id(manifest) == "300289"
    assert steam.build_id(None) is None


def test_a_folder_given_by_hand_must_hold_the_exe(tmp_path: Path) -> None:
    try:
        steam.find(tmp_path)
    except steam.GameNotFound:
        pass
    else:
        raise AssertionError("an empty folder was taken for the game")
    (tmp_path / "FALLOUTW.EXE").write_bytes(b"")
    assert steam.find(tmp_path) == steam.Install(tmp_path, None)


def test_the_window_size_is_the_largest_that_fits_the_work_area() -> None:
    assert fitting_size((2560, 1400)) == (1920, 1080)  # a 1440p screen
    assert fitting_size((1920, 1040)) == (1280, 720)  # a 1080p screen less its taskbar: 1936 x 1119 does not fit
    assert fitting_size((1366, 728)) == (640, 480)
    assert fitting_size((600, 400)) == (640, 480)  # nothing fits: the smallest
