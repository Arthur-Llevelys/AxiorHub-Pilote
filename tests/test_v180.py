import json
import unittest
from unittest.mock import patch
from pathlib import Path

import test_agent as fixtures
import test_desk
from agent.api import dispatch,openapi
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.strategic import (act_projects,analyze_strategy,build_matrix,change,
    draft_act,latest_matrix,latest_strategy)


class FakeStrategicModel:
    calls=[]
    invented=False
    def __init__(self,cfg):pass
    def ask(self,stage,payload):
        self.__class__.calls.append((stage,payload))
        sid='invented' if self.__class__.invented else payload['sources'][0]['id']
        if stage=='strategy_analysis':
            return {'executive_summary':'La demande est documentée, mais le recouvrement reste à apprécier.',
              'objectives':['Obtenir le paiement'],'positions':[{'title':'Demande','description':'12 000 euros sont demandés.','actor':'ALPHA','source_ids':[sid]}],
              'issues':[{'issue':'Fondement à vérifier','why_it_matters':'Conditionne la demande','research_needed':'Vérifier les textes applicables dans Ordali.','source_ids':[sid]}],
              'options':[{'title':'Mise en demeure','description':'Formaliser la demande.','strengths':['Créance chiffrée'],'weaknesses':['Échéance à confirmer'],'risks':['Contestation'],'missing_evidence':['Preuve de réception'],'next_steps':['Contrôler la facture'],'orientation':'preferred','source_ids':[sid]}],
              'recommended_approach':{'summary':'Préparer une mise en demeure après contrôle.','conditions':['Valider l’échéance'],'source_ids':[sid]},
              'procedural_risks':['Prescription à rechercher'],'contradictions':[],
              'questions_for_lawyer':['Le montant est-il arrêté ?'],'source_ids':[sid],
              'limits':['Aucune recherche juridique externe.']}
        if stage=='evidence_matrix':
            return {'rows':[{'row_type':'claim','proposition':'ALPHA réclame 12 000 euros.','asserted_by':'ALPHA',
              'proof_status':'partially_documented','supporting_source_ids':[sid],
              'contradicting_source_ids':[],'neutral_source_ids':[],
              'missing_evidence':['Accusé de réception'],'strategic_use':'Établir le montant demandé.','cautions':['Échéance à confirmer.']}],
              'global_gaps':['Preuve de réception'],'contradictions':[],
              'source_ids':[sid],'limits':['Matrice non exhaustive.']}
        if stage=='act_project':
            return {'title':'Projet de mise en demeure','act_type':payload['type_acte'],
              'introduction':'Projet à relire.','introduction_source_ids':[sid],
              'sections':[{'heading':'Objet','body':'ALPHA réclame 12 000 euros.','source_ids':[sid]}],
              'requests':[{'text':'Régler la somme après vérification de son exigibilité.','source_ids':[sid]}],
              'exhibits_referenced':[{'label':'Facture à numéroter','source_ids':[sid]}],
              'placeholders':['Date d’échéance'],
              'points_for_lawyer':['Valider le fondement juridique.'],'source_ids':[sid],
              'limits':['Aucun texte juridique vérifié.']}
        raise AssertionError(stage)


