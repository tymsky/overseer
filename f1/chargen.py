"""Character creation: enter a build in the game's own editor, every click confirmed in memory.

    python -m f1.chargen [agent|idealist]   create a build (BUILDS; the Agent by default) and start the game with it

The editor's controls come from its window's button list (f1/ui.py): stat plus 503+i and minus 510+i, traits 555+t,
tag skills 536+s, name 517, done 500. Its working state is read after each click: base stats in pc_proto,
character_points, trait_count/temp_trait, tagskill_count/temp_tag_skill.
"""

import struct
import sys
import time
from dataclasses import dataclass

from f1 import engine_map as em
from f1 import flows, frame, session, state, ui, win32
from f1.memory import GameMemory

STATS = ("ST", "PE", "EN", "CH", "IN", "AG", "LK")
TRAITS = (
    "fast metabolism", "bruiser", "small frame", "one hander", "finesse", "kamikaze", "heavy handed", "fast shot",
    "bloody mess", "jinxed", "good natured", "chem reliant", "chem resistant", "night person", "skilled", "gifted",
)  # fmt: skip
SKILLS = (
    "small guns", "big guns", "energy weapons", "unarmed", "melee weapons", "throwing", "first aid", "doctor",
    "sneak", "lockpick", "steal", "traps", "science", "repair", "speech", "barter", "gambling", "outdoorsman",
)  # fmt: skip
BASE_STATS = 0x24  # offset of baseStats[] in CritterProto
STAT_PLUS, STAT_MINUS, TRAIT_BTN, TAG_BTN, NAME_BTN, DONE_BTN = 503, 510, 555, 536, 517, 500


@dataclass(frozen=True)
class Build:
    name: str
    special: tuple[int, int, int, int, int, int, int]  # as set in the editor, before traits
    traits: tuple[str, ...]
    tags: tuple[str, str, str]
    # Exploits are an option of the character, off by default. Off, the routes skip
    # every step marked `exploit` (routes.Step): tricks that take more than the game's rules give.
    exploits: bool = False


# The first character: Gifted + Small Frame, tag Small Guns, Speech, Lockpick.
# After traits: ST 6, PE 9, EN 5, CH 3, IN 9, AG 10, LK 6.
AGENT = Build("Agent", (5, 8, 4, 2, 8, 8, 5), ("gifted", "small frame"), ("small guns", "speech", "lockpick"))
# The best-outcome run's character (a new one): Outdoorsman tagged and
# EN 6 for Pathfinder, because world-map travel sets the clock; IN 10 for 25 skill points a level; ST 6 for the plasma
# and laser rifles and Power Armor's 85 lbs; AG 8 gives the same 9 AP as 9; Fast Shot takes 1 AP off every shot.
IDEALIST = Build("Idealist", (6, 6, 6, 3, 10, 8, 1), ("fast shot",), ("outdoorsman", "speech", "energy weapons"))
BUILDS = {"agent": AGENT, "idealist": IDEALIST}


def build_of(name: str) -> Build | None:
    """The build of a character by the name the game shows (the Agent, the Idealist), if it is one of ours."""
    return next((b for b in BUILDS.values() if b.name == name), None)


class ChargenError(RuntimeError):
    pass


def ints(mem: GameMemory, name: str, count: int, offset: int = 0) -> list[int]:
    return list(struct.unpack(f"<{count}i", mem.read(em.GLOBALS[name].address + offset, 4 * count)))


def editor_state(mem: GameMemory) -> dict:
    return {
        "base": ints(mem, "pc_proto", 7, BASE_STATS),
        "points": mem.glob("character_points"),
        "traits": [t for t in ints(mem, "temp_trait", 2) if t != -1],  # a third slot holds 0, not -1
        "trait_slots": mem.glob("trait_count"),
        "tags": [s for s in ints(mem, "temp_tag_skill", 4) if s != -1],
        "tag_slots": mem.glob("tagskill_count"),
        "name": state.character_name(mem),
    }


def _press_button(mem: GameMemory, code: int, check, what: str, timeout_s: float = 1.5) -> None:
    window = mem.glob("edit_win")
    button = ui.find(mem, window, code)
    if button is None:
        raise ChargenError(f"no button with code {code} for {what}")
    for _ in range(3):  # a click that did not register gets another try
        session.click(*button.center)
        if win32.wait_for(lambda: check(editor_state(mem)), timeout_s, 0.05):
            return
    raise ChargenError(f"{what}: the editor did not change as expected ({editor_state(mem)})")


