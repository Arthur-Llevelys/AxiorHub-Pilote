"""5.6.109 (lot G2, G08) : kit d'audience hors ligne depuis le dossier (« Préparer mon audience »).

Raccordé aux services existants, sans page « Audience » ni copie de travail concurrente :
- **point d'entrée** : bloc « Préparer mon audience » de l'onglet Agenda du dossier, lié à la fiche ``audiences5688`` existante ;
- **instantané référencé** : l'avocat choisit des VERSIONS du registre commun (conclusions, bordereau, pièces, chronologie) ; chaque octet est
  relu et comparé à l'empreinte de sa version (``partage_client56109.octets_version``) ; un élément illisible ou modifié depuis est « manquant »
  et un kit dont un élément obligatoire manque est « incomplet », jamais « complet » ;
- **manifeste protégé** : cabinet, dossier, audience (instantané), créateur, date, version, puis pour chaque élément identifiant + version +
  SHA-256 + taille, couverture, notes de l'audience et dernier état connu ; manifeste ET fichiers sont dans un contenant chiffré authentifié
  (AES-256-GCM, clé dérivée par scrypt d'une phrase secrète que le logiciel ne conserve pas ; ``cryptography`` déjà présent). Toute altération
  du contenant, du manifeste ou d'une pièce est détectée ; aucun repli en clair si la protection est impossible ;
- **hors ligne** : ``ouvrir`` n'utilise ni réseau, ni modèle, ni base ; le contenant est rangé dans la zone privée du poste (jamais dans le
  dossier ni dans Nextcloud) et peut être emporté chiffré ; il ne remplace aucun fichier du cabinet ;
- **reconnexion** : droits relus (un dossier devenu réservé bloque toute publication), versions nouvelles signalées, notes prises après
  l'audience gardées localement avec auteur, date et source ; une note dont la base a changé au cabinet est un CONFLIT conservé côte à côte,
  jamais un écrasement ; publier ajoute la note à la fiche d'audience (``audiences5688.ajouter_note``) par une décision explicite.
Limite dite à l'écran : une copie déjà déchiffrée ne peut pas être retirée à distance ; l'expiration ferme seulement les ouvertures futures.
Le vrai poste déconnecté (réseau coupé physiquement) n'est pas qualifié par les essais automatiques.
"""
import base64
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from html import escape as e
import io
import json
import os
from pathlib import Path
import sqlite3
import uuid
import zipfile

from .common import Stop
from . import partage_client56109 as P

PREFIXE = 'm56109/kit/'
FORMAT = 'axh-kit-audience/1'
MAGIC = b'AXHKIT1\n'
GENRES = {'conclusions': 'Conclusions', 'bordereau': 'Bordereau', 'piece': 'Pièce', 'chronologie': 'Chronologie', 'autre': 'Autre document'}
ETATS = {'pret': 'Prêt (complet)', 'incomplet': 'Incomplet : élément obligatoire manquant', 'expire': 'Expiré'}
ETATS_NOTE = {'locale': 'Locale (non rapprochée)', 'a_publier': 'À publier', 'conflit': 'Conflit : note du cabinet modifiée depuis le kit',
              'publiee': 'Publiée dans la fiche d’audience', 'bloquee': 'Bloquée : droits retirés'}
