"""5.6.60 : la file de travail ne peut plus se figer sans le dire.

Constaté le 18 octobre 2026 sur un serveur du cabinet : 62 travaux en attente depuis trois jours (veille, indexation, surveillance
des dossiers), seuls les travaux d'agenda et de documents avançaient. Causes trouvées dans le code :

1. **Blocage en tête de file** : un travail qui a besoin du verrou général (veille, indexation…) et ne l'obtient pas, parce qu'un autre
   travail le tient, était remis en file puis repris toutes les 3 secondes, sans fin ; à chaque essai il comptait une tentative et
   écrivait « Traitement démarré ». Les travaux suivants n'avaient jamais leur tour, même ceux qui auraient pu tourner en parallèle.
   Désormais le travail attend un délai croissant (10 s à 5 min, colonne ``not_before``), sans compter de tentative, et le worker passe
   aux suivants ; le motif d'attente (« verrou tenu par … ») est noté (``blocked_by``, ``lock_waits``).
2. **Aucune durée maximale** : un travail pouvait garder le verrou des jours. La surveillance (``watchdog``) demande l'arrêt d'un travail
   qui dépasse la durée prévue pour son type ; les appels au modèle en cours sont interrompus (contrôleur d'arrêt de la 5.6.43).
3. **Questions relancées après redémarrage** : une réponse de l'assistant demandée il y a plus de 6 heures n'est plus relancée au
   redémarrage du service ; elle est marquée « question trop ancienne : reposez-la ».
4. **Veille programmée en fin de file** : elle passe désormais devant les travaux automatiques de maintenance (priorité 29) ; un passage
   resté orphelin d'un créneau dépassé est clos (« remplacé par le passage suivant ») au lieu d'afficher « Collecte en file » indéfiniment.

``diagnose`` relève tous les blocages connus (pause de l'IA, verrou tenu trop longtemps, travaux trop longs, arrêts non confirmés,
attente du verrou, workers silencieux, file pleine, file figée, échecs répétés, veille orpheline) ; il sert à « L'agent en un coup
d'œil » et à la commande ``manage.py diagnostic-file`` (lecture seule, utilisable sur le serveur).
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

# Durée maximale d'un travail en cours, par type (minutes). Au-delà, l'arrêt est demandé.
LIMITS = {'assistant_answer': 20, 'prepare_reply': 30, 'prepare_draft': 30, 'live_mail430': 45, 'run': 45,
          'veille5632_collect': 60, 'briefing_oral5632': 30, 'routine520': 60,
          'docrequest520': 240, 'prepare_document_project': 240, 'prepare_hearing': 240, 'prepare_word_project': 240,
          'prepare_legal_opinion': 240, 'prepare_transcript_report': 240, 'task5614': 240, 'mission567': 240,
          'index': 360, 'index_all': 360, 'discover': 360, 'digest_pieces5632': 360, 'actualiser5655': 360, 'fiche_dossier5627': 240,
          'analyser_dossier5624': 360, 'search_index490': 360}
DEFAULT_LIMIT = 180
STALE_QUESTION_HOURS = 6
BACKOFF_MIN, BACKOFF_MAX = 10, 300
RESERVE_AFTER = 3            # 5.6.61 : après 3 attentes du verrou, le travail le réserve (plus de nouveau travail à verrou partagé avant lui)
SEVERITY = {'bloquant': 'Bloquant', 'attention': 'À surveiller', 'info': 'Information'}


def now_utc():
    return datetime.now(timezone.utc)


def _parse(stamp):
    try:
        value = datetime.fromisoformat(str(stamp))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_minutes(stamp, ref=None):
    value = _parse(stamp)
    return None if value is None else max(0.0, ((ref or now_utc()) - value).total_seconds() / 60)


def limit_for(kind):
    return LIMITS.get(kind, DEFAULT_LIMIT)


def backoff_seconds(waits):
    return int(min(BACKOFF_MAX, BACKOFF_MIN * (2 ** max(0, min(int(waits or 0), 6)))))


def holder(state_dir):
    """Qui tient (ou tenait en dernier) le verrou général, d'après ``run.lock.json`` ; ``held`` si un verrou exclusif est en place."""
    out = {'holder': '', 'since': '', 'pid': None, 'alive': None, 'exclusive': False}
    try:
        data = json.loads((Path(state_dir) / 'run.lock.json').read_text(encoding='utf-8'))
        out.update({'holder': str(data.get('holder') or ''), 'since': str(data.get('since') or ''), 'pid': data.get('pid')})
    except (OSError, ValueError):
        pass
    if isinstance(out['pid'], int):
        try:
            os.kill(out['pid'], 0)
            out['alive'] = True
        except PermissionError:
            out['alive'] = True
        except (OSError, ValueError):
            out['alive'] = False
    try:
        from .portable import fcntl
        with open(Path(state_dir) / 'run.lock', 'a') as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                out['exclusive'] = True
    except OSError:
        pass
    return out


