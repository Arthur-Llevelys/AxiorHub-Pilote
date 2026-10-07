"""5.6.9 : régime économe.

Moins d'appels au modèle pour les travaux automatiques (contrôles périodiques espacés, quota journalier d'analyses automatiques),
contrôle par un second modèle réservé aux textes destinés à des tiers, et comptabilité des jetons — locaux comme externes — par
fonction. Activé d'office ; l'avocat revient au régime complet dans « IA externe sûre ».
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json

from .common import HTTP, Stop

SETTING = 'ai:economy_enabled'
QUOTA_SETTING = 'ai:economy_daily_jobs'
DEFAULT_QUOTA = 20
# Minutes entre deux contrôles périodiques : (régime complet, régime économe).
INTERVALS = {'health': (30, 120), 'refresh_cabinet_pilotage': (30, 120), 'run_continuous_business_tests': (60, 1440),
             'production_cycle391': (5, 15), 'classify_portfolio': (360, 1440), 'reconcile_inbox': (15, 60),
             'monitor_all': (30, 120), 'index_all': (360, 1440)}
# Travaux automatiques qui appellent le modèle : soumis au quota journalier (jamais les demandes de l'avocat).
AUTOMATIC_LLM = frozenset(('refresh_brief', 'sync_legal_memory', 'extract_facts460', 'refresh_operational_memory',
                           'analyze_notice440', 'analyze_deadline450'))
# Contrôles par un second modèle dispensés en régime économe : analyses internes relues par l'avocat, jamais envoyées telles quelles.
# 5.6.12 (audit F09) : le contrôle reste proportionné au risque juridique — audience et stratégie restent contrôlées.
INTERNAL_REVIEWS = frozenset(('meeting_preparation', 'transcript_report', 'assistant_answer'))
# Banc rapide d'un modèle candidat : respect du JSON, fiabilité d'une date, tri d'un courriel. Aucune donnée de dossier.
BENCH = (
    ('json', 'Réponds uniquement par un objet JSON avec les clés "categorie" (texte) et "urgent" (vrai ou faux), sans autre texte, pour ce '
             'courriel : « Bonjour Maître, l’audience est fixée au 12 novembre 2026, merci de confirmer votre présence. »'),
    ('date', 'Quelle est la date, au format AAAA-MM-JJ et rien d’autre, mentionnée dans la phrase : « le délai expire le 3 mars 2027 » ?'),
    ('tri', 'Classe ce courriel par un seul mot parmi : publicite, procedure, client. Courriel : « -70 % sur toute la collection, dernière chance '
            'ce week-end ! » Réponds par le mot seul.'),
)


def enabled(desk):
    return bool(desk.settings(SETTING, True))


def quota(desk):
    try:
        return max(1, min(int(desk.settings(QUOTA_SETTING, DEFAULT_QUOTA)), 500))
    except (TypeError, ValueError):
        return DEFAULT_QUOTA


def interval(desk, job, minutes):
    """Minutes entre deux lancements d'un contrôle périodique ; en régime économe, jamais moins que la valeur économe."""
    if not enabled(desk) or job not in INTERVALS:
        return int(minutes)
    return max(int(minutes), INTERVALS[job][1])


def control_required(desk, kind):
    return not (enabled(desk) and kind in INTERNAL_REVIEWS)


def skipped_control(kind):
    """Résultat de contrôle explicite : rien n'a été vérifié par un second modèle, l'avocat relit."""
    return {'status': 'skipped', 'skipped': 'regime_econome', 'kind': kind, 'blocking_reasons': [], 'all_sources_known': True,
            'no_external_action': True, 'recommendation': 'unavailable', 'agree': False, 'unsupported_claims': [], 'omissions': [],
            'risks': ['Contrôle par un second modèle non effectué (régime économe) : relecture par l’avocat.']}


def _day_start():
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def used_today(desk):
    kinds = sorted(AUTOMATIC_LLM)
    return desk.db.execute('SELECT COUNT(*) FROM jobs WHERE created>=? AND kind IN (%s)' % ','.join('?' * len(kinds)),
                           (_day_start(), *kinds)).fetchone()[0]


def quota_ok(desk, kind):
    if not enabled(desk) or kind not in AUTOMATIC_LLM:
        return True
    return used_today(desk) < quota(desk)


DEFERRED_SCHEMA = '''CREATE TABLE IF NOT EXISTS deferred_jobs569(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, args TEXT NOT NULL, matter TEXT NOT NULL, reason TEXT NOT NULL,
 created TEXT NOT NULL, resume_after TEXT NOT NULL);'''


