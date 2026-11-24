"""5.6.109 (lot G2, G03) : demandes au client et partage d'une version approuvée exacte, dans le dossier existant.

Raccordé aux services existants, sans second registre ni page nouvelle :
- **point d'entrée** : bloc « Partager avec le client » de l'onglet Correspondances du dossier (``dossier5679.echanges_html``), sous les
  « Pièces demandées » et « Remis au client » de ``remises5697`` (inchangés) ; aucune entrée de menu ;
- **ressource versionnée** : un partage désigne des VERSIONS du registre commun des livrables (``livrables5689`` : identifiant de document,
  identifiant de version, empreinte SHA-256). Seule une version relue et validée par le cabinet est partageable ; une version suivante
  n'entre jamais dans un partage déjà fait : la réponse d'un client reste attachée à la version qu'il a vue ;
- **adaptateur distinct, sans serveur obligatoire** : « Paquet à remettre » (archive produite sur le poste : documents exacts, manifeste,
  formulaire de réponse) que le cabinet transmet par son propre canal ; « Lien Nextcloud » seulement si le partage de ``remises5697`` est
  configuré et activé (copie de remise en lecture seule, révocation relue chez Nextcloud). Aucune URL fictive, aucun port ouvert ;
- **accès** : chaque lecture d'une ressource (``acceder``) relit le partage (état, révocation, expiration, droit distinct lire /
  télécharger / déposer / répondre, version exacte) ; tout refus est le même refus neutre. Le jeton de retour n'est conservé que haché
  (colonne ``secret_hash``, retirée de tout export) ; il ne prouve pas l'identité d'une personne, seulement l'appartenance d'un retour à CE
  partage : le client A ne peut ni lire ni répondre pour B ;
- **réception** : un fichier reçu va d'abord en QUARANTAINE (zone privée du poste, hors du dossier) avec provenance, nom neutralisé, taille,
  type réel reconnu par sa signature et empreinte ; il n'est classé dans le dossier que par la décision d'un avocat (dépôt exclusif, relu et
  comparé : reçu de classement). Une réponse du client est une pièce à apprécier, jamais une signature électronique qualifiée ;
- **effet externe** : produire le paquet ou créer le lien est une action d'une PERSONNE (avocat, administrateur, droit « envoyer ») ; un
  modèle ou un automatisme est refusé. Tout fonctionne IA en pause (aucun appel de modèle).
"""
import base64
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
from html import escape as e
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import unicodedata
import uuid
import zipfile

from .common import Stop

PREFIXE = 'm56109/partage/'
ROLES = ('administrateur', 'avocat', 'assistant')
VALIDEURS = ('administrateur', 'avocat')
ACTEURS_AUTOMATIQUES = ('agent', 'agent_cabinet', 'ia', 'modele')
PERMISSIONS = {'lire': 'Lire', 'telecharger': 'Télécharger', 'deposer': 'Déposer des fichiers', 'repondre': 'Répondre'}
GENRES = {'piece': 'Pièce demandée', 'question': 'Question', 'validation': 'Validation d’une version'}
CANAUX = {'paquet': 'Paquet à remettre (sans serveur)', 'nextcloud': 'Lien Nextcloud configuré'}
ETATS = {'prepare': 'Préparé : rien n’est parti', 'confirme': 'Partage confirmé', 'revoque': 'Accès révoqué', 'expire': 'Expiré'}
ETATS_QUARANTAINE = {'quarantaine': 'En quarantaine (non classé)', 'refuse': 'Refusé', 'classe': 'Classé dans le dossier'}
DECISIONS_VALIDATION = {'approuve': 'Approuvée par le client', 'refuse': 'Refusée par le client'}
LIMITE_COPIE = ('Une révocation ferme les accès futurs ; une copie déjà téléchargée par le destinataire ne peut pas être retirée à distance.')
MENTION_REPONSE = 'Réponse du client : pièce à apprécier, ce n’est pas une signature électronique qualifiée.'
MAX_RESSOURCES, MAX_DEMANDES, MAX_JOURS = 30, 30, 365
MAX_FICHIER = 6 * 1024 * 1024
MAX_RETOUR = 9_000_000                     # corps JSON d'un paquet de retour (base64) : borne propre à cette route
MAX_ENTREES, MAX_DECOMPRESSE = 40, 24 * 1024 * 1024
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
IDENT = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
# Signature des types admis : le type réel est lu dans les octets, jamais déduit du seul nom.
SIGNATURES = {'.pdf': (b'%PDF-',), '.docx': (b'PK\x03\x04',), '.xlsx': (b'PK\x03\x04',), '.odt': (b'PK\x03\x04',), '.ods': (b'PK\x03\x04',),
              '.zip': (b'PK\x03\x04',), '.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
              '.tif': (b'II*\x00', b'MM\x00*'), '.tiff': (b'II*\x00', b'MM\x00*'), '.rtf': (b'{\\rtf',), '.doc': (b'\xd0\xcf\x11\xe0',),
              '.xls': (b'\xd0\xcf\x11\xe0',)}
