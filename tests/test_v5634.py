"""5.6.34 : cahier des charges « Audit et préparation de la nouvelle version » — contre-tests T01 à T16 transformés en tests de
non-régression sur l'implémentation corrigée, recettes L01 à L06 (profil Juridique), U01 à U07 (parcours), agenda local, état du système
selon le mode d'installation, contrat /integration/v1 et chaîne des versions acceptées. Données fictives uniquement."""
import ast
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
MID = '20240101001'


def _index(desk, rows):
    from agent.index import DocumentIndex
    idx = DocumentIndex(desk.c['state_dir'])
    for path, mod, text, err in rows:
        idx.db.execute('INSERT OR REPLACE INTO docs(matter,path,etag,modified,text,error) VALUES(?,?,?,?,?,?)', (MID, path, 'e-' + path[-6:], mod, text, err))
    idx.db.commit()
    idx.db.close()


class Base(t530.Base):
    pass


class FragmentModel:
    """Modèle factice des relevés : réponses successives (une par appel), configuration exposée comme celle de Model."""
    cfg = {'model': 'modele-essai', 'profile': 'juridique'}

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        self.calls += 1
        value = self.answers.pop(0) if self.answers else {'nature': 'pièce', 'faits': [], 'parties': [], 'demandes': [], 'montants': [], 'references': [],
                                                           'points_cles': [], 'exhaustif': True}
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def ok(**k):
    base = {'nature': 'conclusions', 'faits': [], 'parties': [], 'demandes': [], 'montants': [], 'references': [], 'points_cles': [], 'exhaustif': True}
    base.update(k)
    return base


TEXT = ('[Page 1]\nPar contrat du 12 mars 2025, la société EXEMPLE a confié à la société ALPHA la réalisation d’un logiciel.\n'
        '[Page 2]\nLa société EXEMPLE réclame la somme de 4 500 euros au titre de l’acompte versé.\n'
        'Elle demande en outre la résolution du contrat aux torts exclusifs de la société ALPHA.\n')


# --------------------------------------------------------------------------------------------- B12 : validation JSON Schema
class SchemaValidation(unittest.TestCase):
    def test_types_bounds_enums_and_optional_fields(self):
        from agent.schema5634 import SchemaError, validate
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['n', 'mode'],
                  'properties': {'n': {'type': 'integer', 'minimum': 0}, 'mode': {'type': 'string', 'enum': ['a', 'b']},
                                 'note': {'type': 'string', 'maxLength': 5}}}
        validate({'n': 3, 'mode': 'a'}, schema)                                         # champ facultatif absent : accepté
        for bad in ({'n': 3.5, 'mode': 'a'}, {'n': True, 'mode': 'a'}, {'n': 3, 'mode': 'c'}, {'n': 3, 'mode': 'a', 'x': 1},
                    {'n': 3, 'mode': 'a', 'note': 'trop long'}, {'n': -1, 'mode': 'a'}, {'mode': 'a'}):
            with self.assertRaises(SchemaError) as ctx:
                validate(bad, schema)
            self.assertEqual(str(ctx.exception), 'schema_ia_invalide')
            self.assertTrue(ctx.exception.errors[0][0].startswith('/'))               # chemin précis
        with self.assertRaises(SchemaError):
            validate({'n': 3.0, 'mode': 'a'}, schema)                                   # 3.0 n'est pas un entier

    def test_every_schema_of_the_engine_is_within_the_supported_subset(self):
        from agent import model, synthese5632
        from agent.schema5634 import check_schema
        schemas = [v for v in vars(model).values() if isinstance(v, dict) and v.get('type') == 'object' and 'properties' in v]
        self.assertGreater(len(schemas), 20)
        for s in schemas + [synthese5632.DIGEST_SCHEMA]:
            check_schema(s)

    def test_model_ask_retries_once_then_fails_readably(self):
        from agent import model
        calls = []
        m = model.Model.__new__(model.Model)
        m.cfg = {'temperature': 0.3}
        m.provider_type = 'ollama'

        def complete(messages, temperature=None, max_tokens=0, json_schema=None):
            calls.append(temperature)
            return json.dumps({'needs_reply': 'oui'})
        m.complete = complete
        m._hybrid_choice = lambda *a, **k: None
        with self.assertRaises(Stop):
            m.ask('triage', {'x': 1})
        self.assertEqual(len(calls), 2)                                                  # une reprise unique, bornée


