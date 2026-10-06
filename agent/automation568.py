"""Règles de documents configurables. Le texte n'est jamais du code exécutable.

Une compilation produit une recette fermée, révisable avant activation.
Les effets sont assurés par les mêmes workers et connecteurs que le cabinet.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import PurePosixPath
import re
import secrets

from .common import Stop, clean_path, digest, fold, load_matters, matter_display, under
from . import settings568

ACTIONS={'analyze','file_procedure','calendar','inform_draft','next_task','response_conclusions'}
# 5.6.9 : sous-dossiers de classement admis (liste fermée ; aucun chemin libre fourni par un modèle).
FOLDERS=('PROCEDURE','PIECES','CORRESPONDANCES','EXPERTISES','HONORAIRES')
TYPES={'renvoi','soit_transmis','injonction','appel','conclusions','convocation','contrat','autre'}
LABELS={'analyze':'Analyser intégralement et citer les sources','file_procedure':'Classer dans le sous-dossier choisi du dossier, sans écraser',
 'calendar':'Préparer ou inscrire les dates prouvées dans les agendas sélectionnés',
 'inform_draft':'Déposer un brouillon d’information et le relire depuis IMAP',
 'next_task':'Préparer la prochaine tâche interne','response_conclusions':'Préparer un projet de conclusions en réponse si les sources le justifient'}
DEFAULT_ACTIONS=['analyze','file_procedure','calendar','inform_draft','next_task']
TEMPLATES=[
 ('renvoi','Avis de renvoi',['AVIS DE RENVOI','AVIS_DE_RENVOI'],['renvoi']),
 ('soit_transmis','Soit transmis',['SOIT TRANSMIS','SOIT_TRANSMIS'],['soit_transmis']),
 ('injonction','Injonction',['INJONCTION'],['injonction']),
 ('appel','Déclaration d’appel et convocation',['DECLARATION APPEL','DECLARATION D APPEL','CONVOC'],['appel','convocation']),
 ('conclusions','Nouvelles conclusions',['CONCLUSIONS'],['conclusions']),
]


def normalized(text):return re.sub(r'[^a-z0-9]+',' ',fold(str(text))).strip()


def validate_recipe(value):
    if not isinstance(value,dict) or set(value)-{'names','types','age_days','clock','actions','mode','scope','recipient','folder','guidance'}:
        raise Stop('recette_agent_champ_inconnu')
    out=deepcopy(value)
    for key,limit in (('names',30),('types',8),('actions',6)):
        items=out.get(key,[])
        if not isinstance(items,list) or len(items)>limit or any(not isinstance(x,str) for x in items):raise Stop('recette_agent_invalide')
        out[key]=list(dict.fromkeys(x.strip() for x in items if x.strip()))
    if any(len(x)>100 or any(ord(c)<32 for c in x) for x in out['names']):raise Stop('declencheur_agent_invalide')
    if set(out['types'])-TYPES or set(out['actions'])-ACTIONS or not out['actions']:raise Stop('action_agent_non_autorisee')
    if not out['names'] and not out['types']:raise Stop('declencheur_agent_requis')
    n=out.get('age_days',10)
    if type(n)!=int or not 1<=n<=90:raise Stop('anciennete_agent_invalide')
    out.update(age_days=n,clock=out.get('clock','created'),mode=out.get('mode','prepare'),scope=str(out.get('scope') or ''),
               recipient=out.get('recipient','case_role'),folder=out.get('folder','PROCEDURE'),guidance=str(out.get('guidance') or '').strip())
    if out['clock'] not in ('created','modified') or out['mode'] not in ('observe','prepare','organize'):raise Stop('autonomie_agent_invalide')
    if out['recipient'] not in ('case_role','self') or out['folder'] not in FOLDERS:raise Stop('destination_agent_non_autorisee')
    if len(out['guidance'])>6000 or '\0' in out['guidance']:raise Stop('instruction_agent_trop_longue')
    # L'analyse est obligatoire avant tout effet, même si l'interprétation l'omet.
    out['actions']=['analyze']+[x for x in out['actions'] if x!='analyze']
    return out


def recipe_text(recipe):
    r=validate_recipe(recipe)
    return {'trigger':('Nom contenant '+', '.join(r['names'])+' ou type '+', '.join(r['types'])),
      'age':str(r['age_days'])+' jours maximum depuis '+('la création prouvée par WebDAV' if r['clock']=='created' else 'la modification WebDAV'),
      'actions':[LABELS[x] for x in r['actions']], 'mode':r['mode'],
      'restrictions':['Aucun envoi ni dépôt judiciaire','Aucun script ni outil arbitraire','Aucun écrasement de fichier',
                      'Dates calculées : circuit et points de départ prouvés','Une correction de règle suspend les exécutions précédentes']}


def defaults(desk,owner):
    """Métadonnées seulement, pas de tâche ni d'effet distant lors d'une lecture UI."""
    for key,name,names,types in TEMPLATES:
        rid=digest('document-rule568|'+owner+'|'+key)
        actions=DEFAULT_ACTIONS+(['response_conclusions'] if key in ('renvoi','appel','conclusions') else [])
        instruction=('Si un nouveau fichier créé depuis moins de 10 jours comporte dans son nom '+', '.join(names)+
          ' ou correspond au type '+', '.join(types)+', analyse intégralement le document, classe-le dans PROCEDURE du bon dossier, '
          'prépare les événements prouvés sans doublon dans mes agendas, rédige un brouillon d’information selon mon rôle au dossier '
          'et prépare la prochaine tâche interne. Ne jamais envoyer le courriel ni déposer les conclusions.')
        recipe=validate_recipe({'names':names,'types':types,'age_days':10,'clock':'created','actions':actions,'mode':'organize'})
        now=desk.now()
        desk.db.execute('INSERT OR IGNORE INTO document_rules568 VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (rid,owner,key,name,instruction,json.dumps(recipe,ensure_ascii=False),1,'draft',now,now))
    desk.db.commit()


def _rule(desk,rid,owner):
    row=desk.db.execute('SELECT * FROM document_rules568 WHERE id=? AND owner=?',(str(rid),owner)).fetchone()
    if not row or row['state']=='deleted':raise Stop('agent_document_absent')
    out=dict(row);out['recipe']=json.loads(out['recipe']);return out


def listing(desk,owner):
    defaults(desk,owner)
    result=[]
    for raw in desk.db.execute("SELECT * FROM document_rules568 WHERE owner=? AND state<>'deleted' ORDER BY created,name",(owner,)):
        r=dict(raw);r['recipe']=json.loads(r['recipe']);r['summary']=recipe_text(r['recipe'])
        r['counts']={x['state']:x['n'] for x in desk.db.execute('SELECT state,count(*) AS n FROM document_runs568 WHERE rule_id=? GROUP BY state',(r['id'],))}
        result.append(r)
    return result


def save(desk,data,owner):
    if not isinstance(data,dict) or set(data)-{'id','revision','name','instruction','recipe'}:raise Stop('agent_document_champ_inconnu')
    name=str(data.get('name') or '').strip();text=str(data.get('instruction') or '').strip()
    if not 3<=len(name)<=160 or not 10<=len(text)<=6000:raise Stop('instruction_agent_invalide')
    r=validate_recipe(data.get('recipe'));known={str(m['id']) for m in load_matters(desk.c)}
    if r['scope'] and r['scope'] not in known:raise Stop('dossier_absent')
    old=_rule(desk,data['id'],owner) if data.get('id') else None
    if old and data.get('revision')!=old['revision']:raise Stop('regle_modifiee_recharger')
    if not old and desk.db.execute("SELECT count(*) FROM document_rules568 WHERE owner=? AND state<>'deleted'",(owner,)).fetchone()[0]>=50:raise Stop('cinquante_agents_maximum')
    rid=old['id'] if old else secrets.token_hex(32);now=desk.now()
    if old:
        suspend_children(desk,rid,owner)
        desk.db.execute("UPDATE document_rules568 SET name=?,instruction=?,recipe=?,revision=revision+1,state='draft',updated=? WHERE id=? AND revision=?",(name,text,json.dumps(r,ensure_ascii=False),now,rid,old['revision']))
        desk.db.execute("UPDATE document_runs568 SET state='paused',explanation='Règle modifiée : ancienne révision suspendue.',updated=? WHERE rule_id=? AND state NOT IN ('verified','dismissed','superseded','abstained')",(now,rid))
    else:desk.db.execute('INSERT INTO document_rules568 VALUES(?,?,?,?,?,?,?,?,?,?)',(rid,owner,'custom-'+rid,name,text,json.dumps(r,ensure_ascii=False),1,'draft',now,now))
    desk.db.commit();desk.audit('agent_document568_enregistre',{'id':rid,'owner':owner,'state':'draft'})
    return _rule(desk,rid,owner)


def control(desk,data,owner):
    row=_rule(desk,data.get('id',''),owner);action=data.get('action')
    if action not in ('activate','pause','delete'):raise Stop('action_agent_invalide')
    if action=='activate':
        if data.get('revision')!=row['revision'] or data.get('approved') is not True:raise Stop('revue_agent_requise')
        settings568.check_owner(desk,owner)
    state={'activate':'active','pause':'paused','delete':'deleted'}[action]
    if action!='activate':suspend_children(desk,row['id'],owner)
    desk.db.execute('UPDATE document_rules568 SET state=?,updated=? WHERE id=?',(state,desk.now(),row['id']))
    if action!='activate':desk.db.execute("UPDATE document_runs568 SET state='paused',explanation='Agent désactivé.',updated=? WHERE rule_id=? AND state IN ('queued','running','waiting','decision')",(desk.now(),row['id']))
    desk.db.commit();desk.audit('agent_document568_etat',{'id':row['id'],'state':state});return {'id':row['id'],'state':state}


def suspend_children(desk,rid,owner):
    from .plans568 import control as plan_control
    for row in desk.db.execute("SELECT e.proof FROM document_effects568 e JOIN document_runs568 r ON r.id=e.run_id WHERE r.rule_id=? AND r.owner=? AND e.step='response_conclusions' AND e.state='queued'",(rid,owner)).fetchall():
        plan_control(desk,{'id':json.loads(row['proof'])['plan_id'],'action':'pause'},owner)


def compile_instruction(desk,text,owner,model=None):
    """Interprétation unique, sans exécution. La recette complète reste à approuver."""
    text=str(text or '').strip()
    if not 10<=len(text)<=6000:raise Stop('instruction_agent_invalide')
    # Vocabulaire déterministe pour les exemples courants, pas de généralisation silencieuse.
    f=normalized(text);unsupported=[]
    for word in ('envoyer automatiquement','supprimer le fichier','facturer','signer','deposer au greffe','executer du code'):
        if word in f:unsupported.append(word)
    names=re.findall(r'["«“]([^"»”\n]{2,100})["»”]',text)
    names=[x for x in names if normalized(x)!='procedure']
    types=[kind for kind,_,aliases,kinds in TEMPLATES if any(normalized(a) in f for a in aliases)]
    types=list(dict.fromkeys(t for k in types for t in next(x[3] for x in TEMPLATES if x[0]==k)))
    age=re.search(r'(?:moins de|depuis|maximum|max|inferieur a)\s+(\d{1,2})\s+jours?',f)
    actions=['analyze']
    for terms,key in [(('classer','placer','procedure','ranger'),'file_procedure'),(('agenda','evenement','calendrier'),'calendar'),(('brouillon','draft','mail d information'),'inform_draft'),(('tache','prochaine action'),'next_task'),(('projet de conclusions','conclusions en reponse'),'response_conclusions')]:
        if any(t in f for t in terms):actions.append(key)
    base={'names':names or [a for k,_,aliases,_ in TEMPLATES if k in types for a in aliases], 'types':types,
          'age_days':int(age[1]) if age else 10,'clock':'modified' if 'modifi' in f else 'created',
          'actions':actions,'mode':'observe' if 'seulement proposer' in f else ('organize' if 'inscrire' in f or 'placer' in f else 'prepare'),
          'guidance':text}
    warnings=[]
    if not base['names'] and not base['types'] and not unsupported:
        from .model import Model,CHAT,validate
        from .model import routed_config
        m=model or Model(routed_config(desk.c,'assistant'))
        prompt={'question_avocat':'Transforme cette règle utilisateur en un objet JSON (uniquement dans answer), sans outil ni exécution. '
                'Champs autorisés : names (mots du nom), types parmi '+', '.join(sorted(TYPES))+', age_days 1..90, '
                'clock created/modified, actions parmi '+', '.join(sorted(ACTIONS))+', mode observe/prepare/organize, '
                'recipient case_role/self, folder parmi '+', '.join(FOLDERS)+', guidance. Ne convertir aucun envoi, signature, suppression ou dépôt judiciaire. '
                'En cas de demande non représentable répondre {"unsupported": ["motif"]}. Instruction : '+text,
                'sources':[],'historique_non_probant':[],'dossier':None}
        out=m.ask('chat',prompt);validate(out,CHAT)
        try:base=json.loads(out['answer'])
        except (ValueError,TypeError):raise Stop('compilation_agent_invalide') from None
        if base.get('unsupported'):unsupported=base['unsupported']
        else:base['guidance']=text;warnings.append('Interprétation par le modèle : relire tous les champs avant activation.')
    if unsupported:return {'unsupported':unsupported,'recipe':None,'warnings':['Ces actions ne sont pas exécutables par une règle autonome.']}
    base=validate_recipe(base)
    if not age:warnings.append('Fenêtre proposée : 10 jours. Vous pouvez la modifier.')
    if base['clock']=='created':warnings.append('Sans date de création WebDAV prouvée, la source reste à confirmer ; aucune date de création n’est inventée.')
    return {'recipe':base,'summary':recipe_text(base),'warnings':warnings,'unsupported':[]}


def classify(text):
    # L'intitulé en tête prime sur une pièce simplement citée plus loin. Ce
    # classifieur lexical reste explicable ; les effets exigeant des dates ont
    # leurs contrôles indépendants et ne font pas confiance à cette étiquette.
    f=normalized(str(text)[:1800]);choices=[]
    for kind,words in [('renvoi',('avis de renvoi',)),('soit_transmis',('soit transmis',)),('injonction',('injonction',)),
       ('appel',('declaration d appel','declaration appel','avis de fixation')),('conclusions',('conclusions',)),
       ('convocation',('convocation',)),('contrat',('contrat','conditions generales'))]:
        for word in words:
            match=re.search(r'\b'+re.escape(word)+r'\b',f)
            if match:choices.append((match.start(),kind))
    return min(choices)[1] if choices else 'autre'


def stamp(meta,clock):
    value=meta.get('created' if clock=='created' else 'modified','')
    try:
        try:d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        except ValueError:d=parsedate_to_datetime(str(value))
        if not d or d.tzinfo is None:return None
        return d.astimezone(timezone.utc)
    except (TypeError,ValueError,IndexError):return None


def match(recipe,path,meta,text=None,now=None):
    r=validate_recipe(recipe);now=now or datetime.now(timezone.utc);when=stamp(meta,r['clock'])
    if when is None:return 'age_unknown'
    if when>now+timedelta(minutes=5) or when<now-timedelta(days=r['age_days']):return 'out_of_window'
    if any(normalized(x) in normalized(PurePosixPath(path).stem) for x in r['names']):return 'match'
    if text is None:return 'type_pending' if r['types'] else 'no_match'
    return 'match' if classify(text) in r['types'] else 'no_match'


def observe(desk,matter,items,owner=None,limit=10):
    """Réserve les fichiers avant les anciens automatismes pour éviter leur duplication."""
    owners=[owner] if owner else settings568.owners(desk);managed=set();count=0
    for user in owners:
        if not settings568.profile(desk,user)['enabled']:continue
        rows=desk.db.execute("SELECT * FROM document_rules568 WHERE owner=? AND state IN ('active','draft','paused','deleted') ORDER BY created,id",(user,)).fetchall()
        for path,meta in sorted(items.items()):
            if PurePosixPath(path).suffix.lower() not in ('.pdf','.docx','.odt','.txt','.png','.jpg','.jpeg','.tif','.tiff'):continue
            if desk.db.execute("SELECT 1 FROM document_effects568 WHERE step='file_procedure' AND state='verified' AND json_extract(proof,'$.destination')=?",(path,)).fetchone():managed.add(path);continue
            # Un nom explicite est prioritaire sur un type encore inconnu. Si seul
            # le contenu permettra de choisir, le worker raccorde la bonne règle
            # après extraction et avant tout effet.
            ordered=sorted(rows,key=lambda x:(not any(normalized(n) in normalized(PurePosixPath(path).stem) for n in json.loads(x['recipe'])['names']),x['state']!='active'))
            for raw in ordered:
                row=dict(raw);r=json.loads(row['recipe'])
                if r['scope'] and matter and r['scope']!=str(matter.get('id','')):continue
                # Les modèles supprimés gardent un masque sur les anciens automatismes,
                # mais ne déclenchent plus aucune tâche.
                candidate=any(normalized(x) in normalized(PurePosixPath(path).stem) for x in r['names'])
                state=match(r,path,meta)
                if candidate:managed.add(path)
                if row['state']!='active' or state in ('out_of_window','no_match') or (state=='age_unknown' and not candidate):continue
                managed.add(path)
                mid=str((matter or {}).get('id','') or r['scope']);rid=digest('|'.join([user,row['id'],str(row['revision']),path,str(meta.get('etag',''))]))
                if desk.db.execute('SELECT 1 FROM document_runs568 WHERE id=?',(rid,)).fetchone():break
                now=desk.now();status='decision' if state=='age_unknown' else 'queued'
                cur=desk.db.execute('INSERT OR IGNORE INTO document_runs568 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (rid,user,row['id'],row['revision'],mid,path,str(meta.get('etag','')),json.dumps(meta),status,'{}',
                   'Date de création WebDAV absente : confirmer la date ou choisir modification dans la règle.' if status=='decision' else '',None,now,now))
                desk.db.commit()
                if cur.rowcount:count+=1
                if cur.rowcount and status=='queued':
                    try:job=desk.enqueue('document568_run',{'run':rid,'owner':user},priority=25);desk.db.execute('UPDATE document_runs568 SET job_id=? WHERE id=?',(job,rid));desk.db.commit()
                    except Stop as ex:desk.db.execute("UPDATE document_runs568 SET state='error',explanation=? WHERE id=?",(str(ex),rid));desk.db.commit()
                break # une recette prioritaire par fichier, choix explicable dans le résultat
            if count>=limit:break
        if count>=limit:break
    return managed


def runs(desk,owner,prefix=''):
    labels={str(m['id']):matter_display(m) for m in load_matters(desk.c)};out=[]
    for raw in desk.db.execute('SELECT r.*,d.name AS rule_name FROM document_runs568 r JOIN document_rules568 d ON d.id=r.rule_id WHERE r.owner=? ORDER BY r.updated DESC LIMIT 80',(owner,)):
        r=dict(raw);r['result']=json.loads(r['result']);r['metadata']=json.loads(r['metadata']);r['matter_label']=labels.get(r['matter'],'Dossier à préciser')
        r['effects']=[{**dict(e),'proof':json.loads(e['proof'])} for e in desk.db.execute('SELECT * FROM document_effects568 WHERE run_id=? ORDER BY updated,step',(r['id'],))]
        r['open_url']=prefix+'/matter?id='+r['matter'] if r['matter'] else ''
        out.append(r)
    return out


def run_control(desk,data,owner):
    row=desk.db.execute('SELECT * FROM document_runs568 WHERE id=? AND owner=?',(str(data.get('id') or ''),owner)).fetchone()
    if not row:raise Stop('execution_agent_absente')
    action=data.get('action')
    if action=='dismiss':
        effect=desk.db.execute("SELECT proof FROM document_effects568 WHERE run_id=? AND step='response_conclusions' AND state='queued'",(row['id'],)).fetchone()
        if effect:
            from .plans568 import control as plan_control
            plan_control(desk,{'id':json.loads(effect['proof'])['plan_id'],'action':'pause'},owner)
        desk.db.execute("UPDATE document_runs568 SET state='dismissed',explanation='Écarté par l’utilisateur.',updated=? WHERE id=?",(desk.now(),row['id']));desk.db.commit();return {'state':'dismissed'}
    if action not in ('retry','resolve'):raise Stop('action_agent_invalide')
    if row['state'] in ('running','queued','verified','superseded','dismissed'):raise Stop('execution_non_relancable')
    rule=_rule(desk,row['rule_id'],owner)
    if rule['state']!='active' or rule['revision']!=row['revision']:raise Stop('regle_inactive_ou_modifiee')
    meta=json.loads(row['metadata']);matter=str(data.get('matter') or row['matter'])
    if matter and matter not in {str(m['id']) for m in load_matters(desk.c)}:raise Stop('dossier_absent')
    if data.get('created'):
        value=str(data['created']);when=stamp({'created':value},'created')
        if not when:raise Stop('date_creation_invalide')
        meta['created']=when.isoformat();meta['created_proof']='Confirmation utilisateur : '+str(data.get('proof') or '')[:600]
        if len(str(data.get('proof') or '').strip())<5:raise Stop('preuve_execution_requise')
    desk.db.execute("UPDATE document_runs568 SET matter=?,metadata=?,state='queued',explanation='',updated=? WHERE id=?",(matter,json.dumps(meta),desk.now(),row['id']));desk.db.commit()
    job=desk.enqueue('document568_run',{'run':row['id'],'owner':owner},priority=5)
    desk.db.execute('UPDATE document_runs568 SET job_id=? WHERE id=?',(job,row['id']));desk.db.commit();return {'state':'queued','job':job}


def incoming_scan(desk,owner):
    from .dav import DAV
    client=DAV(desk.c['nextcloud']);paths=desk.c.get('document_agents568',{}).get('incoming_paths',[]);changed=0
    for path in paths[:10]:
        if not any(under(path,root) for root in desk.c['nextcloud']['roots']):raise Stop('fichier_hors_racines')
        key='document568:scan:'+owner+':'+digest(path)
        scan,done=client.inventory_step(path,desk.settings(key,None));desk.setting(key,None if done else scan)
        if done:changed+=len(observe(desk,None,scan['files'],owner))
    return {'candidate_paths':changed,'remote_write':False}
