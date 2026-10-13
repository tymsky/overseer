"""Idealist route tasks for brotherhood."""

import time
from collections.abc import Callable

from f1 import geometry, knowledge, loot, nav, quests, scripts, state, win32, world
from f1.actions import Actor
from f1.agent_brotherhood import (
    BOS_ENTRY_LIFT,
    BOS_MAIN_LIFT,
    BOS_MAIN_LIFT_UPPER,
    BOS_UP_LIFT_34,
    out_of_the_bunker,
)
from f1.engine_map import Obj
from f1.idealist_necropolis import (
    PID_DEANS_ELECTRONICS,
)
from f1.idealist_support import (
    PID_BOS_ARMOR,
    PID_HARDENED_POWER_ARMOR,
    PID_MOTIVATOR,
    PID_POWER_ARMOR,
    beside,
    experience,
    gained,
    hands_empty,
    read_book,
    rearm,
)
from f1.route_support import (
    Step,
    on_map,
    settle,
)

# Inside the Brotherhood's bunker, initiated (planned from the scripts).
# Talus refuses anyone with a weapon in a hand slot (TALUS weapon_check): both hands emptied first. Michael hands out
# what Talus's requisition grants, one item a pass. Kyle's armor job: his talk, Michael's motivator (offered once, on a
# second talk: Speech -10, sure at 121), the install, then a Tool on the parts (Repair +10): a Powered Armor on the
# floor. Vree: the disk, Rad-X and her terminal lesson (+15 Science) in one talk. Sophia's and Maxson's histories.
# Never: Maxson's money lines (thrown out), the supply room (SUPGRD shoot on sight), Steal, Rhombus's quarters.
TALUS_REQUISITION = [
    "#104",
    "#134",
    "#118",
    "#109",
    "#176",
    "#182",
    "#193",
]  # TALUS.MSG 104/134, 118, 109, 176, 182, 193

# TALUS.MSG 205, 213: the Laser Pistol (a second Powered Armor, 85 lb, adds nothing worn; the hub2 plan)
TALUS_REWARD = ["#205", "#213"]

MICHAEL_PICKUP = [
    "@146",
    "@185",
    "@208",
    "@169",
    "@221",  # no energy cells on his list: the dearest box, for trade (225 caps each)
    "@218",  # shells were not on his list live: the next dearest (200)
    "@168",
]  # MICHAEL.MSG 146, 185, goto48, goto49 passes, 221

MICHAEL_MOTIVATOR = [
    "@240",
    "@242",
    "@250",
    "@254",
    "@252",
]  # MICHAEL.MSG 240, 242, 250 (Speech -10 for a man), 254, 252

KYLE_PART = [
    "#104",
    "#106/139/110",
    "#115",
    "#118",
    "#120",
    "#124",
    "#130",
]  # KYLE.MSG 104, 139, 115, 118, 120, 124, 130: CALM_REBELS_7 1

KYLE_INSTALL = ["#164", "#300"]  # KYLE.MSG 164, 300: CALM_REBELS_7 2, Deans Electronics

VREE_ALL = [
    "#112",
    "#123",
    "#140",
    "#145",
    "#149/187",
    "#122",
    "#137",
    "#121",
    "#129",
]  # VREE.MSG 112, 123, 140, 145 (the disk), 149, 122 (Rad-X), 137, 121, 129 (TERM1)

SOPHIA_HISTORY = [
    "#304",
    "#316",
    "#332",
    "#366/382",
    "#330",
    "#360",
    "#315/349/369",
]  # SOPHIA.MSG 304, 316, 332, 366, 330, 360, 349: Brotherhood History (pid 215)

MAXSON_HISTORY = [
    "#349",
    "#332",
    "#335",
    "#344",
    "#318/331/336/348",
]  # MAXSON.MSG 349/332/335, 344, 348: Maxson's History (pid 216)