TEXTES = ('.txt', '.csv', '.eml')
EXECUTABLES = (b'MZ', b'\x7fELF', b'#!', b'\xca\xfe\xba\xbe', b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS partages_client56109(id TEXT PRIMARY KEY, cabinet TEXT NOT NULL, matter_id TEXT NOT NULL, destinataire TEXT NOT NULL,
  destinataire_verifie_par TEXT NOT NULL, canal TEXT NOT NULL CHECK(canal IN ('paquet','nextcloud')), permissions TEXT NOT NULL,
  expire_le TEXT NOT NULL, etat TEXT NOT NULL CHECK(etat IN ('prepare','confirme','revoque','expire')), secret_hash TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1), remise_id TEXT NOT NULL DEFAULT '', paquet_sha256 TEXT NOT NULL DEFAULT '',
  nextcloud_destinataire TEXT NOT NULL DEFAULT '{}', origine TEXT NOT NULL DEFAULT 'dossier', objet_lie TEXT NOT NULL DEFAULT '',
  cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, confirme_par TEXT NOT NULL DEFAULT '', confirme_le TEXT NOT NULL DEFAULT '',
  revoque_par TEXT NOT NULL DEFAULT '', revoque_le TEXT NOT NULL DEFAULT '', motif_revocation TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS partages_client56109_dossier ON partages_client56109(matter_id, cree_le);
CREATE TABLE IF NOT EXISTS partages_client_ressources56109(partage_id TEXT NOT NULL, rang INTEGER NOT NULL, item_id TEXT NOT NULL, document_id TEXT NOT NULL,
  version_id TEXT NOT NULL, sha256 TEXT NOT NULL CHECK(length(sha256)=64), taille INTEGER NOT NULL DEFAULT 0, titre TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(partage_id, rang), UNIQUE(partage_id, document_id, version_id));
CREATE TABLE IF NOT EXISTS partages_client_demandes56109(id TEXT PRIMARY KEY, partage_id TEXT NOT NULL, rang INTEGER NOT NULL,
  genre TEXT NOT NULL CHECK(genre IN ('piece','question','validation')), libelle TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '',
  version_id TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', UNIQUE(partage_id, rang));
CREATE TABLE IF NOT EXISTS partages_client_reponses56109(id TEXT PRIMARY KEY, partage_id TEXT NOT NULL, demande_id TEXT NOT NULL, genre TEXT NOT NULL,
  texte TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '', document_id TEXT NOT NULL DEFAULT '', version_id TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL DEFAULT '', quarantaine TEXT NOT NULL DEFAULT '[]', recu_sha256 TEXT NOT NULL, source TEXT NOT NULL,
  saisi_par TEXT NOT NULL, recu_le TEXT NOT NULL, UNIQUE(partage_id, demande_id, recu_sha256));
CREATE TABLE IF NOT EXISTS partages_client_quarantaine56109(id TEXT PRIMARY KEY, partage_id TEXT NOT NULL, matter_id TEXT NOT NULL,
  demande_id TEXT NOT NULL DEFAULT '', nom_origine TEXT NOT NULL, nom_neutralise TEXT NOT NULL, extension TEXT NOT NULL, type_reel TEXT NOT NULL,
  taille INTEGER NOT NULL, sha256 TEXT NOT NULL CHECK(length(sha256)=64), fichier TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL CHECK(etat IN ('quarantaine','refuse','classe')), motif TEXT NOT NULL DEFAULT '', provenance TEXT NOT NULL,
  recu_le TEXT NOT NULL, decide_par TEXT NOT NULL DEFAULT '', decide_le TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
  chemin_classe TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, UNIQUE(partage_id, sha256));
CREATE TABLE IF NOT EXISTS partages_client_ops56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
TABLES = ('partages_client56109', 'partages_client_ressources56109', 'partages_client_demandes56109', 'partages_client_reponses56109',
          'partages_client_quarantaine56109', 'partages_client_ops56109')
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'action_reservee_a_une_personne': '403 Forbidden', 'acteur_requis': '403 Forbidden', 'version_perimee': '409 Conflict',
           'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict', 'partage_deja_confirme': '409 Conflict',
           'partage_non_confirme': '409 Conflict', 'partage_inactif': '409 Conflict', 'partage_revoque': '409 Conflict', 'partage_expire': '409 Conflict', 'deja_decide': '409 Conflict',
           'version_modifiee_depuis_l_approbation': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'fichier_trop_volumineux': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable',
           'registre_occupe': '503 Service Unavailable', 'partage_nextcloud_indisponible': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Élément introuvable, ou action non permise.',
    'route_inconnue': 'Élément introuvable, ou action non permise.',
    'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'action_reservee_a_une_personne': 'Un partage avec le client est décidé par une personne du cabinet : un automatisme ou un modèle ne peut pas le déclencher.',
    'version_perimee': 'Le partage a changé depuis votre affichage : rechargez la page. Rien n’a été modifié.',
    'cle_reutilisee': 'Cette demande a déjà servi pour un autre contenu : rechargez la page.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'operation_en_cours': 'La même demande est déjà en cours : patientez puis rechargez.',
    'destinataire_requis': 'Indiquez l’adresse du destinataire.',
    'destinataire_non_verifie': 'Ce destinataire n’est pas un correspondant client du dossier : cochez « Identité vérifiée » après l’avoir contrôlée.',
    'ressource_requise': 'Choisissez au moins une version approuvée à partager, ou une demande au client.',
    'version_non_approuvee': 'Seule une version relue et validée par le cabinet peut être partagée.',
    'version_sans_empreinte': 'Cette version n’a pas d’empreinte vérifiée : elle ne peut pas être partagée.',
    'version_hors_dossier': 'Cette version n’appartient pas à ce dossier.',
    'version_modifiee_depuis_l_approbation': 'Le fichier stocké ne correspond plus à la version approuvée : rien n’a été partagé.',
    'expiration_requise': 'Indiquez une date d’expiration (365 jours au plus).',
    'expiration_invalide': 'Date d’expiration invalide : elle doit être future et à 365 jours au plus.',
    'permission_inconnue': 'Droit inconnu.',
    'canal_invalide': 'Canal de partage inconnu.',
    'nextcloud_fichier_requis': 'Le lien Nextcloud ne s’applique qu’à des fichiers ; un livrable structuré se remet par paquet.',
    'partage_nextcloud_desactive': 'Le partage Nextcloud n’est pas activé : utilisez le paquet à remettre.',
    'partage_nextcloud_indisponible': 'Nextcloud n’est pas configuré pour le partage : utilisez le paquet à remettre.',
    'partage_deja_confirme': 'Ce partage est déjà confirmé.',
    'partage_non_confirme': 'Ce partage n’est pas encore confirmé.',
    'partage_revoque': 'Ce partage est révoqué : aucun retour n’est plus accepté.',
    'partage_expire': 'Ce partage a expiré : aucun retour n’est plus accepté.',
    'partage_inactif': 'Ce partage n’accepte pas de fichier (il n’est pas confirmé, a expiré ou a été révoqué).',
    'paquet_invalide': 'Paquet de retour illisible ou non conforme.',
    'paquet_autre_partage': 'Ce paquet de retour ne correspond pas à ce partage.',
    'version_non_conforme': 'La validation reçue ne porte pas exactement sur la version demandée (document, version ou empreinte) : rien n’a été enregistré.',
    'droit_absent': 'Ce partage n’autorise pas ce type de retour.',
    'type_fichier_refuse': 'Type de fichier refusé.',
    'fichier_trop_volumineux': 'Fichier trop volumineux.',
    'deja_decide': 'Ce fichier a déjà été traité.',
    'destination_inconnue': 'Destination inconnue.',
    'motif_requis': 'Indiquez le motif.',
    'requete_trop_longue': 'Requête trop longue.',
    'controle_acces_indisponible': 'Contrôle des droits indisponible : rien n’est affiché ni modifié.',
    'registre_occupe': 'Registre occupé : réessayez dans un instant.',
}


# ------------------------------------------------------------------------------------------------ socle commun du lot G2
def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) : tables des quatre modules du lot G2 ; sans changement, aucune écriture (jamais pendant une lecture)."""
    from . import appels56109, kit_audience56109, remplacements56109
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiences5688'").fetchone():
        from . import audiences5688                             # H1 : fiche d'audience lue par G07/G08, créée ici au démarrage, jamais en lecture
        audiences5688.schema(desk)
    for script in (SCHEMA, appels56109.SCHEMA, remplacements56109.SCHEMA, kit_audience56109.SCHEMA):
        noms = re.findall(r'CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', script)
        existants = {r[0] for r in desk.db.execute('SELECT name FROM sqlite_master')}
        if any(n not in existants for n in noms):
            if desk.db.in_transaction:
                desk.db.commit()
            desk.db.executescript(script)
            desk.db.commit()


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha(*morceaux):
    return hashlib.sha256('|'.join(str(m) for m in morceaux).encode('utf-8')).hexdigest()


def canon(valeur):
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def cabinet(desk):
    from .agenda5697 import cabinet_id
    return cabinet_id(desk)


def tz(desk):
    from .agenda5634 import _tz
    return _tz(desk)


def jour_cabinet(desk):
    return datetime.now(tz(desk)).date()


def texte(valeur, maxi, code='champ_invalide'):
    v = str(valeur if valeur is not None else '').replace('\x00', '').strip()
    if len(v) > maxi or any(ord(c) < 32 and c not in '\t\r\n' for c in v):
        raise Stop(code)
    return v


def acteur(acteur_):
    from .acces5697 import acteur_de
    return acteur_de(acteur_)


def personne(desk, a):
    """Une action externe (partage, transmission) est le geste d'une personne : jamais un automatisme ni un modèle."""
    if a.role == 'agent' or a.compte in ACTEURS_AUTOMATIQUES or getattr(desk, 'origin_context', None) == 'automatic':
        raise Stop('action_reservee_a_une_personne')


def valideur(a):
    if a.role not in VALIDEURS:
        raise Stop('role_insuffisant')


def dossier(desk, a, mid):
    """Dossier lisible par l'acteur (absent ou réservé : même refus neutre) ; contrôle illisible : 503."""
    from . import gestion5697 as g
    if not IDENT.fullmatch(str(mid or '')):
        raise Stop('introuvable')
    try:
        return g.dossier(desk, a, str(mid))
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            raise
        raise Stop('introuvable') from None


def masques_et_droits(desk, a):
    from . import acces5697, membres5650
    try:
        hidden = acces5697.masques(desk, a)
        droits = membres5650.effective_rights(desk.c['state_dir'], a.compte, a.role) if a.role in membres5650.ROLES else {}
    except Stop as ex:
        if str(ex) in ('role_insuffisant', 'acteur_requis'):
            raise
        raise Stop('controle_acces_indisponible') from None
    return hidden, droits


def executer(desk, table, compte, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409).
    ``table`` est une constante du code (table des reçus du module appelant), jamais une valeur reçue."""
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    emp = sha(canon({'n': nature, 'p': parametres}))
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM %s WHERE acteur=? AND cle=?' % table, (compte, cle)).fetchone()
    if row:
        if row['empreinte'] != emp:
            raise Stop('cle_reutilisee')
        if row['etat'] == 'fait':
            return {**json.loads(row['resultat']), 'rejoue': True}
        if row['etat'] == 'echec':
            raise Stop(json.loads(row['resultat']).get('error') or 'operation_echouee')
        raise Stop('operation_en_cours')
    try:
        desk.db.execute('INSERT INTO %s(acteur,cle,nature,empreinte,etat,resultat,cree_le) VALUES(?,?,?,?,?,?,?)' % table,
                        (compte, cle, nature, emp, 'en_cours', '{}', maintenant()))
        desk.db.commit()
    except sqlite3.IntegrityError:
        desk.db.rollback()
        raise Stop('operation_en_cours') from None
    try:
        out = fn()
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne'
        if code in ('version_perimee', 'operation_en_cours', 'controle_acces_indisponible', 'registre_occupe'):
            desk.db.execute('DELETE FROM %s WHERE acteur=? AND cle=?' % table, (compte, cle))   # erreur passagère : la même clé pourra resservir
        else:
            desk.db.execute("UPDATE %s SET etat='echec', resultat=? WHERE acteur=? AND cle=?" % table, (json.dumps({'error': code}), compte, cle))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE %s SET etat='fait', resultat=? WHERE acteur=? AND cle=?" % table, (json.dumps(out, ensure_ascii=False, default=str), compte, cle))
    desk.db.commit()
    return {**out, 'rejoue': False}


# ------------------------------------------------------------------------------------------------ versions exactes du registre commun
def version_registre(desk, a, mid, document_id, version_id, approuvee=True):
    """Version du registre commun (``livrables5689.charger`` : droits relus), du dossier ``mid``, approuvée par le cabinet (relue et validée)
    et munie d'une empreinte. Rend ``{item_id, document_id, version_id, sha256, taille, titre, row}``."""
    from . import livrables5689 as liv
    a = acteur(a)
    hidden, droits = masques_et_droits(desk, a)
    if not IDENT.fullmatch(str(document_id or '')) or not str(version_id or '') or len(str(version_id)) > 200:
        raise Stop('introuvable')
    try:
        row = liv.charger(desk, str(document_id), hidden, version_id=str(version_id), droits=droits)
    except Stop:
        raise Stop('introuvable') from None
    if row['matter_id'] != mid:
        raise Stop('version_hors_dossier')
    if liv.provisoire(row['version_id']):
        raise Stop('version_sans_empreinte')
    empreinte, taille = empreinte_version(desk, row)
    if not SHA.fullmatch(empreinte):
        raise Stop('version_sans_empreinte')
    if approuvee and not est_approuvee(desk, row):
        raise Stop('version_non_approuvee')
    return {'item_id': row['id'], 'document_id': row['document_id'], 'version_id': row['version_id'], 'sha256': empreinte, 'taille': taille,
            'titre': row['title'], 'row': row}


