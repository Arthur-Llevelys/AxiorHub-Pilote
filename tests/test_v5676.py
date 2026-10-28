"""5.6.76 : suivi des opportunités et des prospects (CRM). Application réelle (WSGI) en version serveur ; valeurs fictives (example.test)."""
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_v5648 as t48
import test_v5675 as t75
from agent import crm5676 as op, file5651, fonctions5631, standalone_auth, shell501
from agent.desk import Desk
from agent.common import digest
from agent.state import State


class Base(t48.Base):
    def setUp(self):
        super().setUp()
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)

    def post(self, route, body, cookie):
        r = self.api('m5676/' + route, body, cookie)
        return r['status'], json.loads(r['body'])


class Pipeline(Base):
    def test_creation_relance_echue_et_etapes_avec_conflits(self):
        cookie = self.admin_cookie()
        status, out = self.post('creer', {'title': 'Litige commercial fournisseur', 'name': 'DURAND', 'first_name': 'Alice',
                                          'email': 'prospect@example.test', 'amount': '2 400', 'source': 'site'}, cookie)
        self.assertTrue(status.startswith('200'), out)
        oid = out['id']
        self.assertEqual(out['stage'], 'nouveau')
        self.assertEqual(out['amount_cents'], 240000)
        self.assertEqual(out['next_date'], date.today().isoformat())                 # toujours une prochaine action datée
        from agent import crm5636
        self.assertEqual(crm5636.get(self.d, out['contact'])['category'], 'prospect')
        items = [i for i in file5651.items(self.d, '/agent-courriel') if i['class'] == 'prospect']
        self.assertEqual(len(items), 1)                                               # relance échue dans « À décider »
        self.assertIn('DURAND', items[0]['title'])
        status, refused = self.post('etape', {'id': oid, 'stage': 'proposition'}, cookie)
        self.assertEqual(refused['error'], 'conflit_a_verifier_avant_cette_etape')   # pas de proposition sans recherche de conflits
        status, checked = self.post('conflits', {'id': oid, 'adverses': 'SOCIETE ZORVAL'}, cookie)
        self.assertTrue(status.startswith('200'), checked)
        self.assertEqual(checked['stage'], 'conflit')                                 # aucun rapprochement : étape franchie
        self.assertTrue(checked['conflict']['cleared'])
        status, moved = self.post('etape', {'id': oid, 'stage': 'proposition', 'proposal_kind': 'convention'}, cookie)
        self.assertEqual(moved['stage'], 'proposition')
        self.assertEqual(moved['proposal_sent'], date.today().isoformat())
        self.assertEqual(moved['next_date'], (date.today() + timedelta(days=7)).isoformat())   # relance à 7 jours
        status, lost = self.post('etape', {'id': oid, 'stage': 'perdu'}, cookie)
        self.assertEqual(lost['error'], 'motif_de_perte_requis')
        status, lost = self.post('etape', {'id': oid, 'stage': 'perdu', 'lost_reason': 'prix'}, cookie)
        self.assertEqual(lost['stage'], 'perdu')
        status, frozen = self.post('modifier', {'id': oid, 'title': 'Autre'}, cookie)
        self.assertEqual(frozen['error'], 'opportunite_close')
        k = op.indicators(self.d)
        self.assertEqual((k['perdues'], k['taux_transformation'], k['motifs_de_perte']), (1, 0, {op.LOST['prix']: 1}))

    def test_conflit_a_examiner_bloque_jusqu_a_la_decision(self):
        o = op.create(self.d, 'cabinet', {'title': 'Bail commercial', 'name': 'MARTIN'})
        fake = {'id': 'c1', 'status': 'a_examiner', 'decision': '', 'result': {'summary': 'Conflit potentiel signalé.'}}
        with patch('agent.conflicts500.check', return_value=fake), patch('agent.conflicts500.get', return_value=fake):
            out = op.check_conflicts(self.d, 'cabinet', o['id'])
            self.assertEqual(out['stage'], 'nouveau')
            self.assertIn('Examinez le rapport', out['message'])
            with self.assertRaises(op.Stop):
                op.move(self.d, 'cabinet', o['id'], 'proposition')
        accepted = dict(fake, decision='accepter')
        with patch('agent.conflicts500.get', return_value=accepted):
            self.assertEqual(op.move(self.d, 'cabinet', o['id'], 'proposition')['stage'], 'proposition')
        self.assertFalse(op._cleared(dict(fake, status='aucun_resultat', decision='decliner')))

    def test_gagne_ouvre_le_dossier_et_relie_le_client(self):
        o = op.create(self.d, 'cabinet', {'title': 'Conseil en droit des sociétés', 'name': 'BERNARD', 'email': 'bernard@example.test', 'amount': '1500'})
        with patch('agent.conflicts500.check', return_value={'id': 'c2', 'status': 'aucun_resultat', 'decision': '', 'result': {'summary': 'Aucun'}}), \
             patch('agent.conflicts500.get', return_value={'id': 'c2', 'status': 'aucun_resultat', 'decision': '', 'result': {'summary': 'Aucun'},
                                                           'decision_label': ''}):
            op.check_conflicts(self.d, 'cabinet', o['id'])
            with self.assertRaises(op.Stop) as ctx:
                op.win(self.d, 'assistant1', 'assistant', o['id'], {})
            self.assertEqual(str(ctx.exception), 'role_insuffisant')
            created = {'id': 'DOS-001', 'label': 'BERNARD - Conseil', 'path': '/Dossiers/DEMO', 'message': 'Dossier créé.'}
            with patch('agent.boite5625.create_matter', return_value=created) as cm:
                out = op.win(self.d, 'cabinet', 'administrateur', o['id'], {'parent': '/Dossiers'})
        args = cm.call_args[0][1]
        self.assertEqual((args['parent'], args['correspondent'], args['role']), ('/Dossiers', 'bernard@example.test', 'client'))
        self.assertEqual((out['stage'], out['matter']), ('gagne', 'DOS-001'))
        from agent import crm5636
        c = crm5636.get(self.d, o['contact'])
        self.assertEqual(c['category'], 'client')
        self.assertIn('DOS-001', [l['matter'] for l in c['links']])
        self.assertTrue(out['convention'].startswith('fin-'))                         # convention d'honoraires préparée en brouillon
        self.assertIn('Convention d’honoraires préparée en brouillon (1 500 € HT)', out['message'])
        from agent import facturation5653
        self.assertEqual(facturation5653.get(self.d, out['convention'])['state'], 'brouillon')
        with self.assertRaises(op.Stop):
            op.win(self.d, 'cabinet', 'administrateur', o['id'], {'parent': '/Dossiers'})   # déjà close
        k = op.indicators(self.d)
        self.assertEqual((k['gagnees'], k['taux_transformation']), (1, 100))

    def test_montants_et_emplacement_hors_racine(self):
        self.assertEqual(op.cents('1 500,50 €'), 150050)
        self.assertEqual(op.cents('1.500,50'), 150050)
        self.assertIsNone(op.cents(''))
        with self.assertRaises(op.Stop):
            op.cents('mille')
        o = op.create(self.d, 'cabinet', {'title': 'Divorce', 'name': 'YLDRAN'})
        with patch('agent.conflicts500.get', return_value={'id': 'c', 'status': 'aucun_resultat', 'decision': '', 'result': {}}):
            self.d.db.execute("UPDATE opp5676 SET conflict_check='c' WHERE id=?", (o['id'],))
            with self.assertRaises(op.Stop) as ctx:
                op.win(self.d, 'cabinet', 'administrateur', o['id'], {'parent': '/Autre/Ailleurs'})
        self.assertEqual(str(ctx.exception), 'emplacement_du_dossier_a_choisir')

    def test_opportunite_d_un_dossier_reserve_invisible(self):
        o = op.create(self.d, 'cabinet', {'title': 'Contrat', 'name': 'WOZNEL'})
        from agent import crm5636, membres5650
        crm5636.link(self.d, o['contact'], 'DOS-001', 'client')
        membres5650.set_viewer(frozenset({'DOS-001'}))
        try:
            self.assertEqual(op.listing(self.d), [])
            with self.assertRaises(op.Stop):
                op.get(self.d, o['id'])
        finally:
            membres5650.clear()
        self.assertEqual(len(op.listing(self.d)), 1)


