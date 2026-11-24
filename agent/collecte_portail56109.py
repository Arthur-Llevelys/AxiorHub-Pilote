"""5.6.109 (lot G4, G11 et G12) : collecte supervisée d'un portail procédural (e-Barreau) et routine « tous les trois jours ».

Périmètre (cahier, « Collecter les non lus et leurs documents » ; INTEGRATION-PARCOURS-PORTAILS109 §§ 5 à 9) : consultation dans le navigateur
supervisé DU POSTE, collecte des messages et pièces, classement par dossier, dates « à vérifier », brouillons ordinaires préparés. **Aucune
API officielle ou privée du portail, aucun envoi ni dépôt RPVA, aucune signature, aucun paiement, aucune suppression, aucun changement de
délégation.** Le module ne contient aucun sélecteur d'écran : un **adaptateur de profil qualifié** (``ADAPTATEURS``) décrit les seules
transitions admises ; aucun n'est livré tant qu'un vrai écran n'a pas été qualifié — le profil reste alors « Non qualifié » et rien n'est
collecté (import manuel possible). Portalis reste « Non qualifié » tant que l'adresse exacte et le type de compte professionnel manquent.

- **Profil déclaratif versionné** (``collecte_profils56109``) : portail, adresse d'entrée, domaines nécessaires, système, navigateur, compte,
  avocat, délégation, type de compte, qualification (Non qualifié / À tester / Qualifié sur ce poste) et date de preuve ; toute modification
  crée une version et périme la preuve.
- **Authentification humaine** : le logiciel ne saisit rien ; sans session, la mission passe « En attente de connexion » (aucune boucle, aucun
  PIN redemandé) ; une identité affichée différente du profil suspend la mission.
- **Barrière de prise de main (lot O)** : chaque observation et chaque action passent par ``navigateur56109.Pilote`` (``barriere``) avec la
  génération de la séance ; ``puis_je`` est relu avant chaque étape. Main à l'avocat, pause globale ou génération changée : rien n'est envoyé
  au navigateur, la mission est « suspendue » et son curseur conservé.
- **Fenêtre et inventaire** : cinq jours glissants (fuseau du cabinet, bornes UTC enregistrées) ; la liste des non-lus est figée AVANT toute
  ouverture ; toutes les pages ou couverture **partielle** annoncée (le plafond de pages n'est jamais une collecte complète ; une liste
  partielle ne prouve pas une boîte vide).
- **Originaux** : export natif du portail (ZIP du message et de ses annexes) associé au clic qui l'a déclenché, empreinte, lecture bornée sans
  extraction libre (``parcours56109.lire_archive``) ; noms identiques conservés par ordinal ; copie PDF de la page en complément seulement
  (``Tab.pdf``), identité du message vérifiée avant et après.
- **Classement message par message** : dossier résolu par le registre (correspondance confirmée, ou juridiction + RG du dossier ; RG seul :
  question), droits relus avant chaque dépôt, familles (Correspondances, Procédure, pièces de partie, À classer), dépôt exclusif relu
  (empreinte) et reçu par pièce. Un message ambigu reste dans la zone récupérée avec une question ; les autres continuent.
- **Dates** sourcées « à vérifier » (``parcours56109.plan_date``) proposées à l'agenda, jamais calculées juridiquement ni inscrites d'office
  (point de montage : ``propositions_dates``).
- **Brouillons** ordinaires préparés pour le client et l'avocat du client (dominus litis) confirmés au registre ; dépôt dans Brouillons par
  une action DISTINCTE (``deposer_brouillons``), reçu, rapprochement si incertain ; jamais « Répondre à tous », jamais le greffe ni l'adverse.
- **Reprise sans doublon** : clés stables (portail, compte, mandant, identifiant ; ordinal et empreinte ; clé métier du brouillon).

G12 : ``executer_routine`` est l'exécuteur du type fermé ``collecte_portail`` de ``routines5652`` (préparer et collecter seulement : il ne
dépose, ne confirme ni n'envoie rien) ; ``creer_routine`` crée la routine « Relever mes messages procéduraux » (tous les 3 jours, rattrapage
72 h), désactivée tant que le profil n'est pas qualifié.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import time

from .common import Stop

PORTAILS = {'ebarreau': 'e-Barreau (CNB)', 'portalis': 'Portalis'}
QUALIFICATIONS = {'non_qualifie': 'Non qualifié', 'a_tester': 'À tester', 'qualifie': 'Qualifié sur ce poste'}
ETATS_MISSION = {'prete': 'Prête', 'en_cours': 'En cours', 'attente_connexion': 'En attente de connexion : ouvrez le navigateur et connectez-vous',
                 'identite_differente': 'Compte ou délégation affichés différents du profil : mission suspendue',
                 'suspendue': 'Suspendue (main à l’avocat, pause ou arrêt) : à reprendre', 'terminee': 'Terminée (couverture complète)',
                 'partielle': 'Terminée avec réserves (couverture partielle ou questions)', 'profil_non_qualifie': 'Profil non qualifié : rien n’a été collecté',
                 'echec': 'Échec'}
FAMILLES = {'correspondances': 'Correspondances', 'procedure': 'Procédure', 'pieces_adverses': 'Pièces adverses', 'nos_pieces': 'Nos pièces',
            'a_classer': 'À classer'}
NATURES_NOM = (('conclusions', 'conclusions'), ('assignation', 'assignation'), ('convocation', 'convocation'), ('jugement', 'jugement'),
               ('ordonnance', 'ordonnance'), ('arret', 'jugement'), ('accuse', 'accuse_procedural'), ('message', 'message'), ('corps', 'message'))
FENETRE_JOURS = 5
MAX_PAGES = 20                    # plafond de pages de l'inventaire : l'atteindre rend une couverture PARTIELLE
MAX_MESSAGES = 60                 # messages traités par passage ; le reste attend le passage suivant (curseur conservé)
MAX_ARCHIVE = 60 * 1024 * 1024
ATTENTE_TELECHARGEMENT = 60       # secondes
ADAPTATEURS = {}                  # portail -> classe d'adaptateur QUALIFIÉ ; aucun livré (aucun écran réel qualifié)
NAVIGATION = None                 # essais : fabrique (desk, acteur) -> Navigation ; production : navigateur supervisé du poste
BOITE = None                      # essais : fausse messagerie (cfg -> boîte) pour le dépôt des brouillons
SCHEMA = '''
CREATE TABLE IF NOT EXISTS collecte_profils56109(id TEXT PRIMARY KEY, portail TEXT NOT NULL, libelle TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  url_entree TEXT NOT NULL DEFAULT '', domaines_json TEXT NOT NULL DEFAULT '[]', systeme TEXT NOT NULL DEFAULT '', navigateur TEXT NOT NULL DEFAULT '',
  compte TEXT NOT NULL DEFAULT '', avocat TEXT NOT NULL DEFAULT '', delegation TEXT NOT NULL DEFAULT '', type_compte TEXT NOT NULL DEFAULT '',
  qualification TEXT NOT NULL DEFAULT 'non_qualifie', preuve_le TEXT NOT NULL DEFAULT '', preuve_par TEXT NOT NULL DEFAULT '', maj_par TEXT NOT NULL DEFAULT '',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collecte_missions56109(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, profil_id TEXT NOT NULL,
  profil_version INTEGER NOT NULL, portail TEXT NOT NULL, compte TEXT NOT NULL DEFAULT '', mandant TEXT NOT NULL DEFAULT '', debut_utc TEXT NOT NULL,
  fin_utc TEXT NOT NULL, mode TEXT NOT NULL, etat TEXT NOT NULL, couverture TEXT NOT NULL DEFAULT 'partielle', motif TEXT NOT NULL DEFAULT '',
  pages_lues INTEGER NOT NULL DEFAULT 0, occurrence TEXT NOT NULL DEFAULT '', compteurs_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(acteur, cle));
CREATE INDEX IF NOT EXISTS collecte_missions56109_profil ON collecte_missions56109(profil_id, etat);
CREATE TABLE IF NOT EXISTS collecte_messages56109(cle TEXT PRIMARY KEY, portail TEXT NOT NULL, compte TEXT NOT NULL, mandant TEXT NOT NULL, identifiant TEXT NOT NULL,
  mission TEXT NOT NULL, recu_utc TEXT NOT NULL, non_lu_initial INTEGER NOT NULL, objet TEXT NOT NULL DEFAULT '', juridiction TEXT NOT NULL DEFAULT '',
  instance TEXT NOT NULL DEFAULT '', rg TEXT NOT NULL DEFAULT '', portail_dossier TEXT NOT NULL DEFAULT '', metadonnees_json TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL DEFAULT 'inventorie', effet_portail TEXT NOT NULL DEFAULT '', dossier TEXT NOT NULL DEFAULT '', dossier_version TEXT NOT NULL DEFAULT '',
  question TEXT NOT NULL DEFAULT '', archive_sha256 TEXT NOT NULL DEFAULT '', archive_local TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL,
  UNIQUE(portail, compte, mandant, identifiant));
CREATE TABLE IF NOT EXISTS collecte_pieces56109(message TEXT NOT NULL, ordinal INTEGER NOT NULL, origine TEXT NOT NULL, nom_source TEXT NOT NULL,
  sha256 TEXT NOT NULL, taille INTEGER NOT NULL, famille TEXT NOT NULL DEFAULT 'a_classer', dossier TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL DEFAULT 'attendue', recu_json TEXT NOT NULL DEFAULT '{}', maj_le TEXT NOT NULL, PRIMARY KEY(message, ordinal, origine));
CREATE TABLE IF NOT EXISTS collecte_dates56109(cle TEXT PRIMARY KEY, message TEXT NOT NULL, dossier TEXT NOT NULL, date TEXT NOT NULL, nature TEXT NOT NULL,
  extrait TEXT NOT NULL, etat TEXT NOT NULL, cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collecte_brouillons56109(cle TEXT PRIMARY KEY, message TEXT NOT NULL, dossier TEXT NOT NULL, contact_id TEXT NOT NULL,
  destinataire TEXT NOT NULL, role TEXT NOT NULL, objet TEXT NOT NULL, corps TEXT NOT NULL, mid TEXT NOT NULL, etat TEXT NOT NULL,
  recu_json TEXT NOT NULL DEFAULT '{}', erreur TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collecte_correspondances56109(portail TEXT NOT NULL, compte TEXT NOT NULL, portail_dossier TEXT NOT NULL, dossier TEXT NOT NULL,
  confirme_par TEXT NOT NULL, confirme_le TEXT NOT NULL, PRIMARY KEY(portail, compte, portail_dossier));
'''


class Suspendu(Stop):
    """La barrière ou une autorité a refusé l'étape : la mission s'arrête, son curseur reste (aucune commande envoyée)."""


@dataclass
class Navigation:
    """Séance du navigateur supervisé (barrière du lot O), onglet piloté et navigateur (événements DevTools, téléchargements)."""
    seance: object
    onglet: object
    navigateur: object


class Adaptateur:
    """Contrat d'un profil d'écran QUALIFIÉ (aucune implémentation livrée). Chaque méthode reçoit un ``Pilote`` : toute observation et toute
    action passent par la barrière. Transitions admises seulement : lire l'identité, lister les non-lus, ouvrir un message, lancer l'export natif.
    Aucune méthode d'envoi, de dépôt, de signature, de suppression ou de délégation n'existe dans ce contrat."""
    version = '0'

    def identite(self, pilote):                     # {'compte', 'avocat', 'delegation'} affichés, ou None (pas de session)
        raise Stop('adaptateur_non_qualifie')

    def page_non_lus(self, pilote, numero, debut, fin):   # {'messages': [...], 'derniere': bool} SANS ouvrir les messages
        raise Stop('adaptateur_non_qualifie')

    def ouvrir(self, pilote, identifiant):
        raise Stop('adaptateur_non_qualifie')

    def message_affiche(self, pilote):
        raise Stop('adaptateur_non_qualifie')

    def exporter(self, pilote, identifiant):        # clic sur le bouton visible d'export natif (ZIP) du message ouvert
        raise Stop('adaptateur_non_qualifie')


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def cle_message(portail, compte, mandant, identifiant):
    return _sha('msg', portail, compte, mandant, identifiant)[:32]


def _norm(texte):
    from .offre56109 import plier
    return re.sub(r'[^0-9a-z]', '', plier(texte))


# ------------------------------------------------------------------------------------------------ profils
def _profil(desk, pid):
    row = desk.db.execute('SELECT * FROM collecte_profils56109 WHERE id=?', (str(pid or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def enregistrer_profil(desk, acteur, role, data):
    """Administrateur : profil déclaratif. Chaque modification = nouvelle version et preuve périmée (« À tester »). Portalis sans adresse
    exacte ni type de compte professionnel : « Non qualifié », jamais le compte du client (FranceConnect) ni une adresse supposée."""
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    portail = str(data.get('portail') or '')
    if portail not in PORTAILS:
        raise Stop('portail_inconnu')
    champs = {}
    for k, n in (('libelle', 120), ('url_entree', 300), ('systeme', 60), ('navigateur', 60), ('compte', 200), ('avocat', 200), ('delegation', 200),
                 ('type_compte', 40)):
        champs[k] = re.sub(r'\s+', ' ', str(data.get(k) or '')).strip()[:n]
    if champs['url_entree'] and not re.fullmatch(r'https://[a-z0-9.-]+(?:/[^\s<>"]*)?', champs['url_entree']):
        raise Stop('adresse_portail_invalide')
    domaines = sorted({d.strip().lower() for d in re.split(r'[\s,;]+', str(data.get('domaines') or '')) if d.strip()})[:12]
    if any(not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', d) or d.startswith('*') for d in domaines):
        raise Stop('domaine_portail_invalide')       # un domaine par ligne, jamais tout un suffixe
    if champs['type_compte'] and champs['type_compte'] not in ('professionnel',):
        raise Stop('type_compte_non_professionnel')
    pid = str(data.get('id') or '') or 'pp-' + _sha(portail, champs['compte'], secrets.token_hex(4))[:16]
    quand = _maintenant()
    row = desk.db.execute('SELECT * FROM collecte_profils56109 WHERE id=?', (pid,)).fetchone()
    if row:
        if data.get('version') not in (None, '') and int(data['version']) != row['version']:
            raise Stop('version_perimee')
        cur = desk.db.execute('UPDATE collecte_profils56109 SET portail=?,libelle=?,url_entree=?,domaines_json=?,systeme=?,navigateur=?,compte=?,avocat=?,delegation=?,'
                              "type_compte=?,qualification='non_qualifie',preuve_le='',preuve_par='',version=version+1,maj_par=?,maj_le=? WHERE id=? AND version=?",
                              (portail, champs['libelle'] or PORTAILS[portail], champs['url_entree'], json.dumps(domaines), champs['systeme'], champs['navigateur'],
                               champs['compte'], champs['avocat'], champs['delegation'], champs['type_compte'], acteur, quand, pid, row['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
    else:
        desk.db.execute('INSERT INTO collecte_profils56109(id,portail,libelle,url_entree,domaines_json,systeme,navigateur,compte,avocat,delegation,type_compte,'
                        'maj_par,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (pid, portail, champs['libelle'] or PORTAILS[portail], champs['url_entree'], json.dumps(domaines), champs['systeme'], champs['navigateur'],
                         champs['compte'], champs['avocat'], champs['delegation'], champs['type_compte'], acteur, quand, quand))
    desk.db.commit()
    desk.audit('collecte56109_profil', {'profil': pid, 'portail': portail, 'par': acteur})
    return vue_profil(desk, pid)


def manques_qualification(row):
    """Ce qui empêche de qualifier le profil (liste vide : qualifiable par une preuve d'essai humaine)."""
    out = [k for k in ('url_entree', 'compte', 'systeme', 'navigateur') if not row[k]]
    if not json.loads(row['domaines_json'] or '[]'):
        out.append('domaines')
    if row['portail'] == 'portalis' and row['type_compte'] != 'professionnel':
        out.append('type_compte')                   # Portalis : accès professionnel réel à identifier
    if row['portail'] not in ADAPTATEURS:
        out.append('adaptateur_ecran')               # aucun écran réel qualifié : aucune transition admise
    return out


def qualifier_profil(desk, acteur, role, pid, version, preuve):
    """Administrateur : preuve d'essai RÉEL sur ce poste (système, navigateur, compte, délégation) ; refusée s'il manque un élément."""
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    row = _profil(desk, pid)
    if int(version) != row['version']:
        raise Stop('version_perimee')
    manque = manques_qualification(row)
    if manque:
        raise Stop('profil_non_qualifiable:' + ','.join(manque))
    if not str(preuve or '').strip():
        raise Stop('preuve_essai_requise')
    cur = desk.db.execute("UPDATE collecte_profils56109 SET qualification='qualifie',preuve_le=?,preuve_par=?,maj_le=? WHERE id=? AND version=?",
                          (_maintenant(), acteur, _maintenant(), pid, row['version']))
    if not cur.rowcount:
        raise Stop('version_perimee')
    desk.db.commit()
    return vue_profil(desk, pid)


def vue_profil(desk, pid):
    row = _profil(desk, pid)
    return {'id': row['id'], 'portail': row['portail'], 'libelle': row['libelle'], 'version': row['version'], 'url_entree': row['url_entree'],
            'domaines': json.loads(row['domaines_json'] or '[]'), 'systeme': row['systeme'], 'navigateur': row['navigateur'], 'compte': row['compte'],
            'avocat': row['avocat'], 'delegation': row['delegation'], 'type_compte': row['type_compte'], 'qualification': row['qualification'],
            'qualification_libelle': QUALIFICATIONS[row['qualification']], 'preuve_le': row['preuve_le'], 'manques': manques_qualification(row)}


def profils(desk):
    return [vue_profil(desk, r['id']) for r in desk.db.execute('SELECT id FROM collecte_profils56109 ORDER BY portail, cree_le')]


# ------------------------------------------------------------------------------------------------ navigateur et barrière
def _navigation_produit(desk, acteur):
    """Navigateur supervisé du poste (une séance à la fois) ; la séance est celle du gestionnaire : « Prendre la main » agit sur elle."""
    from . import navigateur5673 as nv
    if not nv.available():
        raise Stop('poste_requis')
    mgr = nv.manager(desk.c)
    with mgr.lock:
        if mgr.session is not None and mgr.session.state not in nv.FINALS:
            raise Stop('une_seance_est_deja_en_cours')
        s = nv.Session(mgr, acteur, 'Collecte supervisée du portail (lecture et téléchargement officiels)', '', '')
        s.state = 'en_cours'
        mgr.session = s
    browser = mgr._browser()
    return Navigation(s, nv.Tab(browser.cdp), browser)


def _navigation(desk, acteur):
    return NAVIGATION(desk, acteur) if NAVIGATION is not None else _navigation_produit(desk, acteur)


def controle(nav, epoch, desk):
    """Avant chaque étape : barrière de prise de main (séance, génération, pause globale) ; refus → ``Suspendu``."""
    from . import navigateur56109 as N
    etat = N.puis_je(nav.seance, epoch, desk)
    if not etat['ok']:
        raise Suspendu(etat['code'] or 'seance_indisponible')


def pilote(nav, epoch, desk):
    from . import navigateur56109 as N
    return N.Pilote(nav.onglet, nav.seance, epoch, desk)


def _attendre_telechargement(nav, ctl, delai=ATTENTE_TELECHARGEMENT):
    """Un SEUL téléchargement commencé après le clic, terminé, borné : associé au message qui l'a déclenché ; sinon refus."""
    commences, termines = {}, set()
    limite = time.monotonic() + delai
    while time.monotonic() < limite:
        ctl()
        for ev in nav.navigateur.cdp.drain():
            methode, p = ev.get('method'), ev.get('params') or {}
            if methode == 'Browser.downloadWillBegin':
                commences[p.get('guid')] = str(p.get('suggestedFilename') or 'export.zip')
            elif methode == 'Browser.downloadProgress' and p.get('state') == 'completed':
                termines.add(p.get('guid'))
            elif methode == 'Browser.downloadProgress' and p.get('state') == 'canceled':
                raise Stop('telechargement_annule')
        if len(commences) > 1:
            raise Stop('telechargement_non_associe')
        if commences and set(commences) <= termines:
            guid, nom = next(iter(commences.items()))
            chemin = Path(nav.navigateur.downloads) / str(guid)
            if not chemin.is_file() or chemin.stat().st_size > MAX_ARCHIVE:
                raise Stop('telechargement_incomplet')
            data = chemin.read_bytes()
            try:
                chemin.unlink()
            except OSError:
                pass
            return nom, data
        time.sleep(0.05)
    raise Stop('telechargement_non_recu')


# ------------------------------------------------------------------------------------------------ missions
def _mission(desk, mid):
    row = desk.db.execute('SELECT * FROM collecte_missions56109 WHERE id=?', (str(mid or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _maj_mission(desk, row, **champs):
    champs['maj_le'] = _maintenant()
    cur = desk.db.execute('UPDATE collecte_missions56109 SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join(k + '=?' for k in champs),
                          tuple(champs.values()) + (row['id'], row['version']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    return _mission(desk, row['id'])


def fenetre(desk, maintenant=None, jours=FENETRE_JOURS):
    """Cinq jours glissants jusqu'au lancement ; bornes affichées en heure du cabinet, enregistrées en UTC."""
    fin = (maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    return (fin - timedelta(days=jours)).isoformat(), fin.isoformat()


def lancer(desk, acteur, role, data, occurrence='', maintenant=None):
    """Mission de collecte (idempotente par acteur et clé) ; profil QUALIFIÉ exigé, sinon état explicite et rien n'est collecté."""
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    cle = str(data.get('cle') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    prof = _profil(desk, data.get('profil'))
    mode = 'preparer_et_collecter'
    debut, fin = fenetre(desk, maintenant)
    empreinte = _sha(prof['id'], mode, occurrence)
    row = desk.db.execute('SELECT * FROM collecte_missions56109 WHERE acteur=? AND cle=?', (acteur, cle)).fetchone()
    if row:
        if row['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return row
    mid = 'cm-' + secrets.token_hex(10)
    etat = 'prete' if prof['qualification'] == 'qualifie' and prof['portail'] in ADAPTATEURS else 'profil_non_qualifie'
    quand = _maintenant()
    desk.db.execute('INSERT INTO collecte_missions56109(id,acteur,cle,empreinte,profil_id,profil_version,portail,compte,mandant,debut_utc,fin_utc,mode,etat,motif,'
                    'occurrence,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, acteur, cle, empreinte, prof['id'], prof['version'], prof['portail'], prof['compte'], prof['delegation'] or prof['avocat'],
                     debut, fin, mode, etat, '' if etat == 'prete' else ','.join(manques_qualification(prof)) or 'qualification_absente', occurrence, quand, quand))
    desk.db.commit()
    return _mission(desk, mid)


def executer(desk, mission_id, acteur=None):
    """Déroule (ou reprend) la mission : connexion, inventaire, téléchargements, résolution, classement, dates, brouillons préparés, bilan.
    Chaque étape relit la barrière ; un refus suspend la mission (curseur conservé) sans rien envoyer au navigateur."""
    row = _mission(desk, mission_id)
    if row['etat'] in ('terminee', 'profil_non_qualifie'):
        return vue_mission(desk, row['id'])
    prof = _profil(desk, row['profil_id'])
    if prof['version'] != row['profil_version'] or prof['qualification'] != 'qualifie' or prof['portail'] not in ADAPTATEURS:
        _maj_mission(desk, row, etat='profil_non_qualifie', motif='profil_modifie_ou_non_qualifie')
        return vue_mission(desk, row['id'])
    adaptateur = ADAPTATEURS[prof['portail']]()
    from .missions567 import check_authority
    check_authority(desk, {})                       # autorité de mission relue avant tout effet (pause des préparations, révocation)
    try:
        nav = _navigation(desk, acteur or row['acteur'])
    except Stop as ex:
        _maj_mission(desk, row, etat='suspendue', motif=str(ex).split(':')[0])
        return vue_mission(desk, row['id'])
    row = _maj_mission(desk, row, etat='en_cours', motif='')
    epoch = nav.seance.epoch
    compteurs = json.loads(row['compteurs_json'] or '{}')
    try:
        controle(nav, epoch, desk)
        ident = adaptateur.identite(pilote(nav, epoch, desk))
        if not ident:
            _maj_mission(desk, row, etat='attente_connexion', motif='authentification_humaine_requise')
            return vue_mission(desk, row['id'])
        if (prof['compte'] and str(ident.get('compte') or '') != prof['compte']) or \
                (prof['delegation'] and str(ident.get('delegation') or '') != prof['delegation']):
            _maj_mission(desk, row, etat='identite_differente', motif='compte_ou_delegation_differents')
            return vue_mission(desk, row['id'])
        row = _inventaire(desk, row, prof, adaptateur, nav, epoch)
        compteurs.update(_traiter_messages(desk, row, prof, adaptateur, nav, epoch, acteur or row['acteur']))
    except Suspendu as ex:
        _maj_mission(desk, _mission(desk, row['id']), etat='suspendue', motif=str(ex).split(':')[0], compteurs_json=json.dumps(compteurs))
        return vue_mission(desk, row['id'])
    except Stop as ex:
        code = str(ex).split(':')[0]
        from .navigateur56109 import DecisionPerimee
        etat = 'suspendue' if isinstance(ex, DecisionPerimee) or code in ('arrete_par_l_avocat', 'ia_en_pause', 'preparation_suspendue') else 'echec'
        _maj_mission(desk, _mission(desk, row['id']), etat=etat, motif=code, compteurs_json=json.dumps(compteurs))
        return vue_mission(desk, row['id'])
    finally:
        _liberer(nav)
    row = _mission(desk, row['id'])
    reste = desk.db.execute("SELECT COUNT(*) FROM collecte_messages56109 WHERE mission=? AND etat NOT IN ('classe','hors_fenetre')", (row['id'],)).fetchone()[0]
    etat = 'terminee' if row['couverture'] == 'complete' and not reste else 'partielle'
    _maj_mission(desk, row, etat=etat, compteurs_json=json.dumps(compteurs))
    desk.audit('collecte56109_mission', {'mission': row['id'], 'etat': etat, 'couverture': row['couverture'], **{k: v for k, v in compteurs.items() if isinstance(v, int)}})
    return vue_mission(desk, row['id'])


def _liberer(nav):
    """Fin de séance de collecte : la séance du gestionnaire passe à un état final (le navigateur reste ouvert, connexions conservées)."""
    try:
        if getattr(nav.seance, 'state', '') not in ('termine', 'arrete', 'erreur', 'limite_atteinte', 'a_verifier'):
            nav.seance.state = 'termine'
    except Exception:   # noqa: BLE001
        pass


def _inventaire(desk, row, prof, adaptateur, nav, epoch):
    """Liste des non-lus FIGÉE avant toute ouverture ; pages jusqu'à la dernière, ou couverture partielle annoncée."""
    from .parcours56109 import Message, selection_messages
    vus, complete, pages, motif = [], False, 0, ''
    for numero in range(1, MAX_PAGES + 1):
        controle(nav, epoch, desk)
        try:
            page = adaptateur.page_non_lus(pilote(nav, epoch, desk), numero, row['debut_utc'], row['fin_utc'])
        except Suspendu:
            raise
        except Stop as ex:
            from .navigateur56109 import DecisionPerimee
            if isinstance(ex, DecisionPerimee):
                raise Suspendu(str(ex).split(':')[0]) from None
            motif = 'inventaire_interrompu:' + str(ex).split(':')[0]
            break
        pages += 1
        vus.extend(page.get('messages') or [])
        if page.get('derniere') is True:
            complete = True
            break
    else:
        motif = 'plafond_de_pages'                      # le plafond n'est jamais une collecte complète
    msgs = []
    for m in vus:
        try:
            recu = datetime.fromisoformat(str(m.get('recu') or ''))
        except ValueError:
            raise Stop('date_message_illisible') from None
        msgs.append(Message(row['portail'], row['compte'] or 'compte', row['mandant'] or 'mandant', str(m.get('identifiant') or ''), recu, m.get('non_lu')))
    sel = selection_messages(msgs, datetime.fromisoformat(row['fin_utc']), FENETRE_JOURS, inventaire_complet=complete)
    retenus = {x.identifiant for x in sel['messages']}
    quand = _maintenant()
    for m in vus:
        if str(m.get('identifiant')) not in retenus:
            continue
        cle = cle_message(row['portail'], row['compte'] or 'compte', row['mandant'] or 'mandant', m['identifiant'])
        meta = {k: m.get(k) for k in ('dates', 'natures', 'pieces_annoncees') if m.get(k) is not None}
        desk.db.execute('INSERT OR IGNORE INTO collecte_messages56109(cle,portail,compte,mandant,identifiant,mission,recu_utc,non_lu_initial,objet,juridiction,instance,rg,'
                        'portail_dossier,metadonnees_json,maj_le) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',
                        (cle, row['portail'], row['compte'] or 'compte', row['mandant'] or 'mandant', str(m['identifiant']), row['id'],
                         datetime.fromisoformat(m['recu']).astimezone(timezone.utc).isoformat(), str(m.get('objet') or '')[:300], str(m.get('juridiction') or '')[:200],
                         str(m.get('instance') or '')[:120], str(m.get('rg') or '')[:60], str(m.get('portail_dossier') or '')[:120],
                         json.dumps(meta, ensure_ascii=False)[:20000], quand))
        # un message déjà connu d'un passage précédent (chevauchement de fenêtres) n'est pas recréé : il est repris par cette mission s'il reste à traiter
        desk.db.execute("UPDATE collecte_messages56109 SET mission=? WHERE cle=? AND etat<>'classe'", (row['id'], cle))
    desk.db.commit()
    return _maj_mission(desk, row, couverture=sel['couverture'], pages_lues=pages, motif=motif)


def nature_du_nom(nom, natures=None):
    if natures and isinstance(natures, dict) and natures.get(nom):
        return str(natures[nom])
    from .offre56109 import plier
    p = plier(nom)
    for mot, nature in NATURES_NOM:
        if mot in p:
            return nature
    return 'piece'


def _traiter_messages(desk, row, prof, adaptateur, nav, epoch, acteur):
    from . import parcours56109 as P
    n = {'messages': 0, 'pieces': 0, 'classees': 0, 'questions': 0, 'dates': 0, 'brouillons_prepares': 0}
    restants = desk.db.execute("SELECT * FROM collecte_messages56109 WHERE mission=? AND etat NOT IN ('classe','hors_fenetre') ORDER BY recu_utc LIMIT ?",
                               (row['id'], MAX_MESSAGES)).fetchall()
    for m in restants:
        ctl = lambda: controle(nav, epoch, desk)       # noqa: E731
        ctl()
        if m['etat'] in ('inventorie', 'telechargement_a_verifier'):
            p = pilote(nav, epoch, desk)
            adaptateur.ouvrir(p, m['identifiant'])
            if str(adaptateur.message_affiche(p)) != m['identifiant']:
                raise Stop('message_affiche_different')
            desk.db.execute("UPDATE collecte_messages56109 SET effet_portail='lu_par_consultation', maj_le=? WHERE cle=?", (_maintenant(), m['cle']))
            desk.db.commit()
            nav.navigateur.cdp.drain()                  # événements antérieurs au clic : jamais associés à ce message
            adaptateur.exporter(pilote(nav, epoch, desk), m['identifiant'])
            try:
                nom, data = _attendre_telechargement(nav, ctl)
            except Suspendu:
                raise
            except Stop as ex:
                desk.db.execute("UPDATE collecte_messages56109 SET etat='telechargement_a_verifier', question=?, maj_le=? WHERE cle=?",
                                (str(ex).split(':')[0], _maintenant(), m['cle']))
                desk.db.commit()
                n['questions'] += 1
                continue
            entrees = P.lire_archive(data, ctl, max_octets=MAX_ARCHIVE)
            sha = hashlib.sha256(data).hexdigest()
            local = _zone_recuperee(desk) / (sha + '.zip')
            if not local.exists():
                local.write_bytes(data)
            quand = _maintenant()
            desk.db.execute('INSERT OR IGNORE INTO collecte_pieces56109(message,ordinal,origine,nom_source,sha256,taille,famille,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                            (m['cle'], -1, 'archive_originale', str(nom)[:200], sha, len(data), 'correspondances', quand))
            meta = json.loads(m['metadonnees_json'] or '{}')
            for x in entrees:
                nature = nature_du_nom(x['nom_source'], meta.get('natures'))
                famille = P.famille_document(nature, 'adverse' if nature == 'piece_adverse' else '')
                desk.db.execute('INSERT OR IGNORE INTO collecte_pieces56109(message,ordinal,origine,nom_source,sha256,taille,famille,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                                (m['cle'], x['ordinal'], 'archive', x['nom_source'][:300], x['sha256'], len(x['octets']), famille, quand))
                cible = _zone_recuperee(desk) / x['sha256']
                if not cible.exists():
                    cible.write_bytes(x['octets'])
            if getattr(adaptateur, 'copie_pdf', False):
                # copie de consultation EN COMPLÉMENT (jamais l'original) : identité du message vérifiée avant et après l'impression
                if str(adaptateur.message_affiche(p)) != m['identifiant']:
                    raise Stop('message_affiche_different')
                copie = p.pdf(ctl)
                if str(adaptateur.message_affiche(p)) != m['identifiant']:
                    raise Stop('navigation_pendant_impression')
                sha_copie = hashlib.sha256(copie).hexdigest()
                if not (_zone_recuperee(desk) / sha_copie).exists():
                    (_zone_recuperee(desk) / sha_copie).write_bytes(copie)
                desk.db.execute('INSERT OR IGNORE INTO collecte_pieces56109(message,ordinal,origine,nom_source,sha256,taille,famille,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                                (m['cle'], 999, 'copie_pdf', 'Copie de consultation de la page du message.pdf', sha_copie, len(copie), 'correspondances', quand))
            annoncees = meta.get('pieces_annoncees')
            ecart = '' if annoncees is None or int(annoncees) == len(entrees) else 'ecart_pieces_annoncees:%s/%s' % (len(entrees), annoncees)
            desk.db.execute("UPDATE collecte_messages56109 SET etat='telecharge', archive_sha256=?, archive_local=?, question=?, maj_le=? WHERE cle=?",
                            (sha, str(local), ecart, quand, m['cle']))
            desk.db.commit()
            n['pieces'] += len(entrees)
        n['messages'] += 1
        m = desk.db.execute('SELECT * FROM collecte_messages56109 WHERE cle=?', (m['cle'],)).fetchone()
        dossier = _resoudre(desk, m)
        if not dossier:
            n['questions'] += 1
            continue
        try:
            from . import acces5697
            acces5697.exiger(desk, (acteur, 'avocat') if acteur != 'cabinet' else ('cabinet', 'administrateur'), dossier_id=dossier[0])
        except Stop:
            desk.db.execute("UPDATE collecte_messages56109 SET etat='a_preciser', question='rattachement_a_preciser', maj_le=? WHERE cle=?", (_maintenant(), m['cle']))
            desk.db.commit()
            n['questions'] += 1
            continue
        ctl()
        n['classees'] += _classer(desk, m, dossier, ctl)
        n['dates'] += _dates(desk, m, dossier[0])
        n['brouillons_prepares'] += _preparer_brouillons(desk, m, dossier[0])
    return n


def _zone_recuperee(desk):
    """Zone privée du poste où restent les originaux d'un message non encore classé (aucune synchronisation, aucun partage)."""
    base = Path(desk.c['state_dir']) / 'collecte56109' / 'recuperes'
    base.mkdir(parents=True, exist_ok=True)
    return base


def candidats_dossier(desk, m):
    """Correspondances fournies par le REGISTRE (jamais par un nom ou un modèle) : correspondance confirmée du portail, sinon juridiction ET RG
    du dossier ; un RG seul donne un candidat non confirmé (question)."""
    out = []
    if m['portail_dossier']:
        r = desk.db.execute('SELECT dossier FROM collecte_correspondances56109 WHERE portail=? AND compte=? AND portail_dossier=?',
                            (m['portail'], m['compte'], m['portail_dossier'])).fetchone()
        if r:
            v = desk.db.execute('SELECT version FROM dossiers5697 WHERE id=?', (r['dossier'],)).fetchone()
            out.append({'dossier': r['dossier'], 'registre_version': str(v['version'] if v else 'correspondance'), 'correspondance_confirmee': True})
            return out
    if not m['rg']:
        return out
    try:
        rows = desk.db.execute("SELECT id, rg, juridiction, version FROM dossiers5697 WHERE rg<>''").fetchall()
    except Exception:   # noqa: BLE001 — registre des dossiers absent
        return out
    for r in rows:
        if _norm(r['rg']) != _norm(m['rg']):
            continue
        confirme = bool(m['juridiction']) and bool(r['juridiction']) and _norm(r['juridiction']) == _norm(m['juridiction'])
        if confirme or not m['juridiction'] or not r['juridiction']:
            out.append({'dossier': r['id'], 'registre_version': str(r['version']), 'correspondance_confirmee': confirme})
    return out


def _resoudre(desk, m):
    from .parcours56109 import dossier_unique
    try:
        dossier, version = dossier_unique(candidats_dossier(desk, m))
    except Stop as ex:
        desk.db.execute("UPDATE collecte_messages56109 SET etat='a_preciser', question=?, maj_le=? WHERE cle=?", (str(ex).split(':')[0], _maintenant(), m['cle']))
        desk.db.commit()
        return None
    desk.db.execute("UPDATE collecte_messages56109 SET dossier=?, dossier_version=?, maj_le=? WHERE cle=?", (dossier, version, _maintenant(), m['cle']))
    desk.db.commit()
    return dossier, version


def _chemin_dossier(desk, dossier):
    from .common import load_matters
    try:
        m = next((x for x in load_matters(desk.c) if str(x['id']) == dossier), None)
    except Stop:
        m = None
    if m and m.get('path'):
        return m['path']
    r = desk.db.execute('SELECT chemin FROM dossiers5697 WHERE id=?', (dossier,)).fetchone()
    if r and r['chemin']:
        return r['chemin']
    raise Stop('emplacement_dossier_absent')


def sous_dossier(desk, famille):
    from .docrequest520 import folders
    f = folders(desk)
    return {'correspondances': f.get('correspondance') or 'CORRESPONDANCES', 'procedure': f.get('procedure') or 'PROCEDURE',
            'pieces_adverses': 'PIECES ADVERSES', 'nos_pieces': 'NOS PIECES', 'a_classer': 'A CLASSER'}.get(famille, 'A CLASSER')


def _nom_sur(texte):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w .,()\'’-]+', ' ', str(texte or ''))).strip()[:90] or 'document'


def _classer(desk, m, dossier, ctl):
    """Dépôt exclusif de chaque pièce dans le dossier résolu, relu par empreinte ; reçu par pièce ; une pièce déjà vérifiée n'est jamais redéposée."""
    from .common import clean_path, under
    from .fichiers5670 import client
    racine = _chemin_dossier(desk, dossier[0])
    dav = client(desk)
    classees = 0
    restantes = desk.db.execute("SELECT * FROM collecte_pieces56109 WHERE message=? AND etat<>'verifiee' ORDER BY ordinal", (m['cle'],)).fetchall()
    for pc in restantes:
        ctl()
        local = _zone_recuperee(desk) / (pc['sha256'] + ('.zip' if pc['origine'] == 'archive_originale' else ''))
        if not local.is_file():
            desk.db.execute("UPDATE collecte_pieces56109 SET etat='a_verifier', maj_le=? WHERE message=? AND ordinal=? AND origine=?",
                            (_maintenant(), m['cle'], pc['ordinal'], pc['origine']))
            continue
        data = local.read_bytes()
        if hashlib.sha256(data).hexdigest() != pc['sha256']:
            raise Stop('original_modifie_dans_la_zone_recuperee')
        dossier_cible = clean_path('%s/%s' % (racine, sous_dossier(desk, pc['famille'])))
        if not under(dossier_cible, racine):
            raise Stop('destination_hors_dossier')
        dav.ensure_folder(dossier_cible, racine)
        jour = m['recu_utc'][:10]
        nom = '%s - %s - %s%s' % (jour, _nom_sur(m['identifiant'])[:30], '%02d ' % pc['ordinal'] if pc['ordinal'] >= 0 else '', _nom_sur(pc['nom_source']))
        chemin = _deposer(dav, clean_path(dossier_cible + '/' + nom), data)
        relu = dav.read_archive(chemin, MAX_ARCHIVE + 1)
        etat = 'verifiee' if hashlib.sha256(relu).hexdigest() == pc['sha256'] else 'a_verifier'
        desk.db.execute('UPDATE collecte_pieces56109 SET etat=?, dossier=?, chemin=?, recu_json=?, maj_le=? WHERE message=? AND ordinal=? AND origine=?',
                        (etat, dossier[0], chemin, json.dumps({'sha256': pc['sha256'], 'relu_le': _maintenant(), 'registre_version': dossier[1]}), _maintenant(),
                         m['cle'], pc['ordinal'], pc['origine']))
        desk.db.commit()
        classees += etat == 'verifiee'
    reste = desk.db.execute("SELECT COUNT(*) FROM collecte_pieces56109 WHERE message=? AND etat<>'verifiee'", (m['cle'],)).fetchone()[0]
    desk.db.execute('UPDATE collecte_messages56109 SET etat=?, maj_le=? WHERE cle=?', ('classe' if not reste else 'classement_a_verifier', _maintenant(), m['cle']))
    desk.db.commit()
    return classees


def _deposer(dav, chemin, data):
    base, ext = (chemin.rsplit('.', 1) + [''])[:2] if '.' in PurePosixPath(chemin).name else (chemin, '')
    for n in range(1, 6):
        cible = chemin if n == 1 else ('%s (%d).%s' % (base, n, ext) if ext else '%s (%d)' % (base, n))
        try:
            return dav.put_archive(cible, data, MAX_ARCHIVE, 'application/octet-stream')
        except Stop as ex:
            if str(ex).split(':')[0] != 'http_412' or n >= 5:
                raise
    raise Stop('depot_impossible')


def _dates(desk, m, dossier):
    """Dates explicites du message, sourcées, « à vérifier » ; un délai calculé sans règle validée reste « à préciser » (jamais inscrit)."""
    from .parcours56109 import plan_date
    meta = json.loads(m['metadonnees_json'] or '{}')
    n = 0
    for d in (meta.get('dates') or [])[:20]:
        try:
            plan = plan_date(str(d.get('date') or ''), str(d.get('nature') or ''), m['cle'], str(d.get('extrait') or '')[:300])
        except Stop:
            continue
        cle = _sha('date', m['cle'], dossier, plan['date'], d.get('nature'))[:32]
        cur = desk.db.execute('INSERT OR IGNORE INTO collecte_dates56109(cle,message,dossier,date,nature,extrait,etat,cree_le) VALUES(?,?,?,?,?,?,?,?)',
                              (cle, m['cle'], dossier, plan['date'], str(d.get('nature')), str(d.get('extrait') or '')[:300], plan['etat'], _maintenant()))
        n += cur.rowcount
    desk.db.commit()
    return n


def propositions_dates(desk, acteur, dossier):
    """Point de montage pour l'agenda (lots G2/G3) : dates proposées pour un dossier visible, avec source et état ; rien n'est inscrit ici."""
    from . import acces5697
    acces5697.exiger(desk, acteur, dossier_id=dossier)
    return [dict(r) for r in desk.db.execute('SELECT d.cle, d.date, d.nature, d.extrait, d.etat, m.identifiant source, m.objet FROM collecte_dates56109 d '
                                             'JOIN collecte_messages56109 m ON m.cle=d.message WHERE d.dossier=? ORDER BY d.date', (dossier,))]


# ------------------------------------------------------------------------------------------------ brouillons d'information
def destinataires_du_dossier(desk, dossier):
    """Relations confirmées au registre du dossier : client et avocat du client (dominus litis) ; jamais l'adverse, le greffe ni le cabinet."""
    rows = []
    try:
        for r in desk.db.execute("SELECT id, role, courriel FROM dossier_parties5697 WHERE dossier_id=? AND role IN ('client','avocat_client')", (dossier,)):
            rows.append({'id': r['id'], 'email': r['courriel'], 'role': 'client' if r['role'] == 'client' else 'dominus_litis',
                         'relation_confirmee': True, 'coordonnees_confirmees': bool(r['courriel'])})
    except Exception:   # noqa: BLE001 — registre des parties absent : aucun destinataire
        return []
    return rows


def _preparer_brouillons(desk, m, dossier):
    from .mailbox import own_addresses
    from .parcours56109 import destinataires_information
    try:
        dest = destinataires_information(destinataires_du_dossier(desk, dossier), own_addresses(desk.c['mail']))
    except Stop as ex:
        desk.db.execute('UPDATE collecte_messages56109 SET question=?, maj_le=? WHERE cle=?', (str(ex).split(':')[0], _maintenant(), m['cle']))
        desk.db.commit()
        return 0
    if not dest:
        desk.db.execute("UPDATE collecte_messages56109 SET question='destinataire_a_preciser', maj_le=? WHERE cle=?", (_maintenant(), m['cle']))
        desk.db.commit()
        return 0
    n = 0
    for d in dest:
        cle = _sha('information', m['cle'], dossier, d['email'].casefold(), 'message_procedural')
        objet = 'Information sur votre dossier : message reçu le %s' % m['recu_utc'][:10]
        if d['role'] == 'client':
            corps = ('Bonjour,\n\nNous avons reçu le %s, par la messagerie de la juridiction, un message concernant votre dossier (« %s »). Les documents '
                     'ont été classés dans votre dossier. Nous revenons vers vous après leur analyse.\n\nBien cordialement.' % (m['recu_utc'][:10], m['objet'][:160]))
        else:
            corps = ('Cher confrère,\n\nPour information, nous avons reçu le %s, par la messagerie de la juridiction, un message dans le dossier (« %s »). '
                     'Les pièces sont à votre disposition sur simple demande.\n\nBien confraternellement.' % (m['recu_utc'][:10], m['objet'][:160]))
        cur = desk.db.execute('INSERT OR IGNORE INTO collecte_brouillons56109(cle,message,dossier,contact_id,destinataire,role,objet,corps,mid,etat,cree_le,maj_le) '
                              'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (cle, m['cle'], dossier, d['contact_id'], d['email'], d['role'], objet, corps,
                                                                 '<axiorhub-%s@mail-agent.local>' % cle, 'prepare', _maintenant(), _maintenant()))
        n += cur.rowcount
    desk.db.commit()
    return n


def _message_brouillon(desk, b, owner):
    """Nouveau message ordinaire (pas une réponse au greffe, aucune copie reprise) ; même clé, même texte à chaque rendu."""
    from email.message import EmailMessage
    from email import policy
    from email.utils import format_datetime, formataddr
    from .sens5643 import check_recipients
    from .signature56102 import effective, poser
    cfg = desk.c['mail']
    check_recipients(b['destinataire'], (), cfg)
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = formataddr((cfg['from_name'], cfg['from_address']))
    msg['To'] = b['destinataire']
    msg['Subject'] = b['objet']
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = b['mid']
    msg['X-AxiorHub-Draft-Key'] = b['cle']
    poser(msg, b['corps'], effective(desk, owner))
    return msg


def deposer_brouillons(desk, acteur, role, mission_id=None, dossier=None):
    """Action DISTINCTE de l'avocat : dépôt dans Brouillons des projets préparés (jamais d'envoi) ; reçu ; incertain → à rapprocher et
    recherche de la même clé avant toute nouvelle création."""
    from . import acces5697
    from .pause5629 import checkpoint
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    sql, params = "SELECT b.* FROM collecte_brouillons56109 b JOIN collecte_messages56109 m ON m.cle=b.message WHERE b.etat IN ('prepare','a_rapprocher')", []
    if mission_id:
        sql += ' AND m.mission=?'
        params.append(mission_id)
    if dossier:
        sql += ' AND b.dossier=?'
        params.append(dossier)
    rows = desk.db.execute(sql, params).fetchall()
    if BOITE is not None:
        box = BOITE(desk.c['mail'])
    else:
        from .mailbox import Mailbox
        box = Mailbox(desk.c['mail'])
    bilan = {'deposes': 0, 'a_rapprocher': 0, 'refuses': 0, 'envoi': False}
    try:
        for b in rows:
            try:
                acces5697.exiger(desk, (acteur, role), dossier_id=b['dossier'])     # droits relus au moment de l'effet
            except Stop:
                bilan['refuses'] += 1
                continue
            msg = _message_brouillon(desk, b, acteur)
            if not box.find_own_draft(b['mid']):
                if b['etat'] == 'a_rapprocher':
                    bilan['a_rapprocher'] += 1               # résultat perdu : on ne recrée jamais à l'aveugle
                    continue
                checkpoint(desk)
                desk.db.execute("UPDATE collecte_brouillons56109 SET etat='a_rapprocher', maj_le=? WHERE cle=?", (_maintenant(), b['cle']))
                desk.db.commit()
                try:
                    box.append_draft(msg)
                except Exception as ex:   # noqa: BLE001
                    desk.db.execute('UPDATE collecte_brouillons56109 SET erreur=?, maj_le=? WHERE cle=?', (str(ex).split(':')[0][:80], _maintenant(), b['cle']))
                    desk.db.commit()
                    bilan['a_rapprocher'] += 1
                    continue
            try:
                recu = box.verify_draft(msg)
            except Exception as ex:   # noqa: BLE001
                desk.db.execute("UPDATE collecte_brouillons56109 SET etat='a_rapprocher', erreur=?, maj_le=? WHERE cle=?", (str(ex).split(':')[0][:80], _maintenant(), b['cle']))
                desk.db.commit()
                bilan['a_rapprocher'] += 1
                continue
            desk.db.execute("UPDATE collecte_brouillons56109 SET etat='depose', recu_json=?, erreur='', maj_le=? WHERE cle=?",
                            (json.dumps({**recu, 'envoi': False}), _maintenant(), b['cle']))
            desk.db.commit()
            bilan['deposes'] += 1
    finally:
        try:
            box.close()
        except Exception:   # noqa: BLE001
            pass
    return bilan


def confirmer_correspondance(desk, acteur, role, portail, compte, portail_dossier, dossier):
    """Réponse humaine à « rattachement à préciser » : correspondance portail → dossier confirmée (le message suivant s'y range seul)."""
    from . import acces5697
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    acces5697.exiger(desk, (acteur, role), dossier_id=dossier)
    if portail not in PORTAILS or not str(portail_dossier or '').strip():
        raise Stop('correspondance_invalide')
    desk.db.execute('INSERT OR REPLACE INTO collecte_correspondances56109(portail,compte,portail_dossier,dossier,confirme_par,confirme_le) VALUES(?,?,?,?,?,?)',
                    (portail, str(compte or ''), str(portail_dossier)[:120], dossier, acteur, _maintenant()))
    desk.db.execute("UPDATE collecte_messages56109 SET etat=CASE WHEN archive_sha256<>'' THEN 'telecharge' ELSE etat END, question='' "
                    "WHERE portail=? AND compte=? AND portail_dossier=? AND etat='a_preciser'", (portail, str(compte or ''), str(portail_dossier)[:120]))
    desk.db.commit()
    return {'ok': True, 'message': 'Correspondance enregistrée : les messages de ce dossier du portail seront classés au prochain passage.'}


def vue_mission(desk, mid):
    row = _mission(desk, mid)
    msgs = desk.db.execute('SELECT cle, identifiant, recu_utc, objet, etat, question, dossier, effet_portail FROM collecte_messages56109 WHERE mission=? '
                           'ORDER BY recu_utc', (row['id'],)).fetchall()
    return {'id': row['id'], 'etat': row['etat'], 'etat_libelle': ETATS_MISSION.get(row['etat'], row['etat']), 'motif': row['motif'],
            'portail': row['portail'], 'compte': row['compte'], 'mandant': row['mandant'], 'debut_utc': row['debut_utc'], 'fin_utc': row['fin_utc'],
            'couverture': row['couverture'], 'pages_lues': row['pages_lues'], 'compteurs': json.loads(row['compteurs_json'] or '{}'),
            'messages': [dict(m) for m in msgs],
            'brouillons': [dict(b) for b in desk.db.execute('SELECT b.cle, b.dossier, b.destinataire, b.role, b.etat FROM collecte_brouillons56109 b JOIN '
                                                            'collecte_messages56109 m ON m.cle=b.message WHERE m.mission=?', (row['id'],))],
            'version': row['version'], 'envoi': False}


# ------------------------------------------------------------------------------------------------ G12 : routine tous les trois jours
def creer_routine(desk, acteur, role, data):
    """« Relever mes messages procéduraux » : routine du type FERMÉ ``collecte_portail`` dans le registre existant (une par profil), créée par
    le programme ; tous les 3 jours calendaires (ancrage et heure locale choisis), rattrapage 72 h ; ACTIVE seulement si le profil est qualifié
    et que l'avocat le demande ; profil et version figés dans la définition."""
    from . import routines5652 as rt
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    prof = _profil(desk, data.get('profil'))
    rt.ensure_schema(desk)
    schedule = rt.normalize_schedule({'freq': 'intervalle', 'interval_days': data.get('interval_days') or 3, 'anchor': data.get('anchor'),
                                      'time': data.get('time') or '08:30'})
    activer = str(data.get('activer') or '').lower() in ('true', '1', 'on', 'oui') and prof['qualification'] == 'qualifie'
    rid = 'r109-collecte-' + _sha(prof['id'])[:16]
    perms = json.dumps({'mode': 'preparation', 'effets_externes': False, 'profil_portail_id': prof['id'], 'profil_version': prof['version'],
                        'fenetre_jours': FENETRE_JOURS, 'collecte': 'preparer_et_collecter'}, sort_keys=True)
    quand = _maintenant()
    old = desk.db.execute('SELECT * FROM routines5652 WHERE id=?', (rid,)).fetchone()
    if old:
        cur = desk.db.execute("UPDATE routines5652 SET schedule_json=?,permissions_json=?,enabled=?,deleted_at=NULL,version=version+1,updated_at=?,updated_by=?,"
                              "active_since=CASE WHEN ?=1 AND enabled=0 THEN ? ELSE active_since END WHERE id=? AND version=?",
                              (json.dumps(schedule, sort_keys=True), perms, int(activer), quand, acteur, int(activer), quand, rid, old['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        desk.db.execute("UPDATE routines5652_occ SET state='annulee',error_code='remplacee_par_nouvelle_version',updated=? WHERE routine_id=? "
                        "AND state IN ('reservee','a_reprogrammer','reportee_pause') AND job_id IS NULL", (quand, rid))
        action = 'modification'
    else:
        desk.db.execute('INSERT INTO routines5652(id,owner,scope,title,objective,instruction,task_type,matters_json,matter_scope,schedule_json,timezone,catchup_hours,'
                        'max_matters,enabled,version,permissions_json,legacy_kind,active_since,created_at,updated_at,updated_by,created_by) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',
                        (rid, 'cabinet', 'cabinet', 'Relever mes messages procéduraux', 'Collecte des non-lus des cinq derniers jours (%s)' % PORTAILS[prof['portail']],
                         '', 'collecte_portail', '[]', '', json.dumps(schedule, sort_keys=True), str(rt.cabinet_tz(desk)), 72, 50, int(activer), perms, '',
                         quand, quand, quand, acteur, acteur))
        action = 'creation'
    rt._log_version(desk, rt.get(desk, rid), acteur, action)
    desk.db.commit()
    return {'id': rid, 'active': activer, 'prochains': rt.upcoming(schedule, str(rt.cabinet_tz(desk)), count=3),
            'message': 'Routine enregistrée%s.' % ('' if activer else ' (désactivée : profil à qualifier ou activation non demandée)')}


def executer_routine(desk, oid, snap, result):
    """Exécuteur du type fermé ``collecte_portail`` (``routines5652.execute``) : lance UNE mission en mode « préparer et collecter » ; ne
    dépose aucun brouillon, ne confirme aucune date, n'envoie rien. Attente de connexion : signalée une fois, sans boucle ni doublon."""
    from . import routines5652 as rt
    perms = json.loads(snap.get('permissions_json') or '{}')
    try:
        prof = _profil(desk, perms.get('profil_portail_id'))
    except Stop:
        result['impossible'].append('profil de portail supprimé : routine à revoir')
        return ''
    if prof['version'] != perms.get('profil_version'):
        result['impossible'].append('profil de portail modifié depuis la programmation : routine à revoir (rien n’a été collecté)')
        return ''
    if prof['qualification'] != 'qualifie' or prof['portail'] not in ADAPTATEURS:
        result['impossible'].append('profil %s non qualifié : rien n’a été collecté' % PORTAILS.get(prof['portail'], prof['portail']))
        return ''
    attente = desk.db.execute("SELECT id, etat FROM collecte_missions56109 WHERE profil_id=? AND etat IN ('attente_connexion','en_cours','suspendue') "
                              'ORDER BY cree_le DESC LIMIT 1', (prof['id'],)).fetchone()
    if attente:
        result['a_decider'].append({'action': 'Ouvrir le navigateur, se connecter au portail puis reprendre la collecte', 'mission': attente['id']})
        result['impossible'].append('collecte précédente %s : aucune nouvelle mission lancée' % ETATS_MISSION.get(attente['etat'], attente['etat']).lower())
        return attente['id']
    rt.checkpoint(desk, oid)
    debut, _ = fenetre(desk)
    derniere = desk.db.execute("SELECT fin_utc FROM collecte_missions56109 WHERE profil_id=? AND etat IN ('terminee','partielle') ORDER BY fin_utc DESC LIMIT 1",
                               (prof['id'],)).fetchone()
    if derniere and derniere['fin_utc'] < debut:
        result['impossible'].append('période non couverte du %s au %s : rattrapage ciblé à demander' % (derniere['fin_utc'][:10], debut[:10]))
    row = lancer(desk, 'cabinet', 'administrateur', {'cle': 'routine-' + _sha(oid)[:40], 'profil': prof['id']}, occurrence=oid)
    vue = executer(desk, row['id'], 'cabinet')
    c = vue['compteurs']
    if vue['etat'] == 'attente_connexion':
        result['a_decider'].append({'action': 'Ouvrir le navigateur et se connecter au portail (authentification par l’avocat)', 'mission': row['id']})
        result['impossible'].append('en attente de connexion : rien n’a été collecté')
    elif vue['etat'] in ('terminee', 'partielle'):
        result['produit'].append('%d message(s) collecté(s), %d pièce(s) classée(s), %d date(s) à vérifier, %d brouillon(s) préparé(s) (non déposés)' % (
            c.get('messages', 0), c.get('classees', 0), c.get('dates', 0), c.get('brouillons_prepares', 0)))
        if vue['couverture'] != 'complete':
            result['impossible'].append('couverture partielle (%s) : ce n’est pas une boîte vide' % (vue['motif'] or 'inventaire incomplet'))
        if c.get('questions'):
            result['a_decider'].append({'action': '%d message(s) à rattacher ou à vérifier' % c['questions'], 'mission': row['id']})
    else:
        result['impossible'].append('collecte : %s' % vue['etat_libelle'])
    return row['id']


# ------------------------------------------------------------------------------------------------ cartes (Mes routines | Mes agents IA)
def carte_html(desk, prefix, role):
    from html import escape as e
    import uuid
    lignes = []                                       # tables créées au démarrage (``Desk._prepare``) : aucune écriture pendant l'affichage
    for p in profils(desk):
        mission = desk.db.execute('SELECT id FROM collecte_missions56109 WHERE profil_id=? ORDER BY cree_le DESC LIMIT 1', (p['id'],)).fetchone()
        v = vue_mission(desk, mission['id']) if mission else None
        etat = ('%s — couverture %s ; %d message(s), %d brouillon(s) préparé(s)' % (v['etat_libelle'], v['couverture'], len(v['messages']),
                                                                                  sum(1 for b in v['brouillons'] if b['etat'] == 'prepare'))) if v else 'Aucune collecte.'
        actions = ''
        if role in ('administrateur', 'avocat'):
            actions = ('<form class="m5-form r52-inline" data-api="m56109/parcours/collecte-lancer" data-reload><input type="hidden" name="profil" value="%s">'
                       '<input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit"%s>Lancer la collecte</button></form>'
                       '<form class="m5-form r52-inline" data-api="m56109/parcours/collecte-deposer" data-reload><input type="hidden" name="cle" value="%s">'
                       '<button class="ax-btn ghost" type="submit">Déposer dans Brouillons les projets préparés</button></form>'
                       '<details class="r52-edit"><summary class="ax-btn ghost">Programmer tous les 3 jours</summary><form class="m5-form" data-api="m56109/parcours/collecte-routine" '
                       'data-reload><input type="hidden" name="profil" value="%s"><label>Premier jour<input name="anchor" type="date" required></label>'
                       '<label>Heure<input name="time" type="time" value="08:30" required></label><label class="r52-day"><input type="checkbox" name="activer"%s> Activer '
                       '(profil qualifié seulement)</label><button class="ax-btn" type="submit">Enregistrer la routine</button><p role="status" class="m5-status"></p>'
                       '</form></details>') % (e(p['id'], quote=True), uuid.uuid4().hex, '' if p['qualification'] == 'qualifie' else ' disabled', uuid.uuid4().hex,
                                               e(p['id'], quote=True), '' if p['qualification'] == 'qualifie' else ' disabled')
        lignes.append('<li><strong>%s</strong> · %s%s<br><span class="r52-meta">%s</span>%s</li>' % (
            e(p['libelle']), e(p['qualification_libelle']), (' (manque : %s)' % e(', '.join(p['manques']))) if p['manques'] else '', e(etat), actions))
    ajout = ''
    if role == 'administrateur':
        ajout = ('<details class="r52-add"><summary class="ax-btn ghost">Déclarer un profil de portail</summary><form class="m5-form" data-api="m56109/parcours/collecte-profil" '
                 'data-reload><label>Portail<select name="portail"><option value="ebarreau">e-Barreau (CNB)</option><option value="portalis">Portalis</option></select></label>'
                 '<label>Adresse officielle d’entrée<input name="url_entree" maxlength="300"></label><label>Domaines nécessaires (un par ligne)<textarea name="domaines" rows="2"></textarea></label>'
                 '<label>Système<input name="systeme" maxlength="60"></label><label>Navigateur<input name="navigateur" maxlength="60"></label>'
                 '<label>Compte professionnel<input name="compte" maxlength="200"></label><label>Avocat représenté<input name="avocat" maxlength="200"></label>'
                 '<label>Délégation<input name="delegation" maxlength="200"></label><label>Type de compte<select name="type_compte"><option value="">À identifier</option>'
                 '<option value="professionnel">Professionnel</option></select></label><button class="ax-btn" type="submit">Enregistrer le profil</button>'
                 '<p role="status" class="m5-status"></p></form></details>')
    return ('<div class="c5614-routine r52-card g4-carte" id="g4-collecte"><div class="r52-head"><strong>Relever mes messages procéduraux</strong></div>'
            '<span class="r52-meta">Consultation supervisée sur ce poste : vous vous connectez vous-même ; aucun envoi ni dépôt RPVA, aucune API du portail. '
            'Un profil non qualifié ne collecte rien.</span><ul class="g4-liste">%s</ul>%s</div>') % (
        ''.join(lignes) or '<li class="ax-muted">Aucun profil de portail déclaré.</li>', ajout)


def monter_cartes(desk, prefix, owner, role, compact, cards, strip):
    """Point de montage de ``web5652.routines_block`` : cartes du lot G4 (collecte, offre, documents publics Pappers)."""
    try:
        from . import offre56109, pappers_documents56109
        cards.append(carte_html(desk, prefix, role))
        cards.append(offre56109.carte_html(desk, prefix, role))
        cards.append(pappers_documents56109.carte_html(desk, prefix, role))
    except Exception as ex:   # noqa: BLE001 — une carte en panne ne retire pas les autres routines ; le motif est tracé
        desk.audit('g4_cartes_indisponibles', {'cause': type(ex).__name__})
