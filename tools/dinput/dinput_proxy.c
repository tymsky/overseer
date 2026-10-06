/* A DINPUT.DLL for the Fallout engine (F1 1.1, F2 1.02d): the game's mouse and keyboard fed from a queue in shared
   memory that the bot fills, so the bot needs neither the real cursor nor the focus.

   Both exes load DirectInput at runtime (LoadLibraryA("DINPUT.DLL"), DirectInputCreateA, DirectInput 3) and call
   eight slots of a device (read from their disassembly: CreateDevice; Release, SetProperty, Acquire, Unacquire,
   GetDeviceState, GetDeviceData, SetDataFormat, SetCooperativeLevel). When the shared memory
   "Local\GNWInput" is absent the call goes to the system's dinput.dll unchanged, so the instance stays playable by hand.

   Shared memory (little-endian int32), written by the bot except where marked:
     0 magic 'GNWI'   4 version 1
     8 total_dx       12 total_dy      (running sums; the device returns what grew since its last read)
     16 buttons (bit 0 left, bit 1 right)
     20 key_head      (the bot's count of keys queued)
     24 key_tail      (the DLL's count of keys delivered)          written by the DLL
     28 mouse_reads   (GetDeviceState calls)                       written by the DLL
     32 key_reads     (GetDeviceData calls)                        written by the DLL
     36 flags seen    (mouse coop level << 8 | keyboard coop level) written by the DLL
     40, 44 the virtual cursor (screen x, y) HRP is shown; 48 imports redirected  (48 written by the DLL)
     64 ring[256]     (scan code | down << 8)
   Build: zig cc -target x86-windows-gnu -O2 -shared dinput_proxy.c dinput.def -o DINPUT.DLL
   (python -m f1.instance input build ZIG does it). No game code is changed; the exe stays the hashed one. */
#include <windows.h>

typedef struct { volatile LONG magic, version, total_dx, total_dy, buttons, key_head, key_tail, mouse_reads,
                 key_reads, flags, pad[6]; volatile LONG ring[256]; } Shared;

static Shared *shm;
static LONG used_dx, used_dy;

typedef struct Dev Dev;
typedef struct { void *fn[18]; } DevVtbl;
struct Dev { DevVtbl *vt; LONG refs; int mouse; };
typedef struct { void *fn[8]; } DiVtbl;
typedef struct { DiVtbl *vt; LONG refs; } Di;