def create(pid: int, build: Build) -> dict:
    flows.to_main_menu(pid)
    with GameMemory(pid) as mem:
        session.press("n")
        flows.wait_screen(mem, {"select_character"}, 15)
        time.sleep(1.0)
        session.press("c")
        flows.wait_screen(mem, {"character"}, 15)
        time.sleep(1.0)
        if not mem.flag("glblmode"):
            raise ChargenError("the editor is not in creation mode")

        for trait in build.traits:
            t = TRAITS.index(trait)
            _press_button(mem, TRAIT_BTN + t, lambda s, t=t: t in s["traits"], f"trait {trait}")

        target = list(build.special)
        order = sorted(range(7), key=lambda i: target[i] - editor_state(mem)["base"][i])  # lowerings first
        for i in order:
            while (cur := editor_state(mem)["base"][i]) != target[i]:
                code = STAT_PLUS + i if cur < target[i] else STAT_MINUS + i
                want = cur + (1 if cur < target[i] else -1)
                _press_button(mem, code, lambda s, i=i, want=want: s["base"][i] == want, f"{STATS[i]} to {want}")

        for tag in build.tags:
            s_id = SKILLS.index(tag)
            _press_button(mem, TAG_BTN + s_id, lambda s, s_id=s_id: s_id in s["tags"], f"tag {tag}")

        session.press("n")  # the name box
        time.sleep(0.8)
        for _ in range(12):
            session.press("backspace")
        session.type_text(build.name)
        session.press("enter")
        time.sleep(0.8)

        state = editor_state(mem)
        problems = []
        if state["base"] != target:
            problems.append(f"base stats {state['base']} != {target}")
        if sorted(state["traits"]) != sorted(TRAITS.index(t) for t in build.traits):
            problems.append(f"traits {state['traits']}")
        if sorted(state["tags"]) != sorted(SKILLS.index(s) for s in build.tags):
            problems.append(f"tags {state['tags']}")
        if state["points"] != 0 or state["tag_slots"] != 0:
            problems.append(f"points {state['points']} / tag slots {state['tag_slots']} left")
        if state["name"] != build.name:
            problems.append(f"name {state['name']!r}")
        frame.compose(mem).save(session.CAPTURES / "chargen-editor.png")
        if problems:
            raise ChargenError("; ".join(problems))

        # DONE by its button: the key "d" right after the name box did not register.
        done = ui.find(mem, mem.glob("edit_win"), DONE_BTN)
        if done is None:
            raise ChargenError("no DONE button")
        session.click(*done.center)
        flows.wait_screen(mem, {"map"}, 90, poke=True)  # the Overseer's movie, then the first map
        time.sleep(2)
        final = {
            "base": ints(mem, "pc_proto", 7, BASE_STATS),
            "traits": ints(mem, "pc_trait", 2),
            "tags": ints(mem, "tag_skill", 4),
            "name": editor_state(mem)["name"],
        }
    return {"editor": state, "game": final}


SKILL_NAMES = (
    "small guns", "big guns", "energy weapons", "unarmed", "melee weapons", "throwing", "first aid", "doctor",
    "sneak", "lockpick", "steal", "traps", "science", "repair", "speech", "barter", "gambling", "outdoorsman",
)  # fmt: skip


