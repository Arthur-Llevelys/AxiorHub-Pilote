"""5.6.96 (lot mistral, A01 à A03 et partie Mistral de la recette A08) : clé Studio Mistral testée, puis publiée en une transaction ;
limites et pannes du fournisseur traitées sans repli dans un mode exclusif.

Aucun appel réel à Mistral, aucune clé réelle : le transport est factice (``FauxMistral``) et les clés sont des chaînes d'essai.
Ces tests contrôlent le circuit logiciel ; ils ne qualifient ni le contrat réel de l'API Mistral ni une offre réelle.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_v5648 as login
import test_v5652 as base
from agent import ai_gateway, catalogue5682, chatgpt5682, ia5682, mistral5696, modeles5681, politique5682, routage5682, sobriete5682
from agent.cles5696 import _http as HTTP_REEL      # transport réel, gardé avant le remplacement des essais
from agent.common import Stop, read_secret
from agent.erreurs5634 import http_error

API = 'https://api.mistral.ai/v1'
CLE_EN_SERVICE = 'cle-essai-en-service-0001'
CLE_NOUVELLE = 'cle-essai-nouvelle-0002'
CLE_REFUSEE = 'cle-essai-refusee-0003'
MODELES = [{'id': 'mistral-small-latest', 'capabilities': {'completion_chat': True}, 'max_context_length': 128000},
           {'id': 'mistral-large-4', 'capabilities': {'completion_chat': True}},
           {'id': 'mistral-embed', 'capabilities': {'completion_chat': False}},
           {'id': 'modele-de-demain'}]
MODELES_CLAUDE = [{'type': 'model', 'id': 'claude-sonnet-5-5', 'display_name': 'Claude Sonnet 5.5', 'lifecycle': 'active', 'max_input_tokens': 200000},
                  {'type': 'model', 'id': 'claude-haiku-5-5', 'display_name': 'Claude Haiku 5.5', 'lifecycle': 'active'},
                  {'type': 'model', 'id': 'claude-ancien-1', 'lifecycle': 'retired'}]
SANS_CONVERSATION = [{'id': 'mistral-embed', 'capabilities': {'completion_chat': False}}, {'id': 'mistral-ocr-latest', 'capabilities': {'completion_chat': False}}]


class FauxMistral:
    """Transport factice : catalogue et réponses programmés par clé ; appels conservés (méthode, chemin, clé reconnue ou non)."""

    def __init__(self, valides=(CLE_EN_SERVICE, CLE_NOUVELLE), modeles=None):
        self.valides, self.modeles, self.refus, self.appels = set(valides), list(modeles or MODELES), {}, []

    def __call__(self, methode, chemin, cle, corps=None):
        self.appels.append((methode, chemin, cle, corps))
        rule = self.refus.get(cle)
        if rule == 'reseau':
            raise Stop('connexion_http_indisponible')
        if rule == 'vide':
            return {'object': 'list', 'data': SANS_CONVERSATION}
        if isinstance(rule, tuple):
            status, body, retry = rule
            raise http_error(status, methode, API + chemin, body, retry)
        if cle not in self.valides:
            raise http_error(401, methode, API + chemin, b'{"message":"Unauthorized"}')
        if methode == 'GET':
            return {'object': 'list', 'data': self.modeles}
        return {'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]}

    def fetch(self, provider):
        """Remplace ``modeles5681._fetch`` (relecture du catalogue avec la clé enregistrée)."""
        return self('GET', '/models', read_secret(provider['secret_file']))


REFUS = {'401': (401, b'{"message":"Unauthorized"}', None), '403': (403, b'{}', None), '429': (429, b'{"type":"rate_limited"}', '120'),
         '500': (503, b'{}', None)}


def snapshot(desk, pid='mistral'):
    """Connexion en service : réglage et catalogue en base, clé lue dans le coffre, configuration en mémoire, fichiers du coffre."""
    provider = desk.settings('ai:provider:' + pid)
    vault = sorted(p.name for p in ai_gateway._vault_dir(desk).iterdir() if p.name.endswith('.secret'))
    return {'reglage': provider, 'catalogue': [(r['slug'], r['statut']) for r in (desk.settings(catalogue5682.KEY + pid, {}) or {}).get('modeles', [])],
            'cle': read_secret(provider['secret_file']) if provider else None, 'memoire': dict(desk.c.get('ai_providers', {}).get(pid) or {}),
            'coffre': vault, 'politique': desk.settings(politique5682.KEY)}


class Base(base.Base):
    def setUp(self):
        super().setUp()
        self.mistral = FauxMistral()
        faux = self.mistral

        class Transport:
            """Remplace ``HTTP`` dans ``agent.model`` : le test de génération passe par ``Model`` (politique, budget), jamais par le réseau."""
            def __init__(self, base, **kw):
                self.headers = {}

            def json(self, methode, chemin, corps=None):
                return faux(methode, chemin, self.headers.get('Authorization', '')[len('Bearer '):], corps)
        self.claude = FauxMistral((CLE_EN_SERVICE, CLE_NOUVELLE), MODELES_CLAUDE)
        transports = lambda base, kind='mistral': self.claude if kind == 'anthropic' else self.mistral
        fetch = lambda provider: (self.claude if provider.get('type') == 'anthropic' else self.mistral).fetch(provider)
        for target, value in (('agent.cles5696._http', transports), ('agent.modeles5681._fetch', fetch),
                              ('agent.model.HTTP', Transport)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    def en_service(self, mode=True):
        """Connexion en service : clé valide, catalogue lu, mode « API Mistral uniquement » si demandé."""
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', API, 'mistral-large-4', CLE_EN_SERVICE, True, True, False, 20, 0.2, 0.7, 2.1)
        if mode:
            politique5682.save(self.desk, {'mode': 'mistral', 'consentement': True}, 'admin@example.test')
        else:
            catalogue5682.refresh(self.desk, 'mistral')
        return snapshot(self.desk)


# ---------------------------------------------------------------------------------------------------- A02 : test avant tout effet
class CleRefusee(Base):
    def test_A02_A08_cle_refusee_par_le_formulaire_du_mode_laisse_la_connexion_intacte(self):
        """Défaut 5.6.95 : la clé était enregistrée avant la lecture du catalogue. 401, 403, 429, 5xx, réseau, catalogue vide."""
        avant = self.en_service()
        for cas in ('401', '403', '429', '500', 'reseau', 'vide'):
            with self.subTest(cas=cas):
                self.mistral.refus[CLE_REFUSEE] = REFUS.get(cas, cas)
                with self.assertRaises(Stop):
                    politique5682.save(self.desk, {'mode': 'mistral', 'consentement': True, 'api_key': CLE_REFUSEE}, 'admin@example.test')
                self.assertEqual(snapshot(self.desk), avant)
                self.assertEqual(read_secret(ai_gateway.provider_registry(self.desk.c)['mistral']['secret_file']), CLE_EN_SERVICE)   # prochain appel
        self.assertNotIn(CLE_REFUSEE, json.dumps([r[0] for r in self.desk.db.execute('SELECT value FROM settings')]))
        self.assertNotIn(CLE_REFUSEE, json.dumps([r[0] for r in self.desk.db.execute('SELECT data FROM audit')]))
        self.assertFalse([c for c in self.mistral.appels if c[0] != 'GET'])        # aucun envoi : lecture du catalogue seulement

    def test_A02_mutation_ancien_ordre_detecte(self):
        """Mutation en mémoire : l'ordre de la 5.6.95 (clé enregistrée, puis catalogue) est rétabli ; la même vérification échoue."""
        avant = self.en_service()
        self.mistral.refus[CLE_REFUSEE] = REFUS['401']

        def ancien(desk, mode, data, owner):
            from agent.modeles5681 import DEFAULT_MONTHLY_USD, DEFAULT_REQUEST_USD
            pid, current = politique5682.provider_of(desk.c, mode)
            ai_gateway.save_provider(desk, pid, mode, current.get('url') or '', current.get('model'), str(data.get('api_key') or '').strip(), True, True,
                                     False, float(current.get('monthly_budget_usd') or DEFAULT_MONTHLY_USD),
                                     float(current.get('per_request_budget_usd') or DEFAULT_REQUEST_USD), 0, 0)
            if not [r for r in catalogue5682.refresh(desk, pid) if r['statut'] == 'actif']:
                raise Stop('catalogue_modeles_vide')
        with patch.object(politique5682, '_prepare_exclusive', ancien), self.assertRaises(Stop):
            politique5682.save(self.desk, {'mode': 'mistral', 'consentement': True, 'api_key': CLE_REFUSEE}, 'admin@example.test')
        apres = snapshot(self.desk)
        self.assertNotEqual(apres, avant)               # le défaut est bien vu par la vérification du test précédent
        self.assertEqual(apres['cle'], CLE_REFUSEE)

    def test_A02_A08_cle_refusee_par_le_service_direct(self):
        avant = self.en_service()
        for cas, code in (('401', 'authentification_fournisseur_refusee'), ('403', 'acces_fournisseur_refuse'), ('429', 'mistral_limite_a_preciser'),
                          ('500', 'mistral_catalogue_indisponible'), ('reseau', 'fournisseur_ia_injoignable'), ('vide', 'catalogue_modeles_vide')):
            with self.subTest(cas=cas):
                self.mistral.refus[CLE_REFUSEE] = REFUS.get(cas, cas)
                with self.assertRaises(Stop) as ctx:
                    mistral5696.enregistrer(self.desk, CLE_REFUSEE, avant['reglage']['revision'], 'admin@example.test')
                self.assertEqual(str(ctx.exception), code)
                self.assertEqual(snapshot(self.desk), avant)

    def test_A02_refus_apres_ecriture_du_candidat_efface_le_candidat(self):
        """Le second test (octets enregistrés) échoue : le fichier candidat est effacé, la clé en service reste."""
        avant = self.en_service()
        reponses = iter([None, (429, b'{}', '30')])

        def capricieux(methode, chemin, cle, corps=None):
            rule = next(reponses)
            if rule:
                raise http_error(rule[0], methode, API + chemin, rule[1], rule[2])
            return self.mistral(methode, chemin, cle, corps)
        with self.assertRaises(Stop):
            mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test', transport=capricieux)
        self.assertEqual(snapshot(self.desk), avant)

    def test_A02_capacites_de_conversation(self):
        utiles, inconnus = mistral5696.catalogue_conversation(MODELES + [{'id': 'mistral-large-4-2610'}, {'id': '../x'}, 'x'])
        self.assertEqual([(r['id'], r['preuve']) for r in utiles],
                         [('mistral-large-4', 'catalogue_serveur'), ('mistral-large-4-2610', 'documentation'), ('mistral-small-latest', 'catalogue_serveur')])
        self.assertEqual([r['id'] for r in inconnus], ['modele-de-demain'])
        self.mistral.modeles = [{'id': 'modele-de-demain'}] + SANS_CONVERSATION
        with self.assertRaises(Stop) as ctx:
            mistral5696.enregistrer(self.desk, CLE_NOUVELLE, None, 'admin@example.test')
        self.assertEqual(str(ctx.exception), 'catalogue_modeles_vide')      # un catalogue sans modèle de conversation ne suffit pas
        self.assertIsNone(self.desk.settings('ai:provider:mistral'))


