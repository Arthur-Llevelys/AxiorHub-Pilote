"""5.2.0 : demande de document depuis la page Documents ; rapport « Pourquoi peu de brouillons ? »."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path, PurePosixPath
import unittest
from unittest.mock import patch
import zipfile

from agent import docrequest520 as dr, web520
from agent.common import Stop, private_json
from agent.state import State
import test_v510 as t510
import test_v520_routines as tr

ALPHA = {**t510.DEMO, 'id': '20240101001', 'client_name': 'ALPHA', 'path': '/Dossiers/DEMO', 'references': ['20240101001'], 'aliases': ['ALPHA']}
BETA = {**t510.DEMO, 'id': '2024020201', 'client_name': 'BRAVO', 'path': '/Dossiers/BRAVO - SCI LES CHENES - 2024020201', 'references': [], 'aliases': []}


class DocModel:
    def __init__(self):
        self.payloads = []

    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        payload = json.loads(messages[-1]['content'].split('\n\n', 1)[1])
        self.payloads.append(payload)
        title = 'Courrier de demande de renvoi' + (' (révisé)' if payload['document_actuel'] else '')
        return json.dumps({'titre': title, 'nom_fichier': 'Courrier renvoi', 'paragraphes': [
            {'style': 'texte', 'texte': 'Cher Confrère,'}, {'style': 'texte', 'texte': 'Je sollicite le renvoi de l’audience du [À COMPLÉTER : date].'}],
            'sources': ['P1', 'C1'], 'a_completer': ['date de l’audience']})


class Docs(t510.Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [ALPHA, BETA])
        self.f.c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts'})
        self.model = DocModel()
        for p in (patch('agent.docrequest520._model', return_value=self.model), patch('agent.document_projects._dav', return_value=self.docs),
                  patch('agent.mailbox.Mailbox', tr.FakeBox)):
            p.start()
            self.addCleanup(p.stop)
        from agent.portfolio import ensure_schema
        ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO portfolio_mail_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('l1', 'k1', '<a2@x>', '<a2@x>', 'INBOX', '2', '20240101001', 95, 'high', 'test', 'durand@example.test', 'cabinet', '2026-10-01', 'automatic', '2026-10-01'))
        from agent.workplan import ensure_schema as wp
        wp(self.desk)
        soon = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('e1', 'u', '', 'c', '/e.ics', 'e', 'Audience TJ Lyon ALPHA', '', '', soon, soon, 1, '20240101001', '[]', soon))
        self.desk.db.commit()

    def run_job(self, rid):
        return dr.perform(self.desk, 'docrequest520', {'request': rid})

    def test_matter_found_in_request_and_document_saved_as_new_file(self):
        rid = dr.submit(self.desk, 'Prépare un courrier au confrère pour un renvoi dans le dossier ALPHA', '', 'courrier')['request']
        self.run_job(rid)
        row = dr.listing(self.desk)[0]
        self.assertEqual((row['status'], row['matter']), ('cree', '20240101001'))
        self.assertTrue(row['path'].startswith('/Dossiers/DEMO/'))
        self.assertTrue(row['path'].endswith('Courrier renvoi.docx'))
        text = zipfile.ZipFile(io.BytesIO(self.docs.files[row['path']])).read('word/document.xml').decode()
        self.assertIn('Je sollicite le renvoi', text)
        self.assertIn('Sources utilisées', text)
        ctx = self.model.payloads[0]['dossier']
        self.assertTrue(ctx['fichiers'])
        self.assertEqual(ctx['agenda'][0]['evenement'], 'Audience TJ Lyon ALPHA')
        self.assertEqual(ctx['courriels'][0]['objet'], 'Assemblée générale')
        rid2 = dr.submit(self.desk, 'Prépare un courrier au confrère pour un renvoi dans le dossier ALPHA', '', 'courrier')['request']
        self.run_job(rid2)
        self.assertTrue(dr.listing(self.desk)[0]['path'].endswith('Courrier renvoi (2).docx'))   # jamais d'écrasement

    def test_revision_creates_a_new_version_next_to_the_original(self):
        rid = dr.submit(self.desk, 'Courrier de renvoi pour ALPHA', '20240101001', 'courrier')['request']
        self.run_job(rid)
        first = dr.listing(self.desk)[0]['path']
        original = self.docs.files[first]
        rev = dr.submit(self.desk, 'Ton plus ferme', '', 'auto', rid)['request']
        self.run_job(rev)
        second = dr.listing(self.desk)[0]['path']
        self.assertTrue(second.endswith(' v2.docx'))
        self.assertEqual(self.docs.files[first], original)
        self.assertTrue(self.model.payloads[-1]['document_actuel'])

    def test_ambiguous_request_asks_for_the_matter(self):
        rid = dr.submit(self.desk, 'Prépare une note de synthèse sur les échanges récents', '', 'note')['request']
        self.run_job(rid)
        row = dr.listing(self.desk)[0]
        self.assertEqual(row['status'], 'dossier_a_choisir')
        self.assertEqual(self.docs.puts, [])
        body = self.request('/documents')['body']
        self.assertIn('Demander un document à l’agent', body)
        self.assertIn('Préciser le dossier', body)

    def test_conflict_gate_applies_to_found_matter(self):
        from agent import conflicts500
        conflicts500.m5.ensure_schema(self.desk)
        self.desk.db.execute('INSERT OR REPLACE INTO conflicts500_matters VALUES(?,?,?,?,?)', ('2024020201', '2026-10-01', 0, '', 'a_examiner'))
        self.desk.setting('conflicts500:baseline_done', '2026-10-01')
        self.desk.setting('conflicts500:released501', '2026-10-01')
        self.desk.db.commit()
        rid = dr.submit(self.desk, 'Courrier au client BRAVO - SCI LES CHENES - 2024020201', '', 'courrier')['request']
        with self.assertRaises(Stop):
            self.run_job(rid)
        self.assertEqual(dr.listing(self.desk)[0]['status'], 'echec')
        with self.assertRaises(Stop):
            dr.submit(self.desk, 'Courrier au client', '2024020201', 'courrier')

    def test_validation(self):
        with self.assertRaises(Stop):
            dr.submit(self.desk, 'court', '', 'auto')
        with self.assertRaises(Stop):
            dr.submit(self.desk, 'Une demande suffisante', 'INCONNU', 'auto')
        with self.assertRaises(Stop):
            dr.submit(self.desk, 'Une demande suffisante', '', 'inexistant')


class MailPipeline(t510.Base):
    def test_reasons_are_counted_with_actions(self):
        st = State(self.f.c['state_dir'])
        for i, (status, reason) in enumerate([('drafted', ''), ('ignored', 'tri_ia_none'), ('ignored', 'tri_ia_none'),
                                              ('review', 'correspondant_ou_dossier_a_confirmer'), ('observed', 'proposition_sans_ecriture_imap')]):
            st.set('k%d' % i, '<m%d@x>' % i, 't%d' % i, status, reason)
        mp = web520.mail_pipeline(self.desk)
        self.assertEqual((mp['total'], mp['drafted']), (5, 1))
        top = mp['reasons'][0]
        self.assertEqual((top['reason'], top['count']), ('tri_ia_none', 2))
        self.assertTrue(any(r['action'][1] == '/associations' for r in mp['reasons']))
        body = self.request('/courriels')['body']
        self.assertIn('Pourquoi peu de brouillons ?', body)
        self.assertIn('5 courriel(s) examiné(s)', body)
        self.assertIn('Courriels et brouillons (7 jours)', self.request('/diagnostic')['body'])


if __name__ == '__main__':
    unittest.main()