def _deferred_schema(desk):
    desk.db.executescript(DEFERRED_SCHEMA)
    desk.db.commit()


def defer(desk, kind, args, reason='quota_econome_journalier'):
    """5.6.11 (audit F07) : une analyse reportée est conservée comme travail différé, reprise le lendemain même sans nouveau changement."""
    from .common import digest
    _deferred_schema(desk)
    args = dict(args or {})
    key = digest('deferred569|' + kind + '|' + json.dumps(args, sort_keys=True, ensure_ascii=False))
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).isoformat()
    desk.db.execute('INSERT OR IGNORE INTO deferred_jobs569 VALUES(?,?,?,?,?,?,?)',
                    (key, kind, json.dumps(args, ensure_ascii=False), str(args.get('matter') or ''), reason, desk.now(), tomorrow))
    desk.db.commit()
    return key


def deferred(desk):
    _deferred_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM deferred_jobs569 ORDER BY resume_after,created LIMIT 200')]


def resume_deferred(desk, limit=20):
    """Relance les travaux différés arrivés à échéance, dans la limite du quota du jour ; les autres attendent le lendemain."""
    _deferred_schema(desk)
    now = datetime.now(timezone.utc).isoformat()
    resumed = 0
    for r in desk.db.execute('SELECT * FROM deferred_jobs569 WHERE resume_after<=? ORDER BY resume_after,created LIMIT ?', (now, limit)).fetchall():
        if not quota_ok(desk, r['kind']):
            break
        try:
            desk.enqueue(r['kind'], json.loads(r['args']), priority=55)
        except Stop:
            continue
        desk.db.execute('DELETE FROM deferred_jobs569 WHERE id=?', (r['id'],))
        desk.db.commit()
        resumed += 1
    return resumed


def _ensure(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(ai_usage_v391)')}
    if 'duration_ms' not in columns:
        db.execute('ALTER TABLE ai_usage_v391 ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0')
        db.commit()


def record_local_usage(cfg, result):
    """Jetons et durée d'une génération Ollama (compteurs renvoyés par /api/chat) ; aucun texte conservé."""
    state_dir = cfg.get('state_dir') if isinstance(cfg, dict) else ''
    if not state_dir or not isinstance(result, dict):
        return
    from .ai_gateway import _usage_db
    db = _usage_db(state_dir)
    try:
        _ensure(db)
        db.execute('INSERT INTO ai_usage_v391(at,provider,purpose,model,input_tokens,output_tokens,estimated_cost_usd,status,duration_ms)'
                   ' VALUES(?,?,?,?,?,?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), 'ollama', str(cfg.get('purpose') or ''), str(cfg.get('model') or ''),
                    int(result.get('prompt_eval_count') or 0), int(result.get('eval_count') or 0), 0.0, 'done',
                    int(result.get('total_duration') or 0) // 1_000_000))
        db.commit()
    finally:
        db.close()


def consumption(desk, days=7):
    """Par fonction et fournisseur : requêtes, jetons entrés/sortis, minutes de génération, coût estimé (externe)."""
    from .ai_gateway import PURPOSES, _usage_db
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 90)))).isoformat()
    db = _usage_db(desk.c['state_dir'])
    try:
        _ensure(db)
        rows = db.execute('SELECT purpose,provider,COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'
                          'COALESCE(SUM(duration_ms),0),ROUND(COALESCE(SUM(estimated_cost_usd),0),4) FROM ai_usage_v391 WHERE at>=? '
                          'GROUP BY purpose,provider ORDER BY 4+5 DESC', (since,)).fetchall()
    finally:
        db.close()
    out = []
    for purpose, provider, n, inp, outp, ms, cost in rows:
        out.append({'purpose': purpose, 'label': PURPOSES.get(purpose, (purpose or 'autre', ''))[0], 'provider': provider,
                    'requests': int(n), 'input_tokens': int(inp), 'output_tokens': int(outp), 'minutes': round(ms / 60000, 1),
                    'cost_usd': float(cost), 'external': provider != 'ollama'})
    return out


