"""5.6.25 : installation, mise à jour et désinstallation du poste Windows, pour l'utilisateur courant (aucun droit administrateur).

- Programme : ``%LOCALAPPDATA%\\Programs\\AxiorHub Pilote\\app-<version>`` (une version active ; les précédentes sont retirées
  après une installation réussie).
- Raccourcis : menu Démarrer (toujours), Bureau (au choix), dossier Démarrage de la session (au choix : services sans fenêtre).
- Désinstallation : entrée « Applications installées » de Windows (clé HKCU ...\\Uninstall\\AxiorHubPilote). Les données de
  l'utilisateur (``%LOCALAPPDATA%\\AxiorHub Pilote``) sont conservées, sauf demande explicite.

Les emplacements peuvent être redirigés par variables d'environnement (``AXIORHUB_WIN_*``) pour les essais d'installation à blanc.
"""
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

APP = 'AxiorHub Pilote'
GUI_EXE = 'AxiorHub Pilote.exe'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\AxiorHubPilote'
# 5.6.97 (A05) : protocole axiorhub:// pour l'utilisateur courant ; la commande passe l'adresse comme UN argument à l'exécutable
# (« --liaison "%1" »), jamais à un interpréteur de commandes ; le lanceur n'en retient que l'identifiant opaque.
PROTOCOL_KEY = r'Software\Classes\axiorhub'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _env_path(name, default):
    value = os.environ.get(name)
    return Path(value) if value else Path(default)


def local_appdata():
    return _env_path('AXIORHUB_WIN_LOCALAPPDATA', os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')


def install_root():
    return _env_path('AXIORHUB_WIN_RACINE', local_appdata() / 'Programs' / APP)


def data_root():
    return local_appdata() / APP


def shortcut_dirs():
    appdata = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    programs = appdata / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    return {'menu': _env_path('AXIORHUB_WIN_MENU', programs),
            'bureau': _env_path('AXIORHUB_WIN_BUREAU', Path(os.environ.get('USERPROFILE') or Path.home()) / 'Desktop'),
            'demarrage': _env_path('AXIORHUB_WIN_DEMARRAGE', programs / 'Startup')}


SHORTCUTS = {'menu': APP + '.lnk', 'bureau': APP + '.lnk', 'demarrage': APP + ' (services).lnk'}
SHORTCUT_DEMO = APP + ' — Démo.lnk'   # 5.6.107 (lot M) : second raccourci du menu Démarrer, « AxiorHub Pilote — Démo » (poste.py --demo)


def make_shortcut(link, target, arguments='', workdir='', description=''):
    """Raccourci Windows (.lnk) par l'objet COM WScript.Shell ; les valeurs passent par l'environnement (aucune injection)."""
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AXH_LNK);$s.TargetPath=$env:AXH_TARGET;"
              "$s.Arguments=$env:AXH_ARGS;$s.WorkingDirectory=$env:AXH_DIR;$s.IconLocation=$env:AXH_TARGET+',0';"
              "$s.Description=$env:AXH_DESC;$s.Save()")
    env = {**os.environ, 'AXH_LNK': str(link), 'AXH_TARGET': str(target), 'AXH_ARGS': arguments, 'AXH_DIR': str(workdir or Path(target).parent),
           'AXH_DESC': description}
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW, check=False, timeout=60)
    if done.returncode or not link.is_file():
        raise OSError('raccourci_non_cree: ' + done.stderr.decode('utf-8', 'replace')[:200])
    return link


def write_registry(root, version, exe, size_kb):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (('DisplayName', APP), ('DisplayVersion', version), ('Publisher', 'AxiorHub'), ('InstallLocation', str(root)),
                            ('DisplayIcon', str(exe)), ('UninstallString', '"%s" --desinstaller' % exe),
                            ('URLInfoAbout', 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (('NoModify', 1), ('NoRepair', 1), ('EstimatedSize', int(size_kb))):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def protocol_command(exe):
    return '"%s" --liaison "%%1"' % exe


def write_protocol(exe):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:AxiorHub Pilote')
        winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + r'\DefaultIcon') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, '"%s",0' % exe)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + r'\shell\open\command') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, protocol_command(exe))


def delete_protocol():
    import winreg
    for sub in (r'\shell\open\command', r'\shell\open', r'\shell', r'\DefaultIcon', ''):   # du plus profond à la racine
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + sub)
        except OSError:
            pass


