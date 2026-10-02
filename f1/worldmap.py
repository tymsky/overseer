"""The world map: travelling to a point or a town, and entering the place where the party stands.

From worldmap.c (fallout1-ce): the view shows the 1400 x 1500 world map through a 450 x 442 area at (22, 21) of the
640 x 480 window (HRP centres the window at bigger resolutions); its corner is the party's position minus (247, 242),
clamped to [0, 950] x [0, 1058]. A left click there sets target = view corner + mouse - (22, 21) and the party walks.
`dropbtn` is 1 while the party stands; a click on the party then enters the place (a town's map, or the desert).
Towns sit at 50 x (column, row) + 25.

    python -m f1.worldmap travel TOWN|X,Y      e.g. travel "shady sands", travel 1075,75
"""

import sys
import time

from f1 import clock, session, state, ui
from f1 import engine_map as em
from f1.memory import GameMemory

VIEW_X0, VIEW_Y0, VIEW_W, VIEW_H = 22, 21, 450, 442
VIEWPORT_MAX_X, VIEWPORT_MAX_Y = 950, 1058
TOWN_MAP_FIRST_CODE = 514  # the town map's entrance buttons send 514, 515, ...
WORLD_TOGGLE_CODE = 512  # the town map's button back to the world map
TOWNS = {  # worldmap.c city_location: (column, row)
    "vault 13": (16, 1), "vault 15": (25, 1), "shady sands": (21, 1), "junktown": (17, 10), "raiders": (22, 3),
    "necropolis": (22, 13), "the hub": (17, 14), "brotherhood": (12, 9), "military base": (3, 1),
    "the glow": (24, 25), "boneyard": (15, 18), "cathedral": (15, 20),
}  # fmt: skip


# worldmap.c TownHotSpots: each town map's entrances in order (the `section` of visit/enter_here), same town order.
SECTIONS = {
    "vault 13": ("V13ENT", "VAULT13", "VAULT13", "VAULT13"),
    "vault 15": ("VAULTENT", "VAULTBUR", "VAULTBUR", "VAULTBUR"),
    "shady sands": ("SHADYW", "SHADYE", "SHADYE"),
    "junktown": ("JUNKENT", "JUNKKILL", "JUNKCSNO"),
    "raiders": ("RAIDERS",),
    "necropolis": ("HOTEL", "HALLDED", "WATRSHD"),
    "the hub": ("HUBENT", "HUBDWNTN", "HUBHEIGT", "HUBOLDTN", "HUBWATER", "DETHCLAW"),
    "brotherhood": ("BROHDENT", "BROHD12", "BROHD12", "BROHD34", "BROHD34"),
    "military base": ("MBENT",),
    "the glow": ("GLOWENT", "GLOW1"),
    "boneyard": ("LAADYTUM", "LABLADES", "LAFOLLWR", "LAGUNRUN", "LARIPPER"),
    "cathedral": ("CHILDRN1", "CHILDRN1"),
}


def section(town: str, map_name: str) -> int:
    """The town map entrance that leads to `map_name` (e.g. ("junktown", "JUNKKILL") -> 1)."""
    return SECTIONS[town].index(map_name.upper())


def town_xy(name: str) -> tuple[int, int]:
    col, row = TOWNS[name]
    return 50 * col + 25, 50 * row + 25


def viewport(wx: int, wy: int) -> tuple[int, int]:
    return min(max(wx - 247, 0), VIEWPORT_MAX_X), min(max(wy - 242, 0), VIEWPORT_MAX_Y)


def _position(mem: GameMemory) -> tuple[int, int]:
    return mem.glob("world_xpos"), mem.glob("world_ypos")


def _view_origin(mem: GameMemory) -> tuple[int, int]:
    """The view's top-left corner on the screen."""
    x0, y0 = ui.origin(mem, mem.glob("world_win")) or (0, 0)
    return x0 + VIEW_X0, y0 + VIEW_Y0


def _click_towards(mem: GameMemory, tx: int, ty: int) -> tuple[int, int]:
    """Click the view where the target is, or at its edge in the target's direction; returns the world point asked."""
    wx, wy = _position(mem)
    vx, vy = viewport(wx, wy)
    ox, oy = _view_origin(mem)
    sx, sy = ox + tx - vx, oy + ty - vy
    # clamp inside the view, keeping the direction from the party
    px, py = ox + wx - vx, oy + wy - vy
    lo_x, hi_x, lo_y, hi_y = ox + 4, ox + VIEW_W - 4, oy + 4, oy + VIEW_H - 4
    if not (lo_x <= sx <= hi_x and lo_y <= sy <= hi_y):
        t = 1.0
        for d, p, lo, hi in ((sx - px, px, lo_x, hi_x), (sy - py, py, lo_y, hi_y)):
            if d > 0:
                t = min(t, (hi - p) / d)
            elif d < 0:
                t = min(t, (lo - p) / d)
        sx, sy = round(px + (sx - px) * t), round(py + (sy - py) * t)
    session.click(sx, sy)
    return vx + sx - ox, vy + sy - oy


