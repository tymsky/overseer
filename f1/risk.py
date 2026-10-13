"""The death risk of a fight: the modelled chance that the player dies in the whole of it. The gate enters a fight
while the upper 95 % bound of that chance is under RISK_MAX and few trials run out of rounds (`acceptable`).

A seeded Monte Carlo (the same situation gives the same number) over exact per-attack outcome tables, the player
playing as the bot's fight loop plays (f1.actions Actor.fight) and the foes as Fallout 1's combat AI does (f1.aipacket,
fallout1-ce combatai.c). Trials run in batches until the Wilson interval of the deaths lies wholly on one side of
RISK_MAX, or MAX_TRIALS.

One attack (fallout1-ce combat.c, static analysis; `to_hit`, `_table`):
- to-hit (determine_to_hit_func): the skill; a gun or a throw -4 per hex beyond 2 x PE (4 x PE with the long range
  perk), at most a bonus of 8 x PE; -20 a point of ST short of the weapon; +20 for the accurate perk; less the
  defender's AC and the AP it kept from its last turn (stat.c: AP left count as AC on others' turns); the location's
  penalty (half of it for melee and unarmed); +15 against a big body; the player's shot loses 40/25/10 in poor light;
  a foe's attack -20 on Wimpy, +20 on Rough; at most 95;
- the roll (roll.c roll_check): a hit on d100 <= to-hit; a hit turns critical on a second d100 <= (to-hit - roll)/10
  plus the critical chance less the location's penalty (the torso's 0 for an uncalled shot), only from the second
  game day on (game time / 864000 >= 1); a critical hit takes its row from f1.crit_tables, a miss's critical failure
  plays as a plain miss;
- damage (compute_damage): per round a roll from the weapon's minimum to its maximum (plus melee damage for melee and
  unarmed; fists 1 to melee damage + 2), times the multiplier in halves (2 plain, the critical row's), times 75 % on
  Wimpy and 125 % on Rough for a foe's hit, less DT, then less DR percent, of the weapon's damage type. A critical row
  that bypasses armour sets DT and DR to 0 (Fallout 2 keeps a fifth of them); the penetrate perk sets DT to 0; no
  ammunition modifiers at all (Fallout 2 has its DR adjust and damage multiplier and divisor);
- a burst (compute_spray): one roll at the to-hit (a critical failure fires nothing, a critical success adds 20 to
  each round's roll); of min(burst, loaded) rounds a third goes down the centre, the main target's share half of it
  (at least one); each of those rounds hits on d100 <= the accuracy, then the centre's rest roll again along the line;
  every round its own damage roll with the one multiplier.
Death: the hit points at 0 or below, or a critical row's DAM_DEAD (with its massive part when a d10 beats the stat).

The player (Actor.fight's rules): at his turn's start a stimpak (the inventory's 4 AP each, 10 to 20 hit points: the
proto's drug data) while his hit points are under `heal_below`: half of them, or what the foes near him can take
in one turn of plain hits when that is more; then the weakest foe in reach first (fewest hit points, then
nearest), the nearest when none is; a melee weapon or fists walk to it (1 AP a hex); a gun out of reach walks into
reach, and closes in on a foe with no gun while its to-hit is under GOOD_SHOT; a gun reloads when empty (2 AP) and
fights with fists when the pack is dry; the AP left count as his AC until his next turn.
The foes (combatai.c combat_ai, ai_try_attack): a foe under its packet's min_hp at its turn's start runs (out of the
fight for good); one whose to-hit even without the range modifier is under min_to_hit runs; out of reach or under
min_to_hit it walks closer (1 AP a hex, at most to the next hex); in reach it attacks while its AP pay, the hit mode
picked once a turn (the secondary 1 in secondary_freq), each single attack a called shot by its packet
(aipacket.called_locations; 1 AP more off the torso); a gun reloads from its pack (2 AP) and fights with fists after.
The rounds: the first is the player's turn, then the foes in the fight; each later one in Sequence order, then Luck,
highest first (combat.c compare_faster; a tie counted for the foes). A joiner (`Fighter.joins`: the round at whose end
it joins) acts at once (combat_add_noncoms).

Simplifications, and which way each errs:
- one dimension: a distance a foe; no walls, lines of fire, blocked shots or doorways (more death for a player who
  holds a doorway, less where walls block his shots);
- the player's walks move him toward his target only; the other foes keep their distances;
- no knockdown, knockout, cripple, blindness or lost turn from criticals either way (less death where the player is
  knocked out: Fallout 1's player table holds knockouts in its upper rows);
- a fled foe never comes back (less death); no foe takes a drug (less death: biped foes with stimpaks use them
  under half their hit points when they have no target);
- a burst's side lines and the rounds that hit others are left out (less death);
- the player's traits and perks beyond what his stats hold (Finesse, Bonus Ranged Damage, Slayer, Sniper) are left
  out.
"""

import math
import random
import sys
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache

from f1 import aipacket, crit_tables, odds

SEED = 0x5EED
BATCH = 200  # trials between two looks at the bound
MAX_TRIALS = 3200
MAX_ROUNDS = 60
Z = 1.96  # the Wilson interval's 95 %
# The gate (Fallout 2's, ported): a fight is entered while the upper 95 % bound of its modelled chance
# of the player's death is under this, and at most UNFINISHED_MAX of its trials run out of rounds
RISK_MAX = 0.02
IRONMAN_RISK_MAX = 0.20  # an ironman run (the owner): fights are prepared for, not avoided; a bound high
# enough that Shady Sands' radscorpions (win 0.885, bound 0.167 on Hard/Rough) are fought, not a stuck run


