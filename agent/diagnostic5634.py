"""5.6.34 : état du système adapté au mode d'installation — serveur Linux, poste Linux ou poste Windows.

Constats corrigés (5.6.33) : la page appelait ``systemctl`` sous Windows, cherchait ``ui-auth.json`` dans /etc alors que le poste
utilise le fichier de son dossier de données, et sautait le contrôle des permissions sous Windows (voyant vert trop affirmatif).

- Serveur Linux : unités systemd (inchangé).
- Poste Linux et Windows : processus réellement lancés par le superviseur (``runtime.json`` : superviseur, interface, worker, veille),
  activité récente (journaux, signe de vie du worker), réponse de l'interface sur son port local.
- Fichier d'authentification : chemin réellement utilisé au démarrage (``AXIORHUB_AUTH_FILE`` posé par l'interface, à défaut le fichier
  voisin de la configuration chargée), jamais un chemin Linux supposé.
- Permissions : POSIX sous Linux ; sous Windows, lecture de la liste de contrôle d'accès réelle (SDDL) — un accès accordé à Tout le
  monde, aux Utilisateurs, aux Utilisateurs authentifiés, aux Invités ou à l'accès anonyme est signalé ; une liste illisible donne
  « non vérifié », jamais un voyant vert.
- Chaque alerte indique la fonctionnalité affectée, la cause vérifiée et une action compréhensible ; les connecteurs facultatifs portent
  « Non configuré », « À tester » ou la date du dernier test réussi.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import stat
import time

from . import portable

BROAD_SIDS = {'WD': 'Tout le monde', 'BU': 'Utilisateurs', 'AU': 'Utilisateurs authentifiés', 'IU': 'Utilisateurs interactifs',
              'AN': 'Accès anonyme', 'BG': 'Invités', 'S-1-1-0': 'Tout le monde', 'S-1-5-32-545': 'Utilisateurs',
              'S-1-5-11': 'Utilisateurs authentifiés', 'S-1-5-4': 'Utilisateurs interactifs', 'S-1-5-7': 'Accès anonyme', 'S-1-5-32-546': 'Invités'}
MODES = {'serveur': 'Serveur Linux', 'poste_linux': 'Poste de travail Linux', 'windows': 'Poste de travail Windows'}


def install_mode(config, auth=None):
    if portable.WINDOWS:
        return 'windows'
    if (auth or {}).get('poste') or os.environ.get('AXIORHUB_POSTE_HOME'):
        return 'poste_linux'
    path = str(config.get('_path') or '')
    if '/.local/share/axiorhub-pilote' in path.replace('\\', '/'):
        return 'poste_linux'
    return 'serveur'


def auth_file(config):
    """Fichier d'authentification réellement utilisé : celui passé à l'interface au démarrage, sinon le voisin de la configuration."""
    explicit = os.environ.get('AXIORHUB_AUTH_FILE')
    if explicit and Path(explicit).exists():
        return Path(explicit), 'chemin transmis à l’interface au démarrage'
    path = str(config.get('_path') or '')
    if path:
        return Path(path).with_name('ui-auth.json'), 'fichier voisin de la configuration chargée'
    return Path('/etc/axiorhub-mail-agent/ui-auth.json'), 'emplacement serveur par défaut'


def _runtime_path(config):
    data = Path(str(config.get('_path') or '')).parent
    if portable.WINDOWS:
        return data.parent / 'journaux' / 'runtime.json'
    home = data.parent.parent.parent if data.name == 'axiorhub-pilote' else Path.home()
    return home / '.local' / 'state' / 'axiorhub-pilote' / 'runtime.json'


def _port_answers(port, timeout=2):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, TypeError):
        return False


def _heartbeat_age(desk):
    try:
        rows = desk.db.execute("SELECT heartbeat FROM live_services_v430 WHERE name LIKE 'worker-%'").fetchall()
        ages = [time.time() - float(r[0]) for r in rows if r[0] is not None]
        return min(ages) if ages else None
    except Exception:   # noqa: BLE001
        return None


def poste_services(desk, runtime=None):
    """Processus du poste : superviseur, interface, worker et veille ; activité et réponse de l'interface. Lignes au format de
    reliability393.service_health (unit, label, state, required, verified) avec la fonctionnalité affectée et l'action."""
    path = Path(runtime) if runtime else _runtime_path(desk.c)
    rows = []
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        info = None
    action_start = ('Relancez AxiorHub Pilote depuis le menu Démarrer (ou son raccourci) : le superviseur redémarre les services.' if portable.WINDOWS
                    else 'Relancez AxiorHub Pilote depuis le menu des applications : le superviseur redémarre les services.')
    if not info:
        rows.append({'unit': 'superviseur', 'label': 'Superviseur du poste', 'state': 'état d’exécution introuvable', 'required': True, 'verified': False,
                     'feature': 'Toutes les fonctions automatiques', 'cause': 'Fichier %s absent ou illisible : le poste n’a pas été lancé par son superviseur.' % path.name,
                     'action': action_start})
        return rows
    alive = portable.pid_alive(info.get('pid')) if info.get('pid') else False
    rows.append({'unit': 'superviseur', 'label': 'Superviseur du poste', 'state': 'actif (pid %s)' % info.get('pid') if alive else 'arrêté', 'required': True,
                 'verified': bool(alive), 'feature': 'Relance automatique des services',
                 'cause': '' if alive else 'Le processus superviseur %s n’existe plus.' % info.get('pid'), 'action': '' if alive else action_start})
    labels = {'interface': ('Interface', 'Pages et boutons de l’application'), 'worker': ('Moteur de traitements', 'Préparations, fiches, rédactions'),
              'veille': ('Veille de la messagerie', 'Arrivée des nouveaux courriels')}
    faults = info.get('defauts') or {}
    logs = path.parent
    for name, (label, feature) in labels.items():
        pid = (info.get('processes') or {}).get(name)
        running = bool(pid) and portable.pid_alive(pid)
        log = logs / (name + '.log')
        try:
            age = time.time() - log.stat().st_mtime
        except OSError:
            age = None
        state = ('actif (pid %s)' % pid) if running else 'arrêté'
        cause = ''
        if not running:
            cause = 'Processus %s absent%s.' % (name, (' : ' + str(faults[name])[:160]) if faults.get(name) else '')
        if name == 'interface' and running and info.get('port') and not _port_answers(info['port']):
            running, cause = False, 'Le processus est lancé mais l’interface ne répond pas sur le port %s.' % info['port']
            state = 'ne répond pas'
        if name == 'worker' and running:
            beat = _heartbeat_age(desk)
            if beat is not None and beat > 180:
                state = 'lancé, sans signe de vie depuis %d min' % (beat // 60)
                running, cause = False, 'Le moteur de traitements est lancé mais ne signale plus d’activité.'
        if age is not None:
            state += ' · journal mis à jour il y a %s' % (('%d min' % (age // 60)) if age < 86400 else ('%d j' % (age // 86400)))
        rows.append({'unit': name, 'label': label, 'state': state, 'required': True, 'verified': running, 'feature': feature, 'cause': cause,
                     'action': '' if running else action_start})
    return rows


# ------------------------------------------------------------------------------------------------ permissions
# 5.6.35 (audit F14) : comptes acceptés sur un fichier protégé — système, administrateurs, « droits du propriétaire », le propriétaire
# lui-même et le compte qui exécute AxiorHub. Tout autre compte autorisé rend le contrôle « non vérifiable » (jamais un vert par défaut).
TRUSTED_SIDS = {'SY', 'BA', 'OW', 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-4'}
TRUSTED_OWNERS = {'SY', 'BA', 'S-1-5-18', 'S-1-5-32-544'}


def windows_user_sid():
    """SID (texte) du compte qui exécute le processus, ou None si illisible."""
    if not portable.WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
        return None
    try:
        needed = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))                   # TokenUser
        if not needed.value:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buf, needed, ctypes.byref(needed)):
            return None
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        out = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out)):
            return None
        try:
            return out.value
        finally:
            kernel.LocalFree(out)
    finally:
        kernel.CloseHandle(token)


