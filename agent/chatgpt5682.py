"""5.6.82 : adaptateur de l'abonnement ChatGPT (ChatGPTPlanAdapter), au-dessus de la connexion officielle de ``chatgpt5662``.

- **Requête** : liste blanche des champs admis sur cette voie. Les paramètres d'une API à clé ou d'Ollama ne passent jamais.
- **Limites** : un refus pour limite pose un report borné (15 minutes, puis jusqu'à 4 heures) ; aucune heure de remise à disposition n'est
  déduite du code d'erreur. Le travail garde sa ligne dans la file, sans doublon, et les travaux sans IA continuent.
- **Serveur** (procédure « Self-hosted VMs ») : le retour 127.0.0.1 atteint le poste du navigateur. La connexion se fait sur le poste,
  puis le dossier protégé passe au serveur, chiffré par une phrase de passe, jamais par une adresse. Le serveur garde son propre
  identifiant d'hôte et seul lui renouvelle ensuite les jetons : le poste efface les siens à l'export.
"""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

from .common import Stop

ALLOWED_FIELDS = ('model', 'input', 'instructions', 'store', 'stream', 'text', 'reasoning')
EFFORTS = ('low', 'medium', 'high')
DEFER_KEY = 'chatgpt5682:report'
DEFER_CODES = frozenset(('chatgpt_limite_usage_atteinte', 'chatgpt_indisponible'))
HELD_CODES = DEFER_CODES | {'plafond_automatique_atteint'}       # travaux reportés sur leur ligne, jamais perdus
DELAYS_MINUTES = (15, 30, 60, 120, 240)
BUNDLE_PREFIX = 'AXH-SIWC1:'
MIN_PASSPHRASE = 12
RECORD_FIELDS = ('email', 'issuer', 'subject', 'client_id', 'id_token', 'access_token', 'refresh_token', 'token_type', 'expires_at', 'scopes', 'saved_at')


def clean_request(req):
    return {k: v for k, v in req.items() if k in ALLOWED_FIELDS}


def effort_field(effort):
    return {'effort': effort} if effort in EFFORTS else None


# ------------------------------------------------------------------------------------------------ report après une limite
def _db(state_dir):
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    return sqlite3.connect(path, timeout=5) if state_dir and path.is_file() else None


def deferred(state_dir, now=None):
    """Report en cours, ou None. Aucune heure de reprise n'est annoncée : ``jusqu_a`` est notre prochaine vérification."""
    db = _db(state_dir)
    if db is None:
        return None
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (DEFER_KEY,)).fetchone()
    finally:
        db.close()
    try:
        value = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or not value.get('jusqu_a'):
        return None
    now = (now or datetime.now(timezone.utc)).isoformat()
    return value if value['jusqu_a'] > now else None


def check_deferred(state_dir):
    found = deferred(state_dir)
    if found:
        raise Stop(found.get('motif') or 'chatgpt_limite_usage_atteinte')


def defer(state_dir, code, now=None):
    db = _db(state_dir)
    if db is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (DEFER_KEY,)).fetchone()
        try:
            previous = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            previous = {}
        tries = min(int(previous.get('essais') or 0) + 1, len(DELAYS_MINUTES)) if previous.get('motif') == code else 1
        value = {'motif': code, 'essais': tries, 'depuis': previous.get('depuis') if previous.get('motif') == code else now.isoformat(),
                 'jusqu_a': (now + timedelta(minutes=DELAYS_MINUTES[tries - 1])).isoformat()}
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (DEFER_KEY, json.dumps(value)))
        db.commit()
        return value
    finally:
        db.close()


def clear(state_dir):
    db = _db(state_dir)
    if db is None:
        return
    try:
        if db.execute('SELECT 1 FROM settings WHERE key=?', (DEFER_KEY,)).fetchone():
            db.execute('DELETE FROM settings WHERE key=?', (DEFER_KEY,))
            db.commit()
    finally:
        db.close()