PID_VREES_DISK, PID_SOPHIAS_DISK, PID_MAXSONS_DISK = 194, 215, 216

PID_BIG_BOOK = 73

ARMOR_PARTS, NEW_POWER_ARMOR = 23277, 22475  # BROHD34 e0: ARMOR (Repair; a Tool +10), the armor KYLE drops

TERM1 = 20104  # BROHD34 e0: open once Vree has used it (vree22's timer)

BOS_DISKS = ((PID_VREES_DISK, "VREE_DISK"), (PID_SOPHIAS_DISK, "SOPHIA_DISK"), (PID_MAXSONS_DISK, "MAXSON_DISK"))


def bos_floor(name: str, elevation: int | None = None) -> Callable[[Actor], bool]:
    """To a floor of the bunker by its lifts (elev0/elev1: BROHD12 e0 '1', e1 '2', BROHD34 e0 '3', e1 '4')."""
    key = {("BROHD12", 0): "1", ("BROHD12", 1): "2", ("BROHD34", 0): "3", ("BROHD34", 1): "4"}[(name, elevation or 0)]

    def go(actor: Actor) -> bool:
        def there() -> bool:
            return on_map(actor, name) and actor.snap().elevation == (elevation or 0)

        for _ in range(3):
            if there():
                return True
            s = actor.snap()
            if on_map(actor, "BROHDENT"):
                quests.ride_from(actor, BOS_ENTRY_LIFT, "1")
            elif on_map(actor, "BROHD34"):
                quests.ride_from(actor, BOS_UP_LIFT_34, key)
            elif s.elevation == 1:
                quests.ride_from(actor, BOS_MAIN_LIFT_UPPER, key)
            else:
                quests.ride_from(actor, BOS_MAIN_LIFT, key)
        return there()

    return go


def talus(actor: Actor) -> bool:
    """The requisition (and the initiate's quest) in one talk; his reward first when the initiate is already free."""
    xp0 = experience(actor)
    if quests.gvar(actor, "FIND_LOST_INITIATE") == 2:
        quests.talk(actor, "talus", TALUS_REWARD, strict=True)
        settle(actor)
    quests.talk(actor, "talus", TALUS_REQUISITION, strict=True)
    settle(actor)
    actor.log.emit("talus", find=quests.gvar(actor, "FIND_LOST_INITIATE"), xp=experience(actor) - xp0)
    return True


def michael(actor: Actor) -> bool:
    """Everything owed handed over (one item a pass, the plan asks again while he has more)."""
    before = len(actor.items())
    for _ in range(2):
        quests.talk(actor, "michael", MICHAEL_PICKUP, strict=True)
        settle(actor)
    actor.log.emit("michael", armor=actor._count(PID_BOS_ARMOR), power=actor._count(PID_POWER_ARMOR))
    return actor._count(PID_BOS_ARMOR) > 0 or len(actor.items()) > before


def read_books(actor: Actor) -> bool:
    """The Big Book of Science and the Dean's Electronics carried (1 game hour each at IN 10; points while low)."""
    for pid in (PID_BIG_BOOK, PID_DEANS_ELECTRONICS):
        for _ in range(3):
            if not actor._count(pid):
                break
            read_book(actor, pid)
    actor.log.emit("books", science=actor.skills().get("science"), repair=actor.skills().get("repair"))
    return True


def use_disks(actor: Actor) -> bool:
    for pid, gvar in BOS_DISKS:
        if actor._count(pid) and not quests.gvar(actor, gvar):
            actor.use_on_self(pid)
            win32.wait_for(lambda g=gvar: quests.gvar(actor, g) != 0, 5, 0.25)
    actor.log.emit("disks", **{g: quests.gvar(actor, g) for _, g in BOS_DISKS})
    return True


def sophia(actor: Actor) -> bool:
    if not actor._count(PID_SOPHIAS_DISK) and not quests.gvar(actor, "SOPHIA_DISK"):
        quests.talk(actor, "sophia", SOPHIA_HISTORY, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "SOPHIA_DISK"))


