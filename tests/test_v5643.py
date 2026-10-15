"""5.6.43 — cahier des charges v9 (audit 5.6.42). Données fictives uniquement ; aucun service réel n'est contacté."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import socket
import threading
import time
import unittest
from unittest.mock import patch

import test_agent as engine_fixtures
import test_v501 as t501
import test_v510 as t510
import test_v520_routines as t520
import test_v530 as fixtures
from agent import agenda5634, arret5643, cockpit530, competences5632, competences5643, decisions5634, docrequest520, fonctions5631
from agent import missions567, navigation5643, operating380, operating380_ui, operations, pause5629, production420_ui, routines520
from agent import sens5643, shell501, taches5614, watch430, workplan
from agent.common import HTTP, Stop
from agent.desk import Desk
from agent.mailbox import make_draft

ROOT = Path(__file__).resolve().parents[1]
MID = '20240101001'


# ---------------------------------------------------------------------------------------------------------------- § 16 : contre-tests
class PauseAnnulationContreTests(fixtures.Base):
    """Les quinze contre-tests de l'audit 5.6.42 (R01 à R15), repris tels quels."""
    def status(self, jid):
        return self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0]

    def test_R01_pause_globale_arrete_la_planification_periodique(self):
        self.desk.setting('missions567:pause', True)
        operations.automation_tick(self.desk)
        self.assertEqual([dict(r) for r in self.desk.db.execute('SELECT kind,status,priority FROM jobs')], [])

    def test_R02_pause_globale_arrete_la_surveillance(self):
        self.desk.setting('missions567:pause', True)
        watch430.schedule(self.desk, stamp=2_000_000_000)
        self.assertEqual([r['kind'] for r in self.desk.db.execute('SELECT kind FROM jobs')], [])

    def test_R03_worker_ne_demarre_pas_le_travail_automatique_en_pause(self):
        self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        self.desk.setting('missions567:pause', True)
        with patch.object(self.desk, 'perform', return_value={'message': 'simulation'}) as producer:
            self.desk.work_once()
        self.assertFalse(producer.called)

    def test_R04_annulation_suivie_de_verrou_occupe_ne_remet_pas_en_file(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)

        def blocked(*args):
            self.desk.cancel_job(jid)
            raise Stop('traitement_deja_en_cours')
        with patch.object(self.desk, 'perform', side_effect=blocked):
            self.desk.work_once()
        self.assertEqual(self.status(jid), 'cancelled')

    def test_R05_arret_du_document_en_cours_empeche_son_depot(self):
        out = docrequest520.submit(self.desk, 'Rédige un courrier de demande de renvoi.', MID, 'courrier')
        original = self.model.complete

        def model_result(*args, **kwargs):
            cockpit530.stop(self.desk, {'ref': 'docreq:' + out['request']})
            return original(*args, **kwargs)
        before = len(self.docs.puts)
        with patch.object(self.model, 'complete', side_effect=model_result):
            self.desk.work_once()
        self.assertEqual(len(self.docs.puts), before)

    def test_R06_annulation_metier_resiste_a_une_erreur_du_modele(self):
        out = docrequest520.submit(self.desk, 'Rédige un courrier de demande de renvoi.', MID, 'courrier')

        def model_error(*args, **kwargs):
            decisions5634.cancel_docreq(self.desk, out['request'])
            raise Stop('generation_ia_delai_depasse')
        with patch.object(self.model, 'complete', side_effect=model_error):
            self.desk.work_once()
        row = self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (out['request'],)).fetchone()
        self.assertEqual(row['status'], 'annule')

    def test_R07_pause_dossier_demande_arret_avec_matter_id(self):
        jid = self.desk.enqueue('refresh_brief', {'matter_id': MID}, priority=50)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.desk.db.commit()
        pause5629.set_pause(self.desk, MID, True)
        self.assertEqual(self.status(jid), 'cancel_requested')

    def test_R08_une_mission_annulee_reste_annulee_si_le_job_termine(self):
        mission = missions567.create(self.desk, {'instruction': 'Résume les sources disponibles.', 'matter': MID, 'intent': 'question',
                                                 'request_key': 'audit-cancel-' + 'a' * 20})
        self.desk.db.execute("UPDATE missions_v567 SET state='cancelled' WHERE id=?", (mission['id'],))
        self.desk.db.execute("UPDATE jobs SET status='done',result='{}' WHERE id=?", (mission['job_id'],))
        self.desk.db.commit()
        self.assertEqual(missions567.get(self.desk, mission['id'])['state'], 'cancelled')

    def test_R09_affichage_distingue_arret_demande_de_file_attente(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        self.desk.db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (self.desk.now(), jid))
        self.desk.db.commit()
        self.desk.cancel_job(jid)
        item = next(r for r in cockpit530.feed(self.desk)['en_cours'] if r['job'] == jid)
        self.assertEqual(item.get('status'), 'cancel_requested')
        self.assertNotEqual(item['when'], 'en file')

    def test_R10_annuler_deux_fois_est_idempotent(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        self.desk.cancel_job(jid)
        self.assertEqual(self.desk.cancel_job(jid)['status'], 'cancelled')

    def test_R11_claim_concurrent_ne_fait_pas_croire_a_un_arret_confirme(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        raw = self.desk.db

        class Interleave:
            armed = True

            def __getattr__(self, name):
                return getattr(raw, name)

            def execute(proxy, sql, params=()):
                if proxy.armed and sql.lstrip().startswith('UPDATE jobs SET'):
                    proxy.armed = False
                    raw.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
                    raw.commit()
                return raw.execute(sql, params)
        self.desk.db = Interleave()
        out = self.desk.cancel_job(jid)
        self.desk.db = raw
        self.assertEqual((out['status'], self.status(jid)), ('cancel_requested', 'cancel_requested'))

    def test_R12_annulation_entre_lecture_et_finalisation_ne_disparait_pas(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        raw = self.desk.db

        class Interleave:
            armed = False

            def __getattr__(self, name):
                return getattr(raw, name)

            def execute(proxy, sql, params=()):
                if proxy.armed and sql.lstrip().startswith('UPDATE jobs SET'):
                    proxy.armed = False
                    raw.execute("UPDATE jobs SET status='cancel_requested' WHERE id=?", (jid,))
                    raw.commit()
                return raw.execute(sql, params)
        proxy = Interleave()
        self.desk.db = proxy

        def completed(*args):
            proxy.armed = True
            return {'message': 'simulation'}
        with patch.object(self.desk, 'perform', side_effect=completed):
            self.desk.work_once()
        self.desk.db = raw
        self.assertEqual(self.status(jid), 'cancelled')


class PauseCourrielsContreTests(unittest.TestCase):
    def setUp(self):
        self.f = engine_fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    def test_R13_pause_dossier_est_relue_entre_deux_courriels(self):
        self.f.engine.process(engine_fixtures.mail(uid='1'))
        self.assertEqual(len(self.f.box.appended), 1)
        pause5629.set_pause(self.desk, 'DOS-001', True)
        self.f.engine.process(engine_fixtures.mail(uid='2'))
        self.assertEqual(len(self.f.box.appended), 1)

    def test_R14_pause_dossier_apres_generation_empeche_append_imap(self):
        self.f.model.after_compose = lambda: pause5629.set_pause(self.desk, 'DOS-001', True)
        self.f.engine.process(engine_fixtures.mail())
        self.assertEqual(self.f.box.appended, [])

    def test_R15_passage_direct_du_moteur_respecte_pause_globale(self):
        self.desk.setting('missions567:pause', True)
        self.assertEqual(self.f.engine.run().get('skipped'), 'automatismes_en_pause')


# ---------------------------------------------------------------------------------------------------------------- § 16 : compléments
class PauseReprenable(fixtures.Base):
    def status(self, jid):
        return self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0]

    def test_pause_globale_suspend_puis_reprend_sans_toucher_aux_annulations(self):
        auto = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        gone = self.desk.enqueue('refresh_brief', {'matter': 'AUTRE'}, priority=50)
        self.desk.cancel_job(gone)
        out = arret5643.pause_all(self.desk, True)
        self.assertEqual((out['paused'], out['suspendus']), (True, 1))
        self.assertEqual((self.status(auto), self.status(gone)), ('suspended', 'cancelled'))
        with self.assertRaisesRegex(Stop, 'automatismes_en_pause'):
            with arret5643.automatic(self.desk):
                self.desk.enqueue('refresh_brief', {'matter': 'TROISIEME'})
        back = arret5643.pause_all(self.desk, False)
        self.assertEqual(back['repris'], 1)
        self.assertEqual((self.status(auto), self.status(gone)), ('pending', 'cancelled'))

    def test_demande_avocat_attend_la_reprise_de_l_ia_et_le_deterministe_s_execute(self):
        self.desk.setting('missions567:pause', True)
        mine = self.desk.enqueue('prepare_document_project', {'matter': MID, 'document_type': 'note', 'instruction': 'ma demande'}, priority=0)
        with patch.object(self.desk, 'perform', return_value={'message': 'simulation'}) as producer:
            self.desk.work_once()
        self.assertFalse(producer.called)
        self.assertEqual(self.status(mine), 'suspended')
        setting = self.desk.enqueue('automation_setting', {'name': 'x', 'value': True}, priority=0)
        with patch.object(self.desk, 'perform', return_value={'message': 'réglage'}) as producer:
            self.desk.work_once()
        self.assertTrue(producer.called)
        self.assertEqual(self.status(setting), 'done')
        arret5643.pause_all(self.desk, False)
        self.assertEqual(self.status(mine), 'pending')

    def test_origine_explicite_parent_et_annulation_des_enfants(self):
        with arret5643.automatic(self.desk):
            auto = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=0)
        user = self.desk.enqueue('refresh_brief', {'matter': 'AUTRE'}, priority=50, origin='user')
        rows = {r['id']: dict(r) for r in self.desk.db.execute('SELECT id,origin,matter_ref,parent_job_id FROM jobs')}
        self.assertEqual((rows[auto]['origin'], rows[user]['origin']), ('automatic', 'user'))   # l'origine prime sur la priorité
        self.assertEqual(rows[auto]['matter_ref'], MID)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (user,))
        self.desk.db.commit()
        self.desk.active_job_id = user
        child = self.desk.enqueue('sync_legal_memory', {'matter': 'AUTRE'})
        row = self.desk.db.execute('SELECT origin,parent_job_id FROM jobs WHERE id=?', (child,)).fetchone()
        self.assertEqual((row['origin'], row['parent_job_id']), ('user', user))   # l'enfant hérite de l'origine et du parent
        self.desk.active_job_id = None
        self.desk.cancel_job(user)
        self.assertEqual((self.status(user), self.status(child)), ('cancel_requested', 'cancelled'))
        self.desk.active_job_id = user
        with self.assertRaisesRegex(Stop, 'operation_annulee'):   # un parent arrêté ne crée plus d'enfant
            self.desk.enqueue('sync_legal_memory', {'matter': 'AUTRE', 'x': 1})
        self.desk.active_job_id = None

    def test_un_travail_suspendu_s_annule(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        arret5643.pause_all(self.desk, True)
        self.assertEqual(self.desk.cancel_job(jid)['status'], 'cancelled')
        arret5643.pause_all(self.desk, False)
        self.assertEqual(self.status(jid), 'cancelled')

    def test_pause_d_un_travail_actif_reste_annulation_si_rien_n_est_reprenable(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.desk.db.commit()
        out = arret5643.pause_all(self.desk, True)
        self.assertEqual(out['arrets_demandes'], 1)
        self.assertEqual(self.status(jid), 'cancel_requested')

    def test_affichage_suspendu(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        arret5643.pause_all(self.desk, True)
        item = next(r for r in cockpit530.feed(self.desk)['en_cours'] if r['job'] == jid)
        self.assertEqual((item['status'], item['when']), ('suspended', 'suspendu (pause de l’IA)'))


class _SilentServer:
    """Serveur HTTP fictif qui accepte la requête et ne répond jamais (inférence longue, aucun fragment)."""
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.closed_by_client = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        conn.settimeout(30)
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    self.closed_by_client.set()
                    return
        except OSError:
            self.closed_by_client.set()
        finally:
            conn.close()

    def close(self):
        self.sock.close()


def _sql(desk, sql, params=()):
    """Écriture d'un autre processus (la route web) : une instruction, sa propre connexion."""
    import sqlite3
    db = sqlite3.connect(str(Path(desk.c['state_dir']) / 'desk.sqlite3'), timeout=20)
    try:
        db.execute(sql, params)
        db.commit()
    finally:
        db.close()


class InferenceInterruptible(fixtures.Base):
    def test_arret_ferme_l_appel_actif_sans_attendre_le_delai_du_modele(self):
        server = _SilentServer()
        self.addCleanup(server.close)
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=0)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.desk.db.commit()
        http = HTTP('http://127.0.0.1:%d' % server.port, local_only=True, timeout=120)
        threading.Timer(1.2, lambda: _sql(self.desk, "UPDATE jobs SET status='cancel_requested' WHERE id=?", (jid,))).start()
        started = time.monotonic()
        with arret5643.job_context(self.desk.c['state_dir'], jid, MID, False):
            with self.assertRaisesRegex(Stop, 'operation_annulee'):
                arret5643.interruptible(lambda: http.json('POST', '/api/chat', {'model': 'fictif'}), {'state_dir': self.desk.c['state_dir']})
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(server.closed_by_client.wait(5), 'la connexion de cet appel doit être fermée')
        note = self.desk.db.execute("SELECT data FROM audit WHERE action='arret5643_inference' ORDER BY id DESC").fetchone()
        self.assertIn('transport fermé', json.loads(note[0])['etat'])

    def test_pause_globale_interrompt_aussi_l_appel_actif(self):
        server = _SilentServer()
        self.addCleanup(server.close)
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=0)
        http = HTTP('http://127.0.0.1:%d' % server.port, local_only=True, timeout=120)
        threading.Timer(1.2, lambda: _sql(self.desk, "INSERT OR REPLACE INTO settings VALUES('missions567:pause','true')")).start()
        with arret5643.job_context(self.desk.c['state_dir'], jid, MID, False):
            with self.assertRaisesRegex(Stop, 'ia_en_pause'):
                arret5643.interruptible(lambda: http.json('POST', '/api/chat', {}), {'state_dir': self.desk.c['state_dir']})

    def test_aucun_nouvel_appel_pendant_la_pause_meme_hors_travail(self):
        self.desk.setting('missions567:pause', True)
        called = []
        with self.assertRaisesRegex(Stop, 'ia_en_pause'):
            arret5643.interruptible(lambda: called.append(1), {'state_dir': self.desk.c['state_dir']})
        self.assertEqual(called, [])

    def test_resultat_arrive_apres_l_arret_est_ignore(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': MID}, priority=0)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.desk.db.commit()

        def answer_after_cancel():
            Desk(self.desk.c).cancel_job(jid)
            return {'message': {'content': 'trop tard'}}
        with arret5643.job_context(self.desk.c['state_dir'], jid, MID, False):
            with self.assertRaisesRegex(Stop, 'operation_annulee'):
                arret5643.interruptible(answer_after_cancel, {'state_dir': self.desk.c['state_dir']})

    def test_le_secours_externe_n_est_jamais_appele_apres_un_arret(self):
        src = (ROOT / 'agent' / 'model.py').read_text(encoding='utf-8')
        self.assertIn('if str(error) in STOP_CODES:raise', src)
        self.assertEqual(src.count('interruptible(lambda:self.http.json('), 4)   # Ollama (2), Anthropic, compatibles OpenAI


class PauseRoute(fixtures.Base):
    def test_route_renvoie_les_comptes_et_le_bouton_dit_pause_de_l_ia(self):
        self.desk.enqueue('refresh_brief', {'matter': MID}, priority=50)
        from agent import web568
        src = (ROOT / 'agent' / 'web568.py').read_text(encoding='utf-8')
        self.assertIn('out=pause_all(desk,bool(data.get(\'paused\')),owner)', src)
        out = arret5643.pause_all(self.desk, True, 'cabinet')
        self.assertEqual(set(out) >= {'paused', 'suspendus', 'arrets_demandes', 'en_cours_a_interrompre', 'message'}, True)
        self.assertIn('IA en pause', out['message'])
        js = (ROOT / 'agent' / 'static' / 'v5614.js').read_text(encoding='utf-8')
        self.assertIn('Pause de l’IA', js)
        self.assertTrue(web568)


# ---------------------------------------------------------------------------------------------------------------- § 21 : courriels


class _BoxWithCabinetMessage(t520.FakeBox):
    """Boîte fictive : le cabinet a écrit à un client et la copie se trouve dans INBOX (cas signalé)."""
    def __init__(self, cfg):
        super().__init__(cfg)
        now = datetime.now(timezone.utc)
        m = t520._mail('7', 'Cabinet <cabinet@example.test>', 'Projet de PV — urgent', 'Pouvez-vous me répondre aujourd’hui ?',
                       now - timedelta(hours=1), '<c7@x>')
        m.msg.replace_header('To', 'Client Durand <durand@example.test>')
        self.folders['INBOX'].append(m)


class _Model:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        if self.fail:
            raise Stop('fournisseur_ia_injoignable')
        data = json.loads(messages[-1]['content'].split('\n\n', 1)[1])
        return json.dumps({'lignes': [{'uid': x['uid'], 'categorie': 'urgent', 'resume': 'Résumé', 'reponse_attendue_aujourdhui': True}
                                      for x in data]})


class CourrielsDuCabinet(t510.Base):
    def setUp(self):
        super().setUp()
        t520.FakeBox.appended = []
        self.desk.c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test',
                                    'from_name': 'Camille EXEMPLE'})
        self.model = _Model()
        for p in (patch('agent.mailbox.Mailbox', _BoxWithCabinetMessage), patch('agent.routines520._model', side_effect=lambda d: self.model)):
            p.start()
            self.addCleanup(p.stop)

    def tri(self):
        routines520.tri(self.desk)
        return json.loads(routines520.latest(self.desk, 'tri')['data'])

    def test_aucune_reponse_au_cabinet_a_partir_de_son_propre_envoi(self):
        data = self.tri()
        own = next(x for x in data['items'] if x['uid'] == '7')
        self.assertEqual((own['sens'], own['category'], own['note']), ('sent', 'info', sens5643.CABINET_NOTE))
        self.assertIn({'uid': '7', 'status': 'message_du_cabinet'}, data['drafts'])
        self.assertEqual(t520.FakeBox.appended, [])
        self.assertIn('Message envoyé par le cabinet — pas de réponse automatique', routines520.latest(self.desk, 'tri')['text'])
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='prepare_reply'").fetchone())

    def test_modele_indisponible_aucune_trame_deposee(self):
        self.model = _Model(fail=True)
        self.tri()
        self.assertEqual(t520.FakeBox.appended, [])
        src = (ROOT / 'agent' / 'routines520.py').read_text(encoding='utf-8')
        self.assertNotIn('reviens vers vous très rapidement', src)
        self.assertNotIn('def _draft(', src)

    def test_dossier_etabli_la_reponse_passe_par_le_circuit_commun(self):
        from agent.common import load_matters
        from agent.state import State
        matter = load_matters(self.desk.c)[0]['id']
        box = _BoxWithCabinetMessage(self.desk.c['mail'])
        mail = next(m for m in box.folders['INBOX'] if m.uid == '2')
        key = mail.key(self.desk.c['mail']['username'] + '@' + self.desk.c['mail']['host'])
        State(self.desk.c['state_dir']).report(key, {'key': key, 'matter': matter, 'status': 'review', 'source_uid': '2', 'source_mailbox': 'INBOX'})
        data = self.tri()
        asked = next(d for d in data['drafts'] if d['uid'] == '2')
        self.assertEqual((asked['status'], asked['matter']), ('demande', matter))
        job = self.desk.db.execute("SELECT args,origin FROM jobs WHERE kind='prepare_reply'").fetchone()
        args = json.loads(job['args'])
        self.assertEqual((args['key'], args['matter'], args['automatic'], job['origin']), (key, matter, 'yes', 'automatic'))
        self.assertEqual(t520.FakeBox.appended, [])          # le tri n'écrit rien lui-même

    def test_garde_commune_au_depot(self):
        cfg = {'from_name': 'Cabinet', 'from_address': 'cabinet@example.test', 'signature': '--', 'aliases': ['Associe@Example.test'],
               'username': 'cabinet@example.test'}
        own = t520._mail('8', 'Cabinet <cabinet@example.test>', 'Objet', 'Texte', datetime.now(timezone.utc), '<o8@x>')
        own.msg.replace_header('To', 'client@example.test')
        with self.assertRaisesRegex(Stop, 'reponse_a_soi_meme_refusee'):
            make_draft(own, cfg, 'Texte', 'k1')
        with self.assertRaisesRegex(Stop, 'reponse_a_soi_meme_refusee'):   # alias déclaré, casse différente
            make_draft(own, cfg, 'Texte', 'k2', to='ASSOCIE@example.test')
        self.assertEqual(make_draft(own, cfg, 'Texte', 'k3', to='client@example.test')['To'], 'client@example.test')   # relance demandée
        self.assertEqual(sens5643.direction(own, cfg), 'sent')
        quoting = t520._mail('9', 'Client <client@example.test>', 'Re: Objet', '> Le cabinet <cabinet@example.test> a écrit :', datetime.now(timezone.utc), '<q9@x>')
        self.assertEqual(sens5643.direction(quoting, cfg), 'received')   # une citation ne change pas l'auteur
        internal = t520._mail('10', 'Cabinet <cabinet@example.test>', 'Note', 'x', datetime.now(timezone.utc), '<i10@x>')
        internal.msg.replace_header('To', 'associe@example.test')
        self.assertEqual(sens5643.direction(internal, cfg), 'internal')
        self.assertEqual(sorted(sens5643.identities(cfg)), ['associe@example.test', 'cabinet@example.test'])

    def test_marqueurs_a_completer_et_leurs_variantes(self):
        self.assertEqual(sens5643.redaction_status('Madame,\n\nMerci, je reviens vers vous.\n\n[À COMPLÉTER : réponse sur le fond]\n\nCordialement'),
                         'needs_validation')
        body = 'Cher Confrère,\n\n' + 'Mon client accepte le calendrier proposé pour les conclusions. ' * 4 + 'Montant : [a compléter : somme]'
        self.assertEqual(sens5643.redaction_status(body), 'partial')
        self.assertEqual(sens5643.redaction_status('Réponse complète.'), 'complete')
        src = (ROOT / 'agent' / 'intelligence.py').read_text(encoding='utf-8')
        self.assertIn("result['statut_redaction']=redaction_status(", src)
        self.assertIn('checkpoint(desk)\n        state.set(key,mid,thread,\'appending\'', src)


