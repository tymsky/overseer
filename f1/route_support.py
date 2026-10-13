"""Step records and reusable route predicates, travel, healing and peaceful arrival."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from f1 import dialogue, geometry, quests, win32
from f1.actions import PID_STIMPAK, Actor
from f1.engine_map import Obj


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[Actor], bool]
    checkpoint: bool = False  # save after it succeeds
    tries: int = 3  # tries in place after failures, and loads of the last checkpoint after deaths
    final: bool = False  # the game's end: the main menu after it is no death
    optional: bool = False  # its failure stands (a roll's first outcome): skipped, and the route goes on
    exploit: bool = False  # a trick past the game's rules; run only for a character whose build allows exploits


def heal_up(actor: Actor, below: float = 0.75) -> bool:
    """Rest until healed when HP is low and the Agent is on a map (not near foes: the game refuses then). Long
    trips at level 3 lost four fights in a row on the way from Necropolis to Vault 13 after Vault 12."""
    s = actor.snap()
    low = s.screen == "map" and s.dude is not None and s.dude.hp < below * actor.max_hp and not actor.in_combat()
    if low:  # the spare stimpaks first: a rest costs days at a low healing rate (quests.spare_stimpaks)
        quests.spare_stimpaks(actor, actor.max_hp)
        low = actor.snap().dude.hp < below * actor.max_hp
    if low and not actor.rest("until healed").ok:  # no rest inside the Military Base (the Pip-Boy has no button)
        win32.wait_for(lambda: actor.snap().screen == "map", 5, 0.2)
        heal_below(actor, int(below * actor.max_hp))
        if actor.snap().dude.hp < below * actor.max_hp and actor._count(PID_FIRST_AID_KIT):
            actor.use_on_self(PID_FIRST_AID_KIT)
    return True


def travel(actor: Actor, town: str, section: int = 0, flee_all: bool = False) -> str:
    """quests.go without its own reloads: a death on the road fails the step and the runner reloads (and redoes
    the steps since its last checkpoint). Heals first while still on a map, and takes up the best weapon (one bought
    or looted since, or the other when this one has no rounds left)."""
    heal_up(actor)
    if actor.snap().screen == "map" and not actor.in_combat():
        actor.ready_weapon()
    if not flee_all:  # nothing to shoot with (a spear, fists): the road's fights are fled, not fought
        from f1 import weapons

        best = next(iter(actor.weapon_options()), None)
        flee_all = best is None or not weapons.uses_ammo(best.pid) or not best.rounds
    return quests.go(actor, town, section, recover=False, flee_all=flee_all)


def on_map(actor: Actor, name: str) -> bool:
    return name.upper() in actor.snap().map_name


def elevation_becomes(actor: Actor, what: str, names: tuple[str, ...], elevation: int, map_name: str = "") -> bool:
    done = lambda: actor.snap().elevation == elevation and (not map_name or on_map(actor, map_name))
    return done() or quests.use_scenery(actor, names, done, what)


DAY = 864000  # game_time ticks (0.1 s) a day

JUNKTOWN_GATE = ["assblow#159"]  # ASSBLOW.MSG 159: the night guard lets the polite one in

# A guard who sees a drawn gun warns (the gun goes away right after)
TOWN_GREETINGS = JUNKTOWN_GATE + ["regulatr#104"]  # REGULATR.MSG 104: Adytum's gate


def hour(actor: Actor) -> float:
    return actor.snap().game_time % DAY / 36000


def zone(centres) -> frozenset[int]:
    """The hexes within each (centre, radius). With a hex of margin GLOW1 had no way through (live); the
    walk keeps its steps short (go_to with `avoid`), so the engine's own path finder keeps to ours."""
    return frozenset(t for c, r in centres for k in range(r + 1) for t in geometry.ring(c, k))


PID_FIRST_AID_KIT = 0x2F


def heal_below(actor: Actor, hp: int) -> None:
    for _ in range(3):
        s = actor.snap()
        if s.dude is None or s.dude.hp >= hp or not actor._count(PID_STIMPAK) or actor.in_combat():
            return
        actor.use_on_self(PID_STIMPAK)


def wearing(actor: Actor, pid: int) -> bool:
    from f1.actions import OBJECT_WORN

    return any(p == pid and actor.mem.u32(i + Obj.FLAGS) & OBJECT_WORN for i, p, _ in actor.items())


def hands_empty(actor: Actor) -> bool:
    return actor.unequip("left").ok and actor.unequip("right").ok


def settle(actor: Actor, plan: list[str] | None = None) -> bool:
    """Close a talk someone started (a guard about the drawn gun, say): the plan's lines, else a polite exit. True
    when no dialogue is left open."""
    for _ in range(4):
        d = dialogue.read(actor.mem)
        if not d.active:
            return True
        if plan and dialogue.converse_strict(actor.mem, plan, log=actor.log) is not None:
            continue
        d = dialogue.read(actor.mem)
        if not d.active or not d.options:
            continue
        i = dialogue.leave(d.options)
        i = i if i is not None else len(d.options) - 1
        actor.log.emit(
            "dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i], settle=True
        )
        dialogue.choose(actor.mem, i)
    return not dialogue.read(actor.mem).active


def in_town(actor: Actor, arrived: str) -> bool:
    """Arrived in a peaceful town: settle a talk begun at the gate, then the gun away (guards stop the armed)."""
    if not arrived.endswith((".MAP", ".SAV")):
        return False
    time.sleep(1.0)
    settle(actor, TOWN_GREETINGS)
    actor.holster(True)
    return settle(actor, TOWN_GREETINGS)
