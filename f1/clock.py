"""A faster game clock for test and development runs. Nothing on disk changes.

In the running game, the one import slot the engine's clock reads (engine_map.GAME_CLOCK_SLOT, KERNEL32!GetTickCount)
is pointed at a stub that returns

    base_game + (now - base_real) * speed

with `now` from WINMM!timeGetTime, which the game is made to keep to 1 ms (`fine_timer`). GetTickCount moves in steps
of 15.6 ms: the engine moves an animation one frame and the game's time one tick per step of its clock at most, and
those steps capped any speed at 64 a second (measured: the game's time at 6.4x for any speed from 8x up).
The stub rebases itself when the speed changes, so the clock never jumps, and it never goes back. Movies, sound and
the random seed keep the real clock: they ask other imports.

Test runs use it in waits only (`set_fast`, `fast`). The agent is fast-forwarded while it waits on the game (a walk,
the foes' turns, an attack's animation, a rest, a ride) and thinks at the game's own speed. The game's timers against
the player keep the time they have at 1x. Vault 13's doors close 3 game seconds after a use (METLDOOR.INT). At a
constant 8x that was 0.4 s, and the route stopped at the library door.

Input goes in at the game's own speed (`held`): the engine times a held key's repeat (500 ms, then every 80 ms,
input.cc) and a held button (250 ms makes a click on an object the action menu, mouse.cc BUTTON_REPEAT_TIME) by this
clock, and a 60 ms click at 10x would be 600 ms long to it.

    python -m f1.clock                   the stub's state: speed, calls, the game's clock
    python -m f1.clock SPEED             install the stub if needed and set a constant speed (1 = the game's own)
    python -m f1.clock fast SPEED        the speed of the waits only (routes --clock SPEED do this)
    python -m f1.clock measure [S]       S seconds (default 5) of the game's clocks against the real one
    python -m f1.clock bench [SPEED ...] the clocks idle and a walk timed at each speed (default 1 2 4 8 16 32 50)
    python -m f1.clock timer             the game's timer at 1 ms (fine_timer): its loop no longer waits 15.6 ms
    python -m f1.clock ride TOWN [SPEED ...]  the world map's pace at each speed against 1x, toward TOWN
"""

import contextlib
import ctypes
import os
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from f1 import engine_map as em
from f1 import paths, win32
from f1.memory import GameMemory, ReadError

ONE = 256  # the speed in fixed point: 256 is 1x
MAX_SPEED = 100  # the stub's arithmetic holds to 255x; nothing above 100x is of use
TAG = b"D19c"
# The stub's data, then its code at CODE. FAST is not the stub's: the speed of `fast` blocks, kept in the game so that
# every process acting on it knows it.
ORIG, LOCK, PENDING, SPEED, BASE_REAL, BASE_GAME, LAST, CALLS, MARK, FAST = range(0, 0x28, 4)
CODE, SIZE = 0x40, 0x100
GET_TIME = em.FUNCTIONS["get_time"]


class ClockError(RuntimeError):
    pass


