"""One driver of the game at a time (routes, quest commands, other drivers), and the address table's names."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

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


def test_nested_calls_keep_the_outer_lock_and_its_metadata(lock) -> None:
    with session.driver_lock("outer"):
        mine = json.loads(lock.read_text())
        with session.driver_lock("inner"):
            assert json.loads(lock.read_text()) == mine
        assert json.loads(lock.read_text()) == mine
        assert lock in session._driver_owners
    assert not lock.exists()


def test_another_thread_cannot_drive_the_same_game(lock) -> None:
    def compete():
        with pytest.raises(session.SessionError, match="outer"), session.driver_lock("other thread"):
            pass

    with session.driver_lock("outer"), ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(compete).result(timeout=5)
        assert json.loads(lock.read_text())["what"] == "outer"


@pytest.mark.parametrize("missing_metadata", [False, True])
def test_another_process_is_refused_even_without_metadata(lock, missing_metadata: bool) -> None:
    code = """
import sys
from pathlib import Path
from f1 import session
session.DRIVER_LOCK = Path(sys.argv[1])
try:
    with session.driver_lock("other process"):
        pass
except session.SessionError as error:
    print(error)
    sys.exit(2)
"""
    with session.driver_lock("outer"):
        if missing_metadata:
            lock.unlink()
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(lock)],
            env=os.environ | {"OVERSEER_HOME": str(lock.parent)},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2, result.stderr
        assert "another driver" in result.stdout
        assert ("metadata unavailable" if missing_metadata else "outer") in result.stdout


def test_a_dead_drivers_lock_is_taken_over(lock, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.write_text(json.dumps({"pid": 999999, "what": "routes idealist_start"}))
    monkeypatch.setattr(session, "python_alive", lambda pid: False)
    with session.driver_lock("routes start"):
        assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_a_reused_python_pid_does_not_keep_a_stale_lock_held(lock, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.write_text(json.dumps({"pid": 999999, "what": "old driver"}))
    monkeypatch.setattr(session, "python_alive", lambda pid: True)
    with session.driver_lock("new driver"):
        assert json.loads(lock.read_text())["what"] == "new driver"


@contextmanager
def holder_process(lock):
    ready = lock.with_name("ready")
    code = """
import os
import sys
from pathlib import Path
from f1 import session
session.DRIVER_LOCK = Path(sys.argv[1])
with session.driver_lock("holder"):
    Path(sys.argv[2]).write_text("ready")
    sys.stdin.read(1)
    os._exit(0)
"""
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(lock), str(ready)],
        env=os.environ | {"OVERSEER_HOME": str(lock.parent)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "the child did not acquire its test lock"
        yield proc
    finally:
        try:
            proc.communicate(input="\n" if proc.poll() is None else None, timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)


def test_an_abrupt_process_exit_releases_the_guard_without_removing_metadata(lock) -> None:
    with holder_process(lock) as proc:
        assert json.loads(lock.read_text())["what"] == "holder"
        proc.communicate(input="\n", timeout=5)
        assert proc.returncode == 0
        assert lock.exists()
        with session.driver_lock("new driver"):
            assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_an_exception_releases_the_lock(lock) -> None:
    with pytest.raises(RuntimeError, match="failed"), session.driver_lock("first"):
        raise RuntimeError("failed")
    with session.driver_lock("second"):
        assert json.loads(lock.read_text())["what"] == "second"


def test_every_global_has_one_name() -> None:
    names = [g.name for g in engine_map.GLOBAL_LIST]
    assert len(names) == len(set(names)) == len(engine_map.GLOBALS)
