"""5.6.14 : missions complexes (hiérarchie bornée, contrats de résultats, reprise sans double effet, parents transmis intégralement, rôle
routé), profils procéduraux datés, contrôle par assertion (exécution ≠ résultat), chaîne de preuve des sources, corrections C01–C20,
Invoice Ninja (adaptateur, clients, projets, devis, réservation par temps, synchronisation), MCP (session, schémas), interface
(routines en tête, « À décider » compact, agenda semaine), recette de production réelle."""
from email import policy
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import zipfile

from agent import controle5614, facturation5614, maildraft5613, parcours5614, pilote5613 as pilot, profils5614, sources5614, taches5614
from agent.common import Stop
import test_v530 as t530
import test_v5613 as t5613

ROOT = Path(__file__).resolve().parents[1]
ALPHA = '20240101001'


class TaskModel:
    """Modèle factice par schéma : cadrage, analyses, sections, corrections. Enregistre les charges utiles reçues (contrôle des entrées)."""
    def __init__(self, missing=(), amounts=False):
        self.calls = [];self.missing = list(missing);self.amounts = amounts
    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        payload = json.loads(messages[-1]['content']);self.calls.append((messages[0]['content'], payload))
        props = (json_schema or {}).get('properties', {})
        if 'objectif' in props:
            return json.dumps({'objectif': 'Recouvrer les loyers', 'pretentions_envisagees': ['paiement des loyers'], 'delai': '', 'juridiction': 'tj', 'voie': 'fond',
                               'representation': 'avocat', 'montant_cents': 1500000, 'champs_manquants': self.missing, 'summary': 'Cadrage fait'})
        if 'sections' in props:
            return json.dumps({'sections': [{'code': 'T12d', 'paragraphes': [{'style': 'texte', 'texte': 'Discussion corrigée.', 'source_ids': ['f-1']}]}], 'summary': 'corrigé'})
        if 'paragraphes' in props:
            text = 'Paragraphe de la section ' + str(payload.get('section', '')) + (' pour 1 500 euros.' if self.amounts else '.')
            return json.dumps({'titre': str(payload.get('section', 'Section')), 'paragraphes': [{'style': 'texte', 'texte': text, 'source_ids': ['f-1']}], 'a_completer': [], 'summary': 'ok'})
        items = [{'texte': 'Élément %d issu de %s' % (i, e.get('origine', '')), 'statut': 'etabli', 'source_ids': ['f-1']} for i, e in enumerate((payload.get('entrees') or [{'origine': 'x'}])[:3], 1)]
        if payload.get('consolidation'):
            items = payload.get('resultats_par_lot', [])
        return json.dumps({'items': items or [{'texte': 'Élément', 'statut': 'etabli', 'source_ids': []}], 'champs_manquants': [], 'notes': [], 'summary': '%d élément(s)' % max(1, len(items))})


def fake_pages(raw, name, cfg):
    if 'prot' in name.lower():
        raise Stop('pdf_chiffre')
    if name.lower().endswith('.pdf'):
        return [{'page': i, 'text': 'Page %d du document %s. Montant 1 000 euros.' % (i, name), 'extraction': 'text'} for i in range(1, 4)]
    return [{'page': 1, 'text': 'Section unique de %s.' % name, 'extraction': 'logical_section'}]


class FakeController:
    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        return json.dumps({'defauts': [], 'affirmations_sans_appui': [], 'couverture_complete': True})


class Base(t530.Base):
    def setUp(self):
        super().setUp()
        self.task_model = TaskModel()
        self.desk.c.setdefault('model_routing', {})['control'] = {'provider': 'ollama', 'model': 'test-control'}
        for p in (patch('agent.taches5614._model', side_effect=lambda d, m, r: (self.task_model, {'purpose': parcours5614.ROLES[r][1], 'provider': 'ollama', 'model': 'test-' + r})),
                  patch('agent.controle5614._control_model', return_value=FakeController()),
                  patch('agent.control470.quality', return_value={'mentions': None, 'citations': {'items': [], 'counts': {}, 'total': 0, 'needs_check': False, 'headline': '', 'sources': {}}, 'fact_date': ''}),
                  patch('agent.documents.extract_pages', side_effect=fake_pages),
                  patch('agent.legal_research.research_enabled_mcp', return_value={'status': 'completed', 'verified_authorities': [{'id': 'auth-1', 'title': 'CPC art. 54', 'official_url': 'https://legifrance.example/54'}], 'leads': [], 'connectors': []})):
            p.start();self.addCleanup(p.stop)

    def mission(self, instruction='Prépare une assignation au fond devant le tribunal judiciaire', key='k', **kw):
        return taches5614.create(self.desk, {'instruction': instruction, 'matter': ALPHA, 'parcours': 'assignation', 'request_key': 'req5614-' + key.ljust(20, 'x'),
                                             'answers': {'juridiction': 'tj', 'voie': 'fond', 'representation': 'avocat'}, **kw})

    def run_all(self, ident, rounds=8):
        for _ in range(rounds):
            taches5614.advance(self.desk, ident)
            if not taches5614.run_pending(self.desk, ident):
                break
        return taches5614.get(self.desk, ident, prefix='/p')


class Graph(Base):
    def test_cycles_bounds_and_hierarchy(self):
        lim = taches5614.limits(self.desk)
        with self.assertRaisesRegex(Stop, 'cycle'):
            taches5614.check_graph([{'code': 'A', 'depends': ['B'], 'parent': ''}, {'code': 'B', 'depends': ['A'], 'parent': ''}], lim)
        with self.assertRaisesRegex(Stop, 'trop_de_taches'):
            taches5614.check_graph([{'code': 'T%d' % i, 'depends': [], 'parent': ''} for i in range(65)], lim)
        deep = [{'code': 'L%d' % i, 'depends': [], 'parent': 'L%d' % (i - 1) if i else ''} for i in range(5)]
        with self.assertRaisesRegex(Stop, 'profondeur'):
            taches5614.check_graph(deep, lim)
        self.assertTrue(taches5614.check_graph(parcours5614.ASSIGNATION, lim));self.assertTrue(taches5614.check_graph(parcours5614.CONCLUSIONS, lim))
        custom = taches5614.create(self.desk, {'instruction': 'Mission de 18 tâches', 'matter': ALPHA, 'parcours': 'custom', 'request_key': 'req5614-custom' + 'c' * 12, 'autonomy': 'suggest',
                                               'tasks': [{'code': 'G1', 'instruction': 'Groupe 1', 'role': 'superviseur', 'type': 'analyse'}] +
                                                        [{'code': 'G1-%d' % i, 'instruction': 'Sous-tâche', 'role': 'analyste_faits', 'type': 'analyse', 'parent': 'G1', 'depends': ['G1']} for i in range(1, 6)] +
                                                        [{'code': 'G2', 'instruction': 'Groupe 2', 'role': 'superviseur', 'type': 'analyse', 'depends': ['G1-1']}] +
                                                        [{'code': 'G2-%d' % i, 'instruction': 'Sous-tâche', 'role': 'redacteur', 'type': 'section', 'parent': 'G2', 'depends': ['G2']} for i in range(1, 6)] +
                                                        [{'code': 'G3', 'instruction': 'Groupe 3', 'role': 'superviseur', 'type': 'analyse', 'depends': ['G2-1']}] +
                                                        [{'code': 'G3-%d' % i, 'instruction': 'Sous-tâche', 'role': 'controleur', 'type': 'analyse', 'parent': 'G3', 'depends': ['G3']} for i in range(1, 6)]})
        self.assertEqual(len(custom['tasks']), 18);self.assertEqual(len(custom['tree']), 3);self.assertEqual(len(custom['tree'][0]['children']), 5);self.assertEqual(custom['state'], 'suggested')