def set_bound(bound: float | None) -> None:
    """The gate's bound on the death chance's upper 95 % bound; None: RISK_MAX's own 0.02."""
    global RISK_MAX
    RISK_MAX = 0.02 if bound is None else bound


UNFINISHED_MAX = 0.05
GAME_DAY = 864000  # ticks: criticals only from the second day (roll.c roll_check_critical)
DIFFICULTY_HIT = {0: -20, 1: 0, 2: 20}
DIFFICULTY_DAMAGE = {0: 75, 1: 100, 2: 125}
PERK_LONG_RANGE, PERK_ACCURATE, PERK_PENETRATE = 58, 59, 60  # perk_defs.h, a weapon proto's perk
FIRE, PLASMA, EMP, EXPLOSION = 2, 3, 5, 6  # damage types
NO_CRITICAL_PID = 0x01000008  # attack_crit_success: a critical on this critter is a plain hit
STIMPAK_HEAL = (10, 20)  # proto 40's drug data: stat -2, i.e. a roll from 10 to 20 of stat 35 (current HP)
INVENTORY_AP = 4  # inventry.c handle_inventory: opening it in combat (less Quick Pockets)
RELOAD_AP = 2
GOOD_SHOT = 50  # f1.actions: a gun closes in on a foe without a gun below this to-hit
LONG_SHOT = 5  # f1.actions: a shot poorer than this is not taken (the turn ends)
ATTEMPTS = 10  # ai_try_attack's loop


@dataclass(frozen=True)
class Weapon:
    """One attack mode as the model needs it (fists by default)."""

    name: str = "fists"
    min_damage: int = 1
    max_damage: int = 2  # the melee damage is added for melee and unarmed kinds
    damage_type: int = 0
    ap: int = 3
    reach: int = 1
    kind: str = "unarmed"  # unarmed, melee, throw, ranged
    min_st: int = 0
    burst: int = 0  # rounds a burst fires (0: a single attack)
    capacity: int = 0  # rounds a magazine holds (0: needs no ammunition)
    perk: int = -1

    @property
    def ranged(self) -> bool:
        return self.kind in ("ranged", "throw")

    @property
    def aimable(self) -> bool:
        """item_w_called_shot: no burst, no explosive, fire or EMP damage, no thrown plasma."""
        return not (
            self.burst
            or self.damage_type in (EXPLOSION, FIRE, EMP)
            or (self.kind == "throw" and self.damage_type == PLASMA)
        )


FISTS = Weapon()


@dataclass(frozen=True)
class Fighter:
    """The player or a foe, as the model needs it."""

    name: str
    hp: int
    max_hp: int
    ap: int  # max AP
    ac: int
    special: tuple[int, ...]  # ST PE EN CH IN AG LK
    dt: tuple[int, ...]  # by damage type (normal first)
    dr: tuple[int, ...]
    skill: int  # the to-hit skill with its weapon
    weapon: Weapon = FISTS
    melee: int = 0  # melee damage
    sequence: int = 0
    crit_chance: int = 0
    better_crits: int = 0
    kill_type: int | None = None  # None: the player (his own critical table)
    pid: int = 0
    big: bool = False
    dist: int = 1  # hexes from the player (foes)
    loaded: int | None = None  # rounds in the gun; None: needs none
    spare: int = 0  # rounds in the pack that fit
    packet: aipacket.Packet | None = None
    secondary: Weapon | None = None
    secondary_skill: int = 0
    unarmed_skill: int = 0  # the fists' skill, when a gun runs dry
    joins: int | None = 0  # 0: in the fight from its start; N: joins at round N's end; None: never
    light: int = odds.LIGHT_MAX  # the light at its tile (the player's shots at it)
    ap_left: int = 0  # AP kept from its last turn (its AC bonus on the player's first turn)
    stimpaks: int = 0  # the player's


@dataclass(frozen=True)
class Rules:
    difficulty: int = 1  # combat difficulty: 0 Wimpy, 1 Normal, 2 Rough
    game_time: int = GAME_DAY  # ticks; criticals only from GAME_DAY on

    @property
    def criticals(self) -> bool:
        return self.game_time // GAME_DAY >= 1


@dataclass(frozen=True)
class Risk:
    deaths: int
    trials: int
    low: float  # the Wilson interval of the death chance
    high: float
    capped: int  # trials still going at MAX_ROUNDS
    wins: int  # every foe in the fight dead or fled, the player alive
    hp_left: float  # the player's, on average over the wins
    turns: float  # rounds, on average
    rounds: float  # spent by the player's gun, on average
    hit: int  # the player's to-hit at the first foe now
    foe_hit: int  # the first foe's at the player

    @property
    def death(self) -> float:
        return self.deaths / self.trials if self.trials else 0.0


def acceptable(r: Risk) -> bool:
    """The gate's verdict: the upper 95 % bound under RISK_MAX and the unfinished trials at most UNFINISHED_MAX."""
    return r.high < RISK_MAX and r.capped <= UNFINISHED_MAX * max(r.trials, 1)


def wilson(deaths: int, trials: int, z: float = Z) -> tuple[float, float]:
    if trials == 0:
        return 0.0, 1.0
    p = deaths / trials
    d = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / d
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


# --- one attack ------------------------------------------------------------------------------------------------------


