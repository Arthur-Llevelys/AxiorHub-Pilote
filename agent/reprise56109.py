"""5.6.109 (lot A, C05 et C06) : reprises sûres — un arrêt central reconnu partout de la même façon, et le curseur versionné d'un parcours borné.

**Arrêt central** (``arret_central``) : annulation, pause de l'IA ou du dossier, plage horaire, attente de budget ou d'abonnement, droits ou
compte retirés, autre poste exécuteur, verrou général occupé. Un gestionnaire d'erreur de transport ou de lecture ne l'absorbe jamais : il
remonte tel quel, sans devenir « partiel », « erreur réseau » ni une reprise automatique ; aucun nouvel effet n'est engagé après lui.

**Curseur d'une campagne de relevé** (``reprendre``, ``encoder``) : noyau de ``reference/reprise_releve.planifier`` du dossier technique,
raccordé au relevé des fichiers partagés (``sync56108_fichiers.relever``). Une campagne parcourt les dossiers partagés dans un ordre stable ;
son état (version du schéma, pile des répertoires à visiter avec leur profondeur et la dernière clé traitée, dossiers finis, erreurs de
lecture, bilan cumulé) tient dans la colonne ``curseur`` existante. Il est écrit APRÈS le traitement durable d'un lot : une interruption garde
l'ancien curseur, le rejeu reconnaît ce qui a déjà été publié. Le périmètre est revalidé à chaque passage sur la liste des dossiers partagés
RELUE : une position qui désigne un dossier retiré, un chemin hors de son dossier ou un curseur d'une autre version n'est jamais suivie (elle
est retirée, ou la campagne recommence) ; une clé inconnue n'est jamais lue comme une permission.

Aucun effet ici : ni base, ni stockage, ni réseau.
"""
import json
from pathlib import PurePosixPath

from .common import Stop

CURSEUR_VERSION = 1
MAX_CURSEUR = 512_000        # caractères du curseur enregistré ; au-delà la campagne recommence (jamais un curseur tronqué suivi à moitié)
MAX_PILE = 4000              # répertoires en attente dans une campagne
MAX_ERREURS = 50             # erreurs de lecture gardées avec leur motif (les suivantes sont comptées)
# Codes qui arrêtent le travail entier, en plus des codes centraux (``references56108.controle`` : arrêt, pause, budget, mission annulée,
# dossier réservé) : contrôle des droits illisible, compte ou rôle retirés, autre poste exécuteur, verrou général occupé, coupe-circuit.
ARRETS = frozenset(('controle_acces_indisponible', 'execution_sur_un_autre_poste', 'traitement_deja_en_cours', 'compte_desactive',
                    'role_insuffisant', 'autorisation_a_renouveler', 'relances_suspendues_apres_trois_echecs', 'dossier_absent',
                    'controle_suspension_indisponible'))


def code_de(ex):
    return str(ex).split(':')[0]


def arret_central(ex):
    """Vrai si ``ex`` est un arrêt central (voir le module) : à relancer tel quel, jamais à convertir."""
    if not isinstance(ex, Stop):
        return False
    code = code_de(ex)
    if code in ARRETS:
        return True
    try:
        from .references56108 import controle
        if controle(ex):
            return True
        from .chatgpt5682 import HELD_CODES
        from .operations5693 import REVOCATIONS
    except Exception:   # noqa: BLE001 — registre des codes illisible : par prudence l'exception remonte (jamais avalée comme une erreur ordinaire)
        return True
    return code in HELD_CODES or code in REVOCATIONS


# ------------------------------------------------------------------------------------------------ curseur d'une campagne
def _sous(chemin, base):
    c, b = PurePosixPath(str(chemin)), PurePosixPath(str(base))
    return c == b or b in c.parents


def nouvelle(dossiers, maintenant):
    """Campagne neuve : un répertoire racine par dossier partagé, dans l'ordre stable des identifiants."""
    return {'v': CURSEUR_VERSION, 'debut': float(maintenant), 'pile': [{'dossier': d, 'chemin': dossiers[d], 'prof': 0, 'apres': ''} for d in sorted(dossiers)],
            'faits': [], 'erreurs': [], 'erreurs_omises': 0, 'cumul': {'examines': 0, 'publies': 0, 'octets': 0, 'ignores': 0, 'passages': 0}}


