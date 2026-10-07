"""5.6.9 : « À décider » sur la page Aujourd'hui.

Tout ce qui attend l'avocat — document dont le dossier est ambigu ou la date de création non prouvée, suite de mission bloquée,
engagement à préciser — est rassemblé en tête de page avec la décision à portée de clic. Rien n'est décidé à sa place : un dossier
ambigu reste proposé, jamais choisi d'office.
"""
from html import escape as e
import json
from pathlib import PurePosixPath
import sqlite3

from .common import load_matters, matter_display


def _labels(desk):
    return {str(m['id']): matter_display(m) for m in load_matters(desk.c)}


def items(desk, owner='cabinet', prefix='', limit=20):
    from .web568 import human
    labels = _labels(desk)
    out = []
    try:
        runs = desk.db.execute("SELECT r.id,r.matter,r.path,r.state,r.explanation,r.metadata,d.name FROM document_runs568 r "
                               "JOIN document_rules568 d ON d.id=r.rule_id WHERE r.owner=? AND r.state IN ('decision','error') "
                               "ORDER BY r.updated DESC LIMIT ?", (owner, limit)).fetchall()
        plans = desk.db.execute("SELECT id,title,matter FROM plans_v568 WHERE owner=? AND state='blocked' ORDER BY updated DESC LIMIT ?",
                                (owner, limit)).fetchall()
        commitments = desk.db.execute("SELECT id,matter,expected,state,condition_text FROM commitments_v568 WHERE owner=? "
                                      "AND state IN ('clarify','conditional') ORDER BY updated DESC LIMIT ?", (owner, limit)).fetchall()
    except sqlite3.OperationalError:          # tables absentes avant la première migration
        return out
    for r in runs:
        meta = json.loads(r['metadata'] or '{}')
        out.append({'kind': 'document', 'id': r['id'], 'state': r['state'],
                    'title': r['name'] + ' — ' + PurePosixPath(r['path']).name, 'matter': r['matter'],
                    'matter_label': labels.get(r['matter'], 'Dossier à préciser'), 'explanation': human(r['explanation']) if r['explanation'] else '',
                    'candidates': [{'id': c, 'label': labels.get(c, c)} for c in meta.get('candidates', []) if c in labels],
                    'url': prefix + '/agents-documents'})
    for p in plans:
        out.append({'kind': 'plan', 'id': p['id'], 'state': 'blocked', 'title': p['title'], 'matter': p['matter'],
                    'matter_label': labels.get(p['matter'], ''), 'explanation': 'Une étape attend une décision ou une reprise.',
                    'candidates': [], 'url': prefix + '/engagements'})
    for c in commitments:
        out.append({'kind': 'commitment', 'id': c['id'], 'state': c['state'], 'title': 'Engagement : ' + c['expected'][:160],
                    'matter': c['matter'], 'matter_label': labels.get(c['matter'], ''),
                    'explanation': ('Condition à confirmer : ' + c['condition_text'][:200]) if c['state'] == 'conditional' else 'Dossier ou échéance à préciser.',
                    'candidates': [], 'url': prefix + '/engagements'})
    out += _extra_items(desk, owner, prefix, labels, limit)
    return out


