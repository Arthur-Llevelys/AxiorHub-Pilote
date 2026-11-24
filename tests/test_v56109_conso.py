"""5.6.109, lot M (C12 et F18) : mesures manquantes, cache par fournisseur, section unique « Consommation et coûts ».

C12 : une mesure absente reste inconnue (jamais zéro), un vrai zéro compte pour zéro, le cache est compris dans l'entrée ou ajouté à l'entrée
selon le fournisseur, le raisonnement n'est jamais compté deux fois, un tarif manquant n'est pas un tarif nul, l'historique n'est ni
recalculé ni complété, le montant calculé reste distinct du relevé du fournisseur, un même événement reçu deux fois ne fait qu'une ligne.
F18 : API, abonnement, IA locale et services externes séparés par unité, sommes partielles dites, devises séparées, filtres, export CSV
filtré, droits (un collaborateur ne lit que ses demandes ; dossier réservé ou inconnu : même 404), aucune écriture ni consommation à la lecture.

Données fictives (DURAND, MARTIN, example.test), faux services : aucun appel réseau, aucun modèle réel, aucun tarif réel (prix d'essai 1,
0,1 et 2 par million de jetons). Les neuf premiers tests de ``Registre`` sont les contre-exemples écrits avant la correction : ils
échouaient tous sur la 5.6.108 (120 au lieu de 220 micro-dollars pour le cache ajouté, 1 400 au lieu de 590 pour le cache compris, etc.).
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_v5634 as t34
from agent import ai_gateway, arret5643, budget_ledger5689 as bl, conso5682, consommation56109 as conso, economie569, ecrans5632, export56108
from agent import gouvernance_web5689 as gw, ia5682, interface56100, membres5650, routage5682, shell501, standalone_auth, web440
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = [{'role': 'user', 'content': 'texte fictif ' * 16}]
PRIX = (1, 0.1, 2, 'usd')      # entrée, cache lu, sortie : prix d'essai par million de jetons
TARIFS = {'entree_hors_cache': '1', 'cache_lu': '0.1', 'cache_ecrit_5m': '1.25', 'cache_ecrit_1h': '2', 'sortie_facturable': '2'}
PARIS = ZoneInfo('Europe/Paris')
ADMIN, DURAND, MARTIN = 'bernard@example.test', 'durand@example.test', 'martin@example.test'
ALPHA, BETA = '20240101001', '2024020201'
MESSAGES_CHAT = {'prompt_tokens': 100, 'completion_tokens': 50}
MESSAGES_ADDITIF = {'input_tokens': 100, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 0, 'output_tokens': 10}
RESPONSES = {'input_tokens': 1000, 'output_tokens': 200, 'input_tokens_details': {'cached_tokens': 400}, 'output_tokens_details': {'reasoning_tokens': 50}}


def api(state, **options):
    return {'provider_type': 'openai', 'provider_id': 'ext', 'model': 'fictif', 'purpose': 'assistant', 'state_dir': state, 'input_usd_per_million': 1,
            'output_usd_per_million': 2, 'per_request_budget_usd': 1, 'monthly_budget_usd': 5, **options}


class Outils:
    """Gestes communs aux deux bancs (base nue, bureau complet) : ``self.state`` est le répertoire d'état."""

    def appel(self, usage, statut='done', auteur='', decision='', dossier='', erreur='', **options):
        """Réserve, marque l'émission, puis enregistre le retour du fournisseur ; rend la configuration de l'appel."""
        if decision:
            routage5682._connect(self.state).close()
            db = sqlite3.connect(Path(self.state) / 'desk.sqlite3', timeout=10)
            db.execute("INSERT OR IGNORE INTO decisions_5682(uid,at,mission,etape,proprietaire,dossier_h,fonction,type_tache,niveau,fournisseur,modele,effort,raisons,raison_courte,"
                       "mode,politique_version,unite,provisoire,etat) VALUES(?,?,?,'etape-fictive','',?,'assistant','analyse','standard','ext','fictif','','[]','','auto',1,'usd',0,'decidee')",
                       (decision, bl._iso(), str(options.get('mission5682') or ''), conso.empreinte_dossier(dossier)))
            db.commit()
            db.close()
            options['decision5682'] = decision
        cfg = api(self.state, **options)
        membres5650.set_author(auteur)
        try:
            estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
            bl.preparer_emission(cfg)
            cfg['_emettre5689']()                                                # la requête part réellement (marquage fait par ``interruptible``)
            ai_gateway.record_usage(cfg, {'usage': usage} if usage is not None else {}, estime, statut, MESSAGES, error=erreur)
        finally:
            membres5650.set_author('')
        return cfg

    def forfait(self, usage=None, **options):
        """Appel par abonnement (forfait) : aucun prix par appel, même si un tarif d'API traîne dans le réglage."""
        cfg = {'provider_type': 'chatgpt', 'provider_id': 'chatgpt', 'model': 'gpt-fictif', 'purpose': 'assistant', 'state_dir': self.state, 'canal': 'abonnement',
               'prix5682': (2.5, 0.25, 12.5, 'credits'), **options}
        bl.noter_forfait(cfg)
        bl.preparer_emission(cfg)
        ai_gateway.record_usage(cfg, {'usage': RESPONSES if usage is None else usage}, 0.0, 'done', MESSAGES)
        return cfg

    def sql(self, requete, *params):
        db = sqlite3.connect(Path(self.state) / 'desk.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in db.execute(requete, params)]
        finally:
            db.close()

    def ecrire(self, requete, *params):
        db = sqlite3.connect(Path(self.state) / 'desk.sqlite3', timeout=10)
        try:
            db.execute(requete, params)
            db.commit()
        finally:
            db.close()

    def ligne(self, cfg):
        return self.sql('SELECT * FROM budget_reservations5689 WHERE call_id=?', cfg.get('_budget_call5689') or cfg.get('decision5682'))[0]

    def observations(self):
        return self.sql('SELECT * FROM conso_5682 ORDER BY id')


class Banc(Outils, unittest.TestCase):
    """Base nue : un répertoire d'état et sa base, comme un poste qui vient de démarrer."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(membres5650.clear)
        self.state = self.temp.name
        sqlite3.connect(Path(self.state) / 'desk.sqlite3').close()
        self.desk = SimpleNamespace(c={'state_dir': self.state})


# ================================================================================ C12 : normaliseur (fonctions pures)
class Normalisation(unittest.TestCase):
    def test_C12_messages_cache_ajoute_a_l_entree(self):
        x = conso.normaliser('anthropic_messages', {'input_tokens': 100, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 100,
                                                    'cache_creation': {'ephemeral_5m_input_tokens': 60, 'ephemeral_1h_input_tokens': 40}, 'output_tokens': 10})
        self.assertEqual((x['entree'], x['entree_hors_cache'], x['cache_lu'], x['cache_ecrit']), (1200, 100, 1000, 100))
        self.assertEqual(conso.cout_tokens(x, TARIFS), 375)                  # 100 + 100 + 75 + 80 + 20
        temoin = conso.normaliser('anthropic_messages', MESSAGES_ADDITIF)
        self.assertEqual(conso.cout_tokens(temoin, TARIFS), 220)             # fixture du témoin : 0,00022 $, et non 0,00003 $

    def test_C12_messages_cache_ecrit_non_detaille_reste_inconnu(self):
        x = conso.normaliser('anthropic_messages', {'input_tokens': 100, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 100, 'output_tokens': 10})
        self.assertIsNone(conso.cout_tokens(x, TARIFS))                      # durée d'écriture inconnue : jamais gratuite par défaut
        sans = conso.normaliser('anthropic_messages', {'input_tokens': 100, 'output_tokens': 10})
        self.assertIsNone(sans['entree'])                                    # cache ni lu ni écrit déclaré : le total n'est pas inventé
        self.assertEqual(sans['entree_hors_cache'], 100)

    def test_C12_chat_et_mistral_cache_compris_dans_l_entree(self):
        usage = {'prompt_tokens': 1000, 'completion_tokens': 200, 'prompt_tokens_details': {'cached_tokens': 900}}
        for fmt in ('mistral', 'openai_chat'):
            x = conso.normaliser(fmt, usage)
            self.assertEqual((x['entree'], x['entree_hors_cache'], x['cache_lu']), (1000, 100, 900), fmt)
            self.assertEqual(conso.cout_tokens(x, TARIFS), 590, fmt)         # 100 + 90 + 400 : le cache n'est pas compté deux fois
        nu = {'prompt_tokens': 1000, 'completion_tokens': 200}
        self.assertEqual(conso.cout_tokens(conso.normaliser('mistral', nu), TARIFS), 1400)       # absence = 0 : documenté pour Mistral seulement
        self.assertIsNone(conso.cout_tokens(conso.normaliser('openai_chat', nu), TARIFS))        # ailleurs, l'absence reste une absence

    def test_C12_raisonnement_deja_dans_la_sortie_jamais_ajoute(self):
        x = conso.normaliser('openai_responses', {'input_tokens': 1000, 'input_tokens_details': {'cached_tokens': 400, 'cache_write_tokens': 0}, 'output_tokens': 200,
                                                  'output_tokens_details': {'reasoning_tokens': 50}})
        self.assertEqual((x['sortie_facturable'], x['raisonnement']), (200, 50))
        self.assertEqual(conso.cout_tokens(x, TARIFS), 1040)                 # 600 + 40 + 400 ; 1 140 si le raisonnement était recompté
        self.assertEqual(conso.cout_tokens({**x, 'raisonnement': 199}, TARIFS), 1040)
        with self.assertRaises(ValueError):
            conso.normaliser('openai_responses', {'input_tokens': 1, 'output_tokens': 5, 'output_tokens_details': {'reasoning_tokens': 6}})

    def test_C12_ecriture_de_cache_non_exposee_reste_inconnue(self):
        usage = {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens': 10}
        self.assertIsNone(conso.cout_tokens(conso.normaliser('openai_responses', usage), TARIFS))
        self.assertEqual(conso.cout_tokens(conso.normaliser('openai_responses', usage, cache_ecriture_non_facturee=True), TARIFS), 120)

    def test_C12_gemini_et_ollama(self):
        x = conso.normaliser('gemini', {'promptTokenCount': 100, 'cachedContentTokenCount': 0, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 30})
        self.assertEqual((x['sortie_facturable'], conso.cout_tokens(x, TARIFS)), (50, 200))
        self.assertIsNone(conso.normaliser('gemini', {'promptTokenCount': 100, 'candidatesTokenCount': 20})['sortie_facturable'])
        local = conso.normaliser('ollama', {'prompt_eval_count': 100, 'eval_count': 10})
        self.assertEqual((local['entree'], local['sortie_facturable']), (100, 10))
        self.assertEqual(conso.montant_affichable('local', local, TARIFS), {'montant_micro': None, 'etat': 'cout_poste_non_mesure'})
        self.assertEqual(conso.montant_affichable('abonnement', local, TARIFS), {'montant_micro': None, 'etat': 'forfait_separe'})

    def test_C12_absence_inconnue_vrai_zero_compte_quantites_invalides_refusees(self):
        self.assertIsNone(conso.normaliser('mistral', {})['entree'])
        zero = conso.normaliser('mistral', {'prompt_tokens': 0, 'completion_tokens': 0})
        self.assertEqual((zero['entree'], zero['sortie_facturable']), (0, 0))
        self.assertEqual(conso.cout_tokens(zero, {}), 0)                     # rien consommé : aucun tarif n'est nécessaire
        for valeur in (True, -1, 1.5, '12', None):
            self.assertIsNone(conso.normaliser('openai_chat', {'prompt_tokens': valeur, 'completion_tokens': 5})['entree'], valeur)

    def test_C12_tarif_manquant_jamais_nul(self):
        x = conso.normaliser('mistral', {'prompt_tokens': 10, 'completion_tokens': 1})
        self.assertIsNone(conso.cout_tokens(x, {'entree_hors_cache': '1'}))  # tarif de sortie absent : coût inconnu, pas 10
        self.assertIsNone(conso.cout_tokens(x, {'entree_hors_cache': '1', 'sortie_facturable': None}))
        for mauvais in ('NaN', '-1', True, 'abc'):
            with self.assertRaises(ValueError):
                conso.cout_tokens(x, {**TARIFS, 'entree_hors_cache': mauvais})
        self.assertIsNone(conso._tarif(0))                                   # les réglages enregistrent 0 pour un champ vide
        self.assertIsNone(conso._tarif(''))
        self.assertEqual(str(conso._tarif('0.15')), '0.15')

    def test_C12_releves_incoherents_et_format_non_qualifie(self):
        for fmt, usage in (('openai_chat', {'prompt_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 11}}),
                           ('anthropic_messages', {'input_tokens': 1, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 10,
                                                   'cache_creation': {'ephemeral_5m_input_tokens': 4, 'ephemeral_1h_input_tokens': 4}, 'output_tokens': 1}),
                           ('format_inconnu', MESSAGES_CHAT)):
            with self.assertRaises(ValueError, msg=fmt):
                conso.normaliser(fmt, usage)


# ================================================================================ C12 : observation d'un appel (format, grille, montant)
class Observation(unittest.TestCase):
    def test_C12_format_choisi_par_le_type_du_fournisseur_jamais_par_la_reponse(self):
        self.assertEqual(conso.FORMATS, {'anthropic': 'anthropic_messages', 'chatgpt': 'openai_responses', 'mistral': 'mistral', 'openai': 'openai_chat',
                                         'openrouter': 'openai_chat', 'ollama': 'ollama',
                                         'gemini': 'gemini_openai'})   # 5.6.109 (lot E, F15) : connexion Gemini qualifiée (compatibilité OpenAI de Google)
        # une réponse d'un fournisseur « Messages » qui se présenterait comme un schéma à cache compris n'est pas crue : ses champs « chat » sont ignorés
        trompeuse = {**MESSAGES_CHAT, 'prompt_tokens_details': {'cached_tokens': 0}, 'input_tokens': 10, 'output_tokens': 1}
        obs = conso.observer(api('', provider_type='anthropic', prix5682=PRIX), trompeuse)
        self.assertEqual((obs['format'], obs['mesure'], obs['montant_micro'], obs['entree_connue']), ('anthropic_messages', 'cache_non_declare', None, 10))
        inconnu = conso.observer(api('', provider_type='fournisseur_non_qualifie'), MESSAGES_CHAT)
        self.assertEqual((inconnu['mesure'], inconnu['montant_micro'], inconnu['etat_montant']), ('non_qualifiee', None, 'format_non_qualifie'))

    def test_C12_majorant_etiquete_jamais_une_sous_estimation(self):
        obs = conso.observer(api(''), MESSAGES_CHAT)                         # passerelle compatible : détail du cache non fourni
        self.assertEqual((obs['mesure'], obs['montant_micro'], obs['etat_montant']), ('sans_detail_cache', 200, 'majorant_cache_non_detaille'))
        cher = conso.observer(api('', prix5682=(1, 3, 2, 'usd')), MESSAGES_CHAT)             # cache plus cher que l'entrée : plus aucune borne
        self.assertIsNone(cher['montant_micro'])
        additif = conso.observer(api('', provider_type='anthropic'), {'input_tokens': 100, 'output_tokens': 10})
        self.assertEqual((additif['montant_micro'], additif['etat_montant']), (None, 'usage_partiel'))       # cache ajouté inconnu : 120 serait une sous-estimation
        lu = conso.observer(api('', provider_type='anthropic'), MESSAGES_ADDITIF)             # cache lu connu, tarif du cache absent : tout au tarif d'entrée
        self.assertEqual((lu['montant_micro'], lu['etat_montant']), (1120, 'majorant_tarif_cache_absent'))
        ecrit = conso.observer(api('', provider_type='anthropic', prix5682=PRIX), {**MESSAGES_ADDITIF, 'cache_creation_input_tokens': 50,
                                                                                  'cache_creation': {'ephemeral_5m_input_tokens': 50, 'ephemeral_1h_input_tokens': 0}})
        self.assertEqual((ecrit['montant_micro'], ecrit['etat_montant']), (None, 'tarif_manquant'))          # écriture de cache sans tarif : inconnu
        relais = conso.observer(api('', provider_type='openrouter', prix5682=PRIX), {'prompt_tokens': 1000, 'completion_tokens': 10,
                                                                                 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 500}})
        self.assertEqual((relais['montant_micro'], relais['etat_montant'], relais['usage']['cache_ecrit']), (None, 'tarif_manquant', 500))   # 1 020 serait une sous-estimation

    def test_C12_tarif_absent_ou_nul_etat_tarif_manquant(self):
        for options in ({'output_usd_per_million': 0}, {'output_usd_per_million': None}, {'output_usd_per_million': ''}):
            obs = conso.observer(api('', **options), MESSAGES_CHAT)
            self.assertEqual((obs['montant_micro'], obs['etat_montant']), (None, 'tarif_manquant'), options)
        self.assertEqual(conso.observer(api(''), {'prompt_tokens': 100})['etat_montant'], 'usage_partiel')
        self.assertEqual(conso.observer(api(''), {})['etat_montant'], 'usage_absent')
        self.assertEqual(conso.observer(api(''), {'prompt_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens': 1})['etat_montant'], 'usage_incoherent')
        self.assertEqual(bl.tarif_de({'input_usd_per_million': 1}), 'usd_par_million:1/non_renseigne')       # jamais « /0 »

    def test_C12_grille_figee_avec_origine_et_version(self):
        _g, routage = conso.tarifs_de(api('', prix5682=PRIX))
        _g, reglage = conso.tarifs_de(api(''))
        self.assertEqual((routage['origine'], routage['prix']['cache_lu'], reglage['origine'], reglage['prix']['cache_lu']), ('decision_de_routage', '0.1', 'reglage_du_fournisseur', None))
        self.assertNotEqual(routage['version'], reglage['version'])
        self.assertNotEqual(conso.tarifs_de(api('', output_usd_per_million=3))[1]['version'], reglage['version'])      # un tarif modifié est une autre grille
        from agent import catalogue5682
        ref = catalogue5682.reference('mistral', 'mistral-small-latest')
        _g, datee = conso.tarifs_de(api('', provider_type='mistral', model='mistral-small-latest', prix5682=(ref['entree'], ref['cache'], ref['sortie'], 'usd')))
        self.assertEqual((datee['origine'], datee['releve']), ('reference_datee', ref['releve']))
        self.assertIsNone(datee['prix']['cache_ecrit_5m'])                    # aucun tarif d'écriture de cache dans les réglages : inconnu

    def test_C12_forfait_et_local_sans_prix_api(self):
        forfait = conso.observer({'provider_type': 'chatgpt', 'input_usd_per_million': 1, 'output_usd_per_million': 2, 'prix5682': PRIX}, RESPONSES)
        self.assertEqual((forfait['canal'], forfait['montant_micro'], forfait['etat_montant'], forfait['tarif']), ('abonnement', None, 'forfait_separe', None))
        self.assertEqual((forfait['mesure'], forfait['usage']['entree_hors_cache'], forfait['usage']['cache_lu']), ('complete', 600, 400))
        local = conso.observer({'provider_type': 'ollama', 'input_usd_per_million': 1, 'output_usd_per_million': 2}, {'prompt_eval_count': 100, 'eval_count': 10})
        self.assertEqual((local['canal'], local['montant_micro'], local['etat_montant']), ('local', None, 'cout_poste_non_mesure'))

    def test_C12_periode_du_cabinet_bornes_en_utc(self):
        desk = SimpleNamespace(c={})
        hiver = conso.periode(desk, {}, datetime(2026, 11, 1, 0, 30, tzinfo=PARIS))            # 31 octobre, 23 h 30 en UTC
        self.assertEqual((hiver['debut'], hiver['fin'], hiver['debut_utc'], hiver['fin_utc']), ('2026-11-01', '2026-11-30', '2026-10-31T23:00:00+00:00', '2026-11-30T23:00:00+00:00'))
        ete = conso.periode(desk, {'periode': 'mois', 'mois': '2026-07'})
        self.assertEqual((ete['debut_utc'], ete['fin_utc']), ('2026-06-30T22:00:00+00:00', '2026-07-31T22:00:00+00:00'))
        jour = conso.periode(desk, {'periode': 'jour'}, datetime(2026, 11, 23, 9, 0, tzinfo=PARIS))
        self.assertEqual((jour['debut'], jour['fin'], jour['debut_utc']), ('2026-11-23', '2026-11-23', '2026-11-22T23:00:00+00:00'))
        semaine = conso.periode(desk, {'periode': '7j'}, datetime(2026, 11, 23, 9, 0, tzinfo=PARIS))
        self.assertEqual((semaine['debut'], semaine['fin']), ('2026-11-17', '2026-11-23'))
        perso = conso.periode(desk, {'periode': 'perso', 'debut': '2026-10-01', 'fin': '2026-10-15'})
        self.assertEqual((perso['debut_utc'], perso['fin_utc']), ('2026-09-30T22:00:00+00:00', '2026-10-15T22:00:00+00:00'))
        for mauvais in ({'periode': 'annee'}, {'periode': 'perso', 'debut': '2026-10-15', 'fin': '2026-10-01'}, {'periode': 'perso', 'debut': 'hier', 'fin': '2026-10-01'},
                        {'periode': 'perso', 'debut': '2024-01-01', 'fin': '2026-10-01'}, {'periode': 'mois', 'mois': 'novembre'}):
            with self.assertRaisesRegex(Stop, 'periode_invalide', msg=mauvais):
                conso.periode(desk, mauvais)


# ================================================================================ C12 : registre du budget, usage, observations
class Registre(Banc):
    # ---- contre-exemples écrits avant la correction
    def test_C12_registre_cache_additif_messages(self):
        cfg = self.appel(MESSAGES_ADDITIF, provider_type='anthropic', prix5682=PRIX)
        self.assertEqual(self.ligne(cfg)['retained_micro'], 220)            # 100 × 1 + 1 000 × 0,1 + 10 × 2 : 0,00022 $ du témoin

    def test_C12_registre_cache_inclus_chat(self):
        cfg = self.appel({'prompt_tokens': 1000, 'completion_tokens': 200, 'prompt_tokens_details': {'cached_tokens': 900}}, provider_type='mistral', prix5682=PRIX)
        self.assertEqual(self.ligne(cfg)['retained_micro'], 590)            # 100 × 1 + 900 × 0,1 + 200 × 2

    def test_C12_registre_tarif_manquant_reste_incertain(self):
        cfg = api(self.state)
        del cfg['output_usd_per_million']
        estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
        ai_gateway.record_usage(cfg, {'usage': MESSAGES_CHAT}, estime, 'done', MESSAGES)
        r = self.ligne(cfg)
        self.assertEqual((r['state'], r['actual_micro'], r['reason']), ('uncertain', None, 'tarif_non_renseigne'))
        self.assertEqual(bl.engage_fournisseur_usd(self.state, 'ext') * 1_000_000, r['ceiling_micro'])       # l'estimation reste comptée

    def test_C12_registre_usage_partiel_reste_incertain(self):
        r = self.ligne(self.appel({'prompt_tokens': 100}))
        self.assertEqual((r['state'], r['actual_micro'], r['reason']), ('uncertain', None, 'usage_partiel'))
        self.assertEqual(json.loads(r['usage_json'])['usage']['entree'], 100)                               # ce qui a été observé est gardé pour le rapprochement

    def test_C12_usage_absent_jamais_zero_dans_la_lecture(self):
        self.appel(None)
        vue = ai_gateway.usage_snapshot(self.desk)[0]
        self.assertIsNone(vue['input_tokens'])
        self.assertIsNone(vue['output_tokens'])
        self.assertEqual(vue['sans_mesure'], 1)
        self.assertEqual(self.sql('SELECT mesure,input_tokens FROM ai_usage_v391')[0], {'mesure': 'absente', 'input_tokens': 0})
        self.assertEqual(self.ligne(self.appel(None))['reason'], 'usage_non_fourni')

    def test_C12_meme_evenement_deux_fois_une_observation(self):
        cfg = self.appel(MESSAGES_CHAT)
        conso5682.note(cfg, MESSAGES_CHAT, 'done')                          # le même événement, reçu une seconde fois
        lignes = self.observations()
        self.assertEqual((len(lignes), lignes[0]['revision']), (1, 0))
        ai_gateway.record_usage(cfg, {'usage': MESSAGES_CHAT}, 0.5, 'done', MESSAGES)        # et le retour complet rejoué : une seule dépense
        self.assertEqual(bl.engage_fournisseur_usd(self.state, 'ext') * 1_000_000, 200)
        self.assertEqual(len(self.observations()), 1)

    def test_C12_montant_calcule_distinct_du_releve_fournisseur(self):
        cfg = self.appel(MESSAGES_CHAT)
        r = self.ligne(cfg)
        self.assertEqual((r['retained_micro'], r['computed_micro'], r['amount_source']), (200, 200, 'majorant_cache_non_detaille'))
        self.assertIsNone(r['provider_amount_micro'])                         # un calcul n'est pas un relevé du fournisseur
        self.assertIsNone(r['provider_currency'])
        suite = bl.rapprocher(self.state, cfg['_budget_call5689'], '0.0003', reference='FACTURE-FICTIVE-7')
        r = self.ligne(cfg)
        self.assertFalse(suite['compte'])                                    # jamais recompté
        self.assertEqual((r['retained_micro'], r['computed_micro'], r['provider_amount_micro'], r['provider_ref']), (200, 200, 300, 'FACTURE-FICTIVE-7'))
        ecart = self.sql("SELECT micro,etat,detail FROM budget_events5689 WHERE nature='ecart_reglement'")[0]
        self.assertEqual((ecart['micro'], ecart['etat']), (300, 'a_controler'))               # l'écart est expliqué, le tarif passé n'est pas modifié
        bl.rapprocher(self.state, cfg['_budget_call5689'], '0.0009')
        self.assertEqual(self.ligne(cfg)['provider_amount_micro'], 300)       # le premier relevé n'est pas écrasé

    def test_C12_periode_du_cabinet_pas_prefixe_utc(self):
        self.appel(MESSAGES_CHAT)
        debut = datetime.now(PARIS).replace(day=1, hour=0, minute=30, second=0, microsecond=0)     # 1er du mois, 0 h 30 à Paris : encore le mois d'avant en UTC
        self.ecrire('UPDATE conso_5682 SET at=?', debut.astimezone(timezone.utc).isoformat())
        self.assertEqual(sum(r['appels'] for r in conso5682.snapshot(self.state)), 1)
        veille = debut - timedelta(hours=1)                                   # 23 h 30 la veille : mois précédent du cabinet
        self.ecrire('UPDATE conso_5682 SET at=?', veille.astimezone(timezone.utc).isoformat())
        self.assertEqual(conso5682.snapshot(self.state), [])

    def test_C12_appel_route_sans_mesure_reste_incertain(self):
        reservation = conso5682.reserve(self.state, 'mission-1', 0.2, 'usd', 5, 'decision-1', 'ext', 'fictif', 'assistant')
        cfg = api(self.state, decision5682='decision-1', mission5682='mission-1')
        estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
        ai_gateway.record_usage(cfg, {}, estime, 'done', MESSAGES)
        conso5682.settle(self.state, reservation, 'consomme')                 # fin de l'étape routée : l'estimation n'est pas une mesure
        r = self.ligne(cfg)
        self.assertEqual((r['state'], r['actual_micro'], r['reason']), ('uncertain', None, 'usage_non_fourni'))
        self.assertEqual(conso5682.used(self.state, 'mission-1', 'usd'), 0.2)                  # et reste comptée dans le plafond de la mission

    # ---- suites
    def test_C12_vrai_zero_regle_a_zero_et_affiche(self):
        cfg = self.appel({'prompt_tokens': 0, 'completion_tokens': 0})
        r = self.ligne(cfg)
        self.assertEqual((r['state'], r['retained_micro']), ('settled', 0))
        self.assertEqual(self.observations()[0]['quantite'], 0.0)
        self.assertIn('0,00 $ estimés', ia5682._usage(self.desk))
        self.assertEqual(ai_gateway.usage_snapshot(self.desk)[0]['input_tokens'], 0)             # zéro mesuré : affiché zéro, pas « inconnu »

    def test_C12_cout_rendu_calcule_sinon_estimation(self):
        cfg = api(self.state)
        estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
        self.assertEqual(ai_gateway.record_usage(cfg, {'usage': MESSAGES_CHAT}, estime, 'done', MESSAGES), 0.0002)
        autre = api(self.state)
        estime = ai_gateway.estimate_and_check_budget(autre, MESSAGES, 900)
        self.assertEqual(ai_gateway.record_usage(autre, {}, estime, 'done', MESSAGES), estime)      # inconnu : l'estimation, jamais zéro

    def test_C12_entree_d_un_schema_additif_somme_des_trois_categories(self):
        self.appel(MESSAGES_ADDITIF, provider_type='anthropic', prix5682=PRIX)
        self.assertEqual(self.sql('SELECT input_tokens,output_tokens,mesure FROM ai_usage_v391')[0], {'input_tokens': 1100, 'output_tokens': 10, 'mesure': 'complete'})
        o = self.observations()[0]
        self.assertEqual((o['entree'], o['hors_cache'], o['entree_cache'], o['cache_ecrit'], o['sortie'], o['mesure'], o['format_usage']),
                         (1100, 100, 1000, 0, 10, 'complete', 'anthropic_messages'))
        self.assertEqual((o['montant_micro'], o['quantite'], o['etat_montant'], o['devise']), (220, 0.00022, 'calcule_depuis_usage', 'USD'))

    def test_C12_messages_sans_champs_de_cache_entree_gardee_cache_inconnu(self):
        """Retour de l'intégrateur (test_v540) : un usage Messages qui donne ``input_tokens`` sans les champs de cache garde son entrée
        connue (12, et non 0) ; le cache, lui, reste inconnu : le montant n'est pas calculé (120 serait une sous-estimation s'il y avait du
        cache), la réservation reste incertaine."""
        obs = conso.observer(api('', provider_type='anthropic', prix5682=PRIX), {'input_tokens': 12, 'output_tokens': 5})
        self.assertEqual((obs['entree_connue'], obs['mesure'], obs['montant_micro'], obs['etat_montant']), (12, 'cache_non_declare', None, 'usage_partiel'))
        self.assertEqual((obs['usage']['entree'], obs['usage']['entree_hors_cache'], obs['usage']['cache_lu']), (None, 12, None))      # total non inventé
        cfg = self.appel({'input_tokens': 12, 'output_tokens': 5}, provider_type='anthropic', provider_id='claude', prix5682=PRIX)
        self.assertEqual(self.sql('SELECT input_tokens,output_tokens,mesure FROM ai_usage_v391')[0], {'input_tokens': 12, 'output_tokens': 5, 'mesure': 'cache_non_declare'})
        o = self.observations()[0]
        self.assertEqual((o['entree'], o['hors_cache'], o['entree_cache'], o['cache_ecrit'], o['sortie'], o['montant_micro']), (12, 12, None, None, 5, None))
        r = self.ligne(cfg)
        self.assertEqual((r['state'], r['actual_micro'], r['reason']), ('uncertain', None, 'usage_partiel'))
        vue = ai_gateway.usage_snapshot(self.desk)[0]
        self.assertEqual((vue['input_tokens'], vue['output_tokens'], vue['sans_mesure']), (12, 5, 0))
        self.assertEqual(conso5682.snapshot(self.state)[0]['entree'], 12)
        complet = conso.observer(api('', provider_type='anthropic', prix5682=PRIX), {'input_tokens': 12, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0, 'output_tokens': 5})
        self.assertEqual((complet['entree_connue'], complet['mesure'], complet['montant_micro']), (12, 'complete', 22))      # champs présents à zéro : calcul exact

    def test_C12_grille_figee_pas_de_recalcul_de_l_historique(self):
        self.appel(MESSAGES_ADDITIF, provider_type='anthropic', prix5682=PRIX)
        avant = self.observations()[0]
        self.appel(MESSAGES_ADDITIF, provider_type='anthropic', prix5682=(10, 1, 20, 'usd'))        # la grille change : l'appel passé garde la sienne
        premier, second = self.observations()
        self.assertEqual((premier['montant_micro'], premier['tarif_json']), (220, avant['tarif_json']))
        self.assertEqual(second['montant_micro'], 2200)
        self.assertNotEqual(json.loads(premier['tarif_json'])['version'], json.loads(second['tarif_json'])['version'])
        self.assertEqual(json.loads(premier['tarif_json'])['prix']['cache_lu'], '0.1')

    def test_C12_flux_cumulatif_revise_la_meme_observation(self):
        cfg = api(self.state)
        estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
        bl.preparer_emission(cfg)
        conso5682.note(cfg, {'prompt_tokens': 100, 'completion_tokens': 5}, 'done')              # états cumulatifs d'un même flux
        conso5682.note(cfg, {'prompt_tokens': 100, 'completion_tokens': 40}, 'done')
        o = self.observations()
        self.assertEqual((len(o), o[0]['sortie'], o[0]['revision'], o[0]['montant_micro']), (1, 40, 1, 180))    # 40, pas 45 : rien n'est additionné
        self.assertEqual(conso5682.snapshot(self.state)[0]['sortie'], 40)
        bl.preparer_emission(cfg)                                              # reprise réellement réémise : identifiant de tentative distinct
        conso5682.note(cfg, MESSAGES_CHAT, 'done')
        self.assertEqual(len(self.observations()), 2)
        self.assertGreater(estime, 0)

    def test_C12_appel_interrompu_conserve_puis_mesure_tardive(self):
        cfg = self.appel(None, statut='error', erreur='delai_http_depasse')
        o = self.observations()[0]
        self.assertEqual((o['etat'], o['mesure'], o['entree'], o['cache_ecrit'], o['montant_micro']), ('error', 'absente', None, None, None))
        self.assertEqual(self.ligne(cfg)['state'], 'uncertain')                # peut avoir été facturé : ni effacé, ni libéré
        conso5682.note(cfg, MESSAGES_CHAT, 'done')                             # mesure tardive de la même émission
        o = self.observations()
        self.assertEqual((len(o), o[0]['etat'], o[0]['entree'], o[0]['revision']), (1, 'done', 100, 1))
        conso5682.note(cfg, {}, 'error')                                       # un événement sans mesure ne retire rien de ce qui est connu
        o = self.observations()[0]
        self.assertEqual((o['etat'], o['entree'], o['montant_micro'], o['revision']), ('done', 100, 200, 1))

    def test_C12_migration_additive_idempotente_historique_intact(self):
        db = sqlite3.connect(Path(self.state) / 'desk.sqlite3')
        db.executescript(conso5682.SCHEMA)                                     # table d'avant la 5.6.109
        self.assertNotIn('mesure', {r[1] for r in db.execute('PRAGMA table_info(conso_5682)')})
        moment = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO conso_5682(at,fournisseur,modele,canal,fonction,mission,entree,entree_cache,sortie,raisonnement,quantite,unite,etat) "
                   "VALUES(?,'claude','ancien','api','assistant','',100,1000,10,NULL,NULL,'usd','done')", (moment,))
        db.execute("INSERT INTO conso_5682(at,fournisseur,modele,canal,fonction,mission,entree,entree_cache,sortie,raisonnement,quantite,unite,etat) "
                   "VALUES(?,'ext','ancien','api','assistant','',NULL,NULL,NULL,NULL,NULL,'usd','done')", (moment,))
        db.commit()
        db.close()
        for _ in range(2):                                                     # deux démarrages : même résultat
            bl.preparer({'state_dir': self.state}, 'essai', '9.9.99')
        anciennes = self.observations()
        self.assertEqual([(o['entree'], o['entree_cache'], o['quantite'], o['montant_micro'], o['format_usage'], o['mesure'], o['hors_cache']) for o in anciennes],
                         [(100, 1000, None, None, '', '', None), (None, None, None, None, '', '', None)])       # ni recalculé, ni complété
        self.assertEqual(self.sql("SELECT valeur FROM budget_meta5689 WHERE cle='schema56109'"), [{'valeur': '1'}])
        r = conso.releve(SimpleNamespace(c={'state_dir': self.state, 'ai_providers': {}}, settings=lambda cle, defaut=None: defaut), 'cabinet', 'administrateur', {'detail': '1'})
        self.assertEqual((r['api']['historiques'], r['api']['appels'], r['api']['sans_mesure'], r['api']['appels_sans_montant']), (2, 2, 1, 2))
        self.assertIsNone(r['api']['registre'])                                # aucun montant inventé pour l'historique
        self.assertEqual({a['mesure'] for a in r['detail']['appels']}, {''})


# ================================================================================ F18 : relevé, droits, routes, section
class Bureau(Outils, t34.Base):
    """Bureau complet (dossiers fictifs ALPHA et BRAVO, comptes DURAND et MARTIN), registre préparé comme au démarrage du service."""

    def setUp(self):
        super().setUp()
        self.state = self.desk.c['state_dir']
        self.addCleanup(membres5650.clear)
        self.desk.db.commit()
        bl.preparer(self.desk.c, 'essai', '9.9.99')

    def releve(self, qui='cabinet', role='administrateur', **args):
        return conso.releve(self.desk, qui, role, args)

    def route(self, nom, qui=ADMIN, role='administrateur', methode='GET', **args):
        with patch('agent.web567.actor', return_value=(qui, role)):
            return gw.api_consommation({'REQUEST_METHOD': methode}, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm56109/conso/' + nom, args, methode)

    def empreinte(self):
        """Octets de la base et de son journal : toute écriture, même annulée puis refaite, les change."""
        self.desk.db.commit()
        h = hashlib.sha256()
        for suffixe in ('', '-wal'):
            fichier = Path(self.state) / ('desk.sqlite3' + suffixe)
            h.update(fichier.read_bytes() if fichier.is_file() else b'')
        return h.hexdigest()

    def jeu(self):
        """Trois demandes : DURAND (dossier ALPHA), MARTIN (dossier BRAVO), un automatisme sans auteur ; modèles distincts pour repérer une fuite."""
        self.appel(MESSAGES_CHAT, auteur=DURAND, decision='d-durand', dossier=ALPHA, model='modele-durand')
        self.appel(MESSAGES_ADDITIF, auteur=MARTIN, decision='d-martin', dossier=BETA, model='modele-martin', provider_type='anthropic', provider_id='claude', prix5682=PRIX)
        self.appel(None, model='modele-auto')


class Releve(Bureau):
    def test_F18_modes_separes_par_unite_pas_de_prix_api_sur_un_forfait(self):
        self.appel(MESSAGES_ADDITIF, provider_type='anthropic', provider_id='claude', model='claude-fictif', prix5682=PRIX)
        self.forfait(input_usd_per_million=1, output_usd_per_million=2)        # tarif d'API présent dans le réglage : il ne doit pas servir
        economie569.record_local_usage({'state_dir': self.state, 'purpose': 'mail_triage', 'model': 'modele-local-fictif'},
                                       {'prompt_eval_count': 1200, 'eval_count': 80, 'total_duration': 90_000_000_000})
        economie569.record_local_usage({'state_dir': self.state, 'purpose': 'mail_triage', 'model': 'modele-local-fictif'}, {'eval_count': 20})   # entrée non renvoyée
        bl.noter_quantite(self.state, 'appel-service-1', 'pappers', 'credit_pappers', 3, 'declaree', operation='document')
        r = self.releve()
        self.assertEqual((r['disponible'], r['portee']), (True, 'cabinet'))
        self.assertEqual((r['api']['appels'], r['api']['registre']['regle_micro'], r['api']['registre']['calcule_micro']), (1, 220, 220))
        abonnement = r['abonnement']
        self.assertEqual((abonnement['appels'], abonnement['entree'], abonnement['cache_lu'], abonnement['sortie']), (1, 1000, 400, 200))
        self.assertNotIn('registre', abonnement)                               # aucun montant en dollars pour un forfait
        self.assertEqual(self.observations()[1]['montant_micro'], None)
        self.assertEqual(r['api']['forfaits'], 1)
        self.assertEqual((r['local']['appels'], r['local']['modeles'][0]['entree'], r['local']['modeles'][0]['entree_n'], r['local']['modeles'][0]['sortie']), (2, 1200, 1, 100))
        self.assertEqual([(s['fournisseur'], s['unite'], s['quantite_milli'], s['etat']) for s in r['services']], [('pappers', 'credit_pappers', 3000, 'declaree')])
        html = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {})
        for titre in ('API (facturée à l’usage)', 'Abonnement (forfait)', 'IA locale', 'Services externes'):
            self.assertEqual(html.count('<h3>%s</h3>' % titre), 1, titre)
        bloc = lambda debut, fin: html[html.index('<h3>%s</h3>' % debut):html.index('<h3>%s</h3>' % fin)]
        self.assertIn('0,00022 $', bloc('API (facturée à l’usage)', 'Abonnement (forfait)'))
        forfait = bloc('Abonnement (forfait)', 'IA locale')
        self.assertNotIn('$', forfait)                                         # ni prix d'API, ni dollars, dans le bloc du forfait
        self.assertIn('Le forfait n’est pas converti au tarif de l’API', forfait)
        self.assertIn('Solde du quota : Non fourni', forfait)
        local = bloc('IA locale', 'Services externes')
        self.assertIn('Pas de facturation API ; coût du poste non mesuré', local)
        self.assertIn('Le coût du local n’est pas nul', local)
        self.assertIn('1,5 min', local)
        self.assertIn('partiel', local)                                        # une génération sans compteur d'entrée : somme partielle, pas un zéro
        self.assertNotIn('$', local.split('Coûts du poste')[0])
        services = bloc('Services externes', 'Plafonds')
        self.assertIn('credit pappers', services)
        self.assertIn('ni des dollars ni des jetons', services)
        self.assertNotIn('$', services)

    def test_F18_somme_partielle_explicite_et_non_fourni_jamais_zero(self):
        self.appel(MESSAGES_CHAT, model='mesure')
        self.appel(None, model='sans-mesure')
        self.appel({'prompt_tokens': 7}, model='mesure')
        r = self.releve()
        self.assertEqual((r['api']['incomplet'], r['api']['appels_sans_montant'], r['appels_sans_mesure'], r['api']['registre']['incertain_n']), (True, 2, 1, 2))
        self.assertEqual((r['api']['entree'], r['api']['sortie'], r['api']['partiel']), (107, 50, True))
        html = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {})
        self.assertIn('total connu, incomplet</span> 2 appel(s) sans montant confirmé', html)
        self.assertIn('2 appel(s) sans montant calculé : usage ou tarif non fourni', html)
        ligne = re.search(r'<tr><td>ext</td><td>sans-mesure</td>.*?</tr>', html).group(0)
        self.assertEqual(ligne.count('Non fourni'), 5)                         # entrée, cache lu, cache écrit, sortie, coût : inconnus, pas 0
        self.assertNotIn('>0<', ligne.replace('<td class="m109-n">1</td>', ''))
        mesure = re.search(r'<tr><td>ext</td><td>mesure</td>.*?</tr>', html).group(0)
        self.assertIn('<td class="m109-n m109-t">107</td>', mesure)              # l'entrée est connue pour les deux appels
        self.assertIn('50 <span class="m109-etat incomplet">partiel</span>', mesure)       # la sortie pour un seul : somme partielle, dite telle
        self.assertIn('ce n’est pas la facture du fournisseur', html)
        vide = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {'fournisseur': 'aucun-fournisseur'})
        self.assertIn('Coût API connu</dt><dd>Non fourni', vide)               # aucun appel : pas « 0,00 $ »
        self.assertIn('Aucun appel à une API sur la période.', vide)

    def test_F18_plafonds_des_missions_enfants_compris_et_total_sans_doublon(self):
        self.desk.setting('ai:politique', {'mode': 'auto', 'plafond_mission_usd': 0.5})
        reservations = [conso5682.reserve(self.state, 'mission-9', 0.2, 'usd', 0.5, uid, 'ext', 'fictif', 'assistant') for uid in ('etape-parent', 'etape-enfant')]
        with self.assertRaisesRegex(Stop, 'budget_mission_atteint'):           # l'enfant de trop : même plafond que la mission
            conso5682.reserve(self.state, 'mission-9', 0.2, 'usd', 0.5, 'etape-petit-enfant', 'ext', 'fictif', 'assistant')
        for uid, usage in (('etape-parent', MESSAGES_CHAT), ('etape-enfant', {'prompt_tokens': 200, 'completion_tokens': 100})):
            cfg = api(self.state, decision5682=uid, mission5682='mission-9')
            estime = ai_gateway.estimate_and_check_budget(cfg, MESSAGES, 900)
            bl.preparer_emission(cfg)
            ai_gateway.record_usage(cfg, {'usage': usage}, estime, 'done', MESSAGES)
        for reservation in reservations:
            conso5682.settle(self.state, reservation, 'consomme')
        r = self.releve(mission='mission-9')
        self.assertEqual((r['mission']['appels'], r['mission']['reel_micro'], r['mission']['engage_micro'], r['mission']['etat']), (2, 600, 600, 'reel'))
        self.assertEqual(r['api']['registre']['regle_micro'], 600)              # projection des deux appels : aucun montant récapitulatif en plus
        self.assertEqual((r['detail']['total'], sorted(a['retenu_micro'] for a in r['detail']['appels'])), (2, [200, 400]))
        html = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {'mission': 'mission-9'})
        self.assertIn('2 appel(s), étapes et sous-agents compris', html)
        self.assertIn('aucun montant récapitulatif ne s’y ajoute', html)
        self.assertIn('Les étapes et les sous-agents d’une mission partagent le plafond de leur mission', ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {}))
        self.assertEqual(bl.mission(self.desk, 'mission-9')['reel_micro'], 600)                 # même calcul que la route existante

    def test_F18_devises_et_unites_jamais_melangees(self):
        bl.saisir_cout(self.desk, {'categorie': 'licence', 'montant': '20', 'devise': 'eur'}, ADMIN)
        bl.saisir_cout(self.desk, {'categorie': 'energie', 'montant': '5'}, ADMIN)
        b = bl.bilan_mensuel(self.desk)
        self.assertEqual((b['total_connu_micro'], b['total_complet'], b['totaux_par_devise']), (5_000_000, False, {'USD': 5_000_000, 'EUR': 20_000_000}))
        self.assertIn('aucun n’est converti', ' '.join(b['limites']))
        page = gw._section_bilan(self.desk, None)
        self.assertIn('<td>20,00 EUR</td>', page)
        self.assertNotIn('20,00 $', page)                                       # le signe du dollar n'est pas posé sur un montant en euros
        self.assertIn('Autres devises, non converties ni additionnées : <strong>20,00 EUR</strong>', page)
        self.assertIn('<select name="devise"><option value="USD">', page)
        self.assertNotIn('en dollars<input name="montant"', page)
        for mauvaise in ('CRD', 'EU', 'E1R', 'euros'):
            with self.assertRaisesRegex(Stop, 'devise_invalide', msg=mauvaise):
                bl.saisir_cout(self.desk, {'categorie': 'stockage', 'montant': '1', 'devise': mauvaise}, ADMIN)
        vue = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {})
        self.assertIn('catégorie « Licences » (forfaits compris) : <strong>20,00 EUR</strong> <small>connu</small>', vue)
        self.assertIn('<span>Énergie</span> : <strong>5,00 $</strong> <small>connu</small>', vue)
        self.assertIn('<span>Maintenance</span> : <strong>non renseigné</strong>', vue)

    def test_F18_quantites_de_service_typees_idempotentes(self):
        for unite in ('CRD', 'usd', 'eur', 'credits', 'jetons', 'Crédit Pappers', ''):      # jamais une fausse devise, jamais une unité ambiguë
            with self.assertRaisesRegex(Stop, 'unite_service_invalide', msg=unite):
                bl.noter_quantite(self.state, 'appel-1', 'pappers', unite, 1)
        self.assertEqual(bl.noter_quantite(self.state, 'appel-1', 'pappers', 'credit_pappers', 3), {'revision': 0, 'compte': True})
        self.assertEqual(bl.noter_quantite(self.state, 'appel-1', 'pappers', 'credit_pappers', 3), {'revision': 0, 'compte': False})      # même relevé reçu deux fois
        self.assertEqual(bl.noter_quantite(self.state, 'appel-1', 'pappers', 'credit_pappers', '4.5', 'rapprochee'), {'revision': 1, 'compte': False})
        bl.noter_quantite(self.state, 'appel-1', 'pappers', 'credit_pappers', None)              # un relevé sans quantité ne remplace rien
        bl.noter_quantite(self.state, 'appel-2', 'pappers', 'credit_pappers', None)              # quantité inconnue : gardée inconnue
        bl.noter_quantite(self.state, 'appel-3', 'tavily', 'recherche_tavily', 2, 'estimee')
        lignes = self.sql('SELECT call_id,quantite_milli,etat,revision FROM budget_quantites56109 ORDER BY call_id')
        self.assertEqual(lignes, [{'call_id': 'appel-1', 'quantite_milli': 4500, 'etat': 'rapprochee', 'revision': 1},
                                  {'call_id': 'appel-2', 'quantite_milli': None, 'etat': 'inconnue', 'revision': 0},
                                  {'call_id': 'appel-3', 'quantite_milli': 2000, 'etat': 'estimee', 'revision': 0}])
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM budget_reservations5689')[0]['n'], 0)     # aucune réservation, aucune dépense créée
        r = self.releve()
        self.assertEqual({(s['fournisseur'], s['unite'], s['etat']): s['quantite_milli'] for s in r['services']},
                         {('pappers', 'credit_pappers', 'rapprochee'): 4500, ('pappers', 'credit_pappers', 'inconnue'): None, ('tavily', 'recherche_tavily', 'estimee'): 2000})
        self.assertIsNone(r['api']['registre'])                                 # des crédits de service ne deviennent pas un coût d'API
        self.assertEqual(self.releve(fournisseur='tavily')['services'][0]['unite'], 'recherche_tavily')
        self.assertEqual(self.releve(canal='api')['services'], [])

    def test_F18_filtres_periode_fournisseur_modele_canal_fonction_mission(self):
        self.appel(MESSAGES_CHAT, model='modele-a', purpose='assistant', mission5682='mission-a')
        self.appel(MESSAGES_ADDITIF, provider_type='anthropic', provider_id='claude', model='modele-b', purpose='legal_analysis', prix5682=PRIX)
        self.forfait()
        ancien = self.appel({'prompt_tokens': 900, 'completion_tokens': 1}, model='modele-ancien')
        passe = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.ecrire('UPDATE conso_5682 SET at=? WHERE modele=?', passe, 'modele-ancien')
        self.ecrire('UPDATE budget_reservations5689 SET created_at=? WHERE call_id=?', passe, ancien['_budget_call5689'])
        tout = self.releve()
        self.assertEqual((tout['api']['appels'], tout['abonnement']['appels'], tout['api']['registre']['regle_micro']), (2, 1, 420))
        self.assertEqual(self.releve(periode='jour')['api']['appels'], 2)
        self.assertEqual(self.releve(periode='7j')['api']['appels'], 2)
        jour = lambda decalage: (datetime.now(PARIS) + timedelta(days=decalage)).date().isoformat()
        large = self.releve(periode='perso', debut=jour(-60), fin=jour(0))
        self.assertEqual((large['api']['appels'], large['api']['registre']['regle_micro']), (3, 420 + 902))
        self.assertEqual(self.releve(periode='perso', debut=jour(-60), fin=jour(-30))['api']['appels'], 1)
        for filtre, appels, cout in (({'fournisseur': 'claude'}, 1, 220), ({'modele': 'modele-a'}, 1, 200), ({'fonction': 'legal_analysis'}, 1, 220), ({'mission': 'mission-a'}, 1, 200),
                                     ({'canal': 'api'}, 2, 420)):
            r = self.releve(**filtre)
            self.assertEqual((r['api']['appels'], r['api']['registre']['regle_micro']), (appels, cout), filtre)
        forfait = self.releve(canal='abonnement')
        self.assertEqual((forfait['api']['appels'], forfait['abonnement']['appels'], forfait['api']['registre']), (0, 1, None))
        self.assertEqual((self.releve(canal='local')['api']['appels'], self.releve(canal='local')['local']['appels']), (0, 0))
        for mauvais, code in (({'canal': 'gratuit'}, 'filtre_invalide'), ({'periode': 'siecle'}, 'periode_invalide'), ({'page': 'deux'}, 'filtre_invalide')):
            with self.assertRaisesRegex(Stop, code, msg=mauvais):
                self.releve(**mauvais)
        detail = self.releve(detail='1', par_page='1', page='2')
        self.assertEqual((detail['detail']['total'], detail['detail']['pages'], len(detail['detail']['appels'])), (3, 3, 1))
        page = ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {'detail': '1', 'par_page': '1', 'page': '2'})
        self.assertIn('Page 2 sur 3 · 3 appel(s)', page)
        self.assertIn('data-m109-page="1"', page)
        self.assertIn('data-m109-page="3"', page)


class Droits(Bureau):
    def test_F18_un_collaborateur_ne_lit_que_ses_propres_demandes(self):
        self.jeu()
        economie569.record_local_usage({'state_dir': self.state, 'purpose': 'mail_triage', 'model': 'modele-local-fictif'}, {'prompt_eval_count': 10, 'eval_count': 5})
        admin = self.releve(ADMIN, 'administrateur', detail='1')
        self.assertEqual((admin['portee'], admin['api']['appels'], admin['detail']['total'], admin['local']['appels']), ('cabinet', 3, 3, 1))
        self.assertIn('plafonds', admin)
        durand = self.releve(DURAND, 'avocat', detail='1')
        texte = json.dumps(durand, ensure_ascii=False, default=str)
        self.assertEqual((durand['portee'], durand['api']['appels'], durand['api']['registre']['regle_micro'], durand['detail']['total']), ('personnelle', 1, 200, 1))
        for interdit in ('modele-martin', 'modele-auto', 'claude', MARTIN, 'modele-local-fictif'):
            self.assertNotIn(interdit, texte, interdit)
        self.assertNotIn('plafonds', durand)                                    # plafonds et bilan du cabinet : administrateur seulement
        self.assertNotIn('couts_fixes', durand)
        self.assertIsNone(durand['local'])
        force = self.releve(DURAND, 'avocat', utilisateur=MARTIN, detail='1')    # le paramètre « utilisateur » n'élargit rien
        self.assertEqual((force['filtres']['utilisateur'], force['api']['appels']), (DURAND, 1))
        self.assertNotIn('modele-martin', json.dumps(force, ensure_ascii=False, default=str))
        self.assertEqual(self.releve('', 'assistant')['api']['appels'], 0)       # compte vide : rien, surtout pas les automatismes sans auteur
        self.assertEqual(self.releve('sans-demande@example.test', 'assistant')['api']['appels'], 0)
        cible = self.releve(ADMIN, 'administrateur', utilisateur=MARTIN)
        self.assertEqual((cible['portee'], cible['api']['appels'], cible['api']['registre']['regle_micro'], cible['local']), ('personnelle', 1, 220, None))
        html = ia5682.vue_consommation(self.desk, DURAND, 'avocat', {'detail': '1'})
        self.assertIn('vos demandes seulement', html)
        self.assertNotIn('modele-martin', html)
        self.assertIn('réservé à l’administrateur', html)
        # mutation : sans le garde-fou de portée, la même lecture montre les demandes des autres (ce test échouerait si le garde-fou était retiré)
        with patch.object(conso, '_portee', lambda f, p='', dossier=True: ([], [])):
            self.assertIn('modele-martin', json.dumps(self.releve(DURAND, 'avocat', detail='1'), ensure_ascii=False, default=str))

    def test_F18_dossier_reserve_ou_inconnu_meme_refus_neutre(self):
        self.jeu()
        self.appel(MESSAGES_CHAT, auteur=DURAND, decision='d-durand-bravo', dossier=BETA, model='modele-durand-bravo')
        self.assertEqual(self.releve(DURAND, 'avocat', dossier=BETA)['api']['appels'], 1)        # tant que le dossier lui est ouvert
        self.assertEqual(self.releve(ADMIN, 'administrateur', dossier=BETA)['api']['appels'], 2)
        membres5650.set_reserve(self.state, BETA, [MARTIN])
        reserve = self.route('releve', DURAND, 'avocat', dossier=BETA)
        inconnu = self.route('releve', DURAND, 'avocat', dossier='dossier-inexistant')
        self.assertEqual((reserve['status'], reserve['body']), ('404 Not Found', inconnu['body']))   # indiscernables
        self.assertEqual(json.loads(reserve['body'])['error'], 'introuvable')
        self.assertNotIn('modele', reserve['body'])
        for nom in ('vue', 'export'):
            self.assertEqual(self.route(nom, DURAND, 'avocat', dossier=BETA)['status'], '404 Not Found', nom)
        self.assertEqual(self.route('releve', ADMIN, 'administrateur', dossier='dossier-inexistant')['status'], '404 Not Found')
        self.assertEqual(json.loads(self.route('releve', ADMIN, 'administrateur', dossier=BETA)['body'])['api']['appels'], 2)
        sien = self.releve(DURAND, 'avocat', detail='1')                         # sa propre demande dans le dossier devenu réservé : retirée de sa vue
        self.assertEqual((sien['api']['appels'], [a['modele'] for a in sien['detail']['appels']]), (1, ['modele-durand']))
        self.assertEqual(self.releve(MARTIN, 'avocat', dossier=BETA)['api']['appels'], 1)
        # mutation : sans le retrait des dossiers réservés, la demande réapparaît
        with patch.object(conso, 'empreinte_dossier', lambda m: ''):
            self.assertEqual(self.releve(DURAND, 'avocat')['api']['appels'], 2)

    def test_F18_controle_des_droits_en_panne_503_jamais_un_acces_elargi(self):
        self.jeu()
        with patch('agent.membres5650.hidden_for', side_effect=Stop('registre_des_acces_illisible')):
            for nom in ('releve', 'vue', 'export'):
                sortie = self.route(nom, DURAND, 'avocat')
                self.assertEqual(sortie['status'], '503 Service Unavailable', nom)
                self.assertNotIn('modele-', sortie['body'])
            self.assertIn('Relevé indisponible : le contrôle des droits n’a pas pu être fait.', ia5682.consommation_html(self.desk, DURAND, 'avocat'))
        with patch('agent.membres5650.effective_rights', side_effect=Stop('registre_des_acces_illisible')):
            self.assertEqual(self.route('export', DURAND, 'avocat')['status'], '503 Service Unavailable')


class Routes(Bureau):
    def test_F18_routes_lecture_seule_roles_et_entetes(self):
        self.jeu()
        for nom, genre in (('releve', 'application/json'), ('vue', 'text/html'), ('export', 'text/csv')):
            sortie = self.route(nom)
            self.assertEqual(sortie['status'], '200 OK', nom)
            self.assertTrue(sortie['kind'].startswith(genre), nom)
            self.assertIn(('Cache-Control', 'private, no-store'), sortie['headers'], nom)
            self.assertEqual(self.route(nom, methode='POST')['status'], '405 Method Not Allowed', nom)
            self.assertEqual(self.route(nom, DURAND, 'avocat')['status'], '200 OK', nom)
            self.assertEqual(self.route(nom, DURAND, 'visiteur')['status'], '403 Forbidden', nom)
        self.assertEqual(self.route('inconnue')['status'], '404 Not Found')
        self.assertEqual(self.route('releve', periode='siecle')['status'], '400 Bad Request')
        self.assertIn('Période non reconnue', json.loads(self.route('releve', periode='siecle')['body'])['message'])
        # écriture refusée à tous hors administrateur par le contrôle central ; lecture ouverte, portée décidée par le service
        for role in ('avocat', 'assistant'):
            self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/m56109/conso/releve'))
            self.assertTrue(standalone_auth.allowed(role, 'GET', '/api440/m56109/conso/export'))
        self.assertIsNone(membres5650.category('GET', '/api440/m56109/conso/releve'))
        self.assertIn('exporter', membres5650.fine_rights('GET', '/api440/m56109/conso/export'))
        membres5650.set_rights(self.state, ADMIN, DURAND, 'avocat', {'exporter': False})
        self.assertEqual(self.route('export', DURAND, 'avocat')['status'], '403 Forbidden')      # droit « exporter » retiré : pas d'export
        self.assertEqual(self.route('releve', DURAND, 'avocat')['status'], '200 OK')
        # raccordement réel au produit : l'adresse est servie par l'application, avec l'en-tête privé
        reponse = self.request('/api440/m56109/conso/releve', query='periode=mois')
        self.assertTrue(reponse['status'].startswith('200'))
        self.assertEqual(reponse['headers']['Cache-Control'], 'private, no-store')
        self.assertEqual(json.loads(reponse['body'])['api']['appels'], 3)
        self.assertTrue(self.request('/api440/m56109/conso/vue')['status'].startswith('200'))

    def test_F18_export_csv_filtre_protege_et_trace(self):
        self.jeu()
        self.appel(MESSAGES_CHAT, auteur=DURAND, model='=SOMME(A1:A9)', purpose='@fonction')
        bl.noter_quantite(self.state, 'appel-service-1', 'pappers', 'credit_pappers', 3)
        sortie = self.route('export')
        self.assertIn('attachment; filename="consommation-', dict(sortie['headers'])['Content-Disposition'])
        self.assertTrue(sortie['body'].startswith('﻿'))
        lignes = list(csv.reader(io.StringIO(sortie['body'].lstrip('﻿')), delimiter=';'))
        self.assertEqual(tuple(lignes[0]), conso.ENTETES)
        self.assertTrue(all(len(x) == len(conso.ENTETES) for x in lignes))
        par_modele = {x[4]: dict(zip(conso.ENTETES, x)) for x in lignes[1:]}
        self.assertIn("'=SOMME(A1:A9)", par_modele)                              # jamais interprété comme une formule
        self.assertEqual(par_modele["'=SOMME(A1:A9)"]['fonction'], "'@fonction")
        martin = par_modele['modele-martin']
        self.assertEqual((martin['nature'], martin['unite_quantites'], martin['entree'], martin['entree_hors_cache'], martin['cache_lu'], martin['sortie']),
                         ('appel_ia', 'jetons', '1100', '100', '1000', '10'))
        self.assertEqual((martin['montant_calcule'], martin['devise'], martin['origine_montant'], martin['tarif_cache_lu_par_million'], martin['etat_registre'], martin['origine_registre']),
                         ('0,000220', 'USD', 'calcule_depuis_usage', '0.1', 'settled', 'calcule_depuis_usage'))
        auto = par_modele['modele-auto']
        self.assertEqual((auto['entree'], auto['sortie'], auto['montant_calcule'], auto['etat_mesure'], auto['etat_registre'], auto['montant_retenu']), ('', '', '', 'absente', 'uncertain', ''))
        service = next(x for x in lignes[1:] if x[0] == 'service')
        self.assertEqual((service[3], dict(zip(conso.ENTETES, service))['unite_quantites'], dict(zip(conso.ENTETES, service))['quantite_service']), ('pappers', 'credit_pappers', '3,000'))
        self.assertNotIn('texte fictif', sortie['body'])                         # ni texte d'un échange, ni clé
        filtre = self.route('export', fournisseur='claude')
        self.assertEqual([x[4] for x in csv.reader(io.StringIO(filtre['body'].lstrip('﻿')), delimiter=';')][1:], ['modele-martin'])
        durand = self.route('export', DURAND, 'avocat', utilisateur=MARTIN)
        self.assertNotIn('modele-martin', durand['body'])
        self.assertNotIn('pappers', durand['body'])
        self.assertIn('modele-durand', durand['body'])
        traces = [json.loads(r['data']) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='consommation56109_export' ORDER BY id")]
        self.assertEqual((len(traces), traces[0]['portee'], traces[1]['filtres'], traces[2]['portee'], traces[2]['filtres']), (3, 'cabinet', ['fournisseur'], 'personnelle', []))
        self.assertNotIn('modele-', json.dumps(traces))
        with patch.object(conso, 'pret', lambda db: False):                     # relevé illisible : pas de fichier de zéros
            self.assertEqual(self.route('export')['status'], '503 Service Unavailable')

    def test_F18_aucune_ecriture_aucun_appel_a_la_lecture_meme_ia_en_pause(self):
        self.jeu()
        self.forfait()
        bl.noter_quantite(self.state, 'appel-service-1', 'pappers', 'credit_pappers', 3)
        arret5643.pause_all(self.desk, True, ADMIN)                              # IA en pause : la lecture du relevé reste possible
        avant = self.empreinte()
        ecritures = []
        self.desk.db.set_trace_callback(lambda sql: ecritures.append(sql) if re.match(r'\s*(UPDATE|INSERT|DELETE|CREATE|ALTER|REPLACE|DROP)', sql, re.I) else None)
        interdit = AssertionError('appel de modèle ou de réseau pendant une lecture du relevé')
        import socket
        with patch('agent.model.Model.complete', side_effect=interdit), patch('agent.common.HTTP.json', side_effect=interdit), \
                patch.object(socket.socket, 'connect', side_effect=interdit), patch.object(socket, 'create_connection', side_effect=interdit), \
                patch('agent.ai_gateway.record_usage', side_effect=interdit), patch('agent.ai_gateway.estimate_and_check_budget', side_effect=interdit):
            for qui, role in ((ADMIN, 'administrateur'), (DURAND, 'avocat')):
                self.assertEqual(self.route('releve', qui, role, detail='1', mission='mission-a')['status'], '200 OK')
                self.assertEqual(self.route('vue', qui, role, detail='1')['status'], '200 OK')
                self.assertIn('id="consommation-couts"', ia5682.consommation_html(self.desk, qui, role))
            ia5682._usage(self.desk)
            ai_gateway.usage_snapshot(self.desk)
        self.desk.db.set_trace_callback(None)
        self.assertEqual(ecritures, [])
        self.assertEqual(self.empreinte(), avant)                                # pas un octet écrit dans la base ni dans son journal
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_F18_releve_indisponible_sans_migration_jamais_un_faux_zero(self):
        with tempfile.TemporaryDirectory() as nu:
            sqlite3.connect(Path(nu) / 'desk.sqlite3').close()
            conso5682.note(api(nu), MESSAGES_CHAT, 'done')                       # une observation existe, mais le registre n'a pas été préparé
            desk = SimpleNamespace(c={'state_dir': nu, 'ai_providers': {}}, settings=lambda cle, defaut=None: defaut)
            avant = (Path(nu) / 'desk.sqlite3').read_bytes()
            r = conso.releve(desk, 'cabinet', 'administrateur', {})
            self.assertEqual((r['disponible'], 'api' in r), (False, False))
            html = ia5682.vue_consommation(desk, 'cabinet', 'administrateur', {})
            self.assertIn('Relevé indisponible', html)
            self.assertNotIn('0,00', html)
            with self.assertRaisesRegex(Stop, 'releve_indisponible'):
                conso.export_csv(desk, 'cabinet', 'administrateur', {})
            self.assertEqual((Path(nu) / 'desk.sqlite3').read_bytes(), avant)    # la lecture n'a rien créé ni migré
        absent = SimpleNamespace(c={'state_dir': str(Path(self.state) / 'inexistant'), 'ai_providers': {}}, settings=lambda cle, defaut=None: defaut)
        self.assertFalse(conso.releve(absent, 'cabinet', 'administrateur', {})['disponible'])
        self.assertFalse((Path(self.state) / 'inexistant').exists())


class Section(Bureau):
    def test_F18_section_unique_dans_les_parametres_et_interrupteur(self):
        self.jeu()
        carte = ia5682.card(self.desk)
        self.assertEqual(carte.count('<h2>Consommation et coûts</h2>'), 1)        # une seule section de ce nom
        self.assertEqual(carte.count('id="consommation-couts"'), 1)
        self.assertNotIn('<legend>Consommation et coûts</legend>', carte)
        self.assertIn('<summary>Derniers choix de l’IA</summary>', carte)
        self.assertNotIn('Usage du mois et derniers choix de l’IA', carte)
        for attendu in ('data-m109="m56109/conso"', 'name="periode"', 'name="fournisseur"', 'name="modele"', 'name="dossier"', 'name="utilisateur"', 'Exporter (CSV)',
                        'Prix et tokens', 'value="prix"', 'value="tokens"', 'il ne change rien à ce qui est enregistré pour les plafonds', 'parametres/gouvernance#budget',
                        'parametres/gouvernance#bilan', '<script defer src="static/v56109-conso.js"></script>'):
            self.assertIn(attendu, carte, attendu)
        self.assertNotRegex(carte, r'<script(?![^>]*\bdefer\b)[^>]*>')            # aucun script en ligne
        self.assertNotRegex(ia5682.consommation_html(self.desk, DURAND, 'avocat'), r'name="utilisateur"')
        # un texte hostile dans un nom de modèle est échappé
        self.appel(MESSAGES_CHAT, model='<script>alert(1)</script>')
        self.assertNotIn('<script>alert', ia5682.vue_consommation(self.desk, 'cabinet', 'administrateur', {'detail': '1'}))
        # interrupteur éteint : l'ancienne présentation reste entière, avec les corrections de C12
        interface56100.regler_cabinet(self.desk, False)
        ancienne = ia5682.card(self.desk)
        self.assertNotIn('id="consommation-couts"', ancienne)
        self.assertIn('<summary>Usage du mois et derniers choix de l’IA</summary>', ancienne)
        self.assertIn('Non fourni', ancienne)
        self.assertIn('(partiel', ancienne)
        # les droits ne dépendent jamais de l'interrupteur
        self.assertEqual(self.route('releve', DURAND, 'avocat', dossier='dossier-inexistant')['status'], '404 Not Found')
        self.assertEqual(json.loads(self.route('releve', DURAND, 'avocat')['body'])['api']['appels'], 1)

    def test_F18_page_des_parametres_et_page_du_budget_raccordees(self):
        self.jeu()
        # usage présent, plafond fixé (colonne « Part » en pourcentage) et nom de modèle portant un « % » : la carte entre dans un gabarit « % »
        # de ia540.page ; sans le doublement des « % » à l'insertion, la page des paramètres tombait dès qu'un pourcentage s'affichait
        self.appel(MESSAGES_CHAT, model='modele-100%')
        self.desk.setting(bl.SETTING, {'mois_usd': '10', 'cabinet_usd': None, 'revision': 1})
        self.assertIn('<td class="m109-n">0 %</td>', ia5682.card(self.desk))
        for chemin, requete in (('/parametres', 'rubrique=ia'), ('/ia-externe', '')):
            page = self.request(chemin, query=requete)
            self.assertTrue(page['status'].startswith('200'), chemin)
            self.assertEqual(page['body'].count('<h2>Consommation et coûts</h2>'), 1, chemin)
            self.assertIn('<td class="m109-n">0 %</td>', page['body'], chemin)   # un seul « % » à l'arrivée : ni doublé, ni perdu
            self.assertIn('modele-100%</td>', page['body'], chemin)
            self.assertNotIn('%%', page['body'].split('id="consommation-couts"')[1].split('</section>')[0], chemin)
        self.assertIn('static/v56109-conso.js', page['body'])
        self.assertIn('modele-martin', page['body'])                              # l'administrateur voit tout le cabinet
        # mutation : sans le doublement des « % » à l'insertion de la carte, la même page ne s'affiche plus (ce test échouerait)
        vraie = ia5682.card

        class SansDoublement(str):
            def replace(self, *a, **k):
                return self
        with patch.object(ia5682, 'card', lambda desk: SansDoublement(vraie(desk))):
            self.assertFalse(self.request('/ia-externe')['status'].startswith('200'))
        with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')):
            budget = gw.page(self.desk, {}, '/p', {'REQUEST_METHOD': 'GET'}, {})
        self.assertIn('href="/p/parametres?rubrique=ia#consommation-couts"', budget)
        self.assertIn('appel(s) sans montant certain', budget)                    # un appel abouti sans usage n'est pas dit « coupé »
        # ni seconde page, ni entrée de menu : la section vit dans les paramètres existants
        from agent import pages5636, parametres5621
        self.assertFalse([p for p in pages5636.PAGES if 'conso' in p])
        self.assertFalse([r for r in parametres5621.RUBRIQUES if 'onsommation et co' in r[1]])
        self.assertFalse([n for n in web440.NAV if 'conso' in n[0]])


class Contrat(unittest.TestCase):
    def test_F18_statiques_declares_et_script_en_lecture_seule(self):
        self.assertEqual(web440.STATIC['/static/v56109-conso.js'], 'text/javascript')
        self.assertEqual(web440.STATIC['/static/v56109-conso.css'], 'text/css')
        self.assertIn('v56109-conso.css', shell501.BUNDLE)
        js = (ROOT / 'agent' / 'static' / 'v56109-conso.js').read_text(encoding='utf-8')
        self.assertNotRegex(js, r"method:\s*'(POST|PUT|PATCH|DELETE)'")            # des lectures seulement
        self.assertNotIn('eval(', js)
        css = (ROOT / 'agent' / 'static' / 'v56109-conso.css').read_text(encoding='utf-8')
        self.assertIn('.m109[data-affichage="prix"] .m109-t, .m109[data-affichage="tokens"] .m109-p { display: none; }', css)
        self.assertIn(':focus-visible', css)
        self.assertIn(css.strip()[:200], (ROOT / 'agent' / 'static' / shell501.BUNDLE_FILE).read_text(encoding='utf-8'))       # feuille unique régénérée

    def test_F18_script_requete_des_filtres(self):
        """Fonctions pures du script (Node) : champs vides omis, dates pour la seule période personnalisée, valeurs encodées, page du détail."""
        import shutil
        import subprocess
        if not shutil.which('node'):
            self.skipTest('Node absent')
        code = ("const m=require(process.argv[1]);console.log(JSON.stringify([m.requete({periode:'mois',debut:'2026-11-01',fin:'2026-11-02',fournisseur:' ext ',modele:''}),"
                "m.requete({periode:'perso',debut:'2026-11-01',fin:'2026-11-02',dossier:'a b&c'},2),m.affichage('prix'),m.affichage('autre')]))")
        r = subprocess.run(['node', '-e', code, str(ROOT / 'agent' / 'static' / 'v56109-conso.js')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ['periode=mois&fournisseur=ext', 'periode=perso&debut=2026-11-01&fin=2026-11-02&dossier=a%20b%26c&detail=1&page=2', 'prix', 'tout'])

    def test_F18_tables_declarees_au_contrat_d_export(self):
        from agent import export5688
        exportees = {t[2]: t for t in export5688.tables_exportees()} if hasattr(export5688, 'tables_exportees') else {t[2]: t for t in export56108.EXPORT}
        self.assertEqual(exportees['budget_quantites56109'][:2], ('gouvernance', 'quantites_de_services'))
        self.assertIn('budget_reservations5689', {t[2] for t in export5688.TABLES})    # le registre, avec l'usage normalisé et la grille de chaque appel
        exclues = dict(export56108.EXPORT_EXCLUES)
        self.assertEqual(exclues['conso_5682'], export56108.MOTIFS['ia'])           # observations : écran et export CSV filtré, sous contrôle des droits
        self.assertNotIn('budget_quantites56109', exclues)

    def test_F18_textes_traduits_en_anglais_allemand_espagnol(self):
        import test_v56107_langues as langues
        with tempfile.TemporaryDirectory() as state:
            sqlite3.connect(Path(state) / 'desk.sqlite3').close()
            bl.preparer({'state_dir': state}, 'essai', '9.9.99')
            banc = Outils()
            banc.state = state
            banc.appel(MESSAGES_CHAT)
            banc.appel(None)
            banc.appel(MESSAGES_ADDITIF, provider_type='anthropic', provider_id='claude', prix5682=PRIX)
            banc.forfait()
            economie569.record_local_usage({'state_dir': state, 'purpose': 'mail_triage', 'model': 'modele-local-fictif'}, {'prompt_eval_count': 10, 'eval_count': 5})
            bl.noter_quantite(state, 'appel-service-1', 'pappers', 'credit_pappers', 3)
            membres5650.clear()
            desk = SimpleNamespace(c={'state_dir': state, 'ai_providers': {}, 'matters_file': str(Path(state) / 'absent.json'), 'nextcloud': {'roots': []}},
                                   settings=lambda cle, defaut=None: defaut)
            html = ia5682.consommation_html(desk, 'cabinet', 'administrateur') + ia5682.vue_consommation(desk, 'cabinet', 'administrateur', {'detail': '1', 'mission': ''})
        self.assertIn('Coût API connu', html)
        for langue in langues.LANGUES:
            traduit = ecrans5632.translate_html(html, langue)
            self.assertEqual(langues.reste_francais(langues.visibles(traduit)), [], langue)
        self.assertEqual(ecrans5632.translate_html(html, 'fr'), html)             # français inchangé


if __name__ == '__main__':
    unittest.main()
