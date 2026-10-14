"""5.6.44 : contre-tests de l'audit 5.6.43 (F01 à F10), démarrage tolérant au verrou de la base, test d'affichage de Talk.
Les contre-tests F01 à F10 reprennent ceux de l'audit (fichier contre_tests_5643.py), sans services externes ni dossier réel.
"""

import json
import socket
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_v5643 as fixtures
from agent import correspondances5643 as cx, arret5643, pause5629
from agent.common import HTTP, Stop
from agent.mailbox import Mail


class Box(fixtures._RawBox):
    def __init__(self, messages, timestamp=None):
        super().__init__(messages)
        self.timestamp = timestamp or datetime.now(timezone.utc) + timedelta(minutes=1)

    def search(self, folder, *criteria):
        return sorted(self.messages, key=int)

    def fetch(self, folder, uid, headers_only=False):
        return Mail(uid, self.validity, folder, set(), self.timestamp, cx.parse(self.messages[uid]))


class Regressions(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Correspondances('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = self.f.desk

    def policy(self, email=True, attachments=False):
        old = cx.get_policy(self.desk)
        return cx.set_policy(self.desk, 'cabinet', 'administrateur',
                             {'expected_version': old['version'], 'email_auto': email, 'attachments_auto': attachments})

    def automatic_proposal(self):
        prop = cx.prepare(self.desk, 'cabinet', {'source': {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity},
                          'matter_id': 'DOS-1', 'actions': ['archive_email']}, self.f.box, origin='automatic')
        out = cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'audit-auto-734'})
        return prop, out

    def test_F02_mail_sans_piece_reste_archive_si_deux_regles_actives(self):
        self.policy(True, True)
        raw = b'From: client@example.test\r\nTo: cabinet@example.test\r\nSubject: Bonjour\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nMessage sans piece.\r\n'
        result = cx.auto_tick(self.desk, Box({'734': raw}))
        self.assertEqual(result['queued'], 1, 'Le manque de PJ ne doit pas bloquer la conservation EML.')

    def test_F03_file_automatique_ne_classe_pas_apres_pause_dossier(self):
        self.policy()
        _, out = self.automatic_proposal()
        pause5629.set_pause(self.desk, 'DOS-1', True)
        job = self.desk.db.execute('SELECT status,matter_ref FROM jobs WHERE id=?', (out['jobs'][0]['job_id'],)).fetchone()
        self.assertEqual(job['status'], 'suspended', 'Le job doit être rattaché au dossier et suspendu.')

    def test_F04_retrait_autorisation_recontrole_avant_depot(self):
        self.policy()
        _, out = self.automatic_proposal()
        self.policy(False, False)
        with patch('agent.correspondances5643._box', return_value=self.f.box), patch('agent.correspondances5643._client', return_value=self.f.client):
            self.desk.work_once()
        originals = self.desk.db.execute("SELECT COUNT(*) FROM mail_archive_assets WHERE link_id=? AND kind='original' AND state='verified'", (out['link_id'],)).fetchone()[0]
        self.assertEqual(originals, 0, 'L’autorisation retirée ne doit pas autoriser un dépôt encore non engagé.')

    def test_F05_plus_de_30_messages_progressent(self):
        self.policy()
        messages = {str(i): self.f.raw.replace(b'Facture', ('Facture %s' % i).encode()) + ('\r\nNumero %s' % i).encode() for i in range(1, 36)}
        box = Box(messages)
        first = cx.auto_tick(self.desk, box)
        second = cx.auto_tick(self.desk, box)
        self.assertEqual(first['queued'] + second['queued'], 35, 'Les cinq messages plus anciens ne doivent pas rester exclus à chaque passage.')

    def test_F06_mandat_pendant_pause_sur_ancien_mail_est_repris(self):
        self.desk.setting('missions567:pause', True)
        result = cx.entrust(self.desk, 'cabinet', {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity,
                           'matter_id': 'DOS-1', 'actions': ['archive_email']}, self.f.box)
        self.assertEqual(result['state'], 'en_attente_reprise')
        self.desk.setting('missions567:pause', False)
        result = cx.auto_tick(self.desk, Box({'734': self.f.raw}, datetime.now(timezone.utc) - timedelta(days=1)))
        self.assertEqual(result['queued'], 1, 'Un mandat explicite ancien ne doit pas passer sous la borne des nouveaux courriels.')

    def test_F07_ancien_job_annule_reclassement_recree_un_job(self):
        _, out = self.f.file_now(['archive_email'])
        self.desk.cancel_job(out['jobs'][0]['job_id'])
        prop = cx.prepare(self.desk, 'cabinet', {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity,
                          'matter_id': 'DOS-1', 'actions': ['archive_email']}, self.f.box)
        again = cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'audit-retry-734'})
        pending = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='archive_correspondence5643' AND status='pending'").fetchone()[0]
        self.assertEqual(pending, 1, 'L’état queued du lien ne doit pas bloquer une nouvelle commande après annulation du job.')

    def test_F08_classer_le_mail_puis_ses_pieces_ne_cree_pas_conflit_manifest(self):
        _, out = self.f.file_now(['archive_email'])
        self.f.run_jobs()
        self.f.file_now(['file_attachments'], key='audit-pieces-734')
        self.f.run_jobs()
        cx.run_archive_email(self.desk, {'link_id': out['link_id']}, self.f.box, self.f.client)
        state = cx.status(self.desk, out['link_id'])
        self.assertEqual(state['extraction_state'], 'complete', 'Les dérivés modifiés doivent avoir une nouvelle version, pas un dépôt exclusif sur la version 1.')

    def test_F09_pause_active_automatique_est_reprenable(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)
        def work(*args):
            arret5643.pause_all(self.desk, True)
            pause5629.checkpoint(self.desk)
        with patch.object(self.desk, 'perform', side_effect=work):
            self.desk.work_once()
        status = self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0]
        self.assertEqual(status, 'suspended', 'Le motif pause ne doit pas devenir une annulation utilisateur terminale.')

    def test_F10_arret_utilisateur_remplace_demande_de_pause(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,)); self.desk.db.commit()
        arret5643.pause_all(self.desk, True)
        self.desk.cancel_job(jid)
        reason = self.desk.db.execute('SELECT stop_reason FROM jobs WHERE id=?', (jid,)).fetchone()[0]
        self.assertEqual(reason, 'user', 'Un clic Arrêter pendant une demande de pause doit devenir une annulation terminale explicite.')

    def test_F01_arret_apres_entetes_ferme_le_transport(self):
        ready, release, closed = threading.Event(), threading.Event(), threading.Event()
        server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)
        self.addCleanup(server.close)
        self.addCleanup(release.set)
        port = server.getsockname()[1]
        def serve():
            conn, _ = server.accept()
            try:
                data = b''
                while b'\r\n\r\n' not in data:
                    data += conn.recv(4096)
                headers, body = data.split(b'\r\n\r\n', 1)
                size = int(next(x.split(b':', 1)[1] for x in headers.split(b'\r\n') if x.lower().startswith(b'content-length:')))
                while len(body) < size: body += conn.recv(4096)
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 10000\r\nConnection: close\r\n\r\n{')
                ready.set(); conn.settimeout(.1)
                while not release.is_set():
                    try:
                        if not conn.recv(1): closed.set(); break
                    except socket.timeout: pass
            finally: conn.close()
        worker = threading.Thread(target=serve, daemon=True); worker.start()
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=0)
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,)); self.desk.db.commit()
        def cancel():
            ready.wait(3); time.sleep(.2)
            fixtures._sql(self.desk, "UPDATE jobs SET status='cancel_requested' WHERE id=?", (jid,))
        threading.Thread(target=cancel, daemon=True).start()
        http = HTTP('http://127.0.0.1:%d' % port, local_only=True, timeout=30)
        with arret5643.job_context(self.desk.c['state_dir'], jid, 'DOS-1', False):
            with self.assertRaisesRegex(Stop, 'operation_annulee'):
                arret5643.interruptible(lambda: http.json('POST', '/api/chat', {}), {'state_dir': self.desk.c['state_dir']}, poll=.1)
        self.assertTrue(closed.wait(.5), 'La socket de réponse reste ouverte après réception des en-têtes.')


