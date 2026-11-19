"""5.6.108 (lot F) : onglet Fichiers du dossier — « Ajouter un fichier » (glisser-déposer) et « Remplir ce formulaire » pour tout PDF à champs.

**Ajouter un fichier** (``POST /api440/m56108/fichiers-dossier/ajouter``, octets bruts) : mince route au-dessus des contrôles existants du
« Nouveau document » (``mission_web5697._binaire`` : origine, jeton CSRF, taille avant lecture ; ``documents5697.creer`` mode « import » :
droits du dossier, formats admis et signature du contenu, 15 Mo, clé d'idempotence, dépôt exclusif relu, registre commun des livrables).
Elle n'existe que pour répondre le **même 404 neutre** pour un dossier réservé et un dossier absent : le paramètre du dossier s'appelle ``d``
(le contrôle central, qui répond « dossier réservé » par son nom, ne le lit pas) et le service fait lui-même le contrôle (``acces5697``).

**Remplir ce formulaire** (page ``GET /dossier/remplir-pdf?d=…&p=…``, lecture ``GET …/champs``, écriture ``POST …/remplir``) :
- détection des champs AcroForm par la copie embarquée de pypdf : texte (une ou plusieurs lignes), case à cocher, boutons radio, liste
  déroulante ou liste à choix, date quand le formulaire déclare son format (``AFDate_FormatEx``) ; champs en lecture seule montrés, jamais écrits ;
- libellés retrouvés comme pour les formulaires officiels (``formulaires5683`` : infobulle du champ, sinon texte le plus proche à gauche ou
  au-dessus) ; préremplissage **proposé** depuis la fiche du dossier, ses parties et sa procédure (valeur et source affichées, jamais imposée) ;
- la copie remplie est un **nouveau fichier** à côté de l'original (« … (rempli le JJ-MM-AAAA).pdf ») ; l'original n'est jamais modifié ;
  « aplatir » (rendre la copie non modifiable) est une option distincte ;
- refus expliqués : PDF sans champs (« plat »), formulaire XFA sans champs AcroForm, PDF chiffré, PDF signé ou certifié (la modification
  casserait la signature). Un formulaire hybride (XFA + AcroForm) est rempli par ses champs AcroForm ; la partie XFA est retirée de la copie,
  sinon certains lecteurs afficheraient l'ancien contenu (dit à l'écran) ;
- garde contre les fichiers piégés : taille, pages, objets et champs bornés, délai borné (fil d'exécution surveillé), flux de page
  décompressés bornés avant le repérage des libellés, aucune exécution du JavaScript du PDF (pypdf n'en exécute jamais ; sa présence est
  signalée), toute erreur de lecture rendue « PDF illisible ou incohérent ».
- Un seul effet par commande : clé d'idempotence (``pdf_formulaire56108_ops``), réservation du chemin avant le dépôt exclusif, relecture
  et constat au registre commun (``livrables5689``) ; la copie produite est déterministe (même PDF, mêmes valeurs : mêmes octets), un rejeu
  après une réponse perdue retrouve le fichier déjà déposé au lieu d'en créer un second. PDF modifié depuis l'ouverture du formulaire : 409.
- Aucun modèle d'IA n'est appelé : le rapprochement des libellés est fait par mots-clés, ici, sans transmettre le PDF.
"""
from datetime import datetime, timezone
from html import escape as e
from io import BytesIO
import hashlib
import json
import logging
from pathlib import PurePosixPath
import re
import threading
import time
from urllib.parse import urlencode
import zlib

from . import acces5697
from .common import Stop, clean_path, fold, load_matters, under

