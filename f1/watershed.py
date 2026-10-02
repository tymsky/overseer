"""The Idealist's second Necropolis visit (planned from the scripts): Set's job, the six
Watershed super mutants one fight at a time (NECROP_MUTANTS_KILLED 2, karma +3, and +1 on the 24th bad kill), the
ghoul prisoner freed, Set's reward through Garret.

    python -m f1.routes idealist_watershed [--from N] [--until N] [--clock K]

No entry to HOTEL, HALLDED or WATRSHD from day 110 (NECROPOLIS_INVADED, the nar_15 slide), nor more than 29 days after
the kill: this visit is the last. Harry talked on the first visit and never talks again; the five others attack on
sight only after the first kill. Each fight starts from a hex where only its target notices the player (the plan's
static notice model): Larry, Terry, Harry, then Barry (his Flamer) with Gary and Sally. Garret pays the best lot only
after one daytime talk (his reaction is computed only then), and Set pays at night.
"""

import time
from collections.abc import Callable

from f1 import geometry, nav, quests, scripts, session, state, watchdog, win32, world
from f1.actions import Actor
from f1.idealist import day, experience, hands_empty, karma, rest_until
from f1.routes import Step, heal_up, in_town, on_map, pick_lock, settle, travel

INVASION_DAY = 110  # GVAR NECROPOLIS_INVADED_DATE: HOTELMAP/HALLDED1/WATRSHED map_enter
SET_JOB = [
    "oh, killer it is",
    "got it.",
    "what can i do for you?",
    "done.",
    "got it.",
]  # SET.MSG 102, 108, 111, 144, 125
SET_BOUNTY = ["i'm here to see the boss. that you?", "i'm here to collect the bounty for some mutants."]  # 199, 215
SET_REWARD = ["ok. thanks."]  # SET.MSG 166 after set18: SIGNAL_REWARD |= 1; never 167
GARRET_DAY = ["oh, ok."]  # GARRET.MSG 102, by day, once: his reaction (level 3 at CH 3, karma 45+)
PRISONER_THANKS = ["no prob."]  # PRISONR.MSG 116: +500 XP; karma +1 when he leaves
CELL_DOOR = 18262  # WATRSHD e1, GDOOR (DOOR.INT's messages): the prisoner behind it
WAIT_SPEED = 50  # the clock for a wait the game will not rest through (f1.clock: the engine holds to 50x)
GARRET_FRIDGE = 19913  # HALLDED e1, NGARFRDG: never touched by the player (Looting_Fridge)
# (script, the hex to fight from, the foes of that fight): the plan's order, each from a hex only its target notices
FIGHTS = (
    ("larry", 18705, ("larry",)),
    ("terry", 16276, ("terry",)),
    ("harry", 14695, ("harry",)),
    ("barry", 11883, ("barry", "gary", "sally")),
)
SIX = frozenset({"larry", "terry", "harry", "barry", "gary", "sally"})  # the six mutants' scripts


def before_the_invasion(actor: Actor) -> bool:
    killed_on = quests.gvar(actor, "HUB_FILLER_28")
    ok = day(actor) < INVASION_DAY and (
        quests.gvar(actor, "NECROP_MUTANTS_KILLED") != 2 or day(actor) - killed_on <= 29
    )
    if not ok:
        actor.log.emit("necropolis", why="past the invasion: no entry", day=day(actor), killed_on=killed_on)
    return ok


def alive(actor: Actor, names) -> list[state.Critter]:
    return [
        c
        for c in state.critters(actor.mem)
        if not c.dead and c.hp > 0 and scripts.script_of(actor.mem, c.address) in names
    ]


def to_section(section: int, name: str) -> Callable[[Actor], bool]:
    def go(actor: Actor) -> bool:
        if on_map(actor, name):
            return True
        if not before_the_invasion(actor):
            return False
        if actor.snap().screen == "map" and not quests.leave_map(actor):
            return False
        arrived = travel(actor, "necropolis", section)
        if not arrived.startswith(name):
            return False
        return in_town(actor, arrived) or on_map(actor, name)

    return go


