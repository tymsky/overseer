"""Floating text over critters ("Zzzz", "We're closed. Come back around four o'clock."), read back from memory.

A script's float_msg draws its string into a text object (fallout1-ce textobj.cc text_object_create, 0x49CFB4): the
list text_object_list (0x665210, 20 slots) and its count text_object_index (0x508324). The string itself is not
kept, only its picture: width = the widest line (+2 with an outline), height = (text_height + 1) a line (+2), each
line centred, drawn in font 101 (FONT1.AAF; 103 for warnings) by FMtext_to_buf (fontmgr.cc). The words are read
back by laying the font's glyphs over the picture, left to right.

    python -m f1.floats      the floats over the running game's critters now
"""

import struct
import sys
import threading
from dataclasses import dataclass
from functools import cache

from f1 import knowledge

TEXT_OBJECT_LIST = 0x665210  # TextObject* [20]
TEXT_OBJECT_INDEX = 0x508324  # how many are in use
TEXT_OBJECT_SIZE = 48  # flags, owner, time, linesCount, sx, sy, tile, x, y, width, height, data
MAX_OBJECTS = 20
FLOAT_FONTS = (101, 103)  # float_msg's font, and its warnings' (intextra.cc op_float_msg)


@dataclass(frozen=True)
class Glyph:
    width: int
    height: int
    levels: tuple[tuple[int, ...], ...]  # rows, top first: intensity 0 (none) to 7


@dataclass(frozen=True)
class Font:
    max_height: int
    letter_spacing: int
    word_spacing: int
    line_spacing: int
    glyphs: dict[str, Glyph]  # the printable characters

    @property
    def text_height(self) -> int:
        return self.line_spacing + self.max_height

    def width(self, text: str) -> int:
        """FMtext_width: every character's width (a space's is the word spacing) plus the letter spacing."""
        return sum((self.word_spacing if c == " " else self.glyphs[c].width) + self.letter_spacing for c in text)


def parse_font(data: bytes) -> Font:
    """An AAF font (fontmgr.cc FMLoadFont): 'AAFF', four big-endian shorts, 256 glyphs of (width, height, offset),
    then one byte of intensity a pixel from byte 2060."""
    if data[:4] != b"AAFF":
        raise ValueError("not an AAF font")
    max_height, letter, word, line = struct.unpack(">4h", data[4:12])
    glyphs = {}
    for code in range(33, 127):
        w, h, offset = struct.unpack(">hhi", data[12 + 8 * code : 20 + 8 * code])
        pixels = data[2060 + offset : 2060 + offset + w * h]
        glyphs[chr(code)] = Glyph(w, h, tuple(tuple(pixels[y * w : (y + 1) * w]) for y in range(h)))
    return Font(max_height, letter, word, line, glyphs)


@cache
def font(number: int = 101) -> Font:
    from f1 import dat

    return parse_font(dat.GameFiles(knowledge.INSTANCE).read(f"FONT{number - 100}.AAF"))


def render(text: str, f: Font) -> list[list[int]]:
    """One line's intensities as FMtext_to_buf lays them (for the tests): a picture text_height + 1 high."""
    rows = [[0] * f.width(text) for _ in range(f.text_height + 1)]
    x = 0
    for c in text:
        if c != " ":
            g = f.glyphs[c]
            for y, row in enumerate(g.levels):
                for dx, level in enumerate(row):
                    rows[f.max_height - g.height + y][x + dx] = level
        x += (f.word_spacing if c == " " else f.glyphs[c].width) + f.letter_spacing
    return rows


def _mismatch(ink: list[list[int]], top: int, x: int, g: Glyph, f: Font, limit: float = float("inf")) -> float:
    """How far the picture is from glyph g laid at x (and the letter gap after it left empty): the sum of the
    intensities' differences, given up once past `limit`. FONT1's 'a' and 'o' ink the same pixels and differ only in
    intensity."""
    bad, y0 = 0, top + f.max_height - g.height
    for y in range(f.max_height):
        row = ink[top + y] if top + y < len(ink) else []
        for dx in range(g.width + f.letter_spacing):
            want = g.levels[top + y - y0][dx] if dx < g.width and y0 <= top + y < y0 + g.height else 0
            have = row[x + dx] if x + dx < len(row) else 0
            bad += abs(want - have)
        if bad > limit:
            return float("inf")
    return bad