def est_approuvee(desk, row):
    if row['review_state'] == 'reviewed':
        return True
    return bool(desk.db.execute("SELECT 1 FROM prepared_reviews5689 WHERE document_id=? AND version_id=? AND scope='cabinet' AND decision IN ('relu','valide') LIMIT 1",
                                (row['document_id'], row['version_id'])).fetchone())


def empreinte_version(desk, row):
    """Empreinte et taille d'une version : celle du fichier inscrit, ou celle du contenu d'un livrable structuré (``artefacts56109``)."""
    from . import artefacts56109 as A
    if row.get('source_service') == A.PRODUCTEUR:
        c = desk.db.execute('SELECT sha256, length(CAST(payload AS BLOB)) AS n FROM livrables_contenus56109 WHERE item_id=?', (row['id'],)).fetchone()
        return (c['sha256'], int(c['n'] or 0)) if c else ('', 0)
    return str(row.get('sha256') or ''), int(row.get('size') or 0)


def octets_version(desk, row, client=None, attendu=''):
    """Octets EXACTS d'une version : contenu canonique d'un livrable structuré, ou fichier relu dans le stockage ; l'empreinte est recomparée.
    Un fichier modifié depuis n'est jamais servi à la place de la version choisie. Rend ``(octets, nom)``."""
    from . import artefacts56109 as A
    attendu = attendu or empreinte_version(desk, row)[0]
    if row.get('source_service') == A.PRODUCTEUR:
        c = desk.db.execute('SELECT payload, titre FROM livrables_contenus56109 WHERE item_id=?', (row['id'],)).fetchone()
        if not c:
            raise Stop('introuvable')
        raw, nom = c['payload'].encode('utf-8'), nom_sur(c['titre'] or 'livrable') + '.json'
    else:
        path = str(row.get('path') or '')
        if not path:
            raise Stop('introuvable')
        from .office440 import dav_client
        client = client or dav_client(desk)
        try:
            raw = client.download(client.stat(path))
        except Stop:
            raise Stop('version_modifiee_depuis_l_approbation') from None
        nom = nom_sur(PurePosixPath(path).name)
    if hashlib.sha256(raw).hexdigest() != attendu:
        raise Stop('version_modifiee_depuis_l_approbation')
    return raw, nom


def versions_partageables(desk, a, mid, limite=60):
    """Versions approuvées du dossier (registre commun), pour le choix du panneau ; droits relus par ``charger`` sur chacune."""
    out = []
    try:
        rows = desk.db.execute("SELECT document_id, version_id FROM prepared_items5689 WHERE matter_id=? AND lifecycle IN ('current','replaced','archived') "
                               "ORDER BY updated DESC LIMIT 200", (mid,)).fetchall()
    except sqlite3.Error:
        return []
    for r in rows:
        try:
            out.append({k: v for k, v in version_registre(desk, a, mid, r['document_id'], r['version_id']).items() if k != 'row'})
        except Stop:
            continue
        if len(out) >= limite:
            break
    return out


def nom_sur(nom):
    """Nom neutralisé : dernier segment, caractères de contrôle et séparateurs retirés, longueur bornée ; jamais un chemin."""
    nom = unicodedata.normalize('NFC', str(nom or '')).replace('\\', '/')
    nom = PurePosixPath(nom).name
    nom = re.sub(r'[\x00-\x1f\x7f<>:"|?*/]', '_', nom).strip(' .')
    nom = re.sub(r'\s+', ' ', nom)
    if not nom or nom in ('.', '..'):
        nom = 'fichier'
    base, ext = os.path.splitext(nom)
    return (base[:100] or 'fichier') + ext[:10].lower()


def controler_fichier(nom, raw):
    """Contrôle avant toute conservation : taille, extension admise, type réel lu dans les octets, exécutable refusé. Rend ``(nom, ext, type_reel)``."""
    propre = nom_sur(nom)
    ext = os.path.splitext(propre)[1].lower()
    if not raw:
        raise Stop('fichier_requis')
    if len(raw) > MAX_FICHIER:
        raise Stop('fichier_trop_volumineux')
    if any(raw.startswith(x) for x in EXECUTABLES):
        raise Stop('type_fichier_refuse')
    if ext in SIGNATURES:
        if not any(raw.startswith(s) for s in SIGNATURES[ext]):
            raise Stop('type_fichier_refuse')                # le nom annonce un type que les octets démentent
        return propre, ext, ext.lstrip('.')
    if ext in TEXTES:
        try:
            raw[:65536].decode('utf-8')
        except UnicodeDecodeError:
            try:
                raw[:65536].decode('cp1252')
            except UnicodeDecodeError:
                raise Stop('type_fichier_refuse') from None
        if b'\x00' in raw[:65536]:
            raise Stop('type_fichier_refuse')
        return propre, ext, 'texte'
    raise Stop('type_fichier_refuse')


def zone_quarantaine(desk):
    p = Path(desk.c['state_dir']) / 'quarantaine56109'
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def ecrire_exclusif(chemin, raw):
    fd = os.open(str(chemin), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)


def donnee_fichier(valeur):
    """Fichier envoyé par le formulaire commun (``{nom, contenu: adresse de données}``) ou ``{nom, contenu_b64}`` ; rend ``(nom, octets)``."""
    if not isinstance(valeur, dict):
        raise Stop('fichier_requis')
    nom = str(valeur.get('nom') or '')
    brut = str(valeur.get('contenu_b64') or valeur.get('contenu') or '')
    if brut.startswith('data:'):
        brut = brut.split(',', 1)[1] if ',' in brut else ''
    try:
        raw = base64.b64decode(brut, validate=True)
    except ValueError:
        raise Stop('fichier_invalide') from None
    return nom, raw


# ------------------------------------------------------------------------------------------------ préparer, confirmer, révoquer
def _adresses_client(desk, m):
    out = {str(c.get('email') or '').strip().lower() for c in m.get('correspondents') or [] if c.get('role') == 'client' and '@' in str(c.get('email') or '')}
    try:
        out |= {str(r[0]).strip().lower() for r in desk.db.execute("SELECT courriel FROM dossier_parties5697 WHERE dossier_id=? AND role='client' AND courriel LIKE '%@%'",
                                                                    (str(m.get('id') or ''),))}
    except sqlite3.Error:
        pass
    return out


def _expiration(desk, valeur):
    if not valeur:
        raise Stop('expiration_requise')
    try:
        jour = date.fromisoformat(str(valeur)[:10])
    except ValueError:
        raise Stop('expiration_invalide') from None
    aujourdhui = jour_cabinet(desk)
    if jour < aujourdhui or jour > aujourdhui + timedelta(days=MAX_JOURS):
        raise Stop('expiration_invalide')
    # fin de la journée choisie, au fuseau du cabinet, en UTC
    return datetime.combine(jour + timedelta(days=1), time.min, tz(desk)).astimezone(timezone.utc).isoformat(timespec='seconds')


def _permissions(valeur):
    if isinstance(valeur, dict):
        choisies = [k for k, v in valeur.items() if v is True]
        inconnues = [k for k in valeur if k not in PERMISSIONS]
    else:
        choisies = [str(x) for x in (valeur or [])]
        inconnues = [x for x in choisies if x not in PERMISSIONS]
    if inconnues:
        raise Stop('permission_inconnue')
    return {k: (k in choisies) for k in PERMISSIONS}


def _lignes(valeur, maxi=MAX_DEMANDES):
    if isinstance(valeur, str):
        valeur = valeur.splitlines()
    out = [texte(x, 300) for x in (valeur or []) if str(x or '').strip()]
    if len(out) > maxi:
        raise Stop('trop_de_demandes')
    return out


def _paires(valeur):
    """Versions choisies : ``[{document_id, version_id}]`` ou ``["document|version"]`` (liste multiple du formulaire)."""
    if isinstance(valeur, str):
        valeur = [valeur] if valeur else []
    out = []
    for x in valeur or []:
        if isinstance(x, dict):
            out.append((str(x.get('document_id') or ''), str(x.get('version_id') or '')))
        else:
            d, _, v = str(x).partition('|')
            out.append((d, v))
    if len(out) > MAX_RESSOURCES:
        raise Stop('trop_de_ressources')
    return list(dict.fromkeys(out))


