"""The Idealist's return to the Raiders' camp (planned from the scripts): Garl and at least
eight more killed for nar_35, the two captive women freed (200 XP and karma +1 each), nobody else harmed.

    python -m f1.routes idealist_raiders [--from N] [--until N] [--clock K]

With Tandi home every raider attacks on sight within 12 hexes; talking to Garl only starts the fight. The women pay
only out of combat, on the map, while alive, once (GARL_DEAD and TOTAL_RAIDERS <= 12) or TOTAL_RAIDERS <= 6: so the
outer camp goes first (ten of its twelve counted raiders: TOTAL 6, both women paid while they still stand behind two
walls), then Garl's room from the south door, where a missed bolt ends in the room's far wall and not in a woman
(a miss flies on past its target and hits the first critter beyond it, with no roll: CE compute_attack). No rest on
this map (the women are another team): stimpaks only.
"""

import time

from f1 import geometry, nav, quests, scripts, session, state, win32
from f1.actions import PID_STIMPAK, Actor
from f1.idealist import experience, karma
from f1.routes import Step, heal_below, on_map, travel

PID_TURBO, PID_MFC, PID_POWER_ARMOR = 233, 39, 3
OUTER = frozenset({"genraidr", "raidgrd", "tolya", "petrox", "diana"})  # scripts: the counted raiders outside the room
ROOM = frozenset({"garl", "gwen", "alya"})  # scripts in Garl's room
OUTER_FOES = tuple(f"script:{s}" for s in sorted(OUTER))
ROOM_FOES = tuple(f"script:{s}" for s in sorted(ROOM))
WOMEN = "women"
SOUTH_DOOR = 19092  # Garl's room to the south building (closed in the SAV); 21092 the south building's own door
SOUTH_SPOTS = (19292, 19492, 19692, 19291, 19293)  # x 91-93 south of 19092: misses at Garl's room end in its far wall
WOMEN_KILLS_LEFT = 6  # TOTAL_RAIDERS at which both women pay without Garl dead
_since: dict[str, int] = {}  # the message line count when the outer camp's fight began: the women's lines after it


def by_script(actor: Actor, names, alive: bool = True) -> list[state.Critter]:
    return [
        c
        for c in state.critters(actor.mem)
        if (not c.dead and c.hp > 0) == alive and scripts.script_of(actor.mem, c.address) in names
    ]


def charges(actor: Actor) -> int:
    turbo = next((c for c in actor.carried_weapons() if c.pid == PID_TURBO), None)
    return (turbo.loaded if turbo else 0) + 50 * actor._count(PID_MFC)


def ready(actor: Actor) -> bool:
    """The Turbo with 30+ charges, 5+ stimpaks, the Powered Armor worn (the plan's minimum for the camp)."""
    from f1.routes import wearing

    if actor.snap().screen == "map":
        if actor._count(PID_POWER_ARMOR) and not wearing(actor, PID_POWER_ARMOR):
            actor.equip(PID_POWER_ARMOR, "armor")
        actor.ready_weapon()
        actor.holster(True)
    out = {"charges": charges(actor), "stimpaks": actor._count(PID_STIMPAK), "armor": wearing(actor, PID_POWER_ARMOR)}
    actor.log.emit("raiders_ready", **out)
    return out["charges"] >= 30 and out["stimpaks"] >= 5


def to_the_camp(actor: Actor) -> bool:
    """No in_town: the gun stays out (the camp is hostile on sight)."""
    if on_map(actor, "RAIDERS"):
        return True
    if actor.snap().screen == "map" and not quests.leave_map(actor):
        return False
    arrived = travel(actor, "raiders", 0)
    ok = arrived.startswith("RAIDERS")
    if ok:
        actor.log.emit(
            "camp", total=quests.gvar(actor, "TOTAL_RAIDERS"), garl_dead=quests.gvar(actor, "GARL_DEAD"),
            women=len(by_script(actor, {WOMEN})), outer=len(by_script(actor, OUTER)), tile=actor.snap().dude.tile,
        )  # fmt: skip
    return ok


def fight_these(actor: Actor, foes: tuple[str, ...], until, rounds: int = 12) -> None:
    """Fight `foes` (by script) until `until()`: combat when it comes, else toward the nearest of them."""
    actor.ready_weapon()
    for _ in range(rounds):
        if until():
            return
        s = actor.snap()
        if s.dude is None or s.dude.hp <= 0 or s.screen == "main_menu":
            return
        if actor.in_combat():
            actor.fight(only=foes, spare=frozenset({WOMEN}))
            continue
        heal_below(actor, int(0.6 * actor.max_hp))
        left = by_script(actor, tuple(f[7:] for f in foes))
        left = [c for c in left if c.elevation == s.elevation]
        if not left:
            return
        target = min(left, key=lambda c: geometry.distance(c.tile, s.dude.tile))
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        if geometry.distance(target.tile, s.dude.tile) <= 10:
            session.press("a")  # combat on our turn first; fight() picks the target
            win32.wait_for(actor.in_combat, 3, 0.2)
            continue
        goals = {t for r in (7, 8, 9) for t in geometry.ring(target.tile, r) if t not in obs.blocked}
        nav.go_to(actor, goals, max_steps=6, fight=False)
        win32.wait_for(actor.in_combat, 2, 0.2)


