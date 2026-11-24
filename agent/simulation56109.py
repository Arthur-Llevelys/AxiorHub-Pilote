"""5.6.109 (lot N, F14) : onglet « Simulation de décision » du dossier — cadrage, appel, quatre actions, recherche contradictoire.

Raccordé aux services existants, sans second registre ni nouvelle page de menu :
- **onglet du dossier** (``/dossier?id=…&vue=simulation``, point de montage unique dans ``dossier5679``) : dossiers de contentieux et de
  postulation seulement (profil de la fiche ``projection56109``, nature du registre, avocat postulant inscrit ou rôle confirmé au cadrage) ;
  quatre actions dans le même onglet (``action=en_etat|hypothese|contrer|ameliorer``) ; une action inconnue est une erreur dite ;
- **instantané** : la projection de la fiche (faits, allégations, demandes, décisions, décisions du cabinet, chacun avec ses sources et sa
  version) et le **cadrage confirmé par le cabinet** (question, stade, rôle, côté du client, jugement, déclaration d'appel, chefs critiqués,
  demandes examinées). L'empreinte (``empreinte``) couvre tout : une nouvelle pièce, une correction ou un autre jugement rend « À actualiser » ;
- **appel** : le jugement est un document IDENTIFIÉ du dossier (nature décision, version, empreinte du texte lu, couverture, dispositif) ; sans
  lui, sans déclaration d'appel ni chefs validés, ``cadrer`` bloque : « Analyse préparatoire », aucun appel au modèle ni recherche ;
- **file existante** : un POST protégé (Origin, CSRF, corps borné, révision attendue, clé d'idempotence) crée un traitement
  ``simulation_runs56109`` et met en file le travail EXISTANT ``prepare_legal_opinion`` (argument ``simulation56109``) : pause, annulation,
  budget, politique des données, reprise et verrous du moteur commun ; aucune boucle propre. ``executer`` contrôle (``checkpoint``) avant et
  après chaque étape coûteuse ; un arrêt central remonte tel quel ; une annulation gagne sur une écriture tardive (mise à jour conditionnelle) ;
- **recherche contradictoire** : ``legal_research.research`` (connecteurs du registre commun, sélection ``selection_sources5697`` : ``None`` =
  sources retenues par défaut, liste vide = aucune), requêtes anonymisées dans les deux sens ; chaque piste est revérifiée sur un texte
  officiel ; SEULE une décision vérifiée entre dans le paquet citable (``_citable``) ; une piste reste « Piste à vérifier » ;
- **rédaction** : ``opinions.preparer_avec_paquet`` (stockage d'avis existant, contrôle déterministe, contrôle indépendant, compétence
  « Assistant juridique » par défaut et complément ``metier109-simulation-decision`` s'il est activé, par la sélection du travail) ;
- **pourcentages** : aucun nombre du modèle n'est affiché (``_sans_pourcentage`` à chaque sortie ; projet bloqué par le contrôle) ; la voie
  statistique (``panneau_probabilites``) n'est ouverte que par un artefact du registre homologué du SERVEUR (``REGISTRE_HOMOLOGUE``) : aucun
  n'est livré, elle est fermée et l'écran le dit ; la fréquence descriptive existante (``assistance35.comparables``) n'est montrée qu'avec son
  dénominateur et au moins dix décisions qualifiées ; niveaux qualitatifs motivés seulement (Position étayée, discutée, Éléments insuffisants) ;
- **hypothèse** : copie dérivée de l'instantané de la simulation de base (``preparer``) ; pièces, faits et demandes réels jamais modifiés ;
  sources figées du scénario de base ; écarts expliqués ;
- **droits** : dossier absent ou réservé : même 404 neutre avant tout compteur ou extrait ; contrôle illisible : 503 ; préparer, annuler,
  corriger, conserver : avocat et administrateur (403 pour l'assistant) ; droits relus par le travail avant d'écrire le résultat ;
- **IA en pause** : consultation, correction et conservation restent possibles (aucun appel au modèle dans une lecture) ; une demande
  attend la reprise. Résultat conservé comme livrable : ``artefacts56109`` (registre commun), jamais un second registre.

Les fonctions ``empreinte``, ``cadrer``, ``dedoublonner_decisions``, ``panneau_probabilites``, ``brier_multiclasse``, ``preparer`` et
``tableau_issues`` reprennent les noyaux du dossier technique (``reference/simulation_decision.py``, ``reference/atelier_simulation109.py``).
La pertinence juridique réelle d'une simulation n'est pas évaluée par ce code : c'est un projet interne à relire par l'avocat.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from html import escape as e
import json
import math
import re
import sqlite3
import uuid
from urllib.parse import urlencode

from .common import Stop
from . import acces5697

PREFIXE = 'm56109/simulation/'
MODES = ('en_etat', 'hypothese', 'contrer', 'ameliorer')
LIBELLES = {'en_etat': 'Simulation en l’état du dossier', 'hypothese': 'Tester une hypothèse',
            'contrer': 'Contrer l’argumentation adverse', 'ameliorer': 'Quelle action améliorerait le dossier ?'}
ONGLETS = (('en_etat', 'En l’état du dossier'), ('hypothese', 'Tester une hypothèse'), ('contrer', 'Contrer l’argumentation adverse'),
           ('ameliorer', 'Améliorer le dossier'))
ETATS = {'queued': 'En file', 'running': 'En cours', 'needs_information': 'Analyse préparatoire : informations à compléter',
         'partial': 'Résultat partiel', 'completed': 'Terminée', 'blocked': 'Bloquée par le contrôle', 'cancelled': 'Annulée', 'failed': 'Échec'}
TERMINES = ('needs_information', 'partial', 'completed', 'blocked', 'cancelled', 'failed')
AVEC_RESULTAT = ('partial', 'completed', 'blocked')
NIVEAUX = {'etayee': 'Position étayée', 'discutee': 'Position discutée', 'insuffisant': 'Éléments insuffisants'}
STADES = {'premiere_instance': 'Première instance', 'refere': 'Référé', 'appel': 'Appel', 'cassation': 'Cassation', 'renvoi': 'Renvoi après cassation',
          'execution': 'Exécution', 'autre': 'Autre'}
ROLES_CABINET = {'': 'À confirmer', 'principal': 'Avocat du client (au fond)', 'postulant': 'Postulation'}
COTES = {'': 'À préciser', 'demandeur': 'Demandeur (ou appelant)', 'defendeur': 'Défendeur (ou intimé)'}
NATURES_DECISION = ('jugement', 'ordonnance', 'arret')
QUESTIONS = {'nature_contentieuse_non_confirmee': 'Confirmer qu’il s’agit d’un contentieux ou d’une postulation.',
             'demandes_a_preciser': 'Aucune demande examinée : vérifiez les demandes de la fiche du dossier.',
             'stade_a_preciser': 'Préciser le stade de la procédure.',
             'demande_incomplete': 'Une demande examinée n’a pas d’identifiant, de titulaire ou d’objet.',
             'jugement_complet_identifie_requis': 'Identifier le jugement ou l’ordonnance de première instance (document du dossier, texte lu en entier).',
             'chefs_contestes_a_valider': 'Valider les chefs du jugement critiqués.',
             'declaration_appel_a_identifier': 'Identifier la déclaration d’appel.',
             'jugement_modifie_depuis_le_cadrage': 'Le jugement a changé depuis le cadrage : identifiez-le à nouveau.',
             'sources_dossier_absentes': 'Aucun fait ni aucune demande sourcés dans la fiche : complétez la fiche du dossier.',
             'jurisprudence_officielle_verifiee_absente': 'Aucune décision vérifiée sur un texte officiel : la simulation n’est pas rédigée (les pistes restent à vérifier).'}
# Registre des artefacts statistiques HOMOLOGUÉS, construit côté serveur depuis des fichiers de confiance vérifiés (empreintes du modèle, du
# calibrateur et du rapport, licence du corpus, approbation humaine). Aucun n'est livré : la voie statistique est fermée. Jamais alimenté par
# un client, un connecteur de recherche ou un modèle de langage.
REGISTRE_HOMOLOGUE = ()
MESSAGE_STATISTIQUE = ('Estimation statistique : la voie statistique est fermée. Aucun modèle statistique validé n’est installé pour ce '
                       'type de demande ; aucun pourcentage n’est affiché. Les niveaux ci-dessous sont des appréciations motivées, pas des chances de succès.')
MASQUE = '[estimation chiffrée masquée : aucun modèle statistique validé]'
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
IDENT = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')
RUN = re.compile(r'^[0-9a-f]{32}$')
ROLES = ('administrateur', 'avocat', 'assistant')
ROLES_ACTION = ('administrateur', 'avocat')
ECRITURES_RUN = ('annuler', 'corriger', 'conserver')
MAX_MODIFICATIONS = 30
MAX_TEXTE_JUGEMENT = 50000

SCHEMA = '''
CREATE TABLE IF NOT EXISTS simulation_runs56109(
  id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, matter_id TEXT NOT NULL, actor TEXT NOT NULL, operation_key TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('en_etat','hypothese','contrer','ameliorer')), base_run_id TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  state TEXT NOT NULL CHECK(state IN ('queued','running','needs_information','partial','completed','blocked','cancelled','failed')),
  cadrage_version INTEGER NOT NULL DEFAULT 0, snapshot_sha256 TEXT NOT NULL, scenario_sha256 TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL,
  modifications_json TEXT NOT NULL DEFAULT '[]', parameters_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}',
  sources_json TEXT NOT NULL DEFAULT '[]', job_id INTEGER, opinion_project_id TEXT NOT NULL DEFAULT '', livrable_id TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(cabinet_id, actor, operation_key));
CREATE INDEX IF NOT EXISTS simulation_runs56109_dossier ON simulation_runs56109(matter_id, updated DESC);
CREATE TABLE IF NOT EXISTS simulation_cadrages56109(
  matter_id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version >= 1), data_json TEXT NOT NULL, modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS simulation_corrections56109(
  run_id TEXT NOT NULL, matter_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1), texte TEXT NOT NULL, par TEXT NOT NULL, le TEXT NOT NULL,
  PRIMARY KEY(run_id, version));
CREATE TABLE IF NOT EXISTS simulation_bruts56109(
  run_id TEXT PRIMARY KEY, matter_id TEXT NOT NULL, result_brut_json TEXT NOT NULL, ecrit_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS simulation_ops56109(
  acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}',
  cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
TABLES = ('simulation_runs56109', 'simulation_cadrages56109', 'simulation_corrections56109', 'simulation_ops56109', 'simulation_bruts56109')
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'acteur_requis': '403 Forbidden', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict',
           'operation_deja_terminee': '409 Conflict', 'simulation_non_terminee': '409 Conflict', 'simulation_de_base_requise': '409 Conflict',
           'conflit_a_examiner': '409 Conflict', 'dossier_decline_conflit': '409 Conflict', 'simulation_reservee_au_contentieux': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable',
           'file_attente_pleine': '503 Service Unavailable', 'registre_occupe': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Dossier ou simulation introuvable.',
    'route_inconnue': 'Route inconnue.',
    'role_insuffisant': 'Votre rôle ne permet pas de préparer, annuler, corriger ou conserver une simulation.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'version_perimee': 'Le cadrage a changé depuis votre affichage : rechargez la page. Rien n’a été lancé ni écrasé.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours : patientez puis rechargez la page.',
    'operation_deja_terminee': 'La simulation est déjà terminée : rien à annuler.',
    'simulation_non_terminee': 'La simulation n’a pas encore de résultat à conserver.',
    'simulation_de_base_requise': 'Une hypothèse se compare à une simulation en l’état déjà terminée : lancez-la d’abord.',
    'simulation_reservee_au_contentieux': 'La simulation de décision est réservée aux dossiers de contentieux et de postulation.',
    'mode_inconnu': 'Action de simulation inconnue.',
    'jugement_non_identifie': 'Ce document n’est pas une décision de ce dossier : choisissez le jugement ou l’ordonnance dans la liste.',
    'declaration_non_identifiee': 'Ce document n’appartient pas à ce dossier.',
    'champ_invalide': 'Certains champs sont à corriger.',
    'texte_requis': 'Saisissez le texte corrigé.',
    'conflit_a_examiner': 'La recherche de conflits de ce dossier n’a pas été examinée : la préparation est suspendue.',
    'dossier_decline_conflit': 'Dossier décliné pour conflit d’intérêts : aucune préparation.',
    'requete_trop_longue': 'Demande trop longue.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant. Rien n’a été affiché ni modifié.',
    'file_attente_pleine': 'La file des travaux est pleine : réessayez dans un moment.',
    'registre_occupe': 'Base occupée : réessayez dans un instant.',
    'document_inconnu': 'Pièce inconnue dans ce dossier.', 'fait_inconnu': 'Fait inconnu dans ce dossier.', 'demande_inconnue': 'Demande inconnue dans ce dossier.',
    'hypothese_vide': 'Cochez au moins une modification pour tester une hypothèse.', 'montant_invalide': 'Montant invalide.',
    'champs_demande_refuses': 'Seuls le libellé, le montant et les fondements d’une demande peuvent varier dans une hypothèse.',
}


# ------------------------------------------------------------------------------------------------ schéma et outils
def ensure_schema(desk):
    """Démarrage (``Desk._prepare``) seulement ; sans changement, aucune écriture (jamais pendant une lecture)."""
    noms = re.findall(r'CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute('SELECT name FROM sqlite_master')}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*morceaux):
    return hashlib.sha256('|'.join(str(m) for m in morceaux).encode('utf-8')).hexdigest()


def _ident(genre, *morceaux):
    return genre + '-' + _sha(*morceaux)[:16]


def _lignes(desk, sql, params=()):
    """Lecture tolérante d'un registre voisin : table absente = aucune ligne."""
    try:
        return desk.db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _int(valeur, defaut=None):
    if isinstance(valeur, bool):
        return defaut
    try:
        return int(str(valeur))
    except (TypeError, ValueError):
        return defaut


