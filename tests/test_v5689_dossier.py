"""5.6.89, lot dossier : mémoire typée, personnes et rôles, pipeline d'extraction, projection, mission de conseil, playbook, accords signés.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, RG 24/01234, example.test). Stockage et modèle simulés : le faux modèle renvoie aussi des sorties invalides et des sources fabriquées. Aucun réseau, aucun modèle réel.
Ces tests prouvent les contrôles du serveur. Ils ne disent rien de la qualité réelle d'une extraction, d'une consultation ou d'une revue : ce point exige un modèle réel et un avis d'avocat.
"""
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest

import test_agent
from test_v5689_livrables import Stockage, texte_docx
from agent import accords5689 as accords, assertions5689 as A, chaine5689, competences5632, consultation5689 as conseil, contexte5651, dossier5624, dossier_web5689, export5688, extraction5689 as ex, legal_memory, livrables5689 as liv, membres5650, memoire5651, missions567
from agent import bibliotheque5639, modele5651, personnes5689 as pers, playbook5689 as pb, verification5689, web5689
from agent import cache_extraction5689 as cache
from agent.budget_ledger5689 import AttenteBudget
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.document_projects import _pdf

MID = 'DOS-001'
AVOCAT, ASSISTANT = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant')
DOSSIER = '/Dossiers/DEMO'


class FauxModele:
    """Rend, dans l'ordre, les réponses préparées par étape. Une réponse peut être un texte invalide, ou une exception à lever."""
    cfg = {'provider_type': 'ollama', 'purpose': 'legal_analysis'}

    def __init__(self, **reponses):
        self.reponses = {k: list(v) if isinstance(v, list) else [v] for k, v in reponses.items()}
        self.appels, self.ecritures, self.charges = [], [], []

    def _etape(self, messages):
        systeme = messages[0]['content']
        if 'extrais les éléments' in systeme:
            return 'extraction'
        if 'matière d’une consultation' in systeme:
            return 'options'
        if 'relis un contrat' in systeme:
            return 'propositions'
        return 'obligations'

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        etape = self._etape(messages)
        self.appels.append(etape)
        self.charges.append(json.loads(messages[1]['content']))
        file = self.reponses.get(etape, [])
        reponse = file.pop(0) if len(file) > 1 else (file[0] if file else {})
        if isinstance(reponse, BaseException):
            raise reponse
        return reponse if isinstance(reponse, str) else json.dumps(reponse, ensure_ascii=False)

    def ask(self, stage, data):
        self.ecritures.append((stage, data))
        if self.reponses.get('ecriture_echec'):
            raise self.reponses['ecriture_echec'].pop(0)
        cite = [data['sources'][0]['id']]
        sections = [{'heading': x, 'body': 'Contenu de la rubrique, sans ajout.', 'source_ids': cite} for x in conseil.RUBRIQUES]
        return {'title': 'Consultation', 'act_type': 'consultation', 'introduction': 'Projet.', 'introduction_source_ids': cite, 'sections': sections, 'requests': [], 'exhibits_referenced': [], 'placeholders': [], 'points_for_lawyer': [], 'source_ids': [], 'limits': []}


class Stock(Stockage):
    def inventory(self, path):
        return [{'path': p, 'directory': False, 'etag': self.etags[p], 'size': len(b), 'modified': '2026-10-01T10:00:00+00:00'} for p, b in self.files.items() if p.startswith(path + '/')]

    def file_web_url(self, path):
        return 'https://nuage.example.test/f/' + path.rsplit('/', 1)[-1]


class Base(unittest.TestCase):
    def setUp(self):
        self.f = test_agent.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        private_json(self.f.matter_file, self.f.matters + [{'id': 'DOS-002', 'client_name': 'Société KERVILAC', 'path': '/Dossiers/DEUX', 'references': ['DOS-002'], 'aliases': [], 'correspondents': []}])
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.nc = Stock()
        p = patch('agent.office440.dav_client', return_value=self.nc)
        p.start()
        self.addCleanup(p.stop)
        p = patch('agent.document_projects._dav', return_value=self.nc)
        p.start()
        self.addCleanup(p.stop)
        A.ensure_schema(self.desk)
        modele5651.ensure_schema(self.desk)

    def docx(self, nom, *paragraphes, emetteur='', **kw):
        raw = texte_docx(*paragraphes)
        path = '%s/%s' % (DOSSIER, nom)
        self.nc.files.pop(path, None)
        self.nc.add(path, raw)
        return A.importer_version(self.desk, MID, path, raw, emetteur=emetteur, **kw)

    def extraire(self, version, modele):
        run = ex.lancer(self.desk, MID, version['version_id'], *AVOCAT, 'cle-%s' % version['version_id'][:12])
        return ex.executer(self.desk, {'run': run['id']}, modele)

    def element(self, statement):
        row = self.desk.db.execute('SELECT * FROM case_assertions5689 WHERE statement=? ORDER BY created DESC', (statement,)).fetchone()
        self.assertIsNotNone(row, statement)
        return row

    def valider(self, row, **plus):
        return A.decider(self.desk, row['id'], {'action': 'valider', 'revision': row['revision'], 'cle': 'cle-' + row['id'][:20], 'passage_relu': True, **plus}, *AVOCAT)

    def appel(self, name, body=None, who=AVOCAT, args=None):
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST' if body is not None else 'GET'}
        out = web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/' + name, args or {}, 'POST' if body is not None else 'GET')
        return out


def elt(nature, texte, source, passage, **plus):
    return {'nature': nature, 'texte': texte, 'source': source, 'passage': passage, **plus}