def _extra_items(desk, owner, prefix, labels, limit):
    """5.6.13 : missions manuelles bloquées, demandes de document sans dossier ou en échec, courriels à rattacher, règles proposées."""
    out = []
    linked = set()
    try:
        from .missions567 import get as mission_get, ensure_schema
        ensure_schema(desk)
        rows = desk.db.execute("SELECT id,ref FROM missions_v567 WHERE owner=? AND state IN ('decision','error','creating','queued','running') ORDER BY updated DESC LIMIT ?",
                               (owner, limit * 3)).fetchall()
        for r in rows:
            if r['ref'].startswith('docreq:'):
                linked.add(r['ref'][7:])
        for r in rows:
            m = mission_get(desk, r['id'], owner, prefix=prefix)
            if m['state'] not in ('decision', 'error'):
                continue
            candidates = []
            for x in m.get('exceptions', []):
                candidates += [c for c in x.get('candidates', []) if c.get('id') in labels]
            explanation = ' '.join(x.get('message', '') for x in m.get('exceptions', [])) or (m.get('job') or {}).get('error', '') or 'Traitement interrompu.'
            out.append({'kind': 'mission', 'id': m['id'], 'state': m['state'], 'title': 'Mission : ' + m['instruction'][:160], 'matter': m['matter'],
                        'matter_label': m.get('matter_label', ''), 'explanation': explanation[:300],
                        'candidates': [{'id': c['id'], 'label': c.get('label') or labels.get(c['id'], c['id'])} for c in candidates[:8]], 'url': prefix + '/missions'})
    except sqlite3.OperationalError:
        pass
    try:
        from .docrequest520 import ensure_schema as docreq_schema
        docreq_schema(desk)
        for r in desk.db.execute("SELECT id,matter,request,status,result FROM docreq520 WHERE status IN ('dossier_a_choisir','echec') ORDER BY updated DESC LIMIT ?", (limit,)):
            if r['id'] in linked:
                continue
            res = json.loads(r['result'] or '{}')
            out.append({'kind': 'docreq', 'id': r['id'], 'state': r['status'], 'title': 'Document demandé : ' + r['request'][:160], 'matter': r['matter'],
                        'matter_label': labels.get(r['matter'], 'Dossier à préciser'),
                        'explanation': 'Dossier à préciser pour rédiger ce document.' if r['status'] == 'dossier_a_choisir' else 'Dépôt ou rédaction en échec : ' + str(res.get('error', 'action_interrompue'))[:160],
                        'candidates': [c for c in res.get('candidates', []) if c.get('id') in labels][:8], 'url': prefix + '/aujourdhui'})
    except sqlite3.OperationalError:
        pass
    try:
        from .state import State
        n = State(desk.c['state_dir']).db.execute("SELECT COUNT(*) FROM messages WHERE status='review' AND (reason LIKE '%correspondant%' OR reason LIKE '%dossier%')").fetchone()[0]
        if n:
            out.append({'kind': 'mails', 'id': 'mails', 'state': 'review', 'title': '%d courriel(s) à rattacher à un dossier ou à un correspondant' % n, 'matter': '',
                        'matter_label': '', 'explanation': 'Sans rattachement, aucun brouillon n’est préparé pour ces courriels.', 'candidates': [], 'url': prefix + '/associations'})
    except Exception:
        pass
    try:
        from .pilote5613 import rule_proposals
        for p in rule_proposals(desk)[:limit]:
            out.append({'kind': 'rule', 'id': p['id'], 'state': 'proposed', 'title': 'Règle proposée après votre correction', 'matter': p['matter'],
                        'matter_label': labels.get(p['matter'], ''), 'explanation': p['instruction'][:300], 'candidates': [], 'url': prefix + '/progres'})
    except sqlite3.OperationalError:
        pass
    return out


def _matter_options(labels, candidates, current):
    suggested = ''.join('<option value="%s"%s>%s (suggéré)</option>' % (e(c['id'], quote=True), ' selected' if c['id'] == current else '', e(c['label']))
                        for c in candidates)
    known = {c['id'] for c in candidates}
    rest = ''.join('<option value="%s"%s>%s</option>' % (e(mid, quote=True), ' selected' if mid == current and mid not in known else '', e(label))
                   for mid, label in sorted(labels.items(), key=lambda kv: kv[1].casefold()) if mid not in known)
    return ('<option value="">Dossier…</option>' + (('<optgroup label="Suggérés">' + suggested + '</optgroup>') if suggested else '')
            + '<optgroup label="Tous les dossiers">' + rest + '</optgroup>')


