"""An ironman scout plays the ironman's difficulties and rules at every speed's top."""

from f1 import flows


def test_scout_prefs_keep_the_ironman_difficulties_at_top_speeds():
    scout, ironman = flows.IRONMAN_TEST_PREFS, flows.IRONMAN_PREFS
    for key in ("combat_difficulty", "game_difficulty", "prf_running", "music_volume"):
        assert scout[key] == ironman[key]
    assert (scout["combat_speed"], scout["player_speedup"]) == (50, 1)
    assert (ironman["combat_speed"], ironman["player_speedup"]) == (0, 0)
