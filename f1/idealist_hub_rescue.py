"""Idealist Hub investigations, the sheriff raid and prisoner rescue."""

from collections.abc import Callable

from f1 import dialogue, geometry, knowledge, loot, nav, quests, scripts, session, state, win32
from f1.actions import PID_STIMPAK, Actor
from f1.agent_brotherhood import (
    out_of_the_bunker,
)
from f1.agent_military import (
    pick_lock,
)
from f1.idealist_brotherhood import (
    bos_floor,
    michael,
    talus,
)
from f1.idealist_hub import (
    door_at,
    hub_map,
    hub_rest_until,
    irwin_paid,
    irwins_job,
    locked,
    the_farm,
)
from f1.idealist_support import (
    beside,
    experience,
    gained,
    hands_empty,
)
from f1.idealist_trading import (
    VANCE_SELL,
    from_merchant,
)
from f1.route_support import (
    Step,
    heal_below,
    in_town,
    on_map,
    settle,
    travel,
)

# The Hub, second visit (planned from the scripts): the farm, Decker by the
# Sheriff's raid, the lost initiate's captors, the Deathclaw chain (Rutger, Butch, Beth, Harold, Slappy, the lair,
# Butch, Rutger), then Talus's reward. Every Hub critter is team 1 and a hit on a citizen sets ENEMY_HUB: each fight
# names its foes (only=). The Powered Armor (DT 12) stops every gun in the Hub but crits; Kane (four punches a turn)
# goes before Decker in the raid.
RUTGER_WORK = ["#104/296/307", "#103/295/306"]  # RUTGER.MSG 296/307, 295/306

BUTCH_JOB = [
    "#107",
    "#111",
    "#134/164",
    "#137",
    "#144",
    "#149",
    "#152/156",
]  # BUTCH.MSG 107, 111, 134, 137, 144, 149, 152: map_var 41 1, HUB_FILLER_29 1

BETH_DEATHCLAW = [
    "#102",
    "#110",
    "#209",
    "#244",
    "#255/260",
]  # HBETH.MSG 102, 110, 209, 244, 260: HUB_FILLER_29 2

BETH_DECKER = [
    "#102",
    "#112",
    "#333",
]  # 6 Stimpaks

HAROLD_DEATHCLAW = [
    "#225",
    "#231",
    "#234",
    "#236",
    "#238",
    "#240",
    "#220",
]  # HAROLD.MSG 225, 231, 234, 236, 238, 240, 220: HUB_FILLER_29 3

SLAPPY_LAIR = [
    "#109",
    "#117",
    "#126",
    "#129",
]  # SLAPPY.MSG 109, 117, 126, 129: +800 XP, DETHCLAW

MUTANT = ["#105", "#107", "#109"]  # DCMUTANT.MSG 105, 107, 109: MISSING_CARAVAN 2

BUTCH_REPORT = [
    "#264",
    "#222/250",
    "#225",
]  # BUTCH.MSG 264, 222, 225: map_var 41 3

RUTGER_REWARD = [
    "#241",
    "#197/221/226",
    "#214/231",
]  # RUTGER.MSG 241, 197, 214: 1000 XP, 800 caps, karma +5

KANE_TO_DECKER = [
    "#146",
    "#215/222/331/341/343",
    "#228",
    "#240",
    "#313",
    "#331",
]  # KANE.MSG 146, 215, 228, 240, 313 (Speech 0), 331

DECKER_REFUSE = ["@104", "#108", "#111", "#115"]  # DECKER.MSG 104, 108, 111, 115

JUSTIN_REPORT = [
    "#161",
    "#105",
    "#171",
    "#117",
    "#124",
]  # JUSTIN.MSG 161, 105, 171, 117, 124: +300 caps, the raid

JONATHAN = ["#106"]  # MISSBRO.MSG 106: FIND_LOST_INITIATE 2 as the talk opens

PID_MUTANT_DISK = 196

CAPTORS_DOOR, JONATHANS_DOOR = 19528, 20140  # HUBOLDTN e0: WOODDOOR (open), DOOR.INT (locked)

HUB2_STIMPAKS = 20  # carried before the captors and the Deathclaw (Vance: 105 caps each)


