"""4.9.0 : dictée à commandes, envoi facultatif, application mobile, recherche unique, ergonomie."""
import base64
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
import re
import shutil
import sqlite3
import statistics
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_agent as fixtures
from agent import mobile490, search490, send490, voice490
from agent.common import Stop, private_json
from agent.desk import Desk
from fake_imap440 import FakeIMAP
from test_v440_drafts import raw_draft, raw_inbound
from test_v440_web import WebWorkshopTests as Base0

try:
    import cryptography  # noqa: F401  (notifications : dépendance de l'image Docker, absente d'un Python nu)
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
NEEDS_CRYPTO = unittest.skipUnless(HAS_CRYPTO, 'bibliothèque cryptography absente : notifications indisponibles')

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'agent' / 'static'
TODAY = date(2026, 10, 3)
BODY = "Bonjour Maître,\n\nJe vous confirme le rendez-vous. Nous verrons le contrat.\n\nCordialement,\nMe X"
DEMO = {'id': 'DEMO', 'client_name': 'Client SECRETCO', 'path': '/Dossiers/DEMO', 'references': ['DEMO'], 'aliases': ['Client SECRETCO'],
        'correspondents': [{'email': 'confrere@example.test', 'role': 'client'}]}


# ======================================================================================= A. dictée
class Voice(unittest.TestCase):
    def go(self, text, body=BODY):
        return voice490.interpret(text, body, TODAY)

    def test_add_sentence_goes_before_the_closing_formula(self):
        r = self.go('Ajoute que je suis disponible le 12 octobre')
        self.assertEqual(r['mode'], 'replace_body')
        self.assertIn('Je suis disponible le 12 octobre.\n\nCordialement,\nMe X', r['new_body'])
        self.assertTrue(r['new_body'].startswith('Bonjour Maître,'))

    def test_incomplete_date_is_flagged_and_never_completed(self):
        r = self.go('ajoute que je suis disponible le 12')
        self.assertIn('Je suis disponible le 12.', r['new_body'])
        self.assertNotRegex(r['new_body'], r'12\s+(octobre|novembre|décembre)')
        self.assertEqual([w['code'] for w in r['warnings']], ['date_incomplete'])
        self.assertIn('mois n’est pas précisé', r['warnings'][0]['message'])

    def test_weekday_date_mismatch_is_flagged(self):
        r = self.go('ajoute que je suis disponible le mardi 14 octobre')   # 14/10/2026 est un mercredi, 14/10/2027 un jeudi
        self.assertEqual([w['code'] for w in r['warnings']], ['jour_incoherent'])
        ok = self.go('ajoute que je suis disponible le lundi 12 octobre')  # 12/10/2026 est un lundi
        self.assertEqual(ok['warnings'], [])

    def test_add_without_closing_formula_appends_at_the_end(self):
        r = self.go('ajoute la pièce est jointe', 'Bonjour,\n\nVoici le texte.')
        self.assertTrue(r['new_body'].endswith('La pièce est jointe.\n'))

    def test_replace_requires_a_unique_match(self):
        r = self.go('remplace rendez-vous par entretien')
        self.assertIn('Je vous confirme le entretien.', r['new_body'])
        with self.assertRaises(Stop) as ctx:
            self.go('remplace Nous par Vous', 'Nous voyons. Nous verrons.')
        self.assertEqual(str(ctx.exception), 'texte_a_remplacer_ambigu')
        with self.assertRaises(Stop) as ctx:
            self.go('remplace inexistant par autre')
        self.assertEqual(str(ctx.exception), 'texte_a_remplacer_introuvable')
        accent = self.go('remplace Maitre par Confrère')       # accents et casse ignorés pour trouver le texte
        self.assertIn('Bonjour Confrère,', accent['new_body'])

    def test_delete_last_sentence_keeps_greeting_and_closing(self):
        r = self.go('supprime la dernière phrase')
        self.assertEqual(r['new_body'], "Bonjour Maître,\n\nJe vous confirme le rendez-vous.\n\nCordialement,\nMe X")
        self.assertIn('Nous verrons le contrat.', r['summary'])
        with self.assertRaises(Stop):
            self.go('efface la dernière phrase', 'Bonjour,\n\nCordialement,\nMe X')

    def test_sending_cannot_be_ordered_by_voice(self):
        for phrase in ('Envoie le courriel', 'envoyer maintenant', 'Expédie', 'transmets au confrère', 'valide l’envoi'):
            r = self.go(phrase)
            self.assertEqual(r['mode'], 'refused', phrase)
            self.assertNotIn('new_body', r)
        self.assertEqual(self.go('enregistre')['action'], 'save')
        self.assertEqual(self.go('Enregistre le brouillon.')['action'], 'save')

    def test_undo_assist_and_plain_dictation(self):
        self.assertEqual(self.go('Annule')['action'], 'undo')
        a = self.go('reformule : plus courtois')
        self.assertEqual((a['mode'], a['instruction']), ('assist', 'plus courtois'))
        self.assertEqual(self.go('rends-le plus ferme')['instruction'], 'Rends-le plus ferme')
        plain = self.go('bonjour virgule merci de votre message point final')
        self.assertEqual((plain['mode'], plain['text']), ('insert', 'bonjour, merci de votre message.'))
        self.assertEqual(self.go('à la ligne')['text'], '\n')
        self.assertEqual(self.go('nouveau paragraphe')['text'], '\n\n')

    def test_a_command_word_inside_a_sentence_is_plain_dictation(self):
        r = self.go('je voudrais que vous ajoute que cela convient')
        self.assertEqual(r['mode'], 'insert')
        r = self.go("l'annulation de l'audience est confirmée")
        self.assertEqual(r['mode'], 'insert')

    def test_limits(self):
        with self.assertRaises(Stop):
            self.go('   ')
        with self.assertRaises(Stop):
            self.go('x' * 4000)

    def test_interpret_never_touches_the_network_or_state(self):
        with patch('socket.socket', side_effect=AssertionError('réseau interdit')):
            self.go('ajoute que tout va bien')


