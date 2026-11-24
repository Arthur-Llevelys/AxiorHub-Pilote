"""5.6.109 (lot Q, G23) : « Fonds juridique local » — fonds publics sélectionnés, versionnés et actualisables, dans les Paramètres existants.

Reprise produit de ``reference/fonds_local109.py`` (``choisir_article``, ``verifier_lot``) raccordée au stockage et à l'index du produit :

- **Contrat de document public** : pays (juridiction), langue, nature (article, décision, texte), identifiant officiel, version, intervalle
  de vigueur PROUVÉ (``intervalle_verifie``) ou dates de décision et de publication, autorité, source et URL, date de récupération, droits du
  lot et empreinte SHA-256 du texte. ELI et ECLI ne sont conservés que s'ils sont fournis ; jamais fabriqués.
- **Fonds publics distincts des pièces privées** : chaque lot publié a sa propre collection de l'index commun (``index.DocumentIndex`` sur
  ``<state_dir>/fonds56109/<génération>/documents.sqlite3`` : même code FTS5/BM25, aucun second moteur). La recherche d'un dossier, celle du
  cabinet et ``recherche5695`` ne lisent jamais ces fichiers ; ce module ne lit jamais l'index des dossiers.
- **Import depuis un dossier local fourni par le cabinet** (``manifeste.json`` + ``documents.jsonl``) : aucun téléchargement par le
  logiciel dans cette version, aucune publication vers un service tiers (``push_to_hub`` et ``upload_file`` n'existent pas ici). Lecture
  par pas bornés avec un curseur durable (octet et rang) : une pause, une erreur ou un arrêt laissent le lot à son curseur, l'index actif
  intact ; « Reprendre » repart du curseur vérifié, sans tout réimporter.
- **Publication atomique d'un lot** : le lot est lu en entier, vérifié (``verifier_lot`` : nombre attendu, lot déclaré complet, empreintes,
  doublons), indexé dans un NOUVEAU fichier, puis désigné comme actif dans une seule transaction de la base du cabinet (l'actif précédent
  est conservé). Un lot partiel, modifié, dupliqué ou à licence non qualifiée n'est jamais promu ; l'absence d'un texte dans un lot partiel
  ne vaut ni suppression ni abrogation. Aucun fichier SQLite n'est jamais écrasé ni ouvert autrement que par ``sqlite3.connect``.
- **Date historique respectée** (``choisir_article``) : la version en vigueur à la date demandée, prouvée par la source ; la version courante
  ne reconstitue pas le passé ; dates inconnues ou versions en conflit → constat, jamais une certitude.
- **Jamais « droit à jour »** : la recherche indique « Corpus arrêté au [date] » (date déclarée par le manifeste du lot), sans réseau.
- **Export et restauration** : lots, documents (texte officiel compris) et désignations actives sont exportés ; l'index (fichiers) est
  recalculé : après restauration, « Index à reconstruire » puis « Reconstruire » le refait depuis les textes conservés.

Limites : la fraîcheur réelle d'un lot, ses droits de réutilisation et sa complétude relèvent du cabinet qui le fournit ; les embeddings ne
sont pas calculés pour le fonds dans cette version (recherche plein texte seule, sans réseau). La mise à jour par le réseau, les
importateurs de jeux tiers et le bail entre postes ne sont pas livrés.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from .common import Stop

RACINE = 'fonds56109'
PREFIXE = 'm56109/fonds/'
LECTURES = ('etat', 'rechercher', 'document')
ECRITURES = ('importer', 'avancer', 'suspendre', 'reprendre', 'supprimer-index', 'reconstruire')
ROLES = ('administrateur', 'avocat', 'assistant')
MANIFESTE, DOCUMENTS = 'manifeste.json', 'documents.jsonl'
MAX_MANIFESTE = 1_000_000
MAX_LIGNE = 4_000_000
MAX_DOCUMENTS = 4_000_000_000
PAS = 200
MAX_POST = 8_000
NATURES = ('article', 'decision', 'texte')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
_ID = re.compile(r'^[A-Za-z0-9_.:/-]{1,200}$')
_PAYS = re.compile(r'^[A-Z]{2}(-[A-Z0-9]{1,3})?$')
_LANGUE = re.compile(r'^[a-z]{2}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
_HOTE = re.compile(r'^[a-z0-9.-]{3,253}$')

ETATS = {'aucun': 'Aucun fonds importé', 'import': 'Import en cours', 'disponible': 'Disponible localement',
         'partiel': 'Partiellement couvert', 'echec': 'Synchronisation en échec', 'licence': 'Licence à qualifier',
         'index': 'Index à reconstruire', 'suspendu': 'Import suspendu'}
LIBELLES_LOT = {'preparation': 'Lecture du lot', 'indexation': 'Indexation', 'actif': 'Actif', 'precedent': 'Précédent (conservé)',
                'remplace': 'Remplacé', 'incomplet': 'Lot incomplet : jamais publié', 'rejete': 'Lot refusé : jamais publié',
                'suspendu': 'Suspendu', 'licence_a_qualifier': 'Licence à qualifier : jamais publié'}
MESSAGES = {
    'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Action réservée à l’administrateur du cabinet.',
    'origine_ou_csrf_refuse': 'Requête refusée (origine ou jeton).', 'requete_trop_longue': 'Requête trop longue.',
    'cle_requise': 'Clé d’idempotence manquante.', 'cle_reutilisee': 'Cette clé a déjà servi à une autre demande.',
    'version_perimee': 'Le lot a changé depuis l’affichage : rechargez la page.',
    'dossier_import_invalide': 'Dossier d’import introuvable ou refusé (chemin absolu d’un dossier du poste, sans lien symbolique).',
    'manifeste_absent': 'Le dossier ne contient pas de manifeste.json.', 'documents_absents': 'Le dossier ne contient pas de documents.jsonl.',
    'manifeste_incomplet': 'Manifeste incomplet (lot, source, pays, droits, couverture, nombre attendu, lot complet ou non).',
    'compte_lot_invalide': 'Nombre de documents attendu invalide.', 'manifeste_illisible': 'Manifeste illisible.',
    'lot_suspendu': 'Import suspendu : reprenez-le pour continuer.', 'lot_modifie_pendant_import': 'Le lot a été modifié pendant l’import : refusé.',
    'lot_trop_volumineux': 'Lot trop volumineux.', 'controle_acces_indisponible': 'Contrôle indisponible : rien n’a été fait.',
    'question_requise': 'Indiquez les mots à rechercher.', 'date_source_invalide': 'Date invalide (AAAA-MM-JJ).',
    'operation_en_pause': 'Pause en cours : rien n’a été modifié, l’import reprendra à son curseur.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'requete_trop_longue': '413 Payload Too Large', 'cle_reutilisee': '409 Conflict', 'version_perimee': '409 Conflict',
           'controle_acces_indisponible': '503 Service Unavailable'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fonds56109_lots(id TEXT PRIMARY KEY, fonds TEXT NOT NULL, snapshot_id TEXT NOT NULL, source TEXT NOT NULL,
  pays TEXT NOT NULL, langue TEXT NOT NULL DEFAULT '', matiere TEXT NOT NULL DEFAULT '', autorite TEXT NOT NULL DEFAULT '',
  rights_ref TEXT NOT NULL, licence TEXT NOT NULL DEFAULT '', licence_verifiee INTEGER NOT NULL DEFAULT 0, coverage TEXT NOT NULL,
  expected_count INTEGER NOT NULL, complet INTEGER NOT NULL DEFAULT 0, corpus_au TEXT NOT NULL DEFAULT '', domaines TEXT NOT NULL DEFAULT '[]',
  chemin_import TEXT NOT NULL DEFAULT '', manifeste_sha256 TEXT NOT NULL, documents_sha256 TEXT NOT NULL, documents_taille INTEGER NOT NULL DEFAULT 0,
  documents_mtime INTEGER NOT NULL DEFAULT 0, etat TEXT NOT NULL, etat_avant TEXT NOT NULL DEFAULT '', curseur INTEGER NOT NULL DEFAULT 0,
  octet INTEGER NOT NULL DEFAULT 0, importes INTEGER NOT NULL DEFAULT 0, indexes INTEGER NOT NULL DEFAULT 0, index_rel TEXT NOT NULL DEFAULT '',
  motif TEXT NOT NULL DEFAULT '', cree_par TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL,
  publie_le TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS fonds56109_lots_fonds ON fonds56109_lots(fonds, etat);
CREATE TABLE IF NOT EXISTS fonds56109_documents(lot TEXT NOT NULL, n INTEGER NOT NULL, official_id TEXT NOT NULL, kind TEXT NOT NULL,
  jurisdiction TEXT NOT NULL, language TEXT NOT NULL, version_id TEXT NOT NULL, valid_from TEXT NOT NULL DEFAULT '',
  valid_to_exclusive TEXT NOT NULL DEFAULT '', intervalle_verifie INTEGER NOT NULL DEFAULT 0, decision_date TEXT NOT NULL DEFAULT '',
  publication_date TEXT NOT NULL DEFAULT '', titre TEXT NOT NULL DEFAULT '', autorite TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
  retrieved_at TEXT NOT NULL DEFAULT '', source_updated_at TEXT NOT NULL DEFAULT '', rights_ref TEXT NOT NULL, eli TEXT NOT NULL DEFAULT '',
  ecli TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, texte TEXT NOT NULL,
  PRIMARY KEY(lot, jurisdiction, official_id, language, version_id));
CREATE INDEX IF NOT EXISTS fonds56109_documents_n ON fonds56109_documents(lot, n);
CREATE TABLE IF NOT EXISTS fonds56109_actifs(fonds TEXT PRIMARY KEY, lot_actif TEXT NOT NULL, lot_precedent TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1, publie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fonds56109_ops(cle TEXT PRIMARY KEY, compte TEXT NOT NULL, action TEXT NOT NULL, empreinte TEXT NOT NULL,
  resultat TEXT NOT NULL, cree_le TEXT NOT NULL);
'''


def ensure_schema(desk):
    """Au démarrage (``Desk._prepare``) ; rejouable, jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode('utf-8')).hexdigest()


