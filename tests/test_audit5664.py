"""Contre-tests indépendants 5.6.64. Données fictives, transports simulés.
Repris sans modification du dossier technique de l'audit de la 5.6.64 (19 octobre 2026) ; ils réussissent sur la 5.6.65.
Les assertions décrivent le comportement attendu après correction.
"""
import copy
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import threading
import types
import unittest
from unittest.mock import patch

import test_v5652 as fx
import test_v5657 as f57
import test_v5650 as f50
import test_v220 as fdoc
from agent import confidentiel5664 as conf, crm5657, crm5636, membres5650 as mb
from agent import contexte5651, modele5651, memoire5651, document_projects as dp
from agent import savoir5659, chatgpt5662 as cg, arret5643, watch430
from agent.common import Stop, load_matters
from agent.index import DocumentIndex

OBSERVATIONS = {}
ACTOR = 'avocat@example.test'
MID = 'DOS-001'


class Confidentialite(fx.Base):
    def test_R01_fragments_heritent_confidentialite_du_document(self):
        matter = load_matters(self.desk.c)[0]
        text = 'Correspondance confidentielle entre avocats.\n' + ('Développement du dossier.\n' * 250) + '\nOFFRESECRETE772 : proposition transactionnelle de 20 000 euros.'
        index = DocumentIndex(self.desk.c['state_dir'], self.desk.c.get('rag'), self.desk.c.get('ollama'))
        index.put_source(matter, '/Dossiers/DEMO/Lettre Me Exemple.txt', text, 'v-conf-1')
        base = {'path': 'modele_interne:MODELE.docx', 'sha256': 'b' * 64}
        packet = dp._source_packet(self.desk, matter, 'OFFRESECRETE772', base, 'Trame publique', [], 'document')
        kept, removed = conf.filter_packet(packet)
        leaked = [s for s in kept if 'OFFRESECRETE772' in s.get('excerpt', '')]
        OBSERVATIONS['R01'] = {'document_complet_reconnu': conf.is_confidential('Lettre Me Exemple.txt', text),
                              'fragments_retires': removed, 'fragments_sensibles_conserves': len(leaked)}
        index.db.close()
        self.assertFalse(leaked, 'Un fragment ne peut perdre le statut du document dont il provient.')

    def test_R02_base_confidentielle_non_choisie_automatiquement(self):
        template = (Path(dp.__file__).parents[1] / 'templates/MODELE_CONCLUSIONS.docx').read_bytes()
        dav = fdoc.DocumentDAV(template)
        dav.source['path'] = '/Dossiers/DEMO/Courrier confidentiel.docx'
        captured = {}

        class Model(fdoc.DocumentModel):
            def ask(self, stage, payload):
                captured.update(copy.deepcopy(payload))
                return super().ask(stage, payload)
        error = ''
        try:
            dp.prepare(self.desk, {'matter': MID, 'document_type': 'document', 'instruction': 'Préparer une note à remettre au client.'}, dav=dav, model=Model())
        except Stop as ex:
            error = str(ex)
        sources = captured.get('sources') or []
        forbidden = [s for s in sources if conf.is_confidential(s.get('path'), s.get('excerpt'))]
        OBSERVATIONS['R02'] = {'source_path_explicitement_demande': False, 'base_transmise': [s['path'] for s in sources if s['kind'] == 'base_document'], 'erreur': error}
        self.assertFalse(forbidden, 'Une base retenue automatiquement doit subir le même filtre que les autres sources.')

    def test_R03_source_confidentielle_mixte_non_transmise(self):
        info = {'text': 'OFFRESECRETE772 : proposition de 20 000 euros', 'author': '', 'author_label': 'Document', 'origin': 'document',
                'proof': '', 'review': 'enregistre', 'relevance': 'pertinent', 'date_value': '2026-09-01', 'date_precision': 'jour',
                'sources': [{'path': '/D/Lettre confidentielle.eml'}, {'path': '/D/Justificatif.pdf'}], 'kind': 'fait'}
        with patch.object(modele5651, 'ensure_schema'), patch.object(memoire5651, 'ensure_schema'), \
                patch.object(modele5651, 'refresh', return_value={'state': 'ok'}), patch.object(modele5651, 'parties', return_value=[]), \
                patch.object(modele5651, 'reference_procedure', return_value={}), patch.object(modele5651, 'assertions', return_value=[info]), \
                patch.object(memoire5651, 'decisions', return_value=[]), patch.object(memoire5651, 'gaps', return_value=[]):
            payload = contexte5651.for_drafting(self.desk, MID, 'Rédiger pour le client')
        OBSERVATIONS['R03'] = {'sources_transmises': payload.get('faits_dates', [{}])[0].get('sources', []),
                              'offre_transmise': 'OFFRESECRETE772' in json.dumps(payload)}
        self.assertNotIn('Lettre confidentielle.eml', json.dumps(payload), 'Une information corroborée doit citer exclusivement les preuves autorisées, après contrôle de leur portée.')

    def test_R04_reference_legale_publique_non_exclue(self):
        source = {'id': 'legal-official-772', 'kind': 'official_legal_source', 'path': 'https://legifrance.example.test/article',
                  'excerpt': "Aux termes de l'article 66-5 de la loi, les correspondances sont couvertes par le secret.", 'officially_verified': True}
        kept, removed = conf.filter_packet([source])
        OBSERVATIONS['R04'] = {'source_publique_retirée': removed, 'sources_restantes': len(kept)}
        self.assertEqual(kept, [source], 'Le texte public d’un article ne devient pas une correspondance confidentielle parce qu’il cite cet article.')

    def test_C01_confidentialite_nom_et_usage_interne_detectes(self):
        self.assertTrue(conf.is_confidential('Courrier confidentiel.eml'))
        self.assertFalse(conf.is_confidential('Contrat.pdf', 'Les parties prévoient une clause de confidentialité.'))

    def test_R11_memoire_validee_ne_contourne_pas_filtre_redaction(self):
        from agent import legal_memory, docrequest520
        legal_memory.ensure_schema(self.desk)
        stamp = datetime.now(timezone.utc).isoformat()
        values = ('mem-conf-772', MID, 'fact', 'Offre confidentielle', 'OFFRESECRETE772 : 20 000 euros proposés par le confrère.',
                  'Confrère adverse', '2026-09-01', 1.0, 'validated', json.dumps(['/Dossiers/DEMO/Lettre confidentielle.eml']),
                  '{}', 'document', 1, '', stamp, stamp, stamp, 'Validation interne des faits')
        self.desk.db.execute('INSERT INTO legal_memory_records VALUES(' + ','.join('?' for _ in values) + ')', values)
        self.desk.db.commit()
        dav = types.SimpleNamespace(inventory=lambda path: [])
        matter = load_matters(self.desk.c)[0]
        ctx, notes = docrequest520.context(self.desk, matter, 'Préparer une note pour le client', dav=dav)
        filtered = docrequest520._without_confidential(ctx, 'note')
        OBSERVATIONS['R11'] = {'fait_legacy_lu': 'OFFRESECRETE772' in json.dumps(ctx),
                              'fait_present_apres_filtre': 'OFFRESECRETE772' in json.dumps(filtered),
                              'sources_du_fait_transmises': filtered.get('faits', [])}
        self.assertNotIn('OFFRESECRETE772', json.dumps(filtered), 'La validation interne d’un fait ne lève pas les restrictions de sa source.')


