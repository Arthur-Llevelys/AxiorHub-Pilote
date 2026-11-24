"""5.6.109 (lot E, correctif de production) : modèle externe choisi DANS le catalogue de la clé, « Modèle à choisir », refus du fournisseur
lisible et journalisé, essai d'une requête réelle.

Faux transport Mistral (``test_v5696_mistral.FauxMistral``) : aucun appel réseau, aucune clé réelle. Aucun identifiant de modèle n'est
présenté ici comme valide ou invalide chez le vrai fournisseur : les catalogues sont fictifs et seuls les circuits logiciels sont vérifiés.
Les garde-fous ont leur contre-exemple par mutation en mémoire."""
import io
import json
from pathlib import Path
from unittest.mock import patch

import test_v5696_mistral as M
from agent import ai_gateway, catalogue5682, ia540, mistral5696, modeles5681, modeles_catalogue56109 as K
from agent.common import Stop
from agent.erreurs5634 import explain, http_error
from agent.model import Model
from support567 import authorize_external

SANS_LARGE4 = [{'id': 'mistral-large-latest', 'capabilities': {'completion_chat': True}},
               {'id': 'mistral-large-2411', 'capabilities': {'completion_chat': True}},
               {'id': 'mistral-small-latest', 'capabilities': {'completion_chat': True}}]


def routes(desk):
    return {p: desk.settings('ai:route:' + p, {}) for p in ai_gateway.PURPOSES}