def html(desk, owner='cabinet', prefix=''):
    rows = items(desk, owner, prefix)
    if not rows:
        return ''
    labels = _labels(desk)
    parts = []
    for x in rows:
        head = ('<strong>%s</strong>%s<p class="c569-why">%s</p>' % (
            e(x['title']), (' <span class="c530-sub">%s</span>' % e(x['matter_label'])) if x['matter_label'] else '', e(x['explanation'])))
        if x['kind'] == 'document':
            actions = ('<label class="c530-sr" for="c569-m-%s">Dossier</label><select id="c569-m-%s" data-field="matter">%s</select>'
                       '<input data-field="created" placeholder="Date de création (ex. 2026-10-06T10:00:00+02:00)" aria-label="Date de création prouvée">'
                       '<input data-field="proof" placeholder="Preuve ou explication" aria-label="Preuve">'
                       '<button type="button" class="ax-btn" data-act="resolve">Confirmer et reprendre</button>'
                       '<button type="button" class="ax-btn ghost" data-act="retry">Relancer</button>'
                       '<button type="button" class="ax-btn ghost" data-act="dismiss">Écarter</button>') % (
                e(x['id'], quote=True), e(x['id'], quote=True), _matter_options(labels, x['candidates'], x['matter']))
        elif x['kind'] == 'plan':
            actions = '<button type="button" class="ax-btn" data-act="plan-resume">Reprendre la suite</button>'
        elif x['kind'] == 'mission':
            if x['state'] == 'decision':
                actions = ('<label class="c530-sr" for="c569-m-%s">Dossier</label><select id="c569-m-%s" data-field="matter">%s</select>'
                           '<button type="button" class="ax-btn" data-act="mission-resolve">Préciser et démarrer</button>') % (
                    e(x['id'], quote=True), e(x['id'], quote=True), _matter_options(labels, x['candidates'], x['matter']))
            else:
                actions = '<button type="button" class="ax-btn" data-act="mission-resume">Relancer</button>'
        elif x['kind'] == 'docreq':
            actions = ('<label class="c530-sr" for="c569-m-%s">Dossier</label><select id="c569-m-%s" data-field="matter">%s</select>'
                       '<button type="button" class="ax-btn" data-act="docreq-resolve">%s</button>') % (
                e(x['id'], quote=True), e(x['id'], quote=True), _matter_options(labels, x['candidates'], x['matter']),
                'Choisir le dossier et rédiger' if x['state'] == 'dossier_a_choisir' else 'Relancer')
        elif x['kind'] == 'mails':
            actions = '<a class="ax-btn" href="%s">Rattacher</a>' % e(x['url'], quote=True)
        elif x['kind'] == 'rule':
            actions = ('<button type="button" class="ax-btn" data-act="rule-adopt">Adopter pour le cabinet</button>'
                       + ('<button type="button" class="ax-btn ghost" data-act="rule-adopt-matter">Pour ce dossier seulement</button>' if x['matter'] else '')
                       + '<button type="button" class="ax-btn ghost" data-act="rule-ignore">Ignorer</button>')
        else:
            actions = ''
        parts.append('<li class="c569-item" data-kind="%s" data-id="%s">%s<div class="c569-actions">%s<a class="ax-btn ghost" href="%s">Détail</a></div></li>'
                     % (e(x['kind']), e(x['id'], quote=True), head, actions, e(x['url'], quote=True)))
    return ('<section class="c530-card c569-decisions" id="c569-decisions" aria-labelledby="c569-title"><h2 id="c569-title">À décider '
            '<span class="c530-badge">%d</span></h2><p class="c530-sub">L’agent s’est arrêté là où votre décision est nécessaire. '
            'Un dossier suggéré n’est jamais choisi à votre place.</p><ul class="c569-list">%s</ul><p id="c569-status" role="status"></p></section>'
            % (len(rows), ''.join(parts)))
