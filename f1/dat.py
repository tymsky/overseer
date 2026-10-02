"""Reading Fallout 1's DAT1 archives (MASTER.DAT, CRITTER.DAT), and the game's files as the engine sees them.

Format, from the engine's db.c/assoc.c/lzss.c (as fallout1-ce documents them): big-endian throughout. The directory
is an "assoc" array: a header of four longs (count, max, data size, a meaningless pointer), then per entry a
length-prefixed name and `data size` bytes. The root lists directory names; then each directory is an assoc of
file names with 16-byte entries: flags, offset, length (unpacked), packed length. Flags 0x10 (or 0): the whole file
is LZSS; 0x20: stored; 0x40: chunks, each a big-endian short length, stored when its top bit is set, else LZSS.

`GameFiles` looks where the engine looks: the loose files under DATA\\ first (master_patches=data), then MASTER.DAT,
then CRITTER.DAT. Names are case-insensitive, with backslashes.
"""

import struct
from dataclasses import dataclass
from pathlib import Path


class DatError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    flags: int
    offset: int
    length: int
    packed: int


def lzss_decode(data: bytes, limit: int | None = None) -> bytes:
    """The engine's LZSS: a flag byte per 8 items, bit set = literal byte, clear = (offset 12 bits, length 4 bits + 3)
    into a 4096-byte ring that starts filled with spaces, writing from 4078. Consumes `data` entirely."""
    ring = bytearray(b" " * 4096)
    r = 4078
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        flags = data[i]
        i += 1
        for bit in range(8):
            if i >= n:
                break
            if flags & (1 << bit):
                c = data[i]
                i += 1
                out.append(c)
                ring[r] = c
                r = (r + 1) & 0xFFF
            else:
                if i + 1 >= n:
                    i = n
                    break
                low, high = data[i], data[i + 1]
                i += 2
                off = low | ((high & 0xF0) << 4)
                for k in range((high & 0x0F) + 3):
                    c = ring[(off + k) & 0xFFF]
                    out.append(c)
                    ring[r] = c
                    r = (r + 1) & 0xFFF
        if limit is not None and len(out) >= limit:
            break
    return bytes(out)


class Dat1:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, Entry] = {}
        with path.open("rb") as f:
            dirs = self._read_assoc(f, data_reader=None)
            for d, _ in dirs:
                prefix = "" if d == "." else d.upper() + "\\"
                for name, entry in self._read_assoc(f, data_reader=self._read_entry):
                    self.entries[prefix + name.upper()] = entry

    @staticmethod
    def _long(f) -> int:
        b = f.read(4)
        if len(b) != 4:
            raise DatError("unexpected end of the directory")
        return struct.unpack(">i", b)[0]

    def _read_entry(self, f) -> Entry:
        flags, offset, length, packed = struct.unpack(">4i", f.read(16))
        return Entry(flags, offset, length, packed)

    def _read_assoc(self, f, data_reader) -> list[tuple[str, object]]:
        size, _max, datasize, _ptr = (self._long(f) for _ in range(4))
        out = []
        for _ in range(size):
            n = f.read(1)[0]
            name = f.read(n).decode("latin1")
            data: object = None
            if datasize:
                data = data_reader(f) if data_reader else f.read(datasize)
            out.append((name, data))
        return out

    def read(self, name: str) -> bytes:
        e = self.entries.get(name.upper().replace("/", "\\"))
        if e is None:
            raise KeyError(name)
        with self.path.open("rb") as f:
            f.seek(e.offset)
            kind = (e.flags & 0xF0) or 0x10
            if kind == 0x20:
                return f.read(e.length)
            if kind == 0x10:
                return lzss_decode(f.read(e.packed), e.length)[: e.length]
            if kind == 0x40:
                out = bytearray()
                while len(out) < e.length:
                    (n,) = struct.unpack(">H", f.read(2))
                    chunk = f.read(n & 0x7FFF)
                    out += chunk if n & 0x8000 else lzss_decode(chunk)
                return bytes(out[: e.length])
            raise DatError(f"{name}: unknown flags {e.flags:#x}")


class GameFiles:
    """The game's files the way the engine resolves them: DATA\\ patches, then MASTER.DAT, then CRITTER.DAT."""

    def __init__(self, game_dir: Path) -> None:
        self.game_dir = game_dir
        self.patches = game_dir / "DATA"
        self.master = Dat1(game_dir / "MASTER.DAT")
        self.critter = Dat1(game_dir / "CRITTER.DAT")

    def read(self, name: str) -> bytes:
        rel = name.replace("/", "\\")
        loose = self.patches.joinpath(*rel.split("\\"))
        if loose.is_file():
            return loose.read_bytes()
        for dat in (self.master, self.critter):
            try:
                return dat.read(rel)
            except KeyError:
                continue
        raise KeyError(name)

    def names(self, prefix: str = "") -> list[str]:
        p = prefix.upper().replace("/", "\\")
        return sorted({n for dat in (self.master, self.critter) for n in dat.entries if n.startswith(p)})
