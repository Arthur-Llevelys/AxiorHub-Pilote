"""5.6.109, lot A (fiabilité) : C05 arrêts propagés, C06 relevé de fichiers reprenable, C07 écritures à résultat incertain rapprochées,
C08 saisie WordCraft préservée, C04 couverture réelle de l'analyse d'un dossier.

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, example.test) ; faux stockages (dossier local temporaire, faux WebDAV en mémoire de
``test_v56103_fichiers``) ; aucun réseau, aucun modèle. Chaque garde-fou a son test de mutation (en mémoire, jamais dans le code livré).
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch
import urllib.parse

from agent import dossier5624 as A, dossiers5697 as D, fichiers56103 as FI, reprise56109 as R, sync56108 as S, sync56108_fichiers as F
from agent import sync56108_missions as M, wordcraft56108 as WC
from agent.common import Stop
from agent.desk import Desk
import test_agent as fx
from test_v56103_fichiers import DURAND
from test_v56108_editeur import Base as BaseEditeur, DOC
from test_v56108_sync import Poste, ADMIN
from test_v56108_sync_s5 import stockage_local

ROOT = Path(__file__).resolve().parent.parent


# ================================================================================================ C05 · arrêts propagés
class C05Analyse(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)

    def etapes(self):
        return {s['step']: s['state'] for s in A.state(self.d, 'DOS-001')['steps']}

    def test_C05_arret_pendant_un_lot_aucune_etape_suivante(self):
        """Arrêt au deuxième lot d'indexation : il remonte tel quel, les étapes suivantes ne sont jamais engagées."""
        appels = []

        def perform(kind, args):
            appels.append(kind)
            if len(appels) == 2:
                raise Stop('operation_annulee')
            return {'fichiers': 100, 'en_attente': 5}
        with patch.object(self.d, 'perform', side_effect=perform), patch('agent.facts460.scan_matter') as faits, \
                patch('agent.legal_memory.sync_matter_memory') as chrono, patch('agent.intelligence.refresh_brief') as synthese:
            with self.assertRaisesRegex(Stop, '^operation_annulee$'):
                A.analyse(self.d, {'matter': 'DOS-001'})
        self.assertEqual(appels, ['index', 'index'])
        for faux in (faits, chrono, synthese):
            faux.assert_not_called()

    def test_C05_etape_interrompue_n_est_plus_affichee_en_cours(self):
        """Le lot déjà accepté reste compté ; l'étape arrêtée est « interrompue », jamais « en cours » sans travail ni « terminée »."""
        appels = []

        def perform(kind, args):
            appels.append(kind)
            if len(appels) == 2:
                raise Stop('ia_en_pause')
            return {'fichiers': 100, 'en_attente': 5}
        with patch.object(self.d, 'perform', side_effect=perform):
            with self.assertRaisesRegex(Stop, '^ia_en_pause$'):
                A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        st = A.state(self.d, 'DOS-001')
        etape = next(s for s in st['steps'] if s['step'] == 'indexation')
        self.assertEqual(etape['state'], 'interrompue')
        self.assertEqual(etape['error'], 'ia_en_pause')
        self.assertEqual(etape['counters'].get('lots'), 1)             # ce qui a déjà pu être accepté est conservé
        self.assertEqual(st['overall'], 'partielle')
        self.assertNotIn(st['overall'], ('a_jour', 'en_cours'))

    def test_C05_etape_laissee_en_cours_par_une_coupure_est_conciliee_a_la_lecture(self):
        """Processus tué pendant une étape : la ligne reste « en_cours » en base ; sans travail actif, la lecture dit « interrompue »
        (sans écrire : R05)."""
        A.ensure_schema(self.d)
        A._step(self.d, 'DOS-001', 'faits', 'en_cours')
        avant = self.d.db.total_changes
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(self.d.db.total_changes, avant)
        self.assertEqual(next(s for s in st['steps'] if s['step'] == 'faits')['state'], 'interrompue')
        self.assertEqual(st['overall'], 'partielle')

    def test_C05_chaque_code_d_arret_remonte_a_chaque_etape(self):
        codes = ('operation_annulee', 'ia_en_pause', 'automatismes_en_pause', 'agent_en_pause_sur_ce_dossier', 'ia_en_pause_horaire',
                 'dossier_reserve', 'mission_annulee', 'budget_mission_atteint', 'budget_cabinet_atteint', 'controle_pause_indisponible',
                 'controle_acces_indisponible', 'execution_sur_un_autre_poste', 'traitement_deja_en_cours', 'compte_desactive')
        cibles = {'indexation': None, 'faits': 'agent.facts460.scan_matter', 'chronologie': 'agent.legal_memory.sync_matter_memory',
                  'synthese': 'agent.intelligence.refresh_brief'}
        for etape, cible in cibles.items():
            for code in codes:
                with self.subTest(etape=etape, code=code):
                    faux = patch.object(self.d, 'perform', side_effect=Stop(code)) if cible is None else patch(cible, side_effect=Stop(code))
                    with faux, self.assertRaisesRegex(Stop, '^' + code + '$'):
                        A.analyse(self.d, {'matter': 'DOS-001', 'steps': [etape]})
                    self.assertEqual(self.etapes()[etape], 'interrompue')

    def test_C05_mutation_sans_le_predicat_l_arret_deviendrait_partial(self):
        """Garde-fou : ``A.interruption``. Mutation en mémoire (prédicat toujours faux) : l'annulation est avalée en « partial »."""
        with patch.object(A, 'interruption', lambda ex: False), patch.object(self.d, 'perform', side_effect=Stop('operation_annulee')):
            out = A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        self.assertEqual(out['status'], 'partial')                    # exactement le défaut reproduit par l'audit

    def test_C05_erreur_technique_reste_partielle_et_les_autres_etapes_continuent(self):
        with patch.object(self.d, 'perform', side_effect=Stop('stockage_injoignable')), \
                patch('agent.intelligence.refresh_brief', side_effect=AssertionError('synthèse non recalculée sur une indexation en erreur')):
            out = A.analyse(self.d, {'matter': 'DOS-001'})
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['errors']['indexation'], 'stockage_injoignable')
        self.assertEqual(out['steps']['faits'], 'ok')
        self.assertEqual(out['steps']['synthese'], 'ignoree')

    def test_C05_travail_annule_par_le_moteur_etat_reel(self):
        """Par le moteur des travaux : l'annulation donne un travail « annulé » (ni « terminé », ni « erreur »), aucune étape active."""
        jid = A.request_analysis(self.d, 'DOS-001')['job_id']
        vrai = Desk.perform

        def perform(desk, kind, args):
            if kind != 'index':
                return vrai(desk, kind, args)
            desk.db.execute("UPDATE jobs SET status='cancel_requested', stop_reason='user' WHERE id=?", (jid,))   # « Arrêter » pendant le lot
            desk.db.commit()
            return {'fichiers': 100, 'en_attente': 5}
        with patch.object(Desk, 'perform', perform):
            self.d.work_once()
        row = self.d.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()
        self.assertEqual(row['status'], 'cancelled')
        st = A.state(self.d, 'DOS-001')
        self.assertIsNone(st['job'])
        self.assertEqual(next(s for s in st['steps'] if s['step'] == 'indexation')['state'], 'interrompue')
        self.assertEqual([s['state'] for s in st['steps'] if s['step'] != 'indexation'], ['jamais'] * 3)


