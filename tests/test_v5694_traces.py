"""5.6.94 (lot traces) : étapes des travaux au format OpenTelemetry, sans dépendance nouvelle (T01 à T07).

Faux services uniquement : faux IMAP (connexion remplacée), faux producteur de travail, faux collecteur (fonction d'envoi remplacée), faux
résolveur de noms. Données fictives (DURAND, MARTIN, DOS-001, example.test). Aucun appel réseau, aucun modèle réel.

Ce que ces essais ne prouvent pas : la réception par un vrai collecteur OpenTelemetry, Jaeger ou Grafana Tempo (format vérifié ici contre
la structure ``ExportTraceServiceRequest``, pas contre un collecteur réel), le comportement d'un vrai serveur IMAP qui coupe la connexion,
ni le rendu de la page dans un vrai navigateur."""
from datetime import datetime, timedelta, timezone
import imaplib
import inspect
import json
import re
import time
import unittest
from unittest.mock import patch

from agent import charge5693 as charge, charge_web5693 as cw, trace480, traces5694 as tr, traces_web5694 as tw
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures
import test_v5689_horaires as ho

HEX32, HEX16 = re.compile(r'[0-9a-f]{32}'), re.compile(r'[0-9a-f]{16}')
SECRETS = ('DURAND', 'MARTIN', 'Conclusions', 'DOS-001', '@example.test', '/Dossiers')


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        tr.oublier_cache()
        self.addCleanup(tr.oublier_cache)
        charge.relever()
        p = patch('agent.relevance370.review_result', return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def cfg(self, **autres):
        return {'state_dir': self.f.c['state_dir'], 'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'llama3:8b', **autres}

    def travail(self, producteur, kind='index', args=None):
        """Un travail réel de la file (``work_once``) dont le producteur est remplacé par ``producteur(desk, kind, args)``."""
        jid = self.desk.enqueue(kind, {'matter': 'DOS-001'} if args is None else args, origin='user')
        with patch.object(Desk, 'perform', lambda desk, k, a: producteur(desk, k, a)):
            self.desk.work_once()
        return jid

    def traces(self):
        return tr.spans(self.desk)

    def racine(self, jid):
        """Dernière trace du travail (un numéro de travail supprimé peut être réattribué par SQLite)."""
        return [s for s in self.traces() if s['genre'] == 'travail' and s['attributs'].get('axiorhub.travail.id') == jid][-1]

    def enfants(self, racine):
        return [s for s in self.traces() if s['parent_id'] == racine['span_id']]

    def brut(self):
        """Tout ce que la base et l'export contiennent, en texte."""
        tr.spans(self.desk)
        tr.schema(self.desk)
        lignes = json.dumps([list(r) for r in self.desk.db.execute('SELECT * FROM traces5694_spans').fetchall()], ensure_ascii=False)
        return lignes + tr.export(self.desk, 24)[1]


# ---------------------------------------------------------------- T01 étapes tracées
class T01(Base):
    def test_T01_un_span_par_travail_attente_execution_essai_resultat(self):
        jid = self.travail(lambda desk, k, a: {'fichiers': 0})
        r = self.racine(jid)
        a = r['attributs']
        self.assertTrue(HEX32.fullmatch(r['trace_id']) and HEX16.fullmatch(r['span_id']))
        self.assertEqual(r['parent_id'], '')
        self.assertEqual((a['axiorhub.travail.type'], a['axiorhub.travail.resultat'], a['axiorhub.travail.essai']), ('index', 'done', 1))
        self.assertEqual(r['statut'], tr.STATUT_OK)
        self.assertGreaterEqual(a['axiorhub.travail.attente_ms'], 0)
        self.assertGreaterEqual(r['fin'], r['debut'])
        self.assertEqual(a['axiorhub.travail.execution_ms'], (r['fin'] - r['debut']) // 1_000_000)
        self.assertTrue(HEX16.fullmatch(a['axiorhub.dossier.empreinte']))            # dossier sous empreinte opaque seulement
        self.assertNotEqual(a['axiorhub.dossier.empreinte'], __import__('hashlib').sha256(b'DOS-001').hexdigest()[:16])

    def test_T01_identifiants_aleatoires_jamais_un_horodatage(self):
        for _ in range(3):
            self.travail(lambda desk, k, a: {})
        racines = [s for s in self.traces() if s['genre'] == 'travail']
        self.assertEqual(len({s['trace_id'] for s in racines}), 3)
        self.assertEqual(len({s['span_id'] for s in racines}), 3)
        source = inspect.getsource(tr)
        self.assertIn('secrets.token_hex(16)', source)
        self.assertNotRegex(source, r'trace_id[^\n]*time\.')

    def test_T01_appels_modele_enfants_nouvel_essai_relais_jetons(self):
        def producteur(desk, kind, args):
            trace480.record_call(self.cfg(), 'triage', {}, [], 'error', 'moteur_ollama_injoignable', 40)
            trace480.record_call(self.cfg(), 'triage', {}, [], 'error', 'generation_ia_delai_depasse', 30)
            tr.jetons(1200, 80)
            trace480.record_call(self.cfg(provider_type='mistral', provider_id='mistral', model='mistral-large-latest', role='complex'), 'triage', {}, [], 'ok', '', 25)
            return {}
        r = self.racine(self.travail(producteur))
        appels = sorted((s for s in self.enfants(r) if s['genre'] == 'modele'), key=lambda s: s['debut'])
        self.assertEqual(len(appels), 3)
        self.assertEqual([s['statut'] for s in appels], [tr.STATUT_ERREUR, tr.STATUT_ERREUR, tr.STATUT_OK])
        self.assertEqual(appels[0]['code'], 'moteur_ollama_injoignable')
        self.assertTrue(appels[1]['attributs']['axiorhub.modele.nouvel_essai'])        # même fournisseur après un échec
        self.assertFalse(appels[1]['attributs']['axiorhub.modele.relais'])
        dernier = appels[2]['attributs']
        self.assertTrue(dernier['axiorhub.modele.relais'])                             # autre fournisseur après un échec
        self.assertEqual((dernier['gen_ai.system'], dernier['axiorhub.fournisseur.id'], dernier['gen_ai.request.model']), ('mistral', 'mistral', 'mistral-large-latest'))
        self.assertEqual((dernier['gen_ai.usage.input_tokens'], dernier['gen_ai.usage.output_tokens']), (1200, 80))
        self.assertEqual((dernier['axiorhub.modele.palier'], dernier['axiorhub.modele.duree_ms']), ('complex', 25))
        self.assertTrue(dernier['axiorhub.modele.externe'])
        self.assertTrue(all(s['trace_id'] == r['trace_id'] for s in appels))

    def test_T01_lots_nextcloud_et_imap_requetes_et_octets(self):
        def producteur(desk, kind, args):
            for _ in range(3):
                charge.compter('stockage')
                charge.compter('stockage_octets', 1000)
            charge.compter('imap', 2)
            charge.compter('imap_octets', 512)
            return {}
        r = self.racine(self.travail(producteur))
        lots = {s['nom']: s['attributs'] for s in self.enfants(r) if s['genre'] == 'lot'}
        self.assertEqual(lots['lot_nextcloud'], {'axiorhub.lot.service': 'nextcloud', 'axiorhub.lot.requetes': 3, 'axiorhub.lot.octets': 3000})
        self.assertEqual(lots['lot_imap'], {'axiorhub.lot.service': 'imap', 'axiorhub.lot.requetes': 2, 'axiorhub.lot.octets': 512})
        # les compteurs de la page « Charge du serveur » restent attribués au travail, comme en 5.6.93
        mesures = dict(self.desk.db.execute("SELECT categorie,SUM(n) FROM charge5693_mesures WHERE travail='index' GROUP BY categorie").fetchall())
        self.assertEqual((mesures['stockage'], mesures['imap']), (3, 2))

    def test_T01_vrai_client_webdav_compte_les_octets(self):
        from agent.dav import DAV
        corps = (b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>/x/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>'
                 b'</d:resourcetype><d:getetag>"e1"</d:getetag></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')

        class FauxHttp:
            base = 'https://nuage.example.test'

            def request(self, *args, **kw):
                return corps
        with patch('agent.exapp5642.nextcloud_http', lambda url, cfg: FauxHttp()):
            dav = DAV({'url': 'https://nuage.example.test', 'username': 'cabinet', 'password_file': 'x', 'roots': ['/Dossiers']})
            charge.relever()
            self.assertEqual(dav.folder_etag('/Dossiers/DEMO'), '"e1"')
        compteurs = charge.relever()
        self.assertEqual((compteurs['stockage'], compteurs['stockage_octets']), (1, len(corps)))
        self.assertEqual(tr.derniere_etape(), ('nextcloud', 'propfind'))

    def test_T01_evenement_pour_chaque_refus(self):
        def refus(code):
            self.desk.db.execute("DELETE FROM jobs WHERE status='pending'")   # un travail identique en attente serait réutilisé par la file
            self.desk.db.commit()

            def producteur(desk, kind, args):
                raise Stop(code)
            return producteur
        causes = {}
        for code in ('budget_mois_atteint', 'dossier_reserve'):
            r = self.racine(self.travail(refus(code)))
            causes[code] = [ev['attributs'] for ev in r['evenements'] if ev['nom'] == 'refus']
        self.assertEqual(causes['budget_mois_atteint'], [{'axiorhub.refus.cause': 'budget', 'axiorhub.erreur.code': 'budget_mois_atteint'}])
        self.assertEqual(causes['dossier_reserve'][0]['axiorhub.refus.cause'], 'dossier_reserve')
        # refus de la politique au moment de l'appel : événement sur le span de l'appel, pas en double sur le travail
        self.desk.db.execute("DELETE FROM jobs WHERE status='pending'")
        self.desk.db.commit()

        def politique(desk, kind, args):
            trace480.record_call(self.cfg(provider_type='openai', provider_id='openai'), 'compose', {}, [], 'error', 'fournisseur_interdit_par_politique', 0)
            raise Stop('fournisseur_interdit_par_politique')
        r = self.racine(self.travail(politique))
        appel = next(s for s in self.enfants(r) if s['genre'] == 'modele')
        self.assertEqual(appel['evenements'][0]['attributs']['axiorhub.refus.cause'], 'politique')
        self.assertEqual([ev for ev in r['evenements'] if ev['nom'] == 'refus'], [])
        # horaires de l'IA : même classement (le passage par la file est couvert par les essais des horaires)
        from agent.arret5643 import CODE_HORAIRE
        tr.debut_travail(self.desk, {'id': 99, 'kind': 'index', 'attempts': 0}, '')
        tr.refus(CODE_HORAIRE)
        tr.issue('suspended', {})
        tr.clore()
        r = self.racine(99)
        self.assertEqual(r['evenements'][0]['attributs']['axiorhub.refus.cause'], 'horaires')
        self.assertEqual(r['attributs']['axiorhub.travail.resultat'], 'suspended')
        # pause de l'IA, en dernier : le travail suspendu est remis en file
        r = self.racine(self.travail(refus('ia_en_pause')))
        self.assertEqual([ev['attributs'] for ev in r['evenements'] if ev['nom'] == 'refus'],
                         [{'axiorhub.refus.cause': 'pause', 'axiorhub.erreur.code': 'ia_en_pause'}])

    def test_T01_remis_en_attente_et_nouvel_essai_du_travail(self):
        def occupe(desk, kind, args):
            raise Stop('traitement_deja_en_cours')
        jid = self.travail(occupe)
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'pending')
        premier = self.racine(jid)
        self.assertEqual(premier['attributs']['axiorhub.travail.resultat'], 'remis_en_attente')
        self.desk.db.execute('UPDATE jobs SET not_before=NULL,attempts=1 WHERE id=?', (jid,))
        self.desk.db.commit()
        with patch.object(Desk, 'perform', lambda desk, k, a: {}):
            self.desk.work_once()
        second = [s for s in self.traces() if s['genre'] == 'travail' and s['attributs'].get('axiorhub.travail.id') == jid][-1]
        self.assertEqual((second['attributs']['axiorhub.travail.essai'], second['attributs']['axiorhub.travail.resultat']), (2, 'done'))


# ---------------------------------------------------------------- T02 confidentialité
class T02(Base):
    def scenario(self):
        """Une consigne qui nomme DURAND, un document « Conclusions MARTIN », un appelant maladroit qui passe un nom en étape ou en code."""
        def producteur(desk, kind, args):
            data = {'matter': 'DOS-001', 'consignes': 'Appeler DURAND avant vendredi',
                    'sources': [{'id': 'p1', 'kind': 'document', 'path': '/Dossiers/DEMO/Conclusions MARTIN.docx', 'text': 'DURAND contre MARTIN'}]}
            messages = [{'role': 'user', 'content': 'Consigne : écrire à DURAND (durand@example.test) au sujet des Conclusions MARTIN'}]
            trace480.record_call(self.cfg(provider_id='cabinet DURAND'), 'Conclusions MARTIN', data, messages, 'error', 'DURAND : refus pour durand@example.test', 12)
            trace480.record_call(self.cfg(model='llama3:8b'), 'compose', data, messages, 'ok', '', 30)
            raise Stop('dossier DURAND introuvable')
        return self.travail(producteur, args={'matter': 'DOS-001', 'consignes': 'Appeler DURAND avant vendredi', 'document': 'Conclusions MARTIN.docx'})

    def test_T02_consigne_durand_et_document_martin_ni_en_base_ni_a_l_export(self):
        jid = self.scenario()
        self.assertEqual(len(self.enfants(self.racine(jid))), 2)
        brut = self.brut()
        for secret in SECRETS:
            self.assertNotIn(secret, brut)
        self.assertNotIn('durand', brut.lower())

    def test_T02_attribut_hors_liste_ou_mal_forme_refuse(self):
        avant = tr.REFUSES['n']
        garde = tr.filtrer({'axiorhub.consigne': 'Appeler DURAND', 'axiorhub.document': 'Conclusions MARTIN', 'axiorhub.travail.type': 'index',
                            'axiorhub.erreur.code': 'Conclusions MARTIN', 'axiorhub.modele.etape': 'durand@example.test', 'axiorhub.travail.id': 'DOS-001',
                            'axiorhub.dossier.empreinte': 'DOS-001', 'axiorhub.modele.externe': 'oui'})
        self.assertEqual(garde, {'axiorhub.travail.type': 'index'})
        self.assertEqual(tr.REFUSES['n'] - avant, 7)
        # une ligne altérée dans la base ne sort pas davantage à l'export : la liste fermée est réappliquée
        tr.debut_travail(self.desk, {'id': 7, 'kind': 'index', 'attempts': 0}, '')
        tr.issue('done')
        tr.clore()
        tr.vider(self.desk)
        self.desk.db.execute("UPDATE traces5694_spans SET attributs=?, evenements=?", (json.dumps({'axiorhub.consigne': 'Appeler DURAND', 'axiorhub.travail.type': 'index'}),
                                                                                       json.dumps([{'nom': 'note', 'attributs': {'texte': 'MARTIN'}}])))
        self.desk.db.commit()
        sortie = tr.export(self.desk, 24)[1]
        self.assertNotIn('DURAND', sortie)
        self.assertNotIn('MARTIN', sortie)
        self.assertIn('axiorhub.travail.type', sortie)

    def test_T02_mutation_filtre_retire_la_fuite_est_detectee(self):
        """Mutation en mémoire : filtre remplacé par un passe-partout -> le même scénario laisse passer MARTIN (l'essai ci-dessus échouerait)."""
        with patch.object(tr, 'filtrer', lambda attributs: {k: v for k, v in (attributs or {}).items() if v not in (None, '')}):
            self.scenario()
            tr.vider(self.desk)
            lignes = json.dumps([list(r) for r in self.desk.db.execute('SELECT * FROM traces5694_spans').fetchall()], ensure_ascii=False)
        self.assertIn('Conclusions MARTIN', lignes)

    def test_T02_empreinte_du_dossier_propre_a_l_installation(self):
        a = tr.empreinte_dossier(self.desk, 'DOS-001')
        autre = fixtures.EngineTests('test_observation_has_no_mail_write')
        autre.setUp()
        self.addCleanup(autre.doCleanups)
        d2 = Desk(autre.c)
        self.addCleanup(d2.db.close)
        self.assertTrue(HEX16.fullmatch(a))
        self.assertEqual(a, tr.empreinte_dossier(self.desk, 'DOS-001'))
        self.assertNotEqual(a, tr.empreinte_dossier(d2, 'DOS-001'))       # sel propre à chaque installation


# ---------------------------------------------------------------- T03 stockage léger
class T03(Base):
    def test_T03_migration_idempotente(self):
        self.desk.db.execute('CREATE TABLE traces5694_spans(span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, nom TEXT NOT NULL, genre TEXT NOT NULL, '
                             'debut_ns INTEGER NOT NULL, fin_ns INTEGER NOT NULL)')
        self.desk.db.commit()
        tr.schema(self.desk)
        self.desk._traces5694 = False
        tr.schema(self.desk)
        colonnes = [r[1] for r in self.desk.db.execute('PRAGMA table_info(traces5694_spans)')]
        self.assertEqual(colonnes, ['span_id', 'trace_id', 'nom', 'genre', 'debut_ns', 'fin_ns', 'parent_id', 'statut', 'code', 'attributs', 'evenements'])
        self.travail(lambda desk, k, a: {})
        self.assertEqual(len(self.traces()), 1)

    def test_T03_ecritures_groupees_sans_travail_de_fond(self):
        kinds_avant = {r[0] for r in self.desk.db.execute('SELECT DISTINCT kind FROM jobs')}
        for _ in range(3):
            self.travail(lambda desk, k, a: {})
        tr.schema(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM traces5694_spans').fetchone()[0], 0)   # encore au tampon : écriture groupée
        self.assertEqual(len(self.traces()), 3)                                                              # la lecture écrit le tampon d'abord
        kinds = {r[0] for r in self.desk.db.execute('SELECT DISTINCT kind FROM jobs')}
        self.assertEqual(kinds - kinds_avant, {'index'})                                                     # aucun travail nouveau (purge comprise)

    def test_T03_retention_sept_jours_par_defaut(self):
        self.assertEqual(tr.reglage(self.desk)['retention_jours'], 7)
        vieux = time.time_ns() - 8 * 86400 * 10 ** 9
        tr._ajouter(self.desk, [(tr.secrets.token_hex(8), tr.secrets.token_hex(16), '', 'index', 'travail', vieux, vieux + 10, 1, '', '{}', '[]')])
        tr.debut_travail(self.desk, {'id': 1, 'kind': 'index', 'attempts': 0}, '')
        tr.clore()
        self.assertEqual([s['nom'] for s in tr.spans(self.desk)], ['index'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM traces5694_spans WHERE debut_ns<?', (vieux + 1,)).fetchone()[0], 0)

    def test_T03_desactivable(self):
        self.desk.setting(tr.REGLAGE, {'actif': False, 'revision': 3})
        tr.oublier_cache()
        self.travail(lambda desk, k, a: {})
        self.assertEqual(self.traces(), [])
        self.assertIn('Traces désactivées', tw.section_html(self.desk, '/p', True))

    def test_T03_base_occupee_le_travail_continue(self):
        def producteur(desk, kind, args):
            return {'ok': 1}
        with patch.object(tr, '_purger', side_effect=__import__('sqlite3').OperationalError('database is locked')), patch.object(tr, 'TAMPON', 1):
            jid = self.travail(producteur)
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')
        self.assertEqual(len(self.traces()), 1)                                                              # gardé au tampon puis écrit


# ---------------------------------------------------------------- T04 export
class T04(Base):
    def test_T04_export_trace_service_request(self):
        def producteur(desk, kind, args):
            trace480.record_call(self.cfg(), 'triage', {}, [], 'ok', '', 10)
            raise Stop('ia_en_pause')
        self.travail(producteur)
        nom, contenu, n = tr.export(self.desk, 24)
        self.assertTrue(nom.endswith('.otlp.json'))
        corps = json.loads(contenu)
        self.assertEqual(list(corps), ['resourceSpans'])
        rs = corps['resourceSpans'][0]
        self.assertIn({'key': 'service.name', 'value': {'stringValue': 'axiorhub'}}, rs['resource']['attributes'])
        spans = rs['scopeSpans'][0]['spans']
        self.assertEqual(rs['scopeSpans'][0]['scope']['name'], 'axiorhub.traces5694')
        self.assertEqual(len(spans), n)
        racine = next(s for s in spans if 'parentSpanId' not in s)
        enfant = next(s for s in spans if 'parentSpanId' in s)
        self.assertTrue(HEX32.fullmatch(racine['traceId']) and HEX16.fullmatch(racine['spanId']))
        self.assertEqual((enfant['parentSpanId'], enfant['traceId']), (racine['spanId'], racine['traceId']))
        self.assertTrue(racine['startTimeUnixNano'].isdigit() and int(racine['endTimeUnixNano']) >= int(racine['startTimeUnixNano']))
        self.assertEqual((racine['kind'], enfant['kind']), (1, 3))
        attributs = {a['key']: a['value'] for a in racine['attributes']}
        self.assertEqual(attributs['axiorhub.travail.type'], {'stringValue': 'index'})
        self.assertEqual(set(attributs['axiorhub.travail.essai']), {'intValue'})
        self.assertIsInstance(attributs['axiorhub.travail.essai']['intValue'], str)
        self.assertEqual(racine['events'][0]['name'], 'refus')
        self.assertTrue(racine['events'][0]['timeUnixNano'].isdigit())
        self.assertEqual(racine['status'], {'code': 0})                    # suspendu : ni réussi ni en erreur

    def test_T04_collecteur_local_seulement(self):
        prive = lambda h, p, *a: [(2, 1, 6, '', ('127.0.0.2', p))]
        public = lambda h, p, *a: [(2, 1, 6, '', ('1.1.1.1', p))]
        self.assertEqual(tr.verifier_collecteur('http://127.0.0.1:4318'), 'http://127.0.0.1:4318/v1/traces')
        self.assertEqual(tr.verifier_collecteur('http://LOCALHOST:4318/v1/traces'), 'http://localhost:4318/v1/traces')
        self.assertEqual(tr.verifier_collecteur('http://[::1]:4318'), 'http://[::1]:4318/v1/traces')
        self.assertEqual(tr.verifier_collecteur('http://otelcol:4318', resolveur=prive), 'http://otelcol:4318/v1/traces')
        refuses = {'https://collecteur.example.test/v1/traces': 'collecteur_non_local', 'http://192.0.2.10:4318': 'collecteur_non_local',
                   'http://2130706433:4318': 'collecteur_non_local', 'http://0x7f000001:4318': 'collecteur_non_local',
                   'http://localhost.example.test:4318': 'collecteur_non_local', 'http://[2001:db8::1]:4318': 'collecteur_non_local',
                   'http://jean:secret@localhost:4318': 'collecteur_invalide', 'http://localhost:4318/v1/traces?x=1': 'collecteur_invalide',
                   'ftp://localhost/': 'collecteur_invalide', 'localhost:4318': 'collecteur_invalide'}
        for url, code in refuses.items():
            with self.assertRaises(Stop, msg=url) as ctx:
                tr.verifier_collecteur(url, resolveur=prive)
            self.assertEqual(str(ctx.exception), code, url)
        with self.assertRaises(Stop) as ctx:
            tr.verifier_collecteur('http://otelcol:4318', resolveur=public)          # nom sans point qui mène à une adresse publique
        self.assertEqual(str(ctx.exception), 'collecteur_non_local')

    def test_T04_mutation_refus_des_adresses_non_locales(self):
        """Mutations en mémoire de ``verifier_collecteur`` : chaque garde retirée laisse passer une adresse publique (l'essai ci-dessus échouerait)."""
        source = inspect.getsource(tr.verifier_collecteur)
        public = lambda h, p, *a: [(2, 1, 6, '', ('1.1.1.1', p))]

        def muter(avant, apres):
            self.assertIn(avant, source)
            espace = dict(vars(tr))
            exec(compile(source.replace(avant, apres), '<mutation>', 'exec'), espace)
            return espace['verifier_collecteur']
        sans_nom = muter("if '.' in hote or ':' in hote or not _ETIQUETTE.fullmatch(hote) or hote.isdigit() or re.fullmatch(r'0x[0-9a-f]+', hote):", 'if False:')
        self.assertEqual(sans_nom('https://collecteur.example.test/v1/traces', resoudre=False), 'https://collecteur.example.test/v1/traces')
        sans_resolution = muter('ip.is_global or ', '')
        self.assertEqual(sans_resolution('http://otelcol:4318', resolveur=public), 'http://otelcol:4318/v1/traces')

    def test_T04_documentation_de_compatibilite(self):
        from pathlib import Path
        doc = (Path(__file__).resolve().parents[1] / 'docs' / 'TRACES.md').read_text(encoding='utf-8')
        for mot in ('OpenTelemetry Collector', 'Jaeger', 'Grafana Tempo', 'ExportTraceServiceRequest', '4318'):
            self.assertIn(mot, doc)


class T04Web(Base):
    """Pile web complète (authentification, droits, ``web5689``, CSRF), compte administrateur fictif, faux collecteur."""
    ORIGIN = ho.Application.ORIGIN
    requete = ho.Application.requete

    def setUp(self):
        ho.Application.setUp(self)
        tr.oublier_cache()
        self.addCleanup(tr.oublier_cache)
        p = patch('agent.relevance370.review_result', return_value=None)
        p.start()
        self.addCleanup(p.stop)
        self.envois = []
        p = patch.object(tr, '_poster', lambda url, corps, delai=5: self.envois.append((url, json.loads(corps))) or 200)
        p.start()
        self.addCleanup(p.stop)

    def test_T04_telechargement_depuis_la_page_charge(self):
        self.travail(lambda desk, k, a: {})
        r = self.requete('/api440/m5689/charge/traces', requete='heures=24')
        self.assertTrue(r['status'].startswith('200'), r['body'][:300])
        self.assertIn('attachment', r['headers'].get('Content-Disposition', ''))
        self.assertTrue(r['headers']['Content-Type'].startswith('application/json'))
        corps = json.loads(r['body'])
        self.assertEqual(len(corps['resourceSpans'][0]['scopeSpans'][0]['spans']), 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='traces5694_export'").fetchone()[0], 1)

    def test_T04_envoi_local_idempotent_et_reserve_aux_reglages(self):
        self.travail(lambda desk, k, a: {})
        r = self.requete('/api440/m5689/charge/traces/reglage', 'POST', {'revision': 0, 'actif': 'on', 'retention_jours': '7', 'plafond': '20000',
                                                                         'collecteur': 'http://localhost:4318'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        refuse = self.requete('/api440/m5689/charge/traces/reglage', 'POST', {'revision': 1, 'actif': 'on', 'retention_jours': '7', 'plafond': '20000',
                                                                              'collecteur': 'https://collecteur.example.test'})
        self.assertTrue(refuse['status'].startswith('400'))
        self.assertEqual(json.loads(refuse['body'])['error'], 'collecteur_non_local')
        self.assertTrue(self.requete('/api440/m5689/charge/traces/reglage', 'POST', {'revision': 0, 'actif': 'on', 'retention_jours': '7',
                                                                                    'plafond': '20000'})['status'].startswith('409'))
        cle = 'cle-essai-0123456789abcdef'
        r = self.requete('/api440/m5689/charge/traces/envoyer', 'POST', {'cle': cle, 'heures': 24})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual([u for u, _ in self.envois], ['http://localhost:4318/v1/traces'])
        self.assertIn('resourceSpans', self.envois[0][1])
        self.assertTrue(self.requete('/api440/m5689/charge/traces/envoyer', 'POST', {'cle': cle})['status'].startswith('409'))
        self.assertFalse(self.requete('/api440/m5689/charge/traces/envoyer', 'POST', {'cle': 'autre-cle-0123456789ab'}, csrf='faux')['status'].startswith('200'))
        self.assertEqual(len(self.envois), 1)
        # adresse publique glissée dans la base (hors formulaire) : refusée au moment de l'envoi, rien n'est parti
        brut = self.desk.settings(tr.REGLAGE)
        self.desk.setting(tr.REGLAGE, {**brut, 'collecteur': 'https://collecteur.example.test/v1/traces'})
        tr.oublier_cache()
        r = self.requete('/api440/m5689/charge/traces/envoyer', 'POST', {'cle': 'troisieme-cle-0123456789'})
        self.assertEqual(json.loads(r['body'])['error'], 'collecteur_non_local')
        self.assertEqual(len(self.envois), 1)
        self.assertFalse(cw.allowed('assistant', 'charge/traces/envoyer'))
        self.assertFalse(cw.allowed('assistant', 'charge/traces/reglage'))
        from agent import membres5650
        self.assertEqual(membres5650.category('POST', '/api440/m5689/charge/traces/envoyer'), 'reglages')

    def test_T05_page_parametres_rendue(self):
        page = self.requete('/parametres', requete='rubrique=poste')
        corps = page['body'].decode()
        self.assertIn('id="traces5694"', corps)
        self.assertIn('Aucune trace sur la période', corps)
        self.assertTrue(self.requete('/static/v5694-traces.css')['status'].startswith('200'))


# ---------------------------------------------------------------- T05 où ça bloque
def _ligne(kind, debut, duree_ms, statut=1, attente=0, essai=1, parent='', trace=None, nom=None, genre='travail', evenements=(), attributs=None):
    span = tr.secrets.token_hex(8)
    a = attributs or {'axiorhub.travail.type': kind, 'axiorhub.travail.execution_ms': duree_ms, 'axiorhub.travail.attente_ms': attente,
                      'axiorhub.travail.essai': essai, 'axiorhub.travail.resultat': 'done' if statut == 1 else 'error'}
    return span, (span, trace or tr.secrets.token_hex(16), parent, nom or kind, genre, debut, debut + duree_ms * 10 ** 6, statut,
                  '' if statut == 1 else 'http_503', json.dumps(a), json.dumps(list(evenements)))


class T05(Base):
    def remplir(self):
        t0 = time.time_ns() - 3600 * 10 ** 9
        lignes = []
        for i in range(10):
            evenements = [{'nom': 'refus', 'temps': t0, 'attributs': {'axiorhub.refus.cause': 'pause', 'axiorhub.erreur.code': 'ia_en_pause'}}] if i == 3 else []
            span, ligne = _ligne('index', t0 + i * 10 ** 10, 100 * (i + 1), statut=2 if i == 0 else 1, attente=10 * (i + 1), essai=2 if i == 1 else 1, evenements=evenements)
            lignes.append(ligne)
            if i == 9:
                trace = ligne[1]
                lignes.append(_ligne('', t0 + i * 10 ** 10 + 50 * 10 ** 6, 400, parent=span, trace=trace, nom='appel_modele compose', genre='modele',
                                     attributs={'axiorhub.fournisseur.id': 'mistral', 'gen_ai.request.model': 'mistral-large-latest', 'axiorhub.modele.etape': 'compose', 'axiorhub.modele.relais': True})[1])
                lignes.append(_ligne('', t0 + i * 10 ** 10 + 500 * 10 ** 6, 200, parent=span, trace=trace, nom='lot_nextcloud', genre='lot',
                                     attributs={'axiorhub.lot.service': 'nextcloud', 'axiorhub.lot.requetes': 12, 'axiorhub.lot.octets': 4096})[1])
        lignes.append(_ligne('live_mail430', t0, 2000)[1])
        tr._ajouter(self.desk, lignes)

    def test_T05_mediane_centile_attente_erreurs_essais_refus(self):
        self.remplir()
        s = tr.synthese(self.desk, 24)
        index = next(t for t in s['par_type'] if t['kind'] == 'index')
        self.assertEqual((index['n'], index['mediane_ms'], index['p95_ms'], index['attente_mediane_ms']), (10, 500, 1000, 50))
        self.assertEqual((index['erreurs'], index['essais'], index['refus']), (1, 1, {'pause': 1}))
        self.assertEqual(len(s['lents']), 10)
        self.assertEqual(s['lents'][0]['kind'], 'live_mail430')
        plus_lent_index = next(m for m in s['lents'] if m['kind'] == 'index')
        self.assertEqual([(x['nom'], x['decalage_ms'], x['duree_ms']) for x in plus_lent_index['etapes']], [('appel_modele compose', 50, 400), ('lot_nextcloud', 500, 200)])

    def test_T05_page_en_francais_clair_avec_frise(self):
        self.remplir()
        html = tw.section_html(self.desk, '/p', True)
        for texte in ('Où ça bloque', 'Attente avant démarrage (médiane)', '95 % des travaux en moins de', 'Les dix travaux les plus lents',
                      'pause de l’IA : 1', '500 ms', '1,0 s', 'Appel au modèle (mistral · mistral-large-latest, compose), relais vers un autre fournisseur',
                      'Lecture du stockage Nextcloud (12 requêtes, 4 Ko)', '<svg class="t5694-frise"', 'data-api="m5689/charge/traces/reglage"'):
            self.assertIn(texte, html)
        self.assertNotIn('queue latency', html.lower())
        self.assertNotIn('style=', html)                                      # CSP : aucun style en ligne
        self.assertNotIn('<script', html)
        lecture = tw.section_html(self.desk, '/p', False)
        self.assertNotIn('data-api="m5689/charge/traces', lecture)
        self.assertIn('droit « Réglages »', lecture)

    def test_T05_aucune_trace_sur_la_periode_jamais_un_faux_zero(self):
        html = tw.section_html(self.desk, '/p', True)
        self.assertIn('Aucune trace sur la période', html)
        self.assertNotIn('<table', html)
        self.assertNotIn('0 ms', html)
        with patch.object(tr, 'spans', return_value=None):
            html = tw.section_html(self.desk, '/p', True)
        self.assertIn('Traces illisibles', html)
        self.assertNotIn('<table', html)
        with patch.object(tr, 'synthese', side_effect=RuntimeError('panne')):
            self.assertIn('Traces indisponibles', tw.section_sure(self.desk, '/p', True))
        self.assertIn('id="traces5694"', cw.section_html(self.desk, '/p', 'admin', 'administrateur'))

    def test_T05_echappement(self):
        _, ligne = _ligne('<script>alert(1)</script>', time.time_ns() - 10 ** 9, 5)
        tr._ajouter(self.desk, [ligne])
        html = tw.section_html(self.desk, '/p', True)
        self.assertNotIn('<script>alert', html)
        self.assertEqual(tw.duree(None), 'non mesuré')
        self.assertEqual((tw.duree(65000), tw.duree(3720000)), ('1 min 05 s', '1 h 02 min'))


# ---------------------------------------------------------------- T06 erreurs de relève sans cause
class FausseConnexion:
    """Connexion IMAP qui tombe à une étape donnée, avec un message qui cite un tiers (il ne doit jamais être consigné)."""

    def __init__(self, etape, erreur):
        self.etape, self.erreur = etape, erreur

    def login(self, *args):
        return 'OK', [b'']

    def select(self, *args, **kw):
        if self.etape == 'select':
            raise self.erreur
        return 'OK', [b'3']

    def response(self, code):
        return 'OK', [b'7']

    def uid(self, commande, *args):
        if self.etape == commande.lower():
            raise self.erreur
        return 'OK', [b'']

    def logout(self):
        return 'BYE', [b'']


class FauxMoteur:
    """Relève réduite à ses commandes IMAP, avec la vraie classe ``Mailbox`` (étapes notées par le code de production)."""

    def __init__(self, config):
        from agent.mailbox import Mailbox
        self.box = Mailbox(config['mail'])

    def run(self):
        try:
            return {'examined': len(self.box.candidates())}
        finally:
            self.box.close()


class T06(Base):
    def releve(self, etape, erreur):
        self.f.c['mail'] = {**self.f.c['mail'], 'password_file': 'secret-fictif'}
        self.desk.c = self.f.c
        with patch('agent.mailbox.connect', lambda cfg: FausseConnexion(etape, erreur)), patch('agent.mailbox.read_secret', lambda p: 'secret-fictif'), \
                patch('agent.engine.Engine', FauxMoteur):
            jid = self.desk.enqueue('live_mail430', priority=5)
            self.desk.work_once()
        statut, resultat = self.desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (jid,)).fetchone()
        return jid, statut, json.loads(resultat)

    def test_T06_coupure_imap_au_select_cause_consignee_sans_contenu(self):
        """Reproduit les relèves en erreur sans cause : avant la 5.6.94, ``result`` valait seulement
        {"erreur": "action_interrompue_consulter_le_journal"} (rien dans ``$.error`` ni ailleurs : ni classe, ni étape, ni code)."""
        jid, statut, resultat = self.releve('select', imaplib.IMAP4.abort('socket error: EOF (message de durand@example.test, Conclusions MARTIN)'))
        self.assertEqual(statut, 'error')
        self.assertEqual(resultat['erreur'], 'action_interrompue_consulter_le_journal')
        self.assertEqual(resultat['detail'], {'cause': 'imaplib.IMAP4.abort', 'code': 'imap_connexion_interrompue', 'service': 'imap', 'stage': 'select'})
        texte = json.dumps(resultat) + self.brut()
        for secret in ('durand', 'MARTIN', 'EOF'):
            self.assertNotIn(secret, texte)
        a = self.racine(jid)['attributs']
        self.assertEqual((a['error.type'], a['axiorhub.erreur.etape'], a['axiorhub.erreur.code'], a['axiorhub.erreur.service']),
                         ('imaplib.IMAP4.abort', 'select', 'imap_connexion_interrompue', 'imap'))
        self.assertEqual(self.racine(jid)['statut'], tr.STATUT_ERREUR)

    def test_T06_connexion_reinitialisee_pendant_la_recherche(self):
        _, statut, resultat = self.releve('search', ConnectionResetError(104, 'reset by durand@example.test'))
        self.assertEqual(statut, 'error')
        self.assertEqual(resultat['detail'], {'cause': 'ConnectionResetError', 'code': 'errno_104', 'service': 'imap', 'stage': 'search'})
        self.assertNotIn('durand', json.dumps(resultat))

    def test_T06_arret_sans_code_n_est_plus_vide(self):
        def producteur(desk, kind, args):
            raise Stop()
        jid = self.travail(producteur)
        resultat = json.loads(self.desk.db.execute('SELECT result FROM jobs WHERE id=?', (jid,)).fetchone()[0])
        self.assertEqual(resultat['erreur'], 'erreur_sans_code')
        self.assertEqual(resultat['detail']['cause'], 'common.Stop')

    def test_T06_erreur_hors_reseau_etape_dite_comme_derniere_connue(self):
        tr.etape('imap', 'fetch')
        detail = tr.cause_exception(KeyError('Conclusions MARTIN'))
        self.assertEqual(detail, {'cause': 'KeyError', 'service': 'imap', 'stage': 'apres_fetch'})


# ---------------------------------------------------------------- T07 mesure du coût
class T07(Base):
    def test_T07_moins_d_une_ms_par_travail_sur_1000_et_plafond_respecte(self):
        self.desk.setting(tr.REGLAGE, {'actif': True, 'retention_jours': 7, 'plafond': 1000, 'revision': 1})
        tr.oublier_cache()
        cfg = self.cfg()
        externe = self.cfg(provider_type='mistral', provider_id='mistral', model='mistral-small-latest')
        cree = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        total = 0.0
        for i in range(1000):
            row = {'id': i + 1, 'kind': 'index', 'origin': 'automatic', 'attempts': 0, 'created': cree, 'not_before': None}
            charge.relever()
            debut = time.perf_counter()
            tr.debut_travail(self.desk, row, 'DOS-%03d' % (i % 50))
            charge.compter('stockage', 3)
            charge.compter('stockage_octets', 2048)
            charge.compter('imap', 2)
            tr.jetons(120, 30)
            tr.appel_modele(cfg, 'triage', 'error', 'moteur_ollama_injoignable', 12)
            tr.appel_modele(externe, 'triage', 'ok', '', 8)
            tr.refus('ia_en_pause') if i % 100 == 0 else None
            tr.issue('done', {})
            tr.clore()
            total += time.perf_counter() - debut
        debut = time.perf_counter()
        tr.vider(self.desk)
        total += time.perf_counter() - debut
        moyenne_ms = total / 1000 * 1000
        print('\nT07 : %.3f ms de traçage par travail en moyenne (1 000 travaux, 5 spans chacun, écritures et purge comprises)' % moyenne_ms)
        self.assertLess(moyenne_ms, 1.0)
        n = self.desk.db.execute('SELECT COUNT(*) FROM traces5694_spans').fetchone()[0]
        self.assertLessEqual(n, 1000)
        self.assertGreater(n, 900)                                               # des traces entières, les plus récentes
        orphelins = self.desk.db.execute("SELECT COUNT(*) FROM traces5694_spans s WHERE parent_id<>'' AND NOT EXISTS "
                                         "(SELECT 1 FROM traces5694_spans r WHERE r.span_id=s.parent_id)").fetchone()[0]
        self.assertEqual(orphelins, 0)


if __name__ == '__main__':
    unittest.main()
