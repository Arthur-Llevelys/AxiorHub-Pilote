"""Coverage and browser behavior for the 5.0.0 document workflow."""
from unittest.mock import patch
import unittest

from agent.common import Stop,load_matters
from agent.desk import Desk
from agent.improvements36 import upload_attachment,attachment_parts
from agent.long_context363 import analyze_attachment,analyze_writing
from agent.preferences363 import save_matter_profile,save_profile,save_model
from agent.web import App
import test_agent as fixtures
import test_desk


class SectionModel:
    cfg={'model':'test-local'}
    def __init__(self):self.calls=[]
    def ask(self,stage,payload):
        assert stage=='chat'
        self.calls.append([item['id'] for item in payload['sources']])
        return {'answer':'Source vérifiée pour la question : '+payload['question_avocat'][:90],
            'source_ids':[item['id'] for item in payload['sources']],
            'limits':[],'proposed_actions':[]}


class Version363(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c)

    def test_long_upload_is_complete_scoped_and_resume_uses_cache(self):
        original=('FAIT-2026-01 : Une date et un argument.\n'*3500)+'FIN-TRACE'
        uploaded=upload_attachment(self.d,original.encode(),'conclusions.txt',matter='DOS-001')
        ident=uploaded['attachment_id']
        source,parts,meta=attachment_parts(self.d,ident,'DOS-001')
        self.assertEqual(''.join(x['excerpt'] for x in parts),original)
        self.assertEqual(meta['extracted_chars'],len(original))
        self.assertGreater(len(parts),6)
        model=SectionModel()
        found,cover=analyze_attachment(self.d,ident,'Quelles dates ?', 'DOS-001','',model,60000)
        self.assertEqual(cover['parts_analyzed'],len(parts))
        self.assertEqual(found[0]['char_start'],0)
        self.assertEqual(found[-1]['char_end'],len(original))
        calls=len(model.calls)
        again,_=analyze_attachment(self.d,ident,'Quelles dates ?', 'DOS-001','',model,60000)
        self.assertEqual(found,again)
        self.assertLess(len(model.calls)-calls,len(parts))
        with self.assertRaisesRegex(Stop,'piece_assistant_autre_contexte'):
            analyze_attachment(self.d,ident,'Quelles dates ?', '', '',model,60000)

    def test_long_hearing_writing_is_covered_and_resumable(self):
        text='CONCLUSION : demande de réparation.\n'*900
        model=SectionModel()
        rows,cover=analyze_writing(text,'our-writing-abc','/Dossiers/DEMO/Nos conclusions.pdf',
                                   'Préparer la plaidoirie',model,60000,self.d)
        self.assertGreater(cover['parts_analyzed'],6)
        self.assertEqual(rows[0]['char_start'],0)
        self.assertEqual(rows[-1]['char_end'],len(text))
        before=len(model.calls)
        analyze_writing(text,'our-writing-abc','/Dossiers/DEMO/Nos conclusions.pdf',
                        'Préparer la plaidoirie',model,60000,self.d)
        self.assertLess(len(model.calls)-before,cover['parts_analyzed'])

    def test_matter_correction_keeps_path_and_cabinet_guidance_persists(self):
        old=load_matters(self.d.c)[0]
        save_matter_profile(self.d,old['id'],'Nom exact de l’affaire','MARTIN,SOCIETE-B')
        new=load_matters(self.d.c)[0]
        self.assertEqual(new['path'],old['path'])
        self.assertEqual(new['client_name'],'Nom exact de l’affaire')
        save_profile(self.d,'Cabinet test','Français clair','Vérifier les dates')
        self.assertEqual(self.d.settings('cabinet:profile')['guidance'],'Vérifier les dates')
        with patch('agent.preferences363.Model') as model:
            save_model(self.d,'qwen-local:27b','complex')
            model.assert_called_once()
        self.assertEqual(Desk(self.f.c).c['model_routing']['complex_model'],'qwen-local:27b')


class Browser363(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_mail_frame_and_hearing_fields_are_reachable(self):
        mail=self.request('/')
        self.assertIn('https://ai.example.com/mail',mail['body'])
        self.assertIn('ws-mail-fold',mail['body'])
        self.assertIn('https://ai.example.com',mail['headers']['Content-Security-Policy'])
        hearing=self.request('/audiences-word',query='matter=DOS-001')['body']
        self.assertIn('data-hearing-upload="ours"',hearing)
        self.assertIn('data-hearing-upload="opponent"',hearing)
        self.assertIn('app520.css',hearing)
        matter=self.request('/matter',query='id=DOS-001')['body']
        self.assertIn('Corriger le nom et les références du dossier',matter)
        profile=self.request('/parametres',query='tab=cabinet')['body']
        self.assertIn('save_cabinet_profile',profile)


if __name__=='__main__':unittest.main()
