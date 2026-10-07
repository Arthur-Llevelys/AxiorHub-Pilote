"""Nextcloud Deck et Tâches comme tableau de bord du travail de l'agent (AxiorHub 5.3.0).

Deck : un tableau « Production de l'agent » (titre réglable) avec quatre colonnes — À faire, En cours, À relire, Fait. Une carte par
production (document demandé, brouillon de réponse, projet) ; la carte change de colonne avec l'avancement. Le tableau peut être partagé
avec votre compte Nextcloud quand le compte technique d'AxiorHub est différent.

Tâches : pour chaque document à relire, une tâche « Relire : … » dans la liste de tâches d'AxiorHub (échéance le jour même, 18 h) ; elle
est cochée quand vous validez ou écartez le document dans AxiorHub.

Garanties : AxiorHub n'écrit QUE dans son tableau et sa liste de tâches ; il ne supprime jamais de carte ni de tâche ; désactivé par
défaut ; au plus 40 écritures par passage ; les titres de carte ne contiennent que l'intitulé et le nom du dossier.
"""
from datetime import datetime, time as dtime, timedelta, timezone
import json
import re
import secrets
import time

from .common import HTTP, Stop, digest, read_secret

STACKS = ('À faire', 'En cours', 'À relire', 'Fait')
DEFAULTS = {'deck': False, 'tasks': False, 'board': 'Production de l’agent', 'share_with': ''}
MAX_WRITES = 40


