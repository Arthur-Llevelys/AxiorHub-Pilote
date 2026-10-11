"""5.6.31 (cahier des charges additionnel, R00 / R01 / R02 — GI01 à GI08, GI31 à GI36) : guide d'installation pas à pas.

Le guide est un contrôleur au-dessus des services existants ; il ne crée pas une seconde configuration :
- les réglages sont validés et publiés par ``config567`` (mêmes règles, même révision, même coffre que Paramètres) ;
- le profil de l'avocat passe par ``pieces510.save_profile`` ; la langue par ``i18n5631`` ;
- une session de guide par propriétaire (table ``guide_sessions5631``) conserve étape, fonctions choisies et valeurs candidates SANS secret ;
  un secret saisi est chiffré aussitôt dans un coffre propre à la session et remplacé par une référence ;
- les tests portent sur la configuration CANDIDATE (contexte temporaire), jamais sur un réglage enregistré « pour essayer » ;
- chaque preuve (table ``connection_receipts5631``) vise une connexion et une capacité, avec l'empreinte des seuls champs utiles et la
  version du secret : modifier le SMTP ne périme pas la preuve IMAP ; changer le mot de passe IMAP la périme ;
- la publication vérifie la révision de la session ET celle de la configuration : un autre écran qui a modifié un réglage provoque un
  conflit ciblé (409) au lieu d'un écrasement silencieux ;
- terminer n'exige que les capacités des fonctions choisies (R00) ; une fin partielle liste les fonctions encore indisponibles.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

from .common import Stop, read_secret
from . import i18n5631 as i18n

GUIDE_VERSION = 1
FEATURES = ('documents', 'messagerie', 'agenda', 'recherches', 'veille', 'facturation', 'telephonie')
# Capacités nécessaires à chaque fonction (R00 étape 2). « ai.generate » et « storage.read » suffisent à l'analyse documentaire.
REQUIRES = {'documents': ('storage.read', 'ai.generate'), 'messagerie': ('mail.read', 'ai.generate'), 'agenda': ('calendar.read',),
            'recherches': (), 'veille': (), 'facturation': ('invoice.read',), 'telephonie': ()}
# Étapes : identifiant stable, fonctions qui l'affichent (vide = toujours).
STEPS = (('fonctions', ()), ('profil', ()), ('coordonnees', ()), ('langues', ()),
         ('stockage', ('documents',)), ('ia', ('documents', 'messagerie', 'recherches')),
         ('messagerie', ('messagerie',)), ('envoi', ('messagerie',)), ('agenda', ('agenda',)), ('sources', ('recherches',)),
         ('modeles', ('documents',)), ('veille', ('veille',)), ('facturation', ('facturation',)), ('telephonie', ('telephonie',)),
         ('voix', ()), ('fin', ()))   # 5.6.34 (B17) : synthèse vocale Kokoro selon le système
# Champs : nom, destination (clé config567, profil ou préférence), type, obligatoire. « Plus de détails » : avancé=True.
FIELDS = {
    'profil': (('prenom', 'profile.prenom_avocat', 'text', True, False), ('nom', 'profile.nom_avocat', 'text', True, False),
               ('cabinet', 'pref.cabinet', 'text', False, False), ('barreau', 'profile.ville_avocat', 'text', True, False),
               ('toque', 'profile.numero_toque_avocat', 'text', False, False)),
    'coordonnees': (('adresse', 'profile.adresse_cabinet_avocat', 'text', False, False), ('email', 'profile.email_avocat', 'email', False, False),
                    ('telephone', 'profile.numero_telephone_avocat', 'text', False, False)),
    'langues': (('langue', 'pref.langue', 'lang', True, False), ('fuseau', 'pref.fuseau', 'timezone', True, False),
                ('redaction', 'pref.langue_redaction', 'lang', True, False), ('domaines', 'pref.domaines', 'domains', False, False)),
    'stockage': (('mode', 'pref.stockage', 'storage_mode', True, False), ('local_path', 'nextcloud.local_path', 'local_dir', False, False),
                 ('url', 'nextcloud.url', 'url', False, False), ('username', 'nextcloud.username', 'text', False, False),
                 ('password', 'nextcloud.password_file', 'secret', False, False), ('racine', 'nextcloud.roots', 'paths', False, False)),
    'ia': (('ollama_url', 'ollama.url', 'local_url', False, False), ('modele_rapide', 'model_routing.fast_model', 'model', False, False),
           ('modele_analyse', 'ollama.model', 'model', False, False)),
    'messagerie': (('host', 'mail.host', 'host', True, False), ('username', 'mail.username', 'text', True, False),
                   ('password', 'mail.password_file', 'secret', False, False), ('port', 'mail.port', 'port', False, True),
                   ('security', 'mail.security', 'imap_security', False, True), ('inbox', 'mail.inbox', 'text', False, True),
                   ('drafts', 'mail.drafts', 'text', False, True), ('sent', 'mail.sent', 'text', False, True),
                   ('from_address', 'mail.from_address', 'email', False, False)),
    'envoi': (('enabled', 'smtp.enabled', 'bool', False, False), ('host', 'smtp.host', 'host', False, False),
              ('username', 'smtp.username', 'text', False, False), ('password', 'smtp.password_file', 'secret', False, False),
              ('port', 'smtp.port', 'port', False, True), ('security', 'smtp.security', 'smtp_security', False, True),
              ('from_address', 'smtp.from_address', 'email', False, True)),
    # 5.6.32 : étapes réelles (champs + vérification) pour les agendas, la facturation et la téléphonie
    'agenda': (('urls', 'calendar.urls', 'urls', False, False), ('task_urls', 'calendar.task_urls', 'urls', False, False),
               ('google_client_id', 'google_calendar568.client_id', 'text', False, True),
               ('google_secret', 'google_calendar568.client_secret_file', 'secret', False, True),
               ('google_redirect', 'google_calendar568.redirect_uri', 'url', False, True)),
    'facturation': (('enabled', 'invoice_ninja.enabled', 'bool', False, False), ('url', 'invoice_ninja.base_url', 'url', False, False),
                    ('token', 'invoice_ninja.api_token_file', 'secret', False, False), ('company', 'invoice_ninja.company_id', 'text', False, True)),
    'telephonie': (('callback', 'reception.twilio_callback_url', 'url', False, False), ('token', 'reception.twilio_auth_token_file', 'secret', False, False)),
}
# Connexions testables : champs dont dépend la preuve (empreinte ciblée, GI35).
CONNECTORS = {'stockage': ('nextcloud.url', 'nextcloud.username', 'nextcloud.password_file', 'nextcloud.local_path', 'nextcloud.roots'),
              'ia': ('ollama.url', 'ollama.model', 'model_routing.fast_model'),
              'messagerie': ('mail.host', 'mail.port', 'mail.security', 'mail.username', 'mail.password_file', 'mail.inbox', 'mail.drafts', 'mail.sent'),
              'envoi': ('smtp.host', 'smtp.port', 'smtp.security', 'smtp.username', 'smtp.password_file', 'smtp.from_address'),
              'agenda': ('calendar.urls', 'calendar.task_urls', 'nextcloud.url', 'nextcloud.username', 'nextcloud.password_file'),
              'facturation': ('invoice_ninja.enabled', 'invoice_ninja.base_url', 'invoice_ninja.api_token_file', 'invoice_ninja.company_id'),
              'telephonie': ('reception.twilio_callback_url', 'reception.twilio_auth_token_file')}
CAPABILITIES = {'stockage': ('read', 'write'), 'ia': ('service', 'model', 'generate'), 'messagerie': ('read', 'folders'), 'envoi': ('auth',),
                'agenda': ('read',), 'facturation': ('read',), 'telephonie': ('signature',)}
CAPABILITY_NAMES = {('stockage', 'read'): 'storage.read', ('stockage', 'write'): 'storage.write', ('ia', 'generate'): 'ai.generate',
                    ('ia', 'service'): 'ai.service', ('ia', 'model'): 'ai.model', ('messagerie', 'read'): 'mail.read',
                    ('messagerie', 'folders'): 'mail.folders', ('envoi', 'auth'): 'mail.send.auth',
                    ('agenda', 'read'): 'calendar.read', ('facturation', 'read'): 'invoice.read', ('telephonie', 'signature'): 'phone.signature'}
VALIDITY = {'generate': timedelta(days=1)}
DEFAULT_VALIDITY = timedelta(days=7)
TIMEZONES = ('Europe/Paris', 'Europe/Brussels', 'Europe/Luxembourg', 'Europe/Zurich', 'Europe/Berlin', 'Europe/Madrid', 'Europe/London',
             'America/Martinique', 'America/Guadeloupe', 'America/Cayenne', 'Indian/Reunion', 'Indian/Mayotte', 'Pacific/Noumea', 'Pacific/Tahiti', 'UTC')
DOMAINS = ('penal', 'travail', 'affaires', 'baux', 'immobilier', 'famille', 'execution', 'fiscal')
SESSION_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def _schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS guide_sessions5631(id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL,
      base_config_revision INTEGER NOT NULL, guide_version INTEGER NOT NULL, locale TEXT NOT NULL, features TEXT NOT NULL,
      step TEXT NOT NULL, status TEXT NOT NULL, candidate TEXT NOT NULL, base TEXT NOT NULL, secret_refs TEXT NOT NULL,
      skipped TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS guide_sessions5631_owner ON guide_sessions5631(owner, status);
    CREATE TABLE IF NOT EXISTS connection_receipts5631(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
      owner TEXT NOT NULL, connector TEXT NOT NULL, capability TEXT NOT NULL, config_hash TEXT NOT NULL, secret_version TEXT NOT NULL,
      status TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, evidence TEXT NOT NULL, checked_at TEXT NOT NULL,
      valid_until TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES guide_sessions5631(id));
    CREATE INDEX IF NOT EXISTS connection_receipts5631_session ON connection_receipts5631(session_id, connector, capability);
    ''')


def _get(cfg, key, default=None):
    node = cfg
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _set(cfg, key, value):
    parts = key.split('.')
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _vault_dir(desk, sid):
    return Path(desk.c['state_dir']) / 'vault' / 'guide5631' / sid


def _row(desk, sid, owner):
    _schema(desk)
    row = desk.db.execute('SELECT * FROM guide_sessions5631 WHERE id=?', (str(sid or ''),)).fetchone()
    if not row:
        raise Stop('session_guide_absente')
    if row['owner'] != owner:
        raise Stop('session_guide_autre_utilisateur')   # GI36 : un autre utilisateur ne lit ni n'applique la session
    return row


def _load(row):
    out = dict(row)
    for key in ('features', 'candidate', 'base', 'secret_refs', 'skipped'):
        out[key] = json.loads(out[key] or ('[]' if key in ('features', 'skipped') else '{}'))
    return out


def _config_revision(desk):
    path = desk.c.get('_path')
    try:
        return int(json.loads(Path(path).read_text(encoding='utf-8')).get('config_revision567', 0)) if path else int(desk.c.get('config_revision567', 0))
    except (OSError, ValueError):
        return int(desk.c.get('config_revision567', 0))


def steps_for(features):
    chosen = set(features or ())
    return [sid for sid, needs in STEPS if not needs or chosen & set(needs)]


def _expire(desk):
    """Sessions abandonnées : leurs secrets candidats sont effacés, la configuration active n'est pas touchée (GI34)."""
    _schema(desk)
    limit = (_now() - timedelta(days=SESSION_DAYS)).isoformat()
    for row in desk.db.execute("SELECT id FROM guide_sessions5631 WHERE status='in_progress' AND updated<?", (limit,)).fetchall():
        shutil.rmtree(_vault_dir(desk, row['id']), ignore_errors=True)
        desk.db.execute("UPDATE guide_sessions5631 SET status='expired', secret_refs='{}' WHERE id=?", (row['id'],))
    desk.db.commit()


