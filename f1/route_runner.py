"""Route execution, checkpoint preparation and failure recovery."""

import contextlib
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from f1 import barter, chargen, clock, dialogue, flows, knowledge, loot, risk, session, state, watchdog, win32
from f1.actions import Actor
from f1.engine_map import Obj
from f1.memory import ReadError
from f1.objectives import ObjectiveOutcome, identified_steps, outcome, route_manifest
from f1.route_support import (
    Step,
    heal_up,
    hour,
    settle,
)
from f1.telemetry import EventLog

UNARMED_MAPS = ("CHILDRN", "MSTRLR", "MBENT", "MBSTRG", "MBVATS")  # a weapon in a hand slot turns their people hostile

IRONMAN_STUCK_S = 60  # an ironman run is stuck after a minute without progress on any screen (the owner's rule)
TOWN_SWEEP = 20  # hexes round the Agent that a checkpoint's loot sweep covers in a town

# towns whose guards walk up to a drawn gun: the talk blocks the save (Junktown's gate, a full run, twice 27 s)
TOWN_MAPS = (
    "SHADYW", "SHADYE", "JUNKENT", "JUNKKILL", "JUNKCSNO", "VAULT13", "HUBENT", "HUBDWNTN", "HUBHEIGT", "HUBOLDTN",
    "HUBWATER", "BROHDENT", "BROHD12", "BROHD34", "LAADYTUM", "LABLADES", "LAFOLLWR", "LAGUNRUN", "CHILDEAD",
)  # fmt: skip


def loadout(actor: Actor) -> tuple:
    """The kinds of weapon and ammunition carried: when they change, the best weapon may be another (the rounds a
    fight spends do not count: a readying at every checkpoint drew the gun in Junktown)."""
    kinds = ("weapon", "ammo")
    return tuple(
        sorted({p for _, p, q in actor.items() if q > 0 and (knowledge.proto(p) or {}).get("item_type") in kinds})
    )


def prepare(actor: Actor, carried: list) -> None:
    """At the route's start, after each reload and at each checkpoint (so that a reload comes back to it): HP made up
    by rest or stimpaks below 75 %, and the best weapon taken up when the weapons or ammunition carried changed,
    then holstered (a fight draws it; guards talk to a drawn gun). A full run went into Vault 12 with a knife, two
    boxes of 10mm in the pack and HP 22 of 35: the Overseer's ammunition came after the last readying, and nothing
    healed after the sewers. Not where a weapon in a hand slot makes enemies (UNARMED_MAPS)."""
    if not hasattr(actor, "mem"):
        return
    s = actor.snap()
    if s.screen != "map" or s.dude is None or actor.in_combat():
        return
    # what lies about free for the taking, before the save so that the checkpoint keeps it. Not on the base's and
    # the Cathedral's floors (no place to roam), and in a town only near and by day: people keep their houses at night
    # (Killian's intruder talk from 19:00 spoiled Kenji's coming, a full run at Full HD), and walking into places can
    # start what a route does later.
    if not any(m in s.map_name for m in UNARMED_MAPS):
        in_town = any(m in s.map_name for m in TOWN_MAPS)
        if not in_town:
            loot.sweep(actor)
        elif 7 <= hour(actor) < 19:
            loot.sweep(actor, max_hexes=TOWN_SWEEP)
        s = actor.snap()
    heal_up(actor, 0.75)
    now = loadout(actor)
    if any(m in s.map_name for m in UNARMED_MAPS + TOWN_MAPS):
        # put away (a fight drew it: after Kenji a townsman came to the gun, and his talk blocked the next steps'
        # inventory, a full run); readied at the next checkpoint out of town
        if actor.weapon() is not None:
            actor.holster(True)
        return
    if now != (carried[0] if carried else None):
        if actor.ready_weapon().ok:
            actor.holster(True)
        carried[:] = [now]


