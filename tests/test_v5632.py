"""5.6.32 : deuxième livraison du cahier des charges additionnel — sources juridiques, contrôle des références, veille planifiée,
compétences, documents de procédure → agenda → brouillons, dossiers de travail, blocages de l'interface, compression des pièces,
tâches Nextcloud (412), habitudes de style utiles, tests métier quotidiens."""
import json
from pathlib import Path
import unittest

import test_desk
from agent.common import Stop, load_config
from agent.desk import Desk


class Base(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp
    request = test_desk.WebTests.request

    def setUp(self):
        self.setUp0()

    def desk(self):
        desk = Desk(load_config(str(self.config)))
        self.addCleanup(desk.db.close)
        return desk


class FakeDav:
    """Client WebDAV minimal : dossiers présents, créations enregistrées (jamais hors du dossier client)."""
    def __init__(self, root, present=()):
        self.root, self.present, self.created = root, set(present), []

    def list_folder(self, path):
        if path.rstrip('/') != self.root:
            raise Stop('http_404')
        return [{'path': self.root + '/' + n, 'directory': True} for n in sorted(self.present)]

    def ensure_folder(self, path, boundary):
        assert path.startswith(boundary + '/'), (path, boundary)
        self.created.append(path)
        self.present.add(path.rsplit('/', 1)[-1])
        return path


# --------------------------------------------------------------------------------------------- dossiers de travail
class WorkingFolders(Base):
    M = {'id': 'm-test', 'path': '/Cabinet/Clients/DOSSIER TEST', 'client_name': 'Société Exemple'}

    def _parties(self, desk, *names):
        from agent import metier500 as m5
        m5.ensure_schema(desk)
        for i, n in enumerate(names):
            desk.db.execute('INSERT INTO conflicts500_parties VALUES(?,?,?,?,?,?,?,?)',
                            ('p%d' % i, self.M['id'], n, n.lower(), 'adverse', '', 'saisie', '2026-10-11T10:0%d:00+00:00' % i))
        desk.db.commit()

    def test_defaults_include_our_and_adverse_exhibits(self):
        from agent import dossiers5632 as d
        desk = self.desk()
        patterns = [x['pattern'] for x in d.configured(desk)]
        self.assertEqual(patterns, ['NOS PIECES', 'PIECES ADVERSES - {{partie_adverse01}}', 'PIECES ADVERSES - {{partie_adverse02}}',
                                    'PIECES ADVERSES - {{partie_adverse03}}'])

    def test_placeholders_are_resolved_from_the_adverse_parties_in_order(self):
        from agent import dossiers5632 as d
        desk = self.desk()
        self._parties(desk, 'SARL Alpha', 'M. Beta / Gamma')
        rows = d.resolve(desk, self.M)
        self.assertEqual([r['name'] for r in rows], ['NOS PIECES', 'PIECES ADVERSES - SARL Alpha', 'PIECES ADVERSES - M. Beta Gamma', None])
        self.assertEqual(rows[1]['path'], '/Cabinet/Clients/DOSSIER TEST/PIECES ADVERSES - SARL Alpha')
        self.assertIn('partie adverse n° 3', rows[3]['reason'])                      # 3e partie absente : signalée, rien d'inventé
        self.assertEqual(rows[1]['label'], 'Pièces adverses — SARL Alpha')

    def test_usage_of_recognises_folder_written_differently(self):
        from agent import dossiers5632 as d
        desk = self.desk()
        self._parties(desk, 'SARL Alpha')
        base = self.M['path']
        self.assertEqual(d.usage_of(desk, self.M, base + '/NOS PIECES/Pièce 1.pdf')['usage'], 'nos_pieces')
        self.assertEqual(d.usage_of(desk, self.M, base + '/Nos Pièces/Pièce 2.pdf')['usage'], 'nos_pieces')   # casse et accents
        other = d.usage_of(desk, self.M, base + '/PIECES ADVERSES - ALPHA SARL/conclusions.pdf')
        self.assertEqual((other['usage'], other['label']), ('pieces_adverses', 'Pièces adverses — ALPHA SARL'))
        self.assertIsNone(d.usage_of(desk, self.M, base + '/PROCEDURE/assignation.pdf'))
        self.assertIsNone(d.usage_of(desk, self.M, base + '/lettre.pdf'))
        self.assertIsNone(d.usage_of(desk, self.M, '/Cabinet/Clients/AUTRE/NOS PIECES/x.pdf'))

    def test_settings_add_remove_and_validate(self):
        from agent import dossiers5632 as d
        from agent import style550
        desk = self.desk()
        out = style550.save_settings(desk, {'mails': True, 'auto': True, 'extra_pattern_0': 'NOS PIECES', 'extra_usage_0': 'nos_pieces',
                                            'extra_pattern_1': '', 'extra_usage_1': 'pieces_adverses',
                                            'extra_pattern_2': 'NOTES INTERNES', 'extra_usage_2': 'ecrits'})
        self.assertEqual(out['extra_folders'], [{'pattern': 'NOS PIECES', 'usage': 'nos_pieces'}, {'pattern': 'NOTES INTERNES', 'usage': 'ecrits'}])
        self.assertEqual(d.configured(desk), out['extra_folders'])
        for bad, code in (('../ETC', 'nom_sous_dossier_invalide'), ('A/B', 'nom_sous_dossier_invalide'), ('X {{inconnue}}', 'variable_dossier_inconnue')):
            with self.assertRaises(Stop) as ctx:
                style550.save_settings(desk, {'extra_pattern_0': bad, 'extra_usage_0': 'travail'})
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(Stop) as ctx:
            style550.save_settings(desk, {'extra_pattern_0': 'NOS PIECES', 'extra_usage_0': 'travail', 'extra_pattern_1': 'nos pièces', 'extra_usage_1': 'travail'})
        self.assertEqual(str(ctx.exception), 'dossier_de_travail_en_double')
        style550.save_settings(desk, {'extra_pattern_0': '', 'extra_usage_0': 'travail'})      # tout retiré : liste vide, pas les valeurs par défaut
        self.assertEqual(d.configured(desk), [])
        style550.save_settings(desk, {'mails': True})                                          # autre formulaire : liste conservée
        self.assertEqual(d.configured(desk), [])

    def test_only_cabinet_writings_feed_style_learning(self):
        from agent import dossiers5632 as d
        desk = self.desk()
        d.save(desk, {'extra_pattern_0': 'NOS PIECES', 'extra_usage_0': 'nos_pieces', 'extra_pattern_1': 'NOTES', 'extra_usage_1': 'ecrits',
                      'extra_pattern_2': 'PIECES ADVERSES - {{partie_adverse01}}', 'extra_usage_2': 'pieces_adverses'})
        self.assertEqual(d.style_folders(desk, self.M), ['NOTES'])

    def test_create_missing_only_on_request_inside_the_matter(self):
        from agent import dossiers5632 as d
        desk = self.desk()
        self._parties(desk, 'SARL Alpha')
        dav = FakeDav(self.M['path'], present=['NOS PIECES', 'PROCEDURE'])
        state = {r['pattern']: r['exists'] for r in d.state(desk, self.M, dav)}
        self.assertEqual(state['NOS PIECES'], True)
        self.assertEqual(state['PIECES ADVERSES - {{partie_adverse01}}'], False)
        result = d.create_missing(desk, self.M, dav)
        self.assertEqual(result['created'], ['PIECES ADVERSES - SARL Alpha'])            # parties 2 et 3 absentes : rien d'inventé
        self.assertEqual(dav.created, ['/Cabinet/Clients/DOSSIER TEST/PIECES ADVERSES - SARL Alpha'])
        self.assertEqual(d.create_missing(desk, self.M, dav)['created'], [])              # deuxième clic : aucun doublon

    def test_document_rules_may_file_into_a_working_folder(self):
        from agent import automation568 as rules
        base = {'names': ['CONCLUSIONS'], 'types': [], 'actions': ['analyze', 'organize'] if 'organize' in rules.ACTIONS else ['analyze'],
                'mode': 'organize'}
        ok = rules.validate_recipe({**base, 'folder': 'PIECES ADVERSES - {{partie_adverse01}}'})
        self.assertEqual(ok['folder'], 'PIECES ADVERSES - {{partie_adverse01}}')
        for bad in ('../PIECES', 'A/B', 'X {{autre}}'):
            with self.assertRaises(Stop):
                rules.validate_recipe({**base, 'folder': bad})

    def test_style_settings_page_lists_working_folders(self):
        from agent import style550_ui
        desk = self.desk()
        html = style550_ui.page(desk, '/agent-courriel', lambda title, body, *a, **k: body, 'csrf')
        self.assertIn('Autres dossiers de travail', html)
        self.assertIn('value="PIECES ADVERSES - {{partie_adverse02}}"', html)
        self.assertIn('name="extra_pattern_4"', html)                                     # ligne vide pour en ajouter un
        self.assertIn('data-s5632-add', html)


# --------------------------------------------------------------------------------------------- résumé oral du briefing
BRIEF = """**BRIEFING DU MATIN – 11/10/2026**

## Aujourd’hui
- Aucune audience ni rendez-vous planifié.

## Demain
- **09:00 – 10:00** : DUPONT - MARTIN – TJ VILLEFRANCHE (Audience hors ressort, tenue par un avocat postulant).
- **09:00 – 10:00** : DURAND - ASSUREUR FICTIF – CA LYON (Date limite des conclusions appelant).
- **11:30 – 12:00** : Rendez-vous – association Exemple.

## À traiter
- **Client Alpha** (10/10) : Demande d'explication sur une reconnaissance de dette de 3 444,78 € (signal urgent).
- **Formulaire AxiorHub #12** (11/10) : Demande d'information de M. Test (test@example.org).

## Réglé
- **Lettre de mission** (10/10) : Réponse envoyée précisant l'honoraire de 960 € TTC.
"""


class FakeModel:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def complete(self, messages, **kw):
        self.calls += 1
        assert 'DONNÉE' in messages[0]['content'] and 'BRIEFING DU MATIN' in messages[1]['content']
        return self.text


class OralBriefing(Base):
    def _report(self, desk, text=BRIEF, status='ok'):
        from agent import routines520
        return routines520._store(desk, 'briefing', '2026-10-11', text, {}, [], status)

    def test_rules_version_is_spoken_prose_with_the_same_facts(self):
        from agent.oral5632 import compose, check
        out = compose(BRIEF, '2026-10-11')
        self.assertTrue(out.startswith('Bonjour.'))
        self.assertIn('Pour aujourd’hui, dimanche 11 octobre : aucune audience', out)
        self.assertIn('Demain, lundi 12 octobre', out)
        self.assertIn('De 9 heures à 10 heures, deux rendez-vous ou échéances sont programmés en même temps', out)
        self.assertIn('devant le tribunal judiciaire de Villefranche', out)
        self.assertIn('devant la cour d’appel de Lyon', out)
        self.assertIn('Enfin, de 11 h 30 à midi', out)
        self.assertIn('D’abord, Client Alpha, le 10 octobre : demande d\'explication', out)
        self.assertIn('3 444,78 euros', out)
        self.assertIn('Pour mémoire, déjà réglé', out)
        self.assertNotIn('@', out)                                                       # adresses électroniques jamais lues
        self.assertNotRegex(out, r'(?m)^\s*[-*#]|\*\*')
        self.assertEqual(check(BRIEF, out, '2026-10-11'), [])

    def test_check_rejects_invented_numbers_and_bullets(self):
        from agent.oral5632 import check
        self.assertEqual(check(BRIEF, 'Bonjour. À 9 heures, audience ; 960 euros TTC. Demain 12 octobre.', '2026-10-11'), [])
        self.assertIn('nombre absent du briefing : 2500', check(BRIEF, 'Bonjour. Un honoraire de 2 500 euros.', '2026-10-11'))
        self.assertIn('puces ou titres restants', check(BRIEF, 'Bonjour.\n- point', '2026-10-11'))

    def test_get_caches_and_queues_ai_once_then_job_accepts_a_checked_version(self):
        from unittest.mock import patch
        from agent import oral5632
        desk = self.desk()
        rid = self._report(desk)
        first = oral5632.get(desk, rid)
        self.assertEqual((first['method'], first['pending']), ('regles', True))
        self.assertEqual(desk.db.execute("SELECT count(*) FROM jobs WHERE kind='briefing_oral5632'").fetchone()[0], 1)
        oral5632.get(desk, rid)
        self.assertEqual(desk.db.execute("SELECT count(*) FROM jobs WHERE kind='briefing_oral5632'").fetchone()[0], 1)   # pas de doublon
        good = FakeModel('Bonjour. Aujourd’hui, rien n’est prévu. Demain, de 9 heures à 10 heures, deux audiences ; à 11 h 30, un rendez-vous. '
                         'D’abord, Client Alpha demande des explications sur 3 444,78 euros.')
        with patch('agent.routines520._model', return_value=good):
            self.assertEqual(oral5632.job(desk, {'report': rid})['method'], 'ia')
        out = oral5632.get(desk, rid)
        self.assertEqual(out['method'], 'ia')
        self.assertIn('3 444,78 euros', out['text'])

    def test_job_keeps_rules_version_when_the_ai_invents(self):
        from unittest.mock import patch
        from agent import oral5632
        desk = self.desk()
        rid = self._report(desk)
        oral5632.get(desk, rid)
        with patch('agent.routines520._model', return_value=FakeModel('Bonjour. Audience à 15 h 20, provision de 4 000 euros.')):
            self.assertEqual(oral5632.job(desk, {'report': rid})['method'], 'regles')
        out = oral5632.get(desk, rid)
        self.assertEqual(out['method'], 'regles')
        self.assertIn('Version par règles conservée', out['notes'][0])
        self.assertFalse(out['pending'])

    def test_briefing_without_ai_does_not_queue(self):
        from agent import oral5632
        desk = self.desk()
        rid = self._report(desk, status='sans_ia')
        self.assertFalse(oral5632.get(desk, rid)['pending'])

    def test_today_page_offers_oral_summary_and_listen_buttons(self):
        from agent import cockpit530
        desk = self.desk()
        rid = self._report(desk)
        html = cockpit530.routines_html(desk, '/agent-courriel')
        self.assertIn('data-o5632-report="%s"' % rid, html)
        self.assertIn('data-o5632="show"', html)
        self.assertIn('data-o5632="play"', html)


# --------------------------------------------------------------------------------------------- messagerie : signature, police, règles
import test_v530 as t530  # noqa: E402
import test_v5628 as t5628  # noqa: E402


class MailSettings(t530.Base):
    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        t5628.WebBox.reset()
        for c in (self.f.c, self.desk.c):
            c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet EXEMPLE',
                              'own_addresses': ['cabinet@example.test'], 'signature': 'Maître EXEMPLE\nAvocat'})
        for target in ('agent.mailbox.Mailbox', 'agent.send490.Mailbox', 'agent.drafts440.Mailbox'):
            p = patch(target, t5628.WebBox)
            p.start()
            self.addCleanup(p.stop)

    def test_signature_defaults_to_mail_signature_and_is_placed_above_the_quote(self):
        from agent import webmail5628 as w
        new = w.template(self.desk, 'nouveau')
        self.assertTrue(new['html'].startswith('<p><br></p><p><br></p>Maître EXEMPLE<br>Avocat'))
        self.assertIn('font-family:Arial', new['font'])
        reply = w.template(self.desk, 'repondre', '1')
        self.assertLess(reply['html'].index('Maître EXEMPLE'), reply['html'].index('<blockquote>'))   # signature au-dessus du message cité
        fwd = w.template(self.desk, 'transferer', '2')
        self.assertLess(fwd['html'].index('Maître EXEMPLE'), fwd['html'].index('Message transféré'))

    def test_preferences_font_and_signature_choices(self):
        from agent import courrier5632 as c, webmail5628 as w
        out = c.save_prefs(self.desk, {'signature_html': '<p><b>Camille EXEMPLE</b><script>x</script></p>', 'font': 'times', 'size': '12',
                                       'sign_nouveau': True, 'sign_repondre': True, 'sign_transferer': False})
        self.assertEqual(out['signature_html'], '<p><b>Camille EXEMPLE</b></p>')                    # nettoyée
        self.assertIn('Times New Roman', c.font_css(self.desk))
        self.assertNotIn('Camille', w.template(self.desk, 'transferer', '2')['html'])                 # pas de signature sur les transferts
        self.assertIn('Camille', w.template(self.desk, 'repondre_tous', '1')['html'])
        for bad in ({'font': 'comic'}, {'font': 'arial', 'size': '40'}):
            with self.assertRaises(Stop):
                c.save_prefs(self.desk, bad)
        saved = w.save(self.desk, {'mode': 'nouveau', 'to': 'tiers@example.test', 'subject': 'Essai', 'html': '<p>Bonjour</p>'})
        from email import policy
        from email.parser import BytesParser
        msg = BytesParser(policy=policy.default).parsebytes(t5628.WebBox.store['Drafts'][int(saved['uid'])][1])
        self.assertIn('<div style="font-family:&quot;Times New Roman&quot;, Times, serif;font-size:12pt"><p>Bonjour</p></div>',
                      msg.get_body(preferencelist=('html',)).get_content())

    def test_bcc_rule_prefills_compose_and_agent_drafts(self):
        from email.message import EmailMessage
        from agent import courrier5632 as c, webmail5628 as w
        c.save_rule(self.desk, {'kind': 'cci', 'addresses': 'archives@example.test, Archives@example.test; suivi@example.test',
                                'scope_nouveau': True, 'scope_agent': True})
        self.assertEqual(w.template(self.desk, 'nouveau')['bcc'], 'archives@example.test, suivi@example.test')
        self.assertEqual(w.template(self.desk, 'repondre', '1')['bcc'], '')                         # hors du périmètre choisi
        draft = EmailMessage()
        draft['To'], draft['Cc'] = 'client@example.test', 'suivi@example.test'
        c.add_agent_bcc(self.desk.c['state_dir'], draft)
        self.assertEqual(draft['Bcc'], 'archives@example.test')                                     # pas de doublon avec Cc
        with self.assertRaises(Stop):
            c.save_rule(self.desk, {'kind': 'cci', 'addresses': 'pas-une-adresse'})
        rid = c.rules(self.desk, 'cci')[0]['id']
        c.set_enabled(self.desk, rid, False)
        self.assertEqual(w.template(self.desk, 'nouveau')['bcc'], '')
        draft2 = EmailMessage()
        draft2['To'] = 'client@example.test'
        self.assertIsNone(c.add_agent_bcc(self.desk.c['state_dir'], draft2)['Bcc'])

    def test_move_rule_moves_only_messages_already_read_by_the_agent(self):
        from datetime import datetime, timezone
        from agent import courrier5632 as c
        from agent.common import digest
        from agent.state import State
        with self.assertRaises(Stop):
            c.save_rule(self.desk, {'kind': 'deplacer', 'cond_field_0': 'subject', 'cond_value_0': 'pièces', 'folder': 'Inexistant'},
                        folders=['INBOX', 'INBOX.Clients'])
        c.save_rule(self.desk, {'kind': 'deplacer', 'cond_field_0': 'subject', 'cond_value_0': 'PIECES', 'cond_field_1': 'from',
                                'cond_value_1': 'avocats.test', 'match': 'all', 'folder': 'INBOX.Clients'}, folders=['INBOX', 'INBOX.Clients'])
        rule = c.rules(self.desk, 'deplacer')[0]
        self.assertIn('l’objet contient « PIECES » et l’expéditeur contient « avocats.test »', c.describe(rule))
        box = t5628.WebBox(self.desk.c['mail'])
        early = datetime(2026, 10, 9, 9, 5, tzinfo=timezone.utc)                                   # message reçu il y a 5 minutes, non lu par l'agent
        self.assertEqual(c.apply_moves(self.desk, box, include_old=True, now=early)['moved'], 0)
        account = self.desk.c['mail']['username'] + '@' + self.desk.c['mail']['host']
        st = State(self.desk.c['state_dir'])
        mail = box.fetch('INBOX', '2', headers_only=True)
        st.set(mail.key(account), digest('m'), digest('t'), 'drafted')
        st.db.close()
        out = c.apply_moves(self.desk, box, include_old=True, now=early)
        self.assertEqual((out['moved'], sorted(t5628.WebBox.store['INBOX'])), (1, [1]))            # « Pièces » du confrère déplacé ; l'autre reste
        self.assertEqual(len(t5628.WebBox.store['INBOX.Clients']), 1)
        self.assertEqual(c.log(self.desk)[0]['state'], 'deplace')
        later = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)                                  # passé 15 minutes : déplacé même sans lecture
        c.save_rule(self.desk, {'kind': 'deplacer', 'cond_field_0': 'any', 'cond_value_0': 'durand', 'folder': 'Spam'}, folders=['Spam'])
        self.assertEqual(c.apply_moves(self.desk, box, include_old=True, now=later)['moved'], 1)
        self.assertEqual(t5628.WebBox.store['INBOX'], {})

    def test_schedule_only_with_an_active_move_rule(self):
        from agent import courrier5632 as c
        self.assertEqual(c.schedule(self.desk, 1000.0), [])
        c.save_rule(self.desk, {'kind': 'deplacer', 'cond_field_0': 'subject', 'cond_value_0': 'facture', 'folder': 'INBOX.Clients'})
        self.assertEqual(len(c.schedule(self.desk, 2000.0)), 1)
        self.assertEqual(c.schedule(self.desk, 2100.0), [])                                         # au plus toutes les 5 minutes

    def test_settings_page_section(self):
        from agent import web5632
        html = web5632.courrier_section(self.desk, '/agent-courriel')
        for needle in ('data-m5632="courrier/prefs"', 'data-m5632-html="signature_html"', 'Times New Roman', 'name="scope_agent"',
                       'value="INBOX.Clients"', 'Appliquer les règles maintenant'):
            self.assertIn(needle, html)
        self.assertNotIn(' style="', html)                                                          # politique de sécurité : aucun attribut style