def preparer(desk, acteur_, mid, data, cle, origine='dossier', objet_lie=''):
    """Panneau « Partager avec le client » : destinataire vérifié, versions approuvées exactes, demandes, droits, expiration. Rien ne part :
    le partage est « préparé » et son aperçu exact est rendu. Avocat et administrateur (assistant : 403)."""
    a = acteur(acteur_)
    personne(desk, a)
    valideur(a)
    m = dossier(desk, a, mid)
    destinataire = texte(data.get('destinataire'), 200).lower()
    if '@' not in destinataire:
        raise Stop('destinataire_requis')
    if destinataire in _adresses_client(desk, m):
        verifie = 'correspondant client du dossier'
    elif data.get('identite_verifiee') is True:
        verifie = 'identité vérifiée par ' + a.compte
    else:
        raise Stop('destinataire_non_verifie')
    canal = str(data.get('canal') or 'paquet')
    if canal not in CANAUX:
        raise Stop('canal_invalide')
    permissions = _permissions(data.get('permissions') if data.get('permissions') is not None else ['lire', 'telecharger', 'repondre'])
    expire = _expiration(desk, data.get('expiration'))
    ressources = [version_registre(desk, a, m['id'], d, v) for d, v in _paires(data.get('ressources'))]
    validations = [version_registre(desk, a, m['id'], d, v) for d, v in _paires(data.get('validations'))]
    pieces, questions = _lignes(data.get('pieces')), _lignes(data.get('questions'))
    if not ressources and not (pieces or questions or validations):
        raise Stop('ressource_requise')
    if pieces and not permissions['deposer']:
        permissions['deposer'] = True                         # une pièce demandée se dépose : droit accordé explicitement, visible dans l'aperçu
    if (questions or validations) and not permissions['repondre']:
        permissions['repondre'] = True
    nc = {}
    if canal == 'nextcloud':
        if any(r['row']['source_service'] == 'artefacts56109' or not r['row']['path'] for r in ressources) or not ressources:
            raise Stop('nextcloud_fichier_requis')
        from . import remises5697
        if not remises5697.reglages(desk)['actif']:
            raise Stop('partage_nextcloud_desactive')
        dn = data.get('nextcloud_destinataire') if isinstance(data.get('nextcloud_destinataire'), dict) else {'type': 'lien', 'id': destinataire}
        if str(dn.get('type')) not in ('utilisateur', 'lien'):
            raise Stop('destinataire_requis')
        nc = {'type': str(dn['type']), 'id': texte(dn.get('id') or destinataire, 160)}
    parametres = {'mid': m['id'], 'dest': destinataire, 'canal': canal, 'perm': permissions, 'exp': expire, 'nc': nc, 'origine': origine, 'objet': objet_lie,
                  'res': [(r['document_id'], r['version_id'], r['sha256']) for r in ressources], 'pieces': pieces, 'questions': questions,
                  'val': [(r['document_id'], r['version_id'], r['sha256']) for r in validations]}

    def faire():
        pid = 'pc-' + uuid.uuid4().hex[:24]
        quand = maintenant()
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            desk.db.execute('INSERT INTO partages_client56109(id,cabinet,matter_id,destinataire,destinataire_verifie_par,canal,permissions,expire_le,etat,'
                            'nextcloud_destinataire,origine,objet_lie,cree_par,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (pid, cabinet(desk), m['id'], destinataire, verifie, canal, canon(permissions), expire, 'prepare', canon(nc), origine,
                             str(objet_lie)[:120], a.compte, quand, quand))
            for rang, r in enumerate(ressources, 1):
                desk.db.execute('INSERT INTO partages_client_ressources56109(partage_id,rang,item_id,document_id,version_id,sha256,taille,titre) VALUES(?,?,?,?,?,?,?,?)',
                                (pid, rang, r['item_id'], r['document_id'], r['version_id'], r['sha256'], r['taille'], r['titre'][:300]))
            rang = 0
            for genre, liste in (('piece', pieces), ('question', questions)):
                for libelle in liste:
                    rang += 1
                    desk.db.execute('INSERT INTO partages_client_demandes56109(id,partage_id,rang,genre,libelle) VALUES(?,?,?,?,?)',
                                    ('dc-' + uuid.uuid4().hex[:20], pid, rang, genre, libelle))
            for r in validations:
                rang += 1
                desk.db.execute('INSERT INTO partages_client_demandes56109(id,partage_id,rang,genre,libelle,document_id,version_id,sha256) VALUES(?,?,?,?,?,?,?,?)',
                                ('dc-' + uuid.uuid4().hex[:20], pid, rang, 'validation', 'Valider : ' + r['titre'][:250], r['document_id'], r['version_id'], r['sha256']))
            desk.db.commit()
        except BaseException:
            desk.db.rollback()
            raise
        desk.audit('partage56109_prepare', {'partage': pid, 'matter': m['id'], 'canal': canal, 'ressources': len(ressources), 'par': a.compte})
        return {'id': pid}

    out = executer(desk, 'partages_client_ops56109', a.compte, cle, 'preparer', parametres, faire)
    return dict(vue(desk, a, out['id']), rejoue=out['rejoue'])


def _partage(desk, a, pid):
    if not IDENT.fullmatch(str(pid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM partages_client56109 WHERE id=?', (str(pid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    dossier(desk, a, row['matter_id'])
    return dict(row)


def _version(row, attendue):
    try:
        attendue = int(str(attendue))
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None
    if attendue != row['version']:
        raise Stop('version_perimee')


def ressources(desk, pid):
    return [dict(r) for r in desk.db.execute('SELECT * FROM partages_client_ressources56109 WHERE partage_id=? ORDER BY rang', (pid,))]


def demandes(desk, pid):
    return [dict(r) for r in desk.db.execute('SELECT * FROM partages_client_demandes56109 WHERE partage_id=? ORDER BY rang', (pid,))]


def _manifeste(desk, row, res, dem, jeton, noms):
    return {'format': 'axh-partage/1', 'partage_id': row['id'], 'destinataire': row['destinataire'], 'expire_le': row['expire_le'],
            'permissions': json.loads(row['permissions']), 'jeton_retour': jeton,
            'ressources': [{'rang': r['rang'], 'titre': r['titre'], 'document_id': r['document_id'], 'version_id': r['version_id'], 'sha256': r['sha256'],
                            'taille': r['taille'], 'fichier': noms.get(r['rang'], '')} for r in res],
            'demandes': [{'id': d['id'], 'genre': d['genre'], 'libelle': d['libelle'], 'document_id': d['document_id'], 'version_id': d['version_id'],
                          'sha256': d['sha256']} for d in dem],
            'avertissements': [LIMITE_COPIE, MENTION_REPONSE]}


def _paquet(desk, row, client=None):
    """Archive « paquet à remettre » : documents exacts (empreintes recomparées), manifeste, formulaire de réponse. Rend ``(octets, jeton)``."""
    from . import pause5629
    res, dem = ressources(desk, row['id']), demandes(desk, row['id'])
    jeton = secrets.token_urlsafe(24)
    noms, fichiers = {}, []
    perm = json.loads(row['permissions'])
    for r in res:
        item = desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (r['item_id'],)).fetchone()
        if not item:
            raise Stop('version_modifiee_depuis_l_approbation')
        pause5629.checkpoint(desk)
        raw, nom = octets_version(desk, dict(item), client, r['sha256'])
        noms[r['rang']] = 'documents/%02d %s' % (r['rang'], nom)
        if perm.get('lire') or perm.get('telecharger'):
            fichiers.append((noms[r['rang']], raw))
    manifeste = _manifeste(desk, row, res, dem, jeton, noms)
    modele = {'format': 'axh-reponse/1', 'partage_id': row['id'], 'jeton_retour': jeton,
              'reponses': [{'demande_id': d['id'], 'texte': '', 'validation': ({'document_id': d['document_id'], 'version_id': d['version_id'], 'sha256': d['sha256'],
                                                                               'decision': ''} if d['genre'] == 'validation' else None), 'fichiers': []} for d in dem]}
    lisez = ('Partage du cabinet\n\nCe paquet contient exactement les versions listées dans manifeste.json (empreinte SHA-256 de chaque document).\n'
             'Pour répondre : complétez reponse.json, ajoutez les fichiers demandés dans un dossier « fichiers », et renvoyez le tout au cabinet\n'
             'dans une archive ZIP par le canal convenu. Validité : jusqu’au %s (UTC).\n\n%s\n%s\n') % (row['expire_le'], LIMITE_COPIE, MENTION_REPONSE)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('LISEZMOI.txt', lisez)
        z.writestr('manifeste.json', json.dumps(manifeste, ensure_ascii=False, indent=1))
        z.writestr('reponse.json', json.dumps(modele, ensure_ascii=False, indent=1))
        for nom, raw in fichiers:
            z.writestr(nom, raw)
    return tampon.getvalue(), jeton


def confirmer(desk, acteur_, pid, data, client=None, ocs=None):
    """« Confirmer le partage » : geste d'une personne (avocat, administrateur, droit « envoyer »), version attendue, versions relues une
    dernière fois. Paquet : archive rendue UNE fois (le jeton de retour n'est conservé que haché). Nextcloud : copie de remise et lien de
    ``remises5697`` (lecture seule, expiration), empreintes comparées à celles des versions approuvées."""
    from . import gestion5697 as g
    a = acteur(acteur_)
    personne(desk, a)
    valideur(a)
    row = _partage(desk, a, pid)
    g.droit(desk, a, 'envoyer')
    unique = {}                                                 # paquet (il contient le jeton en clair) : rendu, jamais rangé dans le reçu

    def faire():
        r = desk.db.execute('SELECT * FROM partages_client56109 WHERE id=?', (row['id'],)).fetchone()
        _version(r, data.get('version'))
        if r['etat'] != 'prepare':
            raise Stop('partage_deja_confirme')
        if r['expire_le'] <= maintenant():
            raise Stop('partage_expire')
        for x in ressources(desk, r['id']):                     # droits et versions relus au moment de l'effet
            v = version_registre(desk, a, r['matter_id'], x['document_id'], x['version_id'])
            if v['sha256'] != x['sha256']:
                raise Stop('version_modifiee_depuis_l_approbation')
        if r['canal'] == 'paquet':
            raw, jeton = _paquet(desk, dict(r), client)
            empreinte = hashlib.sha256(raw).hexdigest()
            remise = ''
            unique['download'] = {'base64': base64.b64encode(raw).decode('ascii'), 'mime': 'application/zip',
                                  'filename': 'partage-%s.zip' % r['id'][-8:]}
        else:
            remise, empreinte, jeton = _nextcloud(desk, a, dict(r), client, ocs), '', secrets.token_urlsafe(24)
        quand = maintenant()
        n = desk.db.execute("UPDATE partages_client56109 SET etat='confirme', secret_hash=?, paquet_sha256=?, remise_id=?, confirme_par=?, confirme_le=?, "
                            "version=version+1, maj_le=? WHERE id=? AND etat='prepare' AND version=?",
                            (hashlib.sha256(jeton.encode()).hexdigest(), empreinte, remise, a.compte, quand, quand, r['id'], r['version'])).rowcount
        if n != 1:
            raise Stop('version_perimee')
        desk.db.commit()
        desk.audit('partage56109_confirme', {'partage': r['id'], 'matter': r['matter_id'], 'canal': r['canal'], 'paquet_sha256': empreinte, 'par': a.compte})
        return {'id': r['id'], 'paquet_sha256': empreinte}

    out = executer(desk, 'partages_client_ops56109', a.compte, data.get('cle'), 'confirmer', {'pid': row['id'], 'v': str(data.get('version'))}, faire)
    resultat = dict(vue(desk, a, row['id']), rejoue=out['rejoue'], paquet_sha256=out.get('paquet_sha256', ''))
    if unique.get('download') and not out['rejoue']:
        resultat['download'] = unique['download']               # rendu une seule fois : le reçu rejoué ne redonne pas le jeton
        resultat['noreload'] = True
        resultat['message'] = 'Partage confirmé : le paquet est téléchargé. Transmettez-le par le canal convenu ; il ne sera pas redonné.'
    elif out['rejoue']:
        resultat['message'] = 'Partage déjà confirmé (demande rejouée) : le paquet n’est pas redonné.'
    return resultat


def _nextcloud(desk, a, row, client, ocs):
    """Adaptateur Nextcloud : ``remises5697.remettre`` (copie de remise en lecture seule, partage, constat). Empreinte de chaque copie
    comparée à la version approuvée ; un écart révoque aussitôt et échoue."""
    from . import remises5697 as rem
    fichiers = []
    for x in ressources(desk, row['id']):
        item = dict(desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (x['item_id'],)).fetchone())
        octets_version(desk, item, client, x['sha256'])        # version exacte encore présente dans le stockage, avant toute copie
        fichiers.append({'path': item['path'], 'version_id': '', 'nature': 'document', 'sha256': x['sha256']})
    nc = json.loads(row['nextcloud_destinataire'] or '{}')
    jour = datetime.fromisoformat(row['expire_le']).astimezone(tz(desk)).date() - timedelta(days=1)
    out = rem.remettre(desk, a, row['matter_id'], {'cle': 'p109-' + row['id'], 'canal': 'nextcloud', 'destinataire': nc, 'identite_verifiee': True,
                                                   'fichiers': [{k: v for k, v in f.items() if k != 'sha256'} for f in fichiers], 'expiration': jour.isoformat()},
                       client, ocs)
    if [f.get('sha256') for f in out.get('fichiers') or []] != [f['sha256'] for f in fichiers]:
        if out.get('share_id'):
            rem.revoquer(desk, a, out['id'], ocs)
        raise Stop('version_modifiee_depuis_l_approbation')
    if out.get('etat') not in ('partage_actif',):
        raise Stop('partage_nextcloud_indisponible')
    return out['id']


