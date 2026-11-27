"""5.6.111 (lot P ; L06, L07, L08 du plan ; audit § 8) : préparation SUPERVISÉE d'un envoi sur un portail procédural (e-Barreau, Portalis).

Aucun écran de portail n'est qualifié (``collecte_portail56109.ADAPTATEURS`` et ``ADAPTATEURS`` ci-dessous sont vides) : AxiorHub ne pilote
AUCUN portail et n'envoie rien. Il prépare, fait confirmer, garde la trace et l'accusé ; l'avocat transmet lui-même dans le portail.

- **Préparer** (dossier, ou « À relire » pour un brouillon de conclusions) : portail et profil (identifiant ET version), compte, dossier,
  juridiction, RG, destinataire, objet, TEXTE EXACT du message (conservé tel quel, avec son empreinte), pièces du REGISTRE du dossier
  (version d'un livrable ``livrables5689`` ou fichier inscrit au registre des documents ``modele5651`` ; octets relus côté serveur et
  empreinte calculée ici ; le navigateur ne fournit que des identifiants). Taille et nombre de pièces bornés par les limites constatées
  lors de la qualification d'une capacité d'envoi du profil ; sinon « non connues : à vérifier sur le portail » (aucune constante
  présentée comme la limite d'un portail). Tables créées AVANT toute lecture. Clé d'idempotence sous contrainte unique ``(acteur, cle)`` :
  même clé et même contenu → même envoi ; même clé et autre contenu → refus.
- **Confirmer** : garde commune ``confirmations56111`` (défi lié à l'acteur, à l'envoi, à l'empreinte du contenu préparé et à celle de
  l'effet ; expiration ; cinq essais ; consommation et changement d'état dans la même transaction ``BEGIN IMMEDIATE``). L'état courant est
  recalculé avant (profil, version et octets de chaque pièce) : un texte, une pièce (nouvelle version, fichier modifié) ou un profil changés
  depuis la préparation invalident la confirmation (envoi « à préparer de nouveau »).
- **Transmettre** : tant qu'aucun profil n'a les capacités « préparer » et « transmettre » qualifiées (avec un adaptateur d'envoi qualifié),
  RIEN n'est piloté : récapitulatif prêt à recopier (texte exact, pièces dans l'ordre, téléchargeables après relecture de leur empreinte) et
  ouverture du navigateur supervisé du poste sur l'adresse officielle du profil, sans aucune saisie. Point de montage d'un futur adaptateur
  (``AdaptateurEnvoi``) : préremplissage champ par champ à travers la barrière de ``navigateur56109``, main rendue à l'avocat qui clique
  lui-même ; le contrat n'a AUCUNE méthode d'envoi. Testé avec un faux adaptateur ; jamais actif en production sans qualification.
- **Accusé** : « Enregistrer l'accusé de dépôt » : fichier fourni par le portail conservé tel quel (PDF, ZIP ou autre, sans présomption de
  format), empreinte, dépôt EXCLUSIF relu dans le dossier (Procédure ou Correspondances, comme la collecte), reçu liant l'accusé à l'envoi
  (texte, pièces, versions, destinataire, juridiction, RG). Sans accusé : « à vérifier », JAMAIS de réémission automatique. Annulation
  possible avant l'accusé.

Aucun mouvement de fonds, aucune comptabilité CARPA, aucune signature, aucun identifiant, mot de passe ou certificat saisi par le logiciel.
Routes ``/api440/m56111/portail/<action>`` (lectures : envoi, envois, piece, profils ; écritures : preparer, confirmer, nouveau-code,
annuler, transmis, accuse, ouvrir, preremplir, constater, capacite) ; toute autre route : 404. Réponses ``no-store``.
"""
from datetime import datetime, timezone
import base64
import hashlib
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import secrets
import sqlite3
from urllib.parse import urlencode, urlsplit

from .common import Stop

PREFIXE = 'm56111/portail/'
CHEMIN = 'portail56111'               # parcours de la garde commune
MINUTES = 30                          # validité du code de confirmation
LECTURES = ('envoi', 'envois', 'piece', 'profils')
ECRITURES = {'preparer': ('administrateur', 'avocat', 'assistant'), 'confirmer': ('administrateur', 'avocat'),
             'nouveau-code': ('administrateur', 'avocat', 'assistant'), 'annuler': ('administrateur', 'avocat', 'assistant'),
             'transmis': ('administrateur', 'avocat'), 'accuse': ('administrateur', 'avocat'), 'ouvrir': ('administrateur', 'avocat'),
             'preremplir': ('administrateur', 'avocat'), 'constater': ('administrateur', 'avocat'), 'capacite': ('administrateur',)}
ROLES = ('administrateur', 'avocat', 'assistant')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,80}$')
ID_ENVOI = re.compile(r'^pe-[a-f0-9]{20}$')
MAX_PIECES = 50                       # borne d'AxiorHub sur une préparation (pas une limite de portail)
MAX_MESSAGE = 20000
MAX_POST = 60_000
MAX_ACCUSE = 8 * 1024 * 1024          # octets d'un accusé relus par AxiorHub (borne technique, pas une limite de portail)
MAX_POST_ACCUSE = 11_500_000          # corps JSON d'un accusé (base64)
FAMILLES_ACCUSE = {'procedure': 'Procédure', 'correspondances': 'Correspondances'}
ETATS = {'preparee': 'Préparé : code de confirmation à recopier',
         'confirmee': 'Confirmé : à transmettre par vous dans le portail',
         'preremplie': 'Prérempli dans le portail : vérifiez puis cliquez vous-même sur « Envoyer »',
         'a_verifier': 'Transmission à vérifier dans le portail (aucun accusé enregistré)',
         'deposee': 'Accusé de dépôt enregistré',
         'annulee': 'Annulé',
         'perimee': 'À préparer de nouveau : le texte, une pièce ou le profil a changé'}
ACTIFS = ('preparee', 'confirmee', 'preremplie', 'a_verifier')
FORMATS = {'pdf': 'PDF', 'zip': 'ZIP', 'xml': 'XML', 'autre': 'Autre format'}
ETATS_ACCUSE = {'verifie': 'Relu dans le dossier', 'a_verifier': 'Relecture à vérifier', 'depot_en_cours': 'Dépôt en cours'}
MOTIFS = {'accuse_non_enregistre': 'Accusé non enregistré.', 'accuse_a_verifier': 'Relecture de l’accusé à vérifier.',
          'profil_modifie': 'Le profil du portail a changé.', 'profil_supprime': 'Le profil du portail a été supprimé.',
          'message_modifie': 'Le texte a changé.', 'contenu_modifie': 'Le contenu a changé.',
          'bloquee_apres_cinq_codes_faux': 'Cinq codes faux.', 'retour_incertain': 'Retour du portail incertain.',
          'main_a_l_avocat': 'Vous aviez la main sur le navigateur.', 'compte_different_du_profil': 'Le compte affiché diffère du profil.',
          'authentification_humaine_requise': 'Connectez-vous d’abord au portail.', 'preremplissage_interrompu': 'Préremplissage interrompu.'}
ADAPTATEURS = {}                      # portail -> classe d'adaptateur d'envoi QUALIFIÉ ; aucun livré (aucun écran réel qualifié)
NAVIGATION = None                     # essais : fabrique (desk, acteur) -> collecte_portail56109.Navigation
OUVERTURE = None                      # essais : fonction (desk, acteur, url) ; production : navigateur supervisé du poste
SCHEMA = '''
CREATE TABLE IF NOT EXISTS portail_envois56111(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  dossier TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'dossier', profil_id TEXT NOT NULL, profil_version INTEGER NOT NULL, portail TEXT NOT NULL,
  compte TEXT NOT NULL DEFAULT '', juridiction TEXT NOT NULL DEFAULT '', rg TEXT NOT NULL DEFAULT '', destinataire TEXT NOT NULL, objet TEXT NOT NULL,
  message TEXT NOT NULL, message_sha256 TEXT NOT NULL, contenu_sha256 TEXT NOT NULL, limites_json TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', confirme_par TEXT NOT NULL DEFAULT '', confirme_le TEXT NOT NULL DEFAULT '',
  transmis_par TEXT NOT NULL DEFAULT '', transmis_le TEXT NOT NULL DEFAULT '', annule_par TEXT NOT NULL DEFAULT '', annule_le TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(acteur, cle));
CREATE INDEX IF NOT EXISTS portail_envois56111_dossier ON portail_envois56111(dossier, cree_le);
CREATE TABLE IF NOT EXISTS portail_envoi_pieces56111(envoi TEXT NOT NULL, ordre INTEGER NOT NULL, source TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '',
  version_id TEXT NOT NULL, chemin TEXT NOT NULL, nom TEXT NOT NULL, sha256 TEXT NOT NULL, taille INTEGER NOT NULL, PRIMARY KEY(envoi, ordre));
CREATE TABLE IF NOT EXISTS portail_accuses56111(id TEXT PRIMARY KEY, envoi TEXT NOT NULL, nom_source TEXT NOT NULL, format TEXT NOT NULL, sha256 TEXT NOT NULL,
  taille INTEGER NOT NULL, famille TEXT NOT NULL, chemin TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'avocat',
  recu_json TEXT NOT NULL DEFAULT '{}', depose_par TEXT NOT NULL, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(envoi, sha256));
'''
TABLES = ('portail_envois56111', 'portail_envoi_pieces56111', 'portail_accuses56111')

STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'action_reservee_a_une_personne': '403 Forbidden', 'confirmation_autre_utilisateur': '403 Forbidden', 'droit_envoyer_requis': '403 Forbidden',
           'cle_reutilisee': '409 Conflict', 'envoi_deja_confirme': '409 Conflict', 'envoi_modifie_depuis_preparation': '409 Conflict',
           'etat_incompatible': '409 Conflict', 'piece_modifiee_depuis_preparation': '409 Conflict', 'confirmation_bloquee': '409 Conflict',
           'code_portail_expire': '409 Conflict', 'version_perimee': '409 Conflict', 'une_seance_est_deja_en_cours': '409 Conflict',
           'transmission_non_pilotable': '409 Conflict', 'accuse_deja_enregistre': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'json_requis': '415 Unsupported Media Type',
           'controle_acces_indisponible': '503 Service Unavailable', 'poste_requis': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Dossier ou envoi introuvable, ou vous n’y avez pas accès.',
    'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'action_reservee_a_une_personne': 'Cette action est réservée à une personne : aucun automatisme ne peut la faire.',
    'droit_envoyer_requis': 'Votre compte n’a pas le droit d’envoyer : demandez-le à l’administrateur.',
    'cle_requise': 'Demande mal formée : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà servi pour un autre contenu : rechargez la page.',
    'profil_requis': 'Choisissez le profil de portail.',
    'profil_absent': 'Aucun profil de portail déclaré : l’administrateur le déclare dans « Relever mes messages procéduraux ».',
    'destinataire_requis': 'Indiquez le destinataire (juridiction, greffe ou confrère) tel qu’il figure dans le portail.',
    'objet_requis': 'Indiquez l’objet de l’envoi.',
    'message_requis': 'Écrivez le texte du message.',
    'message_trop_long': 'Le texte du message est trop long (vingt mille caractères au plus).',
    'piece_invalide': 'Une pièce choisie est mal désignée : rechargez la page.',
    'rang_en_double': 'Deux pièces ont le même rang : donnez un rang différent à chacune.',
    'trop_de_pieces': 'Trop de pièces pour une seule préparation (cinquante au plus).',
    'piece_hors_registre': 'Une pièce n’est pas inscrite au registre de ce dossier : actualisez les fichiers du dossier puis recommencez.',
    'version_hors_dossier': 'Une pièce appartient à un autre dossier.',
    'version_sans_empreinte': 'Une pièce n’a pas encore d’empreinte vérifiée : attendez la fin de son enregistrement.',
    'version_modifiee_depuis_l_approbation': 'Une pièce a été modifiée depuis son inscription au registre : choisissez sa version actuelle.',
    'piece_illisible': 'Une pièce n’a pas pu être relue dans le stockage du cabinet.',
    'piece_trop_volumineuse': 'Une pièce est trop volumineuse pour être relue par AxiorHub.',
    'pieces_au_dela_des_limites_du_portail': 'Les pièces dépassent les limites constatées pour ce portail : retirez ou découpez des pièces.',
    'envoi_deja_confirme': 'Cet envoi est déjà confirmé.',
    'envoi_modifie_depuis_preparation': 'Le texte, une pièce ou le profil a changé depuis la préparation : préparez l’envoi de nouveau.',
    'code_portail_incorrect': 'Code faux.',
    'code_portail_expire': 'Le code a expiré : demandez un nouveau code.',
    'confirmation_bloquee': 'Demande bloquée après cinq codes faux : préparez l’envoi de nouveau.',
    'confirmation_autre_utilisateur': 'Ce code a été demandé par une autre personne : préparez votre propre envoi.',
    'confirmation_deja_en_cours': 'Cette confirmation est déjà en cours : attendez un instant.',
    'etat_incompatible': 'Cette action n’est plus possible dans l’état actuel de l’envoi : rechargez la page.',
    'version_perimee': 'L’envoi a changé entre-temps : rechargez la page.',
    'piece_modifiee_depuis_preparation': 'Cette pièce a changé depuis la préparation : elle n’est pas servie. Préparez l’envoi de nouveau.',
    'adresse_portail_absente': 'Le profil n’a pas d’adresse officielle d’entrée : ouvrez le portail vous-même.',
    'adresse_hors_domaines': 'L’adresse du profil n’appartient pas aux domaines déclarés : l’administrateur doit corriger le profil.',
    'poste_requis': 'Le navigateur supervisé s’ouvre depuis l’application du poste : ouvrez le portail vous-même dans votre navigateur.',
    'une_seance_est_deja_en_cours': 'Une séance du navigateur supervisé est déjà en cours sur ce poste : terminez-la d’abord.',
    'transmission_non_pilotable': 'Aucun écran de ce portail n’est qualifié : AxiorHub ne pilote pas le portail. Transmettez vous-même avec le récapitulatif.',
    'fichier_requis': 'Choisissez le fichier de l’accusé fourni par le portail.',
    'fichier_invalide': 'Le fichier de l’accusé est illisible : choisissez-le de nouveau.',
    'accuse_vide': 'Le fichier de l’accusé est vide.',
    'accuse_trop_volumineux': 'Le fichier de l’accusé est trop volumineux pour AxiorHub (huit mégaoctets au plus).',
    'famille_invalide': 'Choisissez Procédure ou Correspondances.',
    'emplacement_dossier_absent': 'Le répertoire du dossier est inconnu : indiquez-le dans la fiche du dossier.',
    'destination_hors_dossier': 'Destination hors du répertoire du dossier : rien n’a été déposé.',
    'capacite_inconnue': 'Capacité inconnue.', 'etat_capacite_inconnu': 'État inconnu.',
    'preuve_essai_requise': 'Décrivez la preuve de l’essai réel sur ce poste.',
    'limite_invalide': 'Une limite doit être un nombre entier positif.',
    'requete_trop_longue': 'Demande trop volumineuse.', 'json_requis': 'Demande mal formée : rechargez la page.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été fait.',
    'route_inconnue': 'Route inconnue.',
}


class AdaptateurEnvoi:
    """Contrat d'un écran de DÉPÔT qualifié (aucune implémentation livrée). Chaque méthode reçoit un ``Pilote`` de ``navigateur56109`` : toute
    observation et toute action passent par la barrière (main à l'avocat, pause, génération). Transitions admises seulement : lire l'identité
    affichée, ouvrir le formulaire de dépôt, renseigner un champ, joindre une pièce, constater ce que le portail affiche après le clic de
    l'avocat. AUCUNE méthode d'envoi, de signature, de paiement ou de suppression : le clic final reste à l'avocat."""
    version = '0'

    def identite(self, pilote):                       # {'compte', 'avocat', 'delegation'} affichés, ou None (pas de session)
        raise Stop('adaptateur_non_qualifie')

    def ouvrir_formulaire(self, pilote, recap):
        raise Stop('adaptateur_non_qualifie')

    def remplir(self, pilote, champ, valeur):        # champ : destinataire, juridiction, rg, objet, message
        raise Stop('adaptateur_non_qualifie')

    def joindre(self, pilote, ordre, nom, octets):
        raise Stop('adaptateur_non_qualifie')

    def constater(self, pilote):                      # {'statut': 'accuse', 'nom', 'octets'} | {'statut': 'incertain'} (jamais un renvoi)
        raise Stop('adaptateur_non_qualifie')


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode('utf-8')).hexdigest()


def _canon(valeur):
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ensure_schema(desk):
    """Tables créées AVANT toute lecture (premier usage sur une base neuve) ; aucune écriture si elles existent déjà."""
    noms = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)", TABLES)}
    if len(noms) < len(TABLES):
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _acteur(a):
    from .acces5697 import acteur_de
    return acteur_de(a)


def _personne(desk, a):
    from .partage_client56109 import personne
    personne(desk, a)


def _droits(desk, a):
    from .partage_client56109 import masques_et_droits
    return masques_et_droits(desk, a)


def _exiger_dossier(desk, a, mid):
    from . import acces5697
    try:
        acces5697.exiger(desk, a, dossier_id=str(mid or ''))
    except Stop as ex:
        if str(ex) in ('introuvable', 'acteur_requis'):
            raise Stop('introuvable') from None
        raise Stop('controle_acces_indisponible') from None
    if not _dossier_connu(desk, mid):
        raise Stop('introuvable')


def _dossier_connu(desk, mid):
    from .common import load_matters
    mid = str(mid or '')
    if not mid:
        return False
    try:
        if any(str(m.get('id')) == mid for m in load_matters(desk.c)):
            return True
    except Stop:
        pass
    try:
        return bool(desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?', (mid,)).fetchone())
    except sqlite3.Error:
        return False


def _texte(valeur, maximum, multiligne=False):
    s = str(valeur or '')
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s) if multiligne else re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', s))
    s = s.replace('\r\n', '\n').replace('\r', '\n') if multiligne else s
    return s.strip()[:maximum] if not multiligne else s.strip('\n')


