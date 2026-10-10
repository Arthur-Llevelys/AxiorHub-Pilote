#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import shutil
import sys

from agent.common import (Stop, load_config, load_matters, indexable_matters,
                          matter_scope_conflicts, private_json, HTTP)
from agent.state import State


def doctor(c):
    from agent.mailbox import Mailbox
    from agent.dav import DAV
    from agent.model import Model
    checks = {}
    box = None
    try:
        box = Mailbox(c['mail'])
        for folder in [c['mail']['inbox'],c['mail']['sent'],c['mail']['drafts']]: box.select(folder)
        checks['imap'] = 'OK, dossiers accessibles en lecture'
    except Stop as e: checks['imap'] = str(e)
    finally:
        if box: box.close()
    try:
        dav = DAV(c['nextcloud'])
        for root in c['nextcloud']['roots']: dav.list_folder(root)
        checks['nextcloud'] = 'OK'
        now = datetime.now(timezone.utc)
        events = dav.events(c['calendar']['urls'],now,now+timedelta(days=10),c['calendar']['timezone'])
        checks['calendar'] = 'OK, '+str(len(events))+' événements développés'
    except Stop as e: checks['nextcloud_calendar'] = str(e)
    workflow=c.get('nextcloud_workflow') or {}
    if workflow.get('enabled'):
        try:
            workdav=DAV(workflow)
            listed=workdav.calendars()
            selected=set(workflow.get('calendar_read_urls',[])+[workflow.get('task_calendar_url',''),workflow.get('planning_calendar_url','')])-{''}
            visible={x['url'] for x in listed}
            if not selected or not selected.issubset(visible):raise Stop('calendrier_axiorhub_non_visible')
            todos=workdav.todos([workflow['task_calendar_url']],c['calendar']['timezone'],10)
            checks['nextcloud_workflow']='OK, compte '+workflow['username']+', '+str(len(todos))+' tâche(s) contrôlée(s)'
        except Stop as e:checks['nextcloud_workflow']=str(e)
    try:
        model = Model(c['ollama'])
        result = model.ask('triage',{'incoming':{'sender':'newsletter@example.test','subject':'Offres promotionnelles',
                           'text':'Publicité : découvrez nos promotions. Se désabonner.'},'history':[]})
        checks['ollama'] = 'OK, JSON valide' if not result['needs_reply'] else 'JSON valide, classement du témoin à vérifier'
    except Stop as e: checks['ollama'] = str(e)
    from agent.ai_gateway import provider_registry
    from agent.model import provider_diagnostic
    checks['ai_providers']={}
    for provider_id,provider in provider_registry(c).items():
        if provider_id=='ollama':
            checks['ai_providers'][provider_id]={'status':'ok' if str(checks.get('ollama','')).startswith('OK') else 'error',
                                                'message':checks.get('ollama','')}
        else:
            checks['ai_providers'][provider_id]=provider_diagnostic(provider)
    checks['lawve_extensions']={identifier:{
      'kind':item.get('kind'),'status':item.get('status'),'enabled':item.get('enabled',False),
      'last_test':{key:(item.get('last_test') or {}).get(key) for key in ('status','message','error','tools_count','at')
                   if key in (item.get('last_test') or {})}}
      for identifier,item in (c.get('lawve_extensions') or {}).items() if isinstance(item,dict)}
    autonomy=c.get('autonomy') or {}
    if autonomy.get('enabled') and autonomy.get('document_control_enabled'):
        try:
            from agent.model import routed_config
            control_cfg=routed_config(c,'control')
            Model(control_cfg)
            checks['document_control']='OK, modèle local '+control_cfg['model']
        except Stop as e:checks['document_control']=str(e)
    if autonomy.get('enabled'):
        from agent.model import routed_config
        checks['model_routing']={role:routed_config(c,role)['model'] for role in ('fast','complex','control')}
    legal=c.get('legal_research') or {}
    providers=legal.get('providers') or {}
    checks['legal_research']={
      'anonymization':'enabled' if legal.get('enabled',True) else 'disabled',
      'configured_discovery_providers':sorted(name for name,value in providers.items()
        if isinstance(value,dict) and value.get('enabled') and value.get('url')),
      'official_hosts':len(legal.get('official_hosts') or []),
      'deterministic_document_control':bool(c.get('document_projects',{}).get('deterministic_control_enabled',True)),
      'mcp_import':'prepared-query required',
    }
    if c.get('rag',{}).get('enabled'):
        try:
            from agent.rag import Embedder
            Embedder(c['ollama'],c['rag']).embed(['test de disponibilité'])
            checks['embeddings']='OK, modèle local '+c['rag']['embedding_model']
        except Stop as e:checks['embeddings']=str(e)
    checks['extractors'] = {x:bool(shutil.which(x)) for x in ['pdftotext','pdfinfo','pdftoppm','tesseract']}
    all_matters=load_matters(c);conflicts=matter_scope_conflicts(all_matters)
    checks['matters'] = len(all_matters)
    checks['matter_scope']={'indexable':len(indexable_matters(c)),'blocked_parents':conflicts}
    checks['mode'] = c['mode']
    print(json.dumps(checks,ensure_ascii=False,indent=2))
    required=(str(checks.get('imap','')).startswith('OK') and checks.get('nextcloud')=='OK' and
              str(checks.get('calendar','')).startswith('OK') and str(checks.get('ollama','')).startswith('OK'))
    if workflow.get('enabled'):required=required and str(checks.get('nextcloud_workflow','')).startswith('OK')
    if c.get('rag',{}).get('enabled'):required=required and str(checks.get('embeddings','')).startswith('OK')
    if autonomy.get('enabled') and autonomy.get('document_control_enabled'):
        required=required and str(checks.get('document_control','')).startswith('OK')
    return 0 if required else 2


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description='Assistant de courriels AxiorHub — brouillons uniquement')
    p.add_argument('--config', default='/etc/axiorhub-mail-agent/config.json')
    sub = p.add_subparsers(dest='command',required=True)
    for cmd in ['configure','doctor','readiness','run','discover','link','index','status','purge-index','learn','memory-status','mail-folders','draft-status','recette-production']: sub.add_parser(cmd)
    set_url=sub.add_parser('set-url')
    set_url.add_argument('key',choices=['roundcube','roundcube_drafts','openwebui','nextcloud','onlyoffice','invoice_ninja'])
    set_url.add_argument('value')
    mode = sub.add_parser('mode'); mode.add_argument('value',choices=['observe','drafts'])
    report = sub.add_parser('report'); report.add_argument('key',nargs='?')
    forget = sub.add_parser('memory-forget'); forget.add_argument('key')
    retry = sub.add_parser('retry'); retry.add_argument('key')
    cleanup = sub.add_parser('cleanup'); cleanup.add_argument('--days',type=int,default=30)
    args = p.parse_args()
    if args.command=='configure':
        from agent.setup import configure
        configure(args.config); return 0
    c = load_config(args.config)
    if args.command=='doctor': return doctor(c)
    if args.command=='readiness':   # 5.6.9 : mise en service, même rapport que l'écran Outils › Mise en service
        from agent.desk import Desk
        from agent.readiness569 import run, text
        print(text(run(Desk(c))));return 0
    if args.command=='recette-production':   # 5.6.13 : recette reelle (donnees fictives, services reels), meme rapport que Mise en service
        from agent.desk import Desk
        from agent.recette5613 import run
        report=run(Desk(c))
        for s in report['steps']:print(('OK  ' if s['ok'] else 'N/A ' if s['ok'] is None else 'KO  ')+s['label']+' : '+s['message'])
        return 0 if report['ok'] else 1
    if args.command=='set-url':
        from agent.desk import Desk
        from agent.workstation import save_external_url
        print(json.dumps(save_external_url(Desk(c),args.key,args.value),ensure_ascii=False,indent=2));return 0
    if args.command=='draft-status':
        from agent.desk import Desk
        from agent.mailbox import Mailbox
        desk=Desk(c);box=None
        counts={'folder':c['mail']['drafts'],
          'verified_by_axiorhub':desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready' AND source_status='drafted'").fetchone()[0],
          'prepared_locally':desk.db.execute('SELECT COUNT(*) FROM manual_drafts').fetchone()[0],
          'uncertain':State(c['state_dir']).counts().get('append_uncertain',0)}
        try:
            box=Mailbox(c['mail']);counts['imap_messages']=len(box.search(c['mail']['drafts'],'ALL'))
            counts['imap_access']='OK'
        except Stop as exc:counts['imap_access']=str(exc)
        finally:
            if box:box.close()
        print(json.dumps(counts,ensure_ascii=False,indent=2));return 0
    if args.command=='mail-folders':
        from agent.mailbox import Mailbox, imap_quote, modified_utf7
        from agent.setup import mailbox_names
        import re
        box=Mailbox(c['mail'])
        try:
            result=[]
            for name,flags in mailbox_names(box)[:100]:
                if '\\noselect' in flags: continue
                status,data=box.conn.status(imap_quote(modified_utf7(name)), '(MESSAGES UNSEEN)')
                raw=b' '.join(x for x in (data or []) if isinstance(x,bytes)).decode(errors='replace')
                total=re.search(r'MESSAGES (\d+)',raw)
                unseen=re.search(r'UNSEEN (\d+)',raw)
                result.append({'folder':name,'flags':flags,'status':status,
                               'messages':int(total[1]) if total else None,
                               'unseen':int(unseen[1]) if unseen else None})
            print(json.dumps(result,ensure_ascii=False,indent=2))
        finally: box.close()
        return 0
    if args.command=='mode':
        from agent.setup import save_admin
        c.pop('_path',None); c['mode']=args.value
        save_admin(args.config,c)
        print('Mode enregistré : '+args.value); return 0
    if args.command in ('discover','link'):
        from agent import setup
        getattr(setup,args.command)(c); return 0
    state = State(c['state_dir'])
    if args.command in ('learn','memory-status','memory-forget'):
        from agent.memory import SentMemory
        from agent.mailbox import Mailbox
        from agent.portable import fcntl
        memory = SentMemory(c)
        if args.command=='memory-status':
            print(json.dumps(memory.status(),ensure_ascii=False,indent=2)); return 0
        with open(Path(c['state_dir'])/'run.lock','a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Stop('traitement_deja_en_cours') from None
            if args.command=='memory-forget':
                memory.forget(args.key); print('Exemple effacé et réimportation bloquée.'); return 0
            if not c.get('memory',{}).get('enabled'): raise Stop('memoire_desactivee')
            box=Mailbox(c['mail'])
            try: result=memory.collect(box,load_matters(c))
            finally: box.close()
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    if args.command=='purge-index':
        from agent.portable import fcntl
        from agent.index import DocumentIndex
        with open(Path(c['state_dir'])/'run.lock','a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Stop('traitement_deja_en_cours') from None
            index=DocumentIndex(c['state_dir'],c.get('rag'),c.get('ollama'))
            for table in ('docs','search','knowledge_chunks','knowledge_fts','knowledge_embeddings'):
                index.db.execute('DELETE FROM '+table)
            index.db.commit()
        print('Index documentaire local vidé. Sources Nextcloud conservées.');return 0
    if args.command=='run':
        from agent.engine import Engine
        print(json.dumps(Engine(c,state=state).run(),ensure_ascii=False)); return 0
    if args.command=='index':
        from agent.portable import fcntl
        from agent.index import DocumentIndex
        from agent.dav import DAV
        with open(Path(c['state_dir'])/'run.lock','a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Stop('traitement_deja_en_cours') from None
            dav, index = DAV(c['nextcloud']),DocumentIndex(c['state_dir'],c.get('rag'),c.get('ollama'))
            blocked=matter_scope_conflicts(load_matters(c))
            for conflict in blocked:
                print(conflict['parent']+' : ignoré, périmètre parent de '+
                      ', '.join(conflict['children'])+'.',flush=True)
            for m in indexable_matters(c):
                if not m.get('correspondents'): continue
                processed=0;pages=0
                while pages<4:
                    items,_ = index.sync_page(dav,m,c['documents'],250)
                    processed+=len(items);pages+=1
                    if index.last_page_state['complete']:break
                state_page=index.last_page_state
                suffix='inventaire terminé' if state_page['complete'] else 'reprise automatique au prochain passage'
                print(m['id']+' : '+str(processed)+' fichiers traités ce passage ; '+
                      str(state_page['seen'])+' vus dans le cycle ; '+suffix+'.',flush=True)
        return 0
    if args.command=='status':
        print(json.dumps({'mode':c['mode'],'counts':state.counts()},ensure_ascii=False,indent=2)); return 0
    if args.command=='report':
        if args.key:
            if not __import__('re').fullmatch('[0-9a-f]{64}',args.key): raise Stop('cle_invalide')
            print((Path(c['state_dir'])/'reports'/(args.key+'.json')).read_text())
        else:
            for key,status,reason,stamp in state.rows():
                path=Path(c['state_dir'])/'reports'/(key+'.json')
                meta=json.loads(path.read_text()) if path.exists() else {}
                print(key+'\n  '+status+' — '+reason+'\n  '+meta.get('subject','')+'\n')
        return 0
    if args.command=='retry':
        print(str(state.reset_review(args.key))+' message remis en attente. Les messages lus resteront exclus.'); return 0
    if args.command=='cleanup':
        if args.days<1: raise Stop('retention_invalide')
        threshold=(datetime.now(timezone.utc)-timedelta(days=args.days)).timestamp()
        count=0
        for path in (Path(c['state_dir'])/'reports').glob('*.json'):
            if path.stat().st_mtime < threshold: path.unlink();count+=1
        print(str(count)+' rapports locaux expirés supprimés ; journal de déduplication conservé.'); return 0


if __name__=='__main__':
    try: sys.exit(main() or 0)
    except Stop as e: print('ARRÊT : '+str(e),file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt: print('Interrompu.',file=sys.stderr);sys.exit(130)
    except Exception:
        print('ARRÊT : erreur de configuration ou de service. Exécuter diagnostic.py puis doctor ; aucun détail secret n’est affiché.',file=sys.stderr)
        sys.exit(3)