def _cabinet(desk):
    try:
        from .cabinet_profile5689 import cabinet_id
        return str(cabinet_id(desk))
    except Exception:   # noqa: BLE001 — profil illisible : un seul cabinet par base
        return 'cabinet'


def _jour(valeur):
    """Date et heure lisibles, au fuseau du cabinet (jamais l'heure du serveur)."""
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromisoformat(str(valeur).replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Paris'))
        return d.strftime('%d/%m/%Y à %H h %M')
    except (ValueError, TypeError, KeyError):
        return ''


def _sans_pourcentage(valeur):
    """Aucun nombre présenté comme une chance ou une probabilité n'est restitué depuis un texte du modèle (même motif que le contrôle
    déterministe de ``opinions``). Appliqué à CHAQUE sortie (JSON, écran, livrable) ; la donnée brute reste dans le projet d'avis bloqué."""
    from .opinions import NUMERIC_PROBABILITY
    if isinstance(valeur, str):
        return NUMERIC_PROBABILITY.sub(MASQUE, valeur)
    if isinstance(valeur, list):
        return [_sans_pourcentage(x) for x in valeur]
    if isinstance(valeur, dict):
        # extraits officiels vérifiés (citations exactes) et textes du logiciel : jamais réécrits ; tout le reste vient du modèle
        return {k: (v if k in ('comparaison', 'statistique', 'frequence') else _sans_pourcentage(v)) for k, v in valeur.items()}
    return valeur


# ------------------------------------------------------------------------------------------------ noyaux repris du dossier technique
def empreinte(contexte):
    """Instantané déterministe incluant versions des pièces ET des choix humains."""
    raw = json.dumps(contexte, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def cadrer(dossier):
    """Un appel incomplet permet la recherche préparatoire, pas une simulation finale."""
    manques = []
    if dossier.get('nature') not in ('contentieux', 'postulation'):
        manques.append('nature_contentieuse_non_confirmee')
    if not dossier.get('demandes'):
        manques.append('demandes_a_preciser')
    if not dossier.get('stade'):
        manques.append('stade_a_preciser')
    for demande in dossier.get('demandes', []):
        if not demande.get('id') or not demande.get('titulaire_id') or not demande.get('objet'):
            manques.append('demande_incomplete')
    if dossier.get('stade') == 'appel':
        jugement = dossier.get('jugement') or {}
        if (jugement.get('nature') != 'decision_premiere_instance' or not jugement.get('source_id') or not jugement.get('sha256')
                or not jugement.get('version') or jugement.get('complet') is not True):
            manques.append('jugement_complet_identifie_requis')
        if not dossier.get('chefs_contestes_valides'):
            manques.append('chefs_contestes_a_valider')
        if not dossier.get('declaration_appel_source_id'):
            manques.append('declaration_appel_a_identifier')
    return {'etat': 'pret' if not manques else 'a_completer', 'simulation_finale_autorisee': not manques, 'recherche_preparatoire_autorisee': True,
            'questions': sorted(set(manques)), 'empreinte': empreinte(dossier)}


def dedoublonner_decisions(decisions):
    """Même décision chez plusieurs fournisseurs : une ligne, toutes les provenances. Sans clé canonique : piste distincte non comptable.
    Une même affaire à deux degrés reste deux décisions."""
    par_cle, pistes = {}, []
    for decision in decisions:
        d = deepcopy(decision)
        cle = d.get('decision_canonique_id')
        if not cle:
            d['comptable'] = False
            pistes.append(d)
            continue
        if cle not in par_cle:
            par_cle[cle] = d
            d['provenances'] = list(d.get('provenances', []))
        else:
            ancien = par_cle[cle]
            if ancien.get('texte_sha256') and d.get('texte_sha256') and ancien['texte_sha256'] != d['texte_sha256']:
                ancien['versions_a_rapprocher'] = True
                ancien['comptable'] = False
            for source in d.get('provenances', []):
                if source not in ancien['provenances']:
                    ancien['provenances'].append(source)
    return list(par_cle.values()) + pistes


def _date(value):
    date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('date_sans_fuseau')
    return date.astimezone(timezone.utc)


def _absent(raison):
    return {'etat': 'non_estimable', 'libelle': 'Non estimable de façon fiable', 'raison': raison, 'probabilites': None}


def panneau_probabilites(contexte, *, registre_serveur, inferer, maintenant, checkpoint=lambda: None):
    """Façade de contrôle d'un FUTUR service statistique, pas un prédicteur. Le contexte ne fournit jamais l'artefact ni des probabilités ; le
    serveur choisit un artefact homologué pour UNE cible (une demande, des issues exhaustives et exclusives). Exceptions d'arrêt propagées."""
    checkpoint()
    if cadrer(contexte)['etat'] != 'pret':
        return _absent('cadrage_incomplet')
    if contexte.get('couverture_dossier_complete') is not True:
        return _absent('couverture_incomplete')
    cible = contexte.get('cible_statistique') or {}
    if cible.get('demande_id') not in {x['id'] for x in contexte['demandes']}:
        return _absent('demande_statistique_inconnue')
    axes = ('pays', 'domaine', 'stade', 'regime', 'question_code')
    if any(not contexte.get(k) for k in axes):
        return _absent('perimetre_incomplet')
    if not cible.get('issues') or len(set(cible['issues'])) != len(cible['issues']):
        return _absent('issues_invalides')
    correspondants = [a for a in registre_serveur if all(a.get(k) == contexte[k] for k in axes) and a.get('issues') == cible['issues']]
    if len(correspondants) != 1 or inferer is None:
        return _absent('aucun_artefact_unique_applicable')
    a = correspondants[0]
    requis = ('id', 'modele_sha256', 'rapport_sha256', 'calibration_sha256', 'dataset_version', 'evaluation_temporelle_id', 'population',
              'politique_validation_id', 'valide_jusquau')
    if any(not a.get(k) for k in requis) or a.get('homologue') is not True:
        return _absent('artefact_non_homologue')
    if _date(maintenant) >= _date(a['valide_jusquau']):
        return _absent('homologation_expiree')
    # Variables catégorielles d'un vocabulaire fermé homologué : aucun texte, aucun nom de magistrat ni de partie (COJ, art. L111-13).
    schema = a.get('variables_autorisees')
    variables = contexte.get('variables_statistiques') or {}
    if not isinstance(schema, dict) or set(variables) != set(schema):
        return _absent('variables_non_homologuees')
    if any(not isinstance(v, str) or v not in schema[k] for k, v in variables.items()):
        return _absent('valeur_statistique_non_homologuee')
    paquet = {'artefact_id': a['id'], 'empreinte_contexte': empreinte(contexte), 'variables': deepcopy(variables), 'demande_id': cible['demande_id']}
    checkpoint()
    r = inferer(deepcopy(a), paquet)
    checkpoint()
    if r.get('empreinte_contexte') != empreinte(contexte) or r.get('artefact_id') != a['id']:
        return _absent('inference_non_rattachee')
    if r.get('applicable') is not True or not r.get('controle_domaine_id'):
        return _absent('hors_domaine_ou_controle_manquant')
    valeurs = r.get('probabilites')
    if not isinstance(valeurs, dict) or set(valeurs) != set(cible['issues']):
        return _absent('issues_non_exhaustives')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in valeurs.values()):
        return _absent('probabilites_invalides')
    if not math.isclose(sum(valeurs.values()), 1.0, rel_tol=0, abs_tol=1e-6):
        return _absent('distribution_incoherente')
    return {'etat': 'estimation_statistique', 'demande_id': cible['demande_id'], 'probabilites': deepcopy(valeurs), 'artefact_id': a['id'],
            'rapport_sha256': a['rapport_sha256'], 'population': a['population'], 'dataset_version': a['dataset_version'],
            'controle_domaine_id': r['controle_domaine_id'], 'empreinte_contexte': r['empreinte_contexte'],
            'libelle': 'Estimation statistique conditionnelle — à relire'}


def brier_multiclasse(observations, issues):
    """Score moyen non normalisé, 0 à 2 ; pas une preuve de calibration suffisante (jeu temporel tenu à l'écart)."""
    if not observations or len(set(issues)) != len(issues) or len(issues) < 2:
        raise ValueError('evaluation_invalide')
    scores = []
    for obs in observations:
        p = obs['probabilites']
        if set(p) != set(issues) or obs['issue_observee'] not in issues:
            raise ValueError('issues_invalides')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()):
            raise ValueError('probabilites_invalides')
        if not math.isclose(sum(p.values()), 1.0, rel_tol=0, abs_tol=1e-6):
            raise ValueError('distribution_incoherente')
        scores.append(sum((p[k] - int(k == obs['issue_observee'])) ** 2 for k in issues))
    return sum(scores) / len(scores)


def _index(rows, kind):
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] or r['id'] in out:
            raise ValueError(kind + '_identifiants_invalides')
        out[r['id']] = r
    return out


def preparer(snapshot, mode, modifications=None):
    """Scénario dérivé et ses écarts, sur une COPIE de l'instantané déjà autorisé par le serveur (identifiants stables, jamais un chemin)."""
    if mode not in MODES:
        raise ValueError('mode_simulation_inconnu')
    modifications = [] if modifications is None else modifications
    if not isinstance(modifications, list) or len(modifications) > MAX_MODIFICATIONS:
        raise ValueError('modifications_invalides')
    if modifications and mode != 'hypothese':
        raise ValueError('modifications_reservees_au_scenario')
    if not snapshot.get('matter_id') or type(snapshot.get('revision')) is not int or snapshot['revision'] < 1:
        raise ValueError('snapshot_non_versionne')
    base_hash = empreinte(snapshot)
    copie = deepcopy(snapshot)
    documents = _index(copie.setdefault('documents', []), 'documents')
    faits = _index(copie.setdefault('faits', []), 'faits')
    demandes = _index(copie.setdefault('demandes', []), 'demandes')
    sources = _index(copie.setdefault('sources', []), 'sources')
    if any(s.get('document_id') and s['document_id'] not in documents for s in sources.values()):
        raise ValueError('source_document_inconnu')
    if any(set(f.get('source_ids') or []) - set(sources) for f in faits.values()):
        raise ValueError('fait_source_inconnue')
    ecarts, deja = [], set()
    for m in modifications:
        if not isinstance(m, dict) or set(m) - {'operation', 'id', 'valeurs'}:
            raise ValueError('modification_invalide')
        op, ident = m.get('operation'), m.get('id')
        if not isinstance(op, str) or not isinstance(ident, str):
            raise ValueError('identifiant_modification_invalide')
        if (op, ident) in deja:
            raise ValueError('modification_dupliquee')
        deja.add((op, ident))
        if op == 'retirer_piece':
            if ident not in documents:
                raise ValueError('document_inconnu')
            avant = deepcopy(documents[ident])
            documents[ident]['incluse_scenario'] = False
            exclus = {sid for sid, s in sources.items() if s.get('document_id') == ident}
            for sid in exclus:
                sources[sid]['incluse_scenario'] = False
            for fait in list(faits.values()) + list(demandes.values()):
                ids = set(fait.get('source_ids') or [])
                if ids & exclus:
                    fait['sources_exclues_scenario'] = sorted(ids & exclus)
                    fait['preuve_scenario'] = 'a_reexaminer'
            ecarts.append({'operation': op, 'id': ident, 'avant': avant, 'apres': deepcopy(documents[ident]), 'source_ids_exclus': sorted(exclus)})
        elif op == 'fait_non_etabli':
            if ident not in faits:
                raise ValueError('fait_inconnu')
            avant = deepcopy(faits[ident])
            faits[ident]['statut_reference'] = faits[ident].get('statut')
            faits[ident]['statut'] = 'non_etabli_par_hypothese'
            ecarts.append({'operation': op, 'id': ident, 'avant': avant, 'apres': deepcopy(faits[ident])})
        elif op == 'modifier_demande':
            if ident not in demandes:
                raise ValueError('demande_inconnue')
            valeurs = m.get('valeurs')
            if not isinstance(valeurs, dict) or not valeurs or set(valeurs) - {'libelle', 'montant', 'fondements'}:
                raise ValueError('champs_demande_refuses')
            if 'montant' in valeurs:
                try:
                    n = Decimal(str(valeurs['montant']))
                except (InvalidOperation, TypeError):
                    raise ValueError('montant_invalide') from None
                if isinstance(valeurs['montant'], bool) or not n.is_finite() or n < 0:
                    raise ValueError('montant_invalide')
            if 'libelle' in valeurs and (not isinstance(valeurs['libelle'], str) or not 0 < len(valeurs['libelle']) <= 4000):
                raise ValueError('libelle_invalide')
            if 'fondements' in valeurs and (not isinstance(valeurs['fondements'], list) or len(valeurs['fondements']) > 30
                                            or any(not isinstance(x, str) or len(x) > 1000 for x in valeurs['fondements'])):
                raise ValueError('fondements_invalides')
            avant = deepcopy(demandes[ident])
            demandes[ident].update(deepcopy(valeurs))
            demandes[ident]['hypothese'] = True
            ecarts.append({'operation': op, 'id': ident, 'avant': avant, 'apres': deepcopy(demandes[ident])})
        else:
            raise ValueError('operation_hypothese_inconnue')
    if mode == 'hypothese' and not ecarts:
        raise ValueError('hypothese_vide')
    etapes = ['verifier_cadrage_et_couverture', 'rechercher_jurisprudence_comparable_autorisee', 'verifier_sources_et_contraires', 'raisonner_par_demande']
    if mode == 'hypothese':
        etapes += ['comparer_aux_memes_conditions_du_scenario_base', 'expliquer_les_ecarts']
    if mode == 'contrer':
        etapes += ['meilleure_objection_a_nos_arguments', 'reponse_a_chaque_moyen_adverse', 'contre_objection_et_reserves']
    if mode == 'ameliorer':
        etapes += ['lier_lacune_a_demande_et_preuve', 'classer_actions_par_utilite_motivee', 'preparer_question_ou_projet_sans_envoi']
    if mode == 'en_etat':
        etapes += ['projet_motifs_dispositif', 'tableau_issues_et_estimation_si_homologuee']
    return {'mode': mode, 'libelle': LIBELLES[mode], 'matter_id': snapshot['matter_id'], 'revision_base': snapshot['revision'],
            'empreinte_base': base_hash, 'empreinte_scenario': empreinte(copie), 'snapshot_scenario': copie, 'ecarts': ecarts, 'etapes': etapes,
            'probabilites': None, 'motif_probabilites': 'Service statistique homologué nécessaire', 'effets_externes': []}


