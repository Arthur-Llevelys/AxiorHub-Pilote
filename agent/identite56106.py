"""5.6.106 (lot D) : densité d'affichage par personne et identité du cabinet (logo carré et nom) en tête de la barre latérale.

Densité
- Réglage par personne, « compacte » par défaut, « confortable » = présentation de la 5.6.105 (valeurs inchangées). Il est posé sur
  ``<body data-densite>`` à chaque page (``marquer_densite``, appelé par ``ui_helpers.enhance``) ; les règles et variables ``--ax-*`` sont
  dans ``static/v56106-densite.css`` (contrat documenté en tête du fichier pour les autres lots).
- Présentation précédente (interrupteur ``interface56100`` éteint pour le compte) : toujours « confortable », rien ne change pour elle.
- Lecture à l'affichage : base ouverte en lecture seule (aucune écriture pendant un GET, R05), valeur gardée cinq minutes et oubliée dès
  qu'une nouvelle valeur est enregistrée. Une base illisible rend le défaut, jamais une erreur de page.
- Écriture : ``POST /api440/m56106/densite`` (chacun pour son compte), jeton de version (409 si périmé), clé d'idempotence.

Identité du cabinet
- Le nom vient du profil du cabinet (``cabinet_profile5689``, champ ``nom_cabinet``) ; le logo y est ajouté comme champ ``logo`` (empreinte,
  type, dimensions, taille) : chaque dépôt ou retrait crée une révision du profil (même mécanisme, 409 si la révision lue est périmée). Les
  octets sont rangés dans la base du cabinet (table ``identite56106_logos``), jamais dans un dossier servi tel quel.
- Formats : PNG, JPEG, WebP, 1 Mo au plus. Le type est lu dans la signature des octets (jamais l'extension ni le type annoncé) ; les
  dimensions sont lues dans l'en-tête. SVG (scripts possibles), GIF et tout autre contenu sont refusés. Image non carrée : acceptée
  jusqu'au rapport 2:1 et recadrée au centre à l'affichage (``object-fit: cover``) ; au-delà, refusée.
- Dépôt, remplacement et retrait : administrateur seulement (``standalone_auth`` et contrôle du service) ; clé d'idempotence ; audit.
- Servi par ``GET /api440/m56106/identite/logo`` avec le type lu à l'enregistrement, ``X-Content-Type-Options: nosniff`` (en-tête commun) et
  ``Cache-Control: private, no-store`` ; aucun logo : 404 neutre.
- Affichage : en tête de la barre latérale, sur toutes les pages de la nouvelle interface, « [logo] Nom du cabinet » ; sans logo, une
  pastille aux initiales ; sans nom ni logo, la marque AxiorHub d'origine. La marque AxiorHub reste dessous, en petit.
"""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import hmac
from html import escape as e
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from .common import Stop

DENSITES = ('compacte', 'confortable')
DENSITE_DEFAUT = 'compacte'
LIBELLES_DENSITE = {'compacte': 'Compacte (par défaut) : plus d’informations à l’écran',
                    'confortable': 'Confortable : présentation aérée des versions précédentes'}
