"""Thin ctypes bindings to the Windows calls the agent needs: processes, windows, dialogs, capture, input.

Only what is used. Handles are pointer-sized, so every function gets explicit argtypes and restype. The process is
made per-monitor DPI aware on import, so window rectangles and cursor positions are physical pixels.
"""

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _fn(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object):
    f = getattr(dll, name)
    f.restype = restype
    f.argtypes = argtypes
    return f


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


EnumWindows = _fn(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
EnumChildWindows = _fn(user32, "EnumChildWindows", wintypes.BOOL, wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
GetWindowThreadProcessId = _fn(
    user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
)
GetWindowTextW = _fn(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
GetClassNameW = _fn(user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
IsWindowVisible = _fn(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
IsWindow = _fn(user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
GetClientRect = _fn(user32, "GetClientRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
GetWindowRect = _fn(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
ClientToScreen = _fn(user32, "ClientToScreen", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
GetForegroundWindow = _fn(user32, "GetForegroundWindow", wintypes.HWND)
SetForegroundWindow = _fn(user32, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
GetDlgCtrlID = _fn(user32, "GetDlgCtrlID", ctypes.c_int, wintypes.HWND)
SendMessageW = _fn(user32, "SendMessageW", LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
PostMessageW = _fn(
    user32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
GetLastInputInfo = _fn(user32, "GetLastInputInfo", wintypes.BOOL, ctypes.POINTER(LASTINPUTINFO))
GetTickCount = _fn(kernel32, "GetTickCount", wintypes.DWORD)
timeGetTime = _fn(winmm, "timeGetTime", wintypes.DWORD)
SetCursorPos = _fn(user32, "SetCursorPos", wintypes.BOOL, ctypes.c_int, ctypes.c_int)
GetCursorPos = _fn(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
SendInput = _fn(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
MapVirtualKeyW = _fn(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
GetDC = _fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
ReleaseDC = _fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
CreateCompatibleBitmap = _fn(
    gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int
)
SelectObject = _fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
BitBlt = _fn(
    gdi32,
    "BitBlt",
    wintypes.BOOL,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
)
GetDIBits = _fn(
    gdi32,
    "GetDIBits",
    ctypes.c_int,
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER),
    wintypes.UINT,
)
DeleteObject = _fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
DeleteDC = _fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
OpenProcess = _fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
ReadProcessMemory = _fn(
    kernel32,
    "ReadProcessMemory",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
WriteProcessMemory = _fn(
    kernel32,
    "WriteProcessMemory",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
VirtualAllocEx = _fn(
    kernel32, "VirtualAllocEx", wintypes.LPVOID, wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD,
    wintypes.DWORD,
)  # fmt: skip
VirtualProtectEx = _fn(
    kernel32, "VirtualProtectEx", wintypes.BOOL, wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD,
    wintypes.PDWORD,
)  # fmt: skip
FlushInstructionCache = _fn(
    kernel32, "FlushInstructionCache", wintypes.BOOL, wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_size_t
)
CreateRemoteThread = _fn(
    kernel32, "CreateRemoteThread", wintypes.HANDLE, wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t,
    wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD,
)  # fmt: skip
WaitForSingleObject = _fn(kernel32, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
GetExitCodeThread = _fn(kernel32, "GetExitCodeThread", wintypes.BOOL, wintypes.HANDLE, wintypes.LPDWORD)
OpenProcessToken = _fn(advapi32, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.PHANDLE)
GetTokenInformation = _fn(
    advapi32,
    "GetTokenInformation",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.PDWORD,
)

K32EnumProcesses = _fn(kernel32, "K32EnumProcesses", wintypes.BOOL, wintypes.PDWORD, wintypes.DWORD, wintypes.PDWORD)
QueryFullProcessImageNameW = _fn(
    kernel32,
    "QueryFullProcessImageNameW",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.PDWORD,
)
GetExitCodeProcess = _fn(kernel32, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.PDWORD)
TerminateProcess = _fn(kernel32, "TerminateProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2; fails harmlessly if already set

WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
BN_CLICKED = 0
SRCCOPY = 0x00CC0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
PROCESS_VM_OPERATION, PROCESS_VM_WRITE = 0x0008, 0x0020
PROCESS_CREATE_THREAD, PROCESS_QUERY_INFORMATION = 0x0002, 0x0400
MEM_COMMIT_RESERVE, PAGE_EXECUTE_READWRITE, PAGE_READWRITE = 0x3000, 0x40, 0x04
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


# --- windows -----------------------------------------------------------------------------------------------------


def _text(getter, hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    getter(hwnd, buf, 512)
    return buf.value


def window_text(hwnd: int) -> str:
    return _text(GetWindowTextW, hwnd)


def window_class(hwnd: int) -> str:
    return _text(GetClassNameW, hwnd)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_windows(pid: int, visible_only: bool = True) -> list[int]:
    """Top-level windows of a process, in z-order."""
    found: list[int] = []

    def cb(hwnd: int, _lparam: int) -> bool:
        if window_pid(hwnd) == pid and (not visible_only or IsWindowVisible(hwnd)):
            found.append(hwnd)
        return True

    EnumWindows(WNDENUMPROC(cb), 0)
    return found


def child_windows(hwnd: int) -> list[int]:
    found: list[int] = []

    def cb(child: int, _lparam: int) -> bool:
        found.append(child)
        return True

    EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return found


def client_rect(hwnd: int) -> Rect:
    """The client area in screen coordinates."""
    r = wintypes.RECT()
    GetClientRect(hwnd, ctypes.byref(r))
    origin = wintypes.POINT(0, 0)
    ClientToScreen(hwnd, ctypes.byref(origin))
    return Rect(origin.x, origin.y, r.right - r.left, r.bottom - r.top)


def work_area() -> Rect:
    """The primary monitor's desktop without the taskbar, in physical pixels (SPI_GETWORKAREA)."""
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def system_dpi() -> int:
    """The display scaling as DPI: 96 is 100 %, 144 is 150 %."""
    return user32.GetDpiForSystem()


def foreground_pid() -> int:
    hwnd = GetForegroundWindow()
    return window_pid(hwnd) if hwnd else 0


def press_dialog_button(dialog: int, button: int) -> None:
    """Press a dialog's button the way a click would, without mouse input (WM_COMMAND with its control id)."""
    ctrl_id = GetDlgCtrlID(button)
    SendMessageW(dialog, WM_COMMAND, (BN_CLICKED << 16) | (ctrl_id & 0xFFFF), button)


def close_window(hwnd: int) -> None:
    PostMessageW(hwnd, WM_CLOSE, 0, 0)


# --- processes ---------------------------------------------------------------------------------------------------


def all_pids() -> list[int]:
    arr = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    K32EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed))
    return [pid for pid in arr[: needed.value // 4] if pid]


def process_image(pid: int) -> str | None:
    """The full path of a process's exe, or None if it cannot be asked."""
    proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proc:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        return buf.value if QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)) else None
    finally:
        CloseHandle(proc)


def pids_of(exe_path: str) -> list[int]:
    """Running processes started from exactly this exe (case-insensitive path match)."""
    want = exe_path.lower()
    return [pid for pid in all_pids() if (img := process_image(pid)) and img.lower() == want]


def is_alive(pid: int) -> bool:
    proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proc:
        return False
    try:
        code = wintypes.DWORD()
        return bool(GetExitCodeProcess(proc, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        CloseHandle(proc)


def kill(pid: int) -> bool:
    proc = OpenProcess(PROCESS_TERMINATE, False, pid)
    if not proc:
        return False
    try:
        return bool(TerminateProcess(proc, 1))
    finally:
        CloseHandle(proc)


def is_elevated(pid: int) -> bool | None:
    """Whether a process runs with an elevated token; None when it cannot be asked (which itself suggests yes)."""
    proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proc:
        return None
    try:
        token = wintypes.HANDLE()
        if not OpenProcessToken(proc, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevated = wintypes.DWORD()
            size = wintypes.DWORD()
            ok = GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(elevated), 4, ctypes.byref(size))
            return bool(elevated.value) if ok else None
        finally:
            CloseHandle(token)
    finally:
        CloseHandle(proc)


# --- time and input ----------------------------------------------------------------------------------------------


def tick() -> int:
    """Milliseconds since boot, as GetLastInputInfo counts them (wraps at 2**32)."""
    return GetTickCount()


def last_input_tick() -> int:
    """When the last mouse or keyboard input reached this desktop, anyone's (ours too)."""
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    GetLastInputInfo(ctypes.byref(li))
    return li.dwTime


def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(ctypes.get_last_error(), "SendInput was blocked")


def mouse_button(down: bool, right: bool = False) -> None:
    flag = (
        (MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP)
        if right
        else (MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
    )
    _send(INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(0, 0, 0, flag, 0, 0))))


def key(scan: int, down: bool, extended: bool = False) -> None:
    """A key by its scan code (what DirectInput reads)."""
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, scan, flags, 0, 0))))


def scan_code(vk: int) -> int:
    return MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC


# --- capture -----------------------------------------------------------------------------------------------------


def _bitmap_to_image(hdc: int, bmp: int, width: int, height: int) -> Image.Image:
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(width * height * 4)
    GetDIBits(hdc, bmp, 0, height, buf, ctypes.byref(bmi), 0)
    return Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1)


def capture_screen(rect: Rect) -> Image.Image:
    """What GDI sees on the screen in a rectangle: desktop context only (games under DirectX may show white;
    f1/capture.py takes game frames)."""
    sdc = GetDC(0)
    mdc = CreateCompatibleDC(sdc)
    bmp = CreateCompatibleBitmap(sdc, rect.width, rect.height)
    old = SelectObject(mdc, bmp)
    try:
        BitBlt(mdc, 0, 0, rect.width, rect.height, sdc, rect.left, rect.top, SRCCOPY)
        return _bitmap_to_image(mdc, bmp, rect.width, rect.height)
    finally:
        SelectObject(mdc, old)
        DeleteObject(bmp)
        DeleteDC(mdc)
        ReleaseDC(0, sdc)


def wait_for(predicate, timeout_s: float, interval_s: float = 0.1) -> bool:
    """Poll until predicate() is true or the timeout passes."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval_s)
    return bool(predicate())
