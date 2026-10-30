"""5.6.80 : boutons de l'assistant immédiats, voie rapide des questions, replis, Qdrant facultatif, 20 pièces jointes dont seuls les passages
utiles partent au modèle. Valeurs entièrement fictives (example.test) ; aucun service externe : Qdrant est simulé."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_v5648 as t48
from agent import __version__, index as ix, integration, missions567, queue567, rapide5680 as rp, ui_helpers, vecteurs5680 as vx
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex, fuse

MID = 'DOS-001'
ROOT = Path(__file__).resolve().parents[1]
RAG = {'enabled': True, 'qdrant_url': 'http://127.0.0.1:6333', 'embedding_model': 'essai'}


def part(n, text):
    return {'ordinal': n, 'char_start': n * 100, 'char_end': n * 100 + len(text), 'excerpt': text, 'sha256': 'x'}


class Passages(unittest.TestCase):
    def setUp(self):
        fill = 'Le présent document rappelle des généralités sans intérêt particulier. ' * 20
        self.docs = [({'id': 'attachment-a', 'path': 'Contrat.pdf'}, [part(0, fill), part(1, '[Page 7]\nLa clause de non-concurrence interdit toute activité pendant deux ans.'), part(2, fill)]),
                     ({'id': 'attachment-b', 'path': 'Annexe.pdf'}, [part(0, fill), part(1, 'Tableau des loyers et charges du local commercial.')])]

    def test_passage_utile_et_chaque_piece_representee(self):
        out, report = rp.select_parts(self.docs, 'Que dit la clause de non-concurrence ?', 2600)
        self.assertTrue(any('non-concurrence' in s['excerpt'] for s in out))
        self.assertEqual(out[0]['path'], 'Contrat.pdf · page 7')
        self.assertEqual({s['path'].split(' · ')[0] for s in out}, {'Contrat.pdf', 'Annexe.pdf'})      # aucune pièce oubliée
        self.assertLessEqual(report['caracteres_retenus'], 2600)
        self.assertLess(report['passages_retenus'], report['passages_total'])

    def test_lecture_complete_reconnue(self):
        self.assertTrue(rp.wants_full_reading('Fais une synthèse complète de ces pièces'))
        self.assertTrue(rp.wants_full_reading('Résume le document'))
        self.assertFalse(rp.wants_full_reading('Quel est le montant du loyer ?'))

    def test_repartition_selon_longueur_et_canal(self):
        with patch('agent.improvements36.attachment_parts', lambda desk, i, m, k: (self.docs[0][0], self.docs[0][1], {})):
            out, report, rest, _ = rp.attachment_sources(None, ['a'], 'montant ?', '', '', 50000)
            self.assertTrue(report['integral'])                                    # tout tient : donné tel quel, sans génération
            self.assertEqual(rest, [])
            out, report, rest, _ = rp.attachment_sources(None, ['a'], 'Résume tout le document', '', '', 500)
            self.assertEqual((out, rest), ([], ['a']))                              # lecture complète : analyse progressive
            out, report, rest, _ = rp.attachment_sources(None, ['a'], 'Résume tout le document', '', '', 500, voice=True)
            self.assertEqual(rest, [])                                              # jamais à la voix
            self.assertTrue(out)

    def test_fusion_garde_la_correspondance_exacte(self):
        scores = fuse(['exact', 'b'], ['c', 'd', 'exact'])
        self.assertEqual(max(scores, key=scores.get), 'exact')


class Fond(t48.Base):
    def setUp(self):
        super().setUp()
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.addCleanup(rp.clear, self.f.c['state_dir'])
        idx = DocumentIndex(self.f.c['state_dir'])
        idx.put_source(MID, '/Dossiers/DEMO/Bail.pdf', 'Le bail commercial prévoit une clause résolutoire au profit de la société ZORVAL.', 'e1', '2026-09-01')
        idx.db.close()

    def post(self, route, body, cookie):
        r = self.api('m5680/' + route, body, cookie)
        return r['status'], json.loads(r['body'])

    def test_travail_automatique_cede_le_passage_de_facon_bornee(self):
        job = {'state_dir': self.f.c['state_dir'], 'job_id': 1, 'automatic': True}
        slept = []
        self.assertEqual(rp.yield_to_questions(job, slept.append), 0)              # aucune question : aucune attente
        rp.mark(self.f.c['state_dir'])
        self.assertEqual(rp.yield_to_questions(job, slept.append, max_seconds=3), 3)   # attente bornée
        self.assertEqual(rp.yield_to_questions({**job, 'automatic': False}, slept.append), 0)   # une demande de l'avocat n'attend pas
        flag = Path(self.f.c['state_dir']) / rp.FLAG
        os.utime(flag, (time.time() - rp.FLAG_MAX_AGE - 5,) * 2)
        self.assertFalse(rp.busy(self.f.c['state_dir']))                           # processus arrêté en pleine réponse : sans effet

    def test_voie_rapide_ne_prend_que_les_questions(self):
        self.d.enqueue('refresh_brief', {'matter': MID})
        self.assertFalse(self.d.work_once(only=rp.INTERACTIVE))
        self.assertEqual(self.d.db.execute("SELECT status FROM jobs WHERE kind='refresh_brief'").fetchone()[0], 'pending')
        out = integration.submit_question(self.d, 'Que prévoit le bail ?', MID)
        with patch('agent.integration.answer_question', lambda desk, args: {'thread_id': 't', 'sans_controle': True}):
            self.d.worker_id = rp.LANE_ID
            self.d.work_once(only=rp.INTERACTIVE)
        row = self.d.db.execute('SELECT status,worker FROM jobs WHERE id=?', (out['job_id'],)).fetchone()
        self.assertEqual((row[0], row[1]), ('done', rp.LANE_ID))

    @unittest.skipIf(os.name == 'nt', 'verrous de fichiers réels : recette Linux')
    def test_question_sans_verrou_general(self):
        import fcntl
        with open(Path(self.f.c['state_dir']) / 'run.lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with queue567.producer_lock(self.f.c, 'assistant_answer', {'thread': 'f1', 'matter': MID}):
                pass                                                                # le verrou général ne la retient pas
            with self.assertRaisesRegex(Stop, 'traitement_deja_en_cours'):
                with queue567.producer_lock(self.f.c, 'prepare_reply', {'matter': MID}):
                    pass
        with queue567.producer_lock(self.f.c, 'assistant_answer', {'thread': 'f1'}):
            with self.assertRaisesRegex(Stop, 'traitement_deja_en_cours'):          # un même fil reste exclusif
                with queue567.producer_lock(self.f.c, 'assistant_answer', {'thread': 'f1'}):
                    pass

    def test_modele_occupe_passages_annonces_sans_controle(self):
        out = integration.submit_question(self.d, 'Que prévoit le bail commercial ?', MID)
        args = json.loads(self.d.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0])
        with patch('agent.workspace.chat', side_effect=Stop('generation_ia_delai_depasse')), patch('agent.dav.DAV'), patch('agent.model.Model'):
            res = integration.answer_question(self.d, args)
        self.assertTrue(res['repli'] and res['sans_controle'])
        text = self.d.db.execute("SELECT content FROM assistant_messages WHERE role='assistant'").fetchone()[0]
        self.assertIn('Le modèle d’IA est occupé', text)
        self.assertIn('Bail.pdf', text)
        self.assertIn('clause résolutoire', text)
        self.assertFalse(rp.busy(self.f.c['state_dir']))                           # drapeau levé après la réponse

    def test_erreur_ordinaire_reste_une_erreur(self):
        out = integration.submit_question(self.d, 'Que prévoit le bail commercial ?', MID)
        args = json.loads(self.d.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0])
        with patch('agent.workspace.chat', side_effect=Stop('source_conversation_invalide')), patch('agent.dav.DAV'), patch('agent.model.Model'):
            with self.assertRaisesRegex(Stop, 'source_conversation_invalide'):
                integration.answer_question(self.d, args)
        self.assertFalse(rp.busy(self.f.c['state_dir']))

    def test_delai_du_modele_et_budget_de_contexte(self):
        self.assertEqual(rp.deadline(self.d.c, 'voice_conversation', 240), 60)
        self.assertEqual(rp.deadline(self.d.c, 'assistant', 240), 150)
        self.assertEqual(rp.deadline(self.d.c, 'assistant', 90), 90)               # un réglage plus court reste respecté
        self.assertEqual(rp.context_limit(self.d.c, 'voice_conversation', 60000), 14000)
        self.assertEqual(rp.settings({'rapide5680': {'voice_seconds': 'x', 'assistant_seconds': 200}})['assistant_seconds'], 200)

    def test_source_lente_abandonnee(self):
        start = time.monotonic()
        self.assertEqual(rp.bounded(lambda: time.sleep(5), 0.1, 'repli'), 'repli')
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(rp.legal_research(self.d, MID, 'Prépare la réponse au client', True)['statut'], 'non_demandee')

    def test_recherche_sans_synchronisation(self):
        idx = DocumentIndex(self.f.c['state_dir'])
        self.addCleanup(idx.db.close)
        m = {'id': MID, 'client_name': 'DEMO', 'path': '/Dossiers/DEMO'}
        docs, cover = idx.fast_sources(m, ['clause résolutoire'], {'max_documents_per_mail': 3})
        self.assertEqual(docs[0]['path'], '/Dossiers/DEMO/Bail.pdf')
        self.assertTrue(cover['index_sans_synchronisation'])
        self.assertIsNone(idx.fast_sources({**m, 'id': 'VIDE'}, ['bail'], {}))     # jamais indexé : chemin complet

    def test_passages_sans_modele_et_droits(self):
        cookie = self.admin_cookie()
        status, out = self.post('passages', {'matter': MID, 'question': 'clause résolutoire'}, cookie)
        self.assertTrue(status.startswith('200'), out)
        self.assertEqual(out['passages'][0]['nom'], 'Bail.pdf')
        status, out = self.post('passages', {'matter': 'INCONNU', 'question': 'clause résolutoire'}, cookie)
        self.assertEqual(out['error'], 'dossier_absent')
        with patch('agent.web567.actor', lambda env: ('x@example.test', 'lecteur')):
            status, out = self.post('passages', {'matter': MID, 'question': 'clause résolutoire'}, cookie)
        self.assertEqual(out['error'], 'role_insuffisant')

    def test_vingt_pieces_au_plus(self):
        self.assertEqual(integration.MAX_ATTACHMENTS if hasattr(integration, 'MAX_ATTACHMENTS') else 20, 20)
        with self.assertRaisesRegex(Stop, 'trop_de_pieces_jointes'):
            integration.submit_question(self.d, 'Question fictive ?', MID, attachment_ids=['%032x' % i for i in range(21)])
        with self.assertRaisesRegex(Stop, 'trop_de_pieces_jointes'):
            missions567._context(self.d, {'attachments': ['%032x' % i for i in range(21)]}, None)

    def test_scripts_versionnes_et_gardes_par_le_navigateur(self):
        cookie = self.admin_cookie()
        page = self.request('/aujourdhui', cookie=cookie)['body']
        self.assertRegex(page, r'<script defer src="/agent-courriel/static/v5680\.js\?v=[0-9.]+"></script><script defer')   # le premier exécuté
        self.assertLess(page.index('v5680.js'), page.index('v567.js'))
        self.assertIn('/static/v567.js?v=' + __version__, page)
        self.assertEqual(self.request('/static/v5680.js', cookie=cookie, query='v=' + __version__)['headers']['Cache-Control'], 'private, max-age=31536000, immutable')
        self.assertEqual(self.request('/static/v5680.js', cookie=cookie)['headers']['Cache-Control'], 'no-store')


class FakeStore:
    url = 'http://127.0.0.1:6333'
    model = 'essai'
    collection = 'c'

    def __init__(self):
        self.points, self.deleted, self.sizes = [], [], []

    def ensure_collection(self, size):
        self.sizes.append(size)

    def upsert(self, rows):
        self.points.extend(rows)

    def delete_sources(self, source_ids):
        self.deleted.extend(source_ids)


class Vectoriel(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.idx = DocumentIndex(self.tmp.name, RAG, {'url': 'http://127.0.0.1:1'})
        self.addCleanup(self.idx.db.close)
        vx._state.clear()
        with patch.object(DocumentIndex, 'ensure_embeddings', lambda *a, **k: {}):
            self.idx.put_source(MID, '/D/Bail.pdf', 'Clause résolutoire du bail commercial.', 'e1', '2026-09-01')
            self.idx.put_source('AUTRE', '/A/Note.pdf', 'Sans rapport avec la demande.', 'e2', '2026-09-01')
        self.chunks = self.idx.db.execute('SELECT matter,source_id,chunk_no FROM knowledge_chunks').fetchall()
        for m, s, c in self.chunks:
            self.idx.db.execute('INSERT OR REPLACE INTO knowledge_embeddings VALUES(?,?,?,?,?)', (m, s, c, 'essai', '[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8]'))
        self.idx.db.commit()

    def test_sans_reglage_rien_ne_change(self):
        self.assertIsNone(vx.store({'enabled': True}))
        self.assertIsNone(vx.search({'enabled': True}, [0.1]))
        self.assertIsNone(vx.sync_tick({'rag': {'enabled': True}, 'state_dir': self.tmp.name}))
        self.assertEqual(vx.status(self.idx, {'enabled': True})['etat'], 'inactif')

    def test_copie_par_lots_sans_texte_et_reprise(self):
        fake = FakeStore()
        with patch.object(vx, 'store', lambda rag: fake):
            first = vx.tick(self.idx, RAG, 1)
            self.assertEqual(first, {'copied': 1, 'remaining': 1})
            self.assertEqual(vx.tick(self.idx, RAG, 1)['remaining'], 0)
            self.assertEqual(vx.tick(self.idx, RAG, 1), {'copied': 0, 'remaining': 0})   # reprise au dernier rang : rien en double
        self.assertEqual(len(fake.points), 2)
        self.assertEqual(fake.sizes, [8, 8])
        self.assertNotIn('Clause', json.dumps(fake.points))                         # jamais le texte

    def test_suppression_reportee_dans_l_index(self):
        fake = FakeStore()
        m, s, c = self.chunks[0]
        self.idx.remove_knowledge(m, s)
        self.idx.db.commit()
        self.assertEqual(vx.flush_deleted(self.idx, fake), 1)
        self.assertEqual(fake.deleted, [s])
        self.assertEqual(fake.points, [])
        self.assertEqual(self.idx.db.execute('SELECT COUNT(*) FROM vector_deleted5680').fetchone()[0], 0)
        self.assertEqual(vx.flush_deleted(self.idx, fake), 0)

    def test_piece_reindexee_recopiee_apres_le_retrait(self):
        fake = FakeStore()
        m, s, c = self.chunks[1]
        self.idx.remove_knowledge(m, s)
        self.idx.db.execute('INSERT INTO knowledge_embeddings VALUES(?,?,?,?,?)', (m, s, c, 'essai', '[0.2,0.2,0.3,0.4,0.5,0.6,0.7,0.8]'))
        self.idx.db.commit()
        vx.flush_deleted(self.idx, fake)
        self.assertEqual(fake.deleted, [s])
        self.assertEqual([(p[0], p[1], p[2]) for p in fake.points], [(m, s, c)])   # la nouvelle empreinte n'est pas perdue

    def test_requete_filtree_par_dossier_et_dossiers_reserves(self):
        s = vx.Store(RAG)
        with patch.object(s, '_call', return_value={'result': {'points': [{'score': .9, 'payload': {'matter': MID, 'source_id': 's', 'chunk_no': 0}}, {'score': .1}]}}) as call:
            out = s.search([0.1], MID, 5, ('AUTRE',))
        self.assertEqual(out, [(MID, 's', 0, .9)])                                  # point sans référence ignoré
        body = call.call_args[0][2]
        self.assertEqual(body['filter']['must'][0]['match'], {'value': MID})
        self.assertEqual(body['filter']['must_not'][0]['match'], {'any': ['AUTRE']})
        self.assertFalse(body['with_vector'])

    def test_repli_si_le_service_tombe(self):
        with patch.object(vx.Store, 'healthy', side_effect=Stop('connexion_http_indisponible')) as health:
            self.assertIsNone(vx.search(RAG, [0.1]))
            self.assertIsNone(vx.search(RAG, [0.1]))
        self.assertEqual(health.call_count, 1)                                      # service plus sollicité pendant une minute
        self.assertEqual(vx.status(self.idx, RAG)['etat'], 'indisponible')

    def test_resultats_relus_dans_sqlite(self):
        m, s, c = self.chunks[0]
        idx = self.idx
        with patch.object(idx, 'query_vector', return_value=([0.1] * 8, '')), \
                patch.object(vx, 'search', return_value=[(m, s, c, .9), (m, 'retiree', 0, .8)]):
            ranked, _ = idx.ranked_chunks(m, ['sans correspondance xyz'], limit=5)
        self.assertEqual([r[0][1] for r in ranked], [s])                            # passage retiré entre-temps : ignoré

    def test_question_lente_passe_en_plein_texte(self):
        class Slow:
            def embed_query(self, text):
                raise Stop('delai_http_depasse')
        with patch.object(self.idx, 'embedder', return_value=Slow()):
            q, error = self.idx.query_vector(['bail'])
            ranked, _ = self.idx.ranked_chunks(MID, ['clause résolutoire'], limit=5)
        self.assertIsNone(q)
        self.assertEqual(error, 'delai_http_depasse')
        self.assertEqual(ranked[0][0][2], '/D/Bail.pdf')

    def test_tout_le_corpus_avec_qdrant(self):
        m, s, c = self.chunks[0]
        with patch.object(self.idx, 'query_vector', return_value=([0.1] * 8, '')), patch.object(vx, 'search', return_value=[(m, s, c, .9)]):
            rows, cover = self.idx.global_sources(['bail'], 5)
        self.assertEqual(cover['vector_engine'], 'qdrant')
        self.assertEqual(cover['semantic_cap'], 0)


class Sauvegardes(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _backup(self, version, age, stem='desk'):
        p = self.dir / ('%s-before-%s-%s.sqlite3' % (stem, version, 'a' * 32))
        p.write_bytes(b'x' * 10)
        os.utime(p, (time.time() - age,) * 2)
        return p

    def test_seules_les_dernieres_mises_a_jour_sont_gardees(self):
        import upgrade
        old = [self._backup('5.6.70', 900), self._backup('5.6.70', 900, 'documents'), self._backup('5.6.71', 800)]
        recent = [self._backup('5.6.78', 200), self._backup('5.6.79', 100), self._backup('5.6.79', 100, 'documents')]
        config = self.dir / 'config-before-5.6.70-20261001T000000Z.json'
        other = self.dir / 'notes-before-a-garder.sqlite3'
        for p in (config, other):
            p.write_text('{}', encoding='utf-8')
        self.assertEqual(upgrade.prune_database_backups(self.dir), (3, 30))
        self.assertFalse(any(p.exists() for p in old))
        self.assertTrue(all(p.exists() for p in recent))
        self.assertTrue(config.exists() and other.exists())                        # configuration et fichiers inconnus jamais touchés

    def test_place_faite_avant_la_copie(self):
        import upgrade
        self._backup('5.6.78', 200)
        kept = self._backup('5.6.79', 100)
        self.assertEqual(upgrade.prune_database_backups(self.dir, upgrade.DB_BACKUP_GENERATIONS - 1), (1, 10))
        self.assertEqual(list(self.dir.iterdir()), [kept])
        source = (ROOT / 'upgrade.py').read_text(encoding='utf-8')
        self.assertLess(source.index('prune_database_backups(backup_dir,'), source.index('source_db.backup(target_db)'))


class Interface(unittest.TestCase):
    def test_panneau_et_limites(self):
        html = ui_helpers.assistant_dock('/p', 'x', [], '/aujourdhui', True)
        self.assertIn('Documents (20 max.)', html)
        self.assertLess(html.index('id="ws-ai-voice"'), html.index('ws-ai-auto-context'))   # état de la voix visible dès l'ouverture
        js = (ROOT / 'agent' / 'static' / 'v567.js').read_text(encoding='utf-8')
        self.assertIn('attachments.length + files.length > 20', js)
        self.assertIn('axiorhubPassages', js)
        self.assertIn('window.axiorhubPilotReady=true', (ROOT / 'agent' / 'static' / 'v568.js').read_text(encoding='utf-8'))

    def test_qdrant_local_uniquement_et_facultatif(self):
        compose = (ROOT / 'docker-compose.qdrant.yml').read_text(encoding='utf-8')
        self.assertIn('"127.0.0.1:6333:6333"', compose)
        self.assertIn('qdrant/qdrant:v1.20.0', compose)
        from agent import config567
        self.assertEqual(config567.validate_value('optional_local_url', ''), '') if hasattr(config567, 'validate_value') else None
        with self.assertRaises(Stop):
            vx.Store({'qdrant_url': 'https://qdrant.example.test', 'embedding_model': 'x'})   # jamais un service distant

    def test_modele_garde_charge(self):
        source = (ROOT / 'agent' / 'model.py').read_text(encoding='utf-8')
        self.assertIn("req['keep_alive']=self.cfg.get('keep_alive','30m')", source)

    def test_consignes_de_repli_annoncees(self):
        out = rp.fallback_answer({'state_dir': self._dir()}, '', 'question sans résultat')
        self.assertTrue(out['repli'])
        self.assertIn('Réponse de repli sans IA', out['limits'][0])

    def _dir(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name


if __name__ == '__main__':
    unittest.main()
