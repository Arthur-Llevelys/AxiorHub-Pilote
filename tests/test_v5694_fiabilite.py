"""5.6.94, lot fiabilité (F01 à F06) : constats du serveur du cabinet reproduits sur des faux services (Nextcloud, IMAP, modèle).
Données fictives uniquement (DURAND, MARTIN, DOS-001, example.test)."""
from datetime import datetime, timedelta, timezone
import hashlib
import imaplib
import io
import json
import re
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import test_v450_echeances as t450
from agent.common import Stop, private_json
from agent import fiabilite5694 as fia
from agent import watch430


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def erreur_http(status, retry_after=None):
    from agent.erreurs5634 import http_error
    return http_error(status, 'PROPFIND', 'https://cloud.example.test/remote.php/dav/calendars/cabinet/', b'', retry_after)


class Base(t450.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['nextcloud'] = {'roots': ['/Dossiers'], 'url': 'https://cloud.example.test', 'username': 'cabinet'}

    def job(self, kind, status='error', code='http_503', attempts=3, finished=None, args=None, detail=None):
        finished = time.time() if finished is None else finished
        result = {'erreur': code, **({'detail': detail} if detail else {})}
        cur = self.desk.db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts,origin) VALUES(?,?,?,?,?,?,?,?,?)',
                                   (kind, json.dumps(args or {}, sort_keys=True), status, iso(finished - 60), iso(finished), json.dumps(result), 55, attempts,
                                    'automatic'))
        self.desk.db.commit()
        return cur.lastrowid

    def schedule(self, stamp):
        for kind in ('live_mail430', 'live_documents430'):
            self.desk.setting('live430:last:' + kind, stamp)     # seul l'agenda est observé ici
        self.desk.setting('live431:last_daily', stamp)
        with patch('agent.proactive568.schedule', return_value=[]), patch('agent.activity431.maintenance'):
            watch430._schedule_5642(self.desk, stamp)

    def pending(self, kind='live_calendar430'):
        return self.desk.db.execute("SELECT id,attempts,args FROM jobs WHERE kind=? AND status='pending' ORDER BY id", (kind,)).fetchall()


class FakeCalendar:
    """Faux Nextcloud (CalDAV) : répond selon ``etat`` ('503', '200', '401')."""
    etat = '503'
    appels = 0

    def __init__(self, cfg=None):
        pass

    def events(self, *args, **kwargs):
        FakeCalendar.appels += 1
        if FakeCalendar.etat != '200':
            raise erreur_http(int(FakeCalendar.etat))
        return []