def kane_to_decker(actor: Actor) -> bool:
    """Kane takes the player to Decker; Decker's job refused (map_var 46 1). Kane's map_var 11/44 at 1 mean he
    attacks on the fourth talk: the Decker part is closed then."""
    if quests.mvar(actor, 46) >= 1 or quests.gvar(actor, "DECKER_STATUS"):
        return True
    if quests.mvar(actor, 11) or quests.mvar(actor, 44):
        actor.log.emit("decker", why="kane closed", mvar11=quests.mvar(actor, 11), mvar44=quests.mvar(actor, 44))
        return True
    actor.log.emit("decker", mvar45=quests.mvar(actor, 45))
    hands_empty(actor)
    quests.talk(actor, "kane", KANE_TO_DECKER, strict=True)
    win32.wait_for(lambda: actor.snap().elevation == 1 or dialogue.read(actor.mem).active, 15, 0.25)
    for _ in range(3):
        if quests.mvar(actor, 46) >= 1:
            break
        quests.talk(actor, "decker", DECKER_REFUSE, strict=True)
        settle(actor)
    win32.wait_for(lambda: actor.snap().elevation == 0, 15, 0.25)
    return quests.mvar(actor, 46) >= 1 and actor.snap().elevation == 0


def the_raid(actor: Actor) -> bool:
    """The Sheriff told, the raid on Decker's floor: Kane first, then Decker; the guards surrender."""
    if quests.gvar(actor, "DECKER_STATUS") == 1:
        return True
    if quests.mvar(actor, 46) < 1:
        return True  # Decker's part closed
    if actor.snap().elevation == 0:
        heal_below(actor, actor.max_hp - 5)
        hands_empty(actor)
        quests.talk(actor, "justin", JUSTIN_REPORT, strict=True)
        win32.wait_for(lambda: actor.snap().elevation == 1, 15, 0.25)
    actor.ready_weapon()
    for foes in (("Kane",), ("Decker",)):
        win32.wait_for(actor.in_combat, 10, 0.25)
        if actor.in_combat():
            actor.fight(only=foes)
    # the combat ended with Kane dead and Decker at 50 HP, standing back (live): combat mode by hand
    # ("a") and Decker alone, until he is down
    for _ in range(3):
        if quests.gvar(actor, "DECKER_STATUS") == 1 or actor.snap().elevation != 1:
            break
        if not any(
            scripts.script_of(actor.mem, c.address) == "decker" and not c.dead for c in state.critters(actor.mem)
        ):
            break
        if not actor.in_combat():
            session.press("a")
            win32.wait_for(actor.in_combat, 5, 0.2)
        actor.fight(only=("Decker",))
    actor.log.emit("raid", decker=quests.gvar(actor, "DECKER_STATUS"), enemy_hub=quests.gvar(actor, "ENEMY_HUB"))
    win32.wait_for(lambda: actor.snap().elevation == 0, 30, 0.5)  # the map update takes everyone back down
    actor.holster(True)
    return quests.gvar(actor, "DECKER_STATUS") == 1 and not quests.gvar(actor, "ENEMY_HUB")


def sheriffs_reward(actor: Actor) -> bool:
    if quests.gvar(actor, "DECKER_STATUS") != 1:
        return True
    xp0 = experience(actor)
    hands_empty(actor)
    who = "kenny" if quests.gvar(actor, "GREENE_DEAD") else "justin"
    quests.talk(actor, who, [], strict=True)
    settle(actor)
    return gained(actor, xp0, 1400) or quests.mvar(actor, 53) == 0


def stimpaks_to(n: int) -> Callable[[Actor], bool]:
    def buy(actor: Actor) -> bool:
        if actor._count(PID_STIMPAK) >= n:
            return True
        from_merchant("vance", "HUBOLDTN", PID_STIMPAK, n, VANCE_SELL)(actor)
        return True  # what the caps allowed

    return buy