# ================================================================================================ C04 · couverture réelle de l'analyse
class C04Couverture(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        from agent import legal_memory
        legal_memory.ensure_schema(self.d)
        self.lots = []

    # ---- faux index et faux modèle (aucun réseau, aucun modèle)
    def piece(self, nom, texte='Assignation devant le tribunal judiciaire de Valbrune. RG 24/01234.', etag='e1', erreur=''):
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.d.c['state_dir'])
        chemin = '/Dossiers/DEMO/' + nom
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', chemin, etag, '2026-11-20T10:00:00+00:00', texte, erreur))
        idx.db.commit()
        idx.db.close()
        return chemin

    def retirer(self, chemin):
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.d.c['state_dir'])
        idx.db.execute('DELETE FROM docs WHERE matter=? AND path=?', ('DOS-001', chemin))
        idx.db.commit()
        idx.db.close()

    def courriel_indexe(self, sujet, expediteur='durand@example.test'):
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.d.c['state_dir'])
        idx.put_source('DOS-001', 'imap://compte/received/' + hashlib.sha256(sujet.encode()).hexdigest()[:8], 'Bonjour Maître, voici les pièces.', 'm1',
                       '2026-11-19T09:00:00+00:00', 'email_received', {'sender': expediteur, 'subject': sujet})
        idx.db.close()

    def courriel_rattache(self, cle, sujet, expediteur='durand@example.test'):
        self.d.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)', (cle * 64, 'needs_action', '', '', 'DOS-001', sujet, expediteur,
                                                                                '2026-11-19T09:00:00+00:00', '2026-11-19T09:00:00+00:00', ''))
        self.d.db.commit()

    def synthese(self, chemins):
        def faux(desk, args):
            version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM case_briefs WHERE matter=?', ('DOS-001',)).fetchone()[0]
            desk.db.execute('INSERT INTO case_briefs VALUES(NULL,?,?,?,?,?)', ('DOS-001', version, json.dumps({'parties': ['DURAND']}),
                                                                             json.dumps([{'id': 'k%d' % i, 'path': p} for i, p in enumerate(chemins)]), desk.now()))
            desk.db.commit()
            self.appels_modele.append(version)
            return {'fiche_version': version, 'sources': len(chemins), 'fichiers_en_attente': 0}
        self.appels_modele = []
        return patch('agent.intelligence.refresh_brief', side_effect=faux)

    def index(self, *resultats):
        """``desk.perform('index')`` rend les résultats donnés, le dernier indéfiniment."""
        suite = list(resultats)

        def perform(kind, args):
            self.lots.append(kind)
            return suite.pop(0) if len(suite) > 1 else suite[0]
        return patch.object(self.d, 'perform', side_effect=perform)

    def etapes(self):
        return {s['step']: s for s in A.state(self.d, 'DOS-001')['steps']}

    COMPLET = {'fichiers': 2, 'en_attente': 0, 'inventaire': {'complete': True}}

    # ---- les deux défauts reproduits par l'audit
    def test_C04_une_seule_etape_ok_ne_devient_jamais_a_jour(self):
        A.ensure_schema(self.d)
        A._step(self.d, 'DOS-001', 'indexation', 'ok', A.source_revision(self.d, 'DOS-001'))
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(st['overall'], 'partielle')
        self.assertNotEqual(st['overall'], 'a_jour')

    def test_C04_dix_lots_avec_reste_ne_deviennent_jamais_prepared(self):
        with self.index({'fichiers': 100, 'en_attente': 5}):
            out = A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        self.assertEqual(len(self.lots), A.LOTS_INDEXATION)                       # borne de travail conservée…
        self.assertEqual((out['status'], out['steps']['indexation']), ('partial', 'partielle'))   # … qui n'est pas une preuve de fin
        e = self.etapes()['indexation']
        self.assertFalse(e['counters']['inventaire_complet'])
        self.assertIn('relancez', e['counters']['motif'])
        self.assertEqual(A.state(self.d, 'DOS-001')['overall'], 'partielle')

    def test_C04_inventaire_pagine_incomplet_n_est_pas_une_indexation_terminee(self):
        """Stockage paginé : ``en_attente`` vaut 0 à chaque page ; seul l'inventaire dit s'il reste des fichiers (avant : « ok » après une page)."""
        page = {'fichiers': 100, 'en_attente': 0, 'inventaire': {'complete': False, 'next_cursor': 100}}
        with self.index(page, page, self.COMPLET):
            out = A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        self.assertEqual((len(self.lots), out['steps']['indexation']), (3, 'ok'))
        self.lots.clear()
        with self.index(page):
            out = A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        self.assertEqual((len(self.lots), out['status'], out['steps']['indexation']), (A.LOTS_INDEXATION, 'partial', 'partielle'))
        self.assertFalse(A.state(self.d, 'DOS-001')['couverture']['satisfaite'])

    # ---- couverture conservée
    def test_C04_chaque_piece_et_chaque_courriel_eligible_a_un_etat_et_un_motif(self):
        lue = self.piece('Assignation DURAND.pdf')
        resumee = self.piece('Conclusions MARTIN.docx', etag='e2')
        self.piece('Scan vide.pdf', texte='')
        self.piece('Contrat protégé.pdf', texte='', erreur='pdf_protege_par_mot_de_passe')
        self.piece('Photo.heic', texte='', erreur='format_a_convertir_heic')
        self.piece('Courriel 2026-11-19.eml.txt', texte='', erreur='representation_secondaire_de_correspondance')
        self.courriel_indexe('Pièces du dossier DURAND')
        self.courriel_rattache('a', 'Pièces du dossier DURAND')                   # le même courriel, déjà indexé : une seule ligne
        self.courriel_rattache('b', 'Relance du greffe', 'greffe@example.test')   # rattaché, texte jamais récupéré
        with self.index(self.COMPLET), self.synthese([resumee]):
            out = A.analyse(self.d, {'matter': 'DOS-001'})
        self.assertEqual(out['status'], 'prepared')
        st = A.state(self.d, 'DOS-001')
        c = st['couverture']
        self.assertTrue(c['definie'] and c['satisfaite'] and c['inventaire_complet'])
        self.assertEqual({k: c['pieces'][k] for k in ('total', 'traite', 'en_attente', 'en_erreur', 'exclu', 'faits', 'modele')},
                         {'total': 6, 'traite': 2, 'en_attente': 0, 'en_erreur': 2, 'exclu': 2, 'faits': 2, 'modele': 1})
        self.assertEqual({k: c['courriels'][k] for k in ('total', 'traite', 'en_erreur')}, {'total': 2, 'traite': 1, 'en_erreur': 1})
        lignes = {x['nom']: x for x in A.couverture_liste(self.d, 'DOS-001')['elements']}
        self.assertEqual(len(lignes), 8)                                           # toute pièce et tout courriel éligible a sa ligne
        self.assertEqual((lignes['Scan vide.pdf']['etat'], lignes['Scan vide.pdf']['motif']), ('en_erreur', 'aucun_texte_lisible'))
        self.assertEqual((lignes['Contrat protégé.pdf']['etat'], lignes['Contrat protégé.pdf']['motif']), ('en_erreur', 'pdf_protege_par_mot_de_passe'))
        self.assertEqual(lignes['Photo.heic']['etat'], 'exclu')
        self.assertIn('format non pris en charge', lignes['Photo.heic']['motif_libelle'])
        self.assertEqual(lignes['Courriel 2026-11-19.eml.txt']['etat'], 'exclu')
        self.assertEqual((lignes['Relance du greffe']['etat'], lignes['Relance du greffe']['motif']), ('en_erreur', 'texte_non_recupere'))
        self.assertTrue(lignes['Assignation DURAND.pdf']['faits'] and not lignes['Assignation DURAND.pdf']['modele'])   # indexée et lue par les faits ≠ transmise au modèle
        self.assertTrue(lignes['Conclusions MARTIN.docx']['modele'])
        self.assertEqual(st['overall'], 'a_jour')
        self.assertIn('3 éléments à vérifier', st['overall_label'])                # les limites restent affichées, même « à jour »
        html = A.analysis_html('/p', 'DOS-001', st)
        self.assertIn('6 pièce(s) présente(s), 2 indexée(s), 2 lue(s) par le repérage des faits, 1 transmise(s) au modèle', html)
        self.assertIn('aucun texte lisible', html)
        self.assertEqual(A.couverture_liste(self.d, 'DOS-001', 'piece', 'en_erreur')['elements'][0]['etat_libelle'], 'à vérifier')
        self.assertTrue(lue)

    def test_C04_piece_remplacee_ou_supprimee_et_inventaire_incomplet(self):
        a = self.piece('Assignation DURAND.pdf')
        b = self.piece('Bordereau.pdf')
        with self.index(self.COMPLET), self.synthese([a, b]):
            A.analyse(self.d, {'matter': 'DOS-001'})
        self.piece('Assignation DURAND.pdf', texte='Assignation rectifiée. RG 24/01234.', etag='e9')   # pièce remplacée
        self.retirer(b)                                                             # pièce supprimée du stockage
        with self.index({'fichiers': 1, 'en_attente': 0, 'inventaire': {'complete': False}}):
            A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        lignes = {x['nom']: x for x in A.couverture_liste(self.d, 'DOS-001')['elements']}
        self.assertIn('Bordereau.pdf', lignes)                                      # inventaire incomplet : pas une preuve d'absence
        self.assertFalse(lignes['Assignation DURAND.pdf']['faits'] or lignes['Assignation DURAND.pdf']['modele'])   # nouvelle version : rien n'est réputé lu
        self.assertEqual(A.state(self.d, 'DOS-001')['overall'], 'partielle')
        with self.index(self.COMPLET):
            A.analyse(self.d, {'matter': 'DOS-001', 'steps': ['indexation']})
        self.assertNotIn('Bordereau.pdf', {x['nom'] for x in A.couverture_liste(self.d, 'DOS-001')['elements']})
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(st['overall'], 'perimee')                                  # sources changées depuis les faits et la synthèse
        self.assertEqual({s['step']: s['fresh'] for s in st['steps']}, {'indexation': True, 'faits': False, 'chronologie': False, 'synthese': False})

    # ---- révision d'entrée par étape
    def test_C04_un_fait_propose_ne_perime_rien_une_decision_humaine_perime_la_synthese(self):
        from agent import legal_memory as lm
        a = self.piece('Assignation DURAND.pdf')
        with self.index(self.COMPLET), self.synthese([a]):
            A.analyse(self.d, {'matter': 'DOS-001'})
        self.assertEqual(A.state(self.d, 'DOS-001')['overall'], 'a_jour')
        rid = lm.upsert_record(self.d, 'DOS-001', {'record_type': 'event', 'title': 'Audience de mise en état', 'content': 'Audience fixée par le greffe.',
                                                  'source_ids': ['s1'], 'confidence': 'medium'},
                               [{'id': 's1', 'kind': 'document', 'path': a, 'modified': '2026-11-20', 'excerpt': 'Audience fixée.'}])
        self.d.db.commit()
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(st['overall'], 'a_jour')                                   # un fait seulement PROPOSÉ ne périme aucune étape
        self.assertTrue(all(s['fresh'] for s in st['steps']))
        anciennes = self.d.db.execute("SELECT step, entree FROM analyses5624 WHERE matter='DOS-001'").fetchall()
        self.d.db.execute("UPDATE analyses5624 SET entree='' WHERE matter='DOS-001'")   # mutation : sans révision d'entrée (contrat 5.6.108)
        self.d.db.commit()
        self.assertEqual(A.state(self.d, 'DOS-001')['overall'], 'perimee')          # … l'ancienne révision unique périmait tout
        for r in anciennes:
            self.d.db.execute("UPDATE analyses5624 SET entree=? WHERE matter='DOS-001' AND step=?", (r['entree'], r['step']))
        self.d.db.execute("UPDATE legal_memory_records SET status='validated', revision=revision+1 WHERE id=?", (rid,))   # décision de l'avocat
        self.d.db.commit()
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(st['overall'], 'perimee')
        self.assertEqual({s['step']: s['fresh'] for s in st['steps']}, {'indexation': True, 'faits': True, 'chronologie': False, 'synthese': False})

    def test_C04_actualiser_ce_qui_a_change_ne_refait_pas_les_etapes_acquises(self):
        a = self.piece('Assignation DURAND.pdf')
        with self.index(self.COMPLET), self.synthese([a]):
            A.analyse(self.d, {'matter': 'DOS-001'})
            self.assertEqual(len(self.appels_modele), 1)
            out = A.analyse(self.d, {'matter': 'DOS-001', 'mode': 'changements'})
            self.assertEqual(len(self.appels_modele), 1)                            # rien n'a changé : aucun nouvel appel au modèle
            self.assertEqual((out['status'], sorted(out['inchangees'])), ('prepared', ['chronologie', 'faits', 'synthese']))
            self.piece('Conclusions MARTIN.docx', etag='e2')
            out = A.analyse(self.d, {'matter': 'DOS-001', 'mode': 'changements'})
            self.assertEqual((len(self.appels_modele), out['inchangees']), (2, []))  # une pièce nouvelle : tout ce qui en dépend est refait
            out = A.analyse(self.d, {'matter': 'DOS-001', 'mode': 'complete'})
            self.assertEqual((len(self.appels_modele), out['inchangees']), (3, []))  # analyse complète demandée : tout est refait
        campagnes = self.d.db.execute("SELECT numero, mode, etat FROM analyses5624_campagnes WHERE matter='DOS-001' ORDER BY numero").fetchall()
        self.assertEqual([(c['numero'], c['mode'], c['etat']) for c in campagnes],
                         [(1, 'complete', 'terminee'), (2, 'changements', 'terminee'), (3, 'changements', 'terminee'), (4, 'complete', 'terminee')])

    def test_C04_reprise_apres_suspension_ne_refait_pas_ce_qui_est_fait(self):
        a = self.piece('Assignation DURAND.pdf')
        with self.index(self.COMPLET), self.synthese([a]), patch('agent.legal_memory.sync_matter_memory', side_effect=Stop('ia_en_pause')):
            with self.assertRaisesRegex(Stop, '^ia_en_pause$'):
                A.analyse(self.d, {'matter': 'DOS-001'})
        camp = self.d.db.execute("SELECT numero, etat, bilan FROM analyses5624_campagnes WHERE matter='DOS-001'").fetchone()
        self.assertEqual((camp['numero'], camp['etat'], json.loads(camp['bilan'])['arret']), (1, 'suspendue', 'ia_en_pause'))
        with self.index(self.COMPLET), self.synthese([a]), patch('agent.facts460.scan_matter', side_effect=AssertionError('déjà fait dans cette campagne')):
            out = A.analyse(self.d, {'matter': 'DOS-001', 'campagne': 1})
        self.assertEqual((out['status'], out['campagne'], out['inchangees']), ('prepared', 1, ['faits']))
        self.assertEqual(self.d.db.execute("SELECT etat FROM analyses5624_campagnes WHERE matter='DOS-001' AND numero=1").fetchone()[0], 'terminee')

    # ---- une seule commande par dossier
    def test_C04_demandes_simultanees_une_seule_campagne_et_un_seul_travail(self):
        vrai = A._deja_demandee
        appels = []

        def course(desk, mid):
            appels.append(1)
            if len(appels) == 1:                           # la première lecture (hors transaction) ne voit rien ; une autre requête passe alors
                premier.update(A.request_analysis(desk, mid, mode='changements', par='durand@example.test'))
                return None
            return vrai(desk, mid)
        premier = {}
        with patch.object(A, '_deja_demandee', course):
            second = A.request_analysis(self.d, 'DOS-001', mode='complete')
        self.assertFalse(premier['already'])
        self.assertTrue(second['already'])
        self.assertEqual(second['job_id'], premier['job_id'])
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='analyser_dossier5624'").fetchone()[0], 1)
        camp = self.d.db.execute("SELECT numero, mode, etat, job_id, demande_par FROM analyses5624_campagnes WHERE matter='DOS-001'").fetchall()
        self.assertEqual([(c['numero'], c['mode'], c['etat'], c['job_id'], c['demande_par']) for c in camp],
                         [(1, 'changements', 'demandee', premier['job_id'], 'durand@example.test')])
        with self.assertRaises(Exception):                 # clé métier dans la base : une seconde campagne active est refusée
            self.d.db.execute("INSERT INTO analyses5624_campagnes(matter,numero,etat,cree_le,maj_le) VALUES('DOS-001',2,'en_cours','t','t')")
        self.d.db.rollback()

    def test_C04_arret_demande_et_demande_suspendue_ne_sont_pas_doublees(self):
        jid = A.request_analysis(self.d, 'DOS-001')['job_id']
        self.d.db.execute("UPDATE jobs SET status='cancel_requested', stop_reason='user' WHERE id=?", (jid,))
        self.d.db.commit()
        out = A.request_analysis(self.d, 'DOS-001')
        self.assertTrue(out['already'] and out['arret_demande'])
        self.assertEqual(out['job_id'], jid)
        self.assertTrue(A.state(self.d, 'DOS-001')['arret_demande'])
        self.d.db.execute("UPDATE jobs SET status='suspended', stop_reason='pause' WHERE id=?", (jid,))
        self.d.setting('missions567:pause', True)
        st = A.state(self.d, 'DOS-001')
        self.assertEqual((st['job'], st['attente']['job'], st['attente']['motif']), (None, jid, 'pause'))
        self.assertIn('IA en pause', st['attente']['libelle'])
        self.assertIn('en attente', A.analysis_html('/p', 'DOS-001', st))
        out = A.request_analysis(self.d, 'DOS-001')
        self.assertEqual((out['job_id'], out['already'], out['en_attente']), (jid, True, 'pause'))
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='analyser_dossier5624'").fetchone()[0], 1)

    def test_C04_migration_colonnes_ajoutees_etat_ancien_lisible(self):
        db = self.d.db
        A.ensure_schema(self.d)
        db.execute('DROP TABLE analyses5624')
        db.executescript(A.SCHEMA)
        rev = A.source_revision(self.d, 'DOS-001')
        for etape, _ in A.STEPS:
            db.execute('INSERT INTO analyses5624 VALUES(?,?,?,?,?,?,?,?)', ('DOS-001', etape, 'ok', rev, '{}', '', '2026-11-20T10:00:00', '2026-11-20T10:05:00'))
        db.commit()
        A.ensure_schema(self.d)
        self.assertTrue({'entree', 'campagne'} <= {r[1] for r in db.execute('PRAGMA table_info(analyses5624)')})
        avant = db.total_changes
        A.ensure_schema(self.d)
        st = A.state(self.d, 'DOS-001')
        self.assertEqual(db.total_changes, avant)          # sans changement : aucune écriture, y compris à la lecture de l'état (R05)
        self.assertEqual(st['overall'], 'a_jour')          # analyse d'avant la 5.6.109 : lue avec son ancienne révision, rien n'est effacé
        self.assertFalse(st['couverture']['definie'])