# ================================================================================ B. envoi facultatif
class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, cfg):
        self.cfg = cfg

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, message, from_addr=None, to_addrs=None):
        if FakeSMTP.fail:
            import smtplib
            raise smtplib.SMTPServerDisconnected('boom')
        FakeSMTP.sent.append({'message': message, 'from': from_addr, 'to': list(to_addrs)})
        return {}

    def quit(self):
        pass


class SendBase(unittest.TestCase):
    def setUp(self):
        FakeIMAP.reset()
        FakeSMTP.sent, FakeSMTP.fail = [], False
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        secret = self.f.base / 'imap.pw'
        secret.write_text('x')
        secret.chmod(0o600)
        self.f.c['mail'].update({'password_file': str(secret), 'port': 993, 'host': 'imap.test', 'username': 'u',
                                 'smtp': {'host': 'smtp.test', 'port': 587, 'security': 'starttls'}})
        private_json(Path(self.f.c['matters_file']), [DEMO, {**DEMO, 'id': 'AUTRE', 'client_name': 'Autre', 'path': '/Dossiers/AUTRE', 'references': ['AUTRE'], 'aliases': ['Autre']}])
        for p in (patch('agent.mailbox.imaplib.IMAP4_SSL', FakeIMAP), patch.object(send490, 'smtp_factory', FakeSMTP)):
            p.start()
            self.addCleanup(p.stop)
        FakeIMAP.add('INBOX', raw_inbound())
        self.uid = FakeIMAP.add('Drafts', raw_draft(), ['\\Draft'])
        self.desk = Desk(self.f.c)
        self.desk.db.execute("INSERT INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)",
                             ('a' * 64, 'draft_ready', 'DEMO', 'FIMEX - BERTIN : calendrier', 'confrere@example.test', '2026-10-02T08:00:00', '2026-10-02T09:00:00'))
        self.desk.db.commit()

    def enable(self, matter='DEMO'):
        send490.set_enabled(self.desk, True, 'yes')
        send490.set_matter(self.desk, matter, True, 'yes')

    def flow(self, delay=2.0):
        p = send490.prepare(self.desk, self.uid, '7')
        return p, send490.confirm(self.desk, p['token'], now=time.time() + delay)