# --------------------------------------------------------------------------------------------- B01, B02, B03, B05, B11 : relevés
class PieceDigests(Base):
    def setUp(self):
        super().setUp()
        _index(self.desk, [('/Dossiers/DEMO/Conclusions.pdf', '2025-06-01', TEXT, '')])

    def doc(self):
        from agent import synthese5632
        return next(d for d in synthese5632._docs(self.desk, MID) if d['path'].endswith('Conclusions.pdf'))

    def test_t01_unsupported_dates_amounts_and_pages_are_isolated(self):
        from agent import synthese5632
        answer = ok(faits=[{'date': '2030-12-31', 'texte': 'Fait inventé', 'extrait': 'événement absent du document'},
                           {'date': '12 mars 2025', 'texte': 'Contrat de réalisation', 'extrait': 'Par contrat du 12 mars 2025, la société EXEMPLE a confié'}],
                    montants=[{'montant': '999 999 €', 'objet': 'inventé', 'extrait': 'réclame la somme de 4 500 euros'},
                              {'montant': '4 500 €', 'objet': 'acompte', 'extrait': 'réclame la somme de 4 500 euros au titre de l’acompte'}],
                    demandes=[{'texte': 'Résolution du contrat', 'extrait': 'Elle demande en outre la résolution du contrat'}])
        model = FragmentModel([answer])
        self.assertEqual(synthese5632.build_digest(self.desk, MID, self.doc(), model), 'built')
        d = synthese5632.digest_of(self.desk, MID, self.doc()['path'], self.doc()['sha256'])
        faits = {f['texte']: f for f in d['faits']}
        self.assertEqual(faits['Fait inventé']['statut'], 'a_verifier')
        self.assertIn('extrait_introuvable', faits['Fait inventé']['causes'])
        self.assertEqual(faits['Contrat de réalisation']['statut'], 'documente')
        self.assertEqual((faits['Contrat de réalisation']['date'], faits['Contrat de réalisation']['page']), ('2025-03-12', '1'))   # page déduite de l'extrait
        montants = {m['montant']: m for m in d['montants']}
        self.assertIn('montant_absent_du_passage', montants['999 999 €']['causes'])
        self.assertEqual(montants['4 500 €']['statut'], 'documente')
        self.assertEqual(d['demandes'][0]['page'], '2')

    def test_t01_invalid_output_is_retried_once_then_marked_incomplete_never_empty_success(self):
        from agent import synthese5632
        model = FragmentModel([json.dumps({'nature': 'x'}), json.dumps({'nature': 'x'})])   # six propriétés obligatoires absentes, deux fois
        self.assertEqual(synthese5632.build_digest(self.desk, MID, self.doc(), model), 'incomplete')
        self.assertEqual(model.calls, 2)
        rec = synthese5632.record_of(self.desk, MID, self.doc()['path'], self.doc()['sha256'])
        self.assertEqual(rec['status'], 'incomplet')
        self.assertEqual(rec['digest']['controle']['fragments_invalides'], ['1'])

    def test_unpaginated_text_gets_a_position_never_an_invented_page(self):
        from agent import synthese5632
        _index(self.desk, [('/Dossiers/DEMO/Note.docx', '2025-06-02', 'Première partie.\n\nLe client a versé 1 200 euros le 2 février 2025.\n', '')])
        doc = next(d for d in synthese5632._docs(self.desk, MID) if d['path'].endswith('Note.docx'))
        model = FragmentModel([ok(faits=[{'texte': 'Versement', 'date': '2025-02-02', 'extrait': 'Le client a versé 1 200 euros le 2 février 2025'}])])
        synthese5632.build_digest(self.desk, MID, doc, model)
        f = synthese5632.digest_of(self.desk, MID, doc['path'], doc['sha256'])['faits'][0]
        self.assertNotIn('page', f)
        self.assertEqual(f['paragraphe'], 2)
        self.assertEqual(f['statut'], 'documente')

    def test_t02_full_index_is_kept_and_short_view_flags_omissions(self):
        from agent import synthese5632
        faits = [{'date': '2025-01-%02d' % (i + 1), 'texte': 'Fait %d' % i, 'extrait': 'x'} for i in range(24)]
        merged = synthese5632._merge([{'faits': faits[:12]}, {'faits': faits[12:] + [dict(faits[0], date='2025-02-01')]}])
        self.assertEqual(len(merged['faits']), 25)                                       # 24 faits + même texte à une autre date
        out = synthese5632._render('Pièce', '2025-06-01', {**merged, 'demandes': [{'texte': 'D%d' % i} for i in range(15)]}, 3)
        self.assertIn('élément(s) non affiché(s)', out)
        crit = synthese5632._render('Assignation', '', {**merged, 'demandes': [{'texte': 'D%d' % i} for i in range(45)]}, 3, critical=True)
        self.assertIn('D44', crit)                                                       # une demande en fin d'acte reste accessible

    def test_t03_failures_do_not_block_the_next_pieces_and_progress_survives(self):
        from agent import synthese5632
        rows = [('/Dossiers/DEMO/P%d.pdf' % i, '2025-06-0%d' % (i % 9 + 1), '[Page 1]\nTexte de la pièce numéro %d.\n' % i, '') for i in range(7)]
        _index(self.desk, rows)
        down = Stop('erreur_modele_ponctuelle')
        model = FragmentModel([down] * 7)
        with patch.object(self.desk, '_then') as then:
            first = synthese5632.build_job(self.desk, {'matter': MID}, model)
        self.assertEqual(len(first['errors']), 6)
        self.assertTrue(then.called)                                                     # suite programmée malgré zéro succès
        model.answers = []                                                                # le modèle répond désormais
        second = synthese5632.build_job(self.desk, {'matter': MID}, model)
        self.assertGreaterEqual(second['built'], 1)                                       # la septième pièce est traitée
        states = synthese5632.states(self.desk, MID)
        self.assertEqual(sum(1 for s in states.values() if s['state'] == 'retry_wait'), 6)
        self.assertTrue(all(s['error'] for s in states.values() if s['state'] == 'retry_wait'))

    def test_t03_general_model_outage_pauses_without_endless_loop(self):
        from agent import synthese5632
        model = FragmentModel([Stop('moteur_ollama_injoignable')] * 3)
        with patch.object(self.desk, '_then') as then:
            out = synthese5632.build_job(self.desk, {'matter': MID}, model)
        self.assertTrue(out['pause'])
        self.assertFalse(then.called)

    def test_t05_coverage_counts_every_file_and_uses_exact_labels(self):
        from agent import synthese5632
        _index(self.desk, [('/Dossiers/DEMO/Protege.pdf', '2025-06-03', '', 'pdf_chiffre'), ('/Dossiers/DEMO/Vide.pdf', '2025-06-03', '', '')])
        out, cov = synthese5632.sources_for(self.desk, {'id': MID, 'path': '/Dossiers/DEMO'}, 50000)
        self.assertEqual((cov['fichiers'], cov['extraits'], cov['protegees'], cov['vides']), (3, 1, 1, 1))
        self.assertEqual(cov['sources_utilisees'], len(out))
        label = synthese5632.coverage_label(cov)
        self.assertIn('3 fichiers', label)
        self.assertIn('1 source utilisée dans cette version', label)
        self.assertNotIn('lue', label)

    def test_t11_method_change_invalidates_and_index_error_is_qualified(self):
        from agent import synthese5632
        synthese5632.build_digest(self.desk, MID, self.doc(), FragmentModel([ok()]))
        self.assertIsNotNone(synthese5632.digest_of(self.desk, MID, self.doc()['path'], self.doc()['sha256']))
        with patch.object(synthese5632, 'METHOD', '9999.0'):
            self.assertIsNone(synthese5632.digest_of(self.desk, MID, self.doc()['path'], self.doc()['sha256']))
        self.desk.setting('synthese5634:changement_modele', 'refaire')
        self.assertIsNone(synthese5632.record_of(self.desk, MID, self.doc()['path'], self.doc()['sha256'], 'autre-modele'))
        with patch('agent.index.DocumentIndex', side_effect=sqlite3.OperationalError('database disk image is malformed')):
            with self.assertRaises(Stop) as ctx:
                synthese5632.inventory(self.desk, MID)
        self.assertEqual(str(ctx.exception), 'index_documents_indisponible')


# --------------------------------------------------------------------------------------------- B04, B09, B10 : fiche du dossier
PIECES = {'/Dossiers/DEMO/Assignation tribunal de commerce.pdf': ('2025-03-04', 'ASSIGNATION du 4 mars 2025. A LA REQUETE DE ALPHA CONTRE BETA SERVICES.'),
          '/Dossiers/DEMO/Contrat de prestation.pdf': ('2023-01-03', 'Contrat de prestation signé le 3 janvier 2023 : livraison au 30 juin 2023.')}


