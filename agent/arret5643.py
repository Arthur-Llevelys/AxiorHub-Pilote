"""5.6.43 (cahier v9, § 16) : contrôleur unique de pause et d'annulation.

Sémantique :
- **Pause de l'IA** (réglage ``missions567:pause``) : aucun nouvel appel au modèle, les appels actifs sont interrompus, les producteurs
  automatiques ne mettent plus rien en file. Les travaux en attente sont **suspendus** (état reprenable ``suspended``), jamais annulés ;
  « Reprendre » les remet en file. Les actions déterministes explicitement demandées (sans IA) restent possibles.
- **Pause d'un dossier** : même contrôle pour les travaux automatiques de ce dossier (``matter`` ou ``matter_id``) ; ceux en attente sont
  suspendus et repris à la levée de la pause.
- **Annulation** : terminale. Un travail en file passe à ``cancelled`` ; un travail actif à ``cancel_requested`` puis ``cancelled`` quand le
  worker le confirme ; ses enfants non achevés sont annulés et aucun enfant nouveau ne peut être créé.

Les décisions sont lues dans la base à chaque contrôle (aucun cache faisant autorité). Si la vérification est impossible, le travail est
suspendu avec le motif ``controle_pause_indisponible``.

Interruption d'une inférence : chaque appel au modèle fait dans un travail est exécuté dans un fil surveillé. Toutes les secondes (même si
le fournisseur ne renvoie rien), l'état du travail, la pause globale et la pause du dossier sont relus ; sur arrêt, la connexion de CET
appel est fermée (le fournisseur local reçoit la déconnexion) et le résultat éventuel est ignoré. La fermeture du transport ne prouve pas
que le calcul est arrêté chez le fournisseur : le journal le dit (« interruption demandée — calcul peut-être encore actif »).
"""
import json
from pathlib import Path
import socket
import sqlite3
import threading
import time

from .common import Stop

ORIGINS = ('user', 'automatic', 'control', 'legacy')
PAUSE_GLOBAL = 'missions567:pause'
PAUSE_MATTERS = 'pause5629:matters'
AUTOMATIC_PRIORITY = 30
STOP_CODES = frozenset({'operation_annulee', 'ia_en_pause', 'automatismes_en_pause', 'agent_en_pause_sur_ce_dossier', 'dossier_en_pause',
                        'controle_pause_indisponible'})
# Travaux sans appel au modèle : ils restent exécutables pendant la pause de l'IA (contrôles, réglages, surveillance des agendas et
# fichiers, dépôts déjà validés, conservation des courriels et des pièces).
DETERMINISTIC = frozenset({'automation_setting', 'live_calendar430', 'live_documents430', 'deposit_draft', 'archive_correspondence5643',
                           'file_mail_attachments5643', 'verify_deliverable420'})
POLL_SECONDS = 1.0

_local = threading.local()


# ---------------------------------------------------------------- lecture fraîche des décisions
def _read(state_dir):
    """(pause globale, dossiers en pause) lus sans cache dans la base de l'instance."""
    path = Path(state_dir or '') / 'desk.sqlite3'
    if not state_dir or not path.is_file():
        return False, {}
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    except sqlite3.Error:
        raise Stop('controle_pause_indisponible') from None
    try:
        values = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM settings WHERE key IN (?,?)', (PAUSE_GLOBAL, PAUSE_MATTERS))}
        matters = values.get(PAUSE_MATTERS) or {}
        return bool(values.get(PAUSE_GLOBAL, False)), (matters if isinstance(matters, dict) else {})
    except (sqlite3.Error, ValueError, TypeError):
        raise Stop('controle_pause_indisponible') from None
    finally:
        db.close()


PAUSE_REASONS = {'pause': 'ia_en_pause', 'pause_dossier': 'agent_en_pause_sur_ce_dossier'}


def stop_code(status, stop_reason):
    """5.6.44 (audit F09) : code d'arrêt d'un travail selon son état ET son motif. Une demande d'arrêt posée par la pause donne le code
    de pause (le travail sera suspendu, donc repris) ; seule une annulation (motif utilisateur, parent ou ancien) est terminale."""
    if status == 'cancelled':
        return 'operation_annulee'
    if status == 'cancel_requested':
        return PAUSE_REASONS.get(str(stop_reason or ''), 'operation_annulee')
    return ''


def _job_state(state_dir, job_id):
    path = Path(state_dir or '') / 'desk.sqlite3'
    if not job_id or not path.is_file():
        return '', None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            row = db.execute('SELECT status,stop_reason FROM jobs WHERE id=?', (int(job_id),)).fetchone()
        finally:
            db.close()
    except (sqlite3.Error, ValueError, TypeError):
        raise Stop('controle_pause_indisponible') from None
    return (row[0], row[1]) if row else ('absent', None)


