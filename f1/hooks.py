"""Session hooks: other packages add work to a session without the bot knowing them.

A hook is an object, usually a module, registered under the entry-point group `overseer.session` in its package's
pyproject.toml:

    [project.entry-points."overseer.session"]
    recorder = "mypackage.record"

Each function is optional:

    start(env: dict) -> dict          once the game window is up; its fields join the session's start event
    status() -> dict                  its fields join the status event
    checks(start_event: dict) -> list[tuple[str, bool, str]]
                                      after the game has exited (f1.smoke): steps (name, ok, detail) of the hook's own

The bot alone has no hooks; a recorder registered this way, for instance, records every session.
"""

from importlib.metadata import entry_points

GROUP = "overseer.session"


def load() -> dict[str, object]:
    """The registered hooks by name; a hook that fails to import raises (fail fast, no silent skips)."""
    return {ep.name: ep.load() for ep in sorted(entry_points(group=GROUP), key=lambda ep: ep.name)}


def call(fn: str, *args) -> dict:
    """Call `fn` on every hook that has it; their dict results merged in name order."""
    out: dict = {}
    for hook in load().values():
        if f := getattr(hook, fn, None):
            out |= f(*args) or {}
    return out


def checks(start_event: dict) -> list[tuple[str, bool, str]]:
    return [step for hook in load().values() if (f := getattr(hook, "checks", None)) for step in f(start_event)]
