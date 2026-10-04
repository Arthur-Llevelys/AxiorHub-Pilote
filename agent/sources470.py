"""Connecteur de sources juridiques (AxiorHub 4.7.0).

Principes
- Seules des **références structurées** (code, numéro d'article, juridiction, chambre, date, numéro de
  pourvoi ou de RG) quittent le cabinet. Jamais un extrait du dossier, un nom de partie ou un texte rédigé.
  ``outbound`` valide chaque champ par motif strict avant tout appel ; ``audit`` journalise la requête.
- Les sources sont désactivées par défaut : tant que l'avocat ne les a pas activées, toute référence reste
  « non vérifiable » (jamais « vérifiée »).
- Une panne n'est jamais confondue avec une absence : un service injoignable donne « non vérifiable »,
  un service qui répond sans trouver donne « introuvable dans cette source ».
- Cache SQLite avec durée de vie, réponse périmée utilisable en cas de panne (signalée), coupe-circuit
  après des échecs répétés.
"""
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .common import HTTP, Stop, digest, fold, read_secret
from . import refs470

OFFICIAL = {'piste_legifrance': True, 'piste_judilibre': True, 'local': False}
TTL = {'found': 24 * 3600, 'missing': 6 * 3600}
BREAKER_FAILURES = 3
BREAKER_SECONDS = 300
MAX_REFS_PER_CHECK = 40

SCHEMA = ('''
CREATE TABLE IF NOT EXISTS ref_cache470(
  key TEXT NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL, fetched REAL NOT NULL,
  PRIMARY KEY(key,provider));
CREATE TABLE IF NOT EXISTS citation_checks470(
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT NOT NULL, matter TEXT NOT NULL,
  created TEXT NOT NULL, summary TEXT NOT NULL, report TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS citation_checks470_scope ON citation_checks470(scope,scope_id,created DESC);
''',)

ALLOWED_FIELDS = {
    'code': r"[A-Za-zÀ-ÿ' \-]{3,80}", 'number': r'[LRD]?\d{1,4}(?:-\d{1,4}){0,4}(?:-[a-z]+)?',
    'court': r'cass|ca|ce|cc|tcom|tj|cjue|cedh', 'chamber': r'civ1|civ2|civ3|com|soc|crim|mixte|plen|',
    'date': r'\d{4}-\d{2}-\d{2}|', 'pourvoi': r'\d{2}-\d{2}\.\d{3}|', 'rg': r'\d{2}/\d{3,6}|',
    'ce_number': r'\d{5,7}|', 'cc_number': r'\d{4}-\d{2,4} [A-Z]{1,4}|', 'at_date': r'\d{4}-\d{2}-\d{2}|',
    'kind': r'article|decision',
}


def ensure_schema(desk):
    for sql in SCHEMA:
        desk.db.executescript(sql)


# ---------------------------------------------------------------- confidentialité

def outbound(ref, at_date=''):
    """Seul objet autorisé à quitter le cabinet pour une référence. Lève Stop si un champ est suspect."""
    payload = {'kind': ref['kind']}
    names = ('code', 'number') if ref['kind'] == 'article' else ('court', 'chamber', 'date', 'pourvoi', 'rg', 'ce_number', 'cc_number')
    for name in names:
        value = str(ref.get(name, '') or '')
        if value and not re.fullmatch(ALLOWED_FIELDS[name], value):
            raise Stop('champ_reference_refuse')
        payload[name] = value
    if ref['kind'] == 'article':
        payload['at_date'] = str(at_date or '')
        if payload['at_date'] and not re.fullmatch(ALLOWED_FIELDS['at_date'], payload['at_date']):
            raise Stop('champ_reference_refuse')
    return payload


def cache_key(payload):
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))[:40]


# ---------------------------------------------------------------- fournisseurs

