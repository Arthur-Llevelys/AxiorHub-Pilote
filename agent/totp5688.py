"""5.6.88 : double authentification (codes à six chiffres, RFC 6238) des comptes de la version serveur et de la distribution autonome.

Implémentée en Python standard : HMAC-SHA1, pas de 30 secondes, six chiffres, tolérance d'un pas de part et d'autre, code déjà consommé
refusé. Le secret est écrit par le coffre (``vault567``) ; dix codes de secours à usage unique sont gardés hachés (scrypt) ; les essais
sont comptés par le mécanisme de la page de connexion (table ``attempts``, ``connexion5648._limited``). Facultative par défaut, avec une
option du cabinet pour l'exiger. Un administrateur peut réinitialiser celle d'un autre compte (trace d'audit).

**Génération** : chaque activation et chaque réinitialisation incrémente un compteur propre au compte. Une session, un jeton d'API ou un
accès Claude n'est valable que s'il a été obtenu à la génération en cours : ce qui existait avant l'activation est fermé, sans table à
parcourir. Un compte qui n'a jamais activé la double authentification reste à la génération 0 : rien ne change pour lui après mise à jour.

Comportement de chaque accès (repris dans SECURITY.md et sur la page « Mon compte », constante ``ACCES``) :

- **Page de connexion** : le mot de passe seul n'ouvre aucune session ; le code est demandé à chaque connexion.
- **« Se souvenir de moi »** : prolonge la session ouverte APRÈS le code (30 jours) ; ce n'est jamais un appareil de confiance dispensé du
  code. Une session antérieure à l'activation est fermée.
- **Téléphone associé (jeton mobile)** : l'invitation par QR code ne suffit pas, le téléphone doit saisir un code ; les téléphones associés
  avant l'activation sont déconnectés et se réassocient.
- **Connecteur Claude** : l'autorisation passe par la page de connexion, donc par le code ; les autorisations antérieures sont retirées.
- **Jetons d'API personnels** : créés depuis une session qui a passé le code ; ceux d'avant l'activation cessent de fonctionner. Un jeton
  ne se présente jamais sans le code à chaque appel : c'est un secret à garder comme un mot de passe.
- **En-tête « Basic » de l'administrateur** (scripts) : refusé dès que la double authentification de ce compte est active ou exigée ; les
  scripts passent à un jeton d'API personnel.
- **Application Nextcloud** : relier un compte demande le code ; les clés déjà remises sont révoquées à l'activation.
- **Jeton de service** (Open WebUI) : accès de machine sans identité, hors de la double authentification ; il se protège sur le serveur.
- **Poste et ExApp** : sans comptes propres, ils n'ont pas de double authentification (fenêtre locale ; connexion Nextcloud).
- **Réinitialisation sur le serveur** : ``sudo python3 reinitialiser-acces.py --2fa <identifiant>`` (accès root au serveur).
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import struct
import time
from urllib.parse import quote

from .common import Stop

DIGITS = 6
PERIOD = 30
WINDOW = 1
BACKUP_COUNT = 10
PENDING_MINUTES = 10
ISSUER = 'AxiorHub Pilote'
SECRETS_DIR = 'secrets5688'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS totp5688(owner TEXT PRIMARY KEY, actif INTEGER NOT NULL DEFAULT 0, dernier_pas INTEGER NOT NULL DEFAULT 0,
  cree TEXT NOT NULL, active_le TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS secours5688(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, sel TEXT NOT NULL, empreinte TEXT NOT NULL,
  utilise TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS secours5688_owner ON secours5688(owner);
CREATE TABLE IF NOT EXISTS generations5688(owner TEXT PRIMARY KEY, gen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS attente5688(token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, user_id INTEGER NOT NULL, remember INTEGER NOT NULL DEFAULT 0,
  suite TEXT NOT NULL DEFAULT '', client TEXT NOT NULL DEFAULT '', expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reglages5688(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_scope ON attempts(scope, at);
'''
# Affiché sur « Mon compte » et vérifié par les tests : chaque accès a un comportement écrit.
ACCES = (
    ('Page de connexion', 'Le mot de passe seul n’ouvre rien : le code est demandé à chaque connexion.'),
    ('« Se souvenir de moi »', 'Prolonge la session ouverte après le code. Le prochain mot de passe saisi redemandera le code.'),
    ('Téléphone associé', 'Le téléphone saisit un code au moment de l’association. Ceux associés avant l’activation sont déconnectés.'),
    ('Connecteur Claude', 'L’autorisation passe par cette connexion, donc par le code. Les autorisations plus anciennes sont retirées.'),
    ('Jetons d’API personnels', 'Créés après le code. Ceux d’avant l’activation cessent de fonctionner. Gardez-les comme un mot de passe.'),
    ('Scripts avec identifiant et mot de passe', 'Refusés pour ce compte dès que la protection est active : utilisez un jeton d’API.'),
    ('Application Nextcloud', 'Relier un compte demande le code. Les clés déjà remises sont révoquées à l’activation.'),
    ('Jeton de service', 'Accès de machine, sans identité, non concerné : il se protège sur le serveur.'),
)


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ RFC 4226 et RFC 6238
def hotp(key, counter, digits=DIGITS):
    mac = hmac.new(bytes(key), struct.pack('>Q', int(counter)), hashlib.sha1).digest()
    offset = mac[-1] & 0x0f
    return str((struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7fffffff) % (10 ** digits)).zfill(digits)


def step_of(at=None):
    return int((time.time() if at is None else at) // PERIOD)


def totp(key, at=None, digits=DIGITS):
    return hotp(key, step_of(at), digits)


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def key_of(secret):
    return base64.b32decode(str(secret).replace(' ', '').upper() + '=' * (-len(str(secret).replace(' ', '')) % 8))


def matching_step(secret, code, at=None):
    """Pas dont le code correspond (fenêtre de ±1), ou None. Les trois comparaisons sont toujours faites."""
    code = str(code or '').strip().replace(' ', '')
    if not re.fullmatch(r'\d{%d}' % DIGITS, code):
        return None
    key, now, found = key_of(secret), step_of(at), None
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(hotp(key, step), code) and found is None:
            found = step
    return found


def uri(secret, account):
    label = quote('%s:%s' % (ISSUER, account), safe='')
    return 'otpauth://totp/%s?secret=%s&issuer=%s&algorithm=SHA1&digits=%d&period=%d' % (label, secret, quote(ISSUER, safe=''), DIGITS, PERIOD)


def qr_svg(text):
    """QR code embarqué dans la page (jamais servi par une adresse : il contient le secret)."""
    from .appareils5674 import qr_svg as make
    raw = make(text)
    return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)


# ------------------------------------------------------------------------------------------------ stockage
def connect(state_dir):
    """Base des comptes : celle de la page de connexion, ou celle de la distribution autonome."""
    from .comptes5642 import standalone
    if standalone():
        from .missions567 import auth_root
        db = sqlite3.connect(str(auth_root() / 'users.sqlite3'), timeout=10)
        db.row_factory = sqlite3.Row
    else:
        from . import connexion5648 as cx
        db = cx.connect(state_dir)
    db.executescript(SCHEMA)
    return db


def _secret_path(db, owner):
    """Le coffre garde sa clé à côté du secret : dossier privé près de la base des comptes, quelle que soit la distribution."""
    base = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
    return base / SECRETS_DIR / ('totp-%s.secret' % hashlib.sha256(str(owner).encode()).hexdigest()[:40])


def _write_secret(db, owner, secret):
    from . import vault567
    vault567.write(_secret_path(db, owner), secret)


def _read_secret(db, owner):
    from .common import read_secret
    return read_secret(_secret_path(db, owner))


def _drop_secret(db, owner):
    try:
        _secret_path(db, owner).unlink()
    except OSError:
        pass


def setting(db, key, default=''):
    row = db.execute('SELECT value FROM reglages5688 WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def required(db):
    return setting(db, 'exiger') == '1'


def set_required(db, value):
    db.execute('INSERT OR REPLACE INTO reglages5688 VALUES(?,?)', ('exiger', '1' if value else '0'))
    db.commit()


def generation(db, owner):
    row = db.execute('SELECT gen FROM generations5688 WHERE owner=?', (str(owner),)).fetchone()
    return int(row[0]) if row else 0


def _bump(db, owner):
    db.execute('INSERT INTO generations5688(owner,gen) VALUES(?,1) ON CONFLICT(owner) DO UPDATE SET gen=gen+1', (str(owner),))
    return generation(db, owner)


def active(db, owner):
    return bool(db.execute('SELECT 1 FROM totp5688 WHERE owner=? AND actif=1', (str(owner),)).fetchone())


def status(db, owner):
    row = db.execute('SELECT * FROM totp5688 WHERE owner=?', (str(owner),)).fetchone()
    left = db.execute("SELECT COUNT(*) FROM secours5688 WHERE owner=? AND utilise=''", (str(owner),)).fetchone()[0]
    return {'actif': bool(row and row['actif']), 'en_attente': bool(row and not row['actif']), 'active_le': row['active_le'] if row else '',
            'codes_restants': left, 'exigee': required(db), 'generation': generation(db, owner)}


def need(db, owner):
    """Étape après le mot de passe : « code » (protection active), « activation » (exigée mais pas encore activée) ou rien."""
    if active(db, owner):
        return 'code'
    return 'activation' if required(db) else ''


def verdict(db, owner, session_mfa):
    """État d'une session ouverte : « ok », « refus » (ouverte sans le code à la génération en cours) ou « activation » à faire."""
    if active(db, owner):
        return 'ok' if int(session_mfa or 0) == generation(db, owner) else 'refus'
    return 'activation' if required(db) else 'ok'


