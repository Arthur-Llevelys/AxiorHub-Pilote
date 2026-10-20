"""5.6.50 : membres du cabinet, droits réglables compte par compte et dossiers réservés.

- **Annuaire** : les comptes de la version serveur (page de connexion 5.6.48), de la distribution autonome et les membres déjà vus
  (application Nextcloud, ExApp) ; nom et rôle affichés dans la messagerie interne.
- **Droits par compte** : sur la base du rôle (administrateur, avocat, assistant(e)), l'administrateur accorde ou retire six droits
  à un compte avocat ou assistant(e) : envoyer des courriels, déposer des brouillons, valider les décisions et productions, gérer
  l'agenda, facturation et temps, réglages du cabinet. Un administrateur garde tous ses droits ; la gestion des comptes et des droits
  reste réservée aux administrateurs, quel que soit le réglage.
- **Dossiers réservés** (facultatif) : un dossier peut être réservé à certains comptes ; les autres comptes (hors administrateurs) ne
  le voient plus dans les listes, les recherches, l'assistant et la messagerie, et toute requête qui le désigne est refusée.
  Les travaux de l'agent (en arrière-plan, pour le cabinet) ne sont pas cloisonnés.
- Stockage : ``acces5650.json`` (dossier d'état, privé, relu à chaque changement), lisible par les trois passerelles (serveur,
  distribution autonome, ExApp) ; journal des changements dans ``audit``.
"""
from contextvars import ContextVar
from html import escape as e
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs

from .common import Stop, private_json

FILE = 'acces5650.json'
RIGHTS = (('envoyer', 'Envoyer des courriels'), ('deposer', 'Déposer des brouillons dans la messagerie'),
          ('valider', 'Valider les décisions et les productions'), ('agenda', 'Gérer l’agenda et les tâches'),
          ('facturation', 'Facturation et temps passé'), ('reglages', 'Réglages du cabinet'))
RIGHT_IDS = tuple(r for r, _ in RIGHTS)
ROLES = {'administrateur': 'Administrateur', 'avocat': 'Avocat', 'assistant': 'Assistant(e)'}
# Droits de chaque rôle avant tout réglage (mêmes règles que standalone_auth.allowed).
ROLE_DEFAULTS = {'administrateur': {r: True for r in RIGHT_IDS},
                 'avocat': {'envoyer': True, 'deposer': True, 'valider': True, 'agenda': True, 'facturation': True, 'reglages': False},
                 'assistant': {'envoyer': False, 'deposer': False, 'valider': False, 'agenda': True, 'facturation': False, 'reglages': False}}
SEND_API = frozenset({'send/prepare', 'send/confirm', 'send/matter', 'm5628/envoyer', 'm5628/envoyer/confirmer'})
DEPOSIT_API = frozenset({'m5628/brouillon', 'm5625/boite/repondre'})
DEPOSIT_ACTIONS = frozenset({'deposit_draft', 'prepare_reply', 'prepare_draft'})
VALIDATE_API = frozenset({'fact/validate', 'fact/reject', 'deadline/confirm', 'deadline/correct', 'deadline/close', 'm500/limitation/confirm',
                          'm500/limitation/correct', 'm500/limitation/close', 'm500/conflicts/decide', 'm5634/decision/annuler',
                          'm568/profils/approve', 'm568/profils/revoke', 'm568/mission5614/control', 'm568/mission5614/decide',
                          'm568/decision/defer', 'm568/automatismes/pause',
                          'm5644/dossier/corriger', 'm5644/dossier/annuler-correction'}   # 5.6.51 (audit B02)
                         | {'m5651/' + r for r in ('information', 'information/ajouter', 'partie', 'partie/ajouter', 'partie/fusion', 'partie/separer',
                                                    'partie/annuler-fusion', 'procedure', 'decision-cabinet', 'decision-cabinet/retirer', 'file/decider',
                                                    'file/lot', 'relecture/decider', 'relecture/appliquer', 'preparation', 'regle')})
VALIDATE_ACTIONS = frozenset({'approve_cabinet_confirmation_batch', 'approve_cabinet_template', 'approve_project', 'approve_work_plan',
                              'validate_act', 'validate_fact', 'validate_matrix_row', 'validate_memory', 'validate_strategy',
                              'reject_document_project', 'reject_group', 'reject_hearing', 'reject_word_project', 'reject_work_plan',
                              'confirm_matter', 'review_cabinet_decision', 'resolve_conflict', 'trust_document410'})