class Execution(Base):
    def test_assignation_runs_to_a_validated_project_with_all_parents_transmitted(self):
        m = self.mission()
        self.assertEqual(m['state'], 'active');self.assertEqual(len(m['tasks']), len(parcours5614.ASSIGNATION))
        m = self.run_all(m['id'], rounds=12)
        by = {t['code']: t for t in m['tasks']}
        self.assertEqual(by['T02']['result_outcome'], 'accepte');self.assertIn(by['T03']['result_outcome'], ('accepte', 'reserves'))
        self.assertEqual(by['T07']['result']['content']['profile'], 'tj_fond_avocat');self.assertEqual(by['T08']['result_outcome'], 'accepte')
        discussion = next(c for s, c in self.task_model.calls if c.get('tache') == 'T12d')
        self.assertEqual({r['code'] for r in discussion['references_entrees']}, {'T08', 'T09', 'T10', 'T11'})      # tous les parents, avec type/version/hash
        self.assertTrue(all(r.get('hash') for r in discussion['references_entrees']))
        self.assertIn('T14', by);self.assertEqual(by['T14']['run_state'], 'terminee');self.assertIn(by['T14']['result_outcome'], ('accepte', 'reserves'))
        content = by['T14']['result']['content']
        self.assertIn('/Dossiers/DEMO/20_Actes_et_conclusions/90_AxiorHub_Brouillons/', content['path']);self.assertIn(content['path'], self.docs.files)
        self.assertEqual(content['control']['execution'], 'executee');self.assertIn('BORDEREAU', content['text'])
        names, xml = t5613.docx_text(self.docs.files[content['path']])
        self.assertNotIn('AxiorHub', xml)                                                             # aucune note interne dans le corps (U06)
        self.assertEqual(m['state'], 'a_valider');self.assertEqual(m['validation'], 'a_decider');self.assertTrue(m['decisions'])
        self.assertEqual(m['decisions'][0]['kind'], 'validation')
        with self.assertRaisesRegex(Stop, 'version_validee_perimee'):
            taches5614.control(self.desk, {'id': m['id'], 'action': 'validate', 'sha256': 'ffff'})
        done = taches5614.control(self.desk, {'id': m['id'], 'action': 'validate', 'sha256': content['sha256']})
        self.assertEqual((done['state'], done['validation'], done['validated_hash']), ('validee', 'validee', content['sha256']))
        roles = {s.split('«')[1].split('»')[0].strip() for s, _ in self.task_model.calls}
        self.assertIn('Rédacteur', roles);self.assertIn('Analyste des faits', roles)                     # rôle réellement transmis (C20)
        self.assertEqual(by['T12d']['trace']['route']['model'], 'test-redacteur')

    def test_research_without_verified_text_blocks_dependents_and_optional_branch_can_be_omitted(self):
        with patch('agent.legal_research.research_enabled_mcp', return_value={'status': 'not_configured', 'verified_authorities': [], 'leads': [], 'connectors': []}):
            m = self.mission(key='r')
            m = self.run_all(m['id'], rounds=10)
        by = {t['code']: t for t in m['tasks']}
        self.assertEqual(by['T08']['result_outcome'], 'bloque');self.assertEqual(by['T08']['error'], 'aucune_reference_verifiee')
        self.assertEqual(by['T12d']['run_state'], 'en_attente');self.assertIn('T08', by['T12d']['waiting_for'])
        self.assertEqual(by['T12a']['run_state'], 'terminee')                                             # l'en-tête ne dépend pas de la recherche
        self.assertEqual(m['state'], 'bloquee');self.assertIn('Blocage', m['next_action'])
        self.assertEqual(by['T15']['required'], 0)                                                        # branche facultative

    def test_missing_essential_fields_create_one_decision_while_reading_continues(self):
        self.task_model.missing = ['juridiction', 'partie adverse']
        m = taches5614.create(self.desk, {'instruction': 'Prépare une assignation', 'matter': ALPHA, 'parcours': 'assignation', 'request_key': 'req5614-m' + 'm' * 19, 'answers': {}})
        m = self.run_all(m['id'], rounds=4)
        kinds = [d['kind'] for d in m['decisions']]
        self.assertIn('champs_manquants', kinds);self.assertEqual(kinds.count('champs_manquants'), 1)
        self.assertEqual({t['code']: t['run_state'] for t in m['tasks']}['T02'], 'terminee')          # l'inventaire a continué
        d = next(d for d in m['decisions'] if d['kind'] == 'champs_manquants')
        after = taches5614.decide(self.desk, {'id': d['id'], 'answer': {'juridiction': 'tj', 'voie': 'fond', 'representation': 'avocat', 'partie adverse': 'Société X'}})
        self.assertFalse([x for x in after['decisions'] if x['kind'] == 'champs_manquants']);self.assertEqual(after['answers']['juridiction'], 'tj')

    def test_lease_generation_budget_pause_cancel_and_revision(self):
        m = self.mission(key='l')
        tid = m['tasks'][0]['id']
        taches5614.perform(self.desk, {'mission': m['id'], 'task': tid})
        t = self.desk.db.execute('SELECT generation,run_state FROM tasks5614 WHERE id=?', (tid,)).fetchone()
        self.assertEqual((t['generation'], t['run_state']), (1, 'terminee'))
        with self.assertRaisesRegex(Stop, 'tentative_perimee'):                                          # une tentative ancienne ne publie pas
            taches5614._publish(self.desk, {'id': m['id']}, {'id': tid, 'code': 'T01', 'output_type': 'cadrage', 'depends_on': []}, 0, {'x': 1}, 'accepte', [], {})
        self.desk.db.execute('UPDATE missions5614 SET budget_calls=1 WHERE id=?', (m['id'],));self.desk.db.commit()
        m = self.run_all(m['id'], rounds=6)
        self.assertEqual(m['state'], 'suspendue_budget');self.assertTrue([d for d in m['decisions'] if d['kind'] == 'budget'])
        with self.assertRaisesRegex(Stop, 'budget_insuffisant'):
            taches5614.control(self.desk, {'id': m['id'], 'action': 'resume', 'budget_calls': 1})
        m = taches5614.control(self.desk, {'id': m['id'], 'action': 'resume', 'budget_calls': 500});self.assertEqual(m['state'], 'active')
        m = self.run_all(m['id'], rounds=12)
        self.assertEqual(m['state'], 'a_valider')
        m = taches5614.control(self.desk, {'id': m['id'], 'action': 'revise', 'instruction': 'Reprends uniquement la discussion sur la prescription', 'codes': ['T12d']})
        by = {t['code']: t for t in m['tasks']}
        self.assertIn(by['T12d']['run_state'], ('a_preparer', 'en_attente'));self.assertEqual(by['T12d']['result_outcome'], 'perime');self.assertIn(by['T14']['run_state'], ('a_preparer', 'en_attente'))
        self.assertEqual(by['T09']['run_state'], 'terminee');self.assertEqual(m['plan_revision'], 2);self.assertEqual(m['validation'], 'non_demandee')
        m = taches5614.control(self.desk, {'id': m['id'], 'action': 'pause'});self.assertEqual(m['state'], 'suspendue')
        m = taches5614.control(self.desk, {'id': m['id'], 'action': 'cancel'});self.assertEqual(m['state'], 'annulee')
        self.assertTrue(all(t['run_state'] in ('terminee', 'annulee') for t in m['tasks']));self.assertTrue(by['T14']['result']['content']['path'] in self.docs.files)   # fichiers conservés

    def test_deposit_resumes_without_second_write_and_detects_conflict(self):
        m = self.mission(key='d')
        mission = {'id': m['id'], 'matter': ALPHA};task = {'id': 'task-x'}
        data = b'PK\x03\x04fake'
        before = len(self.docs.puts)
        first = taches5614._deposit(self.desk, mission, task, data, 'Projet test')
        self.assertEqual(len(self.docs.puts), before + 1);self.assertEqual(self.desk.db.execute('SELECT state FROM ops5614').fetchone()[0], 'confirme')
        again = taches5614._deposit(self.desk, mission, task, data, 'Projet test')
        self.assertEqual(again['path'], first['path']);self.assertEqual(len(self.docs.puts), before + 1)     # aucun second dépôt
        self.docs.files[first['path']] = b'PK\x03\x04modifie par l avocat'
        self.desk.db.execute("UPDATE ops5614 SET state='en_cours'");self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'conflit'):
            taches5614._deposit(self.desk, mission, task, data, 'Projet test')

    def test_batched_inputs_keep_every_source(self):
        m = self.mission(key='b')
        self.desk.setting('missions5614:limits', {'max_payload_chars': 9000})
        task = {'id': 'x', 'code': 'T09', 'role': 'analyste_faits', 'instruction': 'Chronologie', 'output_type': 'chronologie', 'depends_on': []}
        inputs = [{'origine': 'T03', 'type': 'extraits', 'texte': 'Fait %d. ' % i * 600} for i in range(4)]
        mission = taches5614._mission_row(self.desk, m['id'], 'cabinet');mission['answers'] = {};mission['extra_instructions'] = []
        data, route, batches = taches5614._ask_batched(self.desk, mission, task, {'tache': 'T09'}, inputs, taches5614.GENERIC_SCHEMA, 'chronologie')
        self.assertGreater(batches, 1);self.assertTrue(data['items']);self.assertIn('Consolidé', ' '.join(data['notes']))

    def test_plan_568_migrates_as_flat_groups(self):
        from agent import plans568
        with patch('agent.missions567._launch'):
            p = plans568.create(self.desk, {'title': 'Plan historique', 'matter': ALPHA, 'request_key': 'plan5614-' + 'p' * 16, 'steps': [
                {'role': 'A2', 'instruction': 'Lire les pièces'}, {'role': 'A3', 'instruction': 'Préparer', 'depends_on': [0]}]})
        m = taches5614.migrate_plan(self.desk, p['id'])
        self.assertEqual([t['code'] for t in m['tasks']], ['P01', 'P02']);self.assertEqual(m['tasks'][1]['depends_on'], ['P01']);self.assertEqual(m['parcours'], 'custom')
        self.assertEqual(taches5614.migrate_plan(self.desk, p['id'])['id'], m['id'])
        self.assertNotIn('abstained', plans568.DONE)                                                     # C12


