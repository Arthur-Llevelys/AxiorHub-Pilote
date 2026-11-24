"""5.6.109 (lot L, F02, C02, C09) : tri du répertoire d'un dossier et dates de fichiers.

Noyau repris du dossier technique 5.6.109 (``reference/tri_repertoire.py``), raccordé à ``fiche5698.arborescence`` : le tri porte sur
l'inventaire COMPLET du répertoire, avant la pagination. Aucun appel réseau, aucune écriture, aucun modèle.

- **Tri** : ``nom`` (ordre naturel, sans accents ni casse : « Pièce 2 » avant « Pièce 10 »), ``modifie`` et ``cree`` (instants comparés
  avec leur heure et leur fuseau, jamais la date affichée), ``taille`` (nombre d'octets, jamais « 20 ko »), ``type`` (extension). Une
  valeur inconnue (date illisible ou absente, taille d'un répertoire, fichier sans extension) est rangée à la fin dans les DEUX sens ; les
  égalités sont départagées par le nom puis le chemin (ordre stable d'une page à l'autre). Les répertoires restent en premier.
- **Réglages** : seuls ``tri=nom|modifie|cree|taille|type`` et ``sens=asc|desc`` sont acceptés ; toute autre valeur revient au tri par
  nom (``reglage``) ou est refusée (``trier`` : ``ValueError('tri_invalide')``).
- **Dates** (C02) : une date de stockage, ISO ou HTTP (« Wed, 14 Oct 2026 10:00:00 GMT »), est convertie AVANT toute mise en forme, dans
  le fuseau du cabinet ; elle n'est jamais coupée à dix caractères (« Wed, 14 Oc »). Une date sans heure (« 2026-10-14 ») est un jour et
  le reste. Illisible : chaîne vide, dite « inconnue » à l'écran, jamais un fragment d'anglais. La valeur d'origine n'est pas modifiée.
"""
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from functools import cmp_to_key
from pathlib import PurePosixPath
import re
import unicodedata

CHAMPS = ('nom', 'modifie', 'cree', 'taille', 'type')
SENS = ('asc', 'desc')
DEFAUT = ('nom', 'asc')
# un clic sur un nouveau champ prend ce sens : A → Z pour un texte, le plus récent ou le plus gros d'abord pour une date ou une taille
SENS_INITIAL = {'nom': 'asc', 'type': 'asc', 'modifie': 'desc', 'cree': 'desc', 'taille': 'desc'}
LIBELLES = {'nom': 'Nom', 'type': 'Type', 'modifie': 'Modifié le', 'cree': 'Créé le', 'taille': 'Taille'}
FUSEAU_DEFAUT = 'Europe/Paris'
JOUR = re.compile(r'\d{4}-\d{2}-\d{2}')


def reglage(tri, sens=None):
    """(champ, sens) validés pour un affichage : valeur inconnue → tri par nom ; sens absent ou inconnu → sens initial du champ."""
    tri = str(tri or '')
    if tri not in CHAMPS:
        return DEFAUT
    sens = str(sens or '')
    return tri, sens if sens in SENS else SENS_INITIAL[tri]


def suivant(champ, tri, sens):
    """Sens obtenu en cliquant l'en-tête ``champ`` quand le tri courant est (tri, sens) : inversé sur le même champ, initial sinon."""
    if champ == tri:
        return 'desc' if sens == 'asc' else 'asc'
    return SENS_INITIAL[champ]


def nom_naturel(valeur):
    """Clé de comparaison d'un nom : sans accents ni casse, suites de chiffres comparées comme des nombres."""
    valeur = ''.join(c for c in unicodedata.normalize('NFKD', str(valeur or '')) if not unicodedata.combining(c)).casefold()
    return tuple((1, int(x)) if x.isdigit() else (0, x) for x in re.split(r'(\d+)', valeur))


def _lue(valeur):
    """``datetime`` d'une date ISO ou HTTP, avec ou sans fuseau selon ce qui est écrit ; absente ou illisible : ``None``."""
    if isinstance(valeur, datetime):
        return valeur
    valeur = str(valeur or '').strip()
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur[:-1] + '+00:00' if valeur[-1:] in 'Zz' else valeur)
    except ValueError:
        try:
            return parsedate_to_datetime(valeur)
        except (ValueError, TypeError, IndexError):
            return None


def moment(valeur):
    """``datetime`` avec fuseau d'une date ISO ou HTTP ; sans fuseau : UTC (les stockages rendent du GMT). Illisible : ``None``."""
    d = _lue(valeur)
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def instant(valeur):
    """Instant (secondes depuis l'époque) d'une date ISO ou HTTP, pour le tri ; ``None`` si elle est absente ou illisible."""
    d = moment(valeur)
    try:
        return d.timestamp() if d is not None else None
    except (OverflowError, OSError, ValueError):
        return None