# ---------------------------------------------------------------------------------------------------------------- variantes des fiches
import io
from pathlib import Path
import os
import sqlite3
from contextlib import redirect_stderr

import test_v530 as base530
from agent import desk as desk_module, talk5643
from agent.desk import Desk


class Variantes(Regressions):
    """Variantes demandées dans les fiches de l'audit (recettes), sur les mêmes données fictives."""

    def test_F01_arret_avant_connexion_referme_la_socket_des_son_ouverture(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)

        class Conn:
            sock = left
        slot = arret5643._Slot()
        self.assertFalse(slot.abort())          # pas encore de transport : arrêt mémorisé
        slot.attach(Conn())                     # la connexion ouverte ensuite est refermée aussitôt
        right.settimeout(2)
        self.assertEqual(right.recv(1), b'')

    def test_F01_deux_appels_simultanes_un_seul_arrete(self):
        a, b = arret5643._Slot(), arret5643._Slot()
        la, ra = socket.socketpair()
        lb, rb = socket.socketpair()
        for s in (ra, rb, lb):
            self.addCleanup(s.close)

        class A:
            sock = la

        class B:
            sock = lb
        a.attach(A()); b.attach(B())
        a.abort()
        ra.settimeout(2)
        self.assertEqual(ra.recv(1), b'')
        lb.sendall(b'x')                        # l'autre appel reste ouvert
        rb.settimeout(2)
        self.assertEqual(rb.recv(1), b'x')

    def test_F02_pieces_seules_sans_piece_restent_refusees(self):
        raw = b'From: client@example.test\r\nTo: cabinet@example.test\r\nSubject: Bonjour\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSans piece.\r\n'
        box = Box({'735': raw})
        with self.assertRaisesRegex(Stop, 'aucune_piece_jointe_a_enregistrer'):
            cx.prepare(self.desk, 'cabinet', {'source': {'folder': 'INBOX', 'uid': '735', 'uidvalidity': box.validity}, 'matter_id': 'DOS-1',
                                              'actions': ['file_attachments']}, box)
        prop = cx.prepare(self.desk, 'cabinet', {'source': {'folder': 'INBOX', 'uid': '735', 'uidvalidity': box.validity}, 'matter_id': 'DOS-1',
                                                 'actions': ['archive_email', 'file_attachments']}, box)
        self.assertEqual(prop['actions'], ['archive_email'])
        self.assertIn('Aucune pièce jointe', prop['notices'][0])

    def test_F04_exception_du_message_allow_subsiste_et_deny_bloque(self):
        source = {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity}
        self.policy()
        _, out = self.automatic_proposal()
        cx.set_message_rule(self.desk, 'cabinet', {**source, 'expected_version': 0, 'email_override': 'allow'})
        self.policy(False, False)                                   # règle générale retirée, exception « allow » de ce message conservée
        with patch('agent.correspondances5643._box', return_value=self.f.box), patch('agent.correspondances5643._client', return_value=self.f.client):
            self.desk.work_once()
        self.assertEqual(cx.status(self.desk, out['link_id'])['conservation_state'], 'verified')

    def test_F04_exclusion_du_message_bloque_le_depot_automatique(self):
        source = {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity}
        self.policy()
        _, out = self.automatic_proposal()
        cx.set_message_rule(self.desk, 'cabinet', {**source, 'expected_version': 0, 'email_override': 'deny'})
        with patch('agent.correspondances5643._box', return_value=self.f.box), patch('agent.correspondances5643._client', return_value=self.f.client):
            self.desk.work_once()
        st = cx.status(self.desk, out['link_id'])
        self.assertEqual((st['conservation_state'], st['original']), ('cancelled', None))

    def test_F04_commande_manuelle_garde_son_mandat(self):
        _, out = self.f.file_now(['archive_email'])                 # commande de l'avocat : aucune règle générale requise
        with patch('agent.correspondances5643._box', return_value=self.f.box), patch('agent.correspondances5643._client', return_value=self.f.client):
            self.desk.work_once()
        self.assertEqual(cx.status(self.desk, out['link_id'])['conservation_state'], 'verified')

    def test_F05_uidvalidity_modifiee_reprend_le_parcours_sans_doublon(self):
        self.policy()
        box = Box({'1': self.f.raw})
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 1)
        box.validity = '99999'                                     # boîte renumérotée par le serveur
        cx.auto_tick(self.desk, box)                                 # détection : parcours repris
        again = cx.auto_tick(self.desk, box)
        self.assertEqual(again['queued'], 0)                         # même message (même empreinte) : pas de second classement
        jobs = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='archive_correspondence5643'").fetchone()[0]
        self.assertEqual(jobs, 1)

    def test_F05_message_du_dossier_en_pause_est_differe_puis_repris(self):
        self.policy()
        pause5629.set_pause(self.desk, 'DOS-1', True)
        box = Box({'1': self.f.raw})
        out = cx.auto_tick(self.desk, box)
        self.assertEqual((out['queued'], out['deferred']), (0, 1))
        pause5629.set_pause(self.desk, 'DOS-1', False)
        key = cx._cursor_key(self.desk, 'INBOX')
        cursor = self.desk.settings(key)
        cursor['deferred']['1']['next'] = '2000-01-01T00:00:00+00:00'  # échéance de nouvel essai atteinte
        self.desk.setting(key, cursor)
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 1)

    def test_F05_pendant_la_pause_rien_n_est_consomme(self):
        self.policy()
        self.desk.setting('missions567:pause', True)
        self.assertTrue(cx.auto_tick(self.desk, Box({'1': self.f.raw})).get('paused'))
        self.desk.setting('missions567:pause', False)
        self.assertEqual(cx.auto_tick(self.desk, Box({'1': self.f.raw}))['queued'], 1)

    def test_F06_selection_exacte_conservee_et_mandat_retire_par_exclusion(self):
        self.desk.setting('missions567:pause', True)
        state = cx.message_state(self.desk, '734', 'INBOX', self.f.box)
        first = next(p for p in state['parts'] if p['default'])
        source = {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity}
        out = cx.entrust(self.desk, 'cabinet', {**source, 'matter_id': 'DOS-1', 'actions': ['file_attachments'],
                                               'attachments_selection': {'mode': 'selected', 'parts': [{'part_key': first['part_key']}]}}, self.f.box)
        self.assertEqual(out['state'], 'en_attente_reprise')
        rule = self.desk.db.execute('SELECT attachments_selection,selected_parts_json FROM mail_filing_message_rules').fetchone()
        self.assertEqual((rule[0], json.loads(rule[1])[0]['part_key']), ('selected', first['part_key']))
        version = cx.get_message_rule(self.desk, source)['version']
        cx.set_message_rule(self.desk, 'cabinet', {**source, 'expected_version': version, 'attachments_override': 'deny'})
        self.assertEqual(self.desk.db.execute('SELECT state FROM mail_filing_mandates').fetchone()[0], 'cancelled')
        self.desk.setting('missions567:pause', False)
        self.assertEqual(cx.auto_tick(self.desk, Box({'734': self.f.raw}))['queued'], 0)

    def test_F06_mandat_execute_une_seule_fois_avec_sa_selection(self):
        self.desk.setting('missions567:pause', True)
        state = cx.message_state(self.desk, '734', 'INBOX', self.f.box)
        first = next(p for p in state['parts'] if p['default'])
        source = {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity}
        cx.entrust(self.desk, 'cabinet', {**source, 'matter_id': 'DOS-1', 'actions': ['file_attachments'],
                                         'attachments_selection': {'mode': 'selected', 'parts': [{'part_key': first['part_key']}]}}, self.f.box)
        self.desk.setting('missions567:pause', False)
        box = Box({'734': self.f.raw}, datetime.now(timezone.utc) - timedelta(days=30))
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 1)
        self.assertEqual(cx.auto_tick(self.desk, box)['queued'], 0)            # une seule exécution
        link = self.desk.db.execute('SELECT resolution_json FROM mail_archive_links').fetchone()[0]
        self.assertEqual([p['key'] for p in json.loads(link)['parts']], [first['part_key']])   # jamais élargi à toutes les pièces

    def test_F07_double_clic_sur_la_nouvelle_tentative_un_seul_travail(self):
        _, out = self.f.file_now(['archive_email'])
        self.desk.cancel_job(out['jobs'][0]['job_id'])
        prop = cx.prepare(self.desk, 'cabinet', {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity,
                                                 'matter_id': 'DOS-1', 'actions': ['archive_email']}, self.f.box)
        for _ in range(2):
            cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'nouvel-essai-734'})
        pending = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='archive_correspondence5643' AND status='pending'").fetchone()[0]
        self.assertEqual(pending, 1)

    def test_F07_etat_affiche_suit_le_travail_annule(self):
        _, out = self.f.file_now(['archive_email'])
        self.desk.cancel_job(out['jobs'][0]['job_id'])
        self.assertEqual(cx.status(self.desk, out['link_id'])['conservation_state'], 'cancelled')

    def test_F08_deux_executions_identiques_ne_creent_pas_de_version(self):
        _, out = self.f.file_now(['archive_email'])
        self.f.run_jobs()
        cx.run_archive_email(self.desk, {'link_id': out['link_id']}, self.f.box, self.f.client)
        versions = self.desk.db.execute("SELECT MAX(version) FROM mail_archive_assets WHERE link_id=? AND kind IN ('reading','manifest')",
                                        (out['link_id'],)).fetchone()[0]
        self.assertEqual(versions, 1)

    def test_F08_nouvelle_version_sans_ecraser_et_lecture_courante(self):
        prop, out = self.f.file_now(['archive_email'])
        self.f.run_jobs()
        mdir = prop['paths']['message_dir']
        first = self.f.file(mdir + '/lecture.md')
        self.f.file_now(['file_attachments'], key='pieces-ensuite-734')
        self.f.run_jobs()
        cx.run_archive_email(self.desk, {'link_id': out['link_id']}, self.f.box, self.f.client)
        self.assertEqual(self.f.file(mdir + '/lecture.md'), first)               # version 1 conservée
        self.assertIn('NOS PIECES', self.f.file(mdir + '/lecture.v2.md').decode('utf-8'))
        states = dict(self.desk.db.execute("SELECT version,state FROM mail_archive_assets WHERE link_id=? AND kind='reading'", (out['link_id'],)).fetchall())
        self.assertEqual(states, {1: 'superseded', 2: 'verified'})
        self.assertTrue(cx.secondary_representation(mdir + '/lecture.v2.md'))     # une seule lecture indexée par courriel
        self.assertFalse(cx.secondary_representation(mdir + '/lecture.md'))

    def test_F08_lecture_partielle_regeneree_par_une_nouvelle_commande(self):
        _, out = self.f.file_now(['archive_email'])
        self.f.run_jobs()
        self.desk.db.execute("UPDATE mail_archive_links SET extraction_state='partial' WHERE id=?", (out['link_id'],)); self.desk.db.commit()
        prop = cx.prepare(self.desk, 'cabinet', {'folder': 'INBOX', 'uid': '734', 'uidvalidity': self.f.box.validity,
                                                 'matter_id': 'DOS-1', 'actions': ['archive_email']}, self.f.box)
        again = cx.confirm(self.desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'regenerer-734'})
        self.assertEqual(len(again['jobs']), 1)

    def test_F09_reprise_avant_acquittement_le_travail_continue(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)

        def work(*args):
            arret5643.pause_all(self.desk, True)
            arret5643.pause_all(self.desk, False)                   # reprise avant que le worker ait vu la pause
            pause5629.checkpoint(self.desk)
            return {'message': 'terminé'}
        with patch.object(self.desk, 'perform', side_effect=work):
            self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')

    def test_F09_interrompu_puis_reprise_remis_en_file_sans_second_clic(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)

        def work(*args):
            arret5643.pause_all(self.desk, True)
            try:
                pause5629.checkpoint(self.desk)
            finally:
                self.desk.setting('missions567:pause', False)       # reprise pendant l'acquittement de l'interruption
        with patch.object(self.desk, 'perform', side_effect=work):
            self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'pending')

    def test_F10_pause_puis_arret_puis_reprise_ne_relance_pas(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)

        def work(*args):
            arret5643.pause_all(self.desk, True)
            self.desk.cancel_job(jid)                                # l'avocat arrête pendant la pause
            pause5629.checkpoint(self.desk)
        with patch.object(self.desk, 'perform', side_effect=work):
            self.desk.work_once()
        arret5643.pause_all(self.desk, False)
        self.assertEqual(self.desk.db.execute('SELECT status,stop_reason FROM jobs WHERE id=?', (jid,)).fetchone()[:], ('cancelled', 'user'))


