"""5.6.13 : fiche de préparation par dossier (audience prochaine).

Une seule fiche, sans nouvel appel au modèle : audience(s) à venir, dernières conclusions identifiées dans le dossier, tâches et
pièces encore attendues, notes de plaidoirie déjà produites, questions probables de la dernière préparation, projets à relire.
Chaque élément vient du registre ou du dossier Nextcloud ; la préparation complète reste lancée par l'avocat.
"""
from datetime import datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import sqlite3

from .common import Stop, fold, load_matters, matter_display

HEARING_WORDS = ('audience', 'plaidoirie', 'plaid', 'comparution', 'refere', 'référé', 'mise en etat', 'mise en état', 'delibere', 'délibéré')


def _rows(desk, sql, args=()):
    try:
        return [dict(r) for r in desk.db.execute(sql, args)]
    except sqlite3.OperationalError:
        return []


def sheet(desk, matter_id, dav=None):
    matters = {m['id']: m for m in load_matters(desk.c)}
    matter = matters.get(str(matter_id or ''))
    if not matter:
        raise Stop('dossier_absent')
    now = datetime.now(timezone.utc).isoformat()
    out = {'matter': matter['id'], 'label': matter_display(matter), 'generated_at': now, 'notes': []}
    events = [x for x in _rows(desk, 'SELECT id,title,starts,location FROM calendar_cache WHERE matter=? AND starts>=? ORDER BY starts LIMIT 40', (matter['id'], now))
              if any(w in fold(x['title']) for w in HEARING_WORDS)]
    out['hearings'] = [{'id': x['id'], 'title': x['title'][:160], 'starts': x['starts'][:16].replace('T', ' '), 'location': (x.get('location') or '')[:120]} for x in events[:3]]
    files = []
    try:
        from .document_projects import _dav, _inventory
        client = dav or _dav(desk)
        files = [x for x in _inventory(client, matter['path']) if not x.get('directory')]
    except Exception:
        out['notes'].append('Dossier Nextcloud non lu : la liste des conclusions vient du dernier inventaire connu.')
    conclusions = [x for x in files if 'conclusion' in fold(PurePosixPath(x['path']).name)]
    conclusions.sort(key=lambda x: str(x.get('modified', '')), reverse=True)
    out['conclusions'] = [{'path': x['path'], 'name': PurePosixPath(x['path']).name, 'modified': str(x.get('modified', ''))[:16],
                           'side': 'adverse' if re.search(r'advers|en reponse|intim|defend', fold(PurePosixPath(x['path']).name)) else 'à qualifier'} for x in conclusions[:8]]
    for w in _rows(desk, "SELECT w.side,w.path,w.modified FROM hearing_writings_v250 w JOIN hearing_projects_v250 p ON p.id=w.project_id "
                        "WHERE p.matter=? ORDER BY w.selected_at DESC LIMIT 4", (matter['id'],)):
        for c in out['conclusions']:
            if c['path'] == w['path']:
                c['side'] = 'nôtres' if w['side'] == 'ours' else 'adverse'
    out['tasks'] = [{'id': t['id'], 'title': t['title'][:160], 'due': (t.get('due') or '')[:10]} for t in
                    _rows(desk, "SELECT id,title,due FROM work_tasks_v211 WHERE matter=? AND status NOT IN ('completed','cancelled') ORDER BY COALESCE(due,'9999') LIMIT 12", (matter['id'],))]
    out['missing_pieces'] = [{'title': r['title'][:200], 'detail': (r.get('content') or '')[:300]} for r in
                             _rows(desk, "SELECT title,content FROM legal_memory_records WHERE matter=? AND status IN ('validated','pinned') "
                                         "AND (title LIKE '%manqu%' OR content LIKE '%manqu%' OR title LIKE '%à produire%' OR title LIKE '%a produire%') ORDER BY updated DESC LIMIT 8", (matter['id'],))]
    out['pleading_notes'] = [{'label': r['label'][:160], 'target': r.get('target') or '', 'updated': str(r.get('updated', ''))[:16]} for r in
                             _rows(desk, "SELECT label,target,updated FROM production_deliverables_v420 WHERE matter=? AND status='verified' ORDER BY updated DESC LIMIT 20", (matter['id'],))
                             if any(w in fold(r['label']) for w in HEARING_WORDS)]
    questions, last_prep = [], None
    for p in _rows(desk, 'SELECT id,status,instruction,data,created FROM hearing_projects_v250 WHERE matter=? ORDER BY created DESC LIMIT 1', (matter['id'],)):
        last_prep = {'id': p['id'], 'status': p['status'], 'instruction': p['instruction'][:200], 'created': p['created'][:16]}
        try:
            data = json.loads(p['data'] or '{}')
        except ValueError:
            data = {}
        for key in ('questions_probables', 'probable_questions', 'questions', 'anticipated_questions'):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, list):
                questions = [str(x.get('question') if isinstance(x, dict) else x)[:300] for x in value][:12]
                break
    out['last_preparation'] = last_prep
    out['probable_questions'] = questions
    reviewed = {r['item'] for r in _rows(desk, 'SELECT item FROM cockpit530_reviewed')}
    from .common import digest
    out['drafts_to_review'] = [{'request': r['id'], 'path': r['path'], 'name': PurePosixPath(r['path']).name, 'updated': r['updated'][:16]}
                               for r in _rows(desk, "SELECT id,path,updated FROM docreq520 WHERE matter=? AND status='cree' ORDER BY updated DESC LIMIT 20", (matter['id'],))
                               if 'doc:' + digest(r['path'])[:24] not in reviewed][:10]
    if not out['hearings']:
        out['notes'].append('Aucune audience à venir dans l’agenda synchronisé pour ce dossier.')
    if not questions:
        out['notes'].append('Questions probables : disponibles après une préparation d’audience complète (Audiences › Préparer).')
    return out


