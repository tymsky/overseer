# Fallout 1: the game's file formats, as read by f1/dat.py and f1/knowledge.py

> What each reader relies on, and how it was checked. Sources: the engine's db.c, assoc.c and lzss.c as fallout1-ce
> documents them, then the real files of the Steam install.

## DAT1 archives (`MASTER.DAT`, `CRITTER.DAT`)

- **Big-endian** throughout.
- The directory is an *assoc* array: four longs (count, max, data size, a pointer written as garbage), then per entry
  a length byte, the name, and `data size` bytes of data.
- The root assoc lists directory names (data size 0). `.` is the top level.
- Each directory follows as an assoc of file names with 16-byte entries: `flags`, `offset`, `length` (unpacked),
  `packed` (packed length).
- `flags & 0xF0`: `0x10` (or flags 0) means the whole file is LZSS (`packed` bytes in); `0x20` means stored;
  `0x40` means chunks, each a big-endian short: top bit set means a stored chunk of that many bytes, clear means an
  LZSS chunk of that many packed bytes. `MASTER.DAT` uses only `0x40` (14997 files) and `0x20` (4787).
- LZSS: a flag byte per 8 items, least significant bit first. A set bit is a literal byte. A clear bit is two bytes,
  `low`, `high`: offset `low | (high & 0xF0) << 4` into a 4096-byte ring, length `(high & 0x0F) + 3`. The ring starts
  filled with spaces, and writing starts at 4078.
- Names are upper case with backslashes. 19784 files in `MASTER.DAT` (SOUND 7259, ART 4997, PROTO 4332, TEXT 2098,
  SCRIPTS 953, MAPS 114, ...), 5459 in `CRITTER.DAT`.
- **The engine's lookup order:** loose files under the game's `DATA\` first (`master_patches=data`), then the DATs.
  `f1.dat.GameFiles` does the same.

Checked: `TEXT\ENGLISH\GAME\PRO_ITEM.MSG` decodes to clean text (`{100}{}{Leather Armor}` ...), and 4306 prototype
files decode to headers whose pid matches their list position.

## Messages (`*.MSG`)

Latin-1 text with CRLF line ends, entries `{number}{sound}{text}`. The text may span lines and has no `}` inside.

## Prototypes (`PROTO\<TYPE>\*.PRO`, `<TYPE>.LST`)

- Types by `pid >> 24`: 0 items, 1 critters, 2 scenery, 3 walls, 4 tiles, 5 misc (folders ITEMS, CRITTERS, SCENERY,
  WALLS, TILES, MISC).
- **The file name is not the pid.** Line N (1-based) of `<TYPE>.LST` names the file of pid index N. For example, pid
  8 (10mm Pistol) lives in `00000004.PRO`, the list's eighth line.
- A `.PRO` starts with big-endian `pid` and `message number`. The name is that number in `PRO_<ITEM|CRIT|SCEN|WALL|
  TILE|MISC>.MSG`, and the description is the number plus one. For every file read, the message number was index × 100.
- 4306 prototypes: 242 items, 312 critters, 908 scenery, 1176 walls, 1622 tiles, 46 misc. The player's pid
  0x01000000 (index 0) has no list line.
- **Items** (big-endian ints): pid, message, fid, light distance, light intensity, flags, extended flags (+24), script,
  item type (+32: 0 armor, 1 container, 2 drug, 3 weapon, 4 ammo, 5 misc, 6 key), material, size, weight (+44), cost
  (+48), inventory fid, one sound byte (+56); the type's data starts at +57. File sizes by type: weapon 122 (43
  files), ammo 81 (16), armor 129, drug 125, container 65, misc 69, key 61.
- **Weapon data** (+57, 16 ints and a sound byte): animation, min and max damage, damage type (0 normal, 1 laser, 2
  fire, 3 plasma, 4 electrical, 5 EMP, 6 explosion), range of the primary and the secondary attack, projectile pid,
  minimum ST, AP of the primary and the secondary attack, critical failure table, perk, burst rounds, caliber, the
  default ammunition's pid, capacity. The extended flags hold the attack modes (primary in bits 0-3, secondary in
  4-7: 1 punch, 2 kick, 3 swing, 4 thrust, 5 throw, 6 single, 7 burst, 8 continuous), 0x100 big gun, 0x200
  two-handed. **Ammo data** (+57): caliber, rounds per box, AC and DR modifiers, damage multiplier and divisor.
- Checked: the 10mm Pistol reads 5-12 damage, range 25, AP 5 (the interface bar showed "AP 5", and every shot took
  5 AP live), caliber 8 like 10mm JHP and 10mm AP (24 a box); the Hunting Rifle 8-20, range 40, AP 5 (5 AP a shot
  live), caliber 5 like .223 FMJ (50 a box); every weapon's caliber matches the ammunition of that name. Its perk 58 is
  PERK_WEAPON_LONG_RANGE in CE's perk list.

## Global variables (`DATA\VAULT13.GAM`)

- Text. After `GAME_GLOBAL_VARS:` comes one `NAME :=value;` per GVAR, in GVAR order, with `//` comments.
- Two lines lack the `;` (`BAD_MONSTER`, `MARK_SHADY_1`) and still count. With them there are 618 GVARs, as many as
  `num_game_global_vars` holds in the running game.