class FicheDossier(Base):
    def setUp(self):
        super().setUp()
        _index(self.desk, [(p, mod, text, '') for p, (mod, text) in PIECES.items()])
        self.result = None

    def fake(self, resume='Litige sur un logiciel.', parties=None, chronologie=None):
        test = self

        class FakeModel:
            def __init__(self, cfg):
                self.cfg = cfg

            def ask(self, stage, payload):
                ids = {p['nom']: p['id'] for p in payload['pieces']}
                a = ids['Assignation tribunal de commerce.pdf']
                return {'resume': resume, 'rappel_des_faits': [{'texte': 'Affirmation sans pièce.', 'source_ids': ['P99']}],
                        'parties': parties or [{'nom': 'SAS ALPHA', 'qualite': 'demandeur', 'conseil': '', 'client_du_cabinet': True, 'source_ids': [a]},
                                               {'nom': 'SARL ALPHA', 'qualite': 'defendeur', 'conseil': '', 'client_du_cabinet': False, 'source_ids': [a]}],
                        'procedure': {'juridiction': '', 'numero_rg': '', 'stade': '', 'prochaine_etape': '', 'source_ids': []},
                        'chronologie': chronologie if chronologie is not None else [
                            {'date': '2026-02-31', 'evenement': 'Date impossible', 'source_ids': [a]},
                            {'date': '2025-03', 'evenement': 'Assignation (mois seulement)', 'source_ids': [a]},
                            {'date': '2025-03-04', 'evenement': 'Assignation délivrée', 'source_ids': [a]}],
                        'enjeux': [], 'points_a_verifier': [], 'limits': []}
        return patch('agent.model.Model', FakeModel)

    def prepare(self, **k):
        from agent import dossier5627
        with self.fake(**k):
            return dossier5627.prepare(self.desk, {'matter': MID})

    def test_t04_lawyer_correction_survives_three_regenerations_and_is_exported(self):
        from agent import dossier5624, dossier5627
        self.prepare()
        dossier5627.save_edit(self.desk, MID, 'resume', 'Résumé validé par l’avocat.')
        for _ in range(3):
            self.prepare(resume='Proposition différente de l’agent.')
        f = dossier5627.latest(self.desk, MID)
        self.assertEqual(f['version'], 4)
        self.assertEqual(f['edits']['resume']['text'], 'Résumé validé par l’avocat.')
        page = self.request('/dossier', query='id=' + MID)['body']
        self.assertIn('Résumé validé par l’avocat.', page)
        self.assertIn('L’agent propose une autre rédaction', page)                     # les deux versions et le choix de l'avocat
        dossier5627.save_edit(self.desk, MID, 'parties', 'SAS ALPHA — Demandeur')
        names = [i['value'] for i in dossier5624.rubrique(self.desk, MID, 'parties')['items']]
        self.assertEqual(names, ['SAS ALPHA — Demandeur'])

    def test_t04_concurrent_corrections_raise_an_explicit_conflict(self):
        from agent import dossier5627
        self.prepare()
        first = dossier5627.save_edit(self.desk, MID, 'faits', 'Version A', base=0)
        self.assertEqual(first['revision'], 1)
        with self.assertRaises(Stop) as ctx:
            dossier5627.save_edit(self.desk, MID, 'faits', 'Version B', base=0)          # second onglet resté sur la révision 0
        self.assertEqual(str(ctx.exception), 'conflit_de_correction')
        self.assertEqual(dossier5627.corrections(self.desk, MID)['faits']['text'], 'Version A')

    def test_t04_historical_edits_are_migrated_into_the_correction_layer(self):
        from agent import dossier5627
        dossier5627.ensure_schema(self.desk)
        self.desk.db.execute('DELETE FROM settings WHERE key=?', ('fiche5634:migration_corrections',))
        self.desk.db.execute("INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,?,?,?,?,?)",
                             (MID, 1, '{}', '[]', json.dumps({'resume': {'text': 'Correction 5.6.33', 'by': 'cabinet', 'at': '2026-10-01T10:00:00'}}), '2026-10-01'))
        self.desk.db.execute("INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,?,?,?,?,?)", (MID, 2, '{}', '[]', '{}', '2026-10-02'))
        self.desk.db.commit()
        self.desk.db.execute('DELETE FROM fiche_corrections5634')
        dossier5627.ensure_schema(self.desk)
        self.assertEqual(dossier5627.latest(self.desk, MID)['edits']['resume']['text'], 'Correction 5.6.33')

    def test_t09_companies_with_different_forms_stay_distinct_until_confirmed(self):
        from agent import dossier5627
        self.prepare()
        data = dossier5627.latest(self.desk, MID)['data']
        self.assertEqual([p['nom'] for p in data['parties']], ['SAS ALPHA', 'SARL ALPHA'])
        self.assertEqual(len(data['rapprochements']), 1)
        self.assertEqual([p['forme_sociale'] for p in data['parties']], ['SAS', 'SARL'])

    def test_t10_impossible_partial_and_unsourced_dates(self):
        from agent import dossier5627
        self.prepare()
        data = dossier5627.latest(self.desk, MID)['data']
        dates = [e['date'] for e in data['chronologie']]
        self.assertEqual(dates, ['2025-03', '2025-03-04'])                              # date partielle conservée partielle
        causes = {x['texte']: x['cause'] for x in data['a_verifier']}
        self.assertEqual(causes['2026-02-31 — Date impossible'], 'date impossible')
        self.assertIn('Affirmation sans pièce.', causes)                                 # un fait sans source valide n'est pas « documenté »
        self.assertEqual(data['rappel_des_faits'], [])

    def test_t05_coverage_label_replaces_pieces_lues(self):
        self.prepare()
        page = self.request('/dossier', query='id=' + MID)['body']
        self.assertIn('2 fichiers, 2 extraits disponibles', page)
        self.assertNotIn('pièce(s) lue(s)', page)

    def test_t08_preparation_request_is_idempotent(self):
        from agent import dossier5627
        a = dossier5627.request(self.desk, MID)
        b = dossier5627.request(self.desk, MID)
        self.assertEqual(a['job_id'], b['job_id'])
        self.assertTrue(b['deja_en_cours'])
        n = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='fiche_dossier5627'").fetchone()[0]
        self.assertEqual(n, 1)


class TrackingScript(unittest.TestCase):
    def test_t08_watch_is_bounded_explains_interruptions_and_never_reloads_blindly(self):
        js = (ROOT / 'agent' / 'static' / 'v5627.js').read_text(encoding='utf-8')
        for needle in ("r.status === 401", "r.status >= 500", "application/json", "Le suivi est interrompu ; le travail peut continuer sur le serveur.",
                       "Reprendre le suivi", "Voir le travail", "LIMIT = 12 * 60 * 1000", "Math.min(60000, delay * 2)"):
            self.assertIn(needle, js)
        self.assertNotIn('++tries > 120', js)


# --------------------------------------------------------------------------------------------- B06 : registre unique
class LegalRegistry(Base):
    def test_t06_every_catalog_provider_is_known_to_every_path(self):
        from agent import legal_research, registre5634, sources5632
        for pid in list(sources5632.PROVIDERS) + ['openlegal', 'pappers']:
            self.assertTrue(registre5634.known(pid), pid)
            self.assertTrue(legal_research._known(pid), pid)
        rows = {r['id']: r for r in registre5634.status(self.desk)}
        self.assertFalse(rows['justicelibre']['active'])
        self.assertIn('Non configurée', rows['justicelibre']['reason'])

    def test_t06_recent_connector_results_are_no_longer_dropped(self):
        from agent import legal_research
        found = [{'connector': 'c1', 'provider': 'justicelibre', 'status': 'ok', 'rows': [{'title': 'Décision fictive', 'identifier': 'X-1'}]}]
        with patch('agent.extensions364.legal_connector_search', return_value=found):
            out = legal_research.research_enabled_mcp(self.desk, MID, 'Question générale fictive sur un délai', 5)
        self.assertEqual([c['provider'] for c in out['connectors']], ['justicelibre'])
        self.assertTrue(out['leads'])
        self.assertEqual(out['verified_authorities'], [])                                 # piste, jamais autorité vérifiée sans contrôle officiel

    def test_u03_general_question_without_matter(self):
        from agent import legal_research
        q = legal_research.prepare_research_query(self.desk, {'question': 'Délai de prescription de l’action en garantie', 'providers': ['justicelibre']})
        self.assertEqual(q['matter'], '')
        page = self.request('/recherche-juridique')['body']
        self.assertIn('Une question générale fonctionne sans dossier', page)
        self.assertNotIn('Identifiant exact du dossier', page)
        self.assertNotIn('openlegal,openlegi,goodlegal,pappers', page)