def windows_sddl(path):
    """Descripteur de sécurité (SDDL) réel d'un fichier Windows — propriétaire et DACL —, ou None si illisible."""
    if not portable.WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    info = 0x00000001 | 0x00000004   # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    needed = wintypes.DWORD(0)
    advapi.GetFileSecurityW(str(path), info, None, 0, ctypes.byref(needed))
    if not needed.value:
        return None
    buf = ctypes.create_string_buffer(needed.value)
    if not advapi.GetFileSecurityW(str(path), info, buf, needed, ctypes.byref(needed)):
        return None
    out = wintypes.LPWSTR()
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf, 1, info, ctypes.byref(out), None):
        return None
    try:
        return out.value
    finally:
        kernel.LocalFree(out)


def parse_sddl(sddl):
    """Propriétaire, DACL nulle et entrées « autoriser » effectives (hors héritage seul) d'un descripteur SDDL."""
    import re
    text = str(sddl or '')
    owner = re.search(r'O:(.+?)(?=[GDS]:|$)', text)
    dacl = re.search(r'D:([^()]*)((?:\([^)]*\))*)', text)
    allowed = []
    for ace in re.findall(r'\(([^)]*)\)', dacl.group(2) if dacl else ''):
        parts = ace.split(';')
        if len(parts) >= 6 and parts[0] in ('A', 'OA') and 'IO' not in parts[1]:   # IO : héritage seul, sans effet sur le fichier
            allowed.append(parts[5])
    null = bool(dacl) and 'NO_ACCESS_CONTROL' in dacl.group(1)
    return {'owner': owner.group(1) if owner else None, 'has_dacl': bool(dacl), 'null_dacl': null,
            'empty_dacl': bool(dacl) and not null and not dacl.group(2), 'allowed': allowed}