def public(desk, data):
    """Vue publique d'une session : valeurs candidates sans secret, état « renseigné » des secrets, progression et preuves."""
    steps = steps_for(data['features'])
    step = data['step'] if data['step'] in steps else steps[0]
    return {'id': data['id'], 'revision': data['revision'], 'base_config_revision': data['base_config_revision'], 'locale': data['locale'],
            'features': data['features'], 'step': step, 'status': data['status'], 'steps': steps, 'position': steps.index(step) + 1,
            'total': len(steps), 'skipped': data['skipped'], 'values': data['candidate'],
            'secrets': {k: 'configured' for k in data['secret_refs']},
            'receipts': receipts(desk, data)}


def start(desk, owner, features=None, mode='nouveau', lang=None):
    """Crée (ou reprend) la session de l'utilisateur connecté ; le propriétaire vient toujours de la session d'authentification."""
    _expire(desk)
    if mode != 'nouveau':
        row = desk.db.execute("SELECT * FROM guide_sessions5631 WHERE owner=? AND status='in_progress' ORDER BY updated DESC LIMIT 1", (owner,)).fetchone()
        if row:
            return public(desk, _load(row))
    chosen = [f for f in (features or ()) if f in FEATURES]
    if mode == 'verifier' and not chosen:
        from .fonctions5631 import capabilities
        caps = capabilities(desk.c)
        chosen = [f for f, cap in (('documents', 'storage'), ('messagerie', 'mail'), ('agenda', 'calendar'), ('facturation', 'invoice'),
                                   ('telephonie', 'telephony')) if cap in caps]
    sid = 'guide-' + secrets.token_hex(8)
    now = _now().isoformat()
    for row in desk.db.execute("SELECT id FROM guide_sessions5631 WHERE owner=? AND status='in_progress'", (owner,)).fetchall():
        _cancel_row(desk, row['id'])
    desk.db.execute('INSERT INTO guide_sessions5631 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (sid, owner, 1, _config_revision(desk), GUIDE_VERSION, i18n.clean(lang) or i18n.current(), json.dumps(chosen), 'fonctions',
                     'in_progress', '{}', '{}', '{}', '[]', now, now))
    desk.db.commit()
    desk.audit('guide5631_session_creee', {'session': sid, 'features': chosen})
    return public(desk, _load(desk.db.execute('SELECT * FROM guide_sessions5631 WHERE id=?', (sid,)).fetchone()))


def session(desk, owner, sid):
    return public(desk, _load(_row(desk, sid, owner)))


def current_value(desk, target, prof=None):
    """Valeur actuelle d'un champ (configuration active, profil ou préférence) ; jamais un secret."""
    kind, _, key = target.partition('.')
    if kind == 'profile':
        if prof is None:
            from .pieces510 import profile
            prof = profile(desk)
        return prof.get(key, '')
    if kind == 'pref':
        owner = getattr(desk, '_guide_owner', '')
        if key == 'langue':
            return i18n.preference(desk.c, owner) or i18n.current()
        if key == 'stockage':
            nc = desk.c.get('nextcloud') or {}
            return 'local' if nc.get('local_path') else ('nextcloud' if 'example.com' not in str(nc.get('url', 'example.com')) else '')
        default = {'fuseau': 'Europe/Paris', 'langue_redaction': 'fr'}.get(key, '')
        return desk.settings('cabinet5631:' + key, default) or default
    value = _get(desk.c, target, '')
    if target.endswith('_file'):
        return ''
    if isinstance(value, str) and 'example.com' in value:
        return ''
    return value


