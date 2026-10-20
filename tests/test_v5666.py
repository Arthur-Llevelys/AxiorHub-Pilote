"""5.6.66 : retour du cabinet du 20 octobre 2026 — derniers courriels reçus plus traités par l'IA (régression 5.6.65 : un ancien travail
« suspendu » absorbait les nouvelles demandes identiques) ; notes internes rangées dans le dossier. Données fictives (example.test)."""
from datetime import datetime, timedelta, timezone
import time
import unittest

import test_v5652 as base
from agent import arret5643, cockpit530, notes5666, watch430
from agent.common import Stop, digest


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class SuspendusOrphelins(base.Base):
    def _suspended(self, kind, args=None, reason='pause', created=None, origin='automatic', matter=None):
        jid = self.desk.enqueue(kind, args, origin=origin)
        self.desk.db.execute("UPDATE jobs SET status='suspended',stop_reason=?,created=?,matter_ref=? WHERE id=?",
                             (reason, created or iso(days=-3), matter, jid))
        self.desk.db.commit()
        return jid

    def test_ancien_examen_suspendu_n_absorbe_plus_la_surveillance_des_courriels(self):
        stale = self._suspended('live_mail430')                         # pause levée depuis longtemps
        fresh = self.desk.enqueue('live_mail430', priority=5, origin='automatic')
        self.assertNotEqual(fresh, stale)
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (fresh,)).fetchone()[0], 'pending')

    def test_planificateur_relance_l_examen_des_courriels(self):
        self._suspended('live_mail430')
        done = self.desk.enqueue('live_mail430', priority=5, origin='automatic')
        self.desk.db.execute("UPDATE jobs SET status='done',finished=? WHERE id=?", (iso(minutes=-10), done))
        self.desk.db.commit()
        self.desk.setting('live430:last:live_mail430', time.time() - 600)
        jobs = watch430.schedule(self.desk)
        new = self.desk.db.execute("SELECT id FROM jobs WHERE kind='live_mail430' AND status='pending'").fetchall()
        self.assertEqual(len(new), 1)
        self.assertIn(new[0][0], jobs)

    def test_pendant_la_pause_la_demande_identique_retrouve_le_travail_suspendu(self):
        """Le contrat de la 5.6.65 (audit, R13) reste vrai tant que la pause est en vigueur."""
        args = {'question': 'Résumer', 'matter': base.MID, 'owner': 'avocat@example.test'}
        jid = self.desk.enqueue('assistant_answer', args, origin='user')
        arret5643.pause_all(self.desk, True)
        self.desk.work_once()
        self.assertEqual(self.desk.enqueue('assistant_answer', args, origin='user'), jid)
        self.desk.setting(arret5643.PAUSE_GLOBAL, False)                 # pause levée sans passer par la reprise
        self.assertNotEqual(self.desk.enqueue('assistant_answer', args, origin='user'), jid)

    def test_nettoyage_des_suspensions_orphelines(self):
        auto = self._suspended('health', {'n': 1})
        recent_user = self._suspended('assistant_answer', {'question': 'Q'}, created=iso(hours=-1), origin='user')
        old_user = self._suspended('assistant_answer', {'question': 'Q2'}, created=iso(days=-2), origin='user')
        kept = self._suspended('monitor_matter', {'matter': base.MID}, reason='pause_dossier', matter=base.MID)
        self.desk.setting(arret5643.PAUSE_MATTERS, {base.MID: {'par': 'avocat@example.test'}})   # pause du dossier toujours en vigueur
        self.assertEqual(arret5643.release_stale(self.desk), (1, 2))
        status = {r[0]: r[1] for r in self.desk.db.execute('SELECT id,status FROM jobs')}
        self.assertEqual((status[auto], status[recent_user], status[old_user], status[kept]), ('cancelled', 'pending', 'cancelled', 'suspended'))

    def test_nettoyage_au_demarrage_du_worker(self):
        from agent.queue567 import recover
        stale = self._suspended('live_calendar430')
        recover(self.desk, '1')
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (stale,)).fetchone()[0], 'cancelled')


class Client:
    def __init__(self, refuse=()):
        self.files, self.folders, self.refuse = {}, [], set(refuse)

    def stat(self, path):
        if path not in self.files:
            raise Stop('http_404')
        return {'path': path}

    def ensure_folder(self, path, boundary):
        self.folders.append((path, boundary))
        return path

    def put_file(self, path, data, content_type=''):
        if any(path.startswith(x) for x in self.refuse):
            raise Stop('http_403')
        self.files[path] = data
        return path