class Contacts(fx.Base):
    def setUp(self):
        super().setUp()
        crm5657.save_settings(self.desk, ACTOR, {'actif': True, 'dossiers': 'INBOX', 'periode': '90'})

    def test_R05_erreur_transitoire_ne_saute_pas_contact(self):
        own = self.desk.c['mail']['from_address']

        class Box(f57.FakeBox):
            fail = True
            calls = []
            def fetch(self, folder, uid, headers_only=False):
                self.calls.append(int(uid))
                if uid == '1' and self.fail:
                    self.fail = False
                    raise Stop('delai_imap_depasse')
                return super().fetch(folder, uid, headers_only)
        box = Box({1: f57.msg('Premier <premier@example.test>', own), 2: f57.msg('Second <second@example.test>', own)})
        first = crm5657.collect(self.desk, box)
        second = crm5657.collect(self.desk, box)
        emails = {c['email'] for c in crm5636.search(self.desk)}
        OBSERVATIONS['R05'] = {'erreurs_premier_passage': first['errors'], 'messages_relus': second['read'], 'appels_uid': box.calls,
                              'contact_premier_present': 'premier@example.test' in emails,
                              'curseur': dict(self.desk.db.execute('SELECT * FROM crm57_cursors').fetchone())}
        self.assertIn('premier@example.test', emails, 'Un échec réseau temporaire ne doit pas acquitter définitivement cet UID.')

    def test_R06_adresse_d_abord_exclue_reexaminee_plus_tard(self):
        own = self.desk.c['mail']['from_address']
        box = f57.FakeBox({1: f57.msg('Camille <camille@example.test>', own, List_Id='<liste.example.test>')})
        crm5657.collect(self.desk, box)
        box.messages[2] = f57.msg('Camille <camille@example.test>', own)
        result = crm5657.collect(self.desk, box)
        emails = {c['email'] for c in crm5636.search(self.desk)}
        row = dict(self.desk.db.execute('SELECT * FROM crm57_seen WHERE email=?', ('camille@example.test',)).fetchone())
        OBSERVATIONS['R06'] = {'crees_second_passage': result['created'], 'etat_adresse': row}
        self.assertIn('camille@example.test', emails, 'Une exclusion liée à un message de liste ne doit pas bannir tous les messages personnels futurs de cette adresse.')

    def test_C02_import_normal_repris_sans_doublon(self):
        own = self.desk.c['mail']['from_address']
        box = f57.FakeBox({1: f57.msg('Camille <camille@example.test>', own)})
        self.assertEqual(crm5657.collect(self.desk, box)['created'], 1)
        self.assertEqual(crm5657.collect(self.desk, box)['created'], 0)


