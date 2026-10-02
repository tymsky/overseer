"""What the game is doing, read from memory in one go: the player, the map, the camera, the mouse, the screen."""

import re
import struct
from dataclasses import asdict, dataclass

from f1 import engine_map as em
from f1.engine_map import MapHeader, Obj
from f1.geometry import Camera
from f1.memory import GameMemory

PC_STAT_LEVEL, PC_STAT_EXPERIENCE = 1, 2  # indexes into curr_pc_stat


@dataclass(frozen=True)
class Critter:
    address: int
    tile: int
    elevation: int
    rotation: int
    frame: int
    hp: int
    ap: int
    fid: int
    flags: int
    pid: int
    team: int
    results: int  # Dam flags: 0x80 dead, 0x01 knocked out, ...

    @property
    def dead(self) -> bool:
        return bool(self.results & 0x80)

    @property
    def multihex(self) -> bool:
        """OBJECT_MULTIHEX (0x800): a big body that also takes the six hexes round its tile (radscorpions)."""
        return bool(self.flags & 0x800)


# Screens by the GNW window id their module keeps. Several ids are stale after a screen closes and GNW reuses ids,
# so detection walks the live windows from the top: the map's own windows first, then the others in this order.
MAP_WINDOWS = ("interfaceWindow", "display_win", "bar_window")  # bar_window: indicators (LEVEL, POISONED) on the map
SCREEN_WINDOWS = (
    ("main_menu", "main_window"),
    ("select_character", "select_window_id"),
    ("dialogue", "gOptionWin"),  # the dialogue's windows are initialised to -1 and reset: reliable
    ("dialogue", "gReplyWin"),
    ("dialogue", "dialogueWindow"),
    ("dialogue", "dialogueBackWindow"),
    ("character", "edit_win"),
    ("inventory", "i_wid"),
    ("loot", "i_wid"),  # the same module's window, other shapes
    ("barter", "i_wid"),
    ("barter", "barter_back_win"),  # the 480x180 panel: i_wid is not its id (unnamed in every recording before)
    ("pipboy", "pip_win"),
    ("worldmap", "world_win"),
    ("skilldex", "skldxwin"),
    ("preferences", "prfwin"),
    ("options", "optnwin"),
    ("loadsave", "lsgwin"),
    ("elevator", "elev_win"),
    ("endgame", "endgame_window"),
    ("called_shot", "call_win"),
)
WINDOW_HIDDEN = 0x08


# The window each screen opens: (flags, width, height), measured live at 640x480. Needed because the stale ids of
# closed screens all point at the id GNW hands to the next window (five modules held id 1 at once). The
# place is not part of it: at 1920x1080 HRP centres these windows over the map view (the inventory at (711, 302),
# the Pip-Boy and the character screen at (640, 250)), the load screen over the whole screen (640, 300), the
# skilldex at the bar's right end (1171, 606); every size stayed (measured).
SIGNATURES = {
    "character": (0x12, 640, 480),
    "pipboy": (0x10, 640, 480),
    "inventory": (0x14, 499, 377),
    "skilldex": (0x12, 185, 368),
    "options": (0x12, 164, 217),
    "preferences": (0x12, 640, 480),  # measured; without it a stale prfwin claimed the loot window
    "loot": (0x14, 537, 376),
    "barter": (0x00, 480, 180),
    "loadsave": (0x14, 640, 480),  # its description box on top: 0x14, 290x85
    "elevator": (0x12, 230, 284),  # measured: a stale world_win held its id and claimed it
    "worldmap": (0x02, 640, 480),  # measured: a stale world_win claimed a loading map's window
}


# Screens that come in more than one shape: the second one.
OTHER_SHAPES = {"elevator": (0x12, 231, 285)}  # three buttons (the Military Base's, measured)


WINDOW_NAME = re.compile(r"window \d+ \((0x[0-9a-f]+) (-?\d+),(-?\d+) (\d+)x(\d+)\)")  # detect_screen's name


def window_kind(name: str) -> str | None:
    """An unclaimed window's kind by its shape: a movie plays in a 0x10 window from the screen's corner (640x480 at
    640x480, 1920x1080 at Full HD), a message box is a small 0x14 window (302x127, 290x85, 259x162 at 640x480, where
    the first two sat at x 169; HRP centres them at bigger resolutions)."""
    m = WINDOW_NAME.fullmatch(name)
    if m is None:
        return None
    flags, x, y, width, height = int(m[1], 16), int(m[2]), int(m[3]), int(m[4]), int(m[5])
    if flags == 0x10 and (x, y) == (0, 0):
        return "movie"
    return "box" if flags == 0x14 and width < 400 and height < 300 else None


def live_windows(mem: GameMemory) -> list[tuple[int, int, int, int, int, int]]:
    """(id, flags, x, y, width, height) of GNW's windows, bottom to top."""
    n = mem.glob("num_windows")
    if not 0 < n <= 50:
        return []
    out = []
    for ptr in struct.unpack(f"<{n}I", mem.read(em.GLOBALS["window"].address, 4 * n)):
        if ptr:
            wid, flags, ulx, uly, _lrx, _lry, width, height = struct.unpack("<8i", mem.read(ptr, 32))
            out.append((wid, flags, ulx, uly, width, height))
    return out


