"""Quest routines: small scripted plans on top of the verified actions, with checkpoints and recovery.

python -m f1.quests hunt [KIND]     clear a map of a wild kind (default Radscorpion), saving after kills and
                                    loading the last save when the player dies or runs out of health
python -m f1.quests leave           walk out of this map to the world map
python -m f1.quests exit MAP        walk to this level's exit grids that lead to MAP (e.g. HALLDED)
python -m f1.quests visit TOWN [N]  travel to a town and enter its entrance N (TownHotSpots order, default 0)
python -m f1.quests go TOWN [N]     the same, loading slot 1 and trying again when the Agent dies on the way
python -m f1.quests talk NAME "phrase|phrase"   walk up to a critter by name and follow a dialogue plan
"""

import datetime
import sys
import time
from functools import cache
from pathlib import Path

from f1 import (
    dialogue,
    flows,
    geometry,
    knowledge,
    loot,
    nav,
    odds,
    paths,
    perception,
    scripts,
    session,
    state,
    win32,
    world,
    worldmap,
)
from f1 import engine_map as em
from f1.actions import PID_STIMPAK, Actor
from f1.telemetry import EventLog


def gvar(actor: Actor, index: int | str) -> int:
    """A global variable by number or by its VAULT13.GAM name."""
    number = knowledge.gvar_index(index) if isinstance(index, str) else index
    ptr = actor.mem.u32(em.GLOBALS["game_global_vars"].address)
    return actor.mem.i32(ptr + 4 * number)


def mvar(actor: Actor, index: int) -> int:
    """The current map's variable `index` (a script's map_var(index): map.c map_global_vars); 0 when out of range."""
    if not 0 <= index < actor.mem.glob("num_map_global_vars"):
        return 0
    return actor.mem.i32(actor.mem.u32(em.GLOBALS["map_global_vars"].address) + 4 * index)


def foes_of(actor: Actor, kind: str) -> list:
    return [c for c in actor.enemies() if kind in knowledge.proto_name(c.pid)]


OFF_MAP_S = 15.0  # a hunt that sees another screen this long is stuck there and loads its checkpoint


HEAL_BELOW = 0.8  # of max HP: a hunt's next fight starts above it, when rest or stimpaks allow
FIGHT_ABOVE = 0.5  # of max HP: below it and not to be healed, the hunt stops rather than start another fight
BACK_OFFS = 3  # a hunt's tries from another side before a fight against two is refused
ODDS_TO_FIGHT = 0.8  # f1.odds: a hunt's fight begins only at this chance of winning (fights on computed odds)


STIMPAKS_KEPT = 4  # for fights; those above it heal first: a rest costs days (below)


def spare_stimpaks(actor: Actor, target: float, keep: int = STIMPAKS_KEPT) -> None:
    """Stimpaks above `keep` used until the HP reaches `target`. Resting heals the healing rate every 3 hours: the
    Idealist's rate 2 turned the caves' two heals into about three days (a run ended on day 14.6, not 11.6)."""
    while actor.snap().dude.hp < target and actor._count(PID_STIMPAK) > keep and not actor.in_combat():
        if not actor.use_on_self(PID_STIMPAK).ok:
            break


def heal(actor: Actor, below: float = HEAL_BELOW) -> bool:
    """HP up to `below` of the maximum: the spare stimpaks first, then rest until healed (critter.cc
    critter_can_obj_dude_rest refuses it only near a living critter the player hit, in a town's maps; the radscorpion
    caves are Shady Sands' by xlate_town_table), else the kept stimpaks. True when the HP got there."""
    target = below * actor.max_hp
    spare_stimpaks(actor, target)
    if actor.snap().dude.hp >= target:
        return True
    if not actor.in_combat() and not actor.rest("until healed").ok:
        win32.wait_for(lambda: actor.snap().screen == "map", 5, 0.2)
    while actor.snap().dude.hp < target and actor._count(PID_STIMPAK) > 0 and not actor.in_combat():
        if not actor.use_on_self(PID_STIMPAK).ok:
            break
    return actor.snap().dude.hp >= target


PID_ANTIDOTE = 49  # f1/idealist.py
MEND_BELOW = 0.75  # of max HP: the spare stimpaks (above STIMPAKS_KEPT) heal up to it
MEND_FLOOR = 0.5  # of max HP: below it after the poison, the kept stimpaks too; with none, a rest until healed
POISON_MATTERS = 2  # a tick takes 1 HP and 2 poison: 1 left costs at most 1 HP (a road fight left it)
POISON_REST = "2 hours"  # a tick every 505 - 5 x poison game seconds: poison 39 runs out in about 2.3 h
MEND_RESTS = 4


def poison(actor: Actor) -> int:
    s = actor.snap()
    return actor.mem.i32(s.dude.address + em.Obj.CRITTER_POISON) if s.dude else 0