def vree(actor: Actor) -> bool:
    if (
        not quests.gvar(actor, "DESTROY_MASTER_6")
        or not actor._count(PID_VREES_DISK)
        and not quests.gvar(actor, "VREE_DISK")
    ):
        quests.talk(actor, "vree", VREE_ALL, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "DESTROY_MASTER_6")) and bool(quests.gvar(actor, "VREE_DISK"))


def kyle_part(actor: Actor) -> bool:
    if quests.gvar(actor, "CALM_REBELS_7") >= 1:
        return True
    quests.talk(actor, "kyle", KYLE_PART, strict=True)
    settle(actor)
    return quests.gvar(actor, "CALM_REBELS_7") >= 1


def the_motivator(actor: Actor) -> bool:
    if actor._count(PID_MOTIVATOR) or quests.gvar(actor, "CALM_REBELS_7") >= 2:
        return True
    quests.talk(actor, "michael", MICHAEL_MOTIVATOR, strict=True)
    settle(actor)
    return actor._count(PID_MOTIVATOR) > 0


def kyle_install(actor: Actor) -> bool:
    if quests.gvar(actor, "CALM_REBELS_7") >= 2:
        return True
    quests.talk(actor, "kyle", KYLE_INSTALL, strict=True)
    settle(actor)
    return quests.gvar(actor, "CALM_REBELS_7") >= 2


def the_repair(actor: Actor) -> bool:
    """The Repair skill on the armour's parts; a plain failure is tried again while Repair is 75 or more."""
    if quests.gvar(actor, "CALM_REBELS_7") >= 4:
        return True
    xp0 = experience(actor)
    for _ in range(5):
        if quests.gvar(actor, "CALM_REBELS_7") >= 4:
            break
        line = actor.mem.glob("disp_start")

        def tried(line: int = line) -> bool:  # a hover's look-at line is not an answer
            said = [m for m in state.messages_since(actor.mem, line) if not m.startswith("You see")]
            return quests.gvar(actor, "CALM_REBELS_7") >= 3 or bool(said)

        beside(actor, ARMOR_PARTS)
        s = actor.snap()
        parts = next(
            (t.address for t in world.things(actor.mem) if t.tile == ARMOR_PARTS and t.elevation == s.elevation
             and scripts.script_of(actor.mem, t.address) == "armor"),
            None,
        )  # fmt: skip
        on = frozenset({parts}) if parts else frozenset()  # the parts lie on a Table on the same hex
        # the Repair skill, not the Tool: the Tool used on the parts answered with the no-effect message ten times, the
        # skill repaired them at once (Repair 81, live)
        actor.use_skill_on("repair", ARMOR_PARTS, tried, targets=on)
        actor.log.emit(
            "repair", calm_rebels_7=quests.gvar(actor, "CALM_REBELS_7"), msgs=state.messages_since(actor.mem, line)
        )
        time.sleep(0.5)
    return quests.gvar(actor, "CALM_REBELS_7") >= 4 and gained(actor, xp0, 500)


