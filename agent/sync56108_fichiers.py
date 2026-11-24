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
import sqlite3
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
    from . import pseudo540 as _p56109; _p56109.invalider(desk, 'synchronisation')   # 5.6.109 (lot P, P02) : identités reçues d'un autre poste connues dès la prochaine émission externe
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
        except Exception as ex:   # noqa: BLE001 — le fichier reste disponible dans « Postes du cabinet » ; le motif est noté
            if desk.db.in_transaction:
                desk.db.rollback()
            from .reprise56109 import arret_central
            if arret_central(ex):                      # 5.6.109 (C05) : un arrêt central n'est pas une panne de dépôt : il remonte
                raise


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
# 5.6.109 (C06) : relevé reprenable. Le champ ``curseur`` garde l'état versionné de la campagne en cours (``reprise56109``) : pile des
# répertoires à visiter, dernière clé traitée, dossiers finis, erreurs de lecture, bilan cumulé. Un passage est borné en fichiers examinés (les
# inchangés comptent), en octets lus, en répertoires listés et en durée ; le suivant reprend APRÈS la dernière clé traitée. ``dernier`` : date
# de la dernière campagne finie sans erreur (réussite complète) ; ``essai`` : fin de la dernière campagne finie AVEC erreur (elle espace le
# nouvel essai, sans jamais valoir réussite) ; ``bilan`` : dernier bilan connu, partiel ou complet.
RELEVE_LISTES = 120              # répertoires listés au plus par passage (chaque liste est un appel au stockage)
RELEVE_SECONDES = 30.0           # durée au plus d'un passage ; au-delà il s'arrête sur une clé et le suivant reprend
COLONNES_RELEVE = (('essai', 'REAL NOT NULL DEFAULT 0'), ('bilan', "TEXT NOT NULL DEFAULT '{}'"))
# Refus durables d'un fichier (noté « vu » avec son motif, repris s'il change) ; toute autre erreur de lecture laisse le fichier à reprendre et
# interdit « réussite complète » à la campagne.
REFUS_DURABLES = frozenset(('piece_trop_volumineuse', 'fichier_trop_grand', 'fichier_exclu_de_la_synchronisation', 'chemin_trop_long'))
DISPARUS = frozenset(('http_404', 'fichier_nextcloud_introuvable', 'introuvable'))
DEPOT_TROP_GROS = 'fichier_trop_volumineux_pour_le_depot'
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
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(sync56108_fichiers_releve)')}   # 5.6.109 (C06) : colonnes ajoutées, ligne et date gardées
    for nom, definition in COLONNES_RELEVE:
        if nom not in cols:
            desk.db.execute('ALTER TABLE sync56108_fichiers_releve ADD COLUMN %s %s' % (nom, definition))
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
    """Un passage est-il dû ? Signal d'une écriture d'AxiorHub, campagne en cours (curseur : le passage suivant reprend aussitôt), ou
    intervalle écoulé depuis la dernière campagne finie — réussie (``dernier``) ou finie avec erreur (``essai`` : un stockage en panne
    n'est pas martelé à chaque passage)."""
    maintenant = time.time() if maintenant is None else maintenant
    if desk.db.execute('SELECT 1 FROM sync56108_fichiers_signal LIMIT 1').fetchone():
        return True
    try:
        r = desk.db.execute('SELECT dernier, curseur, essai FROM sync56108_fichiers_releve WHERE id=1').fetchone()
    except sqlite3.OperationalError:   # base d'avant la 5.6.109 ouverte sans migration : contrat d'origine
        r = desk.db.execute("SELECT dernier, '' AS curseur, 0 AS essai FROM sync56108_fichiers_releve WHERE id=1").fetchone()
    if not r or r[1]:
        return True
    return maintenant - max(float(r[0]), float(r[2] or 0)) >= RELEVE_INTERVALLE


def etat_releve(desk):
    """État du relevé (lecture seule) : ``reussite`` (date de la dernière campagne finie sans erreur, 0 si aucune), ``essai`` (fin de la
    dernière campagne finie avec erreur), ``en_cours`` (campagne commencée, à poursuivre), ``bilan`` du dernier passage connu (partiel ou
    complet, avec les erreurs de lecture)."""
    r = desk.db.execute('SELECT dernier, curseur, essai, bilan FROM sync56108_fichiers_releve WHERE id=1').fetchone()
    if not r:
        return {'reussite': 0.0, 'essai': 0.0, 'en_cours': False, 'bilan': {}}
    try:
        bilan = json.loads(r[3] or '{}')
    except ValueError:
        bilan = {}
    return {'reussite': float(r[0] or 0), 'essai': float(r[2] or 0), 'en_cours': bool(r[1]), 'bilan': bilan if isinstance(bilan, dict) else {}}


