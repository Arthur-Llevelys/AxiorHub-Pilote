"""5.6.103 (lot F) : page « Fichiers » — explorateur à deux volets des fichiers du cabinet (Nextcloud partout, disque local en mode poste).

Idées d'ergonomie reprises de Favnyr (gestionnaire de fichiers, GPL-3.0) : AUCUN code repris, seulement les idées (deux volets, onglets,
favoris, renommage par lot avec aperçu, notes et couleurs, propriétés avec empreinte, versions).

Fonctions (services appelés par ``fichiers_web56103``, chacun avec l'acteur ``(compte, rôle)`` et, pour une écriture, une clé d'idempotence) :
lister, chercher (sous-dossiers, bornée et reprenable), proprietes (empreinte SHA-256 à la demande), renommer par lot (aperçu avant/après,
conflits, numérotation de pièces), copier / déplacer (jamais d'écrasement), corbeille (Nextcloud), créer un dossier, notes et couleurs (base
d'AxiorHub, jamais dans les fichiers, partagées au cabinet), favoris (par compte), ZIP (créer, extraire avec contrôle des chemins sortants et
des bombes de décompression), versions (Nextcloud ; disque local surveillé) et restauration conditionnelle, verrou WebDAV facultatif.

Sûreté :
- Chemins : jamais un chemin libre du navigateur sans normalisation (``clean_path`` : ni « .. », ni antislash, ni encodage ambigu) et contrôle
  de racine par la source ; sur le disque local, chemin réel résolu (liens symboliques et jonctions) et contenu dans le dossier choisi.
- Droits : un dossier réservé à d'autres membres (``acces5697.masques``, registre ``membres5650``) ou un chemin hors des racines donne le même
  ``Stop('introuvable')`` (404 neutre) qu'un fichier absent ; un registre illisible donne ``controle_acces_indisponible`` (503), jamais un accès
  élargi. Le contrôle a lieu avant toute lecture, liste, empreinte, note ou écriture.
- Arborescences (5.6.106, audit F1) : une copie, un déplacement, un renommage, une corbeille ou une archive ZIP ne fait jamais sortir une pièce
  d'un dossier réservé, et une racine réservée ou l'un de ses ancêtres ne se copie, ne se déplace, ne se renomme ni ne part à la corbeille
  d'ici ; pour tout le cabinet, administrateur compris, juste avant l'effet et sous le verrou des réservations (voir ``_exiger_arborescence``).
- Écritures : création exclusive (If-None-Match: *), remplacement conditionnel (If-Match sur l'ETag) ; un conflit ne remplace jamais une version
  plus récente : la restauration écrit alors une COPIE DE CONFLIT signalée. Chaque effet passe par ``pause5629.checkpoint`` et le journal d'audit.
- Aucun modèle d'IA ne lit le contenu d'un fichier depuis cette page : aucun appel à un modèle dans ce module.

INTERFACE DE SOURCE (le lot M y branche OneDrive / SharePoint ; rien de Microsoft n'est codé ici)
-----------------------------------------------------------------------------------------------
Une source est un objet ``Source`` (classe de base ci-dessous). Les chemins sont LOGIQUES, au format POSIX (« /Racine/Dossier/fichier.pdf ») ;
la source les traduit vers son stockage et refuse tout chemin hors de ses racines. Entrée normalisée (``entree``) :
``{'chemin', 'nom', 'dossier': bool, 'taille': int, 'modifie': str ISO ou '', 'cree': str, 'etag': str, 'type': str MIME ou ''}``.

    ident, libelle            identifiant court ('nextcloud', 'local', 'onedrive'…) et nom affiché
    capacites()               sous-ensemble de CAPACITES : 'ecriture', 'corbeille', 'versions', 'verrou'
    racines()                 [{'chemin', 'nom'}] — points d'entrée ; tout chemin accepté est sous l'un d'eux
    autoriser(chemin, acteur) chemin normalisé, ou Stop('introuvable') (hors racines, réservé, exclu) — appelé AVANT tout accès
    lister(chemin)            [entree] d'un dossier (un seul niveau)
    proprietes(chemin)        entree + 'auteur' (si connu)
    lire(chemin, limite)      octets ; Stop('fichier_trop_volumineux') au-delà de ``limite``
    ecrire(chemin, octets, etag=None)
                              etag None : création EXCLUSIVE (Stop('deja_existant') si le nom est pris) ; sinon remplacement conditionnel
                              (Stop('version_perimee') si l'ETag a changé). Renvoie le nouvel ETag ('' s'il n'est pas connu).
    creer_dossier(chemin)     Stop('deja_existant') si le nom est pris
    deplacer(chemin, cible, etag)   jamais d'écrasement (Stop('deja_existant')) ; Stop('version_perimee') si l'ETag a changé
    copier(chemin, cible)     jamais d'écrasement
    corbeille(chemin, etag)   suppression récupérable (corbeille du service) ; capacité 'corbeille'
    versions(chemin)          [{'id', 'date', 'auteur', 'taille'}], la plus récente d'abord ; capacité 'versions'
    lire_version(chemin, ident, limite)  octets d'une version
    verrouiller(chemin, acteur) / deverrouiller(chemin, jeton)  {'ok': bool, 'message', 'jeton'} ; capacité 'verrou'

Erreurs : ``Stop`` avec 'introuvable', 'deja_existant', 'version_perimee', 'non_pris_en_charge', 'stockage_injoignable'.
Inscription : ``inscrire_source('onedrive', 'OneDrive', fabrique)`` où ``fabrique(desk, auth, acteur)`` renvoie une ``Source`` ou None (non
configurée pour ce compte). Les services de ce module (droits, idempotence, audit, notes, conflits, ZIP, renommage) s'appliquent tels quels.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat as statmod
import time
import unicodedata
import urllib.parse as U
import zipfile

from .common import Stop, clean_path, under

SERVICE = 'fichiers56103'
CAPACITES = ('ecriture', 'corbeille', 'versions', 'verrou')
LISTE_MAX = 2000                 # entrées rendues par dossier (au-delà : liste tronquée, signalée)
LECTURE_MAX = 100_000_000        # copie entre sources, restauration, ZIP : octets lus au plus
EMPREINTE_MAX = 200_000_000      # empreinte d'un fichier distant (le disque local est lu en flux, sans plafond)
ZIP_ARCHIVE_MAX = 100_000_000    # taille d'une archive lue pour l'extraire
ZIP_TOTAL_MAX = 200_000_000      # taille décompressée totale admise
ZIP_ENTREES_MAX = 1000           # nombre d'entrées admis
ZIP_RATIO_MAX = 100              # taux de compression admis pour une entrée de plus de 1 Mo
ZIP_CREATION_MAX = 1000          # fichiers mis dans une archive créée
LOT_MAX = 500                    # éléments d'un renommage par lot
RECHERCHE_DOSSIERS = 25          # dossiers lus par appel de recherche (la suite se demande avec le curseur)
RECHERCHE_SECONDES = 3.0
RECHERCHE_RESULTATS = 200
RECHERCHE_PROFONDEUR = 12
FAVORIS_MAX = 50
NOTE_MAX = 2000
COULEURS = {'': 'Aucune', 'rouge': 'Rouge', 'orange': 'Orange', 'jaune': 'Jaune', 'vert': 'Vert', 'bleu': 'Bleu', 'violet': 'Violet', 'gris': 'Gris'}
INTERDITS_NOM = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
RESERVES_WINDOWS = {'con', 'prn', 'aux', 'nul'} | {'com%d' % i for i in range(1, 10)} | {'lpt%d' % i for i in range(1, 10)}
# Disque local (mode poste) : réglages par défaut et bornes
LOCAL_DOSSIERS = SERVICE + ':local:dossiers'
LOCAL_REGLAGES = SERVICE + ':local:reglages'
LOCAL_ETAT = SERVICE + ':local:etat'
LOCAL_DEFAUT = {'plafond_mo': 2048, 'jours': 90, 'intervalle': 60}
LOCAL_BORNES = {'plafond_mo': (10, 512_000), 'jours': (1, 3650), 'intervalle': (15, 3600)}
LOCAL_DOSSIERS_MAX = 20
SCRUTATION_FICHIERS = 20_000     # fichiers examinés au plus par passage
SCRUTATION_PROFONDEUR = 12
STABLE_SECONDES = 2              # un fichier modifié il y a moins de 2 s est en cours d'écriture : repris au passage suivant
# Fichiers temporaires de Word, LibreOffice et du système : jamais versionnés ni listés comme documents
TEMPORAIRES = re.compile(r'^(~\$|~WR|\.~lock\.|\.~|~\w+\.tmp$)|(\.tmp|~|\.crdownload|\.part)$|^(thumbs\.db|desktop\.ini|\.ds_store)$', re.I)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS fichiers56103_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL,
  resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS fichiers56103_notes(source TEXT NOT NULL, chemin TEXT NOT NULL, couleur TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  auteur TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(source, chemin));
CREATE TABLE IF NOT EXISTS fichiers56103_verrous(source TEXT NOT NULL, chemin TEXT NOT NULL, jeton TEXT NOT NULL, par TEXT NOT NULL, le TEXT NOT NULL,
  PRIMARY KEY(source, chemin));
CREATE TABLE IF NOT EXISTS fichiers56103_local_etat(racine TEXT NOT NULL, chemin TEXT NOT NULL, mtime_ns INTEGER NOT NULL, taille INTEGER NOT NULL,
  sha256 TEXT NOT NULL DEFAULT '', vu_le TEXT NOT NULL, PRIMARY KEY(racine, chemin));
CREATE TABLE IF NOT EXISTS fichiers56103_local_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, racine TEXT NOT NULL, chemin TEXT NOT NULL, sha256 TEXT NOT NULL,
  taille INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, garde_le TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'changement');
CREATE INDEX IF NOT EXISTS fichiers56103_local_versions_fichier ON fichiers56103_local_versions(racine, chemin, id);
'''


def ensure_schema(desk):
    """Tables du lot (création idempotente au démarrage, mécanisme de ``Desk`` ; jamais pendant un GET)."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _zone(desk):
    from zoneinfo import ZoneInfo
    try:
        from .acces5683 import today_of
        return ZoneInfo(today_of(desk)[1])
    except Exception:   # noqa: BLE001 — fuseau illisible : celui du cabinet par défaut
        return ZoneInfo('Europe/Paris')


def horodatage(desk):
    """« 15-11-2026 14h05 » au fuseau du cabinet (noms d'archive et de copie de conflit)."""
    return datetime.now(_zone(desk)).strftime('%d-%m-%Y %Hh%M')


def _acteur(acteur):
    if isinstance(acteur, (tuple, list)) and len(acteur) == 2 and str(acteur[0] or '').strip() and str(acteur[1] or '').strip():
        return str(acteur[0]).strip()[:200], str(acteur[1]).strip()
    raise Stop('acteur_requis')


# ------------------------------------------------------------------------------------------------------------------- noms
def nom_valide(nom):
    """Nom de fichier ou de dossier acceptable partout (Nextcloud, Windows, Linux) ; Stop('nom_invalide') sinon."""
    nom = unicodedata.normalize('NFC', str(nom or ''))
    if not nom or len(nom) > 200 or nom in ('.', '..') or nom.startswith('.') or nom != nom.strip() or nom.endswith('.'):
        raise Stop('nom_invalide')
    if INTERDITS_NOM.search(nom) or re.search(r'%[0-9a-f]{2}', nom, re.I):
        raise Stop('nom_invalide')
    if nom.split('.')[0].lower() in RESERVES_WINDOWS or nom.lower() in ('secrets', 'mots de passe'):
        raise Stop('nom_invalide')
    return nom


def _parent(chemin):
    return str(PurePosixPath(chemin).parent)


def _nom(chemin):
    return PurePosixPath(chemin).name


def _joindre(dossier, nom):
    return clean_path(dossier.rstrip('/') + '/' + nom)


def nom_conflit(desk, nom, n=1):
    """« Contrat (copie de conflit 15-11-2026 14h05).docx » (puis « … 2 », « … 3 »)."""
    p = PurePosixPath(nom)
    suffixe = '' if n <= 1 else ' %d' % n
    return '%s (copie de conflit %s%s)%s' % (p.stem, horodatage(desk), suffixe, p.suffix)


