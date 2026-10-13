"""Agent route tasks for military."""

import time

from f1 import dialogue, geometry, nav, quests, state, win32, world
from f1.actions import PID_STIMPAK, Actor
from f1.agent_boneyard import (
    PID_ROBES,
)
from f1.agent_cathedral import (
    PID_ELECTRONIC_LOCKPICK,
    the_ending,
)
from f1.engine_map import Obj
from f1.route_support import (
    Step,
    hands_empty,
    heal_below,
    heal_up,
    on_map,
    travel,
    wearing,
)

# The Military Base and the vats: the scripts named below, read with f1.intdump; tiles from f1.mapfile.
GATE_PASS = [  # the base's mutants (GENSUPR/VGATEMUT, 433): 111 needs the Robes (Speech +25); a failure is a fight
    "gensupr#111",
    "gensupr#110",
]

MB_DOOR = 21271  # MBENT e0, MBOUT2IN: the Electronic Lock Pick at Lockpick -40; the third failure alerts the base

MB_LIFT = 14520  # MBSTRG12 e0/e1, elev4 (elevator type 4: '1' MBSTRG12 e0 14920, '2' e1, '3' MBVATS12 e0 12944)

MB_PAIN = frozenset({21068, 25088, 15520, 26710})  # MBSTRG12 e0, painfeld: 10-30 damage on stepping there

VATS_LIFT = 24120  # MBVATS12 e0/e1, elev5 (type 5: '3' e0 24520, '4' e1 24520)

VATS_LIFT_UP = 12544  # MBVATS12 e0, elev4: '1' back to MBSTRG12 e0

VATS_PAIN = frozenset({13544})  # MBVATS12 e0, painfeld

KRUPPER_TALK = ["krupper#103", "krupper#109"]  # KRUPPER 103, 109: then 20 s to be out of his sight

VCONCOMP = 12325  # MBVATS12 e1: Science (0) gives the interface; a failure costs 300 game seconds, nothing else

VATS_CODE = ["#103", "#110/124"]  # VCONCOMP 103, 110: VATS_BLOWN, 300 game seconds

BY_VCONCOMP = {12324, 12326}  # where the Agent stood for the Science tries

LIEUTENANT = 23872  # MBVATS12 e1: he talks within 12 hexes, his guards (ltguard) fight within 6

VATS_CELL_DOOR = 25740  # MBVATS12 e0, the script `door`: locked while its local_var(0) is 0

VATS_CELL_BONES = 25951  # behind that door (map file): 3 stimpaks and a 10mm pistol

PID_LOCK_PICKS = 0x54  # 84: DOOR.INT's use_obj_on rolls Lockpick +20 for them, the bare skill +0

# DOOR.INT's answers to a lock pick try, with message 110 when the skill's critical failure jams the lock
LOCK_RESULTS = (100, 103, 101, 110)  # DOOR.MSG: unlocked, not picked, the picks broke, beyond the skill


def door_said(actor: Actor, n0: int, number: int) -> bool:
    """The message box shows message NUMBER of DOOR.MSG since its line N0."""
    return dialogue.holds(" ".join(state.messages_since(actor.mem, n0)), "door", number)


def vconcomp_said(actor: Actor, n0: int) -> bool:
    """The VATS computer gave its interface: VCONCOMP.MSG 176 in the message box since its line N0."""
    return dialogue.holds(" ".join(state.messages_since(actor.mem, n0)), "vconcomp", 176)


def said_since(actor: Actor, n0: int, text: str) -> bool:
    """The message box got `text` since its line counter (disp_start) read `n0`; wrapped lines are joined first."""
    return text.lower() in " ".join(" ".join(state.messages_since(actor.mem, n0)).split()).lower()