def stub_code(data: int) -> bytes:
    """32-bit x86 for the stub at data + CODE, called like GetTickCount (stdcall, no arguments, the time in eax;
    ebx kept, ecx and edx free); it asks the function at data + ORIG for the real time. A spin lock keeps two threads
    from rebasing at once."""
    return b"".join(
        (
            b"\x53",  # push ebx
            b"\xbb" + struct.pack("<I", data),  # mov ebx, data
            b"\xb8\x01\x00\x00\x00",  # spin: mov eax, 1
            b"\x87\x43\x04",  # xchg [ebx+LOCK], eax
            b"\x85\xc0",  # test eax, eax
            b"\x74\x04",  # jz locked
            b"\xf3\x90",  # pause
            b"\xeb\xf0",  # jmp spin
            b"\xff\x13",  # locked: call [ebx+ORIG]  (eax = now)
            b"\xff\x43\x1c",  # inc dword [ebx+CALLS]
            b"\x89\xc1",  # mov ecx, eax
            b"\x2b\x43\x10",  # sub eax, [ebx+BASE_REAL]
            b"\xf7\x63\x0c",  # mul dword [ebx+SPEED]  (edx:eax = (now - base_real) * speed)
            b"\x0f\xac\xd0\x08",  # shrd eax, edx, 8  (/ 256)
            b"\x03\x43\x14",  # add eax, [ebx+BASE_GAME]
            b"\x8b\x53\x08",  # mov edx, [ebx+PENDING]
            b"\x3b\x53\x0c",  # cmp edx, [ebx+SPEED]
            b"\x75\x0d",  # jne rebase
            b"\x89\xca",  # mov edx, ecx
            b"\x2b\x53\x10",  # sub edx, [ebx+BASE_REAL]
            b"\x81\xfa\x00\x00\x00\x01",  # cmp edx, 0x1000000  (4.7 h on one base: rebase, the product stays small)
            b"\x72\x0c",  # jb monotonic
            b"\x8b\x53\x08",  # rebase: mov edx, [ebx+PENDING]
            b"\x89\x43\x14",  # mov [ebx+BASE_GAME], eax
            b"\x89\x4b\x10",  # mov [ebx+BASE_REAL], ecx
            b"\x89\x53\x0c",  # mov [ebx+SPEED], edx
            b"\x89\xc2",  # monotonic: mov edx, eax
            b"\x2b\x53\x18",  # sub edx, [ebx+LAST]
            b"\x79\x03",  # jns store
            b"\x8b\x43\x18",  # mov eax, [ebx+LAST]
            b"\x89\x43\x18",  # store: mov [ebx+LAST], eax
            b"\xc7\x43\x04\x00\x00\x00\x00",  # mov dword [ebx+LOCK], 0
            b"\x5b",  # pop ebx
            b"\xc3",  # ret
        )
    )