def to_hit(att: Fighter, w: Weapon, skill: int, dfd: Fighter, dist: int, location: int, player_attacks: bool,
           rules: Rules, dfd_ap_left: int = 0, check_range: bool = True) -> int:  # fmt: skip
    """determine_to_hit_func."""
    acc = skill
    if w.ranged and check_range:
        pe = att.special[1]
        mult = 4 if w.perk == PERK_LONG_RANGE else 2
        acc -= 4 * max(dist - mult * pe, -2 * pe)
    if w is not FISTS:
        acc -= 20 * max(0, w.min_st - att.special[0])
        acc += 20 if w.perk == PERK_ACCURATE else 0
    acc -= dfd.ac + dfd_ap_left
    penalty = crit_tables.LOCATION_PENALTY[location]
    acc += penalty if w.ranged else int(penalty / 2)
    acc += 15 if dfd.big else 0
    acc += odds.light_penalty(dfd.light) if player_attacks else DIFFICULTY_HIT.get(rules.difficulty, 0)
    return min(95, acc)


@dataclass(frozen=True)
class _Spec:
    to_hit: int
    crit_chance: int  # the critical chance with the location's bonus
    criticals: bool
    better_crits: int
    rows: tuple | None  # the six critical rows (None: a critical is a plain hit)
    special: tuple[int, ...]  # the defender's, for the massive rolls
    dmin: int
    dmax: int
    difficulty_pct: int
    dt: int
    dr: int
    penetrate: bool
    emp: bool
    burst: int  # rounds fired (0: single)


@dataclass(frozen=True)
class _Table:
    cum: tuple[float, ...]
    damage: tuple[int, ...]
    dead: tuple[bool, ...]

    def draw(self, rng: random.Random) -> tuple[int, bool]:
        i = min(bisect_right(self.cum, rng.random()), len(self.cum) - 1)
        return self.damage[i], self.dead[i]

    @property
    def mean(self) -> float:
        prev, total = 0.0, 0.0
        for c, d in zip(self.cum, self.damage, strict=True):
            total, prev = total + (c - prev) * d, c
        return total


@cache
def _round_damage(dmin: int, dmax: int, mult: int, pct: int, dt: int, dr: int) -> dict[int, float]:
    out: dict[int, float] = {}
    n = dmax - dmin + 1
    for v in range(dmin, dmax + 1):
        d = v * mult // 2 * pct // 100 - dt
        d = d - d * dr // 100 if d > 0 else 0
        d = max(0, d)
        out[d] = out.get(d, 0.0) + 1 / n
    return out


