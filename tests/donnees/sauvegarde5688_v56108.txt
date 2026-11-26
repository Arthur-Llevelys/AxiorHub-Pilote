"""5.6.88 : sauvegarde automatique chiffrée du cabinet (état, comptes, configuration), réglée depuis Paramètres.

Constat de départ : ``deploy/vps/backup.sh`` était manuel, non planifié, non chiffré et écrivait sur le serveur lui-même ; rien n'existait pour un
poste Windows. Ce module fait, une fois par jour et un seul travail à la fois :

1. copie cohérente de chaque base SQLite par l'API de sauvegarde de sqlite3 (jamais une copie du fichier ouvert), un fichier temporaire à la fois ;
2. archive tar.gz chiffrée au fil de l'eau en AES-256-GCM (blocs de 1 Mio, voir ``Ecriture``) : l'archive n'est jamais chargée en mémoire ;
3. relecture du fichier écrit : empreinte SHA-256, déchiffrement complet, contrôle de chaque fichier contre le manifeste interne ;
4. dépôt dans le second emplacement choisi (``stockage5688``, WebDAV ou compatible S3), relu de la même façon ;
5. rétention : 7 quotidiennes et 4 hebdomadaires par défaut, appliquée localement et à distance, seulement après une écriture vérifiée.

Règle de rétention absolue (précédent : 302 Go de copies accumulées par upgrade.py, 30 octobre 2026) : seul un fichier portant exactement le nom
d'une archive d'AxiorHub est supprimé ; le dossier des sauvegardes n'entre jamais dans une sauvegarde ; les fichiers temporaires ne survivent pas.

Phrase : le cabinet la choisit, elle n'est ni stockée ni journalisée. La clé qui en dérive (scrypt) est gardée dans le coffre (``vault567``) pour que
la sauvegarde de nuit tourne sans personne ; elle ne sort jamais dans l'archive. Sans cette clé, la sauvegarde ne démarre pas. Une archive se relit
avec la phrase seule : le sel et les paramètres de scrypt sont dans son en-tête (restauration sur une machine neuve, voir docs/REVERSIBILITE-5.6.88.md).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import struct
import sys
import tarfile
import unicodedata
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, read_secret
from .portable import fcntl
from . import vault567

JOB = 'sauvegarde5688'
CONTROLE = 'controle5688'
CONFIG = 'sauvegarde5688:config'
META_CLE = 'sauvegarde5688:cle'           # sel et paramètres publics de scrypt, jamais la phrase ni la clé
REUSSITE = 'sauvegarde5688:derniere_reussite'
ESSAI = 'auto:sauvegarde5688'
DEFAUTS = {'actif': False, 'heure': 2, 'dossier': '', 'quotidiennes': 7, 'hebdomadaires': 4, 'taille_max_go': 100, 'distant': {'type': ''}}
PHRASE_MIN, PHRASE_MAX = 16, 200
SCRYPT = {'n': 2 ** 16, 'r': 8, 'p': 1}   # 64 Mio de mémoire ; dérivée une fois au réglage, pas à chaque sauvegarde
MAGIC = b'AXIORHUB-SAUVEGARDE-1\n'
BLOC = 1024 * 1024
FICHIER_MEMOIRE = 8 * 1024 * 1024         # au-delà, un fichier ordinaire est d'abord figé dans une copie temporaire (sa taille doit rester exacte dans le tar)
MAX_FICHIERS = 500_000
MARGE_DISQUE = 1024 ** 3                  # jamais moins de 1 Gio libre : une sauvegarde ne remplit pas le disque du serveur
ALERTE_HEURES = 48
RATTRAPAGE_HEURES = 30                    # poste éteint à l'heure prévue : rattrapée au premier passage
REESSAI_SECONDES = 6 * 3600
HISTORIQUE = 200                          # lignes conservées dans sauvegardes5688
NOM = re.compile(r'^axiorhub-(\d{8})-(\d{4})-[0-9a-f]{12}\.axbk$')
PARTIE = re.compile(r'^axiorhub-\d{8}-\d{4}-[0-9a-f]{12}\.axbk\.part$')
MODE_DOSSIER = 'sauvegardes5688'          # dossier par défaut, dans le dossier d'état : le seul qu'un service systemd puisse écrire sans réglage
TRAVAIL = '.travail5688'                  # copies temporaires EN CLAIR : toujours à côté des données d'origine, jamais à l'emplacement de sauvegarde
# Rien de ce qui suit n'entre dans une archive : ses propres sorties, caches, verrous et copies déjà faites par les mises à jour.
EXCLUS_DOSSIERS = frozenset((MODE_DOSSIER, TRAVAIL, 'exports5688', 'voice568-cache', 'producer-locks567', 'calendar-locks568', 'navigateur5673',
                             'maj', 'backups', '__pycache__', 'extension-quarantine'))
EXCLUS_FICHIERS = re.compile(r'(\.lock|\.lock\.json|-wal|-shm|-journal|\.tmp|\.part|\.axbk)$|^\.secret-|-before-.*\.sqlite3$|^bootstrap-token$')
PROGRES_OCTETS = 256 * 1024 * 1024


# ------------------------------------------------------------------------------------------------ phrase et clé
def _coffre(desk):
    path = Path(desk.c['state_dir']) / 'vault' / 'sauvegarde5688'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _phrase_bytes(phrase):
    return unicodedata.normalize('NFC', str(phrase or '')).encode('utf-8')   # même phrase, même clé, quel que soit le clavier


def deriver(phrase, sel, n, r, p):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=sel, length=32, n=n, r=r, p=p).derive(_phrase_bytes(phrase))


def phrase_definie(desk):
    meta = desk.settings(META_CLE, None)
    return bool(meta and meta.get('sel') and (_coffre(desk) / 'cle.secret').is_file())


def cle_active(desk):
    """Clé dérivée lue dans le coffre. Sans elle, aucune sauvegarde ne démarre."""
    if not phrase_definie(desk):
        raise Stop('phrase_de_sauvegarde_absente')
    return bytes.fromhex(read_secret(_coffre(desk) / 'cle.secret'))


def tester_phrase(desk, phrase):
    """Le cabinet vérifie qu'il se souvient de sa phrase : réponse oui ou non, rien n'est conservé."""
    if not phrase_definie(desk):
        raise Stop('phrase_de_sauvegarde_absente')
    meta = desk.settings(META_CLE)
    return hmac.compare_digest(deriver(phrase, base64.b64decode(meta['sel']), meta['n'], meta['r'], meta['p']), cle_active(desk))


def definir_phrase(desk, phrase, confirmation, owner, actuelle=''):
    phrase = str(phrase or '')
    if len(phrase) < PHRASE_MIN or len(phrase) > PHRASE_MAX:
        raise Stop('phrase_de_sauvegarde_trop_courte')
    if not hmac.compare_digest(_phrase_bytes(phrase), _phrase_bytes(confirmation)):
        raise Stop('phrase_de_sauvegarde_differente')
    remplace = phrase_definie(desk)
    if remplace and not tester_phrase(desk, actuelle):
        raise Stop('phrase_actuelle_incorrecte')
    sel = os.urandom(16)
    vault567.write(_coffre(desk) / 'cle.secret', deriver(phrase, sel, **SCRYPT).hex())
    desk.setting(META_CLE, {'sel': base64.b64encode(sel).decode(), **SCRYPT, 'definie_le': desk.now()})
    desk.audit('sauvegarde5688_phrase', {'par': str(owner)[:120], 'remplacee': remplace})
    return {'ok': True, 'message': 'Phrase enregistrée. Notez-la en lieu sûr : sans elle, aucune archive ne peut être relue.'
            + (' Les archives déjà faites restent lisibles avec l’ancienne phrase.' if remplace else '')}


# ------------------------------------------------------------------------------------------------ format chiffré
def _aead(cle):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(cle)


def _nonce(prefixe, compteur, dernier):
    """Construction STREAM : un bloc retiré, déplacé ou la fin coupée se voit au déchiffrement (le numéro et l'indicateur de fin sont dans le nonce)."""
    return prefixe + struct.pack('>I', compteur) + (b'\x01' if dernier else b'\x00')


class Ecriture:
    """Flux chiffré : ``write`` reçoit l'archive au fil de l'eau, rien ne s'accumule au-delà d'un bloc. Une clé propre à l'archive est enveloppée
    par la clé du cabinet. Fichier : MAGIC, longueur de l'en-tête (4 octets), en-tête JSON, puis blocs [longueur (4 octets), chiffré + étiquette]."""

    def __init__(self, sortie, cle, kdf, ident, garde=None):
        self.sortie, self.garde = sortie, garde
        self.sha, self.taille, self.compteur, self.tampon, self.ferme = hashlib.sha256(), 0, 0, bytearray(), False
        contenu, enveloppe = os.urandom(32), os.urandom(12)
        self.prefixe = os.urandom(7)
        entete = json.dumps({'format': 1, 'chiffrement': 'AES-256-GCM', 'derivation': {'nom': 'scrypt', **kdf}, 'bloc': BLOC, 'compression': 'gzip', 'contenu': 'tar',
                             'prefixe': base64.b64encode(self.prefixe).decode(), 'cle_enveloppee': base64.b64encode(enveloppe + _aead(cle).encrypt(enveloppe, contenu, MAGIC)).decode(),
                             'id': ident}, sort_keys=True).encode()
        tete = MAGIC + struct.pack('>I', len(entete)) + entete
        self.lie = hashlib.sha256(tete).digest()    # en-tête lié à chaque bloc : le modifier rend l'archive illisible
        self.aead = _aead(contenu)
        self._brut(tete)

    def _brut(self, data):
        self.sortie.write(data)
        self.sha.update(data)
        self.taille += len(data)
        if self.garde:
            self.garde(self.taille)

    def _bloc(self, data, dernier):
        chiffre = self.aead.encrypt(_nonce(self.prefixe, self.compteur, dernier), bytes(data), self.lie)
        self.compteur += 1
        self._brut(struct.pack('>I', len(chiffre)) + chiffre)

    def write(self, data):
        self.tampon += data
        while len(self.tampon) > BLOC:      # le dernier bloc, même vide, porte l'indicateur de fin
            self._bloc(self.tampon[:BLOC], False)
            del self.tampon[:BLOC]
        return len(data)

    def flush(self):
        return None

    def close(self):
        if not self.ferme:
            self.ferme = True
            self._bloc(self.tampon, True)
            self.tampon = bytearray()


class Lecture:
    """Flux déchiffré : ``read`` rend l'archive au fil de l'eau. Accepte la clé du coffre ou la phrase (paramètres de scrypt lus dans l'en-tête, bornés)."""

    def __init__(self, source, cle=None, phrase=None):
        self.source, self.tampon, self.compteur, self.fini = source, b'', 0, False
        tete = source.read(len(MAGIC) + 4)
        if len(tete) < len(MAGIC) + 4 or tete[:len(MAGIC)] != MAGIC:
            raise Stop('archive_inconnue')
        longueur = struct.unpack('>I', tete[len(MAGIC):])[0]
        if longueur > 65536:
            raise Stop('archive_inconnue')
        brut = source.read(longueur)
        try:
            self.entete = json.loads(brut)
            kdf = self.entete['derivation']
            n, r, p = int(kdf['n']), int(kdf['r']), int(kdf['p'])
            sel = base64.b64decode(kdf['sel'])
            enveloppe = base64.b64decode(self.entete['cle_enveloppee'])
            self.prefixe = base64.b64decode(self.entete['prefixe'])
        except (ValueError, KeyError, TypeError):
            raise Stop('archive_inconnue') from None
        if self.entete.get('format') != 1 or n > 2 ** 20 or r > 32 or p > 4 or len(self.prefixe) != 7:
            raise Stop('archive_inconnue')
        self.lie = hashlib.sha256(tete + brut).digest()
        from cryptography.exceptions import InvalidTag
        try:
            contenu = _aead(deriver(phrase, sel, n, r, p) if phrase is not None else cle).decrypt(enveloppe[:12], enveloppe[12:], MAGIC)
        except (InvalidTag, ValueError, TypeError):
            raise Stop('phrase_ou_cle_incorrecte') from None
        self.aead = _aead(contenu)

    def _suivant(self):
        from cryptography.exceptions import InvalidTag
        tete = self.source.read(4)
        if len(tete) < 4:
            raise Stop('archive_tronquee')
        longueur = struct.unpack('>I', tete)[0]
        if longueur > BLOC + 16:
            raise Stop('archive_alteree')
        chiffre = self.source.read(longueur)
        if len(chiffre) < longueur:
            raise Stop('archive_tronquee')
        for dernier in (False, True):
            try:
                clair = self.aead.decrypt(_nonce(self.prefixe, self.compteur, dernier), chiffre, self.lie)
            except InvalidTag:
                continue
            self.compteur += 1
            if dernier:
                if self.source.read(1):
                    raise Stop('archive_alteree')
                self.fini = True
            return clair
        raise Stop('archive_alteree')

    def read(self, taille=-1):
        while (taille < 0 or len(self.tampon) < taille) and not self.fini:
            self.tampon += self._suivant()
        if taille < 0:
            taille = len(self.tampon)
        out, self.tampon = self.tampon[:taille], self.tampon[taille:]
        return out

    def readable(self):
        return True

    def jusqu_a_la_fin(self):
        """La fin authentifiée doit être atteinte : sans cela, une archive coupée pourrait passer pour complète."""
        while not self.fini:
            self._suivant()
        self.tampon = b''


class _Lu:
    """Empreinte de ce qui est lu, sur le fichier chiffré ou sur un membre."""

    def __init__(self, flux):
        self.flux, self.sha, self.taille = flux, hashlib.sha256(), 0

    def read(self, *args):
        data = self.flux.read(*args)
        self.sha.update(data)
        self.taille += len(data)
        return data


# ------------------------------------------------------------------------------------------------ construction de l'archive
def _exclu(rel):
    parties = rel.parts
    if any(p in EXCLUS_DOSSIERS for p in parties[:-1]) or parties[:2] == ('vault', 'sauvegarde5688'):
        return True
    return bool(EXCLUS_FICHIERS.search(parties[-1]))


def _parcours(racine, prefixe, sauter):
    """Fichiers ordinaires d'un dossier ; les liens symboliques ne sont jamais suivis, le dossier de sauvegarde est toujours sauté."""
    racine = Path(racine)
    for base, dossiers, fichiers in os.walk(racine, followlinks=False):
        ici = Path(base)
        rel = ici.relative_to(racine)
        dossiers[:] = sorted(d for d in dossiers if d not in EXCLUS_DOSSIERS and not (ici / d).is_symlink()
                             and (ici / d).resolve() not in sauter)
        for nom in sorted(fichiers):
            chemin = ici / nom
            relatif = PurePosixPath(*rel.parts, nom)
            if chemin.is_symlink() or not chemin.is_file() or _exclu(relatif):
                continue
            yield prefixe + '/' + relatif.as_posix(), chemin


def plan(desk, cfg=None):
    """Ce qui entre dans l'archive : (nom dans l'archive, chemin réel), plus les fichiers illisibles signalés sans arrêter la sauvegarde."""
    from .missions567 import auth_root
    cfg = cfg or config(desk)
    state = Path(desk.c['state_dir']).resolve()
    sauter = {dossier(desk, cfg).resolve()}
    entrees = list(_parcours(state, 'etat', sauter))
    vus = {p.resolve() for _, p in entrees}
    # Base des comptes (comptes5648 dans l'état, users.sqlite3 ici) et, à côté d'elle, secrets5688/ avec sa .master.key : sans eux, tout compte à double
    # authentification serait à réinitialiser après une restauration. Ils ne sortent que dans cette archive chiffrée, jamais dans l'export.
    racine = auth_root()
    if racine.is_dir():         # sous le dossier d'état, déjà parcouru : rien n'est pris deux fois
        entrees += [x for x in _parcours(racine, 'comptes', sauter) if x[1].resolve() not in vus]
    candidats = []
    for dossier_cfg in {Path('/etc/axiorhub-mail-agent'), Path(str(desk.c.get('_path') or '.')).parent}:
        try:
            if str(desk.c.get('_path') or '') and dossier_cfg.is_dir():
                candidats += sorted(p for p in dossier_cfg.iterdir() if p.is_file() and not EXCLUS_FICHIERS.search(p.name) and not p.name.endswith(('.sqlite3', '.sqlite', '.db'))
                                    and p.stat().st_size <= 16 * 1024 * 1024)
        except OSError:
            continue
    if desk.c.get('matters_file'):
        candidats.append(Path(desk.c['matters_file']))
    deja, avertissements = set(vus), []
    for chemin in candidats:
        try:
            reel = chemin.resolve()
            if reel in deja or not reel.is_file():
                continue
            with reel.open('rb'):
                pass
        except OSError:
            avertissements.append('Fichier de configuration illisible par le service : ' + chemin.name)
            continue
        deja.add(reel)
        entrees.append(('config/' + chemin.name, reel))
    if len(entrees) > MAX_FICHIERS:
        raise Stop('sauvegarde_trop_volumineuse')
    return entrees, avertissements


def _est_sqlite(chemin):
    try:
        with open(chemin, 'rb') as f:
            return f.read(16) == b'SQLite format 3\x00'
    except OSError:
        return False


def copie_sqlite(source, cible):
    """Copie cohérente : API de sauvegarde de sqlite3, en lecture seule côté source. Le fichier obtenu tient seul (pas de journal à côté)."""
    src = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    dst = sqlite3.connect(str(cible))
    try:
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
        verdict = dst.execute('PRAGMA quick_check(1)').fetchone()
        dst.commit()
        return bool(verdict and verdict[0] == 'ok')
    finally:
        src.close()
        dst.close()


def _ajouter(tar, nom, flux, taille, mtime):
    info = tarfile.TarInfo(nom)
    info.size, info.mtime, info.mode = taille, int(mtime), 0o600
    lu = _Lu(flux)
    tar.addfile(info, lu)
    return {'chemin': nom, 'taille': taille, 'sha256': lu.sha.hexdigest()}


def _garde_disque(dest):
    seuil = {'prochain': PROGRES_OCTETS}

    def garde(ecrit):
        if ecrit >= seuil['prochain']:
            seuil['prochain'] = ecrit + PROGRES_OCTETS
            if shutil.disk_usage(dest).free < MARGE_DISQUE:
                raise Stop('espace_disque_insuffisant')
    return garde


def ecrire_archive(desk, cible, cle, kdf, ident, entrees, avertissements, verifier=None):
    """Écrit l'archive chiffrée ; rend {'taille', 'sha256', 'fichiers', 'avertissements'}. ``verifier`` est appelé entre deux fichiers (demande d'arrêt)."""
    travail = Path(desk.c['state_dir']) / TRAVAIL
    travail.mkdir(mode=0o700, exist_ok=True)
    manifeste, signales = [], list(avertissements)
    with open(cible, 'wb') as sortie:
        chiffre = Ecriture(sortie, cle, kdf, ident, _garde_disque(Path(cible).parent))
        compresse = gzip.GzipFile(filename='', fileobj=chiffre, mode='wb', compresslevel=3, mtime=0)   # niveau modéré : la nuit, mais sans lourdeur
        tar = tarfile.open(fileobj=compresse, mode='w|', format=tarfile.PAX_FORMAT)
        for nom, chemin in entrees:
            if verifier:
                verifier()
            copie = travail / uuid.uuid4().hex
            try:
                mtime = chemin.stat().st_mtime
                if _est_sqlite(chemin):
                    if not copie_sqlite(chemin, copie):
                        signales.append('Base à examiner (contrôle rapide non satisfait) : ' + nom)
                    with open(copie, 'rb') as flux:
                        manifeste.append({**_ajouter(tar, nom, flux, copie.stat().st_size, mtime), 'type': 'sqlite'})
                    continue
                if chemin.stat().st_size <= FICHIER_MEMOIRE:
                    data = chemin.read_bytes()
                    manifeste.append(_ajouter(tar, nom, io.BytesIO(data), len(data), mtime))
                    continue
                shutil.copyfile(chemin, copie)
                with open(copie, 'rb') as flux:
                    manifeste.append(_ajouter(tar, nom, flux, copie.stat().st_size, mtime))
            except FileNotFoundError:
                continue                      # fichier retiré entre le parcours et la lecture (temporaire d'un autre travail)
            except (PermissionError, sqlite3.DatabaseError):
                if nom == 'etat/desk.sqlite3':     # sans la base principale, une archive ne vaut pas sauvegarde : l'échec doit se voir
                    raise Stop('base_principale_illisible') from None
                signales.append('Fichier non sauvegardé (lecture impossible) : ' + nom)
            finally:
                copie.unlink(missing_ok=True)
        data = json.dumps({'format': 1, 'id': ident, 'cree': desk.now(), 'version': _version(), 'fichiers': manifeste, 'avertissements': signales},
                          ensure_ascii=False, indent=0).encode('utf-8')
        _ajouter(tar, 'manifeste.json', io.BytesIO(data), len(data), datetime.now(timezone.utc).timestamp())
        tar.close()
        compresse.close()
        chiffre.close()
        sortie.flush()
        os.fsync(sortie.fileno())
    return {'taille': chiffre.taille, 'sha256': chiffre.sha.hexdigest(), 'fichiers': len(manifeste), 'avertissements': signales}


def _version():
    from . import __version__
    return __version__


# ------------------------------------------------------------------------------------------------ contrôle et extraction (jamais d'écrasement)
def _nom_sur(nom):
    p = PurePosixPath(nom)
    if p.is_absolute() or '..' in p.parts or not p.parts or (p.parts[0] not in ('etat', 'comptes', 'config') and nom != 'manifeste.json') or '\\' in nom or ':' in p.parts[0]:
        raise Stop('archive_alteree')
    return p


def _membres(lecture):
    """Parcourt le tar déchiffré, membre par membre ; refuse tout ce qui n'est pas un fichier ordinaire à un chemin sûr."""
    compresse = gzip.GzipFile(fileobj=lecture, mode='rb')
    try:
        with tarfile.open(fileobj=compresse, mode='r|') as tar:
            for membre in tar:
                if not membre.isfile():
                    raise Stop('archive_alteree')
                _nom_sur(membre.name)
                yield membre, tar.extractfile(membre)
        while compresse.read(65536):
            pass
    except (OSError, EOFError, tarfile.TarError, ValueError, gzip.BadGzipFile, __import__('zlib').error):
        raise Stop('archive_alteree') from None
    lecture.jusqu_a_la_fin()


def _manifeste(brut):
    try:
        return json.loads(brut)
    except ValueError:
        raise Stop('archive_alteree') from None


def _comparer(vus, manifeste):
    attendus = {f['chemin']: f for f in manifeste.get('fichiers', [])}
    if set(vus) != set(attendus):
        raise Stop('archive_incomplete')
    for nom, (taille, sha) in vus.items():
        if attendus[nom]['sha256'] != sha or attendus[nom]['taille'] != taille:
            raise Stop('archive_alteree')


def controler(chemin, cle=None, phrase=None, empreinte=None, profond=False, travail=None):
    """Déchiffre et vérifie une archive sans rien restaurer ni écraser. ``profond`` ouvre aussi chaque base (copie temporaire supprimée aussitôt)."""
    bases, vus, manifeste = [], {}, None
    with open(chemin, 'rb') as brut:
        source = _Lu(brut)
        lecture = Lecture(source, cle=cle, phrase=phrase)
        for membre, flux in _membres(lecture):
            lu = _Lu(flux)
            if membre.name == 'manifeste.json':
                manifeste = _manifeste(lu.read(64 * 1024 * 1024))
                continue
            tete = lu.read(16)
            if profond and tete == b'SQLite format 3\x00' and travail:
                bases.append(_controle_base(membre.name, tete, lu, travail))
            while lu.read(1024 * 1024):
                pass
            vus[membre.name] = (lu.taille, lu.sha.hexdigest())
        while source.read(1024 * 1024):
            pass
    if manifeste is None:
        raise Stop('archive_incomplete')
    _comparer(vus, manifeste)
    sha = source.sha.hexdigest()
    if empreinte and not hmac.compare_digest(sha, empreinte):
        raise Stop('archive_alteree')
    return {'ok': True, 'fichiers': len(vus), 'taille': source.taille, 'sha256': sha, 'cree': manifeste.get('cree', ''), 'version': manifeste.get('version', ''),
            'avertissements': manifeste.get('avertissements', []), 'bases': bases}


def _controle_base(nom, tete, lu, travail):
    Path(travail).mkdir(mode=0o700, exist_ok=True)
    temp = Path(travail) / uuid.uuid4().hex
    try:
        with open(temp, 'wb') as sortie:
            sortie.write(tete)
            while True:
                data = lu.read(1024 * 1024)
                if not data:
                    break
                sortie.write(data)
        db = sqlite3.connect(temp.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            ok = [r[0] for r in db.execute('PRAGMA integrity_check(1)')] == ['ok']
        finally:
            db.close()
        return {'chemin': nom, 'integre': ok}
    except sqlite3.DatabaseError:
        return {'chemin': nom, 'integre': False}
    finally:
        temp.unlink(missing_ok=True)


def extraire(chemin, dest, cle=None, phrase=None):
    """Extraction dans un dossier neuf ou vide, jamais par-dessus les données : la remise en place reste une décision de l'administrateur."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise Stop('dossier_extraction_non_vide')
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    vus, manifeste = {}, None
    with open(chemin, 'rb') as brut:
        lecture = Lecture(brut, cle=cle, phrase=phrase)
        for membre, flux in _membres(lecture):
            cible = dest.joinpath(*_nom_sur(membre.name).parts)
            cible.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lu = _Lu(flux)
            with open(cible, 'wb') as sortie:
                while True:
                    data = lu.read(1024 * 1024)
                    if not data:
                        break
                    sortie.write(data)
            os.chmod(cible, 0o600)
            if membre.name == 'manifeste.json':
                manifeste = _manifeste(cible.read_bytes())
            else:
                vus[membre.name] = (lu.taille, lu.sha.hexdigest())
    if manifeste is None:
        raise Stop('archive_incomplete')
    _comparer(vus, manifeste)
    return {'ok': True, 'fichiers': len(vus), 'dossier': str(dest)}


# ------------------------------------------------------------------------------------------------ rétention
def _date(nom):
    m = NOM.match(nom)
    if not m:
        return None
    try:
        return datetime.strptime(m[1] + m[2], '%Y%m%d%H%M')
    except ValueError:
        return None


def a_conserver(noms, quotidiennes=7, hebdomadaires=4):
    """Noms gardés : la plus récente de chacun des N derniers jours, puis, parmi les plus anciennes, la plus récente de chacune des M semaines suivantes.
    N + M archives au plus ; jamais vide s'il existe une archive."""
    dates = sorted(((d, n) for n, d in ((n, _date(n)) for n in noms) if d), reverse=True)
    garde, jours, semaines = set(), [], []
    for quand, nom in dates:
        jour, semaine = quand.date(), quand.isocalendar()[:2]
        if jour in jours:
            continue
        if len(jours) < max(1, int(quotidiennes)):
            jours.append(jour)
            garde.add(nom)
        elif semaine not in semaines and len(semaines) < max(0, int(hebdomadaires)):
            semaines.append(semaine)
            garde.add(nom)
    return garde


def purger_local(dest, cfg, courante=''):
    """Rétention locale : seuls les noms exacts d'AxiorHub sont touchés. ``courante`` doit être présente : sinon on ne supprime rien."""
    dest = Path(dest)
    presents = [p for p in dest.iterdir() if p.is_file() and not p.is_symlink()]
    noms = [p.name for p in presents if NOM.match(p.name)]
    retires = []
    garde = a_conserver(noms, cfg['quotidiennes'], cfg['hebdomadaires'])
    if not courante or courante not in noms:
        return retires
    for p in presents:
        if PARTIE.match(p.name) or (NOM.match(p.name) and p.name not in garde and p.name != courante):
            p.unlink(missing_ok=True)
            retires.append(p.name)
    return retires


def _purge_parties(dest):
    for p in Path(dest).iterdir():
        if PARTIE.match(p.name) and p.is_file():
            p.unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------------ réglages
def dossier(desk, cfg=None):
    cfg = cfg or config(desk)
    return Path(cfg['dossier']) if cfg.get('dossier') else Path(desk.c['state_dir']) / MODE_DOSSIER


def config(desk):
    saved = desk.settings(CONFIG, {}) or {}
    return {**DEFAUTS, **saved, 'distant': {**DEFAUTS['distant'], **(saved.get('distant') or {})}}


def _entier(valeur, mini, maxi, code):
    try:
        n = int(str(valeur).strip())
    except (TypeError, ValueError):
        raise Stop(code) from None
    if not mini <= n <= maxi:
        raise Stop(code)
    return n


def _inscriptible(path):
    if not path.is_absolute() or len(path.parts) < 2:
        raise Stop('dossier_sauvegarde_invalide')
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        essai = path / ('.essai-' + uuid.uuid4().hex)
        essai.write_bytes(b'0')
        essai.unlink()
    except OSError:
        raise Stop('dossier_sauvegarde_non_inscriptible') from None


def enregistrer(desk, data, owner):
    """Réglages (hors phrase et identifiants). Le dossier local doit être réellement inscriptible par le service."""
    cfg = config(desk)
    actif = data.get('actif') in (True, 'on', '1', 'true')
    texte = str(data.get('dossier') or '').strip()
    if len(texte) > 500 or any(ord(c) < 32 for c in texte):
        raise Stop('dossier_sauvegarde_invalide')
    nouveau = {**cfg, 'actif': actif, 'heure': _entier(data.get('heure', cfg['heure']), 0, 23, 'heure_sauvegarde_invalide'), 'dossier': texte,
               'quotidiennes': _entier(data.get('quotidiennes', cfg['quotidiennes']), 1, 60, 'retention_invalide'),
               'hebdomadaires': _entier(data.get('hebdomadaires', cfg['hebdomadaires']), 0, 52, 'retention_invalide'),
               'taille_max_go': _entier(data.get('taille_max_go', cfg['taille_max_go']), 1, 2000, 'taille_maximale_invalide')}
    if actif and not phrase_definie(desk):
        raise Stop('phrase_de_sauvegarde_absente')
    _inscriptible(dossier(desk, nouveau))
    if actif and not cfg.get('activee_le'):
        nouveau['activee_le'] = desk.now()
    nouveau['desactivee'] = bool(cfg.get('actif') and not actif) or (bool(cfg.get('desactivee')) and not actif)
    desk.setting(CONFIG, nouveau)
    desk.audit('sauvegarde5688_reglages', {'par': str(owner)[:120], 'actif': actif, 'heure': nouveau['heure'], 'quotidiennes': nouveau['quotidiennes'],
                                           'hebdomadaires': nouveau['hebdomadaires']})
    return {'ok': True, 'message': 'Réglages de sauvegarde enregistrés.'}


# ------------------------------------------------------------------------------------------------ état
def schema(desk):
    desk.db.executescript('''CREATE TABLE IF NOT EXISTS sauvegardes5688(id TEXT PRIMARY KEY, debut TEXT NOT NULL, fin TEXT NOT NULL DEFAULT '', declencheur TEXT NOT NULL,
      statut TEXT NOT NULL, nom TEXT NOT NULL DEFAULT '', taille INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '', fichiers INTEGER NOT NULL DEFAULT 0,
      emplacement TEXT NOT NULL DEFAULT '', distant TEXT NOT NULL DEFAULT '', retires INTEGER NOT NULL DEFAULT 0, erreur TEXT NOT NULL DEFAULT '', avertissements TEXT NOT NULL DEFAULT '[]');
      CREATE TABLE IF NOT EXISTS controles5688(id TEXT PRIMARY KEY, genre TEXT NOT NULL, cible TEXT NOT NULL, par TEXT NOT NULL, debut TEXT NOT NULL, fin TEXT NOT NULL DEFAULT '',
      statut TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}');''')


def _parse(valeur):
    try:
        when = datetime.fromisoformat(str(valeur).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _tz(desk):
    try:
        return ZoneInfo((desk.c.get('calendar') or {}).get('timezone', 'Europe/Paris'))
    except ZoneInfoNotFoundError:
        return timezone.utc


def archives_locales(desk, cfg=None):
    dest = dossier(desk, cfg)
    if not dest.is_dir():
        return []
    rows = [{'nom': p.name, 'taille': p.stat().st_size, 'date': _date(p.name)} for p in dest.iterdir() if NOM.match(p.name) and p.is_file() and not p.is_symlink()]
    return sorted(rows, key=lambda r: r['date'] or datetime.min, reverse=True)


def alerte(desk, now=None):
    """Motif de l'alerte d'Aujourd'hui, ou ''. Une sauvegarde arrêtée par l'administrateur (autre dispositif) ne déclenche pas d'alerte."""
    cfg = config(desk)
    if not cfg['actif'] and cfg.get('desactivee'):
        return ''
    if not cfg['actif'] or not phrase_definie(desk):
        return 'non_configuree'
    now = now or datetime.now(timezone.utc)
    derniere = _parse(desk.settings(REUSSITE, '')) or _parse(cfg.get('activee_le'))
    if derniere and (now - derniere).total_seconds() <= ALERTE_HEURES * 3600:
        return ''
    return 'ancienne'


def etat(desk):
    schema(desk)
    cfg = config(desk)
    derniere = desk.db.execute('SELECT * FROM sauvegardes5688 ORDER BY debut DESC LIMIT 1').fetchone()
    running = desk.db.execute("SELECT 1 FROM jobs WHERE kind=? AND status IN ('pending','running') LIMIT 1", (JOB,)).fetchone()
    return {'config': cfg, 'phrase': phrase_definie(desk), 'derniere': dict(derniere) if derniere else None, 'reussite': desk.settings(REUSSITE, ''),
            'alerte': alerte(desk), 'en_cours': bool(running), 'dossier': str(dossier(desk, cfg)), 'archives': archives_locales(desk, cfg)}


# ------------------------------------------------------------------------------------------------ planification (boucle des automatismes)
def due(desk, now=None):
    cfg = config(desk)
    if not cfg['actif'] or not phrase_definie(desk):
        return False
    now = now or datetime.now(timezone.utc)
    if now.timestamp() - float(desk.settings(ESSAI, 0) or 0) < REESSAI_SECONDES:
        return False
    local = now.astimezone(_tz(desk))
    derniere = _parse(desk.settings(REUSSITE, ''))
    if derniere and derniere.astimezone(_tz(desk)).date() == local.date():
        return False
    retard = not derniere or (now - derniere).total_seconds() >= RATTRAPAGE_HEURES * 3600
    return local.hour >= int(cfg['heure']) or retard


def schedule(desk, now_ts=None):
    """Une tentative par jour (nouvel essai 6 h après un échec). Origine « control » : la pause de l'IA n'arrête pas les sauvegardes, qui n'appellent aucun modèle."""
    now = datetime.fromtimestamp(now_ts, timezone.utc) if now_ts else datetime.now(timezone.utc)
    if not due(desk, now):
        return False
    from . import cadence5662
    derniere = _parse(desk.settings(REUSSITE, ''))
    if cadence5662.busy(desk, now.timestamp()) and derniere and (now - derniere).total_seconds() < RATTRAPAGE_HEURES * 3600:
        return False                       # file chargée : reporté, sauf si la dernière réussite commence à dater
    desk.enqueue(JOB, {'declencheur': 'automatique'}, priority=85, origin='control')
    desk.setting(ESSAI, now.timestamp())
    return True


def lancer(desk, owner):
    cle_active(desk)
    job = desk.enqueue(JOB, {'declencheur': 'manuel'}, priority=20, origin='user')
    desk.audit('sauvegarde5688_lancee', {'par': str(owner)[:120], 'job': job})
    return {'ok': True, 'message': 'Sauvegarde mise en file.', 'job': job}


@contextmanager
def verrou(desk):
    """Sauvegarde, export et contrôle : un seul à la fois. Occupé : le travail est repris plus tard par la file, sans compter d'échec."""
    with open(Path(desk.c['state_dir']) / 'reversibilite5688.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('traitement_deja_en_cours') from None
        yield


# ------------------------------------------------------------------------------------------------ le travail
def _espace(desk, cfg, dest, entrees):
    total, plus_grosse = 0, 0
    for _, chemin in entrees:
        try:
            taille = chemin.stat().st_size
        except OSError:
            continue
        total += taille
        if _est_sqlite(chemin):
            plus_grosse = max(plus_grosse, taille)
    if total > int(cfg['taille_max_go']) * 1024 ** 3:
        raise Stop('sauvegarde_trop_volumineuse')
    row = desk.db.execute("SELECT taille FROM sauvegardes5688 WHERE statut IN ('reussie','partielle') ORDER BY debut DESC LIMIT 1").fetchone()
    estime = int(row['taille'] * 1.3) if row and row['taille'] else total   # archive : pas plus que la source ; plus précis une fois une archive connue
    try:
        if shutil.disk_usage(dest).free < estime + MARGE_DISQUE or shutil.disk_usage(desk.c['state_dir']).free < plus_grosse + MARGE_DISQUE:
            raise Stop('espace_disque_insuffisant')
    except OSError:
        raise Stop('dossier_sauvegarde_non_inscriptible') from None


def _fin(desk, ident, **champs):
    champs['fin'] = desk.now()
    desk.db.execute('UPDATE sauvegardes5688 SET %s WHERE id=?' % ','.join(k + '=?' for k in champs), (*champs.values(), ident))
    desk.db.execute('DELETE FROM sauvegardes5688 WHERE id NOT IN (SELECT id FROM sauvegardes5688 ORDER BY debut DESC LIMIT ?)', (HISTORIQUE,))
    desk.db.commit()


def executer(desk, declencheur='automatique', liaison=None):
    """Sauvegarde complète. Tout ce qui reste à côté en cas d'échec est retiré ; une erreur est visible dans Paramètres et n'efface aucune ancienne archive."""
    cle = cle_active(desk)                 # sans phrase, rien ne démarre : pas même une ligne d'historique
    from .pause5629 import checkpoint
    meta = desk.settings(META_CLE)
    cfg = config(desk)
    with verrou(desk):
        schema(desk)
        desk.db.execute("UPDATE sauvegardes5688 SET statut='interrompue',fin=?,erreur='travail_interrompu' WHERE statut='en_cours'", (desk.now(),))
        ident = uuid.uuid4().hex
        desk.db.execute("INSERT INTO sauvegardes5688(id,debut,declencheur,statut) VALUES (?,?,?,'en_cours')", (ident, desk.now(), str(declencheur)[:40]))
        desk.db.commit()
        dest = dossier(desk, cfg)
        travail = Path(desk.c['state_dir']) / TRAVAIL
        partie = None
        try:
            _inscriptible(dest)
            shutil.rmtree(travail, ignore_errors=True)
            _purge_parties(dest)
            entrees, avertissements = plan(desk, cfg)
            _espace(desk, cfg, dest, entrees)
            nom = 'axiorhub-%s-%s.axbk' % (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M'), uuid.uuid4().hex[:12])
            partie = dest / (nom + '.part')
            kdf = {'n': meta['n'], 'r': meta['r'], 'p': meta['p'], 'sel': meta['sel']}
            ecrit = ecrire_archive(desk, partie, cle, kdf, ident, entrees, avertissements, lambda: checkpoint(desk))
            controler(partie, cle=cle, empreinte=ecrit['sha256'])    # relecture : empreinte du fichier, blocs authentifiés, manifeste interne
            final = dest / nom
            os.replace(partie, final)
            os.chmod(final, 0o600)
            partie = None
        except Stop as error:
            _fin(desk, ident, statut='echec', erreur=str(error).split(':')[0])
            raise
        except (OSError, sqlite3.Error, tarfile.TarError, ValueError) as error:
            _fin(desk, ident, statut='echec', erreur='erreur_disque')
            raise Stop('erreur_disque') from error
        finally:
            if partie is not None:
                Path(partie).unlink(missing_ok=True)
            shutil.rmtree(travail, ignore_errors=True)
        statut, distant, erreur = 'reussie', '', ''
        try:
            retires = purger_local(dest, cfg, nom)     # seulement ici : l'archive du jour existe et a été relue
        except OSError:
            retires, statut, erreur = [], 'partielle', 'retention_impossible'
        if (cfg['distant'] or {}).get('type'):
            from . import stockage5688
            try:
                distant = 'vérifiée : ' + stockage5688.deposer(desk, cfg, final, ecrit['taille'], ecrit['sha256'], nom, liaison)
            except Stop as error:
                statut, distant, erreur = 'partielle', 'échec', 'second_emplacement:' + str(error).split(':')[0]
            except OSError:
                statut, distant, erreur = 'partielle', 'échec', 'second_emplacement:connexion_http_indisponible'
        _fin(desk, ident, statut=statut, nom=nom, taille=ecrit['taille'], sha256=ecrit['sha256'], fichiers=ecrit['fichiers'], emplacement=str(dest), distant=distant,
             retires=len(retires), erreur=erreur, avertissements=json.dumps(ecrit['avertissements'], ensure_ascii=False))
        if statut == 'reussie':
            desk.setting(REUSSITE, desk.now())
        desk.audit('sauvegarde5688_terminee', {'id': ident, 'statut': statut, 'taille': ecrit['taille'], 'sha256': ecrit['sha256'], 'declencheur': str(declencheur)[:40]})
        return {'sauvegarde': nom, 'statut': statut, 'taille': ecrit['taille'], 'sans_controle': True, 'message': 'Sauvegarde vérifiée.' if statut == 'reussie'
                else 'Archive locale vérifiée, second emplacement en échec.'}


def demander_controle(desk, genre, cible, owner):
    """Contrôle en arrière-plan : une archive locale (par son nom exact) ou un export déposé. Aucune phrase ne passe par la file."""
    schema(desk)
    if genre == 'archive' and not NOM.match(str(cible or '')):
        raise Stop('archive_inconnue')
    if genre == 'archive' and not (dossier(desk) / cible).is_file():
        raise Stop('archive_inconnue')
    ident = uuid.uuid4().hex
    desk.db.execute("INSERT INTO controles5688(id,genre,cible,par,debut,statut) VALUES (?,?,?,?,?,'en_attente')", (ident, genre, str(cible), str(owner)[:120], desk.now()))
    desk.db.execute('DELETE FROM controles5688 WHERE id NOT IN (SELECT id FROM controles5688 ORDER BY debut DESC LIMIT 50)')
    desk.db.commit()
    desk.enqueue(CONTROLE, {'controle': ident}, priority=20, origin='user')
    desk.audit('controle5688_demande', {'par': str(owner)[:120], 'genre': genre, 'cible': str(cible)[:120]})
    return {'ok': True, 'id': ident, 'message': 'Contrôle mis en file ; le résultat s’affichera ici.'}


def controle_travail(desk, args):
    """Travail « controle5688 » : lit, déchiffre et vérifie, sans jamais écrire dans les données du cabinet."""
    schema(desk)
    row = desk.db.execute('SELECT * FROM controles5688 WHERE id=?', (str(args.get('controle') or ''),)).fetchone()
    if not row:
        raise Stop('controle_inconnu')
    with verrou(desk):
        try:
            if row['genre'] == 'archive':
                cible = dossier(desk) / row['cible']
                cle = cle_active(desk)
                archive_meta = _entete(cible)
                if archive_meta.get('derivation', {}).get('sel') != desk.settings(META_CLE)['sel']:
                    raise Stop('archive_autre_phrase')
                travail = Path(desk.c['state_dir']) / TRAVAIL
                place = shutil.disk_usage(desk.c['state_dir']).free > 3 * cible.stat().st_size + MARGE_DISQUE
                out = controler(cible, cle=cle, profond=place, travail=travail)
                out['bases_ouvertes'] = place
                if any(not b['integre'] for b in out['bases']):
                    out['ok'] = False
            else:
                from . import export5688
                out = export5688.controle_fichier(desk, row['cible'])
            statut = 'ok' if out.get('ok') else 'anomalie'
        except Stop as error:
            out, statut = {'ok': False, 'erreur': str(error).split(':')[0]}, 'anomalie'
        except OSError:
            out, statut = {'ok': False, 'erreur': 'erreur_disque'}, 'anomalie'
        finally:
            shutil.rmtree(Path(desk.c['state_dir']) / TRAVAIL, ignore_errors=True)
    desk.db.execute('UPDATE controles5688 SET statut=?,fin=?,resultat=? WHERE id=?', (statut, desk.now(), json.dumps(out, ensure_ascii=False), row['id']))
    desk.db.commit()
    return {'controle': row['id'], 'statut': statut, 'sans_controle': True, 'message': 'Contrôle terminé.'}


def _entete(chemin):
    with open(chemin, 'rb') as brut:
        tete = brut.read(len(MAGIC) + 4)
        if len(tete) < len(MAGIC) + 4 or tete[:len(MAGIC)] != MAGIC:
            raise Stop('archive_inconnue')
        try:
            return json.loads(brut.read(min(65536, struct.unpack('>I', tete[len(MAGIC):])[0])))
        except ValueError:
            raise Stop('archive_inconnue') from None


def controles(desk, limite=8):
    schema(desk)
    rows = desk.db.execute('SELECT * FROM controles5688 ORDER BY debut DESC LIMIT ?', (limite,)).fetchall()
    return [{**dict(r), 'resultat': json.loads(r['resultat'] or '{}')} for r in rows]


# ------------------------------------------------------------------------------------------------ ligne de commande (machine neuve, sans interface)
def _phrase_cli(args):
    if '--phrase-fichier' in args:
        return Path(args[args.index('--phrase-fichier') + 1]).read_text(encoding='utf-8').strip()
    import getpass
    return getpass.getpass('Phrase de sauvegarde : ')


def main(argv=None):
    """``python -m agent.sauvegarde5688 controler ARCHIVE`` ou ``extraire ARCHIVE DOSSIER_VIDE`` ; la phrase est demandée au clavier, jamais en argument."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ('controler', 'extraire') or (args[0] == 'extraire' and len(args) < 3):
        print('Usage : python -m agent.sauvegarde5688 controler ARCHIVE | extraire ARCHIVE DOSSIER_VIDE [--phrase-fichier FICHIER]', file=sys.stderr)
        return 2
    try:
        out = controler(args[1], phrase=_phrase_cli(args)) if args[0] == 'controler' else extraire(args[1], args[2], phrase=_phrase_cli(args))
    except (Stop, OSError) as error:
        print('Échec : %s' % str(error).split(':')[0], file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
