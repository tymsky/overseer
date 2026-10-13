"""The Idealist's Hardened Power Armor (planned from the scripts): Mrs. Stapleton's Chemistry Journals in the Hub,
then Miles in Adytum hardens the Power Armor. Between idealist_watershed (out of Necropolis, next to the Hub) and
idealist_cathedral (the Boneyard): the armour for the Cathedral's road and the ending.

    python -m f1.routes idealist_hardened [--from N] [--until N] [--clock K]

The chain (static analysis; QUESTS.md, the Boneyard page, "Power Armor"):
- idealist_boneyard's "Miles: the armour offer" (MILES.MSG 139, with FIX_FARM 9304 and Power Armor carried) sets
  CHEMISTRY_BOOK 1 and Miles's LVAR 5 to 9305;
- STAPLE.INT Staple02 offers 130 only while CHEMISTRY_BOOK == 1; Staple15 -> 114 -> Staple15a: with 750 caps
  (item_caps_total), -750, CHEMISTRY_BOOK 2 and the Chemistry Journals (pid 237); without them nothing changes;
- MILES.INT talk_p_proc at LVAR 5 9305 with pid 3 carried: Miles24 -> 143 (only with pid 237 carried) -> Miles25
  (the journals taken, LVAR 5 9306) -> 146 -> Miles28: the whole inventory out and back (the armour comes off), 24
  game hours (game_time_advance), pid 3 destroyed and pid 232 given; LVAR 5 2. At 9306 a later talk goes straight to
  Miles28 (147 was "no time").
The Hub's maps are invaded from day 140 (HUB_INVADED) and LAADYTUM never is; no other limit here.
"""

from f1 import quests
from f1.actions import Actor
from f1.boneyard import talk, to_map
from f1.idealist_hub import hub_map
from f1.idealist_support import PID_HARDENED_POWER_ARMOR, PID_POWER_ARMOR, day, rearm
from f1.route_support import Step, in_town, on_map, settle, travel

PID_CAPS, PID_JOURNALS = 0x29, 237
JOURNALS_COST = 750  # STAPLE.INT Staple15a
STAPLETON_JOURNALS = ["#130", "#114"]  # STAPLE.MSG 130, 114: CHEMISTRY_BOOK 2, pid 237
MILES_HARDEN = ["#143", "#146"]  # MILES.MSG 143, 146: 24 hours, pid 3 -> pid 232


def hardened(actor: Actor) -> bool:
    return actor._count(PID_HARDENED_POWER_ARMOR) > 0


def journals_carried(actor: Actor) -> bool:
    return hardened(actor) or actor._count(PID_JOURNALS) > 0


def to_the_hub(actor: Actor) -> bool:
    if journals_carried(actor) or on_map(actor, "HUB"):
        return True
    if actor.snap().screen == "map":
        quests.leave_map(actor)
    return in_town(actor, travel(actor, "the hub", 0))


def stapleton_journals(actor: Actor) -> bool:
    """The journals for 750 caps; refused (logged) without the offer (CHEMISTRY_BOOK 1) or the caps."""
    if journals_carried(actor):
        return True
    book, caps = quests.gvar(actor, "CHEMISTRY_BOOK"), actor._count(PID_CAPS)
    if book == 1 and caps >= JOURNALS_COST:
        actor.holster(True)
        quests.talk(actor, "staple", STAPLETON_JOURNALS, strict=True)
        settle(actor, STAPLETON_JOURNALS)
    actor.log.emit(
        "stapleton_journals", chemistry_book=quests.gvar(actor, "CHEMISTRY_BOOK"), caps_before=caps,
        caps=actor._count(PID_CAPS), journals=actor._count(PID_JOURNALS),
    )  # fmt: skip
    return journals_carried(actor)


def to_adytum(actor: Actor) -> bool:
    if hardened(actor) or on_map(actor, "LAADYTUM"):
        return True
    if not actor.snap().map_name.startswith("LA"):
        if actor.snap().screen == "map":
            quests.leave_map(actor)
        if not in_town(actor, travel(actor, "boneyard", 0)):
            return False
    return to_map("LAADYTUM")(actor)


def miles_hardens(actor: Actor) -> bool:
    """Miles takes the journals and the Power Armor for 24 game hours; the Hardened one back, worn."""
    if not hardened(actor):
        d0 = day(actor)
        talk(actor, "miles", MILES_HARDEN, hardened)
        actor.log.emit(
            "miles_hardens", hardened=hardened(actor), power=actor._count(PID_POWER_ARMOR),
            journals=actor._count(PID_JOURNALS), days=round(day(actor) - d0, 2),
        )  # fmt: skip
    if hardened(actor):
        rearm(actor)
    return hardened(actor)


IDEALIST_HARDENED = [
    Step("to the Hub", to_the_hub, checkpoint=True, tries=6),
    Step("downtown", lambda a: journals_carried(a) or hub_map(a, "HUBDWNTN"), checkpoint=True),
    Step("Stapleton: the journals", stapleton_journals, checkpoint=True),
    Step("to Adytum", to_adytum, checkpoint=True, tries=6),
    Step("Miles: the Hardened Power Armor", miles_hardens, checkpoint=True),
]

ROUTES = {"idealist_hardened": IDEALIST_HARDENED}
