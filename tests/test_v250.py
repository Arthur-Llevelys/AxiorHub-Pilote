import hashlib
from io import BytesIO
import json
from pathlib import Path
import unittest
import zipfile

import test_agent as fixtures

from agent.api import dispatch, openapi
from agent.common import Stop
from agent.dav import DAV
from agent.desk import Desk
from agent.document_projects import _docx
from agent.documents import extract
from agent.index import DocumentIndex
from agent.hearing import (compare_devices, confirm_hearing, identify_party_writings,
    prepare_hearing)
from agent.word_legal import (confirm_word_project, inspect_docx, prepare_word_project,
    render_docx)
import upgrade


class CabinetDAV:
    def __init__(self, ours, opponent):
        self.raw={
          '/Dossiers/DEMO/Nos conclusions exemple 2026-09-12.docx':ours,
          '/Dossiers/DEMO/Conclusions adverses 2026-09-11.docx':opponent,
        }
        self.items=[
          {'path':path,'directory':False,'etag':'"'+str(i)+'"',
           'modified':'Sat, 12 Sep 2026 08:0'+str(i)+':00 GMT','size':len(raw)}
          for i,(path,raw) in enumerate(self.raw.items())
        ]
        self.created=[];self.present={};self.folders=[]

    def inventory(self,path):return [dict(x) for x in self.items]
    def download(self,item):
        return self.raw[item['path']] if item['path'] in self.raw else self.present[item['path']]
    def ensure_folder(self,path,boundary):self.folders.append((path,boundary));return path
    def list_folder(self,path):
        return [{'path':p,'directory':False} for p in self.present if p.startswith(path.rstrip('/')+'/')]
    def put_file(self,path,data,content_type):
        if path in self.present:raise Stop('condition_prealable_echouee')
        self.present[path]=data;self.created.append((path,data,content_type));return path


class HearingModel:
    def ask(self,stage,data):
        if stage=='chat':
            return {'answer':'Examen de la section : prétentions et dispositif à vérifier dans les sources.',
              'source_ids':[item['id'] for item in data['sources']], 'limits':[],
              'proposed_actions':[]}
        assert stage=='hearing_preparation'
        ours=data['sources'][0]['id'];opponent=data['sources'][1]['id']
        def item(seconds,label):
            return {'sequence':1,'duration_seconds':seconds,'heading':label,
              'message':'Présenter le point avec prudence.','source_ids':[ours],'cautions':[]}
        return {'title':'Audience DEMO — projet','procedural_context':'Audience à préparer.',
          'procedural_source_ids':[ours,opponent],
          'argument_matrix':[{'issue':'Résolution','our_position':'Elle est demandée.',
            'opponent_position':'Elle est contestée.','proposed_response':'Revenir aux pièces.',
            'strengths':['Écritures identifiées'],'weaknesses':[],
            'our_source_ids':[ours],'opponent_source_ids':[opponent],
            'authority_source_ids':[],'missing_evidence':[],'points_for_lawyer':[]}],
          'oral_plan_5':[item(300,'Essentiel')],'oral_plan_10':[item(600,'Développé')],
          'oral_plan_20':[item(1200,'Complet')],
          'likely_questions':[{'question':'Quelle pièce principale ?',
            'proposed_answer':'La pièce doit être confirmée.','source_ids':[ours],
            'answer_limits':['Réponse à contrôler']}],
          'exhibits_to_take':[{'label':'Nos conclusions','reason_to_take':'Dernières écritures',
            'source_ids':[ours]}],
          'hearing_checklist':['Vérifier la juridiction et l’heure'],
          'device_changes_to_address':['Demandes adverses à contrôler'],
          'points_for_lawyer':['Valider la stratégie orale'],'source_ids':[ours,opponent],
          'limits':['Projet interne uniquement.']}