# --------------------------------------------------------------------------------------------- B07 : consultations sans écriture
class ReadsWithoutWrites(Base):
    def test_t07_signals_listing_never_writes_and_worker_releases_snoozes(self):
        from agent import proactive
        proactive.ensure_schema(self.desk)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.desk.db.execute("INSERT INTO proactive_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             ('s1', MID, 'piece', 'high', 'Nouvelle pièce', 'Détail', '[]', 'Lire', 'snoozed', '', 'f', past, past, past))
        self.desk.db.commit()
        writes = []
        self.desk.db.set_trace_callback(lambda sql: writes.append(sql) if re.match(r'\s*(UPDATE|INSERT|DELETE)', sql, re.I) else None)
        rows = proactive.signals(self.desk, 'open', matter=MID)
        self.desk.db.set_trace_callback(None)
        self.assertEqual([r['state'] for r in rows], ['open'])                            # rappel échu vu ouvert…
        self.assertEqual(writes, [])                                                       # …sans aucune écriture pendant la lecture
        self.assertEqual(proactive.release_snoozed(self.desk), 1)

    def test_t07_hearing_sheet_reads_the_local_index_without_remote_call_or_write(self):
        from agent import audience5613
        _index(self.desk, [('/Dossiers/DEMO/Conclusions récapitulatives.docx', '2026-10-01', 'texte', '')])
        with patch('agent.document_projects._dav', side_effect=AssertionError('aucun appel distant pendant l’affichage')):
            s = audience5613.sheet(self.desk, MID)
        self.assertEqual(s['conclusions'][0]['name'], 'Conclusions récapitulatives.docx')
        self.assertIsNone(self.desk.settings('audience5614:' + MID, None))


# --------------------------------------------------------------------------------------------- B15 : erreurs qualifiées
class QualifiedErrors(unittest.TestCase):
    def test_t15_same_404_different_messages_and_no_secret(self):
        from agent import erreurs5634 as er
        ex = er.http_error(404, 'PROPFIND', 'https://cloud.exemple.fr/remote.php/dav/files/u/CABINET/Client Fictif/piece.pdf?token=secret123456')
        self.assertEqual(str(ex), 'http_404')
        self.assertNotIn('Client Fictif', json.dumps(ex.detail))
        self.assertNotIn('secret123456', json.dumps(ex.detail))
        messages = {er.explain('http_404', {'stage': s, 'service': svc}) for s, svc in
                    (('inventaire', 'nextcloud'), ('lecture_fichier', 'nextcloud'), ('generation', 'ollama'), ('enregistrement', 'base'),
                     ('lecture_fichier', 'stockage_local'))}
        self.assertEqual(len(messages), 5)
        self.assertIn('Ollama', er.explain('modele_ollama_absent'))
        self.assertNotIn('secret', er.safe_text('Authorization: Bearer abcdefghijkl https://x.test/a?sig=zzzzzz'))

    def test_t15_stage_keeps_the_public_code_and_adds_context(self):
        from agent import erreurs5634 as er
        with self.assertRaises(Stop) as ctx:
            with er.stage('inventaire', 'nextcloud'):
                raise Stop('http_404')
        self.assertEqual(str(ctx.exception), 'http_404')
        self.assertEqual(ctx.exception.detail['stage'], 'inventaire')
        self.assertTrue(ctx.exception.detail['correlation_id'])

    def test_local_missing_file_is_not_reported_as_a_server_address(self):
        from agent.erreurs5634 import explain, local_missing
        ex = local_missing('C:/Cabinet/Client/piece.pdf')
        self.assertEqual(str(ex), 'http_404')
        self.assertIn('dossier de travail local', explain(str(ex), ex.detail))


# --------------------------------------------------------------------------------------------- B16, U06 : Détail et Annuler
class DecisionsCancel(Base):
    def _failed_docreq(self):
        from agent import docrequest520 as dr
        rid = dr.submit(self.desk, 'Prépare une note de synthèse fictive', MID, 'note')['request']
        self.desk.db.execute("UPDATE docreq520 SET status='echec', result=? WHERE id=?", (json.dumps({'error': 'redaction_document_impossible'}), rid))
        self.desk.db.execute("UPDATE jobs SET status='error' WHERE kind='docrequest520'")
        self.desk.db.commit()
        return rid

    def test_t16_detail_opens_the_exact_request_and_cancel_is_durable(self):
        from agent import decisions569, decisions5634, docrequest520 as dr
        rid = self._failed_docreq()
        item = next(x for x in decisions569.items(self.desk, 'cabinet', '/p') if x['kind'] == 'docreq')
        self.assertEqual(item['url'], '/p/decision?type=docreq&id=' + rid)               # plus de lien vers la page courante
        before = self.desk.db.execute('SELECT status,updated FROM docreq520 WHERE id=?', (rid,)).fetchone()
        page = self.request('/decision', query='type=docreq&id=' + rid)['body']
        self.assertIn('Prépare une note de synthèse fictive', page)
        self.assertIn('Annuler la demande', page)
        self.assertEqual(tuple(self.desk.db.execute('SELECT status,updated FROM docreq520 WHERE id=?', (rid,)).fetchone()), tuple(before))   # lecture seule
        out = decisions5634.cancel(self.desk, 'docreq', rid)
        self.assertEqual(out['state'], 'annule')
        self.assertEqual(decisions5634.cancel(self.desk, 'docreq', rid)['state'], 'annule')                  # idempotent
        self.assertFalse(any(x['kind'] == 'docreq' for x in decisions569.items(self.desk, 'cabinet', '/p')))
        with self.assertRaises(Stop):
            dr.resolve(self.desk, rid, MID)                                              # aucune reprise silencieuse
        with self.assertRaises(Stop) as ctx:
            dr.run(self.desk, {'request': rid})
        self.assertEqual(str(ctx.exception), 'demande_annulee')
        self.assertEqual(self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (rid,)).fetchone()[0], 'annule')

    def test_t16_failed_ordinary_mission_can_be_cancelled_and_stays_cancelled(self):
        from agent import decisions5634, missions567
        missions567.ensure_schema(self.desk)
        ident = 'a' * 32
        self.desk.db.execute('INSERT INTO missions_v567(id,owner,request_key,fingerprint,instruction,matter,kind,state,context,plan,exceptions,job_id,ref,created,updated) '
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ident, 'cabinet', 'k', 'f', 'Mission fictive', MID, 'question', 'error', '{}', '{}', '[]', None, '',
                                                                       self.desk.now(), self.desk.now()))
        self.desk.db.commit()
        out = decisions5634.cancel(self.desk, 'mission', ident)
        self.assertEqual(out['state'], 'cancelled')
        self.assertEqual(missions567.get(self.desk, ident)['state'], 'cancelled')
        with self.assertRaises(Stop):
            missions567.control(self.desk, {'id': ident, 'action': 'resume'})

    def test_u06_cards_offer_cancel_and_explain_cancel_versus_defer(self):
        from agent import aujourdhui5614
        self._failed_docreq()
        html = aujourdhui5614.decisions_html(self.desk, 'cabinet', '/p')
        self.assertIn('data-act="docreq-cancel"', html)
        self.assertIn('Annuler cette demande ? Les fichiers déjà créés seront conservés.', html)
        self.assertIn('Reporter y revient plus tard, sans changer aucune échéance', html)
        js = (ROOT / 'agent' / 'static' / 'v5614.js').read_text(encoding='utf-8')
        self.assertNotIn("{id, action: 'pause'}", js)


# --------------------------------------------------------------------------------------------- B13 : migrations
class UpgradeFixtures(unittest.TestCase):
    def test_t13_fixture_name_matches_its_starting_version(self):
        src = (ROOT / 'tests' / 'test_upgrade.py').read_text(encoding='utf-8')
        for m in re.finditer(r"def test_upgrade_from_(\d+)_is_supported_and_reversible\(self\):\n\s+later=self\.base/'releases'/'([\d.]+)'", src):
            digits, version = m.groups()
            self.assertEqual(version.replace('.', ''), digits, version)
        for v in ('5631', '5632', '5633'):
            self.assertIn('def test_upgrade_from_%s_is_supported_and_reversible' % v, src)
        self.assertNotIn("resolve().name,'5.6.", src)                                   # version de sortie centralisée (upgrade.VERSION)
        self.assertIn('Aucun point de retour arrière disponible', src)

    def test_t13_chain_and_rollback_receipt_checks(self):
        src = (ROOT / 'upgrade.py').read_text(encoding='utf-8')
        line = next(l for l in src.splitlines() if l.startswith('SUPPORTED_PREVIOUS = '))
        chain = ast.literal_eval(line.split('=', 1)[1].strip())
        import re as _re
        from agent import __version__
        for v in ('5.6.31', '5.6.32', '5.6.33'):                                         # versions toujours acceptées (5.6.35 : la chaîne s'allonge)
            self.assertIn(v, chain)
        self.assertNotIn(__version__, chain)
        self.assertIn("VERSION = '%s'" % __version__, src)
        self.assertEqual(src.count("'5.6.33'"), 1)                                      # seule occurrence : la chaîne des versions acceptées
        body = src[src.index('def rollback('):]
        self.assertLess(body.index('receipt_path(base)'), body.index('switch(base,old)'))   # reçu contrôlé avant toute modification
        self.assertTrue(_re.search(r"'5\.6\.33'[,}]", (ROOT / 'install-interface.py').read_text(encoding='utf-8')))