AGENDA_PREFIXES = ('m5625/agenda/', 'm5634/agenda/', 'm530/event/', 'm568/calendars/')
AGENDA_ACTIONS = frozenset({'create_agenda_event', 'edit_agenda_event', 'cancel_agenda_event', 'edit_personal_event', 'sync_caldav_tasks',
                            'confirm_event'})
BILLING_PREFIXES = ('m500/invoice/', 'm500/time/', 'm568/ninja/')
BILLING_ACTIONS = frozenset({'link_invoice_client', 'billing_review35', 'record_provision'})
# Gestion des comptes et des droits : jamais accordée par un réglage (pas d'élévation de privilège).
ACCOUNT_ROUTES = ('m5636/comptes/', 'm5636/compte/', 'm5650/droits', 'm5650/reserve', 'm5650/restaurer')
MATTER_KEYS = ('matter', 'matter_id', 'dossier', 'mid')
MATTER_ID_PATHS = ('/dossier', '/fiche', '/chronologie', '/pieces')

_viewer = ContextVar('axiorhub_viewer5650', default=None)
_lock = threading.Lock()
_seen = {}


# ------------------------------------------------------------------------------------------------ stockage
# 5.6.51 (audit B05) : une installation jamais configurée n'a aucune restriction ; un registre DÉJÀ ACTIF devenu absent, illisible ou
# invalide n'est jamais lu comme « aucune restriction » : la copie de même version est utilisée si elle existe, sinon le contrôle
# refuse (Stop « controle_acces_indisponible ») et seul l'administrateur garde l'accès (diagnostic et restauration).
MIRROR = 'acces5650.copie.json'
MARKER = 'acces5650.actif'
LOCK = 'acces5650.verrou'


def _path(state_dir):
    return Path(str(state_dir)) / FILE


def _strict(raw):
    """Contrôle complet du schéma : objets attendus, listes de chaînes, vrais booléens (jamais bool("false"))."""
    if not isinstance(raw, dict):
        raise ValueError('objet')
    version = raw.get('version', 0)
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValueError('version')
    droits, reserves = raw.get('droits', {}), raw.get('reserves', {})
    if not isinstance(droits, dict) or not isinstance(reserves, dict):
        raise ValueError('rubriques')
    out_d = {}
    for owner, rights in droits.items():
        if not isinstance(owner, str) or not isinstance(rights, dict):
            raise ValueError('droits')
        for right, value in rights.items():
            if right not in RIGHT_IDS or not isinstance(value, bool):
                raise ValueError('droit')
        out_d[owner] = dict(rights)
    out_r = {}
    for matter, owners in reserves.items():
        if not isinstance(matter, str) or not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            raise ValueError('reserves')
        if owners:
            out_r[matter] = sorted(set(owners))
    return {'version': version, 'droits': out_d, 'reserves': out_r}


def _read(path):
    return _strict(json.loads(path.read_text(encoding='utf-8')))


def _marker(state_dir):
    try:
        return int((Path(str(state_dir)) / MARKER).read_text(encoding='ascii').strip() or '0')
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return -1   # indicateur illisible : registre réputé actif, version inconnue


def load(state_dir):
    """{'version', 'droits': {compte: {droit: bool}}, 'reserves': {dossier: [comptes]}} — relu à chaque appel (petit fichier)."""
    path = _path(state_dir)
    active = _marker(state_dir)
    try:
        record = _read(path)
        if active is None:   # registre de la 5.6.50 (sans indicateur) : désormais réputé actif
            try:
                (Path(str(state_dir)) / MARKER).write_text(str(record['version']), encoding='ascii')
            except OSError:
                pass
        return record
    except FileNotFoundError:
        if active is None:
            return {'version': 0, 'droits': {}, 'reserves': {}}   # jamais configuré
    except (OSError, ValueError, UnicodeError):
        if active is None and not (Path(str(state_dir)) / MIRROR).exists():
            raise Stop('controle_acces_indisponible') from None   # fichier présent mais illisible : jamais « aucune restriction »
    try:   # registre actif : copie de MÊME version seulement (une copie plus ancienne pourrait rouvrir un accès révoqué)
        mirror = _read(Path(str(state_dir)) / MIRROR)
        if active is not None and active >= 0 and mirror['version'] == active:
            return mirror
    except (OSError, ValueError, UnicodeError):
        pass
    raise Stop('controle_acces_indisponible')


