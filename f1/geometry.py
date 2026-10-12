"""The hex grid: tiles, neighbours, and where a tile is on the screen (and which tile is under a screen point).

Tile numbers run 0..39999 on a 200 x 200 grid per elevation; the column index runs right to left on the screen.
`tile_coord()` gives the top-left corner of a hex's 32 x 16 box in map-view pixels, from the camera globals that
tile_set_center() leaves (read live, see f1/state.py).
"""

from dataclasses import dataclass

GRID_WIDTH = 200
GRID_SIZE = GRID_WIDTH * GRID_WIDTH
HEX_W, HEX_H = 32, 16

# Rotation 0..5 = NE, E, SE, SW, W, NW. Tile step per rotation, by the column's parity (tile % 200 & 1).
DIR_TILE = (
    (-1, GRID_WIDTH - 1, GRID_WIDTH, GRID_WIDTH + 1, 1, -GRID_WIDTH),
    (-GRID_WIDTH - 1, -1, GRID_WIDTH, 1, 1 - GRID_WIDTH, -GRID_WIDTH),
)
# Screen offset from a hex to its neighbour per rotation (the engine's off_tile table).
OFF_TILE = ((16, 32, 16, -16, -32, -16), (-12, 0, 12, 12, 0, -12))


@dataclass(frozen=True)
class Camera:
    """The engine's tile_offx/tile_offy/tile_x/tile_y, and the map view's size."""

    offx: int
    offy: int
    tile_x: int
    tile_y: int
    view_width: int = 640
    view_height: int = 380
    # the roof squares' camera (square_offx/offy/x/y, set by tile_set_center beside the hexes')
    square_offx: int = 0
    square_offy: int = 0
    square_x: int = 0
    square_y: int = 0


def _plane(col: int, row: int) -> tuple[int, int]:
    """A hex box's corner on the whole map drawn as one picture, column 0 of row 0 at (0, 0). A row down is 16 px
    right and 12 down; a column right is 24 px right and 6 up, odd columns half a step lower (8 right, 6 down), so
    that two columns make the 48 x -12 of the off_tile table's east neighbour."""
    odd = col & 1
    return 24 * col + 8 * odd + 16 * row, -6 * col + 6 * odd + 12 * row


