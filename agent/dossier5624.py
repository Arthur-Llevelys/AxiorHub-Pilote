"""5.6.24 (F25, F26, F30, F32, F34) : dossier central — rubriques par dossier, analyse suivie étape par étape, fraîcheur des résultats.

- Une commande unique « Analyser le dossier » (travail ``analyser_dossier5624``) enchaîne indexation, recherche des faits (toutes les
  pièces, par pages), projection de la chronologie et synthèse. Chaque étape enregistre son état (en cours, ok, erreur, ignorée), la
  révision des sources consommée, ses compteurs et sa dernière réussite ; une étape en erreur donne un état partiel explicite, les
  autres continuent quand elles n'en dépendent pas. Les demandes identiques sont regroupées (un seul travail en file par dossier).
- La révision courante d'un dossier combine les pièces indexées (chemin, version, date), les courriels indexés et les décisions
  humaines sur les faits ; un résultat dont la révision consommée diffère est affiché « périmé », jamais « à jour » sur la seule
  existence d'une projection.
- Douze rubriques (synthèse, parties, juridiction et procédure, rappel des faits, chronologie, prétentions et arguments, pièces, textes
  et jurisprudence, écritures et projets, simulation, décisions, échéances et actions) lisent les registres existants (mémoire de
  dossier validée, fiche synthétique, échéances, pièces indexées, autorités vérifiées, missions, matrice de preuves, analyses
  stratégiques). Chaque rubrique indique données présentes, analyse en cours, réserve ou action nécessaire ; rien n'est inventé.
"""
from datetime import date, datetime
import hashlib
from html import escape as e
import json
from pathlib import PurePosixPath
from urllib.parse import urlencode

from .common import Stop, load_matters

SCHEMA = '''CREATE TABLE IF NOT EXISTS analyses5624(
 matter TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '', counters TEXT NOT NULL DEFAULT '{}',
 error TEXT NOT NULL DEFAULT '', started TEXT NOT NULL DEFAULT '', finished TEXT NOT NULL DEFAULT '', PRIMARY KEY(matter,step));'''
STEPS = (('indexation', 'Indexation des pièces et courriels'), ('faits', 'Recherche des faits dans les pièces'),
         ('chronologie', 'Chronologie du dossier'), ('synthese', 'Synthèse du dossier (modèle)'))
RUBRIQUES = (('synthese', 'Synthèse'), ('parties', 'Parties'), ('faits', 'Faits et procédure'), ('pretentions', 'Arguments des parties'),
             ('chronologie', 'Chronologie'), ('procedure', 'Juridiction et échéances'), ('pieces', 'Pièces'), ('textes', 'Textes et jurisprudence'),
             ('ecritures', 'Écritures et projets'), ('simulation', 'Simulation'), ('decisions', 'Décisions'), ('actions', 'Actions'))
STATE_LABELS = {'jamais': 'jamais lancée', 'en_cours': 'en cours', 'ok': 'terminée', 'erreur': 'en erreur', 'ignoree': 'ignorée', 'a_jour': 'à jour',
                'perimee': 'périmée (sources ou faits modifiés depuis)', 'partielle': 'partielle'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def source_revision(desk, mid):
    """Révision courante : pièces indexées (chemin, version, date), courriels indexés, décisions humaines sur les faits (F30)."""
    parts = []
    try:
        from .index import DocumentIndex
        idx = DocumentIndex(desk.c['state_dir'])
        parts.append(json.dumps([list(r) for r in idx.db.execute('SELECT path,etag,modified FROM docs WHERE matter=? ORDER BY path', (mid,))], default=str))
        parts.append(str(tuple(idx.db.execute('SELECT COUNT(*),MAX(modified) FROM knowledge_chunks WHERE matter=?', (mid,)).fetchone())))
    except Exception as ex:
        parts.append('index_indisponible:' + str(ex)[:40])
    try:
        from .legal_memory import ensure_schema as lm_schema
        lm_schema(desk)
        parts.append(str(tuple(desk.db.execute('SELECT COUNT(*),MAX(updated),SUM(revision) FROM legal_memory_records WHERE matter=?', (mid,)).fetchone())))
    except Exception:
        pass
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]


def _step(desk, mid, step, state, revision='', counters=None, error=''):
    now = desk.now()
    desk.db.execute('INSERT OR IGNORE INTO analyses5624 VALUES(?,?,?,?,?,?,?,?)', (mid, step, state, '', '{}', '', now, ''))
    if state == 'en_cours':
        desk.db.execute("UPDATE analyses5624 SET state='en_cours',error='',started=?,finished='' WHERE matter=? AND step=?", (now, mid, step))
    else:
        desk.db.execute('UPDATE analyses5624 SET state=?,revision=?,counters=?,error=?,finished=? WHERE matter=? AND step=?',
                        (state, revision, json.dumps(counters or {}, ensure_ascii=False, default=str), str(error or '')[:300], now, mid, step))
    desk.db.commit()