# --------------------------------------------------------------------------------------------- chapitre 5 : profil Juridique (L01 à L06)
class LegalProfile(unittest.TestCase):
    def test_l01_new_install_uses_legal_profile(self):
        from agent import profil5634
        r = profil5634.resolve({'llm': {'profile': 'juridique'}}, 'assistant', 'complex')
        self.assertEqual((r['temperature'], r['sampling']['top_p'], r['sampling']['top_k'], r['sampling']['repeat_penalty']), (0.3, 0.9, 40, 1.0))
        self.assertEqual(profil5634.resolve({}, 'legal_analysis', 'complex')['temperature'], 0.3)

    def test_l02_upgrade_keeps_an_explicit_zero(self):
        from agent import profil5634
        cfg = {'model_routing': {'complex_temperature': 0, 'fast_temperature': 0, 'control_temperature': 0}}
        self.assertEqual(profil5634.current(cfg)['profile'], 'herite')
        r = profil5634.resolve(cfg, 'assistant', 'complex')
        self.assertEqual((r['temperature'], r['sampling']), (0.0, {}))

    def test_l03_options_are_transmitted_to_ollama(self):
        from agent import model
        sent = []

        class FakeHTTP:
            def __init__(self, *a, **k):
                pass

            def json(self, method, path, data=None):
                if path == '/api/show':
                    return {'capabilities': ['completion'], 'model_info': {'qwen.context_length': 16384}}
                sent.append(data)
                return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"ok": true}'}, 'prompt_eval_count': 10, 'eval_count': 3}
        cfg = {'url': 'http://127.0.0.1:11434', 'model': 'm', 'provider_type': 'ollama', 'temperature': 0.3, 'context_mode': 'auto',
               'sampling': {'top_p': 0.9, 'top_k': 40, 'min_p': 0.0, 'repeat_penalty': 1.0, 'repeat_last_n': 64}}
        with patch('agent.model.HTTP', FakeHTTP):
            m = model.Model(cfg)
            m.complete([{'role': 'user', 'content': 'x'}], max_tokens=50)
            m.complete([{'role': 'user', 'content': 'x'}], temperature=0, max_tokens=50)
        self.assertEqual(sent[0]['options']['temperature'], 0.3)                        # argument absent : température du profil
        self.assertEqual(sent[0]['options']['top_k'], 40)
        self.assertEqual(sent[0]['options']['num_ctx'], 16384)                          # automatique, borné par la limite du modèle
        self.assertNotIn('think', sent[0])                                               # option non prise en charge : non envoyée
        self.assertEqual(sent[1]['options']['temperature'], 0)                          # zéro explicite conservé

    def test_l04_factual_extraction_uses_zero(self):
        from agent import profil5634
        r = profil5634.resolve({'llm': {'profile': 'juridique'}}, 'attachment_review', 'fast')
        self.assertEqual((r['temperature'], r['subprofile']), (0.0, 'extraction'))

    def test_l05_external_provider_gets_only_supported_parameters(self):
        from agent import profil5634
        sent, ignored = profil5634.provider_options('anthropic', 0.3, {'top_p': 0.9, 'top_k': 40})
        self.assertEqual((sent, sorted(ignored)), ({'temperature': 0.3}, ['top_k', 'top_p']))

    def test_l06_french_decimal_input_and_bounds(self):
        from agent import profil5634
        self.assertEqual(profil5634.number('0,3', 'temperature'), 0.3)
        for bad in ('0,5', '-1', 'abc', True):
            with self.assertRaises(Stop):
                profil5634.number(bad, 'temperature')
        with self.assertRaises(Stop):
            profil5634.number('40.5', 'top_k')
        self.assertEqual(profil5634.context_window({'context_mode': 'auto'}, 8192), 8192)