# --------------------------------------------------------------------------------------------- fiabilité : blocages, 412, IA, audit
class Reliability(Base):
    def test_desk_opens_while_another_process_holds_a_write_lock(self):
        import sqlite3
        import time
        desk = self.desk()                                  # schéma déjà préparé dans ce processus
        path = desk.c['state_dir'] + '/desk.sqlite3'
        holder = sqlite3.connect(path, timeout=1)
        holder.execute('BEGIN IMMEDIATE')
        holder.execute("INSERT OR REPLACE INTO settings VALUES('verrou-essai','1')")
        try:
            from agent.common import set_request_mode
            set_request_mode('read')
            start = time.monotonic()
            other = Desk(load_config(str(self.config)))      # page affichée pendant l'écriture : aucune écriture à l'ouverture
            self.addCleanup(other.db.close)
            self.assertLess(time.monotonic() - start, 2.5)
            self.assertFalse(other.soft_write("INSERT OR REPLACE INTO settings VALUES('cache','1')"))   # écriture facultative sautée
        finally:
            holder.rollback()
            holder.close()
            set_request_mode('')
        self.assertTrue(other.soft_write("INSERT OR REPLACE INTO settings VALUES('cache','1')"))

    def test_locked_database_gives_503_with_automatic_reload(self):
        import sqlite3
        from agent import web
        try:
            raise sqlite3.OperationalError('database is locked')
        except sqlite3.OperationalError:
            status, kind, body = web.internal_error_response({'PATH_INFO': '/aujourdhui', 'REQUEST_METHOD': 'GET'}, 'text/html; charset=utf-8', '')
        self.assertTrue(status.startswith('503'))
        self.assertIn('http-equiv="refresh" content="3', body)   # 5.6.36 : rechargement de la même adresse (« 3;url=… »)
        self.assertNotIn('Interface indisponible', body)

    def test_open_transaction_is_committed_before_a_network_call(self):
        from agent.common import OPEN_TRANSACTIONS, release_before_network, track_connection
        desk = self.desk()
        track_connection(desk)
        desk.db.execute("INSERT OR REPLACE INTO settings VALUES('avant-reseau','1')")
        self.assertTrue(desk.db.in_transaction)
        before = OPEN_TRANSACTIONS['count']
        release_before_network('essai')
        self.assertFalse(desk.db.in_transaction)
        self.assertEqual(OPEN_TRANSACTIONS['count'], before + 1)

    def test_http_requests_carry_an_axiorhub_user_agent(self):
        from agent import __version__
        from agent.common import HTTP
        seen = {}

        class Resp:
            status = 200
            headers = {}

            def read(self, n=-1):
                return b'{}'

            def getheaders(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        from unittest.mock import patch

        def fake_open(req, *a, **k):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return Resp()
        with patch('urllib.request.OpenerDirector.open', side_effect=fake_open):
            try:
                HTTP('https://justicelibre.org').request('GET', 'https://justicelibre.org/mcp')
            except Exception:   # noqa: BLE001 — seule l'en-tête envoyée compte ici
                pass
        self.assertTrue(seen.get('user-agent', '').startswith('AxiorHub-Pilote/' + __version__), seen)

    def test_model_refuses_oversized_input_and_retries_a_cut_answer_once(self):
        from unittest.mock import patch
        from agent.model import Model
        from types import SimpleNamespace
        m = Model.__new__(Model)                                                         # sans appel au service Ollama
        m.cfg, m.provider_type, m.http = {'model': 'essai', 'num_ctx': 4096}, 'ollama', SimpleNamespace(json=None)
        with self.assertRaisesRegex(Stop, 'contexte_ia_insuffisant'):
            m._leaf([{'role': 'user', 'content': 'x' * 30000}], 0, 500, None)
        calls = []

        def fake(method, path, req):
            calls.append(req['options']['num_predict'])
            if len(calls) == 1:
                return {'done': True, 'done_reason': 'length', 'prompt_eval_count': 200, 'eval_count': 300, 'message': {'content': 'coupé'}}
            return {'done': True, 'done_reason': 'stop', 'prompt_eval_count': 200, 'eval_count': 600, 'message': {'content': 'complet'}}
        with patch.object(m.http, 'json', side_effect=fake):
            out = m._leaf([{'role': 'user', 'content': 'Bonjour'}], 0, 300, None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], 750)                                                 # 2,5 × la longueur demandée
        self.assertIn('complet', json.dumps(out, ensure_ascii=False))

    def test_audit_chain_check_is_incremental(self):
        from datetime import datetime, timezone
        from agent import cabinet_pilot as cp
        desk = self.desk()
        cp.ensure_schema(desk)
        for i in range(5):
            cp.append_audit(desk, datetime.now(timezone.utc).isoformat(), 'essai', {'n': i}, commit=True)
        first = cp.audit_verify(desk)
        self.assertTrue(first['valid'] and first['full'])
        cp.append_audit(desk, datetime.now(timezone.utc).isoformat(), 'essai', {'n': 9}, commit=True)
        second = cp.audit_verify(desk)
        self.assertEqual((second['valid'], second['full'], second['checked']), (True, False, 1))   # seul le nouveau maillon est relu
        desk.db.execute("UPDATE audit_chain_v290 SET payload='{\"n\":99}' WHERE seq=(SELECT MAX(seq) FROM audit_chain_v290)")
        desk.db.commit()
        cp.append_audit(desk, datetime.now(timezone.utc).isoformat(), 'essai', {'n': 10}, commit=True)
        desk.setting('audit_chain:checkpoint5632', {})                                   # relecture complète : l'altération est trouvée
        self.assertFalse(cp.audit_verify(desk)['valid'])

    def test_business_tests_run_daily(self):
        from agent import economie569
        self.assertEqual(economie569.INTERVALS['run_continuous_business_tests'] if hasattr(economie569, 'INTERVALS') else
                         [v for k, v in vars(economie569).items() if isinstance(v, dict) and 'run_continuous_business_tests' in v][0]['run_continuous_business_tests'],
                         (1440, 1440))

    def test_content_asides_no_longer_take_the_sidebar_style(self):
        css = (Path(__file__).resolve().parents[1] / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        guard = css.index('main aside:where(:not([id])){position:static')
        self.assertGreater(guard, css.rindex('aside{background:var(--ws-sidebar)'))       # après les règles génériques de la barre latérale
        from agent import production420_ui
        self.assertIn('<aside class="studio420-estimate">', Path(production420_ui.__file__).read_text(encoding='utf-8'))

    def test_functions_panel_current_item_stays_readable_in_light_theme(self):
        css = (Path(__file__).resolve().parents[1] / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('#ws-sidebar .f5631-panel li a[aria-current=page],.f5631-panel li a[aria-current=page]{background:rgba(31,78,156,.12)!important;'
                      'color:var(--g31-ink)!important', css)


class Tasks412(Base):
    class Dav:
        def __init__(self, remote):
            self.remote, self.puts = remote, []

        def put_todo(self, url, uid, title, desc, starts, due, status, prio, pct, etag):
            self.puts.append(etag)
            if etag != self.remote['etag']:
                raise Stop('http_412')
            self.remote.update(status=status, title=title, etag='"v%d"' % (len(self.puts) + 1))
            return {'etag': self.remote['etag']}

        def todos(self, urls, tz, limit):
            return [dict(self.remote)]

    def _rows(self, desk, n, etag):
        from agent import workplan
        workplan.ensure_schema(desk) if hasattr(workplan, 'ensure_schema') else None
        for i in range(n):
            desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            ('t%d' % i, 'axiorhub-uid-1', 'https://cloud/t/', '', etag, '', 'Relire', '', '', '', 15, 5, 'todo', '', '', '', '', 'test',
                             '2026-10-11', '2026-10-11', '2026-10-11'))
        desk.db.commit()

    def test_stale_version_is_reconciled_and_all_copies_change_state(self):
        from agent import workplan
        desk = self.desk()
        self._rows(desk, 3, '"v1"')                                                          # même tâche présente trois fois
        dav = self.Dav({'uid': 'axiorhub-uid-1', 'etag': '"v2"', 'status': 'NEEDS-ACTION', 'title': 'Relire'})   # cochée ailleurs entre-temps
        workplan.update_task_status(desk, {'task': 't0', 'status': 'cancelled'}, dav=dav)
        self.assertEqual(dav.puts, ['"v1"', '"v2"'])                                          # 412 puis une seule nouvelle tentative
        rows = desk.db.execute("SELECT status, etag FROM work_tasks_v211 WHERE external_uid='axiorhub-uid-1'").fetchall()
        self.assertEqual({tuple(r) for r in rows}, {('cancelled', '"v3"')})
        again = self.Dav({'uid': 'axiorhub-uid-1', 'etag': '"v9"', 'status': 'CANCELLED', 'title': 'Relire'})   # déjà annulée ailleurs : 412 sans erreur
        workplan.update_task_status(desk, {'task': 't1', 'status': 'cancelled'}, dav=again)
        self.assertEqual(again.puts, ['"v3"'])


class StyleHabits(Base):
    def _docs(self, desk, n, headings, words):
        from agent import style550
        style550.ensure_schema(desk)
        for i in range(n):
            feats = {'v': 2, 'words': words + i * 10, 'headings': headings, 'numbering': 'romains', 'subnumbering': 'lettres',
                     'citation_style': 'Cass. civ. 1re', 'dispositif': 'PAR CES MOTIFS', 'opening': '', 'closing': ''}
            desk.db.execute('INSERT OR REPLACE INTO style550_docs VALUES(?,?,?,?,?,?,?,?,?,?)', ('d%d' % i, 'fichier', '/x/%d.docx' % i, 'm', 'conclusions', 'e',
                                                                                              feats['words'], json.dumps(feats), '', '2026-10-11'))
        desk.db.commit()

    def test_trivial_plan_and_length_are_not_proposed_useful_habits_are(self):
        from agent import style550
        desk = self.desk()
        self._docs(desk, 5, ['POUR', 'CONTRE'], 400)
        style550.propose(desk)
        texts = [r['text'] for r in desk.db.execute("SELECT text FROM style550_habits WHERE status='proposee'")]
        self.assertFalse(any('POUR' in t or 'Longueur' in t for t in texts), texts)
        self.assertTrue(any('chiffres romains' in t for t in texts), texts)
        self.assertTrue(any('Cass. civ. 1re' in t for t in texts), texts)
        self.assertTrue(any('PAR CES MOTIFS' in t for t in texts), texts)
        self.assertEqual(desk.settings('style550:length:conclusions', None)['ecrits'], 5)     # longueur : repère automatique


# --------------------------------------------------------------------------------------------- lot 7 : écrans métier traduits
class ScreenTranslation(unittest.TestCase):
    def test_texts_attributes_numbers_and_breadcrumb_are_translated(self):
        from agent import ecrans5632 as e
        html = ('<h1>Aujourd’hui</h1><label>Racines autorisées (une par ligne)<textarea>Aujourd’hui</textarea></label>'
                '<input placeholder="Nom ou besoin : bordereau, délai, assignation…"><button title="Fermer">Fermer</button>'
                '<p>6 tâche(s) ouverte(s) dont</p><span>Workspace / Vérification des citations</span><p>Page : /dossiers</p>'
                '<script>var a = "Aujourd’hui";</script><p>Client Durand</p>')
        en = e.translate_html(html, 'en')
        for needle in ('<h1>Today</h1>', 'Authorised roots (one per line)<textarea>Aujourd’hui</textarea>', 'placeholder="Name or need: exhibit list, deadline, summons…"',
                       'title="Close">Close</button>', '<p>6 open task(s), of which</p>', 'Workspace / Citation check', 'Page: /dossiers',
                       'var a = "Aujourd’hui";', '<p>Client Durand</p>'):
            self.assertIn(needle, en)
        self.assertIn('<h1>Heute</h1>', e.translate_html(html, 'de'))
        self.assertIn('<h1>Hoy</h1>', e.translate_html(html, 'es'))
        self.assertEqual(e.translate_html(html, 'fr'), html)                                  # français : page inchangée

    def test_dictionaries_cover_the_main_business_screens_in_three_languages(self):
        from agent import ecrans5632 as e
        must = ('Aujourd’hui', 'Courriels', 'Dossiers', 'Agenda et tâches', 'Paramètres', 'Enregistrer', 'Boîte de réception', 'Briefing du matin',
                'Veille juridique', 'Compétences de l’IA', 'Studio de production', 'Pièces et bordereaux', 'Temps et honoraires', 'Ajouter une tâche')
        for lang in ('en', 'de', 'es'):
            table = e.dictionary(lang)
            self.assertGreater(len(table), 2500, lang)
            self.assertEqual([m for m in must if m not in table], [], lang)
            self.assertTrue(all(k.count('#') == v.count('#') for k, v in table.items() if not k.startswith('_')), lang)   # nombres alignés

    def test_json_notification_message_is_translated_when_known(self):
        from agent import ecrans5632 as e
        body = '{"message": "Aucune règle.", "id": 3}'
        self.assertEqual(json.loads(e.apply(body, 'application/json; charset=utf-8', 'en'))['message'], 'No rule.')
        self.assertEqual(e.apply(body, 'application/json', 'fr'), body)
        self.assertEqual(e.apply(b'raw', 'text/html', 'en'), b'raw')


# --------------------------------------------------------------------------------------------- guide : étapes réelles 5.6.32
import test_v5631 as t5631  # noqa: E402


class GuideSteps(t5631.Web):
    def _session(self, desk, features):
        from agent import guide5631 as g
        return g.start(desk, 'cabinet', list(features))

    def test_agenda_billing_and_phone_steps_have_fields_and_tests(self):
        from unittest.mock import patch
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk, ('agenda', 'facturation', 'telephonie'))
        out = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'agenda',
                          {'urls': 'https://cloud.example.org/remote.php/dav/calendars/u/cabinet/\nhttps://cloud.example.org/remote.php/dav/calendars/u/audiences/'}, 'save')
        cal = [{'name': 'Cabinet', 'url': 'https://cloud.example.org/remote.php/dav/calendars/u/cabinet/'}]
        with patch('agent.config567.test', return_value={'ok': True, 'connector': 'nextcloud', 'message': 'ok', 'calendars': cal, 'writes': 0}):
            res = g.run_test(desk, 'cabinet', s['id'], 'agenda')
        self.assertEqual((res['results'][0]['status'], res['results'][0]['code']), ('refused', 'agenda_choisi_introuvable'))   # « audiences » inconnu
        out = g.save_step(desk, 'cabinet', s['id'], out['revision'], 'agenda', {'urls': cal[0]['url']}, 'save')
        with patch('agent.config567.test', return_value={'ok': True, 'connector': 'nextcloud', 'message': 'ok', 'calendars': cal, 'writes': 0}):
            res = g.run_test(desk, 'cabinet', s['id'], 'agenda')
        self.assertEqual(res['results'][0]['status'], 'available')
        self.assertEqual(res['results'][0]['evidence']['events_written'], 0)
        out = g.save_step(desk, 'cabinet', s['id'], out['revision'], 'facturation',
                          {'enabled': True, 'url': 'https://invoices.example.org', 'token': 'Jeton-Fictif-1'}, 'save')
        with patch('agent.config567.test', return_value={'ok': True, 'connector': 'invoice_ninja', 'message': 'Factures lues', 'writes': 0}):
            res = g.run_test(desk, 'cabinet', s['id'], 'facturation')
        self.assertEqual(res['results'][0]['status'], 'available')
        bad = g.save_step(desk, 'cabinet', s['id'], out['revision'], 'telephonie', {'callback': 'https://agent.example.org/autre', 'token': 'Jeton-Twilio-Fictif'}, 'save')
        res = g.run_test(desk, 'cabinet', s['id'], 'telephonie')
        self.assertEqual(res['results'][0]['code'], 'adresse_rappel_twilio_invalide')
        g.save_step(desk, 'cabinet', s['id'], bad['revision'], 'telephonie', {'callback': 'https://agent.example.org/agent-courriel/reception567/twilio'}, 'save')
        res = g.run_test(desk, 'cabinet', s['id'], 'telephonie')
        self.assertEqual((res['results'][0]['status'], res['results'][0]['evidence']['calls']), ('available', 0))

    def test_sources_and_watch_steps_show_the_real_settings(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk, ('recherches', 'veille', 'agenda'))
        body = self.request('/guide-installation', query='session=%s&etape=sources' % s['id'])['body']
        self.assertIn('data-provider="justicelibre"', body)
        body = self.request('/guide-installation', query='session=%s&etape=veille' % s['id'])['body']
        self.assertIn('data-m5632="veille/profile"', body)
        body = self.request('/guide-installation', query='session=%s&etape=agenda' % s['id'])['body']
        self.assertIn('<textarea rows="3" id="g5631-agenda-urls"', body)
        self.assertIn('data-connector="agenda"', body)