def _validate(desk, step, name, target, kind, value):
    from . import config567
    if kind == 'secret':
        value = str(value or '').strip()
        if value and (len(value) > 4096 or any(c in value for c in '\n\r\0')):
            raise Stop('secret_invalide')
        return value
    if kind == 'lang':
        if not i18n.clean(value):
            raise Stop('langue_inconnue')
        return i18n.clean(value)
    if kind == 'timezone':
        value = str(value or '').strip()
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(value)
        except Exception:
            raise Stop('fuseau_inconnu') from None
        return value
    if kind == 'domains':
        values = value if isinstance(value, list) else [v for v in str(value or '').split(',')]
        out = [str(v).strip()[:60] for v in values if str(v).strip()]
        return out[:20]
    if kind == 'storage_mode':
        if value not in ('local', 'nextcloud', 'webdav', 'plus_tard'):
            raise Stop('choix_stockage_invalide')
        return value
    if kind == 'model':
        value = str(value or '').strip()
        if len(value) > 160 or any(ord(c) < 32 for c in value):
            raise Stop('reglage_texte_invalide')
        if value.endswith('-cloud') or ':cloud' in value:
            raise Stop('modele_cloud_refuse')
        return value
    if kind == 'text' and target.startswith(('profile.', 'pref.')):
        value = str(value or '').strip()
        if len(value) > 200 or any(ord(c) < 32 for c in value):
            raise Stop('reglage_texte_invalide')
        return value
    if kind == 'local_url' and not str(value or '').strip():
        return ''
    if kind in ('port',) and str(value or '').strip() == '':
        return ''
    if kind == 'email' and not str(value or '').strip():
        return ''
    if kind == 'paths' and isinstance(value, str):
        value = [value] if value.strip() else []
    if kind == 'urls' and isinstance(value, str):   # 5.6.32 : une adresse par ligne ou séparées par des virgules
        import re as _re
        value = [v for v in _re.split(r'[\s,]+', value) if v]
    if kind == 'url' and not str(value or '').strip():
        return ''
    if kind == 'host' and not str(value or '').strip():
        return ''
    if kind == 'bool':
        return value in (True, 'true', '1', 'on', 'yes')
    return config567._validate(kind, value, target)


def _fresh(desk):
    """Configuration active telle qu'elle est sur disque (un autre écran a pu la publier depuis le début de la requête)."""
    path = desk.c.get('_path')
    if not path:
        return desk.c
    try:
        cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return desk.c
    cfg['_path'] = path
    return cfg


def save_step(desk, owner, sid, revision, step, values, action='continue'):
    """Valide et conserve les champs d'une étape ; renvoie les erreurs liées à chaque champ, sans rien perdre du reste (GI06).
    Les valeurs de départ (pour détecter un conflit) sont lues dans la configuration publiée, pas dans un instantané ancien."""
    with _Candidate(desk, _fresh(desk)):
        return _save_step(desk, owner, sid, revision, step, values, action)


def _save_step(desk, owner, sid, revision, step, values, action='continue'):
    data = _load(_row(desk, sid, owner))
    if data['status'] != 'in_progress':
        raise Stop('session_guide_terminee')
    if int(revision) != data['revision']:
        raise Stop('session_guide_modifiee')
    steps = steps_for(data['features'])
    if step not in steps:
        raise Stop('etape_guide_inconnue')
    errors = {}
    values = values if isinstance(values, dict) else {}
    if step == 'fonctions':
        chosen = [f for f in (values.get('features') or []) if f in FEATURES]
        data['features'] = chosen
        steps = steps_for(chosen)
    for name, target, kind, required, advanced in FIELDS.get(step, ()):
        if name not in values:
            continue
        try:
            clean = _validate(desk, step, name, target, kind, values[name])
        except Stop as ex:
            errors[name] = str(ex)
            continue
        if kind == 'secret':
            if clean:
                folder = _vault_dir(desk, sid)
                old = data['secret_refs'].get(target)
                from .vault567 import write
                data['secret_refs'][target] = write(folder / (target.replace('.', '-') + '-' + secrets.token_hex(6) + '.secret'), clean)
                if old and Path(old).is_file() and Path(old).parent == folder:
                    Path(old).unlink()
            continue
        if target not in data['base']:
            data['base'][target] = current_value(desk, target)
        data['candidate'][target] = clean
    if action == 'continue' and not errors:
        for name, target, kind, required, advanced in FIELDS.get(step, ()):
            if required and not data['candidate'].get(target) and not current_value(desk, target) and target not in data['secret_refs']:
                errors[name] = 'champ_obligatoire'
    if step == 'stockage' and not errors and action == 'continue':
        mode = data['candidate'].get('pref.stockage') or current_value(desk, 'pref.stockage')
        if mode == 'local' and not (data['candidate'].get('nextcloud.local_path') or current_value(desk, 'nextcloud.local_path')):
            errors['local_path'] = 'champ_obligatoire'
        if mode in ('nextcloud', 'webdav') and not (data['candidate'].get('nextcloud.url') or current_value(desk, 'nextcloud.url')):
            errors['url'] = 'champ_obligatoire'
    if action == 'skip' and step not in data['skipped']:
        data['skipped'].append(step)
    if action == 'continue' and not errors and step in data['skipped']:
        data['skipped'].remove(step)
    index = steps.index(step) if step in steps else 0
    if errors:
        target_step = step
    elif action == 'back':
        target_step = steps[max(0, index - 1)]
    elif action in ('continue', 'skip'):
        target_step = steps[min(len(steps) - 1, index + 1)]
    else:   # « Enregistrer et reprendre plus tard » : l'étape courante est conservée
        target_step = step
    data['revision'] += 1
    data['step'] = target_step
    desk.db.execute('UPDATE guide_sessions5631 SET revision=?, features=?, step=?, candidate=?, base=?, secret_refs=?, skipped=?, updated=? WHERE id=?',
                    (data['revision'], json.dumps(data['features']), target_step, json.dumps(data['candidate'], ensure_ascii=False),
                     json.dumps(data['base'], ensure_ascii=False), json.dumps(data['secret_refs']), json.dumps(data['skipped']), _now().isoformat(), sid))
    desk.db.commit()
    out = public(desk, data)
    out['errors'] = {k: {'code': v, 'message': message(v)} for k, v in errors.items()}
    return out


def message(code):
    text = i18n.t('err.' + code)
    if text != 'err.' + code:
        return text
    from .web440 import human
    return human(code)


# --------------------------------------------------------------------------- preuves ciblées (GI05, GI35)
def candidate_config(desk, data):
    """Configuration de test : configuration active + valeurs candidates + secrets candidats du coffre de la session."""
    cfg = deepcopy(desk.c)
    for target, value in data['candidate'].items():
        if target.startswith(('profile.', 'pref.')) or value in ('', None):
            continue
        _set(cfg, target, value)
    for target, ref in data['secret_refs'].items():
        _set(cfg, target, ref)
    mode = data['candidate'].get('pref.stockage')
    if mode == 'local':
        cfg.setdefault('nextcloud', {})
    elif mode in ('nextcloud', 'webdav') and not data['candidate'].get('nextcloud.local_path'):
        cfg.setdefault('nextcloud', {})['local_path'] = ''
    if (cfg.get('nextcloud') or {}).get('roots'):
        cfg['nextcloud'].setdefault('matter_roots', cfg['nextcloud']['roots'])
    return cfg


