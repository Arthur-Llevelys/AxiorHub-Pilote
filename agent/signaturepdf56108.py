"""5.6.108 (lot Z) : SignaturePDF, service facultatif de signature par lien sur la version serveur.

Décision du cabinet (18/11/2026) : SignaturePDF (logiciel libre de 24ème, AGPL-3.0, application web PHP) s'installe **à côté** d'AxiorHub,
sur un sous-domaine HTTPS du serveur du cabinet (guide ``docs/INSTALLATION-SIGNATUREPDF.md``). Il ne s'intègre pas dans les pages
d'AxiorHub : AxiorHub y dépose un PDF du dossier, donne le lien de signature partagée, puis range le PDF signé dans le dossier.

**Mécanisme d'échange, établi d'après le code public de SignaturePDF** (dépôt 24eme/signaturepdf, ``app.php`` et
``lib/ApiController.class.php``) ; rien n'est supposé au-delà :

- ``POST /api/share/new`` (multipart : fichier ``pdf`` de type ``application/pdf``, champ ``duration`` au format des durées de conservation
  proposées par SignaturePDF, par exemple ``+1 month``) → **201** et JSON ``{hash, symmetrickey, adminkey, url}`` ; 400 si la durée ou le
  fichier manque, 500 si le dossier de partage (``PDF_STORAGE_PATH``) est absent ou non accessible en écriture ;
- ``GET /api/share/get/{hash}/{symmetrickey}`` → le PDF **compilé** (calques de signature appliqués), 403 si la clé est fausse ;
- ``GET /signature/{hash}/nblayers`` → nombre de calques de signature (texte) ;
- ``GET /api/share/delete/{hash}/{adminkey}`` → suppression du partage (clé d'administration).

Cette interface n'est **pas documentée** par l'éditeur (absente du README et du guide d'installation) et elle a été réorganisée en 2026 :
elle n'est utilisée que si « Tester la connexion » la trouve (``GET /api/share/new`` répond 405 : la route existe et n'accepte que POST ;
aucun effet). Sinon, ou sur choix de l'administrateur, le parcours reste **manuel** : AxiorHub ouvre SignaturePDF dans un nouvel onglet,
l'avocat y téléverse le PDF téléchargé depuis AxiorHub et crée le lien lui-même, puis « Déposer le PDF signé » ramène le résultat.

Règles : connecteur réglé et testé par l'administrateur (HTTPS ; HTTP seulement vers l'adresse de bouclage, instance locale d'un poste dont
le lien ne s'ouvre que sur cet ordinateur) ; dépôt sur action explicite d'un avocat (rôle avocat ou administrateur, droit « envoyer ») ;
dossier réservé et document absent : même 404 neutre ; politique des données relue au moment du dépôt (outil ``signature_lien``, finalité
``document_tiers`` : une correspondance confidentielle entre avocats est refusée).

5.6.108, complément du 19/11 (postes Windows 11 et Ubuntu 24.04) : le bouton existe aussi sur un poste (cas type : le poste dépose sur
l'instance HTTPS du serveur du cabinet). L'administrateur coche « instance du cabinet » (jamais déduite de l'adresse). Instance du cabinet :
destination ``serveur_cabinet`` (locale). Instance **non marquée** : traitée comme un fournisseur externe (destination ``signaturepdf_tiers``,
mode local strict : refus ; registre des prestataires si le cabinet l'exige), avertissement à chaque dépôt (« le PDF est déposé tel quel chez
un tiers ; il ne peut pas être pseudonymisé ») et seconde confirmation ; correspondance confidentielle et dossier réservé toujours refusés.

Suite : **AxiorHub n'envoie le lien à personne** : l'avocat le copie et l'adresse lui-même. Les clés du
partage (lecture et administration) sont au coffre chiffré du serveur, jamais dans la base ni le journal ; la clé d'administration ne sort
jamais ; le lien complet (qui porte la clé de lecture) n'est rendu qu'à un compte qui voit le dossier (page de la demande, réponse du dépôt). Le PDF signé est rangé à côté de l'original, qui n'est jamais modifié, et inscrit au
registre des documents comme **nouvelle version** du même document quand l'original y figure (sinon comme document lié). Toute écriture est
un POST (CSRF, corps borné, clé d'idempotence, version attendue → 409) ; aucune écriture pendant un GET.
"""
from datetime import datetime, timezone
from html import escape as _e
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .common import NoRedirect, Stop, clean_path, load_matters, under

PREFIXE = 'm56108/signaturepdf/'
PAGE = '/signature-par-lien'
SETTING = 'signaturepdf56108:reglages'
GUIDE_PATH = 'docs/INSTALLATION-SIGNATUREPDF.md'
LECTURES_GET = ('etat', 'pdf')
ECRITURES = ('reglages', 'tester', 'preparer', 'recuperer', 'deposer', 'clore')
ADMIN = ('reglages', 'tester')
ROLES = ('administrateur', 'avocat', 'assistant')
SIGNATAIRES = ('administrateur', 'avocat')          # dépôt, récupération et clôture : un avocat (jamais un automatisme)
MAX_POST = 8_000
MAX_PDF = 24 * 1024 * 1024                          # limite par défaut de SignaturePDF (upload_max_filesize = 24M)
DELAI = 30
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
HASH = re.compile(r'^[a-z0-9]{8,128}$')
CLE_PARTAGE = re.compile(r'^[A-Za-z0-9]{8,128}$')
# Durées de conservation proposées par SignaturePDF (``config.ini.example``, section [signature], et le formulaire de partage).
DUREES = (('+1 hour', 'une heure'), ('+1 day', 'un jour'), ('+1 week', 'une semaine'), ('+1 month', 'un mois'), ('+6 month', 'six mois'),
          ('+1 year', 'un an'))
DUREE_DEFAUT = '+1 month'
ECHANGES = {'auto': 'Automatique si le service le permet (dépôt et récupération par AxiorHub)', 'manuel': 'Manuel (téléversement à la main dans SignaturePDF)'}
ETATS = {'depot_en_cours': 'Dépôt en cours', 'lien_cree': 'Lien de signature créé', 'manuel': 'À déposer à la main dans SignaturePDF',
         'resultat_a_verifier': 'Réponse du service perdue : à vérifier', 'echec': 'Échec du dépôt (rien n’a été créé)', 'rangement_en_cours': 'Rangement du PDF signé en cours',
         'signe_range': 'PDF signé rangé dans le dossier', 'close': 'Demande close'}
OUVERTS = ('lien_cree', 'manuel', 'resultat_a_verifier', 'rangement_en_cours')
BOUCLAGE = ('127.0.0.1', 'localhost')                # seule exception à HTTPS : instance locale d'un poste (lien utilisable sur ce poste seulement)
DESTINATION_CABINET, DESTINATION_TIERS = 'serveur_cabinet', 'signaturepdf_tiers'   # politique des données : locale ou fournisseur externe
AVERTISSEMENT_TIERS = ('Instance non marquée « du cabinet » : le PDF est déposé tel quel chez un tiers ; il ne peut pas être pseudonymisé.')
CLIENT = None                                       # tests : faux stockage (stat, download, put_file)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS signaturepdf56108(id TEXT PRIMARY KEY, matter TEXT NOT NULL, path TEXT NOT NULL, doc_sha TEXT NOT NULL,
  doc_size INTEGER NOT NULL DEFAULT 0, owner TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, duree TEXT NOT NULL DEFAULT '',
  remote_ref TEXT NOT NULL DEFAULT '', service TEXT NOT NULL DEFAULT '', idem_key TEXT NOT NULL UNIQUE, rangement_key TEXT NOT NULL DEFAULT '',
  result_path TEXT NOT NULL DEFAULT '', result_sha TEXT NOT NULL DEFAULT '', result_item TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS signaturepdf56108_doc ON signaturepdf56108(matter, path, updated);