def broad_access(sddl):
    """Entrées « autoriser » accordées à des groupes larges (Tout le monde, Utilisateurs…) dans une DACL SDDL."""
    return sorted({BROAD_SIDS[s] for s in parse_sddl(sddl)['allowed'] if s in BROAD_SIDS})


def file_permission(path, secret, mode, sddl_reader=None, me=None):
    """Contrôle réel d'un fichier : POSIX (Linux) ou liste de contrôle d'accès (Windows). Statut verified | error | unknown.
    5.6.35 (audit F14) : une DACL nulle (accès total pour tous) est une erreur ; un propriétaire ou un compte autorisé non reconnu
    donne « unknown » (à vérifier), jamais « verified »."""
    p = Path(path)
    if not p.exists():
        return {'status': 'missing'}
    if mode == 'windows':
        sddl = (sddl_reader or windows_sddl)(p)
        if not sddl:
            return {'status': 'unknown', 'reason': 'autorisations_windows_illisibles'}
        acl = parse_sddl(sddl)
        if not acl['has_dacl']:
            return {'status': 'unknown', 'reason': 'autorisations_windows_illisibles'}
        if acl['null_dacl']:   # DACL nulle : accès complet pour tous (documentation Microsoft)
            return {'status': 'error', 'reason': 'dacl_nulle', 'broad': ['Tout le monde']}
        if acl['empty_dacl']:  # DACL vide : personne, pas même AxiorHub, ne peut lire le fichier
            return {'status': 'error', 'reason': 'dacl_vide_fichier_illisible'}
        broad = broad_access(sddl)
        if broad:
            return {'status': 'error', 'reason': 'acces_trop_large', 'broad': broad}
        me = me or windows_user_sid()
        owners = TRUSTED_OWNERS | ({me} if me else set())
        if not acl['owner'] or acl['owner'] not in owners:
            return {'status': 'unknown', 'reason': 'proprietaire_non_reconnu', 'owner': acl['owner'] or ''}
        others = sorted({s for s in acl['allowed'] if s not in TRUSTED_SIDS | owners})
        if others:
            return {'status': 'unknown', 'reason': 'compte_non_reconnu', 'accounts': others}
        return {'status': 'verified', 'acl': 'propriétaire et système uniquement'}
    info = p.lstat()
    m = stat.S_IMODE(info.st_mode)
    regular = stat.S_ISREG(info.st_mode) and not p.is_symlink()
    protected = not portable.too_open(m, 0o027 if secret else 0o002)
    return {'status': 'verified' if regular and protected else 'error', 'mode': format(m, '04o'), 'reason': '' if regular and protected else 'type_ou_permissions_inadaptees'}


def poste_files(config):
    """Fichiers sensibles d'un poste (Linux ou Windows) : configuration, authentification réellement utilisée, mot de passe local."""
    auth, origin = auth_file(config)
    data = Path(str(config.get('_path') or '')).parent
    out = [(str(config.get('_path') or ''), False, 'Configuration'), (str(auth), True, 'Connexion à l’interface (%s)' % origin)]
    for name, label in (('mot-de-passe-local', 'Mot de passe local du poste'), ('internal-auth.json', 'Jeton interne')):
        if (data / name).exists():
            out.append((str(data / name), True, label))
    return [x for x in out if x[0]]


# ------------------------------------------------------------------------------------------------ connecteurs facultatifs
CONNECTORS = (('messagerie', 'Messagerie (IMAP)', 'Lecture des courriels, brouillons', ('mail.host',)),
              ('envoi', 'Envoi (SMTP)', 'Envoi des courriels validés', ('smtp.host',)),
              ('stockage', 'Nextcloud / dossier de travail', 'Pièces des dossiers', ('nextcloud.url', 'nextcloud.local_path')),
              ('agenda', 'Synchronisation d’agenda (CalDAV / Google)', 'Synchronisation de l’agenda local (facultative)', ('calendar.urls', 'google_calendar568.client_id')),
              ('facturation', 'Invoice Ninja', 'Facturation', ('invoice_ninja.base_url',)),
              ('telephonie', 'Twilio', 'Accueil téléphonique', ('reception.twilio_callback_url',)))


