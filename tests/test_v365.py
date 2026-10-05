"""Documents longs, MCP juridique, tâches et navigation for AxiorHub 5.0.0."""
from unittest.mock import patch
import unittest

from agent.common import Stop
from agent.desk import Desk
from agent.extensions364 import legal_connector_search
from agent.improvements36 import create_task,update_local_task,cancel_local_task
from agent.long_documents365 import analyze_pages,chunk_pages
import test_agent as fixtures
import test_desk


class ProgressiveModel:
    cfg={'provider_id':'ollama','model':'test-long'}
    def __init__(self,fail_at=0):self.calls=[];self.payloads=[];self.fail_at=fail_at;self.failed=False
    def ask(self,stage,payload):
        self.payloads.append(payload)
        self.calls.append(payload['sources'][0]['id'])
        if self.fail_at and len(self.calls)==self.fail_at and not self.failed:
            self.failed=True;raise Stop('generation_ia_incomplete')
        return {'answer':'Analyse avec citation '+payload['sources'][0]['path'],
          'source_ids':[x['id'] for x in payload['sources']],
          'limits':[],'proposed_actions':[]}


class LongDocuments365(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.d=Desk(f.c)

    def pages(self,count=8):
        return [{'page':n,'label':'p. '+str(n),'text':('Page '+str(n)+' fait montant date.\n')*40,
          'extraction':'text','citation_kind':'page'} for n in range(1,count+1)]

    def test_page_chunks_cover_every_character_and_citations_survive(self):
        pages=self.pages(3);chunks=chunk_pages(pages,1200)
        for page in pages:
            self.assertEqual(''.join(x['excerpt'] for x in chunks if x['page']==page['page']),page['text'])
        model=ProgressiveModel();rows,coverage=analyze_pages(self.d,pages,'ours-abc','/Conclusions.pdf',
          'Préparer la plaidoirie',model,60000)
        self.assertTrue(coverage['complete']);self.assertEqual(coverage['pages_analyzed'],3)
        self.assertEqual([x['label'] for x in coverage['citations']],['p. 1','p. 2','p. 3'])
        self.assertTrue(all('p. ' in row['path'] for row in rows))

    def test_failure_is_resumable_and_completed_chunks_are_cached(self):
        pages=self.pages(5);first=ProgressiveModel(fail_at=3)
        with self.assertRaisesRegex(Stop,'generation_ia_incomplete'):
            analyze_pages(self.d,pages,'opp-abc','/Adverses.pdf','Analyser',first,60000)
        second=ProgressiveModel();_,coverage=analyze_pages(
          self.d,pages,'opp-abc','/Adverses.pdf','Analyser',second,60000)
        self.assertTrue(coverage['complete']);self.assertEqual(coverage['chunks_from_cache'],2)
        self.assertLess(len(second.calls),coverage['chunks_total'])

    def test_enabled_legal_mcp_calls_only_search_tool_with_anonymous_query(self):
        item={'id':'connector-openlegi-test','kind':'connector','name':'OpenLegi','enabled':True,
          'external_data_allowed':True,'purposes':['hearing'],'endpoint':'https://example.test/mcp',
          'last_test':{'status':'ok','tool_specs':[{'name':'search_legal_decisions',
            'inputSchema':{'type':'object','properties':{'query':{'type':'string'},'limit':{'type':'integer'}}}}]}}
        self.d.c['lawve_extensions']={item['id']:item};seen=[]
        def call(_item,method,params):
            seen.append((method,params));return {'structuredContent':{'results':[{'title':'Décision'}]}}
        with patch('agent.extensions364._mcp_call',side_effect=call):
            result=legal_connector_search(self.d,'responsabilité contractuelle anonymisée',4)
        self.assertEqual(result[0]['status'],'ok')
        self.assertEqual(seen,[('tools/call',{'name':'search_legal_decisions','arguments':{
          'query':'responsabilité contractuelle anonymisée','limit':4}})])

    def test_reviewed_skill_guides_analysis_without_execution(self):
        self.d.c['lawve_extensions']={'skill-method':{'id':'skill-method','kind':'skill',
          'name':'Méthode audience','enabled':True,'purposes':['hearing'],
          'archive_sha256':'a'*64,'skill_text':'Comparer chaque prétention au dispositif.'}}
        model=ProgressiveModel();_,coverage=analyze_pages(self.d,self.pages(1),'ours-skill',
          '/Conclusions.pdf','Préparer',model,60000)
        self.assertIn('Comparer chaque prétention',model.payloads[0]['limites'])
        self.assertEqual(coverage['extensions_used'][0]['id'],'skill-method')

    def test_local_task_can_be_edited_completed_and_cancel_requires_confirmation(self):
        created=create_task(self.d,'Préparer les pièces','DOS-001','2026-10-01')
        update_local_task(self.d,created['id'],'Préparer et numéroter les pièces','DOS-001','2026-10-02','closed')
        row=self.d.db.execute('SELECT * FROM tasks WHERE id=?',(created['id'],)).fetchone()
        self.assertEqual((row['title'],row['status']),('Préparer et numéroter les pièces','closed'))
        with self.assertRaisesRegex(Stop,'confirmation_suppression'):
            cancel_local_task(self.d,created['id'],'no')


class Browser365(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_task_controls_simplified_nav_pdf_and_scroll_script_are_present(self):
        create_task(Desk(self.f.c),'Tâche contrôlable','DOS-001','2026-10-01')
        body=self.request('/taches')['body']
        for label in ('Marquer terminée','Supprimer','Modifier ou programmer','Programmer dans l’agenda'):
            self.assertIn(label,body)
        self.assertIn('Produire',body);self.assertNotIn('Outils métier',body)
        hearing=self.request('/audiences-word',query='matter=DOS-001')['body']
        self.assertIn('https://pdf.example.com/',hearing)
        self.assertIn('toutes les pages',hearing)
        script=self.request('/static/v365.js')
        self.assertEqual(script['status'],'200 OK');self.assertIn('sessionStorage',script['body'])

    def test_openapi_exposes_long_document_progress_and_365(self):
        from agent.api import openapi
        spec=openapi('https://cabinet.test')
        self.assertEqual(spec['info']['version'],'5.6.5')
        self.assertIn('/long-documents/{run_id}',spec['paths'])


if __name__=='__main__':unittest.main()