# skill.c skill_data: default value, stat modifier, the stats it follows (two are averaged); stats 0 ST ... 6 LK
SKILL_FORMULA = (
    (35, 1, (5,)), (10, 1, (5,)), (10, 1, (5,)), (65, 1, (5, 0)), (55, 1, (5, 0)), (40, 1, (5,)),
    (30, 1, (1, 4)), (15, 1, (1, 4)), (25, 1, (5,)), (20, 1, (1, 5)), (20, 1, (5,)), (20, 1, (1, 5)),
    (25, 2, (4,)), (20, 1, (4,)), (25, 2, (3,)), (20, 2, (3,)), (20, 3, (6,)), (5, 1, (2, 4)),
)  # fmt: skip
PROTO_SKILLS = BASE_STATS + 2 * 35 * 4  # skills[18] follow baseStats[35] and bonusStats[35]
SKILL_MAX = 200
GOOD_NATURED = {0: -10, 1: -10, 2: -10, 3: -10, 4: -10, 5: -10, 6: 15, 7: 15, 14: 15, 15: 15}
PERK_SIZE = (573, 230)  # editor.c perks_dialog: at (33, 91) at 640x480, (673, 341) at 1920x1080
# skill.c skill_game_difficulty: the game difficulty (0 Easy, 1 Normal, 2 Hard) moves First Aid to Outdoorsman,
# the non-combat skills, by +20 or -10.
DIFFICULTY_SKILLS = frozenset(range(6, 18))
DIFFICULTY_BONUS = {0: 20, 1: 0, 2: -10}
# The Agent's level-ups: skills raised to these values in this order, perks taken by this preference.
AGENT_SKILLS = (("small guns", 100), ("speech", 140), ("lockpick", 100), ("first aid", 70), ("small guns", 150))
AGENT_PERKS = ("Bonus Rate of Fire", "Bonus Ranged Damage", "Earlier Sequence", "Toughness", "Awareness")
# The Idealist's (a first plan for step 4; values on Normal). Small Guns first for the caves (43 at the start),
# Outdoorsman to 100 (worldmap.c CalcTimeAdder: 60 + 0.6 x skill map steps a day, capped at 100), Speech for the talks,
# Lockpick for the ghoul prisoner and the Thieves' Circle, Energy Weapons for the plasma and the deathclaws. Science
# and Repair come later because books add (100 - skill) / 10 points (protinst.c obj_use_book): the Necropolis leader's
# three Dean's Electronics (given while Repair < 60) and the Glow's Big Books go in first. Perks: Swift Learner at 3,
# Pathfinder at 6 and 9 (-25 % world-map time a rank), then Bonus Rate of Fire.
# Speech to 110 right after Outdoorsman, so that the one-roll Speech checks (Garl +10, the hostage-taker +20,
# Sherry and the Master at 0) cannot fail when they come: 99 at level 3, 111 at level 4 (plan_points, Easy). Energy
# Weapons waits a level; no energy weapon comes before the Boneyard anyway.
IDEALIST_SKILLS = (
    ("small guns", 55), ("outdoorsman", 100), ("speech", 110), ("lockpick", 45), ("energy weapons", 90),
    ("speech", 120),  # "But I am a ghoul!" (-20) sure at 121, from the next level-up (Harry on the way out)
    ("science", 75), ("repair", 75), ("energy weapons", 130), ("science", 110), ("repair", 105),
    ("lockpick", 90), ("traps", 60), ("first aid", 70),
)  # fmt: skip
IDEALIST_PERKS = ("Pathfinder", "Swift Learner", "Bonus Rate of Fire", "Faster Healing", "Educated", "Action Boy")
PLANS = {"Agent": (AGENT_SKILLS, AGENT_PERKS), "Idealist": (IDEALIST_SKILLS, IDEALIST_PERKS)}


def compute_skills(
    stat: list[int], points: list[int], tags: set[int], traits: set[int], difficulty: int = 1
) -> dict[str, int]:
    """Skills in percent, as skill.c's skill_level computes them for the player: default + stat bonus + points
    (twice for a tagged skill, which also gets +20) + traits (Gifted -10, Skilled +10, Good Natured) + the game
    difficulty's bonus to the non-combat skills. Left out: perks that change skills (Mr. Fixit and the like)."""
    out = {}
    for i, (default, modifier, stats) in enumerate(SKILL_FORMULA):
        value = default + sum(stat[k] for k in stats) * modifier // len(stats) + points[i]
        if i in tags:
            value += 20 + points[i]
        value += -10 * (TRAITS.index("gifted") in traits) + 10 * (TRAITS.index("skilled") in traits)
        value += GOOD_NATURED.get(i, 0) if TRAITS.index("good natured") in traits else 0
        value += DIFFICULTY_BONUS.get(difficulty, 0) if i in DIFFICULTY_SKILLS else 0
        out[SKILL_NAMES[i]] = min(value, SKILL_MAX)
    return out


def trait_stat_bonus(traits: set[int]) -> list[int]:
    """trait.c trait_adjust_stat for SPECIAL (the game adds it at run time; pc_proto holds the stats without it):
    Gifted +1 to each, Bruiser +2 ST, Small Frame +1 AG. Night Person (PE, IN by the hour) is left out."""
    bonus = [1 if TRAITS.index("gifted") in traits else 0] * 7
    bonus[0] += 2 if TRAITS.index("bruiser") in traits else 0
    bonus[5] += 1 if TRAITS.index("small frame") in traits else 0
    return bonus