def _get(cfg, key):
    value = cfg
    for part in key.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _configured(cfg, keys):
    for key in keys:
        value = _get(cfg, key)
        if isinstance(value, list):
            value = [v for v in value if v]
        if value and 'example.com' not in str(value) and value is not False:
            if key == 'invoice_ninja.base_url' and not _get(cfg, 'invoice_ninja.enabled'):
                continue
            return True
    return False


def connectors(desk):
    """« Non configuré », « À tester » ou « Testé le … » (preuve du guide encore valable pour la configuration en vigueur)."""
    out = []
    receipts = {}
    try:
        from .guide5631 import fingerprint
        now = datetime.now(timezone.utc).isoformat()
        for row in desk.db.execute("SELECT connector,config_hash,secret_version,checked_at,valid_until FROM connection_receipts5631 "
                                   "WHERE status='available' ORDER BY id"):
            h, v = fingerprint(desk.c, row['connector'])
            if row['config_hash'] == h and row['secret_version'] == v and row['valid_until'] >= now:
                receipts[row['connector']] = row['checked_at']
    except Exception:   # noqa: BLE001 — table absente avant la première utilisation du guide
        pass
    for key, label, feature, fields in CONNECTORS:
        if not _configured(desk.c, fields):
            state, cls = 'Non configuré', 'muted'
        elif key in receipts:
            state, cls = 'Testé le ' + receipts[key][:10], 'ok'
        else:
            state, cls = 'À tester', 'warn'
        out.append({'connector': key, 'label': label, 'feature': feature, 'state': state, 'class': cls,
                    'action': '' if cls == 'ok' else ('Configurer pas à pas : Paramètres › Guide d’installation' if cls == 'muted'
                                                      else 'Lancer le test : Paramètres › Guide d’installation › Tester')})
    return out


def alerts(services, files, mode):
    """Alertes lisibles : fonctionnalité affectée, cause vérifiée, action."""
    out = []
    for s in services:
        if s.get('required') and not s.get('verified'):
            out.append({'feature': s.get('feature') or s['label'], 'cause': s.get('cause') or ('%s : %s' % (s['label'], s['state'])),
                        'action': s.get('action') or ('sudo systemctl restart ' + s['unit'] if mode == 'serveur' else 'Relancez AxiorHub Pilote.')})
    for f in files:
        if f['status'] == 'error':
            if f.get('reason') == 'fichier_absent':
                cause, action = 'Fichier absent : %s.' % f['path'], 'Relancez l’installation (ou le guide) pour recréer ce fichier.'
            elif f.get('broad'):
                cause = 'Le fichier %s est lisible par : %s.' % (f['path'], ', '.join(f['broad']))
                action = 'Retirez ces accès dans Propriétés › Sécurité du fichier (seuls votre compte, SYSTEM et les administrateurs doivent y figurer).'
            else:
                cause = 'Permissions trop larges ou type inattendu : %s (%s).' % (f['path'], f.get('mode', ''))
                action = 'chmod 600 %s' % f['path'] if mode != 'windows' else 'Restreignez l’accès au seul compte utilisateur.'
            out.append({'feature': f.get('feature') or 'Sécurité des secrets', 'cause': cause, 'action': action})
        elif f['status'] == 'unknown':
            out.append({'feature': f.get('feature') or 'Sécurité des secrets', 'cause': 'Autorisations non vérifiables : %s.' % f['path'],
                        'action': 'Contrôlez l’accès au fichier (Propriétés › Sécurité) ; le voyant reste orange tant que la vérification échoue.'})
    return out


def relaunch_hint(config):
    """Consigne de relance adaptée au mode d'installation (HTML sûr : texte fixe)."""
    mode = install_mode(config)
    if mode == 'windows':
        return 'Pour relancer : fermez puis rouvrez AxiorHub Pilote (menu Démarrer) ; le superviseur redémarre l’interface, le moteur et la veille.'
    if mode == 'poste_linux':
        return 'Pour relancer : fermez puis rouvrez AxiorHub Pilote depuis le menu des applications ; le superviseur redémarre les services.'
    return ('Pour relancer : <code>sudo systemctl restart axiorhub-mail-desk-worker.service axiorhub-mail-desk-worker@2.service '
            'axiorhub-mail-watch.service</code>. Analyse ou nettoyage périodique arrêté : <code>sudo systemctl enable --now '
            'axiorhub-mail-agent.timer axiorhub-mail-agent-cleanup.timer</code>')