# ------------------------------------------------------------------------------------------------------------------- sources
class Source:
    """Interface de source (voir l'en-tête du module). Les méthodes non prises en charge lèvent ``Stop('non_pris_en_charge')``."""
    ident = ''
    libelle = ''

    def capacites(self):
        return frozenset()

    def racines(self):
        return []

    def autoriser(self, chemin, acteur):
        try:
            chemin = clean_path(str(chemin or ''))
        except Stop:
            raise Stop('introuvable') from None
        if not any(under(chemin, r['chemin']) for r in self.racines()):
            raise Stop('introuvable')
        return chemin

    def _non(self, *a, **k):
        raise Stop('non_pris_en_charge')

    lister = proprietes = lire = ecrire = creer_dossier = deplacer = copier = corbeille = versions = lire_version = _non

    def verrouiller(self, chemin, acteur):
        return {'ok': False, 'jeton': '', 'message': 'Cette source ne propose pas de verrou : la modification reste protégée par le contrôle de version.'}

    def deverrouiller(self, chemin, jeton):
        return {'ok': False, 'message': 'Cette source ne propose pas de verrou.'}


def entree(chemin, dossier, taille=0, modifie='', cree='', etag='', type_=''):
    return {'chemin': chemin, 'nom': _nom(chemin) or chemin, 'dossier': bool(dossier), 'taille': int(taille or 0), 'modifie': str(modifie or ''),
            'cree': str(cree or ''), 'etag': str(etag or ''), 'type': str(type_ or '')}


def _date_http(valeur):
    """Date WebDAV (RFC 1123) → ISO UTC ; '' si illisible."""
    if not valeur:
        return ''
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(str(valeur))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')
    except (TypeError, ValueError, IndexError):
        return str(valeur)[:40]


def _erreur_stockage(ex):
    """Erreur du stockage → code du lot : absent = introuvable (même réponse qu'un dossier réservé), panne = état « injoignable »."""
    code = str(ex).split(':')[0]
    if code in ('http_404', 'fichier_nextcloud_introuvable', 'repertoire_exclu', 'fichier_hors_racines', 'chemin_refuse', 'href_dav_hors_compte',
                'reponse_dav_hors_dossier', 'chemin_est_un_fichier', 'entree_dav_inaccessible', 'dossier_reserve', 'chemin_hors_espace_du_cabinet'):
        return Stop('introuvable')
    if code in ('connexion_http_indisponible', 'delai_http_depasse', 'http_500', 'http_502', 'http_503', 'http_504', 'xml_invalide', 'xml_refuse',
                'nextcloud_ou_dossier_local_a_configurer'):
        return Stop('stockage_injoignable' if code != 'nextcloud_ou_dossier_local_a_configurer' else code)
    return ex