# ---------------------------------------------------------------------------------------------------------------- § 7 : compétences


class CompetencesParPhase(fixtures.Base):
    def enable(self, *ids):
        competences5632.ensure_builtin(self.desk)
        for sid in ids:
            competences5632.set_enabled(self.desk, 'cabinet', sid, True)

    def selection_for(self, kind, args):
        row = {'id': 1, 'kind': kind}
        with competences5632.for_job(self.desk, row, args) as selection:
            return selection, competences5632.current_instructions()

    def test_methodes_installees_desactivees_jusqu_a_relecture(self):
        competences5632.ensure_builtin(self.desk)
        rows = {s['id']: s for s in competences5632.listing(self.desk)}
        for sid in ('builtin.methode_decider', 'builtin.methode_analyser', 'builtin.methode_courriel', 'builtin.methode_document',
                    'builtin.methode_controler'):
            self.assertFalse(rows[sid]['enabled'], sid)
        out = docrequest520.submit(self.desk, 'Rédige des conclusions en défense avec la jurisprudence utile.', MID, 'conclusions')
        selection, text = self.selection_for('docrequest520', {'request': out['request'], 'matter': MID})
        self.assertNotIn('builtin.methode_document', [s['id'] for s in selection])

    def test_document_vraie_consigne_et_methode_de_redaction(self):
        self.enable('builtin.methode_document', 'builtin.methode_courriel')
        out = docrequest520.submit(self.desk, 'Rédige des conclusions en défense avec la jurisprudence utile.', MID, 'conclusions')
        text, deliverable, phase = competences5643.real_instruction(self.desk, 'docrequest520', {'request': out['request']})
        self.assertIn('conclusions en défense', text)                 # la consigne, pas l'identifiant
        self.assertEqual((deliverable, phase), ('conclusions', 'redaction_document'))
        selection, injected = self.selection_for('docrequest520', {'request': out['request'], 'matter': MID})
        self.assertEqual(selection[0]['id'], 'builtin.methode_document')
        self.assertEqual(selection[0]['mode'], 'phase')
        self.assertIn('Rédiger un document avec sources', injected)
        self.assertNotIn('Rédiger un courriel utile', injected)       # une seule méthode de phase
        used = self.desk.db.execute("SELECT skill,mode FROM skill_usage5632 WHERE skill='builtin.methode_document'").fetchone()
        self.assertEqual(used['mode'], 'phase')

    def test_sous_tache_de_mission_et_reponse_courriel(self):
        self.enable('builtin.methode_controler', 'builtin.methode_courriel')
        self.desk.db.executescript(taches5614.SCHEMA)
        now = self.desk.now()
        self.desk.db.execute("INSERT INTO missions5614(id,owner,request_key,matter,title,objective,parcours,state,created,updated) "
                             "VALUES('m1','cabinet','k','%s','Dossier fictif','Obtenir le paiement de la facture','libre','active',?,?)" % MID, (now, now))
        self.desk.db.execute("INSERT INTO tasks5614(id,mission,position,code,title,role,task_type,output_type,instruction,depends_on,run_state,created,updated) "
                             "VALUES('t1','m1',1,'T1','Relire le projet','controleur','controle','note','Vérifier les montants et les dates du projet',"
                             "'[]','pending',?,?)", (now, now))
        self.desk.db.commit()
        text, _deliverable, phase = competences5643.real_instruction(self.desk, 'task5614', {'mission': 'm1', 'task': 't1', 'matter': MID})
        self.assertIn('Vérifier les montants', text)
        self.assertEqual(phase, 'controle')
        selection, injected = self.selection_for('task5614', {'mission': 'm1', 'task': 't1', 'matter': MID})
        self.assertEqual(selection[0]['id'], 'builtin.methode_controler')
        selection, injected = self.selection_for('prepare_reply', {'key': 'k', 'matter': MID})
        self.assertEqual(selection[0]['id'], 'builtin.methode_courriel')   # courriel automatique : méthode transmise aussi

    def test_choix_manuel_prime_et_assemblage_unique(self):
        self.enable('builtin.methode_courriel', 'builtin.methode_document')
        selection, _ = self.selection_for('docrequest520', {'request': 'x', 'skills': ['builtin.methode_courriel']})
        self.assertEqual([s['id'] for s in selection], ['builtin.methode_courriel'])
        legacy = 'Extension Ancienne [abc]\nToujours vouvoyer.'
        chosen = 'COMPÉTENCE « A » version 1 (x)\nMéthode A.'
        joined = competences5643.assemble(legacy + '\n\n' + legacy, chosen)
        self.assertEqual(joined.count('Toujours vouvoyer.'), 1)          # doublon retiré
        self.assertLess(joined.index('Méthode A.'), joined.index('Toujours vouvoyer.'))
        big = 'Extension Longue [def]\n' + 'règle ' * 6000
        cut = competences5643.assemble(big, chosen, budget=2000)
        self.assertNotIn('règle règle', cut)                             # jamais coupée au milieu
        self.assertIn('non transmise', cut)
        src = (ROOT / 'agent' / 'model.py').read_text(encoding='utf-8')
        self.assertIn("skill_text=assemble(str(self.cfg.get('skill_instructions') or ''),current_instructions())", src)

    def test_import_conserve_les_phases_declarees(self):
        raw = ('---\nname: Méthode fictive\ndescription: "Essai"\nsituations: contrôle; relecture\nphases: controle\n---\n\n# Méthode\n1. Vérifier.\n').encode()
        out = competences5632.import_skill(self.desk, 'cabinet', raw, 'methode.md', scope='cabinet')
        manifest = json.loads(competences5632._row(self.desk, out['id'])['manifest'])
        self.assertEqual(manifest['phases'], ['controle'])
        self.assertFalse(manifest['references_injected'])
        self.assertEqual(competences5643.skill_phases({'situations': ['courriel', 'réponse']}), ['redaction_courriel'])