# ================================================================================================ C06 · relevé reprenable
class BaseReleve(unittest.TestCase):
    def setUp(self):
        self.a = Poste(self, 'Poste A', 'Me DURAND')
        self.racine = stockage_local(self, self.a)
        S.creer_espace(self.a.desk, 'Cabinet ZORVAL', self.a.personne, 'cabinet')
        self.lid, self.dir = self.dossier('Bail ZORVAL', 'ZOR-801')

    def dossier(self, titre, ref):
        lid = self.a.dossier(titre, ref)
        D.traiter_rangements(self.a.desk)
        S.partager_dossier(self.a.desk, lid)
        chemin = self.racine / D.lire(self.a.desk, ADMIN, lid)['chemin'].lstrip('/')
        self.assertTrue(chemin.is_dir())
        return lid, chemin

    def noms(self):
        return sorted(r[0] for r in self.a.desk.db.execute('SELECT nom FROM sync56108_documents'))

    def curseur(self):
        return self.a.desk.db.execute('SELECT curseur FROM sync56108_fichiers_releve WHERE id=1').fetchone()[0]

    def passer(self, n, **budget):
        bilans = []
        for _ in range(n):
            bilans.append(F.relever(self.a.desk, **budget))
        return bilans


class C06Releve(BaseReleve):
    def test_C06_plusieurs_passages_de_budget_un_atteignent_tous_les_fichiers(self):
        """Défaut reproduit par l'audit : quatre passages de budget 1 ne traitaient que le premier fichier, curseur vide."""
        for i in range(4):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d ZORVAL' % i)
        bilans = self.passer(4, budget_fichiers=1)
        self.assertEqual([b['publies'] for b in bilans], [1, 1, 1, 1])
        self.assertEqual(self.noms(), ['Note0.txt', 'Note1.txt', 'Note2.txt', 'Note3.txt'])
        self.assertTrue(self.curseur() or bilans[-1]['complet'])       # campagne en cours (curseur) ou finie
        fin = F.relever(self.a.desk, budget_fichiers=1)                # dernier passage : constate la fin du parcours
        self.assertTrue(fin['complet'] or bilans[-1]['complet'])
        self.assertEqual(self.curseur(), '')
        self.assertEqual(len(self.noms()), 4)                          # aucun doublon

    def test_C06_curseur_versionne_et_sous_repertoires(self):
        (self.dir / 'Pieces').mkdir()
        (self.dir / 'Pieces' / 'Sous').mkdir()
        for nom in ('A.txt', 'Pieces/B.txt', 'Pieces/Sous/C.txt', 'Z.txt'):
            (self.dir / nom).write_bytes(nom.encode())
        b1 = F.relever(self.a.desk, budget_fichiers=1)
        self.assertFalse(b1['complet'])
        c = json.loads(self.curseur())
        self.assertEqual(c['v'], R.CURSEUR_VERSION)
        self.assertTrue(c['pile'])
        self.assertTrue(F.releve_du(self.a.desk))                      # campagne en cours : le passage suivant reprend aussitôt
        for _ in range(8):
            if F.relever(self.a.desk, budget_fichiers=1)['complet']:
                break
        self.assertEqual(self.noms(), ['A.txt', 'B.txt', 'C.txt', 'Z.txt'])
        self.assertEqual(self.curseur(), '')

    def test_C06_interruption_garde_l_ancien_curseur_et_le_rejeu_ne_duplique_rien(self):
        for i in range(3):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d' % i)
        F.relever(self.a.desk, budget_fichiers=1)
        avant = self.curseur()
        self.assertTrue(avant)
        vrai = F._noter_vu
        appels = []

        def coupure(*a, **k):                                          # coupure juste après la publication, avant la marque « vu »
            appels.append(1)
            raise RuntimeError('coupure fictive')
        with patch.object(F, '_noter_vu', coupure), self.assertRaises(RuntimeError):
            F.relever(self.a.desk, budget_fichiers=1)
        self.assertTrue(appels)
        self.assertEqual(self.curseur(), avant)                        # l'ancien curseur est gardé
        self.assertEqual(len(self.noms()), 2)                          # Note1 a bien été publiée avant la coupure
        self.assertIs(F._noter_vu, vrai)
        for _ in range(4):
            if F.relever(self.a.desk, budget_fichiers=1)['complet']:
                break
        self.assertEqual(self.noms(), ['Note0.txt', 'Note1.txt', 'Note2.txt'])   # rejeu : Note1 reconnue, jamais un second document
        objets = [r[0] for r in self.a.desk.db.execute('SELECT objet FROM sync56108_documents')]
        self.assertEqual(len(objets), len(set(objets)))

    def test_C06_mutation_sans_reconnaissance_au_rejeu_un_doublon_apparait(self):
        """Garde-fou : ``F._deja_publie`` (rejeu reconnu par dossier, empreinte et tête). Mutation en mémoire : le rejeu publie un second
        document pour le même fichier — exactement le doublon que le test précédent interdit."""
        for i in range(2):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d' % i)
        F.relever(self.a.desk, budget_fichiers=1)

        def coupure(*a, **k):
            raise RuntimeError('coupure fictive')
        with patch.object(F, '_noter_vu', coupure), self.assertRaises(RuntimeError):
            F.relever(self.a.desk, budget_fichiers=1)
        if self.a.desk.db.in_transaction:
            self.a.desk.db.rollback()
        with patch.object(F, '_deja_publie', lambda *a, **k: None):
            F.relever(self.a.desk, budget_fichiers=1)
        self.assertEqual(self.noms(), ['Note0.txt', 'Note1.txt', 'Note1.txt'])

    def test_C06_mutation_position_non_revalidee_jamais_un_acces(self):
        """Second verrou : même si la validation des positions était retirée (mutation en mémoire), la position d'un dossier retiré du
        partage ne donne aucun accès — le périmètre relu ne le contient pas (erreur franche, jamais une lecture)."""
        (self.dir / 'Note.txt').write_bytes(b'note')
        chemin = D.lire(self.a.desk, ADMIN, self.lid)['chemin']
        self.a.desk.db.execute('UPDATE sync56108_fichiers_releve SET curseur=?, dernier=0 WHERE id=1', (json.dumps(
            {'v': R.CURSEUR_VERSION, 'pile': [{'dossier': self.lid, 'chemin': chemin, 'prof': 0, 'apres': ''}], 'faits': []}),))
        self.a.desk.db.execute("UPDATE sync56108_objets SET bloque=1 WHERE genre='dossier' AND local_id=?", (self.lid,))
        self.a.desk.db.commit()
        client = F._stockage(self.a.desk)
        with patch.object(R, '_position_valable', lambda *a: True), patch.object(type(client), 'list_folder', side_effect=AssertionError('jamais lu')), \
                self.assertRaises(KeyError):
            F.relever(self.a.desk, client=client)
        self.assertEqual(self.noms(), [])
        self.assertTrue(F.relever(self.a.desk)['complet'])            # sans mutation : position retirée, campagne finie, rien de lu
        self.assertEqual(self.noms(), [])

    def test_C06_arret_central_pendant_le_releve_remonte_et_garde_le_curseur(self):
        for i in range(3):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d' % i)
        F.relever(self.a.desk, budget_fichiers=1)
        avant = self.curseur()
        with patch('agent.pause5629.checkpoint', side_effect=Stop('operation_annulee')), self.assertRaisesRegex(Stop, '^operation_annulee$'):
            F.relever(self.a.desk, budget_fichiers=5)
        self.assertEqual(self.curseur(), avant)
        self.assertEqual(self.noms(), ['Note0.txt'])                   # aucun nouvel effet après l'arrêt

    def test_C06_revocation_retire_les_positions_sans_keyerror_ni_acces(self):
        lid2, dir2 = self.dossier('Cession KERVILAC', 'KER-802')
        for d in (self.dir, dir2):
            for i in range(2):
                (d / ('Acte%d.txt' % i)).write_bytes(('%s %d' % (d.name, i)).encode())
        F.relever(self.a.desk, budget_fichiers=1)
        c = json.loads(self.curseur())
        self.assertEqual({f['dossier'] for f in c['pile']}, {self.lid, lid2})
        retire = lid2 if c['pile'][0]['dossier'] == self.lid else self.lid   # le dossier pas encore parcouru est retiré du partage
        garde = self.lid if retire == lid2 else lid2
        self.a.desk.db.execute("UPDATE sync56108_objets SET bloque=1, motif='devenu_reserve' WHERE genre='dossier' AND local_id=?", (retire,))
        self.a.desk.db.commit()
        listes = []
        client = F._stockage(self.a.desk)
        vrai = client.list_folder
        with patch.object(type(client), 'list_folder', lambda s, p: listes.append(p) or vrai(p)):
            for _ in range(6):
                if F.relever(self.a.desk, budget_fichiers=1, client=client)['complet']:
                    break
        chemin_retire = D.lire(self.a.desk, ADMIN, retire)['chemin']
        self.assertFalse([p for p in listes if p.startswith(chemin_retire)])       # jamais relu après la révocation
        gid_retire = self.a.objet('dossier', retire)['objet']
        self.assertFalse(self.a.desk.db.execute('SELECT 1 FROM sync56108_documents WHERE dossier=?', (gid_retire,)).fetchone())
        gid_garde = self.a.objet('dossier', garde)['objet']
        self.assertEqual(self.a.desk.db.execute('SELECT COUNT(*) FROM sync56108_documents WHERE dossier=?', (gid_garde,)).fetchone()[0], 2)
        self.assertEqual(self.curseur(), '')

    def test_C06_curseur_hors_dossier_ou_incompatible_jamais_suivi(self):
        (self.dir / 'Note.txt').write_bytes(b'note')
        ailleurs = self.racine / 'Dossiers' / 'AILLEURS'
        ailleurs.mkdir()
        (ailleurs / 'Secret.txt').write_bytes(b'hors du dossier partage')
        for brut in (json.dumps({'v': R.CURSEUR_VERSION, 'pile': [{'dossier': self.lid, 'chemin': '/Dossiers/AILLEURS', 'prof': 0, 'apres': ''}], 'faits': []}),
                     json.dumps({'v': R.CURSEUR_VERSION, 'pile': [{'dossier': 'inconnu', 'chemin': '/Dossiers/AILLEURS', 'prof': 0, 'apres': ''}], 'faits': []}),
                     json.dumps({'v': 99, 'pile': [{'dossier': self.lid, 'chemin': '/Dossiers/AILLEURS', 'prof': 0, 'apres': ''}]}),
                     'pas du json', '[]'):
            with self.subTest(curseur=brut[:30]):
                self.a.desk.db.execute('UPDATE sync56108_fichiers_releve SET curseur=?, dernier=0 WHERE id=1', (brut,))
                self.a.desk.db.execute('DELETE FROM sync56108_documents')
                self.a.desk.db.execute('DELETE FROM sync56108_fichiers_vus')
                self.a.desk.db.commit()
                for _ in range(4):
                    if F.relever(self.a.desk)['complet']:
                        break
                self.assertEqual(self.noms(), ['Note.txt'])            # le dossier partagé est relu depuis sa racine ; rien d'ailleurs
                self.assertEqual(self.curseur(), '')

    def test_C06_erreur_de_lecture_n_est_jamais_une_reussite_complete(self):
        (self.dir / 'Sous').mkdir()
        (self.dir / 'Sous' / 'B.txt').write_bytes(b'b')
        (self.dir / 'A.txt').write_bytes(b'a')
        client = F._stockage(self.a.desk)
        vrai = client.list_folder

        def panne(s, chemin):
            if chemin.endswith('/Sous'):
                raise Stop('stockage_injoignable')
            return vrai(chemin)
        with patch.object(type(client), 'list_folder', panne):
            bilan = F.relever(self.a.desk, maintenant=1000.0, client=client)
        self.assertFalse(bilan['complet'])
        self.assertEqual([e['motif'] for e in bilan['erreurs']], ['stockage_injoignable'])
        etat = F.etat_releve(self.a.desk)
        self.assertEqual(etat['reussite'], 0)                          # aucune réussite complète enregistrée
        self.assertEqual(etat['bilan']['publies'], 1)                  # le bilan partiel est conservé à part
        self.assertFalse(etat['bilan']['complet'])
        self.assertFalse(F.releve_du(self.a.desk, 1001.0))             # pas de martèlement d'un stockage en panne
        self.assertTrue(F.releve_du(self.a.desk, 1000.0 + F.RELEVE_INTERVALLE))
        bilan = F.relever(self.a.desk, maintenant=2000.0)              # stockage revenu : le sous-répertoire est repris
        self.assertTrue(bilan['complet'])
        self.assertEqual(self.noms(), ['A.txt', 'B.txt'])
        self.assertEqual(F.etat_releve(self.a.desk)['reussite'], 2000.0)

    def test_C06_fichier_ajoute_avant_la_cle_courante_repris_au_cycle_suivant(self):
        for nom in ('B.txt', 'C.txt'):
            (self.dir / nom).write_bytes(nom.encode())
        F.relever(self.a.desk, budget_fichiers=1)                      # B publié, curseur après B
        (self.dir / 'A.txt').write_bytes(b'ajoute avant la cle courante')
        for _ in range(4):
            if F.relever(self.a.desk, budget_fichiers=1)['complet']:
                break
        self.assertEqual(self.noms(), ['B.txt', 'C.txt'])              # ce cycle ne revient pas en arrière (parcours borné)
        self.a.desk.db.execute('UPDATE sync56108_fichiers_releve SET dernier=0')
        self.a.desk.db.commit()
        for _ in range(6):
            if F.relever(self.a.desk, budget_fichiers=1)['complet']:
                break
        self.assertEqual(self.noms(), ['A.txt', 'B.txt', 'C.txt'])

    def test_C06_fichiers_inchanges_comptes_le_parcours_reste_borne(self):
        """Un cycle suivant sur des fichiers inchangés avance par lots bornés (pas de parcours illimité « parce que rien n'a changé »)."""
        for i in range(5):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d' % i)
        self.assertTrue(F.relever(self.a.desk)['complet'])
        self.a.desk.db.execute('UPDATE sync56108_fichiers_releve SET dernier=0')
        self.a.desk.db.commit()
        b = F.relever(self.a.desk, budget_fichiers=2)
        self.assertEqual((b['examines'], b['publies'], b['complet']), (2, 0, False))
        examines = 2
        for _ in range(5):
            b = F.relever(self.a.desk, budget_fichiers=2)
            examines += b['examines']
            if b['complet']:
                break
        self.assertEqual(examines, 5)
        self.assertEqual(len(self.noms()), 5)

    def test_C06_pause_du_cabinet_garde_la_campagne(self):
        for i in range(3):
            (self.dir / ('Note%d.txt' % i)).write_bytes(b'note %d' % i)
        F.relever(self.a.desk, budget_fichiers=1)
        avant = self.curseur()
        M.pause_cabinet(self.a.desk, True, 'audit')
        self.assertEqual(F.relever(self.a.desk)['examines'], 0)
        self.assertEqual(self.curseur(), avant)
        M.pause_cabinet(self.a.desk, False)
        self.assertEqual(F.relever(self.a.desk)['publies'], 2)

    def test_C06_migration_colonnes_ajoutees_sans_perdre_le_releve(self):
        """Base 5.6.108 : la table du relevé n'a que ``dernier`` et ``curseur`` ; la migration ajoute les colonnes, garde la ligne."""
        db = self.a.desk.db
        db.execute('DROP TABLE sync56108_fichiers_releve')
        db.execute("CREATE TABLE sync56108_fichiers_releve(id INTEGER PRIMARY KEY CHECK(id=1), dernier REAL NOT NULL DEFAULT 0, curseur TEXT NOT NULL DEFAULT '')")
        db.execute("INSERT INTO sync56108_fichiers_releve(id, dernier, curseur) VALUES(1, 1234.5, '')")
        db.commit()
        F.ensure_schema_s5(self.a.desk)
        F.ensure_schema_s5(self.a.desk)                                # idempotente
        etat = F.etat_releve(self.a.desk)
        self.assertEqual(etat['reussite'], 1234.5)                     # un ancien relevé complet reste une réussite
        avant = db.total_changes
        F.ensure_schema_s5(self.a.desk)
        self.assertEqual(db.total_changes, avant)                      # sans changement : aucune écriture au démarrage

    def test_C07_depot_au_dossier_reponse_perdue_jamais_un_second_fichier(self):
        """Même défaut que C07, dans le dépôt d'une version reçue : fichier écrit, réponse perdue ; l'essai suivant écrivait « … (2) »."""
        src = self.racine / 'acte.bin'
        src.write_bytes(b'conclusions ZORVAL recues d un autre poste')
        out = F.publier(self.a.desk, str(src), 'Conclusions ZORVAL.docx', self.a.objet('dossier', self.lid)['objet'])
        client = F._stockage(self.a.desk)
        vrai = type(client).put_file
        appels = []

        def put_file(c, chemin, donnees, *a):
            appels.append(chemin)
            vrai(c, chemin, donnees, *a)
            if len(appels) == 1:
                raise Stop('delai_http_depasse')                       # écrit, mais la réponse du stockage est perdue
        with patch.object(type(client), 'put_file', put_file):
            self.assertEqual(F.deposer_au_dossier(self.a.desk, out['objet'], out['version'], client), '')
            self.assertEqual(F.deposer_au_dossier(self.a.desk, out['objet'], out['version'], client), 'Conclusions ZORVAL.docx')
        self.assertEqual(len(appels), 1)                               # constaté sur le fichier attendu, jamais réécrit sous un autre nom
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.is_file()), ['Conclusions ZORVAL.docx'])
        ligne = self.a.desk.db.execute('SELECT dossier_chemin FROM sync56108_documents WHERE objet=?', (out['objet'],)).fetchone()
        self.assertTrue(ligne[0].endswith('/Conclusions ZORVAL.docx'))
        self.assertEqual(F.relever(self.a.desk)['publies'], 0)         # le fichier déposé est « vu » : aucun écho

    def test_C07_depot_au_dossier_autre_fichier_du_meme_nom_jamais_adopte(self):
        (self.dir / 'Conclusions ZORVAL.docx').write_bytes(b'un autre document, du meme nom')
        src = self.racine / 'acte.bin'
        src.write_bytes(b'conclusions ZORVAL recues d un autre poste')
        out = F.publier(self.a.desk, str(src), 'Conclusions ZORVAL.docx', self.a.objet('dossier', self.lid)['objet'])
        self.assertEqual(F.deposer_au_dossier(self.a.desk, out['objet'], out['version']), 'Conclusions ZORVAL (2).docx')
        self.assertEqual((self.dir / 'Conclusions ZORVAL.docx').read_bytes(), b'un autre document, du meme nom')   # jamais écrasé, jamais pris pour le nôtre

    def test_C06_depot_volumineux_refuse_avant_tout_chargement_en_memoire(self):
        """Dépôt au dossier d'une version reçue : la limite s'applique avant de lire le fichier (jusqu'à 2 Go annoncés)."""
        src = self.racine / 'gros.bin'
        src.write_bytes(b'x' * 4096)
        out = F.publier(self.a.desk, str(src), 'Gros fichier.bin', self.a.objet('dossier', self.lid)['objet'])
        client = F._stockage(self.a.desk)
        client.cfg['max_generated_file_bytes'] = 1000
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('le fichier ne doit pas être chargé en mémoire')):
            self.assertEqual(F.deposer_au_dossier(self.a.desk, out['objet'], out['version'], client), '')
        motif = self.a.desk.db.execute('SELECT motif FROM sync56108_documents WHERE objet=?', (out['objet'],)).fetchone()[0]
        self.assertEqual(motif, 'fichier_trop_volumineux_pour_le_depot')