class Provider:
    name = ''
    official = False

    def article(self, payload):  # -> {'found':bool,'versions':[...],'source_date':'', ...}
        raise Stop('fournisseur_non_pris_en_charge')

    def decision(self, payload):  # -> {'found':bool,'matches':[...]}
        raise Stop('fournisseur_non_pris_en_charge')

    def supports(self, kind, payload):
        return True


def _norm_text(value):
    return re.sub(r'\s+', ' ', fold(str(value)).replace('’', "'")).strip()


class LocalProvider(Provider):
    """Jeu de références propre au cabinet (JSON). Sert aussi de base de test ; non officiel."""
    name = 'local'
    official = False

    def __init__(self, path=None, data=None):
        self.path = Path(path) if path else None
        self._data = data

    def data(self):
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding='utf-8')) if self.path and self.path.is_file() else {}
            except (OSError, ValueError):
                raise Stop('base_locale_illisible') from None
        return self._data

    def article(self, payload):
        base = self.data()
        for entry in base.get('articles', []):
            if entry['code'] == payload['code'] and entry['number'] == payload['number']:
                return {'found': True, 'versions': entry['versions'], 'source_date': base.get('as_of', '')}
        return {'found': False, 'versions': [], 'source_date': base.get('as_of', '')}

    def decision(self, payload):
        base = self.data()
        matches = []
        for entry in base.get('decisions', []):
            numbers = {entry.get(k) for k in ('pourvoi', 'rg', 'ce_number', 'cc_number')} - {None, ''}
            wanted = {payload.get(k) for k in ('pourvoi', 'rg', 'ce_number', 'cc_number')} - {None, ''}
            if numbers & wanted:
                matches.append(entry)
        return {'found': bool(matches), 'matches': matches, 'source_date': base.get('as_of', '')}


class PisteBase(Provider):
    official = True
    token_url = 'https://oauth.piste.gouv.fr/api/oauth/token'
    api_base = 'https://api.piste.gouv.fr'

    def __init__(self, client_id, secret, http=None, timeout=15):
        self.client_id, self.secret, self.timeout = client_id, secret, timeout
        self._http = http
        self._token = ('', 0.0)

    def http(self):
        return self._http or HTTP(self.api_base, timeout=self.timeout)

    def bearer(self):
        if self._token[1] > time.time() + 30:
            return self._token[0]
        body = ('grant_type=client_credentials&scope=openid&client_id=%s&client_secret=%s' % (
            self.client_id, self.secret)).encode()
        oauth = self._http or HTTP('https://oauth.piste.gouv.fr', timeout=self.timeout)
        raw = oauth.request('POST', self.token_url, body, {'Content-Type': 'application/x-www-form-urlencoded'}, 200_000)
        try:
            data = json.loads(raw)
            token = data['access_token']
        except (ValueError, KeyError, TypeError):
            raise Stop('jeton_piste_invalide') from None
        self._token = (token, time.time() + int(data.get('expires_in', 3000)))
        return token

    def call(self, method, path, body=None):
        http = self.http()
        data = None if body is None else json.dumps(body).encode()
        headers = {'Authorization': 'Bearer ' + self.bearer(), 'Content-Type': 'application/json', 'Accept': 'application/json'}
        raw = http.request(method, self.api_base + path, data, headers, 2_000_000)
        try:
            return json.loads(raw)
        except ValueError:
            raise Stop('json_http_invalide') from None


def _ms_to_date(value):
    try:
        ts = int(value) / 1000.0
        if ts > 32_000_000_000:  # « 2999-01-01 » : version sans fin
            return ''
        return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ''