def ensure_preferences(actor: Actor, prefs: dict[str, int] | str | None) -> None:
    """A save brings back its own preferences (options.c load_options): set ours when they differ (flows.BY_CHARACTER:
    those of the character in the game)."""
    if prefs == flows.BY_CHARACTER:
        prefs = flows.prefs_for(actor.mem)
    if prefs and any(actor.mem.glob(name) != value for name, value in prefs.items()):
        actor.log.emit("preferences", **flows.set_preferences(actor.pid, prefs))


# a step that raised one of these failed: the checkpoint takes over (OwnerActive is not caught: it stops the run)
# Fail fast: only the game's own troubles fail a step: memory gone mid-read, a flow or session that would not
# go, the watchdog's hang. A KeyError, IndexError, AttributeError, ValueError or TypeError is a bug in our code: it is
# not caught, the run stops at once with its traceback and the game's state (main).
STEP_ERRORS = (
    ReadError,
    flows.FlowError,
    session.SessionError,
    watchdog.Stuck,
)


def death_screen(s: state.Snapshot) -> bool:
    """The death picture: a full-screen window over a game already reset (level 1, no XP, the player's HP back to
    the start's). It stays some seconds before the main menu: the Mother Deathclaw's kill ended a step on it, with HP
    30 read, and the runner took it for a plain failure and gave up instead of loading."""
    return s.screen.startswith("window") and s.level == 1 and getattr(s, "experience", None) == 0


def try_step(actor: Actor, step: Step, number: int) -> tuple[ObjectiveOutcome, bool, state.Snapshot | None, bool]:
    """One try of step `number` (1-based), logged and timed: (its own verdict, whether the player died, the game
    after it, whether the game got stuck: the watchdog saw nothing move, or the game is gone, then with no
    snapshot). A step that crashed failed."""
    log = actor.log
    log.emit("objective", text=step.name, step=number, objective_id=step.objective_id)
    before = getattr(log, "last_failed", None)
    t0 = time.monotonic()
    watchdog.clear()
    stuck = False
    try:
        result = outcome(step.run(actor))
        if result and step.verify is not None and not step.verify(actor):
            result = ObjectiveOutcome.recoverable("objective postcondition not verified")
    except STEP_ERRORS as e:
        where = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_tb(e.__traceback__)]
        log.emit("step_error", step=step.name, error=repr(e)[:300], where=where[-6:])
        result, stuck = ObjectiveOutcome("technical_error", repr(e)[:300]), isinstance(e, watchdog.Stuck)
    ok = bool(result)
    watchdog.clear()  # a trip after the step's last look belongs to no one now
    if not actor.game_alive():  # a crash, or the game closed: a stuck game, restarted by the reload
        log.emit("step_end", step=step.name, ok=False, seconds=round(time.monotonic() - t0, 1), crashed=True)
        return ObjectiveOutcome("technical_error", "game exited during objective"), False, None, True
    if not ok and getattr(actor, "mem", None) is not None and dialogue.read(actor.mem).active:
        # a failed talk left open would meet the next step: Curtis's, then the bookcase's loot took the talk for a
        # shop's barter and left the Scout Handbook and the rope behind (an ironman run, Vault 15 without a rope)
        from f1 import quests

        log.emit("talk_left_open", step=step.name, closed=quests.close_dialogue(actor))
    if not ok and getattr(actor, "mem", None) is not None and barter.screen_open(actor.mem):
        # a talk that ended in a barter screen (Razlo by night: his healing unpaid) kept the Pip-Boy from opening
        # for the retry's rest: back to the talk (T), then out of it
        from f1 import quests

        session.press("t")
        win32.wait_for(lambda: not barter.screen_open(actor.mem), 3, 0.1)
        log.emit("barter_left_open", step=step.name, closed=quests.close_dialogue(actor))
    s = actor.snap()
    died = not (ok and step.final) and (s.screen == "main_menu" or s.dude is None or s.dude.hp <= 0 or death_screen(s))
    rads = actor.mem.i32(s.dude.address + Obj.CRITTER_RADIATION) if s.dude and hasattr(actor, "mem") else None
    # a step's own verdict often says nothing ("recoverable", ""): the last action that failed in this try goes
    # with it (a miss the step got past before it failed otherwise is named too: evidence, not the verdict)
    failed = getattr(log, "last_failed", None)
    actor.failed_action = failed[1] if not ok and failed is not None and failed != before else None
    log.emit(
        "step_end", step=step.name, ok=ok and not died, seconds=round(time.monotonic() - t0, 1),
        hp=s.dude.hp if s.dude else None, rads=rads, objective_id=step.objective_id,
        status=result.status, reason=result.reason,
        **({"failed_action": actor.failed_action} if actor.failed_action else {}),
    )  # fmt: skip
    return result, died, s, stuck


