"""Shared for every test: module-level state the live code keeps is reset around each test, and a marker for the
tests that read the local knowledge base (extracted/f1/knowledge.json, built by `python -m f1.knowledge build`)."""

import pytest

from f1 import knowledge, loot, watchdog

needs_knowledge = pytest.mark.skipif(
    not knowledge.DB_PATH.exists(), reason="no knowledge base (python -m f1.knowledge build)"
)


@pytest.fixture(autouse=True)
def _no_live_game(monkeypatch: pytest.MonkeyPatch):
    """No test may start, stop or click the real game. A fake actor whose pid looked dead once made the runner call
    session.start, and a failing test run launched the instance. Tests that want these
    patch them over."""
    from f1 import session

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the live game (session.start/stop/press/click)")

    for name in ("start", "stop", "press", "press_keys", "type_text", "click", "move", "drag"):
        monkeypatch.setattr(session, name, refuse)


@pytest.fixture(autouse=True)
def _fresh_module_state(monkeypatch, tmp_path):
    """A trip or a find given up on in one test must not leak into the next (both are module globals); nor what live
    runs dropped (runs/dropped.json)."""
    watchdog.clear()
    loot.forget()
    monkeypatch.setattr(loot, "_dropped", set())
    monkeypatch.setattr(loot, "DROPPED_FILE", tmp_path / "dropped.json")
    yield
    watchdog.clear()
    loot.forget()