def page(desk, auth, prefix, env, args):
    from .web440 import shell
    mid = str(args.get('matter') or '')
    labels = sorted(((m['id'], matter_display(m)) for m in load_matters(desk.c)), key=lambda x: fold(x[1]))
    options = ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == mid else '', e(v)) for k, v in labels)
    body = ('<h1>Fiche d’audience</h1><p>Pour une audience prochaine : dernières conclusions identifiées, pièces et tâches encore attendues, notes de '
            'plaidoirie, questions probables et projets à relire, réunis sans nouvel appel au modèle.</p>'
            '<form method="get" action="%s/audience" class="m5-form m5-inline"><label class="m5-field">Dossier <select name="matter">%s</select></label>'
            '<button class="ax-btn" type="submit">Afficher la fiche</button></form>') % (e(prefix, quote=True), '<option value="">Choisir…</option>' + options)
    if mid:
        try:
            s = sheet(desk, mid)
        except Stop as ex:
            return shell('Fiche d’audience', body + '<p class="notice">%s</p>' % e(str(ex)), prefix, auth['csrf'], '/production')
        def block(title, items, render, empty):
            inner = ''.join('<li>%s</li>' % render(x) for x in items) if items else '<li class="muted">%s</li>' % e(empty)
            return '<section class="ax-card"><h2>%s</h2><ul>%s</ul></section>' % (e(title), inner)
        body += '<h2>%s</h2>' % e(s['label'])
        body += block('Audience(s) à venir', s['hearings'], lambda x: '%s — %s%s' % (e(x['starts']), e(x['title']), (' · ' + e(x['location'])) if x['location'] else ''), 'Aucune audience dans l’agenda.')
        body += block('Dernières conclusions identifiées', s['conclusions'], lambda x: '%s <small>(%s · %s)</small>' % (e(x['name']), e(x['side']), e(x['modified'])), 'Aucun fichier « conclusions » dans le dossier.')
        body += block('Pièces et tâches attendues', s['missing_pieces'] + [{'title': t['title'] + (' — pour le ' + t['due'] if t['due'] else ''), 'detail': ''} for t in s['tasks']],
                      lambda x: e(x['title']) + (('<br><small>' + e(x['detail']) + '</small>') if x.get('detail') else ''), 'Rien d’attendu dans le registre.')
        body += block('Notes de plaidoirie produites', s['pleading_notes'], lambda x: '%s <small>%s</small>' % (e(x['label']), e(x['updated'])), 'Aucune note de plaidoirie vérifiée.')
        body += block('Questions probables', s['probable_questions'], lambda x: e(x), 'Disponibles après une préparation d’audience complète.')
        body += block('Projets à relire', s['drafts_to_review'], lambda x: '<a href="%s">%s</a> <small>%s</small>' % (
            e(prefix + '/documents/edit?path=' + x['path'] + '&matter=' + s['matter'], quote=True), e(x['name']), e(x['updated'])), 'Aucun projet en attente de relecture.')
        if s['notes']:
            body += '<p class="vf-note">%s</p>' % ' '.join(e(n) for n in s['notes'])
        body += '<p><a class="ax-btn" href="%s/audiences-word?matter=%s">Préparer la plaidoirie (analyse complète)</a></p>' % (e(prefix, quote=True), e(s['matter'], quote=True))
    return shell('Fiche d’audience', body, prefix, auth['csrf'], '/production')
