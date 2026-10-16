"""Contre-tests indépendants : assertions du comportement attendu, données fictives et HTTP simulé."""
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import test_v5652 as fixture
from agent import connexions5652 as cx, routines5652 as rt, membres5650 as membres
from agent.common import Stop

OBSERVATIONS = {}
ACTOR = 'avocat@example.test'

class Operations(fixture.Base):
    def setUp(self):
        super().setUp()
        self.view = self.connect('youtrust', caps=('signature.send_approved',))
        cx.test(self.desk, ACTOR, 'avocat', self.view['id'])
        self.fake.on('POST', '/v3/signature_requests', {'id': 'sr-1', 'status': 'draft'})
        self.fake.on('POST', '/signature_requests/sr-1/documents', {'id': 'doc-1'})
        self.fake.on('POST', '/signature_requests/sr-1/signers', {'id': 'sg-1'})
        self.fake.on('POST', '/signature_requests/sr-1/activate', {'status': 'ongoing'})

    prepare = fixture.Operations.prepare

    def state(self, op):
        return self.desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()[0]

    def test_F01_changer_cle_requiert_nouveau_test(self):
        out = cx.save(self.desk, ACTOR, 'avocat', {'id': self.view['id'], 'provider': 'youtrust', 'api_key': 'nouvelle-cle-fictive-autre-compte'})
        OBSERVATIONS['F01'] = {'etat_apres_changement_cle': out['etat']}
        self.assertEqual(out['etat'], 'acces_enregistres')

    def test_F02_document_inaccessible_ne_bloque_pas_validation(self):
        op = self.prepare()
        with patch.object(cx, '_document', side_effect=Stop('connexion_http_indisponible')):
            with self.assertRaises(Stop):
                cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'])
        state = self.state(op)
        OBSERVATIONS['F02'] = {'etat_apres_echec_lecture_avant_envoi': state, 'repris_par_poll': cx.poll(self.desk)}
        self.assertIn(state, ('a_valider', 'a_reprogrammer', 'echec_avant_envoi'))

    def test_F03_signature_terminee_preuves_en_panne_reste_a_archiver(self):
        op = self.prepare()
        cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'])
        self.fake.on('GET', '/signature_requests/sr-1', {'status': 'done'})
        self.fake.fail['/documents/download'] = 503
        cx.poll(self.desk)
        state = self.state(op)
        self.fake.fail.clear()
        self.fake.on('GET', '/signature_requests/sr-1/documents/download', b'%PDF signe fictif')
        self.fake.on('GET', '/signature_requests/sr-1/audit_trails/download', b'%PDF audit fictif')
        retried = cx.poll(self.desk)
        exists = (self.root / 'Dossiers/DEMO/Convention - document_signe.pdf').exists()
        OBSERVATIONS['F03'] = {'etat_apres_panne': state, 'operations_reprises': retried, 'document_archive': exists}
        self.assertTrue(exists, 'Le document signé doit être récupéré après une panne temporaire.')

    def test_F04_demande_distante_en_brouillon_pas_marquee_envoyee(self):
        op = self.prepare()
        self.fake.fail['/signers'] = 'reseau'
        with self.assertRaises(Stop):
            cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'])
        self.fake.fail.clear()
        self.fake.on('GET', '/signature_requests?external_id=', {'data': [{'id': 'sr-1', 'external_id': op['id']}]})
        self.fake.on('GET', '/signature_requests/sr-1', {'status': 'draft', 'signers': []})
        cx.poll(self.desk)
        state = self.state(op)
        OBSERVATIONS['F04'] = {'etat_local': state, 'etat_distant': 'draft', 'activation_effectuee': any(c[1].endswith('/activate') for c in self.fake.calls)}
        self.assertIn(state, ('resultat_a_verifier', 'preparation_distante_incomplete', 'a_reprendre'))

    def test_F05_connexion_personnelle_autre_compte_non_utilisable(self):
        view = cx.save(self.desk, ACTOR, 'avocat', {'provider': 'youtrust', 'portee': 'personnel', 'api_key': 'cle-personnelle-fictive', 'cap_signature_send_approved': True})
        cx.test(self.desk, ACTOR, 'avocat', view['id'])
        op = cx.prepare(self.desk, ACTOR, 'avocat', 'signature.preparer', {'connection': view['id'], 'matter': fixture.MID,
            'document': '/Dossiers/DEMO/Convention.pdf', 'title': 'Convention privée fictive',
            'signers': [{'first_name':'Alice', 'last_name':'Fictive', 'email':'alice@example.test'}]})
        denied = ''
        try:
            cx.validate(self.desk, 'autre@example.test', 'avocat', op['id'], op['version'])
        except Stop as ex:
            denied = str(ex)
        OBSERVATIONS['F05'] = {'refus': denied, 'etat': self.state(op)}
        self.assertTrue(denied, 'La validation doit vérifier le droit d’utiliser la connexion personnelle.')