class Profils(Base):
    def test_qualification_by_structured_answers_not_keywords(self):
        q = profils5614.qualify(self.desk, {'juridiction': 'tj', 'voie': 'refere'});self.assertEqual(q['profile'], 'refere_tj');self.assertFalse(q['approved'])
        q = profils5614.qualify(self.desk, {'juridiction': 'tj', 'voie': 'fond'});self.assertTrue(q['ambiguous']);self.assertEqual({o['id'] for o in q['options']}, {'tj_fond_avocat', 'tj_fond_sans_avocat'})
        q = profils5614.qualify(self.desk, {'juridiction': 'tj', 'voie': 'fond', 'montant_cents': 2_500_000});self.assertEqual(q['profile'], 'tj_fond_avocat')
        q = profils5614.qualify(self.desk, {'juridiction': 'jex'});self.assertEqual(q['profile'], 'jex')
        q = profils5614.qualify(self.desk, {'juridiction': 'jex', 'voie': 'refere'});self.assertTrue(q['unsupported'])
        with self.assertRaisesRegex(Stop, 'approbation_profil_requise'):
            profils5614.approve(self.desk, 'refere_tj', 'no')
        profils5614.approve(self.desk, 'refere_tj', 'yes');self.assertTrue(profils5614.is_approved(self.desk, 'refere_tj'))
        body = self.request('/profils')['body'];self.assertIn('Profils procéduraux', body);self.assertIn('approuvé (', body)
        self.assertTrue(all(r['verify'] for p in profils5614.PROFILES.values() for r in p['references']))   # références à vérifier, jamais prêtes à copier