def detect_screen(mem: GameMemory) -> str:
    """Which screen is up: the topmost visible window a module claims by id and, where measured, by its shape."""
    visible = [w for w in live_windows(mem) if w[0] != 0 and not w[1] & WINDOW_HIDDEN]
    ids = {name: mem.glob(name) for _, name in SCREEN_WINDOWS} | {name: mem.glob(name) for name in MAP_WINDOWS}
    for wid, flags, x, y, width, height in reversed(visible):
        if any(ids[name] == wid for name in MAP_WINDOWS):
            return "map"
        claims = [screen for screen, name in SCREEN_WINDOWS if ids[name] == wid]
        shape = (flags & 0xFF, width, height)
        shaped = [s for s in claims if shape in (SIGNATURES.get(s), OTHER_SHAPES.get(s))]
        if {"character", "preferences"} <= set(shaped):
            # Same shape, and a new window reuses a closed one's id: right after character creation the editor's
            # stale edit_win named the preferences window "character". The preferences open from the
            # options menu, whose window stays under them.
            options = ids["optnwin"]
            under = any(w[0] == options and (w[1] & 0xFF, w[4], w[5]) == SIGNATURES["options"] for w in visible)
            return "preferences" if under else "character"
        if shaped:
            return shaped[0]
        unshaped = [s for s in claims if s not in SIGNATURES]
        if unshaped:
            return unshaped[0]
        return f"window {wid} ({flags & 0xFF:#x} {x},{y} {width}x{height})"
    return "none"


@dataclass(frozen=True)
class Snapshot:
    screen: str  # detect_screen(): "main_menu", "select_character", "map", "character", "inventory", ...
    map_name: str
    elevation: int
    dude: Critter | None
    level: int
    experience: int
    camera: Camera
    center_tile: int
    mouse: tuple[int, int]
    mouse_tile: int | None  # the hex cursor's tile
    mouse_mode: int
    game_time: int  # 0.1 s ticks

    def to_dict(self) -> dict:
        return asdict(self)


def character_name(mem: GameMemory) -> str:
    """The player's name (pc_name): which character the game holds."""
    return mem.read(em.GLOBALS["pc_name"].address, 32).split(b"\0", 1)[0].decode("latin1")


def read_critter(mem: GameMemory, address: int) -> Critter:
    raw = mem.read(address, Obj.SIZE)

    def i32(off: int) -> int:
        return int.from_bytes(raw[off : off + 4], "little", signed=True)

    return Critter(
        address=address,
        tile=i32(Obj.TILE),
        elevation=i32(Obj.ELEVATION),
        rotation=i32(Obj.ROTATION),
        frame=i32(Obj.FRAME),
        hp=i32(Obj.CRITTER_HP),
        ap=i32(Obj.CRITTER_AP),
        fid=i32(Obj.FID),
        flags=i32(Obj.FLAGS),
        pid=i32(Obj.PID),
        team=i32(Obj.CRITTER_TEAM),
        results=i32(Obj.CRITTER_RESULTS),
    )


def objects(mem: GameMemory) -> list[int]:
    """Addresses of every object on the map, from objectTable's per-tile lists."""
    heads = struct.unpack("<40000I", mem.read(em.GLOBALS["objectTable"].address, 4 * 40000))
    out = []
    for node in heads:
        hops = 0
        while node and hops < 1000:
            obj, node = struct.unpack("<II", mem.read(node, 8))
            out.append(obj)
            hops += 1
    return out


def critters(mem: GameMemory) -> list[Critter]:
    """Every critter on the map (the player included), read in full."""
    out = []
    for addr in objects(mem):
        pid = mem.u32(addr + Obj.PID)
        if pid >> 24 == 1:
            out.append(read_critter(mem, addr))
    return out


def read_camera(mem: GameMemory) -> Camera:
    g = mem.glob
    return Camera(g("tile_offx"), g("tile_offy"), g("tile_x"), g("tile_y"), g("buf_width"), g("buf_length"))


def read(mem: GameMemory) -> Snapshot:
    g = mem.glob
    name_raw = mem.read(em.GLOBALS["map_data"].address + MapHeader.NAME, 16)
    map_name = name_raw.split(b"\0", 1)[0].decode("latin1")
    dude_addr = mem.u32(em.GLOBALS["obj_dude"].address)
    mouse_obj = mem.u32(em.GLOBALS["obj_mouse"].address)
    screen = detect_screen(mem)
    pc_stat = em.GLOBALS["curr_pc_stat"].address
    return Snapshot(
        screen=screen,
        map_name=map_name,
        elevation=g("map_elevation"),
        dude=read_critter(mem, dude_addr) if dude_addr else None,
        level=mem.i32(pc_stat + 4 * PC_STAT_LEVEL),
        experience=mem.i32(pc_stat + 4 * PC_STAT_EXPERIENCE),
        camera=read_camera(mem),
        center_tile=g("tile_center_tile"),
        mouse=(g("mouse_x"), g("mouse_y")),
        mouse_tile=mem.i32(mouse_obj + Obj.TILE) if mouse_obj else None,
        mouse_mode=g("gmouse_3d_current_mode"),
        game_time=g("fallout_game_time"),
    )


def messages(mem: GameMemory, n: int = 5) -> list[str]:
    """The message box's newest `n` lines, oldest first, without their bullet: display.c writes line disp_start and
    advances it; the text lives in HRP's buffer (engine_map.HRP_MESSAGES), not in the engine's disp_str."""
    nxt = mem.glob("disp_start")
    out = []
    for k in range(n, 0, -1):
        line = (nxt - k) % em.HRP_MESSAGE_LINES
        raw = mem.read(em.HRP_MESSAGES + em.HRP_MESSAGE_LINE * line, em.HRP_MESSAGE_LINE)
        out.append(raw.split(b"\0", 1)[0].decode("latin1").lstrip("\x95 "))
    return out


def messages_since(mem: GameMemory, line: int, limit: int = 12) -> list[str]:
    """The message box's lines written since its line counter (disp_start) read `line`, oldest first."""
    n = (mem.glob("disp_start") - line) % em.HRP_MESSAGE_LINES
    return messages(mem, min(n, limit)) if n else []