class Send(SendBase):
    def test_disabled_by_default_and_nothing_is_ever_sent(self):
        st = send490.status(self.desk)
        self.assertFalse(st['enabled'])
        self.assertTrue(st['smtp_configured'])
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'envoi_desactive')
        self.assertEqual(FakeSMTP.sent, [])

    def test_enabling_needs_explicit_confirmation_and_a_smtp_server(self):
        with self.assertRaises(Stop) as ctx:
            send490.set_enabled(self.desk, True, '')
        self.assertEqual(str(ctx.exception), 'confirmation_envoi_requise')
        with self.assertRaises(Stop):
            send490.set_matter(self.desk, 'DEMO', True, '')
        del self.f.c['mail']['smtp']
        with self.assertRaises(Stop) as ctx:
            send490.set_enabled(self.desk, True, 'yes')
        self.assertEqual(str(ctx.exception), 'smtp_non_configure')

    def test_global_switch_is_not_enough_the_matter_must_be_enabled(self):
        send490.set_enabled(self.desk, True, 'yes')
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'envoi_non_active_pour_ce_dossier')
        send490.set_matter(self.desk, 'AUTRE', True, 'yes')          # un autre dossier ne suffit pas
        with self.assertRaises(Stop):
            send490.prepare(self.desk, self.uid, '7')

    def test_unidentified_matter_is_refused(self):
        self.enable()
        self.desk.db.execute('UPDATE work_items SET matter=?', ('',))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'envoi_dossier_non_identifie')

    def test_prepare_sends_nothing_and_shows_recipients_and_subject(self):
        self.enable()
        p = send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(FakeSMTP.sent, [])
        s = p['summary']
        self.assertIn('confrere@example.test', s['to'])
        self.assertEqual(s['subject'], 'Re: FIMEX - BERTIN : calendrier')
        self.assertEqual(s['recipient_count'], 1)
        self.assertGreaterEqual(p['min_delay'], 1)

    def test_second_click_sends_with_copy_in_sent_and_draft_removed(self):
        self.enable()
        p, r = self.flow()
        self.assertTrue(r['sent'] and r['copy_saved'] and r['draft_removed'])
        self.assertEqual(len(FakeSMTP.sent), 1)
        out = FakeSMTP.sent[0]
        self.assertEqual(out['to'], ['confrere@example.test'])
        self.assertEqual(out['from'], self.f.c['mail']['from_address'])
        msg = out['message']
        self.assertIsNone(msg['Bcc'])
        self.assertFalse([k for k in msg.keys() if k.lower().startswith('x-axiorhub')])      # aucune trace interne chez le destinataire
        self.assertNotIn('mail-agent.local', msg['Message-ID'])
        self.assertIn('Bien reçu.', msg.get_body(preferencelist=('plain',)).get_content())
        copies = list(FakeIMAP.folders['Sent'].values())
        self.assertEqual(len(copies), 1)
        self.assertIn('\\Seen', copies[0]['flags'])
        saved = BytesParser(policy=policy.default).parsebytes(copies[0]['raw'])
        self.assertEqual(saved['Subject'], msg['Subject'])
        self.assertEqual(saved['Message-ID'], msg['Message-ID'])
        self.assertNotIn(self.uid, FakeIMAP.folders['Drafts'])
        log = self.desk.db.execute('SELECT * FROM send_log490').fetchall()
        self.assertEqual([x['status'] for x in log], ['envoye'])
        blob = json.dumps([dict(x) for x in log]) + json.dumps([dict(x) for x in self.desk.db.execute('SELECT data FROM audit')])
        for secret in ('Bien reçu', 'FIMEX - BERTIN', 'confrere@example.test'):
            self.assertNotIn(secret, blob, 'le journal ne doit contenir ni texte, ni objet, ni adresse')

    def test_bcc_goes_to_the_envelope_but_not_to_the_headers(self):
        raw = raw_draft().replace(b'Subject:', b'Bcc: cache@example.test\r\nSubject:', 1)
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = raw
        self.enable()
        self.flow()
        self.assertEqual(sorted(FakeSMTP.sent[0]['to']), ['cache@example.test', 'confrere@example.test'])
        self.assertIsNone(FakeSMTP.sent[0]['message']['Bcc'])
        saved = BytesParser(policy=policy.default).parsebytes(list(FakeIMAP.folders['Sent'].values())[0]['raw'])
        self.assertIn('cache@example.test', saved['Bcc'])

    def test_confirmation_must_not_be_instant(self):
        self.enable()
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, p['token'], now=time.time())
        self.assertEqual(str(ctx.exception), 'confirmation_trop_rapide')
        self.assertEqual(FakeSMTP.sent, [])

    def test_token_is_single_use_unknown_or_expired(self):
        self.enable()
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, 'inconnu', now=time.time() + 5)
        self.assertEqual(str(ctx.exception), 'apercu_envoi_absent')
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, p['token'], now=time.time() + 400)
        self.assertEqual(str(ctx.exception), 'apercu_envoi_expire')
        p = send490.prepare(self.desk, self.uid, '7')
        send490.confirm(self.desk, p['token'], now=time.time() + 2)
        with self.assertRaises(Stop):
            send490.confirm(self.desk, p['token'], now=time.time() + 3)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_draft_changed_after_preview_is_not_sent(self):
        self.enable()
        p = send490.prepare(self.desk, self.uid, '7')
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = raw_draft().replace(b'Bien re', b'Tout autre texte re')
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, p['token'], now=time.time() + 2)
        self.assertEqual(str(ctx.exception), 'brouillon_modifie_depuis_apercu')
        self.assertEqual(FakeSMTP.sent, [])

    def test_unverified_draft_is_refused(self):
        self.enable()
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = raw_draft().replace(b'Subject: Re:', b'Subject: [\xc3\x80 V\xc3\x89RIFIER] Re:')
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'brouillon_a_verifier')

    def test_same_content_is_never_sent_twice(self):
        self.enable()
        self.flow()
        FakeIMAP.folders['Drafts'][self.uid + 100] = {'raw': raw_draft(), 'flags': {'\\Draft'}, 'date': datetime.now(timezone.utc)}
        FakeIMAP.next_uid['Drafts'] = self.uid + 101
        p = send490.prepare(self.desk, str(self.uid + 100), '7')
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, p['token'], now=time.time() + 2)
        self.assertEqual(str(ctx.exception), 'deja_envoye')
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_smtp_failure_sends_nothing_and_leaves_the_draft(self):
        self.enable()
        FakeSMTP.fail = True
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaises(Stop) as ctx:
            send490.confirm(self.desk, p['token'], now=time.time() + 2)
        self.assertEqual(str(ctx.exception), 'smtp_echec')
        self.assertIn(self.uid, FakeIMAP.folders['Drafts'])
        self.assertEqual(FakeIMAP.folders['Sent'], {})
        self.assertEqual(self.desk.db.execute('SELECT status FROM send_log490').fetchone()[0], 'echec')
        FakeSMTP.fail = False                       # un échec franc autorise une nouvelle tentative
        self.flow()
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_failed_copy_is_reported_loudly(self):
        self.enable()
        with patch.object(send490, '_copy_to_sent', side_effect=Stop('append_imap_incertain')):
            p, r = self.flow()
        self.assertTrue(r['sent'])
        self.assertFalse(r['copy_saved'])
        self.assertIn('ENVOYÉ', r['message'])
        self.assertEqual(self.desk.db.execute('SELECT status FROM send_log490').fetchone()[0], 'envoye_copie_manquante')

    def test_hourly_ceiling(self):
        self.enable()
        for i in range(send490.MAX_PER_HOUR):
            self.desk.db.execute("INSERT INTO send_log490(at,matter,fingerprint,recipients,status) VALUES(?,?,?,?,?)",
                                 (send490.now_iso(), 'DEMO', 'f%d' % i, 1, 'envoye'))
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'plafond_horaire_envois')

    def test_recipient_limits_and_attachments(self):
        self.enable()
        many = ', '.join('p%d@example.test' % i for i in range(send490.MAX_RECIPIENTS + 1)).encode()
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = re.sub(rb'(?m)^To:.*$', b'To: ' + many, raw_draft(), count=1)
        with self.assertRaises(Stop) as ctx:
            send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(str(ctx.exception), 'trop_de_destinataires')
        FakeIMAP.folders['Drafts'][self.uid]['raw'] = raw_draft(attach=True)
        p, r = self.flow()
        self.assertEqual(p['summary']['attachments'][0]['filename'], 'pièce.pdf')
        self.assertTrue(FakeSMTP.sent[0]['message'].get_payload()[-1].get_filename() == 'pièce.pdf')

    def test_only_the_web_layer_can_reach_the_send_module(self):
        users = []
        for path in (ROOT / 'agent').glob('*.py'):
            if path.name in ('send490.py', 'web490.py'):
                continue
            if re.search(r'send490', path.read_text(encoding='utf-8')):
                users.append(path.name)
        self.assertEqual(users, [], 'aucun module de l’agent ne doit pouvoir déclencher un envoi')

    def test_webmail_link_opens_the_exact_draft(self):
        self.f.c.setdefault('workstation', {})['roundcube_drafts_url'] = 'https://courriel.example.test/webmail/?_task=mail&_mbox=INBOX.Drafts'
        link = send490.webmail_link(self.desk, '42')
        self.assertEqual(link['url'], 'https://courriel.example.test/webmail/?_task=mail&_mbox=INBOX.Drafts&_uid=42&_action=edit')
        self.assertEqual(send490.webmail_link(self.desk, 'x')['url'], '')