def mend_on_the_road(actor: Actor) -> None:
    """After a road fight, before the world map again: an Antidote when poisoned, the spare stimpaks up to MEND_BELOW,
    rests of POISON_REST until the poison is out, then the kept stimpaks below MEND_FLOOR (a rest until healed only
    when none is left). Poison works by game time (critter.cc critter_check_poison: 1 HP and 2 poison a tick, a rest
    stopped at 5 HP or less) and a trip runs days of it: a full run left a fight poisoned at 33 HP and reached 6 on
    the way to Junktown, a 50x run died of it. The HP itself is not rested back: a day of
    travel heals as a day of rest (worldmap.cc partyMemberRestingHeal(24)), and a rest until healed took 70 game hours
    after one fight (live; so only the poison is rested out)."""
    if actor.in_combat() or actor.snap().dude is None:
        return
    p0, hp0 = poison(actor), actor.snap().dude.hp
    if p0 >= POISON_MATTERS and actor._count(PID_ANTIDOTE):
        actor.use_on_self(PID_ANTIDOTE)
    spare_stimpaks(actor, MEND_BELOW * actor.max_hp)
    for _ in range(MEND_RESTS):
        if poison(actor) < POISON_MATTERS:
            break
        if actor.snap().dude.hp <= 5:  # a tick at 5 HP or less ends the rest at once: heal first
            spare_stimpaks(actor, MEND_FLOOR * actor.max_hp, keep=0)
        if not actor.rest(POISON_REST).ok:
            win32.wait_for(lambda: actor.snap().screen == "map", 5, 0.2)
            break
    floor = MEND_FLOOR * actor.max_hp
    spare_stimpaks(actor, floor, keep=0)
    if actor.snap().dude.hp < floor and not actor.in_combat() and not actor.rest("until healed").ok:
        win32.wait_for(lambda: actor.snap().screen == "map", 5, 0.2)
    actor.log.emit("mend", poison_before=p0, poison=poison(actor), hp_before=hp0, hp=actor.snap().dude.hp)


def noticed_by(critter, tile: int) -> bool:
    return perception.notices(critter.tile, critter.rotation, perception.perception_of(critter.pid), tile)


def lure(actor: Actor, target, others: list) -> bool:
    """To a hex the target notices and none of `others` does (combatai.cc is_within_perception), by a way outside
    their notice: from there the fight begins with the target alone. False when there is none, or it was not
    reached (the fight is then reckoned with all who notice)."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    watched = perception.zone(others)
    spots = {
        t
        for r in range(3, 9)
        for t in geometry.ring(target.tile, r)
        if t not in obs.blocked and t not in watched and noticed_by(target, t)
    }
    if not spots or s.dude.tile in spots:
        return False
    reached, _why = nav.go_to(actor, spots, max_steps=40, fight=True, avoid=watched)
    actor.log.emit("tactic", move="lure", spots=len(spots), reached=reached, target=target.tile)
    return reached


def back_off(actor: Actor, target, foes: list) -> bool:
    """Out of every foe's notice to another side of `target` (9 to 12 hexes round it, 8 or more from here), so that
    the next approach comes in at another angle, where a lure may find a hex that only the target notices. Two
    radscorpions side by side refused a fight at 24 % and no hex round them was the target's alone."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    watched = perception.zone(foes)
    spots = {
        t
        for r in range(9, 13)
        for t in geometry.ring(target.tile, r)
        if t not in obs.blocked and t not in watched and geometry.distance(t, s.dude.tile) >= 8
    }
    if not spots:
        return False
    reached, _why = nav.go_to(actor, spots, max_steps=12, fight=True, avoid=watched)
    actor.log.emit("tactic", move="back off", spots=len(spots), reached=reached, target=target.tile)
    return reached


