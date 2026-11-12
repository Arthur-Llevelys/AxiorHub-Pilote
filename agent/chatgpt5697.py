"""5.6.97 (lot chatgpt, A04 à A08) : connexion « Sign in with ChatGPT » suivie sur le poste, liaison poste → serveur par code court,
transfert vérifié de la session, connexion séparée de l'activation.

A04 — Poste. « Continuer avec ChatGPT » ouvre une TENTATIVE : identifiant opaque (distinct de ``state``) lié au compte AxiorHub et à
sa session d'administration ; ``state``, ``nonce`` et vérificateur PKCE S256 neufs, à usage unique (``state`` gardé haché, consommé par
une écriture conditionnelle). Le port local est ouvert AVANT l'onglet d'OpenAI (port préféré, sinon un port libre, même chemin), sur
127.0.0.1 seulement, et fermé à l'issue, à l'annulation ou à l'expiration. Un retour étranger (autre ``state``, paramètre dupliqué) ne
ferme pas l'écoute. ``GET m5662/chatgpt/statut`` n'est lisible que depuis la session qui a créé la tentative (404 sinon, no-store).

A05 — Serveur. « Continuer avec ChatGPT » crée une LIAISON (table ``ia_liaisons``, dix minutes) et ouvre ``axiorhub://chatgpt/liaison?id=…``
(identifiant opaque seulement). AxiorHub Poste la revendique auprès d'un serveur HTTPS déjà confirmé par l'utilisateur
(``/liaison-ia/v1/reclamer`` : une seule revendication, écriture conditionnelle) avec l'empreinte SHA-256 d'une preuve aléatoire de
32 octets qu'il garde, et affiche le code court que l'administrateur compare sur l'écran du serveur avant d'autoriser. Chaque appel du
poste contrôle état, expiration et validité de la session d'administration ; la preuve est exigée après la revendication. Aucun port
n'est ouvert sur Internet : le poste appelle le serveur, jamais l'inverse.

A06 — Transfert. Le poste mène la connexion d'OpenAI avec le nonce de la liaison, garde ``state`` et le vérificateur, et envoie la
session candidate (``chatgpt5682.RECORD_FIELDS``) par ``/liaison-ia/v1/recevoir``. Le serveur revérifie signature, émetteur,
destinataire, nonce et expiration, le compte et le droit d'utiliser le forfait, fait un test de génération (« Réponds uniquement OK »,
``store: false``), écrit un fichier chiffré versionné puis publie en une transaction (référence active, fiche du compte, liaison
« reçue ») si la liaison, la session d'administration et la révision de la connexion n'ont pas changé. Un échec laisse la connexion
précédente en place. Le poste n'efface sa copie temporaire qu'après l'accusé de réception ou l'expiration ; une répétition après
« reçue » ne refait rien.

A07 — Se connecter ne change ni le mode, ni les routes, ni les automatismes : l'activation reste dans « Mode de l'IA » (politique5682).

Diagnostics : ni jeton, ni preuve, ni paramètre OAuth dans les tables de liaison, les journaux ou les réponses ; heure, opération et
motif normalisé seulement. Option de déploiement : ``"liaison_ia": {"active": true}`` dans la configuration du serveur, désactivée par
défaut ; les méthodes d'origine restent sous « Autres méthodes » et leurs adresses restent valides.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html import escape as e
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import urllib.parse
import uuid

from . import chatgpt5662 as cg
from .common import Stop

TENTATIVE_MINUTES = cg.ATTEMPT_MINUTES
LIAISON_MINUTES = 10
RETENTION_HEURES = 24                      # lignes de tentative et de liaison effacées après 24 h (aucun secret n'y reste)
CORPS_MAX = 64 * 1024                      # appel natif du poste (session candidate comprise)
DEBIT_ADRESSE = (30, 60)                   # appels natifs permis par adresse et par minute
DEBIT_TOTAL = (240, 60)
CHEMIN_NATIF = '/liaison-ia/v1/'
ROUTES_NATIVES = ('reclamer', 'etat', 'recevoir')
ID_RE = re.compile(r'[A-Za-z0-9_-]{24,64}')
EMPREINTE_RE = re.compile(r'[0-9a-f]{64}')
PREUVE_RE = re.compile(r'[A-Za-z0-9_-]{43}')          # 32 octets aléatoires en base64url, sans remplissage
SERVEURS_KEY = 'chatgpt5697:serveurs'
SERVEURS_MAX = 5
TEST_TEXTE = 'Réponds uniquement OK'
TEST_SECONDES = 30
TRANSPORT_SERVEUR = None                   # essais : appels natifs du poste simulés (méthode, adresse, corps, en-têtes) → (statut, objet)

ETATS_TENTATIVE = {'en_cours': 'Connexion en cours : terminez l’autorisation dans l’onglet d’OpenAI.',
                   'verification': 'Vérification de la connexion…',
                   'reussie': 'Abonnement autorisé : AxiorHub peut utiliser votre abonnement ChatGPT.',
                   'sans_forfait': 'Compte connecté, abonnement non autorisé : reconnectez-vous et acceptez l’utilisation de l’abonnement.',
                   'transmise': 'Connexion transmise au serveur du cabinet.',
                   'refusee': 'Connexion refusée sur la page d’OpenAI.',
                   'echec': 'Connexion non aboutie.',
                   'expiree': 'Délai dépassé : recommencez la connexion.',
                   'annulee': 'Connexion annulée.'}
ETATS_LIAISON = {'attente_poste': 'En attente d’AxiorHub Poste : autorisez l’ouverture d’AxiorHub Poste dans votre navigateur.',
                 'a_approuver': 'Comparez le code affiché sur le poste avec celui-ci, puis autorisez.',
                 'autorisee': 'Autorisé : terminez la connexion sur la page d’OpenAI ouverte sur le poste.',
                 'verification': 'Vérification de la session reçue…',
                 'recue': 'Abonnement autorisé : le serveur utilise désormais cette connexion et gère seul ses renouvellements.',
                 'sans_forfait': 'Compte connecté, abonnement non autorisé : la connexion en service est conservée.',
                 'echec': 'Connexion non aboutie : la connexion en service est conservée.',
                 'annulee': 'Liaison annulée : la connexion en service est conservée.',
                 'expiree': 'Délai dépassé : recommencez la liaison.'}
ETATS_POSTE = {'a_approuver': 'Comparez ce code avec celui affiché sur l’écran du serveur, puis autorisez sur le serveur.',
               'openai': 'Connectez-vous sur la page d’OpenAI, puis acceptez.',
               'transfert': 'Transmission de la connexion au serveur…',
               'recue': 'Connexion transmise : le serveur l’utilise désormais. Ce poste garde sa propre connexion.',
               'sans_forfait': 'Compte connecté, abonnement non autorisé : rien n’a été transmis.',
               'echec': 'Connexion non aboutie : rien n’a été modifié sur le serveur.',
               'annulee': 'Liaison annulée ou inconnue du serveur.',
               'expiree': 'Délai dépassé : recommencez depuis le serveur.'}
TERMINAUX_TENTATIVE = frozenset(('reussie', 'sans_forfait', 'transmise', 'refusee', 'echec', 'expiree', 'annulee'))
TERMINAUX_LIAISON = frozenset(('recue', 'sans_forfait', 'echec', 'annulee', 'expiree'))
TERMINAUX_POSTE = frozenset(('recue', 'sans_forfait', 'echec', 'annulee', 'expiree'))
# Motifs publics normalisés (journaux, réponses) : jamais un message brut d'OpenAI ni un paramètre OAuth.
MOTIFS = {'chatgpt_autorisation_refusee': 'refus_utilisateur', 'chatgpt_autorisation_echouee': 'autorisation_echouee',
          'chatgpt_code_refuse': 'code_refuse', 'chatgpt_client_different': 'client_different', 'chatgpt_compte_different': 'compte_different',
          'chatgpt_inscription_incomplete': 'inscription_incomplete', 'chatgpt_code_absent': 'code_absent',
          'chatgpt_jeton_identite_invalide': 'jeton_invalide', 'chatgpt_abonnement_non_autorise': 'abonnement_non_autorise',
          'test_generation_echoue': 'test_generation_echoue', 'session_admin_fermee': 'session_admin_fermee',
          'reglage_modifie_entre_temps': 'reglage_modifie', 'liaison_annulee': 'annulee', 'liaison_expiree': 'expiree',
          'session_invalide': 'session_invalide', 'chatgpt_port_local_indisponible': 'port_local_indisponible'}
MOTIFS_TEXTE = {'refus_utilisateur': 'refus sur la page d’OpenAI', 'compte_different': 'autre compte ChatGPT que celui connecté (cochez « Remplacer le compte connecté » pour l’accepter)',
                'client_different': 'inscription OpenAI différente de celle du serveur', 'jeton_invalide': 'jeton d’identité refusé',
                'abonnement_non_autorise': 'utilisation de l’abonnement non accordée', 'test_generation_echoue': 'le test de génération n’a pas abouti',
                'session_admin_fermee': 'session d’administration fermée', 'reglage_modifie': 'la connexion a changé entre-temps',
                'code_refuse': 'code d’autorisation refusé par OpenAI', 'autorisation_echouee': 'autorisation non aboutie',
                'session_invalide': 'session transmise incomplète', 'port_local_indisponible': 'aucun port local disponible',
                'annulee': 'liaison annulée', 'expiree': 'délai dépassé'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS chatgpt_tentatives(id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_empreinte TEXT NOT NULL,
  state_hash TEXT NOT NULL, nonce TEXT NOT NULL, verifier TEXT NOT NULL, redirect_uri TEXT NOT NULL DEFAULT '',
  client_id TEXT NOT NULL DEFAULT '', hote TEXT NOT NULL DEFAULT '', liaison_id TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL,
  motif TEXT NOT NULL DEFAULT '', expire_unix INTEGER NOT NULL, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chatgpt_tentatives_session ON chatgpt_tentatives(session_empreinte, etat);
CREATE TABLE IF NOT EXISTS ia_liaisons(id TEXT PRIMARY KEY, instance TEXT NOT NULL, admin TEXT NOT NULL, session_empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, expire_unix INTEGER NOT NULL, preuve_hash TEXT NOT NULL DEFAULT '', code_court TEXT NOT NULL DEFAULT '',
  nonce TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', remplacement INTEGER NOT NULL DEFAULT 0,
  fichier TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ia_liaisons_poste(id TEXT PRIMARY KEY, serveur TEXT NOT NULL, etat TEXT NOT NULL, code_court TEXT NOT NULL DEFAULT '',
  tentative TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', expire_unix INTEGER NOT NULL, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ia_liaisons_debit(cle TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ia_liaisons_debit_cle ON ia_liaisons_debit(cle, at);
'''


def _unix():
    return int(time.time())


def _iso(unix=None):
    return datetime.fromtimestamp(_unix() if unix is None else unix, timezone.utc).isoformat()


def _h(text):
    return hashlib.sha256(str(text or '').encode()).hexdigest()


def _db(state_dir):
    db = sqlite3.connect(str(Path(state_dir) / 'desk.sqlite3'), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _motif(code):
    return MOTIFS.get(str(code), 'incident')


def _texte_motif(motif):
    return MOTIFS_TEXTE.get(motif, '')


def _audit(desk, operation, motif=''):
    """Journal : heure (par l'audit), fournisseur, opération et motif normalisé ; aucun jeton, aucune preuve, aucun paramètre OAuth."""
    try:
        desk.audit('chatgpt5697_' + operation, {'fournisseur': 'chatgpt', 'operation': operation, 'motif': motif})
    except sqlite3.Error:
        pass


def option_active(config):
    """Liaison automatique poste → serveur : option de déploiement, désactivée par défaut (A07, « Pilotage »)."""
    value = (config or {}).get('liaison_ia')
    return isinstance(value, dict) and value.get('active') is True


def est_poste():
    try:
        from .maj5661 import platform
        return platform() in ('windows', 'poste')
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------ session d'administration
def empreinte_session(env, owner=''):
    """Empreinte de la session qui fait la demande (jamais transmise par le navigateur) : distribution autonome (standalone_auth),
    page de connexion du serveur (connexion5648), fenêtre du poste, en-tête Basic mémorisé ; à défaut, le compte seul."""
    env = env or {}
    found = str(env.get('axiorhub.session_empreinte') or '')
    if EMPREINTE_RE.fullmatch(found):
        return 'sa:' + found
    user = env.get('axiorhub.user')
    if isinstance(user, dict) and EMPREINTE_RE.fullmatch(str(user.get('session') or '')):
        return 'cx:' + user['session']
    cookie = str(env.get('HTTP_COOKIE') or '')
    try:
        from .poste_session import COOKIE
        for part in cookie.split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE and value:
                return 'po:' + _h(value)
    except ImportError:   # pragma: no cover
        pass
    auth = str(env.get('HTTP_AUTHORIZATION') or '')
    if auth.startswith('Basic '):
        return 'ba:' + _h(auth)
    return 'ac:' + _h('acteur:' + str(owner))


def session_valide(desk, empreinte, env=None):
    """La session d'administration qui a créé la liaison est-elle toujours ouverte ? (déconnexion = liaison close)"""
    kind, _, value = str(empreinte or '').partition(':')
    if kind == 'sa':   # distribution autonome : standalone_auth fournit le contrôle de SES sessions pour les appels natifs
        check = (env or {}).get('axiorhub.sessions_actives')
        try:
            return bool(check and check(value))
        except Exception:   # noqa: BLE001 — contrôle impossible : refus
            return False
    if kind == 'cx':
        try:
            from . import connexion5648
            db = connexion5648.connect(desk.c['state_dir'])
            try:
                row = db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires>?', (value, datetime.now(timezone.utc).isoformat())).fetchone()
            finally:
                db.close()
            return bool(row)
        except (OSError, sqlite3.Error):
            return False
    return kind in ('po', 'ba', 'ac')


def revision_connexion(db):
    """Révision de la connexion ChatGPT : fiche du compte (hors « bienvenue vue ») et référence active. Toute connexion, importation,
    publication ou déconnexion la change."""
    rows = dict(db.execute('SELECT key, value FROM settings WHERE key IN (?,?)', (cg.ACCOUNT_KEY, cg.ACTIVE_KEY)).fetchall())
    try:
        acc = json.loads(rows.get(cg.ACCOUNT_KEY) or '{}')
    except ValueError:
        acc = {}
    acc = {k: v for k, v in (acc if isinstance(acc, dict) else {}).items() if k != 'bienvenue_vue'}
    return _h(json.dumps(acc, sort_keys=True) + '|' + str(rows.get(cg.ACTIVE_KEY) or ''))[:24]


# ------------------------------------------------------------------------------------------------ A04 : tentative suivie (poste)
def _ouverte(state_dir, tid):
    db = _db(state_dir)
    try:
        row = db.execute('SELECT etat, expire_unix FROM chatgpt_tentatives WHERE id=?', (tid,)).fetchone()
    finally:
        db.close()
    return bool(row) and row['etat'] == 'en_cours' and row['expire_unix'] > _unix()


def demarrer(desk, owner, empreinte, port=cg.DEFAULT_PORT, transport=None, liaison=None, minutes=TENTATIVE_MINUTES):
    """Nouvelle tentative suivie : écoute ouverte AVANT l'onglet ; rend ``{url, tentative, expire_le}``.
    ``liaison`` (poste associé à un serveur) : nonce, identifiant d'hôte et client du serveur ; la session obtenue lui est transmise."""
    purger(desk)
    state_dir = desk.c['state_dir']
    tid = secrets.token_urlsafe(18)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    liaison = liaison or {}
    nonce = str(liaison.get('nonce') or secrets.token_urlsafe(32))
    client_id = str(liaison.get('client_id') or '') if liaison else str(cg.account(desk).get('client_id') or '')
    hote = str(liaison.get('instance') or '')
    expire = _unix() + int(minutes * 60)
    db = _db(state_dir)
    try:
        db.execute('INSERT INTO chatgpt_tentatives(id,owner,session_empreinte,state_hash,nonce,verifier,client_id,hote,liaison_id,etat,expire_unix,cree_le,maj_le) '
                   "VALUES(?,?,?,?,?,?,?,?,?,'en_cours',?,?,?)", (tid, str(owner)[:120], empreinte, _h(state), nonce, verifier, client_id, hote,
                                                                str(liaison.get('id') or ''), expire, _iso(), _iso()))
    finally:
        db.close()
    config = desk.c
    try:
        port = cg.listen(config, port, minutes, traiter=lambda query: retour(config, tid, query, transport),
                         arreter=lambda: not _ouverte(config['state_dir'], tid))
    except Stop as ex:
        _fin_tentative(state_dir, tid, 'echec', _motif(ex))
        raise
    redirect = 'http://127.0.0.1:%d%s' % (int(port), cg.CALLBACK_PATH)
    db = _db(state_dir)
    try:
        db.execute('UPDATE chatgpt_tentatives SET redirect_uri=? WHERE id=?', (redirect, tid))
    finally:
        db.close()
    url = cg.authorize_url(desk, state, nonce, verifier, redirect, client_id, hote, hints=not liaison)
    _audit(desk, 'tentative_ouverte', 'liaison' if liaison else '')
    return {'url': url, 'tentative': tid, 'expire_le': _iso(expire)}


def _fin_tentative(state_dir, tid, etat, motif=''):
    db = _db(state_dir)
    try:
        db.execute("UPDATE chatgpt_tentatives SET etat=?, motif=?, verifier='', nonce='', maj_le=? WHERE id=?", (etat, motif, _iso(), tid))
    finally:
        db.close()


def retour(config, tid, query, transport=None):
    """Retour d'OpenAI sur l'écoute de la tentative ``tid`` : ``{fin, etat, statut}`` pour la page de retour (état seulement)."""
    try:
        pairs = urllib.parse.parse_qsl(str(query or ''), keep_blank_values=True, max_num_fields=20)
    except ValueError:
        pairs = None
    if not pairs or len({k for k, _ in pairs}) != len(pairs):   # paramètre dupliqué : refusé, sans fermer l'écoute
        return {'fin': False, 'etat': 'Retour non reconnu', 'statut': 400}
    params = dict(pairs)
    from .desk import Desk
    desk = Desk(config)
    try:
        return _retour(desk, tid, params, transport)
    finally:
        desk.db.close()


def _retour(desk, tid, params, transport):
    state_dir = desk.c['state_dir']
    state = params.get('state') or ''
    db = _db(state_dir)
    try:
        row = db.execute('SELECT * FROM chatgpt_tentatives WHERE id=?', (tid,)).fetchone()
        if not row or not state or not hmac.compare_digest(row['state_hash'], _h(state)):
            return {'fin': False, 'etat': 'Retour non reconnu', 'statut': 400}   # retour étranger : l'écoute continue
        now = _unix()
        cur = db.execute("UPDATE chatgpt_tentatives SET etat='verification', verifier='', maj_le=? WHERE id=? AND state_hash=? "
                         "AND etat='en_cours' AND expire_unix>?", (_iso(), tid, row['state_hash'], now))
        if cur.rowcount != 1:   # rejoué, annulé ou expiré : jamais un second échange
            if row['etat'] == 'en_cours':
                db.execute("UPDATE chatgpt_tentatives SET etat='expiree', motif='expiree', verifier='', nonce='', maj_le=? WHERE id=?", (_iso(), tid))
            return {'fin': True, 'etat': 'Retour déjà traité ou expiré', 'statut': 409}
    finally:
        db.close()
    attempt = {'client_id': row['client_id'], 'verifier': row['verifier'], 'redirect_uri': row['redirect_uri'], 'nonce': row['nonce'], 'host': row['hote']}
    try:
        client_id = cg.check_return(params, attempt)
        candidate = cg.exchange(desk, params, attempt, client_id, transport, scope_from_return=False)
        if row['liaison_id']:
            etat = 'transmise' if poste_candidat(desk, row['liaison_id'], candidate) in ('recue', 'transfert') else 'echec'
        else:
            etat = publier_local(desk, row['owner'], candidate, transport)
        motif = ''
    except Stop as ex:
        etat = 'refusee' if str(ex) == 'chatgpt_autorisation_refusee' else 'echec'
        motif = _motif(ex)
    _fin_tentative(state_dir, tid, etat, motif)
    _audit(desk, 'tentative_' + etat, motif)
    return {'fin': True, 'etat': ETATS_TENTATIVE[etat].split(' :')[0], 'statut': 200}


def publier_local(desk, owner, candidate, transport=None):
    """Session obtenue sur CE poste : publiée, sauf un compte sans droit d'utiliser le forfait quand une session opérationnelle existe."""
    acc = cg.account(desk)
    if not candidate['plan'] and acc.get('etat') == 'connecte' and acc.get('plan'):
        return 'sans_forfait'          # état distinct : ne remplace jamais une session qui fonctionne
    cg.publish(desk, candidate, owner, transport)
    return 'reussie' if candidate['plan'] else 'sans_forfait'


def statut_tentative(desk, owner, empreinte, tid):
    db = _db(desk.c['state_dir'])
    try:
        row = db.execute('SELECT * FROM chatgpt_tentatives WHERE id=?', (str(tid or ''),)).fetchone()
        if not row or row['owner'] != str(owner)[:120] or not hmac.compare_digest(row['session_empreinte'], empreinte):
            raise Stop('tentative_inconnue')
        etat = row['etat']
        if etat == 'en_cours' and row['expire_unix'] <= _unix():
            db.execute("UPDATE chatgpt_tentatives SET etat='expiree', motif='expiree', verifier='', nonce='', maj_le=? WHERE id=? AND etat='en_cours'", (_iso(), tid))
            etat = 'expiree'
    finally:
        db.close()
    message = ETATS_TENTATIVE[etat]
    detail = _texte_motif(row['motif'])
    if etat == 'echec' and detail:
        message += ' Motif : %s.' % detail
    return {'etat': etat, 'message': message, 'expire_le': _iso(row['expire_unix'])}


def annuler_tentative(desk, owner, empreinte, tid):
    db = _db(desk.c['state_dir'])
    try:
        cur = db.execute("UPDATE chatgpt_tentatives SET etat='annulee', motif='annulee', verifier='', nonce='', maj_le=? WHERE id=? AND owner=? "
                         "AND session_empreinte=? AND etat='en_cours'", (_iso(), str(tid or ''), str(owner)[:120], empreinte))
    finally:
        db.close()
    if cur.rowcount != 1:
        raise Stop('tentative_inconnue')
    _audit(desk, 'tentative_annulee')
    return {'etat': 'annulee', 'message': ETATS_TENTATIVE['annulee']}


# ------------------------------------------------------------------------------------------------ A05 : liaison (serveur)
def creer_liaison(desk, owner, empreinte, minutes=LIAISON_MINUTES):
    """Navigateur du serveur (session d'administration + CSRF) : identifiant aléatoire, expiration, lien ``axiorhub://`` (identifiant seul)."""
    if not option_active(desk.c):
        raise Stop('liaison_ia_desactivee')
    purger(desk)
    lid = secrets.token_urlsafe(24)
    expire = _unix() + int(minutes * 60)
    db = _db(desk.c['state_dir'])
    try:
        db.execute("INSERT INTO ia_liaisons(id,instance,admin,session_empreinte,etat,expire_unix,revision,cree_le,maj_le) VALUES(?,?,?,?,'attente_poste',?,?,?,?)",
                   (lid, cg.host_id(desk), str(owner)[:120], empreinte, expire, revision_connexion(db), _iso(), _iso()))
    finally:
        db.close()
    _audit(desk, 'liaison_creee')
    return {'liaison': lid, 'lien': 'axiorhub://chatgpt/liaison?' + urllib.parse.urlencode({'id': lid}), 'expire_le': _iso(expire),
            'etat': 'attente_poste', 'message': ETATS_LIAISON['attente_poste']}


def _ligne_admin(db, owner, empreinte, lid):
    row = db.execute('SELECT * FROM ia_liaisons WHERE id=?', (str(lid or ''),)).fetchone()
    if not row or row['admin'] != str(owner)[:120] or not hmac.compare_digest(row['session_empreinte'], empreinte):
        raise Stop('liaison_inconnue')   # réponse neutre : ni l'existence, ni l'état d'une liaison d'une autre session
    return row


def _expirer(db, row):
    if row['etat'] not in TERMINAUX_LIAISON and row['etat'] != 'verification' and row['expire_unix'] <= _unix():
        db.execute("UPDATE ia_liaisons SET etat='expiree', motif='expiree', nonce='', maj_le=? WHERE id=? AND etat=?", (_iso(), row['id'], row['etat']))
        return 'expiree'
    return row['etat']


def statut_liaison(desk, owner, empreinte, lid):
    db = _db(desk.c['state_dir'])
    try:
        row = _ligne_admin(db, owner, empreinte, lid)
        etat = _expirer(db, row)
    finally:
        db.close()
    message = ETATS_LIAISON[etat]
    if etat in ('echec', 'annulee') and _texte_motif(row['motif']):
        message += ' Motif : %s.' % _texte_motif(row['motif'])
    out = {'etat': etat, 'message': message, 'expire_le': _iso(row['expire_unix'])}
    if etat == 'a_approuver':
        out['code'] = row['code_court']
        acc = cg.account(desk)
        out['compte_connecte'] = acc.get('etat') == 'connecte'
    return out


def approuver_liaison(desk, owner, empreinte, lid, remplacement=False):
    """L'administrateur a comparé le code du poste : la liaison passe à « autorisée » et reçoit le nonce destiné à OpenAI."""
    now = _unix()
    db = _db(desk.c['state_dir'])
    try:
        cur = db.execute("UPDATE ia_liaisons SET etat='autorisee', nonce=?, remplacement=?, expire_unix=MAX(expire_unix, ?), maj_le=? WHERE id=? "
                         "AND etat='a_approuver' AND admin=? AND session_empreinte=? AND expire_unix>?",
                         (secrets.token_urlsafe(32), 1 if remplacement else 0, now + LIAISON_MINUTES * 60, _iso(), str(lid or ''), str(owner)[:120],
                          empreinte, now))
    finally:
        db.close()
    if cur.rowcount != 1:
        raise Stop('liaison_inconnue')
    _audit(desk, 'liaison_approuvee', 'remplacement' if remplacement else '')
    return {'etat': 'autorisee', 'message': ETATS_LIAISON['autorisee']}


def annuler_liaison(desk, owner, empreinte, lid):
    """Annuler ne touche à aucun autre travail ; une session en vérification n'est plus publiée (relecture avant validation)."""
    db = _db(desk.c['state_dir'])
    try:
        cur = db.execute("UPDATE ia_liaisons SET etat='annulee', motif='annulee', nonce='', maj_le=? WHERE id=? AND admin=? AND session_empreinte=? "
                         "AND etat IN ('attente_poste','a_approuver','autorisee','verification')", (_iso(), str(lid or ''), str(owner)[:120], empreinte))
    finally:
        db.close()
    if cur.rowcount != 1:
        raise Stop('liaison_inconnue')
    _audit(desk, 'liaison_annulee')
    return {'etat': 'annulee', 'message': ETATS_LIAISON['annulee']}


# ---------------------------------------------------------------- appels natifs du poste (``/liaison-ia/v1/*``, HTTPS, sans session navigateur)
class Refus(Stop):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status


def _debit(db, adresse):
    now = time.time()
    db.execute('DELETE FROM ia_liaisons_debit WHERE at<?', (now - 3600,))
    cle = _h('ip:' + str(adresse))[:32]
    if db.execute('SELECT COUNT(*) FROM ia_liaisons_debit WHERE cle=? AND at>=?', (cle, now - DEBIT_ADRESSE[1])).fetchone()[0] >= DEBIT_ADRESSE[0] or \
            db.execute('SELECT COUNT(*) FROM ia_liaisons_debit WHERE at>=?', (now - DEBIT_TOTAL[1],)).fetchone()[0] >= DEBIT_TOTAL[0]:
        raise Refus('429 Too Many Requests', 'trop_de_demandes')
    db.execute('INSERT INTO ia_liaisons_debit(cle, at) VALUES(?,?)', (cle, now))


def _ligne_poste(db, desk, lid, preuve, env):
    """Ligne revendiquée par CE poste : preuve comparée en temps constant, session d'administration encore ouverte."""
    row = db.execute('SELECT * FROM ia_liaisons WHERE id=?', (lid,)).fetchone()
    if not row or not row['preuve_hash'] or not PREUVE_RE.fullmatch(preuve or '') or not hmac.compare_digest(row['preuve_hash'], _h(preuve)):
        raise Refus('404 Not Found', 'liaison_inconnue')
    if row['etat'] not in TERMINAUX_LIAISON and not session_valide(desk, row['session_empreinte'], env):
        db.execute("UPDATE ia_liaisons SET etat='annulee', motif='session_admin_fermee', nonce='', maj_le=? WHERE id=?", (_iso(), lid))
        _audit(desk, 'liaison_annulee', 'session_admin_fermee')
        raise Refus('404 Not Found', 'liaison_inconnue')
    return row


def reclamer(desk, lid, empreinte, env=None):
    """Une seule revendication : ``attente_poste`` → ``a_approuver`` par écriture conditionnelle ; rend le code court à comparer."""
    if not ID_RE.fullmatch(str(lid or '')) or not EMPREINTE_RE.fullmatch(str(empreinte or '')):
        raise Refus('404 Not Found', 'liaison_inconnue')
    db = _db(desk.c['state_dir'])
    try:
        row = db.execute('SELECT * FROM ia_liaisons WHERE id=?', (lid,)).fetchone()
        if not row or row['etat'] != 'attente_poste':
            raise Refus('404 Not Found', 'liaison_inconnue')
        if not session_valide(desk, row['session_empreinte'], env):
            db.execute("UPDATE ia_liaisons SET etat='annulee', motif='session_admin_fermee', maj_le=? WHERE id=? AND etat='attente_poste'", (_iso(), lid))
            raise Refus('404 Not Found', 'liaison_inconnue')
        code = '%03d %03d' % (secrets.randbelow(1000), secrets.randbelow(1000))   # à comparer : ni mot de passe, ni jeton du poste
        cur = db.execute("UPDATE ia_liaisons SET etat='a_approuver', preuve_hash=?, code_court=?, maj_le=? WHERE id=? AND etat='attente_poste' "
                         "AND expire_unix>?", (empreinte, code, _iso(), lid, _unix()))
        if cur.rowcount != 1:
            raise Refus('404 Not Found', 'liaison_inconnue')
    finally:
        db.close()
    _audit(desk, 'liaison_revendiquee')
    return {'etat': 'a_approuver', 'code': code, 'expire_le': _iso(row['expire_unix'])}


def etat_poste(desk, lid, preuve, env=None):
    """État pour le poste ; le nonce (et l'hôte, le client attendu) seulement une fois la liaison autorisée. « reçue » reste lisible."""
    db = _db(desk.c['state_dir'])
    try:
        row = _ligne_poste(db, desk, lid, preuve, env)
        etat = _expirer(db, row)
    finally:
        db.close()
    out = {'etat': etat, 'code': row['code_court']}
    if etat == 'autorisee':
        acc = cg.account(desk)
        out.update({'nonce': row['nonce'], 'instance': row['instance'],
                    'client_id': '' if row['remplacement'] or acc.get('etat') != 'connecte' else str(acc.get('client_id') or '')})
    if etat in ('echec', 'sans_forfait', 'annulee'):
        out['motif'] = row['motif']
    return out


def _session_candidate(session):
    """Champs admis seulement (``chatgpt5682.RECORD_FIELDS``) ; un champ inconnu ou d'un type inattendu fait refuser l'envoi."""
    from .chatgpt5682 import session_admise
    return session_admise(session)


def verifier_session(desk, session, nonce, transport=None):
    """Revalidation côté serveur : signature, émetteur, destinataire (client émis), expiration et nonce de la liaison ; identité lue dans
    le jeton vérifié ; l'identifiant d'hôte reste celui du serveur."""
    record = _session_candidate(session)
    claims = cg.verify_id_token(record['id_token'], record['client_id'], nonce, transport)
    if str(claims.get('sub') or '') != record['subject']:
        raise Stop('chatgpt_jeton_identite_invalide')
    scopes = sorted({str(x) for x in record['scopes']})
    record.update({'email': str(claims.get('email') or ''), 'issuer': cg.AUTH, 'scopes': scopes, 'ext_agent_host_id': cg.host_id(desk),
                   'saved_at': datetime.now(timezone.utc).isoformat()})
    return {'record': record, 'claims': claims, 'scopes': scopes, 'plan': cg.PLAN_SCOPE in scopes, 'client_id': record['client_id'],
            'espace': cg._workspace(claims)}


def tester_generation(desk, candidate, transport=None):
    """Test borné avec le jeton CANDIDAT (jamais la connexion active, jamais ``respond``) : « Réponds uniquement OK », ``store: false``,
    flux terminé exigé. Consomme quelques jetons de l'abonnement."""
    http = transport or cg.http
    token = candidate['record']['access_token']
    try:
        data = cg._json(http('GET', cg.API + '/models', None, {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}, timeout=TEST_SECONDES))
        rows = data.get('models') if isinstance(data.get('models'), list) else data.get('data') or []
        slugs = [str(m.get('slug') or m.get('id')) for m in rows if isinstance(m, dict) and m.get('visibility', 'list') == 'list' and (m.get('slug') or m.get('id'))]
        wanted = str((desk.settings('ai:provider:' + cg.PROVIDER_ID, {}) or {}).get('model') or '')
        if not slugs:
            raise Stop('test_generation_echoue')
        body = json.dumps(cg.build_request(wanted if wanted in slugs else slugs[0], [{'role': 'user', 'content': TEST_TEXTE}]), ensure_ascii=False).encode()
        resp = cg.poster_responses(http, token, body, TEST_SECONDES)   # transport inventorié dans chatgpt5662
        try:
            cg.read_stream(iter(resp.readline, b'') if hasattr(resp, 'readline') else resp, deadline=time.monotonic() + TEST_SECONDES)
        finally:
            try:
                resp.close()
            except Exception:   # noqa: BLE001
                pass
    except cg.HTTPFailure as ex:
        raise Stop('chatgpt_abonnement_non_autorise' if cg.PLAN_ERRORS.get(ex.code) in ('chatgpt_compte_non_eligible', 'chatgpt_acces_refuse')
                   else 'test_generation_echoue') from None
    except Stop as ex:
        if str(ex) in ('chatgpt_compte_non_eligible', 'chatgpt_acces_refuse'):
            raise Stop('chatgpt_abonnement_non_autorise') from None
        raise Stop('test_generation_echoue') from None
    except (TimeoutError, OSError):
        raise Stop('test_generation_echoue') from None


def _verifier_compte(desk, row, candidate):
    acc = cg.account(desk)
    if acc.get('etat') == 'connecte' and acc.get('subject') and not row['remplacement']:
        if acc['subject'] != candidate['record']['subject']:
            raise Stop('chatgpt_compte_different')    # remplacement explicite demandé à l'approbation
        if acc.get('client_id') and acc['client_id'] != candidate['client_id']:
            raise Stop('chatgpt_client_different')
    if not candidate['plan']:
        raise Stop('chatgpt_abonnement_non_autorise')   # un compte sans forfait ne remplace pas une session opérationnelle


def _publier(desk, row, nom, candidate, env):
    """Une transaction, sous le verrou de renouvellement : référence active, fiche du compte et liaison « reçue », seulement si la
    liaison est toujours en vérification, non expirée, la session d'administration ouverte et la révision inchangée."""
    state_dir = desk.c['state_dir']
    if not session_valide(desk, row['session_empreinte'], env):
        raise Stop('session_admin_fermee')
    with cg._Lock(state_dir):
        db = _db(state_dir)
        try:
            db.execute('BEGIN IMMEDIATE')
            now = db.execute('SELECT etat, expire_unix FROM ia_liaisons WHERE id=?', (row['id'],)).fetchone()
            if not now or now['etat'] != 'verification':
                raise Stop('liaison_annulee')
            if now['expire_unix'] <= _unix():
                raise Stop('liaison_expiree')
            if revision_connexion(db) != row['revision']:
                raise Stop('reglage_modifie_entre_temps')
            found = db.execute('SELECT value FROM settings WHERE key=?', (cg.ACCOUNT_KEY,)).fetchone()
            previous = json.loads(found[0]) if found else {}
            active = db.execute('SELECT value FROM settings WHERE key=?', (cg.ACTIVE_KEY,)).fetchone()
            ancien = (json.loads(active[0]) if active else {}).get('fichier') or ''
            value = {'fichier': nom, 'precedent': ancien, 'le': _iso(), 'origine': 'liaison'}
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (cg.ACTIVE_KEY, json.dumps(value)))
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (cg.ACCOUNT_KEY, json.dumps(cg.account_value(candidate, row['admin'], previous))))
            db.execute("UPDATE ia_liaisons SET etat='recue', nonce='', motif='', maj_le=? WHERE id=?", (_iso(), row['id']))
            db.execute('COMMIT')
        except BaseException:
            try:
                db.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise
        finally:
            db.close()


