"""5.6.89, lot qualification : banc d'essai, état par fonction, conservation des 85 fonctions, corbeille Drive, réversibilité.

Données fictives, faux services. Aucun modèle réel, aucun service réel : ces tests contrôlent le logiciel, pas la qualité juridique d'un produit ni un compte réel.
Les durées et montants saisis ici sont inventés pour exercer les calculs : ce ne sont pas des mesures."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

from agent import assertions5689 as A, banc5689 as banc, banc_corpus5689 as corpus, budget_ledger5689 as bl, connector_readiness5689 as cr, data_policy5689 as dp, export5688 as ex, fonctions5631 as registre
from agent import fonctions_etat5689 as etat, fonctions_reference5688 as reference, livrables5689 as liv, membres5650, memoire5688, qualif_web5689 as web, reversibilite5689 as rv, sauvegarde5688 as sv, standalone_auth
from agent import stockage_drive5688 as gdrive, vueia5688
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.office440 import dav_client
import test_v5688_cloud as cloud
import test_v5689_cabinet as cabinet

ADMIN, AVOCAT, ASSISTANT = cabinet.ADMIN, cabinet.AVOCAT, cabinet.ASSISTANT
RELECTEUR = 'relectrice@example.test'
ROOT = Path(__file__).resolve().parents[1]
ANNEXE = ROOT.parent / 'spec5689'
PHRASE = 'valbrune ardoise kervilac orlandec zorval'


class Base(cabinet.Base):
    def cle(self):
        return uuid.uuid4().hex

    def api(self, route, data=None, method='POST', role='administrateur', owner=ADMIN, args=None):
        corps = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': method, 'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_LENGTH': str(len(corps)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': BytesIO(corps)}
        with patch('agent.web567.actor', return_value=(owner, role)):
            sortie = web.api(env, self.desk, {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}, '/p', 'm5689/' + route, args or {}, method)
        return sortie['status'], json.loads(sortie['body'])

    def ok(self, route, data=None, **kw):
        statut, corps = self.api(route, data, **kw)
        self.assertTrue(statut.startswith('200'), (statut, corps))
        return corps

    def refus(self, code, fonction, *args, **kw):
        with self.assertRaises(Stop) as ctx:
            fonction(*args, **kw)
        self.assertEqual(str(ctx.exception).split(':')[0], code)


def trace_propre(cas):
    return {'sources_fournies': [s['id'] for s in cas['contenu']['sources']], 'sources_utilisees': [s['id'] for s in cas['contenu']['sources']], 'actions': [{'type': 'lire_sources', 'destinataire': ''}], 'references': [], 'echeances': []}


class AvecBanc(Base):
    def exercice(self):
        return banc.charger_exercice(self.desk, ADMIN, self.cle())

    def axiorhub(self, **plus):
        return banc.enregistrer_configuration(self.desk, {'cle': self.cle(), 'canal': 'axiorhub', 'memes_sources': True, 'intervention_humaine': 'Relecture finale', **plus}, ADMIN)

    def desktop(self, **plus):
        return banc.enregistrer_configuration(self.desk, {'cle': self.cle(), 'canal': 'claude_desktop', 'produit': 'Claude', 'offre': 'Offre d’essai fictive', 'version': '0.0-fictive', 'modele': 'modele-fictif', 'competences': 'Legal',
                                                          'sources': 'Sources du protocole', 'memes_sources': True, 'intervention_humaine': 'Une personne pilote l’application', **plus}, ADMIN)

    def ouvrir(self, protocole, phase='reglage'):
        run = banc.ouvrir(self.desk, {'cle': self.cle(), 'protocole': protocole['id'], 'phase': phase, 'candidat': self.axiorhub()['id'], 'reference': self.desktop()['id']}, ADMIN)
        return banc.demarrer(self.desk, run['run_id'], ADMIN, 1)

    def cas_du(self, run_id):
        """Cas de l'essai, lus directement : un test n'a pas à passer par la vue qui masque les cas réservés."""
        r = banc._run(self.desk, run_id)
        return [{'ref': c['ref'], 'famille': c['famille'], 'contenu': json.loads(c['contenu_json'])} for c in banc._cas_du_run(self.desk, r)]

    def saisir(self, run_id, cas, cfg, statut='exploitable', temps=(600, 60, 300), trace=None, **plus):
        plan = {x['cas']: x for x in banc.plan(self.desk, run_id)}[cas['ref']]
        return banc.saisir_essai(self.desk, run_id, {'cle': self.cle(), 'cas': cas['ref'], 'configuration': cfg, 'ordre': 1 if plan['premier'] == cfg else 2, 'statut': statut, 'preparation_s': temps[0], 'execution_s': temps[1], 'correction_s': temps[2],
                                                     'omissions': 0, 'incidents': 0, 'destinataires_exacts': 'oui', 'fidelite': 'fidele', 'livrable_ref': 'livrable-%s' % cas['ref'], 'livrable_version': '1', 'livrable_sha256': 'a' * 64,
                                                     'trace': trace or trace_propre(cas), **plus}, ADMIN)

    def jouer(self, run_id, candidat=(600, 60, 300), reference=(900, 60, 600), avis=True, traces=None):
        """Tous les cas de l'essai, pour les deux produits, relus par une autre personne."""
        run = banc._run(self.desk, run_id)
        for cas in self.cas_du(run_id):
            a = self.saisir(run_id, cas, run['candidat'], temps=candidat, trace=(traces or {}).get(cas['ref']))
            b = self.saisir(run_id, cas, run['reference_cfg'], temps=reference)
            if avis:
                for e in (a, b):
                    banc.donner_avis(self.desk, run_id, {'cle': self.cle(), 'essai': e['id'], 'verdict': 'exploitable', 'aveugle': True}, RELECTEUR)
        return run

    def protocole_complet(self, effectif=3, **plus):
        """Cinquante situations bien formées, tirées des douze cas d'exercice : la forme du corpus cible, sans aucune mesure."""
        p = banc.creer_protocole(self.desk, {'cle': self.cle(), 'nom': 'Protocole fictif du cabinet', 'effectif_minimal': effectif, 'parcours': ['consultation', 'contrat', 'acte_contentieux'], **plus}, ADMIN)
        modeles = {(c['famille'], c['partage']): c for c in corpus.CAS}
        n = 0
        for famille, cibles in corpus.REPARTITION_CIBLE.items():
            for partage, total in zip(('reglage', 'evaluation'), cibles):
                for _ in range(total):
                    n += 1
                    modele = dict(modeles[(famille, partage)])
                    modele.pop('echeance_donnees_manquantes', None)
                    p = banc.ajouter_cas(self.desk, p['id'], {**modele, 'ref': 'S%02d' % n, 'origine': 'fictif'}, ADMIN, p['revision'])
        return banc.figer(self.desk, p['id'], ADMIN, p['revision'])


# ------------------------------------------------------------------------------------------------ corpus
class Corpus(AvecBanc):
    def test_corpus_d_exercice_bien_forme_et_fictif(self):
        for cas in corpus.CAS:
            valide = banc.valider_cas({**cas, 'origine': 'fictif'})
            self.assertEqual(valide['origine'], 'fictif')
            self.assertFalse(set(cas) & {'score', 'resultat', 'gain', 'temps', 'mesure'}, cas['ref'])
        self.assertEqual(len(corpus.CAS), 12)
        self.assertEqual({(c['famille'], c['partage']) for c in corpus.CAS}, {(f, p) for f in corpus.FAMILLES for p in banc.PHASES})
        self.assertEqual(sum(sum(v) for v in corpus.REPARTITION_CIBLE.values()), 50)
        self.assertEqual(set(corpus.REPARTITION_CIBLE), set(corpus.FAMILLES))
        texte = json.dumps(corpus.CAS, ensure_ascii=False)
        self.assertLessEqual(set(re.findall(r'\b\d{9}\b', texte)), {'000010017', '000010025'})
        self.assertTrue(all(d.endswith('example.test') for c in corpus.CAS for d in c['destinataires']))

    def test_cas_mal_forme_refuse(self):
        bon = {**corpus.CAS[0]}
        for champ in ('question', 'sources', 'livrables', 'faits_attendus', 'criteres', 'actions_permises'):
            self.refus('cas_invalide', banc.valider_cas, {**bon, champ: ()})
        self.refus('cas_invalide', banc.valider_cas, {**bon, 'famille': 'inconnue'})
        self.refus('cas_identifiant', banc.valider_cas, {**bon, 'question': 'Écrire à quelquun@example.test'})
        self.refus('cas_autorise_sans_droits', banc.valider_cas, {**bon, 'origine': 'autorise'})

    def test_couverture_dit_ce_qui_manque_pour_cinquante_situations(self):
        p = self.exercice()
        self.assertEqual((p['couverture']['total'], p['couverture']['cible'], p['couverture']['complet']), (12, 50, False))
        self.assertTrue(p['exercice'])


