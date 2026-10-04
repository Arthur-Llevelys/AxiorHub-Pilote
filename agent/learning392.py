"""Explicit, reversible business learning for AxiorHub 3.9.2.

Only lawyer-approved guidance and approved template metadata are exposed to a
model.  Client facts, source documents and the text of old corrections are not
turned into global rules.  Every use is recorded so the lawyer can see whether
the learning layer is actually used.
"""
from datetime import datetime, timezone, timedelta
import json
import re

from .common import Stop, digest


PURPOSES = {
    'assistant': 'Assistant',
    'mail_drafting': 'Courriels',
    'mail_triage': 'Classement des courriels',
    'document_drafting': 'Conclusions et documents',
    'hearing': 'Plaidoirie',
    'word_revision': 'Révision Word',
}


def ensure_schema(desk):
    from .relevance370 import ensure_schema as ensure_relevance
    from .cabinet_docs33 import ensure_schema as ensure_templates
    ensure_relevance(desk);ensure_templates(desk)
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS learning_uses_v392(
      id INTEGER PRIMARY KEY, purpose TEXT NOT NULL, matter TEXT NOT NULL,
      correction_ids TEXT NOT NULL, template_ids TEXT NOT NULL,
      fingerprint TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS learning_uses_created_v392
      ON learning_uses_v392(created,purpose);
    CREATE TABLE IF NOT EXISTS learning_outcomes_v392(
      output_id TEXT PRIMARY KEY, decision TEXT NOT NULL,
      correction_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);
    ''')
    desk.db.commit()


def _stamp():return datetime.now(timezone.utc).isoformat()


def _json(value, default):
    try:return json.loads(value) if value else default
    except (ValueError,TypeError):return default


def _allowed_kinds(purpose):
    if purpose=='mail_triage':return ('classification','association')
    if purpose in ('assistant','mail_drafting','document_drafting','hearing','word_revision'):
        return ('style','structure')
    return ()


def learning_context(desk, matter='', purpose='assistant', limit=8, record=True):
    """Return a small safe profile that can be inserted into a model payload."""
    ensure_schema(desk);purpose=str(purpose or 'assistant')
    if purpose not in PURPOSES:raise Stop('fonction_apprentissage_invalide')
    matter=str(matter or '')
    if matter and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',matter):raise Stop('dossier_invalide')
    kinds=_allowed_kinds(purpose);limit=max(1,min(int(limit),20))
    corrections=[]
    if kinds:
        marks=','.join('?' for _ in kinds)
        rows=desk.db.execute('''SELECT id,scope,matter,source_kind,guidance,created
          FROM correction_examples_v370 WHERE enabled=1
          AND source_kind IN ('''+marks+''')
          AND (scope='general' OR (scope='matter' AND matter=?))
          ORDER BY CASE scope WHEN 'matter' THEN 0 ELSE 1 END,id DESC LIMIT ?''',
          (*kinds,matter,limit)).fetchall()
        corrections=[{'id':r['id'],'scope':r['scope'],'kind':r['source_kind'],
          'guidance':r['guidance']} for r in rows]
    templates=[]
    if purpose in ('document_drafting','word_revision','hearing'):
        rows=desk.db.execute("""SELECT id,label,version,sha256,placeholders
          FROM cabinet_templates_v330 WHERE status='approved'
          ORDER BY label,version DESC LIMIT 12""").fetchall()
        templates=[{'id':r['id'],'label':r['label'],'version':r['version'],
          'sha256':r['sha256'],'placeholders':_json(r['placeholders'],[])} for r in rows]
    profile=desk.settings('cabinet:profile',{})
    if not isinstance(profile,dict):profile={}
    from .learning410 import applicable_context
    advanced=applicable_context(desk,matter,purpose,limit=max(limit,20),record=record)
    result={'purpose':purpose,'corrections':corrections,'approved_templates':templates,
      'approved_business_rules':advanced['rules'],
      'trusted_documents':advanced['trusted_documents'],
      'cabinet_style':{'name':str(profile.get('name') or ''),
        'style':str(profile.get('style') or ''),'guidance':str(profile.get('guidance') or '')},
      'limits':['Les anciennes corrections 3.9 restent des préférences de forme.',
        'Les règles 4.1 sont explicitement approuvées, versionnées et réversibles.',
        'Ne jamais généraliser un fait, un montant ou une source de droit.']+advanced['limits']}
    if record and (corrections or templates or any(result['cabinet_style'].values())):
        correction_ids=[x['id'] for x in corrections];template_ids=[x['id'] for x in templates]
        fingerprint=digest(json.dumps({'purpose':purpose,'matter':matter,
          'corrections':correction_ids,'templates':template_ids,
          'profile':result['cabinet_style']},ensure_ascii=False,sort_keys=True))
        cur=desk.db.execute('''INSERT INTO learning_uses_v392
          (purpose,matter,correction_ids,template_ids,fingerprint,created)
          VALUES(?,?,?,?,?,?)''',(purpose,matter,json.dumps(correction_ids),
          json.dumps(template_ids),fingerprint,_stamp()))
        desk.db.commit();result['usage_id']=cur.lastrowid
    return result


def set_correction_state(desk, correction_id, enabled):
    ensure_schema(desk)
    try:correction_id=int(correction_id)
    except (TypeError,ValueError):raise Stop('correction_absente') from None
    row=desk.db.execute('SELECT id FROM correction_examples_v370 WHERE id=?',(correction_id,)).fetchone()
    if not row:raise Stop('correction_absente')
    value=1 if str(enabled).lower() in ('1','yes','true','on') else 0
    desk.db.execute('UPDATE correction_examples_v370 SET enabled=? WHERE id=?',(value,correction_id))
    desk.db.commit();desk.audit('apprentissage_metier_modifie',{
      'correction_id':correction_id,'enabled':bool(value)})
    return {'correction_id':correction_id,'enabled':bool(value)}


def record_outcome(desk, output_id, decision, correction_id=None):
    ensure_schema(desk);output_id=str(output_id or '')
    if not output_id or decision not in ('accepted','modified','rejected'):
        raise Stop('decision_livrable_invalide')
    stamp=_stamp();old=desk.db.execute(
      'SELECT created FROM learning_outcomes_v392 WHERE output_id=?',(output_id,)).fetchone()
    desk.db.execute('INSERT OR REPLACE INTO learning_outcomes_v392 VALUES(?,?,?,?,?)',
      (output_id,decision,correction_id,old['created'] if old else stamp,stamp))
    desk.db.commit()


def snapshot(desk, days=30):
    ensure_schema(desk);days=max(1,min(int(days),365))
    since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    corrections=[dict(x) for x in desk.db.execute('''SELECT id,scope,matter,source_kind,
      guidance,enabled,created FROM correction_examples_v370 ORDER BY id DESC LIMIT 100''')]
    templates=[dict(x) for x in desk.db.execute("""SELECT id,label,version,sha256,
      placeholders,approved FROM cabinet_templates_v330 WHERE status='approved'
      ORDER BY label,version DESC""")]
    for item in templates:item['placeholders']=_json(item['placeholders'],[])
    uses=[dict(x) for x in desk.db.execute('''SELECT purpose,COUNT(*) uses,
      COUNT(DISTINCT matter) matters,MAX(created) last_used FROM learning_uses_v392
      WHERE created>=? GROUP BY purpose ORDER BY uses DESC''',(since,))]
    outcomes={r['decision']:r['count'] for r in desk.db.execute('''SELECT decision,
      COUNT(*) count FROM learning_outcomes_v392 WHERE updated>=? GROUP BY decision''',(since,))}
    reviewed=sum(outcomes.values());useful=outcomes.get('accepted',0)+outcomes.get('modified',0)
    return {'period_days':days,'corrections':corrections,'templates':templates,'uses':uses,
      'outcomes':outcomes,'active_corrections':sum(bool(x['enabled']) for x in corrections),
      'approved_templates':len(templates),'reviewed_outputs':reviewed,
      'useful_rate_percent':round(100*useful/reviewed) if reviewed else 0,
      'guardrails':{'facts_are_never_learned':True,'law_is_never_learned':True,
        'rules_are_reversible':True,'content_of_external_prompts_is_not_logged':True}}
