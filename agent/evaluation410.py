"""Banc d'évaluation juridique reproductible et anonymisé AxiorHub 4.1."""
from datetime import datetime, timezone
import json
import re
import time

from .common import Stop, digest, fold


TASKS = {'matter_linking', 'dates', 'citations', 'adverse_arguments', 'pieces',
         'mail_draft', 'word_document', 'mixed'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS legal_benchmark_cases_v410(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, task_kind TEXT NOT NULL,
      prompt TEXT NOT NULL, expected TEXT NOT NULL, anonymized INTEGER NOT NULL,
      enabled INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS legal_benchmark_runs_v410(
      id TEXT PRIMARY KEY, status TEXT NOT NULL, models TEXT NOT NULL,
      case_count INTEGER NOT NULL, created TEXT NOT NULL, finished TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS legal_benchmark_results_v410(
      run_id TEXT NOT NULL, case_id TEXT NOT NULL, provider TEXT NOT NULL,
      model TEXT NOT NULL, status TEXT NOT NULL, scores TEXT NOT NULL,
      total_score REAL NOT NULL, hallucination_count INTEGER NOT NULL,
      latency_ms INTEGER NOT NULL, cost_usd REAL NOT NULL, error TEXT NOT NULL,
      response_sha256 TEXT NOT NULL, created TEXT NOT NULL,
      PRIMARY KEY(run_id,case_id,provider,model));
    CREATE INDEX IF NOT EXISTS legal_benchmark_results_model_v410
      ON legal_benchmark_results_v410(provider,model,created);
    ''')
    desk.db.commit()
    if 'scorer_version' not in {r[1] for r in desk.db.execute('PRAGMA table_info(legal_benchmark_results_v410)')}:
        desk.db.execute('ALTER TABLE legal_benchmark_results_v410 ADD COLUMN scorer_version INTEGER NOT NULL DEFAULT 0')
        desk.db.commit()


def _now(): return datetime.now(timezone.utc).isoformat()


def _lines(value, maximum=60):
    if isinstance(value, list): source = value
    else: source = re.split(r'[\r\n]+', str(value or ''))
    return [str(x).strip()[:500] for x in source if str(x).strip()][:maximum]


def _obviously_identifying(text):
    return bool(re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)|/(?:CABINET|Dossiers?|Clients?)/', text, re.I))


def save_case(desk, name, task_kind, prompt, expected, anonymized=False):
    ensure_schema(desk); name = str(name or '').strip(); prompt = str(prompt or '').strip()
    if not name or len(name)>160 or task_kind not in TASKS or not prompt or len(prompt)>30000:
        raise Stop('cas_evaluation_invalide')
    if not anonymized or _obviously_identifying(prompt):
        raise Stop('cas_evaluation_doit_etre_anonymise')
    expected = expected if isinstance(expected, dict) else {}
    allowed = {'matter_id','dates','citations','adverse_arguments','pieces','mail_required',
      'mail_forbidden','word_markers','permitted_claims'}
    clean = {key: (_lines(value) if key!='matter_id' else str(value or '')[:120])
             for key,value in expected.items() if key in allowed}
    canonical=json.dumps({'name':name,'task_kind':task_kind,'prompt':prompt,'expected':clean},ensure_ascii=False,sort_keys=True)
    ident=digest(canonical)[:32];stamp=_now()
    desk.db.execute('''INSERT INTO legal_benchmark_cases_v410
      VALUES(?,?,?,?,?,1,1,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
      task_kind=excluded.task_kind,prompt=excluded.prompt,expected=excluded.expected,
      anonymized=1,enabled=1,updated=excluded.updated''',
      (ident,name,task_kind,prompt,json.dumps(clean,ensure_ascii=False),stamp,stamp))
    desk.db.commit();desk.audit('cas_evaluation_410_enregistre',{'case_id':ident,'task_kind':task_kind})
    return {'id':ident,'saved':True}


def list_cases(desk):
    ensure_schema(desk);rows=[]
    for row in desk.db.execute('SELECT * FROM legal_benchmark_cases_v410 ORDER BY created,id'):
        item=dict(row);item['expected']=json.loads(item['expected'] or '{}');item.pop('prompt',None);rows.append(item)
    return rows


def _norm(values): return {fold(str(x)).strip() for x in values if str(x).strip()}


def _coverage(expected, actual):
    wanted=_norm(expected);found=_norm(actual)
    return None if not wanted else round(100*len(wanted & found)/len(wanted),1)


def score_response(expected, response):
    text=str(response.get('draft_email') or '')
    word=str(response.get('word_document') or '')
    permitted=_norm(expected.get('permitted_claims',[]));claims=_norm(response.get('claims',[]))
    hallucinations=sorted(claims-permitted) if permitted else sorted(claims)
    scores={
      'matter_linking':None if not expected.get('matter_id') else (100.0 if str(response.get('matter_id',''))==str(expected['matter_id']) else 0.0),
      'dates':_coverage(expected.get('dates',[]),response.get('dates',[])),
      'citations':_coverage(expected.get('citations',[]),response.get('citations',[])),
      'adverse_arguments':_coverage(expected.get('adverse_arguments',[]),response.get('adverse_arguments',[])),
      'pieces':_coverage(expected.get('pieces',[]),response.get('pieces',[])),
      'mail_quality':_coverage(expected.get('mail_required',[]),[x for x in expected.get('mail_required',[]) if fold(x) in fold(text)]),
      'word_template':_coverage(expected.get('word_markers',[]),[x for x in expected.get('word_markers',[]) if fold(x) in fold(word)]),
      'no_hallucination':max(0.0,100.0-25.0*len(hallucinations)) if permitted and claims else None,
    }
    forbidden=sum(1 for x in expected.get('mail_forbidden',[]) if fold(x) in fold(text))
    if scores['mail_quality'] is not None or forbidden:
        scores['mail_quality']=max(0.0,(scores['mail_quality'] or 0)-25.0*forbidden)
    measured = [v for v in scores.values() if v is not None]
    total=round(sum(measured)/len(measured),1) if measured else 0.0
    return {'scores':scores,'total_score':total,'hallucinations':hallucinations,
            'hallucination_count':len(hallucinations), 'measured_criteria':len(measured),
            'eligible_for_routing':bool(measured) and scores['no_hallucination'] is not None,
            'warning':'Contrôles déterministes de critères fournis ; les affirmations non déclarées restent à relire.'}


BENCHMARK_PROMPT='''Tu participes à un banc d’évaluation juridique ANONYMISÉ. N’utilise aucune connaissance d’un dossier réel et n’appelle aucun outil. Extrais ou rédige uniquement à partir du cas fourni. Retourne un objet JSON avec exactement ces clés : matter_id (texte), dates (liste), citations (liste), adverse_arguments (liste), pieces (liste), draft_email (texte), word_document (texte), claims (liste des affirmations factuelles présentes dans ta réponse). N’invente rien ; une valeur absente reste vide.'''


def _targets(desk, raw):
    from .ai_gateway import provider_registry
    registry=provider_registry(desk.c);rows=[]
    for line in _lines(raw,12):
        if ':' not in line: raise Stop('modele_evaluation_invalide')
        provider_id,model=line.split(':',1);provider_id=provider_id.strip();model=model.strip()
        cfg=registry.get(provider_id)
        if not cfg or not cfg.get('enabled',True) or not model: raise Stop('modele_evaluation_invalide')
        if cfg.get('type')!='ollama' and not cfg.get('external_data_allowed'): raise Stop('fournisseur_externe_non_autorise')
        rows.append((provider_id,model,dict(cfg)))
    if not rows: raise Stop('modele_evaluation_requis')
    return rows


def run_benchmark(desk, models):
    ensure_schema(desk);cases=list(desk.db.execute("SELECT * FROM legal_benchmark_cases_v410 WHERE enabled=1 AND anonymized=1 ORDER BY id"))
    if not cases: raise Stop('cas_evaluation_absent')
    targets=_targets(desk,models);stamp=_now();rid=digest(stamp+'|'+str(models))[:32]
    desk.db.execute('INSERT INTO legal_benchmark_runs_v410 VALUES(?,?,?,?,?,?)',
      (rid,'running',json.dumps([p+':'+m for p,m,_ in targets]),len(cases),stamp,''));desk.db.commit()
    from .model import Model
    from .ai_gateway import usage_snapshot
    for provider_id,model,cfg in targets:
      cfg.update({'model':model,'provider_id':provider_id,'provider_type':cfg.get('type','ollama'),
        'purpose':'control','state_dir':desk.c['state_dir'],'temperature':0,'external_policy_config':desk.c})
      from .model import pseudo_sources
      cfg['pseudo']=pseudo_sources(desk.c)
      if cfg.get('type')=='openrouter':cfg['zdr_required']=True
      for case in cases:
        started=time.monotonic();status='done';error='';raw='';scored={'scores':{},'total_score':0,'hallucination_count':0}
        before=sum(float(x['cost_usd']) for x in usage_snapshot(desk,provider_id))
        try:
            raw=Model(cfg).complete([{'role':'system','content':BENCHMARK_PROMPT},{'role':'user','content':case['prompt']}],temperature=0,max_tokens=3500)
            response=json.loads(raw);scored=score_response(json.loads(case['expected']),response)
            if not scored['eligible_for_routing']:status='incomplete'
        except (Stop,ValueError,TypeError) as exc:
            status='error';error=str(exc)[:160]
        after=sum(float(x['cost_usd']) for x in usage_snapshot(desk,provider_id))
        latency=round((time.monotonic()-started)*1000)
        desk.db.execute('''INSERT INTO legal_benchmark_results_v410
          (run_id,case_id,provider,model,status,scores,total_score,hallucination_count,latency_ms,cost_usd,error,response_sha256,created)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(rid,case['id'],provider_id,model,status,
          json.dumps(scored.get('scores',{}),ensure_ascii=False),scored.get('total_score',0),
          scored.get('hallucination_count',0),latency,max(0,after-before),error,
          digest(raw),_now()))
        desk.db.execute('UPDATE legal_benchmark_results_v410 SET scorer_version=567 WHERE run_id=? AND case_id=? AND provider=? AND model=?',(rid,case['id'],provider_id,model))
        desk.db.commit()
    desk.db.execute("UPDATE legal_benchmark_runs_v410 SET status='done',finished=? WHERE id=?",(_now(),rid));desk.db.commit()
    desk.audit('banc_juridique_410_termine',{'run_id':rid,'models':len(targets),'cases':len(cases)})
    return {'run_id':rid,'models':len(targets),'cases':len(cases),'status':'done'}


def dashboard(desk):
    ensure_schema(desk);cases=list_cases(desk)
    rows=[]
    for r in desk.db.execute('''SELECT provider,model,COUNT(*) samples,
      ROUND(AVG(total_score),1) score, SUM(hallucination_count) hallucinations,
      ROUND(AVG(latency_ms)) latency_ms, ROUND(SUM(cost_usd),6) cost_usd,
      MAX(created) last_run FROM legal_benchmark_results_v410
      WHERE status='done' AND scorer_version=567 GROUP BY provider,model ORDER BY score DESC,hallucinations,cost_usd'''):
        rows.append(dict(r))
    runs=[dict(x) for x in desk.db.execute('SELECT * FROM legal_benchmark_runs_v410 ORDER BY created DESC LIMIT 20')]
    from .learning410 import snapshot as learning_snapshot
    learning=learning_snapshot(desk,30)
    correction_index={(x['provider'],x['model']):x for x in learning['corrections_by_model']}
    for row in rows:
        observed=correction_index.get((row['provider'],row['model']),{})
        row['lawyer_reviews']=int(observed.get('reviews') or 0)
        row['lawyer_changed_characters']=int(observed.get('changed_characters') or 0)
        row['lawyer_rejections']=int(observed.get('rejected') or 0)
    return {'cases':cases,'leaderboard':rows,'runs':runs,
      'correction_volume_30d':learning['correction_volume'],
      'changed_characters_30d':learning['changed_characters'],
      'corrections_by_model':learning['corrections_by_model'],
      'warning':'Classement propre aux cas anonymisés du cabinet. Il ne prouve pas la justesse juridique générale.'}


def perform(desk, kind, args):
    if kind=='run_legal_benchmark410': return run_benchmark(desk,args.get('models',''))
    raise Stop('action_evaluation_410_inconnue')
