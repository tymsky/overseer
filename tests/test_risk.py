"""The risk model (f1.risk) and its parts, offline: the critical tables' reader on a made-up blob, the AI packets'
parser, one attack's outcome table, and a canned fight's bound that its seed fixes."""

import struct
from fractions import Fraction

import pytest

from f1 import aipacket, crit_tables, odds, risk


def _blob(rows: list[tuple[int, ...]], pad: bytes = b"\x00" * 64) -> bytes:
    body = struct.pack("<9i", *crit_tables.LOCATION_PENALTY)
    body += b"".join(struct.pack("<7i", *r) for r in rows)
    return pad + body + pad


def _rows(n: int) -> list[tuple[int, ...]]:
    first = crit_tables.FIRST_ROW
    return [first] + [(3, 0, -1, 0, 0, 5016, 5000)] * (n - 1)


COUNT = len(crit_tables.KILL_TYPES) * 9 * 6 + 9 * 6


def test_the_tables_are_found_after_the_location_penalties() -> None:
    rows = _rows(COUNT)
    rows[-1] = (6, crit_tables.DAM_DEAD, -1, 0, 0, 6505, 5000)  # the player's last uncalled row
    t = crit_tables.parse(_blob(rows))
    assert len(t.critters) == 15 and len(t.critters[0]) == 9 and len(t.critters[0][0]) == 6
    assert t.critters[0][crit_tables.HEAD][0].message == 5001
    assert t.player[crit_tables.UNCALLED][5].flags == crit_tables.DAM_DEAD
    assert t.address == -1  # not a PE file: no virtual address
    # an uncalled shot takes the torso's rows
    assert t.rows(None, crit_tables.UNCALLED) is t.player[crit_tables.TORSO]


def test_rows_that_are_not_rows_are_refused() -> None:
    rows = _rows(COUNT)
    rows[5] = (0, 0, -1, 0, 0, 5016, 5000)  # a multiplier of 0
    with pytest.raises(ValueError):
        crit_tables.parse(_blob(rows))
    with pytest.raises(ValueError):
        crit_tables.parse(b"\x00" * 1000)


def test_the_row_odds_by_better_criticals() -> None:
    assert crit_tables.row_odds(0) == tuple(Fraction(n, 100) for n in (20, 25, 25, 20, 10, 0))
    assert crit_tables.row_odds(20) == tuple(Fraction(n, 100) for n in (0, 25, 25, 20, 10, 20))


def test_a_massive_part_applies_when_a_d10_beats_the_stat() -> None:
    row = crit_tables.Row(4, 0, 2, -3, crit_tables.DAM_KNOCKED_OUT, 5002, 5003)
    assert crit_tables.massive_odds(row, 5) == Fraction(8, 10)  # EN 5 - 3: a d10 of 3..10
    assert crit_tables.massive_odds(row, None) == 0
    assert crit_tables.massive_odds(crit_tables.Row(4, 0, -1, 0, 0, 1, 1), 5) == 0


AI_TEXT = """; a comment
[Zeta]
packet_num=7
min_to_hit=20
min_hp=5
hurt_too_much=blind, crippled_legs
secondary_freq=4
called_freq=3
[Alpha]
packet_num=2 ; trailing comment
called_freq=0
"""


def test_the_packets_parse_and_a_missing_number_takes_the_first_by_name() -> None:
    packets = tuple(aipacket.parse(AI_TEXT))
    zeta = aipacket.of(7, packets)
    assert (zeta.min_to_hit, zeta.min_hp, zeta.secondary_freq, zeta.called_freq) == (20, 5, 4, 3)
    assert zeta.hurt_too_much == 0x40 | 0x0C
    assert aipacket.of(99, packets).name == "Alpha"
    assert aipacket.of(2, packets).called_freq == 1  # a frequency of 0 is read as 1


def test_called_locations() -> None:
    p = aipacket.Packet("x", 1, min_to_hit=30, called_freq=3)
    assert aipacket.called_locations(p, 4, 1, True, lambda loc: 50) == {aipacket.TORSO: 1.0}  # IN 4 < 5 on Normal
    locs = aipacket.called_locations(p, 6, 1, True, lambda loc: 20 if loc == 6 else 50)  # the eyes under min_to_hit
    assert sum(locs.values()) == pytest.approx(1.0)
    assert 6 not in locs and locs[aipacket.TORSO] == pytest.approx(2 / 3 + 3 / 27)


def _fighter(**kw) -> risk.Fighter:
    base = {
        "name": "x", "hp": 30, "max_hp": 30, "ap": 8, "ac": 5, "special": (5,) * 7, "dt": (0,) * 7, "dr": (0,) * 7,
        "skill": 60, "kill_type": 0,
    }  # fmt: skip
    base.update(kw)
    return risk.Fighter(**base)


NO_CRITS = risk.Rules(game_time=0)


def test_to_hit_matches_the_old_formula_on_a_plain_shot() -> None:
    pistol = risk.Weapon("pistol", 5, 12, 0, 5, 25, "ranged", 3, 0, 12)
    me, foe = _fighter(special=(6, 6, 5, 5, 5, 5, 5), skill=55), _fighter(ac=15, big=True, light=20000)
    new = risk.to_hit(me, pistol, 55, foe, 5, crit_tables.UNCALLED, True, NO_CRITS)
    assert new == odds.to_hit(55, 15, ranged=True, dist=5, pe=6, big=True, light=20000)
    # an aimed melee blow at the eyes loses half the eyes' 60
    fist = risk.to_hit(me, risk.FISTS, 55, _fighter(ac=0), 1, crit_tables.EYES, False, NO_CRITS)
    assert fist == 55 - 30


