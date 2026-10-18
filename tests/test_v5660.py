"""5.6.60 : file de travail débloquée (cas constaté : un dossier en pause figeait toute la file), diagnostic des blocages, page Paramètres
réorganisée. Données fictives (domaines example.test)."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_v5652 as base
from agent import blocage5660 as bl, pause5629, proactive, queue567
from agent.common import Stop

MID = base.MID


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class File(base.Base):
    def _job(self, kind='health', **values):
        jid = self.desk.enqueue(kind, {'n': str(values.pop('n', 1))})
        if values:
            self.desk.db.execute('UPDATE jobs SET %s WHERE id=?' % ','.join('%s=?' % k for k in values), list(values.values()) + [jid])
            self.desk.db.commit()
        return jid

    def _row(self, jid):
        return self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()

    def test_balayage_passe_un_dossier_en_pause(self):
        """Le cas du 18 octobre : un dossier en pause faisait échouer monitor_all, relancé 118 739 fois en tête de file."""
        pause5629.set_pause(self.desk, MID, True)
        with patch('agent.portfolio.portfolio_rows', return_value=[{'matter': MID}]):
            out = proactive.monitor_all(self.desk, {})
        self.assertEqual(out['dossiers_en_pause_passes'], 1)
        self.assertEqual(out['dossiers_mis_en_attente'], 0)
        self.assertFalse(out['suite'])

    def test_suspension_sans_pause_ne_boucle_plus(self):
        jid = self._job()
        with patch('agent.desk.Desk.perform', side_effect=Stop('agent_en_pause_sur_ce_dossier')):
            for attempt in range(1, 4):
                self.desk.db.execute('UPDATE jobs SET not_before=NULL WHERE id=?', (jid,))
                self.desk.db.commit()
                self.desk.work_once()
                row = self._row(jid)
                if attempt < 3:
                    self.assertEqual(row['status'], 'pending', attempt)
                    self.assertGreater(row['not_before'], _iso())          # relance différée, jamais immédiate
        row = self._row(jid)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(json.loads(row['result'])['erreur'], 'suspension_sans_pause_en_vigueur')

    def test_attente_du_verrou_differee_sans_tentative(self):
        first, second = self._job(n=1), self._job(n=2)

        @contextmanager
        def busy(config, kind, args):
            raise Stop('traitement_deja_en_cours')
            yield
        with patch('agent.queue567.producer_lock', busy):
            self.desk.work_once()
        row = self._row(first)
        self.assertEqual((row['status'], row['attempts'], row['lock_waits']), ('pending', 0, 1))
        self.assertGreater(row['not_before'], _iso(seconds=5))
        with patch('agent.desk.Desk.perform', return_value={'ok': True}):
            self.desk.work_once()                                          # le suivant passe au lieu d'attendre derrière
        self.assertEqual(self._row(second)['status'], 'done')
        self.assertEqual(self._row(first)['status'], 'pending')
        self.assertEqual(bl.backoff_seconds(1), 20)
        self.assertEqual(bl.backoff_seconds(50), bl.BACKOFF_MAX)

    def test_duree_maximale(self):
        long_job = self._job(n=3, status='running', started=_iso(minutes=-30))
        self.desk.db.execute("UPDATE jobs SET kind='assistant_answer' WHERE id=?", (long_job,))
        short_job = self._job(n=4, status='running', started=_iso(minutes=-5))
        self.desk.db.commit()
        self.assertEqual(bl.watchdog(self.desk), [long_job])
        row = self._row(long_job)
        self.assertEqual((row['status'], row['stop_reason']), ('cancel_requested', 'delai_depasse'))
        self.assertEqual(self._row(short_job)['status'], 'running')

    def test_question_trop_ancienne_non_relancee(self):
        old = self._job(n=5, status='running', worker='1', created=_iso(hours=-30), attempts=1)
        recent = self._job(n=6, status='running', worker='1', created=_iso(minutes=-10), attempts=1)
        self.desk.db.execute("UPDATE jobs SET kind='assistant_answer' WHERE id IN (?,?)", (old, recent))
        self.desk.db.commit()
        queue567.recover(self.desk, '1')
        self.assertEqual(self._row(old)['status'], 'error')
        self.assertEqual(json.loads(self._row(old)['result'])['erreur'], 'question_trop_ancienne')
        self.assertEqual(self._row(recent)['status'], 'pending')

    def test_diagnostic_des_blocages(self):
        self._job(n=7, status='running', started=_iso(hours=-5))
        self._job(n=8, created=_iso(hours=-10), lock_waits=40, blocked_by='Travail n° 1 (index)')
        self.desk.setting('missions567:pause', True)
        db = sqlite3.connect(str(self.desk.c['state_dir']) + '/desk.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            codes = {f['code'] for f in bl.diagnose(db, self.desk.c['state_dir'])}
        finally:
            db.close()
        for code in ('pause_ia', 'travail_trop_long', 'attente_verrou', 'file_figee'):
            self.assertIn(code, codes)
        self.assertIn('Bloquant', bl.text_report(bl.diagnose(self.desk.db, self.desk.c['state_dir'])))
        self.assertIn('Blocages détectés', bl.html(self.desk, '/p'))

    def test_veille_orpheline_close(self):
        from agent import veille5632
        veille5632.schema(self.desk)
        now = _iso()
        self.desk.db.execute("INSERT INTO veille_runs5632(owner,slot,state,created) VALUES('cabinet','slot-ancien','pending',?)", (now,))
        self.desk.db.execute("INSERT INTO veille_runs5632(owner,slot,state,created) VALUES('cabinet','slot-recent','pending',?)", (now,))
        self.desk.db.commit()
        self.assertEqual(bl.close_orphan_veille(self.desk), 1)
        states = dict(self.desk.db.execute('SELECT slot,state FROM veille_runs5632').fetchall())
        self.assertEqual(states['slot-ancien'], 'cancelled')
        self.assertEqual(states['slot-recent'], 'pending')


import test_v568 as t568                                                                  # noqa: E402


class Flux(t568.Base):
    source = 'https://www.courdecassation.fr/actualites.xml'
    def test_article_republie_avec_une_autre_date_non_duplique(self):
        """Constaté pendant la recette 5.6.60 : deux lectures de part et d'autre d'une seconde donnaient deux fois le même article."""
        from email.utils import format_datetime
        from agent import news568 as news, settings568 as settings
        settings.save(self.desk, {'news_enabled': True, 'legal_fields': ['contrat'], 'news_sources': [self.source]})

        def rss(stamp):
            return ('<rss><channel><item><title>Contrat : nouvelle décision</title><link>https://www.courdecassation.fr/decision/123</link><pubDate>'
                    + format_datetime(stamp) + '</pubDate><description>Un contrat à examiner.</description></item></channel></rss>').encode()
        first = datetime.now(timezone.utc) - timedelta(hours=2)
        news.collect(self.desk, fetch=lambda _: rss(first))
        news.collect(self.desk, fetch=lambda _: rss(first + timedelta(minutes=5)))
        self.assertEqual(len(news.listing(self.desk)['items']), 1)


import test_v5621 as t21                                                                  # noqa: E402
from agent import parametres5621 as p21                                                  # noqa: E402


class Parametres(t21.Hub):
    # repère distinctif de chaque ancienne rubrique (5.6.59) : tous doivent rester présents dans la nouvelle organisation
    MARKERS = {'connexions': 'id="connections567-form"', 'ia': 'id="economie569"', 'voix': 'data-config567="speech568.provider"',
               'routines': 'name="roles"', 'documents': 'id="ax-office-settings"', 'accueil': 'Accueil téléphonique et WhatsApp',
               'envoi': 'id="envoi"', 'clavier': 'id="clavier"', 'maintenance': 'Maintenance', 'messagerie': 'id="envoi5636"',
               'veille': 'name="themes"', 'sources': 's5632-card', 'comptes': 'id="comptes"', 'confidentialite': 'Confidentialité'}

    def test_aucun_reglage_perdu(self):
        pages = {k: self._get('/parametres?rubrique=' + k) for k, _, _ in p21.RUBRIQUES}
        everything = '\n'.join(pages.values())
        for name, marker in self.MARKERS.items():
            self.assertIn(marker, everything, name)
        for key, body in pages.items():
            self.assertIn('class="p5621-tabs"', body, key)
            self.assertIn('Trouver un réglage', body, key)
        self.assertEqual(sum(len(keys) for _, keys in p21.FAMILIES), len(p21.RUBRIQUES))

    def test_anciennes_rubriques_et_adresses(self):
        self.assertEqual(p21.rubrique_for('/parametres', {'rubrique': 'sources'}), 'recherche')
        self.assertEqual(p21.rubrique_for('/parametres', {'rubrique': 'veille'}), 'recherche')
        self.assertEqual(p21.rubrique_for('/parametres', {'rubrique': 'comptes'}), 'cabinet')
        self.assertEqual(p21.rubrique_for('/confort', {}), 'poste')
        self.assertEqual(p21.rubrique_for('/parametres', {}), 'connexions')
        self.assertIn('id="comptes"', self._get('/parametres?rubrique=comptes'))
        self.assertIn('name="themes"', self._get('/parametres?rubrique=veille'))

    def test_vue_d_ensemble_sommaire_et_sections(self):
        body = self._get('/parametres?rubrique=apercu')
        self.assertIn('État des réglages', body)
        self.assertIn('File de travail de l’agent', body)
        body = self._get('/parametres?rubrique=poste')
        self.assertIn('Sur cette page', body)
        self.assertIn('<details open class="p5621-section p5660-sec"', body)       # première section ouverte, les autres repliées
        self.assertIn('/static/v5660.js', body)


if __name__ == '__main__':
    unittest.main()
