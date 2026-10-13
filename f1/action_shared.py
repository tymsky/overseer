"""Shared action results and engine modes used by multiple action families."""

from dataclasses import dataclass
from typing import NamedTuple

COMBAT_ACTIVE = 0x01  # combat_state bit (isInCombat)
MOUSE_CROSSHAIR = 2  # gmouse_3d_current_mode
MOUSE_ARROW = 1
MOUSE_USE_CROSSHAIR = 3  # an item to use on something (gmouse.h GAME_MOUSE_MODE_USE_CROSSHAIR)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    reason: str
    detail: dict


class Held(NamedTuple):
    """The weapon in the active hand: its pid, the rounds in it and which ammunition they are."""

    pid: int
    loaded: int
    ammo_pid: int
