"""Idealist route tasks for glow."""

import time
from collections.abc import Callable

from f1 import dialogue, geometry, loot, nav, quests, state, win32, world
from f1.actions import Actor
from f1.agent_brotherhood import (
    radiation,
)
from f1.agent_glow import (
    GLOW_BEAM_AIMS,
    GLOW_BODY,
    PID_BROTHERHOOD_TAPE,
    hand_in_the_tape,
    rope_on_the_beam,
    the_glow_quest,
    the_tape,
    up_the_rubble,
)
from f1.idealist_hub import (
    hub_map,
)
from f1.idealist_support import (
    PID_COMBAT_ARMOR,
    experience,
    gained,
)
from f1.idealist_trading import (
    PID_10MM_SMG,
    PID_RAD_X,
    PID_RADAWAY,
    PID_TOOL,
    VANCE_SELL,
    from_merchant,
    from_mitch,
)
from f1.idealist_vault15 import (
    PID_ROPE,
)
from f1.route_support import (
    Step,
    in_town,
    on_map,
    settle,
    travel,
    zone,
)

IDEALIST_BROTHERHOOD = [
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("Cabbot: the Glow", the_glow_quest, checkpoint=True),
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 0)), checkpoint=True, tries=6),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("a rope from Mitch", from_mitch(PID_ROPE), checkpoint=True),
    Step("a Tool from Mitch", from_mitch(PID_TOOL), checkpoint=True, optional=True),
    Step("Old Town", lambda a: hub_map(a, "HUBOLDTN"), checkpoint=True),
    # the Glow's Rad-X step takes two; Vance sold three for 1364 of 1697 caps (an ironman run: the fourth unaffordable,
    # the step failed twice and the run stood), so two are enough and the rest spares
    Step(
        "Rad-X from Vance",
        from_merchant("vance", "HUBOLDTN", PID_RAD_X, 4, VANCE_SELL, pay_with=(PID_10MM_SMG,), enough=2),
        checkpoint=True,
        tries=3,
    ),
]


# The Glow, deep (planned from the scripts and maps). Two Rad-X cap the
# radiation resistance at 100 for 24 game hours (proto 109: +50 each; stat.c's cap: measured live by the step).
# Lifts: a key used on a lift's door turns off its electric field (GLOYLDOR/GLORDDOR/GLOBLDOR use_obj_on: "...
# Authorization granted"; a plain use of an armed door rolls Traps -20 and shocks); the trigger is in the cab
# behind the door, the arrival before it. Robots (GSENROB) act only at GLOW_POWER 2 with WEAPONS_ARMED 0: primary
# power is switched on at Zax's Power Terminus, where no robot sees, then Zax turns them off (Science -25).
PID_YELLOW_KEY, PID_RED_KEY, PID_BLUE_KEY = 223, 96, 97

PID_FEV_DISK, PID_ALPHA_DISK, PID_DELTA_DISK = 190, 192, 193

STAT_RAD_RESIST = 31

YELLOW_DOOR, YELLOW_CAB = 22326, 22126  # GLOW1 e0..e2 (e2's door is a plain METLDOOR)

RED_DOOR, RED_CAB = 13886, 13686  # GLOW1 e2, GLOW2 e0, e2

BLUE_DOOR, BLUE_CAB = 13930, 13730  # GLOW2 e0..e2

GLOW1_CHEMS = (21305, 18324)  # e0: the Technician (Rad-X, RadAway), a Guard (RadAway)

RED_KEY_BODY = 14486  # GLOW1 e1: a dead Male Guard

LEVEL4_LOCKERS = (12920, 23108)  # GLOW2 e0: Tool, Rad-X, RadAway, Dean's; Tool, Big Book of Science, Dean's

BLUE_KEY_BODY = 22926  # GLOW2 e0

GENERATOR = 19685  # GLOW2 e2, GLOWGEN: a Tool on it, Repair +10; a failure costs 20 game minutes

# GLOW2 e1: Alpha disk, RadAway, Combat Armor, Delta disk, Plasma Rifle, Stealth Boy, FEV disk
LEVEL5_LOCKERS = (18482, 18882, 20889, 20882, 22689, 22882, 18126)