log = logging.getLogger(__name__)
SERVICE = 'pdf_formulaire56108'
PREFIXE = 'm56108/fichiers-dossier/'
PAGE = '/dossier/remplir-pdf'
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('champs',)
ROUTES_POST = ('ajouter', 'remplir', 'annoter', 'fusionner', 'decouper', 'organiser', 'numeroter', 'caviarder', 'compresser')   # boîte à outils : outils_pdf56108
MAX_PDF = 15_000_000           # comme la lecture des fichiers du stockage (``max_file_bytes``)
MAX_PAGES = 300
MAX_OBJETS = 100_000
MAX_CHAMPS = 800
MAX_FLUX_PAGE = 8_000_000      # contenu d'une page, décompressé, lu pour retrouver les libellés
MAX_TEXTE = 2_000
DELAI = 20.0                   # secondes, analyse ou remplissage
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
# Ff (PDF 32000, 12.7.3 et 12.7.4)
LECTURE_SEULE, MULTILIGNE, RADIO, BOUTON, COMBO, EDITABLE, MULTIPLE = 1, 4096, 32768, 65536, 131072, 262144, 2097152
SCHEMA = '''
CREATE TABLE IF NOT EXISTS pdf_formulaire56108_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, dossier_id TEXT NOT NULL,
  source TEXT NOT NULL, chemin TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}',
  cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
REFUS = {
    'pdf_sans_champs': 'Ce PDF n’a aucun champ à remplir (PDF « plat ») : il se remplit à la main, après impression ou dans un logiciel de PDF. '
                       'Pour un formulaire officiel du catalogue, la page « Formulaires officiels » prépare une fiche des données à recopier.',
    'pdf_xfa': 'Ce formulaire est au format XFA (formulaire dynamique d’Adobe), sans champs ordinaires : AxiorHub ne peut pas le remplir. '
               'Ouvrez-le dans Adobe Acrobat Reader, ou demandez à l’émetteur une version PDF classique.',
    'pdf_chiffre': 'Ce PDF est chiffré ou protégé par un mot de passe : AxiorHub ne le modifie pas. Demandez une version non protégée.',
    'pdf_signe': 'Ce PDF porte une signature électronique (ou une certification) : le remplir casserait la signature. Rien n’a été modifié. '
                 'Remplissez la version vierge, avant signature.',
    'pdf_illisible': 'PDF illisible ou incohérent : il ne peut pas être analysé sans risque. Rien n’a été modifié.',
    'pdf_trop_volumineux': 'PDF trop volumineux pour être rempli ici (15 Mo au plus).',
    'pdf_trop_complexe': 'PDF trop complexe pour être analysé sans risque (trop de pages, d’objets ou de champs). Rien n’a été modifié.',
    'pdf_delai_depasse': 'L’analyse de ce PDF a pris trop de temps : elle a été arrêtée. PDF illisible ou incohérent. Rien n’a été modifié.',
    'pas_un_pdf': 'Ce fichier n’est pas un PDF.',
}
MESSAGES = dict(REFUS, **{
    'introuvable': 'Élément introuvable ou non accessible.', 'route_inconnue': 'Route inconnue.', 'role_insuffisant': 'Action réservée.',
    'origine_ou_csrf_refuse': 'Session expirée : rechargez la page.', 'cle_idempotence_requise': 'Rechargez la page avant de recommencer.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'version_perimee': 'Le PDF a changé depuis l’ouverture du formulaire : rechargez la page avant de recommencer. Rien n’a été écrit.',
    'champ_inconnu': 'Un champ envoyé n’existe pas dans ce PDF : rechargez la page.', 'champ_lecture_seule': 'Un champ en lecture seule ne se modifie pas.',
    'valeur_invalide': 'Une valeur ne convient pas au champ (choix absent de la liste, date mal formée ou texte trop long).',
    'aucune_valeur': 'Aucun champ rempli : rien n’a été enregistré.',
    'aplatissement_impossible': 'La copie ne peut pas être rendue non modifiable pour ce formulaire : enregistrez-la sans cette option.',
    'nom_fichier_indisponible': 'Aucun nom libre pour la copie à côté de l’original : renommez des fichiers, puis recommencez.',
    'json_requis': 'Requête invalide.', 'requete_trop_longue': 'Trop de valeurs envoyées en une fois.',
})
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'cle_reutilisee': '409 Conflict', 'version_perimee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'pdf_trop_volumineux': '413 Payload Too Large', 'stockage_injoignable': '503 Service Unavailable',
           'controle_acces_indisponible': '503 Service Unavailable'}
TYPES = {'texte': 'Texte', 'date': 'Date', 'case': 'Case à cocher', 'radio': 'Choix unique', 'liste': 'Liste'}
# préremplissage proposé (jamais imposé) : clé de valeur, motif sur le libellé replié (sans accents, minuscules)
MOTIFS = (('numero_rg', r'\brg\b|numero de role|repertoire general'),
          ('juridiction', r'juridiction|tribunal|\bcour\b|conseil de prud'),
          ('reference_dossier', r'reference du dossier|\bvotre reference\b|\bnotre reference\b|\breference\b|\bref\b|n[o°] de dossier|numero de dossier'),
          ('adverse_nom', r'adverse|adversaire'),
          ('cabinet_nom', r'\bcabinet\b|\bavocat\b'),
          ('client_nom', r'\bclient\b|\bmandant\b'),
          ('date_du_jour', r'\bfait le\b|date de signature|date du jour'))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def ensure_schema(desk):
    """Démarrage (``Desk``) et première écriture ; sans changement, aucune écriture (recette 5.6.44)."""
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'pdf_formulaire56108_ops' not in existants:
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


# ------------------------------------------------------------------------------------------------ garde : délai borné
def _borne(fn, *args, delai=None):
    """Exécute ``fn`` dans un fil surveillé ; au-delà du délai, ``Stop('pdf_delai_depasse')`` (le fil, sans effet extérieur, s'arrête de
    lui-même au prochain contrôle d'échéance). Toute erreur inattendue de lecture devient ``pdf_illisible``."""
    delai = DELAI if delai is None else delai
    sortie = {}
    echeance = time.monotonic() + delai

    def travail():
        try:
            sortie['ok'] = fn(*args, echeance=echeance)
        except Stop as ex:
            sortie['stop'] = ex
        except RecursionError:
            sortie['stop'] = Stop('pdf_illisible')
        except Exception:   # noqa: BLE001 — pypdf lève des erreurs de toute nature sur un fichier abîmé
            log.info('pdf_formulaire56108 : lecture impossible', exc_info=True)
            sortie['stop'] = Stop('pdf_illisible')
    fil = threading.Thread(target=travail, name='pdf_formulaire56108', daemon=True)
    fil.start()
    fil.join(delai + 1.0)
    if fil.is_alive():
        raise Stop('pdf_delai_depasse')
    if 'stop' in sortie:
        raise sortie['stop']
    return sortie.get('ok')


def _echeance(echeance):
    if echeance is not None and time.monotonic() > echeance:
        raise Stop('pdf_delai_depasse')


# ------------------------------------------------------------------------------------------------ lecture des champs
def _lecteur(raw):
    from ._vendor.pypdf import PdfReader
    if not isinstance(raw, (bytes, bytearray)) or not raw.lstrip()[:5] == b'%PDF-':
        raise Stop('pas_un_pdf')
    if len(raw) > MAX_PDF:
        raise Stop('pdf_trop_volumineux')
    if len(re.findall(rb'\d+\s+\d+\s+obj\b', raw[:MAX_PDF])) > MAX_OBJETS:
        raise Stop('pdf_trop_complexe')
    reader = PdfReader(BytesIO(bytes(raw)), strict=False)
    if reader.is_encrypted:
        raise Stop('pdf_chiffre')
    try:
        taille = int(reader.trailer.get('/Size', 0) or 0)
    except (TypeError, ValueError):
        taille = 0
    if taille > MAX_OBJETS:
        raise Stop('pdf_trop_complexe')
    return reader


def _obj(x):
    return x.get_object() if hasattr(x, 'get_object') and x is not None else x


def _chaine(noeud, cle, defaut=None):
    """Valeur d'un champ, héritée des parents (au plus 20 niveaux, boucles refusées)."""
    vus, n = set(), 0
    while noeud is not None and n < 20:
        ident = id(noeud)
        if ident in vus:
            raise Stop('pdf_illisible')
        vus.add(ident)
        if cle in noeud:
            return _obj(noeud[cle])
        parent = noeud.get('/Parent')
        noeud = _obj(parent) if parent is not None else None
        n += 1
    return defaut


def _nom_complet(noeud):
    parts, vus, n = [], set(), 0
    while noeud is not None and n < 20:
        if id(noeud) in vus:
            raise Stop('pdf_illisible')
        vus.add(id(noeud))
        if noeud.get('/T') is not None:
            parts.append(str(_obj(noeud.get('/T'))))
        parent = noeud.get('/Parent')
        noeud = _obj(parent) if parent is not None else None
        n += 1
    return '.'.join(reversed(parts))


def _champ_du_widget(widget):
    """Le champ terminal d'un widget : lui-même s'il porte un nom, sinon son parent (radio, champ affiché plusieurs fois)."""
    if widget.get('/T') is not None or widget.get('/Parent') is None:
        return widget
    return _obj(widget['/Parent'])


def _etats(widget):
    """Noms des états « actif » d'une case ou d'un bouton radio (clés de /AP /N autres que /Off), tels qu'écrits dans le PDF."""
    ap = _obj(widget.get('/AP'))
    n = _obj(ap.get('/N')) if ap is not None else None
    if not hasattr(n, 'keys'):
        return []
    return [str(k) for k in n.keys() if str(k) != '/Off']


def _js(action):
    action = _obj(action)
    if not hasattr(action, 'get'):
        return ''
    js = _obj(action.get('/JS'))
    if js is None:
        return ''
    if hasattr(js, 'get_data'):
        try:
            data = js.get_data()
        except Exception:   # noqa: BLE001 — flux abîmé : pas de format repéré
            return ''
        return data[:4000].decode('latin-1', 'replace')
    return str(js)[:4000]


FORMAT_DATE = re.compile(r'AFDate_(?:Format|Keystroke)Ex\(\s*["\']([^"\']{2,20})["\']')
JETONS_DATE = re.compile(r'yyyy|yy|mm|m|dd|d')


def format_date(fmt):
    """Format de date pris en charge (jour, mois, année et séparateurs) : rend le format, sinon '' (le champ reste un texte)."""
    reste = JETONS_DATE.sub('', fmt)
    return fmt if fmt and not re.search(r'[A-Za-z]', reste) and 'yy' in fmt and 'm' in fmt and 'd' in fmt else ''


def ecrire_date(iso, fmt):
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', str(iso or ''))
    if not m:
        raise Stop('valeur_invalide')
    a, mo, j = m.groups()
    try:
        datetime(int(a), int(mo), int(j))
    except ValueError:
        raise Stop('valeur_invalide') from None
    valeurs = {'yyyy': a, 'yy': a[2:], 'mm': mo, 'm': str(int(mo)), 'dd': j, 'd': str(int(j))}
    return JETONS_DATE.sub(lambda x: valeurs[x.group(0)], fmt)


def _options(champ):
    out = []
    for x in _obj(_chaine(champ, '/Opt')) or []:
        x = _obj(x)
        if isinstance(x, list) and len(x) >= 2:
            out.append({'valeur': str(_obj(x[0])), 'libelle': str(_obj(x[1]))})
        else:
            out.append({'valeur': str(x), 'libelle': str(x)})
    return out[:500]


def _flux_page_lisible(page):
    """Le contenu de la page peut-il être décompressé sans dépasser la borne ? (repérage des libellés seulement)"""
    contenus = _obj(page.get('/Contents'))
    if contenus is None:
        return True
    flux = [_obj(x) for x in contenus] if isinstance(contenus, list) else [contenus]
    total = 0
    for f in flux:
        brut = getattr(f, '_data', b'') or b''
        filtres = _obj(f.get('/Filter')) if hasattr(f, 'get') else None
        filtres = [str(x) for x in filtres] if isinstance(filtres, list) else ([str(filtres)] if filtres else [])
        if not filtres:
            total += len(brut)
        elif filtres == ['/FlateDecode']:
            d = zlib.decompressobj()
            try:
                sortie = d.decompress(brut, MAX_FLUX_PAGE - total + 1)
            except zlib.error:
                return False
            if d.unconsumed_tail:
                return False
            total += len(sortie)
        else:
            return False
        if total > MAX_FLUX_PAGE:
            return False
    return True


def _textes_page(page, echeance):
    from .formulaires5683 import _nearest   # noqa: F401 — même règle de voisinage que les formulaires officiels
    spans = []
    if not _flux_page_lisible(page):
        return spans, False

    def visite(text, cm, tm, fd, size):
        if text.strip() and len(spans) < 5000:
            spans.append((float(tm[4]), float(tm[5]), re.sub(r'\s+', ' ', text).strip()))
    page.extract_text(visitor_text=visite)
    _echeance(echeance)
    return spans, True


def _analyser(raw, echeance=None):
    from .formulaires5683 import _nearest
    reader = _lecteur(raw)
    racine = reader.trailer['/Root'].get_object()
    if len(reader.pages) > MAX_PAGES:
        raise Stop('pdf_trop_complexe')
    acro = _obj(racine.get('/AcroForm'))
    xfa = bool(acro is not None and acro.get('/XFA') is not None)
    if racine.get('/Perms') is not None or (acro is not None and int(_obj(acro.get('/SigFlags')) or 0) & 1):
        raise Stop('pdf_signe')
    scripts = bool(racine.get('/OpenAction') is not None and 'JavaScript' in str(_obj(racine.get('/OpenAction'))))
    noms = _obj(racine.get('/Names'))
    scripts = scripts or bool(noms is not None and noms.get('/JavaScript') is not None)
    champs, ordre, signatures, libelles_partiels = {}, [], 0, False
    for numero, page in enumerate(reader.pages, 1):
        _echeance(echeance)
        annots = _obj(page.get('/Annots')) or []
        widgets = [w for w in (_obj(a) for a in annots) if hasattr(w, 'get') and w.get('/Subtype') == '/Widget']
        if not widgets:
            continue
        spans = None
        for widget in widgets:
            champ = _champ_du_widget(widget)
            nom = _nom_complet(champ)
            ft = str(_chaine(champ, '/FT') or '')
            if not nom or ft not in ('/Tx', '/Btn', '/Ch', '/Sig'):
                continue
            ff = int(_chaine(champ, '/Ff') or 0)
            if ft == '/Sig':
                if _chaine(champ, '/V') is not None:
                    raise Stop('pdf_signe')
                signatures += 1 if nom not in champs else 0
                champs.setdefault(nom, None)
                continue
            if ft == '/Btn' and ff & BOUTON:
                continue                                      # bouton poussoir : une action, pas une valeur
            if champ.get('/AA') is not None or widget.get('/AA') is not None:
                scripts = True
            entree = champs.get(nom)
            if entree is None and nom in champs:
                continue
            if entree is None:
                if len(ordre) >= MAX_CHAMPS:
                    raise Stop('pdf_trop_complexe')
                rect = [float(x) for x in _obj(widget.get('/Rect')) or (0, 0, 0, 0)][:4]
                if spans is None:
                    spans, complet = _textes_page(page, echeance)
                    libelles_partiels = libelles_partiels or not complet
                infobulle = str(_obj(_chaine(champ, '/TU')) or '').strip()
                voisin = _nearest(spans, rect) if len(rect) == 4 else ''
                entree = {'nom': nom, 'page': numero, 'libelle': re.sub(r'\s+', ' ', infobulle or voisin).strip()[:160],
                          'lecture_seule': bool(ff & LECTURE_SEULE), 'valeur': '', 'aide': ''}
                v = _obj(_chaine(champ, '/V'))
                if ft == '/Tx':
                    fmt = ''
                    for source in (champ, widget):
                        aa = _obj(source.get('/AA'))
                        if hasattr(aa, 'get'):
                            for cle in ('/F', '/K'):
                                trouve = FORMAT_DATE.search(_js(aa.get(cle)))
                                if trouve:
                                    fmt = trouve.group(1)
                    pris = format_date(fmt)
                    entree.update(type='date' if pris else 'texte', format=pris, multiligne=bool(ff & MULTILIGNE),
                                  max=int(_obj(_chaine(champ, '/MaxLen')) or 0) or MAX_TEXTE, valeur=str(v) if v is not None else '')
                    if fmt and not pris:
                        entree['aide'] = 'Format attendu : %s' % fmt
                elif ft == '/Btn':
                    entree.update(type='radio' if ff & RADIO else 'case', etats=[], valeur=str(v)[1:] if v is not None and str(v) != '/Off' else '')
                else:
                    entree.update(type='liste', options=_options(champ), editable=bool(ff & COMBO and ff & EDITABLE), multiple=bool(not ff & COMBO and ff & MULTIPLE),
                                  deroulante=bool(ff & COMBO), valeur=(', '.join(str(x) for x in v) if isinstance(v, list) else str(v)) if v is not None else '')
                champs[nom] = entree
                ordre.append(nom)
            if entree['type'] in ('radio', 'case'):
                for etat in _etats(widget):
                    if etat[1:] not in entree['etats']:
                        entree['etats'].append(etat[1:])
    liste = [champs[n] for n in ordre]
    for c in liste:
        if c['type'] == 'case':
            c['etats'] = c['etats'][:1] or ['Yes']
        if c['type'] == 'radio' and not c['etats']:
            c['lecture_seule'] = True                       # aucun état dessiné : rien ne peut être coché proprement
    if not liste:
        raise Stop('pdf_xfa' if xfa else 'pdf_sans_champs')
    return {'champs': liste, 'pages': len(reader.pages), 'scripts': scripts, 'xfa_hybride': xfa, 'signatures_vides': signatures,
            'libelles_partiels': libelles_partiels, 'lecture_seule': sum(1 for c in liste if c['lecture_seule'])}


def analyser(raw, delai=None):
    """Champs d'un PDF (liste ordonnée) et ce qu'il faut en dire ; ``Stop`` avec un code de ``REFUS`` sinon. Ne modifie rien."""
    out = _borne(_analyser, raw, delai=delai)
    out['empreinte'] = _sha(raw)
    return out


# ------------------------------------------------------------------------------------------------ remplissage
def _valeurs(analyse, valeurs):
    """Valeurs envoyées, contrôlées champ par champ : (nom → valeur prête à écrire). Vide : champ laissé tel quel."""
    if not isinstance(valeurs, dict):
        raise Stop('valeur_invalide')
    champs = {c['nom']: c for c in analyse['champs']}
    out = {}
    for nom, brut in valeurs.items():
        c = champs.get(str(nom))
        if c is None:
            raise Stop('champ_inconnu')
        if c['lecture_seule']:
            if brut not in (None, '', False, [], c['valeur']):
                raise Stop('champ_lecture_seule')
            continue
        t = c['type']
        if t == 'case':
            if brut in (True, 'true', '1', 'on', 'oui', c['etats'][0]):
                out[c['nom']] = c['etats'][0]
            elif brut in (False, None, '', 'false', '0', 'off', 'non'):
                out[c['nom']] = ''
            else:
                raise Stop('valeur_invalide')
            continue
        if t == 'liste' and c.get('multiple'):
            choix = [str(x) for x in (brut if isinstance(brut, list) else ([brut] if brut not in (None, '') else []))]
            if any(x not in [o['valeur'] for o in c['options']] for x in choix):
                raise Stop('valeur_invalide')
            if choix:
                out[c['nom']] = choix
            continue
        if isinstance(brut, (list, dict)):
            raise Stop('valeur_invalide')
        texte = '' if brut is None else str(brut)
        texte = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', texte.replace('\r\n', '\n'))
        if not c.get('multiligne'):
            texte = texte.replace('\n', ' ')
        texte = texte.strip()
        if not texte:
            continue
        if t == 'radio' and texte not in c['etats']:
            raise Stop('valeur_invalide')
        if t == 'liste' and not c.get('editable') and texte not in [o['valeur'] for o in c['options']]:
            raise Stop('valeur_invalide')
        if t == 'date':
            texte = ecrire_date(texte, c['format'])
        if len(texte) > min(int(c.get('max') or MAX_TEXTE), MAX_TEXTE):
            raise Stop('valeur_invalide')
        out[c['nom']] = texte
    return out


def _police(writer):
    """Table de polices du formulaire : Helvetica (/Helv) ajoutée si absente, pour dessiner les valeurs (comme ``formulaires5683``)."""
    from ._vendor.pypdf.generic import DictionaryObject, NameObject, TextStringObject
    acro = writer._root_object['/AcroForm'].get_object()
    dr = _obj(acro.get('/DR'))
    if dr is None:
        dr = DictionaryObject()
        acro[NameObject('/DR')] = dr
    polices = _obj(dr.get('/Font'))
    if polices is None:
        polices = DictionaryObject()
        dr[NameObject('/Font')] = polices
    if '/Helv' not in polices:
        polices[NameObject('/Helv')] = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding')}))
    if acro.get('/DA') is None:
        acro[NameObject('/DA')] = TextStringObject('/Helv 0 Tf 0 g')
    return acro


def _remplir(raw, valeurs, aplatir, echeance=None):
    from ._vendor.pypdf import PdfReader, PdfWriter
    from ._vendor.pypdf.generic import ArrayObject, BooleanObject, NameObject, TextStringObject
    analyse = _analyser(raw, echeance)
    ecrire = _valeurs(analyse, valeurs)
    if not ecrire:
        raise Stop('aucune_valeur')
    types = {c['nom']: c for c in analyse['champs']}
    writer = PdfWriter(clone_from=PdfReader(BytesIO(bytes(raw)), strict=False))
    acro = _police(writer)
    if acro.get('/XFA') is not None:
        del acro['/XFA']                                     # formulaire hybride : les champs AcroForm font foi dans la copie
    ecrits, sans_apparence = set(), set()
    for page in writer.pages:
        _echeance(echeance)
        for annot in _obj(page.get('/Annots')) or []:
            widget = _obj(annot)
            if not hasattr(widget, 'get') or widget.get('/Subtype') != '/Widget':
                continue
            champ = _champ_du_widget(widget)
            nom = _nom_complet(champ)
            if nom not in ecrire:
                continue
            c, v = types[nom], ecrire[nom]
            if c['type'] in ('case', 'radio'):
                etats = {str(k)[1:]: k for k in (_obj(_obj(widget.get('/AP') or {}).get('/N')) or {}).keys()} if widget.get('/AP') is not None else {}
                champ[NameObject('/V')] = NameObject('/' + v) if v else NameObject('/Off')
                widget[NameObject('/AS')] = NameObject(str(etats[v])) if v and v in etats else NameObject('/Off')
            else:
                if isinstance(v, list):
                    champ[NameObject('/V')] = ArrayObject(TextStringObject(x) for x in v)
                else:
                    champ[NameObject('/V')] = TextStringObject(v)
                if '/I' in champ:
                    del champ['/I']
                if '/AP' in widget:
                    del widget['/AP']
                try:
                    writer._update_field_annotation(champ if '/FT' in champ else _chaine_parent_ft(champ), widget)
                except Exception:   # noqa: BLE001 — apparence non dessinée : le lecteur la recalcule (NeedAppearances)
                    sans_apparence.add(nom)
            ecrits.add(nom)
    if aplatir:
        if sans_apparence:
            raise Stop('aplatissement_impossible')
        _aplatir(writer, echeance)
    else:
        writer.set_need_appearances_writer(True)
        writer._root_object['/AcroForm'].get_object()[NameObject('/NeedAppearances')] = BooleanObject(True)
    tampon = BytesIO()
    writer.write(tampon)
    sortie = tampon.getvalue()
    _echeance(echeance)
    _relire(sortie, {n: ecrire[n] for n in ecrits}, aplatir, types)
    return sortie, sorted(ecrits), sorted(c['nom'] for c in analyse['champs'] if c['nom'] not in ecrits and not c['lecture_seule'])


def _chaine_parent_ft(champ):
    """Champ dont le type est porté par un parent : pypdf lit /FT et /Ff sur le dictionnaire passé ; on lui donne une vue complète."""
    from ._vendor.pypdf.generic import DictionaryObject, NameObject
    vue = DictionaryObject(champ)
    for cle in ('/FT', '/Ff', '/Opt'):
        v = _chaine(champ, cle)
        if v is not None and cle not in vue:
            vue[NameObject(cle)] = v
    return vue


def _relire(sortie, ecrits, aplati, types):
    """Preuve avant tout dépôt : la copie se relit, et chaque valeur écrite s'y retrouve (ou, aplatie, il ne reste aucun champ)."""
    from ._vendor.pypdf import PdfReader
    reader = PdfReader(BytesIO(sortie), strict=False)
    champs = reader.get_fields() or {}
    if aplati:
        if any(str(_obj(f.get('/FT'))) in ('/Tx', '/Btn', '/Ch') for f in champs.values()):
            raise Stop('aplatissement_impossible')
        return
    for nom, attendu in ecrits.items():
        f = champs.get(nom)
        lu = _obj(f.get('/V')) if f is not None else None
        if types[nom]['type'] in ('case', 'radio'):
            ok = str(lu) == ('/' + attendu if attendu else '/Off') or (not attendu and lu is None)
        elif isinstance(attendu, list):
            ok = [str(x) for x in (lu or [])] == attendu
        else:
            ok = str(lu) == attendu
        if not ok:
            raise Stop('pdf_illisible')


def _aplatir(writer, echeance=None):
    """Dessine l'apparence de chaque widget dans le contenu de sa page (PDF 32000, 12.5.5), puis retire widgets et formulaire."""
    from ._vendor.pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
    n = 0
    for page in writer.pages:
        _echeance(echeance)
        annots = _obj(page.get('/Annots'))
        if not annots:
            continue
        gardes, ops = ArrayObject(), []
        ressources = _obj(page.get('/Resources'))
        if ressources is None:
            ressources = DictionaryObject()
            page[NameObject('/Resources')] = ressources
        xobjets = _obj(ressources.get('/XObject'))
        if xobjets is None:
            xobjets = DictionaryObject()
            ressources[NameObject('/XObject')] = xobjets
        for ref in annots:
            w = _obj(ref)
            if not hasattr(w, 'get') or w.get('/Subtype') != '/Widget':
                gardes.append(ref)
                continue
            if int(_obj(w.get('/F')) or 0) & 2:              # widget caché : rien à dessiner
                continue
            ap = _obj(w.get('/AP'))
            flux = _obj(ap.get('/N')) if ap is not None else None
            if flux is not None and not hasattr(flux, 'get_data'):          # case ou radio : apparence de l'état choisi
                etat = w.get('/AS')
                flux = _obj(flux.get(etat)) if etat is not None and etat in flux else None
            if flux is None:
                continue
            rect = [float(x) for x in _obj(w.get('/Rect'))]
            bbox = [float(x) for x in _obj(flux.get('/BBox')) or rect]
            mat = [float(x) for x in _obj(flux.get('/Matrix')) or (1, 0, 0, 1, 0, 0)]
            coins = [(x * mat[0] + y * mat[2] + mat[4], x * mat[1] + y * mat[3] + mat[5]) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
            tx0, tx1 = min(c[0] for c in coins), max(c[0] for c in coins)
            ty0, ty1 = min(c[1] for c in coins), max(c[1] for c in coins)
            x0, x1, y0, y1 = min(rect[0], rect[2]), max(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[1], rect[3])
            if tx1 - tx0 <= 0 or ty1 - ty0 <= 0:
                continue
            sx, sy = (x1 - x0) / (tx1 - tx0), (y1 - y0) / (ty1 - ty0)
            n += 1
            nom = '/AxPlat%d' % n
            xobjets[NameObject(nom)] = getattr(flux, 'indirect_reference', None) or writer._add_object(flux)
            ops.append('q %.4f 0 0 %.4f %.4f %.4f cm %s Do Q' % (sx, sy, x0 - tx0 * sx, y0 - ty0 * sy, nom))
        if ops:
            avant, apres = DecodedStreamObject(), DecodedStreamObject()
            avant.set_data(b'q\n')
            apres.set_data(('Q\n' + '\n'.join(ops) + '\n').encode('ascii'))
            contenus = _obj(page.get('/Contents'))
            existants = list(page['/Contents']) if isinstance(contenus, list) else ([page.raw_get('/Contents')] if contenus is not None else [])
            page[NameObject('/Contents')] = ArrayObject([writer._add_object(avant)] + existants + [writer._add_object(apres)])
        if gardes:
            page[NameObject('/Annots')] = gardes
        else:
            del page['/Annots']
    if '/AcroForm' in writer._root_object:
        del writer._root_object['/AcroForm']


def remplir(raw, valeurs, aplatir=False, delai=None):
    """(octets de la copie, champs écrits, champs laissés vides). L'original n'est pas touché ; ``Stop`` pour tout refus."""
    return _borne(_remplir, raw, valeurs, bool(aplatir), delai=delai)


# ------------------------------------------------------------------------------------------------ dossier, fichier, propositions
def _dossier_et_fichier(desk, a, dossier_id, chemin):
    """(dossier, chemin) si l'acteur voit le dossier et que le fichier PDF lui appartient ; sinon le même 404 neutre."""
    from . import documents5697
    dossier_id, chemin = str(dossier_id or '')[:80], str(chemin or '')[:1000]
    if not dossier_id or not chemin:
        raise Stop('introuvable')
    try:
        d = documents5697.dossier(desk, a, dossier_id)          # acces5697.exiger d'abord : réservé et absent donnent la même réponse
        chemin = clean_path(chemin)
    except Stop as ex:
        if str(ex) in ('controle_acces_indisponible', 'stockage_injoignable'):
            raise
        raise Stop('introuvable') from None
    m = next((x for x in load_matters(desk.c, unfiltered=True) if x['id'] == dossier_id), None) or {}
    racines = [d['path']] + [str(p) for p in (m.get('paths') or []) if str(p).strip()]
    if not any(under(chemin, r) for r in racines if r) or PurePosixPath(chemin).suffix.lower() != '.pdf':
        raise Stop('introuvable')
    return d, chemin


def _lire(desk, chemin):
    from .document_projects import _dav
    client = _dav(desk)
    try:
        info = client.stat(chemin)
    except Stop as ex:
        if str(ex) in ('fichier_nextcloud_introuvable', 'http_404', 'chemin_est_un_dossier'):
            raise Stop('introuvable') from None
        raise
    if int(info.get('size') or 0) > MAX_PDF:
        raise Stop('pdf_trop_volumineux')
    try:
        return client, client.download({**info, 'path': chemin})
    except Stop as ex:
        if str(ex) == 'piece_trop_volumineuse':
            raise Stop('pdf_trop_volumineux') from None
        raise


def valeurs_du_dossier(desk, a, dossier_id):
    """Valeurs proposées (jamais imposées) : {clé: (valeur, source)}. Lecture seule ; une source illisible est simplement absente."""
    from .acces5683 import today_of
    out = {}
    m = next((x for x in load_matters(desk.c, unfiltered=True) if x['id'] == dossier_id), None) or {}
    if m.get('client_name'):
        out['client_nom'] = (str(m['client_name']), 'fiche du dossier (client)')
    refs = [str(r) for r in (m.get('references') or []) if str(r).strip()]
    try:
        from . import dossiers5697
        fiche = dossiers5697.lire(desk, a, dossier_id) if _table(desk, 'dossiers5697') else None
    except Stop:
        fiche = None
    if fiche:
        if fiche.get('client') and 'client_nom' not in out:
            out['client_nom'] = (str(fiche['client']), 'fiche du dossier (client)')
        if fiche.get('reference'):
            refs.insert(0, str(fiche['reference']))
    if refs:
        out['reference_dossier'] = (refs[0], 'fiche du dossier (référence)')
    if _table(desk, 'd51_parties'):
        try:
            from . import modele5651
            adverse = next((p for p in modele5651.parties(desk, dossier_id) if not p.get('client')), None)
            if adverse:
                out['adverse_nom'] = (str(adverse['display_name']), 'parties du dossier')
            ref = modele5651.reference_procedure(desk, dossier_id)
            if ref and ref.get('juridiction'):
                out['juridiction'] = (str(ref['juridiction']), 'procédure de référence du dossier')
            if ref and ref.get('rg'):
                out['numero_rg'] = (str(ref['rg']), 'procédure de référence du dossier')
        except Exception:   # noqa: BLE001 — fiche pas encore construite : rien n'est proposé
            log.info('pdf_formulaire56108 : parties illisibles', exc_info=True)
    cabinet = str((desk.c.get('cabinet') or {}).get('name') or '').strip()
    if cabinet:
        out['cabinet_nom'] = (cabinet, 'réglages du cabinet')
    out['date_du_jour'] = (today_of(desk, a.compte)[0].isoformat(), 'date du jour')
    return out


def _table(desk, nom):
    return desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone() is not None


def propositions(champs, valeurs):
    """{nom du champ: {'valeur', 'source', 'cle'}} par mots-clés sur le libellé (et le nom technique) ; chaque proposition reste à accepter."""
    out = {}
    for c in champs:
        if c['lecture_seule'] or c['type'] not in ('texte', 'date'):
            continue
        texte = fold(' '.join((c['libelle'], re.sub(r'[_.]+', ' ', c['nom']))))
        for cle, motif in MOTIFS:
            if not re.search(motif, texte) or cle not in valeurs:
                continue
            if (cle == 'date_du_jour') != (c['type'] == 'date' or bool(re.search(r'\bfait le\b|date', texte))):
                continue
            valeur, source = valeurs[cle]
            if cle == 'date_du_jour':                 # affichée JJ/MM/AAAA ; le script la remet au format du champ de date
                a, mo, j = valeur.split('-')
                valeur = '%s/%s/%s' % (j, mo, a)
            if cle != 'date_du_jour' and c['type'] == 'date':
                continue
            out[c['nom']] = {'valeur': valeur[:int(c.get('max') or MAX_TEXTE)], 'source': source, 'cle': cle}
            break
    return out


def etat_formulaire(desk, a, dossier_id, chemin):
    """Ce que montre la page et ce que rend ``GET …/champs`` : champs, limites, propositions, empreinte. Aucune écriture."""
    d, chemin = _dossier_et_fichier(desk, a, dossier_id, chemin)
    _, raw = _lire(desk, chemin)
    base = {'dossier': d['id'], 'chemin': chemin, 'nom': PurePosixPath(chemin).name}
    try:
        analyse = analyser(raw)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in REFUS:
            return dict(base, etat='refus', code=code, message=REFUS[code])
        raise
    try:
        props = propositions(analyse['champs'], valeurs_du_dossier(desk, a, d['id']))
    except Exception:   # noqa: BLE001 — sans propositions, le formulaire reste utilisable
        log.info('pdf_formulaire56108 : propositions indisponibles', exc_info=True)
        props = {}
    return dict(base, etat='ok', **analyse, propositions=props)


# ------------------------------------------------------------------------------------------------ écriture
def _nom_copie(desk, a, chemin, aplatir):
    from .acces5683 import today_of
    jour = today_of(desk, a.compte)[0]
    stem = PurePosixPath(chemin).stem[:120].rstrip(' .') or 'Formulaire'
    return '%s (rempli le %s%s)' % (stem, jour.strftime('%d-%m-%Y'), ', non modifiable' if aplatir else '')


def _chemin_libre(client, dossier, base, pris=()):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', base).strip(' .')[:150] or 'Copie'
    for n in range(1, 50):
        chemin = clean_path('%s/%s%s.pdf' % (dossier, base, '' if n == 1 else ' (%d)' % n))
        if chemin in pris:
            continue
        try:
            client.stat(chemin)
        except Stop as ex:
            if str(ex) in ('fichier_nextcloud_introuvable', 'http_404'):
                return chemin
            raise
    raise Stop('nom_fichier_indisponible')


def _op(desk, a, cle):
    row = desk.db.execute('SELECT * FROM pdf_formulaire56108_ops WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
    return dict(row) if row else None


def _maj_op(desk, a, cle, **champs):
    champs['maj_le'] = _now()
    desk.db.execute('UPDATE pdf_formulaire56108_ops SET %s WHERE acteur=? AND cle=?' % ', '.join(k + '=?' for k in champs), tuple(champs.values()) + (a.compte, cle))
    desk.db.commit()


def _reserver(desk, a, cle, empreinte, dossier_id, source, chemins, shas):
    """Commande réservée AVANT tout dépôt (chemins et empreintes des sorties). Même clé, autre corps : 409."""
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM pdf_formulaire56108_ops WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
        if row:
            desk.db.rollback()
            if row['empreinte'] != empreinte:
                raise Stop('cle_reutilisee')
            if not json.loads(row['chemin'] or '[]'):
                desk.db.execute('UPDATE pdf_formulaire56108_ops SET chemin=?, sha256=?, maj_le=? WHERE acteur=? AND cle=?',
                                (json.dumps(chemins, ensure_ascii=False), json.dumps(shas), _now(), a.compte, cle))
                desk.db.commit()
            return _op(desk, a, cle)
        now = _now()
        desk.db.execute('INSERT INTO pdf_formulaire56108_ops(acteur,cle,empreinte,dossier_id,source,chemin,sha256,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (a.compte, cle, empreinte, dossier_id, source, json.dumps(chemins, ensure_ascii=False), json.dumps(shas), 'reserve', now, now))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return _op(desk, a, cle)


def _empreinte_corps(operation, corps):
    return _sha(json.dumps({'operation': operation, **corps}, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8'))


def produire(desk, acteur, operation, data, sources, fabrique, libelle, message_fn):
    """Moteur commun des écritures de l'onglet Fichiers (remplir, annoter, fusionner, découper, organiser, numéroter, caviarder, compresser).

    ``sources`` : [(chemin, empreinte attendue)] d'un même dossier ``data['d']`` ; ``fabrique(octets_sources)`` rend la liste des sorties
    ``{'base': nom sans extension, 'octets': …, 'info': {…}}`` (fonction pure : aucun effet). Un seul effet par commande (clé ``data['cle']``) :
    droits relus, empreintes des sources comparées (409 si le PDF a changé), réservation des chemins, dépôt exclusif à côté de la première
    source, relecture et constat au registre commun. Les originaux ne sont jamais réécrits."""
    from . import livrables5689, pause5629
    ensure_schema(desk)
    a = acces5697.acteur_de(acteur)
    cle = str(data.get('cle') or '').strip()
    if not CLE.match(cle):
        raise Stop('cle_idempotence_requise')
    corps = {k: v for k, v in data.items() if k != 'cle'}
    empreinte = _empreinte_corps(operation, corps)
    deja = _op(desk, a, cle)
    if deja and deja['empreinte'] != empreinte:
        raise Stop('cle_reutilisee')
    lues = []
    for chemin, attendue in sources:
        d, chemin = _dossier_et_fichier(desk, a, data.get('d'), chemin)          # droits relus à chaque essai, même rejoué
        lues.append((d, chemin, attendue))
    if not lues:
        raise Stop('introuvable')
    d = lues[0][0]
    if deja and deja['etat'] == 'fait':
        return dict(json.loads(deja['resultat']), rejoue=True)
    client, octets = None, []
    for _, chemin, attendue in lues:
        client, raw = _lire(desk, chemin)
        if _sha(raw) != str(attendue or ''):
            raise Stop('version_perimee')
        octets.append(raw)
    sorties = fabrique(octets)
    if not sorties:
        raise Stop('aucune_sortie')
    dossier_cible = str(PurePosixPath(lues[0][1]).parent)
    connus = json.loads(deja['chemin'] or '[]') if deja else []
    chemins, pris = [], set()
    for i, s in enumerate(sorties):
        if len(connus) == len(sorties):
            chemins.append(connus[i])
            continue
        base = s['base']
        chemin = _chemin_libre(client, dossier_cible, base, pris)
        pris.add(chemin)
        chemins.append(chemin)
    shas = [_sha(s['octets']) for s in sorties]
    op = _reserver(desk, a, cle, empreinte, d['id'], lues[0][1], chemins, shas)
    chemins = json.loads(op['chemin'])
    fichiers, tous_verifies = [], True
    for i, (s, chemin) in enumerate(zip(sorties, chemins)):
        item = livrables5689.reserve(desk, SERVICE, '%s:%s:%d' % (a.compte, cle, i), '1', matter=d['id'], kind=s.get('nature', 'autre_document'),
                                     title=PurePosixPath(chemin).stem, path=chemin, mime='application/pdf', sha256=shas[i], owner=a.compte, label=libelle,
                                     provenance={'producteur': SERVICE, 'operation': operation, 'sources': [x[1] for x in lues],
                                                 'sources_sha256': [_sha(r) for r in octets], 'auteur': a.compte, 'commande': cle, **(s.get('info') or {})},
                                     reserve_note='Copie produite par AxiorHub : à relire avant usage.')
        if not livrables5689.deja_publiee(desk, item, client):
            pause5629.checkpoint(desk)                   # dernier contrôle avant l'effet (sans travail en cours : rien à revalider)
            try:
                client.put_file(chemin, s['octets'], 'application/pdf')
            except Stop as ex:
                if str(ex) not in ('http_412', 'http_409'):   # 412 : dépôt précédent dont la réponse s'est perdue (constaté ci-dessous)
                    _maj_op(desk, a, cle, etat='echec', resultat=json.dumps({'erreur': str(ex)}))
                    raise
        item = livrables5689.constater(desk, item, client, chemin, s['octets'])
        verifie = item.get('production_state') == 'verified'
        tous_verifies = tous_verifies and verifie
        fichiers.append({'chemin': chemin, 'nom': PurePosixPath(chemin).name, 'etat': item.get('production_state', ''), 'motif': item.get('state_reason') or '',
                         'octets': len(s['octets']), **(s.get('info') or {})})
    resultat = {'dossier': d['id'], 'operation': operation, 'fichiers': fichiers, 'etat': 'verified' if tous_verifies else 'a_verifier',
                'chemin': fichiers[0]['chemin'], 'nom': fichiers[0]['nom']}
    resultat['message'] = message_fn(resultat) if tous_verifies else (
        'Dépôt à vérifier : une copie n’a pas pu être relue à l’identique (%s). Les originaux sont inchangés.' % (
            next((f['motif'] for f in fichiers if f['etat'] != 'verified'), '') or 'relecture en attente'))
    _maj_op(desk, a, cle, etat='fait' if tous_verifies else 'a_verifier', resultat=json.dumps(resultat, ensure_ascii=False, default=str))
    desk.audit('pdf_outils56108_' + operation, {'dossier': d['id'], 'sorties': len(fichiers), 'etat': resultat['etat']})
    return resultat


def enregistrer(desk, acteur, data):
    """« Enregistrer la copie remplie » : ``data`` = d, p, empreinte (du PDF lu à l'ouverture), valeurs {champ: valeur}, aplatir, cle."""
    a = acces5697.acteur_de(acteur)
    aplatir = data.get('aplatir') in (True, 'true', '1', 'on')
    valeurs = data.get('valeurs') if isinstance(data.get('valeurs'), dict) else {}
    data = {'d': str(data.get('d') or ''), 'p': str(data.get('p') or ''), 'empreinte': str(data.get('empreinte') or ''), 'valeurs': valeurs,
            'aplatir': aplatir, 'cle': data.get('cle')}

    def fabrique(octets):
        sortie, ecrits, vides = remplir(octets[0], valeurs, aplatir)
        return [{'base': _nom_copie(desk, a, data['p'], aplatir), 'octets': sortie, 'nature': 'formulaire',
                 'info': {'ecrits': len(ecrits), 'vides': len(vides), 'aplati': aplatir}}]

    def message_fn(r):
        f = r['fichiers'][0]
        return ('Copie remplie enregistrée dans le dossier : %s. %d champ(s) rempli(s), %d laissé(s) vide(s). L’original est inchangé. '
                'Relisez la copie avant usage.' % (f['nom'], f['ecrits'], f['vides']))
    r = produire(desk, a, 'remplir', data, [(data['p'], data['empreinte'])], fabrique, 'Formulaire PDF rempli', message_fn)
    f = r['fichiers'][0]
    return dict(r, ecrits=f.get('ecrits'), vides=f.get('vides'), aplati=f.get('aplati'))


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    return route in (ROUTES_GET if method in ('GET', 'HEAD') else ROUTES_POST if method == 'POST' else ())


def _out(valeur, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(valeur, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code):
    from . import outils_pdf56108   # noqa: F401 — messages de la boîte à outils
    if code in MESSAGES:
        return MESSAGES[code]
    from .mission_web5697 import MESSAGES as M5697
    if code in M5697:
        return M5697[code]
    from .erreurs5634 import explain
    return explain(code, None)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        detail = getattr(ex, 'detail', None)
        texte = detail.get('message') if isinstance(detail, dict) and detail.get('message') else message(code)
        return _out({'error': code, 'message': texte}, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if not allowed(role, method, PREFIXE + route):
        raise Stop('route_inconnue')
    a = acces5697.Acteur(owner, role)
    if method in ('GET', 'HEAD'):
        return _out(etat_formulaire(desk, a, args.get('d'), args.get('p')))
    if route == 'ajouter':
        return _out(ajouter(env, desk, auth, a, args))
    from .web440 import check_post
    data = check_post(env, auth)
    if route == 'remplir':
        return _out(enregistrer(desk, a, data))
    from . import outils_pdf56108
    return _out(outils_pdf56108.executer(desk, a, route, data))


def ajouter(env, desk, auth, a, args):
    """« Ajouter un fichier » : mêmes contrôles que l'import du « Nouveau document » ; le dossier est contrôlé AVANT la lecture du corps."""
    from . import documents5697 as docs
    from .mission_web5697 import _binaire
    g = lambda k: str(args.get(k) or '')
    dossier_id = g('d')[:80]
    try:
        docs.dossier(desk, a, dossier_id)                  # droits puis existence du dossier : réservé et absent, même 404 neutre
    except Stop as ex:
        if str(ex) in ('controle_acces_indisponible', 'stockage_injoignable'):
            raise
        raise Stop('introuvable') from None
    octets = _binaire(env, auth)
    nom = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', g('nom')).strip()[:160]
    titre = PurePosixPath(nom).stem.strip()[:160] or 'Fichier ajouté'
    out = dict(docs.creer(desk, a, {'dossier': dossier_id, 'mode': 'import', 'type': 'libre', 'titre': titre, 'nom_fichier': nom, 'octets': octets}, g('cle')))
    out['nom'] = PurePosixPath(out.get('chemin') or nom).name
    if out.get('etat') == 'verified' or str(out.get('message', '')).startswith('Document créé'):
        out['message'] = 'Fichier ajouté au dossier : %s.' % out['nom']
    return out


# ------------------------------------------------------------------------------------------------ page « Remplir ce formulaire »
def lien(prefix, dossier_id, chemin):
    return prefix + PAGE + '?' + urlencode({'d': dossier_id, 'p': chemin})


def _retour(prefix, dossier_id):
    return prefix + '/dossier?' + urlencode({'id': dossier_id, 'vue': 'fichiers'})


def _champ_html(c, prop):
    ident = 'f108-c-' + hashlib.sha1(c['nom'].encode('utf-8')).hexdigest()[:12]
    libelle = c['libelle'] or 'Champ sans libellé'
    attrs = ' data-f108-champ="%s" data-f108-page="%d"' % (e(c['nom'], quote=True), c['page'])
    off = ' disabled' if c['lecture_seule'] else ''
    t = c['type']
    if t == 'case':
        saisie = '<label class="f108-case" for="%s"><input type="checkbox" id="%s"%s%s%s> %s</label>' % (
            ident, ident, attrs, ' checked' if c['valeur'] else '', off, e(libelle))
        titre = ''
    elif t == 'radio':
        saisie = '<fieldset class="f108-radio"%s><legend>%s</legend>%s</fieldset>' % (
            attrs, e(libelle), ''.join('<label><input type="radio" name="%s" value="%s"%s%s> %s</label>' % (
                ident, e(x, quote=True), ' checked' if x == c['valeur'] else '', off, e(x)) for x in c['etats'])
            + ('' if c['lecture_seule'] else '<label><input type="radio" name="%s" value=""%s> Aucun</label>' % (ident, '' if c['valeur'] else ' checked')))
        titre = ''
    elif t == 'liste':
        options = ''.join('<option value="%s"%s>%s</option>' % (e(o['valeur'], quote=True), ' selected' if o['valeur'] in [x.strip() for x in c['valeur'].split(',')] else '',
                                                               e(o['libelle'])) for o in c['options'])
        if c.get('editable'):
            liste = ident + '-l'
            saisie = '<input id="%s" list="%s" value="%s" maxlength="%d"%s%s><datalist id="%s">%s</datalist>' % (
                ident, liste, e(c['valeur'], quote=True), min(c.get('max') or MAX_TEXTE, MAX_TEXTE), attrs, off, liste, options)
        else:
            vide = '' if c.get('multiple') else '<option value="">—</option>'
            saisie = '<select id="%s"%s%s%s>%s%s</select>' % (ident, attrs, ' multiple' if c.get('multiple') else '', off, vide, options)
        titre = '<label for="%s">%s</label>' % (ident, e(libelle))
    elif t == 'date':
        saisie = '<input type="date" id="%s"%s%s data-f108-format="%s">' % (ident, attrs, off, e(c['format'], quote=True))
        titre = '<label for="%s">%s <span class="ax-muted">(écrit au format %s)</span></label>' % (ident, e(libelle), e(c['format']))
    elif c.get('multiligne'):
        saisie = '<textarea id="%s" rows="3" maxlength="%d"%s%s>%s</textarea>' % (ident, min(c['max'], MAX_TEXTE), attrs, off, e(c['valeur']))
        titre = '<label for="%s">%s</label>' % (ident, e(libelle))
    else:
        saisie = '<input id="%s" value="%s" maxlength="%d"%s%s>' % (ident, e(c['valeur'], quote=True), min(c['max'], MAX_TEXTE), attrs, off)
        titre = '<label for="%s">%s</label>' % (ident, e(libelle))
    notes = []
    if c['lecture_seule']:
        notes.append('<span class="f108-note">Lecture seule : ce champ n’est pas modifié.</span>')
    if c.get('aide'):
        notes.append('<span class="f108-note">%s</span>' % e(c['aide']))
    if prop:
        notes.append('<span class="f108-prop">Proposition : <strong data-f108-prop-valeur>%s</strong> <span class="ax-muted">(%s)</span> '
                     '<button type="button" class="ax-btn ghost f108-utiliser" data-f108-utiliser="%s" aria-label="%s">Utiliser</button></span>' % (
                         e(prop['valeur']), e(prop['source']), e(ident, quote=True), e('Utiliser la proposition pour « %s »' % libelle, quote=True)))
    technique = '<span class="f108-technique ax-muted" translate="no">%s · %s · page %d</span>' % (e(c['nom']), e(TYPES.get(t, t)), c['page'])
    return '<div class="f108-champ%s" data-f108-ligne>%s%s%s%s</div>' % (
        ' f108-ls' if c['lecture_seule'] else '', titre, saisie, ''.join(notes), technique)


def page_html(desk, a, prefix, args):
    etat = etat_formulaire(desk, a, args.get('d'), args.get('p'))
    retour = '<p class="f108-retour"><a href="%s">← Fichiers du dossier</a></p>' % e(_retour(prefix, etat['dossier']), quote=True)
    titre = '<h1>Remplir ce formulaire</h1><p class="f108-fichier"><strong>%s</strong></p>' % e(etat['nom'])
    if etat['etat'] == 'refus':
        return ('<div class="f108 f108-page">%s%s<div class="f108-refus" role="status"><p><strong>Ce PDF ne peut pas être rempli ici.</strong></p><p>%s</p>'
                '<p class="ax-muted">L’original n’a pas été modifié.</p></div></div>') % (retour, titre, e(etat['message']))
    limites = []
    if etat['scripts']:
        limites.append('Ce PDF contient des scripts : ils ne sont jamais exécutés ici. Les calculs automatiques du formulaire (totaux, formats) ne sont pas faits : vérifiez ces cases à la relecture.')
    if etat['xfa_hybride']:
        limites.append('Formulaire hybride (XFA et champs ordinaires) : la copie est remplie par ses champs ordinaires et sa partie XFA est retirée, pour que tous les lecteurs affichent les valeurs saisies.')
    if etat['signatures_vides']:
        limites.append('%d champ(s) de signature : ils ne se remplissent pas ici ; signez la copie relue avec votre outil de signature.' % etat['signatures_vides'])
    if etat['lecture_seule']:
        limites.append('%d champ(s) en lecture seule : affichés, jamais modifiés.' % etat['lecture_seule'])
    if etat['libelles_partiels']:
        limites.append('Certains libellés n’ont pas pu être retrouvés sur la page : le nom technique du champ est affiché.')
    props = etat['propositions']
    groupes, page_courante = [], None
    for c in etat['champs']:
        if c['page'] != page_courante:
            page_courante = c['page']
            groupes.append('<h2 class="f108-page-titre">Page %d</h2>' % page_courante)
        groupes.append(_champ_html(c, props.get(c['nom'])))
    tous = ('<p class="f108-props-tous"><button type="button" class="ax-btn ghost" data-f108-tout>Utiliser toutes les propositions (%d)</button> '
            '<span class="ax-muted">Valeurs tirées de la fiche du dossier : à vérifier, rien n’est imposé.</span></p>' % len(props)) if props else ''
    apercu_url = prefix + '/dossier/fichier?' + urlencode({'matter': etat['dossier'], 'path': etat['chemin']})
    formulaire = ('<form class="f108-form" data-f108-remplir data-f108-d="%s" data-f108-p="%s" data-f108-empreinte="%s" data-f108-retour="%s" novalidate>'
                  '%s%s<fieldset class="f108-options"><legend>Enregistrement</legend><p class="ax-muted">La copie remplie est enregistrée comme nouveau fichier, '
                  'à côté de l’original (« %s »). L’original reste intact.</p><label class="f108-case"><input type="checkbox" data-f108-aplatir> '
                  'Rendre la copie non modifiable (aplatir)</label><p class="ax-muted">Option à part : les valeurs sont dessinées dans la page et les champs '
                  'disparaissent. Gardez-la décochée si la copie doit encore être complétée.</p></fieldset>'
                  '<p><button class="ax-btn" type="submit">Enregistrer la copie remplie dans le dossier</button></p>'
                  '<p class="f108-statut" role="status" aria-live="polite" data-f108-statut></p></form>') % (
        e(etat['dossier'], quote=True), e(etat['chemin'], quote=True), e(etat['empreinte'], quote=True), e(_retour(prefix, etat['dossier']) + '&sous=repertoire', quote=True),
        tous, ''.join(groupes), e(_nom_copie(desk, a, etat['chemin'], False) + '.pdf'))
    apercu = ('<section class="f108-apercu" aria-label="Aperçu du PDF"><div class="f108-apercu-corps" data-f108-apercu="%s" data-f108-titre="%s" tabindex="0">'
              '<p class="ax-muted">Chargement de l’aperçu…</p></div></section>') % (e(apercu_url, quote=True), e(etat['nom'], quote=True))
    resume = '<p class="ax-muted">%d champ(s) sur %d page(s). Les valeurs ne sont envoyées à aucune IA.</p>' % (len(etat['champs']), etat['pages'])
    notes = ''.join('<li>%s</li>' % e(x) for x in limites)
    return ('<div class="f108 f108-page">%s%s%s%s<div class="f108-grille">%s%s</div></div>') % (
        retour, titre, resume, ('<ul class="f108-limites">%s</ul>' % notes) if notes else '', formulaire, apercu)


def page_route(env, cfg, auth, prefix, path, args, shell):
    """``GET /dossier/remplir-pdf`` : lecture seule (aucune écriture) ; dossier réservé ou absent, fichier hors dossier : même 404 neutre."""
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    owner, role = actor(env)
    desk = Desk(cfg)
    head = '<script defer src="%s/static/v56108-fichiers.js"></script>' % e(prefix, quote=True)
    try:
        try:
            if role not in ROLES:
                raise Stop('introuvable')
            corps = page_html(desk, acces5697.Acteur(owner, role), prefix, args)
            out = page_out(shell('Remplir ce formulaire', corps, prefix, auth['csrf'], '/dossiers', head))
        except Stop as ex:
            code = str(ex).split(':')[0]
            out = page_out(shell('Remplir ce formulaire', '<div class="f108 f108-page"><p class="notice">%s</p><p><a class="ax-btn ghost" href="%s">Retour aux dossiers</a></p></div>' % (
                e(message(code)), e(prefix + '/dossiers', quote=True)), prefix, auth['csrf'], '/dossiers'))
            out['status'] = STATUTS.get(code, '400 Bad Request')
        out.setdefault('headers', []).append(('Cache-Control', 'private, no-store'))
        return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------------------ onglet Fichiers du dossier
FORMATS = '.docx,.odt,.xlsx,.ods,.pdf,.txt,.md,.csv,.png,.jpg,.jpeg'


def barre_fichiers_html(prefix, dossier_id):
    """Barre de l'onglet Fichiers (nouvelle interface) : « Nouveau document » (même parcours que l'en-tête, dossier prérempli) et
    « Ajouter un fichier » (sélection ou glisser-déposer sur la liste). Les boutons propres à un fichier (lots W et Z) sont montés sur
    chaque ligne de la liste (``dossier5679.MONTAGES_FICHIER108``)."""
    from .mission_web5697 import lien_nouveau_document
    return ('<div class="f108-barre" data-f108-barre data-f108-d="%s"><p class="f108-actions">%s'
            '<label class="ax-btn ghost f108-ajouter" for="f108-fichiers">Ajouter un fichier</label>'
            '<input type="file" id="f108-fichiers" class="sr-only" multiple accept="%s" data-f108-entree>'
            '<button type="button" class="ax-btn ghost" data-f108-selection data-f108-outils-url="%s" disabled>Outils PDF pour la sélection</button></p>'
            '<p class="ax-muted f108-aide">Word, OpenDocument, PDF, tableur, texte ou image, 15 Mo au plus par fichier. Vous pouvez aussi glisser les fichiers sur la liste. '
            'Les fichiers ajoutés sont rangés à la racine du répertoire du dossier : visibles tout de suite dans « Par répertoire », '
            'puis dans « Par type » après leur lecture.</p>'
            '<p class="f108-statut" role="status" aria-live="polite" data-f108-statut></p>'
            '<ul class="f108-envois" data-f108-envois hidden></ul></div>') % (
        e(dossier_id, quote=True), lien_nouveau_document(prefix, dossier_id), e(FORMATS, quote=True), e(prefix + '/dossier/outils-pdf', quote=True))