class Process:
    """The game's process opened for reading and writing its memory."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        access = win32.PROCESS_VM_OPERATION | win32.PROCESS_VM_READ | win32.PROCESS_VM_WRITE
        self.handle = win32.OpenProcess(access | win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self.handle:
            raise ClockError(f"cannot open process {pid} (error {ctypes.get_last_error()})")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        win32.CloseHandle(self.handle)

    def read(self, address: int, size: int) -> bytes:
        buf, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not win32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)):
            raise ClockError(f"read at 0x{address:08X} failed (error {ctypes.get_last_error()})")
        return buf.raw[: got.value]

    def write(self, address: int, data: bytes) -> None:
        got = ctypes.c_size_t()
        if not win32.WriteProcessMemory(self.handle, ctypes.c_void_p(address), data, len(data), ctypes.byref(got)):
            raise ClockError(f"write at 0x{address:08X} failed (error {ctypes.get_last_error()})")

    def u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def put32(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<I", value))


@dataclass(frozen=True)
class Clock:
    stub: int  # the stub's data in the game's memory
    speed: float  # as the stub last used it
    wanted: float  # as last set (the stub takes it at its next call)
    calls: int
    game_ms: int  # the game's clock as the stub last returned it
    fast: float  # the speed of `fast` blocks (0: none set)


def find(mem: "Process | GameMemory") -> int | None:
    """The stub's data, when the game's clock slot points at one; through either reader."""
    data = mem.u32(em.GAME_CLOCK_SLOT) - CODE
    try:
        return data if mem.read(data + MARK, 4) == TAG else None
    except (ClockError, ReadError):
        return None


def speed_of(mem: "Process | GameMemory") -> float:
    """The speed the game's clock runs at: 1.0 without a stub. For whoever reads the game and times what it sees by
    it (the watchdog, the float watch, a recorder hook)."""
    try:
        data = find(mem)
        return mem.u32(data + SPEED) / ONE if data is not None else 1.0
    except (ClockError, ReadError):
        return 1.0


def read_clock(proc: Process, data: int) -> Clock:
    _orig, _lock, wanted, speed, _base_real, _base_game, last, calls, _mark, fast = struct.unpack(
        "<8I4sI", proc.read(data, 40)
    )
    return Clock(data, speed / ONE, wanted / ONE, calls, last, fast / ONE)


def install(proc: Process) -> int:
    """The stub in the game at 1x, the clock slot pointed at it; the one already there if there is one."""
    if (data := find(proc)) is not None:
        return data
    va, pattern = GET_TIME
    if proc.read(va, len(pattern)) != pattern:
        raise ClockError("get_time is not the 1.1 code in memory; the stub stays out")
    fine_timer(proc.pid)  # timeGetTime to 1 ms in the game, and its loop's sleeps with it
    real = proc.u32(em.TIMEGETTIME_SLOT)  # the stub's clock: the game's own timeGetTime
    data = win32.VirtualAllocEx(proc.handle, None, SIZE, win32.MEM_COMMIT_RESERVE, win32.PAGE_EXECUTE_READWRITE)
    if not data:
        raise ClockError(f"no memory in the game for the stub (error {ctypes.get_last_error()})")
    # Both counts are the system's, the same in every process. The game's clock goes on from GetTickCount one step
    # ahead (it may have seen the next step since), the stub counts from timeGetTime.
    now, game = win32.timeGetTime(), win32.GetTickCount() + 16
    head = struct.pack("<8I", real, 0, ONE, ONE, now, game, game, 0) + TAG
    proc.write(data, head.ljust(CODE, b"\0") + stub_code(data))
    win32.FlushInstructionCache(proc.handle, ctypes.c_void_p(data), SIZE)
    old = ctypes.wintypes.DWORD()
    slot = ctypes.c_void_p(em.GAME_CLOCK_SLOT)
    if not win32.VirtualProtectEx(proc.handle, slot, 4, win32.PAGE_READWRITE, ctypes.byref(old)):
        raise ClockError(f"the clock slot stays read-only (error {ctypes.get_last_error()})")
    proc.put32(em.GAME_CLOCK_SLOT, data + CODE)  # one aligned write: every call after it goes to the stub
    win32.VirtualProtectEx(proc.handle, slot, 4, old.value, ctypes.byref(ctypes.wintypes.DWORD()))
    return data


def set_speed(pid: int, speed: float) -> Clock:
    if not 0 < speed <= MAX_SPEED:
        raise ClockError(f"speed {speed} is outside (0, {MAX_SPEED}]")
    with Process(pid) as proc:
        data = install(proc)
        proc.put32(data + PENDING, round(speed * ONE))
        deadline = time.monotonic() + 1
        while (clock := read_clock(proc, data)).speed != clock.wanted and time.monotonic() < deadline:
            time.sleep(0.005)
        return clock


def set_fast(pid: int, speed: float) -> Clock:
    """Test runs: `fast` blocks run the game at `speed`, everything else at the game's own."""
    if not 1 <= speed <= MAX_SPEED:
        raise ClockError(f"speed {speed} is outside [1, {MAX_SPEED}]")
    with Process(pid) as proc:
        data = install(proc)
        proc.put32(data + FAST, round(speed * ONE))
        proc.put32(data + PENDING, ONE)
        return read_clock(proc, data)


def reset(pid: int) -> Clock | None:
    """The game's own speed everywhere, waits included, when a stub runs (a test run killed before its end left
    its speed in the game: the next run, meant for 1x, would play fast). None when no stub runs."""
    with Process(pid) as proc:
        data = find(proc)
        if data is None:
            return None
        proc.put32(data + FAST, ONE)
        proc.put32(data + PENDING, ONE)
        return read_clock(proc, data)


def status(pid: int) -> Clock | None:
    with Process(pid) as proc:
        data = find(proc)
        return read_clock(proc, data) if data is not None else None