def _zone(fuseau):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(str(fuseau or FUSEAU_DEFAUT))
    except Exception:   # noqa: BLE001 — fuseau inconnu de la machine : celui du cabinet par défaut, sinon UTC
        try:
            return ZoneInfo(FUSEAU_DEFAUT)
        except Exception:   # noqa: BLE001
            return timezone.utc


def jour_cabinet(valeur, fuseau=None):
    """``AAAA-MM-JJ`` du jour du cabinet pour une date de stockage. Une date sans heure reste ce jour ; une date avec heure (ISO ou
    HTTP) est convertie dans le fuseau du cabinet avant d'être réduite au jour. Illisible ou absente : ``''``."""
    valeur = str(valeur or '').strip()
    if not valeur:
        return ''
    if JOUR.fullmatch(valeur):
        try:
            return date.fromisoformat(valeur).isoformat()
        except ValueError:
            return ''
    d = _lue(valeur)
    if d is None:
        return ''
    try:
        if d.tzinfo is None:                        # heure écrite sans fuseau : le jour écrit, sans conversion inventée
            return d.date().isoformat()
        return d.astimezone(_zone(fuseau)).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ''


def date_fr(valeur, fuseau=None):
    """``JJ/MM/AAAA`` (dix caractères) du jour du cabinet, ou ``''``."""
    jour = jour_cabinet(valeur, fuseau)
    return '%s/%s/%s' % (jour[8:10], jour[5:7], jour[:4]) if jour else ''


def horodatage_fr(valeur, fuseau=None):
    """« JJ/MM/AAAA à HH:MM (fuseau) » pour l'infobulle ; une date sans heure rend le jour seul ; illisible : ``''``."""
    valeur = str(valeur or '').strip()
    if not valeur:
        return ''
    if JOUR.fullmatch(valeur):
        return date_fr(valeur, fuseau)
    d = _lue(valeur)
    if d is None:
        return ''
    if d.tzinfo is None:
        return '%s à %s' % (d.strftime('%d/%m/%Y'), d.strftime('%H:%M'))
    zone = _zone(fuseau)
    try:
        local = d.astimezone(zone)
    except (OverflowError, OSError, ValueError):
        return ''
    return '%s à %s (%s)' % (local.strftime('%d/%m/%Y'), local.strftime('%H:%M'), getattr(zone, 'key', None) or 'UTC')


def type_de(nom, dossier=False):
    """Type d'une entrée pour le tri et l'affichage : extension en minuscules (« pdf », « docx ») ; ``''`` si le fichier n'en a pas."""
    if dossier:
        return ''
    suffixe = PurePosixPath(str(nom or '')).suffix.lstrip('.').casefold()
    return suffixe if re.fullmatch(r'[a-z0-9]{1,12}', suffixe) else ''


def _valeur(ligne, champ):
    if champ == 'nom':
        return nom_naturel(ligne.get('nom', ''))
    if champ in ('modifie', 'cree'):
        return instant(ligne.get(champ))
    if champ == 'taille':
        v = ligne.get('taille')
        return v if type(v) is int and v >= 0 else None
    return str(ligne.get('type') or type_de(ligne.get('nom', ''), bool(ligne.get('dossier')))).casefold() or None


def trier(entrees, champ='nom', sens='asc', dossiers_premier=True):
    """Liste triée (nouvelle liste ; les entrées ne sont pas modifiées). ``entrees`` : dictionnaires ``{'nom', 'chemin', 'dossier',
    'modifie', 'cree', 'taille', 'type'}`` ; les dates gardent leur valeur d'origine complète (heure et fuseau)."""
    if champ not in CHAMPS or sens not in SENS:
        raise ValueError('tri_invalide')
    cles = {}

    def cle(ligne):                                 # valeur de tri et clé de départage calculées une fois par entrée
        k = id(ligne)
        if k not in cles:
            cles[k] = (_valeur(ligne, champ), (nom_naturel(ligne.get('nom', '')), str(ligne.get('chemin', ''))))
        return cles[k]

    def comparer(a, b):
        if dossiers_premier and bool(a.get('dossier')) != bool(b.get('dossier')):
            return -1 if a.get('dossier') else 1
        (x, ka), (y, kb) = cle(a), cle(b)
        if (x is None) != (y is None):
            return 1 if x is None else -1           # inconnu : à la fin dans les deux sens
        c = 0 if x is None else (x > y) - (x < y)
        if c:
            return c if sens == 'asc' else -c
        return (ka > kb) - (ka < kb)                # égalité : nom naturel puis chemin, toujours croissants
    return sorted(entrees, key=cmp_to_key(comparer))
