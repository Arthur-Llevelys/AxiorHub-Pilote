"""5.6.25 : couche portable Linux / Windows du cœur d'AxiorHub.

Le code métier n'appelle plus directement les fonctions propres à Unix :

- ``fcntl.flock`` : verrous consultatifs de fichiers. Sous Linux, ``fcntl`` lui-même ; sous Windows, ``LockFileEx`` /
  ``UnlockFileEx`` (verrou partagé ou exclusif, non bloquant à la demande), avec la même sémantique que ``flock`` pour un
  fichier de verrou dédié : verrou porté par la poignée ouverte, libéré à sa fermeture, ``BlockingIOError`` si occupé.
- droits et propriétaires (``fchmod``, ``chmod``, ``chown``, ``geteuid``) : sous Windows, les données du poste vivent dans le
  profil de l'utilisateur (``%LOCALAPPDATA%``), déjà réservé à son compte ; ces appels deviennent sans effet au lieu d'échouer.
- programmes externes (LibreOffice, Tesseract, Poppler, eSpeak NG) : recherchés dans le ``PATH`` puis dans leurs dossiers
  d'installation habituels ; les limites de ressources (``preexec_fn``) n'existent que sous Linux, le délai maximal s'applique
  partout.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

WINDOWS = os.name == 'nt'


class _WinFlock:
    """Équivalent de ``fcntl`` limité à ``flock`` et à ses constantes, fondé sur LockFileEx."""
    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8
    _LOCKFILE_FAIL_IMMEDIATELY, _LOCKFILE_EXCLUSIVE_LOCK = 0x1, 0x2
    _ERROR_LOCK_VIOLATION, _ERROR_NOT_LOCKED = 33, 158

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [('Internal', ctypes.c_void_p), ('InternalHigh', ctypes.c_void_p), ('Offset', wintypes.DWORD),
                        ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
        self._ctypes, self._msvcrt, self._OVERLAPPED = ctypes, msvcrt, OVERLAPPED
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        self._lock = k.LockFileEx
        self._lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
        self._lock.restype = wintypes.BOOL
        self._unlock = k.UnlockFileEx
        self._unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
        self._unlock.restype = wintypes.BOOL

    def _handle(self, f):
        fd = f if isinstance(f, int) else f.fileno()
        return self._msvcrt.get_osfhandle(fd)

    def _release(self, handle):
        # Aucun état gardé par poignée : Windows réutilise les numéros de poignées fermées (un état mémorisé ferait croire
        # qu'un nouveau fichier est déjà verrouillé). Libérer une région non verrouillée renvoie ERROR_NOT_LOCKED, ignoré.
        ov = self._OVERLAPPED()
        if not self._unlock(handle, 0, 0xFFFFFFFF, 0xFFFFFFFF, self._ctypes.byref(ov)):
            err = self._ctypes.get_last_error()
            if err != self._ERROR_NOT_LOCKED:
                raise OSError(err, 'UnlockFileEx')

    def flock(self, f, op):
        handle = self._handle(f)
        if op & self.LOCK_UN:
            self._release(handle)
            return None
        self._release(handle)   # conversion partagé ↔ exclusif ou renouvellement : comme flock, non atomique
        flags = (self._LOCKFILE_EXCLUSIVE_LOCK if op & self.LOCK_EX else 0) | (self._LOCKFILE_FAIL_IMMEDIATELY if op & self.LOCK_NB else 0)
        ov = self._OVERLAPPED()
        if not self._lock(handle, flags, 0, 0xFFFFFFFF, 0xFFFFFFFF, self._ctypes.byref(ov)):
            err = self._ctypes.get_last_error()
            if err == self._ERROR_LOCK_VIOLATION:
                raise BlockingIOError(11, 'Resource temporarily unavailable')
            raise OSError(err, 'LockFileEx')
        return None


if WINDOWS:
    fcntl = _WinFlock()
else:   # pragma: no cover - Linux
    import fcntl  # noqa: F401  (réexporté)


def fchmod(fd, mode):
    if not WINDOWS:
        os.fchmod(fd, mode)


def chmod(path, mode):
    """Sous Windows, ``os.chmod`` ne gère que la lecture seule : on ne retire jamais l'écriture d'un fichier du profil."""
    if not WINDOWS:
        os.chmod(path, mode)


def fchown(fd, uid, gid):
    if not WINDOWS and hasattr(os, 'fchown'):
        os.fchown(fd, uid, gid)


def chown(path, uid, gid):
    if not WINDOWS and hasattr(os, 'chown'):
        os.chown(path, uid, gid)


def too_open(st_mode, mask):
    """5.6.25 : droits trop larges ? Sous Windows, les bits POSIX rendus par stat ne reflètent pas les ACL (toujours 0o666) ;
    la protection vient du profil de l'utilisateur (%LOCALAPPDATA%, privé par défaut). Sous Linux, contrôle inchangé."""
    return False if WINDOWS else bool(st_mode & mask)