def fingerprint(cfg, connector):
    """Empreinte des seuls champs de la connexion ; la version du secret est une empreinte de son contenu, jamais le secret."""
    values, version = {}, []
    for key in CONNECTORS[connector]:
        value = _get(cfg, key, '')
        if key.endswith('_file'):
            try:
                version.append(hashlib.sha256(read_secret(value).encode()).hexdigest()[:16] if value else 'absent')
            except Stop:
                version.append('illisible')
            continue
        values[key] = value
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest(), '-'.join(version) or 'not_required'


def _record(desk, data, connector, capability, status, code, text, evidence, cfg):
    config_hash, secret_version = fingerprint(cfg, connector)
    now = _now()
    until = now + VALIDITY.get(capability, DEFAULT_VALIDITY)
    desk.db.execute('INSERT INTO connection_receipts5631(session_id,owner,connector,capability,config_hash,secret_version,status,code,message,evidence,checked_at,valid_until) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (data['id'], data['owner'], connector, capability, config_hash, secret_version, status, code, text[:500],
                     json.dumps(evidence, ensure_ascii=False, default=str)[:4000], now.isoformat(), until.isoformat()))
    return {'connector': connector, 'capability': capability, 'status': status, 'code': code, 'message': text, 'evidence': evidence,
            'checked_at': now.isoformat(), 'valid_until': until.isoformat()}


def receipts(desk, data):
    """Dernière preuve de chaque capacité ; « à renouveler » si l'empreinte de SA connexion a changé ou si elle a expiré."""
    _schema(desk)
    cfg = candidate_config(desk, data)
    out = {}
    now = _now().isoformat()
    for row in desk.db.execute('SELECT * FROM connection_receipts5631 WHERE session_id=? ORDER BY id', (data['id'],)).fetchall():
        config_hash, secret_version = fingerprint(cfg, row['connector'])
        status = row['status']
        if status == 'available' and (row['config_hash'] != config_hash or row['secret_version'] != secret_version):
            status = 'stale'
        elif status == 'available' and row['valid_until'] < now:
            status = 'expired'
        out['%s.%s' % (row['connector'], row['capability'])] = {
            'connector': row['connector'], 'capability': row['capability'], 'status': status, 'code': row['code'], 'message': row['message'],
            'checked_at': row['checked_at'], 'valid_until': row['valid_until'], 'evidence': json.loads(row['evidence'] or '{}')}
    return out


class _Candidate:
    """Contexte temporaire : ``desk.c`` remplacé par la configuration candidate le temps du test (les autres traitements gardent la leur)."""

    def __init__(self, desk, cfg):
        self.desk, self.cfg = desk, cfg

    def __enter__(self):
        self.saved = self.desk.c
        self.desk.c = self.cfg
        return self.desk

    def __exit__(self, *exc):
        self.desk.c = self.saved


def _smtp_auth(cfg):
    import smtplib
    import ssl
    smtp = cfg.get('smtp') or {}
    if not smtp.get('host') or 'example.com' in str(smtp.get('host')):
        raise Stop('smtp_non_renseigne')
    context = ssl.create_default_context()
    port = int(smtp.get('port') or 587)
    if smtp.get('security') == 'ssl':
        client = smtplib.SMTP_SSL(smtp['host'], port, timeout=15, context=context)
    else:
        client = smtplib.SMTP(smtp['host'], port, timeout=15)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    try:
        if smtp.get('username'):
            client.login(smtp['username'], read_secret(smtp['password_file']) if smtp.get('password_file') else '')
    except smtplib.SMTPAuthenticationError:
        raise Stop('smtp_identifiants_refuses') from None
    finally:
        try:
            client.quit()
        except Exception:
            pass
    return {'host': smtp['host'], 'port': port, 'messages_sent': 0}


def run_test(desk, owner, sid, connector, capability=''):
    """Teste la connexion CANDIDATE et enregistre une preuve par capacité ; une panne devient un résultat visible, le guide est conservé."""
    data = _load(_row(desk, sid, owner))
    if connector not in CONNECTORS:
        raise Stop('connecteur_test_inconnu')
    cfg = candidate_config(desk, data)
    results = []
    from . import config567
    with _Candidate(desk, cfg):
        if connector == 'stockage':
            out = config567.test(desk, {'connector': 'nextcloud'})
            results.append(_record(desk, data, connector, 'read', 'available' if out['ok'] else 'refused', out.get('code', ''), out['message'],
                                   {'entries': out.get('entries'), 'writes': 0}, cfg))
            if out['ok'] and capability == 'write':
                results.append(_write_probe(desk, data, cfg))
        elif connector == 'ia':
            results.extend(_ai_tests(desk, data, cfg, capability))
        elif connector == 'messagerie':
            out = config567.test(desk, {'connector': 'imap'})
            results.append(_record(desk, data, connector, 'read', 'available' if out['ok'] else 'refused', out.get('code', ''), out['message'],
                                   {'writes': 0, 'marked_read': 0}, cfg))
            if out['ok']:
                try:
                    from .boite5625 import folders
                    names = [r['name'] for r in folders(desk)['folders']][:200]
                    results.append(_record(desk, data, connector, 'folders', 'available', '', i18n.t('guide.folders_found', n=len(names)),
                                           {'folders': names}, cfg))
                except Exception as ex:   # noqa: BLE001 — une liste indisponible n'invalide pas la lecture
                    code = str(ex) if isinstance(ex, Stop) else 'dossiers_messagerie_illisibles'
                    results.append(_record(desk, data, connector, 'folders', 'unavailable', code, message(code), {}, cfg))
        elif connector == 'agenda':   # 5.6.32 : lecture des agendas (aucun événement créé, aucun agenda choisi à votre place)
            out = config567.test(desk, {'connector': 'nextcloud'})
            cals = out.get('calendars') or []
            chosen = list(_get(cfg, 'calendar.urls', []) or [])
            ok = out['ok'] and (not chosen or all(any(str(u).rstrip('/') == str(c.get('url', '')).rstrip('/') for c in cals) for u in chosen) or not cals)
            results.append(_record(desk, data, connector, 'read', 'available' if ok else 'refused', out.get('code', '') or ('' if ok else 'agenda_choisi_introuvable'),
                                   i18n.t('guide.calendars_found', n=len(cals)) if out['ok'] else out['message'],
                                   {'calendars': [c.get('name') or c.get('url') for c in cals][:50], 'events_written': 0}, cfg))
        elif connector == 'facturation':   # 5.6.32 : lecture seule des factures (aucune facture créée ni envoyée)
            out = config567.test(desk, {'connector': 'invoice_ninja'})
            results.append(_record(desk, data, connector, 'read', 'available' if out['ok'] else 'refused', out.get('code', ''), out['message'],
                                   {'writes': 0}, cfg))
        elif connector == 'telephonie':   # 5.6.32 : contrôle local de la signature Twilio (aucun appel, aucun service externe)
            results.append(_twilio_self_test(desk, data, cfg))
        elif connector == 'envoi':
            try:
                evidence = _smtp_auth(cfg)
                results.append(_record(desk, data, connector, 'auth', 'available', '', i18n.t('guide.smtp_ok'), evidence, cfg))
            except (Stop, OSError) as ex:
                code = str(ex) if isinstance(ex, Stop) else 'smtp_injoignable'
                results.append(_record(desk, data, connector, 'auth', 'refused', code, message(code), {'messages_sent': 0}, cfg))
    desk.db.commit()
    desk.audit('guide5631_test', {'session': sid, 'connector': connector, 'results': [(r['capability'], r['status']) for r in results]})
    return {'connector': connector, 'results': results, 'session': public(desk, data)}


