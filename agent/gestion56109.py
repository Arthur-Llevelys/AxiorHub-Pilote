"""5.6.109 (lot G1 : G01, G02, G09) : préfacturation assistée, assistance régulière, fonds, frais et débours.

Aucune page ni aucun registre en doublon : ce module enrichit les écrans et les registres existants.

- **G01 — préfacturation** (section « Préfacturation » de Temps et honoraires) : les traces d'une même diligence (rendez-vous de
  l'agenda et son compte rendu, appel signalé deux fois, même source vue depuis deux postes) se rattachent à **une** diligence
  (``prefact_diligences56109``, unicité par source au niveau du cabinet, traces dans ``prefact_traces56109``). La durée inconnue reste
  ``NULL`` (jamais zéro : contrainte SQLite) ; l'estimation par barème ou par l'agenda est étiquetée « estimée » et exige une
  confirmation explicite ; deux intervalles qui se chevauchent pour une même personne exigent un choix. La validation se fait **par
  lot atomique** (une transaction : versions, sources déjà saisies ou réservées, temps validés ``time500_entries`` à identifiant
  déterministe, reçu) ; « Préparer la facture » reste le service existant (``honoraires5697.preparer_brouillon``, réservations
  ``b53_allocations``). Montants : centimes entiers, ``Decimal``, arrondi à la moitié supérieure de ``catalogue56109.arrondi``
  (lot D) — la même convention partout (``time500._amount`` y délègue). Une facture émise n'est jamais recalculée.
- **G02 — assistance régulière** (section de la fiche du contact client) : contrat versionné (versions immuables), périodes explicites
  [début inclus, fin exclue] suivant le calendrier civil, quota partagé par les seuls dossiers convenus, consommation unique par temps
  validé, compteur par unité (minutes OU prestations, jamais convertis), solde **indéterminé** dès qu'une durée manque, convention
  qualifiée (signée avec preuve ≠ envoyée ≠ approuvée), compte rendu de période = **un** livrable du registre commun (Produire).
- **G09 — fonds, frais et débours** (vue financière du dossier) : honoraires (factures et règlements ``b53``), frais et débours
  (``frais5697`` et mouvements complémentaires), fonds de tiers (relevé documenté, jamais des honoraires), séparés par nature, par
  devise et par état (prévu ≠ constaté) ; un règlement n'est compté qu'une fois ; une correction est un mouvement compensateur ; un
  changement de coordonnées bancaires est versionné, masqué, « À vérifier » jusqu'à la vérification indépendante d'une personne
  habilitée (jamais l'IA). Aucun paiement, aucun mouvement CARPA, aucune qualification fiscale automatique.

Aucun appel de modèle : tout fonctionne IA en pause. Tables créées au démarrage (``Desk._prepare``), jamais pendant une lecture.
Routes ``/api440/m56109/gestion/…`` : droits relus par le service, 404 neutre, 409 (version ou clé), 422 (champs), no-store.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape as e
import hashlib
import json
import re
import sqlite3
from urllib.parse import urlencode
import uuid

from .common import Stop, digest

PREFIXE = 'm56109/gestion/'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS prefact_diligences56109(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_version TEXT NOT NULL DEFAULT '',
  auteur TEXT NOT NULL DEFAULT '', jour TEXT NOT NULL, debut TEXT NOT NULL DEFAULT '', fin TEXT NOT NULL DEFAULT '',
  minutes INTEGER CHECK(minutes IS NULL OR (typeof(minutes)='integer' AND minutes>0 AND minutes<=1440)),
  origine_duree TEXT NOT NULL CHECK(origine_duree IN ('mesuree','saisie','estimee','inconnue')), bareme TEXT NOT NULL DEFAULT '',
  libelle TEXT NOT NULL DEFAULT '', statut TEXT NOT NULL CHECK(statut IN ('a_completer','estimation_a_confirmer','a_valider','chevauchement_a_verifier',
  'valide','ecarte','fusionnee','deja_saisie')), chevauchements TEXT NOT NULL DEFAULT '[]', distincts TEXT NOT NULL DEFAULT '[]',
  fusion_vers TEXT NOT NULL DEFAULT '', entree_id TEXT NOT NULL DEFAULT '', taux_cents INTEGER, montant_cents INTEGER, convention TEXT NOT NULL DEFAULT '{}',
  valide_par TEXT NOT NULL DEFAULT '', valide_le TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
  cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL, UNIQUE(source_kind, source_id), CHECK(origine_duree<>'inconnue' OR minutes IS NULL));
CREATE INDEX IF NOT EXISTS prefact_diligences56109_m ON prefact_diligences56109(matter, statut, jour);
CREATE TABLE IF NOT EXISTS prefact_traces56109(trace TEXT PRIMARY KEY, diligence TEXT NOT NULL, kind TEXT NOT NULL, cree_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS prefact_traces56109_d ON prefact_traces56109(diligence);
CREATE TABLE IF NOT EXISTS gestion_recus56109(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  quand TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
CREATE TABLE IF NOT EXISTS assistance_contrats56109(id TEXT PRIMARY KEY, client TEXT NOT NULL UNIQUE, version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
  etat TEXT NOT NULL CHECK(etat IN ('actif','clos')), contenu TEXT NOT NULL, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, modifie_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assistance_versions56109(contrat_id TEXT NOT NULL, version INTEGER NOT NULL, contenu TEXT NOT NULL, convention TEXT NOT NULL DEFAULT '{}',
  auteur TEXT NOT NULL, applicable_du TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(contrat_id, version));
CREATE TRIGGER IF NOT EXISTS assistance_versions56109_fige_maj BEFORE UPDATE ON assistance_versions56109 BEGIN SELECT RAISE(ABORT, 'version_contrat_immuable'); END;
CREATE TRIGGER IF NOT EXISTS assistance_versions56109_fige_sup BEFORE DELETE ON assistance_versions56109 BEGIN SELECT RAISE(ABORT, 'version_contrat_immuable'); END;
CREATE TABLE IF NOT EXISTS assistance_periodes56109(id TEXT PRIMARY KEY, contrat_id TEXT NOT NULL, contrat_version INTEGER NOT NULL, debut TEXT NOT NULL,
  fin TEXT NOT NULL, quotas TEXT NOT NULL, report TEXT NOT NULL DEFAULT '{}', partielle INTEGER NOT NULL DEFAULT 0, etat TEXT NOT NULL CHECK(etat IN ('ouverte','close')),
  bilan TEXT NOT NULL DEFAULT '{}', compte_rendu TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, close_le TEXT NOT NULL DEFAULT '',
  UNIQUE(contrat_id, debut), CHECK(debut < fin));
CREATE TABLE IF NOT EXISTS assistance_consommations56109(id TEXT PRIMARY KEY, contrat_id TEXT NOT NULL, periode_id TEXT NOT NULL, diligence TEXT NOT NULL,
  matter TEXT NOT NULL, service TEXT NOT NULL, unite TEXT NOT NULL CHECK(unite IN ('minutes','prestation')),
  quantite INTEGER CHECK(quantite IS NULL OR (typeof(quantite)='integer' AND quantite>=0)), auteur TEXT NOT NULL, at TEXT NOT NULL,
  UNIQUE(contrat_id, diligence), UNIQUE(periode_id, diligence, service));
CREATE TABLE IF NOT EXISTS mouvements56109(id TEXT PRIMARY KEY, matter TEXT NOT NULL, nature TEXT NOT NULL CHECK(nature IN ('honoraires','frais','debours','fonds_tiers')),
  sens TEXT NOT NULL CHECK(sens IN ('entree','sortie')), etat TEXT NOT NULL CHECK(etat IN ('prevu','constate')),
  montant_minor INTEGER NOT NULL CHECK(typeof(montant_minor)='integer' AND montant_minor>0), devise TEXT NOT NULL CHECK(length(devise)=3),
  jour TEXT NOT NULL, contrepartie TEXT NOT NULL DEFAULT '', beneficiaire TEXT NOT NULL DEFAULT '', justificatif TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL, source_id TEXT NOT NULL, compense TEXT NOT NULL DEFAULT '', qualification TEXT NOT NULL DEFAULT '{}',
  rapproche_par TEXT NOT NULL DEFAULT '', rapproche_le TEXT NOT NULL DEFAULT '', rapprochement TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, cree_le TEXT NOT NULL, UNIQUE(source_kind, source_id));
CREATE INDEX IF NOT EXISTS mouvements56109_m ON mouvements56109(matter, nature, devise);
CREATE TABLE IF NOT EXISTS coordonnees56109(id TEXT PRIMARY KEY, beneficiaire TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), iban_masque TEXT NOT NULL,
  iban_empreinte TEXT NOT NULL, cle_iban_correcte INTEGER NOT NULL, provenance TEXT NOT NULL, recu_le TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL CHECK(etat IN ('a_verifier','verifie','rejete')), auteur TEXT NOT NULL, verifie_par TEXT NOT NULL DEFAULT '', verifie_le TEXT NOT NULL DEFAULT '',
  reference_verification TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, UNIQUE(beneficiaire, version));
'''
TABLES = ('prefact_diligences56109', 'prefact_traces56109', 'gestion_recus56109', 'assistance_contrats56109', 'assistance_versions56109',
          'assistance_periodes56109', 'assistance_consommations56109', 'mouvements56109', 'coordonnees56109')

ORIGINES = {'mesuree': 'mesurée', 'saisie': 'saisie par vous', 'estimee': 'estimée', 'inconnue': 'inconnue — à compléter'}
STATUTS_LIGNE = {'a_completer': 'Durée à compléter', 'estimation_a_confirmer': 'Estimation à confirmer', 'a_valider': 'À valider',
                 'chevauchement_a_verifier': 'Chevauchement à vérifier', 'valide': 'Validée', 'ecarte': 'Écartée', 'fusionnee': 'Rattachée à une autre diligence',
                 'deja_saisie': 'Déjà saisie dans les temps'}
FINAUX = ('valide', 'ecarte', 'fusionnee', 'deja_saisie')
SOURCES = {'mail': 'Courriel', 'doc': 'Pièce modifiée', 'liv': 'Livrable préparé', 'rdv': 'Rendez-vous', 'cr': 'Compte rendu', 'appel': 'Appel',
           'travail': 'Travail achevé'}
TRACES_MANUELLES = ('appel', 'cr', 'rdv', 'travail')
NATURES = {'honoraires': 'Honoraires', 'frais': 'Frais', 'debours': 'Débours', 'fonds_tiers': 'Fonds de tiers'}
PERIODICITES = {'mois': 'mensuelle', 'trimestre': 'trimestrielle', 'annee': 'annuelle'}
UNITES = {'minutes': 'minutes', 'prestation': 'prestations'}
EXPOSANTS = {'JPY': 0, 'KRW': 0, 'XOF': 0, 'XAF': 0, 'XPF': 0, 'CLP': 0, 'ISK': 0, 'KWD': 3, 'BHD': 3, 'TND': 3, 'JOD': 3, 'OMR': 3}
DEVISE = re.compile(r'^[A-Z]{3}$')
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,80}$')
IDENT = re.compile(r'^[A-Za-z0-9_.:|@-]{1,160}$')
MAX_LOT = 200

MESSAGES = {'introuvable': 'Élément introuvable.', 'route_inconnue': 'Route inconnue.', 'role_insuffisant': 'Action réservée à un autre rôle ou à un droit que vous n’avez pas.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.', 'champs_invalides': 'Certains champs sont à corriger.',
            'version_perimee': 'Cet élément a été modifié entre-temps : rechargez la page ; votre saisie n’a rien écrasé.',
            'cle_idempotence_requise': 'Page incomplète : rechargez-la.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
            'diligence_deja_validee': 'Cette diligence est déjà validée (sur ce poste ou un autre) : rien n’a été ajouté.',
            'diligence_deja_saisie': 'Cette diligence figure déjà dans un temps validé : rien n’a été ajouté.',
            'diligence_deja_reservee': 'Ce temps est déjà réservé par une facture : rien n’a été ajouté.',
            'tarif_modifie': 'Le taux du dossier a changé depuis l’affichage : relisez le montant puis validez de nouveau.',
            'rien_a_valider': 'Sélectionnez au moins une ligne.', 'lot_trop_grand': 'Lot trop grand : validez au plus 200 lignes à la fois.',
            'contrat_existant': 'Ce client a déjà un contrat d’assistance : modifiez-le (nouvelle version).',
            'periode_existante': 'Cette période est déjà ouverte.', 'periode_close': 'Cette période est close : elle ne se modifie plus.',
            'verification_independante_requise': 'La vérification doit être faite par une autre personne habilitée que celle qui a enregistré le changement.',
            'verification_humaine_requise': 'Une vérification bancaire est faite par une personne, jamais par l’IA.',
            'honoraires_par_reglement': 'Un encaissement d’honoraires s’enregistre sur la facture (règlement) : il n’est pas recopié ici.',
            'paiement_deja_compte': 'Ce règlement est déjà compté : rien n’a été ajouté.'}
STATUTS_HTTP = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'dossier_absent': '404 Not Found', 'dossier_reserve': '404 Not Found',
                'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'acteur_requis': '403 Forbidden',
                'verification_humaine_requise': '403 Forbidden',
                'version_perimee': '409 Conflict', 'version_en_conflit': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict',
                'diligence_deja_validee': '409 Conflict', 'diligence_deja_saisie': '409 Conflict', 'diligence_deja_reservee': '409 Conflict',
                'tarif_modifie': '409 Conflict', 'contrat_existant': '409 Conflict', 'periode_existante': '409 Conflict', 'periode_close': '409 Conflict',
                'paiement_deja_compte': '409 Conflict', 'verification_independante_requise': '409 Conflict',
                'champs_invalides': '422 Unprocessable Entity', 'cle_idempotence_requise': '422 Unprocessable Entity', 'rien_a_valider': '422 Unprocessable Entity',
                'lot_trop_grand': '422 Unprocessable Entity', 'honoraires_par_reglement': '422 Unprocessable Entity',
                'controle_acces_indisponible': '503 Service Unavailable'}


class Refus(Stop):
    """Refus avec détail par champ (422) ou par ligne (409)."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


