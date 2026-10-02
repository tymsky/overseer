"""What the runs skip, by the screen names the full runs' recordings hold: movies, the ending's slides, the credits."""

from f1.actions import cinema


def test_movies_slides_and_credits_are_cinema() -> None:
    assert cinema("window 6 (0x10 0,0 1920x1080)")  # the Cathedral's end, the vats, the walk away at Full HD
    assert cinema("window 1 (0x10 0,0 640x480)")  # a movie at 640x480
    assert cinema("endgame")  # the ending's slides
    assert cinema("window 6 (0x14 0,0 1920x1080)")  # the credits at Full HD
    assert cinema("window 4 (0x14 0,0 640x480)")  # the credits at 640x480


def test_the_play_is_not() -> None:
    for screen in ("map", "worldmap", "dialogue", "loadsave", "main_menu", "pipboy",
                   "window 7 (0x12 673,341 573x230)",  # the perk box
                   "window 9 (0x14 809,477 302x127)"):  # a message box  # fmt: skip
        assert not cinema(screen), screen