class SavoirFaire(fx.Base):
    def test_R07_anonymisation_couvre_tous_les_champs(self):
        modele5651.add_party(self.desk, MID, ACTOR, 'Société Gamma Fictive', client=True)
        data = {'kind': 'clause', 'title': 'Clause de Société Gamma Fictive', 'body': 'La Société Gamma Fictive doit régler le prix.',
                'objective': 'Obtenir le paiement de Société Gamma Fictive à secret@gamma.example.test',
                'limits': 'Accord limité à Société Gamma Fictive', 'keywords': 'Société Gamma Fictive'}
        out = savoir5659.propose(self.desk, ACTOR, data, matter=MID)
        row = savoir5659.listing(self.desk)[0]['latest']
        savoir5659.decide(self.desk, ACTOR, out['resource'], 1, 'approuver')
        sent = savoir5659.instructions_for(self.desk, [{'id': out['resource'], 'version': 1, 'reason': 'Choisie pour un autre dossier'}])
        OBSERVATIONS['R07'] = {'etiquette_anonymise': bool(row['anonymized']), 'corps_anonymise': 'Gamma' not in row['body'],
                              'objectif': row['objective'], 'nom_dans_competence_transmise': 'Gamma' in sent}
        self.assertNotIn('Gamma', sent, 'Les métadonnées réutilisées dans d’autres dossiers doivent être anonymisées comme le corps.')

    def test_R07b_nouvelle_version_reanonymisee_avant_etiquetage(self):
        modele5651.add_party(self.desk, MID, ACTOR, 'Société Gamma Fictive', client=True)
        created = savoir5659.propose(self.desk, ACTOR, {'kind': 'clause', 'title': 'Clause de paiement',
            'body': 'La Société Gamma Fictive paie le prix.'}, matter=MID)
        changed = savoir5659.propose(self.desk, ACTOR, {'resource': created['resource'], 'kind': 'clause', 'title': 'Clause corrigée',
            'body': 'La Société Gamma Fictive reçoit copie à secret@gamma.example.test.'})
        latest = savoir5659.listing(self.desk)[0]['latest']
        OBSERVATIONS['R07b'] = {'version': changed['version'], 'origine_dossier': latest['origin_matter'],
                               'etiquette_anonymise': bool(latest['anonymized']), 'corps': latest['body']}
        self.assertNotIn('Gamma', latest['body'], 'L’origine d’une révision doit être résolue avant de réappliquer l’anonymisation, y compris sans champ matter dans le formulaire.')


