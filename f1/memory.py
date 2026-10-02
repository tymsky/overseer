"""Reading the running game's memory with ReadProcessMemory, by the numbers in f1/engine_map.py. Read-only."""

import ctypes
import struct
from typing import Self

from f1 import engine_map as em
from f1 import win32


class ReadError(RuntimeError):
    pass


class GameMemory:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.handle = win32.OpenProcess(win32.PROCESS_VM_READ | win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self.handle:
            raise ReadError(f"cannot open process {pid} for reading (error {ctypes.get_last_error()})")

    def close(self) -> None:
        if self.handle:
            win32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not win32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)):
            raise ReadError(f"read of {size} bytes at 0x{address:08X} failed (error {ctypes.get_last_error()})")
        return buf.raw[: got.value]

    def u8(self, address: int) -> int:
        return self.read(address, 1)[0]

    def i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def glob(self, name: str) -> int:
        """A 32-bit global by its engine name (signed)."""
        return self.i32(em.GLOBALS[name].address)

    def flag(self, name: str) -> bool:
        """A one-byte bool global by its engine name."""
        return bool(self.u8(em.GLOBALS[name].address))