# ------------------------------------------------------------------------------------------------ surveillance des durées
def watchdog(desk, ref=None):
    """Demande l'arrêt des travaux en cours depuis plus longtemps que la durée prévue pour leur type. Renvoie les identifiants."""
    ref = ref or now_utc()
    stopped = []
    for row in desk.db.execute("SELECT id,kind,started FROM jobs WHERE status='running' AND started IS NOT NULL").fetchall():
        age = _age_minutes(row['started'], ref)
        if age is None or age <= limit_for(row['kind']):
            continue
        message = 'Arrêt automatique : le travail dépassait la durée prévue (%d min) ; relancez-le si nécessaire.' % limit_for(row['kind'])
        changed = desk.db.execute("UPDATE jobs SET status='cancel_requested',stop_reason='delai_depasse',result=? WHERE id=? AND status='running'",
                                  (json.dumps({'message': message, 'erreur': 'duree_maximale_depassee'}, ensure_ascii=False), row['id'])).rowcount
        if changed:
            stopped.append(row['id'])
    if stopped:
        desk.db.commit()
        try:
            desk.audit('travaux_arretes_duree_maximale', {'jobs': stopped})
            from .live430 import emit
            for jid in stopped:
                emit(desk, 'cancel_requested', 'Arrêt automatique : durée maximale dépassée.', jid)
        except Exception:   # noqa: BLE001 — l'arrêt est déjà enregistré
            pass
    return stopped