# ---------------------------------------------------------------------------------------------------------------- démarrage et verrou
class DemarrageVerrou(base530.Base):
    """La préparation du schéma n'écrit rien quand rien n'a changé, attend un verrou tenu par un autre processus, puis réussit ; au-delà
    du délai, elle échoue avec une ligne claire (le service est relancé par systemd)."""

    def fresh_process(self):
        desk_module._SCHEMA_READY.clear()                            # comme un nouveau processus (interface, worker ou veille)

    def hold_lock(self):
        other = sqlite3.connect(str(Path(self.desk.c['state_dir']) / 'desk.sqlite3'), timeout=1, check_same_thread=False)
        other.execute('BEGIN IMMEDIATE')                             # écriture tenue par « un autre processus »
        return other

    def test_sans_changement_aucune_ecriture_au_demarrage(self):
        self.fresh_process()
        other = self.hold_lock()
        self.addCleanup(other.close)
        started = time.monotonic()
        with patch.dict(os.environ, {'AXIORHUB_STARTUP_WAIT': '0'}):
            d = Desk(self.desk.c)                                    # aucune écriture : aucun verrou attendu
        d.db.close()
        other.rollback()
        self.assertLess(time.monotonic() - started, 5)

    def test_verrou_tenu_attente_puis_succes_avec_ligne_au_journal(self):
        self.desk.db.execute('DELETE FROM schema_migrations WHERE id=645'); self.desk.db.commit()   # une écriture devient nécessaire
        self.fresh_process()
        other = self.hold_lock()
        released = threading.Timer(2.5, other.commit)
        released.start()
        self.addCleanup(released.cancel)
        self.addCleanup(other.close)
        log = io.StringIO()
        with patch.dict(os.environ, {'AXIORHUB_STARTUP_WAIT': '60'}), patch('agent.common.busy_timeout', return_value=0.2), redirect_stderr(log):
            d = Desk(self.desk.c)
        self.addCleanup(d.db.close)
        self.assertIn('base de données occupée par un autre processus pendant la préparation du démarrage', log.getvalue())
        self.assertIn('préparation terminée après', log.getvalue())
        self.assertTrue(d.db.execute('SELECT 1 FROM schema_migrations WHERE id=645').fetchone())

    def test_verrou_tenu_au_dela_du_delai_echec_explique(self):
        self.desk.db.execute('DELETE FROM schema_migrations WHERE id=645'); self.desk.db.commit()
        self.fresh_process()
        other = self.hold_lock()
        self.addCleanup(other.close)
        log = io.StringIO()
        with patch.dict(os.environ, {'AXIORHUB_STARTUP_WAIT': '1'}), patch('agent.common.busy_timeout', return_value=0.2), redirect_stderr(log):
            with self.assertRaises((sqlite3.OperationalError, Stop)):
                Desk(self.desk.c)
        other.rollback()
        self.assertIn('toujours occupée par un autre processus', log.getvalue())