class Controle(Base):
    def test_assertions_deterministic_checks_and_execution_vs_outcome(self):
        text = 'Le loyer est de 4 200 euros depuis le 03/02/2026.\nPAR CES MOTIFS\nCondamner X à payer 4 800 euros.'
        sources = [{'id': 's1', 'text': 'Loyer mensuel de 4 200 euros, contrat du 3 février 2026.'}]
        det = controle5614.deterministic(text, sources)
        kinds = {(a['kind'], a['value'], a['status']) for a in det['assertions']}
        self.assertIn(('montant', '4200.00', 'appuyee'), kinds);self.assertIn(('montant', '4800.00', 'non_retrouvee'), kinds);self.assertIn(('date', '2026-02-03', 'appuyee'), kinds)
        self.assertTrue(any(d['kind'] == 'coherence' and d['gravite'] == 'bloquant' for d in det['defects']))
        report = controle5614.review(self.desk, text, sources, 'assignation', ALPHA)
        self.assertEqual((report['execution'], report['outcome']), ('executee', 'bloque'));self.assertIn('bloquant', report['summary'])
        self.desk.setting('automation:second_model_control_enabled', False)
        r2 = controle5614.review(self.desk, 'Texte sans montant.', sources, '', ALPHA)
        self.assertEqual(r2['execution'], 'partielle');self.assertIn(r2['outcome'], ('reserves', 'indisponible'))
        long = ('Paragraphe sans chiffre. ' * 600) + '\nPAR CES MOTIFS\nCondamner Y à payer 9 999 euros.'
        self.desk.setting('automation:second_model_control_enabled', True)
        r3 = controle5614.review(self.desk, long, sources, '', ALPHA)
        self.assertGreater(r3['coverage']['blocks_total'], 1);self.assertEqual(r3['coverage']['blocks_reviewed'], r3['coverage']['blocks_total'])
        self.assertTrue(any('9 999' in d.get('avant', '') for d in r3['defects']))                      # fin de l'acte couverte (C05)

    def test_control_document_distinguishes_block_and_revise(self):
        class Blocker:
            def ask(self, stage, payload): return {'recommendation': 'block', 'agree': False, 'unsupported_claims': ['x'], 'omissions': [], 'risks': []}
        with patch('agent.pilote5613._control_model', return_value=Blocker()):
            out = pilot.control_document(self.desk, 'courrier', 'Cher Confrère, texte.', ALPHA)
        self.assertTrue(out['done']);self.assertEqual((out['execution'], out['outcome']), ('executee', 'bloque'))
        checks = pilot.document_checks(self.desk, None, {'sha256': 'a', 'readback_sha256': 'a', 'control': out})
        self.assertFalse(checks['legal_control']);self.assertEqual(checks['legal_control_label'], 'Contrôle juridique bloquant')
        class Reviser:
            def ask(self, stage, payload): return {'recommendation': 'revise', 'agree': False, 'unsupported_claims': [], 'omissions': ['o'], 'risks': []}
        with patch('agent.pilote5613._control_model', return_value=Reviser()):
            out = pilot.control_document(self.desk, 'courrier', 'Cher Confrère, texte.', ALPHA)
        self.assertEqual(out['outcome'], 'reserves')


class Sources(Base):
    def test_snapshot_manifest_and_reading_coverage(self):
        self.docs.files['/Dossiers/DEMO/B/Conclusions récapitulatives.docx'] = self.docs.files['/Dossiers/DEMO/Conclusions récapitulatives.docx']
        snap = sources5614.snapshot(self.desk, ALPHA, 'm1')
        self.assertEqual(snap['total_files'], len([p for p in self.docs.files if p.startswith('/Dossiers/DEMO/')]))
        ids = [f['id'] for f in snap['files']];self.assertEqual(len(ids), len(set(ids)))
        r = sources5614.read_file(self.desk, self.docs, ALPHA, '/Dossiers/DEMO/Pièces/02_Constat huissier.pdf', snap['id'])
        self.assertEqual(r['status'], 'lu_integralement');self.assertEqual(r['coverage']['total'], 3)
        locked = sources5614.read_file(self.desk, self.docs, ALPHA, '/Dossiers/DEMO/Pièces/Relevé protégé.pdf', snap['id'])
        self.assertIn(locked['status'], ('illisible', 'exclu'))
        man = sources5614.manifest(snap, [r, locked], extracts=['/Dossiers/DEMO/Pièces/Note.docx'])
        self.assertEqual(man['counts']['lu_integralement'], 1);self.assertEqual(man['counts']['extrait'], 1);self.assertTrue(man['excluded'])
        self.assertIn('conclusions recapitulatives.docx', man['homonyms']);self.assertEqual(len(man['homonyms']['conclusions recapitulatives.docx']), 2)
        delta = sources5614.changed_since(self.desk, snap);self.assertFalse(delta['changed'])
        self.docs.files['/Dossiers/DEMO/Nouvelle pièce.pdf'] = b'%PDF-1.4 x'
        self.assertTrue(sources5614.changed_since(self.desk, snap)['changed'])


