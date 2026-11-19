"""5.6.108 (lot S3) : fichiers et versions entre postes — manifeste, blocs vérifiés, zone temporaire, publication après contrôle.

- Un **document** (``doc-``) a des **versions** ; chaque version est une opération ``document.version`` dont la charge est un manifeste :
  nom d'origine (métadonnée), dossier (identifiant global), taille, SHA-256 final, taille de bloc (1 Mio) et SHA-256 de chaque bloc.
  Aucune commande d'écriture sur un chemin distant : chaque poste range les octets dans SON stockage (``state_dir/sync56108``).
- Réception : « Métadonnées reçues » puis « Téléchargement en attente » (jamais un lien cassé) ; les blocs sont demandés au pair
  (``sync.v1.blob_need`` → ``sync.v1.blob_chunk``), chacun vérifié contre le manifeste avant d'être écrit dans une **zone temporaire** propre à
  la version ; après coupure, seuls les blocs absents sont redemandés. Assemblage, contrôle de la taille et de l'empreinte finale, puis
  **publication atomique** (``os.replace``) : « Fichier disponible ».
- Débit et simultanéité bornés : ``MAX_OCTETS_SESSION`` par échange, ``MAX_TRANSFERTS`` transferts simultanés sur le poste (le formulaire de
  rédaction n'attend jamais un transfert de 2 Go).
- Noms : nom de stockage sûr (caractères interdits sous Windows, noms réservés CON, PRN, AUX, NUL, COM1-9, LPT1-9, points et espaces
  finaux, longueur), différences de casse traitées (deux noms qui ne diffèrent que par la casse ne s'écrasent jamais) ; le nom d'origine reste
  une métadonnée, l'identité du document ne change pas.
- Deux modifications concurrentes d'un même document (deux Word modifiés hors ligne) : deux versions conservées, téléchargées et
  comparables ; les octets ne sont jamais fusionnés.
- Jamais de base, de journal WAL/SHM, de coffre, de cache ou de modèle par ce mécanisme : seules les versions publiées explicitement.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from .common import Stop
from . import sync56108 as S

BLOC = 1024 * 1024
MAX_FICHIER = 2 * 1024 * 1024 * 1024
MAX_BLOCS = MAX_FICHIER // BLOC
MAX_OCTETS_SESSION = 32 * 1024 * 1024
MAX_TRANSFERTS = 2
MAX_NOM = 120
MAX_CHEMIN = 240
_TRANSFERTS = threading.BoundedSemaphore(MAX_TRANSFERTS)
RESERVES = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM%d' % i for i in range(1, 10)} | {'LPT%d' % i for i in range(1, 10)}
INTERDITS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
EXCLUS = re.compile(r'(?i)(\.sqlite3?(-wal|-shm|-journal)?|\.db|\.secret|\.key|\.lock|\.gguf|\.safetensors)$')
ETATS = {'metadonnees': 'Métadonnées reçues', 'telechargement': 'Téléchargement en attente', 'disponible': 'Fichier disponible',
         'erreur': 'Fichier à redemander'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sync56108_documents(objet TEXT NOT NULL, version TEXT NOT NULL, espace TEXT NOT NULL, nom TEXT NOT NULL,
  nom_stockage TEXT NOT NULL DEFAULT '', dossier TEXT NOT NULL DEFAULT '', taille INTEGER NOT NULL, sha256 TEXT NOT NULL, bloc INTEGER NOT NULL,
  blocs TEXT NOT NULL, etat TEXT NOT NULL DEFAULT 'metadonnees', chemin TEXT NOT NULL DEFAULT '', supprime INTEGER NOT NULL DEFAULT 0,
  motif TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL, PRIMARY KEY(objet, version));
CREATE INDEX IF NOT EXISTS sync56108_documents_sha ON sync56108_documents(sha256);
'''


def ensure_schema(desk):
    if not S.schema_complet(desk.db, SCHEMA):   # sans changement : aucune écriture au démarrage
        desk.db.executescript(SCHEMA)
    desk.db.commit()
    ensure_schema_s5(desk)
    inscrire()   # 5.6.108 (S5) : source « Postes du cabinet » de la page « Fichiers » (ouverte seulement quand un espace existe)


def _racine(desk):
    d = Path(desk.c['state_dir']) / 'sync56108'
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _blob(desk, sha):
    if not re.fullmatch(r'[0-9a-f]{64}', str(sha or '')):
        raise Stop('bloc_absent')
    d = _racine(desk) / 'blobs'
    d.mkdir(exist_ok=True, mode=0o700)
    return d / sha


def _partiel(desk, sha):
    d = _racine(desk) / 'partiel' / sha
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


# ------------------------------------------------------------------------------------------------ noms sûrs
def nom_sur(nom):
    """Nom de stockage valable sous Windows et Ubuntu (l'original reste une métadonnée)."""
    nom = INTERDITS.sub('_', str(nom or '')).strip().rstrip('. ')
    base, point, ext = nom.rpartition('.')
    if not point:
        base, ext = nom, ''
    ext = ext[:16]
    if base.split('.')[0].upper() in RESERVES:
        base = '_' + base
    base = base[:MAX_NOM - len(ext) - 1].rstrip('. ') or 'document'
    return base + ('.' + ext if ext else '')


def _place(dossier_dir, nom, objet):
    """Nom libre dans le répertoire : jamais un autre document écrasé, même si les noms ne diffèrent que par la casse ; chemin borné."""
    base, point, ext = nom.rpartition('.')
    if not point:
        base, ext = nom, ''
    marge = MAX_CHEMIN - len(str(dossier_dir)) - 1
    if marge < 24:
        raise Stop('chemin_trop_long')
    base = base[:max(8, marge - len(ext) - 8)]
    pris = {}
    try:
        for p in dossier_dir.iterdir():
            pris[p.name.casefold()] = p.name
    except OSError:
        pass
    candidat, n = base + ('.' + ext if ext else ''), 2
    while candidat.casefold() in pris and not _meme_objet(dossier_dir / pris[candidat.casefold()], objet):
        candidat = '%s (%d)%s' % (base, n, ('.' + ext if ext else ''))
        n += 1
    return dossier_dir / candidat


def _meme_objet(chemin, objet):
    try:
        return (chemin.with_name(chemin.name + '.axh-doc').read_text(encoding='utf-8').strip() == objet)
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------ charge (manifeste)
def valider_charge(type_, ch):
    if ch.get('supprime') is True:
        if set(ch) != {'supprime'} or type_ not in ('document.suppression', 'document.resolution'):
            raise Stop('charge_invalide')
        return ch
    if type_ == 'document.suppression' or set(ch) != {'nom', 'dossier', 'taille', 'sha256', 'bloc', 'blocs'}:
        raise Stop('charge_invalide')
    ok = isinstance(ch['nom'], str) and 1 <= len(ch['nom']) <= 255 and not re.search(r'[\x00-\x1f]', ch['nom'])
    ok &= ch['dossier'] == '' or (isinstance(ch['dossier'], str) and re.fullmatch(r'dos-[0-9a-f]{32}', ch['dossier']) is not None)
    ok &= type(ch['taille']) is int and 0 <= ch['taille'] <= MAX_FICHIER and ch['bloc'] == BLOC
    ok &= isinstance(ch['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', ch['sha256']) is not None
    ok &= isinstance(ch['blocs'], list) and len(ch['blocs']) == max(1, -(-ch['taille'] // BLOC)) and len(ch['blocs']) <= MAX_BLOCS
    ok &= all(isinstance(b, str) and re.fullmatch(r'[0-9a-f]{64}', b) for b in ch['blocs']) if isinstance(ch['blocs'], list) else False
    if not ok:
        raise Stop('charge_invalide')
    return ch


def manifeste(chemin):
    """Manifeste d'un fichier local (lecture par blocs, jamais tout en mémoire)."""
    total, h, blocs = 0, hashlib.sha256(), []
    with open(chemin, 'rb') as f:
        while True:
            b = f.read(BLOC)
            if not b and blocs:
                break
            total += len(b)
            h.update(b)
            blocs.append(hashlib.sha256(b).hexdigest())
            if len(b) < BLOC:
                break
    if total > MAX_FICHIER:
        raise Stop('fichier_trop_grand')
    return {'taille': total, 'sha256': h.hexdigest(), 'bloc': BLOC, 'blocs': blocs}


# ------------------------------------------------------------------------------------------------ publication locale (émetteur)
def publier(desk, chemin_source, nom, dossier_gid='', objet=None, par=''):
    """Publie une version d'un document (nouveau si ``objet`` absent) : octets copiés au stockage par empreinte, opération ``document.version``
    (parents : versions courantes) dans une transaction ; la version locale est aussitôt « disponible »."""
    if EXCLUS.search(str(nom or '')) or EXCLUS.search(str(chemin_source)):
        raise Stop('fichier_exclu_de_la_synchronisation')
    esp = S.espace_cabinet(desk)
    if esp is None:
        raise Stop('espace_absent')
    espace = esp['id']
    if dossier_gid:
        r = desk.db.execute("SELECT espace FROM sync56108_objets WHERE objet=? AND genre='dossier'", (dossier_gid,)).fetchone()
        if not r:
            raise Stop('introuvable')
        espace = r[0]
    if objet:
        o = desk.db.execute("SELECT espace FROM sync56108_objets WHERE objet=? AND genre='document'", (objet,)).fetchone()
        if not o:
            raise Stop('introuvable')
        espace = o[0]
    m = manifeste(chemin_source)
    cible = _blob(desk, m['sha256'])
    if not cible.is_file():
        tmp = cible.with_name(cible.name + '.' + secrets.token_hex(4) + '.tmp')
        with open(chemin_source, 'rb') as src, open(tmp, 'wb') as dst:
            while True:
                b = src.read(BLOC)
                if not b:
                    break
                dst.write(b)
        if _sha_fichier(tmp) != m['sha256']:
            tmp.unlink()
            raise Stop('fichier_modifie_pendant_la_lecture')
        os.replace(tmp, cible)
    objet = objet or S.nouvel_id('doc')
    charge = {'nom': S._nom(nom, 255) or 'document', 'dossier': dossier_gid, **m}
    with S._tx(desk):
        op = S.emettre(desk, espace, 'document.version', objet, charge)
        if not desk.db.execute('SELECT 1 FROM sync56108_objets WHERE objet=?', (objet,)).fetchone():
            desk.db.execute("INSERT INTO sync56108_objets(objet,genre,espace,local_id,projete_op,projection,maj_le) VALUES(?,?,?,?,?,'faite',?)",
                            (objet, 'document', espace, objet, op['id'], S._maintenant()))
        else:
            desk.db.execute('UPDATE sync56108_objets SET projete_op=?, conflit=?, maj_le=? WHERE objet=?',
                            (op['id'], int(len(S.tetes(desk, objet)) > 1), S._maintenant(), objet))
        _noter(desk, op)
    _publier_local(desk, objet, op['id'])
    desk.audit('sync56108_document_publie', {'objet': objet, 'taille': m['taille'], 'par': str(par)[:80]})
    return {'objet': objet, 'version': op['id'], 'sha256': m['sha256']}


def _sha_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for b in iter(lambda: f.read(BLOC), b''):
            h.update(b)
    return h.hexdigest()


def _noter(desk, op):
    ch = op['charge']
    if ch.get('supprime'):
        desk.db.execute('UPDATE sync56108_documents SET supprime=1, maj_le=? WHERE objet=?', (S._maintenant(), op['objet']))
        return
    desk.db.execute('INSERT OR IGNORE INTO sync56108_documents(objet,version,espace,nom,nom_stockage,dossier,taille,sha256,bloc,blocs,etat,maj_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (op['objet'], op['id'], op['espace'], ch['nom'], nom_sur(ch['nom']), ch['dossier'], ch['taille'],
                                                        ch['sha256'], ch['bloc'], json.dumps(ch['blocs']), 'metadonnees', S._maintenant()))
    if _blob(desk, ch['sha256']).is_file():
        desk.db.execute("UPDATE sync56108_documents SET etat='telechargement' WHERE objet=? AND version=?", (op['objet'], op['id']))
    else:
        desk.db.execute("UPDATE sync56108_documents SET etat='telechargement' WHERE objet=? AND version=? AND etat='metadonnees'", (op['objet'], op['id']))


def apres_reception(desk, op):
    """Dans la transaction de réception : la version est notée (métadonnées), le fichier part en « téléchargement en attente ».
    Rend la suite (publication si les octets sont déjà là, même empreinte reçue par une autre version)."""
    ensure_tables(desk)
    _noter(desk, op)
    if op['charge'].get('supprime'):
        return []
    return [lambda: _publier_local(desk, op['objet'], op['id'])] if _blob(desk, op['charge']['sha256']).is_file() else []


def ensure_tables(desk):
    if not S._table(desk.db, 'sync56108_documents'):
        for part in SCHEMA.split(';'):
            if part.strip():
                desk.db.execute(part)


def _dossier_dir(desk, dossier_gid):
    base = _racine(desk) / 'documents'
    nom = 'sans-dossier'
    if dossier_gid:
        r = desk.db.execute("SELECT d.ref FROM sync56108_objets o JOIN dossiers5697 d ON d.id=o.local_id WHERE o.objet=?", (dossier_gid,)).fetchone() \
            if S._table(desk.db, 'dossiers5697') else None
        nom = nom_sur(r[0]) if r else nom_sur(dossier_gid[:12])
    d = base / nom
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _publier_local(desk, objet, version):
    """Copie publiée (nom sûr) de la version, à partir des octets vérifiés ; atomique."""
    r = desk.db.execute('SELECT * FROM sync56108_documents WHERE objet=? AND version=?', (objet, version)).fetchone()
    if not r or r['etat'] == 'disponible':
        return
    blob = _blob(desk, r['sha256'])
    if not blob.is_file() or _sha_fichier(blob) != r['sha256']:
        return
    plusieurs = desk.db.execute('SELECT COUNT(*) FROM sync56108_documents WHERE objet=?', (objet,)).fetchone()[0] > 1
    nom = r['nom_stockage']
    if plusieurs:   # versions concurrentes ou successives : chacune son nom, jamais d'écrasement ni de fusion des octets
        base, point, ext = nom.rpartition('.')
        nom = ('%s (version %s).%s' % (base, version[3:11], ext)) if point else '%s (version %s)' % (nom, version[3:11])
    dossier_dir = _dossier_dir(desk, r['dossier'])
    cible = _place(dossier_dir, nom, objet + ':' + version)
    tmp = cible.with_name('.' + cible.name[:60] + '.' + secrets.token_hex(4) + '.tmp')
    with open(blob, 'rb') as src, open(tmp, 'wb') as dst:
        for b in iter(lambda: src.read(BLOC), b''):
            dst.write(b)
    os.replace(tmp, cible)
    cible.with_name(cible.name + '.axh-doc').write_text(objet + ':' + version, encoding='utf-8')
    desk.db.execute("UPDATE sync56108_documents SET etat='disponible', chemin=?, motif='', maj_le=? WHERE objet=? AND version=?",
                    (str(cible), S._maintenant(), objet, version))
    desk.db.commit()
    origine = desk.db.execute('SELECT origine FROM sync56108_operations WHERE id=?', (version,)).fetchone()
    if origine and origine[0] != S._ident(desk).id:   # 5.6.108 (S5) : version reçue d'un autre poste : aussi dans le répertoire du dossier
        try:
            deposer_au_dossier(desk, objet, version)
        except Exception:   # noqa: BLE001 — le fichier reste disponible dans « Postes du cabinet » ; le motif est noté
            if desk.db.in_transaction:
                desk.db.rollback()


# ------------------------------------------------------------------------------------------------ transfert des blocs
def _peut_servir(desk, poste, moi, sha):
    """Une empreinte n'est servie qu'à un membre actif d'un espace qui contient une version de cette empreinte (dossier réservé compris)."""
    for r in desk.db.execute('SELECT DISTINCT espace FROM sync56108_documents WHERE sha256=?', (sha,)):
        if S.membre_actif(desk, r[0], poste) and S.membre_actif(desk, r[0], moi):
            return True
    return False


def servir_bloc(desk, poste, moi, obj):
    sha, index = str(obj.get('sha') or ''), obj.get('index')
    if not re.fullmatch(r'[0-9a-f]{64}', sha) or type(index) is not int or index < 0 or not _peut_servir(desk, poste, moi, sha):
        raise Stop('bloc_absent')
    blob = _blob(desk, sha)
    try:
        with open(blob, 'rb') as f:
            f.seek(index * BLOC)
            data = f.read(BLOC)
    except OSError:
        raise Stop('bloc_absent') from None
    if not data and index:
        raise Stop('bloc_absent')
    return {'t': 'sync.v1.blob_chunk', 'sha': sha, 'index': index, 'taille': len(data), 'sha_bloc': hashlib.sha256(data).hexdigest()}, data


def blocs_manquants(desk, sha, blocs):
    d = _partiel(desk, sha)
    return [i for i in range(len(blocs)) if not (d / ('%06d' % i)).is_file()]


def ecrire_bloc(desk, sha, blocs, index, data):
    """Bloc vérifié contre le manifeste AVANT d'être écrit (zone temporaire de la version) ; faux bloc : refusé, rien n'est écrit."""
    if not 0 <= index < len(blocs) or hashlib.sha256(data).hexdigest() != blocs[index]:
        raise Stop('bloc_altere')
    d = _partiel(desk, sha)
    tmp = d / ('%06d.%s.tmp' % (index, secrets.token_hex(3)))
    tmp.write_bytes(data)
    os.replace(tmp, d / ('%06d' % index))


def assembler(desk, sha, taille, blocs):
    """Tous les blocs présents : assemblage, contrôle taille + empreinte finale, publication atomique ; sinon la zone est purgée."""
    d = _partiel(desk, sha)
    cible = _blob(desk, sha)
    tmp = cible.with_name(sha + '.' + secrets.token_hex(4) + '.tmp')
    h, total = hashlib.sha256(), 0
    with open(tmp, 'wb') as f:
        for i in range(len(blocs)):
            b = (d / ('%06d' % i)).read_bytes()
            h.update(b)
            total += len(b)
            f.write(b)
    if total != taille or h.hexdigest() != sha:
        tmp.unlink()
        for p in d.iterdir():
            p.unlink()
        raise Stop('empreinte_finale_invalide')
    os.replace(tmp, cible)
    for p in d.iterdir():
        p.unlink()
    d.rmdir()


def recuperer_blocs(desk, canal, poste, moi, budget=MAX_OCTETS_SESSION):
    """Dans la session ouverte par ce poste : blocs manquants des versions en attente des espaces communs, bornés en octets ; transferts
    simultanés bornés pour tout le poste. Rend le nombre d'octets reçus."""
    if not S._table(desk.db, 'sync56108_documents'):
        return 0
    communs = S.espaces_communs(desk, poste, moi)
    if not communs:
        return 0
    if not _TRANSFERTS.acquire(blocking=False):
        return 0
    recu = 0
    try:
        marques = ','.join('?' for _ in communs)
        attente = desk.db.execute("SELECT DISTINCT sha256, taille, blocs FROM sync56108_documents WHERE etat IN ('metadonnees','telechargement','erreur') "
                                  'AND supprime=0 AND espace IN (%s) ORDER BY maj_le LIMIT 20' % marques, communs).fetchall()
        for r in attente:
            sha, blocs = r['sha256'], json.loads(r['blocs'])
            if not _blob(desk, sha).is_file():
                for i in blocs_manquants(desk, sha, blocs):
                    if recu >= budget:
                        return recu
                    canal.envoyer({'t': 'sync.v1.blob_need', 'sha': sha, 'index': i})
                    rep, data = canal.recevoir()
                    if rep.get('t') != 'sync.v1.blob_chunk' or rep.get('sha') != sha or rep.get('index') != i:
                        break
                    try:
                        ecrire_bloc(desk, sha, blocs, i, data)
                    except Stop:
                        break
                    recu += len(data)
                if blocs_manquants(desk, sha, blocs):
                    continue
                try:
                    assembler(desk, sha, r['taille'], blocs)
                except Stop as ex:
                    desk.db.execute("UPDATE sync56108_documents SET etat='erreur', motif=? WHERE sha256=?", (str(ex)[:60], sha))
                    desk.db.commit()
                    continue
            for v in desk.db.execute("SELECT objet, version FROM sync56108_documents WHERE sha256=? AND etat<>'disponible'", (sha,)).fetchall():
                _publier_local(desk, v['objet'], v['version'])
        return recu
    finally:
        _TRANSFERTS.release()


def versions_documents(desk, espaces=None, limite=200):
    out = []
    for r in desk.db.execute('SELECT * FROM sync56108_documents ORDER BY maj_le DESC LIMIT ?', (limite,)):
        d = dict(r)
        d['etat_libelle'] = ETATS.get(d['etat'], d['etat'])
        d['conflit'] = len(S.tetes(desk, d['objet'])) > 1
        out.append(d)
    return out


# ================================================================================================ 5.6.108 (lot S5)
# Fichiers reçus visibles dans la page « Fichiers » (source « Postes du cabinet ») et déposés dans le répertoire du dossier ; côté émetteur,
# publication automatique des fichiers des dossiers partagés : prompte après une écriture d'AxiorHub (signal posé par déclencheur dans la
# transaction de l'écriture : dépôt dans Fichiers, document produit, version enregistrée), et relevé périodique borné pour les modifications
# faites hors d'AxiorHub (date, taille, puis empreinte). Jamais pendant un GET : seulement depuis le passage du processus de liaison.
RELEVE_INTERVALLE = 600          # secondes entre deux relevés complets (un signal le déclenche aussitôt)
RELEVE_FICHIERS = 300            # fichiers examinés au plus par passage
RELEVE_OCTETS = 64 * 1024 * 1024  # octets lus au plus par passage (débit borné)
RELEVE_PROFONDEUR = 4
TEMPORAIRES = re.compile(r'(?i)^(~\$|\.~lock|~wr[a-z0-9]*\.tmp$)|\.(tmp|part|crdownload|swp)$')
RACINE_SOURCE = '/Postes du cabinet'

SCHEMA_S5 = '''
CREATE TABLE IF NOT EXISTS sync56108_fichiers_vus(dossier TEXT NOT NULL, chemin TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '', taille INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL, PRIMARY KEY(dossier, chemin));
CREATE TABLE IF NOT EXISTS sync56108_fichiers_signal(id INTEGER PRIMARY KEY AUTOINCREMENT, quand TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync56108_fichiers_releve(id INTEGER PRIMARY KEY CHECK(id=1), dernier REAL NOT NULL DEFAULT 0, curseur TEXT NOT NULL DEFAULT '');
INSERT OR IGNORE INTO sync56108_fichiers_releve(id, dernier, curseur) VALUES(1, 0, '');
'''
# écritures d'AxiorHub qui signalent un relevé prompt : (nom, table, événement, condition)
SIGNAUX = (('fop_u', 'fichiers56103_ops', 'UPDATE', "NEW.etat='fait'"), ('os4_i', 'office_saves440', 'INSERT', '1'),
           ('os5_i', 'office_saves5684', 'INSERT', '1'), ('pd4_i', 'production_deliverables_v420', 'INSERT', '1'),
           ('pr5_i', 'prepared_representations5689', 'INSERT', '1'))


def ensure_schema_s5(desk):
    if not S.schema_complet(desk.db, SCHEMA_S5, (('sync56108_fichiers_releve', 'id=1'),)):
        desk.db.executescript(SCHEMA_S5)
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(sync56108_documents)')}
    if 'dossier_chemin' not in cols:
        desk.db.execute("ALTER TABLE sync56108_documents ADD COLUMN dossier_chemin TEXT NOT NULL DEFAULT ''")
    desk.db.commit()


def installer_signaux(desk):
    """Déclencheurs posés à la création ou à l'adhésion d'un espace (idempotents) ; muets sans espace actif."""
    for nom, table, evt, cond in SIGNAUX:
        if not S._table(desk.db, table):
            continue
        desk.db.execute("CREATE TRIGGER IF NOT EXISTS sync56108_sig_%s AFTER %s ON %s WHEN (%s) AND EXISTS(SELECT 1 FROM sync56108_espaces WHERE "
                        "etat='actif') BEGIN INSERT INTO sync56108_fichiers_signal(quand) VALUES(strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ','now')); END"
                        % (nom, evt, table, cond))
    desk.db.commit()


def _stockage(desk):
    from .office440 import dav_client
    return dav_client(desk)


def _dossiers_partages(desk):
    """(id local, chemin, objet global, espace) des dossiers partagés dont le répertoire est connu sur ce poste."""
    if not S._table(desk.db, 'dossiers5697'):
        return []
    return desk.db.execute("SELECT d.id, d.chemin, o.objet, o.espace FROM sync56108_objets o JOIN dossiers5697 d ON d.id=o.local_id "
                           "WHERE o.genre='dossier' AND o.bloque=0 AND d.chemin<>'' ORDER BY d.id").fetchall()


def releve_du(desk, maintenant=None):
    maintenant = time.time() if maintenant is None else maintenant
    if desk.db.execute('SELECT 1 FROM sync56108_fichiers_signal LIMIT 1').fetchone():
        return True
    r = desk.db.execute('SELECT dernier FROM sync56108_fichiers_releve WHERE id=1').fetchone()
    return not r or maintenant - float(r[0]) >= RELEVE_INTERVALLE


def relever(desk, maintenant=None, budget_fichiers=RELEVE_FICHIERS, budget_octets=RELEVE_OCTETS, client=None):
    """Un passage borné : fichiers nouveaux ou modifiés des dossiers partagés → version publiée (même document si le fichier est connu).
    Respecte la désactivation, la pause du cabinet et ``pause5629.checkpoint`` avant chaque publication. Rend le bilan."""
    bilan = {'examines': 0, 'publies': 0, 'octets': 0, 'ignores': 0}
    if S.espace_cabinet(desk) is None or S.desactivee(desk):
        return bilan
    from .sync56108_missions import cabinet_en_pause
    if cabinet_en_pause(desk) or not releve_du(desk, maintenant):
        return bilan
    from .pause5629 import checkpoint
    try:
        client = client or _stockage(desk)
    except Stop:
        return bilan
    moi = S._ident(desk).id
    for v in desk.db.execute("SELECT d.objet, d.version FROM sync56108_documents d JOIN sync56108_operations o ON o.id=d.version WHERE d.etat='disponible' "
                             "AND d.dossier_chemin='' AND d.supprime=0 AND d.dossier<>'' AND o.origine<>? LIMIT 20", (moi,)).fetchall():
        deposer_au_dossier(desk, v['objet'], v['version'], client)   # reçue avant que le répertoire du dossier soit prêt : déposée maintenant
    desk.db.execute('DELETE FROM sync56108_fichiers_signal')
    desk.db.commit()
    complet = True
    for d in _dossiers_partages(desk):
        if not complet:
            break
        pile = [(d['chemin'], 0)]
        while pile and complet:
            chemin, prof = pile.pop()
            try:
                items = client.list_folder(chemin)
            except Stop:
                continue
            for it in items:
                if it.get('directory'):
                    if prof < RELEVE_PROFONDEUR:
                        pile.append((it['path'], prof + 1))
                    continue
                nom = it['path'].rsplit('/', 1)[-1]
                if nom.startswith('.') or TEMPORAIRES.search(nom) or EXCLUS.search(nom):
                    continue
                if bilan['examines'] >= budget_fichiers or bilan['octets'] >= budget_octets:
                    complet = False
                    break
                bilan['examines'] += 1
                rel = it['path'][len(d['chemin']):].lstrip('/')
                vu = desk.db.execute('SELECT * FROM sync56108_fichiers_vus WHERE dossier=? AND chemin=?', (d['id'], rel)).fetchone()
                etag, taille = str(it.get('etag') or ''), int(it.get('size') or 0)
                if vu and vu['etag'] == etag and vu['taille'] == taille:
                    continue
                try:
                    donnees = client.download(it)
                except Stop as ex:   # trop volumineux pour le relevé automatique, ou illisible : noté, repris si le fichier change
                    _noter_vu(desk, d['id'], rel, etag, taille, vu['sha256'] if vu else '', vu['objet'] if vu else '', str(ex).split(':')[0][:60])
                    bilan['ignores'] += 1
                    continue
                bilan['octets'] += len(donnees)
                sha = hashlib.sha256(donnees).hexdigest()
                if vu and vu['sha256'] == sha:
                    _noter_vu(desk, d['id'], rel, etag, taille, sha, vu['objet'], '')
                    continue
                checkpoint(desk)
                objet = vu['objet'] if vu and vu['objet'] else None
                tmp = _racine(desk) / ('releve-%s.tmp' % secrets.token_hex(6))
                tmp.write_bytes(donnees)
                try:
                    out = publier(desk, str(tmp), nom, d['objet'], objet, par='releve')
                    _noter_vu(desk, d['id'], rel, etag, taille, sha, out['objet'], '')
                    desk.db.execute('UPDATE sync56108_documents SET dossier_chemin=? WHERE objet=? AND version=?', (it['path'], out['objet'], out['version']))
                    desk.db.commit()
                    bilan['publies'] += 1
                except Stop as ex:
                    _noter_vu(desk, d['id'], rel, etag, taille, '', objet or '', str(ex).split(':')[0][:60])
                    bilan['ignores'] += 1
                finally:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
    if complet:
        desk.db.execute('UPDATE sync56108_fichiers_releve SET dernier=? WHERE id=1', (time.time() if maintenant is None else maintenant,))
    else:   # budget épuisé : le passage suivant reprend aussitôt
        desk.db.execute('INSERT INTO sync56108_fichiers_signal(quand) VALUES(?)', (S._maintenant(),))
    desk.db.commit()
    return bilan


def _noter_vu(desk, dossier, rel, etag, taille, sha, objet, motif):
    desk.db.execute('INSERT INTO sync56108_fichiers_vus(dossier,chemin,etag,taille,sha256,objet,motif,maj_le) VALUES(?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(dossier,chemin) DO UPDATE SET etag=excluded.etag,taille=excluded.taille,sha256=excluded.sha256,objet=excluded.objet,'
                    'motif=excluded.motif,maj_le=excluded.maj_le', (dossier, rel, etag, taille, sha, objet, motif, S._maintenant()))
    desk.db.commit()


def _libre(client, dossier, nom):
    """Nom libre dans le répertoire du dossier (casse comprise : jamais un autre fichier écrasé)."""
    try:
        pris = {it['path'].rsplit('/', 1)[-1].casefold() for it in client.list_folder(dossier)}
    except Stop:
        pris = set()
    base, point, ext = nom.rpartition('.')
    if not point:
        base, ext = nom, ''
    candidat, n = nom, 2
    while candidat.casefold() in pris:
        candidat = '%s (%d)%s' % (base, n, ('.' + ext if ext else ''))
        n += 1
    return candidat


def deposer_au_dossier(desk, objet, version, client=None):
    """Version disponible → fichier dans le répertoire LOCAL du dossier. Création exclusive ; remplacement conditionnel seulement si le fichier du
    dossier est encore la version précédente connue (sinon la version reçue va à côté : une modification locale n'est jamais écrasée). Le
    fichier déposé est noté « vu » : le relevé ne le renvoie pas (aucun écho)."""
    r = desk.db.execute('SELECT * FROM sync56108_documents WHERE objet=? AND version=?', (objet, version)).fetchone()
    if not r or r['etat'] != 'disponible' or not r['dossier'] or r['supprime'] or r['dossier_chemin']:
        return ''
    d = desk.db.execute("SELECT d.id, d.chemin FROM sync56108_objets o JOIN dossiers5697 d ON d.id=o.local_id WHERE o.objet=?", (r['dossier'],)).fetchone() \
        if S._table(desk.db, 'dossiers5697') else None
    if not d or not d['chemin']:
        return ''
    try:
        client = client or _stockage(desk)
    except Stop:
        return ''
    donnees = _blob(desk, r['sha256']).read_bytes()
    plusieurs = len(S.tetes(desk, objet)) > 1
    nom = r['nom_stockage']
    if plusieurs:   # versions concurrentes : chacune son fichier dans le dossier, à comparer
        base, point, ext = nom.rpartition('.')
        nom = ('%s (version %s).%s' % (base, version[3:11], ext)) if point else '%s (version %s)' % (nom, version[3:11])
    ancien = desk.db.execute('SELECT * FROM sync56108_fichiers_vus WHERE dossier=? AND objet=? ORDER BY maj_le DESC LIMIT 1', (d['id'], objet)).fetchone()
    rel = ''
    try:
        if ancien and not plusieurs and ancien['sha256'] != r['sha256']:
            item = client.stat(d['chemin'] + '/' + ancien['chemin'])
            if str(item.get('etag') or '') == ancien['etag']:
                client.replace_file(d['chemin'] + '/' + ancien['chemin'], donnees, ancien['etag'])
                rel = ancien['chemin']
        if not rel:
            rel = _libre(client, d['chemin'], nom)
            client.put_file(d['chemin'] + '/' + rel, donnees)
    except Stop as ex:
        desk.db.execute('UPDATE sync56108_documents SET motif=? WHERE objet=? AND version=?', (str(ex).split(':')[0][:60], objet, version))
        desk.db.commit()
        return ''
    try:
        st = client.stat(d['chemin'] + '/' + rel)
        _noter_vu(desk, d['id'], rel, str(st.get('etag') or ''), int(st.get('size') or len(donnees)), r['sha256'], objet, '')
    except Stop:
        _noter_vu(desk, d['id'], rel, '', len(donnees), r['sha256'], objet, '')
    desk.db.execute('UPDATE sync56108_documents SET dossier_chemin=? WHERE objet=? AND version=?', (d['chemin'] + '/' + rel, objet, version))
    desk.db.commit()
    return rel


# ------------------------------------------------------------------------------------------------ page « Fichiers » : source « Postes du cabinet »
def _visibles(desk, acteur):
    """Versions reçues ou publiées, regroupées par dossier visible de l'acteur (dossier réservé à d'autres : absent, comme ailleurs)."""
    from .acces5697 import peut_lire_dossier
    out = {}
    for r in desk.db.execute('SELECT * FROM sync56108_documents WHERE supprime=0 ORDER BY nom, maj_le').fetchall():
        local, groupe = '', 'Sans dossier'
        if r['dossier']:
            row = desk.db.execute("SELECT d.id, d.ref FROM sync56108_objets o JOIN dossiers5697 d ON d.id=o.local_id WHERE o.objet=?", (r['dossier'],)).fetchone() \
                if S._table(desk.db, 'dossiers5697') else None
            if not row:
                continue
            local, groupe = row['id'], nom_sur(row['ref'])
        if local and not peut_lire_dossier(desk, acteur, local):
            continue
        nom = Path(r['chemin']).name if r['etat'] == 'disponible' and r['chemin'] else nom_sur(r['nom'])
        if r['etat'] != 'disponible':
            base, point, ext = nom.rpartition('.')
            nom = ('%s (téléchargement en attente).%s' % (base, ext)) if point else nom + ' (téléchargement en attente)'
        out.setdefault(groupe, {})[nom] = r
    return out


def fabrique_source(desk, auth, acteur):
    """Inscrite dans ``fichiers56103`` : source en lecture seule, ouverte seulement quand un espace partagé existe sur ce poste."""
    from . import fichiers56103 as FI
    if not S._table(desk.db, 'sync56108_espaces') or S.espace_cabinet(desk) is None:
        return None

    class SourcePostes(FI.Source):
        ident, libelle = 'postes', 'Postes du cabinet'

        def racines(self):
            return [{'chemin': RACINE_SOURCE, 'nom': self.libelle}]

        def _parts(self, chemin):
            return [p for p in chemin[len(RACINE_SOURCE):].split('/') if p]

        def _trouver(self, chemin):
            parts = self._parts(chemin)
            vue = _visibles(desk, acteur)
            if len(parts) == 2 and parts[0] in vue and parts[1] in vue[parts[0]]:
                return vue[parts[0]][parts[1]]
            raise Stop('introuvable')

        def lister(self, chemin):
            vue = _visibles(desk, acteur)
            parts = self._parts(chemin)
            if not parts:
                return [FI.entree(RACINE_SOURCE + '/' + g, True) for g in sorted(vue)]
            if len(parts) == 1 and parts[0] in vue:
                return [FI.entree(RACINE_SOURCE + '/' + parts[0] + '/' + n, False, r['taille'], r['maj_le'], '', r['sha256'] if r['etat'] == 'disponible' else '')
                        for n, r in sorted(vue[parts[0]].items())]
            raise Stop('introuvable')

        def proprietes(self, chemin):
            r = self._trouver(chemin)
            return {**FI.entree(chemin, False, r['taille'], r['maj_le'], '', r['sha256']), 'auteur': ETATS.get(r['etat'], r['etat'])}

        def lire(self, chemin, limite):
            r = self._trouver(chemin)
            if r['etat'] != 'disponible':
                raise Stop('telechargement_en_attente')
            if r['taille'] > limite:
                raise Stop('fichier_trop_volumineux')
            data = _blob(desk, r['sha256']).read_bytes()
            if hashlib.sha256(data).hexdigest() != r['sha256']:
                raise Stop('telechargement_en_attente')
            return data
    return SourcePostes()


def inscrire():
    from . import fichiers56103 as FI
    if 'postes' not in FI.SOURCES:
        FI.inscrire_source('postes', 'Postes du cabinet', fabrique_source)