class Detection(Base):
    def _mail(self, sender, subject):
        key = digest(sender + subject)
        account = digest(self.f.c['mail']['username'] + '@' + self.f.c['mail']['host'])
        reports = Path(self.f.c['state_dir']) / 'reports'
        reports.mkdir(exist_ok=True)
        (reports / (key + '.json')).write_text(json.dumps({'key': key, 'sender': sender, 'subject': subject, 'account_key': account,
                                                           'source_uid': '1', 'source_mailbox': 'INBOX', 'triage': {'exclude_category': 'none'}}),
                                               encoding='utf-8')
        State(self.f.c['state_dir']).set(key, '<m@example.test>', 't', 'review', 'expediteur_inconnu_sans_dossier')
        return key

    def test_detection_proposee_puis_acceptee(self):
        k1 = self._mail('nouveau@example.test', 'Demande de rendez-vous pour un licenciement')
        k2 = self._mail('pub@example.test', 'Offre spéciale logiciel')
        verdicts = {k1: {'nouvelle_demande': True, 'nom': 'KERVILAC', 'prenom': 'Paul', 'societe': '', 'objet': 'Contestation d’un licenciement',
                         'domaine': 'droit du travail', 'raison': 'Demande de rendez-vous pour être défendu.'},
                    k2: {'nouvelle_demande': False, 'nom': '', 'prenom': '', 'societe': '', 'objet': '', 'domaine': '', 'raison': 'Publicité.'}}
        current = {}

        def fetch(desk, key, box, full=True):
            current['key'] = key
            return {}, SimpleNamespace(text='Bonjour, je souhaite un rendez-vous.')
        model = SimpleNamespace(complete=lambda messages, **kw: json.dumps(verdicts[current['key']]))
        with patch('agent.intelligence.fetch_source', fetch):
            out = op.detect(self.d, model=model, box=SimpleNamespace(close=lambda: None))
            self.assertEqual((out['lus'], out['proposes']), (2, 1))
            self.assertEqual(op.detect(self.d, model=model, box=SimpleNamespace(close=lambda: None))['lus'], 0)   # jamais relu deux fois
        items = [i for i in file5651.items(self.d, '/agent-courriel') if i['class'] == 'prospect']
        self.assertEqual(len(items), 1)
        self.assertIn('KERVILAC', items[0]['title'])
        self.assertEqual(items[0]['href'], '/agent-courriel/mail?key=' + k1)
        pid = items[0]['object'].split(':', 1)[1]
        cookie = self.admin_cookie()
        status, accepted = self.post('proposition/accepter', {'id': pid}, cookie)
        self.assertTrue(status.startswith('200'), accepted)
        o = op.get(self.d, accepted['id'])
        self.assertEqual((o['origin'], o['source'], o['contact_email'], o['mail_key']), ('detection', 'courriel', 'nouveau@example.test', k1))
        status, again = self.post('proposition/accepter', {'id': pid}, cookie)
        self.assertEqual(again['error'], 'proposition_absente_ou_traitee')
        self.assertEqual(op.detection_candidates(self.d), [])

    def test_detection_desactivable_et_modele_local_seulement(self):
        self._mail('autre@example.test', 'Question')
        cookie = self.admin_cookie()
        status, out = self.post('detection', {'actif': '0'}, cookie)
        self.assertTrue(status.startswith('200'), out)
        self.assertTrue(op.detect(self.d, model=SimpleNamespace())['desactive'])
        self.assertIsNone(op.schedule(self.d, 10 ** 10))
        self.d.setting(op.SETTING, True)
        with patch('agent.model.routed_config', return_value={'provider_type': 'openrouter'}):
            with self.assertRaises(op.Stop) as ctx:
                op._local_model(self.d)
        self.assertEqual(str(ctx.exception), 'detection_reservee_au_modele_local')


