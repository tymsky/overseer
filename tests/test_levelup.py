"""Skill values and level-up plans, checked against what the game showed for the Agent."""

from conftest import needs_knowledge

from f1.chargen import SKILL_NAMES, TRAITS, compute_skills, plan_points

AGENT_STATS = [6, 9, 5, 3, 9, 10, 6]  # ST PE EN CH IN AG LK after Gifted
TAGS = {SKILL_NAMES.index(s) for s in ("small guns", "speech", "lockpick")}
TRAIT_IDS = {TRAITS.index("gifted"), TRAITS.index("small frame")}


def test_skill_values_match_the_character_screen() -> None:
    fresh = compute_skills(AGENT_STATS, [0] * 18, TAGS, TRAIT_IDS)
    assert (fresh["small guns"], fresh["speech"], fresh["lockpick"]) == (55, 41, 39)  # measured at the start
    points = [0] * 18
    points[SKILL_NAMES.index("small guns")] = 18
    points[SKILL_NAMES.index("lockpick")] = 18
    later = compute_skills(AGENT_STATS, points, TAGS, TRAIT_IDS)
    assert (later["small guns"], later["lockpick"]) == (91, 75)  # level 2's 18 points; level 6's 18 points


def test_level_up_points_go_to_the_targets_in_order() -> None:
    targets = (("small guns", 100), ("speech", 140), ("lockpick", 100))
    values = {name: 0 for name in SKILL_NAMES} | {"small guns": 55, "speech": 41, "lockpick": 39}
    assert plan_points(values, TAGS, 18, targets) == {"small guns": 18}
    values |= {"small guns": 91}
    assert plan_points(values, TAGS, 18, targets) == {"small guns": 5, "speech": 13}  # 91 + 10 = 101 >= 100
    done = values | {"small guns": 101, "speech": 140, "lockpick": 100}
    assert plan_points(done, TAGS, 18, targets) == {}


def test_traits_add_to_the_stats_the_proto_holds() -> None:
    """pc_proto holds the Agent's stats before traits (ST 5 PE 8 EN 4 CH 2 IN 8 AG 8 LK 5); live, a skill read
    without the trait bonus came out 2 low (Small Guns 53 for the screen's 55)."""
    from f1.chargen import trait_stat_bonus

    proto_stats = [5, 8, 4, 2, 8, 8, 5]
    stats = [s + b for s, b in zip(proto_stats, trait_stat_bonus(TRAIT_IDS), strict=True)]
    assert stats == AGENT_STATS


@needs_knowledge
def test_barter_price_follows_barter_compute_value() -> None:
    """Killian (CH 8, 19 points: Barter 55) to the Agent (Barter 16): a 100-cap stimpak at 100 x 100 / 61."""
    from f1.barter import npc_skill, price

    assert npc_skill(0x0100004F, "barter") == 55
    assert price(100, 16, 55) == 164
    assert price(100, 16, 250) == 1000  # mod clamped to 10


def test_the_idealist_starts_as_the_formulas_say() -> None:
    """The Idealist (ST 6 PE 6 EN 6 CH 3 IN 10 AG 8 LK 1, Fast Shot; tags Outdoorsman, Speech, Energy Weapons) by
    skill.c's formulas: tagged Outdoorsman 5 + (6 + 10) / 2 + 20, Speech 25 + 2 x 3 + 20, Energy Weapons 10 + 8 + 20;
    Science 25 + 2 x 10 passes Curtis's 40 from the start. Easy adds 20 to the non-combat skills only."""
    from f1.chargen import IDEALIST

    stats = list(IDEALIST.special)
    tags = {SKILL_NAMES.index(s) for s in IDEALIST.tags}
    traits = {TRAITS.index(t) for t in IDEALIST.traits}
    fresh = compute_skills(stats, [0] * 18, tags, traits)
    assert (fresh["outdoorsman"], fresh["speech"], fresh["energy weapons"]) == (33, 51, 38)
    assert (fresh["small guns"], fresh["science"], fresh["repair"], fresh["lockpick"]) == (43, 45, 30, 27)
    easy = compute_skills(stats, [0] * 18, tags, traits, difficulty=0)
    assert (easy["outdoorsman"], easy["science"], easy["small guns"]) == (53, 65, 43)
    assert compute_skills(stats, [0] * 18, tags, traits, difficulty=2)["speech"] == 41


def test_the_idealist_plan_puts_the_first_points_into_guns_then_travel() -> None:
    from f1.chargen import IDEALIST, IDEALIST_SKILLS

    tags = {SKILL_NAMES.index(s) for s in IDEALIST.tags}
    values = {name: 0 for name in SKILL_NAMES} | {"small guns": 43, "outdoorsman": 55, "speech": 51}
    assert plan_points(values, tags, 25, IDEALIST_SKILLS) == {"small guns": 12, "outdoorsman": 13}
    values |= {"small guns": 55, "outdoorsman": 81}
    # Speech toward 110 before anything else (51 + 2 x 15 = 81 here; the rest of the way at the next level)
    assert plan_points(values, tags, 25, IDEALIST_SKILLS) == {"outdoorsman": 10, "speech": 15}
