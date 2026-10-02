"""Where Steam installed Fallout (app 38400): the bot only reads that folder, to copy it into its instance.

The game folder is, in this order: the environment variable OVERSEER_GAME; the Steam library that holds app 38400's
manifest (Steam's own folder from the registry, then every library its steamapps/libraryfolders.vdf lists).

    python -m f1.steam     print the folder found, or why none was
"""

import os
import re
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path

APP_ID = 38400
EXE = "FALLOUTW.EXE"

# Steam's own folder: per user (SteamPath, forward slashes) and per machine (InstallPath; 32-bit view on 64-bit Windows)
REGISTRY = (
    (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
)


class GameNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class Install:
    folder: Path  # the game's folder, FALLOUTW.EXE in it
    manifest: Path | None  # Steam's appmanifest_38400.acf (its build id), None for a folder given by hand


def steam_roots() -> list[Path]:
    """Steam's own folder(s) as the registry names them, existing ones only, without repeats."""
    roots: list[Path] = []
    for hive, key, value in REGISTRY:
        try:
            with winreg.OpenKey(hive, key) as k:
                path = Path(winreg.QueryValueEx(k, value)[0])
        except OSError:
            continue
        if path.is_dir() and all(path.resolve() != r.resolve() for r in roots):
            roots.append(path)
    return roots


def library_folders(vdf_text: str) -> list[Path]:
    """The libraries a libraryfolders.vdf lists: its "path" values (backslashes escaped as \\\\ in the file)."""
    return [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf_text)]


def install_dir(acf_text: str) -> str | None:
    """An app manifest's "installdir": the game's folder name under steamapps/common."""
    m = re.search(r'"installdir"\s+"([^"]+)"', acf_text)
    return m.group(1) if m else None


def build_id(manifest: Path | None) -> str | None:
    if manifest is None or not manifest.exists():
        return None
    m = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def find(folder: str | Path | None = None) -> Install:
    """The game's install: `folder` when given, else OVERSEER_GAME, else the Steam library that has app 38400."""
    given = folder or os.environ.get("OVERSEER_GAME")
    if given:
        path = Path(given)
        if not (path / EXE).exists():
            raise GameNotFound(f"no {EXE} in {path}")
        manifest = path.parent.parent / f"appmanifest_{APP_ID}.acf"  # steamapps/common/<game> -> steamapps
        return Install(path, manifest if manifest.exists() else None)
    looked: list[str] = []
    for root in steam_roots():
        libraries = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libraries += library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
        for library in libraries:
            manifest = library / "steamapps" / f"appmanifest_{APP_ID}.acf"
            looked.append(str(manifest))
            if not manifest.exists():
                continue
            name = install_dir(manifest.read_text(encoding="utf-8", errors="replace")) or "Fallout"
            path = library / "steamapps" / "common" / name
            if (path / EXE).exists():
                return Install(path, manifest)
    where = "; ".join(looked) if looked else "no Steam in the registry"
    raise GameNotFound(
        f"Fallout (Steam app {APP_ID}) not found ({where}); give its folder with --game or OVERSEER_GAME"
    )


def main(argv: list[str]) -> int:
    try:
        found = find(argv[0] if argv else None)
    except GameNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{found.folder}  (manifest {found.manifest}, build {build_id(found.manifest)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
