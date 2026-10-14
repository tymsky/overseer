"""The watchdog's reasoning on made-up looks: when it trips, and what it leaves alone."""

import pytest

from f1 import watchdog
from f1.watchdog import Tracker


def feed(
    tracker: Tracker, looks: list[tuple], screen: str = "map", clock_step: int = 10, owner: set[int] = frozenset()
):
    """One look a second; returns the seconds at which it tripped."""
    trips, clock = [], 1000
    for t, look in enumerate(looks):
        clock += clock_step
        if tracker.update(look, screen, clock, float(t), t in owner):
            trips.append(t)
    return trips


def test_the_load_screen_left_open_trips_after_its_allowance() -> None:
    trips = feed(Tracker(), [("loadsave",)] * 50, "loadsave", clock_step=0)
    assert trips == [21, 42]  # 20 s allowed; then a whole allowance again for the route to recover


def test_any_change_is_progress() -> None:
    looks = [("map", t // 30) for t in range(200)]  # something moves every 30 s: under the map's 90
    assert feed(Tracker(), looks) == []


def test_the_map_allows_90_seconds() -> None:
    assert feed(Tracker(), [("map",)] * 100) == [91]


def test_a_rest_moves_the_clock() -> None:
    assert feed(Tracker(), [("pipboy",)] * 100, "pipboy", clock_step=3000) == []
    assert feed(Tracker(), [("pipboy",)] * 40, "pipboy", clock_step=10) == [31]  # the clock's own run is not a rest


def test_movies_the_ending_and_the_credits_never_trip() -> None:
    assert feed(Tracker(), [("m",)] * 400, "window 5 (0x10 0,0 1920x1080)", clock_step=0) == []
    assert feed(Tracker(), [("e",)] * 400, "endgame", clock_step=0) == []
    assert feed(Tracker(), [("c",)] * 600, "window 4 (0x14 0,0 640x480)", clock_step=0) == []  # the credits


def test_a_message_box_trips_soon() -> None:
    """Fail fast: 5 s (a refused rest's box held the hunt 20 s)."""
    assert feed(Tracker(), [("box",)] * 30, "window 9 (0x14 809,477 302x127)", clock_step=0) == [6, 12, 18, 24]


def test_the_owners_time_does_not_count() -> None:
    owner = set(range(10, 80))
    assert feed(Tracker(), [("map",)] * 150, owner=owner) == []


def test_check_raises_once_and_clear_forgets() -> None:
    watchdog._trip("nothing moved for 20 s on loadsave")
    with pytest.raises(watchdog.Stuck):
        watchdog.check()
    watchdog.check()  # once only
    watchdog._trip("again")
    watchdog.clear()
    watchdog.check()


def test_a_faster_clocks_own_run_is_no_progress() -> None:
    """At 16x the game's time runs about 120 ticks a second on the map, past CLOCK_JUMP; a stuck map still
    trips, a rest still counts."""
    tracker, clock, trips = Tracker(), 1000, []
    for t in range(100):
        clock += 120
        if tracker.update(("map",), "map", clock, float(t), False, speed=16):
            trips.append(t)
    assert trips == [91]
    assert feed(Tracker(), [("map",)] * 100, clock_step=120) == []  # read as a rest at the game's own speed


def test_a_game_that_is_gone_trips_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead game gives read errors every second; they had counted as a map loading, and a crash never tripped."""
    import threading

    from f1.memory import ReadError

    class Gone:
        def __init__(self, pid: int) -> None:
            raise ReadError(f"cannot open process {pid}")

    class Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []
            self.tripped = threading.Event()

        def emit(self, kind: str, **fields) -> None:
            self.events.append((kind, fields))
            self.tripped.set()

    monkeypatch.setattr(watchdog, "GameMemory", Gone)
    monkeypatch.setattr(watchdog.win32, "is_alive", lambda pid: False)
    monkeypatch.setattr(watchdog, "Guard", lambda state=None: None)
    monkeypatch.setattr(watchdog, "PERIOD_S", 0.01)
    log = Log()
    watchdog.clear()
    dog = watchdog.Watchdog(4242, log)
    dog.start()
    try:
        assert log.tripped.wait(2)
    finally:
        dog.stop()
        dog.join(2)
    assert log.events == [("stuck", {"screen": None, "why": "the game is gone"})]
    with pytest.raises(watchdog.Stuck, match="gone"):
        watchdog.check()


def test_a_route_stepping_to_and_fro_is_stuck() -> None:
    """The ironman run at Vault 13's library: a door a dweller kept open was opened again and again, the player's
    tile and the last line taking turns; each look differed from the one before and nothing tripped for minutes."""
    assert feed(Tracker(), [("door", 22124), ("door", 21724)] * 60) == [92]  # the second look was new
    assert feed(Tracker(), [("walk", t) for t in range(150)]) == []  # a long walk: every look new


def test_a_message_box_seen_before_gets_its_allowance_from_its_opening() -> None:
    # two lockers side by side: the second loot's looks (the loot screen, the how-many box) were all seen at the
    # first, so nothing is new; a box answered in a second must not trip, one left standing must
    box = "window 6 (0x14 752,382 259x162)"
    tracker, clock, trips = Tracker(), 1000, []
    timeline = [("map", "map")] + [("loot", "loot")] * 2 + [(box, box)] + [("loot", "loot")] * 3 + [("map", "map")]
    timeline += [("loot", "loot")] * 2 + [(box, box)] + [("loot", "loot")] * 2  # the second locker: all seen
    timeline += [(box, box)] * 7  # a box left standing
    for t, (look, screen) in enumerate(timeline):
        clock += 10
        if tracker.update((look,), screen, clock, float(t), False):
            trips.append(t)
    assert trips == [len(timeline) - 1]  # only the standing box: past its 5 s from its own opening