# --------------------------------------------------------------------------------------------- R08 : aide aux balises, modèle d'exemple
class TemplateHelp(Base):
    def test_example_template_passes_the_import_check_and_is_stable(self):
        from agent import cabinet_docs33, modeles5632
        raw = modeles5632.example_docx()
        info = cabinet_docs33.inspect_template(raw)
        self.assertEqual(set(info['placeholders']), cabinet_docs33.FIELDS)
        self.assertEqual(raw, modeles5632.example_docx())                                        # même fichier à chaque téléchargement

    def test_templates_page_shows_tag_help_and_the_download_is_served(self):
        body = self.request('/modeles-word')['body']
        self.assertIn('id="balises"', body)
        self.assertIn('<code>{{corps}}</code>', body)
        self.assertIn('/api440/m5632/modeles/exemple.docx', body)
        import base64
        import io
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel/api440/m5632/modeles/exemple.docx', 'QUERY_STRING': '',
               'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''),
               'CONTENT_LENGTH': '0', 'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        seen = {}
        raw = b''.join(self.app(env, lambda status, headers: seen.update(status=status, headers=dict(headers))))
        self.assertTrue(seen['status'].startswith('200'), seen['status'])
        self.assertIn('modele-courrier-exemple.docx', seen['headers'].get('Content-Disposition', ''))
        self.assertTrue(raw.startswith(b'PK'))


if __name__ == '__main__':
    unittest.main()