# --------------------------------------------------------------------------------------------- U01 à U07 : parcours
class Journeys(Base):
    def test_u01_old_links_redirect_to_hearings_with_the_matter(self):
        r = self.request('/audience', query='matter=' + MID)
        self.assertTrue(r['status'].startswith('302'))
        self.assertEqual(r['headers']['Location'], '/agent-courriel/audiences?onglet=fiche&matter=' + MID)
        r = self.request('/audiences-word', query='matter=' + MID)
        self.assertIn('onglet=preparer', r['headers']['Location'])
        self.assertTrue(self.request('/audiences-word', query='hearing=x')['status'].startswith('200'))   # liens de contrôle conservés
        page = self.request('/audiences', query='onglet=fiche&matter=' + MID)['body']
        self.assertIn('Dossier courant', page)
        self.assertNotIn('chemin exact', page)

    def test_u01_matter_search_handles_accents_reference_and_parties(self):
        from agent import web5634
        self.assertTrue(web5634.search_matters(self.desk, 'demo')['results'])
        self.assertTrue(web5634.search_matters(self.desk, MID)['results'])
        page = self.request('/audiences', query='q=DÉMO')['body']
        self.assertIn('DEMO', page)

    def test_u02_revision_uses_a_document_id_never_a_path(self):
        from agent import web5634
        _index(self.desk, [('/Dossiers/DEMO/Projet.docx', '2026-10-01', 'texte', ''), ('/Dossiers/DEMO/Note.pdf', '2026-10-01', 'texte', '')])
        docs = web5634.documents(self.desk, MID, kinds=('.docx',))
        self.assertEqual([d['name'] for d in docs], ['Projet.docx'])
        self.assertNotIn('path', docs[0])
        self.assertEqual(web5634.resolve_document(self.desk, MID, docs[0]['document_id']), '/Dossiers/DEMO/Projet.docx')
        with self.assertRaises(Stop):
            web5634.resolve_document(self.desk, MID, docs[0]['document_id'], 'autre-version')
        with self.assertRaises(Stop):
            web5634.resolve_document(self.desk, MID, 'inconnu')
        page = self.request('/reviser', query='matter=' + MID)['body']
        self.assertIn('Projet.docx', page)
        self.assertNotIn('chemin exact', page)

    def test_u01_uploaded_pleadings_are_accepted_only_when_traced_for_the_matter(self):
        from agent import web5634
        path = '/Dossiers/DEMO/60_Audiences/90_AxiorHub_Sources/Conclusions adverses - import avocat - 0123456789abcdef.pdf'
        with self.assertRaises(Stop):                                                    # aucun dépôt tracé : refusé
            web5634.resolve_document(self.desk, MID, 'televerse:' + path, None, ('.docx', '.pdf'))
        self.desk.audit('conclusions_televersees', {'path': path, 'matter': 'AUTRE', 'side': 'opponent'})
        with self.assertRaises(Stop):                                                    # dépôt d'un autre dossier : refusé
            web5634.resolve_document(self.desk, MID, 'televerse:' + path, None, ('.docx', '.pdf'))
        self.desk.audit('conclusions_televersees', {'path': path, 'matter': MID, 'side': 'opponent'})
        self.assertEqual(web5634.resolve_document(self.desk, MID, 'televerse:' + path, None, ('.docx', '.pdf')), path)
        with self.assertRaises(Stop):                                                    # format contrôlé
            web5634.resolve_document(self.desk, MID, 'televerse:' + path, None, ('.docx',))
        page = self.request('/audiences', query='onglet=preparer&matter=' + MID)['body']
        self.assertIn('data-m34-upload="ours"', page)
        self.assertIn('data-m34-upload-matter="%s"' % MID, page)

    def test_u04_alerts_actions_change_only_state_and_reopen_on_material_change(self):
        from agent import proactive
        proactive.ensure_schema(self.desk)
        due = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
        sid = proactive._signal(self.desk, MID, 'echeance', 'normal', 'Échéance proche', 'Conclusions à déposer', ['P1'], 'Examiner', due)
        self.desk.db.commit()
        out = proactive.act5634(self.desk, sid, 'remind', 'avocat@exemple.fr', 24)
        self.assertLess(out['snoozed_until'], due)                                       # le rappel ne dépasse pas la date utile
        self.assertTrue(out['warning'])
        proactive.act5634(self.desk, sid, 'done', 'avocat@exemple.fr')
        proactive.act5634(self.desk, sid, 'undo', 'avocat@exemple.fr')
        self.assertEqual(self.desk.db.execute('SELECT state FROM proactive_signals WHERE id=?', (sid,)).fetchone()[0], 'snoozed')
        proactive.act5634(self.desk, sid, 'done', 'avocat@exemple.fr')
        proactive._signal(self.desk, MID, 'echeance', 'critical', 'Échéance proche', 'Conclusions à déposer demain', ['P1'], 'Examiner', due)
        self.desk.db.commit()
        self.assertEqual(self.desk.db.execute('SELECT state FROM proactive_signals WHERE id=?', (sid,)).fetchone()[0], 'open')
        page = self.request('/surveillance', query='state=all')['body']   # « Toutes » : le dossier fictif n'est pas dans le portefeuille actif
        self.assertIn('Alertes du cabinet', page)
        self.assertIn('elles ne modifient pas vos échéances de procédure', page)
        self.assertIn('Marquer comme lu', page)

    def test_u05_every_function_has_a_migration_line_and_new_routes_answer(self):
        from agent import fonctions5634
        self.assertEqual(fonctions5634.check(), [])
        self.assertGreaterEqual(len(fonctions5634.MIGRATION), 64)   # 5.6.36 : 7 fonctions nouvelles, chacune avec sa ligne (check() ci-dessus)
        for path in ('/audiences', '/reviser', '/surveillance', '/recherche-juridique'):
            self.assertTrue(self.request(path)['status'].startswith('200'), path)

    def test_no_link_to_a_mail_section_of_open_webui(self):
        # 5.6.34 : la version classique d'Open WebUI n'a pas de section Courriel ; la messagerie reste dans AxiorHub
        src = (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8')
        self.assertNotIn("rstrip('/')+'/mail'", src)
        self.assertNotIn('Courrier Open WebUI', src)
        self.assertIn('Ouvrir la boîte de réception', src)

    def test_u07_summary_label_and_full_width(self):
        from agent import oral5632
        html = oral5632.panel_html('r1')
        self.assertIn('>Résumé</button>', html)
        self.assertNotIn('Résumé oral', html)
        css = (ROOT / 'agent' / 'static' / 'v5632.css').read_text(encoding='utf-8')
        rule = re.search(r'\.o5632-text\{[^}]*\}', css).group(0)
        self.assertNotIn('72ch', rule)
        for prop in ('width:100%', 'max-width:none', 'box-sizing:border-box'):
            self.assertIn(prop, rule)
        for lang, word in (('en', 'Summary'), ('de', 'Zusammenfassung'), ('es', 'Resumen')):
            self.assertEqual(json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))['Résumé'], word)
        js = (ROOT / 'agent' / 'static' / 'v5632.js').read_text(encoding='utf-8')
        self.assertNotIn('sans fait ajouté', js)                                          # le seul contrôle des nombres est décrit tel quel


# --------------------------------------------------------------------------------------------- traduction des scripts et des rapports
class ScriptTranslation(unittest.TestCase):
    def test_translator_is_loaded_first_and_dictionary_is_served(self):
        from agent import ui_helpers
        src = (ROOT / 'agent' / 'ui_helpers.py').read_text(encoding='utf-8')
        self.assertLess(src.index('/static/v5634t.js'), src.index('/static/v310.js'))
        js = (ROOT / 'agent' / 'static' / 'v5634t.js').read_text(encoding='utf-8')
        for part in ("'alert', 'confirm', 'prompt'", 'MutationObserver', "/static/ecrans-", "getAttribute('translate') === 'no'"):
            self.assertIn(part, js)
        self.assertTrue(ui_helpers)

    def test_buttons_and_counters_written_by_scripts_are_in_the_dictionary(self):
        from agent import ecrans5632
        for lang, start, pulse in (('en', '🎙 Start dictation', 'Commitment tracking off · 0 follow-up(s) in progress · 2 result(s) available'),
                                   ('de', '🎙 Diktat starten', 'Verfolgung der Zusagen deaktiviert · 0 Folgeschritt(e) in Bearbeitung · 2 Ergebnis(se) verfügbar'),
                                   ('es', '🎙 Empezar el dictado', 'Seguimiento de compromisos desactivado · 0 seguimiento(s) en curso · 2 resultado(s) disponible(s)')):
            table = ecrans5632.dictionary(lang)
            self.assertEqual(ecrans5632.phrase('🎙 Commencer la dictée', table), start)
            self.assertEqual(ecrans5632.phrase('Suivi des engagements désactivé · 0 suite(s) en cours · 2 résultat(s) disponible(s)', table), pulse)
            from agent.about560 import ATTRIBUTION
            self.assertNotIn('créé par', ecrans5632.phrase('Version 5.6.34 · ' + ATTRIBUTION, table))
            self.assertIsNotNone(ecrans5632.phrase('Réviser un document', table))

    def test_templates_keep_inserted_data_and_numbers(self):
        from agent import ecrans5632
        table = ecrans5632.dictionary('de')
        self.assertEqual(ecrans5632.phrase('Dépôt impossible : depot_impossible', table), 'Ablage nicht möglich: depot_impossible')
        self.assertEqual(ecrans5632.phrase('Opération n° 42 — Action terminée.', table), 'Vorgang Nr. 42 — Aktion abgeschlossen.')
        self.assertIsNone(ecrans5632.phrase('Société ALPHA contre BETA', table))           # une donnée n'est jamais traduite

    def test_textarea_example_is_translated_but_never_its_content(self):
        from agent import ecrans5632
        html = ecrans5632.translate_html('<textarea name="instruction" placeholder="Audience du …, 15 minutes, obtenir le rejet de la demande de …">'
                                         'Audience du …, 15 minutes, obtenir le rejet de la demande de …</textarea>', 'en')
        self.assertIn('placeholder="Hearing of …, 15 minutes, obtain dismissal of the claim for …"', html)
        self.assertIn('>Audience du …, 15 minutes, obtenir le rejet de la demande de …</textarea>', html)   # saisie de l'avocat intacte

    def test_every_translation_keeps_variables(self):
        for lang in ('en', 'de', 'es'):
            data = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))
            for fr, value in data.items():
                if fr.startswith('_'):
                    continue
                self.assertEqual(fr.count('%s'), value.count('%s'), (lang, fr))
                self.assertEqual(fr.count('#'), value.count('#'), (lang, fr))


