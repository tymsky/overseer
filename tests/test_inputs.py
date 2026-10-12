"""Background input (f1/dinput.py, f1/inputs.py) without the game: the shared memory's layout and the mode switch."""

import os
import struct

import pytest

from f1 import dinput, inputs, instance


def name() -> str:
    return f"Local\\GNWInputTest{os.getpid()}"


def test_feed_layout():
    f = dinput.Feed(create=True, name=name())
    try:
        assert f._get(0) == dinput.MAGIC and f._get(4) == 1
        f.nudge(5, -3)
        f.nudge(2, 1)
        assert (f._get(dinput.TOTAL_DX), f._get(dinput.TOTAL_DY)) == (7, -2)
        f.buttons(left=True)
        assert f._get(dinput.BUTTONS) == 1
        f.buttons(right=True)
        assert f._get(dinput.BUTTONS) == 2
        f.point(100, 200)
        assert (f._get(dinput.VIRT_X), f._get(dinput.VIRT_Y)) == (100, 200)
        assert f.key(0x1C, True, timeout_s=0.05) is False  # nobody takes it without the game
        assert f._get(dinput.KEY_HEAD) == 1
        assert f._get(dinput.RING) == 0x1C | 0x100
        assert f.devices == (0, 0)
        f._put(dinput.FLAGS, (6 << 8) | 6)
        assert f.devices == (6, 6)
        assert f.wait_reads(1, timeout_s=0.05) is False
    finally:
        f.close()


def test_open_keeps_counters():
    a = dinput.Feed(create=True, name=name())
    try:
        a.nudge(9, 9)
        b = dinput.Feed(name=name())  # a later command opens it by name
        assert b._get(dinput.TOTAL_DX) == 9
        c = dinput.Feed(create=True, name=name())  # created again while it exists: kept, not cleared
        assert c._get(dinput.TOTAL_DX) == 9
        b.close()
        c.close()
    finally:
        a.close()


def test_open_missing():
    with pytest.raises(dinput.FeedError):
        dinput.Feed(name=name() + "missing")


def test_mode_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "INSTANCE_DIR", tmp_path)
    built = tmp_path / "build" / "DINPUT.DLL"
    built.parent.mkdir()
    built.write_bytes(b"MZ")
    monkeypatch.setattr(instance, "PROXY_BUILT", built)
    monkeypatch.setattr(instance, "running_from_instance", list)
    (tmp_path / "f1_res.ini").write_bytes(b"[INPUT]\r\nALT_MOUSE_INPUT=1\r\n")
    assert not inputs.background()
    out = instance.set_input("proxy")
    assert inputs.background() and out["ALT_MOUSE_INPUT"] == "0"
    assert b"ALT_MOUSE_INPUT=0" in (tmp_path / "f1_res.ini").read_bytes()
    instance.set_input("direct")
    assert not inputs.background()
    assert b"ALT_MOUSE_INPUT=1" in (tmp_path / "f1_res.ini").read_bytes()


def test_extended_code():
    assert dinput.EXTENDED == 0x80 and struct.calcsize("<i") == 4


def test_proxy_without_zig(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "PROXY_BUILT", tmp_path / "build" / "DINPUT.DLL")
    monkeypatch.delenv("ZIG", raising=False)
    monkeypatch.setattr(instance.shutil, "which", lambda _name: None)
    with pytest.raises(instance.InstanceError, match="--input direct"):
        instance.ensure_proxy()
    with pytest.raises(instance.InstanceError, match="zig"):
        instance.build(False, input_mode="proxy")  # fails before anything is copied


def test_find_zig(monkeypatch):
    monkeypatch.setenv("ZIG", "C:/tools/zig/zig.exe")
    assert instance.find_zig("given.exe") == "given.exe"
    assert instance.find_zig() == "C:/tools/zig/zig.exe"


def test_proxy_built_once(tmp_path, monkeypatch):
    built = tmp_path / "DINPUT.DLL"
    built.write_bytes(b"MZ")
    monkeypatch.setattr(instance, "PROXY_BUILT", built)
    monkeypatch.setattr(instance, "build_proxy", lambda _zig=None: pytest.fail("built again"))
    assert instance.ensure_proxy() == built


def test_another_copy_is_found_by_its_exe_and_fed_through_its_own_memory(monkeypatch, tmp_path):
    from f1 import session, win32

    copy = tmp_path / "copy" / "FALLOUTW.EXE"
    asked = []
    monkeypatch.setattr(win32, "pids_of", lambda exe: asked.append(exe) or [42])
    monkeypatch.setattr(win32, "top_windows", lambda pid, visible_only=True: asked.append(visible_only) or [])
    monkeypatch.setattr(instance, "INSTANCE_DIR", tmp_path / "instance")  # no proxy there
    f = dinput.Feed(create=True, name=name())
    try:
        session.use_copy(copy, name(), headless=True)
        assert session.game_pid() == 42 and asked[-1] == str(copy)
        assert session.game_window(42) is None and asked[-1] is False  # a headless copy's windows are hidden
        assert inputs.background()
        inputs.key(0x1C, True)
        assert f._get(dinput.KEY_HEAD) == 1  # the copy's memory took the key
        session.use_copy(None)
        session.game_pid()
        assert asked[-1] == str(session.EXE_PATH) and not inputs.background()
    finally:
        session.use_copy(None)
        f.close()