LIMITE = ('Une copie déjà déchiffrée ne peut pas être retirée à distance : l’expiration et la révocation ferment seulement les ouvertures futures.')
MIN_PHRASE, MAX_ELEMENTS, MAX_JOURS = 12, 60, 90
SCRYPT = {'n': 2 ** 15, 'r': 8, 'p': 1}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS kits_audience56109(id TEXT PRIMARY KEY, cabinet TEXT NOT NULL, matter_id TEXT NOT NULL, audience_id TEXT NOT NULL,
  cree_par TEXT NOT NULL, cree_le TEXT NOT NULL, manifeste_sha256 TEXT NOT NULL CHECK(length(manifeste_sha256)=64), conteneur_sha256 TEXT NOT NULL,
  protection TEXT NOT NULL, fichier TEXT NOT NULL, taille INTEGER NOT NULL, etat TEXT NOT NULL CHECK(etat IN ('pret','incomplet')),
  expire_le TEXT NOT NULL, couverture TEXT NOT NULL, notes_sha256 TEXT NOT NULL DEFAULT '', audience_version INTEGER NOT NULL DEFAULT 0,
  derniere_reconnexion TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS kits_audience56109_dossier ON kits_audience56109(matter_id, cree_le);
CREATE TABLE IF NOT EXISTS kits_audience_elements56109(kit_id TEXT NOT NULL, rang INTEGER NOT NULL, genre TEXT NOT NULL, titre TEXT NOT NULL,
  document_id TEXT NOT NULL, version_id TEXT NOT NULL, sha256 TEXT NOT NULL, taille INTEGER NOT NULL DEFAULT 0, obligatoire INTEGER NOT NULL DEFAULT 0,
  present INTEGER NOT NULL DEFAULT 0, motif TEXT NOT NULL DEFAULT '', PRIMARY KEY(kit_id, rang));
CREATE TABLE IF NOT EXISTS kits_audience_notes56109(id TEXT PRIMARY KEY, kit_id TEXT NOT NULL, auteur TEXT NOT NULL, cree_le TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'kit_hors_ligne', texte TEXT NOT NULL, sha256 TEXT NOT NULL, base_sha256 TEXT NOT NULL,
  etat TEXT NOT NULL CHECK(etat IN ('locale','a_publier','conflit','publiee','bloquee')), decide_par TEXT NOT NULL DEFAULT '',
  decide_le TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS kits_audience_ops56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
TABLES = ('kits_audience56109', 'kits_audience_elements56109', 'kits_audience_notes56109', 'kits_audience_ops56109')
MESSAGES = {
    'introuvable': 'Kit introuvable, ou action non permise.',
    'audience_requise': 'Choisissez l’audience du dossier.',
    'elements_requis': 'Choisissez au moins un document.',
    'phrase_trop_courte': 'Phrase secrète trop courte (12 caractères au moins) : le kit n’est pas créé, aucune copie en clair.',
    'protection_indisponible': 'Protection du kit indisponible sur ce poste : le kit n’est pas créé (aucune copie en clair).',
    'kit_altere': 'Kit altéré ou phrase secrète incorrecte : rien n’est affiché.',
    'piece_alteree': 'Une pièce du kit ne correspond plus au manifeste : le kit est considéré comme altéré.',
    'kit_expire': 'Kit expiré : les ouvertures futures sont fermées. ' + LIMITE,
    'expiration_invalide': 'Date d’expiration invalide (90 jours au plus).',
    'note_vide': 'La note est vide.',
    'droits_retires': 'Vos droits sur ce dossier ont changé : rien n’est publié ; la note reste conservée sur ce poste.',
    'note_non_publiable': 'Rapprochez d’abord le kit (reconnexion) avant de publier cette note.',
    'audience_modifiee_entre_temps': 'La fiche d’audience a changé pendant la publication : relancez le rapprochement.',
}


# ------------------------------------------------------------------------------------------------ contenant protégé (aucune base, aucun réseau)
def _cle_phrase(phrase, sel, params=SCRYPT):
    try:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise Stop('protection_indisponible') from None
    return Scrypt(salt=sel, length=32, n=params['n'], r=params['r'], p=params['p']).derive(phrase.encode('utf-8'))


def sceller(contenu, phrase, entete):
    """Contenant chiffré authentifié : ``MAGIC`` + en-tête JSON (une ligne, données associées) + AES-256-GCM(clé scrypt, contenu)."""
    if len(str(phrase or '')) < MIN_PHRASE:
        raise Stop('phrase_trop_courte')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise Stop('protection_indisponible') from None
    sel, nonce = os.urandom(16), os.urandom(12)
    tete = dict(entete, kdf={'algo': 'scrypt', **SCRYPT, 'sel': base64.b64encode(sel).decode()}, aead='AES-256-GCM', nonce=base64.b64encode(nonce).decode())
    ligne = json.dumps(tete, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'
    return MAGIC + ligne + AESGCM(_cle_phrase(phrase, sel)).encrypt(nonce, contenu, MAGIC + ligne)


def desceller(blob, phrase):
    """Rend ``(en-tête, contenu)`` ; toute altération (en-tête, données, étiquette) ou phrase fausse : ``kit_altere`` (rien n'est rendu)."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise Stop('protection_indisponible') from None
    if not blob.startswith(MAGIC) or b'\n' not in blob[len(MAGIC):]:
        raise Stop('kit_altere')
    ligne, chiffre = blob[len(MAGIC):].split(b'\n', 1)
    try:
        tete = json.loads(ligne.decode('utf-8'))
        kdf = tete['kdf']
        if kdf.get('algo') != 'scrypt' or tete.get('aead') != 'AES-256-GCM' or not (2 ** 14 <= int(kdf['n']) <= 2 ** 20):
            raise ValueError
        sel, nonce = base64.b64decode(kdf['sel']), base64.b64decode(tete['nonce'])
        cle = _cle_phrase(str(phrase or ''), sel, {'n': int(kdf['n']), 'r': int(kdf['r']), 'p': int(kdf['p'])})
        contenu = AESGCM(cle).decrypt(nonce, chiffre, MAGIC + ligne + b'\n')
    except (ValueError, KeyError, TypeError, InvalidTag, UnicodeError):
        raise Stop('kit_altere') from None
    return tete, contenu


def ouvrir(blob, phrase, quand=None):
    """Ouverture HORS LIGNE : aucune base, aucun réseau, aucun modèle. Contenant authentifié, manifeste lu DANS le contenant, chaque pièce
    recomparée (empreinte et taille) ; expiration lue dans le manifeste protégé. Rend ``{manifeste, pieces: {rang: octets}, complet, manquants}``."""
    tete, contenu = desceller(blob, phrase)
    try:
        z = zipfile.ZipFile(io.BytesIO(contenu))
        manifeste = json.loads(z.read('manifeste.json').decode('utf-8'))
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeError):
        raise Stop('kit_altere') from None
    if manifeste.get('format') != FORMAT or manifeste.get('kit_id') != tete.get('kit_id'):
        raise Stop('kit_altere')
    if hashlib.sha256(P.canon(manifeste).encode('utf-8')).hexdigest() != tete.get('manifeste_sha256'):
        raise Stop('kit_altere')
    if (quand or P.maintenant()) >= manifeste['expire_le']:
        raise Stop('kit_expire')
    pieces = {}
    for el in manifeste['elements']:
        if not el['present']:
            continue
        try:
            raw = z.read(el['fichier'])
        except KeyError:
            raise Stop('piece_alteree') from None
        if hashlib.sha256(raw).hexdigest() != el['sha256'] or len(raw) != el['taille']:
            raise Stop('piece_alteree')
        pieces[el['rang']] = raw
    manquants = [el for el in manifeste['elements'] if not el['present']]
    return {'manifeste': manifeste, 'pieces': pieces, 'complet': not any(el['obligatoire'] for el in manquants), 'manquants': manquants}


# ------------------------------------------------------------------------------------------------ préparer
def _zone(desk):
    p = Path(desk.c['state_dir']) / 'kits56109'
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _expiration(desk, valeur):
    jour = P.jour_cabinet(desk)
    try:
        fin = date.fromisoformat(str(valeur)[:10]) if valeur else jour + timedelta(days=7)
    except ValueError:
        raise Stop('expiration_invalide') from None
    if fin < jour or fin > jour + timedelta(days=MAX_JOURS):
        raise Stop('expiration_invalide')
    return datetime.combine(fin + timedelta(days=1), time.min, P.tz(desk)).astimezone(timezone.utc).isoformat(timespec='seconds')


def _choix(data):
    out, vus = [], set()
    for cle, obligatoire in (('obligatoires', True), ('complementaires', False)):
        for d, v in P._paires(data.get(cle)):
            if (d, v) not in vus:
                vus.add((d, v))
                out.append((d, v, obligatoire))
    genres = data.get('genres') if isinstance(data.get('genres'), dict) else {}
    if len(out) > MAX_ELEMENTS:
        raise Stop('trop_de_ressources')
    return [(d, v, o, str(genres.get(d) or '')) for d, v, o in out]


def _genre(titre, kind, donne):
    if donne in GENRES:
        return donne
    t = (titre or '').lower()
    if kind == 'conclusions' or 'conclusion' in t:
        return 'conclusions'
    if 'bordereau' in t:
        return 'bordereau'
    if 'chronolog' in t:
        return 'chronologie'
    return 'piece'


def preparer(desk, acteur_, mid, data, cle, client=None):
    """« Préparer mon audience » : sélection de versions exactes liée à l'audience, contenant scellé dans la zone privée du poste. La phrase
    secrète n'est jamais conservée ni journalisée. Trois rôles du cabinet (lecture du dossier exigée)."""
    from . import audiences5688 as au, pause5629
    a = P.acteur(acteur_)
    if a.role not in P.ROLES:
        raise Stop('role_insuffisant')
    m = P.dossier(desk, a, mid)
    phrase = str(data.get('phrase') or '')
    if len(phrase) < MIN_PHRASE:
        raise Stop('phrase_trop_courte')
    try:
        __import__('cryptography.hazmat.primitives.ciphers.aead')
    except ImportError:
        raise Stop('protection_indisponible') from None
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiences5688'").fetchone():
        raise Stop('audience_requise')                               # H1 : schéma créé au démarrage ; jamais de DDL pendant une lecture
    h = desk.db.execute(au.SELECT + ' WHERE h.id=? AND h.matter=?', (str(data.get('audience') or ''), m['id'])).fetchone()
    if not h:
        raise Stop('audience_requise')
    h = au.view(desk, h)
    choix = _choix(data)
    if not choix:
        raise Stop('elements_requis')
    expire = _expiration(desk, data.get('expiration'))
    parametres = {'mid': m['id'], 'h': h['id'], 'choix': choix, 'exp': expire}          # jamais la phrase secrète

    def faire():
        kid = 'kit-' + uuid.uuid4().hex[:24]
        elements, fichiers = [], []
        for rang, (d, v, obligatoire, genre) in enumerate(choix, 1):
            el = {'rang': rang, 'document_id': d, 'version_id': v, 'obligatoire': obligatoire, 'present': False, 'sha256': '', 'taille': 0, 'titre': d,
                  'genre': genre or 'piece', 'fichier': '', 'motif': ''}
            try:
                ver = P.version_registre(desk, a, m['id'], d, v, approuvee=False)
                el.update(titre=ver['titre'], sha256=ver['sha256'], genre=_genre(ver['titre'], ver['row']['kind'], genre))
                pause5629.checkpoint(desk)
                raw, nom = P.octets_version(desk, ver['row'], client, ver['sha256'])
                el.update(present=True, taille=len(raw), fichier='pieces/%02d %s' % (rang, nom))
                fichiers.append((el['fichier'], raw))
            except Stop as ex:
                el['motif'] = str(ex).split(':')[0]           # nommé dans le manifeste : jamais « complet » s'il est obligatoire
            elements.append(el)
        notes = str(h.get('notes') or '')
        manifeste = {'format': FORMAT, 'kit_id': kid, 'cabinet': P.cabinet(desk), 'dossier': {'id': m['id'], 'reference': (m.get('references') or [m['id']])[0]},
                     'audience': {'id': h['id'], 'starts': h['starts'], 'ends': h['ends'], 'location': h['location'], 'title': h['title'],
                                  'juridiction': h['juridiction'], 'chambre': h['chambre'], 'rg': h['rg'], 'type': h['type_label'], 'avocat': h['avocat_nom'],
                                  'version': h['version']},
                     'cree_par': a.compte, 'cree_le': P.maintenant(), 'version': 1, 'expire_le': expire, 'derniere_synchronisation': P.maintenant(),
                     'elements': elements, 'notes': {'texte': notes, 'sha256': hashlib.sha256(notes.encode('utf-8')).hexdigest()},
                     'couverture': {'choisis': len(elements), 'presents': sum(1 for x in elements if x['present']),
                                    'obligatoires_manquants': [x['rang'] for x in elements if x['obligatoire'] and not x['present']]},
                     'avertissements': [LIMITE]}
        msha = hashlib.sha256(P.canon(manifeste).encode('utf-8')).hexdigest()
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifeste.json', json.dumps(manifeste, ensure_ascii=False))
            for nom, raw in fichiers:
                z.writestr(nom, raw)
        blob = sceller(tampon.getvalue(), phrase, {'format': FORMAT, 'kit_id': kid, 'manifeste_sha256': msha})
        ouvrir(blob, phrase)                                   # relecture complète avant d'annoncer le kit : jamais un contenant non vérifié
        fichier = kid + '.axhkit'
        P.ecrire_exclusif(_zone(desk) / fichier, blob)
        etat = 'incomplet' if manifeste['couverture']['obligatoires_manquants'] else 'pret'
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            desk.db.execute('INSERT INTO kits_audience56109(id,cabinet,matter_id,audience_id,cree_par,cree_le,manifeste_sha256,conteneur_sha256,protection,fichier,taille,'
                            'etat,expire_le,couverture,notes_sha256,audience_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (kid, manifeste['cabinet'], m['id'], h['id'], a.compte, manifeste['cree_le'], msha, hashlib.sha256(blob).hexdigest(),
                             'AES-256-GCM + scrypt (phrase secrète)', fichier, len(blob), etat, expire, P.canon(manifeste['couverture']),
                             manifeste['notes']['sha256'], int(h['version'] or 0)))
            for el in elements:
                desk.db.execute('INSERT INTO kits_audience_elements56109(kit_id,rang,genre,titre,document_id,version_id,sha256,taille,obligatoire,present,motif) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (kid, el['rang'], el['genre'], el['titre'][:300], el['document_id'], el['version_id'], el['sha256'],
                                                                  el['taille'], int(el['obligatoire']), int(el['present']), el['motif']))
            desk.db.commit()
        except BaseException:
            desk.db.rollback()
            raise
        desk.audit('kit56109_prepare', {'kit': kid, 'matter': m['id'], 'audience': h['id'], 'etat': etat, 'manifeste_sha256': msha, 'par': a.compte})
        return {'id': kid}

    out = P.executer(desk, 'kits_audience_ops56109', a.compte, cle, 'preparer', parametres, faire)
    return dict(vue(desk, a, out['id']), rejoue=out['rejoue'])


# ------------------------------------------------------------------------------------------------ ouvrir sur le poste, notes, reconnexion
def _kit(desk, a, kid):
    if not P.IDENT.fullmatch(str(kid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM kits_audience56109 WHERE id=?', (str(kid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    P.dossier(desk, a, row['matter_id'])
    return dict(row)


def conteneur(desk, a, kid):
    row = _kit(desk, a, kid)
    blob = (_zone(desk) / row['fichier']).read_bytes()
    if hashlib.sha256(blob).hexdigest() != row['conteneur_sha256']:
        raise Stop('kit_altere')                               # contenant remplacé sur le poste (même scellé avec la bonne phrase)
    return row, blob


def ouvrir_kit(desk, acteur_, kid, phrase, quand=None):
    """Ouverture sur le poste : aucune requête distante ni modèle ; le manifeste lu DANS le contenant doit être celui enregistré."""
    a = P.acteur(acteur_)
    row, blob = conteneur(desk, a, kid)
    k = ouvrir(blob, phrase, quand)
    if hashlib.sha256(P.canon(k['manifeste']).encode('utf-8')).hexdigest() != row['manifeste_sha256']:
        raise Stop('kit_altere')
    return {'id': row['id'], 'instantane_du': k['manifeste']['cree_le'], 'complet': k['complet'], 'manquants': k['manquants'],
            'elements': [{x: el[x] for x in ('rang', 'genre', 'titre', 'document_id', 'version_id', 'sha256', 'taille', 'obligatoire', 'present')}
                         for el in k['manifeste']['elements']], 'audience': k['manifeste']['audience'], 'notes': k['manifeste']['notes'], 'limite': LIMITE}


def lire_piece(desk, acteur_, kid, phrase, rang, quand=None):
    a = P.acteur(acteur_)
    row, blob = conteneur(desk, a, kid)
    k = ouvrir(blob, phrase, quand)
    el = next((x for x in k['manifeste']['elements'] if x['rang'] == rang and x['present']), None)
    if not el:
        raise Stop('introuvable')
    return k['pieces'][rang], el['fichier'].split('/', 1)[1]


def ajouter_note(desk, acteur_, kid, data):
    """Note prise après l'audience, d'abord LOCALE (auteur, date, source « kit hors ligne », base = notes de l'audience figées dans le kit)."""
    a = P.acteur(acteur_)
    row = _kit(desk, a, kid)
    texte = P.texte(data.get('texte'), 10000)
    if not texte:
        raise Stop('note_vide')
    nid = 'kn-' + uuid.uuid4().hex[:20]
    desk.db.execute("INSERT INTO kits_audience_notes56109(id,kit_id,auteur,cree_le,texte,sha256,base_sha256,etat) VALUES(?,?,?,?,?,?,?,'locale')",
                    (nid, row['id'], a.compte, P.maintenant(), texte, hashlib.sha256(texte.encode('utf-8')).hexdigest(), row['notes_sha256']))
    desk.db.commit()
    return dict(vue(desk, a, row['id']), note=nid, message='Note conservée sur ce poste : elle sera rapprochée à la reconnexion.')


def reconnecter(desk, acteur_, kid, data=None):
    """Retour du réseau : droits RELUS (dossier réservé entre-temps : publication bloquée, notes gardées), versions nouvelles signalées,
    notes rapprochées : base inchangée → « à publier » ; notes du cabinet modifiées → « conflit » (les deux textes restent)."""
    from . import acces5697, audiences5688 as au
    a = P.acteur(acteur_)
    if not P.IDENT.fullmatch(str(kid or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM kits_audience56109 WHERE id=?', (str(kid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    try:
        acces5697.exiger(desk, a, dossier_id=row['matter_id'])
        droits = True
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            raise
        droits = False
    quand = P.maintenant()
    if not droits:
        desk.db.execute("UPDATE kits_audience_notes56109 SET etat='bloquee', version=version+1 WHERE kit_id=? AND etat IN ('locale','a_publier','conflit')", (row['id'],))
        desk.db.execute('UPDATE kits_audience56109 SET derniere_reconnexion=? WHERE id=?', (quand, row['id']))
        desk.db.commit()
        raise Stop('droits_retires')
    h = desk.db.execute('SELECT notes, version FROM audiences5688 WHERE id=?', (row['audience_id'],)).fetchone()
    actuelles = hashlib.sha256(str(h['notes'] if h else '').encode('utf-8')).hexdigest()
    for n in desk.db.execute("SELECT * FROM kits_audience_notes56109 WHERE kit_id=? AND etat IN ('locale','a_publier','conflit','bloquee')", (row['id'],)).fetchall():
        etat = 'a_publier' if n['base_sha256'] == actuelles else 'conflit'
        desk.db.execute('UPDATE kits_audience_notes56109 SET etat=?, version=version+1 WHERE id=?', (etat, n['id']))
    desk.db.execute('UPDATE kits_audience56109 SET derniere_reconnexion=? WHERE id=?', (quand, row['id']))
    desk.db.commit()
    desk.audit('kit56109_reconnexion', {'kit': row['id'], 'matter': row['matter_id'], 'par': a.compte})
    return vue(desk, a, row['id'])


def publier_note(desk, acteur_, kid, nid, data):
    """Décision explicite : la note est AJOUTÉE à la fiche d'audience (jamais en remplacement des notes existantes) ; droits relus ; une note
    en conflit est publiée comme note concurrente, signalée telle quelle."""
    from . import acces5697, audiences5688 as au
    a = P.acteur(acteur_)
    row = _kit(desk, a, kid)
    n = desk.db.execute('SELECT * FROM kits_audience_notes56109 WHERE id=? AND kit_id=?', (str(nid or ''), row['id'])).fetchone()
    if not n:
        raise Stop('introuvable')
    P._version(n, data.get('version'))
    if n['etat'] not in ('a_publier', 'conflit'):
        raise Stop('note_non_publiable')
    acces5697.exiger(desk, a, dossier_id=row['matter_id'])
    prefixe = 'Note concurrente prise hors ligne (kit du %s)' % row['cree_le'][:10] if n['etat'] == 'conflit' else 'Note prise hors ligne (kit du %s)' % row['cree_le'][:10]
    au.ajouter_note(desk, row['audience_id'], '%s : %s' % (prefixe, n['texte']), a.compte)
    desk.db.execute("UPDATE kits_audience_notes56109 SET etat='publiee', decide_par=?, decide_le=?, version=version+1 WHERE id=?", (a.compte, P.maintenant(), n['id']))
    desk.db.commit()
    return dict(vue(desk, a, row['id']), message='Note ajoutée à la fiche d’audience.')


# ------------------------------------------------------------------------------------------------ lectures
def versions_nouvelles(desk, row):
    """Éléments dont le registre porte une version plus récente que celle de l'instantané (lecture seule)."""
    out = []
    for el in desk.db.execute('SELECT * FROM kits_audience_elements56109 WHERE kit_id=? ORDER BY rang', (row['id'],)):
        cur = desk.db.execute("SELECT version_id FROM prepared_items5689 WHERE document_id=? AND lifecycle='current' ORDER BY version_number DESC LIMIT 1",
                              (el['document_id'],)).fetchone()
        if cur and cur['version_id'] != el['version_id']:
            out.append({'rang': el['rang'], 'titre': el['titre'], 'version_kit': el['version_id'], 'version_cabinet': cur['version_id']})
    return out


def vue(desk, acteur_, kid):
    a = P.acteur(acteur_)
    row = _kit(desk, a, kid)
    etat = 'expire' if row['expire_le'] <= P.maintenant() else row['etat']
    out = {k: row[k] for k in ('id', 'matter_id', 'audience_id', 'cree_par', 'cree_le', 'manifeste_sha256', 'protection', 'taille', 'expire_le',
                               'derniere_reconnexion', 'version')}
    out.update(etat=etat, etat_libelle=ETATS[etat], couverture=json.loads(row['couverture']), limite=LIMITE,
               elements=[dict(r) for r in desk.db.execute('SELECT rang,genre,titre,document_id,version_id,sha256,taille,obligatoire,present,motif '
                                                           'FROM kits_audience_elements56109 WHERE kit_id=? ORDER BY rang', (row['id'],))],
               notes=[dict(r) for r in desk.db.execute('SELECT id,auteur,cree_le,source,texte,sha256,etat,decide_par,decide_le,version FROM kits_audience_notes56109 '
                                                        'WHERE kit_id=? ORDER BY cree_le', (row['id'],))],
               versions_nouvelles=versions_nouvelles(desk, row))
    for n in out['notes']:
        n['etat_libelle'] = ETATS_NOTE[n['etat']]
    return out


def kits_dossier(desk, acteur_, mid):
    a = P.acteur(acteur_)
    P.dossier(desk, a, mid)
    return [vue(desk, a, r['id']) for r in desk.db.execute('SELECT id FROM kits_audience56109 WHERE matter_id=? ORDER BY cree_le DESC LIMIT 20', (str(mid),))]


# ------------------------------------------------------------------------------------------------ bloc du dossier (onglet Agenda)
def _form(route, champs, bouton, extra='', reload_=True, cls='ax-btn ghost'):
    caches = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in champs.items())
    return ('<form class="m5-form rl109-form" data-api="%s"%s autocomplete="off">%s<input type="hidden" name="cle" value="%s">%s<button class="%s" type="submit">%s</button></form>') % (
        e(PREFIXE + route, quote=True), ' data-reload' if reload_ else '', caches, uuid.uuid4().hex, extra, cls, e(bouton))


PHRASE = '<label>Phrase secrète <input type="password" name="phrase" required minlength="12" autocomplete="off"></label>'


def _kit_html(k):
    els = ''.join('<li>%s — %s <span class="ax-muted">version %s · %s</span>%s</li>' % (
        e(GENRES.get(x['genre'], x['genre'])), e(x['titre']), e(x['version_id'][:20]), e((x['sha256'] or '')[:12]),
        '' if x['present'] else ' <strong class="rl109-manquant">manquant%s</strong>' % (' (obligatoire)' if x['obligatoire'] else '')) for x in k['elements'])
    nouvelles = ''.join('<li>%s : version plus récente au cabinet</li>' % e(v['titre']) for v in k['versions_nouvelles'])
    notes = ''.join('<li>%s — %s <span class="ax-muted">%s, %s</span>%s</li>' % (
        e(n['etat_libelle']), e(n['texte'][:300]), e(n['auteur']), e(n['cree_le'][:16]),
        _form('%s/note/%s/publier' % (k['id'], n['id']), {'version': n['version']}, 'Ajouter à la fiche d’audience') if n['etat'] in ('a_publier', 'conflit') else '')
                    for n in k['notes'])
    lire = _form('%s/piece' % k['id'], {}, 'Ouvrir une pièce (sur ce poste)', PHRASE + '<label>Pièce n° <input type="number" name="rang" min="1" required></label>', reload_=False)
    return ('<li class="rl109-carte"><p><strong>Instantané du %s</strong> — %s · expire le %s</p><ul>%s</ul>%s%s<p class="ax-muted">%s</p>'
            '<div class="rl109-actions">%s%s%s</div></li>') % (
        e(k['cree_le'][:16].replace('T', ' ')), e(k['etat_libelle']), e(k['expire_le'][:10]), els,
        ('<p>Depuis l’instantané :</p><ul>%s</ul>' % nouvelles) if nouvelles else '', ('<p>Notes :</p><ul>%s</ul>' % notes) if notes else '', e(k['limite']),
        lire, _form('%s/note' % k['id'], {}, 'Conserver la note', '<label>Note après l’audience <textarea name="texte" maxlength="10000" required></textarea></label>'),
        _form('%s/reconnecter' % k['id'], {}, 'Rapprocher (reconnexion)'))


def bloc_dossier(desk, prefix, m, acteur_=None):
    """« Préparer mon audience » (onglet Agenda du dossier) : lecture seule ; aucun modèle ni réseau ; nouvelle interface."""
    from .membres5650 import current_author, viewer_role
    from . import audiences5688 as au
    try:
        a = P.acteur(acteur_ or (current_author() or 'cabinet', viewer_role()))
        from .interface56100 import active
        if not active(desk, a.compte):
            return ''
        kits = kits_dossier(desk, a, m['id'])
        audiences = desk.db.execute(au.SELECT + " WHERE h.matter=? AND h.statut IN ('a_venir','renvoyee') ORDER BY starts LIMIT 20", (m['id'],)).fetchall() \
            if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiences5688'").fetchone() else []   # H1 : lecture seule
        docs = desk.db.execute("SELECT document_id, version_id, title FROM prepared_items5689 WHERE matter_id=? AND lifecycle='current' ORDER BY updated DESC LIMIT 120",
                               (m['id'],)).fetchall()
    except (Stop, sqlite3.Error) as ex:
        return '<p class="d79-warn" role="alert">%s</p>' % e(MESSAGES.get(str(ex).split(':')[0], 'Kit d’audience indisponible.'))
    opts = ''.join('<option value="%s">%s — version %s</option>' % (e('%s|%s' % (d['document_id'], d['version_id']), quote=True), e(d['title']), e(d['version_id'][:16]))
                   for d in docs)
    preparer_ = ''
    if audiences:
        preparer_ = ('<details class="rl109-panneau"><summary class="ax-btn">Préparer mon audience</summary>%s</details>' % _form(
            'dossier/%s/preparer' % m['id'], {}, 'Geler l’instantané',
            '<label>Audience <select name="audience">%s</select></label><label>Éléments obligatoires (conclusions, bordereau, pièces essentielles) '
            '<select name="obligatoires" multiple size="5">%s</select></label><label>Éléments complémentaires <select name="complementaires" multiple size="4">%s</select></label>'
            '<label>Expire le <input type="date" name="expiration"></label>%s<p class="ax-muted">La phrase secrète n’est pas conservée : sans elle, le kit est illisible.</p>' % (
                ''.join('<option value="%s">%s — %s</option>' % (e(h['id'], quote=True), e(au.when_label(h['starts'], h['all_day'], P.tz(desk))), e(h['title'] or ''))
                        for h in audiences), opts, opts, PHRASE), cls='ax-btn'))
    return ('<section class="rl109-section" aria-labelledby="rl109-kit-t"><h2 id="rl109-kit-t">Kit d’audience hors ligne</h2>%s<ul class="rl109-liste">%s</ul></section>') % (
        preparer_, ''.join(_kit_html(k) for k in kits) or '<li class="ax-muted">Aucun kit préparé.</li>')


# ------------------------------------------------------------------------------------------------ routes ``m56109/kit/``
def _ecriture(parts):
    return ((len(parts) == 3 and parts[0] == 'dossier' and parts[2] == 'preparer') or (len(parts) == 2 and parts[1] in ('ouvrir', 'piece', 'note', 'reconnecter'))
            or (len(parts) == 4 and parts[1] == 'note' and parts[3] == 'publier'))


def allowed(role, method, api_):
    """Trois rôles du cabinet (le dossier et le kit sont relus par le service) ; la phrase secrète voyage dans un POST, jamais dans l'URL."""
    parts = P.route(api_, PREFIXE)
    if role not in P.ROLES or parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return (len(parts) == 2 and parts[0] == 'dossier') or (len(parts) == 2 and parts[1] in ('etat', 'telecharger'))
    return method == 'POST' and _ecriture(parts)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return P.erreur(ex, statuts={'kit_altere': '409 Conflict', 'piece_alteree': '409 Conflict', 'kit_expire': '410 Gone', 'droits_retires': '404 Not Found',
                                     'protection_indisponible': '503 Service Unavailable'}, messages=MESSAGES)
    except sqlite3.OperationalError:
        return P.sortie({'error': 'registre_occupe', 'message': P.MESSAGES['registre_occupe']}, '503 Service Unavailable')


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    a = actor(env)
    parts = P.route(name, PREFIXE)
    if not parts:
        raise Stop('route_inconnue')
    if not allowed(a[1], method, name):
        raise Stop('role_insuffisant' if a[1] not in P.ROLES else 'route_inconnue')
    if method in ('GET', 'HEAD'):
        if parts[0] == 'dossier':
            return P.sortie({'kits': kits_dossier(desk, a, parts[1])})
        if parts[1] == 'etat':
            return P.sortie(vue(desk, a, parts[0]))
        row, blob = conteneur(desk, P.acteur(a), parts[0])
        return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': blob,
                'headers': [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
                            ('Content-Disposition', 'attachment; filename="kit-audience-%s.axhkit"' % row['id'][-8:])]}
    from .web440 import check_post
    data = check_post(env, auth)
    if parts[0] == 'dossier':
        out = preparer(desk, a, parts[1], data, data.get('cle'))
        return P.sortie({**out, 'message': 'Instantané gelé : %s.' % ETATS[out['etat']]})
    kid, quoi = parts[0], parts[1]
    if quoi == 'ouvrir':
        return P.sortie(ouvrir_kit(desk, a, kid, str(data.get('phrase') or '')))
    if quoi == 'piece':
        try:
            rang = int(str(data.get('rang') or ''))
        except ValueError:
            raise Stop('introuvable') from None
        raw, nom = lire_piece(desk, a, kid, str(data.get('phrase') or ''), rang)
        return P.sortie({'noreload': True, 'message': 'Pièce vérifiée (empreinte conforme au manifeste).',
                         'download': {'base64': base64.b64encode(raw).decode('ascii'), 'mime': 'application/octet-stream', 'filename': nom}})
    if quoi == 'note' and len(parts) == 2:
        return P.sortie(ajouter_note(desk, a, kid, data))
    if quoi == 'reconnecter':
        return P.sortie({**reconnecter(desk, a, kid, data), 'message': 'Rapprochement fait : droits relus, versions nouvelles et notes signalées.'})
    return P.sortie(publier_note(desk, a, kid, parts[2], data))