class Memoire(Base):
    def test_IMP02A_juridiction_validee_reste_une_metadonnee(self):
        v = self.docx('jugement.docx', 'Tribunal judiciaire de Valbrune, RG 24/01234.')
        modele = FauxModele(extraction={'elements': [elt('metadata', 'Tribunal judiciaire de Valbrune', 'S1', 'Tribunal judiciaire de Valbrune', cle='juridiction', valeur='Tribunal judiciaire de Valbrune')]})
        self.extraire(v, modele)
        row = self.element('Tribunal judiciaire de Valbrune')
        out = self.valider(row)
        self.assertEqual((out['kind'], out['status']), ('metadata', 'validated'))
        projection = A.projection(self.desk, MID, *AVOCAT)
        self.assertEqual([x['statement'] for x in projection['metadonnees']], ['Tribunal judiciaire de Valbrune'])
        self.assertEqual(projection['faits_utiles'], [])
        self.assertIn('metadonnees', A.paquet_modele(self.desk, MID, *AVOCAT)['categories'])

    def test_IMP02B_allegation_adverse_attribuee_jusque_dans_le_contexte_du_modele(self):
        v = self.docx('conclusions-adverses.docx', 'La société ZORVAL soutient que DURAND n’a jamais livré la marchandise.', emetteur='Société ZORVAL', categorie='acte_procedure')
        modele = FauxModele(extraction={'elements': [elt('fact', 'DURAND n’a jamais livré la marchandise', 'S1', 'DURAND n’a jamais livré la marchandise', auteur='document')]})
        self.extraire(v, modele)
        row = self.element('DURAND n’a jamais livré la marchandise')
        self.assertEqual((row['kind'], row['asserted_by']), ('allegation', 'Société ZORVAL'))      # le modèle disait « fait »
        out = self.valider(row)
        self.assertEqual((out['kind'], out['status']), ('allegation', 'validated'))
        self.assertIn('pas établie', out['portee'])
        paquet = A.paquet_modele(self.desk, MID, *AVOCAT)
        item = paquet['categories']['allegations_attribuees'][0]
        self.assertEqual(item['selon'], 'Société ZORVAL')
        self.assertIn('non établie', item['nature'])
        self.assertNotIn('faits', paquet['categories'])
        contexte = contexte5651.for_drafting(self.desk, MID, 'livraison', 'cabinet')
        self.assertEqual(contexte['elements_du_dossier']['categories']['allegations_attribuees'][0]['selon'], 'Société ZORVAL')

    def test_IMP02C_nouvelle_version_contradictoire_signale_ses_deux_sources_sans_toucher_au_valide(self):
        v1 = self.docx('contrat.docx', 'Le prix est fixé à 10 000 euros.')
        self.extraire(v1, FauxModele(extraction={'elements': [elt('fact', 'Le prix est de 10 000 euros', 'S1', 'Le prix est fixé à 10 000 euros', cle='prix', valeur='10 000')]}))
        connu = self.element('Le prix est de 10 000 euros')
        self.valider(connu)
        decision = A.ajouter(self.desk, MID, {'kind': 'lawyer_decision', 'statement': 'Retenir le prix de 10 000 euros', 'asserted_by': 'Maître DURAND', 'cle': 'decision-prix-1'}, *AVOCAT)
        self.valider(self.element('Retenir le prix de 10 000 euros'))
        avant = dict(self.element('Le prix est de 10 000 euros'))
        v2 = self.docx('contrat.docx', 'Le prix est fixé à 12 000 euros.')
        out = self.extraire(v2, FauxModele(extraction={'elements': [elt('fact', 'Le prix est de 12 000 euros', 'S1', 'Le prix est fixé à 12 000 euros', cle='prix', valeur='12 000')]}))
        self.assertEqual(out['contradictions'], 1)
        apres = dict(self.element('Le prix est de 10 000 euros'))
        self.assertEqual((apres['status'], apres['statement'], apres['revision']), (avant['status'], avant['statement'], avant['revision']))
        self.assertEqual(apres['source_etat'], 'version_remplacee')       # la décision historique reste, son obsolescence s'affiche
        nouvelle = self.element('Le prix est de 12 000 euros')
        self.assertEqual(nouvelle['status'], 'proposed')
        conflits = A.contradictions(self.desk, MID)
        self.assertEqual(len(conflits), 1)
        self.assertEqual(len(set(conflits[0]['sources'])), 2)
        self.assertTrue(all('contrat.docx' in s for s in conflits[0]['sources']))
        self.assertEqual(len(legal_memory.conflicts(self.desk, MID)), 1)      # visible aussi dans l'écran historique
        self.assertTrue(memoire5651.decisions(self.desk, MID))
        self.assertTrue(any(c['kind'] == 'contradiction' for c in memoire5651.changes(self.desk, MID)))

    def test_IMP02D_correction_reindexe_le_seul_element_et_rien_ne_fuit_hors_droits(self):
        v = self.docx('note.docx', 'La livraison a eu lieu à Valbrune.', 'Le paiement a été reçu de MARTIN.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'Livraison à Valbrune', 'S1', 'La livraison a eu lieu à Valbrune'), elt('fact', 'Paiement reçu de MARTIN', 'S2', 'Le paiement a été reçu de MARTIN')]}))
        a, b = self.element('Livraison à Valbrune'), self.element('Paiement reçu de MARTIN')
        indexe = self.desk.db.execute('SELECT indexe_le FROM assertion_index5689 WHERE assertion_id=?', (b['id'],)).fetchone()[0]
        self.assertEqual(A.rechercher(self.desk, 'Valbrune', *AVOCAT, MID)['total'], 1)
        with patch.object(A, 'indexer', wraps=A.indexer) as suivi:
            A.decider(self.desk, a['id'], {'action': 'corriger', 'revision': a['revision'], 'statement': 'Livraison à ORLANDEC', 'cle': 'cle-correction-1'}, *AVOCAT)
        self.assertEqual(suivi.call_args_list[0].args[1], [a['id']])
        self.assertEqual(self.desk.db.execute('SELECT indexe_le FROM assertion_index5689 WHERE assertion_id=?', (b['id'],)).fetchone()[0], indexe)
        trouve = A.rechercher(self.desk, 'ORLANDEC', *AVOCAT, MID)
        self.assertEqual([x['assertion_id'] for x in trouve['resultats']], [a['id']])
        self.assertEqual(self.desk.db.execute('SELECT revision FROM assertion_index5689 WHERE assertion_id=?', (a['id'],)).fetchone()[0], 2)
        # compte écarté : dossier réservé, ni ligne ni compteur, même avec une entrée de cache déjà écrite pour un autre compte
        with patch.object(membres5650, 'hidden_for', return_value=frozenset((MID,))):
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                A.rechercher(self.desk, 'ORLANDEC', *ASSISTANT, MID)
            large = A.rechercher(self.desk, 'ORLANDEC', *ASSISTANT)
            self.assertEqual((large['total'], large['resultats']), (0, []))
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                A.projection(self.desk, MID, *ASSISTANT)
        self.assertNotEqual(A.cle_cache(self.desk, 'recherche', MID, frozenset(), 'r1'), A.cle_cache(self.desk, 'recherche', MID, frozenset(('DOS-002',)), 'r1'))
        self.assertNotEqual(A.cle_cache(self.desk, 'recherche', MID, frozenset(), 'r1'), A.cle_cache(self.desk, 'recherche', MID, frozenset(), 'r2'))
        self.assertNotEqual(A.cle_cache(self.desk, 'recherche', MID, frozenset(), 'r1', 'consultation'), A.cle_cache(self.desk, 'recherche', MID, frozenset(), 'r1', 'contract_review'))

    def test_AV03_natures_distinctes_apres_validation_et_source_ouvrable(self):
        pdf = _pdf(['Le contrat a été signé par DURAND.', 'La société ZORVAL conteste la signature.'])
        path = DOSSIER + '/piece.pdf'
        self.nc.add(path, pdf)
        v = A.importer_version(self.desk, MID, path, pdf, categorie='piece')
        s = A.segments(self.desk, v['version_id'])
        self.assertEqual(s[0]['locator_kind'], 'pdf_page')
        ref = A.source_ref(v, 'pdf_page', s[0]['locator'])
        e1 = A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Contrat signé par DURAND', 'asserted_by': 'Pièce', 'source_ref': ref, 'cle': 'ajout-fait-1'}, *AVOCAT)
        e2 = A.ajouter(self.desk, MID, {'kind': 'allegation', 'statement': 'Signature contestée', 'asserted_by': 'Société ZORVAL', 'source_ref': ref, 'cle': 'ajout-alleg-1'}, *AVOCAT)
        e3 = A.ajouter(self.desk, MID, {'kind': 'metadata', 'statement': 'Tribunal de Valbrune', 'asserted_by': 'Avocat', 'cle': 'ajout-meta-1'}, *AVOCAT)
        e4 = A.ajouter(self.desk, MID, {'kind': 'lawyer_decision', 'statement': 'Ne pas contester la date', 'asserted_by': 'Avocat', 'cle': 'ajout-deci-1'}, *AVOCAT)
        for x in (e1, e2, e3, e4):
            self.valider(self.desk.db.execute('SELECT * FROM case_assertions5689 WHERE id=?', (x['assertion_id'],)).fetchone())
        kinds = {r['statement']: r['kind'] for r in self.desk.db.execute('SELECT statement, kind FROM case_assertions5689')}
        self.assertEqual(set(kinds.values()), {'fact', 'allegation', 'metadata', 'lawyer_decision'})
        vue = A.vue(self.element('Contrat signé par DURAND'))
        self.assertEqual(vue['source_ref']['sha256'], hashlib.sha256(pdf).hexdigest())
        ouverte = A.controler_ref(self.desk, MID, vue['source_ref'])[1]
        self.assertIn('signé par DURAND', ouverte)
        self.assertTrue(self.element('Ne pas contester la date')['decision_id'])
        self.assertEqual(memoire5651.decisions(self.desk, MID)[0]['text'], 'Ne pas contester la date')

    def test_emplacement_page_refusee_sur_un_word_et_passage_controle(self):
        v = self.docx('acte.docx', 'Premier paragraphe du document.')
        with self.assertRaisesRegex(Stop, 'pagination_word_refusee'):
            A.controler_ref(self.desk, MID, A.source_ref(v, 'pdf_page', '3'))
        with self.assertRaisesRegex(Stop, 'emplacement_introuvable'):
            A.controler_ref(self.desk, MID, A.source_ref(v, 'paragraph', '99'))
        with self.assertRaisesRegex(Stop, 'passage_introuvable'):
            A.controler_ref(self.desk, MID, A.source_ref(v, 'paragraph', '1'), 'autre texte')
        ref = dict(A.source_ref(v, 'paragraph', '1'), sha256='0' * 64)
        with self.assertRaisesRegex(Stop, 'empreinte_source_differente'):
            A.controler_ref(self.desk, MID, ref)
        self.assertEqual(A.controler_ref(self.desk, MID, A.source_ref(v, 'paragraph', '1'))[0]['version_id'], v['version_id'])

    def test_version_source_immuable(self):
        v = self.docx('acte.docx', 'Texte.')
        with self.assertRaises(sqlite3.IntegrityError):
            self.desk.db.execute('UPDATE source_versions5689 SET sha256=? WHERE version_id=?', ('f' * 64, v['version_id']))
        self.desk.db.rollback()
        self.assertEqual(A.importer_version(self.desk, MID, v['path'], self.nc.files[v['path']])['version_id'], v['version_id'])

    def test_correction_evenement_et_audit_dans_la_meme_transaction(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Date de livraison connue', 'asserted_by': 'Avocat', 'cle': 'ajout-transaction'}, *AVOCAT)
        row = self.element('Date de livraison connue')
        with patch.object(self.desk, 'audit', side_effect=sqlite3.OperationalError('disque plein')):
            with self.assertRaises(sqlite3.OperationalError):
                A.decider(self.desk, row['id'], {'action': 'corriger', 'revision': row['revision'], 'statement': 'Autre date', 'cle': 'cle-panne-1'}, *AVOCAT)
        apres = self.desk.db.execute('SELECT statement, revision FROM case_assertions5689 WHERE id=?', (row['id'],)).fetchone()
        self.assertEqual((apres['statement'], apres['revision']), ('Date de livraison connue', 1))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM assertion_events5689 WHERE cle='cle-panne-1'").fetchone())
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM d51_changes WHERE object_kind='element'").fetchone())

    def test_revision_attendue_409_idempotence_et_retracabilite(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Fait initial', 'asserted_by': 'Avocat', 'cle': 'ajout-fait-a'}, *AVOCAT)
        row = self.element('Fait initial')
        donnees = {'action': 'corriger', 'revision': 1, 'statement': 'Fait corrigé', 'motif': 'Erreur de plume', 'cle': 'cle-correction-a'}
        A.decider(self.desk, row['id'], donnees, *AVOCAT)
        self.assertEqual(A.decider(self.desk, row['id'], donnees, *AVOCAT)['revision'], 2)      # rejeu : même résultat, pas de troisième révision
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            A.decider(self.desk, row['id'], {'action': 'corriger', 'revision': 1, 'statement': 'Autre', 'cle': 'cle-correction-b'}, *AVOCAT)
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):
            A.decider(self.desk, row['id'], {**donnees, 'statement': 'Encore autre'}, *AVOCAT)
        histoire = A.historique(self.desk, row['id'], *AVOCAT)
        self.assertEqual(histoire[-1]['avant']['statement'], 'Fait initial')
        self.assertEqual(histoire[-1]['motif'], 'Erreur de plume')

    def test_validation_ne_change_jamais_la_nature_et_reclassement_expres(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Ceci est classé fait', 'asserted_by': 'Avocat', 'cle': 'ajout-nature'}, *AVOCAT)
        row = self.element('Ceci est classé fait')
        out = A.decider(self.desk, row['id'], {'action': 'valider', 'revision': 1, 'kind': 'metadata', 'cle': 'cle-nature-1'}, *AVOCAT)
        self.assertEqual(out['kind'], 'fact')
        out = A.decider(self.desk, row['id'], {'action': 'reclasser', 'revision': 2, 'kind': 'metadata', 'cle': 'cle-nature-2'}, *AVOCAT)
        self.assertEqual((out['kind'], out['status']), ('metadata', 'validated'))

    def test_passage_a_relire_avant_validation_dun_element_important(self):
        v = self.docx('acte.docx', 'DURAND a signé.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'DURAND a signé', 'S1', 'DURAND a signé')]}))
        row = self.element('DURAND a signé')
        with self.assertRaisesRegex(Stop, 'passage_a_relire'):
            A.decider(self.desk, row['id'], {'action': 'valider', 'revision': 1, 'cle': 'cle-sans-relecture'}, *AVOCAT)
        self.assertEqual(self.valider(row)['passage_controle'], 'relu')

    def test_assistant_ne_valide_pas_meme_si_la_route_est_contournee(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Fait proposé', 'asserted_by': 'Avocat', 'cle': 'ajout-role'}, *ASSISTANT)
        row = self.element('Fait proposé')
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            A.decider(self.desk, row['id'], {'action': 'valider', 'revision': 1, 'cle': 'cle-role-1', 'passage_relu': True}, *ASSISTANT)

    def test_retrait_de_source_garde_la_decision_et_affiche_obsolescence(self):
        v = self.docx('acte.docx', 'DURAND a signé le bail.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'Bail signé', 'S1', 'DURAND a signé le bail')]}))
        row = self.element('Bail signé')
        self.valider(row)
        out = A.retirer_source(self.desk, MID, v['version_id'], *AVOCAT, 'Pièce écartée')
        self.assertEqual(out['elements_a_reexaminer'], 1)
        apres = A.vue(self.element('Bail signé'))
        self.assertEqual(apres['status'], 'validated')
        self.assertIn('retirée', apres['source_note'])
        self.assertEqual(A.projection(self.desk, MID, *AVOCAT)['faits_utiles'][0]['source_etat'], 'source_retiree')

    def test_reprise_additive_jamais_certifiee(self):
        now = '2026-10-01T10:00:00+00:00'
        self.desk.db.execute('INSERT INTO legal_memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('rec-1', MID, 'jurisdiction', 'Juridiction', 'Tribunal de Valbrune', 'DURAND', '', 0.9, 'validated', '[]', '[]', 'structured_extraction', 1, '', now, now, now, ''))
        self.desk.db.execute('INSERT INTO legal_memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('rec-2', MID, 'other', 'Divers', 'Note ancienne', '', '', 0.4, 'suggested', '[]', '[]', 'model', 1, '', now, now, '', ''))
        self.desk.db.execute('INSERT INTO legal_memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('rec-3', MID, 'claim', 'Demande', 'Fait refusé', '', '', 0.4, 'disputed', '[]', '[]', 'model', 1, '', now, now, '', ''))
        self.desk.db.execute('INSERT INTO case_facts VALUES (?,?,?,?,?,?,?,?)', ('fait-1', MID, 'general', 'Ancien fait retenu', 'validated', '[]', now, now))
        ident = memoire5651.save_decision(self.desk, MID, 'Maître DURAND', {'kind': 'objectif', 'texte': 'Obtenir la résiliation'})
        avant = [dict(r) for r in self.desk.db.execute('SELECT * FROM legal_memory_records ORDER BY id')]
        n = A.migrer_dossier(self.desk, MID)
        self.assertEqual(n, 5)
        self.assertEqual(A.migrer_dossier(self.desk, MID), 0)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM case_assertions5689 WHERE status='validated'").fetchone()[0], 0)
        self.assertEqual([dict(r) for r in self.desk.db.execute('SELECT * FROM legal_memory_records ORDER BY id')], avant)
        juri = self.desk.db.execute("SELECT * FROM case_assertions5689 WHERE legacy_id='rec-1'").fetchone()
        self.assertEqual((juri['kind'], juri['status'], juri['legacy_status'], juri['a_reclasser']), ('metadata', 'extracted', 'validated', 0))
        self.assertEqual(self.desk.db.execute("SELECT a_reclasser FROM case_assertions5689 WHERE legacy_id='rec-2'").fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute("SELECT status FROM case_assertions5689 WHERE legacy_id='rec-3'").fetchone()[0], 'disputed')
        with self.assertRaisesRegex(Stop, 'source_a_rattacher'):
            A.decider(self.desk, juri['id'], {'action': 'valider', 'revision': 1, 'cle': 'cle-reprise-1', 'passage_relu': True}, *AVOCAT)
        self.assertEqual(memoire5651.decisions(self.desk, MID)[0]['id'], ident)
        self.assertEqual({r['id'] for r in legal_memory.memory_records(self.desk, MID)}, {'rec-1', 'rec-2', 'rec-3'})

    def test_export_contient_les_tables_du_lot(self):
        tables = {t[2] for t in export5688.TABLES}
        for table in ('source_versions5689', 'source_segments5689', 'case_assertions5689', 'assertion_relations5689', 'assertion_events5689', 'person_roles5689', 'rapprochements5689', 'extraction_runs5689', 'missions_conseil5689', 'mission_etapes5689',
                      'revues5689', 'revue_propositions5689', 'accords5689', 'obligations5689', 'occurrences5689', 'playbooks5689', 'playbook_regles5689', 'playbook_propositions5689'):
            self.assertIn(table, tables)


class Personnes(Base):
    def _partie(self, mid, nom, **ident):
        pid = modele5651._party_for(self.desk, mid, nom, 'test', 'test')
        if ident:
            self.desk.db.execute('UPDATE d51_parties SET identifiers=? WHERE id=?', (json.dumps(ident), pid))
        self.desk.db.commit()
        return pid

    def test_une_personne_plusieurs_roles_une_seule_ligne(self):
        pid = self._partie(MID, 'Monsieur Paul DURAND')
        pers.ajouter_role(self.desk, MID, pid, 'Dirigeant', 'avocate')
        pers.ajouter_role(self.desk, MID, pid, 'Caution', 'avocate')
        pers.ajouter_role(self.desk, MID, pid, 'Caution', 'avocate')
        lignes = pers.projection(self.desk, MID)
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]['roles'], ['Caution', 'Dirigeant'])

    def test_rapprochement_reversible_jamais_fusion_sur_le_nom_seul(self):
        a = self._partie(MID, 'Société ZORVAL', siren='000010017')
        b = self._partie('DOS-002', 'Société ZORVAL', siren='000010025')
        c = self._partie('DOS-002', 'Société ZORVAL SARL')
        pers.rapprochements_entre_dossiers(self.desk, MID)
        rows = pers.lister(self.desk, MID)
        self.assertEqual(len(rows), 1)                                    # identifiants différents : aucune proposition ; nom proche : proposition seulement
        self.assertEqual(rows[0]['state'], 'proposee')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM d51_parties WHERE status='fusionnee'").fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM d51_parties WHERE id IN (?,?,?)', (a, b, c)).fetchone()[0], 3)

    def test_fusion_dans_un_dossier_seulement_sur_decision_et_annulable(self):
        a = self._partie(MID, 'Monsieur Paul DURAND')
        exacte, nouvelles = pers.rapprocher_mention(self.desk, MID, 'Paul')
        self.assertIsNone(exacte)
        self.assertEqual(nouvelles, 1)
        self.assertEqual(len(modele5651.parties(self.desk, MID)), 1)
        prop = pers.lister(self.desk, MID)[0]
        self.assertEqual(pers.decider(self.desk, prop['id'], 'accepter', 'avocate')['state'], 'acceptee')
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM d51_aliases WHERE party_id=? AND via LIKE 'rapprochement:%'", (a,)).fetchone())
        self.assertEqual(pers.decider(self.desk, prop['id'], 'annuler', 'avocate')['state'], 'annulee')
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM d51_aliases WHERE party_id=? AND via LIKE 'rapprochement:%'", (a,)).fetchone())

    def test_identifiant_different_ecarte_un_nom_identique(self):
        self._partie(MID, 'Société ZORVAL', siren='000010017')
        exacte, nouvelles = pers.rapprocher_mention(self.desk, MID, 'Société ZORVAL', {'siren': '000010033'})
        self.assertIsNone(exacte)
        self.assertEqual(nouvelles, 1)

    def test_categories_et_piece_avec_numero_affiche(self):
        self.assertEqual(A.classer('Jugement du 12 mars.pdf')[0], 'decision')
        self.assertEqual(A.classer('conclusions adverses.docx')[0], 'acte_procedure')
        ambigu = A.classer('projet de contrat.docx')
        self.assertEqual(ambigu[0], 'a_reclasser')
        self.assertEqual(sorted(ambigu[1]), ['piece', 'projet'])
        v = self.docx('bail.docx', 'Bail commercial.')
        with self.assertRaisesRegex(Stop, 'piece_incomplete'):
            A.qualifier_piece(self.desk, MID, v['version_id'], {'categorie': 'piece'}, *AVOCAT)
        out = A.qualifier_piece(self.desk, MID, v['version_id'], {'categorie': 'piece', 'partie': 'Cabinet', 'numero': '1 bis', 'titre': 'Bail'}, *AVOCAT)
        self.assertEqual((out['piece_numero'], out['piece_partie'], out['sha256']), ('1 bis', 'Cabinet', v['sha256']))
        v2 = self.docx('bail.docx', 'Bail commercial modifié.')
        self.assertEqual((v2['piece_numero'], v2['piece_partie']), ('1 bis', 'Cabinet'))      # nouvelle version, même pièce


class Pipeline(Base):
    def test_le_modele_ne_cree_aucun_identifiant_de_source(self):
        v = self.docx('acte.docx', 'La société KERVILAC a résilié le contrat.')
        modele = FauxModele(extraction={'elements': [
            elt('fact', 'Source inventée', 'S99', 'La société KERVILAC a résilié le contrat'), elt('fact', 'Faux passage', 'S1', 'passage que la source ne contient pas'), elt('lawyer_decision', 'Décision fabriquée', 'S1', 'La société KERVILAC a résilié'),
            elt('nature_inconnue', 'Nature fausse', 'S1', 'La société KERVILAC a résilié'), elt('fact', 'Auteur inventé', 'S1', 'La société KERVILAC a résilié', auteur='P77'), 'pas un objet', elt('fact', 'Résiliation', 'S1', 'La société KERVILAC a résilié le contrat')]})
        out = self.extraire(v, modele)
        self.assertEqual(out['propositions'], 1)
        self.assertEqual(out['ecartes'], {'source_non_fournie': 1, 'passage_introuvable': 1, 'nature_reservee_a_lavocat': 1, 'nature_inconnue': 1, 'auteur_non_fourni': 1, 'element_invalide': 1})
        self.assertEqual([r['statement'] for r in self.desk.db.execute('SELECT statement FROM case_assertions5689')], ['Résiliation'])
        charge = modele.charges[0]
        self.assertEqual(charge['extraits'][0]['ref'], 'S1')
        self.assertNotIn(v['version_id'], json.dumps(charge))            # le modèle ne voit que des repères

    def test_sortie_invalide_ne_cree_rien(self):
        v = self.docx('acte.docx', 'Texte du document.')
        for reponse in ('ceci n’est pas du JSON', '[1, 2]', {'autre': []}):
            run = ex.lancer(self.desk, MID, v['version_id'], *AVOCAT, 'cle-invalide-%d' % len(str(reponse)))
            with self.assertRaisesRegex(Stop, 'reponse_modele_invalide'):
                ex.executer(self.desk, {'run': run['id']}, FauxModele(extraction=reponse))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM case_assertions5689').fetchone()[0], 0)

    def test_extraction_ne_modifie_jamais_une_donnee_validee_et_ne_repropose_pas_un_rejet(self):
        v = self.docx('acte.docx', 'DURAND réside à Valbrune.', 'MARTIN réside à ORLANDEC.')
        modele = FauxModele(extraction={'elements': [elt('fact', 'DURAND réside à Valbrune', 'S1', 'DURAND réside à Valbrune'), elt('fact', 'MARTIN réside à ORLANDEC', 'S2', 'MARTIN réside à ORLANDEC')]})
        self.extraire(v, modele)
        self.valider(self.element('DURAND réside à Valbrune'))
        rejet = self.element('MARTIN réside à ORLANDEC')
        A.decider(self.desk, rejet['id'], {'action': 'rejeter', 'revision': 1, 'cle': 'cle-rejet-1'}, *AVOCAT)
        run = ex.lancer(self.desk, MID, v['version_id'], *AVOCAT, 'cle-seconde-lecture')
        out = ex.executer(self.desk, {'run': run['id']}, modele)
        self.assertEqual((out['propositions'], out['doublons']), (0, 2))
        self.assertEqual(self.element('DURAND réside à Valbrune')['revision'], 2)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM case_assertions5689').fetchone()[0], 2)

    def test_cache_extraction_utilise_puis_invalide_par_correction_et_source(self):
        v = self.docx('acte.docx', 'DURAND a payé la facture.')
        modele = FauxModele(extraction={'elements': [elt('fact', 'Facture payée', 'S1', 'DURAND a payé la facture')]})
        self.extraire(v, modele)
        self.assertEqual(modele.appels, ['extraction'])
        run = ex.lancer(self.desk, MID, v['version_id'], *AVOCAT, 'cle-relecture-1')
        out = ex.executer(self.desk, {'run': run['id']}, modele)
        self.assertTrue(out['cache'])
        self.assertEqual(modele.appels, ['extraction'])                    # réutilisé
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 1)
        row = self.element('Facture payée')
        A.decider(self.desk, row['id'], {'action': 'corriger', 'revision': 1, 'statement': 'Facture payée partiellement', 'cle': 'cle-correction-cache'}, *AVOCAT)
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 0)        # la correction invalide
        run = ex.lancer(self.desk, MID, v['version_id'], *AVOCAT, 'cle-relecture-2')
        self.assertFalse(ex.executer(self.desk, {'run': run['id']}, modele)['cache'])
        self.assertEqual(modele.appels, ['extraction', 'extraction'])
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 1)
        self.docx('acte.docx', 'DURAND n’a pas payé la facture.')
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 0)        # le changement de source aussi

    def test_cache_invalide_par_changement_de_droit(self):
        v = self.docx('acte.docx', 'DURAND a payé.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'Paiement', 'S1', 'DURAND a payé')]}))
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 1)
        cache.sur_droits(self.desk.c['state_dir'], MID)
        self.assertEqual(cache.compter(self.desk, MID, *AVOCAT), 0)

    def test_sept_etapes_journalisees_et_ambiguite_signalee(self):
        v = self.docx('note diverse.docx', 'DURAND écrit à MARTIN.')
        out = self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'DURAND écrit', 'S1', 'DURAND écrit à MARTIN', entites=['Monsieur Paul DURAND'])]}))
        run = self.desk.db.execute('SELECT * FROM extraction_runs5689 WHERE id=?', (out['run'],)).fetchone()
        self.assertEqual(set(json.loads(run['etapes'])), set(ex.ETAPES))
        self.assertEqual(run['etat'], 'terminee')
        self.assertTrue(out['categorie_ambigue'])
        changements = memoire5651.changes(self.desk, MID)
        self.assertTrue(any('Lecture de note diverse' in c['title'] for c in changements))

    def test_date_absente_du_passage_retiree(self):
        v = self.docx('acte.docx', 'La vente a eu lieu en 2024.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'Vente', 'S1', 'La vente a eu lieu en 2024', date_evenement='2024-03-12'), elt('fact', 'Autre vente', 'S1', 'La vente a eu lieu', date_evenement='2019-01-01')]}))
        self.assertEqual(self.element('Vente')['date_evenement'], '2024-03-12')
        self.assertEqual(self.element('Autre vente')['date_evenement'], '')

    def test_adaptateur_commun_des_sorties(self):
        self.assertEqual(chaine5689.extraire_json('```json\n{"a": 1}\n```'), {'a': 1})
        self.assertEqual(chaine5689.extraire_json('Voici : {"a": 1} fin'), {'a': 1})
        for mauvais in ('', 'non', '[1]', '{cassé'):
            with self.assertRaisesRegex(Stop, 'reponse_modele_invalide'):
                chaine5689.extraire_json(mauvais)

    def test_politique_refuse_avant_tout_envoi(self):
        v = self.docx('acte.docx', 'Texte.')
        modele = FauxModele(extraction={'elements': []})
        run = ex.lancer(self.desk, MID, v['version_id'], *AVOCAT, 'cle-politique-1')
        with patch('agent.data_policy5689.exiger', side_effect=Stop('mode_local_strict')):
            with self.assertRaisesRegex(Stop, 'mode_local_strict'):
                ex.executer(self.desk, {'run': run['id']}, modele)
        self.assertEqual(modele.appels, [])

    def test_competences_de_l_etape_seulement_et_version_figee(self):
        competences5632.ensure_builtin(self.desk)
        s = competences5632.listing(self.desk)[0]
        selection = [{'id': s['id'], 'version': s['version'], 'mode': 'manuel', 'reason': 'essai'}]
        vu = []

        class Espion(FauxModele):
            def complete(espion, *a, **k):
                vu.append([x['id'] for x in competences5632.current_selection()])
                return '{"elements": []}'
        with patch.object(competences5632, 'resolve', return_value=selection):
            chaine5689.appeler(self.desk, 'options', 'système', {}, MID, modele=Espion(), budget='MISSION-A')
        self.assertEqual(vu, [[s['id']]])
        self.assertEqual(competences5632.current_selection(), [])          # rien ne reste après l'étape
        self.assertEqual(self.desk.db.execute('SELECT skill, etape FROM mission_skills5689 WHERE mission=?', ('MISSION-A',)).fetchall()[0][0], s['id'])


class Projection(Base):
    def test_page_dossier_sections_et_trois_dates(self):
        v = self.docx('assignation.docx', 'Le 12 mars 2024 la société ZORVAL a livré.', document_date='2024-04-02', emetteur='Société ZORVAL')
        self.extraire(v, FauxModele(extraction={'elements': [elt('procedural_event', 'Assignation délivrée', 'S1', 'Le 12 mars 2024', date_evenement='2024-03-12'), elt('claim', 'Paiement de 5 000 euros', 'S1', 'la société ZORVAL a livré'), elt('evidence', 'Bon de livraison', 'S1', 'a livré')]}))
        claim, preuve = self.element('Paiement de 5 000 euros'), self.element('Bon de livraison')
        A.relier(self.desk, MID, preuve['id'], 'soutient', claim['id'], 'Bon de livraison', 'avocate')
        memoire5651.open_gap(self.desk, MID, 'Facture d’origine', 'Manque au dossier')
        data = A.projection(self.desk, MID, *AVOCAT)
        self.assertEqual(data['procedure'][0]['statement'], 'Assignation délivrée')
        self.assertEqual(data['pretentions'][0]['preuves'][0]['statement'], 'Bon de livraison')
        self.assertEqual(data['lacunes'][0]['donnee'], 'Facture d’origine')
        ligne = next(x for x in data['chronologie'] if x['texte'] == 'Assignation délivrée')
        self.assertEqual((ligne['date_evenement'], ligne['date_document']), ('2024-03-12', '2024-04-02'))
        self.assertTrue(ligne['date_import'])
        html = A.rendu_html(self.desk, '/p', MID, data)
        for titre in ('Faits utiles', 'Procédure', 'Prétentions et preuves', 'Décisions', 'Lacunes', 'Chronologie', 'Date de l’événement', 'Date du document', 'Date d’import'):
            self.assertIn(titre, html)

    def test_rendu_echappe_un_texte_hostile(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': '<script>alert(1)</script>', 'asserted_by': '<b>ZORVAL</b>', 'cle': 'ajout-hostile'}, *AVOCAT)
        html = A.rendu_html(self.desk, '/p', MID, A.projection(self.desk, MID, *AVOCAT))
        self.assertNotIn('<script>', html)
        self.assertNotIn('<b>ZORVAL', html)

    def test_source_ouvrable_a_sa_version(self):
        v = self.docx('acte.docx', 'DURAND a signé le bail.')
        self.extraire(v, FauxModele(extraction={'elements': [elt('fact', 'Bail signé', 'S1', 'DURAND a signé le bail')]}))
        row = self.element('Bail signé')
        ouverte = json.loads(self.appel('assertions/%s/source' % row['id'])['body'])
        self.assertEqual((ouverte['version_id'], ouverte['sha256'], ouverte['locator'], ouverte['etat']), (v['version_id'], v['sha256'], '1', 'courante'))
        self.assertIn('DURAND a signé', ouverte['passage'])
        self.assertTrue(ouverte['href'])
        self.docx('acte.docx', 'Autre texte.')
        self.assertEqual(json.loads(self.appel('assertions/%s/source' % row['id'])['body'])['etat'], 'remplacee')

    def test_rubriques_du_dossier_et_synthese_perimee_apres_correction(self):
        self.assertIn('structure', dict(dossier5624.RUBRIQUES))
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Fait', 'asserted_by': 'Avocat', 'cle': 'ajout-rubrique'}, *AVOCAT)
        data = dossier5624.rubrique(self.desk, MID, 'structure')
        self.assertEqual(data['projection']['synthese']['a_confirmer'], 1)
        self.assertIn('Dossier structuré', dossier5624.render(self.desk, '/p', MID, 'structure', data))
        self.assertIn('Nouvelle mission', dossier5624.render(self.desk, '/p', MID, 'conseil', dossier5624.rubrique(self.desk, MID, 'conseil')))
        avant = dossier5624.source_revision(self.desk, MID)
        row = self.element('Fait')
        A.decider(self.desk, row['id'], {'action': 'corriger', 'revision': 1, 'statement': 'Fait corrigé', 'cle': 'cle-rubrique-1'}, *AVOCAT)
        self.assertNotEqual(dossier5624.source_revision(self.desk, MID), avant)

    def test_cache_de_projection_change_avec_la_correction(self):
        A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Avant', 'asserted_by': 'Avocat', 'cle': 'ajout-cache-p'}, *AVOCAT)
        self.assertEqual(A.projection(self.desk, MID, *AVOCAT)['faits_utiles'][0]['statement'], 'Avant')
        row = self.element('Avant')
        A.decider(self.desk, row['id'], {'action': 'corriger', 'revision': 1, 'statement': 'Après', 'cle': 'cle-cache-p-1'}, *AVOCAT)
        self.assertEqual(A.projection(self.desk, MID, *AVOCAT)['faits_utiles'][0]['statement'], 'Après')


REPONSE_OPTIONS = {'options': [{'titre': 'Résilier', 'analyse': 'Option possible.', 'consequences': 'Rupture.'}, {'titre': 'Négocier', 'analyse': 'Option amiable.', 'consequences': 'Délai.'}], 'recommandation': 'Négocier d’abord.', 'reserves': ['Pièce de livraison manquante.'], 'prochaines_actions': ['Demander la pièce.'], 'questions': [], 'references_citees': []}


class Mission(Base):
    def demande(self, **plus):
        return {'matter_id': MID, 'question': 'Peut-on résilier le contrat conclu avec la société ZORVAL ?', 'as_of': '2026-11-02', 'advised_side': 'Monsieur DURAND', 'expected_output': 'consultation', 'cle': 'mission-cle-0001', 'source_refs': [], **plus}

    def lancer(self, modele, recherche=None, **plus):
        self.docx('contrat-source.docx', 'Le contrat lie DURAND et ZORVAL.')
        m = conseil.creer(self.desk, self.demande(**plus), *AVOCAT)
        return m, conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc, recherche)

    def test_mission_reutilise_missions567_et_idempotence(self):
        self.docx('source.docx', 'Texte.')
        m = conseil.creer(self.desk, self.demande(), *AVOCAT)
        again = conseil.creer(self.desk, self.demande(), *AVOCAT)
        self.assertEqual(m['mission_id'], again['mission_id'])
        ligne = self.desk.db.execute('SELECT kind, job_id, ref FROM missions_v567 WHERE id=?', (m['mission_id'],)).fetchone()
        self.assertEqual(ligne['kind'], 'conseil')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM missions_v567').fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='mission_conseil5689'").fetchone()[0], 1)
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):
            conseil.creer(self.desk, self.demande(question='Autre question sur un autre sujet'), *AVOCAT)
        missions567.check_authority(self.desk, {'mission_id': m['mission_id']})

    def test_serveur_fixe_les_actions_et_refuse_une_source_etrangere(self):
        self.docx('source.docx', 'Texte.')
        m = conseil.creer(self.desk, self.demande(allowed_actions=['envoyer', 'signer'], cabinet_id='autre'), *AVOCAT)
        self.assertEqual(m['allowed_actions'], ['read_authorized', 'prepare_internal'])
        self.assertNotEqual(m['cabinet_id'], 'autre')
        raw = texte_docx('Autre dossier.')
        autre = A.importer_version(self.desk, 'DOS-002', '/Dossiers/DEUX/a.docx', raw)
        with self.assertRaisesRegex(Stop, 'source_mission_autre_dossier'):
            conseil.creer(self.desk, self.demande(cle='mission-cle-0002', source_refs=[{'version_id': autre['version_id']}]), *AVOCAT)

    def test_contrat_de_mission_conforme(self):
        self.docx('source.docx', 'Texte.')
        m = conseil.creer(self.desk, self.demande(), *AVOCAT)
        schema = json.loads(Path(__file__).resolve().parent.joinpath('contrats5689/MissionContract.schema.json').read_text(encoding='utf-8'))
        for champ in schema['required']:
            self.assertIn(champ, m)
        self.assertIn(m['state'], schema['properties']['state']['enum'])
        self.assertIn(m['mission_type'], schema['properties']['mission_type']['enum'])

    def test_IMP03A_source_juridique_absente_consultation_partielle_avec_limite(self):
        def panne(desk, matter, question):
            raise Stop('connecteur_indisponible')
        modele = FauxModele(options={**REPONSE_OPTIONS, 'recommandation': 'Selon article L. 442-1 du code de commerce, négocier.', 'references_citees': ['Cass. com., 3 mai 2020, n° 18-12.345']})
        m, out = self.lancer(modele, panne, objectif_recherche='Droit de la résiliation')
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(vue['state'], 'ready_for_review')
        self.assertIn(conseil.LIMITE_RECHERCHE, vue['limites'])
        etapes = {x['code']: x['state'] for x in vue['etapes']}
        self.assertEqual(etapes['recherche_droit'], 'partielle')
        self.assertEqual((etapes['faits'], etapes['options'], etapes['redaction']), ('termine', 'termine', 'termine'))
        self.assertEqual({x['statut'] for x in vue['references']}, {'a_verifier'})
        self.assertEqual(modele.charges[0]['regles_consultees'], [])
        instruction = modele.ecritures[0][1]['instruction_avocat']
        self.assertIn('[À VÉRIFIER : article L. 442-1 du code de commerce', instruction)
        document = liv.charger(self.desk, vue['livrables'][0])
        lignes = verification5689.paragraphes(self.nc.files[document['path']])
        self.assertTrue(any(conseil.LIMITE_RECHERCHE in x for x in lignes))
        self.assertEqual(document['production_state'], 'verified')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE lifecycle='current' AND path LIKE '%Consultation_projet%'").fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM d51_cabinet_decisions').fetchone()[0], 0)
        self.desk.db.execute("UPDATE jobs SET status='done', result='{}' WHERE id=(SELECT job_id FROM missions_v567 WHERE id=?)", (m['mission_id'],))
        self.desk.db.commit()
        carte = missions567.get(self.desk, m['mission_id'], 'cabinet', True, '/p')
        self.assertEqual(carte['state'], 'verified')                      # carte commune, sur la preuve du fichier
        self.assertEqual(carte['result']['open_url'], '/p/livrables/' + vue['livrables'][0])

    def test_reference_verifiee_seulement_si_consultee(self):
        from agent import legal_research
        legal_research.ensure_schema(self.desk)
        now = '2026-10-01T10:00:00+00:00'
        ident = 'a' * 64
        self.desk.db.execute('INSERT INTO legal_authorities_v240 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, MID, 'q', 'source', '', 'https://justice.example.test/d', 'Cass. civ. 1, n° 20-11.111', '', 'Cour de cassation', '2021-02-03', 'Arrêt fictif', 'La résiliation suppose un manquement grave.', 'b' * 64, 'texte', 'verified', '', now, now))
        modele = FauxModele(options={**REPONSE_OPTIONS, 'references_citees': ['Cass. com., 3 mai 2020, n° 18-12.345']})
        m, _ = self.lancer(modele)
        refs = {x['reference']: x['statut'] for x in conseil.vue(self.desk, m['mission_id'], *AVOCAT)['references']}
        self.assertEqual(refs['Cass. civ. 1, n° 20-11.111'], 'verifiee')
        self.assertEqual(refs['Cass. com., 3 mai 2020, n° 18-12.345'], 'a_verifier')
        self.assertIn(conseil.LIMITE_SANS_RECHERCHE, conseil.vue(self.desk, m['mission_id'], *AVOCAT)['limites'])

    def test_recherche_hors_dossier_seulement_avec_objectif_explicite(self):
        appels = []

        def recherche(desk, matter, question):
            appels.append(question)
            return {'status': 'completed'}
        self.lancer(FauxModele(options=REPONSE_OPTIONS), recherche)
        self.assertEqual(appels, [])

    def test_AV04_etapes_independantes_reserve_questions_regroupees_decision_conservee(self):
        questions = [{'texte': 'Le client accepte-t-il une rupture immédiate ?', 'determinante': True}, {'texte': 'Faut-il prévenir la banque ?', 'determinante': True}, {'texte': 'Souhaitez-vous un résumé ?', 'determinante': False}]
        modele = FauxModele(options=[{**REPONSE_OPTIONS, 'questions': questions}, REPONSE_OPTIONS])
        m, out = self.lancer(modele)
        self.assertTrue(out['information_requise'])
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(vue['state'], 'waiting_decision')
        self.assertEqual(len(vue['questions']), 2)                        # une seule demande regroupée, sans la question non déterminante
        self.assertEqual([x['state'] for x in vue['etapes'] if x['code'] in ('faits', 'options')], ['termine', 'termine'])
        self.assertEqual(modele.ecritures, [])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM case_assertions5689 WHERE statement LIKE '%manquante%'").fetchone()[0], 0)      # une réserve ne devient pas un fait
        with self.assertRaisesRegex(Stop, 'reponses_incompletes'):
            conseil.repondre(self.desk, m['mission_id'], {'revision': vue['revision'], 'reponses': ['Oui']}, *AVOCAT)
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            conseil.repondre(self.desk, m['mission_id'], {'revision': vue['revision'] - 1, 'reponses': ['Oui', 'Non']}, *AVOCAT)
        conseil.repondre(self.desk, m['mission_id'], {'revision': vue['revision'], 'reponses': ['Oui', 'Non']}, *AVOCAT)
        self.assertEqual(len(memoire5651.decisions(self.desk, MID)), 2)
        avant = self.desk.db.execute("SELECT fin FROM mission_etapes5689 WHERE mission=? AND code='faits'", (m['mission_id'],)).fetchone()[0]
        out = conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc)
        self.assertEqual(out['etapes_executees'], ['options', 'redaction', 'verification', 'publication'])
        self.assertEqual(self.desk.db.execute("SELECT fin FROM mission_etapes5689 WHERE mission=? AND code='faits'", (m['mission_id'],)).fetchone()[0], avant)
        self.assertEqual(modele.charges[-1]['reponses_de_lavocat'][0]['reponse'], 'Oui')

    def test_reprise_n_execute_que_ce_qui_manque(self):
        modele = FauxModele(options=REPONSE_OPTIONS, ecriture_echec=[Stop('stockage_indisponible')])
        with self.assertRaisesRegex(Stop, 'stockage_indisponible'):
            self.lancer(modele)
        m = self.desk.db.execute('SELECT * FROM missions_conseil5689').fetchone()
        self.assertEqual(m['state'], 'failed')
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual({x['code']: x['state'] for x in vue['etapes']}['redaction'], 'echec')
        conseil.reprendre(self.desk, m['mission_id'], {'revision': vue['revision']}, *AVOCAT)
        out = conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc)
        self.assertEqual(out['etapes_executees'], ['redaction', 'verification', 'publication'])
        self.assertEqual(modele.appels.count('options'), 1)

    def test_relance_d_une_mission_dont_le_travail_a_echoue(self):
        """La colonne blocked_by est NOT NULL : une relance par l'avocat levait une erreur d'intégrité."""
        self.docx('source.docx', 'Texte.')
        m = conseil.creer(self.desk, self.demande(), *AVOCAT)
        job = self.desk.enqueue('mission_conseil5689', {'mission': m['mission_id']}, origin='user')
        self.desk.db.execute("UPDATE jobs SET status='error' WHERE id=?", (job,))
        self.desk.db.execute('UPDATE missions_v567 SET job_id=? WHERE id=?', (job, m['mission_id']))
        self.desk.db.commit()
        conseil._relancer(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'pending')

    def test_preuve_alteree_rejouee_et_nouvelle_version_de_source_invalide_la_suite(self):
        modele = FauxModele(options=REPONSE_OPTIONS)
        m, _ = self.lancer(modele)
        self.desk.db.execute("UPDATE mission_etapes5689 SET sorties='{\"options\":[]}' WHERE mission=? AND code='options'", (m['mission_id'],))
        self.desk.db.execute("UPDATE missions_conseil5689 SET state='failed' WHERE mission_id=?", (m['mission_id'],))
        self.desk.db.commit()
        out = conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc)
        self.assertIn('options', out['etapes_executees'])
        self.assertNotIn('faits', out['etapes_executees'])

    def test_annulation_terminale_et_attente_de_budget_distincte_d_un_echec(self):
        modele = FauxModele(options=AttenteBudget('budget_mission_atteint'))
        with self.assertRaises(AttenteBudget):
            self.lancer(modele)
        m = self.desk.db.execute('SELECT * FROM missions_conseil5689').fetchone()
        self.assertEqual(m['state'], 'budget_wait')
        self.assertEqual({x['code']: x['state'] for x in conseil.vue(self.desk, m['mission_id'], *AVOCAT)['etapes']}['options'], 'a_faire')
        conseil.annuler(self.desk, m['mission_id'], *AVOCAT)
        modele2 = FauxModele(options=REPONSE_OPTIONS)
        self.assertEqual(conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele2, self.nc)['status'], 'abstention')
        self.assertEqual(modele2.appels, [])

    def test_projet_bloque_par_le_controle_reste_visible(self):
        class Muet(FauxModele):
            def ask(muet, stage, data):
                valeur = super().ask(stage, data)
                for x in valeur['sections']:
                    x['source_ids'] = []
                valeur['introduction_source_ids'] = []
                return valeur
        modele = Muet(options=REPONSE_OPTIONS)
        with self.assertRaisesRegex(Stop, 'projet_bloque_par_le_controle'):
            self.lancer(modele)
        m = self.desk.db.execute('SELECT * FROM missions_conseil5689').fetchone()
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(vue['state'], 'failed')
        self.assertTrue(any('bloqué' in x for x in vue['limites']))
        self.assertEqual(vue['livrables'], [])

    def test_annulation_pendant_le_travail_arrete_avant_l_effet(self):
        modele = FauxModele(options=REPONSE_OPTIONS)
        self.docx('source.docx', 'Texte.')
        m = conseil.creer(self.desk, self.demande(), *AVOCAT)
        vrai = missions567.check_authority
        vus = []

        def autorite(desk, args):
            vus.append(1)
            if len(vus) > 6:
                raise Stop('mission_annulee')
            return vrai(desk, args)
        with patch.object(missions567, 'check_authority', side_effect=autorite):
            conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc)
        self.assertEqual(conseil.vue(self.desk, m['mission_id'], *AVOCAT)['state'], 'cancelled')
        self.assertEqual(modele.ecritures, [])

    def test_sortie_invalide_du_modele_laisse_les_etapes_faites(self):
        modele = FauxModele(options=['pas du JSON'])
        with self.assertRaisesRegex(Stop, 'reponse_modele_invalide'):
            self.lancer(modele)
        m = self.desk.db.execute('SELECT * FROM missions_conseil5689').fetchone()
        etats = {x['code']: x['state'] for x in conseil.vue(self.desk, m['mission_id'], *AVOCAT)['etapes']}
        self.assertEqual((etats['faits'], etats['options']), ('termine', 'echec'))

    def test_contexte_borne_et_categories_distinctes(self):
        for n in range(60):
            A.ajouter(self.desk, MID, {'kind': 'fact', 'statement': 'Fait numéro %d %s' % (n, 'x' * 300), 'asserted_by': 'Avocat', 'cle': 'ajout-borne-%02d' % n}, *AVOCAT)
        paquet = A.paquet_modele(self.desk, MID, *AVOCAT, budget=2000)
        self.assertIn('limite', paquet)
        self.assertLessEqual(paquet['caracteres'], 2000)
        self.assertNotIn(self.desk.db.execute('SELECT id FROM case_assertions5689').fetchone()[0], json.dumps(paquet['categories']))

    def test_travailleur_reel_mene_la_mission_jusqu_a_la_preuve(self):
        modele = FauxModele(options=REPONSE_OPTIONS)
        self.docx('source.docx', 'Le contrat lie DURAND et ZORVAL.')
        with patch.object(chaine5689, 'modele_pour', return_value=modele):
            m = conseil.creer(self.desk, self.demande(), *AVOCAT)
            self.assertTrue(self.desk.work_once())
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(vue['state'], 'ready_for_review')
        self.assertEqual(missions567.get(self.desk, m['mission_id'], 'cabinet', True)['state'], 'verified')
        self.assertEqual(self.desk.db.execute("SELECT status FROM jobs WHERE kind='mission_conseil5689'").fetchone()[0], 'done')

    def test_travailleur_reel_budget_attend_sans_echec_et_reprend(self):
        modele = FauxModele(options=[AttenteBudget('budget_mission_atteint'), REPONSE_OPTIONS])
        self.docx('source.docx', 'Le contrat lie DURAND et ZORVAL.')
        with patch.object(chaine5689, 'modele_pour', return_value=modele):
            m = conseil.creer(self.desk, self.demande(), *AVOCAT)
            self.desk.work_once()
            job = self.desk.db.execute("SELECT status, blocked_by FROM jobs WHERE kind='mission_conseil5689'").fetchone()
            self.assertEqual((job['status'], job['blocked_by']), ('pending', 'budget_mission_atteint'))
            self.assertEqual(conseil.vue(self.desk, m['mission_id'], *AVOCAT)['state'], 'budget_wait')
            self.desk.db.execute("UPDATE jobs SET not_before=NULL, blocked_by='' WHERE kind='mission_conseil5689'")
            self.desk.db.commit()
            self.assertTrue(self.desk.work_once())
        self.assertEqual(conseil.vue(self.desk, m['mission_id'], *AVOCAT)['state'], 'ready_for_review')

    def test_pause_de_l_ia_suspend_la_mission(self):
        modele = FauxModele(options=REPONSE_OPTIONS)
        self.docx('source.docx', 'Texte.')
        with patch.object(chaine5689, 'modele_pour', return_value=modele):
            m = conseil.creer(self.desk, self.demande(), *AVOCAT)
            self.desk.setting('missions567:pause', True)
            self.desk.work_once()
        self.assertEqual(modele.appels, [])                               # rien ne part pendant la pause
        self.desk.db.execute("UPDATE jobs SET status='suspended' WHERE kind='mission_conseil5689'")     # état que pose le contrôleur de pause
        self.desk.db.commit()
        self.assertEqual(conseil.vue(self.desk, m['mission_id'], *AVOCAT)['state'], 'suspended')

    def test_pause_pendant_une_etape_suspend_sans_echec(self):
        modele = FauxModele(options=Stop('ia_en_pause'))
        with self.assertRaisesRegex(Stop, 'ia_en_pause'):
            self.lancer(modele)
        m = self.desk.db.execute('SELECT * FROM missions_conseil5689').fetchone()
        self.assertEqual(m['state'], 'suspended')
        self.assertEqual({x['code']: x['state'] for x in conseil.vue(self.desk, m['mission_id'], *AVOCAT)['etapes']}['options'], 'a_faire')

    def test_carte_de_gouvernance_reprise(self):
        modele = FauxModele(options=REPONSE_OPTIONS)
        m, _ = self.lancer(modele)
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertIn('donnees_utilisees', vue['synthese_donnees'])
        self.assertIn('etat', vue['budget'])


