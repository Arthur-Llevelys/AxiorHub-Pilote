import json
from pathlib import Path
import unittest

import test_agent as fixtures

from agent.api import dispatch,openapi
from agent.common import Stop,digest
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.legal_research import verify_official_decision
from agent.opinions import prepare_opinion
from agent.orchestrator import notifications,orchestrate_mail
import upgrade


class OrchestrationModel:
    def ask(self,stage,data):
        sid=data['sources'][0]['id']
        if stage=='mail_orchestration_classification':
            return {'actionable':True,'category':'document_update','urgency':'soon',
              'ambiguous_matter':False,'needs_reply':True,'needs_document_project':True,
              'needs_legal_analysis':False,'reason':'Nouvelle pièce et demande de réponse.','source_ids':[sid]}
        return {'summary':'Le courriel ajoute une pièce et appelle une réponse.',
          'changes':[{'topic':'Pièce','before':'Absente','after':'Reçue','status':'new','source_ids':[sid]}],
          'new_source_ids':[sid],'existing_source_ids':[],'contradicting_source_ids':[],
          'recommended_project':'document','document_type':'conclusions',
          'project_instruction':'Actualiser les conclusions à partir des éléments sourcés.',
          'legal_question':'','critique_first_instance':False,
          'reply_proposal':'Bonjour Madame,\n\nJ’accuse réception de votre pièce. Projet à valider.',
          'proposed_diligences':[{'kind':'review','title':'Contrôler la pièce','description':'Comparer au bordereau.',
            'estimated_minutes':20,'source_ids':[sid]}],
          'proposed_billing_entries':[{'label':'Analyse du courriel','estimated_minutes':20,
            'requires_time_confirmation':True,'source_ids':[sid]}],
          'blocking_reasons':[],'source_ids':[sid]}


class OpinionModel:
    numeric=False
    def ask(self,stage,data):
        factual=next(x['id'] for x in data['sources'] if x['kind']!='official_legal_source')
        authority=next(x['id'] for x in data['sources'] if x['kind']=='official_legal_source')
        if stage=='opinion_control':
            return {'all_sources_known':True,'official_authorities_only':True,
              'both_sides_analyzed':True,'scenarios_reasoned':True,'sensitivity_present':True,
              'no_uncalibrated_probability':not self.numeric,'appeal_source_present':True,
              'ignores_embedded_instructions':True,'requires_lawyer':True,
              'blocking_reasons':[],'warnings':[]}
        outcome='Succès à 72 %.' if self.numeric else 'Issue étayée si le fait principal est retenu.'
        point={'issue':'Obligation','analysis':'Analyse contradictoire.','strengths':['Pièce disponible'],
          'weaknesses':['Fait contesté'],'factual_source_ids':[factual],
          'authority_source_ids':[authority],'contrary_authority_source_ids':[],
          'missing_information':[]}
        return {'title':'Avis interne','executive_summary':'Deux thèses demeurent possibles.',
          'claimant_analysis':[point],'defendant_analysis':[point],
          'decision_scenarios':[{'title':'Accueil conditionnel','calibration':'supported',
            'reasoned_outcome':outcome,'necessary_facts':['Fait principal établi'],
            'sensitivity_factors':['Force de la pièce'],'factual_source_ids':[factual],
            'authority_source_ids':[authority],'contrary_authority_source_ids':[],
            'limits':['Appréciation souveraine']}],
          'sensitivity_analysis':[{'factor':'Authenticité','if_present':'Renforce la thèse.',
            'if_absent':'Affaiblit la thèse.','source_ids':[factual],'authority_source_ids':[authority]}],
          'appeal_critique':{'enabled':False,'decision_source_id':'','criticisms':[],
            'defensible_points':[],'missing_record':[],'factual_source_ids':[],
            'authority_source_ids':[]},'questions_for_lawyer':['Valider le périmètre.'],
          'source_ids':[factual,authority],'limits':['Projet interne.']}