def _envoi(desk, ident):
    ensure_schema(desk)
    if not ID_ENVOI.fullmatch(str(ident or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM portail_envois56111 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _envoi_visible(desk, a, ident):
    row = _envoi(desk, ident)
    _exiger_dossier(desk, a, row['dossier'])
    return row


def _pieces(desk, envoi_id):
    return [dict(r) for r in desk.db.execute('SELECT * FROM portail_envoi_pieces56111 WHERE envoi=? ORDER BY ordre', (envoi_id,))]


def _limites(prof_caps):
    """Limites du portail constatées pendant la qualification d'une capacité d'envoi (« transmettre » d'abord, puis « préparer ») ; sinon {}."""
    for cap in ('transmettre', 'preparer'):
        c = prof_caps.get(cap) or {}
        if c.get('etat') == 'qualifie' and c.get('limites'):
            return dict(c['limites'])
    return {}


def pilotable(prof_row):
    """Le portail peut-il être prérempli par AxiorHub ? Capacités « préparer » ET « transmettre » qualifiées pour la version courante du
    profil, et adaptateur d'envoi qualifié pour ce portail. Aucun n'est livré : toujours faux en production."""
    from . import collecte_portail56109 as C
    caps = C.capacites(prof_row)
    return (prof_row['portail'] in ADAPTATEURS and caps['preparer']['etat'] == 'qualifie' and caps['transmettre']['etat'] == 'qualifie')


# ------------------------------------------------------------------------------------------------ pièces du registre
def _ref_piece(ref):
    """« l|<document>|<version> » (version d'un livrable) ou « f|<chemin> » (fichier inscrit au registre du dossier) ; rien d'autre."""
    s = str(ref or '')
    if s.startswith('l|'):
        parts = s.split('|', 2)
        if len(parts) == 3 and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', parts[1]) and 0 < len(parts[2]) <= 200:
            return ('livrable', parts[1], parts[2])
    elif s.startswith('f|') and 1 < len(s) <= 1002 and '\x00' not in s:
        return ('fichier', '', s[2:])
    raise Stop('piece_invalide')


def _racine(desk, mid):
    from .collecte_portail56109 import _chemin_dossier
    return _chemin_dossier(desk, mid)


def _client(desk):
    from .office440 import dav_client
    return dav_client(desk)


def _resoudre_piece(desk, a, mid, source, document_id, ref, client=None):
    """Pièce désignée par son identifiant → version au registre et octets RELUS dans le stockage ; empreinte calculée ici."""
    from .common import clean_path, under
    if source == 'livrable':
        from .partage_client56109 import octets_version, version_registre
        v = version_registre(desk, a, mid, document_id, ref, approuvee=False)
        try:
            raw, nom = octets_version(desk, v['row'], client=client)
        except Stop as ex:
            if str(ex).split(':')[0] in ('version_modifiee_depuis_l_approbation', 'introuvable'):
                raise Stop('version_modifiee_depuis_l_approbation') from None
            raise
        return {'source': 'livrable', 'document_id': v['document_id'], 'version_id': v['version_id'], 'chemin': str(v['row'].get('path') or ''),
                'nom': nom, 'sha256': _sha(raw), 'taille': len(raw), 'octets': raw, 'titre': v['titre']}
    chemin = clean_path(ref)
    racine = _racine(desk, mid)
    if not under(chemin, racine):
        raise Stop('piece_hors_registre')
    try:
        inscrit = desk.db.execute('SELECT path FROM d51_documents WHERE matter=? AND path=?', (mid, chemin)).fetchone()
    except sqlite3.Error:
        inscrit = None
    if not inscrit:
        raise Stop('piece_hors_registre')
    client = client or _client(desk)
    try:
        item = client.stat(chemin)
        raw = client.download(item)
    except Stop as ex:
        if str(ex).split(':')[0] == 'piece_trop_volumineuse':
            raise
        raise Stop('piece_illisible') from None
    from .partage_client56109 import nom_sur
    return {'source': 'fichier', 'document_id': '', 'version_id': str(item.get('etag') or '') or 'sha:' + _sha(raw)[:16], 'chemin': chemin,
            'nom': nom_sur(PurePosixPath(chemin).name), 'sha256': _sha(raw), 'taille': len(raw), 'octets': raw, 'titre': PurePosixPath(chemin).stem}


def _etat_piece(desk, a, mid, p, client=None):
    """Relit une pièce préparée : (octets, motif) ; motif non vide si la version au registre ou les octets ont changé."""
    try:
        if p['source'] == 'livrable':
            from .livrables5689 import charger
            hidden, droits = _droits(desk, a)
            courant = charger(desk, p['document_id'], hidden, droits=droits)
            if str(courant['version_id']) != p['version_id']:
                return None, 'nouvelle_version'
            r = _resoudre_piece(desk, a, mid, 'livrable', p['document_id'], p['version_id'], client)
        else:
            r = _resoudre_piece(desk, a, mid, 'fichier', '', p['chemin'], client)
            if r['version_id'] != p['version_id'] and r['sha256'] != p['sha256']:
                return None, 'fichier_modifie'
    except Stop as ex:
        return None, str(ex).split(':')[0]
    if r['sha256'] != p['sha256']:
        return None, 'octets_modifies'
    return r['octets'], ''


def _contenu(envoi, pieces):
    """Empreinte du contenu préparé (version liée au défi) : profil et sa version, adresse, texte exact, pièces (version et empreinte)."""
    return _sha(_canon({'dossier': envoi['dossier'], 'profil': envoi['profil_id'], 'profil_version': int(envoi['profil_version']),
                        'portail': envoi['portail'], 'compte': envoi['compte'], 'juridiction': envoi['juridiction'], 'rg': envoi['rg'],
                        'destinataire': envoi['destinataire'], 'objet': envoi['objet'], 'message_sha256': _sha(envoi['message']),
                        'pieces': [[p['ordre'], p['source'], p['document_id'], p['version_id'], p['chemin'], p['sha256'], int(p['taille'])] for p in pieces]}))


def _effet(envoi_id, contenu):
    return _sha(_canon(['portail56111', 'transmission_par_l_avocat', envoi_id, contenu]))


def _contenu_courant(desk, a, row, client=None):
    """Contenu recalculé sur l'état ACTUEL (profil, versions et octets des pièces) ; rend (empreinte, motif). Lectures du stockage hors de
    toute transaction."""
    from . import collecte_portail56109 as C
    pieces = _pieces(desk, row['id'])
    try:
        prof = C._profil(desk, row['profil_id'])
    except Stop:
        return 'profil_supprime', 'profil_supprime'
    if prof['version'] != row['profil_version']:
        return 'profil_modifie:%d' % prof['version'], 'profil_modifie'
    if row['message_sha256'] != _sha(row['message']):
        return 'message_modifie', 'message_modifie'
    client = client or (_client(desk) if pieces else None)
    for p in pieces:
        _, motif = _etat_piece(desk, a, row['dossier'], p, client)
        if motif:
            return 'piece_%d_%s' % (p['ordre'], motif), 'piece_%d_%s' % (p['ordre'], motif)
    return _contenu(dict(row), pieces), ''


# ------------------------------------------------------------------------------------------------ préparer
def _pieces_demandees(data):
    """Pièces choisies : liste ``pieces`` [{ref, rang}] ou champs du formulaire ``r_<n>`` (identifiant) et ``p_<n>`` (rang, vide = non jointe)."""
    brut = data.get('pieces')
    choix = []
    if isinstance(brut, list):
        for i, x in enumerate(brut, 1):
            if isinstance(x, dict):
                choix.append((str(x.get('rang') or i), x.get('ref')))
            else:
                choix.append((str(i), x))
    else:
        for k, v in data.items():
            m = re.fullmatch(r'r_(\d{1,4})', str(k))
            if m and str(data.get('p_' + m.group(1)) or '').strip():
                choix.append((str(data.get('p_' + m.group(1))).strip(), v))
    if len(choix) > MAX_PIECES:
        raise Stop('trop_de_pieces')
    out, rangs = [], set()
    for rang, ref in choix:
        if not re.fullmatch(r'\d{1,3}', rang) or int(rang) < 1:
            raise Stop('piece_invalide')
        if int(rang) in rangs:
            raise Stop('rang_en_double')
        rangs.add(int(rang))
        out.append((int(rang), _ref_piece(ref)))
    return [r for _, r in sorted(out, key=lambda x: x[0])]


def preparer(desk, acteur, data):
    """Prépare un envoi (aucun effet hors d'AxiorHub) et émet le code de confirmation (affiché une fois, jamais conservé en clair)."""
    from . import collecte_portail56109 as C, confirmations56111 as garde
    ensure_schema(desk)
    a = _acteur(acteur)
    if a.role not in ROLES:
        raise Stop('role_insuffisant')
    _personne(desk, a)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    mid = str(data.get('dossier') or data.get('d') or '')
    _exiger_dossier(desk, a, mid)
    if not str(data.get('profil') or ''):
        raise Stop('profil_requis')
    try:
        prof = C._profil(desk, data.get('profil'))
    except Stop:
        raise Stop('profil_absent') from None
    destinataire, objet = _texte(data.get('destinataire'), 200), _texte(data.get('objet'), 300)
    message = _texte(data.get('message'), MAX_MESSAGE + 1, multiligne=True)
    if not destinataire:
        raise Stop('destinataire_requis')
    if not objet:
        raise Stop('objet_requis')
    if not message.strip():
        raise Stop('message_requis')
    if len(message) > MAX_MESSAGE:
        raise Stop('message_trop_long')
    juridiction, rg = _texte(data.get('juridiction'), 200), _texte(data.get('rg'), 60)
    origine = 'a_relire' if str(data.get('origine') or '') == 'a_relire' else 'dossier'
    demandees = _pieces_demandees(data)
    client = _client(desk) if demandees else None
    pieces = []
    for ordre, (source, doc, ref) in enumerate(demandees, 1):
        r = _resoudre_piece(desk, a, mid, source, doc, ref, client)
        if any(p['source'] == r['source'] and p['chemin'] == r['chemin'] and p['version_id'] == r['version_id'] for p in pieces):
            raise Stop('rang_en_double')
        pieces.append({'ordre': ordre, **{k: r[k] for k in ('source', 'document_id', 'version_id', 'chemin', 'nom', 'sha256', 'taille')}})
    limites = _limites(C.capacites(prof))
    if limites and (('nombre_max' in limites and len(pieces) > limites['nombre_max']) or
                    ('taille_piece_max' in limites and any(p['taille'] > limites['taille_piece_max'] for p in pieces)) or
                    ('taille_totale_max' in limites and sum(p['taille'] for p in pieces) > limites['taille_totale_max'])):
        raise Stop('pieces_au_dela_des_limites_du_portail')
    base = {'dossier': mid, 'profil_id': prof['id'], 'profil_version': prof['version'], 'portail': prof['portail'], 'compte': prof['compte'],
            'juridiction': juridiction, 'rg': rg, 'destinataire': destinataire, 'objet': objet, 'message': message}
    contenu = _contenu(base, pieces)
    empreinte = _sha(_canon([contenu, origine]))
    garde.ensure_schema(desk)                       # schéma de la garde créé AVANT la transaction (aucun DDL sous le verrou)
    db = desk.db
    if db.in_transaction:
        db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT * FROM portail_envois56111 WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
        if row:
            db.rollback()
            if row['empreinte'] != empreinte:
                raise Stop('cle_reutilisee')
            return {**vue(desk, a, row['id']), 'rejoue': True, 'code': None,
                    'message': 'Envoi déjà préparé : demandez un nouveau code si vous n’avez pas noté le premier.'}
        ident = 'pe-' + secrets.token_hex(10)
        quand = _maintenant()
        db.execute('INSERT INTO portail_envois56111(id,acteur,cle,empreinte,dossier,origine,profil_id,profil_version,portail,compte,juridiction,rg,destinataire,'
                   'objet,message,message_sha256,contenu_sha256,limites_json,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, a.compte, cle, empreinte, mid, origine, prof['id'], prof['version'], prof['portail'], prof['compte'], juridiction, rg,
                    destinataire, objet, message, _sha(message), contenu, _canon(limites), 'preparee', quand, quand))
        for p in pieces:
            db.execute('INSERT INTO portail_envoi_pieces56111(envoi,ordre,source,document_id,version_id,chemin,nom,sha256,taille) VALUES(?,?,?,?,?,?,?,?,?)',
                       (ident, p['ordre'], p['source'], p['document_id'], p['version_id'], p['chemin'], p['nom'], p['sha256'], p['taille']))
        defi = garde.emettre(desk, CHEMIN, ident, contenu, _effet(ident, contenu), MINUTES, acteur=a.compte)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        row = db.execute('SELECT * FROM portail_envois56111 WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()   # même clé, autre connexion
        if not row or row['empreinte'] != empreinte:
            raise Stop('cle_reutilisee') from None
        return {**vue(desk, a, row['id']), 'rejoue': True, 'code': None,
                'message': 'Envoi déjà préparé : demandez un nouveau code si vous n’avez pas noté le premier.'}
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    desk.audit('portail56111_prepare', {'envoi': ident, 'dossier': mid, 'profil': prof['id'], 'version': prof['version'], 'pieces': len(pieces)})
    return {**vue(desk, a, ident), 'rejoue': False, 'code': defi['code'], 'expire': defi['expire'],
            'message': 'Envoi préparé. Recopiez le code de confirmation pour le confirmer.'}


def nouveau_code(desk, acteur, ident):
    """Nouveau défi pour un envoi encore « préparé » (code oublié ou expiré) ; refusé après blocage ou si le contenu a changé."""
    from . import confirmations56111 as garde
    a = _acteur(acteur)
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] != 'preparee':
        raise Stop('etat_incompatible')
    if row['acteur'] != a.compte:
        raise Stop('confirmation_autre_utilisateur')
    dernier = garde.etat(desk, CHEMIN, row['id'])
    if dernier and dernier['etat'] == 'bloque':
        _perimer(desk, row, 'bloquee_apres_cinq_codes_faux')
        raise Stop('confirmation_bloquee')
    contenu, motif = _contenu_courant(desk, a, row)
    if motif:
        _perimer(desk, row, motif)
        raise Stop('envoi_modifie_depuis_preparation')
    defi = garde.emettre(desk, CHEMIN, row['id'], contenu, _effet(row['id'], contenu), MINUTES, acteur=a.compte)
    desk.db.commit()
    return {**vue(desk, a, row['id']), 'code': defi['code'], 'expire': defi['expire'], 'message': 'Nouveau code émis : l’ancien ne vaut plus.'}


def _perimer(desk, row, motif):
    from . import confirmations56111 as garde
    desk.db.execute("UPDATE portail_envois56111 SET etat='perimee', motif=?, version=version+1, maj_le=? WHERE id=? AND etat IN ('preparee','confirmee','preremplie')",
                    (str(motif)[:120], _maintenant(), row['id']))
    garde.annuler(desk, CHEMIN, row['id'])
    desk.db.commit()
    desk.audit('portail56111_perime', {'envoi': row['id'], 'motif': str(motif)[:120]})


# ------------------------------------------------------------------------------------------------ confirmer
def confirmer(desk, acteur, ident, code):
    """Code recopié par l'avocat → envoi « confirmé » (consommation du défi et changement d'état dans la même transaction ; rien n'est
    envoyé, ni pendant le verrou ni après). Contenu recalculé AVANT : un changement depuis la préparation invalide la confirmation."""
    from . import confirmations56111 as garde
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    _, droits = _droits(desk, a)
    if droits and not droits.get('envoyer', True):
        raise Stop('droit_envoyer_requis')
    row = _envoi_visible(desk, a, ident)
    if row['etat'] == 'perimee':
        raise Stop('envoi_modifie_depuis_preparation')
    if row['etat'] != 'preparee':
        raise Stop('envoi_deja_confirme' if row['etat'] in ('confirmee', 'preremplie', 'a_verifier', 'deposee') else 'etat_incompatible')
    contenu, motif = _contenu_courant(desk, a, row)
    quand = _maintenant()

    def intention(db):
        cur = db.execute("UPDATE portail_envois56111 SET etat='confirmee', motif='', confirme_par=?, confirme_le=?, version=version+1, maj_le=? "
                         "WHERE id=? AND etat='preparee'", (a.compte, quand, quand, row['id']))
        if not cur.rowcount:
            raise Stop('envoi_deja_confirme')
        return row['id']
    try:
        garde.confirmer(desk, CHEMIN, row['id'], code, version=contenu, effet=_effet(row['id'], contenu), code_faux='code_portail_incorrect',
                        code_expire='code_portail_expire', code_deja='envoi_deja_confirme', code_modifie='envoi_modifie_depuis_preparation',
                        intention=intention, matiere=row['dossier'])
    except Stop as ex:
        c = str(ex).split(':')[0]
        if c == 'envoi_modifie_depuis_preparation':
            _perimer(desk, _envoi(desk, row['id']), motif or 'contenu_modifie')
        elif c == 'confirmation_bloquee':
            _perimer(desk, _envoi(desk, row['id']), 'bloquee_apres_cinq_codes_faux')
        elif c in ('code_portail_incorrect',):
            desk.audit('portail56111_code_refuse', {'envoi': row['id']})
        raise
    desk.audit('portail56111_confirme', {'envoi': row['id'], 'par': a.compte})
    return {**vue(desk, a, row['id']), 'message': 'Envoi confirmé. Transmettez-le vous-même dans le portail avec le récapitulatif, puis enregistrez l’accusé.'}


# ------------------------------------------------------------------------------------------------ transmettre (par l'avocat)
def declarer_transmis(desk, acteur, ident):
    """« J'ai transmis dans le portail » : l'envoi passe « à vérifier » tant qu'aucun accusé n'est enregistré (jamais de renvoi)."""
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] not in ('confirmee', 'preremplie'):
        raise Stop('etat_incompatible')
    quand = _maintenant()
    cur = desk.db.execute("UPDATE portail_envois56111 SET etat='a_verifier', motif='accuse_non_enregistre', transmis_par=?, transmis_le=?, version=version+1, "
                          "maj_le=? WHERE id=? AND version=?", (a.compte, quand, quand, row['id'], row['version']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    desk.audit('portail56111_transmis_declare', {'envoi': row['id'], 'par': a.compte})
    return {**vue(desk, a, row['id']), 'message': 'Transmission notée : enregistrez l’accusé de dépôt dès que le portail le fournit.'}


def annuler(desk, acteur, ident):
    """Annulation avant tout accusé enregistré. Après une transmission déclarée, rien n'est retiré du portail : l'écran le dit."""
    from . import confirmations56111 as garde
    a = _acteur(acteur)
    if a.role not in ROLES:
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] not in ACTIFS:
        raise Stop('etat_incompatible')
    if desk.db.execute("SELECT 1 FROM portail_accuses56111 WHERE envoi=? AND etat='verifie'", (row['id'],)).fetchone():
        raise Stop('accuse_deja_enregistre')
    quand = _maintenant()
    cur = desk.db.execute("UPDATE portail_envois56111 SET etat='annulee', annule_par=?, annule_le=?, version=version+1, maj_le=? WHERE id=? AND version=?",
                          (a.compte, quand, quand, row['id'], row['version']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_perimee')
    garde.annuler(desk, CHEMIN, row['id'])
    desk.db.commit()
    desk.audit('portail56111_annule', {'envoi': row['id'], 'par': a.compte, 'etat_avant': row['etat']})
    msg = 'Envoi annulé.'
    if row['etat'] == 'a_verifier':
        msg += ' Rien n’est retiré du portail : vérifiez-y l’état de votre transmission.'
    return {**vue(desk, a, row['id']), 'message': msg}


def _url_autorisee(prof):
    url = str(prof['url_entree'] or '')
    if not url:
        raise Stop('adresse_portail_absente')
    hote = (urlsplit(url).hostname or '').lower()
    domaines = json.loads(prof['domaines_json'] or '[]')
    if not hote or hote not in domaines:
        raise Stop('adresse_hors_domaines')
    return url


def _ouvrir_produit(desk, acteur, url):
    """Navigateur supervisé du poste : séance de l'avocat, une navigation vers l'adresse officielle du profil, AUCUNE saisie ; la séance passe
    aussitôt à un état final (le navigateur reste ouvert, l'avocat s'y connecte et y travaille lui-même)."""
    from . import navigateur5673 as nv
    mgr = nv.manager(desk.c)
    with mgr.lock:
        if mgr.session is not None and mgr.session.state not in nv.FINALS:
            raise Stop('une_seance_est_deja_en_cours')
        s = nv.Session(mgr, acteur, 'Ouverture du portail pour un envoi fait par l’avocat (aucune saisie par AxiorHub)', '', '')
        s.state = 'main_a_l_avocat'
        mgr.session = s
    try:
        nv.Tab(mgr._browser().cdp).navigate(url)
    finally:
        s.state = 'termine'
        mgr.persist(s)


def ouvrir_portail(desk, acteur, ident, auth=None):
    """Ouvre le navigateur supervisé du poste sur l'adresse officielle du profil (domaines déclarés), sans saisie ; serveur : refus explicite."""
    from . import collecte_portail56109 as C
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] not in ('confirmee', 'preremplie', 'a_verifier'):
        raise Stop('etat_incompatible')
    url = _url_autorisee(C._profil(desk, row['profil_id']))
    if OUVERTURE is not None:
        OUVERTURE(desk, a.compte, url)
    else:
        from . import navigateur5673 as nv
        if not nv.available(auth):
            raise Stop('poste_requis')
        _ouvrir_produit(desk, a.compte, url)
    desk.audit('portail56111_portail_ouvert', {'envoi': row['id'], 'par': a.compte})
    return {'ok': True, 'message': 'Portail ouvert dans le navigateur du poste : connectez-vous vous-même, joignez les pièces dans l’ordre et envoyez.'}


# ------------------------------------------------------------------------------------------------ point de montage d'un adaptateur qualifié (inactif)
def _navigation(desk, acteur):
    if NAVIGATION is not None:
        return NAVIGATION(desk, acteur)
    from . import collecte_portail56109 as C, navigateur5673 as nv
    if not nv.available():
        raise Stop('poste_requis')
    mgr = nv.manager(desk.c)
    with mgr.lock:
        if mgr.session is not None and mgr.session.state not in nv.FINALS:
            raise Stop('une_seance_est_deja_en_cours')
        s = nv.Session(mgr, acteur, 'Préremplissage d’un dépôt sur le portail (le clic final reste à l’avocat)', '', '')
        s.state = 'en_cours'
        mgr.session = s
    browser = mgr._browser()
    return C.Navigation(s, nv.Tab(browser.cdp), browser)


def _profil_pilotable(desk, row):
    from . import collecte_portail56109 as C
    prof = C._profil(desk, row['profil_id'])
    if prof['version'] != row['profil_version']:
        raise Stop('envoi_modifie_depuis_preparation')
    if not pilotable(prof):
        raise Stop('transmission_non_pilotable')
    return prof


def preremplir(desk, acteur, ident):
    """Préremplissage par un adaptateur QUALIFIÉ (aucun livré) : chaque observation et chaque action passent par la barrière ; à la fin, la
    main est rendue à l'avocat (acquittée), qui vérifie et clique lui-même. Barrière refusée ou incident : l'envoi reste « confirmé »."""
    from . import navigateur56109 as N
    from .collecte_portail56109 import controle, pilote
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] != 'confirmee':
        raise Stop('etat_incompatible')
    prof = _profil_pilotable(desk, row)
    pieces = _pieces(desk, row['id'])
    client = _client(desk) if pieces else None
    octets = []
    for p in pieces:
        raw, motif = _etat_piece(desk, a, row['dossier'], p, client)
        if motif:
            _perimer(desk, row, 'piece_%d_%s' % (p['ordre'], motif))
            raise Stop('envoi_modifie_depuis_preparation')
        octets.append(raw)
    adaptateur = ADAPTATEURS[prof['portail']]()
    nav = _navigation(desk, a.compte)
    epoch = nav.seance.epoch
    motif = ''
    try:
        controle(nav, epoch, desk)
        ident_aff = adaptateur.identite(pilote(nav, epoch, desk))
        if not ident_aff:
            motif = 'authentification_humaine_requise'
        elif prof['compte'] and str(ident_aff.get('compte') or '') != prof['compte']:
            motif = 'compte_different_du_profil'
        else:
            recap = recapitulatif(desk, a, row['id'])
            controle(nav, epoch, desk)
            adaptateur.ouvrir_formulaire(pilote(nav, epoch, desk), recap)
            for champ in ('destinataire', 'juridiction', 'rg', 'objet', 'message'):
                controle(nav, epoch, desk)
                adaptateur.remplir(pilote(nav, epoch, desk), champ, row[champ])
            for p, raw in zip(pieces, octets):
                controle(nav, epoch, desk)
                adaptateur.joindre(pilote(nav, epoch, desk), p['ordre'], p['nom'], raw)
    except Stop as ex:
        motif = str(ex).split(':')[0] or 'preremplissage_interrompu'
    if motif:
        desk.db.execute('UPDATE portail_envois56111 SET motif=?, maj_le=? WHERE id=?', (motif[:120], _maintenant(), row['id']))
        desk.db.commit()
        desk.audit('portail56111_preremplissage_interrompu', {'envoi': row['id'], 'motif': motif[:80]})
        return {**vue(desk, a, row['id']), 'message': 'Préremplissage interrompu : rien n’a été envoyé. Transmettez vous-même avec le récapitulatif.'}
    N.changer_main(nav.seance, True)             # main à l'avocat : plus aucune commande du pilote ne part
    N.attendre_acquittement(nav.seance, 5)
    desk.db.execute("UPDATE portail_envois56111 SET etat='preremplie', motif='', version=version+1, maj_le=? WHERE id=? AND etat='confirmee'",
                    (_maintenant(), row['id']))
    desk.db.commit()
    desk.audit('portail56111_prerempli', {'envoi': row['id'], 'main_acquittee': bool(getattr(nav.seance, 'main_acquittee', False))})
    return {**vue(desk, a, row['id']), 'main_acquittee': bool(getattr(nav.seance, 'main_acquittee', False)),
            'message': 'Formulaire prérempli : la main est à vous. Vérifiez puis cliquez vous-même sur « Envoyer » dans le portail.'}


def constater(desk, acteur, ident):
    """Après le clic de l'avocat : ce que le portail affiche (adaptateur qualifié). Accusé fourni → enregistré comme un dépôt de l'avocat ;
    retour incertain → « à vérifier » ; aucune méthode de renvoi n'existe."""
    from .collecte_portail56109 import controle, pilote
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] not in ('preremplie', 'a_verifier'):
        raise Stop('etat_incompatible')
    prof = _profil_pilotable(desk, row)
    adaptateur = ADAPTATEURS[prof['portail']]()
    nav = _navigation(desk, a.compte)
    from . import navigateur56109 as N
    if getattr(nav.seance, 'hold', False):
        N.changer_main(nav.seance, False)         # reprise demandée par l'avocat : nouvelle génération
    epoch = nav.seance.epoch
    try:
        controle(nav, epoch, desk)
        constat = adaptateur.constater(pilote(nav, epoch, desk)) or {}
    except Stop as ex:
        constat = {'statut': 'incertain', 'motif': str(ex).split(':')[0]}
    if constat.get('statut') == 'accuse' and isinstance(constat.get('octets'), bytes) and constat['octets']:
        return enregistrer_accuse(desk, a, row['id'], str(constat.get('nom') or 'accuse'), constat['octets'], 'procedure', origine='portail')
    desk.db.execute("UPDATE portail_envois56111 SET etat='a_verifier', motif=?, version=version+1, maj_le=? WHERE id=? AND etat IN ('preremplie','a_verifier')",
                    (str(constat.get('motif') or 'retour_incertain')[:120], _maintenant(), row['id']))
    desk.db.commit()
    desk.audit('portail56111_retour_incertain', {'envoi': row['id']})
    return {**vue(desk, a, row['id']), 'message': 'Retour du portail incertain : vérifiez dans le portail. Rien n’est renvoyé automatiquement.'}