# ------------------------------------------------------------------------------------------------ cycle de vie
def begin(db, owner, account):
    """Nouveau secret, inactif jusqu'au premier code confirmé. Un secret en attente est conservé : le QR code reste celui déjà scanné."""
    if active(db, owner):
        raise Stop('double_authentification_deja_active')
    if not db.execute('SELECT 1 FROM totp5688 WHERE owner=?', (str(owner),)).fetchone():
        _write_secret(db, owner, new_secret())
        db.execute('INSERT INTO totp5688(owner,actif,dernier_pas,cree) VALUES(?,0,0,?)', (str(owner), _now().isoformat(timespec='seconds')))
        db.commit()
    return enrolment(db, owner, account)


def enrolment(db, owner, account):
    secret = _read_secret(db, owner)
    target = uri(secret, str(account))
    return {'secret': ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4)), 'uri': target, 'svg': qr_svg(target)}


def cancel(db, owner):
    db.execute('DELETE FROM totp5688 WHERE owner=? AND actif=0', (str(owner),))
    db.commit()


def _backup():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # sans I, O, 0, 1 : se recopie sans erreur
    raw = ''.join(secrets.choice(alphabet) for _ in range(12))
    return '%s-%s-%s' % (raw[:4], raw[4:8], raw[8:])