def next_check(state_dir, code=''):
    """Échéance à donner à un travail reporté (une seule ligne dans la file, jamais une nouvelle mission)."""
    from .sobriete5682 import CODE, tomorrow
    if code == CODE:
        return tomorrow()
    found = deferred(state_dir)
    return found['jusqu_a'] if found else (datetime.now(timezone.utc) + timedelta(minutes=DELAYS_MINUTES[0])).isoformat()


# ------------------------------------------------------------------------------------------------ liaison poste → serveur
def _key(passphrase, salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def _check_passphrase(passphrase):
    passphrase = str(passphrase or '')
    if len(passphrase) < MIN_PASSPHRASE or len(passphrase) > 200:
        raise Stop('phrase_de_passe_trop_courte')
    return passphrase


def export_session(desk, owner, passphrase):
    """Poste : session sélectionnée, chiffrée, sans l'identifiant d'hôte du poste. Le poste renonce ensuite à ses jetons (un seul renouveleur)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from . import chatgpt5662
    passphrase = _check_passphrase(passphrase)
    record = chatgpt5662.load_record(desk.c['state_dir'])
    if not record or not record.get('refresh_token'):
        raise Stop('chatgpt_non_connecte')
    payload = json.dumps({k: record.get(k) for k in RECORD_FIELDS}, ensure_ascii=False).encode()
    salt, nonce = os.urandom(16), os.urandom(12)
    sealed = AESGCM(_key(passphrase, salt)).encrypt(nonce, payload, BUNDLE_PREFIX.encode())
    chatgpt5662.clear_tokens(desk.c['state_dir'])
    acc = chatgpt5662.account(desk)
    desk.setting(chatgpt5662.ACCOUNT_KEY, {**acc, 'etat': 'transferee', 'plan': False})
    desk.audit('chatgpt5682_session_exportee', {'par': str(owner)[:120]})
    return {'ok': True, 'paquet': BUNDLE_PREFIX + base64.b64encode(salt + nonce + sealed).decode(),
            'message': 'Session chiffrée prête. Ce poste n’utilise plus cette connexion.'}


def import_session(desk, owner, bundle, passphrase, transport=None):
    """Serveur : importe la session sans toucher à SON identifiant d'hôte. Une identité illisible ne remplace pas la connexion active."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from . import catalogue5682, chatgpt5662
    passphrase = _check_passphrase(passphrase)
    text = str(bundle or '').strip()
    if not text.startswith(BUNDLE_PREFIX) or len(text) > 60000:
        raise Stop('paquet_session_invalide')
    try:
        raw = base64.b64decode(text[len(BUNDLE_PREFIX):], validate=True)
        record = json.loads(AESGCM(_key(passphrase, raw[:16])).decrypt(raw[16:28], raw[28:], BUNDLE_PREFIX.encode()))
    except (InvalidTag, ValueError):
        raise Stop('paquet_session_invalide') from None
    if not isinstance(record, dict) or not record.get('refresh_token') or not record.get('client_id') or not record.get('subject'):
        raise Stop('paquet_session_invalide')
    previous = chatgpt5662.account(desk)
    if previous.get('subject') and previous.get('etat') == 'connecte' and previous['subject'] != record['subject']:
        raise Stop('chatgpt_compte_different')
    record['ext_agent_host_id'] = chatgpt5662.host_id(desk)
    chatgpt5662.save_record(desk.c['state_dir'], record)
    scopes = sorted(record.get('scopes') or [])
    desk.setting(chatgpt5662.ACCOUNT_KEY, {'email': str(record.get('email') or ''), 'subject': str(record['subject']), 'client_id': record['client_id'],
                                           'scopes': scopes, 'plan': chatgpt5662.PLAN_SCOPE in scopes, 'connecte_le': datetime.now(timezone.utc).isoformat(),
                                           'par': str(owner)[:120], 'etat': 'connecte', 'bienvenue_vue': False})
    desk.audit('chatgpt5682_session_importee', {'par': str(owner)[:120], 'abonnement_utilisable': chatgpt5662.PLAN_SCOPE in scopes})
    try:
        catalogue5682.refresh(desk, chatgpt5662.PROVIDER_ID, transport)
    except Stop:
        pass
    return {'ok': True, 'message': 'Session ChatGPT reçue. Ce serveur gère désormais les renouvellements.'}