def _deja_publie(desk, moi, gid, nom, sha, chemin, objet):
    """Rejeu après une coupure entre la publication et la marque « vu » : la version que CE poste a déjà publiée pour ce fichier (même dossier,
    même empreinte, tête courante du document, pas encore rattachée à un autre chemin) est reconnue ; aucun second document, aucune version
    en double. Rend ``(objet, version)`` ou None."""
    if objet:
        lignes = desk.db.execute("SELECT d.objet, d.version FROM sync56108_documents d JOIN sync56108_operations o ON o.id=d.version WHERE d.objet=? AND d.sha256=? "
                                 "AND d.supprime=0 AND o.origine=? AND d.dossier_chemin IN ('', ?) ORDER BY d.maj_le DESC", (objet, sha, moi, chemin)).fetchall()
    else:
        lignes = desk.db.execute("SELECT d.objet, d.version FROM sync56108_documents d JOIN sync56108_operations o ON o.id=d.version WHERE d.dossier=? AND d.sha256=? "
                                 "AND d.nom=? AND d.supprime=0 AND o.origine=? AND d.dossier_chemin IN ('', ?) ORDER BY d.maj_le DESC",
                                 (gid, sha, S._nom(nom, 255) or 'document', moi, chemin)).fetchall()
    for r in lignes:
        if r['version'] in S.tetes(desk, r['objet']):     # la tête seulement : un retour à un contenu ancien reste une nouvelle version
            return r['objet'], r['version']
    return None