class Strategic180Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)
        index=DocumentIndex(self.f.c['state_dir'])
        index.put_source(self.f.matters[0],'/Dossiers/DEMO/facture.txt',
          'La société ALPHA réclame 12 000 euros selon la facture F-1.',
          'etag-1','2026-09-09T10:00:00+00:00','document')
        FakeStrategicModel.calls=[];FakeStrategicModel.invented=False

    def test_strategy_is_source_bound_proposed_and_versioned_on_validation(self):
        with patch('agent.strategic.Model',FakeStrategicModel):
            result=analyze_strategy(self.d,{'matter':'DOS-001','objective':'Choisir une voie de recouvrement.'})
        saved=latest_strategy(self.d,'DOS-001')
        self.assertEqual(result['statut'],'proposee');self.assertEqual(saved['status'],'proposed')
        change(self.d,'validate_strategy',{'matter':'DOS-001','analysis':saved['id'],'note':'Relu.'})
        self.assertEqual(latest_strategy(self.d,'DOS-001')['status'],'validated')
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM strategic_history WHERE entity_type='strategy_analyses'").fetchone()[0],1)

    def test_invented_strategy_source_is_rejected(self):
        FakeStrategicModel.invented=True
        with patch('agent.strategic.Model',FakeStrategicModel),self.assertRaisesRegex(Exception,'source_strategique_invalide'):
            analyze_strategy(self.d,{'matter':'DOS-001','objective':'Analyser.'})
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM strategy_analyses').fetchone()[0],0)

    def test_matrix_separates_support_contradiction_and_missing_proof(self):
        with patch('agent.strategic.Model',FakeStrategicModel):
            result=build_matrix(self.d,{'matter':'DOS-001','objective':'Établir la preuve de la créance.'})
        matrix=latest_matrix(self.d,'DOS-001');row=matrix['rows'][0]
        self.assertEqual(result['lignes'],1);self.assertTrue(row['supporting_sources'])
        self.assertEqual(row['contradicting_sources'],[])
        self.assertEqual(row['missing_evidence'],['Accusé de réception'])

    def test_act_project_stays_internal_and_can_only_be_validated_as_work(self):
        with patch('agent.strategic.Model',FakeStrategicModel):
            result=draft_act(self.d,{'matter':'DOS-001','act_type':'contrat',
              'instruction':'Préparer une mise en demeure prudente.'})
        project=act_projects(self.d,'DOS-001')[0]
        self.assertFalse(result['depot']);self.assertFalse(result['envoi'])
        self.assertIn('PROJET INTERNE',project['content']);self.assertEqual(project['status'],'proposed')
        change(self.d,'validate_act',{'matter':'DOS-001','project':project['id']})
        self.assertEqual(act_projects(self.d,'DOS-001')[0]['status'],'validated')

    def test_api_exposes_and_queues_all_three_workflows(self):
        self.assertEqual(openapi('https://cabinet.test')['info']['version'],'5.6.19')
        a=dispatch(self.d,'/matters/DOS-001/strategy','POST',{'objective':'Comparer les options.'})
        b=dispatch(self.d,'/matters/DOS-001/matrix','POST',{'objective':'Cartographier les preuves.'})
        c=dispatch(self.d,'/matters/DOS-001/act-projects','POST',{'act_type':'conclusions','instruction':'Préparer une trame.'})
        self.assertEqual({self.d.db.execute('SELECT kind FROM jobs WHERE id=?',(x['job_id'],)).fetchone()[0] for x in (a,b,c)},
                         {'analyze_strategy','build_matrix','draft_act'})
        self.assertIsNone(dispatch(self.d,'/matters/DOS-001/strategy','GET')['analysis'])

    def test_openwebui_tool_exposes_bounded_strategic_actions_without_send(self):
        text=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('demander_une_analyse_strategique',text)
        self.assertIn('construire_la_matrice_de_preuve',text)
        self.assertIn('preparer_un_projet_d_acte',text)
        self.assertNotIn('envoyer_un_acte',text)
        self.assertNotIn('subprocess',text)


class Web180Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_strategy_page_is_visible_source_safe_and_does_not_refresh(self):
        page=self.request('/strategy',query='matter=DOS-001')['body']
        self.assertIn('Espace stratégique',page);self.assertIn('Matrice faits / pièces / prétentions',page)
        self.assertIn('Atelier de projets d’actes',page);self.assertIn('name="objective"',page)
        self.assertIn('name="act_type"',page);self.assertNotIn('http-equiv="refresh"',page)
        self.assertIn('Version 5.6.19',page)


if __name__=='__main__':unittest.main()