def export_rva(dll: Path, name: str) -> int:
    """Where a DLL on disk exports `name` (not forwarded), relative to its base."""
    data = dll.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt, count, opt_size = pe + 24, *struct.unpack_from("<H", data, pe + 6), struct.unpack_from("<H", data, pe + 20)[0]
    sections = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(count)]

    def offset(rva: int) -> int:
        return next(rva - va + raw for size, va, raw_size, raw in sections if va <= rva < va + max(size, raw_size))

    exports = offset(struct.unpack_from("<I", data, opt + 96)[0])  # PE32: the export directory
    names, functions, names_at, ordinals_at = struct.unpack_from("<4I", data, exports + 24)[0:1] + struct.unpack_from(
        "<3I", data, exports + 28
    )
    for i in range(names):
        at = offset(struct.unpack_from("<I", data, offset(names_at) + 4 * i)[0])
        if data[at : at + len(name) + 1] == name.encode() + b"\0":
            ordinal = struct.unpack_from("<H", data, offset(ordinals_at) + 2 * i)[0]
            return struct.unpack_from("<I", data, offset(functions) + 4 * ordinal)[0]
    raise ClockError(f"{dll.name} exports no {name}")


def fine_timer(pid: int) -> int:
    """timeBeginPeriod(1) called in the game, on a thread made for the call. The HRP's CPU_USAGE_FIX sleeps once a
    loop for a tick of the system timer: 15.6 ms unless the process asked for finer, which caps the loop near 64 a
    second, and the engine moves an animation one frame and the game's time one tick a loop at most. The request holds
    until the game exits. Returns timeBeginPeriod's result (0: done)."""
    winmm = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "winmm.dll"
    access = win32.PROCESS_CREATE_THREAD | win32.PROCESS_QUERY_INFORMATION | win32.PROCESS_VM_OPERATION
    handle = win32.OpenProcess(access | win32.PROCESS_VM_WRITE | win32.PROCESS_VM_READ, False, pid)
    if not handle:
        raise ClockError(f"cannot open process {pid} to start a thread (error {ctypes.get_last_error()})")
    try:
        with Process(pid) as proc:
            base = proc.u32(em.TIMEGETTIME_SLOT) - export_rva(winmm, "timeGetTime")
            if base & 0xFFFF or proc.read(base, 2) != b"MZ":
                raise ClockError(f"winmm.dll is not where timeGetTime says (0x{base:08X})")
        start = base + export_rva(winmm, "timeBeginPeriod")
        thread = win32.CreateRemoteThread(handle, None, 0, ctypes.c_void_p(start), ctypes.c_void_p(1), 0, None)
        if not thread:
            raise ClockError(f"no thread in the game (error {ctypes.get_last_error()})")
        try:
            if win32.WaitForSingleObject(thread, 5000) != 0:
                raise ClockError("timeBeginPeriod did not return in 5 s")
            code = ctypes.wintypes.DWORD()
            win32.GetExitCodeThread(thread, ctypes.byref(code))
            return code.value
        finally:
            win32.CloseHandle(thread)
    finally:
        win32.CloseHandle(handle)


@contextlib.contextmanager
def fast(pid: int):
    """The waits' speed (set_fast) while the agent waits on the game; the game's own after. Nothing when no speed
    is set, when no stub runs, or inside another fast block."""
    try:
        proc = Process(pid)
    except ClockError:
        yield
        return
    with proc:
        try:
            data = find(proc)
            speed = proc.u32(data + FAST) if data is not None else 0
            idle = data is not None and proc.u32(data + PENDING) == ONE
        except ClockError:
            data, speed, idle = None, 0, False
        if data is None or speed <= ONE or not idle:
            yield
            return
        proc.put32(data + PENDING, speed)
        try:
            yield
        finally:
            with contextlib.suppress(ClockError):  # the game may have closed meanwhile
                proc.put32(data + PENDING, ONE)