static const GUID SysMouse = {0x6F1D2B60, 0xD5A0, 0x11CF, {0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};

static HRESULT __stdcall d_qi(Dev *s, REFIID r, void **o) { *o = s; s->refs++; return 0; }
static ULONG __stdcall d_addref(Dev *s) { return ++s->refs; }
static ULONG __stdcall d_release(Dev *s) { return --s->refs; }
static HRESULT __stdcall d_ok(Dev *s) { return 0; }
static HRESULT __stdcall d_ok1(Dev *s, void *a) { return 0; }
static HRESULT __stdcall d_ok2(Dev *s, void *a, void *b) { return 0; }
static HRESULT __stdcall d_ok3(Dev *s, void *a, void *b, void *c) { return 0; }
static HRESULT __stdcall d_coop(Dev *s, HWND w, DWORD flags) {
    shm->flags = s->mouse ? (shm->flags & 0xFF) | (LONG)(flags << 8) : (shm->flags & ~0xFF) | (LONG)(flags & 0xFF);
    return 0;
}
static HRESULT __stdcall d_state(Dev *s, DWORD size, void *data) {
    ZeroMemory(data, size);
    if (s->mouse && size >= 16) {
        LONG *m = (LONG *)data;
        LONG tx = shm->total_dx, ty = shm->total_dy;
        m[0] = tx - used_dx; m[1] = ty - used_dy; used_dx = tx; used_dy = ty;
        BYTE *b = (BYTE *)data + 12;
        LONG bt = shm->buttons;
        b[0] = (bt & 1) ? 0x80 : 0; b[1] = (bt & 2) ? 0x80 : 0;
        InterlockedIncrement(&shm->mouse_reads);
    }
    return 0;
}
typedef struct { DWORD ofs, data, time, seq; } ObjData;
static HRESULT __stdcall d_data(Dev *s, DWORD size, ObjData *out, DWORD *inout, DWORD flags) {
    DWORD n = 0, room = *inout;
    InterlockedIncrement(&shm->key_reads);
    if (!s->mouse) {
        while (shm->key_tail != shm->key_head && n < room) {
            LONG e = shm->ring[shm->key_tail & 255];
            if (out) { out[n].ofs = e & 0xFF; out[n].data = (e & 0x100) ? 0x80 : 0;
                       out[n].time = GetTickCount(); out[n].seq = (DWORD)shm->key_tail; }
            n++;
            InterlockedIncrement(&shm->key_tail);
        }
    }
    *inout = n;
    return 0;
}
static DevVtbl dev_vt = {{d_qi, d_addref, d_release, d_ok1 /* GetCapabilities */, d_ok3 /* EnumObjects */,
                          d_ok2 /* GetProperty */, d_ok2 /* SetProperty */, d_ok /* Acquire */, d_ok /* Unacquire */,
                          d_state, d_data, d_ok1 /* SetDataFormat */, d_ok1 /* SetEventNotification */, d_coop,
                          d_ok3 /* GetObjectInfo */, d_ok1 /* GetDeviceInfo */, d_ok2 /* RunControlPanel */,
                          d_ok2 /* Initialize */}};

static HRESULT __stdcall i_qi(Di *s, REFIID r, void **o) { *o = s; s->refs++; return 0; }
static ULONG __stdcall i_addref(Di *s) { return ++s->refs; }
static ULONG __stdcall i_release(Di *s) { return --s->refs; }
static HRESULT __stdcall i_create(Di *s, REFGUID g, Dev **out, void *outer) {
    Dev *d = (Dev *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(Dev));
    d->vt = &dev_vt; d->refs = 1; d->mouse = IsEqualGUID(g, &SysMouse);
    *out = d;
    return 0;
}
static HRESULT __stdcall i_ok4(Di *s, void *a, void *b, void *c, void *d) { return 0; }
static HRESULT __stdcall i_ok2(Di *s, void *a, void *b) { return 0; }
static DiVtbl di_vt = {{i_qi, i_addref, i_release, i_create, i_ok4 /* EnumDevices */, i_ok2 /* GetDeviceStatus */,
                        i_ok2 /* RunControlPanel */, i_ok2 /* Initialize */}};
static Di di = {&di_vt, 1};

/* HRP (f1_res.dll / f2_res.dll) in a window sets the engine's cursor from the desktop's cursor (GetCursorPos) and
   clips the desktop's cursor to the window. Their imports are pointed here instead: the cursor HRP sees is the bot's
   virtual one (shared memory 40, 44: screen coordinates), and the owner's real cursor is never moved or clipped. */
static BOOL WINAPI v_GetCursorPos(POINT *p) { p->x = shm->pad[0]; p->y = shm->pad[1]; return TRUE; }
static BOOL WINAPI v_SetCursorPos(int x, int y) { shm->pad[0] = x; shm->pad[1] = y; return TRUE; }
static BOOL WINAPI v_ClipCursor(const RECT *r) { return TRUE; }

static void hook_imports(HMODULE m) {
    if (!m) return;
    BYTE *base = (BYTE *)m;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + ((IMAGE_DOS_HEADER *)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return;
    HMODULE u = GetModuleHandleA("user32.dll");
    void *real[3] = {GetProcAddress(u, "GetCursorPos"), GetProcAddress(u, "SetCursorPos"), GetProcAddress(u, "ClipCursor")};
    void *mine[3] = {v_GetCursorPos, v_SetCursorPos, v_ClipCursor};
    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir.VirtualAddress); d->Name; d++) {
        for (void **slot = (void **)(base + d->FirstThunk); *slot; slot++) {
            for (int k = 0; k < 3; k++) {
                if (*slot == real[k]) {
                    DWORD old;
                    VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &old);
                    *slot = mine[k];
                    VirtualProtect(slot, sizeof(void *), old, &old);
                    InterlockedIncrement(&shm->pad[2]); /* 48: imports pointed here */
                }
            }
        }
    }
}

typedef HRESULT(WINAPI *CreateFn)(HINSTANCE, DWORD, void **, void *);

__declspec(dllexport) HRESULT WINAPI DirectInputCreateA(HINSTANCE inst, DWORD version, void **out, void *outer) {
    HANDLE h = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, "Local\\GNWInput");
    if (h) shm = (Shared *)MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared));
    if (shm && shm->magic == 0x49574E47) {
        used_dx = shm->total_dx; used_dy = shm->total_dy;
        if (!shm->pad[2]) {
            hook_imports(GetModuleHandleA("f1_res.dll"));
            hook_imports(GetModuleHandleA("f2_res.dll"));
            hook_imports(GetModuleHandleA(NULL));
        }
        *out = &di;
        return 0;
    }
    char path[MAX_PATH];
    GetSystemDirectoryA(path, MAX_PATH);
    lstrcatA(path, "\\dinput.dll");
    HMODULE real = LoadLibraryA(path);
    CreateFn fn = real ? (CreateFn)GetProcAddress(real, "DirectInputCreateA") : 0;
    return fn ? fn(inst, version, out, outer) : E_FAIL;
}