# ============================================================================== C. mobile et notifications
class Mobile(SendBase):
    def seed(self, drafts=2, deadlines=1):
        for i in range(drafts):
            self.desk.db.execute("INSERT OR REPLACE INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)",
                                 ('k%d' % i, 'draft_ready', 'DEMO', 'Objet CONFIDENTIEL %d' % i, 'client-secret@example.test', '2026-10-01', '2026-10-01'))
        from agent import echeances450
        echeances450.ensure_schema(self.desk)
        for i in range(deadlines):
            self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 ('d%d' % i, 'DEMO', 'appel', 'jugement', '2026-09-01', (TODAY + timedelta(days=2)).isoformat(), 'confirmee', 'manuel',
                                  'Client SECRETCO note', '2026-09-01', '2026-09-01'))
        self.desk.db.commit()

    def test_summary_contains_numbers_and_fixed_text_only(self):
        self.seed(2, 1)
        s = mobile490.summary(self.desk, TODAY)
        self.assertEqual((s['drafts'], s['deadlines']), (3, 1))        # 2 + le brouillon de la fixture
        self.assertEqual(s['text'], '3 brouillons à valider · 1 échéance proche')
        blob = json.dumps(s, ensure_ascii=False)
        for secret in ('SECRETCO', 'CONFIDENTIEL', 'client-secret', 'DEMO', 'BERTIN', 'confrere'):
            self.assertNotIn(secret, blob)
        self.assertEqual(set(s), {'drafts', 'deadlines', 'total', 'text', 'title'})
        self.assertEqual(mobile490.neutral_text({'drafts': 0, 'deadlines': 0}), '')
        self.assertEqual(mobile490.neutral_text({'drafts': 1, 'deadlines': 0}), '1 brouillon à valider')

    def test_manifest_and_service_worker(self):
        m = mobile490.manifest('/agent-courriel')
        self.assertEqual(m['display'], 'standalone')
        self.assertTrue(m['start_url'].startswith(m['scope']))
        self.assertTrue(all(i['src'].startswith('/agent-courriel/') for i in m['icons']))
        self.assertNotIn('SECRETCO', json.dumps(m))
        sw = mobile490.service_worker('/agent-courriel')
        self.assertIn('"/agent-courriel"', sw)
        self.assertNotIn('/api440/', sw.split("self.addEventListener('push'")[0], 'l’API ne doit jamais être mise en cache')
        self.assertIn("showNotification('AxiorHub'", sw)
        self.assertNotRegex(sw, r'(?i)subject|sender|client_name|objet')
        if shutil.which('node'):
            tmp = self.f.base / 'sw.js'
            tmp.write_text(sw)
            self.assertEqual(subprocess.run(['node', '--check', str(tmp)], capture_output=True).returncode, 0)

    def test_push_endpoints_are_allowlisted(self):
        ok = 'https://fcm.googleapis.com/fcm/send/abc'
        self.assertTrue(mobile490.endpoint_ok(ok))
        self.assertTrue(mobile490.endpoint_ok('https://updates.push.services.mozilla.com/wpush/v2/xyz'))
        for bad in ('http://fcm.googleapis.com/x', 'https://evil.example.test/x', 'https://fcm.googleapis.com.evil.test/x',
                    'https://user:pw@fcm.googleapis.com/x', 'https://fcm.googleapis.com:8443/x', 'https://127.0.0.1/x', 'ftp://fcm.googleapis.com/x',
                    'https://fcm.googleapis.com/' + 'a' * 1100):
            self.assertFalse(mobile490.endpoint_ok(bad), bad)

    @NEEDS_CRYPTO
    def test_subscription_needs_consent_and_is_bounded(self):
        with self.assertRaises(Stop) as ctx:
            mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/a', 'tel', '')
        self.assertEqual(str(ctx.exception), 'confirmation_notifications_requise')
        with self.assertRaises(Stop):
            mobile490.subscribe(self.desk, 'https://evil.example.test/x', 'tel', 'yes')
        for i in range(mobile490.MAX_SUBSCRIPTIONS):
            mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/%d' % i, 'tel', 'yes')
        with self.assertRaises(Stop) as ctx:
            mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/extra', 'tel', 'yes')
        self.assertEqual(str(ctx.exception), 'trop_d_appareils')
        self.assertEqual(mobile490.unsubscribe(self.desk, everything=True)['subscriptions'], 0)
        self.assertFalse(mobile490.status(self.desk)['enabled'])

    @NEEDS_CRYPTO
    def test_vapid_signature_is_valid_and_push_is_empty(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        header = mobile490.vapid_header(self.desk, 'https://fcm.googleapis.com/fcm/send/a', 'mailto:cabinet@example.test')
        token, key = re.fullmatch(r'vapid t=(\S+), k=(\S+)', header).groups()
        pad = lambda s: s + '=' * (-len(s) % 4)
        head, claims, sig = token.split('.')
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(claims)))['aud'], 'https://fcm.googleapis.com')
        raw = base64.urlsafe_b64decode(pad(sig))
        public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.urlsafe_b64decode(pad(key)))
        public.verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                      (head + '.' + claims).encode(), ec.ECDSA(hashes.SHA256()))       # lève si la signature est fausse
        self.assertEqual(oct(Path(self.f.c['state_dir'], 'vapid490.pem').stat().st_mode & 0o777), '0o600')

    @NEEDS_CRYPTO
    def test_dispatch_only_on_increase_sends_no_payload_and_prunes_dead_endpoints(self):
        calls = []

        def post(url, headers):
            calls.append((url, headers))
            return 410 if url.endswith('/dead') else 201
        with patch.object(mobile490, 'http_post', post):
            self.assertEqual(mobile490.dispatch(self.desk, TODAY)['reason'], 'desactive')       # désactivé par défaut
            mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/live', 'a', 'yes')
            mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/dead', 'b', 'yes')
            self.seed(2, 0)
            r = mobile490.dispatch(self.desk, TODAY, now=1000.0)
            self.assertEqual((r['sent'], r['expired']), (1, 1))
            self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM push_subs490').fetchone()[0], 1)
            self.assertEqual(mobile490.dispatch(self.desk, TODAY, now=1100.0)['reason'], 'trop_tot')
            self.assertEqual(mobile490.dispatch(self.desk, TODAY, now=1400.0)['reason'], 'rien_de_nouveau')
            self.seed(3, 0)
            self.assertEqual(mobile490.dispatch(self.desk, TODAY, now=2000.0)['sent'], 1)
        for url, headers in calls:
            self.assertTrue(headers['Authorization'].startswith('vapid t='))
            self.assertEqual(set(headers), {'Authorization', 'TTL', 'Urgency'})
            self.assertNotRegex(json.dumps(headers) + url, r'SECRETCO|CONFIDENTIEL|DEMO')

    @NEEDS_CRYPTO
    def test_without_the_cryptography_package_the_app_still_works(self):
        with patch.object(mobile490, 'vapid_available', lambda: False):
            self.assertFalse(mobile490.status(self.desk)['available'])
            self.assertEqual(mobile490.status(self.desk)['public_key'], '')
            with self.assertRaises(Stop):
                mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/a', 'x', 'yes')
            self.assertEqual(mobile490.dispatch(self.desk, TODAY)['sent'], 0)


