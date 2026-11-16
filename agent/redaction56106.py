"""5.6.106 (lot R) : rédaction d'un courriel dans « Courriels » — modèles de réponse du cabinet.

Les **modèles de réponse** sont de simples textes mis en forme (« Bien reçu, je reviens vers vous… »), partagés par tout le cabinet et
insérés au curseur dans la rédaction. Ils n'ont rien à voir avec les modèles de documents (``modeles5684``).

- Lecture : avocats et administrateurs (mêmes rôles que la rédaction ``m5628``) ; tous les modèles du cabinet sont visibles.
- Création : avocats et administrateurs. Modification et suppression : l'**auteur** du modèle ou un **administrateur**. Un modèle absent et
  un modèle d'un autre auteur donnent le même 404 neutre ; un rôle qui ne peut jamais écrire (assistant) reçoit 403.
- Chaque écriture porte une **clé d'idempotence** (même clé, même demande : même réponse, aucun second effet ; même clé, autre demande :
  409) et, pour une modification ou une suppression, la **version** affichée (409 si le modèle a changé entre-temps).
- Le texte est nettoyé par ``webmail5628.clean_html`` (liste fermée de balises et de styles) à l'enregistrement **et** à la lecture.
- Schéma créé au démarrage (``Desk``) seulement ; une lecture n'écrit jamais rien (R05). Réponses ``Cache-Control: private, no-store``.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .common import Stop

PREFIXE = 'm56106/redaction/'
ROLES = ('administrateur', 'avocat')
ROUTES_GET = ('modeles',)
ROUTES_POST = ('modeles/creer', 'modeles/modifier', 'modeles/supprimer')
MAX_MODELES = 300
MAX_TITRE = 120
MAX_HTML = 20000
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
IDENT = re.compile(r'^[0-9a-f]{32}$')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS modeles_reponse56106(id TEXT PRIMARY KEY, titre TEXT NOT NULL, html TEXT NOT NULL, auteur TEXT NOT NULL,
  cree TEXT NOT NULL, modifie TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS redaction56106_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  reponse TEXT NOT NULL, quand TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
MESSAGES = {'introuvable': 'Modèle introuvable.', 'version_perimee': 'Ce modèle a été modifié entre-temps : rechargez la liste.',
            'modele_vide': 'Le modèle est vide.', 'titre_requis': 'Donnez un titre au modèle.',
            'modele_trop_long': 'Modèle trop long (20 000 caractères au plus).', 'trop_de_modeles': 'Trop de modèles (300 au plus) : supprimez-en.',
            'cle_idempotence_requise': 'Page incomplète : rechargez-la.',
            'cle_idempotence_deja_utilisee': 'Cette demande a déjà été traitée avec d’autres valeurs : rechargez la page.',
            'role_insuffisant': 'Action réservée aux avocats et aux administrateurs.', 'route_inconnue': 'Route inconnue.',
            'controle_indisponible': 'Contrôle momentanément indisponible : réessayez.'}
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden',
           'version_perimee': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'controle_indisponible': '503 Service Unavailable'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) seulement."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ nettoyage
def _titre(value):
    titre = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or '')).strip()
    titre = re.sub(r'\s+', ' ', titre)[:MAX_TITRE].strip()
    if not titre:
        raise Stop('titre_requis')
    return titre


def _html(value):
    from .webmail5628 import clean_html, html_to_text
    raw = str(value or '')
    if len(raw) > MAX_HTML * 3:
        raise Stop('modele_trop_long')
    html = clean_html(raw)
    if len(html) > MAX_HTML:
        raise Stop('modele_trop_long')
    if not html_to_text(html).strip():
        raise Stop('modele_vide')
    return html


# ------------------------------------------------------------------------------------------------ lecture (aucune écriture)
def _vue(row, owner, role):
    from .webmail5628 import clean_html
    return {'id': row['id'], 'titre': row['titre'], 'html': clean_html(row['html']), 'auteur': row['auteur'], 'version': row['version'],
            'modifie': row['modifie'], 'modifiable': role == 'administrateur' or row['auteur'] == owner}


def lister(desk, owner, role):
    try:
        rows = desk.db.execute('SELECT * FROM modeles_reponse56106 ORDER BY lower(titre), id LIMIT ?', (MAX_MODELES,)).fetchall()
    except Exception:   # noqa: BLE001 — base illisible : panne annoncée, jamais une liste vide présentée comme réelle
        raise Stop('controle_indisponible') from None
    return [_vue(r, owner, role) for r in rows]


def _autorise(desk, owner, role, ident):
    """Modèle que ce compte peut modifier ou supprimer ; absent ou d'un autre auteur : même 404 neutre."""
    if not IDENT.fullmatch(str(ident or '')):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM modeles_reponse56106 WHERE id=?', (ident,)).fetchone()
    if row is None or not (role == 'administrateur' or row['auteur'] == owner):
        raise Stop('introuvable')
    return row