def _job_status(state_dir, job_id):
    return _job_state(state_dir, job_id)[0]


def job_stop_code(state_dir, job_id):
    return stop_code(*_job_state(state_dir, job_id))


def global_paused(desk):
    return bool(desk.settings(PAUSE_GLOBAL, False))


def matter_of(args):
    args = args or {}
    return str(args.get('matter') or args.get('matter_id') or '')


# ---------------------------------------------------------------- origine des travaux
def resolve_origin(desk, kind, args, priority, explicit=None):
    """Origine explicite > contexte du planificateur > travail parent > déduction historique (migration)."""
    if explicit:
        if explicit not in ORIGINS:
            raise Stop('origine_travail_invalide')
        return explicit
    context = getattr(desk, 'origin_context', None)
    if context in ORIGINS:
        return context
    parent = getattr(desk, 'active_job_id', None)
    if parent:
        try:
            row = desk.db.execute('SELECT origin FROM jobs WHERE id=?', (parent,)).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0] in ('user', 'automatic', 'control'):
            return row[0]
    if str((args or {}).get('automatic') or '') == 'yes' or int(priority if priority is not None else 0) >= AUTOMATIC_PRIORITY:
        return 'automatic'
    return 'user'


class automatic:
    """``with automatic(desk):`` — les travaux mis en file par un planificateur portent l'origine « automatic »."""
    def __init__(self, desk, origin='automatic'):
        self.desk, self.origin = desk, origin

    def __enter__(self):
        self.previous = getattr(self.desk, 'origin_context', None)
        self.desk.origin_context = self.origin
        return self.desk

    def __exit__(self, *exc):
        self.desk.origin_context = self.previous
        return False


def is_automatic_row(row, args=None):
    """Origine enregistrée d'abord ; la priorité ne sert qu'aux travaux antérieurs à 5.6.43 (origine « legacy »)."""
    try:
        origin = row['origin']
    except (IndexError, KeyError, TypeError):
        origin = None
    if origin == 'automatic':
        return True
    if origin in ('user', 'control'):
        return False
    try:
        priority = row['priority']
    except (IndexError, KeyError, TypeError):
        priority = 0
    try:
        kind = row['kind']
    except (IndexError, KeyError, TypeError):
        kind = ''
    return kind == 'live_mail430' or str((args or {}).get('automatic') or '') == 'yes' or int(priority or 0) >= AUTOMATIC_PRIORITY


def check_parent(desk):
    """Un travail annulé ou dont l'arrêt est demandé ne crée plus d'enfant."""
    parent = getattr(desk, 'active_job_id', None)
    if not parent:
        return None
    row = desk.db.execute('SELECT status,stop_reason FROM jobs WHERE id=?', (parent,)).fetchone()
    code = stop_code(row[0], row[1]) if row else ''
    if code:
        raise Stop(code)
    return parent


# ---------------------------------------------------------------- pause globale et reprise
def pause_all(desk, on, owner='cabinet'):
    """Enregistre la pause AVANT de répondre, suspend la file automatique et demande l'arrêt des travaux automatiques actifs."""
    on = bool(on)
    desk.setting(PAUSE_GLOBAL, on)
    counts = {'suspendus': 0, 'arrets_demandes': 0, 'en_cours_a_interrompre': 0, 'repris': 0}
    if on:
        stamp = desk.now()
        if not desk.db.in_transaction:
            desk.db.execute('BEGIN IMMEDIATE')
        auto = "(origin='automatic' OR (origin='legacy' AND (priority>=? OR kind='live_mail430' OR json_extract(args,'$.automatic')='yes')))"
        counts['suspendus'] = desk.db.execute(
            "UPDATE jobs SET status='suspended',stop_reason='pause',result=? WHERE status='pending' AND " + auto,
            (json.dumps({'message': 'Suspendu : IA en pause. Repris automatiquement à la reprise.'}), AUTOMATIC_PRIORITY)).rowcount
        counts['arrets_demandes'] = desk.db.execute(
            "UPDATE jobs SET status='cancel_requested',stop_reason='pause',result=? WHERE status='running' AND " + auto,
            (json.dumps({'message': 'Arrêt demandé par la pause de l’IA ; confirmation du worker attendue.'}), AUTOMATIC_PRIORITY)).rowcount
        placeholders = ','.join('?' * len(DETERMINISTIC))
        counts['en_cours_a_interrompre'] = desk.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='running' AND kind NOT IN (%s)" % placeholders, tuple(DETERMINISTIC)).fetchone()[0]
        desk.db.commit()
        desk.audit('arret5643_pause_globale', {'pause': True, 'owner': owner, **counts, 'at': stamp})
        message = ('IA en pause : aucun nouvel appel au modèle ni travail automatique. %d travail(aux) suspendu(s), %d arrêt(s) demandé(s)%s. '
                   'Les actions manuelles sans IA restent possibles.' % (
                       counts['suspendus'], counts['arrets_demandes'],
                       (', arrêt demandé pour %d appel(s) au modèle en cours' % counts['en_cours_a_interrompre'])
                       if counts['en_cours_a_interrompre'] else ''))
    else:
        counts['repris'] = resume(desk)
        desk.audit('arret5643_pause_globale', {'pause': False, 'owner': owner, **counts})
        message = 'IA reprise. %d travail(aux) suspendu(s) remis en file.' % counts['repris']
    return {'paused': on, **counts, 'message': message}


