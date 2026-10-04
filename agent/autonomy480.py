"""Niveaux d'autonomie 4.8.0 : l'avocat règle lui-même jusqu'où l'agent agit seul.

Trois niveaux, offerts tâche par tâche selon ce qui a un sens :
  - proposer : l'agent signale seulement ; rien n'est préparé ni écrit ;
  - brouillon : l'agent prépare le résultat dans AxiorHub (brouillon dans le dossier Brouillons, projet d'acte) ;
  - agir : l'agent exécute lui-même une action interne et réversible (inscrire à l'agenda, rattacher et classer).

Ce qui sort du cabinet (envoi, dépôt, signature, paiement, facturation définitive, suppression) n'est jamais
configurable : validation obligatoire. Les niveaux gouvernent le travail que l'agent déclenche de lui-même ;
une demande explicite de l'avocat est toujours exécutée.

Passer une tâche à « agir » exige une confirmation explicite ; revenir à un niveau inférieur est immédiat.
"""
from datetime import datetime
import json
import re

from .common import Stop, digest

LEVELS = ('propose', 'brouillon', 'agir')
LEVEL_LABELS = {'propose': 'Proposer seulement', 'brouillon': 'Préparer un brouillon', 'agir': 'Agir'}

TASKS = {
    'courriel_reponse': {
        'label': 'Répondre aux courriels entrants', 'levels': ('propose', 'brouillon'), 'default': 'brouillon',
        'help': {'propose': 'L’agent signale le courriel à traiter mais n’écrit aucun brouillon.',
                 'brouillon': 'L’agent dépose un brouillon dans le dossier Brouillons. Il ne l’envoie jamais.'}},
    'acte_courrier': {
        'label': 'Préparer actes et courriers à partir des courriels', 'levels': ('propose', 'brouillon'), 'default': 'brouillon',
        'help': {'propose': 'L’agent signale qu’un acte ou un courrier pourrait être préparé, sans le rédiger.',
                 'brouillon': 'L’agent prépare un projet à relire dans AxiorHub. Il ne le dépose ni ne l’envoie.'}},
    'agenda_echeance': {
        'label': 'Inscrire les échéances à l’agenda', 'levels': ('propose', 'agir'), 'default': 'agir',
        'legacy_setting': 'automation:deadline_calendar450',
        'help': {'propose': 'L’échéance est signalée dans la liste « À valider » ci-dessous ; rien n’est écrit dans l’agenda.',
                 'agir': 'L’agent inscrit lui-même l’échéance dans l’agenda Nextcloud (un seul événement, mis à jour sans doublon).'}},
    'agenda_avis': {
        'label': 'Inscrire à l’agenda les dates des avis de procédure', 'levels': ('propose', 'agir'), 'default': 'agir',
        'legacy_setting': 'automation:notice_calendar440',
        'help': {'propose': 'Les dates relevées dans un avis sont signalées dans la liste « À valider » ; rien n’est écrit dans l’agenda.',
                 'agir': 'L’agent inscrit lui-même les dates relevées dans l’agenda Nextcloud.'}},
    'classement': {
        'label': 'Rattacher et classer les courriels par dossier', 'levels': ('propose', 'agir'), 'default': 'agir',
        'help': {'propose': 'Les passes automatiques de rattachement et de rapprochement ne tournent plus seules ; vous les lancez à la main.',
                 'agir': 'L’agent rattache et classe seul, par passes régulières, dans l’état interne d’AxiorHub (aucun courriel n’est déplacé ni supprimé dans votre messagerie).'}},
}

