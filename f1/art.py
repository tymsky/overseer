"""The game's pictures (ART\\<TYPE>\\*.FRM) placed where the engine draws them, to aim a click where an object shows.

A WATRSHD ladder (tile 23858) shares its hex with a Cave Wall whose picture covers all of it but a strip along its
right rail, 16..20 px right of the hex centre (read from the two FRMs): a grid of aims 8 px apart never
landed there, and a use of the ladder took 61 s. Here each object's picture is put where CE's obj_bound puts it: its
bottom centre at the hex centre plus the art's shift for the rotation and the object's own pixel offset (obj->x, y);
a picture's pixel shows where its colour index is not 0 (transparent). The points to aim at are the target's pixels
that no other nearby picture covers, those with visible neighbours first (a pixel off still lands on it).

FRM (big-endian): version, fps, action frame, frames per direction (u16), x shifts and y shifts per direction (6 x
i16 each), offsets of each direction's frames (6 x u32), the frames' size; frames from 0x3E: width, height (u16),
size (u32), x and y offset (i16), then width x height colour indexes.
"""

import struct
from dataclasses import dataclass
from functools import cache

from f1 import knowledge
from f1.dat import GameFiles

TYPES = {0: "ITEMS", 1: "CRITTERS", 2: "SCENERY", 3: "WALLS", 4: "TILES", 5: "MISC", 6: "INTRFACE", 7: "INVEN"}
FRAMES_AT = 0x3E