# ------------------------------------------------------------------------------------------------ accusé de dépôt
def format_accuse(raw):
    """Format CONSTATÉ sur les octets (aucune présomption de ZIP) ; le fichier est gardé tel quel."""
    if raw.startswith(b'%PDF-'):
        return 'pdf'
    if raw.startswith(b'PK\x03\x04') or raw.startswith(b'PK\x05\x06'):
        return 'zip'
    if raw.lstrip()[:5].lower() == b'<?xml':
        return 'xml'
    return 'autre'


def _nom_accuse(nom, fmt):
    from .partage_client56109 import nom_sur
    propre = nom_sur(nom or 'accuse')
    if '.' not in propre or not re.fullmatch(r'[A-Za-z0-9]{1,8}', propre.rsplit('.', 1)[1]):
        propre += {'pdf': '.pdf', 'zip': '.zip', 'xml': '.xml'}.get(fmt, '.bin')
    return propre[:120]


def enregistrer_accuse(desk, acteur, ident, nom, raw, famille='procedure', origine='avocat'):
    """Accusé fourni par le portail : intention durable d'abord (contrainte unique envoi + empreinte), dépôt EXCLUSIF dans le dossier hors
    transaction, relecture et comparaison d'empreinte ; « vérifié » seulement après relecture. Droits relus au moment de l'effet."""
    from .collecte_portail56109 import _deposer, sous_dossier
    from .common import clean_path, under
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _personne(desk, a)
    row = _envoi_visible(desk, a, ident)
    if row['etat'] not in ('confirmee', 'preremplie', 'a_verifier', 'deposee'):
        raise Stop('etat_incompatible')
    if famille not in FAMILLES_ACCUSE:
        raise Stop('famille_invalide')
    if not isinstance(raw, bytes) or not raw:
        raise Stop('accuse_vide')
    if len(raw) > MAX_ACCUSE:
        raise Stop('accuse_trop_volumineux')
    fmt, sha = format_accuse(raw), _sha(raw)
    nom_source = _nom_accuse(nom, fmt)
    existant = desk.db.execute('SELECT * FROM portail_accuses56111 WHERE envoi=? AND sha256=?', (row['id'], sha)).fetchone()
    if existant and existant['etat'] == 'verifie':
        return {**vue(desk, a, row['id']), 'rejoue': True, 'message': 'Cet accusé est déjà enregistré.'}
    pieces = _pieces(desk, row['id'])
    recu = {'envoi': row['id'], 'portail': row['portail'], 'profil': row['profil_id'], 'profil_version': row['profil_version'], 'compte': row['compte'],
            'dossier': row['dossier'], 'juridiction': row['juridiction'], 'rg': row['rg'], 'destinataire': row['destinataire'], 'objet': row['objet'],
            'message_sha256': row['message_sha256'], 'contenu_sha256': row['contenu_sha256'],
            'pieces': [{k: p[k] for k in ('ordre', 'nom', 'source', 'document_id', 'version_id', 'sha256', 'taille')} for p in pieces],
            'accuse': {'nom': nom_source, 'format': fmt, 'sha256': sha, 'taille': len(raw)}, 'origine': origine}
    quand = _maintenant()
    if existant:
        aid = existant['id']
        desk.db.execute("UPDATE portail_accuses56111 SET famille=?, etat='depot_en_cours', maj_le=? WHERE id=?", (famille, quand, aid))
    else:
        aid = 'pa-' + secrets.token_hex(10)
        try:
            desk.db.execute('INSERT INTO portail_accuses56111(id,envoi,nom_source,format,sha256,taille,famille,etat,origine,recu_json,depose_par,cree_le,maj_le) '
                            "VALUES(?,?,?,?,?,?,?,'depot_en_cours',?,?,?,?,?)", (aid, row['id'], nom_source, fmt, sha, len(raw), famille, origine,
                                                                                 _canon(recu), a.compte, quand, quand))
        except sqlite3.IntegrityError:
            desk.db.rollback()
            raise Stop('version_perimee') from None
    desk.db.commit()
    _exiger_dossier(desk, a, row['dossier'])         # droits relus au moment de l'effet
    racine = _racine(desk, row['dossier'])
    cible = clean_path('%s/%s' % (racine, sous_dossier(desk, famille)))
    if not under(cible, racine):
        raise Stop('destination_hors_dossier')
    from .fichiers5670 import client as client_fichiers
    dav = client_fichiers(desk)
    chemin = existant['chemin'] if existant and existant['chemin'] else ''
    relu = b''
    if chemin:
        try:
            relu = dav.read_archive(chemin, MAX_ACCUSE + 1)
        except Stop:
            relu, chemin = b'', ''
    if not chemin or _sha(relu) != sha:
        dav.ensure_folder(cible, racine)
        portail = {'ebarreau': 'e-Barreau', 'portalis': 'Portalis'}.get(row['portail'], row['portail'])
        nom_depot = '%s - Accusé de dépôt %s%s - %s' % (quand[:10], portail, (' - RG ' + re.sub(r'[^\w.-]+', '-', row['rg'])) if row['rg'] else '', nom_source)
        chemin = _deposer(dav, clean_path(cible + '/' + nom_depot), raw)
        relu = dav.read_archive(chemin, MAX_ACCUSE + 1)
    etat = 'verifie' if _sha(relu) == sha else 'a_verifier'
    recu['relu_le'], recu['chemin'] = _maintenant(), chemin
    desk.db.execute('UPDATE portail_accuses56111 SET etat=?, chemin=?, recu_json=?, maj_le=? WHERE id=?', (etat, chemin, _canon(recu), _maintenant(), aid))
    if etat == 'verifie':
        desk.db.execute("UPDATE portail_envois56111 SET etat='deposee', motif='', version=version+1, maj_le=? WHERE id=? AND etat<>'deposee'",
                        (_maintenant(), row['id']))
    else:
        desk.db.execute("UPDATE portail_envois56111 SET etat='a_verifier', motif='accuse_a_verifier', version=version+1, maj_le=? WHERE id=?",
                        (_maintenant(), row['id']))
    desk.db.commit()
    desk.audit('portail56111_accuse', {'envoi': row['id'], 'accuse': aid, 'format': fmt, 'etat': etat, 'origine': origine})
    return {**vue(desk, a, row['id']), 'rejoue': False,
            'message': 'Accusé de dépôt enregistré et relu dans le dossier.' if etat == 'verifie' else
            'Accusé déposé mais sa relecture ne correspond pas : vérifiez le fichier dans le dossier.'}


