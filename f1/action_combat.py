"""Attacks, combat movement and fight decisions for the public Actor."""

import struct
import uuid

from f1 import (
    clock,
    geometry,
    knowledge,
    lof,
    nav,
    odds,
    perception,
    risk,
    scene,
    scripts,
    session,
    state,
    weapons,
    win32,
)
from f1 import engine_map as em
from f1.action_interaction import AIM_OFFSETS
from f1.action_inventory import INVENTORY_AP, PID_STIMPAK
from f1.action_shared import COMBAT_ACTIVE, MOUSE_CROSSHAIR, Held, Outcome
from f1.engine_map import Obj
from f1.memory import ReadError

COMBAT_TURN_OBJ = em.GLOBALS["combat_turn_obj"].address
HOSTILE_KINDS = (  # wild kinds and the mutant army, by prototype name (substring)
    "Rat",
    "Radscorpion",
    "Mantis",
    "Mole",
    "Raider",
    "Deathclaw",
    "Floater",
    "Centaur",
    "Mutant",
    "Nightkin",
    "Coyote",
    "Ghoul Attacker",
)
HOSTILE_EXACT = ("Dog",)  # wild dogs; not "Dogmeat"
RIGHT_CLICKS_TO_CROSSHAIR = 3  # gmouse_3d_toggle_mode: a right click on the map cycles move, arrow, crosshair
FORCED_FLOOR = 0.8  # f1.odds' old rule only: a fight a foe started is left below this win chance
GOOD_SHOT = 50  # percent: below it a gun closes in on a foe that has no gun (f1.odds: fights on computed odds)
HOPELESS_SHOT = 20  # percent: below it, and no way closer this turn, the round is not spent
LONG_SHOT = 5  # percent: after STALL_TURNS turns with nothing done a shot this poor is taken all the same
STALL_TURNS = 2  # turns without a shot, a step or a heal before the fight takes long shots, then steps back
PACK_HEXES = 8  # a foe without a gun with another foe this near it is waited for, not closed in on
AIM_BLOCKED = ("COMBAT", 104)  # a shot whose line of fire is blocked (COMBAT.MSG)
ENGAGING = 1  # object_types.h CRITTER_MANEUVER_ENGAGING; combatai_want_to_join


def retreat_hex(
    at: int, foes: list[int], blocked: set[int] | frozenset[int], max_hexes: int, cam: geometry.Camera
) -> int | None:
    """The free hex on the view within `max_hexes` of `at` that lies farthest from the nearest foe (the nearer of
    equals), or None when none is farther than `at` itself."""
    if not foes:
        return None
    here = min(geometry.distance(at, f) for f in foes)
    best = None
    for r in range(1, max_hexes + 1):
        for t in geometry.ring(at, r):
            if t in blocked or not geometry.on_view(t, cam, 24):
                continue
            key = (min(geometry.distance(t, f) for f in foes), -r)
            if key[0] > here and (best is None or key > best[0]):
                best = (key, t)
    return best[1] if best else None


def target_order(foes: list, chances: dict[int, int], gaps: dict[int, int], blocked_before: set[int],
                 stuck_at: set[int]) -> list:  # fmt: skip
    """The foes in reach in the order a gun tries them: any shot worth taking before a hopeless one, then the ones not
    blocked before and not out of reach, the weakest, the nearest. A foe whose shot was blocked once stays behind the
    others only among shots worth taking: Vault 15, an ironman run: a mole rat's shot was blocked once, and for five
    minutes of turns the fight held fire at a rat 21 hexes off (-25 %) while the first bit HP 48 to 7 beside the
    player, four stimpaks gone."""
    return sorted(
        foes,
        key=lambda e: (
            chances[e.address] < HOPELESS_SHOT,
            e.address in blocked_before,
            e.address in stuck_at,
            e.hp,
            gaps.get(e.address, 99),
        ),
    )


