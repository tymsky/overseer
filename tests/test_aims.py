"""The cursor's search for an object (actions.aim_order) and the step back from foes out of reach (retreat_hex)."""

from f1 import geometry
from f1.actions import AIM_GRID, BODY_GRID, aim_order, retreat_hex
from f1.geometry import Camera

CAM = Camera(offx=304, offy=182, tile_x=100, tile_y=100)
MID = 100 * 200 + 100


def test_the_grid_goes_out_from_the_centre() -> None:
    assert AIM_GRID[0] == (0, 0)
    assert len(AIM_GRID) == 121 and len(set(AIM_GRID)) == 121
    dist = [dx * dx + dy * dy for dx, dy in AIM_GRID]
    assert dist == sorted(dist)
    # the strongbox's (-16, -16) came after 70 points from the top row; now within the first 30
    assert AIM_GRID.index((-16, -16)) < 30


def test_a_body_is_looked_for_low_round_its_hex() -> None:
    assert BODY_GRID and all(-40 <= dy <= 16 for _, dy in BODY_GRID)
    assert set(BODY_GRID) <= set(AIM_GRID)


def test_the_point_that_named_it_before_comes_first_then_the_spots() -> None:
    order = aim_order([(105, 90), (100, 100)], (100, 100), AIM_GRID, [(-16, -16), None])
    assert order[:3] == [(84, 84), (105, 90), (100, 100)]
    assert len(order) == len(set(order)) == 122  # each point once: (100, 100) is the grid's centre too


def test_the_step_back_goes_farther_from_the_nearest_foe() -> None:
    foe = geometry.in_direction(MID, 0, 3)
    to = retreat_hex(MID, [foe], set(), 4, CAM)
    assert to is not None
    assert geometry.distance(to, foe) > 3 and geometry.distance(MID, to) <= 4


def test_no_step_back_when_every_farther_hex_is_blocked_or_there_are_no_foes() -> None:
    foe = geometry.in_direction(MID, 0, 3)
    ring = {t for r in (1, 2) for t in geometry.ring(MID, r)}
    assert retreat_hex(MID, [foe], ring, 2, CAM) is None
    assert retreat_hex(MID, [], set(), 4, CAM) is None


class FakeMem:
    """stack_offset[0] of the inventory list, moved by Down and Up within the list's length."""

    def __init__(self, length: int, lose_every: int = 0) -> None:
        self.offset, self.length, self.lose_every, self.keys = 0, length, lose_every, 0

    def glob(self, name: str) -> int:
        assert name == "stack_offset"
        return self.offset

    def press(self, names, gap_s, hold_s) -> None:
        for name in names:
            self.keys += 1
            if self.lose_every and self.keys % self.lose_every == 0:
                continue  # a press the game lost
            if name == "down" and self.offset + 6 < self.length:
                self.offset += 1
            elif name == "up" and self.offset > 0:
                self.offset -= 1


def scroll(mem: FakeMem, slot: int, monkeypatch) -> int | None:
    from f1 import actions, session

    monkeypatch.setattr(session, "press_keys", mem.press)
    actor = actions.Actor.__new__(actions.Actor)
    actor.mem = mem
    return actor._scroll_list(slot)


def test_the_list_scrolls_in_one_burst_to_show_the_slot(monkeypatch) -> None:
    mem = FakeMem(length=40)
    assert scroll(mem, 37, monkeypatch) == 5 and mem.offset == 32 and mem.keys == 32
    assert scroll(mem, 2, monkeypatch) == 0 and mem.offset == 2  # back up for a slot above the rows shown
    assert scroll(mem, 4, monkeypatch) == 2  # already shown: no key


def test_lost_presses_are_made_up(monkeypatch) -> None:
    mem = FakeMem(length=40, lose_every=5)
    assert scroll(mem, 37, monkeypatch) == 5


def test_a_slot_past_the_list_is_not_shown(monkeypatch) -> None:
    assert scroll(FakeMem(length=8), 20, monkeypatch) is None
