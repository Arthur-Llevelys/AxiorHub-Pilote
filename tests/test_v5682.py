"""5.6.82 : recette du cahier des charges (RT, CG, QL, UX) : politique centrale, routage par niveau, abonnement ChatGPT strict, API Mistral et Claude,
recherche rapide. Données fictives ; aucun appel réseau réel."""
from datetime import datetime, timedelta, timezone
import io
import json
import re
from pathlib import Path
import unittest
from unittest.mock import patch

import test_v510
import test_v5652 as base
from agent import pieces510 as p5, web510
from agent import ai_gateway, catalogue5682, chatgpt5662, chatgpt5682, conso5682, ia5682, model as model_mod, politique5682, recherche5682, routage5682, sobriete5682, tache5682
from agent.arret5643 import pause_all
from agent.common import Stop
from agent.index import DocumentIndex
from agent.model import Model, routed_config
from agent.policy567 import check_external

ROWS = [{'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1 Sol', 'visibility': 'list'}, {'slug': 'gpt-6.1-terra', 'display_name': 'GPT-6.1 Terra', 'visibility': 'list'},
        {'slug': 'gpt-6-luna', 'display_name': 'GPT-6 Luna', 'visibility': 'list'}, {'slug': 'gpt-6-astra', 'display_name': 'GPT-6 Astra', 'visibility': 'list'},
        {'slug': 'interne', 'visibility': 'hide'}]
OK_USAGE = {'input_tokens': 1000, 'output_tokens': 200, 'input_tokens_details': {'cached_tokens': 400}, 'output_tokens_details': {'reasoning_tokens': 50}}
TRIAGE_OK = json.dumps({'needs_reply': True, 'intent': 'status', 'exclude_category': 'none', 'needs_documents': False, 'needs_calendar': False, 'ambiguous': False,
                        'search_terms': [], 'reason': 'demande fictive'})


def stream(text, usage=None):
    lines = ['data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': text}),
             'data: ' + json.dumps({'type': 'response.completed', 'response': {'usage': usage if usage is not None else OK_USAGE, 'output': []}})]
    return io.BytesIO(('\n'.join(lines) + '\n').encode())


class Fake:
    """Remplace ``chatgpt5662.http`` : catalogue et réponses programmées, requêtes conservées."""
    def __init__(self, replies=None, models=None):
        self.replies, self.models, self.sent = list(replies or []), models or ROWS, []

    def __call__(self, method, url, data=None, headers=None, timeout=60, stream=False):
        if url.endswith('/models'):
            return json.dumps({'models': self.models}).encode()
        self.sent.append(json.loads(data))
        reply = self.replies.pop(0) if self.replies else TRIAGE_OK
        if isinstance(reply, Exception):
            raise reply
        return globals()['stream'](reply) if isinstance(reply, str) else reply


def connect(desk, subject='sujet-1'):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    chatgpt5662.save_record(desk.c['state_dir'], {'email': 'compte@example.test', 'issuer': chatgpt5662.AUTH, 'subject': subject, 'client_id': 'client-fictif',
                                                  'id_token': 'jeton-id', 'access_token': 'jeton-acces-fictif', 'refresh_token': 'jeton-renouvellement-fictif',
                                                  'expires_at': future, 'scopes': [chatgpt5662.PLAN_SCOPE]})
    desk.setting(chatgpt5662.ACCOUNT_KEY, {'etat': 'connecte', 'plan': True, 'subject': subject, 'client_id': 'client-fictif', 'email': 'compte@example.test'})


class Strict(base.Base):
    def strict(self, fake=None, **extra):
        self.fake = fake or Fake()
        self.http = patch.object(chatgpt5662, 'http', self.fake)
        self.http.start()
        self.addCleanup(self.http.stop)
        connect(self.desk)
        return politique5682.save(self.desk, {'mode': 'chatgpt', 'consentement': True, **extra}, 'avocat')

    def model(self, purpose='mail_triage'):
        return Model(routed_config(self.desk.c, purpose))


