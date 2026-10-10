"""Fiche de dossier d'une page et chronologie (AxiorHub 4.6.0).

Toute information affichée porte sa source ; une information non validée est marquée comme telle.
Rien n'est inventé : les valeurs viennent de la mémoire de dossier (validée ou proposée), des échéances,
des courriels et des pièces indexées.
"""
from datetime import date, datetime, timezone
import json
from pathlib import PurePosixPath
from urllib.parse import urlencode

from .common import Stop, load_matters
from .legal_memory import ensure_schema as ensure_memory, memory_records, timeline as memory_timeline
from .improvements36 import matter_option

STATUS_LABELS = {'validated': 'Validé', 'pinned': 'Validé', 'suggested': 'À valider', 'disputed': 'Refusé par l’avocat', 'proof_changed': 'À revalider (preuve modifiée)',
                 'configured': 'Configuration du dossier', 'derived': 'Non validé'}
KIND_LABELS = {'document': 'Pièce', 'email_received': 'Courriel reçu', 'email_sent': 'Courriel envoyé', 'draft': 'Brouillon',
               'task': 'Tâche', 'calendar': 'Agenda', 'fact': 'Fait', 'deadline': 'Échéance', 'completed_action': 'Acte accompli',
               'start_event': 'Événement de départ'}


def find_matter(desk, mid):
    found = next((m for m in load_matters(desk.c) if m['id'] == mid), None)
    if not found:
        raise Stop('dossier_absent')
    return found


def doc_href(path, mid):
    return '/documents/edit?' + urlencode({'path': path, 'matter': mid})


def _mail_known(desk, key):
    try:
        return bool(desk.db.execute('SELECT 1 FROM work_items WHERE mail_key=?', (key,)).fetchone())
    except Exception:
        return False


def source_of(desk, mid, ev):
    """(libellé, href) de la source d'une ligne de chronologie. Le libellé n'est jamais vide."""
    kind, path, sid = ev.get('source_kind', ''), ev.get('source_path', ''), ev.get('source_id', '')
    if kind == 'document' and path.startswith('/'):
        return PurePosixPath(path).name, doc_href(path, mid)
    if kind == 'mail_report' and sid:
        return 'Courriel et projet de réponse', '/mail?' + urlencode({'key': sid})
    if kind in ('email_received', 'email_history', 'email_sent'):
        if _mail_known(desk, sid):
            return 'Courriel conservé', '/mail?' + urlencode({'key': sid})
        return 'Courriel indexé (%s)' % (path or 'historique'), ''
    if kind == 'task':
        return 'Tâche du dossier', '/planning'
    if kind == 'calendar':
        return 'Agenda Nextcloud', '/planning'
    if kind == 'legal_memory':
        return ('Mémoire du dossier · ' + PurePosixPath(path).name) if path.startswith('/') else 'Mémoire du dossier', '/fiche?' + urlencode({'matter': mid}) + '#faits'
    if kind == 'echeance450':
        return 'Échéances de procédure', '/echeances'
    if path.startswith('/'):
        return PurePosixPath(path).name, doc_href(path, mid)
    return kind or 'Source non précisée', ''


def chronologie(desk, mid, limit=200, kinds=None):
    """Frise du dossier : courriels, pièces, actes, échéances, agenda, tâches, faits ; chaque ligne renvoie à sa source."""
    find_matter(desk, mid)
    ensure_memory(desk)
    rows = []
    for ev in memory_timeline(desk, mid, 500):
        if ev['source_kind'] == 'legal_memory' and ev['status'] in ('disputed', 'archived'):
            continue   # 5.6.24 (F28) : un fait refusé n'apparaît plus « à valider »
        label, href = source_of(desk, mid, ev)
        validated = ev['source_kind'] != 'legal_memory' or ev['status'] in ('validated', 'pinned')
        rows.append({'at': ev['event_at'], 'kind': ev['event_type'], 'kind_label': KIND_LABELS.get(ev['event_type'], ev['event_type']),
                     'title': ev['title'], 'detail': (ev['detail'] or '')[:300], 'source': label, 'href': href, 'source_id': ev['source_id'],
                     'validated': validated, 'status_label': '' if validated else STATUS_LABELS.get(ev['status'], STATUS_LABELS['suggested'])})
    from . import echeances450 as ech
    ech.ensure_schema(desk)
    for d in ech.listing(desk, mid, True):
        src = d['source_path'].rsplit('/', 1)[-1] if d['source_path'] else 'Saisie à la main'
        href = doc_href(d['source_path'], mid) if d['source_path'] else '/echeances'
        rows.append({'at': d['start_date'], 'kind': 'start_event', 'kind_label': KIND_LABELS['start_event'], 'source_id': 'ech-start-' + d['id'],
                     'title': '%s (%s)' % (d['start_event_label'], d['rule_label']), 'detail': d['source_excerpt'][:300],
                     'source': src, 'href': href, 'validated': d['status'] not in ('a_confirmer', 'a_completer'),
                     'status_label': '' if d['status'] not in ('a_confirmer', 'a_completer') else 'À confirmer'})
        if d['due']:
            rows.append({'at': d['due'], 'kind': 'deadline', 'kind_label': KIND_LABELS['deadline'], 'title': 'Échéance : ' + d['rule_label'], 'source_id': 'ech-due-' + d['id'],
                         'detail': d['status_label'], 'source': 'Échéances de procédure', 'href': '/echeances#e-' + d['id'],
                         'validated': d['status'] not in ('a_confirmer', 'a_completer'),
                         'status_label': '' if d['status'] not in ('a_confirmer', 'a_completer') else 'À confirmer'})
    if kinds:
        rows = [r for r in rows if r['kind'] in kinds]
    # 5.6.24 (F29) : déduplication par identité de source (deux courriels de même objet le même jour restent deux lignes) ;
    # dates connues triées par valeur normalisée, dates inconnues ou partielles présentées à part, après les dates connues.
    seen, unique = set(), []
    for r in rows:
        key = (r['kind'], r.get('source_id') or '', r['at'][:10], '' if r.get('source_id') else r['title'])
        if key not in seen:
            seen.add(key)
            r['undated'] = _undated(r['at'])
            unique.append(r)
    dated = sorted([r for r in unique if not r['undated']], key=lambda r: _moment(r['at']), reverse=True)
    undated = sorted([r for r in unique if r['undated']], key=lambda r: (r['at'], r['title']))
    return (dated + undated)[:max(1, min(int(limit), 500))]