def hunt(actor: Actor, kind: str = "Radscorpion", minutes: float = 40) -> dict:
    """Kill every `kind` on this map, without loading for luck: each fight starts healed (`heal`: a rest, or
    stimpaks) and is fought out. A death is returned for the route runner to load its checkpoint. No saves in here: a
    save after every kill kept a worse state each time (17 HP, no rounds, no stimpaks, a radscorpion 4 hexes off) and
    each load came back to it, 22 minutes of the parity run."""
    log = actor.log
    end = time.monotonic() + minutes * 60
    kills = heals = backs = 0
    tried_heal = False  # since the last fight
    known = len(foes_of(actor, kind))
    log.emit("hunt_start", species=kind, foes=known)
    off_map_since = None

    def done(why: str) -> dict:
        log.emit("hunt_end", why=why, kills=kills, heals=heals)
        return {"why": why, "kills": kills, "heals": heals}

    while time.monotonic() < end:
        s = actor.snap()
        if s.screen == "main_menu" or (s.dude is not None and s.dude.hp <= 0):
            return done("died")
        if s.screen != "map":
            off_map_since = off_map_since or time.monotonic()
            if time.monotonic() - off_map_since > OFF_MAP_S:
                return done(f"stuck on {s.screen}")
            time.sleep(0.5)
            continue
        off_map_since = None
        if actor.in_combat():
            actor.fight(min_hp=0)  # fought out: it began healed
            tried_heal = False
            continue
        left = foes_of(actor, kind)
        if len(left) < known:
            kills += known - len(left)
            known = len(left)
            loot.sweep(actor, max_hexes=15, budget_s=30)  # what the fallen carried that is worth it
        if not left:
            return done("cleared")
        hp = actor.snap().dude.hp
        if hp < HEAL_BELOW * actor.max_hp and not tried_heal:
            heals, tried_heal = heals + 1, True
            log.emit("hunt_heal", hp=hp, stimpaks=actor._count(PID_STIMPAK))
            heal(actor)
            continue
        if hp < FIGHT_ABOVE * actor.max_hp and not actor.in_combat():
            return done("too hurt to fight on")
        target = min(left, key=lambda c: geometry.distance(c.tile, s.dude.tile))
        dist = geometry.distance(target.tile, s.dude.tile)
        if dist <= 9 and geometry.on_view(target.tile, s.camera, 24):
            # a fight takes in every foe that notices the player when it begins (f1.perception); where two
            # would, a hex that only the target notices is sought first, so that it comes alone
            others = [c for c in left if c is not target]
            group = [target] + [c for c in others if noticed_by(c, s.dude.tile)]
            if len(group) > 1 and lure(actor, target, others):
                continue
            chance = odds.against(actor, group)
            log.emit(
                "odds", foes=len(group), win=round(chance.win, 3), hp_left=round(chance.hp_left, 1),
                turns=round(chance.turns, 1), rounds=round(chance.rounds, 1), hit=chance.hit, foe_hit=chance.foe_hit,
            )  # fmt: skip
            if chance.win < ODDS_TO_FIGHT:
                if len(group) > 1 and backs < BACK_OFFS and back_off(actor, target, left):
                    backs += 1  # from another side the target may come alone
                    continue
                return done(f"odds {chance.win:.0%}: not a fight to begin")
            session.press("a")  # start combat; the fight loop takes it from there
            time.sleep(1.0)
            if not actor.in_combat():
                time.sleep(1.0)
            continue
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        goals = {t for r in (4, 5, 6) for t in geometry.ring(target.tile, r) if t not in obs.blocked}
        nav.go_to(actor, goals, max_steps=4, fight=True)
    return done("time up")


@cache
def _exits_of(map_file: str) -> tuple[bool, frozenset[str]]:
    """From the map file: whether any exit grid leads out (to the world or the town map), and the maps the others
    lead to (names without .MAP)."""
    from f1 import mapfile

    try:
        m = mapfile.read(map_file)
    except (KeyError, ValueError):
        return False, frozenset()
    dests = {o.data["map"] for o in m.objects if o.kind == "exit grid"}
    return any(d < 0 for d in dests), frozenset(knowledge.map_name(d).removesuffix(".MAP") for d in dests if d >= 0)


def hops_out(map_file: str, limit: int = 4) -> int:
    """How many map changes from `map_file` to one with an exit out (0: it has one), by its exits; limit + 1 if none."""
    seen, frontier = {map_file}, [map_file]
    for depth in range(limit + 1):
        if any(_exits_of(m)[0] for m in frontier):
            return depth
        frontier = [n for m in frontier for n in _exits_of(m)[1] if n not in seen]
        seen.update(frontier)
    return limit + 1


def map_path(start: str, target: str, limit: int = 6) -> list[str] | None:
    """The maps to cross from `start` to `target` by exit grids (names without .MAP), from the map files; None if no
    way within `limit` changes. The Boneyard: LAADYTUM -> LABLADES -> LAFOLLWR."""
    paths = {start: [start]}
    frontier = [start]
    for _ in range(limit):
        if target in paths:
            break
        nxt = []
        for m in frontier:
            for n in _exits_of(m)[1]:
                if n not in paths:
                    paths[n] = paths[m] + [n]
                    nxt.append(n)
        frontier = nxt
    return paths.get(target)


def goto_map(actor: Actor, target: str) -> bool:
    """Cross a place's maps to `target` (a first visit lands on a town's first map, and sections not yet seen are
    missing from its town map): each exit on the static path in turn."""
    here = actor.snap().map_name.split(".")[0]
    path = map_path(here, target.upper())
    actor.log.emit("action_start", action="goto_map", target=target, path=path)
    for step in (path or [])[1:]:
        if not take_exit(actor, step):
            return actor._end(False, f"no way to {step}", {"at": actor.snap().map_name}, "goto_map").ok
    ok = actor.snap().map_name.startswith(target.upper())
    return actor._end(ok, "there" if ok else "not there", {"path": path}, "goto_map").ok