class Routines(fixture.Base):
    form = fixture.Routines.form

    def test_F06_creation_cabinet_idempotente(self):
        data = self.form(scope='cabinet')
        first = rt.create(self.desk, ACTOR, 'avocat', data)
        error = ''; second = None
        try:
            second = rt.create(self.desk, ACTOR, 'avocat', data)
        except sqlite3.IntegrityError as ex:
            error = str(ex)
        OBSERVATIONS['F06'] = {'premiere': first['id'], 'seconde': second['id'] if second else None, 'erreur': error}
        self.assertFalse(error)
        self.assertEqual(first['id'], second['id'])

    def test_F07_pause_routine_empeche_demarrage_passage_en_file(self):
        row = rt.create(self.desk, ACTOR, 'avocat', self.form(freq='quotidien', time='08:00'))
        self.desk.setting('routines520:since', '2026-10-15T00:00:00+00:00')
        self.desk.db.execute('UPDATE routines5652 SET active_since=? WHERE id=?', ('2026-10-15T00:00:00+00:00', row['id']))
        self.desk.db.commit()
        rt.tick(self.desk, datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc))
        occ = self.desk.db.execute('SELECT * FROM routines5652_occ WHERE routine_id=?', (row['id'],)).fetchone()
        self.assertIsNotNone(occ)
        self.assertEqual(occ['manual'], 0)
        self.assertEqual(occ['state'], 'en_file')
        rt.set_enabled(self.desk, ACTOR, 'avocat', row['id'], False, row['version'])
        error = ''
        try:
            rt.start_occurrence(self.desk, occ['id'])
        except Stop as ex:
            error = str(ex)
        state = rt._occurrence(self.desk, occ['id'])['state']
        OBSERVATIONS['F07'] = {'routine_active': rt.get(self.desk, row['id'])['enabled'], 'passage': state, 'manuel': occ['manual'], 'refus': error}
        self.assertNotEqual(state, 'en_cours')

    def test_F08_reglages_retires_interdisent_config_connexion(self):
        path = '/api440/m5652/connexion/enregistrer'
        allowed = membres.allowed(self.desk.c['state_dir'], ACTOR, 'avocat', 'POST', path)
        OBSERVATIONS['F08'] = {'droit_reglages': membres.effective_rights(self.desk.c['state_dir'], ACTOR, 'avocat')['reglages'],
                               'categorie_route': membres.category('POST', path), 'route_autorisee': allowed}
        self.assertFalse(allowed)

if __name__ == '__main__':
    import sys
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    from pathlib import Path
    Path(__file__).with_name('observations-5652.json').write_text(json.dumps({'tests':result.testsRun,'echecs':len(result.failures),'erreurs':len(result.errors),
        'observations':OBSERVATIONS}, ensure_ascii=False, indent=2)+'\n')
    sys.exit(0 if result.wasSuccessful() else 1)
