"""Workflow, conversational history and narrow integration API helpers.

The module contains no network listener and performs no arbitrary command.  It
only exposes bounded operations already controlled by the mail-agent backend.
"""
from datetime import datetime, timezone, timedelta
import json
import re
import secrets
import sqlite3
from pathlib import Path

from .common import Stop, digest, load_matters
from .desk import report_for
from .index import DocumentIndex
from .state import State


TERMINAL_WORK_STATES = {'draft_ready', 'handled', 'archived'}
ACTIVE_WORK_STATES = {'needs_action', 'needs_confirmation', 'drafting', 'processing'}
STABLE_WORK_STATES = TERMINAL_WORK_STATES | {'backlog'}
CONFIRM_REASONS = {
    'correspondant_ou_dossier_a_confirmer', 'destinataires_multiples_a_verifier',
    'divulgation_a_un_tiers_a_valider', 'index_dossier_a_completer',
    'demande_ambigue', 'aucun_document_exploitable', 'controle_qualite_non_satisfait',
}


def workflow_state(status, reason):
    if status == 'drafted':
        return 'draft_ready'
    if status == 'ignored':
        return 'ignored'
    if status in ('appending', 'append_uncertain'):
        return 'drafting'
    if status == 'retry':
        return 'processing'
    if reason in CONFIRM_REASONS:
        return 'needs_confirmation'
    return 'needs_action'


def sync_work_items(desk):
    """Project immutable processing reports into a mutable business inbox."""
    state = State(desk.c['state_dir'])
    seen = set()
    for key, status, reason, stamp in state.rows(5000):
        try:
            report = report_for(desk.c, key)
        except Stop:
            continue
        seen.add(key)
        target = workflow_state(status, reason)
        linked=desk.db.execute('''SELECT matter FROM portfolio_mail_links
          WHERE mail_key=? AND status IN ('automatic','confirmed')
          ORDER BY confidence DESC,updated DESC LIMIT 1''',(key,)).fetchone()
        matter_value=report.get('matter') or (linked['matter'] if linked else '')
        row = desk.db.execute('SELECT state,source_status,source_reason FROM work_items WHERE mail_key=?',
                              (key,)).fetchone()
        if not row:
            desk.db.execute('''INSERT INTO work_items
                (mail_key,state,source_status,source_reason,matter,subject,sender,received,updated,resolved)
                VALUES (?,?,?,?,?,?,?,?,?,NULL)''',
                (key, target, status, reason, matter_value,
                 report.get('subject') or '', report.get('sender') or '',
                 report.get('received_at') or stamp, stamp))
            continue
        current = row['state']
        if current in STABLE_WORK_STATES and target not in ('draft_ready',):
            desk.db.execute('UPDATE work_items SET source_status=?,source_reason=? WHERE mail_key=?',
                            (status, reason, key))
            continue
        # A user action moves the card immediately. Keep that transitional state
        # until the underlying report changes or the queued action finishes.
        if current in ('drafting', 'processing') and row['source_status'] == status and row['source_reason'] == reason:
            continue
        desk.db.execute('''UPDATE work_items SET state=?,source_status=?,source_reason=?,matter=?,
            subject=?,sender=?,received=?,updated=?,resolved=? WHERE mail_key=?''',
            (target, status, reason, matter_value, report.get('subject') or '',
             report.get('sender') or '', report.get('received_at') or stamp, stamp,
             stamp if target in TERMINAL_WORK_STATES else None, key))
    desk.db.commit()
    return len(seen)


def set_work_state(desk, key, state, reason=''):
    if state not in ACTIVE_WORK_STATES | STABLE_WORK_STATES | {'ignored'}:
        raise Stop('etat_metier_invalide')
    row = desk.db.execute('SELECT 1 FROM work_items WHERE mail_key=?', (key,)).fetchone()
    if not row:
        sync_work_items(desk)
    stamp = desk.now()
    cur = desk.db.execute('UPDATE work_items SET state=?,updated=?,resolved=? WHERE mail_key=?',
                          (state, stamp, stamp if state in TERMINAL_WORK_STATES else None, key))
    if not cur.rowcount:
        raise Stop('courriel_absent_du_tableau')
    desk.audit('etat_metier_modifie', {'key': key, 'state': state, 'reason': reason})
    desk.db.commit()


