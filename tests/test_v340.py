"""Release 3.4: differential work must remain source-bound and supervised."""
from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from agent.desk import Desk
from agent.common import Stop
from agent.index import DocumentIndex
from agent.portfolio import set_matter_state
from agent.proactive34 import run_cycle, briefing, schedule, _notice
from agent.web import App
import test_agent as fixtures


class Preparation340Tests(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c)
        set_matter_state(self.d,{'matter':'DOS-001','state':'active'})
        self.at=datetime(2026,9,20,6,tzinfo=timezone.utc)
        self.f.c['calendar']['urls']=['/calendar/']

    def doc(self,etag='one'):
        idx=DocumentIndex(self.f.c['state_dir'])
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)',
            ('DOS-001','/Dossiers/DEMO/Conclusions.docx',etag,self.at.isoformat(),
             'Conclusions strictement fictives '+etag,''));idx.db.commit();idx.db.close()

    def events(self,rows=None):
        return lambda *a,**k:{'events':rows or []}

    def test_baseline_change_queue_and_idempotence(self):
        self.doc();first=run_cycle(self.d,{'slot':'100'},self.at,calendar=self.events())
        self.assertTrue(first['baseline']);self.assertEqual(first['source_counts']['projects_queued'],0)
        self.doc('two')
        changed=run_cycle(self.d,{'slot':'101'},self.at+timedelta(hours=4),calendar=self.events())
        self.assertEqual(changed['source_counts']['documents'],1)
        self.assertEqual(changed['source_counts']['projects_queued'],1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_document_project'").fetchone()[0],1)
        self.assertTrue(run_cycle(self.d,{'slot':'101'},self.at+timedelta(hours=4),calendar=self.events())['idempotent'])
        later=run_cycle(self.d,{'slot':'102'},self.at+timedelta(hours=8),calendar=self.events())
        self.assertEqual(later['source_counts']['documents'],0)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM proactive_notices_v340').fetchone()[0],1)
        self.assertEqual(self.f.box.appended,[])

    def test_ambiguous_hearing_and_other_deadlines_are_blocked(self):
        self.doc();t=(self.at+timedelta(days=7)).isoformat()
        rows=[{'id':'h1','title':'Audience de plaidoirie DOS-001','matter':'',
               'matter_candidates':['DOS-001','DOS-002'],'starts':t},
              {'id':'d1','title':'Date limite conclusions DOS-001','matter':'DOS-001',
               'matter_candidates':['DOS-001'],'starts':t}]
        report=run_cycle(self.d,{'slot':'200'},self.at,calendar=self.events(rows))
        self.assertEqual(report['source_counts']['blocked'],1)
        self.assertEqual(report['source_counts']['hearings'],0)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_hearing'").fetchone()[0],0)

    def test_j14_preparation_requires_both_writings_and_deduplicates(self):
        self.doc();t=(self.at+timedelta(days=13)).isoformat()
        event={'id':'h1','title':'Audience de plaidoirie DOS-001','matter':'DOS-001',
               'matter_candidates':['DOS-001'],'starts':t,'etag':'v1'}
        chosen={'our_latest':{'path':'/Dossiers/DEMO/Conclusions cabinet.docx'},
                'opponent_latest':{'path':'/Dossiers/DEMO/Conclusions adversaire.docx'}}
        selector=lambda *a:chosen
        first=run_cycle(self.d,{'slot':'300'},self.at,calendar=self.events([event]),writing_selector=selector)
        self.assertEqual(first['source_counts']['hearings'],1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_hearing'").fetchone()[0],1)
        run_cycle(self.d,{'slot':'301'},self.at+timedelta(hours=4),calendar=self.events([event]),writing_selector=selector)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_hearing'").fetchone()[0],1)
        outside={**event,'id':'h2','starts':(self.at+timedelta(days=15)).isoformat()}
        run_cycle(self.d,{'slot':'302'},self.at+timedelta(hours=8),calendar=self.events([outside]),writing_selector=selector)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_hearing'").fetchone()[0],1)

    def test_missing_writings_and_removed_document_are_visible_without_side_effect(self):
        self.doc();run_cycle(self.d,{'slot':'500'},self.at,calendar=self.events())
        idx=DocumentIndex(self.f.c['state_dir']);idx.db.execute('DELETE FROM docs WHERE matter=?',('DOS-001',))
        idx.db.commit();idx.db.close()
        event={'id':'h3','title':'Audience de plaidoirie DOS-001','matter':'DOS-001',
               'matter_candidates':['DOS-001'],'starts':(self.at+timedelta(days=10)).isoformat()}
        def missing(*args):raise Stop('dernieres_conclusions_adverses_absentes')
        result=run_cycle(self.d,{'slot':'501'},self.at+timedelta(hours=4),
                         calendar=self.events([event]),writing_selector=missing)
        self.assertEqual(result['source_counts']['removed'],1)
        self.assertEqual(result['source_counts']['blocked'],2)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_hearing'").fetchone()[0],0)
        self.assertEqual(self.f.box.appended,[])

    def test_new_mail_proposes_orchestration_once_without_sending(self):
        run_cycle(self.d,{'slot':'600'},self.at,calendar=self.events())
        key='a'*64
        self.d.db.execute('INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?)',
            (key,'needs_action','review','','DOS-001','Nouveau message',
             'client@example.test',self.at.isoformat(),self.at.isoformat(),None))
        self.d.db.commit();self.f.c['orchestrator']={'enabled':True}
        report=run_cycle(self.d,{'slot':'601'},self.at+timedelta(hours=4),calendar=self.events())
        self.assertEqual(report['source_counts']['mails'],1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='orchestrate_mail'").fetchone()[0],1)
        run_cycle(self.d,{'slot':'602'},self.at+timedelta(hours=8),calendar=self.events())
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='orchestrate_mail'").fetchone()[0],1)
        self.assertEqual(self.f.box.appended,[])

    def test_schedule_paris_dst_and_bounded_retry(self):
        before=datetime(2026,10,25,6,29,tzinfo=timezone.utc)
        first=schedule(self.d,before)
        self.assertFalse(first['brief_job'])
        due=schedule(self.d,before+timedelta(minutes=1))
        self.assertTrue(due['brief_job']);self.assertFalse(schedule(self.d,before+timedelta(minutes=2))['brief_job'])
        self.assertTrue(due['cycle_job']==0 or isinstance(due['cycle_job'],int))
        self.d.db.execute("UPDATE jobs SET status='error' WHERE id=?",(due['brief_job'],));self.d.db.commit()
        retry=schedule(self.d,before+timedelta(minutes=20))
        self.assertTrue(retry['brief_job'])
        self.d.perform('automation_setting',{'key':'preparation34_enabled','value':'no'})
        self.assertFalse(schedule(self.d,before+timedelta(hours=1))['enabled'])

    def test_briefing_marks_missing_calendar_and_page_escapes(self):
        self.f.c['calendar']['urls']=[]
        report=run_cycle(self.d,{'slot':'400'},self.at)
        self.assertFalse(report['calendar_refreshed'])
        morning=briefing(self.d,{'day':self.at.astimezone(ZoneInfo('Europe/Paris')).date().isoformat()},self.at)
        self.assertTrue(any('Agenda' in warning for warning in morning['coverage_warnings']))
        self.assertTrue(briefing(self.d,{'day':morning['day']},self.at)['idempotent'])
        _notice(self.d,'400','DOS-001','document','s1','fp',
                '<script>alert(1)</script>','Description','Ouvrir le document')
        auth={'prefix':'/agent-courriel','csrf':'token'}
        html=App(self.f.c,auth).page(self.f.c,auth,'/preparation-proactive',{})
        self.assertIn('Préparation proactive',html)
        self.assertIn('Analyser maintenant',html)
        self.assertIn('Agenda non configuré',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)


if __name__=='__main__':unittest.main()