HOT_SPOTS = {  # HOTSPOT spatials: 20-50 rads for every step inside
    ("GLOW1", 0): ((20909, 2), (20316, 2), (16108, 3), (16719, 2)),
    ("GLOW1", 1): ((17105, 3), (20707, 3), (19119, 2)),
    ("GLOW1", 2): ((20107, 4),),
}

ZAX_TRIES = 4  # deactivation tries (10 game minutes each, +5 h a failure): inside one Rad-X window

ZAX_RECORDS = (
    ("gpwrterm#217", "WATER_CHIP_3"),  # the research records' options (GPWRTERM.MSG under ZAX.INT)
    ("gpwrterm#218", "WATER_CHIP_2"),
    ("gpwrterm#219", "WATER_CHIP_1"),
)

DISKS = (
    (PID_FEV_DISK, "FEV_DISK"),
    (PID_ALPHA_DISK, "ALPHA_DISK"),
    (PID_DELTA_DISK, "DELTA_DISK"),
    (PID_BROTHERHOOD_TAPE, "ARTIFACT_DISK"),
)


def rad_resist(actor: Actor) -> int:
    from f1 import chargen

    base = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS)
    bonus = chargen.ints(actor.mem, "pc_proto", 35, chargen.BASE_STATS + 35 * 4)
    return base[STAT_RAD_RESIST] + bonus[STAT_RAD_RESIST]


def rad_x(actor: Actor) -> bool:
    """Rad-X until the resistance reads 100 (two pills), logged with the rads: the plan's claim measured."""
    before = rad_resist(actor)
    for _ in range(2):
        if rad_resist(actor) >= 100 or not actor._count(PID_RAD_X):
            break
        actor.use_on_self(PID_RAD_X)
    left = actor._count(PID_RAD_X)
    actor.log.emit("rad_x", resist_before=before, resist=rad_resist(actor), rads=radiation(actor), left=left)
    return rad_resist(actor) >= 100 or not left


def hot(actor: Actor) -> frozenset[int]:
    s = actor.snap()
    return zone(HOT_SPOTS.get((s.map_name[:5], s.elevation), ()))


def glow_go(actor: Actor, goals: set[int]) -> bool:
    """To `goals` round this floor's hot spots (straight on when that finds no way). A floor trap's passed Traps
    roll stops the walk (TRAPFLOR), so it is issued again."""
    avoid = hot(actor) - goals
    for _ in range(4):
        if actor.snap().dude.tile in goals:
            return True
        ok, why = nav.go_to(actor, goals, max_steps=120, fight=True, avoid=avoid)
        actor.log.emit("glow_walk", ok=ok, why=why, tile=actor.snap().dude.tile)
        if ok:
            return True
        if why == "no path":
            avoid = frozenset()
    return actor.snap().dude.tile in goals


GLOW_ROOM = 45  # lb kept free for the Glow's finds: Combat Armor 20, the Plasma Rifle 12, cells, disks, keys


def glow_loot(actor: Actor, tile: int) -> bool:
    if loot.spare_weight(actor) < GLOW_ROOM:
        loot.lighten(actor, GLOW_ROOM)
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for r in (1, 2) for t in geometry.ring(tile, r) if t not in obs.blocked})
    return actor.loot(tile).ok


def loot_all(tiles: tuple[int, ...]) -> Callable[[Actor], bool]:
    def run(actor: Actor) -> bool:
        for tile in tiles:
            glow_loot(actor, tile)
        return True

    return run


def key_in_door(actor: Actor, key: int, tile: int) -> bool:
    """The key used on the lift's door: its field off (message 106), as the door script says."""
    s = actor.snap()
    door = next(
        (t for t in world.things(actor.mem) if t.tile == tile and t.elevation == s.elevation and "Door" in t.name),
        None,
    )
    if door is None or not actor._count(key):
        actor.log.emit("glow_door", tile=tile, why="no door" if door is None else "no key")
        return False
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for t in geometry.ring(tile, 1) if t not in obs.blocked})
    line = actor.mem.glob("disp_start")

    def granted() -> bool:
        said = " ".join(state.messages_since(actor.mem, line))  # message 106 of the field doors' scripts
        return any(dialogue.holds(said, door, 106) for door in ("glorddor", "globldor", "gloyldor"))

    # the key stays in the right hand: the next use of it skips the inventory, and a fight's first attack swaps to
    # the gun by B (seen: the weapon came off and on all the time; an equip and an unequip cost
    # about 6 s a door in the chain, five doors a Glow run)
    out = actor.use_item_on(key, tile, granted, targets=frozenset({door.address}))
    actor.log.emit("glow_door", tile=tile, granted=granted(), how=out.reason)
    return granted()