class PisteLegifrance(PisteBase):
    """API Légifrance (PISTE). Le contrat de réponse est lu de façon tolérante ; non testé sur le service réel."""
    name = 'piste_legifrance'
    prefix = '/dila/legifrance/lf-engine-app'

    def supports(self, kind, payload):
        return kind == 'article'

    def article(self, payload):
        search = {'fond': 'CODE_DATE', 'recherche': {
            'champs': [{'typeChamp': 'NUM_ARTICLE', 'operateur': 'ET',
                        'criteres': [{'typeRecherche': 'EXACTE', 'valeur': payload['number'], 'operateur': 'ET'}]}],
            'filtres': [{'facette': 'NOM_CODE', 'valeurs': [payload['code']]}],
            'pageNumber': 1, 'pageSize': 10, 'operateur': 'ET', 'sort': 'PERTINENCE', 'typePagination': 'ARTICLE'}}
        found = self.call('POST', self.prefix + '/search', search)
        ids = []
        for result in (found.get('results') or []):
            for section in (result.get('sections') or []):
                for extract in (section.get('extracts') or []):
                    if extract.get('id') and fold(str(extract.get('num', '')).replace(' ', '')) == fold(payload['number']):
                        ids.append(extract['id'])
        if not ids:
            return {'found': False, 'versions': [], 'source_date': date.today().isoformat()}
        article = self.call('POST', self.prefix + '/consult/getArticle', {'id': ids[0]}).get('article') or {}
        versions = []
        for item in (article.get('articleVersions') or [article]):
            versions.append({
                'id': item.get('id', ''), 'start': _ms_to_date(item.get('dateDebut')), 'end': _ms_to_date(item.get('dateFin')),
                'state': str(item.get('etat', '')).upper(), 'text': re.sub(r'<[^>]+>', ' ', str(item.get('texte') or item.get('texteHtml') or '')),
                'url': 'https://www.legifrance.gouv.fr/codes/article_lc/' + str(item.get('id', ''))})
        if not any(v['text'] for v in versions) and article.get('texte'):
            versions[0]['text'] = re.sub(r'<[^>]+>', ' ', str(article['texte']))
        return {'found': True, 'versions': versions, 'source_date': date.today().isoformat()}


class PisteJudilibre(PisteBase):
    """API Judilibre (Cour de cassation, PISTE). Les résultats sont recoupés champ par champ : un résultat de
    recherche n'est retenu que si son numéro correspond exactement à celui cité."""
    name = 'piste_judilibre'
    prefix = '/cassation/judilibre/v1.0'

    def supports(self, kind, payload):
        return kind == 'decision' and payload.get('court') in ('cass', 'ca', 'tj', 'tcom') and bool(payload.get('pourvoi') or payload.get('rg'))

    def decision(self, payload):
        number = payload.get('pourvoi') or payload.get('rg')
        from urllib.parse import quote
        found = self.call('GET', self.prefix + '/search?query=' + quote('"%s"' % number) + '&page_size=10')
        matches = []
        for item in (found.get('results') or []):
            numbers = {str(item.get('number', ''))} | {str(n) for n in (item.get('numbers') or [])}
            if number in {n.replace(' ', '') for n in numbers}:
                matches.append({
                    'id': item.get('id', ''), 'pourvoi': number if payload.get('pourvoi') else '',
                    'rg': number if payload.get('rg') else '', 'date': str(item.get('decision_date', ''))[:10],
                    'court': {'cc': 'cass', 'ca': 'ca', 'tj': 'tj', 'tcom': 'tcom'}.get(str(item.get('jurisdiction', '')).lower(), 'cass'),
                    'chamber': _chamber_from_judilibre(str(item.get('chamber', ''))),
                    'title': str(item.get('summary', ''))[:200],
                    'url': 'https://www.courdecassation.fr/decision/' + str(item.get('id', ''))})
        return {'found': bool(matches), 'matches': matches, 'source_date': date.today().isoformat()}


def _chamber_from_judilibre(value):
    return {'civ1': 'civ1', 'civ2': 'civ2', 'civ3': 'civ3', 'comm': 'com', 'soc': 'soc', 'cr': 'crim',
            'mi': 'mixte', 'pl': 'plen'}.get(fold(value).replace(' ', '')[:5].rstrip('.'), '') or {
        'premiere chambre civile': 'civ1', 'deuxieme chambre civile': 'civ2', 'troisieme chambre civile': 'civ3',
        'chambre commerciale': 'com', 'chambre sociale': 'soc', 'chambre criminelle': 'crim'}.get(fold(value), '')