def diagnose(state_dir):
    """Pour l'administrateur : état du registre, de sa copie et de l'indicateur."""
    out = {'indicateur': _marker(state_dir)}
    for label, name in (('registre', FILE), ('copie', MIRROR)):
        target = Path(str(state_dir)) / name
        try:
            out[label] = {'etat': 'valide', 'version': _read(target)['version']}
        except FileNotFoundError:
            out[label] = {'etat': 'absent'}
        except (OSError, ValueError, UnicodeError) as ex:
            out[label] = {'etat': 'invalide', 'cause': type(ex).__name__}
    return out


class _Locked:
    """Verrou entre processus (fichier exclusif) autour du cycle lecture–modification–écriture."""

    def __init__(self, state_dir):
        self.path = Path(str(state_dir)) / LOCK

    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:   # verrou abandonné par un processus arrêté
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise Stop('registre_acces_occupe') from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _update(state_dir, change):
    """Modification transactionnelle : lecture stricte, version + 1, registre puis copie puis indicateur."""
    with _Locked(state_dir):
        current = load(state_dir)
        data = change({'droits': dict(current['droits']), 'reserves': dict(current['reserves'])})
        record = _strict({'version': current['version'] + 1, 'droits': data['droits'], 'reserves': data['reserves']})
        private_json(_path(state_dir), record)
        private_json(Path(str(state_dir)) / MIRROR, record)
        marker = Path(str(state_dir)) / MARKER
        marker.write_text(str(record['version']), encoding='ascii')
        try:
            marker.chmod(0o600)
        except OSError:
            pass
        return record


def restore(state_dir, choice):
    """Administrateur : « copie » (dernière copie valide, quelle que soit sa version) ou « vide » (aucune restriction)."""
    with _Locked(state_dir):
        if choice == 'copie':
            record = _read(Path(str(state_dir)) / MIRROR)
        elif choice == 'vide':
            record = {'version': 0, 'droits': {}, 'reserves': {}}
        else:
            raise Stop('restauration_invalide')
        version = max(record['version'], (_marker(state_dir) or 0)) + 1
        record = {**record, 'version': version}
        private_json(_path(state_dir), record)
        private_json(Path(str(state_dir)) / MIRROR, record)
        (Path(str(state_dir)) / MARKER).write_text(str(version), encoding='ascii')
        return record


def effective_rights(state_dir, owner, role):
    base = dict(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS['assistant']))
    if role == 'administrateur':
        return base
    base.update(load(state_dir)['droits'].get(str(owner), {}))
    return base


def set_rights(state_dir, actor, owner, role, values):
    if role not in ('avocat', 'assistant'):
        raise Stop('droits_administrateur_non_modifiables')
    if not owner or owner == actor:
        raise Stop('droits_compte_invalide')
    if any(not isinstance(values[r], bool) for r in RIGHT_IDS if r in values):
        raise Stop('droit_invalide')
    defaults = ROLE_DEFAULTS[role]
    overrides = {r: values[r] for r in RIGHT_IDS if r in values and values[r] != defaults[r]}

    def change(data):
        if overrides:
            data['droits'][owner] = overrides
        else:
            data['droits'].pop(owner, None)
        return data
    _update(state_dir, change)
    return effective_rights(state_dir, owner, role)


def set_reserve(state_dir, matter, owners):
    owners = sorted({str(o).strip() for o in owners or [] if str(o).strip()})[:200]

    def change(data):
        if owners:
            data['reserves'][str(matter)] = owners
        else:
            data['reserves'].pop(str(matter), None)
        return data
    _update(state_dir, change)
    return owners