def _twilio_self_test(desk, data, cfg):
    """Adresse de rappel HTTPS se terminant par /reception567/twilio et jeton lisible : une signature calculée sur une requête fictive est
    vérifiée par le même algorithme que le webhook (HMAC-SHA1). Rien n'est envoyé à Twilio."""
    import base64
    import hashlib
    import hmac
    from .common import read_secret
    url = str(_get(cfg, 'reception.twilio_callback_url', '') or '')
    path = _get(cfg, 'reception.twilio_auth_token_file', '')
    if not url.startswith('https://') or not url.rstrip('/').endswith('/reception567/twilio'):
        return _record(desk, data, 'telephonie', 'signature', 'refused', 'adresse_rappel_twilio_invalide', message('adresse_rappel_twilio_invalide'), {}, cfg)
    try:
        token = read_secret(path) if path else ''
    except (Stop, OSError):
        token = ''
    if not token:
        return _record(desk, data, 'telephonie', 'signature', 'refused', 'jeton_twilio_absent', message('jeton_twilio_absent'), {}, cfg)
    params = {'CallSid': 'CA' + '0' * 32, 'From': '+33100000000'}
    payload = url + ''.join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    again = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    ok = hmac.compare_digest(expected, again)
    return _record(desk, data, 'telephonie', 'signature', 'available' if ok else 'refused', '', i18n.t('guide.twilio_ok'),
                   {'callback': url, 'calls': 0}, cfg)


