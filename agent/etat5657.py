"""5.6.57 (parcours 1, A01) : états réels des travaux de l'agent et vue d'ensemble « L'agent en un coup d'œil ».

« En file » ou « terminé » ne disaient pas si un projet était prêt, si une question bloquait un passage ou si un échec allait être
repris. L'état métier est déduit du travail lui-même (statut, résultat enregistré, règle de reprise) — jamais d'un texte du modèle :

- ``en_file`` (en attente du worker), ``en_cours``, ``suspendu`` (pause de l'IA), ``arret_demande`` ;
- ``projet_pret`` (projet ou document produit), ``question`` (une décision de l'avocat bloque un passage ; le reste est prêt),
  ``termine`` (rien à relire) ;
- ``echec_reprise`` (interruption reprise automatiquement), ``echec`` (à relancer par l'avocat).

La vue d'ensemble (page « Pourquoi rien n'est produit ? ») réunit ce qui manquait pour diagnostiquer une file arrêtée : signe de vie du
worker, travail en cours, plus ancien travail en attente, nombre par type, échecs du jour, collectes de veille orphelines, erreurs du
modèle et taille de contexte réglée. Lecture seule.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json
import time

LABELS = {'en_file': 'En file', 'en_cours': 'En cours', 'suspendu': 'Suspendu (pause de l’IA)', 'arret_demande': 'Arrêt demandé',
          'projet_pret': 'Projet prêt', 'question': 'Une question bloque ce passage', 'termine': 'Terminé',
          'echec_reprise': 'Échec, nouvel essai prévu', 'echec': 'Échec — à relancer'}
MODEL_CODES = ('modele', 'ollama', 'ia_', 'llm', 'contexte', 'json_invalide', 'reponse_modele', 'delai')


def _result(raw):
    try:
        v = json.loads(raw or '{}')
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def business_state(kind, status, result=None, priority=None):
    """(code, libellé) d'un travail ; ``result`` = dictionnaire ou JSON enregistré."""
    res = result if isinstance(result, dict) else _result(result)
    if status == 'pending':
        code = 'en_file'
    elif status == 'running':
        code = 'en_cours'
    elif status == 'suspended':
        code = 'suspendu'
    elif status == 'cancel_requested':
        code = 'arret_demande'
    elif status == 'error':
        from . import queue521
        interrupted = res.get('erreur') == queue521.INTERRUPTED
        code = 'echec_reprise' if interrupted and queue521.automatic(kind, priority) else 'echec'
    elif status == 'done':
        blocked = bool(res.get('requires_decision') or res.get('decision_requise') or (res.get('projet_prepare') and res.get('depot_autorise') is False))
        produced = bool(res.get('projet_prepare') or res.get('path') or res.get('document') or res.get('draft') or res.get('deposited')
                        or res.get('depose') or res.get('fichier'))
        code = 'question' if blocked else ('projet_pret' if produced else 'termine')
    else:
        code = 'termine'
    return code, LABELS[code]