# ------------------------------------------------------------------------------------------------ lectures
def recapitulatif(desk, acteur, ident):
    """Récapitulatif prêt à recopier : texte EXACT, pièces dans l'ordre (nom, version, empreinte), destinataire, juridiction, RG, limites."""
    a = _acteur(acteur)
    row = _envoi_visible(desk, a, ident)
    limites = json.loads(row['limites_json'] or '{}')
    return {'portail': row['portail'], 'compte': row['compte'], 'destinataire': row['destinataire'], 'juridiction': row['juridiction'], 'rg': row['rg'],
            'objet': row['objet'], 'message': row['message'], 'message_sha256': row['message_sha256'],
            'pieces': [{k: p[k] for k in ('ordre', 'nom', 'source', 'document_id', 'version_id', 'sha256', 'taille')} for p in _pieces(desk, row['id'])],
            'limites': limites, 'limites_connues': bool(limites)}


def vue(desk, acteur, ident):
    a = _acteur(acteur)
    row = _envoi_visible(desk, a, ident)
    accuses = [{k: r[k] for k in ('id', 'nom_source', 'format', 'sha256', 'taille', 'famille', 'chemin', 'etat', 'origine', 'cree_le')}
               for r in desk.db.execute('SELECT * FROM portail_accuses56111 WHERE envoi=? ORDER BY cree_le', (row['id'],))]
    from . import confirmations56111 as garde
    defi = garde.etat(desk, CHEMIN, row['id']) if row['etat'] == 'preparee' else None
    return {'id': row['id'], 'dossier': row['dossier'], 'etat': row['etat'], 'etat_libelle': ETATS.get(row['etat'], row['etat']), 'motif': row['motif'],
            'motif_libelle': motif_libelle(row['motif']),
            'acteur': row['acteur'], 'origine': row['origine'], 'profil': row['profil_id'], 'profil_version': row['profil_version'], 'version': row['version'],
            'cree_le': row['cree_le'], 'confirme_le': row['confirme_le'], 'transmis_le': row['transmis_le'], 'annule_le': row['annule_le'],
            'recapitulatif': recapitulatif(desk, a, row['id']), 'accuses': accuses,
            'code': {'expire': defi['expire'], 'essais': defi['essais'], 'etat': defi['etat']} if defi else None, 'envoi_automatique': False}