def _position_valable(f, dossiers, profondeur):
    if not isinstance(f, dict) or set(f) != {'dossier', 'chemin', 'prof', 'apres'}:
        return False
    base = dossiers.get(f['dossier']) if isinstance(f['dossier'], str) else None
    if not base or not isinstance(f['chemin'], str) or not isinstance(f['apres'], str) or type(f['prof']) is not int:
        return False
    if not 0 <= f['prof'] <= profondeur or not _sous(f['chemin'], base) or '..' in PurePosixPath(f['chemin']).parts:
        return False
    return f['apres'] == '' or PurePosixPath(f['apres']).parent == PurePosixPath(f['chemin'])


def reprendre(brut, dossiers, maintenant, profondeur=4):
    """État de campagne à poursuivre : ``(état, reprise)``. ``dossiers`` : {identifiant: chemin} des dossiers partagés RELUS à l'instant.

    Curseur vide, illisible, d'une autre version ou démesuré : campagne neuve (``reprise`` faux). Sinon chaque position est revalidée : dossier
    encore partagé, chemin dans SON dossier, profondeur bornée ; les positions invalides sont retirées. Un dossier partagé ni fini ni présent
    dans la pile (partagé depuis, ou dont les positions viennent d'être retirées) repart de sa racine : il n'est jamais oublié."""
    try:
        etat = json.loads(brut) if brut and len(brut) <= MAX_CURSEUR else None
    except (ValueError, TypeError):
        etat = None
    if not (isinstance(etat, dict) and etat.get('v') == CURSEUR_VERSION and isinstance(etat.get('pile'), list) and len(etat['pile']) <= MAX_PILE
            and isinstance(etat.get('faits', []), list)):
        return nouvelle(dossiers, maintenant), False
    neuve = nouvelle(dossiers, maintenant)
    pile = [dict(f) for f in etat['pile'] if _position_valable(f, dossiers, profondeur)]
    faits = [d for d in etat.get('faits', []) if isinstance(d, str) and d in dossiers]
    presents = {f['dossier'] for f in pile}
    pile += [f for f in neuve['pile'] if f['dossier'] not in presents and f['dossier'] not in faits]
    cumul = dict(neuve['cumul'])
    if isinstance(etat.get('cumul'), dict):
        cumul.update({k: int(v) for k, v in etat['cumul'].items() if k in cumul and type(v) is int and v >= 0})
    erreurs = [x for x in etat.get('erreurs', []) if isinstance(x, dict)][:MAX_ERREURS] if isinstance(etat.get('erreurs'), list) else []
    debut = etat.get('debut')
    return {'v': CURSEUR_VERSION, 'debut': float(debut) if isinstance(debut, (int, float)) and not isinstance(debut, bool) else float(maintenant),
            'pile': pile, 'faits': faits, 'erreurs': erreurs,
            'erreurs_omises': int(etat.get('erreurs_omises') or 0) if type(etat.get('erreurs_omises')) is int else 0, 'cumul': cumul}, True


def noter_erreur(etat, dossier, chemin, motif):
    """Erreur de lecture d'une campagne : elle interdit « réussite complète » ; le motif est gardé (borné), jamais le contenu."""
    if len(etat['erreurs']) < MAX_ERREURS:
        etat['erreurs'].append({'dossier': str(dossier), 'chemin': str(chemin)[-300:], 'motif': str(motif)[:60]})
    else:
        etat['erreurs_omises'] += 1


def encoder(etat):
    """Curseur à enregistrer après un lot : ``''`` quand la pile est vide (campagne finie). Trop gros : ``''`` aussi, et la campagne suivante
    repart du début (un parcours borné reste borné ; l'appelant note alors l'erreur ``curseur_trop_volumineux``)."""
    if not etat['pile']:
        return ''
    brut = json.dumps(etat, ensure_ascii=False, separators=(',', ':'))
    return brut if len(brut) <= MAX_CURSEUR and len(etat['pile']) <= MAX_PILE else ''