def delete_registry():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def stop_running():
    """Arrête une instance en cours (superviseur et services) avant mise à jour ou désinstallation."""
    runtime = data_root() / 'journaux' / 'runtime.json'
    try:
        info = json.loads(runtime.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    pids = [info.get('pid')] + list((info.get('processes') or {}).values())
    for pid in pids:
        if isinstance(pid, int) and pid > 0 and pid != os.getpid():
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW, check=False)
    try:
        runtime.unlink()
    except OSError:
        pass
    time.sleep(1)
    return True


# 5.6.102 : juste après l'extraction, l'antivirus (Windows Defender) ou l'indexation peut garder quelques secondes un verrou sur
# les exécutables tout juste écrits ; le renommage échoue alors en WinError 5 (accès refusé) ou 32 (fichier utilisé). On réessaie
# avec une attente croissante (environ 10 s au total) avant d'abandonner proprement, la version installée restant en place.
RENAME_DELAYS = (0.25, 0.5, 1.0, 1.5, 2.0, 2.25, 2.5)
_LOCK_WINERRORS = (5, 32)


class InstallationBloquee(OSError):
    """Le dossier de la nouvelle version n'a pas pu être mis en place : fichiers verrouillés par un autre programme."""


def _transient_lock(exc):
    return isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in _LOCK_WINERRORS


def _with_retries(action, delays=None):
    """Exécute ``action`` ; sur un verrou passager (accès refusé, fichier utilisé), réessaie après chaque délai de ``delays``."""
    delays = RENAME_DELAYS if delays is None else delays
    for delay in tuple(delays) + (None,):
        try:
            return action()
        except OSError as exc:
            if delay is None or not _transient_lock(exc):
                raise
            time.sleep(delay)


def _put_in_place(inner, target):
    """Remplace ``target`` par ``inner`` (renommage), en réessayant tant qu'un verrou passager l'empêche."""
    try:
        if target.exists():
            _with_retries(lambda: target.exists() and shutil.rmtree(target))
        _with_retries(lambda: os.replace(inner, target))
    except OSError as exc:
        if not _transient_lock(exc):
            raise
        raise InstallationBloquee(
            'impossible de mettre en place le dossier %s : des fichiers sont encore utilisés par un autre programme '
            '(souvent l’antivirus qui analyse les fichiers tout juste copiés). La version déjà installée n’a pas été modifiée. '
            'Patientez quelques secondes puis relancez l’installation. [%s]' % (target.name, exc)) from exc


def _size_kb(folder):
    return sum(f.stat().st_size for f in Path(folder).rglob('*') if f.is_file()) // 1024


def install(archive, version, root=None, desktop=True, startup=False, shortcuts=True, registry=True, progress=None):
    """Installe ou met à jour l'application depuis l'archive zip du dossier PyInstaller. Retourne le chemin de l'exécutable."""
    say = progress or (lambda message: None)
    root = Path(root or install_root())
    root.mkdir(parents=True, exist_ok=True)
    say('Arrêt d’une version en cours…')
    stop_running()
    target = root / ('app-' + version)
    staging = Path(tempfile.mkdtemp(prefix='.installation-', dir=str(root)))
    try:
        say('Copie des fichiers…')
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = Path(info.filename)
                if name.is_absolute() or '..' in name.parts:
                    raise ValueError('archive_chemin_refuse')
            z.extractall(staging)
        inner = staging / APP if (staging / APP / GUI_EXE).is_file() else staging
        if not (inner / GUI_EXE).is_file():
            raise ValueError('archive_incomplete')
        _put_in_place(inner, target)   # 5.6.102 : réessais sur verrou passager (antivirus)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for old in root.glob('app-*'):
        if old != target:
            shutil.rmtree(old, ignore_errors=True)
    (root / 'version.txt').write_text(version + '\n', encoding='utf-8')
    exe = target / GUI_EXE
    if shortcuts:
        say('Raccourcis…')
        dirs = shortcut_dirs()
        make_shortcut(dirs['menu'] / SHORTCUTS['menu'], exe, '', target, 'AxiorHub Pilote — agent de cabinet, sur ce poste')
        make_shortcut(dirs['menu'] / SHORTCUT_DEMO, exe, '--demo', target, 'AxiorHub Pilote — Démo : cabinet fictif, données séparées, aucun envoi')
        if desktop:
            make_shortcut(dirs['bureau'] / SHORTCUTS['bureau'], exe, '', target, 'AxiorHub Pilote')
        else:
            _remove(dirs['bureau'] / SHORTCUTS['bureau'])
        if startup:
            make_shortcut(dirs['demarrage'] / SHORTCUTS['demarrage'], exe, '--no-window', target, 'Services AxiorHub Pilote à l’ouverture de session')
        else:
            _remove(dirs['demarrage'] / SHORTCUTS['demarrage'])
    if registry:
        write_registry(root, version, exe, _size_kb(target))
        write_protocol(exe)   # 5.6.97 (A05) : liaison ChatGPT demandée par le serveur du cabinet
    say('Installation terminée.')
    return exe