class ReportsInFirmLanguage(unittest.TestCase):
    def test_plain_briefing_headings_follow_the_language(self):
        from agent import routines520
        data = {'date': '2026-10-12', 'agenda': [], 'courriels': [], 'actualite': {}}
        de = routines520._briefing_plain(data, {}, 'de')
        self.assertIn('# Morgenbriefing vom 12/10/2026', de)
        self.assertIn('## Heute', de)
        self.assertIn('- Nichts zu melden.', de)
        fr = routines520._briefing_plain(data, {}, 'fr')
        self.assertIn('# Briefing du 12/10/2026', fr)
        self.assertIn('## Aujourd’hui', fr)

    def test_prompts_require_the_firm_language_but_drafts_stay_french(self):
        from agent import routines520
        self.assertEqual(routines520.consigne('fr'), '')
        self.assertIn('allemand', routines520.consigne('de'))
        self.assertIn('en %s', routines520.SYSTEM)
        src = (ROOT / 'agent' / 'routines520.py').read_text(encoding='utf-8')
        self.assertIn("text = ask(desk, prompt, data, lang=lang)", src)
        self.assertNotIn('en français, titres compris', src)
        draft = src[src.index('def _draft('):src.index('def _draft(') + 1500]
        self.assertNotIn('lang=', draft)                                                 # réponses aux correspondants : en français

    def test_report_language_follows_the_lawyer_when_the_firm_has_none(self):
        import tempfile
        from agent import i18n5631, routines520

        class D:
            c = {'state_dir': tempfile.mkdtemp()}
        self.assertEqual(routines520.langue(D), 'fr')
        i18n5631.save_preference(D.c, 'avocat@exemple.fr', 'de')
        self.assertEqual(routines520.langue(D), 'de')
        i18n5631.save_preference(D.c, 'assistant@exemple.fr', 'es')
        self.assertEqual(routines520.langue(D), 'fr')                                 # langues différentes : celle du cabinet
        i18n5631.save_preference(D.c, 'avocat@exemple.fr', 'en', cabinet=True)
        self.assertEqual(routines520.langue(D), 'en')

    def test_spoken_summary_in_another_language_adds_nothing(self):
        from agent import oral5632
        md = '# Morning briefing of 12/10/2026\n\n## Today\n- 09:30 Hearing DEMO-A\n\n## To handle\n- Client Alpha: [exhibit 4](https://x.example) requested\n'
        text = oral5632.compose_plain(md, 'en')
        self.assertTrue(text.startswith('Good morning.'))
        self.assertIn('Today. 09:30 Hearing DEMO-A.', text)
        self.assertNotIn('https://', text)
        self.assertEqual(oral5632.check(md, text, '2026-10-12', 'en'), [])
        self.assertTrue(oral5632.check(md, 'Bonjour. Rien.', '2026-10-12', 'en'))


# --------------------------------------------------------------------------------------------- agenda local
class LocalAgenda(Base):
    def test_local_agenda_without_caldav_events_reminders_backup(self):
        from agent import agenda5634
        start = (datetime.now(agenda5634._tz(self.desk)) + timedelta(minutes=30)).replace(second=0, microsecond=0)
        out = agenda5634.create(self.desk, {'title': 'Rendez-vous client fictif', 'kind': 'rendez_vous', 'start': start.strftime('%Y-%m-%dT%H:%M'),
                                            'duration_minutes': 45, 'matter': MID, 'reminders': [60]})
        self.assertEqual(out['event']['sync_state'], 'local')
        cached = self.desk.db.execute("SELECT title,matter FROM calendar_cache WHERE calendar_url='local:agenda'").fetchall()
        self.assertEqual([(r[0], r[1]) for r in cached], [('Rendez-vous client fictif', MID)])   # visible par la fiche d'audience et le briefing
        first = agenda5634.tick(self.desk)
        second = agenda5634.tick(self.desk)
        self.assertEqual((first['reminders'], second['reminders']), (1, 0))             # un rappel, une seule fois
        self.assertTrue(Path(first['backup']).is_file())
        agenda5634.cancel(self.desk, out['id'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM calendar_cache WHERE calendar_url='local:agenda'").fetchone()[0], 0)

    def test_ics_import_expands_simple_series_and_ignores_duplicates(self):
        from agent import agenda5634
        day = (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y%m%d')
        ics = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:serie-1@exemple.test\r\nDTSTART;TZID=Europe/Paris:%sT090000\r\n'
               'DTEND;TZID=Europe/Paris:%sT100000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Permanence\\, cabinet\r\nEND:VEVENT\r\n'
               'BEGIN:VEVENT\r\nUID:jour-1@exemple.test\r\nDTSTART;VALUE=DATE:%s\r\nSUMMARY:Échéance de dépôt\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n') % (day, day, day)
        out = agenda5634.import_ics(self.desk, ics, 'nextcloud')
        self.assertEqual(out['added'], 4)
        again = agenda5634.import_ics(self.desk, ics, 'nextcloud')
        self.assertEqual((again['added'], again['skipped']), (0, 4))
        titles = sorted({e['title'] for e in agenda5634.listing(self.desk, end=datetime.now(timezone.utc) + timedelta(days=60))})
        self.assertEqual(titles, ['Permanence, cabinet', 'Échéance de dépôt'])
        exported = agenda5634.export_ics(self.desk)
        self.assertIn('SUMMARY:Permanence\\, cabinet', exported)
        self.assertIn('DTSTART;VALUE=DATE:' + day, exported)

    def test_event_creation_falls_back_to_local_without_planning_calendar(self):
        from agent import workplan
        start = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%dT10:00')
        out = workplan.create_event(self.desk, {'title': 'Audience fictive', 'start': start, 'duration_minutes': 60, 'matter': MID})
        self.assertTrue(out['local'])
        page = self.request('/planning')['body']
        self.assertIn('Agenda local', page)
        self.assertNotIn('configure-nextcloud-workflow.py', page)


# --------------------------------------------------------------------------------------------- état du système selon le mode
class SystemStateByMode(Base):
    def test_windows_never_calls_systemctl_and_reads_real_processes(self):
        from agent import reliability393, portable
        runtime = Path(self.desk.c['state_dir']) / 'runtime.json'
        runtime.write_text(json.dumps({'pid': 4242, 'port': 1, 'processes': {'interface': 4243, 'worker': 4244, 'veille': 4245}}), encoding='utf-8')
        calls = []
        with patch.object(portable, 'WINDOWS', True), patch.object(portable, 'pid_alive', lambda pid: int(pid) != 4245), \
                patch('subprocess.run', side_effect=lambda *a, **k: calls.append(a)):
            out = reliability393.service_health(self.desk, mode=None, runtime=str(runtime))
        self.assertEqual(calls, [])
        self.assertEqual(out['mode'], 'windows')
        veille = next(r for r in out['services'] if r['unit'] == 'veille')
        self.assertFalse(veille['verified'])
        self.assertTrue(veille['cause'] and veille['action'] and veille['feature'])
        interface = next(r for r in out['services'] if r['unit'] == 'interface')
        self.assertFalse(interface['verified'])                                          # processus lancé mais l'interface ne répond pas

    def test_windows_permissions_are_read_from_the_real_acl(self):
        from agent import diagnostic5634
        p = Path(self.desk.c['state_dir']) / 'ui-auth.json'
        p.write_text('{}', encoding='utf-8')
        self.assertEqual(diagnostic5634.file_permission(p, True, 'windows', lambda _: 'O:S-1-5-21-1-2-3-1000D:PAI(A;;FA;;;SY)(A;;FA;;;S-1-5-21-1-2-3-1000)',
                                                          me='S-1-5-21-1-2-3-1000')['status'], 'verified')
        bad = diagnostic5634.file_permission(p, True, 'windows', lambda _: 'D:(A;;FR;;;WD)(A;;FA;;;SY)')
        self.assertEqual((bad['status'], bad['broad']), ('error', ['Tout le monde']))
        self.assertEqual(diagnostic5634.file_permission(p, True, 'windows', lambda _: None)['status'], 'unknown')   # jamais un vert par défaut

    def test_auth_file_is_the_one_used_at_startup(self):
        from agent import diagnostic5634
        cfg = {'_path': str(Path(self.desk.c['state_dir']) / 'donnees' / 'config.json')}
        with patch.dict('os.environ', {'AXIORHUB_AUTH_FILE': ''}):
            path, origin = diagnostic5634.auth_file(cfg)
        self.assertEqual(path.name, 'ui-auth.json')
        self.assertEqual(path.parent.name, 'donnees')
        self.assertNotIn('/etc/', str(path).replace('\\', '/'))

    def test_optional_connectors_are_labelled(self):
        from agent import diagnostic5634
        states = {c['connector']: c['state'] for c in diagnostic5634.connectors(self.desk)}
        self.assertEqual(states['telephonie'], 'Non configuré')
        self.assertIn(states['messagerie'], ('À tester', 'Non configuré'))


# --------------------------------------------------------------------------------------------- B17 : guide Kokoro
class KokoroGuide(unittest.TestCase):
    def test_guide_per_system_and_language(self):
        from agent import guide5631, kokoro5634
        self.assertIn(('voix', ()), guide5631.STEPS)
        for lang in ('fr', 'en', 'de', 'es'):
            html = kokoro5634.guide_html(lang, 'windows')
            self.assertIn('start-cpu.ps1', html)
            self.assertIn('ff_siwis', html)
            self.assertIn('eSpeak NG', html)                                              # allemand : non proposé par Kokoro, dit clairement
        self.assertLess(kokoro5634.guide_html('fr', 'serveur').index('Serveur Ubuntu'), kokoro5634.guide_html('fr', 'serveur').index('Poste Windows'))


# --------------------------------------------------------------------------------------------- chapitre 9.6 : contrat /integration/v1
class IntegrationContract(Base):
    SECRET = 'secret-de-recette-fictif-0123456789abcdef'

    def setUp(self):
        super().setUp()
        secret = Path(self.desk.c['state_dir']) / 'integration.secret'
        secret.write_text(self.SECRET, encoding='utf-8')
        secret.chmod(0o600)   # le moteur refuse un secret lisible par d'autres comptes (contrôle effectif sous Linux)
        self.desk.c['integration5634'] = {'enabled': True, 'instance_id': 'cabinet-test', 'secret_file': str(secret)}

    def header(self, scope=(MID,), **over):
        from agent.integration5634 import sign
        ctx = {'instance': 'cabinet-test', 'user': 'alice', 'scope': list(scope), 'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
               'nonce': hashlib.sha256(str(datetime.now().timestamp()).encode() + str(over).encode()).hexdigest()[:32]}
        ctx.update(over)
        return {'_ctx': ctx}   # 5.6.35 (F10) : signé au moment de l'appel, pour l'opération réellement appelée

    def call(self, method, path, headers, body=None):
        from agent.integration5634 import handle, sign
        if '_ctx' in headers:
            ctx = dict(headers['_ctx'])
            ctx.setdefault('op', method + ' ' + path)
            headers = {'HTTP_X_AXIORHUB_CONTEXT': sign(self.SECRET, ctx)}
        return handle(self.desk, method, path, headers, json.dumps(body).encode() if body is not None else b'')

    def test_health_signature_replay_and_expiry(self):
        status, out = self.call('GET', '/integration/v1/health', {})
        self.assertEqual(status, '200 OK')
        self.assertRegex(out['contract'], r'^1\.\d+$')   # contrat 1.x (compatible ascendant)
        h = self.header()
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32, h)[0], '404 Not Found')
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32, h)[1]['code'], 'requete_rejouee')
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32, self.header(exp='2020-01-01T00:00:00+00:00'))[1]['code'], 'contexte_expire')
        from agent.integration5634 import sign
        good = sign(self.SECRET, dict(self.header()['_ctx'], op='GET /integration/v1/jobs/' + 'f' * 32))
        forged = {'HTTP_X_AXIORHUB_CONTEXT': good[:-4] + '0000'}
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32, forged)[1]['code'], 'signature_invalide')
        err = self.call('GET', '/integration/v1/jobs/' + 'f' * 32, self.header(instance='autre'))[1]
        for k in ('code', 'service', 'stage', 'retryable', 'correlation_id', 'message'):
            self.assertIn(k, err)

    def test_scope_idempotency_and_approval_of_a_precise_version(self):
        status, out = self.call('POST', '/integration/v1/jobs', self.header(scope=()), {'operation': 'fiche_dossier', 'matter': MID, 'idempotency_key': 'cle-essai-01'})
        self.assertEqual((status, out['code']), ('403 Forbidden', 'dossier_hors_perimetre'))
        body = {'operation': 'fiche_dossier', 'matter': MID, 'idempotency_key': 'cle-essai-01'}
        s1, a = self.call('POST', '/integration/v1/jobs', self.header(), body)
        s2, b = self.call('POST', '/integration/v1/jobs', self.header(), body)
        self.assertEqual((s1, s2, a['job_id']), ('202 Accepted', '202 Accepted', b['job_id']))
        self.assertEqual(b['state'], 'existing')
        st = self.call('GET', '/integration/v1/jobs/' + a['job_id'], self.header())[1]
        self.assertEqual(st['state'], 'pending')
        self.assertEqual(self.call('GET', '/integration/v1/results/' + a['job_id'], self.header())[1]['code'], 'resultat_indisponible')
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + a['job_id'], self.header(user='bob'))[1]['code'], 'travail_absent')