def _terminer_echec(desk, lid, code, nom=''):
    """Échec de la réception : la connexion en service reste en place, le fichier candidat est effacé. Une liaison annulée ou expirée
    entre-temps garde son état."""
    etat = 'sans_forfait' if code == 'chatgpt_abonnement_non_autorise' else 'echec'
    motif = _motif(code)
    db = _db(desk.c['state_dir'])
    try:
        db.execute("UPDATE ia_liaisons SET etat=?, motif=?, nonce='', fichier='', maj_le=? WHERE id=? AND etat='verification'", (etat, motif, _iso(), lid))
        db.execute("UPDATE ia_liaisons SET fichier='' WHERE id=?", (lid,))
        final = db.execute('SELECT etat, motif FROM ia_liaisons WHERE id=?', (lid,)).fetchone()
    finally:
        db.close()
    if nom:
        _effacer_candidat(desk, nom)
    etat, motif = (final['etat'], final['motif']) if final else (etat, motif)
    _audit(desk, 'liaison_' + etat, motif)
    return {'etat': etat, 'motif': motif}


def _effacer_candidat(desk, nom):
    """Fichier candidat jamais publié (ou devenu orphelin) : effacé, sauf s'il est la référence active."""
    if not cg.VERSIONED.fullmatch(str(nom or '')) or cg._active_name(desk.c['state_dir']) == nom:
        return
    try:
        os.unlink(cg.vault_dir(desk.c['state_dir']) / nom)
    except OSError:
        pass