# ------------------------------------------------------------------------------------------------ droits
def category(method, path, action=''):
    """Droit concerné par une requête (None : aucun droit réglable n'est en jeu)."""
    from .standalone_auth import ADMIN_ACTIONS, ADMIN_API, ADMIN_PATHS, normalize
    path = normalize(path) or ''
    path = path.rstrip('/') or '/'
    api = path[len('/api440/'):] if path.startswith('/api440/') else ''
    if api.startswith(ACCOUNT_ROUTES):
        return 'comptes'
    if (path not in ('/parametres/assistant', '/parametres/proactivite', '/parametres/agents', '/parametres/agendas') and
            (any(path == p or path.startswith(p + '/') for p in ADMIN_PATHS) or path in ('/installation', '/comptes'))):
        return 'reglages'
    if api.startswith(ADMIN_API) or action in ADMIN_ACTIONS:
        return 'reglages'
    if api.startswith('m5653/') and method not in ('GET', 'HEAD'):   # 5.6.53 : facturation et honoraires
        route5653 = api[len('m5653/'):].split('?', 1)[0]
        if route5653 in ('cabinet/enregistrer', 'honoraires/reglages', 'logo/importer', 'bibliotheque/importer'):
            return 'reglages'
        if route5653 not in ('estimation/calculer', 'epingler'):
            return 'facturation'
    if api.startswith('m5667/corrections/'):   # 5.6.67 : correction récurrente → règle (comme les règles « toujours faire comme ça »)
        return None
    if api.startswith('m5667/') and method not in ('GET', 'HEAD'):   # 5.6.67 : modèles et banc d'essai = réglage du cabinet
        return 'reglages'
    if api.startswith('m5662/') and method not in ('GET', 'HEAD'):   # 5.6.62 : connexion ChatGPT = réglage du cabinet
        return 'reglages'
    if api.startswith('m5661/file/'):   # 5.6.61 : Relancer / Écarter un travail de l'agent (comme m567/mission/control)
        return None
    if api.startswith('m5661/') and method not in ('GET', 'HEAD'):   # 5.6.61 : mise à jour = réglage du cabinet
        return 'reglages'
    if api.startswith('m5659/') and method not in ('GET', 'HEAD'):   # 5.6.59 : droit déclaré par route
        from .web5659 import CATEGORIES as _cat5659
        cat5659 = _cat5659.get(api[len('m5659/'):].split('?', 1)[0])
        if cat5659:
            return cat5659
    if api.startswith('m5657/') and method not in ('GET', 'HEAD'):   # 5.6.57 : droit déclaré par route
        from .web5657 import CATEGORIES as _cat5657
        cat5657 = _cat5657.get(api[len('m5657/'):].split('?', 1)[0])
        if cat5657:
            return cat5657
    if api.startswith('m5652/'):                             # 5.6.53 (F08) : capacité métier déclarée par route
        cat5652 = M5652_CATEGORIES.get(api[len('m5652/'):].split('?', 1)[0])
        if cat5652:
            return cat5652
    if method in ('GET', 'HEAD'):
        return None
    if api in SEND_API:
        return 'envoyer'
    if api in DEPOSIT_API or action in DEPOSIT_ACTIONS:
        return 'deposer'
    if api in VALIDATE_API or action in VALIDATE_ACTIONS:
        return 'valider'
    if api.startswith(AGENDA_PREFIXES) or action in AGENDA_ACTIONS:
        return 'agenda'
    if (api.startswith(BILLING_PREFIXES) and api != 'm500/time/bareme') or action in BILLING_ACTIONS:
        return 'facturation'
    return None


M5652_CATEGORIES = {'connexion/enregistrer': 'reglages', 'connexion/secret': 'reglages', 'connexion/effacer-secret': 'reglages',
                    'connexion/deconnecter': 'reglages', 'connexion/capacites': 'reglages', 'connexion/oauth/debut': 'reglages',
                    'file/decider': 'valider', 'operation/valider': 'operation', 'operation/reprendre': 'operation'}


def allowed(state_dir, owner, role, method, path, action=''):
    """Règle du rôle (standalone_auth.allowed), puis droit EFFECTIF du compte (5.6.51, audit B02 : un droit faux refuse, qu'il vienne
    du rôle ou d'un réglage ; un droit accordé au-delà du rôle n'ouvre qu'une route de travail connue, jamais la gestion des comptes).
    Registre des accès illisible : refus pour tout compte qui n'est pas administrateur (audit B05)."""
    from .standalone_auth import allowed as role_allowed
    base = role_allowed(role, method, path, action)
    if role == 'administrateur' or not owner or not state_dir:
        return base
    cat = category(method, path, action)
    if cat in (None, 'comptes'):
        return base
    try:
        rights = effective_rights(state_dir, owner, role)
    except Stop:
        return False
    if cat == 'operation':                                  # le service contrôle ensuite le droit précis (envoyer ou facturation)
        if not (rights.get('envoyer') or rights.get('facturation')):
            return False
        return bool(base)
    if not rights.get(cat):
        return False
    if base:
        return True
    return role_allowed('avocat', method, path, action) or (cat == 'reglages' and role_allowed('administrateur', method, path, action))


