"""Check statically that a Fallout 1 executable is the build f1/engine_map.py describes.

Nothing is launched or modified. Evidence per symbol: the build string, initial values of globals in the data
section, how often code references each global's address, and the first bytes of small known functions.

    python -m f1.verify_exe [exe ...]      (default: the Steam install's FALLOUTW.EXE, f1/steam.py)

Exit code 0 when every check holds, 1 otherwise.
"""

import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from f1 import engine_map as em
from f1 import steam


@dataclass(frozen=True)
class Check:
    ok: bool
    text: str


@dataclass(frozen=True)
class Report:
    path: Path
    sha256: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _sections(data: bytes) -> list[tuple[str, int, int, int]]:
    """(name, virtual address, raw size, raw offset) per section; Watcom keeps the size in SizeOfRawData."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
    off = pe + 24 + optsz
    out = []
    for i in range(count):
        name, _, va, rsize, roff = struct.unpack_from("<8sIIII", data, off + 40 * i)
        out.append((name.rstrip(b"\0").decode("latin1"), base + va, rsize, roff))
    return out


def _file_offset(sections: list[tuple[str, int, int, int]], va: int) -> int | None:
    for _, start, size, roff in sections:
        if roff and start <= va < start + size:
            return va - start + roff
    return None


def verify(path: Path) -> Report:
    data = path.read_bytes()
    sections = _sections(data)
    checks = [Check(em.BUILD_STRING in data, f"build string {em.BUILD_STRING.decode()}")]

    for name, expected in em.INITIAL_VALUES.items():
        off = _file_offset(sections, em.GLOBALS[name].address)
        got = struct.unpack_from("<I", data, off)[0] if off is not None else None
        checks.append(Check(got == expected, f"initial {name} = {got} (expected {expected})"))

    begtext = next(s for s in sections if s[0] == "BEGTEXT")
    code = data[begtext[3] : begtext[3] + begtext[2]]
    for g in em.GLOBALS.values():
        refs = code.count(struct.pack("<I", g.address))
        line = f"{g.name:<22} @0x{g.address:06X}: {refs:>4} code refs (>= {g.min_code_refs})"
        checks.append(Check(refs >= g.min_code_refs, line))

    for name, (va, pattern) in em.FUNCTIONS.items():
        off = _file_offset(sections, va)
        ok = off is not None and data[off : off + len(pattern)] == pattern
        checks.append(Check(ok, f"{name} @0x{va:06X} code matches"))

    return Report(path, hashlib.sha256(data).hexdigest(), checks)


def steam_exe() -> Path | None:
    """The Steam install's FALLOUTW.EXE, None when no install is found."""
    try:
        return steam.find().folder / steam.EXE
    except steam.GameNotFound:
        return None


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [p for p in [steam_exe()] if p]
    if not paths:
        print("no Steam install of Fallout found; give the exe's path", file=sys.stderr)
        return 1
    worst = 0
    for path in paths:
        report = verify(path)
        known = "  (the Steam 1.1 build)" if report.sha256 == em.STEAM_EXE_SHA256 else ""
        print(f"### {path}\n  sha256 {report.sha256}{known}")
        for c in report.checks:
            print(f"  [{'ok' if c.ok else 'FAIL'}] {c.text}")
        failed = sum(not c.ok for c in report.checks)
        print(f"  => {'MATCH' if report.ok else f'{failed} of {len(report.checks)} checks failed'}")
        worst = max(worst, 0 if report.ok else 1)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
