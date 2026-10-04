"""Interface 5.2.0 (complétée en 5.2.1 : file de travail détaillée et déblocable) : écran « Aujourd'hui » sur une colonne et page « Pourquoi rien n'est produit ? ».

Tout est lu localement (base d'AxiorHub, systemd, signaux de vie des services) ; les tests de connexion IMAP et Nextcloud ne sont
lancés que sur demande. Toutes les valeurs affichées sont échappées.
"""
from .common import matter_display
from datetime import date, datetime, time as dtime, timedelta, timezone
from html import escape as e
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, load_matters, matter_display


def _tz(desk):
    try:
        return ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))
    except ZoneInfoNotFoundError:
        return timezone.utc


def _labels(desk):
    try:
        return {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        return {}


EXTRA_REASONS = {
    'generation_ia_delai_depasse': 'Le modèle d’IA n’a pas répondu à temps. Testez-le dans « Pourquoi rien n’est produit ? » (bouton « Tester l’IA ») : '
                                   'un modèle local trop lourd pour le serveur ralentit toute la file.',
    'jurisprudence_officielle_verifiee_absente': 'Aucune décision officielle vérifiée n’a été trouvée : l’avis n’est pas rédigé sans source vérifiée. '
                                                 'Lancez d’abord une recherche de jurisprudence dans le dossier.',
    'traitement_deja_en_cours': 'Un autre travail tenait le verrou ; la demande sera reprise.',
}


def _reason(code):
    from .web import REASONS
    from .web440 import human
    from .ai_gateway import ERROR_MESSAGES
    return EXTRA_REASONS.get(code) or REASONS.get(code) or ERROR_MESSAGES.get(code) or human(code)


def _minutes(seconds):
    return '%d min' % max(1, int(seconds // 60)) if seconds < 7200 else '%d h' % int(seconds // 3600)


# ======================================================================================== diagnostic
def diagnosis(desk, now=None):
    from . import queue521
    now = now or time.time()
    out = {'services': [], 'heartbeats': [], 'queue': {}, 'errors': [], 'interrupted': 0, 'blocked': [], 'autonomy': [], 'recette': None, 'problems': []}
    try:
        from .reliability393 import service_health
        out['services'] = service_health(desk)['services']
    except Exception:
        out['services'] = []
    for s in out['services']:
        if s['required'] and not s['verified'] and s['state'] != 'unknown':
            out['problems'].append('Service « %s » arrêté (%s).' % (s['label'], s['state']))
    report = queue521.report(desk)
    suspended = {x['label'] for x in report['surveillance'] if x['suspended']}
    names = {'documents': 'Documents des dossiers', 'agenda': 'Agenda'}
    try:
        for r in desk.db.execute('SELECT name,heartbeat,status,message,next_check FROM live_services_v430 ORDER BY name'):
            age = now - float(r['heartbeat'] or 0)
            if r['name'].startswith('worker'):
                stale = age > 300
            else:
                expected = float(r['next_check'] or 0)
                stale = now > expected + 600 if expected else age > 900
            note = 'suspendu' if names.get(r['name']) in suspended else ''
            out['heartbeats'].append({'name': r['name'], 'age': int(age), 'status': r['status'], 'message': r['message'], 'stale': stale, 'note': note})
    except Exception:
        pass
    workers = [h for h in out['heartbeats'] if h['name'].startswith('worker')]
    if workers and all(h['stale'] for h in workers):
        out['problems'].append('Aucun service de travail ne donne signe de vie depuis plus de 5 minutes : les demandes restent en attente.')
    q = desk.db.execute("SELECT COUNT(*), MIN(created) FROM jobs WHERE status='pending'").fetchone()
    out['queue'] = {'pending': q[0], 'oldest': q[1] or '', 'running': len(report['running']), 'report': report}
    if q[0] and q[1]:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(q[1])
        except ValueError:
            age = timedelta(0)
        if age > timedelta(minutes=30):
            lock, long_jobs = report['lock'], [r for r in report['running'] if r['seconds'] > 900]
            if long_jobs:
                j = long_jobs[0]
                out['problems'].append('%d demande(s) en attente derrière « %s », en cours depuis %s (voir « File de travail »).' % (q[0], j['label'], _minutes(j['seconds'])))
            elif lock['busy'] and not report['running']:
                since = queue521._age_seconds(lock['since']) if lock['since'] else 0
                out['problems'].append('%d demande(s) en attente : « %s » tient le verrou de travail%s. Un modèle d’IA lent en est la cause habituelle : '
                                       '« Tester l’IA » ci-dessous.' % (q[0], lock['holder'] or 'l’analyse périodique des courriels',
                                                                        (' depuis %s' % _minutes(since)) if since else ''))
            else:
                out['problems'].append('%d demande(s) en attente depuis plus de 30 minutes (détail dans « File de travail »).' % q[0])
    for s in report['surveillance']:
        if s['suspended']:
            out['problems'].append('Surveillance « %s » suspendue après trois échecs (%s) : « Relancer la surveillance ».' % (s['label'], _reason(s['reason'] or 'action_interrompue')))
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    from .web import JOB_LABELS
    labels = _labels(desk)
    for r in desk.db.execute("SELECT id,kind,args,priority,created,finished,result FROM jobs WHERE status='error' AND COALESCE(finished,created)>=? ORDER BY id DESC LIMIT 200", (since,)):
        try:
            code = json.loads(r['result'] or '{}').get('erreur', 'action_interrompue')
        except ValueError:
            code = 'action_interrompue'
        if code == queue521.INTERRUPTED and queue521.automatic(r['kind'], r['priority']):
            out['interrupted'] += 1            # travail périodique coupé par un redémarrage : il reprend seul
            continue
        if len(out['errors']) >= 15:
            continue
        try:
            args = json.loads(r['args'] or '{}')
        except ValueError:
            args = {}
        mid = str((args if isinstance(args, dict) else {}).get('matter', '') or '')
        matter = labels.get(mid, ('dossier inconnu : ' + mid) if mid else '')
        out['errors'].append({'id': r['id'], 'kind': JOB_LABELS.get(r['kind'], r['kind']), 'at': (r['finished'] or r['created'] or '')[:16].replace('T', ' '),
                              'code': code, 'reason': _reason(code), 'matter': matter})
    recent = [x for x in out['errors'] if x['at'] >= (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()[:16].replace('T', ' ')]
    if recent:
        out['problems'].append('%d demande(s) en erreur depuis 24 heures (motifs ci-dessous).' % len(recent))
    try:
        from . import conflicts500
        if desk.settings('conflicts500:gate', True):
            for p in conflicts500.pending(desk):
                if conflicts500.matter_gate_state(desk, p['matter'])['gated']:
                    out['blocked'].append({'matter': p['matter'], 'label': p['label'], 'why': 'Recherche de conflits d’intérêts à examiner'})
    except Exception:
        pass
    if out['blocked']:
        out['problems'].append('%d dossier(s) bloqué(s) par la recherche de conflits.' % len(out['blocked']))
    try:
        from . import autonomy480
        for task in ('courriel_reponse', 'acte_courrier'):
            lvl = autonomy480.level(desk, task)
            if lvl == 'propose':
                label = autonomy480.TASKS[task]['label']
                out['autonomy'].append(label)
                out['problems'].append('Autonomie « Proposer seulement » pour « %s » : aucun brouillon n’est préparé automatiquement.' % label)
    except Exception:
        pass
    out['recette'] = recette_status(desk)
    if out['recette'] and out['recette'].get('status') == 'echec':
        out['problems'].append('La dernière recette automatique a relevé des échecs.')
    return out


def recette_status(desk):
    path = Path(desk.c['state_dir']) / 'recette520.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def check_imap(desk):
    from .mailbox import Mailbox
    started = time.monotonic()
    box = Mailbox(desk.c['mail'])
    try:
        status, _ = box.conn.select('"%s"' % desk.c['mail']['drafts'].replace('"', ''), readonly=True)
        if status != 'OK':
            raise Stop('dossier_brouillons_introuvable')
    finally:
        box.close()
    return {'ok': True, 'message': 'Messagerie joignable ; dossier des brouillons accessible (%.1f s).' % (time.monotonic() - started)}


def check_nextcloud(desk):
    from .dav import DAV
    started = time.monotonic()
    cfg = desk.c.get('nextcloud_documents') or desk.c['nextcloud']
    root = (cfg.get('roots') or ['/'])[0]
    DAV(cfg).list_folder(root)
    msg = 'Nextcloud joignable ; dossier « %s » lisible (%.1f s).' % (root, time.monotonic() - started)
    wf = desk.c.get('nextcloud_workflow') or {}
    if wf.get('enabled'):
        from .workplan import _dav
        _dav(desk).calendars()
        msg += ' Compte technique de l’agenda valide.'
    return {'ok': True, 'message': msg}


def _ok(flag, yes='OK', no='À traiter'):
    return '<span class="%s">%s</span>' % ('ok' if flag else 'ko', e(yes if flag else no))


def _queue_html(d):
    rep = d['queue']['report']
    q = d['queue']
    lock = rep['lock']
    html = '<section class="ax-card" id="file"><h2>File de travail</h2><p>%d en attente%s · %d en cours.</p>' % (
        q['pending'], (' (la plus ancienne : %s)' % e(q['oldest'][:16].replace('T', ' '))) if q['oldest'] else '', q['running'])
    if rep['running']:
        html += '<h3>En cours</h3><table class="vf-table"><thead><tr><th>Travail</th><th>Depuis</th><th>Worker</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td>%s%s</td><td>%s</td></tr>' % (e(r['label']), e(_minutes(r['seconds'])), ' <span class="warn">long</span>' if r['seconds'] > 900 else '', e(r['worker']))
            for r in rep['running'])
    if lock['busy']:
        html += '<p class="vf-note">Verrou de travail tenu par : <strong>%s</strong>%s. Les travaux qui écrivent (brouillons, documents) attendent qu’il soit libéré ; ' \
                'depuis la 5.2.1, l’analyse des courriels le libère au bout de 3 minutes.</p>' % (
                    e(lock['holder'] or 'un autre processus AxiorHub'), (' depuis %s' % e(_minutes(queue521_age(lock['since'])))) if lock['since'] else '')
    if rep['pending']:
        html += '<h3>En attente</h3><table class="vf-table"><thead><tr><th>Travail</th><th>Nombre</th><th>Depuis</th><th>Origine</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (e(p['label']), p['count'], e(p['oldest'][:16].replace('T', ' ')),
                                                                       'automatique' if p['automatic'] else '<strong>votre demande</strong>') for p in rep['pending'])
    actions = ''
    if rep['pending_automatic']:
        actions += ('<form class="m5-form m5-inline m5-mini" data-api="m520/queue/purge" data-reload="1" data-confirm="Annuler les %d travaux automatiques en attente ? '
                    'Vos propres demandes sont conservées ; les contrôles automatiques reprendront à leur prochain passage."><button class="ax-btn" type="submit">'
                    'Désengorger : annuler les %d travaux automatiques</button></form>') % (rep['pending_automatic'], rep['pending_automatic'])
    if any(s['suspended'] for s in rep['surveillance']):
        actions += ('<form class="m5-form m5-inline m5-mini" data-api="m520/queue/resume" data-reload="1"><button class="ax-btn ghost" type="submit">'
                    'Relancer la surveillance</button></form>')
    if actions:
        html += '<div class="ax-actions">%s</div>' % actions
    surv = ''.join('<li>%s : %s%s</li>' % (e(s['label']), e({'done': 'dernier contrôle réussi', 'error': 'dernier contrôle en échec', 'pending': 'en attente',
                                                            'running': 'en cours', 'jamais': 'jamais lancé', 'cancelled': 'annulé'}.get(s['state'], s['state'])),
                                         (' — <span class="ko">suspendue</span> : %s' % e(_reason(s['reason'] or 'action_interrompue'))) if s['suspended'] else
                                         ((' — %s' % e(_reason(s['reason']))) if s['state'] == 'error' and s['reason'] else ''))
                   for s in rep['surveillance'])
    html += '<h3>Surveillance</h3><ul>%s</ul>' % surv
    if d.get('interrupted'):
        html += ('<p class="vf-note">%d contrôle(s) automatique(s) interrompu(s) par un redémarrage sur 7 jours (normal après une installation ou un '
                 'redémarrage) : ils reprennent seuls et ne sont plus comptés comme erreurs.</p>') % d['interrupted']
    return html + '</section>'


def queue521_age(stamp):
    from .queue521 import _age_seconds
    return _age_seconds(stamp)


def diagnostic_page(desk, auth, prefix, args, shell):
    d = diagnosis(desk)
    html = ('<div class="d520"><h1>Pourquoi rien n’est produit ?</h1><p class="ax-muted">Un seul écran pour comprendre une panne : services, file de travail, '
            'erreurs récentes et leur motif, dossiers bloqués, connexions et dernière recette automatique.</p>')
    if d['problems']:
        html += '<div class="d520-verdict ko" role="alert"><strong>%d point(s) à traiter :</strong><ul>%s</ul></div>' % (
            len(d['problems']), ''.join('<li>%s</li>' % e(p) for p in d['problems']))
    else:
        html += '<div class="d520-verdict ok" role="status"><strong>Aucune cause de blocage détectée.</strong> Si rien n’est produit, testez les connexions ci-dessous.</div>'
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(s['label']), e(s['state']), _ok(True, 'actif') if s['verified'] else ('<span class="ax-muted">non lisible</span>' if s['state'] == 'unknown' else
                                                     '<span class="ko">arrêté</span>' if s['required'] else '<span class="ax-muted">inactif (facultatif)</span>'))
        for s in d['services'])
    beats = ''.join('<tr><td>%s</td><td>il y a %s</td><td>%s</td></tr>' % (
        e(h['name']), e(_age(h['age'])), '<span class="ko">suspendu</span>' if h.get('note') == 'suspendu' else _ok(not h['stale'], 'vivant', 'silencieux'))
        for h in d['heartbeats'])
    html += ('<section class="ax-card"><h2>Services</h2><table class="vf-table"><thead><tr><th>Service</th><th>État systemd</th><th>Statut</th></tr></thead><tbody>%s</tbody></table>'
             '<h3>Derniers signes de vie</h3><table class="vf-table"><thead><tr><th>Contrôle</th><th>Dernier signal</th><th>Statut</th></tr></thead><tbody>%s</tbody></table>'
             '<p class="vf-note">Un worker « vivant » peut être occupé par un long travail : voir « File de travail ». Pour relancer : '
             '<code>sudo systemctl restart axiorhub-mail-desk-worker.service axiorhub-mail-desk-worker@2.service axiorhub-mail-watch.service</code>. '
             'Analyse ou nettoyage périodique arrêté : <code>sudo systemctl enable --now axiorhub-mail-agent.timer axiorhub-mail-agent-cleanup.timer</code></p></section>') % (
        rows or '<tr><td colspan="3">État systemd non lisible depuis l’interface.</td></tr>', beats or '<tr><td colspan="3">Aucun signal enregistré.</td></tr>')
    html += _queue_html(d)
    html += mail_pipeline_html(desk, prefix)
    errs = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(x['at']), e(x['kind']), e(x['matter']), e(x['reason'])) for x in d['errors'])
    html += ('<section class="ax-card"><h2>Erreurs des 7 derniers jours</h2>%s<p><a href="%s">Reprendre une demande dans « État du système »</a></p></section>') % (
        ('<table class="vf-table"><thead><tr><th>Date</th><th>Demande</th><th>Dossier</th><th>Motif</th></tr></thead><tbody>%s</tbody></table>' % errs) if errs
        else '<p class="ok">Aucune erreur.</p>', e(prefix + '/etat-systeme'))
    blocked = ''.join('<li><a href="%s">%s</a> — %s</li>' % (e('%s/conflits?matter=%s' % (prefix, b['matter']), quote=True), e(b['label']), e(b['why'])) for b in d['blocked'])
    auto = ''.join('<li>%s : <a href="%s">régler l’autonomie</a></li>' % (e(a), e(prefix + '/autonomie')) for a in d['autonomy'])
    html += '<section class="ax-card"><h2>Dossiers et réglages bloquants</h2>%s</section>' % (
        ('<ul>%s%s</ul>' % (blocked, auto)) if (blocked or auto) else '<p class="ok">Aucun dossier bloqué ; préparation automatique des brouillons autorisée.</p>')
    html += ('<section class="ax-card"><h2>Connexions</h2><p class="vf-note">Tests à la demande (lecture seule ; le test de l’IA envoie seulement « Réponds : OK »).</p>'
             '<div class="ax-actions"><form class="m5-form m5-inline m5-mini" data-api="m520/check/imap"><button class="ax-btn ghost" type="submit">Tester la messagerie</button></form>'
             '<form class="m5-form m5-inline m5-mini" data-api="m520/check/nextcloud"><button class="ax-btn ghost" type="submit">Tester Nextcloud</button></form>'
             '<form class="m5-form m5-inline m5-mini" data-api="m520/check/ia"><button class="ax-btn ghost" type="submit">Tester l’IA</button></form></div></section>')
    r = d['recette']
    if r:
        html += ('<section class="ax-card"><h2>Dernière recette automatique</h2><p>%s — version %s, %s : %s</p>%s</section>') % (
            e(r.get('at', '')[:16].replace('T', ' ')), e(r.get('version', '')), e(r.get('summary', '')),
            _ok(r.get('status') == 'ok', 'réussie', 'en cours' if r.get('status') == 'en_cours' else 'échecs'),
            ('<details><summary>Tests en échec (%d)</summary><ul>%s</ul></details>' % (len(r.get('failures', [])), ''.join('<li>%s%s</li>' % (e(f), ('<br><small class="vf-note">%s</small>' % e((r.get('details') or {}).get(f, ''))) if (r.get('details') or {}).get(f) else '')
                                                for f in r.get('failures', [])[:60])))
            if r.get('failures') else '')
    else:
        html += '<section class="ax-card"><h2>Recette automatique</h2><p>Pas encore exécutée. Elle se lance après chaque installation ; manuellement : <code>sudo python3 /opt/axiorhub-mail-agent/current/recette.py</code></p></section>'
    html += '</div>'
    return shell('Pourquoi rien n’est produit ?', html, prefix, auth['csrf'], '/diagnostic')


