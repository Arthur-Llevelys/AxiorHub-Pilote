"""Relevance, correction learning and bounded autonomy controls for AxiorHub 3.7.

The module deliberately learns only user-approved working preferences.  It never
turns a correction into a legal fact, and automation profiles never authorize an
email send, filing, signature, payment or deletion.
"""
from datetime import datetime, timezone
import json
import re
import sqlite3
from pathlib import Path

from .common import Stop, fold


AUTOMATION_LEVELS = {
    'manual': {
        'label': 'Manuel',
        'description': 'L’agent observe et répond uniquement à une demande explicite.',
        'settings': {
            'proactive_enabled': False, 'autonomy_enabled': False,
            'orchestrator_enabled': False, 'automatic_mail_drafts_enabled': False,
            'automatic_legal_projects_enabled': False,
        },
    },
    'assisted': {
        'label': 'Assisté',
        'description': 'L’agent propose et prépare sur demande ; l’avocat déclenche chaque production.',
        'settings': {
            'proactive_enabled': True, 'autonomy_enabled': False,
            'orchestrator_enabled': False, 'automatic_mail_drafts_enabled': False,
            'automatic_legal_projects_enabled': False,
        },
    },
    'proactive': {
        'label': 'Proactif',
        'description': 'L’agent surveille, explique et prépare des propositions internes à relire.',
        'settings': {
            'proactive_enabled': True, 'autonomy_enabled': True,
            'orchestrator_enabled': True, 'automatic_mail_drafts_enabled': False,
            'automatic_legal_projects_enabled': False,
        },
    },
    'autonomous_drafts': {
        'label': 'Autonome — brouillons',
        'description': 'L’agent prépare automatiquement des brouillons et projets, sans envoi ni dépôt externe.',
        'settings': {
            'proactive_enabled': True, 'autonomy_enabled': True,
            'orchestrator_enabled': True, 'automatic_mail_drafts_enabled': True,
            'automatic_legal_projects_enabled': True,
        },
    },
}