# ======================================================================================== contrats temporels (reprise de la référence)
def _jour(value):
    if not isinstance(value, str) or len(value) != 10:
        raise Stop('date_source_invalide')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Stop('date_source_invalide') from None


def choisir_article(rows, *, official_id, jurisdiction, language, date_utile):
    """Version d'un article en vigueur à ``date_utile``. Une version courante ne permet pas de déduire l'historique manquant ; la preuve
    temporelle vient de la source (``intervalle_verifie``), jamais du modèle. États : ``version_selectionnee``, ``version_absente_a_cette_date``,
    ``couverture_temporelle_a_verifier``, ``versions_en_conflit``."""
    jour = _jour(date_utile)
    valides, incertain = [], False
    for r in rows:
        if (r.get('official_id'), r.get('jurisdiction'), r.get('language'), r.get('kind')) != (official_id, jurisdiction, language, 'article'):
            continue
        if not r.get('intervalle_verifie') or not r.get('valid_from'):
            incertain = True
            continue
        debut = _jour(r['valid_from'])
        fin = _jour(r['valid_to_exclusive']) if r.get('valid_to_exclusive') else None
        if fin is not None and fin <= debut:
            raise Stop('intervalle_source_invalide')
        if debut <= jour and (fin is None or jour < fin):
            valides.append(r)
    if incertain:
        return {'etat': 'couverture_temporelle_a_verifier', 'version': None}
    uniques = {}
    for r in valides:
        cle = (r.get('version_id'), r.get('sha256'))
        if not all(isinstance(x, str) and x for x in cle):
            raise Stop('version_source_incomplete')
        uniques[cle] = r
    if len(uniques) > 1:
        return {'etat': 'versions_en_conflit', 'version': None}
    if not uniques:
        return {'etat': 'version_absente_a_cette_date', 'version': None}
    return {'etat': 'version_selectionnee', 'version': next(iter(uniques.values()))}


def verifier_lot(manifeste, nombre_lu, *, empreintes_verifiees):
    """État avant promotion (aucune promotion ni suppression ici). Un manifeste partiel ne justifie ni suppression ni abrogation ;
    l'empreinte du texte est un contrôle d'intégrité locale, pas une signature d'autorité."""
    if not isinstance(manifeste, dict):
        raise Stop('lot_invalide')
    for cle in ('snapshot_id', 'source', 'rights_ref', 'coverage'):
        if not isinstance(manifeste.get(cle), str) or not manifeste[cle]:
            raise Stop('manifeste_incomplet')
    attendu = manifeste.get('expected_count')
    if type(attendu) is not int or attendu < 0:
        raise Stop('compte_lot_invalide')
    if nombre_lu != attendu or manifeste.get('complet') is not True:
        return {'etat': 'lot_incomplet', 'promouvoir': False, 'autorise_suppression': False}
    if empreintes_verifiees is not True:
        raise Stop('empreinte_lot_incorrecte')
    return {'etat': 'lot_verifie_avant_indexation', 'promouvoir': False, 'autorise_suppression': False, 'nombre': nombre_lu,
            'reste': ['indexation', 'publication_atomique']}


# ======================================================================================== lecture du dossier fourni par le cabinet
def _fichier(dossier, nom, maximum, motif):
    p = dossier / nom
    try:
        st = os.lstat(p)
    except OSError:
        raise Stop(motif) from None
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):        # lien symbolique, jonction, dossier : refusé
        raise Stop(motif)
    if st.st_size > maximum:
        raise Stop('lot_trop_volumineux')
    return p, st


def _dossier_import(chemin):
    if not isinstance(chemin, str) or not chemin.strip() or len(chemin) > 1000 or any(ord(c) < 32 for c in chemin):
        raise Stop('dossier_import_invalide')
    p = Path(chemin.strip())
    if not p.is_absolute():
        raise Stop('dossier_import_invalide')
    try:
        if p.is_symlink() or not p.is_dir() or os.path.realpath(p) != os.path.realpath(os.path.abspath(p)):
            raise Stop('dossier_import_invalide')
    except OSError:
        raise Stop('dossier_import_invalide') from None
    return p