def tableau_issues(issues, estimation=None, *, empreinte_attendue='', demande_id=''):
    """Projection d'écran : un booléen ou un pourcentage du modèle n'est pas une estimation. Seule la sortie du panneau homologué, pour la
    MÊME demande et le MÊME instantané, donne une valeur ; sinon « Non estimable de façon fiable », jamais 0 % ni 50 % de remplissage."""
    rows = _index(issues, 'issues')
    for r in rows.values():
        if not r.get('explication') or not isinstance(r.get('source_ids'), list):
            raise ValueError('issue_sans_explication_ou_sources')
    ok = (isinstance(estimation, dict) and estimation.get('etat') == 'estimation_statistique' and estimation.get('rapport_sha256')
          and empreinte_attendue and demande_id and estimation.get('empreinte_contexte') == empreinte_attendue
          and estimation.get('demande_id') == demande_id and isinstance(estimation.get('probabilites'), dict)
          and set(estimation['probabilites']) == set(rows))
    if ok:
        valeurs = estimation['probabilites'].values()
        ok = all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in valeurs) and math.isclose(sum(valeurs), 1, rel_tol=0, abs_tol=1e-6)
    resultat = []
    for ident, r in rows.items():
        p = estimation['probabilites'][ident] if ok else None
        resultat.append({**deepcopy(r), 'pourcentage': round(p * 100, 1) if p is not None else None,
                         'estimation_affichee': str(round(p * 100, 1)).replace('.', ',') + ' %' if p is not None else 'Non estimable de façon fiable'})
    return resultat


# ------------------------------------------------------------------------------------------------ services (faux dans les essais)
def _services(desk):
    """Modèles, connecteur de recherche et lecture du texte officiel : ``None`` = services du produit (routage, politique des données,
    budget et pause communs ; sélection des sources du registre commun). Remplacé par de faux services dans les essais, jamais en production."""
    return {'model': None, 'control_model': None, 'provider_runner': None, 'official_fetcher': None, 'providers': None}


# ------------------------------------------------------------------------------------------------ droits et dossier
def _acteur_courant():
    from .membres5650 import current_author, viewer_role
    return (current_author() or 'cabinet', viewer_role())


def _acces(desk, acteur, did):
    """Acteur normalisé et identifiant courant du dossier, ou le même 404 neutre (absent, réservé) ; contrôle illisible : 503."""
    did = str(did or '')
    if not IDENT.fullmatch(did):
        raise Stop('introuvable')
    try:
        a = acces5697.exiger(desk, acteur, dossier_id=did)
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            raise
        raise Stop('introuvable') from None
    courant = None
    try:
        from . import dossiers5697 as D
        courant = D.resoudre(desk, did)
    except (Stop, sqlite3.Error):
        courant = None
    if courant and courant != did and not acces5697.peut_lire_dossier(desk, a, courant):
        raise Stop('introuvable')
    if not courant:
        from .common import load_matters
        if did not in {str(m['id']) for m in load_matters(desk.c)}:
            raise Stop('introuvable')
        courant = did
    return a, courant


def _role_action(a):
    if a.role not in ROLES_ACTION:
        raise Stop('role_insuffisant')


def _cadrage(desk, did):
    row = _lignes(desk, 'SELECT version, data_json, modifie_par, modifie_le FROM simulation_cadrages56109 WHERE matter_id=?', (did,))
    if not row:
        return {'version': 0}
    try:
        data = json.loads(row[0]['data_json'])
    except ValueError:
        data = {}
    return {**data, 'version': row[0]['version'], 'modifie_par': row[0]['modifie_par'], 'modifie_le': row[0]['modifie_le']}


def eligibilite(desk, did):
    """Contentieux ou postulation : profil de la fiche, nature du registre, avocat postulant inscrit, rôle confirmé au cadrage."""
    from . import projection56109 as P
    row = _lignes(desk, 'SELECT nature FROM dossiers5697 WHERE id=?', (did,))
    profil = ''
    if row:
        try:
            profil = P.profil_du_dossier(desk, did, row[0])[0]
        except (Stop, sqlite3.Error):
            profil = ''
    postulant = bool(_lignes(desk, "SELECT 1 FROM dossier_parties5697 WHERE dossier_id=? AND role='avocat_postulant' LIMIT 1", (did,)))
    if _cadrage(desk, did).get('role_cabinet') == 'postulant':
        postulant = True
    ok = profil in ('contentieux', 'mixte') or bool(row and row[0]['nature'] == 'contentieux') or postulant
    return {'eligible': bool(ok and row), 'profil': profil, 'postulation': postulant, 'registre': bool(row)}


def _stade_instance(desk, did):
    row = _lignes(desk, 'SELECT degre FROM dossier_instances5698 WHERE dossier_id=? AND principale=1', (did,))
    degre = row[0]['degre'] if row else ''
    return degre if degre in STADES else ''


def _documents(desk, did):
    return {r['path']: dict(r) for r in _lignes(desk, 'SELECT path, name, etag, text_sha, nature, reading FROM d51_documents WHERE matter=?', (did,))}