def revoquer(desk, acteur_, pid, data, ocs=None):
    """Ferme les accès futurs (paquet : plus aucun retour accepté ; Nextcloud : partage supprimé puis relu). L'historique, les réponses et
    les reçus restent. Une copie déjà téléchargée ne peut pas être retirée à distance (dit dans le message)."""
    a = acteur(acteur_)
    personne(desk, a)
    valideur(a)
    row = _partage(desk, a, pid)
    motif = texte(data.get('motif'), 300)

    def faire():
        r = desk.db.execute('SELECT * FROM partages_client56109 WHERE id=?', (row['id'],)).fetchone()
        if r['etat'] == 'revoque':
            return {'id': r['id']}
        _version(r, data.get('version'))
        quand = maintenant()
        desk.db.execute("UPDATE partages_client56109 SET etat='revoque', revoque_par=?, revoque_le=?, motif_revocation=?, version=version+1, maj_le=? WHERE id=?",
                        (a.compte, quand, motif, quand, r['id']))
        desk.db.commit()
        desk.audit('partage56109_revoque', {'partage': r['id'], 'matter': r['matter_id'], 'par': a.compte})
        if r['remise_id']:
            from . import remises5697 as rem
            try:
                rem.revoquer(desk, a, r['remise_id'], ocs)
            except Stop:
                pass                                            # état distant relu par ``remises5697`` : « révocation demandée » reste affiché
        return {'id': r['id']}

    out = executer(desk, 'partages_client_ops56109', a.compte, data.get('cle'), 'revoquer', {'pid': row['id']}, faire)
    return dict(vue(desk, a, row['id']), rejoue=out['rejoue'], message='Accès révoqué pour l’avenir. ' + LIMITE_COPIE)


def revoquer_interne(desk, a, pid, motif):
    """Révocation décidée par une transition d'un autre objet du lot (remplacement annulé) : même effet, sans clé de formulaire."""
    row = _partage(desk, a, pid)
    if row['etat'] == 'revoque':
        return
    quand = maintenant()
    desk.db.execute("UPDATE partages_client56109 SET etat='revoque', revoque_par=?, revoque_le=?, motif_revocation=?, version=version+1, maj_le=? WHERE id=?",
                    (a.compte, quand, texte(motif, 300), quand, row['id']))
    desk.db.commit()
    desk.audit('partage56109_revoque', {'partage': row['id'], 'matter': row['matter_id'], 'par': a.compte, 'motif': 'objet_lie'})
    if row['remise_id']:
        from . import remises5697 as rem
        try:
            rem.revoquer(desk, a, row['remise_id'])
        except Stop:
            pass


# ------------------------------------------------------------------------------------------------ accès du destinataire (relu à chaque lecture)
def echeance_effective(desk, row):
    """Échéance réelle d'un partage, lue sans écriture : celle enregistrée, bornée pour un partage lié à un remplacement par le lendemain
    de l'audience COURANTE (C2 : une audience avancée raccourcit l'accès du confrère, même avant tout recalcul enregistré)."""
    echeance = row['expire_le']
    if desk is not None and row['origine'] == 'remplacement' and row['objet_lie']:
        from . import remplacements56109
        borne = remplacements56109.echeance_acces(desk, row['objet_lie'])
        echeance = min(echeance, borne) if borne else maintenant()      # audience illisible : accès fermé, jamais prolongé
    return echeance


def etat_effectif(row, quand=None, desk=None):
    """État lu sans écriture (R05) : un partage confirmé dont l'échéance (effective si ``desk`` est donné) est passée se lit « expiré »."""
    if row['etat'] == 'confirme' and echeance_effective(desk, row) <= (quand or maintenant()):
        return 'expire'
    return row['etat']


def ajuster_echeance(desk, pid, echeance):
    """Échéance enregistrée recalculée par une écriture de l'objet lié (changement d'audience constaté lors d'une étape) ; sans commit."""
    desk.db.execute("UPDATE partages_client56109 SET expire_le=?, maj_le=? WHERE id=? AND etat IN ('prepare','confirme') AND expire_le<>?",
                    (echeance, maintenant(), str(pid), echeance))


def _jeton_valide(row, jeton):
    attendu = row['secret_hash'] or ''
    recu = hashlib.sha256(str(jeton or '').encode()).hexdigest()
    return bool(attendu) and hmac.compare_digest(attendu, recu)


