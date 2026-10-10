"""5.6.30 : la fenêtre AxiorHub (ouverte par Microsoft Edge en mode application) porte sa propre identité dans la barre des tâches.

Sans cela, Windows range la fenêtre sous Edge et affiche l'icône d'Edge. Dès que la fenêtre apparaît, on lui attribue :
- un identifiant d'application propre (System.AppUserModel.ID = « AxiorHub.Pilote ») : groupe séparé dans la barre des tâches ;
- l'icône, le nom et la commande de relance d'AxiorHub Pilote (épingler la fenêtre épingle AxiorHub, pas Edge) ;
- l'icône AxiorHub dans la barre de titre et le bouton de la barre des tâches (WM_SETICON).
Tout passe par l'API du shell Windows (ctypes) ; en cas d'échec, la fenêtre reste simplement celle d'Edge.
"""
import ctypes
import os
import sys
import threading
import time
import uuid

APP_ID = 'AxiorHub.Pilote'
APP_NAME = 'AxiorHub Pilote'
VT_LPWSTR = 31
WM_SETICON = 0x0080


def _structs():
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort), ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [('fmtid', GUID), ('pid', ctypes.c_ulong)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort), ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                    ('value', ctypes.c_void_p), ('pad', ctypes.c_void_p)]
    return wintypes, GUID, PROPERTYKEY, PROPVARIANT


def _guid(GUID, text):
    u = uuid.UUID(text)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def set_identity(hwnd, exe, app_id=APP_ID, name=APP_NAME):
    """Attribue l'identifiant d'application, l'icône, le nom et la commande de relance à une fenêtre (même d'un autre processus)."""
    wintypes, GUID, PROPERTYKEY, PROPVARIANT = _structs()
    shell32 = ctypes.windll.shell32
    store = ctypes.c_void_p()
    iid = _guid(GUID, '886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99')   # IID_IPropertyStore
    if shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid), ctypes.byref(store)) != 0 or not store:
        return False
    vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vtable[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    fmtid = _guid(GUID, '9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3')   # PKEY_AppUserModel_*
    keep = []
    ok = True
    try:
        # Les propriétés de relance d'abord, l'identifiant en dernier (ordre recommandé par Windows).
        for pid, text in ((2, '"%s"' % exe), (3, '%s,0' % exe), (4, name), (5, app_id)):
            buf = ctypes.create_unicode_buffer(text)
            keep.append(buf)
            var = PROPVARIANT()
            var.vt = VT_LPWSTR
            var.value = ctypes.cast(buf, ctypes.c_void_p)
            key = PROPERTYKEY(fmtid, pid)
            ok = set_value(store, ctypes.byref(key), ctypes.byref(var)) == 0 and ok
        ok = commit(store) == 0 and ok
    finally:
        release(store)
    return ok


def _icons(exe):
    large, small = ctypes.c_void_p(), ctypes.c_void_p()
    if ctypes.windll.shell32.ExtractIconExW(exe, 0, ctypes.byref(large), ctypes.byref(small), 1) and large:
        return large.value, small.value or large.value
    return None, None


def _process_tree(root):
    """PID du processus ``root`` et de tous ses descendants (Edge confie la fenêtre à un processus enfant puis rend la main)."""
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {root}
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    tree, todo = {root}, [root]
    while todo:
        for child in parents.get(todo.pop(), []):
            if child not in tree:
                tree.add(child)
                todo.append(child)
    return tree


def windows_of(pids):
    """Fenêtres visibles et titrées appartenant à l'un des processus ``pids``."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pids = set(pids) if not isinstance(pids, int) else {pids}
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True
    user32.EnumWindows(each, 0)
    return found


def brand(proc, exe=None, interval=1.0, first_wait=90, state=None):
    """Donne l'identité AxiorHub aux fenêtres ouvertes par le processus Edge ``proc`` et ses descendants (fil de fond).
    S'arrête quand plus aucun processus de l'arbre ne vit, ou si aucune fenêtre n'est apparue après ``first_wait`` secondes."""
    if os.name != 'nt':
        return None
    exe = exe or sys.executable
    big, small = _icons(exe)
    done = set()
    started = time.time()
    state = state if state is not None else {}
    state.update(seen=False, closed=False)
    last_seen = [started]

    def run():
        while True:
            try:
                tree = _process_tree(proc.pid)
                alive = [pid for pid in tree if pid != proc.pid] or ([proc.pid] if proc.poll() is None else [])
                if not alive and (done or time.time() - started > 10):
                    state['closed'] = bool(done)
                    return
                if not done and time.time() - started > first_wait:
                    return
                visible = windows_of(tree)
                if visible:
                    last_seen[0] = time.time()
                    state['seen'] = True
                elif done and time.time() - last_seen[0] > 3:   # fenêtre fermée : fin de la surveillance
                    state['closed'] = True
                    return
                for hwnd in visible:
                    if hwnd in done:
                        continue
                    set_identity(hwnd, exe)
                    if big:
                        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, big)
                        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, small)
                    done.add(hwnd)
            except (OSError, AttributeError, ValueError):
                return
            time.sleep(interval)
    thread = threading.Thread(target=run, name='axiorhub-marque-fenetre', daemon=True)
    thread.start()
    return thread