def talk(actor: Actor, name: str, plan: list[str], done: Callable[[Actor], bool]) -> bool:
    if done(actor):
        return True
    hands_empty(actor)
    quests.talk(actor, name, plan, strict=True)
    settle(actor, plan)
    return done(actor)


def set_job(actor: Actor) -> bool:
    """Set's first talk (he opens it himself within 6 hexes): the job, NECROP_MUTANTS_KILLED 1."""
    if quests.gvar(actor, "NECROP_MUTANTS_KILLED") >= 1:
        return True
    return talk(actor, "set", SET_JOB, lambda a: quests.gvar(a, "NECROP_MUTANTS_KILLED") >= 1)


_garret_talked: set[int] = set()  # one daytime talk only: a second one lowers his reaction a level (garret02a)


def garret_by_day(actor: Actor) -> bool:
    if actor.pid in _garret_talked:
        return True
    if not rest_until(actor, 7, 17):
        return False
    hands_empty(actor)
    steps = quests.talk(actor, "garret", GARRET_DAY, strict=True) or []
    settle(actor, GARRET_DAY)
    replied = any("shouldn't be here during the day" in (reply or "").lower() for reply, _o, _c in steps)
    actor.log.emit("garret_day", replied=replied, steps=len(steps))
    if steps:
        _garret_talked.add(actor.pid)
    return replied


def fight_from(script: str, spot: int, foes: tuple[str, ...]) -> Callable[[Actor], bool]:
    """To the plan's hex for this fight (out of the others' notice), combat by our hand, then these foes only."""
    targets = tuple(f"script:{f}" for f in foes)

    def go(actor: Actor) -> bool:
        if not on_map(actor, "WATRSHD"):
            return False
        if not alive(actor, foes):
            return True
        xp0, k0 = experience(actor), karma(actor)
        heal_up(actor, 0.8)
        actor.ready_weapon()
        goal = spot
        for _ in range(8):
            if not alive(actor, foes):
                break
            if actor.in_combat():
                actor.fight(only=targets)
                # a foe out of the spot's line of fire (a full run: Gary at 12696, "aim blocked" from every hex
                # the turn's AP reached, the combat over and again, twice): the next combat starts beside him
                s = actor.snap()
                goal = min((c.tile for c in alive(actor, foes)), key=lambda t: geometry.distance(t, s.dude.tile),
                           default=spot)  # fmt: skip
                continue
            s = actor.snap()
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            if s.dude.tile != goal and geometry.distance(s.dude.tile, goal) > 1:
                nav.go_to(actor, {goal} | {t for t in geometry.ring(goal, 1) if t not in obs.blocked}, 40, fight=False)
                continue
            session.press("a")  # the first shot is ours: fight() takes the nearest of `foes`
            win32.wait_for(actor.in_combat, 3, 0.2)
        actor.log.emit(
            "watershed_fight", target=script, left=[scripts.script_of(actor.mem, c.address) for c in alive(actor, SIX)],
            killed=quests.gvar(actor, "SUPER_MUTANTS_KILLED"), necrop=quests.gvar(actor, "NECROP_MUTANTS_KILLED"),
            xp=experience(actor) - xp0, karma=karma(actor) - k0, hp=actor.snap().dude.hp,
        )  # fmt: skip
        if actor.in_combat():  # anyone else who came in
            actor.fight(only=tuple(f"script:{f}" for f in sorted(SIX)))
        return not alive(actor, foes)

    return go


def the_prisoner(actor: Actor) -> bool:
    """The cell door picked (Lock Picks +20, else the skill), opened; the prisoner's thanks (+500 XP); the karma
    comes as he leaves the map."""
    if not on_map(actor, "WATRSHD"):
        return False
    prisoner = alive(actor, ("prisonr",))
    if not prisoner:
        return True
    door = next((t for t in world.things(actor.mem) if t.tile == CELL_DOOR and t.type == "scenery"), None)
    if door is None:
        return False
    actor.holster(True)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if CELL_DOOR in obs.doors:
        nav.go_to(actor, {t for t in geometry.ring(CELL_DOOR, 1) if t not in obs.blocked}, 60, fight=False)
        if not nav.open_door(actor, CELL_DOOR):
            pick_lock(actor, CELL_DOOR, door.address, tries=6)
            nav.open_door(actor, CELL_DOOR)
    xp0 = experience(actor)
    talk(actor, "prisonr", PRISONER_THANKS, lambda a: False)
    actor.log.emit("prisoner", xp=experience(actor) - xp0, mvar6=quests.mvar(actor, 6))
    return experience(actor) - xp0 >= 500 or quests.mvar(actor, 6) != 0


