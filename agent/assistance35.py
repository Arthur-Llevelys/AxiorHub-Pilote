"""Supervised practice tools. Percentages describe evidence, never a court's odds."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit
import json
import re

from .common import Stop, digest, fold


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS assistance_projects_v350(
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, matter TEXT NOT NULL,
      fingerprint TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL,
      UNIQUE(kind,matter,fingerprint));
    CREATE INDEX IF NOT EXISTS assistance_projects_matter_v350
      ON assistance_projects_v350(matter,kind,created DESC);
    CREATE TABLE IF NOT EXISTS comparable_decisions_v350(
      matter TEXT NOT NULL, authority_id TEXT NOT NULL, outcome TEXT NOT NULL,
      similarity_reason TEXT NOT NULL, procedural_context TEXT NOT NULL,
      checked_at TEXT NOT NULL, PRIMARY KEY(matter,authority_id));
    ''');desk.db.commit()


def _matter(desk,reference):
    from .legal_research import _matter as resolve
    return resolve(desk,reference)


def _save(desk,kind,mid,key,data):
    ensure_schema(desk);fp=digest(kind+'|'+mid+'|'+json.dumps(key,sort_keys=True,ensure_ascii=False))
    previous=desk.db.execute('SELECT id,data FROM assistance_projects_v350 WHERE kind=? AND matter=? AND fingerprint=?',
        (kind,mid,fp)).fetchone()
    if previous:return json.loads(previous['data'])|{'idempotent':True}
    pid=fp[:32];data={'project_id':pid,'kind':kind,'matter':mid,'created':desk.now(),
          'external_actions':False,**data}
    desk.db.execute('INSERT INTO assistance_projects_v350 VALUES(?,?,?,?,?,?)',
        (pid,kind,mid,fp,json.dumps(data,ensure_ascii=False),data['created']))
    desk.db.commit();desk.audit('assistance35_prepared',{'kind':kind,'matter':mid,'project':pid})
    return data|{'idempotent':False}


def preview(desk,pid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(pid)):raise Stop('projet_assistance_invalide')
    row=desk.db.execute('SELECT data FROM assistance_projects_v350 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_assistance_absent')
    return json.loads(row['data'])


def recent(desk,kind='',matter='',limit=30):
    ensure_schema(desk);allowed={'coaching','appel','compte_rendu_appel','calcul','facturation'}
    if kind and kind not in allowed:raise Stop('type_assistance_invalide')
    if matter: matter=_matter(desk,matter)['id']
    where=[];values=[]
    if kind:where.append('kind=?');values.append(kind)
    if matter:where.append('matter=?');values.append(matter)
    values.append(max(1,min(int(limit),100)))
    sql='SELECT id,kind,matter,created,data FROM assistance_projects_v350'
    if where:sql+=' WHERE '+' AND '.join(where)
    rows=desk.db.execute(sql+' ORDER BY created DESC LIMIT ?',values).fetchall()
    return [{'project_id':r['id'],'kind':r['kind'],'matter':r['matter'],'created':r['created'],
             'status':json.loads(r['data']).get('status','à relire')} for r in rows]


def _words(value):
    return {w for w in re.findall(r'[a-z0-9]{4,}',fold(str(value)))
            if w not in {'pour','dans','avec','cette','celle','votre','notre','leurs','faire','plus','sont'}}


def coach(desk,args):
    from .hearing import ensure_schema as hearings
    hearings(desk);pid=str(args.get('hearing_project_id',''))
    row=desk.db.execute('SELECT matter,status,data FROM hearing_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row or row['status'] not in ('pending','done'):raise Stop('audience_non_prete_pour_coaching')
    speech=str(args.get('speech','')).strip()
    if not speech or len(speech)>20000:raise Stop('plaidoirie_transcrite_invalide')
    try:seconds=int(args.get('duration_seconds',0));target=int(args.get('target_minutes',5))
    except (ValueError,TypeError):raise Stop('duree_coaching_invalide') from None
    if not 30<=seconds<=7200 or target not in (5,10,20):raise Stop('duree_coaching_invalide')
    hearing=json.loads(row['data']);plan=hearing['preparation']['oral_plan_'+str(target)]
    covered=[];missing=[];words=_words(speech)
    for item in plan:
        terms=_words(item['heading'])|_words(item['message'])
        present=bool(terms and len(terms&words)>=min(2,len(terms)))
        (covered if present else missing).append({'heading':item['heading'],
            'source_ids':item['source_ids']})
    total=len(plan);coverage=round(100*len(covered)/total) if total else 0
    questions=hearing['preparation'].get('likely_questions',[])[:8]
    result={'status':'à relire','hearing_project_id':pid,'target_minutes':target,
      'duration_seconds':seconds,'duration_gap_seconds':seconds-target*60,
      'speech_sha256':digest(speech),'plan_coverage_percent':coverage,
      'plan_items_count':total,'items_detected':covered,'items_to_check':missing,
      'practice_questions':[{'question':q['question'],'source_ids':q['source_ids']} for q in questions],
      'sources':[{'id':x['id'],'path':x['path'],'kind':x['kind']} for x in hearing['sources']],
      'limitations':['Repérage lexical seulement : un thème absent de la transcription peut avoir été traité autrement.',
         'Le pourcentage mesure la couverture des thèmes du plan, pas les chances de gagner.',
         'Les citations et les faits prononcés doivent être vérifiés par l’avocat.']}
    return _save(desk,'coaching',row['matter'],[pid,digest(speech),seconds,target],result)


def prepare_call(desk,args):
    matter=_matter(desk,args.get('matter',''));mid=matter['id']
    purpose=str(args.get('purpose','')).strip();email=str(args.get('contact_email','')).strip().lower()
    if not purpose or len(purpose)>1000:raise Stop('objet_appel_invalide')
    if email and not any(p['email'].lower()==email for p in matter.get('correspondents',[])):
        raise Stop('correspondant_appel_non_lie_au_dossier')
    from .index import DocumentIndex
    index=DocumentIndex(desk.c['state_dir']);sources=[]
    try:
        for row in index.db.execute('SELECT path,etag,modified,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT 8',(mid,)):
            if not row[3]:sources.append({'id':digest(mid+'|'+row[0]+'|'+str(row[1])),
                'path':row[0],'modified':row[2]})
    finally:index.db.close()
    if not sources:raise Stop('sources_dossier_appel_absentes')
    questions=['Confirmer l’identité et la qualité de l’interlocuteur.',
      'Quel est l’objet précis de votre appel ?', 'Quels faits, dates et documents doivent être vérifiés ?',
      'Quelle suite attendez-vous du cabinet ?']
    result={'status':'projet_interne','purpose':purpose,'contact_email':email,
      'documents_to_review':sources,'questions_to_ask':questions,
      'points_for_lawyer':['Vérifier les dernières instructions et les éventuels conflits de dossier.',
          'Confirmer séparément tout engagement, délai ou position juridique.'],
      'safety':{'telephone_call_placed':False,'audio_recorded':False,'invitation_sent':False}}
    return _save(desk,'appel',mid,[purpose,email,sources],result)


def record_call(desk,args):
    call=preview(desk,str(args.get('call_project_id','')))
    if call['kind']!='appel':raise Stop('preparation_appel_invalide')
    notes=str(args.get('notes','')).strip()
    if not notes or len(notes)>12000:raise Stop('notes_appel_invalides')
    data={'status':'à valider','call_project_id':call['project_id'],'notes':notes,
      'source_ids':[x['id'] for x in call['documents_to_review']],
      'next_steps':['Relire les notes, confirmer les instructions et les échéances avec le dossier.'],
      'safety':{'telephone_call_placed':False,'email_sent':False,'task_created':False,
         'facts_confirmed_automatically':False}}
    return _save(desk,'compte_rendu_appel',call['matter'],[call['project_id'],digest(notes)],data)


def _dec(value,label,positive=True):
    try:result=Decimal(str(value).replace(',','.'))
    except (InvalidOperation,ValueError):raise Stop(label+'_invalide') from None
    if not result.is_finite() or (result<=0 if positive else result<0) or result>Decimal('1000000000'):
        raise Stop(label+'_invalide')
    return result


def _cents(value):
    amount=_dec(value,'montant')
    if amount.as_tuple().exponent < -2:raise Stop('montant_invalide')
    return int(amount*100)


def _day(value):
    try:result=date.fromisoformat(str(value))
    except (TypeError,ValueError):raise Stop('date_calcul_invalide') from None
    return result


def calculate(desk,args):
    matter=_matter(desk,args.get('matter',''));mid=matter['id'];kind=str(args.get('calculation_type',''))
    source=str(args.get('source_url','')).strip()
    try:parsed=urlsplit(source);hostname=parsed.hostname
    except ValueError:raise Stop('source_calcul_invalide') from None
    # The URL is a reference for manual checking, not proof that the figure was retrieved.
    if parsed.scheme!='https' or not hostname or parsed.username or parsed.password or len(source)>1500:
        raise Stop('source_calcul_invalide')
    allowed=('legifrance.gouv.fr','insee.fr','banque-france.fr','service-public.fr')
    if not any(hostname==h or hostname.endswith('.'+h) for h in allowed):
        raise Stop('source_calcul_non_officielle')
    reference=str(args.get('source_reference','')).strip()
    if not reference or len(reference)>300:raise Stop('reference_calcul_invalide')
    parameters={};result={};warnings=['Source et paramètres saisis par l’avocat : vérifier le texte, la période et son applicabilité.']
    if kind=='simple_interest':
        principal=_cents(args.get('principal',''));rate=_dec(args.get('annual_rate',''),'taux',positive=False)
        if rate>100:raise Stop('taux_invalide')
        start=_day(args.get('start_date'));end=_day(args.get('end_date'))
        days=(end-start).days
        if not 1<=days<=3650:raise Stop('periode_calcul_invalide')
        # Explicit convention: actual calendar days / fixed 365, without capitalization.
        interest=(Decimal(principal)/100*rate/100*Decimal(days)/365).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        parameters={'principal':str(Decimal(principal)/100),'annual_rate_percent':str(rate),
          'start_date':start.isoformat(),'end_date_excluded':end.isoformat(),
          'days':days,'convention':'jours réels / 365, taux constant, intérêts simples'}
        result={'interest':str(interest),'total':str(interest+Decimal(principal)/100),'currency':'EUR'}
        warnings.append('Ce calcul ne vérifie ni le taux légal applicable ni les versements, suspensions ou capitalisations.')
    elif kind=='rent_indexation':
        rent=_cents(args.get('rent',''));old=_dec(args.get('base_index',''),'indice_base')
        new=_dec(args.get('new_index',''),'indice_nouveau')
        if old>100000 or new>100000:raise Stop('indice_invalide')
        updated=(Decimal(rent)/100*new/old).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        parameters={'rent':str(Decimal(rent)/100),'base_index':str(old),'new_index':str(new),
            'formula':'loyer initial × nouvel indice ÷ indice de base'}
        result={'new_rent':str(updated),'difference':str(updated-Decimal(rent)/100),'currency':'EUR'}
        warnings.append('Vérifier l’indice, les trimestres et la clause contractuelle avant d’appliquer ce montant.')
    elif kind=='calendar_days':
        start=_day(args.get('start_date'))
        try:days=int(args.get('days',0))
        except (ValueError,TypeError):raise Stop('nombre_jours_invalide') from None
        if not 1<=days<=3650:raise Stop('nombre_jours_invalide')
        parameters={'start_date':start.isoformat(),'calendar_days_added':days}
        result={'arithmetic_date':(start+timedelta(days=days)).isoformat()}
        warnings.append('Addition arithmétique uniquement : aucun délai procédural, report ou jour férié calculé.')
    else:raise Stop('type_calcul_invalide')
    data={'status':'paramètres_à_valider','calculation_type':kind,'parameters':parameters,
      'result':result,'source':{'url':source,'reference':reference,'verified_automatically':False},
      'warnings':warnings,'invoice_created':False}
    return _save(desk,'calcul',mid,[kind,parameters,source,reference],data)


def classify_comparable(desk,args):
    ensure_schema(desk);mid=_matter(desk,args.get('matter',''))['id'];aid=str(args.get('authority_id',''))
    row=desk.db.execute('SELECT * FROM legal_authorities_v240 WHERE id=? AND matter=? AND verification_status=?',
        (aid,mid,'verified')).fetchone()
    if not row:raise Stop('decision_officielle_non_verifiee')
    outcome=str(args.get('outcome',''))
    if outcome not in ('favorable','unfavorable','mixed','excluded'):raise Stop('issue_comparable_invalide')
    reason=str(args.get('similarity_reason','')).strip();context=str(args.get('procedural_context','')).strip()
    if not 20<=len(reason)<=1500 or not context or len(context)>300:
        raise Stop('motif_comparabilite_invalide')
    desk.db.execute('INSERT OR REPLACE INTO comparable_decisions_v350 VALUES(?,?,?,?,?,?)',
        (mid,aid,outcome,reason,context,desk.now()));desk.db.commit()
    desk.audit('comparable_decision_labeled',{'matter':mid,'authority':aid,'outcome':outcome})
    return {'matter':mid,'authority_id':aid,'outcome':outcome,'decision_created':False}


def comparables(desk,matter):
    ensure_schema(desk);mid=_matter(desk,matter)['id']
    rows=[dict(r) for r in desk.db.execute('''SELECT c.*,a.official_url,a.identifier,a.exact_excerpt,
      a.official_text_sha256,a.verification_status FROM comparable_decisions_v350 c
      JOIN legal_authorities_v240 a ON a.id=c.authority_id AND a.matter=c.matter
      WHERE c.matter=? AND a.verification_status='verified' ORDER BY c.checked_at DESC''',(mid,))]
    # Distinct judgments, not separately verified excerpts from the same judgment.
    unique={};conflicting=set()
    for row in rows:
        if row['outcome'] not in ('favorable','unfavorable'):continue
        key=fold(row['identifier']).strip() or row['official_url']
        if key in unique and unique[key]['outcome']!=row['outcome']:conflicting.add(key)
        else:unique.setdefault(key,row)
    usable=[r for key,r in unique.items() if key not in conflicting]
    count=len(usable);percentage=(round(100*sum(r['outcome']=='favorable' for r in usable)/count,1)
                       if count>=10 else None)
    return {'matter':mid,'labeled_decisions':rows,'comparable_count':count,
      'favorable_count':sum(r['outcome']=='favorable' for r in usable),
      'observed_favorable_percent':percentage,'minimum_for_percentage':10,
      'conflicting_decisions_excluded':len(conflicting),
      'meaning':'Fréquence descriptive des décisions officiellement vérifiées et comparées par l’avocat ; sélection non représentative, aucune probabilité de succès du dossier.',
      'status':'série_insuffisante' if percentage is None else 'fréquence_descriptive'}


def billing_review(desk,args):
    from .workstation import unpaid_summary
    from .autonomy import billing_proposals
    mid=_matter(desk,args.get('matter',''))['id'];summary=unpaid_summary(desk,mid)
    if not summary['synchronized']:raise Stop('factures_non_synchronisees')
    if not summary['linked']:raise Stop('client_invoice_ninja_non_lie')
    stamp=datetime.fromisoformat(summary['last_sync'].replace('Z','+00:00'))
    if datetime.now(timezone.utc)-stamp>timedelta(hours=24):raise Stop('factures_a_actualiser')
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
    due=[x for x in summary['invoices'] if x['balance']>0 and x['due_date'] and
         x['due_date']<today and x['status'] not in ('cancelled','draft')]
    proposals=billing_proposals(desk,'pending',mid,50)
    data={'status':'à vérifier','invoices':[{'number':r['number'],'balance':r['balance'],
      'currency':r.get('currency',''),'due_date':r['due_date'],'status':r['status']} for r in due],
      'balance_by_currency':summary.get('balance_by_currency',{}),
      'candidate_diligences':[{'id':p['id'],'label':p['label'],
        'estimated_minutes':p['estimated_minutes'],'requires_time_confirmation':True}
        for p in proposals],
      'last_sync':summary['last_sync'],'warnings':['Solde, paiements et contestation à contrôler dans Invoice Ninja.',
        'Temps estimé et temps réellement réalisé sont distincts. Aucune relance ni facture créée.'],
      'invoice_created':False,'payment_created':False,'reminder_sent':False}
    return _save(desk,'facturation',mid,[data['invoices'],data['candidate_diligences'],data['last_sync']],data)


def perform(desk,kind,args):
    return {'coach_hearing35':coach,'prepare_call35':prepare_call,
      'record_call35':record_call,'calculate35':calculate,
      'billing_review35':billing_review,'classify_comparable35':classify_comparable}[kind](desk,args)
