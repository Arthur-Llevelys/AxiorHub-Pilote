"""5.6.45 — le worker survit à une base occupée pendant sa boucle (journal du 14 octobre : « database is locked » dans work_once
pendant l'installation, service arrêté). Données fictives uniquement."""
import io
import sqlite3
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import test_v530 as fixtures
from agent import desk as desk_module


class _Fin(Exception):
    """Sortie de la boucle infinie du worker pour le test."""


class _NoThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class WorkerBaseOccupee(fixtures.Base):
    def run_worker(self, effects):
        log = io.StringIO()
        with patch('agent.desk.Desk.work_once', side_effect=effects), patch('agent.desk.time.sleep') as sleep, \
                patch('threading.Thread', _NoThread), redirect_stderr(log):
            with self.assertRaises(_Fin):
                desk_module.worker(self.desk.c, '2')
        return log.getvalue(), sleep

    def test_verrou_pendant_la_boucle_attente_puis_reprise(self):
        locked = sqlite3.OperationalError('database is locked')
        log, sleep = self.run_worker([locked, locked, False, _Fin()])
        self.assertEqual(log.count('base de données occupée par un autre processus (worker 2'), 2)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(waits[:2], [3.0, 6.0])                     # attente croissante, puis rythme normal
        self.assertEqual(waits[2], 3)

    def test_travail_reste_en_cours_rendu_a_la_file(self):
        jid = self.desk.enqueue('refresh_brief', {'matter': 'DOS-1'}, priority=50)
        self.desk.db.execute("UPDATE jobs SET status='running',worker='2' WHERE id=?", (jid,)); self.desk.db.commit()
        calls = {'n': 0}

        def effect():
            calls['n'] += 1
            if calls['n'] == 1:
                return False                                        # premier tour : récupération de démarrage déjà faite
            if calls['n'] == 2:
                d_obj = effect.desk
                d_obj.active_job_id = jid                            # verrou pendant la finalisation d'un travail
                d_obj.db.execute("UPDATE jobs SET status='running',worker='2' WHERE id=?", (jid,)); d_obj.db.commit()
                raise sqlite3.OperationalError('database is locked')
            raise _Fin()
        real_init = desk_module.Desk.__init__

        def init(self_, c):
            real_init(self_, c)
            effect.desk = self_
        with patch('agent.desk.Desk.__init__', init):
            log, _ = self.run_worker(lambda *a: effect())
        status = self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0]
        self.assertNotEqual(status, 'running')                     # rendu à la file ou signalé, jamais bloqué « en cours »

    def test_autre_erreur_sqlite_toujours_remontee(self):
        with patch('agent.desk.Desk.work_once', side_effect=sqlite3.OperationalError('no such table: fictive')), \
                patch('agent.desk.time.sleep'), patch('threading.Thread', _NoThread):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'no such table'):
                desk_module.worker(self.desk.c, '2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
