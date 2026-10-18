"""5.6.63 : charge réelle mesurée chez le cabinet le 18 octobre 2026 — surveillance des documents 1 693 fois en 24 h, analyses
d'échéances en échec sur un long document ou un scan illisible. Données fictives."""
import time
import unittest
from unittest.mock import patch

import test_v450_echeances as t450
from agent import echeances450 as ech
from agent.common import Stop


class SurveillanceDesDocuments(t450.Base):
    def _schedule(self, stamp):
        from agent import watch430
        with patch('agent.proactive568.schedule', return_value=[]), patch('agent.activity431.maintenance'):
            watch430._schedule_5642(self.desk, stamp)
        return [r[0] for r in self.desk.db.execute("SELECT kind FROM jobs WHERE kind='live_documents430' AND status='pending'")]

    def test_nouveau_tour_30_min_apres_la_fin_du_precedent(self):
        now = time.time()
        self.desk.setting('live430:document_cursor', 0)
        self.desk.setting('live430:documents_sweep_done', now - 10 * 60)    # tour complet fini il y a 10 min
        self.assertEqual(self._schedule(now), [])
        self.desk.setting('live430:documents_sweep_done', now - 61 * 60)    # 61 min (régime économe d'office : 60 min)
        self.assertEqual(self._schedule(now), ['live_documents430'])

    def test_les_lots_d_un_tour_en_cours_continuent(self):
        now = time.time()
        self.desk.setting('live430:document_cursor', 6)                    # tour en cours
        self.desk.setting('live430:documents_sweep_done', now - 60)
        self.assertEqual(self._schedule(now), ['live_documents430'])

    def test_fin_de_tour_enregistree(self):
        from agent import watch430
        src = watch430.__file__
        text = open(src, encoding='utf-8').read()
        self.assertIn("desk.setting('live430:documents_sweep_done',time.time())", text)


class EcheancesSansEchec(t450.Base):
    def test_long_document_lu_en_entier(self):
        seen = {}

        def fake_extract(raw, name, cfg):
            seen['max'] = cfg['max_document_chars']
            return t450.TEXT + '\n' + 'x' * 150000
        with patch('agent.documents.extract', side_effect=fake_extract):
            result = ech.analyze(self.desk, {'matter': 'DOS-001', 'path': t450.PATH, 'etag': '"e1"'}, dav=self.dav, today=t450.TODAY)
        self.assertGreaterEqual(seen['max'], 2_000_000)                    # analyse par règles : pas de limite faite pour un modèle d'IA
        self.assertEqual(result['echeances_creees'], 1)

    def test_scan_illisible_signale_sans_echec_ni_nouvel_essai(self):
        calls = []

        def bad(raw, name):
            calls.append(name)
            raise Stop('ocr_qualite_insuffisante')
        args = {'matter': 'DOS-001', 'path': t450.PATH, 'etag': '"e1"'}
        result = ech.analyze(self.desk, args, dav=self.dav, extractor=bad, today=t450.TODAY)
        self.assertEqual(result['illisible'], 'ocr_qualite_insuffisante')
        self.assertIn('vérifiez vous-même si elle fait courir un délai', result['message'])
        again = ech.analyze(self.desk, args, dav=self.dav, extractor=bad, today=t450.TODAY)
        self.assertEqual(again['illisible'], 'ocr_qualite_insuffisante')
        self.assertEqual(len(calls), 1)                                    # enregistré : pas de nouvel essai à chaque passage

    def test_autre_erreur_toujours_signalee(self):
        def broken(raw, name):
            raise Stop('connexion_http_indisponible')
        with self.assertRaises(Stop):
            ech.analyze(self.desk, {'matter': 'DOS-001', 'path': t450.PATH, 'etag': '"e2"'}, dav=self.dav, extractor=broken, today=t450.TODAY)


if __name__ == '__main__':
    unittest.main()
