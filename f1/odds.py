"""A fight's odds by the game's own formulas (fights are entered on computed odds, never reloaded for), run as a Monte
Carlo of whole turns. From fallout1-ce, the documentation of the 1.1 build:

- To-hit (combat.c determine_to_hit_func): the weapon's skill (Unarmed without one); for a gun, -4 per hex beyond
  2 x PE, which is a bonus of up to 8 x PE close in; -20 per point of ST short of the weapon; less the defender's AC;
  +15 against a big (multihex) body. The player's shot loses 40, 25 or 10 in light up to 26214, 39321 or 52428
  (object.c obj_get_visible_light: the larger of the ambient light and the tile's, light.c). A foe's attack loses 20
  on Wimpy (combat difficulty Easy) and gains 20 on Rough. At most 95.
- Damage (combat.c compute_damage, item.c item_w_damage): a roll from the weapon's minimum to its maximum, plus the
  attacker's melee damage for melee and unarmed; 1 to melee damage + 2 without a weapon. A foe's hit is x 75 % on
  Wimpy. Then less DT, then less DR percent. 1.1 does not use the ammunition's modifiers.
- AP: an attack's cost (weapons.ap_cost: Fast Shot -1); a foe's unarmed attack takes 3. Reloading takes 2.

Left out: criticals (LK 1: about 1 %), knockdowns, poison, bursts, the walk to close in; a foe fights unarmed
(animals: right; a raider with a gun: not yet).

    python -m f1.odds          the running game's odds against every foe in sight
"""

import random
import sys
from dataclasses import dataclass, field

from f1 import chargen, geometry, knowledge, paths, weapons
from f1.engine_map import Obj

AMBIENT_LIGHT = 0x5057E0  # light.c ambient_light
TILE_INTENSITY = 0x59CF1C  # light.c tile_intensity[3][40000]
LIGHT_MAX = 65536
DARK = ((26214, -40), (39321, -25), (52428, -10))  # combat.c: the player's shot in poor light
STAT_AP, STAT_AC, STAT_MELEE, STAT_DT, STAT_DR = 8, 9, 11, 17, 24  # stat.h, normal damage's DT and DR
UNARMED_AP, RELOAD_AP = 3, 2
CLOSE_RANGE = 2  # hexes a gun shoots a foe without a gun from, once the fight has closed in (Actor.fight)
DIFFICULTY_HIT = {0: -20, 1: 0, 2: 20}  # combat difficulty: Wimpy, Normal, Rough, for foes' attacks
DIFFICULTY_DAMAGE = {0: 0.75, 1: 1.0, 2: 1.25}


def light_penalty(light: int) -> int:
    for limit, penalty in DARK:
        if light <= limit:
            return penalty
    return 0


def gun_range(dist: int, pe: int) -> int:
    """combat.c: -4 per hex beyond 2 x PE, a bonus close in down to -2 x PE hexes."""
    return -4 * max(dist - 2 * pe, -2 * pe)


def to_hit(skill: int, ac: int, ranged: bool = False, dist: int = 1, pe: int = 5, st_short: int = 0,
           big: bool = False, light: int = LIGHT_MAX, player: bool = True, difficulty: int = 1) -> int:  # fmt: skip
    """determine_to_hit_func's percent for one attack."""
    hit = skill - 20 * max(0, st_short) - ac + (15 if big else 0)
    if ranged:
        hit += gun_range(dist, pe)
    hit += light_penalty(light) if player else DIFFICULTY_HIT.get(difficulty, 0)
    return min(95, hit)


@dataclass(frozen=True)
class Attacker:
    """One side's way of fighting: its to-hit against the other, its damage roll, what it costs, what it has."""

    hp: int
    ap: int
    hit: int  # percent
    dmg: tuple[int, int]  # the roll's range, melee damage included
    cost: int  # AP an attack
    dt: int  # its own DT and DR, against the other's hits
    dr: int
    mult: float = 1.0  # the difficulty's multiplier on its hits
    mag: int | None = None  # rounds in the gun (None: needs none)
    spare: int = 0  # rounds carried that fit
    capacity: int = 0


@dataclass(frozen=True)
class Odds:
    win: float  # the player's
    hp_left: float  # the player's HP at the end, on average over the wins
    turns: float
    rounds: float  # spent, on average
    hit: int  # the player's to-hit against the first foe
    foe_hit: int  # the first foe's against the player
    trials: int = field(default=0, repr=False)