def leave_map(actor: Actor, max_steps: int = 120, hops: int = 4) -> bool:
    """Walk out to the world map: to the nearest exit grid that leads out (map -1: the world map, -2: the town
    map); when this level has none, by the elevator to the first level, or through an exit to another map of the
    place (Vault 13's levels -> its cave -> out). True once the world map (or the town map on it) shows."""
    for _ in range(hops):
        s = actor.snap()
        if s.screen == "worldmap":
            return True
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        out = {t for t, (to_map, _tile, _elev) in obs.exits.items() if to_map < 0}  # -1 world map, -2 town map
        other = {t for t, (to_map, _tile, _elev) in obs.exits.items() if to_map >= 0}
        actor.log.emit("action_start", action="leave_map", exits=len(out), other=len(other), map=s.map_name)
        if not out and not other and s.elevation > 0 and nearest_thing(actor, ("Vault Elevator", "Elevator")):
            use_elevator(actor, "1")
            continue
        if not out and other:  # toward the map nearest a way out (the Hub's downtown: its entrance, not the heights)
            by_map = {t: hops_out(knowledge.map_name(obs.exits[t][0]).removesuffix(".MAP")) for t in other}
            best = min(by_map.values())
            other = {t for t, n in by_map.items() if n == best}
        goals = out or other
        if not goals:
            return actor._end(False, "no way out of this level", {}, "leave_map").ok
        nav.go_to(actor, goals, max_steps=max_steps, fight=True)
        if win32.wait_for(lambda: actor.snap().screen == "worldmap", 15 if out else 3, 0.25):
            return actor._end(True, "on the world map", {}, "leave_map").ok
        win32.wait_for(lambda: actor.snap().screen == "map", 20, 0.25)  # another map of the place: go on
        time.sleep(1.0)
    return actor._end(False, "not on the world map", {}, "leave_map").ok


def take_exit(actor: Actor, map_file: str, max_steps: int = 150) -> bool:
    """Walk to the exit grids on this level that lead to `map_file` (e.g. "HALLDED"); True once on that map."""
    s = actor.snap()
    obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
    goals = {t for t, (to_map, _t, _e) in obs.exits.items() if map_file.upper() in knowledge.map_name(to_map)}
    actor.log.emit("action_start", action="take_exit", to=map_file, exits=len(goals), map=s.map_name)
    if not goals:
        return actor._end(False, f"no exit to {map_file} on this level", {}, "take_exit").ok
    nav.go_to(actor, goals, max_steps=max_steps, fight=True)
    there = win32.wait_for(lambda: map_file.upper() in actor.snap().map_name, 20, 0.25)
    time.sleep(1.0)
    s = actor.snap()
    return actor._end(there, s.map_name, {"elevation": s.elevation, "tile": s.dude.tile}, "take_exit").ok


def close_dialogue(actor: Actor, nodes: int = 4) -> bool:
    """End a talk someone started: a polite exit option, else the last one (often "[Done]"). True once none is up."""
    for _ in range(nodes):
        d = dialogue.read(actor.mem)
        if not d.active:
            return True
        if d.options:
            i = dialogue.leave(d.options)
            i = len(d.options) - 1 if i is None else i
            actor.log.emit("dialogue", speaker=d.speaker, reply=d.reply, options=d.options, chosen=d.options[i])
            dialogue.choose(actor.mem, i)
        else:
            time.sleep(0.5)
    return not dialogue.read(actor.mem).active


def flee(actor: Actor, turns: int = 15) -> bool:
    """Out of a map by its nearest exit grid in spite of a fight: in combat each turn's AP go into steps toward it,
    and standing on the grid ends the combat and leaves (CE map_leave_map sets game_user_wants_to_quit in combat,
    map_check_state moves once it is over). An encounter need not be fought (a rule)."""
    start = actor.snap().map_name
    actor.log.emit("action_start", action="flee", map=start)
    still = 0  # combat turns in a row that moved nowhere: the way out is shut, fleeing more only loses the turns
    for _ in range(turns * 4):
        s = actor.snap()
        if s.screen != "map" or s.map_name != start:
            return actor._end(True, "fled", {"screen": s.screen, "map": s.map_name}, "flee").ok
        if s.dude is None or s.dude.hp <= 0:
            return actor._end(False, "died", {}, "flee").ok
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        exits = {t for t, (to_map, _t, _e) in obs.exits.items() if to_map < 0} or set(obs.exits)
        if not exits:
            return actor._end(False, "no exit on this map", {}, "flee").ok
        if not actor.in_combat():
            nav.go_to(actor, exits, max_steps=40, fight=False)
            win32.wait_for(lambda: actor.snap().screen != "map", 3, 0.25)
            continue
        if not actor.my_turn():
            win32.wait_for(lambda: actor.my_turn() or not actor.in_combat() or actor.snap().screen != "map", 30, 0.1)
            continue
        walked = s.dude.ap > 0 and actor.combat_walk(exits, s.dude.ap).ok
        still = 0 if walked else still + 1
        if still >= 3:
            return actor._end(False, "no way out", {}, "flee").ok
        if (not walked or actor.snap().dude.ap <= 0) and actor.my_turn():
            actor.end_turn()
    return actor._end(False, "still here", {}, "flee").ok