# ===================================================================================== D. recherche
class SearchBase(SendBase):
    def corpus(self):
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.f.c['state_dir'])
        idx.put_source('DEMO', '/Dossiers/DEMO/Contrat de distribution.docx', 'La clause pénale prévue à l’article 12 du contrat de distribution est excessive. Résiliation du préavis.', 'e1', '2026-06-10T10:00:00')
        idx.put_source('AUTRE', '/Dossiers/AUTRE/Mise en demeure.pdf', 'Mise en demeure de payer la somme de 4 500 euros avant le 15 novembre. Clause pénale non appliquée.', 'e2', '2026-09-20T10:00:00')
        idx.db.close()
        from agent import echeances450
        echeances450.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             ('e1', 'DEMO', 'appel', 'jugement', '2026-09-01', '2026-10-12', 'confirmee', 'manuel', 'Appel du jugement du tribunal de commerce', '2026-09-01', '2026-09-01'))
        self.desk.db.execute("INSERT INTO case_facts(id,matter,category,text,status,sources,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                             ('f1', 'DEMO', 'montants', 'Le prix de vente convenu est de 120 000 euros hors taxes.', 'validated', '[]', '2026-08-01', '2026-08-02'))
        from agent import workplan
        workplan.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             ('c1', 'u1', '', 'cal', 'h', 'e', 'Audience de plaidoirie', 'Salle 4, tribunal de commerce de Lyon', 'Lyon', '2026-11-05T09:00:00+00:00', '2026-11-05T11:00:00+00:00', 1, 'DEMO', '[]', '2026-10-01'))
        self.desk.db.commit()
        search490.refresh_local(self.desk, force=True)


class Search(SearchBase):
    def test_every_source_is_searched_with_sourced_excerpts(self):
        self.corpus()
        r = search490.search(self.desk, 'clause pénale')
        kinds = {x['kind'] for x in r['results']}
        self.assertEqual(kinds, {'fichier'})
        self.assertEqual(r['counts']['fichier'], 2)
        hit = [x for x in r['results'] if x['matter'] == 'DEMO'][0]
        self.assertEqual(hit['source']['path'], '/Dossiers/DEMO/Contrat de distribution.docx')
        self.assertTrue(any(s['hit'] and 'clause' in s['t'].lower() for s in hit['segments']))
        self.assertNotIn('\x01', json.dumps(r))
        for q, kind in (('plaidoirie', 'agenda'), ('tribunal commerce appel', 'agenda'), ('120 000 euros', 'note'), ('BERTIN', 'courriel')):
            got = search490.search(self.desk, q)
            self.assertIn(kind, {x['kind'] for x in got['results']}, q)

    def test_accents_case_and_prefixes_are_ignored(self):
        self.corpus()
        for q in ('CLAUSE PENALE', 'clause penal', 'resiliation', 'resili', 'Mise en demeure'):
            self.assertTrue(search490.search(self.desk, q)['results'], q)

    def test_quoted_phrase_is_exact(self):
        self.corpus()
        self.assertTrue(search490.search(self.desk, '"clause pénale prévue"')['results'])
        self.assertFalse(search490.search(self.desk, '"pénale clause"')['results'])

    def test_filters_by_matter_date_and_kind(self):
        self.corpus()
        only = search490.search(self.desk, 'clause pénale', matter='AUTRE')
        self.assertEqual({x['matter'] for x in only['results']}, {'AUTRE'})
        old = search490.search(self.desk, 'clause pénale', date_to='2026-07-31')
        self.assertEqual([x['matter'] for x in old['results']], ['DEMO'])
        recent = search490.search(self.desk, 'clause pénale', date_from='2026-08-01')
        self.assertEqual([x['matter'] for x in recent['results']], ['AUTRE'])
        self.assertEqual(search490.search(self.desk, 'tribunal', kinds=['agenda'])['counts']['fichier'], 0)
        with self.assertRaises(Stop):
            search490.search(self.desk, 'x', matter='INCONNU')
        with self.assertRaises(Stop):
            search490.search(self.desk, 'clause', date_from='2026-12-01', date_to='2026-01-01')
        with self.assertRaises(Stop):
            search490.search(self.desk, 'clause', date_from='pas une date')
        with self.assertRaises(Stop):
            search490.search(self.desk, 'clause', kinds=['inconnu'])

    def test_hostile_queries_cannot_break_the_index(self):
        self.corpus()
        for q in ('"', '""', 'a OR', 'NEAR(', '*', 'clause" OR "', "'; DROP TABLE s490; --", 'AND', '(', 'col:val', 'x' * 150, '\x00clause'):
            try:
                search490.search(self.desk, q)
            except Stop:
                pass
        self.assertTrue(search490.search(self.desk, 'clause')['results'])
        with self.assertRaises(Stop):
            search490.search(self.desk, '')
        with self.assertRaises(Stop):
            search490.search(self.desk, 'x' * 201)

    def test_time_budget_returns_a_partial_result_instead_of_hanging(self):
        self.corpus()
        with patch.object(search490, 'TIME_BUDGET', -1):
            r = search490.search(self.desk, 'clause')
        self.assertTrue(r['partial'])
        self.assertIn('partiels', r['note'])

    def test_the_query_text_is_never_stored(self):
        self.corpus()
        needle = 'zqxjvbnmotifsecret'
        search490.search(self.desk, needle)
        for name in ('desk.sqlite3', 'search490.sqlite3'):
            raw = (Path(self.f.c['state_dir']) / name).read_bytes()
            wal = Path(self.f.c['state_dir']) / (name + '-wal')
            raw += wal.read_bytes() if wal.exists() else b''
            self.assertNotIn(needle.encode(), raw)

    def test_search_makes_no_network_call(self):
        self.corpus()
        with patch('socket.socket', side_effect=AssertionError('réseau interdit')):
            self.assertTrue(search490.search(self.desk, 'clause')['results'])

    def test_removed_items_leave_the_index(self):
        self.corpus()
        self.desk.db.execute("DELETE FROM case_facts")
        self.desk.db.commit()
        search490.refresh_local(self.desk, force=True)
        self.assertFalse(search490.search(self.desk, '120 000 euros', kinds=['note'])['results'])

    def test_index_file_is_private_and_can_be_purged(self):
        self.corpus()
        path = Path(self.f.c['state_dir']) / 'search490.sqlite3'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(Stop):
            search490.purge(self.desk)
        st = search490.purge(self.desk, 'yes')
        self.assertEqual(sum(st['counts'][k] for k in ('agenda', 'note')), 0)


