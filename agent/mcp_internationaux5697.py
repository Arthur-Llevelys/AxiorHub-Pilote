"""5.6.97 (J04, J05, J08) : adaptateurs AxiorHub intégrés pour les droits étrangers, présentés comme des outils MCP locaux.

- Un module par fonds (fonds_de5697, fonds_es5697, fonds_gb5697, fonds_cafed5697, fonds_caqc5697), choisi par un identifiant compilé
  (``FONDS``) : aucun script, module ni endpoint fourni par une réponse ou un paramètre ne peut en ouvrir un autre.
- Transport intégré : ``appel(item, méthode, paramètres)`` a la forme attendue par ``mcp5614.CallSession`` (initialize, tools/list,
  tools/call) et est branché dans ``extensions364._mcp_call`` ; la recherche passe donc par le même registre et le même filtre de
  sélection des sources (J07, lot proteger) que les MCP distants. Aucun appel réseau pendant une recherche : l'outil lit l'index local.
- Outils publiés : ``search_norms`` (index du fonds, requêtes SQLite paramétrées) et ``get_norm`` (relecture d'un article à une date).
  ``search_decisions`` et ``get_decision`` ne sont publiés pour aucun fonds : leur module de décisions n'est ni autorisé ni testé ;
  l'état (couverture partielle, licence à vérifier, lien seulement, clé conditionnelle) et le lien officiel sont rendus à la place,
  jamais une référence inventée.
- Index SQLite par fonds : ``<state_dir>/sources5697/<fonds>/index-<uuid>.sqlite3`` désigné par ``actif.json``. Un nouvel index est
  préparé à côté (copie du précédent + documents relus), validé, puis désigné par remplacement atomique ; en cas d'échec l'index
  précédent et sa date restent. Rétention : l'index actif et le précédent.
- Synchronisation réseau séparée du traitement des dossiers, désactivée par défaut, déclenchée par un administrateur : hôte officiel
  fixé par le fonds, taille bornée, XML sans DTD ni entité, archives bornées sans chemin sortant.
- Citation (``citation``) : pays, sous-zone, identifiant officiel, lien, langue, version, date utile et extrait. « Citable » seulement
  si la version en vigueur à la date utile est prouvée par l'index, sur un hôte officiel, dans une langue officielle du fonds et sans
  modification connue non intégrée ; sinon « Piste à vérifier » avec la raison.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
import zipfile

from .common import Stop, fold, xml_bytes

FONDS = {'de_bund': 'fonds_de5697', 'es_boe': 'fonds_es5697', 'gb_legislation': 'fonds_gb5697',
         'ca_federal': 'fonds_cafed5697', 'ca_quebec': 'fonds_caqc5697'}
RACINE = 'sources5697'
PROTOCOLE = '2025-06-18'
MAX_RESULTATS = 20
STATUT_CITABLE, STATUT_PISTE = 'citable', 'piste_a_verifier'
LIBELLES_DECISIONS = {'couverture_partielle': 'Décisions : couverture partielle',
                      'licence_a_verifier': 'Décisions : licence à vérifier',
                      'lien_seulement': 'Décisions : consultation sur le site officiel seulement',
                      'cle_conditionnelle': 'Décisions : source conditionnelle (clé et conditions à vérifier)'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS normes(id INTEGER PRIMARY KEY, identifiant TEXT NOT NULL, abreviation TEXT NOT NULL DEFAULT '', titre TEXT NOT NULL,
  langue TEXT NOT NULL, zone TEXT NOT NULL, lien TEXT NOT NULL, nature TEXT NOT NULL, publication TEXT NOT NULL DEFAULT '',
  a_jour_au TEXT NOT NULL DEFAULT '', eli TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '', UNIQUE(identifiant, langue));
CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY, norme INTEGER NOT NULL REFERENCES normes(id) ON DELETE CASCADE, article TEXT NOT NULL,
  cle TEXT NOT NULL, titre TEXT NOT NULL DEFAULT '', texte TEXT NOT NULL, debut TEXT NOT NULL DEFAULT '', fin TEXT NOT NULL DEFAULT '',
  etendue TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', original INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL DEFAULT '',
  lien TEXT NOT NULL, recherche TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS articles_cle ON articles(norme, cle, debut);
'''


def module(pid):
    """Module du fonds, par identifiant compilé uniquement."""
    nom = FONDS.get(str(pid or ''))
    if not nom:
        raise Stop('adaptateur_integre_inconnu')
    return importlib.import_module('.' + nom, __package__)


# ------------------------------------------------------------------------------------------------ lecture sûre des documents
def xml_sur(raw, max_octets=8_000_000):
    """XML sans DTD ni entité (refus avant analyse), taille bornée."""
    raw = bytes(raw or b'')
    if len(raw) > max_octets:
        raise Stop('document_source_trop_volumineux')
    return xml_bytes(raw)


def zip_sur(raw, max_total=40_000_000, max_fichiers=50, suffixe='.xml'):
    """Fichiers d'une archive : décompression bornée (taille annoncée ET lue), aucun chemin absolu ni sortant."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    except zipfile.BadZipFile:
        raise Stop('archive_source_invalide') from None
    infos = archive.infolist()
    if len(infos) > max_fichiers:
        raise Stop('archive_source_trop_de_fichiers')
    total, out = 0, []
    for info in infos:
        name = info.filename.replace('\\', '/')
        if name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '..' in name.split('/'):
            raise Stop('archive_source_chemin_refuse')
        if info.is_dir() or not name.lower().endswith(suffixe):
            continue
        total += info.file_size
        if total > max_total:
            raise Stop('archive_source_trop_volumineuse')
        with archive.open(info) as stream:
            data = stream.read(info.file_size + 1)
        if len(data) > info.file_size:
            raise Stop('archive_source_taille_incoherente')
        out.append((name, data))
    return out