def _moment(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _undated(value):
    value = str(value or '')
    if len(value) < 10:
        return True
    try:
        _moment(value)
        return False
    except ValueError:
        return True


def _fact(desk, mid, r):
    sources = []
    for s in r['source_snapshot'][:3]:
        path = s.get('path', '')
        sources.append({'label': PurePosixPath(path).name if path.startswith('/') else (path or 'Source'),
                        'href': doc_href(path, mid) if path.startswith('/') else '', 'excerpt': s.get('excerpt', '')[:200]})
    conf = 'high' if r['confidence'] >= 0.85 else 'medium' if r['confidence'] >= 0.6 else 'low'
    proof = 'perimee' if r['status'] == 'suggested' and str(r.get('validation_note') or '').startswith('Preuve modifiée') else ''
    return {'id': r['id'], 'value': r['title'].split(' : ', 1)[-1] if r['record_type'] in ('party',) else r['content'],
            'title': r['title'], 'status': r['status'], 'status_label': STATUS_LABELS['proof_changed'] if proof else STATUS_LABELS.get(r['status'], r['status']),
            'confidence': conf, 'sources': sources, 'validated': r['status'] in ('validated', 'pinned'), 'event_date': r['event_date'],
            'revision': r.get('revision'), 'actor': r.get('actor', ''), 'proof_state': proof, 'note': r.get('validation_note', '')}


def _best(records, kind):
    cands = [r for r in records if r['record_type'] == kind and r['status'] in ('pinned', 'validated', 'suggested')]
    rank = {'pinned': 0, 'validated': 1, 'suggested': 2}
    cands.sort(key=lambda r: (rank[r['status']], -r['confidence']))
    return cands[0] if cands else None


def _notice_value(desk, mid, key):
    try:
        rows = desk.db.execute("SELECT path,result FROM notices440 WHERE matter=? AND status='done' ORDER BY updated DESC", (mid,)).fetchall()
    except Exception:
        return None
    for row in rows:
        try:
            value = json.loads(row['result']).get(key, '')
        except ValueError:
            continue
        if value:
            return value, row['path']
    return None


def build_fiche(desk, mid, today=None):
    m = find_matter(desk, mid)
    today = today or date.today()
    ensure_memory(desk)
    records = memory_records(desk, mid, None, 500)
    fiche = {'matter': {'id': mid, 'label': matter_option(m), 'path': m.get('path', '')}, 'today': today.isoformat()}
    parties = [_fact(desk, mid, r) for r in records if r['record_type'] == 'party' and r['status'] in ('pinned', 'validated', 'suggested')]
    client = m.get('client_name', '')
    if client:
        parties.insert(0, {'id': '', 'value': client, 'title': client, 'status': 'configured', 'status_label': STATUS_LABELS['configured'],
                           'confidence': 'high', 'sources': [{'label': 'Configuration du dossier', 'href': '', 'excerpt': ''}], 'validated': True, 'event_date': ''})
    try:   # 5.6.27 : mêmes parties que l'onglet Parties (fiche de travail, sinon propositions fiables seulement)
        from .dossier5624 import rubrique
        parties = [{'event_date': '', **p} for p in rubrique(desk, mid, 'parties')['items']]
    except Stop:
        pass
    fiche['parties'] = parties
    for field, kind, key in (('jurisdiction', 'jurisdiction', 'jurisdiction'), ('case_number', 'case_number', 'rg')):
        best = _best(records, kind)
        if best:
            fiche[field] = _fact(desk, mid, best)
        else:
            derived = _notice_value(desk, mid, key)
            fiche[field] = ({'id': '', 'value': derived[0], 'title': derived[0], 'status': 'derived', 'status_label': STATUS_LABELS['derived'],
                             'confidence': 'medium', 'sources': [{'label': PurePosixPath(derived[1]).name, 'href': doc_href(derived[1], mid), 'excerpt': ''}],
                             'validated': False, 'event_date': ''} if derived else None)
    from . import echeances450 as ech
    ech.ensure_schema(desk)
    upcoming = [d for d in ech.listing(desk, mid) if d['due']]
    if upcoming:
        d = upcoming[0]
        days = (date.fromisoformat(d['due']) - today).days
        fiche['next_deadline'] = {'id': d['id'], 'label': d['rule_label'], 'due': d['due'], 'due_label': d['due_label'], 'days': days,
                                  'validated': d['status'] not in ('a_confirmer', 'a_completer'), 'status_label': d['status_label'],
                                  'href': '/echeances#e-' + d['id'],
                                  'source': d['source_path'].rsplit('/', 1)[-1] if d['source_path'] else 'Saisie à la main',
                                  'source_href': doc_href(d['source_path'], mid) if d['source_path'] else '',
                                  'calc': (d['calc'] or {}).get('steps', [])}
    else:
        fiche['next_deadline'] = None
    events = memory_timeline(desk, mid, 500)

    def last(types):
        for ev in events:
            if ev['event_type'] in types and ev['source_kind'] != 'calendar':
                label, href = source_of(desk, mid, ev)
                return {'at': ev['event_at'], 'title': ev['title'], 'source': label, 'href': href, 'kind_label': KIND_LABELS.get(ev['event_type'], '')}
        return None

    fiche['last_received'] = last(('email_received', 'document'))
    fiche['last_sent'] = last(('email_sent',))
    pending = []
    for d in ech.listing(desk, mid):
        if d['status'] in ('a_confirmer', 'a_completer'):
            pending.append({'kind': 'echeance', 'label': 'Échéance %s : %s' % ('à confirmer' if d['status'] == 'a_confirmer' else 'à compléter', d['rule_label']),
                            'href': '/echeances#e-' + d['id']})
    for r in desk.db.execute("SELECT mail_key,state,subject,received FROM work_items WHERE matter=? AND state IN ('draft_ready','needs_action') ORDER BY updated DESC LIMIT 10", (mid,)):
        if r['state'] == 'draft_ready':
            pending.append({'kind': 'brouillon', 'label': 'Brouillon à relire : ' + (r['subject'] or 'sans objet'), 'href': '/courriels'})
        else:
            pending.append({'kind': 'courriel', 'label': 'Courriel à traiter : ' + (r['subject'] or 'sans objet'), 'href': '/mail?' + urlencode({'key': r['mail_key']})})
    to_validate = sum(1 for r in records if r['status'] == 'suggested')
    if to_validate:
        pending.append({'kind': 'faits', 'label': '%d fait(s) à valider' % to_validate, 'href': '/fiche?' + urlencode({'matter': mid}) + '#faits'})
    conflicts = desk.db.execute("SELECT COUNT(*) FROM legal_memory_conflicts WHERE matter=? AND status='open'", (mid,)).fetchone()[0]
    if conflicts:
        pending.append({'kind': 'contradiction', 'label': '%d contradiction(s) dans les faits' % conflicts, 'href': '/fiche?' + urlencode({'matter': mid}) + '#faits'})
    for t in desk.db.execute("SELECT title,due FROM tasks WHERE matter=? AND status='open' ORDER BY due LIMIT 5", (mid,)):
        pending.append({'kind': 'tache', 'label': 'Tâche : %s%s' % (t['title'], ' (échéance %s)' % t['due'][:10] if t['due'] else ''), 'href': '/planning'})
    fiche['pending'] = pending
    docs = []
    try:
        from .index import DocumentIndex
        idx = DocumentIndex(desk.c['state_dir'])
        docs = [{'name': PurePosixPath(p).name, 'path': p, 'modified': mod, 'href': doc_href(p, mid)}
                for p, mod in idx.db.execute('SELECT path,modified FROM docs WHERE matter=? AND error="" ORDER BY modified DESC LIMIT 8', (mid,))]
    except Exception:
        docs = []
    fiche['documents'] = docs
    fiche['drafts'] = [{'subject': r['subject'] or 'sans objet', 'href': '/courriels'} for r in desk.db.execute(
        "SELECT subject FROM work_items WHERE matter=? AND state='draft_ready' ORDER BY updated DESC LIMIT 5", (mid,))]
    fiche['facts_to_validate'] = to_validate
    fiche['facts'] = [_fact(desk, mid, r) | {'type': r['record_type']} for r in records if r['status'] in ('suggested', 'validated', 'pinned')]   # 5.6.24 (F32) : plus de coupe à 60
    fiche['refused'] = sum(1 for r in records if r['status'] == 'disputed')
    return fiche
