"""The game's picture composed from its own memory: GNW window buffers in z-order, through the system palette.

Windows capture APIs do not see the map view under HRP's DirectDraw 7 mode (GDI, WGC and DXGI all failed while the
game showed on the physical screen), so the frame comes from where the engine draws it: each
GNW window keeps an 8-bit buffer, the windows are drawn bottom to top, and systemCmap holds the palette on screen.
Works with the window covered or in the background, and shows the movies too (they draw into a window).
"""

import struct

import numpy as np
from PIL import Image

from f1 import engine_map as em
from f1.memory import GameMemory

WINDOW_ARRAY = em.GLOBALS["window"].address
NUM_WINDOWS = em.GLOBALS["num_windows"].address
SYSTEM_CMAP = em.GLOBALS["systemCmap"].address
MAX_WINDOWS = 50
WINDOW_HIDDEN, WINDOW_TRANSPARENT = 0x08, 0x20
SCREEN_W, SCREEN_H = 640, 480  # the original screen; the game's own is read (screen_size)


def palette(mem: GameMemory) -> np.ndarray:
    """256 x 3 RGB bytes: the 6-bit components widened to 8 bits."""
    cmap = np.frombuffer(mem.read(SYSTEM_CMAP, 768), dtype=np.uint8).reshape(256, 3) & 0x3F
    return (cmap << 2) | (cmap >> 4)


def windows(mem: GameMemory) -> list[dict]:
    n = mem.i32(NUM_WINDOWS)
    if not 0 < n <= MAX_WINDOWS:
        return []
    out = []
    for ptr in struct.unpack(f"<{n}I", mem.read(WINDOW_ARRAY, 4 * n)):
        if not ptr:
            continue
        wid, flags, ulx, uly, _lrx, _lry, width, height, _color, _tx, _ty, buf = struct.unpack(
            "<iiiiiiiiiiiI", mem.read(ptr, 48)
        )
        out.append({"id": wid, "flags": flags, "x": ulx, "y": uly, "w": width, "h": height, "buffer": buf})
    return out


def screen_size(mem: GameMemory, wins: list[dict] | None = None) -> tuple[int, int]:
    """The game's screen (width, height): GNW's window 0, the background as big as the screen (640x480 measured at
    640x480; the resolution is HRP's SCR_WIDTH/SCR_HEIGHT)."""
    for w in wins if wins is not None else windows(mem):
        if w["id"] == 0 and w["w"] > 0 and w["h"] > 0:
            return w["w"], w["h"]
    return SCREEN_W, SCREEN_H


def compose_indexed(mem: GameMemory) -> np.ndarray:
    """The screen as palette indexes, height x width of the game's screen. GNW's background window 0 is left out
    (index 0, black): at 640x480 other windows always covered it, and at 1920x1080 it shows round the world map, its
    buffer all index 130 (salmon in that palette, measured), where HRP blackens the screen."""
    wins = windows(mem)
    width, height = screen_size(mem, wins)
    canvas = np.zeros((height, width), dtype=np.uint8)
    for w in wins:
        if w["id"] == 0 or w["flags"] & WINDOW_HIDDEN or not w["buffer"] or w["w"] <= 0 or w["h"] <= 0:
            continue
        pixels = np.frombuffer(mem.read(w["buffer"], w["w"] * w["h"]), dtype=np.uint8).reshape(w["h"], w["w"])
        x0, y0 = max(w["x"], 0), max(w["y"], 0)
        x1, y1 = min(w["x"] + w["w"], width), min(w["y"] + w["h"], height)
        if x0 >= x1 or y0 >= y1:
            continue
        src = pixels[y0 - w["y"] : y1 - w["y"], x0 - w["x"] : x1 - w["x"]]
        dst = canvas[y0:y1, x0:x1]
        if w["flags"] & WINDOW_TRANSPARENT:
            np.copyto(dst, src, where=src != 0)
        else:
            dst[:] = src
    return canvas


def draw_cursor(mem: GameMemory, canvas: np.ndarray) -> None:
    """Draw the mouse cursor's picture where the engine draws it (mouse_show() in mouse.c), unless it is hidden."""
    g = mem.glob
    if mem.flag("mouse_is_hidden"):
        return
    w, h, pitch = g("mouse_width"), g("mouse_length"), g("mouse_full")
    shape_ptr = mem.u32(em.GLOBALS["mouse_shape"].address)
    if not shape_ptr or not (0 < w <= 64 and 0 < h <= 64 and pitch >= w):
        return
    shape = np.frombuffer(mem.read(shape_ptr, pitch * h), dtype=np.uint8).reshape(h, pitch)[:, :w]
    trans = mem.u8(em.GLOBALS["mouse_trans"].address)
    x, y = g("mouse_x"), g("mouse_y")
    height, width = canvas.shape
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
    if x0 >= x1 or y0 >= y1:
        return
    src = shape[y0 - y : y1 - y, x0 - x : x1 - x]
    np.copyto(canvas[y0:y1, x0:x1], src, where=src != trans)


def compose_rgb(mem: GameMemory, cursor: bool = True) -> np.ndarray:
    """The screen as an H x W x 3 RGB array, with the cursor drawn in."""
    canvas = compose_indexed(mem)
    if cursor:
        draw_cursor(mem, canvas)
    return palette(mem)[canvas]


def compose(mem: GameMemory, cursor: bool = True) -> Image.Image:
    """The screen as an RGB picture."""
    return Image.fromarray(compose_rgb(mem, cursor), "RGB")
