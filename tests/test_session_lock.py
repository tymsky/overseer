"""One driver of the game at a time (routes, quest commands, other drivers), and the address table's names."""

import json
import os

import pytest

from f1 import engine_map, session


@pytest.fixture
def lock(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "driver.lock"
    monkeypatch.setattr(session, "DRIVER_LOCK", path)
    return path


def test_the_lock_is_held_while_driving_and_gone_after(lock) -> None:
    with session.driver_lock("routes start"):
        assert json.loads(lock.read_text())["pid"] == os.getpid()
    assert not lock.exists()


def test_a_second_driver_is_refused_while_the_first_lives(lock, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.write_text(json.dumps({"pid": 999999, "what": "routes idealist_start"}))
    monkeypatch.setattr(session, "python_alive", lambda pid: pid == 999999)
    with pytest.raises(session.SessionError, match="another driver"), session.driver_lock("quests hunt"):
        pass
    assert json.loads(lock.read_text())["pid"] == 999999  # the first one's lock stays


def test_a_dead_drivers_lock_is_taken_over(lock, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.write_text(json.dumps({"pid": 999999, "what": "routes idealist_start"}))
    monkeypatch.setattr(session, "python_alive", lambda pid: False)
    with session.driver_lock("routes start"):
        assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_every_global_has_one_name() -> None:
    names = [g.name for g in engine_map.GLOBAL_LIST]
    assert len(names) == len(set(names)) == len(engine_map.GLOBALS)