# ------------------------------------------------------------------------------------------------ outils
def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(valeur):
    return hashlib.sha256(json.dumps(valeur, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def _g():
    from . import gestion5697 as g
    return g


def _acteur(acteur):
    return _g().acteur_de(acteur)


def _droit(desk, acteur, nom):
    _g().droit(desk, acteur, nom)


def _peut(desk, acteur, nom):
    try:
        _droit(desk, acteur, nom)
        return True
    except Stop:
        return False


def _dossier(desk, acteur, mid):
    """Dossier lisible par l'acteur ; absent ou réservé : même 404 neutre."""
    _g().dossier(desk, acteur, str(mid or ''))
    return str(mid)


def _perimetre(desk, acteur):
    from .common import load_matters
    autorises = _g().perimetre(desk, acteur)
    return [str(m['id']) for m in load_matters(desk.c, unfiltered=True) if str(m['id']) in autorises]


def jour_cabinet(desk):
    """Jour du cabinet (fuseau du cabinet), jamais la date du serveur."""
    from .catalogue56109 import jour_cabinet as j
    return date.fromisoformat(j(desk))


def _jour(valeur, requis=True):
    from .facturation5653 import day_value
    return day_value(valeur, requis)


def _texte(valeur, n=300):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', str(valeur or ''))).strip()[:n]


def _entier(valeur, nom, mini=1, maxi=1440, err=None):
    """Entier saisi (texte ou entier) ; un flottant, un booléen ou un texte non numérique est refusé, jamais arrondi."""
    if isinstance(valeur, bool) or isinstance(valeur, float):
        raise ValueError(nom)
    try:
        n = int(str(valeur).strip())
    except (TypeError, ValueError):
        raise ValueError(nom) from None
    if not mini <= n <= maxi:
        raise ValueError(nom)
    return n


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk._prepare``) seulement ; sans changement, aucune écriture."""
    from . import facturation5653, metier500
    metier500.ensure_schema(desk)
    facturation5653.ensure_schema(desk)   # registre de facturation (b53_*) prêt avant toute lecture de la préfacturation
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    if any(t not in existants for t in TABLES) or 'assistance_versions56109_fige_sup' not in existants:
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _recu(desk, compte, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    r = desk.db.execute('SELECT empreinte, reponse FROM gestion_recus56109 WHERE compte=? AND route=? AND cle=?', (compte, route, cle)).fetchone()
    if r and r['empreinte'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(r['reponse']) if r else None


def _inscrire_recu(desk, compte, route, cle, empreinte, reponse):
    desk.db.execute('INSERT INTO gestion_recus56109(compte,route,cle,empreinte,reponse,quand) VALUES(?,?,?,?,?,?)',
                    (compte, route, cle, empreinte, json.dumps(reponse, ensure_ascii=False, default=str), _maintenant()))


def _transaction(desk):
    return _g().transaction(desk)


# ------------------------------------------------------------------------------------------------ argent : une seule convention
def arrondi(valeur):
    """Arrondi monétaire commun du produit (lot D) : à l'unité mineure, moitié supérieure, ``Decimal`` — jamais de flottant."""
    from .catalogue56109 import arrondi as a
    return a(valeur)


def montant_temps(minutes, taux_cents):
    """Montant HT d'un temps (centimes) : minutes × taux horaire ÷ 60, arrondi commun. Durée ou taux absent : ``None`` (jamais zéro)."""
    if minutes is None or taux_cents is None:
        return None
    if isinstance(minutes, (bool, float)) or isinstance(taux_cents, (bool, float)):
        raise ValueError('montant_flottant_refuse')
    return arrondi(Decimal(int(minutes)) * Decimal(int(taux_cents)) / 60)


def quantite_heures(minutes):
    """Quantité d'heures d'une ligne de facture (6 décimales, moitié supérieure) : pour un taux en euros entiers, le montant de la ligne
    est égal au montant du temps validé (au lieu des 2 décimales de 5.6.97 : 50 min à 200 € donnaient 166,00 € au lieu de 166,67 €)."""
    from decimal import ROUND_HALF_UP
    return (Decimal(int(minutes)) / 60).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)


def exposant(devise):
    return EXPOSANTS.get(str(devise or '').upper(), 2)


def montant_minor(texte, devise='EUR'):
    """Montant saisi (« 1 250,50 ») → unités mineures entières de la devise ; un flottant, un négatif ou trop de décimales : refus."""
    devise = str(devise or '').upper()
    if not DEVISE.fullmatch(devise):
        raise ValueError('devise')
    if isinstance(texte, bool) or isinstance(texte, float):
        raise ValueError('montant')
    if isinstance(texte, int):
        d = Decimal(texte)
    else:
        brut = re.sub(r'[\s  €]', '', str(texte or '')).replace(',', '.')
        try:
            d = Decimal(brut)
        except InvalidOperation:
            raise ValueError('montant') from None
    n = d.scaleb(exposant(devise))
    if not d.is_finite() or d <= 0 or n != n.to_integral_value() or n > 10 ** 13:
        raise ValueError('montant')
    return int(n)


def afficher(minor, devise='EUR'):
    """Montant mineur → texte français (« 1 250,50 EUR ») ; jamais de conversion de devise."""
    if minor is None:
        return '—'
    x = exposant(devise)
    signe = '-' if minor < 0 else ''
    entier, frac = divmod(abs(int(minor)), 10 ** x) if x else (abs(int(minor)), 0)
    texte = '{:,}'.format(entier).replace(',', ' ')
    return '%s%s%s %s' % (signe, texte, (',%0*d' % (x, frac)) if x else '', devise)


def _euros(cents):
    from .metier500 import euros
    return euros(cents)


# ================================================================================================ G01 préfacturation
def _bareme_version(desk):
    from . import time500
    return 'bareme:' + _sha(time500.bareme(desk))[:12]


def _cle_source(trace):
    """Clé de la diligence d'une trace : le compte rendu d'un rendez-vous et le rendez-vous sont la même diligence."""
    kind, _, rest = trace.partition('|')
    if kind == 'cr':
        return 'rdv|' + rest
    return trace


def _diligence_id(source_kind, source_id):
    # Identifiant déterministe : deux postes qui relèvent la même source produisent la même diligence (fusion par identifiant).
    return 'dil-' + digest('prefact56109|%s|%s' % (source_kind, source_id))[:32]


def _entree_id(diligence_id):
    return digest('time500-prefact56109|' + diligence_id)


def _statut(row):
    if row['statut'] in FINAUX:
        return row['statut']
    if _ouverts(row):
        return 'chevauchement_a_verifier'
    if row['minutes'] is None:
        return 'a_completer'
    if row['origine_duree'] == 'estimee':
        return 'estimation_a_confirmer'
    return 'a_valider'


def _ouverts(row):
    chev = set(json.loads(row['chevauchements'] or '[]'))
    return sorted(chev - set(json.loads(row['distincts'] or '[]')))


def _traces_validees_time500(desk, refs):
    """Traces déjà comptées dans un temps validé par l'ancien chemin (proposition time500 validée)."""
    refs = [r for r in refs if r]
    if not refs:
        return set()
    marques = ','.join('?' * len(refs))
    return {r[0] for r in desk.db.execute("SELECT i.ref FROM time500_items i JOIN time500_proposals p ON p.id=i.proposal_id WHERE p.status='valide' "
                                          'AND i.ref IN (%s)' % marques, refs)}


def refs_validees(desk, refs):
    """Traces (``time500_items.ref``) déjà validées par la préfacturation : ``time500`` ne les compte plus dans une proposition ni ne les
    valide une seconde fois (une diligence, une entrée)."""
    refs = [str(r) for r in refs if r]
    if not refs:
        return set()
    marques = ','.join('?' * len(refs))
    try:
        return {r[0] for r in desk.db.execute("SELECT t.trace FROM prefact_traces56109 t JOIN prefact_diligences56109 d ON d.id=t.diligence "
                                              "WHERE d.statut='valide' AND t.trace IN (%s)" % marques, refs)}
    except sqlite3.OperationalError:   # table absente (base antérieure non migrée) : rien n'est préfacturé
        return set()


def _ajouter_trace(desk, trace, diligence_id):
    kind = trace.split('|', 1)[0]
    desk.db.execute('INSERT OR IGNORE INTO prefact_traces56109(trace,diligence,kind,cree_le) VALUES(?,?,?,?)', (trace, diligence_id, kind, _maintenant()))
    r = desk.db.execute('SELECT diligence FROM prefact_traces56109 WHERE trace=?', (trace,)).fetchone()
    return r['diligence']


def _enregistrer(desk, matter, trace, jour, libelle, minutes=None, origine='inconnue', bareme='', debut='', fin='', auteur='', version='', diligence_id=None):
    """Crée la diligence d'une trace ou y rattache la trace (une seule diligence par source au niveau du cabinet). Une durée inconnue
    reste NULL ; une durée saisie ou confirmée par l'avocat n'est jamais remplacée par une estimation."""
    if minutes is None:
        origine = 'inconnue' if origine in ('estimee', 'inconnue') else origine
    if origine == 'inconnue':
        minutes = None
    deja = desk.db.execute('SELECT diligence FROM prefact_traces56109 WHERE trace=?', (trace,)).fetchone()
    if deja:
        diligence_id = deja['diligence']
    cle = _cle_source(trace)
    kind, _, sid = cle.partition('|')
    diligence_id = diligence_id or _diligence_id(kind, sid)
    row = desk.db.execute('SELECT * FROM prefact_diligences56109 WHERE id=?', (diligence_id,)).fetchone()
    now = _maintenant()
    if not row:
        desk.db.execute('INSERT INTO prefact_diligences56109(id,matter,source_kind,source_id,source_version,auteur,jour,debut,fin,minutes,origine_duree,bareme,libelle,'
                        'statut,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (diligence_id, matter, kind, sid, str(version or '')[:80], auteur, jour, debut, fin, minutes, origine, bareme, libelle[:200],
                         'a_completer' if minutes is None else ('estimation_a_confirmer' if origine == 'estimee' else 'a_valider'), now, now))
    elif row['statut'] not in FINAUX:
        maj = {}
        if row['minutes'] is None and minutes is not None:
            maj.update(minutes=minutes, origine_duree=origine, bareme=bareme)
        elif row['origine_duree'] == 'estimee' and origine in ('mesuree', 'saisie') and minutes is not None:
            maj.update(minutes=minutes, origine_duree=origine, bareme='')
        if not row['debut'] and debut:
            maj.update(debut=debut, fin=fin)
        if not row['auteur'] and auteur:
            maj['auteur'] = auteur
        if kind == 'rdv' and trace.startswith('rdv|') and libelle and row['libelle'] != libelle[:200]:
            maj['libelle'] = libelle[:200]
        if maj:
            sets = ','.join('%s=?' % k for k in maj)
            desk.db.execute('UPDATE prefact_diligences56109 SET %s,version=version+1,modifie_le=? WHERE id=?' % sets, (*maj.values(), now, diligence_id))
    _ajouter_trace(desk, trace, diligence_id)
    _rafraichir(desk, diligence_id)
    return diligence_id


def _rafraichir(desk, diligence_id):
    row = desk.db.execute('SELECT * FROM prefact_diligences56109 WHERE id=?', (diligence_id,)).fetchone()
    if not row or row['statut'] in FINAUX:
        return
    traces = [r[0] for r in desk.db.execute('SELECT trace FROM prefact_traces56109 WHERE diligence=?', (diligence_id,))]
    if _traces_validees_time500(desk, traces):
        desk.db.execute("UPDATE prefact_diligences56109 SET statut='deja_saisie',version=version+1,modifie_le=? WHERE id=?", (_maintenant(), diligence_id))
        return
    nouveau = _statut(row)
    if nouveau != row['statut']:
        desk.db.execute('UPDATE prefact_diligences56109 SET statut=?,modifie_le=? WHERE id=?', (nouveau, _maintenant(), diligence_id))


def _chevauchements(desk, matters):
    """Intervalles déclarés qui se chevauchent pour une même personne : un choix est exigé (jamais une suppression silencieuse).
    Balayage trié par début, par personne (lot borné par la période et le périmètre de la requête)."""
    if not matters:
        return
    marques = ','.join('?' * len(matters))
    rows = [dict(r) for r in desk.db.execute("SELECT * FROM prefact_diligences56109 WHERE matter IN (%s) AND auteur<>'' AND debut<>'' AND fin<>'' "
                                             "AND statut NOT IN ('ecarte','fusionnee','deja_saisie') ORDER BY auteur, debut" % marques, matters)]
    par = {}
    for r in rows:
        par.setdefault(r['auteur'], []).append(r)
    trouves = {r['id']: set() for r in rows}
    for lignes in par.values():
        for i, a in enumerate(lignes):
            for b in lignes[i + 1:]:
                if b['debut'] >= a['fin']:
                    break
                trouves[a['id']].add(b['id'])
                trouves[b['id']].add(a['id'])
    for r in rows:
        nouveau = json.dumps(sorted(trouves[r['id']]))
        if nouveau != r['chevauchements'] and r['statut'] != 'valide':
            desk.db.execute('UPDATE prefact_diligences56109 SET chevauchements=?,version=version+1,modifie_le=? WHERE id=?', (nouveau, _maintenant(), r['id']))
            _rafraichir(desk, r['id'])


def _instant(value):
    from .time500 import _parse_dt
    d = _parse_dt(value)
    if not d or 'T' not in str(value):
        return ''
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec='seconds')