class WordModel:
    def ask(self,stage,data):
        assert stage=='word_revision_plan'
        sid=data['sources'][0]['id']
        return {'title':'Révision contrôlée',
          'edits':[{'operation':'replace','anchor_text':'Date des conclusions : 20/01/2026',
              'text':'Date des conclusions : 13/09/2026','paragraph_id':'date_actualisee',
              'style_id':'','source_ids':[sid],'reason':'Actualisation demandée.'},
            {'operation':'insert_after','anchor_text':'CONCLUSIONS EN REPONSE N°2',
              'text':'Argument nouveau. Voir [[REF:date_actualisee|la date actualisée]].',
              'paragraph_id':'argument_nouveau','style_id':'','source_ids':[sid],
              'reason':'Ajout sourcé.'}],
          'exhibits':[{'label':'Document source contrôlé','source_ids':[sid]}],
          'source_ids':[sid],'limits':['Le fond reste à valider.']}


class Version250Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['hearing']={'enabled':True,'approval_minutes':60,
          'destination_subfolder':'60_Audiences/90_AxiorHub_Brouillons'}
        self.f.c['word_legal']={'enabled':True,'approval_minutes':60,
          'destination_subfolder':'20_Actes_et_conclusions/90_AxiorHub_Brouillons'}
        self.d=Desk(self.f.c)
        template=(Path(__file__).parents[1]/'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        ours=_docx(template,[('PAR CES MOTIFS',True),('Prononcer la résolution du contrat.',False)])
        opponent=_docx(template,[('PAR CES MOTIFS',True),('Débouter le demandeur de ses demandes.',False)])
        self.dav=CabinetDAV(ours,opponent);self.ours=ours

    def test_party_writings_and_devices_are_distinct_and_hashed(self):
        result=identify_party_writings(self.d,{'matter':'DOS-001'},dav=self.dav)
        self.assertEqual(result['certainty'],'certain')
        self.assertNotEqual(result['our_latest']['sha256'],result['opponent_latest']['sha256'])
        comparison=compare_devices(self.d,{'matter':'DOS-001'},dav=self.dav)
        self.assertTrue(comparison['our_device']['found'])
        self.assertIn('Prononcer la résolution',comparison['our_device']['text'])
        self.assertIn('Débouter le demandeur',comparison['opponent_device']['text'])

    def test_ambiguous_party_selection_fails_closed(self):
        duplicate=dict(self.dav.items[0]);duplicate['path']='/Dossiers/DEMO/Nos conclusions exemple 2026-09-12 bis.docx'
        self.dav.raw[duplicate['path']]=self.ours;self.dav.items.append(duplicate)
        with self.assertRaisesRegex(Stop,'plusieurs_dernieres_conclusions_partie'):
            identify_party_writings(self.d,{'matter':'DOS-001'},dav=self.dav)

    def test_hearing_preview_is_read_only_then_creates_five_new_files(self):
        preview=prepare_hearing(self.d,{'matter':'DOS-001','instruction':'Préparer l’audience.'},
          dav=self.dav,model=HearingModel())
        self.assertEqual(self.dav.created,[]);self.assertEqual(preview['status'],'pending')
        created=confirm_hearing(self.d,{'project_id':preview['hearing_project_id'],
          'confirmation_code':preview['confirmation_code'],
          'our_source_path':preview['writings']['ours']['path'],
          'opponent_source_path':preview['writings']['opponent']['path'],
          'destination_folder':preview['destination_folder']},dav=self.dav)
        self.assertEqual(len(created['created_files']),5)
        self.assertEqual(created['files_overwritten'],0)
        self.assertTrue(all(x[0] not in self.dav.raw for x in self.dav.created))

    def test_hearing_existing_target_is_never_overwritten(self):
        preview=prepare_hearing(self.d,{'matter':'DOS-001','instruction':'Préparer l’audience.'},
          dav=self.dav,model=HearingModel())
        self.dav.present[preview['future_files'][0]]=b'existing'
        with self.assertRaisesRegex(Stop,'nom_fichier_deja_existant'):
            confirm_hearing(self.d,{'project_id':preview['hearing_project_id'],
              'confirmation_code':preview['confirmation_code'],
              'our_source_path':preview['writings']['ours']['path'],
              'opponent_source_path':preview['writings']['opponent']['path'],
              'destination_folder':preview['destination_folder']},dav=self.dav)
        self.assertEqual(self.dav.created,[])

    def test_hearing_creation_resumes_after_lost_acknowledgement(self):
        preview=prepare_hearing(self.d,{'matter':'DOS-001','instruction':'Préparer l’audience.'},
          dav=self.dav,model=HearingModel())
        original=self.dav.put_file;failed=[False]
        def uncertain(path,data,content_type):
            original(path,data,content_type)
            if not failed[0]:failed[0]=True;raise Stop('reponse_webdav_incertaine')
            return path
        self.dav.put_file=uncertain
        args={'project_id':preview['hearing_project_id'],'confirmation_code':preview['confirmation_code'],
          'our_source_path':preview['writings']['ours']['path'],
          'opponent_source_path':preview['writings']['opponent']['path'],
          'destination_folder':preview['destination_folder']}
        first=confirm_hearing(self.d,args,dav=self.dav);self.assertEqual(len(first['created_files']),0)
        self.dav.put_file=original
        resumed=confirm_hearing(self.d,args,dav=self.dav)
        self.assertEqual(len(resumed['created_files']),5)
        self.assertEqual(len({x[0] for x in self.dav.created}),5)

    def test_word_clean_and_compared_preserve_structure_and_track_changes(self):
        plan=WordModel().ask('word_revision_plan',{'sources':[{'id':'source'}]})
        clean=render_docx(self.ours,plan,False);compared=render_docx(self.ours,plan,True)
        self.assertIn('Date des conclusions : 13/09/2026',extract(clean,'propre.docx',self.f.c['documents']))
        self.assertEqual(inspect_docx(clean)['has_headers'],inspect_docx(self.ours)['has_headers'])
        with zipfile.ZipFile(BytesIO(clean)) as z:clean_xml=z.read('word/document.xml')
        with zipfile.ZipFile(BytesIO(compared)) as z:
            compared_xml=z.read('word/document.xml');settings=z.read('word/settings.xml')
        self.assertNotIn(b'<w:ins',clean_xml);self.assertNotIn(b'<w:del',clean_xml)
        self.assertIn(b'<w:ins',compared_xml);self.assertIn(b'<w:del',compared_xml)
        self.assertIn(b'trackRevisions',settings);self.assertIn(b'w:hyperlink',compared_xml)

    def test_word_preview_then_four_files_and_no_source_overwrite(self):
        preview=prepare_word_project(self.d,{'matter':'DOS-001','instruction':'Actualiser.',
          'source_path':self.dav.items[0]['path']},dav=self.dav,model=WordModel())
        self.assertEqual(self.dav.created,[])
        created=confirm_word_project(self.d,{'project_id':preview['word_project_id'],
          'confirmation_code':preview['confirmation_code'],
          'source_path':preview['source_file']['path'],
          'destination_folder':preview['destination_folder']},dav=self.dav)
        self.assertEqual(len(created['created_files']),4)
        self.assertTrue(created['source_unchanged']);self.assertEqual(created['files_overwritten'],0)
        self.assertEqual(hashlib.sha256(self.dav.raw[self.dav.items[0]['path']]).hexdigest(),
          preview['source_file']['sha256'])

    def test_word_existing_target_is_never_overwritten(self):
        preview=prepare_word_project(self.d,{'matter':'DOS-001','instruction':'Actualiser.',
          'source_path':self.dav.items[0]['path']},dav=self.dav,model=WordModel())
        self.dav.present[preview['future_files'][0]]=b'existing'
        with self.assertRaisesRegex(Stop,'nom_fichier_deja_existant'):
            confirm_word_project(self.d,{'project_id':preview['word_project_id'],
              'confirmation_code':preview['confirmation_code'],
              'source_path':preview['source_file']['path'],
              'destination_folder':preview['destination_folder']},dav=self.dav)
        self.assertEqual(self.dav.created,[])

    def test_api_openwebui_and_migration_expose_v250(self):
        spec=openapi('https://cabinet.test');self.assertEqual(spec['info']['version'],'5.6.4')
        for path in ('/hearing/writings','/hearing/devices/compare',
          '/hearing-projects/{project_id}/confirm','/word-projects/{project_id}/confirm'):
            self.assertIn(path,spec['paths'])
        caps=dispatch(self.d,'/capabilities','GET');self.assertEqual(caps['version'],'5.6.4')
        self.assertIn('compare_party_devices',caps['can'])
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        for name in ('identifier_les_dernieres_conclusions_des_parties',
          'preparer_une_audience','confirmer_la_creation_du_dossier_de_plaidoirie',
          'preparer_une_revision_word','confirmer_la_creation_des_versions_word'):
            self.assertIn('def '+name+'(',tool)
        self.assertTrue(upgrade.migrate_hearing_word(Path(self.f.c['state_dir']))['registries_created'])
        tables={x[0] for x in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('hearing_projects_v250',tables);self.assertIn('word_projects_v250',tables)

    def test_large_dav_inventory_is_deterministically_paginated(self):
        dav=DAV.__new__(DAV);dav.cfg={'roots':['/Dossiers'],'max_depth':8,
          'inventory_page_files':2,'max_inventory_directories':100}
        tree={
          '/Dossiers/DEMO':[{'path':'/Dossiers/DEMO/B','directory':True},
            {'path':'/Dossiers/DEMO/a.txt','directory':False,'etag':'a','modified':'','size':1}],
          '/Dossiers/DEMO/B':[{'path':'/Dossiers/DEMO/B/d.txt','directory':False,'etag':'d','modified':'','size':1},
            {'path':'/Dossiers/DEMO/B/c.txt','directory':False,'etag':'c','modified':'','size':1}],
        }
        dav.list_folder=lambda path:list(reversed(tree[path]))
        first,cursor,complete=dav.inventory_page('/Dossiers/DEMO',0,2)
        second,next_cursor,finished=dav.inventory_page('/Dossiers/DEMO',cursor,2)
        self.assertEqual([x['path'] for x in first],
          ['/Dossiers/DEMO/a.txt','/Dossiers/DEMO/B/c.txt'])
        self.assertFalse(complete);self.assertEqual(cursor,2)
        self.assertEqual([x['path'] for x in second],['/Dossiers/DEMO/B/d.txt'])
        self.assertTrue(finished);self.assertIsNone(next_cursor)

    def test_partial_index_never_deletes_unvisited_documents(self):
        index=DocumentIndex(self.f.c['state_dir'])
        matter={'id':'DOS-001','path':'/Dossiers/DEMO'}
        index.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',
          ('DOS-001','/Dossiers/DEMO/late.txt','old','','ancien',''))
        index.db.execute('INSERT INTO search VALUES (?,?,?)',
          ('DOS-001','/Dossiers/DEMO/late.txt','ancien'));index.db.commit()
        class Paged:
            def __init__(self):self.round=0
            def inventory_page(self,path,cursor,limit):
                self.round+=1
                if cursor==0:return ([{'path':'/Dossiers/DEMO/first.txt','etag':'1',
                  'modified':'','size':5,'directory':False}],1,False)
                return ([{'path':'/Dossiers/DEMO/late.txt','etag':'2',
                  'modified':'','size':4,'directory':False}],None,True)
            def download(self,item):return b'nouveau'
        dav=Paged();index.sync_page(dav,matter,self.f.c['documents'],1)
        self.assertTrue(index.db.execute('SELECT 1 FROM docs WHERE matter=? AND path=?',
          ('DOS-001','/Dossiers/DEMO/late.txt')).fetchone())
        index.sync_page(dav,matter,self.f.c['documents'],1)
        self.assertTrue(index.last_page_state['complete'])
        paths={x[0] for x in index.db.execute('SELECT path FROM docs WHERE matter=?',('DOS-001',))}
        self.assertEqual(paths,{'/Dossiers/DEMO/first.txt','/Dossiers/DEMO/late.txt'})


if __name__=='__main__':unittest.main()