def model_profile(desk):
    """Plus petit modèle de conversation installé dans Ollama (hors modèles d'empreintes) pour le tri et le contrôle."""
    cfg = desk.c.get('ollama') or {}
    try:
        tags = HTTP(str(cfg.get('url') or 'http://127.0.0.1:11434'), local_only=True, timeout=5).json('GET', '/api/tags')
    except (Stop, KeyError) as ex:
        return {'available': False, 'reason': str(ex), 'suggested': '', 'models': []}
    models = [(int(x.get('size') or 0), str(x.get('name') or x.get('model') or '')) for x in tags.get('models', []) if isinstance(x, dict)]
    models = [m for m in models if m[1] and 'embed' not in m[1].lower() and not m[1].endswith('-cloud') and ':cloud' not in m[1]]
    if not models:
        return {'available': False, 'reason': 'aucun_modele_installe', 'suggested': '', 'models': []}
    size, name = min(models)
    return {'available': True, 'suggested': name, 'size_gb': round(size / 1e9, 1), 'current': str(cfg.get('model') or ''),
            'models': [{'name': n, 'size_gb': round(s / 1e9, 1)} for s, n in sorted(models)]}


def _bench_check(name, answer):
    text = str(answer or '').strip()
    if name == 'json':
        body = text.strip('`').strip()
        if body.lower().startswith('json'):
            body = body[4:].strip()
        try:
            data = json.loads(body[body.index('{'):body.rindex('}') + 1])
        except (ValueError, TypeError):
            return False
        return isinstance(data, dict) and isinstance(data.get('categorie'), str) and isinstance(data.get('urgent'), bool)
    if name == 'date':
        return '2027-03-03' in text
    if name == 'tri':
        from .common import fold
        folded = fold(text)
        return 'publicite' in folded and 'procedure' not in folded and 'client' not in folded
    return False


def bench_model(desk, name, model=None):
    """5.6.12 (audit F09) : un modèle candidat doit passer trois épreuves déterministes avant de recevoir le tri et le contrôle."""
    import time
    name = str(name or '').strip()
    if not name:
        raise Stop('modele_requis')
    if model is None:
        from .model import Model, routed_config
        base = routed_config(desk.c, 'mail_triage')
        if base.get('provider_type', 'ollama') != 'ollama':
            raise Stop('banc_modele_local_requis')
        model = Model({**base, 'model': name, 'purpose': 'bench569'})
    results = []
    for key, prompt in BENCH:
        started = time.monotonic()
        try:
            answer = model.complete([{'role': 'user', 'content': prompt}], temperature=0, max_tokens=80)
            ok = _bench_check(key, answer)
            error = ''
        except Stop as ex:
            answer, ok, error = '', False, str(ex)
        results.append({'test': key, 'ok': bool(ok), 'answer': str(answer)[:200], 'error': error, 'seconds': round(time.monotonic() - started, 1)})
    report = {'model': name, 'at': desk.now(), 'passed': all(r['ok'] for r in results), 'results': results}
    desk.setting('ai:economy_bench:' + name, report)
    desk.audit('economie569_banc_modele', {'model': name, 'passed': report['passed'], 'failed': [r['test'] for r in results if not r['ok']]})
    report['message'] = ('Banc réussi pour %s : JSON, date et tri corrects.' % name) if report['passed'] else \
        'Banc non réussi pour %s : %s. Ce modèle ne reçoit pas le tri ni le contrôle.' % (name, ', '.join(r['test'] for r in results if not r['ok']))
    return report


def bench_status(desk, name):
    return desk.settings('ai:economy_bench:' + str(name or ''), None)


def apply_model_profile(desk, force=False):
    """Route le tri, la lecture des pièces jointes, la conversation vocale et, s'il est distinct du rédacteur, le contrôle vers le plus
    petit modèle local — à condition qu'il ait passé le banc (5.6.12)."""
    from .ai_gateway import PURPOSES
    from .ia540 import _write_routes
    from .model import routed_config
    profile = model_profile(desk)
    if not profile['available']:
        raise Stop('profil_modeles_indisponible')
    name = profile['suggested']
    bench = bench_status(desk, name)
    if not force and not (bench and bench.get('passed')):
        raise Stop('profil_modeles_banc_requis' if not bench else 'profil_modeles_banc_echoue')
    writer = str(routed_config(desk.c, 'mail_drafting').get('model') or '')
    purposes = sorted(p for p, (_, role) in PURPOSES.items() if role == 'fast')
    distinct = name != writer
    if distinct:
        purposes = sorted(purposes + [p for p, (_, role) in PURPOSES.items() if role == 'control'])
    _write_routes(desk, {p: {'provider': 'ollama', 'model': name} for p in purposes})
    desk.audit('economie569_profil_modeles', {'model': name, 'purposes': purposes, 'control_distinct': distinct, 'forced': bool(force)})
    return {'model': name, 'purposes': purposes, 'control_distinct': distinct,
            'message': 'Profil économe appliqué : %s pour %d fonction(s) rapides%s. Les rédactions gardent leur modèle.'
                       % (name, len(purposes), ' et de contrôle' if distinct else ' ; le contrôle garde un modèle distinct du rédacteur')}