def the_captors(actor: Actor) -> bool:
    """Their door opened from outside by day, the four fought in the doorway (they attack on sight)."""
    if quests.gvar(actor, "FIND_LOST_INITIATE") >= 2:
        return True
    left = [
        c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
    ]
    if not left:
        return True
    heal_below(actor, actor.max_hp - 5)
    actor.ready_weapon()
    foes = ("Rutger", "Guard", "Vinnie")
    for _ in range(6):
        if actor.in_combat():
            actor.fight(only=foes)
            continue
        left = [
            c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
        ]
        if not left:
            break
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        if CAPTORS_DOOR in obs.doors:
            beside(actor, CAPTORS_DOOR)
            nav.open_door(actor, CAPTORS_DOOR)
        else:
            nav.go_to(actor, {t for t in geometry.ring(left[0].tile, 2) if t not in obs.blocked}, max_steps=20)
        win32.wait_for(actor.in_combat, 6, 0.25)
    left = [
        c for c in state.critters(actor.mem) if scripts.script_of(actor.mem, c.address) == "hubcaptr" and not c.dead
    ]
    actor.log.emit("captors", left=len(left), enemy_hub=quests.gvar(actor, "ENEMY_HUB"), hp=actor.snap().dude.hp)
    actor.holster(True)
    return not left and not quests.gvar(actor, "ENEMY_HUB")


def jonathan(actor: Actor) -> bool:
    """His room's door picked (DOOR.INT), then his talk: FIND_LOST_INITIATE 2 as it opens."""
    if quests.gvar(actor, "FIND_LOST_INITIATE") >= 2:
        return True
    door = door_at(actor, JONATHANS_DOOR)
    if door is not None and locked(actor, door):
        beside(actor, JONATHANS_DOOR)
        if not nav.open_door(actor, JONATHANS_DOOR):
            pick_lock(actor, JONATHANS_DOOR, door.address, tries=8)
            nav.open_door(actor, JONATHANS_DOOR)
    quests.talk(actor, "missbro", JONATHAN, strict=True)
    settle(actor)
    return quests.gvar(actor, "FIND_LOST_INITIATE") >= 2


def the_deathclaw(actor: Actor) -> bool:
    """The lair's Deathclaw (225 HP, three claws a turn): it comes on sight; met at full HP."""
    if not on_map(actor, "DETHCLAW"):
        return quests.gvar(actor, "HUB_FILLER_29") >= 5 or actor._count(PID_MUTANT_DISK) > 0
    heal_below(actor, actor.max_hp - 5)
    actor.ready_weapon()
    for _ in range(8):
        claw = quests.find_critter(actor, "dethclaw")
        if claw is None:
            break
        if actor.in_combat():
            actor.fight(only=("Deathclaw",))
            continue
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        nav.go_to(actor, {t for t in geometry.ring(claw.tile, 8) if t not in obs.blocked}, max_steps=12)
        win32.wait_for(actor.in_combat, 4, 0.25)
    claw = quests.find_critter(actor, "dethclaw")
    actor.log.emit("deathclaw", dead=claw is None, hp=actor.snap().dude.hp)
    return claw is None


def the_mutant(actor: Actor) -> bool:
    """The dying mutant's talk gives his disk (MISSING_CARAVAN 2); his body holds it if he died."""
    if not actor._count(PID_MUTANT_DISK):
        actor.holster(True)
        if quests.find_critter(actor, "dcmutant") is not None:
            quests.talk(actor, "dcmutant", MUTANT, strict=True)
            settle(actor)
        if not actor._count(PID_MUTANT_DISK):
            body = next(
                (
                    c
                    for c in state.critters(actor.mem)
                    if c.dead and knowledge.proto_name(c.pid) == "Super Mutant Guard"  # a dead one keeps no script
                ),
                None,
            )
            if body is not None:
                # over the limit the loot screen refuses each drag without a word (a full run: 258 of 250 lb after
                # the Deathclaw, "nothing taken" twice)
                if loot.spare_weight(actor) < loot.weight(PID_MUTANT_DISK):
                    loot.lighten(actor, loot.weight(PID_MUTANT_DISK))
                beside(actor, body.tile)
                actor.loot(body.tile)
    if actor._count(PID_MUTANT_DISK) and not quests.gvar(actor, "MUTANT_DISK"):
        actor.use_on_self(PID_MUTANT_DISK)
        win32.wait_for(lambda: quests.gvar(actor, "MUTANT_DISK") != 0, 5, 0.25)
    return actor._count(PID_MUTANT_DISK) > 0


def far_go(name: str, plan: list[str], done) -> Callable[[Actor], bool]:
    def talk(actor: Actor) -> bool:
        if done(actor):
            return True
        if not hub_map(actor, "HUBDWNTN"):
            return False
        hands_empty(actor)
        quests.talk(actor, name, plan, strict=True)
        settle(actor)
        return done(actor)

    return talk


