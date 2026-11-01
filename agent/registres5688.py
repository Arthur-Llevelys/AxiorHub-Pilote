"""5.6.88 : accès aux registres publics français (BODACC, Recherche d'entreprises), sans clé, sans modèle, sans veille ni interface.

Seul endroit où ces deux sources sont appelées : un SIREN ou un nom n'y part que vers ``HOSTS``, jamais vers le journal ni vers un modèle.
- ``entreprise(desk, siren)`` : fiche normalisée, ou ``None`` si la source répond qu'elle ne connaît pas ce SIREN ;
- ``chercher(desk, texte)`` : entreprises d'après un nom ou un SIREN ;
- ``annonces(desk, siren, familles=None, limit=100)`` : annonces normalisées, les plus récentes d'abord.

Une panne n'est jamais un résultat vide : ``SourceIndisponible`` est levée quand la source ne répond pas, répond une erreur ou un contenu
illisible. Une réponse vide et bien formée signifie « rien à signaler ». Les pannes ne sont pas mises en cache. Le cache (24 h par adresse)
contient des données publiques ; ses lignes sont retirées après 7 jours.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from urllib.parse import urlencode, urlsplit

from .common import HTTP, Stop

BODACC = 'https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records'
ENTREPRISES = 'https://recherche-entreprises.api.gouv.fr/search'
HOSTS = frozenset({urlsplit(BODACC).netloc, urlsplit(ENTREPRISES).netloc})
SOURCES = {'bodacc': 'BODACC (bodacc-datadila.opendatasoft.com)', 'entreprises': 'API Recherche d’entreprises (recherche-entreprises.api.gouv.fr)'}
# Valeurs relevées sur la source le 2026-11-01 (regroupement par familleavis) ; « divers » et « inconnue » existent mais ne sont pas filtrables ici.
FAMILLES = frozenset({'collective', 'conciliation', 'retablissement_professionnel', 'radiation', 'modification', 'vente', 'creation', 'immatriculation',
                      'dpc', 'divers'})
TIMEOUT = 15
CACHE_TTL = 86400
CACHE_KEEP = 7 * 86400
MAX_BODY = 4_000_000
MAX_LIMIT = 100
ANNONCE_URL = 'https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:'
ANNUAIRE_URL = 'https://annuaire-entreprises.data.gouv.fr/entreprise/'     # pages lues par une personne, jamais appelées par le logiciel
INFOGREFFE_URL = 'https://www.infogreffe.fr/entreprise/'                   # simple lien : aucun appel, aucun achat depuis AxiorHub
HTTP_FACTORY = None      # tests : fabrique de faux clients ; jamais de réseau réel dans les tests


class SourceIndisponible(Stop):
    """La source n'a pas répondu de façon exploitable : à ne jamais lire comme « rien à signaler »."""

    def __init__(self, source):
        super().__init__('source_registre_indisponible')
        self.source = source


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm_siren(value):
    """Neuf chiffres (un SIRET donne son SIREN). 000000000 est refusé : le BODACC l'emploie pour des commerçants sans numéro, sans lien entre eux."""
    digits = re.sub(r'[\s.\-\xa0]', '', str(value or ''))
    if not re.fullmatch(r'\d{9}(\d{5})?', digits) or digits[:9] == '000000000':
        raise Stop('siren_invalide')
    return digits[:9]


def _schema(desk):
    desk.db.execute('CREATE TABLE IF NOT EXISTS registres_cache5688(cle TEXT PRIMARY KEY, source TEXT NOT NULL, lu REAL NOT NULL, corps TEXT NOT NULL)')


def _client(origin):
    return (HTTP_FACTORY or (lambda base: HTTP(base, timeout=TIMEOUT)))(origin)


def _lire(desk, source, url, valide, ttl=CACHE_TTL, force=False):
    """(données, lue en cache ?) ; ``valide`` contrôle la forme de la réponse : hors de cette forme, la source est réputée indisponible."""
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc not in HOSTS:
        raise Stop('origine_http_refusee')
    _schema(desk)
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()
    now = time.time()
    if not force:
        row = desk.db.execute('SELECT lu,corps FROM registres_cache5688 WHERE cle=?', (key,)).fetchone()
        if row and now - float(row[0]) < ttl:
            return json.loads(row[1]), True
    try:
        raw = _client('https://' + parts.netloc).request('GET', url, None, {'Accept': 'application/json'}, MAX_BODY)
        data = json.loads(raw)
        if not valide(data):
            raise ValueError('forme')
    except Exception as ex:   # noqa: BLE001 — quelle que soit la cause (délai, 429, 500, JSON), la réponse n'est pas exploitable
        raise SourceIndisponible(source) from ex
    desk.db.execute('INSERT OR REPLACE INTO registres_cache5688 VALUES(?,?,?,?)', (key, source, now, json.dumps(data, ensure_ascii=False)))
    desk.db.execute('DELETE FROM registres_cache5688 WHERE lu<?', (now - CACHE_KEEP,))
    desk.db.commit()
    return data, False


