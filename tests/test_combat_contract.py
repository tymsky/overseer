"""Observed turn state, participant escalation, encounter identity and safe movement contracts."""

import random
import struct
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

from f1 import (
    action_combat,
    actions,
    clock,
    crit_tables,
    geometry,
    knowledge,
    odds,
    perception,
    risk,
    state,
    weapons,
    win32,
)
from f1.engine_map import Obj


def fighter(**changes):
    defaults = {
        "name": "fighter",
        "hp": 40,
        "max_hp": 40,
        "ap": 10,
        "ac": 0,
        "special": (5,) * 7,
        "dt": (0,) * 7,
        "dr": (0,) * 7,
        "skill": 95,
        "kill_type": 0,
    }
    return risk.Fighter(**(defaults | changes))


class Log:
    def __init__(self):
        self.events = []

    def emit(self, kind, **detail):
        self.events.append((kind, detail))


def test_flare_opener_keeps_five_ap_but_next_turn_recovers_full_ap(monkeypatch):
    pistol = risk.Weapon("gun", 5, 5, ap=5, reach=25, kind="ranged", capacity=12)
    me = fighter(weapon=pistol, loaded=12, initial_ap=5)
    foe = fighter(hp=1000)
    model = risk._Model(me, [foe], risk.Rules(game_time=0))
    monkeypatch.setattr(model, "table", lambda *args: (95, SimpleNamespace(draw=lambda rng: (5, False))))
    live = risk._Foe(foe, foe.hp, 1, 0, None, 0, risk.FISTS, 95, True)
    current = risk._Me(me.hp, 0, 12, 0, pistol, 95, 0)
    risk._player_turn(model, current, [live], random.Random(0), initial=True)
    assert current.spent == 1 and current.ap_left == 0
    risk._player_turn(model, current, [live], random.Random(0))
    assert current.spent == 3 and current.ap_left == 0


def test_live_risk_uses_equipped_weapon_and_five_or_zero_ap(monkeypatch):
    from f1 import chargen

    dude = SimpleNamespace(hp=40, ap=5)
    actor = SimpleNamespace(
        mem=None,
        snap=lambda: SimpleNamespace(dude=dude),
        in_combat=lambda: True,
        weapon=lambda: actions.Held(8, 7, -1),
        max_hp=40,
        max_ap=10,
        carried_weapons=lambda: [SimpleNamespace(pid=8, loaded=7, spare=15)],
        skills=lambda: {"small_guns": 60},
        _count=lambda pid: 3,
    )
    stats = [5] * 35
    monkeypatch.setattr(odds, "player_stats", lambda mem: stats)
    monkeypatch.setattr(weapons, "skill", lambda pid: chargen.SKILL_NAMES[0])
    monkeypatch.setattr(weapons, "uses_ammo", lambda pid: True)
    pistol = risk.Weapon("gun", 5, 5, ap=5, reach=25, kind="ranged", capacity=12)
    monkeypatch.setattr(risk, "weapon_of", lambda pid, *args: pistol if pid == 8 else risk.FISTS)
    first = risk.player(actor)
    assert first.initial_ap == 5 and first.ap == 10 and first.loaded == 7 and first.weapon == pistol
    dude.ap = 0
    assert risk.player(actor).initial_ap == 0


def test_current_round_does_not_replay_foes_that_acted_before_player(monkeypatch):
    trace = []
    foes = [fighter(name="before"), fighter(name="after")]
    rules = risk.Rules(game_time=0, first_foes=(1,))
    model = risk._Model(fighter(sequence=100), foes, rules)
    monkeypatch.setattr(risk, "MAX_ROUNDS", 2)
    monkeypatch.setattr(risk, "_player_turn", lambda *args, **kw: trace.append("player"))
    monkeypatch.setattr(risk, "_foe_turn", lambda m, foe, *args: trace.append(foe.f.name))
    risk._trial(model, random.Random(0))
    assert trace == ["player", "after", "player", "before", "after"]


def test_reachable_melee_and_distant_gun_are_in_next_turn_danger():
    me = fighter()
    rules = risk.Rules(game_time=0)
    claw = risk.Weapon("claw", 8, 8, ap=3)
    reachable = fighter(weapon=claw, ap=7, dist=4)
    assert risk.turn_threat(reachable, me, rules) == 8
    assert risk.turn_threat(replace(reachable, dist=9), me, rules) == 0
    gun = fighter(weapon=risk.Weapon("gun", 10, 10, ap=5, reach=25, kind="ranged"), dist=15)
    assert risk.turn_threat(gun, me, rules) == 20