def _backup_hash(code, salt):
    return hashlib.scrypt(re.sub(r'[\s-]', '', str(code)).upper().encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def renew_codes(db, owner):
    """Dix nouveaux codes de secours : les précédents ne valent plus ; seule leur empreinte est gardée."""
    db.execute('DELETE FROM secours5688 WHERE owner=?', (str(owner),))
    codes = [_backup() for _ in range(BACKUP_COUNT)]
    for code in codes:
        salt = secrets.token_hex(16)
        db.execute('INSERT INTO secours5688(owner,sel,empreinte) VALUES(?,?,?)', (str(owner), salt, _backup_hash(code, salt)))
    db.commit()
    return codes


def confirm(db, owner, code):
    """Premier code juste : la protection devient active, génération suivante, codes de secours remis une seule fois.
    Renvoie (codes, génération) ; un code faux lève ``code_double_authentification_incorrect``."""
    row = db.execute('SELECT * FROM totp5688 WHERE owner=? AND actif=0', (str(owner),)).fetchone()
    if not row:
        raise Stop('double_authentification_non_commencee')
    step = matching_step(_read_secret(db, owner), code)
    if step is None:
        raise Stop('code_double_authentification_incorrect')
    gen = _bump(db, owner)
    db.execute('UPDATE totp5688 SET actif=1, dernier_pas=?, active_le=? WHERE owner=?', (step, _now().isoformat(timespec='seconds'), str(owner)))
    return renew_codes(db, owner), gen


def _consume_step(db, owner, step):
    """Atomique : deux requêtes simultanées avec le même code ne passent pas toutes les deux."""
    cur = db.execute('UPDATE totp5688 SET dernier_pas=? WHERE owner=? AND actif=1 AND dernier_pas<?', (int(step), str(owner), int(step)))
    db.commit()
    return cur.rowcount == 1


def check(db, owner, code):
    """« code », « secours » ou '' : chaque code ne sert qu'une fois. Ne compte pas les essais (voir ``attempt``)."""
    code = str(code or '').strip()
    if re.fullmatch(r'\d{6}', code.replace(' ', '')):
        try:
            step = matching_step(_read_secret(db, owner), code)
        except Stop:
            return ''
        return 'code' if step is not None and _consume_step(db, owner, step) else ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9\s-]{10,20}', code):
        return ''
    for row in db.execute("SELECT id,sel,empreinte FROM secours5688 WHERE owner=? AND utilise=''", (str(owner),)).fetchall():
        if hmac.compare_digest(_backup_hash(code, row['sel']), row['empreinte']):
            cur = db.execute("UPDATE secours5688 SET utilise=? WHERE id=? AND utilise=''", (_now().isoformat(timespec='seconds'), row['id']))
            db.commit()
            return 'secours' if cur.rowcount == 1 else ''
    return ''