# ------------------------------------------------------------------------------------------------ diagnostic
def diagnose(db, state_dir, ref=None):
    """Blocages connus, du plus grave au moins grave. ``db`` : connexion sqlite3 (``row_factory = sqlite3.Row``), lecture seule suffit."""
    ref = ref or now_utc()
    out = []

    def add(code, severity, title, detail='', job=None, action=''):
        out.append({'code': code, 'severity': severity, 'title': title, 'detail': detail, 'job': job, 'action': action})

    def one(sql, params=()):
        try:
            return db.execute(sql, params).fetchone()
        except sqlite3.Error:
            return None

    def rows(sql, params=()):
        try:
            return db.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

    paused = one("SELECT value FROM settings WHERE key='missions567:pause'")
    try:
        if paused and json.loads(paused[0]):
            add('pause_ia', 'bloquant', 'L’IA est en pause pour tout le cabinet', 'Aucun travail utilisant l’IA ne démarre tant que la pause dure.',
                action='Reprendre l’IA depuis Aujourd’hui ou Paramètres.')
    except (TypeError, ValueError):
        pass
    h = holder(state_dir)
    if h['since']:
        held_for = _age_minutes(h['since'], ref) or 0
        running_holder = h['holder'].startswith('Travail n°')
        if h['exclusive'] and held_for > 60:
            add('verrou_long', 'bloquant', 'Le verrou général est tenu depuis %d min' % held_for,
                'Par : %s%s. Les travaux qui en ont besoin attendent.' % (h['holder'] or 'inconnu', (' (processus %s)' % h['pid']) if h['pid'] else ''),
                action='Arrêter le travail concerné, ou redémarrer le service qui le tient.')
        elif h['alive'] is False and running_holder and held_for > 60:
            add('verrou_orphelin', 'info', 'Dernier détenteur du verrou arrêté', '%s, processus %s disparu : le verrou est libre.' % (h['holder'], h['pid']))
    for r in rows("SELECT id,kind,started,worker FROM jobs WHERE status='running' AND started IS NOT NULL ORDER BY started"):
        age = _age_minutes(r['started'], ref)
        if age is not None and age > limit_for(r['kind']):
            add('travail_trop_long', 'bloquant', 'Travail n° %s (%s) en cours depuis %d min' % (r['id'], r['kind'], age),
                'Durée prévue : %d min. Il bloque les travaux qui attendent le même verrou.' % limit_for(r['kind']), r['id'], 'Arrêter ce travail.')
        elif age is not None and age > 30:
            add('travail_long', 'attention', 'Travail n° %s (%s) en cours depuis %d min' % (r['id'], r['kind'], age), job=r['id'])
    for r in rows("SELECT id,kind,started,finished FROM jobs WHERE status='cancel_requested'"):
        age = _age_minutes(r['started'], ref)
        if age is not None and age > 5:
            add('arret_non_confirme', 'bloquant', 'Arrêt demandé mais non confirmé : travail n° %s (%s)' % (r['id'], r['kind']),
                'Le travail ne répond pas à la demande d’arrêt.', r['id'],
                'Redémarrer les workers : sudo systemctl restart axiorhub-mail-desk-worker.service axiorhub-mail-desk-worker@2.service')
    cols = {c[1] for c in rows('PRAGMA table_info(jobs)')}
    if 'lock_waits' in cols:
        for r in rows("SELECT id,kind,lock_waits,blocked_by,created FROM jobs WHERE status='pending' AND lock_waits>=20 ORDER BY lock_waits DESC LIMIT 5"):
            add('attente_verrou', 'attention', 'Travail n° %s (%s) attend le verrou (%d essais)' % (r['id'], r['kind'], r['lock_waits']),
                'Tenu par : %s.' % (r['blocked_by'] or 'inconnu'), r['id'])
    for name, beat in [(r['name'], r['heartbeat']) for r in rows("SELECT name,heartbeat FROM live_services_v430 WHERE name LIKE 'worker-%'")]:
        try:
            silent = ref.timestamp() - float(beat)
        except (TypeError, ValueError):
            continue
        if silent > 180:
            add('worker_silencieux', 'bloquant', '%s silencieux depuis %d min' % (name.replace('worker-', 'Worker '), silent // 60),
                'Aucun signe de vie : il ne prend plus de travaux.', action='Redémarrer les workers.')
    pending = (one("SELECT COUNT(*) FROM jobs WHERE status='pending'") or [0])[0]
    automatic = (one("SELECT COUNT(*) FROM jobs WHERE status='pending' AND priority>=30") or [0])[0]
    if automatic >= 55:
        add('file_pleine', 'attention', 'File des travaux automatiques presque pleine (%d / 60)' % automatic,
            'Au-delà, les nouveaux travaux automatiques sont refusés jusqu’à ce que la file se vide.')
    since = (ref - timedelta(hours=6)).isoformat()
    finished = (one("SELECT COUNT(*) FROM jobs WHERE finished>=? AND status IN ('done','error','cancelled')", (since,)) or [0])[0]
    oldest = one("SELECT MIN(created) FROM jobs WHERE status='pending'")
    oldest_age = _age_minutes(oldest[0], ref) if oldest and oldest[0] else None
    if pending and finished == 0 and oldest_age is not None and oldest_age > 360:
        add('file_figee', 'bloquant', 'File figée : %d travaux en attente, aucun terminé en 6 h' % pending,
            'Le plus ancien attend depuis %d h.' % (oldest_age // 60), action='Voir les autres constats ci-dessus ; à défaut, redémarrer les workers.')
    user_wait = one("SELECT MIN(created) FROM jobs WHERE status='pending' AND priority<30")
    user_age = _age_minutes(user_wait[0], ref) if user_wait and user_wait[0] else None
    if user_age is not None and user_age > 15:
        add('demande_en_attente', 'attention', 'Une demande attend depuis %d min' % user_age, 'Demandes de l’avocat (priorité haute) non démarrées.')
    for r in rows("SELECT kind,COUNT(*) n,MAX(finished) last FROM jobs WHERE status='error' AND finished>=? GROUP BY kind HAVING n>=3 ORDER BY n DESC LIMIT 5",
                  ((ref - timedelta(hours=24)).isoformat(),)):
        add('echecs_repetes', 'attention', '%d échecs de « %s » en 24 h' % (r['n'], r['kind']), 'Dernier : %s.' % str(r['last'])[:16].replace('T', ' '))
    for r in rows("SELECT id,owner,slot FROM veille_runs5632 WHERE state='pending' AND COALESCE(job_id,'')='' ORDER BY id"):
        add('veille_orpheline', 'info', 'Passage de veille sans travail : %s' % r['slot'], 'Il sera clos ou relancé au prochain cycle.')
    order = {'bloquant': 0, 'attention': 1, 'info': 2}
    out.sort(key=lambda f: order[f['severity']])
    return out


def text_report(findings):
    if not findings:
        return 'Aucun blocage détecté : la file avance normalement.'
    lines = []
    for f in findings:
        lines.append('[%s] %s' % (SEVERITY[f['severity']], f['title']))
        if f['detail']:
            lines.append('    ' + f['detail'])
        if f['action']:
            lines.append('    → ' + f['action'])
    return '\n'.join(lines)


def html(desk, prefix=''):
    from html import escape as e
    try:
        findings = diagnose(desk.db, desk.c['state_dir'])
    except Exception:   # noqa: BLE001 — le diagnostic ne doit jamais masquer la page
        return ''
    if not findings:
        return '<p class="ok b60-ok" role="status">Aucun blocage détecté.</p>'
    items = []
    for f in findings:
        act = ''
        if f['job'] and f['code'] in ('travail_trop_long', 'travail_long', 'attente_verrou'):
            route = 'file/arreter' if f['code'] != 'attente_verrou' else 'file/relancer'
            label = 'Arrêter ce travail' if f['code'] != 'attente_verrou' else 'Réessayer maintenant'
            act = ('<form class="m5-form d51-inline" data-api="m5660/%s" data-reload><input type="hidden" name="job" value="%s">'
                   '<button class="ax-btn ghost" type="submit">%s</button><p role="status" class="m5-status"></p></form>') % (route, e(str(f['job']), quote=True), e(label))
        items.append('<li class="b60-%s"><span class="d51-badge">%s</span> <strong>%s</strong>%s%s%s</li>' % (
            e(f['severity']), e(SEVERITY[f['severity']]), e(f['title']), ('<div class="ax-muted">%s</div>' % e(f['detail'])) if f['detail'] else '',
            ('<div>→ %s</div>' % e(f['action'])) if f['action'] else '', act))
    return '<div class="b60" role="status"><h3>Blocages détectés</h3><ul class="b60-list">%s</ul></div>' % ''.join(items)


# ------------------------------------------------------------------------------------------------ actions
def retry_now(desk, job_id):
    changed = desk.db.execute("UPDATE jobs SET not_before=NULL WHERE id=? AND status='pending'", (int(job_id),)).rowcount
    desk.db.commit()
    return {'ok': bool(changed), 'message': 'Le travail sera repris dès que le verrou sera libre.' if changed else 'Ce travail n’est plus en attente.'}


def close_orphan_veille(desk):
    """Passages de veille « en attente » d'un créneau dépassé, sans travail vivant, alors qu'un passage plus récent existe : clos."""
    closed = 0
    try:
        from .veille5632 import _job_active
        for r in desk.db.execute("SELECT * FROM veille_runs5632 WHERE state='pending' ORDER BY id").fetchall():
            newer = desk.db.execute('SELECT 1 FROM veille_runs5632 WHERE owner=? AND id>?', (r['owner'], r['id'])).fetchone()
            if newer and not _job_active(desk, r):
                closed += desk.db.execute("UPDATE veille_runs5632 SET state='cancelled',error='remplace_par_le_passage_suivant' WHERE id=? AND state='pending'",
                                          (r['id'],)).rowcount
        if closed:
            desk.db.commit()
    except sqlite3.Error:
        pass
    return closed
