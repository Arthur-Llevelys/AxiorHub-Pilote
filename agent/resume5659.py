"""5.6.59 (audit 5.6.55, A02) : un résumé de dossier directement exploitable, en tête de la page Dossier.

En ouvrant le dossier, l'avocat retrouve en une carte :

- **qui représente qui** : parties, client du cabinet, qualité dans la procédure de référence, conseil ;
- **l'objectif du client** (décisions du cabinet de type « objectif ») ;
- **la situation** : procédure de référence (juridiction, RG, stade) et derniers événements datés ;
- **les prochaines dates confirmées** : échéances confirmées ou corrigées par l'avocat et rendez-vous de l'agenda ; les dates seulement
  proposées sont comptées à part, jamais présentées comme confirmées ;
- **les décisions du cabinet** (consignes, arguments retenus ou abandonnés, choix) ;
- **les changements récents**, dont les **bilans de pièce** ;
- **les pièces et informations à rechercher** : lacunes ouvertes et pièces demandées au client non encore rapprochées.

Chaque élément renvoie à sa source (pièce et page, agenda, décision et son auteur). Le résumé est **calculé** à partir de ces données,
sans IA : il ne peut donc ni inventer ni reformuler. Il est versionné par une empreinte de ses entrées (``d59_resumes``) : la version ne
change que si une donnée utilisée change. La couverture (« 18 documents lus sur 20 identifiés ») est une mesure technique, pas un
score de vérité.

**Bilan de changement** : quand une pièce nouvellement lue apporte quelque chose (faits, événements, parties, échéances, décisions de
justice), un événement « Bilan de la pièce » est écrit dans le journal du dossier (carte « Depuis votre dernière lecture »), avec la
liste des apports et leur source. Une pièce dont le texte n'a pas changé (simple mise en forme, renommage, réindexation) ne crée aucun
bilan ; à la première actualisation, les pièces déjà présentes servent de point de départ et ne produisent pas de bilan.
"""
from datetime import date, datetime, timezone
import hashlib
from html import escape as e
import json
from pathlib import PurePosixPath

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d59_resumes(matter TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL, content TEXT NOT NULL,
  created TEXT NOT NULL, PRIMARY KEY(matter, version));