CONTRAT = ('Article 1 Définitions', '« Prestation » désigne le service fourni par ZORVAL.', 'Article 2 Prix', 'Le prix de la Prestation est de 10 000 euros.', 'Article 3 Résiliation', 'Chaque partie peut résilier avec un préavis de 30 jours.', 'Article 4 Responsabilité', 'La responsabilité de ZORVAL est illimitée, selon l’article 3 ci-dessus.')
REGLES = [{'cle': 'resiliation', 'clause_type': 'resiliation', 'position': 'Préavis de 90 jours', 'acceptable': 'Préavis de 60 jours', 'repli': 'Préavis de 45 jours', 'motif': 'Sécuriser la trésorerie du client DURAND', 'risque': 'eleve', 'seuil': 'Plancher 45 jours validé par l’associé'},
          {'cle': 'responsabilite', 'clause_type': 'responsabilite', 'position': 'Plafond égal au prix', 'motif': 'Limiter l’exposition', 'risque': 'moyen', 'seuil': ''}]


class Revue(Base):
    def setUp(self):
        super().setUp()
        self.playbook = pb.creer(self.desk, {'domaine': 'prestation', 'juridiction': 'France', 'cote': 'Prestataire ZORVAL', 'regles': REGLES}, *AVOCAT)
        pb.publier(self.desk, self.playbook['id'], *AVOCAT)
        self.v = self.docx('contrat-zorval.docx', *CONTRAT)
        self.brut = self.nc.files[self.v['path']]

    def clause(self, titre):
        return next(c for c in pb.dependances(pb.decouper(list(CONTRAT)))[0] if c['titre'] == titre)['id']

    def reponse(self, **plus):
        return {'propositions': [dict({'clause': self.clause('Article 3 Résiliation'), 'regle': 'resiliation', 'texte_propose': 'Chaque partie peut résilier avec un préavis de 90 jours.', 'motif_externe': 'Un préavis plus long laisse le temps d’organiser la transition.', 'note_interne': 'Ne pas descendre sous 45 jours, confidentiel.', 'risque': 'eleve'}, **plus)]}

    def mission(self, modele, cle='revue-cle-0001'):
        m = conseil.creer(self.desk, {'matter_id': MID, 'question': 'Relire le contrat de prestation avec la société ZORVAL', 'as_of': '2026-11-02', 'advised_side': 'Prestataire ZORVAL', 'expected_output': 'contract_review', 'domaine': 'prestation', 'juridiction': 'France', 'cle': cle, 'document_version': self.v['version_id'], 'source_refs': [{'version_id': self.v['version_id']}]}, *AVOCAT)
        return m, conseil.executer(self.desk, {'mission_id': m['mission_id']}, modele, self.nc)

    def test_playbook_brouillon_jusqua_publication_et_regle_retiree(self):
        brouillon = pb.nouvelle_version(self.desk, self.playbook['id'], {'retirer': ['resiliation']}, *AVOCAT)
        self.assertEqual(brouillon['etat'], 'brouillon')
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], self.playbook['id'])
        m, _ = self.mission(FauxModele(propositions=self.reponse()))
        pb.publier(self.desk, brouillon['id'], *AVOCAT)
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], brouillon['id'])
        self.assertEqual([r['cle'] for r in pb.regles_de(self.desk, brouillon['id'])], ['responsabilite'])
        # l'ancienne mission garde la version qui explique sa proposition
        self.assertEqual(conseil.vue(self.desk, m['mission_id'], *AVOCAT)['playbook_version'], '%s:1' % self.playbook['id'])
        self.assertEqual([r['cle'] for r in pb.regles_de(self.desk, self.playbook['id'])], ['resiliation', 'responsabilite'])
        # une nouvelle mission n'utilise plus la règle retirée
        modele = FauxModele(propositions=self.reponse())
        m2, _ = self.mission(modele, 'revue-cle-0002')
        self.assertEqual([r['cle'] for r in modele.charges[0]['playbook']], ['responsabilite'])
        self.assertEqual(conseil.vue(self.desk, m2['mission_id'], *AVOCAT)['revue']['propositions'], [])

    def test_mission_refusee_sans_playbook_publie(self):
        with self.assertRaisesRegex(Stop, 'playbook_non_publie'):
            conseil.creer(self.desk, {'matter_id': MID, 'question': 'Relire ce contrat', 'as_of': '2026-11-02', 'advised_side': 'Inconnu', 'expected_output': 'contract_review', 'domaine': 'bail', 'juridiction': 'France', 'cle': 'revue-cle-0003'}, *AVOCAT)

    def test_clauses_identifiants_stables_dependances_et_incoherences(self):
        clauses, incoherences = pb.dependances(pb.decouper(list(CONTRAT)))
        renum = pb.decouper(['Article 9 Prix', 'Le prix est modifié.'])
        self.assertEqual(renum[0]['id'], self.clause('Article 2 Prix'))     # une renumérotation ne change pas l'identifiant
        prix = next(c for c in clauses if c['titre'] == 'Article 2 Prix')
        resp = next(c for c in clauses if c['titre'] == 'Article 4 Responsabilité')
        self.assertIn(self.clause('Article 1 Définitions'), prix['depend_de'])
        self.assertIn(self.clause('Article 3 Résiliation'), resp['depend_de'])
        _, autres = pb.dependances(pb.decouper(['Article 1 Prix', 'Le prix est de 10 000 euros.', 'Article 2 Paiement du prix', 'Le prix de 12 000 euros est payé. Voir l’article 8.']))
        self.assertEqual({x['genre'] for x in autres}, {'renvoi_sans_cible', 'montants_divergents'})

    def test_AV05_chaque_proposition_a_sa_clause_et_sa_regle_du_playbook_applicable(self):
        reponse = self.reponse()
        reponse['propositions'].append({'clause': self.clause('Article 4 Responsabilité'), 'regle': 'responsabilite', 'texte_propose': 'La responsabilité de ZORVAL est plafonnée au prix.', 'motif_externe': 'Équilibre du contrat.', 'note_interne': 'Point sensible.'})
        modele = FauxModele(propositions=reponse)
        m, _ = self.mission(modele)
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)['revue']
        identifiants = {c['id'] for c in vue['clauses']}
        regles = {r['cle']: r for r in pb.regles_de(self.desk, self.playbook['id'])}
        self.assertEqual(len(vue['propositions']), 2)
        for p in vue['propositions']:
            self.assertIn(p['clause_id'], identifiants)
            self.assertEqual(p['position'], regles[p['regle']]['position'])
            self.assertIn(p['texte_propose'], vue['message'])
            self.assertTrue(any(x['texte_propose'] == p['texte_propose'] for x in vue['negociation']))
        plan = pb.vue_word(pb.propositions(self.desk, vue['id']))
        self.assertEqual({e['text'] for e in plan['edits']}, {p['texte_propose'] for p in vue['propositions']})

    def test_IMP03B_word_regle_et_message_concordent_notes_internes_non_transmises(self):
        reponse = self.reponse()
        reponse['propositions'].append({'clause': 'c-inventee', 'regle': 'resiliation', 'texte_propose': 'x', 'motif_externe': ''})
        reponse['propositions'].append({'clause': self.clause('Article 4 Responsabilité'), 'regle': 'regle-inventee', 'texte_propose': 'x'})
        m, _ = self.mission(FauxModele(propositions=reponse))
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        revue = vue['revue']
        self.assertEqual(vue['state'], 'ready_for_review')
        self.assertEqual(len(revue['propositions']), 1)
        p = revue['propositions'][0]
        self.assertEqual((p['regle'], p['position']), ('resiliation', 'Préavis de 90 jours'))
        self.assertIn(self.clause('Article 4 Responsabilité'), p['impacts'])      # la clause qui renvoie à celle-ci
        documents = [liv.charger(self.desk, d) for d in vue['livrables']]
        self.assertEqual(len(documents), 3)
        word, tableau, message = [self.nc.files[d['path']] for d in documents]
        texte = 'Chaque partie peut résilier avec un préavis de 90 jours.'
        self.assertEqual(revue['negociation'][0]['texte_propose'], texte)
        self.assertIn(texte, revue['message'])
        import zipfile
        self.assertIn(texte, zipfile.ZipFile(io.BytesIO(word)).read('word/document.xml').decode('utf-8'))
        self.assertIn('w:ins', zipfile.ZipFile(io.BytesIO(word)).read('word/document.xml').decode('utf-8'))      # modification suivie
        self.assertIn(texte, ' '.join(verification5689.paragraphes(tableau)))
        self.assertIn(texte, ' '.join(verification5689.paragraphes(message)))
        interdits = ('Ne pas descendre', 'Plancher 45', 'Préavis de 45 jours', 'Préavis de 60 jours', 'trésorerie du client')
        tout = ' '.join(verification5689.paragraphes(tableau) + verification5689.paragraphes(message)) + revue['message'] + json.dumps(revue['negociation'], ensure_ascii=False)
        for mot in interdits:
            self.assertNotIn(mot, tout)
        self.assertEqual(self.nc.files[self.v['path']], self.brut)      # l'original n'est pas touché
        self.assertTrue(any('écartée' in x for x in vue['limites']))

    def test_motif_du_modele_recopiant_le_seuil_retire_et_signale(self):
        m, _ = self.mission(FauxModele(propositions=self.reponse(motif_externe='Notre plancher 45 jours validé par l’associé.')))
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)
        self.assertEqual(vue['revue']['propositions'][0]['motif_externe'], '')
        self.assertNotIn('Plancher', vue['revue']['message'])
        self.assertTrue(any('donnée interne' in x for x in vue['limites']))

    def test_message_bloque_si_une_donnee_interne_y_passe_quand_meme(self):
        revue = {'playbook_id': self.playbook['id']}
        ligne = {'regle_cle': 'resiliation', 'note_interne': 'Ne pas descendre sous 45 jours'}
        with self.assertRaisesRegex(Stop, 'donnee_interne_dans_le_message'):
            pb.controle_sortie(self.desk, revue, [ligne], 'Texte : ne pas descendre sous 45 jours.')

    def test_IMP03C_clause_modifiee_par_l_avocat_non_ecrasee_choix_demande(self):
        m, _ = self.mission(FauxModele(propositions=self.reponse()))
        modifie = list(CONTRAT)
        modifie[5] = 'Chaque partie peut résilier avec un préavis de 120 jours, négocié par l’avocat.'
        raw = texte_docx(*modifie)
        self.nc.files[self.v['path']] = raw
        out = pb.actualiser(self.desk, m['mission_id'], *AVOCAT, client=self.nc)
        self.assertTrue(out['modifie'])
        conflit = out['conflits'][0]
        self.assertEqual(conflit['texte_actuel'], [modifie[5]])
        self.assertEqual(len(conflit['choix']), 3)
        self.assertEqual(self.nc.files[self.v['path']], raw)
        vue = conseil.vue(self.desk, m['mission_id'], *AVOCAT)['revue']
        self.assertEqual(vue['negociation'], [])                           # une clause en conflit ne sort plus
        p = vue['propositions'][0]
        self.assertEqual(p['state'], 'conflit')
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            pb.resoudre(self.desk, p['id'], 'garder', *AVOCAT, p['revision'] - 1)
        self.assertEqual(pb.resoudre(self.desk, p['id'], 'garder', *AVOCAT, p['revision'])['state'], 'refusee')

    def test_clause_inchangee_reste_valable_quand_une_autre_change(self):
        m, _ = self.mission(FauxModele(propositions=self.reponse()))
        modifie = list(CONTRAT)
        modifie[3] = 'Le prix de la Prestation est de 11 000 euros.'
        self.nc.files[self.v['path']] = texte_docx(*modifie)
        self.assertEqual(pb.actualiser(self.desk, m['mission_id'], *AVOCAT, client=self.nc)['conflits'], [])

    def test_vers_le_registre_passe_par_les_propositions_et_detecte_l_edition(self):
        from agent import propositions5689 as patches
        item = liv.reserve(self.desk, 'test', 'contrat', 'v1', matter=MID, kind='contrat', title='Contrat ZORVAL', path=self.v['path'], sha256=self.v['sha256'], owner='avocate')
        item = liv.constater(self.desk, item, self.nc, self.v['path'])
        m, _ = self.mission(FauxModele(propositions=self.reponse()))
        patch_ = pb.vers_livrable(self.desk, m['mission_id'], item['document_id'], 'cle-vers-livrable', *AVOCAT, client=self.nc)
        self.assertEqual(patch_['state'], 'proposee')                      # rien n'est appliqué sans décision
        self.assertEqual(self.nc.files[self.v['path']], self.brut)
        self.nc.replace_file(self.v['path'], texte_docx('Article 3 Résiliation', 'Texte de l’avocat.'), self.nc.etags[self.v['path']])
        with self.assertRaises(Stop):
            patches.decider(self.desk, patch_['id'], 'accepter', 'avocate', 'avocat', client=self.nc)
        self.assertEqual(verification5689.paragraphes(self.nc.files[self.v['path']])[1], 'Texte de l’avocat.')

    def test_correction_propose_une_regle_a_approuver_sans_entrainement(self):
        modele = FauxModele()
        ident = pb.proposer_regle(self.desk, self.playbook['id'], {'clause_type': 'prix', 'texte': 'Indexer le prix chaque année', 'portee': 'cabinet', 'matter': MID}, *AVOCAT)
        self.assertEqual([r['cle'] for r in pb.regles_de(self.desk, self.playbook['id'])], ['resiliation', 'responsabilite'])
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], self.playbook['id'])
        out = pb.decider_regle(self.desk, ident, 'approuver', *AVOCAT, 'prix')
        self.assertEqual(out['brouillon']['etat'], 'brouillon')
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], self.playbook['id'])      # toujours pas active
        self.assertEqual(modele.appels, [])
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            pb.decider_regle(self.desk, ident, 'approuver', *ASSISTANT)