LOCKED = (
    ('envoi_courriel', 'Envoyer un courriel ou une réponse'),
    ('depot_rpva', 'Déposer un acte (RPVA, e-Barreau) ou le notifier'),
    ('signature', 'Signer ou apposer la signature scannée sans votre accord'),
    ('paiement', 'Payer ou engager une dépense'),
    ('facturation', 'Émettre une facture définitive'),
    ('suppression', 'Supprimer ou écraser une source, une pièce ou un courriel'),
)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS autonomy_history480(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, task TEXT NOT NULL, old TEXT NOT NULL, new TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS autonomy_pending480(
  id INTEGER PRIMARY KEY, created TEXT NOT NULL, decided TEXT NOT NULL DEFAULT '', task TEXT NOT NULL,
  matter TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, payload TEXT NOT NULL, dedupe TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '');
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now(desk):
    return desk.now()


def _lowest(task):
    return TASKS[task]['levels'][0]


def default_level(desk, task):
    spec = TASKS[task]
    legacy = spec.get('legacy_setting')
    if legacy and not desk.settings(legacy, True):
        return _lowest(task)          # l'ancien interrupteur « désactivé » est respecté
    return spec['default']


def level(desk, task):
    if task not in TASKS:
        raise Stop('tache_autonomie_inconnue')
    if desk.settings('autonomy480:prudent', False):
        return _lowest(task)
    stored = desk.settings('autonomy480:level:' + task, None)
    if stored in TASKS[task]['levels']:
        return stored
    return default_level(desk, task)


def allows(desk, task, wanted):
    """Vrai si le niveau courant de la tâche est au moins `wanted` (propose < brouillon < agir)."""
    current = level(desk, task)
    return LEVELS.index(current) >= LEVELS.index(wanted)


def set_level(desk, task, new, confirm='', reason='manuel'):
    ensure_schema(desk)
    if task not in TASKS:
        if task in dict(LOCKED):
            raise Stop('action_toujours_soumise_a_validation')
        raise Stop('tache_autonomie_inconnue')
    if new not in TASKS[task]['levels']:
        raise Stop('niveau_autonomie_invalide')
    old = level(desk, task)
    if new == 'agir' and old != 'agir' and str(confirm) != 'yes':
        raise Stop('confirmation_autonomie_requise')
    desk.setting('autonomy480:level:' + task, new)
    if new != old:
        desk.db.execute('INSERT INTO autonomy_history480(at,task,old,new,reason) VALUES(?,?,?,?,?)',
                        (_now(desk), task, old, new, str(reason)[:120]))
        desk.db.commit()
        desk.audit('autonomie_480_niveau', {'task': task, 'old': old, 'new': new, 'confirmed': str(confirm) == 'yes'})
    return {'task': task, 'level': new, 'previous': old}


def set_prudent(desk, on=True):
    """Interrupteur général réversible : toutes les tâches au niveau le plus bas, sans effacer vos réglages."""
    ensure_schema(desk)
    desk.setting('autonomy480:prudent', bool(on))
    desk.db.execute('INSERT INTO autonomy_history480(at,task,old,new,reason) VALUES(?,?,?,?,?)',
                    (_now(desk), '*', 'normal' if on else 'prudent', 'prudent' if on else 'normal', 'interrupteur général'))
    desk.db.commit()
    desk.audit('autonomie_480_prudent', {'on': bool(on)})
    return {'prudent': bool(on)}


def snapshot(desk):
    ensure_schema(desk)
    prudent = bool(desk.settings('autonomy480:prudent', False))
    tasks = []
    for key, spec in TASKS.items():
        current = level(desk, key)
        tasks.append({'task': key, 'label': spec['label'], 'level': current, 'level_label': LEVEL_LABELS[current],
                      'levels': [{'level': l, 'label': LEVEL_LABELS[l], 'help': spec['help'][l]} for l in spec['levels']],
                      'default': default_level(desk, key)})
    history = [dict(r) for r in desk.db.execute('SELECT at,task,old,new,reason FROM autonomy_history480 ORDER BY id DESC LIMIT 30')]
    return {'prudent': prudent, 'tasks': tasks, 'locked': [{'task': k, 'label': v} for k, v in LOCKED],
            'history': history, 'pending': pending_list(desk)}


# ------------------------------------------------------------------ éléments en attente (niveau « proposer »)
def pending_add(desk, task, matter, title, payload):
    ensure_schema(desk)
    dedupe = digest(task + '|' + json.dumps(payload, sort_keys=True, default=str))
    row = desk.db.execute('SELECT id,status FROM autonomy_pending480 WHERE dedupe=?', (dedupe,)).fetchone()
    if row:
        return row['id'], False
    cur = desk.db.execute('INSERT INTO autonomy_pending480(created,task,matter,title,payload,dedupe) VALUES(?,?,?,?,?,?)',
                          (_now(desk), task, str(matter or '')[:80], str(title)[:300], json.dumps(payload, default=str), dedupe))
    desk.db.commit()
    from . import trace480
    trace480.event(desk, matter, 'propose', 'Proposition : ' + str(title)[:200],
                   'Niveau d’autonomie « proposer seulement » : rien n’a été écrit', {'pending_id': cur.lastrowid, 'task': task})
    return cur.lastrowid, True


def pending_list(desk, status='pending', limit=100):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT id,created,task,matter,title,status,decided FROM autonomy_pending480 WHERE status=? ORDER BY id DESC LIMIT ?',
                           (status, limit)).fetchall()
    return [dict(r) for r in rows]


def pending_decide(desk, pending_id, action, dav=None):
    ensure_schema(desk)
    try:
        pending_id = int(pending_id)
    except (TypeError, ValueError):
        raise Stop('proposition_absente_ou_traitee') from None
    row = desk.db.execute("SELECT * FROM autonomy_pending480 WHERE id=? AND status='pending'", (pending_id,)).fetchone()
    if not row:
        raise Stop('proposition_absente_ou_traitee')
    if action == 'dismiss':
        desk.db.execute("UPDATE autonomy_pending480 SET status='dismissed',decided=? WHERE id=?", (_now(desk), pending_id))
        desk.db.commit()
        desk.audit('autonomie_480_proposition_ecartee', {'id': pending_id, 'task': row['task']})
        return {'id': pending_id, 'status': 'dismissed'}
    if action != 'execute':
        raise Stop('action_invalide')
    if row['task'] not in ('agenda_echeance', 'agenda_avis'):
        raise Stop('tache_autonomie_inconnue')
    payload = json.loads(row['payload'])
    cal = desk.c.get('calendar', {})
    urls = cal.get('urls') or []
    if not urls:
        raise Stop('agenda_non_configure')
    if dav is None:
        from .dav import DAV
        dav = DAV(desk.c['nextcloud'])
    start = datetime.fromisoformat(payload['start'])
    end = datetime.fromisoformat(payload['end'])
    try:
        uid = dav.put_event(urls[0], payload['uid'], payload['title'], start, end, payload.get('description', ''))
        state = 'cree'
    except Stop as ex:
        if str(ex) == 'http_412':
            uid, state = payload['uid'], 'deja_present'
        else:
            raise
    if payload.get('deadline_id'):
        desk.db.execute('UPDATE deadlines450 SET calendar_uid=?,calendar_state=?,updated=? WHERE id=?',
                        (uid, state, _now(desk), payload['deadline_id']))
    desk.db.execute("UPDATE autonomy_pending480 SET status='done',decided=?,result=? WHERE id=?", (_now(desk), state, pending_id))
    desk.db.commit()
    desk.audit('autonomie_480_proposition_executee', {'id': pending_id, 'task': row['task'], 'state': state})
    from . import trace480
    trace480.event(desk, row['matter'], 'agi', 'Inscrit à l’agenda après votre validation : ' + row['title'][:200], 'Validation explicite depuis la page Autonomie', {'pending_id': pending_id})
    return {'id': pending_id, 'status': 'done', 'calendar': state}
