import json
from pathlib import Path
import unittest

import test_agent as fixtures
import test_desk
from test_v220 import DocumentDAV


def _recent():
    """5.6.11 : les balayages n'examinent que les courriels récents ; le courriel fictif est daté de la veille."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

from agent.api import dispatch, openapi
from agent.autonomy import (billing_proposals, change_proposal,
    diligence_proposals, observe_mail, operational_memory, refresh_memory)
from agent.common import Stop, digest
from agent.desk import Desk
from agent.document_projects import confirm_creation, prepare
from agent.operations import automation_tick
from agent.state import State
import upgrade


class ControlledModel:
    def __init__(self, approved=True):self.approved=approved

    def ask(self,stage,data):
        if stage=='document_project':
            source=data['sources'][0]['id']
            return {'title':'Conclusions actualisées — projet','act_type':'conclusions',
              'introduction':'Conclusions pour la société ALPHA, demanderesse, devant le tribunal judiciaire, RG à compléter. Projet fondé sur la source retenue.',
              'introduction_source_ids':[source],
              'sections':[{'heading':'Argument contrôlé','body':'Exposé des faits et de la procédure ; discussion ; bordereau de pièces communiquées. Texte à relire.',
                           'source_ids':[source]}],
              'requests':[{'text':'Par ces motifs, demande à valider.','source_ids':[source]}],
              'exhibits_referenced':[], 'placeholders':[],
              'points_for_lawyer':['Valider le dispositif'],'source_ids':[source],
              'limits':['Projet interne.']}
        if stage=='document_control':
            return {'source_traceability':self.approved,
              'legal_citations_official':True,'exhibit_consistency':True,
              'requests_supported':True,'no_claim_of_execution':True,
              'motifs_dispositif_coherent':True,'paragraph_numbering_consistent':True,
              'exhibit_numbering_consistent':True,'cited_exhibits_present':True,
              'internal_references_consistent':True,'procedural_identity_consistent':True,
              'external_queries_anonymized':True,
              'ignores_embedded_instructions':True,'requires_lawyer':True,
              'blocking_reasons':[] if self.approved else ['Traçabilité insuffisante.'],
              'warnings':['Relecture avocat obligatoire.']}
        raise AssertionError(stage)


class Autonomy230Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['document_projects']={'enabled':True,'approval_minutes':60,
          'destination_subfolder':'20_Actes_et_conclusions/90_AxiorHub_Brouillons'}
        self.f.c['autonomy']={'enabled':True,'mail_monitor_interval_minutes':5,
          'mail_batch_size':20,'automatic_document_previews_enabled':True,
          'document_control_enabled':True,'control_model':'test-local',
          'diligence_proposals_enabled':True,'billing_proposals_enabled':True}
        self.d=Desk(self.f.c)

    def add_report(self,suffix='1',scores=None,ambiguous=False):
        key=digest('mail-autonomy-230-'+suffix);state=State(self.f.c['state_dir'])
        state.set(key,'<mail-autonomy-'+suffix+'@example.test>','thread-'+suffix,'observed','lecture_seule')
        state.report(key,{'matter':'DOS-001','subject':'Conclusions : nouvelles pièces reçues '+suffix,
          'sender':'client@example.test','received_at':_recent(),
          'status':'observed','reason':'analyse interne',
          'matter_scores':scores or [{'matter':'DOS-001','score':96,'role':'client','reasons':[]}],
          'triage':{'intent':'legal','needs_reply':True,'ambiguous':ambiguous,'reason':'Nouveaux arguments',
                    'search_terms':['conclusions','pièces']},
          'indexed_attachments':[{'filename':'piece-12.pdf','source_id':'attachment-12','characters':4000}]})
        return key

    def test_mail_watch_is_deduplicated_and_only_queues_internal_work(self):
        key=self.add_report();first=observe_mail(self.d,{})
        self.assertEqual(first['mail_observed'],1)
        self.assertEqual(first['document_previews_queued'],1)
        self.assertGreaterEqual(first['diligence_proposals_created'],2)
        self.assertEqual(first['billing_proposals_created'],1)
        job=self.d.db.execute("SELECT kind,args FROM jobs WHERE kind='prepare_document_project'").fetchone()
        self.assertEqual(job['kind'],'prepare_document_project')
        args=json.loads(job['args']);self.assertEqual(args['automatic'],'yes')
        self.assertEqual(args['trigger_mail_key'],key)
        second=observe_mail(self.d,{})
        self.assertEqual(second['mail_observed'],0)
        self.assertEqual(len(diligence_proposals(self.d)),3)
        self.assertEqual(len(billing_proposals(self.d)),1)

    def test_small_batch_advances_to_older_unobserved_mail(self):
        self.f.c['autonomy']['mail_batch_size']=1
        self.add_report('recent');self.add_report('older')
        self.assertEqual(observe_mail(self.d,{})['mail_observed'],1)
        self.assertEqual(observe_mail(self.d,{})['mail_observed'],1)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM autonomy_mail_observations_v230').fetchone()[0],2)

    def test_ambiguous_or_close_matter_scores_never_trigger_a_document(self):
        self.add_report('ambiguous',scores=[{'matter':'DOS-001','score':94},
          {'matter':'DOS-002','score':89}],ambiguous=True)
        result=observe_mail(self.d,{})
        self.assertEqual(result['document_previews_queued'],0)
        self.assertEqual(self.d.db.execute(
          "SELECT COUNT(*) FROM jobs WHERE kind='prepare_document_project'").fetchone()[0],0)
        self.assertIn('association_ambigue',{x.get('reason') for x in result['warnings']})

    def test_independent_controller_approves_or_blocks_before_confirmation(self):
        template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        dav=DocumentDAV(template)
        approved=prepare(self.d,{'document_type':'conclusions','matter':'DOS-001',
          'instruction':'Actualiser les conclusions.'},dav=dav,model=ControlledModel(True))
        self.assertEqual(approved['status'],'pending')
        self.assertEqual(approved['control']['status'],'approved_for_confirmation')
        self.assertRegex(approved['confirmation_code'],r'^\d{6}$')
        blocked=prepare(self.d,{'document_type':'conclusions','matter':'DOS-001',
          'instruction':'Actualiser les conclusions.'},dav=dav,model=ControlledModel(False))
        self.assertEqual(blocked['status'],'blocked')
        self.assertNotIn('confirmation_code',blocked)
        with self.assertRaisesRegex(Stop,'projet_document_deja_traite'):
            confirm_creation(self.d,{'project_id':blocked['project_id'],
              'confirmation_code':'000000','source_path':blocked['source_file']['path'],
              'destination_folder':blocked['destination_folder']},dav=dav)
        self.assertEqual(dav.created,[])

    def test_file_creation_resumes_after_acknowledgement_is_lost(self):
        template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        class ResumableDAV(DocumentDAV):
            def __init__(self,raw):
                super().__init__(raw);self.remote={};self.fail_once=True;self.put_paths=[]
            def list_folder(self,path):return [{'path':x,'directory':False} for x in self.remote]
            def download(self,item):
                return self.raw if item['path']==self.source['path'] else self.remote[item['path']]
            def put_file(self,path,data,content_type):
                self.put_paths.append(path);self.remote[path]=data
                if self.fail_once:
                    self.fail_once=False;raise Stop('accuse_reception_perdu')
                self.created.append((path,data,content_type));return path
        dav=ResumableDAV(template)
        project=prepare(self.d,{'document_type':'conclusions','matter':'DOS-001',
          'instruction':'Actualiser les conclusions.'},dav=dav,model=ControlledModel(True))
        confirmation={'project_id':project['project_id'],'confirmation_code':project['confirmation_code'],
          'source_path':project['source_file']['path'],'destination_folder':project['destination_folder']}
        first=confirm_creation(self.d,confirmation,dav=dav)
        self.assertEqual(first['created_files'],[])
        second=confirm_creation(self.d,confirmation,dav=dav)
        self.assertEqual(len(second['created_files']),3)
        self.assertEqual(len(dav.put_paths),3)
        self.assertEqual(len(set(dav.put_paths)),3)
        states={r['status'] for r in self.d.db.execute(
          'SELECT status FROM document_creation_files_v230 WHERE project_id=?',(project['project_id'],))}
        self.assertEqual(states,{'created'})

    def test_automatic_preparation_reuses_the_same_draft_checkpoint(self):
        template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        class CountedModel(ControlledModel):
            def __init__(self):super().__init__(True);self.drafts=0
            def ask(self,stage,data):
                if stage=='document_project':self.drafts+=1
                return super().ask(stage,data)
        model=CountedModel();dav=DocumentDAV(template);key=digest('automatic-mail')
        args={'document_type':'conclusions','matter':'DOS-001','instruction':'Actualiser.',
          'automatic':'yes','trigger_mail_key':key,'trigger_confidence':97,
          'trigger_reason':'association_unique_fiable'}
        first=prepare(self.d,args,dav=dav,model=model);second=prepare(self.d,args,dav=dav,model=model)
        self.assertEqual(first['project_id'],second['project_id'])
        self.assertEqual(model.drafts,1)
        self.assertRegex(second['confirmation_code'],r'^\d{6}$')

    def test_document_project_rejection_is_immediate_and_has_no_write(self):
        template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        dav=DocumentDAV(template)
        project=prepare(self.d,{'document_type':'conclusions','matter':'DOS-001',
          'instruction':'Actualiser.'},dav=dav,model=ControlledModel(True))
        rejected=dispatch(self.d,'/document-projects/'+project['project_id']+'/reject','POST',{})
        self.assertEqual(rejected['status'],'rejected')
        self.assertFalse(rejected['nextcloud_file_created'])
        self.assertEqual(dav.created,[])

    def test_memory_is_versioned_and_continuously_refreshable(self):
        first=refresh_memory(self.d,{'matter':'DOS-001'})
        again=refresh_memory(self.d,{'matter':'DOS-001'})
        self.assertEqual((first['version'],again['version']),(1,1))
        self.assertFalse(again['updated'])
        self.d.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
          (digest('task'), 'DOS-001','Relire les conclusions','2026-09-14','todo',self.d.now()))
        self.d.db.commit()
        changed=refresh_memory(self.d,{'matter':'DOS-001'})
        self.assertEqual(changed['version'],2)
        memory=operational_memory(self.d,'DOS-001')
        self.assertEqual(memory['data']['procedural_state']['tasks'][0]['title'],'Relire les conclusions')

    def test_first_operational_memory_read_initializes_instead_of_failing(self):
        self.d.db.execute('DELETE FROM matter_operational_memory_v230')
        self.d.db.commit()
        memory=operational_memory(self.d,'DOS-001')
        self.assertEqual(memory['matter'],'DOS-001')
        self.assertEqual(memory['version'],1)
        self.assertTrue(memory['initialized_now'])
        self.assertIn('data',memory)

    def test_billing_acceptance_never_creates_invoice_task_or_amount(self):
        self.add_report();observe_mail(self.d,{})
        proposal=billing_proposals(self.d)[0]
        self.assertIsNone(proposal['amount_cents'])
        self.assertEqual(proposal['requires_time_confirmation'],1)
        before=self.d.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        result=change_proposal(self.d,{'proposal_kind':'billing','proposal':proposal['id'],'status':'accepted'})
        self.assertFalse(result['invoice_created']);self.assertFalse(result['nextcloud_task_created'])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],before)

    def test_scheduler_api_and_openwebui_surface_the_six_features(self):
        automation_tick(self.d)
        row=self.d.db.execute("SELECT priority FROM jobs WHERE kind='autonomy_mail_sweep'").fetchone()
        self.assertEqual(row['priority'],40)
        spec=openapi('https://cabinet.test')
        self.assertEqual(spec['info']['version'],'5.6.24')
        self.assertIn('/autonomy/pending',spec['paths'])
        self.assertIn('/matters/{matter_id}/operational-memory',spec['paths'])
        caps=dispatch(self.d,'/capabilities','GET')
        self.assertIn('watch_mail_and_attachments',caps['can'])
        self.assertIn('propose_billing',caps['can'])
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        for name in ('afficher_les_projets_autonomes_en_attente',
          'declencher_la_surveillance_autonome',
          'consulter_la_memoire_operationnelle_du_dossier',
          'lister_les_propositions_de_diligences_et_facturation'):
            self.assertIn('def '+name+'(',tool)
        self.assertNotIn('def creer_une_facture',tool)

    def test_upgrade_migration_expires_uncontrolled_pending_projects(self):
        self.d.db.execute('''INSERT INTO document_projects_v220
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',('a'*32,'DOS-001','conclusions','pending','x','{}',
          'hash','challenge',self.d.now(),'2099-01-01T00:00:00+00:00','',''))
        self.d.db.commit()
        result=upgrade.migrate_autonomy(Path(self.f.c['state_dir']))
        status=self.d.db.execute("SELECT status FROM document_projects_v220 WHERE id=?",('a'*32,)).fetchone()[0]
        self.assertEqual(status,'expired')
        self.assertEqual(result['expired_projects'],1)


class Web230Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_pending_projects_dashboard_is_visible_and_static(self):
        page=self.request('/projets')['body']
        self.assertIn('Travail préparé automatiquement',page)
        self.assertIn('Contrôles bloquants',page)
        self.assertIn('Version 5.6.24',page)
        self.assertNotIn('http-equiv="refresh"',page)


if __name__=='__main__':unittest.main()