def _hacher(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:                 # documents.jsonl : texte JSON, jamais une base SQLite (nom imposé)
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def lire_manifeste(chemin):
    """Manifeste normalisé d'un dossier d'import (aucune écriture, aucun réseau)."""
    dossier = _dossier_import(chemin)
    mp, _ = _fichier(dossier, MANIFESTE, MAX_MANIFESTE, 'manifeste_absent')
    dp, dst = _fichier(dossier, DOCUMENTS, MAX_DOCUMENTS, 'documents_absents')
    raw = mp.read_bytes()
    try:
        m = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise Stop('manifeste_illisible') from None
    if not isinstance(m, dict):
        raise Stop('manifeste_illisible')
    for cle in ('snapshot_id', 'source', 'rights_ref', 'coverage', 'pays'):
        if not isinstance(m.get(cle), str) or not m[cle].strip() or len(m[cle]) > 200:
            raise Stop('manifeste_incomplet')
    if type(m.get('expected_count')) is not int or m['expected_count'] < 0 or type(m.get('complet')) is not bool:
        raise Stop('manifeste_incomplet')
    if not _PAYS.match(m['pays']):
        raise Stop('manifeste_incomplet')
    corpus_au = m.get('corpus_au') or ''
    if corpus_au:
        _jour(corpus_au)
    domaines = [str(d).lower() for d in (m.get('domaines_officiels') or []) if isinstance(d, str) and _HOTE.match(str(d).lower())][:20]
    fonds = re.sub(r'[^a-z0-9_.-]+', '-', str(m.get('fonds') or '%s-%s-%s' % (m['source'], m['pays'], m.get('matiere') or 'general')).lower())[:80]
    return {'dossier': dossier, 'manifeste_sha256': _sha(raw), 'documents': dp, 'documents_taille': dst.st_size,
            'documents_mtime': int(getattr(dst, 'st_mtime_ns', 0) or dst.st_mtime * 1e9),
            'fonds': fonds.strip('-') or 'fonds', 'snapshot_id': m['snapshot_id'].strip(), 'source': m['source'].strip(), 'pays': m['pays'],
            'langue': str(m.get('langue') or '')[:5], 'matiere': str(m.get('matiere') or '')[:80], 'autorite': str(m.get('autorite') or '')[:200],
            'rights_ref': m['rights_ref'].strip(), 'licence': str(m.get('licence') or '')[:200], 'licence_verifiee': m.get('licence_verifiee') is True,
            'coverage': m['coverage'].strip(), 'expected_count': m['expected_count'], 'complet': m['complet'], 'corpus_au': corpus_au,
            'domaines': domaines}


def _document(raw, lot):
    """Document du lot contrôlé contre le contrat (aucune donnée prise pour une consigne ; le texte reste une donnée)."""
    if not isinstance(raw, dict):
        raise Stop('document_lot_invalide')
    d = {}
    for cle in ('official_id', 'version_id', 'jurisdiction', 'language', 'text', 'sha256', 'kind'):
        if not isinstance(raw.get(cle), str) or not raw[cle]:
            raise Stop('document_lot_incomplet')
    if not _ID.match(raw['official_id']) or not _ID.match(raw['version_id']) or raw['kind'] not in NATURES:
        raise Stop('document_lot_invalide')
    if not _PAYS.match(raw['jurisdiction']) or not _LANGUE.match(raw['language']) or not _SHA.match(raw['sha256']):
        raise Stop('document_lot_invalide')
    if _sha(raw['text']) != raw['sha256']:
        raise Stop('empreinte_lot_incorrecte')
    for cle in ('valid_from', 'valid_to_exclusive', 'decision_date', 'publication_date'):
        v = raw.get(cle) or ''
        if v:
            _jour(v)
        d[cle] = v
    if d['valid_from'] and d['valid_to_exclusive'] and d['valid_to_exclusive'] <= d['valid_from']:
        raise Stop('intervalle_source_invalide')
    url = str(raw.get('source_url') or '')
    if url and not url.startswith('https://'):
        url = ''
    d.update({k: raw[k] for k in ('official_id', 'version_id', 'jurisdiction', 'language', 'sha256', 'kind')})
    d.update({'texte': raw['text'], 'intervalle_verifie': 1 if raw.get('intervalle_verifie') is True else 0,
              'titre': str(raw.get('title') or '')[:300], 'autorite': str(raw.get('autorite') or lot['autorite'] or '')[:200], 'source_url': url[:500],
              'retrieved_at': str(raw.get('retrieved_at') or '')[:40], 'source_updated_at': str(raw.get('source_updated_at') or '')[:40],
              'rights_ref': str(raw.get('rights_ref') or lot['rights_ref'])[:200],
              'eli': str(raw.get('eli') or '')[:300] if raw.get('eli') else '', 'ecli': str(raw.get('ecli') or '')[:120] if raw.get('ecli') else ''})
    return d


# ======================================================================================== lots : import, vérification, indexation, publication
def _lot(desk, lot_id):
    row = desk.db.execute('SELECT * FROM fonds56109_lots WHERE id=?', (str(lot_id or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return dict(row)


def _racine(desk):
    return Path(desk.c['state_dir']) / RACINE


def _chemin_index(desk, index_rel):
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', str(index_rel or '')):
        return None
    return _racine(desk) / index_rel / 'documents.sqlite3'


def _index(desk, index_rel):
    from .index import DocumentIndex
    fichier = _chemin_index(desk, index_rel)
    if fichier is None:
        raise Stop('index_absent')
    fichier.parent.mkdir(parents=True, exist_ok=True)
    return DocumentIndex(desk.c['state_dir'], rag={}, ollama={}, fichier=fichier)   # plein texte seul : aucun appel de modèle ni réseau


def _index_present(desk, index_rel):
    fichier = _chemin_index(desk, index_rel)
    return bool(fichier is not None and fichier.is_file())


def _partition(lot_id):
    return 'fonds56109:' + lot_id


def _chemin_doc(d):
    return 'fonds56109://%s/%s/%s/%s' % (d['jurisdiction'], d['official_id'], d['language'], d['version_id'])


def _vue_lot(r):
    return {'id': r['id'], 'fonds': r['fonds'], 'snapshot_id': r['snapshot_id'], 'source': r['source'], 'pays': r['pays'], 'langue': r['langue'],
            'matiere': r['matiere'], 'couverture': r['coverage'], 'attendus': r['expected_count'], 'complet': bool(r['complet']),
            'corpus_au': r['corpus_au'], 'licence': r['licence'], 'licence_verifiee': bool(r['licence_verifiee']), 'etat': r['etat'],
            'libelle': LIBELLES_LOT.get(r['etat'], r['etat']), 'importes': r['importes'], 'indexes': r['indexes'], 'motif': r['motif'],
            'publie_le': r['publie_le'], 'maj_le': r['maj_le'], 'version': r['version']}


def _admin(acteur):
    if acteur[1] != 'administrateur':
        raise Stop('role_insuffisant')


def _recu(desk, acteur, action, cle, empreinte):
    if not CLE.match(str(cle or '')):
        raise Stop('cle_requise')
    row = desk.db.execute('SELECT * FROM fonds56109_ops WHERE cle=?', (cle,)).fetchone()
    if row:
        if row['empreinte'] != empreinte or row['action'] != action or row['compte'] != acteur[0]:
            raise Stop('cle_reutilisee')
        return json.loads(row['resultat'])
    return None


def _noter(desk, acteur, action, cle, empreinte, resultat):
    desk.db.execute('INSERT OR IGNORE INTO fonds56109_ops VALUES(?,?,?,?,?,?)',
                    (cle, acteur[0], action, empreinte, json.dumps(resultat, ensure_ascii=False, default=str), _now()))
    desk.db.commit()


def importer(desk, acteur, chemin, cle, pas=PAS):
    """Enregistre un lot lu depuis un dossier local fourni par le cabinet, puis avance d'un pas borné. Même lot → même identifiant."""
    _admin(acteur)
    empreinte = _sha(json.dumps(['importer', chemin], ensure_ascii=False))
    deja = _recu(desk, acteur, 'importer', cle, empreinte)
    if deja is not None:
        return deja
    m = lire_manifeste(chemin)
    documents_sha = _hacher(m['documents'])
    lot_id = 'L' + _sha('%s|%s|%s|%s' % (m['fonds'], m['snapshot_id'], m['manifeste_sha256'], documents_sha))[:23]
    if not desk.db.execute('SELECT 1 FROM fonds56109_lots WHERE id=?', (lot_id,)).fetchone():
        stamp = _now()
        desk.db.execute('INSERT INTO fonds56109_lots(id,fonds,snapshot_id,source,pays,langue,matiere,autorite,rights_ref,licence,licence_verifiee,'
                        'coverage,expected_count,complet,corpus_au,domaines,chemin_import,manifeste_sha256,documents_sha256,documents_taille,'
                        'documents_mtime,etat,cree_par,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (lot_id, m['fonds'], m['snapshot_id'], m['source'], m['pays'], m['langue'], m['matiere'], m['autorite'], m['rights_ref'],
                         m['licence'], int(m['licence_verifiee']), m['coverage'], m['expected_count'], int(m['complet']), m['corpus_au'],
                         json.dumps(m['domaines']), str(m['dossier']), m['manifeste_sha256'], documents_sha, m['documents_taille'],
                         m['documents_mtime'], 'preparation', acteur[0][:120], stamp, stamp))
        desk.db.commit()
        desk.audit('fonds56109_import', {'lot': lot_id, 'fonds': m['fonds'], 'snapshot': m['snapshot_id'][:80]})
    resultat = {**avancer(desk, acteur, lot_id, pas=pas), 'lot': lot_id}
    _noter(desk, acteur, 'importer', cle, empreinte, resultat)
    return resultat


def _rejeter(desk, lot_id, etat, motif):
    desk.db.execute('UPDATE fonds56109_lots SET etat=?, motif=?, maj_le=?, version=version+1 WHERE id=?', (etat, motif[:120], _now(), lot_id))
    desk.db.commit()


def _manifeste_du_lot(lot):
    return {'snapshot_id': lot['snapshot_id'], 'source': lot['source'], 'rights_ref': lot['rights_ref'], 'coverage': lot['coverage'],
            'expected_count': lot['expected_count'], 'complet': bool(lot['complet'])}


def _lire_pas(desk, lot, pas, checkpoint):
    """Lit au plus ``pas`` documents depuis le curseur durable (octet, rang) ; chaque pas est validé en une transaction."""
    dp = Path(lot['chemin_import']) / DOCUMENTS
    try:
        st = os.lstat(dp)
    except OSError:
        raise Stop('lot_modifie_pendant_import') from None
    if st.st_size != lot['documents_taille'] or int(getattr(st, 'st_mtime_ns', 0) or st.st_mtime * 1e9) != lot['documents_mtime']:
        raise Stop('lot_modifie_pendant_import')
    lus, fin = [], False
    with open(dp, 'rb') as f:
        f.seek(lot['octet'])
        octet = lot['octet']
        while len(lus) < pas:
            ligne = f.readline(MAX_LIGNE + 1)
            if not ligne:
                fin = True
                break
            if len(ligne) > MAX_LIGNE:
                raise Stop('document_lot_trop_long')
            octet += len(ligne)
            if not ligne.strip():
                continue
            try:
                raw = json.loads(ligne.decode('utf-8-sig'))
            except (ValueError, UnicodeError):
                raise Stop('document_lot_illisible') from None
            lus.append(_document(raw, lot))
        else:
            fin = not f.read(1)
    checkpoint(desk)                                  # pause ou arrêt : rien n'est écrit, le curseur reste où il était
    n = lot['curseur']
    try:
        for d in lus:
            n += 1
            desk.db.execute('INSERT INTO fonds56109_documents(lot,n,official_id,kind,jurisdiction,language,version_id,valid_from,valid_to_exclusive,'
                            'intervalle_verifie,decision_date,publication_date,titre,autorite,source_url,retrieved_at,source_updated_at,rights_ref,'
                            'eli,ecli,sha256,texte) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (lot['id'], n, d['official_id'], d['kind'], d['jurisdiction'], d['language'], d['version_id'], d['valid_from'],
                             d['valid_to_exclusive'], d['intervalle_verifie'], d['decision_date'], d['publication_date'], d['titre'], d['autorite'],
                             d['source_url'], d['retrieved_at'], d['source_updated_at'], d['rights_ref'], d['eli'], d['ecli'], d['sha256'], d['texte']))
        desk.db.execute('UPDATE fonds56109_lots SET curseur=?, octet=?, importes=importes+?, maj_le=? WHERE id=?',
                        (n, octet, len(lus), _now(), lot['id']))
        desk.db.commit()
    except sqlite3.IntegrityError:
        desk.db.rollback()
        raise Stop('version_dupliquee_dans_lot') from None
    except BaseException:
        desk.db.rollback()
        raise
    return fin


def _indexer_pas(desk, lot, pas, checkpoint):
    index = _index(desk, lot['index_rel'])
    try:
        rows = desk.db.execute('SELECT * FROM fonds56109_documents WHERE lot=? AND n>? ORDER BY n LIMIT ?', (lot['id'], lot['indexes'], pas)).fetchall()
        dernier = lot['indexes']
        for r in rows:
            checkpoint(desk)
            meta = {k: r[k] for k in ('official_id', 'kind', 'jurisdiction', 'language', 'version_id', 'valid_from', 'valid_to_exclusive',
                                      'decision_date', 'publication_date', 'titre', 'autorite', 'source_url', 'rights_ref')}
            meta.update({'confidentiality': 'public', 'source': 'fonds_juridique_local', 'lot': lot['id']})
            index.put_source(_partition(lot['id']), _chemin_doc(r), (r['titre'] + '\n' if r['titre'] else '') + r['texte'], r['sha256'],
                             r['valid_from'] or r['decision_date'], r['kind'], meta)
            dernier = r['n']
            desk.db.execute('UPDATE fonds56109_lots SET indexes=?, maj_le=? WHERE id=?', (dernier, _now(), lot['id']))
            desk.db.commit()
        fini = not desk.db.execute('SELECT 1 FROM fonds56109_documents WHERE lot=? AND n>? LIMIT 1', (lot['id'], dernier)).fetchone()
        sources = index.db.execute('SELECT COUNT(DISTINCT source_id) FROM knowledge_chunks WHERE matter=?', (_partition(lot['id']),)).fetchone()[0]
        return fini, sources
    finally:
        index.db.close()


def _publier(desk, lot):
    """Bascule atomique : une seule transaction désigne le nouveau lot actif et garde l'actif précédent. Rien n'est écrasé."""
    stamp = _now()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        actif = desk.db.execute('SELECT * FROM fonds56109_actifs WHERE fonds=?', (lot['fonds'],)).fetchone()
        precedent = actif['lot_actif'] if actif else ''
        if actif and actif['lot_precedent'] and actif['lot_precedent'] != lot['id']:
            desk.db.execute("UPDATE fonds56109_lots SET etat='remplace', maj_le=?, version=version+1 WHERE id=?", (stamp, actif['lot_precedent']))
        if precedent and precedent != lot['id']:
            desk.db.execute("UPDATE fonds56109_lots SET etat='precedent', maj_le=?, version=version+1 WHERE id=?", (stamp, precedent))
        desk.db.execute('INSERT INTO fonds56109_actifs(fonds,lot_actif,lot_precedent,revision,publie_le) VALUES(?,?,?,1,?) '
                        'ON CONFLICT(fonds) DO UPDATE SET lot_actif=excluded.lot_actif, lot_precedent=excluded.lot_precedent, '
                        'revision=fonds56109_actifs.revision+1, publie_le=excluded.publie_le',
                        (lot['fonds'], lot['id'], precedent if precedent != lot['id'] else '', stamp))
        desk.db.execute("UPDATE fonds56109_lots SET etat='actif', publie_le=?, maj_le=?, motif='', version=version+1 WHERE id=?",
                        (stamp, stamp, lot['id']))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('fonds56109_publication', {'lot': lot['id'], 'fonds': lot['fonds'], 'precedent': precedent})
    _nettoyer(desk, lot['fonds'])


def _nettoyer(desk, fonds):
    """Index des lots remplacés retirés (ni actif, ni précédent) ; un fichier encore ouvert ailleurs est laissé pour un prochain passage."""
    for r in desk.db.execute("SELECT id,index_rel FROM fonds56109_lots WHERE fonds=? AND etat IN ('remplace','rejete','incomplet','licence_a_qualifier') AND index_rel<>''",
                             (fonds,)).fetchall():
        fichier = _chemin_index(desk, r['index_rel'])
        desk.db.execute("UPDATE fonds56109_lots SET index_rel='' WHERE id=?", (r['id'],))
        desk.db.commit()
        if fichier is not None:
            shutil.rmtree(fichier.parent, ignore_errors=True)


class _Pause(Stop):
    pass


def _controle(checkpoint=None):
    """Contrôle commun (``pause5629.checkpoint``) avant chaque effet ; son refus est une pause, jamais un lot refusé."""
    from .pause5629 import checkpoint as commun
    appel = checkpoint or commun

    def controler(desk):
        try:
            appel(desk)
        except Stop as ex:
            raise _Pause(str(ex)) from None
    return controler


def avancer(desk, acteur, lot_id, pas=PAS, version=None, checkpoint=None):
    """Un pas borné de l'import : lecture → vérification du lot → indexation dans un nouveau fichier → publication atomique. Reprenable."""
    _admin(acteur)
    checkpoint = _controle(checkpoint)
    lot = _lot(desk, lot_id)
    if version is not None and int(version) != lot['version']:
        raise Stop('version_perimee')
    if lot['etat'] == 'suspendu':
        raise Stop('lot_suspendu')
    pas = max(1, min(int(pas or PAS), 2000))
    try:
        if lot['etat'] == 'preparation':
            fin = _lire_pas(desk, lot, pas, checkpoint)
            lot = _lot(desk, lot_id)
            if fin:
                if _hacher(Path(lot['chemin_import']) / DOCUMENTS) != lot['documents_sha256']:
                    raise Stop('lot_modifie_pendant_import')
                bilan = verifier_lot(_manifeste_du_lot(lot), lot['importes'], empreintes_verifiees=True)
                if bilan['etat'] == 'lot_incomplet':      # jamais promu ; rien n'est supprimé ni abrogé dans l'actif
                    _rejeter(desk, lot_id, 'incomplet', 'lot_incomplet')
                else:
                    desk.db.execute("UPDATE fonds56109_lots SET etat='indexation', index_rel=?, maj_le=?, version=version+1 WHERE id=?",
                                    ('%s-%s' % (lot_id, uuid.uuid4().hex[:10]), _now(), lot_id))
                    desk.db.commit()
            return _vue_lot(_lot(desk, lot_id))
        if lot['etat'] == 'indexation':
            fini, sources = _indexer_pas(desk, lot, pas, checkpoint)
            lot = _lot(desk, lot_id)
            if fini:
                if sources != lot['importes']:
                    raise Stop('index_incomplet')
                if not lot['licence_verifiee']:
                    _rejeter(desk, lot_id, 'licence_a_qualifier', 'licence_non_qualifiee')
                else:
                    _publier(desk, lot)
            return _vue_lot(_lot(desk, lot_id))
        return _vue_lot(lot)
    except _Pause:
        raise Stop('operation_en_pause') from None   # pause ou arrêt : rien d'écrit pour ce pas, le curseur reste, l'actif est intact
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('version_perimee', 'lot_suspendu', 'role_insuffisant', 'introuvable'):
            raise
        _rejeter(desk, lot_id, 'rejete', code)       # lot refusé : l'actif reste celui d'avant, intact
        return _vue_lot(_lot(desk, lot_id))


def suspendre(desk, acteur, lot_id, version=None):
    _admin(acteur)
    lot = _lot(desk, lot_id)
    if version is not None and int(version) != lot['version']:
        raise Stop('version_perimee')
    if lot['etat'] in ('preparation', 'indexation'):
        desk.db.execute("UPDATE fonds56109_lots SET etat='suspendu', etat_avant=?, maj_le=?, version=version+1 WHERE id=?", (lot['etat'], _now(), lot_id))
        desk.db.commit()
    return _vue_lot(_lot(desk, lot_id))


def reprendre(desk, acteur, lot_id, version=None, pas=PAS):
    _admin(acteur)
    lot = _lot(desk, lot_id)
    if version is not None and int(version) != lot['version']:
        raise Stop('version_perimee')
    if lot['etat'] == 'suspendu':
        desk.db.execute('UPDATE fonds56109_lots SET etat=?, maj_le=?, version=version+1 WHERE id=?', (lot['etat_avant'] or 'preparation', _now(), lot_id))
        desk.db.commit()
    return avancer(desk, acteur, lot_id, pas=pas)


def supprimer_index(desk, acteur, fonds):
    """Retire l'index (fichiers recalculables) des lots actif et précédent d'un fonds ; les textes, versions et références restent."""
    _admin(acteur)
    rows = desk.db.execute("SELECT id,index_rel FROM fonds56109_lots WHERE fonds=? AND index_rel<>''", (str(fonds or ''),)).fetchall()
    if not rows and not desk.db.execute('SELECT 1 FROM fonds56109_actifs WHERE fonds=?', (str(fonds or ''),)).fetchone():
        raise Stop('introuvable')
    for r in rows:
        desk.db.execute("UPDATE fonds56109_lots SET index_rel='', maj_le=? WHERE id=?", (_now(), r['id']))
        desk.db.commit()                              # désignation retirée d'abord : aucune recherche n'ouvre un fichier en cours de retrait
        fichier = _chemin_index(desk, r['index_rel'])
        if fichier is not None:
            shutil.rmtree(fichier.parent, ignore_errors=True)
    return etat(desk)


def reconstruire(desk, acteur, fonds, checkpoint=None):
    """Refait l'index du lot actif dans un NOUVEAU fichier depuis les textes conservés, puis le désigne en une mise à jour (restauration)."""
    _admin(acteur)
    checkpoint = _controle(checkpoint)
    actif = desk.db.execute('SELECT lot_actif FROM fonds56109_actifs WHERE fonds=?', (str(fonds or ''),)).fetchone()
    if not actif:
        raise Stop('introuvable')
    lot = _lot(desk, actif['lot_actif'])
    nouveau = '%s-%s' % (lot['id'], uuid.uuid4().hex[:10])
    index = _index(desk, nouveau)
    try:
        for r in desk.db.execute('SELECT * FROM fonds56109_documents WHERE lot=? ORDER BY n', (lot['id'],)).fetchall():
            checkpoint(desk)
            meta = {k: r[k] for k in ('official_id', 'kind', 'jurisdiction', 'language', 'version_id', 'valid_from', 'valid_to_exclusive',
                                      'decision_date', 'publication_date', 'titre', 'autorite', 'source_url', 'rights_ref')}
            meta.update({'confidentiality': 'public', 'source': 'fonds_juridique_local', 'lot': lot['id']})
            index.put_source(_partition(lot['id']), _chemin_doc(r), (r['titre'] + '\n' if r['titre'] else '') + r['texte'], r['sha256'],
                             r['valid_from'] or r['decision_date'], r['kind'], meta)
        sources = index.db.execute('SELECT COUNT(DISTINCT source_id) FROM knowledge_chunks WHERE matter=?', (_partition(lot['id']),)).fetchone()[0]
    except _Pause:
        index.db.close()
        shutil.rmtree(_chemin_index(desk, nouveau).parent, ignore_errors=True)   # nouveau fichier abandonné ; l'index désigné reste
        raise Stop('operation_en_pause') from None
    finally:
        index.db.close()
    if sources != lot['importes']:
        shutil.rmtree(_chemin_index(desk, nouveau).parent, ignore_errors=True)
        raise Stop('index_incomplet')
    ancien = lot['index_rel']
    desk.db.execute('UPDATE fonds56109_lots SET index_rel=?, indexes=importes, maj_le=?, version=version+1 WHERE id=?', (nouveau, _now(), lot['id']))
    desk.db.commit()
    if ancien:
        fichier = _chemin_index(desk, ancien)
        if fichier is not None:
            shutil.rmtree(fichier.parent, ignore_errors=True)
    return etat(desk)


# ======================================================================================== recherche sans réseau, citations
def _actifs(desk, pays='', langue='', matiere=''):
    rows = desk.db.execute('SELECT l.* FROM fonds56109_actifs a JOIN fonds56109_lots l ON l.id=a.lot_actif ORDER BY a.fonds').fetchall()
    out = []
    for r in rows:
        if pays and r['pays'] != pays and not str(r['pays']).startswith(pays + '-'):
            continue
        if langue and r['langue'] and r['langue'] != langue:
            continue
        if matiere and r['matiere'] and r['matiere'] != matiere:
            continue
        out.append(dict(r))
    return out


def _doc_par_chemin(desk, lot_id, chemin):
    m = re.fullmatch(r'fonds56109://([^/]+)/(.+)/([a-z]{2})/([^/]+)', str(chemin or ''))
    if not m:
        return None
    row = desk.db.execute('SELECT * FROM fonds56109_documents WHERE lot=? AND jurisdiction=? AND official_id=? AND language=? AND version_id=?',
                          (lot_id, m.group(1), m.group(2), m.group(3), m.group(4))).fetchone()
    return dict(row) if row else None


def corpus_message(lots):
    dates = sorted({l['corpus_au'] for l in lots if l.get('corpus_au')})
    if not lots:
        return 'Aucun fonds juridique local disponible.'
    if not dates:
        return 'Corpus local : date d’arrêt non déclarée par le lot (fraîcheur inconnue).'
    return 'Corpus arrêté au %s.' % ', '.join(dates)


def rechercher(desk, question, pays='', langue='', date_utile='', limite=8, matiere=''):
    """Recherche plein texte dans les lots ACTIFS du fonds, filtrés par pays, langue et matière AVANT le classement ; aucune pièce privée,
    aucun réseau, aucun modèle. ``date_utile`` : pour un article, seule la version en vigueur à cette date (prouvée) est rendue."""
    question = str(question or '').strip()[:2000]
    if not question:
        raise Stop('question_requise')
    if date_utile:
        _jour(date_utile)
    limite = max(1, min(int(limite or 8), 30))
    from .recherche5695 import _extrait, _mots
    lots = _actifs(desk, str(pays or '').upper(), str(langue or '').lower(), str(matiere or ''))
    etat = {'corpus': 'fonds_public', 'reseau': False, 'a_jour': False, 'recherche_exhaustive': False, 'constats': [], 'index_absents': [],
            'corpus_au': {l['fonds']: l['corpus_au'] for l in lots}, 'message': corpus_message(lots)}
    if not lots:
        etat['etat'] = 'aucun_fonds_local'
        return [], etat
    trouves, vus = [], set()
    for lot in lots:
        if not _index_present(desk, lot['index_rel']):
            etat['index_absents'].append(lot['fonds'])
            continue
        index = _index(desk, lot['index_rel'])
        try:
            trouvees = index.lexical_keys(_partition(lot['id']), [question])
            ranked, _err = index.ranked_chunks(_partition(lot['id']), [question], limit=limite * 3) if trouvees else ([], '')
        finally:
            index.db.close()
        for row, score in ranked:
            if (row[1], row[6]) not in trouvees:               # passage sans rapport avec la question : jamais rendu
                continue
            d = _doc_par_chemin(desk, lot['id'], row[2])
            if d is None:
                continue
            if date_utile and d['kind'] == 'article':
                versions = [dict(x) for x in desk.db.execute('SELECT * FROM fonds56109_documents WHERE lot=? AND official_id=? AND jurisdiction=? '
                                                               'AND language=?', (lot['id'], d['official_id'], d['jurisdiction'], d['language']))]
                choix = choisir_article(versions, official_id=d['official_id'], jurisdiction=d['jurisdiction'], language=d['language'],
                                        date_utile=date_utile)
                if choix['etat'] != 'version_selectionnee':
                    cle = (d['official_id'], choix['etat'])
                    if cle not in vus:
                        vus.add(cle)
                        etat['constats'].append({'official_id': d['official_id'], 'etat': choix['etat']})
                    continue
                if choix['version']['version_id'] != d['version_id']:
                    d = choix['version']           # la version en vigueur à la date demandée, pas celle qui a le mieux répondu
            cle = (lot['id'], d['official_id'], d['language'], d['version_id'])
            if cle in vus:
                continue
            vus.add(cle)
            posterieure = bool(date_utile and d['kind'] == 'decision' and d['decision_date'] and d['decision_date'] > date_utile)
            trouves.append({'lot': lot['id'], 'fonds': lot['fonds'], 'official_id': d['official_id'], 'version_id': d['version_id'],
                            'nature': d['kind'], 'titre': d['titre'], 'juridiction': d['jurisdiction'], 'langue': d['language'],
                            'valid_from': d['valid_from'], 'valid_to_exclusive': d['valid_to_exclusive'], 'intervalle_verifie': bool(d['intervalle_verifie']),
                            'decision_date': d['decision_date'], 'publication_date': d['publication_date'], 'autorite': d['autorite'],
                            'source_url': d['source_url'], 'ecli': d['ecli'], 'eli': d['eli'],
                            'extrait': _extrait(row[7] if row[2] == _chemin_doc(d) else d['texte'], _mots(question), 1200),
                            'localisateur': {'type': 'section_index', 'numero': int(row[6]) + 1}, 'score': score, 'corpus_au': lot['corpus_au'],
                            'statut': 'texte_officiel_local', 'posterieure_a_la_date': posterieure})
            if len(trouves) >= limite:
                break
    etat['etat'] = 'resultats' if trouves else ('index_a_reconstruire' if etat['index_absents'] and len(etat['index_absents']) == len(lots)
                                                else 'aucun_passage_dans_le_fonds_consulte')
    return trouves[:limite], etat


def document(desk, lot_id, official_id, version_id, langue=''):
    """Texte officiel d'une version conservée (lot actif ou précédent seulement : l'instantané cité reste lisible)."""
    lot = _lot(desk, lot_id)
    if lot['etat'] not in ('actif', 'precedent'):
        raise Stop('introuvable')
    sql = 'SELECT * FROM fonds56109_documents WHERE lot=? AND official_id=? AND version_id=?' + (' AND language=?' if langue else '')
    row = desk.db.execute(sql, (lot_id, str(official_id or ''), str(version_id or '')) + ((langue,) if langue else ())).fetchone()
    if not row:
        raise Stop('introuvable')
    d = dict(row)
    return {**{k: d[k] for k in d if k not in ('n',)}, 'corpus_au': lot['corpus_au'], 'lot_etat': lot['etat']}


def verifier_citation(desk, lot_id, official_id, version_id, passage, langue=''):
    """Citation relue contre le texte officiel conservé de CETTE version avant d'être dite vérifiée. URL officielle qualifiée par le lot
    (domaines déclarés) requise ; sinon « à vérifier ». Ne dit rien de l'applicabilité de la règle aux faits."""
    from .qualite_sources56109 import citation_verifiee, passage_relu
    try:
        d = document(desk, lot_id, official_id, version_id, langue)
    except Stop:
        return {'etat': 'a_verifier', 'motif': 'source_introuvable'}
    if not passage_relu(passage, d['texte']):
        return {'etat': 'a_verifier', 'motif': 'passage_non_retrouve'}
    domaines = set(json.loads(_lot(desk, lot_id)['domaines'] or '[]'))
    try:
        out = citation_verifiee({'source_id': d['official_id'], 'source_url': d['source_url'], 'passage': passage, 'version': d['version_id'],
                                 'eli_uri': d['eli'], 'ecli': d['ecli']}, texte_original=d['texte'], domaines_autorises=domaines)
    except Stop as ex:
        return {'etat': 'a_verifier', 'motif': str(ex).split(':')[0]}
    return {**out, 'etat': 'verifiee', 'motif': '', 'corpus_au': d['corpus_au']}


# ======================================================================================== état, écran, routes
def _taille(desk, index_rel):
    fichier = _chemin_index(desk, index_rel)
    total = 0
    if fichier is not None and fichier.parent.is_dir():
        for p in fichier.parent.iterdir():
            try:
                total += os.stat(p).st_size           # métadonnées seulement : aucun fichier SQLite n'est ouvert ici
            except OSError:
                pass
    return total


def etat(desk):
    """État lisible de chaque fonds (aucune écriture) : actif, versions, couverture, corpus arrêté au, lots en cours et échecs."""
    fonds = {}
    for r in desk.db.execute('SELECT * FROM fonds56109_lots ORDER BY maj_le DESC').fetchall():
        f = fonds.setdefault(r['fonds'], {'fonds': r['fonds'], 'actif': None, 'precedent': None, 'en_cours': [], 'echecs': [], 'lots': 0})
        f['lots'] += 1
        v = _vue_lot(r)
        if r['etat'] == 'actif':
            f['actif'] = {**v, 'index_present': _index_present(desk, r['index_rel']), 'taille_octets': _taille(desk, r['index_rel']),
                          'versions': desk.db.execute('SELECT COUNT(*) FROM fonds56109_documents WHERE lot=?', (r['id'],)).fetchone()[0]}
        elif r['etat'] == 'precedent':
            f['precedent'] = v
        elif r['etat'] in ('preparation', 'indexation', 'suspendu'):
            f['en_cours'].append(v)
        elif r['etat'] in ('rejete', 'incomplet', 'licence_a_qualifier') and len(f['echecs']) < 3:
            f['echecs'].append(v)
    out = []
    for f in fonds.values():
        a = f['actif']
        if f['en_cours'] and f['en_cours'][0]['etat'] == 'suspendu':
            cle = 'suspendu'
        elif f['en_cours']:
            cle = 'import'
        elif a and not a['index_present']:
            cle = 'index'
        elif f['echecs'] and (not a or f['echecs'][0]['maj_le'] > a['maj_le']):
            cle = 'licence' if f['echecs'][0]['etat'] == 'licence_a_qualifier' else 'echec'
        elif a:
            cle = 'disponible' if a['couverture'] in ('complete', 'complet', 'integral') else 'partiel'
        else:
            cle = 'aucun'
        out.append({**f, 'etat': cle, 'libelle': ETATS[cle], 'message': corpus_message([a] if a else [])})
    return {'fonds': out, 'etat': 'aucun' if not out else 'present', 'libelle': ETATS['aucun'] if not out else '', 'reseau': False,
            'telechargement': False}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES and role in ROLES
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(acteur[1], method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        args = args or {}
        if method in ('GET', 'HEAD'):
            if reste == 'etat':
                return _json(etat(desk))
            if reste == 'rechercher':
                resultats, info = rechercher(desk, args.get('q'), args.get('pays') or '', args.get('langue') or '', args.get('date') or '',
                                             args.get('limite') or 8)
                return _json({'resultats': resultats, **info})
            return _json(document(desk, args.get('lot'), args.get('id'), args.get('version'), args.get('langue') or ''))
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        cle, version = d.get('cle'), d.get('version')
        if reste == 'importer':
            return _json(importer(desk, acteur, d.get('chemin'), cle))
        if not CLE.match(str(cle or '')):
            raise Stop('cle_requise')
        empreinte = _sha(json.dumps([reste, d.get('lot'), d.get('fonds'), version], ensure_ascii=False, default=str))
        deja = _recu(desk, acteur, reste, cle, empreinte)
        if deja is not None:
            return _json(deja)
        if reste == 'avancer':
            out = avancer(desk, acteur, d.get('lot'), version=version)
        elif reste == 'suspendre':
            out = suspendre(desk, acteur, d.get('lot'), version)
        elif reste == 'reprendre':
            out = reprendre(desk, acteur, d.get('lot'), version)
        elif reste == 'supprimer-index':
            out = supprimer_index(desk, acteur, d.get('fonds'))
        else:
            out = reconstruire(desk, acteur, d.get('fonds'))
        _noter(desk, acteur, reste, cle, empreinte, out)
        return _json(out)
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée (%s).' % code)}, STATUTS.get(code, '422 Unprocessable Entity'))


def _date(v):
    return str(v or '')[:16].replace('T', ' ')


def section_html(desk, prefix='', role='administrateur'):
    """Panneau « Fonds juridique local » de Paramètres › Recherche et sources (aucune écriture, aucun réseau pendant l'affichage)."""
    from html import escape as e
    from .documents import OPTIONS56109, options_locales
    data = etat(desk)
    admin = role == 'administrateur'
    cartes = ''
    for f in data['fonds']:
        a = f['actif']
        lignes = '<li><strong>%s</strong> — %s</li>' % (e(f['libelle']), e(f['message']))
        if a:
            lignes += ('<li>Lot actif : %s · %s · %s · %d version(s) · couverture : %s · %s Ko</li><li>Dernière réussite : %s · licence : %s</li>'
                       % (e(a['snapshot_id']), e(a['source']), e(a['pays']), a['versions'], e(a['couverture']), a['taille_octets'] // 1024,
                          e(_date(a['publie_le'])), e(a['licence'] or 'non renseignée')))
        if f['precedent']:
            lignes += '<li>Lot précédent conservé : %s</li>' % e(f['precedent']['snapshot_id'])
        actions = ''
        for lot in f['en_cours']:
            lignes += '<li>%s : %s — %d sur %d lus, %d indexés</li>' % (e(lot['snapshot_id']), e(lot['libelle']), lot['importes'], lot['attendus'], lot['indexes'])
            if admin:
                bouton = 'reprendre' if lot['etat'] == 'suspendu' else 'avancer'
                actions += ('<button type="button" class="ax-btn" data-f56109="%s" data-lot="%s" data-version="%d">%s</button>'
                            % (bouton, e(lot['id'], quote=True), lot['version'], 'Reprendre' if bouton == 'reprendre' else 'Continuer la mise à jour'))
                if lot['etat'] != 'suspendu':
                    actions += ('<button type="button" class="ax-btn" data-f56109="suspendre" data-lot="%s" data-version="%d">Suspendre</button>'
                                % (e(lot['id'], quote=True), lot['version']))
        for lot in f['echecs']:
            lignes += '<li class="f56109-echec">%s : %s (%s)</li>' % (e(lot['snapshot_id']), e(lot['libelle']), e(lot['motif']))
        if admin and a:
            actions += ('<button type="button" class="ax-btn" data-f56109="reconstruire" data-fonds="%s">Reconstruire l’index</button>'
                        '<button type="button" class="ax-btn" data-f56109="supprimer-index" data-fonds="%s">Supprimer l’index reconstruit</button>'
                        % (e(f['fonds'], quote=True), e(f['fonds'], quote=True)))
        cartes += '<article class="f56109-fonds"><h3>%s</h3><ul>%s</ul><p class="f56109-actions">%s</p></article>' % (e(f['fonds']), lignes, actions)
    if not cartes:
        cartes = '<p class="ax-muted">%s</p>' % e(ETATS['aucun'])
    options = ''.join('<li>%s : %s</li>' % (e(spec['libelle']), e({'desactivee': 'désactivée', 'non_installee': 'non installée (rien n’est téléchargé)',
                                                                    'a_qualifier': 'à qualifier', 'adaptateur_non_livre': 'adaptateur non livré'}[st]))
                      for oid, spec in OPTIONS56109.items() for st in [options_locales(desk.c.get('documents') or {})[oid]])
    formulaire = ('<form class="f56109-import" data-f56109-form="importer"><label for="f56109-chemin">Dossier du lot fourni par le cabinet '
                  '(manifeste.json et documents.jsonl)</label><input id="f56109-chemin" name="chemin" type="text" autocomplete="off" required>'
                  '<button class="ax-btn" type="submit">Importer le lot</button></form>') if admin else ''
    recherche = ('<form class="f56109-recherche" data-f56109-form="rechercher"><label for="f56109-q">Rechercher dans le fonds local</label>'
                 '<input id="f56109-q" name="q" type="search" required><label for="f56109-date">Date utile (facultative)</label>'
                 '<input id="f56109-date" name="date" type="date"><button class="ax-btn" type="submit">Rechercher</button></form>'
                 '<div class="f56109-resultats" aria-live="polite"></div>')
    return ('<section class="ax-card f56109" id="fonds-juridique-local" data-prefix="%s"><h2>Fonds juridique local</h2>'
            '<p class="ax-muted">Textes publics sélectionnés par le cabinet, versionnés, séparés des pièces des dossiers. Import depuis un dossier '
            'local fourni par le cabinet : le logiciel ne télécharge rien. Un lot partiel n’est jamais publié ; le lot précédent reste actif en '
            'cas d’échec. Hors réseau, le corpus est daté : jamais « droit à jour » sans preuve.</p>%s%s%s'
            '<details><summary>Options locales de traitement (désactivées par défaut)</summary><ul>%s</ul></details>'
            '<p class="f56109-out" role="status" aria-live="polite"></p>'
            '<script defer src="%s/static/v56109-fonds.js"></script></section>') % (
        e(prefix, quote=True), cartes, formulaire, recherche, options, e(prefix, quote=True))