# ------------------------------------------------------------------------------------------------ dossiers réservés
def hidden_for(state_dir, owner, role):
    if role == 'administrateur' or not state_dir:
        return frozenset()
    reserves = load(state_dir)['reserves']
    return frozenset(m for m, owners in reserves.items() if str(owner) not in owners)


def viewer_hidden():
    """Dossiers masqués pour l'utilisateur de la requête en cours (ensemble vide hors requête : travaux de l'agent)."""
    value = _viewer.get()
    return value if value else frozenset()


def set_viewer(hidden):
    _viewer.set(frozenset(hidden) if hidden else None)


def clear():
    _viewer.set(None)


INDIRECT_KEYS = ('lien', 'link_id', 'request', 'mission', 'mission_id', 'project', 'task', 'job', 'job_id')
PATH_KEYS = ('path', 'chemin', 'fichier', 'document', 'source_path')
INDIRECT_SQL = {
    'lien': ("SELECT matter_id FROM mail_archive_links WHERE id=?",),
    'link_id': ("SELECT matter_id FROM mail_archive_links WHERE id=?",),
    'request': ("SELECT matter FROM docreq520 WHERE id=?",),
    'mission': ("SELECT matter FROM missions_v567 WHERE id=?", "SELECT matter FROM missions5614 WHERE id=?"),
    'mission_id': ("SELECT matter FROM missions_v567 WHERE id=?", "SELECT matter FROM missions5614 WHERE id=?"),
    'project': ("SELECT matter FROM document_projects_v220 WHERE id=?",),
    'task': ("SELECT matter FROM work_tasks_v211 WHERE id=?",),
}
# 5.6.52 (audit 5.6.51, F01) : objets désignés par un identifiant propre à une route ; leur dossier réel est lu sur le serveur, jamais
# déduit du dossier déclaré par le navigateur. Une règle de style (sans dossier) relève du contrôle de propriétaire du service.
ROUTE_OBJECTS = {
    'm5651/regle': (('id', 'SELECT matter FROM d51_learned WHERE id=?'),),
    'm5651/information': (('id', 'SELECT matter FROM d51_assertions WHERE id=?'), ('avec', 'SELECT matter FROM d51_assertions WHERE id=?')),
    'm5651/partie': (('id', 'SELECT matter FROM d51_parties WHERE id=?'),),
    'm5651/partie/fusion': (('a', 'SELECT matter FROM d51_parties WHERE id=?'), ('b', 'SELECT matter FROM d51_parties WHERE id=?')),
    'm5651/partie/separer': (('a', 'SELECT matter FROM d51_parties WHERE id=?'), ('b', 'SELECT matter FROM d51_parties WHERE id=?')),
    'm5651/partie/annuler-fusion': (('decision', 'SELECT matter FROM d51_party_decisions WHERE id=?'),),
    'm5651/procedure': (('id', 'SELECT matter FROM d51_procedures WHERE id=?'),),
    'm5651/decision-cabinet': (('remplace', 'SELECT matter FROM d51_cabinet_decisions WHERE id=?'),),
    'm5651/decision-cabinet/retirer': (('id', 'SELECT matter FROM d51_cabinet_decisions WHERE id=?'),),
    'm5651/lacune': (('id', 'SELECT matter FROM d51_gaps WHERE id=?'),),
    'm5651/relecture/decider': (('id', 'SELECT matter FROM d51_proposals WHERE id=?'),),
    'm5651/relecture/appliquer': (('session', 'SELECT matter FROM d51_review_sessions WHERE id=?'),),
    'm5651/file/decider': (('key', 'SELECT matter FROM d51_queue WHERE key=?'),),
    **{'m5652/routine/' + r: (('id', 'SELECT matter_scope FROM routines5652 WHERE id=?'),)
       for r in ('modifier', 'activer', 'dupliquer', 'supprimer', 'lancer', 'historique')},
    'm5652/outil/appeler': (('operation', 'SELECT matter FROM connexion_ops5652 WHERE id=?'),),
    # 5.6.65 (audit 5.6.64, R12) : une ressource de savoir-faire tirée d'un dossier réservé, désignée par son identifiant
    **{'m5659/savoir/' + r: (('resource', "SELECT origin_matter FROM s59_versions WHERE resource=? AND origin_matter<>'' ORDER BY version DESC LIMIT 1"),)
       for r in ('proposer', 'decider', 'retirer')},
    'm5652/operation/valider': (('id', 'SELECT matter FROM connexion_ops5652 WHERE id=?'),),
    'm5652/operation/annuler': (('id', 'SELECT matter FROM connexion_ops5652 WHERE id=?'),),
}


