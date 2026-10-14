"""Inventory, loot, equipped items and weapon readiness for the public Actor."""

import struct
import time

from f1 import chargen, clock, dialogue, geometry, inputs, knowledge, nav, session, state, ui, weapons, win32
from f1 import engine_map as em
from f1.action_interaction import BODY_GRID, LOOK_SETTLE_S
from f1.action_shared import MOUSE_USE_CROSSHAIR, Held, Outcome
from f1.engine_map import Obj

# The loot screen (inventry.c, INVENTORY_LOOT_*): 537 x 376; slot centres inside it
LOOT_WINDOW, LOOT_BODY_SLOT, LOOT_PLAYER_LIST = (537, 376), (424 + 32, 35 + 24), (46 + 32, 35 + 24 + 96)
LOOT_AIMS = ((-12, -12), (-12, -6), (0, -12), (-24, -12), (12, -12), (0, -6), (-6, -18), (0, 0), (-18, -6))
CONTAINER_AIMS = ((0, -12), (0, -24), (0, -36), (-8, -24), (8, -24), (0, 0), (0, -48), (-12, -12), (12, -12))
OBJECT_IN_LEFT_HAND, OBJECT_IN_RIGHT_HAND, OBJECT_WORN = 0x01000000, 0x02000000, 0x04000000
ITEM_EQUIPPED = OBJECT_IN_LEFT_HAND | OBJECT_IN_RIGHT_HAND | OBJECT_WORN
PID_10MM_PISTOL, PID_STIMPAK, PID_10MM_JHP = 0x08, 0x28, 0x1D
ITEM_STATE_SIZE, ITEM_STATE_ACTION = 0x18, 0x10  # intface.c InterfaceItemState
ITEM_STATE_DISABLED = 4  # InterfaceItemState.isDisabled (a byte): the button is left disabled by intface_enable
BUTTON_FLAG_DISABLED = 0x08  # gnw_types.h: a disabled window button ignores clicks
ITEM_ACTION_PRIMARY, ITEM_ACTION_RELOAD = 1, 5
PROTO_BASE_STATS, STAT_COUNT, STAT_MAX_HP, STAT_MAX_AP = 0x24, 35, 7, 8  # critter proto: base, bonus stats
INVENTORY_CURSOR_HAND, INVENTORY_CURSOR_ARROW = 0, 1  # immode: 0 hand (drag), 1 arrow (action menu), 4 blank
INVENTORY_AP = 4  # the inventory screen's cost in combat
INVENTORY_ROWS = 6  # the inventory screen's list shows six items (inventry.c inven_cur_disp)
LIST_HOLD_S, LIST_GAP_S = 0.05, 0.03  # a burst of Down keys for the list: held past the ~50 ms the game may lose
# Interface bar buttons in game-screen pixels (intface.c positions, bar at y=381 on a 640x480 screen).
ITEM_BUTTON = (361, 440)  # the active item at 640x480 (the bar at (0, 380)): a click enters attack mode
ITEM_BUTTON_CODE = 110  # its right click sends 'n' (the next action): how it is found among the bar's buttons
PICKUP_TOO_HEAVY = ("PROTO", 905)  # a pick-up refused at the carry weight (PROTO.MSG)
LOOT_TOO_HEAVY = ("INVENTRY", 25)  # an item of a loot screen refused at the carry weight (INVENTRY.MSG)
ITEM_MENU_USE, ITEM_MENU_DROP = 1, 2
ITEM_MENU_DROP_UNUSABLE = 1  # an item with no Use (a weapon, ammunition, armour): Look, Drop, ...  # entries of an item's action menu below Look (inven_action_cursor act_use)