def texte(element):
    """Texte d'un élément (descendants compris), espaces normalisés."""
    return re.sub(r'\s+', ' ', ''.join(element.itertext())).strip() if element is not None else ''


def local(tag):
    return str(tag).rsplit('}', 1)[-1]


def cle_article(value):
    """Clé normalisée d'un article : « § 433 » → 433, « Artículo 1. » → 1, « section-2 » → 2, « 2999.1.1 » reste tel quel."""
    m = re.search(r'\d+(?:\.\d+)*(?:\s*[a-z]\b)?', fold(str(value or '')))
    return re.sub(r'\s+', '', m.group(0)) if m else fold(str(value or '')).strip()[:40]


def jour(value):
    """Date ISO à partir de AAAAMMJJ, AAAA-MM-JJ ou AAAAMMJJhhmmss ; '' si absente ou illisible."""
    value = str(value or '').strip()
    m = re.fullmatch(r'(\d{4})-?(\d{2})-?(\d{2})(?:[T ]?\d{2}:?\d{2}:?\d{2}Z?)?', value) or re.fullmatch(r'(\d{4})(\d{2})(\d{2})\d{6}', value)
    if not m:
        return ''
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return ''


def hote_officiel(url, hotes):
    from urllib.parse import urlsplit
    p = urlsplit(str(url or ''))
    return p.scheme == 'https' and not p.username and not p.password and (p.hostname or '').lower() in hotes


# ------------------------------------------------------------------------------------------------ index par fonds
def _dossier(racine, pid):
    racine = Path(racine)
    if racine.name != RACINE:
        raise Stop('index_source_racine_invalide')
    module(pid)
    return racine / pid


