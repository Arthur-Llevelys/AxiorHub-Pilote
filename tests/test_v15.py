import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from io import BytesIO

import test_agent as fixtures
import test_desk
from agent.common import Stop,private_json
from agent.documents import extract
from agent.index import DocumentIndex
from agent.matcher import rank
from agent.memory import SentMemory
from agent.operations import exceptions


class Matcher15Tests(unittest.TestCase):
    def test_scores_are_explainable_and_unknown_role_stays_blocked(self):
        m=fixtures.mail(sender='unknown@client.test',subject='Référence DOS-001')
        matters=[{'id':'DOS-001','client_name':'Client','path':'/Dossiers/A','references':['DOS-001'],
                  'correspondents':[]}]
        found,role,rows=rank(m,matters,evidence={'DOS-001':[{'signal':'adresse_dans_document','weight':10,'value':m.sender}]})
        self.assertEqual(found['id'],'DOS-001');self.assertIsNone(role)
        self.assertGreaterEqual(rows[0]['score'],95)
        self.assertIn('adresse_dans_document',{x['signal'] for x in rows[0]['reasons']})

    def test_conflicting_explicit_references_never_auto_match(self):
        m=fixtures.mail(subject='DOS-001 contre DOS-002')
        matters=[{'id':x,'client_name':x,'path':'/Dossiers/'+x,'references':[x],
                  'correspondents':[]} for x in ('DOS-001','DOS-002')]
        self.assertIsNone(rank(m,matters)[0])


class Rag15Tests(unittest.TestCase):
    def test_knowledge_is_scoped_and_embedding_failure_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            index=DocumentIndex(td,{'enabled':True,'embedding_model':'local'}, {'url':'http://127.0.0.1:11434'})
            a={'id':'A'};b={'id':'B'}
            index.put_source(a,'/A/contrat.txt','rupture de la relation commerciale','1')
            index.put_source(b,'/B/secret.txt','SECRET AUTRE DOSSIER','1')
            with patch.object(index,'embedder',side_effect=Stop('embeddings_indisponibles')):
                rows,error=index.ranked_chunks(a,['rupture relation'],10)
            self.assertTrue(error);self.assertTrue(rows)
            self.assertNotIn('SECRET AUTRE DOSSIER',json.dumps([list(x[0]) for x in rows]))

    def test_xlsx_cells_are_extracted_without_macros(self):
        raw=BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Montant</t></si></sst>')
            z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="s"><v>0</v></c><c><v>21757.28</v></c></row></sheetData></worksheet>')
        text=extract(raw.getvalue(),'tableau.xlsx',{'max_document_chars':10000})
        self.assertIn('Montant',text);self.assertIn('21757.28',text)


class Memory15Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.memory=SentMemory(self.f.c);self.matter=self.f.matters[0]
        key='a'*64;binding='[["client@example.test","client"]]'
        self.memory.db.execute('INSERT INTO examples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)',
            (key,'DOS-001@'+__import__('agent.common',fromlist=['digest']).digest('/Dossiers/DEMO'),
             '["client@example.test"]','2099-01-01T00:00:00+00:00','Bonjour Maître,\nRéponse finale assez courte.',
             'Bonjour,\nAncien brouillon beaucoup plus long.','{}','matched_draft_and_sent',self.memory.account(),binding))
        self.memory.db.execute('INSERT INTO example_reviews VALUES (?,?,?)',(key,json.dumps({'result':{
            'style_preferences':['Réponses courtes'],'useful_corrections':['Supprimer les répétitions'],
            'do_not_generalize':['Montant du dossier'],'summary':'Concision'},'matter':'DOS-001','recipients':['client@example.test'],'audience':binding}),'2099-01-01'))
        self.memory.db.commit();self.key=key

    def test_confirmed_general_style_is_used_but_never_as_fact(self):
        self.memory.set_scope(self.key,'general')
        sources=self.memory.preference_sources(self.matter,['client@example.test'])
        self.assertEqual(sources[0]['kind'],'sent_preference')
        self.assertNotIn('Montant du dossier',sources[0]['guidance'])

    def test_negative_feedback_excludes_old_example(self):
        self.memory.apply_feedback(['sent-'+self.key],'fabricated_fact')
        self.assertEqual(self.memory.examples(self.matter,['client@example.test']),[])


class Interface15Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_dashboard_quality_and_new_actions_are_exposed(self):
        for path in ('/dashboard','/qualite'):
            response=self.request(path);self.assertTrue(response['status'].startswith('200'))
        self.f.model.intent='legal';key=self.f.engine.process(fixtures.mail())
        html=self.request('/mail',query='key='+key)['body']
        self.assertIn('Préparer quand même un projet prudent',html)
        self.assertIn('Marquer traité sans réponse',html)
        self.assertIn('Ne jamais répondre',html)

    def test_all_new_post_actions_accept_exact_origin_only(self):
        actions=['refresh_brief','validate_fact','pin_fact','archive_fact','attachment_review',
                 'deadline_review','confirm_event','confirm_task','ignore_deadline','prepare_draft',
                 'deposit_draft','feedback','add_rule','mark_handled','execute_actions',
                 'memory_insight','memory_scope','health','daily_digest','automation_setting','create_matter']
        for action in actions:
            # 5.2.1 : un réglage d'automatisme est appliqué immédiatement ; il doit donc nommer un automatisme valide.
            extra={'key':'sync_enabled','value':'yes'} if action=='automation_setting' else {}
            response=self.request('/action','POST',{'action':action,'csrf':'test-csrf',**extra},origin=self.origin)
            self.assertTrue(response['status'].startswith('303'),action)


if __name__=='__main__':unittest.main()
