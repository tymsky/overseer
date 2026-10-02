"""The map reader on the game's own maps (needs the instance and the knowledge base)."""

import pytest
from conftest import needs_knowledge

from f1 import mapfile, paths

INSTANCE = paths.INSTANCE


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
@needs_knowledge
def test_the_military_base_surface() -> None:
    m = mapfile.read("MBENT")
    assert m.script == "mbent"
    door = [o for o in m.objects if o.script == "mbout2in"]
    assert [(o.kind, o.tile) for o in door] == [("door", 21271)]
    guards = {o.script for o in m.objects if o.kind == "critter"}
    assert {"vgatemut", "vdoormut", "vfencemt"} <= guards
    assert all(o.data["map"] == -2 for o in m.objects if o.kind == "exit grid")  # out to the town map


@pytest.mark.skipif(not (INSTANCE / "MASTER.DAT").exists(), reason="instance not built")
@needs_knowledge
def test_vault_13s_cave_has_its_spatial_scripts() -> None:
    m = mapfile.read("V13ENT")
    assert {"cave2v13", "valtleav", "ratpit"} <= {s for s, _tile, _radius in m.spatial}