class CombatActions:
    """Fight execution using the Actor equipment, targeting and movement actions."""

    def in_combat(self) -> bool:
        return bool(self.mem.glob("combat_state") & COMBAT_ACTIVE)

    def my_turn(self) -> bool:
        dude = self.mem.u32(em.GLOBALS["obj_dude"].address)
        return self.in_combat() and self.mem.u32(COMBAT_TURN_OBJ) == dude and self.mem.glob("combat_turn_running") == 0

    def combat_order(self) -> tuple[int, ...]:
        """The engine's active combat prefix, in actual turn order (combat.cc list_com)."""
        if getattr(self, "mem", None) is None or not self.in_combat():
            return ()
        count, address = self.mem.glob("list_com"), self.mem.glob("combat_list")
        if not 0 <= count <= 1000 or (count and not address):
            raise ReadError("invalid active combat list")
        return struct.unpack(f"<{count}I", self.mem.read(address, count * 4)) if count else ()

    def enemies(self, only: tuple[str, ...] | None = None) -> list[state.Critter]:
        """Known wild foes and observed hostile factions, including participants and possible onlookers.
        A combatant on a different team can be fighting for the player. Seed a faction from actual player-team
        who-hit links or deliberate targets, then include its joiners. ONLY limits deliberate targets; risk also
        observes every active foe of those hostile factions.
        """
        snap = self.snap()
        dude = snap.dude
        active = set(self.combat_order())
        combat = bool(active)
        context = (getattr(snap, "map_name", ""), dude.elevation)
        if not combat or context != getattr(self, "_combat_hostile_context", None):
            self._combat_hostile_teams = set()
            self._combat_hostile_context = context
        crew = [c for c in state.critters(self.mem) if c.elevation == dude.elevation]
        by_address = {c.address: c for c in [*crew, dude]}
        hit_by = {c.address: self.mem.u32(c.address + Obj.CRITTER_WHO_HIT_ME) for c in by_address.values()}
        player_team = {c.address for c in by_address.values() if c.team == dude.team and not c.dead}
        player_hit = {hit_by[address] for address in player_team}
        targets = getattr(self, "_combat_targets", set()) if combat else set()
        hostile = self._combat_hostile_teams
        names = {c.address: knowledge.proto_name(c.pid) for c in crew}
        wild = {
            c.address
            for c in crew
            if any(k in names[c.address] for k in HOSTILE_KINDS) or names[c.address] in HOSTILE_EXACT
        }
        # Retain the attacker's faction after its death: its living household may already be taking turns.
        for address in player_hit | targets:
            if (c := by_address.get(address)) is not None and c.team != dude.team:
                hostile.add(c.team)
        for c in crew:
            if c.team != dude.team and hit_by[c.address] in player_team:
                hostile.add(c.team)
            if c.address in wild and c.address in active:
                hostile.add(c.team)
        out = []
        for c in crew:
            if c.address == dude.address or c.dead or c.hp <= 0:
                continue
            if c.team == dude.team or c.elevation != dude.elevation:  # companions; other floors of the map
                continue
            name = names[c.address]
            if only is not None:  # a fight with named opponents only (Garl's fistfight among neutral raiders)
                # "script:NAME" picks by the critter's script: the Raiders' Gwen and Alya are "Citizen" like the
                # plain raiders, and must be left for the fight in Garl's room (the raiders plan)
                script = scripts.script_of(self.mem, c.address) if any(k.startswith("script:") for k in only) else ""
                if any(k[7:] == script if k.startswith("script:") else k in name for k in only):
                    out.append(c)
                continue
            direct = hit_by[c.address] in player_team or c.address in player_hit or c.address in targets
            if c.address in wild or direct or (combat and c.team in hostile):
                out.append(c)
        return out

    def attack(self, target: state.Critter) -> Outcome:
        """One attack with the active item on a target (adjacent for fists, in range for a gun); done when over."""
        self._combat_action("attack", target=target.address)
        self.still_view()  # a following view still gliding after the last step would carry the aim away
        s = self.snap()
        pre = {"target": target.address, "target_tile": target.tile, "target_hp": target.hp, "ap": s.dude.ap}
        self.log.emit("action_start", action="attack", **pre)
        if not self.my_turn():
            return self._end(False, "not my turn", pre, "attack")
        held = self.active_item()
        if held is not None and weapons.weapon(held) is None:
            # an item used on something stays in its hand (the COC badge after the red door): its button gives the
            # use cursor, not the crosshair (live, a road fight lost that way); B swaps the active hand
            session.press("b")
            armed = lambda: (h := self.active_item()) is None or weapons.weapon(h) is not None
            win32.wait_for(armed, 1.0, 0.05)
        cx, cy = geometry.tile_center(target.tile, s.camera)
        if s.mouse_mode != MOUSE_CROSSHAIR and not self.arm_crosshair(cx, cy):
            return self._end(False, "no crosshair after the item button", pre, "attack")
        line = self.mem.glob("disp_start")
        blocked = lambda: any(knowledge.says(m, *AIM_BLOCKED) for m in state.messages_since(self.mem, line))
        offsets = list(AIM_OFFSETS)
        point = self.predict(target.address, scene.CRITTER, with_dude=False)  # the crosshair's walk
        if point is not None:
            offsets.insert(0, (point[0] - cx, point[1] - cy))
        hand = self.weapon()
        ranged = hand is not None and weapons.attack_type(hand.pid) == "ranged"
        pre["lof_blocked"] = predicted_block = self.line_blocked(target.address) if ranged else None
        with clock.fast(self.pid):  # the swing's animation (the clicks themselves go in at 1x: clock.held)
            for dx, dy in offsets:  # the computed point, then a few points on the sprite above its hex
                self.point(cx + dx, cy + dy)
                self.click()
                if win32.wait_for(lambda: self.snap().dude.ap < s.dude.ap or blocked(), 1.0, 0.05) and not blocked():
                    break
                if blocked():  # COMBAT.MSG 104: no other point on the target helps (a full run's rats, 34 times)
                    self.log.emit("lof_check", target=target.address, predicted=predicted_block, game=True,
                                  agree=predicted_block is True)  # fmt: skip
                    return self._end(False, "aim blocked", pre, "attack")
            else:
                return self._end(False, "no swing: the clicks missed the target", pre, "attack")
            win32.wait_for(lambda: self.mem.glob("combat_turn_running") == 0, 10, 0.05)
        after = state.read_critter(self.mem, target.address)
        detail = pre | {
            "aim": [dx, dy],
            "target_hp_after": after.hp,
            "killed": after.dead,
            "ap_after": self.snap().dude.ap,
        }
        return self._end(True, "swung", detail, "attack")

    def arm_crosshair(self, x: int, y: int) -> bool:
        """The attack cursor: the item button (intface.c intface_use_item), else right clicks on the map at (x, y),
        which cycle the mouse's modes move, arrow, crosshair (gmouse.c gmouse_3d_toggle_mode; in combat the crosshair
        is skipped only for an item that is no weapon). Measured: in the radscorpion caves, after a critical miss
        dropped the pistol, the item button gave no crosshair for 11 attacks over 9 turns, the bar showing the
        empty hand, and the fight only ended its turns (HP 43 to 1); the right clicks do not need the bar's button."""
        crosshair = lambda: self.mem.glob("gmouse_3d_current_mode") == MOUSE_CROSSHAIR
        self.point(*self.item_button())
        self.click()
        if win32.wait_for(crosshair, 1.5, 0.05):
            return True
        try:
            why = self.item_state()
        except ReadError as e:
            why = {"error": str(e)}
        self.point(x, y)
        for n in range(RIGHT_CLICKS_TO_CROSSHAIR):
            self.click(right=True)
            if win32.wait_for(crosshair, 0.6, 0.05):
                self.log.emit("crosshair", how="right click", clicks=n + 1, item_button=why)
                return True
        self.log.emit("crosshair", how=None, clicks=RIGHT_CLICKS_TO_CROSSHAIR, item_button=why,
                      mode=self.mem.glob("gmouse_3d_current_mode"))  # fmt: skip
        return False

    def rearm(self, s: state.Snapshot, held: Held | None, drew: bool) -> tuple[bool, Held | None, bool]:
        """A turn's start: a hand that cannot fight made one that can. Returns (acted: the turn's loop starts again,
        the weapon in the active hand, `drew`: the empty hand was looked at in this fight).
        - The active hand empty (once a fight): B brings a weapon from the other hand (holstered for a town); else
          the best way to fight from the pack (weapons.choose, the inventory's 4 AP) when it is not fists. Measured:
          a critical miss dropped the 10mm pistol in the radscorpion caves and the fight went on with fists while
          Brass Knuckles (4.9 to the fists' 2.1) and a Knife lay in the pack: the old check drew from the pack only
          with an item in the active hand.
        - An empty gun: reloaded (2 AP); with no rounds that fit, another weapon from the pack, else the empty hand
          (an empty gun cannot even punch)."""
        if held is None and not drew:
            drew = True
            if self.holster(False):
                return True, self.weapon(), drew
            if self.pick_up_dropped(s):  # the draw comes next, with the picked-up weapon among the pack's
                return True, self.weapon(), False
            options = self.weapon_options()
            if options and options[0].pid is not None and s.dude.ap >= INVENTORY_AP and self.ready_weapon().ok:
                return True, self.weapon(), drew
        if held and weapons.uses_ammo(held.pid) and held.loaded == 0 and s.dude.ap >= weapons.RELOAD_AP:
            loaded = self.reload()
            if loaded.ok:
                return True, self.weapon(), drew
            if loaded.reason == "no ammo":  # another weapon with rounds (the inventory costs AP), else fists
                if s.dude.ap >= INVENTORY_AP and self.ready_weapon().ok and self.weapon() not in (None, held):
                    return True, self.weapon(), drew
                if self.holster(True):
                    held = None
        return False, held, drew

    def pick_up_dropped(self, s: state.Snapshot) -> bool:
        """A weapon lying on or beside the player's hex back into the pack: a critical miss drops the one in hand
        there. Measured: in the radscorpion caves the 10mm pistol dropped, the next fight was fought with the Knife
        from the pack, and in melee the player stood in Ian's line of fire (hit instead of the radscorpion, dead).
        One pick per empty hand; a failed one leaves the pack's best."""
        from f1 import world

        lying = [
            t
            for t in world.things(self.mem)
            if t.type == "item"
            and t.elevation == s.elevation
            and weapons.weapon(t.pid) is not None
            and geometry.distance(t.tile, s.dude.tile) <= 1
        ]
        if not lying:
            return False
        dropped = min(lying, key=lambda t: geometry.distance(t.tile, s.dude.tile))
        return self.pick_up(dropped.tile, dropped.pid, timeout_s=8.0).ok

    def forced_group(self) -> tuple[list[state.Critter], list[state.Critter]]:
        """A fight a foe started: its foes as the gate chooses them (those who notice the player, else the nearest)
        and every other foe of the map an onlooker that may join."""
        left = self.enemies()
        if not left:
            return [], []
        at = self.snap().dude.tile
        active = set(self.combat_order())
        group = [c for c in left if c.address in active]
        group = group or [
            c for c in left if perception.notices(c.tile, c.rotation, perception.perception_of(c.pid), at)
        ]
        group = group or [min(left, key=lambda c: geometry.distance(c.tile, at))]
        return group, [c for c in left if c not in group]

    @property
    def flare_prep(self):
        """Any fight a foe began, with flares carried: one lit where the dark costs the shot at the nearest foe
        (quests.light_up decides). Vault 15's entrance: a radscorpion out of the dark at hit 23 was over the bound
        and fled (an ironman run)."""
        from f1 import quests

        if getattr(self, "mem", None) is None or not self._count(quests.PID_FLARE):
            return None

        def prep() -> None:
            group, _ = self.forced_group()
            here = self.snap().dude.tile
            if group:
                quests.light_up(self, min(group, key=lambda c: geometry.distance(c.tile, here)))

        return prep

    def weigh_forced(self) -> odds.Odds | None:
        """A fight a foe started (on a walk: nav.go_to): weighed at its start by the gate's own rule (odds.assess,
        odds.accepted), logged as the gate logs it (`odds`, forced). None when there is no foe to weigh. Measured:
        the risk live check's walks met 27 such fights, 7 of them over the gate's bound (2.7 to 18 %, one at 1 HP)."""
        group, others = self.forced_group()
        if not group:
            return None
        chance = odds.assess(self, group, others)
        self.log.emit(
            "odds", foes=len(group), others=len(others), win=round(chance.win, 3), hp_left=round(chance.hp_left, 1),
            turns=round(chance.turns, 1), rounds=round(chance.rounds, 1), hit=chance.hit, foe_hit=chance.foe_hit,
            death_high=chance.death_high, rule=odds.RULE, forced=True, accept=odds.accepted(chance, FORCED_FLOOR),
            encounter_id=getattr(self, "_encounter_id", None),
        )  # fmt: skip
        return chance

    def end_turn(self) -> Outcome:
        self._combat_action("end_turn")
        self.log.emit("action_start", action="end_turn", ap=self.snap().dude.ap)
        session.press("space")
        with clock.fast(self.pid):
            gone = win32.wait_for(lambda: not self.my_turn(), 3, 0.05)
        return self._end(gone, "turn passed" if gone else "still my turn", {}, "end_turn")

    def fight(
        self,
        max_rounds: int = 60,
        min_hp: int = 8,
        only: tuple[str, ...] | None = None,
        spare: frozenset[str] = frozenset(),
        forced: bool = False,
    ) -> Outcome:
        """Execute one encounter, preserving its identity across bounded resumptions."""
        if not self.in_combat() and not getattr(self, "_encounter_id", None):
            s = self.snap()
            dead = s.screen == "main_menu" or s.dude is None or s.dude.hp <= 0 or getattr(s.dude, "dead", False)
            return self._end(not dead, "died" if dead else "combat over", {"screen": s.screen}, "fight")
        if not getattr(self, "_encounter_id", None):
            self._encounter_id = uuid.uuid4().hex
            self._combat_targets = set()
            self.log.emit("encounter_start", encounter_id=self._encounter_id, forced=forced, only=only)
        encounter = self._encounter_id
        try:
            out = self._fight(max_rounds, min_hp, only, spare, forced)
        except BaseException as exc:
            self.log.emit("encounter_end", encounter_id=encounter, outcome="interrupted", error=type(exc).__name__)
            self._encounter_id = None
            self._combat_onlookers = ()
            raise
        terminal = out.reason in ("combat over", "died", "refused; fled")
        self.log.emit(
            "encounter_end" if terminal else "encounter_pause",
            encounter_id=encounter,
            outcome=out.reason,
            ok=out.ok,
            detail=out.detail,
        )
        if terminal:
            self._encounter_id = None
            self._combat_hostile_teams = set()
            self._combat_hostile_context = None
            self._combat_targets = set()
            # a later combat walk outside this fight goes on a hex at a time while onlookers are kept: Hightower's
            # hex was crossed in two such steps and his talk showed the player out (an ironman scout)
            self._combat_onlookers = ()
        return Outcome(out.ok, out.reason, out.detail | {"encounter_id": encounter})

    def _combat_action(self, action: str, **detail) -> None:
        if encounter := getattr(self, "_encounter_id", None):
            s = self.snap()
            self.log.emit(
                "encounter_action",
                encounter_id=encounter,
                action=action,
                hp=s.dude.hp if s.dude else None,
                ap=s.dude.ap if s.dude else None,
                tile=s.dude.tile if s.dude else None,
                **detail,
            )

    def _combat_signature(self, s, group: list, others: list) -> tuple:
        """Only observed decision inputs; an unsuccessful click does not itself clear target exclusions."""
        from f1 import loot

        def equipment(address: int) -> tuple:
            if getattr(self, "mem", None) is None:
                return ()
            return tuple(
                (pid, self.mem.i32(item + Obj.ITEM_AMMO), qty, self.mem.u32(item + Obj.FLAGS))
                for item, pid, qty in loot.inventory(self.mem, address)
            )

        def observed(c) -> tuple:
            return (
                c.address,
                c.tile,
                c.hp,
                getattr(c, "ap", 0),
                getattr(c, "rotation", 0),
                getattr(c, "results", 0),
                equipment(c.address),
            )

        stats = tuple(odds.player_stats(self.mem)) if getattr(self, "mem", None) is not None else ()
        blockers = ()
        if getattr(self, "mem", None) is not None:
            obstacles = nav.obstacles(self.mem, s.elevation, s.dude.address)
            blockers = (frozenset(obstacles.blocked), frozenset(obstacles.passable_doors))
        return (
            getattr(s, "map_name", ""),
            getattr(s, "elevation", 0),
            s.dude.tile,
            s.dude.hp,
            s.dude.ap,
            stats,
            blockers,
            self.weapon(),
            self._count(PID_STIMPAK),
            equipment(getattr(s.dude, "address", 0)),
            tuple(observed(c) for c in group),
            tuple(observed(c) for c in others),
        )

    def _safe_combat_prefix(self, path: list[int]) -> list[int]:
        """Stop before a step newly enters an excluded onlooker's combat perception."""
        excluded = getattr(self, "_combat_onlookers", ())
        if not path or not excluded:
            return path
        safe = [path[0]]
        for tile in path[1:]:
            activated = [
                c.address
                for c in excluded
                if not perception.notices(c.tile, c.rotation, perception.perception_of(c.pid), path[0], in_combat=True)
                and perception.notices(c.tile, c.rotation, perception.perception_of(c.pid), tile, in_combat=True)
            ]
            if activated:
                self.log.emit(
                    "tactic",
                    move="avoid activating onlookers",
                    tile=tile,
                    foes=activated,
                    encounter_id=getattr(self, "_encounter_id", None),
                )
                break
            safe.append(tile)
        return safe

    def _fight(
        self,
        max_rounds: int,
        min_hp: int,
        only: tuple[str, ...] | None,
        spare: frozenset[str],
        forced: bool,
    ) -> Outcome:
        """Fight until combat is over: shoot the nearest foe in range with a gun (or punch an adjacent one), heal
        with a stimpak below risk.heal_below, end the turn when nothing more can be done. `spare`: scripts of critters that
        must not be hit: a foe with one of them beyond it on the line of fire is not shot (a missed shot flies on
        and hits the first critter past its target with no roll, CE compute_attack: the Raiders' captive women).
        `forced`: a fight a foe started (no gate weighed it): weighed first (`weigh_forced`), and one the gate would
        refuse is left by the map's nearest exit (quests.flee, as Fallout 2's way out of a forced fight) while
        `leave_refused`; when no way out is found it is fought."""
        self.log.emit("action_start", action="fight", forced=forced)
        self._combat_targets = {c.address for c in self.enemies(only)} if only is not None else set()
        # a forced fight's weighed foes: the fight steps towards no other (an onlooker comes by itself, or not at
        # all; the radscorpion caves, an ironman run: the weighed one's shot blocked, eight hexes towards one the
        # gate had not counted, both stung and the player died)
        weighed: set[int] | None = None
        if forced and self.in_combat():
            prep = self.forced_prep or self.flare_prep
            if prep is not None:  # on the player's turn, so the gate weighs what it changed
                with clock.fast(self.pid):
                    win32.wait_for(lambda: self.my_turn() or not self.in_combat(), 45, 0.1)
                if self.in_combat() and self.my_turn():
                    prep()
            chance = self.weigh_forced()
            weighed = {c.address for c in self.forced_group()[0]} or None
            if chance is not None and not odds.accepted(chance, FORCED_FLOOR) and self.leave_refused:
                from f1 import quests

                if quests.flee(self):
                    return self._end(False, "refused; fled", {"death_high": chance.death_high}, "fight")
                self.log.emit("tactic", move="fight a refused fight", why="no way out")
        rounds, turn_of_enter, drew = 0, -1, False
        acted = 0  # the last round with a shot, a step or a heal: the turns since are a stall (foes out of reach)
        missed: dict[int, int] = {}  # target -> attacks whose clicks did not land
        stuck_at: set[int] = set()  # targets with no way to close in on them (tried last)
        # targets whose shot was blocked in an earlier turn: tried after the others (the Hub's captors, the chain:
        # a Guard behind a door frame came first every turn, "aim blocked", while Vinnie in the doorway was
        # never shot at; 60 turns)
        blocked_before: set[int] = set()
        assessed = None
        # A fight the route began (not forced) was chosen from measured games: its estimates are logged, and it is
        # left only once one of them accepted and a later one refuses (the fight changed). The model weighs no
        # allies: Kenji (shooting Killian first) and the raid on Gizmo (Lars's guards) were refused from their first
        # estimate (death 0.45-1.0), fled, and one scout died stuck in Killian's store, one left Junktown.
        accepted_before = forced
        for _ in range(max_rounds * 4):
            if rounds >= max_rounds:
                break
            now = self.snap()
            if now.screen == "main_menu" or now.dude is None or now.dude.hp <= 0 or getattr(now.dude, "dead", False):
                return self._end(False, "died", {"screen": now.screen}, "fight")
            if not self.in_combat():
                return self._end(True, "combat over", {"hp": now.dude.hp}, "fight")
            # my turn, or the end of combat (after the last kill the foes' turns ran out and the wait lasted 45 s)
            with clock.fast(self.pid):  # the foes' turns
                came = win32.wait_for(lambda: self.my_turn() or not self.in_combat(), 45, 0.1)
            s = self.snap()
            if s.screen == "main_menu" or s.dude is None or s.dude.hp <= 0 or getattr(s.dude, "dead", False):
                return self._end(False, "died", {"screen": s.screen}, "fight")
            if not came:
                return self._end(False, "my turn did not come", {}, "fight")
            if not self.in_combat():
                continue
            # One accepted estimate is not permission for a changed fight. Re-observe at every decision;
            # participant moves, joins/deaths, equipment, lost HP and depleted supplies invalidate the estimate.
            group, others = self.forced_group()
            weighed = {c.address for c in group} or weighed
            self._combat_onlookers = others
            signature = self._combat_signature(s, group, others)
            if signature != assessed and group:
                assessed = signature
                chance = odds.assess(self, group, others)
                accept = odds.accepted(chance, FORCED_FLOOR)
                self.log.emit(
                    "encounter_risk",
                    encounter_id=self._encounter_id,
                    foes=[c.address for c in group],
                    others=[c.address for c in others],
                    initial_ap=s.dude.ap,
                    hp=s.dude.hp,
                    death_high=chance.death_high,
                    win=chance.win,
                    accept=accept,
                    rule=odds.RULE,
                )
                if not accept and self.leave_refused and accepted_before:
                    from f1 import quests

                    if quests.flee(self):
                        return self._end(False, "refused; fled", {"death_high": chance.death_high}, "fight")
                    self.log.emit("tactic", move="fight a refused fight", why="no way out")
                accepted_before = accepted_before or accept
                # Target failures only describe the geometry/equipment where they were observed.
                missed.clear()
                stuck_at.clear()
                blocked_before.clear()
            # half the HP, or the near foes' turn of hits when more (risk.heal_below; two radscorpion stings took
            # 22 of a level-2 player's 23, no stimpak used: an ironman run)
            below = risk.heal_below(self.max_hp, risk.danger(self, self.enemies(), s.dude.tile))
            low = s.dude.hp < below and s.dude.ap >= 4 and self._count(PID_STIMPAK) > 0
            if low and self.use_on_self(PID_STIMPAK).ok:  # heal first: 4 AP for the screen still leaves a shot
                acted = rounds
                continue
            if s.dude.hp < min_hp:
                return self._end(False, "hp low; stopping to keep the character alive", {"hp": s.dude.hp}, "fight")
            acted_now, held, drew = self.rearm(s, self.weapon(), drew)
            if acted_now:
                continue
            cost, reach = self.attack_cost(held), self.attack_reach(held)
            # combat.c obj_dist: a big body (multihex) is one hex nearer than its tile
            here = s.dude.tile
            gaps = {e.address: geometry.distance(e.tile, here) - (1 if e.multihex else 0) for e in self.enemies(only)}
            foes = sorted(self.enemies(only), key=lambda e: gaps.get(e.address, 99))
            near = [e for e in foes if gaps.get(e.address, 99) <= reach]
            if near and not geometry.on_view(near[0].tile, s.camera, 24):
                nav.scroll_towards(self, near[0].tile)  # the camera does not follow the player: bring it over
                s = self.snap()
            in_reach = [
                e
                for e in near
                if geometry.on_view(e.tile, s.camera, 16)
                and missed.get(e.address, 0) < 2
                and (weighed is None or e.address in weighed)
            ]
            if spare and held and weapons.attack_type(held.pid) == "ranged":
                kept = [e for e in in_reach if not self._spared_behind(e, spare, s, reach)]
                if len(kept) < len(in_reach):
                    self.log.emit("tactic", move="spare", held_back=len(in_reach) - len(kept))
                in_reach = kept
            # the weakest first: one foe less hits back sooner (Vault 12, a full run: the Agent turned to the Mad
            # Glowing One's 60 HP while the guard it fought had 9 left)
            in_reach.sort(key=lambda e: (e.address in blocked_before, e.hp, gaps.get(e.address, 99)))
            if in_reach and s.dude.ap >= cost and held and weapons.attack_type(held.pid) == "ranged":
                # the tactics: a gun gains 4 a hex closer (combat.c); against a foe without a gun of its own, close
                # in before a poor shot, and let a hopeless one go (the AP left over is armour class on its turn).
                # The weakest foe first only among those worth a shot: in Vault 15 the weakest was a rat
                # 16 hexes off with no way to it (-5 %), held fire at for 12 turns while another bit HP 38 to 4.
                chances = {e.address: odds.shot_chance(self, e, held.pid, gaps.get(e.address, 99)) for e in in_reach}
                in_reach = target_order(in_reach, chances, gaps, blocked_before, stuck_at)
                target, gap = in_reach[0], gaps.get(in_reach[0].address, 99)
                chance = chances[target.address]
                armed = odds.armed_at_range(self.mem, target)
                # a foe with a gun is not closed in on (its shots gain as much), but a shot below LONG_SHOT at it is held
                # all the same: a desert encounter, an ironman run: a gunman 25 hexes off at -15 %, about 30 shots
                # in 90 s and none could hit, until the watchdog called the run stuck
                if chance < GOOD_SHOT and gap > 1 and (not armed or chance < LONG_SHOT):
                    if not armed:
                        # it walks up by itself; a step towards it among others of its kind brings them in too (the
                        # radscorpion caves, two ironman runs: the weighed one's shot was blocked, six hexes in towards
                        # another, then two stung 14-29 a round and the player died)
                        pack = sum(
                            1 for e in foes if e is not target and geometry.distance(e.tile, target.tile) <= PACK_HEXES
                        )
                        onlooker = weighed is not None and target.address not in weighed
                        closer = (
                            0 if pack or onlooker else min(gap - 1, -(-(GOOD_SHOT - chance) // 4), s.dude.ap - cost)
                        )
                        self.log.emit(
                            "tactic", move="wait for it" if pack or onlooker else "close in", chance=chance, gap=gap, hexes=closer,
                            pack=pack, onlooker=onlooker,
                        )  # fmt: skip
                        if closer > 0:
                            moved = self.combat_move_towards(target.tile, closer)
                            if moved.ok:
                                acted = rounds
                                continue
                            if moved.reason == "no path":
                                stuck_at.add(target.address)
                    # Vault 15's rubble, the 1x chain: rats 12-16 hexes off with no way to or from them, 11 % at
                    # best, and Enter could not end the fight (they still saw the Agent): 60 turns held, 545 s. Two
                    # turns of that and a poor shot is better than none; nothing to shoot at, and the Agent steps
                    # back out of their sight.
                    stalled = rounds - acted
                    if chance < HOPELESS_SHOT and stalled >= STALL_TURNS and chance >= LONG_SHOT:
                        self.log.emit("tactic", move="long shot", chance=chance, gap=gap, stalled=stalled)
                    elif chance < HOPELESS_SHOT:
                        self.log.emit("tactic", move="hold fire", chance=chance, gap=gap)
                        # a foe with no way to the player keeps its AP, and combat.c combat_end lets Enter end the
                        # fight when every foe wants to stop (combatai_want_to_stop: full AP, or out of perception).
                        # Vault 15: a rat with no path, 120 turns of holding fire at 0-3 %.
                        if turn_of_enter != rounds:
                            turn_of_enter = rounds
                            session.press("enter")
                            if win32.wait_for(lambda: not self.in_combat(), 3, 0.1):
                                continue
                        if stalled > STALL_TURNS:  # a step back is no progress: the next stalled turn steps on
                            self.retreat(foes, s.dude.ap)
                        self.end_turn()
                        rounds += 1
                        # a new turn tries every foe again here too: the radscorpion caves, an ironman run: the
                        # weighed one's aim was blocked in the first turn, it walked up and stung beside the player
                        # for seven turns, never shot at, while the fight held fire at an onlooker 14 hexes off
                        missed.clear()
                        continue
            if in_reach and s.dude.ap >= cost:
                out = self.attack(in_reach[0])
                if out.ok:
                    acted = rounds
                    continue
                # no line of fire, or hidden; the game's AIM_BLOCKED says so at once: move now
                missed[in_reach[0].address] = (
                    2 if out.reason == "aim blocked" else missed.get(in_reach[0].address, 0) + 1
                )
                if out.reason == "aim blocked":
                    blocked_before.add(in_reach[0].address)
                    if len(in_reach) > 1:
                        continue  # the next foe this same turn
            towards = [e for e in foes if weighed is None or e.address in weighed]
            unhittable = [e for e in near if missed.get(e.address, 0) >= 2 and e in towards]
            if unhittable and s.dude.ap > cost and self.combat_move_towards(unhittable[0].tile, 3).ok:
                missed[unhittable[0].address] = 0  # from closer the clicks may land (Vault 12: a glowing one, 7 HP)
                acted = rounds
                continue
            if not in_reach and s.dude.ap == self.max_ap and turn_of_enter != rounds:
                turn_of_enter = rounds  # once a turn: Enter ends combat when no foe is close (combat_should_end)
                session.press("enter")
                if win32.wait_for(lambda: not self.in_combat(), 3, 0.1):
                    continue
            far = foes and not in_reach and s.dude.ap > cost  # foes too far: close in, keeping AP for a shot
            if far and towards and self.combat_move_towards(towards[0].tile, s.dude.ap - cost).ok:
                acted = rounds
                continue
            if far and rounds - acted > STALL_TURNS and s.dude.ap == self.max_ap:
                self.retreat(foes, s.dude.ap)
            self.end_turn()
            rounds += 1
            # a new turn tries every foe again: a guard in a doorway marked "aim blocked" once was never shot at
            # again, and the fight walked into "no path" for turns (the Hub's captors, live)
            missed.clear()
        return self._end(False, "too many rounds", {"hp": self.snap().dude.hp}, "fight")

    def _spared_behind(self, target: state.Critter, spare: frozenset[str], s: state.Snapshot, reach: int = 35) -> bool:
        """A spared critter a missed shot at `target` could hit: on the engine's miss line (f1.lof.miss_line, the
        exe's check_ranged_miss: from the player through the target to the weapon's reach, up to the first wall).
        Without a readable map: the old screen-line test (within a hex of the line, up to 35 hexes past)."""
        snap = self.scene()
        obj = snap.find(target.address) if snap else None
        if obj is not None and snap.dude is not None:
            spared = {
                o.address
                for o in snap.objects()
                if o.kind == scene.CRITTER and not lof.dead(o) and scripts.script_of(self.mem, o.address) in spare
            }
            return bool(spared) and lof.in_miss_line(snap, snap.dude, obj, reach, spared)
        x0, y0 = geometry.tile_center(s.dude.tile, s.camera)
        x1, y1 = geometry.tile_center(target.tile, s.camera)
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        for c in state.critters(self.mem):
            if c.dead or c.elevation != s.elevation or scripts.script_of(self.mem, c.address) not in spare:
                continue
            cx, cy = geometry.tile_center(c.tile, s.camera)
            along = ((cx - x0) * dx + (cy - y0) * dy) / length
            off = abs((cx - x0) * dy - (cy - y0) * dx) / length
            if length - 8 < along < length + 35 * 32 and off < 28:
                return True
        return False

    def retreat(self, foes: list[state.Critter], max_hexes: int) -> Outcome:
        """In my combat turn: up to `max_hexes` away from foes that can neither be reached nor shot, to the hex on the
        view farthest from the nearest of them: out of their sight, Enter can end the fight (combatai_want_to_stop)."""
        s = self.snap()
        obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
        to = retreat_hex(s.dude.tile, [f.tile for f in foes], obs.blocked | set(obs.exits), max_hexes, s.camera)
        self.log.emit("tactic", move="retreat", to=to, foes=len(foes))
        if to is None:
            return Outcome(False, "nowhere farther", {})
        return self.combat_walk({to}, max_hexes)

    def combat_move_towards(self, tile: int, max_hexes: int) -> Outcome:
        """In my combat turn: walk up to `max_hexes` along our path towards `tile` (a click in move mode; each hex
        costs 1 AP). Done when the player stands still on the chosen hex or stops early."""
        self.still_view()  # a following view still gliding after the last step would carry the aim away
        self._combat_action("move_towards", towards=tile, max_hexes=max_hexes)
        s = self.snap()
        pre = {"from": s.dude.tile, "towards": tile, "max": max_hexes, "ap": s.dude.ap}
        self.log.emit("action_start", action="combat_move", **pre)
        refused: set[int] = set()  # hexes the game would not move to (a red X: taken, or out of reach)
        target, after = s.dude.tile, s
        big = any(c.tile == tile and c.multihex and not c.dead for c in state.critters(self.mem))
        beside = set(geometry.ring(tile, 2 if big else 1))  # a big body takes the ring round its tile itself
        for _ in range(3):
            obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
            blocked = obs.blocked | refused
            path = nav.astar(s.dude.tile, beside - blocked or {tile}, blocked)
            if not path or len(path) < 2:
                return self._end(False, "no path", pre | {"refused": sorted(refused)}, "combat_move")
            path = self._safe_combat_prefix(path[: max_hexes + 1])
            if len(path) < 2:
                return self._end(False, "would activate onlooker", pre, "combat_move")
            # With excluded observers, adjacent clicks keep the engine from choosing a different long path.
            target = path[1] if getattr(self, "_combat_onlookers", ()) else path[-1]
            if not geometry.on_view(target, s.camera, 24):
                nav.scroll_towards(self, target)
                s = self.snap()
            if not self.set_mouse_mode(0):
                return self._end(False, "no move cursor", pre, "combat_move")
            self.point(*geometry.tile_center(target, s.camera))
            self.click()
            with clock.fast(self.pid):
                win32.wait_for(lambda target=target: self.snap().dude.tile == target, 8, 0.1)
                still = lambda: self.mem.glob("combat_turn_running") == 0 and self._still(self.snap().dude.tile)
                win32.wait_for(still, 4)
            after = self.snap()
            if after.dude.tile != s.dude.tile:
                break
            refused.add(target)  # mole rats round the Agent: the hex beside them was taken
        moved = after.dude.tile != s.dude.tile
        detail = pre | {"to": target, "at": after.dude.tile, "ap_after": after.dude.ap, "refused": sorted(refused)}
        return self._end(moved, "moved" if moved else "did not move", detail, "combat_move")

    def combat_walk(self, goals: set[int], max_hexes: int) -> Outcome:
        """In my combat turn: up to `max_hexes` along our path onto one of `goals` (an exit grid: standing on it ends
        the combat and leaves the map, CE map_leave_map), to the farthest of those hexes on the view."""
        self.still_view()  # a following view still gliding after the last step would carry the aim away
        self._combat_action("walk", goals=sorted(goals), max_hexes=max_hexes)
        s = self.snap()
        pre = {"from": s.dude.tile, "goals": len(goals), "max": max_hexes, "ap": s.dude.ap}
        self.log.emit("action_start", action="combat_walk", **pre)
        obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
        path = nav.astar(s.dude.tile, goals, obs.passable_doors)
        if not path or len(path) < 2:
            return self._end(False, "no path", pre, "combat_walk")
        exiting = path[-1] in obs.exits
        if not exiting:
            path = self._safe_combat_prefix(path[: max_hexes + 1])
            if len(path) < 2:
                return self._end(False, "would activate onlooker", pre, "combat_walk")
            if getattr(self, "_combat_onlookers", ()):
                path = path[:2]
        i = nav.next_step(path, s.camera, max_hexes=max_hexes, margin=8)
        if i is None:
            nav.scroll_towards(self, path[min(max_hexes, len(path) - 1)])
            s = self.snap()
            i = nav.next_step(path, s.camera, max_hexes=max_hexes, margin=8)
        if i is None or not self.set_mouse_mode(0):
            return self._end(False, "no step on the view", pre, "combat_walk")
        # the farthest step first; a hex the game will not walk to (its own path differs, or runs past a foe) takes
        # a nearer one: the same refused hex stood thirty turns of a flee in a row (a full run at Full HD)
        for k in sorted({i, max(1, i // 2), 1}, reverse=True):
            self.point(*geometry.tile_center(path[k], s.camera))
            self.click()
            if win32.wait_for(lambda: self.snap().dude.tile != s.dude.tile or self.snap().screen != "map", 1.5, 0.1):
                win32.wait_for(lambda k=k: self.snap().dude.tile == path[k] or self.snap().screen != "map", 8, 0.1)
                i = k
                break
        win32.wait_for(lambda: self.mem.glob("combat_turn_running") == 0, 4, 0.05)
        after = self.snap()
        alive = after.dude is not None and not after.dude.dead and after.dude.hp > 0
        moved = (
            alive
            and after.screen in ("map", "worldmap", "townmap")
            and (after.screen != "map" or after.dude.tile != s.dude.tile)
        )
        detail = pre | {"to": path[i], "at": after.dude.tile if after.dude else None}
        return self._end(moved, "moved" if moved else "did not move", detail, "combat_walk")

    def enemies_close(self, s: state.Snapshot, radius: int = 8) -> bool:
        close = set()
        frontier = {s.dude.tile}
        for _ in range(radius):
            frontier = {geometry.neighbour(t, r) for t in frontier for r in range(6)} - close
            close |= frontier
        return any(e.tile in close for e in self.enemies())