def hub_talk(where: str, name: str, plan: list[str], done) -> Callable[[Actor], bool]:
    def talk(actor: Actor) -> bool:
        if done(actor):
            return True
        if not hub_map(actor, where):
            return False
        hands_empty(actor)
        quests.talk(actor, name, plan, strict=True)
        settle(actor)
        return done(actor)

    return talk


def hub2_ready(actor: Actor) -> bool:
    actor.ready_weapon()
    actor.holster(True)
    return True


IDEALIST_HUB2 = [
    Step("to the Hub", lambda a: in_town(a, travel(a, "the hub", 1)), checkpoint=True, tries=6),
    Step("downtown", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step(
        "Rutger: work",
        far_go("rutger", RUTGER_WORK, lambda a: quests.mvar(a, 34) >= 1 or quests.mvar(a, 41) >= 1),
        checkpoint=True,
    ),
    Step("Butch: the job", far_go("butch", BUTCH_JOB, lambda a: quests.mvar(a, 41) >= 1), checkpoint=True),
    Step("Irwin: the farm", irwins_job, checkpoint=True),
    Step("the farm's raiders", the_farm, checkpoint=True, tries=3),
    Step("Irwin: paid", irwin_paid, checkpoint=True),
    Step("downtown again", lambda a: hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("Kane: to Decker", kane_to_decker, checkpoint=True, tries=2),
    Step("the Sheriff's raid", the_raid, checkpoint=True, tries=2),
    Step("the Sheriff's reward", sheriffs_reward, checkpoint=True),
    Step("by day for Beth", lambda a: hub_rest_until(a, 7, 15), checkpoint=True),
    Step(
        "Beth: the Deathclaw",
        far_go("hbeth", BETH_DEATHCLAW, lambda a: quests.gvar(a, "HUB_FILLER_29") >= 2),
        checkpoint=True,
    ),
    # Beth's six stimpaks once (Beth67); her later talks only repeat themselves (live): one try, done either way
    Step("Beth: Decker's death", lambda a: far_go("hbeth", BETH_DECKER, lambda _a: False)(a) or True, tries=1),
    Step("Old Town", lambda a: hub_map(a, "HUBOLDTN"), checkpoint=True),
    Step("stimpaks from Vance", stimpaks_to(HUB2_STIMPAKS), optional=True),
    Step(
        "Harold: the Deathclaw",
        hub_talk("HUBOLDTN", "harold", HAROLD_DEATHCLAW, lambda a: quests.gvar(a, "HUB_FILLER_29") >= 3),
        checkpoint=True,
    ),
    Step("the captors", the_captors, checkpoint=True, tries=2),
    Step("Brother Jonathan", jonathan, checkpoint=True, tries=2),
    Step(
        "Slappy: to the lair",
        hub_talk(
            "HUBOLDTN", "slappy", SLAPPY_LAIR, lambda a: on_map(a, "DETHCLAW") or quests.gvar(a, "HUB_FILLER_29") >= 5
        ),
        checkpoint=True,
    ),
    Step("the Deathclaw", the_deathclaw, checkpoint=True, tries=2),
    Step("the dying mutant", the_mutant, checkpoint=True),
    Step("out of the lair", lambda a: not on_map(a, "DETHCLAW") or quests.leave_map(a)),
    Step(
        "back to Downtown", lambda a: on_map(a, "HUB") or in_town(a, travel(a, "the hub", 1)), checkpoint=True, tries=6
    ),
    Step("Butch: the report", far_go("butch", BUTCH_REPORT, lambda a: quests.mvar(a, 41) >= 3), checkpoint=True),
    Step("Rutger: the reward", far_go("rutger", RUTGER_REWARD, lambda a: quests.mvar(a, 41) >= 4), checkpoint=True),
    Step("ready", hub2_ready),
    Step("to the Brotherhood", lambda a: in_town(a, travel(a, "brotherhood", 0)), checkpoint=True, tries=6),
    Step("into the bunker", bos_floor("BROHD12"), checkpoint=True, tries=3),
    Step("hands empty for Talus", hands_empty),
    Step("Talus: the reward", talus, checkpoint=True),
    Step("Michael: the reward", michael, checkpoint=True, optional=True),
    Step("re-armed", hub2_ready),
    Step("out of the bunker", out_of_the_bunker, checkpoint=True, tries=3),
]