def guarded_walk(actor: Actor, goals: set[int], avoid: frozenset[int] = frozenset(), plan=None, legs: int = 12) -> bool:
    """Walk to `goals` in the Military Base, where a mutant stops the player to talk (within 12 hexes, until one
    talk passed: `ignoring_dude`, which a load forgets): each talk is answered strictly by `plan`. A fight fails the
    step (the runner reloads). True once on a goal, or once an elevator's panel opened there."""
    plan = plan or GATE_PASS
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if avoid and nav.astar(s.dude.tile, goals, obs.passable_doors | avoid) is None:
        # the pain fields are the gates of MBSTRG12's corridors: the ones on the only way are crossed (15520 before
        # the lift), the others still avoided
        avoid = avoid - set(nav.astar(s.dude.tile, goals, obs.passable_doors) or [])
    for _ in range(legs):
        s = actor.snap()
        if s.screen == "dialogue":
            if dialogue.converse_strict(actor.mem, plan, log=actor.log) is None:
                return False
            time.sleep(0.5)
            continue
        if s.screen == "elevator" or (s.screen == "map" and s.dude is not None and s.dude.tile in goals):
            return True
        if s.screen != "map" or actor.in_combat():
            return False
        nav.go_to(actor, goals, max_steps=40, fight=False, avoid=avoid)
    s = actor.snap()
    return s.screen == "elevator" or (s.dude is not None and s.dude.tile in goals)


def the_base_door(actor: Actor) -> bool:
    """MBOUT2IN: the Electronic Lock Pick on the door, two tries (a third failure alerts the base; the runner's reload
    gives two more), then through the door to MBSTRG12 (27092)."""
    if on_map(actor, "MBSTRG12"):
        return True
    door = next((t for t in world.things(actor.mem) if t.tile == MB_DOOR and t.type == "scenery"), None)
    if door is None:
        return False
    locked = lambda: bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    if not guarded_walk(actor, {t for t in geometry.ring(MB_DOOR, 1) if t not in obs.blocked}):
        return False
    for _ in range(2):
        if not locked():
            break
        time.sleep(1.5)  # the last try's message comes in two lines: a late one ended the next try at once (live)
        n0 = actor.mem.glob("disp_start")
        tried = lambda n0=n0: not locked() or said_since(actor, n0, "unable")
        actor.use_item_on(PID_ELECTRONIC_LOCKPICK, MB_DOOR, tried)
        win32.wait_for(tried, 8, 0.2)
        actor.log.emit("lockpick", tile=MB_DOOR, unlocked=not locked(), failed=said_since(actor, n0, "unable"))
    actor.unequip("right")
    if locked():
        return False
    through = lambda: on_map(actor, "MBSTRG12")
    return actor.click_object(MB_DOOR, through, 20, aims=nav.DOOR_AIMS, targets=frozenset({door.address})) or through()


def to_the_vats(actor: Actor) -> bool:
    """MBSTRG12 e0 to the lift's trigger round the pain fields, the level's guards answered; '3' to MBVATS12 e0."""
    if on_map(actor, "MBVATS12"):
        return True
    heal_up(actor)  # the lift's pain field (15520) takes 10-30
    return guarded_walk(actor, {MB_LIFT}, avoid=MB_PAIN, legs=16) and quests.ride_from(actor, MB_LIFT, "3")


def past_krupper(actor: Actor) -> bool:
    """MBVATS12 e0: Krupper stops a Robe-wearer (103, 109, his warning, then 20 game seconds to be out of his
    sight or he fights): straight on to the lift's trigger, '4' to e1."""
    if on_map(actor, "MBVATS12") and actor.snap().elevation == 1:
        return True
    return guarded_walk(actor, {VATS_LIFT}, VATS_PAIN, KRUPPER_TALK + GATE_PASS, 16) and quests.ride_from(
        actor, VATS_LIFT, "4"
    )


def vats_computer(actor: Actor):
    """VCONCOMP's object: a Computer on MBVATS12 e1 12325 under two Monitors (scenery without scripts) on that hex."""
    return next(
        (
            t
            for t in world.things(actor.mem)
            if t.tile == VCONCOMP and t.elevation == 1 and actor.mem.i32(t.address + Obj.SID) != -1
        ),
        None,
    )