def the_outer_camp(actor: Actor) -> bool:
    if not on_map(actor, "RAIDERS"):
        return False
    _since.setdefault("outer", actor.mem.glob("disp_start"))
    done = lambda: quests.gvar(actor, "TOTAL_RAIDERS") <= WOMEN_KILLS_LEFT and not actor.in_combat()
    fight_these(actor, OUTER_FOES, done, rounds=20)
    if actor.in_combat():  # Garl's people may have come out: whoever still fights is fought, the women aside
        actor.fight(only=OUTER_FOES + ROOM_FOES, spare=frozenset({WOMEN}))
    actor.log.emit(
        "outer_camp", total=quests.gvar(actor, "TOTAL_RAIDERS"), garl_dead=quests.gvar(actor, "GARL_DEAD"),
        women=len(by_script(actor, {WOMEN})), hp=actor.snap().dude.hp, charges=charges(actor),
    )  # fmt: skip
    return quests.gvar(actor, "TOTAL_RAIDERS") <= WOMEN_KILLS_LEFT or quests.gvar(actor, "GARL_DEAD") == 1


def rescues(actor: Actor, n0: int) -> int:
    """The women's lines since `n0`, joined first: the game wraps "...for the rescue of a / slave." over two lines
    (live: both paid and the step, matching line by line, saw none)."""
    count = lambda lines: " ".join(" ".join(lines).split()).count("rescue of a slave")
    # disp_start counts modulo the 100-line box: a long fight wraps it past n0 and the window shrinks to a few
    # lines (a full run: both paid, three minutes of fight, the step saw none), so the newest 40 lines count too
    return max(count(state.messages_since(actor.mem, n0)), count(state.messages(actor.mem, 40)))


def the_women_freed(actor: Actor) -> bool:
    """Out of combat on the map: each living woman's critter_p_proc pays once (karma +1, 200 XP)."""
    k0, xp0 = karma(actor), experience(actor)
    # they may have paid before this step began: since the outer camp's start, or the box's last lines on a resume
    n0 = _since.get("outer", max(0, actor.mem.glob("disp_start") - 40))
    women = len(by_script(actor, {WOMEN}))
    win32.wait_for(lambda: rescues(actor, n0) >= women, 30, 0.5)
    paid = rescues(actor, n0)
    actor.log.emit("women", alive=women, paid=paid, karma=karma(actor) - k0, xp=experience(actor) - xp0)
    return women > 0 and paid >= women


def in_the_room(tile: int) -> bool:
    x, y = tile % 200, tile // 200
    return 89 <= x <= 99 and 85 <= y <= 94


def garls_room(actor: Actor) -> bool:
    """Garl, Gwen and Alya, the women spared on the line of fire. Inside the room they are fought from the south
    building through 19092 (the plan); live they came out to the south building once the outer camp fell,
    and are fought where they stand. Walks never fight on their own (go_to's fight has no spare)."""
    if quests.gvar(actor, "GARL_DEAD") == 1 and not by_script(actor, ROOM):
        return True
    heal_below(actor, int(0.9 * actor.max_hp))
    if any(in_the_room(c.tile) for c in by_script(actor, ROOM)) and not actor.in_combat():
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        spots = {t for t in SOUTH_SPOTS if t not in obs.blocked}
        nav.go_to(actor, spots, max_steps=30, fight=False)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        if SOUTH_DOOR in obs.doors and not actor.in_combat():
            nav.go_to(actor, {t for t in geometry.ring(SOUTH_DOOR, 1) if t not in obs.blocked and t > SOUTH_DOOR}, 10,
                      fight=False)  # fmt: skip
            nav.open_door(actor, SOUTH_DOOR)
            nav.go_to(actor, spots, max_steps=6, fight=False)
    fight_these(actor, ROOM_FOES, lambda: not by_script(actor, ROOM), rounds=16)
    women = by_script(actor, {WOMEN})
    actor.log.emit(
        "garls_room", garl_dead=quests.gvar(actor, "GARL_DEAD"), total=quests.gvar(actor, "TOTAL_RAIDERS"),
        women=len(women), women_tiles=[c.tile for c in women], hp=actor.snap().dude.hp, charges=charges(actor),
    )  # fmt: skip
    return quests.gvar(actor, "GARL_DEAD") == 1


def out_of_the_camp(actor: Actor) -> bool:
    if actor.in_combat():
        actor.fight()
    time.sleep(1.0)
    actor.holster(True)
    ok = actor.snap().screen == "worldmap" or quests.leave_map(actor)
    actor.log.emit("camp_left", total=quests.gvar(actor, "TOTAL_RAIDERS"), garl_dead=quests.gvar(actor, "GARL_DEAD"),
                   karma=karma(actor), bad=quests.gvar(actor, "BAD_MONSTER"), good=quests.gvar(actor, "GOOD_MONSTER"))  # fmt: skip
    return ok


IDEALIST_RAIDERS = [
    Step("ready for the camp", ready),
    Step("to the camp", to_the_camp, checkpoint=True, tries=6),
    Step("the outer camp", the_outer_camp, checkpoint=True, tries=2),
    Step("the women freed", the_women_freed, checkpoint=True),
    Step("Garl's room", garls_room, checkpoint=True, tries=2),
    Step("out of the camp", out_of_the_camp),
]

ROUTES = {"idealist_raiders": IDEALIST_RAIDERS}
