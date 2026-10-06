"""Missions persistantes : une intention, les producteurs existants, un résultat prouvé.

Pas de second moteur IA. Les missions référencent les jobs, conversations et
documents du cabinet. Elles n'autorisent jamais un envoi, une signature ou une
suppression. Une commande répétée avec le même identifiant ne crée pas de travail.
"""
import hashlib
import json
import re
import secrets
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from .common import Stop, clean_path, fold, load_matters, matter_display, under

SCHEMA = '''CREATE TABLE IF NOT EXISTS missions_v567(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, instruction TEXT NOT NULL, matter TEXT NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, context TEXT NOT NULL, plan TEXT NOT NULL,
 exceptions TEXT NOT NULL, job_id INTEGER, ref TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(owner,request_key));
 CREATE INDEX IF NOT EXISTS missions567_owner ON missions_v567(owner,updated);
 CREATE TABLE IF NOT EXISTS mission_events_v567(
 id INTEGER PRIMARY KEY, mission TEXT NOT NULL, at TEXT NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL);
'''
ACTIVE = {'creating', 'queued', 'running', 'cancel_requested'}
LABELS = {'creating': 'Demande enregistrée', 'queued': 'En attente', 'running': 'Préparation en cours',
          'suggested':'Plan proposé, préparation non lancée',
          'cancel_requested': 'Arrêt demandé', 'paused': 'Suspendue', 'decision': 'Une précision est nécessaire',
          'prepared': 'Projet préparé', 'verified': 'Livrable déposé et relu', 'answered': 'Réponse disponible',
          'abstained': 'Aucune production nécessaire', 'error': 'Traitement interrompu'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def check_authority(desk, args):
    """Revalide la mission immédiatement avant un effet, même après une longue inférence."""
    ident = str(args.get('mission_id') or '')
    if not ident:
        desk.mission_owner567 = 'cabinet'
        return
    if desk.settings('missions567:pause', False):
        raise Stop('preparation_suspendue')
    row = desk.db.execute('SELECT m.owner,m.kind,m.state,j.status AS job_status FROM missions_v567 m LEFT JOIN jobs j ON j.id=m.job_id WHERE m.id=?', (ident,)).fetchone()
    if not row:
        raise Stop('autorisation_mission_absente')
    desk.mission_owner567 = row['owner']
    if row['state'] == 'paused' or row['job_status'] in ('cancelled','cancel_requested'):
        raise Stop('preparation_suspendue')
    if row['owner'] == 'cabinet':
        return                         # installation avec authentification serveur
    root = Path(os.environ.get('AXIORHUB_AUTH_STATE') or (os.environ.get('AXIORHUB_DATA_DIR', '/data') + '/auth'))
    if not (root / 'users.sqlite3').is_file():
        raise Stop('autorisation_mission_non_verifiable')
    db = sqlite3.connect('file:' + str(root / 'users.sqlite3') + '?mode=ro', uri=True, timeout=10)
    try:
        member = db.execute('SELECT active,role FROM users WHERE email=?', (row['owner'],)).fetchone()
    finally:
        db.close()
    if not member or not member[0] or member[1] not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('autorisation_mission_revoquee')
    if member[1] == 'assistant' and row['kind'] == 'mail':
        raise Stop('depot_brouillon_role_insuffisant')


def _event(desk, ident, kind, detail):
    desk.db.execute('INSERT INTO mission_events_v567(mission,at,kind,detail) VALUES (?,?,?,?)',
                    (ident, desk.now(), kind, json.dumps(detail, ensure_ascii=False)))
    desk.db.commit()
    from .live430 import emit
    emit(desk, 'mission', 'Mission : ' + LABELS.get(kind, kind), matter=str(detail.get('matter') or ''))


def _row(desk, ident, owner, admin=False):
    if not re.fullmatch(r'[a-f0-9]{32}', str(ident)):
        raise Stop('mission_invalide')
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM missions_v567 WHERE id=?', (ident,)).fetchone()
    if not row or (row['owner'] != owner and not admin):
        raise Stop('mission_absente')
    return dict(row)


def _context(desk, data, matter, owner='cabinet'):
    raw = data.get('context') or {}
    if not isinstance(raw, dict):
        raise Stop('contexte_mission_invalide')
    parent_id = str(raw.get('mission_id') or '')
    active, recent, thread_id = '', [], ''
    if parent_id:
        previous = get(desk,parent_id,owner)
        if previous['matter'] != (matter['id'] if matter else ''):
            raise Stop('mission_precedente_autre_dossier')
        active = previous['instruction'][:300]
        if previous['result'].get('text'):
            recent = [previous['result']['text'][:4000]]
        if previous['kind']=='question' and previous['ref'].startswith('ask:'):
            thread_id = previous['ref'][4:]
    sources = raw.get('selected_documents') or []
    if not isinstance(sources, list) or len(sources) > 20:
        raise Stop('sources_mission_invalides')
    paths = []
    for source in sources:
        path = clean_path(str(source))
        if not matter or not under(path, matter['path']):
            raise Stop('source_mission_autre_dossier')
        if path not in paths:
            paths.append(path)
    attachments = data.get('attachments') or []
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise Stop('trois_pieces_maximum')
    key = str(raw.get('mail_key') or '')
    if key:
        from .workspace import chat_scope
        _, selected, _ = chat_scope(desk.c, {'matter': matter['id'] if matter else '', 'key': key})
        if selected and matter and selected['id'] != matter['id']:
            raise Stop('courriel_mission_autre_dossier')
    for ident in attachments:
        from .improvements36 import attachment_source
        attachment_source(desk, str(ident), matter['id'] if matter else '', key)
    page = str(raw.get('page') or '/')
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{0,160}', page):
        page = '/'
    return {'page': page, 'selected_documents': paths, 'mail_key': key,
            'attachments': list(dict.fromkeys(str(a) for a in attachments)),
            'parent_mission':parent_id, 'active_mission':active, 'recent_results':recent, 'thread_id':thread_id,
            'due': str(data.get('due') or '')[:32], 'channel': 'voice' if data.get('channel') == 'voice' else 'text'}


def create(desk, data, owner='cabinet'):
    ensure_schema(desk)
    text = str(data.get('instruction') or '').strip()
    if not 3 <= len(text) <= 12000:
        raise Stop('question_requise_12000_caracteres_maximum')
    token = str(data.get('request_key') or '')
    if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', token):
        raise Stop('identifiant_requete_invalide')
    matters = {str(m['id']): m for m in load_matters(desk.c)}
    chosen = str(data.get('matter') or '')
    if chosen and chosen not in matters:
        raise Stop('dossier_absent')
    from .docrequest520 import find_matter, guess_kind
    named, candidates = find_matter(desk, text)
    exceptions = []
    if chosen and named and named['id'] != chosen:
        exceptions.append({'code': 'changement_dossier', 'message': 'La demande nomme un autre dossier. Choisissez explicitement le dossier de cette mission.',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in (matters[chosen], named)]})
    elif not chosen and named:
        chosen = named['id']
    elif not chosen and candidates:
        exceptions.append({'code': 'dossier_ambigu', 'message': 'Quel dossier concerne cette demande ?',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in candidates]})
    ctx = _context(desk, data, matters.get(chosen), owner)
    f = fold(text)
    from .cockpit530 import is_document_request
    kind = 'mail' if ctx['mail_key'] and re.search(r'\b(repond|reponds|repondre|reponse|brouillon)\b', f) else \
           ('document' if is_document_request(text) or any(word in f for word in ('plaidoirie', 'audience', 'conclusions', 'contrat')) else 'question')
    if data.get('analysis_only') is True:
        kind = 'question'
    if kind == 'document' and not chosen and not exceptions:
        exceptions.append({'code': 'dossier_requis', 'message': 'Choisissez le dossier où préparer ce document.',
                           'candidates': [{'id': m['id'], 'label': matter_display(m)} for m in list(matters.values())[:100]]})
    if desk.settings('missions567:pause', False):
        exceptions.append({'code': 'preparation_suspendue', 'message': 'La préparation de nouvelles missions est suspendue par l’administrateur.'})
    plan = {'objective': text, 'deliverable': {'mail': 'Brouillon IMAP', 'document': 'Projet Word dans Nextcloud', 'question': 'Réponse sourcée'}[kind],
            'steps': ['Identifier le dossier et les sources', 'Consulter les sources autorisées et l’agenda',
                      'Préparer le résultat', 'Contrôler et relire le dépôt' if kind != 'question' else 'Présenter la réponse et ses sources'],
            'restrictions': ['Aucun envoi de courriel', 'Aucun dépôt auprès d’un tiers', 'Aucune signature ni facture définitive'],
            'document_kind': 'note' if 'plaidoirie' in f or 'audience' in f else guess_kind(text)}
    from .assistant567 import profile
    autonomy = str(data.get('autonomy') or ('suggest' if profile(desk,owner)['initiative']=='proposer' else 'prepare'))
    if autonomy not in ('prepare','suggest'):
        raise Stop('autonomie_mission_invalide')
    plan['autonomy']=autonomy
    canonical = json.dumps({'instruction': text, 'matter': chosen, 'kind': kind, 'context': ctx,'autonomy':autonomy}, ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(canonical.encode()).hexdigest()
    ident, stamp = secrets.token_hex(16), desk.now()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        old = desk.db.execute('SELECT id,fingerprint FROM missions_v567 WHERE owner=? AND request_key=?', (owner, token)).fetchone()
        if old:
            if old['fingerprint'] != fp:
                raise Stop('requete_reutilisee_avec_autres_donnees')
            desk.db.commit()
            return get(desk, old['id'], owner)
        desk.db.execute('INSERT INTO missions_v567 VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)',
                        (ident, owner, token, fp, text, chosen, kind, 'decision' if exceptions else ('suggested' if autonomy=='suggest' else 'creating'),
                         json.dumps(ctx, ensure_ascii=False), json.dumps(plan, ensure_ascii=False), json.dumps(exceptions, ensure_ascii=False), '', stamp, stamp))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    _event(desk, ident, 'decision' if exceptions else 'creating', {'matter': chosen, 'kind': kind})
    if not exceptions and autonomy=='prepare':
        _launch(desk, _row(desk, ident, owner))
    return get(desk, ident, owner)


def _launch(desk, row):
    ctx, plan = json.loads(row['context']), json.loads(row['plan'])
    try:
        if row['kind'] == 'mail':
            job = desk.enqueue('prepare_reply', {'key': ctx['mail_key'], 'matter': row['matter'],
                               'instruction': row['instruction'], 'mission_id': row['id']}, priority=0)
            ref = 'mail:' + ctx['mail_key']
        elif row['kind'] == 'document':
            from .docrequest520 import submit
            out = submit(desk, row['instruction'], row['matter'], plan['document_kind'],
                         attachments=ctx['attachments'], mission_id=row['id'], selected_documents=ctx['selected_documents'])
            job, ref = out['job_id'], 'docreq:' + out['request']
        else:
            from .integration import submit_question
            out = submit_question(desk, row['instruction'], row['matter'], ctx['mail_key'], attachment_ids=ctx['attachments'],
                                  thread_id=ctx.get('thread_id',''), page_context={k:ctx.get(k) for k in ('page','selected_documents','active_mission','recent_results')}, mission_id=row['id'],
                                  purpose='voice_conversation' if ctx.get('channel')=='voice' else 'assistant')
            job, ref = out['job_id'], 'ask:' + out['thread_id']
        desk.db.execute("UPDATE missions_v567 SET state='queued',job_id=?,ref=?,updated=? WHERE id=?", (job, ref, desk.now(), row['id']))
        desk.db.commit()
        _event(desk, row['id'], 'queued', {'matter': row['matter'], 'job_id': job})
    except (Stop, OSError) as error:
        desk.db.execute("UPDATE missions_v567 SET state='error',exceptions=?,updated=? WHERE id=?", 
                        (json.dumps([{'code': str(error), 'message': 'Préparation non démarrée. Consultez le motif et reprenez la mission.'}]), desk.now(), row['id']))
        desk.db.commit()


def get(desk, ident, owner='cabinet', admin=False, prefix=''):
    row = _row(desk, ident, owner, admin)
    if not row['job_id'] and row['state'] in ('creating','error'):
        orphan=desk.db.execute("SELECT id,kind,args FROM jobs WHERE json_valid(args) AND json_extract(args,'$.mission_id')=? ORDER BY id LIMIT 1",(ident,)).fetchone()
        if orphan:
            a=json.loads(orphan['args']);ref='docreq:'+a['request'] if orphan['kind']=='docrequest520' else ('ask:'+a['thread'] if orphan['kind']=='assistant_answer' else 'mail:'+a.get('key',''))
            desk.db.execute("UPDATE missions_v567 SET job_id=?,ref=?,state='queued' WHERE id=?",(orphan['id'],ref,ident));desk.db.commit()
            row.update(job_id=orphan['id'],ref=ref,state='queued')
    for key in ('context', 'plan', 'exceptions'):
        row[key] = json.loads(row[key])
    row['result'] = {}
    if row['job_id']:
        job = desk.db.execute('SELECT id,status,progress,result FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
        if job:
            result = json.loads(job['result'] or '{}')
            state = {'pending': 'queued', 'running': 'running', 'error': 'error', 'cancelled': 'paused', 'cancel_requested': 'cancel_requested'}.get(job['status'], row['state'])
            if job['status'] == 'done':
                state = 'prepared'
                if row['kind'] == 'mail':
                    state = 'verified' if result.get('brouillon_imap') == 'verifie' else ('prepared' if result.get('projet_prepare') else 'abstained')
                    row['result'] = {'message': result.get('message', ''), 'proof': result.get('brouillon_imap', ''),
                                     'open_url': prefix + '/mail?' + urlencode({'key': row['context']['mail_key']})}
                elif row['kind'] == 'document' and row['ref'].startswith('docreq:'):
                    doc = desk.db.execute('SELECT status,path,result FROM docreq520 WHERE id=?', (row['ref'][7:],)).fetchone()
                    if doc and doc['status'] == 'cree':
                        proof = json.loads(doc['result'] or '{}')
                        state = 'verified' if proof.get('readback_sha256') == proof.get('sha256') and proof.get('sha256') else 'prepared'
                        row['result'] = {'message': 'Projet Word disponible dans le dossier.', 'path': doc['path'], 'sources': proof.get('sources', []),
                                         'to_complete': proof.get('to_complete', []), 'proof': proof.get('readback_sha256', ''),
                                         'open_url': prefix + '/documents/edit?' + urlencode({'path': doc['path']})}
                    elif doc and doc['status'] == 'dossier_a_choisir':
                        state = 'decision'
                elif row['kind'] == 'question' and row['ref'].startswith('ask:'):
                    answer = desk.db.execute("SELECT content,sources FROM assistant_messages WHERE thread_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (row['ref'][4:],)).fetchone()
                    if answer:
                        state = 'answered'
                        row['result'] = {'text': answer['content'], 'sources': json.loads(answer['sources'] or '[]')}
            row['job'] = {'id': job['id'], 'status': job['status'], 'progress': job['progress'] or '',
                          'error': result.get('erreur', '') if job['status'] == 'error' else ''}
            if state != row['state']:
                desk.db.execute('UPDATE missions_v567 SET state=?,updated=? WHERE id=?', (state, desk.now(), ident))
                desk.db.commit()
            row['state'] = state
    row['label'] = LABELS.get(row['state'], row['state'])
    row['matter_label'] = next((matter_display(m) for m in load_matters(desk.c) if m['id'] == row['matter']), '')
    return row


def listing(desk, owner='cabinet', admin=False, prefix=''):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT id FROM missions_v567' + ('' if admin else ' WHERE owner=?') + ' ORDER BY created DESC LIMIT 40',
                          () if admin else (owner,)).fetchall()
    return [get(desk, r['id'], owner, admin, prefix) for r in rows]


def control(desk, data, owner='cabinet', admin=False):
    row = _row(desk, str(data.get('id') or ''), owner, admin)
    current = get(desk, row['id'], owner, admin)
    row = _row(desk, row['id'], owner, admin)  # get peut avoir rattaché un job orphelin
    action = str(data.get('action') or '')
    if action == 'pause':
        if current['state'] not in ACTIVE:
            raise Stop('mission_non_active')
        if row['job_id']:
            desk.cancel_job(row['job_id'])
        else:
            desk.db.execute("UPDATE missions_v567 SET state='paused',updated=? WHERE id=?", (desk.now(), row['id']))
            desk.db.commit()
    elif action == 'resolve':
        if current['state'] != 'decision':
            raise Stop('mission_sans_question')
        matter = str(data.get('matter') or '')
        choices = {m['id'] for m in load_matters(desk.c)}
        if matter not in choices:
            raise Stop('dossier_absent')
        ctx = json.loads(row['context'])
        _context(desk, {'context': ctx, 'attachments': ctx['attachments']}, next(m for m in load_matters(desk.c) if m['id'] == matter))
        if desk.settings('missions567:pause', False):
            raise Stop('preparation_suspendue')
        claimed=desk.db.execute("UPDATE missions_v567 SET matter=?,state='creating',exceptions='[]',updated=? WHERE id=? AND state='decision'", (matter, desk.now(), row['id']))
        desk.db.commit()
        if not claimed.rowcount:
            return get(desk, row['id'], owner, admin)
        _launch(desk, _row(desk, row['id'], owner, admin))
    elif action == 'resume':
        if current['state'] not in ('error', 'paused','suggested'):
            raise Stop('mission_non_reprenante')
        if desk.settings('missions567:pause', False):
            raise Stop('preparation_suspendue')
        if row['job_id']:
            # Reprend le même job et son producteur, donc le même identifiant de
            # document. Les producteurs vérifient leurs dépôts avant de rejouer.
            desk.db.execute("UPDATE jobs SET status='pending',finished=NULL,result=NULL WHERE id=? AND status IN ('error','cancelled')", (row['job_id'],))
            desk.db.execute("UPDATE missions_v567 SET state='queued',exceptions='[]',updated=? WHERE id=?", (desk.now(), row['id']))
            desk.db.commit()
        else:
            claimed=desk.db.execute("UPDATE missions_v567 SET state='creating',updated=? WHERE id=? AND state IN ('error','paused','suggested')",(desk.now(),row['id']))
            desk.db.commit()
            if not claimed.rowcount:
                return get(desk,row['id'],owner,admin)
            _launch(desk,_row(desk,row['id'],owner,admin))
    else:
        raise Stop('action_mission_invalide')
    _event(desk, row['id'], action, {'matter': row['matter']})
    return get(desk, row['id'], owner, admin)
