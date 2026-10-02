"""Where Fallout 1.1 (Steam FALLOUTW.EXE) keeps things: addresses, struct offsets, code fingerprints.

The single source of the numbers. Addresses come from the `// 0x...` comments of fallout1-ce
(github.com/alexbatalov/fallout1-ce), which follow the 1.1 build of November 1997; `python -m f1.verify_exe`
checks them against a binary. Struct offsets are the 32-bit layouts of fallout1-ce's headers; each one says
whether it has been seen right in a live game yet. The static evidence: python -m f1.verify_exe.
"""

from dataclasses import dataclass

# The Steam build 300289's FALLOUTW.EXE, as installed.
STEAM_EXE_SHA256 = "444257e5fe91e6c333261aecadacb1f46dbba0319fa0b187de89f36914f32272"
# The instance's copy after f1_res_patcher.exe (38 bytes: the entry calls LoadLibraryA("f1_res.dll")).
INSTANCE_EXE_SHA256 = "5bdae4c57773b4027b15b918b78ef9ca7d9f12a58d0151563151ddb97e9b7b9f"
BUILD_STRING = b"Nov 11 1997 14:59:39"  # fallout1-ce VERSION_BUILD_TIME, also in the exe
IMAGE_BASE = 0x400000  # no ASLR: the exe always loads here
# The High Resolution Patch (f1_res.dll, image base 0x10000000, no ASLR) keeps the message box's text itself: 100
# lines of 256 bytes from here, written at the engine's disp_start (measured live: "Game Loaded." at line
# 0, then each new message one line on, disp_start counting along). The engine's disp_str stays empty.
HRP_MESSAGES, HRP_MESSAGE_LINE, HRP_MESSAGE_LINES = 0x10064728, 0x100, 100


@dataclass(frozen=True)
class Global:
    name: str
    address: int
    min_code_refs: int  # how often code must reference the address for the static check to believe it
    note: str = ""