def _version(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None


# ------------------------------------------------------------------------------------------------ écritures
def creer(desk, owner, titre, html):
    titre, html = _titre(titre), _html(html)
    if desk.db.execute('SELECT COUNT(*) FROM modeles_reponse56106').fetchone()[0] >= MAX_MODELES:
        raise Stop('trop_de_modeles')
    ident, quand = uuid.uuid4().hex, _now()
    desk.db.execute('INSERT INTO modeles_reponse56106 VALUES(?,?,?,?,?,?,1)', (ident, titre, html, owner, quand, quand))
    desk.db.commit()
    desk.audit('redaction56106_modele', {'action': 'creer', 'id': ident, 'owner': owner})
    return {'id': ident, 'version': 1, 'message': 'Modèle enregistré.'}


def modifier(desk, owner, role, ident, titre, html, version):
    row = _autorise(desk, owner, role, ident)
    titre, html = _titre(titre), _html(html)
    cur = desk.db.execute('UPDATE modeles_reponse56106 SET titre=?, html=?, modifie=?, version=version+1 WHERE id=? AND version=?',
                          (titre, html, _now(), row['id'], _version(version)))
    if cur.rowcount != 1:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    desk.audit('redaction56106_modele', {'action': 'modifier', 'id': row['id'], 'owner': owner})
    return {'id': row['id'], 'version': row['version'] + 1, 'message': 'Modèle modifié.'}


def supprimer(desk, owner, role, ident, version):
    row = _autorise(desk, owner, role, ident)
    cur = desk.db.execute('DELETE FROM modeles_reponse56106 WHERE id=? AND version=?', (row['id'], _version(version)))
    if cur.rowcount != 1:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    desk.audit('redaction56106_modele', {'action': 'supprimer', 'id': row['id'], 'owner': owner})
    return {'id': row['id'], 'message': 'Modèle supprimé.'}


# ------------------------------------------------------------------------------------------------ reçus d'idempotence
def _recu(desk, compte, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    r = desk.db.execute('SELECT empreinte, reponse FROM redaction56106_recus WHERE compte=? AND route=? AND cle=?', (compte, route, cle)).fetchone()
    if r and r['empreinte'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(r['reponse']) if r else None


def _garder(desk, compte, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO redaction56106_recus VALUES(?,?,?,?,?,?)',
                    (compte, route, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str), _now()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    return role in ROLES and route in (ROUTES_GET if method == 'GET' else ROUTES_POST if method == 'POST' else ())


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as erreur:
        code = str(erreur).split(':')[0]
        return _out({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):] if name.startswith(PREFIXE) else ''
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    if not allowed(role, method, PREFIXE + route):
        raise Stop('role_insuffisant' if role not in ROLES else 'route_inconnue')
    if method == 'GET':
        return _out({'modeles': lister(desk, owner, role)})
    from .web440 import check_post
    data = check_post(env, auth)   # origine, jeton CSRF, corps borné (MAX_JSON) et JSON objet
    cle = str(data.get('cle') or '')
    champs = [route, str(data.get('id') or ''), str(data.get('titre') or ''), str(data.get('html') or ''), str(data.get('version') or '')]
    empreinte = hashlib.sha256(json.dumps(champs, ensure_ascii=False).encode('utf-8')).hexdigest()
    ancien = _recu(desk, owner, route, cle, empreinte)
    if ancien is not None:
        return _out(ancien)
    if route == 'modeles/creer':
        out = creer(desk, owner, data.get('titre'), data.get('html'))
    elif route == 'modeles/modifier':
        out = modifier(desk, owner, role, data.get('id'), data.get('titre'), data.get('html'), data.get('version'))
    else:
        out = supprimer(desk, owner, role, data.get('id'), data.get('version'))
    _garder(desk, owner, route, cle, empreinte, out)
    return _out(out)