class Selection(Strict):
    def test_rt01_classement_courant_modele_econome_et_decision_tracee_avant_appel(self):
        self.strict()
        seen = []
        original = self.fake.__call__

        class Spy(Fake):
            pass
        real = routage5682.child_config

        def spy(*a):
            seen.append(routage5682.recent(self.desk.c['state_dir'], 1)[0]['etat'])
            return real(*a)
        with patch.object(routage5682, 'child_config', spy):
            value = self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(value['intent'], 'status')
        self.assertEqual(seen, ['decidee'])
        self.assertEqual(self.fake.sent[-1]['model'], 'gpt-6-luna')
        self.assertEqual(self.fake.sent[-1]['reasoning'], {'effort': 'low'})
        row = routage5682.recent(self.desk.c['state_dir'], 1)[0]
        self.assertEqual((row['modele'], row['etat'], row['type_tache']), ('gpt-6-luna', 'terminee', 'classement'))

    def test_rt02_courriel_court_mais_strategique(self):
        short = tache5682.profile('mail_drafting', 'compose', {'incoming': 'Faut-il transiger ?', 'sources': [1]})
        self.assertEqual(short['niveau'], 'avance')
        self.assertIn('question_strategique', short['raisons'])

    def test_rt03_extraction_longue_sans_escalade_de_longueur(self):
        long = tache5682.profile('attachment_review', 'attachment_review', {'text': 'x' * 300000, 'documents': list(range(30)), 'contradictions': ['a']})
        self.assertEqual((long['niveau'], long['effort']), ('econome', 'low'))

    def test_rt04_modele_sans_capacite_exclu_avant_transmission(self):
        self.strict()
        data = self.desk.settings('catalogue5682:chatgpt')
        for row in data['modeles']:
            row['capacites']['contexte'] = 100
        self.desk.setting('catalogue5682:chatgpt', data)
        with self.assertRaises(Stop) as ctx:
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(str(ctx.exception), 'contexte_insuffisant')
        self.assertEqual(self.fake.sent, [])

    def test_rt05_piece_absente_ne_fait_pas_monter_le_niveau(self):
        missing = tache5682.profile('mail_drafting', 'compose', {'incoming': 'Où en est ma demande ?', 'sources': []})
        self.assertTrue(missing['sources_manquantes'])
        self.assertEqual(missing['niveau'], 'standard')
        self.assertIn('Pièces manquantes', tache5682.short_reason(missing))

    def test_rt06_sortie_invalide_une_reprise_au_niveau_superieur(self):
        self.strict(Fake(['pas du json', TRIAGE_OK]))
        self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual([x['model'] for x in self.fake.sent], ['gpt-6-luna', 'gpt-6.1-terra'])

    def test_rt06_deux_sorties_invalides_s_arretent(self):
        self.strict(Fake(['pas du json', 'toujours pas', TRIAGE_OK]))
        with self.assertRaises(Stop):
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(len(self.fake.sent), 2)

    def test_rt07_pause_aucun_appel(self):
        self.strict()
        pause_all(self.desk, True)
        with self.assertRaises(Stop) as ctx:
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.assertEqual(self.fake.sent, [])

    def test_rt07_decision_refusee_sous_pause(self):
        self.strict()
        pause_all(self.desk, True)
        before = len(routage5682.recent(self.desk.c['state_dir']))
        with self.assertRaises(Stop) as ctx:
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.assertEqual(len(routage5682.recent(self.desk.c['state_dir'])), before)

    def test_rt06_une_seule_escalade_par_etape(self):
        self.strict()
        calls = []

        def child(cfg, method, *args):
            calls.append(cfg['model'])
            raise Stop('generation_ia_incomplete')
        parent = self.model()
        with patch.object(parent, '_call_child', child), self.assertRaises(Stop):
            parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(calls, ['gpt-6-luna', 'gpt-6.1-terra'])

    def test_rt07_pause_apres_la_selection(self):
        self.strict()
        real = routage5682.child_config

        def later(*a):
            pause_all(self.desk, True)
            return real(*a)
        with patch.object(routage5682, 'child_config', later), self.assertRaises(Stop):
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(self.fake.sent, [])

    def test_niveaux_abonnement_par_famille_de_travail(self):
        self.strict()
        config = self.desk.c
        pick = lambda purpose, stage: routage5682.decide(config, purpose, stage, {'text': 'sans objet'})['modele']
        self.assertEqual(pick('mail_triage', 'triage'), 'gpt-6-luna')
        self.assertEqual(pick('mail_drafting', 'manual_draft'), 'gpt-6.1-terra')
        self.assertEqual(pick('legal_analysis', 'strategy_analysis'), 'gpt-6.1-sol')
        self.assertEqual(pick('hearing', 'hearing_preparation'), 'gpt-6.1-sol')   # Astra reste une option

    def test_option_niveau_maximal_jamais_implicite(self):
        self.strict()
        rows = catalogue5682.active(self.desk.c['state_dir'], 'chatgpt')
        self.assertIsNone(routage5682._rank_level('avance', {'niveau': 'maximal'}, False))
        self.assertIn('maximal', [r['niveau'] for r in rows])

    def test_preference_ne_modifie_pas_les_fournisseurs(self):
        self.strict(preference='approfondi')
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_drafting', 'manual_draft', {'text': 'x'})['type_fournisseur'], 'chatgpt')

    def test_budget_de_mission_cumule_et_persistant(self):
        self.strict()
        state = self.desk.c['state_dir']
        conso5682.reserve(state, 'mission-1', 90.0, 'credits', 100)
        with self.assertRaises(Stop) as ctx:
            conso5682.reserve(state, 'mission-1', 20.0, 'credits', 100)
        self.assertEqual(str(ctx.exception), 'budget_mission_atteint')
        self.assertEqual(conso5682.used(state, 'mission-1', 'credits'), 90.0)