def _relever_fichier(desk, client, d, it, bilan, etat, moi, checkpoint):
    """Un fichier du relevé, jusqu'à son état durable (version publiée ET marque « vu », ou refus noté). Rend après ; un arrêt central remonte."""
    from . import reprise56109 as R
    nom = it['path'].rsplit('/', 1)[-1]
    rel = it['path'][len(d['chemin']):].lstrip('/')
    vu = desk.db.execute('SELECT * FROM sync56108_fichiers_vus WHERE dossier=? AND chemin=?', (d['id'], rel)).fetchone()
    etag, taille = str(it.get('etag') or ''), int(it.get('size') or 0)
    if vu and vu['etag'] == etag and vu['taille'] == taille:
        return
    try:
        donnees = client.download(it)
    except Stop as ex:
        if R.arret_central(ex):
            raise
        code = R.code_de(ex)[:60]
        if code in REFUS_DURABLES:   # trop volumineux pour le relevé automatique : noté, repris si le fichier change
            _noter_vu(desk, d['id'], rel, etag, taille, vu['sha256'] if vu else '', vu['objet'] if vu else '', code)
            bilan['ignores'] += 1
        elif code not in DISPARUS:   # lecture en panne : le fichier reste à reprendre, la campagne n'est pas une réussite complète
            R.noter_erreur(etat, d['id'], it['path'], code)
            bilan['erreurs'].append({'chemin': it['path'], 'motif': code})
        return
    bilan['octets'] += len(donnees)
    sha = hashlib.sha256(donnees).hexdigest()
    if vu and vu['sha256'] == sha:
        _noter_vu(desk, d['id'], rel, etag, taille, sha, vu['objet'], '')
        return
    objet = vu['objet'] if vu and vu['objet'] else None
    deja = _deja_publie(desk, moi, d['objet'], nom, sha, it['path'], objet)
    if deja:
        desk.db.execute('UPDATE sync56108_documents SET dossier_chemin=? WHERE objet=? AND version=?', (it['path'], deja[0], deja[1]))
        _noter_vu(desk, d['id'], rel, etag, taille, sha, deja[0], '')
        return
    checkpoint(desk)
    tmp = _racine(desk) / ('releve-%s.tmp' % secrets.token_hex(6))
    tmp.write_bytes(donnees)
    try:
        out = publier(desk, str(tmp), nom, d['objet'], objet, par='releve')
        desk.db.execute('UPDATE sync56108_documents SET dossier_chemin=? WHERE objet=? AND version=?', (it['path'], out['objet'], out['version']))
        _noter_vu(desk, d['id'], rel, etag, taille, sha, out['objet'], '')
        bilan['publies'] += 1
    except Stop as ex:
        if R.arret_central(ex):
            raise
        code = R.code_de(ex)[:60]
        if code in REFUS_DURABLES:
            _noter_vu(desk, d['id'], rel, etag, taille, '', objet or '', code)
            bilan['ignores'] += 1
        else:
            R.noter_erreur(etat, d['id'], it['path'], code)
            bilan['erreurs'].append({'chemin': it['path'], 'motif': code})
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def relever(desk, maintenant=None, budget_fichiers=RELEVE_FICHIERS, budget_octets=RELEVE_OCTETS, client=None, budget_listes=RELEVE_LISTES,
            budget_secondes=RELEVE_SECONDES, horloge=time.monotonic):
    """Un passage borné d'une campagne reprenable : fichiers nouveaux ou modifiés des dossiers partagés → version publiée (même document si
    le fichier est connu). Respecte la désactivation, la pause du cabinet et ``pause5629.checkpoint`` avant chaque effet.

    5.6.109 (C06) : le passage reprend la campagne au curseur enregistré (après la dernière clé traitée), sur le périmètre RELU des dossiers
    partagés ; le curseur n'est réécrit qu'à la fin du passage, après le traitement durable de chaque fichier : une interruption (arrêt
    central, coupure) garde l'ancien curseur et le rejeu reconnaît ce qui a déjà été publié. Une erreur de lecture est gardée dans le bilan et
    interdit « réussite complète ». Rend le bilan du passage : ``examines``, ``publies``, ``octets``, ``ignores``, ``erreurs``, ``reprise``
    (campagne poursuivie), ``fini`` (campagne terminée par ce passage), ``complet`` (terminée sans aucune erreur)."""
    from . import reprise56109 as R
    bilan = {'examines': 0, 'publies': 0, 'octets': 0, 'ignores': 0, 'erreurs': [], 'reprise': False, 'fini': False, 'complet': False}
    if S.espace_cabinet(desk) is None or S.desactivee(desk):
        return bilan
    from .sync56108_missions import cabinet_en_pause
    if cabinet_en_pause(desk) or not releve_du(desk, maintenant):
        return bilan
    from .pause5629 import checkpoint
    try:
        client = client or _stockage(desk)
    except Stop as ex:
        if R.arret_central(ex):
            raise
        return bilan
    quand = time.time() if maintenant is None else maintenant
    depart = horloge()
    moi = S._ident(desk).id
    for v in desk.db.execute("SELECT d.objet, d.version FROM sync56108_documents d JOIN sync56108_operations o ON o.id=d.version WHERE d.etat='disponible' "
                             "AND d.dossier_chemin='' AND d.supprime=0 AND d.dossier<>'' AND d.motif<>? AND o.origine<>? ORDER BY d.maj_le LIMIT 20",
                             (DEPOT_TROP_GROS, moi)).fetchall():
        deposer_au_dossier(desk, v['objet'], v['version'], client)   # reçue avant que le répertoire du dossier soit prêt : déposée maintenant
    desk.db.execute('DELETE FROM sync56108_fichiers_signal')
    desk.db.commit()
    partages = {d['id']: d for d in _dossiers_partages(desk)}       # périmètre relu avant le lot : une révocation retire ses positions
    brut = desk.db.execute('SELECT curseur FROM sync56108_fichiers_releve WHERE id=1').fetchone()
    etat, bilan['reprise'] = R.reprendre(brut[0] if brut else '', {k: d['chemin'] for k, d in partages.items()}, quand, RELEVE_PROFONDEUR)
    pile, listes, epuise = etat['pile'], 0, False
    campagne = set(etat['faits']) | {f['dossier'] for f in pile}
    while pile and not epuise:
        if listes >= budget_listes or bilan['examines'] >= budget_fichiers or bilan['octets'] >= budget_octets or horloge() - depart >= budget_secondes:
            break
        cadre = pile.pop(0)
        d = partages[cadre['dossier']]                             # position validée par ``reprendre`` sur le périmètre relu
        listes += 1
        try:
            items = sorted(client.list_folder(cadre['chemin']), key=lambda it: str(it['path']))
        except Stop as ex:
            if R.arret_central(ex):
                raise
            R.noter_erreur(etat, cadre['dossier'], cadre['chemin'], R.code_de(ex))   # ni lu, ni « complet » : repris à la campagne suivante
            bilan['erreurs'].append({'chemin': cadre['chemin'], 'motif': R.code_de(ex)[:60]})
            continue
        enfants = []
        for it in items:
            chemin = str(it['path'])
            if chemin <= cadre['apres'] or chemin.rsplit('/', 1)[0] != cadre['chemin'].rstrip('/'):
                continue                                           # déjà traité à un passage précédent, ou entrée hors de ce répertoire
            nom = chemin.rsplit('/', 1)[-1]
            if it.get('directory'):
                if cadre['prof'] < RELEVE_PROFONDEUR:
                    enfants.append({'dossier': cadre['dossier'], 'chemin': chemin, 'prof': cadre['prof'] + 1, 'apres': ''})
                cadre['apres'] = chemin
                continue
            if nom.startswith('.') or TEMPORAIRES.search(nom) or EXCLUS.search(nom):
                cadre['apres'] = chemin
                continue
            if bilan['examines'] >= budget_fichiers or bilan['octets'] >= budget_octets or horloge() - depart >= budget_secondes:
                epuise = True                                      # le répertoire reste en tête de pile, à reprendre après ``apres``
                break
            bilan['examines'] += 1
            _relever_fichier(desk, client, d, it, bilan, etat, moi, checkpoint)
            cadre['apres'] = chemin                                # avancé seulement après l'état durable du fichier
        pile[:0] = ([cadre] if epuise else []) + enfants
    en_cours = {f['dossier'] for f in pile}
    etat['faits'] = sorted(x for x in campagne if x not in en_cours and x in partages)
    for k in ('examines', 'publies', 'octets', 'ignores'):
        etat['cumul'][k] += bilan[k]
    etat['cumul']['passages'] += 1
    curseur = R.encoder(etat)
    if pile and not curseur:                                       # campagne trop vaste pour son curseur : elle recommencera, jamais « complète »
        R.noter_erreur(etat, '', '', 'curseur_trop_volumineux')
        bilan['erreurs'].append({'chemin': '', 'motif': 'curseur_trop_volumineux'})
    bilan['fini'] = not curseur
    bilan['complet'] = bilan['fini'] and not etat['erreurs'] and not etat['erreurs_omises']
    garde = json.dumps({**etat['cumul'], 'debut': etat['debut'], 'maj': quand, 'fini': bilan['fini'], 'complet': bilan['complet'],
                        'erreurs': etat['erreurs'], 'erreurs_omises': etat['erreurs_omises'], 'dossiers': len(partages)}, ensure_ascii=False)
    if bilan['complet']:      # réussite complète : seule une campagne finie sans erreur date ``dernier``
        desk.db.execute("UPDATE sync56108_fichiers_releve SET dernier=?, essai=0, curseur='', bilan=? WHERE id=1", (quand, garde))
    elif bilan['fini']:       # finie avec des erreurs de lecture : nouvel essai espacé, ``dernier`` inchangé
        desk.db.execute("UPDATE sync56108_fichiers_releve SET essai=?, curseur='', bilan=? WHERE id=1", (quand, garde))
    else:                     # budget du passage épuisé : le suivant reprend aussitôt au curseur
        desk.db.execute('UPDATE sync56108_fichiers_releve SET curseur=?, bilan=? WHERE id=1', (curseur, garde))
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