# ------------------------------------------------------------------------------------------------ protocole figé
class Protocole(AvecBanc):
    def test_protocole_fige_ne_change_plus_et_empreintes_conservees(self):
        p = self.exercice()
        self.assertEqual(p['etat'], 'fige')
        self.assertRegex(p['corpus_sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(p['protocole_sha256'], r'^[0-9a-f]{64}$')
        self.refus('protocole_fige', banc.ajouter_cas, self.desk, p['id'], dict(corpus.CAS[0], ref='C99'), ADMIN, p['revision'])
        self.refus('protocole_fige', banc.figer, self.desk, p['id'], ADMIN, p['revision'])
        self.assertEqual(set(p['erreurs_critiques']), set(banc.ERREURS_CRITIQUES))

    def test_protocole_cabinet_nouvelle_version_laisse_l_ancienne_intacte(self):
        p = banc.creer_protocole(self.desk, {'cle': self.cle(), 'nom': 'Essai'}, ADMIN)
        p = banc.ajouter_cas(self.desk, p['id'], dict(corpus.CAS[0]), ADMIN, p['revision'])
        self.refus('reglage_et_evaluation_requis', banc.figer, self.desk, p['id'], ADMIN, p['revision'])
        p = banc.ajouter_cas(self.desk, p['id'], dict(corpus.CAS[1]), ADMIN, p['revision'])
        self.refus('revision_perimee', banc.ajouter_cas, self.desk, p['id'], dict(corpus.CAS[2]), ADMIN, p['revision'] - 1)
        fige = banc.figer(self.desk, p['id'], ADMIN, p['revision'])
        suite = banc.nouvelle_version(self.desk, fige['id'], ADMIN, self.cle())
        self.assertEqual((suite['version'], suite['etat'], suite['parent']), (2, 'brouillon', fige['id']))
        suite = banc.ajouter_cas(self.desk, suite['id'], dict(corpus.CAS[2]), ADMIN, suite['revision'])
        self.assertEqual(len(suite['cas']), 3)
        self.assertEqual(banc.lire_protocole(self.desk, fige['id'])['corpus_sha256'], fige['corpus_sha256'])

    def test_corpus_altere_directement_en_base_est_vu_et_retire_toute_conclusion(self):
        p = self.exercice()
        run = self.ouvrir(p)
        c = self.desk.db.execute("SELECT id,contenu_json FROM banc_cas5689 WHERE protocole=? AND partage='reglage' LIMIT 1", (p['id'],)).fetchone()
        contenu = json.loads(c['contenu_json'])
        contenu['faits_attendus'].append('Fait ajouté après lecture des résultats')
        self.desk.db.execute('UPDATE banc_cas5689 SET contenu_json=? WHERE id=?', (json.dumps(contenu, ensure_ascii=False), c['id']))
        self.desk.db.commit()
        self.assertFalse(banc.lire_protocole(self.desk, p['id'])['corpus_intact'])
        self.assertIn('corpus_altere', banc.comparaison(self.desk, run['run_id'])['conclusion']['motifs'])
        self.refus('corpus_altere', banc.ouvrir, self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': self.axiorhub()['id'], 'reference': self.desktop()['id']}, ADMIN)

    def test_reglage_et_evaluation_separes(self):
        p = self.exercice()
        reserves = [c for c in p['cas'] if c['partage'] == 'evaluation']
        self.assertTrue(reserves)
        self.assertTrue(all(c.get('reserve') and 'contenu' not in c for c in reserves))
        reglage = self.ouvrir(p)
        self.assertTrue(reglage['run_id'].startswith('reg-'))
        cfg = banc._run(self.desk, reglage['run_id'])['candidat']
        self.refus('cas_hors_de_cette_phase', banc.saisir_essai, self.desk, reglage['run_id'], {'cle': self.cle(), 'cas': reserves[0]['ref'], 'configuration': cfg, 'ordre': 1, 'statut': 'partiel'}, ADMIN)
        self.assertTrue(all('contenu' not in c for c in banc.lire_protocole(self.desk, p['id'])['cas'] if c['partage'] == 'evaluation'), 'un essai de réglage ne découvre pas les cas réservés')
        evaluation = self.ouvrir(p, 'evaluation')
        self.assertTrue(evaluation['run_id'].startswith('eva-'))
        self.assertTrue(all('contenu' in c for c in banc.lire_protocole(self.desk, p['id'])['cas']))


# ------------------------------------------------------------------------------------------------ IMP08A : équité
class Equite(AvecBanc):
    def test_IMP08A_essai_api_jamais_presente_comme_desktop(self):
        for produit, offre in (('Claude Desktop', 'API'), ('Claude', 'Cowork via API')):
            self.refus('essai_api_presente_comme_desktop', banc.enregistrer_configuration, self.desk, {'cle': self.cle(), 'canal': 'api', 'produit': produit, 'offre': offre, 'version': '1', 'modele': 'm', 'intervention_humaine': 'x'}, ADMIN)
        api = banc.enregistrer_configuration(self.desk, {'cle': self.cle(), 'canal': 'api', 'produit': 'Claude', 'offre': 'Accès développeur fictif', 'version': '1', 'modele': 'm', 'intervention_humaine': 'Script', 'memes_sources': True}, ADMIN)
        self.assertIn('ce n’est pas un essai de Claude Desktop', api['intitule'])
        with patch.object(banc, 'CANAUX', {**banc.CANAUX}):
            self.assertNotEqual(api['intitule'], banc.CANAUX['claude_desktop'])
        html = web._section_configurations({'configurations': [api]}, False)
        self.assertIn('ce n’est pas un essai de Claude Desktop', html)
        self.assertNotIn('<td>Claude Desktop</td>', html)

    def test_IMP08A_reference_consignee_offre_version_capacites_intervention(self):
        for manque in ('offre', 'version', 'modele', 'intervention_humaine', 'produit'):
            self.refus('configuration_incomplete', self.desktop, **{manque: ''})
        self.refus('capacites_a_consigner', self.desktop, competences='', sources='')
        self.assertTrue(self.desktop(competences='', sources='', aucune_capacite=True))
        r = self.desktop()
        self.assertEqual((r['role'], r['canal'], r['intitule'], r['saisie_manuelle']), ('reference', 'claude_desktop', 'Claude Desktop', True))
        self.assertEqual(r['capacites']['competences'], ['Legal'])

    def test_IMP08A_axiorhub_releve_sa_configuration_sans_saisie(self):
        from agent import __version__
        c = self.axiorhub(version='9.9.9', modele='modele-annonce')
        self.assertEqual((c['version'], c['role'], c['saisie_manuelle']), (__version__, 'candidat', False))
        self.assertNotEqual(c['modele'], 'modele-annonce')

    def test_IMP08A_memes_sources_exigees_et_paires_inegales_non_comparees(self):
        p = self.exercice()
        self.refus('memes_sources_requises', banc.ouvrir, self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': self.axiorhub(memes_sources=False)['id'], 'reference': self.desktop()['id']}, ADMIN)
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        self.saisir(run['run_id'], cas, r['candidat'])
        self.saisir(run['run_id'], cas, r['reference_cfg'], trace={**trace_propre(cas), 'sources_fournies': trace_propre(cas)['sources_fournies'][:1]})
        ligne = next(x for x in banc.comparaison(self.desk, run['run_id'])['cas'] if x['cas'] == cas['ref'])
        self.assertEqual((ligne['mesurable'], ligne['motif'], ligne['ecart_s']), (False, 'sources_differentes', None))

    def test_IMP08A_sources_non_relevees_ne_font_pas_une_paire_comparable(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        vide = {**trace_propre(cas), 'sources_fournies': []}
        self.saisir(run['run_id'], cas, r['candidat'], trace=vide)
        self.saisir(run['run_id'], cas, r['reference_cfg'], trace=vide)
        self.assertEqual(next(x for x in banc.comparaison(self.desk, run['run_id'])['cas'] if x['cas'] == cas['ref'])['motif'], 'sources_differentes')

    def test_IMP08A_ordre_alterne_et_ecart_consigne(self):
        p = self.exercice()
        run = self.ouvrir(p)
        plan = banc.plan(self.desk, run['run_id'])
        self.assertEqual(len({x['premier'] for x in plan}), 2)
        self.assertTrue(all(a['premier'] != b['premier'] for a, b in zip(plan, plan[1:])))
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        mauvais = 2 if plan[0]['premier'] == r['candidat'] else 1
        e = banc.saisir_essai(self.desk, run['run_id'], {'cle': self.cle(), 'cas': cas['ref'], 'configuration': r['candidat'], 'ordre': mauvais, 'statut': 'partiel', 'trace': trace_propre(cas)}, ADMIN)
        self.assertEqual(next(c['resultat'] for c in e['controles'] if c['nature'] == 'ordre_alterne'), 'ecart')

    def test_IMP08A_preparation_initiale_mesuree_a_part_de_l_usage(self):
        p = self.exercice()
        cand = self.axiorhub(preparation_s=86400)
        ref = self.desktop(preparation_s=7200)
        run = banc.ouvrir(self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': cand['id'], 'reference': ref['id']}, ADMIN)
        banc.demarrer(self.desk, run['run_id'], ADMIN, 1)
        cas = self.cas_du(run['run_id'])[0]
        self.saisir(run['run_id'], cas, cand['id'], temps=(10, 10, 10))
        self.saisir(run['run_id'], cas, ref['id'], temps=(20, 20, 20))
        ligne = next(x for x in banc.comparaison(self.desk, run['run_id'])['cas'] if x['cas'] == cas['ref'])
        self.assertEqual((ligne['candidat']['temps_total_s'], ligne['reference']['temps_total_s']), (30, 60))
        self.assertEqual(banc.detail(self.desk, run['run_id'])['candidat']['preparation_s'], 86400)

    def test_IMP08A_dossier_autorise_seulement_vers_ses_destinations(self):
        p = banc.creer_protocole(self.desk, {'cle': self.cle(), 'nom': 'Autorisé'}, ADMIN)
        for c in corpus.CAS[:2]:
            p = banc.ajouter_cas(self.desk, p['id'], {**c, 'origine': 'autorise', 'droits': 'Accord écrit fictif du client', 'destinations': ['axiorhub']}, ADMIN, p['revision'])
        p = banc.figer(self.desk, p['id'], ADMIN, p['revision'])
        self.refus('cas_non_autorise_pour_cette_destination', banc.ouvrir, self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': self.axiorhub()['id'], 'reference': self.desktop()['id']}, ADMIN)


# ------------------------------------------------------------------------------------------------ IMP08B : résultat reproductible
class Reproductible(AvecBanc):
    def test_IMP08B_contrat_benchmarkrun_et_metriques_rattachees(self):
        p = self.exercice()
        run = self.ouvrir(p)
        schema = {'run_id', 'protocol_version', 'dataset_sha256', 'reference_configuration_ref', 'candidate_configuration_ref', 'number_of_cases', 'state', 'metric_artifact_refs'}
        self.assertEqual(set(run), schema)
        self.assertEqual(run['state'], 'running')
        self.assertRegex(run['dataset_sha256'], r'^[0-9a-f]{64}$')
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        e = self.saisir(run['run_id'], cas, r['candidat'])
        contrat = banc.contrat(self.desk, run['run_id'])
        self.assertEqual(contrat['metric_artifact_refs'], ['banc:%s/resultats/%s' % (run['run_id'], e['id'])])
        ligne = banc.comparaison(self.desk, run['run_id'])['cas'][0]
        self.assertEqual(ligne['references_metriques'], contrat['metric_artifact_refs'])
        self.assertEqual((ligne['candidat']['livrable_ref'], ligne['candidat']['livrable_sha256']), ('livrable-' + cas['ref'], 'a' * 64))
        if ANNEXE.is_dir():
            officiel = json.loads((ANNEXE / 'Implementation-8-objectifs/BenchmarkRun.schema.json').read_text(encoding='utf-8'))
            self.assertEqual(set(officiel['required']), schema)
            self.assertIn(contrat['state'], officiel['properties']['state']['enum'])

    def test_IMP08B_mediane_dispersion_effectif_et_cas_rejetes_visibles(self):
        p = self.protocole_complet()
        run = self.ouvrir(p, 'evaluation')
        r = banc._run(self.desk, run['run_id'])
        cas = [c for c in self.cas_du(run['run_id']) if c['famille'] == 'consultation']
        self.assertEqual(len(cas), 5)
        for c, (cand, ref) in zip(cas, (((60, 0, 0), (100, 0, 0)), ((50, 0, 0), (100, 0, 0)), ((70, 0, 0), (100, 0, 0)), ((40, 0, 0), (100, 0, 0)), ((10, 0, 0), (100, 0, 0)))):
            statut = 'rejete' if cand[0] == 10 else 'exploitable'
            self.saisir(run['run_id'], c, r['candidat'], temps=cand, statut=statut)
            self.saisir(run['run_id'], c, r['reference_cfg'], temps=ref)
        f = next(x for x in banc.comparaison(self.desk, run['run_id'])['familles'] if x['famille'] == 'consultation')
        self.assertEqual(f['reduction_pct'], {'effectif': 4, 'mediane': 45.0, 'q1': 37.5, 'q3': 52.5, 'minimum': 30.0, 'maximum': 60.0})
        self.assertEqual(f['candidat']['rejete'], 1)
        self.assertEqual(f['non_comparables'], 1, 'le résultat rejeté reste compté, il ne devient pas un gain')
        self.assertEqual(f['degradations'], 1)

    def test_IMP08B_mesure_absente_reste_absente_jamais_zero(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        e = banc.saisir_essai(self.desk, run['run_id'], {'cle': self.cle(), 'cas': cas['ref'], 'configuration': r['candidat'], 'ordre': 1, 'statut': 'partiel', 'trace': trace_propre(cas), 'preparation_s': '', 'execution_s': 10, 'correction_s': 5}, ADMIN)
        self.assertIsNone(e['preparation_s'])
        self.saisir(run['run_id'], cas, r['reference_cfg'])
        ligne = next(x for x in banc.comparaison(self.desk, run['run_id'])['cas'] if x['cas'] == cas['ref'])
        self.assertEqual((ligne['candidat']['temps_total_s'], ligne['ecart_s'], ligne['motif']), (None, None, 'mesure_incomplete'))
        self.assertEqual(banc.resume([]), {'effectif': 0, 'mediane': None, 'q1': None, 'q3': None, 'minimum': None, 'maximum': None})

    def test_IMP08B_aucune_conclusion_sans_donnees(self):
        p = self.exercice()
        run = self.ouvrir(p)
        c = banc.comparaison(self.desk, run['run_id'])
        self.assertFalse(c['conclusion']['possible'])
        self.assertEqual(set(c['conclusion']['motifs']) & {'phase_reglage', 'corpus_d_exercice', 'corpus_incomplet', 'essai_non_termine', 'aucune_paire_mesurable', 'relecture_incomplete', 'cible_non_figee'},
                         {'phase_reglage', 'corpus_d_exercice', 'corpus_incomplet', 'essai_non_termine', 'aucune_paire_mesurable', 'relecture_incomplete', 'cible_non_figee'})
        self.assertEqual(c['conclusion']['parcours'], [])
        self.assertNotIn('limite', c['conclusion'])

    def test_IMP08B_saisie_rejouee_sans_doublon_et_resultat_non_reecrit(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        donnees = {'cle': self.cle(), 'cas': cas['ref'], 'configuration': r['candidat'], 'ordre': 1, 'statut': 'partiel', 'execution_s': 10, 'trace': trace_propre(cas)}
        a = banc.saisir_essai(self.desk, run['run_id'], donnees, ADMIN)
        b = banc.saisir_essai(self.desk, run['run_id'], donnees, ADMIN)
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM banc_essais5689').fetchone()[0], 1)
        self.refus('cle_idempotence_deja_utilisee', banc.saisir_essai, self.desk, run['run_id'], {**donnees, 'statut': 'exploitable'}, ADMIN)
        self.refus('essai_deja_saisi', banc.saisir_essai, self.desk, run['run_id'], {**donnees, 'cle': self.cle(), 'statut': 'exploitable'}, ADMIN)

    def test_IMP08B_journal_horodate_et_etats(self):
        p = self.exercice()
        cible = banc.ouvrir(self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': self.axiorhub()['id'], 'reference': self.desktop()['id'], 'droits': 'Corpus fictif, sans limite'}, ADMIN)
        self.assertEqual(cible['state'], 'planned')
        self.refus('revision_perimee', banc.demarrer, self.desk, cible['run_id'], ADMIN, 9)
        banc.demarrer(self.desk, cible['run_id'], ADMIN, 1)
        self.refus('etat_essai_invalide', banc.demarrer, self.desk, cible['run_id'], ADMIN, 2)
        self.refus('motif_requis', banc.echouer, self.desk, cible['run_id'], ADMIN, 2, '')
        d = banc.echouer(self.desk, cible['run_id'], ADMIN, 2, 'Interruption fictive')
        self.assertEqual(d['state'], 'failed')
        detail = banc.detail(self.desk, cible['run_id'])
        self.assertEqual([x['genre'] for x in detail['evenements']], ['prevu', 'demarre', 'echoue'])
        self.assertEqual((detail['droits'], detail['motif_echec']), ('Corpus fictif, sans limite', 'Interruption fictive'))
        cas = self.cas_du(cible['run_id'])[0]
        self.refus('etat_essai_invalide', self.saisir, cible['run_id'], cas, banc._run(self.desk, cible['run_id'])['candidat'])


# ------------------------------------------------------------------------------------------------ IMP08C : erreurs critiques
class Critiques(AvecBanc):
    def essai_avec(self, trace):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = next(c for c in self.cas_du(run['run_id']) if c['ref'] == 'C03')
        return self.saisir(run['run_id'], cas, r['candidat'], trace={**trace_propre(cas), **trace}), cas

    def test_IMP08C_chaque_erreur_critique_detectee_sur_la_trace(self):
        cas_type = next(banc.valider_cas({**c, 'origine': 'fictif'}) for c in corpus.CAS if c['ref'] == 'C03')['contenu']
        delai = next(banc.valider_cas({**c, 'origine': 'fictif'}) for c in corpus.CAS if c['ref'] == 'C08')['contenu']
        for attendu, contenu, trace in (('divulgation_hors_droits', cas_type, {'sources_utilisees': ['dossier-reel-non-autorise']}),
                                        ('divulgation_hors_droits', cas_type, {'sources_fournies': ['dossier-reel-non-autorise']}),
                                        ('action_externe_non_autorisee', cas_type, {'actions': [{'type': 'envoyer', 'destinataire': ''}]}),
                                        ('mauvais_destinataire', cas_type, {'actions': [{'type': 'preparer_brouillon', 'destinataire': 'autre@zorval.example.test'}]}),
                                        ('reference_fabriquee_verifiee', cas_type, {'references': [{'ref': 'Décision inventée', 'statut': 'verifiee'}]}),
                                        ('echeance_certaine_sans_donnees', delai, {'echeances': [{'date': '2026-12-01', 'certaine': True}]})):
            base_trace = {'sources_fournies': [], 'sources_utilisees': [], 'actions': [], 'references': [], 'echeances': []}
            _, erreurs = banc.controler_trace(contenu, {**base_trace, **trace})
            self.assertEqual([e[0] for e in erreurs], [attendu], trace)

    def test_IMP08C_conduite_conforme_ne_declenche_rien(self):
        cas_type = banc.valider_cas({**next(c for c in corpus.CAS if c['ref'] == 'C03'), 'origine': 'fictif'})['contenu']
        trace = {'sources_fournies': ['C03-S1'], 'sources_utilisees': ['C03-S2'], 'actions': [{'type': 'preparer_brouillon', 'destinataire': 'CONTACT@orlandec.example.test'}],
                 'references': [{'ref': 'C03-S1', 'statut': 'verifiee'}, {'ref': 'Décision à retrouver', 'statut': 'a_verifier'}], 'echeances': [{'date': '2026-12-01', 'certaine': False}]}
        self.assertEqual(banc.controler_trace(cas_type, trace)[1], [])

    def test_IMP08C_trace_non_renseignee_n_est_pas_conforme(self):
        controles, erreurs = banc.controler_trace(banc.valider_cas({**corpus.CAS[0], 'origine': 'fictif'})['contenu'], banc._trace({}))
        self.assertEqual({c[1] for c in controles}, {'non_renseigne'})
        self.assertEqual(erreurs, [])

    def test_IMP08C_erreur_inscrite_au_journal_du_resultat(self):
        e, cas = self.essai_avec({'references': [{'ref': 'Décision inventée', 'statut': 'verifiee'}]})
        self.assertEqual([x['categorie'] for x in e['erreurs']], ['reference_fabriquee_verifiee'])
        self.assertEqual(next(c['resultat'] for c in e['controles'] if c['nature'] == 'references'), 'erreur')
        rec = banc.reception(self.desk, e['run_id'])
        ligne = next(f for f in rec['familles'] if f['famille'] == cas['famille'])
        self.assertEqual((ligne['etat'], ligne['erreurs_critiques']), ('bloquee', 1))
        self.assertIn('absence universelle', rec['limite'])
        audit = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='banc_erreur_critique5689'").fetchone()[0]
        self.assertEqual(audit, 1)

    def complet(self, traces=None):
        p = self.protocole_complet()
        run = self.ouvrir(p, 'evaluation')
        r = self.jouer(run['run_id'], traces=traces)
        banc.clore(self.desk, run['run_id'], ADMIN, 2)
        return run['run_id'], r

    def test_IMP08C_erreur_critique_empeche_de_recevoir_la_seule_famille_concernee(self):
        ref_contrat = None
        p = self.protocole_complet()
        run = self.ouvrir(p, 'evaluation')
        cas = self.cas_du(run['run_id'])
        ref_contrat = next(c for c in cas if c['famille'] == 'contrat')
        faute = {**trace_propre(ref_contrat), 'actions': [{'type': 'envoyer', 'destinataire': 'contact@orlandec.example.test'}]}
        self.jouer(run['run_id'], traces={ref_contrat['ref']: faute})
        banc.clore(self.desk, run['run_id'], ADMIN, 2)
        etats = {f['famille']: f['etat'] for f in banc.reception(self.desk, run['run_id'])['familles']}
        self.assertEqual(etats['contrat'], 'bloquee')
        self.assertEqual(etats['consultation'], 'recevable')
        self.refus('reception_bloquee_erreur_critique', banc.recevoir, self.desk, run['run_id'], 'contrat', ADMIN, self.cle())
        statut, corps = self.api('benchmarks/%s/recevoir' % run['run_id'], {'famille': 'contrat', 'cle': self.cle()})
        self.assertTrue(statut.startswith('409'), corps)
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM banc_receptions5689 WHERE famille='contrat'").fetchone())
        banc.recevoir(self.desk, run['run_id'], 'consultation', ADMIN, self.cle())
        self.assertEqual(next(f for f in banc.reception(self.desk, run['run_id'])['familles'] if f['famille'] == 'consultation')['recue'], 'recue')
        # mutation : sans le détecteur, la même faute passerait ; c'est donc bien lui qui bloque
        self.desk.db.execute("DELETE FROM banc_erreurs5689")
        self.desk.db.commit()
        self.assertEqual(next(f for f in banc.reception(self.desk, run['run_id'])['familles'] if f['famille'] == 'contrat')['etat'], 'recevable')

    def test_IMP08C_relecteur_signale_une_erreur_que_seule_la_lecture_revele(self):
        run_id, r = self.complet()
        e = self.desk.db.execute("SELECT b.id FROM banc_essais5689 b JOIN banc_cas5689 c ON c.id=b.cas WHERE c.famille='audience' LIMIT 1").fetchone()
        self.refus('categorie_inconnue', banc.signaler_erreur, self.desk, run_id, {'essai': e['id'], 'categorie': 'autre'}, ADMIN)
        out = banc.signaler_erreur(self.desk, run_id, {'essai': e['id'], 'categorie': 'divulgation_hors_droits', 'detail': 'Note interne citée dans le livrable'}, RELECTEUR)
        self.assertEqual(next(f for f in out['familles'] if f['famille'] == 'audience')['etat'], 'bloquee')
        self.refus('reception_bloquee_erreur_critique', banc.recevoir, self.desk, run_id, 'audience', ADMIN, self.cle())

    def test_IMP08C_exercice_ou_reglage_ne_se_recoit_jamais(self):
        p = self.exercice()
        run = self.ouvrir(p, 'evaluation')
        self.jouer(run['run_id'])
        banc.clore(self.desk, run['run_id'], ADMIN, 2)
        familles = banc.reception(self.desk, run['run_id'])['familles']
        self.assertEqual({f['etat'] for f in familles}, {'sans_conclusion'})
        self.assertTrue(all('corpus_d_exercice' in f['motifs'] for f in familles))
        self.refus('reception_sans_donnees_suffisantes', banc.recevoir, self.desk, run['run_id'], 'contrat', ADMIN, self.cle())

    def test_IMP08C_nouvelle_version_appelle_une_nouvelle_qualification(self):
        run_id, _ = self.complet()
        banc.recevoir(self.desk, run_id, 'consultation', ADMIN, self.cle())
        with patch('agent.__version__', '9.9.99'):   # une version ultérieure quelconque, indépendante du passage de version
            self.assertEqual(next(f for f in banc.reception(self.desk, run_id)['familles'] if f['famille'] == 'consultation')['recue'], 'a_requalifier')


# ------------------------------------------------------------------------------------------------ AV14, AV15
class Couts(AvecBanc):
    def setUp(self):
        super().setUp()
        p = self.exercice()
        self.run = self.ouvrir(p)['run_id']
        self.r = banc._run(self.desk, self.run)

    def cout(self, **plus):
        return banc.ajouter_cout(self.desk, self.run, {'cle': self.cle(), 'configuration': self.r['candidat'], 'nature': 'usage', **plus}, ADMIN)

    def bilan(self, cfg=None):
        return banc.bilan_cout(self.desk, self.run)['par_configuration'][cfg or self.r['candidat']]

    def test_AV14_couts_fixes_usage_maintenance_identifies_et_estimations_separees(self):
        self.cout(nature='fixe', origine='facture', montant='100', reference='F-2026-01')
        self.cout(nature='maintenance', origine='facture', montant='40', reference='F-2026-02')
        self.cout(nature='usage', origine='estime', montant='7,5')
        b = self.bilan()
        self.assertEqual(b['par_nature'], {'usage': 1, 'fixe': 1, 'maintenance': 1, 'credit': 0})
        self.assertEqual(b['net']['USD'], {'observe_micro': 140_000_000, 'estime_micro': 7_500_000})

    def test_AV14_appel_lu_au_registre_jamais_nul_tant_qu_il_est_incertain(self):
        bl.reserver_service(self.desk, {'call_id': 'appel-1', 'idempotency_key': 'k-appel-1', 'montant_usd': '0.25', 'mission_id': 'm1'}, ADMIN)
        self.cout(call_id='appel-1')
        self.assertEqual((self.bilan()['inconnus'], self.bilan()['complet'], self.bilan()['net']), (1, False, {}))
        bl.rapprocher(self.desk.c['state_dir'], 'appel-1', '0.20')
        self.assertEqual((self.bilan()['inconnus'], self.bilan()['net']['USD']['observe_micro']), (0, 200_000))
        self.refus('cout_deja_compte', self.cout, call_id='appel-1')
        self.refus('reservation_absente', self.cout, call_id='appel-inconnu')

    def test_AV14_credit_effectif_applique_une_seule_fois(self):
        self.cout(origine='facture', montant='10', reference='F-1')
        self.cout(nature='credit', origine='facture', montant='3', reference='AVOIR-1')
        self.refus('cout_deja_compte', self.cout, nature='credit', origine='facture', montant='3', reference='AVOIR-1')
        self.refus('credit_sans_justificatif', self.cout, nature='credit', origine='estime', montant='3', reference='AVOIR-2')
        self.refus('credit_sans_justificatif', self.cout, nature='credit', origine='facture', montant='3')
        self.assertEqual(self.bilan()['net']['USD']['observe_micro'], 7_000_000)

    def test_AV14_correction_observee_distincte_des_estimations(self):
        cas = self.cas_du(self.run)
        self.saisir(self.run, cas[0], self.r['candidat'], temps=(1, 1, 300))
        self.saisir(self.run, cas[1], self.r['candidat'], temps=(1, 1, 200))
        banc.saisir_essai(self.desk, self.run, {'cle': self.cle(), 'cas': cas[2]['ref'], 'configuration': self.r['candidat'], 'ordre': 1, 'statut': 'partiel', 'trace': trace_propre(cas[2])}, ADMIN)
        b = self.bilan()
        self.assertEqual((b['correction_observee_s'], b['corrections_non_relevees']), (500, 1))
        self.assertNotIn('correction_estimee_s', b)

    def test_AV14_montant_inconnu_reste_inconnu(self):
        self.cout(origine='inconnu', nature='maintenance')
        self.assertEqual((self.bilan()['inconnus'], self.bilan()['complet']), (1, False))
        self.refus('montant_invalide', self.cout, origine='facture', montant='-3')
        self.refus('montant_invalide', self.cout, origine='facture', montant='beaucoup')


class Preenregistree(AvecBanc):
    def test_AV15_comparaison_preenregistree_resultats_et_limites_par_parcours(self):
        p = self.protocole_complet()
        run = self.ouvrir(p, 'evaluation')
        self.jouer(run['run_id'], candidat=(60, 10, 30), reference=(100, 50, 50))
        banc.clore(self.desk, run['run_id'], ADMIN, 2)
        c = banc.comparaison(self.desk, run['run_id'])
        self.assertTrue(c['conclusion']['possible'], c['conclusion'])
        self.assertEqual({x['famille'] for x in c['conclusion']['parcours']}, {'consultation', 'contrat', 'acte_contentieux'})
        self.assertTrue(all(x['cible_pct'] == 30 for x in c['conclusion']['parcours']))
        self.assertEqual(c['conclusion']['cible']['nature'], banc.CIBLE_DEFAUT['nature'])
        self.assertTrue(all(f['reduction_pct']['effectif'] == 5 for f in c['familles']))
        self.assertIn('ni garantie générale', c['conclusion']['limite'])
        d = banc.detail(self.desk, run['run_id'])
        self.assertEqual(d['protocole']['corpus_sha256'], run['dataset_sha256'])
        self.assertEqual((d['reference']['canal'], d['reference']['offre']), ('claude_desktop', 'Offre d’essai fictive'))

    def test_AV15_cible_figee_avant_essai_et_non_modifiable(self):
        p = self.protocole_complet()
        cible = banc.lire_protocole(self.desk, p['id'])['cible']
        self.assertEqual(cible['reduction_mediane_pct'], 30)
        self.refus('protocole_fige', banc.ajouter_cas, self.desk, p['id'], dict(corpus.CAS[0], ref='Z1'), ADMIN, p['revision'])
        sans_parcours = banc.creer_protocole(self.desk, {'cle': self.cle(), 'nom': 'Sans parcours'}, ADMIN)
        self.assertEqual(sans_parcours['cible']['parcours'], [])

    def test_AV15_relecture_sans_nom_du_produit_et_aveugle_non_obtenu_consigne(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        a = self.saisir(run['run_id'], cas, r['candidat'])
        b = self.saisir(run['run_id'], cas, r['reference_cfg'])
        sorties = banc.relecture(self.desk, run['run_id'])
        self.assertEqual({s['sortie'] for s in sorties}, {'A', 'B'})
        texte = json.dumps(sorties, ensure_ascii=False)
        for interdit in ('AxiorHub', 'Claude', r['candidat'], r['reference_cfg'], 'configuration', 'cout'):
            self.assertNotIn(interdit, texte)
        banc.donner_avis(self.desk, run['run_id'], {'cle': self.cle(), 'essai': a['id'], 'verdict': 'partiel', 'aveugle': True}, ADMIN)   # ADMIN a saisi : pas d'aveugle, même s'il coche
        banc.donner_avis(self.desk, run['run_id'], {'cle': self.cle(), 'essai': b['id'], 'verdict': 'partiel', 'aveugle': True}, RELECTEUR)
        avis = {x['essai']: x for x in banc.detail(self.desk, run['run_id'])['avis']}
        self.assertEqual((avis[a['id']]['aveugle'], avis[a['id']]['motif_non_aveugle']), (0, 'Mise à l’aveugle non obtenue'))
        self.assertEqual(avis[b['id']]['aveugle'], 1)

    def test_AV15_le_relecteur_prime_et_l_avis_le_plus_severe_est_retenu(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        e = self.saisir(run['run_id'], cas, r['candidat'], statut='exploitable')
        banc.donner_avis(self.desk, run['run_id'], {'cle': self.cle(), 'essai': e['id'], 'verdict': 'partiel'}, RELECTEUR)
        banc.donner_avis(self.desk, run['run_id'], {'cle': self.cle(), 'essai': e['id'], 'verdict': 'rejete'}, AVOCAT)
        self.refus('avis_deja_donne', banc.donner_avis, self.desk, run['run_id'], {'cle': self.cle(), 'essai': e['id'], 'verdict': 'exploitable'}, AVOCAT)
        self.assertEqual(banc.essai(self.desk, e['id'])['statut_retenu'], 'rejete')


# ------------------------------------------------------------------------------------------------ routes et page
class Routes(AvecBanc):
    def test_droits_des_routes(self):
        self.assertTrue(web.allowed('administrateur', 'benchmarks'))
        self.assertTrue(web.allowed('avocat', 'benchmarks'))
        self.assertFalse(web.allowed('assistant', 'benchmarks'))
        self.assertTrue(web.allowed('assistant', 'fonctions'))
        self.assertFalse(web.allowed('avocat', 'benchmarks/inconnue'))
        self.assertFalse(web.allowed('invite', 'fonctions'))
        p = self.exercice()
        self.assertTrue(self.api('benchmarks', {'cle': self.cle(), 'protocole': p['id']}, role='avocat', owner=AVOCAT)[0].startswith('403'))
        self.assertTrue(self.api('benchmarks', role='assistant', owner=ASSISTANT, method='GET')[0].startswith('403'))
        self.assertTrue(self.api('benchmarks/exercice', {'cle': self.cle()}, role='assistant', owner=ASSISTANT)[0].startswith('403'))
        self.assertTrue(self.api('fonctions/relever', role='avocat', owner=AVOCAT)[0].startswith('403'))
        self.assertTrue(self.api('benchmarks/%s' % ('z' * 32), method='GET')[0].startswith('403'))

    def test_droits_declares_au_serveur_et_droits_fins(self):
        self.assertEqual(membres5650.category('POST', '/api440/m5689/benchmarks'), 'reglages')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/benchmarks/reg-%s/avis' % ('a' * 32)), 'valider')
        self.assertIsNone(membres5650.category('GET', '/api440/m5689/benchmarks'))
        self.assertTrue(standalone_auth.allowed('avocat', 'GET', '/api440/m5689/benchmarks'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/benchmarks'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m5689/inconnu'))
        self.assertTrue(standalone_auth.allowed('assistant', 'GET', '/banc-essai'))

    def test_avis_ouvert_a_l_avocat_acteur_impose_par_la_session(self):
        p = self.exercice()
        run = self.ouvrir(p)
        r = banc._run(self.desk, run['run_id'])
        cas = self.cas_du(run['run_id'])[0]
        e = self.saisir(run['run_id'], cas, r['candidat'])
        self.ok('benchmarks/%s/avis' % run['run_id'], {'cle': self.cle(), 'essai': e['id'], 'verdict': 'partiel', 'relecteur': 'usurpe@example.test', 'aveugle': True}, role='avocat', owner=AVOCAT)
        self.assertEqual(self.desk.db.execute('SELECT relecteur FROM banc_avis5689').fetchone()[0], AVOCAT)

    def test_csrf_exige_sur_chaque_ecriture(self):
        corps = b'{}'
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'https://intrus.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_LENGTH': '2', 'CONTENT_TYPE': 'application/json', 'wsgi.input': BytesIO(corps)}
        with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')):
            sortie = web.api(env, self.desk, {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}, '/p', 'm5689/benchmarks/exercice', {}, 'POST')
        self.assertEqual(json.loads(sortie['body'])['error'], 'origine_ou_csrf_refuse')
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='banc_protocoles5689'").fetchone() and self.desk.db.execute('SELECT 1 FROM banc_protocoles5689').fetchone())

    def test_parcours_complet_par_les_routes_avec_409_si_perime(self):
        p = self.ok('benchmarks/exercice', {'cle': self.cle()})
        cand = self.ok('benchmarks/configurations', {'cle': self.cle(), 'canal': 'axiorhub', 'memes_sources': 'on'})
        ref = self.ok('benchmarks/configurations', {'cle': self.cle(), 'canal': 'claude_cowork', 'produit': 'Claude', 'offre': 'Offre fictive', 'version': '1', 'modele': 'm', 'competences': 'Legal', 'memes_sources': 'on', 'intervention_humaine': 'Pilotage'})
        run = self.ok('benchmarks', {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': cand['id'], 'reference': ref['id']})
        ident = run['run_id']
        statut, corps = self.api('benchmarks/%s/demarrer' % ident, {'revision': 7})
        self.assertTrue(statut.startswith('409'), corps)
        self.ok('benchmarks/%s/demarrer' % ident, {'revision': 1})
        lu = self.ok('benchmarks/' + ident, method='GET')
        self.assertEqual(lu['run']['run_id'], ident)
        donnees = {'cle': self.cle(), 'cas': 'C01', 'configuration': cand['id'], 'ordre': 1, 'statut': 'partiel', 'execution_s': 12, 'sources_fournies': 'C01-S1\nC01-S2', 'sources_utilisees': 'C01-S1', 'actions': 'preparer_brouillon ; quelquun@example.test',
                   'references': 'Inventée ; verifiee', 'echeances': '2026-12-01 ; certaine'}
        e = self.ok('benchmarks/%s/resultats' % ident, donnees)
        self.assertEqual(sorted(x['categorie'] for x in e['erreurs']), ['action_externe_non_autorisee', 'mauvais_destinataire', 'reference_fabriquee_verifiee'])
        self.assertEqual(self.ok('benchmarks/%s/plan' % ident, method='GET')['plan'][0]['cas'], 'C01')
        self.assertEqual(self.ok('benchmarks/%s/relecture' % ident, method='GET')['sorties'][0]['cas'], 'C01')
        self.assertTrue(self.api('benchmarks/%s/recevoir' % ident, {'famille': 'consultation', 'cle': self.cle()})[0].startswith('409'))

    def test_trace_non_relevee_depuis_le_formulaire(self):
        self.assertEqual(web.trace_formulaire({'trace_non_renseignee': 'on'}), {})
        self.assertEqual(banc._trace(web.trace_formulaire({'trace_non_renseignee': 'on'}))['actions'], None)
        self.assertEqual(banc._trace(web.trace_formulaire({}))['actions'], [])

    def test_page_dit_que_rien_n_est_fait_et_n_affiche_aucun_resultat(self):
        with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')):
            html = web.page(self.desk, {'csrf': 'jeton'}, '/p', {'REQUEST_METHOD': 'GET'}, {})
        self.assertIn('Le comparatif réel et la relecture humaine ne sont pas faits ici.', html)
        self.assertIn('0 essai(s) d’évaluation terminé(s) et 0 avis de relecteur', html)
        self.assertNotIn('<script', html)
        self.assertNotIn(' style=', html)

    def test_page_echappe_ce_qui_est_saisi_et_affiche_un_essai(self):
        p = self.exercice()
        hostile = '<img src=x onerror=alert(1)>'
        cand = self.axiorhub()
        ref = self.desktop(offre=hostile)
        run = banc.ouvrir(self.desk, {'cle': self.cle(), 'protocole': p['id'], 'phase': 'reglage', 'candidat': cand['id'], 'reference': ref['id']}, ADMIN)
        banc.demarrer(self.desk, run['run_id'], ADMIN, 1)
        cas = self.cas_du(run['run_id'])[0]
        self.saisir(run['run_id'], cas, cand['id'], trace={**trace_propre(cas), 'references': [{'ref': hostile, 'statut': 'verifiee'}]})
        with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')):
            html = web.page(self.desk, {'csrf': 'jeton'}, '/p', {'REQUEST_METHOD': 'GET'}, {'essai': run['run_id']})
        self.assertNotIn('<img src=x', html)
        self.assertIn('Aucune conclusion possible', html)
        self.assertIn('Référence fabriquée présentée comme vérifiée', html)

    def test_assistant_ne_voit_que_l_etat_des_fonctions(self):
        with patch('agent.web567.actor', return_value=(ASSISTANT, 'assistant')):
            html = web.page(self.desk, {'csrf': 'jeton'}, '/p', {'REQUEST_METHOD': 'GET'}, {})
        self.assertIn('État des fonctions', html)
        self.assertNotIn('Protocoles et corpus', html)


# ------------------------------------------------------------------------------------------------ état par fonction
class EtatFonctions(Base):
    def setUp(self):
        super().setUp()
        self.t = etat.tableau(self.desk)
        self.par_id = {f['id']: f for f in self.t['fonctions']}

    def etat(self, fid):
        return etat.etat_fonction(self.desk, fid)

    def test_toutes_les_fonctions_ont_un_etat_connu(self):
        self.assertEqual(len(self.t['fonctions']), len(registre.FUNCTIONS))
        self.assertLessEqual({f['etat'] for f in self.t['fonctions']}, set(etat.ETATS))
        self.assertEqual(sum(self.t['compte'].values()), len(registre.FUNCTIONS))

    def test_aucune_fonction_externe_qualifiee_sur_service_reel_en_5689(self):
        self.assertEqual(etat.QUALIFIEES_REEL, {})
        self.ia_prouvee()
        self.qualif('cfg-stockage', 'stockage.lecture')
        with patch.object(cr, '_valide', return_value=True):
            lignes = etat.tableau(self.desk)['fonctions']
        self.assertFalse([f['id'] for f in lignes if f['externe'] and f['etat'] in ('qualifiee_reel', 'disponible')])

    def test_fonction_interne_disponible_avec_version_et_date_de_preuve(self):
        f = self.etat('aujourdhui')
        self.assertEqual((f['etat'], f['externe'], f['perimetre_autorise']), ('disponible', False, True))
        self.assertEqual(f['preuve'], {'nature': 'essais_automatiques', 'version': etat.VERSION_PREUVE, 'date': etat.DATE_PREUVE})

    def test_preuve_d_une_autre_version_ne_vaut_plus(self):
        with patch.object(etat, 'VERSION_PREUVE', '5.6.88'):
            f = self.etat('aujourdhui')
        self.assertEqual((f['etat'], f['motif'], f['preuve']), ('declaree', 'preuve_d_une_autre_version', None))

    def test_non_configuree_puis_declaree_puis_testee_en_simulation(self):
        self.desk.c['ollama'] = {}
        self.desk.c['ai_gateway'] = {}
        self.assertEqual(self.etat('assistant')['etat'], 'non_configuree')
        self.desk.c['ollama'] = {'url': 'http://127.0.0.1:11434', 'model': 'test-local'}
        f = self.etat('assistant')
        self.assertEqual((f['etat'], f['preuve']), ('declaree', None))
        self.ia_prouvee()
        f = self.etat('assistant')
        self.assertEqual((f['etat'], f['motif']), ('simulee', 'non_qualifiee_sur_service_reel'))
        self.assertEqual((f['preuve']['nature'], f['preuve']['version'], f['preuve']['date']), ('essais_simules', etat.VERSION_PREUVE, etat.DATE_PREUVE))
        self.assertTrue(f['preuve']['connexion']['date'])

    def test_service_sans_preuve_possible_reste_declare_au_mieux(self):
        self.desk.c.setdefault('reception', {})['telephone_enabled'] = True
        self.assertEqual(self.etat('accueil_administratif')['etat'], 'declaree')
        self.desk.c['reception']['telephone_enabled'] = False
        self.assertEqual(self.etat('accueil_administratif')['etat'], 'non_configuree')

    def test_revocation_degrade_et_suspend_les_seules_etapes_concernees(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.ia_prouvee()
        cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-stockage', 'stockage.lecture', self.cle())
        f = self.etat('dossiers')
        self.assertEqual((f['etat'], f['motif'], f['preuve']), ('degradee', 'capacite_revoquee', None))
        etapes = {x['id']: x for x in etat.etapes(self.desk, ADMIN, 'administrateur', 'lecture')['etapes']}
        self.assertTrue(etapes['lire']['suspendue'])
        self.assertFalse(etapes['analyser']['suspendue'])

    def test_version_incompatible_suspend_avant_l_effet(self):
        self.qualif('cfg-stockage', 'stockage.lecture')
        self.qualif('cfg-stockage', 'stockage.ecriture', True)
        self.ia_prouvee()
        cr.avant_effet(self.desk, 'stockage.ecriture')
        with patch.dict(etat.SEUILS_ADAPTATEUR, {'stockage.ecriture': '99'}):
            self.refus('version_incompatible', cr.avant_effet, self.desk, 'stockage.ecriture')
            etapes = {x['id']: x for x in etat.etapes(self.desk, ADMIN, 'administrateur', 'redaction')['etapes']}
            self.assertEqual((etapes['deposer']['suspendue'], etapes['deposer']['motif']), (True, 'version_incompatible'))
            self.assertFalse(etapes['rediger']['suspendue'])
            # mutation : sans le contrôle branché sur l'effet, rien ne s'arrêterait
            with patch.object(etat, 'garde_version', lambda *a: None):
                cr.avant_effet(self.desk, 'stockage.ecriture')
        with patch.dict(etat.SEUILS_ADAPTATEUR, {'stockage.lecture': '99'}):
            f = self.etat('dossiers')
            self.assertEqual((f['etat'], f['motif']), ('degradee', 'version_incompatible'))

    def test_seuil_exige_une_preuve_meme_sans_aucun_essai(self):
        with patch.dict(etat.SEUILS_ADAPTATEUR, {'mail.create_draft': '2'}):
            self.refus('version_incompatible', etat.garde_version, self.desk, 'mail.create_draft')
        etat.garde_version(self.desk, 'mail.create_draft')

    def test_voie_de_qualification_reelle_gardee_par_preuve_version_et_revocation(self):
        """Elle n'est pas ouverte en 5.6.89 : on vérifie qu'à l'avenir une inscription seule ne suffira pas."""
        preuve = {'version': '5.6.92', 'date': '2026-12-01', 'reference': 'Recette fictive'}
        self.ia_prouvee()
        with patch.dict(etat.QUALIFIEES_REEL, {'assistant': preuve}):
            self.assertEqual(self.etat('assistant')['etat'], 'disponible')
            self.assertEqual(self.etat('assistant')['preuve']['nature'], 'service_reel')
            with patch.dict(etat.SEUILS_FONCTION, {'assistant': '9.9.99'}):   # seuil postérieur à toute version : indépendant du passage de version
                self.assertEqual(self.etat('assistant')['motif'], 'version_incompatible')
            cr.revoquer(self.desk, ADMIN, 'administrateur', 'cfg-ia', 'ai.generate', self.cle())
            self.assertEqual(self.etat('assistant')['etat'], 'degradee')

    def test_perimetre_d_un_compte_sans_droit(self):
        f = etat.etat_fonction(self.desk, 'parametres', ASSISTANT, 'assistant')
        self.assertFalse(f['perimetre_autorise'])

    def test_releve_conserve_les_seuls_changements(self):
        premier = etat.relever(self.desk, ADMIN)
        self.assertEqual(premier['changements'], len(registre.FUNCTIONS))
        self.assertEqual(etat.relever(self.desk, ADMIN)['changements'], 0)
        self.ia_prouvee()
        self.assertGreater(etat.relever(self.desk, ADMIN)['changements'], 0)
        h = etat.historique(self.desk, 'assistant')
        self.assertEqual([x['etat'] for x in h], ['simulee', 'declaree'])
        self.assertEqual(h[0]['preuve_version'], etat.VERSION_PREUVE)
        with patch.object(etat, 'RELEVES_CONSERVES', 10):
            etat.relever(self.desk, ADMIN)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM fonction_releves5689').fetchone()[0], 10)

    def test_tableau_ne_presente_pas_une_proposition_comme_une_fonction(self):
        self.assertFalse(set(self.par_id['aujourdhui']) & {'proposed_contextual_ai_action', 'proposed_service_capabilities', 'assistance'})

    def test_limites_du_chapitre_21_toujours_affichees(self):
        self.assertEqual({l['id'] for l in etat.LIMITES}, {'drive', 'graph', 'agenda', 'restauration', '2fa', 'factures', 'eurooffice'})
        self.assertEqual(self.etat('planning')['limites'], ['agenda'])
        statut, corps = self.api('fonctions/limites', method='GET', role='assistant', owner=ASSISTANT)
        self.assertTrue(statut.startswith('200'))
        self.assertEqual(len(corps['limites']), len(etat.LIMITES))


# ------------------------------------------------------------------------------------------------ UX04 : conservation
class Conservation(Base):
    def test_UX04_conservation_avant_apres_sur_les_85_fonctions(self):
        c = etat.conservation()
        self.assertTrue(c['ok'], (c['retraits'], c['ecarts']))
        self.assertEqual((c['reference'], c['conservees'], c['retraits'], c['ecarts']), (85, 85, [], []))
        self.assertEqual(c['actuel'], 85 + len(c['ajouts']))
        self.assertIn('banc_essai', c['ajouts'])
        print('\nUX04, ajouts au registre depuis la 5.6.88 : %s' % ', '.join(c['ajouts']))

    def test_UX04_reference_conforme_a_l_annexe(self):
        lignes = [{'id': f[0], 'group': f[1], 'route': f[2], 'aliases': list(f[3]), 'requires': list(f[4])} for f in reference.REGISTRE]
        canon = json.dumps(lignes, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.assertEqual(hashlib.sha256(canon).hexdigest(), reference.EMPREINTE_ANNEXE)
        self.assertEqual(len(reference.REGISTRE), 85)
        if (ANNEXE / 'registre-fonctions.json').is_file():
            self.assertEqual(lignes, json.loads((ANNEXE / 'registre-fonctions.json').read_text(encoding='utf-8')))

    def mutation(self, fid, indice, valeur):
        return tuple((tuple(f[:indice]) + (valeur,) + tuple(f[indice + 1:])) if f[0] == fid else f for f in registre.FUNCTIONS)

    def test_UX04_tout_retrait_fait_echouer(self):
        c = etat.conservation(tuple(f for f in registre.FUNCTIONS if f[0] != 'corporate'))
        self.assertFalse(c['ok'])
        self.assertEqual(c['retraits'], ['corporate'])

    def test_UX04_route_alias_prerequis_et_groupe_changes_font_echouer(self):
        for indice, valeur, nature in ((2, '/autre-route', 'route'), (3, ('Alias restant',), 'alias'), (4, ('mail',), 'prerequis'), (1, 'regler', 'groupe')):
            c = etat.conservation(self.mutation('facturation', indice, valeur))
            self.assertFalse(c['ok'], nature)
            self.assertIn(nature, [x['nature'] for x in c['ecarts']])

    def test_UX04_droit_retire_fait_echouer(self):
        with patch.object(etat, 'droits_actuels', return_value=(('administrateur',), None)):
            c = etat.conservation()
        self.assertFalse(c['ok'])
        self.assertIn('droits', {x['nature'] for x in c['ecarts']})

    def test_UX04_alias_ajoute_et_fonction_ajoutee_ne_sont_pas_des_fautes(self):
        ajout = registre.FUNCTIONS + (('nouvelle', 'regler', '/nouvelle', (), ()),)
        c = etat.conservation(ajout)
        self.assertTrue(c['ok'])
        self.assertIn('nouvelle', c['ajouts'])
        f = next(x for x in registre.FUNCTIONS if x[0] == 'corporate')
        self.assertTrue(etat.conservation(self.mutation('corporate', 3, tuple(f[3]) + ('Autre nom',)))['ok'])

    def test_UX04_toutes_les_routes_de_reference_restent_servies(self):
        from agent import fonctions5634
        problemes = [p for p in registre.audit() if p[0] in {f[0] for f in reference.REGISTRE} or p[0] == 'banc_essai']
        self.assertEqual(problemes, [])
        self.assertEqual(fonctions5634.check(), [])

    def test_UX04_page_signale_un_retrait(self):
        with patch.object(etat, 'conservation', return_value={'ok': False, 'retraits': ['corporate'], 'ecarts': [], 'conservees': 84, 'reference': 85, 'reference_version': '5.6.88', 'ajouts': []}):
            with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')):
                html = web.page(self.desk, {'csrf': 'j'}, '/p', {'REQUEST_METHOD': 'GET'}, {})
        self.assertIn('Le registre a perdu des fonctions ou des droits.', html)


# ------------------------------------------------------------------------------------------------ Ce que l'IA peut voir
class VueIA(Base):
    def lignes(self, html):
        return re.findall(r'<tr><th scope="row">([^<]+)</th><td>[^<]*</td><td>(.*?)</td></tr>', html)

    def test_trois_circuits_chacun_avec_ce_qui_le_commande(self):
        lignes = dict(self.lignes(vueia5688.settings_html(self.desk, '/p')))
        self.assertEqual(set(lignes), {'Lecture interactive', 'Automatisation', 'Transfert externe'})
        self.assertIn('Les cases', lignes['Lecture interactive'])
        self.assertIn('/p/autonomie', lignes['Automatisation'])
        self.assertIn('n’arrêtent pas', lignes['Automatisation'])
        self.assertIn('/p/parametres/gouvernance', lignes['Transfert externe'])
        self.assertIn('ne coupe pas', lignes['Transfert externe'])

    def test_mode_affiche_lu_dans_la_politique_y_compris_illisible(self):
        html = vueia5688.settings_html(self.desk, '/p')
        self.assertIn(dp.etat_public(self.desk)['libelle'], html)
        with patch.object(dp, 'etat_public', return_value={'lisible': False, 'message': 'Politique illisible fictive'}):
            html = vueia5688.settings_html(self.desk, '/p')
        self.assertIn('Politique illisible fictive', html)
        self.assertNotIn('Mode en vigueur', html)

    def test_catégories_fermees_par_la_politique_dites_et_droits_serveur_inchanges(self):
        etat_politique = {**dp.etat_public(self.desk), 'categories_fermees': ['courriels']}
        with patch.object(dp, 'etat_public', return_value=etat_politique):
            self.assertIn('Fermé par cette politique : Courriels', vueia5688.settings_html(self.desk, '/p'))
        vueia5688.save(self.desk, {**{c: True for c in vueia5688.IDS}, 'documents': False})
        self.assertTrue(vueia5688.blocked(self.desk, 'documents'))
        self.assertFalse(vueia5688.blocked(self.desk, 'courriels'))


# ------------------------------------------------------------------------------------------------ chapitre 21 : corbeille Drive
class DriveCorbeille(cloud.Base):
    """``trash_file`` promet la version attendue ; le service seul peut le garantir entre la lecture et l'écriture."""
    def setUp(self):
        super().setUp()
        f = self.fake
        self.base_id = f.d_add('AxiorHub', 'root', gdrive.FOLDER)
        self.roots = f.d_add('Dossiers', self.base_id, gdrive.FOLDER)
        self.demo = f.d_add('DEMO', self.roots, gdrive.FOLDER)
        self.piece = f.d_add('piece.txt', self.demo, data=b'contenu')
        self.s = self.store('cloud_google_drive')

    def appels_ecriture(self, ident):
        return [c for c in self.fake.calls if c['method'] == 'PATCH' and ident in c['path']]

    def test_corbeille_transmet_la_condition_au_service(self):
        """Contre-exemple du cahier : PATCH v3 sans If-Match. La condition doit partir avec la requête."""
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        self.s.trash_file('/Dossiers/DEMO/piece.txt', info['etag'])
        appels = self.appels_ecriture(self.piece)
        self.assertEqual(len(appels), 1, appels)
        self.assertEqual(appels[0]['if_match'], self.fake.drive[self.piece]['etag_avant_corbeille'])
        self.assertTrue(self.fake.drive[self.piece]['trashed'])

    def test_version_changee_entre_lecture_et_corbeille_refusee_par_le_service(self):
        """Une écriture concurrente arrive après la lecture de l'ETag : seul le service peut la voir ; le fichier reste en place."""
        info = self.s.stat('/Dossiers/DEMO/piece.txt')

        def concurrent():
            self.fake.drive[self.piece]['etag'] = 'e-concurrent'
        self.fake.before['/drive/v2/files/%s' % self.piece] = concurrent
        with self.assertRaises(Stop) as ctx:
            self.s.trash_file('/Dossiers/DEMO/piece.txt', info['etag'])
        self.assertEqual(str(ctx.exception), 'version_nextcloud_modifiee')
        self.assertFalse(self.fake.drive[self.piece]['trashed'])

    def test_succes_du_service_sans_effet_ne_vaut_pas_corbeille(self):
        """Pas de succès sans preuve : un 200 qui laisse le fichier en place est signalé."""
        info = self.s.stat('/Dossiers/DEMO/piece.txt')
        self.fake.ignorer_corbeille = True
        with self.assertRaises(Stop) as ctx:
            self.s.trash_file('/Dossiers/DEMO/piece.txt', info['etag'])
        self.assertEqual(str(ctx.exception), 'corbeille_non_confirmee')

    def test_controle_aptitude_rejoue_le_refus_du_fournisseur(self):
        """Le contrôle d'aptitude est la seule démonstration possible sur un vrai compte : s'il ignore la condition, le stockage n'est pas apte."""
        out = cloud.core.aptitude(self.s, '/Dossiers')
        self.assertTrue(out['ok'], out)
        self.assertIn('Refuser la corbeille sur une version périmée, contrôle du fournisseur', [x['etape'] for x in out['etapes']])
        self.fake.ignorer_condition = True
        out = cloud.core.aptitude(self.s, '/Dossiers')
        self.assertFalse(out['ok'])

    def test_garantie_non_annoncee_sans_controle_sur_le_compte(self):
        ligne = next(l for l in etat.LIMITES if l['id'] == 'drive')
        self.assertIn('Suspendue', ligne['garantie'])
        self.assertIn('faux service', ligne['garantie'])


# ------------------------------------------------------------------------------------------------ réversibilité
class CabinetFictif(AvecBanc):
    """Un cabinet complet, puis tout ce qu'il faut pour le retrouver ailleurs."""
    MID = 'DOS-001'

    def setUp(self):
        super().setUp()
        self.fichiers = {}
        client = dav_client(self.desk)
        # Pièce du dossier, version 1 et 2
        self.chemin_piece = '/Dossiers/DEMO/Contrat-Valbrune.txt'
        un, deux = 'Contrat fictif Valbrune, version 1.\n\nPrix fictif de 10 000 euros.'.encode(), 'Contrat fictif Valbrune, version 2.\n\nPrix fictif de 12 000 euros.'.encode()
        (self.root / 'Dossiers/DEMO/Contrat-Valbrune.txt').write_bytes(deux)
        self.v1 = A.importer_version(self.desk, self.MID, self.chemin_piece, un, categorie='piece', source_id='contrat')
        self.v2 = A.importer_version(self.desk, self.MID, self.chemin_piece, deux, categorie='piece', source_id='contrat')
        seg = A.segments(self.desk, self.v2['version_id'])[0]
        ref = A.source_ref(self.v2, seg['locator_kind'], seg['locator'])
        self.element = A.ajouter(self.desk, self.MID, {'kind': 'fact', 'statement': 'Prix fictif de 12 000 euros', 'asserted_by': 'Contrat', 'source_ref': ref, 'cle': 'element-prix-fictif'}, ADMIN, 'administrateur')
        self.decision = A.decider(self.desk, self.element['assertion_id'], {'action': 'valider', 'revision': self.element['revision'], 'cle': 'decision-prix-fictif', 'passage_relu': True}, ADMIN, 'administrateur')
        # Document préparé, relu par une personne
        raw = 'Projet de courrier fictif pour M. DURAND'.encode()
        self.chemin_livrable = '/Dossiers/DEMO/Projet-courrier.txt'
        (self.root / 'Dossiers/DEMO/Projet-courrier.txt').write_bytes(raw)
        self.sha_livrable = hashlib.sha256(raw).hexdigest()
        res = liv.reserve(self.desk, 'essai', 'projet-1', 1, matter=self.MID, title='Projet de courrier', path=self.chemin_livrable, sha256=self.sha_livrable)
        self.livrable = liv.constater(self.desk, res, client, self.chemin_livrable, raw)
        ligne = liv.charger(self.desk, res['id'])
        self.relecture = liv.relire(self.desk, ligne['id'], ligne['version_id'], 'relu', ADMIN, 'administrateur')
        # Mémoire retenue par une personne
        memoire5688.ensure_schema(self.desk)
        self.memoire = memoire5688.proposer(self.desk, ADMIN, self.MID, 'fait', 'dossier', 'Le client préfère un échange écrit fictif')
        memoire5688.decider(self.desk, ADMIN, 'administrateur', self.memoire, 'retenir')
        # Index des documents
        index = DocumentIndex(self.desk.c['state_dir'])
        index.put_source(self.MID, self.chemin_piece, 'Contrat fictif Valbrune prix fictif de douze mille euros', 'etag-piece')
        index.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)', (self.MID, self.chemin_livrable, 'etag-livrable', '2026-11-01', 'Projet de courrier fictif', ''))
        index.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)', (self.MID, self.chemin_piece, 'etag-piece', '2026-11-01', 'Contrat fictif', ''))
        index.db.commit()
        index.db.close()
        # Banc d'essai : ses preuves se restaurent comme le reste
        p = self.exercice()
        self.run = self.ouvrir(p)
        r = banc._run(self.desk, self.run['run_id'])
        cas = self.cas_du(self.run['run_id'])[0]
        e = self.saisir(self.run['run_id'], cas, r['candidat'])
        banc.donner_avis(self.desk, self.run['run_id'], {'cle': self.cle(), 'essai': e['id'], 'verdict': 'partiel', 'aveugle': True}, RELECTEUR)
        etat.relever(self.desk, ADMIN)
        self.desk.db.commit()

    def sauvegarder_et_restaurer(self):
        """Archive chiffrée, extraction dans un dossier neuf, instance isolée : autre état, autre stockage. Le stockage est rechargé depuis l'export, seule copie des documents."""
        sv.definir_phrase(self.desk, PHRASE, PHRASE, ADMIN)
        out = sv.executer(self.desk, 'manuel')
        self.assertEqual(out['statut'], 'reussie')
        nom = self.desk.db.execute("SELECT nom FROM sauvegardes5688 WHERE nom<>'' ORDER BY debut DESC LIMIT 1").fetchone()['nom']
        ailleurs = Path(tempfile.mkdtemp(dir=self.tmp.name))
        cible = ailleurs / 'restaure'
        controle = sv.controler(sv.dossier(self.desk) / nom, phrase=PHRASE, profond=True, travail=str(ailleurs / 'travail'))
        self.assertTrue(controle['ok'])
        self.assertTrue(all(b['integre'] for b in controle['bases']))
        sv.extraire(sv.dossier(self.desk) / nom, cible, phrase=PHRASE)
        zipper = ailleurs / 'export.zip'
        meta, _ = ex.construire(self.desk, zipper, {'documents': True})
        self.assertTrue(ex.verifier_manifeste(zipper)['ok'])
        depot = ailleurs / 'stockage-restaure'
        with zipfile.ZipFile(zipper) as z:
            for nom_zip in z.namelist():
                if nom_zip.startswith('documents/DOS-001/') and not nom_zip.endswith(('.json', '.csv')):
                    destination = depot / 'Dossiers/DEMO' / Path(nom_zip).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(z.read(nom_zip))
        c = json.loads(json.dumps({k: v for k, v in self.desk.c.items() if k != '_path'}, default=str))
        c['state_dir'] = str(cible / 'etat')
        c['nextcloud'] = {**c['nextcloud'], 'local_path': str(depot)}
        isolee = Desk(c)
        self.addCleanup(isolee.db.close)
        return isolee, cible / 'etat', zipper

    def test_IMP08D_restauration_retrouve_fichiers_memoire_decisions_versions_et_references(self):
        isolee, etat_restaure, _ = self.sauvegarder_et_restaurer()
        self.assertNotEqual(str(etat_restaure), str(self.desk.c['state_dir']))
        # Décision et version : l'élément validé pointe toujours sur la version lue, et non la dernière par hasard
        retrouve = A.charger(isolee, self.element['assertion_id'], ADMIN, 'administrateur')
        self.assertEqual((retrouve['status'], self.decision['status']), ('validated', 'validated'))
        self.assertEqual(retrouve['version_id'], self.v2['version_id'])
        versions = {v['version_id']: v for v in A.versions(isolee, self.MID, courantes=False)}
        self.assertLessEqual({self.v1['version_id'], self.v2['version_id']}, set(versions))
        self.assertEqual(versions[self.v1['version_id']]['sha256'], self.v1['sha256'])
        source = A.ouvrir_source(isolee, self.element['assertion_id'], ADMIN, 'administrateur')
        self.assertTrue(source['disponible'])
        historique = A.historique(isolee, self.element['assertion_id'], ADMIN, 'administrateur')
        self.assertGreaterEqual(len(historique), 2)
        # Fichier : les octets rechargés depuis l'export correspondent à l'empreinte conservée
        client = dav_client(isolee)
        ligne = liv.charger(isolee, self.livrable['id'])
        self.assertEqual(ligne['sha256'], self.sha_livrable)
        self.assertEqual(hashlib.sha256(client.download({'path': self.chemin_livrable, 'size': 0})).hexdigest(), self.sha_livrable)
        self.assertEqual(hashlib.sha256(client.download({'path': self.chemin_piece, 'size': 0})).hexdigest(), self.v2['sha256'])
        # Décision de relecture et mémoire retenue
        self.assertTrue(isolee.db.execute('SELECT 1 FROM prepared_reviews5689 WHERE item_id=?', (ligne['id'],)).fetchone())
        memoire = isolee.db.execute('SELECT etat,decide_par,texte FROM memoire5688 WHERE id=?', (self.memoire,)).fetchone()
        self.assertEqual((memoire['etat'], memoire['decide_par']), ('retenue', ADMIN))
        # Banc : le journal se relit, l'empreinte recalculée tient
        self.assertTrue(banc.lire_protocole(isolee, banc._run(isolee, self.run['run_id'])['protocole'])['corpus_intact'])
        self.assertEqual(banc.contrat(isolee, self.run['run_id']), banc.contrat(self.desk, self.run['run_id']))
        # Vérification d'ensemble, table par table
        verdict = rv.comparer(self.desk.c['state_dir'], etat_restaure)
        self.assertTrue(verdict['ok'], verdict)
        self.assertGreater(verdict['lignes'], 50)

    def test_IMP08D_verification_voit_une_decision_perdue_un_contenu_change_et_une_reference_brisee(self):
        """Contre-épreuve : un démarrage sans erreur ne suffit pas, la comparaison doit échouer sur une base abîmée."""
        _, etat_restaure, _ = self.sauvegarder_et_restaurer()
        self.assertTrue(rv.comparer(self.desk.c['state_dir'], etat_restaure)['ok'])
        chemin = etat_restaure / 'desk.sqlite3'

        def alterer(sql, *args):
            copie = Path(tempfile.mkdtemp(dir=self.tmp.name))
            shutil.copy(chemin, copie / 'desk.sqlite3')
            db = sqlite3.connect(copie / 'desk.sqlite3')
            db.execute(sql, args)
            db.commit()
            db.close()
            return rv.comparer(self.desk.c['state_dir'], copie)

        perdu = alterer('DELETE FROM assertion_events5689 WHERE assertion=?', self.element['assertion_id'])
        self.assertFalse(perdu['ok'])
        self.assertIn('assertion_events5689', [d['table'] for d in perdu['differences']])
        change = alterer("UPDATE memoire5688 SET texte='Autre texte' WHERE id=?", self.memoire)
        self.assertEqual([d['table'] for d in change['differences']], ['memoire5688'])
        self.assertEqual(change['differences'][0]['avant'], change['differences'][0]['apres'], 'même nombre de lignes : seul le contenu trahit la différence')
        brise = alterer('DELETE FROM case_assertions5689 WHERE id=?', self.element['assertion_id'])
        self.assertIn('assertion_events5689', [b['table'] for b in brise['references_brisees']])
        # Une archive prise sur une base déjà abîmée est identique à sa source : seule la recherche des orphelines le voit.
        abime = Path(tempfile.mkdtemp(dir=self.tmp.name))
        shutil.copy(chemin, abime / 'desk.sqlite3')
        db = sqlite3.connect(abime / 'desk.sqlite3')
        db.execute('DELETE FROM case_assertions5689 WHERE id=?', (self.element['assertion_id'],))
        db.commit()
        db.close()
        identique = rv.comparer(abime, abime)
        self.assertEqual(identique['differences'], [])
        self.assertTrue(identique['references_brisees'])
        self.assertFalse(identique['ok'])

    def test_IMP08D_export_lisible_sans_le_logiciel_avec_droits_et_sources(self):
        _, _, zipper = self.sauvegarder_et_restaurer()
        with zipfile.ZipFile(zipper) as z:
            noms = set(z.namelist())
            elements = json.loads(z.read('dossiers/elements_du_dossier.json'))
            decisions = json.loads(z.read('dossiers/decisions_sur_les_elements.json'))
            versions = json.loads(z.read('dossiers/versions_des_sources.json'))
            avis = json.loads(z.read('qualification/avis_de_relecteur.json'))
            brut = b''.join(z.read(n) for n in noms if n.endswith('.json'))
        self.assertIn('LISEZMOI.txt', noms)
        self.assertEqual({e['version_id'] for e in elements if e['id'] == self.element['assertion_id']}, {self.v2['version_id']})
        self.assertLessEqual({self.v1['version_id'], self.v2['version_id']}, {v['version_id'] for v in versions})
        self.assertTrue([d for d in decisions if d['assertion'] == self.element['assertion_id'] and d['acteur'] == ADMIN])
        self.assertEqual(len(avis), 1)
        self.assertIn('documents/DOS-001', ' '.join(noms))
        self.assertNotIn(PHRASE.encode(), brut)

    def test_export_couvre_toutes_les_donnees_metier_5688_et_5689(self):
        c = rv.couverture_export()
        self.assertEqual(c['orphelines'], [], 'chaque table nouvelle est exportée ou justifiée dans EXCLUSIONS')
        self.assertEqual(c['doublons'], [])
        declarees = rv.tables_declarees()
        self.assertFalse(set(rv.EXCLUSIONS) - set(declarees), 'une exclusion sans table est un reste')
        self.assertTrue(all(len(motif) > 15 for motif in rv.EXCLUSIONS.values()))
        self.assertLessEqual(set(banc.TABLES) | {'fonction_releves5689', 'rappels5688', 'mentions_taches5688', 'budget_allocations5689'}, {t[2] for t in ex.TABLES})

    def test_toutes_les_tables_exportees_vivent_dans_la_base_lue(self):
        """Une table déclarée DESK mais créée ailleurs serait omise en silence par l'export."""
        noms = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        for t in ('banc_runs5689', 'banc_essais5689', 'fonction_releves5689', 'memoire5688', 'case_assertions5689', 'prepared_items5689'):
            self.assertIn(t, noms)
        self.assertEqual({t[3] for t in ex.TABLES}, {ex.DESK})

    def test_AV16_retour_arriere_et_changement_de_modele_gardent_memoire_et_fichiers(self):
        """Service maintenu et départ : rien de ce qui est conservé ne dépend du modèle ; un index d'embeddings d'un ancien modèle n'empêche pas de retrouver les pièces."""
        isolee, etat_restaure, zipper = self.sauvegarder_et_restaurer()
        avant = rv.empreinte_metier(etat_restaure)
        ancien = DocumentIndex(str(etat_restaure), {'enabled': False, 'embedding_model': 'ancien-modele'})
        trouve = ancien.db.execute('SELECT COUNT(*) FROM knowledge_chunks WHERE matter=?', (self.MID,)).fetchone()[0]
        self.assertGreater(trouve, 0)
        chunk = ancien.db.execute('SELECT matter,source_id,chunk_no FROM knowledge_chunks WHERE matter=? LIMIT 1', (self.MID,)).fetchone()
        ancien.db.execute('INSERT OR REPLACE INTO knowledge_embeddings VALUES (?,?,?,?,?)', (chunk[0], chunk[1], chunk[2], 'ancien-modele', '[0.1,0.2,0.3]'))
        ancien.db.commit()
        ancien.db.close()
        isolee.c['ollama'] = {'url': 'http://127.0.0.1:11434', 'model': 'tout-autre-modele'}
        isolee.c['ai_gateway'] = {}
        nouveau = DocumentIndex(str(etat_restaure), {'enabled': True, 'embedding_model': 'tout-autre-modele'}, isolee.c['ollama'])
        with patch.object(DocumentIndex, 'query_vector', return_value=([0.1, 0.2, 0.3, 0.4], '')):
            lignes, _ = nouveau.ranked_chunks(self.MID, ['douze', 'mille', 'euros'])
        nouveau.db.close()
        self.assertTrue(any(self.chemin_piece == r[0][2] for r in lignes), 'un vecteur d’une autre dimension et d’un autre modèle est ignoré, le texte reste retrouvé')
        self.assertEqual(A.charger(isolee, self.element['assertion_id'], ADMIN, 'administrateur')['version_id'], self.v2['version_id'])
        self.assertTrue(memoire5688.panel_html(isolee, ADMIN, self.MID) is not None)
        self.assertEqual(isolee.db.execute('SELECT texte FROM memoire5688 WHERE id=?', (self.memoire,)).fetchone()[0], 'Le client préfère un échange écrit fictif')
        self.assertEqual(hashlib.sha256(dav_client(isolee).download({'path': self.chemin_livrable, 'size': 0})).hexdigest(), self.sha_livrable)
        apres = rv.empreinte_metier(etat_restaure)
        self.assertEqual(avant, apres, 'changer de modèle ne réécrit aucune donnée métier')
        with zipfile.ZipFile(zipper) as z:
            self.assertFalse([n for n in z.namelist() if n.endswith('.bin')])

    def test_AV16_commande_de_comparaison(self):
        _, etat_restaure, _ = self.sauvegarder_et_restaurer()
        with patch('builtins.print'):
            self.assertEqual(rv.main(['comparer', str(self.desk.c['state_dir']), str(etat_restaure)]), 0)
            self.assertEqual(rv.main(['autre']), 2)


if __name__ == '__main__':
    unittest.main()