def _write_probe(desk, data, cfg):
    """Écriture de test annoncée : fichier technique identifiable, relu, supprimé puis disparition vérifiée (aucune pièce modifiée)."""
    nc = cfg.get('nextcloud') or {}
    if not nc.get('local_path'):
        return _record(desk, data, 'stockage', 'write', 'untested', 'ecriture_webdav_non_testee', i18n.t('guide.write_webdav_untested'), {'writes': 0}, cfg)
    from .dav import LocalFolder
    import tempfile
    client = LocalFolder(nc)
    target = client._fs(nc['roots'][0])[1]
    try:
        fd, probe = tempfile.mkstemp(dir=str(target), prefix='.axiorhub-test-ecriture-', suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('AxiorHub : fichier de test, supprimé automatiquement.\n')
        ok = Path(probe).read_text(encoding='utf-8').startswith('AxiorHub')
        os.unlink(probe)
        gone = not Path(probe).exists()
    except OSError:
        return _record(desk, data, 'stockage', 'write', 'refused', 'dossier_non_inscriptible', message('dossier_non_inscriptible'), {'writes': 0}, cfg)
    status = 'available' if ok and gone else 'refused'
    return _record(desk, data, 'stockage', 'write', status, '' if status == 'available' else 'ecriture_non_verifiee',
                   i18n.t('guide.write_ok') if status == 'available' else message('ecriture_non_verifiee'),
                   {'writes': 1, 'deleted': gone, 'file': Path(probe).name}, cfg)


def _ai_tests(desk, data, cfg, capability):
    """Trois niveaux séparés : service joignable, modèle installé, génération réussie (texte fictif)."""
    from .common import HTTP
    out = []
    ol = cfg.get('ollama') or {}
    if not ol.get('url'):
        out.append(_record(desk, data, 'ia', 'service', 'refused', 'ollama_non_renseigne', message('ollama_non_renseigne'), {}, cfg))
        return out
    try:
        client = HTTP(ol['url'], local_only=True, local_hosts=ol.get('local_hosts', ()), timeout=8)
        names = [m.get('name', '') for m in client.json('GET', '/api/tags').get('models', [])]
    except (Stop, OSError, ValueError, KeyError) as ex:
        code = str(ex) if isinstance(ex, Stop) else 'ollama_injoignable'
        out.append(_record(desk, data, 'ia', 'service', 'refused', code, message(code), {}, cfg))
        return out
    out.append(_record(desk, data, 'ia', 'service', 'available', '', i18n.t('guide.ollama_ok', n=len(names)), {'models': names[:100]}, cfg))
    wanted = [m for m in (ol.get('model'), (cfg.get('model_routing') or {}).get('fast_model')) if m]
    absent = [m for m in wanted if m not in names]
    if absent or not wanted:
        code = 'modele_ollama_selectionne_non_installe' if wanted else 'modele_non_choisi'
        out.append(_record(desk, data, 'ia', 'model', 'unavailable', code, message(code), {'absent': absent}, cfg))
        return out
    out.append(_record(desk, data, 'ia', 'model', 'available', '', i18n.t('guide.model_ok'), {'models': wanted}, cfg))
    if capability != 'generate':
        return out
    try:
        long_client = HTTP(ol['url'], local_only=True, local_hosts=ol.get('local_hosts', ()), timeout=120)
        reply = long_client.json('POST', '/api/generate', {'model': ol['model'], 'prompt': 'Réponds uniquement par le mot : prêt.', 'stream': False,
                                                          'options': {'num_predict': 12, 'temperature': 0}})
        text = str(reply.get('response') or '').strip()
    except (Stop, OSError, ValueError) as ex:
        code = str(ex) if isinstance(ex, Stop) else 'generation_interrompue'
        out.append(_record(desk, data, 'ia', 'generate', 'refused', code, message(code), {}, cfg))
        return out
    status = 'available' if text else 'unavailable'
    out.append(_record(desk, data, 'ia', 'generate', status, '' if text else 'generation_vide',
                       i18n.t('guide.generate_ok') if text else message('generation_vide'), {'chars': len(text), 'model': ol['model']}, cfg))
    return out


# --------------------------------------------------------------------------- publication (GI34, R02 étape 5)
def _capability_status(data, recs):
    out = {}
    for (connector, cap), name in CAPABILITY_NAMES.items():
        rec = recs.get('%s.%s' % (connector, cap))
        out[name] = rec['status'] if rec else 'untested'
    return out


def summary(desk, data):
    """Récapitulatif de fin (GI32) : fonctions disponibles, connexions à compléter, données externes autorisées ; aucun secret."""
    recs = receipts(desk, data)
    caps = _capability_status(data, recs)
    available, missing = [], {}
    for feature in data['features']:
        need = [c for c in REQUIRES.get(feature, ()) if caps.get(c) != 'available']
        if need:
            missing[feature] = need
        else:
            available.append(feature)
    external = bool((desk.c.get('ai_gateway') or {}).get('providers'))
    return {'available': available, 'missing': missing, 'capabilities': caps, 'skipped': data['skipped'],
            'external_ai_configured': external, 'secrets_included': False}


def apply(desk, owner, sid, revision, env, finish=True):
    """Publie le candidat : révision de session ET révision de configuration contrôlées ; conflit ciblé plutôt qu'écrasement."""
    from . import config567
    data = _load(_row(desk, sid, owner))
    if data['status'] != 'in_progress':
        raise Stop('session_guide_terminee')
    if int(revision) != data['revision']:
        raise Stop('session_guide_modifiee')
    current = _config_revision(desk)
    if current != data['base_config_revision']:
        # La configuration publiée par l'autre écran est relue sur disque (desk.c date du début de la requête), puis comparée
        # avec la même normalisation que les valeurs de départ enregistrées par la session.
        fresh = json.loads(config567._read_text(config567._path(env)))
        with _Candidate(desk, fresh):
            changed = [k for k, v in data['base'].items() if not k.startswith(('profile.', 'pref.')) and current_value(desk, k) != v]
        if changed:
            return {'conflict': True, 'fields': changed, 'config_revision': current}
        data['base_config_revision'] = current
    values = {k: v for k, v in data['candidate'].items() if not k.startswith(('profile.', 'pref.')) and k in config567.INDEX and v not in ('', None)}
    if 'nextcloud.roots' in values:
        values['nextcloud.matter_roots'] = values['nextcloud.roots']
    mode = data['candidate'].get('pref.stockage')
    if mode in ('nextcloud', 'webdav') and 'nextcloud.local_path' not in data['candidate']:
        values['nextcloud.local_path'] = ''
    if 'messagerie' not in data['features'] and data['features']:
        values['mail.enabled'] = False   # R00 : messagerie non choisie, non bloquante
    elif 'messagerie' in data['features']:
        values['mail.enabled'] = True
    for target, ref in data['secret_refs'].items():
        if target in config567.INDEX:
            values[target] = read_secret(ref)
    result = {'revision': current}
    if values:
        try:
            result = config567.save(desk, {'revision': data['base_config_revision'], 'values': values}, env)
        except Stop as ex:
            if str(ex) == 'configuration_modifiee_recharger':
                return {'conflict': True, 'fields': sorted(values), 'config_revision': _config_revision(desk)}
            raise
    _apply_profile(desk, owner, data)
    report = summary(desk, data)
    if finish:
        _mark_installation(env, data['features'], report)
    shutil.rmtree(_vault_dir(desk, sid), ignore_errors=True)
    desk.db.execute("UPDATE guide_sessions5631 SET status=?, secret_refs='{}', revision=revision+1, updated=? WHERE id=?",
                    ('applied' if finish else 'in_progress', _now().isoformat(), sid))
    desk.db.commit()
    desk.audit('guide5631_applique', {'session': sid, 'fields': sorted(values), 'features': data['features'], 'partial': bool(report['missing'])})
    return {'applied': True, 'config_revision': result.get('revision'), 'summary': report}


def _apply_profile(desk, owner, data):
    cand = data['candidate']
    prof = {k[len('profile.'):]: v for k, v in cand.items() if k.startswith('profile.')}
    if prof:
        from .pieces510 import profile, save_profile
        merged = dict(profile(desk))
        merged.update(prof)
        save_profile(desk, {k: merged.get(k, '') for k in ('prenom_avocat', 'nom_avocat', 'ville_avocat', 'numero_toque_avocat',
                                                             'adresse_cabinet_avocat', 'numero_telephone_avocat', 'email_avocat')})
    if cand.get('pref.langue'):
        i18n.save_preference(desk.c, owner, cand['pref.langue'])
    for key in ('fuseau', 'langue_redaction', 'cabinet', 'stockage'):
        if cand.get('pref.' + key):
            desk.setting('cabinet5631:' + key, cand['pref.' + key])
    if 'pref.domaines' in cand:
        desk.setting('cabinet5631:domaines', ', '.join(cand['pref.domaines']))
        desk.setting('cabinet560:specialite', ', '.join(cand['pref.domaines'])[:80])


def _mark_installation(env, features, report):
    """R00 étape 4 : finalisation complète ou partielle reconnue par le contrôle de premier démarrage (pas de redirection répétée)."""
    from . import config567
    from .portable import fcntl
    path = config567._path(env)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cfg = json.loads(config567._read_text(path))
        inst = cfg.setdefault('installation', {})
        inst.update({'done': True, 'at': _now().isoformat(), 'by': 'guide5631', 'features': list(features),
                     'partial': sorted(report['missing']), 'guide_version': GUIDE_VERSION})
        stat = path.stat()
        tmp = path.with_suffix('.guide5631.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(tmp, stat.st_mode & 0o777)
        os.replace(tmp, path)


def _cancel_row(desk, sid):
    shutil.rmtree(_vault_dir(desk, sid), ignore_errors=True)
    desk.db.execute("UPDATE guide_sessions5631 SET status='cancelled', secret_refs='{}', updated=? WHERE id=?", (_now().isoformat(), sid))


def cancel(desk, owner, sid):
    """Annulation : seuls les secrets candidats de la session sont effacés ; ceux de la configuration active restent intacts (GI34)."""
    _row(desk, sid, owner)
    _cancel_row(desk, sid)
    desk.db.commit()
    desk.audit('guide5631_annule', {'session': sid})
    return {'cancelled': True}


def export(desk, owner, sid):
    """Diagnostic exportable (GI T25) : ni secret, ni référence de coffre, ni pièce."""
    data = _load(_row(desk, sid, owner))
    out = public(desk, data)
    out['secrets'] = {k: 'configured' for k in data['secret_refs']}
    out['summary'] = summary(desk, data)
    out['secrets_included'] = False
    return out


def later(env):
    """« Découvrir puis configurer plus tard » au premier démarrage : l'installation est marquée partielle, sans fonction promise."""
    _mark_installation(env, [], {'missing': {f: list(REQUIRES[f]) for f in FEATURES if REQUIRES[f]}})
    return {'done': True, 'partial': True}


# --------------------------------------------------------------------------- API (préfixe /api440/m5631/guide/)
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    desk._guide_owner = owner
    route = name[len('m5631/guide/'):]
    if role != 'administrateur':
        raise Stop('role_insuffisant')   # GI36 : la configuration commune du cabinet reste à l'administrateur
    if method == 'GET':
        if route == 'session':
            return json_out(session(desk, owner, args.get('id', '')))
        if route == 'export':
            return json_out(export(desk, owner, args.get('id', '')))
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    try:
        if route == 'start':
            return json_out(start(desk, owner, data.get('features') or [], str(data.get('mode') or 'nouveau'), data.get('locale')))
        if route == 'step':
            return json_out(save_step(desk, owner, data.get('id', ''), data.get('revision', -1), str(data.get('step') or ''),
                                      data.get('values') or {}, str(data.get('action') or 'continue')))
        if route == 'test':
            return json_out(run_test(desk, owner, data.get('id', ''), str(data.get('connector') or ''), str(data.get('capability') or '')))
        if route == 'apply':
            out = apply(desk, owner, data.get('id', ''), data.get('revision', -1), env, finish=data.get('finish', True) is not False)
            return json_out(out, '409 Conflict' if out.get('conflict') else '200 OK')
        if route == 'cancel':
            return json_out(cancel(desk, owner, data.get('id', '')))
        if route == 'later':
            return json_out(later(env))
    except Stop as ex:
        code = str(ex)
        status = {'session_guide_modifiee': '409 Conflict', 'session_guide_autre_utilisateur': '403 Forbidden'}.get(code, '400 Bad Request')
        return json_out({'error': code, 'message': message(code)}, status)
    raise Stop('route_inconnue')


def lang_api(env, desk, auth, name, method):
    """Choix de langue de l'utilisateur connecté (tous rôles) : préférence enregistrée + cookie de session (LG01)."""
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    lang = i18n.save_preference(desk.c, owner, data.get('lang'), cabinet=bool(data.get('cabinet')) and role == 'administrateur')
    out = json_out({'lang': lang, 'name': i18n.NAMES[lang]})
    out['headers'] = [i18n.cookie_header(lang, auth.get('prefix') or '/')]
    return out


# --------------------------------------------------------------------------- page
def _field_html(desk, data, step, field, prof):
    name, target, kind, required, advanced = field
    t, th = i18n.t, i18n.th
    label = th('gf.%s.%s' % (step, name))
    help_text = i18n.catalog('fr').get('gf.%s.%s.help' % (step, name))
    hint = '<small class="g5631-hint">' + th('gf.%s.%s.help' % (step, name)) + '</small>' if help_text else ''
    value = data['candidate'].get(target, current_value(desk, target, prof))
    ident = 'g5631-%s-%s' % (step, name)
    req = ' required aria-required="true"' if required else ''
    err = '<span class="g5631-error" id="%s-err" role="alert" hidden></span>' % ident
    attrs = ' id="%s" name="%s" aria-describedby="%s-err"%s' % (ident, name, ident, req)
    if kind == 'secret':
        state = th('guide.secret_set') if target in data['secret_refs'] or _get(desk.c, target) else th('guide.secret_unset')
        control = '<input type="password" autocomplete="new-password"%s placeholder="%s">' % (attrs, e(state, quote=True))
    elif kind == 'lang':
        control = '<select%s>%s</select>' % (attrs, ''.join('<option value="%s"%s>%s</option>' % (c, ' selected' if c == value else '', e(i18n.NAMES[c]))
                                                            for c in i18n.LANGS))
    elif kind == 'timezone':
        control = '<select%s>%s</select>' % (attrs, ''.join('<option%s>%s</option>' % (' selected' if z == value else '', z) for z in TIMEZONES))
    elif kind == 'storage_mode':
        control = '<div class="g5631-cards" role="radiogroup" aria-labelledby="%s-label">%s</div>' % (ident, ''.join(
            '<label class="g5631-card"><input type="radio" name="mode" value="%s"%s><strong>%s</strong><small>%s</small></label>' % (
                m, ' checked' if m == value else '', th('guide.storage.%s' % m), th('guide.storage.%s.help' % m))
            for m in ('local', 'nextcloud', 'webdav', 'plus_tard')))
        return '<fieldset class="g5631-field"><legend id="%s-label">%s</legend>%s%s%s</fieldset>' % (ident, label, control, hint, err)
    elif kind == 'domains':
        chosen = set(value or [])
        control = '<div class="g5631-checks">%s</div>' % ''.join(
            '<label><input type="checkbox" name="domaines" value="%s"%s> %s</label>' % (
                e(t('dom.%s' % d), quote=True), ' checked' if t('dom.%s' % d) in chosen else '', th('dom.%s' % d)) for d in DOMAINS)
        return '<fieldset class="g5631-field"><legend>%s</legend>%s%s%s</fieldset>' % (label, control, hint, err)
    elif kind == 'imap_security':
        control = '<select%s>%s</select>' % (attrs, ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if v == (value or '') else '', th('guide.imap_sec.' + (v or 'auto')))
                                                            for v in ('', 'ssl', 'starttls')))
    elif kind == 'smtp_security':
        control = '<select%s>%s</select>' % (attrs, ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if v == (value or 'starttls') else '', v.upper())
                                                            for v in ('starttls', 'ssl')))
    elif kind == 'bool':
        return '<label class="g5631-field g5631-bool"><input type="checkbox"%s%s> %s</label>%s%s' % (attrs, ' checked' if value is True else '', label, hint, err)
    elif kind == 'paths':
        control = '<input type="text"%s value="%s" placeholder="%s">' % (attrs, e(', '.join(value) if isinstance(value, list) else str(value or ''), quote=True),
                                                                      th('gf.%s.%s.example' % (step, name)))
    elif kind == 'urls':   # 5.6.32 : liste d'adresses (une par ligne)
        control = '<textarea rows="3"%s placeholder="%s">%s</textarea>' % (attrs, th('gf.%s.%s.example' % (step, name)),
                                                                          e('\n'.join(value) if isinstance(value, list) else str(value or '')))
    else:
        example = i18n.catalog('fr').get('gf.%s.%s.example' % (step, name))
        control = '<input type="%s"%s value="%s"%s>' % ('email' if kind == 'email' else 'text', attrs, e(str(value if value is not None else ''), quote=True),
                                                       (' placeholder="%s"' % th('gf.%s.%s.example' % (step, name))) if example else '')
    return '<div class="g5631-field"><label for="%s">%s</label>%s%s%s</div>' % (ident, label, control, hint, err)