def read_line(ink: list[list[int]], top: int, f: Font) -> str:
    """The characters of the line whose band starts at row `top`: at each ink the glyph that fits best (the widest of
    the exact ones), a word space where the columns stay empty."""
    width = len(ink[0]) if ink else 0
    band = range(top, min(top + f.max_height, len(ink)))

    def empty(x: int) -> bool:
        return not any(ink[y][x] for y in band)

    x = next((c for c in range(width) if not empty(c)), width)
    out, end = [], 0  # end: where the last glyph's letter gap ended
    while x < width:
        if empty(x):
            gap = next((c for c in range(x, width) if not empty(c)), width) - x
            if x + gap < width:  # ink after it: a word space when the gap is a space's width
                out.append(" " * ((x + gap - end + 1) // (f.word_spacing + f.letter_spacing)))
            x += gap
            continue
        # a glyph may begin with blank columns: lay each one up to two pixels left of the first ink
        best = (float("inf"), 0, 0, "?")
        for c, g in f.glyphs.items():
            for k in (0, 1, 2):
                if x - k >= end:
                    best = min(best, (_mismatch(ink, top, x - k, g, f, best[0]), -g.width, k, c))
        bad, _, k, c = best
        g = f.glyphs[c]
        if bad > max(6, g.width * f.max_height // 2):
            out.append("?")
            x += 1
            continue
        out.append(c)
        x = end = x - k + g.width + f.letter_spacing
    return _twins("".join(out).strip(), f)


def _twins(text: str, f: Font) -> str:
    """Glyphs drawn alike (FONT1's 'I' and 'l') told apart by their neighbours: the lower case beside a lower-case
    letter ("closed", "o'clock"), the capital otherwise ("I'm")."""
    looks = {}
    for c, g in f.glyphs.items():
        looks.setdefault((g.width, g.height, g.levels), []).append(c)
    twin = {c: group for group in looks.values() if len(group) > 1 for c in group}
    out = list(text)
    for i, c in enumerate(out):
        if c in twin:
            beside = (out[i - 1] if i else "") + (out[i + 1] if i + 1 < len(out) else "")
            lower = [t for t in twin[c] if t.islower()]
            upper = [t for t in twin[c] if t.isupper()]
            out[i] = lower[0] if lower and any(b.islower() for b in beside) else (upper or [c])[0]
    return "".join(out)


def read_picture(
    pixels: bytes, width: int, height: int, lines: int, f: Font, brightness: list[int] | None = None
) -> str:
    """The words of a text object. Its ink is any pixel other than the background (0) and the outline's colour (what
    the border rows and columns hold: the text starts one pixel in); a pixel's intensity is its colour's brightness
    (COLOR.PAL) against the brightest ink, in the font's eight steps."""
    bright = brightness if brightness is not None else palette_brightness()
    rows = [list(pixels[y * width : (y + 1) * width]) for y in range(height)]
    outlined = height == (f.text_height + 1) * lines + 2
    border = {v for v in rows[0] + rows[-1] + [r[0] for r in rows] + [r[-1] for r in rows] if v} if outlined else set()
    inked = [v for row in rows for v in row if v and v not in border]
    top_bright = max((bright[v] for v in inked), default=1) or 1
    ink = [[round(7 * bright[v] / top_bright) if v and v not in border else 0 for v in row] for row in rows]
    top = 1 if outlined else 0
    return " ".join(read_line(ink, top + k * (f.text_height + 1), f) for k in range(lines)).strip()


@cache
def palette_brightness() -> list[int]:
    """r + g + b of each palette index (COLOR.PAL's first 768 bytes, 0-63 each; index 0 is the see-through one)."""
    from f1 import dat

    pal = dat.GameFiles(knowledge.INSTANCE).read("COLOR.PAL")
    return [0] + [sum(pal[3 * i : 3 * i + 3]) for i in range(1, 256)]


@dataclass(frozen=True)
class Float:
    owner: int  # the object's address
    tile: int
    time: int  # the game's tick count when it was made
    text: str

    @property
    def key(self) -> tuple[int, int, int]:
        return self.owner, self.tile, self.time


def read_words(pixels: bytes, width: int, height: int, lines: int) -> str:
    """In float_msg's font; a warning's (103) when that reads with fewer unknown characters."""
    text = read_picture(pixels, width, height, lines, font(101))
    if "?" in text:
        other = read_picture(pixels, width, height, lines, font(103))
        text = other if other.count("?") < text.count("?") else text
    return text


def keys(mem, owner: int) -> set[tuple[int, int, int]]:
    """The keys (owner, tile, time) of the floats over `owner` now, without reading their words: a new key after a
    click is the critter's answer (the cook's "I'm too busy to talk right now.", COOK.INT, instead of a talk)."""
    n = min(max(mem.i32(TEXT_OBJECT_INDEX), 0), MAX_OBJECTS)
    out = set()
    for i in range(n):
        ptr = mem.u32(TEXT_OBJECT_LIST + 4 * i)
        if ptr:
            _flags, who, time, _lines, _sx, _sy, tile = struct.unpack("<iIIiiii", mem.read(ptr, 28))
            if who == owner:
                out.add((who, tile, time))
    return out


def floats(mem, seen: frozenset | set = frozenset()) -> list[Float]:
    """Every floating text shown now, read back into words (those whose key is in `seen` are left unread)."""
    n = min(max(mem.i32(TEXT_OBJECT_INDEX), 0), MAX_OBJECTS)
    out = []
    for i in range(n):
        ptr = mem.u32(TEXT_OBJECT_LIST + 4 * i)
        if not ptr:
            continue
        _flags, owner, time, lines, _sx, _sy, tile, _x, _y, width, height, data = struct.unpack(
            "<iIIiiiiiiiiI", mem.read(ptr, TEXT_OBJECT_SIZE)
        )
        if not (0 < width <= 400 and 0 < height <= 200 and 0 < lines <= 10 and data) or (owner, tile, time) in seen:
            continue
        out.append(Float(owner, tile, time, read_words(mem.read(data, width * height), width, height, lines)))
    return out


class FloatWatch(threading.Thread):
    """Reads the floating texts as they come (every `period` s; one lasts 3.5 s and more, textobj.cc) and logs each
    new one as a "float" event with its owner's name: the words a player sees over heads, which the executors'
    own reports do not carry (Neal's "Zzzz")."""

    def __init__(self, pid: int, log, period: float = 0.25) -> None:
        super().__init__(daemon=True)
        from f1.memory import GameMemory

        self.mem, self.log, self.period = GameMemory(pid), log, period
        self.seen: set[tuple[int, int, int]] = set()
        self.halt = threading.Event()

    def run(self) -> None:
        from f1.clock import speed_of
        from f1.engine_map import Obj
        from f1.memory import ReadError

        period = self.period
        while not self.halt.wait(period):
            # a float lasts 3.5 s and more of the engine's clock: a faster clock (f1.clock) takes it away sooner
            period = min(self.period, 1.5 / speed_of(self.mem))
            try:
                for f in floats(self.mem, self.seen):
                    self.seen.add(f.key)
                    who = knowledge.proto_name(self.mem.u32(f.owner + Obj.PID)) if f.owner else "someone"
                    self.log.emit("float", who=who, text=f.text, tile=f.tile)
            except (ReadError, ValueError, OSError):
                continue

    def stop(self) -> None:
        self.halt.set()
        self.join(2)
        self.mem.close()


def main(argv: list[str]) -> int:
    from f1 import session, state
    from f1.memory import GameMemory

    mem = GameMemory(session.game_pid())
    try:
        names = {c.address: knowledge.proto_name(c.pid) for c in state.critters(mem)}
        for f in floats(mem):
            print(f"{names.get(f.owner, hex(f.owner))} (tile {f.tile}): {f.text!r}")
    finally:
        mem.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
