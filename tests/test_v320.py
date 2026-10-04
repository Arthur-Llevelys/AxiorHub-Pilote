import json
from pathlib import Path
import shutil
import subprocess
import unittest

from agent.common import Stop, load_matters
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.web import App
from agent.workstation32 import briefing, search
import test_agent as fixtures


class Workstation320Tests(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write')
        f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.d=Desk(f.c)

    def test_local_search_sources_filters_and_provenance(self):
        index=DocumentIndex(self.f.c['state_dir'])
        index.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',
            ('DOS-001','/Dossiers/DEMO/Contrat.docx','etag','2026-09-18T09:00:00+00:00',
             'Le contrat a été relu par le cabinet.',''))
        index.db.execute('INSERT INTO search VALUES (?,?,?)',
                         ('DOS-001','/Dossiers/DEMO/Contrat.docx','Le contrat a été relu par le cabinet.'))
        index.put_source('DOS-001','imap://local/piece-1','Contrat en pièce jointe','etag-pj',
                         '2026-09-18T12:00:00+00:00','attachment')
        index.put_source('DOS-001','imap://local/recu-1','Le client parle du contrat en cours','etag-mail',
                         '2026-09-18T10:00:00+00:00','email_received',
                         {'subject':'Courriel du client','message_id':'<mail-1@example.test>'})
        self.d.db.execute('''INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?)''',
            ('a'*64,'needs_action','review','','DOS-001','Contrat à examiner',
             'client@example.test','2026-09-18T10:00:00+00:00','2026-09-18T10:00:00+00:00',None))
        self.d.db.execute('''INSERT INTO calendar_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('event','event','', '/calendar','/calendar/event.ics','etag','Revue du contrat','', '',
             '2026-09-22T09:00:00+00:00','2026-09-22T10:00:00+00:00',1,
             'DOS-001','[]','2026-09-19T10:00:00+00:00'))
        self.d.db.commit()
        result=search(self.d,'contrat')
        self.assertEqual({'Courriel','Courriel indexé','Document','Pièce jointe','Événement'},
                         {x['kind'] for x in result['results']})
        self.assertTrue(all(x['origin'] for x in result['results']))
        self.assertEqual(len(search(self.d,'contrat',source='documents')['results']),1)
        self.assertEqual(len(search(self.d,'contrat',source='documents',after='2026-09-19')['results']),0)
        self.assertTrue(search(self.d,'client@example.test',source='contacts')['results'])
        with self.assertRaisesRegex(Stop,'source_recherche_invalide'):
            search(self.d,'contrat',source='other')
        with self.assertRaisesRegex(Stop,'dossier_absent'):
            search(self.d,'contrat',matter='DOS-999')
        with self.assertRaisesRegex(Stop,'dates_recherche_inversees'):
            search(self.d,'contrat',after='2026-10-01',before='2026-09-01')

    def test_pages_use_real_data_escape_and_keep_legacy_routes(self):
        auth={'prefix':'/agent-courriel','csrf':'token'}
        app=App(self.f.c,auth)
        for route,label in [('/rechercher','Rechercher'),('/contacts','Contacts'),
                            ('/taches','Tâches'),('/agenda','Agenda'),
                            ('/parametres','Paramètres'),('/accueil','Tableau de bord')]:
            page=app.page(self.f.c,auth,route,{})
            self.assertIn(label,page)
            self.assertIn('/static/app520.css',page)   # 5.2.0 : feuilles regroupées
            self.assertIn('/static/v320.js',page)
            self.assertIn('aria-label="Navigation principale"',page)
            self.assertIn('id="ws-sidebar-toggle"',page)
            self.assertIn('id="ws-theme-toggle"',page)
        page=app.page(self.f.c,auth,'/rechercher',{'q':'<script>'})
        self.assertNotIn('<script>',page)
        self.assertIn('Portefeuille du cabinet',app.page(self.f.c,auth,'/dossiers',{}))
        matter=app.page(self.f.c,auth,'/matter',{'id':'DOS-001'})
        self.assertIn('aria-label="Sections du dossier"',matter)
        self.assertIn('id="ws-matter-admin"',matter)

    @unittest.skipUnless(shutil.which('node'),'Node.js indisponible')
    def test_theme_and_sidebar_browser_controls(self):
        script=Path(__file__).with_name('interface-v320.js')
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_unsynchronized_invoices_are_unknown_not_zero(self):
        from agent.workstation import unpaid_summary
        invoices=unpaid_summary(self.d)
        self.assertFalse(invoices['synchronized'])
        page=App(self.f.c,{'prefix':'/agent-courriel','csrf':'token'}).page(
            self.f.c,{'prefix':'/agent-courriel','csrf':'token'},'/administration',{})
        self.assertIn('nombre et solde inconnus',page)

    def test_confirmation_required_for_only_this_matter_link(self):
        with self.assertRaisesRegex(Stop,'confirmation_retrait_requise'):
            self.d.perform('remove_contact',{'matter':'DOS-001','email':'client@example.test'})
        self.assertEqual(self.d.perform('remove_contact',{'matter':'DOS-001',
            'email':'client@example.test','confirm':'yes'})['correspondant'],'retire_du_registre_web')
        self.assertEqual(load_matters(self.f.c)[0]['correspondents'],[])

    def test_briefing_explains_unknown_freshness(self):
        view=briefing(self.d)
        self.assertIsNone(view['calendar_last_fetched'])
        self.assertEqual(view['events'],[])
        page=App(self.f.c,{'prefix':'/agent-courriel','csrf':'token'}).page(
            self.f.c,{'prefix':'/agent-courriel','csrf':'token'},'/accueil',{})
        self.assertIn('inconnue',page)
        self.assertIn('ne certifie pas que l’agenda est vide',page)


if __name__=='__main__':unittest.main()