def _texte_indexe(desk, did, chemin):
    """Texte lu d'un document du dossier (index local), ses morceaux et sa couverture ; jamais un téléchargement."""
    from pathlib import Path
    base = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if not base.is_file():
        return '', {'morceaux': 0, 'partielle': True}
    db = sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        rows = db.execute('SELECT text, meta FROM knowledge_chunks WHERE matter=? AND path=? ORDER BY chunk_no', (did, chemin)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    partielle = False
    for _, meta in rows:
        try:
            partielle = partielle or (json.loads(meta or '{}').get('coverage') == 'partial')
        except ValueError:
            partielle = True
    return ''.join(r[0] for r in rows), {'morceaux': len(rows), 'partielle': partielle or not rows}


def _dispositif(texte):
    m = re.search(r'PAR\s+CES\s+MOTIFS', texte, re.I)
    return texte[m.start():][:8000].strip() if m else ''


def _jugement(desk, did, chemin):
    """Jugement ou ordonnance IDENTIFIÉ : document du dossier, de nature décision ; version, empreinte du texte lu, couverture, dispositif.
    Complet seulement si la lecture est entière (une lecture partielle ou un scan sans texte ne l'est jamais)."""
    docs = _documents(desk, did)
    d = docs.get(str(chemin or ''))
    if not d or d['nature'] not in NATURES_DECISION:
        raise Stop('jugement_non_identifie')
    texte, couverture = _texte_indexe(desk, did, d['path'])
    complet = bool(texte) and not couverture['partielle'] and d['reading'] == 'lisible'
    return {'source_id': _ident('doc', d['path']), 'path': d['path'], 'nom': d['name'], 'nature': 'decision_premiere_instance', 'nature_document': d['nature'],
            'version': d['etag'] or d['text_sha'] or '', 'sha256': hashlib.sha256(texte.encode('utf-8')).hexdigest() if texte else '',
            'complet': complet, 'couverture': {**couverture, 'caracteres': len(texte), 'lecture': d['reading']}, 'dispositif': _dispositif(texte)}


def instantane(desk, acteur, did):
    """Instantané autorisé : projection de la fiche (lecture seule, aucun modèle) et cadrage confirmé. Données fictives dans les essais."""
    from . import projection56109 as P
    fiche = P.fiche(desk, acteur, did)
    docs = _documents(desk, did)
    doc_ids = {p: _ident('doc', p) for p in docs}
    documents = [{'id': doc_ids[p], 'path': p, 'nom': d['name'], 'nature': d['nature'], 'version': d['etag'] or d['text_sha'] or '', 'lecture': d['reading']}
                 for p, d in sorted(docs.items())]
    sources, vus = [], set()

    def srcs(eid, liste):
        ids = []
        for s in liste or []:
            if s.get('indisponible') or not s.get('path'):
                continue
            sid = _ident('src', eid, s['path'], s.get('page') or '')
            if sid not in vus:
                vus.add(sid)
                sources.append({'id': sid, 'document_id': doc_ids.get(s['path'], ''), 'path': s['path'], 'page': str(s.get('page') or ''),
                                'version': s.get('version') or ''})
            ids.append(sid)
        return ids
    sec = fiche.get('sections') or {}
    faits = []
    for nom, genre in (('faits', 'fait'), ('allegations', 'allegation')):
        for x in sec.get(nom) or []:
            fid = 'f-' + str(x['id'])
            statut = 'etabli' if nom == 'faits' and x.get('statut') == 'confirme' and not x.get('a_verifier') else ('allegation' if nom == 'allegations' else 'a_verifier')
            faits.append({'id': fid, 'type': genre, 'texte': x['texte'], 'auteur': x.get('auteur') or '', 'statut': statut, 'revision': x.get('revision'),
                          'source_ids': srcs(fid, x.get('sources'))})
    cad = _cadrage(desk, did)
    exclues = set(cad.get('demandes_exclues') or [])
    demandes = []
    for x in sec.get('pretentions') or []:
        did_ = 'd-' + str(x['id'])
        demandes.append({'id': did_, 'titulaire_id': x.get('auteur') or 'document', 'objet': x['texte'], 'montant': str(x.get('montant') or ''),
                         'revision': x.get('revision'), 'examinee': did_ not in exclues, 'source_ids': srcs(did_, x.get('sources'))})
    decisions = [{'id': 'j-' + str(x['id']), 'texte': x['texte'], 'statut': x.get('statut'), 'original_examine': x.get('original_examine')}
                 for x in sec.get('decisions') or []]
    humaines = [{'id': str(x['id']), 'texte': x['texte'], 'revision': x.get('revision')} for x in (sec.get('objectif') or []) + (sec.get('decisions_cabinet') or [])]
    elig = eligibilite(desk, did)
    illisibles = [d['nom'] for d in documents if d['lecture'] in ('erreur', 'protegee', 'non_prise_en_charge')]
    non_lus = [d['nom'] for d in documents if d['lecture'] not in ('lisible', 'partielle', 'erreur', 'protegee', 'non_prise_en_charge')]
    return {'matter_id': did, 'revision': max(1, int(cad['version'] or 0)), 'fiche_revision': fiche.get('revision', ''),
            'nature': 'postulation' if elig['postulation'] else ('contentieux' if elig['eligible'] else 'autre'),
            'role_cabinet': cad.get('role_cabinet') or ('postulant' if elig['postulation'] else ''), 'cote_client': cad.get('cote_client') or '',
            'stade': cad.get('stade') or _stade_instance(desk, did), 'question': cad.get('question') or '',
            'documents': documents, 'sources': sources, 'faits': faits, 'demandes': demandes, 'decisions': decisions, 'decisions_humaines': humaines,
            'jugement': cad.get('jugement') or {}, 'declaration_appel_source_id': (cad.get('declaration') or {}).get('source_id', ''),
            'chefs_contestes_valides': list(cad.get('chefs') or []),
            'postulation': {'conclusions_fond': bool(cad.get('conclusions_fond')), 'instructions_correspondant': bool(cad.get('instructions_correspondant'))},
            'couverture': {'documents': len(documents), 'lus': sum(1 for d in documents if d['lecture'] in ('lisible', 'partielle')),
                           'illisibles': illisibles, 'non_analyses': non_lus, 'limites': list((fiche.get('couverture') or {}).get('limites') or [])}}


def _contexte_cadrage(snap):
    return {'nature': snap.get('nature'), 'stade': snap.get('stade'), 'jugement': snap.get('jugement') or {},
            'demandes': [{'id': d['id'], 'titulaire_id': d['titulaire_id'], 'objet': d['objet']} for d in snap.get('demandes') or [] if d.get('examinee', True)],
            'chefs_contestes_valides': snap.get('chefs_contestes_valides') or [], 'declaration_appel_source_id': snap.get('declaration_appel_source_id') or ''}


def _atelier(snap):
    """Contrat de ``preparer`` : seules les demandes examinées entrent dans le scénario."""
    s = deepcopy(snap)
    s['demandes'] = [d for d in s.get('demandes') or [] if d.get('examinee', True)]
    return s


# ------------------------------------------------------------------------------------------------ commandes idempotentes
def _executer(desk, compte, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409)."""
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    emp = _sha(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str))
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM simulation_ops56109 WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row['empreinte'] != emp:
            raise Stop('cle_reutilisee')
        if row['etat'] == 'fait':
            return {**json.loads(row['resultat']), 'rejoue': True}
        if row['etat'] == 'echec':
            raise Stop(json.loads(row['resultat']).get('error') or 'operation_echouee')
        raise Stop('operation_en_cours')
    desk.db.execute('INSERT INTO simulation_ops56109(acteur,cle,nature,empreinte,etat,resultat,cree_le) VALUES(?,?,?,?,?,?,?)',
                    (compte, cle, nature, emp, 'en_cours', '{}', _maintenant()))
    desk.db.commit()
    try:
        out = fn()
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne'
        desk.db.execute("UPDATE simulation_ops56109 SET etat='echec', resultat=? WHERE acteur=? AND cle=?", (json.dumps({'error': code}), compte, cle))
        desk.db.commit()
        raise
    desk.db.execute("UPDATE simulation_ops56109 SET etat='fait', resultat=? WHERE acteur=? AND cle=?", (json.dumps(out, ensure_ascii=False, default=str), compte, cle))
    desk.db.commit()
    return out


def _texte(valeur, maxi):
    v = str(valeur if valeur is not None else '').replace('\x00', '').strip()
    if len(v) > maxi:
        raise Stop('champ_invalide')
    return v


def _valider_cadrage(desk, did, d):
    """Contrôle complet AVANT toute écriture. Les documents désignés sont résolus côté serveur dans CE dossier (jamais un chemin libre)."""
    v = {'question': _texte(d.get('question'), 2000)}
    for champ, permis in (('stade', STADES), ('role_cabinet', ROLES_CABINET), ('cote_client', COTES)):
        valeur = str(d.get(champ) or '')
        if valeur and valeur not in permis:
            raise Stop('champ_invalide')
        v[champ] = valeur
    if str(d.get('jugement') or '').strip():
        v['jugement'] = _jugement(desk, did, str(d['jugement']).strip())
    if str(d.get('declaration') or '').strip():
        doc = _documents(desk, did).get(str(d['declaration']).strip())
        if not doc:
            raise Stop('declaration_non_identifiee')
        v['declaration'] = {'source_id': _ident('doc', doc['path']), 'path': doc['path'], 'nom': doc['name'], 'version': doc['etag'] or doc['text_sha'] or ''}
    chefs = d.get('chefs') or []
    if isinstance(chefs, str):
        chefs = chefs.splitlines()
    if not isinstance(chefs, list) or len(chefs) > 30:
        raise Stop('champ_invalide')
    v['chefs'] = [c for c in (_texte(x, 500) for x in chefs) if c]
    v['demandes_exclues'] = sorted(k.split(':', 1)[1] for k, x in d.items() if k.startswith('demande:') and x is False)[:60]
    v['conclusions_fond'] = d.get('conclusions_fond') is True
    v['instructions_correspondant'] = d.get('instructions_correspondant') is True
    return v


def enregistrer_cadrage(desk, acteur, did, d, cle):
    """POST cadrage : ce que le cabinet confirme (jamais déduit d'un nom de fichier ni de la seule mention « appel »). Révision attendue."""
    a, did = _acces(desk, acteur, did)
    _role_action(a)
    if not eligibilite(desk, did)['eligible'] and d.get('role_cabinet') != 'postulant':
        raise Stop('simulation_reservee_au_contentieux')
    corps = {k: x for k, x in d.items() if k != 'cle'}

    def faire():
        courant = _cadrage(desk, did)
        if _int(d.get('version'), -1) != int(courant['version']):
            raise Stop('version_perimee')
        valeurs = _valider_cadrage(desk, did, d)
        nouvelle = int(courant['version']) + 1
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            n = desk.db.execute('UPDATE simulation_cadrages56109 SET version=?, data_json=?, modifie_par=?, modifie_le=? WHERE matter_id=? AND version=?',
                                (nouvelle, json.dumps(valeurs, ensure_ascii=False), a.compte, _maintenant(), did, courant['version'])).rowcount
            if not n:
                if courant['version']:
                    raise Stop('version_perimee')
                desk.db.execute('INSERT INTO simulation_cadrages56109(matter_id,version,data_json,modifie_par,modifie_le) VALUES(?,?,?,?,?)',
                                (did, nouvelle, json.dumps(valeurs, ensure_ascii=False), a.compte, _maintenant()))
            desk.db.commit()
        except BaseException:
            desk.db.rollback()
            raise
        desk.audit('simulation56109_cadrage', {'matter': did, 'par': a.compte, 'version': nouvelle, 'jugement': bool(valeurs.get('jugement'))})
        return {'dossier': did, 'version': nouvelle, 'cadrage': {**valeurs, 'version': nouvelle}, 'message': 'Cadrage enregistré (version %d).' % nouvelle}
    return _executer(desk, a.compte, cle, 'cadrage:' + did, corps, faire)


# ------------------------------------------------------------------------------------------------ préparer une simulation (POST → file)
def _modifications(d):
    """Cases et champs du formulaire « Tester une hypothèse » → opérations fermées (identifiants du dossier, jamais un chemin)."""
    mods, demandes = [], {}
    for k, x in sorted(d.items()):
        if ':' not in k:
            continue
        genre, ident = k.split(':', 1)
        if genre in ('retirer_piece', 'fait_non_etabli') and x is True:
            mods.append({'operation': genre, 'id': ident})
        elif genre in ('montant', 'libelle') and isinstance(x, str) and x.strip():
            demandes.setdefault(ident, {})[genre] = x.strip().replace(',', '.') if genre == 'montant' else x.strip()
    for ident, valeurs in sorted(demandes.items()):
        mods.append({'operation': 'modifier_demande', 'id': ident, 'valeurs': valeurs})
    if len(mods) > MAX_MODIFICATIONS:
        raise Stop('champ_invalide')
    return mods


def _run(desk, did, rid):
    if not RUN.fullmatch(str(rid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=? AND matter_id=?', (str(rid), did)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row


def _recu(row, rejoue=False, doublon=False):
    return {'run_id': row['id'], 'job_id': row['job_id'], 'state': row['state'], 'etat': ETATS.get(row['state'], row['state']), 'mode': row['mode'],
            'rejoue': rejoue, 'doublon': doublon,
            'cout': 'Coût : relevé après exécution dans « Consommation et coûts » ; aucune estimation fiable n’est donnée avant le lancement.',
            'message': ('Simulation déjà demandée (même demande).' if rejoue or doublon else
                        'Simulation mise en file (travail n° %s) : le résultat s’affichera dans cet onglet.' % row['job_id'])}


def simuler(desk, acteur, did, d, cle):
    """POST « Préparer la simulation » (et les trois autres actions) : un traitement versionné et le travail EXISTANT ``prepare_legal_opinion``
    dans la file commune ; rien n'est recherché ni rédigé dans la requête. Rend ``(reçu, nouveau)``."""
    a, did = _acces(desk, acteur, did)
    _role_action(a)
    if not eligibilite(desk, did)['eligible']:
        raise Stop('simulation_reservee_au_contentieux')
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    mode = str(d.get('mode') or '')
    if mode not in MODES:
        raise Stop('mode_inconnu')
    cad = _cadrage(desk, did)
    if _int(d.get('version'), -1) != int(cad['version']):
        raise Stop('version_perimee')
    mods = _modifications(d) if mode == 'hypothese' else []
    base_id = str(d.get('base_run_id') or '') if mode == 'hypothese' else ''
    parametres = {'mode': mode, 'modifications': mods, 'base_run_id': base_id, 'cadrage_version': cad['version']}
    emp = _sha(json.dumps(parametres, sort_keys=True, ensure_ascii=False))
    cabinet = _cabinet(desk)
    deja = desk.db.execute('SELECT * FROM simulation_runs56109 WHERE cabinet_id=? AND actor=? AND operation_key=?', (cabinet, a.compte, cle)).fetchone()
    if deja:
        if json.loads(deja['parameters_json']).get('empreinte') != emp:
            raise Stop('cle_reutilisee')
        return _recu(deja, rejoue=True), False
    if mode == 'hypothese':
        if not base_id:
            raise Stop('simulation_de_base_requise')
        base = _run(desk, did, base_id)
        if base['state'] not in AVEC_RESULTAT or base['mode'] == 'hypothese':
            raise Stop('simulation_de_base_requise')
        snap = json.loads(base['snapshot_json'])
    else:
        snap = instantane(desk, a, did)
    try:
        scenario = preparer(_atelier(snap), mode, mods)
    except ValueError as ex:
        raise Stop(str(ex)) from None
    doublon = desk.db.execute("SELECT * FROM simulation_runs56109 WHERE matter_id=? AND mode=? AND scenario_sha256=? AND base_run_id=? "
                              "AND state IN ('queued','running') ORDER BY created DESC, rowid DESC LIMIT 1", (did, mode, scenario['empreinte_scenario'], base_id)).fetchone()
    if doublon:
        return _recu(doublon, doublon=True), False
    rid = uuid.uuid4().hex
    quand = _maintenant()
    question = snap.get('question') or 'Quelle issue pour les demandes examinées du dossier ?'
    args = {'matter': did, 'simulation56109': rid, 'question': question[:500]}
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute('INSERT INTO simulation_runs56109(id,cabinet_id,matter_id,actor,operation_key,mode,base_run_id,version,state,cadrage_version,snapshot_sha256,'
                        'scenario_sha256,snapshot_json,modifications_json,parameters_json,created,updated) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)',
                        (rid, cabinet, did, a.compte, cle, mode, base_id, 'queued', cad['version'], empreinte(snap), scenario['empreinte_scenario'],
                         json.dumps(snap, ensure_ascii=False), json.dumps(mods, ensure_ascii=False),
                         json.dumps({**parametres, 'empreinte': emp, 'role': a.role}, ensure_ascii=False), quand, quand))
        jid = desk.enqueue('prepare_legal_opinion', args, priority=0, origin='user', transactional=True)
        desk.db.execute('UPDATE simulation_runs56109 SET job_id=? WHERE id=?', (jid, rid))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.notify_queued(jid, args)
    desk.audit('simulation56109_demandee', {'matter': did, 'run': rid, 'mode': mode, 'job': jid, 'par': a.compte, 'modifications': len(mods)})
    return _recu(desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()), True


def annuler(desk, acteur, did, rid, cle):
    """POST « Annuler » : commande centrale d'annulation du moteur, puis état « annulé » durable ; un retour tardif n'écrit plus rien."""
    a, did = _acces(desk, acteur, did)
    _role_action(a)
    row = _run(desk, did, rid)

    def faire():
        courant = desk.db.execute('SELECT state, job_id FROM simulation_runs56109 WHERE id=?', (row['id'],)).fetchone()
        if courant['state'] not in ('queued', 'running'):
            raise Stop('operation_deja_terminee')
        if courant['job_id']:
            try:
                desk.cancel_job(courant['job_id'])
            except Stop as ex:
                if str(ex) not in ('operation_deja_terminee', 'operation_absente'):
                    raise
        desk.db.execute("UPDATE simulation_runs56109 SET state='cancelled', version=version+1, updated=? WHERE id=? AND state IN ('queued','running')",
                        (_maintenant(), row['id']))
        desk.db.commit()
        desk.audit('simulation56109_annulee', {'matter': did, 'run': row['id'], 'par': a.compte})
        return {'run_id': row['id'], 'state': 'cancelled', 'message': 'Simulation annulée : un résultat arrivé après l’annulation ne sera pas enregistré.'}
    return _executer(desk, a.compte, cle, 'annuler', {'r': row['id']}, faire)


def corriger(desk, acteur, did, rid, d, cle):
    """POST « Corriger » : la correction de l'avocat est une nouvelle version, sans modifier le résultat ni les sources historiques."""
    a, did = _acces(desk, acteur, did)
    _role_action(a)
    row = _run(desk, did, rid)
    texte = _texte(d.get('texte'), 20000)
    if not texte:
        raise Stop('texte_requis')

    def faire():
        courante = desk.db.execute('SELECT COALESCE(MAX(version),0) FROM simulation_corrections56109 WHERE run_id=?', (row['id'],)).fetchone()[0]
        if _int(d.get('version'), -1) != courante:
            raise Stop('version_perimee')
        desk.db.execute('INSERT INTO simulation_corrections56109(run_id,matter_id,version,texte,par,le) VALUES(?,?,?,?,?,?)',
                        (row['id'], did, courante + 1, texte, a.compte, _maintenant()))
        desk.db.commit()
        desk.audit('simulation56109_corrigee', {'matter': did, 'run': row['id'], 'version': courante + 1, 'par': a.compte})
        return {'run_id': row['id'], 'version': courante + 1, 'message': 'Correction enregistrée (version %d).' % (courante + 1)}
    return _executer(desk, a.compte, cle, 'corriger', {'r': row['id'], 'v': d.get('version'), 't': _sha(texte)}, faire)


def _payload_livrable(desk, row):
    res = _sans_pourcentage(json.loads(row['result_json'] or '{}'))
    corr = desk.db.execute('SELECT texte, version, par FROM simulation_corrections56109 WHERE run_id=? ORDER BY version DESC LIMIT 1', (row['id'],)).fetchone()
    blocs = [{'id': 'titre', 'type': 'heading', 'source_ids': [], 'text': 'Simulation interne — projet à relire (ce n’est pas une décision rendue)'},
             {'id': 'mode', 'type': 'paragraph', 'source_ids': [], 'text': '%s — %s' % (LIBELLES[row['mode']], ETATS.get(row['state'], row['state']))},
             {'id': 'synthese', 'type': 'paragraph', 'source_ids': [], 'text': str(res.get('synthese') or 'Aucune synthèse.')[:20000]},
             {'id': 'statistique', 'type': 'paragraph', 'source_ids': [], 'text': MESSAGE_STATISTIQUE}]
    lignes = [{'id': 'L%d' % i, 'cells': [str(x.get('demande') or '')[:2000], str(x.get('issue') or '')[:2000], str(x.get('estimation_affichee') or ''),
                                          str(x.get('niveau_libelle') or ''), str(x.get('explication') or '')[:4000]],
               'statut': 'a_confirmer', 'source_ids': []} for i, x in enumerate(res.get('issues') or [], 1)]
    if lignes:
        blocs.append({'id': 'issues', 'type': 'table', 'source_ids': [], 'columns': ['Demande', 'Issue', 'Estimation', 'Niveau', 'Explication'], 'rows': lignes})
    if corr:
        blocs.append({'id': 'correction', 'type': 'paragraph', 'source_ids': [], 'text': 'Correction de l’avocat (version %d, %s) : %s' % (corr['version'], corr['par'], corr['texte'])})
    return {'kind': 'consultation', 'title': ('Simulation interne — ' + LIBELLES[row['mode']])[:180], 'blocks': blocs}


def conserver(desk, acteur, did, rid, cle):
    """POST « Conserver comme livrable » : un document du registre COMMUN (``artefacts56109``), visible dans Produire et Aujourd'hui."""
    from . import artefacts56109 as A
    from .membres5650 import effective_rights, hidden_for
    a, did = _acces(desk, acteur, did)
    _role_action(a)
    row = _run(desk, did, rid)
    if row['state'] not in AVEC_RESULTAT:
        raise Stop('simulation_non_terminee')
    if row['livrable_id']:
        return {'document_id': row['livrable_id'], 'rejoue': True, 'message': 'Cette simulation est déjà conservée comme livrable.'}
    try:
        hidden, droits = hidden_for(desk.c['state_dir'], a.compte, a.role), effective_rights(desk.c['state_dir'], a.compte, a.role)
    except Exception:   # noqa: BLE001 — droits illisibles : jamais un accès élargi
        raise Stop('controle_acces_indisponible') from None
    out = A.ecrire(desk, a.compte, a.role, hidden, droits, _payload_livrable(desk, row), cle, 0, None, did, '', 'simulation56109')
    desk.db.execute("UPDATE simulation_runs56109 SET livrable_id=? WHERE id=? AND livrable_id=''", (out['document_id'], row['id']))
    desk.db.commit()
    return {**out, 'message': 'Simulation conservée comme livrable : elle apparaît dans Produire, Aujourd’hui et le dossier.'}


# ------------------------------------------------------------------------------------------------ lecture
def _resume_run(row):
    return {'id': row['id'], 'mode': row['mode'], 'mode_libelle': LIBELLES[row['mode']], 'state': row['state'], 'etat': ETATS.get(row['state'], row['state']),
            'created': row['created'], 'updated': row['updated'], 'base_run_id': row['base_run_id'], 'job_id': row['job_id'], 'livrable_id': row['livrable_id'],
            'snapshot_sha256': row['snapshot_sha256'], 'error': row['error']}


def lire_run(desk, acteur, did, rid):
    """GET d'un traitement : droits et appartenance au dossier relus ; sortie sans aucun pourcentage du modèle."""
    a, did = _acces(desk, acteur, did)
    row = _run(desk, did, rid)
    corr = [dict(r) for r in desk.db.execute('SELECT version, texte, par, le FROM simulation_corrections56109 WHERE run_id=? ORDER BY version', (row['id'],))]
    return {**_resume_run(row), 'result': _sans_pourcentage(json.loads(row['result_json'] or '{}')), 'corrections': _sans_pourcentage(corr),
            'modifications': json.loads(row['modifications_json'] or '[]')}


def _pause_ia(desk, did):
    try:
        from .pause5629 import PAUSE_IA, is_paused
        return bool(desk.settings(PAUSE_IA, False)) or is_paused(desk, did)
    except Exception:   # noqa: BLE001
        return False


def contexte(desk, acteur, did):
    """GET : cadrage, couverture, simulations existantes, éléments manquants, signal « À actualiser ». Aucun modèle, aucune recherche, aucune écriture."""
    a, did = _acces(desk, acteur, did)
    elig = eligibilite(desk, did)
    cad = _cadrage(desk, did)
    out = {'dossier': did, **elig, 'cadrage': cad, 'pause_ia': _pause_ia(desk, did), 'statistique': {'voie': 'fermee', 'message': MESSAGE_STATISTIQUE},
           'simulations': [_resume_run(r) for r in desk.db.execute('SELECT * FROM simulation_runs56109 WHERE matter_id=? ORDER BY created DESC, rowid DESC LIMIT 50', (did,))]}   # 5.6.109 (I2) : deux simulations de la même seconde : la plus récente d'abord (ordre d'insertion), jamais au hasard
    if not elig['eligible']:
        return {**out, 'instantane': None, 'a_actualiser': False, 'cadrage_etat': None}
    snap = instantane(desk, a, did)
    emp = empreinte(snap)
    derniere = next((s for s in out['simulations'] if s['mode'] != 'hypothese' and s['state'] in AVEC_RESULTAT + ('needs_information',)), None)
    return {**out, 'instantane': snap, 'empreinte': emp, 'cadrage_etat': cadrer(_contexte_cadrage(snap)),
            'a_actualiser': bool(derniere and derniere['snapshot_sha256'] != emp)}


# ------------------------------------------------------------------------------------------------ traitement (travail de la file commune)
def _citable(autorite):
    """Seule une décision VÉRIFIÉE sur un texte officiel (extrait exact retrouvé, identifiant présent, empreinte du texte) est citable."""
    return (autorite.get('verification_status') == 'verified' and bool(autorite.get('exact_excerpt')) and bool(autorite.get('official_text_sha256')))


def _noms_du_dossier(desk, did):
    noms = [r[0] for r in _lignes(desk, 'SELECT nom FROM dossier_parties5697 WHERE dossier_id=?', (did,))]
    noms += [r[0] for r in _lignes(desk, 'SELECT display_name FROM d51_parties WHERE matter=?', (did,))]
    return sorted({str(n).strip() for n in noms if len(str(n or '').strip()) >= 3}, key=len, reverse=True)


def _depersonnaliser(texte, noms):
    """Les noms des parties ne partent pas vers un connecteur (l'anonymisation de ``legal_research`` s'applique ensuite)."""
    out = texte
    for nom in noms:
        out = re.sub(re.escape(nom), '[PARTIE]', out, flags=re.I)
        for mot in nom.split():
            if len(mot) >= 4 and mot.isupper():
                out = re.sub(r'\b%s\b' % re.escape(mot), '[PARTIE]', out)
    return out


def _requetes(desk, did, snap):
    """Deux requêtes contradictoires (solutions favorables, solutions contraires), dépersonnalisées : question et qualification seules."""
    question = snap.get('question') or 'Issue des demandes en paiement et de leurs conditions de preuve'
    noms = _noms_du_dossier(desk, did)
    base = _depersonnaliser(question, noms)[:1200]
    return [base, _depersonnaliser('Décisions contraires et solutions opposées : ' + question, noms)[:1200]]


def _rechercher(desk, did, snap, services, checkpoint):
    """Connecteurs du registre commun, revérification officielle de chaque piste ; une panne d'un fournisseur garde les résultats des autres."""
    from . import legal_research as LR
    from .reprise56109 import arret_central
    providers, erreurs = services.get('providers'), []
    if providers is None:
        from .selection_sources5697 import rapports_ecartes, selectionner
        sel = selectionner(desk)
        providers = [p for p in sel.ids if LR._known(p)]
        erreurs += rapports_ecartes(sel)
    providers = list(providers)
    if not providers:
        return {'fournisseurs': [], 'erreurs': erreurs + [{'provider': '', 'error': 'aucune_source_retenue'}], 'requetes': [], 'query_ids': [], 'figee': False}
    qids, envoyees = [], []
    for requete in _requetes(desk, did, snap):
        checkpoint()
        try:
            if services.get('provider_runner') is None:   # connecteurs réels : politique des données sur la requête telle qu'elle partirait
                from .data_policy5689 import exiger
                anonyme, _ = LR.anonymize_query(desk, LR._matter(desk, did), requete)
                exiger(desk.c, 'recherche_web', 'sources_juridiques', finalite='recherche_juridique', requete=anonyme)
            res = LR.research(desk, {'matter': did, 'question': requete, 'providers': providers, 'limit': 8},
                              provider_runner=services.get('provider_runner'), official_fetcher=services.get('official_fetcher'))
        except Stop as ex:
            if arret_central(ex):
                raise
            erreurs.append({'provider': '', 'error': str(ex).split(':')[0]})
            continue
        qids.append(res['query_id'])
        envoyees.append(res['anonymized_query'])
        erreurs += [x for x in res['errors'] if not x.get('identifier')]
    return {'fournisseurs': providers, 'erreurs': erreurs, 'requetes': envoyees, 'query_ids': qids, 'figee': False}


def _autorites(desk, did, query_ids=(), ids=()):
    if query_ids:
        marks = ','.join('?' * len(query_ids))
        rows = _lignes(desk, 'SELECT * FROM legal_authorities_v240 WHERE matter=? AND query_id IN (%s)' % marks, (did, *query_ids))
    elif ids:
        marks = ','.join('?' * len(ids))
        rows = _lignes(desk, 'SELECT * FROM legal_authorities_v240 WHERE matter=? AND id IN (%s)' % marks, (did, *ids))
    else:
        rows = []
    return [dict(r) for r in rows]


def _pistes(desk, query_ids):
    if not query_ids:
        return []
    marks = ','.join('?' * len(query_ids))
    return [dict(r) for r in _lignes(desk, 'SELECT * FROM legal_research_leads_v240 WHERE query_id IN (%s)' % marks, tuple(query_ids))]


def _comparaison(autorites, pistes):
    """Une ligne par décision canonique ; une décision vérifiée garde ses provenances ; une piste non vérifiée n'est jamais comptée ni citée."""
    lignes = []
    for x in autorites:
        cle = (x.get('ecli') or x.get('identifier') or '').strip().upper()
        lignes.append({'decision_canonique_id': cle or None, 'identifiant': x.get('identifier') or x.get('ecli') or '', 'juridiction': x.get('court') or '',
                       'date': x.get('decision_date') or '', 'titre': x.get('title') or '', 'provenances': [x.get('provider') or ''],
                       'texte_sha256': x.get('official_text_sha256') or '', 'statut': 'verifiee' if _citable(x) else 'piste',
                       'url_officielle': x.get('official_url') if _citable(x) else '', 'extrait': x.get('exact_excerpt') if _citable(x) else ''})
    verifiees = {l['identifiant'] for l in lignes if l['statut'] == 'verifiee'}
    for p in pistes:
        ident = p.get('identifier') or p.get('ecli') or ''
        if ident in verifiees or any(l['identifiant'] == ident for l in lignes):
            continue
        lignes.append({'decision_canonique_id': None, 'identifiant': ident, 'juridiction': p.get('court') or '', 'date': p.get('decision_date') or '',
                       'titre': p.get('title') or '', 'provenances': [p.get('provider') or ''], 'statut': 'piste', 'url_officielle': '', 'extrait': ''})
    out = []
    for l in dedoublonner_decisions([l if l['statut'] == 'verifiee' else {**l, 'decision_canonique_id': None} for l in lignes]):
        l.pop('decision_canonique_id', None)
        out.append(l)
    return out


def _paquet(desk, did, scenario, autorites, jugement):
    """Paquet contrôlé côté serveur : faits et demandes du scénario (sources exclues retirées), jugement identifié, décisions citables."""
    snap = scenario['snapshot_scenario']
    srcs = {s['id']: s for s in snap.get('sources') or []}
    items, ecartes = [], []
    for f in (snap.get('faits') or []) + [dict(x, type='demande') for x in snap.get('demandes') or []]:
        if f.get('statut') == 'non_etabli_par_hypothese':
            ecartes.append({'id': f['id'], 'motif': 'Fait considéré comme non établi dans l’hypothèse'})
            continue
        ids = [sid for sid in f.get('source_ids') or [] if sid in srcs and srcs[sid].get('incluse_scenario', True) and srcs[sid].get('path')]
        if not ids:
            if f.get('source_ids'):
                ecartes.append({'id': f['id'], 'motif': 'Toutes ses sources sont retirées dans l’hypothèse : à réexaminer'})
            continue
        s = srcs[ids[0]]
        texte = f.get('texte') or f.get('objet') or ''
        if f.get('type') == 'allegation':
            texte = 'Allégation attribuée à %s : %s' % (f.get('auteur') or 'une partie', texte)
        if f.get('hypothese'):
            texte += ' [Hypothèse : %s]' % ', '.join('%s = %s' % (k, f[k]) for k in ('montant', 'libelle') if f.get(k))
        items.append({'id': 'fiche-' + f['id'], 'kind': 'fiche_' + (f.get('type') or 'fait'), 'path': s['path'], 'page': s.get('page', ''),
                      'excerpt': texte[:3000], 'version': s.get('version', ''), 'statut': f.get('statut') or '', 'officially_verified': False})
    decision_id = None
    if jugement:
        texte, _ = _texte_indexe(desk, did, jugement['path'])
        extrait = texte if len(texte) <= MAX_TEXTE_JUGEMENT else texte[:MAX_TEXTE_JUGEMENT - 8000] + '\n[…]\n' + jugement.get('dispositif', '')
        decision_id = 'decision-' + jugement['sha256'][:20]
        items.append({'id': decision_id, 'kind': 'first_instance_decision', 'path': jugement['path'], 'excerpt': extrait, 'sha256': jugement['sha256'],
                      'version': jugement['version'], 'dispositif': jugement.get('dispositif', ''), 'couverture': jugement.get('couverture', {}),
                      'officially_verified': False})
    for r in autorites:
        if not _citable(r):
            continue
        items.append({'id': 'authority-' + r['id'][:20], 'kind': 'official_legal_source', 'path': r['official_url'], 'excerpt': r['exact_excerpt'],
                      'authority_id': r['id'], 'identifier': r['identifier'], 'ecli': r['ecli'], 'court': r['court'], 'decision_date': r['decision_date'],
                      'official_text_sha256': r['official_text_sha256'], 'officially_verified': True})
    return items, ecartes, decision_id


def _niveau(scenario, officielles):
    """Appréciation QUALITATIVE motivée, calculée par le code (jamais convertie en nombre) : sources factuelles, décisions vérifiées, contraires."""
    auth, contra = scenario.get('authority_source_ids') or [], scenario.get('contrary_authority_source_ids') or []
    if not scenario.get('factual_source_ids') or not auth or any(x not in officielles for x in auth):
        return 'insuffisant', 'Faits ou décisions vérifiées manquants pour ce scénario.'
    if contra or scenario.get('calibration') in ('plausible', 'indeterminate'):
        motif = 'Des décisions vérifiées vont en sens contraire.' if contra else 'Le raisonnement reste conditionnel (appréciation du modèle à vérifier).'
        return 'discutee', motif
    return 'etayee', 'Appuyée sur des faits sourcés et des décisions vérifiées sur un texte officiel, sans décision contraire relevée.'


def _resultat(desk, row, snap, scenario, opinion, recherche, packet, ecartes, autorites, pistes):
    op, mode = opinion['opinion'], row['mode']
    officielles = {x['id'] for x in packet if x.get('officially_verified')}
    scenarios = []
    for i, s in enumerate(op.get('decision_scenarios') or [], 1):
        niveau, motif = _niveau(s, officielles)
        scenarios.append({'id': 'S%d' % i, 'titre': s['title'], 'issue': s['reasoned_outcome'], 'niveau': niveau, 'niveau_libelle': NIVEAUX[niveau],
                          'motif_niveau': motif, 'conditions': s.get('necessary_facts') or [], 'facteurs': s.get('sensitivity_factors') or [],
                          'sources': (s.get('factual_source_ids') or []) + (s.get('authority_source_ids') or []),
                          'contraires': s.get('contrary_authority_source_ids') or [], 'limites': s.get('limits') or []})
    demandes = [d for d in snap.get('demandes') or [] if d.get('examinee', True)]
    libelle_demandes = ' ; '.join(d['objet'][:160] for d in demandes) or 'Demandes examinées'
    issues = []
    for d in demandes or [{'id': '', 'objet': libelle_demandes}]:
        lignes = tableau_issues([{'id': s['id'], 'explication': s['issue'], 'source_ids': s['sources']} for s in scenarios], None) if scenarios else []
        for s, t in zip(scenarios, lignes):
            issues.append({'demande_id': d['id'], 'demande': d['objet'][:300], 'issue': s['titre'], 'pourcentage': t['pourcentage'],
                           'estimation_affichee': t['estimation_affichee'], 'niveau': s['niveau'], 'niveau_libelle': s['niveau_libelle'],
                           'explication': s['issue'], 'conditions': s['conditions'], 'sources': s['sources'], 'limites': s['limites']})
    ctx_stat = {**_contexte_cadrage(snap), 'id': row['matter_id'], 'couverture_dossier_complete': not snap.get('couverture', {}).get('non_analyses')}
    statistique = panneau_probabilites(ctx_stat, registre_serveur=REGISTRE_HOMOLOGUE, inferer=None, maintenant=_maintenant())
    statistique['message'] = MESSAGE_STATISTIQUE
    freq = opinion.get('case_law_comparison') or {}
    frequence = {'decisions_qualifiees': freq.get('comparable_count', 0), 'favorables': freq.get('favorable_count', 0),
                 'pourcentage_observe': freq.get('observed_favorable_percent') if (freq.get('comparable_count') or 0) >= 10 else None,
                 'minimum': 10, 'sens': freq.get('meaning') or ''}
    manquants = []
    for side in ('claimant_analysis', 'defendant_analysis'):
        for p in op.get(side) or []:
            manquants += [m for m in p.get('missing_information') or [] if m not in manquants]
    res = {'titre': op.get('title') or 'Simulation interne', 'synthese': op.get('executive_summary') or '', 'mode': mode, 'scenarios': scenarios,
           'issues': issues, 'points_changeants': [{'facteur': x['factor'], 'si_present': x['if_present'], 'si_absent': x['if_absent']}
                                                    for x in op.get('sensitivity_analysis') or []],
           'questions_restantes': list(dict.fromkeys((op.get('questions_for_lawyer') or []) + manquants)),
           'critique_appel': op.get('appeal_critique') if snap.get('stade') == 'appel' else None,
           'statistique': statistique, 'frequence': frequence, 'comparaison': _comparaison(autorites, pistes),
           'recherche': {k: recherche.get(k) for k in ('fournisseurs', 'erreurs', 'requetes', 'figee', 'base_run_id')},
           'controle': opinion['control'], 'opinion_project_id': opinion['project_id'], 'statut_avis': opinion.get('status'),
           'couverture': {**(snap.get('couverture') or {}), 'sources_transmises': len(packet), 'decisions_citables': len(officielles)},
           'ecartes_du_paquet': ecartes,
           'limites': list(op.get('limits') or []) + ['Simulation interne : ni une décision rendue, ni une prédiction ; aucune signature de magistrat.',
                                                      'Les scénarios ne sont pas rattachés une à une aux demandes par le modèle : à vérifier par demande.'],
           'effets_externes': []}
    cote = snap.get('cote_client') or ''
    if mode == 'contrer':
        nous, eux = ((op.get('defendant_analysis'), op.get('claimant_analysis')) if cote == 'defendeur' else (op.get('claimant_analysis'), op.get('defendant_analysis')))
        res['contrer'] = {
            'cote_client': COTES.get(cote, ''),
            'reponses_aux_moyens_adverses': [{'moyen': p['issue'], 'analyse': p['analysis'], 'forces_adverses': p.get('strengths') or [],
                                              'reponse_proposee': ' ; '.join(p.get('weaknesses') or []) or 'Aucune faiblesse relevée : moyen adverse à prendre au sérieux.',
                                              'preuves_et_droit': (p.get('factual_source_ids') or []) + (p.get('authority_source_ids') or []),
                                              'reserve': 'Moyen adverse solide.' if (p.get('strengths') and not p.get('weaknesses')) else ''} for p in eux or []],
            'epreuve_de_nos_arguments': [{'argument': p['issue'], 'meilleure_objection': (p.get('weaknesses') or ['Aucune objection relevée par le modèle : à vérifier.'])[0],
                                          'autres_objections': (p.get('weaknesses') or [])[1:], 'contre_objection': ' ; '.join(p.get('strengths') or []),
                                          'decisions_contraires': p.get('contrary_authority_source_ids') or [],
                                          'reserve': '' if cote else 'Côté du client à préciser dans le cadrage.'} for p in nous or []]}
    if mode == 'ameliorer':
        decisifs = ' '.join(c for s in scenarios for c in s['conditions'] + s['facteurs']).lower()
        actions = []
        for x in opinion.get('improvement_actions') or []:
            lacune = x['action'].split(' : ', 1)[-1]
            if lacune.lower() in decisifs:
                prio, motif = 1, 'Condition ou facteur décisif d’un scénario : obtenir cet élément peut changer l’analyse.'
            elif x['action'].startswith('Vérifier'):
                prio, motif = 2, 'Information manquante relevée dans l’analyse d’une partie.'
            else:
                prio, motif = 3, 'Choix à faire par l’avocat avant d’aller plus loin.'
            actions.append({'priorite': prio, 'action': x['action'], 'lacune': lacune, 'demande': libelle_demandes, 'question': x.get('issue') or '',
                            'sources': x.get('source_ids') or [], 'consequence': 'Peut modifier les scénarios « %s ».' % ', '.join(s['titre'] for s in scenarios),
                            'effort': 'À estimer par le cabinet', 'responsable': 'À désigner', 'condition': 'Accord de l’avocat avant toute démarche',
                            'projet': 'Projet de question au client (rien n’est envoyé) : pouvez-vous nous transmettre tout élément établissant « %s » ?' % lacune,
                            'motif_priorite': motif, 'effets_externes': []})
        actions.sort(key=lambda a: (a['priorite'], a['action']))
        for i, a in enumerate(actions, 1):
            a['rang'] = i
        res['ameliorer'] = {'actions': actions, 'note': 'Priorité motivée, sans score : aucune action n’est lancée sans votre choix.'}
    if mode == 'hypothese':
        base = desk.db.execute('SELECT result_json FROM simulation_runs56109 WHERE id=?', (row['base_run_id'],)).fetchone()
        base_res = json.loads(base['result_json'] or '{}') if base else {}
        res['ecarts'] = [{'operation': x['operation'], 'id': x['id'], 'source_ids_exclus': x.get('source_ids_exclus', []),
                          'avant': {k: x['avant'].get(k) for k in ('nom', 'texte', 'objet', 'statut', 'montant') if k in x['avant']},
                          'apres': {k: x['apres'].get(k) for k in ('nom', 'texte', 'objet', 'statut', 'montant', 'incluse_scenario') if k in x['apres']}}
                         for x in scenario['ecarts']]
        res['comparaison_base'] = {'base_run_id': row['base_run_id'],
                                   'base': [{'titre': s['titre'], 'niveau_libelle': s['niveau_libelle']} for s in base_res.get('scenarios') or []],
                                   'hypothese': [{'titre': s['titre'], 'niveau_libelle': s['niveau_libelle']} for s in scenarios],
                                   'note': 'Comparaison du raisonnement aux mêmes sources que la simulation de base ; aucun écart chiffré n’est calculé.'}
    return res


def _terminer(desk, rid, state, result=None, sources=(), opinion='', error=''):
    """Écriture conditionnelle : seulement si le traitement est encore « en cours » (une annulation l'emporte toujours).

    5.6.109 (intégration I1) : ``result_json`` (colonne exportée) est écrit MASQUÉ (``_sans_pourcentage``) : un pourcentage inventé par le modèle
    ne sort jamais dans un export. Le texte brut, s'il diffère, est gardé dans ``simulation_bruts56109`` (exclue de l'export avec motif,
    jamais restituée à l'écran ni au livrable)."""
    brut = json.dumps(result or {}, ensure_ascii=False, default=str)
    masque = json.dumps(_sans_pourcentage(json.loads(brut)), ensure_ascii=False)
    quand = _maintenant()
    n = desk.db.execute("UPDATE simulation_runs56109 SET state=?, result_json=?, sources_json=?, opinion_project_id=?, error=?, version=version+1, updated=? "
                        "WHERE id=? AND state='running'", (state, masque, json.dumps(list(sources)), opinion, error, quand, rid)).rowcount
    if n and masque != brut:
        desk.db.execute('INSERT OR REPLACE INTO simulation_bruts56109(run_id, matter_id, result_brut_json, ecrit_le) '
                        'SELECT id, matter_id, ?, ? FROM simulation_runs56109 WHERE id=?', (brut, quand, rid))
    desk.db.commit()
    return bool(n)


def _autorise(desk, row):
    params = json.loads(row['parameters_json'] or '{}')
    try:
        a = acces5697.acteur_de((row['actor'], params.get('role') or 'avocat'))
    except Stop:
        return False
    return acces5697.peut_lire_dossier(desk, a, row['matter_id'])


def executer(desk, args):
    """Travail ``prepare_legal_opinion`` portant ``simulation56109`` : exécuté par le moteur commun (pause, annulation, budget, reprise)."""
    from .pause5629 import checkpoint as controle
    from .reprise56109 import arret_central
    rid = str(args.get('simulation56109') or '')
    row = desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()
    if not row:
        return {'run_id': rid, 'ignored': 'simulation_absente'}
    if row['state'] not in ('queued', 'running'):
        return {'run_id': rid, 'state': row['state'], 'ignored': True, 'message': 'Simulation déjà ' + ETATS.get(row['state'], row['state']).lower() + '.'}

    def checkpoint():
        controle(desk)
        st = desk.db.execute('SELECT state FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone()
        if not st or st['state'] == 'cancelled':
            raise Stop('operation_annulee')
    checkpoint()
    desk.db.execute("UPDATE simulation_runs56109 SET state='running', updated=? WHERE id=? AND state IN ('queued','running')", (_maintenant(), rid))
    desk.db.commit()
    try:
        return _pipeline(desk, desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=?', (rid,)).fetchone(), checkpoint)
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        if isinstance(ex, Stop) and arret_central(ex):
            # pause ou plage horaire : le traitement redevient « en file » et sera repris avec le travail ; annulation : état déjà durable
            if str(ex).split(':')[0] != 'operation_annulee':
                desk.db.execute("UPDATE simulation_runs56109 SET state='queued', updated=? WHERE id=? AND state='running'", (_maintenant(), rid))
                desk.db.commit()
            raise
        _terminer(desk, rid, 'failed', {}, error=str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne')
        raise


def _pipeline(desk, row, checkpoint):
    from . import opinions
    services = _services(desk)
    rid, did, mode = row['id'], row['matter_id'], row['mode']
    snap = json.loads(row['snapshot_json'])
    mods = json.loads(row['modifications_json'] or '[]')
    if not _autorise(desk, row):
        _terminer(desk, rid, 'failed', {}, error='droits_retires')
        return {'run_id': rid, 'state': 'failed', 'error': 'droits_retires'}
    cad = cadrer(_contexte_cadrage(snap))
    questions = list(cad['questions'])
    jugement = snap.get('jugement') if snap.get('stade') == 'appel' else None
    if jugement and jugement.get('path') and not questions:
        try:
            actuel = _jugement(desk, did, jugement['path'])
        except Stop:
            actuel = {}
        if actuel.get('sha256') != jugement.get('sha256') or actuel.get('version') != jugement.get('version'):
            questions.append('jugement_modifie_depuis_le_cadrage')
    if questions:
        _terminer(desk, rid, 'needs_information', {'libelle': 'Analyse préparatoire', 'questions': questions,
                                                   'questions_libelles': [QUESTIONS.get(q, q) for q in questions], 'effets_externes': []})
        return {'run_id': rid, 'state': 'needs_information', 'status': 'blocked', 'questions': questions}
    scenario = preparer(_atelier(snap), mode, mods)
    checkpoint()
    if mode == 'hypothese':
        base = desk.db.execute('SELECT sources_json FROM simulation_runs56109 WHERE id=?', (row['base_run_id'],)).fetchone()
        ids = json.loads(base['sources_json'] or '[]') if base else []
        autorites, pistes = _autorites(desk, did, ids=tuple(ids)), []
        recherche = {'fournisseurs': [], 'erreurs': [], 'requetes': [], 'figee': True, 'base_run_id': row['base_run_id']}
    else:
        recherche = _rechercher(desk, did, snap, services, checkpoint)
        autorites, pistes = _autorites(desk, did, query_ids=tuple(recherche['query_ids'])), _pistes(desk, recherche['query_ids'])
    checkpoint()
    packet, ecartes, decision_id = _paquet(desk, did, scenario, autorites, jugement)
    citables = [x for x in packet if x.get('officially_verified')]
    manque = []
    if not any(x['kind'].startswith('fiche_') for x in packet):
        manque.append('sources_dossier_absentes')
    if not citables:
        manque.append('jurisprudence_officielle_verifiee_absente')
    if manque:
        _terminer(desk, rid, 'needs_information', {'libelle': 'Analyse préparatoire', 'questions': manque, 'questions_libelles': [QUESTIONS.get(q, q) for q in manque],
                                                   'comparaison': _comparaison(autorites, pistes), 'recherche': {k: recherche.get(k) for k in ('fournisseurs', 'erreurs', 'requetes', 'figee')},
                                                   'effets_externes': []})
        return {'run_id': rid, 'state': 'needs_information', 'status': 'blocked', 'questions': manque}
    question = snap.get('question') or 'Quelle issue pour les demandes examinées du dossier ?'
    regles = {'simulation_mode': mode, 'simulation_internal_only': True, 'no_magistrate_signature': True, 'per_claim_reasoning': True,
              'hypothesis_is_not_a_fact': mode == 'hypothese', 'contradictory_review': mode == 'contrer', 'improvement_actions_without_sending': mode == 'ameliorer'}
    opinion = opinions.preparer_avec_paquet(desk, {'id': did, 'client_name': '', 'path': ''}, question, packet, bool(jugement), model=services.get('model'),
                                            control_model=services.get('control_model'), research_summary={k: recherche.get(k) for k in ('fournisseurs', 'erreurs', 'figee')},
                                            expected_decision_id=decision_id, extra_rules=regles, checkpoint=checkpoint, simulation={'run_id': rid, 'mode': mode})
    checkpoint()
    res = _resultat(desk, row, snap, scenario, opinion, recherche, packet, ecartes, autorites, pistes)
    state = 'blocked' if opinion['control']['status'] == 'blocked' else ('partial' if recherche['erreurs'] else 'completed')
    if not _autorise(desk, row):
        _terminer(desk, rid, 'failed', {}, error='droits_retires')
        return {'run_id': rid, 'state': 'failed', 'error': 'droits_retires'}
    ecrit = _terminer(desk, rid, state, res, [x['authority_id'] for x in citables], opinion['project_id'])
    desk.audit('simulation56109_terminee', {'matter': did, 'run': rid, 'state': state if ecrit else 'ignoree_apres_annulation', 'numeric_probability': False,
                                            'external_actions': 0})
    return {'run_id': rid, 'state': state if ecrit else 'cancelled', 'project_id': opinion['project_id'],
            'status': 'blocked' if state == 'blocked' else 'prepared', 'message': 'Simulation ' + ETATS[state].lower() + '.'}


# ------------------------------------------------------------------------------------------------ onglet du dossier
def _q(did, **extra):
    return '/dossier?' + urlencode({'id': did, 'vue': 'simulation', **{k: v for k, v in extra.items() if v}})


def lien_onglet(desk, prefix, m, view, acteur=None):
    """Lien de l'onglet dans l'en-tête du dossier : contentieux et postulation, nouvelle interface active ; jamais une erreur de page."""
    try:
        acteur = acteur or _acteur_courant()
        from .interface56100 import active
        if not active(desk, str(acteur[0] or 'cabinet')):
            return ''
        a, did = _acces(desk, acteur, m['id'])
        if not eligibilite(desk, did)['eligible']:
            return ''
    except Exception:   # noqa: BLE001 — un onglet facultatif ne casse jamais l'en-tête
        return ''
    return '<a href="%s"%s>Simulation de décision</a>' % (e(prefix + _q(did), quote=True), ' aria-current="page"' if view == 'simulation' else '')


def _cle():
    return uuid.uuid4().hex


def _liste(items):
    items = [x for x in items if str(x).strip()]
    return ('<ul class="s109-liste">%s</ul>' % ''.join('<li>%s</li>' % e(str(x)) for x in items)) if items else '<p class="ax-muted">Rien à signaler.</p>'


def _form(prefix, did, route, champs, bouton, cls='ax-btn', extra=''):
    caches = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in champs.items())
    return ('<form class="m5-form s109-form" data-api="%s" data-reload>%s<input type="hidden" name="cle" value="%s">%s'
            '<button class="%s" type="submit">%s</button></form>') % (e(PREFIXE + did + '/' + route, quote=True), caches, _cle(), extra, cls, e(bouton))


def _cadrage_html(prefix, did, ctx):
    cad, snap = ctx['cadrage'], ctx['instantane'] or {}
    decisions = [d for d in snap.get('documents') or [] if d['nature'] in NATURES_DECISION]
    j = (cad.get('jugement') or {}).get('path', '')
    dec = (cad.get('declaration') or {}).get('path', '')

    def options(valeurs, choisi, vide='— Choisir —'):
        return '<option value="">%s</option>' % e(vide) + ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == choisi else '', e(v))
                                                                  for k, v in valeurs)
    demandes = ''.join('<label class="s109-case"><input type="checkbox" name="demande:%s"%s> %s</label>' % (
        e(d['id'], quote=True), ' checked' if d.get('examinee', True) else '', e(d['objet'][:200])) for d in snap.get('demandes') or [])
    corps = ('<label>Question à examiner<textarea name="question" maxlength="2000" rows="2">%s</textarea></label>'
             '<div class="s109-grille"><label>Stade<select name="stade">%s</select></label><label>Rôle du cabinet<select name="role_cabinet">%s</select></label>'
             '<label>Côté du client<select name="cote_client">%s</select></label></div>'
             '<fieldset><legend>Demandes examinées</legend>%s</fieldset>'
             '<fieldset><legend>Appel : jugement et périmètre (confirmés par le cabinet)</legend>'
             '<label>Jugement ou ordonnance de première instance<select name="jugement">%s</select></label>'
             '<label>Déclaration d’appel<select name="declaration">%s</select></label>'
             '<label>Chefs du jugement critiqués (un par ligne)<textarea name="chefs" rows="3" maxlength="15000">%s</textarea></label></fieldset>'
             '<fieldset><legend>Postulation</legend><label class="s109-case"><input type="checkbox" name="conclusions_fond"%s> Conclusions au fond disponibles</label>'
             '<label class="s109-case"><input type="checkbox" name="instructions_correspondant"%s> Instructions du correspondant disponibles</label></fieldset>') % (
        e(cad.get('question') or ''), options(STADES.items(), cad.get('stade') or snap.get('stade') or '', 'À préciser'),
        options([(k, v) for k, v in ROLES_CABINET.items() if k], cad.get('role_cabinet') or '', 'À confirmer'),
        options([(k, v) for k, v in COTES.items() if k], cad.get('cote_client') or '', 'À préciser'),
        demandes or '<p class="ax-muted">Aucune demande dans la fiche du dossier.</p>',
        options([(d['path'], d['nom']) for d in decisions], j, 'Aucun (pas d’appel)'),
        options([(d['path'], d['nom']) for d in snap.get('documents') or []], dec, 'Aucune'),
        e('\n'.join(cad.get('chefs') or [])), ' checked' if cad.get('conclusions_fond') else '', ' checked' if cad.get('instructions_correspondant') else '')
    return ('<details class="s109-cadrage"><summary class="ax-btn">Préparer la simulation</summary>'
            '<p class="ax-muted">Cadrage prérempli depuis la fiche : corrigez ce qui manque ou paraît erroné. L’IA ne déduit jamais le périmètre de l’appel d’un nom de fichier.</p>%s</details>') % (
        _form(prefix, did, 'cadrage', {'version': cad['version']}, 'Enregistrer le cadrage', extra=corps))


def _sources_html(res, snap):
    lignes = []
    for x in (res or {}).get('comparaison') or []:
        statut = 'Vérifiée sur source officielle' if x['statut'] == 'verifiee' else 'Piste à vérifier (non citée)'
        lien = ('<a href="%s" rel="noopener noreferrer" target="_blank">texte officiel</a>' % e(x['url_officielle'], quote=True)) if x.get('url_officielle') else ''
        lignes.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class="s109-statut s109-%s">%s</span> %s</td></tr>' % (
            e(x.get('identifiant') or ''), e(x.get('juridiction') or ''), e(x.get('date') or ''), e(', '.join(p for p in x.get('provenances') or [] if p)),
            e(x['statut'], quote=True), e(statut), lien))
    tableau = ('<div class="s109-defile"><table class="s109-table"><caption>Décisions comparées</caption><thead><tr><th scope="col">Identifiant</th>'
               '<th scope="col">Juridiction</th><th scope="col">Date</th><th scope="col">Fournisseurs</th><th scope="col">Statut</th></tr></thead><tbody>%s</tbody></table></div>'
               % ''.join(lignes)) if lignes else '<p class="ax-muted">Aucune décision comparée pour l’instant.</p>'
    pieces = ''.join('<li>%s <span class="ax-muted">(%s)</span></li>' % (e(d['nom']), e(d['lecture'])) for d in (snap or {}).get('documents') or [])
    return ('<details class="s109-sources" id="s109-sources"><summary>Sources</summary>%s<h4>Pièces du dossier</h4><ul class="s109-liste">%s</ul></details>'
            % (tableau, pieces or '<li class="ax-muted">Aucune pièce.</li>'))


def _resultat_html(res, row):
    if not res:
        return ''
    if row['state'] == 'needs_information':
        return ('<section class="s109-bloc s109-prepa"><h3>Analyse préparatoire</h3><p>La simulation complète n’est pas rédigée : il manque des éléments essentiels.</p>%s</section>'
                % _liste(res.get('questions_libelles') or res.get('questions') or []))
    out = ''
    if row['state'] == 'blocked':
        out += ('<p class="s109-alerte" role="alert">Projet bloqué par le contrôle : %s. Les passages chiffrés écrits par le modèle sont masqués.</p>'
                % e(', '.join(res.get('controle', {}).get('blocking_reasons') or []) or 'contrôle non satisfait'))
    out += '<section class="s109-bloc"><h3>Conclusion provisoire</h3><p class="s109-interne">Simulation interne — projet à relire, ce n’est pas une décision rendue.</p><p>%s</p></section>' % e(res.get('synthese') or '')
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td><span class="s109-niveau s109-n-%s">%s</span> <span class="ax-muted">%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(x['demande']), e(x['issue']), e(x['estimation_affichee']), e(x['niveau'], quote=True), e(x['niveau_libelle']),
        e(next((s['motif_niveau'] for s in res.get('scenarios') or [] if s['titre'] == x['issue']), '')), e(x['explication']), e(', '.join(x['conditions'])),
        e(', '.join(x['limites']))) for x in res.get('issues') or [])
    out += ('<section class="s109-bloc"><h3>Issues possibles</h3><div class="s109-defile"><table class="s109-table"><thead><tr><th scope="col">Demande concernée</th>'
            '<th scope="col">Issue</th><th scope="col">Estimation en pourcentage</th><th scope="col">Appréciation</th><th scope="col">Explication</th>'
            '<th scope="col">Conditions déterminantes</th><th scope="col">Limites</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p class="s109-stat">%s</p>%s</section>') % (lignes, e(MESSAGE_STATISTIQUE), _frequence_html(res.get('frequence') or {}))
    out += '<section class="s109-bloc"><h3>Points qui changeraient l’analyse</h3>%s</section>' % _liste(
        '%s — présent : %s ; absent : %s' % (p['facteur'], p['si_present'], p['si_absent']) for p in res.get('points_changeants') or [])
    out += '<section class="s109-bloc"><h3>Questions restantes</h3>%s</section>' % _liste(res.get('questions_restantes') or [])
    if res.get('contrer'):
        c = res['contrer']
        out += '<section class="s109-bloc"><h3>Contrer l’argumentation adverse</h3><h4>Réponse à chaque moyen adverse</h4>%s<h4>Éprouver nos arguments</h4>%s</section>' % (
            _liste('%s — réponse proposée : %s %s' % (x['moyen'], x['reponse_proposee'], x['reserve']) for x in c['reponses_aux_moyens_adverses']),
            _liste('%s — meilleure objection : %s ; contre-objection : %s %s' % (x['argument'], x['meilleure_objection'], x['contre_objection'] or '—', x['reserve'])
                   for x in c['epreuve_de_nos_arguments']))
    if res.get('ameliorer'):
        out += '<section class="s109-bloc"><h3>Améliorer le dossier : actions hiérarchisées</h3><p class="ax-muted">%s</p><ol class="s109-liste">%s</ol></section>' % (
            e(res['ameliorer']['note']), ''.join('<li><strong>%s</strong> — %s <span class="ax-muted">(%s)</span><br>%s</li>' % (
                e(a['action']), e(a['motif_priorite']), e(a['condition']), e(a['projet'])) for a in res['ameliorer']['actions']))
    if res.get('ecarts') is not None:
        out += '<section class="s109-bloc"><h3>Hypothèse : écarts avec la simulation de base</h3>%s<p class="ax-muted">%s</p></section>' % (
            _liste('%s : %s' % ({'retirer_piece': 'Pièce retirée', 'fait_non_etabli': 'Fait considéré comme non établi', 'modifier_demande': 'Demande modifiée'}
                                .get(x['operation'], x['operation']), x['avant'].get('nom') or x['avant'].get('texte') or x['avant'].get('objet') or x['id'])
                   for x in res['ecarts']), e(res.get('comparaison_base', {}).get('note', '')))
    erreurs = res.get('recherche', {}).get('erreurs') or []
    if erreurs:
        out += '<p class="s109-alerte">Recherche incomplète : %s</p>' % e(', '.join('%s (%s)' % (x.get('provider') or 'source', x.get('error') or '') for x in erreurs))
    envoyees = res.get('recherche', {}).get('requetes') or []
    if envoyees:
        out += '<details class="s109-technique"><summary>Requêtes envoyées aux connecteurs (anonymisées)</summary>%s</details>' % _liste(envoyees)
    out += '<details class="s109-technique"><summary>Détails techniques (contrôles)</summary><pre>%s</pre></details>' % e(json.dumps(res.get('controle') or {}, ensure_ascii=False, indent=1))
    return out


