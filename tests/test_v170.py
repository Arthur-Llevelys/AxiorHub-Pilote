import copy
import json
import unittest

import test_agent as fixtures
import test_desk
from agent.api import dispatch,openapi
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.legal_memory import (change_record,conflicts,ingest_extraction,
    memory_records,memory_sources,sync_timeline,timeline,upsert_record)
from agent.workspace import chat


def source(sid='source-1',kind='document',text='La société ALPHA demande 12 000 euros.'):
    return {'id':sid,'kind':kind,'path':'/Dossiers/ALPHA/piece.txt',
            'modified':'2026-09-09T10:00:00+00:00','excerpt':text}


def record(kind='claim',content='ALPHA demande 12 000 euros.',status='assertion',sid='source-1'):
    return {'record_type':kind,'title':'Demande de la société ALPHA','content':content,
            'actor':'Société ALPHA','event_date':'2026-09-09','confidence':'high',
            'status':status,'source_ids':[sid]}


class LegalMemory170Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_source_bound_record_and_untrusted_confirmation(self):
        extraction={'records':[record(status='confirmed')],'contradictions':[],'limits':[]}
        result=ingest_extraction(self.d,'DOS-001',extraction,[source()])
        self.assertEqual(result['records'],1)
        saved=memory_records(self.d,'DOS-001')[0]
        self.assertEqual(saved['status'],'suggested')
        self.assertEqual(saved['source_snapshot'][0]['path'],'/Dossiers/ALPHA/piece.txt')
        self.assertEqual(len(saved['source_snapshot'][0]['excerpt_sha256']),64)

    def test_human_correction_is_versioned_and_survives_rescan(self):
        rid=upsert_record(self.d,'DOS-001',record(),[source()])
        change_record(self.d,'validate_memory',{'record':rid,'matter':'DOS-001',
            'text':'ALPHA sollicite exactement 12 000 euros.','note':'Vérifié dans la pièce.'})
        upsert_record(self.d,'DOS-001',record(),[source()])
        saved=memory_records(self.d,'DOS-001')[0]
        self.assertEqual(saved['status'],'validated')
        self.assertEqual(saved['content'],'ALPHA sollicite exactement 12 000 euros.')
        self.assertGreaterEqual(saved['revision'],2)
        self.assertGreaterEqual(self.d.db.execute(
            'SELECT COUNT(*) FROM legal_memory_history WHERE record_id=?',(rid,)).fetchone()[0],1)

    def test_conflicting_singletons_are_exposed_without_deciding(self):
        a=record('case_number','RG 24/001','assertion');a['title']='Numéro de dossier'
        b=record('case_number','RG 25/002','assertion');b['title']='Numéro de dossier'
        ingest_extraction(self.d,'DOS-001',{'records':[a,b],'contradictions':[],'limits':[]},[source()])
        rows=conflicts(self.d,'DOS-001')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['record_type'],'case_number')

    def test_unified_timeline_keeps_source_paths_and_scope(self):
        index=DocumentIndex(self.f.c['state_dir'])
        index.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',('DOS-001','/Dossiers/DEMO/contrat.pdf','e1',
            '2026-09-08T09:00:00+00:00','Contrat',''));index.db.commit()
        self.d.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',('task-1','DOS-001','Conclure',
            '2026-09-20T10:00:00+00:00','open',self.d.now()));self.d.db.commit()
        sync_timeline(self.d,'DOS-001',[{'uid':'cal-1','start':'2026-09-21T09:00:00+00:00',
            'end':'2026-09-21T10:00:00+00:00','summary':'Audience DOS-001','description':'TJ Lyon'}])
        rows=timeline(self.d,'DOS-001');types={x['event_type'] for x in rows}
        self.assertTrue({'document','task','calendar'}<=types)
        doc=next(x for x in rows if x['event_type']=='document')
        self.assertEqual(doc['source_path'],'/Dossiers/DEMO/contrat.pdf')

    def test_matter_chat_receives_structured_memory_with_status(self):
        rid=upsert_record(self.d,'DOS-001',record(),[source()])
        change_record(self.d,'validate_memory',{'record':rid,'matter':'DOS-001'})
        class Model:
            calls=[]
            def ask(self,stage,payload):
                self.calls.append(copy.deepcopy(payload))
                sid=next(x['id'] for x in payload['sources'] if x['kind']=='structured_memory')
                return {'answer':'La demande enregistrée est de 12 000 euros.','source_ids':[sid],
                        'limits':['À vérifier dans la pièce.'],'proposed_actions':[]}
        model=Model();result=chat(self.d,{'matter':'DOS-001','question':'Quel montant est demandé ?'},self.f.dav,model,return_result=True)
        memory=next(x for x in model.calls[0]['sources'] if x['kind']=='structured_memory')
        self.assertIn('validé par l’avocat',memory['excerpt'])
        self.assertEqual(result['sources'][0]['id'],memory['id'])

    def test_api_exposes_memory_timeline_and_version(self):
        upsert_record(self.d,'DOS-001',record(),[source()]);sync_timeline(self.d,'DOS-001')
        self.assertEqual(openapi('https://cabinet.test')['info']['version'],'5.6.18')
        memory=dispatch(self.d,'/matters/DOS-001/memory','GET')
        events=dispatch(self.d,'/matters/DOS-001/timeline','GET')
        self.assertEqual(memory['matter'],'DOS-001');self.assertIn('records',memory)
        self.assertEqual(events['matter'],'DOS-001')


class Web170Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_matter_page_shows_structured_memory_actions_and_no_refresh(self):
        desk=Desk(json.loads(self.config.read_text()))
        upsert_record(desk,'DOS-001',record(),[source()]);sync_timeline(desk,'DOS-001')
        page=self.request('/matter',query='id=DOS-001')['body']
        self.assertIn('Mémoire juridique structurée',page)
        self.assertIn('Corriger avant confirmation',page)
        self.assertIn('Chronologie unifiée',page)
        self.assertNotIn('http-equiv="refresh"',page)


if __name__=='__main__':unittest.main()