def test_next_turn_healing_includes_measured_bypassing_critical_tail(monkeypatch):
    row = crit_tables.Row(6, crit_tables.DAM_BYPASS, -1, 0, 0, 1, 1)
    rows = tuple(tuple((row,) * 6 for _ in range(9)) for _ in range(15))
    monkeypatch.setattr(crit_tables, "tables", lambda: crit_tables.Tables(rows, rows[0], 0))
    enemy = fighter(ap=6, crit_chance=100, weapon=risk.Weapon("claw", 8, 8, ap=3))
    player = fighter(dt=(20,) * 7, dr=(80,) * 7)
    assert risk.turn_threat(enemy, player, risk.Rules()) == 48
    assert risk.heal_below(player.max_hp, 48) == 30


def test_partially_loaded_burst_is_not_excluded_from_healing_danger():
    gun = risk.Weapon("burst", 10, 10, ap=5, reach=25, kind="ranged", capacity=12, burst=10)
    foe = fighter(weapon=gun, loaded=3, dist=10)
    assert risk.turn_threat(foe, fighter(), risk.Rules(game_time=0)) > 0


class Memory:
    def __init__(self, active, words=None):
        self.active = active
        self.words = words or {}

    def glob(self, name):
        return {
            "combat_state": 1,
            "combat_list": 2000,
            "list_com": len(self.active),
            "combat_difficulty": 1,
            "fallout_game_time": 0,
        }[name]

    def read(self, address, size):
        assert address == 2000 and size == len(self.active) * 4
        return struct.pack(f"<{len(self.active)}I", *self.active)

    def u32(self, address):
        return self.words.get(address, 0)

    i32 = u32


def critter(address, **changes):
    fields = {
        "tile": 20101,
        "elevation": 0,
        "rotation": 0,
        "frame": 0,
        "hp": 25,
        "ap": 7,
        "fid": 0,
        "flags": 0,
        "pid": 0x01000001,
        "team": 1,
        "results": 0,
    }
    return state.Critter(address=address, **(fields | changes))


def test_household_participants_and_engaging_joiners_are_not_invisible(monkeypatch):
    dude = critter(100, team=0)
    opponent, household, engaging, bystander = [critter(address, team=42) for address in (200, 300, 400, 500)]
    bystander = replace(bystander, team=77)
    a = object.__new__(actions.Actor)
    a.mem = Memory(
        (100, 200, 300), {100 + Obj.CRITTER_WHO_HIT_ME: 200, 400 + Obj.CRITTER_MANEUVER: action_combat.ENGAGING}
    )
    a.snap = lambda: SimpleNamespace(dude=dude)
    monkeypatch.setattr(state, "critters", lambda mem: [dude, opponent, household, engaging, bystander])
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == [opponent, household, engaging]
    assert a.forced_group() == ([opponent, household], [engaging])
    a.mem.active = ()
    a.mem.glob = lambda name: 0
    a.mem.words[100 + Obj.CRITTER_WHO_HIT_ME] = 0
    assert a.enemies() == []  # a team number alone never authorizes fighting a townsperson


def test_third_party_defenders_are_not_enemies_of_the_player(monkeypatch):
    """Measured scripted teams: the defender/guards use 12, the assassin 13; both appear in the turn list."""
    dude, defender, guard, assassin = (
        critter(100, team=0),
        critter(200, team=12),
        critter(300, team=12),
        critter(400, team=13),
    )
    a = object.__new__(actions.Actor)
    a.mem = Memory((200, 400, 100, 300), {200 + Obj.CRITTER_WHO_HIT_ME: 400, 400 + Obj.CRITTER_WHO_HIT_ME: 200})
    a.snap = lambda: SimpleNamespace(dude=dude)
    a._combat_targets = {400}  # deliberate assistance against the assassin
    monkeypatch.setattr(state, "critters", lambda mem: [dude, defender, guard, assassin])
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == [assassin]
    assert a.forced_group() == ([assassin], [])
    captured = []
    monkeypatch.setattr(risk, "player", lambda actor: fighter())
    monkeypatch.setattr(risk, "live_foe", lambda actor, c, here, joins=0: fighter(name=str(c.address), joins=joins))
    monkeypatch.setattr(risk, "fight", lambda me, foes, rules: captured.extend(foes))
    risk.against(a, [assassin])
    assert [f.name for f in captured] == ["400"]


