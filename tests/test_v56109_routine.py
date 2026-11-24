"""5.6.109 (lot G4, G12) : routine « Relever mes messages procéduraux » tous les trois jours (type fermé ``collecte_portail`` de
``routines5652``, exécuteur ``collecte_portail56109.executer_routine``).

Cadence calendaire locale (changements d'heure), type fermé créé par le programme, pause prioritaire, un seul rattrapage utile, attente de
connexion sans boucle ni doublon, couverture explicite, aucune confirmation ni envoi par la routine, occurrences existantes conservées.
Faux navigateur, faux portail, fausse messagerie (socle de ``test_v56109_collecte``) ; aucun réseau, aucun portail réel."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from agent import collecte_portail56109 as C, routines5652 as rt
from agent.common import Stop
from test_v56109_collecte import Base, message

UTC = timezone.utc


class G12Cadence(unittest.TestCase):
    def test_G12_trois_jours_calendaires_et_passage_a_l_heure_d_ete(self):
        s = {'freq': 'intervalle', 'interval_days': 3, 'anchor': '2026-03-26', 'time': '02:30'}
        rows = rt.upcoming(s, 'Europe/Paris', datetime(2026, 3, 26, tzinfo=UTC), 3)
        self.assertEqual([(r['jour'], r['local']) for r in rows],
                         [('2026-03-26', '2026-03-26 02:30'), ('2026-03-29', '2026-03-29 03:00'), ('2026-04-01', '2026-04-01 02:30')])

    def test_G12_heure_repetee_une_seule_occurrence(self):
        s = {'freq': 'intervalle', 'interval_days': 3, 'anchor': '2026-10-22', 'time': '02:30'}
        rows = rt.upcoming(s, 'Europe/Paris', datetime(2026, 10, 24, tzinfo=UTC), 2)
        self.assertEqual([(r['jour'], r['utc']) for r in rows], [('2026-10-25', '2026-10-25T00:30:00+00:00'), ('2026-10-28', '2026-10-28T01:30:00+00:00')])

    def test_G12_exemple_du_cahier(self):
        s = {'freq': 'intervalle', 'interval_days': 3, 'anchor': '2026-11-22', 'time': '08:30'}
        rows = rt.upcoming(s, 'Europe/Paris', datetime(2026, 11, 22, 6, tzinfo=UTC), 4)
        self.assertEqual([r['jour'] for r in rows], ['2026-11-22', '2026-11-25', '2026-11-28', '2026-12-01'])


class G12Routine(Base):
    def setUp(self):
        super().setUp()
        rt.ensure_schema(self.desk)
        self.desk.setting('routines520:since', '2026-01-01T00:00:00+00:00')

    def routine(self, activer=True, anchor=None, time='08:30'):
        anchor = anchor or (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
        out = C.creer_routine(self.desk, 'avocat@example.test', 'avocat', {'profil': self.profil['id'], 'anchor': anchor, 'time': time, 'activer': activer})
        self.desk.db.execute("UPDATE routines5652 SET active_since='2026-01-01T00:00:00+00:00' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        return out

    def passage(self, rid, cle='clic-g12-0001'):
        occ = rt.run_now(self.desk, 'avocat@example.test', 'avocat', rid, cle)
        return rt.execute(self.desk, {'occurrence': occ['id']}), occ['id']

    def test_G12_type_ferme_cree_par_le_programme_seulement(self):
        self.assertIn('collecte_portail', rt.TYPES_FERMES)
        self.assertNotIn('collecte_portail', rt.TASK_TYPES)
        with self.assertRaisesRegex(Stop, 'type_routine_invalide'):
            rt.create(self.desk, 'avocat@example.test', 'avocat', {'title': 'Collecte libre', 'task_type': 'collecte_portail', 'freq': 'quotidien',
                                                                   'time': '08:30', 'request_key': 'g12-libre-0001'})
        out = self.routine()
        row = rt.get(self.desk, out['id'])
        perms = json.loads(row['permissions_json'])
        self.assertEqual((row['task_type'], row['enabled'], row['catchup_hours']), ('collecte_portail', 1, 72))
        self.assertEqual((perms['profil_portail_id'], perms['profil_version'], perms['effets_externes']), (self.profil['id'], self.profil['version'], False))
        self.assertEqual(json.loads(row['schedule_json'])['interval_days'], 3)
        self.assertEqual(len(out['prochains']), 3)
        listing = rt.listing(self.desk, 'avocat@example.test', 'avocat')['routines']
        from agent import web5652
        html = web5652.routines_block(self.desk, {'owner': 'avocat@example.test'}, '', 'avocat')
        self.assertIn('id="g4-collecte"', html)                          # carte propre (montage du lot G4) ; jamais le formulaire générique
        self.assertNotIn('id="r52-%s"' % out['id'], html)
        self.assertTrue(any(r['id'] == out['id'] for r in listing))

    def test_G12_profil_non_qualifie_routine_desactivee(self):
        p = C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'portail': 'ebarreau', 'compte': 'autre'})
        out = C.creer_routine(self.desk, 'avocat@example.test', 'avocat', {'profil': p['id'], 'anchor': '2026-11-22', 'activer': True})
        self.assertFalse(out['active'])
        self.assertEqual(rt.get(self.desk, out['id'])['enabled'], 0)

    def test_G12_pause_prioritaire_aucune_mission(self):
        out = self.routine(anchor=datetime.now(UTC).date().isoformat(), time='00:00')
        self.desk.setting('missions567:pause', True)
        rt.tick(self.desk, datetime.now(UTC))
        occ = self.desk.db.execute('SELECT state, error_code FROM routines5652_occ WHERE routine_id=?', (out['id'],)).fetchall()
        self.assertEqual([o['state'] for o in occ], ['reportee_pause'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM collecte_missions56109').fetchone()[0], 0)

    def test_G12_un_seul_rattrapage_utile_apres_absence(self):
        out = self.routine(anchor='2026-11-01', time='08:30')
        now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)          # passages dus : 1, 4, 7 … 19, 22 novembre ; absence de plusieurs semaines
        with patch.object(self.desk, 'enqueue', lambda *a, **k: 1), patch.object(self.desk, 'notify_queued', lambda *a, **k: None):
            rt.tick(self.desk, now)
            rt.tick(self.desk, now + timedelta(minutes=5))
        keys = [r['schedule_key'] for r in self.desk.db.execute('SELECT schedule_key FROM routines5652_occ WHERE routine_id=?', (out['id'],))]
        self.assertEqual(keys, ['2026-11-22 08:30'])                 # le plus récent seulement, une seule fois
        row = dict(rt.get(self.desk, out['id']))
        self.assertEqual(rt._due(row, datetime(2026, 12, 3, 12, 0, tzinfo=UTC))['key'], '2026-12-01 08:30')   # cadence de 3 jours : fenêtre de 72 h
        row['catchup_hours'] = 1                                                     # fenêtre réglée plus courte : un passage manqué n'est pas rattrapé
        self.assertIsNone(rt._due(row, datetime(2026, 12, 1, 14, 0, tzinfo=UTC)))

    def test_G12_attente_de_connexion_sans_boucle_ni_doublon(self):
        self.portail.connecte = False
        self.portail.messages = [message('m1')]
        out = self.routine()
        res, _ = self.passage(out['id'], 'clic-g12-0001')
        self.assertEqual(res['state'], 'partielle')
        self.assertTrue(any('connecter' in a['action'] for a in res['resultat']['a_decider']))
        res2, _ = self.passage(out['id'], 'clic-g12-0002')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM collecte_missions56109').fetchone()[0], 1)   # aucune mission en double
        self.assertTrue(any('aucune nouvelle mission' in x for x in res2['resultat']['impossible']))
        self.assertEqual(self.onglet.appels.count('text'), 1)                                                    # aucun nouvel essai de connexion

    def test_G12_routine_ne_depose_ne_confirme_ni_n_envoie(self):
        self.portail.messages = [message('m1', dates=[{'date': '2026-12-10', 'nature': 'audience', 'extrait': 'Audience du 10 décembre 2026'}])]
        out = self.routine()
        res, _ = self.passage(out['id'])
        self.assertEqual(res['state'], 'terminee', res)
        self.assertIn('non déposés', res['resultat']['produit'][0])
        self.assertEqual((self.boite.appends, self.boite.drafts), (0, []))
        self.assertEqual({r['etat'] for r in self.desk.db.execute('SELECT etat FROM collecte_brouillons56109')}, {'prepare'})
        self.assertEqual({r['etat'] for r in self.desk.db.execute('SELECT etat FROM collecte_dates56109')}, {'a_verifier'})

    def test_G12_couverture_partielle_annoncee(self):
        self.portail.messages = [message('m%d' % i) for i in range(4)]
        out = self.routine()
        with patch.object(C, 'MAX_PAGES', 1):
            res, _ = self.passage(out['id'])
        self.assertTrue(any('couverture partielle' in x and 'pas une boîte vide' in x for x in res['resultat']['impossible']))

    def test_G12_periode_non_couverte_signalee(self):
        quand = (datetime.now(UTC) - timedelta(days=20)).isoformat(timespec='seconds')
        self.desk.db.execute("INSERT INTO collecte_missions56109(id,acteur,cle,empreinte,profil_id,profil_version,portail,debut_utc,fin_utc,mode,etat,cree_le,maj_le) "
                             "VALUES('cm-ancienne','cabinet','cle-ancienne-1','x',?,?,'ebarreau',?,?,'preparer_et_collecter','terminee',?,?)",
                             (self.profil['id'], self.profil['version'], quand, quand, quand, quand))
        self.desk.db.commit()
        self.portail.messages = []
        out = self.routine()
        res, _ = self.passage(out['id'])
        self.assertTrue(any('période non couverte' in x for x in res['resultat']['impossible']))

    def test_G12_profil_modifie_depuis_la_programmation(self):
        out = self.routine()
        C.enregistrer_profil(self.desk, 'admin@example.test', 'administrateur', {'id': self.profil['id'], 'portail': 'ebarreau', 'compte': 'avocat-A',
                                                                                'url_entree': 'https://portail.example.test/'})
        res, _ = self.passage(out['id'])
        self.assertTrue(any('profil de portail modifié' in x for x in res['resultat']['impossible']))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM collecte_missions56109').fetchone()[0], 0)

    def test_G12_occurrences_existantes_conservees(self):
        self.portail.messages = [message('m1')]
        out = self.routine()
        _, oid = self.passage(out['id'])
        C.creer_routine(self.desk, 'avocat@example.test', 'avocat', {'profil': self.profil['id'], 'anchor': '2026-11-22', 'time': '09:15', 'activer': True})
        occ = self.desk.db.execute('SELECT state FROM routines5652_occ WHERE id=?', (oid,)).fetchone()
        self.assertEqual(occ['state'], 'terminee')
        self.assertEqual(json.loads(rt.get(self.desk, out['id'])['schedule_json'])['time'], '09:15')


if __name__ == '__main__':
    unittest.main()
