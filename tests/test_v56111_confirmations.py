"""5.6.111 (lot C, C03) : confirmations par code bornées et atomiques (garde commune ``confirmations56111``) sur les huit parcours.

Données fictives (DOS-001, DURAND), aucun réseau : stockage Nextcloud simulé, horloge de la garde remplacée, deux connexions SQLite réelles
pour les validations concurrentes."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_desk  # noqa: F401 — même préparation des essais que les modules historiques
from test_v330 import CabinetDAV, FIXTURE, fake_pages

from agent import cabinet_docs33, cabinet_pilot, confirmations56111 as garde, membres5650, supervision, workplan
from agent.common import Stop, digest
from agent.desk import Desk
from agent.erreurs5634 import detail_of, explain


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.model.intent = 'legal'
        self.key = self.f.engine.process(fixtures.mail())
        self.d = Desk(self.f.c)
        self.avant = {k: self.compter(k) for k in ('prepare_reply', 'apply_work_plan')}   # le courriel fictif traité en crée déjà
        membres5650.set_author('')
        membres5650.set_viewer(frozenset())
        self.addCleanup(membres5650.set_author, '')
        self.addCleanup(membres5650.set_viewer, frozenset())

    def proposer(self):
        return supervision.propose(self.d, {'action': 'prepare_reply', 'mail_key': self.key, 'instruction': 'Répondre à DURAND.'})

    def approuver(self, item, code):
        return supervision.approve(self.d, {'approval_id': item['approval_id'], 'confirmation_code': code})

    def faux(self, code):
        return '%06d' % ((int(code) + 1) % 1_000_000)

    def compter(self, kind):
        return self.d.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (kind,)).fetchone()[0]

    def jobs(self, kind='prepare_reply'):
        """Travaux de ce type mis en file depuis le début de l'essai."""
        return self.compter(kind) - self.avant.get(kind, 0)


