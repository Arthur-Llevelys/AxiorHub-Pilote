"""Contre-tests de disponibilité 5.6.64. Données fictives, aucun service extérieur.

Repris du dossier technique de l'audit de la 5.6.64 (19 octobre 2026). Seule adaptation 5.6.65, prévue par l'audit : R15 attend un cycle
du superviseur indépendant (cadence réduite à 50 ms pour le test) au lieu de lire l'état immédiatement.

Les assertions expriment le comportement attendu APRÈS correction. Les échecs
sur les sources originales constituent les reproductions, pas une suite verte.
"""
from datetime import datetime, timedelta, timezone
import json
import threading
from unittest.mock import patch

import test_v5652 as base
from agent import arret5643, blocage5660, cadence5662, economie569, queue521, queue567
from agent.common import Stop
from agent.desk import Desk, worker

OBSERVATIONS = {}


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class FilesAttente(base.Base):
    def row(self, jid):
        return dict(self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())

    def alter(self, jid, **values):
        self.desk.db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                             [*values.values(), jid])
        self.desk.db.commit()

    def test_R13_demande_identique_suspendue_ne_cree_pas_de_doublon(self):
        args = {'question': 'Résumer les prochaines étapes', 'matter': base.MID, 'owner': 'avocat@example.test'}
        jid = self.desk.enqueue('assistant_answer', args, origin='user')
        arret5643.pause_all(self.desk, True)
        self.desk.work_once()
        self.assertEqual(self.row(jid)['status'], 'suspended')
        second = self.desk.enqueue('assistant_answer', args, origin='user')
        reprise = arret5643.pause_all(self.desk, False)
        pending = self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='assistant_answer' AND status='pending'").fetchone()[0]
        OBSERVATIONS['R13'] = {'premiere_demande': jid, 'demande_identique': second,
                               'repris': reprise['repris'], 'doublons_en_attente': pending}
        self.assertEqual(second, jid, 'Une demande suspendue identique doit être retrouvée, sans nouvelle production à la reprise.')
        self.assertEqual(pending, 1)

    def test_R14a_demande_future_ne_bloque_pas_maintenance_actuelle(self):
        jid = self.desk.enqueue('assistant_answer', {'question': 'Demande en reprise différée'}, origin='user')
        self.alter(jid, created=iso(hours=-2), not_before=iso(seconds=30))
        available = self.desk.work_once()
        reason = cadence5662.busy(self.desk)
        allowed = cadence5662.allow(self.desk, 'index_all', 360)
        OBSERVATIONS['R14a'] = {'travail_disponible': available, 'motif_charge': reason,
                                'indexation_autorisee': allowed, 'delai_avant_echeance_secondes': 30}
        self.assertFalse(available)
        self.assertTrue(allowed, 'Un travail non échu ne doit pas provoquer une contre-pression permanente.')

    def test_R14b_six_travaux_futurs_ne_simulent_pas_une_file_active(self):
        for i in range(6):
            jid = self.desk.enqueue('health', {'numero': i}, priority=50, origin='automatic')
            self.alter(jid, created=iso(minutes=-4), not_before=iso(seconds=30))
        reason = cadence5662.busy(self.desk)
        OBSERVATIONS['R14b'] = {'motif_charge': reason, 'travaux_echus': 0, 'travaux_futurs': 6}
        self.assertEqual(reason, '', 'Le seuil de charge doit compter les travaux réellement exécutables.')

    def test_R15_watchdog_surveille_aussi_un_worker_occupe(self):
        """Vraie boucle worker, horloge avancée, exécution bloquée puis libérée.

        Aucun délai réel de 20 minutes. Le second worker évite les appels de
        planification et de mise à jour du premier. Le fil heartbeat est neutralisé.
        """
        jid = self.desk.enqueue('assistant_answer', {'question': 'Question lente'}, origin='user')
        entered, release = threading.Event(), threading.Event()
        clock = [100.0]
        worker_errors = []
        original_start = threading.Thread.start

        class EndWorker(Exception):
            pass

        def blocked_work(desk):
            desk.db.execute("UPDATE jobs SET status='running',worker='2',started=? WHERE id=?", (iso(minutes=-30), jid))
            desk.db.commit()
            clock[0] += 120
            entered.set()
            release.wait(5)
            desk.db.close()
            raise EndWorker()

        def run_worker():
            try:
                worker(dict(self.desk.c), '2')
            except EndWorker:
                pass
            except BaseException as error:
                worker_errors.append(repr(error))

        def start_without_pulse(thread):
            if getattr(thread._target, '__name__', '') == 'pulse':
                return None
            return original_start(thread)

        with patch.object(Desk, 'work_once', blocked_work), \
                patch('agent.desk.time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(blocage5660, 'watchdog', wraps=blocage5660.watchdog) as watch, \
                patch.object(threading.Thread, 'start', start_without_pulse),                 patch('agent.desk.SUPERVISION_SECONDS', 0.05):
            thread = threading.Thread(target=run_worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(3), worker_errors)
                # 5.6.65 : un cycle du superviseur indépendant suffit, le worker restant bloqué
                import time as _time
                deadline = _time.time() + 3
                row = self.row(jid)
                while row['status'] == 'running' and _time.time() < deadline:
                    _time.sleep(0.05)
                    row = self.row(jid)
                OBSERVATIONS['R15'] = {'passages_watchdog': watch.call_count,
                                       'minutes_du_travail': 30, 'limite_minutes': blocage5660.limit_for('assistant_answer'),
                                       'statut_pendant_execution': row['status']}
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive(), 'Le worker de test doit être terminé.')
        self.assertFalse(worker_errors, worker_errors)
        self.assertEqual(row['status'], 'cancel_requested', 'La limite doit être surveillée pendant le travail, même sans worker libre.')

    def test_R16_controle_sante_ne_reserve_pas_toute_la_production(self):
        jid = self.desk.enqueue('health', origin='automatic')
        production_possible = []

        def health_in_progress(desk, kind, args):
            self.assertEqual(kind, 'health')
            try:
                with queue567.producer_lock(desk.c, 'assistant_answer', {'matter': 'DOS-AUTRE'}):
                    production_possible.append(True)
            except Stop as error:
                self.assertEqual(str(error), 'traitement_deja_en_cours')
                production_possible.append(False)
            return {'imap': 'ok', 'ollama': 'ok'}

        with patch.object(Desk, 'perform', health_in_progress):
            self.desk.work_once()
        OBSERVATIONS['R16'] = {'sante_terminee': self.row(jid)['status'],
                               'production_autre_dossier_possible_pendant_sante': production_possible[0]}
        self.assertTrue(production_possible[0], 'La sonde réseau en lecture seule doit laisser une place à la production utilisateur.')

    def test_R17_purge_automatique_preserve_une_indexation_manuelle(self):
        user = self.desk.enqueue('index', {'matter': base.MID}, origin='user')
        auto = self.desk.enqueue('health', {'automatique': True}, origin='automatic')
        out = queue521.purge_automatic(self.desk)
        OBSERVATIONS['R17'] = {'origine_demande': self.row(user)['origin'],
                               'statut_demande_manuelle': self.row(user)['status'],
                               'statut_controle_automatique': self.row(auto)['status'], 'bilan': out}
        self.assertEqual(self.row(user)['status'], 'pending', 'La purge doit respecter origin=user même pour une fonction de maintenance.')
        self.assertEqual(self.row(auto)['status'], 'cancelled')

    def test_R09b_reservation_future_ne_vide_pas_la_capacite_presente(self):
        holder = self.desk.enqueue('assistant_answer', {'question': 'Demande sur autre dossier', 'matter': 'DOS-AUTRE'}, origin='user')
        self.alter(holder, lock_waits=3, not_before=iso(seconds=15))
        user = self.desk.enqueue('assistant_answer', {'question': 'Demande immédiate', 'matter': base.MID}, origin='user')
        with patch.object(Desk, 'perform', return_value={'ok': True}):
            worked = self.desk.work_once()
        OBSERVATIONS['R09b'] = {'travail_reservataire_non_echu': holder, 'travail_immediat': user,
                                'traitement_execute': worked, 'statut_demande': self.row(user)['status']}
        self.assertEqual(self.row(user)['status'], 'done', 'Une réservation hors échéance ne doit pas paralyser la capacité disponible.')

    def test_C01_annulation_reste_terminale_apres_pause_reprise(self):
        jid = self.desk.enqueue('health', origin='automatic')
        self.desk.cancel_job(jid)
        arret5643.pause_all(self.desk, True)
        arret5643.pause_all(self.desk, False)
        self.assertEqual(self.row(jid)['status'], 'cancelled')

    def test_C02_watchdog_direct_detecte_bien_un_travail_trop_long(self):
        jid = self.desk.enqueue('assistant_answer', {'question': 'Question longue'}, origin='user')
        self.alter(jid, status='running', started=iso(minutes=-30))
        self.assertEqual(blocage5660.watchdog(self.desk), [jid])
        self.assertEqual(self.row(jid)['status'], 'cancel_requested')

    def test_C03_diagnostic_ollama_ne_genere_pas_de_texte(self):
        from agent import model
        calls = []

        class FakeHTTP:
            def __init__(self, *args, **kwargs):
                pass

            def json(self, method, path, payload):
                calls.append((method, path))
                return {'capabilities': ['completion']}

        with patch.object(model, 'HTTP', FakeHTTP):
            result = model.provider_diagnostic({'url': 'http://127.0.0.1:11434', 'model': 'modele-fictif'})
        OBSERVATIONS['C03'] = {'appels': calls, 'resultat': result['status']}
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(calls, [('POST', '/api/show')])

    def test_C04_frequences_effectives_selon_regime(self):
        econome = [economie569.interval(self.desk, 'health', 30),
                   economie569.interval(self.desk, 'run_continuous_business_tests', 60)]
        self.desk.setting(economie569.SETTING, False)
        complet = [economie569.interval(self.desk, 'health', 30),
                   economie569.interval(self.desk, 'run_continuous_business_tests', 60)]
        OBSERVATIONS['C04'] = {'minutes_econome_sante_metier': econome, 'minutes_complet_sante_metier': complet}
        self.assertEqual(econome, [120, 1440])
        self.assertEqual(complet, [30, 60])