GLOBAL_LIST = (
    Global("obj_dude", 0x65F618, 100, "Object* of the player"),
    Global("objectTable", 0x6382F0, 10, "ObjectListNode* [40000], every object on the map by tile"),
    Global("combat_state", 0x4FEC84, 20, "combat flags; 2 at start"),
    Global("combat_turn_running", 0x4FEC80, 3),
    Global("combat_list", 0x56BC8C, 10, "Object** in turn order"),
    Global("map_elevation", 0x505AF8, 20),
    Global("map_data", 0x6302D0, 1, "MapHeader of the current map"),
    Global("tile_center_tile", 0x668E54, 5, "the camera"),
    Global("mouse_x", 0x671F30, 5, "the engine's cursor, screen pixels"),
    Global("mouse_y", 0x671F2C, 5),
    Global("mouse_buttons", 0x671F38, 5),
    Global("game_global_vars", 0x504FC8, 20, "int* GVARs (quest state); NULL before a game starts"),
    Global("num_game_global_vars", 0x504FCC, 3),
    Global("curr_pc_stat", 0x6651FC, 1, "int[5]: unspent skill points, level, experience, reputation, karma"),
    Global("fallout_game_time", 0x5078C0, 3, "game time in 0.1 s ticks; 264600 (07:21) at start, seen live"),
    Global("main_window", 0x505A84, 3, "GNW window id of the main menu; -1 while there is none"),
    Global("in_main_menu", 0x505A98, 1, "bool"),
    Global("main_menu_created", 0x505A9C, 1, "bool"),
    Global("main_menu_timeout", 0x505AA0, 1, "ms idle in the main menu before the attract loop; 120000, seen live"),
    Global("GNW95_isActive", 0x53A290, 1, "bool: the window is active; get_input() stalls while it is not"),
    Global("select_window_id", 0x50797C, 3, "GNW window of the character selection screen; -1 when closed"),
    Global("premade_index", 0x5079E0, 3, "which premade character the selection screen shows"),
    Global("game_ui_disabled", 0x504FBC, 3, "bool"),
    Global("game_user_wants_to_quit", 0x504FD4, 5),
    Global("display_win", 0x6303C8, 3, "GNW window of the map view"),
    Global("map_script_id", 0x505AE4, 3),
    Global("map_local_vars", 0x505AE8, 3, "int*: every script's LVARs on this map (each script has an offset)"),
    Global("num_map_local_vars", 0x505AF0, 3),
    Global("scriptlists", 0x507860, 3, "scripts.c: ScriptList[5] {head, tail, extents, next id}; critters are 4"),
    Global("obj_mouse", 0x59520C, 5, "Object* of the hex cursor: its tile is the hex under the mouse (move mode)"),
    Global("obj_mouse_flat", 0x595210, 3),
    Global("gmouse_enabled", 0x50525C, 3),
    Global("gmouse_3d_current_mode", 0x505394, 3, "0 move (hex cursor), 1 arrow (command), 2 crosshair, ..."),
    Global("last_object", 0x595214, 2, "Object* the arrow cursor rested on 250 ms (gmouse.c: 'You see: ...')"),
    # The camera, as tile_set_center() leaves it; tile_coord() maps a tile to window pixels from these.
    Global("tile_offx", 0x668E08, 3),
    Global("tile_offy", 0x668E04, 3),
    Global("tile_x", 0x668E24, 3),
    Global("tile_y", 0x668E28, 3),
    Global("grid_width", 0x668E38, 3, "hexes per row: 200"),
    Global("buf_width", 0x668E30, 2, "map view width in pixels"),
    Global("buf_length", 0x668E1C, 2, "map view height in pixels"),
    Global("combat_turn_obj", 0x56BC80, 3, "Object* whose turn it is; the player's turn when it is obj_dude"),
    Global("window", 0x6AC1E8, 3, "GNW Window* [50], drawn bottom to top"),
    Global("num_windows", 0x6AC2B4, 3),
    Global("systemCmap", 0x6732C0, 2, "the palette on screen: 256 x RGB, 6-bit components"),
    # Each screen's GNW window id. Several are uninitialised statics that keep a stale id after the screen
    # closes, and GNW reuses ids: a screen is up when its id is the top live window (f1/state.py).
    Global("interfaceWindow", 0x50560C, 3, "the interface bar"),
    Global("edit_win", 0x56EC74, 3, "character screen"),
    Global("skill_cursor", 0x504F2C, 2, "character screen: the selected skill (0 Small Guns ... 14 Speech)"),
    Global("name_sort_list", 0x56E340, 2, "character screen pickers (perks, kills): {int value; char* name}[]"),
    Global("perk_crow", 0x56ECB4, 4, "perk window: the first line on view (the pick is crow + cline)"),
    Global("perk_cline", 0x56EC80, 4, "perk window: the selected line on view"),
    Global("free_perk", 0x56ED2D, 3, "byte: a perk to pick; the character screen opens the perk window first"),
    Global("i_wid", 0x59CEF8, 3, "inventory, loot, barter, use-item-on (immode tells which)"),
    Global("inven_dude", 0x505640, 3, "inventry.c: the one whose inventory is shown (the player)"),
    Global("stack_offset", 0x59CD74, 3, "int[10]: the player list's scroll (first slot shown)"),
    Global("target_stack_offset", 0x59CD9C, 3, "int[10]: the other side's list scroll (barter, loot)"),
    Global("barter_mod", 0x59CE24, 3, "the barter modifier the talk set (percent points)"),
    Global("ptable", 0x59CEC4, 3, "Object*: the player's barter table"),
    Global("btable", 0x59CED8, 3, "Object*: the merchant's barter table"),
    Global("barter_back_win", 0x59CEE4, 3, "the barter screen's window"),
    Global("immode", 0x59CED0, 3, "inventory mode"),
    Global("dialogueWindow", 0x505030, 3, "dialogue: the main window; -1 when none"),
    Global("gdialog_state", 0x505008, 3, "dialogue state; -1 when none"),
    Global("pip_win", 0x662C88, 3, "Pip-Boy"),
    Global("world_win", 0x670FD4, 3, "world map"),
    Global("tcode_xref", 0x670E40, 1, "town map: button i (code 514 + i) enters section tcode_xref[i]"),
    Global("itemButtonItems", 0x595680, 1, "interface bar item states, 24 bytes per hand; action at +0x10"),
    Global("itemCurrentItem", 0x505578, 2, "the active hand: 0 left, 1 right"),
    Global("bar_window", 0x505610, 2, "indicator boxes above the bar (LEVEL, POISONED, SNEAK...); -1 none"),
    Global("disp_str", 0x56C38C, 1, "the message box: 100 lines of 80 chars (display.c)"),
    Global("disp_start", 0x56E2E4, 2, "next line of disp_str to write; the newest is the one before"),
    Global("skldxwin", 0x665188, 3, "skilldex"),
    Global("optnwin", 0x661E6C, 3, "options menu"),
    Global("prfwin", 0x661E70, 3, "preferences"),
    Global("combat_difficulty", 0x661F14, 3, "options.c preference: 0 Wimpy, 1 Normal, 2 Rough"),
    Global("game_difficulty", 0x661F1C, 3, "options.c preference: 0 Easy, 1 Normal, 2 Hard (non-combat skills)"),
    Global("map_global_vars", 0x505AEC, 10, "int*: the map's variables, map_var(n) in scripts (map.c)"),
    Global("num_map_global_vars", 0x505AF4, 10, "how many the current map has"),
    Global("combat_speed", 0x661F08, 3, "options.c preference: 0..50"),
    Global("prf_running", 0x661F04, 3, "options.c preference: 1 always run"),
    Global("player_speedup", 0x661EEC, 3, "options.c preference: 1 the player moves at the combat speed"),
    # gsound_background_volume_set(music_volume) on every change; a movie's volume is the background volume read once
    # at startup (gmovie.cc gmovie_init), so a game started with fallout.cfg's music on keeps its movies' sound
    Global("music_volume", 0x661EFC, 3, "options.c preference: 0..32767, the background music"),
    Global("lsgwin", 0x612D58, 3, "save/load screen"),
    Global("elev_win", 0x56ED58, 3, "elevator panel"),
    Global("endgame_window", 0x56EEC0, 2, "endgame slides"),
    Global("call_win", 0x56BC74, 3, "called-shot window"),
    # The mouse cursor's picture, drawn at (mouse_x, mouse_y) skipping mouse_trans (mouse.c mouse_show()).
    Global("mouse_shape", 0x539DC8, 2, "unsigned char*"),
    Global("mouse_width", 0x671F48, 3),
    Global("mouse_length", 0x671F20, 3, "height"),
    Global("mouse_full", 0x671F44, 3, "pitch"),
    Global("mouse_hotx", 0x671F54, 3),
    Global("mouse_hoty", 0x671F50, 3),
    Global("mouse_trans", 0x671F60, 2, "char: transparent colour"),
    Global("mouse_is_hidden", 0x671F18, 3, "bool"),
    # The character: its base stats live in pc_proto (a CritterProto), traits and tags in their modules.
    Global("pc_proto", 0x507530, 3, "CritterProto of the player: base stats at +0x24, 7 SPECIAL first"),
    Global("pc_name", 0x56BEFC, 3, "char[32]"),
    Global("pc_trait", 0x668E60, 3, "int[2], -1 for none"),
    Global("perk_lev", 0x662964, 3, "perk.c: int[64], the player's level in each perk (PERK_* order)"),
    Global("tag_skill", 0x664FF0, 3, "int[4], -1 for none"),
    # The character editor's working state.
    Global("character_points", 0x504F38, 3, "points left to spend in creation"),
    Global("glblmode", 0x56ECC8, 3, "bool: the editor is in creation mode"),
    Global("trait_count", 0x56ED00, 3, "trait slots still free"),
    Global("temp_trait", 0x56ED08, 3, "int[3], the traits chosen so far"),
    Global("tagskill_count", 0x56ED14, 3, "tag slots still free"),
    Global("temp_tag_skill", 0x56ED18, 3, "int[4], the tags chosen so far"),
    # The world map (worldmap.c): the party's position and target in world pixels (1400 x 1500, 50 per section).
    Global("world_xpos", 0x670FCC, 3),
    Global("world_ypos", 0x670FD0, 3),
    # fallout1-ce has these two the other way round; 1.1's click handler stores viewport + mouse - 22 (x) into
    # 0x670FB0 and - 21 (y) into 0x670FAC (0x4AAA5F-0x4AAA80), and live the party walked to (560, 75) while
    # 0x670FAC read 75
    Global("target_xpos", 0x670FB0, 3),
    Global("target_ypos", 0x670FAC, 3),
    Global("our_town", 0x670FC8, 3),
    Global("dropbtn", 0x670FC0, 3, "1 while the party stands (arrived): a click on it enters the place"),
    Global("first_visit_flag", 0x670F60, 3, "bit per town: visited, so its button travels there"),
    Global("wmap_day", 0x670F84, 2),
    # Dialogue (gdialog.c): the reply and the options on screen, and who is being talked to.
    Global("sneak_working", 0x56BF1C, 2, "int: the last Sneak roll's result (critter.c), re-rolled every 60 game s"),
    Global("dialogBlock", 0x58DAC8, 3, "GameDialogBlock: reply text at +0x10, 30 options from +0xA9C"),
    Global("gdNumOptions", 0x505198, 3, "options on screen; keys 1..9 choose"),
    Global("dialog_target", 0x505014, 3, "Object* of the one talked to"),
    Global("gReplyWin", 0x50519C, 3, "dialogue: the reply window; -1 when none"),
    Global("gOptionWin", 0x5051A0, 3, "dialogue: the options window; -1 when none"),
    Global("dialogueBackWindow", 0x50502C, 3, "dialogue: the background window; -1 when none"),
)
GLOBALS = {g.name: g for g in GLOBAL_LIST}
if len(GLOBALS) != len(GLOBAL_LIST):  # a name given twice would silently keep its last entry
    raise ImportError("engine_map.GLOBAL_LIST names a global twice")