def lift(key: int, door: int, cab: int, button: str, arrived) -> Callable[[Actor], bool]:
    def ride(actor: Actor) -> bool:
        if arrived(actor):
            return True
        if not key_in_door(actor, key, door):
            return False
        quests.ride_from(actor, cab, button, avoid=hot(actor))
        return arrived(actor)

    return ride


def at(name: str, elevation: int) -> Callable[[Actor], bool]:
    return lambda a: on_map(a, name) and a.snap().elevation == elevation


def zax_next(actor: Actor, goal: str, d, tries: int, most: int) -> tuple[int | None, int]:
    """The option to take at this node of Zax's talk for `goal`, and the deactivation tries so far. The nodes are
    known by their reply's message and the options by theirs (ZAX.INT: its own list, and GPWRTERM.MSG for the
    mainframe's menus)."""
    reply = (dialogue.list_name(d.reply_list), d.reply_message)

    def pick(*items: str) -> int | None:
        return next((i for i in (dialogue.plan_index(d, p) for p in items) if i is not None), None)

    pending = [opt for opt, g in ZAX_RECORDS if not quests.gvar(actor, g)]
    power = quests.gvar(actor, "GLOW_POWER") == 2
    armed = bool(quests.gvar(actor, "WEAPONS_ARMED"))
    robots_left = goal == "robots" and power and not armed and tries < most
    if reply == ("zax", 102):  # the greeting
        done = (
            (goal == "downloads" and not pending)
            or (goal == "power" and power)
            or (goal == "robots" and not robots_left)
        )
        return pick("zax#108") if done else pick("gpwrterm#200"), tries  # never mind; the mainframe
    if reply == ("gpwrterm", 206):  # the mainframe's menu
        if goal == "downloads" and pending:
            return pick("gpwrterm#209"), tries  # research
        if goal == "power" and not power:
            return pick("gpwrterm#210"), tries  # power terminus
        if robots_left:
            return pick("gpwrterm#208"), tries  # security
        return pick("gpwrterm#204"), tries  # exit the mainframe
    if reply == ("gpwrterm", 216):  # the research records
        return (pick(pending[0]) if pending else pick("gpwrterm#205")), tries  # a record; terminate
    if reply in (("gpwrterm", 212), ("gpwrterm", 213)):  # the security robots' status
        i = pick("gpwrterm#214") if robots_left else None  # deactivate
        return (i, tries + 1) if i is not None else (pick("gpwrterm#205"), tries)
    if goal == "power" and not power:  # power management, primary power, reinitialize it, else the main menu
        return pick("gpwrterm#101/120", "gpwrterm#105/109/123/127", "gpwrterm#109", "gpwrterm#203"), tries
    return pick("gpwrterm#229", "gpwrterm#203", "gpwrterm#205", "gpwrterm#204"), tries  # download, menu, end, exit


def zax(actor: Actor, goal: str, most: int = ZAX_TRIES) -> None:
    """One talk with Zax (the "Terminal" ZAX on GLOW2 e0), steered node by node: its menus come back to themselves
    (a download returns to Research), so a phrase list would loop. `goal`: "downloads", "power" or "robots".
    ZAX.MSG / GPWRTERM.MSG (list 828); never 105/111/112 (ZaxClearance arms the robots again), never chess."""
    mem = actor.mem
    quests.use_scenery(actor, ("zax",), lambda: dialogue.read(mem).active, "Zax")
    tries = 0
    for _ in range(40):
        d = dialogue.read(mem)
        if not d.active:
            break
        if not d.options:
            time.sleep(0.3)
            continue
        i, tries = zax_next(actor, goal, d, tries, most)
        if i is None and len(d.options) == 1:
            i = 0
        if i is None:
            actor.log.emit("dialogue_stop", speaker=d.speaker, reply=d.reply, options=d.options)
            break
        actor.log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i])
        dialogue.choose(mem, i)
        time.sleep(0.2)
    settle(actor)
    actor.log.emit(
        "zax",
        goal=goal,
        tries=tries,
        glow_power=quests.gvar(actor, "GLOW_POWER"),
        weapons_armed=quests.gvar(actor, "WEAPONS_ARMED"),
        downloads=[quests.gvar(actor, g) for _, g in ZAX_RECORDS],
    )