def work_items(desk, states=None, limit=100):
    sync_work_items(desk)
    limit = max(1, min(int(limit), 200))
    if states:
        states = [x for x in states if x in ACTIVE_WORK_STATES | STABLE_WORK_STATES | {'ignored'}]
        if not states:
            return []
        marks = ','.join('?' for _ in states)
        rows = desk.db.execute('SELECT * FROM work_items WHERE state IN ('+marks+') ORDER BY updated DESC LIMIT ?',
                               (*states, limit)).fetchall()
    else:
        rows = desk.db.execute('SELECT * FROM work_items ORDER BY updated DESC LIMIT ?', (limit,)).fetchall()
    return [dict(x) for x in rows]


def _page_context(value, selected_matter=''):
    if not isinstance(value,dict):return {}
    context={}
    for key,maximum in (('page',160),('active_mission',300)):
        if value.get(key):context[key]=str(value[key]).strip()[:maximum]
    documents=value.get('selected_documents',[])
    if isinstance(documents,list):context['selected_documents']=[str(x).strip()[:1000] for x in documents if str(x).strip()][:20]
    results=value.get('recent_results',[])
    if isinstance(results,list):context['recent_results']=[str(x).strip()[:800] for x in results if str(x).strip()][:5]
    # The browser context may describe the current matter, but it can never
    # switch the server-side scope chosen and validated by chat_scope.
    context['matter']=str(selected_matter or '')[:80]
    return context


def submit_question(desk, question, matter='', mail_key='', thread_id='', attachment_id='', attachment_ids=None, page_context=None, mission_id='', purpose='assistant'):
    if purpose not in ('assistant','voice_conversation'):raise Stop('fonction_ia_invalide')
    from .workspace import chat_scope
    question = (question or '').strip()
    if not question or len(question) > 12000:
        raise Stop('question_requise_12000_caracteres_maximum')
    scope, selected, report = chat_scope(desk.c, {'matter': matter, 'key': mail_key})
    attachments=[]
    if isinstance(attachment_ids,list):attachments=[str(x) for x in attachment_ids if x]
    elif attachment_id:attachments=[str(attachment_id)]
    if len(attachments)>3 or len(set(attachments))!=len(attachments):raise Stop('trois_pieces_maximum')
    if attachment_id and not attachments:attachments=[str(attachment_id)]
    for ident in attachments:
        from .improvements36 import attachment_source
        attachment_source(desk,ident,matter,mail_key)
    if thread_id:
        if not re.fullmatch(r'[a-f0-9]{32}', thread_id):
            raise Stop('conversation_invalide')
        thread = desk.db.execute('SELECT * FROM assistant_threads WHERE id=?', (thread_id,)).fetchone()
        if not thread or thread['scope'] != scope:
            raise Stop('conversation_autre_contexte')
    else:
        thread_id = secrets.token_hex(16)
        title = question[:90]
        desk.db.execute('''INSERT INTO assistant_threads
            (id,scope,matter,mail_key,title,created,updated) VALUES (?,?,?,?,?,?,?)''',
            (thread_id, scope, selected['id'] if selected else '', mail_key if report else '',
             title, desk.now(), desk.now()))
    cur = desk.db.execute('''INSERT INTO assistant_messages
        (thread_id,role,content,status,sources,job_id,created,updated)
        VALUES (?,?,?,?,?,NULL,?,?)''',
        (thread_id, 'user', question, 'queued', '[]', desk.now(), desk.now()))
    message_id = cur.lastrowid
    safe_context=_page_context(page_context,selected['id'] if selected else '')
    job = desk.enqueue('assistant_answer', {'message': message_id, 'thread': thread_id,
                                             'mission_id':mission_id,
                                             'matter': matter, 'key': mail_key,
                                             'attachment_id':attachments[0] if attachments else '',
                                             'attachment_ids':attachments,
                                             'page_context':safe_context,'purpose':purpose})
    desk.db.execute('UPDATE assistant_messages SET job_id=? WHERE id=?', (job, message_id))
    desk.db.execute('UPDATE assistant_threads SET updated=? WHERE id=?', (desk.now(), thread_id))
    desk.db.commit()
    return {'thread_id': thread_id, 'message_id': message_id, 'job_id': job,
            'status': 'queued', 'scope': 'matter' if selected else ('mail' if report else 'cabinet')}