def _remove(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


INTERDITS_CMD = ('"', '\r', '\n', '\x00')


def commande_suppression_differee(targets):
    """5.6.111 (lot C, S7) : commande ``cmd /c`` de la suppression différée, sans aucun chemin dans son texte. Les dossiers viennent de variables
    d'environnement (``AXIORHUB_WIN_RACINE``, ``LOCALAPPDATA``…) : un ``%``, un ``&`` ou un ``^`` dans un chemin aurait été interprété par
    ``cmd``. Chaque chemin est transmis dans une variable propre au processus enfant (``%AXH_SUPPR_n%`` : développée une seule fois, jamais
    relue comme commande, entre guillemets) ; un guillemet, un retour à la ligne ou un caractère nul, impossibles dans un chemin Windows
    valide, font refuser la commande. ``/d`` écarte les commandes AutoRun, ``/v:off`` l'expansion différée (``!``)."""
    env = dict(os.environ)
    parts = ['ping -n 4 127.0.0.1 >nul']
    for i, target in enumerate(targets):
        text = str(target)
        if not text or any(c in text for c in INTERDITS_CMD) or not (PureWindowsPath(text).is_absolute() or Path(text).is_absolute()):
            raise ValueError('chemin_de_suppression_refuse')
        env['AXH_SUPPR_%d' % i] = text
        parts.append('rmdir /s /q "%%AXH_SUPPR_%d%%"' % i)
    # ligne de commande passée telle quelle à CreateProcess : ``/s /c "…"`` retire seulement les guillemets extérieurs (une liste d'arguments
    # ferait échapper les guillemets intérieurs en \" par subprocess, que cmd ne comprend pas : l'ancienne suppression différée échouait)
    return 'cmd /d /v:off /s /c "' + ' & '.join(parts) + '"', env


def uninstall(root=None, purge_data=False, shortcuts=True, registry=True, detach=True):
    """Désinstalle : arrêt, raccourcis, entrée de désinstallation, dossier du programme (après la fin de ce processus si besoin)."""
    root = Path(root or install_root())
    stop_running()
    if shortcuts:
        dirs = shortcut_dirs()
        for kind, name in SHORTCUTS.items():
            _remove(dirs[kind] / name)
        _remove(dirs['menu'] / SHORTCUT_DEMO)   # 5.6.107 (lot M)
    if registry:
        delete_registry()
        delete_protocol()
    targets = [root] + ([data_root()] if purge_data else [])
    running_inside = getattr(sys, 'frozen', False) and root in Path(sys.executable).resolve().parents
    if detach and running_inside:
        # l'exécutable en cours ne peut pas effacer son propre dossier : suppression différée de quelques secondes
        command, env = commande_suppression_differee(targets)
        subprocess.Popen(command, env=env,
                         creationflags=NO_WINDOW | getattr(subprocess, 'DETACHED_PROCESS', 0), close_fds=True)
    else:
        for t in targets:
            shutil.rmtree(t, ignore_errors=True)
    return targets


def uninstall_dialog():
    """Désinstallation depuis « Applications installées » : confirmation, puis choix de conserver les données (par défaut)."""
    import tkinter
    from tkinter import messagebox
    window = tkinter.Tk()
    window.withdraw()
    if not messagebox.askyesno(APP, 'Désinstaller AxiorHub Pilote de ce poste ?\n\nLes services seront arrêtés.', parent=window):
        return 0
    purge = messagebox.askyesno(APP, 'Supprimer aussi vos données AxiorHub (configuration, mémoire des dossiers, journaux) ?\n\n'
                                     'Choisissez « Non » pour les conserver en vue d’une réinstallation. '
                                     'Vos dossiers de travail et vos courriels ne sont jamais touchés.', default='no', parent=window)
    uninstall(purge_data=purge)
    messagebox.showinfo(APP, 'AxiorHub Pilote a été désinstallé.' + ('' if purge else '\nVos données restent dans ' + str(data_root()) + '.'), parent=window)
    window.destroy()
    return 0