def read_error(result: ObjectiveOutcome) -> bool:
    """A try that ended on a ReadError with the game alive (try_step's reason is the error's repr): a read the game
    raced, as the map's object lists rewired while the game moves objects, not a game that is gone."""
    return result.status == "technical_error" and result.reason.startswith(f"{ReadError.__name__}(")


def failure_state(actor: Actor, step: Step, s: state.Snapshot) -> tuple:
    """Observe relevant resources and quest progress, rather than only the player's position."""
    inventory = (
        tuple(sorted((pid, quantity) for _address, pid, quantity in actor.items())) if hasattr(actor, "items") else ()
    )
    extra = step.progress(actor) if step.progress else ()
    return (
        s.screen,
        s.map_name,
        s.elevation,
        s.dude.tile if s.dude else None,
        s.dude.hp if s.dude else None,
        inventory,
        extra,
    )


def inferred_progress(actor: Actor, steps: list[Step], start: int, progress: dict | None = None) -> tuple[dict, dict]:
    """A loaded segment reconstructs only facts backed by its current game predicates."""
    statuses = dict((progress or {}).get("statuses", {}))
    pending = {key: dict(value) for key, value in (progress or {}).get("pending", {}).items()}
    for step in steps[: start - 1]:
        predicate = step.satisfied or step.verify
        if predicate is None:
            continue
        achieved = predicate(actor)
        statuses[step.objective_id] = "achieved" if achieved else "deferred" if step.obligation else "unavailable"
        if achieved:
            for key in (step.objective_id, *step.resolves):
                pending.pop(key, None)
        elif step.obligation:
            pending[step.obligation] = {
                "objective_id": step.objective_id,
                "step": step.name,
                "required": pending.get(step.obligation, {}).get("required", False) or not step.optional,
                "reason": "unfulfilled loaded-game predicate",
            }
    return statuses, pending


def sync_obligations(actor: Actor, pending: dict) -> None:
    actor.caves_deferred = "sands.caves" in pending
    actor.tandi_later = "sands.tandi" in pending
    actor.antidote_later = any(key in pending for key in ("sands.antidote", "sands.jarvis", "sands.tail"))


def checkpoint(
    actor: Actor,
    number: int,
    level: int,
    leveled: int,
    level_up: Callable[[int], dict] | None,
    carried: list,
    save: bool = True,
) -> tuple[bool, int]:
    """After checkpoint step `number`: a talk left open settled (someone walked up; it blocks the character screen
    and the save), the level-up spent when the level rose past `leveled`, prepare, then the quicksave to slot 1 (none
    without `save`: an ironman run). (saved, the level spent up to). Raises watchdog.Stuck when its own screens
    hang."""
    settle(actor)
    if level_up is not None and level > leveled and not actor.in_combat():
        try:
            spent = level_up(actor.pid)
        except (chargen.ChargenError, ReadError, session.SessionError) as e:  # never stops the route
            spent = {"ok": False, "why": repr(e)[:200]}
        actor.log.emit("level_up", level=level, **spent)
        leveled = level if spent.get("ok") else leveled
    prepare(actor, carried)
    if not save:
        return False, leveled
    name = f"route {number}"
    saved = actor.quicksave(1, name).ok or (settle(actor) and actor.quicksave(1, name).ok)
    return saved, leveled