def save(desk, data):
    flag = data.get('enabled')
    on = flag is True or str(flag or '').strip().lower() in ('1', 'on', 'true', 'oui', 'yes')
    try:
        daily = max(1, min(int(data.get('daily_jobs') or DEFAULT_QUOTA), 500))
    except (TypeError, ValueError):
        raise Stop('quota_econome_invalide') from None
    desk.setting(SETTING, on)
    desk.setting(QUOTA_SETTING, daily)
    desk.audit('economie569_reglage', {'enabled': on, 'daily_jobs': daily})
    return {'enabled': on, 'daily_jobs': daily,
            'message': ('Régime économe activé : %d analyses automatiques par jour au plus.' % daily) if on else 'Régime complet rétabli.'}


def section_html(desk, prefix):
    on = enabled(desk)
    rows = consumption(desk)
    table = ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(r['label']), e('externe · ' + r['provider'] if r['external'] else 'local'), r['requests'],
        format(r['input_tokens'], ',').replace(',', ' '), format(r['output_tokens'], ',').replace(',', ' '),
        r['minutes'], ('%.2f $' % r['cost_usd']) if r['external'] else '—') for r in rows)
    profile = model_profile(desk)
    if profile['available']:
        bench = bench_status(desk, profile['suggested'])
        if bench:
            verdict = ('<p class="ok">Banc réussi le %s : JSON, date et tri corrects.</p>' % e(str(bench.get('at', ''))[:16].replace('T', ' '))) if bench.get('passed') else \
                ('<p class="notice">Banc non réussi (%s) : ce modèle ne reçoit pas le tri ni le contrôle.</p>'
                 % e(', '.join(r['test'] for r in bench.get('results', []) if not r.get('ok'))))
        else:
            verdict = '<p class="vf-note">Banc non encore passé : testez le modèle avant de l’appliquer (trois épreuves, aucune donnée de dossier).</p>'
        suggestion = ('<p>Plus petit modèle installé : <strong>%s</strong> (%s Go).</p>%s'
                      '<form class="m5-form m5-inline" data-api="m540/economie/banc" data-reload="1">'
                      '<button type="submit">Tester le modèle (banc rapide)</button></form> '
                      '<form class="m5-form m5-inline" data-api="m540/economie/profil" data-reload="1" '
                      'data-confirm="Router le tri, la lecture des pièces jointes, la conversation vocale et, s’il est distinct du rédacteur, le contrôle vers %s ?">'
                      '<button type="submit"%s>Appliquer le profil économe de modèles</button></form>'
                      % (e(profile['suggested']), profile['size_gb'], verdict, e(profile['suggested'], quote=True),
                         '' if bench and bench.get('passed') else ' disabled'))
    else:
        suggestion = '<p class="vf-note">Ollama ne répond pas ou aucun modèle n’est installé : profil de modèles indisponible.</p>'
    if rows:
        consumption_html = ('<table class="vf-table"><thead><tr><th>Fonction</th><th>Modèle</th><th>Requêtes</th><th>Jetons entrés</th>'
                            '<th>Jetons sortis</th><th>Minutes</th><th>Coût estimé</th></tr></thead><tbody>%s</tbody></table>' % table)
    else:
        consumption_html = '<p class="ok">Aucune génération enregistrée sur 7 jours (les compteurs locaux commencent avec la 5.6.9).</p>'
    return ('<section class="ax-card" id="economie569"><h2>Régime économe et consommation</h2>'
            '<p>Le régime économe espace les contrôles périodiques (état, pilotage, tests métier, classement), limite les analyses '
            'automatiques des dossiers à un quota journalier et réserve le contrôle par un second modèle aux textes destinés à des tiers '
            '(courriels, actes, documents). Vos demandes ne sont jamais limitées.</p>'
            '<form class="m5-form" data-api="m540/economie" data-reload="1">'
            '<label class="m5-field"><input type="checkbox" name="enabled" value="1"%s> Régime économe</label>'
            '<label class="m5-field">Analyses automatiques par jour <input type="number" name="daily_jobs" min="1" max="500" value="%d"></label>'
            '<button type="submit">Enregistrer</button></form>'
            '<p class="vf-note">Aujourd’hui : %d analyse(s) automatique(s) lancée(s) sur %d · %d différée(s) (reprise automatique le lendemain).</p>%s'
            '<h3>Consommation des 7 derniers jours</h3>%s</section>') % (
        ' checked' if on else '', quota(desk), used_today(desk), quota(desk), len(deferred(desk)), suggestion, consumption_html)