def attempt(db, owner, code, client=''):
    """Essai compté par le mécanisme de la connexion : 5 échecs par compte en 15 minutes, 20 par adresse. Lève ``trop_de_tentatives``."""
    from . import connexion5648 as cx
    scopes = ('mfa:' + str(owner).lower()[:120], 'ip:' + str(client)[:64])
    if cx._limited(db, scopes[0], 'mfa') or (client and cx._limited(db, scopes[1], 'ip')):
        raise Stop('trop_de_tentatives')
    kind = check(db, owner, code)
    if kind:
        db.execute('DELETE FROM attempts WHERE scope=?', (scopes[0],))
        db.commit()
        return kind
    cx._attempt(db, *(scopes if client else scopes[:1]))
    return ''


def confirm_limited(db, owner, code, client=''):
    """Activation : le premier code passe par la même limitation que les suivants."""
    from . import connexion5648 as cx
    scope = 'mfa:' + str(owner).lower()[:120]
    if cx._limited(db, scope, 'mfa'):
        raise Stop('trop_de_tentatives')
    try:
        out = confirm(db, owner, code)
    except Stop as ex:
        if str(ex) == 'code_double_authentification_incorrect':
            cx._attempt(db, scope)
        raise
    db.execute('DELETE FROM attempts WHERE scope=?', (scope,))
    db.commit()
    return out


def disable(db, owner):
    db.execute('DELETE FROM totp5688 WHERE owner=?', (str(owner),))
    db.execute('DELETE FROM secours5688 WHERE owner=?', (str(owner),))
    db.commit()
    _drop_secret(db, owner)


def reset(db, owner):
    """Réinitialisation par un administrateur ou sur le serveur : génération suivante, donc sessions, jetons et accès Claude fermés."""
    gen = _bump(db, owner)
    disable(db, owner)
    return gen


# ------------------------------------------------------------------------------------------------ étape de connexion
def pending_open(db, owner, user_id, remember, suite, client):
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM attente5688 WHERE expires<?', (_now().isoformat(),))
    db.execute('INSERT INTO attente5688 VALUES(?,?,?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), str(owner), int(user_id), 1 if remember else 0,
                                                                str(suite)[:600], str(client)[:64],
                                                                (_now() + timedelta(minutes=PENDING_MINUTES)).isoformat()))
    db.commit()
    return token


def pending_get(db, token):
    if not token or len(token) > 200:
        return None
    return db.execute('SELECT * FROM attente5688 WHERE token_hash=? AND expires>?', (hashlib.sha256(str(token).encode()).hexdigest(), _now().isoformat())).fetchone()


def pending_close(db, token):
    db.execute('DELETE FROM attente5688 WHERE token_hash=?', (hashlib.sha256(str(token).encode()).hexdigest(),))
    db.commit()
