"""Routage hybride local-first, explicable et contrôlé pour AxiorHub 4.0.

Le routeur ne journalise jamais les prompts. Toute sortie du serveur est précédée
des barrières dossier/fonction/consentement/ZDR/anonymisation/budget. Les aperçus
sont anonymisés, temporaires et supprimés après lecture.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop, fold, load_matters

DEFAULT_ALLOWED = ('assistant', 'legal_analysis', 'hearing', 'document_drafting', 'control')
BASE_SCORES = {'mail_triage': 5, 'attachment_review': 18, 'mail_drafting': 28,
    'assistant': 35, 'legal_analysis': 58, 'hearing': 62,
    'document_drafting': 62, 'roundcube': 22, 'control': 48}
COMPLEX_STAGES = {'strategy_analysis', 'evidence_matrix', 'act_project',
    'document_project', 'hearing_preparation', 'word_revision_plan',
    'legal_opinion_simulation', 'opinion_control', 'second_model_review',
    'mail_case_differential'}
ESCALATABLE_LOCAL_ERRORS = {'generation_ia_incomplete', 'generation_ia_delai_depasse',
    'fournisseur_ia_injoignable', 'reponse_ia_vide', 'json_ia_invalide',
    'schema_ia_invalide', 'contexte_trop_long'}


def defaults():
    return {'mode': 'local', 'external_provider': 'openrouter', 'threshold': 65,
      'allowed_purposes': list(DEFAULT_ALLOWED), 'external_client_data_approved': False,
      'fallback_local_on_error': True, 'escalate_after_local_failure': True,
      'anonymize_external': True, 'max_external_characters': 120000,
      'excluded_matters': [], 'benchmark_min_external_gain': 5.0}


def normalize(raw):
    value = defaults(); raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get('mode') or value['mode'])
    value['mode'] = mode if mode in ('local', 'hybrid', 'manual') else 'local'
    # 5.4.0 : la pseudonymisation n'est plus une option (forcée plus bas, après lecture des autres réglages).
    value['external_provider'] = str(raw.get('external_provider') or 'openrouter')
    try: value['threshold'] = max(20, min(int(raw.get('threshold', 65)), 95))
    except (TypeError, ValueError): value['threshold'] = 65
    allowed = raw.get('allowed_purposes', value['allowed_purposes'])
    value['allowed_purposes'] = ([str(x) for x in allowed if str(x) in BASE_SCORES]
                                 if isinstance(allowed, list) else list(DEFAULT_ALLOWED))
    for key in ('external_client_data_approved', 'fallback_local_on_error',
                'escalate_after_local_failure', 'anonymize_external'):
        value[key] = bool(raw.get(key, value[key]))
    try:
        value['max_external_characters'] = max(4000, min(
            int(raw.get('max_external_characters', 120000)), 250000))
    except (TypeError, ValueError): value['max_external_characters'] = 120000
    excluded = raw.get('excluded_matters', [])
    value['excluded_matters'] = sorted({str(x).strip() for x in excluded
                                        if str(x).strip()})[:1000] if isinstance(excluded, list) else []
    try:value['benchmark_min_external_gain']=max(0.0,min(float(raw.get('benchmark_min_external_gain',5)),30.0))
    except (TypeError,ValueError):value['benchmark_min_external_gain']=5.0
    return value


def policy(config):
    value = normalize(config.get('hybrid_routing', {}))
    value['anonymize_external'] = True          # 5.4.0 : obligatoire
    return value


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS hybrid_decisions_v400(
      id INTEGER PRIMARY KEY, at TEXT NOT NULL, purpose TEXT NOT NULL,
      stage TEXT NOT NULL, score INTEGER NOT NULL, threshold INTEGER NOT NULL,
      selected_provider TEXT NOT NULL, selected_model TEXT NOT NULL,
      external INTEGER NOT NULL, input_characters INTEGER NOT NULL,
      source_count INTEGER NOT NULL, document_count INTEGER NOT NULL,
      reason_codes TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT NOT NULL,
      decision_sha256 TEXT NOT NULL, estimated_cost_usd REAL NOT NULL DEFAULT 0,
      anonymized INTEGER NOT NULL DEFAULT 0,
      matter_hashes TEXT NOT NULL DEFAULT '[]');
    CREATE INDEX IF NOT EXISTS hybrid_decisions_at_v400
      ON hybrid_decisions_v400(at,purpose,external,status);
    CREATE TABLE IF NOT EXISTS hybrid_previews_v400(
      id TEXT PRIMARY KEY, created TEXT NOT NULL, expires TEXT NOT NULL,
      payload TEXT NOT NULL);
    ''')
    columns = {x[1] for x in desk.db.execute('PRAGMA table_info(hybrid_decisions_v400)')}
    for name, declaration in (('estimated_cost_usd', 'REAL NOT NULL DEFAULT 0'),
        ('anonymized', 'INTEGER NOT NULL DEFAULT 0'),
        ('matter_hashes', "TEXT NOT NULL DEFAULT '[]'")):
        if name not in columns:
            desk.db.execute('ALTER TABLE hybrid_decisions_v400 ADD COLUMN '+name+' '+declaration)
    desk.db.execute('DELETE FROM hybrid_previews_v400 WHERE expires<?',
                    (datetime.now(timezone.utc).isoformat(),))
    desk.db.commit()


def _walk_count(value, names):
    total = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names and isinstance(item, list): total += len(item)
            total += _walk_count(item, names)
    elif isinstance(value, list):
        for item in value: total += _walk_count(item, names)
    return total


def _size(payload=None, messages=None):
    if messages is not None:
        return sum(len(str(x.get('content') or '')) for x in messages if isinstance(x, dict))
    try: return len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    except (TypeError, ValueError): return len(str(payload or ''))


def _matter_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('matter', 'matter_id', 'dossier', 'dossier_id') and isinstance(item, (str, int)):
                if str(item).strip(): found.add(str(item).strip())
            found.update(_matter_ids(item))
    elif isinstance(value, list):
        for item in value: found.update(_matter_ids(item))
    return found


def _benchmark_recommendation(config, purpose):
    """Return cabinet-observed scores only; absence never blocks production."""
    mapping={'mail_drafting':('mail_draft','mixed'),'legal_analysis':('citations','adverse_arguments','mixed'),
      'hearing':('adverse_arguments','pieces','citations','mixed'),
      'document_drafting':('word_document','citations','pieces','mixed'),
      'assistant':('mixed',),'control':('citations','pieces','mixed')}
    tasks=mapping.get(str(purpose),())
    state=Path(str(config.get('state_dir') or ''))/'desk.sqlite3'
    if not tasks or not state.is_file():return {}
    db=sqlite3.connect(state,timeout=10);db.row_factory=sqlite3.Row
    try:
        placeholders=','.join('?' for _ in tasks)
        rows=list(db.execute('''SELECT r.provider,r.model,ROUND(AVG(r.total_score),1) score,
          COUNT(*) samples,SUM(r.hallucination_count) hallucinations
          FROM legal_benchmark_results_v410 r JOIN legal_benchmark_cases_v410 c ON c.id=r.case_id
          WHERE r.status='done' AND c.task_kind IN ('''+placeholders+''')
          GROUP BY r.provider,r.model ORDER BY score DESC,hallucinations,samples DESC''',tasks))
    except sqlite3.Error:return {}
    finally:db.close()
    if not rows:return {}
    result={'all':[dict(x) for x in rows]}
    local=next((dict(x) for x in rows if x['provider']=='ollama'),None)
    if local:result['local']=local
    return result


def complexity(purpose, stage='', payload=None, messages=None, metrics=None):
    purpose = str(purpose or 'assistant'); stage = str(stage or '')
    chars = _size(payload, messages); metrics = metrics if isinstance(metrics, dict) else {}
    sources = int(metrics.get('source_count') or _walk_count(
        payload, {'sources', 'source_ids', 'factual_sources', 'legal_sources'}))
    documents = int(metrics.get('document_count') or _walk_count(
        payload, {'documents', 'pieces', 'attachments', 'files'}))
    score = BASE_SCORES.get(purpose, 30); reasons = ['base_'+purpose]
    if stage in COMPLEX_STAGES: score += 18; reasons.append('production_juridique_complexe')
    if chars >= 8000: score += 8; reasons.append('contexte_8k')
    if chars >= 30000: score += 12; reasons.append('contexte_30k')
    if chars >= 70000: score += 10; reasons.append('contexte_70k')
    if sources >= 8: score += 8; reasons.append('sources_multiples')
    if sources >= 20: score += 8; reasons.append('sources_nombreuses')
    if documents >= 2: score += 8; reasons.append('documents_multiples')
    if documents >= 6: score += 7; reasons.append('documents_nombreux')
    return {'score': min(score, 100), 'input_characters': chars,
      'source_count': sources, 'document_count': documents, 'reason_codes': reasons}


def _estimate(provider, characters, max_tokens):
    input_tokens = max(1, (int(characters)+3)//4); output_tokens = max(0, int(max_tokens or 0))
    return (input_tokens*float(provider.get('input_usd_per_million', 0) or 0)+
            output_tokens*float(provider.get('output_usd_per_million', 0) or 0))/1_000_000


def _monthly_used(config, provider_id):
    state = str(config.get('state_dir') or '')
    if not state or not (Path(state)/'desk.sqlite3').is_file(): return 0.0
    db = sqlite3.connect(Path(state)/'desk.sqlite3', timeout=10)
    try:
        prefix = datetime.now(timezone.utc).strftime('%Y-%m-')+'%'
        try:
            return float(db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE provider=? AND at LIKE ? AND status='done'", (provider_id, prefix)).fetchone()[0])
        except sqlite3.Error: return 0.0
    finally: db.close()


def choose(config, purpose, stage='', payload=None, messages=None, metrics=None,
           max_tokens=3500, force_external=False, local_error=''):
    from .ai_gateway import provider_registry
    rule = policy(config); facts = complexity(purpose, stage, payload, messages, metrics)
    matters = _matter_ids(payload)
    result = {**facts, 'purpose': purpose, 'stage': stage, 'threshold': rule['threshold'],
      'external': False, 'provider': 'ollama', 'model': str(config.get('ollama', {}).get('model', '')),
      'fallback_local_on_error': rule['fallback_local_on_error'],
      'escalate_after_local_failure': rule['escalate_after_local_failure'],
      'anonymized': False, 'estimated_cost_usd': 0.0, 'monthly_used_usd': 0.0,
      'monthly_budget_usd': 0.0, 'per_request_budget_usd': 0.0,
      'matter_hashes': sorted(hashlib.sha256(x.encode()).hexdigest()[:16] for x in matters)}
    benchmark=_benchmark_recommendation(config,purpose)
    if benchmark.get('local'):
        result['model']=benchmark['local']['model'];result['reason_codes'].append('modele_local_mesure_par_banc')
    if rule['mode'] != 'hybrid': result['reason_codes'].append('mode_'+rule['mode']); return result
    excluded = {fold(x) for x in rule['excluded_matters']}
    if any(fold(x) in excluded for x in matters):
        result['reason_codes'].append('dossier_exclu'); return result
    if purpose not in rule['allowed_purposes']:
        result['reason_codes'].append('fonction_locale'); return result
    if not rule['external_client_data_approved']:
        result['reason_codes'].append('autorisation_externe_absente'); return result
    if facts['input_characters'] > rule['max_external_characters']:
        result['reason_codes'].append('taille_externe_depassee'); return result
    provider = provider_registry(config).get(rule['external_provider'])
    if not provider or provider.get('type') != 'openrouter' or not provider.get('enabled'):
        result['reason_codes'].append('openrouter_indisponible'); return result
    if not provider.get('external_data_allowed'):
        result['reason_codes'].append('donnees_externes_non_autorisees'); return result
    if not provider.get('zdr_required'):
        result['reason_codes'].append('zdr_non_exige'); return result
    if not rule['anonymize_external']:
        result['reason_codes'].append('anonymisation_desactivee'); return result
    if not force_external and facts['score'] < rule['threshold']:
        result['reason_codes'].append('complexite_sous_seuil'); return result
    route = (config.get('model_routing', {}).get(purpose) or {})
    # (5.4.0) la pseudonymisation réversible est appliquée à l'envoi, quel que soit ce réglage.
    model = (str(route.get('model') or '') if route.get('provider') == rule['external_provider'] else '')
    measured_external=next((x for x in benchmark.get('all',[]) if x['provider']==rule['external_provider']),None)
    if measured_external:model=str(measured_external['model']);result['reason_codes'].append('modele_externe_mesure_par_banc')
    model = model or str(provider.get('model') or '')
    if not model: result['reason_codes'].append('modele_openrouter_absent'); return result
    if measured_external and benchmark.get('local'):
        gain=float(measured_external['score'])-float(benchmark['local']['score'])
        if gain < rule['benchmark_min_external_gain']:
            result['reason_codes'].append('gain_banc_insuffisant_pour_cout_externe')
            result['benchmark_gain']=round(gain,1)
            return result
    estimated = _estimate(provider, facts['input_characters'], max_tokens)
    monthly_used = _monthly_used(config, rule['external_provider'])
    per_request = float(provider.get('per_request_budget_usd', 0) or 0)
    monthly = float(provider.get('monthly_budget_usd', 0) or 0)
    result.update({'estimated_cost_usd': estimated, 'monthly_used_usd': monthly_used,
      'monthly_budget_usd': monthly, 'per_request_budget_usd': per_request})
    if not per_request or estimated > per_request:
        result['reason_codes'].append('plafond_requete'); return result
    if not monthly or monthly_used+estimated > monthly:
        result['reason_codes'].append('budget_mensuel'); return result
    result.update({'external': True, 'provider': rule['external_provider'],
                   'model': model, 'anonymized': True})
    if force_external:
        result['reason_codes'].append('echec_local_eligible')
        if local_error: result['local_error'] = str(local_error)[:80]
    else: result['reason_codes'].append('complexite_au_dessus_seuil')
    return result


def _tokens_for_matters(config, matter_ids):
    tokens = set()
    try: matters = load_matters(config)
    except (OSError, ValueError, Stop): matters = []
    wanted = {fold(x) for x in matter_ids}
    for matter in matters:
        if not wanted or fold(str(matter.get('id', ''))) in wanted:
            values = [matter.get('id', ''), matter.get('client_name', ''), matter.get('path', '')]
            values += matter.get('aliases', []) + matter.get('references', [])
            for value in values:
                value = str(value or '').strip()
                if len(value) >= 3: tokens.add(value)
    return sorted(tokens, key=len, reverse=True)


def anonymize(config, value, matter_ids=None):
    counts = {'matter': 0, 'email': 0, 'phone': 0, 'path': 0}
    tokens = _tokens_for_matters(config, set(matter_ids or []) or _matter_ids(value))
    def clean_text(item):
        current = str(item)
        for token in tokens:
            current, number = re.subn(re.escape(token), '[DOSSIER]', current, flags=re.I)
            counts['matter'] += number
        current, number = re.subn(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])', '[COURRIEL]', current)
        counts['email'] += number
        current, number = re.subn(r'(?<!\d)(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)', '[TÉLÉPHONE]', current)
        counts['phone'] += number
        current, number = re.subn(r'(?<!\w)/(?:CABINET|Dossiers?|Clients?|Affaires?)[^\s\]\[{}<>"\']{3,}', '[CHEMIN]', current, flags=re.I)
        counts['path'] += number
        return current
    def walk(item):
        if isinstance(item, dict): return {str(k): walk(v) for k, v in item.items()}
        if isinstance(item, list): return [walk(v) for v in item]
        if isinstance(item, str): return clean_text(item)
        return item
    return walk(value), counts


def sanitize_external(config, decision, payload=None, messages=None):
    """5.4.0 : la pseudonymisation RÉVERSIBLE est faite au moment de l'envoi (model.Model._leaf), pour tout fournisseur non local.
    Ici, on contrôle seulement l'autorisation et on compte ce qui sera remplacé (journal des décisions)."""
    if not decision.get('external'): raise Stop('routage_externe_non_autorise')
    from .pseudo540 import Pseudonymizer
    from .model import pseudo_sources
    ps = Pseudonymizer(pseudo_sources(config))
    source = messages if messages is not None else payload
    ps.apply(source)
    counts = ps.summary()
    return ({'messages': messages, 'payload': None, 'redactions': counts}
            if messages is not None else {'messages': None, 'payload': payload, 'redactions': counts})