def evening(actor: Actor) -> bool:
    """Night for Set's reward. The Pip-Boy offers no rest anywhere on HALLDED e1, by the Hall or inside it (live:
    "no button 520"; the plan read CE's rule as allowing it): the game clock runs at 50x while the
    Idealist stands by Set (33 game seconds a second, measured: 4 h in about 8 minutes)."""
    from f1 import clock

    night = lambda: not 7 <= hour_of(actor) < 18
    if night() or rest_until(actor, 18, 24):
        return True
    watchdog.quiet(900)  # nothing moves on purpose (the watchdog tripped at 90 s and reloaded, live)
    clock.set_speed(actor.pid, WAIT_SPEED)
    try:
        win32.wait_for(night, 900, 2.0)
    finally:
        clock.set_speed(actor.pid, 1)
    actor.log.emit("evening", hour=round(hour_of(actor), 2))
    return night()


def hour_of(actor: Actor) -> float:
    from f1.routes import hour

    return hour(actor)


def set_reward(actor: Actor) -> bool:
    if quests.gvar(actor, "SIGNAL_REWARD") & 1:
        return True
    plan = SET_REWARD if quests.gvar(actor, "NECROP_MUTANTS_KILLED") == 2 else []
    if not quests.gvar(actor, "NECROP_MUTANTS_KILLED"):
        plan = SET_BOUNTY
    return talk(actor, "set", plan, lambda a: quests.gvar(a, "SIGNAL_REWARD") & 1)


def garrets_fridge(actor: Actor) -> bool:
    """Garret walks to his fridge and pays once the player is within 4 hexes: followed 2 hexes behind."""
    from f1.routes import PID_CAPS

    caps0 = actor._count(PID_CAPS)
    end = time.monotonic() + 90
    while time.monotonic() < end and actor._count(PID_CAPS) <= caps0:
        garret = quests.find_critter(actor, "garret")
        if garret is None:
            break
        s = actor.snap()
        if geometry.distance(garret.tile, s.dude.tile) > 3:
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            nav.go_to(actor, {t for t in geometry.ring(garret.tile, 2) if t not in obs.blocked}, 6, fight=False)
        time.sleep(0.5)
    actor.log.emit("garret_paid", caps=actor._count(PID_CAPS) - caps0)
    return actor._count(PID_CAPS) > caps0


def out_of_necropolis(actor: Actor) -> bool:
    actor.holster(True)
    return actor.snap().screen == "worldmap" or quests.leave_map(actor)


IDEALIST_WATERSHED = [
    Step("to the Hall", to_section(1, "HALLDED"), checkpoint=True, tries=6),
    Step("Set: the job", set_job, checkpoint=True),
    Step("Garret by day", garret_by_day, checkpoint=True),
    Step("to the Watershed", to_section(2, "WATRSHD"), checkpoint=True, tries=6),
    *(Step(f"the Watershed: {s}", fight_from(s, spot, foes), checkpoint=True, tries=2) for s, spot, foes in FIGHTS),
    Step("the prisoner", the_prisoner, checkpoint=True, optional=True),
    Step("back to the Hall", to_section(1, "HALLDED"), checkpoint=True, tries=6),
    Step("evening", evening),
    Step("Set: the reward", set_reward, checkpoint=True),
    Step("Garret's fridge", garrets_fridge, checkpoint=True, optional=True),
    Step("out of Necropolis", out_of_necropolis),
]

ROUTES = {"idealist_watershed": IDEALIST_WATERSHED}