def _actif(racine, pid):
    try:
        info = json.loads((_dossier(racine, pid) / 'actif.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    fichier = _dossier(racine, pid) / str(info.get('fichier') or '')
    if not re.fullmatch(r'index-[0-9a-f]{32}\.sqlite3', fichier.name) or not fichier.is_file():
        return None
    return {**info, 'chemin': fichier}


def etat_index(racine, pid):
    """État affiché sur la carte : présent ou non, date, origine, volumes ; jamais un faux zéro si l'index est absent."""
    info = _actif(racine, pid)
    if not info:
        return {'present': False, 'date': '', 'origine': '', 'normes': None, 'articles': None}
    return {'present': True, 'date': info.get('date', ''), 'origine': info.get('origine', ''), 'normes': info.get('normes'),
            'articles': info.get('articles'), 'documents': info.get('documents', [])}


def _ouvrir(racine, pid):
    info = _actif(racine, pid)
    if not info:
        return None
    db = sqlite3.connect('file:%s?mode=ro' % info['chemin'].as_posix(), uri=True)
    db.row_factory = sqlite3.Row
    return db


def _fins(db):
    """Fin implicite d'une version : la veille de la version suivante du même article (même norme, même langue)."""
    rows = db.execute('SELECT id, norme, cle, debut, fin FROM articles ORDER BY norme, cle, debut').fetchall()
    for a, b in zip(rows, rows[1:]):
        if a[1] == b[1] and a[2] == b[2] and not a[4] and a[3] and b[3] and b[3] > a[3]:
            fin = (date.fromisoformat(b[3]) - timedelta(days=1)).isoformat()
            db.execute('UPDATE articles SET fin=? WHERE id=?', (fin, a[0]))


def _inserer(db, mod, normes):
    for n in normes:
        db.execute('DELETE FROM articles WHERE norme IN (SELECT id FROM normes WHERE identifiant=? AND langue=?)', (n['identifiant'], n['langue']))
        db.execute('DELETE FROM normes WHERE identifiant=? AND langue=?', (n['identifiant'], n['langue']))
    seen = set()
    for n in normes:
        key = (n['identifiant'], n['langue'])
        if key not in seen:
            seen.add(key)
            db.execute('INSERT INTO normes(identifiant,abreviation,titre,langue,zone,lien,nature,publication,a_jour_au,eli,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (n['identifiant'], n.get('abreviation', ''), n['titre'], n['langue'], n['zone'], n['lien'], n.get('nature', 'consolide'),
                        n.get('publication', ''), n.get('a_jour_au', ''), n.get('eli', ''), n.get('revision', '')))
        nid = db.execute('SELECT id FROM normes WHERE identifiant=? AND langue=?', key).fetchone()[0]
        for a in n.get('articles', ()):
            corps = str(a.get('texte') or '')
            db.execute('INSERT INTO articles(norme,article,cle,titre,texte,debut,fin,etendue,version,original,revision,lien,recherche,sha256) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (nid, a['article'], cle_article(a['article']), a.get('titre', ''), corps, a.get('debut', ''), a.get('fin', ''),
                        a.get('etendue', ''), a.get('version', ''), 1 if a.get('original') else 0, a.get('revision', ''), a['lien'],
                        fold(' '.join((n.get('abreviation', ''), n['titre'], a['article'], a.get('titre', ''), corps))),
                        hashlib.sha256(corps.encode('utf-8')).hexdigest()))


def _valider(db, mod):
    """Contrôle avant bascule : intégrité SQLite, au moins un article, liens sur un hôte officiel du fonds, texte non vide."""
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise Stop('index_source_corrompu')
    if not db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]:
        raise Stop('index_source_vide')
    for (lien,) in db.execute('SELECT lien FROM normes UNION SELECT lien FROM articles'):
        if not hote_officiel(lien, mod.HOTES):
            raise Stop('index_source_lien_non_officiel')
    if db.execute("SELECT COUNT(*) FROM articles WHERE trim(texte)=''").fetchone()[0]:
        raise Stop('index_source_texte_vide')
    zones = {z for (z,) in db.execute('SELECT DISTINCT zone FROM normes')}
    if not zones <= set(mod.ZONES):
        raise Stop('index_source_zone_hors_fonds')


