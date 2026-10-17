"""5.6.58 (correctif) : file unique robuste, note des modèles de facture masquée, échéances distinguées. Données fictives."""
import unittest
from unittest.mock import patch

import test_v5652 as base
from agent import file5651, modeles5653 as models, parcours5657 as p57


class Queue58(base.Base):
    def test_proposition_de_facturation_ne_fait_plus_disparaitre_la_file(self):
        item = {'class': 'decision', 'object': 'proposition:k', 'revision': 'r1', 'matter': '', 'matter_name': 'Cabinet', 'title': 'Temps validés non facturés',
                'consequence': 'Préparer la facture', 'href': '/p/facturation', 'choices': ('ouvrir',)}
        odd = dict(item, **{'class': 'inconnue', 'object': 'autre:k', 'choices': ('ouvrir', 'choix_inconnu')})
        with patch('agent.facturation5653.queue_items', return_value=[item, odd]):
            html = file5651.html(self.desk, '/p')
        self.assertIn('Proposition à décider', html)
        self.assertIn('Temps validés non facturés', html)
        self.assertIn('À examiner', html)
        self.assertIn('decision', file5651.FILTERS['decider'])


class Templates58(unittest.TestCase):
    def test_note_du_modele_absente_de_l_apercu(self):
        for t in [x for x in models._catalog() if x['kind'] == 'facture']:
            text, _ = models.render_text(t, {})
            self.assertNotIn('Variables résolues par le serveur', text, t['id'])
            self.assertNotIn('Version de travail', text, t['id'])


class Kinds58(unittest.TestCase):
    def test_echeance_audience_rendez_vous(self):
        self.assertEqual(p57.event_kind('Échéance : Dépôt des conclusions en réponse'), 'echeance')
        self.assertEqual(p57.event_kind('Audience de plaidoirie'), 'audience')
        self.assertEqual(p57.event_kind('Rendez-vous client'), 'rendez_vous')


if __name__ == '__main__':
    unittest.main()
