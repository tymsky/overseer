"""The bot's side of the DINPUT.DLL proxy (tools/dinput/dinput_proxy.c): the shared memory through which the game's
mouse and keyboard are fed, so the bot needs neither the real cursor, the real keyboard nor the focus.

How the engine takes input (static, from the exe and HRP's f1_res.dll): keys come only from DirectInput's buffered
keyboard; the mouse from DirectInput's relative state; HRP in a window also sets the engine's cursor from the desktop's
cursor (GetCursorPos). The proxy answers the game's DirectInput calls from this memory and points HRP's
GetCursorPos/SetCursorPos/ClipCursor at a virtual cursor kept here, so the owner's pointer is never read, moved or
clipped. Measured live: walks, keys and a dialogue with the game's window behind another one.

The memory is created before the game starts (the DLL looks for it when the game creates DirectInput) and outlives
the command that made it as long as the game holds its view; later commands open it by name.
"""

import ctypes
import struct
import time
from ctypes import wintypes

NAME = "Local\\GNWInput"
SIZE = 64 + 256 * 4
MAGIC = 0x49574E47  # 'GNWI'
TOTAL_DX, TOTAL_DY, BUTTONS, KEY_HEAD, KEY_TAIL, MOUSE_READS, KEY_READS, FLAGS = 8, 12, 16, 20, 24, 28, 32, 36
VIRT_X, VIRT_Y, REDIRECTED, RING = 40, 44, 48, 64
EXTENDED = 0x80  # DirectInput's code for an extended key is its scan code | 0x80

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileMappingW.restype = wintypes.HANDLE
_k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.LPCWSTR]  # fmt: skip
_k32.OpenFileMappingW.restype = wintypes.HANDLE
_k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PAGE_READWRITE, FILE_MAP_ALL_ACCESS = 0x04, 0xF001F


class FeedError(RuntimeError):
    pass


class Feed:
    """The shared memory, created (`create=True`, before the game starts) or opened (while the game holds it)."""

    def __init__(self, create: bool = False, name: str = NAME) -> None:
        if create:
            self.handle = _k32.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, SIZE, name)
        else:
            self.handle = _k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, name)
        if not self.handle:
            raise FeedError(f"no input memory {name!r} (error {ctypes.get_last_error()})")
        self.view = _k32.MapViewOfFile(self.handle, FILE_MAP_ALL_ACCESS, 0, 0, SIZE)
        if not self.view:
            raise FeedError(f"cannot map the input memory (error {ctypes.get_last_error()})")
        if create and self._get(0) != MAGIC:  # new: a fresh layout; an existing one keeps its counters
            ctypes.memset(self.view, 0, SIZE)
            self._put(4, 1)
            self._put(0, MAGIC)

    def close(self) -> None:
        if self.view:
            _k32.UnmapViewOfFile(self.view)
            self.view = None
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def _get(self, at: int) -> int:
        return struct.unpack("<i", ctypes.string_at(self.view + at, 4))[0]

    def _put(self, at: int, value: int) -> None:
        ctypes.memmove(self.view + at, struct.pack("<i", value), 4)

    @property
    def mouse_reads(self) -> int:
        """How often the game has read the mouse: one a frame while its input loop runs."""
        return self._get(MOUSE_READS)

    @property
    def devices(self) -> tuple[int, int]:
        """(mouse, keyboard) cooperative levels the game asked for; (0, 0) until it has created its devices."""
        f = self._get(FLAGS)
        return (f >> 8) & 0xFF, f & 0xFF

    @property
    def redirected(self) -> int:
        """How many cursor imports (HRP's and the exe's) the DLL pointed at the virtual cursor."""
        return self._get(REDIRECTED)

    def wait_reads(self, n: int = 2, timeout_s: float = 2.0) -> bool:
        """Wait until the game has read the mouse `n` more times; False if it has not (its loop is stalled)."""
        start, end = self.mouse_reads, time.monotonic() + timeout_s
        while self.mouse_reads - start < n:
            if time.monotonic() > end:
                return False
            time.sleep(0.005)
        return True

    def point(self, sx: int, sy: int) -> None:
        """The virtual desktop cursor HRP reads, in screen coordinates."""
        self._put(VIRT_X, sx)
        self._put(VIRT_Y, sy)

    def nudge(self, dx: int, dy: int) -> None:
        """Relative mouse motion: the game reads what was added since its last read."""
        self._put(TOTAL_DX, self._get(TOTAL_DX) + dx)
        self._put(TOTAL_DY, self._get(TOTAL_DY) + dy)

    def buttons(self, left: bool = False, right: bool = False) -> None:
        self._put(BUTTONS, (1 if left else 0) | (2 if right else 0))

    def key(self, code: int, down: bool, timeout_s: float = 2.0) -> bool:
        """A key event (a DirectInput code); True once the game has taken it."""
        head = self._get(KEY_HEAD)
        self._put(RING + 4 * (head & 255), (code & 0xFF) | (0x100 if down else 0))
        self._put(KEY_HEAD, head + 1)
        end = time.monotonic() + timeout_s
        while self._get(KEY_TAIL) < head + 1:
            if time.monotonic() > end:
                return False
            time.sleep(0.005)
        return True