# ---------------------------------------------------------------------------------------------------------------- test d'affichage de Talk
class TalkRegleNavigateurs(unittest.TestCase):
    NC, AXH = 'https://cloud.example.test', 'https://courriel.example.test'

    def verdict(self, *headers):
        return talk5643.framing_verdict(list(headers), self.NC, self.AXH)

    def test_frame_ancestors_autorisant_axiorhub_l_emporte_sur_sameorigin(self):
        v = self.verdict(('X-Frame-Options', 'SAMEORIGIN'), ('Content-Security-Policy', "frame-ancestors 'self' https://courriel.example.test"))
        self.assertEqual((v['blocked'], v['rule'], v['xfo_ignored']), (False, 'frame-ancestors', True))

    def test_frame_ancestors_sans_axiorhub_bloque(self):
        v = self.verdict(('Content-Security-Policy', "default-src 'self'; frame-ancestors 'self' https://autre.example.test"))
        self.assertTrue(v['blocked'])

    def test_chaque_politique_doit_autoriser(self):
        v = self.verdict(('Content-Security-Policy', "frame-ancestors 'self' https://courriel.example.test"),
                         ('Content-Security-Policy', "frame-ancestors 'self'"))
        self.assertTrue(v['blocked'])
        ok = self.verdict(('Content-Security-Policy', "frame-ancestors 'self' https://courriel.example.test"),
                          ('Content-Security-Policy', "frame-ancestors https://*.example.test"))
        self.assertFalse(ok['blocked'])

    def test_sans_frame_ancestors_x_frame_options_s_applique(self):
        self.assertTrue(self.verdict(('X-Frame-Options', 'SAMEORIGIN'))['blocked'])
        self.assertTrue(self.verdict(('X-Frame-Options', 'DENY'), ('Content-Security-Policy', "default-src 'self'"))['blocked'])
        self.assertFalse(self.verdict()['blocked'])
        same = talk5643.framing_verdict([('X-Frame-Options', 'SAMEORIGIN')], self.NC, self.NC)   # ExApp : même origine
        self.assertFalse(same['blocked'])

    def test_none_et_schema(self):
        self.assertTrue(self.verdict(('Content-Security-Policy', "frame-ancestors 'none'"))['blocked'])
        self.assertFalse(self.verdict(('Content-Security-Policy', 'frame-ancestors https:'))['blocked'])
        self.assertTrue(self.verdict(('Content-Security-Policy', 'frame-ancestors https://courriel.example.test:8443'))['blocked'])

    def test_origine_vue_par_le_navigateur(self):
        self.assertEqual(talk5643.axiorhub_origin({'HTTP_ORIGIN': 'https://Courriel.example.test'}), 'https://courriel.example.test')
        self.assertEqual(talk5643.axiorhub_origin({'HTTP_X_FORWARDED_PROTO': 'https', 'HTTP_HOST': 'courriel.example.test'}),
                         'https://courriel.example.test')

    def test_message_du_test_complet(self):
        class Response:
            status = 302

            def getheaders(self):
                return [('X-Frame-Options', 'SAMEORIGIN'), ('Content-Security-Policy', "frame-ancestors 'self' https://courriel.example.test")]

        class Conn:
            def __init__(self, *a, **k):
                pass

            def request(self, *a, **k):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass
        desk = type('D', (), {'c': {'nextcloud': {'url': 'https://cloud.example.test/remote.php/dav/files/fictif/'}}})()
        with patch('http.client.HTTPSConnection', Conn):
            out = talk5643.test(desk, {'talk_url': 'https://cloud.example.test/index.php/apps/spreed/'},
                                {'HTTP_ORIGIN': 'https://courriel.example.test'})
        self.assertFalse(out['blocked'])
        self.assertIn('Intégration autorisée', out['message'])
        self.assertIn('ignoré par les navigateurs', out['message'])


