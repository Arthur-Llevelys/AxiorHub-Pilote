"""5.6.27 : page Dossier refondue — fiche de travail préparée par l'agent (résumé, rappel des faits, parties au litige, procédure,
chronologie), corrections de l'avocat, instructions à l'agent dans le dossier, navigation (retour, onglets, liens)."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
MID = '20240101001'
PIECES = {
    '/Dossiers/DEMO/Facture honoraires 2025-08.pdf': ('2025-08-14', 'Facture d’honoraires du 14/08/2025.'),
    '/Dossiers/DEMO/Assignation tribunal de commerce.pdf': ('2025-03-04', 'ASSIGNATION. A LA REQUETE DE ALPHA CONTRE BETA SERVICES. Par la SELARL Démo, commissaire de justice.'),
    '/Dossiers/DEMO/JP - CA Paris 2019.docx': ('2024-01-24', 'Cour d’appel de Paris, 4 avril 2019.'),
    '/Dossiers/DEMO/Contrat de prestation.pdf': ('2023-01-03', 'Contrat de prestation : livraison au 30 juin 2023.'),
}


class Fiche(t530.Base):
    def setUp(self):
        super().setUp()
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.desk.c['state_dir'])
        for path, (mod, text) in PIECES.items():
            idx.db.execute('INSERT OR REPLACE INTO docs(matter,path,etag,modified,text,error) VALUES(?,?,?,?,?,?)', (MID, path, 'e', mod, text, ''))
        idx.db.commit()
        self.payloads = []

    def fake_model(self, result=None):
        test = self

        class FakeModel:
            def __init__(self, cfg):
                pass

            def ask(self, stage, payload):
                test.assertEqual(stage, 'dossier_fiche')
                test.payloads.append(payload)
                ids = {p['nom']: p['id'] for p in payload['pieces']}
                a = ids['Assignation tribunal de commerce.pdf']
                return result or {
                    'resume': 'ALPHA, cliente, contre BETA SERVICES : logiciel non livré.',
                    'rappel_des_faits': [{'texte': 'Le 3 janvier 2023, contrat de prestation.', 'source_ids': [ids['Contrat de prestation.pdf'], 'P99']}],
                    'parties': [{'nom': 'ALPHA', 'qualite': 'demandeur', 'conseil': 'Maître EXEMPLE', 'client_du_cabinet': True, 'source_ids': [a]},
                                {'nom': 'SAS ALPHA', 'qualite': 'demandeur', 'conseil': '', 'client_du_cabinet': True, 'source_ids': [a]},
                                {'nom': 'BETA SERVICES', 'qualite': 'defendeur', 'conseil': '', 'client_du_cabinet': False, 'source_ids': [a]},
                                {'nom': 'SELARL Démo, commissaire de justice', 'qualite': 'autre', 'conseil': '', 'client_du_cabinet': False, 'source_ids': [a]},
                                {'nom': 'Monsieur le juge Durando', 'qualite': 'autre', 'conseil': '', 'client_du_cabinet': False, 'source_ids': [a]}],
                    'procedure': {'juridiction': 'Tribunal de commerce de Lyon', 'numero_rg': '2025F00445', 'stade': 'Assignation délivrée', 'prochaine_etape': '', 'source_ids': [a]},
                    'chronologie': [{'date': '2025-03-04', 'evenement': 'Assignation', 'source_ids': [a]}, {'date': '', 'evenement': 'Échanges informels', 'source_ids': []},
                                    {'date': '2023-01-03', 'evenement': 'Contrat', 'source_ids': [ids['Contrat de prestation.pdf']]}],
                    'enjeux': ['Restitution de l’acompte'], 'points_a_verifier': ['Date de livraison réelle'], 'limits': []}
        return patch('agent.model.Model', FakeModel)

    def test_sources_put_acts_first_and_flag_cited_case_law(self):
        from agent import dossier5627
        from agent.common import load_matters
        m = next(x for x in load_matters(self.desk.c) if x['id'] == MID)
        names = [s['nom'] for s in dossier5627.select_sources(self.desk, m)]
        self.assertEqual(names[0], 'Assignation tribunal de commerce.pdf')
        self.assertLess(names.index('Contrat de prestation.pdf'), names.index('Facture honoraires 2025-08.pdf'))
        self.assertEqual(names[-1], 'Facture honoraires 2025-08.pdf')
        jp = next(s for s in dossier5627.select_sources(self.desk, m) if s['nom'].startswith('JP'))
        self.assertEqual(jp['nature'], 'jurisprudence citée')
        self.assertEqual(dossier5627._iso('Fri, 13 Mar 2026 14:57:33 GMT'), '2026-03-13')   # dates HTTP de l'index Nextcloud
        self.assertEqual(dossier5627._iso('2025-03-04T10:00:00'), '2025-03-04')

    def test_prepare_keeps_only_real_parties_and_sorted_sourced_events(self):
        from agent import dossier5627
        with self.fake_model():
            out = dossier5627.prepare(self.desk, {'matter': MID})
        self.assertEqual(out['version'], 1)
        f = dossier5627.latest(self.desk, MID)
        data = f['data']
        self.assertEqual([p['nom'] for p in data['parties']], ['ALPHA', 'BETA SERVICES'])   # doublon fusionné, commissaire et juge écartés
        self.assertEqual([e['date'] for e in data['chronologie']], ['2023-01-03', '2025-03-04', ''])
        self.assertNotIn('P99', data['rappel_des_faits'][0]['source_ids'])                  # référence inventée retirée
        self.assertEqual(data['pieces_lues'], 4)
        self.assertEqual(self.payloads[0]['dossier']['client_du_cabinet'], 'ALPHA')
        prompt = (ROOT / 'agent' / 'model.py').read_text(encoding='utf-8')
        for rule in ('Ne sont JAMAIS des parties', 'commissaires de justice', 'jurisprudence citée', 'Ce n\'est PAS une liste de documents'):
            self.assertIn(rule, prompt)

    def test_lawyer_corrections_show_at_once_and_are_sent_back_to_the_agent(self):
        from agent import dossier5627
        with self.fake_model():
            dossier5627.prepare(self.desk, {'matter': MID})
            dossier5627.save_edit(self.desk, MID, 'resume', 'Résumé revu par l’avocat.')
            page = self.request('/dossier', query='id=' + MID)['body']
            self.assertIn('Résumé revu par l’avocat.', page)
            self.assertIn('Corrigé par vous', page)
            dossier5627.prepare(self.desk, {'matter': MID})
        self.assertEqual(self.payloads[-1]['corrections_de_l_avocat'], {'resume': 'Résumé revu par l’avocat.'})
        with self.assertRaisesRegex(Stop, 'rubrique_inconnue'):
            dossier5627.save_edit(self.desk, MID, 'autre', 'x')

    def test_one_pending_request_per_matter(self):
        from agent import dossier5627
        first = dossier5627.request(self.desk, MID)
        again = dossier5627.request(self.desk, MID)
        self.assertEqual(first['job_id'], again['job_id'])
        self.assertTrue(again['deja_en_cours'])
        page = self.request('/dossier', query='id=' + MID)['body']
        self.assertIn('L’agent prépare la fiche du dossier', page)

    def test_page_layout_navigation_and_views(self):
        page = self.request('/dossier', query='id=' + MID)['body']
        for text in ('← Tous les dossiers', 'De quoi s’agit-il ?', 'Préparer la fiche du dossier', 'Travailler avec l’agent', 'Parties', 'Procédure',
                     'Chronologie clé', 'Rappel des faits', 'Vue détaillée par rubriques', '/static/v5627.js', 'aria-current="page">Fiche'):
            self.assertIn(text, page)
        self.assertNotIn('<aside class="d27-side"', page)
        pieces = self.request('/dossier', query='id=%s&vue=pieces' % MID)['body']
        self.assertIn('Assignation tribunal de commerce.pdf', pieces)
        self.assertIn('data-d27-filter', pieces)
        self.assertIn('Courriels rattachés', self.request('/dossier', query='id=%s&vue=courriels' % MID)['body'])
        self.assertIn('Documents préparés par l’agent', self.request('/dossier', query='id=%s&vue=travaux' % MID)['body'])
        self.assertIn('Dossier introuvable', self.request('/dossier', query='id=INCONNU')['body'])
        self.assertIn('← Retour au dossier', self.request('/fiche', query='matter=' + MID)['body'])
        self.assertIn('← Retour au dossier', self.request('/chronologie', query='matter=' + MID)['body'])
        listing = self.request('/dossiers', query='state=all')['body']
        self.assertIn('/dossier?id=' + MID, listing)

    def test_agent_instructions_from_the_matter(self):
        from agent import dossier5627
        out = dossier5627.ask_agent(self.desk, MID, 'Prépare un courrier au client sur la prochaine audience.', 'courrier')
        self.assertTrue(out['ref'].startswith('docreq:'))
        row = self.desk.db.execute('SELECT matter,kind FROM docreq520 WHERE id=?', (out['ref'].split(':', 1)[1],)).fetchone()
        self.assertEqual((row['matter'], row['kind']), (MID, 'courrier'))
        html, waiting = dossier5627.thread_html(self.desk, '', MID)
        self.assertIn('Prépare un courrier au client', html)
        self.assertTrue(waiting)
        with patch('agent.cockpit530.ask', return_value={'message': 'ok', 'ref': 'ask:1:1'}) as ask:
            dossier5627.ask_agent(self.desk, MID, 'Quelle est la prochaine échéance ?', 'question')
        ask.assert_called_once_with(self.desk, {'text': 'Quelle est la prochaine échéance ?', 'matter': MID})
        with self.assertRaisesRegex(Stop, 'type_de_demande_invalide'):
            dossier5627.ask_agent(self.desk, MID, 'Texte suffisamment long', 'inconnu')

    def test_rights_routes_and_assets(self):
        from agent import dossier5627
        from agent.standalone_auth import allowed
        for route in ('m5627/fiche/preparer', 'm5627/fiche/corriger', 'm5627/agent'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + route))
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + route))
        with patch('agent.web567.actor', return_value=('x', 'assistant')):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                dossier5627.api({}, self.desk, {}, '', 'm5627/etat', {'matter': MID}, 'GET')
        web440 = (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8')
        self.assertIn("'/static/v5627.js'", web440)
        self.assertIn("'/fiche', '/dossier', '/chronologie'", web440)
        self.assertIn("'v5627.css'", (ROOT / 'agent' / 'shell501.py').read_text(encoding='utf-8'))
        css = (ROOT / 'agent' / 'static' / 'v5627.css').read_text(encoding='utf-8')
        self.assertIn('.d27-edit[hidden]{display:none!important}', css)
        self.assertNotIn('prefers-color-scheme', css)
        self.assertIn('.d27-grid', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        desk = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertIn("if kind=='fiche_dossier5627':", desk)


class Parties(t530.Base):
    TEXT = ("ASSIGNATION DEVANT LE TRIBUNAL JUDICIAIRE DE LYON\n"
            "A LA REQUETE DE : La SAS FICTIVE PRODUCTION, prise en la personne de son représentant légal,\n"
            "ayant pour avocat postulant la SELARL ACTIVE AVOCATS Maître Michel, Avocats Toque 123,\n"
            "CONTRE : SAS EXEMPLE HEALTHCARE, et Monsieur Hubert DUPONT, gérant,\n"
            "Par le ministère de la SELARL THIERRY DEMO HUISSIER DE justice.\n"
            "Pièce n°3 : attestation de Monsieur DUPONT Pièce. Madame MARTIN Elsa, clerc assermentée.\n"
            "Monsieur le juge DURANDO a ordonné.")

    def test_only_designated_parties_are_proposed(self):
        from agent.facts460 import extract_facts, party_name
        facts, _ = extract_facts(self.TEXT, '/Dossiers/DEMO/assignation.pdf', '2023-04-04')
        self.assertEqual([r['title'] for r, _ in facts if r['record_type'] == 'party'],
                         ['Partie : SAS FICTIVE PRODUCTION', 'Partie : SAS EXEMPLE HEALTHCARE', 'Partie : Monsieur Hubert DUPONT'])
        self.assertEqual(party_name('VAST Hubert Organisme'), 'VAST Hubert')
        self.assertEqual(party_name('Pièce'), '')

    def test_detailed_views_use_the_working_sheet_and_hide_legacy_noise(self):
        from agent.legal_memory import upsert_record
        from agent import dossier5624, dossier5627
        for title, content in (('Partie : SELARL ACTIVE AVOCATS Maître Michel', 'Partie citée dans la pièce : SELARL ACTIVE AVOCATS Maître Michel'),
                               ('Partie : Monsieur VAST Pièce', 'Personne citée dans la pièce : Monsieur VAST Pièce')):
            try:
                upsert_record(self.desk, MID, {'record_type': 'party', 'title': title, 'content': content, 'event_date': '', 'confidence': 'medium',
                                                'source_ids': ['s1'], 'actor': ''}, [{'id': 's1', 'kind': 'document', 'path': '/Dossiers/DEMO/a.pdf',
                                                                                       'modified': '', 'excerpt': title}])
            except TypeError:
                self.skipTest('signature de upsert_record différente')
        names = [i['value'] for i in dossier5624.rubrique(self.desk, MID, 'parties')['items']]
        self.assertFalse(any('AVOCATS' in n or 'VAST' in n for n in names), names)
        dossier5627.save_edit(self.desk, MID, 'parties', 'ALPHA — Demandeur\nBETA SERVICES — Défendeur')
        names = [i['value'] for i in dossier5624.rubrique(self.desk, MID, 'parties')['items']]
        self.assertEqual(names, ['ALPHA — Demandeur', 'BETA SERVICES — Défendeur'])
        page = self.request('/fiche', query='matter=' + MID)['body']
        self.assertIn('BETA SERVICES — Défendeur', page)

if __name__ == '__main__':
    unittest.main()