def route_objects(state_dir, path, args):
    """Dossiers réels des objets désignés par identifiant dans une route connue (lecture seule ; objet inconnu : rien)."""
    api = str(path or '').split('/api440/', 1)[-1].split('?', 1)[0].rstrip('/') if '/api440/' in str(path or '') else ''
    specs = ROUTE_OBJECTS.get(api)
    if not specs:
        return set()
    import sqlite3
    found = set()
    try:
        db = sqlite3.connect('file:%s?mode=ro' % (Path(str(state_dir)) / 'desk.sqlite3').as_posix(), uri=True, timeout=2)
    except sqlite3.Error:
        return found
    try:
        for key, sql in specs:
            value = str(args.get(key) or '')
            if not value:
                continue
            try:
                row = db.execute(sql, (value,)).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                for m in str(row[0]).split(','):
                    if m.strip():
                        found.add(m.strip())
    finally:
        db.close()
    return found


def _request_values(env, path, args):
    """(clé, valeur) de la requête : dossier désigné directement, objets indirects et chemins (requête et corps JSON ou formulaire)."""
    keys = MATTER_KEYS + INDIRECT_KEYS + PATH_KEYS
    pairs = [(k, str(args.get(k) or '')) for k in keys]
    if path.rstrip('/') in MATTER_ID_PATHS:
        pairs.append(('matter', str(args.get('id') or '')))
    if 'mission' in path:
        pairs.append(('mission', str(args.get('id') or '')))
    method = env.get('REQUEST_METHOD', 'GET')
    if method in ('POST', 'PUT', 'PATCH'):
        ctype = str(env.get('CONTENT_TYPE') or '').split(';')[0].strip()
        try:
            length = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        if ctype in ('application/json', 'application/x-www-form-urlencoded') and 0 < length <= 2_000_000:
            from io import BytesIO
            raw = env['wsgi.input'].read(length)
            env['wsgi.input'] = BytesIO(raw)
            try:
                if ctype == 'application/json':
                    data = json.loads(raw.decode('utf-8') or '{}')
                    if isinstance(data, dict):
                        pairs += [(k, str(data.get(k) or '')) for k in keys if not isinstance(data.get(k), (dict, list))]
                else:
                    form = parse_qs(raw.decode('utf-8', 'replace'), max_num_fields=400)
                    pairs += [(k, str((form.get(k) or [''])[0])) for k in keys]
            except (ValueError, UnicodeError):
                pass
    return [(k, v) for k, v in pairs if v]


def _matters_of(state_dir, pairs, matter_paths):
    """Dossiers désignés par la requête, objets indirects résolus sur le serveur (jamais d'après le navigateur)."""
    from .common import clean_path, under
    found = {v for k, v in pairs if k in MATTER_KEYS}
    lookups = [(k, v) for k, v in pairs if k in INDIRECT_KEYS]
    if lookups:
        import sqlite3
        db = sqlite3.connect('file:%s?mode=ro' % (Path(str(state_dir)) / 'desk.sqlite3').as_posix(), uri=True, timeout=2)
        try:
            for key, value in lookups:
                if key in ('job', 'job_id'):
                    try:
                        row = db.execute('SELECT args FROM jobs WHERE id=?', (value,)).fetchone()
                        if row:
                            found.add(str((json.loads(row[0] or '{}') or {}).get('matter') or ''))
                    except (sqlite3.Error, ValueError):
                        pass
                    continue
                for sql in INDIRECT_SQL.get(key, ()):
                    try:
                        row = db.execute(sql, (value,)).fetchone()
                    except sqlite3.Error:
                        continue
                    if row and row[0]:
                        found.add(str(row[0]))
        finally:
            db.close()
    paths = [v for k, v in pairs if k in PATH_KEYS]
    if paths and matter_paths:
        for value in paths:
            try:
                target = clean_path(value)
            except Stop:
                continue
            for mid, root in matter_paths().items():
                if root and under(target, clean_path(root)):
                    found.add(mid)
    found.discard('')
    return found


