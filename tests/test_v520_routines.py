"""5.2.0 : routines d'« Aujourd'hui » — briefing du matin, tri matinal, bilan hebdomadaire, documents à préparer."""
from datetime import datetime, time as dtime, timedelta, timezone
from email.message import EmailMessage
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import routines520 as r5
from agent.common import Stop
from agent.mailbox import Mail
import test_v510 as t510

ROOT = Path(__file__).resolve().parent.parent


def _mail(uid, sender, subject, body, when, mid, refs='', extra=None, flags=()):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'], m['Message-ID'] = sender, 'cabinet@example.test', subject, mid
    if refs:
        m['In-Reply-To'] = refs
        m['References'] = refs
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    return Mail(uid, '7', 'INBOX', set(flags), when, m)


class FakeBox:
    appended = []

    def __init__(self, cfg):
        now = datetime.now(timezone.utc)
        self.folders = {
            'INBOX': [
                _mail('1', 'AR24 <no-reply@ar24.fr>', 'Lettre recommandée électronique en attente', 'Votre LRE est disponible jusqu’au 10/10/2026.', now - timedelta(hours=2), '<a1@x>'),
                _mail('2', 'Client Durand <durand@example.test>', 'Assemblée générale', 'Maître, pouvez-vous me répondre aujourd’hui sur le projet de PV ?', now - timedelta(hours=3), '<a2@x>'),
                _mail('3', 'Lettre <news@media.test>', 'Newsletter juridique', 'Actualités', now - timedelta(hours=4), '<a3@x>', extra={'List-Unsubscribe': '<mailto:u@x>'}),
                _mail('4', 'Banque <info@qonto.test>', 'Votre relevé de compte', 'Relevé', now - timedelta(hours=5), '<a4@x>'),
                _mail('5', 'Confrère Martin <martin@avocats.test>', 'Dossier BETA', 'Cher Confrère, merci de me transmettre vos pièces.', now - timedelta(hours=6), '<a5@x>'),
                _mail('6', 'Ancien <vieux@example.test>', 'Ancien message', 'Bonjour', now - timedelta(days=9), '<a6@x>'),
            ],
            'Sent': [_mail('9', 'cabinet@example.test', 'Re: Dossier BETA', 'Réponse', now - timedelta(hours=1), '<s1@x>', refs='<a5@x>')],
            'Drafts': []}

    def search(self, folder, *crit):
        return [m.uid for m in self.folders.get(folder, [])]

    def fetch(self, folder, uid, headers_only=False):
        return next(m for m in self.folders[folder] if m.uid == uid)

    def existing_draft(self, mail, mid=None):
        return any(d['Message-ID'] == mid for d in FakeBox.appended)

    def append_draft(self, msg):
        FakeBox.appended.append(msg)

    def close(self):
        pass


class FakeModel:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        if self.fail:
            raise Stop('fournisseur_ia_injoignable')
        if json_schema is not None:
            data = json.loads(messages[-1]['content'].split('\n\n', 1)[1])
            return json.dumps({'lignes': [{'uid': x['uid'], 'categorie': 'regle' if x['answered'] else ('urgent' if x['uid'] in ('1', '2') else 'info'),
                                           'resume': 'Résumé ' + x['uid'], 'reponse_attendue_aujourdhui': x['uid'] == '2'} for x in data]})
        return 'Madame, Monsieur,\n\nJe reviens vers vous rapidement. [À COMPLÉTER : réponse]'