class NotesInternes(base.Base):
    def test_note_rangee_dans_le_dossier(self):
        c = Client()
        out = notes5666.deposit(self.desk, c, {'id': base.MID, 'path': '/Dossiers/DEMO'}, 'Avis de renvoi – DEMO', ['Ligne 1', 'Ligne 2'],
                                day=datetime(2026, 10, 20))
        self.assertEqual(out['chemin'], '/Dossiers/DEMO/2026-10-20 - Note interne - Avis de renvoi – DEMO.docx')
        self.assertEqual(out['emplacement'], 'dossier')
        again = notes5666.deposit(self.desk, c, {'id': base.MID, 'path': '/Dossiers/DEMO'}, 'Avis de renvoi – DEMO', ['x'], day=datetime(2026, 10, 20))
        self.assertTrue(again['chemin'].endswith(' (2).docx'))       # jamais d'écrasement

    def test_sans_dossier_ou_dossier_inaccessible_a_traiter(self):
        c = Client(refuse=('/Dossiers/DEMO/',))
        out = notes5666.deposit(self.desk, c, {'id': base.MID, 'path': '/Dossiers/DEMO'}, 'Note', ['x'])
        self.assertEqual(out['emplacement'], 'a_traiter')
        self.assertTrue(out['chemin'].startswith('/Dossiers/A TRAITER/'))
        self.assertIn(('/Dossiers/A TRAITER', '/Dossiers'), c.folders)
        self.assertEqual(notes5666.deposit(self.desk, Client(), None, 'Note', ['x'])['emplacement'], 'a_traiter')

    def test_brouillon_d_avis_rattache_a_son_dossier(self):
        from agent import notices440
        notices440.ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO notices440 VALUES(?,?,?,?,?,?,?,?)', ('n1', base.MID, '/Dossiers/DEMO/Avis.pdf', 'e', 'done', '{}', iso(), iso()))
        self.desk.db.commit()
        self.assertEqual(cockpit530._draft_matter(self.desk, {'agent_key': digest('notice-draft440|n1')}), base.MID)


class RubriquesDuJour(base.Base):
    """Retour du cabinet du 20 octobre 2026 : « À décider / À relire » mélangeait tout — courriels à envoyer noyés parmi les notes internes."""
    CACHE = {'at': 9e18, 'ok': True, 'validity': '5', 'items': [
        {'uid': '1', 'subject': '[À traiter] Avis de renvoi – DEMO – 2 décembre 2026', 'to': 'cabinet@example.test', 'date': '2026-10-20', 'agent': True,
         'agent_key': 'k1', 'message_id': 'axiorhub-k1@mail-agent.local', 'in_reply_to': ''},
        {'uid': '2', 'subject': 'Re: Calendrier de procédure', 'to': 'confrere@example.test', 'date': '2026-10-20', 'agent': False,
         'agent_key': '', 'message_id': 'x@example.test', 'in_reply_to': 'orig@example.test'},
        {'uid': '3', 'subject': 'Information au client', 'to': 'client@example.test', 'date': '2026-10-20', 'agent': True,
         'agent_key': 'k3', 'message_id': 'axiorhub-k3@mail-agent.local', 'in_reply_to': ''}]}

    def _items(self):
        from unittest.mock import patch
        from agent import file5651
        self.desk.setting('cockpit530:drafts', self.CACHE)
        with patch('agent.aujourdhui5614.items', return_value=[]):
            return file5651.items(self.desk, '/p'), file5651.html(self.desk, '/p')

    def test_nature_de_chaque_brouillon(self):
        rows, _ = self._items()
        by = {r['item']: r['class'] for r in rows if r.get('item')}
        self.assertEqual(by, {'mail:5:1': 'note_interne', 'mail:5:2': 'courriel', 'mail:5:3': 'courriel'})
        note = next(r for r in rows if r.get('item') == 'mail:5:1')
        self.assertEqual(note['title'], 'Avis de renvoi – DEMO – 2 décembre 2026')
        self.assertIn('ranger', note['choices'])

    def test_courriels_en_premier_notes_a_part(self):
        _, html = self._items()
        self.assertLess(html.index('<h3>Courriels à relire et envoyer'), html.index('<h3>Notes internes'))
        self.assertIn('Re: Calendrier de procédure', html)          # réponse sans marque de l'agent : visible
        self.assertIn('data-api="m5661/file/ranger-notes"', html)
        self.assertIn('Tout ranger dans les dossiers', html)

    def test_ranger_les_notes(self):
        from unittest.mock import patch
        from agent import drafts440
        self.desk.setting('cockpit530:drafts', self.CACHE)
        c, moved = Client(), []
        with patch.object(drafts440, 'get_draft', return_value={'body': 'Note interne – le document a été détecté.\nDates retenues : 2 décembre 2026'}):
            out = notes5666.file_drafts(self.desk, {'items': ['mail:5:1', 'mail:5:2']}, dav=c, trash=lambda d, uid, v: moved.append(uid) or True)
        self.assertEqual(out['rangees'], 1)                           # la réponse au confrère n'est pas une note : jamais rangée ni retirée
        self.assertEqual(moved, ['1'])
        self.assertTrue(out['notes'][0].startswith('/Dossiers/A TRAITER/'))   # dossier inconnu : dossier neutre
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM cockpit530_reviewed WHERE item='mail:5:1'").fetchone())

    def test_route_de_rangement(self):
        from agent import web5661
        self.assertTrue(web5661.allowed('avocat', 'file/ranger-notes'))
        self.assertFalse(web5661.allowed('lecture', 'file/ranger-notes'))


if __name__ == '__main__':
    unittest.main()