class Base(M.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['ollama'] = {**(self.desk.c.get('ollama') or {}), 'model': 'modele-local:12b'}

    def catalogue(self, modeles):
        self.mistral.modeles = list(modeles)
        catalogue5682.refresh(self.desk, 'mistral')

    def cfg(self, **extra):
        authorize_external(self.desk.c)
        reglage = dict(self.desk.settings('ai:provider:mistral'))
        return {**reglage, 'id': 'mistral', 'provider_id': 'mistral', 'provider_type': 'mistral', 'purpose': 'mail_drafting',
                'external_policy_config': self.desk.c, 'state_dir': self.desk.c['state_dir'], 'sans_liste5682': True, **extra}

    def posts(self):
        return [c for c in self.mistral.appels if c[0] == 'POST']


# ================================================================================================ (1) préréglage choisi dans le catalogue
class Prereglage(Base):
    def test_mixte_alias_latest_s_il_est_liste(self):
        self.en_service(mode=False)
        self.mistral.modeles = SANS_LARGE4
        out = modeles5681.apply_mixte(self.desk, '', True)
        self.assertEqual(out['modele'], 'mistral-large-latest')
        self.assertEqual(self.desk.settings('ai:provider:mistral')['model'], 'mistral-large-latest')
        self.assertEqual(routes(self.desk)['mail_drafting'], {'provider': 'mistral', 'model': 'mistral-large-latest'})

    def test_mixte_version_la_plus_recente_sans_alias(self):
        self.en_service(mode=False)
        self.mistral.modeles = [m for m in SANS_LARGE4 if m['id'] != 'mistral-large-latest'] + [{'id': 'mistral-large-2407'}]
        self.assertEqual(modeles5681.apply_mixte(self.desk, '', True)['modele'], 'mistral-large-2411')

    def test_mixte_refuse_sans_rien_enregistrer_si_aucun_modele_ne_correspond(self):
        """Témoin du défaut : le préréglage écrivait « MIXTE_MODEL » sur la connexion AVANT de lire le catalogue."""
        self.en_service(mode=False)
        ai_gateway.save_provider(self.desk, 'mistral', 'mistral', M.API, 'mistral-small-latest', '', True, True, False, 20, 0.2, 0.15, 0.6)
        avant, routes_avant = self.desk.settings('ai:provider:mistral'), routes(self.desk)
        self.mistral.modeles = [{'id': 'mistral-small-latest', 'capabilities': {'completion_chat': True}}]
        with self.assertRaises(Stop) as ctx:
            modeles5681.apply_mixte(self.desk, '', True)
        self.assertEqual(str(ctx.exception), 'modele_ou_route_fournisseur_absent')
        self.assertEqual(self.desk.settings('ai:provider:mistral'), avant)
        self.assertEqual(routes(self.desk), routes_avant)
        message = explain(str(ctx.exception), ctx.exception.detail)
        self.assertIn('mistral-small-latest', message)
        self.assertIn('rien n’a été enregistré', message)

    def test_modele_saisi_absent_du_catalogue_refuse(self):
        self.en_service(mode=False)
        self.catalogue(SANS_LARGE4)
        avant = routes(self.desk)
        for appel in (lambda: ia540.apply_mixed(self.desk, 'mistral', 'mistral-large-4', 'modele-local:12b', test=False),
                      lambda: ia540.apply_external_all(self.desk, 'mistral', 'mistral-large-4')):
            with self.assertRaises(Stop) as ctx:
                appel()
            self.assertEqual(str(ctx.exception), 'modele_absent_du_catalogue')
            self.assertIn('mistral-large-latest', explain('modele_absent_du_catalogue', ctx.exception.detail))
        self.assertEqual(routes(self.desk), avant)

    def test_modele_absent_jamais_envoye(self):
        self.en_service(mode=False)
        self.catalogue(SANS_LARGE4)
        with self.assertRaisesRegex(Stop, 'modele_absent_du_catalogue'):
            Model(self.cfg(model='mistral-large-4')).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertFalse(self.posts())
        # contre-exemple : sans la garde, le modèle absent partirait chez le fournisseur
        with patch.object(K, 'garde_modele', lambda cfg: None):
            Model(self.cfg(model='mistral-large-4')).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertTrue(self.posts())


# ================================================================================================ (2) « Modèle à choisir »
class ModeleAChoisir(Base):
    def reglages(self):
        return sorted(tuple(r) for r in self.desk.db.execute('SELECT key, value FROM settings'))

    def test_signale_au_controle_de_la_cle_sans_modifier_la_connexion(self):
        self.en_service(mode=False)
        self.assertNotIn('Modèle à choisir', mistral5696.carte(self.desk))
        self.mistral.modeles = SANS_LARGE4
        mistral5696.tester(self.desk, utiliser_existante=True)                  # prochain contrôle de la clé : catalogue relu et conservé
        self.assertEqual(self.desk.settings('ai:provider:mistral')['model'], 'mistral-large-4')      # rien n'est changé en silence
        avant = self.reglages()
        carte = mistral5696.carte(self.desk)
        self.assertEqual(self.reglages(), avant)                                # affichage : aucune écriture
        self.assertIn('Modèle à choisir', carte)
        self.assertIn('data-api="m5682/mistral/modele"', carte)
        self.assertIn('<option value="mistral-large-latest">', carte)

    def test_choix_explicite_met_a_jour_connexion_et_fonctions(self):
        self.en_service(mode=False)
        ia540.apply_mixed(self.desk, 'mistral', 'mistral-large-4', 'modele-local:12b', test=False)
        self.catalogue(SANS_LARGE4)
        rev = ai_gateway.revision_of(self.desk.settings('ai:provider:mistral'))
        with self.assertRaisesRegex(Stop, 'modele_non_teste'):
            mistral5696.choisir_modele(self.desk, 'modele-invente', rev)
        with self.assertRaisesRegex(Stop, 'reglage_modifie_entre_temps'):
            mistral5696.choisir_modele(self.desk, 'mistral-large-latest', rev - 1)
        out = mistral5696.choisir_modele(self.desk, 'mistral-large-latest', rev)
        self.assertGreater(out['routes'], 0)
        self.assertEqual(self.desk.settings('ai:provider:mistral')['model'], 'mistral-large-latest')
        self.assertEqual(routes(self.desk)['mail_drafting']['model'], 'mistral-large-latest')
        self.assertEqual(routes(self.desk)['mail_triage']['provider'], 'ollama')
        self.assertNotIn('Modèle à choisir', mistral5696.carte(self.desk))

    def test_test_de_generation_porte_sur_le_modele_choisi(self):
        self.en_service()
        mistral5696.tester(self.desk, M.CLE_NOUVELLE, generation=True)
        self.assertEqual(self.posts()[-1][3]['model'], 'mistral-large-4')
        self.mistral.modeles = SANS_LARGE4
        n = len(self.posts())
        with self.assertRaisesRegex(Stop, 'modele_absent_du_catalogue'):
            mistral5696.tester(self.desk, M.CLE_NOUVELLE, generation=True)
        self.assertEqual(len(self.posts()), n)


# ================================================================================================ (3) et (4) refus lisible et journalisé
class Refus(Base):
    def refuser(self, statut, corps):
        self.mistral.refus[M.CLE_EN_SERVICE] = (statut, corps, None)
        with self.assertRaises(Stop) as ctx:
            Model(self.cfg(model='mistral-large-4')).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        return ctx.exception

    def test_motif_du_fournisseur_au_lieu_de_http_400(self):
        self.en_service(mode=False)
        e1 = self.refuser(400, b'{"object":"error","message":"Invalid model: DURAND","type":"invalid_model","param":null,"code":"1500"}')
        self.assertEqual(str(e1), 'modele_inconnu_du_fournisseur')
        m1 = explain(str(e1), e1.detail)
        self.assertIn('Modèle inconnu du fournisseur (400)', m1)
        self.assertIn('invalid_model', m1)
        self.assertNotIn('DURAND', m1)
        e2 = self.refuser(422, b'{"error":{"type":"invalid_request_error","param":"seed","code":"unsupported_value","message":"texte KERVILAC"}}')
        self.assertEqual(str(e2), 'parametre_refuse_par_fournisseur')
        m2 = explain(str(e2), e2.detail)
        self.assertIn('« seed »', m2)                                       # (5) un paramètre refusé est nommé
        self.assertNotIn('KERVILAC', m2)
        e3 = self.refuser(400, b'[{"error":{"code":400,"message":"texte ZORVAL","status":"INVALID_ARGUMENT"}}]')
        self.assertEqual(str(e3), 'requete_refusee_par_fournisseur')
        self.assertIn('INVALID_ARGUMENT', explain(str(e3), e3.detail))
        self.assertNotIn('http 400', m1 + m2)
        from agent.travaux5689 import definitif
        self.assertTrue(definitif(str(e1), e1.detail))                    # un nouvel essai identique échouerait : aucune relance

    def test_refus_journalise_sans_contenu(self):
        self.en_service(mode=False)
        sortie = io.StringIO()
        with patch('sys.stderr', sortie):
            self.refuser(400, b'{"object":"error","message":"Invalid model: DURAND","type":"invalid_model","code":"1500"}')
        journal = (Path(self.desk.c['state_dir']) / 'erreurs-interface.log').read_text(encoding='utf-8')
        for texte in (journal, sortie.getvalue()):
            self.assertIn('refus_fournisseur_ia', texte)
            self.assertIn('connecteur=mistral', texte)
            self.assertIn('modele=mistral-large-4', texte)
            self.assertIn('fonction=mail_drafting', texte)
            self.assertIn('statut=400', texte)
            self.assertIn('refus_type=invalid_model', texte)
            self.assertNotIn('DURAND', texte)
            self.assertNotIn(M.CLE_EN_SERVICE, texte)


# ================================================================================================ (A) échantillonnage glouton (cause établie)
GLOUTON = (b'{"object":"error","message":"top_p must be 1 when using greedy sampling.","type":"invalid_request_greedy_sampling",'
           b'"param":null,"code":"3054","raw_status_code":400}')


class Glouton(Base):
    """Faux transport qui reproduit le refus constaté par l'administrateur : 400 ``invalid_request_greedy_sampling`` dès que la température
    vaut 0 et que top_p est absent ou différent de 1."""

    def setUp(self):
        super().setUp()
        appel = self.mistral.__call__
        faux = self.mistral

        def strict(methode, chemin, cle, corps=None):
            if methode == 'POST' and isinstance(corps, dict) and corps.get('temperature') == 0 and corps.get('top_p') != 1:
                faux.appels.append((methode, chemin, cle, corps))
                raise http_error(400, methode, M.API + chemin, GLOUTON)
            return appel(methode, chemin, cle, corps)
        p = patch.object(type(self.mistral), '__call__', lambda s, *a, **k: strict(*a, **k))
        p.start()
        self.addCleanup(p.stop)

    def test_A_temperature_zero_envoie_top_p_1_quel_que_soit_le_profil(self):
        self.en_service(mode=False)
        for sampling in ({}, {'top_p': 0.9}):
            out = Model(self.cfg(model='mistral-large-4', sampling=sampling)).complete([{'role': 'user', 'content': 'Texte fictif.'}], 0)
            self.assertEqual(out, 'OK')
            self.assertEqual((self.posts()[-1][3]['temperature'], self.posts()[-1][3]['top_p']), (0, 1))
        from agent.profil5634 import provider_options
        sent, ignored = provider_options('mistral', 0, {'top_p': 0.9})
        self.assertEqual(sent, {'temperature': 0, 'top_p': 1})
        self.assertIn('top_p (échantillonnage glouton)', ignored)
        self.assertEqual(provider_options('mistral', 0.3, {'top_p': 0.9})[0], {'temperature': 0.3, 'top_p': 0.9})   # hors glouton : profil inchangé
        self.assertEqual(provider_options('openai', 0, {'top_p': 0.9})[0], {'temperature': 0, 'top_p': 0.9})         # autres fournisseurs : sans preuve, rien ne change
        # contre-exemple : sans la règle déclarée, le refus constaté revient
        with patch('agent.profil5634.TOP_P_GLOUTON', {}):
            with self.assertRaises(Stop) as ctx:
                Model(self.cfg(model='mistral-large-4', sampling={'top_p': 0.9})).complete([{'role': 'user', 'content': 'Texte fictif.'}], 0)
        self.assertEqual(str(ctx.exception), 'echantillonnage_refuse_par_fournisseur')
        message = explain(str(ctx.exception), ctx.exception.detail)
        self.assertIn('Paramètres d’échantillonnage refusés par le fournisseur (température 0 : top_p doit valoir 1)', message)
        self.assertIn('invalid_request_greedy_sampling', message)
        self.assertIn('3054', message)
        self.assertNotIn('greedy sampling.', message)                    # jamais le message libre du fournisseur

    def test_A_test_de_la_carte_et_essai_passent(self):
        self.en_service(mode=False)
        authorize_external(self.desk.c)
        self.assertEqual(mistral5696.tester(self.desk, M.CLE_NOUVELLE, generation=True)['statut'], 'generation_reussie')
        self.assertEqual(self.posts()[-1][3]['top_p'], 1)
        res = K.essai_requete(self.desk, 'mistral')
        self.assertTrue(res['ok'], res)


# ================================================================================================ raisonnement : marge de sortie et consommation
REPONSE_REELLE_USAGE = {'prompt_tokens': 20, 'completion_tokens': 32, 'total_tokens': 52, 'prompt_tokens_details': {'cached_tokens': 0},
                        'completion_tokens_details': {'reasoning_tokens': 31}, 'service_tier': 'standard'}


class Raisonnement(Base):
    def test_usage_reel_normalise_sans_double_compte_et_cache_zero_vrai(self):
        from agent import consommation56109 as C
        obs = C.observer({'provider_type': 'mistral', 'input_usd_per_million': 1, 'output_usd_per_million': 2}, REPONSE_REELLE_USAGE)
        u = obs['usage']
        self.assertEqual((u['entree'], u['cache_lu'], u['entree_hors_cache'], u['sortie_facturable'], u['raisonnement']), (20, 0, 20, 32, 31))
        self.assertEqual(obs['mesure'], 'complete')
        self.assertEqual(obs['montant_micro'], 20 * 1 + 32 * 2)          # raisonnement compris dans la sortie : jamais ajouté une seconde fois

    def test_modele_qui_raisonne_note_puis_marge_de_sortie(self):
        self.en_service(mode=False)
        cfg = self.cfg(model='mistral-large-4')
        self.assertEqual(K.plancher_sortie(cfg, 8), 8)
        K.noter_raisonnement(cfg, {'usage': REPONSE_REELLE_USAGE})
        caps = {r['slug']: r['capacites'] for r in catalogue5682.entries(self.desk.c['state_dir'], 'mistral')}
        self.assertEqual(caps['mistral-large-4']['raisonnement'], 'reponse_fournisseur')
        self.assertNotIn('raisonnement', caps['mistral-small-latest'])
        self.assertEqual(K.plancher_sortie(cfg, 8), 8 + K.MARGE_RAISONNEMENT)          # borne demandée + marge (raisonnement compris)
        self.assertEqual(K.plancher_sortie(cfg, 6000), 6000 + K.MARGE_RAISONNEMENT)
        Model(cfg).complete([{'role': 'user', 'content': 'Texte fictif.'}], 0, 8)
        self.assertEqual(self.posts()[-1][3]['max_tokens'], 8 + K.MARGE_RAISONNEMENT)


class SortieConsommee(Base):
    """Faux transport : réponse coupée (« length ») dont la sortie est presque entièrement du raisonnement, tant que la borne est petite."""

    def setUp(self):
        super().setUp()
        appel = M.FauxMistral.__call__
        self.seuil = 10 ** 9

        def faux(s, methode, chemin, cle, corps=None):
            if methode == 'POST' and isinstance(corps, dict) and corps.get('max_tokens', 0) < self.seuil:
                s.appels.append((methode, chemin, cle, corps))
                n = corps['max_tokens']
                return {'choices': [{'message': {'role': 'assistant', 'content': ''}, 'finish_reason': 'length'}],
                        'usage': {'prompt_tokens': 20, 'completion_tokens': n, 'total_tokens': 20 + n, 'completion_tokens_details': {'reasoning_tokens': n - 1}}}
            return appel(s, methode, chemin, cle, corps)
        p = patch.object(M.FauxMistral, '__call__', faux)
        p.start()
        self.addCleanup(p.stop)

    def complete(self, max_tokens=3000):
        return Model(self.cfg(model='mistral-large-4')).complete([{'role': 'user', 'content': 'Texte fictif.'}], 0.3, max_tokens)

    def test_une_reprise_plus_large_puis_reponse(self):
        self.en_service(mode=False)
        self.seuil = 3001
        self.assertEqual(self.complete(), 'OK')
        bornes = [c[3]['max_tokens'] for c in self.posts()]
        self.assertEqual(bornes[0], 3000)
        self.assertGreater(bornes[1], 3000)
        self.assertEqual(len(bornes), 2)                                     # UNE reprise
        caps = {r['slug']: r['capacites'] for r in catalogue5682.entries(self.desk.c['state_dir'], 'mistral')}
        self.assertEqual(caps['mistral-large-4']['raisonnement_jetons'], 2999)   # marge apprise de la réponse réelle
        self.complete()
        self.assertEqual(self.posts()[-1][3]['max_tokens'], 3000 + 2999)      # appel suivant : marge déjà accordée, sans reprise

    def test_deux_coupures_motif_exact(self):
        self.en_service(mode=False)
        with self.assertRaises(Stop) as ctx:
            self.complete()
        self.assertEqual(str(ctx.exception), 'reponse_consommee_en_raisonnement')
        self.assertEqual(len(self.posts()), 2)
        self.assertIn('consommé sa réponse en raisonnement', explain(str(ctx.exception), ctx.exception.detail))
        from agent.web440 import human
        self.assertNotIn('plus capable', human('generation_ia_incomplete'))

    def test_coupure_sans_raisonnement_aucune_reprise(self):
        self.en_service(mode=False)
        self.mistral.refus.clear()
        with patch.object(K, '_usage_sortie', lambda result: (0, 3000)):
            with self.assertRaisesRegex(Stop, 'generation_ia_incomplete'):
                self.complete()
        self.assertEqual(len(self.posts()), 1)


# ================================================================================================ (6) essai d'une requête réelle
class Essai(Base):
    def test_essai_meme_forme_que_la_redaction(self):
        self.en_service(mode=False)
        authorize_external(self.desk.c)
        out = mistral5696.carte(self.desk)
        self.assertIn('data-api="m5682/essai-requete"', out)
        res = K.essai_requete(self.desk, 'mistral')
        self.assertTrue(res['ok'], res)
        corps = self.posts()[-1][3]
        self.assertEqual(corps['response_format'], {'type': 'json_object'})
        self.assertEqual(corps['max_tokens'], 3500)
        self.assertEqual(corps['messages'][0]['role'], 'system')
        self.assertIn('response_format=json_object', res['message'])
        self.mistral.refus[M.CLE_EN_SERVICE] = (422, b'{"error":{"type":"invalid_request_error","param":"response_format","message":"x"}}', None)
        res = K.essai_requete(self.desk, 'mistral')
        self.assertFalse(res['ok'])
        self.assertIn('« response_format »', res['message'])