class Corrections(Base):
    def test_imap_resume_never_duplicates_and_full_verification(self):
        box = t5613.FakeMailbox()
        state = {'fail': True}
        real_append = box.append_draft
        def flaky(msg):
            real_append(msg)
            if state['fail']:
                state['fail'] = False;raise Stop('verification_imap_indisponible')
        box.append_draft = flaky
        args = {'instruction': 'Prépare un courriel au confrère pour demander ses disponibilités', 'matter': ALPHA, 'mission_id': ''}
        with patch('agent.maildraft5613._mail_model', return_value=t5613.MailModel()), patch('agent.maildraft5613._mailbox', return_value=box):
            with self.assertRaisesRegex(Stop, 'incertain'):
                maildraft5613.perform(self.desk, args)
            out = maildraft5613.perform(self.desk, args)                                                # reprise : rapprochement, aucun second APPEND
        self.assertEqual(out['brouillon_imap'], 'verifie');self.assertEqual(len(box.msgs), 1);self.assertNotIn('AxiorHub', box.msgs[0].get_content())
        ident = self.desk.db.execute('SELECT id FROM maildraft5613').fetchone()[0]
        box.msgs[0].set_content('Corps modifié par l’avocat.\n')
        with patch('agent.maildraft5613._mailbox', return_value=box):
            r = maildraft5613.recheck(self.desk, ident)
        self.assertEqual(r['state'], 'modifie');self.assertIn('corps', r['detail'])
        js = (ROOT / 'agent' / 'maildraft5613.py').read_text(encoding='utf-8');self.assertIn("routed_config(desk.c, 'mail_drafting')", js)   # C09

    def test_word_revision_keeps_tables_and_unchanged_paragraphs(self):
        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        para = lambda t, style='': '<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else '', t)
        table = '<w:tbl><w:tr><w:tc>' + para('Montant 1 000 euros') + '</w:tc></w:tr></w:tbl>'
        body = para('Titre', 'Titre1') + para('Premier paragraphe.') + table + para('Deuxième paragraphe.') + para('Troisième paragraphe.')
        doc = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s"><w:body>%s<w:sectPr><w:pgSz w:w="1"/></w:sectPr></w:body></w:document>' % (W, body)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>');z.writestr('word/document.xml', doc);z.writestr('word/footnotes.xml', '<w:footnotes/>')
        new_body = para('Titre') + para('Premier paragraphe.') + para('Deuxième paragraphe modifié.') + para('Troisième paragraphe.') + para('Quatrième ajouté.')
        out, report = pilot.revise_body(buf.getvalue(), new_body)
        names, xml = t5613.docx_text(out)
        self.assertIn('<w:tbl>', xml);self.assertIn('Montant 1 000 euros', xml);self.assertIn('w:pStyle w:val="Titre1"', xml)                     # tableau et style conservés
        self.assertIn('Deuxième paragraphe modifié.', xml);self.assertIn('Quatrième ajouté.', xml);self.assertIn('word/footnotes.xml', names)
        self.assertEqual((report['kept'], report['replaced'], report['inserted'], report['tables_kept']), (3, 1, 1, 1))
        self.assertLess(xml.index('Premier paragraphe.'), xml.index('<w:tbl>'));self.assertLess(xml.index('<w:tbl>'), xml.index('Deuxième paragraphe modifié.'))

    def test_revision_diff_normalizes_and_orders_changes(self):
        before = 'Il est demandé 1 000 €.\nAudience du 2026-03-12.\nParagraphe A.\nParagraphe B.\nParagraphe B.'
        after = 'Il est demandé 1 500 euros.\nAudience du 2026-03-13.\nParagraphe B.\nParagraphe A.'
        d = pilot.revision_diff(before, after)
        self.assertIn('montant(s) modifié(s)', d['alerts']);self.assertIn('date(s) modifiée(s)', d['alerts'])
        self.assertEqual(d['amounts']['added'], ['1500.00']);self.assertEqual(d['dates']['added'], ['2026-03-13'])
        kinds = [c['kind'] for c in d['changes']];self.assertIn('modification', kinds);self.assertIn('deplacement', kinds)
        self.assertGreaterEqual(d['moved_count'] + d['duplicates_removed'], 1)

    def test_template_fields_and_validation_hash(self):
        t5613.install_template(self.desk, 'Cabinet conclusions')                                        # libellé « Cabinet » : plus un repli générique (C10)
        self.assertIsNone(pilot.template_for_kind(self.desk, 'courrier')[0])
        row, raw = pilot.template_for_kind(self.desk, 'conclusions');self.assertTrue(row)
        data, info = pilot.build_from_template_ex(self.desk, raw, {'titre': 'T', 'paragraphes': [{'style': 'texte', 'texte': 'x'}]}, {}, 'req', {'id': 'A', 'client_name': 'C'})
        self.assertEqual(info['missing_fields'], ['destinataire']);self.assertEqual(info['provenance']['objet'], 'projet')
        names, xml = t5613.docx_text(data);self.assertIn('[À COMPLÉTER : destinataire]', xml)
        from agent.cockpit530 import ensure_schema as cockpit_schema
        cockpit_schema(self.desk)
        pilot.record_validation(self.desk, 'doc:abc', '/p', 'sha-old', 'valide')
        from agent.common import digest
        item = 'doc:' + digest('/p')[:24]
        pilot.record_validation(self.desk, item, '/p', 'sha-old', 'valide')
        self.desk.db.execute('INSERT OR REPLACE INTO cockpit530_reviewed VALUES(?,?,?,?)', (item, 'valide', 't', self.desk.now()));self.desk.db.commit()
        checks = pilot.document_checks(self.desk, {'path': '/p'}, {'sha256': 'sha-new', 'readback_sha256': 'sha-new', 'control': {}})
        self.assertFalse(checks['lawyer_validation']);self.assertIn('à renouveler', checks['lawyer_validation_label'])

    def test_hearing_sheet_reads_likely_questions_and_reports_unavailability(self):
        from agent import audience5613, hearing
        hearing.ensure_schema(self.desk)
        data = {'preparation': {'likely_questions': [{'question': 'Quel est le point de départ du délai ?', 'proposed_answer': 'La signification.'}]}}
        self.desk.db.execute('INSERT INTO hearing_projects_v250 VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('p1', ALPHA, 'pending', 'i', json.dumps(data), 'h', '', self.desk.now(), '', '', ''));self.desk.db.commit()
        s = audience5613.sheet(self.desk, ALPHA)
        self.assertEqual(s['probable_questions'][0]['question'], 'Quel est le point de départ du délai ?');self.assertEqual(s['status'], 'disponible')
        import sqlite3
        real = audience5613._rows
        def broken(desk, sql, args=(), errors=None):
            if 'hearing_projects' in sql:
                if errors is not None:
                    errors.append('database is locked')
                return []
            return real(desk, sql, args, errors)
        with patch('agent.audience5613._rows', side_effect=broken):
            s = audience5613.sheet(self.desk, ALPHA);self.assertEqual(s['status'], 'indisponible');self.assertTrue(s['unavailable'])
            body = self.request('/audience', query='matter=' + ALPHA)['body'];self.assertIn('partiellement indisponible', body)


class FakeNinjaHTTP:
    """Invoice Ninja v5 simulé : clients, projets, devis, factures, tâches ; pagination ; 422 sur champ manquant."""
    def __init__(self):
        self.store = {'clients': {}, 'projects': {}, 'quotes': {}, 'invoices': {}, 'tasks': {}};self.calls = [];self.fail_get_once = False;self.n = 100
        self.headers = {}
    def json(self, method, path, data=None):
        self.calls.append((method, path))
        if method == 'GET' and '?' in path:
            entity = path.split('/api/v1/')[1].split('?')[0]
            rows = list(self.store[entity].values())
            return {'data': rows[:100], 'meta': {'pagination': {'total_pages': 1}}}
        if method == 'GET':
            entity, rid = path.split('/api/v1/')[1].split('/')
            if self.fail_get_once:
                self.fail_get_once = False;raise Stop('delai_http_depasse')
            if rid not in self.store[entity]:
                raise Stop('http_404')
            return {'data': self.store[entity][rid]}
        if method == 'POST':
            entity = path.split('/api/v1/')[1]
            if entity == 'clients' and not data.get('name'):
                raise Stop('http_422')
            self.n += 1;rid = 'id%d' % self.n
            row = {**data, 'id': rid, 'status_id': '1', 'number': entity[:3].upper() + str(self.n), 'updated_at': self.n}
            if entity == 'invoices':
                row['amount'] = sum(float(i['cost']) * float(i['quantity']) for i in data['line_items'])
            self.store[entity][rid] = row
            return {'data': row}
        if method == 'PUT':
            entity, rid = path.split('/api/v1/')[1].split('/')
            self.store[entity][rid].update(data);return {'data': self.store[entity][rid]}
        raise Stop('methode')


class Facturation(Base):
    def setUp(self):
        super().setUp()
        self.desk.c['invoice_ninja'] = {'enabled': True, 'write_enabled': True, 'base_url': 'https://factures.example.test', 'api_token_file': '/x', 'company_id': 'c1'}
        self.http = FakeNinjaHTTP()

    def test_client_search_create_and_update_keep_contacts(self):
        self.http.store['clients']['c9'] = {'id': 'c9', 'name': 'ALPHA', 'contacts': [{'email': 'a@example.test'}]}
        with self.assertRaisesRegex(Stop, 'homonyme_client_choisir'):
            facturation5614.ensure_client(self.desk, ALPHA, {'name': 'ALPHA'}, http=self.http)
        out = facturation5614.ensure_client(self.desk, ALPHA, {'client_id': 'c9'}, http=self.http)
        self.assertEqual((out['client_id'], out['created']), ('c9', False))
        self.assertEqual(self.desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (ALPHA,)).fetchone()[0], 'c9')
        up = facturation5614.update_client(self.desk, 'c9', {'address1': '1 rue X', 'contacts': [{'email': 'b@example.test'}]}, http=self.http)
        self.assertEqual(up['contacts'], 2)
        new = facturation5614.ensure_client(self.desk, '2024020201', {'name': 'BRAVO SARL', 'contacts': [{'email': 'bravo@example.test'}]}, confirm=True, http=self.http)
        self.assertTrue(new['created']);self.assertEqual(new['issues'], [])
        with self.assertRaisesRegex(Stop, 'invoice_ninja_champs_invalides'):
            facturation5614.InvoiceNinjaClient(self.desk, self.http, write=True).create('client', {'private_notes': 'x'}, 'bad')

    def test_project_quote_lines_and_reconciliation(self):
        facturation5614.ensure_client(self.desk, ALPHA, {'name': 'ALPHA SAS'}, confirm=True, http=self.http)
        p = facturation5614.ensure_project(self.desk, ALPHA, {'task_rate': '250'}, http=self.http);self.assertTrue(p['created'])
        again = facturation5614.ensure_project(self.desk, ALPHA, http=self.http);self.assertEqual((again['project_id'], again['created']), (p['project_id'], False))
        prev = facturation5614.preview_quote(self.desk, ALPHA, [{'label': 'Consultation', 'quantity': 2, 'cost': '150.10', 'tax_name': 'TVA', 'tax_rate': 20}])
        self.assertEqual((prev['total_ht'], prev['taxes'], prev['total_ttc']), ('300.20', '60.04', '360.24'))
        with self.assertRaisesRegex(Stop, 'confirmation_requise'):
            facturation5614.draft_quote(self.desk, ALPHA, prev['lines'], http=self.http)
        q = facturation5614.draft_quote(self.desk, ALPHA, [{'label': 'Consultation', 'quantity': 2, 'cost': '150.10'}], confirm=True, http=self.http)
        self.assertTrue(q['verified']);self.assertFalse(q['sent']);self.assertFalse(q['converted']);self.assertNotIn('send_email', self.http.store['quotes'][q['quote_id']])
        remote = {'id': 'i1', 'client_id': 'other', 'status_id': '1', 'line_items': [{'notes': 'Consultation', 'quantity': 2, 'cost': 150.1}]}
        issues = facturation5614.check_lines(remote, {'line_items': [{'notes': 'Consultation', 'quantity': 2, 'cost': 150.1}]}, 'c1', '300.20')
        self.assertEqual(issues, ['client différent'])
        issues = facturation5614.check_lines({**remote, 'client_id': 'c1', 'line_items': [{'notes': 'Autre', 'quantity': 2, 'cost': 150.1, 'tax_rate1': 20}]}, {'line_items': [{'notes': 'Consultation', 'quantity': 2, 'cost': 150.1, 'tax_rate1': 0}]}, 'c1', '300.20')
        self.assertIn('ligne 1 : libellé différent', issues);self.assertIn('ligne 1 : taxe différente', issues)
        with self.assertRaisesRegex(Stop, 'operation_invoice_ninja_non_permise'):
            facturation5614.InvoiceNinjaClient(self.desk, self.http, write=True).call('POST', '/api/v1/invoices/i1/email', {})

    def test_time_entries_reserved_individually_and_faithful_time_log(self):
        from agent import facturation569, time500, workstation
        workstation.link_invoice_client(self.desk, ALPHA, 'c1')
        time500.set_terms(self.desk, ALPHA, 'horaire', rate='250')
        time500.add_manual(self.desk, ALPHA, '2026-10-05', 30, 'Lecture');time500.add_manual(self.desk, ALPHA, '2026-10-06', 30, 'Lecture 2');time500.add_manual(self.desk, ALPHA, '2026-10-07', 45, 'Lecture 3')
        entries = facturation569.billable_entries(self.desk, ALPHA);self.assertGreaterEqual(len(entries), 2)
        a, b = entries[0]['id'], entries[1]['id']
        facturation5614.reserve_entries(self.desk, [a, b], 'k1')
        with self.assertRaisesRegex(Stop, 'temps_deja_reserve'):
            facturation5614.reserve_entries(self.desk, [a], 'k2')
        self.assertFalse(facturation569.billable_entries(self.desk, ALPHA)[:1] and facturation569.billable_entries(self.desk, ALPHA)[0]['id'] in (a, b))
        facturation5614.release_entries(self.desk, 'k1', 'test')
        facturation5614.mark_billed_remotely(self.desk, [a], 'INV-9')
        self.assertNotIn(a, {x['id'] for x in facturation569.billable_entries(self.desk, ALPHA)})
        log, rep = facturation5614.time_log({'day': '2026-10-05', 'minutes': 30, 'origin': 'manuelle'})
        self.assertEqual(log[0][1] - log[0][0], 1800);self.assertIn('déclarée', rep);self.assertNotIn('9h', rep)
        log2, rep2 = facturation5614.time_log({'day': '2026-10-05', 'minutes': 30, 'origin': 'chrono', 'started_at': '2026-10-05T14:10:00+00:00'})
        self.assertEqual(log2[0][0], 1791209400);self.assertIn('mesuré', rep2)
        out = facturation569.log_time(self.desk, b, confirm=True, http=self.http)
        self.assertTrue(out['verified']);self.assertIn('durée déclarée', self.http.store['tasks'][out['task_id']]['description'])
        self.assertNotIn(b, {x['id'] for x in facturation569.billable_entries(self.desk, ALPHA)})            # tâche exportée : plus facturable localement
        self.http.fail_get_once = True
        entries = facturation569.billable_entries(self.desk, ALPHA)
        if entries:
            with self.assertRaisesRegex(Stop, 'identifiant_conserve'):
                facturation569.log_time(self.desk, entries[0]['id'], confirm=True, http=self.http)
            row = self.desk.db.execute("SELECT remote_id,state FROM invoice_ninja_writes_v569 WHERE kind='task' AND state='uncertain'").fetchone()
            self.assertTrue(row['remote_id'])

    def test_sync_pull_marks_remote_billing(self):
        self.http.store['invoices']['i7'] = {'id': 'i7', 'number': 'F7', 'status_id': '4', 'amount': 100, 'balance': 0, 'paid_to_date': 100, 'updated_at': 5,
                                             'line_items': [{'task_id': 't55', 'cost': 100, 'quantity': 1}]}
        facturation5614.ensure_schema(self.desk)
        facturation5614.InvoiceNinjaClient(self.desk, self.http)._map('task', 'entry:e55', 't55')
        changes = facturation5614.pull(self.desk, http=self.http)
        self.assertEqual(changes['invoice'], 1);self.assertEqual(changes['billed_entries'], 1)
        self.assertEqual(facturation5614.remote_state(self.desk, 'invoice', 'i7')['status_label'], 'payée')
        self.assertIn('e55', facturation5614.reserved_entries(self.desk))
        self.assertEqual(facturation5614.pull(self.desk, http=self.http)['invoice'], 0)                      # rejoué : aucun effet


