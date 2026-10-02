"""The hex grid: tiles, neighbours, and where a tile is on the screen.

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