class Pages(Base):
    def test_vue_opportunites_et_fiche(self):
        o = op.create(self.d, 'cabinet', {'title': 'Recouvrement de factures', 'name': 'XARVOL', 'amount': '900'})
        cookie = self.admin_cookie()
        page = self.request('/contacts-crm', query='vue=opportunites', cookie=cookie)
        self.assertEqual(page['status'], '200 OK', page['body'][:300])
        for text in ('Opportunités', 'Nouveau contact', 'Proposition envoyée', 'Recouvrement de factures', 'taux de transformation', 'Créer l’opportunité'):
            self.assertIn(text, page['body'])
        detail = self.request('/contacts-crm', query='vue=opportunites&id=' + o['id'], cookie=cookie)
        for text in ('Lancer la recherche de conflits', 'Gagné : ouvrir le dossier', 'Classer comme perdue', 'obligatoire avant la proposition'):
            self.assertIn(text, detail['body'])
        contacts = self.request('/contacts-crm', cookie=cookie)
        self.assertIn('vue=opportunites', contacts['body'])                          # onglet depuis le répertoire

    def test_cablage(self):
        aliases = next(f[3] for f in fonctions5631.FUNCTIONS if f[0] == 'contacts_crm')
        self.assertIn('Opportunités', aliases)
        self.assertIn('Prospects', aliases)
        self.assertIn('prospect', file5651.FILTERS['decider'])
        self.assertIn('v5676.css', shell501.BUNDLE)
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5676/creer'))
        from agent.desk import JOBS
        self.assertIn('opportunites5676', JOBS)