def settings(desk):
    raw = desk.settings('deck530:settings', {}) or {}
    out = dict(DEFAULTS)
    out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def save_settings(desk, data):
    s = settings(desk)
    s['deck'] = data.get('deck') in (True, 'yes', 'on', 'true')
    s['tasks'] = data.get('tasks') in (True, 'yes', 'on', 'true')
    board = re.sub(r'\s+', ' ', str(data.get('board') or s['board'])).strip()
    if not 3 <= len(board) <= 80:
        raise Stop('titre_tableau_invalide')
    share = str(data.get('share_with') or '').strip()
    if share and not re.fullmatch(r'[\w .@+-]{1,64}', share):
        raise Stop('utilisateur_nextcloud_invalide')
    if board != s['board']:
        desk.setting('deck530:board_id', None)
    s['board'], s['share_with'] = board, share
    desk.setting('deck530:settings', s)
    desk.audit('deck530_reglages', {'deck': s['deck'], 'tasks': s['tasks']})
    return {**s, 'message': 'Réglages enregistrés.' + (' Le tableau sera créé au prochain passage (5 minutes au plus).' if s['deck'] else '')}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS deck530_cards(item TEXT PRIMARY KEY, card_id INTEGER NOT NULL, stack TEXT NOT NULL, title TEXT NOT NULL,
      updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deck530_tasks(item TEXT PRIMARY KEY, task_id TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
    ''')
    desk.db.commit()


# ---------------------------------------------------------------------------------------- client Deck (API REST v1.0)
class Deck:
    def __init__(self, cfg):
        self.http = HTTP(cfg['url'], cfg['username'], read_secret(cfg['password_file']))
        self.user = cfg['username']
        self.api = self.http.base + '/index.php/apps/deck/api/v1.0'
        self.web = self.http.base + '/index.php/apps/deck/#/board/'
        self.writes = 0

    def call(self, method, path, body=None):
        if method != 'GET':
            if self.writes >= MAX_WRITES:
                raise Stop('limite_ecritures_deck')
            self.writes += 1
        raw = self.http.request(method, self.api + path, json.dumps(body).encode() if body is not None else None,
                                {'OCS-APIRequest': 'true', 'Content-Type': 'application/json', 'Accept': 'application/json'}, 8_000_000)
        return json.loads(raw or b'null')

    def boards(self):
        return self.call('GET', '/boards')

    def create_board(self, title):
        return self.call('POST', '/boards', {'title': title, 'color': '1d4ed8'})

    def stacks(self, board):
        return self.call('GET', '/boards/%d/stacks' % int(board))

    def create_stack(self, board, title, order):
        return self.call('POST', '/boards/%d/stacks' % int(board), {'title': title, 'order': order})

    def create_card(self, board, stack, title, description):
        return self.call('POST', '/boards/%d/stacks/%d/cards' % (int(board), int(stack)),
                         {'title': title[:255], 'type': 'plain', 'order': 999, 'description': description[:4000]})

    def move_card(self, board, stack, card, new_stack):
        return self.call('PUT', '/boards/%d/stacks/%d/cards/%d/reorder' % (int(board), int(stack), int(card)), {'order': 0, 'stackId': int(new_stack)})

    def share(self, board, user):
        return self.call('POST', '/boards/%d/acl' % int(board), {'type': 0, 'participant': user, 'permissionEdit': True,
                                                                  'permissionShare': False, 'permissionManage': False})


def _client(desk):
    from .workplan import _workflow_cfg
    return Deck(_workflow_cfg(desk))


def ensure_board(desk, deck, s):
    """Tableau et colonnes d'AxiorHub ; renvoie (id du tableau, {nom de colonne: id})."""
    bid = desk.settings('deck530:board_id', None)
    boards = deck.boards() if not bid else []
    if not bid:
        found = next((b for b in boards if b.get('title') == s['board'] and not b.get('archived') and not b.get('deletedAt')), None)
        bid = int((found or deck.create_board(s['board']))['id'])
        desk.setting('deck530:board_id', bid)
    stacks = {st.get('title'): int(st['id']) for st in (deck.stacks(bid) or [])}
    for order, title in enumerate(STACKS):
        if title not in stacks:
            stacks[title] = int(deck.create_stack(bid, title, order)['id'])
    share = s['share_with']
    if share and share != deck.user and desk.settings('deck530:shared_with', '') != share:
        deck.share(bid, share)
        desk.setting('deck530:shared_with', share)
    return bid, stacks


# ---------------------------------------------------------------------------------------- ce que le tableau doit montrer
PRODUCTION = {'docrequest520', 'maildraft5613', 'prepare_reply', 'prepare_draft', 'prepare_document_project', 'prepare_word_project', 'prepare_legal_opinion',
              'draft_act', 'pieces_create510', 'studio_prepare420', 'prepare_hearing', 'prepare_cabinet_letter'}


def desired(desk):
    from . import cockpit530
    from .web import JOB_LABELS
    from .common import load_matters, matter_display
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    out = {}
    for r in desk.db.execute("SELECT id,kind,args FROM jobs WHERE status IN ('pending','running') ORDER BY id DESC LIMIT 60"):
        if r['kind'] not in PRODUCTION:
            continue
        try:
            args = json.loads(r['args'] or '{}')
        except ValueError:
            args = {}
        name = labels.get(str(args.get('matter', '')), '')
        out['job:%d' % r['id']] = ('En cours', '%s%s' % (JOB_LABELS.get(r['kind'], r['kind']), (' — ' + name) if name else ''), 'Travail n° %d en cours.' % r['id'])
    for item in cockpit530.review_items(desk, cached_only=True):
        out[item['id']] = ('À relire', '%s — %s' % (item['title'], item['matter_label'] or 'cabinet'), 'À relire dans AxiorHub : %s' % item['dest'])
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for r in desk.db.execute('SELECT item,title FROM cockpit530_reviewed WHERE at>=?', (since,)):
        out[r['item']] = ('Fait', r['title'] or 'Production relue', '')
    try:
        from . import routines520
        for d in routines520.documents(desk)[:15]:
            key = 'todo:' + digest(d['source'] + '|' + d['title'])[:24]
            out.setdefault(key, ('À faire', '%s — %s' % (d['title'][:120], labels.get(d['matter'], 'dossier à préciser')), d['source']))
    except Exception:
        pass
    return out


def sync(desk, deck=None, dav=None):
    s = settings(desk)
    if not (s['deck'] or s['tasks']):
        return {'message': 'Deck et Tâches désactivés.', 'writes': 0}
    ensure_schema(desk)
    from . import cockpit530
    cockpit530.ensure_schema(desk)
    want = desired(desk)
    report = {'cards_created': 0, 'cards_moved': 0, 'tasks_created': 0, 'tasks_done': 0}
    if s['deck']:
        deck = deck or _client(desk)
        bid, stacks = ensure_board(desk, deck, s)
        mapped = {r['item']: dict(r) for r in desk.db.execute('SELECT * FROM deck530_cards')}
        try:
            for item, (stack, title, description) in want.items():
                row = mapped.get(item)
                if not row:
                    if stack == 'Fait':
                        continue                      # rien à créer pour ce qui est déjà terminé
                    card = deck.create_card(bid, stacks[stack], title, description)
                    desk.db.execute('INSERT OR REPLACE INTO deck530_cards VALUES(?,?,?,?,?)', (item, int(card['id']), stack, title[:255], desk.now()))
                    report['cards_created'] += 1
                elif row['stack'] != stack:
                    deck.move_card(bid, stacks[row['stack']], row['card_id'], stacks[stack])
                    desk.db.execute('UPDATE deck530_cards SET stack=?,updated=? WHERE item=?', (stack, desk.now(), item))
                    report['cards_moved'] += 1
            for item, row in mapped.items():
                # Travail terminé, document disparu, échéance traitée : la carte rejoint « Fait » (jamais supprimée).
                if item not in want and row['stack'] != 'Fait':
                    deck.move_card(bid, stacks[row['stack']], row['card_id'], stacks['Fait'])
                    desk.db.execute("UPDATE deck530_cards SET stack='Fait',updated=? WHERE item=?", (desk.now(), item))
                    report['cards_moved'] += 1
        except Stop as ex:
            if str(ex) != 'limite_ecritures_deck':
                raise
            report['suite'] = True
        finally:
            desk.db.commit()
        report['board_url'] = deck.web + str(bid)
        desk.setting('deck530:board_url', report['board_url'])
    if s['tasks']:
        report.update(_sync_tasks(desk, want, dav))
    desk.setting('deck530:last', {'at': desk.now(), **{k: v for k, v in report.items() if k != 'board_url'}})
    return {**report, 'message': 'Deck : %d carte(s) créée(s), %d déplacée(s). Tâches : %d créée(s), %d cochée(s).' % (
        report['cards_created'], report['cards_moved'], report['tasks_created'], report['tasks_done'])}


def _sync_tasks(desk, want, dav=None):
    from .workplan import _dav, _workflow_cfg, ensure_schema as wp_schema
    cfg = _workflow_cfg(desk)
    url = cfg.get('task_calendar_url', '')
    if not url:
        return {'tasks_note': 'Aucune liste de tâches AxiorHub configurée (configure-nextcloud-workflow.py).'}
    wp_schema(desk)
    client = dav or _dav(desk)
    from . import cockpit530
    tz = cockpit530.tz(desk)
    created = done = 0
    mapped = {r['item']: dict(r) for r in desk.db.execute('SELECT * FROM deck530_tasks')}
    for item, (stack, title, description) in want.items():
        if not item.startswith('doc:') or created >= 10:
            continue
        row = mapped.get(item)
        if stack == 'À relire' and not row:
            uid = 'axiorhub-%s@mail-agent.local' % secrets.token_hex(16)
            tid = digest('task530|' + uid)[:32]
            due = datetime.combine(datetime.now(tz).date(), dtime(18, 0), tz)
            name = ('Relire : ' + title)[:300]
            client.put_todo(url, uid, name, description[:2000], None, due.isoformat(), 'NEEDS-ACTION', 5, 0, '')
            now = desk.now()
            desk.db.execute('INSERT OR REPLACE INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (tid, uid, url, '', '*', '', name, description[:2000], '', due.isoformat(), 15, 5, 'todo', '', 'review', '', '', 'cockpit530', now, now, now))
            desk.db.execute('INSERT OR REPLACE INTO deck530_tasks VALUES(?,?,0,?)', (item, tid, now))
            created += 1
    for item, row in mapped.items():
        if row['done']:
            continue
        stack = want.get(item, ('Fait',))[0]
        if stack == 'Fait':
            from .workplan import update_task_status
            try:
                update_task_status(desk, {'task': row['task_id'], 'status': 'completed'}, client)
            except Stop:
                pass
            desk.db.execute('UPDATE deck530_tasks SET done=1,updated=? WHERE item=?', (desk.now(), item))
            done += 1
    desk.db.commit()
    return {'tasks_created': created, 'tasks_done': done}


def tick(desk, now=None):
    """Toutes les 5 minutes, si Deck ou Tâches est activé : un travail de synchronisation (automatique, annulable)."""
    s = settings(desk)
    if not (s['deck'] or s['tasks']):
        return None
    now = now or time.time()
    if now - float(desk.settings('deck530:last_tick', 0) or 0) < 300:
        return None
    desk.setting('deck530:last_tick', now)
    return desk.enqueue('deck530_sync', {}, priority=60)


def counts(desk):
    try:
        ensure_schema(desk)
        rows = dict(desk.db.execute('SELECT stack, COUNT(*) FROM deck530_cards GROUP BY stack').fetchall())
    except Exception:
        rows = {}
    return [(name, int(rows.get(name, 0))) for name in STACKS]


def perform(desk, kind, args):
    if kind == 'deck530_sync':
        return sync(desk)
    raise Stop('action_inconnue')