def _convolve(x: dict[int, float], y: dict[int, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for a, p in x.items():
        for b, q in y.items():
            out[a + b] = out.get(a + b, 0.0) + p * q
    return out


@cache
def _rounds_damage(dmin: int, dmax: int, mult: int, pct: int, dt: int, dr: int, k: int) -> dict[int, float]:
    if k <= 0:
        return {0: 1.0}
    one = _round_damage(dmin, dmax, mult, pct, dt, dr)
    return one if k == 1 else _convolve(_rounds_damage(dmin, dmax, mult, pct, dt, dr, k - 1), one)


def _binomial(n: int, p: float) -> list[float]:
    return [math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]


def _crit_branches(s: _Spec) -> list[tuple[float, int, bool, bool]]:
    """(chance, multiplier, bypass, instant death) of a critical hit's row and massive part."""
    if s.rows is None:
        return [(1.0, 2, False, False)]
    out = []
    for p_row, row in zip(crit_tables.row_odds(s.better_crits), s.rows, strict=True):
        stat = s.special[row.massive_stat] if 0 <= row.massive_stat < len(s.special) else None
        pm = float(crit_tables.massive_odds(row, stat))
        for p, flags in ((1 - pm, row.flags), (pm, row.flags | row.massive_flags)):
            if p > 0 and p_row > 0:
                bypass = bool(flags & crit_tables.DAM_BYPASS) and not s.emp
                out.append((float(p_row) * p, row.multiplier, bypass, bool(flags & crit_tables.DAM_DEAD)))
    return out


def _armour(s: _Spec, bypass: bool) -> tuple[int, int]:
    if bypass:
        return 0, 0
    return (0 if s.penetrate else s.dt), s.dr


@cache
def _table(s: _Spec) -> _Table:
    outcomes: dict[tuple[int, bool], float] = {}

    def add(p: float, dist: dict[int, float], dead: bool) -> None:
        for d, q in dist.items():
            key = (d, dead)
            outcomes[key] = outcomes.get(key, 0.0) + p * q

    plain = critical = critical_failure = 0.0
    for roll in range(1, 101):
        delta = s.to_hit - roll
        if delta >= 0:
            pc = min(1.0, max(0.0, (delta // 10 + s.crit_chance) / 100)) if s.criticals else 0.0
            plain, critical = plain + (1 - pc) / 100, critical + pc / 100
        elif s.criticals:
            critical_failure += min(1.0, max(0.0, (-delta // 10) / 100)) / 100
    miss = 1.0 - plain - critical
    if not s.burst:
        dt, dr = _armour(s, False)
        add(plain, _round_damage(s.dmin, s.dmax, 2, s.difficulty_pct, dt, dr), False)
        for p, mult, bypass, dead in _crit_branches(s):
            dt, dr = _armour(s, bypass)
            add(critical * p, _round_damage(s.dmin, s.dmax, mult, s.difficulty_pct, dt, dr), dead)
        add(miss, {0: 1.0}, False)
    else:
        centre = max(1, s.burst // 3)
        main = centre // 2
        if main == 0:
            main, centre = 1, centre - 1

        def hits(accuracy: int) -> list[float]:
            p = min(1.0, max(0.0, accuracy / 100))
            out = [0.0] * (main + max(centre, 0) + 1)
            for k1, q1 in enumerate(_binomial(main, p)):
                for k2, q2 in enumerate(_binomial(max(0, centre - k1), p)):
                    out[k1 + k2] += q1 * q2
            return out

        # a plain success or a failure: the rounds at the to-hit, the plain multiplier; a critical failure fires none
        fired = plain + max(0.0, miss - critical_failure)
        dt, dr = _armour(s, False)
        for k, q in enumerate(hits(s.to_hit)):
            add(fired * q, _rounds_damage(s.dmin, s.dmax, 2, s.difficulty_pct, dt, dr, k), False)
        crit_hits = hits(s.to_hit + 20)
        for p, mult, bypass, dead in _crit_branches(s):
            dt, dr = _armour(s, bypass)
            for k, q in enumerate(crit_hits):
                # no round on the target: the roll turns into a miss (no critical effect)
                add(critical * p * q, _rounds_damage(s.dmin, s.dmax, mult, s.difficulty_pct, dt, dr, k), dead and k > 0)
        add(min(critical_failure, miss), {0: 1.0}, False)
    keys = sorted(outcomes)
    total = sum(outcomes.values()) or 1.0
    cum, acc = [], 0.0
    for k in keys:
        acc += outcomes[k] / total
        cum.append(acc)
    return _Table(tuple(cum), tuple(k[0] for k in keys), tuple(k[1] for k in keys))


def attack_table(att: Fighter, w: Weapon, skill: int, dfd: Fighter, dist: int, location: int, player_attacks: bool,
                 rules: Rules, dfd_ap_left: int = 0, rounds: int = 0) -> tuple[int, _Table]:  # fmt: skip
    """The to-hit and the outcome table of one attack (a burst of ROUNDS when the weapon bursts)."""
    hit = to_hit(att, w, skill, dfd, dist, location, player_attacks, rules, dfd_ap_left)
    loc = crit_tables.TORSO if location == crit_tables.UNCALLED else location
    melee = att.melee if w.kind in ("melee", "unarmed") else 0
    dtype = w.damage_type if 0 <= w.damage_type < len(dfd.dt) else 0
    rows = None
    if rules.criticals and dfd.pid != NO_CRITICAL_PID:
        rows = crit_tables.tables().rows(dfd.kill_type, loc)
    spec = _Spec(
        to_hit=hit,
        crit_chance=att.crit_chance - crit_tables.LOCATION_PENALTY[loc],
        criticals=rules.criticals,
        better_crits=att.better_crits,
        rows=rows,
        special=dfd.special,
        dmin=w.min_damage,
        dmax=w.max_damage + melee,
        difficulty_pct=100 if player_attacks else DIFFICULTY_DAMAGE.get(rules.difficulty, 100),
        dt=dfd.dt[dtype],
        dr=dfd.dr[dtype],
        penetrate=w.perk == PERK_PENETRATE,
        emp=w.damage_type == EMP,
        burst=rounds if w.burst else 0,
    )
    return hit, _table(spec)


# --- the fight -------------------------------------------------------------------------------------------------------


@dataclass
class _Foe:
    f: Fighter
    hp: int
    dist: int
    ap_left: int
    loaded: int | None
    spare: int
    weapon: Weapon
    skill: int
    active: bool
    fled: bool = False

    @property
    def out(self) -> bool:
        return self.hp <= 0 or self.fled

    @property
    def gun(self) -> bool:
        return self.weapon.ranged


@dataclass
class _Me:
    hp: int
    ap_left: int
    loaded: int | None
    spare: int
    weapon: Weapon
    skill: int
    stimpaks: int
    spent: int = 0
    dead: bool = False


@dataclass
class _Model:
    me: Fighter
    foes: Sequence[Fighter]
    rules: Rules
    tables: dict = field(default_factory=dict)

    def table(self, att: Fighter, w: Weapon, skill: int, dfd: Fighter, dist: int, loc: int, player: bool,
              ap_left: int, rounds: int) -> tuple[int, _Table]:  # fmt: skip
        key = (id(att), w, skill, id(dfd), dist, loc, player, ap_left, rounds)
        got = self.tables.get(key)
        if got is None:
            got = self.tables[key] = attack_table(att, w, skill, dfd, dist, loc, player, self.rules, ap_left, rounds)
        return got


NEAR = 2  # hexes: a foe this close reaches the player in its turn (hand to hand: a step and its attacks)


def turn_damage(f: Fighter, rules: Rules) -> int:
    """The most a foe's plain hits take in one turn (no criticals, no armour): the attacks its AP pay, each its
    weapon's top roll (plus its melee damage hand to hand) at the difficulty's percent. A radscorpion on Rough:
    2 x (2 + 6) x 125 % = 20, more than half the player's 30 at level 2 (an ironman run: two stings took 22)."""
    w = f.weapon
    top = w.max_damage + (f.melee if w.kind in ("unarmed", "melee") else 0)
    return f.ap // max(1, w.ap) * top * DIFFICULTY_DAMAGE.get(rules.difficulty, 100) // 100


def heal_below(max_hp: int, danger: int) -> float:
    """The hit points under which a turn starts with a stimpak: half of them, or the near foes' turn of hits when
    that is more, at most a stimpak's least heal under the top (so that a heal is never wasted on a full bar)."""
    return max(max_hp / 2, min(danger, max_hp - STIMPAK_HEAL[0]))


def danger(actor, critters, tile: int) -> int:
    """The live foes' `turn_damage` for those within NEAR of TILE (fists or their proto's weapon: a gun's holder
    further off is left out)."""
    from f1 import geometry

    rules = Rules(actor.mem.glob("combat_difficulty"))
    near = [c for c in critters if geometry.distance(c.tile, tile) - (1 if c.multihex else 0) <= NEAR]
    return sum(turn_damage(foe(c.pid, 1, hp=c.hp), rules) for c in near)


def _player_turn(m: _Model, me: _Me, foes: list[_Foe], rng: random.Random) -> None:
    f = m.me
    ap = f.ap
    near = sum(turn_damage(x.f, m.rules) for x in foes if x.active and not x.out and x.dist <= NEAR)
    while me.hp < heal_below(f.max_hp, near) and ap >= INVENTORY_AP and me.stimpaks > 0:
        ap -= INVENTORY_AP
        me.stimpaks -= 1
        me.hp = min(f.max_hp, me.hp + rng.randint(*STIMPAK_HEAL))
    while True:
        live = [x for x in foes if x.active and not x.out]
        if not live:
            break
        w = me.weapon
        if me.loaded is not None and me.loaded == 0:
            if me.spare > 0 and ap >= RELOAD_AP:
                take = min(w.capacity, me.spare)
                me.loaded, me.spare, ap = take, me.spare - take, ap - RELOAD_AP
                continue
            if me.spare > 0:
                break  # no AP to reload: the turn ends
            me.weapon, me.loaded, me.skill = FISTS, None, f.unarmed_skill  # the gun is dry: fists
            continue
        reach = w.reach
        in_reach = [x for x in live if x.dist <= reach]
        target = min(in_reach, key=lambda x: (x.hp, x.dist)) if in_reach else min(live, key=lambda x: x.dist)
        if target.dist > reach:
            steps = min(target.dist - reach, ap)
            target.dist, ap = target.dist - steps, ap - steps
            if target.dist > reach:
                break
        hit = to_hit(f, w, me.skill, target.f, target.dist, crit_tables.UNCALLED, True, m.rules, target.ap_left)
        if w.ranged and hit < GOOD_SHOT and target.dist > 1 and not target.gun:
            closer = min(target.dist - 1, -(-(GOOD_SHOT - hit) // 4), ap - w.ap)
            if closer > 0:
                target.dist, ap = target.dist - closer, ap - closer
        hit = to_hit(f, w, me.skill, target.f, target.dist, crit_tables.UNCALLED, True, m.rules, target.ap_left)
        if ap < w.ap or hit < LONG_SHOT:
            break
        rounds = min(w.burst, me.loaded or 0) if w.burst else 0
        _, table = m.table(f, w, me.skill, target.f, target.dist, crit_tables.UNCALLED, True, target.ap_left, rounds)
        ap -= w.ap
        if me.loaded is not None:
            used = max(1, rounds)
            me.loaded, me.spent = me.loaded - used, me.spent + used
        damage, dead = table.draw(rng)
        target.hp = 0 if dead else target.hp - damage
    me.ap_left = ap


def _foe_turn(m: _Model, x: _Foe, me: _Me, rng: random.Random) -> None:
    f, p = x.f, x.f.packet
    ap = f.ap
    if p is not None and x.hp < p.min_hp:
        x.fled = True
        return
    w, skill = x.weapon, x.skill
    sec = f.secondary
    usable = sec is not None and sec.kind != "throw" and (sec.capacity == 0 or (x.loaded or 0) > 0)
    if usable and p is not None and rng.randint(1, p.secondary_freq) == 1:
        w, skill = sec, f.secondary_skill
    min_to_hit = p.min_to_hit if p is not None else -1000
    for _ in range(ATTEMPTS):
        if me.dead or ap <= 0:
            break
        if x.loaded is not None and x.loaded == 0 and w.capacity:
            if x.spare > 0:
                take = min(w.capacity, x.spare)
                x.loaded, x.spare, ap = take, x.spare - take, max(0, ap - RELOAD_AP)
                continue
            x.weapon = w = FISTS
            x.skill = skill = f.unarmed_skill
            x.loaded = None
            continue
        far = to_hit(f, w, skill, m.me, x.dist, crit_tables.UNCALLED, False, m.rules, me.ap_left)
        if x.dist > w.reach or far < min_to_hit:
            near = to_hit(f, w, skill, m.me, x.dist, crit_tables.UNCALLED, False, m.rules, me.ap_left, False)
            if near < min_to_hit or x.dist <= 1:
                x.fled = True  # ai_run_away
                return
            steps = min(ap, x.dist - 1)
            x.dist, ap = x.dist - steps, ap - steps
            continue
        if ap < w.ap:
            break
        loc = crit_tables.TORSO
        if p is not None and w.aimable and ap >= w.ap + 1:
            locs = aipacket.called_locations(
                p, f.special[4], m.rules.difficulty, True,
                lambda loc, w=w, skill=skill: to_hit(f, w, skill, m.me, x.dist, loc, False, m.rules, me.ap_left),
            )  # fmt: skip
            r, acc = rng.random(), 0.0
            for where, q in locs.items():
                acc += q
                if r < acc:
                    loc = where
                    break
        cost = w.ap + (0 if loc == crit_tables.TORSO else 1)
        rounds = min(w.burst, x.loaded or 0) if w.burst else 0
        _, table = m.table(f, w, skill, m.me, x.dist, loc, False, me.ap_left, rounds)
        ap -= cost
        if x.loaded is not None and w.capacity:
            x.loaded -= max(1, rounds)
        damage, dead = table.draw(rng)
        me.hp -= damage
        if dead or me.hp <= 0:
            me.dead = True
        if w.ap > ap:
            break
    x.ap_left = ap


def _order(m: _Model, foes: list[_Foe]) -> list[_Foe | None]:
    """A round's order after the first: Sequence, then Luck, highest first; None is the player; ties to the foes."""
    me = (m.me.sequence, m.me.special[6], 0)
    keyed = [((x.f.sequence, x.f.special[6], 1), x) for x in foes] + [(me, None)]
    keyed.sort(key=lambda kx: kx[0], reverse=True)
    return [x for _, x in keyed]


def _trial(m: _Model, rng: random.Random) -> tuple[str, int, int, int]:
    """'death', 'win' or 'capped', the player's HP, the rounds, his gun's rounds spent."""
    me = _Me(
        m.me.hp, 0, m.me.loaded, m.me.spare, m.me.weapon, m.me.skill, m.me.stimpaks,
    )  # fmt: skip
    foes = [
        _Foe(f, f.hp, f.dist, f.ap_left, f.loaded, f.spare, f.weapon, f.skill, f.joins == 0)
        for f in m.foes
        if f.joins is not None
    ]
    for rnd in range(1, MAX_ROUNDS + 1):
        order: list[_Foe | None] = [None, *foes] if rnd == 1 else _order(m, foes)
        for who in order:
            if who is None:
                _player_turn(m, me, foes, rng)
            elif who.active and not who.out:
                _foe_turn(m, who, me, rng)
            if me.dead:
                return "death", me.hp, rnd, me.spent
        for x in foes:  # joiners at the round's end act at once
            if not x.active and x.f.joins == rnd:
                x.active = True
                _foe_turn(m, x, me, rng)
                if me.dead:
                    return "death", me.hp, rnd, me.spent
        if all(x.out for x in foes):
            return "win", me.hp, rnd, me.spent
    return "capped", me.hp, MAX_ROUNDS, me.spent


NORMAL = Rules()


def fight(me: Fighter, foes: Sequence[Fighter], rules: Rules = NORMAL, seed: int = SEED, bound: float | None = None,
          max_trials: int = MAX_TRIALS) -> Risk:  # fmt: skip
    """The death risk of this fight, in batches of BATCH trials until the Wilson interval clears BOUND (RISK_MAX)."""
    bound = RISK_MAX if bound is None else bound
    m = _Model(me, foes, rules)
    rng = random.Random(seed)
    deaths = wins = capped = trials = 0
    hp_sum = turns = spent = 0
    while trials < max_trials:
        for _ in range(BATCH):
            end, hp, rnd, used = _trial(m, rng)
            trials, turns, spent = trials + 1, turns + rnd, spent + used
            if end == "death":
                deaths += 1
            elif end == "win":
                wins, hp_sum = wins + 1, hp_sum + hp
            else:
                capped += 1
        low, high = wilson(deaths, trials)
        if high < bound or low >= bound:
            break
    low, high = wilson(deaths, trials)
    first = next((f for f in foes if f.joins == 0), foes[0] if foes else None)
    hit = foe_hit = 0
    if first is not None:
        hit = to_hit(me, me.weapon, me.skill, first, first.dist, crit_tables.UNCALLED, True, rules, first.ap_left)
        foe_hit = to_hit(first, first.weapon, first.skill, me, 1, crit_tables.UNCALLED, False, rules)
    return Risk(
        deaths, trials, low, high, capped, wins, hp_sum / wins if wins else 0.0, turns / trials, spent / trials,
        hit, foe_hit,
    )  # fmt: skip


# --- from the game's data ---------------------------------------------------------------------------------------------


def weapon_of(pid: int | None, secondary: bool = False, strength: int = 5) -> Weapon:
    """A weapon proto's attack mode (f1.weapons); None or a mode the weapon lacks: fists."""
    from f1 import weapons

    w = weapons.weapon(pid) if pid is not None else None
    if w is None or weapons.attack_type(pid, secondary) == "none":
        return FISTS
    d = w["weapon"]
    rules = weapons.Rules(strength=strength)
    burst = d.get("burst", 0) if weapons.mode(pid, secondary) in (7, 8) else 0  # type: ignore[arg-type]
    return Weapon(
        name=w.get("name", ""), min_damage=d["min_damage"], max_damage=d["max_damage"],
        damage_type=d["damage_type"], ap=d["ap2"] if secondary else d["ap1"],
        reach=weapons.reach(pid, rules, secondary), kind=weapons.attack_type(pid, secondary), min_st=d["min_st"],
        burst=burst, capacity=d["capacity"], perk=d.get("perk", -1),
    )  # fmt: skip


@cache
def critter_proto(pid: int) -> dict:
    """What f1.knowledge leaves out of a critter's proto: its AI packet, body type and kill type (ints 9, 100, 102
    of the 103 big-endian ints)."""
    import struct

    from f1 import knowledge, paths
    from f1.dat import GameFiles

    p = knowledge.proto(pid) or {}
    try:
        raw = GameFiles(paths.INSTANCE).read(p["file"])
    except (KeyError, OSError):
        return {"ai": 0, "body": 0, "kill_type": 0}
    ints = struct.unpack(f">{len(raw) // 4}i", raw[: len(raw) // 4 * 4])
    return (
        {"ai": ints[9], "body": ints[100], "kill_type": ints[102]}
        if len(ints) > 102
        else {"ai": 0, "body": 0, "kill_type": 0}
    )


def foe(pid: int, dist: int, hp: int | None = None, packet: int | None = None, held: int | None = None,
        loaded: int | None = None, spare: int = 0, joins: int | None = 0, big: bool = False,
        light: int = odds.LIGHT_MAX, ap_left: int = 0) -> Fighter:  # fmt: skip
    """A critter from its proto (knowledge.json and the proto file): its stats, its skills from its SPECIAL and
    points (odds.foe_skill), its AI packet (its own number, else the proto's), the weapon in its hand."""
    from f1 import chargen, knowledge, weapons

    p = knowledge.proto(pid) or {}
    stats = p.get("stats", [0] * 35)
    extra = critter_proto(pid)
    special = tuple(p.get("special", [5] * 7))
    w = weapon_of(held, False, special[0])
    sec = weapon_of(held, True, special[0]) if held is not None else None

    def skill_of(weapon: Weapon, secondary: bool = False) -> int:
        name = weapons.skill(held, secondary) if weapon is not FISTS else "unarmed"
        return odds.foe_skill(p, chargen.SKILL_NAMES.index(name))

    needs = held is not None and weapons.uses_ammo(held)
    return Fighter(
        name=p.get("name", hex(pid)), hp=hp if hp is not None else p.get("hp", 1),
        max_hp=max(p.get("hp", 1), hp or 1), ap=stats[odds.STAT_AP] or 5, ac=stats[odds.STAT_AC],
        special=special, dt=tuple(stats[17:24]), dr=tuple(stats[24:31]), skill=skill_of(w),
        weapon=w, melee=stats[odds.STAT_MELEE], sequence=stats[13], crit_chance=stats[15], better_crits=stats[16],
        kill_type=extra["kill_type"], pid=pid, big=big, dist=dist,
        loaded=(loaded if loaded is not None else w.capacity) if needs else None, spare=spare,
        packet=aipacket.of(packet if packet is not None else extra["ai"]),
        secondary=sec if sec is not FISTS and sec != w else None, secondary_skill=skill_of(sec or FISTS, True),
        unarmed_skill=odds.foe_skill(p, chargen.SKILL_NAMES.index("unarmed")), joins=joins, light=light,
        ap_left=ap_left,
    )  # fmt: skip


def against(actor, foes: list, others: list = ()) -> Risk:  # type: ignore[assignment]
    """The risk of the player's best way to fight (weapons.choose) against these live critters, from where they stand
    now; OTHERS (hostile critters not in the fight yet) join at the first round's end when they notice the player or
    the first foe (combatai.c combatai_notify_onlookers, is_within_perception at combat's distances)."""
    from f1 import geometry, loot, perception, weapons
    from f1.engine_map import Obj

    mem, s = actor.mem, actor.snap()
    st = odds.player_stats(mem)
    options = actor.weapon_options()
    o = options[0] if options else None
    pid = o.pid if o else None
    carried = next((c for c in actor.carried_weapons() if c.pid == pid), None)
    w = weapon_of(pid, False, st[0])
    skills = actor.skills()
    me = Fighter(
        name="player", hp=s.dude.hp, max_hp=actor.max_hp, ap=actor.max_ap, ac=st[odds.STAT_AC],
        special=tuple(st[:7]), dt=tuple(st[17:24]), dr=tuple(st[24:31]),
        skill=o.skill_value if o else skills.get("unarmed", 0), weapon=w, melee=st[odds.STAT_MELEE],
        sequence=st[13], crit_chance=st[15], better_crits=st[16], kill_type=None,
        loaded=carried.loaded if carried and weapons.uses_ammo(pid) else None,
        spare=carried.spare if carried else 0, unarmed_skill=skills.get("unarmed", 0),
        stimpaks=actor._count(0x28),
    )  # fmt: skip

    def live(c, joins: int | None) -> Fighter:
        held, loaded, spare = None, None, 0
        for item, ipid, qty in loot.inventory(mem, c.address):
            if mem.u32(item + Obj.FLAGS) & odds.IN_HAND and weapons.weapon(ipid):
                held, loaded = ipid, mem.i32(item + Obj.ITEM_AMMO)
        if held is not None and weapons.uses_ammo(held):
            spare = sum(
                qty * ((knowledge_proto(ipid).get("ammo") or {}).get("quantity", 0))
                for _, ipid, qty in loot.inventory(mem, c.address)
                if weapons.fits(held, ipid)
            )
        return foe(
            c.pid, geometry.distance(c.tile, s.dude.tile) - (1 if c.multihex else 0), hp=c.hp,
            packet=mem.i32(c.address + Obj.CRITTER_AI_PACKET), held=held, loaded=loaded, spare=spare, joins=joins,
            big=c.multihex, light=odds.visible_light(mem, c.elevation, c.tile), ap_left=max(0, c.ap),
        )  # fmt: skip

    first = foes[0] if foes else None
    them = [live(c, 0) for c in foes]
    for c in others:
        pe = perception.perception_of(c.pid)
        sees = perception.notices(c.tile, c.rotation, pe, s.dude.tile, in_combat=True) or (
            first is not None and perception.notices(c.tile, c.rotation, pe, first.tile, in_combat=True)
        )
        them.append(live(c, 1 if sees else None))
    return fight(me, them, Rules(mem.glob("combat_difficulty"), mem.glob("fallout_game_time")))


def knowledge_proto(pid: int) -> dict:
    from f1 import knowledge

    return knowledge.proto(pid) or {}


def as_odds(r: Risk) -> odds.Odds:
    """The risk in the odds' shape (callers log win, hp_left, turns, rounds, hit, foe_hit), with its verdict."""
    return odds.Odds(
        win=r.wins / r.trials if r.trials else 0.0, hp_left=r.hp_left, turns=r.turns, rounds=r.rounds, hit=r.hit,
        foe_hit=r.foe_hit, trials=r.trials, death_high=r.high, accept=acceptable(r),
    )  # fmt: skip


# --- canned fights: the old odds against the risk, offline ------------------------------------------------------------


def canned_player(level: str = "early") -> Fighter:
    """Two player sheets for comparisons: an early one (10mm pistol, leather jacket) and a later one (plasma rifle,
    combat armour). Plain numbers, not a saved character."""
    special = (6, 8, 5, 4, 7, 8, 5)
    if level == "early":
        return Fighter(
            name="early", hp=32, max_hp=32, ap=9, ac=13, special=special, dt=(0,) * 7, dr=(20, 0, 10, 10, 0, 500, 20),
            skill=75, weapon=weapon_of(0x08), melee=1, sequence=16, crit_chance=5, kill_type=None, loaded=12,
            spare=24, unarmed_skill=70, stimpaks=2,
        )  # fmt: skip
    return Fighter(
        name="late", hp=70, max_hp=70, ap=9, ac=28, special=special, dt=(5, 8, 4, 4, 2, 500, 6),
        dr=(40, 60, 25, 30, 20, 500, 40), skill=110, weapon=weapon_of(0x0F), melee=1, sequence=16, crit_chance=5,
        kill_type=None, loaded=10, spare=40, unarmed_skill=80, stimpaks=4,
    )  # fmt: skip


CANNED = (  # map, the critters' proto names, how many, hexes off, the player's sheet
    ("V13ENT.MAP", "Cave Rat", 1, 6, "early"),
    ("V13ENT.MAP", "Cave Rat", 4, 6, "early"),
    ("CAVES.MAP", "Radscorpion", 1, 8, "early"),
    ("CAVES.MAP", "Radscorpion", 2, 8, "early"),
    ("RAIDERS.MAP", "Raider", 3, 10, "early"),
    ("RAIDERS.MAP", "Raider", 3, 10, "late"),
    ("SHADYW.MAP", "Dog", 1, 6, "early"),
    ("SHADYW.MAP", "Dog", 3, 6, "early"),
    ("LARIPPER.MAP", "Mother Deathclaw", 1, 8, "late"),
    ("DETHCLAW.MAP", "Deathclaw", 1, 8, "late"),
)


def _map_foes(name: str, critter: str, count: int, dist: int) -> list[Fighter]:
    from f1 import mapfile, weapons

    found = [o for o in mapfile.read(name).objects if o.kind == "critter" and o.name == critter][:count]
    out = []
    for o in found:
        held = next((it.pid for _, it in o.inventory if it.flags & odds.IN_HAND and weapons.weapon(it.pid)), None)
        held_item = next((it for _, it in o.inventory if it.pid == held), None)
        spare = 0
        if held is not None and weapons.uses_ammo(held):
            spare = sum(
                n * (knowledge_proto(it.pid).get("ammo") or {}).get("quantity", 0)
                for n, it in o.inventory
                if weapons.fits(held, it.pid)
            )
        loaded = held_item.data.get("rounds") if held_item is not None else None
        out.append(
            foe(
                o.pid, dist + len(out), hp=o.data.get("hp") or None, packet=o.data.get("ai"), held=held,
                loaded=loaded, spare=spare, big=bool(o.flags & 0x800),
            )
        )  # fmt: skip
    return out


def _old(me: Fighter, foes: list[Fighter], rules: Rules) -> odds.Odds:
    """odds.fight on the same sheets, as odds.against builds its inputs (foes unarmed, the first foe's to-hit)."""
    first = foes[0]
    hit = odds.to_hit(
        me.skill, first.ac, ranged=me.weapon.ranged, dist=min(first.dist, odds.CLOSE_RANGE) if me.weapon.ranged
        else first.dist, pe=me.special[1], big=first.big, light=first.light,
    )  # fmt: skip
    melee = me.melee if not me.weapon.ranged else 0
    a = odds.Attacker(
        hp=me.hp, ap=me.ap, hit=hit, dmg=(me.weapon.min_damage, me.weapon.max_damage + melee), cost=me.weapon.ap,
        dt=me.dt[0], dr=me.dr[0], mag=me.loaded, spare=me.spare, capacity=me.weapon.capacity,
    )  # fmt: skip
    them = [
        odds.Attacker(
            hp=f.hp,
            ap=f.ap,
            hit=odds.to_hit(f.unarmed_skill, me.ac, player=False, difficulty=rules.difficulty),
            dmg=(1, f.melee + 2),
            cost=odds.UNARMED_AP,
            dt=f.dt[0],
            dr=f.dr[0],
            mult=odds.DIFFICULTY_DAMAGE.get(rules.difficulty, 1.0),
        )
        for f in foes
    ]
    return odds.fight(a, them)


def compare(rules: Rules = NORMAL) -> list[str]:
    lines = []
    for name, critter, count, dist, sheet in CANNED:
        foes = _map_foes(name, critter, count, dist)
        if not foes:
            lines.append(f"{name} {critter}: none on the map")
            continue
        me = canned_player(sheet)
        old = _old(me, foes, rules)
        new = fight(me, foes, rules)
        w = foes[0].weapon.name
        lines.append(
            f"{critter} x{len(foes)} ({w}, HP {foes[0].hp}, packet {foes[0].packet.num if foes[0].packet else '-'})"
            f" vs {sheet} ({me.weapon.name}): old win {old.win:.1%} | risk death {new.death:.2%} [{new.low:.2%}, {new.high:.2%}]"
            f" n={new.trials} capped {new.capped} win {new.wins / new.trials:.1%} -> "
            f"{'enter' if acceptable(new) else 'refuse'} (old: {'enter' if old.win >= 0.8 else 'refuse'})"
        )
    return lines


def main(argv: list[str]) -> int:
    if argv[:1] == ["compare"]:
        difficulty = int(argv[1]) if len(argv) > 1 else 1
        for line in compare(Rules(difficulty=difficulty)):
            print(line)
        return 0
    if argv[:1] == ["tables"]:
        t = crit_tables.tables()
        print(f"critical tables at 0x{t.address:X}: {len(t.critters)} kill types, the player's after them")
        return 0
    print("python -m f1.risk compare [difficulty]   the old odds against the risk on canned fights from the maps")
    print("python -m f1.risk tables                  where the critical tables are in the exe")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
