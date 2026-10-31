"""5.6.83 : vérification d'un lien cité, par le service réseau d'AxiorHub (remplace le script ``verify_links`` des paquets importés).

Un lien venant d'un document n'est pas digne de confiance : hôte contrôlé avant chaque requête, redirections relues une à une, adresses internes
refusées, volume et délai bornés. Une réponse 200 ne valide rien : la référence n'est déclarée concordante que si le texte lu contient les termes
de l'affirmation, et cette concordance reste à confirmer par l'avocat.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
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


def _fetch(url):
    """(statut, en-têtes, octets) sans suivre de redirection ni passer par un mandataire."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'AxiorHub-Pilote', 'Accept': 'text/html,text/plain'})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as ex:
        return ex.code, dict(ex.headers or {}), b''
    except (urllib.error.URLError, OSError, TimeoutError):
        raise Stop('lien_injoignable') from None


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


def verifier(url, claim='', fetch=None, resolve=True):
    """État d'une référence : jamais « vérifiée » sur le seul code 200, et aucun accès à une adresse interne, même après une redirection."""
    current = str(url or '').strip()
    fetch = fetch or _fetch
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