class Tester(Base):
    def test_A01_A02_tester_une_cle_candidate_sans_effet_local(self):
        avant = self.en_service()
        audit = self.desk.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        out = mistral5696.tester(self.desk, CLE_NOUVELLE)
        self.assertEqual(set(out) - {'ok', 'message', 'noreload'}, {'statut', 'le', 'modeles'})    # statut, date, nombre : rien d'autre
        self.assertEqual((out['statut'], out['modeles']), ('cle_reconnue', 2))
        self.assertTrue(out['noreload'])
        self.assertEqual(snapshot(self.desk), avant)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], audit + 1)
        self.assertNotIn(CLE_NOUVELLE, json.dumps(out) + self.desk.db.execute('SELECT data FROM audit ORDER BY id DESC').fetchone()[0])
        self.assertEqual([c[:2] for c in self.mistral.appels[-1:]], [('GET', '/models')])

    def test_A02_generation_seulement_sur_demande(self):
        self.en_service()
        mistral5696.tester(self.desk, CLE_NOUVELLE)
        self.assertFalse([c for c in self.mistral.appels if c[0] == 'POST'])
        vault = sorted(p.name for p in ai_gateway._vault_dir(self.desk).iterdir())
        out = mistral5696.tester(self.desk, CLE_NOUVELLE, generation=True)
        posts = [c for c in self.mistral.appels if c[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0][1], posts[0][2], posts[0][3]['max_tokens']), ('/chat/completions', CLE_NOUVELLE, 8))
        self.assertIn({'role': 'user', 'content': 'Réponds uniquement OK'}, posts[0][3]['messages'])     # texte fictif, aucun dossier
        self.assertEqual(posts[0][3]['model'], 'mistral-small-latest')          # le plus économe
        self.assertEqual(out['statut'], 'generation_reussie')
        self.assertEqual(sorted(p.name for p in ai_gateway._vault_dir(self.desk).iterdir()), vault)   # fichier temporaire effacé

    def test_A02_generation_refusee_en_local_uniquement(self):
        self.en_service(mode=False)
        politique5682.save(self.desk, {'mode': 'local'}, 'admin@example.test')
        with self.assertRaises(Stop):
            mistral5696.tester(self.desk, CLE_NOUVELLE, generation=True)
        self.assertFalse([c for c in self.mistral.appels if c[0] == 'POST'])      # rien ne contourne « local uniquement »
        self.assertEqual(mistral5696.tester(self.desk, CLE_NOUVELLE)['statut'], 'cle_reconnue')        # lecture du catalogue seule : permise

    def test_A02_generation_confirme_un_modele_a_capacite_inconnue(self):
        avant = self.en_service()
        out = mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test', modele='modele-de-demain', generation=True)
        self.assertEqual((out['modele'], out['statut']), ('modele-de-demain', 'generation_reussie'))
        caps = {r['slug']: r['capacites'] for r in catalogue5682.entries(self.desk.c['state_dir'], 'mistral')}
        self.assertEqual(caps['modele-de-demain']['conversation'], 'test_generation')
        self.assertEqual(caps['mistral-large-4']['conversation'], 'catalogue_serveur')
        self.assertNotIn('mistral-embed', caps)

    def test_A02_generation_refusee_pendant_la_pause(self):
        from agent.arret5643 import pause_all
        self.en_service()
        pause_all(self.desk, 'admin@example.test')
        with self.assertRaises(Stop) as ctx:
            mistral5696.tester(self.desk, CLE_NOUVELLE, generation=True)
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.assertFalse([c for c in self.mistral.appels if c[0] == 'POST'])

    def test_A01_reexamen_de_la_cle_enregistree(self):
        self.en_service()
        out = mistral5696.tester(self.desk, utiliser_existante=True)
        self.assertFalse(out['noreload'])
        st = mistral5696.etat(self.desk)
        self.assertEqual((st['etat'], st['libelle'], st['modeles']), ('cle_reconnue', 'Clé reconnue', 2))
        self.assertTrue(st['controle_le'])
        self.mistral.valides.discard(CLE_EN_SERVICE)              # clé révoquée chez Mistral
        with self.assertRaises(Stop):
            mistral5696.tester(self.desk, utiliser_existante=True)
        self.assertEqual(mistral5696.etat(self.desk)['libelle'], 'À reconnecter')


