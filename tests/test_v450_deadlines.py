"""Jeu de cas vérifiés à la main pour le moteur de délais 4.5.0 (CPC art. 640 à 643)."""
from datetime import date
import unittest

from agent import deadlines450 as dl

# (règle, départ, distance, alsace, date de régime, échéance attendue, commentaire du calcul manuel)
CASES = [
    ('appel_jugement_contentieux', '2026-03-10', 'none', False, None, '2026-04-10', '10 mars + 1 mois = vendredi 10 avril'),
    ('appel_jugement_contentieux', '2026-01-31', 'none', False, None, '2026-03-02', '31 janv. -> 28 févr. (samedi) -> lundi 2 mars'),
    ('appel_jugement_contentieux', '2024-01-31', 'none', False, None, '2024-02-29', 'année bissextile : 29 février (jeudi)'),
    ('appel_jugement_contentieux', '2023-01-31', 'none', False, None, '2023-02-28', '28 février 2023 (mardi)'),
    ('appel_jugement_contentieux', '2025-12-31', 'none', False, None, '2026-02-02', '31 janv. 2026 samedi -> lundi 2 févr.'),
    ('appel_jugement_contentieux', '2026-04-03', 'none', False, None, '2026-05-04', '3 mai dimanche -> lundi 4'),
    ('appel_jugement_contentieux', '2026-04-08', 'none', False, None, '2026-05-11', '8 mai férié (vendredi) -> samedi, dimanche -> lundi 11'),
    ('appel_jugement_contentieux', '2026-04-14', 'none', False, None, '2026-05-15', '14 mai 2026 Ascension -> vendredi 15'),
    ('appel_jugement_contentieux', '2026-04-25', 'none', False, None, '2026-05-26', '25 mai lundi de Pentecôte -> mardi 26'),
    ('appel_jugement_contentieux', '2026-06-30', 'none', False, None, '2026-07-30', 'jeudi'),
    ('appel_jugement_contentieux', '2026-07-14', 'none', False, None, '2026-08-14', 'vendredi (le 15 est un samedi)'),
    ('appel_jugement_contentieux', '2026-07-15', 'none', False, None, '2026-08-17', '15 août samedi -> lundi 17'),
    ('appel_jugement_contentieux', '2026-10-01', 'none', False, None, '2026-11-02', '1er nov. dimanche (Toussaint) -> lundi 2'),
    ('appel_jugement_contentieux', '2026-10-11', 'none', False, None, '2026-11-12', '11 nov. mercredi férié -> jeudi 12'),
    ('appel_jugement_contentieux', '2026-11-25', 'none', False, None, '2026-12-28', '25 déc. vendredi férié, samedi, dimanche -> lundi 28'),
    ('appel_jugement_contentieux', '2026-12-01', 'none', False, None, '2027-01-04', '1er janv. 2027 vendredi férié -> lundi 4'),
    ('appel_jugement_contentieux', '2026-12-26', 'none', False, None, '2027-01-26', 'mardi'),
    ('appel_jugement_contentieux', '2025-11-26', 'none', False, None, '2025-12-26', 'vendredi ouvrable hors Alsace-Moselle'),
    ('appel_jugement_contentieux', '2025-11-26', 'none', True, None, '2025-12-29', 'Alsace-Moselle : 26 déc. férié -> lundi 29'),
    ('appel_jugement_contentieux', '2026-03-03', 'none', False, None, '2026-04-03', 'vendredi saint ouvrable hors Alsace-Moselle'),
    ('appel_jugement_contentieux', '2026-03-03', 'none', True, None, '2026-04-07', 'Alsace-Moselle : 3 avr., sam., dim., lundi de Pâques -> mardi 7'),
    ('appel_jugement_contentieux', '2026-10-10', 'none', False, None, '2026-11-10', 'changement d’heure du 25 oct. sans effet sur les dates'),
    ('appel_jugement_gracieux', '2026-03-10', 'none', False, None, '2026-03-25', '10 + 15 jours = mercredi 25'),
    ('appel_jugement_gracieux', '2026-03-15', 'none', False, None, '2026-03-30', 'changement d’heure du 29 mars sans effet'),
    ('appel_ordonnance_refere', '2026-03-20', 'none', False, None, '2026-04-07', '4 avr. sam., 5 dim., 6 lundi de Pâques -> mardi 7'),
    ('appel_ordonnance_refere', '2026-12-10', 'none', False, None, '2026-12-28', '25 déc. férié -> lundi 28'),
    ('appel_ordonnance_refere', '2026-04-26', 'none', False, None, '2026-05-11', '11 mai lundi'),
    ('appel_ordonnance_refere', '2026-04-23', 'none', False, None, '2026-05-11', '8 mai férié -> lundi 11'),
    ('appel_ordonnance_refere', '2026-12-20', 'none', False, None, '2027-01-04', '20 déc. + 15 = 4 janv. lundi'),
    ('appel_ordonnance_refere', '2028-02-20', 'none', False, None, '2028-03-06', 'bissextile : 20 févr. + 9 = 29 févr., + 6 = 6 mars'),
    ('appel_ordonnance_refere', '2027-02-20', 'none', False, None, '2027-03-08', '7 mars 2027 dimanche -> lundi 8'),
    ('pourvoi_cassation', '2026-03-31', 'none', False, None, '2026-06-01', '31 mai dimanche -> lundi 1er juin'),
    ('pourvoi_cassation', '2026-12-31', 'none', False, None, '2027-03-01', '28 févr. 2027 dimanche -> lundi 1er mars'),
    ('pourvoi_cassation', '2023-12-31', 'none', False, None, '2024-02-29', 'bissextile : 29 février 2024 jeudi'),
    ('pourvoi_cassation', '2026-08-30', 'none', False, None, '2026-10-30', 'vendredi'),
    ('opposition_jugement_defaut', '2026-09-30', 'none', False, None, '2026-10-30', 'vendredi'),
    ('appel_jugement_contentieux', '2026-03-10', 'outre_mer', False, None, '2026-05-11', '10 avr. + 1 mois = dim. 10 mai -> lundi 11'),
    ('appel_jugement_contentieux', '2026-03-10', 'etranger', False, None, '2026-06-10', '10 avr. + 2 mois = mercredi 10 juin'),
    ('pourvoi_cassation', '2026-01-15', 'etranger', False, None, '2026-05-15', '15 mars + 2 mois = vendredi 15 mai'),
    ('appel_ordonnance_refere', '2026-01-20', 'outre_mer', False, None, '2026-03-04', '4 févr. + 1 mois = mercredi 4 mars'),
    ('constitution_intime', '2025-02-03', 'outre_mer', False, '2025-01-10', '2025-02-18', 'distance non applicable : mardi 18 févr.'),
    ('conclusions_appelant', '2025-03-31', 'none', False, '2025-03-31', '2025-06-30', 'lundi 30 juin'),
    ('conclusions_appelant', '2025-05-30', 'none', False, '2025-05-30', '2025-09-01', '30 août samedi -> lundi 1er sept.'),
    ('conclusions_appelant', '2025-11-30', 'none', False, '2025-11-30', '2026-03-02', '28 févr. 2026 samedi -> lundi 2 mars'),
    ('conclusions_appelant', '2025-10-08', 'none', False, '2025-10-08', '2026-01-08', 'jeudi'),
    ('conclusions_intime', '2025-12-10', 'none', False, '2025-10-08', '2026-03-10', 'mardi'),
    ('appel_incident_intime', '2026-02-11', 'none', False, '2026-01-05', '2026-05-11', 'lundi'),
    ('bref_delai_appelant', '2026-02-27', 'none', False, '2026-02-01', '2026-04-27', 'lundi'),
    ('bref_delai_intime', '2026-01-08', 'none', False, '2025-12-01', '2026-03-09', '8 mars dimanche -> lundi 9'),
    ('constitution_intime', '2026-04-20', 'none', False, '2026-04-01', '2026-05-05', 'mardi 5 mai'),
    ('signification_da_appelant', '2026-04-08', 'none', False, '2026-04-01', '2026-05-11', '8 mai férié -> lundi 11'),
]


