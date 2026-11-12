"""5.6.97 (lot gmail, cahier v3 C05-C06) : client OAuth Google commun, réutilisé par Gmail et par Google Agenda.

Le serveur Pilote porte UN client OAuth 2.0 de type « Application Web » ; chaque personne clique « Connecter », choisit son compte et consent
chez Google. Le client identifie AxiorHub, les jetons sont propres à chaque autorisation. Ce n'est ni une clé partagée ni un accès sans
consentement, et « Se connecter à AxiorHub » reste distinct.

- **Configuration du serveur, jamais le code** : section ``google5697`` (``client_id``, ``client_secret_file``, ``redirect_uri``), avec une
  section propre à un usage si le cabinet sépare ses projets Google (``google5697_gmail``, ``google5697_agenda`` : mêmes clés, prioritaires).
  Le secret est lu dans le coffre du serveur (``common.read_secret``) au moment de l'échange, du renouvellement ou de la révocation ; il
  n'est jamais renvoyé au navigateur. L'URI de retour vient de la configuration, jamais de l'en-tête Host ni d'un paramètre du navigateur ;
  elle se termine par ``/oauth/google/callback``.
- **Départ** : ``state`` aléatoire (gardé haché), vérificateur PKCE S256, lien à la personne, à la session, à l'usage, à la génération de
  la connexion et à l'empreinte de la configuration ; valable 10 minutes, consommé une seule fois (verrou SQLite), même en cas de refus.
- **Retour** : refus, mauvais état, rejeu, session différente ou expiration : aucune connexion active créée. Échange côté serveur avec le
  secret et le vérificateur ; identité lue chez Google (Gmail : ``users.getProfile``) ; portées réellement accordées retenues (consentement
  partiel : capacités disponibles seulement) ; jetons chiffrés séparément pour chaque connexion (un dossier du coffre et sa clé par
  connexion). Le code n'apparaît jamais dans l'URL finale.
- **Vie de la connexion** : ``access_type=offline`` ; renouvellement côté serveur, sérialisé par connexion (verrou local + bail en base),
  le jeton de renouvellement conservé si Google n'en renvoie pas ; génération revérifiée avant toute écriture (une déconnexion pendant
  un renouvellement l'emporte). ``invalid_grant`` : arrêt, plus aucun appel, état « À reconnecter ». « Déconnecter » arrête la file,
  révoque le jeton si possible et efface les jetons locaux.
- **Portées par capacité** (C06) : lecture Gmail (``gmail.readonly``), brouillons Gmail (``gmail.compose``, consentement additionnel à la
  demande), agenda. ``gmail.modify``, ``mail.google.com`` et ``gmail.send`` ne sont jamais demandées.
- **Transport** : ``TRANSPORT`` (tests : faux Google) ; sinon ``common.HTTP`` vers les seules origines Google. Aucun jeton, code ni
  secret dans une URL, un journal ou une erreur.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

from .common import Stop

AUTORISATION = 'https://accounts.google.com/o/oauth2/v2/auth'
JETON = 'https://oauth2.googleapis.com/token'
REVOCATION = 'https://oauth2.googleapis.com/revoke'
ORIGINES = ('https://oauth2.googleapis.com', 'https://gmail.googleapis.com', 'https://www.googleapis.com')
RETOUR = '/oauth/google/callback'
DUREE_ETAT = 600                     # secondes : 10 minutes
BAIL_RENOUVELLEMENT = 60             # secondes : un seul renouvellement à la fois par connexion, tous processus confondus
MARGE = 120                          # secondes retirées de la durée annoncée du jeton d'accès

# capacité -> portée Google ; une capacité absente de cette table n'est jamais demandée
PORTEES = {'gmail.lecture': 'https://www.googleapis.com/auth/gmail.readonly',
           'gmail.brouillons': 'https://www.googleapis.com/auth/gmail.compose',
           'agenda.lecture': 'https://www.googleapis.com/auth/calendar.readonly',
           'agenda.ecriture': 'https://www.googleapis.com/auth/calendar.events'}
# portées refusées pour cette livraison (cahier v3 C06 et décision du cabinet : brouillons, envoi sur clic, jamais de modification)
INTERDITES = frozenset({'https://www.googleapis.com/auth/gmail.modify', 'https://mail.google.com/', 'https://www.googleapis.com/auth/gmail.send',
                        'https://www.googleapis.com/auth/gmail.insert', 'https://www.googleapis.com/auth/gmail.settings.basic'})
LIBELLES = {'gmail.lecture': 'Lire les courriels et leurs pièces', 'gmail.brouillons': 'Enregistrer des brouillons dans Gmail (envoi seulement sur votre clic)',
            'agenda.lecture': 'Lire l’agenda', 'agenda.ecriture': 'Écrire dans l’agenda'}
ETATS = {'en_attente': 'Consentement en attente', 'active': 'Connectée', 'a_reconnecter': 'À reconnecter', 'deconnectee': 'Déconnectée'}


def _identite_gmail(appeler):
    out = appeler('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/profile')
    return {'adresse': str(out.get('emailAddress') or ''), 'history_id': str(out.get('historyId') or '')}


def _identite_agenda(appeler):
    out = appeler('GET', 'https://www.googleapis.com/calendar/v3/calendars/primary')
    return {'adresse': str(out.get('id') or '')}


# usage -> capacités possibles, capacités demandées à la première connexion, page de retour, lecture de l'identité
USAGES = {'gmail': {'capacites': ('gmail.lecture', 'gmail.brouillons'), 'base': ('gmail.lecture',),
                    'retour': '/parametres?rubrique=connexions#gmail5697', 'identite': _identite_gmail},
          'agenda': {'capacites': ('agenda.lecture', 'agenda.ecriture'), 'base': ('agenda.lecture',),
                     'retour': '/parametres?rubrique=connexions', 'identite': _identite_agenda}}

TRANSPORT = None          # tests : appelable (méthode, url, en-têtes, corps) -> (statut, en-têtes, octets)
HORLOGE = time.time       # tests : horloge déplaçable
_VERROUS, _VERROU = {}, threading.Lock()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS google5697_connexions(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL DEFAULT 'cabinet', owner TEXT NOT NULL, usage TEXT NOT NULL,
  adresse TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, portees TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 1,
  jeton_version INTEGER NOT NULL DEFAULT 0, expire REAL NOT NULL DEFAULT 0, bail REAL NOT NULL DEFAULT 0, config TEXT NOT NULL DEFAULT '',
  erreur TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, cree TEXT NOT NULL, maj TEXT NOT NULL, UNIQUE(cabinet_id, owner, usage));
CREATE TABLE IF NOT EXISTS google5697_etats(etat_hash TEXT PRIMARY KEY, connexion_id TEXT NOT NULL, owner TEXT NOT NULL, session_hash TEXT NOT NULL,
  usage TEXT NOT NULL, capacites TEXT NOT NULL, verificateur TEXT NOT NULL, redirect TEXT NOT NULL, config TEXT NOT NULL, generation INTEGER NOT NULL,
  retour TEXT NOT NULL DEFAULT '', expire REAL NOT NULL, utilise INTEGER NOT NULL DEFAULT 0, cree TEXT NOT NULL);
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _hache(valeur):
    return hashlib.sha256(str(valeur).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------------------------------------ configuration du serveur
def configuration(desk, usage):
    """Client OAuth de l'usage : section propre (``google5697_<usage>``) prioritaire, sinon section commune. Le secret n'est pas lu ici."""
    if usage not in USAGES:
        raise Stop('google_usage_inconnu')
    cfg = dict(desk.c.get('google5697') or {})
    cfg.update({k: v for k, v in (desk.c.get('google5697_' + usage) or {}).items() if v})
    client, fichier, retour = str(cfg.get('client_id') or '').strip(), str(cfg.get('client_secret_file') or '').strip(), str(cfg.get('redirect_uri') or '').strip()
    if not client or not fichier or not retour:
        raise Stop('google_non_configure')
    if not re.fullmatch(r'[A-Za-z0-9._-]{8,200}', client):
        raise Stop('google_identifiant_client_invalide')
    p = urlsplit(retour)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment or not p.path.endswith(RETOUR):
        raise Stop('google_uri_retour_invalide')
    return {'client_id': client, 'secret_file': fichier, 'redirect_uri': retour, 'empreinte': _hache('|'.join((usage, client, fichier, retour)))[:32]}


def etat_configuration(desk, usage):
    """Pour l'interface : configuré ou non, l'URI de retour à déclarer (jamais le secret, jamais l'identifiant complet)."""
    try:
        cfg = configuration(desk, usage)
    except Stop as error:
        return {'configure': False, 'code': str(error), 'uri_retour': str(((desk.c.get('google5697_' + usage) or {}).get('redirect_uri')
                                                                             or (desk.c.get('google5697') or {}).get('redirect_uri') or ''))}
    return {'configure': True, 'uri_retour': cfg['redirect_uri'], 'client': cfg['client_id'][:6] + '…'}


def _secret(cfg):
    from .common import read_secret
    return read_secret(cfg['secret_file'])


def session_de(env, auth=None):
    """Empreinte de la session du navigateur : session ouverte par la page de connexion, sinon l'en-tête d'authentification, sinon le
    jeton anti-falsification de la page. Jamais la valeur elle-même."""
    user = env.get('axiorhub.user') or {}
    if isinstance(user, dict) and user.get('session'):
        return _hache('session|' + str(user['session']))
    if env.get('HTTP_AUTHORIZATION'):
        return _hache('autorisation|' + str(env['HTTP_AUTHORIZATION']))
    return _hache('csrf|' + str((auth or {}).get('csrf') or ''))


# ------------------------------------------------------------------------------------------------ transport
def _transport(methode, url, entetes, corps):
    if not url.startswith(tuple(o + '/' for o in ORIGINES)):
        raise Stop('google_origine_refusee')
    if TRANSPORT is not None:
        return TRANSPORT(methode, url, dict(entetes), corps)
    from .common import HTTP
    p = urlsplit(url)
    client = HTTP('%s://%s' % (p.scheme, p.netloc), timeout=30)
    try:
        brut = client.request(methode, url, corps, entetes, 25_000_000)
        return 200, dict(client.last_headers or {}), brut
    except Stop as error:
        detail = getattr(error, 'detail', None) or {}
        statut = int(detail.get('remote_status') or 0)
        if not statut:
            raise                                   # délai dépassé, réseau coupé : l'appelant décide (résultat incertain pour un effet)
        oauth = (detail.get('provider_error') or {}).get('oauth', '')
        return statut, ({'Retry-After': str(detail['retry_after'])} if detail.get('retry_after') is not None else {}), json.dumps({'error': oauth}).encode()


class ErreurGoogle(Stop):
    """Erreur d'un appel Google : code public ``google_http_<statut>`` (ou ``google_reconnexion_requise``), statut et délai d'attente."""

    def __init__(self, code, statut=0, attente=None, oauth=''):
        super().__init__(code)
        self.statut, self.attente, self.oauth = int(statut or 0), attente, oauth


def _json(brut):
    try:
        out = json.loads(brut or b'{}')
    except (ValueError, UnicodeError):
        raise Stop('google_reponse_invalide') from None
    if not isinstance(out, dict):
        raise Stop('google_reponse_invalide')
    return out


def _attente(entetes):
    from .erreurs5634 import retry_after_seconds
    valeur = {k.lower(): v for k, v in (entetes or {}).items()}.get('retry-after')
    return retry_after_seconds(valeur)


def _formulaire(url, champs):
    statut, entetes, brut = _transport('POST', url, {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
                                       urlencode(champs).encode())
    if statut >= 400:
        try:
            oauth = str(_json(brut).get('error') or '')
        except Stop:
            oauth = ''
        raise ErreurGoogle('google_reconnexion_requise' if oauth == 'invalid_grant' else 'google_http_%d' % statut, statut, _attente(entetes), oauth)
    return _json(brut) if brut else {}


# ------------------------------------------------------------------------------------------------ connexions
def _vault(desk, conn_id, nom):
    """Un dossier du coffre (et sa clé de chiffrement) par connexion : les jetons d'une autorisation ne se déchiffrent pas avec ceux d'une autre."""
    return Path(desk.c['state_dir']) / 'vault' / 'google5697' / re.sub(r'[^A-Za-z0-9_-]', '', conn_id) / (nom + '.secret')


def _effacer_jetons(desk, conn_id):
    for nom in ('access', 'refresh'):
        chemin = _vault(desk, conn_id, nom)
        if chemin.is_file():
            chemin.unlink()


def _lire_jeton(desk, conn_id, nom):
    from .common import read_secret
    chemin = _vault(desk, conn_id, nom)
    return read_secret(str(chemin)) if chemin.is_file() else ''


def connexion(desk, owner, usage):
    ensure_schema(desk)
    row = desk.db.execute("SELECT * FROM google5697_connexions WHERE cabinet_id='cabinet' AND owner=? AND usage=?", (str(owner), usage)).fetchone()
    return dict(row) if row else None


def lire(desk, conn_id):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM google5697_connexions WHERE id=?', (str(conn_id or ''),)).fetchone()
    if not row:
        raise Stop('google_connexion_absente')
    return dict(row)


def capacites(row):
    """Capacités réellement accordées par Google (portées de la dernière autorisation), dans l'ordre de ``PORTEES``."""
    accordees = set(str((row or {}).get('portees') or '').split())
    return [c for c, p in PORTEES.items() if p in accordees]


def vue(row):
    """Vue sans secret d'une connexion."""
    if not row:
        return {'etat': 'absente', 'libelle': 'Non connectée', 'capacites': [], 'adresse': ''}
    return {'id': row['id'], 'usage': row['usage'], 'adresse': row['adresse'], 'etat': row['etat'], 'libelle': ETATS.get(row['etat'], row['etat']),
            'capacites': capacites(row) if row['etat'] == 'active' else [], 'generation': row['generation'], 'version': row['version'],
            'erreur': row['erreur'], 'maj': row['maj']}


def demarrer(desk, owner, usage, session, capacites_voulues=None):
    """Départ du consentement (POST authentifié et contrôlé par l'appelant) : rend l'URL Google. Capacités : celles de base de l'usage,
    ou une capacité additionnelle (consentement à la demande, ``include_granted_scopes``)."""
    from .adaptateurs5652 import pkce_pair
    ensure_schema(desk)
    if usage not in USAGES:
        raise Stop('google_usage_inconnu')
    cfg = configuration(desk, usage)
    voulues = sorted(set(capacites_voulues or USAGES[usage]['base']))
    if not voulues or any(c not in USAGES[usage]['capacites'] for c in voulues):
        raise Stop('google_capacite_inconnue')
    portees = sorted({PORTEES[c] for c in voulues})
    if set(portees) & INTERDITES:                                  # garde-fou : jamais une portée de modification ou d'envoi direct
        raise Stop('google_portee_interdite')
    if not session:
        raise Stop('google_session_requise')
    row = connexion(desk, owner, usage)
    maintenant = _iso()
    if row is None:
        ident = 'gg-' + uuid.uuid4().hex
        desk.db.execute("INSERT INTO google5697_connexions(id, owner, usage, etat, config, cree, maj) VALUES(?,?,?,?,?,?,?)",
                        (ident, str(owner), usage, 'en_attente', cfg['empreinte'], maintenant, maintenant))
        row = lire(desk, ident)
    elif row['etat'] == 'deconnectee':
        desk.db.execute("UPDATE google5697_connexions SET etat='en_attente', erreur='', version=version+1, maj=? WHERE id=?", (maintenant, row['id']))
        row = lire(desk, row['id'])
    verificateur, defi = pkce_pair()
    etat = secrets.token_urlsafe(32)
    t = HORLOGE()
    desk.db.execute('DELETE FROM google5697_etats WHERE expire<?', (t - DUREE_ETAT,))
    desk.db.execute('INSERT INTO google5697_etats(etat_hash, connexion_id, owner, session_hash, usage, capacites, verificateur, redirect, config, generation, retour, expire, cree) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (_hache(etat), row['id'], str(owner), str(session), usage, ' '.join(voulues), verificateur, cfg['redirect_uri'],
                                                          cfg['empreinte'], row['generation'], USAGES[usage]['retour'], t + DUREE_ETAT, maintenant))
    desk.db.commit()
    requete = {'client_id': cfg['client_id'], 'redirect_uri': cfg['redirect_uri'], 'response_type': 'code', 'scope': ' '.join(portees), 'state': etat,
               'code_challenge': defi, 'code_challenge_method': 'S256', 'access_type': 'offline', 'include_granted_scopes': 'true', 'prompt': 'consent'}
    if row.get('adresse'):
        requete['login_hint'] = row['adresse']                     # consentement additionnel : le même compte Google
    desk.audit('google5697_depart', {'usage': usage, 'capacites': voulues})
    return {'url': AUTORISATION + '?' + urlencode(requete), 'expire_secondes': DUREE_ETAT, 'capacites': voulues}


def _consommer(desk, etat):
    """Consommation atomique de l'état, à usage unique, avant tout autre contrôle : un rejeu trouve l'état déjà utilisé."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM google5697_etats WHERE etat_hash=?', (_hache(etat),)).fetchone()
        if row is None or row['utilise']:
            desk.db.rollback()
            return None
        desk.db.execute('UPDATE google5697_etats SET utilise=1 WHERE etat_hash=? AND utilise=0', (row['etat_hash'],))
        desk.db.commit()
        return dict(row)
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise


def terminer(desk, owner, session, args):
    """Retour de Google. Rend ``{'connexion', 'capacites', 'retour'}`` ; lève une erreur nommée sans rien activer sinon."""
    ensure_schema(desk)
    etat, code = str(args.get('state') or ''), str(args.get('code') or '')
    if not etat or len(etat) > 200 or len(code) > 3000:
        raise Stop('google_etat_invalide')
    demande = _consommer(desk, etat)
    if demande is None:
        raise Stop('google_etat_invalide')                          # inconnu ou rejoué
    if demande['owner'] != str(owner) or not hmac.compare_digest(demande['session_hash'], str(session or '')):
        raise Stop('google_etat_invalide')                          # autre personne ou autre session : rien n'est dit de plus
    if demande['expire'] < HORLOGE():
        raise Stop('google_etat_expire')
    if args.get('error'):
        desk.audit('google5697_refus', {'usage': demande['usage']})
        raise Stop('google_autorisation_refusee')
    if not code:
        raise Stop('google_etat_invalide')
    usage = demande['usage']
    cfg = configuration(desk, usage)
    if cfg['empreinte'] != demande['config'] or cfg['redirect_uri'] != demande['redirect']:
        raise Stop('google_configuration_modifiee')
    row = lire(desk, demande['connexion_id'])
    if row['generation'] != demande['generation']:
        raise Stop('google_connexion_modifiee')                     # déconnexion pendant le consentement : on ne réactive rien
    jeton = _formulaire(JETON, {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': cfg['redirect_uri'], 'client_id': cfg['client_id'],
                                'client_secret': _secret(cfg), 'code_verifier': demande['verificateur']})
    acces = str(jeton.get('access_token') or '')
    if not acces or str(jeton.get('token_type') or 'Bearer').lower() != 'bearer':
        raise Stop('google_autorisation_refusee')
    accordees = set(str(jeton.get('scope') or '').split())
    connues = {p for c, p in PORTEES.items() if c in USAGES[usage]['capacites']} & accordees
    if not connues:
        raise Stop('google_aucune_capacite_accordee')               # consentement sans aucune des portées de l'usage
    identite = USAGES[usage]['identite'](lambda m, u: _appel_brut(m, u, acces))
    adresse = str(identite.get('adresse') or '').strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+', adresse):
        raise Stop('google_identite_illisible')
    if row['adresse'] and row['adresse'] != adresse:
        raise Stop('google_compte_different')                       # un autre compte Google : déconnecter d'abord, jamais de mélange
    renouvellement = str(jeton.get('refresh_token') or '')
    if not renouvellement and not _vault(desk, row['id'], 'refresh').is_file():
        raise Stop('google_jeton_renouvellement_absent')
    portees = ' '.join(sorted(connues))       # avec include_granted_scopes, Google rend toutes les portées encore accordées : rien d'autre n'est supposé
    from .vault567 import write
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        cur = desk.db.execute("UPDATE google5697_connexions SET adresse=?, etat='active', portees=?, jeton_version=jeton_version+1, expire=?, bail=0, config=?, "
                              "erreur='', version=version+1, maj=? WHERE id=? AND generation=?",
                              (adresse, portees, HORLOGE() + max(60, int(jeton.get('expires_in') or 3600)) - MARGE, cfg['empreinte'], _iso(), row['id'], demande['generation']))
        if not cur.rowcount:
            raise Stop('google_connexion_modifiee')
        write(_vault(desk, row['id'], 'access'), acces)
        if renouvellement:
            write(_vault(desk, row['id'], 'refresh'), renouvellement)
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    nouvelle = lire(desk, row['id'])
    desk.audit('google5697_connectee', {'usage': usage, 'capacites': capacites(nouvelle)})
    return {'connexion': vue(nouvelle), 'capacites': capacites(nouvelle), 'retour': demande['retour'], 'identite': {k: v for k, v in identite.items() if k != 'adresse'}}


def _verrou(conn_id):
    with _VERROU:
        return _VERROUS.setdefault(conn_id, threading.Lock())


def _marquer_a_reconnecter(desk, row):
    desk.db.execute("UPDATE google5697_connexions SET etat='a_reconnecter', erreur='invalid_grant', expire=0, bail=0, version=version+1, maj=? "
                    "WHERE id=? AND generation=? AND etat='active'", (_iso(), row['id'], row['generation']))
    desk.db.commit()
    chemin = _vault(desk, row['id'], 'access')
    if chemin.is_file():
        chemin.unlink()
    desk.audit('google5697_a_reconnecter', {'usage': row['usage']})


def jeton(desk, conn_id, forcer=False):
    """Jeton d'accès valide de la connexion ; renouvelé côté serveur s'il expire, un seul renouvellement à la fois par connexion."""
    row = lire(desk, conn_id)
    if row['etat'] == 'a_reconnecter':
        raise Stop('google_reconnexion_requise')                    # aucune relance après invalid_grant
    if row['etat'] != 'active':
        raise Stop('google_non_connecte')
    if not forcer and row['expire'] > HORLOGE() + 30:
        acces = _lire_jeton(desk, row['id'], 'access')
        if acces:
            return acces
    with _verrou(row['id']):
        frais = lire(desk, conn_id)
        if frais['etat'] != 'active' or frais['generation'] != row['generation']:
            raise Stop('google_reconnexion_requise' if frais['etat'] == 'a_reconnecter' else 'google_non_connecte')
        if frais['jeton_version'] != row['jeton_version'] and frais['expire'] > HORLOGE() + 30:
            return _lire_jeton(desk, row['id'], 'access')           # renouvelé entre-temps par un autre fil
        return _renouveler(desk, frais)


def _renouveler(desk, row):
    t = HORLOGE()
    cur = desk.db.execute('UPDATE google5697_connexions SET bail=? WHERE id=? AND generation=? AND bail<?', (t + BAIL_RENOUVELLEMENT, row['id'], row['generation'], t))
    desk.db.commit()
    if not cur.rowcount:                                            # un autre processus renouvelle : on attend son résultat, sans second appel
        for _ in range(20):
            time.sleep(0.25)
            frais = lire(desk, row['id'])
            if frais['jeton_version'] != row['jeton_version'] and frais['etat'] == 'active':
                return _lire_jeton(desk, row['id'], 'access')
            if frais['etat'] != 'active':
                break
        raise Stop('google_renouvellement_en_cours')
    try:
        renouvellement = _lire_jeton(desk, row['id'], 'refresh')
        if not renouvellement:
            _marquer_a_reconnecter(desk, row)
            raise Stop('google_reconnexion_requise')
        cfg = configuration(desk, row['usage'])
        try:
            neuf = _formulaire(JETON, {'grant_type': 'refresh_token', 'refresh_token': renouvellement, 'client_id': cfg['client_id'], 'client_secret': _secret(cfg)})
        except ErreurGoogle as error:
            if error.oauth == 'invalid_grant':
                _marquer_a_reconnecter(desk, row)
            raise
        acces = str(neuf.get('access_token') or '')
        if not acces:
            raise Stop('google_reponse_invalide')
        from .vault567 import write
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            cur = desk.db.execute("UPDATE google5697_connexions SET jeton_version=jeton_version+1, expire=?, bail=0, maj=? WHERE id=? AND generation=? AND etat='active'",
                                  (HORLOGE() + max(60, int(neuf.get('expires_in') or 3600)) - MARGE, _iso(), row['id'], row['generation']))
            if not cur.rowcount:
                raise Stop('google_non_connecte')                  # déconnexion pendant le renouvellement : le nouveau jeton est jeté
            write(_vault(desk, row['id'], 'access'), acces)
            if neuf.get('refresh_token'):                           # sinon le jeton de renouvellement existant est conservé
                write(_vault(desk, row['id'], 'refresh'), str(neuf['refresh_token']))
            desk.db.commit()
        except BaseException:
            if desk.db.in_transaction:
                desk.db.rollback()
            raise
        return acces
    finally:
        desk.db.execute('UPDATE google5697_connexions SET bail=0 WHERE id=? AND bail>0', (row['id'],))
        desk.db.commit()


def _appel_brut(methode, url, acces, corps=None, entetes=None):
    head = {'Authorization': 'Bearer ' + acces, 'Accept': 'application/json', **(entetes or {})}
    data = None
    if corps is not None:
        data = json.dumps(corps).encode('utf-8')
        head['Content-Type'] = 'application/json'
    statut, rep, brut = _transport(methode, url, head, data)
    if statut >= 400:
        raise ErreurGoogle('google_http_%d' % statut, statut, _attente(rep))
    return _json(brut) if brut else {}


def appeler(desk, conn_id, methode, url, corps=None, capacite='', entetes=None):
    """Appel d'API pour une connexion active : capacité accordée vérifiée avant l'appel ; un 401 déclenche un seul renouvellement."""
    row = lire(desk, conn_id)
    if capacite and capacite not in capacites(row):
        raise Stop('google_capacite_non_accordee')
    acces = jeton(desk, conn_id)
    try:
        return _appel_brut(methode, url, acces, corps, entetes)
    except ErreurGoogle as error:
        if error.statut != 401:
            raise
    return _appel_brut(methode, url, jeton(desk, conn_id, forcer=True), corps, entetes)


def deconnecter(desk, owner, conn_id):
    """Arrête la file (nouvelle génération, état « déconnectée »), révoque le jeton chez Google si possible, efface les jetons locaux.
    Un échec de révocation est dit et n'empêche jamais l'effacement local."""
    row = lire(desk, conn_id)
    if row['owner'] != str(owner):
        raise Stop('google_connexion_absente')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute("UPDATE google5697_connexions SET etat='deconnectee', portees='', expire=0, bail=0, generation=generation+1, erreur='', version=version+1, maj=? "
                        "WHERE id=?", (_iso(), row['id']))
        desk.db.execute('DELETE FROM google5697_etats WHERE connexion_id=?', (row['id'],))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    a_revoquer = ''
    try:
        a_revoquer = _lire_jeton(desk, row['id'], 'refresh') or _lire_jeton(desk, row['id'], 'access')
    except Stop:
        a_revoquer = ''
    revocation = 'aucun_jeton'
    if a_revoquer:
        try:
            _formulaire(REVOCATION, {'token': a_revoquer})
            revocation = 'revoquee'
        except Stop:
            revocation = 'echec'
    _effacer_jetons(desk, row['id'])
    desk.audit('google5697_deconnectee', {'usage': row['usage'], 'revocation': revocation})
    return {'connexion': vue(lire(desk, row['id'])), 'revocation': revocation}