# Hostiles on an encounter map that make the Agent leave by the nearest exit instead of fighting: two large packs of
# radscorpions killed it at level 4 on the road to Junktown (a full run at Full HD, twice: HP 47 to death
# in about 55 s each, poisoned, half its shots missed); four mantises in the second run took all three stimpaks
PACK = 4
# Road foes fled whatever their number: "A patrol unit of super mutants" hit the level-8 Agent for 70 of its 59 HP
# with one shot (a full run at Full HD)
DEADLY = ("Mutant", "Deathclaw", "Nightkin")


def visit(actor: Actor, town: str, section: int = 0, tries: int = 40, flee_all: bool = False) -> str:
    """From the world map: travel to a town and enter its entrance `section`. Random encounters on the way are
    fought (when hostile) and left by the map's edge, each taking two or three tries (the Glow to the Brotherhood
    met six in a row and ran out of 16, two full runs at Full HD: 40);
    without a gun, with `flee_all`, or against PACK foes or more, they are fled instead. Returns the map entered, or what stopped the trip."""
    tx, ty = worldmap.town_xy(town)
    actor.log.emit("action_start", action="visit", town=town, section=section)
    why = "no try"
    for _ in range(tries):
        if actor.skip_cinema():  # the Cathedral's end plays on the world map as the trip starts
            continue
        s = actor.snap()
        if s.screen == "dialogue":  # someone speaks up on arrival (Adytum's guard about a drawn gun): answer, go on
            close_dialogue(actor)
            continue
        if s.screen == "map" and not s.map_name:  # between a fled map and the world map (a full run)
            win32.wait_for(lambda: actor.snap().screen != "map" or bool(actor.snap().map_name), 5, 0.2)
            continue
        if s.screen == "map" and s.map_name.split(".")[0] in worldmap.SECTIONS[town][section : section + 1]:
            # arrived, though the entry itself looked failed: Junktown's gate guard spoke up at once about the drawn
            # gun, the talk was closed, and this loop walked out of the town again, 13 times
            return actor._end(True, s.map_name, {"town": town, "after_talk": True}, "visit").reason
        if s.screen == "map":  # an encounter (or we are already somewhere): fight or flee, then out by the edge
            encounter = s.map_name.split(".")[0] not in worldmap.SECTIONS[town]
            if encounter:  # not in the town itself: guards stop guns
                actor.holster(False)  # the gun out on the road (it may have been put away for a town or a camp)
            foes = actor.enemies() if encounter else []
            deadly = [c for c in foes if any(k in knowledge.proto_name(c.pid) for k in DEADLY)]
            pack = len(foes)
            if pack >= PACK or deadly:
                actor.log.emit("pack", map=s.map_name, foes=pack, deadly=len(deadly))
            if encounter and (flee_all or not actor.armed() or pack >= PACK or deadly):  # out by the nearest exit
                if not flee(actor):
                    s = actor.snap()
                    alive = s.dude is not None and s.dude.hp > 0 and s.screen == "map"
                    if not (alive and actor.in_combat() and actor.armed() and actor.fight().ok):  # no way out: fight
                        return actor._end(False, "could not flee an encounter", {}, "visit").reason
                continue
            if actor.in_combat() and not actor.fight().ok:
                s = actor.snap()
                if s.dude is None or s.dude.hp <= 0 or s.screen == "main_menu" or not flee(actor):
                    return actor._end(False, "lost a fight on the way", {}, "visit").reason
                continue  # the fight stopped to keep the Agent alive: fled
            if encounter:  # loot: the fallen's ammunition and medicine, before going on
                loot.sweep(actor, max_hexes=25, budget_s=60)
                mend_on_the_road(actor)  # poison and wounds before more days of travel
            if not leave_map(actor):
                return actor._end(False, "could not leave an encounter map", {}, "visit").reason
            continue
        why = worldmap.travel(actor.pid, tx, ty)
        if why == "town map":  # a town's map: ours means pick the entrance, another means out to the world map
            if actor.mem.glob("our_town") != worldmap.town_index(town):
                worldmap.to_world_map(actor.mem)
                continue
            why = "arrived"
        if why == "arrived":
            screen = worldmap.enter_here(actor.pid, section)
            if screen == "map" or win32.wait_for(lambda: actor.snap().screen == "map", 30, 0.25):
                time.sleep(1.5)
                name = actor.snap().map_name
                return actor._end(True, name, {"town": town}, "visit").reason
            why = f"did not enter ({screen})"
        elif why.startswith("interrupted"):
            win32.wait_for(lambda: actor.snap().screen == "map", 30, 0.25)
            time.sleep(1.5)
            name = actor.snap().map_name
            # a first visit enters the town's first map by itself (worldmap.c: no town map yet): arrived, not waylaid
            if name.split(".")[0] == worldmap.SECTIONS[town][section]:
                return actor._end(True, name, {"town": town, "first_visit": True}, "visit").reason
            actor.log.emit("encounter", map=name)
    return actor._end(False, why, {"town": town}, "visit").reason