class AbonnementStrict(Strict):
    def test_cg01_sans_ollama(self):
        self.strict()
        cfg = routed_config(self.desk.c, 'assistant')
        self.assertTrue(cfg['routeur5682'])
        with patch('agent.model.HTTP', side_effect=AssertionError('aucun appel au moteur local')):
            self.assertEqual(Model(cfg).ask('triage', {'subject': 'Objet fictif'})['intent'], 'status')
        self.assertFalse(politique5682.local_required(self.desk.c))

    def test_cg02_cles_api_presentes_zero_appel(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        self.strict()
        cfg = dict(ai_gateway.provider_registry(self.desk.c)['mistral'], provider_id='mistral', provider_type='mistral')
        with self.assertRaises(Stop) as ctx:
            Model(cfg)
        self.assertEqual(str(ctx.exception), 'fournisseur_interdit_par_politique')

    def test_cg02_repli_ancien_sans_effet(self):
        self.desk.setting('ai:route:assistant', {'provider': 'ollama', 'model': 'modele-local:1'})
        self.strict()
        cfg = routed_config(self.desk.c, 'assistant')
        for key in ('budget_fallback', 'secours', 'hybrid_external'):
            self.assertNotIn(key, cfg)
        with self.assertRaises(Stop):
            Model({**self.desk.c['ollama'], 'state_dir': self.desk.c['state_dir'], 'provider_id': 'ollama', 'provider_type': 'ollama'})

    def test_cg03_controle_meme_fournisseur(self):
        self.strict()
        self.assertEqual(routage5682.decide(self.desk.c, 'control', 'verify', {'text': 'x'})['type_fournisseur'], 'chatgpt')

    def test_cg04_identite_sans_usage_du_forfait(self):
        self.strict()
        acc = chatgpt5662.account(self.desk)
        self.desk.setting(chatgpt5662.ACCOUNT_KEY, {**acc, 'plan': False})
        with self.assertRaises(Stop) as ctx:
            routed_config(self.desk.c, 'assistant')
        self.assertEqual(str(ctx.exception), 'chatgpt_usage_plan_non_autorise')
        self.assertEqual(self.fake.sent, [])

    def test_cg04_reactivation_demande_le_consentement(self):
        self.desk.setting(chatgpt5662.ACCOUNT_KEY, {'etat': 'connecte', 'plan': False, 'client_id': 'client-fictif', 'subject': 's'})
        self.assertIn('prompt=consent', chatgpt5662.start(self.desk, 'avocat'))
        self.desk.setting(chatgpt5662.ACCOUNT_KEY, {'etat': 'connecte', 'plan': True, 'client_id': 'client-fictif', 'subject': 's'})
        self.assertNotIn('prompt=consent', chatgpt5662.start(self.desk, 'avocat'))

    def test_cg05_changement_de_compte(self):
        self.strict()
        connect(self.desk, 'sujet-2')
        self.fake.models = ROWS[:1]
        rows = catalogue5682.refresh(self.desk, 'chatgpt')
        self.assertEqual([r['slug'] for r in rows], ['gpt-6.1-sol'])
        self.assertEqual({r['compte'] for r in rows}, {'sujet-2'})

    def test_cg06_modele_retire(self):
        self.strict()
        self.fake.models = [x for x in ROWS if x['slug'] != 'gpt-6-luna']
        rows = catalogue5682.refresh(self.desk, 'chatgpt')
        self.assertEqual(next(r for r in rows if r['slug'] == 'gpt-6-luna')['statut'], 'retire')
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['modele'], 'gpt-6.1-terra')

    def test_cg06_choix_force_retire_ignore(self):
        self.strict(modeles={'chatgpt': {'econome': 'ancien-modele'}})
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['modele'], 'gpt-6-luna')

    def test_cg07_effort_refuse_une_seule_correction(self):
        refused = chatgpt5662.HTTPFailure(400, 'subscription_sharing_unsupported_capability', 'non admis', 'reasoning.effort')
        self.strict(Fake([refused, TRIAGE_OK]))
        self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(['reasoning' in x for x in self.fake.sent], [True, False])
        row = next(r for r in catalogue5682.entries(self.desk.c['state_dir'], 'chatgpt') if r['slug'] == 'gpt-6-luna')
        self.assertEqual((row['capacites']['efforts'], row['capacites']['preuve']), ([], 'refus_fournisseur'))
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['effort'], '')

    def test_cg07_liste_blanche_des_parametres(self):
        req = chatgpt5662.build_request('gpt-6-luna', [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}], None, True, 'low')
        self.assertEqual(sorted(req), ['input', 'instructions', 'model', 'reasoning', 'store', 'stream'])
        self.assertEqual(chatgpt5682.clean_request({'model': 'm', 'temperature': 0, 'top_p': 1, 'max_output_tokens': 9, 'store': False}), {'model': 'm', 'store': False})

    def test_cg08_limite_pendant_le_flux_aucun_succes_partiel_puis_report(self):
        failed = ['data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': 'début'}),
                  'data: ' + json.dumps({'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}})]
        self.strict(Fake([io.BytesIO(('\n'.join(failed) + '\n').encode())]))
        with self.assertRaises(Stop) as ctx:
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(str(ctx.exception), 'chatgpt_limite_usage_atteinte')
        self.assertEqual(len(self.fake.sent), 1)
        with self.assertRaises(Stop):
            routed_config(self.desk.c, 'mail_triage')        # report : aucun nouvel appel
        self.assertEqual(len(self.fake.sent), 1)

    def test_cg08_report_borne_et_progressif(self):
        self.strict()
        state = self.desk.c['state_dir']
        start = datetime.now(timezone.utc)
        spans = [(datetime.fromisoformat(chatgpt5682.defer(state, 'chatgpt_limite_usage_atteinte', start)['jusqu_a']) - start).total_seconds() / 60 for _ in range(7)]
        self.assertEqual(spans, [15, 30, 60, 120, 240, 240, 240])
        chatgpt5682.clear(state)
        self.assertIsNone(chatgpt5682.deferred(state))

    def test_cg08_travail_reporte_sans_nouvelle_ligne(self):
        from agent.ia5672 import TRANSIENT
        self.assertTrue(chatgpt5682.DEFER_CODES <= TRANSIENT)
        state = self.desk.c['state_dir']
        chatgpt5682.defer(state, 'chatgpt_limite_usage_atteinte')
        job = self.desk.enqueue('health', {'n': '1'})
        before = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        with patch('agent.desk.Desk.perform', side_effect=Stop('chatgpt_limite_usage_atteinte')):
            self.desk.work_once()
        row = self.desk.db.execute('SELECT status,not_before,blocked_by,attempts FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual((row['status'], row['blocked_by'], row['attempts']), ('pending', 'chatgpt_limite_usage_atteinte', 0))
        self.assertEqual(row['not_before'], chatgpt5682.deferred(state)['jusqu_a'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)
        with patch('agent.desk.Desk.perform', side_effect=AssertionError('non échu : pas encore repris')):
            self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'pending')

    def test_cg08_strict_sans_autre_fournisseur(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        self.strict(Fake([Stop('chatgpt_indisponible')]))
        calls = []

        def child(cfg, method, *args):
            calls.append(cfg['provider_id'])
            raise Stop('chatgpt_indisponible')
        parent = self.model()
        with patch.object(parent, '_call_child', child), self.assertRaises(Stop):
            parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(calls, ['chatgpt'])

    def test_consentement_retire_aucun_appel(self):
        self.strict()
        value = politique5682.normalize(self.desk.settings(politique5682.KEY))
        value['consentement'] = False
        self.desk.setting(politique5682.KEY, value)
        with self.assertRaises(Stop) as ctx:
            routed_config(self.desk.c, 'assistant')
        self.assertEqual(str(ctx.exception), 'politique_externe_non_autorisee')
        self.assertEqual(self.fake.sent, [])

    def test_cg09_jeton_revoque_aucun_changement_de_fournisseur(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        self.strict(Fake([chatgpt5662.HTTPFailure(401, 'subscription_sharing_invalid_user')]))
        with patch.object(chatgpt5662, 'access_token', side_effect=['j', Stop('chatgpt_reconnexion_requise')]):
            with self.assertRaises(Stop) as ctx:
                routage5682.run(self.model(), [{'role': 'user', 'content': 'x'}], 0, 100, None, 'triage', {})
        self.assertEqual(str(ctx.exception), 'chatgpt_reconnexion_requise')

    def test_cg10_abonnement_ni_gratuit_ni_illimite(self):
        self.strict()
        html = ia5682.card(self.desk).lower()
        self.assertNotIn('gratuit', html)
        self.assertNotIn('illimit', html)
        self.assertIn('utilise votre abonnement chatgpt', html)
        self.assertIn('non fournie', html)
        provider = self.desk.settings('ai:provider:chatgpt')
        self.assertEqual(provider['canal'], 'abonnement')
        self.assertNotIn('pricing_confirmed_free', provider)

    def test_cg10_ancienne_marque_gratuite_remplacee(self):
        config = {}
        ai_gateway._load_rows(config, [{'key': 'ai:provider:chatgpt', 'value': json.dumps({'type': 'chatgpt', 'pricing_confirmed_free': True})}])
        self.assertEqual(config['ai_providers']['chatgpt'].get('canal'), 'abonnement')
        self.assertNotIn('pricing_confirmed_free', config['ai_providers']['chatgpt'])

    def test_cg10_usage_inconnu_reste_inconnu(self):
        self.assertIsNone(conso5682.amount((2.5, 0.25, 12.5, 'credits'), conso5682.fields({})))
        used = conso5682.fields(OK_USAGE)
        self.assertEqual(used, {'entree': 1000, 'entree_cache': 400, 'sortie': 200, 'raisonnement': 50})
        self.assertEqual(conso5682.amount((2.5, 0.25, 12.5, 'credits'), used), round((600 * 2.5 + 400 * 0.25 + 200 * 12.5) / 1e6, 6))    # raisonnement non recompté

    def test_cg11_annulation_sans_escalade(self):
        self.strict(Fake([Stop('operation_annulee')]))
        with self.assertRaises(Stop):
            self.model().ask('triage', {'subject': 'Objet fictif'})
        self.assertEqual(len(routage5682.recent(self.desk.c['state_dir'])), 1)

    def test_cg13_credits_desactives_par_defaut_aucun_achat(self):
        self.assertFalse(politique5682.defaults()['credits_autorises'])
        self.strict()
        self.assertIn('n’achète jamais de crédits', ia5682.card(self.desk))

    def test_cg14_recherche_textuelle_sans_modele_cache(self):
        self.strict()
        state = self.desk.c['state_dir']
        self.assertFalse(politique5682.embeddings_allowed(state))
        index = DocumentIndex(state, {'enabled': True, 'embedding_model': 'modele-fictif'}, {'url': 'http://127.0.0.1:1'})
        try:
            self.assertIsNone(index.embedder())
            self.assertEqual(index.ensure_embeddings('DOS-001')['created'], 0)
        finally:
            index.db.close()
        politique5682.save(self.desk, {'mode': 'chatgpt', 'consentement': True, 'semantique_locale': True}, 'avocat')
        self.assertTrue(politique5682.embeddings_allowed(state))


class Liaison(Strict):
    def test_cg12_transfert_vers_le_serveur(self):
        self.strict()
        host_server = 'urn:uuid:serveur-fictif'
        out = chatgpt5682.export_session(self.desk, 'avocat', 'phrase de passe fictive')
        self.assertTrue(out['paquet'].startswith(chatgpt5682.BUNDLE_PREFIX))
        self.assertNotIn('jeton-renouvellement-fictif', out['paquet'])
        self.assertEqual(chatgpt5662.load_record(self.desk.c['state_dir'])['refresh_token'], '')       # le poste ne renouvelle plus
        self.desk.setting(chatgpt5662.HOST_KEY, host_server)
        with self.assertRaises(Stop) as ctx:
            chatgpt5682.import_session(self.desk, 'avocat', out['paquet'], 'autre phrase fictive')
        self.assertEqual(str(ctx.exception), 'paquet_session_invalide')
        chatgpt5682.import_session(self.desk, 'avocat', out['paquet'], 'phrase de passe fictive')
        record = chatgpt5662.load_record(self.desk.c['state_dir'])
        self.assertEqual(record['ext_agent_host_id'], host_server)
        self.assertEqual(record['refresh_token'], 'jeton-renouvellement-fictif')
        self.assertEqual(chatgpt5662.account(self.desk)['etat'], 'connecte')

    def test_cg12_phrase_trop_courte(self):
        self.strict()
        with self.assertRaises(Stop):
            chatgpt5682.export_session(self.desk, 'avocat', 'court')


class ApiMistralClaude(base.Base):
    def setup_api(self, kind, ids):
        pid = {'mistral': 'mistral', 'anthropic': 'anthropic'}[kind]
        url = {'mistral': 'https://api.mistral.ai/v1', 'anthropic': 'https://api.anthropic.com/v1'}[kind]
        ai_gateway.save_provider(self.desk, pid, kind, url, ids[0], 'cle-fictive', True, True)
        with patch('agent.modeles5681._fetch', return_value={'data': [{'id': i} for i in ids]}):
            politique5682.save(self.desk, {'mode': kind, 'consentement': True}, 'avocat')

    def test_mistral_petit_pour_le_simple_grand_pour_le_complexe(self):
        self.setup_api('mistral', ['mistral-small-latest', 'mistral-large-4'])
        pick = lambda p, s: routage5682.decide(self.desk.c, p, s, {'text': 'x'})
        self.assertEqual(pick('mail_triage', 'triage')['modele'], 'mistral-small-latest')
        self.assertEqual(pick('legal_analysis', 'strategy_analysis')['modele'], 'mistral-large-4')
        self.assertEqual(pick('mail_drafting', 'manual_draft')['modele'], 'mistral-large-4')
        self.assertEqual(pick('mail_triage', 'triage')['effort'], '')                     # pas de réglage d'effort sur cette voie
        self.assertIn('Effort non réglable', pick('mail_triage', 'triage')['raison_courte'])

    def test_claude_trois_niveaux(self):
        self.setup_api('anthropic', ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1'])
        pick = lambda p, s: routage5682.decide(self.desk.c, p, s, {'text': 'x'})
        self.assertEqual(pick('mail_triage', 'triage')['modele'], 'claude-haiku-5-5')
        self.assertEqual(pick('mail_drafting', 'manual_draft')['modele'], 'claude-sonnet-5-5')
        chosen = pick('legal_analysis', 'strategy_analysis')
        self.assertEqual((chosen['modele'], chosen['effort'], chosen['unite']), ('claude-opus-5-5', 'high', 'usd'))
        self.assertEqual(chosen['prix'][0], 4.0)

    def test_claude_sans_temperature_avec_effort(self):
        sent = {}

        class Http:
            headers = {}

            def json(self, method, path, body=None):
                sent.update(body)
                return {'content': [{'type': 'text', 'text': 'réponse fictive'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        model = Model.__new__(Model)
        model.cfg = {'model': 'claude-opus-5-5', 'effort5682': 'high', 'state_dir': ''}
        model.http, model.usage_cost_usd, model.usage_cost_known = Http(), 0.0, True
        with patch('agent.ai_gateway.estimate_and_check_budget', return_value=0.0), patch('agent.ai_gateway.record_usage', return_value=0.0):
            model._anthropic([{'role': 'user', 'content': 'x'}], 0, 3500, None)
        self.assertNotIn('temperature', sent)
        self.assertEqual(sent['output_config'], {'effort': 'high'})
        self.assertEqual(sent['max_tokens'], 8000)
        model.cfg['model'] = 'claude-sonnet-4-5'
        with patch('agent.ai_gateway.estimate_and_check_budget', return_value=0.0), patch('agent.ai_gateway.record_usage', return_value=0.0):
            model._anthropic([{'role': 'user', 'content': 'x'}], 0, 3500, None)
        self.assertEqual(sent['temperature'], 0.0)

    def test_claude_refus_du_modele(self):
        class Http:
            headers = {}

            def json(self, method, path, body=None):
                return {'content': [], 'stop_reason': 'refusal'}
        model = Model.__new__(Model)
        model.cfg = {'model': 'claude-opus-5-5', 'state_dir': ''}
        model.http, model.usage_cost_usd, model.usage_cost_known = Http(), 0.0, True
        with patch('agent.ai_gateway.estimate_and_check_budget', return_value=0.0), patch('agent.ai_gateway.record_usage', return_value=0.0):
            with self.assertRaises(Stop) as ctx:
                model._anthropic([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(str(ctx.exception), 'reponse_ia_refusee')

    def test_slug_jamais_fabrique(self):
        self.setup_api('anthropic', ['claude-sonnet-5-5'])
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['modele'], 'claude-sonnet-5-5')
        self.assertEqual(catalogue5682.level_of('anthropic', 'claude-haiku-9'), 'econome')
        self.assertIsNone(catalogue5682.reference('anthropic', 'claude-haiku-9'))

    def test_accord_requis(self):
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', False, False)
        with self.assertRaises(Stop) as ctx:
            politique5682.save(self.desk, {'mode': 'mistral', 'consentement': False}, 'avocat')
        self.assertEqual(str(ctx.exception), 'autorisation_donnees_externes_requise')


class Automatique(Strict):
    def auto(self, fake=None):
        self.fake = fake or Fake()
        http = patch.object(chatgpt5662, 'http', self.fake)
        http.start()
        self.addCleanup(http.stop)
        connect(self.desk)
        catalogue5682.refresh(self.desk, 'chatgpt')
        chatgpt5662.save_provider(self.desk, 'avocat', 'gpt-6-luna', True, True)
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', 'https://api.mistral.ai/v1', 'mistral-large-4', 'cle-fictive', True, True)
        with patch('agent.modeles5681._fetch', return_value={'data': [{'id': 'mistral-small-latest'}, {'id': 'mistral-large-4'}]}):
            politique5682.save(self.desk, {'mode': 'auto', 'consentement': True, 'fournisseurs': ['chatgpt', 'mistral']}, 'avocat')

    def test_abonnement_avant_api_a_niveau_egal(self):
        self.auto()
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['type_fournisseur'], 'chatgpt')

    def test_limite_repli_dans_la_liste(self):
        self.auto()
        chatgpt5682.defer(self.desk.c['state_dir'], 'chatgpt_limite_usage_atteinte')
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['type_fournisseur'], 'mistral')

    def test_panne_repli_une_autre_connexion_cochee(self):
        self.auto(Fake([Stop('chatgpt_indisponible')]))
        calls = []

        def child(cfg, method, *args):
            calls.append(cfg['provider_id'])
            if cfg['provider_id'] == 'chatgpt':
                raise Stop('chatgpt_indisponible')
            return TRIAGE_OK
        parent = self.model()
        with patch.object(parent, '_call_child', child):
            self.assertEqual(json.loads(parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None))['intent'], 'status')
        self.assertEqual(calls, ['chatgpt', 'mistral'])

    def test_annulation_aucun_repli(self):
        self.auto()
        calls = []

        def child(cfg, method, *args):
            calls.append(cfg['provider_id'])
            raise Stop('operation_annulee')
        parent = self.model()
        with patch.object(parent, '_call_child', child), self.assertRaises(Stop):
            parent.complete([{'role': 'user', 'content': 'x'}], 0, 100, None)
        self.assertEqual(calls, ['chatgpt'])

    def test_fournisseur_non_coche_jamais_candidat(self):
        self.auto()
        rule = politique5682.read_fresh(self.desk.c['state_dir'])
        politique5682.save(self.desk, {'mode': 'auto', 'consentement': True, 'fournisseurs': ['chatgpt']}, 'avocat')
        chatgpt5682.defer(self.desk.c['state_dir'], 'chatgpt_limite_usage_atteinte')
        with self.assertRaises(Stop):
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertIn('mistral', rule['fournisseurs'])

    def test_dossier_exclu_reste_refuse_sans_local(self):
        self.strict()
        self.desk.c['hybrid_routing'] = {'mode': 'manual', 'excluded_matters': ['DOS-001']}
        with patch.object(routage5682, '_matters', return_value={'DOS-001'}), self.assertRaises(Stop) as ctx:
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'matter': 'DOS-001'})
        self.assertEqual(str(ctx.exception), 'politique_externe_dossier_exclu')

    def test_service_deja_lance_suit_le_changement_de_mode(self):
        self.strict()
        stale = {k: v for k, v in self.desk.c.items() if k not in ('politique_ia', 'ai_providers')}
        self.assertTrue(routed_config(stale, 'assistant')['routeur5682'])


class Sobriete(Strict):
    def job(self, automatic=True):
        from agent.arret5643 import job_context
        return job_context(self.desk.c['state_dir'], 1, 'DOS-001', automatic)

    def test_plafond_journalier_automatique(self):
        self.strict(plafond_auto_jour=2)
        with self.job():
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
            with self.assertRaises(Stop) as ctx:
                routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertEqual(str(ctx.exception), sobriete5682.CODE)
        self.assertEqual(sobriete5682.counters(self.desk.c['state_dir']), (2, 0))

    def test_plafond_des_modeles_avances(self):
        self.strict(plafond_auto_avance_jour=1)
        with self.job():
            routage5682.decide(self.desk.c, 'legal_analysis', 'strategy_analysis', {'text': 'x'})
            with self.assertRaises(Stop):
                routage5682.decide(self.desk.c, 'legal_analysis', 'strategy_analysis', {'text': 'x'})
            self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['modele'], 'gpt-6-luna')    # le tri continue

    def test_demandes_de_l_avocat_jamais_plafonnees(self):
        self.strict(plafond_auto_jour=1, plafond_auto_avance_jour=1)
        with self.job():
            routage5682.decide(self.desk.c, 'legal_analysis', 'strategy_analysis', {'text': 'x'})
            with self.assertRaises(Stop):
                routage5682.decide(self.desk.c, 'legal_analysis', 'strategy_analysis', {'text': 'x'})
        for _ in range(4):        # plafond atteint : l'avocat reste servi, et ses demandes ne comptent pas
            routage5682.decide(self.desk.c, 'legal_analysis', 'strategy_analysis', {'text': 'x'})
        self.assertEqual(sobriete5682.counters(self.desk.c['state_dir']), (1, 1))

    def test_travail_plafonne_attend_le_lendemain_sur_sa_ligne(self):
        job = self.desk.enqueue('health', {'n': '1'})
        with patch('agent.desk.Desk.perform', side_effect=Stop(sobriete5682.CODE)):
            self.desk.work_once()
        row = self.desk.db.execute('SELECT status,not_before FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(row['status'], 'pending')
        self.assertGreater(row['not_before'], datetime.now(timezone.utc).isoformat())
        self.assertEqual(row['not_before'], sobriete5682.tomorrow())

    def test_releve_sans_lecture_quand_le_plafond_est_atteint(self):
        self.strict(plafond_auto_jour=1)
        self.assertEqual(sobriete5682.hold(self.desk.c), '')
        with self.job():
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertEqual(sobriete5682.hold(self.desk.c), sobriete5682.CODE)

    def test_limite_chatgpt_suspend_le_releve(self):
        self.strict()
        chatgpt5682.defer(self.desk.c['state_dir'], 'chatgpt_limite_usage_atteinte')
        self.assertEqual(sobriete5682.hold(self.desk.c), 'chatgpt_limite_usage_atteinte')

    def test_tri_reutilise_sans_second_appel(self):
        triage = {'needs_reply': True, 'intent': 'legal', 'exclude_category': 'none', 'ambiguous': False, 'reason': 'motif fictif'}
        out = sobriete5682.classification_from_triage(triage, ['incoming', 'p1'])
        from agent.model import MAIL_ORCHESTRATION_CLASSIFICATION, validate
        validate(out, MAIL_ORCHESTRATION_CLASSIFICATION)
        self.assertTrue(out['actionable'] and out['needs_legal_analysis'])
        self.assertEqual(out['source_ids'], ['incoming'])
        pub = sobriete5682.classification_from_triage({**triage, 'exclude_category': 'advertisement'}, ['incoming'])
        self.assertFalse(pub['actionable'])
        self.assertIsNone(sobriete5682.classification_from_triage({}, ['incoming']))

    def test_trois_echecs_arretent_les_appels_pour_ce_courriel(self):
        for _ in range(2):
            sobriete5682.note_mail_failure(self.desk, 'a' * 64, 'source_orchestration_invalide')
        self.assertEqual(sobriete5682.mail_failures(self.desk), set())
        sobriete5682.note_mail_failure(self.desk, 'a' * 64, 'source_orchestration_invalide')
        self.assertEqual(sobriete5682.mail_failures(self.desk), {'a' * 64})

    def test_balayage_s_arrete_a_la_premiere_panne_et_compte_les_echecs(self):
        from agent import orchestrator
        rows = [{'mail_key': 'a' * 64}, {'mail_key': 'b' * 64}, {'mail_key': 'c' * 64}]
        calls = []

        def run(desk, args):
            calls.append(args['mail_key'])
            raise Stop('fournisseur_ia_injoignable' if args['mail_key'][0] == 'b' else 'source_orchestration_invalide')
        with patch('agent.balayage5611.recent_work_items', return_value=rows), patch.object(orchestrator, 'orchestrate_mail', run):
            orchestrator.sweep(self.desk, {})
        self.assertEqual([c[0] for c in calls], ['a', 'b'])
        self.assertEqual(self.desk.db.execute('SELECT essais FROM orchestration_echecs_5682').fetchall()[0][0], 1)
        for _ in range(2):
            sobriete5682.note_mail_failure(self.desk, 'a' * 64, 'source_orchestration_invalide')
        calls.clear()
        with patch('agent.balayage5611.recent_work_items', return_value=rows[:1]), patch.object(orchestrator, 'orchestrate_mail', run):
            orchestrator.sweep(self.desk, {})
        self.assertEqual(calls, [])                # trois échecs : plus aucun appel pour ce courriel

    def test_fichiers_produits_par_axiorhub_ignores(self):
        self.assertTrue(sobriete5682.own_file('/Dossiers/DEMO/90_AxiorHub_Brouillons/projet.docx'))
        self.assertFalse(sobriete5682.own_file('/Dossiers/DEMO/20_Actes_et_conclusions/conclusions.docx'))

    def test_synthese_regroupee(self):
        self.assertEqual(sobriete5682.brief_after(self.desk, 'DOS-001'), '')
        stamp = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,matter_ref) VALUES('refresh_brief','{}','done',?,?,'DOS-001')", (stamp, stamp))
        self.desk.db.commit()
        self.assertGreater(sobriete5682.brief_after(self.desk, 'DOS-001'), stamp)

    def test_une_seule_preparation_d_audience(self):
        self.assertFalse(sobriete5682.hearing_prepared(self.desk, 'DOS-001'))
        stamp = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,matter_ref) VALUES('prepare_hearing','{}','pending',?,'DOS-001')", (stamp,))
        self.desk.db.commit()
        self.assertTrue(sobriete5682.hearing_prepared(self.desk, 'DOS-001'))

    def test_dossier_inactif_et_strategie_ancienne_sans_analyse_automatique(self):
        source = (Path(model_mod.__file__).parent / 'production420.py').read_text(encoding='utf-8')
        self.assertNotIn("'document_type':'note_interne'", source)
        self.assertNotIn("'objective':", source)
        self.assertNotIn('stale_strategy', (Path(model_mod.__file__).parent / 'production390.py').read_text(encoding='utf-8'))

    def test_analyse_d_un_courriel_envoye_seulement_sur_demande(self):
        self.assertNotIn("enqueue('memory_insight'", (Path(model_mod.__file__).parent / 'memory.py').read_text(encoding='utf-8'))

    def test_non_lus_bornes_par_defaut(self):
        from agent import mailbox
        self.assertEqual(mailbox.DEFAULT_UNSEEN_DAYS, 14)

    def test_liste_des_modeles_au_plus_une_fois_par_heure(self):
        calls = []

        class Http:
            def __init__(self, *a, **k):
                self.headers = {}

            def json(self, method, path, body=None):
                calls.append(path)
                return {'data': []}
        model_mod.LISTED.clear()
        cfg = {'provider_type': 'mistral', 'provider_id': 'mistral', 'url': 'https://api.mistral.ai/v1', 'external_data_allowed': True, 'secret_file': 'x'}
        with patch('agent.model.HTTP', Http), patch('agent.common.read_secret', return_value='cle-fictive'):
            Model(cfg)
            Model(cfg)
            self.assertEqual(calls, ['/models'])
            Model({**cfg, 'diagnostic5682': True})
            self.assertEqual(calls, ['/models', '/models'])


class Refonte(base.Base):
    def test_menu_du_cahier(self):
        from agent import fonctions5631, navigation5643, shell501
        self.assertEqual([p for p, *_ in shell501.PRIMARY], ['/aujourdhui', '/courriels', '/dossiers', '/planning', '/production', '/facturation'])
        self.assertEqual(fonctions5631.PRIMARY, navigation5643.PRIMARY)
        self.assertEqual(navigation5643.visibility('parametres'), 'primary')      # lien propre, sans doublon dans Toutes les fonctions
        self.assertEqual(navigation5643.visibility('facturation'), 'primary')

    def test_bibliotheques_servies_localement(self):
        from agent import web440
        root = Path(model_mod.__file__).parent / 'static'
        for name in ('carbon5682.js', 'pdfjs5682.js', 'pdf.worker5682.js'):
            self.assertIn('/static/' + name, web440.STATIC)
            self.assertGreater((root / name).stat().st_size, 100000)
        js = (root / 'v5682.js').read_text(encoding='utf-8')
        self.assertNotRegex(js, r'https?://')

    def test_chronologie_ne_fabrique_pas_de_jour(self):
        js = (Path(model_mod.__file__).parent / 'static' / 'v5682.js').read_text(encoding='utf-8')
        self.assertIn("p === 'annee'", js)
        self.assertIn("p === 'mois'", js)

    def test_notification_carbon_de_limite(self):
        chatgpt5682.defer(self.desk.c['state_dir'], 'chatgpt_limite_usage_atteinte')
        html = ia5682.banner_html(self.desk, '')
        self.assertIn('cds-inline-notification', html)
        self.assertIn('Limite d’usage atteinte', html)
        self.assertIn('Gérer l’usage', html)


class Bordereau(test_v510.Base):
    def payload(self, did):
        return [{'key': r['key'], 'include': True, 'title': r['title'], 'date': r['date']} for r in self.rows(did)]

    def test_ux08_modification_concurrente_sans_ecrasement(self):
        did = self.scan()
        rows = list(reversed(self.payload(did)))
        p5.update_table(self.desk, did, rows, revision=0)
        order = [r['key'] for r in p5.get(self.desk, did)['data']['rows']]
        self.assertEqual(p5.get(self.desk, did)['data']['revision'], 1)
        with self.assertRaises(Stop) as ctx:
            p5.update_table(self.desk, did, list(reversed(rows)), revision=0)        # page restée ouverte sur l'ancienne version
        self.assertEqual(str(ctx.exception), 'version_en_conflit')
        self.assertEqual([r['key'] for r in p5.get(self.desk, did)['data']['rows']], order)

    def test_ux07_tableau_affiche_annulation_et_version(self):
        did = self.scan()
        p5.update_table(self.desk, did, self.payload(did), revision=0)
        html = web510._table_html(p5.get(self.desk, did))
        self.assertIn('id="m82-undo"', html)
        self.assertIn('data-revision="1"', html)
        self.assertIn('data-draft="%s"' % did, html)


class Migration(base.Base):
    def test_mise_a_jour_garde_le_mode_configure(self):
        self.assertIsNone(politique5682.read(self.desk.c))
        self.assertEqual(routed_config(self.desk.c, 'mail_triage').get('provider_type'), 'ollama')

    def test_mode_automatique_limite_a_la_liste(self):
        rule = politique5682.normalize({'mode': 'auto', 'fournisseurs': ['ollama']})
        self.assertTrue(politique5682.is_allowed(rule, 'ollama', 'ollama'))
        self.assertFalse(politique5682.is_allowed(rule, 'mistral', 'mistral'))

    def test_mode_local_ferme_tout_envoi(self):
        politique5682.save(self.desk, {'mode': 'local'}, 'avocat')
        cfg = {'provider_type': 'mistral', 'provider_id': 'mistral', 'state_dir': self.desk.c['state_dir'], 'url': 'https://api.mistral.ai/v1'}
        with self.assertRaises(Stop) as ctx:
            Model(cfg)
        self.assertEqual(str(ctx.exception), 'fournisseur_interdit_par_politique')

    def test_mode_mixte_retour_aux_reglages_par_fonction(self):
        politique5682.save(self.desk, {'mode': 'local'}, 'avocat')
        from agent import ia540
        ia540._reset_policy(self.desk)
        self.assertIsNone(politique5682.read(self.desk.c))

    def test_route_explicite_autorisee_sans_seuil_hybride(self):
        from agent.hybrid400 import normalize
        config = {'hybrid_routing': normalize({'mode': 'manual'}), 'matters_file': '', 'model_routing': {'mail_drafting': {'provider': 'mistral', 'model': 'mistral-large-4'}}}
        cfg = {'provider_type': 'mistral', 'provider_id': 'mistral', 'purpose': 'mail_drafting', 'external_policy_config': config, 'external_data_allowed': True}
        check_external(cfg, messages=[{'role': 'user', 'content': 'x'}])
        with self.assertRaises(Stop):
            check_external({**cfg, 'purpose': 'assistant'}, messages=[{'role': 'user', 'content': 'x'}])
        with self.assertRaises(Stop):
            check_external({**cfg, 'external_data_allowed': False}, messages=[{'role': 'user', 'content': 'x'}])

    def test_dossier_exclu_refuse_en_mode_strict(self):
        from agent.hybrid400 import normalize
        config = {'hybrid_routing': normalize({'mode': 'manual', 'excluded_matters': ['DOS-001']}), 'matters_file': '',
                  'politique_ia': {'mode': 'chatgpt', 'consentement': True}}
        cfg = {'provider_type': 'chatgpt', 'provider_id': 'chatgpt', 'purpose': 'assistant', 'external_policy_config': config, 'external_data_allowed': True}
        with self.assertRaises(Stop) as ctx:
            check_external(cfg, payload={'matter': 'DOS-001'})
        self.assertEqual(str(ctx.exception), 'politique_externe_dossier_exclu')


class Interface(base.Base):
    def test_ux01_faute_dans_un_nom(self):
        self.assertGreaterEqual(recherche5682.score('durnad', 'DURAND c/ MARTIN'), recherche5682.MIN_SCORE)
        self.assertLess(recherche5682.score('zzzz', 'DURAND c/ MARTIN'), recherche5682.MIN_SCORE)

    def test_ux01_dossier_reserve_jamais_propose(self):
        matters = [{'id': 'DOS-001', 'client_name': 'DURAND', 'path': '/d'}, {'id': 'DOS-002', 'client_name': 'DURAND', 'path': '/e'}]
        with patch('agent.recherche5682.load_matters', return_value=matters), patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-002'})):
            out = recherche5682.search(self.desk, 'durand', 'membre', 'utilisateur')
        urls = [r['url'] for r in out['resultats']]
        self.assertIn('/dossier?id=DOS-001', urls)
        self.assertNotIn('/dossier?id=DOS-002', urls)

    def test_ressources_declarees(self):
        from agent import shell501, web440
        self.assertIn('/static/v5682.js', web440.STATIC)
        self.assertIn('v5682.css', shell501.BUNDLE)
        self.assertIn('m5682/', __import__('agent.standalone_auth', fromlist=['x']).ADMIN_API)
        self.assertIn('data-m82-quick', shell501.topbar_html('', 'Essai'))

    def test_jeton_jamais_dans_le_stockage_du_navigateur(self):
        js = (Path(model_mod.__file__).parent / 'static' / 'v5682.js').read_text(encoding='utf-8')
        self.assertFalse([l for l in js.splitlines() if 'Storage' in l and ('paquet' in l or 'jeton' in l)])


if __name__ == '__main__':
    unittest.main()
