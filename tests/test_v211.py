from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

import test_agent as fixtures
import test_desk
from agent.api import dispatch, openapi
from agent.common import Stop
from agent.dav import parse_todos
from agent.desk import Desk
from agent.operations import automation_tick
from agent.workplan import (apply_plan, approve_plan, calendar_events, extract_tasks,
                            list_tasks, propose_plan, sync_tasks, update_task_status)


class FakeWorkflowDAV:
    def __init__(self):
        self.created_tasks=[];self.created_events=[];self.updated=[]
        self.agenda=[{
          'uid':'audience-1','recurrence_id':'20260914T090000','start':'2026-09-14T09:00:00+02:00',
          'end':'2026-09-14T10:00:00+02:00','summary':'Audience DOS-001',
          'description':'Client DEMO','location':'TJ Lyon','status':'CONFIRMED','busy':True,
          'source_url':'https://cloud.test/calendars/tech/cabinet/','href':'/dav/audience-1.ics','etag':'"e1"'}]
        self.remote_tasks=[{
          'uid':'human-task','title':'Relire DOS-001','description':'Tâche existante',
          'start':'','due':'2026-09-18T10:00:00+02:00','completed':'',
          'status':'IN-PROCESS','priority':4,'percent':50,
          'source_url':'https://cloud.test/calendars/tech/tasks/','href':'/dav/human-task.ics','etag':'"t1"'}]

    def calendar_url(self,url):return url.rstrip('/')+'/'
    def events(self,urls,start,end,tz):return list(self.agenda)
    def todos(self,urls,tz,limit=1000):return list(self.remote_tasks)[:limit]
    def put_todo(self,calendar,uid,title,description='',start=None,due=None,
                 status='NEEDS-ACTION',priority=5,percent=0,etag=''):
        item={'calendar':calendar,'uid':uid,'title':title,'status':status,'etag':etag}
        (self.updated if etag else self.created_tasks).append(item);return item
    def put_event(self,calendar,proposal_id,title,start,end,description):
        self.created_events.append({'calendar':calendar,'id':proposal_id,'title':title,
                                    'start':start,'end':end})
        return 'axiorhub-'+proposal_id+'@mail-agent.local'