def the_new_armor(actor: Actor) -> bool:
    """The Powered Armor Kyle left on the floor, put on (ST +3: carry 250, live). When lighten() has
    nothing more to let go, the heaviest single items are set down for a moment and taken back once it is worn."""
    aside: list[int] = []
    spot = actor.snap().dude.tile
    if not actor._count(PID_POWER_ARMOR):
        need = loot.weight(PID_POWER_ARMOR)  # 85 lb: the worn armour comes off first so it may go too
        if loot.spare_weight(actor) < need + loot.WEIGHT_SLACK:
            actor.unequip("armor")
            loot.lighten(actor, need, coming=knowledge.item_cost(PID_POWER_ARMOR))
        spot = actor.snap().dude.tile
        aside = set_aside(actor, need + loot.WEIGHT_SLACK)
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(
            actor, {t for t in geometry.ring(NEW_POWER_ARMOR, 1) if t not in obs.blocked} or {NEW_POWER_ARMOR}, 60
        )
        actor.pick_up(NEW_POWER_ARMOR, PID_POWER_ARMOR)
    if actor._count(PID_POWER_ARMOR):
        actor.equip(PID_POWER_ARMOR, "armor")
    if aside and actor.snap().dude.tile == spot:  # off the pile: the Agent's own sprite would take the clicks
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(spot, 2) if t not in obs.blocked}, 20)
    back = [pid for pid in aside if actor.pick_up(spot, pid).ok]
    if aside:
        actor.log.emit("set_aside", tile=spot, items=[knowledge.proto_name(p) for p in aside], back=len(back))
    return actor._count(PID_POWER_ARMOR) > 0


def set_aside(actor: Actor, need: int) -> list[int]:
    """Single items (no stacks: a stack's pick-up from the floor is unmeasured), heaviest first, dropped where the
    Agent stands until `need` lb are spare: Kyle's Powered Armor was refused with lighten() done and 87 lb spare by
    the prototypes (the bunker). Their pids, to be taken back."""
    from f1.actions import ITEM_EQUIPPED

    items = [
        (loot.item_weight(actor.mem, item, pid), pid)
        for item, pid, qty in actor.items()
        if qty == 1
        and not actor.mem.u32(item + Obj.FLAGS) & ITEM_EQUIPPED
        and pid not in (PID_POWER_ARMOR, PID_HARDENED_POWER_ARMOR)
    ]
    aside = []
    for w, pid in sorted(items, reverse=True):
        if loot.spare_weight(actor) >= need or w <= 0:
            break
        if loot.drop(actor, pid, 1):
            aside.append(pid)
    return aside


def maxson(actor: Actor) -> bool:
    if not actor._count(PID_MAXSONS_DISK) and not quests.gvar(actor, "MAXSON_DISK"):
        quests.talk(actor, "maxson", MAXSON_HISTORY, strict=True)
        settle(actor)
    use_disks(actor)
    return bool(quests.gvar(actor, "MAXSON_DISK"))


IDEALIST_BOS = [
    Step("into the bunker", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("the books read", read_books),
    Step("hands empty for Talus", hands_empty),
    Step("Talus: the requisition", talus, checkpoint=True),
    Step("Michael: the armor and the ammo", michael, checkpoint=True),
    Step("re-armed", rearm),
    Step("to the library", bos_floor("BROHD34"), checkpoint=True, tries=3),
    Step("Sophia: the history", sophia, checkpoint=True),
    Step("Vree: the disk, Rad-X, the lesson", vree, checkpoint=True),
    # Vree's terminal (+15 Science) is left out: her line 129 shows once, and her use of TERM1 after it did not
    # happen live ("denied" in 9 tries, the line gone); Science stood at 110 after the books anyway
    Step("Kyle: the armor job", kyle_part, checkpoint=True),
    Step("up to Michael", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("Michael: the motivator", the_motivator, checkpoint=True),
    Step("down to Kyle", bos_floor("BROHD34"), checkpoint=True, tries=3),
    Step("Kyle: the install", kyle_install, checkpoint=True),
    Step("the armor repaired", the_repair, checkpoint=True),
    # Kyle's Powered Armor weighs 85 lb: the worn armour off, lighten() (the old armour, junk), then picked up and
    # worn: ST 9, carry 250, AC 33 (live)
    Step("the Powered Armor on", the_new_armor, checkpoint=True),
    Step("to the Elders", bos_floor("BROHD34", 1), checkpoint=True, tries=3),
    Step("Maxson: the history", maxson, checkpoint=True),
    Step("re-armed again", rearm),
    Step("out of the bunker", out_of_the_bunker, checkpoint=True, tries=3),
]