def reload_checkpoint(actor: Actor, prefs: dict[str, int] | str | None, carried: list) -> bool:
    """Slot 1 loaded from the main menu (three tries: once the load came back to the world map after 60 s),
    the actor attached to the new game, the preferences and prepare again. False: it would not load."""
    log = actor.log
    actor.close()
    loot.forget()  # the load puts the finds back
    if not actor.game_alive():  # a crash: the game again, then the checkpoint
        try:
            actor.pid = session.start()["pid"]
        except session.SessionError as e:
            log.emit("restart_failed", error=repr(e)[:300])
            return False
        watchdog.watch(actor.pid)
        log.emit("restarted", pid=actor.pid)
    for attempt in range(3):
        try:
            flows.load_from_menu(actor.pid, 1)
            break
        except (flows.FlowError, session.SessionError) as e:
            log.emit("load_failed", attempt=attempt + 1, error=repr(e)[:200])
    else:
        return False
    actor.__init__(actor.pid, log)
    carried.clear()  # the reloaded game carries what the checkpoint did
    try:
        ensure_preferences(actor, prefs)
        prepare(actor, carried)
    except (
        watchdog.Stuck,
        ReadError,
    ) as e:  # the next step starts from wherever this left the game (a crash: the next step's own check sees it)
        log.emit("step_error", step="after the reload", error=repr(e)[:300])
    return True