class MapHeader:
    """Offsets into `MapHeader` (map_data)."""

    VERSION = 0x00
    NAME = 0x04  # char[16], e.g. "V13ENT.MAP"
    ENTERING_TILE = 0x14
    ENTERING_ELEVATION = 0x18
    ENTERING_ROTATION = 0x1C


# Initial dwords in the exe's data section, a check that the address is a variable of the right kind.
INITIAL_VALUES = {  # as unsigned dwords
    "combat_state": 2,  # COMBAT_STATE_0x02
    "combat_turn_running": 0,
    "map_elevation": 0,
    "game_global_vars": 0,
    "main_window": 0xFFFFFFFF,  # -1
    "main_menu_timeout": 120000,
}

# Small functions whose first bytes must match the 1.1 exe's code (Watcom, register calls), read off its disassembly.
FUNCTIONS = {
    # eax in 0..4: curr_pc_stat[eax], else 0
    "stat_pc_get": (0x49CAD4, bytes.fromhex("85c07c0583f8057c0331c0c38b0485fc516600c3")),
    # push ebx; mov ebx,eax; range check; mov eax,-5; ...
    "stat_pc_set": (0x49CAE8, bytes.fromhex("5389c385c07c0583f8057c07b8fbffffff5bc3")),
    # formats the version as FALLOUT 1.1: push 1; push 1; push offset of the format string
    "getverstr": (0x4A10C0, bytes.fromhex("5289c26a016a016888d44f00")),
    # push ecx; push edx; call cs:[GAME_CLOCK_SLOT]; pop edx; pop ecx; ret (input.cc get_time)
    "get_time": (0x4B3BB8, bytes.fromhex("51522eff15cc016c005a59c3")),
}