def recevoir(desk, lid, preuve, session, env=None, transport=None):
    """Session candidate du poste : réservation conditionnelle (« autorisée » → « vérification »), vérifications sans verrou SQLite pendant
    les appels à OpenAI, fichier chiffré versionné, publication si tout est encore valable. Répétition après « reçue » : rien n'est refait."""
    if not ID_RE.fullmatch(str(lid or '')):
        raise Refus('404 Not Found', 'liaison_inconnue')
    db = _db(desk.c['state_dir'])
    try:
        row = _ligne_poste(db, desk, lid, preuve, env)
        etat = _expirer(db, row)
        if etat == 'recue':
            return {'etat': 'recue'}                     # accusé perdu : répétition autorisée sans nouvel échange
        if etat != 'autorisee':
            return {'etat': etat, 'motif': row['motif']}
        cur = db.execute("UPDATE ia_liaisons SET etat='verification', maj_le=? WHERE id=? AND etat='autorisee' AND expire_unix>?", (_iso(), lid, _unix()))
        if cur.rowcount != 1:
            return {'etat': db.execute('SELECT etat FROM ia_liaisons WHERE id=?', (lid,)).fetchone()['etat']}
        row = db.execute('SELECT * FROM ia_liaisons WHERE id=?', (lid,)).fetchone()
    finally:
        db.close()
    nom = ''
    try:
        candidate = verifier_session(desk, session, row['nonce'], transport)
        _verifier_compte(desk, row, candidate)
        tester_generation(desk, candidate, transport)
        nom = 'compte-%s.secret' % uuid.uuid4().hex
        db = _db(desk.c['state_dir'])
        try:   # nom noté AVANT l'écriture : un fichier laissé par un arrêt brutal est retrouvé et purgé
            db.execute('UPDATE ia_liaisons SET fichier=? WHERE id=?', (nom, lid))
        finally:
            db.close()
        cg.save_record(desk.c['state_dir'], candidate['record'], cg.vault_dir(desk.c['state_dir']) / nom)
        _publier(desk, row, nom, candidate, env)
    except Stop as ex:
        return _terminer_echec(desk, lid, str(ex), nom)
    cg.after_publish(desk, True, transport)
    _audit(desk, 'liaison_recue')
    return {'etat': 'recue'}