def _receipts_html(recs, connector):
    rows = [r for r in recs.values() if r['connector'] == connector]
    if not rows:
        return '<p class="g5631-proof-none">' + i18n.th('guide.not_tested') + '</p>'
    return '<ul class="g5631-proofs">' + ''.join(
        '<li data-status="%s"><strong>%s</strong> — %s<br><small>%s</small></li>' % (
            e(r['status']), i18n.th('cap.%s.%s' % (connector, r['capability'])), i18n.th('status.' + r['status']), e(r['message']))
        for r in rows) + '</ul>'


def page(desk, auth, prefix, env, args, shell):
    """Écran du guide : une question principale et 3 à 5 champs visibles par écran, aide « Où trouver cette information », progression."""
    from .web567 import actor
    owner, role = actor(env)
    desk._guide_owner = owner
    th = i18n.th
    _schema(desk)
    sid = str(args.get('session') or '')
    row = None
    if sid:
        try:
            row = _row(desk, sid, owner)
        except Stop:
            row = None
    if row is None:
        row = desk.db.execute("SELECT * FROM guide_sessions5631 WHERE owner=? AND status='in_progress' ORDER BY updated DESC LIMIT 1", (owner,)).fetchone()
    from .fonctions5631 import capabilities
    installed = 'storage' in capabilities(desk.c)
    head = ''   # v5631.css fait partie de la feuille unique (app520.css)
    if row is None:
        body = ('<div class="g5631" id="g5631" data-api="%s" data-page="%s"><h1>%s</h1><p class="g5631-lead">%s</p>' % (
            e(prefix + '/api440/m5631/guide/', quote=True), e(prefix + '/guide-installation', quote=True), th('guide.title'), th('guide.lead')))
        if installed:
            body += ('<div class="g5631-actions"><button type="button" class="g5631-start" data-mode="verifier">%s</button>'
                     '<button type="button" class="g5631-start ghost" data-mode="ajouter">%s</button></div>' % (th('guide.check_settings'), th('guide.add_feature')))
        else:
            body += ('<div class="g5631-actions"><button type="button" class="g5631-start" data-mode="nouveau">%s</button>'
                     '<button type="button" class="g5631-later ghost">%s</button></div>' % (th('guide.begin'), th('guide.discover_later')))
        body += '<p class="g5631-note">%s</p></div>' % th('guide.no_send')
        return shell(i18n.t('guide.title'), body, prefix, auth['csrf'], '/guide-installation', head)
    data = _load(row)
    pub = public(desk, data)
    step = str(args.get('etape') or pub['step'])
    if step not in pub['steps']:
        step = pub['step']
    from .pieces510 import profile
    prof = profile(desk)
    position = pub['steps'].index(step) + 1
    progress = '<p class="g5631-progress" aria-live="polite">%s</p><ol class="g5631-steps">%s</ol>' % (
        th('guide.progress', position=position, total=pub['total']),
        ''.join('<li%s><a href="%s">%s</a></li>' % (' aria-current="step"' if s == step else (' class="done"' if pub['steps'].index(s) < position - 1 else ''),
                                                    e(prefix + '/guide-installation?session=' + data['id'] + '&etape=' + s, quote=True), th('step.%s.title' % s))
                for s in pub['steps']))
    body = ('<div class="g5631" id="g5631" data-api="%s" data-page="%s" data-session="%s" data-revision="%d" data-step="%s">' % (
        e(prefix + '/api440/m5631/guide/', quote=True), e(prefix + '/guide-installation', quote=True), e(data['id'], quote=True), data['revision'], e(step, quote=True)))
    body += '<h1>%s</h1>%s' % (th('guide.title'), progress)
    body += '<section class="g5631-step"><h2>%s</h2><p class="g5631-question">%s</p><p>%s</p>' % (
        th('step.%s.title' % step), th('step.%s.question' % step), th('step.%s.explain' % step))
    body += '<p class="g5631-unlocks"><strong>%s</strong> %s</p>' % (th('guide.unlocks'), th('step.%s.unlocks' % step))
    body += '<details class="g5631-where"><summary>%s</summary><div>%s</div></details>' % (th('guide.where'), _where_html(step, prefix))
    body += '<form class="g5631-form" novalidate>'
    if step == 'fonctions':
        body += '<fieldset class="g5631-field"><legend>%s</legend><div class="g5631-cards">%s</div></fieldset>' % (th('step.fonctions.question'), ''.join(
            '<label class="g5631-card"><input type="checkbox" name="features" value="%s"%s><strong>%s</strong><small>%s</small></label>' % (
                f, ' checked' if f in data['features'] else '', th('feat.%s' % f), th('feat.%s.help' % f)) for f in FEATURES))
    elif step == 'fin':
        body += _summary_html(desk, data, prefix)
    elif step in FIELDS:
        basic = [f for f in FIELDS[step] if not f[4]]
        more = [f for f in FIELDS[step] if f[4]]
        body += ''.join(_field_html(desk, data, step, f, prof) for f in basic)
        if more:
            body += '<details class="g5631-more"><summary>%s</summary>%s</details>' % (th('guide.more'), ''.join(_field_html(desk, data, step, f, prof) for f in more))
    elif step == 'sources':   # 5.6.32 : cartes réelles (compte, clé, vérification, activation) des sources juridiques
        from .web5632 import sources_section
        body += sources_section(desk) if role == 'administrateur' else _info_step_html(step, prefix)
    elif step == 'voix':   # 5.6.34 (B17) : Kokoro selon le système d'installation et voix par langue
        from .kokoro5634 import guide_html
        from .diagnostic5634 import install_mode
        body += guide_html(i18n.current(), install_mode(desk.c))
    elif step == 'veille':
        from .web5632 import veille_settings
        body += veille_settings(desk, env, prefix, admin=role == 'administrateur')
    else:
        body += _info_step_html(step, prefix)
    if step in CONNECTORS:
        recs = pub['receipts']
        body += '<section class="g5631-test" aria-live="polite"><h3>%s</h3><p>%s</p><div class="g5631-proof" data-connector="%s">%s</div>' % (
            th('guide.verify_title'), th('test.%s.explain' % step), step, _receipts_html(recs, step))
        body += '<button type="button" class="g5631-test-btn" data-connector="%s">%s</button>' % (step, th('guide.verify'))
        extra = {'stockage': ('write', 'guide.verify_write'), 'ia': ('generate', 'guide.verify_generate')}.get(step)
        if extra:
            body += ' <button type="button" class="g5631-test-btn ghost" data-connector="%s" data-capability="%s">%s</button>' % (step, extra[0], th(extra[1]))
        body += '</section>'
    body += '<p class="g5631-status" role="status" aria-live="polite"></p><div class="g5631-nav">'
    if position > 1:
        body += '<button type="button" class="ghost" data-action="back">%s</button>' % th('guide.back')
    if step == 'fin':
        body += '<button type="button" data-action="apply">%s</button>' % th('guide.finish')
    else:
        body += '<button type="button" data-action="continue">%s</button>' % th('guide.continue')
        if step not in ('fonctions', 'profil', 'langues'):
            body += '<button type="button" class="ghost" data-action="skip">%s</button>' % th('guide.skip')
    body += '<button type="button" class="ghost" data-action="save">%s</button><button type="button" class="ghost danger" data-action="cancel">%s</button></div></form></section>' % (
        th('guide.save_later'), th('guide.cancel'))
    body += '</div>'
    return shell(i18n.t('guide.title'), body, prefix, auth['csrf'], '/guide-installation', head)