class SearchMail(SearchBase):
    def add_mails(self, n):
        for i in range(n):
            raw = (b'From: Contact <c%d@example.test>\r\nTo: cabinet@example.test\r\nSubject: Dossier ref%d\r\n'
                   b'Message-ID: <m%d@example.test>\r\nDate: Fri, 02 Oct 2026 08:00:00 +0200\r\n\r\nCorps du message %d avec le mot %s.\r\n') % (
                i, i, i, i, b'alpha' if i % 2 else b'beta')
            FakeIMAP.add('INBOX', raw, when=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i))

    def test_mail_is_indexed_read_only_newest_first_then_backfilled(self):
        self.add_mails(9)
        total = 10   # + le courriel de la fixture
        FakeIMAP.log.clear()
        first = search490.collect_mail(self.desk, batch=4)
        self.assertEqual(first['indexed'], 4)
        newest = search490.search(self.desk, 'ref8', kinds=['courriel'])
        self.assertTrue(newest['results'])
        self.assertFalse(search490.search(self.desk, 'ref0', kinds=['courriel'])['results'])      # pas encore rattrapé
        for _ in range(6):
            search490.collect_mail(self.desk, batch=4)
        self.assertTrue(search490.search(self.desk, 'ref0', kinds=['courriel'])['results'])
        st = search490.status(self.desk)
        self.assertTrue(st['backfill_done'])
        self.assertGreaterEqual(st['mail_indexed'], total)
        writes = [x for x in FakeIMAP.log if x[0] in ('APPEND',) or (x[0] == 'UID' and x[1] in ('STORE', 'EXPUNGE', 'COPY'))]
        self.assertEqual(writes, [], 'l’indexation ne doit rien écrire dans la messagerie')
        again = search490.collect_mail(self.desk, batch=50)
        self.assertEqual(again['indexed'], 0)

    def test_new_mail_is_picked_up_and_can_be_disabled(self):
        self.add_mails(3)
        search490.collect_mail(self.desk, batch=50)
        FakeIMAP.add('INBOX', b'From: x@example.test\r\nSubject: Nouveau\r\nMessage-ID: <n@example.test>\r\nDate: Fri, 02 Oct 2026 08:00:00 +0200\r\n\r\nNouveautexteunique.\r\n')
        search490.set_mail_indexing(self.desk, False)
        self.assertEqual(search490.collect_mail(self.desk)['reason'], 'desactive')
        search490.set_mail_indexing(self.desk, True)
        self.assertEqual(search490.collect_mail(self.desk, batch=50)['indexed'], 1)
        self.assertTrue(search490.search(self.desk, 'Nouveautexteunique')['results'])

    def test_mail_inherits_the_matter_of_tracked_items(self):
        search490.collect_mail(self.desk, batch=50)
        got = search490.search(self.desk, 'calendrier', kinds=['courriel'], matter='DEMO')
        self.assertTrue(got['results'])

    def test_background_job_is_registered_and_scheduled(self):
        from agent import desk as deskmod
        self.assertIn('search_index490', deskmod.JOBS)
        self.assertTrue(search490.schedule(self.desk, now=10_000.0))
        self.assertFalse(search490.schedule(self.desk, now=10_100.0))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='search_index490'").fetchone()[0], 1)
        out = self.desk.perform('search_index490', {})
        self.assertIn('indexed', out)


