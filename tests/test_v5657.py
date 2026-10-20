"""5.6.57 : parcours 1 à 3 et parcours A03, A04, A06, A10, contacts depuis la boîte. Données fictives (domaines example.test)."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import test_v220 as doc_fixture
import test_v5652 as base
import test_v5653 as t53
from agent import (crm5636 as crm, crm5657, etat5657, facturation5653 as fact, facturx5653 as fx, file5651, file5657, membres5650 as mb,
                   memoire5651, modeles5653 as models, parcours5657 as p57, reprise5657, web5657)
from agent.common import Stop, load_matters

ACTOR, MID = t53.ACTOR, t53.MID


# ------------------------------------------------------------------------------------------------ parcours 1
class Queue(base.Base):
    def test_decisions_et_projets_dans_la_file_unique(self):
        card = {'key': 'mission:abc', 'kind': 'mission', 'id': 'abc', 'state': 'decision', 'title': 'Préciser le dossier', 'matter': MID,
                'matter_label': 'DEMO', 'question': 'Plusieurs dossiers possibles', 'candidates': [], 'url': '/p/decision?type=mission&id=abc',
                'urgent': False, 'recommendation': '', 'sources': [], 'consequence': 'Rien ne démarre sans décision.'}
        review = [{'id': 'mail:1:7', 'type': 'Courriel', 'title': 'Réponse au client', 'matter': '', 'matter_label': '', 'when': '2026-10-17',
                   'dest': 'Brouillons (messagerie)'}]
        with patch('agent.aujourdhui5614.items', return_value=[card]), patch('agent.cockpit530.review_items', return_value=review):
            rows = file5651.items(self.desk, '/p')
            classes = {r['class'] for r in rows}
            self.assertIn('decision_agent', classes)
            self.assertIn('courriel', classes)   # 5.6.66 : un brouillon de courriel a sa rubrique (« Courriels à relire et envoyer »)
            dec = next(r for r in rows if r['class'] == 'decision_agent')
            self.assertEqual(dec['href'], '/p/aujourdhui#decision=mission%3Aabc')
            proj = next(r for r in rows if r['class'] == 'courriel')
            file5651.decide(self.desk, ACTOR, proj['key'], 'approuve', proj['revision'], prefix='/p')     # « Relu » : retiré partout
            self.assertFalse([r for r in file5651.items(self.desk, '/p') if r['class'] == 'courriel'])
            self.assertTrue([r for r in file5651.items(self.desk, '/p', MID) if r['class'] == 'decision_agent'])   # même objet dans le dossier

    def test_etats_reels_et_vue_d_ensemble(self):
        self.assertEqual(etat5657.business_state('prepare_draft', 'done', {'projet_prepare': True, 'depot_autorise': False})[0], 'question')
        self.assertEqual(etat5657.business_state('docrequest520', 'done', {'path': '/x.docx'})[0], 'projet_pret')
        self.assertEqual(etat5657.business_state('index', 'done', {})[0], 'termine')
        self.assertEqual(etat5657.business_state('index', 'pending')[1], 'En file')
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created) VALUES('index','{}','pending',?)", (old,))
        self.desk.db.commit()
        s = etat5657.summary(self.desk)
        self.assertTrue(any('attendent depuis' in p for p in s['problems']))
        self.assertIn('L’agent en un coup d’œil', etat5657.html(self.desk, '/p'))


# ------------------------------------------------------------------------------------------------ parcours 2
class Versions(unittest.TestCase):
    def setUp(self):
        self.f = doc_fixture.fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c['document_projects'] = {'enabled': True, 'approval_minutes': 60, 'destination_subfolder': '20_Actes_et_conclusions/90_AxiorHub_Brouillons'}
        from agent.desk import Desk
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        template = (Path(doc_fixture.__file__).parents[1] / 'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        self.dav = doc_fixture.DocumentDAV(template)

    def prepare(self):
        from agent.document_projects import prepare
        return prepare(self.d, {'document_type': 'conclusions', 'matter': 'DOS-001', 'instruction': 'Actualiser les conclusions avec les nouveaux arguments.'},
                       dav=self.dav, model=doc_fixture.DocumentModel())

    def test_sans_autorisation_code_de_confirmation(self):
        out = self.prepare()
        self.assertIn('confirmation_code', out)
        self.assertEqual(self.dav.created, [])

    def test_autorisation_cree_une_nouvelle_version_sans_ecraser(self):
        self.d.setting('documents5657:brouillons_auto', True)
        first = self.prepare()
        self.assertNotIn('confirmation_code', first)
        paths = [p for p, _, _ in self.dav.created]
        self.assertTrue(paths and all('/90_AxiorHub_Brouillons/' in p for p in paths))
        self.dav.present = set(paths)
        self.dav.created = []
        from agent.document_projects import prepare
        prepare(self.d, {'document_type': 'conclusions', 'matter': 'DOS-001', 'instruction': 'Actualiser les conclusions, seconde version.'},
                dav=self.dav, model=doc_fixture.DocumentModel())
        second = [p for p, _, _ in self.dav.created]
        self.assertTrue(second)
        self.assertTrue(all(p not in paths for p in second))                    # jamais d'écrasement
        self.assertTrue(any('_v2' in p for p in second))


class Routes(unittest.TestCase):
    def test_droits_et_roles(self):
        self.assertEqual(mb.category('POST', '/api440/m5657/documents/auto'), 'reglages')
        self.assertEqual(mb.category('POST', '/api440/m5657/pieces/deposer'), 'deposer')
        self.assertEqual(mb.category('POST', '/api440/m5657/diligence/confirmer'), 'facturation')
        self.assertTrue(web5657.allowed('assistant', 'pieces/preparer'))
        self.assertFalse(web5657.allowed('assistant', 'documents/auto'))
        self.assertFalse(web5657.allowed('lecteur', 'pieces/preparer'))


# ------------------------------------------------------------------------------------------------ parcours 3
class Invoices(base.Base):
    invoice = t53.Billing.invoice

    def setUp(self):
        base.Base.setUp(self)
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', t53.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, t53.CLIENT)

    def test_trois_presentations_et_sobre_par_defaut(self):
        factures = [t for t in models._catalog() if t['kind'] == 'facture']
        self.assertEqual(len(factures), 4)
        self.assertEqual(models.default_for(self.desk, 'facture')['id'], 'defaut:09-facture-sobre')
        ref = set(factures[0]['variables'])
        for t in factures:
            self.assertEqual(set(models.inspect_docx(Path(t['file']).read_bytes())) - set(models.LINE_VARS), ref - set(models.LINE_VARS), t['id'])

    def test_apercu_pdf_reel_ou_word(self):
        d = self.invoice()
        with patch.object(fx, 'office_pdf', return_value=b'%PDF-1.7 fictif'):
            out = fact.preview_file(self.desk, ACTOR, 'avocat', d['id'])
        self.assertEqual((out['mime'], out['data'][:4]), ('application/pdf', b'%PDF'))
        with patch.object(fx, 'office_pdf', side_effect=Stop('libreoffice_absent')):
            out = fact.preview_file(self.desk, ACTOR, 'avocat', d['id'])
        self.assertTrue(out['name'].endswith('.docx'))
        self.assertIn('LibreOffice absent', out['limite'])

    def test_diligences_proposees_puis_confirmees(self):
        from agent import facturation5655 as f55, metier500 as m5, time500
        m5.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO time500_proposals VALUES('prop5657',?,'2026-10-15',1,45,'1 × livrable préparé','a_valider',?,'','')",
                             (MID, m5.now()))
        self.desk.db.execute("INSERT INTO time500_items VALUES('ref5657','prop5657','livrable',45,'Projet de conclusions','2026-10-15',?)", (MID,))
        self.desk.db.commit()
        rows = p57.diligences(self.desk, MID)
        self.assertEqual(rows[0]['minutes'], 45)
        self.assertIn('Projet de conclusions', rows[0]['sources'])
        f55.note_seen(self.desk)
        self.assertTrue([x for x in f55.todo(self.desk) if x['type'] == 'diligences'])
        out = p57.post(self.desk, ACTOR, 'avocat', 'diligence/confirmer', {'proposition': 'prop5657', 'minutes': '30'})
        self.assertEqual(out['minutes'], 30)
        self.assertEqual(self.desk.db.execute('SELECT minutes FROM time500_entries').fetchone()[0], 30)
        self.assertFalse(p57.diligences(self.desk, MID))


# ------------------------------------------------------------------------------------------------ A04, A03, A06
class Preparations(base.Base):
    def event(self, title, days):
        start = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        from agent import workplan
        workplan.ensure_schema(self.desk)
        self.desk.db.execute("INSERT OR REPLACE INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             ('ev-' + title[:6], 'uid-' + title[:6], '', 'local:agenda', '', '', title, '', '', start, start, 1, MID, '[]', start))
        self.desk.db.commit()
        return 'ev-' + title[:6]

    def test_preparation_depuis_l_agenda_versionnee(self):
        memoire5651.save_decision(self.desk, MID, ACTOR, {'kind': 'argument_abandonne', 'texte': 'Ne pas invoquer la clause fictive ALPHA.'})
        eid = self.event('Audience de mise en état', 5)
        self.assertEqual(p57.upcoming_events(self.desk, MID)[0]['id'], eid)
        p = p57.create_preparation(self.desk, ACTOR, MID, eid)
        self.assertEqual((p['kind'], p['version']), ('audience', 1))
        self.assertTrue(p['snapshot']['a_ne_pas_reprendre'])
        self.assertEqual(p57.create_preparation(self.desk, ACTOR, MID, eid)['version'], 2)
        html = p57.matter_html(self.desk, '/p', MID)
        for text in ('Préparation d’audience ou de rendez-vous', 'Chronologie', 'Pièces clés', 'Points ouverts', 'Préparer une note de plaidoirie'):
            self.assertIn(text, html)
        with self.assertRaisesRegex(Stop, 'evenement_absent'):
            p57.create_preparation(self.desk, ACTOR, MID, 'inexistant')

    def test_demande_de_pieces_regroupee_puis_rapprochee(self):
        g = memoire5651.open_gap(self.desk, MID, 'Bail commercial signé', reason='Prouver la date d’effet', responsible='client')
        gid = g['id'] if isinstance(g, dict) else g
        r = p57.prepare_request(self.desk, ACTOR, MID, [gid], 'Quittances de loyer 2025')
        self.assertEqual(len(r['items']), 2)
        self.assertIn('Bail commercial signé', r['body'])
        self.assertEqual(p57.prepare_request(self.desk, ACTOR, MID, [gid], 'Quittances de loyer 2025')['id'], r['id'])   # même sélection : rejointe
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        self.desk.db.execute("INSERT INTO d51_documents(matter,path,name,updated) VALUES(?,?,?,?)", (MID, '/Dossiers/DEMO/Bail commercial signe.pdf',
                                                                                                    'Bail commercial signe.pdf', later))
        self.desk.db.commit()
        self.assertEqual(p57.match_received(self.desk, r['id']), 1)
        item = next(i for i in p57.request(self.desk, r['id'])['items'] if i['gap'])
        self.assertEqual(item['state'], 'recue')
        p57.set_item(self.desk, ACTOR, r['id'], item['item'], 'rapprochee')
        self.assertFalse([x for x in memoire5651.gaps(self.desk, MID) if x['id'] == gid])        # information fournie

    def test_depot_en_brouillon_jamais_au_cabinet(self):
        r = p57.prepare_request(self.desk, ACTOR, MID, [], 'Relevé bancaire')
        own = self.desk.c['mail']['from_address']
        self.desk.db.execute('UPDATE p57_requests SET recipient=? WHERE id=?', (own, r['id']))
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'reponse_a_soi_meme_refusee'):
            p57.deposit_request(self.desk, ACTOR, r['id'], box=types.SimpleNamespace(close=lambda: None))

    def test_point_client_propose_puis_refuse_non_repropose(self):
        from agent import facturation5655 as f55
        f55.note_seen(self.desk)
        r = p57.prepare_request(self.desk, ACTOR, MID, [], 'Attestation fictive')
        p57.set_item(self.desk, ACTOR, r['id'], p57.request(self.desk, r['id'])['items'][0]['item'], 'rapprochee')
        made = p57.propose_updates(self.desk, MID)
        self.assertEqual(len(made), 1)
        u = p57.update(self.desk, made[0])
        self.assertIn('Nous avons bien reçu : Attestation fictive.', u['body'])
        self.assertNotIn('Ce qui est attendu de vous :\n—', u['body'])
        self.assertTrue([x for x in file5651.items(self.desk, '/p') if x['object'] == 'suivi:' + made[0]])
        p57.decide_update(self.desk, ACTOR, made[0], 'refusee')
        self.assertEqual(p57.propose_updates(self.desk, MID), [])                 # refus : jamais reproposé
        p57.set_frequency(self.desk, MID, 'aucune')
        self.assertEqual(p57.propose_updates(self.desk, MID), [])


# ------------------------------------------------------------------------------------------------ contacts depuis la boîte
class FakeBox:
    def __init__(self, messages, validity='1'):
        self.messages, self.validity = messages, validity

    def select(self, folder):
        return self.validity

    def search(self, folder, *crit):
        if crit[0] == 'UID':
            low = int(crit[1].split(':')[0])
            return [str(u) for u in sorted(self.messages) if u >= low] or [str(max(self.messages))]
        return [str(u) for u in sorted(self.messages)]

    def fetch(self, folder, uid, headers_only=False):
        return types.SimpleNamespace(uid=uid, msg=self.messages[int(uid)], timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc))

    def close(self):
        pass


def msg(frm, to, **extra):
    m = EmailMessage()
    m['From'], m['To'] = frm, to
    for k, v in extra.items():
        m[k.replace('_', '-')] = v
    return m


class Contacts57(base.Base):
    def test_collecte_progressive_et_exclusions(self):
        own = self.desk.c['mail']['from_address']
        box = FakeBox({1: msg('Camille Exemple <camille@example.test>', own), 2: msg('no-reply@example.test', own),
                       3: msg('Lettre <info@example.test>', own, List_Id='<liste.example.test>'), 4: msg(own, 'Dominique <dominique@example.test>')}, '7')
        crm5657.save_settings(self.desk, ACTOR, {'actif': True, 'dossiers': 'INBOX', 'periode': '90'})
        out = crm5657.collect(self.desk, box)
        self.assertEqual((out['read'], out['created']), (4, 2))
        rows = {c['email']: c for c in crm.search(self.desk)}
        self.assertEqual(rows['camille@example.test']['category'], 'a_classer')
        self.assertEqual(rows['camille@example.test']['name'], 'Camille Exemple')
        self.assertNotIn(own, rows)
        self.assertNotIn('no-reply@example.test', rows)
        self.assertNotIn('info@example.test', rows)
        box.messages[5] = msg('Camille Exemple <camille@example.test>', own)
        again = crm5657.collect(self.desk, box)
        self.assertEqual((again['read'], again['created']), (1, 0))               # rien n'est relu ni dupliqué
        crm.save(self.desk, {'id': rows['camille@example.test']['id'], 'name': 'Camille Exemple', 'email': 'camille@example.test', 'category': 'client'})
        box.validity = '8'                                                          # UIDVALIDITY modifiée : dossier relu
        third = crm5657.collect(self.desk, box)
        self.assertEqual(third['created'], 0)
        self.assertEqual(crm.get(self.desk, rows['camille@example.test']['id'])['category'], 'client')   # correction de l'avocat conservée

    def test_desactivee_par_defaut(self):
        with self.assertRaisesRegex(Stop, 'collecte_contacts_desactivee'):
            crm5657.collect(self.desk, FakeBox({}))
        self.assertEqual(crm5657.schedule(self.desk), [])


# ------------------------------------------------------------------------------------------------ A10
class Resume(base.Base):
    def setUp(self):
        base.Base.setUp(self)
        mb.set_viewer(None)
        self.addCleanup(mb.set_viewer, None)

    def test_point_de_reprise_revérifie(self):
        reprise5657.note(self.desk, ACTOR, '/dossier', {'id': MID, 'vue': 'travaux'})
        reprise5657.note(self.desk, ACTOR, '/dossier', {'id': 'INCONNU'})
        rows = reprise5657.items(self.desk, ACTOR, '/p')
        self.assertEqual(len(rows), 1)
        self.assertIn('vue=travaux', rows[0]['url'])
        mb.set_viewer(frozenset({MID}))
        self.assertEqual(reprise5657.items(self.desk, ACTOR, '/p'), [])           # dossier devenu réservé : retiré
        mb.set_viewer(None)
        self.assertIn('Reprendre là où j’en étais', reprise5657.card_html(self.desk, ACTOR, '/p'))

    def test_recherche_facturation(self):
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', t53.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, t53.CLIENT)
        t53.Billing.invoice(self)
        rows = reprise5657.search_extra(self.desk, 'facture alpha', '/p')
        self.assertTrue(rows and rows[0]['type'] == 'Facturation')


if __name__ == '__main__':
    unittest.main()