def test_one_attack_without_criticals() -> None:
    me, foe = _fighter(skill=200, melee=0), _fighter(dt=(2,) + (0,) * 6, dr=(50,) + (0,) * 6)
    hit, table = risk.attack_table(me, risk.FISTS, 200, foe, 1, crit_tables.UNCALLED, True, NO_CRITS)
    assert hit == 95
    # fists 1..2: less DT 2 leaves nothing
    assert table.mean == 0
    club = risk.Weapon("club", 10, 10, 0, 3, 1, "melee")
    _, table = risk.attack_table(me, club, 200, foe, 1, crit_tables.UNCALLED, True, NO_CRITS)
    assert table.mean == pytest.approx(0.95 * 4)  # (10 - 2) less 50 %


def test_a_bypassing_critical_takes_no_armour_in_fallout_1(monkeypatch: pytest.MonkeyPatch) -> None:
    row = crit_tables.Row(4, crit_tables.DAM_BYPASS, -1, 0, 0, 5017, 5000)
    rows = tuple(tuple((row,) * 6 for _ in range(9)) for _ in range(15))
    monkeypatch.setattr(crit_tables, "tables", lambda: crit_tables.Tables(rows, rows[0], 0))
    me = _fighter(skill=95, crit_chance=100)  # every hit critical
    foe = _fighter(ac=0, dt=(5,) + (0,) * 6, dr=(50,) + (0,) * 6)
    club = risk.Weapon("club", 10, 10, 0, 3, 1, "melee")
    _, table = risk.attack_table(me, club, 95, foe, 1, crit_tables.UNCALLED, True, risk.Rules())
    assert table.mean == pytest.approx(0.95 * 20)  # x2, no DT, no DR


def test_a_canned_fights_bound_is_fixed_by_its_seed() -> None:
    me = _fighter(name="me", hp=25, max_hp=25, skill=70, weapon=risk.Weapon("club", 3, 8, 0, 3, 1, "melee"))
    packet = aipacket.Packet("rat", 1, min_to_hit=0, min_hp=0)
    rats = [_fighter(name="rat", hp=12, max_hp=12, ap=6, skill=60, melee=2, dist=4 + i, packet=packet)
            for i in range(3)]  # fmt: skip
    a = risk.fight(me, rats, NO_CRITS, seed=7)
    b = risk.fight(me, rats, NO_CRITS, seed=7)
    assert a == b
    assert a.trials % risk.BATCH == 0 and a.deaths + a.wins + a.capped == a.trials
    assert a.low <= a.death <= a.high


def test_a_foe_under_its_min_hp_runs_and_a_joiner_comes() -> None:
    me = _fighter(name="me", hp=100, max_hp=100, skill=95, weapon=risk.Weapon("club", 1, 1, 0, 3, 1, "melee"))
    runner = _fighter(name="r", hp=3, max_hp=30, dist=1, packet=aipacket.Packet("r", 1, min_hp=5))
    r = risk.fight(me, [runner], NO_CRITS)
    assert r.wins == r.trials and r.deaths == 0  # it runs at its first turn: the fight is won
    never = _fighter(name="n", hp=1000, max_hp=1000, dist=1, joins=None)
    assert risk.fight(me, [runner, never], NO_CRITS).wins == r.trials  # one that never joins does not count


def test_the_gate_and_the_old_rule() -> None:
    r = risk.Risk(0, 3200, 0.0, 0.0012, 0, 3200, 20.0, 3.0, 4.0, 80, 40)
    assert risk.acceptable(r) and risk.as_odds(r).accept is True
    assert not risk.acceptable(risk.Risk(0, 3200, 0.0, 0.0012, 400, 2800, 20.0, 3.0, 4.0, 80, 40))  # many unfinished
    assert not risk.acceptable(risk.Risk(100, 3200, 0.025, 0.04, 0, 3100, 20.0, 3.0, 4.0, 80, 40))
    old = odds.Odds(win=0.85, hp_left=10, turns=3, rounds=4, hit=70, foe_hit=40)
    assert odds.accepted(old, 0.8) and not odds.accepted(old, 0.9)
    assert not odds.accepted(risk.as_odds(risk.Risk(100, 3200, 0.025, 0.04, 0, 3100, 1, 1, 1, 1, 1)), 0.0)


def test_a_holstered_gun_is_the_weapon_weighed_in_combat(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ironman run: Seth's talk holstered the gun, a radscorpion in the caves began a fight and fists were
    weighed (death bound 1.0), refused and fled; the fight's first turn would have drawn the gun (Actor.rearm)."""
    from types import SimpleNamespace

    from f1 import weapons
    from f1.action_shared import Held

    pistol = 8
    weighed = []
    monkeypatch.setattr(risk.odds, "player_stats", lambda mem: [5] * 35)
    monkeypatch.setattr(risk, "weapon_of", lambda pid, secondary, st: weighed.append(pid) or risk.FISTS)
    monkeypatch.setattr(weapons, "skill", lambda pid, secondary=False: "small_guns")
    monkeypatch.setattr(weapons, "uses_ammo", lambda pid: True)
    actor = SimpleNamespace(
        mem=None, max_hp=43, max_ap=9, in_combat=lambda: True, weapon=lambda: None,
        holstered=lambda: Held(pistol, 6, 0), carried_weapons=list, skills=lambda: {"small_guns": 72},
        _count=lambda pid: 0, snap=lambda: SimpleNamespace(dude=SimpleNamespace(hp=43, ap=9)),
    )  # fmt: skip
    fighter = risk.player(actor)
    assert weighed == [pistol] and fighter.loaded == 6