def _age(seconds):
    s = int(max(0, seconds or 0))
    if s < 90:
        return '%d s' % s
    if s < 5400:
        return '%d min' % (s // 60)
    if s < 172800:
        return '%d h' % (s // 3600)
    return '%d j' % (s // 86400)


def _since(stamp, now):
    try:
        dt = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds()
    except (TypeError, ValueError):
        return None


def summary(desk, now=None):
    from .web import JOB_LABELS
    now = now or datetime.now(timezone.utc)
    label = lambda k: JOB_LABELS.get(k, k)
    out = {'worker_age': None, 'current': None, 'oldest': None, 'pending': [], 'errors': [], 'veille_orphans': 0, 'model_errors': [],
           'context': {}, 'problems': []}
    try:
        beats = [float(r[0] or 0) for r in desk.db.execute("SELECT heartbeat FROM live_services_v430 WHERE name LIKE 'worker-%'")]
        if beats:
            out['worker_age'] = time.time() - max(beats)
    except Exception:   # noqa: BLE001 — table des signes de vie absente
        pass
    cur = desk.db.execute("SELECT id,kind,args,started,created FROM jobs WHERE status IN ('running','cancel_requested') ORDER BY started LIMIT 1").fetchone()
    if cur:
        last = None
        try:
            last = desk.db.execute('SELECT message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC LIMIT 1', (cur['id'],)).fetchone()
        except Exception:   # noqa: BLE001
            pass
        out['current'] = {'job': cur['id'], 'label': label(cur['kind']), 'age': _since(cur['started'] or cur['created'], now), 'detail': last[0] if last else ''}
    old = desk.db.execute("SELECT id,kind,created FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
    if old:
        out['oldest'] = {'job': old['id'], 'label': label(old['kind']), 'age': _since(old['created'], now)}
    out['pending'] = [{'label': label(r[0]), 'n': r[1]} for r in desk.db.execute(
        "SELECT kind,COUNT(*) FROM jobs WHERE status='pending' GROUP BY kind ORDER BY COUNT(*) DESC LIMIT 12")]
    since = (now - timedelta(hours=24)).isoformat()
    codes = {}
    for r in desk.db.execute("SELECT kind,result FROM jobs WHERE status='error' AND finished>=?", (since,)):
        code = str(_result(r['result']).get('erreur') or 'action_interrompue')
        codes.setdefault((label(r['kind']), code), 0)
        codes[(label(r['kind']), code)] += 1
        if any(m in code for m in MODEL_CODES):
            out['model_errors'].append(code)
    out['errors'] = [{'label': k[0], 'code': k[1], 'n': n} for k, n in sorted(codes.items(), key=lambda kv: -kv[1])[:10]]
    try:
        from . import veille5632
        veille5632.schema(desk)
        for run in desk.db.execute("SELECT * FROM veille_runs5632 WHERE state='pending'").fetchall():
            if not veille5632._job_active(desk, run):           # pylint: disable=protected-access
                out['veille_orphans'] += 1
    except Exception:   # noqa: BLE001
        pass
    oll = desk.c.get('ollama') or {}
    out['context'] = {'num_ctx': oll.get('num_ctx'), 'max_context_chars': oll.get('max_context_chars', 65000), 'model': oll.get('model', '')}
    if out['worker_age'] is not None and out['worker_age'] > 600:
        out['problems'].append('Le worker n’a pas donné signe de vie depuis %s : les travaux restent en file.' % _age(out['worker_age']))
    if out['oldest'] and (out['oldest']['age'] or 0) > 3600 and not out['current']:
        out['problems'].append('Des travaux attendent depuis %s sans qu’aucun ne soit en cours.' % _age(out['oldest']['age']))
    if out['current'] and (out['current']['age'] or 0) > 1800:
        out['problems'].append('« %s » est en cours depuis %s : il retient les travaux suivants.' % (out['current']['label'], _age(out['current']['age'])))
    if out['veille_orphans']:
        out['problems'].append('%d collecte(s) de veille en attente sans travail : « Actualiser » dans la veille les reprend.' % out['veille_orphans'])
    if out['model_errors']:
        out['problems'].append('%d erreur(s) du modèle d’IA en 24 h : testez l’IA ci-dessous.' % len(out['model_errors']))
    return out


def html(desk, prefix):
    s = summary(desk)
    worker = ('<span class="ok">signe de vie il y a %s</span>' % e(_age(s['worker_age']))) if s['worker_age'] is not None and s['worker_age'] <= 600 else (
        '<span class="ko">silencieux depuis %s</span>' % e(_age(s['worker_age'])) if s['worker_age'] is not None else '<span class="ax-muted">aucun signal enregistré</span>')
    cur = ('%s — depuis %s%s' % (e(s['current']['label']), e(_age(s['current']['age'])), (' · ' + e(s['current']['detail'])) if s['current']['detail'] else '')
           if s['current'] else 'aucun')
    oldest = ('%s — depuis %s' % (e(s['oldest']['label']), e(_age(s['oldest']['age'])))) if s['oldest'] else 'aucun'
    pend = ''.join('<li>%s : %d</li>' % (e(p['label']), p['n']) for p in s['pending']) or '<li class="ax-muted">Aucun travail en attente.</li>'
    errs = ''.join('<li>%s — %s (%d)</li>' % (e(x['label']), e(x['code']), x['n']) for x in s['errors']) or '<li class="ok">Aucun échec en 24 h.</li>'
    probs = ''.join('<li>%s</li>' % e(p) for p in s['problems'])
    ctx = s['context']
    return ('<section class="ax-card e5657" id="agent-etat"><h2>L’agent en un coup d’œil</h2>%s<dl class="e5657-grid">'
            '<div><dt>Worker</dt><dd>%s</dd></div><div><dt>Travail en cours</dt><dd>%s</dd></div><div><dt>Plus ancien travail en attente</dt><dd>%s</dd></div>'
            '<div><dt>Collectes de veille orphelines</dt><dd>%d</dd></div><div><dt>Modèle et contexte</dt><dd>%s · contexte %s · %s caractères au plus</dd></div></dl>'
            '<details><summary>En attente par type</summary><ul>%s</ul></details><details><summary>Échecs des 24 dernières heures</summary><ul>%s</ul></details>'
            '%s</section>') % (
        ('<ul class="e5657-problems" role="status">%s</ul>' % probs) if probs else '<p class="ok" role="status">La file avance normalement.</p>',
        worker, cur, oldest, s['veille_orphans'], e(str(ctx.get('model') or 'modèle non renseigné')), e(str(ctx.get('num_ctx') or 'automatique')),
        e(str(ctx.get('max_context_chars'))), pend, errs, _securite(desk, prefix))


def _securite(desk, prefix):
    try:   # 5.6.59 : alertes de sécurité de l'IA
        from .web5659 import overview_line
        return overview_line(desk, prefix)
    except Exception:   # noqa: BLE001
        return ''