OBLIGATIONS = {'obligations': [{'clause': 'C2', 'intitule': 'Payer le loyer', 'responsable': 'DURAND', 'passage': 'Le loyer est payé chaque mois', 'recurrence': 'mensuelle', 'delai': 1, 'unite': 'mois'},
                               {'clause': 'C3', 'intitule': 'Restituer les clés', 'responsable': 'DURAND', 'passage': 'les clés sont restituées', 'declencheur': 'Fin du bail', 'delai': 15, 'unite': 'jours'},
                               {'clause': 'C99', 'intitule': 'Inventée', 'responsable': 'X', 'passage': 'rien'}]}
SIGNE = ('Article 1 Parties', 'Entre DURAND et ZORVAL.', 'Article 2 Loyer', 'Le loyer est payé chaque mois à ZORVAL.', 'Article 3 Fin', 'En fin de bail, les clés sont restituées sous quinze jours.')


class Accord(Base):
    def accord(self, **plus):
        v = self.docx('bail signé.docx', *plus.pop('texte', SIGNE))
        return v, accords.creer(self.desk, MID, {'version_id': v['version_id'], 'date_signature': '2026-01-31', 'parties': ['DURAND', 'ZORVAL'], 'preuve_nature': 'attestation_avocat', 'attestation': True, 'cle': plus.pop('cle', 'accord-cle-0001'), **plus}, *AVOCAT, client=self.nc)

    def test_signature_prouvee_le_nom_du_fichier_ne_suffit_pas(self):
        v = self.docx('contrat SIGNÉ définitif.docx', *SIGNE)
        base = {'version_id': v['version_id'], 'date_signature': '2026-01-31', 'parties': ['DURAND', 'ZORVAL'], 'cle': 'accord-cle-0002'}
        with self.assertRaisesRegex(Stop, 'preuve_signature_requise'):
            accords.creer(self.desk, MID, base, *AVOCAT, client=self.nc)
        with self.assertRaisesRegex(Stop, 'preuve_signature_requise'):
            accords.creer(self.desk, MID, {**base, 'preuve_nature': 'prestataire'}, *AVOCAT, client=self.nc)
        with self.assertRaisesRegex(Stop, 'attestation_explicite_requise'):
            accords.creer(self.desk, MID, {**base, 'preuve_nature': 'attestation_avocat'}, *AVOCAT, client=self.nc)
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            accords.creer(self.desk, MID, {**base, 'preuve_nature': 'prestataire', 'preuve_ref': 'enveloppe-1'}, *ASSISTANT, client=self.nc)
        self.nc.files[v['path']] = texte_docx('Autre texte')
        with self.assertRaisesRegex(Stop, 'empreinte_source_differente'):
            accords.creer(self.desk, MID, {**base, 'preuve_nature': 'prestataire', 'preuve_ref': 'enveloppe-1'}, *AVOCAT, client=self.nc)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM accords5689').fetchone()[0], 0)

    def test_IMP03D_seule_la_version_signee_alimente_le_suivi_sans_doublon(self):
        v, a = self.accord()
        modele = FauxModele(obligations=OBLIGATIONS)
        out = accords.extraire(self.desk, a['id'], *AVOCAT, modele, self.nc)
        self.assertEqual((out['obligations'], out['ecartees']), (2, {'clause_non_fournie': 1}))
        premier = self.desk.db.execute('SELECT COUNT(*) FROM occurrences5689').fetchone()[0]
        self.assertGreater(premier, 2)
        accords.extraire(self.desk, a['id'], *AVOCAT, modele, self.nc)
        accords.planifier(self.desk, a['id'], 'avocate')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM occurrences5689').fetchone()[0], premier)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM obligations5689').fetchone()[0], 2)
        self.assertEqual({r[0] for r in self.desk.db.execute('SELECT version_id FROM occurrences5689')}, {v['version_id']})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)       # proposition, pas d'inscription
        occ = accords.occurrences(self.desk, a['id'])
        self.assertEqual(occ[0]['echeance'], '2026-02-28')                  # 31 janvier + 1 mois : fin de mois déterministe
        self.assertEqual(occ[0]['parametres'], {'base': '2026-01-31', 'delai': 1, 'unite': 'mois', 'methode': 'calendaire'})
        self.assertEqual(occ[1]['echeance'], '2026-03-28')
        accords.decider(self.desk, occ[0]['id'], 'inscrire', *AVOCAT)
        accords.decider(self.desk, occ[0]['id'], 'inscrire', *AVOCAT)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE source='accord5689'").fetchone()[0], 1)

    def test_conditionnelle_attend_son_evenement_documente(self):
        v, a = self.accord()
        accords.extraire(self.desk, a['id'], *AVOCAT, FauxModele(obligations=OBLIGATIONS), self.nc)
        o = self.desk.db.execute("SELECT * FROM obligations5689 WHERE intitule='Restituer les clés'").fetchone()
        self.assertEqual(o['statut'], 'en_attente_evenement')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM occurrences5689 WHERE obligation=?', (o['id'],)).fetchone()[0], 0)
        with self.assertRaisesRegex(Stop, 'evenement_non_documente'):
            accords.declarer_evenement(self.desk, o['id'], {'date': '2026-06-30'}, *AVOCAT)
        preuve = self.docx('conge.docx', 'Fin du bail notifiée.', categorie='correspondance')
        accords.declarer_evenement(self.desk, o['id'], {'date': '2026-06-30', 'source_ref': A.source_ref(preuve, 'paragraph', '1')}, *AVOCAT)
        ligne = self.desk.db.execute('SELECT echeance FROM occurrences5689 WHERE obligation=?', (o['id'],)).fetchone()
        self.assertEqual(ligne['echeance'], '2026-07-15')

    def test_calcul_deterministe(self):
        self.assertEqual(accords.calculer({'delai': 12, 'unite': 'mois'}, '2024-02-29')[0], '2025-02-28')
        self.assertEqual(accords.calculer({'delai': 30, 'unite': 'jours'}, '2026-01-01')[0], '2026-01-31')
        self.assertEqual(accords.calculer({'delai': 2, 'unite': 'ans'}, '2026-03-31')[0], '2028-03-31')
        with self.assertRaisesRegex(Stop, 'regle_calcul_invalide'):
            accords.calculer({'delai': 1, 'unite': 'lunes'}, '2026-01-01')

    def test_avenant_propose_une_mise_a_jour_sans_dupliquer(self):
        v, a = self.accord()
        modele = FauxModele(obligations=OBLIGATIONS)
        accords.extraire(self.desk, a['id'], *AVOCAT, modele, self.nc)
        premiere = accords.occurrences(self.desk, a['id'])[0]
        accords.decider(self.desk, premiere['id'], 'inscrire', *AVOCAT)
        taches = self.desk.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        v2 = self.docx('bail signé.docx', 'Article 1 Parties', 'Entre DURAND et ZORVAL.', 'Article 2 Loyer', 'Le loyer est payé chaque mois à ZORVAL, avenant.', 'Article 3 Fin', 'En fin de bail, les clés sont restituées sous quinze jours.')
        b = accords.creer(self.desk, MID, {'version_id': v2['version_id'], 'date_signature': '2026-03-15', 'parties': ['DURAND', 'ZORVAL'], 'preuve_nature': 'prestataire', 'preuve_ref': 'enveloppe-2', 'cle': 'accord-cle-0003'}, *AVOCAT, client=self.nc)
        self.assertEqual(accords.vue(self.desk, a['id'])['statut'], 'remplace')
        accords.extraire(self.desk, b['id'], *AVOCAT, modele, self.nc)
        occ = accords.occurrences(self.desk, b['id'])
        maj = next(x for x in occ if x['numero'] == 1)
        self.assertEqual(maj['etat'], 'mise_a_jour')
        self.assertEqual(maj['remplace'], premiere['id'])
        self.assertIn('2026-02-28', maj['motif'])
        accords.decider(self.desk, maj['id'], 'inscrire', *AVOCAT)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], taches)      # la tâche existante se met à jour
        self.assertEqual(self.desk.db.execute("SELECT due FROM tasks").fetchone()[0], maj['echeance'])
        self.assertEqual(self.desk.db.execute('SELECT etat FROM occurrences5689 WHERE id=?', (premiere['id'],)).fetchone()[0], 'remplacee')

    def test_AV06_changement_de_document_ou_droits_invalident_les_propositions(self):
        v, a = self.accord()
        accords.extraire(self.desk, a['id'], *AVOCAT, FauxModele(obligations=OBLIGATIONS), self.nc)
        occ = accords.occurrences(self.desk, a['id'])
        accords.decider(self.desk, occ[0]['id'], 'inscrire', *AVOCAT)
        self.assertEqual(accords.revalider(self.desk, a['id'], *AVOCAT, self.nc)['invalidees'], 0)
        self.nc.files[v['path']] = texte_docx('Texte changé après signature')
        out = accords.revalider(self.desk, a['id'], *AVOCAT, self.nc)
        self.assertGreater(out['invalidees'], 0)
        self.assertEqual(out['statut'], 'a_recontroler')
        etats = {x['etat'] for x in accords.occurrences(self.desk, a['id'])}
        self.assertEqual(etats, {'acceptee', 'perimee'})                    # l'inscription décidée reste, les propositions tombent
        with self.assertRaisesRegex(Stop, 'occurrence_perimee'):
            accords.decider(self.desk, occ[1]['id'], 'inscrire', *AVOCAT)
        with self.assertRaisesRegex(Stop, 'version_signee_modifiee'):
            accords.extraire(self.desk, a['id'], *AVOCAT, FauxModele(obligations=OBLIGATIONS), self.nc)

    def test_AV06_droits_retires_invalident(self):
        v, a = self.accord()
        accords.extraire(self.desk, a['id'], *AVOCAT, FauxModele(obligations=OBLIGATIONS), self.nc)
        occ = accords.occurrences(self.desk, a['id'])
        reel = membres5650.hidden_for

        def filtre(state_dir, owner, role):
            return frozenset((MID,)) if owner == 'avocate@example.test' else reel(state_dir, owner, role)
        with patch('agent.membres5650.hidden_for', side_effect=filtre):
            out = accords.revalider(self.desk, a['id'], 'admin@example.test', 'administrateur', self.nc)
        self.assertGreater(out['invalidees'], 0)
        with self.assertRaisesRegex(Stop, 'occurrence_perimee'):
            accords.decider(self.desk, occ[0]['id'], 'inscrire', *AVOCAT)