def recentre(mem: GameMemory) -> None:
    """The cursor into the view, then the key C: the view back on the party (viewport = party - (247, 242), which
    `viewport` assumes). The world map scrolls 16 px a frame while the cursor is at the screen's edge (worldmap.c),
    and with HRP at 1920x1080 anywhere outside its 640x480 window too: a cursor left at (560, 84) by the town's exit
    moved the view 64 px before the first click (measured). From Vault 13 the party then walked 63 px south
    of the line to Shady Sands and the trip took 10-14 % more time than the exe's rule; the click to enter a town
    missed the party and walked it 80 px away."""
    ox, oy = _view_origin(mem)
    session.move(ox + VIEW_W // 2, oy + VIEW_H // 2)
    time.sleep(0.15)
    session.press("c")
    time.sleep(0.2)


def aim(mem: GameMemory, tx: int, ty: int) -> tuple[int, int]:
    """Click toward world point (tx, ty) from a recentred view; returns the point asked."""
    recentre(mem)
    asked = _click_towards(mem, tx, ty)
    time.sleep(0.4)
    return asked


def town_index(name: str) -> int:
    """worldmap.c's town number (TOWN_VAULT_13 = 0, ...): TOWNS is in that order."""
    return list(TOWNS).index(name)


def to_world_map(mem: GameMemory) -> bool:
    """From a town map: its world map button (code 512); True once the town map's entrances are gone."""
    toggle = next((b for b in ui.buttons(mem, mem.glob("world_win")) if b.answers(WORLD_TOGGLE_CODE)), None)
    if toggle is None:
        return not town_map_buttons(mem)
    session.click(*toggle.center)
    for _ in range(20):
        time.sleep(0.25)
        if not town_map_buttons(mem):
            return True
    return False


def travel(pid: int, tx: int, ty: int, timeout_s: float = 600) -> str:
    """Walk the party to world point (tx, ty). Returns "arrived", "town map" (a town's map is up: pick an entrance
    or go to the world map first), or what interrupted it (a map: an encounter)."""
    end = time.monotonic() + timeout_s
    with GameMemory(pid) as mem, clock.fast(pid):  # a test run's ride at its speed (the clicks at 1x)
        while time.monotonic() < end:
            screen = state.detect_screen(mem)
            if screen != "worldmap":
                return f"interrupted: {screen}"
            if town_map_buttons(mem):
                return "town map"
            wx, wy = _position(mem)
            if abs(wx - tx) <= 2 and abs(wy - ty) <= 2 and mem.glob("dropbtn") == 1:
                return "arrived"
            if mem.glob("dropbtn") == 1:  # standing: set the next target
                asked = aim(mem, tx, ty)
                got = (mem.glob("target_xpos"), mem.glob("target_ypos"))
                if mem.glob("dropbtn") == 0 and max(abs(got[0] - asked[0]), abs(got[1] - asked[1])) > 2:
                    aim(mem, tx, ty)  # walking the wrong way: a click while walking sets a new target
            time.sleep(0.5)
    return "timeout"


def town_map_buttons(mem: GameMemory) -> dict[int, ui.Button]:
    """While a town's map is up (a town entered before): its entrance buttons by section. Button i sends 514 + i
    and enters section tcode_xref[i] (worldmap.c town_map, RegTMAPsels)."""
    out = {}
    xref = em.GLOBALS["tcode_xref"].address
    for b in ui.buttons(mem, mem.glob("world_win")):
        code = b.codes[3] if b.codes[3] >= 0 else b.codes[2]
        if TOWN_MAP_FIRST_CODE <= code < TOWN_MAP_FIRST_CODE + 8:
            out[mem.u8(xref + code - TOWN_MAP_FIRST_CODE)] = b  # a byte array in this build (read 00 01 02 03)
    return out


def enter_here(pid: int, section: int = 0) -> str:
    """Click the standing party to enter the place under it; returns the screen that follows. A town entered before
    shows its town map first: then the entrance `section` (TownHotSpots order) is clicked."""
    with GameMemory(pid) as mem:
        clicked = False
        for attempt in range(3):  # once a click on the party entered nothing for 15 s: click again
            if not town_map_buttons(mem):
                if mem.glob("dropbtn") != 1:
                    return "not standing"
                recentre(mem)
                wx, wy = _position(mem)
                vx, vy = viewport(wx, wy)
                ox, oy = _view_origin(mem)
                session.click(ox + wx - vx, oy + wy - vy - 1)  # worldmap.c's hover: within 10 of (x + 22, y + 20)
            for _ in range(40 if clicked else 16):
                time.sleep(0.25)
                screen = state.detect_screen(mem)
                if screen != "worldmap":
                    return screen
                entrances = town_map_buttons(mem)
                if entrances and not clicked:
                    time.sleep(0.5)
                    b = entrances.get(section) or entrances[min(entrances)]
                    session.click(*b.center)
                    clicked = True
            if clicked:
                break
    return "still on the world map"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "travel":
        print(__doc__)
        return 2
    target = argv[1].lower()
    tx, ty = town_xy(target) if target in TOWNS else tuple(int(v) for v in target.split(","))
    pid = session.game_pid()
    print(travel(pid, tx, ty))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