RULE_ACTIONS = {'ignore', 'review', 'priority'}
RULE_FIELDS = {'sender', 'domain', 'subject', 'recipient'}
CONTROLLED_JOBS = {
    'assistant_answer', 'analyze_strategy', 'draft_act', 'prepare_document_project',
    'prepare_hearing', 'prepare_word_project', 'coach_hearing35',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS mail_rules_v370(
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, priority INTEGER NOT NULL,
      field TEXT NOT NULL, operator TEXT NOT NULL, value TEXT NOT NULL,
      action TEXT NOT NULL, enabled INTEGER NOT NULL, created TEXT NOT NULL,
      updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS mail_rules_v370_order
      ON mail_rules_v370(enabled,priority,id);
    CREATE TABLE IF NOT EXISTS correction_examples_v370(
      id INTEGER PRIMARY KEY, scope TEXT NOT NULL, matter TEXT NOT NULL,
      source_kind TEXT NOT NULL, original TEXT NOT NULL, corrected TEXT NOT NULL,
      guidance TEXT NOT NULL, enabled INTEGER NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS quality_reviews_v370(
      id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, job_kind TEXT NOT NULL,
      primary_model TEXT NOT NULL, control_model TEXT NOT NULL,
      status TEXT NOT NULL, recommendation TEXT NOT NULL, result TEXT NOT NULL,
      created TEXT NOT NULL, UNIQUE(job_id));
    ''')
    desk.db.commit()


def list_rules(desk):
    ensure_schema(desk)
    return [dict(row) for row in desk.db.execute(
        'SELECT * FROM mail_rules_v370 ORDER BY priority,id')]


def save_rule(desk, name, priority, field, operator, value, action, enabled=True):
    name = str(name or '').strip()
    field = str(field or '').strip()
    operator = str(operator or '').strip()
    value = str(value or '').strip()
    action = str(action or '').strip()
    if not name or len(name) > 120 or field not in RULE_FIELDS:
        raise Stop('regle_courriel_invalide')
    if operator not in {'equals', 'contains', 'ends_with'} or action not in RULE_ACTIONS:
        raise Stop('regle_courriel_invalide')
    if not value or len(value) > 300 or any(x in value for x in ('\x00', '\r', '\n')):
        raise Stop('valeur_regle_courriel_invalide')
    try:
        priority = max(1, min(int(priority), 999))
    except (TypeError, ValueError):
        raise Stop('priorite_regle_invalide') from None
    stamp = _now()
    cur = desk.db.execute('''INSERT INTO mail_rules_v370
      (name,priority,field,operator,value,action,enabled,created,updated)
      VALUES (?,?,?,?,?,?,?,?,?)''',
      (name, priority, field, operator, value, action, int(bool(enabled)), stamp, stamp))
    desk.db.commit()
    desk.audit('regle_courriel_creee', {'rule_id': cur.lastrowid, 'action': action,
                                        'field': field, 'operator': operator})
    return {'id': cur.lastrowid, 'saved': True}


def delete_rule(desk, rule_id, confirm):
    if confirm != 'yes':
        raise Stop('confirmation_suppression_regle_requise')
    try: rule_id = int(rule_id)
    except (TypeError, ValueError): raise Stop('regle_courriel_absente') from None
    row = desk.db.execute('SELECT name FROM mail_rules_v370 WHERE id=?', (rule_id,)).fetchone()
    if not row: raise Stop('regle_courriel_absente')
    desk.db.execute('DELETE FROM mail_rules_v370 WHERE id=?', (rule_id,)); desk.db.commit()
    desk.audit('regle_courriel_supprimee', {'rule_id': rule_id, 'name': row[0]})
    return {'id': rule_id, 'deleted': True}


def evaluate_mail(desk, mail):
    """Return the first matching rule without executing an external action."""
    ensure_schema(desk)
    sender = str(getattr(mail, 'sender', '') or '')
    subject = str(getattr(mail, 'subject', '') or '')
    msg = getattr(mail, 'msg', {})
    recipient = str(msg.get('To', '') if msg else '')
    domain = sender.rsplit('@', 1)[-1] if '@' in sender else ''
    values = {'sender': sender, 'domain': domain, 'subject': subject, 'recipient': recipient}
    for row in desk.db.execute('SELECT * FROM mail_rules_v370 WHERE enabled=1 ORDER BY priority,id'):
        actual, expected = fold(values.get(row['field'], '')), fold(row['value'])
        matched = (actual == expected if row['operator'] == 'equals' else
                   expected in actual if row['operator'] == 'contains' else
                   actual.endswith(expected))
        if matched:
            return {'id': row['id'], 'name': row['name'], 'action': row['action']}
    return None


def evaluate_mail_config(config, mail):
    """Read enabled rules from the existing database without constructing a Desk."""
    path = Path(config.get('state_dir', '')) / 'desk.sqlite3'
    if not path.is_file(): return None
    try:
        db = sqlite3.connect(path, timeout=2); db.row_factory = sqlite3.Row
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_rules_v370'").fetchone()
        if not exists: return None
        sender = str(getattr(mail, 'sender', '') or '')
        subject = str(getattr(mail, 'subject', '') or '')
        msg = getattr(mail, 'msg', {})
        recipient = str(msg.get('To', '') if msg else '')
        domain = sender.rsplit('@', 1)[-1] if '@' in sender else ''
        values = {'sender': sender, 'domain': domain, 'subject': subject, 'recipient': recipient}
        for row in db.execute('SELECT * FROM mail_rules_v370 WHERE enabled=1 ORDER BY priority,id'):
            actual, expected = fold(values.get(row['field'], '')), fold(row['value'])
            matched = (actual == expected if row['operator'] == 'equals' else
                       expected in actual if row['operator'] == 'contains' else actual.endswith(expected))
            if matched: return {'id': row['id'], 'name': row['name'], 'action': row['action']}
    except sqlite3.Error:
        return None
    finally:
        try: db.close()
        except (UnboundLocalError, sqlite3.Error): pass
    return None


def record_correction(desk, scope, matter, source_kind, original, corrected, guidance):
    scope = str(scope or '').strip()
    matter = str(matter or '').strip()
    source_kind = str(source_kind or '').strip()
    original = str(original or '').strip()
    corrected = str(corrected or '').strip()
    guidance = str(guidance or '').strip()
    if scope not in {'general', 'matter', 'mail', 'document'}:
        raise Stop('portee_correction_invalide')
    if scope == 'matter' and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', matter):
        raise Stop('dossier_correction_requis')
    if source_kind not in {'style', 'classification', 'association', 'structure'}:
        raise Stop('type_correction_invalide')
    if not original or not corrected or len(original) > 12000 or len(corrected) > 12000:
        raise Stop('correction_invalide')
    if not guidance or len(guidance) > 1500:
        raise Stop('regle_apprentissage_requise')
    cur = desk.db.execute('''INSERT INTO correction_examples_v370
      (scope,matter,source_kind,original,corrected,guidance,enabled,created)
      VALUES (?,?,?,?,?,?,1,?)''',
      (scope, matter if scope == 'matter' else '', source_kind, original, corrected,
       guidance, _now()))
    desk.db.commit()
    desk.audit('correction_approuvee', {'correction_id': cur.lastrowid, 'scope': scope,
                                        'matter': matter if scope == 'matter' else '',
                                        'source_kind': source_kind})
    return {'id': cur.lastrowid, 'saved': True}


def correction_guidance(desk, matter='', limit=8):
    ensure_schema(desk)
    rows = desk.db.execute('''SELECT scope,matter,source_kind,guidance FROM correction_examples_v370
      WHERE enabled=1 AND (scope='general' OR (scope='matter' AND matter=?))
      ORDER BY id DESC LIMIT ?''', (str(matter or ''), max(1, min(int(limit), 20)))).fetchall()
    return [{'scope': r['scope'], 'kind': r['source_kind'], 'guidance': r['guidance']} for r in rows]


def set_automation_level(desk, level):
    level = str(level or '').strip()
    profile = AUTOMATION_LEVELS.get(level)
    if not profile: raise Stop('niveau_automatisation_invalide')
    for key, value in profile['settings'].items():
        desk.setting('automation:' + key, value)
    desk.setting('automation:level', level)
    desk.audit('niveau_automatisation_modifie', {
        'level': level, 'external_actions_authorized': False,
        'email_send_authorized': False, 'filing_authorized': False})
    return {'level': level, 'label': profile['label'], 'settings': profile['settings'],
            'external_actions_authorized': False}


def quality_snapshot(desk):
    ensure_schema(desk)
    jobs = {r[0]: r[1] for r in desk.db.execute(
        'SELECT status,COUNT(*) FROM jobs GROUP BY status')}
    reviews = {r[0]: r[1] for r in desk.db.execute(
        'SELECT recommendation,COUNT(*) FROM quality_reviews_v370 GROUP BY recommendation')}
    corrections = desk.db.execute(
        'SELECT COUNT(*) FROM correction_examples_v370 WHERE enabled=1').fetchone()[0]
    rules = desk.db.execute(
        'SELECT COUNT(*) FROM mail_rules_v370 WHERE enabled=1').fetchone()[0]
    return {'jobs': jobs, 'control_reviews': reviews, 'active_corrections': corrections,
            'active_mail_rules': rules,
            'automation_level': desk.settings('automation:level', 'assisted'),
            'second_model_control': bool(desk.settings('automation:second_model_control_enabled', True))}


def review_result(desk, job_id, job_kind, result):
    """Ask the configured control route for an independent, non-executing review."""
    ensure_schema(desk)
    if job_kind not in CONTROLLED_JOBS:
        return None
    if not desk.settings('automation:second_model_control_enabled', True):
        return None
    from . import economie569   # 5.6.9 : analyses internes dispensées en régime économe (actes et documents toujours contrôlés)
    if not economie569.control_required(desk, job_kind):
        return None
    existing = desk.db.execute('SELECT result FROM quality_reviews_v370 WHERE job_id=?',
                               (int(job_id),)).fetchone()
    if existing:
        return json.loads(existing[0])
    from .model import Model, routed_config
    primary = routed_config(desk.c, {
        'assistant_answer': 'assistant', 'analyze_strategy': 'legal_analysis',
        'draft_act': 'document_drafting', 'prepare_document_project': 'document_drafting',
        'prepare_hearing': 'hearing', 'prepare_word_project': 'document_drafting',
        'coach_hearing35': 'hearing',
    }[job_kind])
    control = routed_config(desk.c, 'control')
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True)
    # In hybrid mode the control route may deliberately select OpenRouter even
    # though its safe base configuration remains Ollama. Compare the effective
    # decision, not merely the local base route.
    from .hybrid400 import choose as hybrid_choice
    control_decision = hybrid_choice(desk.c, 'control', 'second_model_review',
      payload={'result': raw[:24000]}, max_tokens=3500)
    control_provider = (control_decision['provider'] if control_decision['external']
                        else control.get('provider_id'))
    control_model = (control_decision['model'] if control_decision['external']
                     else control.get('model'))
    same = (primary.get('provider_id'), primary.get('model')) == (control_provider, control_model)
    if same:
        review = {'status': 'not_independent', 'recommendation': 'unavailable',
                  'agree': False, 'unsupported_claims': [], 'omissions': [],
                  'risks': ['Le modèle de contrôle est identique au modèle principal.'],
                  'blocking_reasons': ['Configurer un second modèle distinct.']}
    else:
        review = Model(control).ask('second_model_review', {
            'job_kind': job_kind, 'primary_provider': primary.get('provider_id', 'ollama'),
            'primary_model': primary.get('model', ''), 'result': raw[:24000],
            'limits': 'Contrôle interne seulement. Ne pas exécuter, envoyer, déposer ou modifier.'})
        review['status'] = 'completed'
    recommendation = review.get('recommendation', 'unavailable')
    desk.db.execute('''INSERT OR REPLACE INTO quality_reviews_v370
      (job_id,job_kind,primary_model,control_model,status,recommendation,result,created)
      VALUES (?,?,?,?,?,?,?,?)''',
      (int(job_id), job_kind, str(primary.get('model', '')), str(control_model or ''),
       review.get('status', 'completed'), recommendation,
       json.dumps(review, ensure_ascii=False), _now()))
    desk.db.commit()
    desk.audit('controle_second_modele', {'job_id': int(job_id), 'kind': job_kind,
      'recommendation': recommendation, 'independent': not same,
      'control_provider': str(control_provider or ''), 'control_model': str(control_model or '')})
    return review