class Claude(t75.Base):
    def test_outils_du_connecteur(self):
        self.enable()
        d = Desk(self.f.c)
        try:
            op.create(d, 'cabinet', {'title': 'Cession de fonds de commerce', 'name': 'ORLANDEC', 'amount': '3000'})
        finally:
            d.db.close()
        _c, tok = self.tokens()
        access = tok['access_token']
        names = {t['name'] for t in json.loads(self.rpc(access, 'tools/list')['body'])['result']['tools']}
        self.assertTrue({'opportunites', 'proposer_opportunite', 'proposer_relance'} <= names)
        _r, text = self.tool(access, 'opportunites')
        data = json.loads(text)
        self.assertNotIn('ORLANDEC', text)                                            # pseudonymisé comme le reste
        oid = data['opportunites'][0]['opportunite']
        self.assertEqual(data['indicateurs']['en_cours'], 1)
        res, out = self.tool(access, 'proposer_relance', {'opportunite': oid, 'action': 'Appeler le prospect', 'date': '2026-11-05'})
        self.assertFalse(res['isError'], out)
        res, out = self.tool(access, 'proposer_opportunite', {'nom': 'ZEPHRAN', 'objet': 'Rupture conventionnelle', 'honoraires_estimes': '1200'})
        self.assertFalse(res['isError'], out)
        d = Desk(self.f.c)
        try:
            rows = d.db.execute("SELECT kind,origin,state FROM opp5676_propositions ORDER BY created").fetchall()
            self.assertEqual(sorted((r['kind'], r['origin'], r['state']) for r in rows),
                             [('opportunite', 'claude', 'proposee'), ('relance', 'claude', 'proposee')])
            self.assertEqual(len(op.listing(d)), 1)                                   # rien n'est créé sans l'avocat
        finally:
            d.db.close()


if __name__ == '__main__':
    unittest.main()