class EssaisBornes(Base):
    def test_code_malforme_consomme_un_essai_et_message_lisible(self):
        item = self.proposer()
        with self.assertRaisesRegex(Stop, 'confirmation_supervisee_invalide') as ctx:
            self.approuver(item, 'abc')
        self.assertEqual(detail_of(ctx.exception)['essais_restants56111'], 4)
        self.assertIn('Il reste 4 essai(s)', explain(str(ctx.exception), detail_of(ctx.exception)))
        self.assertEqual(garde.etat(self.d, 'supervision', item['approval_id'])['essais'], 1)
        with self.assertRaisesRegex(Stop, 'confirmation_supervisee_invalide'):   # identifiant malformé : refus sans toucher au défi
            supervision.approve(self.d, {'approval_id': 'z' * 32, 'confirmation_code': item['confirmation_code']})
        self.assertEqual(self.approuver(item, item['confirmation_code'])['status'], 'approved')

    def test_cinquieme_essai_juste_ouvre_l_effet(self):
        item = self.proposer()
        for restant in (4, 3, 2, 1):
            with self.assertRaises(Stop) as ctx:
                self.approuver(item, self.faux(item['confirmation_code']))
            self.assertEqual(detail_of(ctx.exception)['essais_restants56111'], restant)
        out = self.approuver(item, item['confirmation_code'])
        self.assertEqual(out['status'], 'approved')
        self.assertEqual(self.jobs(), 1)

    def test_cinquieme_essai_faux_bloque_et_le_bon_code_n_ouvre_plus_rien(self):
        item = self.proposer()
        for _ in range(4):
            with self.assertRaisesRegex(Stop, 'confirmation_supervisee_invalide'):
                self.approuver(item, self.faux(item['confirmation_code']))
        with self.assertRaisesRegex(Stop, '^confirmation_bloquee$'):
            self.approuver(item, self.faux(item['confirmation_code']))
        with self.assertRaisesRegex(Stop, '^confirmation_bloquee$'):
            self.approuver(item, item['confirmation_code'])
        self.assertEqual(self.jobs(), 0)
        self.assertEqual(garde.etat(self.d, 'supervision', item['approval_id'])['etat'], 'bloque')
        self.assertIn('recommencez', explain('confirmation_bloquee'))

    def test_expiration_exacte(self):
        item = self.proposer()
        fin = datetime.fromisoformat(garde.etat(self.d, 'supervision', item['approval_id'])['expire'])
        with patch.object(garde, '_maintenant', return_value=fin):
            with self.assertRaisesRegex(Stop, '^confirmation_supervisee_expiree$'):
                self.approuver(item, item['confirmation_code'])
        self.assertEqual(self.d.db.execute('SELECT status FROM supervision_requests WHERE id=?', (item['approval_id'],)).fetchone()[0], 'expired')
        self.assertEqual(self.jobs(), 0)
        item2 = self.proposer()
        fin2 = datetime.fromisoformat(garde.etat(self.d, 'supervision', item2['approval_id'])['expire'])
        with patch.object(garde, '_maintenant', return_value=fin2 - timedelta(microseconds=1)):
            self.assertEqual(self.approuver(item2, item2['confirmation_code'])['status'], 'approved')

    def test_deux_validations_concurrentes_deux_connexions_reelles(self):
        item = self.proposer()
        depart = threading.Barrier(2)
        resultats = []

        def valider():
            d = Desk(self.f.c)   # connexion SQLite distincte
            try:
                depart.wait(5)
                resultats.append(('ok', self.approuver_sur(d, item)))
            except Stop as ex:
                resultats.append(('refus', str(ex)))
            finally:
                d.db.close()
        fils = [threading.Thread(target=valider) for _ in range(2)]
        for t in fils:
            t.start()
        for t in fils:
            t.join(30)
        self.assertEqual(sorted(r[0] for r in resultats), ['ok', 'refus'], resultats)
        self.assertIn(next(r[1] for r in resultats if r[0] == 'refus'), ('demande_supervisee_deja_traitee',))
        self.assertEqual(self.jobs(), 1)

    def approuver_sur(self, d, item):
        return supervision.approve(d, {'approval_id': item['approval_id'], 'confirmation_code': item['confirmation_code']})

    def test_droit_retire_et_autre_personne_sans_essai_decompte(self):
        membres5650.set_author('durand')
        item = self.proposer()
        membres5650.set_author('martin')
        with self.assertRaisesRegex(Stop, '^confirmation_autre_utilisateur$'):
            self.approuver(item, item['confirmation_code'])
        self.assertEqual(garde.etat(self.d, 'supervision', item['approval_id'])['essais'], 0)
        membres5650.set_author('durand')
        self.assertEqual(self.approuver(item, item['confirmation_code'])['status'], 'approved')
        self.assertEqual(garde.etat(self.d, 'supervision', item['approval_id'])['confirme_par'], 'durand')

    def test_demande_d_avant_la_5_6_111_reste_confirmable_puis_suit_la_regle(self):
        rid, code = 'a' * 32, '123456'
        stamp = datetime.now(timezone.utc)
        payload = json.dumps({'key': self.key, 'instruction': 'Répondre.'}, ensure_ascii=False)
        self.d.db.execute('INSERT INTO supervision_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (rid, 'prepare_reply', payload, 'Résumé fictif', digest(rid + '|' + code), 'pending', stamp.isoformat(),
                           (stamp + timedelta(days=30)).isoformat(), '', None, ''))
        self.d.db.commit()
        for _ in range(4):
            with self.assertRaisesRegex(Stop, 'confirmation_supervisee_invalide'):
                supervision.approve(self.d, {'approval_id': rid, 'confirmation_code': '000000'})
        row = garde.etat(self.d, 'supervision', rid)
        self.assertEqual((row['origine'], row['essais']), ('ancienne', 4))
        self.assertLessEqual(datetime.fromisoformat(row['expire']), datetime.now(timezone.utc) + timedelta(hours=24, minutes=1))
        self.assertEqual(supervision.approve(self.d, {'approval_id': rid, 'confirmation_code': code})['status'], 'approved')
        old = 'b' * 32
        self.d.db.execute('INSERT INTO supervision_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (old, 'prepare_reply', payload, 'Résumé fictif', digest(old + '|' + code), 'pending', stamp.isoformat(),
                           (stamp - timedelta(minutes=1)).isoformat(), '', None, ''))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, '^confirmation_supervisee_expiree$'):
            supervision.approve(self.d, {'approval_id': old, 'confirmation_code': code})

    def test_le_code_n_est_jamais_conserve_en_clair(self):
        item = self.proposer()
        row = self.d.db.execute('SELECT challenge_hash FROM supervision_requests WHERE id=?', (item['approval_id'],)).fetchone()[0]
        self.assertTrue(row.startswith(garde.MARQUE))
        dump = json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM confirmations56111')])
        self.assertNotIn(item['confirmation_code'], dump.replace(item['approval_id'], ''))