def test_npc_combat_without_a_player_enemy_does_not_target_either_side(monkeypatch):
    dude, defender, assassin = critter(100, team=0), critter(200, team=12), critter(400, team=13)
    a = object.__new__(actions.Actor)
    a.mem = Memory((100, 200, 400), {200 + Obj.CRITTER_WHO_HIT_ME: 400, 400 + Obj.CRITTER_WHO_HIT_ME: 200})
    a.snap = lambda: SimpleNamespace(dude=dude)
    monkeypatch.setattr(state, "critters", lambda mem: [dude, defender, assassin])
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == []


def test_household_hostility_remains_after_the_initial_foe_dies(monkeypatch):
    dude, initial, household = critter(100, team=0), critter(200, team=42), critter(300, team=42)
    crew = [dude, initial, household]
    a = object.__new__(actions.Actor)
    a.mem = Memory((100, 200, 300), {100 + Obj.CRITTER_WHO_HIT_ME: 200})
    a.snap = lambda: SimpleNamespace(dude=dude)
    monkeypatch.setattr(state, "critters", lambda mem: crew)
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == [initial, household]
    crew[:] = [dude, household]  # the old attacker's object may no longer appear in the map scan
    a.mem.active = (100, 300)
    a.mem.words[100 + Obj.CRITTER_WHO_HIT_ME] = 0
    assert a.enemies() == [household]
    a.log = Log()
    resumed = []
    a._fight = lambda *args: resumed.extend(a.enemies()) or actions.Outcome(False, "too many rounds", {})
    a.fight()
    assert resumed == [household]  # discovery before fight() belongs to this same running combat


def test_a_hostile_faction_onlooker_is_modelled_before_it_gets_a_turn(monkeypatch):
    dude, attacker, observer = critter(100, team=0), critter(200, team=42), critter(300, team=42)
    a = object.__new__(actions.Actor)
    a.mem = Memory((100, 200), {100 + Obj.CRITTER_WHO_HIT_ME: 200})
    a.snap = lambda: SimpleNamespace(dude=dude)
    monkeypatch.setattr(state, "critters", lambda mem: [dude, attacker, observer])
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == [attacker, observer]
    assert a.forced_group() == ([attacker], [observer])


def test_a_raid_keeps_player_team_copies_and_other_factions_separate(monkeypatch):
    dude, friendly_copy, hostile, guard, bystander = (
        critter(100, team=0),
        critter(200, team=0),
        critter(300, team=13),
        critter(400, team=87),
        critter(500, team=12),
    )
    a = object.__new__(actions.Actor)
    a.mem = Memory((100, 200, 300, 400, 500), {200 + Obj.CRITTER_WHO_HIT_ME: 300, 400 + Obj.CRITTER_WHO_HIT_ME: 100})
    a.snap = lambda: SimpleNamespace(dude=dude)
    monkeypatch.setattr(state, "critters", lambda mem: [dude, friendly_copy, hostile, guard, bystander])
    monkeypatch.setattr(knowledge, "proto_name", lambda pid: "person")
    assert a.enemies() == [hostile, guard]


def test_named_target_cannot_hide_other_active_participants_from_risk(monkeypatch):
    dude = critter(100, team=0)
    before, target = critter(200), critter(300)
    a = SimpleNamespace(
        mem=Memory((200, 100, 300)),
        snap=lambda: SimpleNamespace(dude=dude),
        in_combat=lambda: True,
        combat_order=lambda: (200, 100, 300),
        enemies=lambda: [before, target],
    )
    monkeypatch.setattr(risk, "player", lambda actor: fighter(initial_ap=5))
    monkeypatch.setattr(risk, "live_foe", lambda actor, c, here, joins=0: fighter(name=str(c.address), joins=joins))
    captured = []
    monkeypatch.setattr(risk, "fight", lambda me, foes, rules: captured.append((me, foes, rules)))
    risk.against(a, [target])
    me, foes, rules = captured[0]
    assert me.initial_ap == 5 and [f.name for f in foes] == ["200", "300"]
    assert rules.first_foes == (1,)


def test_a_joiner_noticing_the_second_opponent_is_in_the_model(monkeypatch):
    dude = critter(100, team=0)
    first, second, joiner = critter(200, tile=10), critter(300, tile=20), critter(400, tile=30)
    actor = SimpleNamespace(mem=Memory(()), snap=lambda: SimpleNamespace(dude=dude), in_combat=lambda: False)
    monkeypatch.setattr(risk, "player", lambda actor: fighter())
    monkeypatch.setattr(risk, "live_foe", lambda actor, c, here, joins=0: fighter(name=str(c.address), joins=joins))
    monkeypatch.setattr(perception, "perception_of", lambda pid: 2)
    monkeypatch.setattr(perception, "notices", lambda tile, rotation, pe, here, **kw: here == second.tile)
    captured = []
    monkeypatch.setattr(risk, "fight", lambda me, foes, rules: captured.extend(foes))
    risk.against(actor, [first, second], [joiner])
    assert captured[-1].name == "400" and captured[-1].joins == 1


