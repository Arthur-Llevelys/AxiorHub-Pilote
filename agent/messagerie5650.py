"""5.6.50 : messagerie interne du cabinet — sans Nextcloud, tout reste sur le serveur du cabinet.

- **Conversations à deux**, **groupes** (créés par n'importe quel membre ; le créateur retire des membres, chacun peut partir) et
  **fil de chaque dossier** (visible de tous ceux qui voient le dossier ; dossiers réservés respectés).
- Chaque message porte le nom et le rôle de son auteur ; un administrateur ne lit pas les conversations des autres.
- **Suivre** un fil de dossier : on le suit en y écrivant, en y étant mentionné (« @identifiant ») ou d'un clic ; seuls les fils
  suivis comptent dans la pastille « non lus » du menu.
- **Avis de l'agent** : l'agent dépose un avis dans le fil du dossier (document produit, réponse préparée, fiche mise à jour) ; il ne
  lit jamais les conversations (aucune donnée de la messagerie n'est donnée au modèle).
- Liens internes seulement (chemin d'AxiorHub) ; un message retiré par son auteur reste signalé comme retiré.
"""
from datetime import datetime, timezone
import json
import re

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fils5650(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  matter TEXT NOT NULL DEFAULT '', direct_key TEXT, created_by TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
  last_message INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS fils5650_direct ON fils5650(direct_key) WHERE direct_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS fils5650_dossier ON fils5650(matter) WHERE kind='dossier';
CREATE TABLE IF NOT EXISTS participants5650(thread INTEGER NOT NULL, owner TEXT NOT NULL, since TEXT NOT NULL,
  last_read INTEGER NOT NULL DEFAULT 0, follows INTEGER NOT NULL DEFAULT 1, left_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(thread, owner));
CREATE INDEX IF NOT EXISTS participants5650_owner ON participants5650(owner, follows);
CREATE TABLE IF NOT EXISTS messages5650(id INTEGER PRIMARY KEY AUTOINCREMENT, thread INTEGER NOT NULL, author TEXT NOT NULL,
  author_name TEXT NOT NULL, author_role TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'message', text TEXT NOT NULL,
  link TEXT NOT NULL DEFAULT '', link_label TEXT NOT NULL DEFAULT '', mentions TEXT NOT NULL DEFAULT '[]', at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS messages5650_thread ON messages5650(thread, id);
'''
MAX_TEXT = 8000
MAX_GROUP = 50
AGENT = 'agent'
LINK = re.compile(r'/[A-Za-z0-9/_.\-]*(\?[A-Za-z0-9_.\-=&%]*)?(#[A-Za-z0-9_-]*)?')
KINDS = {'direct': 'Conversation', 'groupe': 'Groupe', 'dossier': 'Dossier'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


# ------------------------------------------------------------------------------------------------ accès
def _thread(desk, tid):
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        raise Stop('conversation_absente') from None
    row = desk.db.execute('SELECT * FROM fils5650 WHERE id=?', (tid,)).fetchone()
    if not row:
        raise Stop('conversation_absente')
    return row


def _participant(desk, tid, me):
    return desk.db.execute('SELECT * FROM participants5650 WHERE thread=? AND owner=?', (tid, me)).fetchone()


def can_read(desk, row, me, hidden=frozenset()):
    if row['kind'] == 'dossier':
        if row['matter'] in hidden:
            return False
        from .common import load_matters
        return any(m['id'] == row['matter'] for m in load_matters(desk.c))
    part = _participant(desk, row['id'], me)
    return bool(part) and not part['left_at']


def _check(desk, tid, me, hidden):
    row = _thread(desk, tid)
    if not can_read(desk, row, me, hidden):
        raise Stop('conversation_absente')   # même réponse qu'un fil inexistant : rien n'est révélé
    return row


def _join(desk, tid, owner, follows=1):
    desk.db.execute('INSERT INTO participants5650(thread,owner,since,last_read,follows) VALUES(?,?,?,0,?) ON CONFLICT(thread,owner) DO UPDATE SET '
                    "follows=MAX(participants5650.follows,excluded.follows),left_at=''", (tid, owner, _now(), follows))


# ------------------------------------------------------------------------------------------------ fils
def open_direct(desk, me, other, members):
    other = str(other or '').strip()
    if not other or other == me or other not in members:
        raise Stop('destinataire_inconnu')
    key = '|'.join(sorted((me, other)))
    row = desk.db.execute('SELECT id FROM fils5650 WHERE direct_key=?', (key,)).fetchone()
    if row:
        tid = row['id']
    else:
        cur = desk.db.execute("INSERT INTO fils5650(kind,title,direct_key,created_by,created,updated) VALUES('direct','',?,?,?,?)",
                              (key, me, _now(), _now()))
        tid = cur.lastrowid
    for owner in (me, other):
        _join(desk, tid, owner)
    desk.db.commit()
    return tid


def create_group(desk, me, title, owners, members):
    title = re.sub(r'\s+', ' ', str(title or '')).strip()[:120]
    if not title:
        raise Stop('titre_groupe_requis')
    chosen = sorted({str(o) for o in owners or [] if str(o) in members and str(o) != me})
    if not chosen:
        raise Stop('membres_groupe_requis')
    if len(chosen) + 1 > MAX_GROUP:
        raise Stop('groupe_trop_grand')
    cur = desk.db.execute("INSERT INTO fils5650(kind,title,created_by,created,updated) VALUES('groupe',?,?,?,?)", (title, me, _now(), _now()))
    for owner in [me] + chosen:
        _join(desk, cur.lastrowid, owner)
    desk.db.commit()
    desk.audit('messagerie5650_groupe', {'thread': cur.lastrowid, 'membres': len(chosen) + 1})
    return cur.lastrowid


def change_group(desk, me, tid, add=(), remove=(), members=None, hidden=frozenset()):
    row = _check(desk, tid, me, hidden)
    if row['kind'] != 'groupe':
        raise Stop('conversation_non_groupe')
    for owner in add or ():
        if str(owner) in (members or {}):
            _join(desk, row['id'], str(owner))
    for owner in remove or ():
        owner = str(owner)
        if owner != me and row['created_by'] != me:
            raise Stop('retrait_reserve_au_createur')
        desk.db.execute('UPDATE participants5650 SET left_at=? WHERE thread=? AND owner=?', (_now(), row['id'], owner))
    count = desk.db.execute("SELECT COUNT(*) FROM participants5650 WHERE thread=? AND left_at=''", (row['id'],)).fetchone()[0]
    if count > MAX_GROUP:
        desk.db.rollback()
        raise Stop('groupe_trop_grand')
    desk.db.commit()
    return count


def matter_thread(desk, matter, me='', hidden=frozenset(), follow=True):
    if matter in hidden:
        raise Stop('conversation_absente')
    from .common import load_matters
    if not any(m['id'] == matter for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    row = desk.db.execute("SELECT id FROM fils5650 WHERE kind='dossier' AND matter=?", (matter,)).fetchone()
    if row:
        tid = row['id']
    else:
        cur = desk.db.execute("INSERT INTO fils5650(kind,title,matter,created_by,created,updated) VALUES('dossier','',?,?,?,?)",
                              (matter, me or AGENT, _now(), _now()))
        tid = cur.lastrowid
    if me and follow:
        _join(desk, tid, me)
    desk.db.commit()
    return tid


def follow(desk, me, tid, on, hidden=frozenset()):
    row = _check(desk, tid, me, hidden)
    if row['kind'] != 'dossier':
        raise Stop('suivi_reserve_aux_dossiers')
    if on:
        _join(desk, row['id'], me)
    else:
        desk.db.execute('UPDATE participants5650 SET follows=0 WHERE thread=? AND owner=?', (row['id'], me))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ messages
def _mentions(text, members):
    found = []
    for token in re.findall(r'@([\w.+\-@]{2,120})', text):
        token = token.rstrip('.').lower()
        for owner, m in members.items():
            names = {owner.lower(), owner.split('@')[0].lower(), re.sub(r'\s+', '', str(m.get('name') or '')).lower()}
            if token in names and owner not in found:
                found.append(owner)
    return found


def post(desk, me, name, role, tid, text, members, hidden=frozenset(), link='', link_label=''):
    row = _check(desk, tid, me, hidden)
    text = str(text or '').replace('\r\n', '\n').strip()
    if not text:
        raise Stop('message_vide')
    if len(text) > MAX_TEXT:
        raise Stop('message_trop_long')
    link = str(link or '').strip()
    if link and (not LINK.fullmatch(link) or link.startswith('//')):
        raise Stop('lien_interne_invalide')
    mentions = _mentions(text, members)
    cur = desk.db.execute('INSERT INTO messages5650(thread,author,author_name,author_role,kind,text,link,link_label,mentions,at) '
                          "VALUES(?,?,?,?,'message',?,?,?,?,?)", (row['id'], me, str(name or me)[:120], role, text, link[:300],
                                                                  str(link_label or '')[:120], json.dumps(mentions), _now()))
    mid = cur.lastrowid
    _join(desk, row['id'], me)
    for owner in mentions:
        if row['kind'] == 'dossier':
            from .membres5650 import hidden_for
            if row['matter'] not in hidden_for(desk.c['state_dir'], owner, (members.get(owner) or {}).get('role', 'avocat')):
                _join(desk, row['id'], owner)
    desk.db.execute('UPDATE participants5650 SET last_read=? WHERE thread=? AND owner=?', (mid, row['id'], me))
    desk.db.execute('UPDATE fils5650 SET updated=?,last_message=? WHERE id=?', (_now(), mid, row['id']))
    desk.db.commit()
    return {'id': mid, 'mentions': mentions}


def notice(desk, matter, text, link='', link_label=''):
    """Avis de l'agent dans le fil du dossier (jamais d'erreur visible : un avis manqué n'empêche pas le travail)."""
    try:
        ensure_schema(desk)
        tid = matter_thread(desk, matter, '', frozenset(), follow=False)
        link = str(link or '')
        if link and not LINK.fullmatch(link):
            link = ''
        cur = desk.db.execute("INSERT INTO messages5650(thread,author,author_name,author_role,kind,text,link,link_label,mentions,at) "
                              "VALUES(?,?,?,?,'avis',?,?,?,'[]',?)", (tid, AGENT, 'Agent AxiorHub', 'agent', str(text)[:2000], link[:300],
                                                                     str(link_label or '')[:120], _now()))
        desk.db.execute('UPDATE fils5650 SET updated=?,last_message=? WHERE id=?', (_now(), cur.lastrowid, tid))
        desk.db.commit()
        return cur.lastrowid
    except Exception:   # noqa: BLE001
        try:
            desk.db.rollback()
        except Exception:   # noqa: BLE001
            pass
        return None


def remove(desk, me, message_id, hidden=frozenset()):
    row = desk.db.execute('SELECT * FROM messages5650 WHERE id=?', (int(message_id),)).fetchone()
    if not row or row['author'] != me:
        raise Stop('message_absent')
    _check(desk, row['thread'], me, hidden)
    desk.db.execute("UPDATE messages5650 SET deleted=1,text='',link='',link_label='' WHERE id=?", (row['id'],))
    desk.db.commit()


def _public(r, me):
    return {'id': r['id'], 'auteur': r['author'], 'nom': r['author_name'], 'role': r['author_role'], 'genre': r['kind'], 'moi': r['author'] == me,
            'texte': '' if r['deleted'] else r['text'], 'retire': bool(r['deleted']), 'lien': '' if r['deleted'] else r['link'],
            'lien_libelle': r['link_label'], 'quand': r['at']}


def messages_page(desk, me, tid, hidden=frozenset(), after=0, before=0, limit=60):
    """Un lot de messages, sans trou (5.6.51, audit B04) :
    - ``after`` : les messages SUIVANTS, dans l'ordre croissant (« plus_recents » : il en reste) ;
    - ``before`` : l'historique précédant ce message (« plus_anciens ») ;
    - ni l'un ni l'autre : ouverture au premier message non lu s'il y en a, sinon les plus récents.
    La lecture est marquée jusqu'au dernier message livré dans l'ordre (jamais au-delà, jamais pour l'historique)."""
    row = _check(desk, tid, me, hidden)
    limit = max(1, min(int(limit), 200))
    after, before = int(after or 0), int(before or 0)
    part = _participant(desk, row['id'], me)
    if not after and not before and part:
        unread = desk.db.execute('SELECT 1 FROM messages5650 WHERE thread=? AND id>? AND author<>? AND deleted=0 LIMIT 1',
                                 (row['id'], part['last_read'], me)).fetchone()
        if unread and part['last_read']:
            after = part['last_read']
    if before:
        rows = desk.db.execute('SELECT * FROM messages5650 WHERE thread=? AND id<? ORDER BY id DESC LIMIT ?', (row['id'], before, limit + 1)).fetchall()
        older, rows = len(rows) > limit, list(reversed(rows[:limit]))
        newer = False
    elif after:
        rows = desk.db.execute('SELECT * FROM messages5650 WHERE thread=? AND id>? ORDER BY id ASC LIMIT ?', (row['id'], after, limit + 1)).fetchall()
        newer, rows = len(rows) > limit, rows[:limit]
        older = bool(desk.db.execute('SELECT 1 FROM messages5650 WHERE thread=? AND id<=? LIMIT 1', (row['id'], after)).fetchone())
    else:
        rows = desk.db.execute('SELECT * FROM messages5650 WHERE thread=? ORDER BY id DESC LIMIT ?', (row['id'], limit + 1)).fetchall()
        older, rows = len(rows) > limit, list(reversed(rows[:limit]))
        newer = False
    out = [_public(r, me) for r in rows]
    if out and part and not before:
        desk.db.execute('UPDATE participants5650 SET last_read=MAX(last_read,?) WHERE thread=? AND owner=?', (out[-1]['id'], row['id'], me))
        desk.db.commit()
    return {'messages': out, 'plus_recents': newer, 'plus_anciens': older}


def messages(desk, me, tid, hidden=frozenset(), after=0, limit=60):
    return messages_page(desk, me, tid, hidden, after=after, limit=limit)['messages']


# ------------------------------------------------------------------------------------------------ listes
def _title(desk, row, me, members, names):
    if row['kind'] == 'direct':
        other = next((o for o in str(row['direct_key'] or '').split('|') if o != me), me)
        m = members.get(other) or {'name': other}
        return m.get('name') or other
    if row['kind'] == 'dossier':
        return 'Dossier ' + names.get(row['matter'], row['matter'])
    return row['title']


def threads(desk, me, members, hidden=frozenset()):
    """Fils de l'utilisateur : conversations et groupes dont il est membre, fils de dossier qu'il suit ; non lus par fil."""
    from .common import load_matters, matter_display
    names = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    rows = desk.db.execute(
        "SELECT f.*, p.last_read, p.follows FROM fils5650 f JOIN participants5650 p ON p.thread=f.id AND p.owner=? "
        "WHERE p.left_at='' AND (f.kind<>'dossier' OR p.follows=1) ORDER BY f.updated DESC LIMIT 200", (me,)).fetchall()
    out = []
    for r in rows:
        if r['kind'] == 'dossier' and (r['matter'] in hidden or r['matter'] not in names):
            continue
        unread = desk.db.execute('SELECT COUNT(*) FROM messages5650 WHERE thread=? AND id>? AND author<>? AND deleted=0',
                                 (r['id'], r['last_read'], me)).fetchone()[0]
        last = desk.db.execute('SELECT author_name,text,deleted,at FROM messages5650 WHERE thread=? ORDER BY id DESC LIMIT 1', (r['id'],)).fetchone()
        count = None
        if r['kind'] == 'groupe':
            count = desk.db.execute("SELECT COUNT(*) FROM participants5650 WHERE thread=? AND left_at=''", (r['id'],)).fetchone()[0]
        out.append({'id': r['id'], 'genre': r['kind'], 'titre': _title(desk, r, me, members, names), 'non_lus': unread, 'dossier': r['matter'],
                    'membres': count, 'dernier': ('%s : %s' % (last['author_name'], 'message retiré' if last['deleted'] else last['text'][:120])) if last else '',
                    'quand': last['at'] if last else r['created']})
    return out


def unread_total(desk, me, hidden=frozenset()):
    rows = desk.db.execute(
        "SELECT f.id, f.kind, f.matter, p.last_read FROM fils5650 f JOIN participants5650 p ON p.thread=f.id AND p.owner=? "
        "WHERE p.left_at='' AND (f.kind<>'dossier' OR p.follows=1) AND f.last_message>p.last_read", (me,)).fetchall()
    total = 0
    for r in rows:
        if r['kind'] == 'dossier' and r['matter'] in hidden:
            continue
        total += desk.db.execute('SELECT COUNT(*) FROM messages5650 WHERE thread=? AND id>? AND author<>? AND deleted=0',
                                 (r['id'], r['last_read'], me)).fetchone()[0]
    return total


def thread_info(desk, me, tid, members, hidden=frozenset()):
    from .common import load_matters, matter_display
    row = _check(desk, tid, me, hidden)
    names = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    people = [r['owner'] for r in desk.db.execute("SELECT owner FROM participants5650 WHERE thread=? AND left_at='' ORDER BY owner<>?, since, rowid",
                                                    (row['id'], row['created_by']))]
    part = _participant(desk, row['id'], me)
    return {'id': row['id'], 'genre': row['kind'], 'titre': _title(desk, row, me, members, names), 'dossier': row['matter'],
            'createur': row['created_by'], 'suivi': bool(part and part['follows'] and not part['left_at']),
            'membres': [{'owner': o, 'nom': (members.get(o) or {}).get('name') or o, 'role': (members.get(o) or {}).get('role', '')}
                        for o in people] if row['kind'] != 'dossier' else []}