class DeadlineCases(unittest.TestCase):
    def test_at_least_forty_cases_pass(self):
        self.assertGreaterEqual(len(CASES), 40)
        for rule, start, distance, alsace, regime, expected, why in CASES:
            with self.subTest(rule=rule, start=start, distance=distance, alsace=alsace):
                result = dl.compute(rule, start, distance=distance, alsace=alsace, regime_date=regime)
                self.assertEqual(result['due'], expected, why)
                self.assertTrue(result['steps'])
                self.assertTrue(result['articles'])
                self.assertEqual(result['steps'][-1], 'Échéance : %s.' % dl.fr_date(date.fromisoformat(expected)))

    def test_prorogation_reason_is_reported(self):
        result = dl.compute('appel_jugement_contentieux', '2026-04-25')
        self.assertEqual(result['raw_end'], '2026-05-25')
        self.assertEqual(result['prorogation'], 'Lundi de Pentecôte')
        self.assertTrue(any('art. 642' in s for s in result['steps']))
        self.assertEqual(dl.compute('appel_jugement_contentieux', '2026-03-10')['prorogation'], '')

    def test_distance_days_rule_carries_a_warning(self):
        result = dl.compute('appel_ordonnance_refere', '2026-01-20', distance='outre_mer')
        self.assertTrue(any('à confirmer' in w for w in result['warnings']))
        self.assertEqual(result['distance_months'], 1)

    def test_easter_and_holidays(self):
        self.assertEqual(dl.easter(2024), date(2024, 3, 31))
        self.assertEqual(dl.easter(2025), date(2025, 4, 20))
        self.assertEqual(dl.easter(2026), date(2026, 4, 5))
        self.assertEqual(dl.easter(2027), date(2027, 3, 28))
        self.assertEqual(len(dl.holidays(2026)), 11)
        self.assertEqual(len(dl.holidays(2026, alsace=True)), 13)
        self.assertEqual(dl.holidays(2025)[date(2025, 5, 29)], 'Ascension')
        self.assertEqual(dl.holidays(2025)[date(2025, 6, 9)], 'Lundi de Pentecôte')

    def test_add_months_edges(self):
        self.assertEqual(dl.add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertEqual(dl.add_months(date(2026, 8, 31), 1), date(2026, 9, 30))
        self.assertEqual(dl.add_months(date(2026, 11, 30), 3), date(2027, 2, 28))
        self.assertEqual(dl.add_months(date(2026, 12, 15), 2), date(2027, 2, 15))

    def test_out_of_scope_and_missing_inputs_are_refused(self):
        with self.assertRaises(dl.DeadlineError) as ctx:
            dl.compute('conclusions_appelant', '2024-05-02', regime_date='2024-04-02')
        self.assertEqual(ctx.exception.code, 'hors_perimetre')
        with self.assertRaises(dl.DeadlineError) as ctx:
            dl.compute('conclusions_appelant', '2025-05-02')
        self.assertEqual(ctx.exception.code, 'date_regime_requise')
        with self.assertRaises(dl.DeadlineError) as ctx:
            dl.compute('appel_jugement_contentieux', '2026-05-02', scope='prud_hommes')
        self.assertEqual(ctx.exception.code, 'hors_perimetre')
        with self.assertRaises(dl.DeadlineError) as ctx:
            dl.compute('appel_jugement_contentieux', '2026-13-45')
        self.assertEqual(ctx.exception.code, 'date_invalide')
        with self.assertRaises(dl.DeadlineError) as ctx:
            dl.compute('inconnue', '2026-05-02')
        self.assertEqual(ctx.exception.code, 'regle_inconnue')
        with self.assertRaises(dl.DeadlineError):
            dl.compute('appel_jugement_contentieux', '2026-05-02', distance='lune')

    def test_every_rule_has_article_event_and_unit(self):
        for item in dl.rules():
            self.assertTrue(item['articles'])
            self.assertIn(item['unit'], ('jours', 'mois'))
            self.assertIn(item['start_event'], dl.load()['start_events'])
            self.assertTrue(item['valid_from'])


if __name__ == '__main__':
    unittest.main()