def _frequence_html(f):
    if f.get('pourcentage_observe') is not None:
        return '<p class="ax-muted">Fréquence descriptive des décisions qualifiées par l’avocat : %s favorables sur %s (sélection non représentative, pas une chance de succès).</p>' % (
            f['favorables'], f['decisions_qualifiees'])
    return '<p class="ax-muted">Fréquence descriptive : série insuffisante (moins de dix décisions qualifiées).</p>'


def _hypothese_html(prefix, did, ctx, base):
    snap = json.loads(base['snapshot_json'])
    pieces = ''.join('<label class="s109-case"><input type="checkbox" name="retirer_piece:%s"> Retirer la pièce « %s » (version %s)</label>' % (
        e(d['id'], quote=True), e(d['nom']), e(d['version'][:12])) for d in snap.get('documents') or [])
    faits = ''.join('<label class="s109-case"><input type="checkbox" name="fait_non_etabli:%s"> Considérer comme non établi : %s</label>' % (
        e(f['id'], quote=True), e(f['texte'][:200])) for f in snap.get('faits') or [] if f['statut'] == 'etabli')
    demandes = ''.join('<label>Montant hypothétique — %s<input name="montant:%s" inputmode="decimal" maxlength="20" placeholder="%s"></label>' % (
        e(d['objet'][:160]), e(d['id'], quote=True), e(d.get('montant') or '')) for d in snap.get('demandes') or [] if d.get('examinee', True))
    corps = '<fieldset><legend>Pièces</legend>%s</fieldset><fieldset><legend>Faits</legend>%s</fieldset><fieldset><legend>Demandes</legend>%s</fieldset>' % (
        pieces or '—', faits or '—', demandes or '—')
    return ('<p class="ax-muted">Hypothèse sur une copie de la simulation de base du %s : le dossier réel n’est pas modifié ; les sources de la base sont réutilisées.</p>%s'
            % (e(_jour(base['created'])), _form(prefix, did, 'simuler', {'mode': 'hypothese', 'version': ctx['cadrage']['version'], 'base_run_id': base['id']},
                                                  'Tester cette hypothèse', extra=corps)))