def go(actor: Actor, town: str, section: int = 0, tries: int = 4, recover: bool = True, flee_all: bool = False) -> str:
    """visit() with recovery: when the Agent dies on the way (a random encounter), load slot 1, walk out again and
    travel again. Returns the map entered, or the last reason. Routes pass recover=False: a reload behind the route
    runner's back brought back an older checkpoint and undid the steps since (the chip's hand-in)."""
    why = "no try"
    for attempt in range(tries if recover else 1):
        s = actor.snap()
        if s.screen == "map" and not leave_map(actor):
            why = "could not leave"
        else:
            why = visit(actor, town, section, flee_all=flee_all)
            if why.endswith((".MAP", ".SAV")):
                return why
        s = actor.snap()
        died = s.screen == "main_menu" or s.dude is None or s.dude.hp <= 0
        beaten = not died and actor.in_combat() and s.dude.hp < 10  # the fight stopped to keep the Agent alive
        if (died or beaten) and recover:
            actor.log.emit("recover", why=f"{'died' if died else 'beaten'} on the way ({why})", attempt=attempt + 1)
            actor.close()
            loot.forget()  # the load puts the finds back
            flows.load_from_menu(actor.pid, 1)
            actor.__init__(actor.pid, actor.log)
    return why


def nearest_thing(actor: Actor, names: tuple[str, ...], kind: str = "scenery"):
    """The nearest object of `kind` on this elevation named exactly one of `names` (earlier names first); a name in
    lower case may also be the object's script (the Cathedral's doors are plain "Door"s run by `cocdoor`)."""
    s = actor.snap()
    here = [t for t in world.things(actor.mem) if t.type == kind and t.elevation == s.elevation]
    for name in names:
        found = [t for t in here if t.name == name]
        if not found and name.islower():
            found = [t for t in here if scripts.script_of(actor.mem, t.address) == name]
        if found:
            return min(found, key=lambda t: geometry.distance(t.tile, s.dude.tile))
    return None


# Where a scenery's usable pixels lie relative to its hex varies (the Necropolis sewer hole answered at (-36, 0) once
# and sat ~19 px higher another time; its cover takes the clicks nearer the hex and is only looked at): a grid of
# aims, the ones seen working first.
SCENERY_AIMS = [(-36, 0), (0, 0), (0, -10), (12, -10), (-12, -10)] + [
    (dx, dy) for dy in (-18, -8, 6, -28) for dx in (-36, -28, -20, -12, -4, 4, 12)
]


def use_scenery(actor: Actor, names: tuple[str, ...], done, what: str, timeout_s: float = 40, aims=None) -> bool:
    """Walk up to the nearest scenery named one of `names` and use it (its default action: a sewer hole, a ladder,
    a door); True once `done()` holds."""
    thing = nearest_thing(actor, names)
    actor.log.emit("action_start", action="use_scenery", what=what, tile=thing.tile if thing else None)
    if thing is None:
        return actor._end(False, f"no {what} here", {}, "use_scenery").ok
    s = actor.snap()
    if geometry.distance(thing.tile, s.dude.tile) > 1:  # right beside it: from 3 hexes the Hotel's hole never took
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        beside = {t for t in geometry.ring(thing.tile, 1) if t not in obs.blocked}
        nav.go_to(
            actor, beside or {t for r in (2, 3) for t in geometry.ring(thing.tile, r) if t not in obs.blocked}, 60
        )
    # clicked where the cursor names it (a bookcase over MSTRLR12's stairs took blind clicks), else blind as before;
    # its namesakes on its hex count too: the rope on the Glow's beam is a second "Beam" its script lays over the
    # first (GENT2LV1.INT create_object_sid), and the aimed pass spent its 40 s there in every run (inferred: the
    # cursor names the one on top)
    twins = {
        t.address
        for t in world.things(actor.mem)
        if (t.tile, t.elevation, t.name) == (thing.tile, thing.elevation, thing.name)
    }
    targets = frozenset(twins | {thing.address})
    ok = actor.click_object(thing.tile, done, timeout_s, aims=aims or SCENERY_AIMS, targets=targets)
    ok = ok or actor.click_object(thing.tile, done, timeout_s, aims=aims or SCENERY_AIMS)
    actor.set_mouse_mode(0)
    return actor._end(ok, "used" if ok else "no effect", {"tile": thing.tile, "name": thing.name}, "use_scenery").ok