CLE_DENSITE = 'densite56106:compte:'
PREFIXE = 'm56106/'
ROUTES_GET = ('identite/logo',)
ROUTES_POST = ('identite/logo', 'identite/logo/retirer', 'densite')
ROUTES_ADMIN = ('identite/logo', 'identite/logo/retirer')
ROLES = ('administrateur', 'avocat', 'assistant')
CLE = re.compile(r'^[A-Za-z0-9_-]{8,80}$')
MAX_LOGO = 1_000_000                                # octets décodés
MAX_CORPS_LOGO = 1_450_000                          # corps JSON d'un dépôt (base64 + enveloppe)
COTE_MIN, COTE_MAX, RAPPORT_MAX = 32, 4096, 2.0
TYPES = {'png': 'image/png', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}
GARDE_S = 300
GARDE_IDENTITE_S = 30
SCHEMA = '''
CREATE TABLE IF NOT EXISTS identite56106_logos(sha256 TEXT PRIMARY KEY, type TEXT NOT NULL, largeur INTEGER NOT NULL, hauteur INTEGER NOT NULL,
  octets INTEGER NOT NULL, donnees BLOB NOT NULL, auteur TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identite56106_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  created TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
MESSAGES = {'logo_type_refuse': 'Ce fichier n’est pas une image PNG, JPEG ou WebP. Les images SVG sont refusées : elles peuvent contenir des scripts.',
            'logo_trop_lourd': 'Le logo dépasse 1 Mo : réduisez sa taille, puis recommencez.',
            'logo_dimensions': 'Le logo doit mesurer entre 32 et 4096 pixels de côté.',
            'logo_non_carre': 'Le logo doit être carré ou presque (rapport de 2 pour 1 au plus) : il est recadré au centre à l’affichage.',
            'logo_absent': 'Aucun logo n’est enregistré.', 'introuvable': 'Élément introuvable.',
            'revision_perimee': 'Le profil du cabinet a changé depuis l’ouverture de la page : rechargez-la, puis recommencez.',
            'version_en_conflit': 'Ce réglage a changé depuis l’ouverture de la page : rechargez-la, puis recommencez.',
            'revision_requise': 'La révision lue est nécessaire pour enregistrer.',
            'cle_idempotence_requise': 'Cette action demande une clé d’essai valide.', 'cle_idempotence_deja_utilisee': 'Cette clé a déjà servi à une autre action.',
            'densite_inconnue': 'Cette densité d’affichage n’existe pas.', 'role_insuffisant': 'Seul un administrateur dépose ou retire le logo du cabinet.',
            'route_inconnue': 'Élément introuvable.', 'methode_refusee': 'Méthode refusée.', 'requete_trop_longue': 'Le fichier envoyé est trop lourd (1 Mo au plus).',
            'json_requis': 'Requête invalide.', 'json_invalide': 'Requête invalide.', 'objet_json_requis': 'Requête invalide.',
            'origine_ou_csrf_refuse': 'Requête refusée : rechargez la page, puis recommencez.'}
STATUTS = {'introuvable': '404 Not Found', 'logo_absent': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden',
           'origine_ou_csrf_refuse': '403 Forbidden', 'revision_perimee': '409 Conflict', 'version_en_conflit': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large', 'methode_refusee': '405 Method Not Allowed'}
# Mots qui ne servent pas aux initiales (« Cabinet DURAND Avocats » → « D »).
MOTS_GENERIQUES = {'cabinet', 'avocat', 'avocats', 'avocate', 'avocates', 'selarl', 'selas', 'selafa', 'scp', 'aarpi', 'sas', 'sarl', 'societe', 'société',
                   'd', 'de', 'des', 'du', 'la', 'le', 'les', 'l', 'et', 'en', 'and', 'associes', 'associés', 'associe', 'associé', 'maitre', 'maître', 'me'}

_GARDE = {}
_VERROU = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, à la préparation de la base (``Desk``) seulement, jamais pendant un affichage."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ lecture seule à l'affichage
def _lecture(cfg):
    """Connexion en lecture seule à la base de l'état, ou None (aucune base : installation neuve)."""
    state = str((cfg or {}).get('state_dir') or '')
    db = Path(state) / 'desk.sqlite3' if state else None
    if db is None or not db.is_file():
        return None
    con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True, timeout=0.5)
    con.row_factory = sqlite3.Row
    return con


def _reglage(con, cle):
    row = con.execute('SELECT value FROM settings WHERE key=?', (cle,)).fetchone()
    return json.loads(row[0]) if row else None


def _garde(cle, calcul, duree):
    with _VERROU:
        vu = _GARDE.get(cle)
    if vu and vu[1] > time.monotonic():
        return vu[0]
    valeur = calcul()
    with _VERROU:
        if len(_GARDE) > 500:
            _GARDE.clear()
        _GARDE[cle] = (valeur, time.monotonic() + duree)
    return valeur


def oublier(state_dir=None):
    """Efface les valeurs gardées (toutes, ou celles d'une base) : la page suivante relit le réglage ou l'identité."""
    with _VERROU:
        for cle in [k for k in _GARDE if state_dir is None or k[1] == str(state_dir)]:
            _GARDE.pop(cle, None)


def densite_compte(cfg, owner):
    """Densité choisie par la personne (lecture seule, gardée cinq minutes) ; défaut « compacte »."""
    state = str((cfg or {}).get('state_dir') or '')

    def lire():
        try:
            con = _lecture(cfg)
            if con is None:
                return DENSITE_DEFAUT
            try:
                valeur = _reglage(con, CLE_DENSITE + str(owner or 'cabinet')[:200])
            finally:
                con.close()
        except (sqlite3.Error, ValueError, TypeError):
            return DENSITE_DEFAUT
        return valeur if valeur in DENSITES else DENSITE_DEFAUT
    return _garde(('densite', state, str(owner or 'cabinet')), lire, GARDE_S)


def densite_effective(cfg, owner):
    """Ce qui est posé sur la page : la présentation précédente reste toujours « confortable »."""
    from .shell501 import relire_separe          # même lecture de l'interrupteur interface56100 que le menu (lecture seule)
    if not relire_separe(cfg, owner):
        return 'confortable'
    return densite_compte(cfg, owner)


def marquer_densite(html, cfg=None, owner=None):
    """Pose ``data-densite`` sur ``<body>`` (une seule fois). Toute erreur de lecture rend le défaut ; jamais une page en erreur."""
    if not isinstance(html, str) or re.search(r'<body[^>]*\sdata-densite=', html[:4000]):
        return html
    try:
        if cfg is None:
            from .shell501 import context
            cfg = context() or {}
        if owner is None:
            from .affichage5696 import compte_courant
            owner = compte_courant()
        valeur = densite_effective(cfg, owner)
    except Exception:   # noqa: BLE001 — la densité ne bloque jamais une page
        valeur = DENSITE_DEFAUT
    return re.sub(r'<body(?=[\s>])', '<body data-densite="%s"' % e(valeur, quote=True), html, count=1)


def identite(cfg):
    """Nom du cabinet et métadonnées du logo, lus dans le profil du cabinet (lecture seule, gardés 30 s) : {'nom', 'logo'}."""
    state = str((cfg or {}).get('state_dir') or '')

    def lire():
        vide = {'nom': '', 'logo': None}
        try:
            con = _lecture(cfg)
            if con is None:
                return vide
            try:
                cid = _reglage(con, 'cabinet5689:id')
                data = {}
                if cid:
                    try:
                        row = con.execute('SELECT data_json FROM cabinet_profile5689 WHERE cabinet_id=?', (str(cid),)).fetchone()
                        data = json.loads(row[0]) if row else {}
                    except sqlite3.OperationalError:     # profil pas encore créé
                        data = {}
                nom = str(data.get('nom_cabinet') or '').strip() or str(_reglage(con, 'cabinet5631:cabinet') or '').strip()
                logo = data.get('logo') if isinstance(data.get('logo'), dict) else None
                if logo and not re.fullmatch(r'[0-9a-f]{64}', str(logo.get('sha256') or '')):
                    logo = None
            finally:
                con.close()
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            return vide
        return {'nom': nom[:200], 'logo': logo}
    return _garde(('identite', state), lire, GARDE_IDENTITE_S)


def initiales(nom):
    """Une ou deux lettres pour la pastille : « Cabinet DURAND Avocats » → « D », « MARTIN et BERNARD » → « MB »."""
    mots = [m for m in re.split(r"[\s'’\-&,.]+", str(nom or '')) if m and m.isalpha() is not False and any(c.isalpha() for c in m)]
    utiles = [m for m in mots if m.lower() not in MOTS_GENERIQUES]
    choisis = (utiles or mots)[:2]
    return ''.join(next(c for c in m if c.isalpha()).upper() for m in choisis) or 'C'


def bloc_html(prefix, cfg=None):
    """En-tête de la barre latérale : « [logo] Nom du cabinet » (ou pastille aux initiales), marque AxiorHub dessous en petit.
    Vide (la marque d'origine reste) sans nom ni logo, ou sous la présentation précédente."""
    try:
        if cfg is None:
            from .shell501 import context
            cfg = context() or {}
        from .shell501 import relire_separe
        if not relire_separe(cfg):
            return ''
        ident = identite(cfg)
    except Exception:   # noqa: BLE001 — l'en-tête facultatif ne bloque jamais le menu
        return ''
    nom, logo = ident.get('nom') or '', ident.get('logo')
    if not nom and not logo:
        return ''
    if logo:
        marque = ('<img class="id106-marque" src="%s" width="44" height="44" alt="%s">' % (
            e(prefix + '/api440/m56106/identite/logo?v=' + logo['sha256'][:16], quote=True), '' if nom else 'Logo du cabinet'))
    else:
        marque = '<span class="id106-marque id106-initiales" aria-hidden="true">%s</span>' % e(initiales(nom))
    texte = ('<span class="id106-nom" title="%s">%s</span>' % (e(nom, quote=True), e(nom))) if nom else ''
    return ('<div class="id106" data-id106>%s<span class="id106-texte">%s<span class="id106-axiorhub"><img src="%s/static/axiorhub-icon.png" width="14" height="14" alt="">'
            'AxiorHub Pilote</span></span></div>') % (marque, texte, e(prefix, quote=True))


# ------------------------------------------------------------------------------------------------ logo : contrôle des octets
def _le16(b, i):
    return b[i] | (b[i + 1] << 8)


def _le24(b, i):
    return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16)


def detecter(octets):
    """(sorte, largeur, hauteur) d'après la signature et l'en-tête ; Stop('logo_type_refuse') pour tout autre contenu (SVG compris)."""
    b = bytes(octets or b'')
    if b.startswith(b'\x89PNG\r\n\x1a\n') and len(b) >= 24 and b[12:16] == b'IHDR':
        return 'png', int.from_bytes(b[16:20], 'big'), int.from_bytes(b[20:24], 'big')
    if b.startswith(b'\xff\xd8\xff'):
        i = 2
        while i + 4 <= len(b):
            if b[i] != 0xFF:
                break
            marqueur = b[i + 1]
            if marqueur == 0xFF:                       # octet de remplissage
                i += 1
                continue
            if marqueur in (0xD8, 0x01) or 0xD0 <= marqueur <= 0xD7:
                i += 2
                continue
            if marqueur in (0xD9, 0xDA):               # fin d'image ou début des données sans en-tête de trame : rien de lisible
                break
            longueur = int.from_bytes(b[i + 2:i + 4], 'big')
            if longueur < 2:
                break
            if marqueur in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and i + 9 <= len(b):
                return 'jpeg', int.from_bytes(b[i + 7:i + 9], 'big'), int.from_bytes(b[i + 5:i + 7], 'big')
            i += 2 + longueur
        raise Stop('logo_type_refuse')
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP' and len(b) >= 30:
        morceau, d = b[12:16], 20
        if morceau == b'VP8 ' and b[d + 3:d + 6] == b'\x9d\x01\x2a':
            return 'webp', _le16(b, d + 6) & 0x3FFF, _le16(b, d + 8) & 0x3FFF
        if morceau == b'VP8L' and b[d] == 0x2F:
            b1, b2, b3, b4 = b[d + 1], b[d + 2], b[d + 3], b[d + 4]
            return 'webp', 1 + (((b2 & 0x3F) << 8) | b1), 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
        if morceau == b'VP8X':
            return 'webp', 1 + _le24(b, d + 4), 1 + _le24(b, d + 7)
    raise Stop('logo_type_refuse')


def valider(octets):
    """Contrôles complets d'un logo : taille, signature, dimensions, rapport. Rend {'type', 'largeur', 'hauteur', 'octets', 'sha256'}."""
    if not octets:
        raise Stop('logo_type_refuse')
    if len(octets) > MAX_LOGO:
        raise Stop('logo_trop_lourd')
    sorte, largeur, hauteur = detecter(octets)
    if not (COTE_MIN <= largeur <= COTE_MAX and COTE_MIN <= hauteur <= COTE_MAX):
        raise Stop('logo_dimensions')
    if max(largeur, hauteur) / min(largeur, hauteur) > RAPPORT_MAX:
        raise Stop('logo_non_carre')
    return {'type': TYPES[sorte], 'largeur': largeur, 'hauteur': hauteur, 'octets': len(octets), 'sha256': hashlib.sha256(octets).hexdigest()}


def _decoder(donnees):
    """Contenu base64 (avec ou sans en-tête « data:…;base64, ») ; le type annoncé n'est jamais cru."""
    texte = str(donnees or '')
    if texte.startswith('data:'):
        texte = texte.split(',', 1)[1] if ',' in texte else ''
    if len(texte) > (MAX_LOGO * 4) // 3 + 8:
        raise Stop('logo_trop_lourd')
    try:
        return base64.b64decode(texte, validate=True)
    except (binascii.Error, ValueError):
        raise Stop('logo_type_refuse') from None


# ------------------------------------------------------------------------------------------------ logo : profil du cabinet
def _profil_lu(desk):
    """Ligne du profil sans rien écrire (affichage) : None si le profil n'existe pas encore."""
    cid = desk.settings('cabinet5689:id', '')
    if not cid:
        return None
    try:
        return desk.db.execute('SELECT * FROM cabinet_profile5689 WHERE cabinet_id=?', (str(cid),)).fetchone()
    except sqlite3.OperationalError:
        return None


def _profil(desk, revision):
    """Profil à modifier (action POST) : la révision lue est obligatoire ; « 0 » = profil encore absent à l'affichage, créé ici par la reprise
    des réglages (``cabinet_profile5689``), puis la révision attendue devient la sienne. Toute autre différence : 409."""
    from . import cabinet_profile5689 as profil
    if str(revision if revision is not None else '') == '' or not re.fullmatch(r'\d{1,9}', str(revision)):
        raise Stop('revision_requise')
    profil.ensure_schema(desk)
    avant = _profil_lu(desk)
    row = profil._ligne(desk)
    attendue = int(revision)
    if (avant is None and attendue != 0) or (avant is not None and attendue != row['revision']):
        raise Stop('revision_perimee')
    return profil, row


def _purger(desk, garde):
    """Les octets d'un logo remplacé ou retiré ne sont pas conservés (seule l'image en vigueur reste)."""
    desk.db.execute('DELETE FROM identite56106_logos WHERE sha256<>?', (garde or '',))


def deposer(desk, owner, revision, donnees):
    octets = _decoder(donnees)
    meta = valider(octets)
    profil, row = _profil(desk, revision)
    actuel = json.loads(row['data_json'])
    nouveau = {**actuel, 'logo': {k: meta[k] for k in ('sha256', 'type', 'largeur', 'hauteur', 'octets')}}
    desk.db.execute('INSERT OR IGNORE INTO identite56106_logos VALUES(?,?,?,?,?,?,?,?)', (meta['sha256'], meta['type'], meta['largeur'], meta['hauteur'], meta['octets'],
                                                                                    sqlite3.Binary(octets), str(owner)[:200], _now()))
    if nouveau == actuel:
        desk.db.commit()
        return {'enregistre': True, 'inchange': True, 'revision': row['revision'], 'logo': nouveau['logo'], 'message': 'Ce logo est déjà celui du cabinet.'}
    revision_nouvelle = profil._ecrire(desk, row, nouveau, owner)     # révision conditionnée à celle lue (409 sinon), audit du profil
    _purger(desk, meta['sha256'])
    desk.db.commit()
    desk.audit('identite56106_logo', {'action': 'deposer', 'type': meta['type'], 'largeur': meta['largeur'], 'hauteur': meta['hauteur'], 'octets': meta['octets']})
    oublier(desk.c.get('state_dir', ''))
    carre = meta['largeur'] == meta['hauteur']
    return {'enregistre': True, 'revision': revision_nouvelle, 'logo': nouveau['logo'],
            'message': 'Logo enregistré.' + ('' if carre else ' L’image n’est pas carrée : elle est recadrée au centre à l’affichage.')}


def retirer(desk, owner, revision):
    profil, row = _profil(desk, revision)
    actuel = json.loads(row['data_json'])
    if not actuel.get('logo'):
        raise Stop('logo_absent')
    nouveau = {k: v for k, v in actuel.items() if k != 'logo'}
    revision_nouvelle = profil._ecrire(desk, row, nouveau, owner)
    _purger(desk, '')
    desk.db.commit()
    desk.audit('identite56106_logo', {'action': 'retirer'})
    oublier(desk.c.get('state_dir', ''))
    return {'enregistre': True, 'revision': revision_nouvelle, 'logo': None, 'message': 'Logo retiré : la pastille aux initiales le remplace.'}


def logo_courant(desk):
    """(type, octets) du logo en vigueur, ou Stop('logo_absent') (même réponse neutre, qu'il n'y en ait jamais eu ou qu'il ait été retiré)."""
    cid = desk.settings('cabinet5689:id', '')
    try:
        row = desk.db.execute('SELECT data_json FROM cabinet_profile5689 WHERE cabinet_id=?', (str(cid),)).fetchone() if cid else None
    except sqlite3.OperationalError:
        row = None
    logo = (json.loads(row[0]) if row else {}).get('logo')
    if not isinstance(logo, dict):
        raise Stop('logo_absent')
    try:
        blob = desk.db.execute('SELECT type, donnees FROM identite56106_logos WHERE sha256=?', (str(logo.get('sha256') or ''),)).fetchone()
    except sqlite3.OperationalError:
        blob = None
    if not blob:
        raise Stop('logo_absent')
    octets = bytes(blob[1])
    if hashlib.sha256(octets).hexdigest() != logo.get('sha256'):    # contenu altéré : jamais servi
        raise Stop('logo_absent')
    return TYPES[detecter(octets)[0]], octets                         # le type servi est celui des octets, relu à chaque fois


# ------------------------------------------------------------------------------------------------ densité : écriture
def _jeton_densite(valeur):
    return valeur if valeur in DENSITES else 'defaut'


def regler_densite(desk, owner, choix, attendu):
    if choix not in DENSITES:
        raise Stop('densite_inconnue')
    brut = desk.settings(CLE_DENSITE + str(owner or 'cabinet')[:200], None)
    actuel = brut if brut in DENSITES else None
    if actuel == choix:                                   # même choix renvoyé : aucun effet, aucune écriture
        return {'enregistre': True, 'inchange': True, 'densite': choix, 'message': 'Déjà enregistré.'}
    if str(attendu or '') != _jeton_densite(actuel):      # réglage changé ailleurs depuis l'ouverture de la page
        raise Stop('version_en_conflit')
    desk.setting(CLE_DENSITE + str(owner or 'cabinet')[:200], choix)
    desk.audit('densite56106_reglage', {'densite': choix})
    oublier(desk.c.get('state_dir', ''))
    return {'enregistre': True, 'densite': choix, 'message': 'Densité d’affichage enregistrée.'}


# ------------------------------------------------------------------------------------------------ écrans
def section_compte(desk, owner):
    """Encadré de Mon compte : densité d'affichage de la personne."""
    brut = desk.settings(CLE_DENSITE + str(owner or 'cabinet')[:200], None)
    actuel = brut if brut in DENSITES else None
    choix = actuel or DENSITE_DEFAUT
    radios = ''.join('<label class="i100-choix"><input type="radio" name="densite" value="%s"%s> <span>%s</span></label>' % (
        k, ' checked' if k == choix else '', e(LIBELLES_DENSITE[k])) for k in DENSITES)
    return ('<section class="ax-card" id="d106-densite"><h2>Densité d’affichage</h2>'
            '<p class="ax-muted">Votre choix ne change que votre affichage, sur toutes les pages. La présentation précédente de l’interface reste aérée.</p>'
            '<form class="m5-form" data-api="m56106/densite" data-reload><input type="hidden" name="revision" value="%s">'
            '<input type="hidden" name="cle" value="%s">'
            '<fieldset class="i100-choix-groupe"><legend>Densité d’affichage</legend>%s</fieldset>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></section>') % (
        e(_jeton_densite(actuel), quote=True), secrets.token_hex(12), radios)


def section_html(desk, prefix, role):
    """Paramètres › Démarrage du cabinet : logo carré du cabinet (dépôt, aperçu, remplacement, retrait). Administrateur seulement."""
    if role != 'administrateur':
        return ''
    row = _profil_lu(desk)                     # lecture seule : jamais d'écriture pendant l'affichage (R05)
    data = json.loads(row['data_json']) if row else {}
    nom = str(data.get('nom_cabinet') or '').strip()
    logo = data.get('logo') if isinstance(data.get('logo'), dict) else None
    if logo:
        apercu = ('<img class="id106-marque id106-apercu" src="%s" width="44" height="44" alt="Logo actuel du cabinet">' % e(
            prefix + '/api440/m56106/identite/logo?v=' + str(logo.get('sha256', ''))[:16], quote=True))
        etat = 'Logo actuel : %s, %d × %d pixels, %d ko.' % (e(str(logo.get('type', '')).split('/')[-1].upper()), int(logo.get('largeur') or 0), int(logo.get('hauteur') or 0),
                                                             max(1, round(int(logo.get('octets') or 0) / 1024)))
    else:
        apercu = '<span class="id106-marque id106-initiales id106-apercu" aria-hidden="true">%s</span>' % e(initiales(nom))
        etat = 'Aucun logo : la pastille aux initiales s’affiche.'
    retrait = ('<button type="button" class="ax-btn ghost" data-d106-retirer>Retirer le logo</button>') if logo else ''
    return ('<section class="ax-card" id="d106-logo" data-d106-revision="%d" aria-labelledby="d106-logo-t"><h2 id="d106-logo-t">Logo du cabinet</h2>'
            '<p class="ax-muted">Affiché en haut de la barre latérale, à côté du nom du cabinet (%s), sur toutes les pages. Image carrée PNG, JPEG ou WebP, '
            '1 Mo au plus ; une image presque carrée est recadrée au centre. Les fichiers SVG sont refusés.</p>'
            '<div class="d106-logo-ligne">%s<p class="d106-logo-etat" id="d106-logo-etat">%s</p></div>'
            '<div class="d106-logo-actions"><label class="ax-btn ghost d106-fichier">%s<input type="file" class="d106-champ" accept="image/png,image/jpeg,image/webp" data-d106-fichier></label>%s</div>'
            '<p class="m5-status" role="status" data-d106-statut></p></section>') % (
        row['revision'] if row else 0, e(nom or 'nom à renseigner dans l’étape « Identité du cabinet »'), apercu, etat, 'Remplacer le logo…' if logo else 'Déposer un logo…', retrait)


# ------------------------------------------------------------------------------------------------ routes /api440/m56106/(identite|densite)
def allowed(role, method, api):
    """``standalone_auth.allowed`` : logo réservé à l'administrateur ; densité : chacun pour son compte."""
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else route
    if role not in ROLES:
        return False
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET
    if route in ROUTES_ADMIN:
        return role == 'administrateur'
    return route in ROUTES_POST


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('logo_absent',) and method in ('GET', 'HEAD'):
            code = 'introuvable'
        return _out({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


def _corps(env, auth, limite):
    """Corps JSON borné ; mêmes contrôles d'origine et de jeton CSRF que ``web440.check_post`` (borne propre au dépôt du logo)."""
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


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    me, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):]
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    if method in ('GET', 'HEAD'):
        if route not in ROUTES_GET or not allowed(role, 'GET', route):
            raise Stop('route_inconnue')
        kind, octets = logo_courant(desk)
        return {'status': '200 OK', 'kind': kind, 'body': octets if method == 'GET' else b'',
                'headers': [('Cache-Control', 'private, no-store'), ('Content-Disposition', 'inline')], 'csp': "default-src 'none'; sandbox"}
    if method != 'POST':
        raise Stop('methode_refusee')
    if not allowed(role, 'POST', route):
        raise Stop('role_insuffisant')                     # rôle qui ne peut jamais déposer un logo : contrôle avant tout corps
    data = _corps(env, auth, MAX_CORPS_LOGO if route == 'identite/logo' else 0)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_idempotence_requise')
    empreinte = hashlib.sha256(json.dumps([route, {k: v for k, v in data.items() if k != 'cle'}], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    r = desk.db.execute('SELECT empreinte, reponse FROM identite56106_recus WHERE compte=? AND route=? AND cle=?', (me, route, cle)).fetchone()
    if r:
        if r[0] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')
        return _out(json.loads(r[1]))
    if route == 'densite':
        out = regler_densite(desk, me, str(data.get('densite') or ''), str(data.get('revision') or ''))
    elif route == 'identite/logo':
        out = deposer(desk, me, data.get('revision'), data.get('donnees'))
    else:
        out = retirer(desk, me, data.get('revision'))
    desk.db.execute('DELETE FROM identite56106_recus WHERE created<?', (datetime.fromtimestamp(time.time() - 2 * 86400, timezone.utc).isoformat(timespec='seconds'),))
    desk.db.execute('INSERT OR IGNORE INTO identite56106_recus VALUES(?,?,?,?,?,?)', (me, route, cle, empreinte, json.dumps(out, ensure_ascii=False), _now()))
    desk.db.commit()
    return _out(out)
