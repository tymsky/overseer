"""state.objects: the map's objects read while the game moves them between its lists."""

import struct

from f1 import engine_map as em
from f1 import state
from f1.memory import ReadError


class Lists:
    """objectTable's heads and nodes: tile 0 holds a node with no object (0) and then one, tile 1 a node that is gone."""

    def __init__(self) -> None:
        self.table = em.GLOBALS["objectTable"].address
        self.nodes = {0x100: (0, 0x200), 0x200: (0x5000, 0)}

    def read(self, address: int, size: int) -> bytes:
        if address == self.table:
            heads = [0] * 40000
            heads[0], heads[1] = 0x100, 0x300
            return struct.pack("<40000I", *heads)
        if address not in self.nodes:
            raise ReadError(f"read of {size} bytes at {address:#x} failed")
        return struct.pack("<II", *self.nodes[address])


def test_a_node_with_no_object_and_a_list_that_comes_apart_are_skipped():
    # a counted life read an object at 0 opening a Thieves' Circle door, and its route ended
    assert state.objects(Lists()) == [0x5000]