def decode(value):
    """Champs BODACC livrés en JSON dans une chaîne : dict ou liste si possible, sinon le texte brut sous ``texte``, jamais d'exception."""
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ''):
        return {}
    try:
        out = json.loads(str(value))
    except (ValueError, TypeError):
        return {'texte': str(value)}
    return out if isinstance(out, (dict, list)) else {'texte': str(out)}


def _personnes(raw):
    """Liste de personnes d'un champ ``listepersonnes`` : un objet seul, une liste, ou absent."""
    data = decode(raw)
    data = data.get('personne', data) if isinstance(data, dict) else data
    return [p for p in (data if isinstance(data, list) else [data]) if isinstance(p, dict) and p]


def _sirens(raw):
    values = raw if isinstance(raw, list) else [raw]
    out = []
    for v in values:
        digits = re.sub(r'\D', '', str(v or ''))
        if len(digits) == 9 and digits not in out:
            out.append(digits)
    return out


def _url(rec, ident):
    """Lien officiel : seule l'adresse du BODACC est reprise ; toute autre valeur est reconstruite depuis l'identifiant."""
    url = str(rec.get('url_complete') or '')
    return url if url.startswith('https://www.bodacc.fr/') else (ANNONCE_URL + ident if re.fullmatch(r'[A-Za-z0-9]+', ident) else '')


def annonce(rec):
    """Annonce normalisée (``None`` sans identifiant). Les champs JSON sont décodés ; aucun classement ici : il revient à l'appelant."""
    ident = str(rec.get('id') or '').strip()
    if not ident:
        return None
    return {'id': ident, 'date': str(rec.get('dateparution') or '')[:10], 'famille': str(rec.get('familleavis') or ''), 'famille_libelle': str(rec.get('familleavis_lib') or ''),
            'type': str(rec.get('typeavis') or ''), 'type_libelle': str(rec.get('typeavis_lib') or ''), 'commercant': str(rec.get('commercant') or ''),
            'ville': str(rec.get('ville') or ''), 'tribunal': str(rec.get('tribunal') or ''), 'sirens': _sirens(rec.get('registre')), 'url': _url(rec, ident),
            'jugement': decode(rec.get('jugement')), 'depot': decode(rec.get('depot')), 'radiation': decode(rec.get('radiationaurcs')),
            'personnes': _personnes(rec.get('listepersonnes')), 'modifications': decode(rec.get('modificationsgenerales')), 'acte': decode(rec.get('acte')),
            'precedents_proprietaires': _personnes(rec.get('listeprecedentproprietaire')), 'precedents_exploitants': _personnes(rec.get('listeprecedentexploitant'))}


def _bodacc_ok(data):
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        return False
    return bool(data['results']) or not data.get('total_count')     # un total positif sans ligne n'est pas « rien à signaler »


def annonces(desk, siren, familles=None, limit=100, depuis='', force=False):
    """{'annonces', 'total', 'partiel', 'source', 'date_lecture', 'cache'} ; ``familles`` : valeurs officielles de ``familleavis`` ; ``depuis`` : AAAA-MM-JJ.
    Le filtre ``like`` de la source étant approché, seule une annonce portant ce SIREN exact est gardée. Panne : ``SourceIndisponible``."""
    siren = norm_siren(siren)
    where = ['registre like "%s"' % siren]
    if familles:
        chosen = sorted({str(f) for f in familles})
        if not set(chosen) <= FAMILLES:
            raise Stop('famille_bodacc_invalide')
        where.append('familleavis in (%s)' % ','.join('"%s"' % f for f in chosen))
    if depuis:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(depuis)):
            raise Stop('date_invalide')
        where.append("dateparution>=date'%s'" % depuis)
    limit = max(1, min(int(limit), MAX_LIMIT))
    url = BODACC + '?' + urlencode({'where': ' and '.join(where), 'order_by': 'dateparution desc', 'limit': limit})
    data, cached = _lire(desk, 'bodacc', url, _bodacc_ok, force=force)
    rows = [a for a in (annonce(r) for r in data['results'] if isinstance(r, dict)) if a and siren in a['sirens']]
    total = int(data.get('total_count') or 0)
    return {'annonces': rows, 'total': total, 'partiel': total > limit, 'source': SOURCES['bodacc'], 'date_lecture': _now(), 'cache': cached}


def _texte(*values):
    return ' '.join(str(v).strip() for v in values if v not in (None, '') and str(v).strip())


def _dirigeant(d):
    if d.get('denomination'):
        return {'nom': str(d['denomination']), 'prenoms': '', 'qualite': str(d.get('qualite') or ''), 'type': 'personne morale', 'siren': str(d.get('siren') or '')}
    return {'nom': str(d.get('nom') or ''), 'prenoms': str(d.get('prenoms') or ''), 'qualite': str(d.get('qualite') or ''), 'type': 'personne physique', 'siren': ''}