def resume(desk, matter=None):
    """Remet en file les travaux suspendus par une pause levée (globale ou d'un dossier) ; jamais les travaux annulés."""
    if global_paused(desk):
        return 0
    paused_matters = desk.settings(PAUSE_MATTERS, {}) or {}
    paused_matters = paused_matters if isinstance(paused_matters, dict) else {}
    rows = desk.db.execute("SELECT id,matter_ref,stop_reason FROM jobs WHERE status='suspended'").fetchall()
    ids = [r['id'] for r in rows if (matter is None or r['matter_ref'] == matter) and str(r['matter_ref'] or '') not in paused_matters]
    count = 0
    for jid in ids:
        count += desk.db.execute("UPDATE jobs SET status='pending',stop_reason=NULL,result=NULL WHERE id=? AND status='suspended'",
                                 (jid,)).rowcount
    # 5.6.44 (F09) : reprise avant acquittement de la pause — la demande d'arrêt posée par la pause est retirée ; un travail encore actif
    # continue, et un travail interrompu entre-temps est remis en file par le worker (pas de second clic).
    stopping = desk.db.execute("SELECT id,matter_ref,stop_reason FROM jobs WHERE status='cancel_requested' AND stop_reason IN ('pause','pause_dossier')").fetchall()
    for r in stopping:
        if (matter is None or r['matter_ref'] == matter) and str(r['matter_ref'] or '') not in paused_matters:
            desk.db.execute("UPDATE jobs SET status='running',stop_reason=NULL WHERE id=? AND status='cancel_requested' AND stop_reason=?",
                            (r['id'], r['stop_reason']))
    desk.db.commit()
    return count


def suspend_matter(desk, mid):
    """Pause d'un dossier : ses travaux automatiques en attente sont suspendus, ceux en cours reçoivent une demande d'arrêt."""
    cond = ("matter_ref=? AND (origin='automatic' OR (origin='legacy' AND (priority>=? OR json_extract(args,'$.automatic')='yes')))")
    suspended = desk.db.execute("UPDATE jobs SET status='suspended',stop_reason='pause_dossier',result=? WHERE status='pending' AND " + cond,
                                (json.dumps({'message': 'Suspendu : IA en pause sur ce dossier.'}), mid, AUTOMATIC_PRIORITY)).rowcount
    stopping = desk.db.execute("UPDATE jobs SET status='cancel_requested',stop_reason='pause_dossier' WHERE status='running' AND " + cond,
                               (mid, AUTOMATIC_PRIORITY)).rowcount
    desk.db.commit()
    return suspended, stopping


def cancel_descendants(desk, job_id):
    """Annulation terminale des enfants non achevés (récursive, bornée)."""
    stamp, seen, todo, cancelled, stopping = desk.now(), set(), [int(job_id)], 0, 0
    while todo and len(seen) < 500:
        parent = todo.pop()
        if parent in seen:
            continue
        seen.add(parent)
        children = [r[0] for r in desk.db.execute('SELECT id FROM jobs WHERE parent_job_id=?', (parent,))]
        cancelled += desk.db.execute(
            "UPDATE jobs SET status='cancelled',finished=?,result=? WHERE parent_job_id=? AND status IN ('pending','suspended')",
            (stamp, json.dumps({'message': 'Annulé avec le travail parent.'}), parent)).rowcount
        stopping += desk.db.execute("UPDATE jobs SET status='cancel_requested',stop_reason='parent' WHERE parent_job_id=? AND "
                                    "(status='running' OR (status='cancel_requested' AND stop_reason IN ('pause','pause_dossier')))", (parent,)).rowcount
        todo.extend(children)
    desk.db.commit()
    return cancelled, stopping


# ---------------------------------------------------------------- contexte du travail en cours (worker)
class job_context:
    """Déclaré par le worker autour de l'exécution d'un travail : les appels au modèle de ce fil sont rattachés au travail."""
    def __init__(self, state_dir, job_id, matter='', automatic=False):
        self.value = {'state_dir': str(state_dir or ''), 'job_id': job_id, 'matter': str(matter or ''), 'automatic': bool(automatic)}

    def __enter__(self):
        self.previous = getattr(_local, 'job', None)
        _local.job = self.value
        return self.value

    def __exit__(self, *exc):
        _local.job = self.previous
        return False


