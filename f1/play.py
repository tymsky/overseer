"""From nothing to a playing bot: set up the bot's copy of the game, then play a character's routes in order.

    python -m f1.play setup [--game DIR] [--size WxH]
        build the instance from the Steam install (f1/steam.py finds it) and the knowledge from its files
    python -m f1.play [idealist|agent] [--until N] [--clock K]
        the character's start save, made once (the character entered in the game's editor, the bot's preferences,
        a save on the first map, kept as saves/<name>-start); then a new session from it and the character's routes
        one after another, unattended (f1/routes.py). The Idealist plays the whole game; the Agent the first quests.
        --until N stops before step N of the chain, --clock K runs a test at K times the game's speed

Keep off the mouse and keyboard while it plays (the presence guard pauses the bot otherwise), and keep the PC
unlocked and awake.
"""

import argparse
import datetime
import sys

from f1 import chargen, flows, instance, knowledge, paths, routes, session
from f1.actions import Actor
from f1.telemetry import EventLog

# Each character's routes, in the order they play (f1/routes.py, f1/idealist.py and the late-game modules).
CHAINS = {
    "idealist": (
        "idealist_start",
        "idealist_junktown",
        "idealist_hub",
        "idealist_necropolis",
        "idealist_vault13",
        "idealist_brotherhood",
        "idealist_glow",
        "idealist_bos",
        "idealist_hub2",
        "idealist_boneyard",
        "idealist_military",
        "idealist_raiders",
        "idealist_watershed",
        "idealist_cathedral",
    ),
    "agent": ("start", "water_chip", "junktown", "tandi"),
}


def setup(game: str | None, size: tuple[int, int] | None) -> int:
    if instance.MANIFEST.exists():
        print(f"instance at {instance.INSTANCE_DIR} (python -m f1.instance build --force rebuilds it)")
    else:
        m = instance.build(False, game, size)
        print(f"instance built from {m['steam_dir']} at {instance.INSTANCE_DIR}")
    if problems := instance.check():
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"the game's resolution: {'x'.join(map(str, instance.configured_size()))}")
    return knowledge.main(["build"])


def start_save(build: chargen.Build) -> str:
    """The kept save `<name>-start`: made now when there is none (a new game with the character, the bot's
    preferences, a save in slot 1 on the first map), else the one kept before."""
    name = f"{build.name.lower()}-start"
    if (flows.GOLDEN / name / "SLOT01" / "SAVE.DAT").exists():
        return name
    pid = session.game_pid() or session.start()["pid"]
    print(f"entering the {build.name} in the game's editor ...")
    chargen.create(pid, build)
    prefs = flows.set_preferences(pid, flows.PREFS.get(build.name, flows.AGENT_PREFS))
    if not prefs["ok"]:
        raise flows.FlowError(f"the preferences did not take: {prefs}")
    actor = Actor(pid, EventLog(paths.RUNS / "sessions.jsonl"))
    try:
        saved = actor.quicksave(1, f"{build.name} start")
    finally:
        actor.close()
    if not saved.ok:
        raise flows.FlowError(f"the start save failed: {saved}")
    kept = flows.keep_save(name)
    print(f"kept the start save as {kept.parent}")
    return name


def play(character: str, until: int | None, speed: float | None) -> int:
    build = chargen.BUILDS[character]
    steps = [step for name in CHAINS[character] for step in routes.all_routes()[name]]
    steps = steps[: until - 1] if until else steps
    run_dir = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-play-{character}"
    with session.driver_lock(f"play {character}"):
        name = start_save(build)
        snap = flows.start_from(name)
        if snap.screen != "map":
            print(f"the start save did not load: {snap.screen}", file=sys.stderr)
            return 1
        return routes._drive(steps, 1, speed, run_dir)


def main(argv: list[str]) -> int:
    if argv[:1] == ["setup"]:
        ap = argparse.ArgumentParser(prog="python -m f1.play setup")
        ap.add_argument("--game", help="the game's folder (default: found through Steam)")
        ap.add_argument("--size", type=instance.parse_size, help="the game's resolution WxH")
        args = ap.parse_args(argv[1:])
        try:
            return setup(args.game, args.size)
        except instance.InstanceError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    ap = argparse.ArgumentParser(prog="python -m f1.play", description=__doc__.split("\n\n")[0])
    ap.add_argument("character", nargs="?", default="idealist", choices=sorted(CHAINS))
    ap.add_argument("--until", type=int, help="stop before step N of the chain")
    ap.add_argument("--clock", type=float, help="a test run at K times the game's speed")
    args = ap.parse_args(argv)
    return play(args.character, args.until, args.clock)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
