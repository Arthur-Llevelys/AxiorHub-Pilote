"""Apprentissage métier, banc juridique et interface simplifiée AxiorHub 4.1."""
import json
import re
import unittest

from agent.api import dispatch, openapi
from agent.common import Stop
from agent.desk import Desk
from agent.evaluation410 import save_case, score_response
from agent.integration import submit_question
from agent.learning410 import applicable_context, record_review, save_rule, snapshot, trust_document
import test_agent as fixtures
import test_desk


class Learning410Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_rules_are_scoped_versioned_and_injected_without_training(self):
        first=save_rule(self.d,'cabinet','','mail_drafting','subject',
          'Toujours rappeler le numéro de dossier dans l’objet.','manual','test')
        exact=save_rule(self.d,'matter','DOS-001','mail_drafting','recipient',
          'Ne jamais écrire directement au client adverse.','manual','test')
        context=applicable_context(self.d,'DOS-001','mail_drafting')
        self.assertEqual([x['id'] for x in context['rules']],[exact['id'],first['id']])
        self.assertNotIn('training',json.dumps(context).lower())
        self.assertEqual(snapshot(self.d)['active_rules'],2)

    def test_review_records_paragraph_diff_and_rejection_reason(self):
        changed=record_review(self.d,'output-1','DOS-001','mail_drafting','modified',
          'Premier paragraphe.\n\nAncien paragraphe.',
          'Premier paragraphe.\n\nNouveau paragraphe.',rule={'scope':'matter',
          'scope_value':'DOS-001','purpose':'mail_drafting','rule_type':'style',
          'instruction':'Pour ce dossier, employer une formulation courte.'})
        self.assertEqual(changed['added_paragraphs'],1);self.assertEqual(changed['deleted_paragraphs'],1)
        record_review(self.d,'output-2','','document_drafting','rejected',rejection_reason='Source juridique absente')
        data=snapshot(self.d);self.assertEqual(data['correction_volume'],2)
        self.assertIn('Source juridique absente',[x['rejection_reason'] for x in data['corrections']])

    def test_review_is_attributed_to_the_model_that_produced_the_output(self):
        self.d.db.execute('''INSERT INTO production_outputs_v390
          (id,job_id,job_kind,output_kind,matter,source_id,status,label,paths,
           detail,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
          ('out-model',41001,'prepare_reply','mail_draft','DOS-001','mail-1',
           'prepared','Projet',json.dumps([]),json.dumps({}),
           '2026-10-01T08:00:00+00:00','2026-10-01T08:00:00+00:00'))
        self.d.db.execute('''INSERT INTO production_flows_v391
          (id,job_id,job_kind,trigger_kind,source_id,matter,status,stage,stages,
           model_provider,model_name,paths,last_error,retry_count,created,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          ('flow-model',41001,'prepare_reply','mail','mail-1','DOS-001',
           'prepared','prepared',json.dumps([]),'ollama','qwen3.8:27b',
           json.dumps([]),'',0,'2026-10-01T08:00:00+00:00',
           '2026-10-01T08:00:00+00:00'))
        self.d.db.commit()
        record_review(self.d,'out-model','DOS-001','mail_drafting','modified',
          'Texte initial.','Texte corrigé.')
        rows=snapshot(self.d)['corrections_by_model']
        self.assertEqual(rows[0]['provider'],'ollama')
        self.assertEqual(rows[0]['model'],'qwen3.8:27b')
        self.assertEqual(rows[0]['reviews'],1)

    def test_trusted_document_is_bound_to_exact_fingerprint(self):
        sha='a'*64
        result=trust_document(self.d,'matter','DOS-001','/Dossiers/DOS-001/modele.docx',sha,
          'approved','Modèle validé par le cabinet')
        self.assertEqual(result['sha256'],sha)
        context=applicable_context(self.d,'DOS-001','document_drafting',record=False)
        self.assertEqual(context['trusted_documents'][0]['sha256'],sha)

    def test_page_context_is_bounded_and_cannot_switch_matter(self):
        result=submit_question(self.d,'Analyse la page.',page_context={'page':'/production',
          'matter':'DOS-MALVEILLANT','selected_documents':['/x']*30,
          'active_mission':'Produire','recent_results':['ok']*9})
        row=self.d.db.execute('SELECT args FROM jobs WHERE id=?',(result['job_id'],)).fetchone()
        args=json.loads(row[0]);ctx=args['page_context']
        self.assertEqual(ctx['matter'],'');self.assertEqual(len(ctx['selected_documents']),20)
        self.assertEqual(len(ctx['recent_results']),5)


class LegalBenchmark410Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_case_must_be_anonymized_and_scores_all_legal_dimensions(self):
        with self.assertRaises(Stop):
            save_case(self.d,'Réel','mixed','Écrire à client@example.test',{},True)
        case=save_case(self.d,'Cas D-001','mixed','Dossier fictif D-001.',{
          'matter_id':'D-001','dates':['2026-10-01'],'citations':['Article 9'],
          'adverse_arguments':['Prescription'],'pieces':['Pièce 1'],'mail_required':['Madame'],
          'word_markers':['PAR CES MOTIFS'],'permitted_claims':['Contrat signé']},True)
        self.assertTrue(case['saved'])
        score=score_response({'matter_id':'D-001','dates':['2026-10-01'],'citations':['Article 9'],
          'adverse_arguments':['Prescription'],'pieces':['Pièce 1'],'mail_required':['Madame'],
          'mail_forbidden':[],'word_markers':['PAR CES MOTIFS'],'permitted_claims':['Contrat signé']},
          {'matter_id':'D-001','dates':['2026-10-01'],'citations':['Article 9'],
           'adverse_arguments':['Prescription'],'pieces':['Pièce 1'],'draft_email':'Madame',
           'word_document':'PAR CES MOTIFS','claims':['Contrat signé','Fait inventé']})
        self.assertEqual(set(score['scores']),{'matter_linking','dates','citations','adverse_arguments',
          'pieces','mail_quality','word_template','no_hallucination'})
        self.assertEqual(score['hallucination_count'],1)

    def test_api_exposes_41_learning_and_benchmark(self):
        spec=openapi('https://agent.example.test')
        self.assertEqual(spec['info']['version'],'5.6.2')
        for path in ('/learning/business-rules','/evaluations/legal',
                     '/evaluations/legal/cases','/evaluations/legal/run'):
            self.assertIn(path,spec['paths'])
        self.assertEqual(dispatch(self.d,'/capabilities','GET')['version'],'5.6.2')


class Browser410Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_sidebar_has_exactly_ten_primary_entries(self):
        # 5.3.0 : quatre entrées principales ; les rubriques historiques restent dans le volet « Rubriques ».
        body=self.request('/parametres')['body']
        nav=re.search(r'<nav aria-label="Navigation principale".*?</nav>',body,re.S).group(0)
        self.assertEqual(nav.count('<a '),4)
        sub=re.search(r'<nav aria-label="Rubriques".*?</nav>',body,re.S).group(0)
        self.assertEqual(sub.count('<a '),7)
        for label in ('Aujourd’hui','Dossiers','Mon style','Paramètres'):
            self.assertIn(label,nav)
        for label in ('Courriels à relire','Documents','Échéances','Agenda et tâches','Produire','Recherche','Cabinet'):
            self.assertIn(label,sub)
        self.assertIn('Apprentissage métier',body);self.assertIn('Banc juridique',body)
        self.assertIn('Version 5.6.2',body)

    def test_produce_hub_contains_the_six_expected_outputs(self):
        body=self.request('/production')['body']
        for label in ('Courriers et courriels','Conclusions et contrats','Audiences',
                      'Recherche juridique','Pièces PDF et bordereaux','Facturation'):
            self.assertIn(label,body)
        self.assertIn('Contexte détecté',body)


if __name__=='__main__':unittest.main()
