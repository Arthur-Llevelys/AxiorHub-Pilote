"""5.6.55 : page « Facturation et honoraires » refondue (dossiers clients seulement, tableau de bord, « À faire ») et actualisation
des données du dossier sans blocage du travailleur (régression des brouillons automatiques). Données fictives."""
import unittest
from unittest.mock import patch

import test_v5652 as base
import test_v5653 as t53
from agent import facturation5653 as fact, facturation5655 as f55, modele5651, contexte5651
from agent.common import Stop

ACTOR, MID = t53.ACTOR, t53.MID


class Scope(unittest.TestCase):
    def kind(self, name, roles=()):
        class D:
            c = {'state_dir': '.'}

            def settings(self, key, default=None):
                return default
        m = {'id': 'X1', 'path': '/Cabinet/' + name, 'client_name': name, 'correspondents': [{'role': r, 'email': 'a@example.test'} for r in roles]}
        return f55.classify(D(), m, {'exclus': {}, 'inclus': {}}, {})[0]

    def test_dossiers_internes_reconnus(self):
        for name in ('00 - Bibliothèque Cabinet', '01 - Dossiers', '03 - Modèles', '09 - Scanner', '10 - IA', '06 - Collaboration - Stage',
                     'Comptabilité', 'Modèles du cabinet'):
            self.assertEqual(self.kind(name), 'interne', name)
        for name in ('ALPHA - SAS EXEMPLE - CONSEIL - 20240101001', 'SOCIETE FORMATION PLUS - CONTENTIEUX - 2025000123', 'DUPONT / MARTIN'):
            self.assertEqual(self.kind(name), 'client', name)
        self.assertEqual(self.kind('03 - Modèles', ('client',)), 'client')        # un correspondant client l'emporte


class Billing55(base.Base):
    def setUp(self):
        base.Base.setUp(self)
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', t53.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, t53.CLIENT)

    def test_dossiers_existants_pas_de_convention_proposee(self):
        f55.note_seen(self.desk)                                                  # mise en service : dossiers présents = référence
        self.assertFalse([x for x in f55.todo(self.desk) if x['type'] == 'sans_convention'])
        self.desk.db.execute('UPDATE b55_matter_seen SET baseline=0 WHERE matter=?', (MID,))
        self.desk.db.commit()
        self.assertTrue([x for x in f55.todo(self.desk) if x['type'] == 'sans_convention'])

    def test_exclure_puis_reintegrer(self):
        f55.note_seen(self.desk)
        self.desk.db.execute('UPDATE b55_matter_seen SET baseline=0 WHERE matter=?', (MID,))
        self.desk.db.commit()
        f55.set_scope(self.desk, ACTOR, 'avocat', MID, 'exclure')
        self.assertFalse([x for x in f55.todo(self.desk) if x['matter'] == MID])
        self.assertIn(MID, [x['id'] for x in f55.excluded(self.desk)])
        f55.set_scope(self.desk, ACTOR, 'avocat', MID, 'inclure')
        self.assertIn(MID, [m['id'] for m in f55.client_matters(self.desk)])
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            f55.set_scope(self.desk, 'assistant@example.test', 'assistant', MID, 'exclure')

    def test_tableau_de_bord_et_a_faire(self):
        d = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'libre', 'date_prestation': '2026-10-01', 'lignes': [
            {'libelle': 'Consultation', 'quantite': '1', 'prix_unitaire_ht': '1000'}]})
        board = f55.dashboard(self.desk)
        self.assertEqual(board['brouillons'], 1)
        items = f55.todo(self.desk)
        self.assertEqual(items[0]['type'], 'brouillon')
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        self.assertEqual(f55.todo(self.desk)[0]['type'], 'a_emettre')
        d = fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'cle-emission-5501')
        self.desk.db.execute("UPDATE b53_documents SET snapshot_json=replace(snapshot_json, ?, '\"echeance\": \"2026-01-01\"') WHERE id=?",
                             ('"echeance": "%s"' % d['snapshot']['dates']['echeance'], d['id']))
        self.desk.db.commit()
        top = f55.todo(self.desk)[0]
        self.assertEqual(top['type'], 'echue')
        self.assertEqual(f55.dashboard(self.desk)['echus_nombre'], 1)
        self.assertEqual(f55.overview(self.desk)[0]['id'], MID)

    def test_fiche_cabinet_incomplete_signalee(self):
        self.assertEqual(f55.setup_missing(self.desk), [])
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', {'iban': '', 'version': fact.entity(self.desk)['version']})
        self.assertIn('IBAN', f55.setup_missing(self.desk))


class Refresh(base.Base):
    """Régression des brouillons automatiques : plus de reconstruction du dossier dans l'indexation, jamais pendant une rédaction sur un
    grand dossier ; une seule actualisation de faible priorité en file par dossier."""

    def test_indexation_planifie_une_seule_actualisation(self):
        with patch.object(modele5651, 'ensure_current', side_effect=AssertionError('reconstruction interdite ici')), \
                patch.object(modele5651, 'doc_count', return_value=40):
            self.desk._current5652(MID)
            self.desk._current5652(MID)
        jobs = self.desk.db.execute("SELECT priority,args FROM jobs WHERE kind='actualiser5655'").fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertGreater(jobs[0]['priority'], 20)                                # après les brouillons automatiques
        self.assertEqual(self.desk.db.execute('SELECT state FROM d51_rebuilds WHERE matter=?', (MID,)).fetchone()['state'], 'a_actualiser')

    def test_grand_dossier_jamais_reconstruit_pendant_une_redaction(self):
        with patch.object(modele5651, 'doc_count', return_value=5000), \
                patch.object(modele5651, 'ensure_current', side_effect=AssertionError('reconstruction interdite ici')):
            ctx = contexte5651.for_drafting(self.desk, MID, 'question fictive')
        self.assertIn('etat_du_contexte', ctx)
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='actualiser5655'").fetchone())   # trop volumineux : sur demande

    def test_bouton_actualiser_grand_dossier_en_arriere_plan(self):
        from agent import web5651
        with patch('agent.web5634._post', return_value={'matter': MID}), patch('agent.web567.actor', return_value=(ACTOR, 'avocat')), \
                patch.object(modele5651, 'doc_count', return_value=5000), \
                patch.object(modele5651, 'rebuild', side_effect=AssertionError('reconstruction interdite dans la requête')):
            for _ in range(2):
                body = web5651.api({}, self.desk, None, '', 'm5651/analyser', {}, 'POST')
        self.assertIn('arrière-plan', str(body))
        jobs = self.desk.db.execute("SELECT priority FROM jobs WHERE kind='actualiser5655' AND status='pending'").fetchall()
        self.assertEqual([j['priority'] for j in jobs], [5])                       # une seule, avant les brouillons automatiques

    def test_petit_dossier_toujours_a_jour_avant_redaction(self):
        with patch.object(modele5651, 'doc_count', return_value=10):
            self.assertEqual(modele5651.refresh(self.desk, MID)['state'], 'ok')


if __name__ == '__main__':
    unittest.main()