class SourceNextcloud(Source):
    """Fichiers du cabinet par le client existant (``dav.DAV`` : Nextcloud, autre serveur WebDAV, dossier local de la version serveur ou
    stockage cloud de l'administrateur). Aucun second client : les requêtes passent par son transport, sa validation d'adresse et sa lecture XML.
    Versions, verrou et copie côté serveur : Nextcloud seulement (``DAV`` natif) ; les autres stockages copient par lecture puis écriture."""
    ident = 'nextcloud'
    libelle = 'Nextcloud'

    def __init__(self, desk, dav=None):
        self.desk, self._dav = desk, dav

    @property
    def dav(self):
        if self._dav is None:
            from . import fichiers5670
            try:
                self._dav = fichiers5670.client(self.desk)
            except Stop as ex:
                raise _erreur_stockage(ex) from None
        return self._dav

    def _http(self):
        """Client HTTP du DAV natif (Nextcloud ou WebDAV générique), None pour un dossier local ou un stockage cloud."""
        from .dav import DAV
        return self.dav.http if type(self.dav) is DAV else None

    def _nextcloud(self):
        return self._http() is not None and not getattr(self.dav, 'generic', False)

    def capacites(self):
        out = {'ecriture', 'corbeille'}
        if self._nextcloud():
            out |= {'versions', 'verrou'}
        return frozenset(out)

    def racines(self):
        return [{'chemin': clean_path(r), 'nom': _nom(clean_path(r)) or r} for r in ((self.desk.c.get('nextcloud') or {}).get('roots') or [])]

    def _caches(self, acteur):
        """Chemins des dossiers que l'acteur ne peut pas voir (registre relu à chaque appel ; illisible : Stop, jamais « rien de caché »)."""
        from . import acces5697
        from .common import load_matters
        masques = acces5697.masques(self.desk, acteur)
        if not masques:
            return []
        return [clean_path(m['path']) for m in load_matters(self.desk.c, unfiltered=True) if str(m['id']) in masques and m.get('path')]

    def autoriser(self, chemin, acteur):
        chemin = Source.autoriser(self, chemin, acteur)
        if any(p.startswith('.') or p.casefold() in ('secrets', 'mots de passe') for p in chemin.split('/') if p):
            raise Stop('introuvable')
        if any(under(chemin, h) for h in self._caches(acteur)):
            raise Stop('introuvable')
        return chemin

    def visible(self, chemin, acteur, caches=None):
        caches = self._caches(acteur) if caches is None else caches
        return not any(under(chemin, h) for h in caches)

    def _appel(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Stop as ex:
            raise _erreur_stockage(ex) from None

    def lister(self, chemin):
        lignes = self._appel(self.dav.list_folder, chemin)
        return [entree(x['path'], x['directory'], x.get('size'), _date_http(x.get('modified')), _date_http(x.get('created')), x.get('etag'))
                for x in lignes]

    def proprietes(self, chemin):
        http = self._http()
        if http is None:                                 # dossier local ou stockage cloud : l'entrée est lue dans son dossier parent
            if chemin in [r['chemin'] for r in self.racines()]:
                return {**entree(chemin, True), 'auteur': ''}
            trouve = [x for x in self.lister(_parent(chemin)) if x['chemin'] == chemin]
            if not trouve:
                raise Stop('introuvable')
            return {**trouve[0], 'auteur': ''}
        from .dav import D, OC
        corps = ('<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns"><d:prop><d:resourcetype/><d:getetag/>'
                 '<d:getlastmodified/><d:creationdate/><d:getcontentlength/><d:getcontenttype/><oc:size/><oc:fileid/><oc:owner-display-name/>'
                 '<nc:upload_time/></d:prop></d:propfind>')
        racine = self._appel(self.dav.request_xml, 'PROPFIND', self._appel(self.dav.file_url, chemin), corps, depth='0')
        rep = racine.find(D + 'response')
        if rep is None:
            raise Stop('introuvable')
        for ps in rep.findall(D + 'propstat'):
            if ' 200 ' in ps.findtext(D + 'status', ''):
                prop = ps.find(D + 'prop')
                dossier = prop.find('.//' + D + 'collection') is not None
                taille = prop.findtext(D + 'getcontentlength', '') or prop.findtext(OC + 'size', '0') or '0'
                out = entree(chemin, dossier, int(taille) if str(taille).isdigit() else 0, _date_http(prop.findtext(D + 'getlastmodified', '')),
                             _date_http(prop.findtext(D + 'creationdate', '')), prop.findtext(D + 'getetag', ''), prop.findtext(D + 'getcontenttype', ''))
                out['auteur'] = (prop.findtext(OC + 'owner-display-name', '') or '')[:200]
                out['fileid'] = prop.findtext(OC + 'fileid', '') or ''
                return out
        raise Stop('introuvable')

    def lire(self, chemin, limite):
        http = self._http()
        if http is None:
            meta = self._appel(self.dav.stat, chemin)
            if int(meta.get('size') or 0) > limite:
                raise Stop('fichier_trop_volumineux')
            return self._appel(self.dav.download, meta)
        p = self.proprietes(chemin)
        if p['dossier']:
            raise Stop('chemin_est_un_dossier')
        if p['taille'] > limite:
            raise Stop('fichier_trop_volumineux')
        entetes = {'If-Match': p['etag']} if p['etag'] else {}
        try:
            return self._appel(http.request, 'GET', self.dav.file_url(chemin), None, entetes, limite)
        except Stop as ex:
            if str(ex) == 'reponse_trop_volumineuse':
                raise Stop('fichier_trop_volumineux') from None
            if str(ex) == 'http_412':
                raise Stop('version_perimee') from None
            raise

    def _existe(self, chemin):
        try:
            self.proprietes(chemin)
            return True
        except Stop as ex:
            if str(ex) == 'introuvable':
                return False
            raise

    def ecrire(self, chemin, octets, etag=None):
        if not isinstance(octets, (bytes, bytearray)):
            raise Stop('contenu_fichier_invalide')
        if len(octets) > LECTURE_MAX:
            raise Stop('fichier_trop_volumineux')
        http = self._http()
        if http is None:
            try:
                if etag:
                    self.dav.replace_file(chemin, bytes(octets), etag)
                else:
                    self.dav.put_file(chemin, bytes(octets))
            except Stop as ex:
                code = str(ex)
                if code in ('version_nextcloud_modifiee', 'http_412'):
                    raise Stop('version_perimee' if etag else 'deja_existant') from None
                if code in ('fichier_existant', 'destination_existante'):
                    raise Stop('deja_existant') from None
                raise _erreur_stockage(ex) from None
            return ''
        entetes = {'Content-Type': 'application/octet-stream', **({'If-Match': etag} if etag else {'If-None-Match': '*'})}
        try:
            http.request('PUT', self.dav.file_url(chemin), bytes(octets), entetes, 100_000)
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code == 'http_412':
                raise Stop('version_perimee' if etag else 'deja_existant') from None
            raise _erreur_stockage(ex) from None
        return str((getattr(http, 'last_headers', {}) or {}).get('ETag') or (getattr(http, 'last_headers', {}) or {}).get('Etag') or '')

    def creer_dossier(self, chemin):
        try:
            self.dav.create_folder(chemin)
        except Stop as ex:
            if str(ex) in ('http_405', 'http_412', 'creation_racine_refusee'):
                raise Stop('deja_existant') from None
            raise _erreur_stockage(ex) from None

    def deplacer(self, chemin, cible, etag):
        http = self._http()
        if self._existe(cible):
            raise Stop('deja_existant')
        if http is None:
            p = self.proprietes(chemin)
            if p['dossier']:
                raise Stop('non_pris_en_charge')
            self.ecrire(cible, self.lire(chemin, LECTURE_MAX))
            self.corbeille(chemin, etag)
            return
        entetes = {'Destination': self.dav.file_url(cible), 'Overwrite': 'F', **({'If-Match': etag} if etag else {})}
        try:
            http.request('MOVE', self.dav.file_url(chemin), None, entetes, 100_000)
        except Stop as ex:
            if str(ex).split(':')[0] == 'http_412':
                raise Stop('deja_existant' if self._existe(cible) else 'version_perimee') from None
            raise _erreur_stockage(ex) from None

    def copier(self, chemin, cible):
        http = self._http()
        if self._existe(cible):
            raise Stop('deja_existant')
        if http is None:
            if self.proprietes(chemin)['dossier']:
                raise Stop('non_pris_en_charge')
            self.ecrire(cible, self.lire(chemin, LECTURE_MAX))
            return
        try:
            http.request('COPY', self.dav.file_url(chemin), None, {'Destination': self.dav.file_url(cible), 'Overwrite': 'F', 'Depth': 'infinity'}, 100_000)
        except Stop as ex:
            if str(ex).split(':')[0] == 'http_412':
                raise Stop('deja_existant') from None
            raise _erreur_stockage(ex) from None

    def corbeille(self, chemin, etag):
        if not etag:
            raise Stop('version_perimee')
        try:
            self.dav.trash_file(chemin, etag)
        except Stop as ex:
            if str(ex).split(':')[0] == 'http_412':
                raise Stop('version_perimee') from None
            raise _erreur_stockage(ex) from None

    def _versions_url(self, fileid):
        if not re.fullmatch(r'\d{1,20}', str(fileid or '')):
            raise Stop('non_pris_en_charge')
        return self.dav.http.base + '/remote.php/dav/versions/' + U.quote(self.dav.cfg['username'], safe='') + '/versions/' + str(fileid)

    def versions(self, chemin):
        """Versions Nextcloud (PROPFIND sur remote.php/dav/versions/<compte>/versions/<fileid> ; le nom d'une version est son horodatage)."""
        if not self._nextcloud():
            raise Stop('non_pris_en_charge')
        p = self.proprietes(chemin)
        if p['dossier']:
            raise Stop('chemin_est_un_dossier')
        base = self._versions_url(p.get('fileid'))
        from .dav import D
        NC = '{http://nextcloud.org/ns}'
        corps = ('<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns"><d:prop><d:getlastmodified/><d:getcontentlength/>'
                 '<nc:version-author/><nc:version-label/></d:prop></d:propfind>')
        racine = self._appel(self.dav.request_xml, 'PROPFIND', base, corps)
        origine = U.urlsplit(base)
        out = []
        for rep in racine.findall(D + 'response'):
            href = U.urlsplit(U.urljoin(self.dav.http.base + '/', rep.findtext(D + 'href', '')))
            if (href.scheme, href.netloc) != (origine.scheme, origine.netloc) or not U.unquote(href.path).startswith(U.unquote(origine.path)):
                raise Stop('reponse_versions_hors_fichier')
            vid = U.unquote(href.path).rstrip('/').rsplit('/', 1)[-1]
            if not re.fullmatch(r'\d{1,20}', vid) or vid == str(p.get('fileid')):
                continue
            prop = next((ps.find(D + 'prop') for ps in rep.findall(D + 'propstat') if ' 200 ' in ps.findtext(D + 'status', '')), None)
            if prop is None:
                continue
            date = _date_http(prop.findtext(D + 'getlastmodified', '')) or datetime.fromtimestamp(int(vid), timezone.utc).isoformat(timespec='seconds')
            taille = prop.findtext(D + 'getcontentlength', '0') or '0'
            out.append({'id': vid, 'date': date, 'auteur': (prop.findtext(NC + 'version-author', '') or '')[:200],
                        'libelle': (prop.findtext(NC + 'version-label', '') or '')[:200], 'taille': int(taille) if taille.isdigit() else 0})
        out.sort(key=lambda v: int(v['id']), reverse=True)
        return out

    def lire_version(self, chemin, ident, limite):
        if not self._nextcloud() or not re.fullmatch(r'\d{1,20}', str(ident or '')):
            raise Stop('introuvable')
        if str(ident) not in {v['id'] for v in self.versions(chemin)}:
            raise Stop('introuvable')
        p = self.proprietes(chemin)
        try:
            return self.dav.http.request('GET', self._versions_url(p.get('fileid')) + '/' + str(ident), None, {}, limite)
        except Stop as ex:
            if str(ex) == 'reponse_trop_volumineuse':
                raise Stop('fichier_trop_volumineux') from None
            raise _erreur_stockage(ex) from None

    def verrouiller(self, chemin, acteur):
        """Verrou WebDAV (LOCK exclusif, 30 minutes) si le serveur l'accepte ; sinon on le dit, sans prétendre verrouiller."""
        http = self._http()
        if http is None or not self._nextcloud():
            return Source.verrouiller(self, chemin, acteur)
        proprietaire = 'AxiorHub (%s)' % str(acteur[0])[:80]
        from xml.sax.saxutils import escape
        corps = ('<?xml version="1.0" encoding="utf-8"?><d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope>'
                 '<d:locktype><d:write/></d:locktype><d:owner>%s</d:owner></d:lockinfo>' % escape(proprietaire)).encode('utf-8')
        try:
            http.request('LOCK', self.dav.file_url(chemin), corps, {'Content-Type': 'application/xml; charset=utf-8', 'Timeout': 'Second-1800', 'Depth': '0'}, 100_000)
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code == 'http_423':
                return {'ok': False, 'jeton': '', 'message': 'Fichier déjà verrouillé par une autre personne ou une autre application.'}
            if code in ('http_405', 'http_501', 'http_403', 'http_400', 'http_412', 'http_415'):
                return {'ok': False, 'jeton': '', 'message': 'Le serveur Nextcloud n’accepte pas le verrou WebDAV (application « Temporary files lock » absente ?). '
                                                             'Rien n’est verrouillé ; vos enregistrements restent protégés par le contrôle de version.'}
            raise _erreur_stockage(ex) from None
        jeton = str((getattr(http, 'last_headers', {}) or {}).get('Lock-Token') or '').strip().strip('<>')
        if not jeton:
            return {'ok': False, 'jeton': '', 'message': 'Le serveur n’a pas confirmé le verrou : rien n’est verrouillé.'}
        return {'ok': True, 'jeton': jeton[:300], 'message': 'Fichier verrouillé pour 30 minutes.'}

    def deverrouiller(self, chemin, jeton):
        http = self._http()
        if http is None or not jeton:
            return {'ok': False, 'message': 'Aucun verrou connu sur ce fichier.'}
        try:
            http.request('UNLOCK', self.dav.file_url(chemin), None, {'Lock-Token': '<%s>' % jeton}, 100_000)
        except Stop as ex:
            if str(ex).split(':')[0] in ('http_409', 'http_412', 'http_423', 'http_400'):
                return {'ok': False, 'message': 'Le serveur a refusé de lever ce verrou (expiré ou détenu par une autre session).'}
            raise _erreur_stockage(ex) from None
        return {'ok': True, 'message': 'Verrou levé.'}


class SourceLocale(Source):
    """Disque local du poste (mode poste seulement) : dossiers choisis par l'utilisateur. Chemin logique « /<id>/<chemin relatif> » ;
    chaque accès résout le chemin réel (liens symboliques et jonctions compris) et refuse ce qui sort du dossier choisi."""
    ident = 'local'
    libelle = 'Ce poste'

    def __init__(self, desk, dossiers=None):
        self.desk = desk
        self.dossiers = {d['id']: d for d in (dossiers if dossiers is not None else dossiers_locaux(desk))}

    def capacites(self):
        return frozenset({'ecriture', 'versions'})

    def racines(self):
        return [{'chemin': '/' + i, 'nom': d.get('nom') or Path(d['chemin']).name or d['chemin'], 'surveille': bool(d.get('surveille'))}
                for i, d in self.dossiers.items()]

    def reel(self, chemin):
        """Chemin réel d'un chemin logique, contenu dans son dossier choisi ; Stop('introuvable') sinon (lien sortant, « .. », lecteur)."""
        try:
            chemin = clean_path(str(chemin or ''))
        except Stop:
            raise Stop('introuvable') from None
        parts = [p for p in chemin.split('/') if p]
        if not parts or parts[0] not in self.dossiers:
            raise Stop('introuvable')
        base = os.path.realpath(self.dossiers[parts[0]]['chemin'])
        for p in parts[1:]:
            if ':' in p or p != p.strip() or p.endswith('.'):
                raise Stop('introuvable')
        cible = os.path.realpath(os.path.join(base, *parts[1:])) if parts[1:] else base
        try:
            if os.path.commonpath([os.path.normcase(cible), os.path.normcase(base)]) != os.path.normcase(base):
                raise Stop('introuvable')
        except ValueError:      # autre lecteur sous Windows
            raise Stop('introuvable') from None
        return cible

    def autoriser(self, chemin, acteur):
        chemin = Source.autoriser(self, chemin, acteur)
        self.reel(chemin)
        return chemin

    @staticmethod
    def etag_de(st):
        return 'l%d-%d' % (st.st_mtime_ns, st.st_size)

    def _entree(self, chemin, st):
        dossier = statmod.S_ISDIR(st.st_mode)
        quand = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')
        # 5.6.109 (C09) : « Créé le » = date de naissance du fichier si le système la donne, sinon vide (affichée « — ») ; ``st_ctime``
        # (changement des métadonnées sous Unix) n'est plus présenté comme une création.
        naissance = getattr(st, 'st_birthtime', None)
        naissance = naissance if isinstance(naissance, (int, float)) and not isinstance(naissance, bool) and naissance > 0 else None
        return entree(chemin, dossier, 0 if dossier else st.st_size, quand(st.st_mtime), quand(naissance) if naissance is not None else '',
                      self.etag_de(st))

    def lister(self, chemin):
        reel = self.reel(chemin)
        if not os.path.isdir(reel):
            raise Stop('introuvable')
        out = []
        try:
            with os.scandir(reel) as it:
                for x in it:
                    if x.name.startswith('.') or TEMPORAIRES.search(x.name):
                        continue
                    enfant = _joindre(chemin, x.name) if not INTERDITS_NOM.search(x.name) and not re.search(r'%[0-9a-f]{2}', x.name, re.I) else None
                    if enfant is None:
                        continue
                    try:
                        self.reel(enfant)                     # lien symbolique ou jonction qui sort du dossier : jamais listé
                        out.append(self._entree(enfant, os.stat(x.path)))
                    except (Stop, OSError):
                        continue
                    if len(out) > LISTE_MAX:
                        break
        except PermissionError:
            raise Stop('introuvable') from None
        except OSError:
            raise Stop('stockage_injoignable') from None
        return out

    def proprietes(self, chemin):
        try:
            st = os.stat(self.reel(chemin))
        except OSError:
            raise Stop('introuvable') from None
        out = self._entree(chemin, st)
        out['auteur'] = ''
        return out

    def lire(self, chemin, limite):
        reel = self.reel(chemin)
        if not os.path.isfile(reel):
            raise Stop('introuvable')
        _refuser_base(reel)
        if os.path.getsize(reel) > limite:
            raise Stop('fichier_trop_volumineux')
        with open(reel, 'rb') as f:
            data = f.read(limite + 1)
        if len(data) > limite:
            raise Stop('fichier_trop_volumineux')
        return data

    def ecrire(self, chemin, octets, etag=None):
        parent = self.reel(_parent(chemin))
        nom = nom_valide(_nom(chemin))
        cible = os.path.join(parent, nom)
        if etag is None:
            try:
                with open(cible, 'xb') as f:
                    f.write(bytes(octets))
            except FileExistsError:
                raise Stop('deja_existant') from None
            return self.etag_de(os.stat(cible))
        try:
            courant = self.etag_de(os.stat(cible))
        except OSError:
            raise Stop('version_perimee') from None
        if courant != etag:
            raise Stop('version_perimee')
        tmp = os.path.join(parent, '.~axiorhub-%s.tmp' % secrets.token_hex(6))
        with open(tmp, 'xb') as f:
            f.write(bytes(octets))
            f.flush()
            os.fsync(f.fileno())
        if self.etag_de(os.stat(cible)) != etag:          # changé pendant l'écriture : rien n'est remplacé
            os.unlink(tmp)
            raise Stop('version_perimee')
        os.replace(tmp, cible)
        return self.etag_de(os.stat(cible))

    def creer_dossier(self, chemin):
        parent = self.reel(_parent(chemin))
        try:
            os.mkdir(os.path.join(parent, nom_valide(_nom(chemin))))
        except FileExistsError:
            raise Stop('deja_existant') from None

    def deplacer(self, chemin, cible, etag):
        src = self.reel(chemin)
        dst = os.path.join(self.reel(_parent(cible)), nom_valide(_nom(cible)))
        if os.path.lexists(dst):
            raise Stop('deja_existant')
        if etag and self.etag_de(os.stat(src)) != etag and not os.path.isdir(src):
            raise Stop('version_perimee')
        if os.path.isdir(src) and os.path.commonpath([os.path.normcase(dst), os.path.normcase(src)]) == os.path.normcase(src):
            raise Stop('destination_dans_la_source')
        os.rename(src, dst)

    def copier(self, chemin, cible):
        src = self.reel(chemin)
        dst = os.path.join(self.reel(_parent(cible)), nom_valide(_nom(cible)))
        if os.path.lexists(dst):
            raise Stop('deja_existant')
        if os.path.isdir(src):
            if os.path.commonpath([os.path.normcase(dst), os.path.normcase(src)]) == os.path.normcase(src):
                raise Stop('destination_dans_la_source')
            for _d, _sous, noms in os.walk(src):   # 5.6.109 : une base SQLite du dossier ne serait copiée qu'en l'ouvrant : refus dit
                for n in noms:
                    _refuser_base(n)
            # les liens symboliques ne sont jamais suivis ni recopiés (un lien pourrait désigner un fichier hors du dossier choisi)
            shutil.copytree(src, dst, symlinks=False, ignore=lambda d, noms: [n for n in noms if os.path.islink(os.path.join(d, n))])
            return
        _refuser_base(src)
        with open(src, 'rb') as a, open(dst, 'xb') as b:
            shutil.copyfileobj(a, b, 1 << 20)

    def versions(self, chemin):
        parts = [p for p in clean_path(chemin).split('/') if p]
        racine, rel = parts[0], '/'.join(parts[1:])
        lignes = self.desk.db.execute('SELECT id, garde_le, taille, origine FROM fichiers56103_local_versions WHERE racine=? AND chemin=? ORDER BY id DESC LIMIT 500',
                                      (racine, rel)).fetchall()
        return [{'id': str(r[0]), 'date': r[1], 'auteur': '', 'taille': r[2], 'libelle': ORIGINES.get(r[3], '')} for r in lignes]

    def lire_version(self, chemin, ident, limite):
        parts = [p for p in clean_path(chemin).split('/') if p]
        row = self.desk.db.execute('SELECT sha256, taille FROM fichiers56103_local_versions WHERE id=? AND racine=? AND chemin=?',
                                   (int(ident) if str(ident).isdigit() else -1, parts[0], '/'.join(parts[1:]))).fetchone()
        if not row:
            raise Stop('introuvable')
        if row[1] > limite:
            raise Stop('fichier_trop_volumineux')
        try:
            data = _blob(self.desk, row[0]).read_bytes()
        except OSError:
            raise Stop('version_illisible') from None
        if hashlib.sha256(data).hexdigest() != row[0]:
            raise Stop('version_illisible')
        return data


ORIGINES = {'changement': 'Changement repéré', 'premiere': 'Premier relevé', 'avant_restauration': 'Avant une restauration'}

# Sources ajoutées par d'autres lots (OneDrive / SharePoint : lot M) : ident → (libellé, fabrique(desk, auth, acteur) -> Source | None)
SOURCES = {}


def inscrire_source(ident, libelle, fabrique):
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', str(ident)) or ident in ('nextcloud', 'local'):
        raise ValueError('identifiant de source invalide')
    SOURCES[ident] = (str(libelle), fabrique)


def poste(auth):
    from .integration5634 import workstation
    return workstation({'axiorhub.auth': auth} if isinstance(auth, dict) else None)


def sources(desk, auth, acteur):
    """Sources ouvertes à cet acteur : {ident: Source}. Nextcloud si le stockage est configuré ; le disque local en mode poste seulement."""
    if 'onedrive' not in SOURCES:          # 5.6.103 (intégration) : OneDrive / SharePoint du lot M, ouvert seulement si le compte Microsoft a l'autorisation « fichiers »
        try:
            from . import onedrive56103
            onedrive56103.inscrire()
        except ImportError:
            pass
    out = {}
    cfg = desk.c.get('nextcloud') or {}
    if cfg.get('url') or cfg.get('local_path') or cfg.get('stockage_cloud') or cfg.get('webdav_url'):
        out['nextcloud'] = SourceNextcloud(desk)
    if poste(auth):
        out['local'] = SourceLocale(desk)
    for ident, (libelle, fabrique) in sorted(SOURCES.items()):
        try:
            s = fabrique(desk, auth, acteur)
        except Exception:   # noqa: BLE001 — une source ajoutée en panne ne masque pas les autres
            s = None
        if isinstance(s, Source):
            out[ident] = s
    return out


def ouvrir(desk, auth, acteur, ident, chemin, sources_=None):
    """(source, chemin normalisé et autorisé). Source inconnue ou fermée : même 404 neutre."""
    acteur = _acteur(acteur)
    toutes = sources_ if sources_ is not None else sources(desk, auth, acteur)
    s = toutes.get(str(ident or ''))
    if s is None:
        raise Stop('introuvable')
    return s, s.autoriser(chemin, acteur)


# ------------------------------------------------------------------------------------------------------------------- arborescences réservées
# 5.6.106 (audit 5.6.105, F1) : ``autoriser`` contrôle le chemin demandé (est-il DANS un dossier masqué ?), pas la relation inverse. Or une
# copie, un déplacement, un renommage ou une mise à la corbeille d'un dossier parent est récursif côté Nextcloud (RFC 4918 § 9.8 et 9.9) : il
# emportait le contenu d'un dossier réservé, et le registre des réservations, résolu par le chemin enregistré du dossier, ne suit pas un
# déplacement. Contrôle serveur JUSTE AVANT l'effet, pour tout le cabinet (administrateur compris : il voit tout, mais ne fait pas sortir une
# pièce de son périmètre) :
# - refus d'une opération sur une racine réservée ou sur l'un de ses ancêtres (un déplacement « officiel » d'un dossier protégé demandera une
#   commande dédiée qui garde la protection au nouvel emplacement ; le déplacement générique reste fermé pour ce cas) ;
# - aucune sortie d'une pièce de son périmètre par copie ou déplacement (vers un autre dossier ou une autre source) ;
# - les opérations internes au périmètre restent permises à qui y a droit (``autoriser`` a déjà vérifié l'accès à la source et à la cible).
# Refus neutre : ``Stop('introuvable')``, même 404 qu'un élément absent, aucun nom de dossier réservé. Registre des réservations ou des dossiers
# illisible : ``controle_acces_indisponible`` (503), aucune mutation.
# Concurrence : le contrôle et l'effet se font sous le verrou du registre des réservations (``membres5650._Locked``, entre processus), celui que
# prend toute modification d'une réservation ; une réservation ajoutée, retirée ou déplacée par une autre requête se place donc avant ou après
# l'opération, jamais entre son contrôle et son effet.
def _racines_reservees(source):
    """Chemins des dossiers réservés (à quelqu'un) dans cette source, relus à chaque appel. Seul Nextcloud porte les dossiers du cabinet ; un
    dossier réservé absent du registre des dossiers, ou sans chemin, n'a pas d'emplacement à protéger (même règle que ``_caches``)."""
    if not isinstance(source, SourceNextcloud):
        return ()
    from . import membres5650
    from . import common
    try:
        reserves = membres5650.load(source.desk.c['state_dir'])['reserves']
        if not reserves:
            return ()
        return tuple(sorted({clean_path(m['path']) for m in common.load_matters(source.desk.c, unfiltered=True)
                             if str(m.get('id')) in reserves and m.get('path')}))
    except (Stop, OSError, ValueError, TypeError, KeyError, AttributeError):
        raise Stop('controle_acces_indisponible') from None


def _exiger_arborescence(chemin, reserves, *, cible=None, autre_source=False):
    """Refus neutre si l'opération sur ``chemin`` (vers ``cible`` pour une copie ou un déplacement) toucherait une racine réservée par un de ses
    ancêtres, ou ferait sortir une pièce de son périmètre."""
    for reserve in reserves:
        if under(reserve, chemin):                       # racine réservée elle-même, ou l'un de ses ancêtres : opération récursive refusée
            raise Stop('introuvable')
        if cible is not None and under(chemin, reserve) and (autre_source or not under(cible, reserve)):
            raise Stop('introuvable')                    # sortie du périmètre (autre dossier, autre source)


class _GardeReserves:
    """``with _GardeReserves(source) as reserves`` : verrou du registre des réservations pris (Nextcloud seulement), réservations relues ;
    le contrôle et l'effet se font dans le bloc. Verrou indisponible (registre occupé plus de 5 s) : 503, rien n'est fait."""

    def __init__(self, source):
        self.source, self.verrou = source, None

    def __enter__(self):
        if not isinstance(self.source, SourceNextcloud):
            return ()
        from . import membres5650
        verrou = membres5650._Locked(self.source.desk.c['state_dir'])
        try:
            verrou.__enter__()
        except (Stop, OSError):
            raise Stop('controle_acces_indisponible') from None
        self.verrou = verrou
        try:
            return _racines_reservees(self.source)
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        if self.verrou is not None:
            verrou, self.verrou = self.verrou, None
            verrou.__exit__(*exc)
        return False


# ------------------------------------------------------------------------------------------------------------------- idempotence
def executer(desk, acteur, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409). La commande est
    réservée (validée en base) avant l'effet ; rejouée pendant qu'elle s'exécute ou après une coupure → ``operation_en_cours`` (409)."""
    compte = _acteur(acteur)[0]
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    empreinte = hashlib.sha256(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    row = desk.db.execute('SELECT nature, empreinte, etat, resultat FROM fichiers56103_ops WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row[1] != empreinte:
            raise Stop('cle_reutilisee')
        if row[2] == 'fait':
            return {**json.loads(row[3]), 'rejoue': True}
        if row[2] == 'echec':
            raise Stop(json.loads(row[3]).get('error') or 'operation_echouee')
        raise Stop('operation_en_cours')
    quand = _maintenant()
    desk.db.execute('INSERT INTO fichiers56103_ops VALUES(?,?,?,?,?,?,?,?)', (compte, cle, nature, empreinte, 'en_cours', '{}', quand, quand))
    desk.db.commit()
    try:
        out = fn()
    except Stop as ex:
        desk.db.rollback()
        desk.db.execute("UPDATE fichiers56103_ops SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                        (json.dumps({'error': str(ex).split(':')[0]}), _maintenant(), compte, cle))
        desk.db.commit()
        raise
    except Exception:
        desk.db.rollback()
        desk.db.execute("UPDATE fichiers56103_ops SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                        (json.dumps({'error': 'erreur_interne'}), _maintenant(), compte, cle))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE fichiers56103_ops SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                    (json.dumps(out, ensure_ascii=False, default=str), _maintenant(), compte, cle))
    desk.db.commit()
    return out


def _effet(desk):
    from .pause5629 import checkpoint
    checkpoint(desk)


def _journal(desk, acteur, action, donnees):
    desk.audit('fichiers56103_' + action, {'par': _acteur(acteur)[0], **donnees})


# ------------------------------------------------------------------------------------------------------------------- lecture
def _notes(desk, source, chemins):
    if not chemins:
        return {}
    out = {}
    chemins = list(chemins)
    for i in range(0, len(chemins), 400):
        lot = chemins[i:i + 400]
        for r in desk.db.execute('SELECT chemin, couleur, note, auteur, maj_le, version FROM fichiers56103_notes WHERE source=? AND chemin IN (%s)' % ','.join('?' * len(lot)),
                                 [source] + lot):
            out[r[0]] = {'couleur': r[1], 'note': r[2], 'auteur': r[3], 'le': r[4], 'version': r[5]}
    return out


def _verrous(desk, source, chemins):
    if not chemins:
        return {}
    chemins = list(chemins)[:400]
    return {r[0]: {'par': r[1], 'le': r[2]} for r in desk.db.execute('SELECT chemin, par, le FROM fichiers56103_verrous WHERE source=? AND chemin IN (%s)' % ','.join('?' * len(chemins)),
                                                                       [source] + chemins)}


def fil(source, chemin):
    """Fil d'Ariane : de la racine au dossier affiché."""
    racine = next((r for r in source.racines() if under(chemin, r['chemin'])), None)
    if racine is None:
        return []
    out = [{'chemin': racine['chemin'], 'nom': racine['nom']}]
    reste = chemin[len(racine['chemin']):].strip('/')
    courant = racine['chemin']
    for p in [x for x in reste.split('/') if x]:
        courant = _joindre(courant, p)
        out.append({'chemin': courant, 'nom': p})
    return out


def lister(desk, auth, acteur, ident, chemin='', sources_=None):
    """Contenu d'un dossier (ou racines de la source si ``chemin`` est vide), avec notes, couleurs et verrous ; lecture seule (R05)."""
    acteur = _acteur(acteur)
    toutes = sources_ if sources_ is not None else sources(desk, auth, acteur)
    s = toutes.get(str(ident or ''))
    if s is None:
        raise Stop('introuvable')
    if not str(chemin or '').strip('/'):
        entrees = [{**entree(r['chemin'], True), 'nom': r['nom']} for r in s.racines()]
        if isinstance(s, SourceNextcloud):
            caches = s._caches(acteur)
            entrees = [x for x in entrees if not any(under(x['chemin'], h) for h in caches)]
        return {'source': s.ident, 'chemin': '', 'parent': None, 'fil': [], 'entrees': entrees, 'tronque': False, 'capacites': sorted(s.capacites()),
                'notes': _notes(desk, s.ident, [x['chemin'] for x in entrees]), 'verrous': {}}
    chemin = s.autoriser(chemin, acteur)
    lignes = s.lister(chemin)
    if isinstance(s, SourceNextcloud):
        caches = s._caches(acteur)
        lignes = [x for x in lignes if not any(under(x['chemin'], h) for h in caches)]
    lignes = [x for x in lignes if x['chemin'] != chemin and under(x['chemin'], chemin)]
    lignes.sort(key=lambda x: (not x['dossier'], x['nom'].casefold()))
    tronque = len(lignes) > LISTE_MAX
    lignes = lignes[:LISTE_MAX]
    parent = _parent(chemin)
    try:
        parent = s.autoriser(parent, acteur) if parent != chemin else ''
    except Stop:
        parent = ''
    chemins = [x['chemin'] for x in lignes]
    return {'source': s.ident, 'chemin': chemin, 'parent': parent, 'fil': fil(s, chemin), 'entrees': lignes, 'tronque': tronque,
            'capacites': sorted(s.capacites()), 'notes': _notes(desk, s.ident, chemins + [chemin]), 'verrous': _verrous(desk, s.ident, chemins)}


def _index_pieces(desk, dossier, q, limite):
    """Index des pièces (``documents.sqlite3``, celui de ``fichiers5670.search``) : chemins sous ``dossier`` dont le nom contient ``q``."""
    import sqlite3
    base = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if not base.is_file():
        return None
    echap = lambda v: v.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    try:
        db = sqlite3.connect('file:%s?mode=ro' % base.as_posix(), uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        lignes = db.execute("SELECT DISTINCT path FROM docs WHERE path LIKE ? ESCAPE '\\' AND path LIKE ? ESCAPE '\\' ORDER BY path LIMIT ?",
                            (echap(dossier.rstrip('/')) + '/%', '%' + echap(q) + '%', limite * 4)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return [r[0] for r in lignes]


def chercher(desk, auth, acteur, ident, dossier, q, curseur=None, sources_=None, horloge=time.monotonic):
    """Recherche par nom dans les sous-dossiers, BORNÉE : l'index des pièces d'abord (Nextcloud), puis un parcours limité à
    ``RECHERCHE_DOSSIERS`` dossiers et ``RECHERCHE_SECONDES`` par appel. Le reste du parcours est rendu dans ``curseur`` (chemins relus et
    revalidés à l'appel suivant) : jamais un parcours complet du stockage à chaque frappe ; le navigateur peut abandonner à tout moment."""
    acteur = _acteur(acteur)
    q = unicodedata.normalize('NFC', str(q or '')).strip()
    if len(q) < 2:
        raise Stop('recherche_trop_courte')
    if len(q) > 120:
        raise Stop('recherche_trop_longue')
    s, dossier = ouvrir(desk, auth, acteur, ident, dossier, sources_)
    cf = q.casefold()
    caches = s._caches(acteur) if isinstance(s, SourceNextcloud) else []
    trouves, vus = [], set()
    index_utilise = False
    if not curseur and isinstance(s, SourceNextcloud):
        chemins = _index_pieces(desk, dossier, q, RECHERCHE_RESULTATS)
        if chemins is not None:
            index_utilise = True
            for c in chemins:
                try:
                    c = clean_path(c)
                except Stop:
                    continue
                if not under(c, dossier) or c == dossier or any(under(c, h) for h in caches) or cf not in _nom(c).casefold():
                    continue
                vus.add(c)
                trouves.append({**entree(c, False), 'origine': 'index'})
                if len(trouves) >= RECHERCHE_RESULTATS:
                    break
    attente = []
    for x in (curseur if isinstance(curseur, list) else [[dossier, 0]]):
        try:
            c, prof = clean_path(str(x[0])), int(x[1])
        except (Stop, TypeError, ValueError, IndexError, KeyError):
            continue
        if under(c, dossier) and not any(under(c, h) for h in caches) and 0 <= prof <= RECHERCHE_PROFONDEUR:
            attente.append([c, prof])
    attente = attente[:2000]
    debut, lus = horloge(), 0
    while attente and lus < RECHERCHE_DOSSIERS and horloge() - debut < RECHERCHE_SECONDES and len(trouves) < RECHERCHE_RESULTATS:
        c, prof = attente.pop(0)
        try:
            enfants = s.lister(c)            # bornes de la source revérifiées (racines Nextcloud, chemin réel du poste)
        except Stop as ex:
            if str(ex) == 'stockage_injoignable':
                raise
            continue
        lus += 1
        for e in enfants:
            if not under(e['chemin'], c) or e['chemin'] == c or any(under(e['chemin'], h) for h in caches):
                continue
            if e['dossier'] and prof + 1 <= RECHERCHE_PROFONDEUR:
                attente.append([e['chemin'], prof + 1])
            if cf in e['nom'].casefold() and e['chemin'] not in vus:
                vus.add(e['chemin'])
                trouves.append({**e, 'origine': 'parcours'})
    return {'source': s.ident, 'dossier': dossier, 'q': q, 'resultats': trouves[:RECHERCHE_RESULTATS], 'index': index_utilise,
            'curseur': attente or None, 'partiel': bool(attente), 'dossiers_lus': lus}


def proprietes(desk, auth, acteur, ident, chemin, empreinte=False, sources_=None):
    """Taille, dates, auteur si connu, type ; empreinte SHA-256 calculée seulement à la demande (lecture bornée, jamais pour un modèle)."""
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    out = s.proprietes(chemin)
    if not out.get('type') and not out['dossier']:
        import mimetypes
        out['type'] = mimetypes.guess_type(out['nom'])[0] or ''
    out.pop('fileid', None)
    out['note'] = _notes(desk, s.ident, [chemin]).get(chemin)
    out['verrou'] = _verrous(desk, s.ident, [chemin]).get(chemin)
    out['capacites'] = sorted(s.capacites())
    if empreinte:
        if out['dossier']:
            raise Stop('chemin_est_un_dossier')
        if isinstance(s, SourceLocale) and _base_sqlite(s.reel(chemin)):   # 5.6.109 : empreinte d'état (taille, date), jamais une lecture
            out['sha256'] = ''
            out['empreinte_etat'] = _empreinte_etat(os.stat(s.reel(chemin)))
        elif isinstance(s, SourceLocale):
            h = hashlib.sha256()
            with open(s.reel(chemin), 'rb') as f:
                for bloc in iter(lambda: f.read(1 << 20), b''):
                    h.update(bloc)
            out['sha256'] = h.hexdigest()
        else:
            out['sha256'] = hashlib.sha256(s.lire(chemin, EMPREINTE_MAX)).hexdigest()
    return out


def versions(desk, auth, acteur, ident, chemin, sources_=None):
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    if 'versions' not in s.capacites():
        return {'versions': [], 'disponible': False, 'message': 'Cette source ne conserve pas de versions.'}
    if isinstance(s, SourceLocale) and not s.dossiers[[p for p in chemin.split('/') if p][0]].get('surveille'):
        return {'versions': [], 'disponible': False, 'message': 'Ce dossier du poste n’est pas surveillé : aucune version n’est gardée.'}
    actuel = s.proprietes(chemin)
    return {'versions': s.versions(chemin), 'disponible': True, 'actuel': {k: actuel.get(k) for k in ('etag', 'modifie', 'taille', 'auteur')}}


# ------------------------------------------------------------------------------------------------------------------- écritures
def _etag(valeur):
    valeur = str(valeur or '')
    if len(valeur) > 200 or any(ord(c) < 32 for c in valeur):
        raise Stop('etag_invalide')
    return valeur


def _suivre_notes(desk, source, ancien, nouveau):
    """Les notes suivent un fichier ou un dossier renommé ou déplacé (descendants compris)."""
    desk.db.execute('UPDATE OR REPLACE fichiers56103_notes SET chemin=? WHERE source=? AND chemin=?', (nouveau, source, ancien))
    echap = ancien.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    for (c,) in desk.db.execute("SELECT chemin FROM fichiers56103_notes WHERE source=? AND chemin LIKE ? ESCAPE '\\'", (source, echap + '/%')).fetchall():
        desk.db.execute('UPDATE OR REPLACE fichiers56103_notes SET chemin=? WHERE source=? AND chemin=?', (nouveau + c[len(ancien):], source, c))
    desk.db.execute('DELETE FROM fichiers56103_verrous WHERE source=? AND chemin=?', (source, ancien))
    desk.db.commit()


def creer_dossier(desk, auth, acteur, ident, dossier, nom, cle, sources_=None):
    acteur = _acteur(acteur)
    s, dossier = ouvrir(desk, auth, acteur, ident, dossier, sources_)
    nom = nom_valide(nom)
    cible = s.autoriser(_joindre(dossier, nom), acteur)

    def faire():
        _effet(desk)
        s.creer_dossier(cible)
        _journal(desk, acteur, 'dossier_cree', {'source': s.ident, 'chemin': cible})
        return {'ok': True, 'chemin': cible, 'message': 'Dossier « %s » créé.' % nom}
    return executer(desk, acteur, cle, 'creer_dossier', {'s': s.ident, 'c': cible}, faire)


def transferer(desk, auth, acteur, mode, src_ident, chemin, etag, dst_ident, dossier, cle, sources_=None):
    """Copier ou déplacer un fichier ou un dossier d'un volet à l'autre. Jamais d'écrasement : nom pris → ``deja_existant`` (409).
    Entre deux sources différentes : copie d'un FICHIER seulement (lecture bornée puis création exclusive) ; le déplacement entre sources n'est
    pas proposé (rien n'est supprimé d'une source parce qu'il a été écrit dans une autre)."""
    acteur = _acteur(acteur)
    if mode not in ('copier', 'deplacer'):
        raise Stop('action_invalide')
    toutes = sources_ if sources_ is not None else sources(desk, auth, acteur)
    s, chemin = ouvrir(desk, auth, acteur, src_ident, chemin, toutes)
    d, dossier = ouvrir(desk, auth, acteur, dst_ident, dossier, toutes)
    etag = _etag(etag)
    if s.racines() and chemin in [r['chemin'] for r in s.racines()]:
        raise Stop('racine_non_modifiable')
    cible = d.autoriser(_joindre(dossier, nom_valide(_nom(chemin))), acteur)
    if s.ident == d.ident and (cible == chemin or under(cible, chemin)):
        raise Stop('destination_dans_la_source')

    def faire():
        _effet(desk)
        with _GardeReserves(s) as reserves:              # 5.6.106 (F1) : contrôle de l'arborescence juste avant l'effet, sous le verrou des réservations
            _exiger_arborescence(chemin, reserves, cible=cible, autre_source=s.ident != d.ident)
            if s.ident == d.ident:
                if mode == 'copier':
                    s.copier(chemin, cible)
                else:
                    s.deplacer(chemin, cible, etag)
                    _suivre_notes(desk, s.ident, chemin, cible)
            else:
                if mode == 'deplacer':
                    raise Stop('deplacement_entre_sources')
                p = s.proprietes(chemin)
                if p['dossier']:
                    raise Stop('copie_dossier_entre_sources')
                d.ecrire(cible, s.lire(chemin, LECTURE_MAX))
        _journal(desk, acteur, mode, {'source': s.ident, 'chemin': chemin, 'vers_source': d.ident, 'vers': cible})
        return {'ok': True, 'chemin': cible, 'message': ('Copié dans « %s ».' if mode == 'copier' else 'Déplacé dans « %s ».') % _nom(dossier)}
    return executer(desk, acteur, cle, mode, {'s': s.ident, 'c': chemin, 'e': etag, 'd': d.ident, 'v': cible}, faire)


def corbeille(desk, auth, acteur, ident, chemin, etag, cle, sources_=None):
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    if 'corbeille' not in s.capacites():
        raise Stop('corbeille_indisponible')
    if chemin in [r['chemin'] for r in s.racines()]:
        raise Stop('racine_non_modifiable')
    etag = _etag(etag)

    def faire():
        _effet(desk)
        with _GardeReserves(s) as reserves:              # 5.6.106 (F1) : la corbeille d'un parent emportait le dossier réservé
            _exiger_arborescence(chemin, reserves)
            s.corbeille(chemin, etag)
        desk.db.execute('DELETE FROM fichiers56103_verrous WHERE source=? AND chemin=?', (s.ident, chemin))
        desk.db.commit()
        _journal(desk, acteur, 'corbeille', {'source': s.ident, 'chemin': chemin})
        return {'ok': True, 'message': '« %s » est dans la corbeille de Nextcloud (restaurable depuis Nextcloud).' % _nom(chemin)}
    return executer(desk, acteur, cle, 'corbeille', {'s': s.ident, 'c': chemin, 'e': etag}, faire)


# --- renommage par lot
def _nouveau_nom(nom, regle, rang):
    p = PurePosixPath(nom)
    stem, ext = (p.stem, p.suffix) if not regle.get('_dossier') else (nom, '')
    mode = regle.get('mode')
    if mode == 'remplacer':
        cherche = str(regle.get('cherche') or '')
        if not cherche:
            raise Stop('texte_a_chercher_requis')
        stem = stem.replace(cherche, str(regle.get('remplace') or ''))
    elif mode == 'ajouter':
        stem = str(regle.get('prefixe') or '') + stem + str(regle.get('suffixe') or '')
    elif mode == 'pieces':
        try:
            debut, zeros = int(regle.get('debut') or 1), int(regle.get('zeros') or 0)
        except (TypeError, ValueError):
            raise Stop('numerotation_invalide') from None
        if not (0 <= debut <= 99999 and 0 <= zeros <= 5):
            raise Stop('numerotation_invalide')
        numero = str(debut + rang).zfill(zeros) if zeros else str(debut + rang)
        base = re.sub(r'^Pièce n° \d+(?: – )?', '', stem)        # renuméroter une pièce déjà numérotée ne double pas le préfixe
        stem = 'Pièce n° %s' % numero + ((' – ' + base) if regle.get('garder_nom', True) and base else '')
    else:
        raise Stop('mode_renommage_invalide')
    return unicodedata.normalize('NFC', stem + ext)


def apercu_renommage(desk, auth, acteur, ident, dossier, elements, regle, sources_=None, _s=None):
    """Aperçu avant/après, sans rien écrire. Conflits : nom invalide, nom déjà pris dans le dossier (insensible à la casse), doublon dans le
    lot. L'ordre des éléments est celui choisi dans la page (numérotation des pièces)."""
    acteur = _acteur(acteur)
    s, dossier = _s or ouvrir(desk, auth, acteur, ident, dossier, sources_)
    if not isinstance(elements, list) or not elements:
        raise Stop('selection_vide')
    if len(elements) > LOT_MAX:
        raise Stop('lot_trop_grand')
    if not isinstance(regle, dict):
        raise Stop('mode_renommage_invalide')
    regle = {k: (str(v)[:200] if isinstance(v, str) else v) for k, v in regle.items() if k in ('mode', 'cherche', 'remplace', 'prefixe', 'suffixe', 'debut', 'zeros', 'garder_nom')}
    contenu = {x['nom'].casefold(): x for x in s.lister(dossier)}
    lignes, vus, sources_lot = [], {}, set()
    for x in elements:
        c = s.autoriser(str((x or {}).get('chemin') if isinstance(x, dict) else x), acteur)
        if _parent(c) != dossier:
            raise Stop('selection_hors_dossier')
        sources_lot.add(_nom(c).casefold())
    for rang, x in enumerate(elements):
        c = clean_path(str((x or {}).get('chemin') if isinstance(x, dict) else x))
        avant = _nom(c)
        actuel = contenu.get(avant.casefold())
        ligne = {'chemin': c, 'avant': avant, 'apres': avant, 'etag': str((x or {}).get('etag') or '') if isinstance(x, dict) else '', 'conflit': '', 'message': ''}
        if actuel is None:
            ligne.update(conflit='absent', message='Fichier introuvable dans le dossier (déplacé ou supprimé entre-temps).')
            lignes.append(ligne)
            continue
        try:
            apres = nom_valide(_nouveau_nom(avant, {**regle, '_dossier': actuel['dossier']}, rang))
        except Stop as ex:
            if str(ex) in ('texte_a_chercher_requis', 'numerotation_invalide', 'mode_renommage_invalide'):
                raise
            ligne.update(conflit='nom_invalide', message='Nom obtenu refusé (caractère interdit, trop long, point initial ou final).')
            lignes.append(ligne)
            continue
        ligne['apres'] = apres
        cf = apres.casefold()
        if apres == avant:
            ligne.update(conflit='inchange', message='Nom inchangé.')
        elif cf in vus:
            ligne.update(conflit='doublon_lot', message='Même nom que « %s » dans ce lot.' % vus[cf])
        elif cf in contenu and cf != avant.casefold():
            ligne.update(conflit='nom_pris_lot' if cf in sources_lot else 'nom_pris',
                         message=('Nom porté par un autre élément du lot : renommez en deux temps.' if cf in sources_lot else 'Nom déjà pris dans ce dossier.'))
        vus[cf] = avant
        lignes.append(ligne)
    bloquants = [l for l in lignes if l['conflit'] not in ('', 'inchange')]
    return {'source': s.ident, 'dossier': dossier, 'lignes': lignes, 'conflits': len(bloquants), 'a_renommer': sum(1 for l in lignes if not l['conflit']),
            'ok': not bloquants and any(not l['conflit'] for l in lignes)}


def renommer_lot(desk, auth, acteur, ident, dossier, elements, regle, cle, sources_=None):
    """Applique l'aperçu RECALCULÉ sur le serveur ; au moindre conflit, rien n'est renommé (``conflits_renommage``, 409). Chaque renommage
    est conditionnel (ETag) et sans écrasement ; un échec en cours de lot arrête la suite et le compte rendu dit ce qui a été fait."""
    acteur = _acteur(acteur)
    s, dossier = ouvrir(desk, auth, acteur, ident, dossier, sources_)
    apercu = apercu_renommage(desk, auth, acteur, ident, dossier, elements, regle, _s=(s, dossier))
    if apercu['conflits']:
        ex = Stop('conflits_renommage')
        ex.detail = {'lignes': apercu['lignes']}
        raise ex
    if not apercu['ok']:
        raise Stop('rien_a_renommer')
    a_faire = [l for l in apercu['lignes'] if not l['conflit']]

    def faire():
        faits, echec = [], None
        # 5.6.106 (F1) : tout le lot est contrôlé AVANT le premier renommage (un parent de dossier réservé dans le lot : rien n'est renommé) ;
        # chaque renommage est ensuite recontrôlé sous le verrou des réservations, juste avant son effet.
        reserves = _racines_reservees(s)
        cibles = []
        for l in a_faire:
            cible = s.autoriser(_joindre(dossier, l['apres']), acteur)
            _exiger_arborescence(l['chemin'], reserves, cible=cible)
            cibles.append(cible)
        for l, cible in zip(a_faire, cibles):
            try:
                _effet(desk)
                with _GardeReserves(s) as reserves:
                    _exiger_arborescence(l['chemin'], reserves, cible=cible)
                    s.deplacer(l['chemin'], cible, _etag(l['etag']))
                _suivre_notes(desk, s.ident, l['chemin'], cible)
                faits.append({'avant': l['avant'], 'apres': l['apres']})
            except Stop as ex:
                echec = {'avant': l['avant'], 'raison': str(ex).split(':')[0]}
                break
        _journal(desk, acteur, 'renommage_lot', {'source': s.ident, 'dossier': dossier, 'renommes': len(faits), 'echec': echec,
                                                 'mode': str(regle.get('mode') if isinstance(regle, dict) else '')[:20], 'noms': faits[:50]})
        if echec and not faits:
            raise Stop(echec['raison'])
        msg = '%d élément%s renommé%s.' % (len(faits), 's' if len(faits) > 1 else '', 's' if len(faits) > 1 else '')
        if echec:
            msg += ' Arrêt sur « %s » (%s) : les suivants n’ont pas été renommés.' % (echec['avant'], echec['raison'].replace('_', ' '))
        return {'ok': not echec, 'faits': faits, 'echec': echec, 'message': msg}
    return executer(desk, acteur, cle, 'renommer', {'s': s.ident, 'd': dossier, 'l': [(l['chemin'], l['apres'], l['etag']) for l in a_faire]}, faire)


# --- notes, couleurs, favoris
def noter(desk, auth, acteur, ident, chemin, couleur, note, version, cle, sources_=None):
    """Note et couleur d'un fichier ou d'un dossier, rangées dans la base d'AxiorHub (jamais dans le fichier), partagées au cabinet.
    ``version`` : celle affichée (0 si aucune note) ; une note changée entre-temps → ``version_perimee`` (409), rien n'est écrasé."""
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    couleur = str(couleur or '')
    if couleur not in COULEURS:
        raise Stop('couleur_invalide')
    note = str(note or '').replace('\r\n', '\n').strip()
    if len(note) > NOTE_MAX or any(ord(c) < 32 and c not in '\n\t' for c in note):
        raise Stop('note_invalide')
    try:
        version = int(version or 0)
    except (TypeError, ValueError):
        raise Stop('version_invalide') from None
    s.proprietes(chemin)                           # objet réel (absent : 404 neutre)

    def faire():
        row = desk.db.execute('SELECT version FROM fichiers56103_notes WHERE source=? AND chemin=?', (s.ident, chemin)).fetchone()
        if (row[0] if row else 0) != version:
            raise Stop('version_perimee')
        if not couleur and not note:
            desk.db.execute('DELETE FROM fichiers56103_notes WHERE source=? AND chemin=?', (s.ident, chemin))
            nouvelle = 0
        else:
            nouvelle = version + 1
            desk.db.execute('INSERT OR REPLACE INTO fichiers56103_notes VALUES(?,?,?,?,?,?,?)', (s.ident, chemin, couleur, note, acteur[0], _maintenant(), nouvelle))
        desk.db.commit()
        _journal(desk, acteur, 'note', {'source': s.ident, 'chemin': chemin, 'couleur': couleur, 'longueur_note': len(note)})
        return {'ok': True, 'version': nouvelle, 'couleur': couleur, 'note': note, 'message': 'Note enregistrée.' if nouvelle else 'Note et couleur retirées.'}
    return executer(desk, acteur, cle, 'note', {'s': s.ident, 'c': chemin, 'k': couleur, 'n': note, 'v': version}, faire)


def _cle_favoris(compte):
    return SERVICE + ':favoris:' + hashlib.sha256(str(compte).encode()).hexdigest()[:32]


def favoris(desk, auth, acteur, sources_=None):
    """Favoris du compte, filtrés par les droits ACTUELS (un dossier devenu réservé disparaît de la liste)."""
    acteur = _acteur(acteur)
    toutes = sources_ if sources_ is not None else sources(desk, auth, acteur)
    out = []
    for f in desk.settings(_cle_favoris(acteur[0]), []) or []:
        s = toutes.get(str(f.get('source') or ''))
        if s is None:
            continue
        try:
            out.append({'source': s.ident, 'chemin': s.autoriser(f.get('chemin'), acteur), 'nom': str(f.get('nom') or '')[:200]})
        except Stop as ex:
            if str(ex) == 'controle_acces_indisponible':
                raise
    return out


def favori(desk, auth, acteur, action, ident, chemin, cle, sources_=None):
    acteur = _acteur(acteur)
    if action not in ('ajouter', 'retirer'):
        raise Stop('action_invalide')
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)

    def faire():
        liste = [f for f in (desk.settings(_cle_favoris(acteur[0]), []) or []) if not (f.get('source') == s.ident and f.get('chemin') == chemin)]
        if action == 'ajouter':
            if len(liste) >= FAVORIS_MAX:
                raise Stop('favoris_trop_nombreux')
            liste.append({'source': s.ident, 'chemin': chemin, 'nom': _nom(chemin) or chemin})
        desk.setting(_cle_favoris(acteur[0]), liste)
        return {'ok': True, 'message': 'Ajouté aux favoris.' if action == 'ajouter' else 'Retiré des favoris.'}
    return executer(desk, acteur, cle, 'favori', {'a': action, 's': s.ident, 'c': chemin}, faire)


# --- ZIP
def analyser_zip(raw):
    """Contrôle d'une archive AVANT toute écriture : [(nom relatif, ZipInfo, dossier)] ou ``Stop('archive_refusee:<raison>')``.
    Refus : chemin absolu, lecteur, « .. », nom interdit, lien symbolique, entrée chiffrée, doublon, trop d'entrées, taille décompressée totale
    ou taux de compression excessifs (bombe de décompression)."""
    if len(raw) > ZIP_ARCHIVE_MAX:
        raise Stop('archive_refusee:archive_trop_volumineuse')
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise Stop('archive_refusee:archive_illisible') from None
    if len(infos) > ZIP_ENTREES_MAX:
        raise Stop('archive_refusee:trop_d_entrees')
    total, vus, out = 0, set(), []
    for info in infos:
        nom = info.filename.replace('\\', '/')
        if nom.startswith('/') or re.match(r'^[A-Za-z]:', nom) or '\x00' in nom:
            raise Stop('archive_refusee:chemin_sortant')
        dossier = nom.endswith('/')
        parts = [p for p in nom.split('/') if p != '']
        if not parts or any(p in ('.', '..') for p in parts):
            raise Stop('archive_refusee:chemin_sortant')
        try:
            parts = [nom_valide(p) for p in parts]
        except Stop:
            raise Stop('archive_refusee:nom_interdit') from None
        if statmod.S_ISLNK(info.external_attr >> 16):
            raise Stop('archive_refusee:lien_symbolique')
        if info.flag_bits & 0x1:
            raise Stop('archive_refusee:archive_chiffree')
        rel = '/'.join(parts)
        if rel.casefold() in vus:
            raise Stop('archive_refusee:doublon')
        vus.add(rel.casefold())
        if not dossier:
            total += info.file_size
            if total > ZIP_TOTAL_MAX:
                raise Stop('archive_refusee:trop_volumineuse_decompressee')
            if info.file_size > 1_000_000 and info.file_size > ZIP_RATIO_MAX * max(1, info.compress_size):
                raise Stop('archive_refusee:taux_de_compression_suspect')
        out.append((rel, info, dossier))
    return zf, out


def extraire_zip(desk, auth, acteur, ident, chemin, cle, sources_=None):
    """Extrait une archive dans un NOUVEAU dossier voisin (« Archive », « Archive (2) »…) ; rien n'est écrit si un seul contrôle échoue ;
    chaque fichier est créé exclusivement et relu à la taille annoncée (une entrée qui ment sur sa taille arrête l'extraction)."""
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    if not chemin.lower().endswith('.zip'):
        raise Stop('archive_zip_requise')

    def faire():
        raw = s.lire(chemin, ZIP_ARCHIVE_MAX)
        zf, membres = analyser_zip(raw)
        parent, base = _parent(chemin), PurePosixPath(_nom(chemin)).stem.strip() or 'Archive'
        cible = None
        for n in range(1, 21):
            essai = s.autoriser(_joindre(parent, nom_valide(base if n == 1 else '%s (%d)' % (base, n))), acteur)
            try:
                _effet(desk)
                s.creer_dossier(essai)
                cible = essai
                break
            except Stop as ex:
                if str(ex) != 'deja_existant':
                    raise
        if cible is None:
            raise Stop('destination_occupee')
        crees, ecrits, total = {cible}, 0, 0
        try:
            for rel, info, dossier in membres:
                parts = rel.split('/')
                courant = cible
                for p in (parts if dossier else parts[:-1]):
                    courant = _joindre(courant, p)
                    if courant not in crees:
                        _effet(desk)
                        try:
                            s.creer_dossier(courant)
                        except Stop as ex:
                            if str(ex) != 'deja_existant':
                                raise
                        crees.add(courant)
                if dossier:
                    continue
                with zf.open(info) as f:
                    data = f.read(info.file_size + 1)
                if len(data) != info.file_size:
                    raise Stop('archive_refusee:taille_annoncee_fausse')
                total += len(data)
                if total > ZIP_TOTAL_MAX:
                    raise Stop('archive_refusee:trop_volumineuse_decompressee')
                _effet(desk)
                s.ecrire(_joindre(courant, parts[-1]), data)
                ecrits += 1
        except (Stop, zipfile.BadZipFile, OSError, RuntimeError) as ex:
            code = str(ex).split(':', 1)[-1] if str(ex).startswith('archive_refusee:') else str(ex).split(':')[0]
            _journal(desk, acteur, 'zip_extraction_partielle', {'source': s.ident, 'archive': chemin, 'vers': cible, 'fichiers': ecrits, 'raison': code[:60]})
            return {'ok': False, 'chemin': cible, 'fichiers': ecrits,
                    'message': 'Extraction arrêtée (%s) : %d fichier%s écrit%s dans « %s » ; vérifiez le dossier.' % (
                        code.replace('_', ' '), ecrits, 's' if ecrits > 1 else '', 's' if ecrits > 1 else '', _nom(cible))}
        _journal(desk, acteur, 'zip_extrait', {'source': s.ident, 'archive': chemin, 'vers': cible, 'fichiers': ecrits})
        return {'ok': True, 'chemin': cible, 'fichiers': ecrits, 'message': '%d fichier%s extrait%s dans « %s ».' % (
            ecrits, 's' if ecrits > 1 else '', 's' if ecrits > 1 else '', _nom(cible))}
    return executer(desk, acteur, cle, 'extraire', {'s': s.ident, 'c': chemin}, faire)


def creer_zip(desk, auth, acteur, ident, dossier, chemins, nom, cle, sources_=None):
    """Archive ZIP d'une sélection du dossier affiché (sous-dossiers compris, bornés), créée exclusivement dans ce dossier."""
    acteur = _acteur(acteur)
    s, dossier = ouvrir(desk, auth, acteur, ident, dossier, sources_)
    if not isinstance(chemins, list) or not chemins or len(chemins) > LOT_MAX:
        raise Stop('selection_vide' if not chemins else 'lot_trop_grand')
    choisis = []
    for c in chemins:
        c = s.autoriser(c, acteur)
        if _parent(c) != dossier:
            raise Stop('selection_hors_dossier')
        choisis.append(c)
    nom = str(nom or '').strip() or 'Archive %s' % horodatage(desk)
    nom = nom_valide(nom if nom.lower().endswith('.zip') else nom + '.zip')
    cible = s.autoriser(_joindre(dossier, nom), acteur)
    caches = s._caches(acteur) if isinstance(s, SourceNextcloud) else []

    def _hors_archive(reserves):
        # 5.6.106 (F1) : une archive est une copie ; un dossier réservé n'y entre que si l'archive reste dans son périmètre (administrateur compris)
        return {r for r in reserves if not under(cible, r) and any(under(r, c) or under(c, r) for c in choisis)}

    def faire():
        exclus = _hors_archive(_racines_reservees(s))
        if all(any(under(c, r) for r in exclus) for c in choisis):
            raise Stop('introuvable')                    # rien d'autre que du réservé : aucune archive vide, refus neutre
        tampon, total, n, laisses = io.BytesIO(), 0, 0, set()
        with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as zf:
            attente = [(c, 0) for c in choisis]
            while attente:
                c, prof = attente.pop(0)
                if any(under(c, h) for h in caches):
                    continue
                if any(under(c, r) for r in exclus):
                    laisses.update(r for r in exclus if under(c, r))
                    continue
                p = s.proprietes(c)
                rel = c[len(dossier):].strip('/')
                if p['dossier']:
                    if prof >= RECHERCHE_PROFONDEUR:
                        raise Stop('dossier_trop_profond')
                    zf.writestr(rel + '/', b'')
                    attente.extend((e['chemin'], prof + 1) for e in s.lister(c) if under(e['chemin'], c) and e['chemin'] != c)
                    continue
                n += 1
                if n > ZIP_CREATION_MAX:
                    raise Stop('archive_trop_de_fichiers')
                data = s.lire(c, LECTURE_MAX - total)
                total += len(data)
                zf.writestr(rel, data)
        _effet(desk)
        with _GardeReserves(s) as reserves:
            if _hors_archive(reserves) != exclus:        # réservation changée pendant la lecture : rien n'est écrit, à refaire
                raise Stop('version_perimee')
            s.ecrire(cible, tampon.getvalue())
        _journal(desk, acteur, 'zip_cree', {'source': s.ident, 'chemin': cible, 'fichiers': n, 'octets': total})
        message = 'Archive « %s » créée (%d fichier%s).' % (nom, n, 's' if n > 1 else '')
        vus = [r for r in laisses if not any(under(r, h) for h in caches)]   # dit seulement à qui voit ces dossiers (jamais leur nom)
        if vus:
            message += (' %d dossier réservé laissé hors de l’archive : placée hors de ce dossier, elle en ferait sortir les pièces.' % len(vus) if len(vus) == 1
                        else ' %d dossiers réservés laissés hors de l’archive : placée hors de ces dossiers, elle en ferait sortir les pièces.' % len(vus))
        return {'ok': True, 'chemin': cible, 'fichiers': n, 'message': message}
    return executer(desk, acteur, cle, 'zip', {'s': s.ident, 'd': dossier, 'c': choisis, 'n': nom}, faire)


# --- versions : restauration
def restaurer(desk, auth, acteur, ident, chemin, version_id, etag, cle, sources_=None):
    """Restaure une version en un clic SANS jamais écraser une version plus récente : écriture conditionnelle sur l'ETag affiché. Si le fichier
    a changé depuis l'affichage, la version est écrite dans une COPIE DE CONFLIT à côté du fichier, signalée ; le fichier n'est pas touché.
    Disque local : le contenu actuel est d'abord gardé comme version (rien n'est perdu)."""
    acteur = _acteur(acteur)
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)
    if 'versions' not in s.capacites():
        raise Stop('versions_indisponibles')
    etag = _etag(etag)
    if not etag:
        raise Stop('version_perimee')
    version_id = str(version_id or '')
    if not re.fullmatch(r'\d{1,20}', version_id):
        raise Stop('introuvable')

    def faire():
        contenu = s.lire_version(chemin, version_id, LECTURE_MAX)
        _effet(desk)
        try:
            if isinstance(s, SourceLocale):
                _garder_avant_restauration(desk, s, chemin, etag)
            nouveau = s.ecrire(chemin, contenu, etag)
            if isinstance(s, SourceLocale):
                _etat_apres_ecriture(desk, s, chemin, contenu)
        except Stop as ex:
            if str(ex) != 'version_perimee':
                raise
            for n in range(1, 21):
                copie = s.autoriser(_joindre(_parent(chemin), nom_conflit(desk, _nom(chemin), n)), acteur)
                try:
                    _effet(desk)
                    s.ecrire(copie, contenu)
                    break
                except Stop as ex2:
                    if str(ex2) != 'deja_existant':
                        raise
            else:
                raise Stop('destination_occupee')
            _journal(desk, acteur, 'restauration_conflit', {'source': s.ident, 'chemin': chemin, 'version': version_id, 'copie': copie})
            return {'ok': False, 'conflit': True, 'copie': copie,
                    'message': 'Le fichier a été modifié depuis l’affichage : il n’a pas été remplacé. La version choisie est enregistrée dans « %s ».' % _nom(copie)}
        _journal(desk, acteur, 'restauration', {'source': s.ident, 'chemin': chemin, 'version': version_id})
        return {'ok': True, 'conflit': False, 'etag': nouveau, 'message': 'Version restaurée ; la version remplacée reste dans l’historique.'}
    return executer(desk, acteur, cle, 'restaurer', {'s': s.ident, 'c': chemin, 'v': version_id, 'e': etag}, faire)


# --- verrou
def verrouiller(desk, auth, acteur, ident, chemin, action, cle, sources_=None):
    acteur = _acteur(acteur)
    if action not in ('verrouiller', 'deverrouiller'):
        raise Stop('action_invalide')
    s, chemin = ouvrir(desk, auth, acteur, ident, chemin, sources_)

    def faire():
        _effet(desk)
        if action == 'verrouiller':
            out = s.verrouiller(chemin, acteur)
            if out.get('ok'):
                desk.db.execute('INSERT OR REPLACE INTO fichiers56103_verrous VALUES(?,?,?,?,?)', (s.ident, chemin, out['jeton'], acteur[0], _maintenant()))
                desk.db.commit()
        else:
            row = desk.db.execute('SELECT jeton FROM fichiers56103_verrous WHERE source=? AND chemin=?', (s.ident, chemin)).fetchone()
            out = s.deverrouiller(chemin, row[0] if row else '')
            if out.get('ok') or row:
                desk.db.execute('DELETE FROM fichiers56103_verrous WHERE source=? AND chemin=?', (s.ident, chemin))
                desk.db.commit()
        _journal(desk, acteur, action, {'source': s.ident, 'chemin': chemin, 'ok': bool(out.get('ok'))})
        return {'ok': bool(out.get('ok')), 'message': out.get('message', '')}
    return executer(desk, acteur, cle, action, {'s': s.ident, 'c': chemin}, faire)


# ------------------------------------------------------------------------------------------------------------------- disque local
def dossiers_locaux(desk):
    valeur = desk.settings(LOCAL_DOSSIERS, []) or []
    return [d for d in valeur if isinstance(d, dict) and re.fullmatch(r'p\d{1,4}', str(d.get('id') or '')) and d.get('chemin')][:LOCAL_DOSSIERS_MAX]


def reglages_locaux(desk):
    r = dict(LOCAL_DEFAUT)
    valeur = desk.settings(LOCAL_REGLAGES, {}) or {}
    for k, (a, b) in LOCAL_BORNES.items():
        try:
            r[k] = min(b, max(a, int(valeur.get(k, r[k]))))
        except (TypeError, ValueError):
            pass
    return r


def regler_local(desk, auth, acteur, dossiers, reglages, cle):
    """Dossiers du poste ouverts dans la page (chemin absolu saisi par l'utilisateur du poste, contrôlé : dossier existant, ni racine d'un
    disque, ni dossier d'état d'AxiorHub) et réglages de la surveillance (plafond d'espace, durée de conservation, intervalle)."""
    acteur = _acteur(acteur)
    if not poste(auth):
        raise Stop('introuvable')
    if acteur[1] not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if not isinstance(dossiers, list) or len(dossiers) > LOCAL_DOSSIERS_MAX or not isinstance(reglages, dict):
        raise Stop('reglages_invalides')
    anciens = {d['chemin']: d['id'] for d in dossiers_locaux(desk)}
    etat = os.path.realpath(desk.c['state_dir'])
    propres, ids = [], set(anciens.values())
    for d in dossiers:
        brut = str((d or {}).get('chemin') or '').strip()
        if not brut or len(brut) > 400 or any(ord(c) < 32 for c in brut) or not os.path.isabs(brut):
            raise Stop('dossier_local_invalide')
        reel = os.path.realpath(brut)
        if not os.path.isdir(reel) or os.path.dirname(reel) == reel:
            raise Stop('dossier_local_invalide')
        try:
            if os.path.commonpath([os.path.normcase(reel), os.path.normcase(etat)]) in (os.path.normcase(reel), os.path.normcase(etat)):
                raise Stop('dossier_local_invalide')
        except ValueError:
            pass
        ident = anciens.get(reel)
        if not ident:
            n = 1
            while 'p%d' % n in ids:
                n += 1
            ident = 'p%d' % n
            ids.add(ident)
        nom = str((d or {}).get('nom') or '').strip()[:80] or Path(reel).name
        propres.append({'id': ident, 'chemin': reel, 'nom': nom, 'surveille': bool((d or {}).get('surveille'))})
    if len({p['chemin'] for p in propres}) != len(propres):
        raise Stop('dossier_local_en_double')
    nouveaux = {}
    for k, (a, b) in LOCAL_BORNES.items():
        try:
            v = int(reglages.get(k, reglages_locaux(desk)[k]))
        except (TypeError, ValueError):
            raise Stop('reglages_invalides') from None
        if not a <= v <= b:
            raise Stop('reglages_invalides')
        nouveaux[k] = v

    def faire():
        desk.setting(LOCAL_DOSSIERS, propres)
        desk.setting(LOCAL_REGLAGES, nouveaux)
        _journal(desk, acteur, 'reglages_locaux', {'dossiers': len(propres), 'surveilles': sum(1 for p in propres if p['surveille']), **nouveaux})
        return {'ok': True, 'dossiers': propres, 'reglages': nouveaux, 'message': 'Réglages du poste enregistrés.'}
    return executer(desk, acteur, cle, 'reglages_locaux', {'d': propres, 'r': nouveaux}, faire)


def _blob(desk, sha):
    if not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise Stop('empreinte_invalide')
    return Path(desk.c['state_dir']) / SERVICE / 'versions' / sha[:2] / sha


def _base_sqlite(chemin):
    """5.6.109 (règle du lot Q, ``dav.fichier_sqlite``) : base SQLite ou l'un de ses journaux, d'après le nom seulement."""
    from .dav import fichier_sqlite
    return fichier_sqlite(os.path.basename(str(chemin)))


def _refuser_base(chemin):
    """Une base SQLite d'un dossier du poste n'est jamais ouverte par un descripteur (ouvrir puis refermer le fichier d'une base vivante
    peut annuler les verrous de l'application qui l'utilise et l'endommager) : même code que ``dav.LocalFolder``."""
    if _base_sqlite(chemin):
        raise Stop('fichier_base_sqlite_non_lu')


def _empreinte_etat(st):
    return 'stat-' + hashlib.sha256(('%d|%d' % (st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:26]


def _garder(desk, reel, racine, rel, st, origine='changement', plafond=None):
    """Copie du fichier dans la réserve des versions (empreinte calculée pendant la copie : une seule lecture) ; renvoie l'empreinte."""
    dossier = Path(desk.c['state_dir']) / SERVICE / 'versions' / 'tmp'
    dossier.mkdir(parents=True, exist_ok=True)
    _refuser_base(reel)
    tmp = dossier / ('%s.part' % secrets.token_hex(8))
    h, n = hashlib.sha256(), 0
    try:
        with open(reel, 'rb') as a, open(tmp, 'xb') as b:
            for bloc in iter(lambda: a.read(1 << 20), b''):
                n += len(bloc)
                if plafond is not None and n > plafond:
                    raise Stop('version_trop_volumineuse')
                h.update(bloc)
                b.write(bloc)
        sha = h.hexdigest()
        cible = _blob(desk, sha)
        cible.parent.mkdir(parents=True, exist_ok=True)
        if cible.exists():
            tmp.unlink()
        else:
            os.replace(tmp, cible)
    finally:
        if tmp.exists():
            tmp.unlink()
    desk.db.execute('INSERT INTO fichiers56103_local_versions(racine, chemin, sha256, taille, mtime_ns, garde_le, origine) VALUES(?,?,?,?,?,?,?)',
                    (racine, rel, sha, n, st.st_mtime_ns, _maintenant(), origine))
    return sha


def _garder_avant_restauration(desk, s, chemin, etag):
    reel = s.reel(chemin)
    st = os.stat(reel)
    if s.etag_de(st) != etag:
        raise Stop('version_perimee')
    parts = [p for p in chemin.split('/') if p]
    _garder(desk, reel, parts[0], '/'.join(parts[1:]), st, 'avant_restauration')
    desk.db.commit()


def _etat_apres_ecriture(desk, s, chemin, contenu):
    parts = [p for p in chemin.split('/') if p]
    st = os.stat(s.reel(chemin))
    desk.db.execute('INSERT OR REPLACE INTO fichiers56103_local_etat VALUES(?,?,?,?,?,?)',
                    (parts[0], '/'.join(parts[1:]), st.st_mtime_ns, st.st_size, hashlib.sha256(contenu).hexdigest(), _maintenant()))
    desk.db.commit()


def scruter(desk, maintenant=None):
    """Un passage de surveillance des dossiers du poste marqués « surveillés » (scrutation légère : date et taille, empreinte seulement si
    elles ont changé). Une version est gardée au premier relevé puis à chaque changement de contenu, même fait dans Word (fichiers
    temporaires ignorés ; un fichier encore en cours d'écriture est repris au passage suivant). Puis plafond et durée de conservation."""
    maintenant = time.time() if maintenant is None else maintenant
    plafond = reglages_locaux(desk)['plafond_mo'] * 1_000_000
    bilan = {'examines': 0, 'versions': 0, 'trop_gros': 0, 'erreurs': 0, 'en_cours': 0, 'bases_non_lues': 0}
    for d in dossiers_locaux(desk):
        if not d.get('surveille'):
            continue
        base = os.path.realpath(d['chemin'])
        if not os.path.isdir(base):
            bilan['erreurs'] += 1
            continue
        connus = {r[0]: r for r in desk.db.execute('SELECT chemin, mtime_ns, taille, sha256 FROM fichiers56103_local_etat WHERE racine=?', (d['id'],))}
        derniers = {r[0]: r[1] for r in desk.db.execute(
            'SELECT chemin, sha256 FROM fichiers56103_local_versions v WHERE racine=? AND id=(SELECT MAX(id) FROM fichiers56103_local_versions w WHERE w.racine=v.racine AND w.chemin=v.chemin)',
            (d['id'],))}
        attente = [(base, '', 0)]
        while attente and bilan['examines'] < SCRUTATION_FICHIERS:
            reel_dir, rel_dir, prof = attente.pop()
            try:
                entrees = list(os.scandir(reel_dir))
            except OSError:
                bilan['erreurs'] += 1
                continue
            for x in entrees:
                if x.name.startswith('.') or TEMPORAIRES.search(x.name) or x.is_symlink():
                    continue
                rel = (rel_dir + '/' + x.name).strip('/')
                try:
                    if x.is_dir(follow_symlinks=False):
                        if prof < SCRUTATION_PROFONDEUR:
                            attente.append((x.path, rel, prof + 1))
                        continue
                    if not x.is_file(follow_symlinks=False):
                        continue
                    st = x.stat(follow_symlinks=False)
                except OSError:
                    bilan['erreurs'] += 1
                    continue
                bilan['examines'] += 1
                ancien = connus.get(rel)
                if ancien and ancien[1] == st.st_mtime_ns and ancien[2] == st.st_size:
                    continue
                if maintenant - st.st_mtime < STABLE_SECONDES:
                    bilan['en_cours'] += 1
                    continue
                if _base_sqlite(x.name):   # 5.6.109 : base SQLite ou journal : relevé sur taille et date, jamais ouvert ni versionné
                    bilan['bases_non_lues'] += 1
                    desk.db.execute('INSERT OR REPLACE INTO fichiers56103_local_etat VALUES(?,?,?,?,?,?)',
                                    (d['id'], rel, st.st_mtime_ns, st.st_size, _empreinte_etat(st), _maintenant()))
                    continue
                if st.st_size > plafond:
                    bilan['trop_gros'] += 1
                    desk.db.execute('INSERT OR REPLACE INTO fichiers56103_local_etat VALUES(?,?,?,?,?,?)', (d['id'], rel, st.st_mtime_ns, st.st_size, '', _maintenant()))
                    continue
                try:
                    h = hashlib.sha256()
                    with open(x.path, 'rb') as f:
                        for bloc in iter(lambda: f.read(1 << 20), b''):
                            h.update(bloc)
                    sha = h.hexdigest()
                    if derniers.get(rel) != sha:
                        sha = _garder(desk, x.path, d['id'], rel, st, 'changement' if rel in derniers else 'premiere', plafond)
                        derniers[rel] = sha
                        bilan['versions'] += 1
                    desk.db.execute('INSERT OR REPLACE INTO fichiers56103_local_etat VALUES(?,?,?,?,?,?)', (d['id'], rel, st.st_mtime_ns, st.st_size, sha, _maintenant()))
                except (OSError, Stop):
                    bilan['erreurs'] += 1
            desk.db.commit()
    bilan.update(retention(desk, maintenant))
    desk.setting(LOCAL_ETAT, {'le': datetime.fromtimestamp(maintenant, timezone.utc).isoformat(timespec='seconds'), **bilan})
    return bilan


def retention(desk, maintenant=None):
    """Durée de conservation puis plafond d'espace. La version la plus récente de chaque fichier est toujours gardée ; au-delà, les plus
    anciennes partent d'abord. Les copies que plus aucune version ne désigne sont effacées de la réserve."""
    maintenant = time.time() if maintenant is None else maintenant
    r = reglages_locaux(desk)
    limite = datetime.fromtimestamp(maintenant - r['jours'] * 86400, timezone.utc).isoformat(timespec='seconds')
    recentes = 'id NOT IN (SELECT MAX(id) FROM fichiers56103_local_versions GROUP BY racine, chemin)'
    expirees = desk.db.execute('DELETE FROM fichiers56103_local_versions WHERE garde_le<? AND ' + recentes, (limite,)).rowcount
    plafond, retirees = r['plafond_mo'] * 1_000_000, 0
    while True:
        total = desk.db.execute('SELECT COALESCE(SUM(taille),0) FROM (SELECT sha256, MAX(taille) AS taille FROM fichiers56103_local_versions GROUP BY sha256)').fetchone()[0]
        if total <= plafond:
            break
        row = desk.db.execute('SELECT id FROM fichiers56103_local_versions WHERE ' + recentes + ' ORDER BY id LIMIT 1').fetchone()
        if not row:
            break                                      # il ne reste que les versions les plus récentes : elles sont gardées
        desk.db.execute('DELETE FROM fichiers56103_local_versions WHERE id=?', (row[0],))
        retirees += 1
    desk.db.commit()
    utiles = {x[0] for x in desk.db.execute('SELECT DISTINCT sha256 FROM fichiers56103_local_versions')}
    reserve = Path(desk.c['state_dir']) / SERVICE / 'versions'
    effaces = 0
    if reserve.is_dir():
        for sous in reserve.iterdir():
            if not sous.is_dir() or sous.name == 'tmp':
                continue
            for f in sous.iterdir():
                if f.name not in utiles and re.fullmatch(r'[0-9a-f]{64}', f.name):
                    try:
                        f.unlink()
                        effaces += 1
                    except OSError:
                        pass
    return {'expirees': expirees, 'retirees_plafond': retirees, 'copies_effacees': effaces}


def maintenance(desk, stamp=None):
    """Appelée par la maintenance (``activity431``) : un passage de surveillance quand l'intervalle réglé est écoulé, en mode poste seulement
    (aucun dossier surveillé : rien n'est fait)."""
    stamp = time.time() if stamp is None else stamp
    if not any(d.get('surveille') for d in dossiers_locaux(desk)):
        return None
    dernier = desk.settings(SERVICE + ':local:dernier', 0) or 0
    if stamp - float(dernier) < reglages_locaux(desk)['intervalle']:
        return None
    desk.setting(SERVICE + ':local:dernier', stamp)
    return scruter(desk, stamp)


def etat_local(desk):
    """État de la surveillance pour l'affichage (lecture seule)."""
    etat = desk.settings(LOCAL_ETAT, {}) or {}
    occupe = desk.db.execute('SELECT COALESCE(SUM(taille),0), COUNT(*) FROM (SELECT sha256, MAX(taille) AS taille FROM fichiers56103_local_versions GROUP BY sha256)').fetchone()
    return {**etat, 'octets': occupe[0], 'copies': occupe[1], 'reglages': reglages_locaux(desk)}
