"""Print what a Windows PE file says about itself: headers, sections, imports, version strings, SHA-256.

No dependencies. Used to survey the game's binaries.

    python tools/peinfo.py <file.exe|file.dll> [...]
"""

import datetime
import hashlib
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in Counter(data).values())


def sections_of(data: bytes, pe: int, base: int) -> list[dict]:
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    off = pe + 24 + optsz
    out = []
    for i in range(count):
        name, vsize, va, rsize, roff = struct.unpack_from("<8sIIII", data, off + 40 * i)
        chars = struct.unpack_from("<I", data, off + 40 * i + 36)[0]
        name = name.rstrip(b"\0").decode("latin1")
        out.append({"name": name, "vsize": vsize, "va": va, "rsize": rsize, "roff": roff, "chars": chars})
    return out


def rva_to_off(sections: list[dict], rva: int) -> int | None:
    for s in sections:
        # Watcom leaves VirtualSize at 0 and puts the size in SizeOfRawData.
        size = max(s["vsize"], s["rsize"])
        if s["roff"] and s["va"] <= rva < s["va"] + size:
            return rva - s["va"] + s["roff"]
    return None


def cstr(data: bytes, off: int) -> str:
    return data[off : data.index(b"\0", off)].decode("latin1")


def imports(data: bytes, sections: list[dict], imp_rva: int) -> list[tuple[str, list[str]]]:
    out = []
    off = rva_to_off(sections, imp_rva)
    while off is not None:
        oft, _, _, name_rva, ft = struct.unpack_from("<IIIII", data, off)
        if name_rva == 0:
            break
        dll = cstr(data, rva_to_off(sections, name_rva))
        funcs = []
        toff = rva_to_off(sections, oft or ft)
        while toff is not None:
            v = struct.unpack_from("<I", data, toff)[0]
            if v == 0:
                break
            if v & 0x80000000:
                funcs.append(f"#{v & 0xFFFF}")
            else:
                o = rva_to_off(sections, v)
                funcs.append(cstr(data, o + 2) if o is not None else f"?{v:x}")
            toff += 4
        out.append((dll, funcs))
        off += 20
    return out


def version_strings(data: bytes) -> dict[str, str]:
    found = {}
    for key in ("FileVersion", "ProductVersion", "ProductName", "FileDescription", "CompanyName", "OriginalFilename"):
        m = re.search(re.escape(key.encode("utf-16le")) + rb"(?:\0\0)+((?:[^\0]\0)+)", data)
        if m:
            found[key] = m.group(1).decode("utf-16le", "replace")
    return found


def inspect(path: str) -> None:
    data = Path(path).read_bytes()
    print(f"==== {path}")
    print(f"size {len(data)}  sha256 {hashlib.sha256(data).hexdigest()}")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        print("not a PE file")
        return
    machine, _, stamp, _, _, _, chars = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    entry = struct.unpack_from("<I", data, opt + 16)[0]
    base = struct.unpack_from("<I", data, opt + 28)[0]
    dllchars = struct.unpack_from("<H", data, opt + 70)[0]
    ndirs = struct.unpack_from("<I", data, opt + 92)[0]
    dirs = [struct.unpack_from("<II", data, opt + 96 + 8 * i) for i in range(ndirs)]
    when = datetime.datetime.fromtimestamp(stamp, datetime.UTC).isoformat()
    print(f"machine 0x{machine:x}  linked {when}  characteristics 0x{chars:x}")
    print(
        f"image base 0x{base:x}  entry 0x{base + entry:x}  ASLR(DYNAMICBASE) {bool(dllchars & 0x40)}  "
        f"NX {bool(dllchars & 0x100)}"
    )
    sections = sections_of(data, pe, base)
    for s in sections:
        raw = data[s["roff"] : s["roff"] + s["rsize"]] if s["roff"] else b""
        print(
            f"  section {s['name']:<8} va 0x{base + s['va']:08x} vsize 0x{s['vsize']:x} raw 0x{s['rsize']:x}"
            f"{'' if s['roff'] else ' (no file data)'}  entropy {entropy(raw):.2f}"
        )
    names = {s["name"] for s in sections}
    print(f"  SteamStub (.bind section): {'YES' if '.bind' in names else 'no'}")
    if ndirs > 1 and dirs[1][0]:
        for dll, funcs in imports(data, sections, dirs[1][0]):
            print(f"  import {dll}: {', '.join(funcs)}")
    for k, v in version_strings(data).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for p in sys.argv[1:]:
        inspect(p)
        print()