class RepriseApresValidation(Base):
    """Arrêt après validation et reprise : même clé (parcours, objet), même travail ; jamais deux exécutions simultanées."""

    def travail(self, status):
        cur = self.d.db.execute("INSERT INTO jobs(kind,args,status,created,priority,origin) VALUES('create_document_files','{}',?,?,0,'user')",
                                (status, self.d.now()))
        self.d.db.commit()
        return cur.lastrowid

    def presenter(self, code, commence=False):
        return garde.confirmer(self.d, 'essai', 'objet1', code, version='v1', effet='e1', code_faux='faux', code_expire='expire',
                               code_deja='deja', reprise=True, effet_commence=commence)

    def test_reprise_par_le_meme_travail_et_refus_pendant_un_autre(self):
        defi = garde.emettre(self.d, 'essai', 'objet1', 'v1', 'e1', 30)
        self.d.db.commit()
        j1 = self.travail('running')
        self.d.active_job_id = j1
        self.addCleanup(setattr, self.d, 'active_job_id', None)
        self.assertFalse(self.presenter(defi['code'])['reprise'])   # consommé ; « arrêt » juste après la validation
        self.assertEqual(self.presenter(defi['code'])['reprise'], True)   # même travail relancé : reprise par la même clé
        j2 = self.travail('running')
        self.d.active_job_id = j2
        with self.assertRaisesRegex(Stop, '^confirmation_deja_en_cours$'):
            self.presenter(defi['code'])
        self.d.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (j1,))
        self.d.db.commit()
        self.assertTrue(self.presenter(defi['code'])['reprise'])   # premier travail arrêté : reprise sans nouvel essai
        self.assertEqual(garde.etat(self.d, 'essai', 'objet1')['essais'], 1)
        with self.assertRaisesRegex(Stop, '^deja$'):
            self.presenter(self.faux(defi['code']))

    def test_confirmation_directe_consommee_n_est_pas_rejouee_avant_le_debut_de_l_effet(self):
        defi = garde.emettre(self.d, 'essai', 'objet1', 'v1', 'e1', 30)
        self.d.db.commit()
        self.presenter(defi['code'])
        with self.assertRaisesRegex(Stop, '^deja$'):
            self.presenter(defi['code'])                 # seconde validation simultanée : refusée
        self.assertTrue(self.presenter(defi['code'], commence=True)['reprise'])   # effet commencé puis interrompu : reprise

    def test_intention_refusee_ne_consomme_rien(self):
        defi = garde.emettre(self.d, 'essai', 'objet1', 'v1', 'e1', 30)
        self.d.db.commit()

        def refus(db):
            db.execute("UPDATE settings SET value=value WHERE key='inexistante'")
            raise Stop('file_attente_pleine')
        with self.assertRaisesRegex(Stop, 'file_attente_pleine'):
            garde.confirmer(self.d, 'essai', 'objet1', defi['code'], version='v1', effet='e1', code_faux='faux', code_expire='expire',
                            code_deja='deja', intention=refus)
        row = garde.etat(self.d, 'essai', 'objet1')
        self.assertEqual((row['etat'], row['essais']), ('attente', 0))
        self.assertFalse(self.d.db.in_transaction)