def _historique_html(desk, prefix, did, runs, peut_agir):
    lignes = []
    for r in runs[:20]:
        actions = ''
        if peut_agir and r['state'] in ('queued', 'running'):
            actions += _form(prefix, did, 'runs/%s/annuler' % r['id'], {}, 'Annuler', 'ax-btn ghost')
        if peut_agir and r['state'] in AVEC_RESULTAT:
            actions += _form(prefix, did, 'runs/%s/conserver' % r['id'], {}, 'Conserver comme livrable', 'ax-btn ghost') if not r['livrable_id'] else (
                '<a href="%s">Livrable conservé</a>' % e(prefix + '/livrables/' + r['livrable_id'], quote=True))
            v = desk.db.execute('SELECT COALESCE(MAX(version),0) FROM simulation_corrections56109 WHERE run_id=?', (r['id'],)).fetchone()[0]
            actions += ('<details class="s109-corriger"><summary>Corriger</summary>%s</details>'
                        % _form(prefix, did, 'runs/%s/corriger' % r['id'], {'version': v}, 'Enregistrer la correction', 'ax-btn ghost',
                                '<label>Correction de l’avocat<textarea name="texte" rows="3" maxlength="20000"></textarea></label>'))
        lignes.append('<li><span class="s109-etat s109-e-%s">%s</span> %s — %s %s</li>' % (e(r['state'], quote=True), e(r['etat']), e(r['mode_libelle']),
                                                                                          e(_jour(r['created'])), actions))
    return ('<details class="s109-historique"><summary>Simulations précédentes et versions (%d)</summary><ul class="s109-liste">%s</ul></details>' % (len(runs), ''.join(lignes))
            if lignes else '')