def vats_interface(actor: Actor) -> bool:
    """VCONCOMP, MBVATS12 e1: Science until its interface message (VCONCOMP.MSG 176; 33 % a try with Science 33).
    Its technicians stand in front of it: the clicks are aimed where the engine names the computer."""
    comp = vats_computer(actor)
    targets = frozenset({comp.address}) if comp else frozenset()
    for _ in range(15):
        n0 = actor.mem.glob("disp_start")
        actor.use_skill_on(
            "science",
            VCONCOMP,
            lambda n0=n0: vconcomp_said(actor, n0) or said_since(actor, n0, "too"),
            targets=targets,
        )
        if vconcomp_said(actor, n0):
            return True
    return False


def pick_lock(actor: Actor, tile: int, door: int, tries: int = 5) -> bool:
    """A door scripted by DOOR.INT: the Lock Picks used on it (Lockpick +20), or the bare skill once they broke. Its
    answer is read from the message box after the click. DOOR.MSG 100 (unlocked) sets the script's local_var(0); the
    engine's lock bit stays set until the door is next used, and that use unlocks and opens it."""
    got = None
    for _ in range(tries):
        n0 = actor.mem.glob("disp_start")
        answered = lambda n0=n0: next((r for r in LOCK_RESULTS if door_said(actor, n0, r)), None) is not None
        if actor._count(PID_LOCK_PICKS):
            actor.use_item_on(PID_LOCK_PICKS, tile, answered, targets=frozenset({door}))
        else:
            actor.use_skill_on("lockpick", tile, answered, targets=frozenset({door}))
        win32.wait_for(answered, 3, 0.1)
        got = next((r for r in LOCK_RESULTS if door_said(actor, n0, r)), None)
        actor.log.emit("lock_pick", tile=tile, picks=actor._count(PID_LOCK_PICKS), result=got)
        if got in (LOCK_RESULTS[0], LOCK_RESULTS[3]) or said_since(actor, n0, "is jammed"):  # unlocked, or jammed
            break
        time.sleep(1.0)
    actor.unequip("right")  # the picks left in the active hand would turn an attack into a use
    return got == LOCK_RESULTS[0]


def cell_stimpaks(actor: Actor) -> bool:
    """MBVATS12 e0's cell: the Bones behind the locked door 25740 hold three stimpaks, for an escape across two pain
    fields (10-30 each) begun at HP 32 of 59. Down by the lift ('3'), the lock picked, the door opened, the Bones
    looted; HP made up with the stimpaks; back up ('4') and beside the computer, where the code is entered."""
    things = world.things(actor.mem)
    bones = next((t for t in things if t.tile == VATS_CELL_BONES and t.elevation == 0 and t.type == "item"), None)
    door = next((t for t in things if t.tile == VATS_CELL_DOOR and t.elevation == 0 and t.type == "scenery"), None)
    if bones is None or door is None:
        return False
    if any(t.address == bones.address for t, _ in world.holders_of(actor.mem, PID_STIMPAK)):
        if actor.snap().elevation == 1 and not quests.ride_from(actor, VATS_LIFT, "3"):
            return False
        locked = lambda: bool(actor.mem.u32(door.address + Obj.DOOR_OPEN_FLAGS) & Obj.DOOR_LOCKED)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(VATS_CELL_DOOR, 1) if t not in obs.blocked}, 60, fight=False)
        if locked() and not pick_lock(actor, VATS_CELL_DOOR, door.address):
            return False

        def is_open() -> bool:
            s = actor.snap()
            return VATS_CELL_DOOR not in nav.obstacles(actor.mem, s.elevation, s.dude.address).doors

        targets = frozenset({door.address})
        if not (is_open() or actor.click_object(VATS_CELL_DOOR, is_open, 10, aims=nav.DOOR_AIMS, targets=targets)):
            return False
        actor.set_mouse_mode(0)
        if not actor.loot(VATS_CELL_BONES).ok:
            return False
    heal_below(actor, int(0.95 * actor.max_hp))
    if actor.snap().elevation == 0 and not quests.ride_from(actor, VATS_LIFT, "4"):
        return False
    nav.go_to(actor, BY_VCONCOMP, 80, fight=False)
    s = actor.snap()
    healed = s.dude.hp >= int(0.95 * actor.max_hp) or not actor._count(PID_STIMPAK)
    return healed and s.elevation == 1 and s.dude.tile in BY_VCONCOMP