class Programme(Base):
    def setUp(self):
        super().setUp()
        self.f.c['planning'] = {'timezone': 'Europe/Paris', 'weekdays': [0, 1, 2, 3, 4], 'working_hours': [['09:00', '12:00'], ['14:00', '18:00']]}
        self.f.c['nextcloud_workflow'] = {'enabled': True, 'url': 'https://cloud.test', 'roots': ['/Dossiers'],
                                          'task_calendar_url': 'https://cloud.test/calendars/tech/tasks/',
                                          'planning_calendar_url': 'https://cloud.test/calendars/tech/planning/'}
        from test_v211 import FakeWorkflowDAV
        self.d = Desk(self.f.c)
        self.plan = workplan.propose_plan(self.d, {'task_text': '- DOS-001 : préparer conclusions DURAND (2h)',
                                                   'period_start': '2026-09-14T00:00:00+02:00', 'period_end': '2026-09-19T00:00:00+02:00'},
                                          dav=FakeWorkflowDAV())

    def test_objet_modifie_entre_apercu_et_code(self):
        pid = self.plan['proposal_id']
        data = json.loads(self.d.db.execute('SELECT data FROM work_plan_proposals WHERE id=?', (pid,)).fetchone()[0])
        data['tasks'][0]['title'] = 'Tâche ajoutée après l’aperçu'
        self.d.db.execute('UPDATE work_plan_proposals SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), pid))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, '^objet_modifie_depuis_apercu$'):
            workplan.approve_plan(self.d, pid, self.plan['confirmation_code'])
        with self.assertRaisesRegex(Stop, 'objet_modifie_depuis_apercu'):   # le défi est périmé : le bon code n'ouvre plus rien
            workplan.approve_plan(self.d, pid, self.plan['confirmation_code'])
        self.assertEqual(self.jobs('apply_work_plan'), 0)

    def test_espaces_non_configures_le_code_reste_valable(self):
        self.f.c['nextcloud_workflow']['enabled'] = False
        with self.assertRaisesRegex(Stop, 'espaces_nextcloud_axiorhub_non_configures'):
            workplan.approve_plan(self.d, self.plan['proposal_id'], self.plan['confirmation_code'])
        self.f.c['nextcloud_workflow']['enabled'] = True
        out = workplan.approve_plan(self.d, self.plan['proposal_id'], self.plan['confirmation_code'])
        self.assertEqual(out['status'], 'approved')
        self.assertEqual(self.jobs('apply_work_plan'), 1)


class LotsDuPilote(Base):
    def decision(self, n, risk='low'):
        did = hashlib.sha256(('decision-%d' % n).encode()).hexdigest()
        self.d.db.execute('INSERT INTO cabinet_decisions_v290 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (did, 'essai', str(n), 'DOS-001', risk, 'alert', 'Alerte fictive DURAND', 'Résumé', '{}', 'fp-%d' % n,
                           'pending', '', '', self.d.now(), self.d.now()))
        self.d.db.commit()
        return did

    def setUp(self):
        super().setUp()
        cabinet_pilot.ensure_schema(self.d)
        self.ids = [self.decision(1), self.decision(2)]
        self.lot = cabinet_pilot.create_batch(self.d, {'decision_ids': self.ids})

    def test_lot_modifie_entre_apercu_et_code(self):
        self.d.db.execute("UPDATE cabinet_decisions_v290 SET fingerprint='fp-autre' WHERE id=?", (self.ids[1],))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, '^lot_modifie_depuis_previsualisation$'):
            cabinet_pilot.approve_batch(self.d, {'batch_id': self.lot['batch_id'], 'confirmation_code': self.lot['confirmation_code']})
        statuts = {r[0] for r in self.d.db.execute('SELECT status FROM cabinet_decisions_v290')}
        self.assertEqual(statuts, {'pending'})

    def test_lot_confirme_une_seule_fois_et_code_malforme_decompte(self):
        with self.assertRaisesRegex(Stop, '^confirmation_lot_invalide$'):
            cabinet_pilot.approve_batch(self.d, {'batch_id': self.lot['batch_id'], 'confirmation_code': '12'})
        out = cabinet_pilot.approve_batch(self.d, {'batch_id': self.lot['batch_id'], 'confirmation_code': self.lot['confirmation_code']})
        self.assertEqual((out['status'], out['count']), ('approved', 2))
        with self.assertRaisesRegex(Stop, 'lot_absent_ou_deja_traite'):
            cabinet_pilot.approve_batch(self.d, {'batch_id': self.lot['batch_id'], 'confirmation_code': self.lot['confirmation_code']})
        self.assertEqual(garde.etat(self.d, 'cabinet_pilot', self.lot['batch_id'])['essais'], 2)