def acceder(desk, pid, jeton, document_id, version_id, droit='lire', quand=None, client=None):
    """Porte unique de toute lecture par un destinataire : partage confirmé, non révoqué, non expiré, jeton de CE partage, droit accordé,
    version EXACTE du partage. Tout refus est le même ``introuvable`` (aucun indice sur l'existence d'un autre partage ou dossier)."""
    if droit not in ('lire', 'telecharger'):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM partages_client56109 WHERE id=?', (str(pid or ''),)).fetchone() if IDENT.fullmatch(str(pid or '')) else None
    if not row or etat_effectif(row, quand, desk) != 'confirme' or not _jeton_valide(row, jeton):
        raise Stop('introuvable')
    if not json.loads(row['permissions']).get(droit):
        raise Stop('introuvable')
    r = desk.db.execute('SELECT * FROM partages_client_ressources56109 WHERE partage_id=? AND document_id=? AND version_id=?',
                        (row['id'], str(document_id or ''), str(version_id or ''))).fetchone()
    if not r:
        raise Stop('introuvable')
    item = desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (r['item_id'],)).fetchone()
    if not item or item['matter_id'] != row['matter_id']:
        raise Stop('introuvable')
    raw, nom = octets_version(desk, dict(item), client, r['sha256'])
    desk.audit('partage56109_acces', {'partage': row['id'], 'document': r['document_id'], 'droit': droit})
    return raw, nom


# ------------------------------------------------------------------------------------------------ retour du client et quarantaine
def _mettre_en_quarantaine(desk, row, demande_id, nom, raw, provenance):
    propre, ext, type_reel = controler_fichier(nom, raw)
    empreinte = hashlib.sha256(raw).hexdigest()
    deja = desk.db.execute('SELECT id FROM partages_client_quarantaine56109 WHERE partage_id=? AND sha256=?', (row['id'], empreinte)).fetchone()
    if deja:
        return deja['id']
    qid = 'pq-' + uuid.uuid4().hex[:24]
    fichier = qid + '.bin'
    ecrire_exclusif(zone_quarantaine(desk) / fichier, raw)
    desk.db.execute('INSERT INTO partages_client_quarantaine56109(id,partage_id,matter_id,demande_id,nom_origine,nom_neutralise,extension,type_reel,taille,sha256,'
                    'fichier,etat,provenance,recu_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (qid, row['id'], row['matter_id'], demande_id, str(nom)[:300], propre, ext, type_reel, len(raw), empreinte, fichier, 'quarantaine',
                     canon(provenance), maintenant()))
    return qid