def elevator_panel(actor: Actor) -> bool:
    """The elevator panel is up: elev_win names a live, panel-sized window (the id goes stale after use and gets
    reused, e.g. by the indicator bar)."""
    wid = actor.mem.glob("elev_win")
    if wid <= 0 or wid == actor.mem.glob("bar_window"):
        return False
    return any(w[0] == wid and w[4] >= 100 and w[5] >= 100 for w in state.live_windows(actor.mem))


def ride_cab(actor: Actor, door_tile: int, key: str, avoid: frozenset[int] = frozenset()) -> bool:
    """Vault 12's elevators: open the elevator door, step onto the cab's floor behind it (the door tile - 200:
    13504 -> 13304, measured) and the panel opens; then the level key. True once the elevation changed."""
    return ride_from(actor, door_tile - 200, key, avoid)


def ride_from(actor: Actor, tile: int, key: str, avoid: frozenset[int] = frozenset()) -> bool:
    """An elevator whose panel opens when the player steps on `tile` (a cab's floor, or a spatial trigger such as
    the Brotherhood's elev0/elev1); the key picks the level (elevator.c keytable: '1'.., 'G'). True once the map or
    the elevation changed. `avoid`: hexes to keep off while there is another way (a guard's reach)."""
    s0 = actor.snap()
    actor.log.emit("action_start", action="ride", tile=tile, key=key)
    _, why = nav.go_to(actor, {tile}, max_steps=80, fight=True, avoid=avoid)  # go_to opens the doors on the way
    if why == "no path" and avoid:
        _, why = nav.go_to(actor, {tile}, max_steps=80, fight=True)
    if not win32.wait_for(lambda: elevator_panel(actor), 3, 0.1):
        return actor._end(False, f"no panel ({why})", {"at": actor.snap().dude.tile}, "ride").ok

    def moved() -> bool:
        s = actor.snap()
        return s.screen == "map" and (s.elevation != s0.elevation or s.map_name != s0.map_name)

    time.sleep(0.4)
    if key.isalpha():  # 'G' (ground) is a capital in keytable: typed with Shift ('g' did nothing, live)
        session.type_text(key.upper())
    else:
        session.press(key)
    ok = win32.wait_for(moved, 20, 0.2)
    time.sleep(1.0)
    s = actor.snap()
    detail = {"map": s.map_name, "elevation": s.elevation, "tile": s.dude.tile if s.dude else None}
    return actor._end(ok, "rode" if ok else "still here", detail, "ride").ok


def use_elevator(actor: Actor, key: str) -> bool:
    """Ride the elevator on this elevation to the level on `key` (elevator.c keytable: '1', '2', '3'). Measured in
    Vault 13 and Vault 12: the panel opens when the player steps onto the cab's floor, the tile behind the elevator
    door (door tile - 200; the arrival tile in front is door + 200). So: the nearest elevator door, then ride_cab."""
    door = nearest_thing(actor, ("Elevator Door",))
    if door is None:
        actor.log.emit("action_start", action="use_elevator", key=key)
        return actor._end(False, "no elevator door here", {}, "use_elevator").ok
    return ride_cab(actor, door.tile, key)


def loot_the_dead(actor: Actor, names: tuple[str, ...] = (), max_bodies: int = 8) -> int:
    """Walk to each dead critter on this floor that still carries something (named `names`, or any) and take it
    all. Returns how many bodies were emptied."""
    emptied = 0
    for _ in range(max_bodies):
        s = actor.snap()
        bodies = [
            c
            for c in state.critters(actor.mem)
            if c.dead
            and c.elevation == s.elevation
            and actor.mem.i32(c.address + 0x2C) > 0
            and (not names or any(n in knowledge.proto_name(c.pid) for n in names))
        ]
        if not bodies:
            break
        body = min(bodies, key=lambda c: geometry.distance(c.tile, s.dude.tile))
        if (
            geometry.distance(body.tile, s.dude.tile) > 6
        ):  # near, not beside: the Agent's own sprite would take the click
            obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
            goals = {t for r in (3, 4) for t in geometry.ring(body.tile, r) if t not in obs.blocked}
            nav.go_to(actor, goals, max_steps=30, fight=True)
        if actor.loot(body.tile).ok:
            emptied += 1
        elif actor.mem.i32(body.address + 0x2C) > 0:
            break  # the body keeps its things (too heavy, or it could not be reached): stop rather than loop
    return emptied


