"""Weapons by their prototypes (f1/knowledge.py): the skill, reach and AP cost of an attack and the ammunition that
fits, by item.c's rules (fallout1-ce: attack_subtype 0x505780, item_w_skill 0x46AB30, item_w_range 0x46B104,
item_w_mp_cost 0x46B1E8, item_w_can_reload 0x46AFB8), and the weapon a character fights best with.

    python -m f1.weapons          the player's weapons as the choice sees them (the game running)
    python -m f1.weapons ready    the best of them into the active hand first
    python -m f1.weapons all      every weapon prototype
"""

import sys
from dataclasses import dataclass

from f1 import knowledge, paths

# attack_subtype and attack_skill by attack mode, the extended flags' nibble: none, punch, kick, swing, thrust, throw,
# single, burst, continuous
ATTACK_TYPES = ("none", "unarmed", "unarmed", "melee", "melee", "throw", "ranged", "ranged", "ranged")
ATTACK_SKILLS = ("", "unarmed", "unarmed", "melee weapons", "melee weapons", "throwing") + ("small guns",) * 3
MODE_BURST = 7
ENERGY_DAMAGE = {1, 3, 4}  # laser, plasma, electrical (proto_types.h DAMAGE_TYPE_*): Energy Weapons
BIG_GUN, TWO_HANDED = 0x100, 0x200
UNARMED_AP, RELOAD_AP = 3, 2
PERK_BONUS_HTH_ATTACKS, PERK_BONUS_RATE_OF_FIRE, PERK_HEAVE_HO = 1, 5, 35  # perk_defs.h
TRAIT_FAST_SHOT = 7  # trait.h
# The interface bar's actions for a hand (intface.c): 1 primary, 2 primary aimed, 3 secondary, 4 secondary aimed.
SECONDARY_ACTIONS, AIMED_ACTIONS = (3, 4), (2, 4)


@dataclass(frozen=True)
class Rules:
    """What the character brings to an attack: ST (a thrown weapon's reach), Fast Shot, and the perks that change AP
    or reach (read from memory by the Actor)."""

    strength: int = 5
    fast_shot: bool = False
    bonus_hth_attacks: bool = False
    bonus_rate_of_fire: bool = False
    heave_ho: int = 0


def weapon(pid: int) -> dict | None:
    """The weapon prototype (name, flags_ext, weapon data), or None for anything that is not a weapon."""
    p = knowledge.proto(pid)
    return p if p and "weapon" in p else None


def mode(pid: int, secondary: bool = False) -> int:
    fx = weapon(pid)["flags_ext"]  # type: ignore[index]
    return fx >> 4 & 0xF if secondary else fx & 0xF


def attack_type(pid: int | None, secondary: bool = False) -> str:
    """unarmed, melee, throw or ranged (none for a mode the weapon lacks); no weapon is unarmed."""
    return "unarmed" if pid is None else ATTACK_TYPES[mode(pid, secondary)]


def skill(pid: int | None, secondary: bool = False) -> str:
    """The skill an attack uses (chargen.SKILL_NAMES): guns doing laser, plasma or electrical damage use Energy
    Weapons, guns flagged big use Big Guns."""
    if pid is None:
        return "unarmed"
    p = weapon(pid)
    name = ATTACK_SKILLS[mode(pid, secondary)]
    if name == "small guns" and p["weapon"]["damage_type"] in ENERGY_DAMAGE:  # type: ignore[index]
        return "energy weapons"
    if name == "small guns" and p["flags_ext"] & BIG_GUN:  # type: ignore[index]
        return "big guns"
    return name


def reach(pid: int | None, rules: Rules, secondary: bool = False) -> int:
    """Hexes an attack reaches: the proto's range; a thrown one no farther than 3 x (ST + 2 x Heave Ho)."""
    if pid is None:
        return 1
    w = weapon(pid)["weapon"]  # type: ignore[index]
    hexes = w["range2"] if secondary else w["range1"]
    if attack_type(pid, secondary) == "throw":
        hexes = min(hexes, 3 * (rules.strength + 2 * rules.heave_ho))
    return hexes


def ap_cost(pid: int | None, rules: Rules, secondary: bool = False, aimed: bool = False) -> int:
    """AP of one attack: the proto's cost (3 unarmed), -1 for Fast Shot with a weapon, -1 for Bonus HtH Attacks in
    melee or unarmed, -1 for Bonus Rate of Fire with a gun, +1 aimed; at least 1."""
    if pid is None:
        ap = UNARMED_AP
    else:
        w = weapon(pid)["weapon"]  # type: ignore[index]
        ap = (w["ap2"] if secondary else w["ap1"]) - rules.fast_shot
    kind = attack_type(pid, secondary)
    ap -= rules.bonus_hth_attacks and kind in ("melee", "unarmed")
    ap -= rules.bonus_rate_of_fire and kind == "ranged"
    return max(1, ap + aimed)


def uses_ammo(pid: int | None) -> bool:
    w = weapon(pid) if pid is not None else None
    return bool(w) and w["weapon"]["capacity"] > 0  # type: ignore[index]