def external_config(config, decision):
    from .ai_gateway import provider_registry
    provider = provider_registry(config).get(decision['provider'])
    if not provider: raise Stop('fournisseur_ia_absent')
    result = dict(provider); result['model'] = decision['model']
    result.update({'provider_id': decision['provider'], 'provider_type': 'openrouter',
      'purpose': decision['purpose'], 'state_dir': config.get('state_dir', ''),
      '_hybrid_decision_done': True})
    from .extensions364 import active_skill_instructions
    result['skill_instructions'] = active_skill_instructions(config, decision['purpose'])
    return result


def record(config, decision, status, error=''):
    state = str(config.get('state_dir') or '')
    if not state: return
    db = sqlite3.connect(Path(state)/'desk.sqlite3', timeout=10); db.row_factory = sqlite3.Row
    try:
        class Proxy: pass
        proxy = Proxy(); proxy.db = db; ensure_schema(proxy)
        safe = {'purpose': decision.get('purpose', ''), 'stage': decision.get('stage', ''),
          'score': int(decision.get('score', 0)), 'threshold': int(decision.get('threshold', 0)),
          'provider': decision.get('provider', 'ollama'), 'model': decision.get('model', ''),
          'external': bool(decision.get('external')),
          'input_characters': int(decision.get('input_characters', 0)),
          'source_count': int(decision.get('source_count', 0)),
          'document_count': int(decision.get('document_count', 0)),
          'reason_codes': list(decision.get('reason_codes', [])), 'status': str(status),
          'error': str(error)[:160], 'estimated_cost_usd': float(decision.get('estimated_cost_usd', 0) or 0),
          'anonymized': bool(decision.get('anonymized')),
          'matter_hashes': list(decision.get('matter_hashes', []))}
        canonical = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('''INSERT INTO hybrid_decisions_v400(at,purpose,stage,score,threshold,
          selected_provider,selected_model,external,input_characters,source_count,
          document_count,reason_codes,status,error_code,decision_sha256,
          estimated_cost_usd,anonymized,matter_hashes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (datetime.now(timezone.utc).isoformat(), safe['purpose'], safe['stage'], safe['score'],
           safe['threshold'], safe['provider'], safe['model'], int(safe['external']),
           safe['input_characters'], safe['source_count'], safe['document_count'],
           json.dumps(safe['reason_codes'], ensure_ascii=False), safe['status'], safe['error'],
           hashlib.sha256(canonical.encode()).hexdigest(), safe['estimated_cost_usd'],
           int(safe['anonymized']), json.dumps(safe['matter_hashes'])))
        db.commit()
    finally: db.close()


def save_policy(desk, mode, external_provider, threshold, allowed_purposes,
                external_client_data_approved=False, fallback_local_on_error=True,
                max_external_characters=120000, escalate_after_local_failure=True,
                anonymize_external=True, excluded_matters=None):
    from .ai_gateway import PURPOSES, provider_registry
    value = normalize({'mode': str(mode), 'external_provider': str(external_provider),
      'threshold': threshold, 'allowed_purposes': [x for x in allowed_purposes if x in PURPOSES],
      'external_client_data_approved': bool(external_client_data_approved),
      'fallback_local_on_error': bool(fallback_local_on_error),
      'escalate_after_local_failure': bool(escalate_after_local_failure),
      'anonymize_external': bool(anonymize_external),
      'max_external_characters': max_external_characters,
      'excluded_matters': list(excluded_matters or [])})
    if value['mode'] == 'hybrid':
        provider = provider_registry(desk.c).get(value['external_provider'])
        if not provider or provider.get('type') != 'openrouter' or not provider.get('enabled'):
            raise Stop('openrouter_actif_requis')
        if not provider.get('external_data_allowed'): raise Stop('autorisation_donnees_externes_requise')
        if not provider.get('zdr_required'): raise Stop('openrouter_zdr_requis')
        if not value['external_client_data_approved']: raise Stop('confirmation_routage_hybride_requise')
        if not value['anonymize_external']: raise Stop('anonymisation_externe_requise')
        if not value['allowed_purposes']: raise Stop('fonction_hybride_requise')
    desk.setting('ai:hybrid', value); desk.c['hybrid_routing'] = value
    desk.audit('routage_hybride_modifie', {'mode': value['mode'],
      'external_provider': value['external_provider'], 'threshold': value['threshold'],
      'allowed_purposes': value['allowed_purposes'],
      'external_client_data_approved': value['external_client_data_approved'],
      'fallback_local_on_error': value['fallback_local_on_error'],
      'escalate_after_local_failure': value['escalate_after_local_failure'],
      'anonymize_external': value['anonymize_external'],
      'excluded_matter_count': len(value['excluded_matters'])})
    return value


def snapshot(desk, days=30):
    ensure_schema(desk)
    since = (datetime.now(timezone.utc)-timedelta(days=max(1, min(int(days), 365)))).isoformat()
    rows = [dict(x) for x in desk.db.execute('''SELECT at,purpose,stage,score,threshold,
      selected_provider,selected_model,external,input_characters,source_count,
      document_count,reason_codes,status,error_code,decision_sha256,
      estimated_cost_usd,anonymized,matter_hashes FROM hybrid_decisions_v400
      WHERE at>=? ORDER BY id DESC LIMIT 100''', (since,))]
    for row in rows:
        row['reason_codes'] = json.loads(row['reason_codes'] or '[]')
        row['matter_hashes'] = json.loads(row['matter_hashes'] or '[]')
    summary = {r['selected_provider']: {'decisions': r['decisions'], 'external': r['external']}
               for r in desk.db.execute('''SELECT selected_provider,COUNT(*) decisions,SUM(external) external
      FROM hybrid_decisions_v400 WHERE at>=? GROUP BY selected_provider''', (since,))}
    return {'policy': policy(desk.c), 'summary': summary, 'decisions': rows,
            'content_logged': False, 'secrets_exposed': False}


def simulate(desk, purpose, stage='', input_characters=0, source_count=0,
             document_count=0, max_tokens=3500, matter=''):
    metrics = {'source_count': max(0, min(int(source_count), 1000)),
      'document_count': max(0, min(int(document_count), 1000))}
    size = max(0, min(int(input_characters), 250000))
    payload = {'matter': str(matter), 'text': 'x'*size} if matter else {'text': 'x'*size}
    return choose(desk.c, purpose, stage, payload=payload, metrics=metrics, max_tokens=max_tokens)


def preview(desk, purpose, stage, text, matter='', max_tokens=3500):
    text = str(text or '')
    if not text or len(text) > 120000: raise Stop('apercu_hybride_taille_invalide')
    payload = {'matter': str(matter or ''), 'instruction': text}
    decision = choose(desk.c, purpose, stage, payload=payload, max_tokens=max_tokens)
    from .pseudo540 import Pseudonymizer
    from .model import pseudo_sources
    ps = Pseudonymizer(pseudo_sources(desk.c))
    clean = ps.apply(payload)
    counts = ps.summary()
    visible = json.dumps(clean, ensure_ascii=False, indent=2)
    return {'decision': decision, 'transmitted_preview': visible[:40000],
      'preview_truncated': len(visible) > 40000, 'redactions': counts,
      'would_transmit': bool(decision['external'])}


def store_preview(desk, value):
    ensure_schema(desk); raw = json.dumps(value, ensure_ascii=False)
    if len(raw) > 50000: raise Stop('apercu_hybride_trop_long')
    preview_id = hashlib.sha256((raw+datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:32]
    stamp = datetime.now(timezone.utc)
    desk.db.execute('INSERT INTO hybrid_previews_v400(id,created,expires,payload) VALUES(?,?,?,?)',
      (preview_id, stamp.isoformat(), (stamp+timedelta(minutes=10)).isoformat(), raw))
    desk.db.commit(); return preview_id


def take_preview(desk, preview_id):
    ensure_schema(desk)
    if not re.fullmatch(r'[0-9a-f]{32}', str(preview_id or '')): return None
    row = desk.db.execute('SELECT payload,expires FROM hybrid_previews_v400 WHERE id=?',
                          (preview_id,)).fetchone()
    desk.db.execute('DELETE FROM hybrid_previews_v400 WHERE id=?', (preview_id,)); desk.db.commit()
    if not row or row['expires'] < datetime.now(timezone.utc).isoformat(): return None
    return json.loads(row['payload'])