def find_critter(actor: Actor, name: str, near: int | None = None):
    """The live critter on this floor called `name`, nearest to the Agent (or to the tile `near`: Seth is a plain
    "Peasant" who keeps Shady Sands' gate): an exact name wins over one that only contains it ("Garl" is not
    "Garl's Advisor")."""
    s = actor.snap()
    here = [c for c in state.critters(actor.mem) if not c.dead and c.elevation == s.elevation]
    exact = [c for c in here if knowledge.proto_name(c.pid).lower() == name.lower()]
    found = exact or [c for c in here if name.lower() in knowledge.proto_name(c.pid).lower()]
    if not found:  # a generic proto ("Peasant") with its own script: Seth is SETH.INT, Sinthia's raider JTRAIDER
        found = [c for c in here if scripts.script_of(actor.mem, c.address) == name.lower()]
    origin = s.dude.tile if near is None else near
    return min(found, key=lambda c: geometry.distance(c.tile, origin)) if found else None


def talk(
    actor: Actor, name: str, plan: list[str], max_legs: int = 12, strict: bool = False, near: int | None = None
) -> list | None:
    """Walk up to the critter called `name` on this map and follow a dialogue plan; the steps (reply, options,
    chosen). Empty when the critter is not here or no dialogue started."""
    for _ in range(max_legs):
        if actor.snap().screen == "dialogue":  # they spoke first on seeing the player (Lasher does): answer by plan
            if strict:
                return dialogue.converse_strict(actor.mem, plan, log=actor.log)
            return dialogue.converse(actor.mem, plan, log=actor.log)
        who = find_critter(actor, name, near)
        if who is None:
            actor.log.emit("talk", name=name, found=False)
            return []
        s = actor.snap()
        obs = nav.obstacles(actor.mem, s.elevation, s.dude.address)
        # Beside them by a real path (a wall between: the way round, through the door); across a desk, the
        # nearest ring that has one (the Overseer: 3 hexes).
        goals, path = set(), None
        for r in (1, 2, 3):
            goals = {t for t in geometry.ring(who.tile, r) if t not in obs.blocked}
            path = nav.astar(s.dude.tile, goals, obs.passable_doors) if goals else None
            if path is not None:
                break
        beside = path is not None and len(path) <= 2
        # Within a few hexes a click to talk lets the game walk the rest: Gizmo behind his desk, whose last hex
        # the engine never walked to (24 tries from 3 hexes), talked from there.
        if beside or geometry.distance(who.tile, s.dude.tile) <= 4:
            out = actor.talk_to(who)
            if out.ok:
                if strict:  # only plan lines; an unknown node stops with the talk open (None)
                    return dialogue.converse_strict(actor.mem, plan, log=actor.log)
                return dialogue.converse(actor.mem, plan, log=actor.log)
            if beside or out.reason == "answered by a float":  # they will not talk now: no walk closer for it
                return []
        nav.go_to(actor, goals, max_steps=8, fight=True)
    return []


def main(argv: list[str]) -> int:
    commands = {"hunt", "leave", "visit", "go", "talk", "exit"}
    if (
        argv[:1] == []
        or argv[0] not in commands
        or (argv[0] == "talk" and len(argv) != 3)
        or (argv[0] in ("visit", "go") and len(argv) not in (2, 3))
    ):
        print(__doc__)
        return 2
    cmd = argv[0]
    run = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-{cmd}"
    with session.driver_lock("quests " + " ".join(argv)):
        return _command(cmd, argv, run)


def _command(cmd: str, argv: list[str], run: Path) -> int:
    actor = Actor(session.game_pid(), EventLog(run / "events.jsonl"))
    try:
        if cmd == "hunt":
            result = hunt(actor, argv[1] if len(argv) > 1 else "Radscorpion")
            result["xp"] = actor.snap().experience
            print(result, f"(events: {run / 'events.jsonl'})")
            return 0 if result["why"] == "cleared" else 1
        if cmd == "exit" and len(argv) == 2:
            ok = take_exit(actor, argv[1])
            print(actor.snap().map_name if ok else "not there")
            return 0 if ok else 1
        if cmd == "leave":
            ok = leave_map(actor)
            print("on the world map" if ok else "still on a map")
            return 0 if ok else 1
        if cmd in ("visit", "go"):
            trip = visit if cmd == "visit" else go
            where = trip(actor, argv[1].lower(), int(argv[2]) if len(argv) == 3 else 0)
            print(where)
            return 0 if where.endswith((".MAP", ".SAV")) else 1
        steps = talk(actor, argv[1], [p.strip().lower() for p in argv[2].split("|") if p.strip()])
        for reply, options, chosen in steps:
            print(f"< {reply[:200]}")
            if chosen:
                print(f"> {chosen}")
        return 0 if steps else 1
    finally:
        actor.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