def _deja_depose(client, dossier, connus, nom, taille, sha):
    """Fichier du répertoire qui EST déjà cette version (dépôt précédent dont la réponse s'est perdue) : chemin relatif, ou ''. Seuls le
    nom attendu, ses variantes « (2) » à « (4) » et l'ancien emplacement du document sont relus ; la taille est comparée avant toute lecture ;
    un autre contenu du même nom n'est jamais adopté. Lecture en panne : '' (le dépôt suivra son cours ordinaire, création exclusive)."""
    base, point, ext = nom.rpartition('.')
    if not point:
        base, ext = nom, ''
    candidats = list(connus) + [nom] + ['%s (%d)%s' % (base, n, ('.' + ext if ext else '')) for n in (2, 3, 4)]
    for rel in dict.fromkeys(c for c in candidats if c):
        try:
            item = client.stat(dossier + '/' + rel)
            if int(item.get('size') or 0) != taille:
                continue
            if hashlib.sha256(client.download({**item, 'path': dossier + '/' + rel})).hexdigest() == sha:
                return rel
        except Stop:
            continue
    return ''


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
    # 5.6.109 (C06) : la limite du dépôt s'applique AVANT le chargement (un fichier synchronisé peut atteindre 2 Go) : au-delà, il reste
    # disponible dans « Postes du cabinet », avec un motif lisible, et n'est jamais lu en entier en mémoire pour être refusé ensuite.
    if int(r['taille'] or 0) > int(getattr(client, 'cfg', {}).get('max_generated_file_bytes', 20_000_000)):
        desk.db.execute('UPDATE sync56108_documents SET motif=? WHERE objet=? AND version=?', (DEPOT_TROP_GROS, objet, version))
        desk.db.commit()
        return ''
    from .pause5629 import checkpoint
    checkpoint(desk)                                   # contrôle d'arrêt avant l'effet (écriture dans le répertoire du dossier)
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
            # 5.6.109 (C07) : un dépôt précédent a pu aboutir sans que sa réponse arrive (délai, coupure). Avant de choisir un nom libre, le
            # fichier attendu est relu : même taille et même empreinte = c'est cette version, constatée sans rien réécrire (jamais « … (2) »).
            rel = _deja_depose(client, d['chemin'], [ancien['chemin']] if ancien and not plusieurs else [], nom, len(donnees), r['sha256'])
        if not rel:
            rel = _libre(client, d['chemin'], nom)
            client.put_file(d['chemin'] + '/' + rel, donnees)
    except Stop as ex:
        from .reprise56109 import arret_central
        if arret_central(ex):     # arrêt central : jamais noté comme une panne de dépôt ; l'essai suivant constatera le fichier s'il a été écrit
            raise
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
