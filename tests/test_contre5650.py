"""Contre-tests de l'audit 5.6.50 (repris tels quels en 5.6.51). Données fictives, aucun service extérieur.
Depuis ce dossier : PYTHONPATH=SOURCE:SOURCE/tests python3 -m unittest contre_tests_5650 -v
"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import test_v5650 as t50
import test_v5643 as t43
from agent import membres5650 as mb, messagerie5650 as mx, juridique5649 as jr
from agent import correspondances5643 as archives
from agent import dossier5644
from agent.common import Stop
from agent.dav import LocalFolder
from agent.desk import Desk

OBSERVATIONS = {}


class WebRegressions(unittest.TestCase):
    def setUp(self):
        self.f=t50.ServerAccounts('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.admin=self.f.admin_cookie()
        self.lawyer=self.f.add(self.admin,'autre@example.test','avocat')
        mb.clear()
        self.addCleanup(mb.clear)

    def test_B01_dossier_reserve_original_inaccessible_par_lien(self):
        desk=Desk(self.f.f.c)
        self.addCleanup(desk.db.close)
        raw=t43._raw_message(subject='Courriel confidentiel fictif')
        box=t43._RawBox({'734':raw})
        prop=archives.prepare(desk,'cabinet',{'matter_id':'DOS-001','actions':['archive_email'],
            'source':{'folder':'INBOX','uid':'734','uidvalidity':box.validity}},box)
        result=archives.confirm(desk,'cabinet',{'proposal_id':prop['proposal_id'],'expected_version':1,'request_key':'audit5650-archive'})
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);(root/'Dossiers/DEMO').mkdir(parents=True)
        client=LocalFolder({'local_path':str(root),'roots':['/Dossiers']})
        archives.run_archive_email(desk,{'link_id':result['link_id']},box,client)
        mb.set_reserve(str(self.f.state),'DOS-001',['avocate@example.test'])
        self.assertEqual(self.f.request('/dossier',query='id=DOS-001',cookie=self.lawyer)['status'],'403 Forbidden')
        with patch('agent.correspondances5643._client',return_value=client):
            response=self.f.request('/api440/m5643/correspondances/original',query='lien='+result['link_id'],cookie=self.lawyer)
        OBSERVATIONS['B01'] = {'page_dossier':'403 Forbidden','telechargement_eml':response['status'],
            'contenu_fictif_recu':'Courriel confidentiel fictif' in response['body']}
        self.assertIn(response['status'],('403 Forbidden','404 Not Found'),
            'Un lien d’archive ne doit pas contourner le dossier réservé. Corps reçu : '+response['body'][:100])

    def test_B02_validation_retiree_bloque_ajout_information_validee(self):
        desk=Desk(self.f.f.c);self.addCleanup(desk.db.close)
        rights={**mb.ROLE_DEFAULTS['avocat'],'valider':False}
        response=self.f.api('m5650/droits',{'owner':'autre@example.test',**rights},self.admin)
        self.assertTrue(response['status'].startswith('200'))
        self.assertFalse(mb.effective_rights(str(self.f.state),'autre@example.test','avocat')['valider'])
        response=self.f.api('m5644/dossier/corriger',{'matter':'DOS-001','section':'jurisdiction','action':'add','value':'Juridiction fictive de test'},self.lawyer)
        items=dossier5644.apply(desk,'DOS-001','jurisdiction',[])
        OBSERVATIONS['B02'] = {'droit_valider':False,'reponse_correction':response['status'],
            'donnees_retournees':[{'value':i['value'],'validated':i['validated'],'status':i['status']} for i in items]}
        self.assertEqual(response['status'],'403 Forbidden','La correction devient une donnée validée malgré le droit retiré.')
        self.assertEqual(items,[],'Un refus ne doit pas laisser de correction enregistrée.')


class UnitRegressions(unittest.TestCase):
    def setUp(self):
        self.f=t50.Messaging('run');self.f.setUp();self.addCleanup(self.f.doCleanups)
        mb.clear();self.addCleanup(mb.clear)

    def test_B03_reference_meme_numero_autre_code_signalee(self):
        sources={'sources':[{'titre':'Code de procédure civile, article 700','reference':'DEMO-700','extrait':'Passage fictif limité à ce code.'}]}
        same=jr.unsourced_references('Selon l’article 700 du Code de procédure civile.',sources)
        unsupported=jr.unsourced_references('Selon l’article 700 du Code civil.',sources)
        OBSERVATIONS['B03'] = {'reference_presente_signalee':same,'reference_autre_code_signalee':unsupported}
        self.assertEqual(same,[])
        self.assertTrue(unsupported,'Même numéro dans un autre code ne constitue pas la source de cette référence.')

    def test_B04_polling_70_messages_ne_saute_pas_les_10_premiers(self):
        a,b='avocate@example.test','assistant@example.test'
        tid=mx.open_direct(self.f.d,a,b,self.f.members)
        initial=self.f.post(a,tid,'Premier message')['id']
        mx.messages(self.f.d,b,tid)
        sent=[self.f.post(a,tid,'Message fictif %d'%i)['id'] for i in range(70)]
        response=mx.messages(self.f.d,b,tid,after=initial)
        OBSERVATIONS['B04'] = {'nouveaux_messages':len(sent),'taille_lot':len(response),
            'premier_attendu':sent[0],'premier_recu':response[0]['id'],'dernier_recu':response[-1]['id'],
            'non_lus_apres_reception':mx.unread_total(self.f.d,b)}
        self.assertEqual(response[0]['id'],sent[0],'Le polling incrémental doit parcourir les nouveaux messages sans trou.')

    def test_B05_regles_acces_invalides_ne_rouvrent_pas_les_dossiers(self):
        state=self.f.d.c['state_dir']
        mb.set_reserve(state,'DOS-001',['avocate@example.test'])
        mb.set_rights(state,'cabinet','autre@example.test','avocat',{'envoyer':False})
        self.assertIn('DOS-001',mb.hidden_for(state,'autre@example.test','avocat'))
        (Path(state)/mb.FILE).write_text('{configuration interrompue',encoding='utf-8')
        try:
            hidden=mb.hidden_for(state,'autre@example.test','avocat')
            rights=mb.effective_rights(state,'autre@example.test','avocat')
        except Stop as exc:
            OBSERVATIONS['B05'] = {'controle_refuse':str(exc)}
            return  # Un refus explicite est aussi une correction acceptable.
        OBSERVATIONS['B05'] = {'controle_refuse':False,'dossiers_masques':sorted(hidden),
            'droit_envoyer_apres_corruption':rights['envoyer']}
        self.assertIn('DOS-001',hidden,'Une configuration illisible ne doit pas lever une restriction existante.')
        self.assertFalse(rights['envoyer'],'Une configuration illisible ne doit pas rétablir un droit retiré.')


if __name__=='__main__':unittest.main(verbosity=2)
