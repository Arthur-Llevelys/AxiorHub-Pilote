"""5.6.36 : Outils IA — grilles d'analyse, playbooks de négociation, mémos juridiques.

Grilles d'analyse : les mêmes critères (ex. « date de signature », « clause de non-concurrence », « durée du préavis ») relevés dans
chaque pièce d'un dossier et présentés en tableau (pièces × critères). Chaque valeur cite l'extrait de la pièce qui la fonde ; une
valeur dont l'extrait n'est pas retrouvé dans le texte est marquée « à vérifier ». Export tableur.

Playbooks : les positions de négociation du cabinet (clause, position souhaitée, repli acceptable, inacceptable) appliquées à un
contrat Word. Le résultat est une COPIE du contrat avec modifications suivies (révisions Word, auteur « AxiorHub ») et une note qui
justifie chaque écart ; l'original n'est jamais modifié. Le complément Word propose les mêmes écarts dans le document ouvert.

Mémos juridiques : réponses de recherche sourcées, relues puis validées par un avocat, réutilisables dans tout le cabinet (recherche
par mot, thème, réutilisation dans un document). Un mémo rédigé par l'IA reste « à relire » tant qu'un avocat ne l'a pas validé.
"""
import csv
from datetime import datetime, timezone
from html import escape as e
import io
import json
import re
import uuid

from .common import Stop, fold, load_matters, matter_display