# ---------------------------------------------------------------------------------------------------- A03 : publication transactionnelle
class Rotation(Base):
    def test_A03_A08_rotation_conserve_modele_plafonds_droits_et_mode(self):
        avant = self.en_service()
        out = mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test')
        apres = snapshot(self.desk)
        self.assertEqual(apres['cle'], CLE_NOUVELLE)
        self.assertEqual(out['revision'], avant['reglage']['revision'] + 1)
        for key in ('model', 'monthly_budget_usd', 'per_request_budget_usd', 'enabled', 'external_data_allowed', 'url', 'zdr_required'):
            self.assertEqual(apres['reglage'][key], avant['reglage'][key], key)
        self.assertEqual(apres['politique'], avant['politique'])                  # mode, accord et routes inchangés
        self.assertEqual(apres['reglage']['secret_precedent'], avant['reglage']['secret_file'])
        self.assertTrue(Path(avant['reglage']['secret_file']).is_file())         # l'ancien fichier reste pour les appels en cours
        self.assertEqual(read_secret(ai_gateway.provider_registry(self.desk.c)['mistral']['secret_file']), CLE_NOUVELLE)
        fresh = {'state_dir': self.desk.c['state_dir']}
        ai_gateway.load_config_settings(fresh)                                   # un autre processus lit la nouvelle référence
        self.assertEqual(read_secret(fresh['ai_providers']['mistral']['secret_file']), CLE_NOUVELLE)
        self.assertEqual(mistral5696.etat(self.desk)['etat'], 'cle_reconnue')

    def test_A03_A08_deux_administrateurs_meme_revision_une_seule_publication(self):
        avant = self.en_service()
        rev = avant['reglage']['revision']
        mistral5696.enregistrer(self.desk, CLE_NOUVELLE, rev, 'admin@example.test')
        publie = snapshot(self.desk)
        self.mistral.valides.add('cle-essai-deuxieme-0004')
        with self.assertRaises(Stop) as ctx:
            mistral5696.enregistrer(self.desk, 'cle-essai-deuxieme-0004', rev, 'second@example.test')
        self.assertEqual(str(ctx.exception), 'reglage_modifie_entre_temps')
        self.assertEqual(snapshot(self.desk), publie)

    def test_A03_modele_en_service_disparu_exige_un_choix(self):
        avant = self.en_service()
        self.mistral.modeles = [m for m in MODELES if m['id'] != 'mistral-large-4']
        with self.assertRaises(Stop) as ctx:
            mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test')
        self.assertEqual(str(ctx.exception), 'modele_en_service_absent')
        self.assertEqual(snapshot(self.desk), avant)
        with self.assertRaises(Stop):
            mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test', modele='modele-invente')
        out = mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test', modele='mistral-small-latest')
        self.assertEqual(out['modele'], 'mistral-small-latest')

    def test_A03_premiere_cle_cree_une_connexion_inactive(self):
        routes = [r[0] for r in self.desk.db.execute("SELECT key FROM settings WHERE key LIKE 'ai:route:%'")]
        out = mistral5696.enregistrer(self.desk, CLE_NOUVELLE, 0, 'admin@example.test')
        reglage = self.desk.settings('ai:provider:mistral')
        self.assertEqual((reglage['enabled'], reglage['external_data_allowed'], out['actif']), (False, False, False))
        self.assertEqual(reglage['model'], 'mistral-small-latest')
        self.assertEqual(reglage['url'], API)
        self.assertIsNone(self.desk.settings(politique5682.KEY))
        self.assertEqual([r[0] for r in self.desk.db.execute("SELECT key FROM settings WHERE key LIKE 'ai:route:%'")], routes)
        self.assertEqual(politique5682.candidates(self.desk.c, {'mode': 'mistral', 'fournisseurs': []}), [])   # rien n'est envoyé sans accord

    def test_A03_cle_geree_par_le_systeme_jamais_remplacee(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        secret = root / 'mistral.key'
        secret.write_text(CLE_EN_SERVICE)
        secret.chmod(0o600)
        self.en_service(mode=False)
        value = {**self.desk.settings('ai:provider:mistral'), 'secret_file': str(secret)}
        self.desk.setting('ai:provider:mistral', value)
        self.desk.c['ai_providers']['mistral'] = value
        with self.assertRaises(Stop) as ctx:
            mistral5696.enregistrer(self.desk, CLE_NOUVELLE, None, 'admin@example.test')
        self.assertEqual(str(ctx.exception), 'cle_geree_par_le_systeme')
        self.assertEqual(secret.read_text(), CLE_EN_SERVICE)
        self.assertIn('administrateur du serveur', mistral5696.carte(self.desk))

    def test_A03_seuls_la_cle_en_service_et_la_precedente_restent_au_coffre(self):
        self.en_service()
        cles = ['cle-essai-rotation-%04d' % i for i in range(3)]
        self.mistral.valides.update(cles)
        for cle in cles:
            mistral5696.enregistrer(self.desk, cle, None, 'admin@example.test')
        vault = [p for p in ai_gateway._vault_dir(self.desk).iterdir() if p.name.endswith('.secret')]
        self.assertEqual(sorted(read_secret(p) for p in vault), sorted(cles[-2:]))

    def test_A03_formulaire_mixte_passe_par_le_meme_service(self):
        avant = self.en_service(mode=False)
        self.mistral.refus[CLE_REFUSEE] = REFUS['403']
        with self.assertRaises(Stop):
            modeles5681.apply_mixte(self.desk, CLE_REFUSEE, True)
        self.assertEqual(snapshot(self.desk), avant)
        out = modeles5681.apply_mixte(self.desk, CLE_NOUVELLE, True, revision=avant['reglage']['revision'])
        self.assertEqual(out['modele'], 'mistral-large-4')
        self.assertEqual(read_secret(self.desk.settings('ai:provider:mistral')['secret_file']), CLE_NOUVELLE)


# ---------------------------------------------------------------------------------------------------- A03 : limites et pannes
class Limites(Base):
    def cfg(self):
        return {'state_dir': self.desk.c['state_dir'], 'provider_id': 'mistral', 'provider_type': 'mistral'}

    def test_A03_A08_distinguer_debit_quota_cle_revoquee_et_panne(self):
        state = self.desk.c['state_dir']
        cases = ((http_error(401, 'POST', API), 'cle_a_verifier'), (http_error(403, 'POST', API), 'acces_refuse'),
                 (http_error(429, 'POST', API, b'{"type":"rate_limited"}'), 'limite_temporaire'),
                 (http_error(429, 'POST', API, b'{"error":{"type":"insufficient_quota","code":"quota_exceeded"}}'), 'quota_atteint'),
                 (http_error(402, 'POST', API), 'quota_atteint'), (http_error(503, 'POST', API), 'indisponible'),
                 (Stop('connexion_http_indisponible'), 'indisponible'), (Stop('json_ia_invalide'), None))
        for error, nature in cases:
            with self.subTest(nature=nature):
                mistral5696._effacer_incident(state, 'mistral')
                found = mistral5696.noter_erreur(self.cfg(), error)
                self.assertEqual(found and found['nature'], nature)
                if nature in mistral5696.BLOQUANTS:
                    self.assertTrue(found['bloquant'])
                    self.assertEqual(mistral5696.ETATS[nature], 'À reconnecter' if nature != 'quota_atteint' else 'Limite atteinte')

    def test_A03_retry_after_respecte_puis_delai_croissant_borne_puis_decision(self):
        self.en_service(mode=False)
        state = self.desk.c['state_dir']
        start = datetime.now(timezone.utc)
        found = mistral5696.noter_erreur(self.cfg(), http_error(429, 'POST', API, b'{}', '120'), start)
        self.assertEqual((datetime.fromisoformat(found['jusqu_a']) - start).total_seconds(), 120)
        self.assertEqual(found['retry_after'], 120)
        mistral5696._effacer_incident(state, 'mistral')
        spans = []
        for _ in range(4):
            found = mistral5696.noter_erreur(self.cfg(), http_error(429, 'POST', API), start)
            spans.append((datetime.fromisoformat(found['jusqu_a']) - start).total_seconds() / 60)
        self.assertEqual(spans, [2, 10, 30, 60])
        found = mistral5696.noter_erreur(self.cfg(), http_error(503, 'POST', API), start)
        self.assertEqual((found['nature'], found['bloquant'], found['code']), ('decision_requise', True, 'mistral_decision_requise'))
        self.assertEqual(mistral5696.etat(self.desk)['libelle'], 'Décision requise')
        again = mistral5696.noter_erreur(self.cfg(), http_error(429, 'POST', API), start)      # un incident passager n'efface pas la décision attendue
        self.assertEqual(again['nature'], 'decision_requise')

    def test_A03_A08_mode_exclusif_aucun_repli_et_aucun_appel_pendant_l_incident(self):
        self.en_service()
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'model': 'modele-local:12b'}
        calls = []

        def child(cfg, method, *args):
            calls.append(cfg['provider_id'])
            error = http_error(429, 'POST', API + '/chat/completions', b'{"type":"rate_limited"}', '90')
            mistral5696.noter_erreur(cfg, error)          # ce que fait ``Model._translate_error`` pour Mistral
            raise Stop('quota_fournisseur_depasse')
        from agent.model import Model, routed_config
        parent = Model(routed_config(self.desk.c, 'mail_triage'))
        with patch.object(parent, '_call_child', child), self.assertRaises(Stop) as ctx:
            parent.complete([{'role': 'user', 'content': 'texte fictif'}], 0, 100, None)
        self.assertEqual(str(ctx.exception), 'mistral_limite_temporaire')
        self.assertEqual(calls, ['mistral'])                                       # ni Ollama ni autre fournisseur
        with self.assertRaises(Stop) as ctx:
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})   # pas de reprise immédiate en boucle
        self.assertEqual(str(ctx.exception), 'mistral_limite_temporaire')
        self.assertEqual(sobriete5682.hold(self.desk.c), 'mistral_limite_temporaire')

    def test_A03_mode_automatique_connexion_en_incident_non_candidate(self):
        self.en_service(mode=False)
        politique5682.save(self.desk, {'mode': 'auto', 'consentement': True, 'fournisseurs': ['mistral']}, 'admin@example.test')
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['fournisseur'], 'mistral')
        mistral5696.noter_erreur(self.cfg(), http_error(503, 'POST', API))
        with self.assertRaises(Stop) as ctx:
            routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})
        self.assertEqual(str(ctx.exception), 'mistral_indisponible')
        mistral5696._effacer_incident(self.desk.c['state_dir'], 'mistral')
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_triage', 'triage', {'text': 'x'})['fournisseur'], 'mistral')

    def test_A03_A08_une_seule_ligne_dans_la_file(self):
        from agent.ia5672 import TRANSIENT
        self.assertTrue(mistral5696.HELD_CODES <= chatgpt5682.HELD_CODES and mistral5696.HELD_CODES <= TRANSIENT)   # relevés : la liste attend
        state = self.desk.c['state_dir']
        found = mistral5696.noter_erreur(self.cfg(), http_error(429, 'POST', API, b'{}', '600'))
        job = self.desk.enqueue('health', {'n': '1'})
        before = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        with patch('agent.desk.Desk.perform', side_effect=Stop('mistral_limite_temporaire')):
            self.desk.work_once()
        row = self.desk.db.execute('SELECT status,not_before,blocked_by,attempts FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual((row['status'], row['blocked_by'], row['attempts']), ('pending', 'mistral_limite_temporaire', 0))
        self.assertEqual(row['not_before'], found['jusqu_a'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)
        with patch('agent.desk.Desk.perform', side_effect=AssertionError('non échu : pas encore repris')):
            self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'pending')
        self.assertEqual(chatgpt5682.next_check(state, 'mistral_cle_a_verifier')[:13],
                         (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()[:13])

    def test_A03_cle_corrigee_libere_les_travaux_retenus(self):
        avant = self.en_service()
        mistral5696.noter_erreur(self.cfg(), http_error(401, 'POST', API))
        job = self.desk.enqueue('health', {'n': '1'})
        with patch('agent.desk.Desk.perform', side_effect=Stop('mistral_cle_a_verifier')):
            self.desk.work_once()
        self.assertGreater(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (job,)).fetchone()[0],
                           (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat())
        mistral5696.enregistrer(self.desk, CLE_NOUVELLE, avant['reglage']['revision'], 'admin@example.test')
        self.assertIsNone(mistral5696.incident(self.desk.c['state_dir'], 'mistral'))
        self.assertLessEqual(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (job,)).fetchone()[0], datetime.now(timezone.utc).isoformat())

    def test_A03_reponse_obtenue_leve_un_incident_temporaire(self):
        from agent.model import Model
        mistral5696.noter_erreur(self.cfg(), http_error(503, 'POST', API))

        class Http:
            headers = {}

            def json(self, method, path, body=None):
                return {'choices': [{'message': {'content': 'réponse fictive'}, 'finish_reason': 'stop'}], 'usage': {}}
        model = Model.__new__(Model)
        model.cfg, model.provider_type, model.provider_id = {**self.cfg(), 'model': 'mistral-small-latest'}, 'mistral', 'mistral'
        model.http, model.usage_cost_usd, model.usage_cost_known = Http(), 0.0, True
        with patch('agent.ai_gateway.estimate_and_check_budget', return_value=0.0), patch('agent.ai_gateway.record_usage', return_value=0.0), \
                patch('agent.budget_ledger5689.preparer_emission'):
            model._openai_compatible([{'role': 'user', 'content': 'x'}], 0, 50, None)
        self.assertIsNone(mistral5696.incident(self.desk.c['state_dir'], 'mistral'))

    def test_A03_model_note_le_retour_du_fournisseur(self):
        from agent.model import Model

        class Http:
            headers = {}

            def json(self, method, path, body=None):
                raise http_error(429, 'POST', API + path, b'{"type":"rate_limited"}', '45')
        model = Model.__new__(Model)
        model.cfg, model.provider_type, model.provider_id = {**self.cfg(), 'model': 'mistral-small-latest'}, 'mistral', 'mistral'
        model.http, model.usage_cost_usd, model.usage_cost_known = Http(), 0.0, True
        with patch('agent.ai_gateway.estimate_and_check_budget', return_value=0.0), patch('agent.ai_gateway.record_usage', return_value=0.0), \
                patch('agent.budget_ledger5689.preparer_emission'), self.assertRaises(Stop) as ctx:
            model._openai_compatible([{'role': 'user', 'content': 'x'}], 0, 50, None)
        self.assertEqual(str(ctx.exception), 'quota_fournisseur_depasse')       # code public historique inchangé hors du mode exclusif
        found = mistral5696.incident(self.desk.c['state_dir'], 'mistral')
        self.assertEqual((found['nature'], found['retry_after']), ('limite_temporaire', 45))


# ---------------------------------------------------------------------------------------------------- A01 : affichage
class Affichage(Base):
    def test_A01_libelle_du_quota_mistral(self):
        self.assertEqual(ia5682.HINTS['mistral'].count('Facturé à l’usage'), 0)
        self.assertIn(mistral5696.QUOTA_LIBELLE, ia5682.HINTS['mistral'])
        self.assertEqual(routage5682.summary({'unite': 'usd', 'type_fournisseur': 'mistral'})['canal'], mistral5696.QUOTA_LIBELLE)
        self.assertEqual(routage5682.summary({'unite': 'usd', 'type_fournisseur': 'anthropic'})['canal'], 'API facturée à l’usage')

    def test_A01_etats_et_carte(self):
        st = mistral5696.etat(self.desk)
        self.assertEqual(st['libelle'], 'À connecter')
        html = mistral5696.carte(self.desk)
        self.assertIn('data-api="m5682/mistral/tester"', html)
        self.assertIn('data-api="m5682/mistral/enregistrer"', html)
        self.assertIn('name="revision" value="0"', html)
        self.assertIn('Quota restant : Non fourni', html)
        self.assertIn(mistral5696.USAGE_URL, html)
        self.en_service()
        mistral5696.noter_erreur({'state_dir': self.desk.c['state_dir'], 'provider_id': 'mistral'}, http_error(429, 'POST', API, b'{}', '300'))
        html = mistral5696.carte(self.desk)
        self.assertIn('État : Limite atteinte', html)
        self.assertIn('prochain essai', html)
        self.assertIn('Fournisseur actif : <strong>API Mistral uniquement</strong>', html)
        self.assertIn('Envoi de données à Mistral : autorisé', html)
        self.assertNotIn(CLE_EN_SERVICE, html)
        self.assertIn('id="k96-mistral"', ia5682.card(self.desk))


# ---------------------------------------------------------------------------------------------------- même défaut et même méthode pour Claude
def snapshot_claude(desk):
    return snapshot(desk, 'anthropic')


class Claude(Base):
    def en_service_claude(self, mode=True):
        ai_gateway.save_provider(self.desk, 'anthropic', 'anthropic', 'https://api.anthropic.com/v1', 'claude-sonnet-5-5', CLE_EN_SERVICE, True, True,
                                 False, 30, 0.3, 2.0, 10.0)
        if mode:
            politique5682.save(self.desk, {'mode': 'anthropic', 'consentement': True}, 'admin@example.test')
        else:
            catalogue5682.refresh(self.desk, 'anthropic')
        return snapshot_claude(self.desk)

    def test_A02_cle_claude_refusee_par_le_formulaire_du_mode_laisse_la_connexion_intacte(self):
        """Même défaut que Mistral dans la 5.6.95 : 401, 403, 429, 5xx, réseau, catalogue vide."""
        avant = self.en_service_claude()
        for cas in ('401', '403', '429', '500', 'reseau', 'vide'):
            with self.subTest(cas=cas):
                self.claude.refus[CLE_REFUSEE] = REFUS.get(cas, cas)
                with self.assertRaises(Stop):
                    politique5682.save(self.desk, {'mode': 'anthropic', 'consentement': True, 'api_key': CLE_REFUSEE}, 'admin@example.test')
                self.assertEqual(snapshot_claude(self.desk), avant)
                self.assertEqual(read_secret(ai_gateway.provider_registry(self.desk.c)['anthropic']['secret_file']), CLE_EN_SERVICE)
        self.assertNotIn(CLE_REFUSEE, json.dumps([r[0] for r in self.desk.db.execute('SELECT data FROM audit')]))
        self.assertFalse([c for c in self.claude.appels if c[0] != 'GET'])

    def test_A02_mutation_claude_ancien_ordre_detecte(self):
        """Mutation en mémoire : l'ordre de la 5.6.95 pour Claude (clé enregistrée, puis catalogue) ; la même vérification échoue."""
        avant = self.en_service_claude()
        self.claude.refus[CLE_REFUSEE] = REFUS['401']

        def ancien(desk, mode, data, owner):
            pid, current = politique5682.provider_of(desk.c, mode)
            ai_gateway.save_provider(desk, pid, mode, current.get('url') or '', current.get('model'), str(data.get('api_key') or '').strip(), True, True,
                                     False, 30, 0.3, 0, 0)
            if not [r for r in catalogue5682.refresh(desk, pid) if r['statut'] == 'actif']:
                raise Stop('catalogue_modeles_vide')
        with patch.object(politique5682, '_prepare_exclusive', ancien), self.assertRaises(Stop):
            politique5682.save(self.desk, {'mode': 'anthropic', 'consentement': True, 'api_key': CLE_REFUSEE}, 'admin@example.test')
        apres = snapshot_claude(self.desk)
        self.assertNotEqual(apres, avant)
        self.assertEqual(apres['cle'], CLE_REFUSEE)

    def test_A03_cle_claude_valide_publiee_en_une_transaction(self):
        avant = self.en_service_claude()
        politique5682.save(self.desk, {'mode': 'anthropic', 'consentement': True, 'api_key': CLE_NOUVELLE, 'revision_cle': avant['reglage']['revision']},
                           'admin@example.test')
        apres = snapshot_claude(self.desk)
        self.assertEqual(apres['cle'], CLE_NOUVELLE)
        self.assertEqual(apres['reglage']['secret_precedent'], avant['reglage']['secret_file'])
        for key in ('model', 'monthly_budget_usd', 'per_request_budget_usd', 'enabled', 'external_data_allowed', 'url'):
            self.assertEqual(apres['reglage'][key], avant['reglage'][key], key)
        self.assertEqual(sorted(slug for slug, st in apres['catalogue'] if st == 'actif'), ['claude-haiku-5-5', 'claude-sonnet-5-5'])   # « retired » écarté
        with self.assertRaises(Stop) as ctx:      # même révision une seconde fois : une seule publication
            politique5682.save(self.desk, {'mode': 'anthropic', 'consentement': True, 'api_key': CLE_EN_SERVICE, 'revision_cle': avant['reglage']['revision']},
                               'admin@example.test')
        self.assertEqual(str(ctx.exception), 'reglage_modifie_entre_temps')
        self.assertEqual(snapshot_claude(self.desk)['cle'], CLE_NOUVELLE)

    def test_A02_entetes_officiels_de_l_api_claude(self):
        """Transport réel, réseau remplacé : adresse, en-têtes ``x-api-key`` et ``anthropic-version``, aucun jeton « Bearer »."""
        from agent import cles5696
        from agent.model import ANTHROPIC_VERSION
        seen = {}

        def request(http, method, url, data=None, headers=None, limit=0):
            seen.update(url=url, method=method, headers=dict(http.headers))
            return json.dumps({'data': MODELES_CLAUDE, 'has_more': False}).encode()
        with patch('agent.common.HTTP.request', request):
            url = cles5696.PROFILS['anthropic']['url']
            rows = cles5696.lire_catalogue('anthropic', CLE_NOUVELLE, url, HTTP_REEL(url, 'anthropic'))
        self.assertEqual(len(rows), 3)
        self.assertEqual((seen['method'], seen['url']), ('GET', 'https://api.anthropic.com/v1/models?limit=1000'))
        self.assertEqual((seen['headers']['x-api-key'], seen['headers']['anthropic-version']), (CLE_NOUVELLE, ANTHROPIC_VERSION))
        self.assertNotIn('Authorization', seen['headers'])
        with patch('agent.common.HTTP.request', request):
            url = cles5696.PROFILS['mistral']['url']
            cles5696.lire_catalogue('mistral', CLE_NOUVELLE, url, HTTP_REEL(url, 'mistral'))
        self.assertEqual((seen['url'], seen['headers']['Authorization']), ('https://api.mistral.ai/v1/models', 'Bearer ' + CLE_NOUVELLE))

    def test_A02_verification_commune_des_formulaires_historiques(self):
        """Fournisseurs (``save_ai_provider``) et installation (``setup560``) : la clé est testée avant d'être écrite."""
        from agent import cles5696
        avant = self.en_service_claude(mode=False)
        self.claude.refus[CLE_REFUSEE] = REFUS['401']
        with self.assertRaises(Stop) as ctx:
            cles5696.verifier_cle('anthropic', CLE_REFUSEE, 'https://api.anthropic.com/v1')
        self.assertEqual(str(ctx.exception), 'authentification_fournisseur_refusee')
        self.assertEqual(snapshot_claude(self.desk), avant)
        self.assertEqual(cles5696.verifier_cle('anthropic', CLE_NOUVELLE), 2)
        self.claude.modeles = [{'type': 'model', 'id': 'claude-ancien-1', 'lifecycle': 'retired'}]
        with self.assertRaises(Stop) as ctx:
            cles5696.verifier_cle('anthropic', CLE_NOUVELLE)
        self.assertEqual(str(ctx.exception), 'catalogue_modeles_vide')

    def test_A02_installation_cle_refusee_ne_remplace_rien(self):
        from agent import setup560
        avant = self.en_service_claude(mode=False)
        self.claude.refus[CLE_REFUSEE] = REFUS['403']
        notes = []
        form = {'ai': 'externe', 'provider': 'anthropic', 'provider_key': CLE_REFUSEE, 'consent': 'yes', 'provider_model': 'claude-sonnet-5-5'}
        with patch('agent.pieces510.save_profile'):
            setup560._save_app(self.desk, self.desk.c, form, 'externe', notes)
        self.assertTrue(any('acces_fournisseur_refuse' in n for n in notes), notes)
        self.assertEqual(snapshot_claude(self.desk)['cle'], CLE_EN_SERVICE)
        self.assertEqual(snapshot_claude(self.desk)['reglage'], avant['reglage'])


class Routes(login.Base):
    """Routes réelles (WSGI) : administrateur, CSRF, 409 sur révision périmée, aucune clé dans les réponses."""

    def setUp(self):
        super().setUp()
        self.cookie = self.admin_cookie()
        self.mistral = FauxMistral()
        p = patch('agent.cles5696._http', lambda base, kind='mistral': self.mistral)
        p.start()
        self.addCleanup(p.stop)

    def post(self, route, body, csrf=True):
        out = self.call('/api440/' + route, 'POST', body, auth=False, csrf=csrf, extra={'HTTP_COOKIE': '%s=%s' % (login.cx.COOKIE, self.cookie)})
        return out['status'][:3], json.loads(out['body'] or b'{}'), out['body'].decode('utf-8')

    def test_A02_A03_tester_puis_enregistrer_par_les_routes(self):
        from agent.standalone_auth import ADMIN_API
        self.assertIn('m5682/', ADMIN_API)
        status, out, raw = self.post('m5682/mistral/tester', {'cle': CLE_NOUVELLE})
        self.assertEqual(status, '200', raw)
        self.assertEqual((out['statut'], out['modeles']), ('cle_reconnue', 2))
        self.assertNotIn(CLE_NOUVELLE, raw)
        status, out, raw = self.post('m5682/mistral/enregistrer', {'cle': CLE_NOUVELLE, 'revision': '0'}, csrf=False)
        self.assertEqual((status, out.get('error')), ('400', 'origine_ou_csrf_refuse'))
        status, out, raw = self.post('m5682/mistral/enregistrer', {'cle': CLE_NOUVELLE, 'revision': '0'})
        self.assertEqual(status, '200', raw)
        self.assertNotIn(CLE_NOUVELLE, raw)
        self.mistral.valides.add('cle-essai-deuxieme-0004')
        status, out, raw = self.post('m5682/mistral/enregistrer', {'cle': 'cle-essai-deuxieme-0004', 'revision': '0'})
        self.assertEqual((status, out.get('error')), ('409', 'reglage_modifie_entre_temps'))
        self.assertNotIn('cle-essai-deuxieme-0004', raw)
        status, out, raw = self.post('m5682/mistral/tester', {'cle': CLE_REFUSEE})
        self.assertEqual((status, out.get('error')), ('400', 'authentification_fournisseur_refusee'))

    def test_A01_carte_sur_la_page_des_modeles(self):
        out = self.request('/ia-externe', cookie=self.cookie)
        self.assertEqual(out['status'][:3], '200')
        self.assertIn('Mistral · clé Studio', out['body'])
        self.assertIn('data-api="m5682/mistral/tester"', out['body'])
        self.assertIn('name="revision_mistral"', out['body'])


if __name__ == '__main__':
    unittest.main()