class Routines(t510.Base):
    def setUp(self):
        super().setUp()
        FakeBox.appended = []
        self.f.c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test', 'from_name': 'Camille EXEMPLE'})
        for p in (patch('agent.mailbox.Mailbox', FakeBox), patch('agent.routines520._model', return_value=FakeModel())):
            p.start()
            self.addCleanup(p.stop)

    def test_schedule_runs_once_a_day_at_the_set_time_and_weekdays(self):
        paris = datetime(2026, 10, 5, 6, 50, tzinfo=r5.tz(self.desk))          # lundi
        self.assertEqual(r5.tick(self.desk, paris.replace(hour=6, minute=0)), [])   # premier passage : initialisation, aucun rattrapage
        self.assertEqual(r5.tick(self.desk, paris), [])
        self.assertEqual(r5.tick(self.desk, paris.replace(hour=7, minute=35)), ['tri', 'briefing'])
        self.assertEqual(r5.tick(self.desk, paris.replace(hour=9)), [])
        friday = datetime(2026, 10, 9, 18, 0, tzinfo=r5.tz(self.desk))
        self.assertIn('bilan', r5.tick(self.desk, friday))
        late = datetime(2026, 10, 6, 13, 0, tzinfo=r5.tz(self.desk))             # mardi 13 h : plus de 4 h après 7 h et 7 h 30
        self.assertEqual(r5.tick(self.desk, late), [])
        self.assertEqual(r5._since_last_sort(datetime(2026, 10, 5, 7, 0, tzinfo=r5.tz(self.desk))).isoformat()[:16], '2026-10-02T07:00')

    def test_tri_classifies_excludes_and_drafts_only_urgent_replies(self):
        r5.tri(self.desk)
        rep = r5.latest(self.desk, 'tri')
        data = json.loads(rep['data'])
        self.assertEqual({x['uid']: x['category'] for x in data['items']}, {'1': 'urgent', '2': 'urgent', '5': 'regle'})
        self.assertEqual(data['excluded'], 2)
        self.assertIn('🔴 Urgent', rep['text'])
        self.assertIn('2 message(s) exclu(s)', rep['text'])
        self.assertEqual(len(FakeBox.appended), 1)                           # aucun brouillon pour l'avis LRE automatique
        d = FakeBox.appended[0]
        self.assertEqual((d['To'], d['In-Reply-To']), ('durand@example.test', '<a2@x>'))
        self.assertIn('Avocat au Barreau de Lyon', d.get_content())
        self.assertIn('[À COMPLÉTER', d.get_content())
        r5.tri(self.desk)
        self.assertEqual(len(FakeBox.appended), 1)                           # jamais deux brouillons pour le même message

    def test_tri_without_model_and_without_mailbox(self):
        with patch('agent.routines520._model', return_value=FakeModel(fail=True)):
            r5.tri(self.desk)
        self.assertEqual(r5.latest(self.desk, 'tri')['status'], 'sans_ia')
        with patch('agent.mailbox.Mailbox', side_effect=Stop('connexion_imap_echouee')):
            r5.tri(self.desk)
        rep = r5.latest(self.desk, 'tri')
        self.assertEqual(rep['status'], 'echec')
        self.assertIn('n’a pas pu être fait ce matin', rep['text'])

    def test_briefing_agenda_rules_and_sources(self):
        from agent.workplan import ensure_schema
        ensure_schema(self.desk)
        today = datetime.now(r5.tz(self.desk)).date()
        at = lambda h: datetime.combine(today, dtime(h, 0), r5.tz(self.desk)).astimezone(timezone.utc).isoformat()
        for i, (title, loc) in enumerate((('Audience TJ Paris – DUPONT', 'Tribunal judiciaire de Paris'), ('Rendez-vous client', 'Cabinet, digicode 4512B'))):
            self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 ('c%d' % i, 'u%d' % i, '', 'cal', '/c%d.ics' % i, 'e', title, 'Code BAL 1234 secret', loc, at(9 + i), at(10 + i), 1, '', '[]', at(8)))
        self.desk.db.commit()
        with patch('agent.routines520.news', return_value={'numerique': [{'title': 'CNIL : sanction', 'url': 'https://www.cnil.fr/x', 'source': 'CNIL', 'date': '03/10'}]}):
            r5.briefing(self.desk, today)
        data = json.loads(r5.latest(self.desk, 'briefing')['data'])
        self.assertTrue(next(x for x in data['agenda'] if 'Paris' in x['title'])['hors_ressort'])
        self.assertFalse(next(x for x in data['agenda'] if 'Rendez-vous' in x['title'])['hors_ressort'])
        self.assertNotIn('4512', json.dumps(data, ensure_ascii=False))
        self.assertNotIn('Code BAL', json.dumps(data, ensure_ascii=False))
        self.assertEqual(data['actualite']['numerique'][0]['url'], 'https://www.cnil.fr/x')
        with patch('agent.routines520._model', return_value=FakeModel(fail=True)), patch('agent.mailbox.Mailbox', side_effect=Stop('x')), \
                patch('agent.routines520.news', side_effect=Stop('http_404')):
            r5.briefing(self.desk, today)
        rep = r5.latest(self.desk, 'briefing')
        self.assertIn('audience hors ressort, avocat postulant', rep['text'])
        self.assertTrue(any('messagerie n’a pas pu être lue' in n for n in rep['notes']))
        self.assertTrue(any('Actualité non disponible' in n for n in rep['notes']))

    def test_bilan_falls_back_without_model(self):
        with patch('agent.routines520._model', return_value=FakeModel(fail=True)), patch('agent.routines520._nextcloud_week', return_value=([], [])):
            r5.bilan(self.desk)
        rep = r5.latest(self.desk, 'bilan')
        self.assertIn('Bilan de la semaine du', rep['text'])
        self.assertIn('À venir la semaine prochaine', rep['text'])
        self.assertTrue(any('Dossier BETA' in x['objet'] for x in json.loads(rep['data'])['envoyes']))

    def test_documents_to_prepare_and_today_frames(self):
        from agent.workplan import ensure_schema
        ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('t9', 'x', 'cal', '/t.ics', 'e', 'DEMO', 'Rédiger les conclusions récapitulatives', '', '', '2026-10-20', 0, 0, 'todo', '', '', '', '', '{}', 'x', 'x', 'x'))
        self.desk.db.commit()
        docs = r5.documents(self.desk)
        self.assertEqual((docs[0]['kind'], docs[0]['matter']), ('conclusions', 'DEMO'))
        r5.tri(self.desk)
        body = self.request('/aujourdhui', query='vue=essentiel')['body']   # 5.3.0 : cadres 5.2 dans la vue « essentiel »
        for title in ('Briefing du matin', 'Tri matinal des courriels', 'Bilan de la semaine', 'Documents à préparer', 'Réglages des routines'):
            self.assertIn(title, body)
        self.assertIn('name="action" value="studio_prepare420"', body)
        self.assertIn('name="action" value="routine520"', body)
        self.assertIn('Résumé 2', body)

    def test_settings_validation_and_safe_rendering(self):
        with self.assertRaises(Stop):
            r5.save_settings(self.desk, {'briefing_time': '25:00'})
        s = r5.save_settings(self.desk, {'briefing_enabled': 'yes', 'briefing_time': '08:15', 'briefing_days': '12345', 'tri_enabled': 'yes', 'tri_time': '07:00',
                                         'tri_days': '12345', 'bilan_enabled': 'yes', 'bilan_time': '17:30', 'bilan_days': '5', 'ressort': 'Lyon', 'exclude': 'clipa\nqonto'})
        self.assertEqual((s['briefing']['time'], s['tri']['drafts'], s['exclude']), ('08:15', False, ['clipa', 'qonto']))
        html = r5.render_markdown('# Titre\n- <script>x</script> [lien](https://ok.test/a) [mauvais](javascript:alert(1))')
        self.assertNotIn('<script>', html)
        self.assertIn('href="https://ok.test/a"', html)
        self.assertNotIn('href="javascript', html)

    def test_routines_never_write_to_the_mailbox_except_drafts(self):
        src = (ROOT / 'agent' / 'routines520.py').read_text(encoding='utf-8')
        for forbidden in ('send490', 'smtplib', '.store(', "'STORE'", 'EXPUNGE', 'append_reminder', "'COPY'", 'MOVE'):
            self.assertNotIn(forbidden, src, forbidden)


if __name__ == '__main__':
    unittest.main()