class FileTravail(fx.Base):
    def test_R08_limite_six_pas_depassee_par_lot_de_trois(self):
        class DAV:
            def inventory_step(self, path, state):
                return {'files': {path + '/Nouveau.pdf': {'etag': 'e1', 'modified': datetime.now(timezone.utc).isoformat()}}}, True
        for i in range(5):
            self.desk.enqueue('index', {'matter': 'M%d' % i}, priority=50)
        matter = {'id': MID, 'path': '/Dossiers/DEMO'}
        with patch('agent.watch430.indexable_matters', return_value=[matter]), patch('agent.watch430.ignored', return_value=[]), \
                patch('agent.watch430.recent_change', return_value=True), patch('agent.automation568.observe', return_value=set()), \
                patch('agent.notices440.observe_inventory'), patch('agent.echeances450.observe_inventory'), \
                patch('agent.proactive568.document_events'), patch('agent.economie569.quota_ok', return_value=True):
            result = watch430.documents_check(self.desk, dav=DAV())
        n = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND kind IN ('index','monitor_matter','extract_facts460')").fetchone()[0]
        OBSERVATIONS['R08'] = {'avant': 5, 'apres': n, 'differe': result['deferred']}
        self.assertLessEqual(n, 6, 'Le plafond doit réserver la capacité nécessaire au lot entier, et pas seulement tester le total avant ajout.')

    def test_R09_verrou_dossier_n_interdit_pas_autre_dossier(self):
        from agent.queue567 import producer_lock
        first = self.desk.enqueue('prepare_reply', {'matter': MID, 'key': 'a'}, priority=50)
        self.desk.db.execute('UPDATE jobs SET lock_waits=3 WHERE id=?', (first,))
        self.desk.db.commit()
        other = self.desk.enqueue('prepare_reply', {'matter': 'DOS-AUTRE', 'key': 'b'}, priority=0)
        with producer_lock(self.desk.c, 'prepare_reply', {'matter': MID}), patch.object(self.desk, 'perform', return_value={'ok': True}):
            self.desk.work_once()
            self.desk.work_once()
            states = {r['id']: r['status'] for r in self.desk.db.execute('SELECT id,status FROM jobs')}
        OBSERVATIONS['R09'] = {'dossier_occupe': states[first], 'autre_dossier': states[other]}
        self.assertEqual(states[other], 'done', 'Une attente de verrou propre à un dossier ne doit pas réserver le verrou global exclusif.')

    def test_R10_flux_chatgpt_reellement_ferme_a_annulation(self):
        response_ready = threading.Event()
        peer_closed = threading.Event()
        accepted = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                accepted.append(self.connection)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.flush()
                response_ready.set()
                self.connection.settimeout(10)
                try:
                    if self.connection.recv(1) == b'':
                        peer_closed.set()
                except (OSError, TimeoutError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        server_thread.start()
        host = '127.0.0.1:%d' % server.server_address[1]
        job = {'job_id': 777, 'state_dir': self.desk.c['state_dir']}

        def reason(**kw):
            return 'operation_annulee' if response_ready.is_set() else ''
        closed = None
        try:
            with patch.object(cg, 'access_token', return_value='JETON-FICTIF'), patch.object(cg, 'API', 'http://' + host), \
                    patch.object(cg, 'ORIGINS', {('http', host)}), \
                    patch.object(arret5643, 'before_model_call'), patch.object(arret5643, 'current_job', return_value=job), \
                    patch.object(arret5643, 'reason_to_stop', side_effect=reason):
                with self.assertRaisesRegex(Stop, 'operation_annulee'):
                    arret5643.interruptible(lambda: cg.respond(self.desk.c['state_dir'], 'modele-fictif', [{'role': 'user', 'content': 'Texte fictif'}]), poll=0.01)
                closed = peer_closed.wait(0.2)
        finally:
            for conn in accepted:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
            server.shutdown()
            server.server_close()
            server_thread.join(1)
        OBSERVATIONS['R10'] = {'flux_ouvert_avant_annulation': response_ready.is_set(), 'ferme_au_retour_annulation': closed}
        self.assertTrue(closed, 'Le transport ChatGPT doit être inscrit dans le mécanisme d’arrêt ; abandonner le fil appelant ne ferme pas le flux.')


class SavoirReserve(unittest.TestCase):
    def test_R12_brouillon_savoir_dossier_reserve_pas_partage_avant_approbation(self):
        from agent.desk import Desk
        f = f50.ServerAccounts('run')
        f.setUp()
        self.addCleanup(f.doCleanups)
        admin = f.admin_cookie()
        lawyer = f.add(admin, 'autre@example.test', 'avocat')
        desk = Desk(f.f.c)
        self.addCleanup(desk.db.close)
        mb.clear()
        self.addCleanup(mb.clear)
        mb.set_reserve(str(f.state), MID, ['avocate@example.test'])
        savoir5659.propose(desk, ACTOR, {'kind': 'clause', 'title': 'Projet de compétence',
                          'body': 'STRATEGIEPRIVEE772 : argument en cours de préparation, pas encore approuvé pour partage.'}, matter=MID)
        direct = f.request('/dossier', query='id=' + MID, cookie=lawyer)
        page = f.request('/savoir-faire', cookie=lawyer)
        OBSERVATIONS['R12'] = {'dossier': direct['status'], 'page_savoir': page['status'],
                              'brouillon_prive_visible': 'STRATEGIEPRIVEE772' in page['body']}
        self.assertEqual(direct['status'], '403 Forbidden')
        self.assertNotIn('STRATEGIEPRIVEE772', page['body'], 'Une version issue d’un dossier réservé doit rester dans ce périmètre tant que le partage de son contenu anonymisé n’est pas approuvé.')


if __name__ == '__main__':
    unittest.main(verbosity=2)