def _lire_paquet_retour(raw):
    """Archive de retour : entrées bornées (nombre, taille décompressée), noms sans chemin absolu ni remontée ; ``reponse.json`` fermé."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError):
        raise Stop('paquet_invalide') from None
    infos = z.infolist()
    if len(infos) > MAX_ENTREES or sum(i.file_size for i in infos) > MAX_DECOMPRESSE:
        raise Stop('paquet_invalide')
    fichiers, reponse = {}, None
    for i in infos:
        nom = i.filename.replace('\\', '/')
        if i.is_dir():
            continue
        if nom.startswith('/') or '..' in nom.split('/') or ':' in nom:
            raise Stop('paquet_invalide')
        data = z.read(i)
        if nom == 'reponse.json':
            reponse = data
        elif nom.startswith('fichiers/') and nom.count('/') == 1:
            fichiers[nom.split('/', 1)[1]] = data
    if reponse is None:
        raise Stop('paquet_invalide')
    try:
        rep = json.loads(reponse.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise Stop('paquet_invalide') from None
    if not isinstance(rep, dict) or set(rep) != {'format', 'partage_id', 'jeton_retour', 'reponses'} or rep['format'] != 'axh-reponse/1' \
            or not isinstance(rep['reponses'], list) or len(rep['reponses']) > MAX_DEMANDES:
        raise Stop('paquet_invalide')
    for x in rep['reponses']:
        if not isinstance(x, dict) or not set(x) <= {'demande_id', 'texte', 'validation', 'fichiers'} or 'demande_id' not in x:
            raise Stop('paquet_invalide')
        if x.get('validation') is not None and (not isinstance(x['validation'], dict) or set(x['validation']) != {'document_id', 'version_id', 'sha256', 'decision'}):
            raise Stop('paquet_invalide')
        if not isinstance(x.get('fichiers') or [], list) or not isinstance(x.get('texte') or '', str):
            raise Stop('paquet_invalide')
    return rep, fichiers


def importer_retour(desk, acteur_, pid, data):
    """Le cabinet importe le paquet de retour du client. Le jeton doit être celui de CE partage (haché), le partage confirmé, non révoqué
    et non expiré ; une validation doit porter EXACTEMENT sur la version demandée (document, version, empreinte), sinon rien n'est inscrit.
    Fichiers : quarantaine. Reçu : empreinte du retour ; le même paquet importé deux fois rend le même reçu."""
    a = acteur(acteur_)
    row = _partage(desk, a, pid)
    nom, raw = donnee_fichier(data.get('paquet'))
    if len(raw) > MAX_DECOMPRESSE:
        raise Stop('fichier_trop_volumineux')
    rep, fichiers = _lire_paquet_retour(raw)
    if rep['partage_id'] != row['id'] or not _jeton_valide(row, rep['jeton_retour']):
        raise Stop('paquet_autre_partage')
    etat = etat_effectif(row, desk=desk)
    if etat == 'revoque':
        raise Stop('partage_revoque')
    if etat == 'expire':
        raise Stop('partage_expire')
    if etat != 'confirme':
        raise Stop('partage_non_confirme')
    perm = json.loads(row['permissions'])
    dem = {d['id']: d for d in demandes(desk, row['id'])}
    lignes = []
    for x in rep['reponses']:
        d = dem.get(str(x['demande_id']))
        if not d:
            raise Stop('paquet_invalide')
        texte_ = texte(x.get('texte') or '', 5000, 'paquet_invalide')
        noms = [str(n) for n in (x.get('fichiers') or [])]
        if (texte_ or x.get('validation')) and not perm.get('repondre'):
            raise Stop('droit_absent')
        if noms and not perm.get('deposer'):
            raise Stop('droit_absent')
        val = x.get('validation')
        decision = ''
        if d['genre'] == 'validation':
            if val is None or not val.get('decision'):
                if texte_ or noms:
                    raise Stop('version_non_conforme')
                continue                                        # validation laissée sans réponse : rien n'est inscrit pour elle
            if (val['document_id'], val['version_id'], val['sha256']) != (d['document_id'], d['version_id'], d['sha256']) \
                    or val['decision'] not in DECISIONS_VALIDATION:
                raise Stop('version_non_conforme')
            decision = val['decision']
        elif val is not None:
            raise Stop('paquet_invalide')
        for n in noms:
            if n not in fichiers:
                raise Stop('paquet_invalide')
            controler_fichier(n, fichiers[n])                   # contrôle complet AVANT toute écriture
        if texte_ or decision or noms:
            lignes.append((d, texte_, decision, noms))
    recu = sha(canon(rep), *sorted(hashlib.sha256(v).hexdigest() for v in fichiers.values()))
    deja = desk.db.execute('SELECT 1 FROM partages_client_reponses56109 WHERE partage_id=? AND recu_sha256=? LIMIT 1', (row['id'], recu)).fetchone()
    if deja:
        return dict(vue(desk, a, row['id']), recu=recu, rejoue=True, message='Retour déjà importé (même paquet) : même reçu.')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        for d, texte_, decision, noms in lignes:
            qids = [_mettre_en_quarantaine(desk, row, d['id'], n, fichiers[n], {'source': 'paquet_retour', 'paquet': nom[:120], 'importe_par': a.compte})
                    for n in noms]
            desk.db.execute('INSERT INTO partages_client_reponses56109(id,partage_id,demande_id,genre,texte,decision,document_id,version_id,sha256,quarantaine,'
                            'recu_sha256,source,saisi_par,recu_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            ('pr-' + uuid.uuid4().hex[:24], row['id'], d['id'], d['genre'], texte_, decision, d['document_id'], d['version_id'], d['sha256'],
                             json.dumps(qids), recu, 'paquet_retour', a.compte, maintenant()))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('partage56109_retour', {'partage': row['id'], 'matter': row['matter_id'], 'reponses': len(lignes), 'recu': recu, 'par': a.compte})
    return dict(vue(desk, a, row['id']), recu=recu, rejoue=False,
                message='Retour importé : %d réponse(s) inscrite(s) ; les fichiers restent en quarantaine jusqu’à votre classement.' % len(lignes))


def recevoir_fichier(desk, acteur_, pid, data):
    """Fichier remis par un autre canal (dépôt Nextcloud, main propre) pour une demande de ce partage : contrôlé puis mis en quarantaine."""
    a = acteur(acteur_)
    row = _partage(desk, a, pid)
    if etat_effectif(row, desk=desk) != 'confirme':
        raise Stop('partage_inactif')                           # C1 : non confirmé, expiré ou révoqué : même réponse, rien n'entre en quarantaine
    did = str(data.get('demande') or '')
    if did and not desk.db.execute('SELECT 1 FROM partages_client_demandes56109 WHERE id=? AND partage_id=?', (did, row['id'])).fetchone():
        raise Stop('introuvable')
    nom, raw = donnee_fichier(data.get('fichier'))
    qid = _mettre_en_quarantaine(desk, row, did, nom, raw, {'source': texte(data.get('canal') or 'autre', 60), 'saisi_par': a.compte})
    desk.db.commit()
    desk.audit('partage56109_quarantaine', {'partage': row['id'], 'matter': row['matter_id'], 'fichier': qid, 'par': a.compte})
    return dict(vue(desk, a, row['id']), fichier=qid, message='Fichier contrôlé et placé en quarantaine : il n’est pas encore dans le dossier.')


def _quarantaine(desk, a, qid):
    if not IDENT.fullmatch(str(qid or '')):
        raise Stop('introuvable')
    q = desk.db.execute('SELECT * FROM partages_client_quarantaine56109 WHERE id=?', (str(qid),)).fetchone()
    if not q:
        raise Stop('introuvable')
    dossier(desk, a, q['matter_id'])
    return dict(q)


def classer(desk, acteur_, qid, data, client=None):
    """Classement décidé par un avocat : destination résolue côté serveur (``correspondances5643.destination_pieces`` : jamais un chemin
    reçu), octets relus dans la quarantaine et recomparés, dépôt sous un nom nouveau (aucun fichier remplacé), relecture et comparaison :
    reçu de classement. Le contenu n'est pas pour autant un fait validé du dossier."""
    from . import correspondances5643 as cx, pause5629
    a = acteur(acteur_)
    personne(desk, a)
    valideur(a)
    q = _quarantaine(desk, a, qid)
    m = dossier(desk, a, q['matter_id'])

    def faire():
        r = desk.db.execute('SELECT * FROM partages_client_quarantaine56109 WHERE id=?', (q['id'],)).fetchone()
        if r['etat'] != 'quarantaine':
            raise Stop('deja_decide')
        _version(r, data.get('version'))
        cible = cx.destination_pieces(desk, m, str(data.get('destination') or 'nos_pieces'))
        raw = (zone_quarantaine(desk) / r['fichier']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != r['sha256']:
            raise Stop('fichier_quarantaine_altere')
        from .office440 import dav_client
        cl = client or dav_client(desk)
        racine = str(m.get('path') or '').rstrip('/')
        cl.ensure_folder(cible['path'], racine)
        chemin = _nom_libre(cl, cible['path'], '%s %s' % (jour_cabinet(desk).isoformat(), r['nom_neutralise']))
        pause5629.checkpoint(desk)
        cl.put_file(chemin, raw)
        if hashlib.sha256(cl.download(cl.stat(chemin))).hexdigest() != r['sha256']:
            raise Stop('depot_fichier_non_verifie')
        quand = maintenant()
        desk.db.execute("UPDATE partages_client_quarantaine56109 SET etat='classe', decide_par=?, decide_le=?, destination=?, chemin_classe=?, version=version+1 "
                        "WHERE id=? AND etat='quarantaine'", (a.compte, quand, cible['id'], chemin, r['id']))
        desk.db.commit()
        desk.audit('partage56109_classe', {'fichier': r['id'], 'matter': r['matter_id'], 'destination': cible['id'], 'sha256': r['sha256'], 'par': a.compte})
        return {'id': r['id'], 'chemin': chemin, 'sha256': r['sha256']}

    out = executer(desk, 'partages_client_ops56109', a.compte, data.get('cle'), 'classer', {'q': q['id'], 'dest': str(data.get('destination') or '')}, faire)
    return {**out, 'message': 'Fichier classé dans le dossier (empreinte vérifiée après dépôt).'}


def _nom_libre(client, dossier_, nom):
    base, ext = os.path.splitext(nom)
    for n in range(50):
        candidat = '%s/%s%s%s' % (dossier_.rstrip('/'), base[:120], '' if not n else ' (%d)' % (n + 1), ext)
        try:
            client.stat(candidat)
        except Stop:
            return candidat
    raise Stop('nom_de_fichier_indisponible')


def refuser_fichier(desk, acteur_, qid, data):
    a = acteur(acteur_)
    valideur(a)
    q = _quarantaine(desk, a, qid)
    motif = texte(data.get('motif'), 300)
    if len(motif) < 3:
        raise Stop('motif_requis')
    if q['etat'] != 'quarantaine':
        raise Stop('deja_decide')
    _version(q, data.get('version'))
    desk.db.execute("UPDATE partages_client_quarantaine56109 SET etat='refuse', motif=?, decide_par=?, decide_le=?, version=version+1 WHERE id=? AND etat='quarantaine'",
                    (motif, a.compte, maintenant(), q['id']))
    desk.db.commit()
    desk.audit('partage56109_fichier_refuse', {'fichier': q['id'], 'matter': q['matter_id'], 'par': a.compte})
    return {'id': q['id'], 'message': 'Fichier refusé : il reste en quarantaine, hors du dossier.'}


# ------------------------------------------------------------------------------------------------ lectures (aucune écriture)
def vue(desk, acteur_, pid):
    a = acteur(acteur_)
    row = _partage(desk, a, pid)
    reps = [dict(r) for r in desk.db.execute('SELECT * FROM partages_client_reponses56109 WHERE partage_id=? ORDER BY recu_le', (row['id'],))]
    quar = [dict(r) for r in desk.db.execute('SELECT id,demande_id,nom_neutralise,type_reel,taille,sha256,etat,motif,recu_le,decide_par,destination,chemin_classe,version '
                                             'FROM partages_client_quarantaine56109 WHERE partage_id=? ORDER BY recu_le', (row['id'],))]
    etat = etat_effectif(row, desk=desk)
    out = {k: row[k] for k in ('id', 'matter_id', 'destinataire', 'destinataire_verifie_par', 'canal', 'expire_le', 'version', 'remise_id', 'paquet_sha256',
                               'origine', 'objet_lie', 'cree_par', 'cree_le', 'confirme_par', 'confirme_le', 'revoque_par', 'revoque_le', 'motif_revocation')}
    out.update(expire_le=echeance_effective(desk, row), etat=etat, etat_libelle=ETATS[etat], canal_libelle=CANAUX[row['canal']], permissions=json.loads(row['permissions']),
               ressources=ressources(desk, row['id']), demandes=demandes(desk, row['id']), reponses=reps, quarantaine=quar,
               avertissements=[LIMITE_COPIE, MENTION_REPONSE])
    for x in out['reponses']:
        x['quarantaine'] = json.loads(x['quarantaine'] or '[]')
        x['decision_libelle'] = DECISIONS_VALIDATION.get(x['decision'], '')
    return out


def partages_dossier(desk, acteur_, mid, limite=30):
    a = acteur(acteur_)
    m = dossier(desk, a, mid)
    return [vue(desk, a, r['id']) for r in desk.db.execute('SELECT id FROM partages_client56109 WHERE matter_id=? ORDER BY cree_le DESC LIMIT ?', (m['id'], limite))]


def partages_objet(desk, acteur_, origine, objet):
    """Partages préparés depuis un autre objet du lot (remplacement d'audience) : même registre, mêmes droits."""
    a = acteur(acteur_)
    out = []
    for r in desk.db.execute('SELECT id FROM partages_client56109 WHERE origine=? AND objet_lie=? ORDER BY cree_le DESC', (origine, str(objet))):
        try:
            out.append(vue(desk, a, r['id']))
        except Stop:
            continue
    return out


# ------------------------------------------------------------------------------------------------ bloc du dossier (onglet Correspondances)
def _cle():
    return uuid.uuid4().hex


def _form(route, champs, bouton, cls='ax-btn ghost', confirmer_='', extra='', reload_=True):
    caches = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in champs.items())
    return ('<form class="m5-form rl109-form" data-api="%s"%s%s>%s<input type="hidden" name="cle" value="%s">%s<button class="%s" type="submit">%s</button></form>') % (
        e(PREFIXE + route, quote=True), ' data-reload' if reload_ else '', (' data-confirm="%s"' % e(confirmer_, quote=True)) if confirmer_ else '', caches, _cle(),
        extra, cls, e(bouton))


def _court(empreinte):
    return (empreinte or '')[:12]