def current_job():
    return getattr(_local, 'job', None)


def reason_to_stop(state_dir=None, job=None):
    """Motif d'arrêt courant ('' si l'appel au modèle peut se faire) : annulation, pause globale ou pause du dossier."""
    job = job if job is not None else current_job()
    state_dir = (job or {}).get('state_dir') or state_dir
    if not state_dir:
        return ''
    if job and job.get('job_id'):
        code = job_stop_code(state_dir, job['job_id'])
        if code:
            return code
    paused, matters = _read(state_dir)
    if paused:
        return 'ia_en_pause'
    if job and job.get('automatic') and job.get('matter') and job['matter'] in matters:
        return 'agent_en_pause_sur_ce_dossier'
    return ''


def before_model_call(cfg):
    reason = reason_to_stop((cfg or {}).get('state_dir'))
    if reason:
        raise Stop(reason)


# ---------------------------------------------------------------- appel au modèle interruptible
def _close(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _Slot:
    """Transport d'un seul appel au modèle. 5.6.44 (audit F01) : la socket est retenue dès la connexion, car urllib détache
    ``conn.sock`` une fois les en-têtes reçus alors que le corps est encore lu sur cette même socket."""
    def __init__(self):
        self.conn = None
        self.sock = None
        self.stopped = False
        self.lock = threading.Lock()

    def attach(self, conn):
        with self.lock:
            self.conn, self.sock = conn, getattr(conn, 'sock', None)
            stopped = self.stopped
        if stopped and self.sock is not None:   # arrêt demandé avant l'ouverture : la connexion est refermée aussitôt
            _close(self.sock)

    def abort(self):
        with self.lock:
            self.stopped = True
            sock = self.sock or (getattr(self.conn, 'sock', None) if self.conn is not None else None)
        if sock is None:
            return False
        _close(sock)
        return True

    def release(self):
        with self.lock:
            self.conn = self.sock = None


def register_connection(conn):
    """Appelé par le transport HTTP à l'ouverture d'une connexion : rend l'appel du modèle de ce fil interruptible."""
    slot = getattr(_local, 'slot', None)
    if slot is not None:
        slot.attach(conn)


def interruptible(call, cfg=None, poll=POLL_SECONDS):
    """Exécute ``call()`` (un appel HTTP au modèle) en surveillant l'arrêt. Hors d'un travail : appel direct après contrôle de pause."""
    before_model_call(cfg)
    job = current_job()
    if not job or not job.get('job_id'):
        return call()
    box, slot = {}, _Slot()

    def run():
        _local.slot = slot
        try:
            box['value'] = call()
        except BaseException as error:   # noqa: BLE001 — remonté au fil appelant
            box['error'] = error
        finally:
            _local.slot = None
            slot.release()

    worker = threading.Thread(target=run, name='axiorhub-inference-%s' % job['job_id'], daemon=True)
    worker.start()
    while True:
        worker.join(poll)
        if not worker.is_alive():
            break
        try:
            reason = reason_to_stop(job=job)
        except Stop as error:
            reason = str(error)
        if reason:
            closed = slot.abort()
            worker.join(5)
            _note(job, reason, closed, worker.is_alive())
            raise Stop(reason)
    if 'error' in box:
        raise box['error']
    reason = reason_to_stop(job=job)   # le résultat d'un appel terminé après l'arrêt n'engage aucune action
    if reason:
        raise Stop(reason)
    return box.get('value')


def _note(job, reason, closed, still_alive):
    """Trace l'interruption (sans contenu) dans le journal d'activité du travail."""
    # 5.6.44 (F01) : distinguer demande d'arrêt, transport fermé et fin constatée de l'appel ; la fermeture du transport ne prouve
    # pas que le fournisseur a cessé son calcul.
    state = ('transport fermé' if closed else 'arrêt demandé avant l’ouverture du transport') + (
        ' ; appel encore en cours de fermeture' if still_alive else ' ; appel terminé côté AxiorHub') + (
        ' ; arrêt du calcul chez le fournisseur non confirmé' if closed else '')
    path = Path(job['state_dir']) / 'desk.sqlite3'
    try:
        db = sqlite3.connect(str(path), timeout=5)
        try:
            db.execute('INSERT INTO audit VALUES(NULL,?,?,?)', (time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()), 'arret5643_inference',
                       json.dumps({'job': job['job_id'], 'motif': reason, 'etat': state})))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


LABELS = {'suspended': 'Suspendu (pause)', 'cancel_requested': 'Arrêt demandé', 'cancelled': 'Annulé', 'pending': 'En attente',
          'running': 'En cours', 'done': 'Terminé', 'error': 'Erreur'}