def run(
    actor: Actor,
    steps: list[Step],
    start: int = 1,
    level_up: Callable[[int], dict] | None = None,
    prefs: dict[str, int] | str | None = None,
    exploits: bool | None = None,
    on_step: Callable[[int], None] | None = None,
    ironman: bool = False,
    progress: dict | None = None,
) -> dict:
    """Run steps from `start` (1-based). `on_step(i)` is told each step's index (0-based) before it runs, with
    actor.objective_progress current (the ledger, `leveled`, `fails`, `skipped`): another driver can take over before
    that step in the same game with it as `progress`. Starting mid-way assumes slot 1 holds the checkpoint before it. With
    `level_up`, a checkpoint first spends a level-up when the level rose (the game adds the skill points only when
    the character screen opens, so the level is what tells). With `prefs`, the game's preferences are made so at the
    start and after every reload.

    No reloading for luck: a step that fails is tried again where the game stands, never loaded for (a roll's outcome
    stands); past its tries an optional step is skipped and a required one ends the route. Only a death or a stuck
    game loads the last checkpoint.

    An ironman run (`ironman`) never saves or loads: a checkpoint only spends the level-up and prepares, and a
    death, a stuck game (the watchdog, a crash) or a required step past its tries ends the route; the result's
    `ended` says which ("death", "stuck", "failed"; "done" when it got through).

    A step marked `exploit` runs only when `exploits` is true; None takes it from the build of the character in the
    game (chargen.Build.exploits, off by default)."""
    steps = identified_steps(steps)
    actor.ironman = ironman
    actor.ending_audit = None
    carry = progress or {}  # a driver taking over at a step boundary: the counters it carries on with
    log, i, loads, leveled = actor.log, start - 1, {}, int(carry.get("leveled", 0))
    fails: dict[str, int] = dict(carry.get("fails", {}))
    skipped: list[str] = list(carry.get("skipped", []))
    last_failure: dict[str, tuple] = {}  # where the game stood after a step's last failure (fail fast)
    read_retried: set[str] = set(carry.get("read_retried", []))  # objectives tried again after a ReadError
    actor.objective_progress = None
    statuses, pending = inferred_progress(actor, steps, start, progress)
    actor.pending_objectives = pending
    sync_obligations(actor, pending)
    actor.objective_progress = {"statuses": statuses, "pending": pending}
    last_checkpoint = max((k for k in range(start - 1) if steps[k].checkpoint), default=-1)
    checkpoint_progress = {last_checkpoint: (dict(statuses), {k: dict(v) for k, v in pending.items()})}
    if exploits is None:  # the character's build decides (off for a name that is not ours, or a fake game)
        mem = getattr(actor, "mem", None)
        build = chargen.build_of(state.character_name(mem)) if mem is not None else None
        exploits = bool(build and build.exploits)
    manifest = route_manifest(steps)
    log.emit(
        "route_start",
        steps=len(steps),
        exploits=exploits,
        ironman=ironman,
        objectives=manifest["objectives"],
        route_hash=manifest["route_hash"],
    )
    carried: list = []  # the loadout the weapon was last readied for
    try:
        ensure_preferences(actor, prefs)
        prepare(actor, carried)
    except (
        watchdog.Stuck,
        ReadError,
    ) as e:  # the first step starts from wherever this left the game (a crash: the next step's own check sees it)
        log.emit("step_error", step="the start", error=repr(e)[:300])
    while i < len(steps):
        step = steps[i]
        if step.exploit and not exploits:  # not for this character
            log.emit("skipped", step=step.name, why="exploit")
            skipped.append(step.name)
            i += 1
            continue
        if on_step:
            if actor.objective_progress is not None:  # what a driver taking over before this step carries on with
                actor.objective_progress.update(
                    leveled=leveled, fails=fails, skipped=skipped, read_retried=sorted(read_retried)
                )
            on_step(i)
        missing = [key for key in step.requires if statuses.get(key) != "achieved"]
        if missing:
            why = "unfulfilled dependencies: " + ", ".join(missing)
            result = (
                ObjectiveOutcome.deferred(why)
                if any(statuses.get(k) == "deferred" for k in missing)
                else ObjectiveOutcome.unavailable(why)
            )
            s, died, stuck = actor.snap(), False, False
            log.emit("objective", text=step.name, step=i + 1, objective_id=step.objective_id)
            log.emit(
                "step_end",
                step=step.name,
                ok=False,
                status=result.status,
                reason=why,
                objective_id=step.objective_id,
                seconds=0,
            )
        else:
            result, died, s, stuck = try_step(actor, step, i + 1)
        ok = bool(result)
        statuses[step.objective_id] = "death" if died else result.status
        if not died and not stuck and result.status == "technical_error":
            if read_error(result) and step.tries > 1 and not step.final and step.objective_id not in read_retried:
                # memory gone mid-read fails the try, not the run: once more where the game stands, once per
                # objective (names repeat on a route); a second one ends it. Not a step of one try (its roll's first
                # outcome stands, and the read may have come after it), nor the final one (the ending plays on).
                read_retried.add(step.objective_id)
                log.emit("retry", step=step.name, tries=fails.get(step.name, 0) + 1, why=result.reason)
                continue
            fields = {
                "ok": False,
                "at": step.name,
                "why": result.reason,
                "loads": loads,
                "fails": fails,
                "skipped": skipped,
                "ended": "error",
                "step": i + 1,
                "objective_id": step.objective_id,
                "status": "technical_error",
            }
            log.emit("route_end", **fields)
            return fields
        if not died and not stuck and result.status in {"deferred", "unavailable"}:
            if result.status == "deferred":
                key = result.obligation or step.obligation or step.objective_id
                pending[key] = {
                    "objective_id": step.objective_id,
                    "step": step.name,
                    "required": pending.get(key, {}).get("required", False) or not step.optional,
                    "reason": result.reason,
                }
                log.emit("deferred", step=step.name, objective_id=step.objective_id, obligation=key, why=result.reason)
                sync_obligations(actor, pending)
                i += 1
                continue
            if step.optional:
                log.emit(
                    "skipped", step=step.name, objective_id=step.objective_id, why=result.reason, status="unavailable"
                )
                skipped.append(step.name)
                i += 1
                continue
            fields = {
                "ok": False,
                "at": step.name,
                "why": result.reason,
                "loads": loads,
                "fails": fails,
                "skipped": skipped,
                "ended": "failed",
                "step": i + 1,
                "objective_id": step.objective_id,
                "status": "unavailable",
            }
            log.emit("route_end", **fields)
            return fields
        if ok and not died:
            verified_resolves = step.resolves if step.verify is None or step.verify(actor) else ()
            for key in (step.objective_id, *verified_resolves, *result.resolves):
                if key in pending:
                    log.emit("obligation_resolved", obligation=key, objective_id=step.objective_id)
                    del pending[key]
            sync_obligations(actor, pending)
        if ok and not died and step.checkpoint:
            try:
                saved, leveled = checkpoint(actor, i + 1, s.level, leveled, level_up, carried, save=not ironman)
            except (watchdog.Stuck, ReadError) as e:  # its screens hung, or the game crashed under it: back to the last
                # one (the Hub's farm: the game's process ended during the checkpoint's loot sweep, and the
                # ReadError stopped the route instead of the restart a crash in a step gets)
                if isinstance(e, ReadError) and actor.game_alive():
                    raise
                log.emit("step_error", step=step.name, error=repr(e)[:300])
                ok, stuck = False, True
            else:
                if saved:  # an unsaved checkpoint must not become the place a reload resumes from
                    last_checkpoint = i
                    checkpoint_progress[i] = (dict(statuses), {k: dict(v) for k, v in pending.items()})
                    log.emit("objective_checkpoint", step=i + 1, statuses=dict(statuses), pending=dict(pending))
                elif not ironman:
                    log.emit("checkpoint_failed", step=step.name)
        if ok and not died:
            i += 1
            continue
        if not died and not stuck:  # the failure stands; tried again in place, never loaded for
            fails[step.name] = fails.get(step.name, 0) + 1
            where = failure_state(actor, step, s)
            if where == last_failure.get(step.name) and not step.optional:  # fail fast: again, and nothing changed
                why = "no progress: failed again and the game did not change"
                log.emit("route_end", ok=False, at=step.name, why=why, loads=loads, fails=fails, skipped=skipped,
                         ended="failed", objective_id=step.objective_id, status=result.status,
                         failed_action=getattr(actor, "failed_action", None))  # fmt: skip
                return {"ok": False, "at": step.name, "why": why, "loads": loads, "fails": fails, "skipped": skipped,
                        "ended": "failed", "step": i + 1, "objective_id": step.objective_id}  # fmt: skip
            last_failure[step.name] = where
            if fails[step.name] < step.tries:
                log.emit("retry", step=step.name, tries=fails[step.name])
                continue
            if step.optional:
                log.emit("skipped", step=step.name, tries=fails[step.name])
                skipped.append(step.name)
                i += 1
                continue
            log.emit(
                "route_end",
                ok=False,
                at=step.name,
                loads=loads,
                fails=fails,
                skipped=skipped,
                ended="failed",
                objective_id=step.objective_id,
                status=result.status,
                failed_action=getattr(actor, "failed_action", None),
            )
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped,
                    "ended": "failed", "step": i + 1, "objective_id": step.objective_id}  # fmt: skip
        if ironman:  # no load: the run ends here
            ended = "death" if died else "stuck"
            hp = s.dude.hp if s and s.dude else None
            log.emit(
                "route_end",
                ok=False,
                at=step.name,
                ended=ended,
                step=i + 1,
                hp=hp,
                fails=fails,
                skipped=skipped,
                objective_id=step.objective_id,
                status=result.status,
            )
            return {
                "ok": False,
                "at": step.name,
                "ended": ended,
                "step": i + 1,
                "fails": fails,
                "skipped": skipped,
                "objective_id": step.objective_id,
            }
        failed = steps[last_checkpoint + 1] if last_checkpoint + 1 < len(steps) else step
        loads[failed.name] = loads.get(failed.name, 0) + 1
        if loads[failed.name] > failed.tries:
            log.emit("route_end", ok=False, at=step.name, loads=loads, fails=fails, skipped=skipped)
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped}
        log.emit("recover", why="died" if died else f"stuck: {step.name}", loads=loads[failed.name])
        if not reload_checkpoint(actor, prefs, carried):
            log.emit("route_end", ok=False, at=step.name, loads=loads, why="the checkpoint would not load")
            return {"ok": False, "at": step.name, "loads": loads, "fails": fails, "skipped": skipped}
        restored_statuses, restored_pending = checkpoint_progress[last_checkpoint]
        statuses.clear()
        statuses.update(restored_statuses)
        pending.clear()
        pending.update({k: dict(v) for k, v in restored_pending.items()})
        actor.pending_objectives = pending
        sync_obligations(actor, pending)
        actor.objective_progress = {"statuses": statuses, "pending": pending}
        i = last_checkpoint + 1
    unresolved = {key: value for key, value in pending.items() if value["required"]}
    result = {
        "ok": not unresolved,
        "loads": loads,
        "fails": fails,
        "skipped": skipped,
        "ended": "failed" if unresolved else "done",
    }
    if pending:
        result["pending"] = pending
    if ironman:
        audit = getattr(actor, "ending_audit", None)
        result.update(
            best_ending=bool(audit and audit["ok"] and audit.get("ending_seen") is True and not unresolved),
            goal_audit=audit,
        )
        result["progression"] = {"statuses": dict(statuses), "pending": {k: dict(v) for k, v in pending.items()}}
    log.emit("route_end", **result)
    return result