def _collecter_appels(desk, mid, du, au):
    """5.6.109 (I2) : appels du dossier consignés par le lot G2 (``appels56109``). La durée vient de ``appels56109.duree_prefacturation`` :
    saisie ou mesurée, sinon « À compléter » (jamais zéro, jamais déduite du texte). Le jour est celui du fuseau de l'appel."""
    from zoneinfo import ZoneInfo
    from . import appels56109
    try:
        rows = desk.db.execute('SELECT id, instant_utc, zone, interlocuteur, auteur, version FROM appels56109 WHERE matter_id=? ORDER BY instant_utc LIMIT 2000',
                               (mid,)).fetchall()
    except sqlite3.OperationalError:   # table absente (base antérieure non migrée) : aucun appel à relever
        return 0
    vus = 0
    for r in rows:
        try:
            instant = datetime.fromisoformat(str(r['instant_utc']).replace('Z', '+00:00'))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            local = instant.astimezone(ZoneInfo(r['zone'] or 'Europe/Paris'))
        except (ValueError, KeyError):
            continue
        jour = local.date().isoformat()
        if not du <= jour <= au:
            continue
        duree = appels56109.duree_prefacturation(desk, r['id'])
        minutes = duree['minutes'] or None                             # 0 ou vide : à compléter, jamais une durée nulle
        origine = duree['origine'] if minutes else 'inconnue'
        debut = fin = ''
        if minutes:
            debut = instant.astimezone(timezone.utc).isoformat(timespec='seconds')
            fin = (instant + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat(timespec='seconds')
        libelle = 'Appel : ' + _texte(r['interlocuteur'] or 'interlocuteur non renseigné', 80)
        _enregistrer(desk, mid, 'appel|' + r['id'], jour, libelle, minutes, origine, '', debut, fin, r['auteur'] if minutes else '', str(r['version']))
        vus += 1
    return vus


def collecter(desk, acteur, data):
    """« Rechercher le travail non facturé » : relève courriels, pièces, livrables, rendez-vous passés de l'agenda (barème affiché, étiqueté
    « estimé »), comptes rendus de rendez-vous (même diligence que le rendez-vous) et appels consignés (durée ou « à compléter »).
    N'enregistre AUCUN temps."""
    from . import time500
    _droit(desk, acteur, 'facturation')
    today = jour_cabinet(desk)
    bar = time500.bareme(desk)
    au = _jour(data.get('au'), False) or today.isoformat()
    du = _jour(data.get('du'), False) or (date.fromisoformat(au) - timedelta(days=bar['fenetre_jours'])).isoformat()
    if du > au or (date.fromisoformat(au) - date.fromisoformat(du)).days > 370:
        raise Refus('champs_invalides', {'du': 'Période invalide (au plus un an).'})
    matters = [_dossier(desk, acteur, data['dossier'])] if data.get('dossier') else _perimetre(desk, acteur)
    version_bareme = _bareme_version(desk)
    vus = 0
    with _transaction(desk):
        for mid in matters:
            items, _ = time500.collect(desk, mid, du, au)
            for it in items:
                debut = fin = ''
                if it['kind'] == 'rendez_vous':
                    r = desk.db.execute('SELECT starts,ends FROM calendar_cache WHERE id=?', (it['ref'].split('|', 1)[1],)).fetchone()
                    if r:
                        debut, fin = _instant(r['starts']), _instant(r['ends'])
                _enregistrer(desk, mid, it['ref'], it['day'], it['label'], it['minutes'], 'estimee', version_bareme, debut, fin)
                vus += 1
            try:
                rapports = desk.db.execute('SELECT r.event_id, r.updated, c.starts, c.title FROM meeting500_reports r LEFT JOIN calendar_cache c ON c.id=r.event_id '
                                           'WHERE r.matter=?', (mid,)).fetchall()
            except sqlite3.Error:
                rapports = []
            for r in rapports:
                jour = str(r['starts'] or r['updated'] or '')[:10]
                if not jour or not du <= jour <= au:
                    continue
                _enregistrer(desk, mid, 'cr|' + r['event_id'], jour, 'Compte rendu : ' + _texte(r['title'] or 'rendez-vous', 80), version=r['updated'])
                vus += 1
            vus += _collecter_appels(desk, mid, du, au)
        _chevauchements(desk, matters)
    desk.audit('prefact56109_collecte', {'dossiers': len(matters), 'traces': vus, 'du': du, 'au': au})
    return {'du': du, 'au': au, 'traces': vus, 'message': '%d trace(s) relue(s) du %s au %s. Aucun temps n’est enregistré sans votre validation.' % (vus, du, au)}


def signaler_trace(desk, acteur, data):
    """Trace d'un travail (appel, compte rendu, rendez-vous tenu, travail achevé) — exposé pour les appels du lot G2 et la saisie
    manuelle. Durée facultative : sans durée, la diligence reste « à compléter » (jamais zéro, jamais déduite de la longueur d'un texte)."""
    _droit(desk, acteur, 'facturation')
    mid = _dossier(desk, acteur, data.get('dossier'))
    err = {}
    kind = str(data.get('nature') or '')
    if kind not in TRACES_MANUELLES:
        err['nature'] = 'Nature de trace inconnue.'
    sid = str(data.get('source') or '').strip()
    if not IDENT.fullmatch(sid):
        err['source'] = 'Identifiant de la source requis.'
    try:
        jour = _jour(data.get('jour'))
    except Stop:
        err['jour'] = 'Date invalide.'
        jour = ''
    minutes, origine = None, 'inconnue'
    if str(data.get('minutes') or '').strip():
        try:
            minutes = _entier(data.get('minutes'), 'minutes')
            origine = 'mesuree' if data.get('origine') == 'mesuree' else 'saisie'
        except ValueError:
            err['minutes'] = 'Durée en minutes entières, de 1 à 1440 (ou vide si elle est inconnue).'
    debut, fin = _instant(data.get('debut')), _instant(data.get('fin'))
    if (debut and not fin) or (fin and not debut) or (debut and fin <= debut):
        err['debut'] = 'Début et fin incomplets ou incohérents.'
    libelle = _texte(data.get('libelle'), 200)
    if not libelle:
        err['libelle'] = 'Libellé requis.'
    cible = str(data.get('rattacher_a') or '')
    if cible and not desk.db.execute('SELECT 1 FROM prefact_diligences56109 WHERE id=? AND matter=?', (cible, mid)).fetchone():
        err['rattacher_a'] = 'Diligence de rattachement introuvable dans ce dossier.'
    if err:
        raise Refus('champs_invalides', err)
    auteur = _texte(data.get('auteur') or _acteur(acteur).compte, 120)
    with _transaction(desk):
        did = _enregistrer(desk, mid, '%s|%s' % (kind, sid), jour, libelle, minutes, origine, '', debut, fin, auteur, str(data.get('version') or ''),
                           diligence_id=cible or None)
        _chevauchements(desk, [mid])
        _g().journal(desk, mid, 'prefacturation', did, 'trace', acteur, '', {}, {'trace': '%s|%s' % (kind, sid), 'minutes': minutes})
    return {'diligence': did, 'message': 'Trace enregistrée.' + ('' if minutes else ' Durée à compléter avant validation.')}


def _ligne(desk, did, mid=None):
    row = desk.db.execute('SELECT * FROM prefact_diligences56109 WHERE id=?', (str(did or ''),)).fetchone()
    if not row or (mid and row['matter'] != mid):
        raise Stop('introuvable')
    return row


def _version(row, attendue):
    try:
        if int(attendue) != int(row['version']):
            raise Stop('version_perimee')
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None


def completer(desk, acteur, data):
    """Compléter une ligne : durée (saisie par vous), libellé. Version attendue : 409 si la ligne a changé."""
    _droit(desk, acteur, 'facturation')
    row = _ligne(desk, data.get('id'))
    _dossier(desk, acteur, row['matter'])
    if row['statut'] in FINAUX:
        raise Stop('diligence_deja_validee')
    _version(row, data.get('version'))
    maj = {}
    if str(data.get('minutes') or '').strip():
        try:
            maj.update(minutes=_entier(data.get('minutes'), 'minutes'), origine_duree='saisie', bareme='')
        except ValueError:
            raise Refus('champs_invalides', {'minutes': 'Durée en minutes entières, de 1 à 1440.'}) from None
    if data.get('libelle') is not None and _texte(data.get('libelle'), 200):
        maj['libelle'] = _texte(data.get('libelle'), 200)
    if not maj:
        raise Refus('champs_invalides', {'minutes': 'Indiquez une durée ou un libellé.'})
    with _transaction(desk):
        sets = ','.join('%s=?' % k for k in maj)
        desk.db.execute('UPDATE prefact_diligences56109 SET %s,version=version+1,modifie_le=? WHERE id=? AND version=?' % sets, (*maj.values(), _maintenant(), row['id'], row['version']))
        _rafraichir(desk, row['id'])
        _g().journal(desk, row['matter'], 'prefacturation', row['id'], 'completer', acteur, '', {'minutes': row['minutes']}, {'minutes': maj.get('minutes')})
    return {'diligence': row['id'], 'message': 'Ligne complétée.'}


def rapprocher(desk, acteur, data):
    """Chevauchement ou doublon : « fusionner » rattache les traces de cette ligne à une autre (une seule diligence, aucune addition) ;
    « distinct » consigne que les deux travaux sont différents. Toujours une décision humaine, tracée."""
    _droit(desk, acteur, 'facturation')
    row = _ligne(desk, data.get('id'))
    mid = _dossier(desk, acteur, row['matter'])
    autre = _ligne(desk, data.get('avec'))
    _dossier(desk, acteur, autre['matter'])
    if row['statut'] in FINAUX or row['id'] == autre['id']:
        raise Stop('diligence_deja_validee')
    _version(row, data.get('version'))
    decision = str(data.get('decision') or '')
    if decision not in ('fusionner', 'distinct'):
        raise Refus('champs_invalides', {'decision': 'Choisissez « même travail » ou « travaux distincts ».'})
    with _transaction(desk):
        if decision == 'fusionner':
            if autre['statut'] in ('ecarte', 'fusionnee', 'deja_saisie'):
                raise Stop('diligence_deja_validee')
            desk.db.execute('UPDATE prefact_traces56109 SET diligence=? WHERE diligence=?', (autre['id'], row['id']))
            desk.db.execute("UPDATE prefact_diligences56109 SET statut='fusionnee',fusion_vers=?,version=version+1,modifie_le=? WHERE id=?",
                            (autre['id'], _maintenant(), row['id']))
            for x in (autre['id'],):
                d = json.loads(desk.db.execute('SELECT distincts FROM prefact_diligences56109 WHERE id=?', (x,)).fetchone()[0])
                desk.db.execute('UPDATE prefact_diligences56109 SET distincts=?,version=version+1,modifie_le=? WHERE id=?',
                                (json.dumps(sorted(set(d) | {row['id']})), _maintenant(), x))
        else:
            for a, b in ((row['id'], autre['id']), (autre['id'], row['id'])):
                d = json.loads(desk.db.execute('SELECT distincts FROM prefact_diligences56109 WHERE id=?', (a,)).fetchone()[0])
                desk.db.execute('UPDATE prefact_diligences56109 SET distincts=?,version=version+1,modifie_le=? WHERE id=?', (json.dumps(sorted(set(d) | {b})), _maintenant(), a))
        _rafraichir(desk, row['id'])
        _rafraichir(desk, autre['id'])
        _g().journal(desk, mid, 'prefacturation', row['id'], 'rapprochement_' + decision, acteur, '', {}, {'avec': autre['id']})
    return {'diligence': row['id'], 'message': 'Rattachée à l’autre diligence (une seule sera validée).' if decision == 'fusionner' else 'Travaux distincts consignés.'}


def ecarter(desk, acteur, data):
    _droit(desk, acteur, 'facturation')
    row = _ligne(desk, data.get('id'))
    _dossier(desk, acteur, row['matter'])
    if row['statut'] in FINAUX:
        raise Stop('diligence_deja_validee')
    _version(row, data.get('version'))
    with _transaction(desk):
        desk.db.execute("UPDATE prefact_diligences56109 SET statut='ecarte',version=version+1,modifie_le=? WHERE id=?", (_maintenant(), row['id']))
        _g().journal(desk, row['matter'], 'prefacturation', row['id'], 'ecarter', acteur, _texte(data.get('motif'), 300))
    return {'diligence': row['id'], 'message': 'Ligne écartée (aucun temps enregistré).'}


def _conditions(desk, mid):
    """Conditions applicables (``time500.terms``) et leur version, figées à la validation."""
    from . import time500
    t = time500.terms(desk, mid)
    forfait = t['mode'] == 'forfait'
    return {'mode': t['mode'] or '', 'taux_cents': 0 if forfait else int(t['rate_cents'] or 0), 'version': t.get('updated') or '', 'forfait': forfait}


def _lignes_lot(data):
    """Lot lu depuis le formulaire (``sel.<id>``, ``min.<id>``, ``ver.<id>``, ``conf.<id>``, ``taux.<id>``) ou depuis une liste JSON."""
    if isinstance(data.get('lignes'), list):
        return [x for x in data['lignes'] if isinstance(x, dict)]
    sel = data.get('sel') if isinstance(data.get('sel'), dict) else {}
    out = []
    for did, coche in sel.items():
        if coche in (True, 'true', 'on', '1'):
            def champ(k, did=did):
                return data[k].get(did) if isinstance(data.get(k), dict) else None
            out.append({'id': did, 'version': champ('ver'), 'minutes': champ('min'), 'confirmer': champ('conf') in (True, 'true', 'on', '1'), 'taux_vu': champ('taux')})
    return out


def valider_lot(desk, acteur, data):
    """Valide les lignes sélectionnées en UNE transaction : tout ou rien. Pour chaque ligne : version attendue, durée confirmée (une
    estimation exige « confirmer » ou une durée saisie ; une durée inconnue bloque le lot), aucun chevauchement ouvert, source ni déjà
    saisie (ancien chemin) ni déjà validée (autre poste) ni réservée par une facture, taux affiché égal au taux courant. Crée les temps
    validés (identifiant déterministe par diligence), fige durée, libellé, auteur, date, dossier, convention, taux et version, puis le reçu."""
    from . import time500
    a = _acteur(acteur)
    _droit(desk, acteur, 'facturation')
    lignes = _lignes_lot(data)
    if not lignes:
        raise Stop('rien_a_valider')
    if len(lignes) > MAX_LOT:
        raise Stop('lot_trop_grand')
    cle, compte = str(data.get('cle') or ''), a.compte
    # Empreinte de la DEMANDE (pas de l'état) : le rejeu de la même décision rend le même reçu, même après la validation.
    empreinte = _sha({'lignes': sorted([[str(x.get('id') or ''), str(x.get('version') or ''), str(x.get('minutes') or ''), bool(x.get('confirmer')),
                                          str(x.get('libelle') or '')] for x in lignes]), 'route': 'prefacturation/valider-lot'})
    ancien = _recu(desk, compte, 'prefacturation/valider-lot', cle, empreinte)
    if ancien is not None:
        return {**ancien, 'rejoue': True}
    err, prets, vus = {}, [], set()
    for x in lignes:
        did = str(x.get('id') or '')
        if did in vus:
            continue
        vus.add(did)
        row = _ligne(desk, did)
        _dossier(desk, acteur, row['matter'])
        if row['statut'] == 'valide':
            raise Refus('diligence_deja_validee', {did: MESSAGES['diligence_deja_validee']})
        if row['statut'] in FINAUX:
            raise Refus('diligence_deja_saisie' if row['statut'] == 'deja_saisie' else 'version_perimee', {did: STATUTS_LIGNE[row['statut']]})
        _version(row, x.get('version'))
        minutes, origine = row['minutes'], row['origine_duree']
        if str(x.get('minutes') or '').strip() and (row['minutes'] is None or str(x.get('minutes')).strip() != str(row['minutes'])):
            try:
                minutes, origine = _entier(x.get('minutes'), 'minutes'), 'saisie'
            except ValueError:
                err[did] = 'Durée en minutes entières, de 1 à 1440.'
                continue
        if minutes is None:
            err[did] = 'Durée inconnue : complétez-la avant de valider (aucune durée n’est inventée).'
            continue
        if origine == 'estimee' and not x.get('confirmer'):
            err[did] = 'Durée estimée : cochez « confirmer » ou saisissez la durée réelle.'
            continue
        if _ouverts(row):
            err[did] = 'Chevauchement avec une autre diligence : choisissez « même travail » ou « travaux distincts ».'
            continue
        c = _conditions(desk, row['matter'])
        if x.get('taux_vu') not in (None, '') and str(x.get('taux_vu')) != str(c['taux_cents']):
            raise Refus('tarif_modifie', {did: MESSAGES['tarif_modifie']})
        prets.append((row, minutes, origine, c, _texte(x.get('libelle'), 200) or row['libelle'] or 'Diligence'))
    if err:
        raise Refus('champs_invalides', err)
    if desk.db.in_transaction:
        desk.db.commit()
    with _transaction(desk):
        ancien = _recu(desk, compte, 'prefacturation/valider-lot', cle, empreinte)
        if ancien is not None:
            return {**ancien, 'rejoue': True}
        out = []
        for row, minutes, origine, c, libelle in prets:
            cur = desk.db.execute('SELECT * FROM prefact_diligences56109 WHERE id=?', (row['id'],)).fetchone()
            if int(cur['version']) != int(row['version']) or cur['statut'] in FINAUX:
                raise Refus('version_perimee', {row['id']: MESSAGES['version_perimee']})
            traces = [r[0] for r in desk.db.execute('SELECT trace FROM prefact_traces56109 WHERE diligence=?', (row['id'],))]
            if _traces_validees_time500(desk, traces):
                raise Refus('diligence_deja_saisie', {row['id']: MESSAGES['diligence_deja_saisie']})
            eid = _entree_id(row['id'])
            if desk.db.execute('SELECT 1 FROM time500_entries WHERE id=?', (eid,)).fetchone():
                raise Refus('diligence_deja_validee', {row['id']: MESSAGES['diligence_deja_validee']})
            if desk.db.execute("SELECT 1 FROM b53_allocations WHERE source_kind='temps' AND source_id=?", (eid,)).fetchone():
                raise Refus('diligence_deja_reservee', {row['id']: MESSAGES['diligence_deja_reservee']})
            montant = montant_temps(minutes, c['taux_cents'])
            time500._insert_entry(desk, 'INSERT', eid, row['matter'], row['jour'], minutes, c['taux_cents'], libelle, 'prefacturation', '',
                                  row['auteur'] or compte)
            convention = {'mode': c['mode'], 'version_conditions': c['version'], 'forfait': c['forfait'], 'origine_duree': origine, 'bareme': row['bareme']}
            desk.db.execute("UPDATE prefact_diligences56109 SET statut='valide',minutes=?,origine_duree=?,libelle=?,entree_id=?,taux_cents=?,montant_cents=?,convention=?,"
                            'valide_par=?,valide_le=?,version=version+1,modifie_le=? WHERE id=?',
                            (minutes, origine, libelle, eid, c['taux_cents'], montant, json.dumps(convention, ensure_ascii=False), compte, _maintenant(), _maintenant(), row['id']))
            time500.retirer_prefactures(desk, traces)
            _g().journal(desk, row['matter'], 'prefacturation', row['id'], 'validation', acteur, '', {}, {'entree': eid, 'minutes': minutes, 'taux_cents': c['taux_cents']})
            out.append({'diligence': row['id'], 'entree': eid, 'matter': row['matter'], 'minutes': minutes, 'taux_cents': c['taux_cents'], 'montant_cents': montant,
                        'forfait': c['forfait']})
        reponse = {'validees': out, 'nombre': len(out), 'message': '%d diligence(s) validée(s) en un seul lot. La facture se prépare ensuite, en brouillon.' % len(out)}
        _inscrire_recu(desk, compte, 'prefacturation/valider-lot', cle, empreinte, reponse)
    desk.audit('prefact56109_lot', {'lignes': len(out), 'par': compte})
    return reponse


def annuler_entree(desk, entree_id):
    """Appelé par ``time500.cancel_entry`` (même transaction, sans commit) : la diligence d'un temps annulé redevient à valider ; ses traces
    restent rattachées (aucune seconde diligence)."""
    row = desk.db.execute("SELECT * FROM prefact_diligences56109 WHERE entree_id=? AND statut='valide'", (str(entree_id),)).fetchone()
    if not row:
        return
    desk.db.execute("UPDATE prefact_diligences56109 SET statut='a_valider',entree_id='',taux_cents=NULL,montant_cents=NULL,valide_par='',valide_le='',"
                    "origine_duree=CASE WHEN origine_duree='estimee' THEN 'saisie' ELSE origine_duree END,version=version+1,modifie_le=? WHERE id=?",
                    (_maintenant(), row['id']))
    _rafraichir(desk, row['id'])


def prefacturation(desk, acteur, filtres=None):
    """Lecture (aucune écriture) : lignes de la période, dans le périmètre de l'acteur ; montants seulement avec « voir les honoraires »."""
    from . import time500
    f = filtres or {}
    voir = _peut(desk, acteur, 'voir_honoraires')
    if f.get('dossier'):
        matters = [_dossier(desk, acteur, f['dossier'])]
    else:
        matters = _perimetre(desk, acteur)
    out = {'lignes': [], 'voir_montants': voir, 'dossiers': {}}
    if not matters:
        return out
    sql = 'SELECT * FROM prefact_diligences56109 WHERE matter IN (%s)' % ','.join('?' * len(matters))
    params = list(matters)
    for k, col, op in (('du', 'jour', '>='), ('au', 'jour', '<=')):
        if f.get(k):
            sql += ' AND %s%s?' % (col, op)
            params.append(_jour(f[k]))
    if f.get('personne') is not None and str(f.get('personne')) != '':
        sql += ' AND auteur=?'
        params.append(str(f['personne']))
    if not f.get('tout'):
        sql += " AND statut NOT IN ('ecarte','fusionnee','deja_saisie')"
    try:
        rows = desk.db.execute(sql + ' ORDER BY jour DESC, matter, id LIMIT 500', params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conds = {}
    for r in rows:
        c = conds.setdefault(r['matter'], _conditions(desk, r['matter']))
        traces = [t[0] for t in desk.db.execute('SELECT trace FROM prefact_traces56109 WHERE diligence=? ORDER BY trace', (r['id'],))]
        if r['statut'] == 'valide':
            montant, nature_montant = r['montant_cents'], 'valide'
        elif c['forfait']:
            montant, nature_montant = None, 'forfait'
        elif not c['taux_cents']:
            montant, nature_montant = None, 'taux_a_definir'
        else:
            montant = montant_temps(r['minutes'], c['taux_cents'])
            nature_montant = 'estime' if r['origine_duree'] == 'estimee' else ('a_completer' if montant is None else 'propose')
        alertes = []
        if r['minutes'] is None and r['statut'] not in FINAUX:
            alertes.append('Durée inconnue : à compléter.')
        if r['origine_duree'] == 'estimee' and r['statut'] not in FINAUX:
            alertes.append('Durée estimée (%s), pas mesurée : à confirmer.' % (r['bareme'] or 'agenda'))
        if _ouverts(r) and r['statut'] not in FINAUX:
            alertes.append('Chevauche %d autre(s) diligence(s) de la même personne.' % len(_ouverts(r)))
        if len(traces) > 1:
            alertes.append('%d traces rapprochées : une seule diligence.' % len(traces))
        out['lignes'].append({'id': r['id'], 'matter': r['matter'], 'jour': r['jour'], 'libelle': r['libelle'], 'source': SOURCES.get(r['source_kind'], r['source_kind']),
                              'traces': traces, 'auteur': r['auteur'], 'minutes': r['minutes'], 'origine': r['origine_duree'],
                              'origine_libelle': ORIGINES[r['origine_duree']], 'statut': r['statut'], 'statut_libelle': STATUTS_LIGNE[r['statut']],
                              'version': r['version'], 'montant_cents': montant if voir else None, 'nature_montant': nature_montant,
                              'taux_cents': (r['taux_cents'] if r['statut'] == 'valide' else c['taux_cents']) if voir else None, 'chevauche': _ouverts(r),
                              'alertes': alertes, 'entree': r['entree_id'], 'debut': r['debut'], 'fin': r['fin']})
    for mid in {x['matter'] for x in out['lignes']} | set(matters if f.get('dossier') else []):
        c = conds.get(mid) or _conditions(desk, mid)
        info = {'mode': time500.MODES.get(c['mode'], 'à définir'), 'version_conditions': c['version'], 'taux_cents': c['taux_cents'] if voir else None}
        if voir:
            try:
                from . import catalogue56109
                t = catalogue56109.tarif_applicable(desk, acteur, mid)
                info['tarif'] = {'origine': t['origine'], 'libelle': t['libelle']}
            except Stop:
                info['tarif'] = None
            try:
                from . import facturation5653
                info['a_facturer_cents'] = sum(int(x['amount_cents']) for x in facturation5653.billable_time(desk, mid))
            except Exception:   # noqa: BLE001 — registre de facturation pas encore initialisé : non additionné
                info['a_facturer_cents'] = None
        out['dossiers'][mid] = info
    out['compteurs'] = {'a_valider': sum(1 for x in out['lignes'] if x['statut'] in ('a_valider', 'estimation_a_confirmer')),
                        'a_completer': sum(1 for x in out['lignes'] if x['statut'] == 'a_completer'),
                        'chevauchements': sum(1 for x in out['lignes'] if x['statut'] == 'chevauchement_a_verifier')}
    return out


def resume_aujourdhui(desk, acteur):
    """Résumé filtré par droits pour une carte d'Aujourd'hui (lecture seule, sans IA ni synchronisation) : diligences à valider, travail
    validé non facturé (jamais mélangé aux factures émises ni aux encaissements), forfaits à surveiller. Sans « voir les honoraires » :
    aucun montant ni compteur financier."""
    v = prefacturation(desk, acteur, {})
    out = {'diligences_a_traiter': v['compteurs']['a_valider'] + v['compteurs']['a_completer'] + v['compteurs']['chevauchements']}
    if v['voir_montants']:
        from . import time500
        out['valide_non_facture_cents'] = sum(x.get('a_facturer_cents') or 0 for x in v['dossiers'].values())
        visibles = set(_perimetre(desk, acteur))
        out['forfaits_a_surveiller'] = [r['matter'] for r in time500.alerts_now(desk) if r['matter'] in visibles]
    return out


# ================================================================================================ G02 assistance régulière
def _client(desk, ident):
    from . import crm5636
    try:
        return crm5636.get(desk, str(ident or ''))
    except Stop:
        raise Stop('introuvable') from None


def convention_qualifiee(desk, doc_id):
    """Qualification d'une convention ou lettre de mission du registre de facturation : état, version, périmètre, preuve. « Signée »
    exige l'état ``signe`` ET une preuve enregistrée par l'avocat ; ``envoye``/``en_signature`` = envoyée, ``approuve`` = approuvée
    (non envoyée). ``facturation5653._active_convention`` peut retenir ces trois états : son résultat n'est pas une preuve d'engagement."""
    from . import facturation5653
    return facturation5653.convention_qualifiee(desk, doc_id)


def _contenu_contrat(desk, acteur, data):
    err = {}
    out = {}
    try:
        out['debut'] = _jour(data.get('debut'))
    except Stop:
        err['debut'] = 'Date de début requise.'
    try:
        out['fin'] = _jour(data.get('fin'), False)
    except Stop:
        err['fin'] = 'Date de fin invalide.'
    if out.get('fin') and out.get('debut') and out['fin'] <= out['debut']:
        err['fin'] = 'La fin doit suivre le début.'
    out['periodicite'] = str(data.get('periodicite') or 'mois')
    if out['periodicite'] not in PERIODICITES:
        err['periodicite'] = 'Périodicité inconnue.'
    out['fuseau'] = str(data.get('fuseau') or 'Europe/Paris')[:60]
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(out['fuseau'])
    except Exception:   # noqa: BLE001
        err['fuseau'] = 'Fuseau horaire inconnu.'
    out['devise'] = str(data.get('devise') or 'EUR').upper()
    if not DEVISE.fullmatch(out['devise']):
        err['devise'] = 'Devise en trois lettres (EUR, CHF…).'
    out['montant_minor'] = None
    if str(data.get('montant') or '').strip():
        try:
            out['montant_minor'] = montant_minor(data.get('montant'), out['devise'])
        except ValueError:
            err['montant'] = 'Montant invalide.'
    services = data.get('services')
    if not isinstance(services, list):
        services = []
        for i in range(1, 6):
            lib = _texte(data.get('service_%d' % i), 120)
            if lib:
                services.append({'id': 's%d' % i, 'libelle': lib, 'unite': data.get('unite_%d' % i) or 'minutes', 'inclus': data.get('inclus_%d' % i)})
    propres = []
    for s in services[:10]:
        if not isinstance(s, dict) or not _texte(s.get('libelle'), 120):
            continue
        sid = re.sub(r'[^a-z0-9_]', '', str(s.get('id') or 's%d' % (len(propres) + 1)).lower())[:20] or 's%d' % (len(propres) + 1)
        if s.get('unite') not in UNITES:
            err['services'] = 'Unité inconnue (minutes ou prestations).'
            continue
        try:
            inclus = _entier(s.get('inclus'), 'inclus', 0, 1_000_000)
        except ValueError:
            err['services'] = 'Quantité incluse : nombre entier (minutes ou prestations), jamais converti d’une unité à l’autre.'
            continue
        propres.append({'id': sid, 'libelle': _texte(s.get('libelle'), 120), 'unite': s['unite'], 'inclus': inclus})
    if not propres:
        err.setdefault('services', 'Indiquez au moins une prestation incluse et son unité.')
    out['services'] = propres
    out['report'] = 'periode_suivante' if data.get('report') in ('periode_suivante', True, 'true', 'on', '1') else 'aucun'
    dossiers = data.get('dossiers')
    if not isinstance(dossiers, list):
        dossiers = [x for x in re.split(r'[\s,;]+', str(dossiers or '')) if x]
    visibles = []
    for mid in dossiers[:50]:
        try:
            visibles.append(_dossier(desk, acteur, mid))
        except Stop:
            err['dossiers'] = 'Dossier inconnu ou non accessible : %s.' % _texte(mid, 40)
    if not visibles:
        err.setdefault('dossiers', 'Indiquez les dossiers qui partagent ce quota (aucun autre dossier du client ne le consomme).')
    out['dossiers'] = sorted(set(visibles))
    out['interlocuteurs'] = _texte(data.get('interlocuteurs'), 300)
    out['delai_reponse_jours_ouvres'] = None
    if str(data.get('delai_reponse') or '').strip():
        try:
            out['delai_reponse_jours_ouvres'] = _entier(data.get('delai_reponse'), 'delai', 0, 60)
        except ValueError:
            err['delai_reponse'] = 'Délai de réponse en jours ouvrés (engagement contractuel, distinct de tout délai de procédure).'
    out['hors_forfait'] = _texte(data.get('hors_forfait'), 400)
    out['convention'] = str(data.get('convention') or '').strip()[:80]
    if out['convention']:
        try:
            q = convention_qualifiee(desk, out['convention'])
            if q['matter'] not in out['dossiers']:
                err['convention'] = 'La convention doit appartenir à un dossier convenu.'
        except Stop:
            err['convention'] = 'Convention introuvable dans le registre de facturation.'
    if err:
        raise Refus('champs_invalides', err)
    return out


def enregistrer_contrat(desk, acteur, data):
    """Crée le contrat (version 1) ou en publie une nouvelle version (version attendue). L'historique est conservé (versions immuables) ;
    une période déjà ouverte garde le quota figé de sa version ; la nouvelle version s'applique aux périodes ouvertes ensuite."""
    a = _acteur(acteur)
    _droit(desk, acteur, 'facturation')
    client = _client(desk, data.get('client'))
    contenu = _contenu_contrat(desk, acteur, data)
    conv = convention_qualifiee(desk, contenu['convention']) if contenu['convention'] else {'etat': 'aucune'}
    now = _maintenant()
    with _transaction(desk):
        row = desk.db.execute('SELECT * FROM assistance_contrats56109 WHERE client=?', (client['id'],)).fetchone()
        if not row:
            if str(data.get('version') or '0') not in ('', '0'):
                raise Stop('version_perimee')
            cid, version = 'ast-' + uuid.uuid4().hex[:24], 1
            desk.db.execute("INSERT INTO assistance_contrats56109(id,client,version,etat,contenu,cree_par,cree_le,modifie_le) VALUES(?,?,1,'actif',?,?,?,?)",
                            (cid, client['id'], json.dumps(contenu, ensure_ascii=False), a.compte, now, now))
        else:
            _version(row, data.get('version'))
            cid, version = row['id'], row['version'] + 1
            desk.db.execute('UPDATE assistance_contrats56109 SET version=?,contenu=?,modifie_le=? WHERE id=? AND version=?',
                            (version, json.dumps(contenu, ensure_ascii=False), now, cid, row['version']))
        desk.db.execute('INSERT INTO assistance_versions56109(contrat_id,version,contenu,convention,auteur,applicable_du,at) VALUES(?,?,?,?,?,?,?)',
                        (cid, version, json.dumps(contenu, ensure_ascii=False), json.dumps(conv, ensure_ascii=False), a.compte,
                         _jour(data.get('applicable_du'), False) or jour_cabinet(desk).isoformat(), now))
        _g().journal(desk, contenu['dossiers'][0], 'assistance', cid, 'version', acteur, '', {}, {'version': version, 'convention': conv.get('etat')})
    return {'contrat': cid, 'version': version, 'convention': conv,
            'message': 'Contrat enregistré (version %d).%s' % (version, '' if conv.get('etat') == 'signee' else ' Convention non signée : les conditions ne font pas encore foi.')}


def _bornes(periodicite, jour):
    """Période civile [début inclus, fin exclue] qui contient ``jour`` : mois, trimestre ou année du calendrier (jamais 30 jours fixes)."""
    if periodicite == 'mois':
        debut = jour.replace(day=1)
        fin = (debut.replace(year=debut.year + 1, month=1) if debut.month == 12 else debut.replace(month=debut.month + 1))
    elif periodicite == 'trimestre':
        m = 3 * ((jour.month - 1) // 3) + 1
        debut = jour.replace(month=m, day=1)
        fin = debut.replace(year=debut.year + 1, month=1) if m == 10 else debut.replace(month=m + 3)
    else:
        debut, fin = jour.replace(month=1, day=1), jour.replace(year=jour.year + 1, month=1, day=1)
    return debut, fin


def bornes_periode(contenu, jour):
    """Règle explicite des périodes : périodes CIVILES (un contrat qui commence le 29, le 30 ou le 31 n'est pas décalé) ; la première
    et la dernière période sont bornées par le début et la fin du contrat et marquées « partielles » : leur quota n'est pas proratisé
    automatiquement (l'avocat décide). Les jours non ouvrés n'ont pas d'effet sur le quota."""
    debut, fin = _bornes(contenu['periodicite'], jour)
    d0 = date.fromisoformat(contenu['debut'])
    partielle = False
    if debut < d0:
        debut, partielle = d0, True
    if contenu.get('fin'):
        f0 = date.fromisoformat(contenu['fin'])
        if fin > f0:
            fin, partielle = f0, True
    return debut, fin, partielle


def _contrat(desk, acteur, ident=None, client=None):
    if client:
        row = desk.db.execute('SELECT * FROM assistance_contrats56109 WHERE client=?', (str(client),)).fetchone()
    else:
        row = desk.db.execute('SELECT * FROM assistance_contrats56109 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    contenu = json.loads(row['contenu'])
    return row, contenu


def ouvrir_periode(desk, acteur, data):
    """Ouvre la période qui contient la date donnée ; quota figé depuis la version courante du contrat ; report de la période
    précédente seulement si le contrat le prévoit ET si le solde de celle-ci était exact (sinon : report indéterminé, à compléter)."""
    _droit(desk, acteur, 'facturation')
    row, contenu = _contrat(desk, acteur, data.get('contrat'))
    for mid in contenu['dossiers']:
        _dossier(desk, acteur, mid)
    jour = date.fromisoformat(_jour(data.get('jour'), False) or jour_cabinet(desk).isoformat())
    if jour.isoformat() < contenu['debut'] or (contenu.get('fin') and jour.isoformat() >= contenu['fin']):
        raise Refus('champs_invalides', {'jour': 'Date hors de la durée du contrat.'})
    debut, fin, partielle = bornes_periode(contenu, jour)
    with _transaction(desk):
        if desk.db.execute('SELECT 1 FROM assistance_periodes56109 WHERE contrat_id=? AND debut=?', (row['id'], debut.isoformat())).fetchone():
            raise Stop('periode_existante')
        if desk.db.execute('SELECT 1 FROM assistance_periodes56109 WHERE contrat_id=? AND debut<? AND fin>?', (row['id'], fin.isoformat(), debut.isoformat())).fetchone():
            raise Stop('periode_existante')
        quotas = {s['id']: {'libelle': s['libelle'], 'unite': s['unite'], 'inclus': s['inclus']} for s in contenu['services']}
        report = {}
        prec = desk.db.execute('SELECT * FROM assistance_periodes56109 WHERE contrat_id=? AND fin<=? ORDER BY fin DESC LIMIT 1', (row['id'], debut.isoformat())).fetchone()
        if contenu['report'] == 'periode_suivante' and prec:
            bilan = json.loads(prec['bilan'] or '{}')
            for sid in quotas:
                b = (bilan.get('services') or {}).get(sid)
                if prec['etat'] != 'close' or b is None:
                    report[sid] = None            # période précédente non close : report indéterminé
                else:
                    report[sid] = b.get('reste_exact')   # None si la période précédente avait une durée inconnue
        pid = 'per-' + uuid.uuid4().hex[:24]
        desk.db.execute("INSERT INTO assistance_periodes56109(id,contrat_id,contrat_version,debut,fin,quotas,report,partielle,etat,cree_le) VALUES(?,?,?,?,?,?,?,?,'ouverte',?)",
                        (pid, row['id'], row['version'], debut.isoformat(), fin.isoformat(), json.dumps(quotas, ensure_ascii=False), json.dumps(report), int(partielle), _maintenant()))
        _g().journal(desk, contenu['dossiers'][0], 'assistance', row['id'], 'periode', acteur, '', {}, {'periode': pid, 'debut': debut.isoformat(), 'fin': fin.isoformat()})
    return {'periode': pid, 'debut': debut.isoformat(), 'fin': fin.isoformat(), 'partielle': partielle,
            'message': 'Période du %s au %s (fin exclue) ouverte%s.' % (debut.isoformat(), fin.isoformat(), ', partielle : quota non proratisé' if partielle else '')}


def _periode(desk, pid):
    p = desk.db.execute('SELECT * FROM assistance_periodes56109 WHERE id=?', (str(pid or ''),)).fetchone()
    if not p:
        raise Stop('introuvable')
    return p


def _contenu_periode(desk, p):
    """Contrat tel qu'il s'appliquait à l'ouverture de la période (version figée) : dossiers convenus et prestations de CETTE période."""
    r = desk.db.execute('SELECT contenu FROM assistance_versions56109 WHERE contrat_id=? AND version=?', (p['contrat_id'], p['contrat_version'])).fetchone()
    row = desk.db.execute('SELECT * FROM assistance_contrats56109 WHERE id=?', (p['contrat_id'],)).fetchone()
    if not row:
        raise Stop('introuvable')
    return row, json.loads(r['contenu'] if r else row['contenu'])


def affecter(desk, acteur, data):
    """Affecte des temps validés des dossiers convenus à une prestation de la période. Minutes : quantité = durée du temps validé
    (jamais convertie en prestations). Prestations : quantité entière explicite (par défaut 1). Un temps n'est consommé qu'une fois
    pour le contrat (contrainte unique) ; lot atomique ; un temps hors des dossiers convenus ou de la période est refusé."""
    a = _acteur(acteur)
    _droit(desk, acteur, 'facturation')
    p = _periode(desk, data.get('periode'))
    if p['etat'] == 'close':
        raise Stop('periode_close')
    row, contenu = _contenu_periode(desk, p)
    for mid in contenu['dossiers']:
        _dossier(desk, acteur, mid)
    quotas = json.loads(p['quotas'])
    service = str(data.get('service') or next(iter(quotas), ''))
    if service not in quotas:
        raise Refus('champs_invalides', {'service': 'Prestation inconnue de cette période.'})
    unite = quotas[service]['unite']
    temps = data.get('temps')
    if temps in (None, '', 'tous'):
        marques = ','.join('?' * len(contenu['dossiers']))
        deja = {r[0] for r in desk.db.execute('SELECT diligence FROM assistance_consommations56109 WHERE contrat_id=?', (row['id'],))}
        temps = [r['id'] for r in desk.db.execute('SELECT id FROM time500_entries WHERE matter IN (%s) AND day>=? AND day<?' % marques,
                                                  (*contenu['dossiers'], p['debut'], p['fin'])) if r['id'] not in deja]
    elif not isinstance(temps, list):
        temps = [str(temps)]
    err = {}
    lignes = []
    for tid in temps[:MAX_LOT]:
        t = desk.db.execute('SELECT * FROM time500_entries WHERE id=?', (str(tid),)).fetchone()
        if not t:
            err[str(tid)[:40]] = 'Temps introuvable.'
            continue
        if t['matter'] not in contenu['dossiers']:
            err[str(tid)[:40]] = 'Ce dossier ne partage pas le quota du contrat.'
            continue
        if not p['debut'] <= t['day'] < p['fin']:
            err[str(tid)[:40]] = 'Temps hors de la période.'
            continue
        if unite == 'minutes':
            q = int(t['minutes'])
        else:
            try:
                q = _entier(data.get('quantite') if str(data.get('quantite') or '').strip() else 1, 'quantite', 0, 1000)
            except ValueError:
                err['quantite'] = 'Nombre de prestations entier.'
                continue
        lignes.append((t, q))
    if err:
        raise Refus('champs_invalides', err)
    ajoutes = 0
    with _transaction(desk):
        for t, q in lignes:
            c = desk.db.execute('INSERT OR IGNORE INTO assistance_consommations56109(id,contrat_id,periode_id,diligence,matter,service,unite,quantite,auteur,at) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?)', ('cso-' + uuid.uuid4().hex[:24], row['id'], p['id'], t['id'], t['matter'], service, unite, q, a.compte, _maintenant()))
            ajoutes += c.rowcount
        _g().journal(desk, contenu['dossiers'][0], 'assistance', row['id'], 'affectation', acteur, '', {}, {'periode': p['id'], 'ajoutes': ajoutes})
    return {'ajoutes': ajoutes, 'deja_affectes': len(lignes) - ajoutes, 'message': '%d temps affecté(s) ; %d déjà décompté(s).' % (ajoutes, len(lignes) - ajoutes)}


def consommation_abonnement(inclus, consommations_validees, report=0):
    """Noyau repris de ``reference/gestion_cabinet109.consommation_abonnement`` : unités homogènes d'une période (minutes OU prestations).
    Une quantité inconnue (``None``) rend le reste exact indéterminé, jamais nul ; le dépassement connu n'est jamais un supplément accepté."""
    if type(inclus) is not int or inclus < 0 or type(report) is not int or report < 0:
        raise ValueError('quota_invalide')
    connus, inconnus = 0, 0
    for n in consommations_validees:
        if n is None:
            inconnus += 1
        elif type(n) is not int or n < 0:
            raise ValueError('consommation_invalide')
        else:
            connus += n
    quota = inclus + report
    return {'quota': quota, 'consommation_connue': connus, 'inconnues': inconnus, 'reste_exact': None if inconnus else max(0, quota - connus),
            'depassement_connu': max(0, connus - quota), 'a_completer': bool(inconnus)}


def bilan_periode(desk, acteur, pid):
    """Consommation d'une période par prestation et par unité. Les estimations ne consomment rien ; une diligence d'un dossier convenu
    dont la durée est inconnue rend le reste EXACT indéterminé (``None``), jamais nul. Lecture seule."""
    from . import time500   # noqa: F401 — schéma des temps
    p = _periode(desk, pid)
    row, contenu = _contenu_periode(desk, p)
    caches = []
    for mid in contenu['dossiers']:
        try:
            _dossier(desk, acteur, mid)
        except Stop:
            caches.append(mid)
    if len(caches) == len(contenu['dossiers']):
        raise Stop('introuvable')
    visibles = [m for m in contenu['dossiers'] if m not in caches]
    quotas, report = json.loads(p['quotas']), json.loads(p['report'] or '{}')
    marques = ','.join('?' * len(visibles))
    inconnues = [dict(r) for r in desk.db.execute("SELECT id,matter,jour,libelle FROM prefact_diligences56109 WHERE matter IN (%s) AND jour>=? AND jour<? "
                                                  "AND minutes IS NULL AND statut NOT IN ('valide','ecarte','fusionnee','deja_saisie')" % marques,
                                                  (*visibles, p['debut'], p['fin']))]
    estimees = desk.db.execute("SELECT COUNT(*) FROM prefact_diligences56109 WHERE matter IN (%s) AND jour>=? AND jour<? AND statut='estimation_a_confirmer'" % marques,
                               (*visibles, p['debut'], p['fin'])).fetchone()[0]
    deja = {r[0] for r in desk.db.execute('SELECT diligence FROM assistance_consommations56109 WHERE contrat_id=?', (row['id'],))}
    non_affectes = [dict(r) for r in desk.db.execute('SELECT id,matter,day,minutes,label FROM time500_entries WHERE matter IN (%s) AND day>=? AND day<?' % marques,
                                                     (*visibles, p['debut'], p['fin'])) if r['id'] not in deja]
    services = {}
    for sid, q in quotas.items():
        cons = [r['quantite'] for r in desk.db.execute('SELECT quantite FROM assistance_consommations56109 WHERE periode_id=? AND service=? AND matter IN (%s)' % marques,
                                                       (p['id'], sid, *visibles))]
        inconnus = list(cons)
        if q['unite'] == 'minutes':
            inconnus += [None] * len(inconnues)       # une durée inconnue d'un dossier convenu : solde exact indéterminé
        rep = report.get(sid, 0) if sid in report else 0
        b = consommation_abonnement(q['inclus'], inconnus, rep if rep is not None else 0)
        if sid in report and report[sid] is None:
            b['reste_exact'], b['a_completer'] = None, True
            b['report_indetermine'] = True
        services[sid] = {**q, **b, 'report': report.get(sid, 0)}
    partiel = bool(caches)
    return {'periode': p['id'], 'debut': p['debut'], 'fin': p['fin'], 'etat': p['etat'], 'partielle': bool(p['partielle']), 'contrat_version': p['contrat_version'],
            'services': services, 'inconnues': inconnues, 'estimations_non_comptees': estimees, 'temps_non_affectes': non_affectes, 'vue_partielle': partiel,
            'compte_rendu': p['compte_rendu'],
            'hors_forfait': [sid for sid, s in services.items() if s['depassement_connu'] > 0],
            'mention': 'Le dépassement n’est pas un honoraire accepté : il appelle une proposition de devis ou d’avenant.'}


def cloturer_periode(desk, acteur, data):
    _droit(desk, acteur, 'facturation')
    _g().valideur(acteur)
    p = _periode(desk, data.get('periode'))
    if p['etat'] == 'close':
        raise Stop('periode_close')
    b = bilan_periode(desk, acteur, p['id'])
    if b['vue_partielle']:
        raise Stop('role_insuffisant')               # clôturer exige le périmètre complet du contrat
    bilan = {'services': {sid: {'reste_exact': s['reste_exact'], 'consommation_connue': s['consommation_connue'], 'inconnues': s['inconnues']}
                          for sid, s in b['services'].items()}}
    with _transaction(desk):
        c = desk.db.execute("UPDATE assistance_periodes56109 SET etat='close',bilan=?,close_le=? WHERE id=? AND etat='ouverte'", (json.dumps(bilan), _maintenant(), p['id']))
        if not c.rowcount:
            raise Stop('periode_close')
        row, contenu = _contenu_periode(desk, p)
        _g().journal(desk, contenu['dossiers'][0], 'assistance', row['id'], 'cloture_periode', acteur, '', {}, {'periode': p['id']})
    return {'periode': p['id'], 'message': 'Période close ; son bilan est figé.'}


def compte_rendu(desk, acteur, data):
    """Compte rendu de période : UN livrable du registre commun (``artefacts56109.ecrire``, nature « budget », droit « voir les
    honoraires »), visible dans Produire et dans le dossier principal du contrat. Une nouvelle préparation crée une nouvelle version du
    même livrable (jamais un second document). L'avocat choisit les temps communicables (``exclure``) ; aucune note interne, aucune
    donnée d'un autre client ; rien n'est envoyé."""
    from . import artefacts56109, membres5650
    a = _acteur(acteur)
    _droit(desk, acteur, 'voir_honoraires')
    _droit(desk, acteur, 'facturation')
    p = _periode(desk, data.get('periode'))
    row, contenu = _contenu_periode(desk, p)
    b = bilan_periode(desk, acteur, p['id'])
    if b['vue_partielle']:
        raise Stop('role_insuffisant')
    client = _client(desk, row['client'])
    exclure = set(x for x in (data.get('exclure') or []) if isinstance(x, str)) if isinstance(data.get('exclure'), list) else set()
    conso = [dict(r) for r in desk.db.execute('SELECT c.diligence,c.service,c.quantite,c.unite,t.day,t.label,t.matter FROM assistance_consommations56109 c '
                                              'JOIN time500_entries t ON t.id=c.diligence WHERE c.periode_id=? ORDER BY t.day', (p['id'],))]
    lignes = [{'id': 'T%d' % (i + 1), 'cells': [x['day'], _texte(x['label'], 200), b['services'].get(x['service'], {}).get('libelle', x['service']),
                                              '%s %s' % (x['quantite'] if x['quantite'] is not None else 'inconnue', UNITES[x['unite']])],
               'statut': 'trouve', 'source_ids': []} for i, x in enumerate(c for c in conso if c['diligence'] not in exclure)]
    synthese = []
    for sid, s in b['services'].items():
        reste = 'à compléter (durée inconnue)' if s['reste_exact'] is None else '%d %s' % (s['reste_exact'], UNITES[s['unite']])
        synthese.append({'id': 'S-' + sid, 'cells': [s['libelle'], '%d %s' % (s['quota'], UNITES[s['unite']]), '%d %s' % (s['consommation_connue'], UNITES[s['unite']]), reste],
                         'statut': 'a_confirmer' if s['reste_exact'] is None else 'trouve', 'source_ids': []})
    blocs = [{'id': 'intro', 'type': 'paragraph', 'source_ids': [], 'text': 'Assistance régulière — %s — période du %s au %s (fin exclue)%s.' % (
                 client['label'], p['debut'], p['fin'], ', période partielle' if p['partielle'] else '')},
             {'id': 'conso', 'type': 'table', 'source_ids': [], 'columns': ['Prestation', 'Inclus', 'Consommé (validé)', 'Reste'], 'rows': synthese},
             {'id': 'titre-travaux', 'type': 'heading', 'source_ids': [], 'text': 'Travaux réalisés et validés'},
             {'id': 'travaux', 'type': 'table', 'source_ids': [], 'columns': ['Date', 'Diligence', 'Prestation', 'Quantité'], 'rows': lignes}]
    if b['inconnues']:
        blocs.append({'id': 'attente', 'type': 'paragraph', 'source_ids': [], 'text': '%d diligence(s) dont la durée reste à compléter : le solde exact n’est pas encore connu.' % len(b['inconnues'])})
    if b['hors_forfait']:
        blocs.append({'id': 'hors', 'type': 'paragraph', 'source_ids': [], 'text': 'Consommation au-delà du forfait : une proposition de devis ou d’avenant est à soumettre ; '
                                                                                  'rien n’est facturé en supplément sans accord.'})
    payload = {'kind': 'budget', 'title': 'Compte rendu d’assistance — %s — %s' % (client['label'][:80], p['debut'][:7]), 'blocks': blocs}
    hidden = membres5650.viewer_hidden() or frozenset()
    try:
        droits = membres5650.effective_rights(desk.c['state_dir'], a.compte, a.role) if a.role in membres5650.ROLES else None
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    doc = p['compte_rendu'] or None
    revision = 0
    if doc:
        r = desk.db.execute('SELECT COALESCE(MAX(revision),0) FROM livrables_contenus56109 WHERE document_id=?', (doc,)).fetchone()
        revision = int(r[0])
    cle = str(data.get('cle') or '')
    if a.role not in artefacts56109.ROLES_EDITION:
        raise Stop('role_insuffisant')
    recu = artefacts56109.ecrire(desk, a.compte, a.role, hidden, droits, payload, cle, revision, doc,
                                 contenu['dossiers'][0], '', 'assistance56109')
    if not p['compte_rendu']:
        desk.db.execute("UPDATE assistance_periodes56109 SET compte_rendu=? WHERE id=? AND compte_rendu=''", (recu['document_id'], p['id']))
        desk.db.commit()
    return {**recu, 'message': 'Compte rendu préparé dans Produire (version %d) : à relire avant toute transmission.' % recu['revision']}


def assistance(desk, acteur, client_id):
    """Vue de la section « Assistance régulière » d'une fiche client (lecture seule)."""
    _droit(desk, acteur, 'voir_honoraires')
    client = _client(desk, client_id)
    row = desk.db.execute('SELECT * FROM assistance_contrats56109 WHERE client=?', (client['id'],)).fetchone()
    if not row:
        return {'client': client['id'], 'contrat': None}
    contenu = json.loads(row['contenu'])
    visibles = []
    for mid in contenu['dossiers']:
        try:
            visibles.append(_dossier(desk, acteur, mid))
        except Stop:
            pass
    if not visibles:
        raise Stop('introuvable')
    conv = None
    if contenu.get('convention'):
        try:
            conv = convention_qualifiee(desk, contenu['convention'])
        except Stop:
            conv = {'etat': 'introuvable', 'libelle': 'Convention introuvable'}
    periodes = []
    for p in desk.db.execute('SELECT id FROM assistance_periodes56109 WHERE contrat_id=? ORDER BY debut DESC LIMIT 12', (row['id'],)):
        periodes.append(bilan_periode(desk, acteur, p['id']))
    versions = [dict(r) for r in desk.db.execute('SELECT version,auteur,applicable_du,at,convention FROM assistance_versions56109 WHERE contrat_id=? ORDER BY version DESC',
                                                 (row['id'],))]
    return {'client': client['id'], 'contrat': {'id': row['id'], 'version': row['version'], 'etat': row['etat'], **contenu}, 'convention': conv, 'periodes': periodes,
            'versions': versions, 'vue_partielle': len(visibles) < len(contenu['dossiers'])}


# ================================================================================================ G09 fonds, frais, débours
def _masquer_iban(iban):
    s = re.sub(r'\s+', '', str(iban or '')).upper()
    return (s[:4] + ' **** **** ' + s[-4:]) if len(s) >= 8 else '****'


def enregistrer_mouvement(desk, acteur, data):
    """Mouvement complémentaire du dossier (ce que ``frais5697`` et les règlements ``b53`` ne couvrent pas) : fonds de tiers reçus ou
    reversés, débours avancés, frais prévus. Un encaissement d'honoraires reste un règlement de la facture (jamais recopié). Clé
    métier ``(source, identifiant)`` : un double import ne compte qu'une fois. Une correction est un mouvement compensateur (``compense``)."""
    a = _acteur(acteur)
    _droit(desk, acteur, 'facturation')
    mid = _dossier(desk, acteur, data.get('dossier'))
    err = {}
    nature = str(data.get('nature') or '')
    if nature not in NATURES:
        err['nature'] = 'Nature : honoraires, frais, débours ou fonds de tiers.'
    sens = str(data.get('sens') or '')
    if sens not in ('entree', 'sortie'):
        err['sens'] = 'Sens : entrée ou sortie.'
    etat = str(data.get('etat') or 'constate')
    if etat not in ('prevu', 'constate'):
        err['etat'] = 'État : prévu ou constaté.'
    if nature == 'honoraires' and etat == 'constate':
        raise Refus('honoraires_par_reglement', {'nature': MESSAGES['honoraires_par_reglement']})
    devise = str(data.get('devise') or 'EUR').upper()
    try:
        montant = montant_minor(data.get('montant'), devise)
    except ValueError as ex:
        err['devise' if str(ex) == 'devise' else 'montant'] = 'Devise en trois lettres.' if str(ex) == 'devise' else 'Montant positif, sans flottant, selon les décimales de la devise.'
        montant = None
    try:
        jour = _jour(data.get('jour'))
    except Stop:
        err['jour'] = 'Date invalide.'
        jour = ''
    source_kind = str(data.get('source_kind') or 'saisie')
    source_id = str(data.get('source_id') or '').strip()[:120]
    if source_kind not in ('saisie', 'import', 'releve', 'paiement', 'frais'):
        err['source_kind'] = 'Origine inconnue.'
    if source_kind in ('paiement', 'frais'):
        raise Stop('paiement_deja_compte')           # le règlement b53 et le frais5697 sont déjà comptés par la vue : jamais recopiés
    compense = str(data.get('compense') or '')
    if compense:
        o = desk.db.execute('SELECT * FROM mouvements56109 WHERE id=? AND matter=?', (compense, mid)).fetchone()
        if not o:
            err['compense'] = 'Mouvement à corriger introuvable.'
        elif o['nature'] != nature or o['devise'] != devise or o['sens'] == sens:
            err['compense'] = 'Un mouvement compensateur reprend la nature et la devise, en sens inverse.'
    if err:
        raise Refus('champs_invalides', err)
    if source_kind == 'saisie' and not source_id:
        source_id = 'cle:' + _g().cle_de(data.get('cle'))
    if not source_id:
        raise Refus('champs_invalides', {'source_id': 'Référence de l’import ou du relevé requise.'})
    empreinte = _sha([mid, nature, sens, etat, montant, devise, jour, _texte(data.get('contrepartie'), 200), compense])
    with _transaction(desk):
        old = desk.db.execute('SELECT * FROM mouvements56109 WHERE source_kind=? AND source_id=?', (source_kind, source_id)).fetchone()
        if old:
            if json.loads(old['qualification'] or '{}').get('empreinte_saisie') != empreinte:
                raise Stop('paiement_deja_compte')
            return {'mouvement': old['id'], 'deja': True, 'message': 'Mouvement déjà enregistré : rien n’a été ajouté.'}
        ident = 'mvt-' + uuid.uuid4().hex[:24]
        desk.db.execute('INSERT INTO mouvements56109(id,matter,nature,sens,etat,montant_minor,devise,jour,contrepartie,beneficiaire,justificatif,source_kind,source_id,compense,'
                        'qualification,auteur,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, mid, nature, sens, etat, montant, devise, jour, _texte(data.get('contrepartie'), 200), _texte(data.get('beneficiaire'), 120),
                         _texte(data.get('justificatif'), 300), source_kind, source_id, compense,
                         json.dumps({'empreinte_saisie': empreinte, 'qualifie': False}), a.compte, _maintenant()))
        _g().journal(desk, mid, 'mouvement', ident, 'saisie', acteur, '', {}, {'nature': nature, 'montant_minor': montant, 'devise': devise, 'etat': etat})
    return {'mouvement': ident, 'deja': False, 'message': 'Mouvement enregistré (%s, %s).' % (NATURES[nature].lower(), 'prévu' if etat == 'prevu' else 'constaté')}


def rapprocher_mouvement(desk, acteur, data):
    """Rapprochement HUMAIN d'un mouvement avec un relevé ou un justificatif : référence, auteur, date. Un mouvement prévu devient
    constaté seulement par cette décision, jamais par l'écoulement d'une date. Jamais par l'IA."""
    a = _acteur(acteur)
    if a.role == 'agent':
        raise Stop('verification_humaine_requise')
    _droit(desk, acteur, 'facturation')
    m = desk.db.execute('SELECT * FROM mouvements56109 WHERE id=?', (str(data.get('id') or ''),)).fetchone()
    if not m:
        raise Stop('introuvable')
    _dossier(desk, acteur, m['matter'])
    _version(m, data.get('version'))
    ref = _texte(data.get('reference'), 200)
    if not ref:
        raise Refus('champs_invalides', {'reference': 'Référence du relevé ou du justificatif requise.'})
    with _transaction(desk):
        desk.db.execute("UPDATE mouvements56109 SET etat='constate',rapproche_par=?,rapproche_le=?,rapprochement=?,justificatif=CASE WHEN justificatif='' THEN ? ELSE justificatif END,"
                        'version=version+1 WHERE id=? AND version=?', (a.compte, _maintenant(), ref, _texte(data.get('justificatif'), 300), m['id'], m['version']))
        _g().journal(desk, m['matter'], 'mouvement', m['id'], 'rapprochement', acteur, '', {'etat': m['etat']}, {'etat': 'constate', 'reference': ref})
    return {'mouvement': m['id'], 'message': 'Mouvement rapproché par %s.' % a.compte}


def qualifier_debours(desk, acteur, data):
    """Décision HUMAINE de qualification d'un débours (selon la convention et les règles applicables, avec justificatif) : le logiciel
    enregistre la décision et son auteur ; il ne déduit aucune règle fiscale et ne qualifie jamais d'après le mot « frais » ou un modèle."""
    a = _acteur(acteur)
    if a.role == 'agent':
        raise Stop('verification_humaine_requise')
    _droit(desk, acteur, 'facturation')
    _g().valideur(acteur)
    m = desk.db.execute("SELECT * FROM mouvements56109 WHERE id=? AND nature='debours'", (str(data.get('id') or ''),)).fetchone()
    if not m:
        raise Stop('introuvable')
    _dossier(desk, acteur, m['matter'])
    _version(m, data.get('version'))
    motif = _texte(data.get('motif'), 300)
    if not motif or not (m['justificatif'] or _texte(data.get('justificatif'), 300)):
        raise Refus('champs_invalides', {'motif': 'Motif (convention, règle retenue) et justificatif requis : rien n’est qualifié automatiquement.'})
    q = json.loads(m['qualification'] or '{}')
    q.update(qualifie=True, par=a.compte, le=_maintenant(), motif=motif)
    with _transaction(desk):
        desk.db.execute('UPDATE mouvements56109 SET qualification=?,justificatif=CASE WHEN justificatif=\'\' THEN ? ELSE justificatif END,version=version+1 WHERE id=? AND version=?',
                        (json.dumps(q, ensure_ascii=False), _texte(data.get('justificatif'), 300), m['id'], m['version']))
        _g().journal(desk, m['matter'], 'mouvement', m['id'], 'qualification', acteur, motif)
    return {'mouvement': m['id'], 'message': 'Qualification enregistrée (décision de %s).' % a.compte}


def changer_coordonnees(desk, acteur, data):
    """Nouvelles coordonnées bancaires d'un bénéficiaire : nouvelle version (l'ancienne reste), provenance, date, auteur. Seuls l'IBAN
    masqué et son empreinte sont conservés (le logiciel bancaire du cabinet garde l'IBAN complet). Toujours « À vérifier » : une clé
    IBAN correcte ne prouve ni l'identité ni la titularité."""
    from .facturation5653 import iban_ok
    a = _acteur(acteur)
    _droit(desk, acteur, 'facturation')
    mid = _dossier(desk, acteur, data.get('dossier')) if data.get('dossier') else ''
    benef = _texte(data.get('beneficiaire'), 120)
    iban = re.sub(r'\s+', '', str(data.get('iban') or '')).upper()
    err = {}
    if not benef:
        err['beneficiaire'] = 'Bénéficiaire requis.'
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', iban):
        err['iban'] = 'IBAN illisible.'
    provenance = str(data.get('provenance') or '')
    if provenance not in ('courriel', 'courrier', 'telephone', 'remise_en_main_propre', 'saisie'):
        err['provenance'] = 'Provenance requise (courriel, courrier, téléphone…).'
    try:
        recu_le = _jour(data.get('recu_le'), False) or jour_cabinet(desk).isoformat()
    except Stop:
        err['recu_le'] = 'Date invalide.'
        recu_le = ''
    if err:
        raise Refus('champs_invalides', err)
    empreinte = hashlib.sha256(('iban56109|' + iban).encode()).hexdigest()
    with _transaction(desk):
        dern = desk.db.execute('SELECT * FROM coordonnees56109 WHERE beneficiaire=? ORDER BY version DESC LIMIT 1', (benef,)).fetchone()
        if dern and dern['iban_empreinte'] == empreinte and dern['etat'] != 'rejete':
            return {'coordonnees': dern['id'], 'version': dern['version'], 'deja': True, 'message': 'Coordonnées identiques à la version en cours.'}
        version = (dern['version'] + 1) if dern else 1
        cid = 'iban-' + uuid.uuid4().hex[:24]
        desk.db.execute("INSERT INTO coordonnees56109(id,beneficiaire,version,iban_masque,iban_empreinte,cle_iban_correcte,provenance,recu_le,matter,etat,auteur,cree_le) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'a_verifier',?,?)", (cid, benef, version, _masquer_iban(iban), empreinte, int(iban_ok(iban)), provenance, recu_le, mid,
                                                                       a.compte, _maintenant()))
        _g().journal(desk, mid or '-', 'coordonnees', cid, 'changement', acteur, '', {'version': dern['version'] if dern else 0, 'iban': dern['iban_masque'] if dern else ''},
                     {'version': version, 'iban': _masquer_iban(iban), 'provenance': provenance})
    return {'coordonnees': cid, 'version': version, 'etat': 'a_verifier', 'iban': _masquer_iban(iban),
            'message': 'Changement enregistré : À vérifier par une autre personne habilitée, par un moyen indépendant (numéro connu, pas celui du courriel).'}


def verifier_coordonnees(desk, acteur, data):
    """Vérification indépendante d'un changement bancaire : une personne habilitée (avocat ou administrateur), AUTRE que celle qui a
    enregistré le changement, avec la référence de sa vérification. L'IA (rôle ``agent``) ne lève jamais l'alerte. Aucun paiement."""
    a = _acteur(acteur)
    if a.role == 'agent':
        raise Stop('verification_humaine_requise')
    _droit(desk, acteur, 'facturation')
    _g().valideur(acteur)
    c = desk.db.execute('SELECT * FROM coordonnees56109 WHERE id=?', (str(data.get('id') or ''),)).fetchone()
    if not c:
        raise Stop('introuvable')
    if c['matter']:
        _dossier(desk, acteur, c['matter'])
    if c['etat'] != 'a_verifier':
        raise Stop('version_perimee')
    if c['auteur'] == a.compte:
        raise Stop('verification_independante_requise')
    decision = str(data.get('decision') or 'verifie')
    ref = _texte(data.get('reference'), 200)
    if decision not in ('verifie', 'rejete') or not ref:
        raise Refus('champs_invalides', {'reference': 'Indiquez comment le changement a été vérifié (appel au numéro connu, courrier signé…).'})
    with _transaction(desk):
        cur = desk.db.execute("UPDATE coordonnees56109 SET etat=?,verifie_par=?,verifie_le=?,reference_verification=? WHERE id=? AND etat='a_verifier'",
                              (decision, a.compte, _maintenant(), ref, c['id']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        _g().journal(desk, c['matter'] or '-', 'coordonnees', c['id'], 'verification_' + decision, acteur, ref)
    return {'coordonnees': c['id'], 'etat': decision, 'message': 'Changement %s par %s.' % ('vérifié' if decision == 'verifie' else 'rejeté', a.compte)}


def coordonnees_a_verifier(desk, beneficiaires=None):
    """Bénéficiaires dont la dernière version des coordonnées reste « À vérifier »."""
    out = {}
    for r in desk.db.execute('SELECT c.* FROM coordonnees56109 c WHERE c.version=(SELECT MAX(version) FROM coordonnees56109 x WHERE x.beneficiaire=c.beneficiaire)'):
        if r['etat'] == 'a_verifier' and (beneficiaires is None or r['beneficiaire'] in beneficiaires):
            out[r['beneficiaire']] = {'id': r['id'], 'version': r['version'], 'iban': r['iban_masque'], 'provenance': r['provenance'], 'recu_le': r['recu_le'],
                                      'auteur': r['auteur']}
    return out


def paiement_partiel(desk, acteur, doc_id):
    """Exposé pour G3/G4 : état d'une facture émise — dû, réglé (règlements vérifiés ``b53_payments``, chacun compté une fois), reste,
    état (non réglée / partielle / réglée). Lecture seule ; aucun paiement."""
    from . import facturation5653
    _droit(desk, acteur, 'voir_honoraires')
    d = facturation5653.get(desk, doc_id)
    _dossier(desk, acteur, d['matter'])
    if d['kind'] != 'facture' or not d['number']:
        raise Stop('introuvable')
    reste = facturation5653.invoice_balance(desk, d['id'])
    du = int(d['snapshot']['totaux']['net_a_payer_cents'])
    return {'facture': d['id'], 'numero': d['number'], 'du_cents': du, 'regle_cents': du - reste, 'reste_cents': reste, 'devise': 'EUR',
            'etat': d['payment_state'], 'reglements': [{'id': p['id'], 'montant_cents': p['amount_cents'], 'jour': p['day']} for p in d['payments'] if p['verified']]}


def enregistrer_frais_achat(desk, acteur, mid, data):
    """Exposé pour G3/G4 (frais d'achat d'une formalité, d'une offre) : passe par le registre existant des frais (``honoraires5697.saisir_frais``,
    nature ``frais`` ou ``debours`` À QUALIFIER par l'avocat) ; aucune seconde comptabilité, aucun paiement."""
    from . import honoraires5697
    return honoraires5697.saisir_frais(desk, acteur, mid, data)


def vue_financiere(desk, acteur, mid):
    """Vue financière du dossier (lecture seule) : quatre familles séparées, par devise et par état. Honoraires : factures émises et
    règlements vérifiés (registre ``b53``, chaque règlement une fois) ; frais et débours : ``frais5697`` (EUR, HT) puis mouvements
    complémentaires ; fonds de tiers : relevé documenté (entrées − sorties constatées), jamais additionné aux honoraires ni présenté
    comme disponible pour les régler. Aucun total ne mélange deux devises ; aucune conversion."""
    from . import honoraires5697
    _droit(desk, acteur, 'voir_honoraires')
    mid = _dossier(desk, acteur, mid)
    honoraires5697._schemas(desk)   # noqa: SLF001 — schémas du registre (création idempotente déjà faite au démarrage)
    familles = {k: {} for k in NATURES}

    def case(nature, devise):
        return familles[nature].setdefault(devise, {'prevu_entrees': 0, 'prevu_sorties': 0, 'constate_entrees': 0, 'constate_sorties': 0, 'lignes': []})

    for f in honoraires5697._factures(desk, [mid]):   # noqa: SLF001
        c = case('honoraires', f['devise'])
        c['constate_entrees'] += f['regle_cents']
        c['prevu_entrees'] += max(0, f['solde_cents'])
        c['lignes'].append({'origine': 'facture', 'id': f['id'], 'libelle': 'Facture %s' % f['numero'], 'jour': f['emise_le'], 'regle': f['regle_cents'],
                            'reste': f['solde_cents'], 'etat': 'constate' if f['solde_cents'] <= 0 else 'partiel' if f['regle_cents'] else 'prevu'})
    for r in desk.db.execute("SELECT * FROM frais5697 WHERE matter=? ORDER BY jour", (mid,)):
        c = case('frais' if r['nature'] == 'frais' else 'debours', 'EUR')
        cle = 'constate_sorties'
        c[cle] += int(r['montant_ht_cents'])
        c['lignes'].append({'origine': 'frais5697', 'id': r['id'], 'libelle': r['libelle'], 'jour': r['jour'], 'montant': int(r['montant_ht_cents']),
                            'etat': r['etat'], 'qualification': 'à qualifier par l’avocat' if r['nature'] == 'debours' else ''})
    a_verifier = coordonnees_a_verifier(desk)
    alertes = []
    for m in desk.db.execute('SELECT * FROM mouvements56109 WHERE matter=? ORDER BY jour, cree_le', (mid,)):
        c = case(m['nature'], m['devise'])
        cle = '%s_%s' % (m['etat'], 'entrees' if m['sens'] == 'entree' else 'sorties')
        c[cle] += int(m['montant_minor'])
        q = json.loads(m['qualification'] or '{}')
        verif = m['beneficiaire'] in a_verifier and m['sens'] == 'sortie' and m['etat'] == 'prevu'
        if verif:
            alertes.append('Sortie prévue vers %s : coordonnées bancaires À vérifier (version %d reçue par %s).' % (
                m['beneficiaire'], a_verifier[m['beneficiaire']]['version'], a_verifier[m['beneficiaire']]['provenance']))
        c['lignes'].append({'origine': 'mouvement', 'id': m['id'], 'version': m['version'], 'libelle': m['contrepartie'] or NATURES[m['nature']], 'jour': m['jour'],
                            'montant': int(m['montant_minor']), 'sens': m['sens'], 'etat': m['etat'], 'justificatif': m['justificatif'], 'compense': m['compense'],
                            'rapproche': bool(m['rapproche_par']), 'a_verifier': verif,
                            'qualification': ('qualifié par %s' % q.get('par')) if q.get('qualifie') else ('à qualifier par l’avocat' if m['nature'] == 'debours' else '')})
    for nature, devises in familles.items():
        for devise, c in devises.items():
            c['solde_constate'] = c['constate_entrees'] - c['constate_sorties']
            c['devise'] = devise
    return {'dossier': mid, 'familles': familles, 'alertes': alertes, 'coordonnees_a_verifier': a_verifier, 'lu_le': _maintenant(),
            'mentions': ['Fonds de tiers : relevé documenté ; ils ne sont ni des honoraires encaissés ni un montant disponible pour les régler.',
                         'Aucun total ne mélange deux devises ; aucune conversion n’est faite.',
                         'Prévu et constaté sont distincts : un mouvement prévu ne devient constaté que par un rapprochement humain.',
                         'Les débours et leur traitement fiscal sont qualifiés par l’avocat ; le logiciel comptable du cabinet reste la référence.']}


# ================================================================================================ écrans (sections dans les pages existantes)
def _form(route, corps, bouton, recharger=True, cls='', confirmer=''):
    return ('<form class="d97-form g109-form%s" data-d97-api="%s"%s novalidate%s><input type="hidden" name="cle" value="%s">%s<div class="g109-actions">'
            '<button class="ax-btn%s" type="submit">%s</button><span class="d97-statut" role="status" aria-live="polite"></span></div></form>') % (
        (' ' + cls) if cls else '', e(PREFIXE + route, quote=True), ' data-d97-apres="recharger"' if recharger else '',
        (' data-d98-confirmer="%s"' % e(confirmer, quote=True)) if confirmer else '', uuid.uuid4().hex, corps, ' ghost' if 'ghost' in cls else '', e(bouton))


def _champ(label, nom, valeur='', kind='text', extra=''):
    return '<label class="g109-champ">%s<input type="%s" name="%s" value="%s" %s><span class="g109-err" data-erreur="%s"></span></label>' % (
        e(label), kind, e(nom, quote=True), e(str(valeur), quote=True), extra, e(nom, quote=True))


def _hidden(**kw):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in kw.items())


def section_prefacturation_html(desk, acteur, prefix, args):
    """Section « Préfacturation » de Temps et honoraires : période, personne, dossier ; tableau Date / Diligence / Source / Durée et
    origine / Montant proposé / Alerte / Sélection ; « Valider les lignes sélectionnées » (un lot atomique) puis « Préparer la facture »
    (service existant, brouillon). Lecture seule à l'affichage."""
    from .common import load_matters, matter_display
    if not _peut(desk, acteur, 'facturation') and not _peut(desk, acteur, 'voir_honoraires'):
        return ''
    filtres = {k: str(args.get('pf_' + k) or '') for k in ('du', 'au', 'dossier', 'personne')}
    try:
        v = prefacturation(desk, acteur, {k: x for k, x in filtres.items() if x})
    except Stop:
        v = {'lignes': [], 'voir_montants': False, 'dossiers': {}, 'compteurs': {'a_valider': 0, 'a_completer': 0, 'chevauchements': 0}}
    noms = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    opts = ''.join('<option value="%s"%s>%s</option>' % (e(mid, quote=True), ' selected' if mid == filtres['dossier'] else '', e(noms.get(mid, mid)))
                   for mid in sorted(_perimetre(desk, acteur), key=lambda x: noms.get(x, x).casefold()))
    filtre = ('<form class="g109-filtres" method="get" action="%s/honoraires#g109-prefacturation">'
              '<label>Du<input type="date" name="pf_du" value="%s"></label><label>Au<input type="date" name="pf_au" value="%s"></label>'
              '<label>Dossier<select name="pf_dossier"><option value="">Tous</option>%s</select></label>'
              '<label>Personne<input name="pf_personne" value="%s" maxlength="120"></label><button class="ax-btn ghost" type="submit">Filtrer</button></form>') % (
        e(prefix, quote=True), e(filtres['du'], quote=True), e(filtres['au'], quote=True), opts, e(filtres['personne'], quote=True))
    collecte = _form('prefacturation/collecter', _hidden(dossier=filtres['dossier'], du=filtres['du'], au=filtres['au']),
                     'Rechercher le travail non facturé', cls='g109-inline')
    voir = v['voir_montants']
    corps = ''
    for x in v['lignes']:
        if x['statut'] in FINAUX:
            continue
        duree = ('%d min' % x['minutes']) if x['minutes'] else '—'
        if x['montant_cents'] is not None:
            montant = _euros(x['montant_cents']) + (' (estimé)' if x['nature_montant'] == 'estime' else '')
        else:
            montant = {'forfait': 'Compris dans le forfait', 'taux_a_definir': 'Taux à définir', 'a_completer': 'À compléter'}.get(x['nature_montant'], '—')
        if not voir:
            montant = '—'
        sel = '<input type="checkbox" name="sel.%s" aria-label="Sélectionner">' % e(x['id'], quote=True)
        conf = ('<label class="g109-conf"><input type="checkbox" name="conf.%s"> confirmer</label>' % e(x['id'], quote=True)) if x['origine'] == 'estimee' else ''
        corps += ('<tr><td>%s</td><td>%s<br><small>%s</small></td><td>%s</td><td><input class="g109-min" type="number" min="1" max="1440" name="min.%s" value="%s" '
                  'aria-label="Durée en minutes"> <small class="g109-origine g109-%s">%s</small>%s<input type="hidden" name="ver.%s" value="%d">'
                  '<input type="hidden" name="taux.%s" value="%s"></td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
            e(x['jour']), e(x['libelle']), e(noms.get(x['matter'], x['matter'])), e(x['source'] + (' + %d' % (len(x['traces']) - 1) if len(x['traces']) > 1 else '')),
            e(x['id'], quote=True), e(str(x['minutes'] or ''), quote=True), e(x['origine']), e(x['origine_libelle'] + ('' if x['minutes'] else '')), conf,
            e(x['id'], quote=True), x['version'], e(x['id'], quote=True), e('' if x['taux_cents'] is None else str(x['taux_cents']), quote=True),
            e(montant), e(' '.join(x['alertes']) or x['statut_libelle']), sel)
    if corps:
        table = ('<div class="g109-table-wrap"><table class="vf-table g109-table"><thead><tr><th>Date</th><th>Diligence</th><th>Source</th><th>Durée et origine</th>'
                 '<th>Montant proposé</th><th>Alerte</th><th>Sélection</th></tr></thead><tbody>%s</tbody></table></div>') % corps
        table = _form('prefacturation/valider-lot', table, 'Valider les lignes sélectionnées')
    else:
        table = '<p class="vf-note">Aucune diligence à valider pour ce choix. Lancez la recherche du travail non facturé.</p>'
    factures = ''
    if voir and _peut(desk, acteur, 'facturation'):
        for mid, info in v['dossiers'].items():
            if info.get('a_facturer_cents'):
                factures += '<li>%s — %s validés non facturés %s</li>' % (
                    e(noms.get(mid, mid)), e(_euros(info['a_facturer_cents'])),
                    ('<form class="d97-form g109-form g109-inline" data-d97-api="m5689/gestion/honoraires/brouillon" data-d97-apres="recharger" novalidate>'
                     '<input type="hidden" name="cle" value="%s"><input type="hidden" name="dossier" value="%s"><button class="ax-btn ghost" type="submit">'
                     'Préparer la facture</button><span class="d97-statut" role="status" aria-live="polite"></span></form>') % (uuid.uuid4().hex, e(mid, quote=True)))
    tarifs = ''.join('<li>%s : %s%s</li>' % (e(noms.get(mid, mid)), e(info['mode']), (' — %s' % e(info['tarif']['libelle'])) if info.get('tarif') else '')
                     for mid, info in v['dossiers'].items() if voir)
    c = v['compteurs']
    return ('<section id="g109-prefacturation" class="g109"><h2>Préfacturation</h2><p class="vf-note">Les traces d’une même diligence (rendez-vous et compte rendu, '
            'appel relevé deux fois) forment une seule ligne. Une durée inconnue reste à compléter ; une estimation est étiquetée « estimée » et doit être confirmée. '
            'La validation enregistre tout le lot ou rien ; la facture se prépare ensuite, en brouillon. Émission et envoi restent des décisions distinctes.</p>'
            '<p class="g109-compteurs">%d à valider · %d à compléter · %d chevauchement(s)</p>%s%s%s%s%s</section>') % (
        c['a_valider'], c['a_completer'], c['chevauchements'], filtre, collecte, table,
        ('<h3>Préparer la facture</h3><ul class="g109-liste">%s</ul>' % factures) if factures else '',
        ('<details><summary>Conditions et tarif applicables</summary><ul class="g109-liste">%s</ul></details>' % tarifs) if tarifs else '')


def section_assistance_html(desk, acteur, prefix, contact):
    """Section « Assistance régulière » de la fiche d'un contact client (lecture seule à l'affichage)."""
    if not _peut(desk, acteur, 'voir_honoraires'):
        return ''
    try:
        v = assistance(desk, acteur, contact['id'])
    except Stop:
        return ''
    if not v['contrat'] and contact.get('category') != 'client':
        return ''
    ecrire = _peut(desk, acteur, 'facturation')
    c = v['contrat']
    html = '<section class="ax-card g109" id="g109-assistance"><h2>Assistance régulière</h2>'
    if c:
        conv = v.get('convention') or {'etat': 'aucune'}
        etat_conv = {'signee': 'Convention signée (preuve enregistrée)', 'envoyee': 'Convention envoyée, non signée : elle ne fait pas encore foi',
                     'approuvee': 'Convention approuvée, non envoyée', 'aucune': 'Aucune convention reliée', 'introuvable': 'Convention introuvable'}.get(conv.get('etat'), conv.get('etat'))
        html += ('<p>Contrat version %d · période %s · du %s%s · %s · dossiers convenus : %s</p><p class="g109-%s">%s</p>') % (
            c['version'], e(PERIODICITES[c['periodicite']]), e(c['debut']), (' au %s' % e(c['fin'])) if c.get('fin') else '',
            e(afficher(c['montant_minor'], c['devise']) if c.get('montant_minor') else 'montant à préciser'), e(', '.join(c['dossiers'])),
            'ok' if conv.get('etat') == 'signee' else 'alerte', e(etat_conv))
        if v['vue_partielle']:
            html += '<p class="g109-alerte">Vue partielle : certains dossiers du contrat ne vous sont pas accessibles.</p>'
        for p in v['periodes']:
            lignes = ''
            for sid, s in p['services'].items():
                reste = 'À compléter (durée inconnue)' if s['reste_exact'] is None else '%d %s' % (s['reste_exact'], UNITES[s['unite']])
                lignes += '<tr><td>%s</td><td>%d %s</td><td>%d %s</td><td>%s</td><td>%s</td></tr>' % (
                    e(s['libelle']), s['quota'], e(UNITES[s['unite']]), s['consommation_connue'], e(UNITES[s['unite']]), e(reste),
                    e(('Dépassement connu : %d — proposition de devis ou d’avenant' % s['depassement_connu']) if s['depassement_connu'] else ''))
            gestes = ''
            if ecrire and p['etat'] == 'ouverte':
                gestes = (_form('assistance/affecter', _hidden(periode=p['periode']), 'Décompter les temps validés de la période', cls='g109-inline ghost') +
                          _form('assistance/cloturer', _hidden(periode=p['periode']), 'Clôturer la période', cls='g109-inline ghost'))
            if ecrire:
                gestes += _form('assistance/compte-rendu', _hidden(periode=p['periode']), 'Préparer le compte rendu (Produire)', cls='g109-inline ghost')
            html += ('<h3>Période du %s au %s%s — %s</h3><table class="vf-table"><thead><tr><th>Prestation</th><th>Inclus</th><th>Consommé (validé)</th><th>Reste</th>'
                     '<th>Hors forfait</th></tr></thead><tbody>%s</tbody></table><p class="vf-note">%d diligence(s) à compléter · %d estimation(s) non comptée(s) · '
                     '%d temps validé(s) non décompté(s)%s</p>%s') % (
                e(p['debut']), e(p['fin']), ' (partielle)' if p['partielle'] else '', 'ouverte' if p['etat'] == 'ouverte' else 'close', lignes,
                len(p['inconnues']), p['estimations_non_comptees'], len(p['temps_non_affectes']),
                (' · <a href="%s">compte rendu</a>' % e(prefix + '/livrables/' + p['compte_rendu'], quote=True)) if p['compte_rendu'] else '', gestes)
        if ecrire:
            html += _form('assistance/periode', _hidden(contrat=c['id']) + _champ('Date dans la période', 'jour', '', 'date'), 'Ouvrir la période', cls='g109-inline ghost')
        html += '<details><summary>Historique des versions (%d)</summary><ul class="g109-liste">%s</ul></details>' % (
            len(v['versions']), ''.join('<li>Version %d — %s, applicable du %s</li>' % (x['version'], e(x['auteur']), e(x['applicable_du'])) for x in v['versions']))
    else:
        html += '<p class="vf-note">Aucun contrat d’assistance régulière pour ce client.</p>'
    if ecrire:
        s = (c or {}).get('services') or [{}]
        corps = (_hidden(client=contact['id'], version=c['version'] if c else 0) + _champ('Début', 'debut', (c or {}).get('debut', ''), 'date') +
                 _champ('Fin (facultative)', 'fin', (c or {}).get('fin', ''), 'date') +
                 '<label class="g109-champ">Périodicité<select name="periodicite">%s</select></label>' % ''.join(
                     '<option value="%s"%s>%s</option>' % (k, ' selected' if (c or {}).get('periodicite') == k else '', e(x)) for k, x in PERIODICITES.items()) +
                 _champ('Montant par période', 'montant', '', 'text', 'inputmode="decimal"') + _champ('Devise', 'devise', (c or {}).get('devise', 'EUR'), 'text', 'maxlength="3"') +
                 _champ('Prestation incluse', 'service_1', s[0].get('libelle', 'Consultations courantes')) +
                 '<label class="g109-champ">Unité<select name="unite_1"><option value="minutes">minutes</option><option value="prestation"%s>prestations</option></select></label>' % (
                     ' selected' if s[0].get('unite') == 'prestation' else '') +
                 _champ('Quantité incluse', 'inclus_1', s[0].get('inclus', ''), 'number', 'min="0"') +
                 _champ('Dossiers convenus (références)', 'dossiers', ' '.join((c or {}).get('dossiers', []))) +
                 '<label class="g109-case"><input type="checkbox" name="report"%s> Report du solde sur la période suivante</label>' % (
                     ' checked' if (c or {}).get('report') == 'periode_suivante' else '') +
                 _champ('Délai de réponse (jours ouvrés)', 'delai_reponse', (c or {}).get('delai_reponse_jours_ouvres') or '', 'number', 'min="0" max="60"') +
                 _champ('Convention (identifiant du registre de facturation)', 'convention', (c or {}).get('convention', '')) +
                 _champ('Hors forfait', 'hors_forfait', (c or {}).get('hors_forfait', '')))
        html += '<details><summary>%s</summary>%s</details>' % ('Modifier le contrat (nouvelle version)' if c else 'Créer le contrat d’assistance',
                                                               _form('assistance/contrat', corps, 'Enregistrer le contrat'))
    return html + '</section>'


def section_finances_html(desk, acteur, prefix, mid):
    """Vue « Fonds, frais et débours » du dossier, sous le bloc Honoraires et facturation (lecture seule à l'affichage)."""
    if not _peut(desk, acteur, 'voir_honoraires'):
        return ''
    try:
        v = vue_financiere(desk, acteur, mid)
    except Stop:
        return ''
    ecrire = _peut(desk, acteur, 'facturation')
    blocs = ''
    for nature, devises in v['familles'].items():
        if not devises:
            blocs += '<div class="g109-famille"><h4>%s</h4><p class="vf-note">Aucun élément.</p></div>' % e(NATURES[nature])
            continue
        for devise, c in devises.items():
            lignes = ''.join('<li>%s — %s%s%s%s</li>' % (
                e(x.get('jour') or ''), e(x.get('libelle') or ''),
                (' · %s' % e(afficher(x['montant'], devise))) if 'montant' in x else (' · réglé %s, reste %s' % (e(afficher(x['regle'], devise)), e(afficher(x['reste'], devise))) if 'regle' in x else ''),
                (' · %s' % e({'prevu': 'prévu', 'constate': 'constaté', 'partiel': 'partiel'}.get(x.get('etat'), x.get('etat') or ''))),
                (' · ' + e(x['qualification'])) if x.get('qualification') else '') + ('<strong class="g109-alerte"> À vérifier</strong>' if x.get('a_verifier') else '')
                for x in c['lignes'][-20:])
            libelle_solde = 'Relevé (constaté)' if nature == 'fonds_tiers' else 'Constaté'
            blocs += ('<div class="g109-famille"><h4>%s (%s)</h4><p>%s : entrées %s · sorties %s · prévu %s / %s</p><ul class="g109-liste">%s</ul></div>') % (
                e(NATURES[nature]), e(devise), libelle_solde, e(afficher(c['constate_entrees'], devise)), e(afficher(c['constate_sorties'], devise)),
                e(afficher(c['prevu_entrees'], devise)), e(afficher(c['prevu_sorties'], devise)), lignes)
    alertes = ''.join('<p class="g109-alerte">%s</p>' % e(a) for a in v['alertes'])
    gestes = ''
    if ecrire:
        gestes = '<details><summary>Enregistrer un mouvement (fonds de tiers, débours, frais prévus)</summary>%s</details>' % _form('finances/mouvement', _hidden(dossier=mid) +
            '<label class="g109-champ">Nature<select name="nature"><option value="fonds_tiers">Fonds de tiers</option><option value="debours">Débours</option>'
            '<option value="frais">Frais</option><option value="honoraires">Honoraires (prévu)</option></select></label>'
            '<label class="g109-champ">Sens<select name="sens"><option value="entree">Entrée</option><option value="sortie">Sortie</option></select></label>'
            '<label class="g109-champ">État<select name="etat"><option value="constate">Constaté</option><option value="prevu">Prévu</option></select></label>' +
            _champ('Montant', 'montant', '', 'text', 'inputmode="decimal" required') + _champ('Devise', 'devise', 'EUR', 'text', 'maxlength="3"') +
            _champ('Date', 'jour', jour_cabinet(desk).isoformat(), 'date') + _champ('Contrepartie', 'contrepartie') + _champ('Bénéficiaire', 'beneficiaire') +
            _champ('Justificatif (pièce)', 'justificatif'), 'Enregistrer le mouvement')
        gestes += '<details><summary>Changement de coordonnées bancaires reçu</summary>%s</details>' % _form('finances/coordonnees', _hidden(dossier=mid) +
            _champ('Bénéficiaire', 'beneficiaire', '', 'text', 'required') + _champ('Nouvel IBAN', 'iban', '', 'text', 'autocomplete="off" required') +
            '<label class="g109-champ">Provenance<select name="provenance"><option value="courriel">Courriel</option><option value="courrier">Courrier</option>'
            '<option value="telephone">Téléphone</option><option value="remise_en_main_propre">Remise en main propre</option></select></label>' +
            _champ('Reçu le', 'recu_le', jour_cabinet(desk).isoformat(), 'date'), 'Enregistrer (À vérifier)')
        for benef, c in v['coordonnees_a_verifier'].items():
            gestes += '<p class="g109-alerte">Coordonnées de %s (version %d, %s) : À vérifier.</p>%s' % (e(benef), c['version'], e(c['iban']), _form(
                'finances/coordonnees/verifier', _hidden(id=c['id'], decision='verifie') + _champ('Vérifié comment (moyen indépendant)', 'reference', '', 'text', 'required'),
                'Je confirme l’avoir vérifié', cls='g109-inline ghost'))
    return ('<section class="d51-block g109" id="g109-finances"><h3>Fonds, frais et débours</h3>%s<div class="g109-familles">%s</div><ul class="vf-note">%s</ul>%s</section>') % (
        alertes, blocs, ''.join('<li>%s</li>' % e(m) for m in v['mentions']), gestes)


def habiller(desk, env, prefix, path, args, shell):
    """Point de montage dans les pages existantes (``web440``) : la page est rendue par son module, puis la section du lot est insérée
    à sa place — Temps et honoraires (avant « Dossiers »), fiche d'un contact client (avant « Archiver »), dossier (sous le bloc
    Honoraires et facturation). Un incident n'empêche jamais la page de s'afficher ; aucune écriture."""
    from .web567 import actor
    acteur = actor(env)

    def inserer(html):
        try:
            if path == '/honoraires':
                sec = section_prefacturation_html(desk, acteur, prefix, args)
                if sec:
                    i = html.find('<section id="dossiers">')
                    return html[:i] + sec + html[i:] if i >= 0 else html + sec
            elif path == '/contacts-crm' and args.get('id') and not args.get('vue'):
                from . import crm5636
                c = crm5636.get(desk, str(args['id']))
                sec = section_assistance_html(desk, acteur, prefix, c)
                if sec:
                    i = html.find('<form class="m5-form m5-inline" data-api="m5636/contacts/archiver"')
                    return html[:i] + sec + html[i:] if i >= 0 else html + sec
            elif path == '/dossier' and args.get('id'):
                i = html.find('b53-matter-block')
                if i >= 0:
                    j = html.find('</section>', i)
                    if j >= 0:
                        sec = section_finances_html(desk, acteur, prefix, str(args['id']))
                        return html[:j + 10] + sec + html[j + 10:]
        except Exception:   # noqa: BLE001 — la page existante reste affichée telle quelle
            import logging
            logging.getLogger(__name__).exception('gestion56109 : section non insérée (%s)', path)
        return html

    def habillee(title, html, *a, **k):
        return shell(title, inserer(html), *a, **k)
    return habillee


# ================================================================================================ routes
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('prefacturation', 'assistance', 'finances', 'paiement')
ROUTES_POST = ('prefacturation/collecter', 'prefacturation/trace', 'prefacturation/completer', 'prefacturation/rapprocher', 'prefacturation/ecarter',
               'prefacturation/valider-lot', 'assistance/contrat', 'assistance/periode', 'assistance/affecter', 'assistance/cloturer', 'assistance/compte-rendu',
               'finances/mouvement', 'finances/rapprocher', 'finances/qualifier', 'finances/coordonnees', 'finances/coordonnees/verifier')
VALIDEURS_SEULS = ('assistance/cloturer', 'finances/qualifier', 'finances/coordonnees/verifier')


def allowed(role, method, api_):
    """Contrôle de rôle sans état (``standalone_auth``) : les trois rôles du cabinet atteignent les routes ; chaque droit fin (« voir les
    honoraires », « facturation ») est relu par le service ; clôturer, qualifier un débours et vérifier des coordonnées bancaires : jamais
    un assistant ni l'IA."""
    route = str(api_ or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else None
    if role not in ROLES or route is None:
        return False
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET
    if method != 'POST' or route not in ROUTES_POST:
        return False
    return role != 'assistant' or route not in VALIDEURS_SEULS


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0]
        if code == 'dossier_absent':
            code = 'introuvable'
        corps = {'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}
        detail = getattr(ex, 'detail', None)
        if isinstance(detail, dict) and detail:
            corps['champs'] = detail
        return _out(corps, STATUTS_HTTP.get(code, '400 Bad Request'))
    except sqlite3.IntegrityError as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        return _out({'error': 'conflit', 'message': 'Enregistrement refusé par une contrainte d’intégrité : rien n’a été écrit (%s).' % str(ex)[:80]}, '409 Conflict')


ROUTES_SANS_RECU = ('prefacturation/valider-lot', 'assistance/compte-rendu')   # reçu écrit DANS la transaction du service


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    acteur = (owner, role)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    if not allowed(role, method, PREFIXE + route):
        raise Stop('role_insuffisant' if (role not in ROLES or route in VALIDEURS_SEULS) else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if route == 'prefacturation':
            return _out(prefacturation(desk, acteur, {k: str(args.get(k) or '') for k in ('du', 'au', 'dossier', 'personne') if args.get(k)}))
        if route == 'assistance':
            return _out(assistance(desk, acteur, args.get('client')))
        if route == 'finances':
            return _out(vue_financiere(desk, acteur, args.get('dossier')))
        return _out(paiement_partiel(desk, acteur, args.get('facture')))
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné, objet JSON
    if route == 'prefacturation/valider-lot':
        return _out(valider_lot(desk, acteur, data))
    if route == 'assistance/compte-rendu':
        return _out(compte_rendu(desk, acteur, data))
    cle = str(data.get('cle') or '')
    empreinte = _sha({k: v for k, v in data.items() if k != 'cle'} | {'route': route})
    ancien = _recu(desk, owner, route, cle, empreinte)
    if ancien is not None:
        return _out({**ancien, 'rejoue': True})
    fn = {'prefacturation/collecter': collecter, 'prefacturation/trace': signaler_trace, 'prefacturation/completer': completer,
          'prefacturation/rapprocher': rapprocher, 'prefacturation/ecarter': ecarter, 'assistance/contrat': enregistrer_contrat,
          'assistance/periode': ouvrir_periode, 'assistance/affecter': affecter, 'assistance/cloturer': cloturer_periode,
          'finances/mouvement': enregistrer_mouvement, 'finances/rapprocher': rapprocher_mouvement, 'finances/qualifier': qualifier_debours,
          'finances/coordonnees': changer_coordonnees, 'finances/coordonnees/verifier': verifier_coordonnees}[route]
    out = fn(desk, acteur, data)
    desk.db.execute('INSERT OR IGNORE INTO gestion_recus56109(compte,route,cle,empreinte,reponse,quand) VALUES(?,?,?,?,?,?)',
                    (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str), _maintenant()))
    desk.db.commit()
    return _out(out, '201 Created' if route in ('assistance/contrat', 'finances/mouvement', 'finances/coordonnees') else '200 OK')