# ================================================================================================ F01
class F01Indisponibilite(Base):
    def setUp(self):
        super().setUp()
        FakeCalendar.etat, FakeCalendar.appels = '503', 0
        for p in (patch.object(watch430, 'DAV', FakeCalendar), patch('agent.echeances450.daily_check'),
                  patch('agent.relevance370.review_result', return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def run_calendar(self):
        self.desk.work_once(only={'live_calendar430'})

    def test_F01_503_puis_200_un_essai_espace_puis_la_suspension_se_leve(self):
        debut = time.time() - 3 * 3600
        self.job('live_calendar430', finished=debut)                       # suspendue après trois échecs (503)
        self.schedule(debut + 1700)
        self.assertEqual(self.pending(), [], 'pas avant 30 minutes')
        self.schedule(debut + 1801)
        essai = self.pending()
        self.assertEqual(len(essai), 1, 'un seul nouvel essai automatique')
        self.assertEqual(essai[0]['attempts'], 2)                          # un échec le suspend aussitôt (pas de rafale de trois)
        self.run_calendar()                                                 # Nextcloud répond encore 503
        row = self.desk.db.execute('SELECT status,attempts,finished FROM jobs WHERE id=?', (essai[0]['id'],)).fetchone()
        self.assertEqual((row['status'], row['attempts']), ('error', 3))
        fin = datetime.fromisoformat(row['finished']).timestamp()
        self.schedule(fin + 1801)
        self.assertEqual(self.pending(), [], 'deuxième palier : 1 h')
        self.schedule(fin + 3601)
        self.assertEqual(len(self.pending()), 1)
        FakeCalendar.etat = '200'
        self.run_calendar()                                                 # Nextcloud revenu : succès
        self.assertIsNone(fia.reprise(self.desk, 'live_calendar430'))
        from agent import queue521
        agenda = next(x for x in queue521.report(self.desk)['surveillance'] if x['kind'] == 'live_calendar430')
        self.assertFalse(agenda['suspended'])
        self.assertEqual(FakeCalendar.appels, 2)

    def test_F01_paliers_plafonnes_a_quatre_heures(self):
        t = time.time() - 50 * 3600
        self.job('live_calendar430', finished=t)
        for n in range(1, 6):
            self.job('live_calendar430', finished=t + n * 60, args={fia.MARQUE: 'oui'})
        found = fia.reprise(self.desk, 'live_calendar430')
        self.assertEqual(found['essais'], 5)
        self.assertAlmostEqual(found['essai'] - (t + 300), 4 * 3600, delta=2)

    def test_F01_retry_after_respecte(self):
        t = time.time() - 10
        self.job('live_calendar430', finished=t, detail={'retry_after': 5400})
        self.assertAlmostEqual(fia.reprise(self.desk, 'live_calendar430')['essai'] - t, 5400, delta=2)
        self.job('live_calendar430', finished=t + 1, detail={'retry_after': 30})       # plus court que le palier : le palier reste
        self.assertAlmostEqual(fia.reprise(self.desk, 'live_calendar430')['essai'] - (t + 1), 1800, delta=2)

    def test_F01_429_502_504_et_connexion_sont_passageres(self):
        for code in ('http_429', 'http_502', 'http_504', 'connexion_http_indisponible', 'delai_http_depasse'):
            self.assertTrue(fia.passagere(code), code)
        for code in ('http_401', 'http_403', 'http_404', 'agenda_non_configure', 'adresse_webdav_invalide', ''):
            self.assertFalse(fia.passagere(code), code)

    def _relance_seule(self, code):
        debut = time.time() - 30 * 3600
        self.desk.setting('live430:last:live_calendar430', 0)
        self.job('live_calendar430', code=code, finished=debut)
        self.schedule(debut + 20 * 3600)
        return bool(self.pending())

    def test_F01_401_403_404_restent_suspendues_jusqu_a_decision_humaine(self):
        for code in ('http_401', 'http_403', 'http_404', 'agenda_non_configure'):
            with self.subTest(code=code):
                self.desk.db.execute("DELETE FROM jobs")
                self.desk.db.commit()
                self.assertFalse(self._relance_seule(code))
        from agent import queue521
        self.assertEqual(queue521.resume_surveillance(self.desk)['resumed'], ['Agenda'])   # « Relancer la surveillance » : toujours possible

    def test_F01_mutation_regle_passagere_autorisation(self):
        """La règle « une erreur passagère se relance seule, une erreur d'autorisation non » est bien celle que les tests observent."""
        self.assertTrue(self._relance_seule('http_503'))
        self.desk.db.execute("DELETE FROM jobs")
        self.desk.db.commit()
        with patch.object(fia, 'PASSAGERES', fia.PASSAGERES | {'http_401'}):          # mutation : un 401 deviendrait passager
            self.assertTrue(self._relance_seule('http_401'))
        self.desk.db.execute("DELETE FROM jobs")
        self.desk.db.commit()
        with patch.object(fia, 'PASSAGERES', fia.PASSAGERES - {'http_503'}):          # mutation : un 503 deviendrait définitif
            self.assertFalse(self._relance_seule('http_503'))

    def test_F01_une_seule_ligne_pendant_la_panne(self):
        t = time.time() - 600
        self.job('live_calendar430', finished=t)
        for kind in ('deck530_sync', 'sync_caldav_tasks', 'deck530_sync'):
            self.job(kind, attempts=1, finished=t + 30)
        from agent import sante5636, web520
        problems = web520.diagnosis(self.desk)['problems']
        lignes = [p for p in problems if p.startswith('Nextcloud indisponible depuis')]
        self.assertEqual(len(lignes), 1, problems)
        self.assertRegex(lignes[0], r'^Nextcloud indisponible depuis \d\d:\d\d, nouvel essai à \d\d:\d\d\.$')
        self.assertFalse([p for p in problems if 'suspendue après trois échecs' in p or 'en erreur depuis 24 heures' in p], problems)
        point = next(x for x in sante5636.checks(self.desk) if x['id'] == 'nextcloud')
        self.assertEqual(point['actions'][0]['api'], 'm520/queue/resume')

    def test_F01_une_erreur_d_un_autre_service_reste_signalee(self):
        t = time.time() - 600
        self.job('live_calendar430', finished=t)
        self.job('prepare_reply', code='http_503', attempts=1, finished=t + 30)          # fournisseur d'IA, pas Nextcloud
        from agent import web520
        problems = web520.diagnosis(self.desk)['problems']
        self.assertTrue([p for p in problems if 'en erreur depuis 24 heures' in p], problems)

    def test_F01_deck_et_taches_attendent_le_nouvel_essai(self):
        from agent import cadence5662
        now = time.time()
        self.assertTrue(cadence5662.allow(self.desk, 'deck530_sync', 5, now))
        self.job('live_calendar430', finished=now - 60)
        self.assertFalse(cadence5662.allow(self.desk, 'deck530_sync', 5, now))
        self.assertFalse(cadence5662.allow(self.desk, 'sync_caldav_tasks', 15, now))
        self.assertTrue(cadence5662.allow(self.desk, 'deck530_sync', 5, now + 1900))       # au moment du nouvel essai
        self.assertTrue(cadence5662.allow(self.desk, 'health', 30, now))                    # rien d'autre n'est retenu

    def test_F01_retry_after_lu_dans_la_reponse_http(self):
        from agent.common import HTTP
        from agent.erreurs5634 import public
        client = HTTP('https://cloud.example.test')
        headers = Message()
        headers['Retry-After'] = '120'

        def refus(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 503, 'Service Unavailable', headers, io.BytesIO(b''))
        with patch.object(client.opener, 'open', refus):
            with self.assertRaises(Stop) as ctx:
                client.request('PROPFIND', 'https://cloud.example.test/remote.php/dav/files/cabinet/')
        self.assertEqual(str(ctx.exception), 'http_503')
        self.assertEqual(public(ctx.exception.detail)['retry_after'], 120)


# ================================================================================================ F02
class F02Suspendu(Base):
    MID = 'DOS-001'

    def echecs(self, kind):
        for _ in range(3):
            self.job(kind, code='generation_ia_incomplete', attempts=1, args={'matter': self.MID})

    def test_F02_advance_ne_finit_plus_en_erreur_quand_la_fiche_est_suspendue(self):
        from agent.arret5643 import automatic
        self.echecs('refresh_brief')
        with automatic(self.desk):
            jid = self.desk.enqueue('advance_matter420', {'matter': self.MID, 'trigger_source_id': 'sig-1'}, priority=5)
        with patch('agent.relevance370.review_result', return_value=None):
            self.desk.work_once()
        row = self.desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (jid,)).fetchone()
        self.assertEqual(row['status'], 'done', row['result'])
        result = json.loads(row['result'])
        self.assertIn({'travail': 'refresh_brief', 'motif': fia.IGNORE}, result['ignores'])
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='refresh_brief' AND status='pending'").fetchone())

    def test_F02_travail_suspendu_qui_demarre_quand_meme_est_ignore(self):
        self.echecs('refresh_brief')
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority,origin) VALUES('refresh_brief',?,'pending',?,50,'automatic')",
                             (json.dumps({'matter': self.MID}), self.desk.now()))
        self.desk.db.commit()
        with patch.object(self.desk, 'perform', side_effect=AssertionError('ne doit pas tourner')):
            self.desk.work_once()
        row = self.desk.db.execute("SELECT status,result FROM jobs WHERE kind='refresh_brief' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row['status'], 'ignored')
        self.assertEqual(json.loads(row['result'])['motif'], fia.IGNORE)
        from agent import web520
        problems = web520.diagnosis(self.desk)['problems']
        erreurs = [p for p in problems if 'en erreur depuis 24 heures' in p]
        self.assertEqual(erreurs, ['3 demande(s) en erreur depuis 24 heures (motifs ci-dessous).'])   # les trois vrais échecs, pas un quatrième
        from agent import pause5629
        self.assertEqual(pause5629.failures(self.desk, 'refresh_brief', self.MID)[0], 3)         # l'ignoré ne lève pas le coupe-circuit

    def test_F02_une_demande_de_l_avocat_n_est_jamais_bloquee(self):
        self.echecs('refresh_brief')
        jid = self.desk.enqueue('refresh_brief', {'matter': self.MID, 'source': 'avocat'}, priority=0)
        with patch.object(self.desk, 'perform', return_value={'fiche_version': 1}), patch('agent.relevance370.review_result', return_value=None):
            self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')

    def test_F02_suite_d_un_travail_fini_vers_un_travail_suspendu(self):
        self.echecs('refresh_brief')
        self.assertIsNone(self.desk._then('refresh_brief', {'matter': self.MID}))

    def test_F02_aucune_projection_d_incident_pour_un_ignore(self):
        from agent import production420
        production420.ensure_schema(self.desk)
        before = self.desk.db.execute("SELECT COUNT(*) FROM production_deliverables_v420 WHERE status='error'").fetchone()[0]
        production420.record_job(self.desk, {'id': 999, 'kind': 'prepare_reply'}, {'key': 'k'}, 'ignored', {'motif': fia.IGNORE})
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM production_deliverables_v420 WHERE status='error'").fetchone()[0], before)


# ================================================================================================ F06
class F06Dates(Base):
    MID = 'DOS-001'

    def test_F06_fenetre_de_quatorze_jours_exacte_sur_son_bord(self):
        from agent import pause5629
        bord = datetime.now(timezone.utc) - timedelta(days=14)
        if bord.hour == 0 and bord.minute < 5:
            self.skipTest('bord à minuit : la comparaison de format ne se distingue pas')
        hors = (bord - timedelta(minutes=2)).timestamp()        # juste hors de la fenêtre, même jour calendaire
        for _ in range(3):
            self.job('refresh_brief', code='x', attempts=1, args={'matter': self.MID}, finished=hors)
        self.assertEqual(pause5629.blocked(self.desk, self.MID), [])
        dedans = (bord + timedelta(minutes=5)).timestamp()
        for _ in range(3):
            self.job('index', code='x', attempts=1, args={'matter': self.MID}, finished=dedans)
        self.assertEqual([x['kind'] for x in pause5629.blocked(self.desk, self.MID)], ['index'])

    def test_F06_travail_de_l_ia_sur_trente_jours(self):
        from agent import suivi5679
        bord = datetime.now(timezone.utc) - timedelta(days=120)
        if bord.hour == 0 and bord.minute < 5:
            self.skipTest('bord à minuit')
        self.job('prepare_reply', status='done', code='', args={'matter': self.MID}, finished=(bord - timedelta(minutes=2)).timestamp())
        self.assertNotIn(self.MID, suivi5679.last_ai_work(self.desk))
        self.job('prepare_reply', status='done', code='', args={'matter': self.MID}, finished=(bord + timedelta(minutes=5)).timestamp())
        self.assertIn(self.MID, suivi5679.last_ai_work(self.desk))

    def test_F06_plus_aucune_comparaison_de_format_mixte(self):
        racine = Path(fia.__file__).parent
        fautifs = []
        for path in racine.glob('*.py'):
            text = path.read_text(encoding='utf-8')
            for m in re.finditer(r"(?:date|datetime)\('now'", text):
                fautifs.append(path.name + ':' + str(text[:m.start()].count('\n') + 1))
        self.assertEqual(fautifs, [])


# ================================================================================================ F05
class FakeNextcloud:
    """Faux Nextcloud (WebDAV) pour la surveillance des documents par ETag : ``panne`` (None, 503) et dossiers absents (404)."""
    etag_propage = True

    def __init__(self, tree, absents=()):
        self.cfg = {'roots': ['/Dossiers']}
        self.tree, self.absents, self.panne, self.requetes = tree, set(absents), None, 0
        self.refused = []
        self.etags_dossiers = {}

    def _check(self, path):
        self.requetes += 1
        if self.panne:
            raise erreur_http(self.panne)
        if any(path == a or path.startswith(a + '/') for a in self.absents):
            raise erreur_http(404)

    def folder_etag(self, path):
        self._check(path)
        if path not in self.tree:
            raise erreur_http(404)
        return '"e-%s"' % hashlib.sha256(path.encode()).hexdigest()[:8]

    def list_folder(self, path):
        self._check(path)
        out = []
        for child in self.tree.get(path, []):
            directory = child in self.tree
            out.append({'path': child, 'directory': directory, 'etag': '"e-%s"' % hashlib.sha256(child.encode()).hexdigest()[:8],
                        'modified': 'Mon, 05 Jan 2026 10:00:00 GMT', 'size': 10})
        self.etags_dossiers[path] = '"e-%s"' % hashlib.sha256(path.encode()).hexdigest()[:8]
        return out


class F05Illisibles(Base):
    def setUp(self):
        super().setUp()
        matters = [{'id': 'DOS-%03d' % i, 'client_name': 'Client %s' % n, 'path': '/Dossiers/%s - 2024000%03d' % (n, i), 'references': [],
                    'aliases': [], 'correspondents': []} for i, n in enumerate(('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC'), 1)]
        private_json(Path(self.desk.c['matters_file']), matters)
        self.matters = matters
        tree = {'/Dossiers': [m['path'] for m in matters]}
        for m in matters:
            tree[m['path']] = [m['path'] + '/note.txt']
        self.nc = FakeNextcloud(tree)
        for p in (patch('agent.notices440.observe_inventory', return_value=0), patch('agent.echeances450.observe_inventory', return_value=0),
                  patch('agent.automation568.observe', return_value=set()), patch('agent.cadence5662.busy', return_value=''),
                  patch.object(self.desk, 'admit', return_value=[1, 2])):
            p.start()
            self.addCleanup(p.stop)

    def tour(self, n=1):
        out = None
        for _ in range(n):
            out = watch430.documents_check(self.desk, self.nc)
        return out

    def test_F05_un_503_ne_signale_rien_et_le_dossier_est_relu_au_lot_suivant(self):
        self.tour(2)                                     # premier tour complet : tous réglés avec leur ETag
        self.assertEqual(watch430.unreadable(self.desk), {})
        curseur = self.desk.settings('live430:document_cursor', 0)
        self.nc.panne = 503
        out = self.tour()
        self.assertEqual(watch430.unreadable(self.desk), {}, 'un 503 ne crée pas de signalement « illisible »')
        self.assertTrue(out['continue_scan'])
        self.assertEqual(self.desk.settings('live430:document_cursor', 0), curseur, 'le lot sera repris tel quel')
        self.assertLessEqual(self.nc.requetes, 40)

    def test_F05_hypothese_anciens_signalements_503_jamais_relus_en_mode_etag(self):
        """Signalements 503 laissés par la 5.6.93 : le dossier n'a pas changé d'ETag ; il est relu au lot suivant et retiré de la liste."""
        self.tour(2)
        gone = {m['id']: {'label': 'x', 'path': m['path'], 'code': 'http_503', 'at': iso(time.time() - 20 * 3600), 'count': 1} for m in self.matters[:4]}
        self.desk.setting(watch430.UNREADABLE, gone)
        self.tour()
        restants = watch430.unreadable(self.desk)
        self.assertEqual(len(restants), 1, 'trois dossiers relus en tête du lot (taille du lot)')
        self.tour()
        self.assertEqual(watch430.unreadable(self.desk), {})

    def test_F05_un_404_est_reessaye_une_fois_par_jour_au_plus(self):
        perdu = self.matters[1]
        self.nc.absents.add(perdu['path'])
        for _ in range(8):
            self.desk.setting('live430:documents_sweep_done', 0)
            self.tour()
        entry = watch430.unreadable(self.desk)[perdu['id']]
        self.assertEqual((entry['code'], entry['count']), ('http_404', 1))
        entry['at'] = iso(time.time() - 25 * 3600)
        self.desk.setting(watch430.UNREADABLE, {perdu['id']: entry})
        for _ in range(4):
            self.tour()
        self.assertEqual(watch430.unreadable(self.desk)[perdu['id']]['count'], 2)

    def test_F05_dossier_introuvable_affiche_avec_ses_actions(self):
        from agent import sante5636
        perdu = self.matters[1]
        self.desk.setting(watch430.UNREADABLE, {perdu['id']: {'label': 'MARTIN', 'path': perdu['path'], 'code': 'http_404', 'at': iso(time.time()), 'count': 3}})
        html = sante5636.unreadable_html(self.desk, '/p')
        self.assertIn(fia.INTROUVABLE_TEXTE, html)
        self.assertIn('m5636/dossier/retrouver', html)
        self.assertIn('Ne plus surveiller', html)
        self.assertNotIn('m5636/dossier/reessayer', html)
        texte = next(x['text'] for x in sante5636.checks(self.desk) if x['id'] == 'introuvables')
        self.assertIn('introuvable', texte)

    def test_F05_retrouver_son_nouvel_emplacement(self):
        perdu = self.matters[1]
        nouveau = '/Dossiers/Archives/MARTIN - 2024000002'
        self.nc.absents.add(perdu['path'])
        self.nc.tree[nouveau] = []
        self.tour(3)
        with patch.object(self.desk, 'enqueue', return_value=77) as lance:
            out = fia.retrouver(self.desk, perdu['id'], self.nc)
        self.assertEqual(out['candidats'], [])
        self.assertEqual(lance.call_args[0][0], 'discover')                 # découverte existante, à la demande de l'avocat
        self.desk.db.execute('INSERT OR REPLACE INTO directories VALUES (?,?)', (nouveau, self.desk.now()))   # vu par la découverte
        self.desk.db.execute('INSERT OR REPLACE INTO directories VALUES (?,?)', ('/Dossiers/Autre - 2099000002', self.desk.now()))
        out = fia.retrouver(self.desk, perdu['id'], self.nc)
        self.assertEqual(out['candidats'], [nouveau])
        with self.assertRaisesRegex(Stop, 'emplacement_non_propose'):
            fia.deplacer(self.desk, perdu['id'], '/Dossiers/Autre - 2099000002')
        fia.deplacer(self.desk, perdu['id'], nouveau)
        from agent.common import load_matters
        self.assertEqual(next(m for m in load_matters(self.desk.c) if m['id'] == perdu['id'])['path'], nouveau)
        self.assertNotIn(perdu['id'], watch430.unreadable(self.desk))

    def test_F05_route_retrouver_reservee_a_l_avocat(self):
        from agent import web5636
        env = {}
        with patch('agent.web567.actor', return_value=('assistant1', 'assistant')), patch('agent.web5636._post', return_value={'matter': 'DOS-002'}):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                web5636.api(env, self.desk, None, '/p', 'm5636/dossier/retrouver', {}, 'POST')


# ================================================================================================ F03
SOURCE = {'id': 'S1', 'kind': 'document', 'path': '/Dossiers/DEMO/Conclusions MARTIN.pdf', 'modified': '2026-01-05', 'excerpt': 'Texte fictif.',
          'partial': False}


class FakeIndex:
    def sync_page(self, *args, **kwargs):
        return None, []

    def overview_sources(self, matter, n):
        return [dict(SOURCE)]


class FakeModel:
    """Modèle simulé : la fiche entière dépasse la limite de sortie ; les rubriques passent, sauf celles listées dans ``coupees``."""
    coupees = set()
    erreur_rubrique = 'generation_ia_incomplete'
    entiere = 'trop_longue'
    calls = []

    def __init__(self, cfg=None):
        pass

    def ask(self, stage, data):
        FakeModel.calls.append(stage)
        if stage == 'case_brief':
            if FakeModel.entiere == 'trop_longue':
                raise Stop('generation_ia_incomplete')
            return {**fia.fiche_vide(), 'summary': 'Litige DURAND contre MARTIN.', 'source_ids': ['S1']}
        if stage == 'case_brief_part':
            champs = data['rubriques_demandees']
            if set(champs) & FakeModel.coupees:
                raise Stop(FakeModel.erreur_rubrique)
            part = fia.fiche_vide()
            for c in champs:
                part[c] = {'summary': 'Litige DURAND contre MARTIN.', 'chronology': [{'date': '2026-01-05', 'text': 'Assignation.', 'source_ids': ['S1'],
                           'status': 'assertion'}]}.get(c, part[c])
            part['source_ids'] = ['S1']
            return part
        if stage == 'legal_memory':
            return {'records': [], 'contradictions': [], 'limits': []}
        raise AssertionError(stage)


class F03Fiche(Base):
    def setUp(self):
        super().setUp()
        FakeModel.coupees, FakeModel.entiere, FakeModel.calls, FakeModel.erreur_rubrique = set(), 'trop_longue', [], 'generation_ia_incomplete'
        for p in (patch('agent.intelligence.DAV'), patch('agent.intelligence.source_index', return_value=FakeIndex()),
                  patch('agent.intelligence.Model', FakeModel), patch('agent.intelligence.routed_config', return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def brief(self):
        from agent import intelligence
        return intelligence.refresh_brief(self.desk, {'matter': 'DOS-001'})

    def stored(self):
        row = self.desk.db.execute("SELECT data FROM case_briefs WHERE matter='DOS-001' ORDER BY version DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def test_F03_fiche_trop_longue_produite_par_rubriques(self):
        out = self.brief()
        data = self.stored()
        self.assertFalse(data['partielle'])
        self.assertEqual(data['rubriques_manquantes'], [])
        self.assertEqual(data['chronology'][0]['text'], 'Assignation.')
        self.assertEqual(FakeModel.calls, ['case_brief'] + ['case_brief_part'] * 4 + ['legal_memory'])
        self.assertFalse(out['partielle'])

    def test_F03_fiche_partielle_enregistree_comme_partielle(self):
        FakeModel.coupees = {'chronology'}
        out = self.brief()
        data = self.stored()
        self.assertTrue(data['partielle'])
        self.assertEqual(data['rubriques_manquantes'], ['Chronologie'])
        self.assertTrue(any('Chronologie' in x for x in data['limits']))
        self.assertEqual(data['summary'], 'Litige DURAND contre MARTIN.')
        self.assertTrue(out['partielle'])
        self.assertIn('Chronologie', out['message'])

    def test_F03_fiche_entiere_sans_decoupage(self):
        FakeModel.entiere = 'ok'
        self.brief()
        self.assertEqual(FakeModel.calls, ['case_brief', 'legal_memory'])
        self.assertFalse(self.stored()['partielle'])

    def test_F03_budget_atteint_rien_n_est_enregistre(self):
        FakeModel.coupees, FakeModel.erreur_rubrique = {'deadlines'}, 'budget_mission_atteint'
        with self.assertRaisesRegex(Stop, 'budget_mission_atteint'):
            self.brief()
        self.assertIsNone(self.stored())

    def test_F03_aucune_rubrique_produite_reste_une_erreur(self):
        FakeModel.coupees = {'summary', 'chronology', 'deadlines', 'latest_instructions'}
        with self.assertRaisesRegex(Stop, 'generation_ia_incomplete'):
            self.brief()
        self.assertIsNone(self.stored())

    def test_F03_etape_rubrique_du_modele_bornee(self):
        from agent.model import Model
        model = Model.__new__(Model)
        model.cfg, model.provider_type, model.provider_id = {}, 'ollama', 'ollama'
        seen = {}

        def complete(messages, temperature, maximum, schema):
            seen['maximum'], seen['system'] = maximum, messages[0]['content']
            return json.dumps({**fia.fiche_vide(), 'chronology': [{'date': '', 'text': 'Assignation.', 'source_ids': ['S1'], 'status': 'assertion'}]})
        model.complete = complete
        out = model.ask('case_brief_part', {'sources': [SOURCE], 'rubriques_demandees': ['chronology']})
        self.assertEqual(out['chronology'][0]['text'], 'Assignation.')
        self.assertLessEqual(seen['maximum'], 6000)
        self.assertIn('rubriques_demandees', seen['system'])


# ================================================================================================ F04
class FakeIMAP5694:
    """Faux serveur IMAP en mémoire (réception, envoyés, brouillons) qui compte chaque commande."""
    def __init__(self, condstore=True):
        self.folders = {'INBOX': {}, 'Sent': {}, 'Drafts': {}}
        self.validity = {k: 11 for k in self.folders}
        self.uidnext = {k: 1 for k in self.folders}
        self.modseq = {k: 1 for k in self.folders}
        self.capabilities = ('IMAP4REV1', 'UIDPLUS') + (('CONDSTORE',) if condstore else ())
        self.selected = None
        self.commands = 0

    def add(self, folder, headers, flags=(), when=None):
        uid = self.uidnext[folder]
        self.uidnext[folder] += 1
        self.modseq[folder] += 1
        raw = ''.join('%s: %s\r\n' % kv for kv in headers.items()) + '\r\nCorps fictif.\r\n'
        self.folders[folder][uid] = {'raw': raw.encode(), 'head': raw.split('\r\n\r\n')[0], 'flags': set(flags),
                                     'h': {k.lower(): str(v).lower() for k, v in headers.items()},
                                     'date': when or datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)}
        return uid

    def flag(self, folder, uid, flag):
        self.folders[folder][uid]['flags'].add(flag)
        self.modseq[folder] += 1

    def expunge(self, folder, uid):
        self.folders[folder].pop(uid)
        self.modseq[folder] += 1

    def login(self, *a):
        return 'OK', [b'ok']

    def logout(self):
        return 'BYE', [b'']

    def select(self, folder, readonly=False):
        self.commands += 1
        name = folder.strip('"')
        if name not in self.folders:
            return 'NO', [b'']
        self.selected = name
        return 'OK', [str(len(self.folders[name])).encode()]

    def response(self, name):
        return name, [str(self.validity[self.selected]).encode()]

    def status(self, folder, items):
        self.commands += 1
        name = folder.strip('"')
        text = 'MESSAGES %d UIDNEXT %d UIDVALIDITY %d' % (len(self.folders[name]), self.uidnext[name], self.validity[name])
        if 'HIGHESTMODSEQ' in items:
            text += ' HIGHESTMODSEQ %d' % self.modseq[name]
        return 'OK', [('"%s" (%s)' % (name, text)).encode()]

    def _eval(self, item, tokens):
        c = tokens.pop(0).upper()
        if c == 'UNDELETED':
            return '\\Deleted' not in item['flags']
        if c == 'UNSEEN':
            return '\\Seen' not in item['flags']
        if c == 'SINCE':
            return item['date'].date() >= datetime.strptime(tokens.pop(0), '%d-%b-%Y').date()
        if c in ('TO', 'FROM'):
            return tokens.pop(0).strip('"').lower() in item['h'].get(c.lower(), '')
        if c == 'HEADER':
            key, value = tokens.pop(0).lower(), tokens.pop(0).strip('"').lower()
            return value in item['h'].get(key, '')
        if c == 'OR':
            a = self._eval(item, tokens)
            b = self._eval(item, tokens)
            return a or b
        raise AssertionError(c)

    def _match(self, item, criteria):
        tokens = [str(x) for x in criteria]
        ok = True
        while tokens:
            ok = self._eval(item, tokens) and ok
        return ok

    def uid(self, command, *args):
        self.commands += 1
        msgs = self.folders[self.selected]
        command = command.upper()
        if command == 'SEARCH':
            return 'OK', [' '.join(str(u) for u, it in sorted(msgs.items()) if self._match(it, args[1:])).encode()]
        if command != 'FETCH':
            raise AssertionError(command)
        wanted = []
        top = max(msgs) if msgs else 0
        for part in str(args[0]).split(','):
            if ':' in part:
                a, b = part.split(':')
                lo, hi = int(a), (top if b == '*' else int(b))
                wanted += [u for u in sorted(msgs) if lo <= u <= hi] or ([top] if b == '*' and top else [])
            else:
                wanted.append(int(part))
        query, out = args[1], []
        for u in wanted:
            it = msgs.get(u)
            if not it:
                continue
            flags = ' '.join(sorted(it['flags']))
            if query == '(UID FLAGS INTERNALDATE RFC822.SIZE)':
                out.append(('%d (UID %d FLAGS (%s) INTERNALDATE "%s" RFC822.SIZE %d)' % (u, u, flags, it['date'].strftime('%d-%b-%Y %H:%M:%S %z'),
                                                                                          len(it['raw']))).encode())
            elif query == '(UID FLAGS)':
                out.append(('%d (UID %d FLAGS (%s))' % (u, u, flags)).encode())
            elif 'HEADER' in query:
                head = it['head'].encode() + b'\r\n\r\n'
                out += [(('%d (UID %d BODY[HEADER] {%d}' % (u, u, len(head))).encode(), head), b')']
            elif query == '(BODY.PEEK[])':
                out += [(('%d (UID %d BODY[] {%d}' % (u, u, len(it['raw']))).encode(), it['raw']), b')']
            else:
                raise AssertionError(query)
        return 'OK', out


class F04Boite(Base):
    N = 5000
    ITEMS = 200

    def setUp(self):
        super().setUp()
        self.desk.c['mail'].update({'own_addresses': ['cabinet@example.test']})
        self.imap = FakeIMAP5694()
        reports = Path(self.desk.c['state_dir']) / 'reports'
        reports.mkdir(exist_ok=True)
        self.keys = {}
        start = datetime.now(timezone.utc) - timedelta(days=40)
        for i in range(self.N):
            when = start + timedelta(minutes=10 * i)
            sender = 'client%03d@example.test' % (i % 400)
            uid = self.imap.add('INBOX', {'From': sender, 'To': 'cabinet@example.test', 'Subject': 'Dossier DOS-%03d point %d' % (i % 50, i),
                                          'Message-ID': '<m%d@example.test>' % i, 'Date': when.strftime('%a, %d %b %Y %H:%M:%S +0000')},
                                flags=('\\Seen',), when=when)
            if i >= self.N - self.ITEMS:
                key = hashlib.sha256(('cle-%d' % i).encode()).hexdigest()
                self.keys[i] = (key, uid, sender)
                (reports / (key + '.json')).write_text(json.dumps({'key': key, 'subject': 'x', 'sender': sender, 'source_uid': str(uid),
                                                                   'source_mailbox': 'INBOX', 'received_at': when.isoformat(),
                                                                   'incoming_message_id': '<m%d@example.test>' % i}), encoding='utf-8')
                self.desk.db.execute("INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                                     (key, 'needs_action', 'review', '', 'DOS-001', 'x', sender, when.isoformat(), self.desk.now()))
        for j in range(300):
            when = start + timedelta(hours=j)
            self.imap.add('Sent', {'From': 'cabinet@example.test', 'To': 'client%03d@example.test' % (j % 150), 'Subject': 'Re: ancien %d' % j,
                                   'Message-ID': '<s%d@example.test>' % j, 'In-Reply-To': '<m%d@example.test>' % j}, flags=('\\Seen',), when=when)
        for j in range(20):
            self.imap.add('Drafts', {'From': 'cabinet@example.test', 'To': 'tiers%d@example.test' % j, 'Subject': 'Brouillon %d' % j,
                                     'Message-ID': '<d%d@example.test>' % j}, flags=('\\Draft',))
        self.desk.db.commit()

    def box(self):
        from agent.mailbox import Mailbox
        box = Mailbox.__new__(Mailbox)
        box.cfg, box.conn, box.selected, box.validity = self.desk.c['mail'], self.imap, '', ''
        return box

    def passage(self, complet=False):
        from agent import portfolio
        self.imap.commands = 0
        started = time.perf_counter()
        if complet:
            with patch.object(fia, 'plan_rapprochement', return_value=None):
                out = portfolio.reconcile_work_items(self.desk, self.box())
        else:
            out = portfolio.reconcile_work_items(self.desk, self.box())
        return out, self.imap.commands, time.perf_counter() - started

    def states(self):
        return dict(self.desk.db.execute('SELECT mail_key,state FROM work_items').fetchall())

    def test_F04_mesure_avant_apres_sur_5000_messages(self):
        avant, n_avant, t_avant = self.passage()                  # premier passage : complet (comme chaque passage en 5.6.93)
        _, n_second_5693, t_second_5693 = self.passage(complet=True)   # 5.6.93 : le passage suivant relit tout de nouveau
        apres, n_apres, t_apres = self.passage()                  # 5.6.94 : passage suivant incrémental, rien n'a changé
        print('\nF04 mesure (faux IMAP, %d messages, %d cartes ouvertes) : passage complet %d commandes en %.2f s ; '
              'passage suivant 5.6.93 %d commandes en %.2f s ; passage suivant 5.6.94 %d commandes en %.3f s' % (
                  self.N, self.ITEMS, n_avant, t_avant, n_second_5693, t_second_5693, n_apres, t_apres))
        self.assertEqual(avant.get('mode'), 'complet')
        self.assertEqual(apres.get('mode'), 'incremental')
        self.assertLessEqual(n_apres, 5)
        self.assertLess(n_apres * 100, n_second_5693)

    def _changements(self):
        items = sorted(self.keys)
        repondu, envoye, supprime, brouillon = items[-1], items[-2], items[-3], items[-4]
        self.imap.flag('INBOX', self.keys[repondu][1], '\\Answered')
        self.imap.add('Sent', {'From': 'cabinet@example.test', 'To': self.keys[envoye][2], 'Subject': 'Re: Dossier',
                               'Message-ID': '<r1@example.test>', 'In-Reply-To': '<m%d@example.test>' % envoye},
                      when=datetime.now(timezone.utc) + timedelta(minutes=1))
        self.imap.expunge('INBOX', self.keys[supprime][1])
        self.imap.add('Drafts', {'From': 'cabinet@example.test', 'To': self.keys[brouillon][2], 'Subject': 'Re: Dossier',
                                 'Message-ID': '<b1@example.test>', 'In-Reply-To': '<m%d@example.test>' % brouillon}, flags=('\\Draft',))
        return {self.keys[repondu][0]: 'handled', self.keys[envoye][0]: 'handled', self.keys[supprime][0]: 'archived',
                self.keys[brouillon][0]: 'draft_ready'}

    def test_F04_meme_resultat_que_le_passage_complet(self):
        self.passage()
        initial = self.states()
        attendu = self._changements()
        out, n, _ = self.passage()
        self.assertEqual(out['mode'], 'incremental')
        incremental = self.states()
        for key, state in attendu.items():
            self.assertEqual(incremental[key], state)
        self.desk.db.executemany('UPDATE work_items SET state=? WHERE mail_key=?', [(v, k) for k, v in initial.items()])
        self.desk.db.commit()
        self.passage(complet=True)
        self.assertEqual(self.states(), incremental)
        self.assertLess(n, 80)

    def test_F04_sans_condstore_un_seul_fetch_des_drapeaux(self):
        self.imap.capabilities = ('IMAP4REV1',)
        self.passage()
        out, n, _ = self.passage()
        self.assertEqual(out['mode'], 'incremental')
        self.assertLessEqual(n, 5)

    def test_F04_relecture_complete_une_fois_par_jour_et_apres_uidvalidity(self):
        self.passage()
        state = self.desk.settings(fia.ETAT_BOITE)
        state['complet'] = time.time() - 25 * 3600
        self.desk.setting(fia.ETAT_BOITE, state)
        self.assertEqual(self.passage()[0]['mode'], 'complet')
        self.assertEqual(self.passage()[0]['mode'], 'incremental')
        self.imap.validity['Sent'] = 12
        self.assertEqual(self.passage()[0]['mode'], 'complet')


if __name__ == '__main__':
    unittest.main()