def fail_fast(actor: Actor, error: BaseException) -> None:
    """Fail fast: the error, its traceback and the game as it stood, in the run's events."""
    import traceback

    try:
        s = actor.snap()
        where = {"screen": s.screen, "map": s.map_name, "tile": s.dude.tile if s.dude else None}
    except (ReadError, watchdog.Stuck, OSError) as e:  # the game may be what failed
        where = {"unreadable": repr(e)[:200]}
    actor.log.emit(
        "route_error", error=repr(error)[:300], traceback=traceback.format_exc()[-3000:], **where
    )  # fmt: skip


def _drive(
    steps: list[Step],
    start: int,
    speed: float | None,
    run_dir: Path,
    on_step: Callable[[Actor, int], None] | None = None,
    ironman: bool = False,
    out: dict | None = None,
    prefs: dict[str, int] | None = None,
    progress: dict | None = None,
) -> int:
    """Run `steps` from `start` on the running game, events to `run_dir`. `progress`: the objective ledger and the
    counters (actor.objective_progress) of a driver that stopped before step `start` in this same game."""
    actor = Actor(session.game_pid(), EventLog(run_dir / "events.jsonl"))
    if speed:  # a test run's waits at a faster clock, none at the end
        actor.log.emit("clock", **clock.set_fast(actor.pid, speed).__dict__)
    elif left := clock.reset(actor.pid):  # a killed test run left its speed in the game
        actor.log.emit("clock_reset", **left.__dict__)
    watchdog.set_cap(IRONMAN_STUCK_S if ironman else None)
    risk.set_bound(risk.IRONMAN_RISK_MAX if ironman else None)
    dog = watchdog.Watchdog(actor.pid, actor.log)
    dog.start()
    try:
        try:
            hook = (lambda i: on_step(actor, i)) if on_step else None
            prefs = prefs or (flows.IRONMAN_PREFS if ironman else flows.BY_CHARACTER)
            result = run(
                actor,
                steps,
                start,
                level_up=chargen.level_up,
                prefs=prefs,
                on_step=hook,
                ironman=ironman,
                progress=progress,
            )
        except Exception as e:  # fail fast: a bug stops the run at once; what it saw goes in the events first
            fail_fast(actor, e)
            raise
        if out is not None:  # the caller's copy of run()'s result (an ironman coordinator reads `ended`, `step`)
            out.update(result)
        print(result, f"(events: {run_dir / 'events.jsonl'})")
        return 0 if result["ok"] else 1
    finally:
        dog.stop()
        if speed:
            with contextlib.suppress(clock.ClockError):  # the game may be gone
                clock.set_fast(actor.pid, 1)
        actor.close()
