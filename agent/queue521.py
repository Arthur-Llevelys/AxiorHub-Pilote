"""File de travail lisible et déblocable (AxiorHub 5.2.1).

Constat sur un serveur réel : 43 demandes en attente, workers « vivants », réglage d'automatisme bloqué en position 42. Les travaux qui
écrivent (brouillons, documents, réglages anciens) partagent un verrou unique ``run.lock`` ; l'analyse périodique des courriels le gardait
pendant toute sa série d'appels au modèle d'IA (jusqu'à 45 minutes avec un modèle local lent), et la file ne s'écoulait plus.

Ce module :
- note qui tient le verrou (``run.lock.json`` : libellé, heure, processus) pour l'afficher dans le diagnostic ;
- décrit la file : travaux en cours et depuis quand, attente par type, surveillance suspendue ;
- annule, sur demande, les seuls travaux AUTOMATIQUES en attente (ils sont reprogrammés par les minuteries) — jamais une demande de l'avocat ;
- relance la surveillance suspendue après trois échecs ;
- teste le modèle d'IA (durée d'une réponse très courte).
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time

from .common import Stop

# Travaux de maintenance reprogrammés automatiquement : les annuler ne perd rien.
AUTOMATIC = {'run', 'live_mail430', 'live_calendar430', 'live_documents430', 'orchestrator_mail_sweep', 'autonomy_mail_sweep',
             'build_daily_dashboard', 'monitor_all', 'index_all', 'index', 'refresh_brief', 'sync_legal_memory', 'refresh_operational_memory',
             'monitor_matter', 'extract_facts460', 'sync', 'discover', 'health', 'daily_digest', 'organize_cabinet', 'classify_portfolio',
             'reconcile_inbox', 'memory_all', 'proactive34_cycle', 'snapshot_metrics420', 'refresh_unpaid_invoices', 'deck530_sync', 'style550_scan'}
SURVEILLANCE = (('live_mail430', 'Courriels'), ('live_calendar430', 'Agenda'), ('live_documents430', 'Documents des dossiers'))
INTERRUPTED = 'service_redemarre_verifier_avant_relance'


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------------------- verrou
def note_holder(state_dir, label):
    """Écrit qui tient run.lock (appelé juste après l'avoir obtenu). Sans effet en cas d'erreur d'écriture."""
    try:
        path = Path(state_dir) / 'run.lock.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'holder': str(label)[:120], 'since': now(), 'pid': os.getpid()}), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        pass


def lock_state(state_dir):
    """{'busy': bool, 'holder': libellé, 'since': iso, 'pid': int} — sans prendre le verrou durablement."""
    path = Path(state_dir) / 'run.lock'
    busy = False
    try:
        with open(path, 'a') as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
            except BlockingIOError:
                busy = True
    except OSError:
        return {'busy': False, 'holder': '', 'since': '', 'pid': 0}
    info = {}
    if busy:
        try:
            info = json.loads((Path(state_dir) / 'run.lock.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            info = {}
    return {'busy': busy, 'holder': info.get('holder', ''), 'since': info.get('since', ''), 'pid': info.get('pid', 0)}


# ---------------------------------------------------------------------------------------- état de la file
def _age_seconds(stamp):
    try:
        return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(str(stamp))).total_seconds())
    except (TypeError, ValueError):
        return 0


def automatic(kind, priority):
    from .desk import USER_JOBS
    if kind in AUTOMATIC:
        return True
    return int(priority or 0) >= 40 and kind not in USER_JOBS and kind not in ('automation_setting', 'docrequest520')


def report(desk):
    from .web import JOB_LABELS
    label = lambda k: JOB_LABELS.get(k, k)
    running = []
    for r in desk.db.execute("SELECT id,kind,started,worker FROM jobs WHERE status IN ('running','cancel_requested') ORDER BY id"):
        running.append({'id': r['id'], 'kind': r['kind'], 'label': label(r['kind']), 'since': r['started'] or '', 'seconds': int(_age_seconds(r['started'])),
                        'worker': r['worker'] or ''})
    pending = []
    for r in desk.db.execute("SELECT kind, COUNT(*) n, MIN(created) oldest, MIN(priority) p FROM jobs WHERE status='pending' GROUP BY kind ORDER BY n DESC, oldest"):
        pending.append({'kind': r['kind'], 'label': label(r['kind']), 'count': r['n'], 'oldest': r['oldest'] or '', 'automatic': automatic(r['kind'], r['p'])})
    surveillance = []
    for kind, name in SURVEILLANCE:
        row = desk.db.execute('SELECT id,status,attempts,result,finished FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1', (kind,)).fetchone()
        if not row:
            surveillance.append({'kind': kind, 'label': name, 'state': 'jamais', 'reason': '', 'suspended': False})
            continue
        try:
            code = json.loads(row['result'] or '{}').get('erreur', '')
        except ValueError:
            code = ''
        suspended = kind != 'live_mail430' and row['status'] == 'error' and int(row['attempts'] or 0) >= 3
        surveillance.append({'kind': kind, 'label': name, 'state': row['status'], 'reason': code, 'suspended': suspended,
                             'at': (row['finished'] or '')[:16].replace('T', ' ')})
    return {'running': running, 'pending': pending, 'lock': lock_state(desk.c['state_dir']), 'surveillance': surveillance,
            'pending_total': sum(x['count'] for x in pending), 'pending_automatic': sum(x['count'] for x in pending if x['automatic'])}


# ---------------------------------------------------------------------------------------- actions
def purge_automatic(desk):
    """Annule les travaux automatiques en attente. Les demandes de l'avocat (brouillon demandé, document, réglage…) sont conservées."""
    rows = desk.db.execute("SELECT id,kind,priority FROM jobs WHERE status='pending'").fetchall()
    ids = [r['id'] for r in rows if automatic(r['kind'], r['priority'])]
    for jid in ids:
        desk.db.execute("UPDATE jobs SET status='cancelled',finished=?,result=? WHERE id=? AND status='pending'",
                        (now(), json.dumps({'message': 'Travail automatique annulé depuis le diagnostic ; il sera reprogrammé.'}), jid))
    desk.db.commit()
    desk.audit('file_521_travaux_automatiques_annules', {'count': len(ids)})
    kept = len(rows) - len(ids)
    return {'cancelled': len(ids), 'kept': kept,
            'message': '%d travail(aux) automatique(s) annulé(s) ; %d demande(s) de votre part conservée(s). Les contrôles reprendront à leur prochain passage.' % (len(ids), kept)}


def resume_surveillance(desk):
    """Relance les contrôles en lecture suspendus après trois échecs (agenda, documents)."""
    resumed = []
    for kind, name in SURVEILLANCE[1:]:
        row = desk.db.execute('SELECT status,attempts FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1', (kind,)).fetchone()
        if row and row['status'] == 'error' and int(row['attempts'] or 0) >= 3:
            desk.enqueue(kind, priority=55)
            resumed.append(name)
    desk.audit('file_521_surveillance_relancee', {'count': len(resumed)})
    return {'resumed': resumed, 'message': ('Surveillance relancée : %s.' % ', '.join(resumed)) if resumed else 'Aucune surveillance suspendue.'}


def check_ai(desk, purpose='mail_drafting'):
    """Une réponse très courte du modèle utilisé pour les brouillons ; mesure la durée. Aucune donnée de dossier n'est envoyée."""
    from .model import Model, routed_config
    cfg = routed_config(desk.c, purpose)
    where = str(cfg.get('url', ''))
    model = '%s · %s' % (cfg.get('display_provider') or cfg.get('provider_id', ''), cfg.get('display_model') or cfg.get('model', ''))
    started = time.monotonic()
    try:
        Model(cfg).complete([{'role': 'user', 'content': 'Réponds uniquement : OK'}], temperature=0, max_tokens=5)
    except Stop as ex:
        raise Stop(str(ex)) from None
    seconds = time.monotonic() - started
    slow = seconds > 60
    msg = 'Modèle d’IA joignable (%s%s) : réponse courte en %.0f s.' % (model, (' · ' + where) if where.startswith(('http://127.', 'http://localhost')) else '', seconds)
    if slow:
        msg += ' C’est lent : un brouillon demande plusieurs réponses longues ; envisagez un modèle plus léger ou le mode hybride.'
    return {'ok': True, 'seconds': round(seconds, 1), 'slow': slow, 'message': msg}