def construire(racine, pid, normes, origine, documents=()):
    """Prépare un nouvel index (copie de l'actif + normes relues), le valide, puis le désigne ; l'actif reste en cas d'échec."""
    mod = module(pid)
    dossier = _dossier(racine, pid)
    dossier.mkdir(parents=True, exist_ok=True)
    precedent = _actif(racine, pid)
    nouveau = dossier / ('index-%s.sqlite3' % uuid.uuid4().hex)
    try:
        db = sqlite3.connect(str(nouveau))
        try:
            if precedent:
                source = sqlite3.connect('file:%s?mode=ro' % precedent['chemin'].as_posix(), uri=True)
                try:
                    source.backup(db)
                finally:
                    source.close()
            db.executescript(SCHEMA)
            db.execute('PRAGMA foreign_keys=ON')
            _inserer(db, mod, list(normes))
            _fins(db)
            _valider(db, mod)
            maintenant = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('fonds', pid))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('date', maintenant))
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('origine', origine))
            db.commit()
            compte = (db.execute('SELECT COUNT(*) FROM normes').fetchone()[0], db.execute('SELECT COUNT(*) FROM articles').fetchone()[0])
            idents = sorted({r[0] for r in db.execute('SELECT identifiant FROM normes')})
        finally:
            db.close()
    except BaseException:
        try:
            nouveau.unlink()
        except OSError:
            pass
        raise
    info = {'fichier': nouveau.name, 'date': maintenant, 'origine': origine, 'normes': compte[0], 'articles': compte[1],
            'documents': idents[:200], 'precedent': precedent['fichier'] if precedent else ''}
    temporaire = dossier / ('actif-%s.json' % uuid.uuid4().hex)
    temporaire.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
    os.replace(temporaire, dossier / 'actif.json')
    garder = {nouveau.name, info['precedent']}
    for ancien in dossier.glob('index-*.sqlite3'):          # rétention : l'index actif et le précédent
        if ancien.name not in garder:
            try:
                ancien.unlink()
            except OSError:
                pass
    return etat_index(racine, pid)


def importer(racine, pid, documents, origine='instantane_local'):
    """Instantané local (aucun réseau) : ``documents`` = [(identifiant, octets)] au format officiel du fonds."""
    mod = module(pid)
    normes = []
    for identifiant, contenu in documents:
        normes += mod.analyser(contenu, identifiant)
    return construire(racine, pid, normes, origine)


# ------------------------------------------------------------------------------------------------ recherche, relecture, citation
def _valide_a(row, quand):
    return (not row['debut'] or row['debut'] <= quand) and (not row['fin'] or row['fin'] >= quand)


def _date(value):
    value = str(value or '').strip()
    if not value:
        return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise Stop('date_utile_invalide')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise Stop('date_utile_invalide') from None
    return value


def _decisions(pid):
    from .sources5632 import PROVIDERS
    d = dict(PROVIDERS[pid].get('decisions') or {})
    d['libelle'] = LIBELLES_DECISIONS.get(d.get('etat', ''), 'Décisions : non couvertes par cet adaptateur')
    return d