class SearchPerformance(SearchBase):
    def test_answer_in_under_three_seconds_on_a_large_cabinet(self):
        """Volume de référence : 400 dossiers, 60 000 passages de fichiers, 12 000 courriels, 3 000 événements."""
        private_json(Path(self.f.c['matters_file']), [{**DEMO, 'id': 'M%03d' % i} for i in range(400)])
        words = ['contrat', 'société', 'assignation', 'clause', 'bail', 'dividendes', 'saisie', 'hypothèque', 'cession', 'garantie', 'préavis',
                 'expertise', 'mandat', 'quittance', 'procédure', 'référé', 'audience', 'conclusions', 'pièce', 'facture'] * 3
        import random
        rnd = random.Random(490)
        db = sqlite3.connect(Path(self.f.c['state_dir']) / 'documents.sqlite3')
        from agent.index import DocumentIndex
        DocumentIndex(self.f.c['state_dir']).db.close()
        rows, fts = [], []
        for i in range(60_000):
            matter = 'M%03d' % (i % 400)
            text = ' '.join(rnd.choice(words) for _ in range(60)) + ' réf%d' % i
            rows.append((matter, 's%d' % (i // 3), '/Dossiers/%s/doc%d.docx' % (matter, i // 3), 'e', '2026-0%d-10T10:00:00' % (1 + i % 9), 'document', i % 3, text, '{}', 'x'))
            fts.append((matter, 's%d' % (i // 3), i % 3, '/Dossiers/%s/doc%d.docx' % (matter, i // 3), text))
        db.executemany('INSERT OR IGNORE INTO knowledge_chunks VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
        db.executemany('INSERT INTO knowledge_fts VALUES (?,?,?,?,?)', fts)
        db.commit()
        db.close()
        s = search490.connect(self.desk)
        s.executemany('INSERT INTO s490(kind,ref,matter,title,date,body,meta,sig) VALUES(?,?,?,?,?,?,?,?)',
                      [('courriel', 'imap|INBOX|1|%d' % i, 'M%03d' % (i % 400), 'Objet %d %s' % (i, rnd.choice(words)),
                        '2026-0%d-1%dT10:00:00' % (1 + i % 9, i % 9), 'De : x@example.test ' + ' '.join(rnd.choice(words) for _ in range(120)), '{"label":"Courriel"}', 's%d' % i)
                       for i in range(12_000)] +
                      [('agenda', 'cal|%d' % i, 'M%03d' % (i % 400), 'Audience %d' % i, '2026-11-%02dT09:00:00' % (1 + i % 28), 'salle ' + rnd.choice(words), '{"label":"Événement"}', 'c%d' % i)
                       for i in range(3_000)])
        s.commit()
        s.close()
        self.desk.setting('search490:local_refresh', time.time())
        queries = ['clause pénale', 'assignation société', 'saisie hypothèque', 'réf4242', 'audience référé conclusions', 'mandat', 'préavis bail', 'facture quittance',
                   'cession dividendes garantie', 'expertise']
        times = []
        for q in queries * 3:
            t0 = time.perf_counter()
            r = search490.search(self.desk, q, limit=30)
            times.append(time.perf_counter() - t0)
            self.assertFalse(r['partial'], q)
        scoped = time.perf_counter()
        search490.search(self.desk, 'clause', matter='M007', date_from='2026-03-01', date_to='2026-06-30')
        times.append(time.perf_counter() - scoped)
        times.sort()
        if __import__('os').environ.get('AXIORHUB_PERF'):
            print('\n[perf 4.9] %d requêtes : médiane %.0f ms, p95 %.0f ms, maximum %.0f ms' % (
                len(times), statistics.median(times) * 1000, times[int(len(times) * .95)] * 1000, times[-1] * 1000))
        self.assertLess(times[-1], 3.0, 'maximum %.2f s' % times[-1])
        self.assertLess(statistics.median(times), 1.0, 'médiane %.2f s' % statistics.median(times))


# =================================================================================== interface et API
class Web(unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()
        private_json(Path(self.f.c['matters_file']), [DEMO])
        self.desk.db.execute("INSERT INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)",
                             ('a' * 64, 'draft_ready', 'DEMO', 'FIMEX - BERTIN : calendrier', 'confrere@example.test', '2026-10-02T08:00:00', '2026-10-02T09:00:00'))
        self.desk.db.commit()
        p = patch.object(send490, 'smtp_factory', FakeSMTP)
        p.start()
        self.addCleanup(p.stop)
        FakeSMTP.sent, FakeSMTP.fail = [], False

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def get(self, name, query=''):
        r = self.call('/api440/' + name, query=query)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_pages_need_authentication_and_are_in_the_navigation(self):
        for path in ('/recherche', '/confort', '/sw.js', '/hors-ligne'):
            self.assertIn('401', self.call(path, auth=False)['status'], path)
            self.assertTrue(self.call(path)['status'].startswith('200'), path)
        html = self.call('/courriels')['body'].decode()
        for needle in ('/agent-courriel/recherche', '/agent-courriel/parametres', 'rel="manifest"', 'theme-color', 'cf-skip', 'id="ax-main"', '/static/v490.js', '/static/app520.css'):
            self.assertIn(needle, html, needle)

    def test_manifest_is_public_service_worker_is_not_and_csp_allows_both(self):
        r = self.call('/pwa/manifest.webmanifest', auth=False)
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('manifest+json', r['headers']['Content-Type'])
        self.assertEqual(json.loads(r['body'])['start_url'], '/agent-courriel/aujourdhui')
        page = self.call('/recherche')
        csp = page['headers']['Content-Security-Policy']
        self.assertIn("manifest-src 'self'", csp)
        self.assertIn("worker-src 'self'", csp)
        sw = self.call('/sw.js')
        self.assertIn('javascript', sw['headers']['Content-Type'])

    def test_search_page_and_api(self):
        self.assertIn('Une seule barre', self.call('/recherche')['body'].decode())
        r, out = self.get('search/query', 'q=calendrier')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertTrue(out['results'])
        r, out = self.get('search/query', 'q=')
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('Tapez un mot', out['message'])
        r, out = self.get('search/status')
        self.assertIn('timing', out)
        r, out = self.get('search/query', 'q=calendrier&kinds=inconnu')
        self.assertTrue(r['status'].startswith('400'))

    def test_voice_route_is_protected_and_proposes_only(self):
        body = {'transcript': 'ajoute que tout va bien', 'body': BODY}
        for kw in ({'origin': False}, {'csrf': False}):
            self.assertTrue(self.post('voice/apply', body, **kw)[0]['status'].startswith('400'))
        before = FakeIMAP.folders['Drafts'][self.uid]['raw']
        r, out = self.post('voice/apply', body)
        self.assertEqual(out['mode'], 'replace_body')
        # rien n'est écrit dans la messagerie (5.6.1 : comparaison au brouillon tel qu'il était, et non à un brouillon régénéré à l'heure courante)
        self.assertEqual(FakeIMAP.folders['Drafts'][self.uid]['raw'], before)
        r, out = self.post('voice/apply', {'transcript': 'envoie', 'body': BODY})
        self.assertEqual(out['mode'], 'refused')
        r, out = self.post('voice/apply', {'transcript': 'remplace absent par autre', 'body': BODY})
        self.assertIn('n’apparaît pas', out['message'])
        audit = json.dumps([tuple(x) for x in self.desk.db.execute('SELECT action,data FROM audit')])
        self.assertNotIn('tout va bien', audit)

    def test_send_routes_require_csrf_double_step_and_stay_off_by_default(self):
        r, out = self.get('send/status')
        self.assertFalse(out['enabled'])
        self.assertFalse(out['smtp_configured'])
        r, out = self.post('send/prepare', {'uid': str(self.uid), 'uidvalidity': '7'})
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('désactivé', out['message'])
        self.assertTrue(self.post('send/confirm', {'token': 'x'}, csrf=False)[0]['status'].startswith('400'))
        self.f.c['mail']['smtp'] = {'host': 'smtp.test'}
        private_json(self.config, self.f.c)
        self.assertTrue(self.post('send/settings', {'enabled': True})[0]['status'].startswith('400'))
        self.assertTrue(self.post('send/settings', {'enabled': True, 'confirm': 'yes'})[0]['status'].startswith('200'))
        self.assertTrue(self.post('send/matter', {'matter': 'DEMO', 'enabled': True, 'confirm': 'yes'})[0]['status'].startswith('200'))
        r, prepared = self.post('send/prepare', {'uid': str(self.uid), 'uidvalidity': '7'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(FakeSMTP.sent, [])
        r, out = self.post('send/confirm', {'token': prepared['token']})
        self.assertIn('trop rapide', out['message'])
        self.assertEqual(FakeSMTP.sent, [])

    def test_mobile_routes(self):
        r, out = self.get('mobile/summary')
        self.assertEqual(set(out), {'drafts', 'deadlines', 'total', 'text', 'title'})
        r, out = self.post('mobile/subscribe', {'endpoint': 'https://evil.example.test/x', 'confirm': 'yes'})
        self.assertTrue(r['status'].startswith('400'))
        r, out = self.post('mobile/subscribe', {'endpoint': 'https://fcm.googleapis.com/fcm/send/a', 'confirm': 'yes'})
        self.assertEqual(out['subscriptions'], 1)
        self.assertTrue(self.post('mobile/subscribe', {'endpoint': 'https://fcm.googleapis.com/fcm/send/b'})[0]['status'].startswith('400'))

    def test_confort_page_lists_commands_shortcuts_and_the_decision_on_sending(self):
        html = self.call('/confort')['body'].decode() + self.call('/parametres/assistant')['body'].decode()   # 5.6.21 : dictée dans « Voix », envoi et raccourcis dans « Cabinet »
        for needle in ('Ajoute que je suis disponible le 12', 'Envoie…', 'REFUSÉ', 'désactivé', 'Ouvrir dans la messagerie', 'Aucun serveur d’envoi', 'uniquement des nombres',
                       '<kbd>g puis c</kbd>', 'Ctrl + Entrée', 'Raccourcis clavier'):
            self.assertIn(needle, html, needle)
        self.assertNotRegex(html, r'(?i)<script(?![^>]*\bsrc=)(?![^>]*type="application/json")')
        self.assertNotRegex(html, r' style="')

    def test_every_new_route_is_in_the_static_allowlist(self):
        for name in ('v490.js', 'v490.css'):
            r = self.call('/static/' + name)
            self.assertTrue(r['status'].startswith('200'), name)
            self.assertTrue(r['body'].startswith(b'/* AxiorHub 4.9.0'))


# =================================================================================== E. ergonomie
class Ergonomics(unittest.TestCase):
    CSS = ('v440', 'v450', 'v460', 'v470', 'v480', 'v490')

    def test_javascript_is_syntactically_valid_and_injects_no_html(self):
        js = (STATIC / 'v490.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('document.write', js)
        self.assertNotIn('eval(', js)
        self.assertNotIn('localStorage', js)
        if shutil.which('node'):
            self.assertEqual(subprocess.run(['node', '--check', str(STATIC / 'v490.js')], capture_output=True).returncode, 0)

    def test_dark_mode_covers_every_light_coloured_rule(self):
        def lum(h):
            h = h.lstrip('#')
            h = ''.join(c * 2 for c in h) if len(h) == 3 else h
            r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        light, dark = [], set()
        for name in self.CSS:
            css = re.sub(r'/\*.*?\*/', '', (STATIC / (name + '.css')).read_text(encoding='utf-8'), flags=re.S)
            while True:
                m = re.search(r'@media \(prefers-color-scheme:\s*dark\)\s*\{', css)
                if not m:
                    break
                i = depth = j = m.end()
                depth = 1
                while depth and j < len(css):
                    depth += (css[j] == '{') - (css[j] == '}')
                    j += 1
                for sel in re.findall(r'([^{}]+)\{[^{}]*\}', css[i:j - 1]):
                    dark.update(s.strip() for s in sel.split(','))
                css = css[:m.start()] + css[j:]
            for sel, decl in re.findall(r'([^{}@]+)\{([^{}]*)\}', css):
                if sel.strip().startswith(':root[data-theme=dark]'):      # 5.0.1 : le mode sombre suit le bouton de thème commun
                    dark.update(x.strip().replace(':root[data-theme=dark] ', '', 1) for x in sel.split(','))
                    continue
                for prop, val in re.findall(r'(background(?:-color)?|color)\s*:\s*(#[0-9a-fA-F]{3,6})\b', decl):
                    if (prop != 'color' and lum(val) > 0.75) or (prop == 'color' and lum(val) < 0.25):
                        light.append((name, sel.strip()))
        exempt = {'#ax-office-wrap', '.ck-skip:focus'}          # cadre blanc de l’éditeur de documents ; lien d’évitement à contraste maximal
        missing = [(n, s) for n, s in light if s not in exempt and not any(x.strip() in dark for x in s.split(','))]
        self.assertEqual(missing, [])

    def test_phone_layout_and_touch_targets(self):
        css = (STATIC / 'v490.css').read_text(encoding='utf-8')
        self.assertIn('@media (max-width:760px)', css)
        self.assertIn('min-height:44px', css)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('display-mode:standalone', css)

    def test_shortcuts_documented_match_the_script(self):
        from agent import web490
        js = (STATIC / 'v490.js').read_text(encoding='utf-8')
        keys = dict(re.findall(r"(\w): '(/[a-z]+)'", re.search(r'var NAVKEYS = \{(.*?)\};', js, re.S).group(0)))
        documented = {k.split()[-1] for k, _, _ in web490.SHORTCUTS if k.startswith('g puis')}
        self.assertEqual(documented, set(keys))
        for letter, route in keys.items():
            self.assertIn(route, [p for p, _, _ in __import__('agent.web440', fromlist=['NAV']).NAV])

    def test_no_inline_style_or_event_handler_anywhere_in_the_new_pages(self):
        for name in ('web490.py', 'mobile490.py'):
            src = (ROOT / 'agent' / name).read_text(encoding='utf-8')
            self.assertNotRegex(src, r'\bstyle="')
            self.assertNotRegex(src, r'\bon(click|load|error)=')


if __name__ == '__main__':
    unittest.main()