def test_live_foe_reads_equipped_gun_loaded_and_spare_ammunition(monkeypatch):
    from f1 import loot

    actor = SimpleNamespace(
        mem=Memory((), {1000 + Obj.FLAGS: odds.IN_HAND, 1000 + Obj.ITEM_AMMO: 3, 1100 + Obj.ITEM_AMMO: 4})
    )
    enemy = critter(200)
    monkeypatch.setattr(loot, "inventory", lambda mem, address: [(1000, 8, 1), (1100, 9, 2)])
    monkeypatch.setattr(weapons, "weapon", lambda pid: pid == 8)
    monkeypatch.setattr(weapons, "uses_ammo", lambda pid: True)
    monkeypatch.setattr(weapons, "fits", lambda held, pid: pid == 9)
    monkeypatch.setattr(risk, "knowledge_proto", lambda pid: {"ammo": {"quantity": 10}})
    monkeypatch.setattr(odds, "visible_light", lambda *args: odds.LIGHT_MAX)
    captured = []
    monkeypatch.setattr(risk, "foe", lambda pid, distance, **kwargs: captured.append(kwargs))
    risk.live_foe(actor, enemy, 20100)
    assert (captured[0]["held"], captured[0]["loaded"], captured[0]["spare"]) == (8, 3, 14)


def test_move_stops_before_activating_an_excluded_onlooker(monkeypatch):
    a = object.__new__(actions.Actor)
    a.log = Log()
    a._combat_onlookers = [SimpleNamespace(address=7, tile=30, rotation=0, pid=1)]
    monkeypatch.setattr(perception, "perception_of", lambda pid: 2)
    monkeypatch.setattr(perception, "notices", lambda tile, rotation, pe, here, **kw: here >= 3)
    assert a._safe_combat_prefix([1, 2, 3, 4]) == [1, 2]
    assert a.log.events[0][1]["foes"] == [7]


def test_participant_movement_or_equipment_invalidates_target_failure_state(monkeypatch):
    a = object.__new__(actions.Actor)
    a.mem = None
    a.weapon = lambda: actions.Held(8, 12, -1)
    a._count = lambda pid: 2
    s = SimpleNamespace(dude=SimpleNamespace(tile=20100, hp=40, ap=10))
    foe = critter(200)
    original = a._combat_signature(s, [foe], [])
    assert original != a._combat_signature(s, [replace(foe, tile=20102)], [])
    a.weapon = lambda: actions.Held(9, 3, -1)
    assert original != a._combat_signature(s, [foe], [])


def test_encounter_identity_survives_bounded_pause_until_terminal_outcome():
    a = object.__new__(actions.Actor)
    a.log = Log()
    a.in_combat = lambda: True
    outcomes = [actions.Outcome(False, "too many rounds", {}), actions.Outcome(True, "combat over", {})]
    a._fight = lambda *args: outcomes.pop(0)
    first, final = a.fight(), a.fight()
    assert first.detail["encounter_id"] == final.detail["encounter_id"]
    assert [k for k, _ in a.log.events] == ["encounter_start", "encounter_pause", "encounter_end"]


class Actor(actions.Actor):
    max_hp, max_ap, flare_prep = 40, 10, None


def combat_actor(monkeypatch):
    a = object.__new__(Actor)
    a.log, a.pid, a.mem, a.forced_prep = Log(), 1, None, None
    a.dude = SimpleNamespace(tile=20100, hp=40, ap=10)
    a.snap = lambda: SimpleNamespace(dude=a.dude, screen="map", camera=None)
    a.combat = True
    a.in_combat = lambda: a.combat
    a.my_turn = lambda: True
    a._count = lambda pid: 0
    a.weapon = lambda: actions.Held(8, 12, -1)
    a.rearm = lambda s, held, drew: (False, held, drew)
    a.attack_cost, a.attack_reach = lambda held: 5, lambda held: 25
    monkeypatch.setattr(clock, "fast", lambda pid: nullcontext())
    monkeypatch.setattr(win32, "wait_for", lambda cond, *args, **kw: cond())
    monkeypatch.setattr(geometry, "on_view", lambda *args: True)
    monkeypatch.setattr(risk, "danger", lambda *args: 0)
    monkeypatch.setattr(odds, "shot_chance", lambda *args: 95)
    monkeypatch.setattr(odds, "armed_at_range", lambda *args: False)
    return a


