"""Operational reliability and destination read-back in 5.0.0."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.api import dispatch,openapi
from agent.common import Stop
from agent.desk import Desk
from agent.production390 import dashboard as production_dashboard
from agent.reliability393 import (job_health,ollama_health,retry_job,service_health,
                                  verify_imap_drafts,verify_nextcloud_outputs)
from agent.state import State
import test_agent as fixtures
import test_desk


class Reliability393(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_stale_and_looping_jobs_are_detected_without_args(self):
        old=(datetime.now(timezone.utc)-timedelta(hours=8)).isoformat()
        for number in range(3):
            self.d.db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,?,?,?,?,?,?)',
              ('index',json.dumps({'matter':'DOS-001'}),'error',old,old,
               json.dumps({'erreur':'connexion_nextcloud_echouee'}),50))
        self.d.db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,?,?,?,?,?,?)',
          ('sync','{}','pending',old,None,None,50));self.d.db.commit()
        report=job_health(self.d)
        self.assertEqual(len(report['loops']),1);self.assertEqual(len(report['stale']),1)
        self.assertNotIn('args',report['recent'][0]);self.assertEqual(report['status'],'error')

    def test_failed_job_can_be_retried_three_times_only(self):
        stamp=datetime.now(timezone.utc).isoformat()
        cur=self.d.db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,?,?,?,?,?,?)',
          ('index',json.dumps({'matter':'DOS-001'}),'error',stamp,stamp,
           json.dumps({'erreur':'indexation_interrompue'}),50));self.d.db.commit()
        original=cur.lastrowid
        for attempt in range(1,4):
            result=retry_job(self.d,original);self.assertEqual(result['attempt'],attempt)
            priority=self.d.db.execute('SELECT priority FROM jobs WHERE id=?',(result['job_id'],)).fetchone()[0]
            self.assertEqual(priority,50)
            self.d.db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(result['job_id'],));self.d.db.commit()
        with self.assertRaisesRegex(Stop,'nombre_reprises_depasse'):retry_job(self.d,original)

    def test_imap_green_requires_a_fresh_read_back(self):
        state=State(self.f.c['state_dir']);key='a'*64;mid='<axiorhub-'+'a'*64+'@mail-agent.local>'
        state.set(key,'<incoming@example.test>','thread','drafted','brouillon_imap_cree',mid)
        class Box:
            def __init__(self,c):pass
            def find_own_draft(self,value):return value==mid
            def close(self):pass
        with patch('agent.reliability393.Mailbox',Box):report=verify_imap_drafts(self.d)
        self.assertEqual(report['status'],'verified');self.assertTrue(report['verified'])
        class Missing(Box):
            def find_own_draft(self,value):return False
        with patch('agent.reliability393.Mailbox',Missing):report=verify_imap_drafts(self.d)
        self.assertEqual(report['status'],'error');self.assertFalse(report['verified'])

    def test_mail_dashboard_counts_only_destination_verified_drafts(self):
        stamp=datetime.now(timezone.utc).isoformat()
        self.d.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
          ('mail','draft_ready','drafted','prepared','DOS-001','Objet','client@example.test',
           stamp,stamp,stamp))
        self.d.db.execute('INSERT INTO production_outputs_v390 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
          ('mail-output',1,'prepare_reply','mail_draft','DOS-001','mail','delivered',
           'Brouillon','[]','{}',stamp,stamp));self.d.db.commit()
        self.assertEqual(production_dashboard(self.d)['counts']['verified_mail_drafts'],0)
        self.d.db.execute("UPDATE production_outputs_v390 SET status='verified' WHERE id='mail-output'")
        self.d.db.commit()
        self.assertEqual(production_dashboard(self.d)['counts']['verified_mail_drafts'],1)

    def test_nextcloud_green_requires_path_in_remote_listing(self):
        stamp=datetime.now(timezone.utc).isoformat();path='/Dossiers/DEMO/90_AxiorHub_Brouillons/note.docx'
        self.d.db.execute('INSERT INTO production_outputs_v390 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
          ('out',1,'create_document_files','document_files','DOS-001','source','delivered','Note',
           json.dumps([path]),'{}',stamp,stamp));self.d.db.commit()
        class Dav:
            def __init__(self,c):pass
            def list_folder(self,parent):return [{'path':path,'directory':False,'etag':'e1','modified':stamp,'size':42}]
        with patch('agent.reliability393.DAV',Dav):report=verify_nextcloud_outputs(self.d)
        self.assertEqual(report['status'],'verified')
        class Missing(Dav):
            def list_folder(self,parent):return []
        with patch('agent.reliability393.DAV',Missing):report=verify_nextcloud_outputs(self.d)
        self.assertEqual(report['status'],'warning')     # 5.6.8 : fichier supprimé ou déplacé = à vérifier, pas un incident

    def test_service_green_is_read_from_systemd(self):
        class Result:
            stdout='active\n';stderr='';returncode=0
        report=service_health(self.d,runner=lambda *a,**k:Result())
        self.assertEqual(report['status'],'verified');self.assertTrue(all(x['verified'] for x in report['services']))

    def test_ollama_is_not_green_until_a_selected_model_is_loaded(self):
        self.d.c['ollama']['url']='http://127.0.0.1:11434'
        class Http:
            def __init__(self,*a,**k):pass
            def json(self,method,path):
                return {'models':[{'name':'test-local'}]} if path=='/api/tags' else {'models':[]}
        with patch('agent.reliability393.HTTP',Http):report=ollama_health(self.d)
        self.assertEqual(report['status'],'warning')
        class Loaded(Http):
            def json(self,method,path):return {'models':[{'name':'test-local'}]}
        with patch('agent.reliability393.HTTP',Loaded):report=ollama_health(self.d)
        self.assertEqual(report['status'],'verified')

    def test_api_exposes_status_retry_and_version(self):
        spec=openapi('https://agent.example.test')
        self.assertEqual(spec['info']['version'],'5.6.10')
        for path in ('/system/status','/system/checks/run','/system/openrouter-test','/jobs/{job_id}/retry'):
            self.assertIn(path,spec['paths'])
        status=dispatch(self.d,'/system/status','GET')
        self.assertIn('jobs',status);self.assertIn('permissions',status)


class Browser393(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_single_system_status_page_has_controls_and_green_policy(self):
        body=self.request('/etat-systeme')['body']
        for value in ('État du système','Contrôler maintenant','Tester OpenRouter sans donnée de dossier',
                      'Traitements récents','Preuves de destination','app520.css','Version 5.6.10'):
            self.assertIn(value,body)


if __name__=='__main__':unittest.main()