# ================================================================================================ C07 · écritures à résultat incertain
MARQUE = '(modifié dans WordCraft'


class C07Ecritures(BaseEditeur):
    """Service réel (``wordcraft56108``), faux stockage WebDAV en mémoire ; les pannes sont posées sur le transport du faux stockage."""

    def setUp(self):
        super().setUp()
        self.s = self.ouvrir()
        self.p = WC.proposer(self.desk, self.ident(), DURAND, self.s['seance'], 0, self.modifie(self.original), self.src())
        self.n_cle = 0

    def versions(self):
        return [f for f in self.fichiers_demo() if MARQUE in f]

    def garder(self, **extra):
        self.n_cle += 1
        return self.api('decider', {'seance': self.s['seance'], 'revision': self.p['revision'], 'decision': 'garder',
                                    'cle': 'cle-c07-%05d-%d' % (self.n_cle, time.time_ns() % 100000), **extra})

    def panne(self, methode, fois=1, applique=False, code='delai_http_depasse', filtre=MARQUE):
        """Le transport échoue ``fois`` fois pour cette méthode sur la nouvelle version ; ``applique`` : l'effet a lieu, la réponse est perdue."""
        vrai = self.nc.request
        etat = {'reste': fois, 'appels': 0}

        def request(method, url, data=None, headers=None, limit=12_000_000):
            if method == methode and filtre in urllib.parse.unquote(url):
                etat['appels'] += 1
                if etat['reste'] > 0:
                    etat['reste'] -= 1
                    if applique:
                        vrai(method, url, data, headers, limit)
                    raise Stop(code)
            return vrai(method, url, data, headers, limit)
        self.nc.request = request
        self.addCleanup(lambda: self.nc.__dict__.pop('request', None))
        return etat

    def op(self):
        row = self.desk.db.execute("SELECT * FROM editeur56108_ops WHERE decision<>'' ORDER BY cree_le DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def candidat(self):
        return WC.dossier_seance(self.desk, self.s['seance']) / ('candidat-%d.docx' % self.p['revision'])

    # ---------------------------------------------------------------------------------------- le défaut reproduit par l'audit
    def test_C07_fichier_cree_relecture_en_panne_deuxieme_clic_sans_deuxieme_fichier(self):
        """Avant : fichier créé, relecture en panne, reçu « échec », puis DEUXIÈME fichier au deuxième clic avec une nouvelle clé."""
        self.panne('GET')                                               # la relecture de contrôle échoue une fois
        r1 = self.garder()
        self.assertTrue(r1['status'].startswith('202'), r1['body'])      # ni succès, ni échec : résultat à vérifier
        self.assertEqual(r1['json']['etat'], 'a_verifier')
        self.assertNotIn('error', r1['json'])
        self.assertIn('À vérifier', r1['json']['message'])
        self.assertEqual(len(self.versions()), 1)                        # le fichier existe bien
        self.assertTrue(self.candidat().is_file())                       # candidat conservé tant que rien n'est résolu
        cible = r1['json']['chemin']
        r2 = self.garder()                                               # deuxième clic, NOUVELLE clé de transport
        self.assertTrue(r2['status'].startswith('200'), r2['body'])
        self.assertEqual(r2['json']['etat'], 'verifie')
        self.assertEqual(r2['json']['chemin'], cible)                    # preuve sur la MÊME cible
        self.assertEqual(self.versions(), [cible])                       # jamais un second fichier
        self.assertEqual(hashlib.sha256(self.nc.lire(cible)).hexdigest(), self.p['sha256'])
        self.assertFalse(self.candidat().exists())                       # résolu : candidat retiré
        r3 = self.garder()                                               # troisième clé : le même reçu
        self.assertTrue(r3['json'].get('rejoue'))
        self.assertEqual(r3['json']['chemin'], cible)
        self.assertEqual(self.versions(), [cible])
        self.assertEqual(self.nc.lire(DOC), self.original)

    def test_C07_mutation_sans_identite_de_decision_le_doublon_revient(self):
        """Garde-fou : l'identité durable de la décision (colonne ``decision``, index unique). Mutation sur la base d'essai : identité
        effacée, le deuxième clic choisit une autre cible et crée un second fichier — le défaut d'origine."""
        self.panne('GET')
        self.assertEqual(self.garder()['json']['etat'], 'a_verifier')
        self.desk.db.execute("UPDATE editeur56108_ops SET decision='', rapprochement='' WHERE decision<>''")
        self.desk.db.commit()
        with patch.object(FI, 'horodatage', lambda desk: '31-12-2027 23h59'):   # plus tard : sans cible figée, le nom horodaté change
            self.garder()
        self.assertEqual(len(self.versions()), 2)

    def test_C07_reponse_perdue_apres_creation_constat_sur_la_cible(self):
        e = self.panne('PUT', applique=True)                             # fichier créé, réponse du stockage perdue
        r = self.garder()
        self.assertTrue(r['status'].startswith('200'), r['body'])        # relu aussitôt sur la cible réservée : conforme
        self.assertEqual(r['json']['etat'], 'verifie')
        self.assertEqual(self.versions(), [r['json']['chemin']])
        self.assertEqual(e['appels'], 1)                                 # un seul effet

    def test_C07_reponse_et_relecture_perdues_puis_verifier(self):
        e = self.panne('PUT', applique=True)
        self.panne('GET')
        r1 = self.garder()
        self.assertEqual((r1['status'][:3], r1['json']['etat']), ('202', 'a_verifier'))
        r2 = self.garder()
        self.assertEqual(r2['json']['etat'], 'verifie')
        self.assertEqual(self.versions(), [r1['json']['chemin']])
        self.assertEqual(e['appels'], 1)                                 # « Vérifier » ne recrée jamais

    def test_C07_creation_non_aboutie_reessai_explicite_sur_la_meme_cible(self):
        e = self.panne('PUT')                                            # délai dépassé AVANT l'effet : rien n'est écrit, mais on ne le sait pas
        r1 = self.garder()
        self.assertEqual((r1['status'][:3], r1['json']['etat'], r1['json']['motif']), ('202', 'a_verifier', 'cible_absente'))
        self.assertTrue(r1['json']['reessayable'])
        self.assertEqual(self.versions(), [])
        cible = r1['json']['chemin']
        r2 = self.garder()                                               # « Vérifier » : lecture seule de la cible, aucune création
        self.assertEqual((r2['json']['etat'], r2['json']['chemin']), ('a_verifier', cible))
        self.assertEqual(e['appels'], 1)
        self.assertEqual(self.versions(), [])
        with patch.object(FI, 'horodatage', lambda desk: '31-12-2027 23h59'):   # même bien plus tard : la cible figée ne change pas
            r3 = self.garder(reessayer=True)                             # « Réessayer l'écriture » : même nom, création exclusive
        self.assertEqual((r3['status'][:3], r3['json']['etat'], r3['json']['chemin']), ('200', 'verifie', cible))
        self.assertEqual(self.versions(), [cible])

    def test_C07_cible_differente_a_verifier_jamais_une_nouvelle_cible(self):
        self.panne('PUT')
        r1 = self.garder()
        cible = r1['json']['chemin']
        self.nc.put(cible, b'fichier d un autre poste, autre contenu')   # entre-temps : un autre contenu porte ce nom
        for extra in ({}, {'reessayer': True}):
            r = self.garder(**extra)
            self.assertEqual((r['status'][:3], r['json']['etat'], r['json']['motif']), ('202', 'a_verifier', 'cible_differente'), extra)
            self.assertFalse(r['json']['reessayable'])
        self.assertEqual(self.versions(), [cible])                       # aucune nouvelle cible automatique, rien d'écrasé
        self.assertEqual(self.nc.lire(cible), b'fichier d un autre poste, autre contenu')
        self.assertTrue(self.candidat().is_file())

    def test_C07_candidat_conserve_nouvel_enregistrement_et_fermeture_retenus_puis_abandon(self):
        self.panne('PUT')
        r1 = self.garder()
        self.assertEqual(r1['json']['etat'], 'a_verifier')
        with self.assertRaisesRegex(Stop, '^ecriture_a_verifier$'):      # pas de nouveau candidat (donc pas de nouvelle cible) tant que c'est incertain
            WC.proposer(self.desk, self.ident(), DURAND, self.s['seance'], self.p['revision'], self.modifie(self.original, 'deux '), self.src())
        r = self.api('fermer', {'seance': self.s['seance'], 'cle': 'cle-c07-fermer-0001'})
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'ecriture_a_verifier'))
        self.assertTrue(self.candidat().is_file())
        r = self.api('decider', {'seance': self.s['seance'], 'revision': self.p['revision'], 'decision': 'abandonner', 'cle': 'cle-c07-abandon-0001'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertIn(PurePosixPathName(r1['json']['chemin']), r['json']['message'])      # l'abandon dit quel fichier contrôler
        self.assertFalse(self.candidat().exists())
        self.assertEqual(self.op()['rapprochement'], 'abandonnee')
        r = self.garder()
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'version_perimee'))
        self.assertEqual(self.versions(), [])
        WC.fermer(self.desk, DURAND, self.s['seance'])                   # résolu : la séance se ferme de nouveau

    def test_C07_cible_reservee_avant_l_effet_et_bail_entre_deux_requetes(self):
        """Coupure entre la réservation et l'effet : la cible est déjà durable ; une seconde requête immédiate attend (409), puis reprend la
        MÊME cible une fois le bail échu ; deux clés de transport ne font jamais deux versions de la même décision."""
        vu = {}

        def coupure(desk, auth, acteur, s, op, data, sources_, premiere=False):
            vu.update(op)
            raise RuntimeError('coupure fictive avant l effet')
        with patch.object(WC, '_engager', coupure), self.assertRaises(RuntimeError):
            WC.decider(self.desk, self.ident(), DURAND, self.s['seance'], self.p['revision'], 'garder', False, self.src())
        op = self.op()
        self.assertEqual((op['rapprochement'], op['cible'], op['candidat_sha'], op['revision']), ('reservee', vu['cible'], self.p['sha256'], self.p['revision']))
        self.assertEqual(self.versions(), [])
        r = self.garder()                                                # requête concurrente ou rejeu immédiat
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'operation_en_cours'))
        self.assertEqual(self.versions(), [])
        with self.assertRaises(Exception):                               # unicité de la décision : une seconde ligne est refusée par la base
            self.desk.db.execute("INSERT INTO editeur56108_ops(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le,decision) "
                                 "VALUES('x','cle-doublon-0001','decider','e','en_cours','{}','t','t',?)", (op['decision'],))
        self.desk.db.rollback()
        with patch.object(WC, 'BAIL_ECRITURE', 0), patch.object(FI, 'horodatage', lambda desk: '31-12-2027 23h59'):
            r = self.garder()
        self.assertEqual((r['status'][:3], r['json']['etat'], r['json']['chemin']), ('200', 'verifie', vu['cible']))
        self.assertEqual(self.versions(), [vu['cible']])

    def test_C07_coupure_apres_l_effet_puis_rapprochement(self):
        vraie = FI.SourceNextcloud.ecrire

        def ecrire(source, chemin, octets, etag=None):
            vraie(source, chemin, octets, etag)
            raise RuntimeError('processus interrompu après l écriture')
        with patch.object(FI.SourceNextcloud, 'ecrire', ecrire), patch.object(WC, '_constater', side_effect=RuntimeError('interrompu')), self.assertRaises(RuntimeError):
            WC.decider(self.desk, self.ident(), DURAND, self.s['seance'], self.p['revision'], 'garder', False, self.src())
        self.assertIn(self.op()['rapprochement'], ('engagee', 'a_verifier'))   # le fait « un effet a pu être accepté » est conservé
        self.assertEqual(len(self.versions()), 1)
        with patch.object(WC, 'BAIL_ECRITURE', 0):
            r = self.garder()
        self.assertEqual(r['json']['etat'], 'verifie')
        self.assertEqual(self.versions(), [r['json']['chemin']])

    # ---------------------------------------------------------------------------------------- droits, compte, arrêts
    def test_C07_droits_relus_avant_l_effet(self):
        avant = len(self.nc.journal)
        with patch.object(FI.SourceNextcloud, '_caches', return_value=['/Dossiers/DEMO']):   # dossier réservé depuis l'enregistrement
            r = self.garder()
        self.assertEqual((r['status'][:3], r['json']['error']), ('404', 'introuvable'))
        self.assertEqual(len(self.nc.journal), avant)                    # aucun accès au stockage
        self.assertIsNone(self.op())                                     # rien de réservé
        self.assertTrue(self.candidat().is_file())

    def test_C07_droits_relus_avant_la_lecture_de_rapprochement(self):
        self.panne('PUT', applique=True)
        self.panne('GET')
        self.assertEqual(self.garder()['json']['etat'], 'a_verifier')
        avant = len(self.nc.journal)
        with patch.object(FI.SourceNextcloud, '_caches', return_value=['/Dossiers/DEMO']):
            r = self.garder()
        self.assertEqual((r['status'][:3], r['json']['error']), ('404', 'introuvable'))      # même 404 neutre
        self.assertEqual(len(self.nc.journal), avant)                    # la cible n'est pas relue pour un compte qui n'y a plus droit
        self.assertEqual(self.op()['rapprochement'], 'a_verifier')       # l'état incertain est gardé pour qui y a droit
        r = self.api('decider', {'seance': self.s['seance'], 'revision': self.p['revision'], 'decision': 'garder', 'cle': 'cle-c07-autre-0001'},
                     acteur=('bernard@example.test', 'assistant'))       # un autre compte : séance d'autrui, même 404
        self.assertTrue(r['status'].startswith('404'))

    def test_C07_mutation_sans_relecture_des_droits_la_cible_serait_lue(self):
        self.panne('PUT', applique=True)
        self.panne('GET')
        self.assertEqual(self.garder()['json']['etat'], 'a_verifier')
        avant = len(self.nc.journal)
        vrai = FI.SourceNextcloud.autoriser
        with patch.object(FI.SourceNextcloud, '_caches', return_value=['/Dossiers/DEMO']), \
                patch.object(FI.SourceNextcloud, 'autoriser', lambda source, chemin, acteur: chemin):
            self.garder()
        self.assertGreater(len(self.nc.journal), avant)                  # sans le contrôle, la cible est lue : c'est bien lui qui protège
        self.assertIs(FI.SourceNextcloud.autoriser, vrai)

    def test_C07_arret_central_avant_l_effet_jamais_absorbe_ni_bloquant(self):
        for code in ('operation_annulee', 'ia_en_pause', 'dossier_reserve'):
            with self.subTest(code=code):
                with patch('agent.pause5629.checkpoint', side_effect=Stop(code)):
                    r = self.garder()
                self.assertEqual(r['json']['error'], 'introuvable' if code == 'dossier_reserve' else code)
                self.assertNotEqual(r['json'].get('etat'), 'a_verifier')  # ni « à vérifier », ni reçu : l'arrêt est dit tel quel
                self.assertEqual(self.versions(), [])                    # aucun effet après l'arrêt
                self.assertTrue(self.candidat().is_file())
        r = self.garder()                                                # arrêt levé : la décision reprend, sur la cible déjà réservée
        self.assertEqual((r['status'][:3], r['json']['etat']), ('200', 'verifie'))
        self.assertEqual(len(self.versions()), 1)

    def test_C07_arret_central_pendant_l_effet_remonte_et_l_etat_garde_le_fait(self):
        vraie = FI.SourceNextcloud.ecrire

        def ecrire(source, chemin, octets, etag=None):
            vraie(source, chemin, octets, etag)
            raise Stop('controle_acces_indisponible')                    # arrêt levé alors que l'écriture a déjà pu être acceptée
        with patch.object(FI.SourceNextcloud, 'ecrire', ecrire):
            r = self.garder()
        self.assertEqual((r['status'][:3], r['json']['error']), ('503', 'controle_acces_indisponible'))   # pas une « erreur réseau », pas un reçu
        self.assertEqual(self.op()['rapprochement'], 'a_verifier')
        self.assertEqual(len(self.versions()), 1)
        r = self.garder()
        self.assertEqual(r['json']['etat'], 'verifie')
        self.assertEqual(len(self.versions()), 1)

    def test_C07_refus_certain_du_stockage_reste_un_echec_franc(self):
        """Refus AVANT tout effet (nom déjà pris par un autre fichier : cible suivante ; stockage refusant l'écriture : échec) : pas d'état incertain."""
        with patch.object(FI.SourceNextcloud, 'ecrire', side_effect=Stop('fichier_trop_volumineux')):
            r = self.garder()
        self.assertEqual(r['json']['error'], 'fichier_trop_volumineux')
        self.assertIsNone(self.op())                                     # décision libérée : rien n'a été engagé
        r = self.garder()
        self.assertEqual(r['json']['etat'], 'verifie')
        self.assertEqual(len(self.versions()), 1)

    # ---------------------------------------------------------------------------------------- stockage, expiration, page
    def test_C07_migration_colonnes_et_unicite_sans_perdre_les_recus(self):
        db = self.desk.db
        db.execute('DROP TABLE editeur56108_ops')
        db.execute("CREATE TABLE editeur56108_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL, etat TEXT NOT NULL, "
                   "resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle))")
        db.execute("INSERT INTO editeur56108_ops VALUES('durand@example.test','cle-ancienne-0001','decider','e1','en_cours','{}','2026-11-20T10:00:00+00:00','2026-11-20T10:00:00+00:00')")
        db.execute("INSERT INTO editeur56108_ops VALUES('durand@example.test','cle-ancienne-0002','decider','e2','fait',?,'2026-11-20T10:00:00+00:00','2026-11-20T10:00:00+00:00')",
                   (json.dumps({'chemin': '/Dossiers/DEMO/Ancien.docx'}),))
        db.commit()
        WC.ensure_schema(self.desk)
        WC.ensure_schema(self.desk)
        cols = {r[1] for r in db.execute('PRAGMA table_info(editeur56108_ops)')}
        self.assertTrue({'decision', 'seance', 'revision', 'cible', 'candidat_sha', 'rapprochement', 'motif'} <= cols)
        self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='editeur56108_ops_decision'").fetchone())
        etats = dict(db.execute('SELECT cle, etat FROM editeur56108_ops'))
        self.assertEqual(etats, {'cle-ancienne-0001': 'en_cours', 'cle-ancienne-0002': 'fait'})   # une ancienne opération incertaine ne devient pas un échec
        avant = db.total_changes
        WC.ensure_schema(self.desk)
        self.assertEqual(db.total_changes, avant)
        r = self.garder()                                                # le service fonctionne sur la table migrée
        self.assertEqual(r['json']['etat'], 'verifie')

    def test_C07_expiration_expliquee_par_la_maintenance(self):
        self.panne('PUT')
        r1 = self.garder()
        self.assertEqual(r1['json']['etat'], 'a_verifier')
        self.assertEqual(WC.maintenance(self.desk), 0)                    # séance récente : candidat conservé
        self.assertTrue(self.candidat().is_file())
        WC.maintenance(self.desk, maintenant=time.time() + (WC.SEANCE_JOURS + 1) * 86400)
        self.assertFalse(self.candidat().exists())
        op = self.op()
        self.assertEqual(op['rapprochement'], 'expiree')
        self.assertIn(r1['json']['nom'], json.loads(op['resultat'])['message'])
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM audit WHERE action='editeur56108_ecriture_expiree'").fetchone())

    def test_C07_page_montre_l_ecriture_a_verifier_apres_rechargement(self):
        self.panne('PUT')
        r1 = self.garder()
        avant = self.desk.db.total_changes
        out = WC.page_route({'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1]}, self.desk.c, self.ident(), '/agent-courriel',
                            '/editeur/wordcraft', {'s': 'nextcloud', 'p': DOC}, lambda t, c, *a, **k: c, self.src())
        self.assertEqual(self.desk.db.total_changes, avant)               # R05 : la page n'écrit rien
        corps = out['body']
        self.assertIn('À vérifier', corps)
        self.assertIn(r1['json']['nom'].replace('&', '&amp;'), corps)
        self.assertIn('data-w108="verifier"', corps)
        self.assertIn('data-w108-seance="%s"' % self.s['seance'], corps)
        autre = WC.page_route({'HTTP_X_AXIORHUB_USER': 'bernard@example.test', 'HTTP_X_AXIORHUB_ROLE': 'assistant'}, self.desk.c, self.ident(), '/agent-courriel',
                              '/editeur/wordcraft', {'s': 'nextcloud', 'p': DOC}, lambda t, c, *a, **k: c, self.src())['body']
        self.assertNotIn('data-w108="verifier"', autre)                   # l'écriture d'un autre compte n'est pas montrée

    def test_C07_navigateur_etat_a_verifier_et_bouton_verifier(self):
        js = (ROOT / 'agent' / 'static' / 'v56108-editeur.js').read_text(encoding='utf-8')
        self.assertNotIn('rien n’a été écrit.\'', js.split('function decider')[1].split('function fermer')[0])   # plus de fausse certitude après une réponse perdue
        for attendu in ("'a_verifier'", "'verifier'", "'reessayer'", 'À vérifier', 'corps.reessayer = true', 'etat.incertain'):
            self.assertIn(attendu, js, attendu)
        self.assertNotIn('innerHTML', js)


def PurePosixPathName(chemin):
    return chemin.rsplit('/', 1)[-1]


# ================================================================================================ C08 · saisie WordCraft préservée
# Le VRAI script ``v56108-editeur.js`` est chargé par Node dans un DOM minimal (ce n'est pas une recette visuelle de WordCraft) ; ``fetch``
# est un faux dont chaque appel se résout ou échoue à la demande du scénario. Sans Node sur le poste : tests ignorés, dits comme tels.
BANC_JS = r'''
'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = process.argv[2], scenario = process.argv[3];
function banc() {
  const handlers = {}, docHandlers = {}, appels = [], dits = [];
  const node = () => ({ textContent: '', hidden: false, type: '', classList: { toggle() {} }, appendChild() {}, insertBefore() {}, addEventListener() {},
    querySelector() { return { focus() {}, hidden: false }; }, setAttribute() {}, focus() {} });
  const rapport = node(), controle = node(), iframe = { ...node(), contentWindow: {} };
  const zoneEtat = { classList: { toggle() {} }, set textContent(v) { dits.push(v); }, get textContent() { return dits[dits.length - 1] || ''; } };
  const conf = { getAttribute(k) { return k === 'data-w108-config' ? JSON.stringify({ api: '/api/', nom: 'Acte.docx' }) : 'wordcraft'; } };
  const document = { documentElement: { getAttribute() { return null; } },
    querySelector(s) {
      if (s === '[data-w108-config]') return conf;
      if (s === '[data-w108-rapport]') return rapport;
      if (s === '[data-w108-controle]') return controle;
      if (s === '[data-w108-etat]') return zoneEtat;
      if (s === '[data-w108-iframe]') return iframe;
      return null;
    }, createElement: node, addEventListener(n, fn) { docHandlers[n] = fn; } };
  const window = { addEventListener(n, fn) { handlers[n] = fn; }, crypto: require('crypto').webcrypto, confirm() { return true; } };
  const fetch = (url, opts) => new Promise((resolve, reject) => { appels.push({ url, opts, resolve, reject }); });
  const ctx = { document, window, location: { origin: 'https://cabinet.example.test' }, fetch, Uint8Array, Promise, console, setInterval() {}, clearInterval() {},
    URLSearchParams, encodeURIComponent, JSON, Number, String, Object };
  ctx.globalThis = ctx;
  let js = fs.readFileSync(source, 'utf8');
  js = js.replace(/\}\)\(\);\s*$/, "globalThis.lire=()=>etat; globalThis.initialiser=()=>{etat.seance='seance-fictive';};})();");
  vm.runInNewContext(js, ctx); ctx.initialiser();
  const tick = async () => { for (let i = 0; i < 6; i++) { await new Promise(r => setImmediate(r)); } };
  return { etat: () => ctx.lire(), appels, dits, tick,
    message(data) { handlers.message({ source: iframe.contentWindow, origin: ctx.location.origin, data }); },
    clic(action, attributs) { const b = { disabled: false, getAttribute(k) { return k === 'data-w108' ? action : ((attributs || {})[k] || null); }, closest() { return null; } };
      docHandlers.click({ target: { closest() { return b; } } }); },
    quitter() { let retenu = false; handlers.beforeunload({ preventDefault() { retenu = true; } }); return retenu; },
    ok(i, j, statut) { appels[i].resolve({ ok: true, status: statut || 200, json: () => Promise.resolve(j) }); },
    refus(i, j, statut) { appels[i].resolve({ ok: false, status: statut || 409, json: () => Promise.resolve(j) }); },
    perdu(i) { appels[i].reject(new Error('reseau')); } };
}
const sauver = (b, extra) => b.message({ type: 'wordcraft:save', name: 'Acte.docx', bytes: new Uint8Array([1, 2, 3]), ...(extra || {}) });
const saisir = (b, extra) => b.message({ type: 'wordcraft:dirty', dirty: true, ...(extra || {}) });
const param = (url, nom) => new URLSearchParams(url.split('?')[1]).get(nom);
const scenarios = {
  async recu_tardif() {                        // le reçu de V1 arrive après une saisie V2 : V2 n'est jamais déclarée enregistrée
    const b = banc(); saisir(b); sauver(b); await b.tick(); saisir(b);
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, true); assert.strictEqual(b.etat().revision, 1);
    assert.ok(/plus récentes/.test(b.dits[b.dits.length - 1]));
    assert.strictEqual(b.quitter(), true);
  },
  async propre_sans_saisie_posterieure() {
    const b = banc(); saisir(b); sauver(b); await b.tick();
    b.message({ type: 'wordcraft:dirty', dirty: false });           // « propre » annoncé pendant l'envoi : ignoré, le reçu décidera
    assert.strictEqual(b.etat().sale, true);
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, false); assert.strictEqual(b.etat().envois, 0);
  },
  async deux_enregistrements_successifs() {    // V1 puis V2 : chaque reçu n'acquitte que son instantané, dans l'ordre
    const b = banc(); saisir(b); sauver(b); saisir(b); sauver(b); await b.tick();
    assert.strictEqual(b.appels.length, 1);                          // le second attend le reçu du premier (révision attendue)
    assert.strictEqual(param(b.appels[0].url, 'instantane'), '1'); assert.strictEqual(param(b.appels[0].url, 'revision'), '0');
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, true);                         // V2 saisie entre-temps
    assert.strictEqual(b.appels.length, 2);
    assert.strictEqual(param(b.appels[1].url, 'instantane'), '2'); assert.strictEqual(param(b.appels[1].url, 'revision'), '1');
    assert.notStrictEqual(param(b.appels[0].url, 'cle'), param(b.appels[1].url, 'cle'));
    b.ok(1, { revision: 2, instantane: 2, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, false); assert.strictEqual(b.etat().revision, 2); assert.strictEqual(b.etat().candidat, 2);
  },
  async erreur_puis_saisie() {                 // refus du serveur : rien n'est acquitté ; la saisie suivante reste « modifiée »
    const b = banc(); saisir(b); sauver(b); await b.tick();
    b.message({ type: 'wordcraft:dirty', dirty: false });
    b.refus(0, { error: 'version_perimee', message: 'Version plus récente.' }); await b.tick();
    assert.strictEqual(b.etat().sale, true); assert.strictEqual(b.etat().revision, 0); assert.strictEqual(b.etat().envois, 0);
    saisir(b); assert.strictEqual(b.etat().sale, true); assert.strictEqual(b.quitter(), true);
  },
  async reponse_perdue() {                     // perte de réponse : résultat inconnu, jamais « enregistré »
    const b = banc(); saisir(b); sauver(b); await b.tick(); b.perdu(0); await b.tick();
    assert.strictEqual(b.etat().sale, true); assert.strictEqual(b.etat().envois, 0);
    assert.ok(/inconnu/.test(b.dits[b.dits.length - 1]));
  },
  async recu_d_un_autre_instantane() {         // le serveur acquitte un autre numéro : rien n'est confirmé
    const b = banc(); saisir(b); sauver(b); await b.tick();
    b.ok(0, { revision: 5, instantane: 9, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, true); assert.strictEqual(b.etat().revision, 0);
  },
  async fermeture_retenue_pendant_un_envoi() {
    const b = banc(); saisir(b); sauver(b); await b.tick();
    b.clic('fermer'); await b.tick();
    assert.strictEqual(b.appels.length, 1);                          // aucune demande de fermeture pendant l'enregistrement
    assert.ok(/attendez/.test(b.dits[b.dits.length - 1]));
  },
  async contrat_identifiants_du_cadre() {      // évolution du cadre : « revision » jointe à save ET dirty, utilisée telle quelle
    let b = banc(); saisir(b, { revision: 7 }); sauver(b, { revision: 7 }); await b.tick(); saisir(b, { revision: 8 });
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, true);
    b = banc(); saisir(b, { revision: 7 }); sauver(b, { revision: 7 }); await b.tick();
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, false);
    b = banc(); saisir(b); sauver(b, { revision: 40 }); await b.tick();   // « revision » sur save seulement : contrat incomplet, compteur de l'hôte
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    assert.strictEqual(b.etat().sale, false);
  },
  async c07_reponse_perdue_puis_verifier() {   // « Garder » sans réponse : état À vérifier ; « Vérifier » redemande la MÊME décision
    const b = banc(); saisir(b); sauver(b); await b.tick();
    b.ok(0, { revision: 1, instantane: 1, controle: {} }); await b.tick();
    b.clic('garder'); await b.tick();
    const d1 = JSON.parse(b.appels[1].opts.body);
    b.perdu(1); await b.tick();
    assert.ok(b.etat().incertain); assert.ok(/À vérifier/.test(b.dits[b.dits.length - 1]));
    assert.ok(!/rien n.a été écrit/.test(b.dits[b.dits.length - 1]));
    assert.strictEqual(b.quitter(), true);
    b.clic('fermer'); await b.tick(); assert.strictEqual(b.appels.length, 2);   // fermeture retenue tant que c'est incertain
    b.clic('verifier'); await b.tick();
    const d2 = JSON.parse(b.appels[2].opts.body);
    assert.deepStrictEqual([d2.seance, d2.revision, d2.decision], [d1.seance, d1.revision, 'garder']);
    assert.notStrictEqual(d2.cle, d1.cle); assert.strictEqual(d2.reessayer, undefined);
    b.ok(2, { etat: 'a_verifier', motif: 'cible_absente', reessayable: true, seance: d1.seance, revision: d1.revision, message: 'À vérifier : absent.' }, 202);
    await b.tick();
    assert.strictEqual(b.etat().incertain.reessayable, true); assert.strictEqual(b.etat().candidat, 1);
    b.clic('reessayer'); await b.tick();
    const d3 = JSON.parse(b.appels[3].opts.body);
    assert.deepStrictEqual([d3.seance, d3.revision, d3.decision, d3.reessayer], [d1.seance, d1.revision, 'garder', true]);
    b.ok(3, { etat: 'verifie', chemin: '/Dossiers/DEMO/Acte (modifié).docx', message: 'Nouvelle version enregistrée.' }); await b.tick();
    assert.strictEqual(b.etat().incertain, null); assert.strictEqual(b.etat().candidat, 0);
  },
  async c07_ligne_de_la_page_apres_rechargement() {   // écriture listée par la page : séance et révision viennent du serveur
    const b = banc();
    b.clic('verifier', { 'data-w108-seance': 'a'.repeat(32), 'data-w108-revision': '3' }); await b.tick();
    const d = JSON.parse(b.appels[0].opts.body);
    assert.deepStrictEqual([d.seance, d.revision, d.decision], ['a'.repeat(32), 3, 'garder']);
  },
};
scenarios[scenario]().then(() => console.log('OK ' + scenario), e => { console.error(e); process.exitCode = 1; });
'''


@unittest.skipUnless(shutil.which('node'), 'Node absent : scénarios du script navigateur non exécutés sur ce poste')
class C08Saisie(unittest.TestCase):
    SCRIPT = ROOT / 'agent' / 'static' / 'v56108-editeur.js'

    def jouer(self, scenario, script=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            banc = Path(d) / 'banc.cjs'
            banc.write_text(BANC_JS, encoding='utf-8')
            return subprocess.run(['node', str(banc), str(script or self.SCRIPT), scenario], capture_output=True, text=True, encoding='utf-8', timeout=60)

    def reussit(self, scenario):
        r = self.jouer(scenario)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn('OK ' + scenario, r.stdout)

    def test_C08_recu_tardif_ne_declare_pas_la_saisie_posterieure_enregistree(self):
        self.reussit('recu_tardif')

    def test_C08_propre_seulement_sans_saisie_posterieure(self):
        self.reussit('propre_sans_saisie_posterieure')

    def test_C08_deux_enregistrements_successifs(self):
        self.reussit('deux_enregistrements_successifs')

    def test_C08_erreur_puis_saisie(self):
        self.reussit('erreur_puis_saisie')

    def test_C08_reponse_perdue_resultat_inconnu(self):
        self.reussit('reponse_perdue')

    def test_C08_recu_d_un_autre_instantane_non_acquitte(self):
        self.reussit('recu_d_un_autre_instantane')

    def test_C08_fermeture_retenue_pendant_un_envoi(self):
        self.reussit('fermeture_retenue_pendant_un_envoi')

    def test_C08_contrat_identifiants_du_cadre(self):
        self.reussit('contrat_identifiants_du_cadre')

    def test_C08_mutation_script_d_origine_le_recu_tardif_efface_la_marque(self):
        """Garde-fou : la comparaison de la révision de saisie. Mutation sur une COPIE du script (l'ancienne affectation ``sale = false``) :
        le scénario du reçu tardif échoue, comme sur la 5.6.108."""
        import tempfile
        js = self.SCRIPT.read_text(encoding='utf-8')
        garde = 'etat.sale = etat.modifications !== saisie;'
        self.assertEqual(js.count(garde), 1)
        with tempfile.TemporaryDirectory() as d:
            copie = Path(d) / 'v56108-editeur.js'
            copie.write_text(js.replace(garde, 'etat.sale = false;'), encoding='utf-8')
            self.assertNotEqual(self.jouer('recu_tardif', copie).returncode, 0)

    def test_C07_navigateur_reponse_perdue_puis_verifier_meme_decision(self):
        self.reussit('c07_reponse_perdue_puis_verifier')

    def test_C07_navigateur_ligne_de_la_page_apres_rechargement(self):
        self.reussit('c07_ligne_de_la_page_apres_rechargement')

class C08Serveur(BaseEditeur):
    def test_C08_le_recu_rend_le_numero_d_instantane_avec_la_revision_du_candidat(self):
        s = self.ouvrir()
        v1 = self.modifie(self.original)
        r = self.api('enregistrer', binaire=v1, args={'seance': s['seance'], 'revision': '0', 'cle': 'cle-c08-00001', 'instantane': '4'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual((r['json']['revision'], r['json']['instantane']), (1, 4))
        r = self.api('enregistrer', binaire=v1, args={'seance': s['seance'], 'revision': '0', 'cle': 'cle-c08-00001', 'instantane': '4'})
        self.assertEqual((r['json']['revision'], r['json']['instantane'], r['json'].get('rejoue')), (1, 4, True))
        r = self.api('enregistrer', binaire=v1, args={'seance': s['seance'], 'revision': '1', 'cle': 'cle-c08-00002', 'instantane': '1;DROP'})
        self.assertFalse(r['status'].startswith('200'))
        self.assertEqual(r['json']['error'], 'instantane_invalide')
        r = self.api('enregistrer', binaire=self.modifie(self.original, 'deux '), args={'seance': s['seance'], 'revision': '1', 'cle': 'cle-c08-00003'})
        self.assertEqual(r['json']['revision'], 2)                       # sans numéro (ancienne page encore ouverte) : contrat d'origine
        self.assertNotIn('instantane', r['json'])


if __name__ == '__main__':
    unittest.main()