class FakeMCP:
    """Serveur MCP simulé : exige un identifiant de session après initialize, pagine tools/list, outil au schéma imbriqué."""
    def __init__(self):
        self.headers = {};self.last_headers = {};self.calls = []
    def request(self, method, url, data=None, headers=None, limit=0):
        payload = json.loads(data);self.calls.append((payload.get('method'), headers or {}))
        if payload.get('method') == 'initialize':
            self.last_headers = {'Mcp-Session-Id': 'sess-1'}
            return json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'fake'}}}).encode()
        if payload.get('method') == 'notifications/initialized':
            return b''
        if (headers or {}).get('Mcp-Session-Id') != 'sess-1':
            return json.dumps({'jsonrpc': '2.0', 'id': payload.get('id'), 'error': {'code': -32000, 'message': 'session requise'}}).encode()
        if payload['method'] == 'tools/list':
            if not payload.get('params', {}).get('cursor'):
                return ('data: ' + json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [{'name': 'legifrance_get_article', 'inputSchema': {'type': 'object', 'properties': {'ref': {'type': 'object', 'properties': {'identifiant': {'type': 'string'}}, 'required': ['identifiant']}}, 'required': ['ref']}}], 'nextCursor': 'p2'}})).encode()
            return json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {'tools': [{'name': 'legifrance_search', 'description': 'Recherche de textes', 'inputSchema': {'type': 'object', 'properties': {'filtre': {'type': 'object', 'properties': {'terme': {'type': 'string'}}, 'required': ['terme']}, 'page_size': {'type': 'integer'}, 'fond': {'type': 'string'}}, 'required': ['filtre', 'fond']}}]}}).encode()
        if payload['method'] == 'tools/call':
            args = payload['params']['arguments']
            if payload['params']['name'] == 'legifrance_search':
                assert args['filtre']['terme'] and 'fond' in args and args.get('page_size') == 3
                return json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {'content': [{'type': 'text', 'text': json.dumps([{'id': 'LEGIARTI1', 'title': 'Article 1240'}])}]}}).encode()
            assert args['ref']['identifiant'] == 'LEGIARTI1'
            return json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {'content': [{'type': 'text', 'text': 'Tout fait quelconque de l’homme…'}]}}).encode()
        raise AssertionError(payload)