def onglet_html(desk, auth, prefix, m, args, acteur=None):
    """Contenu de l'onglet. Lecture seule : aucun appel au modèle, aucune recherche, aucune écriture (R05) ; utilisable IA en pause."""
    acteur = acteur or _acteur_courant()
    try:
        a, did = _acces(desk, acteur, m['id'])
        ctx = contexte(desk, a, did)
    except Stop as ex:
        code = str(ex).split(':')[0]
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(code) or 'Simulation indisponible.')
    if not ctx['eligible']:
        return ('<section class="s109-onglet"><h2>Simulation de décision</h2><p>La simulation de décision est réservée aux dossiers de contentieux et de postulation. '
                'Modifiez la nature ou le profil du dossier dans sa fiche si nécessaire.</p></section>')
    action = str(args.get('action') or 'en_etat')
    snap = ctx['instantane']
    runs = ctx['simulations']
    peut_agir = a.role in ROLES_ACTION
    derniere = next((r for r in runs if r['mode'] != 'hypothese'), None)
    couv = snap['couverture']
    bandeau = ('<div class="s109-bandeau"><div><p><strong>Question :</strong> %s</p><p><strong>Stade :</strong> %s · <strong>Pièces :</strong> %d lue(s) sur %d%s · '
               '<strong>Dernière simulation :</strong> %s%s</p></div><div class="s109-actions">%s%s<a class="ax-btn ghost" href="#s109-sources">Ouvrir les sources</a></div></div>') % (
        e(snap.get('question') or 'à préciser dans le cadrage'), e(STADES.get(snap.get('stade'), 'à préciser')), couv['lus'], couv['documents'],
        (' · %d illisible(s)' % len(couv['illisibles'])) if couv['illisibles'] else '',
        e(('%s (%s)' % (_jour(derniere['created']), derniere['etat'])) if derniere else 'aucune'),
        ' <span class="s109-badge s109-perime">À actualiser</span>' if ctx['a_actualiser'] else '',
        _cadrage_html(prefix, did, ctx) if peut_agir else '',
        _form(prefix, did, 'simuler', {'mode': 'en_etat', 'version': ctx['cadrage']['version']}, 'Actualiser', 'ax-btn ghost') if peut_agir and ctx['cadrage']['version'] else '')
    pause = ('<p class="s109-pause" role="status">IA en pause : les simulations déjà produites restent consultables, corrigeables et exportables ; '
             'une nouvelle demande attendra la reprise.</p>') if ctx['pause_ia'] else ''
    prepa = ''
    if ctx['cadrage_etat'] and not ctx['cadrage_etat']['simulation_finale_autorisee']:
        prepa = ('<div class="s109-prepa"><p><strong>Analyse préparatoire</strong> : la simulation complète attend ces éléments.</p>%s</div>'
                 % _liste(QUESTIONS.get(q, q) for q in ctx['cadrage_etat']['questions']))
    nav = '<nav class="s109-nav" aria-label="Actions de simulation">%s</nav>' % ''.join(
        '<a href="%s"%s>%s</a>' % (e(prefix + _q(did, action=k), quote=True), ' aria-current="page"' if k == action else '', e(v)) for k, v in ONGLETS)
    if action not in MODES:
        corps = '<p class="d79-warn" role="alert">Action inconnue : choisissez l’une des quatre actions de simulation.</p>'
        row = None
    else:
        lancer = ''
        if peut_agir:
            if action == 'hypothese':
                base_row = next((desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=?', (r['id'],)).fetchone() for r in runs
                                 if r['mode'] != 'hypothese' and r['state'] in AVEC_RESULTAT), None)
                lancer = _hypothese_html(prefix, did, ctx, base_row) if base_row else '<p class="ax-muted">Lancez d’abord une simulation en l’état : l’hypothèse se compare à elle.</p>'
            else:
                lancer = _form(prefix, did, 'simuler', {'mode': action, 'version': ctx['cadrage']['version']}, LIBELLES[action])
        r = next((x for x in runs if x['mode'] == action), None)
        row = desk.db.execute('SELECT * FROM simulation_runs56109 WHERE id=?', (r['id'],)).fetchone() if r else None
        res = _sans_pourcentage(json.loads(row['result_json'] or '{}')) if row else {}
        etat = ('<p class="s109-suivi" role="status">%s — %s%s</p>' % (e(LIBELLES[action]), e(ETATS.get(row['state'], row['state'])),
                                                                    (' (%s)' % e(row['error'])) if row['error'] else '')) if row else ''
        corps = '<div class="s109-lancer">%s</div>%s%s' % (lancer, etat, _resultat_html(res, row) if row else
                                                          '<p class="ax-muted">Aucun résultat pour cette action. Estimation en pourcentage : Non estimable de façon fiable.</p>')
    sources = _sources_html(_sans_pourcentage(json.loads(row['result_json'] or '{}')) if row else {}, snap)
    return ('<section class="s109-onglet" aria-labelledby="s109-titre"><h2 id="s109-titre">Simulation de décision</h2>%s%s%s%s'
            '<div class="s109-corps"><div class="s109-resultat">%s</div>%s</div>%s<p class="s109-stat">%s</p></section>') % (
        bandeau, pause, prepa, nav, corps, sources, _historique_html(desk, prefix, did, runs, peut_agir), e(MESSAGE_STATISTIQUE))