class InventoryActions:
    """Item handling using the Actor input, targeting and screen actions."""

    def pick_up(self, tile: int, pid: int, timeout_s: float = 25.0) -> Outcome:
        """Pick up the item `pid` lying on `tile`, or take everything from the container there (the loot screen:
        Shift+A takes all, Escape closes it). Confirmed by the item's count going up. Weighed first (f1.loot
        weigh_object): an item that would not fit, with no room to be made, is refused before any click."""
        from f1 import loot, world

        before = self._count(pid)
        self.log.emit("action_start", action="pick_up", pid=pid, tile=tile, count=before)
        # aimed where the engine names the item itself: in the caves a radscorpion's body lay over the Stimpak and
        # took every blind click (live)
        s = self.snap()
        items = frozenset(t.address for t in world.things(self.mem) if t.tile == tile and t.pid == pid)
        items = frozenset(a for a in items if s.elevation == self.mem.i32(a + Obj.ELEVATION))
        if items and not loot.weigh_object(self, min(items), pid):  # before the click: room made, or refused
            return self._end(False, "too heavy", {"count": [before, before]}, "pick_up")
        line = self.mem.glob("disp_start")
        # the game's refusal ends it at once: Kyle's Powered Armor (85 lb) was clicked for 25 s a try, twice, against
        # the game's refusal (PICKUP_TOO_HEAVY; the bunker)
        heavy = lambda: any(knowledge.says(m, *PICKUP_TOO_HEAVY) for m in state.messages_since(self.mem, line))
        done = lambda: self._count(pid) > before or self.snap().screen != "map" or heavy()
        if not (items and self.click_object(tile, done, timeout_s, targets=items)) and not heavy():
            self.click_object(tile, done, timeout_s)
        if self.snap().screen != "map":  # a container's loot screen
            time.sleep(0.5)
            session.type_text("A")
            time.sleep(0.6)
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen == "map", 5, 0.1)
        self.set_mouse_mode(0)
        after = self._count(pid)
        why = "picked up" if after > before else "too heavy" if heavy() else "not picked up"
        return self._end(after > before, why, {"count": [before, after]}, "pick_up")

    def loot(self, tile: int, timeout_s: float = 25.0) -> Outcome:
        """Take everything from the body or the container (shelves, a locker) on `tile`. A click on it (arrow mode)
        walks the Agent there and opens the loot screen (gmouse.c action_loot_container); a lying body answers up and
        left of its hex, and the Agent's own sprite over it wins the click, so it is clicked from a few hexes away.
        The 1.1 exe has no take-all key (CE's Shift+A did nothing live): each item is dragged from the other side's
        list (the window's x 424, first slot) to the player's (x 46); a stack asks how many. Confirmed by the other
        side's inventory emptying. Weighed first (f1.loot weigh_object): refused before any step when it would not fit."""
        from f1 import actions, knowledge, loot, world

        # on the Agent's own floor: GLOW1 has a Locker on 12925 on two floors, and the other floor's count (1 item,
        # unchanged) made a loot of this one "nothing taken" after 62 s
        elevation = self.snap().elevation
        body = next(
            (c for c in state.critters(self.mem) if c.dead and c.tile == tile and c.elevation == elevation), None
        )
        if body is None:  # a container: an item object of the container kind
            body = next(
                (
                    t
                    for t in world.things(self.mem)
                    if t.tile == tile
                    and t.elevation == elevation
                    and t.type == "item"
                    and (knowledge.proto(t.pid) or {}).get("item_type") == "container"
                ),
                None,
            )
        if body is None:
            return self._end(False, "no body or container there", {"tile": tile}, "loot")
        before = self.mem.i32(body.address + Obj.INV_LENGTH)
        self.log.emit("action_start", action="loot", tile=tile, items=before)
        if before <= 0:  # nothing inside: the Glow's empty lockers took 30 s each of clicks for no loot screen
            return self._end(False, "empty", {"items": [0, 0]}, "loot")
        if not loot.weigh_object(self, body.address, body.pid):  # the whole inventory, before any step or click
            return self._end(False, "too heavy", {"items": before}, "loot")
        opened = lambda: self._loot_window() is not None
        aims, targets = LOOT_AIMS, frozenset()
        if isinstance(body, state.Critter):
            # where the engine names the body under the cursor: the blind aims rested on "Wall." and "Door." over a
            # captor who died in a doorway, and on the Agent's own Power Armor over a raider at its feet, for 25 s a
            # body (the Hub). From a few hexes off when the near view found nothing.
            spot = self._body_spot(body, tile)
            if spot is None and geometry.distance(self.snap().dude.tile, tile) <= 3:
                s = self.snap()
                obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
                away = {t for r in (3, 4) for t in geometry.ring(tile, r) if t not in obs.blocked}
                if away and nav.go_to(self, away, 20)[0]:
                    spot = self._body_spot(body, tile)
            if spot is not None:
                cx, cy = geometry.tile_center(tile, self.snap().camera)
                aims, targets = ((spot[0] - cx, spot[1] - cy), *aims), frozenset({body.address})
                time.sleep(LOOK_SETTLE_S)
                self.click()
                win32.wait_for(opened, timeout_s, 0.1)
        if not isinstance(body, state.Critter):  # a container stands: its sprite rises above the hex
            aims = CONTAINER_AIMS
            s = self.snap()
            if geometry.distance(s.dude.tile, tile) > 1:  # inside first: a roof hides what stands under it
                obs = nav.obstacles(self.mem, s.elevation, s.dude.address)
                nav.go_to(self, {t for t in geometry.ring(tile, 1) if t not in obs.blocked} or {tile}, 60)
            # a point where the engine names the container, as for skills (a pile of bones lies flat on its hex):
            # first where its picture shows (f1.art), then the usual offsets, then the grid. A bookcase took 17 s of
            # misses at 0.4 s each with the offsets alone, and the same search again in click_object (live)
            s = self.snap()
            if not geometry.on_view(tile, s.camera, 150):
                self.center_view()
                s = self.snap()
            cx, cy = geometry.tile_center(tile, s.camera)
            front = self._art_aims(tile, frozenset({body.address})) if actions.ART_AIMS else []
            offsets = front + [a for a in aims if a not in front]
            spot = self._aim_at(frozenset({body.address}), [(cx + dx, cy + dy) for dx, dy in offsets], (cx, cy))
            if spot is not None:
                aims, targets = ((spot[0] - cx, spot[1] - cy), *aims), frozenset({body.address})
                # the cursor rests there and the engine names the container: the click after the settle, no search
                time.sleep(LOOK_SETTLE_S)
                self.click()
                win32.wait_for(opened, timeout_s, 0.1)
        if not opened():
            self.click_object(tile, opened, timeout_s, aims=aims, targets=targets) or (
                targets and self.click_object(tile, opened, timeout_s, aims=aims)
            )
        origin = self._loot_window()
        if origin is None:
            if self.snap().screen in ("barter", "dialogue"):  # a shopkeeper's shelf: his barter, not a loot screen
                dialogue.close(self.mem)
                self.set_mouse_mode(0)
                return self._end(False, "a shop's shelf", {"items": before}, "loot")
            self.set_mouse_mode(0)
            return self._end(False, "no loot screen", {"items": before}, "loot")
        ox, oy = origin
        line = self.mem.glob("disp_start")
        heavy = lambda: any(knowledge.says(m, *LOOT_TOO_HEAVY) for m in state.messages_since(self.mem, line))
        windows = len(state.live_windows(self.mem))
        asking = lambda: len(state.live_windows(self.mem)) > windows  # the how-many box over the loot screen
        quantity = 1
        for _ in range(before + 2):
            left = self.mem.i32(body.address + 0x2C)
            if asking():
                # the box came after the wait below: answered now. With four games at once one stood unanswered for
                # 5 s and the watchdog ended the run (a Vault 15 locker, live)
                session.type_text(str(min(quantity, 999)))
                session.press("enter")
                win32.wait_for(lambda left=left: self.mem.i32(body.address + 0x2C) < left, 2, 0.05)
                continue
            if left <= 0 or heavy():  # the game refused an item at the carry weight (LOOT_TOO_HEAVY; the Glow)
                break
            arr = self.mem.u32(body.address + 0x34)
            quantity = self.mem.i32(arr + 4)
            self.drag(
                ox + LOOT_BODY_SLOT[0], oy + LOOT_BODY_SLOT[1], ox + LOOT_PLAYER_LIST[0], oy + LOOT_PLAYER_LIST[1]
            )
            if quantity > 1 and win32.wait_for(asking, 3.0, 0.05):
                time.sleep(0.2)
                session.type_text(str(min(quantity, 999)))
                session.press("enter")
            win32.wait_for(lambda left=left: self.mem.i32(body.address + 0x2C) < left or asking(), 2, 0.05)
        after = self.mem.i32(body.address + 0x2C)
        if self.snap().screen == "loot":  # on the map an Escape opens the options menu (the rest's)
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen == "map", 5, 0.1)
        self.set_mouse_mode(0)
        why = "looted" if after < before else "too heavy" if heavy() else "nothing taken"
        return self._end(after < before, why, {"items": [before, after]}, "loot")

    def _body_spot(self, body: state.Critter, tile: int) -> tuple[int, int] | None:
        """A point where the engine names the dead `body` under the resting cursor: what a click there acts on
        (gmouse.c object_under_mouse looks past a dead critter at whatever lies under it, and the hover names the
        same object). The usual aims first, then the low grid round its hex, for at most 8 s."""
        s = self.snap()
        if not geometry.on_view(tile, s.camera, 150):
            self.center_view()
            s = self.snap()
        cx, cy = geometry.tile_center(tile, s.camera)
        spots = [(cx + dx, cy + dy) for dx, dy in LOOT_AIMS]
        return self._aim_at(frozenset({body.address}), spots, (cx, cy), grid=BODY_GRID, budget_s=8)

    def _loot_window(self) -> tuple[int, int] | None:
        for w in state.live_windows(self.mem):
            if w[4:] == LOOT_WINDOW:
                return w[2], w[3]
        return None

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Press on (x0, y0), move in steps to (x1, y1), release: the game's drag and drop."""
        self.point(x0, y0)
        self._front()
        r = win32.client_rect(self.hwnd)
        with clock.held(self.pid), inputs.acting(self.guard):
            inputs.mouse_button(down=True)
            time.sleep(0.15)
            steps = 12
            for k in range(1, steps + 1):
                inputs.set_cursor(r.left + x0 + (x1 - x0) * k // steps, r.top + y0 + (y1 - y0) * k // steps)
                time.sleep(0.03)
            time.sleep(0.15)
            inputs.mouse_button(down=False)
        time.sleep(0.3)

    def items(self) -> list[tuple[int, int, int]]:
        """(item address, pid, quantity) of the player's inventory, in its order."""
        dude = self.mem.u32(em.GLOBALS["obj_dude"].address)
        n, arr = self.mem.i32(dude + Obj.INV_LENGTH), self.mem.u32(dude + Obj.INV_ITEMS)
        out = []
        for i in range(max(0, min(n, 200))):
            item, qty = self.mem.u32(arr + 8 * i), self.mem.i32(arr + 8 * i + 4)
            out.append((item, self.mem.u32(item + Obj.PID), qty))
        return out

    def _inventory_window(self) -> tuple[int, int] | None:
        wid = self.mem.glob("i_wid")
        for w in state.live_windows(self.mem):
            if w[0] == wid:
                return w[2], w[3]
        return None

    def open_inventory(self) -> tuple[int, int] | None:
        """The inventory screen, with the hand cursor (drag and drop); its window's origin."""
        for _ in range(3):  # a key during the holster animation is lost (after a readying, a full run in the Glow)
            if self.snap().screen == "inventory":
                break
            session.press("i")
            win32.wait_for(lambda: self.snap().screen == "inventory", 2, 0.1)
        time.sleep(0.4)
        if self.snap().screen != "inventory":
            return None
        origin = self._inventory_window()
        if origin and self.mem.glob("immode") != INVENTORY_CURSOR_HAND:  # a right click toggles hand and arrow
            self.point(origin[0] + 300, origin[1] + 20)
            self.click(right=True)
            win32.wait_for(lambda: self.mem.glob("immode") == INVENTORY_CURSOR_HAND, 1.0, 0.05)
        return origin

    def close_inventory(self) -> None:
        if self.snap().screen == "inventory":
            session.press("esc")
            win32.wait_for(lambda: self.snap().screen != "inventory", 3, 0.1)

    def _scroll_list(self, slot: int) -> int | None:
        """Scroll the inventory screen's list (six rows; Down moves it one item, inventry.c) until `slot` shows; its
        row, or None. The keys go in one burst and the engine's stack_offset[0] says where the list stands: one key at
        a time, 0.15 s apart and each through the presence guard, took 6-10 s for a holodisk at the end of a long
        list (the Glow's four disks: 39 s)."""
        now = self.mem.glob("stack_offset")
        want = (
            now if now <= slot < now + INVENTORY_ROWS else max(0, slot - (INVENTORY_ROWS - 1)) if slot > now else slot
        )
        for _ in range(4):
            now = self.mem.glob("stack_offset")
            if now == want:
                break
            session.press_keys(["down" if want > now else "up"] * abs(want - now), LIST_GAP_S, LIST_HOLD_S)
            win32.wait_for(lambda: self.mem.glob("stack_offset") == want, 1.0, 0.03)
        row = slot - self.mem.glob("stack_offset")
        return row if 0 <= row < INVENTORY_ROWS else None

    def _list_slot(self, pid: int) -> int | None:
        """Where an item stands in the inventory screen's list: equipped items are not listed."""
        listed = [p for item, p, _ in self.items() if not self.mem.u32(item + Obj.FLAGS) & ITEM_EQUIPPED]
        return listed.index(pid) if pid in listed else None

    def equip(self, pid: int, hand: str = "left") -> Outcome:
        """Drag an item from the inventory list into a hand slot or the armor slot ("armor"); confirmed by the item's
        flag (in the left/right hand, worn)."""
        self.log.emit("action_start", action="equip", pid=pid, hand=hand)
        origin = self.open_inventory()
        if origin is None:
            return self._end(False, "no inventory screen", {}, "equip")
        slot = self._list_slot(pid)
        if slot is None:
            self.close_inventory()
            return self._end(False, "item not in the list", {}, "equip")
        shown = self._scroll_list(slot)
        if shown is None:
            self.close_inventory()
            return self._end(False, "list not scrolled", {"slot": slot}, "equip")
        ox, oy = origin
        sx, sy = ox + 46 + 32, oy + 35 + 48 * shown + 24
        targets = {"left": (154, 286), "right": (245, 286), "armor": (154, 183)}  # inven_pickup's slots (inventry.c)
        tx, ty = ox + targets[hand][0] + 45, oy + targets[hand][1] + 30
        self.drag(sx, sy, tx, ty)
        self.close_inventory()
        flag = {"left": OBJECT_IN_LEFT_HAND, "right": OBJECT_IN_RIGHT_HAND, "armor": OBJECT_WORN}[hand]
        ok = any(p == pid and self.mem.u32(item + Obj.FLAGS) & flag for item, p, _ in self.items())
        return self._end(ok, "equipped" if ok else "not in the hand", {"slot": slot}, "equip")

    def unequip(self, hand: str = "left") -> Outcome:
        """Drag whatever is in a hand slot (or the armor slot) back to the inventory list; confirmed by nothing
        carrying that slot's flag. Scripts that check a hand slot (the Cathedral's nightkin) see an empty hand only
        so: a holstered gun still sits in its slot."""
        flag = {"left": OBJECT_IN_LEFT_HAND, "right": OBJECT_IN_RIGHT_HAND, "armor": OBJECT_WORN}[hand]
        held = lambda: [p for item, p, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & flag]
        self.log.emit("action_start", action="unequip", hand=hand, held=held())
        if not held():
            return self._end(True, "empty already", {}, "unequip")
        origin = self.open_inventory()
        if origin is None:
            return self._end(False, "no inventory screen", {}, "unequip")
        ox, oy = origin
        slots = {"left": (154, 286), "right": (245, 286), "armor": (154, 183)}  # inven_pickup's slots (inventry.c)
        sx, sy = ox + slots[hand][0] + 45, oy + slots[hand][1] + 30
        self.drag(sx, sy, ox + 46 + 32, oy + 35 + 24)  # onto the list
        self.close_inventory()
        ok = not held()
        return self._end(ok, "emptied" if ok else "still held", {}, "unequip")

    def use_on_self(self, pid: int) -> Outcome:
        """Use an item (a stimpak) from the inventory screen. In Fallout 1 a drop on the player's picture only fills
        containers (inven_pickup); use goes through the item's action menu (inven_action_cursor): arrow cursor (a
        right click), hold the left button on the item until the menu opens, move down one entry (Look, Use, ...),
        release. Confirmed by the count going down, or HP going up. In combat the inventory screen itself costs 4 AP."""
        self.log.emit("action_start", action="use_on_self", pid=pid)
        hp0 = self.snap().dude.hp
        count0 = sum(q for _, p, q in self.items() if p == pid)
        why = self.item_menu(pid, ITEM_MENU_USE)
        if why:
            return self._end(False, why, {}, "use_on_self")
        time.sleep(0.6)
        self.close_inventory()
        hp1 = self.snap().dude.hp
        count1 = sum(q for _, p, q in self.items() if p == pid)
        # used up, or HP up: the First Aid Kit stays (HP 17 -> 22 in Vault 15 was logged "not used")
        ok = count1 < count0 or hp1 > hp0
        return self._end(ok, "used" if ok else "not used", {"hp": [hp0, hp1], "count": [count0, count1]}, "use_on_self")

    def item_menu(self, pid: int, entry: int) -> str | None:
        """An entry of the item's action menu in the inventory screen (inven_action_cursor): 1 Use, 2 Drop for an
        item that can be used (Look, Use, Drop, Cancel). The screen is left open for the caller (who closes it).
        None when the entry was chosen, else why not."""
        origin = self.open_inventory()
        slot = self._list_slot(pid)
        if origin is None or slot is None:
            self.close_inventory()
            return "item not in the list"
        shown = self._scroll_list(slot)  # looted stimpaks come last (slot 19, MBVATS12's cell)
        if shown is None:
            self.close_inventory()
            return "list not scrolled"
        ox, oy = origin
        sx, sy = ox + 44 + 32, oy + 35 + 48 * shown + 24
        self.point(sx, sy)
        for _ in range(2):
            if self.mem.glob("immode") == INVENTORY_CURSOR_ARROW:
                break
            self.click(right=True)
            win32.wait_for(lambda: self.mem.glob("immode") == INVENTORY_CURSOR_ARROW, 1.0, 0.05)
        if self.mem.glob("immode") != INVENTORY_CURSOR_ARROW:
            self.close_inventory()
            return f"no arrow cursor in the inventory (immode {self.mem.glob('immode')})"
        r = win32.client_rect(self.hwnd)
        self._front()
        with clock.held(self.pid), inputs.acting(self.guard):
            inputs.mouse_button(down=True)
            time.sleep(0.8)  # held past the button repeat time: the action menu opens under the cursor
            for k in range(1, 4 * entry + 1):
                inputs.set_cursor(r.left + sx, r.top + sy + 4 * k)  # 16 px down an entry ("Use" is one down)
                time.sleep(0.05)
            time.sleep(0.3)
            inputs.mouse_button(down=False)
        return None

    def use_item_on(
        self, pid: int, tile: int, done, timeout_s: float = 25.0, aims=None, targets=frozenset()
    ) -> Outcome:
        """An item used on the object on `tile` (a rope on a hole, a radio on a console): the item into the right hand,
        that hand active, and the item button, which for an item made to be used on things gives the use cursor
        (intface.c intface_use_item); then a click on the object. The weapon stays in the left hand."""
        detail = {"pid": pid, "tile": tile}
        self.log.emit("action_start", action="use_item_on", **detail)
        in_right = any(p == pid and self.mem.u32(i + Obj.FLAGS) & OBJECT_IN_RIGHT_HAND for i, p, _ in self.items())
        # an item left there by the last use (a Glow key) is swapped by the drop; if the game keeps it, the hand is
        # emptied first
        if (
            not in_right
            and not self.equip(pid, "right").ok
            and not (self.unequip("right").ok and self.equip(pid, "right").ok)
        ):
            return self._end(False, "not in the right hand", detail, "use_item_on")
        for _ in range(2):
            if self.mem.glob("itemCurrentItem") == 1:
                break
            session.press("b")
            time.sleep(0.5)  # the swap's animation (holster)

        def arm() -> bool:
            if self.mem.glob("gmouse_3d_current_mode") != MOUSE_USE_CROSSHAIR:
                self.point(*self.item_button())
                self.click()
            return win32.wait_for(lambda: self.mem.glob("gmouse_3d_current_mode") == MOUSE_USE_CROSSHAIR, 1.5, 0.05)

        return self._use_in_mode(tile, arm, done, timeout_s, aims, "use_item_on", detail, targets)

    def item_action(self) -> int:
        """The interface bar's action for the active hand (intface.c): 1 single, 2 aimed, 5 reload, 0 use."""
        hand = self.mem.glob("itemCurrentItem")
        return self.mem.i32(em.GLOBALS["itemButtonItems"].address + ITEM_STATE_SIZE * hand + ITEM_STATE_ACTION)

    def _set_item_action(self, action: int) -> bool:
        """Cycle the item button's action with N until it is `action` (reload is offered only when not full)."""
        for _ in range(7):
            if self.item_action() == action:
                return True
            session.press("n")
            time.sleep(0.2)
        return self.item_action() == action

    def reload(self) -> Outcome:
        """Reload the weapon in the active hand: N until the item button says reload, click it, back to single shot.
        Only with ammunition that fits (weapons.fits: the caliber, and the same kind while not empty). Confirmed by
        the loaded count. In combat it costs 2 AP."""
        held = self.weapon()
        if held is None or not weapons.uses_ammo(held.pid):
            return self._end(False, "no gun", {}, "reload")
        before = held.loaded
        if not any(weapons.fits(held.pid, p, before, held.ammo_pid) for _, p, _ in self.items()):
            return self._end(False, "no ammo", {"loaded": before}, "reload")
        self.log.emit("action_start", action="reload", loaded=before)
        if not self._set_item_action(ITEM_ACTION_RELOAD):
            self._set_item_action(ITEM_ACTION_PRIMARY)
            return self._end(False, "no reload action on the item button", {"loaded": before}, "reload")
        self.point(*self.item_button())
        self.click()
        win32.wait_for(lambda: (self.weapon() or held).loaded > before, 3, 0.1)
        after = (self.weapon() or held).loaded
        self._set_item_action(ITEM_ACTION_PRIMARY)
        return self._end(
            after > before, "reloaded" if after > before else "not reloaded", {"loaded": [before, after]}, "reload"
        )

    def rules(self) -> weapons.Rules:
        """What the character brings to an attack, from memory: ST, the Fast Shot trait, the perks that change AP."""
        perks = chargen.ints(self.mem, "perk_lev", 64)
        return weapons.Rules(
            strength=chargen.special(self.mem)[0],
            fast_shot=weapons.TRAIT_FAST_SHOT in chargen.ints(self.mem, "pc_trait", 2),
            bonus_hth_attacks=perks[weapons.PERK_BONUS_HTH_ATTACKS] > 0,
            bonus_rate_of_fire=perks[weapons.PERK_BONUS_RATE_OF_FIRE] > 0,
            heave_ho=perks[weapons.PERK_HEAVE_HO],
        )

    def skills(self) -> dict[str, int]:
        return chargen.skill_values(self.mem)

    def carried_weapons(self) -> list[weapons.Carried]:
        """Every weapon the player has (in hand or not), with its rounds and the carried rounds that fit it: an ammo
        entry is a stack of boxes whose top box holds the object's count, the others are full."""
        entries = self.items()
        boxes = []
        for item, pid, qty in entries:
            p = knowledge.proto(pid)
            if p and "ammo" in p and qty > 0:
                boxes.append((pid, (qty - 1) * p["ammo"]["quantity"] + self.mem.i32(item + Obj.ITEM_AMMO)))
        out = []
        for item, pid, _qty in entries:
            if weapons.weapon(pid) is None:
                continue
            loaded, loaded_pid = self.mem.i32(item + Obj.ITEM_AMMO), self.mem.i32(item + Obj.ITEM_AMMO_PID)
            spare = sum(n for a, n in boxes if weapons.fits(pid, a, loaded, loaded_pid))
            out.append(weapons.Carried(pid, loaded, loaded_pid, spare))
        return out

    def weapon_options(self) -> list[weapons.Option]:
        return weapons.choose(self.carried_weapons(), self.skills(), self.rules(), self.max_ap)

    def ready_weapon(self) -> Outcome:
        """The best way to fight (weapons.choose) in the left hand, which is made the active one; fists mean nothing
        to do. Outside combat: the inventory screen costs AP in a fight."""
        options = self.weapon_options()
        best = options[0] if options else None
        detail = {"best": best.name if best else None, "score": round(best.score, 1) if best else 0}
        self.log.emit("action_start", action="ready_weapon", options=[(o.name, round(o.score, 1)) for o in options])
        if best is None or best.pid is None:
            return self._end(True, "fists are best", detail, "ready_weapon")
        left = next((p for item, p, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & OBJECT_IN_LEFT_HAND), None)
        if left != best.pid and not self.equip(best.pid, "left").ok:
            return self._end(False, "could not equip", detail, "ready_weapon")
        self.holster(False)  # the weapon's hand active
        held = self.weapon()
        ok = held is not None and held.pid == best.pid
        return self._end(ok, "ready" if ok else "not in the active hand", detail, "ready_weapon")

    def attack_cost(self, held: Held | None) -> int:
        """AP of an attack with what is in the active hand, in the item button's mode (primary, aimed ...)."""
        action = self.item_action()
        secondary, aimed = action in weapons.SECONDARY_ACTIONS, action in weapons.AIMED_ACTIONS
        return weapons.ap_cost(held.pid if held else None, self.rules(), secondary and bool(held), aimed)

    def attack_reach(self, held: Held | None) -> int:
        secondary = self.item_action() in weapons.SECONDARY_ACTIONS and bool(held)
        return weapons.reach(held.pid if held else None, self.rules(), secondary)

    def item_button(self) -> tuple[int, int]:
        """The active item's button on the interface bar: at 1920x1080 the 800-wide bar sits at (560, 980) and the
        button at (987..1174, 1006..1072) (measured); found by its right click's code, else 640x480's."""
        bar = self.mem.glob("interfaceWindow")
        button = next((b for b in ui.buttons(self.mem, bar) if b.codes[5] == ITEM_BUTTON_CODE), None)
        return button.center if button else ITEM_BUTTON

    def weapon(self) -> Held | None:
        """The weapon in the active hand (itemCurrentItem: 0 left, 1 right), if any: any weapon prototype. With the
        other, empty hand active the player punches: that is how a weapon is holstered in Fallout 1."""
        hand_flag = OBJECT_IN_LEFT_HAND if self.mem.glob("itemCurrentItem") == 0 else OBJECT_IN_RIGHT_HAND
        for item, pid, _ in self.items():
            if self.mem.u32(item + Obj.FLAGS) & hand_flag and weapons.weapon(pid) is not None:
                return Held(pid, self.mem.i32(item + Obj.ITEM_AMMO), self.mem.i32(item + Obj.ITEM_AMMO_PID))
        return None

    def holstered(self) -> Held | None:
        """The weapon in the other hand while the active one holds none: what B draws (holster(False)), as a
        fight's first turn does (Actor.rearm)."""
        if self.weapon() is not None:
            return None
        other = OBJECT_IN_RIGHT_HAND if self.mem.glob("itemCurrentItem") == 0 else OBJECT_IN_LEFT_HAND
        for item, pid, _ in self.items():
            if self.mem.u32(item + Obj.FLAGS) & other and weapons.weapon(pid) is not None:
                return Held(pid, self.mem.i32(item + Obj.ITEM_AMMO), self.mem.i32(item + Obj.ITEM_AMMO_PID))
        return None

    def active_item(self) -> int | None:
        """The pid of whatever the active hand holds (itemCurrentItem: 0 left, 1 right), a weapon or not."""
        flag = OBJECT_IN_LEFT_HAND if self.mem.glob("itemCurrentItem") == 0 else OBJECT_IN_RIGHT_HAND
        return next((pid for item, pid, _ in self.items() if self.mem.u32(item + Obj.FLAGS) & flag), None)

    def holster(self, on: bool = True) -> bool:
        """Put the gun away (make the empty hand active) or draw it again: the B key swaps the active hand
        (game.c). Raiders attack a visitor with a weapon out."""
        for _ in range(2):
            if (self.weapon() is None) == on:
                return True
            session.press("b")
            time.sleep(0.6)  # the swap's animation: a key pressed during it is lost (a wait on the hand flag was not
            # enough: "not in the active hand" after a readying)
        return (self.weapon() is None) == on

    def item_state(self) -> dict:
        """The active hand's InterfaceItemState and the item button's window flags: why a click on it did nothing."""
        hand = self.mem.glob("itemCurrentItem")
        base = em.GLOBALS["itemButtonItems"].address + ITEM_STATE_SIZE * hand
        raw = self.mem.read(base, ITEM_STATE_SIZE)
        bar = self.mem.glob("interfaceWindow")
        button = next((b for b in ui.buttons(self.mem, bar) if b.codes[5] == ITEM_BUTTON_CODE), None)
        return {
            "hand": hand, "item": struct.unpack_from("<I", raw, 0)[0], "disabled": raw[ITEM_STATE_DISABLED],
            "weapon": raw[ITEM_STATE_DISABLED + 1], "action": struct.unpack_from("<i", raw, ITEM_STATE_ACTION)[0],
            "button_flags": button.flags if button else None,
            "button_disabled": bool(button.flags & BUTTON_FLAG_DISABLED) if button else None,
        }  # fmt: skip

    def armed(self) -> bool:
        """A weapon that reaches past the next hex and has rounds to fire: fists and a knife did nothing to armoured
        road foes (26 HP each, 0 damage a punch)."""
        best = next(iter(self.weapon_options()), None)
        return best is not None and best.pid is not None and best.reach > 1 and bool(best.rounds)

    @property
    def max_ap(self) -> int:
        """Action points at the start of a turn: base plus bonus of stat 8 (the Agent's 10)."""
        base = em.GLOBALS["pc_proto"].address + PROTO_BASE_STATS
        return self.mem.i32(base + 4 * STAT_MAX_AP) + self.mem.i32(base + 4 * (STAT_COUNT + STAT_MAX_AP))

    def _count(self, pid: int) -> int:
        return sum(q for _, p, q in self.items() if p == pid)

    @property
    def max_hp(self) -> int:
        """The player's maximum HP: base plus bonus of stat 7 in the player's prototype (level-ups raise the base)."""
        base = em.GLOBALS["pc_proto"].address + PROTO_BASE_STATS
        return self.mem.i32(base + 4 * STAT_MAX_HP) + self.mem.i32(base + 4 * (STAT_COUNT + STAT_MAX_HP))