def chercher(racine, pid, requete, zone='', quand='', langue='', limite=8):
    """Recherche dans l'index du fonds : requêtes paramétrées, zone et version contrôlées ; le résultat est une piste."""
    mod = module(pid)
    requete = str(requete or '').strip()
    if not 1 <= len(requete) <= 300:
        raise Stop('requete_source_invalide')
    zone = str(zone or mod.ZONES[0])
    quand = _date(quand) or date.today().isoformat()
    limite = max(1, min(int(limite or 8), MAX_RESULTATS))
    base = {'fonds': pid, 'zone': zone, 'date': quand, 'decisions': _decisions(pid), 'results': []}
    if zone not in mod.ZONES:
        return {**base, 'statut': 'hors_perimetre', 'message': 'Zone hors du périmètre de cet adaptateur.'}
    if langue and langue not in mod.LANGUES:
        return {**base, 'statut': 'hors_perimetre', 'message': 'Langue non publiée par ce fonds.'}
    db = _ouvrir(racine, pid)
    if db is None:
        return {**base, 'statut': 'index_absent', 'message': 'Index local absent : aucune donnée de ce fonds sur ce serveur.'}
    mots = [m for m in re.findall(r'\w{3,}|\d+', fold(requete))][:8]
    if not mots:
        db.close()
        return {**base, 'statut': 'ok'}
    try:
        filtre = ' OR '.join(['a.recherche LIKE ?'] * len(mots) + ['a.cle = ?'] * len(mots))
        rows = db.execute('SELECT a.*, n.identifiant, n.abreviation, n.titre AS norme_titre, n.langue, n.zone AS norme_zone, n.lien AS norme_lien, '
                          'n.nature, n.eli FROM articles a JOIN normes n ON n.id=a.norme WHERE (%s) LIMIT 2000' % filtre,
                          ['%' + m + '%' for m in mots] + list(mots)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        if not _valide_a(r, quand) or (langue and r['langue'] != langue) or not mod.couvre(zone, r['norme_zone'], r['etendue']):
            continue
        score = sum(1 for m in mots if m in r['recherche']) + sum(2 for m in mots if m == r['cle'])
        out.append((score, r))
    out.sort(key=lambda x: (-x[0], x[1]['identifiant'], x[1]['cle']))
    base['results'] = [{'title': '%s, %s' % (r['abreviation'] or r['norme_titre'], r['article']), 'summary': r['texte'][:500],
                        'provider_url': r['lien'], 'identifier': r['identifiant'], 'article': r['article'], 'date': r['debut'],
                        'version': r['version'], 'zone': r['norme_zone'], 'langue': r['langue'], 'nature': r['nature'],
                        'statut': STATUT_PISTE} for _, r in out[:limite]]
    base['statut'] = 'ok'
    return base


def citation(racine, pid, identifiant, article, date_utile='', langue='', original=False):
    """Citation normalisée (J07 « Citation », J08) : citable seulement si la version à la date utile est prouvée."""
    mod = module(pid)
    quand = _date(date_utile) or date.today().isoformat()
    sortie = {'fonds': pid, 'pays': mod.PAYS, 'sous_zone': '', 'identifiant_officiel': '', 'reference': '', 'lien': '', 'langue': langue or '',
              'version': '', 'debut': '', 'fin': '', 'date_utile': quand, 'extrait': '', 'texte': '', 'nature': '', 'publication': '',
              'origine': '', 'a_jour_au': '', 'statut': STATUT_PISTE, 'raison': '', 'decisions': _decisions(pid)}
    identifiant = str(identifiant or '').strip()
    if not re.fullmatch(mod.IDENT_RE, identifiant):
        return {**sortie, 'raison': 'identifiant_officiel_invalide'}
    if langue and langue not in mod.LANGUES:
        return {**sortie, 'raison': 'langue_non_officielle'}
    db = _ouvrir(racine, pid)
    if db is None:
        return {**sortie, 'raison': 'index_absent'}
    try:
        origine = (db.execute("SELECT valeur FROM meta WHERE cle='origine'").fetchone() or [''])[0]
        normes = db.execute('SELECT * FROM normes WHERE lower(identifiant)=lower(?) OR lower(abreviation)=lower(?) ORDER BY langue',
                            (identifiant, identifiant)).fetchall()
        if langue:
            normes = [n for n in normes if n['langue'] == langue]
        if not normes:
            return {**sortie, 'origine': origine, 'raison': 'reference_introuvable'}
        norme = next((n for n in normes if n['langue'] == mod.LANGUES[0]), normes[0])
        versions = db.execute('SELECT * FROM articles WHERE norme=? AND cle=? ORDER BY debut', (norme['id'], cle_article(article))).fetchall()
    finally:
        db.close()
    sortie.update(sous_zone=norme['zone'], langue=norme['langue'], nature=norme['nature'], publication=norme['publication'], origine=origine)
    if not versions:
        return {**sortie, 'raison': 'article_introuvable'}
    choisie = next((v for v in versions if v['original']), None) if original else next((v for v in versions if _valide_a(v, quand)), None)
    if choisie is None:
        premier = versions[0]['debut']
        raison = 'version_anterieure_non_prouvee' if premier and quand < premier else 'version_norme_non_verifiee'
        return {**sortie, 'raison': raison}
    sortie.update(identifiant_officiel=mod.identifiant_officiel(norme, choisie), reference=mod.reference(norme, choisie), lien=choisie['lien'],
                  version=choisie['version'], debut=choisie['debut'], fin=choisie['fin'], texte=choisie['texte'],
                  extrait=choisie['texte'][:1200], sha256=choisie['sha256'], nature='original' if choisie['original'] else norme['nature'])
    consolidation = jour(str(norme['a_jour_au'] or '')[:10])
    sortie['a_jour_au'] = consolidation
    if consolidation and quand > consolidation:
        sortie['raison'] = 'index_anterieur_a_la_date_utile'    # l'instantané ne prouve rien après sa propre date de consolidation
    elif not hote_officiel(choisie['lien'], mod.HOTES):
        sortie['raison'] = 'lien_non_officiel'
    elif norme['langue'] not in mod.LANGUES:
        sortie['raison'] = 'traduction_non_officielle'
    elif not mod.couvre(mod.ZONES[0], norme['zone'], choisie['etendue']):
        sortie['raison'] = 'hors_perimetre_territorial'          # ex. disposition propre à l'Écosse dans le fonds GB-EAW
    elif choisie['revision'] == 'modifications_non_appliquees' or norme['revision'] == 'modifications_non_appliquees':
        sortie['raison'] = 'modifications_non_appliquees'
    elif not sortie['identifiant_officiel']:
        sortie['raison'] = 'identifiant_officiel_absent'
    else:
        sortie['statut'], sortie['raison'] = STATUT_CITABLE, 'version_prouvee_a_la_date_utile'
    return sortie


# ------------------------------------------------------------------------------------------------ transport MCP intégré
def outils(pid):
    """Schémas JSON des entrées : aucun paramètre n'est une URL ; la zone est limitée aux zones compilées du fonds."""
    mod = module(pid)
    commun = {'date': {'type': 'string', 'description': 'Date utile AAAA-MM-JJ (par défaut : aujourd’hui).', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
              'langue': {'type': 'string', 'enum': list(mod.LANGUES)}}
    return [{'name': 'search_norms', 'description': 'Rechercher dans l’index local du fonds (%s).' % ', '.join(mod.ZONES),
             'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['query'],
                             'properties': {'query': {'type': 'string', 'maxLength': 300}, 'zone': {'type': 'string', 'enum': list(mod.ZONES)},
                                            'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_RESULTATS}, **commun}}},
            {'name': 'get_norm', 'description': 'Relire un article à une date utile, avec sa version et son lien officiel.',
             'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['identifiant', 'article'],
                             'properties': {'identifiant': {'type': 'string', 'maxLength': 80}, 'article': {'type': 'string', 'maxLength': 40},
                                            'original': {'type': 'boolean'}, **commun}}}]


def _arguments(schema, args):
    if not isinstance(args, dict):
        raise Stop('arguments_outil_invalides')
    props = schema['properties']
    if set(args) - set(props):
        raise Stop('arguments_outil_inconnus')
    for name in schema.get('required', ()):
        if not str(args.get(name) or '').strip():
            raise Stop('arguments_outil_requis')
    for name, value in args.items():
        spec = props[name]
        if spec['type'] == 'string' and (not isinstance(value, str) or len(value) > spec.get('maxLength', 40)):
            raise Stop('arguments_outil_invalides')
        if 'enum' in spec and value not in spec['enum']:
            raise Stop('arguments_outil_invalides')
        if spec['type'] == 'integer' and (isinstance(value, bool) or not isinstance(value, int)):
            raise Stop('arguments_outil_invalides')
        if spec['type'] == 'boolean' and not isinstance(value, bool):
            raise Stop('arguments_outil_invalides')
    return args


def _resultat(data):
    return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data, 'isError': False}


def appel(item, method, params=None):
    """Session MCP interne (initialize, tools/list, tools/call) ; l'adaptateur est choisi par l'identifiant compilé de la fiche."""
    pid = str((item or {}).get('provider') or '')
    mod = module(pid)
    racine = str(item.get('index_racine') or '')
    if method == 'initialize':
        from . import __version__
        return {'protocolVersion': PROTOCOLE, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'Adaptateur AxiorHub ' + pid, 'version': __version__}}
    if method == 'notifications/initialized':
        return {}
    if method == 'tools/list':
        return {'tools': outils(pid)}
    if method != 'tools/call':
        raise Stop('methode_mcp_inconnue')
    params = params if isinstance(params, dict) else {}
    nom = params.get('name')
    schemas = {t['name']: t['inputSchema'] for t in outils(pid)}
    if nom not in schemas:
        raise Stop('outil_integre_inconnu')
    args = _arguments(schemas[nom], params.get('arguments') or {})
    if nom == 'search_norms':
        return _resultat(chercher(racine, pid, args['query'], args.get('zone', ''), args.get('date', ''), args.get('langue', ''), args.get('limit', 8)))
    return _resultat(citation(racine, pid, args['identifiant'], args['article'], args.get('date', ''), args.get('langue', ''), bool(args.get('original'))))


# ------------------------------------------------------------------------------------------------ synchronisation réseau (séparée)
def etat_synchronisation(desk, pid):
    module(pid)
    etat = desk.settings('sources5697:sync:' + pid, {}) or {}
    mod = module(pid)
    return {'active': bool(etat.get('active')), 'qualifiee': bool(mod.SYNC_QUALIFIEE), 'raison_fermee': getattr(mod, 'SYNC_RAISON', ''),
            'derniere': etat.get('derniere') or {}}


def regler_synchronisation(desk, pid, active, owner=''):
    """Interrupteur d'administration : désactivé par défaut, réversible ; ne lance rien par lui-même."""
    mod = module(pid)
    if active and not mod.SYNC_QUALIFIEE:
        raise Stop('synchronisation_a_qualifier')
    etat = desk.settings('sources5697:sync:' + pid, {}) or {}
    etat['active'] = bool(active)
    desk.setting('sources5697:sync:' + pid, etat)
    desk.audit('sources5697_synchronisation_reglage', {'fonds': pid, 'active': bool(active), 'par': owner})
    return etat_synchronisation(desk, pid)


def lecture_officielle(mod):
    """Lecture réseau réelle : hôte officiel fixé par le fonds, GET seulement, sans redirection (classe HTTP), taille et type bornés."""
    from urllib.parse import urlsplit
    from .common import HTTP

    def lire(url, max_octets):
        if not hote_officiel(url, mod.HOTES):
            raise Stop('source_non_officielle')
        p = urlsplit(url)
        client = HTTP('%s://%s' % (p.scheme, p.netloc), timeout=60)
        raw = client.request('GET', url, headers={'Accept': mod.ACCEPT}, limit=max_octets)
        kind = ' '.join(str(v) for k, v in (client.last_headers or {}).items() if str(k).lower() == 'content-type').lower()
        if kind and not any(x in kind for x in ('xml', 'zip', 'octet-stream')):
            raise Stop('type_document_source_refuse')
        return raw
    return lire


def synchroniser(desk, pid, identifiants, lecture=None, owner=''):
    """Synchronisation explicite d'une liste bornée de documents publics ; nouvel index validé avant bascule ; échec = index
    précédent conservé avec sa date. Aucune donnée de dossier n'est lue ni transmise."""
    from .pause5629 import checkpoint
    from .sources5632 import _index_racine
    mod = module(pid)
    if not mod.SYNC_QUALIFIEE:
        raise Stop('synchronisation_a_qualifier')
    etat = desk.settings('sources5697:sync:' + pid, {}) or {}
    if not etat.get('active'):
        raise Stop('synchronisation_desactivee')
    idents = [str(x).strip() for x in (identifiants or []) if str(x).strip()]
    if not idents or len(idents) > 20 or any(not re.fullmatch(mod.IDENT_RE, x) for x in idents):
        raise Stop('identifiants_source_invalides')
    lecture = lecture or lecture_officielle(mod)
    racine = _index_racine(desk)
    debut = datetime.now(timezone.utc).isoformat()
    try:
        normes = []
        for ident in idents:
            documents = []
            for url in mod.urls_document(ident):
                checkpoint(desk)
                if not hote_officiel(url, mod.HOTES):
                    raise Stop('source_non_officielle')
                documents.append(lecture(url, mod.MAX_OCTETS))
            normes += mod.analyser_synchronisation(documents, ident)
        resultat = construire(racine, pid, normes, 'synchronisation')
        derniere = {'debut': debut, 'fin': datetime.now(timezone.utc).isoformat(), 'resultat': 'ok', 'erreur': '', 'documents': idents,
                    'index_date': resultat['date']}
    except Stop as ex:
        derniere = {'debut': debut, 'fin': datetime.now(timezone.utc).isoformat(), 'resultat': 'echec', 'erreur': str(ex).split(':')[0],
                    'documents': idents, 'index_date': etat_index(racine, pid).get('date', '')}
    etat['derniere'] = derniere
    desk.setting('sources5697:sync:' + pid, etat)
    desk.audit('sources5697_synchronisation', {'fonds': pid, 'resultat': derniere['resultat'], 'erreur': derniere['erreur'],
                                               'documents': len(idents), 'par': owner})
    return {'synchronisation': derniere, 'index': etat_index(racine, pid)}
