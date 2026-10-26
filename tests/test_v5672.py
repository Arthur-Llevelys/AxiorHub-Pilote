"""5.6.72 : alerte « modèle local injoignable », nouvelles tentatives des réponses « sans dossier » après une coupure.

Valeurs entièrement fictives (dossier DOS-001, adresses example.test, réseau de documentation)."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import test_v5652 as base
from agent import ia5672, reponses5671 as rep
from agent.common import Stop

KEY = 'd' * 64


def _job(desk, kind, status, result, minutes_ago, args=None):
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    jid = desk.enqueue(kind, args or {})
    desk.db.execute('UPDATE jobs SET status=?,finished=?,result=? WHERE id=?', (status, when, json.dumps(result), jid))
    desk.db.commit()
    return jid


def _trace_ok(desk, minutes_ago):
    from agent import trace480
    trace480.ensure_schema(desk.db)
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    desk.db.execute("INSERT INTO trace_calls480(at,stage,provider,external,status) VALUES(?,?,?,?,?)", (when, 'triage', 'ollama', 0, 'ok'))
    desk.db.commit()


class Alerte(base.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'url': 'http://192.0.2.10:11434'}

    def test_panne_signalee_apres_dix_minutes_puis_levee(self):
        self.assertFalse(ia5672.local_down(self.desk)['down'])
        _trace_ok(self.desk, 60)
        _job(self.desk, 'live_mail430', 'error', {'erreur': 'moteur_ollama_injoignable'}, 5)
        self.assertFalse(ia5672.local_down(self.desk)['down'])                       # moins de 10 minutes : pas d'alerte
        _job(self.desk, 'live_mail430', 'error', {'erreur': 'moteur_ollama_injoignable'}, 30)
        st = ia5672.local_down(self.desk)
        self.assertTrue(st['down'])
        self.assertEqual((st['errors'], st['url']), (2, 'http://192.0.2.10:11434'))
        html = ia5672.banner_html(self.desk, '/p')
        self.assertIn('Le modèle local d’IA ne répond plus', html)
        self.assertIn('data-c5671="m5672/ia/tester"', html)
        self.assertIn('OLLAMA_HOST=0.0.0.0', html)
        _trace_ok(self.desk, 1)                                                         # le moteur répond de nouveau
        self.assertFalse(ia5672.local_down(self.desk)['down'])
        self.assertEqual(ia5672.banner_html(self.desk, '/p'), '')

    def test_autres_erreurs_sans_alerte(self):
        _job(self.desk, 'prepare_reply', 'error', {'erreur': 'reponse_envoyee_depuis'}, 30)
        self.assertFalse(ia5672.local_down(self.desk)['down'])

    def test_bouton_tester(self):
        _job(self.desk, 'live_mail430', 'error', {'erreur': 'moteur_ollama_injoignable'}, 30)
        with patch('agent.common.HTTP.json', side_effect=Stop('connexion_http_indisponible')):
            out = ia5672.probe(self.desk)
        self.assertFalse(out['ok'])
        self.assertTrue(ia5672.local_down(self.desk)['down'])
        with patch('agent.common.HTTP.json', return_value={'version': '0.34.2'}):
            out = ia5672.probe(self.desk)
        self.assertTrue(out['ok'])
        self.assertIn('version 0.34.2', out['message'])
        self.assertFalse(ia5672.local_down(self.desk)['down'])                       # le test réussi lève l'alerte

    def test_bandeau_sur_aujourdhui_et_droits(self):
        from pathlib import Path
        from agent import cockpit530, standalone_auth, web440
        self.assertIn('_banner5672(desk, prefix)', Path(cockpit530.__file__).read_text(encoding='utf-8'))
        self.assertIn("m5672/", Path(web440.__file__).read_text(encoding='utf-8'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5672/ia/tester'))


class NouvelleTentative(base.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'url': 'http://192.0.2.10:11434'}

    def _review(self):
        from agent.state import State
        st = State(self.desk.c['state_dir'])
        st.set(KEY, 'm-x', 't-x', 'review', 'expediteur_inconnu_sans_dossier')
        st.report(KEY, {'key': KEY, 'subject': 'Demande de devis', 'sender': 'prospect@example.test', 'source_uid': '5',
                        'source_mailbox': 'INBOX', 'matter': None, 'reply_recipients': ['prospect@example.test']})

    def _failed(self, code, minutes_ago=30):
        return _job(self.desk, 'prepare_reply', 'error', {'erreur': code}, minutes_ago,
                    {'key': KEY, 'matter': '', 'sans_dossier': 'oui', 'origine': 'sans_dossier5671', 'n': minutes_ago})

    def test_echec_ia_injoignable_retente_trois_fois_au_plus(self):
        self.assertFalse(rep._attempted(self.desk, KEY))
        self._failed('moteur_ollama_injoignable', 40)
        self.assertFalse(rep._attempted(self.desk, KEY))
        self._failed('ia_en_pause', 41)
        self.assertFalse(rep._attempted(self.desk, KEY))
        self._failed('moteur_ollama_injoignable', 42)
        self.assertTrue(rep._attempted(self.desk, KEY))                                 # 3 essais : on s'arrête

    def test_echec_de_fond_non_retente(self):
        self._failed('reponse_envoyee_depuis')
        self.assertTrue(rep._attempted(self.desk, KEY))

    def test_pas_de_tentative_pendant_la_coupure_puis_reprise(self):
        self._review()
        self._failed('moteur_ollama_injoignable', 30)
        out = rep.sweep(self.desk)
        self.assertTrue(out.get('ia_injoignable'))
        _trace_ok(self.desk, 1)                                                         # l'IA répond de nouveau
        out = rep.sweep(self.desk)
        self.assertEqual(out['mis_en_preparation'], 1)


if __name__ == '__main__':
    unittest.main()