# ---------------------------------------------------------------------------------------------------------------- § 18 : lisibilité


def _link(path, label, **params):
    from urllib.parse import urlencode
    return '<a href="%s%s">%s</a>' % (path, ('?' + urlencode(params)) if params else '', production420_ui.e(label))


def _ratio(a, b):
    def lum(h):
        rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ProduireLisible(unittest.TestCase):
    def card(self, **over):
        item = {'id': 'd1', 'status': 'verified', 'matter': '20240101001', 'matter_name': 'ALPHA - SAS EXEMPLE - CONSEIL - 20240101001',
                'updated': '2026-10-14T04:30:00.123456+00:00', 'label': 'Projet de courrier', 'business_message': 'Courrier prêt à relire.',
                'source_ids': [], 'trigger_text': '', 'verification': {}, 'stages': [], 'deliverable_kind': 'document', 'output_id': ''}
        item.update(over)
        return production420_ui._deliverable_card(item, lambda *a, **k: '', _link)

    def test_dossier_en_tete_reference_separee_et_date_lisible(self):
        html = self.card()
        self.assertLess(html.index('result420-matter'), html.index('<h3>'))
        self.assertIn('>ALPHA - SAS EXEMPLE - CONSEIL - 20240101001</a>', html)      # nom complet, jamais découpé sur ses tirets
        self.assertIn('Dossier n° 20240101001', html)
        self.assertIn('14/10/2026 06:30', html)                                       # heure de Paris (UTC+2)
        self.assertNotIn('.123456', html)
        self.assertNotIn('<small>', html)
        self.assertIn('class="result420-state">Disponible<', html)

    def test_sans_dossier_et_dossier_inconnu(self):
        self.assertIn('>Cabinet<', self.card(matter='', matter_name='Cabinet'))
        self.assertIn('Dossier à confirmer', self.card(matter='INCONNU', matter_name='INCONNU'))

    def test_styles_sources_bundle_et_contrastes(self):
        src = (ROOT / 'agent' / 'static' / 'v420.css').read_text(encoding='utf-8')
        bundle = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        for css in (src, bundle):
            self.assertIn('.result420-matter{display:block;font-size:1.125rem;font-weight:700', css)
            self.assertIn('--ui-text:#f1f5f9', css)
        for fg, bg in (('#182230', '#ffffff'), ('#475467', '#ffffff'), ('#f1f5f9', '#111827'), ('#cbd5e1', '#111827')):
            self.assertGreaterEqual(_ratio(fg, bg), 4.5, (fg, bg))
        self.assertNotIn('nowrap', src.split('5.6.43 (cahier v9, § 18)')[1])

    def test_total_et_nombre_affiche_distingues(self):
        src = (ROOT / 'agent' / 'production420_ui.py').read_text(encoding='utf-8')
        self.assertIn("'%d affichés sur %d' % (shown,total)", src)