def answer_question(desk, args):
    from .workspace import chat
    from .dav import DAV
    from .mailbox import Mailbox
    from .model import Model,routed_config

    try:
        message_id = int(args.get('message', 0))
    except (TypeError, ValueError):
        raise Stop('message_assistant_invalide') from None
    row = desk.db.execute('SELECT * FROM assistant_messages WHERE id=? AND role="user"',
                          (message_id,)).fetchone()
    if not row or row['thread_id'] != args.get('thread'):
        raise Stop('message_assistant_absent')
    if row['status']=='done':
        return {'thread_id':row['thread_id'],'status':'done','message':'Réponse déjà préparée ; aucune nouvelle inférence.'}
    desk.db.execute('UPDATE assistant_messages SET status=?,updated=? WHERE id=?',
                    ('running', desk.now(), message_id)); desk.db.commit()
    matter = args.get('matter', '')
    key = args.get('key', '')
    box = Mailbox(desk.c['mail']) if key else None
    try:
        result = chat(desk, {'matter': matter, 'key': key, 'question': row['content'],
                             'attachment_id':args.get('attachment_id',''),
                             'attachment_ids':args.get('attachment_ids',[]),
                             'page_context':_page_context(args.get('page_context'),matter)},
                      DAV(desk.c['nextcloud']) if matter else None,
                      Model(routed_config(desk.c,args.get('purpose','assistant'))), box, return_result=True)
    except Exception:
        desk.db.execute('UPDATE assistant_messages SET status=?,updated=? WHERE id=?',
                        ('error', desk.now(), message_id)); desk.db.commit()
        raise
    finally:
        if box:
            box.close()
    desk.db.execute('UPDATE assistant_messages SET status=?,updated=? WHERE id=?',
                    ('done', desk.now(), message_id))
    desk.db.execute('''INSERT INTO assistant_messages
        (thread_id,role,content,status,sources,job_id,created,updated)
        VALUES (?,?,?,?,?,NULL,?,?)''',
        (row['thread_id'], 'assistant', result['answer'], 'done',
         json.dumps(result.get('sources', []), ensure_ascii=False), desk.now(), desk.now()))
    desk.db.execute('UPDATE assistant_threads SET updated=? WHERE id=?',
                    (desk.now(), row['thread_id']))
    desk.db.commit()
    return {'thread_id': row['thread_id'], 'status': 'done',
            'sources': len(result.get('sources', [])), 'proposed_actions': result.get('proposed_actions', [])}