def geteuid():
    """Identifiant effectif ; sous Windows, jamais « root » (0)."""
    return os.geteuid() if hasattr(os, 'geteuid') else 1000


KNOWN = {
    'libreoffice': [r'{ProgramFiles}\LibreOffice\program\soffice.exe', r'{ProgramFiles(x86)}\LibreOffice\program\soffice.exe', '/usr/bin/libreoffice', '/usr/bin/soffice'],
    'tesseract': [r'{ProgramFiles}\Tesseract-OCR\tesseract.exe', r'{LOCALAPPDATA}\Programs\Tesseract-OCR\tesseract.exe', '/usr/bin/tesseract'],
    'pdftoppm': [r'{ProgramFiles}\poppler\Library\bin\pdftoppm.exe', r'{LOCALAPPDATA}\Programs\poppler\Library\bin\pdftoppm.exe', '/usr/bin/pdftoppm'],
    'pdfinfo': [r'{ProgramFiles}\poppler\Library\bin\pdfinfo.exe', r'{LOCALAPPDATA}\Programs\poppler\Library\bin\pdfinfo.exe', '/usr/bin/pdfinfo'],
    'espeak-ng': [r'{ProgramFiles}\eSpeak NG\espeak-ng.exe', r'{ProgramFiles(x86)}\eSpeak NG\espeak-ng.exe', '/usr/bin/espeak-ng'],
}
ALIASES = {'libreoffice': ('libreoffice', 'soffice'), 'soffice': ('soffice', 'libreoffice')}


def which(name):
    """Chemin d'un programme externe : ``PATH`` d'abord, puis dossiers d'installation habituels (Windows ou Linux) ; ``None`` sinon."""
    for candidate in ALIASES.get(name, (name,)):
        found = shutil.which(candidate)
        if found:
            return found
    key = 'libreoffice' if name in ('libreoffice', 'soffice') else name
    for pattern in KNOWN.get(key, ()):
        try:
            path = pattern.format(**{k: os.environ.get(k, '') for k in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')})
        except (KeyError, IndexError):
            continue
        if path and not path.startswith('\\') and Path(path).is_file():
            return path
    return None


def tool_env(**extra):
    """Environnement minimal des outils externes : Linux strict (PATH système), Windows avec les variables indispensables au système."""
    if WINDOWS:
        keep = ('SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'PATHEXT', 'COMSPEC', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA',
                'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
        env = {k: os.environ[k] for k in keep if k in os.environ}
    else:
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def run_tool(args, timeout, limits=None, **kwargs):
    """``subprocess.run`` d'un outil externe : limites de ressources sous Linux seulement, aucune fenêtre console sous Windows."""
    if WINDOWS:
        kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    elif limits is not None:
        kwargs['preexec_fn'] = limits
    return subprocess.run(args, timeout=timeout, check=False, **kwargs)


def frozen():
    """Vrai dans l'application Windows empaquetée (PyInstaller)."""
    return bool(getattr(sys, 'frozen', False))


def pid_alive(pid):
    """Vrai si le processus existe encore. Sous Windows, ``os.kill(pid, 0)`` TERMINERAIT le processus : on interroge son code de sortie."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5      # accès refusé : le processus existe
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                  # STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


def kill_tree(pid):
    """Arrête un processus et tous ses descendants (Windows : taskkill /T /F ; Linux : groupe de processus)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if WINDOWS:
        done = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
        return done.returncode == 0
    import signal
    try:
        os.killpg(pid, signal.SIGKILL)
        return True
    except OSError:
        return False
