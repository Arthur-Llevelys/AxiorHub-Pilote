"""5.6.83 : vérification d'un lien cité, par le service réseau d'AxiorHub (remplace le script ``verify_links`` des paquets importés).

Un lien venant d'un document n'est pas digne de confiance : hôte contrôlé avant chaque requête, redirections relues une à une, adresses internes
refusées, volume et délai bornés. Une réponse 200 ne valide rien : la référence n'est déclarée concordante que si le texte lu contient les termes
de l'affirmation, et cette concordance reste à confirmer par l'avocat.

5.6.111 (lot C, C04) : la connexion se fait vers l'adresse VÉRIFIÉE. Le nom est résolu une seule fois par requête ; toutes les adresses rendues
(IPv4 et IPv6) doivent être publiques ; le socket est ouvert vers l'une d'elles, sans seconde résolution par le transport, puis le TLS est
négocié avec le nom d'origine (SNI) et le certificat vérifié pour ce nom (contexte par défaut, jamais désactivé). Aucun mandataire (proxy) n'est
utilisé ; chaque redirection repasse par le même contrôle ; taille et durée bornées, y compris pour une erreur ou une redirection.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import Stop, fold
from .extensions364 import validate_endpoint

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
TIMEOUT = 15
STOPWORDS = frozenset('avec dans pour plus cette sont leur leurs elle elles dont sans sous entre apres avant comme ainsi etre avoir fait'.split())
STATES = ('reference_trouvee', 'texte_lu', 'concordance_a_confirmer', 'non_verifie')
SCHEMA = '''CREATE TABLE IF NOT EXISTS legal_proofs5683(id INTEGER PRIMARY KEY, at TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', query TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '', identifier TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '', text_sha256 TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '', claim TEXT NOT NULL DEFAULT '', state TEXT NOT NULL)'''


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _publique(adresse):
    """Adresse joignable sur Internet seulement : ni bouclage, ni réseau privé, ni lien local, ni multidiffusion, ni réservée ; une adresse IPv6
    qui transporte une IPv4 (``::ffff:a.b.c.d``) est jugée sur l'IPv4 transportée."""
    try:
        ip = ipaddress.ip_address(str(adresse).split('%', 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _resoudre(hote):
    """Résolution système, une seule fois par requête : adresses distinctes dans l'ordre rendu (IPv4 et IPv6)."""
    try:
        infos = socket.getaddrinfo(hote, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise Stop('lien_injoignable') from None
    return list(dict.fromkeys(str(i[4][0]) for i in infos))


def adresses_verifiees(hote, resoudre=None):
    """Adresses autorisées pour ``hote`` : un littéral IP est jugé tel quel ; un nom est résolu UNE fois et refusé si une seule des adresses
    rendues n'est pas publique (un nom qui mêle adresses publiques et internes n'est pas « un peu » sûr)."""
    try:
        ipaddress.ip_address(hote)
        adresses = [hote]
    except ValueError:
        adresses = list((resoudre or _resoudre)(hote) or ())
    if not adresses:
        raise Stop('lien_injoignable')
    if not all(_publique(a) for a in adresses):
        raise Stop('lien_refuse')
    return adresses


class ConnexionEpinglee(http.client.HTTPSConnection):
    """Connexion HTTPS ouverte vers une adresse DÉJÀ vérifiée : aucun appel DNS ici. Le TLS est négocié avec le nom d'origine (SNI) et le
    certificat est vérifié pour ce nom par le contexte fourni (par défaut : ``ssl.create_default_context()``, vérification obligatoire)."""

    def __init__(self, hote, adresse, port=443, timeout=TIMEOUT, context=None):
        context = context or ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise Stop('lien_tls_non_verifie')   # garde-fou : aucun contexte qui désactive la vérification n'est accepté
        super().__init__(hote, port, timeout=timeout, context=context)
        self._adresse = adresse

    def connect(self):
        brut = socket.create_connection((self._adresse, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(brut, server_hostname=self.host)
        except BaseException:
            brut.close()
            raise


def _fetch(url, resoudre=None, contexte=None, connexion=ConnexionEpinglee):
    """(statut, en-têtes, octets) sans suivre de redirection ni passer par un mandataire, vers une adresse vérifiée (voir l'en-tête)."""
    from . import common
    parts = urllib.parse.urlsplit(url)
    hote = parts.hostname or ''
    adresses = adresses_verifiees(hote, resoudre)
    if common.EGRESS_GUARD is not None:   # politique des sorties du cabinet (local strict), comme les autres lectures de pages
        common.EGRESS_GUARD(_Garde(), 'GET', url, None)
    common.release_before_network(hote)   # aucune transaction SQLite ouverte pendant l'appel
    chemin = (parts.path or '/') + ('?' + parts.query if parts.query else '')
    debut, derniere = time.monotonic(), None
    for adresse in adresses:   # chaque adresse essayée a été vérifiée ; la suivante seulement si la connexion échoue
        conn = connexion(hote, adresse, parts.port or 443, timeout=TIMEOUT, context=contexte)
        try:
            try:
                conn.connect()
            except (OSError, ssl.SSLError) as ex:
                derniere = ex
                if isinstance(ex, ssl.SSLCertVerificationError):
                    raise Stop('lien_certificat_refuse') from None   # certificat invalide pour le nom : jamais d'autre adresse ni de repli
                continue
            conn.request('GET', chemin, headers={'User-Agent': 'AxiorHub-Pilote', 'Accept': 'text/html,text/plain',
                                                 'Accept-Encoding': 'identity'})
            rep = conn.getresponse()
            entetes = dict(rep.getheaders())
            if rep.status >= 300:
                return rep.status, entetes, b''   # redirection ou erreur : corps jamais lu (relu par l'appelant si redirection)
            morceaux, total = [], 0
            while total <= MAX_BYTES:
                if time.monotonic() - debut > TIMEOUT:
                    raise Stop('lien_injoignable')   # durée totale bornée, même pour un serveur qui répond goutte à goutte
                bloc = rep.read(min(65536, MAX_BYTES + 1 - total))
                if not bloc:
                    break
                morceaux.append(bloc)
                total += len(bloc)
            return rep.status, entetes, b''.join(morceaux)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise Stop('lien_injoignable') from None
        finally:
            conn.close()
    raise Stop('lien_injoignable') from derniere


class _Garde:
    local_only = False


def _check(url, resolve):
    parts = urllib.parse.urlsplit(str(url))
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc, '', '', ''))
    try:
        validate_endpoint(clean, resolve=resolve)
    except Stop:
        raise Stop('lien_refuse') from None
    if len(str(url)) > 1500:
        raise Stop('lien_refuse')


def key_terms(claim):
    return {w for w in re.findall(r'[a-z0-9]{4,}', fold(str(claim))) if w not in STOPWORDS}


def concordance(claim, text, ratio=0.7):
    terms = key_terms(claim)
    if not terms:
        return False
    hay = set(re.findall(r'[a-z0-9]{4,}', fold(str(text))))
    return len(terms & hay) / len(terms) >= ratio


def verifier(url, claim='', fetch=None, resolve=True, resoudre=None, contexte=None):
    """État d'une référence : jamais « vérifiée » sur le seul code 200, et aucun accès à une adresse interne, même après une redirection.
    5.6.111 (C04) : sans ``fetch`` fourni, le transport par défaut résout lui-même une seule fois et se connecte à l'adresse vérifiée
    (``resoudre`` et ``contexte`` : résolveur et contexte TLS remplaçables, pour les essais) ; un ``fetch`` fourni garde l'ancien contrôle."""
    current = str(url or '').strip()
    if fetch is None:
        def fetch(u):
            return _fetch(u, resoudre=resoudre, contexte=contexte)
        resolve = False   # la résolution est faite par le transport, une seule fois, sur l'adresse effectivement contactée
    for _ in range(MAX_REDIRECTS + 1):
        _check(current, resolve)
        status, headers, body = fetch(current)
        if status in (301, 302, 303, 307, 308):
            target = {k.lower(): v for k, v in headers.items()}.get('location', '')
            current = urllib.parse.urljoin(current, target)
            continue
        break
    else:
        raise Stop('lien_trop_de_redirections')
    if status != 200 or not body:
        return {'url': current, 'status': status, 'state': 'non_verifie', 'motif': 'page_non_lue'}
    if len(body) > MAX_BYTES:
        return {'url': current, 'status': status, 'state': 'non_verifie', 'motif': 'page_trop_volumineuse'}
    text = re.sub(r'<(script|style)\b.*?</\1>', ' ', body.decode('utf-8', 'replace'), flags=re.S | re.I)
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()
    state = 'texte_lu' if text else 'non_verifie'
    if text and claim:
        state = 'concordance_a_confirmer' if concordance(claim, text) else 'non_verifie'
    return {'url': current, 'status': status, 'state': state, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'motif': '' if state != 'non_verifie' else 'affirmation_non_retrouvee'}


def record(desk, matter='', query='', tool='', identifier='', url='', text='', excerpt='', version='', claim='', state='reference_trouvee'):
    """Preuve d'une référence : ce qui a été cherché, lu et rapproché. Trouvée, lue et concordante restent trois états distincts."""
    if state not in STATES:
        raise Stop('etat_preuve_invalide')
    desk.db.execute(SCHEMA)
    cur = desk.db.execute('INSERT INTO legal_proofs5683(at,matter,query,tool,identifier,url,excerpt,text_sha256,version,claim,state) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (datetime.now(timezone.utc).isoformat(), str(matter or ''), str(query)[:500], str(tool)[:120], str(identifier)[:300], str(url)[:1500], str(excerpt)[:1500],
                           hashlib.sha256(str(text).encode()).hexdigest() if text else '', str(version)[:60], str(claim)[:1000], state))
    desk.db.commit()
    return cur.lastrowid


def proofs(desk, matter, limit=50):
    desk.db.execute(SCHEMA)
    return [dict(r) for r in desk.db.execute('SELECT * FROM legal_proofs5683 WHERE matter=? ORDER BY id DESC LIMIT ?', (str(matter), max(1, min(int(limit), 500))))]


def dumps(value):
    return json.dumps(value, ensure_ascii=False)