def threads(desk, limit=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    desk.db.execute('DELETE FROM assistant_threads WHERE updated<?', (cutoff,)); desk.db.commit()
    rows = desk.db.execute('SELECT * FROM assistant_threads ORDER BY updated DESC LIMIT ?',
                           (max(1, min(int(limit), 100)),)).fetchall()
    return [dict(x) for x in rows]


def thread_messages(desk, thread_id):
    if not re.fullmatch(r'[a-f0-9]{32}', thread_id or ''):
        raise Stop('conversation_invalide')
    if not desk.db.execute('SELECT 1 FROM assistant_threads WHERE id=?', (thread_id,)).fetchone():
        raise Stop('conversation_absente')
    return [dict(x) for x in desk.db.execute(
        'SELECT * FROM assistant_messages WHERE thread_id=? ORDER BY id LIMIT 100', (thread_id,))]


def cabinet_search(c, query, limit=12):
    query = (query or '').strip()
    if not query or len(query) > 1000:
        raise Stop('recherche_requise_1000_caracteres_maximum')
    matters = {m['id']: m for m in load_matters(c)}
    rows, coverage = DocumentIndex(c['state_dir'], c.get('rag'), c.get('ollama')).global_sources(
        [query], limit=max(1, min(int(limit), 30)))
    portfolio={}
    try:
        db=sqlite3.connect(Path(c['state_dir'])/'desk.sqlite3')
        portfolio={row[0]:row[1] for row in db.execute('SELECT matter,state FROM matter_portfolio')}
        db.close()
    except sqlite3.Error:
        portfolio={}
    results = []
    for row in rows:
        m = matters.get(row['matter'])
        if not m:
            continue
        results.append({**row, 'matter_name': m.get('client_name', ''), 'matter_path': m['path'],
                        'portfolio_state':portfolio.get(row['matter'],'dormant')})
    return {'query': query, 'results': results, 'coverage': coverage,
            'warning': 'Résultats limités aux contenus déjà indexés ; chaque extrait reste à vérifier dans sa source.'}


def dashboard(desk):
    from .legal_memory import ensure_schema
    ensure_schema(desk)
    from .strategic import ensure_schema as ensure_strategic_schema
    ensure_strategic_schema(desk)
    sync_work_items(desk)
    counts = {row[0]: row[1] for row in desk.db.execute(
        'SELECT state,COUNT(*) FROM work_items GROUP BY state')}
    index = DocumentIndex(desk.c['state_dir'])
    matters = {m['id']: m for m in load_matters(desk.c)}
    from .portfolio import portfolio_rows,portfolio_summary
    portfolio=portfolio_summary(desk)
    active_rows=portfolio_rows(desk,['active','to_confirm'],limit=200,refresh_missing=False)
    active_ids={row['matter'] for row in active_rows}
    recent = []
    for portfolio_row in active_rows[:12]:
        mid=portfolio_row['matter'];stamp=portfolio_row['last_external_activity'] or portfolio_row['next_event']
        if mid not in matters:
            continue
        m = matters[mid]
        docs = index.db.execute('SELECT COUNT(*) FROM docs WHERE matter=?', (mid,)).fetchone()[0]
        open_tasks = desk.db.execute("SELECT COUNT(*) FROM tasks WHERE matter=? AND status='open'", (mid,)).fetchone()[0]
        brief = desk.db.execute('SELECT data FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
        summary = ''
        if brief:
            try:
                summary = json.loads(brief[0]).get('summary', '')[:400]
            except (ValueError, TypeError):
                pass
        recent.append({'id': mid, 'name': m.get('client_name', ''), 'path': m['path'],
                       'last_activity': stamp, 'documents': docs, 'open_tasks': open_tasks,
                       'portfolio_state':portfolio_row['state'],
                       'memory_to_confirm':desk.db.execute("SELECT COUNT(*) FROM legal_memory_records WHERE matter=? AND status='suggested'",(mid,)).fetchone()[0],
                       'memory_conflicts':desk.db.execute("SELECT COUNT(*) FROM legal_memory_conflicts WHERE matter=? AND status='open'",(mid,)).fetchone()[0],
                       'strategy_count':desk.db.execute('SELECT COUNT(*) FROM strategy_analyses WHERE matter=?',(mid,)).fetchone()[0],
                       'matrix_to_confirm':desk.db.execute("SELECT COUNT(*) FROM evidence_matrix_rows WHERE matter=? AND status='proposed'",(mid,)).fetchone()[0],
                       'act_projects':desk.db.execute('SELECT COUNT(*) FROM act_projects WHERE matter=?',(mid,)).fetchone()[0],
                       'summary': summary})
    active_jobs = [dict(x) for x in desk.db.execute(
        "SELECT id,kind,status,priority,created FROM jobs WHERE status IN ('pending','running','cancel_requested') ORDER BY priority,id LIMIT 20")]
    last_threads = threads(desk, 8)
    def scoped_count(table,condition):
        if not active_ids:return 0
        marks=','.join('?' for _ in active_ids)
        return desk.db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+condition+
          ' AND matter IN ('+marks+')',tuple(active_ids)).fetchone()[0]
    memory_to_confirm=scoped_count('legal_memory_records',"status='suggested'")
    memory_conflicts=scoped_count('legal_memory_conflicts',"status='open'")
    matrix_to_confirm=scoped_count('evidence_matrix_rows',"status='proposed'")
    act_projects_count=scoped_count('act_projects',"status<>'archived'")
    return {'counts': counts, 'recent_matters': recent, 'jobs': active_jobs,
            'portfolio':portfolio,
            'legal_memory':{'to_confirm':memory_to_confirm,'conflicts':memory_conflicts},
            'strategic':{'matrix_to_confirm':matrix_to_confirm,'act_projects':act_projects_count},
            'conversations': last_threads, 'generated_at': desk.now(),
            'proposed_actions': [
                {'label': 'Préparer les réponses en attente', 'count': counts.get('needs_action', 0)},
                {'label': 'Confirmer les dossiers proposés', 'count': counts.get('needs_confirmation', 0)},
                {'label': 'Vérifier les brouillons prêts', 'count': counts.get('draft_ready', 0)},
                {'label': 'Confirmer les informations extraites', 'count': memory_to_confirm},
                {'label': 'Examiner les contradictions', 'count': memory_conflicts},
                {'label': 'Vérifier les matrices de preuve', 'count': matrix_to_confirm},
                {'label': 'Relire les projets d’actes', 'count': act_projects_count},
            ]}