def _entreprise(r):
    siege = r.get('siege') if isinstance(r.get('siege'), dict) else {}
    voie = _texte(*(siege.get(k) for k in ('numero_voie', 'indice_repetition', 'type_voie', 'libelle_voie')))
    return {'siren': str(r.get('siren') or ''), 'nom': str(r.get('nom_raison_sociale') or r.get('nom_complet') or ''), 'nom_complet': str(r.get('nom_complet') or ''),
            'sigle': str(r.get('sigle') or ''), 'nature_juridique': str(r.get('nature_juridique') or ''), 'etat': str(r.get('etat_administratif') or ''),
            'date_creation': str(r.get('date_creation') or ''), 'date_fermeture': str(r.get('date_fermeture') or ''), 'activite': str(r.get('activite_principale') or ''),
            'siret_siege': str(siege.get('siret') or ''), 'adresse': str(siege.get('adresse') or ''), 'adresse_ligne1': voie, 'code_postal': str(siege.get('code_postal') or ''),
            'ville': str(siege.get('libelle_commune') or ''),
            'dirigeants': [_dirigeant(d) for d in (r.get('dirigeants') or []) if isinstance(d, dict)][:20]}


def _entreprises_ok(data):
    return isinstance(data, dict) and isinstance(data.get('results'), list)


def chercher(desk, texte, limit=5, force=False):
    """{'resultats', 'source', 'date_lecture', 'cache'} d'après un nom ou un SIREN. Un nom part vers cette source publique : à n'appeler que sur demande d'une personne."""
    texte = re.sub(r'\s+', ' ', str(texte or '')).strip()[:120]
    if len(texte) < 3:
        raise Stop('recherche_trop_courte')
    limit = max(1, min(int(limit), 10))
    url = ENTREPRISES + '?' + urlencode({'q': texte, 'page': 1, 'per_page': limit})
    data, cached = _lire(desk, 'entreprises', url, _entreprises_ok, force=force)
    return {'resultats': [_entreprise(r) for r in data['results'] if isinstance(r, dict) and r.get('siren')],
            'source': SOURCES['entreprises'], 'date_lecture': _now(), 'cache': cached}


def _complements(rows, siren):
    """Capital et RCS : la personne désignée par CE SIREN dans l'annonce la plus récente qui les donne (une vente cite aussi l'autre partie)."""
    out = {'capital': None, 'rcs': '', 'forme_juridique': '', 'annonce_id': '', 'annonce_date': ''}
    for a in rows:
        for p in a['personnes']:
            imm = p.get('numeroImmatriculation') if isinstance(p.get('numeroImmatriculation'), dict) else {}
            if re.sub(r'\D', '', str(imm.get('numeroIdentification') or '')) != siren:
                continue
            found = False
            cap = p.get('capital') if isinstance(p.get('capital'), dict) else {}
            amount = cap.get('montantCapital') or cap.get('montant')
            if amount and out['capital'] is None:
                out['capital'] = {'montant': str(amount), 'devise': str(cap.get('devise') or 'EUR')}
                found = True
            if imm.get('nomGreffeImmat') and not out['rcs']:
                out['rcs'] = '%s %s %s %s' % (str(imm.get('codeRCS') or 'RCS'), imm['nomGreffeImmat'], siren[:3] + ' ' + siren[3:6], siren[6:])
                found = True
            if p.get('formeJuridique') and not out['forme_juridique']:
                out['forme_juridique'] = str(p['formeJuridique'])
                found = True
            if found and not out['annonce_id']:
                out['annonce_id'], out['annonce_date'] = a['id'], a['date']
        if out['capital'] is not None and out['rcs'] and out['forme_juridique']:
            break
    return out


def entreprise(desk, siren, force=False):
    """Fiche normalisée ; ``None`` si l'annuaire répond sans ce SIREN ; ``SourceIndisponible`` si l'annuaire ne répond pas.
    Capital, RCS et forme juridique viennent du BODACC quand il les donne ; une panne de ce seul complément laisse la fiche utilisable et
    se signale par ``complements_indisponibles`` : True (capital et RCS restent alors inconnus, pas absents)."""
    siren = norm_siren(siren)
    url = ENTREPRISES + '?' + urlencode({'q': siren, 'page': 1, 'per_page': 5})
    data, cached = _lire(desk, 'entreprises', url, _entreprises_ok, force=force)
    match = next((r for r in data['results'] if isinstance(r, dict) and str(r.get('siren')) == siren), None)
    if match is None:
        return None
    out = _entreprise(match)
    out.update({'source': SOURCES['entreprises'], 'date_lecture': _now(), 'cache': cached, 'complements_indisponibles': False,
                'capital': None, 'rcs': '', 'forme_juridique': '', 'annonce_id': '', 'annonce_date': '', 'source_complements': ''})
    try:
        rows = annonces(desk, siren, familles=('immatriculation', 'modification', 'creation', 'vente'), limit=30, force=force)['annonces']
        out.update(_complements(rows, siren))
        out['source_complements'] = SOURCES['bodacc'] if out['annonce_id'] else ''
    except SourceIndisponible:
        out['complements_indisponibles'] = True
    return out
