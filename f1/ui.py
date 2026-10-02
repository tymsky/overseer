"""The game's own buttons, read from memory: every GNW window keeps a list of its buttons with their rectangles and
event codes, so a screen's controls are found by code instead of by guessed pixel positions.
"""

import struct
from dataclasses import dataclass

from f1 import engine_map as em
from f1.memory import GameMemory

# Window: id, flags, rect(ulx, uly, lrx, lry), width, height, color, tx, ty, buffer, buttonListHead (+0x30)
WINDOW_BUTTONS = 0x30
# Button: id, flags, rect (+0x08), event codes: enter, exit, left down, left up, right down, right up (+0x18..);
# next (+0x78)
BUTTON_SIZE = 0x7C
BUTTON_NEXT = 0x78


@dataclass(frozen=True)
class Button:
    id: int
    flags: int
    x0: int  # screen coordinates, inclusive
    y0: int
    x1: int
    y1: int
    codes: tuple[int, int, int, int, int, int]  # enter, exit, left down, left up, right down, right up

    @property
    def center(self) -> tuple[int, int]:
        return (self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2

    def answers(self, code: int) -> bool:
        return code in (self.codes[2], self.codes[3])  # a left click sends one of these


def window_address(mem: GameMemory, window_id: int) -> int | None:
    n = mem.glob("num_windows")
    for ptr in struct.unpack(f"<{n}I", mem.read(em.GLOBALS["window"].address, 4 * n)):
        if ptr and struct.unpack("<i", mem.read(ptr, 4))[0] == window_id:
            return ptr
    return None


def origin(mem: GameMemory, window_id: int) -> tuple[int, int] | None:
    """The window's top-left corner on the screen (HRP centres the 640x480 screens at bigger resolutions)."""
    wptr = window_address(mem, window_id)
    return None if wptr is None else struct.unpack("<ii", mem.read(wptr + 8, 8))


def buttons(mem: GameMemory, window_id: int) -> list[Button]:
    """The window's buttons in screen coordinates."""
    wptr = window_address(mem, window_id)
    if wptr is None:
        return []
    wx, wy = struct.unpack("<ii", mem.read(wptr + 8, 8))
    node = mem.u32(wptr + WINDOW_BUTTONS)
    out = []
    for _ in range(500):
        if not node:
            break
        raw = mem.read(node, BUTTON_SIZE)
        bid, flags, x0, y0, x1, y1, *codes = struct.unpack("<12i", raw[:48])
        out.append(Button(bid, flags, wx + x0, wy + y0, wx + x1, wy + y1, tuple(codes)))  # type: ignore[arg-type]
        node = struct.unpack("<I", raw[BUTTON_NEXT : BUTTON_NEXT + 4])[0]
    return out


def find(mem: GameMemory, window_id: int, code: int) -> Button | None:
    return next((b for b in buttons(mem, window_id) if b.answers(code)), None)