def fits(weapon_pid: int, ammo_pid: int, loaded: int = 0, loaded_pid: int | None = None) -> bool:
    """item_w_can_reload: the calibers match, and a weapon that is not empty takes only the ammunition it holds."""
    w, a = weapon(weapon_pid), knowledge.proto(ammo_pid)
    if not w or not a or "ammo" not in a or w["weapon"]["caliber"] != a["ammo"]["caliber"]:
        return False
    return loaded == 0 or loaded_pid == ammo_pid


@dataclass(frozen=True)
class Carried:
    """A weapon the player has: its pid, the rounds in it and of which ammunition, and the rounds carried that fit."""

    pid: int
    loaded: int = 0
    loaded_pid: int = -1
    spare: int = 0


@dataclass(frozen=True)
class Option:
    pid: int | None  # None: fists
    name: str
    skill: str
    skill_value: int
    ap: int
    reach: int
    attacks: int  # a turn, from full AP
    hit: float  # the to-hit guess
    damage: float  # average of an attack
    rounds: int | None  # left to fire (None: needs none)
    score: float  # expected damage of a turn


def option(c: Carried | None, skills: dict[str, int], rules: Rules, max_ap: int) -> Option | None:
    """How a weapon (None: fists) would fare over a turn; None when it cannot attack: thrown and continuous weapons
    (thrown ones are spent, flamers are left out for now), a gun without a round, a mode it lacks."""
    pid = c.pid if c else None
    kind = attack_type(pid)
    if kind in ("none", "throw") or (pid is not None and mode(pid) == 8):
        return None
    rounds = c.loaded + c.spare if c and uses_ammo(pid) else None
    if rounds == 0:
        return None
    w = weapon(pid)["weapon"] if pid is not None else {"min_damage": 1, "max_damage": 2, "min_st": 1, "burst": 0}
    name = weapon(pid)["name"] if pid is not None else "fists"  # type: ignore[index]
    uses = skill(pid)
    value = skills.get(uses, 0) - 20 * max(0, w["min_st"] - rules.strength)  # -20 % per point of ST short
    hit = min(95, max(5, value)) / 100
    ap = ap_cost(pid, rules)
    attacks = max_ap // ap if ap <= max_ap else 0
    damage = (w["min_damage"] + w["max_damage"]) / 2
    if pid is not None and mode(pid) == MODE_BURST:  # a burst: several rounds, some miss
        damage *= max(1, min(w["burst"], rounds or 0)) / 2
    close_in = 0.75 if kind in ("melee", "unarmed") else 1.0  # AP spent walking up to the foe
    return Option(
        pid, name, uses, value, ap, reach(pid, rules), attacks, hit, damage, rounds, hit * damage * attacks * close_in
    )


def choose(carried: list[Carried], skills: dict[str, int], rules: Rules, max_ap: int) -> list[Option]:
    """The ways to fight, best first by expected damage in a turn (attacks from full AP x average damage x a to-hit
    guess from the skill, 5..95 %). Fists are always one of them."""
    options = [o for c in [*carried, None] if (o := option(c, skills, rules, max_ap)) is not None]
    return sorted(options, key=lambda o: -o.score)


def main(argv: list[str]) -> int:
    if argv[:1] == ["all"]:
        rules = Rules()
        for key, p in sorted(knowledge.load()["protos"].items()):
            if "weapon" not in p:
                continue
            pid, w = int(key, 16), p["weapon"]
            print(
                f"{key} {p['name'][:22]:22} {attack_type(pid):7} {skill(pid):14} {w['min_damage']:>3}-{w['max_damage']:<3}"
                f" AP {ap_cost(pid, rules)} reach {reach(pid, rules):>2} ST {w['min_st']}"
                f" ammo {knowledge.proto_name(w['ammo_pid']) if w['capacity'] else '-'}"
            )
        return 0
    import datetime

    from f1 import session
    from f1.actions import Actor
    from f1.telemetry import EventLog

    pid = session.game_pid()
    if not pid:
        print("the game is not running")
        return 2
    run = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-weapons"
    actor = Actor(pid, EventLog(run / "events.jsonl"))
    try:
        if argv[:1] == ["ready"]:
            print(actor.ready_weapon())
        held = actor.weapon()
        rules = actor.rules()
        print(
            f"in hand: {knowledge.proto_name(held.pid) if held else 'nothing'} {held}; {rules}; max AP {actor.max_ap}"
        )
        print(
            f"attack: {actor.attack_cost(held)} AP, reach {actor.attack_reach(held)} (item action {actor.item_action()})"
        )
        for o in choose(actor.carried_weapons(), actor.skills(), rules, actor.max_ap):
            print(
                f"  {o.name:22} {o.skill:14} {o.skill_value:>3} %  AP {o.ap}  reach {o.reach:>2}  {o.attacks}/turn"
                f"  dmg {o.damage:5.1f}  hit {o.hit:.2f}  rounds {o.rounds if o.rounds is not None else '-':>4}"
                f"  score {o.score:5.1f}"
            )
    finally:
        actor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
