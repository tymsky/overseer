"""The script reader on a hand-made program, and on the game's MASTER.INT when the scripts are extracted."""

import struct

import pytest
from conftest import needs_knowledge

from f1 import intdump


def _program(code: bytes, name: bytes = b"talk") -> bytes:
    """A one-procedure .INT: 42 bytes of header, the procedure table, identifiers, static strings, code."""
    identifiers = b"\x00\x06" + name + b"\x00"  # a record points at its name from the length field: 4 + 2
    strings = struct.pack(">i", 4) + b"hi\x00\x00"
    table_end = 46 + 24
    code_at = table_end + 4 + len(identifiers) + len(strings)
    record = struct.pack(">6i", 6, 0, 0, 0, code_at, 0)
    return (
        bytes(42) + struct.pack(">i", 1) + record + struct.pack(">i", len(identifiers)) + identifiers + strings + code
    )


def push(value: int) -> bytes:
    return struct.pack(">Hi", intdump.PUSH_INT, value)


@needs_knowledge
def test_a_dialogue_option_and_a_skill_check_read_back() -> None:
    code = (
        push(0x100)  # where to go when the check fails
        + push(0)  # dude_obj() is a call; here a plain 0 stands in for the object
        + push(14)  # SPEECH
        + push(-20)
        + struct.pack(">H", 0x80AC)  # roll_vs_skill(obj, skill, mod)
        + struct.pack(">H", 0x80AF)  # is_success(roll)
        + struct.pack(">H", 0x802F)  # if
        + push(6)
        + push(0)  # message list 0: no file, no text
        + push(133)
        + push(0)  # procedure 0: this one
        + push(50)
        + struct.pack(">H", 0x8121)  # giq_option(iq, list, msg, proc, reaction)
    )
    script = intdump.Script(_program(code))
    assert [p.name for p in script.procs] == ["talk"]
    lines = intdump.decompile(script, script.procs[0], len(script.data))
    assert "if not is_success(roll_vs_skill(0, SPEECH, -20)) goto 0x00100" in lines[0]
    assert lines[1].endswith("giq_option(6, 0, 133, talk, 50)")


@pytest.mark.skipif(not (intdump.SCRIPTS_DIR / "MASTER.INT").exists(), reason="scripts not extracted")
@needs_knowledge
def test_the_masters_sterility_line_needs_vrees_disk_or_her_word() -> None:
    text = intdump.dump("master", ["master09"])
    assert "obj_carrying_pid_obj(dude_obj(), 194) or global_var(DESTROY_MASTER_6)" in text
    assert "giq_option(7, 51, 133 'I happen to know that your mutants are sterile.', master11, 50)" in text