def route_native(desk, env, path, origin):
    """Filtre WSGI des trois chemins natifs (POST exact) : HTTPS, option active, taille et débit limités, réponses sans cache."""
    def out(status, value):
        return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False),
                'headers': [('Cache-Control', 'no-store'), ('Pragma', 'no-cache')]}
    route = path[len(CHEMIN_NATIF):] if path.startswith(CHEMIN_NATIF) else ''
    if route not in ROUTES_NATIVES or env.get('REQUEST_METHOD') != 'POST' or not option_active(desk.c):
        return out('404 Not Found', {'error': 'introuvable'})
    if getattr(origin, 'scheme', '') != 'https' or (env.get('HTTP_X_FORWARDED_PROTO') != 'https' and env.get('wsgi.url_scheme') != 'https'):
        return out('403 Forbidden', {'error': 'https_obligatoire'})
    try:
        length = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = -1
    if length < 0 or length > CORPS_MAX:
        return out('413 Payload Too Large', {'error': 'requete_trop_longue'})
    if str(env.get('CONTENT_TYPE') or '').split(';')[0].strip() != 'application/json':
        return out('415 Unsupported Media Type', {'error': 'json_requis'})
    raw = env['wsgi.input'].read(length) if length else b''
    try:
        data = json.loads(raw.decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        data = None
    if not isinstance(data, dict):
        return out('400 Bad Request', {'error': 'json_invalide'})
    from .garde5670 import client_ip
    preuve = str(env.get('HTTP_AUTHORIZATION') or '')
    preuve = preuve[7:] if preuve.startswith('Bearer ') else ''
    try:
        db = _db(desk.c['state_dir'])
        try:
            _debit(db, client_ip(env))
        finally:
            db.close()
        attendus = {'reclamer': {'id', 'empreinte'}, 'etat': {'id'}, 'recevoir': {'id', 'session'}}[route]
        if set(data) != attendus:
            raise Refus('400 Bad Request', 'champs_refuses')
        if route == 'reclamer':
            return out('200 OK', reclamer(desk, data['id'], data['empreinte'], env))
        if route == 'etat':
            return out('200 OK', etat_poste(desk, data['id'], preuve, env))
        result = recevoir(desk, data['id'], preuve, data['session'], env)
        return out('409 Conflict' if result['etat'] == 'verification' else '200 OK', result)
    except Refus as ex:
        return out(ex.status, {'error': str(ex)})
    except Stop as ex:
        return out('400 Bad Request', {'error': _motif(ex)})


# ------------------------------------------------------------------------------------------------ A05 et A06 : côté poste
def lire_lien(uri):
    """``axiorhub://chatgpt/liaison?id=…`` → identifiant opaque. Toute autre forme est refusée : aucune commande, aucun domaine, aucun
    autre paramètre n'est accepté depuis le lien."""
    text = str(uri or '')
    if not text or len(text) > 200 or any(ord(c) < 33 or ord(c) > 126 for c in text):
        raise Stop('lien_liaison_invalide')
    p = urllib.parse.urlsplit(text)
    if p.scheme != 'axiorhub' or p.netloc != 'chatgpt' or p.path.rstrip('/') != '/liaison' or p.fragment:
        raise Stop('lien_liaison_invalide')
    try:
        pairs = urllib.parse.parse_qsl(p.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError:
        raise Stop('lien_liaison_invalide') from None
    if len(pairs) != 1 or pairs[0][0] != 'id' or not ID_RE.fullmatch(pairs[0][1]):
        raise Stop('lien_liaison_invalide')
    return pairs[0][1]


def normaliser_serveur(url):
    """Serveur AxiorHub confirmé par l'utilisateur du poste : HTTPS, sans identifiant, paramètre ni fragment ; préfixe du site admis."""
    p = urllib.parse.urlsplit(str(url or '').strip())
    path = p.path.rstrip('/')
    try:
        port_ok = p.port is None or 0 < p.port < 65536
    except ValueError:
        port_ok = False
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment or not port_ok \
            or not re.fullmatch(r'(/[A-Za-z0-9._~-]+){0,4}', path) or len(url) > 300:
        raise Stop('serveur_https_requis')
    return 'https://' + p.netloc.lower() + path


def serveurs(desk):
    value = desk.settings(SERVEURS_KEY, []) or []
    return [s for s in value if isinstance(s, dict) and s.get('url')][:SERVEURS_MAX]


def confirmer_serveur(desk, owner, url, retirer=False):
    url = normaliser_serveur(url)
    items = [s for s in serveurs(desk) if s['url'] != url]
    if not retirer:
        if len(items) >= SERVEURS_MAX:
            raise Stop('trop_de_serveurs')
        items.append({'url': url, 'confirme_le': _iso(), 'par': str(owner)[:120]})
    desk.setting(SERVEURS_KEY, items)
    _audit(desk, 'serveur_retire' if retirer else 'serveur_confirme')
    return {'serveurs': [s['url'] for s in items], 'message': 'Serveur retiré.' if retirer else 'Serveur confirmé pour la liaison.'}


def _appel(serveur, route, corps, preuve=''):
    """Appel natif HTTPS vers un serveur confirmé : certificat vérifié, aucune redirection suivie, réponse bornée. (statut, objet)"""
    url = serveur + CHEMIN_NATIF + route
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if preuve:
        headers['Authorization'] = 'Bearer ' + preuve
    if TRANSPORT_SERVEUR is not None:
        return TRANSPORT_SERVEUR('POST', url, corps, headers)
    from .common import HTTP
    client = HTTP(serveur, timeout=20)
    try:
        raw = client.request('POST', url, json.dumps(corps).encode(), headers, limit=CORPS_MAX)
    except Stop as ex:
        status = int(getattr(ex, 'remote_status', 0) or 0)
        if status:
            return status, {}
        raise
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise Stop('json_http_invalide') from None
    return 200, value if isinstance(value, dict) else {}


def _coffre_poste(desk, lid):
    return Path(desk.c['state_dir']) / 'vault' / 'chatgpt5697' / 'poste' / ('%s.secret' % lid)


def _lire_coffre(desk, lid):
    path = _coffre_poste(desk, lid)
    if not path.is_file():
        return None
    from .vault567 import decrypt
    try:
        return json.loads(decrypt(path, path.read_text(encoding='utf-8').strip()))
    except ValueError:
        return None


def _ecrire_coffre(desk, lid, value):
    from .vault567 import write
    if not ID_RE.fullmatch(lid):
        raise Stop('liaison_inconnue')
    return write(_coffre_poste(desk, lid), json.dumps(value, ensure_ascii=False, separators=(',', ':')), limit=CORPS_MAX)


def _effacer_coffre(desk, lid):
    try:
        os.unlink(_coffre_poste(desk, lid))
    except OSError:
        pass


def _poste_etat(desk, lid, etat, motif=''):
    db = _db(desk.c['state_dir'])
    try:
        db.execute('UPDATE ia_liaisons_poste SET etat=?, motif=?, maj_le=? WHERE id=?', (etat, motif, _iso(), lid))
    finally:
        db.close()
    if etat in TERMINAUX_POSTE:
        _effacer_coffre(desk, lid)       # copie temporaire effacée après l'accusé ou la fin ; la connexion du poste n'est jamais touchée
        _audit(desk, 'poste_' + etat, motif)


def poste_associer(desk, owner, lid):
    """Poste : revendique la liaison auprès d'un serveur confirmé ; la preuve (32 octets) reste chiffrée dans le coffre du poste."""
    if not ID_RE.fullmatch(str(lid or '')):
        raise Stop('lien_liaison_invalide')
    purger(desk)
    db = _db(desk.c['state_dir'])
    try:
        row = db.execute('SELECT * FROM ia_liaisons_poste WHERE id=?', (lid,)).fetchone()
    finally:
        db.close()
    if row:   # double clic : même liaison, même code
        return {'etat': row['etat'], 'code': row['code_court'], 'serveur': row['serveur'], 'message': ETATS_POSTE.get(row['etat'], '')}
    liste = serveurs(desk)
    if not liste:
        raise Stop('aucun_serveur_confirme')
    preuve = secrets.token_urlsafe(32)
    for item in liste:
        try:
            status, out = _appel(item['url'], 'reclamer', {'id': lid, 'empreinte': _h(preuve)})
        except Stop:
            continue
        if status == 200 and out.get('code'):
            break
    else:
        raise Stop('serveur_liaison_inconnu')
    _ecrire_coffre(desk, lid, {'preuve': preuve})
    db = _db(desk.c['state_dir'])
    try:
        db.execute("INSERT INTO ia_liaisons_poste(id,serveur,etat,code_court,expire_unix,cree_le,maj_le) VALUES(?,?,'a_approuver',?,?,?,?)",
                   (lid, item['url'], str(out['code'])[:12], _unix() + LIAISON_MINUTES * 60 + 300, _iso(), _iso()))
    finally:
        db.close()
    _audit(desk, 'poste_revendique')
    return {'etat': 'a_approuver', 'code': str(out['code'])[:12], 'serveur': item['url'], 'message': ETATS_POSTE['a_approuver']}


def poste_candidat(desk, lid, candidate):
    """Session obtenue sur le poste pour le serveur : gardée chiffrée (copie temporaire, isolée de la connexion du poste) puis transmise."""
    from .chatgpt5682 import RECORD_FIELDS
    secret = _lire_coffre(desk, lid) or {}
    if not secret.get('preuve'):
        raise Stop('liaison_inconnue')
    secret['session'] = {k: candidate['record'].get(k) for k in RECORD_FIELDS}
    _ecrire_coffre(desk, lid, secret)
    _poste_etat(desk, lid, 'transfert')
    return poste_transmettre(desk, lid)


def poste_transmettre(desk, lid):
    """Envoi de la session candidate ; la copie n'est effacée qu'après l'accusé « reçue » (ou une fin définitive annoncée par le serveur)."""
    db = _db(desk.c['state_dir'])
    try:
        row = db.execute('SELECT * FROM ia_liaisons_poste WHERE id=?', (lid,)).fetchone()
    finally:
        db.close()
    secret = _lire_coffre(desk, lid) or {}
    if not row or not secret.get('session'):
        return row['etat'] if row else 'annulee'
    try:
        status, out = _appel(row['serveur'], 'recevoir', {'id': lid, 'session': secret['session']}, secret['preuve'])
    except Stop:
        return 'transfert'                                # réponse perdue : la copie est gardée, l'état du serveur sera relu
    etat = str(out.get('etat') or '')
    if status == 200 and etat in TERMINAUX_POSTE:
        _poste_etat(desk, lid, etat, str(out.get('motif') or ''))
        return etat
    if status == 404:
        _poste_etat(desk, lid, 'annulee')
        return 'annulee'
    return 'transfert'


def poste_suivre(desk, owner, empreinte, lid, port=cg.DEFAULT_PORT, transport=None):
    """Poste : relit l'état chez le serveur ; une fois la liaison autorisée, ouvre la tentative d'OpenAI avec le nonce de la liaison.
    L'adresse d'OpenAI n'est rendue qu'une fois (elle n'est pas conservée)."""
    db = _db(desk.c['state_dir'])
    try:
        row = db.execute('SELECT * FROM ia_liaisons_poste WHERE id=?', (str(lid or ''),)).fetchone()
    finally:
        db.close()
    if not row:
        raise Stop('liaison_inconnue')
    def rendu(etat, **extra):
        return {'etat': etat, 'code': row['code_court'], 'message': ETATS_POSTE.get(etat, ''), **extra}
    if row['etat'] in TERMINAUX_POSTE:
        return rendu(row['etat'])
    if row['expire_unix'] <= _unix():
        _poste_etat(desk, lid, 'expiree')
        return rendu('expiree')
    secret = _lire_coffre(desk, lid) or {}
    if not secret.get('preuve'):
        _poste_etat(desk, lid, 'annulee')
        return rendu('annulee')
    if secret.get('session'):
        return rendu(poste_transmettre(desk, lid))
    try:
        status, out = _appel(row['serveur'], 'etat', {'id': lid}, secret['preuve'])
    except Stop:
        return rendu(row['etat'], message='Serveur momentanément injoignable : nouvel essai dans un instant.')
    if status == 404:
        _poste_etat(desk, lid, 'annulee')
        return rendu('annulee')
    etat = str(out.get('etat') or '')
    if etat in TERMINAUX_POSTE:
        _poste_etat(desk, lid, etat, str(out.get('motif') or ''))
        return rendu(etat)
    if etat == 'autorisee' and not row['tentative']:
        nonce = str(out.get('nonce') or '')
        if not 20 <= len(nonce) <= 100:
            return rendu('a_approuver')
        t = demarrer(desk, owner, empreinte, port, transport, liaison={'id': lid, 'nonce': nonce, 'instance': str(out.get('instance') or '')[:80],
                                                                       'client_id': str(out.get('client_id') or '')[:120]})
        db = _db(desk.c['state_dir'])
        try:
            db.execute("UPDATE ia_liaisons_poste SET tentative=?, etat='openai', maj_le=? WHERE id=?", (t['tentative'], _iso(), lid))
        finally:
            db.close()
        return rendu('openai', url=t['url'], tentative=t['tentative'])
    if row['tentative']:
        return rendu('openai', tentative=row['tentative'])
    return rendu('a_approuver')


# ------------------------------------------------------------------------------------------------ purge et rétention
def purger(desk):
    """Réservations expirées closes, fichiers candidats orphelins effacés, lignes de plus de 24 h supprimées (aucun secret n'y reste)."""
    state_dir = desk.c['state_dir']
    now = _unix()
    limite = now - RETENTION_HEURES * 3600
    db = _db(state_dir)
    try:
        db.execute("UPDATE chatgpt_tentatives SET etat='expiree', motif='expiree', verifier='', nonce='', maj_le=? WHERE etat IN ('en_cours','verification') "
                   'AND expire_unix<=?', (_iso(), now - 120))
        db.execute('DELETE FROM chatgpt_tentatives WHERE expire_unix<?', (limite,))
        orphelins = [r['fichier'] for r in db.execute("SELECT fichier FROM ia_liaisons WHERE etat='verification' AND expire_unix<=? AND fichier<>''", (now,))]
        db.execute("UPDATE ia_liaisons SET etat='expiree', motif='expiree', nonce='', fichier='', maj_le=? WHERE etat NOT IN ('recue','sans_forfait','echec','annulee','expiree') "
                   'AND expire_unix<=?', (_iso(), now))
        db.execute('DELETE FROM ia_liaisons WHERE expire_unix<?', (limite,))
        expires_poste = [r['id'] for r in db.execute("SELECT id FROM ia_liaisons_poste WHERE etat NOT IN ('recue','sans_forfait','echec','annulee','expiree') "
                                                     'AND expire_unix<=?', (now,))]
        db.execute('DELETE FROM ia_liaisons_poste WHERE expire_unix<?', (limite,))
        connus = {r['id'] for r in db.execute('SELECT id FROM ia_liaisons_poste')}
        enregistres = {r['fichier'] for r in db.execute("SELECT fichier FROM ia_liaisons WHERE fichier<>''")}
    finally:
        db.close()
    for nom in orphelins:
        _effacer_candidat(desk, nom)
    for lid in expires_poste:
        _poste_etat(desk, lid, 'expiree')
    dossier = Path(state_dir) / 'vault' / 'chatgpt5697' / 'poste'
    if dossier.is_dir():
        for path in dossier.glob('*.secret'):
            if path.stem not in connus:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    _nettoyer_versions(desk, enregistres)


def _nettoyer_versions(desk, garder=()):
    """Fichiers de session versionnés devenus orphelins : la référence active et la précédente sont gardées (appels en cours)."""
    state_dir = desk.c['state_dir']
    dossier = cg.vault_dir(state_dir)
    if not dossier.is_dir():
        return 0
    try:
        ref = desk.settings(cg.ACTIVE_KEY, {}) or {}
    except sqlite3.Error:
        return 0
    keep = {ref.get('fichier'), ref.get('precedent')} | set(garder)
    removed = 0
    for path in dossier.iterdir():
        if cg.VERSIONED.fullmatch(path.name) and path.name not in keep and path.is_file() and not path.is_symlink():
            try:
                if time.time() - path.stat().st_mtime > 3600:   # jamais un fichier en cours d'écriture ou de vérification
                    os.unlink(path)
                    removed += 1
            except OSError:
                pass
    return removed


# ------------------------------------------------------------------------------------------------ API du navigateur (web5662)
def _json_out(value, status='200 OK'):
    from .web440 import json_out
    out = json_out(value, status)
    out['headers'] = [('Cache-Control', 'no-store')]
    return out


ERREURS = {'tentative_inconnue': ('404 Not Found', 'Tentative introuvable.'), 'liaison_inconnue': ('404 Not Found', 'Liaison introuvable.'),
           'liaison_ia_desactivee': ('404 Not Found', 'La liaison automatique avec AxiorHub Poste n’est pas activée sur ce serveur : utilisez « Autres méthodes ».'),
           'aucun_serveur_confirme': ('400 Bad Request', 'Aucun serveur confirmé sur ce poste : ajoutez l’adresse du serveur du cabinet ci-dessous.'),
           'serveur_liaison_inconnu': ('400 Bad Request', 'Aucun serveur confirmé ne connaît cette demande (expirée, déjà utilisée ou serveur différent).'),
           'serveur_https_requis': ('400 Bad Request', 'Adresse refusée : https://, sans identifiant ni paramètre (exemple : https://pilote.example.test/agent-courriel).'),
           'trop_de_serveurs': ('400 Bad Request', 'Cinq serveurs au plus.'), 'lien_liaison_invalide': ('400 Bad Request', 'Lien de liaison refusé.'),
           'chatgpt_port_local_indisponible': ('503 Service Unavailable', 'Aucun port local disponible pour recevoir la réponse d’OpenAI.')}


def api(env, desk, owner, route, data, method):
    """Routes ``chatgpt/statut``, ``chatgpt/annuler``, ``chatgpt/liaison/*`` et ``chatgpt/poste/*`` (administrateur ; CSRF vérifié par web5662)."""
    empreinte = empreinte_session(env, owner)
    try:
        if route == 'chatgpt/statut':
            if data.get('liaison'):
                return _json_out(statut_liaison(desk, owner, empreinte, data['liaison']))
            return _json_out(statut_tentative(desk, owner, empreinte, data.get('tentative')))
        if route == 'chatgpt/liaison/statut':
            return _json_out(statut_liaison(desk, owner, empreinte, data.get('id')))
        if route == 'chatgpt/annuler':
            if data.get('liaison'):
                return _json_out(annuler_liaison(desk, owner, empreinte, data['liaison']))
            return _json_out(annuler_tentative(desk, owner, empreinte, data.get('tentative')))
        if route == 'chatgpt/liaison/creer':
            return _json_out({'ok': True, 'mode': 'liaison', **creer_liaison(desk, owner, empreinte)})
        if route == 'chatgpt/liaison/approuver':
            return _json_out(approuver_liaison(desk, owner, empreinte, data.get('id'), str(data.get('remplacement') or '') in ('on', '1', 'true', 'True')))
        if route == 'chatgpt/liaison/annuler':
            return _json_out(annuler_liaison(desk, owner, empreinte, data.get('id')))
        if route == 'chatgpt/poste/serveur':
            return _json_out(confirmer_serveur(desk, owner, data.get('url'), str(data.get('retirer') or '') in ('on', '1', 'true')))
        if route == 'chatgpt/poste/associer':
            return _json_out(poste_associer(desk, owner, data.get('id')))
        if route == 'chatgpt/poste/suivre':
            return _json_out(poste_suivre(desk, owner, empreinte, data.get('id')))
    except Stop as ex:
        code = str(ex)
        status, message = ERREURS.get(code, ('400 Bad Request', 'Action impossible (%s).' % code))
        return _json_out({'error': code, 'message': message}, status)
    raise Stop('route_inconnue')


def connecter(env, desk, owner, local, manuel=False):
    """« Continuer avec ChatGPT » : poste → tentative suivie ; serveur avec l'option → liaison ; sinon None (procédure manuelle)."""
    if manuel:
        return None
    if local:
        empreinte = empreinte_session(env, owner)
        try:
            out = demarrer(desk, owner, empreinte)
        except Stop as ex:
            status, message = ERREURS.get(str(ex), ('400 Bad Request', 'Connexion impossible (%s).' % ex))
            return _json_out({'error': str(ex), 'message': message}, status)
        return _json_out({'ok': True, 'mode': 'poste', **out, 'message': ETATS_TENTATIVE['en_cours']})
    if option_active(desk.c):
        return _json_out({'ok': True, 'mode': 'liaison', **creer_liaison(desk, owner, empreinte_session(env, owner))})
    return None


# ------------------------------------------------------------------------------------------------ affichage
def etat_connexion(desk):
    """État lisible (A01) : « À connecter », « Compte connecté, abonnement non autorisé », « Abonnement autorisé », « Limite atteinte »,
    « À reconnecter » ; séparé du mode en service et de la politique d'envoi."""
    from . import chatgpt5682
    acc = cg.account(desk)
    if acc.get('etat') == 'connecte':
        record = None
        try:
            record = cg.load_record(desk.c['state_dir'])
        except Stop:
            record = None
        if not record or not record.get('refresh_token'):
            return 'À reconnecter', acc
        if chatgpt5682.deferred(desk.c['state_dir']):
            return 'Limite atteinte', acc
        return ('Abonnement autorisé' if acc.get('plan') else 'Compte connecté, abonnement non autorisé'), acc
    if acc.get('etat') == 'transferee':
        return 'Connexion transférée au serveur', acc
    return 'À connecter', acc


def section_connexion(desk, prefix, local, connected):
    """Bouton « Continuer avec ChatGPT », suivi en direct (aria-live) et « Autres méthodes » (procédures d'origine, adresses inchangées)."""
    from .ia5682 import transfer_html
    poste = est_poste()
    liaison = (not local) and option_active(desk.c)
    mode = 'poste' if local else ('liaison' if liaison else 'manuel')
    attempt = desk.settings(cg.ATTEMPT_KEY, {}) or {}
    pending = bool(attempt) and attempt.get('expires', '') > cg._now().isoformat()
    label, acc = etat_connexion(desk)
    when = acc.get('connecte_le') or ''
    body = ('<p class="c5697-etat" data-c5697-etat><strong>État : %s</strong>%s</p>' % (
        e(label), (' · dernier contrôle : %s' % e(when[:16].replace('T', ' '))) if when else ''))
    body += ('<form class="d51-inline c5662-connect" data-c5662-open data-mode="%s"><button class="ax-btn c5662-siwc" type="submit">Continuer avec ChatGPT</button> '
             '<button class="ax-btn ghost" type="button" data-c5697-annuler hidden>Annuler</button>'
             '<p role="status" aria-live="polite" class="m5-status c5697-suivi"></p><div class="c5697-zone" data-c5697-zone hidden></div></form>') % mode
    if liaison:
        body += ('<p class="ax-muted">Le navigateur demande d’ouvrir AxiorHub Poste. Comparez ensuite le code affiché sur les deux écrans : '
                 'le poste ouvre OpenAI et transmet la session, sans adresse ni paquet à recopier. AxiorHub Poste doit connaître ce serveur '
                 '(Paramètres › Modèles d’IA › Serveur du cabinet, sur le poste).</p>')
    autres = ''
    if not local:
        autres += ('<h3>Recopier l’adresse de retour</h3>%s<p>Après votre accord sur la page d’OpenAI, votre navigateur affiche une page introuvable dont '
                   'l’adresse commence par <code>http://127.0.0.1:1455/auth/callback</code>. C’est normal : copiez cette adresse complète et '
                   'collez-la ici. Elle ne contient qu’un code à usage unique, inutilisable sans le secret resté sur le serveur.</p>'
                   '<form class="m5-form" data-api="m5662/chatgpt/terminer" data-reload><label class="m5-field">Adresse affichée après l’accord'
                   '<input name="adresse" maxlength="4000" autocomplete="off" placeholder="http://127.0.0.1:1455/auth/callback?code=…"></label>'
                   '<button class="ax-btn" type="submit">Terminer la connexion</button><p role="status" class="m5-status"></p></form>') % (
            ('<form class="d51-inline c5662-connect" data-c5662-open data-mode="manuel"><button class="ax-btn ghost" type="submit">Ouvrir la page '
             'd’OpenAI (procédure manuelle)</button><p role="status" class="m5-status"></p></form>') if liaison else '')
    autres += transfer_html(connected, local)
    body += '<details class="c5697-autres"%s><summary>Autres méthodes</summary>%s</details>' % (' open' if pending and not local else '', autres)
    if poste:
        liste = ''.join('<li>%s <form class="m5-form d51-inline" data-api="m5662/chatgpt/poste/serveur" data-reload><input type="hidden" name="url" value="%s">'
                        '<input type="hidden" name="retirer" value="1"><button class="ax-btn ghost" type="submit">Retirer</button></form></li>' % (
                            e(s['url']), e(s['url'], quote=True)) for s in serveurs(desk))
        body += ('<details class="c5697-serveurs"><summary>Serveur du cabinet (liaison avec AxiorHub sur un serveur)</summary>'
                 '<p>Un serveur AxiorHub peut demander à ce poste de connecter ChatGPT pour lui. Seuls les serveurs confirmés ici sont contactés, '
                 'en HTTPS avec certificat valide.</p><ul>%s</ul>'
                 '<form class="m5-form" data-api="m5662/chatgpt/poste/serveur" data-reload><label class="m5-field">Adresse du serveur (avec son préfixe)'
                 '<input name="url" type="url" maxlength="300" autocomplete="off" placeholder="https://pilote.example.test/agent-courriel" required></label>'
                 '<button class="ax-btn ghost" type="submit">Confirmer ce serveur</button><p role="status" class="m5-status"></p></form></details>'
                 '<section class="c5697-poste" data-c5697-poste hidden aria-labelledby="c5697-poste-titre"><h3 id="c5697-poste-titre">Liaison demandée par le serveur du cabinet</h3>'
                 '<p>Le serveur demande à ce poste de connecter ChatGPT pour lui. Ce poste garde sa propre connexion.</p>'
                 '<button class="ax-btn" type="button" data-c5697-associer>Associer ce poste</button>'
                 '<p role="status" aria-live="polite" class="m5-status" data-c5697-poste-suivi></p><div data-c5697-poste-zone></div></section>') % (
            liste or '<li class="ax-muted">Aucun serveur confirmé.</li>')
    return body


def carte_guide(desk, env, prefix, admin=False):
    """A07 : sous-étape « Connecter l'IA » du guide d'installation : même carte et mêmes routes que Paramètres ; aucun secret redemandé."""
    if not admin:
        return ''
    from .web5662 import local_browser
    label, acc = etat_connexion(desk)
    connected = acc.get('etat') == 'connecte' and bool(acc.get('plan'))
    return ('<section class="ax-card" id="chatgpt5697-guide"><h4>ChatGPT · abonnement</h4>%s'
            '<p class="ax-muted">Se connecter ne change ni le mode de l’IA, ni les routes, ni les automatismes. Pour utiliser ChatGPT, choisissez ensuite '
            '« Abonnement ChatGPT uniquement » dans <a href="%s">Paramètres › Modèles d’IA</a>, avec l’autorisation d’envoi.</p></section>') % (
        section_connexion(desk, prefix, local_browser(env or {}), connected), e(prefix + '/parametres?rubrique=ia', quote=True))