CREATE TABLE IF NOT EXISTS d59_seen(matter TEXT NOT NULL, text_sha TEXT NOT NULL, path TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(matter, text_sha));
'''
LIMIT = 6


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _src(value):
    """Une source au format des liens de la page Dossier : chemin (et page) d'une pièce, ou libellé."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in ('path', 'page', 'extrait', 'label') and v}
    value = str(value or '')
    if not value:
        return {}
    return {'path': value} if ('/' in value or PurePosixPath(value).suffix) else {'label': value}


def _sources(items, limit=3):
    out, seen = [], set()
    for s in items or []:
        s = _src(s)
        key = json.dumps(s, sort_keys=True)
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _rows(desk, sql, params=()):
    try:
        return [dict(r) for r in desk.db.execute(sql, params)]
    except Exception:   # noqa: BLE001 — table absente (module jamais utilisé)
        return []


# ------------------------------------------------------------------------------------------------ sections
def representation(desk, mid):
    from . import modele5651
    ref = modele5651.reference_procedure(desk, mid)
    out = []
    for p in modele5651.parties(desk, mid):
        roles = sorted({r['role'] for r in p['roles'] if r['role'] and (not ref or r['procedure_id'] == ref['id'])})
        counsel = p['counsel'] or next((r['counsel'] for r in p['roles'] if r.get('counsel')), '')
        out.append({'id': p['id'], 'nom': p['display_name'], 'client': bool(p['client']), 'qualite': ', '.join(roles), 'conseil': counsel,
                    'version': p['version'], 'sources': _sources([a['source'] for a in p['aliases']])})
    out.sort(key=lambda x: (not x['client'], x['nom']))
    return out


def decisions(desk, mid):
    from . import memoire5651
    rows = memoire5651.decisions(desk, mid)
    pick = lambda d: {'id': d['id'], 'type': d['kind'], 'libelle': memoire5651.DECISION_KINDS.get(d['kind'], d['kind']), 'texte': d['text'],
                      'version': d['version'], 'sources': [{'label': 'Décision du cabinet — %s, %s' % (d['author'], d['created'][:10])}]}
    return ([pick(d) for d in rows if d['kind'] == 'objectif'], [pick(d) for d in rows if d['kind'] != 'objectif'])


def situation(desk, mid):
    from . import modele5651
    ref = modele5651.reference_procedure(desk, mid)
    proc = None
    if ref:
        proc = {'id': ref['id'], 'juridiction': ref['juridiction'], 'chambre': ref.get('chambre', ''), 'rg': ref['rg'], 'stade': ref['stade'],
                'nature': ref.get('nature', ''), 'version': ref.get('version', 1), 'sources': _sources(json.loads(ref['sources']) if isinstance(ref.get('sources'), str) else ref.get('sources'))}
    from .vue5651 import timeline   # même service que la chronologie de la page Dossier (vie du litige et procédure)
    events = []
    for it in timeline(desk, mid, 'tout'):
        if not it.get('date') or it.get('nature') == 'Échéance' or it.get('review') == modele5651.REVIEW.get('refuse'):
            continue
        events.append({'date': str(it['date'])[:10], 'precision': it.get('precision') or 'jour', 'titre': ('%s : %s' % (it['nature'], it['text']))[:240],
                       'sources': _sources(it.get('sources'))})
    today = date.today().isoformat()
    past = sorted((x for x in events if x['date'] <= today), key=lambda x: x['date'], reverse=True)[:3]
    return {'procedure': proc, 'derniers_evenements': past}


def dates(desk, mid):
    today = date.today().isoformat()
    confirmed, proposed = [], 0
    for r in _rows(desk, "SELECT id,due,rule_id,status,source_path,source_excerpt,note,updated FROM deadlines450 WHERE matter=? AND due<>'' AND due>=? "
                         "AND status IN ('a_confirmer','confirmee','manuel') ORDER BY due", (mid, today)):
        if r['status'] == 'a_confirmer':
            proposed += 1
            continue
        label = r['note'] or r['rule_id'].replace('_', ' ')
        src = [{'path': r['source_path'], 'extrait': r['source_excerpt'][:300]}] if r['source_path'] else [{'label': 'Échéance confirmée par l’avocat'}]
        confirmed.append({'id': 'echeance:' + r['id'], 'date': r['due'][:10], 'titre': 'Échéance — ' + label[:200], 'nature': 'echeance',
                          'version': r['updated'], 'sources': _sources(src)})
    now = datetime.now(timezone.utc).isoformat()
    for r in _rows(desk, 'SELECT id,title,starts FROM calendar_cache WHERE matter=? AND starts>=? ORDER BY starts LIMIT 10', (str(mid), now)):
        try:   # heure locale du serveur du cabinet ; un événement « journée entière » n'a pas d'heure
            local = datetime.fromisoformat(str(r['starts'])).astimezone()
            day, hour = local.date().isoformat(), ('' if (local.hour, local.minute) == (0, 0) else local.strftime('%H:%M'))
        except ValueError:
            day, hour = str(r['starts'])[:10], ''
        confirmed.append({'id': 'agenda:' + str(r['id']), 'date': day, 'heure': hour, 'titre': r['title'][:200],
                          'nature': 'agenda', 'version': str(r['starts']), 'sources': [{'label': 'Agenda du cabinet'}]})
    confirmed.sort(key=lambda x: (x['date'], x.get('heure', '')))
    return confirmed[:LIMIT], proposed


def recent_changes(desk, mid):
    from . import memoire5651
    rows = memoire5651.changes(desk, mid, technical=False, limit=200)
    rows = sorted(rows, key=lambda c: c['id'], reverse=True)[:LIMIT]
    out = []
    for c in rows:
        detail = c.get('detail') or {}
        out.append({'id': c['id'], 'type': c['kind'], 'libelle': memoire5651.CHANGE_KINDS.get(c['kind'], c['kind']), 'titre': c['title'][:240],
                    'date': c['created'][:10], 'apports': (detail.get('apports') or [])[:8],
                    'sources': _sources([{'path': c['object_id']}] if c['object_kind'] == 'document' else
                                        ([{'label': detail['source']}] if detail.get('source') else []))})
    return out


def to_find(desk, mid):
    from . import memoire5651
    out = []
    for g in memoire5651.gaps(desk, mid, 'ouverte'):
        out.append({'id': 'lacune:' + g['id'], 'titre': g['expected'][:240], 'raison': (g.get('reason') or '')[:240], 'blocage': g.get('blocking', ''),
                    'nature': 'lacune', 'sources': [{'label': 'Lacune relevée %s' % str(g.get('created', ''))[:10]}]})
    for r in _rows(desk, "SELECT i.request,i.item,i.label,i.reason,i.state,r.created FROM p57_request_items i JOIN p57_requests r ON r.id=i.request "
                         "WHERE r.matter=? AND i.state IN ('demandee','recue') ORDER BY r.created", (mid,)):
        out.append({'id': 'demande:%s:%s' % (r['request'], r['item']), 'titre': r['label'][:240], 'raison': (r['reason'] or '')[:240],
                    'nature': 'demandee' if r['state'] == 'demandee' else 'recue',
                    'sources': [{'label': 'Demande au client du %s' % str(r['created'])[:10]}]})
    return out


def coverage(desk, mid):
    from .vue5651 import coverage as cov
    c = cov(desk, mid)
    lus = c['lisibles'] + c['partiels'] - c['a_analyser']
    return {'identifies': c['inventories'], 'lus': max(lus, 0), 'en_erreur': c['erreurs'], 'a_analyser': c['a_analyser']}


def build(desk, mid):
    objectif, autres = decisions(desk, mid)
    prochaines, a_confirmer = dates(desk, mid)
    content = {'representation': representation(desk, mid), 'objectif': objectif, 'situation': situation(desk, mid),
               'dates': prochaines, 'dates_a_confirmer': a_confirmer, 'decisions': autres, 'changements': recent_changes(desk, mid),
               'a_rechercher': to_find(desk, mid), 'couverture': coverage(desk, mid)}
    return content


def fingerprint(content):
    """Empreinte des entrées réellement utilisées (identifiants, versions, textes) ; la couverture technique n'en fait pas partie."""
    used = {k: v for k, v in content.items() if k != 'couverture'}
    return hashlib.sha256(json.dumps(used, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def current(desk, mid):
    """Résumé courant et sa version : une nouvelle version n'est enregistrée que si l'empreinte change."""
    ensure_schema(desk)
    content = build(desk, mid)
    fp = fingerprint(content)
    row = desk.db.execute('SELECT version,fingerprint,created FROM d59_resumes WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    if row and row['fingerprint'] == fp:
        return {'version': row['version'], 'fingerprint': fp, 'since': row['created'], 'content': content}
    version = (row['version'] + 1) if row else 1
    desk.db.execute('INSERT INTO d59_resumes(matter,version,fingerprint,content,created) VALUES(?,?,?,?,?)',
                    (mid, version, fp, json.dumps(content, ensure_ascii=False, default=str), _now()))
    desk.db.execute('DELETE FROM d59_resumes WHERE matter=? AND version<=?', (mid, version - 20))
    desk.db.commit()
    return {'version': version, 'fingerprint': fp, 'since': _now(), 'content': content}


# ------------------------------------------------------------------------------------------------ bilan de changement
def _contributions(desk, mid, path):
    like = '%' + json.dumps(path, ensure_ascii=False)[1:-1].replace('%', '\\%').replace('_', '\\_') + '%'
    out = []
    for r in _rows(desk, "SELECT kind,text,review FROM d51_assertions WHERE matter=? AND sources LIKE ? ESCAPE '\\' AND validity='courante' ORDER BY created", (mid, like)):
        if r['review'] == 'refuse':
            continue
        out.append({'type': {'demande': 'Demande', 'montant': 'Montant', 'argument': 'Argument'}.get(r['kind'], 'Fait'), 'texte': r['text'][:200]})
    for r in _rows(desk, "SELECT title,dates FROM d51_events WHERE matter=? AND sources LIKE ? ESCAPE '\\' ORDER BY created", (mid, like)):
        when = next((v for v in json.loads(r['dates'] or '{}').values() if v), '')
        out.append({'type': 'Événement', 'texte': ('%s — %s' % (str(when)[:10], r['title']) if when else r['title'])[:200]})
    for r in _rows(desk, 'SELECT DISTINCT p.display_name FROM d51_aliases a JOIN d51_parties p ON p.id=a.party_id WHERE a.matter=? AND a.source=?', (mid, path)):
        out.append({'type': 'Partie mentionnée', 'texte': r['display_name'][:200]})
    for r in _rows(desk, "SELECT due,rule_id,status FROM deadlines450 WHERE matter=? AND source_path=?", (mid, path)):
        out.append({'type': 'Échéance %s' % ('à confirmer' if r['status'] == 'a_confirmer' else 'enregistrée'), 'texte': '%s — %s' % (r['due'][:10], r['rule_id'])})
    for r in _rows(desk, 'SELECT juridiction,date,nature FROM d51_judgments WHERE matter=? AND path=?', (mid, path)):
        out.append({'type': 'Décision de justice', 'texte': ' '.join(x for x in (r['nature'], r['juridiction'], r['date']) if x)[:200]})
    return out


def after_rebuild(desk, mid):
    """Appelé après chaque actualisation des données du dossier : un bilan par pièce nouvellement lue dont le texte est nouveau."""
    from . import memoire5651
    ensure_schema(desk)
    docs = _rows(desk, "SELECT path,name,text_sha,reading FROM d51_documents WHERE matter=? AND text_sha<>'' AND reading IN ('lisible','partielle')", (mid,))
    digested = {r['path'] for r in _rows(desk, 'SELECT DISTINCT path FROM piece_digests5634 WHERE matter=?', (mid,))}
    seen = {r['text_sha'] for r in _rows(desk, 'SELECT text_sha FROM d59_seen WHERE matter=?', (mid,))}
    first = not seen
    made = 0
    for d in docs:
        if d['text_sha'] in seen or d['path'] not in digested:
            continue                                            # déjà vue (même texte), ou pas encore lue : bilan plus tard
        desk.db.execute('INSERT OR IGNORE INTO d59_seen(matter,text_sha,path,at) VALUES(?,?,?,?)', (mid, d['text_sha'], d['path'], _now()))
        seen.add(d['text_sha'])
        if first:
            continue                                            # point de départ : aucune avalanche de bilans à la première actualisation
        apports = _contributions(desk, mid, d['path'])
        title = 'Bilan de la pièce « %s » : %s' % (d['name'], ('%d apport(s)' % len(apports)) if apports else 'aucun apport nouveau relevé')
        memoire5651.record_change(desk, mid, 'bilan_piece', 'document', d['path'], d['text_sha'][:16], title,
                                  {'apports': apports[:20]}, commit=False)   # le titre nomme déjà la pièce
        made += 1
    desk.db.commit()
    return made


# ------------------------------------------------------------------------------------------------ affichage
def _links(prefix, mid, sources):
    if not sources:   # journal du dossier (décision, correction) : l'élément est lui-même la source, rien à lier
        return ''
    from .vue5651 import _source_links
    return _source_links(prefix, mid, sources, 2)


def _date_fr(value):
    from .vue5651 import _date_fr as fr
    return fr(value) if value else ''


def html(desk, prefix, m):
    mid = m['id']
    try:
        res = current(desk, mid)
    except Exception:   # noqa: BLE001 — une donnée illisible ne doit jamais masquer le reste de la page
        return ''
    c = res['content']
    from .vue5651 import _q
    blocks = []
    reps = c['representation']
    if reps:
        rows = ''.join('<li%s><strong>%s</strong>%s%s%s %s</li>' % (
            ' class="r59-client"' if p['client'] else '', e(p['nom']), ' <span class="d27-badge">client</span>' if p['client'] else '',
            (' — ' + e(p['qualite'])) if p['qualite'] else ' — <span class="ax-muted">qualité à préciser</span>',
            (' · conseil : ' + e(p['conseil'])) if p['conseil'] else '', _links(prefix, mid, p['sources'])) for p in reps[:8])
        more = ('<li class="ax-muted">… et %d autre(s) : <a href="%s">voir les parties</a></li>' % (len(reps) - 8, e(prefix + _q(mid) + '#d51-parties', quote=True))
                if len(reps) > 8 else '')
        blocks.append(('Qui représente qui', '<ul class="r59-list">%s%s</ul>' % (rows, more)))
    else:
        blocks.append(('Qui représente qui', '<p class="ax-muted">Parties non encore identifiées.</p>'))
    if c['objectif']:
        blocks.append(('Objectif du client', '<ul class="r59-list">%s</ul>' % ''.join(
            '<li>%s %s</li>' % (e(d['texte']), _links(prefix, mid, d['sources'])) for d in c['objectif'])))
    else:
        blocks.append(('Objectif du client', '<p class="ax-muted">Non renseigné. <a href="%s">Ajouter l’objectif</a></p>' % e(prefix + _q(mid) + '#d51-decisions', quote=True)))
    st = c['situation']
    proc = st['procedure']
    sit = ''
    if proc:
        sit += '<p>%s%s%s %s</p>' % (e(proc['juridiction'] or 'Juridiction à préciser'), (' · RG ' + e(proc['rg'])) if proc['rg'] else '',
                                    (' · ' + e(proc['stade'])) if proc['stade'] else '', _links(prefix, mid, proc['sources']))
    sit += ''.join('<li>%s — %s %s</li>' % (e(_date_fr(ev['date'])), e(ev['titre']), _links(prefix, mid, ev['sources'])) for ev in st['derniers_evenements'])
    blocks.append(('Situation', (sit.replace('<li>', '<ul class="r59-list"><li>', 1) + '</ul>') if st['derniers_evenements'] else (sit or '<p class="ax-muted">Aucune procédure ni événement daté identifié.</p>')))
    dl = ''.join('<li><strong>%s%s</strong> — %s %s</li>' % (e(_date_fr(x['date'])), (' ' + e(x['heure'])) if x.get('heure') else '', e(x['titre']),
                                                          _links(prefix, mid, x['sources'])) for x in c['dates'])
    pending = ('<p class="ax-muted">%d date(s) proposée(s) à confirmer : <a href="%s">Échéances</a>. Elles ne sont pas reprises ici tant qu’elles ne sont pas confirmées.</p>'
               % (c['dates_a_confirmer'], e(prefix + '/echeances', quote=True))) if c['dates_a_confirmer'] else ''
    blocks.append(('Prochaines dates confirmées', ('<ul class="r59-list">%s</ul>' % dl if dl else '<p class="ax-muted">Aucune date confirmée à venir.</p>') + pending))
    if c['decisions']:
        blocks.append(('Décisions du cabinet', '<ul class="r59-list">%s</ul>' % ''.join(
            '<li><span class="d51-kind">%s</span> %s</li>' % (e(d['libelle']), e(d['texte'][:300])) for d in c['decisions'][:LIMIT])))
    if c['changements']:
        rows = []
        for ch in c['changements']:
            extra = ''
            if ch['apports']:
                extra = '<ul class="r59-apports">%s</ul>' % ''.join('<li>%s : %s</li>' % (e(a['type']), e(a['texte'])) for a in ch['apports'])
            rows.append('<li>%s — %s %s%s</li>' % (e(_date_fr(ch['date'])), e(ch['titre']), _links(prefix, mid, ch['sources']), extra))
        blocks.append(('Changements récents', '<ul class="r59-list">%s</ul>' % ''.join(rows)))
    if c['a_rechercher']:
        labels = {'lacune': 'À obtenir', 'demandee': 'Demandée au client', 'recue': 'Reçue — à rapprocher'}
        blocks.append(('Pièces et informations à rechercher', '<ul class="r59-list">%s</ul>' % ''.join(
            '<li><span class="d51-kind">%s</span> %s%s</li>' % (e(labels.get(x['nature'], '')), e(x['titre']), (' <span class="ax-muted">(%s)</span>' % e(x['raison'])) if x['raison'] else '')
            for x in c['a_rechercher'][:8])))
    cov = c['couverture']
    foot = ('<p class="r59-foot ax-muted">%d document(s) lu(s) sur %d identifié(s)%s · résumé calculé à partir des pièces, de l’agenda et des '
            'décisions du cabinet, sans rédaction par l’IA · version %d</p>' % (
                cov['lus'], cov['identifies'], (' · %d en erreur' % cov['en_erreur']) if cov['en_erreur'] else '', res['version']))
    body = '<div class="r59-grid">%s</div>' % ''.join('<div class="r59-block"><h4>%s</h4>%s</div>' % (e(t), b) for t, b in blocks)
    return '<section class="d51-card r59" id="r59-resume" aria-label="Le dossier en bref"><h3>Le dossier en bref</h3>%s%s</section>' % (body, foot)