def special(mem: GameMemory) -> list[int]:
    """The player's SPECIAL as the game counts it: base + bonus in pc_proto plus the traits' run-time bonus, 1..10."""
    base, bonus = ints(mem, "pc_proto", 7, BASE_STATS), ints(mem, "pc_proto", 7, BASE_STATS + 35 * 4)
    traits = set(ints(mem, "pc_trait", 2))
    return [max(1, min(10, b + x + t)) for b, x, t in zip(base, bonus, trait_stat_bonus(traits), strict=True)]


def skill_values(mem: GameMemory) -> dict[str, int]:
    """The player's skills in percent, from pc_proto (stats base + bonus, skill points), the tags, the traits and the
    game difficulty."""
    proto = em.GLOBALS["pc_proto"].address
    points = list(struct.unpack("<18i", mem.read(proto + PROTO_SKILLS, 18 * 4)))
    traits = set(ints(mem, "pc_trait", 2))
    tags = set(ints(mem, "tag_skill", 4))
    return compute_skills(special(mem), points, tags, traits, mem.glob("game_difficulty"))


def perk_window_open(mem: GameMemory) -> bool:
    return any(w[4:] == PERK_SIZE for w in state.live_windows(mem))


def c_string(mem: GameMemory, address: int, most: int = 48) -> str:
    """A NUL-ended string, read in small pieces once a whole read fails: a perk name near the end of a mapped page
    failed the 48-byte read (error 299) and left the perk window open at level 12."""
    from f1.memory import ReadError

    try:
        raw = mem.read(address, most)
    except ReadError:
        raw = b""
        while len(raw) < most and b"\0" not in raw:
            raw += mem.read(address + len(raw), 4)
    return raw.split(b"\0", 1)[0].decode("latin1")


def pick_perk(mem: GameMemory, wanted: tuple[str, ...]) -> str | None:
    """The perk window (the character screen opens it first while free_perk is set): its list is name_sort_list
    ({perk, name}, sorted by name), the pick is crow + cline, moved one line per arrow key; Enter takes it, Esc
    leaves the perk for later. The first of `wanted` on the list is taken; None when none is (Esc)."""
    if not win32.wait_for(lambda: perk_window_open(mem), 3, 0.1):
        return None

    def pick() -> int:
        return mem.glob("perk_crow") + mem.glob("perk_cline")

    for _ in range(40):  # to the end: the last line tells the count
        session.press("down")
    count = pick() + 1
    names = []
    for i in range(count):
        ptr = mem.u32(em.GLOBALS["name_sort_list"].address + 8 * i + 4)
        names.append(c_string(mem, ptr))
    lowered = [n.lower() for n in names]
    index = next((lowered.index(w.lower()) for w in wanted if w.lower() in lowered), None)
    if index is not None:
        for _ in range(40):
            session.press("up")
        for _ in range(index):
            session.press("down")
    if index is None or pick() != index:
        session.press("esc")
        win32.wait_for(lambda: not perk_window_open(mem), 3, 0.1)
        return None
    session.press("enter")
    closed = win32.wait_for(lambda: not perk_window_open(mem), 3, 0.1)
    return names[index] if closed and mem.read(em.GLOBALS["free_perk"].address, 1) == b"\0" else None


def _open_character_screen(mem: GameMemory) -> bool:
    """C from the map; open once the screen (or the perk window over it) is up. `edit_win` alone is no proof: its
    id stays set after the screen closes (a level-up right after a talk found no DONE button)."""
    up = lambda: state.detect_screen(mem) == "character" or perk_window_open(mem)
    if up():  # left open by a level-up cut short (the perk window's failed read)
        return True
    for _ in range(2):
        if not win32.wait_for(lambda: state.detect_screen(mem) == "map", 5, 0.1):
            return False
        session.press("c")
        if win32.wait_for(up, 4, 0.1):
            time.sleep(0.8)
            return True
    return False