def _pending_job(desk, mid):
    try:
        return desk.db.execute("SELECT id,status FROM jobs WHERE kind='analyser_dossier5624' AND status IN ('pending','running') AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
    except Exception:
        return None


def state(desk, mid):
    """État de l'analyse du dossier : étapes, révision consommée, fraîcheur, travail en file, dernière réussite."""
    ensure_schema(desk)
    current = source_revision(desk, mid)
    rows = {r['step']: dict(r) for r in desk.db.execute('SELECT * FROM analyses5624 WHERE matter=?', (mid,))}
    steps, last_ok = [], ''
    for key, label in STEPS:
        r = rows.get(key)
        if not r:
            steps.append({'step': key, 'label': label, 'state': 'jamais', 'state_label': STATE_LABELS['jamais'], 'fresh': None, 'counters': {}, 'error': '', 'finished': ''})
            continue
        fresh = (r['revision'] == current) if r['state'] == 'ok' else None
        if r['state'] == 'ok' and r['finished'] > last_ok:
            last_ok = r['finished']
        steps.append({'step': key, 'label': label, 'state': r['state'], 'state_label': STATE_LABELS.get(r['state'], r['state']), 'fresh': fresh,
                      'counters': json.loads(r['counters'] or '{}'), 'error': r['error'], 'finished': r['finished'], 'revision': r['revision']})
    pending = _pending_job(desk, mid)
    if pending:
        overall = 'en_cours'
    elif all(s['state'] == 'jamais' for s in steps):
        overall = 'jamais'
    elif any(s['state'] == 'erreur' for s in steps):
        overall = 'partielle'
    elif any(s['fresh'] is False for s in steps):
        overall = 'perimee'
    else:
        overall = 'a_jour'
    return {'matter': mid, 'revision': current, 'steps': steps, 'job': pending['id'] if pending else None, 'overall': overall,
            'overall_label': STATE_LABELS.get(overall, overall), 'last_success': last_ok}


def request_analysis(desk, mid, steps=None):
    """Demande regroupée : un seul travail en file par dossier (F25)."""
    _matter(desk, mid)
    ensure_schema(desk)
    pending = _pending_job(desk, mid)
    if pending:
        return {'job_id': pending['id'], 'already': True, 'message': 'Analyse déjà en file (travail n° %d) : indexation, faits, chronologie, synthèse.' % pending['id']}
    args = {'matter': mid}
    if steps:
        args['steps'] = [s for s in steps if s in dict(STEPS)]
    job = desk.enqueue('analyser_dossier5624', args, priority=20)
    return {'job_id': job, 'already': False, 'message': 'Analyse du dossier lancée (travail n° %d) : indexation, faits, chronologie, synthèse.' % job}


def analyse(desk, args):
    """Travail « analyser_dossier5624 » : parcours complet et suivi ; une demande en file n'est jamais présentée comme une analyse terminée."""
    mid = str(args.get('matter') or '')
    _matter(desk, mid)
    ensure_schema(desk)
    wanted = set(args.get('steps') or [s for s, _ in STEPS])
    errors = {}
    if 'indexation' in wanted:
        _step(desk, mid, 'indexation', 'en_cours')
        try:
            counters = {'lots': 0}
            for _ in range(10):
                res = desk.perform('index', {'matter': mid}) or {}
                counters['lots'] += 1
                counters.update({k: res.get(k) for k in ('indexes', 'en_attente', 'contenu_modifie', 'fichiers') if k in res})
                if not res.get('en_attente'):
                    break
            _step(desk, mid, 'indexation', 'ok', source_revision(desk, mid), counters)
        except Exception as ex:   # pylint: disable=broad-except
            errors['indexation'] = str(ex)[:200]
            _step(desk, mid, 'indexation', 'erreur', '', {}, str(ex))
    if 'faits' in wanted:
        _step(desk, mid, 'faits', 'en_cours')
        try:
            from .facts460 import scan_matter
            res = scan_matter(desk, {'matter': mid, 'sync': False})
            _step(desk, mid, 'faits', 'ok', source_revision(desk, mid), {k: res.get(k) for k in ('documents', 'faits', 'nouveaux', 'modifies', 'inchanges', 'refus_respectes', 'erreurs', 'documents_peu_lisibles') if k in res})
        except Exception as ex:   # pylint: disable=broad-except
            errors['faits'] = str(ex)[:200]
            _step(desk, mid, 'faits', 'erreur', '', {}, str(ex))
    if 'chronologie' in wanted:
        _step(desk, mid, 'chronologie', 'en_cours')
        try:
            from .legal_memory import sync_matter_memory
            res = sync_matter_memory(desk, {'matter': mid})
            _step(desk, mid, 'chronologie', 'ok', source_revision(desk, mid), {'evenements': res['chronologie'], 'agenda': res['evenements_agenda'], 'agenda_erreur': res['agenda_erreur']})
        except Exception as ex:   # pylint: disable=broad-except
            errors['chronologie'] = str(ex)[:200]
            _step(desk, mid, 'chronologie', 'erreur', '', {}, str(ex))
    if 'synthese' in wanted:
        if 'indexation' in errors:
            _step(desk, mid, 'synthese', 'ignoree', '', {}, 'indexation en erreur : synthèse non recalculée')
        else:
            _step(desk, mid, 'synthese', 'en_cours')
            try:
                from .intelligence import refresh_brief
                res = refresh_brief(desk, {'matter': mid})
                _step(desk, mid, 'synthese', 'ok', source_revision(desk, mid), {'fiche_version': res.get('fiche_version'), 'sources': res.get('sources'), 'fichiers_en_attente': res.get('fichiers_en_attente')})
            except Exception as ex:   # pylint: disable=broad-except
                errors['synthese'] = str(ex)[:200]
                _step(desk, mid, 'synthese', 'erreur', '', {}, str(ex))
    st = state(desk, mid)
    desk.audit('dossier5624_analyse', {'matter': mid, 'etat': st['overall'], 'erreurs': sorted(errors)})
    done = [s['step'] for s in st['steps'] if s['state'] == 'ok']
    message = 'Analyse du dossier %s : %s.' % (STATE_LABELS.get(st['overall'], st['overall']), ', '.join(done) or 'aucune étape réussie')
    if errors:
        message += ' Étape(s) en erreur : ' + ' ; '.join('%s (%s)' % (k, v[:80]) for k, v in errors.items()) + '. Le bouton « Reprendre » relance ces seules étapes.'
    return {'status': 'prepared' if not errors else 'partial', 'matter': mid, 'etat': st['overall'], 'steps': {s['step']: s['state'] for s in st['steps']},
            'errors': errors, 'message': message}


# ------------------------------------------------------------------------------------------------ rubriques
def _records(desk, mid):
    from .legal_memory import memory_records
    return memory_records(desk, mid, None, 500)


def _fact_view(desk, mid, r):
    from .fiche460 import _fact
    return _fact(desk, mid, r) | {'type': r['record_type']}


def _brief(desk, mid):
    try:
        row = desk.db.execute('SELECT version,data,created FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        return {'version': row['version'], 'created': row['created'], 'data': json.loads(row['data'])}
    except ValueError:
        return None


def _docs(desk, mid, limit=500):
    try:
        from .index import DocumentIndex
        idx = DocumentIndex(desk.c['state_dir'])
        return [{'path': p, 'name': PurePosixPath(p).name, 'modified': str(mod or '')[:10], 'error': err or '', 'etag': etag or ''}
                for p, etag, mod, err in idx.db.execute('SELECT path,etag,modified,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT ?', (mid, limit))]
    except Exception:
        return []


def _mission_items(desk, mid, output_types, limit=40):
    out = []
    try:
        for a in desk.db.execute("SELECT a.output_type,a.content,a.created,m.title FROM artifacts5614 a JOIN missions5614 m ON m.id=a.mission "
                                 "WHERE m.matter=? AND a.state='actif' AND a.output_type IN (%s) ORDER BY a.created DESC LIMIT 12" % ','.join('?' * len(output_types)), (mid, *output_types)):
            content = json.loads(a['content'])
            items = content.get('items') if isinstance(content, dict) else None
            out.append({'type': a['output_type'], 'mission': a['title'], 'created': a['created'][:10], 'summary': (content.get('summary') if isinstance(content, dict) else '') or '',
                        'items': [i for i in (items or []) if isinstance(i, dict)][:limit], 'proposition': True})
    except Exception:
        pass
    return out


def rubrique(desk, mid, key):
    """Données d'une rubrique : ``state`` ∈ {present, vide, en_cours, reserve}, ``items``, ``note`` (ce qui manque ou l'action à faire)."""
    from . import fiche460
    st = state(desk, mid)
    busy = st['overall'] == 'en_cours'
    out = {'key': key, 'label': dict(RUBRIQUES).get(key, key), 'items': [], 'state': 'vide', 'note': '', 'analysis': st}
    records = _records(desk, mid) if key in ('parties', 'faits', 'pretentions', 'decisions', 'procedure', 'synthese') else []
    active = [r for r in records if r['status'] in ('pinned', 'validated', 'suggested')]
    refused = [r for r in records if r['status'] == 'disputed']
    if key == 'parties':
        m = _matter(desk, mid)
        items = [_fact_view(desk, mid, r) for r in active if r['record_type'] == 'party']
        if m.get('client_name'):
            items.insert(0, {'id': '', 'value': m['client_name'], 'title': m['client_name'], 'status': 'configured', 'status_label': 'Configuration du dossier', 'confidence': 'high',
                             'sources': [{'label': 'Configuration du dossier', 'href': '', 'excerpt': ''}], 'validated': True, 'type': 'party'})
        brief = _brief(desk, mid)
        for p in ((brief or {}).get('data') or {}).get('parties', []) or []:
            if isinstance(p, str) and not any(p.lower() in str(i.get('value', '')).lower() for i in items):
                items.append({'id': '', 'value': p, 'title': p, 'status': 'derived', 'status_label': 'Synthèse (non validé)', 'confidence': 'medium', 'sources': [{'label': 'Synthèse v%s' % brief['version'], 'href': '', 'excerpt': ''}], 'validated': False, 'type': 'party'})
        out['items'] = items
        out['refused'] = len([r for r in refused if r['record_type'] == 'party'])
    elif key == 'procedure':
        from . import echeances450 as ech
        ech.ensure_schema(desk)
        for kind in ('jurisdiction', 'case_number'):
            best = fiche460._best(records, kind)
            if best:
                out['items'].append({'kind': kind, 'label': 'Juridiction' if kind == 'jurisdiction' else 'Numéro de RG', **_fact_view(desk, mid, best)})
        acts = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('completed_action', 'deadline', 'planned_action')]
        acts.sort(key=lambda x: x.get('event_date') or '', reverse=True)
        out['actes'] = acts
        out['echeances'] = [{'label': d['rule_label'], 'due': d['due'], 'due_label': d['due_label'], 'status_label': d['status_label'], 'validated': d['status'] not in ('a_confirmer', 'a_completer'),
                             'href': '/echeances#e-' + d['id'], 'source': d['source_path'].rsplit('/', 1)[-1] if d['source_path'] else 'Saisie à la main'} for d in ech.listing(desk, mid)]
        try:
            prof = desk.db.execute("SELECT profile,state,updated FROM missions5614 WHERE matter=? AND profile<>'' ORDER BY updated DESC LIMIT 1", (mid,)).fetchone()
            out['profil'] = {'id': prof['profile'], 'mission_state': prof['state']} if prof else None
        except Exception:
            out['profil'] = None
    elif key == 'faits':
        facts = [_fact_view(desk, mid, r) for r in active]
        out['items'] = facts
        dated = [f for f in facts if f.get('event_date') and f['status'] in ('validated', 'pinned') and f.get('type') in ('event', 'completed_action', 'amount', 'claim', 'other')]
        dated.sort(key=lambda f: f['event_date'])
        out['rappel'] = dated
        out['refused'] = len(refused)
        out['counts'] = {'valides': sum(1 for f in facts if f['status'] in ('validated', 'pinned')), 'a_valider': sum(1 for f in facts if f['status'] == 'suggested'),
                         'refuses': len(refused), 'preuves_modifiees': sum(1 for f in facts if f.get('proof_state') == 'perimee')}
    elif key == 'chronologie':
        rows = fiche460.chronologie(desk, mid, 500)
        out['items'] = [r for r in rows if not r.get('undated')]
        out['undated'] = [r for r in rows if r.get('undated')]
    elif key == 'pretentions':
        out['items'] = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('claim', 'negotiation', 'instruction', 'open_question')]
        brief = _brief(desk, mid)
        data = (brief or {}).get('data') or {}
        out['synthese'] = {'claims': data.get('claims', []), 'negotiation_positions': data.get('negotiation_positions', []), 'open_questions': data.get('open_questions', []),
                           'version': (brief or {}).get('version')}
        matrix = []
        try:
            run = desk.db.execute('SELECT id,version,created FROM matrix_runs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
            if run:
                matrix = [dict(r) for r in desk.db.execute('SELECT proposition,asserted_by,proof_status,row_type,status,supporting_sources,contradicting_sources FROM evidence_matrix_rows WHERE run_id=? ORDER BY position LIMIT 200', (run['id'],))]
                out['matrice'] = {'version': run['version'], 'created': run['created'][:10], 'rows': matrix}
        except Exception:
            pass
        out['missions'] = _mission_items(desk, mid, ('matrice', 'analyse', 'conclusions', 'section'))
        try:
            sa = desk.db.execute('SELECT version,objective,status,data,created FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
            out['strategie'] = {'version': sa['version'], 'objective': sa['objective'], 'status': sa['status'], 'created': sa['created'][:10], 'data': json.loads(sa['data'])} if sa else None
        except Exception:
            out['strategie'] = None
    elif key == 'pieces':
        out['items'] = _docs(desk, mid)
        out['counts'] = {'total': len(out['items']), 'erreurs': sum(1 for d in out['items'] if d['error'])}
    elif key == 'textes':
        try:
            from .legal_research import ensure_schema as lr_schema
            lr_schema(desk)
            out['items'] = [dict(r) for r in desk.db.execute('SELECT id,identifier,ecli,court,decision_date,title,official_url,exact_excerpt,verification_status,official_text_sha256 FROM legal_authorities_v240 WHERE matter=? ORDER BY updated DESC LIMIT 200', (mid,))]
            out['pistes'] = desk.db.execute("SELECT COUNT(*) FROM legal_research_leads_v240 WHERE matter=? AND status<>'verified'", (mid,)).fetchone()[0]
        except Exception:
            out['items'], out['pistes'] = [], 0
        out['missions'] = _mission_items(desk, mid, ('recherche',))
    elif key == 'ecritures':
        try:
            out['items'] = [dict(r) for r in desk.db.execute('SELECT id,request,kind,status,path,revision_of,updated FROM docreq520 WHERE matter=? ORDER BY updated DESC LIMIT 100', (mid,))]
        except Exception:
            out['items'] = []
        try:
            out['missions'] = [dict(r) for r in desk.db.execute('SELECT id,title,state,validation,validated_hash,updated FROM missions5614 WHERE matter=? ORDER BY updated DESC LIMIT 50', (mid,))]
        except Exception:
            out['missions'] = []
        out['drafts'] = [{'subject': r['subject'] or 'sans objet'} for r in desk.db.execute("SELECT subject FROM work_items WHERE matter=? AND state='draft_ready' ORDER BY updated DESC LIMIT 20", (mid,))]
    elif key == 'simulation':
        try:
            out['items'] = [dict(r) for r in desk.db.execute('SELECT version,objective,status,created FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 20', (mid,))]
        except Exception:
            out['items'] = []
        try:
            out['actes'] = [dict(r) for r in desk.db.execute('SELECT version,act_type,title,status,created FROM act_projects WHERE matter=? ORDER BY version DESC LIMIT 20', (mid,))]
        except Exception:
            out['actes'] = []
        out['note'] = 'Une simulation motivée reste une hypothèse de travail : elle n’alimente jamais le registre des décisions obtenues.'
    elif key == 'decisions':
        words = ('jugement', 'ordonnance', 'arret', 'arrêt', 'decision', 'décision')
        out['items'] = [_fact_view(desk, mid, r) for r in active if r['record_type'] in ('event', 'completed_action') and any(w in r['title'].lower() for w in words)]
        try:
            out['avis'] = [dict(r) for r in desk.db.execute("SELECT path,status,result,updated FROM notices440 WHERE matter=? AND status='done' ORDER BY updated DESC LIMIT 20", (mid,))]
        except Exception:
            out['avis'] = []
    elif key == 'actions':
        f = fiche460.build_fiche(desk, mid)
        out['items'] = f['pending']
        out['next_deadline'] = f['next_deadline']
        out['tasks'] = [dict(t) for t in desk.db.execute("SELECT id,title,due,status FROM tasks WHERE matter=? AND status='open' ORDER BY due LIMIT 50", (mid,))]
    elif key == 'synthese':
        brief = _brief(desk, mid)
        out['brief'] = brief
        out['counts'] = {'faits_valides': sum(1 for r in active if r['status'] in ('validated', 'pinned')), 'faits_a_valider': sum(1 for r in active if r['status'] == 'suggested'),
                         'faits_refuses': len(refused), 'pieces': len(_docs(desk, mid))}
        try:
            out['counts']['evenements'] = desk.db.execute("SELECT COUNT(*) FROM timeline_events WHERE matter=? AND status NOT IN ('stale','disputed','archived')", (mid,)).fetchone()[0]
        except Exception:
            out['counts']['evenements'] = 0
        out['items'] = [brief] if brief else []
    if busy and not out['items']:
        out['state'] = 'en_cours'
    elif out['items'] or out.get('echeances') or out.get('actes') or out.get('rappel') or out.get('missions') or out.get('matrice'):
        out['state'] = 'present'
    else:
        out['state'] = 'vide'
    synth = next((s for s in st['steps'] if s['step'] == 'synthese'), None)
    if key in ('synthese', 'pretentions', 'parties') and synth and synth['fresh'] is False:
        out['freshness'] = 'perimee'
    elif synth and synth['state'] == 'ok':
        out['freshness'] = 'a_jour'
    else:
        out['freshness'] = 'inconnue'
    return out


# ------------------------------------------------------------------------------------------------ rendu
def _a(prefix, href, label, cls=''):
    return '<a%s href="%s">%s</a>' % ((' class="%s"' % cls) if cls else '', e(prefix + href, quote=True), e(label))


def _badge(status, label):
    cls = {'validated': 'ok', 'pinned': 'ok', 'configured': 'ok', 'suggested': 'warn', 'derived': 'warn', 'disputed': 'bad', 'perimee': 'warn'}.get(status, '')
    return '<span class="fc-badge %s">%s</span>' % (cls, e(label))


def _fact_li(prefix, x):
    src = ' · '.join((_a(prefix, s['href'], s['label']) if s.get('href') else e(s['label'])) for s in x.get('sources', [])[:2]) or 'Source non précisée'
    when = (' <span class="fc-muted">%s</span>' % e(str(x.get('event_date', ''))[:10])) if x.get('event_date') else ''
    return '<li><strong>%s</strong>%s %s<br><span class="fc-src">Source : %s</span></li>' % (e(x.get('value') or x.get('title', '')), when, _badge(x['status'], x['status_label']), src)


def tabs_html(prefix, mid, active):
    q = lambda key: '/fiche?' + urlencode({'matter': mid, 'onglet': key})
    return '<nav class="fc-tabs" aria-label="Rubriques du dossier">%s</nav>' % ''.join(
        '<a class="fc-tab%s" href="%s"%s>%s</a>' % (' on' if key == active else '', e(prefix + q(key), quote=True), ' aria-current="page"' if key == active else '', e(label))
        for key, label in RUBRIQUES)


def analysis_html(prefix, mid, st):
    steps = ''.join('<li class="fc-step fc-s-%s"><span class="fc-type">%s</span> %s%s%s</li>' % (
        e(s['state']), e(s['label']), e(s['state_label']), (' · ' + e(s['finished'][:16].replace('T', ' '))) if s.get('finished') else '',
        (' · <span class="fc-badge warn">périmée</span>' if s['fresh'] is False else '') + ((' · <span class="fc-badge bad">%s</span>' % e(s['error'][:120])) if s.get('error') else '')) for s in st['steps'])
    retry = ''
    if st['overall'] == 'partielle':
        retry = ' <button type="button" class="ax-btn ghost" id="fc-reprendre" data-steps="%s">Reprendre les étapes en erreur</button>' % e(','.join(s['step'] for s in st['steps'] if s['state'] == 'erreur'), quote=True)
    return ('<section class="ax-card fc-analysis" id="fc-analysis" data-matter="%s" data-job="%s"><h2>Analyse du dossier</h2><p>État : <strong>%s</strong>%s. '
            '<button type="button" class="ax-btn" id="fc-analyse">Analyser le dossier</button>%s <span id="fc-analyse-result" class="fc-muted"></span></p>'
            '<details><summary>Étapes et compteurs</summary><ul class="fc-list">%s</ul><p class="fc-muted">Révision courante des sources et faits : %s. Une demande en file n’est pas une analyse terminée ; '
            'les résultats antérieurs restent affichés avec leur fraîcheur.</p></details></section>') % (
        e(mid, quote=True), e(str(st['job'] or ''), quote=True), e(st['overall_label']), (' (dernière réussite : %s)' % e(st['last_success'][:16].replace('T', ' '))) if st['last_success'] else '',
        retry, steps, e(st['revision']))


def render(desk, prefix, mid, key, data):
    """HTML d'une rubrique (hors synthèse et faits, rendues par web460 avec les sections historiques)."""
    empty = lambda text: '<p class="fc-empty">%s</p>' % e(text)
    head = ''
    if data['state'] == 'en_cours':
        head = '<p class="fc-muted">Analyse en cours : les données ci-dessous sont celles de la dernière analyse terminée.</p>'
    if data.get('freshness') == 'perimee':
        head += '<p class="fc-muted"><span class="fc-badge warn">périmée</span> Sources ou faits modifiés depuis la dernière synthèse : relancez l’analyse.</p>'
    body = head
    if key == 'parties':
        body += '<ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data['items']) or '<li class="fc-empty">Aucune partie connue : lancez l’analyse ou validez les faits proposés.</li>')
        if data.get('refused'):
            body += '<p class="fc-muted">%d partie(s) refusée(s) par l’avocat : non réutilisées.</p>' % data['refused']
    elif key == 'procedure':
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li><span class="fc-type">%s</span> ' % e(x['label']) + _fact_li(prefix, x)[4:] for x in data['items']) or '<li class="fc-empty">Juridiction et numéro de RG non renseignés : aucune pièce ne les mentionne encore.</li>')
        if data.get('profil'):
            body += '<p>Profil procédural retenu par les missions : <strong>%s</strong> (mission %s).</p>' % (e(data['profil']['id']), e(data['profil']['mission_state']))
        body += '<h3>Actes de procédure</h3><ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data.get('actes', [])) or '<li class="fc-empty">Aucun acte enregistré dans les faits validés ou proposés.</li>')
        body += '<h3>Échéances</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s – <strong>%s</strong> %s<br><span class="fc-src">Source : %s · %s</span></li>' % (
            e(d['due_label'] or d['due'] or 'sans date'), e(d['label']), _badge('validated' if d['validated'] else 'suggested', 'Confirmée' if d['validated'] else d['status_label']), e(d['source']), _a(prefix, d['href'], 'voir'))
            for d in data.get('echeances', [])) or '<li class="fc-empty">Aucune échéance de procédure.</li>')
    elif key == 'chronologie':
        lines = ''.join('<li class="fc-line fc-k-%s"><time datetime="%s">%s</time><div><span class="fc-type">%s</span> <strong>%s</strong>%s<p class="fc-muted">%s</p><span class="fc-src">Source : %s</span></div></li>' % (
            e(r['kind']), e(r['at'][:10]), e(r['at'][:10]), e(r['kind_label']), e(r['title']), '' if r['validated'] else ' <span class="fc-badge warn">%s</span>' % e(r['status_label']),
            e(r['detail'][:200]), _a(prefix, r['href'], r['source']) if r['href'] else e(r['source'])) for r in data['items'][:200])
        body += '<p>%s</p><ol class="fc-timeline">%s</ol>' % (_a(prefix, '/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète et filtres'), lines or '<li class="fc-empty">Aucun événement daté.</li>')
        if data.get('undated'):
            body += '<h3>Dates inconnues ou partielles</h3><ul class="fc-list">%s</ul>' % ''.join('<li><span class="fc-type">%s</span> %s <span class="fc-muted">(%s)</span></li>' % (e(r['kind_label']), e(r['title']), e(r['at'] or 'date inconnue')) for r in data['undated'][:100])
    elif key == 'pretentions':
        body += '<h3>Prétentions, positions et instructions (faits du dossier)</h3><ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data['items']) or '<li class="fc-empty">Aucune prétention enregistrée.</li>')
        s = data.get('synthese') or {}
        if s.get('version'):
            body += '<h3>Synthèse v%s</h3>' % e(str(s['version']))
            for label, keyname in (('Demandes', 'claims'), ('Positions de négociation', 'negotiation_positions'), ('Questions ouvertes', 'open_questions')):
                if s.get(keyname):
                    body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s</li>' % e(str(x)) for x in s[keyname][:40]))
        if data.get('matrice'):
            m = data['matrice']
            body += '<h3>Matrice de preuves v%s (%s) — arguments des parties</h3><table class="fc-table"><thead><tr><th>Proposition</th><th>Soutenue par</th><th>Preuve</th><th>État</th></tr></thead><tbody>%s</tbody></table>' % (
                e(str(m['version'])), e(m['created']), ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['proposition'][:300]), e(r['asserted_by']), e(r['proof_status']), e(r['status'])) for r in m['rows']))
        if data.get('strategie'):
            st = data['strategie']
            body += '<h3>Analyse stratégique v%s (%s · %s)</h3><p class="fc-muted">%s</p>' % (e(str(st['version'])), e(st['created']), e(st['status']), e(st['objective'][:300]))
            d = st.get('data') or {}
            for keyname in ('arguments', 'arguments_adverses', 'risks', 'options', 'recommendation', 'summary'):
                v = d.get(keyname)
                if isinstance(v, list) and v:
                    body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(keyname.replace('_', ' ')), ''.join('<li>%s</li>' % e(json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x)[:400] for x in v[:30]))
                elif isinstance(v, str) and v:
                    body += '<p><strong>%s</strong> %s</p>' % (e(keyname), e(v[:1500]))
        for mi in data.get('missions', []):
            body += '<h3>Mission « %s » · %s (%s) <span class="fc-badge warn">proposition de l’agent</span></h3><p class="fc-muted">%s</p><ul class="fc-list">%s</ul>' % (
                e(mi['mission']), e(mi['type']), e(mi['created']), e(mi['summary'][:300]), ''.join('<li>%s%s</li>' % (e(str(i.get('texte', ''))[:400]), (' <span class="fc-muted">(%s)</span>' % e(str(i.get('partie') or i.get('statut') or ''))) if (i.get('partie') or i.get('statut')) else '') for i in mi['items'][:40]))
        if not (data['items'] or s.get('version') or data.get('matrice') or data.get('strategie') or data.get('missions')):
            body += empty('Aucun argument analysé : lancez l’analyse du dossier, une mission (conclusions, assignation) ou « Analyser la stratégie ».')
    elif key == 'pieces':
        from .fiche460 import doc_href
        body += '<p class="fc-muted">%d pièce(s) indexée(s), %d en erreur de lecture.</p><ul class="fc-list">%s</ul>' % (
            data['counts']['total'], data['counts']['erreurs'], ''.join('<li>%s <span class="fc-muted">%s</span>%s</li>' % (
                _a(prefix, doc_href(d['path'], mid), d['name']), e(d['modified']), (' <span class="fc-badge bad">%s</span>' % e(d['error'][:80])) if d['error'] else '') for d in data['items']) or '<li class="fc-empty">Aucune pièce indexée : lancez l’analyse du dossier.</li>')
    elif key == 'textes':
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li><strong>%s</strong> %s<br><span class="fc-muted">%s</span><br><a href="%s" rel="noopener noreferrer" target="_blank">texte officiel</a> · <span class="fc-src">empreinte %s</span></li>' % (
            e(x['identifier'] or x['ecli'] or x['title']), _badge('validated' if x['verification_status'] == 'verified' else 'suggested', 'vérifiée' if x['verification_status'] == 'verified' else x['verification_status']),
            e((x['exact_excerpt'] or '')[:300]), e(x['official_url'], quote=True), e((x['official_text_sha256'] or '')[:12])) for x in data['items']) or '<li class="fc-empty">Aucune référence vérifiée pour ce dossier.</li>')
        if data.get('pistes'):
            body += '<p class="fc-muted">%d piste(s) de recherche non vérifiée(s) : non citables.</p>' % data['pistes']
    elif key == 'ecritures':
        body += '<h3>Documents demandés au Pilote</h3><ul class="fc-list">%s</ul>' % (''.join('<li><strong>%s</strong> <span class="fc-muted">%s · %s · %s</span>%s</li>' % (
            e(x['request'][:160]), e(x['kind']), e(x['status']), e(str(x['updated'])[:10]), (' · ' + e(PurePosixPath(x['path']).name)) if x['path'] else '') for x in data['items']) or '<li class="fc-empty">Aucun document demandé.</li>')
        body += '<h3>Missions complexes</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s <span class="fc-muted">%s · validation : %s</span></li>' % (
            _a(prefix, '/missions-complexes?id=' + x['id'], x['title']), e(x['state']), e(x['validation'])) for x in data.get('missions', [])) or '<li class="fc-empty">Aucune mission.</li>')
        body += '<h3>Brouillons de courriels</h3><ul class="fc-list">%s</ul>' % (''.join('<li>%s</li>' % e(d['subject']) for d in data.get('drafts', [])) or '<li class="fc-empty">Aucun brouillon en attente.</li>')
    elif key == 'simulation':
        body += '<p class="fc-muted">%s</p>' % e(data['note'])
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li>Analyse stratégique v%s · %s · %s<br><span class="fc-muted">%s</span></li>' % (e(str(x['version'])), e(x['status']), e(str(x['created'])[:10]), e(x['objective'][:300])) for x in data['items']) or '<li class="fc-empty">Aucune simulation.</li>')
        if data.get('actes'):
            body += '<h3>Projets d’actes</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s · %s · %s</li>' % (e(x['title']), e(x['act_type']), e(x['status'])) for x in data['actes'])
    elif key == 'decisions':
        body += '<ul class="fc-list">%s</ul>' % (''.join(_fact_li(prefix, x) for x in data['items']) or '<li class="fc-empty">Aucune décision (jugement, ordonnance, arrêt) enregistrée dans les faits.</li>')
        if data.get('avis'):
            body += '<h3>Avis et actes analysés</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s <span class="fc-muted">%s</span></li>' % (e(PurePosixPath(x['path']).name), e(str(x['updated'])[:10])) for x in data['avis'])
    elif key == 'actions':
        body += '<ul class="fc-list">%s</ul>' % (''.join('<li>%s</li>' % _a(prefix, p['href'], p['label']) for p in data['items']) or '<li class="fc-empty">Rien en attente.</li>')
        if data.get('tasks'):
            body += '<h3>Tâches ouvertes</h3><ul class="fc-list">%s</ul>' % ''.join('<li>%s%s</li>' % (e(t['title']), (' <span class="fc-muted">(échéance %s)</span>' % e(t['due'][:10])) if t['due'] else '') for t in data['tasks'])
    return '<section class="ax-card fc-rubrique" id="rubrique-%s"><h2>%s</h2>%s</section>' % (e(key), e(data['label']), body)