class MCP(Base):
    def test_session_pagination_schema_arguments_and_three_level_diagnostic(self):
        from agent import mcp5614
        item = {'endpoint': 'https://mcp.example.test/mcp', 'auth_type': 'none'}
        with patch('agent.extensions364.validate_endpoint'):
            d = mcp5614.diagnostic(item, http=FakeMCP())
        self.assertEqual((d['connection'], d['search'], d['text']), ('ok', 'ok', 'ok'));self.assertTrue(d['details']['session']);self.assertEqual(len(d['details']['tools']), 2)
        self.assertEqual(d['details']['search_tool'], 'legifrance_search');self.assertEqual(d['details']['text_tool'], 'legifrance_get_article')
        with patch('agent.extensions364.validate_endpoint'):
            r = mcp5614.search(item, 'prescription', 3, http=FakeMCP())
        self.assertEqual(r['status'], 'ok');self.assertEqual(r['rows'][0]['id'], 'LEGIARTI1')
        class Down(FakeMCP):
            def request(self, *a, **k): raise Stop('connexion_http_indisponible')
        with patch('agent.extensions364.validate_endpoint'):
            r = mcp5614.search(item, 'x', 3, http=Down())
        self.assertEqual(r['status'], 'unavailable');self.assertNotIn('introuvable', r['error'])


