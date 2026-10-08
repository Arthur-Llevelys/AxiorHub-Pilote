"""Cabinet operating layer, ecosystem API and licensing for AxiorHub 5.0.0."""
import json
from pathlib import Path
import unittest

from agent.api import dispatch, openapi
from agent.common import Stop, digest
from agent.desk import Desk
from agent.operating380 import (action_center, advance_playbook,
  complete_ecosystem_event, matter_graph, playbook_run, playbooks,
  prepare_ecosystem_action, refresh_matter_graph, register_service,
  relevance_metrics, run_evaluation, services, start_playbook)
import test_agent as fixtures
import test_desk


class Operating380(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_today_groups_actionable_signals_and_explains_sources(self):
        stamp=self.d.now();sid=digest('deadline')
        self.d.db.execute('INSERT INTO proactive_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (sid,'DOS-001','deadline','critical','Échéance demain','Conclusions à relire',
           json.dumps(['calendar:event-1']),'Ouvrir le dossier','open','2026-09-28',
           digest('deadline-source'),stamp,stamp,''))
        self.d.db.commit();today=action_center(self.d)
        self.assertEqual(today['counts']['urgent'],1)
        self.assertEqual(today['cards'][0]['source_ids'],['calendar:event-1'])
        self.assertIn('sources originales',today['warning'])

    def test_graph_keeps_provenance_and_explicit_relationships(self):
        stamp=self.d.now();rid=digest('record')
        self.d.db.execute('INSERT INTO legal_memory_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (rid,'DOS-001','claim','Créance exigible','La créance est exigible.','Client','2026-09-01',
           .9,'validated',json.dumps(['doc:conclusions:p12']),json.dumps([]),'test',1,'',stamp,stamp,stamp,'test'))
        self.d.db.commit();refresh_matter_graph(self.d,'DOS-001');graph=matter_graph(self.d,'DOS-001')
        self.assertEqual(graph['counts']['claim'],1);self.assertEqual(graph['counts']['evidence'],1)
        self.assertTrue(any(x['relation']=='supports' for x in graph['edges']))
        claim=next(x for x in graph['nodes'] if x['node_kind']=='claim')
        self.assertEqual(claim['source_ids'],['doc:conclusions:p12'])

    def test_four_built_in_playbooks_reuse_the_job_queue(self):
        catalogue=playbooks(self.d)
        self.assertEqual({x['family'] for x in catalogue},{'contentieux','contrats'})
        self.assertEqual(len(catalogue),4)
        run=start_playbook(self.d,'contract_review_v1','DOS-001','Relire les clauses de résiliation')
        state=advance_playbook(self.d,run['run_id'])
        self.assertEqual(state['steps'][0]['status'],'queued')
        self.assertEqual(self.d.db.execute('SELECT kind FROM jobs').fetchone()[0],'index')

    def test_ecosystem_registry_never_returns_secret_and_requires_review(self):
        with self.assertRaisesRegex(Stop,'revue_service_requise'):
            register_service(self.d,'lexdelai','LexDélai','https://lexdelai.example/api',
              ['calculate_deadline'],'vault:lexdelai',True,False)
        register_service(self.d,'lexdelai','LexDélai','https://lexdelai.example/api',
          ['calculate_deadline'],'vault:lexdelai',True,True)
        public=services(self.d)[0]
        self.assertNotIn('secret_ref',public);self.assertTrue(public['secret_configured'])
        event=prepare_ecosystem_action(self.d,'lexdelai','DOS-001','calculate_deadline',{'start':'2026-09-27'})
        result=complete_ecosystem_event(self.d,event['event_id'],'done',{'deadline':'2026-10-27'})
        self.assertEqual(result['status'],'done')

    def test_evaluation_bench_is_reproducible_and_exposes_denominators(self):
        result=run_evaluation(self.d);metrics=relevance_metrics(self.d)
        self.assertEqual(len(result['results']),5);self.assertEqual(result['score'],100.0)
        self.assertIn('sample_sizes',metrics);self.assertIn('limits',metrics)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM evaluation_runs_v380').fetchone()[0],1)

    def test_shared_api_exposes_the_five_deliverables(self):
        schema=openapi('https://agent.example.com')
        self.assertEqual(schema['info']['version'],'5.6.14')
        for path in ('/operating/today','/playbooks','/matters/{matter_id}/graph',
                     '/ecosystem/actions','/evaluations','/relevance/metrics'):
            self.assertIn(path,schema['paths'])
        self.assertEqual(len(dispatch(self.d,'/playbooks','GET')['playbooks']),4)
        self.assertIn('counts',dispatch(self.d,'/operating/today','GET'))


class Browser380(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_operating_pages_are_real_and_keep_the_transversal_assistant(self):
        expected={'/aujourdhui':'Aujourd’hui','/playbooks':'Playbooks',
          '/ecosysteme':'Écosystème du cabinet','/evaluations':'Banc d’évaluation métier'}
        for path,label in expected.items():
            body=self.request(path)['body']
            self.assertIn(label,body,path);self.assertIn('ws-ai-launcher',body,path)
            self.assertIn('app520.css',body,path)

    def test_matter_has_six_work_sections_and_argument_graph(self):
        body=self.request('/matter',query='id=DOS-001')['body']
        for label in ('Vue d’ensemble','Chronologie','Arguments et preuves','Travail','Documents','Honoraires'):
            self.assertIn(label,body)
        self.assertIn('refresh_matter_graph380',body)


class PublicPackage380(unittest.TestCase):
    def test_agpl_brand_logo_notices_and_sbom_are_present(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('LICENSE','TRADEMARKS.md','LOGO-LICENSE.md','THIRD_PARTY_NOTICES.md','SBOM.cdx.json'):
            self.assertTrue((root/name).is_file(),name)
        self.assertIn('GNU AFFERO GENERAL PUBLIC LICENSE',(root/'LICENSE').read_text())
        self.assertIn('AGPL-3.0-or-later',(root/'THIRD_PARTY_NOTICES.md').read_text())
        sbom=json.loads((root/'SBOM.cdx.json').read_text())
        self.assertEqual(sbom['bomFormat'],'CycloneDX');self.assertEqual(sbom['metadata']['component']['version'],'5.6.14')


if __name__=='__main__':unittest.main()