def envois(desk, acteur, mid, limite=20):
    a = _acteur(acteur)
    _exiger_dossier(desk, a, mid)
    ensure_schema(desk)
    ids = [r[0] for r in desk.db.execute('SELECT id FROM portail_envois56111 WHERE dossier=? ORDER BY cree_le DESC, id DESC LIMIT ?', (str(mid), int(limite)))]
    return [vue(desk, a, i) for i in ids]


def octets_piece(desk, acteur, ident, ordre):
    """Octets d'une pièce de l'envoi, servis seulement s'ils portent encore l'empreinte préparée (sinon refus : jamais une autre version)."""
    a = _acteur(acteur)
    row = _envoi_visible(desk, a, ident)
    try:
        ordre = int(ordre)
    except (TypeError, ValueError):
        raise Stop('introuvable') from None
    p = next((x for x in _pieces(desk, row['id']) if x['ordre'] == ordre), None)
    if p is None:
        raise Stop('introuvable')
    raw, motif = _etat_piece(desk, a, row['dossier'], p)
    if motif:
        raise Stop('piece_modifiee_depuis_preparation')
    return p['nom'], raw


def profils_envoi(desk):
    from . import collecte_portail56109 as C
    out = []
    for p in C.profils(desk):
        row = C._profil(desk, p['id'])
        out.append({**{k: p[k] for k in ('id', 'portail', 'libelle', 'version', 'compte', 'url_entree', 'qualification', 'qualification_libelle', 'capacites')},
                    'pilotable': pilotable(row), 'limites': _limites(p['capacites'])})
    return out


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and role in ECRITURES.get(reste, ())


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code, detail=None):
    from . import confirmations56111 as garde
    base = MESSAGES.get(code) or garde.message(code) or 'Demande refusée.'
    if detail and 'essais_restants56111' in detail:
        base += ' ' + garde.RESTE % int(detail['essais_restants56111'])
    return base


