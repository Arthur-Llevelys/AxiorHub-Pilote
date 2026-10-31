"""5.6.85 : erreur 402 d'un fournisseur d'IA. Avant, le code remontait tel quel : ni relais, ni message clair, rien de produit. Données fictives, aucun appel réseau."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_v5668 as t68
import test_v5682 as t82
from agent import ai_gateway, budget5668, chatgpt5662, conso5682, messages5668, model, rapide5680, routage5682, synthese5632
from agent.common import Stop
from agent.erreurs5634 import http_error

CODE = 'credit_fournisseur_epuise'


def translate(cfg=None):
    return str(model.Model._translate_error(SimpleNamespace(cfg=cfg or {}), http_error(402, 'POST', 'https://api.example.test/v1/chat/completions')))


class Traduction(unittest.TestCase):
    def test_402_devient_credit_epuise(self):
        self.assertEqual(translate(), CODE)

    def test_autres_codes_inchanges(self):
        self.assertEqual(str(model.Model._translate_error(SimpleNamespace(cfg={}), Stop('http_429'))), 'quota_fournisseur_depasse')
        self.assertEqual(str(model.Model._translate_error(SimpleNamespace(cfg={}), Stop('http_500'))), 'http_500')

    def test_relais_et_listes(self):
        self.assertIn(CODE, budget5668.BUDGET_CODES)
        self.assertIn(CODE, routage5682.FALLBACK_ON)
        self.assertIn(CODE, rapide5680.BUSY_CODES)
        self.assertIn(CODE, synthese5632.PROVIDER_DOWN)
        self.assertTrue(budget5668.not_billed(CODE))
        self.assertTrue(budget5668.not_billed('http_402'))
        self.assertIn('rechargez', ai_gateway.ERROR_MESSAGES[CODE])
        self.assertIn('rechargez', messages5668.MESSAGES[CODE])

    def test_message_en_clair(self):
        from agent.web440 import human
        self.assertIn('Crédit épuisé', human(CODE))
        self.assertNotIn('_', human(CODE))

    def test_abonnement_chatgpt(self):
        import inspect
        self.assertIn("402: 'chatgpt_compte_non_eligible'", inspect.getsource(chatgpt5662))

    def test_relais_local_du_modele(self):
        calls = []

        class Local(model.Model):
            def __init__(self):
                self.cfg = {'budget_fallback': {'provider_type': 'ollama', 'model': 'local-fictif'}}
                self.provider_type = 'openrouter'
                self._trace = ('', None)

            def _hybrid_choice(self, *a, **k):
                return None

            def _traced(self, *a):
                raise Stop(CODE)

            def _call_child(self, cfg, method, *args):
                calls.append(cfg['model'])
                return 'réponse locale'
        with patch.object(budget5668, 'note_fallback', lambda *a: None):
            self.assertEqual(Local().complete([{'role': 'user', 'content': 'x'}]), 'réponse locale')
        self.assertEqual(calls, ['local-fictif'])


class Repli(t82.Automatique):
    def child(self, failing):
        calls = []

        def run(cfg, method, *args):
            calls.append(cfg['provider_id'])
            if cfg['provider_id'] == failing:
                raise Stop(CODE)
            return t82.TRIAGE_OK
        return calls, run

    def test_repli_sur_une_autre_connexion_cochee(self):
        self.auto()
        calls, run = self.child('chatgpt')
        parent = self.model()
        with patch.object(parent, '_call_child', run):
            self.assertEqual(json.loads(parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None))['intent'], 'status')
        self.assertEqual(calls, ['chatgpt', 'mistral'])

    def test_reservation_liberee(self):
        self.auto()
        settled = []
        calls, run = self.child('chatgpt')
        parent = self.model()
        real = conso5682.settle
        with patch.object(parent, '_call_child', run), patch.object(conso5682, 'settle', lambda s, r, state, *a, **k: (settled.append(state), real(s, r, state, *a, **k))[1]):
            parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(settled[0], 'liberee')

    def test_sans_autre_connexion_erreur_claire(self):
        self.auto()
        parent = self.model()

        def run(cfg, method, *args):
            raise Stop(CODE)
        with patch.object(parent, '_call_child', run), self.assertRaises(Stop) as ctx:
            parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(str(ctx.exception), CODE)


class Bandeau(t68.base.Base):
    def note(self, provider='fournisseur-fictif'):
        budget5668.note_credit({'state_dir': self.desk.c['state_dir'], 'provider_id': provider})

    def test_bandeau_et_disparition_apres_un_succes(self):
        self.note()
        html = budget5668.banner_html(self.desk, '/p')
        self.assertIn('Crédit épuisé chez fournisseur-fictif', html)
        self.assertIn('/p/parametres?tab=ia', html)
        later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        ai_gateway._usage_db(self.desk.c['state_dir']).close()
        self.desk.db.execute("INSERT INTO ai_usage_v391(at,provider,purpose,model,input_tokens,output_tokens,estimated_cost_usd,status) VALUES(?,?,?,?,0,0,0,'done')", (later, 'fournisseur-fictif', 'assistant', 'm'))
        self.desk.db.commit()
        self.assertNotIn('Crédit épuisé', budget5668.banner_html(self.desk, '/p'))

    def test_echec_recent_ne_compte_pas_comme_succes(self):
        self.note()
        ai_gateway._usage_db(self.desk.c['state_dir']).close()
        earlier = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.desk.db.execute("INSERT INTO ai_usage_v391(at,provider,purpose,model,input_tokens,output_tokens,estimated_cost_usd,status) VALUES(?,?,?,?,0,0,0,'done')", (earlier, 'fournisseur-fictif', 'assistant', 'm'))
        self.desk.db.commit()
        self.assertIn('Crédit épuisé', budget5668.banner_html(self.desk, '/p'))

    def test_trace_ancienne_effacee(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.desk.setting(budget5668.CREDIT_KEY, {'fournisseur-fictif': old})
        self.assertNotIn('Crédit épuisé', budget5668.banner_html(self.desk, '/p'))

    def test_traduction_note_le_fournisseur(self):
        translate({'state_dir': self.desk.c['state_dir'], 'provider_id': 'fournisseur-fictif'})
        self.assertEqual(budget5668._credit_rows(self.desk), ['fournisseur-fictif'])

    def test_note_sans_etat_ne_leve_rien(self):
        budget5668.note_credit({})


if __name__ == '__main__':
    unittest.main()