def _partage_html(p, peut):
    res = ''.join('<li>%s <span class="ax-muted">version %s · empreinte %s</span></li>' % (e(r['titre']), e(r['version_id'][:24]), e(_court(r['sha256'])))
                  for r in p['ressources']) or '<li class="ax-muted">Aucun document partagé.</li>'
    dem = ''.join('<li>%s : %s</li>' % (e(GENRES[d['genre']]), e(d['libelle'])) for d in p['demandes'])
    reps = ''.join('<li>%s%s%s <span class="ax-muted">reçu %s</span></li>' % (
        e(next((d['libelle'] for d in p['demandes'] if d['id'] == r['demande_id']), '')), (' — ' + e(r['decision_libelle'])) if r['decision'] else '',
        (' — « %s »' % e(r['texte'][:200])) if r['texte'] else '', e(_court(r['recu_sha256']))) for r in p['reponses'])
    quar = ''
    for q in p['quarantaine']:
        actions = ''
        if peut and q['etat'] == 'quarantaine':
            actions = (_form('quarantaine/%s/classer' % q['id'], {'version': q['version'], 'destination': 'nos_pieces'}, 'Classer dans « Nos pièces »')
                       + _form('quarantaine/%s/refuser' % q['id'], {'version': q['version']}, 'Refuser',
                               extra='<label>Motif <input name="motif" required maxlength="300"></label>'))
        quar += '<li>%s <span class="ax-muted">%s · %d octets · %s</span> — %s%s</li>' % (
            e(q['nom_neutralise']), e(q['type_reel']), q['taille'], e(_court(q['sha256'])), e(ETATS_QUARANTAINE[q['etat']]), actions)
    actions = ''
    if peut and p['etat'] == 'prepare':
        actions += _form('%s/confirmer' % p['id'], {'version': p['version']}, 'Confirmer et transmettre' if p['canal'] == 'nextcloud' else 'Confirmer et produire le paquet',
                         'ax-btn', 'Confirmer ce partage ? Les versions listées seront remises au destinataire.', reload_=False)
    if peut and p['etat'] in ('prepare', 'confirme'):
        actions += _form('%s/revoquer' % p['id'], {'version': p['version']}, 'Révoquer', confirmer_='Révoquer cet accès pour l’avenir ?')
    if peut and p['etat'] == 'confirme':
        actions += ('<form class="m5-form rl109-form" data-api="%s" data-reload><input type="hidden" name="cle" value="%s">'
                    '<label>Paquet de retour du client (ZIP) <input type="file" name="paquet" accept=".zip" data-envoi-fichier required></label>'
                    '<button class="ax-btn ghost" type="submit">Importer le retour</button></form>') % (e(PREFIXE + p['id'] + '/retour', quote=True), _cle())
    return ('<li class="rl109-carte"><p><strong>%s</strong> — %s · %s · expire le %s</p><p class="ax-muted">Destinataire vérifié : %s</p>'
            '<ul>%s</ul>%s%s%s%s</li>') % (
        e(p['destinataire']), e(p['etat_libelle']), e(p['canal_libelle']), e(p['expire_le'][:10]), e(p['destinataire_verifie_par']), res,
        ('<p>Demandes :</p><ul>%s</ul>' % dem) if dem else '', ('<p>Réponses :</p><ul>%s</ul>' % reps) if reps else '',
        ('<p>Fichiers reçus :</p><ul>%s</ul>' % quar) if quar else '', '<div class="rl109-actions">%s</div>' % actions if actions else '')


def bloc_dossier(desk, prefix, m, acteur_=None):
    """Bloc « Partager avec le client » de l'onglet Correspondances : lecture seule (aucune écriture, aucun modèle) ; nouvelle interface."""
    from .membres5650 import current_author, viewer_role
    try:
        a = acteur(acteur_ or (current_author() or 'cabinet', viewer_role()))
        from .interface56100 import active
        if not active(desk, a.compte):
            return ''
        liste = partages_dossier(desk, a, m['id'])
        choix = versions_partageables(desk, a, m['id']) if a.role in VALIDEURS else []
    except Stop as ex:
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Partage indisponible.'))
    peut = a.role in VALIDEURS
    options = ''.join('<option value="%s">%s — version %s (%s)</option>' % (e('%s|%s' % (v['document_id'], v['version_id']), quote=True), e(v['titre']),
                                                                         e(v['version_id'][:20]), e(_court(v['sha256']))) for v in choix)
    panneau = ''
    if peut:
        perms = ''.join('<label class="rl109-case"><input type="checkbox" name="permissions.%s"%s> %s</label>' % (k, ' checked' if k in ('lire', 'telecharger', 'repondre') else '', e(v))
                        for k, v in PERMISSIONS.items())
        panneau = ('<details class="rl109-panneau"><summary class="ax-btn">Partager avec le client</summary>'
                   '<form class="m5-form rl109-form" data-api="%s" data-reload><input type="hidden" name="cle" value="%s">'
                   '<label>Destinataire (adresse) <input type="email" name="destinataire" required maxlength="200"></label>'
                   '<label class="rl109-case"><input type="checkbox" name="identite_verifiee"> Identité vérifiée (destinataire hors des correspondants client du dossier)</label>'
                   '<label>Versions approuvées à partager <select name="ressources" multiple size="4">%s</select></label>'
                   '<label>Pièces demandées (une par ligne) <textarea name="pieces" maxlength="6000"></textarea></label>'
                   '<label>Questions (une par ligne) <textarea name="questions" maxlength="6000"></textarea></label>'
                   '<label>Faire valider ces versions <select name="validations" multiple size="3">%s</select></label>'
                   '<fieldset><legend>Droits</legend>%s</fieldset>'
                   '<label>Expire le <input type="date" name="expiration" required></label>'
                   '<label>Canal <select name="canal"><option value="paquet">%s</option><option value="nextcloud">%s</option></select></label>'
                   '<p class="ax-muted">Préparer n’envoie rien : l’aperçu exact s’affiche, puis vous confirmez.</p>'
                   '<button class="ax-btn" type="submit">Préparer l’aperçu</button></form></details>') % (
            e(PREFIXE + 'dossier/' + m['id'] + '/preparer', quote=True), _cle(), options, options, perms, e(CANAUX['paquet']), e(CANAUX['nextcloud']))
        if not choix:
            panneau += '<p class="ax-muted">Aucune version relue et validée dans ce dossier : seules des demandes au client peuvent être préparées.</p>'
    cartes = ''.join(_partage_html(p, peut) for p in liste)
    return ('<section class="rl109-section" aria-labelledby="rl109-pc-t"><h2 id="rl109-pc-t">Partager avec le client</h2>'
            '<p class="ax-muted">%s</p>%s<ul class="rl109-liste">%s</ul></section>') % (
        e(LIMITE_COPIE), panneau, cartes or '<li class="ax-muted">Aucun partage avec le client.</li>')


# ------------------------------------------------------------------------------------------------ routes ``m56109/partage/``
def route(api_, prefixe=PREFIXE):
    r = str(api_ or '').split('?', 1)[0]
    if not r.startswith(prefixe):
        return None
    return [p for p in r[len(prefixe):].split('/') if p]


def ecriture_connue(parts):
    return ((len(parts) == 3 and parts[0] == 'dossier' and parts[2] == 'preparer') or (len(parts) == 2 and parts[1] in ('confirmer', 'revoquer', 'retour', 'recevoir'))
            or (len(parts) == 3 and parts[0] == 'quarantaine' and parts[2] in ('classer', 'refuser')))


def allowed(role, method, api_):
    """Contrôle de rôle sans état (``standalone_auth``) : lectures pour les trois rôles ; préparer, confirmer, révoquer, classer, refuser :
    avocat et administrateur ; importer un retour, recevoir un fichier : trois rôles (quarantaine seulement). Le service relit tout."""
    parts = route(api_)
    if role not in ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[0] == 'dossier') or (len(parts) == 1 and IDENT.fullmatch(parts[0]) is not None)
    if method != 'POST' or not ecriture_connue(parts):
        return False
    if len(parts) == 2 and parts[1] in ('retour', 'recevoir'):
        return True
    return role in VALIDEURS


def sortie(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def erreur(ex, statuts=STATUTS, messages=MESSAGES):
    code = str(ex).split(':')[0]
    statut = statuts.get(code) or STATUTS.get(code) or ('404 Not Found' if code in ('audience_absente', 'evenement_absent') else '422 Unprocessable Entity')
    return sortie({'error': code, 'message': messages.get(code) or MESSAGES.get(code) or code.replace('_', ' ')}, statut)


def corps(env, auth, limite):
    """Corps JSON borné ; mêmes contrôles d'origine et de jeton CSRF que ``web440.check_post`` (borne propre aux fichiers de cette route)."""
    from .web440 import MAX_JSON, check_post
    if limite <= MAX_JSON:
        return check_post(env, auth)
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        raise Stop('requete_trop_longue') from None
    if longueur > limite or longueur < 0:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(longueur).decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    return data


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return erreur(ex)
    except sqlite3.OperationalError:
        return sortie({'error': 'registre_occupe', 'message': MESSAGES['registre_occupe']}, '503 Service Unavailable')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    a = actor(env)
    parts = route(name)
    if not parts:
        raise Stop('route_inconnue')
    if not allowed(a[1], method, name):
        if a[1] not in ROLES or (method == 'POST' and ecriture_connue(parts)):
            raise Stop('role_insuffisant')
        raise Stop('route_inconnue')
    if method in ('GET', 'HEAD'):
        if parts[0] == 'dossier':
            return sortie({'partages': partages_dossier(desk, a, parts[1])})
        return sortie(vue(desk, a, parts[0]))
    if len(parts) == 2 and parts[1] in ('retour', 'recevoir'):
        data = corps(env, auth, MAX_RETOUR)
        return sortie(importer_retour(desk, a, parts[0], data) if parts[1] == 'retour' else recevoir_fichier(desk, a, parts[0], data))
    from .web440 import check_post
    data = check_post(env, auth)
    if parts[0] == 'dossier':
        data = dict(data)
        data['permissions'] = {k: data.get('permissions.' + k) is True for k in PERMISSIONS} if any(('permissions.' + k) in data for k in PERMISSIONS) \
            else data.get('permissions')
        out = preparer(desk, a, parts[1], data, data.get('cle'))
        return sortie({**out, 'message': 'Aperçu prêt : vérifiez les versions, les demandes, les droits et l’expiration, puis confirmez. Rien n’est parti.'})
    if parts[0] == 'quarantaine':
        return sortie(classer(desk, a, parts[1], data) if parts[2] == 'classer' else refuser_fichier(desk, a, parts[1], data))
    if parts[1] == 'confirmer':
        return sortie(confirmer(desk, a, parts[0], data))
    return sortie(revoquer(desk, a, parts[0], data))