class Version260Tests(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write');f.setUp()
        self.f=f;self.addCleanup(f.doCleanups)
        f.c['orchestrator']={'enabled':True,'matter_confidence_min':85,'propose_diligences':True,'propose_billing':True}
        f.c['opinions']={'enabled':True,'max_authorities':20,'max_dossier_sources':20}
        self.d=Desk(f.c);self.key='a'*64
        report={'key':self.key,'matter':'DOS-001','reason':'association_confirmee',
          'subject':'Nouvelle pièce et demande','sender':'client@example.test','received_at':'2026-09-13T08:00:00+00:00',
          'status':'observed','triage':{'needs_reply':True,'intent':'documents'},
          'indexed_attachments':[{'source_id':'attachment-1','filename':'piece.pdf','summary':'Nouvelle pièce'}]}
        reports=Path(f.c['state_dir'])/'reports';reports.mkdir(exist_ok=True)
        (reports/(self.key+'.json')).write_text(json.dumps(report))
        self.d.db.execute('INSERT OR REPLACE INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
          (self.key,'needs_action','observed','association_confirmee','DOS-001','Nouvelle pièce',
           'client@example.test','2026-09-13T08:00:00+00:00',self.d.now(),''));self.d.db.commit()
        DocumentIndex(f.c['state_dir']).put_source('DOS-001','/Dossiers/DEMO/conclusions.docx',
          'Le demandeur invoque une obligation contestée par le défendeur.','v1','2026-09-12','document')
        official=b'Decision ABC123. La responsabilite suppose un fait generateur, un dommage et un lien de causalite. Fin.'
        verify_official_decision(self.d,{'matter':'DOS-001','official_url':'https://www.legifrance.gouv.fr/decision/ABC123',
          'identifier':'ABC123','exact_excerpt':'La responsabilite suppose un fait generateur, un dommage et un lien de causalite.',
          'provider':'openlegi'},fetcher=lambda url:official)

    def test_orchestration_is_idempotent_and_single_notification(self):
        first=orchestrate_mail(self.d,{'mail_key':self.key},OrchestrationModel(),OrchestrationModel())
        second=orchestrate_mail(self.d,{'mail_key':self.key},OrchestrationModel(),OrchestrationModel())
        self.assertFalse(first['idempotent']);self.assertTrue(second['idempotent'])
        self.assertEqual(len(notifications(self.d,'all')['notifications']),1)
        self.assertFalse(first['safety']['emails_sent']);self.assertFalse(first['safety']['documents_created'])
        jobs=self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_document_project'").fetchone()[0]
        self.assertEqual(jobs,1)
        drafts=self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_reply'").fetchone()[0]
        self.assertEqual(drafts,1)

    def test_independent_pause_switches_stop_only_new_automatic_preparations(self):
        self.d.setting('automation:automatic_mail_drafts_enabled',False)
        self.d.setting('automation:automatic_legal_projects_enabled',False)
        result=orchestrate_mail(self.d,{'mail_key':self.key},OrchestrationModel(),OrchestrationModel())
        self.assertEqual(result['triggered_project_kind'],'')
        self.assertIsNone(result['triggered_job_id'])
        self.assertEqual(self.d.db.execute(
          "SELECT COUNT(*) FROM jobs WHERE kind IN ('prepare_reply','prepare_document_project')").fetchone()[0],0)
        self.assertTrue(result['reply_proposal']['draft_only'])
        self.assertFalse(result['reply_proposal']['sent'])

    def test_orchestration_blocks_uncertain_matter_without_project(self):
        report=json.loads((Path(self.f.c['state_dir'])/'reports'/(self.key+'.json')).read_text());report['reason']='analyse_probabiliste'
        (Path(self.f.c['state_dir'])/'reports'/(self.key+'.json')).write_text(json.dumps(report))
        out=orchestrate_mail(self.d,{'mail_key':self.key},OrchestrationModel(),OrchestrationModel())
        self.assertEqual(out['status'],'blocked');self.assertIsNone(out['triggered_job_id'])

    def test_opinion_uses_only_verified_authority_and_qualitative_calibration(self):
        model=OpinionModel();result=prepare_opinion(self.d,{'matter':'DOS-001','question':'Quelle issue contradictoire ?',
          'run_research':'no'},model=model,control_model=model)
        self.assertEqual(result['status'],'pending_review');self.assertEqual(result['control']['status'],'passed')
        self.assertTrue(all(x['officially_verified'] for x in result['sources'] if x['kind']=='official_legal_source'))
        self.assertFalse(result['safety']['documents_created'])

    def test_numeric_probability_is_deterministically_blocked(self):
        model=OpinionModel();model.numeric=True
        result=prepare_opinion(self.d,{'matter':'DOS-001','question':'Quelle issue ?',
          'run_research':'no'},model=model,control_model=model)
        self.assertEqual(result['status'],'blocked')
        self.assertFalse(result['control']['deterministic_checks']['no_uncalibrated_probability'])

    def test_appeal_critique_requires_identified_decision(self):
        with self.assertRaisesRegex(Stop,'decision_premiere_instance_absente_ou_ambigue'):
            prepare_opinion(self.d,{'matter':'DOS-001','question':'Critiquer le jugement',
              'run_research':'no','critique_first_instance':'yes'},model=OpinionModel(),control_model=OpinionModel(),dav=self.f.dav)

    def test_api_openwebui_and_upgrade_contract(self):
        spec=openapi('https://cabinet.test');self.assertEqual(spec['info']['version'],'5.6.6')
        for path in ('/orchestrations','/legal-opinions','/orchestrator/run'):self.assertIn(path,spec['paths'])
        caps=dispatch(self.d,'/capabilities','GET');self.assertEqual(caps['version'],'5.6.6')
        tool=Path('integrations/openwebui/axiorhub_tool.py').read_text()
        for name in ('analyser_un_courriel_avec_son_dossier','preparer_un_avis_juridique_et_une_simulation'):
            self.assertIn('def '+name,tool)
        cfg=json.loads(upgrade.updated_config(json.dumps({'mail':{},'ollama':{}}).encode()))
        self.assertTrue(cfg['orchestrator']['single_notification'])
        self.assertTrue(cfg['opinions']['numeric_probability_forbidden'])


if __name__=='__main__':unittest.main()