# --------------------------------------------------------------------------------------------- chaîne des versions et paquets
class Release(unittest.TestCase):
    def test_version_and_cache_busting(self):
        from agent import __version__
        self.assertTrue(__version__.startswith('5.6.') and int(__version__.split('.')[2]) >= 34)
        sb = json.loads((ROOT / 'SBOM.cdx.json').read_text(encoding='utf-8'))
        self.assertEqual(sb['metadata']['component']['version'], __version__)
        listing = json.loads((ROOT / 'RELEASE-FILES.json').read_text(encoding='utf-8'))
        for name in ('agent/schema5634.py', 'agent/synthese5632.py', 'agent/agenda5634.py', 'agent/diagnostic5634.py', 'agent/integration5634.py',
                     'agent/static/v5634.js', 'agent/static/v5634.css', 'tests/test_v5634.py', 'nextcloud-app/axiorhub_pilote/appinfo/info.xml'):
            self.assertIn(name, listing)

    def test_no_external_font_or_inline_style_in_new_assets(self):
        for name in ('v5634.js', 'v5634.css', 'v5627.js', 'v5632.css'):
            text = (ROOT / 'agent' / 'static' / name).read_text(encoding='utf-8')
            self.assertNotIn('frontend-cdn', text)                                       # O02 : aucune police externe dans le logiciel
            self.assertNotIn('@font-face', text)
            self.assertNotIn('.style.', text if name.endswith('.js') else '')

    def test_nextcloud_app_declares_targets_and_routes_without_csrf_bypass(self):
        app = ROOT / 'nextcloud-app' / 'axiorhub_pilote'
        info = (app / 'appinfo' / 'info.xml').read_text(encoding='utf-8')
        self.assertIn('<nextcloud min-version="32" max-version="35"/>', info)
        self.assertIn('<id>axiorhub_pilote</id>', info)
        for php in (app / 'lib' / 'Controller').glob('*.php'):
            text = php.read_text(encoding='utf-8')
            self.assertNotIn('PublicPage', text)
            if php.name != 'PageController.php':
                self.assertNotIn('NoCSRFRequired', text, php.name)
        routes = (app / 'appinfo' / 'routes.php').read_text(encoding='utf-8')
        self.assertFalse(re.search(r"'verb' => 'GET'\][^\n]*(cancel|approvals|config'|jobs')", routes))
        self.assertTrue((app / 'js').is_dir() and any((app / 'js').iterdir()))         # actifs compilés inclus dans le paquet


if __name__ == '__main__':
    unittest.main()