def test_joining_household_is_reassessed_before_next_attack_and_has_one_encounter(monkeypatch):
    a = combat_actor(monkeypatch)
    initial, household = critter(200), critter(300)
    group, assessed, shots = [initial], [], []
    a.enemies = lambda only=None: group
    a.forced_group = lambda: (group, [])

    def assess(actor, foes, others):
        assessed.append(tuple(c.address for c in foes))
        return odds.Odds(1, 40, 1, 1, 95, 60, accept=True)

    def attack(target):
        shots.append(target.address)
        if target is initial:
            group[:] = [household]
        else:
            a.combat = False
        return actions.Outcome(True, "attacked", {})

    monkeypatch.setattr(odds, "assess", assess)
    a.attack = attack
    result = a.fight()
    assert result.ok and shots == [200, 300] and assessed == [(200,), (300,)]
    ids = {detail["encounter_id"] for event, detail in a.log.events if event.startswith("encounter_")}
    assert ids == {result.detail["encounter_id"]}
    assert sum(event == "encounter_start" for event, _ in a.log.events) == 1
    assert sum(event == "encounter_end" for event, _ in a.log.events) == 1


def test_risk_refused_after_joining_foe_flees_before_attacking_it(monkeypatch):
    from f1 import quests

    a = combat_actor(monkeypatch)
    initial, household = critter(200), critter(300)
    group, shots = [initial], []
    a.enemies = lambda only=None: group
    a.forced_group = lambda: (group, [])
    monkeypatch.setattr(
        odds,
        "assess",
        lambda actor, foes, others: odds.Odds(
            1, 40, 1, 1, 95, 60, death_high=0.5 if household in foes else 0, accept=household not in foes
        ),
    )
    monkeypatch.setattr(quests, "flee", lambda actor: True)
    a.attack = lambda target: (
        shots.append(target.address) or group.append(household) or actions.Outcome(True, "hit", {})
    )
    result = a.fight()
    assert result.reason == "refused; fled" and shots == [200]
    assert [d["accept"] for k, d in a.log.events if k == "encounter_risk"] == [True, False]


def test_a_fight_the_route_began_is_fought_through_a_refused_first_estimate(monkeypatch):
    """Kenji and the raid on Gizmo: the model weighs no allies and refused them from the first estimate; the route
    chose them from measured games, so they are fought (a forced fight is still left: the test above)."""
    from f1 import quests

    a = combat_actor(monkeypatch)
    kenji = critter(200)
    group, shots, fled = [kenji], [], []
    a.enemies = lambda only=None: group
    a.forced_group = lambda: (group, [])
    monkeypatch.setattr(
        odds, "assess", lambda actor, foes, others: odds.Odds(0, 40, 1, 1, 95, 60, death_high=0.53, accept=False)
    )
    monkeypatch.setattr(quests, "flee", lambda actor: fled.append(1) or True)

    def attack(target):
        shots.append(target.address)
        a.combat = False
        return actions.Outcome(True, "hit", {})

    a.attack = attack
    result = a.fight(only=("Kenji",))
    assert result.ok and shots == [200] and fled == []
    assert [d["accept"] for k, d in a.log.events if k == "encounter_risk"] == [False]


def test_a_finished_fight_keeps_no_onlookers_for_later_combat_walks(monkeypatch):
    """Onlookers limit a combat walk to one hex at a time; kept after their fight, they split the walk past
    Hightower's hex in two and the second step stopped on it (his talk showed the player out)."""
    a = combat_actor(monkeypatch)
    foe, bystander = critter(200), critter(300)
    a.enemies = lambda only=None: [foe]
    a.forced_group = lambda: ([foe], [bystander])
    monkeypatch.setattr(odds, "assess", lambda actor, foes, others: odds.Odds(1, 40, 1, 1, 95, 60, accept=True))

    def attack(target):
        a.combat = False
        return actions.Outcome(True, "hit", {})

    a.attack = attack
    assert a.fight().reason == "combat over"
    assert not getattr(a, "_combat_onlookers", ())


def test_death_during_foe_turn_is_a_terminal_death_without_a_new_risk_assessment(monkeypatch):
    a = combat_actor(monkeypatch)

    def wait_for(cond, *args, **kwargs):
        a.dude.hp = 0
        return False

    monkeypatch.setattr(win32, "wait_for", wait_for)
    a.forced_group = lambda: (_ for _ in ()).throw(AssertionError("assessing a dead character"))
    result = a.fight()
    assert result.reason == "died"
    assert a.log.events[-1][0] == "encounter_end" and a.log.events[-1][1]["outcome"] == "died"