# The engine's clock (input.cc get_time, elapsed_time, pause_for_tocks, block_for_tocks, the background tick and the
# key repeat) asks KERNEL32!GetTickCount through this import slot, and nothing else asks through it. The movie player
# and the random seed ask WINMM!timeGetTime (0x6C0164), the sound its multimedia timers, the C runtime its own
# GetTickCount slot (0x6C0358). Measured statically: every code reference to the time imports. f1/clock.py points this slot at a faster clock in test runs.
GAME_CLOCK_SLOT = 0x6C01CC
TIMEGETTIME_SLOT = 0x6C0164  # WINMM!timeGetTime: where winmm.dll lies in the game (f1/clock.py fine_timer)


class Obj:
    """Offsets into `Object` (0x84 bytes). Confirmed live against the engine's own screens (HP, AP, tile and team read back)."""

    ID = 0x00
    TILE = 0x04  # hex index, 200 x 200 per elevation
    X = 0x08
    Y = 0x0C
    SX = 0x10
    SY = 0x14
    FRAME = 0x18
    ROTATION = 0x1C  # 0 NE, 1 E, 2 SE, 3 SW, 4 W, 5 NW
    FID = 0x20  # art id
    FLAGS = 0x24
    ELEVATION = 0x28
    INV_LENGTH = 0x2C
    INV_CAPACITY = 0x30
    INV_ITEMS = 0x34  # {Object* item; int quantity}[]
    CRITTER_REACTION = 0x38
    CRITTER_MANEUVER = 0x3C
    CRITTER_AP = 0x40
    CRITTER_RESULTS = 0x44  # Dam flags (knocked out, crippled, dead, ...)
    CRITTER_DAMAGE_LAST_TURN = 0x48
    CRITTER_AI_PACKET = 0x4C
    CRITTER_TEAM = 0x50
    CRITTER_WHO_HIT_ME = 0x54
    CRITTER_HP = 0x58
    CRITTER_RADIATION = 0x5C
    CRITTER_POISON = 0x60
    ITEM_FLAGS = 0x38  # locked 0x02000000, jammed 0x04000000
    ITEM_AMMO = 0x3C  # a weapon's loaded rounds, an ammo box's rounds (item.c item_w_curr_ammo; live: the pistol's)
    ITEM_AMMO_PID = 0x40  # the ammunition a weapon holds
    DOOR_OPEN_FLAGS = 0x3C  # a door's openFlags after the data flags at +0x38 (protinst.c obj_is_locked)
    DOOR_LOCKED = 0x02000000  # in DOOR_OPEN_FLAGS (the Cathedral's red door, live)
    PID = 0x64  # prototype id, type in the top byte
    CID = 0x68
    LIGHT_DISTANCE = 0x6C
    LIGHT_INTENSITY = 0x70
    OUTLINE = 0x74
    SID = 0x78
    OWNER = 0x7C
    SIZE = 0x84
