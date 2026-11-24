"""5.6.109 (lot D, F05) : catalogue de prestations du cabinet et chiffrage déterministe des honoraires.

Les montants suggérés par un modèle ne tiennent plus lieu de tarif du cabinet. Le cabinet décrit ses prestations dans Paramètres › Cabinet ›
Prestations et honoraires (forfait, temps passé, mixte, assistance régulière ; champs, conditions et options, diligences incluses et exclues,
provision, TVA), les publie, et **le logiciel calcule** :

- **brouillon → publication** : une prestation nouvelle, dupliquée, importée ou tirée de la bibliothèque reste un brouillon ; la publication
  (administrateur ou avocat qui a le droit « réglages ») valide TOUTES les branches (types, bornes, règles en double, critère inconnu, prix
  manquant, TVA) et fige une **version immuable** (``catalogue_versions56109`` : aucun UPDATE ni DELETE possible, déclencheurs SQLite).
  Modifier une prestation publiée produit une nouvelle version ; les devis et conventions déjà établis gardent la leur ;
- **calcul** (``evaluer``) : montants en centimes entiers, pourcentages en ``Decimal``, arrondi à la moitié supérieure, jamais de flottant ni
  d'évaluation de code. Inconnu, non, zéro et « non applicable » restent distincts : un critère déterminant inconnu donne des **questions**,
  jamais un forfait ferme ; un prix absent donne « à chiffrer », jamais zéro ; zéro n'existe qu'avec la gratuité explicite et motivée ;
  une TVA non confirmée reste inconnue (aucun TTC) ; les pourcentages portent sur le forfait de base (aucune capitalisation) ;
- **priorité contractuelle** (``tarif_applicable``) : convention ou lettre de mission signée (preuve enregistrée par l'avocat), devis accepté,
  montant retenu par l'avocat, version publiée du profil de l'avocat, catalogue du cabinet, sinon « à chiffrer ». Une convention signée
  interrompt tout nouveau chiffrage : son prix n'est jamais remplacé ;
- **IA** : le modèle ne reçoit que la vue client (``vue_client``, ``texte_client``) des prestations publiées et marquées « utilisable par
  l'IA » (désactivé par défaut) ; ni coût interne ni note interne ; ``montants_hors_tarif`` relève tout montant d'un texte qui ne vient pas
  du calcul. Un courriel ne modifie jamais le catalogue ;
- **bibliothèque** : 77 trames d'amorçage sans prix (``data/prestations56109.json``), inactives ; le cabinet en fait un brouillon par un
  geste explicite. Les 354 diligences Honorium et les estimations de ``honoraires5653`` sont conservées : ce catalogue les complète, une
  proposition chiffrée y devient une estimation (origine « catalogue du cabinet », à confirmer par l'avocat).

Droits : voir (« voir_honoraires »), préparer une proposition (« facturation »), gérer et publier le catalogue (« réglages » ; publier :
jamais un assistant). Dossier absent ou réservé : même 404 neutre. Tables créées au démarrage (``Desk``) ; aucune écriture pendant un GET ;
tout fonctionne IA en pause. Routes : ``m56109/honoraires/…``.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .common import Stop

PREFIXE = 'm56109/honoraires/'
MOTEUR = 'catalogue56109/1'
FICHIER_TRAMES = Path(__file__).resolve().parent / 'data' / 'prestations56109.json'
REGLAGE_CATEGORIES = 'catalogue56109:categories'
MODES = {'forfait': 'Forfait', 'temps': 'Au temps passé', 'mixte': 'Mixte (forfait et temps)', 'abonnement': 'Assistance régulière (forfait par période)'}
TYPES_MISSION = {'': 'Non précisé', 'contentieux': 'Contentieux', 'conseil': 'Conseil', 'redaction': 'Rédaction d’acte', 'formalite': 'Formalités',
                 'postulation': 'Postulation', 'assistance': 'Assistance régulière', 'autre': 'Autre'}
ETATS = {'brouillon': 'Brouillon', 'publie': 'Publiée', 'archive': 'Archivée'}
TYPES_CHAMP = {'booleen': 'Oui / non', 'entier': 'Nombre', 'montant_cents': 'Montant', 'minutes': 'Durée (minutes)', 'choix': 'Choix dans une liste'}
OPERATEURS = {'eq': 'est', 'ge': 'est au moins', 'le': 'est au plus'}
ACTIONS = {'ajout_fixe': 'ajouter un montant', 'pourcentage_base': 'majorer le forfait de base (%)', 'reduction_fixe': 'déduire un montant',
           'reduction_pourcentage_base': 'réduire le forfait de base (%)', 'ajout_minutes': 'ajouter du temps prévisionnel'}
PERIODES = {'mois': 'par mois', 'trimestre': 'par trimestre', 'annee': 'par an'}
IDENT = re.compile(r'^[a-z0-9][a-z0-9-]{1,79}$')
CHAMP_ID = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,80}$')
MAX_CENTS = 10 ** 12
MAX_CHAMPS, MAX_REGLES, MAX_LIGNES = 30, 60, 40
MAX_CONDITIONS = 4   # conditions liées par ET dans une règle : l'éditeur les affiche toutes (5.6.109, I2)
STATUTS_DONNEE = ('confirme',)   # seule une donnée confirmée entre dans le calcul ; « extrait », « propose » : inconnue

SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalogue_offres56109(
  id TEXT PRIMARY KEY, etat TEXT NOT NULL DEFAULT 'brouillon', version_publiee INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
  profil_avocat TEXT NOT NULL DEFAULT '', origine TEXT NOT NULL DEFAULT 'cabinet', contenu_json TEXT NOT NULL, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
  modifie_par TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalogue_versions56109(
  offre_id TEXT NOT NULL, version INTEGER NOT NULL, profil_avocat TEXT NOT NULL DEFAULT '', debut TEXT NOT NULL DEFAULT '', fin TEXT NOT NULL DEFAULT '',
  contenu_json TEXT NOT NULL, empreinte TEXT NOT NULL, valide_par TEXT NOT NULL, valide_le TEXT NOT NULL, PRIMARY KEY(offre_id, version));
CREATE TRIGGER IF NOT EXISTS catalogue_versions56109_fige_maj BEFORE UPDATE ON catalogue_versions56109
  BEGIN SELECT RAISE(ABORT, 'version_publiee_immuable'); END;
CREATE TRIGGER IF NOT EXISTS catalogue_versions56109_fige_sup BEFORE DELETE ON catalogue_versions56109
  BEGIN SELECT RAISE(ABORT, 'version_publiee_immuable'); END;
CREATE TABLE IF NOT EXISTS catalogue_simulations56109(
  id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL DEFAULT '', demande_id TEXT NOT NULL DEFAULT '', offre_id TEXT NOT NULL, offre_version INTEGER NOT NULL,
  statut TEXT NOT NULL, parametres_json TEXT NOT NULL, resultat_json TEXT NOT NULL, empreinte TEXT NOT NULL, auteur TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS catalogue_simulations56109_d ON catalogue_simulations56109(dossier_id, cree_le);
CREATE TABLE IF NOT EXISTS catalogue_estimations56109(
  estimation_id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, simulation_id TEXT NOT NULL, offre_id TEXT NOT NULL, offre_version INTEGER NOT NULL,
  empreinte TEXT NOT NULL, cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalogue_recus56109(
  compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL, quand TEXT NOT NULL,
  PRIMARY KEY(compte, route, cle));
'''
MESSAGES = {'introuvable': 'Élément introuvable.', 'version_perimee': 'Cette prestation a été modifiée entre-temps : rechargez la page ; rien n’a été écrasé.',
            'champs_invalides': 'Certains champs sont à corriger.', 'publication_refusee': 'Publication impossible : corrigez les points signalés.',
            'cle_idempotence_requise': 'Page incomplète : rechargez-la.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
            'role_insuffisant': 'Action réservée à un autre rôle ou à un droit que vous n’avez pas.', 'route_inconnue': 'Route inconnue.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.', 'catalogue_illisible': 'Fichier illisible : un catalogue JSON est attendu.',
            'offre_non_publiee': 'Cette prestation n’a pas de version publiée applicable.', 'offre_expiree': 'La version publiée de cette prestation n’est plus applicable à cette date.',
            'offre_pas_encore_applicable': 'La version publiée de cette prestation n’est pas encore applicable à cette date.',
            'offre_archivee': 'Cette prestation est archivée : elle ne sert plus à de nouveaux chiffrages.', 'identifiant_deja_utilise': 'Cet identifiant est déjà celui d’une prestation.',
            'trame_inconnue': 'Trame inconnue de la bibliothèque.', 'trop_de_prestations': 'Trop de prestations : archivez-en avant d’en créer.'}
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'dossier_absent': '404 Not Found', 'dossier_reserve': '404 Not Found',
           'trame_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'acteur_requis': '403 Forbidden',
           'version_perimee': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'identifiant_deja_utilise': '409 Conflict', 'offre_archivee': '409 Conflict',
           'offre_non_publiee': '409 Conflict', 'offre_expiree': '409 Conflict', 'offre_pas_encore_applicable': '409 Conflict',
           'champs_invalides': '422 Unprocessable Entity', 'publication_refusee': '422 Unprocessable Entity', 'catalogue_illisible': '422 Unprocessable Entity',
           'cle_idempotence_requise': '422 Unprocessable Entity', 'controle_acces_indisponible': '503 Service Unavailable'}


class Refus(Stop):
    """Refus avec détail par champ (422) ou état courant (409)."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(valeur):
    return hashlib.sha256(json.dumps(valeur, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement ; la bibliothèque de trames n'est jamais copiée en base ici."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def jour_cabinet(desk):
    """Jour du cabinet (fuseau du cabinet), jamais la date du serveur."""
    try:
        from .dossiers56100 import jour_cabinet as jour
        return jour(desk).isoformat()
    except Exception:   # noqa: BLE001 — fuseau illisible : Europe/Paris
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()


def entier(valeur, nom, maximum=MAX_CENTS):
    """Entier positif borné ; un flottant, un booléen ou un texte est refusé (aucun flottant pour l'argent)."""
    if type(valeur) is not int or valeur < 0 or valeur > maximum:
        raise ValueError(nom + '_invalide')
    return valeur


def decimal(valeur, nom, maximum=Decimal('1000')):
    """Pourcentage décimal venu d'un texte ou d'un entier ; NaN, infini, négatif ou flottant refusés."""
    if type(valeur) not in (str, int):
        raise ValueError(nom + '_invalide')
    try:
        v = Decimal(str(valeur).strip().replace(',', '.'))
    except InvalidOperation:
        raise ValueError(nom + '_invalide') from None
    if not v.is_finite() or v < 0 or v > maximum:
        raise ValueError(nom + '_invalide')
    return v


def arrondi(valeur):
    """Arrondi monétaire explicite : au centime, moitié supérieure."""
    return int(Decimal(valeur).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def cents(texte, nom='montant', vide=None):
    """Montant saisi en euros (« 1 250,50 ») → centimes entiers ; vide → ``vide`` (prix non renseigné, jamais zéro)."""
    if texte is None or (isinstance(texte, str) and not texte.strip()):
        return vide
    if type(texte) is int:
        return entier(texte * 100, nom)
    if type(texte) is not str:
        raise ValueError(nom + '_invalide')
    brut = texte.replace(' ', '').replace('\xa0', '').replace(' ', '').replace('€', '').replace(',', '.')
    try:
        d = Decimal(brut)
    except InvalidOperation:
        raise ValueError(nom + '_invalide') from None
    if not d.is_finite() or d < 0 or d * 100 != (d * 100).to_integral_value():
        raise ValueError(nom + '_invalide')
    return entier(int(d * 100), nom)


def fmt(valeur_cents):
    from .honoraires5653 import fmt as f
    return f(valeur_cents)


# ------------------------------------------------------------------------------------------------ calcul déterministe
def _valeur(champ, info):
    """Valeur d'un critère : seulement si elle est CONFIRMÉE et du bon type ; sinon ``None`` (inconnue — jamais « non », jamais zéro)."""
    if not isinstance(info, dict) or info.get('statut') not in STATUTS_DONNEE or info.get('valeur') is None:
        return None
    v = info['valeur']
    if champ['type'] == 'booleen':
        if type(v) is not bool:
            raise ValueError('booleen_invalide')
    elif champ['type'] in ('entier', 'montant_cents', 'minutes'):
        entier(v, 'champ')
    elif champ['type'] == 'choix':
        if type(v) is not str or v not in (champ.get('valeurs') or []):
            raise ValueError('choix_invalide')
    else:
        raise ValueError('type_champ_invalide')
    return v


def evaluer(offre, donnees, *, au, accord=None):
    """Chiffre une version PUBLIÉE d'une prestation. Aucun effet, aucun réseau, aucun modèle.

    ``donnees`` : ``{critère: {'valeur', 'statut', 'source'}}`` (seules les valeurs confirmées comptent). ``accord`` : convention signée
    vérifiée ; elle interrompt le chiffrage (« accord_existant »), son prix n'étant jamais remplacé par le catalogue.
    Rend ``statut`` : ``proposition`` (calcul complet), ``a_completer`` (questions utiles, aucun total), ``a_chiffrer`` (prix non renseigné),
    ``accord_existant``. Lève ``ValueError`` pour une prestation ou une donnée invalide (jamais un calcul approximatif)."""
    if accord:
        if accord.get('statut') != 'signe' or not accord.get('source_id'):
            raise ValueError('accord_non_verifie')
        return {'statut': 'accord_existant', 'source_id': accord['source_id'],
                'message': 'Une convention signée existe : relire son périmètre et ses avenants ; son prix n’est pas remplacé par le catalogue.'}
    jour = date.fromisoformat(str(au)[:10])
    if offre.get('statut') != 'publie' or not offre.get('valide_par'):
        raise ValueError('offre_non_publiee')
    entier(offre.get('version'), 'version')
    if not offre['version'] or not offre.get('id') or offre.get('devise') != 'EUR':
        raise ValueError('offre_invalide')
    if offre.get('debut') and jour < date.fromisoformat(offre['debut']):
        raise ValueError('offre_pas_encore_applicable')
    if offre.get('fin') and jour >= date.fromisoformat(offre['fin']):
        raise ValueError('offre_expiree')
    mode = offre.get('mode')
    if mode not in MODES:
        raise ValueError('mode_invalide')
    champs = offre.get('champs') or {}
    regles = offre.get('regles') or []
    if len(champs) > 100 or len(regles) > 200:
        raise ValueError('catalogue_trop_complexe')
    donnees = donnees if isinstance(donnees, dict) else {}
    valeurs = {k: _valeur(c, donnees.get(k)) for k, c in champs.items()}
    requis = {k for k, c in champs.items() if c.get('obligatoire') and valeurs[k] is None}
    appliquees, vues, groupes = [], set(), set()
    for regle in regles:
        ident = regle['id']
        if ident in vues:
            raise ValueError('regle_en_double')
        vues.add(ident)
        conditions = regle.get('si')
        if not isinstance(conditions, list) or not conditions:
            raise ValueError('conditions_requises')
        resultats, inconnus = [], set()
        for c in conditions:
            k, op = c.get('champ'), c.get('op')
            if k not in champs or op not in OPERATEURS:
                raise ValueError('condition_invalide')
            attendu = _valeur(champs[k], {'statut': 'confirme', 'valeur': c.get('valeur')})
            if attendu is None or (op != 'eq' and champs[k]['type'] in ('booleen', 'choix')):
                raise ValueError('comparaison_invalide')
            v = valeurs[k]
            if v is None:
                resultats.append(None)
                inconnus.add(k)
            else:
                resultats.append(v == attendu if op == 'eq' else v >= attendu if op == 'ge' else v <= attendu)
        if False in resultats:        # une branche certainement fausse ne demande pas ses autres critères
            continue
        if None in resultats:
            requis.update(inconnus)
            continue
        groupe = str(regle.get('groupe') or '')
        if groupe:                    # variantes exclusives : la première règle vraie du groupe s'applique, les autres ne se cumulent pas
            if groupe in groupes:
                continue
            groupes.add(groupe)
        appliquees.append(regle)
    base = {'offre_id': offre['id'], 'offre_version': offre['version'], 'mode': mode, 'devise': 'EUR'}
    questions = [{'champ': k, 'question': champs[k].get('question') or k} for k in sorted(requis)]
    if questions:
        return {**base, 'statut': 'a_completer', 'questions': questions, 'total_ht_cents': None}
    fixe = 0
    if mode in ('forfait', 'mixte', 'abonnement'):
        if offre.get('fixe_ht_cents') is None:
            return {**base, 'statut': 'a_chiffrer', 'total_ht_cents': None, 'questions': [], 'motif': 'Le forfait de cette prestation n’est pas renseigné.'}
        fixe = entier(offre['fixe_ht_cents'], 'forfait')
    minutes, taux = 0, None
    if mode in ('temps', 'mixte'):
        if offre.get('taux_ht_cents') is None or valeurs.get('minutes_prevues') is None:
            return {**base, 'statut': 'a_chiffrer', 'total_ht_cents': None, 'questions': [],
                    'motif': 'Le taux horaire ou le temps prévisionnel de cette prestation n’est pas renseigné.'}
        taux, minutes = entier(offre['taux_ht_cents'], 'taux'), entier(valeurs['minutes_prevues'], 'minutes', 10 ** 7)
        if taux == 0:
            raise ValueError('taux_nul')
    if mode == 'abonnement':
        if offre.get('periode') not in PERIODES:
            raise ValueError('periode_requise')
        if offre.get('minutes_incluses') is not None:
            entier(offre['minutes_incluses'], 'quota', 10 ** 7)
    lignes = [{'libelle': offre['libelle'], 'montant_ht_cents': fixe, 'nature': 'base'}] if mode != 'temps' else []
    for regle in appliquees:
        a = regle.get('action') or {}
        typ = a.get('type')
        if typ in ('ajout_fixe', 'reduction_fixe'):
            if a.get('montant_ht_cents') is None:
                return {**base, 'statut': 'a_chiffrer', 'total_ht_cents': None, 'questions': [],
                        'motif': 'Le montant de la condition « %s » n’est pas renseigné.' % regle.get('libelle_client', ident)}
            montant = entier(a['montant_ht_cents'], 'supplement')
        elif typ in ('pourcentage_base', 'reduction_pourcentage_base'):
            if mode == 'temps':
                raise ValueError('assiette_absente')
            montant = arrondi(Decimal(fixe) * decimal(a.get('pct'), 'pourcentage') / 100)   # assiette : le forfait de base, jamais les suppléments
        elif typ == 'ajout_minutes':
            if mode not in ('temps', 'mixte'):
                raise ValueError('minutes_hors_mode')
            minutes += entier(a.get('minutes'), 'minutes', 10 ** 6)
            continue
        else:
            raise ValueError('action_invalide')
        if typ.startswith('reduction'):
            montant = -montant
        lignes.append({'libelle': regle['libelle_client'], 'montant_ht_cents': montant, 'nature': 'reduction' if montant < 0 else 'supplement', 'regle_id': regle['id']})
    if taux is not None:
        lignes.append({'libelle': 'Temps prévisionnel', 'montant_ht_cents': arrondi(Decimal(minutes) * taux / 60), 'nature': 'temps', 'minutes': minutes,
                       'taux_ht_cents': taux})
    total = sum(l['montant_ht_cents'] for l in lignes)
    if total < 0:
        raise ValueError('total_negatif')
    entier(total, 'total')
    if total == 0 and not (offre.get('gratuite') is True and str(offre.get('motif_gratuite') or '').strip()):
        raise ValueError('zero_sans_gratuite')
    tva = ttc = None
    if offre.get('tva_statut') == 'confirme':   # une TVA non confirmée reste inconnue : jamais zéro par défaut, jamais de TTC inventé
        pct = decimal(offre.get('tva_pct'), 'tva', Decimal('100'))
        tva = arrondi(Decimal(total) * pct / 100)
        ttc = total + tva
    provision = None
    if offre.get('provision_pct') not in (None, ''):
        provision = arrondi(Decimal(total) * decimal(offre['provision_pct'], 'provision', Decimal('100')) / 100)
    empreinte = _sha({'offre': offre, 'valeurs': valeurs, 'au': str(au)[:10], 'moteur': MOTEUR})
    # liste blanche de champs destinés au client : ni coût interne, ni marge, ni note interne
    return {**base, 'statut': 'proposition', 'moteur': MOTEUR, 'empreinte': empreinte, 'libelle': offre['libelle'], 'lignes': lignes, 'total_ht_cents': total,
            'tva_cents': tva, 'ttc_cents': ttc, 'tva_pct': str(offre.get('tva_pct')) if tva is not None else None, 'tva_statut': offre.get('tva_statut') or 'a_verifier',
            'provision_ht_cents': provision, 'gratuite': bool(offre.get('gratuite')) and total == 0,
            'inclus': list(offre.get('inclus') or []), 'exclus': list(offre.get('exclus') or []), 'modalites': str(offre.get('modalites') or ''),
            'periode': offre.get('periode') if mode == 'abonnement' else None, 'minutes_incluses': offre.get('minutes_incluses') if mode == 'abonnement' else None,
            'depassement': offre.get('depassement') if mode == 'abonnement' else None,
            'regles_appliquees': [r['id'] for r in appliquees],
            'hypotheses': [{'champ': k, 'question': champs[k].get('question') or k, 'valeur': v} for k, v in sorted(valeurs.items()) if v is not None],
            'questions': []}


def vue_client(resultat):
    """Ce qui peut être transmis au client ou au modèle de rédaction : liste blanche de champs du calcul, rien d'interne."""
    permis = ('statut', 'offre_id', 'offre_version', 'empreinte', 'libelle', 'mode', 'devise', 'lignes', 'total_ht_cents', 'tva_cents', 'ttc_cents', 'tva_pct',
              'tva_statut', 'provision_ht_cents', 'gratuite', 'inclus', 'exclus', 'modalites', 'periode', 'minutes_incluses', 'depassement', 'hypotheses', 'questions',
              'message', 'source_id', 'motif')
    out = {k: resultat[k] for k in permis if k in resultat}
    if 'lignes' in out:
        out['lignes'] = [{k: v for k, v in l.items() if k in ('libelle', 'montant_ht_cents', 'nature', 'minutes', 'taux_ht_cents')} for l in out['lignes']]
    return out


def explication(resultat):
    """Motifs lisibles du montant : base, conditions déclenchées, hypothèses, TVA, questions restantes. Texte produit par le logiciel."""
    s = resultat.get('statut')
    if s == 'accord_existant':
        return [resultat['message']]
    if s == 'a_completer':
        return ['Montant non calculé : %d information(s) déterminante(s) manquent.' % len(resultat['questions'])] + ['Question : ' + q['question'] for q in resultat['questions']]
    if s == 'a_chiffrer':
        return ['Prestation à chiffrer : ' + (resultat.get('motif') or 'prix non renseigné.') + ' Aucun montant n’est proposé.']
    out = []
    for l in resultat['lignes']:
        if l.get('nature') == 'temps':
            out.append('Temps prévisionnel : %d min à %s HT de l’heure, soit %s HT.' % (l['minutes'], fmt(l['taux_ht_cents']), fmt(l['montant_ht_cents'])))
        else:
            out.append('%s : %s HT.' % (l['libelle'], fmt(l['montant_ht_cents'])))
    out.append('Total : %s HT.' % fmt(resultat['total_ht_cents']) if not resultat.get('gratuite') else 'Mission gratuite (choix explicite du cabinet).')
    if resultat['ttc_cents'] is not None:
        out.append('TVA %s %% : %s ; total TTC : %s.' % (resultat['tva_pct'], fmt(resultat['tva_cents']), fmt(resultat['ttc_cents'])))
    else:
        out.append('TVA non confirmée pour cette prestation : aucun montant TTC n’est annoncé.')
    if resultat.get('provision_ht_cents') is not None:
        out.append('Provision : %s HT.' % fmt(resultat['provision_ht_cents']))
    if resultat.get('periode'):
        out.append('Forfait %s%s ; un dépassement donne lieu à un accord complémentaire, jamais à un débit automatique.' % (
            PERIODES[resultat['periode']], (', %d min incluses' % resultat['minutes_incluses']) if resultat.get('minutes_incluses') is not None else ''))
    for h in resultat.get('hypotheses') or []:
        out.append('Hypothèse retenue — %s : %s.' % (h['question'], 'oui' if h['valeur'] is True else 'non' if h['valeur'] is False else h['valeur']))
    return out


# ------------------------------------------------------------------------------------------------ contenu d'une prestation : normalisation et validation
def _texte(valeur, maxi, nom, err, requis=False):
    texte = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(valeur or '')).strip()
    if len(texte) > maxi:
        err[nom] = 'Texte trop long (%d caractères au plus).' % maxi
        return texte[:maxi]
    if requis and not texte:
        err[nom] = 'Champ requis.'
    return texte


def _lignes_texte(valeur, nom, err):
    lignes = valeur if isinstance(valeur, list) else str(valeur or '').replace('\r', '').split('\n')
    lignes = [re.sub(r'\s+', ' ', str(x)).strip(' -•\t')[:300] for x in lignes]
    lignes = [x for x in lignes if x]
    if len(lignes) > MAX_LIGNES:
        err[nom] = 'Trop de lignes (%d au plus).' % MAX_LIGNES
    return lignes[:MAX_LIGNES]


def _cents_ou_rien(valeur, nom, err):
    """Montant d'un contenu : entier en centimes déjà normalisé, texte en euros saisi, ou rien (non renseigné)."""
    try:
        if valeur is None or valeur == '':
            return None
        return entier(valeur, nom) if type(valeur) is int else cents(valeur, nom)
    except ValueError:
        err[nom] = 'Montant invalide (exemple : 1 250,50).'
        return None


def normaliser(d, err=None):
    """Contenu d'une prestation réduit à sa liste fermée de champs, typé et borné. Un brouillon peut rester sans prix ; les erreurs de
    structure (type inconnu, identifiant invalide, montant illisible) sont rendues par champ. Aucune expression, aucun code."""
    err = {} if err is None else err
    d = d if isinstance(d, dict) else {}
    c = {'libelle': _texte(d.get('libelle'), 160, 'libelle', err, True), 'categorie': _texte(d.get('categorie'), 60, 'categorie', err),
         'type_mission': str(d.get('type_mission') or ''), 'procedure': _texte(d.get('procedure'), 200, 'procedure', err),
         'description_interne': _texte(d.get('description_interne'), 2000, 'description_interne', err), 'mode': str(d.get('mode') or 'forfait'), 'devise': 'EUR',
         'fixe_ht_cents': _cents_ou_rien(d.get('fixe_ht_cents', d.get('fixe_ht')), 'fixe_ht', err),
         'taux_ht_cents': _cents_ou_rien(d.get('taux_ht_cents', d.get('taux_ht')), 'taux_ht', err),
         'gratuite': d.get('gratuite') in (True, 'true', 'on', '1'), 'motif_gratuite': _texte(d.get('motif_gratuite'), 300, 'motif_gratuite', err),
         'tva_statut': 'confirme' if d.get('tva_statut') in ('confirme', True, 'true', 'on', '1') else 'a_verifier', 'tva_pct': None,
         'provision_pct': None, 'modalites': _texte(d.get('modalites'), 600, 'modalites', err),
         'inclus': _lignes_texte(d.get('inclus'), 'inclus', err), 'exclus': _lignes_texte(d.get('exclus'), 'exclus', err),
         'pieces': _lignes_texte(d.get('pieces'), 'pieces', err), 'documents_modeles': _lignes_texte(d.get('documents_modeles'), 'documents_modeles', err),
         'cout_interne_cents': _cents_ou_rien(d.get('cout_interne_cents', d.get('cout_interne')), 'cout_interne', err),
         'note_interne': _texte(d.get('note_interne'), 1000, 'note_interne', err),
         'actif_pour_ia': d.get('actif_pour_ia') in (True, 'true', 'on', '1'),
         'profil_avocat': _texte(d.get('profil_avocat'), 200, 'profil_avocat', err), 'variante_de': _texte(d.get('variante_de'), 80, 'variante_de', err)}
    if c['type_mission'] not in TYPES_MISSION:
        err['type_mission'] = 'Type de mission inconnu.'
    if c['mode'] not in MODES:
        err['mode'] = 'Mode inconnu.'
    for nom, maxi in (('tva_pct', Decimal('100')), ('provision_pct', Decimal('100'))):
        if d.get(nom) not in (None, ''):
            try:
                c[nom] = format(decimal(d[nom] if type(d[nom]) is int else str(d[nom]), nom, maxi).normalize(), 'f')
            except ValueError:
                err[nom] = 'Pourcentage invalide (0 à %s).' % maxi
    if c['mode'] == 'abonnement':
        c['periode'] = str(d.get('periode') or '')
        if c['periode'] and c['periode'] not in PERIODES:
            err['periode'] = 'Période inconnue.'
        c['minutes_incluses'] = None
        if d.get('minutes_incluses') not in (None, ''):
            try:
                c['minutes_incluses'] = entier(int(d['minutes_incluses']) if not isinstance(d['minutes_incluses'], (float, bool)) else d['minutes_incluses'], 'quota', 10 ** 7)
            except (ValueError, TypeError):
                err['minutes_incluses'] = 'Nombre de minutes invalide.'
        for nom in ('depassement', 'report', 'engagement', 'resiliation', 'prestations_incluses'):
            c[nom] = _texte(d.get(nom), 400, nom, err)
        c['depassement'] = c['depassement'] or 'accord_prealable'
    c['champs'] = _champs(d, err)
    c['regles'] = _regles(d, c['champs'], err)
    return c


def _champs(d, err):
    """Critères de la prestation : dictionnaire déjà structuré (import, trame, API) ou lignes de formulaire ``champ_<n>_*``."""
    bruts = []
    if isinstance(d.get('champs'), dict):
        bruts = [{'id': k, **(v if isinstance(v, dict) else {})} for k, v in d['champs'].items()]
    else:
        for n in range(1, MAX_CHAMPS + 1):
            ident, question = str(d.get('champ_%d_id' % n) or '').strip().lower(), str(d.get('champ_%d_question' % n) or '').strip()
            if ident or question:
                bruts.append({'id': ident or re.sub(r'[^a-z0-9]+', '_', _plier(question)).strip('_')[:40], 'type': d.get('champ_%d_type' % n) or 'booleen', 'question': question,
                              'obligatoire': d.get('champ_%d_obligatoire' % n), 'valeurs': [x.strip() for x in str(d.get('champ_%d_valeurs' % n) or '').split(';') if x.strip()],
                              '_n': n})
    out = {}
    if len(bruts) > MAX_CHAMPS:
        err['champs'] = 'Trop de critères (%d au plus).' % MAX_CHAMPS
    for b in bruts[:MAX_CHAMPS]:
        cle = 'champ_%s' % (b.get('_n') or b['id'])
        ident = str(b.get('id') or '')
        if not CHAMP_ID.fullmatch(ident) or ident in out:
            err[cle + '_id'] = 'Identifiant de critère invalide ou en double (lettres minuscules, chiffres, tiret bas).'
            continue
        type_ = str(b.get('type') or '')
        if type_ not in TYPES_CHAMP:
            err[cle + '_type'] = 'Type de critère inconnu.'
            continue
        champ = {'type': type_, 'question': re.sub(r'\s+', ' ', str(b.get('question') or ''))[:200]}
        if b.get('obligatoire') in (True, 'true', 'on', '1'):
            champ['obligatoire'] = True
        if type_ == 'choix':
            champ['valeurs'] = [str(x)[:80] for x in (b.get('valeurs') or [])][:20]
            if not champ['valeurs']:
                err[cle + '_valeurs'] = 'Indiquez les valeurs possibles, séparées par des points-virgules.'
        out[ident] = champ
    return out


def _plier(texte):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', str(texte or '')) if not unicodedata.combining(c)).lower()


def _attendu(champ, brut):
    """Valeur attendue d'une condition, typée d'après son critère (« oui », « 3 », « tae »)."""
    if champ['type'] == 'booleen':
        if brut in (True, 'oui', 'true', '1', 'on'):
            return True
        if brut in (False, 'non', 'false', '0'):
            return False
        raise ValueError('booleen_invalide')
    if champ['type'] == 'choix':
        if str(brut) not in champ.get('valeurs', []):
            raise ValueError('choix_invalide')
        return str(brut)
    if type(brut) is int:
        return entier(brut, 'valeur')
    if champ['type'] == 'montant_cents':
        return cents(str(brut), 'valeur', None)
    if not re.fullmatch(r'\d{1,9}', str(brut).strip()):
        raise ValueError('nombre_invalide')
    return int(str(brut).strip())


def _regles(d, champs, err):
    """Règles conditionnelles : liste structurée (import, trame, API) ou lignes de formulaire ``regle_<n>_*`` (une condition principale et
    jusqu'à trois autres facultatives, liées par ET). Chaque règle : condition(s), action, assiette (forfait de base), groupe d'exclusivité."""
    bruts = []
    if isinstance(d.get('regles'), list):
        bruts = [r if isinstance(r, dict) else {} for r in d['regles']]
    else:
        for n in range(1, MAX_REGLES + 1):
            libelle = str(d.get('regle_%d_libelle' % n) or '').strip()
            if not libelle and not d.get('regle_%d_champ' % n):
                continue
            si = [{'champ': d.get('regle_%d_champ' % n), 'op': d.get('regle_%d_op' % n) or 'eq', 'valeur': d.get('regle_%d_valeur' % n)}]
            for rang in range(2, MAX_CONDITIONS + 1):   # 5.6.109 (I2) : jusqu'à quatre conditions, comme une règle importée
                if d.get('regle_%d_champ%d' % (n, rang)):
                    si.append({'champ': d.get('regle_%d_champ%d' % (n, rang)), 'op': d.get('regle_%d_op%d' % (n, rang)) or 'eq',
                               'valeur': d.get('regle_%d_valeur%d' % (n, rang))})
            type_ = str(d.get('regle_%d_action' % n) or 'ajout_fixe')
            action = {'type': type_}
            brut = d.get('regle_%d_montant' % n)
            if type_ in ('ajout_fixe', 'reduction_fixe'):
                action['montant_ht'] = brut
            elif type_ in ('pourcentage_base', 'reduction_pourcentage_base'):
                action['pct'] = brut
            else:
                action['minutes'] = brut
            bruts.append({'id': str(d.get('regle_%d_id' % n) or '') or re.sub(r'[^a-z0-9]+', '-', _plier(libelle)).strip('-')[:40] or 'regle-%d' % n,
                          'libelle_client': libelle, 'si': si, 'action': action, 'groupe': d.get('regle_%d_groupe' % n), '_n': n})
    out, vus = [], set()
    if len(bruts) > MAX_REGLES:
        err['regles'] = 'Trop de règles (%d au plus).' % MAX_REGLES
    for i, b in enumerate(bruts[:MAX_REGLES], 1):
        cle = 'regle_%s' % (b.get('_n') or i)
        ident = str(b.get('id') or '')
        if not IDENT.fullmatch(ident) or ident in vus:
            err[cle + '_libelle'] = 'Règle en double ou identifiant invalide : chaque règle a un libellé distinct.'
            continue
        vus.add(ident)
        libelle = re.sub(r'\s+', ' ', str(b.get('libelle_client') or '')).strip()[:200]
        if not libelle:
            err[cle + '_libelle'] = 'Donnez à la règle un libellé lisible par le client.'
        si = []
        if isinstance(b.get('si'), list) and len(b['si']) > MAX_CONDITIONS:   # jamais une condition tronquée en silence
            err[cle + '_champ'] = 'Une règle a %d conditions au plus.' % MAX_CONDITIONS
        for cond in (b.get('si') if isinstance(b.get('si'), list) else [])[:MAX_CONDITIONS]:
            k, op = str((cond or {}).get('champ') or ''), str((cond or {}).get('op') or 'eq')
            if k not in champs:
                err[cle + '_champ'] = 'Critère inconnu de cette prestation : %s.' % (k or '(vide)')
                continue
            if op not in OPERATEURS or (op != 'eq' and champs[k]['type'] in ('booleen', 'choix')):
                err[cle + '_op'] = 'Comparaison impossible pour ce critère.'
                continue
            try:
                si.append({'champ': k, 'op': op, 'valeur': _attendu(champs[k], cond.get('valeur'))})
            except ValueError:
                err[cle + '_valeur'] = 'Valeur attendue invalide pour le critère « %s ».' % k
        if not si and cle + '_champ' not in err and cle + '_valeur' not in err and cle + '_op' not in err:
            err[cle + '_champ'] = 'Une règle a au moins une condition.'
        a = b.get('action') if isinstance(b.get('action'), dict) else {}
        type_ = str(a.get('type') or '')
        action = {'type': type_}
        if type_ not in ACTIONS:
            err[cle + '_action'] = 'Action inconnue.'
        elif type_ in ('ajout_fixe', 'reduction_fixe'):
            action['montant_ht_cents'] = _cents_ou_rien(a.get('montant_ht_cents', a.get('montant_ht')), cle + '_montant', err)
        elif type_ in ('pourcentage_base', 'reduction_pourcentage_base'):
            action['pct'] = None
            if a.get('pct') not in (None, ''):
                try:
                    action['pct'] = format(decimal(a['pct'] if type(a['pct']) is int else str(a['pct']), 'pct'), 'f')
                except ValueError:
                    err[cle + '_montant'] = 'Pourcentage invalide.'
        else:
            action['minutes'] = None
            if a.get('minutes') not in (None, ''):
                if re.fullmatch(r'\d{1,6}', str(a['minutes']).strip()) and not isinstance(a['minutes'], (float, bool)):
                    action['minutes'] = int(str(a['minutes']).strip())
                else:
                    err[cle + '_montant'] = 'Nombre de minutes invalide.'
        regle = {'id': ident, 'libelle_client': libelle, 'si': si, 'action': action}
        groupe = re.sub(r'[^a-z0-9_-]+', '', _plier(b.get('groupe') or ''))[:40]
        if groupe:
            regle['groupe'] = groupe
        out.append(regle)
    return out


def controler_publication(c):
    """Contrôle COMPLET avant publication : toutes les branches, déclenchées ou non. Rend ``{champ: message}`` (vide : publiable)."""
    err = {}
    normaliser(c, err)
    mode = c.get('mode')
    gratuit = c.get('gratuite') and str(c.get('motif_gratuite') or '').strip()
    if c.get('gratuite') and not gratuit:
        err['motif_gratuite'] = 'Une mission gratuite se publie avec son motif.'
    if mode in ('forfait', 'mixte', 'abonnement'):
        if c.get('fixe_ht_cents') is None:
            err['fixe_ht'] = 'Indiquez le forfait hors taxes (ou choisissez expressément la gratuité, avec son motif).'
        elif c['fixe_ht_cents'] == 0 and not gratuit and not (c.get('regles') or mode == 'mixte'):
            err['fixe_ht'] = 'Un forfait de zéro n’existe qu’avec la gratuité explicite et son motif.'
    if mode in ('temps', 'mixte'):
        if not c.get('taux_ht_cents'):
            err['taux_ht'] = 'Indiquez le taux horaire hors taxes.'
        prevues = (c.get('champs') or {}).get('minutes_prevues')
        if not prevues or prevues.get('type') != 'minutes':
            err['champs'] = 'Une prestation au temps passé ou mixte déclare le critère « minutes_prevues » (durée prévisionnelle).'
    if mode == 'abonnement' and c.get('periode') not in PERIODES:
        err['periode'] = 'Choisissez la période de l’assistance régulière.'
    if c.get('tva_statut') == 'confirme' and c.get('tva_pct') in (None, ''):
        err['tva_pct'] = 'TVA confirmée : indiquez son taux.'
    for i, r in enumerate(c.get('regles') or [], 1):
        a = r.get('action') or {}
        if a.get('type') in ('ajout_fixe', 'reduction_fixe') and a.get('montant_ht_cents') is None:
            err['regle_%d_montant' % i] = 'Règle « %s » : indiquez son montant.' % r.get('libelle_client', '')
        if a.get('type') in ('pourcentage_base', 'reduction_pourcentage_base'):
            if a.get('pct') is None:
                err['regle_%d_montant' % i] = 'Règle « %s » : indiquez son pourcentage.' % r.get('libelle_client', '')
            if mode == 'temps':
                err['regle_%d_action' % i] = 'Un pourcentage porte sur le forfait de base : impossible pour une prestation au temps passé.'
        if a.get('type') == 'ajout_minutes':
            if a.get('minutes') is None:
                err['regle_%d_montant' % i] = 'Règle « %s » : indiquez le temps ajouté.' % r.get('libelle_client', '')
            if mode not in ('temps', 'mixte'):
                err['regle_%d_action' % i] = 'Du temps ne s’ajoute qu’à une prestation au temps passé ou mixte.'
    if not c.get('inclus'):
        err['inclus'] = 'Décrivez ce que la prestation comprend (au moins une ligne) : le périmètre figure dans chaque proposition.'
    return err


# ------------------------------------------------------------------------------------------------ droits
def _acteur(acteur):
    from .acces5697 import acteur_de
    return acteur_de(acteur)


def _droit(desk, a, droit):
    """Droit relu dans le registre des droits (``facturation5653._require``) ; une panne du contrôle donne 503, jamais un accès élargi."""
    from .facturation5653 import _require
    if a.role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    _require(desk, a.compte, a.role, droit)


def _peut(desk, a, droit):
    try:
        _droit(desk, a, droit)
        return True
    except Stop as ex:
        if str(ex) != 'role_insuffisant':
            raise
        return False


def _dossier(desk, a, ident):
    """Dossier visible par l'acteur ; absent ou réservé : même ``introuvable`` (404 neutre)."""
    from . import dossiers5697 as D
    courant = D.resoudre(desk, ident)
    if not courant or not D._visible(desk, a, courant):
        raise Stop('introuvable')
    return courant


# ------------------------------------------------------------------------------------------------ bibliothèque de trames et catégories
_TRAMES = {}


def bibliotheque():
    """Bibliothèque d'amorçage livrée (77 trames, 18 domaines, AUCUN prix) : des modèles à adapter, jamais un barème de la profession.
    Lue dans le fichier livré ; rien n'est actif tant que le cabinet n'a pas créé puis publié sa propre prestation."""
    if 'data' not in _TRAMES:
        data = json.loads(FICHIER_TRAMES.read_text(encoding='utf-8'))
        for p in data['prestations']:   # garde-fou : une trame ne porte jamais de prix ni d'activation
            p['fixe_ht_cents'] = p['taux_ht_cents'] = None
            p['actif_pour_ia'] = False
            for r in p.get('regles') or []:
                a = r.get('action') or {}
                for k in ('montant_ht_cents', 'pct', 'minutes'):
                    if k in a:
                        a[k] = None
        _TRAMES['data'] = data
    return _TRAMES['data']


def categories(desk):
    """Domaines du catalogue : ceux de la bibliothèque et ceux ajoutés par le cabinet (réglage, sans modifier de code)."""
    out = {c['id']: c['libelle'] for c in bibliotheque()['categories']}
    ajoutees = desk.settings(REGLAGE_CATEGORIES, None) or {}
    if isinstance(ajoutees, dict):
        out.update({str(k)[:60]: str(v)[:80] for k, v in ajoutees.items()})
    return out


def _categorie(desk, c, libelle_nouveau=''):
    """Catégorie d'une prestation ; une catégorie nouvelle saisie par le cabinet est ajoutée à la liste (jamais retirée d'office)."""
    nouveau = re.sub(r'\s+', ' ', str(libelle_nouveau or '')).strip()[:80]
    if nouveau:
        ident = re.sub(r'[^a-z0-9]+', '_', _plier(nouveau)).strip('_')[:60] or 'autre'
        connues = dict(desk.settings(REGLAGE_CATEGORIES, None) or {})
        if ident not in categories(desk):
            connues[ident] = nouveau
            desk.setting(REGLAGE_CATEGORIES, connues)
        c['categorie'] = ident
    return c


# ------------------------------------------------------------------------------------------------ lecture
def _vue_offre(desk, row, interne, cats=None):
    c = json.loads(row['contenu_json'])
    publiee = _publiee(desk, row['id']) if row['version_publiee'] else None
    a_publier = row['etat'] == 'brouillon' or _sha(c) != (publiee or {}).get('empreinte')   # brouillon différent de la version en vigueur
    if not interne:   # qui ne gère ni ne facture ne voit ni le coût interne ni les notes internes
        for k in ('cout_interne_cents', 'note_interne', 'description_interne'):
            c.pop(k, None)
    cats = categories(desk) if cats is None else cats
    return {'id': row['id'], 'etat': row['etat'], 'etat_libelle': ETATS.get(row['etat'], row['etat']), 'version': row['version'], 'version_publiee': row['version_publiee'],
            'profil_avocat': row['profil_avocat'], 'origine': row['origine'], 'modifie_par': row['modifie_par'], 'modifie_le': row['modifie_le'], 'contenu': c,
            'categorie_libelle': cats.get(c.get('categorie') or '', c.get('categorie') or 'Sans domaine'), 'mode_libelle': MODES.get(c.get('mode'), ''),
            'a_publier': a_publier, 'publiee': {k: publiee[k] for k in ('version', 'debut', 'fin', 'valide_par', 'valide_le')} if publiee else None}


def _publiee(desk, ident, version=None):
    """Version publiée (la dernière par défaut) : contenu figé, empreinte, auteur de la publication, dates d'application."""
    if version is None:
        row = desk.db.execute('SELECT * FROM catalogue_versions56109 WHERE offre_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
    else:
        row = desk.db.execute('SELECT * FROM catalogue_versions56109 WHERE offre_id=? AND version=?', (ident, int(version))).fetchone()
    if not row:
        return None
    return {'offre_id': row['offre_id'], 'version': row['version'], 'debut': row['debut'], 'fin': row['fin'], 'contenu': json.loads(row['contenu_json']),
            'empreinte': row['empreinte'], 'valide_par': row['valide_par'], 'valide_le': row['valide_le'], 'profil_avocat': row['profil_avocat']}


def lister(desk, acteur, filtres=None):
    """Prestations du cabinet (droit « voir_honoraires ») : recherche, filtres domaine, type de mission, état. Lecture seule."""
    a = _acteur(acteur)
    _droit(desk, a, 'voir_honoraires')
    interne = _peut(desk, a, 'reglages') or _peut(desk, a, 'facturation')
    f = filtres or {}
    q = _plier(f.get('q') or '')
    out, cats = [], categories(desk)
    for row in desk.db.execute('SELECT * FROM catalogue_offres56109 ORDER BY id LIMIT 1000').fetchall():
        v = _vue_offre(desk, row, interne, cats)
        c = v['contenu']
        if f.get('categorie') and c.get('categorie') != f['categorie']:
            continue
        if f.get('type_mission') and c.get('type_mission') != f['type_mission']:
            continue
        if f.get('etat') and v['etat'] != f['etat']:
            continue
        if not f.get('etat') and v['etat'] == 'archive' and not f.get('archives'):
            continue
        if q and q not in _plier(' '.join((c.get('libelle') or '', v['categorie_libelle'], c.get('procedure') or '', v['id']))):
            continue
        out.append(v)
    out.sort(key=lambda v: (v['categorie_libelle'].casefold(), (v['contenu'].get('libelle') or '').casefold()))
    return {'prestations': out, 'categories': categories(desk), 'types_mission': TYPES_MISSION, 'modes': MODES, 'peut_gerer': _peut(desk, a, 'reglages'),
            'peut_publier': a.role in ('administrateur', 'avocat') and _peut(desk, a, 'reglages'), 'peut_proposer': _peut(desk, a, 'facturation')}


def versions(desk, acteur, ident):
    a = _acteur(acteur)
    _droit(desk, a, 'voir_honoraires')
    rows = desk.db.execute('SELECT offre_id,version,debut,fin,empreinte,valide_par,valide_le FROM catalogue_versions56109 WHERE offre_id=? ORDER BY version DESC', (str(ident),)).fetchall()
    if not rows and not desk.db.execute('SELECT 1 FROM catalogue_offres56109 WHERE id=?', (str(ident),)).fetchone():
        raise Stop('introuvable')
    return [dict(r) for r in rows]


def offre_applicable(desk, ident, au=None, avocat=''):
    """Version publiée applicable à une date, pour un avocat : sa variante publiée si elle existe (profil), sinon celle du cabinet. Rend
    l'offre prête pour ``evaluer`` ; lève ``Stop`` (introuvable, archivée, non publiée, pas encore applicable, expirée)."""
    au = str(au or jour_cabinet(desk))[:10]
    row = desk.db.execute('SELECT * FROM catalogue_offres56109 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    if avocat:   # profil propre à l'avocat : variante publiée de cette prestation, prioritaire sur celle du cabinet
        for v in desk.db.execute("SELECT id,contenu_json FROM catalogue_offres56109 WHERE profil_avocat=? AND etat='publie'", (str(avocat),)):
            if json.loads(v['contenu_json']).get('variante_de') == row['id']:
                try:
                    return offre_applicable(desk, v['id'], au)
                except Stop:
                    break
    if row['etat'] == 'archive':
        raise Stop('offre_archivee')
    candidates = [dict(r) for r in desk.db.execute('SELECT * FROM catalogue_versions56109 WHERE offre_id=? ORDER BY version DESC', (row['id'],))]
    if not candidates:
        raise Stop('offre_non_publiee')
    # la version la plus récente déjà entrée en application remplace les précédentes ; si elle a pris fin, la prestation est expirée
    # (on ne revient jamais à une version antérieure)
    choisie = next((v for v in candidates if not v['debut'] or v['debut'] <= au), None)
    if choisie is None:
        raise Stop('offre_pas_encore_applicable')
    if choisie['fin'] and au >= choisie['fin']:
        raise Stop('offre_expiree')
    return {**json.loads(choisie['contenu_json']), 'id': row['id'], 'version': choisie['version'], 'statut': 'publie', 'valide_par': choisie['valide_par'],
            'debut': choisie['debut'] or None, 'fin': choisie['fin'] or None, 'empreinte_version': choisie['empreinte'], 'profil_avocat': row['profil_avocat']}


# ------------------------------------------------------------------------------------------------ écritures du catalogue
def _version(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        raise Refus('champs_invalides', {'version': 'Version attendue manquante : rechargez la page.'}) from None


def _identifiant(desk, c, propose=''):
    base = str(propose or '').strip().lower() or re.sub(r'[^a-z0-9]+', '-', _plier('%s %s' % (c.get('categorie') or '', c['libelle']))).strip('-')[:70]
    if not IDENT.fullmatch(base):
        raise Refus('champs_invalides', {'id': 'Identifiant invalide (lettres minuscules, chiffres, tirets).'})
    if not propose:
        n, ident = 1, base
        while desk.db.execute('SELECT 1 FROM catalogue_offres56109 WHERE id=?', (ident,)).fetchone():
            n += 1
            ident = '%s-%d' % (base[:70], n)
        return ident
    if desk.db.execute('SELECT 1 FROM catalogue_offres56109 WHERE id=?', (base,)).fetchone():
        raise Stop('identifiant_deja_utilise')
    return base


def creer(desk, acteur, donnees):
    """Nouvelle prestation, toujours en BROUILLON : saisie, copie d'une prestation (``copie_de``) ou trame de la bibliothèque (``trame`` :
    activation explicite, sans prix). Droit « réglages »."""
    a = _acteur(acteur)
    _droit(desk, a, 'reglages')
    d = donnees if isinstance(donnees, dict) else {}
    origine = 'cabinet'
    if d.get('trame'):
        trame = next((p for p in bibliotheque()['prestations'] if p['id'] == str(d['trame'])), None)
        if not trame:
            raise Stop('trame_inconnue')
        d = {**trame, **{k: v for k, v in d.items() if k in ('libelle', 'profil_avocat') and v}}
        origine = 'trame'
    elif d.get('copie_de'):
        row = desk.db.execute('SELECT contenu_json FROM catalogue_offres56109 WHERE id=?', (str(d['copie_de']),)).fetchone()
        if not row:
            raise Stop('introuvable')
        source = json.loads(row['contenu_json'])
        d = {**source, 'libelle': str(d.get('libelle') or '') or (source['libelle'] + ' (copie)')[:160], 'actif_pour_ia': False,
             **{k: v for k, v in d.items() if k in ('profil_avocat', 'variante_de') and v}}
    err = {}
    c = _categorie(desk, normaliser(d, err), d.get('categorie_nouvelle'))
    if err:
        raise Refus('champs_invalides', err)
    if desk.db.execute('SELECT COUNT(*) FROM catalogue_offres56109').fetchone()[0] >= 1000:
        raise Stop('trop_de_prestations')
    saisie = donnees if isinstance(donnees, dict) else {}
    ident = _identifiant(desk, c, saisie.get('id') if origine == 'cabinet' and not saisie.get('copie_de') else '')
    quand = _maintenant()
    desk.db.execute('INSERT INTO catalogue_offres56109(id,etat,version_publiee,version,profil_avocat,origine,contenu_json,cree_par,cree_le,modifie_par,modifie_le) '
                    "VALUES(?,'brouillon',0,1,?,?,?,?,?,?,?)", (ident, c['profil_avocat'], origine, json.dumps(c, ensure_ascii=False), a.compte, quand, a.compte, quand))
    desk.db.commit()
    desk.audit('catalogue56109', {'action': 'creer', 'id': ident, 'owner': a.compte, 'origine': origine})
    return {'id': ident, 'version': 1, 'etat': 'brouillon', 'message': 'Prestation créée en brouillon : elle ne sert à aucun chiffrage tant qu’elle n’est pas publiée.'}


def modifier(desk, acteur, ident, version, donnees):
    """Modification du brouillon (version attendue). Une prestation déjà publiée garde sa version publiée intacte : la modification ne
    prendra effet qu'à la prochaine publication, comme nouvelle version. Droit « réglages »."""
    a = _acteur(acteur)
    _droit(desk, a, 'reglages')
    row = desk.db.execute('SELECT * FROM catalogue_offres56109 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    d = donnees if isinstance(donnees, dict) else {}
    err = {}
    c = _categorie(desk, normaliser(d, err), d.get('categorie_nouvelle'))
    if err:
        raise Refus('champs_invalides', err)
    n = desk.db.execute('UPDATE catalogue_offres56109 SET contenu_json=?, profil_avocat=?, version=version+1, modifie_par=?, modifie_le=?, '
                        "etat=CASE WHEN etat='archive' THEN 'brouillon' ELSE etat END WHERE id=? AND version=?",
                        (json.dumps(c, ensure_ascii=False), c['profil_avocat'], a.compte, _maintenant(), row['id'], _version(version))).rowcount
    if n != 1:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    desk.audit('catalogue56109', {'action': 'modifier', 'id': row['id'], 'owner': a.compte})
    return {'id': row['id'], 'version': row['version'] + 1, 'message': 'Brouillon enregistré.' + (
        ' La version publiée reste en vigueur jusqu’à la prochaine publication.' if row['version_publiee'] else '')}


def publier(desk, acteur, ident, version, debut='', fin=''):
    """Publication : contrôle complet (toutes les branches) puis version immuable. Administrateur ou avocat ayant le droit « réglages » ;
    jamais un assistant, même autorisé à préparer."""
    a = _acteur(acteur)
    if a.role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    _droit(desk, a, 'reglages')
    row = desk.db.execute('SELECT * FROM catalogue_offres56109 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    if row['version'] != _version(version):
        raise Stop('version_perimee')
    c = json.loads(row['contenu_json'])
    err = controler_publication(c)
    debut = str(debut or '').strip()[:10] or jour_cabinet(desk)
    fin = str(fin or '').strip()[:10]
    try:
        date.fromisoformat(debut)
        if fin and date.fromisoformat(fin) <= date.fromisoformat(debut):
            err['fin'] = 'La fin d’application suit le début.'
    except ValueError:
        err['debut'] = 'Dates d’application invalides.'
    if err:
        raise Refus('publication_refusee', err)
    nouvelle, quand = row['version_publiee'] + 1, _maintenant()
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        n = desk.db.execute("UPDATE catalogue_offres56109 SET etat='publie', version_publiee=?, version=version+1, modifie_par=?, modifie_le=? WHERE id=? AND version=?",
                            (nouvelle, a.compte, quand, row['id'], row['version'])).rowcount
        if n != 1:
            raise Stop('version_perimee')
        desk.db.execute('INSERT INTO catalogue_versions56109(offre_id,version,profil_avocat,debut,fin,contenu_json,empreinte,valide_par,valide_le) VALUES(?,?,?,?,?,?,?,?,?)',
                        (row['id'], nouvelle, row['profil_avocat'], debut, fin, json.dumps(c, ensure_ascii=False), _sha(c), a.compte, quand))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('catalogue56109', {'action': 'publier', 'id': row['id'], 'version': nouvelle, 'owner': a.compte})
    return {'id': row['id'], 'version': row['version'] + 1, 'version_publiee': nouvelle, 'debut': debut,
            'message': 'Version %d publiée, applicable à compter du %s. Les propositions déjà établies gardent leur version.' % (nouvelle, debut)}


def archiver(desk, acteur, ident, version):
    """Archivage : la prestation ne sert plus à de nouveaux chiffrages ; ses versions publiées restent consultables. Droit « réglages »."""
    a = _acteur(acteur)
    _droit(desk, a, 'reglages')
    n = desk.db.execute("UPDATE catalogue_offres56109 SET etat='archive', version=version+1, modifie_par=?, modifie_le=? WHERE id=? AND version=?",
                        (a.compte, _maintenant(), str(ident), _version(version))).rowcount
    if n != 1:
        desk.db.rollback()
        raise Stop('version_perimee' if desk.db.execute('SELECT 1 FROM catalogue_offres56109 WHERE id=?', (str(ident),)).fetchone() else 'introuvable')
    desk.db.commit()
    desk.audit('catalogue56109', {'action': 'archiver', 'id': str(ident), 'owner': a.compte})
    return {'id': str(ident), 'etat': 'archive', 'message': 'Prestation archivée : ses versions publiées restent consultables.'}


def importer(desk, acteur, brut, appliquer=False):
    """Import d'un catalogue JSON (bibliothèque livrée, export d'un autre poste). D'abord la SIMULATION : pour chaque prestation, nouvelle,
    différente ou identique — rapprochement par identifiant, jamais par libellé. ``appliquer`` : les nouvelles et les différentes
    deviennent des BROUILLONS (jamais publiées, jamais « utilisable par l'IA ») ; les versions publiées ne sont pas touchées. Droit « réglages »."""
    a = _acteur(acteur)
    _droit(desk, a, 'reglages')
    try:
        data = json.loads(brut.decode('utf-8') if isinstance(brut, (bytes, bytearray)) else brut)
    except (ValueError, UnicodeDecodeError, AttributeError):
        raise Stop('catalogue_illisible') from None
    lignes = data.get('prestations') if isinstance(data, dict) else None
    if not isinstance(lignes, list) or not lignes or len(lignes) > 1000:
        raise Stop('catalogue_illisible')
    rapport = {'nouvelles': [], 'differentes': [], 'identiques': [], 'refusees': []}
    quand = _maintenant()
    for p in lignes:
        ident = str((p or {}).get('id') or '') if isinstance(p, dict) else ''
        err = {}
        c = normaliser({**p, 'actif_pour_ia': False}, err) if isinstance(p, dict) else {}
        if not IDENT.fullmatch(ident) or err:
            rapport['refusees'].append({'id': ident[:80], 'motif': '; '.join(err.values())[:300] or 'identifiant invalide'})
            continue
        row = desk.db.execute('SELECT * FROM catalogue_offres56109 WHERE id=?', (ident,)).fetchone()
        if not row:
            rapport['nouvelles'].append({'id': ident, 'libelle': c['libelle']})
            if appliquer:
                desk.db.execute('INSERT INTO catalogue_offres56109(id,etat,version_publiee,version,profil_avocat,origine,contenu_json,cree_par,cree_le,modifie_par,modifie_le) '
                                "VALUES(?,'brouillon',0,1,?,'import',?,?,?,?,?)", (ident, c['profil_avocat'], json.dumps(c, ensure_ascii=False), a.compte, quand, a.compte, quand))
        elif _sha({**json.loads(row['contenu_json']), 'actif_pour_ia': False}) == _sha(c):
            rapport['identiques'].append({'id': ident, 'libelle': c['libelle']})
        else:
            rapport['differentes'].append({'id': ident, 'libelle': c['libelle']})
            if appliquer:   # le brouillon est remplacé ; la version publiée, elle, reste en vigueur jusqu'à une publication explicite
                desk.db.execute('UPDATE catalogue_offres56109 SET contenu_json=?, version=version+1, modifie_par=?, modifie_le=? WHERE id=?',
                                (json.dumps({**c, 'actif_pour_ia': bool(json.loads(row['contenu_json']).get('actif_pour_ia'))}, ensure_ascii=False), a.compte, quand, ident))
    if appliquer:
        desk.db.commit()
        desk.audit('catalogue56109', {'action': 'importer', 'owner': a.compte, 'nouvelles': len(rapport['nouvelles']), 'differentes': len(rapport['differentes'])})
    rapport.update(applique=bool(appliquer), message='%d nouvelle(s), %d différente(s), %d identique(s), %d refusée(s)%s' % (
        len(rapport['nouvelles']), len(rapport['differentes']), len(rapport['identiques']), len(rapport['refusees']),
        ' — importées en brouillon, rien n’est publié.' if appliquer else ' — simulation : rien n’est écrit.'))
    return rapport


def exporter(desk, acteur):
    """Catalogue du cabinet au format d'import (brouillons courants) : pour un autre poste. Droit « réglages » (contient les notes internes)."""
    a = _acteur(acteur)
    _droit(desk, a, 'reglages')
    return {'schema': 'axiorhub/catalogue-cabinet/1', 'categories': [{'id': k, 'libelle': v} for k, v in categories(desk).items()],
            'prestations': [{'id': r['id'], **json.loads(r['contenu_json'])} for r in desk.db.execute("SELECT id,contenu_json FROM catalogue_offres56109 WHERE etat<>'archive' ORDER BY id")]}


# ------------------------------------------------------------------------------------------------ tarif applicable, simulation, proposition
def _donnees(d):
    """Critères reçus d'un formulaire ou d'un travail : ``{critère: {'valeur', 'statut', 'source'}}``. Un formulaire saisi par une personne
    (``donnee_<critère>``) vaut confirmation ; une valeur vide reste INCONNUE (jamais « non », jamais zéro)."""
    out = {}
    d = d if isinstance(d, dict) else {}
    if isinstance(d.get('donnees'), dict):
        for k, info in d['donnees'].items():
            if isinstance(info, dict):
                out[str(k)] = {'valeur': info.get('valeur'), 'statut': str(info.get('statut') or ''), 'source': str(info.get('source') or '')[:200]}
    for k, v in d.items():
        if str(k).startswith('donnee_') and v not in (None, ''):
            out[str(k)[len('donnee_'):]] = {'valeur': v, 'statut': 'confirme', 'source': 'saisie'}
    return out


def _typer(offre, donnees):
    """Valeurs saisies en texte converties d'après le type du critère (« oui » → vrai, « 90 » → 90) ; une valeur illisible est refusée."""
    out, err = {}, {}
    for k, info in donnees.items():
        champ = (offre.get('champs') or {}).get(k)
        if not champ:
            continue
        v = info.get('valeur')
        try:
            if isinstance(v, str):
                v = _attendu(champ, v.strip())
            out[k] = {**info, 'valeur': v}
        except ValueError:
            err['donnee_' + k] = 'Valeur invalide pour « %s ».' % (champ.get('question') or k)
    if err:
        raise Refus('champs_invalides', err)
    return out


def convention_du_dossier(desk, did):
    """Engagement contractuel du dossier, du plus fort au plus faible : convention ou lettre de mission SIGNÉE (preuve enregistrée par
    l'avocat dans le registre de facturation), devis ACCEPTÉ, montant RETENU par l'avocat. Jamais déduit d'un courriel ni d'un modèle."""
    def lignes(sql, params):
        try:
            return desk.db.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
    for r in lignes("SELECT id,kind,number,state,snapshot_json FROM b53_documents WHERE matter=? AND kind IN ('convention','lettre_mission') AND state='signe' "
                    'ORDER BY updated DESC LIMIT 1', (did,)):
        return {'origine': 'convention_signee', 'statut': 'signe', 'source_id': r['id'], 'reference': r['number'], 'montant_ht_cents': _ht(r['snapshot_json']),
                'libelle': 'Convention ou lettre de mission signée %s' % (r['number'] or '')}
    for r in lignes("SELECT id,number,snapshot_json FROM b53_documents WHERE matter=? AND kind='devis' AND state='accepte' ORDER BY updated DESC LIMIT 1", (did,)):
        return {'origine': 'proposition_acceptee', 'statut': 'accepte', 'source_id': r['id'], 'reference': r['number'], 'montant_ht_cents': _ht(r['snapshot_json']),
                'libelle': 'Devis accepté %s' % (r['number'] or '')}
    for r in lignes("SELECT id,signed_cents,signed_source FROM b53_estimates WHERE matter=? AND state='signee' AND signed_source<>'' ORDER BY updated DESC LIMIT 1", (did,)):
        return {'origine': 'convention_signee', 'statut': 'signe', 'source_id': r['id'], 'reference': r['signed_source'], 'montant_ht_cents': r['signed_cents'],
                'libelle': 'Montant figé à la signature (%s)' % r['signed_source']}
    for r in lignes("SELECT id,retained_cents,proposed_cents,decided_by FROM b53_estimates WHERE matter=? AND state='retenue' ORDER BY updated DESC LIMIT 1", (did,)):
        return {'origine': 'derogation' if r['retained_cents'] != r['proposed_cents'] else 'montant_retenu', 'statut': 'retenu', 'source_id': r['id'], 'reference': '',
                'montant_ht_cents': r['retained_cents'], 'libelle': 'Montant retenu par %s' % (r['decided_by'] or 'l’avocat')}
    return None


def _ht(snapshot_json):
    try:
        v = (json.loads(snapshot_json or '{}').get('totaux') or {}).get('ht_cents')
        return v if type(v) is int else None
    except ValueError:
        return None


def tarif_applicable(desk, acteur, dossier, offre_id=None, au=None, avocat=None):
    """Tarif applicable à un dossier, par ordre de priorité : convention ou lettre de mission signée ; devis accepté ; montant retenu par
    l'avocat (dérogation) ; version publiée du profil de l'avocat ; catalogue du cabinet ; sinon « à chiffrer ». Lecture seule.

    Rend ``{'origine', 'libelle', 'montant_ht_cents'?, 'source_id'?, 'offre'?, 'taux_horaire_confirme'?}``. Exposé pour la préfacturation,
    l'assistance régulière et les frais (vague suivante)."""
    a = _acteur(acteur)
    _droit(desk, a, 'voir_honoraires')
    did = _dossier(desk, a, dossier)
    engage = convention_du_dossier(desk, did)
    if engage and engage['origine'] in ('convention_signee', 'proposition_acceptee', 'derogation', 'montant_retenu'):
        return {**engage, 'dossier': did, 'message': 'Relire le périmètre de cet engagement et ses avenants ; le catalogue ne remplace pas son prix.'}
    if offre_id:
        responsable = avocat if avocat is not None else (desk.db.execute('SELECT responsable FROM dossiers5697 WHERE id=?', (did,)).fetchone() or [''])[0]
        try:
            offre = offre_applicable(desk, offre_id, au, responsable or '')
            return {'origine': 'profil_avocat' if offre.get('profil_avocat') else 'catalogue_cabinet', 'dossier': did, 'offre': offre,
                    'libelle': '%s (version %d du %s)' % (offre['libelle'], offre['version'], 'profil de l’avocat' if offre.get('profil_avocat') else 'catalogue du cabinet')}
        except Stop as ex:
            if str(ex) == 'introuvable':
                raise
            return {'origine': 'a_chiffrer', 'dossier': did, 'libelle': MESSAGES.get(str(ex), 'Aucune version publiée applicable.'), 'motif': str(ex)}
    from . import honoraires5653 as fees
    confirme = fees.tarifs_confirmes(desk)
    return {'origine': 'a_chiffrer', 'dossier': did, 'libelle': 'Aucun engagement signé ni prestation publiée désignée : à chiffrer.',
            # un taux technique par défaut n'est pas une décision du cabinet : il n'est communiqué que confirmé
            'taux_horaire_confirme': fees.settings(desk)['taux_horaire'] if confirme else None}


def simuler(desk, acteur, offre_id, donnees=None, dossier=None, au=None, essai=False):
    """Calcul déterministe d'une prestation publiée (ou, ``essai``, du brouillon avant publication — droit « réglages », jamais enregistré).
    Avec un dossier : accès relu, convention signée prioritaire. Rend la vue client, l'explication et les questions ; n'écrit rien."""
    a = _acteur(acteur)
    _droit(desk, a, 'voir_honoraires')
    did = _dossier(desk, a, dossier) if dossier else ''
    au = str(au or jour_cabinet(desk))[:10]
    accord = None
    if did:
        engage = convention_du_dossier(desk, did)
        if engage and engage['origine'] == 'convention_signee':
            accord = {'statut': 'signe', 'source_id': engage['source_id']}
    if essai:
        _droit(desk, a, 'reglages')
        row = desk.db.execute('SELECT * FROM catalogue_offres56109 WHERE id=?', (str(offre_id),)).fetchone()
        if not row:
            raise Stop('introuvable')
        c = json.loads(row['contenu_json'])
        err = controler_publication(c)
        if err:
            raise Refus('publication_refusee', err)
        offre = {**c, 'id': row['id'], 'version': row['version_publiee'] + 1, 'statut': 'publie', 'valide_par': '(essai avant publication)', 'debut': None, 'fin': None}
    else:
        responsable = (desk.db.execute('SELECT responsable FROM dossiers5697 WHERE id=?', (did,)).fetchone() or [''])[0] if did else ''
        offre = offre_applicable(desk, offre_id, au, responsable)
    valeurs = _typer(offre, _donnees(donnees))
    try:
        resultat = evaluer(offre, valeurs, au=au, accord=accord)
    except ValueError as ex:
        raise Refus('champs_invalides', {'calcul': 'Calcul impossible : %s.' % str(ex).replace('_', ' ')}) from None
    return {'dossier': did, 'au': au, 'essai': bool(essai), 'resultat': vue_client(resultat), 'explication': explication(resultat),
            'regles_appliquees': resultat.get('regles_appliquees', []), 'parametres': valeurs,
            'ferme': resultat['statut'] == 'proposition', '_complet': resultat, '_offre': offre}


def estimation_motivee(desk, acteur, dossier, offre_id, donnees=None, au=None):
    """Estimation motivée pour un dossier : tarif applicable d'abord (un engagement signé interrompt le chiffrage), puis calcul du
    catalogue avec ses motifs. Lecture seule ; exposé pour la préfacturation et les réponses (vague suivante)."""
    tarif = tarif_applicable(desk, acteur, dossier, offre_id, au)
    if tarif['origine'] not in ('profil_avocat', 'catalogue_cabinet'):
        return {'origine': tarif['origine'], 'ferme': False, 'tarif': {k: v for k, v in tarif.items() if k != 'offre'},
                'explication': [tarif['libelle']] + ([tarif['message']] if tarif.get('message') else [])}
    s = simuler(desk, acteur, offre_id, donnees, dossier, au)
    return {'origine': tarif['origine'], 'ferme': s['ferme'], 'resultat': s['resultat'], 'explication': s['explication'], 'regles_appliquees': s['regles_appliquees'],
            'parametres': s['parametres'], 'au': s['au'], 'dossier': s['dossier']}


def proposer(desk, acteur, offre_id, donnees, dossier, au=None):
    """Fige le calcul pour un dossier (droit « facturation ») : la simulation est enregistrée avec ses paramètres, sa version et son empreinte ;
    si le calcul est complet, elle devient une estimation du moteur d'honoraires existant (origine « catalogue du cabinet », à confirmer par
    l'avocat — rien n'est envoyé, signé ni facturé). Calcul incomplet ou prix absent : la simulation garde l'état, AUCUN montant n'est inscrit."""
    a = _acteur(acteur)
    _droit(desk, a, 'facturation')
    s = simuler(desk, acteur, offre_id, donnees, dossier, au)
    if not s['dossier']:
        raise Refus('champs_invalides', {'dossier': 'Choisissez le dossier de la proposition.'})
    complet, offre = s['_complet'], s['_offre']
    sid, quand = uuid.uuid4().hex, _maintenant()
    desk.db.execute('INSERT INTO catalogue_simulations56109(id,dossier_id,demande_id,offre_id,offre_version,statut,parametres_json,resultat_json,empreinte,auteur,version,cree_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,1,?)', (sid, s['dossier'], '', offre['id'], offre['version'], complet['statut'], json.dumps(s['parametres'], ensure_ascii=False),
                                                       json.dumps(complet, ensure_ascii=False), complet.get('empreinte') or _sha(complet), a.compte, quand))
    estimation = None
    if complet['statut'] == 'proposition':
        from . import honoraires5653 as fees
        estimation = fees.save_estimate(desk, a.compte, s['dossier'], vers_estimation(complet, sid))
        desk.db.execute('INSERT INTO catalogue_estimations56109(estimation_id,dossier_id,simulation_id,offre_id,offre_version,empreinte,cree_le) VALUES(?,?,?,?,?,?,?)',
                        (estimation['id'], s['dossier'], sid, offre['id'], offre['version'], complet['empreinte'], quand))
    desk.db.commit()
    desk.audit('catalogue56109', {'action': 'proposer', 'simulation': sid, 'dossier': s['dossier'], 'owner': a.compte, 'statut': complet['statut']})
    return {'simulation': sid, 'dossier': s['dossier'], 'statut': complet['statut'], 'estimation': estimation['id'] if estimation else None,
            'resultat': s['resultat'], 'explication': s['explication'],
            'message': ('Proposition enregistrée : %s HT (prestation « %s », version %d). À confirmer par l’avocat ; rien n’est envoyé.' % (
                fmt(complet['total_ht_cents']), offre['libelle'], offre['version'])) if estimation else
            'Simulation enregistrée sans montant : ' + ' '.join(s['explication'][:2])}


def vers_estimation(resultat, simulation=''):
    """Adaptateur vers ``honoraires5653.estimate`` : le total du catalogue devient le forfait PROPOSÉ d'une estimation, avec son origine
    (« catalogue du cabinet », prestation, version, empreinte, simulation). Jamais « choisi par l'avocat » avant sa décision."""
    if resultat.get('statut') != 'proposition':
        raise ValueError('calcul_incomplet')
    euros = '%d.%02d' % (resultat['total_ht_cents'] // 100, resultat['total_ht_cents'] % 100)
    entree = {'mode': 'forfait', 'diligences': [], 'forfait': euros,
              'origine_forfait': {'type': 'catalogue', 'offre_id': resultat['offre_id'], 'offre_version': resultat['offre_version'], 'libelle': resultat['libelle'],
                                  'empreinte': resultat['empreinte'], 'simulation': simulation}}
    if resultat.get('gratuite'):
        entree.update(gratuite=True, motif_gratuite='Gratuité prévue par la prestation publiée « %s »' % resultat['libelle'])
    return entree


# ------------------------------------------------------------------------------------------------ IA : vue client seulement
def texte_client(offre):
    """Description d'une prestation publiée telle qu'elle peut être donnée à un prospect ou au modèle de rédaction : périmètre, prix ou
    méthode, conditions présentées comme telles, exclusions, précisions à demander. Écrit par le logiciel ; rien d'interne."""
    mode = offre.get('mode')
    if offre.get('gratuite') and offre.get('fixe_ht_cents') == 0:
        prix = 'mission gratuite'
    elif mode == 'temps':
        prix = 'au temps passé, %s HT de l’heure' % fmt(offre['taux_ht_cents'])
    elif mode == 'mixte':
        prix = 'forfait de %s HT, puis temps passé à %s HT de l’heure' % (fmt(offre['fixe_ht_cents']), fmt(offre['taux_ht_cents']))
    elif mode == 'abonnement':
        prix = 'forfait de %s HT %s' % (fmt(offre['fixe_ht_cents']), PERIODES.get(offre.get('periode'), ''))
    else:
        prix = 'forfait de %s HT' % fmt(offre['fixe_ht_cents'])
    if offre.get('tva_statut') == 'confirme':
        prix += ' (TVA %s %% en sus)' % offre.get('tva_pct')
    morceaux = ['%s : %s' % (offre['libelle'], prix)]
    champs = offre.get('champs') or {}
    for r in offre.get('regles') or []:
        a = r['action']
        effet = {'ajout_fixe': '+ %s HT', 'reduction_fixe': '− %s HT'}.get(a['type'])
        if effet:
            effet = effet % fmt(a['montant_ht_cents'])
        elif a['type'] in ('pourcentage_base', 'reduction_pourcentage_base'):
            effet = '%s %s %% du forfait de base' % ('+' if a['type'] == 'pourcentage_base' else '−', a['pct'])
        else:
            effet = '+ %d min de temps prévisionnel' % a['minutes']
        conditions = ' et '.join('%s %s %s' % (champs[c['champ']].get('question') or c['champ'], OPERATEURS[c['op']],
                                               'oui' if c['valeur'] is True else 'non' if c['valeur'] is False else c['valeur']) for c in r['si'])
        morceaux.append('condition « %s » (%s) : %s' % (r['libelle_client'], conditions, effet))
    if offre.get('inclus'):
        morceaux.append('comprend : ' + ' ; '.join(offre['inclus']))
    if offre.get('exclus'):
        morceaux.append('ne comprend pas : ' + ' ; '.join(offre['exclus']))
    questions = [c.get('question') for c in champs.values() if c.get('question')]
    if questions:
        morceaux.append('précisions à demander avant tout montant ferme : ' + ' ; '.join(questions))
    return ' — '.join(morceaux)


def prestations_pour_ia(desk, au=None):
    """Prestations PUBLIÉES, applicables et marquées « utilisable par l'IA » (désactivé par défaut) : ``[{offre_id, version, texte}]``."""
    au = str(au or jour_cabinet(desk))[:10]
    out = []
    for row in desk.db.execute("SELECT id FROM catalogue_offres56109 WHERE etat='publie' AND profil_avocat='' ORDER BY id"):
        try:
            offre = offre_applicable(desk, row['id'], au)
        except Stop:
            continue
        if offre.get('actif_pour_ia'):
            out.append({'offre_id': offre['id'], 'version': offre['version'], 'texte': texte_client(offre), 'montants': montants_de(offre)})
    return out


def presentation_pour_ia(desk, au=None, limite=12):
    """Texte ajouté à la présentation du cabinet transmise au modèle pour un premier contact : les seules prestations et les seuls
    montants utilisables. Vide si le cabinet n'a rien publié pour l'IA (le modèle garde alors sa consigne : ne rien avancer)."""
    offres = prestations_pour_ia(desk, au)[:limite]
    if not offres:
        return ''
    return ('Prestations et honoraires publiés par le cabinet (seuls montants utilisables ; n’avancer aucun autre montant ni fourchette ; si la demande ne '
            'correspond à aucune de ces prestations, le dire et proposer un premier échange ; un montant soumis à condition se présente comme tel et la '
            'précision manquante se demande) :\n' + '\n'.join('- ' + o['texte'] for o in offres))


def montants_de(offre):
    """Montants (centimes) qu'une prestation publiée autorise à citer : forfait, taux, suppléments et réductions à montant fixe."""
    out = {v for v in (offre.get('fixe_ht_cents'), offre.get('taux_ht_cents')) if type(v) is int}
    for r in offre.get('regles') or []:
        if type((r.get('action') or {}).get('montant_ht_cents')) is int:
            out.add(r['action']['montant_ht_cents'])
    return sorted(out)


_MONTANT = re.compile(r'(?<![\w,.])(\d{1,3}(?:[  \xa0.]\d{3})+|\d+)(?:[,.](\d{1,2}))?\s*(?:€|euros?\b|eur\b)', re.I)


def montants_hors_tarif(texte, autorises):
    """Montants en euros cités dans un texte (projet de réponse, devis rédigé) qui ne figurent PAS parmi les montants autorisés (centimes) :
    lignes, total HT, TVA, TTC et provision d'un calcul, ou montants d'une prestation publiée. Liste vide : aucun montant inventé."""
    permis = {int(x) for x in autorises if type(x) is int}
    out = []
    for m in _MONTANT.finditer(str(texte or '')):
        valeur = int(re.sub(r'\D', '', m.group(1))) * 100 + int((m.group(2) or '0').ljust(2, '0'))
        if valeur not in permis and valeur not in out:
            out.append(valeur)
    return out


def montants_du_calcul(resultat):
    """Montants (centimes) d'un calcul : ce qu'un courriel, un devis ou une convention peut reprendre, et rien d'autre."""
    out = {l['montant_ht_cents'] for l in resultat.get('lignes') or []} | {abs(l['montant_ht_cents']) for l in resultat.get('lignes') or []}
    for k in ('total_ht_cents', 'tva_cents', 'ttc_cents', 'provision_ht_cents'):
        if type(resultat.get(k)) is int:
            out.add(resultat[k])
    out |= {l['taux_ht_cents'] for l in resultat.get('lignes') or [] if type(l.get('taux_ht_cents')) is int}
    return sorted(out)


# ------------------------------------------------------------------------------------------------ Paramètres › Cabinet › Prestations et honoraires
def _euros(valeur):
    """Centimes → texte de saisie (« 1250,50 ») ; rien → vide (prix non renseigné)."""
    return '' if valeur is None else '%d,%02d' % (valeur // 100, valeur % 100)


def _form(route, corps, bouton, recharger=True, cls='', confirmer=''):
    from html import escape as e
    return ('<form class="d97-form d98-form%s" data-d97-api="%s"%s novalidate%s><input type="hidden" name="cle" value="%s">%s<div class="d98-actions">'
            '<button class="ax-btn%s" type="submit">%s</button><span class="d97-statut" role="status" aria-live="polite"></span></div></form>') % (
        (' ' + cls) if cls else '', PREFIXE + route, ' data-d97-apres="recharger"' if recharger else '',
        (' data-d98-confirmer="%s"' % e(confirmer, quote=True)) if confirmer else '', uuid.uuid4().hex, corps, ' ghost' if cls else '', e(bouton))


def _editeur(desk, v, cats):
    """Formulaire d'une prestation : description, prix, critères, conditions écrites en phrases (« Si … alors … »), périmètre. Sans code."""
    from html import escape as e
    c = (v or {}).get('contenu') or {}
    champ = lambda libelle, nom, valeur='', maxi=200, aide='', extra='': '<label>%s<input name="%s" value="%s" maxlength="%d"%s>%s<span class="d97-erreur" data-erreur="%s" role="alert"></span></label>' % (
        e(libelle), nom, e(str(valeur or ''), quote=True), maxi, extra, ('<span class="d97-aide">%s</span>' % e(aide)) if aide else '', nom)
    zone = lambda libelle, nom, lignes, aide='': '<label class="h109-large">%s<textarea name="%s" rows="3" maxlength="12000">%s</textarea>%s<span class="d97-erreur" data-erreur="%s" role="alert"></span></label>' % (
        e(libelle), nom, e('\n'.join(lignes) if isinstance(lignes, list) else str(lignes or '')), ('<span class="d97-aide">%s</span>' % e(aide)) if aide else '', nom)
    choix = lambda nom, options, actuel: '<select name="%s">%s</select>' % (nom, ''.join(
        '<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == actuel else '', e(lib)) for k, lib in options.items()))
    case = lambda libelle, nom, coche: '<label class="d98-case"><input type="checkbox" name="%s"%s> %s</label>' % (nom, ' checked' if coche else '', e(libelle))
    general = ('<div class="h109-grille">%s<label>Domaine%s</label>%s<label>Type de mission%s</label>%s<label>Mode%s<span class="d97-erreur" data-erreur="mode" role="alert"></span></label>'
               '%s%s%s%s%s%s%s%s</div>') % (
        champ('Intitulé pour le client', 'libelle', c.get('libelle'), 160), choix('categorie', {'': 'Sans domaine', **cats}, c.get('categorie') or ''),
        champ('Ou nouveau domaine', 'categorie_nouvelle', '', 80, 'Crée le domaine s’il n’existe pas.'), choix('type_mission', TYPES_MISSION, c.get('type_mission') or ''),
        champ('Procédure ou opération', 'procedure', c.get('procedure')), choix('mode', MODES, c.get('mode') or 'forfait'),
        champ('Forfait de base HT (€)', 'fixe_ht', _euros(c.get('fixe_ht_cents')), 20, 'Vide : prix non renseigné (jamais zéro).', ' inputmode="decimal"'),
        champ('Taux horaire HT (€)', 'taux_ht', _euros(c.get('taux_ht_cents')), 20, 'Temps passé et mixte.', ' inputmode="decimal"'),
        case('Mission gratuite (choix explicite)', 'gratuite', c.get('gratuite')) + champ('Motif de la gratuité', 'motif_gratuite', c.get('motif_gratuite'), 300),
        case('TVA confirmée pour cette prestation', 'tva_statut', c.get('tva_statut') == 'confirme') + champ(
            'Taux de TVA (%)', 'tva_pct', c.get('tva_pct'), 6, 'Non confirmée : aucun montant TTC n’est annoncé.', ' inputmode="decimal"'),
        champ('Provision (%)', 'provision_pct', c.get('provision_pct'), 6, '', ' inputmode="decimal"'), champ('Modalités de règlement', 'modalites', c.get('modalites'), 600),
        champ('Profil propre à un avocat (compte)', 'profil_avocat', c.get('profil_avocat'), 200, 'Vide : tarif du cabinet.'),
        champ('Variante de la prestation (identifiant)', 'variante_de', c.get('variante_de'), 80, 'Pour un profil d’avocat : prestation du cabinet remplacée.'))
    regulier = ('<fieldset class="h109-large"><legend>Assistance régulière</legend><div class="h109-grille"><label>Période%s<span class="d97-erreur" data-erreur="periode" role="alert">'
                '</span></label>%s%s%s%s%s</div></fieldset>') % (
        choix('periode', {'': 'À choisir', **{k: v.capitalize() for k, v in PERIODES.items()}}, c.get('periode') or ''),
        champ('Minutes incluses par période', 'minutes_incluses', '' if c.get('minutes_incluses') is None else c.get('minutes_incluses'), 8),
        champ('Prestations incluses', 'prestations_incluses', c.get('prestations_incluses'), 400), champ('Dépassement', 'depassement', c.get('depassement') or 'accord_prealable', 400,
                                                                                                         'Jamais un débit automatique : un accord complémentaire.'),
        champ('Report éventuel', 'report', c.get('report'), 400), champ('Engagement et résiliation', 'resiliation', c.get('resiliation'), 400))
    perimetre = (zone('Diligences comprises', 'inclus', c.get('inclus') or [], 'Une par ligne ; reprises dans chaque proposition.') + zone('Diligences exclues', 'exclus', c.get('exclus') or [])
                 + zone('Pièces nécessaires', 'pieces', c.get('pieces') or []) + zone('Documents associés (modèles)', 'documents_modeles', c.get('documents_modeles') or []))
    champs = c.get('champs') or {}
    rangs_champs = ''
    for n, (ident, ch) in enumerate(list(champs.items()) + [('', {}), ('', {})], 1):
        rangs_champs += '<div class="h109-grille h109-regle">%s%s<label>Type%s</label>%s%s</div>' % (
            champ('Critère (identifiant)', 'champ_%d_id' % n, ident, 40, '' if ident else 'Ex. enfants, juridiction, minutes_prevues'),
            champ('Question posée', 'champ_%d_question' % n, ch.get('question'), 200), choix('champ_%d_type' % n, TYPES_CHAMP, ch.get('type') or 'booleen'),
            champ('Valeurs (choix), séparées par « ; »', 'champ_%d_valeurs' % n, ' ; '.join(ch.get('valeurs') or []), 600),
            case('Toujours nécessaire au calcul', 'champ_%d_obligatoire' % n, ch.get('obligatoire')))
    rangs_regles = ''
    texte_valeur = lambda val: '' if val is None else ('oui' if val is True else 'non' if val is False else str(val))
    for n, r in enumerate(list(c.get('regles') or []) + [{}, {}], 1):
        si = (r.get('si') or []) + [{}] * MAX_CONDITIONS
        a = r.get('action') or {}
        autres = ''.join('%s<label>Comparaison%s</label>%s' % (   # conditions 3 et 4 : repliées, ouvertes quand la règle les utilise (rien n'est perdu à l'enregistrement)
            champ('Et le critère (facultatif)', 'regle_%d_champ%d' % (n, k), si[k - 1].get('champ'), 40, '', ' list="h109-criteres"'),
            choix('regle_%d_op%d' % (n, k), OPERATEURS, si[k - 1].get('op') or 'eq'), champ('Valeur', 'regle_%d_valeur%d' % (n, k), texte_valeur(si[k - 1].get('valeur')), 80))
            for k in range(3, MAX_CONDITIONS + 1))
        autres = '<details class="h109-large"%s><summary>Autres conditions (facultatif)</summary><div class="h109-grille">%s</div></details>' % (
            ' open' if len(r.get('si') or []) > 2 else '', autres)
        if a.get('type') in ('pourcentage_base', 'reduction_pourcentage_base'):
            montant = '' if a.get('pct') is None else str(a['pct'])
        elif a.get('type') == 'ajout_minutes':
            montant = '' if a.get('minutes') is None else str(a['minutes'])
        else:
            montant = _euros(a.get('montant_ht_cents'))
        rangs_regles += ('<div class="h109-grille h109-regle"><input type="hidden" name="regle_%d_id" value="%s">%s%s<label>Comparaison%s</label>%s%s<label>Comparaison%s</label>%s'
                         + autres.replace('%', '%%') + '<label>Alors%s<span class="d97-erreur" data-erreur="regle_%d_action" role="alert"></span></label>%s%s</div>') % (
            n, e(r.get('id') or '', quote=True), champ('Libellé pour le client', 'regle_%d_libelle' % n, r.get('libelle_client'), 200),
            champ('Si le critère', 'regle_%d_champ' % n, si[0].get('champ'), 40, '', ' list="h109-criteres"'), choix('regle_%d_op' % n, OPERATEURS, si[0].get('op') or 'eq'),
            champ('Valeur', 'regle_%d_valeur' % n, texte_valeur(si[0].get('valeur')), 80, 'oui, non, un nombre ou une valeur de la liste'),
            champ('Et le critère (facultatif)', 'regle_%d_champ2' % n, si[1].get('champ'), 40, '', ' list="h109-criteres"'), choix('regle_%d_op2' % n, OPERATEURS, si[1].get('op') or 'eq'),
            champ('Valeur', 'regle_%d_valeur2' % n, texte_valeur(si[1].get('valeur')), 80), choix('regle_%d_action' % n, ACTIONS, a.get('type') or 'ajout_fixe'), n,
            champ('Montant HT (€), pourcentage ou minutes', 'regle_%d_montant' % n, montant, 20, 'Vide : non renseigné.'),
            champ('Variantes exclusives (groupe)', 'regle_%d_groupe' % n, r.get('groupe'), 40, 'Deux règles du même groupe ne se cumulent pas.'))
    interne = ('<div class="h109-grille">%s%s%s</div>') % (zone('Description interne', 'description_interne', c.get('description_interne') or ''),
                                                          champ('Coût interne (€)', 'cout_interne', _euros(c.get('cout_interne_cents')), 20, 'Jamais transmis au client ni à l’IA.'),
                                                          zone('Note interne', 'note_interne', c.get('note_interne') or '', 'Jamais transmise au client ni à l’IA.'))
    ia = case('Utilisable par l’IA pour préparer une réponse (vue client seulement, après publication)', 'actif_pour_ia', c.get('actif_pour_ia'))
    return ('%s%s<fieldset class="h109-large"><legend>Périmètre</legend><div class="h109-grille">%s</div></fieldset>'
            '<fieldset class="h109-large"><legend>Critères qui font varier le prix</legend><datalist id="h109-criteres">%s</datalist>%s<span class="d97-erreur" data-erreur="champs" role="alert"></span></fieldset>'
            '<fieldset class="h109-large"><legend>Conditions : « Si … alors … »</legend><p class="ax-muted">Le logiciel calcule ; un pourcentage porte sur le forfait de base. '
            'Un critère inconnu donne une question, jamais un prix.</p>%s<span class="d97-erreur" data-erreur="regles" role="alert"></span></fieldset>'
            '<fieldset class="h109-large"><legend>Interne au cabinet</legend>%s</fieldset>%s') % (
        general, regulier, perimetre, ''.join('<option value="%s">' % e(k, quote=True) for k in champs), rangs_champs, rangs_regles, interne, ia)


def _prix_html(c):
    from html import escape as e
    if c.get('gratuite') and c.get('fixe_ht_cents') == 0:
        return 'mission gratuite'
    morceaux = []
    if c.get('mode') != 'temps':
        morceaux.append(('forfait %s HT' % fmt(c['fixe_ht_cents'])) if c.get('fixe_ht_cents') is not None else 'forfait non renseigné')
    if c.get('mode') in ('temps', 'mixte'):
        morceaux.append(('%s HT de l’heure' % fmt(c['taux_ht_cents'])) if c.get('taux_ht_cents') is not None else 'taux non renseigné')
    if c.get('mode') == 'abonnement' and c.get('periode'):
        morceaux.append(PERIODES[c['periode']])
    return e(' · '.join(morceaux))


def section_html(desk, prefix, owner, role, args=None):
    """Paramètres › Cabinet › Prestations et honoraires : recherche, filtres, prestations, Ajouter, Modifier, Dupliquer, Publier, Archiver,
    Importer, Simuler. Rendu par le serveur, échappé ; formulaires envoyés par le script commun (jeton CSRF, clé d'idempotence)."""
    from html import escape as e
    from urllib.parse import urlencode
    from . import honoraires5653 as fees
    args = args or {}
    titre = '<section class="ax-card h109" id="catalogue56109"><h2>Prestations et honoraires</h2>%s</section>'
    try:
        data = lister(desk, (owner, role), {k: str(args.get('h109_' + k) or '') for k in ('q', 'categorie', 'type_mission', 'etat')})
    except Stop as ex:
        return titre % ('<p class="ax-alert" role="status">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Section indisponible pour le moment.')))
    cats, gerer = data['categories'], data['peut_gerer']
    intro = ('<p class="ax-muted">Les prestations que le cabinet propose, leur prix et leurs conditions. Seule une prestation publiée sert à un chiffrage ; '
             'le montant est toujours calculé par le logiciel, jamais par l’IA. Ce catalogue exprime les choix du cabinet : il ne contient aucun barème de la profession.</p>')
    if not fees.tarifs_confirmes(desk):
        cfg = fees.settings(desk)
        intro += ('<div class="h109-avis" role="note"><p>Le taux horaire par défaut (%s € HT) et les autres réglages d’honoraires sont des valeurs techniques d’installation : '
                  'tant qu’ils ne sont pas confirmés, ils ne sont jamais présentés comme le tarif du cabinet. <a href="%s">Les modifier</a></p>%s</div>') % (
            e(str(cfg['taux_horaire'])), e(prefix + '/facturation?vue=reglages', quote=True),
            _form('tarifs/confirmer', '', 'Confirmer ces valeurs comme tarifs du cabinet', cls='d98-form-ligne') if gerer else '')
    filtres = ('<form class="h109-filtres" method="get" action="%s#catalogue56109"><input type="hidden" name="rubrique" value="cabinet">'
               '<label>Rechercher<input type="search" name="h109_q" value="%s" maxlength="80"></label><label>Domaine<select name="h109_categorie"><option value="">Tous</option>%s</select></label>'
               '<label>Type de mission<select name="h109_type_mission"><option value="">Tous</option>%s</select></label><label>État<select name="h109_etat"><option value="">En cours</option>%s</select></label>'
               '<button class="ax-btn ghost" type="submit">Filtrer</button></form>') % (
        e(prefix + '/parametres', quote=True), e(str(args.get('h109_q') or ''), quote=True),
        ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if args.get('h109_categorie') == k else '', e(v)) for k, v in cats.items()),
        ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if args.get('h109_type_mission') == k else '', e(v)) for k, v in TYPES_MISSION.items() if k),
        ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if args.get('h109_etat') == k else '', e(v)) for k, v in ETATS.items()))
    lignes = []
    for v in data['prestations']:
        c = v['contenu']
        cache = '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">' % (e(v['id'], quote=True), v['version'])
        etat = '<span class="h109-etat h109-etat-%s">%s</span>' % (e(v['etat'], quote=True), e(v['etat_libelle']))
        if v['publiee']:
            etat += ' <span class="ax-muted">version %d publiée par %s, applicable à compter du %s%s</span>' % (
                v['publiee']['version'], e(v['publiee']['valide_par']), e(v['publiee']['debut']), (' jusqu’au ' + e(v['publiee']['fin'])) if v['publiee']['fin'] else '')
            if v['a_publier'] and v['etat'] == 'publie':
                etat += ' <span class="h109-etat h109-etat-brouillon">modifications non publiées</span>'
        gestes = ''
        if gerer:
            gestes += '<details><summary>Modifier</summary>%s</details>' % _form('offres/modifier', cache + _editeur(desk, v, cats), 'Enregistrer le brouillon')
            gestes += _form('offres', '<input type="hidden" name="copie_de" value="%s">' % e(v['id'], quote=True), 'Dupliquer', cls='d98-form-ligne')
            if data['peut_publier'] and v['a_publier'] and v['etat'] != 'archive':
                gestes += '<details><summary>Publier</summary>%s</details>' % _form(
                    'offres/publier', cache + '<div class="h109-grille"><label>Applicable à compter du<input type="date" name="debut"><span class="d97-erreur" data-erreur="debut" role="alert"></span>'
                    '</label><label>Jusqu’au (facultatif)<input type="date" name="fin"><span class="d97-erreur" data-erreur="fin" role="alert"></span></label></div>'
                    '<p class="ax-muted">La publication contrôle toutes les conditions puis fige une version : les propositions déjà établies gardent la leur.</p>'
                    + ''.join('<span class="d97-erreur" data-erreur="%s" role="alert"></span>' % k for k in ('fixe_ht', 'taux_ht', 'inclus', 'tva_pct', 'periode', 'champs', 'motif_gratuite')),
                    'Publier cette version')
            if v['etat'] != 'archive':
                gestes += _form('offres/archiver', cache, 'Archiver', cls='d98-retirer', confirmer='Archiver cette prestation ? Elle ne servira plus à de nouveaux chiffrages.')
        saisies = ''.join('<label>%s<input name="donnee_%s" maxlength="80" placeholder="%s"></label>' % (
            e(ch.get('question') or k), e(k, quote=True), 'oui / non' if ch['type'] == 'booleen' else ('minutes' if ch['type'] == 'minutes' else ''))
            for k, ch in (c.get('champs') or {}).items())
        essai = '<input type="hidden" name="essai" value="1">' if v['a_publier'] and gerer else ''
        if v['version_publiee'] or essai:
            gestes += '<details><summary>Simuler%s</summary>%s</details>' % (
                ' (essai du brouillon)' if essai else '', _form('simuler', '<input type="hidden" name="offre" value="%s">%s<div class="h109-grille">%s</div><p class="ax-muted">Laissez vide ce qui '
                                                                'n’est pas connu : le logiciel pose alors la question au lieu d’annoncer un prix.</p>' % (e(v['id'], quote=True), essai, saisies),
                                                                'Calculer', recharger=False))
        lignes.append('<li class="h109-offre"><div class="h109-tete"><strong>%s</strong> %s</div><p class="ax-muted">%s · %s · <span class="h109-prix">%s</span>%s</p>'
                      '<div class="h109-gestes">%s</div></li>' % (
                          e(c.get('libelle') or v['id']), etat, e(v['categorie_libelle']), e(v['mode_libelle']), _prix_html(c),
                          (' · profil de %s' % e(v['profil_avocat'])) if v['profil_avocat'] else '', gestes))
    liste = ('<ul class="h109-liste">%s</ul>' % ''.join(lignes)) if lignes else \
        '<p class="ax-muted">Aucune prestation%s. La bibliothèque ci-dessous propose des trames à adapter, sans prix.</p>' % (' pour ces filtres' if any(args.get('h109_' + k) for k in ('q', 'categorie', 'type_mission', 'etat')) else '')
    ajout = ''
    if gerer:
        b = bibliotheque()
        libelles = {c['id']: c['libelle'] for c in b['categories']}
        options = ''.join('<optgroup label="%s">%s</optgroup>' % (e(libelles.get(cat, cat), quote=True), ''.join(
            '<option value="%s">%s</option>' % (e(p['id'], quote=True), e(p['libelle'])) for p in b['prestations'] if p['categorie'] == cat)) for cat in libelles)
        ajout = ('<details><summary class="ax-btn ghost">Ajouter une prestation</summary>%s</details>'
                 '<details><summary class="ax-btn ghost">Partir d’une trame de la bibliothèque (%d trames, sans prix)</summary><p class="ax-muted">%s Une trame devient un brouillon du '
                 'cabinet : elle n’est ni publiée ni utilisée tant que vous n’avez pas renseigné vos prix et publié.</p>%s</details>'
                 '<details><summary class="ax-btn ghost">Importer un catalogue (JSON)</summary><p class="ax-muted">Le rapprochement se fait par identifiant, jamais par libellé. '
                 'Sans la case cochée, seul le rapport des différences est rendu ; rien n’est écrit. Un import ne publie jamais rien.</p>%s</details>') % (
            _form('offres', _editeur(desk, None, cats), 'Créer le brouillon'), len(b['prestations']), e(b['notice']),
            _form('offres', '<label>Trame<select name="trame">%s</select></label>' % options, 'Créer un brouillon à partir de cette trame'),
            _form('importer', '<label class="h109-large">Contenu du fichier JSON<textarea name="contenu" rows="5" maxlength="250000"></textarea></label>'
                              '<label class="d98-case"><input type="checkbox" name="appliquer"> Importer en brouillon les prestations nouvelles ou différentes</label>', 'Comparer ou importer'))
    lien = '<p class="ax-muted"><a href="%s">Honoraires et facturation</a> : estimations, devis, conventions et factures du cabinet.</p>' % e(prefix + '/facturation?' + urlencode({'vue': 'honoraires'}), quote=True)
    return titre % (intro + filtres + liste + '<div class="h109-gestes">%s</div>' % ajout + lien)


# ------------------------------------------------------------------------------------------------ routes
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('catalogue', 'versions', 'bibliotheque', 'export', 'tarif')
ROUTES_POST = ('offres', 'offres/modifier', 'offres/publier', 'offres/archiver', 'importer', 'simuler', 'proposer', 'tarifs/confirmer')


def allowed(role, method, api_):
    """Contrôle de rôle sans état (``standalone_auth``) : les trois rôles atteignent les routes ; chaque droit (« voir_honoraires »,
    « facturation », « réglages ») est relu par le service dans le registre des droits ; publier n'est jamais ouvert à un assistant."""
    route = str(api_ or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else None
    if role not in ROLES or route is None:
        return False
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET
    if method != 'POST' or route not in ROUTES_POST:
        return False
    return role != 'assistant' or route != 'offres/publier'


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _recu(desk, compte, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    r = desk.db.execute('SELECT empreinte, reponse FROM catalogue_recus56109 WHERE compte=? AND route=? AND cle=?', (compte, route, cle)).fetchone()
    if r and r['empreinte'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(r['reponse']) if r else None


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        corps = {'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}
        detail = getattr(ex, 'detail', None)
        if isinstance(detail, dict) and detail:
            corps['champs'] = detail
        return _out(corps, STATUTS.get(code, '400 Bad Request'))
    except sqlite3.IntegrityError as ex:
        if 'version_publiee_immuable' in str(ex):
            return _out({'error': 'version_publiee_immuable', 'message': 'Une version publiée ne se modifie pas : publiez une nouvelle version.'}, '409 Conflict')
        raise


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    acteur = (owner, role)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    if not allowed(role, method, PREFIXE + route):
        raise Stop('role_insuffisant' if (role not in ROLES or route == 'offres/publier') else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if route == 'catalogue':
            return _out(lister(desk, acteur, {k: str(args.get(k) or '') for k in ('q', 'categorie', 'type_mission', 'etat', 'archives')}))
        if route == 'versions':
            return _out({'versions': versions(desk, acteur, args.get('id'))})
        if route == 'bibliotheque':
            _droit(desk, _acteur(acteur), 'voir_honoraires')
            b = bibliotheque()
            return _out({'notice': b['notice'], 'categories': b['categories'], 'trames': [{'id': p['id'], 'libelle': p['libelle'], 'categorie': p['categorie'], 'mode': p['mode']}
                                                                                          for p in b['prestations']]})
        if route == 'export':
            return _out(exporter(desk, acteur))
        return _out({k: v for k, v in tarif_applicable(desk, acteur, args.get('dossier'), str(args.get('offre') or '') or None).items() if k != 'offre'})
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné, objet JSON
    cle = str(data.get('cle') or '')
    if route == 'simuler':         # calcul sans effet : aucune clé d'idempotence nécessaire, rien n'est écrit
        s = simuler(desk, acteur, data.get('offre'), data, str(data.get('dossier') or '') or None, str(data.get('au') or '') or None, essai=data.get('essai') in (True, 'true', 'on', '1'))
        return _out({**{k: v for k, v in s.items() if not k.startswith('_')}, 'noreload': True,
                     'message': ' '.join(s['explication'])[:900]})
    empreinte = _sha({k: v for k, v in data.items() if k != 'cle'} | {'route': route})
    ancien = _recu(desk, owner, route, cle, empreinte)
    if ancien is not None:
        return _out({**ancien, 'rejoue': True})
    statut = '200 OK'
    if route == 'offres':
        out, statut = creer(desk, acteur, data), '201 Created'
    elif route == 'offres/modifier':
        out = modifier(desk, acteur, data.get('id'), data.get('version'), data)
    elif route == 'offres/publier':
        out = publier(desk, acteur, data.get('id'), data.get('version'), data.get('debut'), data.get('fin'))
    elif route == 'offres/archiver':
        out = archiver(desk, acteur, data.get('id'), data.get('version'))
    elif route == 'importer':
        out = importer(desk, acteur, data.get('contenu') or '', appliquer=data.get('appliquer') in (True, 'true', 'on', '1'))
    elif route == 'tarifs/confirmer':
        from . import honoraires5653 as fees
        a = _acteur(acteur)
        _droit(desk, a, 'reglages')
        fees.confirmer_tarifs(desk, a.compte)
        out = {'message': 'Taux et réglages d’honoraires confirmés comme tarifs du cabinet.'}
    else:
        out = proposer(desk, acteur, data.get('offre'), data, data.get('dossier'), str(data.get('au') or '') or None)
        statut = '201 Created'
    desk.db.execute('INSERT OR IGNORE INTO catalogue_recus56109 VALUES(?,?,?,?,?,?)', (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str), _maintenant()))
    desk.db.commit()
    return _out(out, statut)