def gate(env, state_dir, path, args, matter_paths=None):
    """Contrôle central (toutes versions) : présence du membre, dossiers masqués de la requête, refus d'une requête qui désigne un
    dossier réservé — directement ou par un objet indirect (lien d'archive, demande, mission, projet, tâche, travail, chemin).
    Renvoie None (requête autorisée) ou le code d'erreur ; registre illisible : Stop (refus, sauf administrateur)."""
    from .web567 import actor
    owner, role = actor(env)
    hidden = hidden_for(state_dir, owner, role)
    set_viewer(hidden)
    _presence(state_dir, owner, role, env)
    if hidden and (_matters_of(state_dir, _request_values(env, path, args), matter_paths) | route_objects(state_dir, path, args)) & hidden:
        return 'dossier_reserve'
    return None


def _presence(state_dir, owner, role, env):
    """Annuaire des membres vus (au plus une écriture toutes les 5 minutes par membre)."""
    key = (str(state_dir), owner, role)
    now = time.time()
    if _seen.get(key, 0) > now - 300:
        return
    _seen[key] = now
    user = env.get('axiorhub.user') or {}
    name = str(user.get('name') or env.get('HTTP_X_AXIORHUB_NAME') or '')[:120]
    try:
        import sqlite3
        db = sqlite3.connect(str(Path(state_dir) / 'desk.sqlite3'), timeout=0.3)
        try:
            db.execute(SCHEMA)
            db.execute('INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
                       "role=excluded.role,last_seen=excluded.last_seen,name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE membres5650.name END",
                       (owner, name, role, _iso(), _iso()))
            db.commit()
        finally:
            db.close()
    except Exception:   # noqa: BLE001 — la présence est facultative (base occupée : écriture sautée)
        _seen.pop(key, None)


def _iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


SCHEMA = ('CREATE TABLE IF NOT EXISTS membres5650(owner TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT \'\', role TEXT NOT NULL DEFAULT \'avocat\', '
          'first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)')


# ------------------------------------------------------------------------------------------------ annuaire
def directory(desk, auth=None):
    """Membres du cabinet : {owner: {'owner', 'name', 'role', 'source'}} — comptes de la version serveur, de la distribution autonome,
    puis membres vus (Nextcloud, ExApp)."""
    out = {}
    desk.db.execute(SCHEMA)
    for r in desk.db.execute('SELECT owner,name,role FROM membres5650'):
        out[r['owner']] = {'owner': r['owner'], 'name': r['name'] or r['owner'], 'role': r['role'], 'source': 'vu'}
    auth = auth or {}
    try:
        from . import connexion5648
        if connexion5648.enabled(auth):
            out['cabinet'] = {'owner': 'cabinet', 'name': str(auth.get('username') or 'admin'), 'role': 'administrateur', 'source': 'compte'}
            db = connexion5648.connect(desk.c['state_dir'])
            try:
                for u in connexion5648.list_users(db):
                    if u['active']:
                        out[u['ident']] = {'owner': u['ident'], 'name': u['name'] or u['ident'], 'role': u['role'], 'source': 'compte'}
                    else:
                        out.pop(u['ident'], None)
            finally:
                db.close()
    except Exception:   # noqa: BLE001
        pass
    try:
        from .comptes5642 import standalone
        if standalone():
            import sqlite3
            from .missions567 import auth_root
            db = sqlite3.connect(str(auth_root() / 'users.sqlite3'), timeout=2)
            db.row_factory = sqlite3.Row
            try:
                for u in db.execute('SELECT email,name,role,active FROM users'):
                    if u['active']:
                        out[u['email']] = {'owner': u['email'], 'name': u['name'] or u['email'], 'role': u['role'], 'source': 'compte'}
                    else:
                        out.pop(u['email'], None)
            finally:
                db.close()
    except Exception:   # noqa: BLE001
        pass
    return out


def label(member):
    return '%s (%s)' % (member.get('name') or member.get('owner'), ROLES.get(member.get('role'), member.get('role') or ''))


