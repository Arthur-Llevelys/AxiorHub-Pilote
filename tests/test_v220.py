import json
from pathlib import Path
import unittest

import test_agent as fixtures
from agent.api import dispatch, openapi
from agent.common import Stop
from agent.dav import DAV
from agent.desk import Desk
from agent.document_projects import _select_source, confirm_creation, prepare, preview
from agent.documents import extract


class DocumentModel:
    def ask(self,stage,data):
        assert stage=='document_project'
        source=data['sources'][0]['id']
        return {'title':'Conclusions actualisées — projet','act_type':'conclusions',
          'introduction':'Conclusions pour la société ALPHA, demanderesse, devant le tribunal judiciaire, RG à compléter. Projet fondé sur la dernière version identifiée.',
          'introduction_source_ids':[source],
          'sections':[{'heading':'Argument nouveau','body':'Exposé des faits et de la procédure. Discussion : argument à contrôler par l’avocat. Bordereau de pièces communiquées joint.',
                       'source_ids':[source]}],
          'requests':[{'text':'Par ces motifs, demande à contrôler.','source_ids':[source]}],
          'exhibits_referenced':[{'label':'Justificatif nouveau','source_ids':[source]}],
          'placeholders':['Numérotation finale des pièces'],
          'points_for_lawyer':['Valider le dispositif'],'source_ids':[source],
          'limits':['Aucune recherche juridique externe transmise.']}


class DocumentDAV:
    def __init__(self,raw):
        self.raw=raw;self.created=[];self.folders=[];self.present=set();self.changed=False
        self.source={'path':'/Dossiers/DEMO/Conclusions DEMO.docx','directory':False,
          'etag':'"v1"','modified':'Sat, 12 Sep 2026 09:00:00 GMT','size':len(raw)}

    def inventory(self,path):
        value=dict(self.source)
        if self.changed:value['etag']='"v2"'
        return [value]

    def download(self,item):return self.raw
    def ensure_folder(self,path,boundary):self.folders.append((path,boundary));return path
    def list_folder(self,path):return [{'path':x} for x in self.present]
    def put_file(self,path,data,content_type):self.created.append((path,data,content_type));return path


class DocumentProjects220Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['document_projects']={'enabled':True,'approval_minutes':60,
          'destination_subfolder':'20_Actes_et_conclusions/90_AxiorHub_Brouillons'}
        self.d=Desk(self.f.c)
        self.template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        self.dav=DocumentDAV(self.template);self.model=DocumentModel()

    def proposal(self):
        return prepare(self.d,{'document_type':'conclusions','matter':'DOS-001',
          'instruction':'Actualiser les conclusions avec les nouveaux arguments.'},
          dav=self.dav,model=self.model)

    def test_prepare_and_preview_do_not_write_nextcloud(self):
        result=self.proposal()
        self.assertEqual(self.dav.created,[]);self.assertEqual(self.dav.folders,[])
        self.assertEqual(result['source_file']['path'],self.dav.source['path'])
        self.assertEqual(len(result['future_files']),3)
        shown=preview(self.d,result['project_id'])
        self.assertNotIn('confirmation_code',shown)
        self.assertTrue(shown['safety']['new_files_only'])

    def test_confirmation_creates_three_new_files_and_never_overwrites(self):
        result=self.proposal()
        created=confirm_creation(self.d,{'project_id':result['project_id'],
          'confirmation_code':result['confirmation_code'],
          'source_path':result['source_file']['path'],
          'destination_folder':result['destination_folder']},dav=self.dav)
        self.assertEqual(len(created['created_files']),3)
        self.assertEqual(len(self.dav.created),3)
        self.assertTrue(self.dav.created[0][1].startswith(b'PK'))
        bcp_text=extract(self.dav.created[1][1],'Bordereau.docx',self.f.c['documents'])
        self.assertIn('Pièce n° 35',bcp_text)
        self.assertTrue(self.dav.created[-1][1].startswith(b'%PDF-1.4'))
        self.assertEqual(created['files_overwritten'],0)

    def test_changed_source_and_existing_target_fail_closed(self):
        result=self.proposal();self.dav.changed=True
        with self.assertRaisesRegex(Stop,'fichier_source_modifie'):
            confirm_creation(self.d,{'project_id':result['project_id'],
              'confirmation_code':result['confirmation_code'],
              'source_path':result['source_file']['path'],
              'destination_folder':result['destination_folder']},dav=self.dav)
        self.assertEqual(self.dav.created,[])

    def test_existing_target_is_never_overwritten(self):
        result=self.proposal();self.dav.present.add(result['future_files'][0])
        with self.assertRaisesRegex(Stop,'nom_fichier_deja_existant'):
            confirm_creation(self.d,{'project_id':result['project_id'],
              'confirmation_code':result['confirmation_code'],
              'source_path':result['source_file']['path'],
              'destination_folder':result['destination_folder']},dav=self.dav)
        self.assertEqual(self.dav.created,[])

    def test_latest_source_wins_even_when_it_is_pdf(self):
        items=[self.dav.source,{
          'path':'/Dossiers/DEMO/Conclusions DEMO plus recentes.pdf','directory':False,
          'etag':'"pdf2"','modified':'Sat, 12 Sep 2026 10:00:00 GMT','size':100}]
        selected,alternatives=_select_source(items,'conclusions')
        self.assertTrue(selected['path'].endswith('.pdf'))
        self.assertEqual(alternatives[0]['path'],self.dav.source['path'])

    def test_webdav_creation_uses_atomic_non_overwrite_precondition(self):
        calls=[]
        class HTTP:
            def request(self,*args,**kwargs):calls.append((args,kwargs));return b''
        dav=DAV.__new__(DAV);dav.cfg={'roots':['/Dossiers'],'max_generated_file_bytes':1000}
        dav.http=HTTP();dav.files='https://cloud.test/remote.php/dav/files/tech'
        dav.put_file('/Dossiers/DEMO/nouveau.docx',b'PK-data','application/test')
        self.assertEqual(calls[0][0][0],'PUT')
        self.assertEqual(calls[0][0][3]['If-None-Match'],'*')

    def test_api_and_openwebui_expose_exact_ten_tools(self):
        spec=openapi('https://cabinet.test')
        self.assertEqual(spec['info']['version'],'5.6.24')
        self.assertIn('/document-projects/{project_id}/confirm',spec['paths'])
        capabilities=dispatch(self.d,'/capabilities','GET')
        self.assertIn('create_new_nextcloud_document_files',capabilities['requires_confirmation'])
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        names=['preparer_un_projet_de_conclusions','preparer_un_projet_d_assignation',
          'preparer_un_projet_de_cgv','preparer_un_projet_de_contrat',
          'preparer_un_projet_de_charte_RGPD','preparer_un_projet_de_bcp',
          'preparer_un_projet_de_courrier','preparer_un_projet_de_document',
          'previsualiser_les_fichiers_du_projet','confirmer_la_creation_des_fichiers_nextcloud']
        self.assertTrue(all(('def '+name+'(') in tool for name in names))
        self.assertNotIn('def envoyer_',tool)


if __name__=='__main__':unittest.main()