- Every comment that carries a number carries the GVAR's own index (618 of 618 checked). At the start of a new game,
  all 618 live values equal these initial values.

## Scripts (`SCRIPTS\*.INT`, compiled SSL)

- 952 files in `MASTER.DAT`. Read by `f1/intdump.py` (`python -m f1.intdump extract`, then `NAME [PROC ...]` or
  `grep PATTERN`); the layout and the opcodes are fallout1-ce's interpreter (`src/int/intrpret.cc`).
- **Big-endian.** 42 bytes of header, then the procedure table: a count and 24-byte records (name, flags, time,
  condition, body offset, argument count). Then the identifiers (a length, then names; a record's name offset counts
  from the length field) and the static strings (a length, then strings at +4 + offset). Record 0 repeats `start`;
  flag 0x04 marks an imported procedure (no body in this file).
- **Code.** 16-bit opcodes with the top bit set; `opcode & 0x3FF` picks the handler. A push (index 1) carries its
  type in the high bits (0xC001 int, 0xA001 float, 0x9001 static string) and a 32-bit value. `if` pops the condition
  and then the address to go to when it is false; a call to the script's own procedure pops its index, the argument
  count and the arguments (the return address goes aside with `d_to_a`). The game's functions (0x80A1 on) pop the
  arguments `f1/intops.py` lists (263, counted from CE's handlers).
- **Messages.** A dialogue function's message list id N is `SCRIPTS.LST` line N - 1 (scripts.c
  scr_get_dialog_msg_file), its texts in `TEXT\ENGLISH\DIALOG\<script>.MSG`.
- Checked: MASTER.INT reads back into its 80 procedures with every `giq_option` naming a line of MASTER.MSG that fits
  the node (the sterility line is 133, the Speech rolls sit between the nodes); one of 952 files (NHUP2DN3) does not
  decode yet.

## Maps (`MAPS\*.MAP`)

- Read by `f1/mapfile.py` (`python -m f1.mapfile NAME [WORD ...]`); the layout is fallout1-ce's loaders (map.cc
  map_load_file, scripts.cc scr_load, object.cc obj_load_func, proto.cc proto_read_protoUpdateData). Big-endian.
- A 236-byte header: version 19, the name (16 bytes), entering tile, elevation, rotation, the local variable count,
  the map script (numbered from 1: MBENT's 443 is `MBEnt.int`, SCRIPTS.LST line 442), flags, darkness, the global
  variable count, two more ints and 44 spare. Then the map's global and local variables, then 100 x 100 squares
  (ints) for each elevation present (flags 2, 4, 8 mark elevations 0, 1, 2 absent).
- **Scripts**: five lists (system, spatial, timed, item, critter): a count, then extents of 16 records, each sid,
  next, two ints more for a spatial script (tile with the elevation in its top bits, radius) or one for a timed one,
  then 14 ints whose second is the SCRIPTS.LST index (from 0); an extent ends with its length and a link.
- **Objects**: a total, then per elevation a count and the objects: 18 ints (id, tile, x, y, sx, sy, frame, rotation,
  fid, flags, elevation, pid, cid, light distance and intensity, an unused one, sid, script index), an inventory head
  (length, capacity, a stale pointer), then a critter's 11 ints (reaction, damage last turn, maneuver, AP, results,
  AI packet, team, who hit it, HP, radiation, poison) or anyone else's flags and, by the prototype: a weapon's rounds
  and ammunition pid, an ammo box's rounds, a misc item's charges, a key's code, a door's flags, stairs' map and
  tile, an elevator's type and level, a ladder's tile, an exit grid's (pids 0x5000010-17) map, tile, elevation and
  rotation. Then the inventory: per entry a count and an object in the same form. A scenery prototype's kind is its
  int at +32 (0 door 42, 1 stairs 10, 2 elevator 1, 3 ladder up 1, 4 ladder down 4, 5 generic 850).
- Checked: MBENT reads to its end with the door `mbout2in` and the guards `vgatemut`, `vdoormut`, `vfencemt` (as the
  scripts name them), exit grids to the town map (-2); V13ENT has the spatial scripts `cave2v13`, `valtleav`,
  `ratpit`; SHADYW puts Seth (script `seth`) on tile 14108, where he stood live, and JUNKCSNO has Gizmo and
  Izo. Not yet understood: JUNKKILL's Vinnie reads script index 0 (`obj_dude`).
