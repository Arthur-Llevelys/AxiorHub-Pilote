"""Apprentissage métier explicite, versionné et réversible pour AxiorHub 4.1.

Ce module ne réentraîne aucun modèle. Il transforme uniquement une décision
explicite de l'avocat en consigne bornée, dont la portée et chaque utilisation
sont auditables.
"""
from datetime import datetime, timezone, timedelta
import difflib
import json
import re

from .common import Stop, digest, load_matters


SCOPES = {'cabinet', 'matter_type', 'client', 'matter'}
PURPOSES = {'all', 'assistant', 'mail_drafting', 'mail_triage',
            'document_drafting', 'hearing', 'word_revision', 'control'}
RULE_TYPES = {'style', 'structure', 'recipient', 'subject', 'legal_position',
              'word_template', 'trusted_source', 'prohibited_claim', 'other'}
DECISIONS = {'accepted', 'modified', 'rejected'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS business_rules_v410(
      id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_value TEXT NOT NULL,
      purpose TEXT NOT NULL, rule_type TEXT NOT NULL, instruction TEXT NOT NULL,
      status TEXT NOT NULL, origin TEXT NOT NULL, origin_ref TEXT NOT NULL,
      version INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS business_rules_scope_v410
      ON business_rules_v410(status,scope,scope_value,purpose);
    CREATE TABLE IF NOT EXISTS correction_events_v410(
      id INTEGER PRIMARY KEY, output_id TEXT NOT NULL, matter TEXT NOT NULL,
      purpose TEXT NOT NULL, decision TEXT NOT NULL, original_text TEXT NOT NULL,
      corrected_text TEXT NOT NULL, added_paragraphs TEXT NOT NULL,
      deleted_paragraphs TEXT NOT NULL, rejection_reason TEXT NOT NULL,
      original_sha256 TEXT NOT NULL, corrected_sha256 TEXT NOT NULL,
      changed_characters INTEGER NOT NULL, rule_id INTEGER,
      model_provider TEXT NOT NULL DEFAULT '', model_name TEXT NOT NULL DEFAULT '',
      created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS correction_events_created_v410
      ON correction_events_v410(created,decision,purpose);
    CREATE TABLE IF NOT EXISTS trusted_documents_v410(
      id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_value TEXT NOT NULL,
      path TEXT NOT NULL, sha256 TEXT NOT NULL, trust_level TEXT NOT NULL,
      reason TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
      updated TEXT NOT NULL, UNIQUE(scope,scope_value,path,sha256));
    CREATE TABLE IF NOT EXISTS business_rule_uses_v410(
      id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL, purpose TEXT NOT NULL,
      matter_hash TEXT NOT NULL, created TEXT NOT NULL);
    ''')
    columns={row[1] for row in desk.db.execute('PRAGMA table_info(correction_events_v410)')}
    if 'model_provider' not in columns:
        desk.db.execute("ALTER TABLE correction_events_v410 ADD COLUMN model_provider TEXT NOT NULL DEFAULT ''")
    if 'model_name' not in columns:
        desk.db.execute("ALTER TABLE correction_events_v410 ADD COLUMN model_name TEXT NOT NULL DEFAULT ''")
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value, maximum):
    value = str(value or '').strip()
    if not value or len(value) > maximum or '\x00' in value:
        raise Stop('regle_metier_invalide')
    return value


def save_rule(desk, scope, scope_value, purpose, rule_type, instruction,
              origin='manual', origin_ref=''):
    ensure_schema(desk)
    scope = str(scope or '').strip(); purpose = str(purpose or '').strip()
    rule_type = str(rule_type or '').strip(); scope_value = str(scope_value or '').strip()
    instruction = _clean(instruction, 2000)
    if scope not in SCOPES or purpose not in PURPOSES or rule_type not in RULE_TYPES:
        raise Stop('regle_metier_invalide')
    if scope == 'cabinet':
        scope_value = ''
    elif not scope_value or len(scope_value) > 240:
        raise Stop('valeur_portee_regle_requise')
    if scope == 'matter' and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', scope_value):
        raise Stop('dossier_invalide')
    stamp = _now()
    previous = desk.db.execute('''SELECT id,version FROM business_rules_v410
      WHERE scope=? AND scope_value=? AND purpose=? AND rule_type=?
      AND instruction=? ORDER BY version DESC LIMIT 1''',
      (scope, scope_value, purpose, rule_type, instruction)).fetchone()
    if previous:
        desk.db.execute("UPDATE business_rules_v410 SET status='active',updated=? WHERE id=?",
                        (stamp, previous['id']))
        desk.db.commit()
        return {'id': previous['id'], 'version': previous['version'], 'saved': True}
    version = 1 + int(desk.db.execute('''SELECT COALESCE(MAX(version),0)
      FROM business_rules_v410 WHERE scope=? AND scope_value=? AND purpose=?
      AND rule_type=?''', (scope, scope_value, purpose, rule_type)).fetchone()[0])
    cur = desk.db.execute('''INSERT INTO business_rules_v410
      (scope,scope_value,purpose,rule_type,instruction,status,origin,origin_ref,
       version,created,updated) VALUES(?,?,?,?,?,'active',?,?,?,?,?)''',
      (scope, scope_value, purpose, rule_type, instruction, str(origin)[:40],
       str(origin_ref)[:160], version, stamp, stamp))
    desk.db.commit()
    desk.audit('regle_metier_410_creee', {'rule_id': cur.lastrowid, 'scope': scope,
      'scope_value_hash': digest(scope_value)[:16] if scope_value else '',
      'purpose': purpose, 'rule_type': rule_type, 'version': version})
    return {'id': cur.lastrowid, 'version': version, 'saved': True}


def set_rule_status(desk, rule_id, status):
    ensure_schema(desk)
    try: rule_id = int(rule_id)
    except (TypeError, ValueError): raise Stop('regle_metier_absente') from None
    if status not in ('active', 'paused', 'archived'):
        raise Stop('etat_regle_metier_invalide')
    row = desk.db.execute('SELECT id FROM business_rules_v410 WHERE id=?', (rule_id,)).fetchone()
    if not row: raise Stop('regle_metier_absente')
    desk.db.execute('UPDATE business_rules_v410 SET status=?,updated=? WHERE id=?',
                    (status, _now(), rule_id)); desk.db.commit()
    desk.audit('regle_metier_410_etat', {'rule_id': rule_id, 'status': status})
    return {'id': rule_id, 'status': status}


def _paragraph_diff(original, corrected):
    before = [x.strip() for x in re.split(r'\n\s*\n', original) if x.strip()]
    after = [x.strip() for x in re.split(r'\n\s*\n', corrected) if x.strip()]
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    added, deleted = [], []
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op in ('delete', 'replace'): deleted.extend(before[a1:a2])
        if op in ('insert', 'replace'): added.extend(after[b1:b2])
    return added[:80], deleted[:80]


def record_review(desk, output_id, matter, purpose, decision, original='', corrected='',
                  rejection_reason='', rule=None):
    ensure_schema(desk)
    if decision not in DECISIONS: raise Stop('decision_livrable_invalide')
    original = str(original or '')[:12000]; corrected = str(corrected or '')[:12000]
    rejection_reason = str(rejection_reason or '')[:3000]
    added, deleted = _paragraph_diff(original, corrected) if decision == 'modified' else ([], [])
    rule_id = None
    if rule and decision == 'modified':
        saved = save_rule(desk, rule.get('scope'), rule.get('scope_value'),
          rule.get('purpose') or purpose, rule.get('rule_type'), rule.get('instruction'),
          'correction', output_id)
        rule_id = saved['id']
    changed = sum(len(x) for x in added) + sum(len(x) for x in deleted)
    provenance=desk.db.execute('''SELECT f.model_provider,f.model_name
      FROM production_outputs_v390 o LEFT JOIN production_flows_v391 f ON f.job_id=o.job_id
      WHERE o.id=?''',(str(output_id),)).fetchone()
    provider=str(provenance['model_provider'] or '') if provenance else ''
    model=str(provenance['model_name'] or '') if provenance else ''
    cur = desk.db.execute('''INSERT INTO correction_events_v410
      (output_id,matter,purpose,decision,original_text,corrected_text,
       added_paragraphs,deleted_paragraphs,rejection_reason,original_sha256,
       corrected_sha256,changed_characters,rule_id,model_provider,model_name,created)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (str(output_id), str(matter or ''), str(purpose or ''), decision, original,
       corrected, json.dumps(added, ensure_ascii=False),
       json.dumps(deleted, ensure_ascii=False), rejection_reason, digest(original),
       digest(corrected), changed, rule_id, provider, model, _now()))
    desk.db.commit()
    desk.audit('correction_metier_410_enregistree', {'correction_event_id': cur.lastrowid,
      'decision': decision, 'changed_characters': changed, 'rule_id': rule_id})
    return {'event_id': cur.lastrowid, 'rule_id': rule_id,
            'added_paragraphs': len(added), 'deleted_paragraphs': len(deleted)}


def trust_document(desk, scope, scope_value, path, sha256, trust_level, reason):
    ensure_schema(desk); scope = str(scope or '')
    if scope not in SCOPES or scope == 'matter_type': raise Stop('portee_document_fiable_invalide')
    if scope == 'cabinet': scope_value = ''
    elif not str(scope_value or '').strip(): raise Stop('valeur_portee_regle_requise')
    path = _clean(path, 2000); reason = _clean(reason, 1000)
    sha256 = str(sha256 or '').lower().strip()
    if not re.fullmatch(r'[a-f0-9]{64}', sha256): raise Stop('empreinte_document_invalide')
    if trust_level not in ('reference', 'approved', 'authoritative'):
        raise Stop('niveau_fiabilite_invalide')
    stamp = _now()
    desk.db.execute('''INSERT INTO trusted_documents_v410
      (scope,scope_value,path,sha256,trust_level,reason,status,created,updated)
      VALUES(?,?,?,?,?,?,'active',?,?) ON CONFLICT(scope,scope_value,path,sha256)
      DO UPDATE SET trust_level=excluded.trust_level,reason=excluded.reason,
      status='active',updated=excluded.updated''',
      (scope, str(scope_value or ''), path, sha256, trust_level, reason, stamp, stamp))
    desk.db.commit(); desk.audit('document_fiable_410', {'scope': scope,
      'scope_value_hash': digest(str(scope_value or ''))[:16], 'sha256': sha256})
    return {'saved': True, 'sha256': sha256}


def _matter_context(desk, matter):
    found = next((m for m in load_matters(desk.c) if m.get('id') == matter), {})
    return {'matter': str(matter or ''), 'client': str(found.get('client_name') or ''),
            'matter_type': str(found.get('matter_type') or found.get('type') or '')}


def applicable_context(desk, matter='', purpose='assistant', limit=20, record=True):
    ensure_schema(desk); purpose = purpose if purpose in PURPOSES else 'assistant'
    ctx = _matter_context(desk, str(matter or ''))
    rows = []
    for row in desk.db.execute("SELECT * FROM business_rules_v410 WHERE status='active' AND purpose IN ('all',?) ORDER BY CASE scope WHEN 'matter' THEN 0 WHEN 'client' THEN 1 WHEN 'matter_type' THEN 2 ELSE 3 END,id DESC", (purpose,)):
        if row['scope'] == 'cabinet' or (row['scope'] == 'matter' and row['scope_value'] == ctx['matter']) or (row['scope'] == 'client' and row['scope_value'] == ctx['client']) or (row['scope'] == 'matter_type' and row['scope_value'] == ctx['matter_type']):
            rows.append(dict(row))
        if len(rows) >= max(1, min(int(limit), 40)): break
    trusted = [dict(x) for x in desk.db.execute("SELECT scope,scope_value,path,sha256,trust_level,reason FROM trusted_documents_v410 WHERE status='active' AND (scope='cabinet' OR (scope='matter' AND scope_value=?) OR (scope='client' AND scope_value=?)) ORDER BY id DESC LIMIT 30", (ctx['matter'], ctx['client']))]
    if record:
        matter_hash = digest(ctx['matter'])[:16] if ctx['matter'] else ''
        for row in rows:
            desk.db.execute('INSERT INTO business_rule_uses_v410(rule_id,purpose,matter_hash,created) VALUES(?,?,?,?)',
                            (row['id'], purpose, matter_hash, _now()))
        if rows: desk.db.commit()
    return {'rules': [{'id': x['id'], 'scope': x['scope'], 'purpose': x['purpose'],
      'type': x['rule_type'], 'instruction': x['instruction'], 'version': x['version']} for x in rows],
      'trusted_documents': trusted,
      'limits': ['Consignes approuvées par l’avocat, pas des faits ni des sources de droit.',
                 'En cas de conflit, la règle la plus spécifique prévaut : dossier, client, type, puis cabinet.',
                 'La confiance d’un document exige toujours la vérification de son empreinte.']}


def _rule_insights(desk, rules, since):
    """Measured, explainable signals. They never change a rule by themselves."""
    try:
        influence={row['rule_id']:dict(row) for row in desk.db.execute('''SELECT rule_id,
          COUNT(*) influenced,MAX(updated) last_influence,
          SUM(CASE WHEN decision='accepted' THEN 1 ELSE 0 END) accepted,
          SUM(CASE WHEN decision='modified' THEN 1 ELSE 0 END) modified,
          SUM(CASE WHEN decision='rejected' THEN 1 ELSE 0 END) rejected,
          SUM(changed_characters) changed_characters
          FROM production_rule_influence_v420 WHERE updated>=? GROUP BY rule_id''',(since,))}
    except Exception:
        influence={}
    uses={row['rule_id']:dict(row) for row in desk.db.execute('''SELECT rule_id,
      COUNT(*) uses,MAX(created) last_used FROM business_rule_uses_v410
      WHERE created>=? GROUP BY rule_id''',(since,))}
    active=[x for x in rules if x['status']=='active']
    negations=(' ne ',' jamais ',' interdit ',' exclure ',' sans ',' pas ')
    for rule in rules:
        stats=influence.get(rule['id'],{});usage=uses.get(rule['id'],{})
        decided=sum(int(stats.get(x) or 0) for x in ('accepted','modified','rejected'))
        rule['uses']=int(usage.get('uses') or 0)
        rule['last_used']=str(usage.get('last_used') or stats.get('last_influence') or '')
        rule['influenced_productions']=int(stats.get('influenced') or 0)
        rule['acceptance_percent']=round(100*int(stats.get('accepted') or 0)/decided,1) if decided else None
        rule['average_changed_characters']=round(int(stats.get('changed_characters') or 0)/max(1,int(stats.get('modified') or 0)),1)
        text=' '+re.sub(r'\s+',' ',rule['instruction'].casefold())+' '
        words={x for x in re.findall(r'[a-zà-ÿ]{4,}',text) if x not in {'toujours','jamais','avec','pour','dans','cette'}}
        polarity=any(x in text for x in negations);conflicts=[]
        for other in active:
            if other['id']==rule['id'] or other['scope']!=rule['scope'] or other['scope_value']!=rule['scope_value']:continue
            if other['purpose'] not in (rule['purpose'],'all') and rule['purpose']!='all':continue
            other_text=' '+re.sub(r'\s+',' ',other['instruction'].casefold())+' '
            other_words={x for x in re.findall(r'[a-zà-ÿ]{4,}',other_text)}
            overlap=len(words & other_words)/max(1,len(words | other_words))
            if overlap>=.25 and polarity != any(x in other_text for x in negations):conflicts.append(other['id'])
        rule['possible_conflicts']=sorted(set(conflicts))
        if decided>=5 and (rule['acceptance_percent'] or 0)<20:
            rule['recommendation']='À réexaminer : faible acceptation mesurée. Aucune désactivation automatique.'
        elif rule['possible_conflicts']:rule['recommendation']='Contradiction potentielle à arbitrer.'
        else:rule['recommendation']='Conserver' if decided else 'Données insuffisantes'
        params=[rule['purpose']];where="purpose IN (?, 'all')"
        if rule['scope']=='matter':where+=' AND matter=?';params.append(rule['scope_value'])
        rule['simulation']=[dict(x) for x in desk.db.execute('''SELECT id,output_id,matter,
          decision,substr(original_text,1,180) original,substr(corrected_text,1,180) corrected
          FROM correction_events_v410 WHERE '''+where+' ORDER BY created DESC LIMIT 3',params)]
    return rules


def snapshot(desk, days=30):
    ensure_schema(desk); since = (datetime.now(timezone.utc)-timedelta(days=max(1,min(int(days),365)))).isoformat()
    rules = [dict(x) for x in desk.db.execute('SELECT * FROM business_rules_v410 ORDER BY id DESC LIMIT 200')]
    _rule_insights(desk,rules,since)
    corrections = [dict(x) for x in desk.db.execute('''SELECT id,output_id,matter,purpose,decision,
      added_paragraphs,deleted_paragraphs,rejection_reason,changed_characters,rule_id,
      model_provider,model_name,created
      FROM correction_events_v410 WHERE created>=? ORDER BY id DESC LIMIT 200''', (since,))]
    for item in corrections:
        item['added_paragraphs'] = len(json.loads(item['added_paragraphs'] or '[]'))
        item['deleted_paragraphs'] = len(json.loads(item['deleted_paragraphs'] or '[]'))
    outcomes = {x['decision']: x['count'] for x in desk.db.execute('SELECT decision,COUNT(*) count FROM correction_events_v410 WHERE created>=? GROUP BY decision', (since,))}
    uses = desk.db.execute('SELECT COUNT(*) FROM business_rule_uses_v410 WHERE created>=?', (since,)).fetchone()[0]
    trusted = [dict(x) for x in desk.db.execute("SELECT * FROM trusted_documents_v410 WHERE status='active' ORDER BY id DESC LIMIT 100")]
    corrections_by_model=[dict(x) for x in desk.db.execute('''SELECT model_provider provider,
      model_name model,COUNT(*) reviews,SUM(changed_characters) changed_characters,
      SUM(CASE WHEN decision='accepted' THEN 1 ELSE 0 END) accepted,
      SUM(CASE WHEN decision='modified' THEN 1 ELSE 0 END) modified,
      SUM(CASE WHEN decision='rejected' THEN 1 ELSE 0 END) rejected
      FROM correction_events_v410 WHERE created>=? AND model_name<>''
      GROUP BY model_provider,model_name ORDER BY changed_characters,modified,rejected''',(since,))]
    suggestions=[x for x in corrections if x['decision']=='modified' and not x['rule_id']][:20]
    return {'period_days': days, 'rules': rules, 'corrections': corrections,
      'active_rules': sum(x['status']=='active' for x in rules),
      'correction_volume': sum(outcomes.values()), 'changed_characters': sum(x['changed_characters'] for x in corrections),
      'outcomes': outcomes, 'rule_uses': uses, 'trusted_documents': trusted,
      'corrections_by_model':corrections_by_model,
      'rule_suggestions':suggestions,
      'possible_conflicts':sum(bool(x['possible_conflicts']) for x in rules),
      'direct_model_training': False, 'rules_are_reversible': True}