def _age(seconds):
    seconds = int(seconds)
    if seconds < 120:
        return '%d s' % seconds
    if seconds < 7200:
        return '%d min' % (seconds // 60)
    if seconds < 172800:
        return '%d h' % (seconds // 3600)
    return '%d j' % (seconds // 86400)


def handle(desk, name, data, method='POST', args=None):
    n = name[len('m520/'):]
    if n == 'check/imap':
        try:
            return check_imap(desk)
        except Stop as ex:
            raise Stop(str(ex)) from None
    if n == 'docrequest' and method == 'POST':
        return docrequest_handle(desk, data)
    if n == 'check/ia' and method == 'POST':
        from .queue521 import check_ai
        return check_ai(desk)
    if n == 'queue/purge' and method == 'POST':
        from .queue521 import purge_automatic
        return purge_automatic(desk)
    if n == 'queue/resume' and method == 'POST':
        from .queue521 import resume_surveillance
        return resume_surveillance(desk)
    if n == 'check/nextcloud':
        return check_nextcloud(desk)
    if n == 'diagnosis' and method == 'GET':
        return diagnosis(desk)
    raise Stop('route_inconnue')


# ======================================================================================== Aujourd'hui
def today_html(desk, prefix, form=None, today=None):
    tz = _tz(desk)
    today = today or datetime.now(tz).date()
    labels = _labels(desk)
    start = datetime.combine(today, dtime.min, tz).astimezone(timezone.utc).isoformat()
    end = datetime.combine(today + timedelta(days=1), dtime.min, tz).astimezone(timezone.utc).isoformat()
    out = '<div class="t520">'
    try:
        d = diagnosis(desk)
        if d['problems']:
            out += '<section class="t520-alert" role="alert"><h2>Production à vérifier</h2><p>%s <a href="%s">Pourquoi rien n’est produit ?</a></p></section>' % (
                e(d['problems'][0]), e(prefix + '/diagnostic'))
    except Exception:
        pass
    # agenda du jour
    try:
        events = [dict(r) for r in desk.db.execute('SELECT id,title,starts,ends,matter FROM calendar_cache WHERE starts<? AND ends>? ORDER BY starts LIMIT 30', (end, start))]
    except Exception:
        events = []
    items = ''
    for ev in events:
        s = datetime.fromisoformat(ev['starts']).astimezone(tz)
        when = 'Journée' if s.time() == dtime.min else s.strftime('%H:%M')
        link = (' — <a href="%s">%s</a>' % (e('%s/rendez-vous?event=%s' % (prefix, ev['id']), quote=True), e(labels.get(ev['matter'], ev['matter'])))) if ev['matter'] else ''
        items += '<li><time>%s</time>%s%s</li>' % (e(when), e(ev['title'] or 'Événement'), link)
    out += '<section><h2>Audiences et rendez-vous <small><a href="%s">Agenda</a></small></h2>%s</section>' % (
        e(prefix + '/planning'), ('<ul>%s</ul>' % items) if items else '<p class="t520-empty">Rien à l’agenda aujourd’hui.</p>')
    # brouillons
    try:
        drafts = [dict(r) for r in desk.db.execute("SELECT subject,matter,updated FROM work_items WHERE state='draft_ready' ORDER BY updated DESC LIMIT 8")]
        total = desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0]
    except Exception:
        drafts, total = [], 0
    items = ''.join('<li>%s%s</li>' % (e(x['subject'] or 'Sans objet'), (' — ' + e(labels.get(x['matter'], x['matter']))) if x['matter'] else '') for x in drafts)
    out += '<section><h2>Brouillons à relire (%d) <small><a href="%s">Courriels à relire</a></small></h2>%s</section>' % (
        total, e(prefix + '/courriels'), ('<ul>%s</ul>' % items) if items else '<p class="t520-empty">Aucun brouillon en attente de relecture.</p>')
    # échéances et prescriptions
    rows = []
    horizon = (today + timedelta(days=14)).isoformat()
    try:
        for r in desk.db.execute("SELECT matter,due,status,rule_id FROM deadlines450 WHERE status IN ('a_confirmer','confirmee','manuel') AND due<>'' AND due<=? ORDER BY due LIMIT 15", (horizon,)):
            rows.append((r['due'], 'Échéance', r['matter'], r['status'], prefix + '/echeances'))
    except Exception:
        pass
    try:
        for r in desk.db.execute("SELECT matter,due,status FROM limitation500 WHERE status IN ('a_confirmer','confirmee','manuel') AND due<>'' AND due<=? ORDER BY due LIMIT 15", ((today + timedelta(days=60)).isoformat(),)):
            rows.append((r['due'], 'Prescription', r['matter'], r['status'], prefix + '/prescriptions'))
    except Exception:
        pass
    rows.sort()
    items = ''
    for due, kind, matter, status, href in rows[:12]:
        days = (date.fromisoformat(due[:10]) - today).days
        when = 'dépassée' if days < 0 else ("aujourd’hui" if days == 0 else 'dans %d j' % days)
        items += '<li><time>%s</time><a href="%s">%s</a> — %s (%s)%s</li>' % (
            e(due[8:10] + '/' + due[5:7]), e(href), e(kind), e(labels.get(matter, matter)), e(when), ' · à confirmer' if status == 'a_confirmer' else '')
    out += '<section><h2>Échéances <small>procédure 14 j · prescription 60 j</small></h2>%s</section>' % (
        ('<ul>%s</ul>' % items) if items else '<p class="t520-empty">Aucune échéance proche.</p>')
    # décisions en attente
    decisions = []
    try:
        from . import conflicts500
        for p in conflicts500.pending(desk):
            decisions.append(('Conflits d’intérêts : ' + p['label'], '%s/conflits?matter=%s' % (prefix, p['matter'])))
    except Exception:
        pass
    for sql, label, href in (("SELECT COUNT(*) FROM time500_proposals WHERE status='pending'", 'temps à valider', '/honoraires'),
                             ("SELECT COUNT(*) FROM autonomy_pending480 WHERE status='pending'", 'proposition(s) de l’agent à valider', '/autonomie'),
                             ("SELECT COUNT(*) FROM work_plan_proposals WHERE status='pending'", 'programme(s) de la semaine à confirmer', '/planning?vue=organiser'),
                             ("SELECT COUNT(*) FROM pieces510_drafts WHERE status='a_confirmer'", 'bordereau(x) en attente du code de confirmation', '/pieces'),
                             ("SELECT COUNT(*) FROM limitation500 WHERE status='a_confirmer'", 'prescription(s) à confirmer', '/prescriptions'),
                             ("SELECT COUNT(*) FROM deadlines450 WHERE status='a_confirmer'", 'échéance(s) à confirmer', '/echeances')):
        try:
            n = desk.db.execute(sql).fetchone()[0]
        except Exception:
            n = 0
        if n:
            decisions.append(('%d %s' % (n, label), prefix + href))
    items = ''.join('<li><a href="%s">%s</a></li>' % (e(h, quote=True), e(t)) for t, h in decisions)
    out += '<section><h2>Décisions en attente</h2>%s</section>' % (('<ul>%s</ul>' % items) if items else '<p class="t520-empty">Aucune décision en attente.</p>')
    if form is not None:
        out += routines_html(desk, prefix, form, today)
    out += '<p class="vf-note"><a href="%s">Vue détaillée (ancien cockpit)</a></p></div>' % e(prefix + '/aujourdhui?vue=cockpit')
    return out


def _days_label(days):
    if days == '1234567':
        return 'tous les jours'
    if days == '12345':
        return 'du lundi au vendredi'
    names = ('lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim')
    return ', '.join(names[int(d) - 1] for d in days)


def routines_html(desk, prefix, form, today):
    from . import routines520 as r5
    s = r5.settings(desk)
    out = ''
    for kind in ('briefing', 'tri', 'bilan'):
        rep = r5.latest(desk, kind)
        cfg = s[kind]
        when = 'à %s, %s' % (cfg['time'], _days_label(cfg['days']))
        fresh = rep and rep['created'][:10] >= (today - timedelta(days=0 if kind != 'bilan' else 6)).isoformat()
        head = '%s <small>%s%s</small>' % (e(r5.KINDS[kind]), e(when) if cfg['enabled'] else 'désactivé',
                                           (' · dernier : ' + e(datetime.fromisoformat(rep['created']).astimezone(_tz(desk)).strftime('%d/%m %H:%M'))) if rep else '')
        body = r5.render_markdown(rep['text']) if rep else '<p class="t520-empty">Pas encore produit.</p>'
        if rep and rep['notes']:
            body += ''.join('<p class="vf-note">%s</p>' % e(n) for n in rep['notes'])
        if rep and rep['status'] == 'sans_ia':
            body += '<p class="vf-note">Version sans IA : le modèle n’était pas disponible.</p>'
        body += form('routine520', 'Générer maintenant', {'kind': kind})
        open_ = ' open' if fresh and (kind != 'tri' or any(x.get('category') == 'urgent' for x in json.loads(rep['data'] or '{}').get('items', []))) else ''
        out += '<details class="t520-frame"%s><summary><h2>%s</h2></summary>%s</details>' % (open_, head, body)
    docs = r5.documents(desk, today=today)
    items = ''
    for d in docs:
        due = (' — échéance ' + e(d['due'][8:10] + '/' + d['due'][5:7])) if d['due'] else ''
        if d['matter']:
            action = form('studio_prepare420', 'Préparer avec l’IA', {'deliverable_kind': d['kind'], 'matter': d['matter'], 'instruction': d['instruction']})
        else:
            action = '<a href="%s">Choisir le dossier dans le Studio</a>' % e(prefix + '/studio')
        items += '<li><strong>%s</strong> · %s%s<div class="t520-doc-action">%s</div></li>' % (e(d['source']), e(d['title']), due, action)
    out += ('<details class="t520-frame"%s><summary><h2>Documents à préparer <small>%d</small></h2></summary>%s'
            '<p class="vf-note">Conclusions, courriers, actes, contrats… L’agent prépare un projet à relire dans « Produire » ; rien n’est envoyé ni déposé.</p></details>') % (
        ' open' if docs else '', len(docs), ('<ul>%s</ul>' % items) if items else '<p class="t520-empty">Aucun document en attente.</p>')
    days = lambda k: e(s[k]['days'])
    out += ('<details class="t520-frame"><summary><h2>Réglages des routines</h2></summary>' + form('save_routines520', 'Enregistrer', None,
        ''.join('<fieldset><legend>%s</legend><label><input type="checkbox" name="%s_enabled" value="yes"%s> Activé</label>'
                '<label>Heure<input name="%s_time" value="%s" pattern="[0-2][0-9]:[0-5][0-9]" required></label>'
                '<label>Jours (1 = lundi … 7 = dimanche)<input name="%s_days" value="%s" pattern="[1-7]+" required></label></fieldset>' % (
                    e(r5.KINDS[k]), k, ' checked' if s[k]['enabled'] else '', k, e(s[k]['time']), k, days(k)) for k in ('briefing', 'tri', 'bilan')) +
        '<label><input type="checkbox" name="tri_drafts" value="yes"%s> Tri : préparer un brouillon de réponse pour les urgences (jamais envoyé)</label>' % (' checked' if s['tri']['drafts'] else '') +
        '<label><input type="checkbox" name="news" value="yes"%s> Briefing : actualité lue dans l’application Nouvelles (News) de Nextcloud (3 derniers jours)</label>' % (' checked' if s['news'] else '') +
        '<label>Ressort (audiences tenues par un postulant ailleurs)<input name="ressort" value="%s" maxlength="60"></label>' % e(s['ressort']) +
        '<label>Signature des brouillons<textarea name="signature" rows="6" maxlength="600">%s</textarea></label>' % e(s['signature']) +
        '<label>Exclusions (expéditeurs ou objets, un par ligne)<textarea name="exclude" rows="5">%s</textarea></label>' % e('\n'.join(s['exclude']))) +
        '<p class="vf-note">Messagerie lue sans rien marquer ni déplacer ; seule écriture : les brouillons de réponse. Actualité : dossiers de flux dont le nom contient « affaires », « numérique », « avocat/barreau » ou « Lyon ».</p></details>')
    return out


# ======================================================================================== demande de document (page Documents)
def docrequest_html(desk, prefix, selected=''):
    from urllib.parse import urlencode
    from . import docrequest520 as dr
    from .web500 import _opts
    kinds = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in dr.KINDS.items())
    out = ('<section class="ax-card d520-request"><h2>Demander un document à l’agent</h2>'
           '<p class="ax-muted">Décrivez le document : l’agent retrouve le dossier, lit ses fichiers, les courriels et l’agenda qui s’y rattachent, '
           'rédige un projet et l’enregistre comme <strong>nouveau fichier</strong> dans le dossier. Vous le modifiez ensuite dans l’éditeur ou le faites '
           'modifier par l’IA (nouvelle version). Rien n’est envoyé.</p>'
           '<form class="m5-form" data-api="m520/docrequest" data-reload="1">'
           '<label class="m5-field">Votre demande<textarea name="request" rows="4" maxlength="4000" required placeholder="Ex. : Prépare un courrier au confrère '
           'adverse pour solliciter un renvoi de l’audience du 12 novembre dans le dossier ALPHA, en reprenant le dernier échange."></textarea></label>'
           '<div class="p5-grid"><label class="m5-field">Dossier<select name="matter">%s</select></label>'
           '<label class="m5-field">Type de document<select name="kind">%s</select></label></div>'
           '<button class="ax-btn" type="submit">Préparer le document</button></form>') % (
        _opts(desk, selected, 'L’agent trouve le dossier d’après la demande'), kinds)
    status = {'en_file': 'en attente', 'en_cours': 'en cours de rédaction', 'cree': 'enregistré', 'echec': 'échec', 'dossier_a_choisir': 'dossier à préciser'}
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    items = ''
    for r in dr.listing(desk, 8):
        res = r['result']
        line = '<li>%s<strong>%s</strong> — %s <span class="vf-badge muted">%s</span>' % (
            'Nouvelle version demandée : ' if r['revision_of'] else '', e(r['request'][:140]), e(labels.get(r['matter'], 'dossier à identifier')), e(status.get(r['status'], r['status'])))
        if r['status'] == 'cree':
            name = r['path'].rsplit('/', 1)[-1]
            line += '<div class="ax-actions"><a class="ax-btn" href="%s">Modifier « %s »</a>%s</div>' % (
                e(prefix + '/documents/edit?' + urlencode({'path': r['path'], 'matter': r['matter']})), e(name),
                (' <a class="ax-btn ghost" target="_blank" rel="noopener noreferrer" href="%s">Ouvrir dans Nextcloud</a>' % e(res['url'], quote=True))
                if str(res.get('url', '')).startswith('https://') else '')
            if res.get('to_complete'):
                line += '<p class="vf-note">À compléter : %s</p>' % e(' ; '.join(res['to_complete'][:6]))
            line += ('<form class="m5-form m5-inline" data-api="m520/docrequest" data-reload="1"><input type="hidden" name="revision_of" value="%s">'
                     '<label class="m5-field">Faire modifier par l’IA<input name="request" maxlength="2000" required placeholder="Ex. : ton plus ferme, ajouter la date d’audience"></label>'
                     '<button class="ax-btn ghost" type="submit">Créer une nouvelle version</button></form>') % e(r['id'], quote=True)
        elif r['status'] == 'dossier_a_choisir':
            cands = ''.join('<option value="%s">%s</option>' % (e(c['id'], quote=True), e(c['label'])) for c in res.get('candidates', []))
            line += ('<form class="m5-form m5-inline" data-api="m520/docrequest" data-reload="1"><input type="hidden" name="request" value="%s">'
                     '<input type="hidden" name="kind" value="%s"><label class="m5-field">Préciser le dossier<select name="matter" required>%s</select></label>'
                     '<button class="ax-btn" type="submit">Relancer</button></form>') % (
                e(r['request'], quote=True), e(r['kind'], quote=True), cands or _opts(desk, '', 'Choisir un dossier'))
        elif r['status'] == 'echec':
            line += '<p class="m5-warn">%s</p>' % e(_reason(res.get('error', 'action_interrompue')))
        items += line + '</li>'
    if items:
        out += '<h3>Demandes récentes</h3><ul class="d520-requests">%s</ul>' % items
    return out + '</section>'


def docrequest_handle(desk, data):
    from . import docrequest520 as dr
    s = lambda k: str(data.get(k, '') or '')
    files = data.get('attachments') if isinstance(data.get('attachments'), list) else []
    return dr.submit(desk, s('request'), s('matter'), s('kind') or 'auto', s('revision_of'), files, s('revision_path'))


# ======================================================================================== pourquoi peu de brouillons ?
def mail_pipeline(desk, days=7):
    from .state import State
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    st = State(desk.c['state_dir'])
    rows = st.db.execute('SELECT status, reason, COUNT(*) n FROM messages WHERE updated>=? GROUP BY status, reason ORDER BY n DESC', (since,)).fetchall()
    total = sum(r[2] for r in rows)
    drafted = sum(r[2] for r in rows if r[0] == 'drafted')
    reasons = [{'status': s, 'reason': r, 'count': n, 'text': _reason(r or s), 'action': _mail_action(r or '', s)} for s, r, n in rows if s != 'drafted']
    try:
        from . import autonomy480
        level = autonomy480.level(desk, 'courriel_reponse')
    except Exception:
        level = ''
    return {'days': days, 'total': total, 'drafted': drafted, 'reasons': reasons[:12], 'mode': desk.c.get('mode', ''), 'autonomy': level,
            'per_run': int(desk.c.get('max_messages_per_run', 8))}


def _mail_action(reason, status):
    r = reason or ''
    if status == 'observed' or r == 'proposition_sans_ecriture_imap':
        return ('Le service est en mode « observation » : aucun brouillon n’est écrit. Passez en mode brouillons (sudo axiorhub-mail configure).', '')
    if r.startswith('autonomie'):
        return ('Réglez « Répondre aux courriels entrants » sur « Préparer un brouillon ».', '/autonomie')
    if 'correspondant' in r or 'dossier' in r or 'association' in r or 'role' in r:
        return ('Confirmez les correspondants et leurs dossiers : les messages suivants de ces expéditeurs seront rédigés.', '/associations')
    if r.startswith('tri_ia_'):
        return ('Si c’est faux, créez une règle « toujours traiter » pour l’expéditeur.', '/regles')
    if r.startswith('regle_graphique'):
        return ('Une de vos règles a écarté ces messages.', '/regles')
    if 'contexte' in r or 'generation' in r or 'modele' in r or 'fournisseur' in r:
        return ('Le modèle d’IA n’a pas pu rédiger : vérifiez-le dans « Pourquoi rien n’est produit ? ».', '/diagnostic')
    return ('', '')


def mail_pipeline_html(desk, prefix, compact=False):
    try:
        mp = mail_pipeline(desk)
    except Exception:
        return ''
    if not mp['total']:
        return '' if compact else '<section class="ax-card"><h2>Courriels et brouillons (7 jours)</h2><p>Aucun courriel examiné sur la période.</p></section>'
    rows = ''
    for r in mp['reasons'][:8 if compact else 12]:
        text, href = r['action']
        hint = ''
        if text:
            hint = ' <em>%s</em>' % e(text) + ((' <a href="%s">Ouvrir</a>' % e(prefix + href)) if href else '')
        rows += '<li><strong>%d</strong> — %s%s</li>' % (r['count'], e(r['text']), hint)
    head = '%d courriel(s) examiné(s) en 7 jours, %d brouillon(s) préparé(s).' % (mp['total'], mp['drafted'])
    extra = []
    if mp['mode'] == 'observe':
        extra.append('Mode observation actif : aucun brouillon ne peut être écrit.')
    if mp['autonomy'] == 'propose':
        extra.append('Autonomie « Proposer seulement » pour les réponses : aucun brouillon automatique.')
    body = ('%s%s<p class="vf-note">Pourquoi les autres n’ont pas de brouillon :</p><ul>%s</ul>'
            '<p class="vf-note">Par prudence (secret professionnel), un brouillon n’est rédigé que si le dossier et le rôle de l’expéditeur sont identifiés '
            'avec certitude et si une réponse est attendue. Au plus %d courriel(s) analysé(s) toutes les 5 minutes.</p>') % (
        '' if compact else '<p>%s</p>' % e(head), ''.join('<p class="m5-warn">%s</p>' % e(x) for x in extra), rows or '<li>—</li>', mp['per_run'])
    if compact:
        return '<details class="ax-card d520-mail"><summary><strong>Pourquoi peu de brouillons ?</strong> %s</summary>%s</details>' % (e(head), body)
    return '<section class="ax-card"><h2>Courriels et brouillons (7 jours)</h2>%s</section>' % body