# ---------------------------------------------------------------- connecteur

class Sources:
    """Orchestre cache, coupe-circuit, journal et fournisseurs. ``providers`` : liste d'objets Provider."""

    def __init__(self, desk, providers=None, enabled=None, clock=time.time):
        self.desk = desk
        ensure_schema(desk)
        self.clock = clock
        cfg = desk.c.get('legal_sources', {}) or {}
        self.enabled = bool(desk.settings('automation:sources470_enabled', cfg.get('enabled', False))) if enabled is None else bool(enabled)
        self.providers = providers if providers is not None else build_providers(desk)
        self.stats = {'requests': 0, 'cache_hits': 0, 'failures': 0, 'stale': 0}

    # --- état du coupe-circuit (persistant : partagé entre processus)
    def _breaker(self, name):
        return self.desk.settings('sources470:breaker:' + name, {'failures': 0, 'open_until': 0})

    def _record(self, name, ok):
        state = self._breaker(name)
        if ok:
            state = {'failures': 0, 'open_until': 0}
        else:
            state['failures'] = int(state.get('failures', 0)) + 1
            if state['failures'] >= BREAKER_FAILURES:
                state['open_until'] = self.clock() + BREAKER_SECONDS
        self.desk.setting('sources470:breaker:' + name, state)

    def available(self, provider):
        return self._breaker(provider.name).get('open_until', 0) <= self.clock()

    def status(self):
        rows = []
        for provider in self.providers:
            state = self._breaker(provider.name)
            rows.append({'name': provider.name, 'official': provider.official,
                         'state': 'ouvert' if not self.available(provider) else 'disponible',
                         'failures': state.get('failures', 0)})
        count = self.desk.db.execute('SELECT COUNT(*) FROM ref_cache470').fetchone()[0]
        return {'enabled': self.enabled, 'providers': rows, 'cache_entries': count}

    # --- cache
    def _cached(self, key, provider, allow_stale=False):
        row = self.desk.db.execute('SELECT payload,fetched FROM ref_cache470 WHERE key=? AND provider=?', (key, provider)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        ttl = TTL['found'] if payload.get('found') else TTL['missing']
        fresh = self.clock() - row[1] <= ttl
        if fresh or allow_stale:
            return {**payload, 'cached': True, 'stale': not fresh, 'fetched_at': row[1]}
        return None

    def _store(self, key, provider, payload):
        self.desk.db.execute('INSERT OR REPLACE INTO ref_cache470 VALUES(?,?,?,?)',
                             (key, provider, json.dumps(payload, ensure_ascii=False), self.clock()))
        self.desk.db.commit()

    # --- résolution
    def lookup(self, ref, at_date=''):
        """Retourne la liste des réponses fournisseur : [{'provider','official','ok','result'|'error', ...}]."""
        payload = outbound(ref, at_date)
        base = {k: v for k, v in payload.items() if k != 'at_date'}
        key = cache_key(base)
        answers = []
        if not self.enabled:
            return [{'provider': '', 'official': False, 'ok': False, 'error': 'sources_desactivees'}]
        usable = [p for p in self.providers if p.supports(ref['kind'], payload)]
        if not usable:
            return [{'provider': '', 'official': False, 'ok': False, 'error': 'aucune_source_pour_cette_reference'}]
        for provider in usable:
            hit = self._cached(key, provider.name)
            if hit:
                self.stats['cache_hits'] += 1
                answers.append({'provider': provider.name, 'official': provider.official, 'ok': True, 'result': hit})
                continue
            if not self.available(provider):
                stale = self._cached(key, provider.name, allow_stale=True)
                if stale:
                    self.stats['stale'] += 1
                    answers.append({'provider': provider.name, 'official': provider.official, 'ok': True, 'result': stale})
                else:
                    answers.append({'provider': provider.name, 'official': provider.official, 'ok': False,
                                    'error': 'service_en_pause_apres_echecs'})
                continue
            self.desk.audit('source_470_requete', {'provider': provider.name, 'reference': base})
            self.stats['requests'] += 1
            try:
                result = provider.article(payload) if ref['kind'] == 'article' else provider.decision(payload)
            except Stop as exc:
                self._failed(provider, key, answers, str(exc))
                continue
            except Exception:
                self._failed(provider, key, answers, 'erreur_service_sources')
                continue
            self._record(provider.name, True)
            self._store(key, provider.name, result)
            answers.append({'provider': provider.name, 'official': provider.official, 'ok': True,
                            'result': {**result, 'cached': False, 'stale': False, 'fetched_at': self.clock()}})
        return answers

    def _failed(self, provider, key, answers, error):
        self.stats['failures'] += 1
        self._record(provider.name, False)
        stale = self._cached(key, provider.name, allow_stale=True)
        if stale:
            self.stats['stale'] += 1
            answers.append({'provider': provider.name, 'official': provider.official, 'ok': True, 'result': stale,
                            'warning': error})
        else:
            answers.append({'provider': provider.name, 'official': provider.official, 'ok': False, 'error': error})


def build_providers(desk):
    cfg = desk.c.get('legal_sources', {}) or {}
    providers = []
    local = cfg.get('local_file') or str(Path(desk.c.get('state_dir', '.')) / 'legal_sources' / 'local.json')
    if cfg.get('local_enabled', True) and Path(local).is_file():
        providers.append(LocalProvider(local))
    piste = cfg.get('piste') or {}
    client_id = piste.get('client_id') or ''
    secret = ''
    secret_path = Path(desk.c.get('state_dir', '.')) / 'secrets' / 'piste_client_secret'
    try:
        if piste.get('client_secret_file'):
            secret = read_secret(piste['client_secret_file'])
        elif secret_path.is_file():
            secret = read_secret(str(secret_path))
    except Stop:
        secret = ''
    client_id = client_id or str(desk.settings('sources470:piste_client_id', '') or '')
    if client_id and secret:
        if piste.get('legifrance', True):
            providers.append(PisteLegifrance(client_id, secret))
        if piste.get('judilibre', True):
            providers.append(PisteJudilibre(client_id, secret))
    return providers


def save_settings(desk, enabled, client_id='', secret='', clear_secret=False):
    desk.setting('automation:sources470_enabled', bool(enabled))
    client_id = str(client_id or '').strip()
    if client_id:
        if not re.fullmatch(r'[A-Za-z0-9\-_.]{8,128}', client_id):
            raise Stop('identifiant_piste_invalide')
        desk.setting('sources470:piste_client_id', client_id)
    secret = str(secret or '').strip()
    path = Path(desk.c.get('state_dir', '.')) / 'secrets' / 'piste_client_secret'
    if secret:
        if len(secret) < 8 or len(secret) > 512 or any(ord(c) < 33 for c in secret):
            raise Stop('secret_piste_invalide')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        import os
        fd = os.open(str(path) + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secret + '\n')
        os.replace(str(path) + '.tmp', path)
    elif clear_secret and path.exists():
        path.unlink()
    desk.audit('sources_470_reglages', {'enabled': bool(enabled), 'client_id_set': bool(client_id), 'secret_updated': bool(secret)})
    return {'saved': True, 'enabled': bool(enabled)}


def outbound_log(desk, limit=50):
    rows = desk.db.execute("SELECT at,data FROM audit WHERE action='source_470_requete' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for at, data in rows:
        try:
            out.append({'at': at, **json.loads(data)})
        except ValueError:
            continue
    return out
