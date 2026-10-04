from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest

import test_agent as fixtures
import test_desk
from agent.api import dispatch, openapi
from agent.common import load_matters, private_json
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.integration import dashboard, sync_work_items
from agent.mailbox import exclusion
from agent.portfolio import (classify_all, grouped_associations, observe_mail,
    portfolio_rows, reconcile_work_items, set_matter_state)
from agent.proactive import build_daily_dashboard, monitor_all


class Portfolio210Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['portfolio']={'active_days':180,'archive_days':730,
          'bootstrap_months':18,'mail_batch_size':100,'folder_batch_size':10,
          'reconcile_batch_size':200,'backlog_days':14}
        self.d=Desk(self.f.c);self.index=DocumentIndex(self.f.c['state_dir'])

    def add_matter(self,mid,name='Ancien dossier'):
        matters=load_matters(self.f.c)
        matters.append({'id':mid,'client_name':name,'path':'/Dossiers/'+mid,
          'references':[mid],'aliases':[name],'correspondents':[]})
        private_json(Path(self.f.c['matters_file']),matters)

    def add_doc(self,mid,modified,indexed_text='Texte'):
        self.index.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)',
          (mid,'/Dossiers/'+mid+'/piece.pdf','v1',modified,indexed_text,''))
        self.index.db.commit()

    def test_source_activity_classifies_active_dormant_archived_not_index_time(self):
        self.add_matter('2017010101');self.add_matter('DOS-SLEEP','Sommeil')
        self.add_doc('DOS-001',datetime.now(timezone.utc).isoformat())
        self.add_doc('2017010101','2019-01-02T10:00:00+00:00')
        counts=classify_all(self.d)
        states={x['matter']:x['state'] for x in portfolio_rows(self.d,None,100)}
        self.assertEqual(states['DOS-001'],'active')
        self.assertEqual(states['2017010101'],'archived')
        self.assertEqual(states['DOS-SLEEP'],'dormant')
        self.assertEqual(sum(counts.values()),3)

    def test_webdav_http_last_modified_date_activates_matter(self):
        self.d.db.execute('INSERT OR REPLACE INTO portfolio_document_activity VALUES (?,?,?,?,?)',
          ('DOS-001',datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'),
           '/Dossiers/DEMO/piece.pdf',self.d.now(),''));self.d.db.commit()
        classify_all(self.d)
        row=next(x for x in portfolio_rows(self.d,['active']) if x['matter']=='DOS-001')
        self.assertIn('document_recent',row['reasons'])

    def test_open_task_and_manual_activation_are_real_active_signals(self):
        self.add_matter('DOS-TASK','Tâche active')
        self.d.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
          ('t1','DOS-TASK','Relire','','open',self.d.now()));self.d.db.commit()
        classify_all(self.d)
        row=next(x for x in portfolio_rows(self.d,['active']) if x['matter']=='DOS-TASK')
        self.assertIn('tache_ouverte',row['reasons'])
        set_matter_state(self.d,{'matter':'DOS-001','state':'archived'})
        self.assertEqual(next(x for x in portfolio_rows(self.d,['archived']) if x['matter']=='DOS-001')['manual_state'],'archived')

    def test_unique_reference_links_matter_without_inventing_role(self):
        mail=fixtures.mail(sender='nouveau@example.test',subject='DOS-001 — demande administrative')
        result=observe_mail(self.d,mail,'INBOX')
        self.assertTrue(result['linked']);self.assertEqual(result['confidence'],100)
        self.assertFalse(any(x['email']=='nouveau@example.test' for x in load_matters(self.f.c)[0]['correspondents']))
        group=grouped_associations(self.d,'automatic',20)[0]
        self.assertEqual(group['matter'],'DOS-001');self.assertEqual(group['email'],'nouveau@example.test')

    def test_reference_conflicting_with_confirmed_correspondent_is_not_automatic(self):
        self.add_matter('DOS-002','Autre client')
        matters=load_matters(self.f.c)
        other=next(x for x in matters if x['id']=='DOS-002')
        other['correspondents']=[{'email':'other@example.test','role':'client'}]
        private_json(Path(self.f.c['matters_file']),matters)
        mail=fixtures.mail(sender='other@example.test',subject='DOS-001 — demande contradictoire')
        result=observe_mail(self.d,mail,'INBOX')
        self.assertFalse(result['linked'])
        self.assertEqual(result['reason'],'reference_et_fil_contradictoires')
        self.assertGreaterEqual(len(grouped_associations(self.d,'conflict',20)),2)

    def test_confirmation_of_one_correspondent_retries_the_whole_matter(self):
        self.d.associate({'matter':'DOS-001','email':'nouveau@example.test','role':'tiers'})
        jobs=[dict(x) for x in self.d.db.execute("SELECT kind,args FROM jobs WHERE status='pending'")]
        self.assertTrue(any(x['kind']=='retry_matter' and 'DOS-001' in x['args'] for x in jobs))

    def test_reconciliation_keeps_read_mail_open_and_moves_old_unread_to_backlog(self):
        self.f.model.intent='legal';mail=fixtures.mail();key=self.f.engine.process(mail)
        self.f.box.inputs[mail.uid]=mail;sync_work_items(self.d)
        mail.flags.add('\\Seen');result=reconcile_work_items(self.d,self.f.box)
        state=self.d.db.execute('SELECT state FROM work_items WHERE mail_key=?',(key,)).fetchone()[0]
        self.assertNotEqual(state,'handled');self.assertEqual(result['handled'],0)
        mail.flags.add('\\Answered');result=reconcile_work_items(self.d,self.f.box)
        state=self.d.db.execute('SELECT state FROM work_items WHERE mail_key=?',(key,)).fetchone()[0]
        self.assertEqual(state,'handled');self.assertEqual(result['handled'],1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='learn' AND status='pending'").fetchone()[0],1)

        self.f.model.fake_source=True
        old=fixtures.mail(uid='2',sender='client@example.test',subject='DOS-001 — ancienne demande')
        old.timestamp=datetime.now(timezone.utc)-timedelta(days=20)
        self.f.box.inputs[old.uid]=old;key2=self.f.engine.process(old);sync_work_items(self.d)
        result=reconcile_work_items(self.d,self.f.box)
        state=self.d.db.execute('SELECT state FROM work_items WHERE mail_key=?',(key2,)).fetchone()[0]
        self.assertEqual(state,'backlog');self.assertEqual(result['backlog'],1)

    def test_recruitment_is_classified_before_any_matter_requirement(self):
        mail=fixtures.mail(sender='recrutement@example.test',
          subject='Recrutements - profil juriste droit des affaires en recherche active')
        self.assertEqual(exclusion(mail,self.f.c['mail']),'sollicitation_recrutement')

    def test_administrative_request_can_be_drafted_without_matter(self):
        self.f.model.intent='administrative'
        mail=fixtures.mail(sender='administratif@example.test',subject='Demande de confirmation')
        mail.msg.set_content('Bonjour Maître, pouvez-vous accuser réception de mon message ?')
        key=self.f.engine.process(mail)
        self.assertEqual(self.f.status(key),'drafted')
        report=json.loads((Path(self.f.c['state_dir'])/'reports'/(key+'.json')).read_text())
        self.assertIsNone(report['matter'])
        self.assertEqual(self.f.box.appended[0]['To'],'administratif@example.test')

    def test_engine_records_unique_reference_as_automatic_case_link(self):
        self.f.model.intent='administrative'
        mail=fixtures.mail(sender='nouveau@example.test',subject='DOS-001 — accusé de réception')
        self.f.engine.process(mail)
        row=self.d.db.execute('SELECT matter,status,confidence FROM portfolio_mail_links').fetchone()
        self.assertEqual((row['matter'],row['status'],row['confidence']),('DOS-001','automatic',100))

    def test_monitoring_and_dashboard_ignore_archived_matters(self):
        self.add_matter('2017010101')
        set_matter_state(self.d,{'matter':'DOS-001','state':'active'})
        set_matter_state(self.d,{'matter':'2017010101','state':'archived'})
        result=monitor_all(self.d,{})
        queued=[json.loads(x[0])['matter'] for x in self.d.db.execute(
          "SELECT args FROM jobs WHERE kind='monitor_matter' AND status='pending'")]
        self.assertEqual(result['dossiers_mis_en_attente'],1);self.assertEqual(queued,['DOS-001'])
        data=build_daily_dashboard(self.d)
        self.assertIn('today',data);self.assertIn('recommended_actions',data)
        self.assertTrue(all(x['id']!='2017010101' for x in data['recent_matters']))

    def test_api_and_openwebui_expose_portfolio_without_send(self):
        self.assertEqual(openapi('https://cabinet.test')['info']['version'],'5.6.1')
        self.assertIn('summary',dispatch(self.d,'/portfolio','GET',query={}))
        queued=dispatch(self.d,'/portfolio/organize','POST',{})
        self.assertEqual(queued['status'],'queued')
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('organiser_automatiquement_le_cabinet',tool)
        self.assertIn('nettoyer_la_boite_a_traiter',tool)
        self.assertNotIn('def envoyer_',tool)


class Web210Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_portfolio_dashboard_and_backlog_are_clear_and_static(self):
        dashboard_page=self.request('/dashboard')['body']
        dossiers=self.request('/dossiers')['body']
        inbox=self.request('/',query='view=backlog')['body']
        self.assertIn('Organiser automatiquement mon cabinet',dashboard_page)
        self.assertIn('Aujourd’hui',dashboard_page)
        self.assertIn('Actifs',dossiers);self.assertIn('Archivés',dossiers)
        self.assertIn('Arriéré',inbox)
        self.assertIn('Version 5.6.1',dashboard_page)
        self.assertNotIn('http-equiv="refresh"',dashboard_page+dossiers+inbox)


if __name__=='__main__':unittest.main()
