"""Regressions for case disambiguation, document import and scoped uploads."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import base64
import io
import json
import unittest
import zipfile
import xml.etree.ElementTree as ET

from agent.agenda36 import page as calendar_page
from agent.cabinet_docs33 import _neutralize_hyperlinks,inspect_template,import_template
from agent.common import Stop
from agent.desk import Desk
from agent.improvements36 import matter_option,create_task,delegate_task,upload_attachment,attachment_source
from agent.matcher import rank
from agent.workplan import _matter_candidates
from agent.web import App
from agent.workstation import CABINET_SITES,external_links,save_external_url
import test_agent as fixtures


class Improvements360(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c)

    def test_two_similarly_named_cases_remain_distinct(self):
        matters=[
            {'id':'2018091201','client_name':'MARTIN',
             'path':'/Dossiers/MARTIN - SOCIETE-A - Assistance - 2018091201',
             'correspondents':[]},
            {'id':'DOSSIER-02','client_name':'MARTIN',
             'path':'/Dossiers/MARTIN - SOCIETE-B','correspondents':[]}]
        self.assertNotEqual(matter_option(matters[0]),matter_option(matters[1]))
        self.assertEqual(_matter_candidates(matters,'Audience du 2018091201'),['2018091201'])
        self.assertEqual(_matter_candidates(matters,'Rendez-vous MARTIN - SOCIETE-B'),['DOSSIER-02'])
        self.assertEqual(len(_matter_candidates(matters,'Rendez-vous MARTIN')),2)
        class Mail:
            subject='MARTIN - SOCIETE-B';text='';sender='inconnu@example.test'
        chosen,_,candidates=rank(Mail(),matters)
        self.assertEqual(candidates[0]['matter']['id'],'DOSSIER-02')
        self.assertIsNone(chosen)  # Lacking a verified correspondent or exact reference.

    def test_task_creation_and_scoped_attachment(self):
        created=create_task(self.d,'Préparer les pièces',matter='DOS-001',due='2026-09-21')
        row=self.d.db.execute('SELECT matter,title,status FROM tasks WHERE id=?',(created['id'],)).fetchone()
        self.assertEqual(tuple(row),('DOS-001','Préparer les pièces','open'))
        delegated=delegate_task(self.d,created['id'])
        jobs=list(self.d.db.execute('SELECT id,kind,priority FROM jobs WHERE id IN (?,?) ORDER BY id',
                  (delegated['index_job'],delegated['job_id'])))
        self.assertEqual([(r['kind'],r['priority']) for r in jobs],
                         [('index',0),('assistant_answer',0)])
        uploaded=upload_attachment(self.d,'Pièce client confidentielle'.encode(),'preuve.txt',matter='DOS-001')
        source=attachment_source(self.d,uploaded['attachment_id'],'DOS-001')
        self.assertIn('Pièce client',source['excerpt'])
        with self.assertRaisesRegex(Stop,'piece_assistant_autre_contexte'):
            attachment_source(self.d,uploaded['attachment_id'])
        with self.assertRaisesRegex(Stop,'format_piece_assistant_invalide'):
            upload_attachment(self.d,b'<script/>','actif.svg',matter='DOS-001')

    def test_calendar_views_link_only_exact_case(self):
        from agent.workplan import _calendar_urls
        self.f.c.setdefault('calendar',{})['urls']=['https://example.test/calendar']
        events=[{'starts':'2026-09-21T07:00:00+00:00','ends':'2026-09-21T08:00:00+00:00',
                 'title':'Audience DOS-001','matter':'DOS-001','matter_candidates':['DOS-001']},
                {'starts':'2026-09-22T07:00:00+00:00','ends':'2026-09-22T08:00:00+00:00',
                 'title':'MARTIN','matter':'','matter_candidates':['A','B']}]
        fake={'events':events,'counts':{'linked':1}}
        link=lambda p,label,**kw:'<a href="'+p+'?id='+kw.get('id','')+'">'+label+'</a>'
        url=lambda p,**kw:p+'?'+'&'.join(k+'='+str(v) for k,v in kw.items())
        with patch('agent.agenda36.calendar_events',return_value=fake):
            for view in ('list','week','month'):
                html=calendar_page(self.d,{'view':view,'date':'2026-09-21'},link,url)
                self.assertIn('Audience DOS-001',html)
                self.assertEqual(html.count('Ouvrir le dossier'),1 if view!='month' else 1)
                self.assertIn('Rapprochement ambigu',html)

    def test_all_seven_cabinet_sites_appear_in_navigation_and_settings(self):
        auth={'prefix':'/agent-courriel','csrf':'token','origin':'https://cabinet.example.test'}
        app=App(self.f.c,auth)
        page=app.page(self.f.c,auth,'/accueil',{})
        self.assertEqual(len(CABINET_SITES),7)
        for _,label,url in CABINET_SITES:
            self.assertIn(url,page)
            self.assertIn(label,page)
        saved=external_links(self.d)
        self.assertEqual({key for key,_,_ in CABINET_SITES} & saved.keys(),
                         {key for key,_,_ in CABINET_SITES})
        save_external_url(self.d,'honoraires','https://honorium.example.test/')
        updated=app.page(self.f.c,auth,'/accueil',{})
        self.assertIn('https://honorium.example.test/',updated)
        self.assertNotIn('https://honoraires.example.com/',updated)
        settings=app.page(self.f.c,auth,'/parametres',{'tab':'connexions'})
        self.assertIn('https://honorium.example.test/',settings)

    def test_template_hyperlinks_cleaned_other_external_targets_refused(self):
        fixture=Path(__file__).with_name('fixture-modele-fictif.docx').read_bytes()
        relname='word/_rels/document.xml.rels'
        with zipfile.ZipFile(BytesIO(fixture)) as archive:
            orig={n:archive.read(n) for n in archive.namelist()}
        rels=ET.fromstring(orig[relname]);ns='http://schemas.openxmlformats.org/package/2006/relationships'
        ET.SubElement(rels,'{'+ns+'}Relationship',{'Id':'rIdExternalDemo',
            'Type':'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
            'Target':'https://example.test','TargetMode':'External'})
        orig[relname]=ET.tostring(rels)
        w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        root=ET.fromstring(orig['word/document.xml'])
        paragraph=ET.SubElement(root.find('{'+w+'}body'),'{'+w+'}p')
        hyperlink=ET.SubElement(paragraph,'{'+w+'}hyperlink',{'{'+r+'}id':'rIdExternalDemo'})
        run=ET.SubElement(hyperlink,'{'+w+'}r')
        ET.SubElement(run,'{'+w+'}t').text='Texte affiché'
        orig['word/document.xml']=ET.tostring(root)
        def build(parts):
            out=BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                for name,raw in parts.items():z.writestr(name,raw)
            return out.getvalue()
        raw=build(orig)
        with self.assertRaisesRegex(Stop,'lien_externe_modele_word_refuse'):inspect_template(raw)
        clean,count=_neutralize_hyperlinks(raw)
        self.assertEqual(count,1)
        self.assertTrue(inspect_template(clean)['has_images'])
        from test_v330 import fake_pages
        imported=import_template(self.d,raw,'Modèle avec lien.docx',fake_pages)
        self.assertEqual(imported['neutralized_hyperlinks'],1)
        with zipfile.ZipFile(BytesIO(clean)) as z:
            self.assertIn(b'Texte affich',z.read('word/document.xml'))
            self.assertNotIn(b'TargetMode="External"',z.read(relname))
        settings=ET.fromstring(orig['word/settings.xml'])
        ET.SubElement(settings,'{'+w+'}attachedTemplate',{'{'+r+'}id':'rIdTemplateDemo'})
        orig['word/settings.xml']=ET.tostring(settings)
        template_rels=ET.Element('{'+ns+'}Relationships')
        ET.SubElement(template_rels,'{'+ns+'}Relationship',{'Id':'rIdTemplateDemo',
            'Type':'http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate',
            'Target':'file:///outside/Model.dotx','TargetMode':'External'})
        orig['word/_rels/settings.xml.rels']=ET.tostring(template_rels)
        cleaned,removed=_neutralize_hyperlinks(build(orig))
        self.assertEqual(removed,2)
        self.assertTrue(inspect_template(cleaned)['has_images'])
        with zipfile.ZipFile(BytesIO(cleaned)) as z:
            self.assertNotIn(b'attachedTemplate',z.read('word/settings.xml'))
        rels[-1].set('Type','http://schemas.openxmlformats.org/officeDocument/2006/relationships/image')
        orig[relname]=ET.tostring(rels)
        with self.assertRaisesRegex(Stop,'lien_externe_modele_word_refuse'):
            _neutralize_hyperlinks(build(orig))


class AttachmentWebTests(unittest.TestCase):
    setUp=__import__('test_desk').WebTests.setUp

    def test_attachment_endpoint_authentication_and_csrf(self):
        def request(csrf='test-csrf',authorization=True,matter=''):
            raw=b'Source du dossier local'
            env={'REQUEST_METHOD':'POST','PATH_INFO':'/agent-courriel/assistant/attachment',
                'QUERY_STRING':'','HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https',
                'wsgi.url_scheme':'http','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),
                'CONTENT_TYPE':'application/octet-stream','HTTP_ORIGIN':self.origin,
                'HTTP_X_CSRF_TOKEN':csrf,'HTTP_X_ATTACHMENT_NAME':'preuve.txt',
                'HTTP_X_ATTACHMENT_MATTER':matter}
            if authorization:
                env['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(
                    ('admin:'+self.password).encode()).decode()
            response={};body=b''.join(self.app(env,lambda status,headers:response.update(
                status=status,headers=dict(headers))))
            return response,body
        self.assertTrue(request(csrf='invalid')[0]['status'].startswith('400'))
        self.assertTrue(request(authorization=False)[0]['status'].startswith('401'))
        status,body=request()
        self.assertTrue(status['status'].startswith('200'))
        self.assertRegex(json.loads(body)['attachment_id'],r'^[a-f0-9]{32}$')


if __name__=='__main__':unittest.main()