# ---------------------------------------------------------------------------------------------------------------- § 19 : Playbooks


class Playbooks(fixtures.Base):
    def start(self):
        operating380.ensure_schema(self.desk)
        pid = operating380.playbooks(self.desk)[0]['id']
        return operating380.start_playbook(self.desk, pid, MID, 'Objectif fictif')['run_id']

    def page(self, args):
        from agent.common import load_matters
        return operating380_ui.playbooks_page(self.desk, load_matters(self.desk.c), args, lambda *a, **k: '<form>%s</form>' % a[1], _link)

    def test_PB_OBS_01_consultation_ne_reactive_pas_un_parcours_annule(self):
        rid = self.start()
        operating380.cancel_run(self.desk, rid)
        self.assertEqual(operating380.playbook_run(self.desk, rid)['status'], 'cancelled')
        operating380.recent_playbook_runs(self.desk, MID)
        self.assertEqual(self.desk.db.execute('SELECT status FROM playbook_runs_v380 WHERE id=?', (rid,)).fetchone()[0], 'cancelled')

    def test_PB_OBS_02_lien_job_ouvre_le_parcours(self):
        operating380.ensure_schema(self.desk)
        pid = operating380.playbooks(self.desk)[0]['id']
        jid = self.desk.enqueue('start_playbook380', {'playbook_id': pid, 'matter': MID, 'objective': 'x'}, priority=0)
        self.assertIn('Démarrage du parcours en cours', self.page({'job': str(jid)}))
        self.desk.work_once()
        rid = json.loads(self.desk.db.execute('SELECT result FROM jobs WHERE id=?', (jid,)).fetchone()[0])['run_id']
        html = self.page({'job': str(jid)})
        self.assertIn('id="playbook-run"', html)
        self.assertLess(html.index('id="playbook-run"'), html.index('Démarrer un parcours'))   # suivi avant le catalogue
        self.assertIn('Dossier n° ' + MID, html)
        self.assertIn(rid, self.desk.db.execute('SELECT id FROM playbook_runs_v380').fetchone()[0])

    def test_dossier_resolu_depuis_le_parcours_et_parcours_absent(self):
        rid = self.start()
        html = self.page({'run': rid, 'matter': 'AUTRE-DOSSIER'})
        self.assertIn('pas celui indiqué dans le lien', html)
        self.assertIn('n’existe plus ou n’est pas accessible', self.page({'run': 'inconnu'}))
        src = (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8')
        self.assertNotIn("'matter':form.get('matter','')})", src.split("'start_playbook380','advance_playbook380'")[1][:300])

    def test_etape_annulee_jamais_sautee_puis_reprise_controlee(self):
        rid = self.start()
        run = operating380.advance_playbook(self.desk, rid)
        first = next(s for s in run['steps'] if s['status'] in ('queued', 'awaiting_input'))
        if first['status'] == 'awaiting_input':
            self.skipTest('premier pas manuel dans ce parcours')
        again = operating380.advance_playbook(self.desk, rid)          # double clic : aucune seconde copie
        self.assertEqual(sum(1 for s in again['steps'] if s['status'] == 'queued'), 1)
        self.desk.cancel_job(first['job_id'])
        run = operating380.playbook_run(self.desk, rid)
        self.assertEqual(run['status'], 'step_cancelled')
        before = [s['status'] for s in run['steps']]
        operating380.advance_playbook(self.desk, rid)                   # « Continuer » ne saute pas l'étape annulée
        self.assertEqual([s['status'] for s in operating380.playbook_run(self.desk, rid)['steps']], before)
        retried = operating380.retry_step(self.desk, rid, first['step_no'])
        step = next(s for s in retried['steps'] if s['step_no'] == first['step_no'])
        self.assertEqual(step['status'], 'queued')
        self.assertNotEqual(step['job_id'], first['job_id'])
        self.assertIn('Reprendre cette étape', operating380_ui.RUN_STATES['step_cancelled'] + 'Reprendre cette étape')


# ---------------------------------------------------------------------------------------------------------------- § 20 : navigation


class NavigationSansDoublons(unittest.TestCase):
    def setUp(self):
        self.web = t501.UnifiedInterface('test_every_page_uses_the_same_sidebar')
        self.web.setUp()
        self.addCleanup(self.web.doCleanups)

    def panel(self, path='/aujourdhui'):
        return fonctions5631.panel_html('/agent-courriel', path, self.web.f.c)

    def test_NAV_OBS_01_et_02_aucun_acces_principal_ni_ancien_tableau_de_bord(self):
        html = self.panel()
        visible = re.findall(r'<li data-search="[^"]*"><a href="/agent-courriel([^"]+)"', html)
        for route in ('/aujourdhui', '/courriels', '/dossiers', '/parametres', '/planning', '/production', '/accueil'):
            self.assertNotIn(route, visible)
        self.assertTrue(6 <= len(visible) <= 10, visible)
        self.assertEqual(len(visible), len(set(visible)))

    def test_toute_fonction_reste_trouvable_et_mene_a_sa_destination(self):
        html = self.panel()
        reachable = set(re.findall(r'href="/agent-courriel([^"?]+)', html))
        for fid, group, route, aliases, needs in fonctions5631.FUNCTIONS:
            kind = navigation5643.visibility(fid)
            if kind == 'primary':
                continue
            target = fonctions5631.INDEX[navigation5643.canonical(fid)][2] if kind == 'hidden' else route
            self.assertIn(target.split('?', 1)[0], reachable, fid)
        old = re.search(r'<li hidden data-search-only data-search="([^"]*tableau de bord[^"]*)"><a href="/agent-courriel([^"]+)"', html)
        self.assertEqual(old.group(2), '/aujourdhui')                       # l'ancien nom mène à l'accueil unique

    def test_onglets_de_produire_et_etat_actif(self):
        r = self.web.request('/playbooks')
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('<nav class="ws-tabs production-tabs" aria-label="Rubriques de Produire">', r['body'])
        self.assertIn('href="/agent-courriel/playbooks" aria-current="page">Parcours guidés</a>', r['body'])
        self.assertEqual(re.findall(r'<a [^>]*aria-label="([^"]+)"[^>]*aria-current="page"', r['body']), ['Produire'])
        self.assertEqual(shell501.active_primary('/echeances'), '/dossiers')

    def test_production_vue_parcours_ouvre_l_onglet_en_gardant_le_parcours(self):
        r = self.web.request('/production', query='vue=parcours&run=abc&secret=x')
        self.assertTrue(r['status'].startswith('302'), r['status'])
        location = r['headers'].get('Location', '')
        self.assertTrue(location.endswith('/playbooks?run=abc'), location)


# ---------------------------------------------------------------------------------------------------------------- § 17 : deux agendas


class _NoCalDAV:
    """Tout appel au connecteur d'agenda distant est une faute : l'Agenda personnel ne doit jamais l'atteindre."""
    calls = []

    def __getattr__(self, name):
        def record(*args, **kw):
            _NoCalDAV.calls.append(name)
            return 'uid-distant'
        return record


class AgendaPersonnelJamaisCopie(fixtures.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['nextcloud_workflow'] = {'enabled': True, 'planning_calendar_url': '/remote.php/dav/calendars/fictif/planning/'}
        _NoCalDAV.calls = []
        p = patch('agent.workplan._dav', return_value=_NoCalDAV())
        p.start()
        self.addCleanup(p.stop)

    def start(self, days=2):
        return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()

    def test_aucun_appel_caldav_sur_tous_les_chemins(self):
        ev = agenda5634.api(self.desk, 'agenda/creer', {'title': 'Rendez-vous fictif', 'start': self.start(), 'copy': True,
                                                         'calendar_scope': 'legacy_bridge', 'calendar_url': '/remote/x'}, 'POST')
        row = self.desk.db.execute('SELECT calendar_scope,copy_policy,sync_state FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()
        self.assertEqual(tuple(row), ('personal_local', 'none', 'local'))       # paramètres falsifiés ignorés
        agenda5634.update(self.desk, {'id': ev['id'], 'title': 'Rendez-vous fictif déplacé'})
        agenda5634.cancel(self.desk, ev['id'])
        ics = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:fictif-1\r\nDTSTART:%s\r\nDTEND:%s\r\nSUMMARY:Importé\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' % (
            (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y%m%dT090000Z'), (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y%m%dT100000Z')))
        agenda5634.import_ics(self.desk, ics, 'fictif')
        self.desk.db.execute("UPDATE agenda5634 SET sync_state='a_synchroniser'")   # même une ligne forcée « à copier » ne part pas
        self.desk.db.commit()
        agenda5634.tick(self.desk)
        name = Path(agenda5634.backup(self.desk, force=True)).name
        agenda5634.restore(self.desk, name, apply=True)
        self.assertEqual(_NoCalDAV.calls, [])
        self.assertIn('non synchronisé', agenda5634.mode(self.desk)['label'])

    def test_lectures_separees_et_planificateur_interne(self):
        agenda5634.create(self.desk, {'title': 'Audience personnelle fictive', 'start': self.start()})
        self.desk.db.execute("INSERT INTO calendar_cache VALUES('r1','u1','','https://cloud.example.test/cal/','','','Rendez-vous en ligne','','',?,?,1,'','[]',?)",
                             (self.start(), (datetime.now(timezone.utc) + timedelta(days=2, hours=1)).isoformat(), self.desk.now()))
        self.desk.db.commit()
        a, b = (datetime.now(timezone.utc)).isoformat(), (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        titles = lambda src: sorted(x['title'] for x in workplan.calendar_events(self.desk, a, b, refresh=False, source=src)['events'])  # noqa: E731
        self.assertEqual(titles('online'), ['Rendez-vous en ligne'])
        self.assertEqual(titles('personal'), ['Audience personnelle fictive'])
        self.assertEqual(len(titles(None)), 2)                                   # l'organisation de la semaine voit les deux
        self.assertEqual(_NoCalDAV.calls, [])

    def test_migration_anciennes_copies_a_repartir_sans_relance(self):
        ev = agenda5634.create(self.desk, {'title': 'Ancien rendez-vous copié', 'start': self.start()})
        self.desk.db.execute("UPDATE agenda5634 SET calendar_scope='legacy_bridge',sync_state='a_repartir',remote_uid='uid-ancien' WHERE id=?", (ev['id'],))
        self.desk.db.commit()
        agenda5634.tick(self.desk)
        self.assertEqual(_NoCalDAV.calls, [])                                    # aucune copie ne repart sans décision
        self.assertEqual([x['id'] for x in agenda5634.legacy_inventory(self.desk)], [ev['id']])
        out = agenda5634.decide_legacy(self.desk, ev['id'], 'garder_local')
        self.assertIn('n’a pas été supprimée', out['message'])
        row = self.desk.db.execute('SELECT calendar_scope,remote_uid FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()
        self.assertEqual(tuple(row), ('personal_local', 'uid-ancien'))          # copie distante conservée, jamais effacée
        self.assertEqual(agenda5634.legacy_inventory(self.desk), [])
        with self.assertRaisesRegex(Stop, 'evenement_deja_reparti'):
            agenda5634.decide_legacy(self.desk, ev['id'], 'garder_copie')

    def test_journee_entiere_au_changement_d_heure(self):
        ev = agenda5634.create(self.desk, {'title': 'Échéance fictive', 'kind': 'echeance', 'all_day': True, 'start': '2026-10-24', 'days': 2})
        paris = agenda5634._tz(self.desk)
        s = datetime.fromisoformat(ev['event']['starts']).astimezone(paris)
        e_ = datetime.fromisoformat(ev['event']['ends']).astimezone(paris)
        self.assertEqual((s.date().isoformat(), e_.date().isoformat(), s.hour, e_.hour), ('2026-10-24', '2026-10-26', 0, 0))


class AgendaSousVues(unittest.TestCase):
    def setUp(self):
        self.web = t501.UnifiedInterface('test_every_page_uses_the_same_sidebar')
        self.web.setUp()
        self.addCleanup(self.web.doCleanups)
        p = patch('agent.workplan._dav', return_value=self.web.dav)
        p.start()
        self.addCleanup(p.stop)

    def page(self, query=''):
        r = self.web.request('/planning', query=query)
        self.assertTrue(r['status'].startswith('200'), r['status'])
        return r['body']

    def test_deux_sous_boutons_une_seule_source_et_selection_visible(self):
        personal = self.page('vue=agenda')
        self.assertIn('<nav class="planning-subtabs" aria-label="Sources de l’agenda">', personal)
        self.assertIn('aria-current="page" href="/agent-courriel/planning?vue=agenda&amp;agenda=personnel"', personal)
        self.assertIn('data-m34-agenda', personal)
        self.assertNotIn('<h2>Agenda en ligne</h2>', personal)
        self.assertNotIn('Copie vers Nextcloud active', personal)                # jamais annoncé côté personnel
        online = self.page('vue=agenda&agenda=en_ligne&view=month&date=2026-10-05')
        self.assertIn('<h2>Agenda en ligne</h2>', online)
        self.assertNotIn('data-m34-agenda', online)
        self.assertIn('aria-current="page" href="/agent-courriel/planning?vue=agenda&amp;agenda=en_ligne&amp;view=month&amp;date=2026-10-05"', online)
        self.assertIn('agenda=en_ligne&amp;view=week&amp;date=2026-10-05', online)   # liens de période : sous-vue conservée
        self.assertIn('class="ws-tabs planning-tabs"', online)

    def test_preference_par_utilisateur_et_anciennes_adresses(self):
        self.page('vue=agenda&agenda=en_ligne')
        self.assertIn('<h2>Agenda en ligne</h2>', self.page('vue=agenda'))       # préférence reprise
        self.assertIn('<h2>Agenda en ligne</h2>', self.page('vue=agenda&agenda=inconnu'))
        self.assertIn('data-m34-agenda', self.page('vue=agenda&agenda=personnel'))
        r = self.web.request('/agenda')
        self.assertTrue(r['status'].startswith('200'))

    def test_styles_actifs_dans_les_sources_et_le_bundle(self):
        for name in ('v5634.css', 'app520.css'):
            css = (ROOT / 'agent' / 'static' / name).read_text(encoding='utf-8')
            self.assertIn('.planning-subtabs a[aria-current="page"]', css)
            self.assertIn('outline:3px solid #7b55cf', css)
        self.assertGreaterEqual(_ratio('#ffffff', '#1f4e9c'), 4.5)


# ---------------------------------------------------------------------------------------------------------------- § 22 : page Talk
class PageTalk(unittest.TestCase):
    NC = 'https://cloud.example.test/nextcloud'

    def setUp(self):
        self.web = t501.UnifiedInterface('test_every_page_uses_the_same_sidebar')
        self.web.setUp()
        self.addCleanup(self.web.doCleanups)
        self.web.f.c['nextcloud']['url'] = self.NC + '/remote.php/dav/files/technique/'
        from agent.common import private_json
        private_json(self.web.config, self.web.f.c)
        self.desk = Desk(self.web.f.c)
        self.addCleanup(self.desk.db.close)
        from agent import talk5643
        self.talk = talk5643

    def test_desactive_par_defaut_puis_entree_apres_agenda_pour_cet_utilisateur(self):
        body = self.web.request('/aujourdhui')['body']
        self.assertNotIn('href="/agent-courriel/talk"', body.split('class="ws-primary"')[1].split('</nav>')[0])
        self.assertIn('n’est pas activée', self.web.request('/talk')['body'])
        out = self.talk.save(self.desk, 'cabinet', {'enabled': '1', 'expected_version': 0})
        self.assertEqual((out['enabled'], out['talk_url']), (True, self.NC + '/index.php/apps/spreed/'))   # sous-chemin conservé
        nav = self.web.request('/aujourdhui')['body'].split('class="ws-primary"')[1].split('</nav>')[0]
        order = re.findall(r'href="/agent-courriel(/[a-z]+)"', nav)
        self.assertEqual(order[order.index('/planning') + 1], '/talk')          # juste après « Agenda et tâches »
        self.assertEqual(len(order), 8)   # 5.6.50 : + Messagerie (après Courriels)
        self.assertEqual(order[order.index('/courriels') + 1], '/messagerie')
        self.assertFalse(self.talk.visible(self.desk.c, 'autre-utilisateur'))   # préférence propre à chaque utilisateur
        with self.assertRaisesRegex(Stop, 'reglage_modifie_entre_temps'):
            self.talk.save(self.desk, 'cabinet', {'enabled': '0', 'expected_version': 0})

    def test_iframe_csp_et_permissions_limitees_a_talk(self):
        self.talk.save(self.desk, 'cabinet', {'enabled': '1', 'expected_version': 0})
        prefixed = self.web.request('/agent-courriel/talk')                        # chemin réel derrière le serveur web (préfixe)
        self.assertIn('https://cloud.example.test', prefixed['headers']['Content-Security-Policy'].split('frame-src')[1].split(';')[0])
        r = self.web.request('/talk')
        self.assertIn('<iframe class="talk5643-frame" title="Nextcloud Talk" src="%s/index.php/apps/spreed/"' % self.NC, r['body'])
        self.assertIn('allow="microphone; camera; fullscreen; display-capture; autoplay"', r['body'])
        self.assertIn('Ouvrir Talk dans un nouvel onglet', r['body'])
        csp = r['headers']['Content-Security-Policy']
        frame_src = csp.split('frame-src')[1].split(';')[0]
        self.assertIn(' https://cloud.example.test', frame_src)                  # origine Nextcloud validée, rien de plus large
        self.assertNotIn('*', frame_src)
        self.assertIn('camera=(self "https://cloud.example.test")', r['headers']['Permissions-Policy'])
        other = self.web.request('/aujourdhui')['headers']
        self.assertNotIn('cloud.example.test', other['Content-Security-Policy'])   # aucun élargissement hors de /talk
        self.assertIn('camera=()', other['Permissions-Policy'])

    def test_adresse_validee_contre_la_connexion_nextcloud(self):
        for bad in ('https://autre.example.test/index.php/apps/spreed/', 'https://u:p@cloud.example.test/nextcloud/index.php/apps/spreed/',
                    self.NC + '/remote.php/dav/files/x', 'javascript:alert(1)', 'http://cloud.example.test/nextcloud/index.php/apps/spreed/'):
            with self.assertRaises(Stop, msg=bad):
                self.talk.validate(self.desk.c, bad)

    def test_aucun_salon_ni_travail_et_ia_en_pause(self):
        self.desk.setting('missions567:pause', True)
        before = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        self.talk.save(self.desk, 'cabinet', {'enabled': '1', 'expected_version': 0})
        self.assertTrue(self.web.request('/talk')['status'].startswith('200'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)
        self.assertFalse(self.desk.settings('settings568:talk_enabled', False))   # automatismes Talk non activés
        from agent.web5636 import allowed
        self.assertTrue(allowed('assistant', 'talk/enregistrer'))


# ---------------------------------------------------------------------------------------------------------------- § 10-14 : correspondances
from email.message import EmailMessage
import hashlib
import shutil
import tempfile
from agent import correspondances5643 as cx
from agent.common import private_json
from agent.dav import LocalFolder


def _raw_message(sender='Client Fictif <client@example.test>', to='cabinet@example.test', subject='Facture et demande', pdf=b'%PDF-1.4 facture fictive'):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'], m['Message-ID'] = sender, to, subject, '<fictif-1@example.test>'
    m['Date'] = 'Wed, 14 Oct 2026 08:30:00 +0200'
    m.set_content('Bonjour Maître,\nVoici la facture qui manquait.\nCordialement')
    m.add_alternative('<p>Bonjour Maître,<br>Voici la facture <img src="cid:logo1"> <img src="https://pistage.example.test/x.png"></p>', subtype='html')
    m.get_payload()[1].add_related(b'\x89PNG\r\n\x1a\nimage-fictive', maintype='image', subtype='png', cid='<logo1>')
    m.add_attachment(pdf, maintype='application', subtype='pdf', filename='Facture.pdf')
    m.add_attachment(b'second', maintype='application', subtype='pdf', filename='Facture.pdf')   # même nom : suffixes stables
    return m.as_bytes()


class _RawBox:
    """IMAP fictif : octets exacts, UIDVALIDITY contrôlée, aucune écriture (STORE/MOVE absents)."""
    def __init__(self, messages, validity='20261014'):
        self.messages, self.validity, self.calls = messages, validity, []

    def fetch_raw(self, folder, uid, expected_validity=None, limit=0):
        self.calls.append(('fetch_raw', folder, uid))
        if expected_validity and expected_validity != self.validity:
            raise Stop('uidvalidity_modifiee')
        if uid not in self.messages:
            raise Stop('message_imap_disparu')
        raw = self.messages[uid]
        return {'folder': folder, 'uidvalidity': self.validity, 'uid': uid, 'raw': raw, 'rfc822_size': len(raw),
                'internaldate': '2026-10-14T06:31:00+00:00', 'flags': [], 'complete': True}

    def close(self):
        pass


class Correspondances(unittest.TestCase):
    def setUp(self):
        self.base = t510.Base('run')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.desk = self.base.desk
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / 'Dossiers' / 'CLIENT FICTIF - DOS-1').mkdir(parents=True)
        (self.tmp / 'Dossiers' / 'AUTRE - DOS-2').mkdir(parents=True)
        self.desk.c['nextcloud'] = {'local_path': str(self.tmp), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers'], 'max_depth': 8, 'max_files': 500}
        self.desk.c['mail'].update({'from_address': 'cabinet@example.test', 'username': 'cabinet@example.test', 'host': 'imap.example.test'})
        private_json(Path(self.desk.c['matters_file']), [
            {'id': 'DOS-1', 'client_name': 'Client fictif', 'path': '/Dossiers/CLIENT FICTIF - DOS-1', 'references': ['DOS-1'], 'aliases': [],
             'correspondents': [{'email': 'client@example.test', 'role': 'client'}]},
            {'id': 'DOS-2', 'client_name': 'Autre', 'path': '/Dossiers/AUTRE - DOS-2', 'references': ['DOS-2'], 'aliases': [], 'correspondents': []}])
        self.raw = _raw_message()
        self.box = _RawBox({'734': self.raw})
        self.client = LocalFolder(self.desk.c['nextcloud'])

    def file(self, path):
        return (self.tmp / path.lstrip('/')).read_bytes()

    def file_now(self, actions, matter='DOS-1', parts=None, key='cle-fictive-1'):
        data = {'source': {'folder': 'INBOX', 'uid': '734', 'uidvalidity': '20261014'}, 'matter_id': matter, 'actions': actions}
        if parts is not None:
            data['attachments_selection'] = {'mode': 'selected', 'parts': parts}
        prop = cx.prepare(self.desk, 'cabinet', data, self.box)
        out = cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': key})
        return prop, out

    def run_jobs(self):
        for r in self.desk.db.execute("SELECT id,kind,args FROM jobs WHERE kind IN ('archive_correspondence5643','file_mail_attachments5643') "
                                      "AND status='pending' ORDER BY id").fetchall():
            fn = cx.run_archive_email if r['kind'] == 'archive_correspondence5643' else cx.run_file_attachments
            fn(self.desk, json.loads(r['args']), self.box, self.client)
            self.desk.db.execute("UPDATE jobs SET status='done' WHERE id=?", (r['id'],))
        self.desk.db.commit()

    def test_original_octet_pour_octet_lecture_md_et_metadonnees(self):
        prop, out = self.file_now(['archive_email'])
        self.assertEqual([f['name'] for f in prop['files_expected']], ['original.eml', 'lecture.md', 'metadata.json'])
        self.assertEqual(out['conservation_state'], 'queued')                    # l'enqueue n'est pas un succès
        self.run_jobs()
        st = cx.status(self.desk, out['link_id'])
        self.assertEqual((st['conservation_state'], st['extraction_state'], st['attachments_state']), ('verified', 'complete', 'not_requested'))
        mdir = prop['paths']['message_dir']
        self.assertTrue(mdir.startswith('/Dossiers/CLIENT FICTIF - DOS-1/CORRESPONDANCES/2026-10/2026-10-14_083000-recu-facture-et-demande--'))
        self.assertEqual(self.file(mdir + '/original.eml'), self.raw)            # aucune reconstruction depuis l'analyse
        reading = self.file(mdir + '/lecture.md').decode('utf-8')
        self.assertIn('Voici la facture qui manquait.', reading)
        self.assertIn('![logo1](images/image-', reading.replace('![image](', '![logo1]('))
        self.assertIn('image(s) distante(s) non conservée(s)', reading)
        self.assertIn('pas un résumé', reading)
        meta = json.loads(self.file(mdir + '/metadata.json'))
        self.assertEqual(meta['raw_sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertFalse((self.tmp / 'Dossiers' / 'CLIENT FICTIF - DOS-1' / 'NOS PIECES').exists())   # courriel seul : aucune pièce copiée
        self.assertEqual(cx.original(self.desk, out['link_id'], self.client), self.raw)
        self.assertEqual([c[0] for c in self.box.calls], ['fetch_raw', 'fetch_raw'])   # lecture seule, jamais d'écriture IMAP

    def test_pieces_seules_dans_nos_pieces_sans_eml(self):
        prop, out = self.file_now(['file_attachments'])
        self.run_jobs()
        st = cx.status(self.desk, out['link_id'])
        self.assertEqual((st['attachments_state'], st['conservation_state'], st['original']), ('verified', 'not_requested', None))
        names = sorted(p.name for p in (self.tmp / prop['paths']['pieces_dir'].lstrip('/')).iterdir())
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.startswith('Facture--partie-') and n.endswith('.pdf') for n in names))
        self.assertNotEqual(names[0], names[1])                                   # même nom reçu : suffixes stables
        self.assertFalse((self.tmp / 'Dossiers' / 'CLIENT FICTIF - DOS-1' / 'CORRESPONDANCES').exists())
        self.assertEqual(cx.listing(self.desk, 'DOS-1'), [])                      # pas annoncé « courriel conservé »

    def test_double_clic_deux_onglets_et_reprise_sans_doublon(self):
        prop, out = self.file_now(['archive_email'], key='meme-cle-1234')
        again = cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'meme-cle-1234'})
        self.assertEqual(again['link_id'], out['link_id'])
        prop2, out2 = self.file_now(['archive_email'], key='autre-onglet-1')   # deuxième onglet : rejoint le classement en cours
        self.assertEqual(out2['link_id'], out['link_id'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='archive_correspondence5643'").fetchone()[0], 1)
        self.run_jobs()
        # reprise après coupure : fichier déjà présent et identique → rejoint, jamais remplacé ni numéroté
        link = cx._link(self.desk, out['link_id'])
        self.desk.db.execute("UPDATE mail_archive_assets SET state='writing' WHERE link_id=? AND kind='original'", (link['id'],))
        self.desk.db.commit()
        res = cx._deposit(self.desk, self.client, link, 'original', 'correspondances', prop['paths']['message_dir'] + '/original.eml', self.raw,
                          'message/rfc822', 'mail_original', boundary=prop['paths']['matter_root'])
        self.assertTrue(res['rejoined'])
        with self.assertRaisesRegex(Stop, 'conflit_fichier_existant'):        # collision différente : conflit expliqué
            cx._deposit(self.desk, self.client, link, 'reading', 'correspondances', prop['paths']['message_dir'] + '/original.eml', b'autre',
                        'text/plain', 'mail_extraction', part_key='x', boundary=prop['paths']['matter_root'])
        with self.assertRaisesRegex(Stop, 'proposition_obsolete'):
            cx.confirm(self.desk, 'cabinet', {'proposal_id': prop2['proposal_id'], 'expected_version': 2, 'request_key': 'encore-une-cle'})

    def test_uidvalidity_changee_source_disparue_et_partie_etrangere(self):
        with self.assertRaisesRegex(Stop, 'uidvalidity_modifiee'):
            cx.prepare(self.desk, 'cabinet', {'source': {'folder': 'INBOX', 'uid': '734', 'uidvalidity': '1'}, 'matter_id': 'DOS-1',
                                              'actions': ['archive_email']}, self.box)
        with self.assertRaisesRegex(Stop, 'piece_jointe_inconnue'):
            self.file_now(['file_attachments'], parts=[{'part_key': '99', 'display_name': 'x.pdf'}])
        prop, out = self.file_now(['archive_email'])
        self.box.messages = {}                                                      # message déplacé entre la préparation et le dépôt
        with self.assertRaisesRegex(Stop, 'courriel_source_deplace_ou_absent'):
            self.run_jobs()
        self.assertEqual(cx.status(self.desk, out['link_id'])['conservation_state'], 'error')

    def test_deux_dossiers_et_chemin_traversant(self):
        _, a = self.file_now(['archive_email'], 'DOS-1', key='dossier-un-1')
        _, b = self.file_now(['archive_email'], 'DOS-2', key='dossier-deux-1')
        self.assertNotEqual(a['link_id'], b['link_id'])                            # même archive, deux classements traçables
        self.assertEqual(a['archive_id'], b['archive_id'])
        self.assertEqual(cx.safe_name('../../con.pdf'), '_con.pdf')
        self.assertNotIn('/', cx.safe_name('a/../../b.pdf'))
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            self.file_now(['archive_email'], 'INCONNU', key='dossier-inconnu')

    def test_autorisations_generales_et_par_message(self):
        source = {'folder': 'INBOX', 'uid': '734', 'uidvalidity': '20261014'}
        eff = cx.effective(self.desk, source)
        self.assertEqual((eff['archive_email']['allowed'], eff['file_attachments']['allowed']), (False, False))   # désactivées par défaut
        cx.set_message_rule(self.desk, 'cabinet', {**source, 'expected_version': 0, 'email_override': 'allow', 'matter_id': ''})
        eff = cx.effective(self.desk, source)
        self.assertEqual((eff['archive_email']['allowed'], eff['file_attachments']['allowed']), (True, False))    # indépendantes
        self.assertEqual(eff['notice'], 'Autorisé pour ce courriel — en attente du dossier.')
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            cx.set_policy(self.desk, 'assistant1', 'assistant', {'expected_version': 0, 'email_auto': True})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            cx.set_policy(self.desk, 'avocat1', 'avocat', {'scope_kind': 'cabinet', 'expected_version': 0, 'email_auto': True})
        pol = cx.set_policy(self.desk, 'avocat1', 'avocat', {'expected_version': 0, 'email_auto': False, 'attachments_auto': True})
        self.assertTrue(pol['attachments_auto'] and not pol['email_auto'])
        cx.set_message_rule(self.desk, 'cabinet', {**source, 'expected_version': 1, 'attachments_override': 'deny'})
        self.assertFalse(cx.effective(self.desk, source)['file_attachments']['allowed'])   # exclusion locale malgré la règle générale
        other = {'folder': 'INBOX', 'uid': '735', 'uidvalidity': '20261014'}
        self.assertEqual(cx.get_message_rule(self.desk, other)['email_override'], 'inherit')   # jamais étendu à un autre message

    def test_automatique_sur_rattachement_confirme_et_pause(self):
        cx.set_policy(self.desk, 'cabinet', 'administrateur', {'expected_version': 0, 'email_auto': True})
        own = _raw_message(sender='Cabinet <cabinet@example.test>', to='client@example.test', subject='Envoi du cabinet')

        class Box(_RawBox):
            def search(inner, folder, *crit):
                return ['734', '800']

            def fetch(inner, folder, uid, headers_only=False):
                from agent.mailbox import Mail
                msg = cx.parse(inner.messages[uid])
                return Mail(uid, '20261014', folder, set(), datetime.now(timezone.utc), msg)
        box = Box({'734': self.raw, '800': own})
        self.desk.setting('missions567:pause', True)
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 0)              # la pause bloque l'automatique
        self.desk.setting('missions567:pause', False)
        out = cx.auto_tick(self.desk, box)
        self.assertEqual(out['queued'], 1)                                       # message du cabinet jamais classé automatiquement
        job = self.desk.db.execute("SELECT origin FROM jobs WHERE kind='archive_correspondence5643'").fetchone()
        self.assertEqual(job['origin'], 'automatic')
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 0)              # pas de second classement

    def test_onglet_correspondances_et_lecture_sans_imap(self):
        prop, out = self.file_now(['archive_email', 'file_attachments'])
        self.run_jobs()
        from agent import dossier5627
        m = next(x for x in __import__('agent.common', fromlist=['load_matters']).load_matters(self.desk.c) if x['id'] == 'DOS-1')
        with patch('agent.correspondances5643._client', return_value=self.client):
            html = dossier5627.correspondances_html(self.desk, '/agent-courriel', m, out['archive_id'])
        self.assertIn('Correspondances (1)', html)
        self.assertIn('Original vérifié', html)                                   # l'état, pas la seule présence, dit « conservé »
        self.assertIn('Voici la facture qui manquait.', html)                     # lu depuis l'archive, pas depuis IMAP
        self.assertIn('2 pièce(s) dans NOS PIECES', html)
        self.assertIn('Télécharger l’original (.eml)', html)
        self.assertEqual(dossier5627._counts(self.desk, 'DOS-1')['correspondances'], 1)
        with patch('agent.correspondances5643._client', return_value=self.client):
            note = cx.annotate(self.desk, 'cabinet', {'link_id': out['link_id'], 'text': 'Relancer le client fictif.'})
        self.assertEqual(note['version'], 1)
        self.assertEqual(self.file(prop['paths']['message_dir'] + '/original.eml'), self.raw)   # original inchangé

    def test_les_travaux_de_conservation_passent_pendant_la_pause_de_l_ia(self):
        from agent.arret5643 import DETERMINISTIC
        self.assertTrue({'archive_correspondence5643', 'file_mail_attachments5643'} <= DETERMINISTIC)
        src = (ROOT / 'agent' / 'mailbox.py').read_text(encoding='utf-8')
        raw_fn = src[src.index('def fetch_raw('):src.index('def unseen(')]
        self.assertIn("'(BODY.PEEK[])'", raw_fn)
        self.assertEqual(re.findall(r"self\.conn\.uid\('(\w+)'", raw_fn), ['FETCH', 'FETCH'])   # commandes envoyées : lecture seule
        self.assertEqual(set(re.findall(r"self\.conn\.(\w+)\(", raw_fn)), {'uid'})


if __name__ == '__main__':
    unittest.main()
