"""Concurrence bornée des producteurs isolés et reprise des seuls travaux rejouables."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from .common import Stop

READ568={'document568_scan','document568_compile','sent568_scan','received568_scan','proactive568_cycle','news568_collect'}
SCOPED = {'docrequest520', 'prepare_reply', 'assistant_answer'} | READ568
REPLAY_SAFE = SCOPED | {'live_calendar430', 'live_documents430','followup568_prepare','talk568_prepare','document568_run'}


@contextmanager
def producer_lock(config, kind, args):
    if kind not in SCOPED:
        from .desk import run_lock
        with run_lock(config):
            yield
        return
    scope = str(args.get('matter') or args.get('matter_id') or args.get('key') or args.get('thread') or 'cabinet')
    if kind in READ568:scope='read568:'+kind+':'+str(args.get('owner') or 'cabinet')
    locks = Path(config['state_dir']) / 'producer-locks567'
    locks.mkdir(mode=0o700, exist_ok=True)
    path = locks / (hashlib.sha256(scope.encode()).hexdigest() + '.lock')
    with open(Path(config['state_dir']) / 'run.lock','a') as global_lock, path.open('a') as resource:
        try:
            fcntl.flock(global_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(resource, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('traitement_deja_en_cours') from None
        yield


def recover(desk, worker_id):
    rows=desk.db.execute("SELECT id,kind,status,args,attempts FROM jobs WHERE status IN ('running','cancel_requested') AND (worker=? OR (worker IS NULL AND ?='1'))",(str(worker_id),str(worker_id))).fetchall()
    counts={'resumed':0,'cancelled':0,'review':0}
    for row in rows:
        if row['status']=='cancel_requested':
            state,result='cancelled',{'message':'Arrêt confirmé au redémarrage ; les dépôts déjà effectués restent conservés.'};counts['cancelled']+=1
        elif row['kind'] in REPLAY_SAFE and int(row['attempts'] or 0)<3:
            state,result='pending',None;counts['resumed']+=1
        else:
            state,result='error',{'erreur':'service_redemarre_verifier_avant_relance'};counts['review']+=1
        desk.db.execute('UPDATE jobs SET status=?,finished=?,result=? WHERE id=?',(state,None if state=='pending' else desk.now(),json.dumps(result) if result else None,row['id']))
    desk.db.commit()
    if rows:desk.audit('reprise567_apres_redemarrage',counts)
    return counts