def _spend(mem: GameMemory, plan: dict[str, int]) -> dict[str, int]:
    """In the character screen: points into each skill of `plan` ({skill: points}). A skill is selected by a click on
    its row (editor.c: skill_cursor = (y - 27) * 0.0923 in the window, which HRP centres at bigger resolutions), each
    Right key adds a point (SliderBtn), checked by the unspent count (curr_pc_stat[0], read inside the screen: it read
    0 on the map after a level-up)."""
    unspent_at = em.GLOBALS["curr_pc_stat"].address
    spent: dict[str, int] = {}
    x0, y0 = ui.origin(mem, mem.glob("edit_win")) or (0, 0)
    for skill, points in plan.items():
        index = SKILL_NAMES.index(skill)
        for _ in range(3):  # a click right after a run of Right keys once did not take: check skill_cursor
            session.click(x0 + 420, y0 + 27 + int((index + 0.5) / 0.0923))  # the list: x 380..600
            if win32.wait_for(lambda index=index: mem.glob("skill_cursor") == index, 1.5, 0.05):
                break
        if mem.glob("skill_cursor") != index:
            spent[skill] = spent.get(skill, 0)
            continue
        time.sleep(0.3)
        got = 0
        for _ in range(min(points, mem.i32(unspent_at))):
            left = mem.i32(unspent_at)
            session.press("right")
            if not win32.wait_for(lambda left=left: mem.i32(unspent_at) < left, 2, 0.05):
                break
            got += 1
        spent[skill] = spent.get(skill, 0) + got
    return spent


def plan_points(values: dict[str, int], tags: set[int], unspent: int, targets) -> dict[str, int]:
    """Points per skill that bring each skill of `targets` ((skill, value), in order) up to its value, while they
    last; a tagged skill gains 2 % a point."""
    plan: dict[str, int] = {}
    values = dict(values)
    for skill, target in targets:
        per_point = 2 if SKILL_NAMES.index(skill) in tags else 1
        need = max(0, -(-(target - values[skill]) // per_point))
        take = min(need, unspent)
        if take:
            plan[skill] = plan.get(skill, 0) + take
            values[skill] += take * per_point
            unspent -= take
    return plan


def level_up(pid: int, targets=None, perks: tuple[str, ...] | None = None) -> dict:
    """Spend a level-up: the character screen, a perk by preference when one is due, the skill points toward
    `targets`; then DONE. Points no target needs stay unspent. Without targets and perks, the plan of the character
    in the game (PLANS by its name; the Agent's for any other)."""
    unspent_at = em.GLOBALS["curr_pc_stat"].address
    with GameMemory(pid) as mem:
        plan_skills, plan_perks = PLANS.get(state.character_name(mem), PLANS["Agent"])
        targets = plan_skills if targets is None else targets
        perks = plan_perks if perks is None else perks
        if not _open_character_screen(mem):
            return {"ok": False, "why": "no character screen"}
        perk = None
        if mem.read(em.GLOBALS["free_perk"].address, 1) != b"\0" or perk_window_open(mem):
            perk = pick_perk(mem, perks)
        before, values = mem.i32(unspent_at), skill_values(mem)
        plan = plan_points(values, set(ints(mem, "tag_skill", 4)), before, targets)
        spent = _spend(mem, plan)
        after, now = mem.i32(unspent_at), skill_values(mem)
        _press_button(mem, DONE_BTN, lambda _editor: state.detect_screen(mem) == "map", "done")
        changed = {k: (values[k], now[k]) for k in now if now[k] != values[k]}
        ok = sum(spent.values()) == sum(plan.values())
        return {"ok": ok, "perk": perk, "spent": spent, "left": after, "before": before, "skills": changed}


def spend_skill_points(pid: int, plan: dict[str, int] | str = "small guns") -> dict:
    """Level-up by points: open the character screen and spend the unspent skill points by `plan` ({skill: points},
    the last skill takes what is left; a plain name takes all); then DONE. A due perk is left for later (Esc)."""
    unspent_at = em.GLOBALS["curr_pc_stat"].address
    plan = {plan: 10**6} if isinstance(plan, str) else dict(plan)
    plan[list(plan)[-1]] = 10**6  # the last skill takes what is left
    with GameMemory(pid) as mem:
        if not _open_character_screen(mem):
            return {"ok": False, "why": "no character screen"}
        if perk_window_open(mem):
            session.press("esc")
            win32.wait_for(lambda: not perk_window_open(mem), 3, 0.1)
        before = mem.i32(unspent_at)
        spent = _spend(mem, plan)
        after = mem.i32(unspent_at)
        _press_button(mem, DONE_BTN, lambda _editor: state.detect_screen(mem) == "map", "done")
        return {"ok": after == 0, "before": before, "spent": spent, "left": after}


def main(argv: list[str]) -> int:
    if len(argv) > 1 or (argv and argv[0].lower() not in BUILDS):
        print(__doc__)
        return 2
    build = BUILDS[argv[0].lower()] if argv else AGENT
    session.CAPTURES.mkdir(parents=True, exist_ok=True)
    pid = session.game_pid() or session.start()["pid"]
    result = create(pid, build)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