'''

STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'droit_envoyer_requis': '403 Forbidden',
           'origine_ou_csrf_refuse': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'deja_range': '409 Conflict', 'document_modifie': '409 Conflict', 'demande_close': '409 Conflict', 'service_non_configure': '409 Conflict',
           'echange_automatique_indisponible': '409 Conflict', 'aucune_signature': '409 Conflict', 'pdf_identique': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'fichier_trop_volumineux': '413 Payload Too Large',
           'controle_acces_indisponible': '503 Service Unavailable', 'service_injoignable': '502 Bad Gateway', 'reponse_service_invalide': '502 Bad Gateway',
           'stockage_indisponible': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'droit_envoyer_requis': 'Votre compte n’a pas le droit « envoyer » : demandez à un avocat du cabinet.',
    'origine_ou_csrf_refuse': 'Session expirée : rechargez la page.', 'version_en_conflit': 'La demande a changé entre-temps : rechargez la page.',
    'cle_reutilisee': 'Demande déjà reçue avec un autre contenu : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'confirmation_requise': 'Cochez la case pour confirmer le dépôt du PDF sur le service de signature du cabinet.',
    'service_non_configure': 'SignaturePDF n’est pas réglé : l’administrateur le règle et le teste dans Paramètres › Connexions.',
    'echange_automatique_indisponible': 'Le dépôt automatique n’est pas disponible sur ce service : suivez le parcours manuel.',
    'adresse_invalide': 'Adresse invalide : indiquez l’adresse HTTPS du service, par exemple https://signature.example.test.',
    'duree_invalide': 'Durée de conservation inconnue.', 'echange_invalide': 'Mode d’échange inconnu.',
    'pdf_requis': 'Seuls les fichiers PDF peuvent être signés par lien.', 'pdf_invalide': 'Ce fichier n’est pas un PDF lisible.',
    'fichier_trop_volumineux': 'PDF trop volumineux pour SignaturePDF (24 Mo au plus).',
    'document_modifie': 'Le document a changé dans le dossier depuis la création du lien : créez une nouvelle demande.',
    'deja_range': 'Un PDF signé est déjà rangé pour cette demande : créez une nouvelle demande si besoin.',
    'demande_close': 'Cette demande est close.', 'aucune_signature': 'Aucune signature n’a encore été apposée sur ce lien.',
    'pdf_identique': 'Ce PDF est identique au document envoyé : il ne porte aucune signature.',
    'service_injoignable': 'Le service SignaturePDF ne répond pas : réessayez plus tard ou vérifiez-le dans Paramètres › Connexions.',
    'reponse_service_invalide': 'Réponse inattendue du service SignaturePDF : rien n’a été rangé.',
    'service_refuse': 'Le service SignaturePDF a refusé la demande.', 'stockage_indisponible': 'Stockage des dossiers indisponible : rien n’a été rangé.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été fait.', 'requete_trop_longue': 'Demande trop longue.',
    'confirmation_tiers_requise': 'Cochez la seconde case : le PDF est déposé tel quel chez un tiers ; il ne peut pas être pseudonymisé.',
    'dossier_reserve_tiers': 'Dossier réservé : ses documents ne sont jamais déposés sur une instance qui n’est pas celle du cabinet.',
    'mode_local_strict': 'Mode local strict : aucun dépôt vers une instance qui n’est pas celle du cabinet.',
    'prestataire_non_admis': 'Cette instance n’est pas admise au registre des prestataires du cabinet (Paramètres › Confidentialité).',
    'region_inconnue': 'Lieu de traitement de cette instance non renseigné, alors que la politique des données l’exige.',
    'region_non_admise': 'Lieu de traitement de cette instance non admis par la politique des données.',
    'correspondance_confidentielle': 'Correspondance confidentielle entre avocats : elle ne part jamais vers des tiers, même par lien.',
    'politique_illisible': 'La politique des données est illisible : rien n’a été déposé.',
    'dossier_exclu': 'Ce dossier est exclu des envois.', 'relecture_differente': 'Le PDF rangé relu diffère de celui reçu : à vérifier, rien n’est validé.'}


# ------------------------------------------------------------------------------------------------ outils
def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _h(value):
    """Empreinte courte d'un identifiant pour le journal (jamais le nom d'un dossier ni d'un fichier)."""
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()[:16]


def ensure_schema(desk):
    """Création idempotente, par l'écriture qui en a besoin (POST) ou au démarrage : jamais par une lecture de page."""
    desk.db.executescript(SCHEMA)
    if desk.db.in_transaction:
        desk.db.commit()


def _table_existe(desk):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signaturepdf56108'").fetchone())


def guide_url():
    """Adresse du guide dans le dépôt public (ou celui indiqué par AXIORHUB_SOURCE_URL), comme ``office440.guide_url``."""
    import os
    from .about560 import REPOSITORY
    source = os.environ.get('AXIORHUB_SOURCE_URL', '').strip().rstrip('/') or REPOSITORY
    if not source.startswith('https://'):
        source = REPOSITORY
    return source + '/blob/HEAD/' + GUIDE_PATH


def est_poste(auth=None):
    """Poste de travail (fenêtre locale, 127.0.0.1). Depuis le complément du 19/11, le service y est proposé (instance HTTPS du serveur du
    cabinet, ou instance locale) : cette information ne sert plus qu'aux explications affichées."""
    try:
        from .integration5634 import workstation
        return workstation({'axiorhub.auth': auth} if isinstance(auth, dict) else None)
    except Exception:   # noqa: BLE001 — dans le doute, version serveur : le réglage reste celui de l'administrateur
        return False


def adresse_valide(value):
    """Adresse de base du service : HTTPS, sans identifiant, requête ni fragment ; chemin facultatif (service publié sous un sous-chemin).
    HTTP n'est admis que vers l'adresse de bouclage (instance locale d'un poste : le lien ne s'ouvre que sur cet ordinateur)."""
    value = str(value or '').strip().rstrip('/')
    if not value:
        return ''
    try:
        u = urllib.parse.urlsplit(value)
        port = u.port
    except ValueError:
        raise Stop('adresse_invalide') from None
    hote = (u.hostname or '').lower()
    if u.username or u.password or u.query or u.fragment or not re.fullmatch(r'[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?', hote or '-'):
        raise Stop('adresse_invalide')
    if not re.fullmatch(r'(/[A-Za-z0-9._~-]+)*', u.path or ''):
        raise Stop('adresse_invalide')
    if not (u.scheme == 'https' or (u.scheme == 'http' and hote in BOUCLAGE)):
        raise Stop('adresse_invalide')
    return '%s://%s%s%s' % (u.scheme, hote, (':%d' % port) if port else '', u.path or '')


def est_locale(adresse):
    """Instance sur l'adresse de bouclage : le lien de signature ne s'ouvre que sur l'ordinateur qui l'héberge."""
    try:
        return (urllib.parse.urlsplit(str(adresse or '')).hostname or '').lower() in BOUCLAGE
    except ValueError:
        return False


def reglages(desk):
    r = desk.settings(SETTING, {}) or {}
    r = r if isinstance(r, dict) else {}
    duree = r.get('duree') if r.get('duree') in dict(DUREES) else DUREE_DEFAUT
    echange = r.get('echange') if r.get('echange') in ECHANGES else 'auto'
    test = r.get('test') if isinstance(r.get('test'), dict) else {}
    # « instance du cabinet » : case cochée par l'administrateur, jamais déduite de l'adresse ; absente = instance tierce
    return {'adresse': str(r.get('adresse') or ''), 'duree': duree, 'echange': echange, 'version': int(r.get('version') or 0), 'test': test,
            'cabinet': r.get('cabinet') is True}


def configure(desk):
    """Connecteur réglé ET dernier test réussi pour cette adresse : condition d'affichage du bouton."""
    r = reglages(desk)
    return bool(r['adresse']) and bool(r['test'].get('ok')) and r['test'].get('adresse') == r['adresse']


def mode_effectif(desk):
    """« auto » seulement si l'administrateur le permet et que le dernier test a trouvé l'interface d'échange."""
    r = reglages(desk)
    return 'auto' if r['echange'] == 'auto' and r['test'].get('api') and configure(desk) else 'manuel'


# ------------------------------------------------------------------------------------------------ échanges HTTP avec le service
def _ouvrir(req, limite):
    opener = urllib.request.build_opener(NoRedirect)      # aucune redirection suivie (ni vers un autre hôte, ni vers HTTP)
    try:
        with opener.open(req, timeout=DELAI) as r:
            corps = r.read(limite + 1)
            return r.status, dict(r.headers.items()), corps
    except urllib.error.HTTPError as ex:
        try:
            corps = ex.read(min(limite, 65536) + 1)
        except Exception:   # noqa: BLE001
            corps = b''
        return ex.code, dict(ex.headers.items()) if ex.headers else {}, corps
    except (urllib.error.URLError, OSError, ValueError):
        raise Stop('service_injoignable') from None


def _get(url, limite=200_000):
    status, entetes, corps = _ouvrir(urllib.request.Request(url, headers={'User-Agent': 'AxiorHub-Pilote', 'Accept': '*/*'}), limite)
    if len(corps) > limite:
        raise Stop('reponse_service_invalide')
    return status, entetes, corps


def _multipart(champs, fichier):
    limite = '----axiorhub' + secrets.token_hex(12)
    morceaux = []
    for nom, valeur in champs.items():
        morceaux.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (limite, nom, valeur)).encode('utf-8'))
    nom_champ, nom_fichier, octets = fichier
    nom_fichier = re.sub(r'[^\w .()-]', '_', nom_fichier)[:120] or 'document.pdf'
    morceaux.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: application/pdf\r\n\r\n'
                     % (limite, nom_champ, nom_fichier)).encode('utf-8') + octets + b'\r\n')
    morceaux.append(('--%s--\r\n' % limite).encode('ascii'))
    return b''.join(morceaux), 'multipart/form-data; boundary=' + limite


def sonder(base):
    """« Tester la connexion » : lectures sans effet. Page de signature (marqueurs publics de SignaturePDF), partage à plusieurs activé,
    chiffrement proposé, interface d'échange (``GET /api/share/new`` → 405 : la route existe). Rend les étapes, jamais un succès supposé."""
    etapes = []

    def etape(ident, libelle, ok, detail=''):
        etapes.append({'id': ident, 'libelle': libelle, 'ok': bool(ok), 'detail': detail})
        return ok

    try:
        status, _, corps = _get(base + '/signature', 400_000)
    except Stop:
        etape('joignable', 'Service joignable', False, 'connexion impossible (nom, certificat HTTPS ou service arrêté)')
        return {'ok': False, 'etapes': etapes, 'api': False, 'version': ''}
    texte = corps.decode('utf-8', 'replace')
    if not etape('joignable', 'Service joignable', status == 200, 'réponse %d' % status if status != 200 else ''):
        return {'ok': False, 'etapes': etapes, 'api': False, 'version': ''}
    marque = '24eme/signaturepdf' in texte or re.search(r'<title>\s*Signature PDF', texte) is not None
    if not etape('signaturepdf', 'Page de signature de SignaturePDF', marque, '' if marque else 'la page ne ressemble pas à SignaturePDF'):
        return {'ok': False, 'etapes': etapes, 'api': False, 'version': ''}
    m = re.search(r'24eme/signaturepdf/tree/([0-9a-f]{7,40})', texte)
    version = m.group(1) if m else ''
    partage = 'id="form_sharing"' in texte or 'action="/share"' in texte or re.search(r'action="[^"]*/share"', texte) is not None
    etape('partage', 'Signature à plusieurs (partage par lien) activée', partage,
          '' if partage else 'activer le stockage des PDF (PDF_STORAGE_PATH) : voir le guide, étape « Stockage »')
    chiffre = 'checkbox_encryption' in texte
    etapes.append({'id': 'chiffrement', 'libelle': 'Chiffrement des PDF stockés proposé', 'ok': chiffre, 'information': True,
                   'detail': '' if chiffre else 'facultatif (PDF_STORAGE_ENCRYPTION et GPG) : voir le guide'})
    try:
        status_api, _, _ = _get(base + '/api/share/new', 20_000)
    except Stop:
        status_api = 0
    api = status_api == 405
    etapes.append({'id': 'api', 'libelle': 'Interface d’échange (dépôt et récupération par AxiorHub)', 'ok': api, 'information': True,
                   'detail': 'disponible (non documentée par l’éditeur)' if api else 'absente : parcours manuel'})
    return {'ok': marque and partage, 'etapes': etapes, 'api': api, 'version': version}


def deposer_partage(base, octets, nom_fichier, duree):
    """``POST /api/share/new`` : un seul envoi. Rend ``{hash, symmetrickey, adminkey}`` validés. Une réponse perdue lève ``resultat_a_verifier``
    (le partage peut exister sur le service) ; un refus du service lève ``service_refuse`` (rien n'a été créé)."""
    corps, type_ = _multipart({'duration': duree}, ('pdf', nom_fichier, octets))
    req = urllib.request.Request(base + '/api/share/new', data=corps, method='POST',
                                 headers={'Content-Type': type_, 'User-Agent': 'AxiorHub-Pilote', 'Accept': 'application/json'})
    try:
        status, _, rep = _ouvrir(req, 20_000)
    except Stop:
        raise Stop('resultat_a_verifier') from None
    if status != 201:
        raise Stop('service_refuse:%d' % status)
    try:
        data = json.loads(rep[:20_000].decode('utf-8'))
    except (ValueError, UnicodeError):
        raise Stop('resultat_a_verifier') from None
    h, cle, admin = str(data.get('hash') or ''), str(data.get('symmetrickey') or ''), str(data.get('adminkey') or '')
    if not HASH.fullmatch(h) or not CLE_PARTAGE.fullmatch(cle) or not CLE_PARTAGE.fullmatch(admin):
        raise Stop('resultat_a_verifier')
    return {'hash': h, 'symmetrickey': cle, 'adminkey': admin}


def lien_signature(base, h, cle):
    """Lien construit par AxiorHub sur l'adresse réglée (jamais l'adresse renvoyée par le service, qui dépend de son mandataire)."""
    return '%s/signature/%s#%s' % (base, h, cle)


def nombre_signatures(base, h):
    status, _, corps = _get('%s/signature/%s/nblayers' % (base, h), 100)
    texte = corps.decode('ascii', 'replace').strip()
    if status != 200 or not texte.isdigit():
        raise Stop('reponse_service_invalide')
    return int(texte)


def recuperer_partage(base, h, cle):
    status, _, corps = _get('%s/api/share/get/%s/%s' % (base, h, cle), MAX_PDF)
    if status != 200 or not corps.startswith(b'%PDF'):
        raise Stop('reponse_service_invalide')
    return corps


def supprimer_partage(base, h, admin):
    status, _, _ = _get('%s/api/share/delete/%s/%s' % (base, h, admin), 20_000)
    return status in (200, 404)


# ------------------------------------------------------------------------------------------------ accès, document, coffre
def _client(desk):
    if CLIENT is not None:
        return CLIENT
    from .document_projects import _dav
    return _dav(desk)


def _dossier(desk, mid, owner, role):
    """Dossier visible de ce compte, sinon le même 404 neutre (absent ou réservé). Une panne du contrôle : 503, jamais un accès élargi."""
    mid = str(mid or '')
    try:
        from . import membres5650
        masques = set(membres5650.hidden_for(desk.c['state_dir'], owner, role)) | set(membres5650.viewer_hidden())
        m = next((x for x in load_matters(desk.c) if str(x.get('id')) == mid), None) if mid and mid not in masques else None
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    except Exception:   # noqa: BLE001
        raise Stop('controle_acces_indisponible') from None
    if not m or not m.get('path'):
        raise Stop('introuvable')
    return m


def _chemin(m, chemin):
    """PDF situé sous le répertoire du dossier ; sinon 404 neutre (aucun chemin arbitraire)."""
    try:
        chemin = clean_path(str(chemin or ''))
        ok = under(chemin, clean_path(m['path'])) and chemin != clean_path(m['path'])
    except Stop:
        raise Stop('introuvable') from None
    if not ok:
        raise Stop('introuvable')
    if PurePosixPath(chemin).suffix.lower() != '.pdf':
        raise Stop('pdf_requis')
    return chemin


def _lire(desk, chemin):
    client = _client(desk)
    try:
        info = client.stat(chemin)
        if int(info.get('size') or 0) > MAX_PDF:
            raise Stop('fichier_trop_volumineux')
        octets = client.download({**info, 'path': chemin})
    except Stop as ex:
        if str(ex) in ('fichier_trop_volumineux',):
            raise
        if str(ex) in ('http_404', 'fichier_nextcloud_introuvable', 'introuvable'):
            raise Stop('introuvable') from None
        raise Stop('stockage_indisponible') from None
    if len(octets) > MAX_PDF:
        raise Stop('fichier_trop_volumineux')
    if not octets.startswith(b'%PDF'):
        raise Stop('pdf_invalide')
    return octets, info


def _coffre(desk, ident, nom):
    return Path(desk.c['state_dir']) / 'vault' / 'signaturepdf56108' / re.sub(r'[^A-Za-z0-9_-]', '', ident) / (nom + '.secret')


def _secret(desk, ident, nom):
    from .common import read_secret
    p = _coffre(desk, ident, nom)
    try:
        return read_secret(str(p)) if p.is_file() else ''
    except Stop:
        return ''


def _effacer_secrets(desk, ident):
    for nom in ('cle', 'admin', 'hash'):
        p = _coffre(desk, ident, nom)
        if p.is_file():
            p.unlink()


def _demande(desk, ident, owner, role):
    """Demande et dossier recontrôlés ; inconnue, ou dossier devenu invisible : même 404 neutre."""
    if not _table_existe(desk):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM signaturepdf56108 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    m = _dossier(desk, row['matter'], owner, role)
    return dict(row), m


def _peut_signer(desk, owner, role):
    if role not in SIGNATAIRES:
        raise Stop('role_insuffisant')
    if role == 'administrateur':
        return
    try:
        from .membres5650 import effective_rights
        droits = effective_rights(desk.c['state_dir'], owner, role)
    except Exception:   # noqa: BLE001 — registre illisible : refus
        raise Stop('controle_acces_indisponible') from None
    if not droits.get('envoyer'):
        raise Stop('droit_envoyer_requis')


def _politique(desk, mid, chemin, owner, role, cabinet=True):
    """Politique des données relue au moment du dépôt : un dossier réservé reste réservé, une correspondance confidentielle ne part pas.
    Instance du cabinet : destination locale (``serveur_cabinet``). Instance non marquée : fournisseur externe (``signaturepdf_tiers`` :
    mode local strict, registre des prestataires et régions appliqués par ``data_policy5689``) ; en plus, dossier réservé et correspondance
    confidentielle y sont refusés d'office (avant la politique, qui ne pourrait que les admettre selon le mode)."""
    from . import data_policy5689 as dp
    from .confidentiel5664 import is_confidential
    confidentiel = is_confidential(PurePosixPath(chemin).name)
    labels = ('dossier', 'documents') + (('confidentiel',) if confidentiel else ())
    if not cabinet:
        if confidentiel:
            raise Stop('correspondance_confidentielle')
        try:
            from . import membres5650
            reserve = str(mid) in membres5650.load(desk.c['state_dir'])['reserves']
        except Exception:   # noqa: BLE001 — registre illisible : refus, jamais un envoi élargi
            raise Stop('controle_acces_indisponible') from None
        if reserve:
            raise Stop('dossier_reserve_tiers')
    dp.exiger(desk.c, 'signature_lien', DESTINATION_CABINET if cabinet else DESTINATION_TIERS, finalite='document_tiers', dossiers=(mid,), labels=labels,
              acteur=owner, role=role, local=bool(cabinet))


def _version(data, row):
    try:
        attendue = int(data.get('version'))
    except (TypeError, ValueError):
        raise Stop('version_en_conflit') from None
    if attendue != row['version']:
        raise Stop('version_en_conflit')


def _maj(desk, ident, version, **champs):
    champs['updated'] = _now()
    cur = desk.db.execute('UPDATE signaturepdf56108 SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join(k + '=?' for k in champs),
                          tuple(champs.values()) + (ident, version))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    return version + 1


def _vue(desk, row, avec_lien=False):
    out = {'demande': row['id'], 'etat': row['state'], 'etat_libelle': ETATS.get(row['state'], row['state']), 'mode': row['mode'],
           'version': row['version'], 'cree': row['created'], 'maj': row['updated'], 'document': PurePosixPath(row['path']).name,
           'signe': PurePosixPath(row['result_path']).name if row['result_path'] else '', 'erreur': row['error']}
    if avec_lien and row['mode'] == 'auto' and row['remote_ref'] and row['state'] in OUVERTS:
        cle, h = _secret(desk, row['id'], 'cle'), _secret(desk, row['id'], 'hash')
        out['lien'] = lien_signature(row['service'], h, cle) if cle and h else ''
    return out


# ------------------------------------------------------------------------------------------------ réglages (administrateur)
def enregistrer_reglages(desk, data, owner):
    actuel = reglages(desk)
    try:
        attendue = int(data.get('version'))
    except (TypeError, ValueError):
        raise Stop('version_en_conflit') from None
    if attendue != actuel['version']:
        raise Stop('version_en_conflit')
    adresse = adresse_valide(data.get('adresse'))
    duree = str(data.get('duree') or actuel['duree'])
    if duree not in dict(DUREES):
        raise Stop('duree_invalide')
    echange = str(data.get('echange') or actuel['echange'])
    if echange not in ECHANGES:
        raise Stop('echange_invalide')
    test = actuel['test'] if adresse == actuel['adresse'] else {}          # autre adresse : le test est à refaire
    cabinet = data.get('cabinet') in (True, 'true', 'on', '1', 1)          # case du formulaire : non cochée (absente) = instance tierce
    nouveau = {'adresse': adresse, 'duree': duree, 'echange': echange, 'version': actuel['version'] + 1, 'test': test, 'cabinet': cabinet}
    desk.setting(SETTING, nouveau)
    desk.audit('signaturepdf56108_reglages', {'adresse_renseignee': bool(adresse), 'duree': duree, 'echange': echange, 'instance_du_cabinet': cabinet})
    return {'reglages': {k: v for k, v in nouveau.items()}, 'message': 'Réglage enregistré.' + (' Cliquez « Tester la connexion ».' if adresse and not test else '')}


def tester(desk, data, owner):
    actuel = reglages(desk)
    if not actuel['adresse']:
        raise Stop('service_non_configure')
    resultat = sonder(actuel['adresse'])
    test = {'ok': resultat['ok'], 'api': resultat['api'], 'version': resultat['version'], 'adresse': actuel['adresse'], 'le': _now(),
            'etapes': resultat['etapes']}
    frais = reglages(desk)                                                  # relu : un réglage concurrent n'est pas écrasé
    if frais['adresse'] == actuel['adresse']:
        desk.setting(SETTING, {**{k: frais[k] for k in ('adresse', 'duree', 'echange', 'cabinet')}, 'version': frais['version'] + 1, 'test': test})
    desk.audit('signaturepdf56108_test', {'ok': resultat['ok'], 'api': resultat['api']})
    message = (('Connexion vérifiée : SignaturePDF répond ; dépôt automatique disponible.' if resultat['api'] else
                'Connexion vérifiée : SignaturePDF répond ; parcours manuel.') if resultat['ok'] else 'Échec du test : voir les étapes.')
    return {'ok': resultat['ok'], 'etapes': resultat['etapes'], 'api': resultat['api'], 'message': message}


# ------------------------------------------------------------------------------------------------ demandes de signature
def preparer(desk, data, owner, role, auth=None):
    """« Faire signer par lien » : demande enregistrée puis, en mode automatique, un seul dépôt du PDF sur le service (après la pause et la
    politique des données). Rend le lien à copier ; AxiorHub ne l'envoie à personne. Même clé, même contenu : réponse rejouée.
    Instance non marquée « du cabinet » (parcours automatique comme manuel) : seconde confirmation ``confirmer_tiers`` exigée à chaque dépôt."""
    from . import pause5629
    _peut_signer(desk, owner, role)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    m = _dossier(desk, data.get('matter'), owner, role)
    chemin = _chemin(m, data.get('chemin'))
    if not configure(desk):
        raise Stop('service_non_configure')
    if data.get('confirmer') not in (True, 'true', 'on', '1', 1):
        raise Stop('confirmation_requise')
    ensure_schema(desk)
    r = reglages(desk)
    deja = desk.db.execute('SELECT * FROM signaturepdf56108 WHERE idem_key=?', (cle,)).fetchone()
    if deja:
        if deja['matter'] != m['id'] or deja['path'] != chemin or deja['owner'] != owner:
            raise Stop('cle_reutilisee')
        return {**_vue(desk, dict(deja), True), 'rejoue': True, 'message': 'Demande déjà reçue.'}
    _politique(desk, m['id'], chemin, owner, role, r['cabinet'])
    if not r['cabinet'] and data.get('confirmer_tiers') not in (True, 'true', 'on', '1', 1):
        raise Stop('confirmation_tiers_requise')
    octets, _info = _lire(desk, chemin)
    mode = mode_effectif(desk)
    ident = 'sp-' + uuid.uuid4().hex[:24]
    now = _now()
    desk.db.execute('INSERT INTO signaturepdf56108(id,matter,path,doc_sha,doc_size,owner,mode,state,duree,service,idem_key,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, m['id'], chemin, _sha(octets), len(octets), owner, mode,
                                                         'depot_en_cours' if mode == 'auto' else 'manuel', r['duree'], r['adresse'], cle, now, now))
    desk.db.commit()
    desk.audit('signaturepdf56108_demande', {'demande': ident, 'dossier': _h(m['id']), 'mode': mode, 'document': _sha(octets)[:16],
                                              'instance_du_cabinet': r['cabinet']})
    if mode == 'manuel':
        row = dict(desk.db.execute('SELECT * FROM signaturepdf56108 WHERE id=?', (ident,)).fetchone())
        return {**_vue(desk, row), 'ouvrir': r['adresse'] + '/signature',
                'message': 'Demande créée : téléchargez le PDF, déposez-le dans SignaturePDF (nouvel onglet), puis rangez ici le PDF signé.'}
    pause5629.checkpoint(desk)                                                 # pause et horaires revalidés juste avant l'effet
    try:
        out = deposer_partage(r['adresse'], octets, PurePosixPath(chemin).name, r['duree'])
    except Stop as ex:
        code = str(ex).split(':')[0]
        etat = 'resultat_a_verifier' if code == 'resultat_a_verifier' else 'echec'
        _maj(desk, ident, 1, state=etat, error=str(ex)[:80])
        desk.audit('signaturepdf56108_depot', {'demande': ident, 'ok': False, 'etat': etat})
        row = dict(desk.db.execute('SELECT * FROM signaturepdf56108 WHERE id=?', (ident,)).fetchone())
        msg = ('La réponse du service s’est perdue : le PDF a peut-être été déposé. Il s’effacera seul à la fin de sa durée de conservation. '
               'Créez une nouvelle demande ou suivez le parcours manuel.') if etat == 'resultat_a_verifier' else \
            'Le service a refusé le dépôt (%s) : rien n’a été créé. Vérifiez le service dans Paramètres › Connexions.' % str(ex).split(':')[-1]
        return {**_vue(desk, row), 'ok': False, 'message': msg}
    from .vault567 import write
    write(_coffre(desk, ident, 'cle'), out['symmetrickey'])
    write(_coffre(desk, ident, 'admin'), out['adminkey'])
    write(_coffre(desk, ident, 'hash'), out['hash'])
    _maj(desk, ident, 1, state='lien_cree', remote_ref=_h(out['hash']))
    desk.audit('signaturepdf56108_depot', {'demande': ident, 'ok': True})
    row = dict(desk.db.execute('SELECT * FROM signaturepdf56108 WHERE id=?', (ident,)).fetchone())
    return {**_vue(desk, row, True), 'ok': True, 'noreload': False,
            'message': 'Lien de signature créé. Copiez-le et adressez-le vous-même aux signataires : AxiorHub ne l’envoie à personne.'}


def etat(desk, ident, owner, role):
    """Lecture : demande, lien (compte qui voit le dossier) et, en mode automatique, nombre de signatures lu sur le service. Aucune écriture."""
    row, _m = _demande(desk, ident, owner, role)
    out = _vue(desk, row, avec_lien=True)
    h = _secret(desk, row['id'], 'hash') if row['mode'] == 'auto' and row['state'] == 'lien_cree' and row['remote_ref'] else ''
    if h:
        try:
            out['signatures'] = nombre_signatures(row['service'], h)
        except Stop:
            out['signatures'] = None                                           # panne dite, jamais un faux zéro
    return out


def pdf_original(desk, ident, owner, role):
    """PDF envoyé pour signature (parcours manuel) : relu dans le dossier et comparé à l'empreinte de la demande."""
    row, _m = _demande(desk, ident, owner, role)
    if row['owner'] != owner and role != 'administrateur':
        _peut_signer(desk, owner, role)
    octets, _ = _lire(desk, row['path'])
    if _sha(octets) != row['doc_sha']:
        raise Stop('document_modifie')
    return PurePosixPath(row['path']).name, octets


def _nom_signe(desk, chemin):
    from .acces5683 import today_of
    jour = today_of(desk)[0]
    p = PurePosixPath(chemin)
    return str(p.parent / ('%s (signé le %s).pdf' % (p.stem[:150], jour.strftime('%d-%m-%Y'))))


def _poser(client, chemin, octets):
    """Création exclusive (jamais d'écrasement) : « (2) », « (3) »… si le nom existe."""
    base, ext = chemin[:-4], chemin[-4:]
    for n in range(1, 40):
        cible = chemin if n == 1 else '%s (%d)%s' % (base, n, ext)
        try:
            client.put_file(cible, octets, 'application/pdf')
            return cible
        except Stop as ex:
            if str(ex) != 'http_412':
                raise
    raise Stop('fichier_nom_indisponible')


def _ranger(desk, row, m, octets, cle, owner, origine):
    """PDF signé rangé à côté de l'original (jamais modifié), relu, puis inscrit au registre (nouvelle version du même document s'il y figure)."""
    from . import pause5629
    if not octets.startswith(b'%PDF'):
        raise Stop('pdf_invalide')
    if len(octets) > MAX_PDF:
        raise Stop('fichier_trop_volumineux')
    sha = _sha(octets)
    if sha == row['doc_sha']:
        raise Stop('pdf_identique')
    if row['state'] == 'signe_range':
        if row['rangement_key'] == cle and row['result_sha'] == sha:
            return {**_vue(desk, row), 'rejoue': True, 'message': 'PDF signé déjà rangé : %s.' % PurePosixPath(row['result_path']).name}
        raise Stop('deja_range')
    if row['state'] == 'close':
        raise Stop('demande_close')
    client = _client(desk)
    version = row['version']
    if row['state'] == 'rangement_en_cours' and row['result_sha'] == sha and row['result_path']:
        try:                                                                   # réponse perdue : le fichier attendu est-il déjà là ?
            info = client.stat(row['result_path'])
            if _sha(client.download({**info, 'path': row['result_path']})) == sha:
                return _constat(desk, row, m, client, row['result_path'], octets, cle, owner, origine)
        except Stop:
            pass
    cible = _nom_signe(desk, row['path'])
    version = _maj(desk, row['id'], version, state='rangement_en_cours', result_sha=sha, result_path=cible, rangement_key=cle)
    row = {**row, 'version': version, 'state': 'rangement_en_cours', 'result_sha': sha, 'result_path': cible, 'rangement_key': cle}
    pause5629.checkpoint(desk)
    try:
        cible = _poser(client, cible, octets)
    except Stop:
        raise Stop('stockage_indisponible') from None
    if cible != row['result_path']:
        version = _maj(desk, row['id'], version, result_path=cible)
        row = {**row, 'version': version, 'result_path': cible}
    return _constat(desk, row, m, client, cible, octets, cle, owner, origine)


def _constat(desk, row, m, client, cible, octets, cle, owner, origine):
    sha = _sha(octets)
    try:
        info = client.stat(cible)
        relu = client.download({**info, 'path': cible})
    except Stop:
        raise Stop('stockage_indisponible') from None
    if _sha(relu) != sha:
        raise Stop('relecture_differente')
    item = ''
    try:
        from . import livrables5689
        livrables5689.ensure_schema(desk)
        origine_doc = desk.db.execute("SELECT document_id, title FROM prepared_items5689 WHERE path=? AND matter_id=? AND lifecycle='current' "
                                      "ORDER BY version_number DESC LIMIT 1", (row['path'], m['id'])).fetchone()
        sortie = livrables5689.Sortie(desk, 'signaturepdf56108', row['id'], sha, matter=m['id'], kind='', path=cible, mime='application/pdf', owner=owner,
                                      title=(origine_doc['title'] if origine_doc else PurePosixPath(row['path']).stem)[:250] + ' (signé)',
                                      document_id=origine_doc['document_id'] if origine_doc else '',
                                      provenance={'producteur': 'signaturepdf56108', 'demande': row['id'], 'original_sha256': row['doc_sha'], 'origine': origine})
        res = sortie.constater(client, cible, octets)
        item = str((res or {}).get('id') or '')
    except (sqlite3.Error, Stop, KeyError, ValueError, ImportError):
        item = ''                                                              # le registre reprendra au rapprochement ; le fichier est rangé et relu
    version = _maj(desk, row['id'], row['version'], state='signe_range', result_item=item, error='')
    desk.audit('signaturepdf56108_range', {'demande': row['id'], 'origine': origine, 'signe': sha[:16]})
    out = {**_vue(desk, {**row, 'version': version, 'state': 'signe_range', 'result_path': cible, 'error': ''}), 'ok': True,
           'message': 'PDF signé rangé dans le dossier : %s (l’original n’a pas été modifié).' % PurePosixPath(cible).name}
    return out


def recuperer(desk, data, owner, role):
    """Mode automatique : le PDF compilé est lu sur le service (au moins une signature) puis rangé."""
    _peut_signer(desk, owner, role)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    row, m = _demande(desk, data.get('demande'), owner, role)
    if row['state'] == 'signe_range':                                          # rejeu de la même demande : avant le contrôle de version
        if row['rangement_key'] == cle:
            return {**_vue(desk, row), 'rejoue': True, 'message': 'PDF signé déjà rangé.'}
        raise Stop('deja_range')
    _version(data, row)
    if row['state'] == 'close':
        raise Stop('demande_close')
    if row['mode'] != 'auto' or not row['remote_ref']:
        raise Stop('echange_automatique_indisponible')
    cle_partage, h = _secret(desk, row['id'], 'cle'), _secret(desk, row['id'], 'hash')
    if not cle_partage or not h:
        raise Stop('echange_automatique_indisponible')
    if nombre_signatures(row['service'], h) < 1:
        raise Stop('aucune_signature')
    octets = recuperer_partage(row['service'], h, cle_partage)
    return _ranger(desk, row, m, octets, cle, owner, 'service')


def deposer(desk, args, octets, owner, role):
    """« Déposer le PDF signé » (parcours manuel, ou secours du mode automatique) : octets bruts, bornés, contrôlés."""
    _peut_signer(desk, owner, role)
    cle = str(args.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    row, m = _demande(desk, args.get('demande'), owner, role)
    if not (row['state'] == 'signe_range' and row['rangement_key'] == cle):    # rejeu de la même demande : rendu par ``_ranger``
        _version(args, row)
    return _ranger(desk, row, m, octets, cle, owner, 'depot_manuel')


def clore(desk, data, owner, role):
    """Clôt la demande ; sur demande expresse, supprime aussi le PDF du service (mode automatique, clé d'administration). Clés effacées."""
    _peut_signer(desk, owner, role)
    row, _m = _demande(desk, data.get('demande'), owner, role)
    _version(data, row)
    if row['state'] == 'close':
        return {**_vue(desk, row), 'rejoue': True, 'message': 'Demande déjà close.'}
    retire = None
    if data.get('supprimer_distant') in (True, 'true', 'on', '1', 1) and row['mode'] == 'auto' and row['remote_ref']:
        admin, h = _secret(desk, row['id'], 'admin'), _secret(desk, row['id'], 'hash')
        if admin and h:
            try:
                retire = supprimer_partage(row['service'], h, admin)
            except Stop:
                retire = False
    if retire is False:
        return {**_vue(desk, row), 'ok': False, 'message': 'Le service n’a pas confirmé la suppression : la demande reste ouverte, réessayez plus tard.'}
    _maj(desk, row['id'], row['version'], state='close')
    _effacer_secrets(desk, row['id'])
    desk.audit('signaturepdf56108_close', {'demande': row['id'], 'supprime_du_service': bool(retire)})
    msg = 'Demande close.' + (' Le PDF a été supprimé du service SignaturePDF.' if retire else
                              ' Le PDF reste sur le service jusqu’à la fin de sa durée de conservation.' if row['mode'] == 'auto' and row['remote_ref'] else '')
    return {'demande': row['id'], 'etat': 'close', 'message': msg}


def demandes_document(desk, mid, chemin, owner, role):
    """Demandes de ce document (lecture ; table absente : aucune)."""
    if not _table_existe(desk):
        return []
    return [dict(r) for r in desk.db.execute('SELECT * FROM signaturepdf56108 WHERE matter=? AND path=? ORDER BY created DESC LIMIT 20', (mid, chemin))]


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES_GET
    if method != 'POST' or reste not in ECRITURES:
        return False
    if reste in ADMIN:
        return role == 'administrateur'
    return role in SIGNATAIRES


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _binaire(env, auth):
    """Octets du PDF signé : mêmes contrôles d'origine et de jeton que ``check_post`` ; taille bornée avant lecture."""
    import hmac
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        longueur = 0
    if longueur <= 0:
        raise Stop('pdf_invalide')
    if longueur > MAX_PDF:
        raise Stop('fichier_trop_volumineux')
    if env.get('CONTENT_TYPE', '').split(';')[0] not in ('application/pdf', 'application/octet-stream'):
        raise Stop('pdf_invalide')
    return env['wsgi.input'].read(longueur)


def _message(code):
    from .web import REASONS
    return MESSAGES.get(code) or REASONS.get(code) or code.replace('_', ' ')


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in LECTURES_GET + ECRITURES:
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        code = 'role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable'
        return _json({'error': code, 'message': MESSAGES[code]}, STATUTS[code])
    try:
        if method in ('GET', 'HEAD'):
            if reste == 'etat':
                return _json(etat(desk, str(args.get('demande') or ''), owner, role))
            nom, octets = pdf_original(desk, str(args.get('demande') or ''), owner, role)
            from .livrables_export5689 import _disposition
            return {'status': '200 OK', 'kind': 'application/pdf', 'body': octets,
                    'headers': [('Content-Disposition', _disposition(nom)), ('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff')]}
        if reste == 'deposer':
            octets = _binaire(env, auth)
            return _json(deposer(desk, args, octets, owner, role))
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        if reste == 'reglages':
            return _json(enregistrer_reglages(desk, data, owner))
        if reste == 'tester':
            return _json(tester(desk, data, owner))
        if reste == 'preparer':
            return _json(preparer(desk, data, owner, role, auth))
        if reste == 'recuperer':
            return _json(recuperer(desk, data, owner, role))
        return _json(clore(desk, data, owner, role))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent'):
            code = 'introuvable'
        return _json({'error': code, 'message': _message(code)}, STATUTS.get(code, '400 Bad Request'))


# ------------------------------------------------------------------------------------------------ écrans (lecture seule)
def reglages_html(desk, prefix, role, auth=None):
    """Paramètres › Connexions › « SignaturePDF (signature par lien) ». GET : aucune écriture, aucun appel au service."""
    r = reglages(desk)
    intro = ('<p>Service facultatif : SignaturePDF (logiciel libre, AGPL-3.0) s’installe à côté '
             'd’AxiorHub, de préférence sur un sous-domaine HTTPS du serveur du cabinet, que les postes utilisent aussi. Sur un PDF d’un dossier, « Faire signer par lien » y dépose le document '
             'et donne le lien à adresser aux signataires ; le PDF signé revient dans le dossier, à côté de l’original. AxiorHub n’envoie le lien '
             'à personne.</p><p class="ax-muted">Signature visible (signature électronique simple) : pour une signature avancée par un prestataire, '
             'voir Youtrust (Yousign) ou DocuSign dans les connexions ci-dessus. <a href="%s" target="_blank" rel="noopener noreferrer">Guide '
             'd’installation pas à pas</a></p>') % _e(guide_url(), quote=True)
    if est_poste(auth):   # complément du 19/11 : proposé sur un poste ; l'instance du serveur du cabinet reste le cas type
        intro += ('<p class="ax-muted">Sur ce poste, indiquez de préférence l’adresse HTTPS du SignaturePDF du serveur du cabinet : les liens '
                  'donnés aux signataires pointent alors vers ce serveur.</p>')
    test = r['test'] if r['test'].get('adresse') == r['adresse'] else {}
    if not r['adresse']:
        etat_html = '<p class="z108-etat z108-non" role="status">Non configuré.</p>'
    elif not test:
        etat_html = '<p class="z108-etat z108-non" role="status">Adresse enregistrée, pas encore testée : le bouton n’apparaît qu’après un test réussi.</p>'
    else:
        # chaque donnée (date, version, détail) dans son propre élément : le dictionnaire des écrans traduit les phrases, jamais les données
        etat_html = '<p class="z108-etat z108-%s" role="status"><span>%s</span> <span class="ax-muted">Dernier test : %s</span>%s</p>' % (
            'ok' if test.get('ok') else 'ko', 'Connexion vérifiée' if test.get('ok') else 'Échec du dernier test', _e(str(test.get('le', ''))[:16].replace('T', ' ')),
            (' <span class="ax-muted">Version : %s</span>' % _e(test['version'])) if test.get('version') else '')
        etat_html += '<ul class="z108-etapes">%s</ul>' % ''.join(
            '<li class="z108-%s"><span aria-hidden="true">%s</span> <span>%s</span>%s</li>' % (
                'info' if x.get('information') and not x.get('ok') else ('ok' if x.get('ok') else 'ko'), '✔' if x.get('ok') else ('·' if x.get('information') else '✖'),
                _e(x.get('libelle', '')), (' — <span>%s</span>' % _e(x['detail'])) if x.get('detail') else '') for x in test.get('etapes') or [])
        etat_html += '<p class="ax-muted"><span>Échange en vigueur :</span> <strong>%s</strong></p>' % _e('automatique' if mode_effectif(desk) == 'auto' else 'manuel')
    if r['adresse']:
        etat_html += ('<p class="ax-muted">Instance du cabinet (cochée par l’administrateur).</p>' if r['cabinet'] else
                      '<p class="ax-alert"><span>%s</span> <span>Chaque dépôt demande une seconde confirmation ; dossiers réservés et correspondances '
                      'confidentielles refusés ; mode local strict : aucun dépôt.</span></p>' % _e(AVERTISSEMENT_TIERS))
        if est_locale(r['adresse']):
            etat_html += ('<p class="ax-alert">Instance locale (adresse de bouclage) : le lien de signature ne s’ouvre que sur cet ordinateur. '
                          'Pour faire signer un client à distance, utilisez l’instance du serveur du cabinet.</p>')
    if role != 'administrateur':
        return '<section class="ax-card z108" id="signaturepdf56108"><h2>SignaturePDF (signature par lien)</h2>%s%s<p class="ax-muted">Seul l’administrateur règle ce service.</p></section>' % (intro, etat_html)
    durees = ''.join('<option value="%s"%s>%s</option>' % (_e(k, quote=True), ' selected' if k == r['duree'] else '', _e(v)) for k, v in DUREES)
    echanges = ''.join('<option value="%s"%s>%s</option>' % (_e(k, quote=True), ' selected' if k == r['echange'] else '', _e(v)) for k, v in ECHANGES.items())
    formulaire = ('<form class="m5-form z108-form" data-api="%sreglages" data-reload><input type="hidden" name="version" value="%d">'
                  '<label for="z108-adresse">Adresse HTTPS du service</label><input id="z108-adresse" name="adresse" type="url" inputmode="url" maxlength="300" '
                  'placeholder="https://signature.example.test" value="%s" autocomplete="off">'
                  '<label for="z108-duree">Conservation du PDF sur le service</label><select id="z108-duree" name="duree">%s</select>'
                  '<label for="z108-echange">Échange avec le service</label><select id="z108-echange" name="echange">%s</select>'
                  '<label class="d51-check"><input type="checkbox" name="cabinet"%s> Instance du cabinet (hébergée et administrée par le cabinet)</label>'
                  '<p class="ax-muted">Sans cette case, l’instance est traitée comme un tiers : avertissement et seconde confirmation à chaque dépôt.</p>'
                  '<p class="ax-muted">Adresse en HTTPS ; HTTP seulement pour une instance locale, sur l’adresse de bouclage de ce poste.</p><p class="ax-muted">Aucune clé n’est demandée : SignaturePDF n’en utilise pas. Protégez son interface d’échange comme l’indique le guide.</p>'
                  '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>') % (
        PREFIXE, r['version'], _e(r['adresse'], quote=True), durees, echanges, ' checked' if r['cabinet'] else '')
    if r['adresse']:
        formulaire += ('<form class="m5-form z108-form" data-api="%stester" data-reload><input type="hidden" name="cle" value="%s">'
                       '<button class="ax-btn ghost" type="submit">Tester la connexion</button><p role="status" class="m5-status"></p></form>') % (
            PREFIXE, secrets.token_hex(12))
    return '<section class="ax-card z108" id="signaturepdf56108"><h2>SignaturePDF (signature par lien)</h2>%s%s%s</section>' % (intro, etat_html, formulaire)


def bouton_html(desk, prefix, mid, chemin, role, auth=None):
    """Point de montage de l'onglet Fichiers du dossier (lot F) : bouton « Faire signer par lien » sur un PDF, ou chaîne vide (service
    non configuré ou non testé, autre format, rôle qui ne peut pas déposer). Présent aussi sur un poste (complément du 19/11) : le test réussi
    prouve que l'instance réglée (HTTPS, ou instance locale sur l'adresse de bouclage) est joignable. GET : aucune écriture, aucun appel au service."""
    try:
        if role not in SIGNATAIRES or PurePosixPath(str(chemin or '')).suffix.lower() != '.pdf' or not configure(desk):
            return ''
    except Exception:   # noqa: BLE001 — le bouton est facultatif : une panne ne casse jamais la liste des fichiers
        return ''
    href = prefix + PAGE + '?' + urllib.parse.urlencode({'dossier': str(mid or ''), 'chemin': str(chemin or '')})
    return '<a class="ax-btn ghost z108-bouton" href="%s">Faire signer par lien</a>' % _e(href, quote=True)


def boutons_fichier_html(desk, auth, prefix, ident_source, chemin, role='administrateur', titre=''):
    """Entrée du point de montage ``dossier5679.MONTAGES_FICHIER108`` (lot F), même signature que ``wordcraft56108.boutons_html`` :
    le dossier est retrouvé par le chemin (répertoire de dossier le plus précis) ; la page ouverte revérifie le dossier et les droits (dossier
    réservé : 404 neutre). Fichier hors stockage des dossiers, autre format, poste, service non configuré ou rôle sans dépôt : chaîne vide."""
    try:
        if ident_source != 'nextcloud' or PurePosixPath(str(chemin or '')).suffix.lower() != '.pdf' or not configure(desk):
            return ''
        chemin = clean_path(str(chemin))
        candidats = [m for m in load_matters(desk.c) if m.get('path') and under(chemin, clean_path(m['path'])) and chemin != clean_path(m['path'])]
        if not candidats:
            return ''
        m = max(candidats, key=lambda x: len(clean_path(x['path'])))
        bouton = bouton_html(desk, prefix, m['id'], chemin, role, auth)
    except Exception:   # noqa: BLE001 — bouton facultatif : la liste des fichiers reste utilisable
        return ''
    if bouton and titre:
        bouton = bouton.replace('</a>', '<span class="sr-only"> : %s</span></a>' % _e(titre), 1)
    return bouton


def _demande_html(desk, prefix, row, owner, role):
    v = _vue(desk, row, avec_lien=True)
    cle = secrets.token_hex(12)
    commun = '<input type="hidden" name="demande" value="%s"><input type="hidden" name="version" value="%d">' % (_e(row['id'], quote=True), row['version'])
    corps = '<p><strong>%s</strong> <span class="ax-muted">Demandée le %s</span></p>' % (_e(v['etat_libelle']), _e(row['created'][:16].replace('T', ' ')))
    ouvert = row['state'] in OUVERTS
    if v.get('lien'):
        corps += ('<p class="z108-lien-ligne"><label for="z108-lien-%(i)s">Lien à adresser aux signataires</label>'
                  '<input id="z108-lien-%(i)s" class="z108-lien" readonly value="%(l)s"> <button type="button" class="ax-btn ghost" data-z108-copier="z108-lien-%(i)s">'
                  'Copier le lien</button></p><p class="ax-muted">Chaque signataire ouvre le lien, signe, puis le suivant signe sur le même lien. '
                  'Transmettez-le par votre messagerie habituelle : AxiorHub ne l’envoie pas.</p>') % {'i': _e(row['id'], quote=True), 'l': _e(v['lien'], quote=True)}
        if row['state'] == 'lien_cree':
            corps += ('<p class="z108-compte" data-z108-etat="%s" aria-live="polite"></p>'
                      '<form class="m5-form z108-form" data-api="%srecuperer" data-reload>%s<input type="hidden" name="cle" value="r%s">'
                      '<button class="ax-btn" type="submit">Récupérer le PDF signé</button><p role="status" class="m5-status"></p></form>') % (
                _e(row['id'], quote=True), PREFIXE, commun, cle)
    if row['mode'] == 'manuel' and ouvert:
        r = reglages(desk)
        corps += ('<ol class="z108-etapes-manuelles"><li><a class="ax-btn ghost" href="%s">Télécharger le PDF</a></li>'
                  '<li><a class="ax-btn ghost" href="%s" target="_blank" rel="noopener noreferrer">Ouvrir SignaturePDF (nouvel onglet)</a></li>'
                  '<li>Déposez-y le PDF, choisissez le partage pour signer à plusieurs (icône de partage), la durée de conservation, puis démarrez le partage.</li>'
                  '<li>Copiez le lien affiché par SignaturePDF et adressez-le vous-même aux signataires.</li>'
                  '<li>Quand tous ont signé, téléchargez le PDF signé depuis ce lien, puis déposez-le ci-dessous.</li></ol>') % (
            _e(prefix + '/api440/' + PREFIXE + 'pdf?' + urllib.parse.urlencode({'demande': row['id']}), quote=True), _e(r['adresse'] + '/signature', quote=True))
    if ouvert:
        corps += ('<form class="z108-depot" data-z108-depot="%s" data-version="%d" data-cle="d%s"><label for="z108-fichier-%s">%s</label>'
                  '<input id="z108-fichier-%s" type="file" accept="application/pdf,.pdf" required> <button class="ax-btn%s" type="submit">Déposer le PDF signé</button>'
                  '<p role="status" class="m5-status"></p></form>') % (
            _e(row['id'], quote=True), row['version'], cle, _e(row['id'], quote=True),
            'PDF signé téléchargé depuis SignaturePDF' if row['mode'] == 'manuel' else 'Secours : PDF signé téléchargé à la main depuis le lien',
            _e(row['id'], quote=True), '' if row['mode'] == 'manuel' else ' ghost')
        supprimer = ('<label class="d51-check"><input type="checkbox" name="supprimer_distant"> Supprimer aussi le PDF du service SignaturePDF</label>'
                     if row['mode'] == 'auto' and row['remote_ref'] else '')
        corps += ('<form class="m5-form z108-form" data-api="%sclore" data-reload data-confirm="Clore cette demande ?">%s%s'
                  '<button class="ax-btn ghost" type="submit">Clore la demande</button><p role="status" class="m5-status"></p></form>') % (PREFIXE, commun, supprimer)
    if row['state'] == 'signe_range' and row['result_path']:
        from .fiche460 import doc_href
        corps += '<p><span>PDF signé :</span> <a href="%s">%s</a> <span>(l’original n’a pas été modifié)</span></p>' % (
            _e(prefix + doc_href(row['result_path'], row['matter']), quote=True), _e(PurePosixPath(row['result_path']).name))
    if row['error'] and row['state'] in ('echec', 'resultat_a_verifier'):
        corps += '<p class="ax-muted"><span>Détail :</span> <code>%s</code></p>' % _e(row['error'])
    return '<article class="z108-demande" id="z108-%s">%s</article>' % (_e(row['id'], quote=True), corps)


def page_html(desk, prefix, args, owner, role, auth=None):
    """Page d'un PDF du dossier : demandes existantes, création d'un lien (confirmation), parcours manuel. Lecture seule."""
    mid = str(args.get('dossier') or '')
    m = _dossier(desk, mid, owner, role)
    chemin = _chemin(m, args.get('chemin'))
    nom = PurePosixPath(chemin).name
    from .fiche460 import doc_href
    retour = prefix + '/dossier?' + urllib.parse.urlencode({'id': m['id'], 'vue': 'fichiers'})
    tete = ('<section class="z108 z108-page" aria-labelledby="z108-titre"><h1 id="z108-titre">Faire signer par lien</h1>'
            '<p><span>Document :</span> <a href="%s">%s</a> · <a href="%s">Retour aux fichiers du dossier</a></p>') % (
        _e(prefix + doc_href(chemin, m['id']), quote=True), _e(nom), _e(retour, quote=True))
    if not configure(desk):
        return tete + ('<p class="ax-alert" role="status">%s</p><p><a href="%s" target="_blank" rel="noopener noreferrer">Guide d’installation</a></p></section>' % (
            _e(MESSAGES['service_non_configure']), _e(guide_url(), quote=True)))
    r = reglages(desk)
    demandes = demandes_document(desk, m['id'], chemin, owner, role)
    corps = ''.join(_demande_html(desk, prefix, d, owner, role) for d in demandes)
    duree = dict(DUREES)[r['duree']]
    if role in SIGNATAIRES:
        auto = mode_effectif(desk) == 'auto'
        # instance non marquée « du cabinet » : avertissement à chaque dépôt et seconde case (le service la revérifie)
        tiers = '' if r['cabinet'] else ('<p class="ax-alert" role="alert">%s</p><label class="d51-check"><input type="checkbox" name="confirmer_tiers" required> '
                                         'J’accepte que ce PDF soit déposé tel quel sur une instance qui n’est pas celle du cabinet</label>') % _e(AVERTISSEMENT_TIERS)
        locale = ('<p class="ax-alert">Instance locale : le lien de signature ne s’ouvre que sur cet ordinateur. Pour faire signer un client à distance, '
                  'utilisez l’instance du serveur du cabinet.</p>') if est_locale(r['adresse']) else ''
        corps = ('<form class="m5-form z108-form z108-nouvelle" data-api="%spreparer" data-reload><input type="hidden" name="matter" value="%s">'
                 '<input type="hidden" name="chemin" value="%s"><input type="hidden" name="cle" value="%s"><p>%s</p>'
                 '<p class="ax-muted"><span>Conservation sur le service :</span> <strong>%s</strong></p>%s'
                 '<label class="d51-check"><input type="checkbox" name="confirmer" required> %s</label>%s'
                 '<button class="ax-btn" type="submit">%s</button><p role="status" class="m5-status"></p></form>') % (
            PREFIXE, _e(m['id'], quote=True), _e(chemin, quote=True), secrets.token_hex(12),
            _e('Le PDF sera déposé sur le service SignaturePDF, conservé après la dernière signature pendant la durée ci-dessous, puis '
               'effacé par le service. Vous obtiendrez un lien à adresser vous-même aux signataires.' if auto else
               'Parcours manuel : vous déposerez vous-même le PDF dans SignaturePDF, puis rangerez ici le PDF signé.'), _e(duree), locale,
            _e(('Je confirme le dépôt de ce PDF sur le service de signature du cabinet' if r['cabinet'] else 'Je confirme le dépôt de ce PDF sur le service de signature')
               if auto else 'Je prépare la signature de ce PDF par lien'), tiers,
            _e('Créer le lien de signature' if auto else 'Préparer la signature par lien')) + corps
    else:
        corps = '<p class="ax-muted">La signature par lien est lancée par un avocat du cabinet.</p>' + corps
    return tete + corps + '</section>'


def page_route(env, cfg, auth, prefix, path, args, shell):
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    owner, role = actor(env)
    desk = Desk(cfg)
    try:
        try:
            corps = page_html(desk, prefix, args, owner, role, auth)
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code in ('introuvable', 'pdf_requis', 'dossier_reserve', 'dossier_absent'):
                return None                                                     # même réponse qu'une page absente
            corps = '<section class="z108"><h1>Faire signer par lien</h1><p class="ax-alert" role="status">%s</p></section>' % _e(_message(code))
    finally:
        desk.db.close()
    head = '<script defer src="%s/static/v56108-signaturepdf.js"></script>' % _e(prefix, quote=True)
    out = page_out(shell('Faire signer par lien', corps, prefix, auth['csrf'], PAGE, head=head))
    out['headers'] = [h for h in out.get('headers', []) if h[0].lower() != 'cache-control'] + [('Cache-Control', 'private, no-store')]
    return out