@contextlib.contextmanager
def held(pid: int):
    """The game's own speed while input goes in, the set speed again after. Nothing when no stub runs or the game
    is gone. Nested holds keep the outer one's speed."""
    try:
        proc = Process(pid)
    except ClockError:
        yield
        return
    with proc:
        try:
            data = find(proc)
            wanted = proc.u32(data + PENDING) if data is not None else ONE
        except ClockError:
            data, wanted = None, ONE
        if data is None or wanted == ONE:
            yield
            return
        proc.put32(data + PENDING, ONE)
        deadline = time.monotonic() + 0.1  # the game asks for the time many times a frame: the rebase is at once
        while proc.u32(data + SPEED) != ONE and time.monotonic() < deadline:
            time.sleep(0.001)
        try:
            yield
        finally:
            with contextlib.suppress(ClockError):  # the game may have closed meanwhile
                proc.put32(data + PENDING, wanted)


@dataclass(frozen=True)
class Sample:
    seconds: float  # real
    clock: float  # the engine's clock against the real one (the set speed, unless the loop falls behind)
    game_time: float  # the game's time (fallout_game_time, 0.1 s a tick) against the real one
    calls: float  # the stub's calls a real second


def measure(pid: int, seconds: float = 5.0) -> Sample:
    with Process(pid) as proc, GameMemory(pid) as mem:
        data = find(proc)
        if data is None:
            raise ClockError("no stub in the game (python -m f1.clock 1 installs it)")
        a, ta, t0 = read_clock(proc, data), mem.glob("fallout_game_time"), time.perf_counter()
        time.sleep(seconds)
        b, tb, t1 = read_clock(proc, data), mem.glob("fallout_game_time"), time.perf_counter()
    real_ms = (t1 - t0) * 1000
    return Sample(
        round(t1 - t0, 2),
        round(((b.game_ms - a.game_ms) & 0xFFFFFFFF) / real_ms, 2),
        round((tb - ta) * 100 / real_ms, 2),
        round((b.calls - a.calls) * 1000 / real_ms),
    )


