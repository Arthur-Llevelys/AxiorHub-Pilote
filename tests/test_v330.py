"""Word fidelity, supervised creation, optimistic concurrency and real preview."""
from io import BytesIO
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
import zipfile
from unittest.mock import patch

from agent.cabinet_docs33 import (SAMPLE, approve_template, confirm, fill_template,
    import_template, inspect_template, prepare, preview_png, project, render_pages)
from agent.common import Stop
from agent.desk import Desk
from agent.web import App
import test_agent as fixtures
import test_desk


FIXTURE=Path(__file__).with_name('fixture-modele-fictif.docx').read_bytes()


def fake_pages(raw,folder,variant):
    target=folder/variant;target.mkdir(parents=True,exist_ok=True)
    (target/'1.png').write_bytes(b'\x89PNG\r\n\x1a\nprivate-preview')
    return 1


class CabinetDAV:
    def __init__(self):self.files={};self.etags={};self.fail_once=False;self.puts=[]
    def list_folder(self,folder):
        return [{'path':p,'directory':False,'etag':self.etags[p],'size':len(raw)}
                for p,raw in self.files.items() if p.rsplit('/',1)[0]==folder]
    def download(self,item):return self.files[item['path']]
    def ensure_folder(self,folder,boundary):
        if not folder.startswith(boundary+'/'):raise Stop('destination_hors_dossier')
    def put_file(self,path,raw,content_type):
        if path in self.files:raise Stop('condition_prealable_echouee')
        self.files[path]=raw;self.etags[path]='"1"';self.puts.append((path,content_type))
        if self.fail_once:self.fail_once=False;raise Stop('delai_http_depasse')
    def file_web_url(self,path):return 'https://cloud.example.test/index.php/f/123'
    def edit(self,path,raw):self.files[path]=raw;self.etags[path]='"new"'


class CabinetWordTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c);self.dav=CabinetDAV()
        self.raw=FIXTURE

    def model(self):
        record=import_template(self.d,self.raw,'Lettre du cabinet.docx',fake_pages)
        return record['template_id']

    def approved(self):
        tid=self.model()
        sha=hashlib.sha256(self.raw).hexdigest()
        approve_template(self.d,tid,sha,'yes')
        return tid

    def args(self,tid):return {'matter':'DOS-001','template_id':tid,'date':'2026-09-20',
       'destinataire':'Madame Exemple','qualite':'Directrice','adresse':'4 rue Exemple',
       'reference':'DOS-001','objet':'Projet fictif','envoi':'courriel',
       'appel':'madame','corps':'Premier passage.\n\nSecond passage.',
       'fin':'formelle','signature':'Maître Démo'}

    def test_split_runs_original_parts_and_static_date(self):
        info=inspect_template(self.raw)
        self.assertTrue(info['has_headers'] and info['has_footers'] and info['has_tables'] and info['has_images'])
        produced=fill_template(self.raw,SAMPLE)
        with zipfile.ZipFile(BytesIO(self.raw)) as old,zipfile.ZipFile(BytesIO(produced)) as new:
            self.assertEqual(set(old.namelist()),set(new.namelist()))
            for part in ['word/styles.xml','word/header1.xml','word/footer1.xml']:
                self.assertEqual(old.read(part),new.read(part))
            images=[p for p in old.namelist() if p.startswith('word/media/')]
            self.assertTrue(images)
            for part in images:self.assertEqual(old.read(part),new.read(part))
            text=new.read('word/document.xml').decode()
            self.assertIn('Madame Martin',text)
            self.assertIn('Deuxième paragraphe',text)
            self.assertNotIn('{{',text)
            self.assertNotIn('DATE',text)

    def test_approval_is_explicit_and_document_is_exclusive_and_verified(self):
        tid=self.model();self.assertTrue(preview_png(self.d,tid,'original',1).startswith(b'\x89PNG'))
        with self.assertRaisesRegex(Stop,'validation_visuelle_modele_requise'):
            approve_template(self.d,tid,'0'*64,'yes')
        with self.assertRaisesRegex(Stop,'modele_word_non_valide'):
            prepare(self.d,self.args(tid),self.dav,fake_pages)
        approve_template(self.d,tid,hashlib.sha256(self.raw).hexdigest(),'yes')
        prepared=prepare(self.d,self.args(tid),self.dav,fake_pages)
        self.assertEqual(prepared['pages'],1)
        self.assertTrue(preview_png(self.d,prepared['project_id'],'prepared',1).startswith(b'\x89PNG'))
        with self.assertRaisesRegex(Stop,'confirmation_courrier_invalide'):
            confirm(self.d,{'project_id':prepared['project_id'],'confirmation_code':'000000',
                            'destination':prepared['destination']},self.dav)
        arguments={'project_id':prepared['project_id'],'confirmation_code':prepared['confirmation_code'],
                   'destination':prepared['destination']}
        self.dav.fail_once=True
        partial=confirm(self.d,arguments,self.dav)
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(len(self.dav.puts),1)
        done=confirm(self.d,arguments,self.dav)
        self.assertEqual(done['status'],'done')
        self.assertEqual(len(self.dav.puts),1)
        self.assertIn('/index.php/f/123',done['created_files'][0]['edit_url'])
        with self.assertRaisesRegex(Stop,'deja_traite'):confirm(self.d,arguments,self.dav)

    def test_edit_source_stale_rejected_and_new_version_copies_exact_bytes(self):
        tid=self.approved();initial=prepare(self.d,self.args(tid),self.dav,fake_pages)
        result=confirm(self.d,{'project_id':initial['project_id'],'destination':initial['destination'],
            'confirmation_code':initial['confirmation_code']},self.dav)
        source=result['created_files'][0]['path'];changed=fill_template(self.raw,{**SAMPLE,'objet':'Objet édité dans OnlyOffice'})
        self.dav.edit(source,changed)
        revision=prepare(self.d,{'matter':'DOS-001','template_id':tid,'kind':'revision','source_path':source},
                         self.dav,fake_pages)
        self.assertTrue(revision['destination'].endswith('_v002.docx'))
        arguments={'project_id':revision['project_id'],'destination':revision['destination'],
                   'confirmation_code':revision['confirmation_code']}
        self.dav.edit(source,fill_template(self.raw,{**SAMPLE,'objet':'Autre modification'}))
        with self.assertRaisesRegex(Stop,'version_source_modifiee'):confirm(self.d,arguments,self.dav)
        self.assertNotIn(revision['destination'],self.dav.files)
        revision=prepare(self.d,{'matter':'DOS-001','template_id':tid,'kind':'revision','source_path':source},
                         self.dav,fake_pages)
        done=confirm(self.d,{'project_id':revision['project_id'],'destination':revision['destination'],
                             'confirmation_code':revision['confirmation_code']},self.dav)
        self.assertEqual(done['files_overwritten'],0)
        self.assertEqual(self.dav.files[revision['destination']],self.dav.files[source])
        with zipfile.ZipFile(BytesIO(self.dav.files[revision['destination']])) as final:
            self.assertIn('Autre modification',final.read('word/document.xml').decode())

    def test_collision_and_invalid_template_and_ui(self):
        tid=self.approved();proposal=prepare(self.d,self.args(tid),self.dav,fake_pages)
        self.dav.files[proposal['destination']]=b'other';self.dav.etags[proposal['destination']]='"other"'
        with self.assertRaisesRegex(Stop,'nom_fichier_deja_existant'):
            confirm(self.d,{'project_id':proposal['project_id'],'destination':proposal['destination'],
                            'confirmation_code':proposal['confirmation_code']},self.dav)
        with self.assertRaisesRegex(Stop,'balise_corps_paragraphe_unique_requise'):
            inspect_template(fill_template(self.raw,SAMPLE))
        auth={'prefix':'/agent-courriel','csrf':'token'}
        html=App(self.f.c,auth).page(self.f.c,auth,'/modeles-word',{'project':proposal['project_id']})
        self.assertIn('Documents du cabinet',html)
        self.assertIn('Créer cette nouvelle version',html)
        self.assertIn('v330.js',html)
        self.assertIn('/documents/preview/',html)

    @unittest.skipUnless(shutil.which('soffice') and shutil.which('pdftoppm') and shutil.which('pdfinfo'),
                         'LibreOffice / Poppler absents')
    def test_actual_all_page_raster_and_private_file(self):
        tid=import_template(self.d,self.raw,'Modèle fictif.docx')['template_id']
        data=preview_png(self.d,tid,'sample',1)
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        from agent.cabinet_docs33 import _root
        path=_root(self.d)/'previews'/tid/'sample'/'1.png'
        self.assertEqual(path.stat().st_mode & 0o777,0o600)