class CourriersSurModele(Base):
    """Création d'un fichier Nextcloud : aucun appel au stockage pendant le verrou SQLite ; droit retiré ; validation de modèle atomique."""

    def setUp(self):
        super().setUp()
        self.dav = CabinetDAV()
        test = self

        def sans_transaction(nom):
            original = getattr(CabinetDAV, nom)

            def appel(dav, *a, **k):
                test.assertFalse(test.d.db.in_transaction, 'appel au stockage (%s) pendant une transaction SQLite' % nom)
                return original(dav, *a, **k)
            return appel
        for nom in ('list_folder', 'download', 'ensure_folder', 'put_file'):
            setattr(self.dav, nom, sans_transaction(nom).__get__(self.dav))
        record = cabinet_docs33.import_template(self.d, FIXTURE, 'Lettre du cabinet.docx', fake_pages)
        self.tid = record['template_id']

    def args(self):
        return {'matter': 'DOS-001', 'template_id': self.tid, 'date': '2026-09-20', 'destinataire': 'Madame DURAND', 'qualite': 'Gérante',
                'adresse': '1 rue Fictive', 'reference': 'DOS-001', 'objet': 'Projet fictif', 'envoi': 'courriel', 'appel': 'madame',
                'corps': 'Premier passage.', 'fin': 'formelle', 'signature': 'Maître MARTIN'}

    def test_validation_du_modele_atomique_entre_deux_connexions(self):
        sha = hashlib.sha256(FIXTURE).hexdigest()
        depart = threading.Barrier(2)
        resultats = []

        def valider():
            d = Desk(self.f.c)
            try:
                depart.wait(5)
                cabinet_docs33.approve_template(d, self.tid, sha, 'yes')
                resultats.append('ok')
            except Stop as ex:
                resultats.append(str(ex))
            finally:
                d.db.close()
        fils = [threading.Thread(target=valider) for _ in range(2)]
        for t in fils:
            t.start()
        for t in fils:
            t.join(30)
        self.assertEqual(sorted(resultats), ['modele_word_deja_decide', 'ok'], resultats)
        audits = self.d.db.execute("SELECT COUNT(*) FROM audit WHERE action='cabinet_template_approved'").fetchone()[0]
        self.assertEqual(audits, 1)

    def test_creation_reprise_sans_second_essai_et_sans_reseau_sous_verrou(self):
        cabinet_docs33.approve_template(self.d, self.tid, hashlib.sha256(FIXTURE).hexdigest(), 'yes')
        prepared = cabinet_docs33.prepare(self.d, self.args(), self.dav, fake_pages)
        arguments = {'project_id': prepared['project_id'], 'confirmation_code': prepared['confirmation_code'],
                     'destination': prepared['destination']}
        membres5650.set_viewer(frozenset({'DOS-001'}))   # droit retiré entre l'aperçu et le code
        with self.assertRaisesRegex(Stop, '^dossier_reserve$'):
            cabinet_docs33.confirm(self.d, arguments, self.dav)
        membres5650.set_viewer(frozenset())
        self.assertEqual(garde.etat(self.d, 'cabinet_docs33', prepared['project_id'])['essais'], 0)
        self.dav.fail_once = True
        self.assertEqual(cabinet_docs33.confirm(self.d, arguments, self.dav)['status'], 'partial')
        self.assertEqual(cabinet_docs33.confirm(self.d, arguments, self.dav)['status'], 'done')
        self.assertEqual(len(self.dav.puts), 1)
        self.assertEqual(garde.etat(self.d, 'cabinet_docs33', prepared['project_id'])['essais'], 1)


if __name__ == '__main__':
    unittest.main()
