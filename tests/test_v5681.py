"""5.6.81 : fournisseurs d'IA préréglés (liste des modèles, tarifs et plafonds préremplis, profil Mixte) et pause de l'IA pendant les mises à jour.
Données fictives."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_v5648 as login
import test_v5652 as base
from agent import ai_gateway, ia540, maj5661, modeles5681, queue567
from agent.arret5643 import DETERMINISTIC, global_paused, pause_all
from agent.common import Stop

MISTRAL = {'data': [{'id': 'mistral-large-4', 'capabilities': {'completion_chat': True}}, {'id': 'mistral-embed', 'capabilities': {'completion_chat': False}},
                    {'id': 'mistral-ocr-latest', 'capabilities': {'completion_chat': False}}, {'id': 'modele-de-demain', 'name': 'Modèle de demain'}]}
FEED = '<feed><entry><title>v9.9.9</title></entry></feed>'


def provider(desk, key='cle-fictive'):
    ai_gateway.save_provider(desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', key, True, True)


class Liste(base.Base):
    def test_lecture_des_reponses_de_chaque_fournisseur(self):
        self.assertEqual([r['id'] for r in modeles5681.parse('mistral', MISTRAL)], ['mistral-large-4', 'modele-de-demain'])
        self.assertEqual([r['id'] for r in modeles5681.parse('ollama', {'models': [{'name': 'modele-local:12b'}, {'model': 'nomic-embed-text'}]})], ['modele-local:12b'])
        rows = modeles5681.parse('openrouter', {'data': [{'id': 'fournisseur/modele', 'name': 'Modèle', 'pricing': {'prompt': '0.000001', 'completion': '0.000003'}}]})
        self.assertEqual((rows[0]['entree'], rows[0]['sortie']), (1.0, 3.0))
        self.assertEqual(modeles5681.parse('anthropic', {'data': [{'id': 'modele-essai', 'display_name': 'Essai'}]})[0]['label'], 'Essai')
        with self.assertRaises(Stop):
            modeles5681.parse('openai', {'detail': 'inattendu'})

    def test_tarif_seulement_quand_il_est_verifie(self):
        self.assertEqual(modeles5681.tariff('mistral-large-4'), (0.70, 2.10))
        self.assertEqual(modeles5681.tariff('mistral-large-4-2610'), (0.70, 2.10))
        self.assertIsNone(modeles5681.tariff('mistral-large-40'))
        self.assertNotIn('entree', modeles5681.parse('mistral', MISTRAL)[1])                # aucun tarif inventé

    def test_liste_gardee_une_heure_et_actualisable(self):
        provider(self.desk)
        calls = []
        fetch = lambda row: calls.append(row['id'] if 'id' in row else 1) or MISTRAL
        first = modeles5681.list_models(self.desk, 'mistral', fetch=fetch)
        second = modeles5681.list_models(self.desk, 'mistral', fetch=fetch)
        self.assertFalse(first['depuis_le_cache'])
        self.assertTrue(second['depuis_le_cache'])
        self.assertEqual(len(calls), 1)
        modeles5681.list_models(self.desk, 'mistral', True, fetch=fetch)
        self.assertEqual(len(calls), 2)

    def test_fournisseur_inconnu_ou_sans_cle(self):
        with self.assertRaises(Stop):
            modeles5681.list_models(self.desk, 'inconnu', fetch=lambda row: MISTRAL)
        with patch('agent.ai_gateway.provider_registry', return_value={'mistral': {'type': 'mistral', 'url': 'https://api.mistral.ai/v1'}}), \
                self.assertRaises(Stop) as stop:
            modeles5681.list_models(self.desk, 'mistral', fetch=lambda row: MISTRAL)
        self.assertEqual(str(stop.exception).split(':')[0], 'cle_api_fournisseur_absente')


class Plafonds(base.Base):
    def test_tarif_et_plafonds_preremplis(self):
        provider(self.desk)
        row = ai_gateway.provider_registry(self.desk.c)['mistral']
        self.assertEqual((row['input_usd_per_million'], row['output_usd_per_million']), (0.70, 2.10))
        self.assertEqual((row['monthly_budget_usd'], row['per_request_budget_usd']), (50.0, 0.50))

    def test_les_montants_de_l_avocat_sont_gardes(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True, False, 12, 0.1, 3, 9)
        row = ai_gateway.provider_registry(self.desk.c)['mistral']
        self.assertEqual((row['input_usd_per_million'], row['output_usd_per_million'], row['monthly_budget_usd'], row['per_request_budget_usd']), (3, 9, 12, 0.1))

    def test_openrouter_exige_toujours_ses_plafonds(self):
        with self.assertRaises(Stop) as stop:
            ai_gateway.save_provider(self.desk, 'openrouter', 'openrouter', 'https://openrouter.ai/api/v1', 'fournisseur/modele', 'cle-fictive', True, True)
        self.assertEqual(str(stop.exception).split(':')[0], 'plafonds_openrouter_requis')

    def test_modele_sans_tarif_connu_reste_refuse_par_le_budget(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'modele-de-demain', 'cle-fictive', True, True)
        row = ai_gateway.provider_registry(self.desk.c)['mistral']
        self.assertEqual((row['input_usd_per_million'], row['output_usd_per_million']), (0, 0))


class ProfilMixte(base.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'model': 'modele-local:12b'}

    def test_autorisation_d_envoi_jamais_deduite(self):
        with self.assertRaises(Stop) as stop:
            modeles5681.apply_mixte(self.desk, 'cle-fictive', False, fetch=lambda row: MISTRAL)
        self.assertEqual(str(stop.exception).split(':')[0], 'autorisation_donnees_externes_requise')
        self.assertNotIn('mistral', {k: v for k, v in ai_gateway.provider_registry(self.desk.c).items() if v.get('enabled')})

    def test_routes_du_profil(self):
        out = modeles5681.apply_mixte(self.desk, 'cle-fictive', True, fetch=lambda row: MISTRAL)
        self.assertEqual(out['modele'], 'mistral-large-4')
        routes = {p: self.desk.settings('ai:route:' + p, {}) for p in ai_gateway.PURPOSES}
        for purpose in ia540.LOCAL_PURPOSES:
            self.assertEqual(routes[purpose]['provider'], 'ollama')
        self.assertEqual(routes['voice_conversation'], {'provider': 'mistral', 'model': 'mistral-large-4'})
        self.assertEqual(routes['mail_drafting']['provider'], 'mistral')
        self.assertIn('50,00 $ par mois', out['message'])

    def test_version_datee_retenue_si_le_nom_court_manque(self):
        out = modeles5681.apply_mixte(self.desk, 'cle-fictive', True, fetch=lambda row: {'data': [{'id': 'mistral-large-4-2609'}, {'id': 'mistral-large-4-2610'}]})
        self.assertEqual(out['modele'], 'mistral-large-4-2610')
        self.assertEqual(ai_gateway.provider_registry(self.desk.c)['mistral']['model'], 'mistral-large-4-2610')

    def test_modele_absent_du_compte(self):
        with self.assertRaises(Stop) as stop:
            modeles5681.apply_mixte(self.desk, 'cle-fictive', True, fetch=lambda row: {'data': [{'id': 'modele-de-demain'}]})
        self.assertEqual(str(stop.exception).split(':')[0], 'modele_ou_route_fournisseur_absent')

    def test_plafonds_deja_regles_conserves(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', False, False, False, 20, 0.2, 0.7, 2.1)
        out = modeles5681.apply_mixte(self.desk, '', True, fetch=lambda row: MISTRAL)
        self.assertEqual((out['plafond_mensuel_usd'], out['plafond_requete_usd']), (20, 0.2))

    def test_carte_du_profil(self):
        html = modeles5681.form_html(self.desk)
        self.assertIn('name="api_key"', html)
        self.assertIn('name="consent" required', html)
        self.assertIn('modele-local:12b', html)
        provider(self.desk)
        self.assertNotIn('name="api_key"', modeles5681.form_html(self.desk))


class Pages(login.Base):
    def setUp(self):
        super().setUp()
        self.cookie = self.admin_cookie()

    def test_routes_reservees_a_l_administrateur(self):
        from agent.standalone_auth import ADMIN_API
        self.assertIn('m5681/', ADMIN_API)
        out = self.api('m5681/modeles', {'provider': 'inconnu'}, self.cookie)
        self.assertEqual(out['status'][:3], '400', out)

    def test_boutons_et_scripts_presents(self):
        out = self.request('/ia-externe', cookie=self.cookie)
        self.assertEqual(out['status'][:3], '200')
        self.assertIn('v5681.js', out['body'])
        self.assertIn('data-m81-load="mistral"', out['body'])
        self.assertIn('data-m81-load="ollama"', out['body'])
        self.assertIn('name="monthly_budget_usd" value="50.0"', out['body'])


class PauseMaj(base.Base):
    def setUp(self):
        super().setUp()
        maj5661.check(self.desk, fetch=lambda url: FEED.encode(), force=True)

    def _job(self, kind='index', origin='automatic', status='running'):
        jid = self.desk.enqueue(kind, {'n': kind + origin + status})
        self.desk.db.execute('UPDATE jobs SET status=?,origin=?,worker=? WHERE id=?', (status, origin, '1', jid))
        self.desk.db.commit()
        return jid

    def _row(self, jid):
        return self.desk.db.execute('SELECT status,stop_reason,finished FROM jobs WHERE id=?', (jid,)).fetchone()

    def _serveur(self, **kw):
        with patch('agent.maj5661.platform', return_value='serveur'), patch('agent.maj5661.activated', return_value=True), \
                patch('agent.maj5661.signer_ready', return_value=True), patch('agent.maj5661.UPDATE_WAIT_SECONDS', 0):
            maj5661.request_dir(self.desk).mkdir(exist_ok=True)
            return maj5661.request(self.desk, 'admin@example.test', '9.9.9')

    def test_serveur_pause_avant_la_demande(self):
        out = self._serveur()
        self.assertTrue(global_paused(self.desk))
        self.assertTrue((maj5661.request_dir(self.desk) / 'demande.json').exists())
        self.assertEqual(self.desk.settings(maj5661.UPDATE_PAUSE, {})['version'], '9.9.9')
        self.assertIn('pause', out['message'])

    def test_demande_refusee_ne_met_pas_en_pause(self):
        with patch('agent.maj5661.platform', return_value='serveur'), patch('agent.maj5661.activated', return_value=True), \
                patch('agent.maj5661.signer_ready', return_value=True):
            with self.assertRaises(Stop):
                maj5661.request(self.desk, 'admin', '5.6.59')
        self.assertFalse(global_paused(self.desk))

    def test_poste_le_travail_de_mise_a_jour_passe_malgre_la_pause(self):
        with patch('agent.maj5661.platform', return_value='windows'), patch('agent.maj5661.signer_ready', return_value=True), \
                patch('agent.maj5661.UPDATE_WAIT_SECONDS', 0):
            out = maj5661.request(self.desk, 'admin@example.test', '9.9.9')
        self.assertTrue(global_paused(self.desk))
        self.assertEqual(self._row(out['job'])['status'], 'pending')
        self.assertIn('maj_local5661', DETERMINISTIC)
        with patch('agent.desk.Desk.perform', return_value={'ok': True}) as perform:
            self.desk.work_once()
        perform.assert_called_once()
        self.assertEqual(self._row(out['job'])['status'], 'done')

    def test_attente_bornee_des_travaux_en_cours(self):
        self._job()
        naps = []
        self.assertTrue(maj5661.pause_before_update(self.desk, '9.9.9', sleep=naps.append))
        self.assertEqual(len(naps), maj5661.UPDATE_WAIT_SECONDS)

    def test_attente_finie_des_que_les_travaux_s_arretent(self):
        jid = self._job()

        def stop(_):
            self.desk.db.execute("UPDATE jobs SET status='suspended' WHERE id=?", (jid,))
            self.desk.db.commit()
        naps = []
        maj5661.pause_before_update(self.desk, '9.9.9', sleep=lambda s: (naps.append(s), stop(s)))
        self.assertEqual(len(naps), 1)

    def test_pause_de_l_avocat_conservee(self):
        pause_all(self.desk, True, 'avocat')
        self.assertFalse(maj5661.pause_before_update(self.desk, '9.9.9'))
        with patch('agent.maj5661.installed_version', return_value='9.9.9'):
            self.assertFalse(maj5661.resume_after_update(self.desk))
        self.assertTrue(global_paused(self.desk))

    def test_reprise_une_fois_la_version_installee(self):
        self._serveur()
        with patch('agent.maj5661.installed_version', return_value='5.6.81'):
            self.assertFalse(maj5661.resume_after_update(self.desk))
        self.assertTrue(global_paused(self.desk))
        with patch('agent.maj5661.installed_version', return_value='9.9.9'):
            self.assertTrue(maj5661.resume_after_update(self.desk))
        self.assertFalse(global_paused(self.desk))
        self.assertEqual(self.desk.settings(maj5661.UPDATE_PAUSE, {}), {})

    def test_reprise_apres_echec_de_la_mise_a_jour(self):
        self._serveur()
        later = datetime.now(timezone.utc) + timedelta(minutes=46)
        with patch('agent.maj5661.installed_version', return_value='5.6.81'):
            self.assertTrue(maj5661.resume_after_update(self.desk, now=later))
        self.assertFalse(global_paused(self.desk))

    def test_pause_levee_a_la_main_efface_le_repere(self):
        self._serveur()
        pause_all(self.desk, False, 'avocat')
        self.assertFalse(maj5661.resume_after_update(self.desk))
        self.assertEqual(self.desk.settings(maj5661.UPDATE_PAUSE, {}), {})

    def test_travail_arrete_par_la_pause_repris_apres_redemarrage(self):
        jid = self._job()
        pause_all(self.desk, True, 'mise-a-jour')
        self.assertEqual(self._row(jid)['status'], 'cancel_requested')
        queue567.recover(self.desk, '1')
        row = self._row(jid)
        self.assertEqual((row['status'], row['stop_reason'], row['finished']), ('suspended', 'pause', None))
        pause_all(self.desk, False, 'mise-a-jour')
        self.assertEqual(self._row(jid)['status'], 'pending')

    def test_arret_demande_par_l_avocat_reste_une_annulation(self):
        jid = self._job(status='cancel_requested')
        queue567.recover(self.desk, '1')
        self.assertEqual(self._row(jid)['status'], 'cancelled')


class Dictee(unittest.TestCase):
    def _dictate(self, code):
        from agent import audio

        class Client:
            base = 'http://127.0.0.1:9011'

            def __init__(self, *a, **k):
                pass

            def request(self, *a, **k):
                raise Stop(code)
        config = {'audio': {'enabled': True, 'bridge_token_file': 'jeton-fictif'}}
        with patch('agent.audio.HTTP', Client), patch('agent.audio.read_secret', return_value='jeton-fictif'), self.assertRaises(Stop) as stop:
            audio.dictate(config, b'son', 'audio/webm')
        return str(stop.exception)

    def test_cause_reelle_plutot_qu_un_code(self):
        from agent.web import REASONS
        self.assertEqual(self._dictate('http_502'), 'transcription_locale_en_echec')
        self.assertEqual(self._dictate('connexion_http_indisponible'), 'pont_vocal_injoignable')
        self.assertEqual(self._dictate('http_429'), 'transcription_en_cours')
        self.assertEqual(self._dictate('http_401'), 'http_401')
        for code in ('transcription_locale_en_echec', 'pont_vocal_injoignable', 'transcription_trop_longue', 'transcription_en_cours'):
            self.assertIn(code, REASONS)

    def test_micro_coupe_apres_deux_echecs(self):
        js = (Path(__file__).resolve().parents[1] / 'agent/static/v568.js').read_text(encoding='utf-8')
        self.assertIn('++dictationFailures>=2', js)
        self.assertIn('dictationFailures=0;transcript.value=v.text', js)


class Conversation(base.Base):
    def test_phrases_sans_objet(self):
        from agent import rapide5680 as rp
        for text in ('Ok, alors, est-ce que ça fonctionne ?', 'Bonjour', 'Merci !', 'Tu m’entends ?', 'test du micro'):
            self.assertTrue(rp.small_talk(text), text)
        for text in ('Quel est le délai d’appel ?', 'Est-ce que la clause fonctionne ?', 'Résume le dossier DOS-001', 'RG 24/01234', ''):
            self.assertFalse(rp.small_talk(text), text)

    def test_ni_passages_ni_modele_a_l_echelle_du_cabinet(self):
        from agent import integration, rapide5680 as rp
        self.assertEqual(rp.passages(self.desk.c, '', 'est-ce que ça fonctionne ?'), [])
        self.desk.db.execute("INSERT INTO assistant_threads(id,title,created,updated) VALUES('a','essai','2026-01-01','2026-01-01')")
        cur = self.desk.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) "
                                   "VALUES('a','user','Ok, est-ce que ça fonctionne ?','pending','[]',NULL,'2026-01-01','2026-01-01')")
        self.desk.db.commit()
        with patch('agent.workspace.chat', side_effect=AssertionError('aucun modèle')):
            out = integration.answer_question(self.desk, {'message': cur.lastrowid, 'thread': 'a', 'purpose': 'voice_conversation'})
        self.assertTrue(out['sans_controle'])
        self.assertEqual(out['sources'], 0)
        text = self.desk.db.execute("SELECT content FROM assistant_messages WHERE role='assistant'").fetchone()[0]
        self.assertIn('je vous reçois bien', text)

    def test_dans_un_dossier_ou_avec_une_piece_la_question_suit_son_chemin(self):
        from agent import rapide5680 as rp
        self.assertFalse(rp.conversation_only({}, 'DOS-001', '', 'ça fonctionne ?'))
        self.assertFalse(rp.conversation_only({'attachment_ids': ['x']}, '', '', 'ça fonctionne ?'))
        self.assertFalse(rp.conversation_only({'page_context': {'selected_documents': ['/a.pdf']}}, '', '', 'ça fonctionne ?'))
        self.assertTrue(rp.conversation_only({}, '', '', 'ça fonctionne ?'))

    def test_reponse_vocale_sans_balises_et_en_entier(self):
        root = Path(__file__).resolve().parents[1] / 'agent/static'
        js = (root / 'v568.js').read_text(encoding='utf-8')
        self.assertIn('status,answer,transcript,send', js)
        self.assertIn('const text=plain(', js)
        self.assertIn('.voice568-panel .voice568-answer{max-height:none', (root / 'v5681.css').read_text(encoding='utf-8'))


class Mentions(unittest.TestCase):
    def test_emplacement_de_qdrant(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('Paramètres › Connexions, section Intelligence artificielle', (root / 'docker-compose.qdrant.yml').read_text(encoding='utf-8'))
        self.assertIn('Paramètres › Connexions, section Intelligence artificielle', (root / 'agent/ia540.py').read_text(encoding='utf-8'))
        self.assertIsNone(re.search(r'Paramètres › Intelligence artificielle', (root / 'agent/ia540.py').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