class Interface(Base):
    def test_today_has_routines_bar_compact_decisions_and_agenda_defaults_to_week(self):
        body = self.request('/aujourdhui')['body']
        for text in ('id="c5614-routines"', 'data-routine="briefing"', 'id="c5614-pause"', '/static/v5614.js'):
            self.assertIn(text, body)
        self.assertNotIn('id="c569-decisions"', body)                                               # rien à décider : rien d'affiché
        self.assertLess(body.index('id="c5614-routines"'), body.index('data-legacy-composer'))     # routines sous le titre (5.6.19 : Pilote flottant)
        self.task_model.missing = ['juridiction']
        m = taches5614.create(self.desk, {'instruction': 'Prépare une assignation', 'matter': ALPHA, 'parcours': 'assignation', 'request_key': 'req5614-ui' + 'u' * 18, 'answers': {}})
        self.run_all(m['id'], rounds=2)
        body = self.request('/aujourdhui')['body'];self.assertIn('Ouvrir les décisions', body);self.assertIn('urgente(s)', body)
        for text in ('id="c5614-open"', 'id="c5614-panel"', 'data-act="m5614-answer"'):
            self.assertIn(text, body)
        self.desk.c['calendar'] = {**self.desk.c.get('calendar', {}), 'urls': []}
        page = self.request('/planning', query='vue=agenda')['body']
        self.assertIn('aria-current="page" href="/agent-courriel/planning?vue=agenda&amp;view=week', page)
        self.request('/planning', query='vue=agenda&view=list');self.assertEqual(self.desk.settings('agenda5614:view', ''), 'list')
        self.assertIn('aria-current="page" href="/agent-courriel/planning?vue=agenda&amp;view=list', self.request('/planning', query='vue=agenda')['body'])

    def test_decisions_center_gathers_every_kind_with_actions(self):
        from agent import aujourdhui5614, docrequest520 as dr
        self.task_model.missing = ['juridiction']
        m = taches5614.create(self.desk, {'instruction': 'Prépare une assignation', 'matter': ALPHA, 'parcours': 'assignation', 'request_key': 'req5614-dc' + 'd' * 18, 'answers': {}})
        self.run_all(m['id'], rounds=3)
        rid = dr.submit(self.desk, 'Prépare une note sur le bail commercial', '', 'note')['request'];dr.perform(self.desk, 'docrequest520', {'request': rid})
        cards = aujourdhui5614.items(self.desk, prefix='/p')
        kinds = {c['kind'] for c in cards};self.assertIn('mission5614', kinds);self.assertIn('docreq', kinds)
        m5614 = next(c for c in cards if c['kind'] == 'mission5614');self.assertTrue(m5614['urgent']);self.assertTrue(m5614['recommendation']);self.assertTrue(m5614['consequence'])
        html = aujourdhui5614.decisions_html(self.desk, prefix='/p')
        for text in ('data-act="m5614-answer"', 'data-act="m5614-revise"', 'data-act="m5614-cancel"', 'data-act="defer"', 'data-act="docreq-resolve"', 'Compléter les instructions'):
            self.assertIn(text, html)
        aujourdhui5614.defer(self.desk, 'docreq:' + rid)
        self.assertNotIn('docreq', {c['kind'] for c in aujourdhui5614.items(self.desk, prefix='/p')})
        cancelled = taches5614.control(self.desk, {'id': m['id'], 'action': 'cancel'});self.assertEqual(cancelled['state'], 'annulee')
        self.assertFalse([c for c in aujourdhui5614.items(self.desk, prefix='/p') if c['kind'] == 'mission5614'])

    def test_pilot_intents_mission_and_billing_and_missions_page(self):
        self.assertEqual(pilot.classify_intent('Prépare une assignation dans ce dossier')['intent'], 'mission')
        self.assertEqual(pilot.classify_intent('Réponds aux dernières conclusions adverses')['intent'], 'mission')
        self.assertEqual(pilot.classify_intent('Prépare le devis et le projet Invoice Ninja')['intent'], 'facturation')
        self.assertEqual(pilot.classify_intent('Quels sont les risques du contrat ?')['intent'], 'question')
        p = pilot.preview(self.desk, {'instruction': 'Prépare une assignation', 'matter': ALPHA});self.assertEqual((p['intent'], p['parcours']), ('mission', 'assignation'))
        from agent import missions567
        m = missions567.create(self.desk, {'instruction': 'Prépare une assignation au fond', 'matter': ALPHA, 'request_key': 'req5614-pi' + 'p' * 18, 'intent': 'mission'})
        self.assertTrue(m['complex']);self.assertEqual(m['kind'], 'mission5614');self.assertTrue(m['plan']['steps'])
        shim = json.loads(self.request('/api440/m567/mission', query='id=' + m['id'])['body']);self.assertEqual(shim['id'], m['id'])
        f = missions567.create(self.desk, {'instruction': 'Prépare le devis et le projet Invoice Ninja', 'matter': ALPHA, 'request_key': 'req5614-fa' + 'f' * 18, 'intent': 'facturation'})
        self.assertEqual(f['kind'], 'facturation');self.assertTrue(f['result']['missing'])
        page = self.request('/missions-complexes', query='id=' + m['id'])['body']
        for text in ('m5614-tree', 'data-m5614-filter="blocages"', 'T12d', 'Prochaine action', 'data-m5614="revise"'):
            self.assertIn(text, page)
        dock = self.request('/missions')['body'];self.assertIn('value="mission"', dock);self.assertIn('value="facturation"', dock)   # 5.6.19 : plus de chips

    def test_readiness_voice_levels_and_recette_with_injected_error(self):
        from agent import readiness569, recette5613
        self.desk.c['speech568'] = {'provider': 'kokoro', 'url': 'http://127.0.0.1:8880', 'fallback_local': True}
        with patch('agent.readiness569._connector', return_value=(True, 'ok')):
            report = readiness569.run(self.desk)
        keys = {c['key'] for c in report['checks']};self.assertIn('tts_fallback', keys);self.assertIn('voice_summary', keys)
        r = recette5613.run(self.desk, dav=self.docs, box=t5613.FakeMailbox(), stamp='T5')
        by = {s['id']: s for s in r['steps']}
        self.assertTrue(by['control']['ok']);self.assertTrue(by['resume']['ok']);self.assertTrue(by['word']['ok']);self.assertIsNone(by['template']['ok'])
        self.assertFalse(r['ok']);self.assertTrue(r['incomplete'])                                          # prérequis non testé : pas de succès global
        t5613.install_template(self.desk)
        r = recette5613.run(self.desk, dav=self.docs, box=t5613.FakeMailbox(), stamp='T6');self.assertTrue(r['ok'])


if __name__ == '__main__':
    unittest.main()