def the_vats_code(actor: Actor) -> bool:
    """The computer used (its talk opens once the interface is up): 103, then 110 sets VATS_BLOWN and the 300 s."""
    if quests.gvar(actor, "VATS_BLOWN") == 1:
        return True
    heal_up(actor, 0.95)  # the way out crosses two pain fields against the clock
    comp = vats_computer(actor)
    if comp is None:
        return False
    talking = lambda: actor.snap().screen == "dialogue"
    if not actor.click_object(VCONCOMP, talking, 60, aims=quests.SCENERY_AIMS, targets=frozenset({comp.address})):
        return False
    win32.wait_for(lambda: actor.mem.glob("gdNumOptions") > 0, 5, 0.1)  # its prompt shows before its options (live)
    dialogue.converse_strict(actor.mem, VATS_CODE, log=actor.log)
    time.sleep(1.0)
    return quests.gvar(actor, "VATS_BLOWN") == 1


def out_of_the_base(actor: Actor) -> bool:
    """300 game seconds from the code (the base's map scripts: then the movie and the end): e1's lift ('3'), e0 to the
    lift up (12544, '1'), MBSTRG12 round the pain fields to its exits south (to MBENT), MBENT's exits east."""
    start = time.monotonic()

    def mark(what: str) -> None:
        s = actor.snap()
        actor.log.emit("escape", what=what, seconds=round(time.monotonic() - start, 1), map=s.map_name,
                       hp=s.dude.hp if s.dude else None)  # fmt: skip

    if on_map(actor, "MBVATS12") and actor.snap().elevation == 1:
        guarded_walk(actor, {VATS_LIFT}, plan=KRUPPER_TALK + GATE_PASS)
        quests.ride_from(actor, VATS_LIFT, "3")
        mark("vats e0")
    if on_map(actor, "MBVATS12"):
        guarded_walk(actor, {VATS_LIFT_UP}, VATS_PAIN, KRUPPER_TALK + GATE_PASS, 16)
        quests.ride_from(actor, VATS_LIFT_UP, "1")
        mark("storage")
    for name, avoid in (("MBSTRG12", MB_PAIN), ("MBENT", frozenset())):
        if on_map(actor, name):
            s = actor.snap()
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            exits = {t for t, (to_map, _t, _e) in obs.exits.items() if to_map == 30 or to_map < 0}  # 30: MBENT
            guarded_walk(actor, exits, avoid, legs=16)
            win32.wait_for(lambda name=name: not on_map(actor, name), 20, 0.25)
            mark(f"out of {name}")
    ok = actor.snap().screen != "map"
    mark("out" if ok else "not out")
    return ok


VATS = [
    Step("robes on", lambda a: wearing(a, PID_ROBES) or a.equip(PID_ROBES, "armor").ok),
    Step(  # the road's fights near the Boneyard beat the Agent even with a Desert Eagle (two of two): fled instead
        "to the Military Base",
        lambda a: travel(a, "military base", 0, flee_all=True).startswith("MBENT"),
        checkpoint=True,
        tries=6,
    ),
    Step("hands empty", hands_empty, tries=12),  # the loads after the arrival count here: the door's tries
    Step("the base's door", the_base_door, checkpoint=True),  # 33 % a try, two tries a load
    Step("to the vats", to_the_vats, checkpoint=True),
    Step("past Krupper", past_krupper, checkpoint=True),
    Step("the vats' computer", vats_interface, checkpoint=True),
    Step("the cell's stimpaks", cell_stimpaks, checkpoint=True),
    Step("the code", the_vats_code),
    # no checkpoint after it: the world map plays the explosion and the ending, which the keys of a save would skip
    Step("out of the base", out_of_the_base),
    Step("the ending", the_ending, final=True),
]