# ---------------------------------------------------------------------------------------------------------------- page du dossier : corrections
import test_v460 as t460
from agent import dossier5644


class CorrectionsDuDossier(unittest.TestCase):
    """L'avocat supprime, modifie ou ajoute une information proposée par l'IA ; les doublons évidents de parties sont fusionnés."""
    PATH = t460.Pages.PATH
    call = t460.Pages.call
    post = t460.Pages.post
    _base_setup = t460.Pages._base_setup

    def setUp(self):
        t460.Pages.setUp(self)   # mêmes données fictives que les pages 4.6.0, sans reprendre leurs tests
        from agent import facts460
        facts460.propose(self.desk, 'DOS-001', t460.PATH, t460.TEXT)

    def page(self, onglet=''):
        return self.call('/fiche', query='matter=DOS-001' + ('&onglet=' + onglet if onglet else ''))['body'].decode()

    def test_doublons_evidents_fusionnes_avec_leurs_sources(self):
        items = [{'id': 'r1', 'value': 'Alpha Fictive SARL', 'status': 'suggested', 'sources': [{'label': 'Courrier.pdf'}]},
                 {'id': '', 'value': 'Alpha Fictive SARL (3 rue Exemple, 75000 Paris)', 'status': 'derived', 'sources': [{'label': 'Synthèse v1'}]},
                 {'id': 'r2', 'value': 'Alpha Fictive SAS', 'status': 'suggested', 'sources': [{'label': 'CGV.docx'}]},
                 {'id': '', 'value': 'SCI Exemple - Bureaux', 'status': 'derived', 'sources': []},
                 {'id': '', 'value': 'SCI Exemple', 'status': 'derived', 'sources': []}]
        out = dossier5644.dedupe(items)
        self.assertEqual([i['value'] for i in out], ['Alpha Fictive SARL', 'Alpha Fictive SAS', 'SCI Exemple - Bureaux', 'SCI Exemple'])
        self.assertEqual([s['label'] for s in out[0]['sources']], ['Courrier.pdf', 'Synthèse v1'])

    def test_supprimer_une_partie_refuse_le_fait_et_s_annule(self):
        parties = self.page('parties')
        self.assertIn('SAS FIMEX', parties)
        self.assertIn('data-api="m5644/dossier/corriger"', parties)
        self.assertIn('Ajouter une partie', parties)
        from agent import dossier5624
        item = next(i for i in dossier5624.rubrique(self.desk, 'DOS-001', 'parties')['items'] if 'FIMEX' in i['value'])
        r, out = self.post('m5644/dossier/corriger', {'matter': 'DOS-001', 'section': 'parties', 'action': 'hide', 'target': item['value'],
                                                      'record_id': item['id']})
        self.assertTrue(r['status'].startswith('200'), out)
        self.assertNotIn(item['value'], [i['value'] for i in dossier5624.rubrique(self.desk, 'DOS-001', 'parties')['items']])
        status = self.desk.db.execute('SELECT status FROM legal_memory_records WHERE id=?', (item['id'],)).fetchone()[0]
        self.assertEqual(status, 'disputed')                       # plus réutilisé dans les rédactions
        self.assertIn('Supprimé(s) par l’avocat (1)', self.page('parties'))
        cid = dossier5644.corrections(self.desk, 'DOS-001', 'parties')[0]['id']
        r, out = self.post('m5644/dossier/annuler-correction', {'matter': 'DOS-001', 'id': cid})
        self.assertTrue(r['status'].startswith('200'), out)
        self.assertEqual(dossier5644.corrections(self.desk, 'DOS-001', 'parties'), [])

    def test_modifier_et_ajouter_puis_reafficher_dans_la_synthese(self):
        from agent import dossier5624
        item = next(i for i in dossier5624.rubrique(self.desk, 'DOS-001', 'parties')['items'] if 'FIMEX' in i['value'])
        dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'parties', 'action': 'replace', 'target': item['value'],
                                                  'value': 'SAS FIMEX (demanderesse)', 'record_id': item['id']})
        dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'parties', 'action': 'add', 'value': 'Société Témoin Fictive'})
        synthese = self.page()
        self.assertIn('SAS FIMEX (demanderesse)', synthese)
        self.assertIn('Corrigé par l’avocat', synthese)
        self.assertIn('Société Témoin Fictive', synthese)
        self.assertIn('Annuler ma correction', synthese)

    def test_juridiction_fausse_supprimee_puis_saisie(self):
        from agent import fiche460
        f = fiche460.build_fiche(self.desk, 'DOS-001')
        self.assertTrue(f['jurisdiction'])
        dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'jurisdiction', 'action': 'hide', 'target': f['jurisdiction']['value'],
                                                  'record_id': f['jurisdiction']['id']})
        self.assertIsNone(fiche460.build_fiche(self.desk, 'DOS-001')['jurisdiction'])
        self.assertIn('Indiquer la juridiction', self.page())
        dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'jurisdiction', 'action': 'add', 'value': 'Tribunal de commerce fictif'})
        self.assertEqual(fiche460.build_fiche(self.desk, 'DOS-001')['jurisdiction']['value'], 'Tribunal de commerce fictif')

    def test_garde_fous(self):
        with self.assertRaisesRegex(Stop, 'correction_invalide'):
            dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'autre', 'action': 'hide', 'target': 'x'})
        with self.assertRaisesRegex(Stop, 'correction_vide'):
            dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'parties', 'action': 'add', 'value': '  '})
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            dossier5644.correct(self.desk, 'cabinet', {'matter': 'INCONNU', 'section': 'parties', 'action': 'add', 'value': 'X'})
        self.assertTrue(dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'parties', 'action': 'add', 'value': 'Y'})['id'])
        again = dossier5644.correct(self.desk, 'cabinet', {'matter': 'DOS-001', 'section': 'parties', 'action': 'add', 'value': 'Y'})
        self.assertEqual(again['message'], 'Correction déjà enregistrée.')   # double clic : une seule correction
        from agent.web5644 import allowed
        self.assertTrue(allowed('avocat', 'dossier/corriger'))
        self.assertFalse(allowed('lecture', 'dossier/corriger'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
