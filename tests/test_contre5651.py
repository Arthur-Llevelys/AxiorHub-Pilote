"""Contre-tests d'audit 5.6.51 : données fictives, aucune connexion externe.

Exécution via reproduire.py --sources CHEMIN. Les assertions expriment le
comportement attendu après correction, et échouent sur la version originale.
"""
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_d5651 as fixture
import test_v5650 as web_fixture
from agent import apprentissage5651 as ap, contexte5651 as ctx
from agent import memoire5651 as mem, missions5651 as missions, modele5651 as md
from agent import relecture5651 as word, recit5651 as recit, vue5651 as vue
from agent import membres5650 as rights, routines520
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.index import DocumentIndex

OBSERVATIONS = {}
MID = fixture.MID


class DataRegressions(fixture.Base):
    def test_F02_contexte_actualise_sans_ouverture_prealable(self):
        context = ctx.for_drafting(self.desk, MID)
        OBSERVATIONS['F02'] = {'documents_indexes': len(fixture.DOCS),
                               'reconstruction_prealable': md.last_rebuild(self.desk, MID),
                               'cles_contexte_transmis': list(context)}
        self.assertTrue(any('infiltrations' in str(v) for v in context.values()),
                        'Le contexte de rédaction ignore les lectures existantes tant que la page Dossier n’a pas été ouverte.')

    def test_F03_ancienne_affirmation_non_reutilisee_apres_remplacement_source(self):
        path = fixture.DOCS[0][0]
        md.rebuild(self.desk, MID)
        old = 'Les locaux sont affectés d’infiltrations ayant perturbé l’activité.'
        old_item = next(a for a in md.assertions(self.desk, MID) if a['text'] == old)
        index = DocumentIndex(self.desk.c['state_dir'])
        try:
            index.db.execute('UPDATE docs SET etag=?,text=? WHERE matter=? AND path=?',
                             ('version-2', 'ASSIGNATION rectifiée : les infiltrations sont exclues.', MID, path))
            index.db.commit()
        finally:
            index.db.close()
        fixture._digest(self.desk, path, {'nature': 'Assignation', 'parties': ['SARL Alpha Fictive'],
                        'faits': [{'texte': 'Les infiltrations sont exclues après examen du constat.', 'statut': 'documente'}]}, etag='version-2')
        md.rebuild(self.desk, MID, force=True)
        context = ctx.for_drafting(self.desk, MID)
        facts_current = [x for key in ('faits_dates', 'faits_contexte_date_non_determinee') for x in context.get(key, [])]
        current = next(a for a in md.assertions(self.desk, MID) if a['id'] == old_item['id'])
        OBSERVATIONS['F03'] = {'ancienne_information': current['text'], 'revue': current['review'],
                               'anciennes_sources': current['sources'], 'ancienne_information_dans_contexte': old in str(context),
                               'nouvelle_information_dans_contexte': 'infiltrations sont exclues' in str(context)}
        self.assertNotIn(old, str(facts_current), 'L’historique des sources remplacées ne doit pas rester une information courante sans alerte.')

    def test_F04a_changer_procedure_invalide_son_rappel(self):
        md.rebuild(self.desk, MID)
        pid = md.save_procedure(self.desk, MID, 'avocat@example.test',
                                {'juridiction': 'TJ fictif A', 'rg': '26/00001', 'reference': True})
        class Model:
            def complete(self, messages, **kwargs):
                items = json.loads(messages[-1]['content'])['informations']
                key = next(k for k, v in items.items() if v.get('rg') == '26/00001')
                return json.dumps({'paragraphes': [{'texte': 'L’affaire est inscrite sous le RG 26/00001 au TJ fictif A.', 'informations': [key]}]})
        recit.run(self.desk, {'matter': MID, 'kind': 'procedure'}, model=Model())
        md.save_procedure(self.desk, MID, 'avocat@example.test',
                          {'id': pid, 'version': 1, 'juridiction': 'TJ fictif B', 'rg': '26/00002', 'reference': True})
        narrative = vue.narrative(self.desk, MID, 'procedure')
        OBSERVATIONS['F04a'] = {'rappel': narrative['paragraphs'], 'a_actualiser': narrative['stale'],
                                'rg_courant': md.reference_procedure(self.desk, MID)['rg']}
        self.assertTrue(narrative['stale'], 'La base du rappel ignore les procédures et décisions qui ont servi à sa rédaction.')

    def test_F04b_information_modifiee_pendant_generation_invalide_rappel(self):
        md.rebuild(self.desk, MID)
        desk = self.desk
        row = next(a for a in md.assertions(desk, MID, 'fait') if 'infiltrations' in a['text'])
        class Model:
            def complete(self, messages, **kwargs):
                items = json.loads(messages[-1]['content'])['informations']
                key = next(k for k, v in items.items() if 'infiltrations' in v['texte'])
                # Une correction autorisée survient pendant l'attente du modèle.
                md.review(desk, MID, row['id'], 'avocat@example.test', 'corriger',
                          {'version': row['version'], 'texte': 'Les infiltrations alléguées ont été exclues par le constat.'})
                desk.db.execute('UPDATE d51_assertions SET updated=? WHERE id=?', ('2099-01-01T00:00:00+00:00', row['id']))
                desk.db.commit()
                return json.dumps({'paragraphes': [{'texte': items[key]['texte'], 'informations': [key]}]})
        recit.run(desk, {'matter': MID, 'kind': 'faits'}, model=Model())
        narrative = vue.narrative(desk, MID, 'faits')
        OBSERVATIONS['F04b'] = {'texte_base_ancienne': narrative['paragraphs'], 'a_actualiser': narrative['stale']}
        self.assertTrue(narrative['stale'], 'La signature est calculée après l’appel IA sur des données différentes de celles envoyées au modèle.')

    def test_F08_preparation_bloquee_reprend_apres_pause(self):
        from agent import echeances450, pause5629
        if hasattr(echeances450, 'ensure_schema'):
            echeances450.ensure_schema(self.desk)
        today = date(2026, 10, 16)
        due = (today + timedelta(days=5)).isoformat()
        self.desk.db.execute('INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             ('deadline-audit', MID, 'audience-fictive', 'acte-fictif', today.isoformat(), due, 'confirmee', 'manuel', today.isoformat(), today.isoformat()))
        self.desk.db.commit()
        missions.set_mode(self.desk, MID, 'avocat@example.test', {'mode': 'preparation_automatique', 'paliers': '7'})
        pause5629.set_pause(self.desk, MID, True)
        first = missions.run_due(self.desk, MID, today=today)
        pause5629.set_pause(self.desk, MID, False)
        second = missions.run_due(self.desk, MID, today=today)
        rows = missions.plan(self.desk, MID, today=today)
        OBSERVATIONS['F08'] = {'pendant_pause': first, 'apres_reprise': second,
                               'etats_preparations': [p['state'] for p in rows]}
        self.assertEqual(len(second), 1, 'La préparation reste bloquée après reprise, sans nouveau lancement au palier échu.')

    def test_F10_routine_non_perdue_si_file_pleine(self):
        now = datetime(2026, 10, 16, 8, 30, tzinfo=timezone.utc)
        cfg = routines520.settings(self.desk)
        for kind in ('tri', 'bilan', 'briefing'):
            cfg[kind]['enabled'] = kind == 'briefing'
            cfg[kind]['time'] = '10:00'
            cfg[kind]['days'] = '1234567'
        self.desk.setting('routines520', cfg)
        self.desk.setting('routines520:since', '2026-10-15T06:00:00+00:00')
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            with self.assertRaisesRegex(Stop, 'file_attente_pleine'):
                routines520.tick(self.desk, now=now)
        marker = self.desk.settings('routines520:last:briefing', '')
        with patch.object(self.desk, 'enqueue', return_value=12345) as enqueue:
            second = routines520.tick(self.desk, now=now + timedelta(minutes=1))
        OBSERVATIONS['F10'] = {'marque_apres_refus': marker, 'nouvelle_tentative': second,
                               'appels_mise_en_file_apres_reprise': enqueue.call_count}
        self.assertEqual(enqueue.call_count, 1, 'La routine est marquée faite avant que sa mise en file ait réussi.')


class WordRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.Review('run')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.desk, self.client, self.path = self.fixture.desk, self.fixture.client, self.fixture.path
        md.rebuild(self.desk, MID)

    def generate(self, propositions):
        session = word.start(self.desk, MID, 'avocat@example.test', self.path,
                             'Contrôler le document et proposer les corrections utiles', client=self.client)
        word.run(self.desk, {'session': session['id']}, model=fixture.FakeModel(propositions), client=self.client)
        return word.session(self.desk, MID, session['id'])

    def test_F05_source_modifiee_interdit_application_sans_nouveau_controle(self):
        session = self.generate([{'index': 0, 'remplacement': 'Le preneur doit 1 250 euros.', 'motif': 'facture',
                                 'nature': 'fait', 'sources': ['Facture 2024-03.pdf']}])
        p = session['proposals'][0]
        word.decide(self.desk, MID, p['id'], 'avocat@example.test', 'accepter', p['version'])
        self.desk.db.execute('UPDATE d51_documents SET etag=? WHERE matter=? AND path=?',
                             ('facture-modifiee', MID, fixture.DOCS[3][0]))
        self.desk.db.commit()
        pending = word.session(self.desk, MID, session['id'])['proposals'][0]
        result, error = None, None
        try:
            result = word.apply(self.desk, MID, session['id'], 'avocat@example.test', client=self.client)
        except Stop as ex:
            error = str(ex)
        OBSERVATIONS['F05'] = {'source_a_recontroler': pending['a_recontroler'],
                               'application': result, 'erreur': error}
        self.assertIsNone(result, 'Une proposition fondée sur une source modifiée est tout de même appliquée au Word.')

    def test_F06_ancre_ambigue_ne_revise_pas_autre_paragraphe_identique(self):
        p = '<w:p><w:r><w:t>La demande doit être rejetée.</w:t></w:r></w:p>'
        initial = fixture._docx(p + fixture.P2 + p)
        first = word.paragraphs(initial)[0]
        current = fixture._docx(fixture.P2 + p)  # le premier paragraphe visé a été supprimé par l'avocat
        _, applied, manual, stale = word.apply_to_docx(current, [{'id': 'prop-fictive', 'anchor': first['anchor'],
            'occurrence': first['occurrence'], 'para_index': first['index'], 'expected': first['text'],
            'replacement': 'La demande doit être accueillie.'}])
        OBSERVATIONS['F06'] = {'paragraphe_initial': first['index'], 'original_supprime': True,
                               'appliquees': applied, 'manuelles': manual, 'perimees': stale}
        self.assertEqual(applied, [], 'Le rang parmi les paragraphes identiques s’est déplacé : un autre passage est réécrit.')

    def test_F07_application_successive_conserve_revisions_precedentes(self):
        first_new = 'Le preneur doit 1 250 euros.'
        second_new = 'Le bailleur conteste formellement.'
        session = self.generate([{'index': 0, 'remplacement': first_new, 'motif': 'facture', 'nature': 'fait'},
                                 {'index': 1, 'remplacement': second_new, 'motif': 'style', 'nature': 'style'}])
        p, q = session['proposals']
        word.decide(self.desk, MID, p['id'], 'avocat@example.test', 'accepter', p['version'])
        with patch('agent.relecture5651.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 16, 8, 30, 0, tzinfo=timezone.utc)
            first = word.apply(self.desk, MID, session['id'], 'avocat@example.test', client=self.client)
            before = self.client.download({'path': first['path'], 'etag': ''})
            word.decide(self.desk, MID, q['id'], 'avocat@example.test', 'accepter', q['version'])
            clock.now.return_value = datetime(2026, 10, 16, 8, 30, 2, tzinfo=timezone.utc)
            second = word.apply(self.desk, MID, session['id'], 'avocat@example.test', client=self.client)
        after = self.client.download({'path': second['path'], 'etag': ''})
        texts = [p['text'] for p in word.paragraphs(after)]
        OBSERVATIONS['F07'] = {'premiere_sortie': first['path'], 'seconde_sortie': second['path'],
                               'meme_chemin': first['path'] == second['path'],
                               'premiere_version_conservee': before == self.client.download({'path': first['path'], 'etag': ''}),
                               'textes_derniere_sortie': texts}
        self.assertIn(first_new, texts, 'La seconde application repart de l’original et omet la première modification pourtant marquée appliquée.')

    def test_F09_correction_limitee_document_transmise_a_relecture(self):
        instruction = 'Préférer exclusivement la formule « Nous sollicitons le rejet » dans ce document.'
        ap.learn(self.desk, 'avocat@example.test', {'portee': 'document', 'categorie': 'style', 'matter': MID,
                 'document': self.path, 'consigne': instruction, 'avant': 'Nous demandons', 'apres': 'Nous sollicitons'})
        captured = []
        class Model:
            def complete(self, messages, **kwargs):
                captured.append(json.loads(messages[-1]['content']))
                return json.dumps({'propositions': []})
        session = word.start(self.desk, MID, 'avocat@example.test', self.path, 'Relire le document', client=self.client)
        word.run(self.desk, {'session': session['id']}, model=Model(), client=self.client)
        expected = ap.rules_for(self.desk, MID, 'avocat@example.test', self.path)
        actual = captured[0]['dossier_structure'].get('regles_apprises', [])
        OBSERVATIONS['F09'] = {'regles_document_enregistrees': expected, 'regles_recues_par_modele': actual}
        self.assertIn(instruction, str(actual), 'Le chemin du document n’est jamais passé au service de contexte ; la règle est ignorée.')


class AccessRegressions(unittest.TestCase):
    def setUp(self):
        self.f = web_fixture.ServerAccounts('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        rights.clear()
        self.addCleanup(rights.clear)
        self.admin = self.f.admin_cookie()
        self.lawyer = self.f.add(self.admin, 'autre@example.test', 'avocat')
        matters_path = Path(self.f.f.c['matters_file'])
        matters = json.loads(matters_path.read_text())
        matters.append({'id': 'DOS-002', 'client_name': 'Autre client fictif', 'path': '/Dossiers/AUTRE', 'references': ['DOS-002']})
        private_json(matters_path, matters)

    def test_F01_regle_dossier_reserve_non_modifiable_via_autre_dossier(self):
        desk = Desk(self.f.f.c)
        self.addCleanup(desk.db.close)
        rule = ap.learn(desk, 'cabinet', {'portee': 'dossier', 'categorie': 'strategie', 'matter': MID,
                        'consigne': 'Ne pas invoquer la force majeure dans ce dossier fictif.'})
        rights.set_reserve(str(self.f.state), MID, ['avocate@example.test'])
        blocked = self.f.request('/dossier', query='id=' + MID, cookie=self.lawyer)
        response = self.f.api('m5651/regle', {'matter': 'DOS-002', 'id': rule['id'], 'status': 'desactivee'}, self.lawyer)
        final = desk.db.execute('SELECT status,matter FROM d51_learned WHERE id=?', (rule['id'],)).fetchone()
        OBSERVATIONS['F01'] = {'page_dossier_reserve': blocked['status'], 'api_avec_autre_dossier': response['status'],
                               'regle_dossier': final['matter'], 'regle_etat_apres_requete': final['status'],
                               'contenu_retourne': response['body'][:900].decode('utf-8', 'replace') if isinstance(response['body'], bytes) else response['body'][:900]}
        self.assertIn(response['status'][:3], ('403', '404'),
                      'La règle du dossier réservé est modifiée via le paramètre d’un dossier accessible.')
        self.assertEqual(final['status'], 'active')
