"""The critters' AI packets (DATA\\AI.TXT in the game's DATs) and the attack choices the AI makes from them, as
fallout1-ce combatai.c has them (static analysis).

combat_ai_init reads every section with the keys packet_num, max_dist, min_to_hit, min_hp, aggression, hurt_too_much
(words: blind, crippled, crippled_legs, crippled_arms, as DAM_* bits), secondary_freq, called_freq and the message
ranges. Fallout 1's packets have none of Fallout 2's area_attack_mode, best_weapon, chem_use or distance keys. A
critter's packet is the number in its combat data (a map object's own, from its proto at creation); a number no
section carries gets the first section of the config's list (ai_cap; the config's list is kept sorted by name).

What the AI does with a packet (combatai.c, Fallout 1's):
- it runs away (ai_run_away, no attack) when its hit points are under min_hp, or its results hold any hurt_too_much
  bit, at the start of its turn (combat_ai); also when its to-hit without the range modifier is under min_to_hit
  (ai_try_attack: it cannot hope to hit, coming closer would not help);
- out of reach, or in reach but under min_to_hit with the range modifier, it walks closer (ai_move_closer);
- the hit mode (ai_pick_hit_mode, once a turn): the secondary when the weapon has one it can use and a d(secondary_freq)
  rolls 1;
- each attack may be a called shot (ai_called_shot): when the AP pay for the aimed attack, the weapon allows aiming
  (item_w_called_shot: no burst, no explosive, fire or EMP damage), a d(called_freq) rolls 1 and its IN is at least
  7, 5 or 3 on Wimpy, Normal, Rough: a location d9 - 1 (0..8, 8 uncalled), the torso when its to-hit there is under
  min_to_hit. A shot at the torso or uncalled costs the plain AP, any other location 1 more (combat_attack's aiming).
"""

from dataclasses import dataclass
from functools import cache

from f1 import paths

HURT_WORDS = {"blind": 0x40, "crippled": 0x3C, "crippled_legs": 0x0C, "crippled_arms": 0x30}
MIN_IN = {0: 7, 1: 5, 2: 3}  # combat difficulty Wimpy, Normal, Rough: the IN a called shot wants
TORSO, UNCALLED = 3, 8


@dataclass(frozen=True)
class Packet:
    name: str
    num: int
    max_dist: int = 0
    min_to_hit: int = 0
    min_hp: int = 0
    aggression: int = 0
    hurt_too_much: int = 0
    secondary_freq: int = 1
    called_freq: int = 1


def _hurt(text: str) -> int:
    value = 0
    for word in text.lower().split(","):
        value |= HURT_WORDS.get(word.strip(), 0)
    return value


def parse(text: str) -> list[Packet]:
    """The sections in the file's order (comments after ';' dropped)."""
    sections: list[tuple[str, dict[str, str]]] = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1].strip(), {}))
        elif "=" in line and sections:
            key, value = line.split("=", 1)
            sections[-1][1][key.strip().lower()] = value.strip()
    out = []
    for name, kv in sections:
        if "packet_num" not in kv:
            continue

        def num(key: str, default: int = 0, kv: dict[str, str] = kv) -> int:
            try:
                return int(kv.get(key, default))
            except ValueError:
                return default

        out.append(
            Packet(
                name, num("packet_num"), num("max_dist"), num("min_to_hit"), num("min_hp"), num("aggression"),
                _hurt(kv.get("hurt_too_much", "")), max(1, num("secondary_freq", 1)), max(1, num("called_freq", 1)),
            )
        )  # fmt: skip
    return out


@cache
def packets() -> tuple[Packet, ...]:
    from f1.dat import GameFiles

    return tuple(parse(GameFiles(paths.INSTANCE).read("DATA/AI.TXT").decode("latin1")))


def of(num: int, table: tuple[Packet, ...] | None = None) -> Packet:
    """ai_cap: the packet with this number, else the first of the config's list (sorted by section name)."""
    table = packets() if table is None else table
    for p in table:
        if p.num == num:
            return p
    return min(table, key=lambda p: p.name.lower()) if table else Packet("", num)


def called_locations(p: Packet, intelligence: int, difficulty: int, can_aim: bool, to_hit_at) -> dict[int, float]:
    """The chance of each location of one attack (ai_called_shot); TO_HIT_AT(location) is the to-hit there. The
    torso and uncalled cost the same and hit the same rows (the torso's), so both are given as the torso."""
    if not can_aim or intelligence < MIN_IN.get(difficulty, 5):
        return {TORSO: 1.0}
    called = 1.0 / p.called_freq
    out: dict[int, float] = {TORSO: 1.0 - called}
    for loc in range(9):
        where = TORSO if loc == UNCALLED or to_hit_at(loc) < p.min_to_hit else loc
        out[where] = out.get(where, 0.0) + called / 9
    return out