class WorkPlanning211Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['calendar'].update({'working_hours':[['09:00','12:00'],['14:00','18:00']]})
        self.f.c['planning']={'timezone':'Europe/Paris','weekdays':[0,1,2,3,4],
          'working_hours':[['09:00','12:00'],['14:00','18:00']],'max_daily_minutes':360}
        self.f.c['nextcloud_workflow']={
          'enabled':True,'url':'https://cloud.test','username':'tech@example.test',
          'password_file':'/secret','roots':['/Dossiers'],
          'calendar_read_urls':['https://cloud.test/calendars/tech/cabinet/'],
          'task_calendar_url':'https://cloud.test/calendars/tech/tasks/',
          'planning_calendar_url':'https://cloud.test/calendars/tech/planning/',
          'notes_enabled':False,'deck_enabled':False}
        self.f.c['automation']={'nextcloud_tasks_enabled':True,
          'nextcloud_tasks_interval_minutes':15,'health_enabled':False,'sync_enabled':False,
          'reconcile_inbox_enabled':False,'classify_portfolio_enabled':False,
          'index_all_enabled':False}
        self.f.c['proactive']={'enabled':False}
        self.d=Desk(self.f.c);self.dav=FakeWorkflowDAV()

    def test_vtodo_parser_preserves_state_dates_priority_and_progress(self):
        data=('BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:task-1\r\n'
          'SUMMARY:Préparer conclusions\r\nDESCRIPTION:DOS-001\\nSource interne\r\n'
          'DTSTART;TZID=Europe/Paris:20260914T090000\r\nDUE:20260918T150000Z\r\n'
          'STATUS:IN-PROCESS\r\nPRIORITY:3\r\nPERCENT-COMPLETE:50\r\n'
          'END:VTODO\r\nEND:VCALENDAR\r\n')
        task=parse_todos(data,'Europe/Paris')[0]
        self.assertEqual(task['status'],'IN-PROCESS');self.assertEqual(task['priority'],3)
        self.assertEqual(task['percent'],50);self.assertIn('+02:00',task['start'])

    def test_period_read_keeps_unlinked_events_and_refresh_removes_deleted_events(self):
        result=calendar_events(self.d,'2026-09-14T00:00:00+02:00',
          '2026-09-19T00:00:00+02:00',dav=self.dav)
        self.assertEqual(result['counts']['all'],1);self.assertEqual(result['events'][0]['matter'],'DOS-001')
        self.dav.agenda=[{'uid':'private','recurrence_id':'','start':'2026-09-15T08:00:00+02:00',
          'end':'2026-09-15T09:00:00+02:00','summary':'Dentiste','description':'',
          'location':'Lyon','status':'CONFIRMED','busy':True,
          'source_url':'https://cloud.test/calendars/tech/cabinet/','href':'/dav/private.ics','etag':'"e2"'}]
        refreshed=calendar_events(self.d,'2026-09-14T00:00:00+02:00',
          '2026-09-19T00:00:00+02:00',dav=self.dav)
        self.assertEqual([x['title'] for x in refreshed['events']],['Dentiste'])
        self.assertEqual(refreshed['counts']['unlinked'],1)

    def test_task_extraction_routes_outputs_and_keeps_forbidden_steps_manual(self):
        tasks=extract_tasks(self.d,
          '- DOS-001 : répondre au mail client (20 min)\n'
          '- DOS-001 : actualiser conclusions (2h30)\n'
          '- Personnel : payer le loyer (10 min)\n'
          '- DOS-001 : préparer conclusions après le 23 septembre 2026 (1h)',
          '2026-09-14T00:00:00+02:00')
        self.assertEqual(len(tasks),4);self.assertEqual(tasks[0]['action_type'],'email_draft')
        self.assertEqual(tasks[1]['duration_minutes'],150);self.assertEqual(tasks[1]['action_type'],'act_project')
        self.assertIn('paiement',tasks[2]['forbidden_steps'])
        self.assertEqual(tasks[3]['not_before'],'2026-09-23')

    def test_group_approval_creates_only_proposed_spaces_and_queues_internal_outputs(self):
        self.d.db.execute('''INSERT INTO work_items VALUES
          (?,?,?,?,?,?,?,?,?,?)''',('a'*64,'needs_action','review','', 'DOS-001','Réponse client',
          'client@example.test','2026-09-12T08:00:00+00:00',self.d.now(),''));self.d.db.commit()
        proposal=propose_plan(self.d,{'task_text':
          '- DOS-001 : répondre au mail client (20 min)\n'
          '- DOS-001 : préparer conclusions (2h)\n'
          '- Personnel : payer le loyer (10 min)',
          'period_start':'2026-09-14T00:00:00+02:00',
          'period_end':'2026-09-19T00:00:00+02:00','max_daily_minutes':240},dav=self.dav)
        self.assertEqual(self.dav.created_tasks,[]);self.assertEqual(self.dav.created_events,[])
        with self.assertRaisesRegex(Stop,'confirmation_programme_invalide'):
            approve_plan(self.d,proposal['proposal_id'],'000000')
        accepted=approve_plan(self.d,proposal['proposal_id'],proposal['confirmation_code'])
        self.assertEqual(accepted['status'],'approved')
        result=apply_plan(self.d,{'proposal':proposal['proposal_id']},dav=self.dav)
        self.assertEqual(result['tasks_created'],3);self.assertGreater(result['slots_created'],0)
        self.assertEqual(result['forbidden_actions_executed'],0);self.assertFalse(result['email_sent'])
        kinds={x['kind'] for x in result['outputs']}
        self.assertIn('email_draft',kinds);self.assertIn('act_project',kinds);self.assertIn('manual',kinds)
        self.assertTrue(all(x['calendar']==self.f.c['nextcloud_workflow']['task_calendar_url']
                            for x in self.dav.created_tasks))
        self.assertTrue(all(x['calendar']==self.f.c['nextcloud_workflow']['planning_calendar_url']
                            for x in self.dav.created_events))
        self.assertTrue(all(' — ' in x['title'] and x['title'].endswith('[AxiorHub]')
                            for x in self.dav.created_tasks))
        self.assertTrue(all(' — ' in x['title'] and x['title'].endswith('[AxiorHub]')
                            for x in self.dav.created_events))
        own=next(x for x in list_tasks(self.d) if x['external_uid'].startswith('axiorhub-'))
        changed=update_task_status(self.d,{'task':own['id'],'status':'completed'},dav=self.dav)
        self.assertTrue(changed['nextcloud_updated']);self.assertEqual(self.dav.updated[-1]['etag'],'*')

    def test_task_sync_and_status_update(self):
        result=sync_tasks(self.d,dav=self.dav)
        self.assertEqual(result['synchronized'],1)
        task=list_tasks(self.d,'in_progress')[0]
        self.assertEqual(task['title'],'Relire DOS-001');self.assertEqual(task['matter'],'DOS-001')
        with self.assertRaisesRegex(Stop,'tache_externe_lecture_seule'):
            update_task_status(self.d,{'task':task['id'],'status':'completed'},dav=self.dav)

    def test_automatic_sync_is_bounded_maintenance(self):
        automation_tick(self.d)
        row=self.d.db.execute("SELECT kind,priority FROM jobs WHERE kind='sync_caldav_tasks'").fetchone()
        self.assertEqual((row['kind'],row['priority']),('sync_caldav_tasks',50))

    def test_api_and_openwebui_expose_supervised_planning_without_external_actions(self):
        spec=openapi('https://cabinet.test')
        self.assertEqual(spec['info']['version'],'5.6.26')
        self.assertIn('/calendar/events',spec['paths']);self.assertIn('/planning/proposals',spec['paths'])
        capabilities=dispatch(self.d,'/capabilities','GET')
        self.assertIn('read_calendar_period',capabilities['can'])
        self.assertIn('create_planning_events',capabilities['requires_confirmation'])
        self.assertIn('send_email',capabilities['never'])
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('lire_l_agenda_sur_une_periode',tool)
        self.assertIn('proposer_un_programme_de_travail',tool)
        self.assertNotIn('def envoyer_',tool)


class Web211Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_planning_page_is_visible_static_and_explains_group_confirmation(self):
        page=self.request('/planning',query='vue=organiser')['body']
        self.assertIn('Agenda et tâches',page);self.assertIn('créer ce programme dans Nextcloud',page)
        self.assertIn('Version 5.6.26',page);self.assertNotIn('http-equiv="refresh"',page)


if __name__=='__main__':unittest.main()