def synthese_html(prefix, mid, data):
    brief = data.get('brief')
    c = data['counts']
    body = '<p class="fc-muted">%d fait(s) validé(s), %d à valider, %d refusé(s) · %d pièce(s) indexée(s) · %d événement(s) de chronologie.</p>' % (
        c['faits_valides'], c['faits_a_valider'], c['faits_refuses'], c['pieces'], c.get('evenements', 0))
    if data.get('freshness') == 'perimee':
        body += '<p><span class="fc-badge warn">périmée</span> Sources ou faits modifiés depuis la synthèse v%s : relancez l’analyse.</p>' % e(str((brief or {}).get('version', '')))
    if not brief:
        body += '<p class="fc-empty">Aucune synthèse calculée : lancez « Analyser le dossier » (la synthèse exige le modèle configuré).</p>'
    else:
        d = brief['data']
        body += '<p class="fc-muted">Synthèse v%s du %s (proposition du modèle, relue par l’avocat avant toute réutilisation).</p>' % (e(str(brief['version'])), e(str(brief['created'])[:10]))
        for label, keyname in (('Parties', 'parties'), ('Demandes', 'claims'), ('Dernières instructions', 'latest_instructions'), ('Positions de négociation', 'negotiation_positions'),
                               ('Actes accomplis', 'actions_completed'), ('Actions prévues', 'actions_planned'), ('Questions ouvertes', 'open_questions')):
            v = d.get(keyname)
            if isinstance(v, list) and v:
                body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s</li>' % e(str(x))[:400] for x in v[:30]))
        for label, keyname in (('Chronologie', 'chronology'), ('Échéances', 'deadlines'), ('Montants', 'amounts')):
            v = d.get(keyname)
            if isinstance(v, list) and v:
                body += '<p><strong>%s</strong></p><ul class="fc-list">%s</ul>' % (e(label), ''.join('<li>%s — %s</li>' % (e(str(x.get('date', ''))), e(str(x.get('text', ''))[:300])) for x in v[:40] if isinstance(x, dict)))
        if d.get('jurisdiction') or d.get('case_number'):
            body += '<p class="fc-muted">Juridiction : %s · RG : %s (selon la synthèse, à confirmer dans l’onglet Juridiction et procédure).</p>' % (e(str(d.get('jurisdiction') or '—')), e(str(d.get('case_number') or '—')))
    return '<section class="ax-card fc-rubrique" id="rubrique-synthese"><h2>Synthèse du dossier</h2>%s</section>' % body


def rappel_html(prefix, mid, data):
    items = data.get('rappel', [])
    c = data.get('counts', {})
    body = '<p class="fc-muted">Faits validés et datés, dans l’ordre chronologique ; chaque ligne renvoie à sa source. %d validé(s), %d à valider, %d refusé(s)%s.</p>' % (
        c.get('valides', 0), c.get('a_valider', 0), c.get('refuses', 0), (', %d dont la preuve a changé (à recontrôler)' % c['preuves_modifiees']) if c.get('preuves_modifiees') else '')
    body += '<ol class="fc-list">%s</ol>' % (''.join('<li><time>%s</time> — %s<br><span class="fc-src">Source : %s</span></li>' % (
        e(str(x['event_date'])[:10]), e(x.get('value') or x['title']), ' · '.join((_a(prefix, s['href'], s['label']) if s.get('href') else e(s['label'])) for s in x['sources'][:2]) or 'non précisée') for x in items)
        or '<li class="fc-empty">Aucun fait validé et daté : validez les faits proposés ci-dessous (date confirmée) pour constituer le rappel des faits.</li>')
    return '<section class="ax-card fc-rubrique" id="rubrique-rappel"><h2>Rappel des faits</h2>%s</section>' % body