def _hits(rng: random.Random, a: Attacker, target_dt: int, target_dr: int) -> int:
    """One attack's damage (0 on a miss)."""
    if rng.randint(1, 100) > a.hit:
        return 0
    dmg = int(rng.randint(*a.dmg) * a.mult) - target_dt
    return max(0, dmg - dmg * target_dr // 100) if dmg > 0 else 0


def fight(me: Attacker, foes: list[Attacker], trials: int = 2000, max_turns: int = 40, seed: int = 1) -> Odds:
    """The player's turn first (combat begun by the player), the weakest foe shot first, then each foe's attacks."""
    rng = random.Random(seed)
    wins = turns_sum = rounds_sum = 0
    hp_sum = 0.0
    for _ in range(trials):
        hp, foe_hp = me.hp, [f.hp for f in foes]
        mag, spare, spent = me.mag, me.spare, 0
        for turn in range(1, max_turns + 1):
            ap = me.ap
            while ap >= me.cost and any(h > 0 for h in foe_hp):
                if mag is not None and mag == 0:
                    if spare <= 0 or ap < RELOAD_AP:
                        break
                    mag, spare, ap = min(me.capacity, spare), spare - min(me.capacity, spare), ap - RELOAD_AP
                    continue
                k = min((i for i, h in enumerate(foe_hp) if h > 0), key=lambda i: foe_hp[i])
                foe_hp[k] -= _hits(rng, me, foes[k].dt, foes[k].dr)
                ap -= me.cost
                if mag is not None:
                    mag, spent = mag - 1, spent + 1
            if all(h <= 0 for h in foe_hp):
                wins, hp_sum, turns_sum = wins + 1, hp_sum + hp, turns_sum + turn
                break
            for f, h in zip(foes, foe_hp, strict=True):
                for _ in range(f.ap // f.cost if h > 0 else 0):
                    hp -= _hits(rng, f, me.dt, me.dr)
            if hp <= 0:
                turns_sum += turn
                break
        rounds_sum += spent
    return Odds(
        win=wins / trials,
        hp_left=hp_sum / wins if wins else 0.0,
        turns=turns_sum / trials,
        rounds=rounds_sum / trials,
        hit=me.hit,
        foe_hit=foes[0].hit if foes else 0,
        trials=trials,
    )


# --- from the running game ------------------------------------------------------------------------------------------


def visible_light(mem, elevation: int, tile: int) -> int:
    """object.c obj_get_visible_light for a critter (not the player): the tile's light, at least the ambient one."""
    ambient = mem.i32(AMBIENT_LIGHT)
    tile_light = mem.i32(TILE_INTENSITY + 4 * (elevation * 40000 + tile)) if 0 <= tile < 40000 else 0
    return min(LIGHT_MAX, max(ambient, tile_light))


def player_stats(mem) -> list[int]:
    """The player's 35 stats as base + bonus in pc_proto (worn armour adds to the bonus)."""
    base = chargen.ints(mem, "pc_proto", 35, chargen.BASE_STATS)
    bonus = chargen.ints(mem, "pc_proto", 35, chargen.BASE_STATS + 35 * 4)
    return [b + x for b, x in zip(base, bonus, strict=True)]


def foe_skill(proto: dict, index: int) -> int:
    """A critter's skill: skill.c's default and stat part from its SPECIAL, plus its proto's points."""
    default, modifier, stats = chargen.SKILL_FORMULA[index]
    special = proto.get("special", [5] * 7)
    return (
        default + sum(special[k] for k in stats) * modifier // len(stats) + proto.get("skill_points", [0] * 18)[index]
    )


IN_HAND = 0x01000000 | 0x02000000  # OBJECT_IN_LEFT_HAND | OBJECT_IN_RIGHT_HAND


def armed_at_range(mem, critter) -> bool:
    """The critter holds a gun (or anything thrown): closing in on it helps its shots as much as ours."""
    from f1 import loot

    return any(
        mem.u32(item + Obj.FLAGS) & IN_HAND and weapons.attack_type(pid) in ("ranged", "throw")
        for item, pid, _ in loot.inventory(mem, critter.address)
    )


def shot_chance(actor, target, pid: int, dist: int) -> int:
    """The player's to-hit with gun `pid` at `target` from `dist` hexes, as determine_to_hit_func has it (the target's
    AC from its prototype: a critter's worn armour is left out)."""
    mem = actor.mem
    stats = player_stats(mem)
    w = (weapons.weapon(pid) or {}).get("weapon", {})
    return to_hit(
        actor.skills().get(weapons.skill(pid), 0),
        (knowledge.proto(target.pid) or {}).get("stats", [0] * 35)[STAT_AC],
        ranged=True,
        dist=dist,
        pe=stats[1],
        st_short=max(0, w.get("min_st", 0) - stats[0]),
        big=target.multihex,
        light=visible_light(mem, target.elevation, target.tile),
    )


def against(actor, foes: list, option: weapons.Option | None = None) -> Odds:
    """The odds of the player's best way to fight (weapons.choose) against these live critters, from where they
    stand now, in the light on their tiles, at the game's combat difficulty."""
    mem, s = actor.mem, actor.snap()
    me_stats = player_stats(mem)
    options = actor.weapon_options()
    o = option or (options[0] if options else None)
    pid = o.pid if o else None
    pe, st = me_stats[1], me_stats[0]
    difficulty = mem.glob("combat_difficulty")
    carried = next((c for c in actor.carried_weapons() if c.pid == pid), None)
    w = (weapons.weapon(pid) or {}).get("weapon") if pid is not None else None
    ranged = o is not None and weapons.attack_type(pid) == "ranged"
    first = foes[0]
    dist = geometry.distance(first.tile, s.dude.tile)
    if ranged and not armed_at_range(mem, first):
        dist = min(dist, CLOSE_RANGE)  # the fight closes in on a foe without a gun (Actor.fight's tactics)
    hit = to_hit(
        o.skill_value if o else actor.skills().get("unarmed", 0),
        (knowledge.proto(first.pid) or {}).get("stats", [0] * 35)[STAT_AC],
        ranged=ranged,
        dist=dist,
        pe=pe,
        st_short=max(0, (w or {}).get("min_st", 0) - st),
        big=first.multihex,
        light=visible_light(mem, first.elevation, first.tile),
    )
    melee = me_stats[STAT_MELEE] if not ranged else 0
    dmg = (w["min_damage"], w["max_damage"] + melee) if w else (1, melee + 2)
    me = Attacker(
        hp=s.dude.hp, ap=actor.max_ap, hit=hit, dmg=dmg, cost=o.ap if o else UNARMED_AP,
        dt=me_stats[STAT_DT], dr=me_stats[STAT_DR],
        mag=carried.loaded if carried and weapons.uses_ammo(pid) else None,
        spare=carried.spare if carried else 0, capacity=(w or {}).get("capacity", 0),
    )  # fmt: skip
    them = []
    for c in foes:
        p = knowledge.proto(c.pid) or {}
        stats = p.get("stats", [0] * 35)
        foe_hit = to_hit(foe_skill(p, 3), me_stats[STAT_AC], player=False, difficulty=difficulty)
        them.append(
            Attacker(
                hp=c.hp,
                ap=stats[STAT_AP] or 5,
                hit=foe_hit,
                dmg=(1, stats[STAT_MELEE] + 2),
                cost=UNARMED_AP,
                dt=stats[STAT_DT],
                dr=stats[STAT_DR],
                mult=DIFFICULTY_DAMAGE.get(difficulty, 1.0),
            )
        )
    return fight(me, them)


def main(argv: list[str]) -> int:
    from f1 import session
    from f1.actions import Actor
    from f1.telemetry import EventLog

    actor = Actor(session.game_pid(), EventLog(paths.RUNS / "odds" / "events.jsonl"))
    try:
        foes = actor.enemies()
        s = actor.snap()
        for c in sorted(foes, key=lambda c: geometry.distance(c.tile, s.dude.tile)):
            odds = against(actor, [c])
            light = visible_light(actor.mem, c.elevation, c.tile)
            print(
                f"{knowledge.proto_name(c.pid)} {geometry.distance(c.tile, s.dude.tile)} hexes, HP {c.hp}, light "
                f"{light} ({light_penalty(light)}): win {odds.win:.0%}, HP left {odds.hp_left:.0f}, turns "
                f"{odds.turns:.1f}, rounds {odds.rounds:.1f}, to-hit {odds.hit} / theirs {odds.foe_hit}"
            )
        if not foes:
            print("no foes in sight")
    finally:
        actor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
