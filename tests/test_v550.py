"""5.5.0 : style du cabinet (analyse des écrits définitifs et des courriels envoyés, habitudes proposées, validées, appliquées) ;
feuilles de style toujours à jour (adresse versionnée, cache de l'application mobile) ; dossier des brouillons retrouvé."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from agent import cockpit530 as ck, docrequest520 as dr, style550, style550_ui
from agent.common import Stop, private_json
from agent.state import State
import test_v510 as t510
import test_v520_docs as td

CONCLUSIONS = '''CONCLUSIONS RÉCAPITULATIVES
POUR : M. Jean DUPOND
I. FAITS ET PROCÉDURE
Par acte du 3 mars 2025, la société a assigné M. DUPOND devant le Tribunal judiciaire de Lyon.
Le contrat liait les parties depuis plusieurs années et la relation était ancienne et paisible.
II. DISCUSSION
A. Sur la recevabilité
Les demandes nouvelles formées en cause d'appel sont irrecevables en application des textes applicables.
B. Sur le fond
Aucune faute ne peut être reprochée au concluant qui a toujours exécuté ses obligations de bonne foi.
PAR CES MOTIFS
Il est demandé au Tribunal de débouter la demanderesse de l'ensemble de ses demandes, fins et conclusions.'''
LETTER = '''Mon cher Confrère,
Je fais suite à notre entretien téléphonique concernant le dossier {ref}.
Mon client m'indique ne pas être opposé à un rapprochement amiable dans cette affaire.
Je vous remercie de bien vouloir me faire connaître la position de votre client dans les meilleurs délais.
Je reste à votre disposition pour en convenir ensemble.
Je vous prie de croire, Mon cher Confrère, en l'assurance de mes sentiments confraternels les meilleurs.
Camille EXEMPLE'''


class Analyse(unittest.TestCase):
    def test_features_without_identifying_data(self):
        f = style550.analyse(LETTER.format(ref='DUPOND c/ MARTIN'))
        self.assertEqual(f['opening'], 'Mon cher Confrère,')
        self.assertIn('sentiments confraternels', f['closing'])
        self.assertTrue(f['steps'])
        self.assertIn('Je reste à votre disposition pour en convenir ensemble.', f['formulas'])
        self.assertFalse(any('DUPOND' in s or 'MARTIN' in s for s in f['formulas']))
        self.assertEqual(style550.analyse('Cher Monsieur DUPOND,\nMerci pour votre message.')['opening'], 'Cher Monsieur [Nom],')
        c = style550.analyse(CONCLUSIONS)
        self.assertEqual(c['headings'], ['FAITS ET PROCÉDURE', 'DISCUSSION', 'PAR CES MOTIFS'])      # ni titre, ni sous-parties
        self.assertNotIn('DUPOND', c['skeleton'])
        self.assertEqual(style550.classify('procedure', 'Conclusions n°2.docx', ''), 'conclusions')
        self.assertEqual(style550.classify('correspondance', 'Lettre.docx', 'Mon cher Confrère,'), 'courrier_confrere')
        self.assertEqual(style550.classify('projets', 'Avenant.docx', ''), 'contrat')
        self.assertEqual(style550.classify('livrables', 'Note.docx', ''), 'consultation')


class Corpus(t510.Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [td.ALPHA, td.BETA])
        root = '/Dossiers/DEMO'
        for i in range(3):
            self.docs.files['%s/PROCEDURE/Conclusions %d.docx' % (root, i)] = t510.make_docx(CONCLUSIONS)
            self.docs.files['%s/CORRESPONDANCES/Lettre %d.docx' % (root, i)] = t510.make_docx(LETTER.format(ref='REF %d' % i))
        self.docs.files[root + '/CORRESPONDANCES/Projet agent.docx'] = t510.make_docx('Projet préparé par AxiorHub le 01/10/2026\n' + LETTER.format(ref='X'))
        self.model = td.DocModel()
        for p in (patch('agent.document_projects._dav', return_value=self.docs), patch('agent.docrequest520._model', return_value=self.model),
                  patch('agent.mailbox.Mailbox', __import__('test_v520_routines').FakeBox)):
            p.start()
            self.addCleanup(p.stop)

    def sent(self):
        db = sqlite3.connect(Path(self.f.c['state_dir']) / 'sent-memory.sqlite3')
        db.execute('CREATE TABLE IF NOT EXISTS examples_v2 (key TEXT PRIMARY KEY, matter TEXT, recipients TEXT, sent_at TEXT, body TEXT, draft TEXT, stats TEXT, provenance TEXT, account TEXT, audience TEXT)')
        for i in range(3):
            db.execute('INSERT INTO examples_v2 VALUES(?,?,?,?,?,?,?,?,?,?)', ('k%d' % i, 'DEMO', '[]', '2026-10-0%d' % (i + 1),
                       'Bonjour Madame,\nJe vous confirme la bonne réception de vos pièces et je les analyse cette semaine.\n'
                       'Je reviens vers vous avec une proposition de stratégie et un calendrier.\nBien cordialement,', '', '{}', '', '', json.dumps([['c@x.test', 'client']])))
        db.commit()
        db.close()

    def test_scan_proposes_habits_validated_ones_shape_drafting(self):
        self.sent()
        out = style550.scan(self.desk)
        self.assertEqual(out['documents'], 6)                                                  # le projet de l'agent est exclu
        self.assertEqual(out['mails'], 3)
        self.assertEqual(style550.scan(self.desk)['documents'], 0)                              # rien n'est relu s'il n'a pas changé
        o = style550.overview(self.desk)
        texts = {h['kind'] + '|' + h['doc_type']: h for h in o['pending']}
        plan = texts['plan|conclusions']
        self.assertEqual(plan['text'], 'Plan habituel : FAITS ET PROCÉDURE → DISCUSSION → PAR CES MOTIFS')
        self.assertEqual(plan['evidence'], '3 écrit(s) sur 3')
        self.assertIn('Mon cher Confrère,', texts['ouverture|courrier_confrere']['text'])
        self.assertIn('Bien cordialement,', texts['cloture|courriel_client']['text'])
        self.assertTrue(any(h['kind'] == 'formule' and 'Je reste à votre disposition' in h['text'] for h in o['pending']))
        # validation (texte corrigé) → règle métier active, appliquée à la rédaction
        style550.decide(self.desk, {'id': plan['id'], 'action': 'valider', 'text': 'Plan : FAITS ET PROCÉDURE, DISCUSSION, PAR CES MOTIFS'})
        from agent.learning410 import applicable_context
        rules = applicable_context(self.desk, '', 'document_drafting', record=False)['rules']
        self.assertTrue(any('Plan : FAITS ET PROCÉDURE' in r['instruction'] and r['type'] == 'structure' for r in rules))
        ctx = style550.drafting_context(self.desk, 'conclusions')
        self.assertEqual(ctx['habitudes_validees'], ['Plan : FAITS ET PROCÉDURE, DISCUSSION, PAR CES MOTIFS'])
        self.assertTrue(ctx['plans_types'])
        rid = dr.submit(self.desk, 'Prépare des conclusions récapitulatives dans le dossier ALPHA', '20240101001', 'conclusions')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        payload = self.model.payloads[-1]
        self.assertIn('Plan : FAITS ET PROCÉDURE, DISCUSSION, PAR CES MOTIFS', payload['style_du_cabinet']['habitudes_validees'])
        self.assertTrue(any('Plan : FAITS ET PROCÉDURE' in r for r in payload['regles_du_cabinet']))
        # retrait → règle archivée
        style550.decide(self.desk, {'id': plan['id'], 'action': 'retirer'})
        self.assertFalse(applicable_context(self.desk, '', 'document_drafting', record=False)['rules'])
        with self.assertRaises(Stop):
            style550.decide(self.desk, {'id': plan['id'], 'action': 'inconnue'})
        with self.assertRaises(Stop):
            style550.decide(self.desk, {'id': 'absent', 'action': 'valider'})

    def test_pages_api_settings_and_cockpit_card(self):
        style550.scan(self.desk)
        body = self.request('/mon-style')['body']
        for text in ('Mon style', 'Corpus analysé', 'Habitudes à valider', 'Habitudes appliquées', 'Plans types', 'Analyser maintenant',
                     'Affiner avec l’IA', 'data-action="valider"', '/static/v550.js', 'value="PROCEDURE"'):
            self.assertIn(text, body)
        self.assertIn('href="/agent-courriel/mon-style" aria-label="Apprentissage et modèles"', body)
        card = ck.style_html(self.desk, '/p')
        self.assertIn('écrits des dossiers', card)
        self.assertIn('data-habit=', card)
        self.assertIn('Analyse lancée', style550_ui.handle(self.desk, 'm550/scan', {})['message'])
        out = style550_ui.handle(self.desk, 'm550/settings', {'folder_procedure': 'ACTES', 'folder_correspondance': 'COURRIERS', 'folder_projets': 'PROJETS',
                                                               'folder_livrables': 'LIVRABLES', 'mails': True, 'auto': False})
        self.assertEqual(out['folders']['procedure'], 'ACTES')
        self.assertEqual(dr.folders(self.desk)['correspondance'], 'COURRIERS')
        self.assertIsNone(style550.tick(self.desk))                                             # analyse automatique désactivée
        with self.assertRaises(Stop):
            style550_ui.handle(self.desk, 'm550/settings', {'folder_procedure': '../x'})

    def test_daily_tick(self):
        self.assertIsNone(style550.tick(self.desk, now=100000))                                 # installation : rien tout de suite
        self.assertTrue(style550.tick(self.desk, now=100000 + 3700))                            # une heure plus tard
        self.assertIsNone(style550.tick(self.desk, now=100000 + 7200))                          # puis une fois par jour


class Interface(t510.Base):
    def test_stylesheet_address_changes_with_the_version_and_mobile_cache_refreshes(self):
        from agent import __version__, mobile490
        body = self.request('/aujourdhui')['body']
        self.assertIn('/static/app520.css?v=' + __version__, body)
        sw = mobile490.service_worker('/p')
        self.assertIn("'axiorhub-static-' + \"%s\"" % __version__, sw)
        self.assertIn("url.pathname.endsWith('.css')", sw)
        self.assertIn('fetch(req).then(res', sw)
        self.assertIn('/static/v550.js', body)

    def test_draft_matter_found_through_local_state(self):
        st = State(self.f.c['state_dir'])
        st.set('k' * 64, '<orig@x>', 't', 'drafted', '', '<axiorhub-%s@mail-agent.local>' % ('k' * 64))
        self.desk.db.execute('INSERT INTO work_items(mail_key,state,matter,subject) VALUES(?,?,?,?)', ('k' * 64, 'draft_ready', 'DEMO', 'x'))
        self.desk.db.commit()
        self.assertEqual(ck._draft_matter(self.desk, {'agent_key': '', 'message_id': 'axiorhub-%s@mail-agent.local' % ('k' * 64), 'in_reply_to': ''}), 'DEMO')
        self.assertEqual(ck._draft_matter(self.desk, {'agent_key': 'k' * 64}), 'DEMO')
        self.assertEqual(ck._draft_matter(self.desk, {'agent_key': 'z' * 64, 'message_id': 'inconnu@x'}), '')


if __name__ == '__main__':
    unittest.main()
