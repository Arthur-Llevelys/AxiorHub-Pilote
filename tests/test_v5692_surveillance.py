"""5.6.92 : surveillance des documents espacée, bouton de pause toujours visible. Données fictives, Nextcloud simulé."""
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_v450_echeances as t450
from agent import accueil5689, watch430

RESULTAT = {'changed': 0, 'partial': 1, 'failed': 0, 'deferred': 0, 'continue_scan': True, 'remote_write': False}


class Lots(t450.Base):
    def _schedule(self, stamp):
        with patch('agent.proactive568.schedule', return_value=[]), patch('agent.activity431.maintenance'):
            watch430._schedule_5642(self.desk, stamp)
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='live_documents430' AND status='pending'").fetchone()[0]

    def test_un_lot_en_cours_de_tour_n_enchaine_pas_le_suivant(self):
        """Avant : chaque lot terminé avec continue_scan relançait aussitôt le suivant, plus de 400 fois en 3 heures chez le cabinet."""
        job = self.desk.enqueue('live_documents430', priority=55)
        with patch.object(watch430, 'documents_check', lambda desk: RESULTAT):
            self.desk.work_once(only={'live_documents430'})
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'done')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='live_documents430' AND status='pending'").fetchone()[0], 0)

    def test_au_milieu_d_un_tour_un_lot_toutes_les_cinq_minutes_au_plus(self):
        now = time.time()
        self.desk.setting('live430:document_cursor', 6)                    # tour en cours
        self.desk.setting('live430:last:live_documents430', now - 120)    # dernier lot il y a 2 min
        self.assertEqual(self._schedule(now), 0)
        self.desk.setting('live430:last:live_documents430', now - watch430.LOT_DOCUMENTS - 1)
        self.assertEqual(self._schedule(now), 1)
        self.assertGreaterEqual(watch430.LOT_DOCUMENTS, 300)

    def test_cadence_sur_une_heure_simulee(self):
        """Au plus 12 lots par heure, même si chaque lot annonce qu'il reste des dossiers."""
        debut = time.time()
        self.desk.setting('live430:document_cursor', 6)
        self.desk.setting('live430:last:live_documents430', debut - 10_000)
        lots = 0
        with patch.object(watch430, 'documents_check', lambda desk: RESULTAT):
            for seconde in range(0, 3600, 15):
                if self._schedule(debut + seconde):
                    self.desk.work_once(only={'live_documents430'})
                    lots += 1
        self.assertLessEqual(lots, 12)
        self.assertGreaterEqual(lots, 11)


class Pause(t450.Base):
    def test_bouton_de_pause_dans_l_en_tete(self):
        html = accueil5689.pause_html(self.desk)
        self.assertIn('id="a92-pause"', html)
        self.assertIn('Pause de l’IA', html)
        self.desk.setting('missions567:pause', True)
        self.assertIn('IA en pause — reprendre', accueil5689.pause_html(self.desk))

    def test_le_script_gere_le_bouton_de_l_en_tete_hors_du_bloc_routines(self):
        js = (Path(watch430.__file__).parent / 'static' / 'v5614.js').read_text(encoding='utf-8')
        self.assertLess(js.index("closest('#a92-pause')"), js.index("const routines = document.querySelector('#c5614-routines')"))

    def test_page_aujourd_hui_avec_le_bouton_dans_l_en_tete(self):
        from agent import cockpit530
        html = cockpit530.page(self.desk, '/p', 'x', 'admin')
        tete = html[html.index('<header class="c530-top'):html.index('</header>')]
        self.assertIn('id="a92-pause"', tete)


if __name__ == '__main__':
    unittest.main()