def zax_downloads(actor: Actor) -> bool:
    if all(quests.gvar(actor, g) for _, g in ZAX_RECORDS):
        return True
    zax(actor, "downloads")
    return all(quests.gvar(actor, g) for _, g in ZAX_RECORDS)


def zax_power(actor: Actor) -> bool:
    if quests.gvar(actor, "GLOW_POWER") == 2:
        return True
    zax(actor, "power")
    return quests.gvar(actor, "GLOW_POWER") == 2


def zax_robots(actor: Actor) -> bool:
    """The robots off (Science -25; each failure 5 game hours), at most ZAX_TRIES times; Level 5 waits on it."""
    if quests.gvar(actor, "WEAPONS_ARMED") or quests.gvar(actor, "GLOW_POWER") != 2:
        return True
    zax(actor, "robots")
    return bool(quests.gvar(actor, "WEAPONS_ARMED"))


def the_generator(actor: Actor) -> bool:
    """A Tool on the generator (Repair +10); a failure costs 20 game minutes and is tried again (the game's own
    retry); the lack-of-skill message ends it."""
    if quests.gvar(actor, "START_POWER") == 2:
        return True
    if not actor._count(PID_TOOL):
        return False
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    glow_go(actor, {t for t in geometry.ring(GENERATOR, 1) if t not in obs.blocked})
    xp0 = experience(actor)
    for _ in range(6):
        line = actor.mem.glob("disp_start")

        def said(*words: str, line: int = line) -> bool:
            return any(w in m.lower() for m in state.messages_since(actor.mem, line) for w in words)

        actor.use_item_on(
            PID_TOOL, GENERATOR, lambda: quests.gvar(actor, "START_POWER") == 2 or said("fail", "knowledge")
        )
        actor.log.emit(
            "generator", start_power=quests.gvar(actor, "START_POWER"), msgs=state.messages_since(actor.mem, line)
        )
        if quests.gvar(actor, "START_POWER") == 2 or said("knowledge"):
            break
    return quests.gvar(actor, "START_POWER") == 2 and gained(actor, xp0, 1000)  # the Tool stays in hand (key_in_door)


def read_disks(actor: Actor) -> bool:
    """The holodisks used (each sets its GVAR, +100 XP, an archive entry; the disk stays)."""
    for pid, gvar in DISKS:
        if actor._count(pid) and not quests.gvar(actor, gvar):
            actor.use_on_self(pid)
            win32.wait_for(lambda g=gvar: quests.gvar(actor, g) != 0, 5, 0.25)
    actor.log.emit("disks", **{g: quests.gvar(actor, g) for _, g in DISKS})
    return True


def glow_gear(actor: Actor) -> bool:
    """The Combat Armor on, the best weapon ready (the Plasma Rifle at Energy Weapons 130)."""
    if actor._count(PID_COMBAT_ARMOR):
        actor.equip(PID_COMBAT_ARMOR, "armor")
    actor.ready_weapon()
    actor.holster(True)
    return True


def rads_down(actor: Actor) -> bool:
    """RadAway while the rads are 200 or more (the plan: a midnight at 600+ kills a CH 3 character)."""
    for _ in range(4):
        if radiation(actor) < 200 or not actor._count(PID_RADAWAY):
            break
        actor.use_on_self(PID_RADAWAY)
    actor.log.emit("rads", rads=radiation(actor), radaway=actor._count(PID_RADAWAY))
    return True


def key_and_tape(actor: Actor) -> bool:
    if not actor._count(PID_BROTHERHOOD_TAPE) or not actor._count(PID_YELLOW_KEY):
        the_tape(actor)
        if not actor._count(PID_YELLOW_KEY):
            glow_loot(actor, GLOW_BODY)
    return actor._count(PID_BROTHERHOOD_TAPE) > 0 and actor._count(PID_YELLOW_KEY) > 0


def take_key(pid: int, body: int) -> Callable[[Actor], bool]:
    return lambda a: a._count(pid) > 0 or (glow_loot(a, body) and a._count(pid) > 0)