def _fichier(valeur):
    from .partage_client56109 import donnee_fichier
    nom, raw = donnee_fichier(valeur)
    return nom, raw


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    compte, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    a = (compte, role)
    try:
        if reste not in LECTURES and reste not in ECRITURES:
            raise Stop('route_inconnue')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'route_inconnue')
        ensure_schema(desk)
        if method in ('GET', 'HEAD'):
            if reste == 'envoi':
                return _json(vue(desk, a, str(args.get('id') or '')))
            if reste == 'envois':
                return _json({'envois': envois(desk, a, str(args.get('d') or ''))})
            if reste == 'profils':
                return _json({'profils': profils_envoi(desk)})
            nom, raw = octets_piece(desk, a, str(args.get('id') or ''), args.get('ordre'))
            from urllib.parse import quote
            return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': raw,
                    'headers': [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
                                ('Content-Disposition', "attachment; filename=\"piece\"; filename*=UTF-8''%s" % quote(nom, safe=''))]}
        if reste == 'accuse':
            from .partage_client56109 import corps
            d = corps(env, auth, MAX_POST_ACCUSE)
        else:
            try:
                longueur = int(env.get('CONTENT_LENGTH') or 0)
            except ValueError:
                longueur = MAX_POST + 1
            if longueur > MAX_POST:
                raise Stop('requete_trop_longue')
            from .web440 import check_post
            d = check_post(env, auth)
        ident = str(d.get('id') or '')
        if reste == 'preparer':
            return _json(preparer(desk, a, d))
        if reste == 'confirmer':
            return _json(confirmer(desk, a, ident, d.get('code')))
        if reste == 'nouveau-code':
            return _json(nouveau_code(desk, a, ident))
        if reste == 'annuler':
            return _json(annuler(desk, a, ident))
        if reste == 'transmis':
            return _json(declarer_transmis(desk, a, ident))
        if reste == 'accuse':
            nom, raw = _fichier(d.get('accuse') or d.get('fichier'))
            return _json(enregistrer_accuse(desk, a, ident, nom, raw, str(d.get('famille') or 'procedure')))
        if reste == 'ouvrir':
            return _json(ouvrir_portail(desk, a, ident, auth))
        if reste == 'preremplir':
            return _json(preremplir(desk, a, ident))
        if reste == 'constater':
            return _json(constater(desk, a, ident))
        from . import collecte_portail56109 as C
        out = C.qualifier_capacite(desk, compte, role, d.get('profil'), d.get('version') or 0, str(d.get('capacite') or ''), str(d.get('etat') or ''),
                                   d.get('preuve'), {k: d.get(k) for k in C.LIMITES})
        return _json({**out, 'message': 'Capacité enregistrée.'})
    except Stop as ex:
        from .erreurs5634 import detail_of
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis', 'livrable_absent', 'version_inconnue'):
            code = 'introuvable' if code != 'version_inconnue' else 'piece_invalide'
        status = STATUTS.get(code, '404 Not Found' if code == 'route_inconnue' else '422 Unprocessable Entity')
        return _json({'error': code, 'message': message(code, detail_of(ex))}, status)


# ------------------------------------------------------------------------------------------------ écran (onglet Juridiction de la fiche)
def _q(prefix, route, **params):
    return '%s/api440/%s%s?%s' % (prefix, PREFIXE, route, urlencode(params))


def _taille(n):
    n = int(n or 0)
    return ('%d octets' % n) if n < 1024 else ('%.1f Ko' % (n / 1024) if n < 1024 * 1024 else '%.1f Mo' % (n / 1024 / 1024))


def _limites_html(limites):
    if not limites:
        return '<p class="p111-limites">Limites du portail (taille, nombre de pièces) : non connues, à vérifier sur le portail.</p>'
    parts = []
    if 'nombre_max' in limites:
        parts.append('%d pièce(s) au plus' % limites['nombre_max'])
    if 'taille_piece_max' in limites:
        parts.append('%s par pièce au plus' % _taille(limites['taille_piece_max']))
    if 'taille_totale_max' in limites:
        parts.append('%s au total au plus' % _taille(limites['taille_totale_max']))
    return ('<p class="p111-limites">Limites constatées lors de la qualification du profil :</p><ul class="p111-limites">%s</ul>'
            % ''.join('<li>%s</li>' % e(x) for x in parts))


def _candidats(desk, mid, a):
    """Pièces proposées : versions courantes des livrables du dossier, puis fichiers inscrits au registre des documents du dossier."""
    out = []
    try:
        from . import livrables5689 as liv
        liv.ensure_schema(desk)
        hidden, droits = _droits(desk, a)
        rows = desk.db.execute("SELECT document_id, version_id, version_number, title, kind, review_state, path FROM prepared_items5689 WHERE matter_id=? "
                               "AND lifecycle='current' ORDER BY updated DESC LIMIT 30", (mid,)).fetchall()
        from .livrables5689 import NATURES, provisoire
        for r in rows:
            if provisoire(r['version_id']):
                continue
            out.append({'ref': 'l|%s|%s' % (r['document_id'], r['version_id']), 'libelle': '%s — %s, version %d%s' % (
                r['title'] or 'Document', NATURES.get(r['kind'], 'Document'), int(r['version_number']), '' if r['review_state'] == 'reviewed' else ' (à relire)'),
                'document_id': r['document_id']})
    except (Stop, sqlite3.Error):
        pass
    try:
        from . import modele5651
        for d in modele5651.documents(desk, mid, limit=60):
            out.append({'ref': 'f|' + d['path'], 'libelle': d['name'] or PurePosixPath(d['path']).name, 'document_id': ''})
    except (Stop, sqlite3.Error):
        pass
    return out


def _dossier_proc(desk, mid):
    try:
        r = desk.db.execute('SELECT juridiction, rg FROM dossiers5697 WHERE id=?', (mid,)).fetchone()
        return (r['juridiction'] or '', r['rg'] or '') if r else ('', '')
    except sqlite3.Error:
        return '', ''


