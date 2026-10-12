"""Explosives: the timer, the Traps roll, the countdown in the game's own event queue, the blast's reach.

From fallout1-ce, each number read in the 1.1 exe's code with capstone:

- Dynamite (pid 51) and Plastic Explosives (85) become their armed protos (206, 209) when the timer is set
  (protinst.cc obj_use_explosive). The timer's dialog (inventry.cc do_move_timer 0x469458, the set-timer kind)
  starts at 60 s (0x3C), its least is 10; plus and minus (6000, 7000) step 10 up to 180; Enter takes only a multiple
  of 10; typed digits count only in the move-items kind.
- The roll: skill_result(player, Traps, 0): a d100 at most the skill succeeds (no 95 % cap); from the game's second
  day a failure by D points is critical at floor(D / 10) % (roll.cc roll_check_critical). A success queues the full
  time (EXPLOSION, 8), a failure half of it, a critical failure none (EXPLOSION_FAILURE, 11 both). Fallout 1 has no
  perk that skips it there.
- The queue (queue.cc, head `queue` 0x662F4C, queue_add 0x4908A0): nodes {time, type, owner, data, next}, in time
  order; one tick is 0.1 s of game time.
- The blast (queue.cc 0x490B48: Dynamite 30-50, Plastic 40-80, cmp 0x33/0xCE then 0x1E/0x32, 0x28/0x50; action_explode
  -> combat.cc compute_explosion_on_extras): rings round its tile up to 3 hexes (a grenade's 2); nothing 4 or more
  hexes away is hit. Damage less the target's explosion DT, then DR.
"""

import struct
from dataclasses import dataclass
from fractions import Fraction

QUEUE_HEAD = 0x662F4C
EXPLOSION, EXPLOSION_FAILURE = 8, 11
DYNAMITE, PLASTIC = 51, 85
ARMED = {DYNAMITE: 206, PLASTIC: 209}
DAMAGE = {DYNAMITE: (30, 50), PLASTIC: (40, 80), 206: (30, 50), 209: (40, 80)}
TIMER_START, TIMER_MIN, TIMER_MAX, TIMER_STEP = 60, 10, 180, 10
BLAST_HEXES = 3
TICKS_PER_SECOND = 10


@dataclass(frozen=True)
class Event:
    time: int  # game ticks
    type: int
    owner: int
    data: int


def events(mem, limit: int = 512) -> list[Event]:
    """The game's queued events in time order."""
    out, node = [], mem.u32(QUEUE_HEAD)
    for _ in range(limit):
        if not node:
            break
        time_, kind, owner, data, node = struct.unpack("<IiIII", mem.read(node, 20))
        out.append(Event(time_, kind, owner, data))
    return out


def pending(mem, owner: int) -> Event | None:
    """The explosion queued for the explosive at OWNER, None when there is none."""
    return next((e for e in events(mem) if e.owner == owner and e.type in (EXPLOSION, EXPLOSION_FAILURE)), None)


def presses(seconds: int) -> tuple[str, int]:
    """The button and the number of its clicks that take the dialog from its start to SECONDS."""
    if seconds % TIMER_STEP or not TIMER_MIN <= seconds <= TIMER_MAX:
        raise ValueError(f"the timer takes {TIMER_MIN}-{TIMER_MAX} s in steps of {TIMER_STEP}, not {seconds}")
    steps = (seconds - TIMER_START) // TIMER_STEP
    return ("plus", steps) if steps >= 0 else ("minus", -steps)


def arming_odds(traps: int, criticals: bool = True) -> tuple[Fraction, Fraction, Fraction]:
    """The arming roll's odds at the Traps skill TRAPS: the full timer, the half timer, the blast at once.
    CRITICALS: from the game's second day on."""
    full = half = at_once = Fraction(0)
    for roll in range(1, 101):
        over = roll - traps
        if over <= 0:
            full += Fraction(1, 100)
            continue
        critical = Fraction(min(over // 10, 100), 100) if criticals else Fraction(0)
        at_once += critical / 100
        half += (1 - critical) / 100
    return full, half, at_once


def outcome(event: Event, armed_at: int, seconds: int) -> str:
    """The roll an event shows: "success" (the full time), "failure" (half), "critical" (at once), "unexpected"."""
    delay = event.time - armed_at
    if event.type == EXPLOSION and delay == seconds * TICKS_PER_SECOND:
        return "success"
    if event.type == EXPLOSION_FAILURE and delay == seconds * TICKS_PER_SECOND // 2:
        return "failure"
    if event.type == EXPLOSION_FAILURE and delay == 0:
        return "critical"
    return "unexpected"


def in_blast(distance: int) -> bool:
    """Whether a critter this many hexes from an explosive's tile is hit by its blast."""
    return distance <= BLAST_HEXES