def tile_coord(tile: int, cam: Camera) -> tuple[int, int]:
    """Top-left corner of the tile's hex box in map-view pixels: its place on the whole map's picture, less the
    camera tile's, plus the camera's offset. With the camera on an odd column the engine's picture is not one rigid
    shift: it measures a tile from an even column beside the camera's, the left one for odd columns right of the
    camera and for even columns at or left of it, the right one for the rest."""
    col = GRID_WIDTH - 1 - tile % GRID_WIDTH
    anchor = cam.tile_x
    if anchor & 1:
        anchor += -1 if (col & 1) == (col > anchor) else 1
    x, y = _plane(col, tile // GRID_WIDTH)
    ax, ay = _plane(anchor, cam.tile_y)
    return cam.offx + x - ax, cam.offy + y - ay


def tile_center(tile: int, cam: Camera) -> tuple[int, int]:
    x, y = tile_coord(tile, cam)
    return x + HEX_W // 2, y + HEX_H // 2


def on_view(tile: int, cam: Camera, margin: int = 8) -> bool:
    """The tile's centre is inside the map view, at least `margin` pixels from its edges."""
    x, y = tile_center(tile, cam)
    return margin <= x < cam.view_width - margin and margin <= y < cam.view_height - margin


def neighbour(tile: int, rotation: int) -> int:
    return tile + DIR_TILE[(tile % GRID_WIDTH) & 1][rotation]


def in_direction(tile: int, rotation: int, distance: int) -> int:
    for _ in range(distance):
        tile = neighbour(tile, rotation)
    return tile


def axial(tile: int) -> tuple[int, int]:
    """Axial hex coordinates (q, r). The grid is 'even-q': an even column's side neighbours sit on its row and the row
    below (DIR_TILE[0]), an odd column's on its row and the row above."""
    x, y = tile % GRID_WIDTH, tile // GRID_WIDTH
    return x, y - (x + (x & 1)) // 2


def distance(a: int, b: int) -> int:
    """Hex steps between two tiles (what tile_dist() walks)."""
    (q1, r1), (q2, r2) = axial(a), axial(b)
    return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2


def neighbours(tile: int) -> list[int]:
    """The up to six tiles next to `tile`, inside the grid."""
    x = tile % GRID_WIDTH
    out = []
    for r in range(6):
        n = neighbour(tile, r)
        if 0 <= n < GRID_SIZE and abs(n % GRID_WIDTH - x) <= 1:
            out.append(n)
    return out


def ring(tile: int, radius: int) -> list[int]:
    """Tiles exactly `radius` steps away, walking the hexagon around `tile`."""
    if radius == 0:
        return [tile]
    out = []
    t = in_direction(tile, 4, radius)  # start west, then walk the six sides
    for side in (0, 1, 2, 3, 4, 5):
        for _ in range(radius):
            out.append(t)
            t = neighbour(t, side)
    return out


# ---- the screen to the grid, the engine's own functions (fallout1-ce tile.cc, the 1.1 exe's code at the addresses
# its comments give, read with capstone: tile_num 0x49E354, tile_in_front_of 0x49E4A0, tile_to_right_of 0x49E508,
# square_xy_roof 0x49EB30, square_coord_roof 0x49E954, tile_num_beyond 0x49E680). C division truncates toward zero.


def _cdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _tile_mask() -> bytes:
    """tile_init's 32 x 16 table: which neighbour a pixel of a hex box belongs to (0 the hex itself)."""
    out = bytearray()
    for v11 in range(0, 64, 16):
        out += bytes(1 if v13 > v11 else 0 for v13 in range(64, 0, -4))
        out += bytes(2 if v13 > v11 else 0 for v13 in range(0, 64, 4))
    out += bytes(32 * 8)
    for v11 in range(0, 64, 16):
        out += bytes(0 if v13 > v11 else 3 for v13 in range(0, 64, 4))
        out += bytes(0 if v13 > v11 else 4 for v13 in range(64, 0, -4))
    return bytes(out)


TILE_MASK = _tile_mask()


def tile_num(x: int, y: int, cam: Camera, ignore_bounds: bool = False) -> int:
    """tile_num: the tile under a map-view point (-1 off the grid unless `ignore_bounds`)."""
    v2 = y - cam.offy
    v3 = v2 // 12 if v2 >= 0 else _cdiv(v2 + 1, 12) - 1
    v4 = x - cam.offx - 16 * v3
    v5 = v2 - 12 * v3
    v6 = v4 // 64 if v4 >= 0 else _cdiv(v4 + 1, 64) - 1
    v8 = v4 - v6 * 64
    v9 = 2 * v6
    if v8 >= 32:
        v8 -= 32
        v9 += 1
    row = cam.tile_y + v6 + v3
    colx = cam.tile_x + v9
    m = TILE_MASK[32 * v5 + v8]
    if m == 2:
        colx += 1
        if colx & 1:
            row -= 1
    elif m == 1:
        row -= 1
    elif m == 3:
        colx -= 1
        if not colx & 1:
            row += 1
    elif m == 4:
        row += 1
    col = GRID_WIDTH - 1 - colx
    if (0 <= col < GRID_WIDTH and 0 <= row < GRID_WIDTH) or ignore_bounds:
        return GRID_WIDTH * row + col
    return -1


def in_front_of(tile1: int, tile2: int, cam: Camera) -> bool:
    """tile_in_front_of."""
    x1, y1 = tile_coord(tile1, cam)
    x2, y2 = tile_coord(tile2, cam)
    return float(x2 - x1) <= float(y2 - y1) * -4.0


def to_right_of(tile1: int, tile2: int, cam: Camera) -> bool:
    """tile_to_right_of (the exe's constant is a hair above 4/3, CE's note)."""
    x1, y1 = tile_coord(tile1, cam)
    x2, y2 = tile_coord(tile2, cam)
    return float(x2 - x1) <= float(y2 - y1) * 1.3333333333333335


def roof_square_at(x: int, y: int, cam: Camera, square_width: int = 100) -> tuple[int, int]:
    """square_xy_roof: the roof square (column, row) under a map-view point."""
    v4 = x - cam.square_offx
    v5 = y + 96 - cam.square_offy - 12
    v6 = 3 * v4 - 4 * v5
    cx = v6 // 192 if v6 >= 0 else _cdiv(v6 + 1, 192) - 1
    v8 = 4 * v5 + v4
    cy = v8 // 128 if v8 >= 0 else _cdiv(v8 + 1, 128) - 1
    return square_width - 1 - (cx + cam.square_x), cy + cam.square_y


def roof_square_to_screen(square: int, cam: Camera, square_width: int = 100) -> tuple[int, int]:
    """square_coord_roof: the top-left of a roof square's picture."""
    v5 = square_width - 1 - square % square_width
    v6 = square // square_width
    x = cam.square_offx + 48 * (v5 - cam.square_x)
    y = cam.square_offy - 12 * (v5 - cam.square_x)
    v9 = v6 - cam.square_y
    return x + 32 * v9, y + 24 * v9 - 96


def tile_is_valid(tile: int) -> bool:
    return 0 <= tile < GRID_SIZE


def tile_on_edge(tile: int) -> bool:
    """tile_on_edge: the grid's first or last row or column."""
    if not tile_is_valid(tile):
        return False
    return tile < GRID_WIDTH or tile >= GRID_SIZE - GRID_WIDTH or tile % GRID_WIDTH in (0, GRID_WIDTH - 1)


def tile_num_in_direction(tile: int, rotation: int, steps: int = 1) -> int:
    """tile_num_in_direction: STEPS hexes in a rotation's direction, stopping at the grid's edge."""
    for _ in range(steps):
        if tile_on_edge(tile):
            break
        tile += DIR_TILE[(tile % GRID_WIDTH) & 1][rotation]
    return tile