def walk_bench(pid: int, speeds: list[float], legs: int = 4, reach: int = 8) -> list[dict]:
    """At each speed: the clocks idle for 3 s, then `legs` walks between the player's tile and one `reach` hexes
    away, each timed from the click to the arrival (the player's tile read every 10 ms). Events in runs/."""
    import datetime

    from f1 import actions, geometry, nav
    from f1.telemetry import EventLog

    log = EventLog(paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-clockbench" / "events.jsonl")
    actor = actions.Actor(pid, log)
    try:
        set_speed(pid, 1)
        s = actor.snap()
        home = s.dude.tile
        blocked = nav.obstacles(actor.mem, s.elevation, s.dude.address).blocked
        far = None
        for t in geometry.ring(home, reach):  # a straight way: a path of `reach` hexes, so the legs compare
            path = nav.astar(home, {t}, blocked) if t not in blocked else None
            if path and len(path) <= reach + 1 and geometry.on_view(t, s.camera, 60) and actor.walk_to(t, 20).ok:
                far = t
                break
        if far is None or not actor.walk_to(home, 20).ok:
            raise ClockError(f"no tile {reach} hexes from {home} to walk to and back")
        log.emit("bench_start", home=home, far=far, map=s.map_name, speeds=speeds, legs=legs)
        rows = []
        for speed in speeds:
            set_speed(pid, speed)
            idle = measure(pid, 3.0)
            times, game_ticks = [], []
            for leg in range(legs):
                target = far if leg % 2 == 0 else home
                cam = actor.snap().camera
                if not actor.point(*geometry.tile_center(target, cam)):
                    raise ClockError("the cursor did not land")
                g0 = actor.mem.glob("fallout_game_time")
                actor.click()
                t0 = time.perf_counter()
                while actor.snap().dude.tile != target and time.perf_counter() - t0 < 30:
                    time.sleep(0.01)
                times.append(round(time.perf_counter() - t0, 3))
                game_ticks.append(actor.mem.glob("fallout_game_time") - g0)
                time.sleep(0.3)
            walk = sum(times) / len(times)
            row = {"speed": speed, "idle": idle.__dict__, "walk_s": times, "walk_mean_s": round(walk, 3)}
            row |= {"hexes_per_s": round(reach / walk, 1), "game_s_per_walk": [t / 10 for t in game_ticks]}
            log.emit("bench_speed", **row)
            rows.append(row)
            print(row)
        set_speed(pid, 1)
        return rows
    finally:
        actor.close()


def ride_bench(pid: int, town: str, speeds: list[float], pairs: int = 4, burst_s: float = 0.5) -> list[dict]:
    """The party's pace on the world map at each speed against 1x on the same stretch: bursts of `burst_s` at 1x and
    at the speed in turns while it walks toward `town` (the terrain changes the pace: a mountain square takes two
    frames a step, worldmap.c), `pairs` of them a speed; a burst that ends with the party standing is not counted.
    From the world map; stops at a map (an encounter). Events in runs/."""
    import datetime

    from f1 import state, worldmap
    from f1.telemetry import EventLog

    run = paths.RUNS / f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-ridebench"
    log = EventLog(run / "events.jsonl")
    tx, ty = worldmap.town_xy(town)
    rows = []
    with Process(pid) as proc, GameMemory(pid) as mem:
        install(proc)

        def burst(speed: float) -> float | None:
            """Game hours a real second over one burst; None when the party stood or the screen changed."""
            if mem.glob("dropbtn") == 1:
                set_speed(pid, 1)
                if worldmap.town_map_buttons(mem):
                    worldmap.to_world_map(mem)
                worldmap.aim(mem, tx, ty)
            set_speed(pid, speed)
            g0, t0 = mem.glob("fallout_game_time"), time.perf_counter()
            time.sleep(burst_s)
            g1, t1 = mem.glob("fallout_game_time"), time.perf_counter()
            if mem.glob("dropbtn") == 1 or state.detect_screen(mem) != "worldmap":
                return None
            return (g1 - g0) / 36000 / (t1 - t0)

        def record(speed: float, base: list[float], fast_: list[float], stopped: str = "") -> None:
            row = {"speed": speed, "pairs": len(fast_)}
            if fast_:
                row |= {
                    "hours_per_s_1x": round(sum(base) / len(base), 2),
                    "hours_per_s": round(sum(fast_) / len(fast_), 2),
                    "ratio": round(sum(fast_) / sum(base), 2),
                }
            if stopped:
                row["stopped"] = stopped
            log.emit("ride_bench", **row)
            rows.append(row)
            print(row, flush=True)

        try:
            for speed in speeds:
                base, fast_ = [], []
                for _ in range(pairs * 3):
                    if len(fast_) >= pairs:
                        break
                    a, b = burst(1), burst(speed)
                    if a and b:
                        base.append(a)
                        fast_.append(b)
                    if (screen := state.detect_screen(mem)) != "worldmap":  # a town reached, or an encounter
                        record(speed, base, fast_, f"left the world map ({screen})")
                        return rows
                record(speed, base, fast_)
        finally:
            set_speed(pid, 1)
    return rows


def main(argv: list[str]) -> int:
    from f1 import session

    pid = session.game_pid()
    if not pid:
        print("the game is not running", file=sys.stderr)
        return 1
    try:
        if not argv:
            print(status(pid) or "no stub: the game's own clock")
        elif argv[0] == "measure":
            print(measure(pid, float(argv[1]) if len(argv) > 1 else 5.0))
        elif argv[0] == "fast":
            print(set_fast(pid, float(argv[1])))
        elif argv[0] == "timer":
            print(f"timeBeginPeriod(1) in the game: {fine_timer(pid)} (0 is done)")
        elif argv[0] == "ride":
            ride_bench(pid, argv[1], [float(a) for a in argv[2:]] or [2, 4, 8, 16, 32, 50])
        elif argv[0] == "bench":
            walk_bench(pid, [float(a) for a in argv[1:]] or [1, 2, 4, 8, 16, 32, 50])
        else:
            print(set_speed(pid, float(argv[0])))
    except ClockError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