def page_dossier(desk, auth, prefix, args, shell, m):
    """Page du dossier, onglet « Simulation de décision » : même en-tête, même volet d'aperçu, mêmes scripts que les autres onglets."""
    from . import dossier5679 as P
    from .common import matter_display
    body = P.header_html(desk, prefix, m, 'simulation')
    contenu = P._safe('Simulation de décision', onglet_html, desk, auth, prefix, m, args)
    body += '<div class="d79-layout"><div class="d79-main">%s</div>%s</div></div>' % (contenu, P.PREVIEW)
    head = ''.join('<script defer src="%s/static/%s"></script>' % (e(prefix), n) for n in ('v5627.js', 'v5679.js', 'v5698-fiche.js'))
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)


# ------------------------------------------------------------------------------------------------ routes
def _route(api_):
    route = str(api_ or '').split('?', 1)[0]
    if not route.startswith(PREFIXE):
        return None
    return [p for p in route[len(PREFIXE):].split('/') if p]


def allowed(role, method, api_):
    """Contrôle de rôle sans état (``standalone_auth``) : lectures pour les trois rôles ; préparer, annuler, corriger et conserver : avocat
    et administrateur. Le dossier, la simulation, la révision et la clé sont relus par le service."""
    parts = _route(api_)
    if role not in ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[1] == 'contexte') or (len(parts) == 3 and parts[1] == 'runs')
    if method != 'POST' or role not in ROLES_ACTION:
        return False
    return (len(parts) == 2 and parts[1] in ('cadrage', 'simuler')) or (len(parts) == 4 and parts[1] == 'runs' and parts[3] in ECRITURES_RUN)


def _ecriture_connue(parts):
    return (len(parts) == 2 and parts[1] in ('cadrage', 'simuler')) or (len(parts) == 4 and parts[1] == 'runs' and parts[3] in ECRITURES_RUN)


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        statut = STATUTS.get(code) or ('422 Unprocessable Entity' if code in MESSAGES or code.endswith(('_inconnu', '_inconnue', '_invalide', '_invalides', '_vide', '_refuses'))
                                       else '400 Bad Request')
        return _out({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, statut)
    except sqlite3.OperationalError:   # base occupée : panne annoncée, jamais un accès élargi ni un faux succès
        return _out({'error': 'registre_occupe', 'message': MESSAGES['registre_occupe']}, '503 Service Unavailable')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    acteur = (owner, role)
    parts = _route(name)
    if parts is None or not parts:
        raise Stop('route_inconnue')
    if not allowed(role, method, name):
        if role not in ROLES or (method == 'POST' and _ecriture_connue(parts)):
            raise Stop('role_insuffisant')
        raise Stop('route_inconnue')
    did = parts[0]
    if method in ('GET', 'HEAD'):
        if parts[1] == 'contexte':
            return _out(_sans_pourcentage(contexte(desk, acteur, did)))
        return _out(lire_run(desk, acteur, did, parts[2]))
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné, objet JSON
    cle = data.get('cle')
    if parts[1] == 'cadrage':
        return _out(enregistrer_cadrage(desk, acteur, did, data, cle))
    if parts[1] == 'simuler':
        recu, nouveau = simuler(desk, acteur, did, data, cle)
        return _out(recu, '202 Accepted' if nouveau else '200 OK')
    rid, genre = parts[2], parts[3]
    if genre == 'annuler':
        return _out(annuler(desk, acteur, did, rid, cle))
    if genre == 'corriger':
        return _out(corriger(desk, acteur, did, rid, data, cle))
    return _out(conserver(desk, acteur, did, rid, cle))