def formulaire_html(desk, prefix, a, mid):
    from . import collecte_portail56109 as C
    profils = C.profils(desk)
    if not profils:
        return ('<p class="ax-muted">Aucun profil de portail déclaré : l’administrateur le déclare dans Mes routines, carte « Relever mes messages '
                'procéduraux ».</p>')
    juridiction, rg = _dossier_proc(desk, mid)
    options = ''.join('<option value="%s">%s — %s (version %d)</option>' % (e(p['id'], quote=True), e(p['libelle']), e(p['qualification_libelle']), int(p['version']))
                      for p in profils)
    cands = _candidats(desk, mid, a)
    lignes = ''.join('<li><input type="hidden" name="r_%d" value="%s"><label class="p111-rang">Rang<input name="p_%d" inputmode="numeric" maxlength="3" '
                     'size="3" data-p111-doc="%s" aria-label="Rang de %s"></label><span>%s</span></li>' % (
                         i, e(c['ref'], quote=True), i, e(c['document_id'], quote=True), e(c['libelle'], quote=True), e(c['libelle']))
                     for i, c in enumerate(cands, 1)) or '<li class="ax-muted">Aucune pièce inscrite au registre de ce dossier.</li>'
    return ('<details class="p111-preparer" id="p111-preparer"><summary class="ax-btn">Préparer un envoi sur le portail</summary>'
            '<form class="p111-form" data-p111-preparer data-api="%spreparer"><input type="hidden" name="dossier" value="%s">'
            '<input type="hidden" name="cle" value="%s"><input type="hidden" name="origine" value="dossier" data-p111-origine>'
            '<label>Profil de portail<select name="profil" required>%s</select></label>'
            '<label>Destinataire (tel qu’il figure dans le portail)<input name="destinataire" maxlength="200" required></label>'
            '<div class="p111-ligne"><label>Juridiction<input name="juridiction" maxlength="200" value="%s"></label>'
            '<label>RG<input name="rg" maxlength="60" value="%s"></label></div>'
            '<label>Objet<input name="objet" maxlength="300" required></label>'
            '<label>Texte exact du message<textarea name="message" rows="6" maxlength="20000" required></textarea></label>'
            '<fieldset class="p111-pieces"><legend>Pièces à joindre : donnez un rang à chacune (1, 2, 3…) ; laissez vide pour ne pas la joindre</legend>'
            '<ul>%s</ul></fieldset>'
            '<button class="ax-btn" type="submit">Préparer et obtenir le code</button><p role="status" class="m5-status" data-p111-statut></p></form></details>') % (
        PREFIXE, e(mid, quote=True), secrets.token_hex(12), options, e(juridiction, quote=True), e(rg, quote=True), lignes)


def motif_libelle(motif):
    """Motif lisible par l'avocat (jamais un code technique à l'écran)."""
    m = str(motif or '')
    if m in MOTIFS:
        return MOTIFS[m]
    piece = re.fullmatch(r'piece_(\d+)_\w+', m)
    if piece:
        return 'La pièce %s a changé.' % piece.group(1)
    return 'Motif : voir le journal.' if m else ''


def _envoi_html(prefix, v, role):
    r = v['recapitulatif']
    pieces = ''.join('<li><a href="%s" download>%s</a> <span class="ax-muted">%s</span></li>' % (
        e(_q(prefix, 'piece', id=v['id'], ordre=p['ordre']), quote=True), e(p['nom']),
        e('Version %s, %s, empreinte %s.' % (str(p['version_id'])[:24], _taille(p['taille']), p['sha256'][:12]))) for p in r['pieces']) or         '<li class="ax-muted">Aucune pièce.</li>'
    accuses = ''.join('<li><span>%s</span> · <span>%s</span> · <span>%s</span>%s</li>' % (
        e(x['nom_source']), e(FORMATS.get(x['format'], 'Autre format')), e('Empreinte %s' % x['sha256'][:12]),
        (' · <span>%s</span>' % e(ETATS_ACCUSE.get(x['etat'], x['etat']))) if x['etat'] != 'verifie' else '') for x in v['accuses'])
    actions = ''
    avocat = role in ('administrateur', 'avocat')
    form = lambda route, bouton, extra='', confirm='': ('<form class="m5-form r52-inline" data-api="%s%s" data-reload%s><input type="hidden" name="id" value="%s">%s'
                                                       '<button class="ax-btn ghost" type="submit">%s</button></form>') % (
        PREFIXE, route, (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '', e(v['id'], quote=True), extra, e(bouton))
    if v['etat'] == 'preparee':
        if avocat:
            actions += ('<form class="m5-form p111-code" data-api="%sconfirmer" data-reload><input type="hidden" name="id" value="%s">'
                        '<label>Code de confirmation (six chiffres)<input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required></label>'
                        '<button class="ax-btn" type="submit">Confirmer l’envoi préparé</button></form>') % (PREFIXE, e(v['id'], quote=True))
        actions += ('<button class="ax-btn ghost" type="button" data-p111-nouveau="%s">Nouveau code</button>' % e(v['id'], quote=True))
    if v['etat'] in ('confirmee', 'preremplie', 'a_verifier') and avocat:
        actions += form('ouvrir', 'Ouvrir le portail dans le navigateur du poste')
        if v['etat'] in ('confirmee', 'preremplie'):
            actions += form('transmis', 'J’ai transmis dans le portail')
    if v['etat'] in ('confirmee', 'preremplie', 'a_verifier', 'deposee') and avocat:
        actions += ('<form class="m5-form p111-accuse" data-api="%saccuse" data-reload><input type="hidden" name="id" value="%s">'
                    '<label>Accusé de dépôt fourni par le portail<input type="file" name="accuse" data-envoi-fichier required></label>'
                    '<label>Ranger dans<select name="famille"><option value="procedure">Procédure</option><option value="correspondances">Correspondances</option>'
                    '</select></label><button class="ax-btn" type="submit">Enregistrer l’accusé de dépôt</button></form>') % (PREFIXE, e(v['id'], quote=True))
    if v['etat'] in ACTIFS and not any(x['etat'] == 'verifie' for x in v['accuses']):
        actions += form('annuler', 'Annuler l’envoi', confirm='Annuler cet envoi préparé ? Rien n’est retiré du portail.')
    motif = (' <span class="ax-muted">%s</span>' % e(motif_libelle(v['motif']))) if v['motif'] else ''
    return ('<li class="p111-envoi" data-p111-envoi="%s"><details%s><summary><strong>%s</strong> — <span>%s</span> · <span>%s</span>%s</summary>'
            '<dl class="p111-recap"><dt>Portail et compte</dt><dd>%s — %s</dd><dt>Destinataire</dt><dd>%s</dd><dt>Juridiction et RG</dt><dd>%s</dd>'
            '<dt>Objet</dt><dd>%s</dd></dl>'
            '<label>Texte exact à recopier<textarea readonly rows="5" data-p111-texte>%s</textarea></label>'
            '<button class="ax-btn ghost" type="button" data-p111-copier>Copier le texte</button>'
            '<p>Pièces à joindre, dans cet ordre :</p><ol class="p111-liste">%s</ol>%s%s'
            '<div class="p111-actions">%s</div></details></li>') % (
        e(v['id'], quote=True), ' open' if v['etat'] in ('preparee', 'confirmee', 'preremplie', 'a_verifier') else '', e(r['objet']), e(v['etat_libelle']),
        e(v['cree_le'][:10]), motif, e({'ebarreau': 'e-Barreau', 'portalis': 'Portalis'}.get(r['portail'], r['portail'])), e(r['compte'] or 'compte non précisé'),
        e(r['destinataire']), e(' · '.join(x for x in (r['juridiction'], ('RG ' + r['rg']) if r['rg'] else '') if x) or 'non précisés'), e(r['objet']),
        e(r['message']), pieces, _limites_html(r['limites']), ('<p>Accusés enregistrés :</p><ul>%s</ul>' % accuses) if accuses else '', actions)


def montage_fiche(desk, acteur, prefix, m):
    """Point de montage de l'onglet « Juridiction » de la fiche (``fiche_ui5698.juridiction_html``) ; ne lève jamais."""
    try:
        a = _acteur(acteur)
        mid = str(m['id'])
        _exiger_dossier(desk, a, mid)
        ensure_schema(desk)
        liste = envois(desk, a, mid)
        corps = formulaire_html(desk, prefix, a, mid) if a.role in ROLES else ''
        items = ''.join(_envoi_html(prefix, v, a.role) for v in liste)
        return ('<section class="d79-card p111" id="p111-portail" aria-labelledby="p111-titre"><div class="d79-card-head"><h2 id="p111-titre">Envois sur le portail '
                '(e-Barreau, Portalis)</h2></div><p class="p111-avert">AxiorHub ne pilote aucun portail : aucun écran n’est qualifié. Vous transmettez vous-même ; '
                'AxiorHub prépare le récapitulatif exact, garde la trace et enregistre l’accusé.</p>%s<div class="p111-code-zone" data-p111-code-zone hidden '
                'role="status" aria-live="polite"></div><ul class="p111-envois">%s</ul><script defer src="%s/static/v56111-portail.js"></script></section>') % (
            corps, items or '<li class="ax-muted">Aucun envoi préparé pour ce dossier.</li>', e(prefix))
    except Stop:
        return ''
    except Exception:   # noqa: BLE001 — le reste de la fiche s'affiche ; le motif est tracé
        try:
            desk.audit('portail56111_bloc_indisponible', {'dossier': str(m.get('id') or '')})
        except Exception:   # noqa: BLE001
            pass
        return '<p class="d79-warn">Envois sur le portail : affichage momentanément indisponible.</p>'


def statiques():
    return {'/static/v56111-portail.js': 'text/javascript', '/static/v56111-portail.css': 'text/css'}