MAX_CRITERIA = 20
MAX_POSITIONS = 40


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS grids5636(id TEXT PRIMARY KEY, name TEXT NOT NULL, matter TEXT NOT NULL, criteria TEXT NOT NULL, state TEXT NOT NULL,
      job INTEGER, owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS grid_cells5636(grid TEXT NOT NULL, path TEXT NOT NULL, criterion INTEGER NOT NULL, value TEXT NOT NULL, excerpt TEXT NOT NULL,
      statut TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(grid, path, criterion));
    CREATE TABLE IF NOT EXISTS playbooks5636(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '', positions TEXT NOT NULL,
      owner TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_runs5636(id TEXT PRIMARY KEY, playbook TEXT NOT NULL, matter TEXT NOT NULL, source TEXT NOT NULL, state TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT '{}', job INTEGER, owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS memos5636(id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL, body TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
      tags TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', validated_by TEXT NOT NULL DEFAULT '',
      job INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);''')


def _model(desk, matter=''):
    from .docrequest520 import _model as model
    return model(desk, matter)


def _json(raw):
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _labels(desk):
    try:
        return {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        return {}


def _check_matter(desk, mid):
    if mid not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')


# ------------------------------------------------------------------------------------------------ grilles d'analyse
def grid_create(desk, data, owner='cabinet'):
    schema(desk)
    mid = str(data.get('matter') or '')
    _check_matter(desk, mid)
    criteria = [re.sub(r'\s+', ' ', c).strip()[:200] for c in str(data.get('criteria') or '').splitlines() if c.strip()][:MAX_CRITERIA]
    if not criteria:
        raise Stop('grille_criteres_requis')
    name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()[:120] or 'Grille du ' + datetime.now().strftime('%d/%m/%Y')
    gid = uuid.uuid4().hex
    desk.db.execute('INSERT INTO grids5636 VALUES(?,?,?,?,?,?,?,?,?)', (gid, name, mid, json.dumps(criteria, ensure_ascii=False), 'en_file', None, owner,
                                                                     _now(), _now()))
    desk.db.commit()
    job = desk.enqueue('outils5636', {'tool': 'grille', 'id': gid, 'matter': mid}, priority=45)
    desk.db.execute('UPDATE grids5636 SET job=? WHERE id=?', (job, gid))
    desk.db.commit()
    desk.audit('outils5636_grille', {'id': gid, 'matter': mid, 'criteria': len(criteria), 'owner': owner})
    return {'id': gid, 'job': job, 'message': 'Grille créée : les critères sont relevés pièce par pièce (quelques minutes).'}


def _grid(desk, gid):
    schema(desk)
    row = desk.db.execute('SELECT * FROM grids5636 WHERE id=?', (gid,)).fetchone()
    if not row:
        raise Stop('grille_absente')
    out = dict(row)
    out['criteria'] = json.loads(row['criteria'])
    return out


def _proof(value, excerpt, text):
    """« documenté » si l'extrait cité figure dans la pièce (comparaison sans accents ni espaces multiples), sinon « à vérifier »."""
    ex = re.sub(r'\s+', ' ', fold(excerpt or '')).strip()
    return 'documente' if value and len(ex) >= 8 and ex in re.sub(r'\s+', ' ', fold(text or '')) else 'a_verifier'


def grid_run(desk, gid, model=None):
    from .synthese5632 import inventory
    g = _grid(desk, gid)
    desk.db.execute("UPDATE grids5636 SET state='en_cours', updated=? WHERE id=?", (_now(), gid))
    desk.db.commit()
    docs = [d for d in inventory(desk, g['matter']) if d['state'] == 'extrait']
    model = model or _model(desk, g['matter'])
    schema_out = {'type': 'object', 'additionalProperties': False, 'required': ['valeurs'],
                  'properties': {'valeurs': {'type': 'array', 'maxItems': MAX_CRITERIA, 'items': {
                      'type': 'object', 'additionalProperties': False, 'required': ['critere', 'valeur', 'extrait'],
                      'properties': {'critere': {'type': 'integer', 'minimum': 0}, 'valeur': {'type': 'string', 'maxLength': 600},
                                     'extrait': {'type': 'string', 'maxLength': 600}}}}}}
    done = 0
    for d in docs:
        try:
            raw = model.complete([
                {'role': 'system', 'content': 'Tu relèves des critères dans une pièce d’un dossier d’avocat. Pour chaque critère (numéroté), donne la '
                                              'valeur trouvée et l’extrait EXACT de la pièce qui la fonde. Si la pièce ne dit rien, valeur "" et extrait "". '
                                              'N’invente rien. JSON conforme au schéma.'},
                {'role': 'user', 'content': json.dumps({'criteres': {i: c for i, c in enumerate(g['criteria'])}, 'piece': d['path'].rsplit('/', 1)[-1],
                                                        'texte': d['text'][:20000]}, ensure_ascii=False)}],
                temperature=0, max_tokens=2500, json_schema=schema_out)
            values = _json(raw).get('valeurs') or []
        except (Stop, ValueError, TypeError, KeyError) as exc:
            values = []
            desk.db.execute('INSERT OR REPLACE INTO grid_cells5636 VALUES(?,?,?,?,?,?,?)', (gid, d['path'], -1, '', str(exc)[:200], 'erreur', _now()))
        for v in values:
            i = v.get('critere')
            if not isinstance(i, int) or not 0 <= i < len(g['criteria']):
                continue
            value, excerpt = str(v.get('valeur') or '').strip(), str(v.get('extrait') or '').strip()
            desk.db.execute('INSERT OR REPLACE INTO grid_cells5636 VALUES(?,?,?,?,?,?,?)',
                            (gid, d['path'], i, value, excerpt, _proof(value, excerpt, d['text']) if value else 'absent', _now()))
        desk.db.commit()
        done += 1
    desk.db.execute("UPDATE grids5636 SET state='prete', updated=? WHERE id=?", (_now(), gid))
    desk.db.commit()
    return {'id': gid, 'pieces': done}


def grid_table(desk, gid):
    g = _grid(desk, gid)
    cells = {}
    for r in desk.db.execute('SELECT * FROM grid_cells5636 WHERE grid=? AND criterion>=0', (gid,)):
        cells.setdefault(r['path'], {})[r['criterion']] = dict(r)
    return g, cells


def grid_csv(desk, gid):
    g, cells = grid_table(desk, gid)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['Pièce'] + g['criteria'])
    for path in sorted(cells):
        w.writerow([path.rsplit('/', 1)[-1]] + [(cells[path].get(i) or {}).get('value', '') + (' [à vérifier]' if (cells[path].get(i) or {}).get('statut') == 'a_verifier' else '')
                                               for i in range(len(g['criteria']))])
    return {'download': {'filename': 'grille-%s.csv' % re.sub(r'[^A-Za-z0-9_-]+', '-', g['name'])[:60], 'mime': 'text/csv', 'text': '﻿' + buf.getvalue()}}


# ------------------------------------------------------------------------------------------------ playbooks
def _positions(text):
    """Une position par bloc séparé d'une ligne vide : « Clause : … / Position : … / Repli : … / Inacceptable : … »."""
    out = []
    for block in re.split(r'\n\s*\n', str(text or '').strip()):
        item = {'clause': '', 'position': '', 'repli': '', 'inacceptable': ''}
        for line in block.splitlines():
            key, _, value = line.partition(':')
            k = fold(key).strip()
            target = {'clause': 'clause', 'position': 'position', 'repli': 'repli', 'inacceptable': 'inacceptable'}.get(k)
            if target and value.strip():
                item[target] = value.strip()[:800]
        if item['clause'] and item['position']:
            out.append(item)
    return out[:MAX_POSITIONS]


def positions_text(positions):
    return '\n\n'.join('\n'.join('%s : %s' % (lab, p[k]) for k, lab in (('clause', 'Clause'), ('position', 'Position'), ('repli', 'Repli'),
                                                                       ('inacceptable', 'Inacceptable')) if p.get(k)) for p in positions)


def playbook_save(desk, data, owner='cabinet'):
    schema(desk)
    positions = _positions(data.get('positions'))
    if not positions:
        raise Stop('playbook_positions_requises')
    name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()[:120]
    if not name:
        raise Stop('playbook_nom_requis')
    pid = str(data.get('id') or '')
    if pid:
        if not desk.db.execute('SELECT 1 FROM playbooks5636 WHERE id=?', (pid,)).fetchone():
            raise Stop('playbook_absent')
        desk.db.execute('UPDATE playbooks5636 SET name=?, kind=?, positions=?, updated=? WHERE id=?',
                        (name, str(data.get('kind') or '')[:80], json.dumps(positions, ensure_ascii=False), _now(), pid))
    else:
        pid = uuid.uuid4().hex
        desk.db.execute('INSERT INTO playbooks5636 VALUES(?,?,?,?,?,?,?,?)', (pid, name, str(data.get('kind') or '')[:80],
                                                                           json.dumps(positions, ensure_ascii=False), owner, 0, _now(), _now()))
    desk.db.commit()
    desk.audit('outils5636_playbook', {'id': pid, 'positions': len(positions), 'owner': owner})
    return {'id': pid, 'positions': len(positions), 'message': 'Playbook enregistré (%d position(s)).' % len(positions)}


def playbook(desk, pid):
    schema(desk)
    row = desk.db.execute('SELECT * FROM playbooks5636 WHERE id=? AND archived=0', (pid,)).fetchone()
    if not row:
        raise Stop('playbook_absent')
    out = dict(row)
    out['positions'] = json.loads(row['positions'])
    return out


EDITS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['ecarts'],
                'properties': {'ecarts': {'type': 'array', 'maxItems': 40, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['paragraphe', 'operation', 'texte', 'position', 'justification'],
                    'properties': {'paragraphe': {'type': 'integer', 'minimum': 0}, 'operation': {'type': 'string', 'enum': ['replace', 'insert_after', 'delete']},
                                   'texte': {'type': 'string', 'maxLength': 4000}, 'position': {'type': 'integer', 'minimum': 0},
                                   'justification': {'type': 'string', 'maxLength': 800}}}}}}


def propose_edits(desk, pb, paragraphs, matter='', model=None):
    """Écarts entre le contrat et les positions du playbook : liste d'éditions contrôlées (paragraphe existant, texte non vide)."""
    texts = [str(p or '') for p in paragraphs][:1500]
    numbered = [{'n': i, 'texte': t[:1500]} for i, t in enumerate(texts) if t.strip()]
    model = model or _model(desk, matter)
    raw = model.complete([
        {'role': 'system', 'content': 'Tu es avocat en droit des contrats. Compare le contrat (paragraphes numérotés) aux positions de négociation du '
                                      'cabinet (numérotées). Pour chaque clause qui s’écarte de la position, propose une modification minimale : '
                                      'remplacement du paragraphe (replace), ajout après un paragraphe (insert_after) ou suppression (delete). Respecte '
                                      'la position, à défaut le repli ; jamais l’inacceptable. Justification courte citant la position. Ne modifie pas '
                                      'ce qui est conforme. JSON conforme au schéma.'},
        {'role': 'user', 'content': json.dumps({'positions': {i: p for i, p in enumerate(pb['positions'])}, 'contrat': numbered}, ensure_ascii=False)[:60000]}],
        temperature=0, max_tokens=6000, json_schema=EDITS_SCHEMA)
    edits, seen = [], set()
    for x in _json(raw).get('ecarts') or []:
        n, op, text = x.get('paragraphe'), x.get('operation'), str(x.get('texte') or '').strip()
        if not isinstance(n, int) or not 0 <= n < len(texts) or not texts[n].strip() or (n, op) in seen:
            continue
        if op != 'delete' and not text:
            continue
        unique = sum(1 for t in texts if re.sub(r'\s+', ' ', t).strip() == re.sub(r'\s+', ' ', texts[n]).strip()) == 1
        seen.add((n, op))
        pos = x.get('position') if isinstance(x.get('position'), int) and 0 <= x.get('position') < len(pb['positions']) else None
        edits.append({'paragraph': n, 'operation': op, 'text': text, 'original': texts[n], 'unique': unique,
                      'clause': pb['positions'][pos]['clause'] if pos is not None else '', 'justification': str(x.get('justification') or '')[:800]})
    return edits


def apply_docx(raw, edits):
    """Copie du contrat avec modifications suivies ; seuls les paragraphes identifiables sans ambiguïté sont modifiés."""
    from .word_legal import render_docx
    plan = {'edits': [{'anchor_text': x['original'], 'operation': x['operation'], 'text': x['text'] or '', 'paragraph_id': 'PB%d_%s' % (x['paragraph'], x['operation'][:3]),
                       'style_id': '', 'source_ids': []} for x in edits if x['unique']]}
    return render_docx(raw, plan, tracked=True)


def playbook_start(desk, data, owner='cabinet'):
    schema(desk)
    pb = playbook(desk, str(data.get('playbook') or ''))
    mid = str(data.get('matter') or '')
    _check_matter(desk, mid)
    path = str(data.get('path') or '').strip()
    matter = next(m for m in load_matters(desk.c) if m['id'] == mid)
    from .common import clean_path, under
    path = clean_path(path) if path else ''
    if not path or not path.lower().endswith('.docx') or not under(path, matter['path']):
        raise Stop('contrat_docx_requis')
    rid = uuid.uuid4().hex
    desk.db.execute('INSERT INTO playbook_runs5636 VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, pb['id'], mid, path, 'en_file', '{}', None, owner, _now(), _now()))
    desk.db.commit()
    job = desk.enqueue('outils5636', {'tool': 'playbook', 'id': rid, 'matter': mid}, priority=40)
    desk.db.execute('UPDATE playbook_runs5636 SET job=? WHERE id=?', (job, rid))
    desk.db.commit()
    return {'id': rid, 'job': job, 'message': 'Application du playbook lancée : la copie annotée apparaîtra à côté du contrat.'}


def playbook_run(desk, rid, dav=None, model=None):
    from .dav import DAV
    from .word_legal import _simple_docx
    row = desk.db.execute('SELECT * FROM playbook_runs5636 WHERE id=?', (rid,)).fetchone()
    if not row:
        raise Stop('playbook_absent')
    pb = playbook(desk, row['playbook'])
    client = dav or DAV(desk.c['nextcloud'])
    item = client.stat(row['source'])
    raw = client.download(item)
    from .word_legal import inspect_docx
    paragraphs = [p['text'] for p in inspect_docx(raw)['paragraphs']]
    edits = propose_edits(desk, pb, paragraphs, row['matter'], model)
    folder, name = row['source'].rsplit('/', 1)
    stem = name[:-5]
    day = datetime.now().strftime('%Y-%m-%d')
    outputs = []
    applicable = [x for x in edits if x['unique']]
    if applicable:
        target = '%s/%s - playbook %s - %s.docx' % (folder, stem[:80], re.sub(r'[\\/:*?"<>|]', '_', pb['name'])[:40], day)
        client.put_file(target, apply_docx(raw, applicable), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        outputs.append(target)
    note_rows = ['Contrat : %s' % name, 'Playbook : %s' % pb['name'], 'Écarts relevés : %d (dont %d appliqués en modifications suivies)' % (len(edits), len(applicable)), '']
    for x in edits:
        note_rows += ['§ %d — %s%s' % (x['paragraph'] + 1, x['clause'] or 'clause', '' if x['unique'] else ' (paragraphe répété : à reprendre à la main)'),
                      'Texte actuel : ' + x['original'][:600], ('Proposition (%s) : ' % {'replace': 'remplacement', 'insert_after': 'ajout', 'delete': 'suppression'}[x['operation']])
                      + (x['text'][:1500] or '—'), 'Justification : ' + x['justification'], '']
    note_rows.append('Note de travail produite par AxiorHub : à relire par l’avocat ; aucune modification n’a été faite dans le contrat d’origine.')
    note = '%s/%s - note playbook - %s.docx' % (folder, stem[:80], day)
    try:
        client.put_file(note, _simple_docx('Note d’application du playbook', note_rows), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        outputs.append(note)
    except Stop as exc:
        if str(exc) != 'http_412':
            raise
    result = {'edits': len(edits), 'applied': len(applicable), 'files': outputs}
    desk.db.execute("UPDATE playbook_runs5636 SET state='termine', result=?, updated=? WHERE id=?", (json.dumps(result, ensure_ascii=False), _now(), rid))
    desk.db.commit()
    desk.audit('outils5636_playbook_applique', {'run': rid, 'edits': len(edits), 'applied': len(applicable)})
    return result


# ------------------------------------------------------------------------------------------------ mémos juridiques
def memo_save(desk, data, owner='cabinet', role='avocat'):
    schema(desk)
    title = re.sub(r'\s+', ' ', str(data.get('title') or '')).strip()[:200]
    body = str(data.get('body') or '').strip()[:60000]
    if not title or not body:
        raise Stop('memo_titre_et_texte_requis')
    sources = [s.strip()[:500] for s in str(data.get('sources') or '').splitlines() if s.strip()][:60]
    tags = ', '.join(t.strip()[:40] for t in re.split(r'[,;]', str(data.get('tags') or '')) if t.strip())[:300]
    mid = str(data.get('matter') or '')
    validate = bool(data.get('valider')) and role in ('avocat', 'administrateur')
    ident = str(data.get('id') or '')
    if ident:
        row = desk.db.execute('SELECT * FROM memos5636 WHERE id=?', (ident,)).fetchone()
        if not row:
            raise Stop('memo_absent')
        desk.db.execute('UPDATE memos5636 SET title=?, body=?, sources=?, tags=?, status=?, validated_by=?, updated=? WHERE id=?',
                        (title, body, json.dumps(sources, ensure_ascii=False), tags, 'valide' if validate else row['status'],
                         owner if validate else row['validated_by'], _now(), ident))
    else:
        ident = uuid.uuid4().hex
        desk.db.execute('INSERT INTO memos5636 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, title, str(data.get('question') or title)[:2000], body, json.dumps(sources, ensure_ascii=False), tags, mid,
                         'valide' if validate else 'a_relire', owner, owner if validate else '', None, _now(), _now()))
    desk.db.commit()
    desk.audit('outils5636_memo', {'id': ident, 'validated': validate, 'owner': owner})
    return {'id': ident, 'message': 'Mémo validé et partagé avec le cabinet.' if validate else 'Mémo enregistré (à relire avant validation).'}


def memo_ask(desk, data, owner='cabinet'):
    """Mémo rédigé par l'IA à partir de la recherche juridique du cabinet (sources vérifiées) ; reste « à relire »."""
    schema(desk)
    question = re.sub(r'\s+', ' ', str(data.get('question') or '')).strip()[:2000]
    if len(question) < 10:
        raise Stop('memo_question_trop_courte')
    ident = uuid.uuid4().hex
    desk.db.execute('INSERT INTO memos5636 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, question[:200], question, '', '[]', str(data.get('tags') or '')[:300],
                                                                              str(data.get('matter') or ''), 'en_redaction', owner, '', None, _now(), _now()))
    desk.db.commit()
    job = desk.enqueue('outils5636', {'tool': 'memo', 'id': ident}, priority=40)
    desk.db.execute('UPDATE memos5636 SET job=? WHERE id=?', (job, ident))
    desk.db.commit()
    return {'id': ident, 'job': job, 'message': 'Recherche et rédaction du mémo lancées ; il apparaîtra « à relire » dans la bibliothèque.'}


def memo_run(desk, ident, model=None):
    from .web5634 import research_job
    row = desk.db.execute('SELECT * FROM memos5636 WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Stop('memo_absent')
    try:
        found = research_job(desk, {'question': row['question'], 'matter': row['matter']})
    except Stop:
        found = {'verified_authorities': [], 'leads': []}
    authorities = (found.get('verified_authorities') or [])[:12]
    leads = (found.get('leads') or [])[:12]
    sources = []
    for a in authorities + leads:
        label = ' — '.join(str(a.get(k) or '') for k in ('title', 'reference', 'date') if a.get(k))[:300]
        url = str(a.get('url') or '')
        sources.append(label + ((' ' + url) if url else '') + ('' if a in authorities else ' [piste non vérifiée]'))
    model = model or _model(desk, row['matter'])
    raw = model.complete([
        {'role': 'system', 'content': 'Tu rédiges un mémo juridique de cabinet d’avocats français : question, réponse courte, développement, limites. '
                                      'Appuie chaque affirmation sur une source fournie en la citant [n] ; si les sources ne suffisent pas, dis-le. '
                                      'N’invente aucune référence. JSON {"titre", "texte"}.'},
        {'role': 'user', 'content': json.dumps({'question': row['question'], 'sources': [{'n': i + 1, 'source': s, 'extrait': str((authorities + leads)[i].get('excerpt')
                                                or (authorities + leads)[i].get('summary') or '')[:1500]} for i, s in enumerate(sources)]}, ensure_ascii=False)}],
        temperature=0.1, max_tokens=5000,
        json_schema={'type': 'object', 'additionalProperties': False, 'required': ['titre', 'texte'],
                     'properties': {'titre': {'type': 'string', 'maxLength': 200}, 'texte': {'type': 'string', 'maxLength': 30000}}})
    out = _json(raw)
    body = str(out.get('texte') or '').strip()
    if not sources:
        body = '⚠ Aucune source n’a pu être consultée : ce mémo ne repose sur aucune référence vérifiée.\n\n' + body
    desk.db.execute("UPDATE memos5636 SET title=?, body=?, sources=?, status='a_relire', updated=? WHERE id=?",
                    (str(out.get('titre') or row['question'])[:200], body, json.dumps(sources, ensure_ascii=False), _now(), ident))
    desk.db.commit()
    return {'id': ident, 'sources': len(sources)}


def memo_search(desk, q='', tag='', status=''):
    schema(desk)
    where, params = ["status<>'archive'"], []
    if status:
        where.append('status=?')
        params.append(status)
    if tag:
        where.append('lower(tags) LIKE ?')
        params.append('%' + tag.lower() + '%')
    for w in [w for w in re.split(r'\s+', str(q or '').strip().lower()) if len(w) >= 2][:6]:
        where.append("lower(title||' '||question||' '||body||' '||tags) LIKE ?")
        params.append('%' + w.replace('%', '') + '%')
    return [dict(r, sources=json.loads(r['sources'] or '[]')) for r in desk.db.execute(
        'SELECT * FROM memos5636 WHERE ' + ' AND '.join(where) + ' ORDER BY updated DESC LIMIT 200', params)]


def memo_docx(desk, ident):
    from .word_legal import _simple_docx
    import base64
    schema(desk)
    row = desk.db.execute('SELECT * FROM memos5636 WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Stop('memo_absent')
    lines = [x for x in row['body'].split('\n')] + ['', 'Sources :'] + json.loads(row['sources'] or '[]')
    raw = _simple_docx(row['title'], lines)
    return {'download': {'filename': 'memo-%s.docx' % re.sub(r'[^A-Za-z0-9_-]+', '-', fold(row['title']))[:60],
                         'mime': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'base64': base64.b64encode(raw).decode()}}


def job(desk, args):
    tool, ident = str(args.get('tool') or ''), str(args.get('id') or '')
    if tool == 'grille':
        return grid_run(desk, ident)
    if tool == 'playbook':
        try:
            return playbook_run(desk, ident)
        except Stop as exc:
            desk.db.execute("UPDATE playbook_runs5636 SET state='echec', result=?, updated=? WHERE id=?", (json.dumps({'erreur': str(exc)}), _now(), ident))
            desk.db.commit()
            raise
    if tool == 'memo':
        try:
            return memo_run(desk, ident)
        except Stop:
            desk.db.execute("UPDATE memos5636 SET status='a_relire', body=?, updated=? WHERE id=? AND body=''",
                            ('La rédaction automatique a échoué : complétez ce mémo à la main ou relancez.', _now(), ident))
            desk.db.commit()
            raise
    raise Stop('operation_inconnue')


# ------------------------------------------------------------------------------------------------ page
TABS = (('grilles', 'Grilles d’analyse'), ('playbooks', 'Playbooks'), ('memos', 'Mémos juridiques'))
STATUS = {'en_file': 'en file', 'en_cours': 'relevé en cours', 'prete': 'prête', 'a_relire': 'à relire', 'valide': 'validé', 'en_redaction': 'en rédaction',
          'termine': 'terminé', 'echec': 'échec'}


def _matter_select(labels, current='', name='matter'):
    return '<select name="%s" required><option value="">Choisir un dossier…</option>%s</select>' % (name, ''.join(
        '<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == current else '', e(v)) for k, v in sorted(labels.items(), key=lambda kv: kv[1].casefold())))


def _grids_html(desk, prefix, args, labels):
    gid = str(args.get('grille') or '')
    html = ('<section class="ax-card"><h2>Nouvelle grille</h2><form class="m5-form" data-api="m5636/outils/grille" data-reload="1">'
            '<label>Nom<input name="name" placeholder="Ex. : Contrats de travail — clauses clés"></label><label>Dossier%s</label>'
            '<label>Critères (un par ligne)<textarea name="criteria" rows="5" data-no-dictation placeholder="Date de signature&#10;Durée du préavis&#10;Clause de non-concurrence"></textarea></label>'
            '<button class="ax-btn" type="submit">Créer et relever</button></form></section>') % _matter_select(labels, str(args.get('dossier') or ''))
    rows = desk.db.execute('SELECT * FROM grids5636 ORDER BY created DESC LIMIT 30').fetchall()
    html += '<ul class="o5636-list">%s</ul>' % (''.join('<li><a href="%s">%s</a> — %s · %s</li>' % (
        e(prefix + '/outils-ia?onglet=grilles&grille=' + r['id'], quote=True), e(r['name']), e(labels.get(r['matter'], r['matter'])), e(STATUS.get(r['state'], r['state'])))
        for r in rows) or '<li class="ax-muted">Aucune grille.</li>')
    if gid:
        g, cells = grid_table(desk, gid)
        head = ''.join('<th scope="col">%s</th>' % e(c) for c in g['criteria'])
        body = ''
        for path in sorted(cells):
            tds = ''
            for i in range(len(g['criteria'])):
                c = cells[path].get(i) or {}
                badge = {'documente': '', 'a_verifier': ' <span class="g5636-check">à vérifier</span>', 'absent': ''}.get(c.get('statut'), '')
                tds += '<td title="%s">%s%s</td>' % (e(c.get('excerpt', ''), quote=True), e(c.get('value', '')) or '—', badge)
            body += '<tr><th scope="row">%s</th>%s</tr>' % (e(path.rsplit('/', 1)[-1]), tds)
        html += ('<section class="ax-card"><h2>%s</h2><p class="ax-muted">%s · survolez une valeur pour lire l’extrait qui la fonde.</p>'
                 '<button type="button" class="ax-btn ghost m5-export" data-api="m5636/outils/grille/export?id=%s">Exporter (tableur)</button>'
                 '<div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Pièce</th>%s</tr></thead><tbody>%s</tbody></table></div></section>') % (
            e(g['name']), e(STATUS.get(g['state'], g['state'])), e(gid, quote=True), head, body or '<tr><td colspan="99" class="ax-muted">Relevé en cours…</td></tr>')
    return html


def _playbooks_html(desk, prefix, args, labels):
    pbs = desk.db.execute('SELECT * FROM playbooks5636 WHERE archived=0 ORDER BY name').fetchall()
    edit = next((dict(p) for p in pbs if p['id'] == args.get('playbook')), None)
    example = ('Clause : Limitation de responsabilité\nPosition : plafond égal au prix annuel\nRepli : plafond égal à deux fois le prix annuel\n'
               'Inacceptable : responsabilité illimitée\n\nClause : Juridiction\nPosition : tribunaux de Lyon\nRepli : tribunaux du siège du client')
    html = ('<section class="ax-card"><h2>%s</h2><form class="m5-form" data-api="m5636/outils/playbook" data-reload="1"><input type="hidden" name="id" value="%s">'
            '<label>Nom<input name="name" required value="%s" placeholder="Ex. : Contrat de prestation — côté client"></label>'
            '<label>Type de contrat<input name="kind" value="%s" placeholder="prestation, bail commercial, cession…"></label>'
            '<label>Positions (une par bloc, séparées par une ligne vide)<textarea name="positions" rows="10" placeholder="%s">%s</textarea></label>'
            '<button class="ax-btn" type="submit">Enregistrer le playbook</button></form></section>') % (
        'Modifier le playbook' if edit else 'Nouveau playbook', e((edit or {}).get('id', ''), quote=True), e((edit or {}).get('name', ''), quote=True),
        e((edit or {}).get('kind', ''), quote=True), e(example, quote=True), e(positions_text(json.loads(edit['positions'])) if edit else ''))
    if pbs:
        html += ('<section class="ax-card"><h2>Appliquer à un contrat</h2><form class="m5-form" data-api="m5636/outils/playbook/appliquer" data-reload="1">'
                 '<label>Playbook<select name="playbook">%s</select></label><label>Dossier%s</label>'
                 '<label>Contrat Word (chemin dans le dossier)<input name="path" required placeholder="/…/Contrat.docx"></label>'
                 '<button class="ax-btn" type="submit">Appliquer</button><p class="ax-muted">Une copie avec modifications suivies et une note de '
                 'justification sont créées à côté du contrat ; l’original reste intact. Dans Word, le complément AxiorHub propose les mêmes écarts.</p>'
                 '</form></section>') % (''.join('<option value="%s">%s</option>' % (e(p['id'], quote=True), e(p['name'])) for p in pbs), _matter_select(labels))
        html += '<ul class="o5636-list">%s</ul>' % ''.join('<li><a href="%s">%s</a> · %s · %d position(s)</li>' % (
            e(prefix + '/outils-ia?onglet=playbooks&playbook=' + p['id'], quote=True), e(p['name']), e(p['kind'] or 'tout contrat'),
            len(json.loads(p['positions']))) for p in pbs)
    runs = desk.db.execute('SELECT * FROM playbook_runs5636 ORDER BY created DESC LIMIT 10').fetchall()
    if runs:
        html += '<h3>Applications récentes</h3><ul class="o5636-list">%s</ul>' % ''.join('<li>%s — %s · %s%s</li>' % (
            e(r['source'].rsplit('/', 1)[-1]), e(labels.get(r['matter'], r['matter'])), e(STATUS.get(r['state'], r['state'])),
            (' · ' + e(', '.join(x.rsplit('/', 1)[-1] for x in json.loads(r['result']).get('files', [])))) if r['state'] == 'termine' else
            ((' · ' + e(json.loads(r['result']).get('erreur', ''))) if r['state'] == 'echec' else '')) for r in runs)
    return html


def _memos_html(desk, prefix, args, labels):
    q, tag = str(args.get('q') or ''), str(args.get('tag') or '')
    current = str(args.get('memo') or '')
    html = ('<section class="ax-card"><h2>Bibliothèque du cabinet</h2><form method="get" class="g5636-bar"><input type="hidden" name="onglet" value="memos">'
            '<input type="search" name="q" value="%s" placeholder="Rechercher un mémo"><input name="tag" value="%s" placeholder="Thème">'
            '<button class="ax-btn ghost" type="submit">Rechercher</button></form>') % (e(q, quote=True), e(tag, quote=True))
    memos = memo_search(desk, q, tag)
    html += '<ul class="o5636-list">%s</ul></section>' % (''.join('<li><a href="%s">%s</a> · %s%s</li>' % (
        e(prefix + '/outils-ia?onglet=memos&memo=' + m['id'], quote=True), e(m['title']), e(STATUS.get(m['status'], m['status'])),
        (' · ' + e(m['tags'])) if m['tags'] else '') for m in memos) or '<li class="ax-muted">Aucun mémo.</li>')
    m = next((x for x in memos if x['id'] == current), None)
    if m:
        html += ('<section class="ax-card"><h2>%s</h2><form class="m5-form" data-api="m5636/outils/memo" data-reload="1"><input type="hidden" name="id" value="%s">'
                 '<label>Titre<input name="title" value="%s"></label><label>Texte<textarea name="body" rows="14">%s</textarea></label>'
                 '<label>Sources (une par ligne)<textarea name="sources" rows="5" data-no-dictation>%s</textarea></label><label>Thèmes<input name="tags" value="%s"></label>'
                 '<label class="e5636-switch"><input type="checkbox" name="valider"%s> Mémo relu et validé (partagé avec tout le cabinet)</label>'
                 '<div class="ax-actions"><button class="ax-btn" type="submit">Enregistrer</button>'
                 '<button type="button" class="ax-btn ghost m5-export" data-api="m5636/outils/memo/word?id=%s">Télécharger (Word)</button></div></form></section>') % (
            e(m['title']), e(m['id'], quote=True), e(m['title'], quote=True), e(m['body']), e('\n'.join(m['sources'])), e(m['tags'], quote=True),
            ' checked' if m['status'] == 'valide' else '', e(m['id'], quote=True))
    html += ('<section class="ax-card"><h2>Nouveau mémo</h2><form class="m5-form" data-api="m5636/outils/memo/rediger" data-reload="1">'
             '<label>Question juridique<textarea name="question" rows="3" required placeholder="Ex. : Délai de prescription de l’action en garantie des vices cachés entre commerçants ?"></textarea></label>'
             '<label>Thèmes<input name="tags" placeholder="vente, prescription"></label><button class="ax-btn" type="submit">Rechercher et rédiger avec l’IA</button></form>'
             '<details><summary>Saisir un mémo à la main</summary><form class="m5-form" data-api="m5636/outils/memo" data-reload="1"><label>Titre<input name="title" required></label>'
             '<label>Texte<textarea name="body" rows="8" required></textarea></label><label>Sources (une par ligne)<textarea name="sources" rows="3"></textarea></label>'
             '<label>Thèmes<input name="tags"></label><button class="ax-btn ghost" type="submit">Enregistrer</button></form></details></section>')
    return html


def page_html(desk, prefix, args):
    schema(desk)
    tab = str(args.get('onglet') or 'grilles')
    if tab not in dict(TABS):
        tab = 'grilles'
    labels = _labels(desk)
    nav = '<nav class="tabs o5636-tabs">%s</nav>' % ''.join('<a class="%s" href="%s">%s</a>' % ('active' if k == tab else '', e(prefix + '/outils-ia?onglet=' + k, quote=True), e(v))
                                                           for k, v in TABS)
    intro = ('<h1>Outils IA</h1><p class="ax-muted">Des tableaux comparatifs sourcés, vos positions de négociation appliquées aux contrats, et une '
             'bibliothèque de mémos réutilisables. Tout résultat de l’IA reste à relire : les valeurs non retrouvées dans les pièces sont signalées.</p>')
    body = {'grilles': _grids_html, 'playbooks': _playbooks_html, 'memos': _memos_html}[tab](desk, prefix, args, labels)
    return intro + nav + body


def api_post(desk, route, data, owner, role='avocat'):
    if route == 'outils/grille':
        return grid_create(desk, data, owner)
    if route == 'outils/playbook':
        return playbook_save(desk, data, owner)
    if route == 'outils/playbook/appliquer':
        return playbook_start(desk, data, owner)
    if route == 'outils/playbook/propositions':   # complément Word : écarts proposés pour le document ouvert
        paragraphs = data.get('paragraphs') if isinstance(data.get('paragraphs'), list) else []
        return {'edits': propose_edits(desk, playbook(desk, str(data.get('playbook') or '')), paragraphs, str(data.get('matter') or ''))}
    if route == 'outils/memo':
        return memo_save(desk, data, owner, role)
    if route == 'outils/memo/rediger':
        return memo_ask(desk, data, owner)
    return None


def api_get(desk, route, args):
    if route == 'outils/grille/export':
        return grid_csv(desk, str(args.get('id') or ''))
    if route == 'outils/memo/word':
        return memo_docx(desk, str(args.get('id') or ''))
    if route == 'outils/playbooks':
        schema(desk)
        return {'playbooks': [{'id': r['id'], 'name': r['name'], 'kind': r['kind']} for r in desk.db.execute('SELECT * FROM playbooks5636 WHERE archived=0 ORDER BY name')]}
    if route == 'outils/memos':
        return {'memos': [{k: m[k] for k in ('id', 'title', 'status', 'tags', 'body', 'sources')} for m in memo_search(desk, args.get('q', ''), args.get('tag', ''), 'valide')]}
    return None
