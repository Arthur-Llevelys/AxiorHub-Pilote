"""Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests."""
from datetime import date
from email.message import EmailMessage
from pathlib import Path
import json
from urllib.parse import urlencode
from uuid import uuid4
import unittest

from fake_imap440 import FakeIMAP
import test_v5648 as fixtures
from test_v5697_gestion import FauxAgenda
from agent import connexion5648, dossiers5697, membres5650, portfolio, procedure5697
from agent.common import private_json
from agent.mailbox import Mailbox


def affaire(i):
    return {'id': f'DOS-{i:04d}', 'client_name': f'Client fictif {i}', 'path': f'/Dossiers/{i}',
            'references': [f'DOS-{i:04d}'], 'aliases': [], 'correspondents': []}


class Parcours(unittest.TestCase):
    def setUp(self):
        self.b = fixtures.CabinetAccounts()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.addCleanup(membres5650.clear)
        self.addCleanup(self.b.desk.db.close)
        private_json(self.b.f.matter_file, [affaire(1), affaire(2)])
        self.mid = affaire(2)['id']
        membres5650.set_reserve(self.b.f.c['state_dir'], self.mid, ['titulaire@example.test'])
        self.cookie = self.b.first_login('avocate@example.test', self.b.add(self.b.admin_cookie()))

    def lire(self, path, **args):
        return self.b.call(path, auth=False, query=urlencode(args),
                           extra={'HTTP_COOKIE': f'{connexion5648.COOKIE}={self.cookie}'})

    def test_aucun_courriel_reserve_dans_quatre_routes(self):
        src = EmailMessage()
        src['From'], src['To'] = 'exp@example.test','cabinet@example.test'
        src['Subject'], src['Message-ID'] = 'OBJET-RESTREINT-TEST','<source-56100@example.test>'
        src.set_content('Message fictif réservé.')
        uid = FakeIMAP.add('INBOX', src.as_bytes())
        b = self.b
        box = Mailbox(b.f.c['mail'])
        try:
            m = box.fetch('INBOX', str(uid))
        finally:
            box.close()
        key = m.key(b.f.c['mail']['username']+'@'+b.f.c['mail']['host'])
        p = Path(b.f.c['state_dir'])/'reports'
        p.mkdir(exist_ok=True)
        private_json(p/(key+'.json'), {'subject':'OBJET-RESTREINT-TEST', 'matter':self.mid,
                     'source_uid':str(uid), 'source_mailbox':'INBOX', 'sender':'exp@example.test'})
        b.desk.db.execute('INSERT INTO manual_drafts VALUES (?,?,?)',
                          (key, json.dumps({'matter':'', 'result':{'body':'PROJET-RESTREINT-TEST','requires_decision':True}},
                                           separators=(',',':')), b.desk.now()))
        b.desk.db.commit()
        FakeIMAP.log.clear()
        for path, params, code in (('/courriels', {}, '200'), ('/mail', {'key':key}, '404'),
                                    ('/api440/m5625/boite/liste', {}, '200'),
                                    ('/api440/m5625/boite/courriel', {'uid':str(uid)}, '404'),
                                    ('/api440/m5625/boite/piece', {'uid':str(uid),'index':'0'}, '404')):
            with self.subTest(path=path):
                r = self.lire(path, **params)
                self.assertTrue(r['status'].startswith(code), r['status'])
                self.assertNotIn(b'OBJET-RESTREINT-TEST',r['body'])
                self.assertNotIn(b'PROJET-RESTREINT-TEST',r['body'])
        self.assertFalse(any(x[:2]==('UID','FETCH') and x[-1]=='(BODY.PEEK[])' for x in FakeIMAP.log))
        self.assertIn(b'PROJET-RESTREINT-TEST',b.call('/mail',query=urlencode({'key':key}))['body'])

    def test_get_dossiers_conserve_le_suivi_et_le_rattachement_reserves(self):
        db=self.b.desk.db
        portfolio.matter_status(self.b.desk, affaire(2))
        db.execute("UPDATE matter_portfolio SET manual_state='active',pinned=1 WHERE matter=?", (self.mid,))
        db.execute('INSERT INTO portfolio_mail_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('lien','x','<m@fictif.test>','','INBOX','1',self.mid,100,'certain','manuel','a@example.test',
                    '',self.b.desk.now(),'confirmed',self.b.desk.now()))
        db.commit()
        def instantane():
            return [list(map(tuple, db.execute('SELECT * FROM '+t+' WHERE matter=?',(self.mid,))))
                    for t in ('matter_portfolio','portfolio_mail_links')]
        avant = instantane()
        self.assertTrue(all(avant))
        for params in ({}, {'tri':'nom'}):
            self.assertTrue(self.lire('/dossiers',**params)['status'].startswith('200'))
            self.assertEqual(instantane(),avant)

    def test_statut_ne_contourne_pas_la_cloture(self):
        b = self.b
        a = ('cabinet','administrateur')
        d = dossiers5697.creer(b.desk,a,{'titre':'Dossier fictif','client':'Client fictif','nature':'contentieux'},uuid4().hex)
        mid=d['id']
        res=procedure5697.saisir_echeance(b.desk,a,mid,{'regle':'appel_jugement_contentieux','depart':date.today().isoformat(),
                'source':'Jugement fictif p. 1','depart_certain':True},FauxAgenda())
        self.assertEqual(res['status'],'confirmee')
        cmd={'cle':uuid4().hex,'version':d['version'],'statut':'archive'}
        for route, method, data in (('/modifier','POST',cmd),('','PATCH',{'cle':uuid4().hex,'version':d['version'],'champs':{'statut':'archive'}})):
            r=b.call('/api440/m57/dossiers/'+mid+route,method,data)
            self.assertTrue(r['status'].startswith('409'), r['body'])
            self.assertEqual(json.loads(r['body'])['error'],'utiliser_cloture')
        self.assertEqual(dossiers5697.lire(b.desk,a,mid)['statut'],'actif')