class Routes(Base):
    def test_routes_declarees_roles_et_inconnues(self):
        self.assertTrue(web5689.allowed('assistant', 'dossiers/DOS-001/assertions'))
        self.assertFalse(web5689.allowed('assistant', 'assertions/abc/validation'))
        self.assertTrue(web5689.allowed('avocat', 'assertions/abc/validation'))
        self.assertFalse(web5689.allowed('avocat', 'assertions/abc/supprimer'))
        self.assertFalse(web5689.allowed('invite', 'missions'))
        self.assertTrue(web5689.allowed('avocat', 'accords/abc/obligations'))
        self.assertFalse(web5689.allowed('assistant', 'playbooks'))
        self.assertEqual(membres5650.category('POST', '/api440/m5689/assertions/abc/validation'), 'valider')
        self.assertEqual(membres5650.category('POST', '/api440/m5689/playbooks/abc/publier'), 'valider_modele')

    def test_flux_elements_par_les_routes_et_conflit_409(self):
        r = self.appel('dossiers/DOS-001/assertions', {'kind': 'metadata', 'statement': 'Tribunal de Valbrune', 'asserted_by': 'Avocat', 'cle': 'route-ajout-1'})
        vue = json.loads(r['body'])
        self.assertEqual(vue['status'], 'proposed')
        out = self.appel('assertions/%s/validation' % vue['assertion_id'], {'action': 'valider', 'revision': 1, 'cle': 'route-valide-1'}, ASSISTANT)
        self.assertTrue(out['status'].startswith('403') or out['status'].startswith('404'))
        out = self.appel('assertions/%s/validation' % vue['assertion_id'], {'action': 'valider', 'revision': 1, 'cle': 'route-valide-2'})
        self.assertTrue(out['status'].startswith('200'))
        out = self.appel('assertions/%s/validation' % vue['assertion_id'], {'action': 'contester', 'revision': 1, 'cle': 'route-valide-3'})
        self.assertTrue(out['status'].startswith('409'))
        lue = json.loads(self.appel('dossiers/DOS-001/assertions')['body'])
        self.assertEqual(lue['assertions'][0]['status'], 'validated')
        self.assertIn('pas sa vérité', lue['avis'])

    def test_csrf_exige_et_role_vient_de_la_requete(self):
        raw = b'{}'
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://autre.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_LENGTH': '2', 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        out = web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/dossiers/DOS-001/assertions', {}, 'POST')
        self.assertIn('origine_ou_csrf_refuse', out['body'])

    def test_dossier_reserve_repond_403(self):
        with patch('agent.membres5650.hidden_for', return_value=frozenset((MID,))):
            out = self.appel('dossiers/DOS-001/projection', who=ASSISTANT)
        self.assertTrue(out['status'].startswith('403'))

    def test_mission_par_la_route_rend_un_suivi(self):
        self.docx('source.docx', 'Texte.')
        out = self.appel('missions', {'matter_id': MID, 'question': 'Peut-on résilier ce contrat ?', 'as_of': '2026-11-02', 'advised_side': 'Monsieur DURAND', 'cle': 'route-mission-0001', 'expected_output': 'consultation'})
        vue = json.loads(out['body'])
        self.assertIn(vue['state'], ('planned', 'running'))
        self.assertEqual(json.loads(self.appel('missions/' + vue['mission_id'])['body'])['mission_id'], vue['mission_id'])

    def test_travaux_declares_et_acheminement(self):
        from agent import desk as module_desk
        for kind in ('extraction_dossier5689', 'mission_conseil5689', 'obligations5689'):
            self.assertIn(kind, module_desk.JOBS)
            self.assertIn(kind, module_desk.USER_JOBS)
        with patch.object(conseil, 'executer', return_value={'status': 'prepared'}) as appel:
            self.assertEqual(self.desk.perform('mission_conseil5689', {'mission_id': 'x'}), {'status': 'prepared'})
        appel.assert_called_once()
        with patch.object(ex, 'executer', return_value={'status': 'prepared'}) as appel:
            self.desk.perform('extraction_dossier5689', {'run': 'x'})
        appel.assert_called_once()

    def test_css_et_script_declares(self):
        from agent.shell501 import BUNDLE
        from agent.web440 import STATIC
        self.assertIn('v5689-dossier.css', BUNDLE)
        self.assertIn('/static/v5689-dossier.js', STATIC)