@dataclass(frozen=True)
class Picture:
    width: int
    height: int
    shift_x: int  # the art's shift for the direction (FRM header)
    shift_y: int
    pixels: bytes  # width x height colour indexes, 0 transparent

    def opaque(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and self.pixels[y * self.width + x] != 0


@cache
def _files() -> GameFiles:
    return GameFiles(knowledge.INSTANCE)


@cache
def _listing(kind: str) -> tuple[str, ...]:
    return tuple(_files().read(f"ART/{kind}/{kind}.LST").decode("latin1").split())


@cache
def picture(fid: int, rotation: int = 0) -> Picture | None:
    """Frame 0 of the art `fid` in direction `rotation` (single-direction art has only 0); None for art this does
    not place (critters: their name also holds the animation)."""
    kind = TYPES.get((fid >> 24) & 0xF)
    if kind is None or kind == "CRITTERS":
        return None
    names = _listing(kind)
    index = fid & 0xFFF
    if index >= len(names):
        return None
    try:
        data = _files().read(f"ART/{kind}/{names[index]}")
    except KeyError:
        return None
    shifts_x, shifts_y = struct.unpack(">6h", data[10:22]), struct.unpack(">6h", data[22:34])
    offsets = struct.unpack(">6I", data[34:58])
    direction = rotation if 0 <= rotation < 6 and (rotation == 0 or offsets[rotation] != offsets[0]) else 0
    at = FRAMES_AT + offsets[direction]
    width, height = struct.unpack(">HH", data[at : at + 4])
    pixels = data[at + 12 : at + 12 + width * height]
    return Picture(width, height, shifts_x[direction], shifts_y[direction], pixels)


def corner(p: Picture, obj_x: int = 0, obj_y: int = 0) -> tuple[int, int]:
    """The picture's top-left pixel relative to its hex centre (obj_bound)."""
    return p.shift_x + obj_x - p.width // 2, p.shift_y + obj_y - p.height + 1


@dataclass(frozen=True)
class Placed:
    """A picture on the screen relative to some origin (the target's hex centre)."""

    picture: Picture
    x0: int
    y0: int

    def opaque(self, x: int, y: int) -> bool:
        return self.picture.opaque(x - self.x0, y - self.y0)


def visible_points(target: Placed, others: list[Placed], step: int = 2, limit: int = 12) -> list[tuple[int, int]]:
    """Points (relative to the origin) where the target's picture shows and no other covers it, best first: those
    whose neighbours 2 px away show too, nearest the visible part's middle; at most `limit`."""
    p = target.picture

    def shows(x: int, y: int) -> bool:
        return target.opaque(x, y) and not any(o.opaque(x, y) for o in others)

    seen = [
        (x, y)
        for y in range(target.y0, target.y0 + p.height, step)
        for x in range(target.x0, target.x0 + p.width, step)
        if shows(x, y)
    ]
    if not seen:
        return []
    mx = sorted(x for x, _ in seen)[len(seen) // 2]
    my = sorted(y for _, y in seen)[len(seen) // 2]

    def solid(pt: tuple[int, int]) -> int:
        x, y = pt
        return sum(shows(x + dx, y + dy) for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)))

    seen.sort(key=lambda pt: (-solid(pt), abs(pt[0] - mx) + abs(pt[1] - my)))
    out: list[tuple[int, int]] = []
    for pt in seen:  # spread out: a point every 6 px at least
        if all(abs(pt[0] - q[0]) + abs(pt[1] - q[1]) >= 6 for q in out):
            out.append(pt)
        if len(out) == limit:
            break
    return out


# ---- every frame of every direction, as the engine reads it (fallout1-ce art.cc: art_get_name 0x418BFC, art_get_code
# 0x418AE8, art_alias_fid, art_read_lst 0x418E38, frame_ptr 0x419008), for the hit test (f1/hit.py) ----
#
# A fid: list index bits 0-11, weapon code 12-15, animation 16-23, type 24-27, a rotation file 28-30. A critter's name
# gets two letters for its weapon and animation and `.frm`, or `.fr<r - 1>` with the rotation bits set; a few death
# animations take another critter's art (critters.lst's second field). The same rules as Fallout 2's art.cc.

LIST_TYPES = ("ITEMS", "CRITTERS", "SCENERY", "WALLS", "TILES", "MISC", "INTRFACE", "INVEN", "HEADS", "BACKGRND",
              "SKILLDEX")  # fmt: skip
# animations (fallout1-ce anim.h)
ANIM_WALK, ANIM_DODGE, ANIM_THROW, ANIM_FALL_BACK = 1, 13, 18, 20
ANIM_ELECTRIFY, ANIM_BURNED, ANIM_ELECTRIFIED, ANIM_FIRE_DANCE = 27, 29, 30, 33
ANIM_PRONE_TO_STANDING, ANIM_BACK_TO_STANDING, ANIM_TAKE_OUT, ANIM_FIRE_CONTINUOUS = 36, 37, 38, 47
ANIM_FALL_BACK_SF, ANIM_ELECTRIFY_SF, ANIM_BURNED_SF, ANIM_ELECTRIFIED_SF, ANIM_CALLED_SHOT = 48, 55, 57, 58, 64
ALIASED = {ANIM_ELECTRIFY, ANIM_BURNED, ANIM_ELECTRIFIED, ANIM_ELECTRIFY_SF, ANIM_BURNED_SF, ANIM_ELECTRIFIED_SF,
           ANIM_FIRE_DANCE, ANIM_CALLED_SHOT}  # fmt: skip
WEAPON_KNIFE, WEAPON_SPEAR, WEAPON_COUNT = 1, 4, 11


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    x: int  # the frame's own offset (an animation adds it to the object's x/y)
    y: int
    pixels: bytes  # width x height colour indexes, 0 transparent

    def opaque(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and self.pixels[y * self.width + x] != 0


@dataclass(frozen=True)
class Art:
    fps: int
    frames: int  # per direction
    shift_x: tuple[int, ...]  # per direction
    shift_y: tuple[int, ...]
    directions: tuple[tuple[Frame, ...], ...]  # [rotation][frame]

    def frame(self, frame: int, rotation: int) -> Frame | None:
        """frame_ptr: None out of range (the engine then has no pixel there)."""
        if not (0 <= rotation < 6 and 0 <= frame < self.frames):
            return None
        return self.directions[rotation][frame]


def parse_frm(data: bytes) -> Art:
    _version, fps, _action, frames = struct.unpack(">IHHH", data[:10])
    shift_x, shift_y = struct.unpack(">6h", data[10:22]), struct.unpack(">6h", data[22:34])
    offsets = struct.unpack(">6I", data[34:58])
    directions = []
    for d in range(6):
        at, out = FRAMES_AT + offsets[d], []
        for _ in range(frames):
            w, h, size, fx, fy = struct.unpack(">HHIhh", data[at : at + 12])
            out.append(Frame(w, h, fx, fy, data[at + 12 : at + 12 + w * h]))
            at += 12 + size
        directions.append(tuple(out))
    return Art(fps or 10, frames, shift_x, shift_y, tuple(directions))


def parse_lst(text: str) -> tuple[str, ...]:
    """art_read_lst: a list's names by line, each cut at a space, comma, semicolon or tab, at most 12 characters."""
    out = []
    for line in text.splitlines():
        cut = min([i for i in (line.find(c) for c in " ,;\t") if i >= 0], default=len(line))
        out.append(line[:cut][:12].upper())
    return tuple(out)


def critter_aliases(text: str) -> tuple[int, ...]:
    """critters.lst's second field per line (the art a few death animations borrow; the vault suit's without one)."""
    names = parse_lst(text)
    vault_guy = names.index("HMJMPS") if "HMJMPS" in names else 0
    return tuple(_atoi(line.split(",")[1]) if "," in line else vault_guy for line in text.splitlines())


def _atoi(s: str) -> int:
    s = s.lstrip()
    sign, s = (-1, s[1:]) if s[:1] == "-" else (1, s[1:] if s[:1] == "+" else s)
    digits = ""
    for c in s:
        if not c.isdigit():
            break
        digits += c
    return sign * int(digits) if digits else 0


def code_letters(animation: int, weapon: int) -> tuple[str, str] | None:
    """art_get_code: the critter file's two letters (weapon, animation), None where the engine has no art."""
    if not 0 <= weapon < WEAPON_COUNT:
        return None
    if ANIM_TAKE_OUT <= animation <= ANIM_FIRE_CONTINUOUS:
        if weapon == 0:
            return None
        return chr(ord("D") + weapon - 1), chr(ord("C") + animation - ANIM_TAKE_OUT)
    if animation == ANIM_PRONE_TO_STANDING:
        return "C", "H"
    if animation == ANIM_BACK_TO_STANDING:
        return "C", "J"
    if animation == ANIM_CALLED_SHOT:
        return "N", "A"
    if animation >= ANIM_FALL_BACK_SF:
        return "R", chr(ord("A") + animation - ANIM_FALL_BACK_SF)
    if animation >= ANIM_FALL_BACK:
        return "B", chr(ord("A") + animation - ANIM_FALL_BACK)
    if animation == ANIM_THROW:
        return {WEAPON_KNIFE: ("D", "M"), WEAPON_SPEAR: ("G", "M")}.get(weapon, ("A", "S"))
    if animation == ANIM_DODGE:
        return ("A", "N") if weapon <= 0 else (chr(ord("D") + weapon - 1), "E")
    letter = chr(ord("A") + animation)
    if animation <= ANIM_WALK and weapon > 0:
        return chr(ord("D") + weapon - 1), letter
    return "A", letter


def art_path(fid: int, lists: dict[str, tuple[str, ...]], aliases: tuple[int, ...] = ()) -> str | None:
    """art_get_name: the art file of a fid (ART/<type>/<name>), None where the engine has none."""
    rotation_file = (fid >> 28) & 0x7
    kind = (fid >> 24) & 0xF
    animation = (fid >> 16) & 0xFF
    if kind == 1 and animation in ALIASED and (fid & 0xFFF) < len(aliases):
        fid = (fid & 0x70000000) | (animation << 16) | 0x1000000 | (fid & 0xF000) | (aliases[fid & 0xFFF] & 0xFFF)
    index, weapon = fid & 0xFFF, (fid >> 12) & 0xF
    if kind >= len(LIST_TYPES) or kind == 8:
        return None
    names = lists.get(LIST_TYPES[kind], ())
    if index >= len(names) or not names[index]:
        return None
    if kind == 1:
        letters = code_letters(animation, weapon)
        if letters is None:
            return None
        ext = f"FR{rotation_file - 1}" if rotation_file else "FRM"
        return f"ART/CRITTERS/{names[index]}{letters[0]}{letters[1]}.{ext}"
    return f"ART/{LIST_TYPES[kind]}/{names[index]}"


@cache
def _lists() -> dict[str, tuple[str, ...]]:
    out = {}
    for kind in LIST_TYPES:
        try:
            out[kind] = parse_lst(_files().read(f"ART/{kind}/{kind}.LST").decode("latin1"))
        except KeyError:
            out[kind] = ()
    return out


@cache
def _aliases() -> tuple[int, ...]:
    return critter_aliases(_files().read("ART/CRITTERS/CRITTERS.LST").decode("latin1"))


@cache
def load(fid: int) -> Art | None:
    """The art of a fid from the instance's files, None where it has none."""
    path = art_path(fid, _lists(), _aliases())
    if path is None:
        return None
    try:
        return parse_frm(_files().read(path))
    except (KeyError, struct.error):
        return None