class CabinetWordWebTests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_authenticated_upload_preview_and_csrf(self):
        from agent import cabinet_docs33 as mod
        raw=FIXTURE
        def call(csrf='test-csrf',authenticated=True):
            env={'REQUEST_METHOD':'POST','PATH_INFO':'/agent-courriel/templates/upload',
                 'QUERY_STRING':'','HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https',
                 'wsgi.url_scheme':'http','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),
                 'CONTENT_TYPE':mod.DOCX,'HTTP_X_CSRF_TOKEN':csrf,
                 'HTTP_X_TEMPLATE_NAME':'Fictif.docx','HTTP_ORIGIN':self.origin}
            if authenticated:
                env['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(('admin:'+self.password).encode()).decode()
            result={};body=b''.join(self.app(env,lambda status,headers:result.update(
                status=status,headers=dict(headers))));return result,body
        original=mod.import_template
        with patch.object(mod,'import_template',side_effect=lambda d,b,n:original(d,b,n,fake_pages)):
            bad,_=call(csrf='wrong');self.assertTrue(bad['status'].startswith('400'))
            anonymous,_=call(authenticated=False);self.assertTrue(anonymous['status'].startswith('401'))
            good,body=call();self.assertTrue(good['status'].startswith('200'))
        tid=json.loads(body)['template_id']
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/agent-courriel/documents/preview/'+tid+'/original/1.png',
             'QUERY_STRING':'','HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https',
             'wsgi.url_scheme':'http','wsgi.input':io.BytesIO(b''),'CONTENT_LENGTH':'0',
             'HTTP_AUTHORIZATION':'Basic '+base64.b64encode(('admin:'+self.password).encode()).decode()}
        preview={}
        data=b''.join(self.app(env,lambda status,headers:preview.update(status=status,headers=dict(headers))))
        self.assertEqual(preview['headers']['Content-Type'],'image/png')
        self.assertTrue(preview['status'].startswith('200'))
        self.assertTrue(data.startswith(b'\x89PNG'))
        invalid=self.request('/documents/preview/'+tid+'/original/2.png')
        self.assertTrue(invalid['status'].startswith('400'))


if __name__=='__main__':unittest.main()