def to_level_1(actor: Actor) -> bool:
    """Down by the yellow lift from Level 3: its e2 door is a plain metal door (no key needed)."""
    if at("GLOW1", 0)(actor):
        return True
    quests.ride_from(actor, YELLOW_CAB, "1", avoid=hot(actor))
    return at("GLOW1", 0)(actor)


def to_level_5(actor: Actor) -> bool:
    if not quests.gvar(actor, "WEAPONS_ARMED"):
        return True  # the robots stayed on: Level 5 is left out
    return lift(PID_BLUE_KEY, BLUE_DOOR, BLUE_CAB, "5", at("GLOW2", 1))(actor)


def level_5(actor: Actor) -> bool:
    return not at("GLOW2", 1)(actor) or loot_all(LEVEL5_LOCKERS)(actor)


def back_to_4(actor: Actor) -> bool:
    return not at("GLOW2", 1)(actor) or lift(PID_BLUE_KEY, BLUE_DOOR, BLUE_CAB, "4", at("GLOW2", 0))(actor)


IDEALIST_GLOW = [
    Step("to the Glow", lambda a: on_map(a, "GLOW") or travel(a, "the glow", 0).startswith("GLOW"), tries=6),
    Step("Rad-X", rad_x, checkpoint=True),
    Step("room for the finds", lambda a: loot.lighten(a, GLOW_ROOM)["spare"] >= GLOW_ROOM),
    Step("the rope on the beam", rope_on_the_beam),
    Step(
        "down the beam",
        lambda a: (
            on_map(a, "GLOW1")
            or quests.use_scenery(a, ("gent2lv1", "Beam"), lambda: on_map(a, "GLOW1"), "the beam", aims=GLOW_BEAM_AIMS)
        ),
        checkpoint=True,
    ),
    Step("the Brotherhood Tape and the Yellow key", key_and_tape, checkpoint=True, tries=3),
    Step("Level 1's chems", loot_all(GLOW1_CHEMS), optional=True),
    Step("yellow lift to Level 2", lift(PID_YELLOW_KEY, YELLOW_DOOR, YELLOW_CAB, "2", at("GLOW1", 1)), checkpoint=True),
    Step("the Red key", take_key(PID_RED_KEY, RED_KEY_BODY), checkpoint=True, tries=3),
    Step("yellow lift to Level 3", lift(PID_YELLOW_KEY, YELLOW_DOOR, YELLOW_CAB, "3", at("GLOW1", 2)), checkpoint=True),
    Step("red lift to Level 4", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "4", at("GLOW2", 0)), checkpoint=True),
    Step("Level 4's lockers", loot_all(LEVEL4_LOCKERS), checkpoint=True),
    Step("Zax: the downloads", zax_downloads, checkpoint=True, tries=3),
    Step("the Blue key", take_key(PID_BLUE_KEY, BLUE_KEY_BODY), checkpoint=True, tries=3),
    Step("the Blue key in its door", lambda a: key_in_door(a, PID_BLUE_KEY, BLUE_DOOR), tries=2),
    Step("red lift to Level 6", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "6", at("GLOW2", 2)), checkpoint=True),
    Step("the generator", the_generator, checkpoint=True, tries=2),
    Step("red lift to Level 4 again", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "4", at("GLOW2", 0)), checkpoint=True),
    Step("Zax: primary power", zax_power, checkpoint=True, tries=2),
    Step("Zax: the robots off", zax_robots, checkpoint=True, tries=1, optional=True),
    Step("blue lift to Level 5", to_level_5, checkpoint=True),
    Step("Level 5's lockers", level_5, checkpoint=True),
    Step("the disks read", read_disks),
    Step("the armor and the rifle", glow_gear),
    Step("blue lift to Level 4", back_to_4, checkpoint=True),
    Step("the rads", rads_down),
    Step("red lift to Level 3", lift(PID_RED_KEY, RED_DOOR, RED_CAB, "3", at("GLOW1", 2)), checkpoint=True),
    Step("down to Level 1", to_level_1, checkpoint=True, tries=3),
    Step("up the rubble", up_the_rubble, checkpoint=True),
    Step("out of the Glow", lambda a: a.snap().screen == "worldmap" or quests.leave_map(a)),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("the tape read", read_disks),
    Step("Cabbot: the tape", hand_in_the_tape, checkpoint=True),
    Step("the rads after the road", rads_down),
]