# ------------------------------------------------------------------------------------------------ page Paramètres › Droits et accès
def page_html(desk, auth, prefix, args):
    state = desk.c['state_dir']
    known = directory(desk, auth)
    members = sorted(known.values(), key=lambda m: (m['role'] != 'administrateur', (m['name'] or '').lower()))
    cards = []
    for m in members:
        head = '<h3>%s <small>%s · %s</small></h3>' % (e(m['name'] or m['owner']), e(ROLES.get(m['role'], m['role'])), e(m['owner']))
        if m['role'] == 'administrateur':
            cards.append('<article class="m50-member">%s<p class="ax-muted">Tous les droits (administrateur).</p></article>' % head)
            continue
        rights = effective_rights(state, m['owner'], m['role'])
        defaults = ROLE_DEFAULTS.get(m['role'], ROLE_DEFAULTS['assistant'])
        checks = ''.join('<label class="m50-check"><input type="checkbox" name="%s"%s> %s%s</label>' % (
            r, ' checked' if rights[r] else '', e(lbl), ' <small>(réglage propre à ce compte)</small>' if rights[r] != defaults[r] else '')
            for r, lbl in RIGHTS)
        cards.append('<article class="m50-member">%s<form class="m5-form" data-api="m5650/droits" data-reload>'
                     '<input type="hidden" name="owner" value="%s"><input type="hidden" name="role" value="%s"><div class="m50-checks">%s</div>'
                     '<button class="ax-btn ghost" type="submit">Enregistrer les droits</button><p role="status" class="m5-status"></p></form></article>' % (
                         head, e(m['owner'], quote=True), e(m['role'], quote=True), checks))
    reserves = load(state)['reserves']
    from .common import load_matters, matter_display
    names = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    res_rows = ''.join('<li><strong>%s</strong> — réservé à : %s · <a href="%s">Modifier</a></li>' % (
        e(names.get(mid, mid)), e(', '.join(label(known.get(o, {'owner': o})) for o in owners)),
        e(prefix + '/dossier?id=' + mid + '#acces-dossier', quote=True)) for mid, owners in sorted(reserves.items()))
    return ('<h1>Droits et accès</h1>'
            '<p class="ax-muted">Chaque compte reçoit les droits de son rôle ; vous pouvez en accorder ou en retirer compte par compte. Un administrateur '
            'garde tous ses droits ; la gestion des comptes et des droits lui reste réservée.</p>'
            '<section class="ax-card"><h2>Droits par compte</h2><div class="m50-members">%s</div>'
            '<p class="ax-muted">Un membre apparaît ici dès la création de son compte (page de connexion, distribution autonome) ou à sa première '
            'connexion (application Nextcloud).</p></section>'
            '<section class="ax-card" id="dossiers-reserves"><h2>Dossiers réservés</h2>'
            '<p class="ax-muted">Un dossier réservé n’est visible que des comptes choisis et des administrateurs : listes, recherches, assistant, '
            'messagerie. La réservation se règle dans la page du dossier (rubrique « Accès au dossier »).</p>%s</section>') % (
        ''.join(cards) or '<p>Aucun membre connu pour l’instant.</p>',
        ('<ul>%s</ul>' % res_rows) if res_rows else '<p>Aucun dossier réservé : tous les comptes voient tous les dossiers.</p>')


def access_card(desk, auth, prefix, mid, role):
    """Page du dossier : rubrique « Accès au dossier » (administrateur)."""
    if role != 'administrateur':
        return ''
    owners = load(desk.c['state_dir'])['reserves'].get(mid, [])
    members = [m for m in directory(desk, auth).values() if m['role'] != 'administrateur']
    checks = ''.join('<label class="m50-check"><input type="checkbox" name="%s"%s> %s</label>' % (
        e('compte:' + m['owner'], quote=True), ' checked' if m['owner'] in owners else '', e(label(m)))
        for m in sorted(members, key=lambda m: (m['name'] or '').lower()))
    return ('<section class="ax-card" id="acces-dossier"><h2>Accès au dossier</h2><p class="ax-muted">%s</p>'
            '<form class="m5-form" data-api="m5650/reserve" data-reload><input type="hidden" name="matter" value="%s"><div class="m50-checks">%s</div>'
            '<button class="ax-btn ghost" type="submit">Enregistrer l’accès</button><p role="status" class="m5-status"></p></form></section>') % (
        e('Réservé aux comptes cochés et aux administrateurs.' if owners else 'Tous les comptes du cabinet voient ce dossier. Cochez des comptes pour le leur réserver.'),
        e(mid, quote=True), checks or '<p>Aucun compte avocat ou assistant(e) connu.</p>')


def _slug(value):
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(value))[:60]