def _version():
    from . import __version__
    return __version__


LINKS = {'voix': (('Kokoro-FastAPI (GitHub)', 'https://github.com/remsky/Kokoro-FastAPI'), ('Docker Desktop', 'https://www.docker.com/products/docker-desktop/'),
                  ('eSpeak NG', 'https://github.com/espeak-ng/espeak-ng')),
         'ia': (('Ollama', 'https://ollama.com/download'), ('Ollama — modèles', 'https://ollama.com/search'),
                ('Anthropic (Claude)', 'https://docs.anthropic.com/'), ('OpenRouter', 'https://openrouter.ai/docs/quickstart'),
                ('OpenAI', 'https://platform.openai.com/docs'), ('Mistral', 'https://docs.mistral.ai/')),
         'stockage': (('Nextcloud — mots de passe d’application', 'https://docs.nextcloud.com/server/latest/user_manual/fr/session_management.html'),),
         'agenda': (('Nextcloud — CalDAV', 'https://docs.nextcloud.com/server/latest/user_manual/fr/groupware/sync_android.html'),
                    ('Google Calendar API', 'https://developers.google.com/calendar/api/guides/overview')),
         'sources': (('GoodLegal', 'https://api.goodlegal.fr/dashboard'), ('OpenLegi', 'https://www.openlegi.fr/accounts/signup'),
                     ('LibreJustice', 'https://librejustice.fr/mcp-guide'), ('Legal Data Hunter', 'https://legaldatahunter.com/account')),
         'veille': (('Tavily', 'https://app.tavily.com'),),
         'facturation': (('Invoice Ninja', 'https://invoiceninja.github.io/'),),
         'telephonie': (('Twilio — sécurité des webhooks', 'https://www.twilio.com/docs/usage/webhooks/webhooks-security'),)}
# Écrans existants qui réalisent les étapes encore informatives (lots suivants du cahier des charges).
SCREENS = {'voix': '/parametres?rubrique=voix', 'agenda': '/parametres?rubrique=connexions', 'sources': '/mcp', 'modeles': '/modeles-word', 'veille': '/veille',
           'facturation': '/parametres?rubrique=connexions', 'telephonie': '/accueil-administratif'}


def _where_html(step, prefix):
    th = i18n.th
    links = ''.join('<li><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></li>' % (e(url, quote=True), e(label)) for label, url in LINKS.get(step, ()))
    return '<p>%s</p>%s' % (th('step.%s.where' % step), '<ul>' + links + '</ul>' if links else '')


def _info_step_html(step, prefix):
    th = i18n.th
    return ('<div class="g5631-info"><p>%s</p><p><a class="g5631-open" href="%s" target="_blank" rel="noopener">%s</a></p>'
            '<p class="g5631-limit">%s</p></div>') % (th('step.%s.todo' % step), e(prefix + SCREENS.get(step, '/parametres'), quote=True),
                                                       th('guide.open_screen'), th('guide.info_step_limit'))


def _summary_html(desk, data, prefix):
    th = i18n.th
    rep = summary(desk, data)
    ok = ''.join('<li>%s</li>' % th('feat.%s' % f) for f in rep['available']) or '<li>%s</li>' % th('guide.none')
    todo = ''.join('<li>%s — %s</li>' % (th('feat.%s' % f), ', '.join(e(c) for c in need)) for f, need in rep['missing'].items()) or '<li>%s</li>' % th('guide.none')
    return ('<div class="g5631-summary"><h3>%s</h3><ul>%s</ul><h3>%s</h3><ul>%s</ul><p>%s</p><p>%s</p>'
            '<p class="g5631-next"><a href="%s">%s</a> · <a href="%s">%s</a></p></div>') % (
        th('guide.available'), ok, th('guide.to_complete'), todo,
        th('guide.external_yes') if rep['external_ai_configured'] else th('guide.external_no'), th('guide.partial_ok'),
        e(prefix + '/aujourdhui', quote=True), th('guide.open_today'), e(prefix + '/guide-utilisation', quote=True), th('menu.usage_guide'))