class LecteurFormulaires(HTMLParser):
    """Lit les formulaires d'une page comme un navigateur : API, champs et valeurs par défaut, libellé du bouton."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.formulaires, self.courant, self.zone, self.select, self.bouton = [], None, None, None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self.courant = {'classe': a.get('class', ''), 'api': a.get('data-api', ''), 'recharge': 'data-reload' in a, 'champs': {}, 'bouton': ''}
            self.formulaires.append(self.courant)
        elif self.courant is None:
            return
        elif tag == 'input' and a.get('name'):
            self.courant['champs'][a['name']] = (False if a.get('type') == 'checkbox' else a.get('value', ''))
        elif tag == 'textarea':
            self.zone = a['name']
            self.courant['champs'][self.zone] = ''
        elif tag == 'select':
            self.select = a['name']
        elif tag == 'option' and self.select and self.select not in self.courant['champs']:
            self.courant['champs'][self.select] = a.get('value', '')
        elif tag == 'button':
            self.bouton = True

    def handle_endtag(self, tag):
        if tag == 'form':
            self.courant = None
        elif tag == 'textarea':
            self.zone = None
        elif tag == 'select':
            self.select = None
        elif tag == 'button':
            self.bouton = False

    def handle_data(self, data):
        if self.zone:
            self.courant['champs'][self.zone] += data
        elif self.bouton and self.courant is not None:
            self.courant['bouton'] += data


class Ecrans(Base):
    """Le parcours d'un avocat : ce que la page propose, envoyé tel quel à la route, et le résultat métier lu ensuite."""

    def formulaires(self, html):
        lecteur = LecteurFormulaires()
        lecteur.feed(html)
        return lecteur.formulaires

    def bibliotheque(self):
        return bibliotheque5639.page_html(self.desk, '/p', {})

    def conseil(self):
        return dossier5624.render(self.desk, '/p', MID, 'conseil', dossier5624.rubrique(self.desk, MID, 'conseil'))

    def envoyer(self, html, bouton, attendu='200', who=AVOCAT, **valeurs):
        trouve = [f for f in self.formulaires(html) if bouton in f['bouton']]
        self.assertTrue(trouve, bouton)
        f = trouve[0]
        self.assertIn('m5-form', f['classe'])
        self.assertTrue(f['recharge'])
        out = self.appel(f['api'][len('m5689/'):], {**f['champs'], **valeurs}, who)
        self.assertTrue(out['status'].startswith(attendu), out['body'][:300])
        return json.loads(out['body'])

    def playbook_publie(self):
        self.envoyer(self.bibliotheque(), 'Créer le brouillon', domaine='prestation', juridiction='France', cote='Prestataire ZORVAL')
        self.envoyer(self.bibliotheque(), 'Ajouter la règle', regle_cle='resiliation', clause_type='resiliation', position='Préavis de 90 jours', repli='Préavis de 45 jours', motif='Sécuriser la trésorerie', seuil='Plancher 45 jours', risque='eleve')
        self.envoyer(self.bibliotheque(), 'Ajouter la règle', regle_cle='responsabilite', clause_type='responsabilite', position='Plafond égal au prix')
        return self.envoyer(self.bibliotheque(), 'Publier cette version')

    def test_page_sans_script_ni_style_en_ligne(self):
        self.playbook_publie()
        for html in (self.bibliotheque(), self.conseil()):
            self.assertNotIn('<script', html)
            self.assertNotRegex(html, r'\son[a-z]+=')
            self.assertNotRegex(html[html.find('id="playbooks"'):] if 'id="playbooks"' in html else html, r'\sstyle=')
            for f in self.formulaires(html):
                if f['api']:
                    self.assertTrue(f['api'].startswith(('m5689/', 'm5636/', 'm5684/')))

    def test_playbook_cree_regles_publication_et_nouvelle_version_depuis_la_bibliotheque(self):
        publie = self.playbook_publie()
        self.assertEqual((publie['etat'], publie['version']), ('publie', 1))
        self.assertEqual([r['cle'] for r in publie['regles']], ['resiliation', 'responsabilite'])
        page = self.bibliotheque()
        self.assertIn('Publié', page)
        self.assertNotIn('Ajouter la règle', page)                        # une version publiée est figée
        with self.assertRaises(AssertionError):
            self.envoyer(page, 'Publier cette version')
        out = self.appel('playbooks/%s/regle' % publie['id'], {'regle_cle': 'x', 'clause_type': 'prix', 'position': 'y'})
        self.assertTrue(out['status'].startswith('409'))
        brouillon = self.envoyer(page, 'Retirer cette règle (nouvelle version)')
        self.assertEqual((brouillon['etat'], brouillon['version']), ('brouillon', 2))
        self.assertEqual([r['cle'] for r in brouillon['regles']], ['responsabilite'])
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], publie['id'])
        publiee = self.envoyer(self.bibliotheque(), 'Publier cette version')
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], publiee['id'])
        self.assertEqual(len(pb.regles_de(self.desk, publie['id'])), 2)    # l'ancienne version reste intacte

    def test_creation_de_playbook_rejouee_ne_cree_qu_un_brouillon(self):
        corps = {'domaine': 'bail', 'juridiction': 'France', 'cote': 'Bailleur', 'cle': 'playbook-cle-0001'}
        self.appel('playbooks', corps)
        self.appel('playbooks', corps)
        self.assertEqual(len(pb.lister(self.desk)), 1)
        self.assertTrue(self.appel('playbooks', {'domaine': 'bail', 'juridiction': 'France', 'cote': 'Bailleur'})['status'].startswith('400'))

    def test_proposition_de_regle_approuvee_ou_refusee_depuis_la_bibliotheque(self):
        publie = self.playbook_publie()
        pb.proposer_regle(self.desk, publie['id'], {'clause_type': 'prix', 'texte': 'Indexer le prix chaque année', 'portee': 'cabinet'}, *AVOCAT)
        pb.proposer_regle(self.desk, publie['id'], {'clause_type': 'delai', 'texte': 'Refuser tout délai inférieur à dix jours', 'portee': 'dossier', 'matter': MID}, *AVOCAT)
        page = self.bibliotheque()
        self.assertIn('Indexer le prix chaque année', page)
        out = self.envoyer(page, 'Approuver dans une nouvelle version', cle_regle='indexation')
        self.assertEqual(out['brouillon']['etat'], 'brouillon')
        self.assertIn('indexation', [r['cle'] for r in out['brouillon']['regles']])
        self.assertEqual(pb.applicable(self.desk, 'prestation', 'France', 'Prestataire ZORVAL')['id'], publie['id'])
        self.envoyer(self.bibliotheque(), 'Refuser')
        self.assertEqual(pb.propositions_regles(self.desk), [])
        self.assertEqual({r[0] for r in self.desk.db.execute('SELECT state FROM playbook_propositions5689')}, {'approuvee', 'refusee'})

    def test_proposition_d_un_dossier_reserve_absente_de_la_page(self):
        publie = self.playbook_publie()
        pb.proposer_regle(self.desk, publie['id'], {'clause_type': 'prix', 'texte': 'Proposition réservée au dossier', 'portee': 'dossier', 'matter': MID}, *AVOCAT)
        from agent.conseil_ui5689 import playbooks_html
        self.assertNotIn('Proposition réservée', playbooks_html(self.desk, *ASSISTANT, frozenset((MID,))))

    def test_assistant_voit_sans_pouvoir_decider(self):
        publie = self.playbook_publie()
        v = self.docx('contrat-zorval.docx', *CONTRAT)
        membres5650.set_viewer_role('assistant')
        self.addCleanup(membres5650.set_viewer_role, '')
        membres5650.set_author(ASSISTANT[0])
        self.addCleanup(membres5650.set_author, '')
        page = self.bibliotheque()
        self.assertIn('Plafond égal au prix', page)
        for libelle in ('Créer le brouillon', 'Créer une nouvelle version', 'Retirer cette règle'):
            self.assertNotIn(libelle, page)
        conseil_page = self.conseil()
        self.assertIn('Préparer', conseil_page)                           # préparer reste possible
        self.assertNotIn('Enregistrer l’accord', conseil_page)
        self.assertTrue(self.appel('playbooks/%s/version' % publie['id'], {'cle': 'cle-assistant-1'}, ASSISTANT)['status'].startswith('403'))
        self.assertEqual(len(pb.lister(self.desk)), 1)
        refus = self.appel('accords', {'dossier': MID, 'version_id': v['version_id'], 'preuve_nature': 'attestation_avocat', 'attestation': True, 'cle': 'cle-assistant-2'}, ASSISTANT)
        self.assertTrue(refus['status'].startswith('403'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM accords5689').fetchone()[0], 0)

    def lancer_revue(self, reponse):
        publie = self.playbook_publie()
        self.v = self.docx('contrat-zorval.docx', *CONTRAT)
        self.brut = self.nc.files[self.v['path']]
        vue = self.envoyer(self.conseil(), 'Préparer', question='Relire le contrat de prestation', advised_side='Prestataire ZORVAL', expected_output='contract_review', playbook_version='%s:%d' % (publie['id'], publie['version']), document_version=self.v['version_id'])
        self.assertEqual(vue['mission_type'], 'contract_review')
        conseil.executer(self.desk, {'mission_id': vue['mission_id']}, FauxModele(propositions=reponse), self.nc)
        return vue['mission_id']

    def clause(self, titre):
        return next(c for c in pb.dependances(pb.decouper(list(CONTRAT)))[0] if c['titre'] == titre)['id']

    def deux_propositions(self):
        return {'propositions': [{'clause': self.clause('Article 3 Résiliation'), 'regle': 'resiliation', 'texte_propose': 'Chaque partie peut résilier avec un préavis de 90 jours.', 'motif_externe': 'Laisser le temps d’organiser la transition.', 'note_interne': 'Ne pas descendre sous 45 jours.'},
                                 {'clause': self.clause('Article 4 Responsabilité'), 'regle': 'responsabilite', 'texte_propose': 'La responsabilité est plafonnée au prix.', 'motif_externe': 'Équilibre du contrat.', 'note_interne': 'Point sensible.'}]}

    def test_revue_lancee_depuis_l_onglet_et_decidee_proposition_par_proposition(self):
        mission = self.lancer_revue(self.deux_propositions())
        page = self.conseil()
        self.assertIn('Ne pas descendre sous 45 jours.', page)            # visible de l'avocat, marqué interne
        self.assertIn('Interne', page)
        self.envoyer(page, 'Accepter ce texte', texte='Chaque partie peut résilier avec un préavis de 75 jours.')
        self.envoyer(self.conseil(), 'Refuser')
        vue = conseil.vue(self.desk, mission, *AVOCAT)['revue']
        etats = {p['regle']: (p['state'], p['texte_propose']) for p in vue['propositions']}
        self.assertEqual(etats['resiliation'], ('acceptee', 'Chaque partie peut résilier avec un préavis de 75 jours.'))
        self.assertEqual(etats['responsabilite'][0], 'refusee')
        documents = self.envoyer(self.conseil(), 'Reporter les propositions acceptées')['documents']
        self.assertEqual(len(documents), 3)
        contenus = ' '.join(' '.join(verification5689.paragraphes(self.nc.files[liv.charger(self.desk, d)['path']])) for d in documents[1:])
        self.assertIn('75 jours', contenus)
        self.assertNotIn('plafonnée au prix', contenus)                   # une proposition refusée quitte le tableau et le message
        self.assertNotIn('Ne pas descendre', contenus)
        self.assertEqual(self.nc.files[self.v['path']], self.brut)        # l'original n'est pas touché
        cartes = self.desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE source_service='playbook5689' AND lifecycle='current'").fetchone()[0]
        self.assertEqual(cartes, 3)                                       # nouvelles versions, pas de nouvelles cartes
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE source_service='playbook5689' AND lifecycle='replaced'").fetchone()[0], 3)
        self.assertEqual(conseil.vue(self.desk, mission, *AVOCAT)['livrables'], documents)
        self.assertIn('/p/livrables/' + documents[0], self.conseil())

    def test_decision_perimee_409_et_report_sans_acceptation_refuse(self):
        mission = self.lancer_revue(self.deux_propositions())
        page = self.conseil()
        self.assertNotIn('Reporter les propositions acceptées', page)
        self.envoyer(page, 'Accepter')
        self.envoyer(page, 'Accepter', '409')                             # même page rejouée : proposition déjà décidée
        revue = conseil.vue(self.desk, mission, *AVOCAT)['revue']
        self.assertTrue(self.appel('missions/%s/reporter' % mission, {'revision': revue['revision'] - 1})['status'].startswith('409'))
        p = next(x for x in revue['propositions'] if x['state'] == 'proposee')
        self.assertTrue(self.appel('missions/propositions/%s/decider' % p['id'], {'decision': 'refuser', 'revision': p['revision']}, ASSISTANT)['status'].startswith('403'))
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            pb.decider_proposition(self.desk, p['id'], {'decision': 'refuser', 'revision': p['revision']}, *ASSISTANT)     # le service refait le contrôle de la route
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            pb.reporter_decisions(self.desk, mission, {'revision': revue['revision']}, *ASSISTANT)
        self.assertEqual(self.desk.db.execute("SELECT state FROM revue_propositions5689 WHERE id=?", (p['id'],)).fetchone()[0], 'proposee')

    def test_conflit_presente_a_l_ecran_avec_ses_choix(self):
        mission = self.lancer_revue(self.deux_propositions())
        modifie = list(CONTRAT)
        modifie[5] = 'Chaque partie peut résilier avec un préavis de 120 jours.'
        self.nc.files[self.v['path']] = texte_docx(*modifie)
        out = self.envoyer(self.conseil(), 'Relire le contrat dans le dossier')
        self.assertEqual(len(out['conflits']), 1)
        page = self.conseil()
        self.assertIn('Clause modifiée après l’analyse', page)
        self.assertIn('120 jours', page)
        self.assertIn('Reprendre', page)
        self.envoyer(page, 'Garder le texte de l’avocat')
        revue = conseil.vue(self.desk, mission, *AVOCAT)['revue']
        self.assertEqual(revue['conflits'], [])
        self.assertEqual(next(p for p in revue['propositions'] if p['regle'] == 'resiliation')['state'], 'refusee')

    def test_proposer_une_regle_depuis_une_proposition(self):
        self.lancer_revue(self.deux_propositions())
        self.envoyer(self.conseil(), 'Soumettre à approbation', portee='cabinet', texte='Préavis de 90 jours minimum')
        self.assertIn('Préavis de 90 jours minimum', self.bibliotheque())

    def test_questions_regroupees_dans_un_seul_formulaire(self):
        self.docx('source.docx', 'Le contrat lie DURAND et ZORVAL.')
        questions = [{'texte': 'Le client accepte-t-il une rupture ?', 'determinante': True}, {'texte': 'Faut-il prévenir la banque ?', 'determinante': True}]
        modele = FauxModele(options=[{**REPONSE_OPTIONS, 'questions': questions}, REPONSE_OPTIONS])
        vue = self.envoyer(self.conseil(), 'Préparer', question='Peut-on résilier ce contrat ?', advised_side='Monsieur DURAND', expected_output='consultation')
        conseil.executer(self.desk, {'mission_id': vue['mission_id']}, modele, self.nc)
        page = self.conseil()
        formulaire = next(f for f in self.formulaires(page) if 'Enregistrer mes décisions' in f['bouton'])
        self.assertEqual(sorted(k for k in formulaire['champs'] if k.startswith('reponse_')), ['reponse_0', 'reponse_1'])
        self.envoyer(page, 'Enregistrer mes décisions', reponse_0='Oui', reponse_1='Non')
        self.assertEqual({d['text'] for d in memoire5651.decisions(self.desk, MID)}, {'Oui', 'Non'})
        self.assertEqual(memoire5651.gaps(self.desk, MID), [])
        conseil.executer(self.desk, {'mission_id': vue['mission_id']}, modele, self.nc)
        self.assertEqual(conseil.vue(self.desk, vue['mission_id'], *AVOCAT)['state'], 'ready_for_review')

    def test_accord_enregistre_obligations_et_agenda_depuis_l_onglet(self):
        v = self.docx('bail signé.docx', *SIGNE)
        page = self.conseil()
        refus = self.envoyer(page, 'Enregistrer l’accord', '400', date_signature='2026-01-31', parties='DURAND, ZORVAL', preuve_nature='attestation_avocat')
        self.assertEqual(refus['error'], 'attestation_explicite_requise')
        out = self.envoyer(page, 'Enregistrer l’accord', date_signature='2026-01-31', parties='DURAND, ZORVAL', preuve_nature='attestation_avocat', attestation=True, version_id=v['version_id'])
        self.assertEqual(out['parties'], ['DURAND', 'ZORVAL'])
        page = self.conseil()
        self.assertIn('Attestation de l’avocat', page)
        with patch.object(chaine5689, 'modele_pour', return_value=FauxModele(obligations=OBLIGATIONS)):
            self.envoyer(page, 'Relever les obligations')
            self.envoyer(page, 'Relever les obligations')                  # un second clic rend le même travail
            self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='obligations5689'").fetchone()[0], 1)
            self.assertTrue(self.desk.work_once())
        page = self.conseil()
        for texte in ('Payer le loyer', 'Restituer les clés', 'En attente de son événement', '2026-02-28'):
            self.assertIn(texte, page)
        self.envoyer(page, 'Inscrire à l’agenda et aux tâches')
        self.assertEqual(self.desk.db.execute('SELECT due FROM tasks').fetchone()[0], '2026-02-28')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE source='accord5689'").fetchone()[0], 1)
        rejeu = self.envoyer(page, 'Inscrire à l’agenda et aux tâches')    # même page, même occurrence : aucune seconde inscription
        self.assertEqual(rejeu['etat'], 'acceptee')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.envoyer(self.conseil(), 'Refuser')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM occurrences5689 WHERE etat='refusee'").fetchone()[0], 1)
        preuve = self.docx('conge.docx', 'Fin du bail notifiée.', categorie='correspondance')
        self.envoyer(self.conseil(), 'Enregistrer l’événement', date='2026-06-30', version_id=preuve['version_id'], locator_kind='paragraph', locator='1')
        self.assertIn('2026-07-15', self.conseil())
        out = self.envoyer(self.conseil(), 'Recontrôler le document signé')
        self.assertEqual(out['invalidees'], 0)

    def test_obligation_ajoutee_a_la_main(self):
        v = self.docx('bail signé.docx', *SIGNE)
        self.envoyer(self.conseil(), 'Enregistrer l’accord', date_signature='2026-01-31', parties='DURAND, ZORVAL', preuve_nature='prestataire', preuve_ref='enveloppe-7', version_id=v['version_id'])
        self.envoyer(self.conseil(), 'Ajouter', clause='Article 2 Loyer', intitule='Déposer la garantie', responsable='DURAND', delai='30', unite='jours')
        page = self.conseil()
        self.assertIn('Déposer la garantie', page)
        self.assertIn('2026-03-02', page)

    def test_mission_annulee_et_reprise_depuis_l_onglet(self):
        self.docx('source.docx', 'Texte.')
        vue = self.envoyer(self.conseil(), 'Préparer', question='Peut-on résilier ce contrat ?', advised_side='Monsieur DURAND', expected_output='consultation')
        with self.assertRaises(Stop):
            conseil.executer(self.desk, {'mission_id': vue['mission_id']}, FauxModele(options=['pas du JSON']), self.nc)
        page = self.conseil()
        self.assertIn('reponse_modele_invalide', page)
        self.assertEqual(self.envoyer(page, 'Reprendre ce qui manque')['state'], 'planned')
        self.assertEqual(self.envoyer(self.conseil(), 'Annuler la mission')['state'], 'cancelled')
        self.assertNotIn('Annuler la mission', self.conseil())

    def test_role_de_la_requete_fixe_les_boutons(self):
        env = {'HTTP_X_AXIORHUB_USER': ASSISTANT[0], 'HTTP_X_AXIORHUB_ROLE': 'assistant', 'REQUEST_METHOD': 'GET'}
        self.addCleanup(membres5650.set_viewer_role, '')
        self.addCleanup(membres5650.set_author, '')
        self.addCleanup(membres5650.set_viewer, ())
        self.assertIsNone(membres5650.gate(env, self.desk.c['state_dir'], '/fiche', {}))
        self.assertEqual(membres5650.viewer_role(), 'assistant')
        self.assertNotIn('Créer le brouillon', self.bibliotheque())

    def test_pages_reelles_de_l_onglet_et_de_la_bibliotheque(self):
        from agent import corporate5636, web460
        from agent.shell501 import AX_JS
        self.assertIn('v500.js', AX_JS)                                   # le script commun des formulaires
        self.assertIn('id="playbooks"', corporate5636.page_html(self.desk, '/p', {'vue': 'bibliotheque'}))
        page = web460.fiche_page(self.desk, {'csrf': 'jeton'}, '/p', {'matter': MID, 'onglet': 'conseil'}, lambda titre, corps, *reste: corps)
        self.assertIn('id="rubrique-conseil"', page)
        self.assertIn('Enregistrer un accord signé', page)
        self.assertIn('/p/fiche?matter=DOS-001&amp;onglet=conseil', page)

    def test_textes_hostiles_echappes(self):
        self.docx('source.docx', 'Texte.')
        self.envoyer(self.conseil(), 'Préparer', question='<img src=x onerror=alert(1)> résilier ?', advised_side='Monsieur DURAND', expected_output='consultation')
        page = self.conseil()
        self.assertNotIn('<img', page)


if __name__ == '__main__':
    unittest.main()
