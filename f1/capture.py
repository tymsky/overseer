"""A window's picture through Windows Graphics Capture (the API OBS uses), cropped to the client area.

GDI does not work for Fallout here: BitBlt of the screen and PrintWindow both saw a white window under HRP's
DirectX 9 and DirectDraw 7 modes while the intro played in it on the screen. The frames reach the
display without passing through what GDI reads. WGC takes the window's own composed picture.
"""

import contextlib
import ctypes
import threading
from ctypes import wintypes

import numpy as np
from PIL import Image
from windows_capture import WindowsCapture

from f1 import win32

dwmapi = ctypes.WinDLL("dwmapi")
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class CaptureError(RuntimeError):
    pass


def visible_frame(hwnd: int) -> win32.Rect:
    """The window's visible bounds on screen (without the invisible resize borders), which WGC frames cover."""
    r = wintypes.RECT()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    return win32.Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def grab(hwnd: int, timeout_s: float = 5.0) -> Image.Image:
    """One frame of the window's client area, as RGB."""
    frames: list[np.ndarray] = []
    arrived = threading.Event()
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @cap.event
    def on_frame_arrived(frame, control) -> None:
        if not frames:
            frames.append(frame.frame_buffer.copy())
            arrived.set()
        control.stop()

    @cap.event
    def on_closed() -> None:
        arrived.set()

    control = cap.start_free_threaded()
    try:
        if not arrived.wait(timeout_s) or not frames:
            raise CaptureError(f"no frame from window {hwnd:#x} within {timeout_s} s")
    finally:
        with contextlib.suppress(Exception):  # usually stopped already, from the callback
            control.stop()

    bgra = frames[0]
    bounds, client = visible_frame(hwnd), win32.client_rect(hwnd)
    if (bgra.shape[1], bgra.shape[0]) != (bounds.width, bounds.height):
        raise CaptureError(f"frame {bgra.shape[1]}x{bgra.shape[0]} does not match the window {bounds}")
    x, y = client.left - bounds.left, client.top - bounds.top
    crop = bgra[y : y + client.height, x : x + client.width, 2::-1]  # BGRA -> RGB
    return Image.fromarray(np.ascontiguousarray(crop), "RGB")
