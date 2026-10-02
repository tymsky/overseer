"""Floating text read back from its picture: the AAF font's parsing, and words drawn the engine's way then read."""

import struct

import pytest

from f1 import floats


def aaf(glyphs: dict[str, list[str]], max_height: int = 3) -> bytes:
    """A small AAF font: each glyph as rows of '#' (ink) and '.'."""
    head = b"AAFF" + struct.pack(">4h", max_height, 1, 2, 1)
    table, data = [], b""
    for code in range(256):
        rows = glyphs.get(chr(code), [])
        w, h = (len(rows[0]), len(rows)) if rows else (0, 0)
        table.append(struct.pack(">hhi", w, h, len(data)))
        data += bytes(7 if ch == "#" else 0 for row in rows for ch in row)
    return head + b"".join(table) + data


IDENTITY = list(range(256))
TINY = {"I": ["#", "#", "#"], "L": ["#.", "#.", "##"], "T": ["###", ".#.", ".#."], "o": ["##", "##"]}


def picture(lines: list[str], f: floats.Font, outline: int = 99) -> tuple[bytes, int, int]:
    """Lines drawn as text_object_create does: centred, one pixel in, the outline's colour around the ink; a pixel's
    value is its intensity (IDENTITY brightness)."""
    width = max(f.width(t) for t in lines) + 2
    height = (f.text_height + 1) * len(lines) + 2
    px = [[0] * width for _ in range(height)]
    for k, text in enumerate(lines):
        ink = floats.render(text, f)
        x0, y0 = (width - f.width(text)) // 2, 1 + k * (f.text_height + 1)
        for y, row in enumerate(ink):
            for x, level in enumerate(row):
                if level:
                    px[y0 + y][x0 + x] = level
    for y in range(height):
        for x in range(width):
            if px[y][x] == 0 and any(
                0 <= y + dy < height and 0 <= x + dx < width and 0 < px[y + dy][x + dx] < outline
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
            ):
                px[y][x] = outline
    return bytes(v for row in px for v in row), width, height


def test_an_aaf_font_is_parsed() -> None:
    f = floats.parse_font(aaf(TINY))
    assert (f.max_height, f.letter_spacing, f.word_spacing, f.line_spacing, f.text_height) == (3, 1, 2, 1, 4)
    assert f.glyphs["T"].levels == ((7, 7, 7), (0, 7, 0), (0, 7, 0))
    assert f.glyphs["o"].height == 2
    assert f.width("IL T") == (1 + 1) + (2 + 1) + (2 + 1) + (3 + 1)


def test_words_drawn_the_engines_way_are_read_back() -> None:
    f = floats.parse_font(aaf(TINY))
    pixels, w, h = picture(["TILT o", "LIT"], f)
    assert floats.read_picture(pixels, w, h, 2, f, IDENTITY) == "TILT o LIT"


@pytest.fixture(scope="module")
def font101() -> floats.Font:
    try:
        return floats.font(101)
    except (OSError, KeyError, ValueError):
        pytest.skip("no game instance here")


@pytest.mark.parametrize(
    "lines",
    [
        ["Zzzz"],
        ["We're closed. Come back around four", "o'clock."],
        ["Hey! Don't think you're going to get away", "with that, thief!"],
    ],
)
def test_the_games_float_font_reads_back(font101: floats.Font, lines: list[str]) -> None:
    pixels, w, h = picture(lines, font101)
    assert floats.read_picture(pixels, w, h, len(lines), font101, IDENTITY) == " ".join(lines)
