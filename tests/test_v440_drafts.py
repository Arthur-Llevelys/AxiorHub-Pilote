"""Draft editor: list, read, save with read-back, conflicts, discard."""
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.common import Stop
from agent import drafts440
from agent.mailbox import make_draft, Mail
from fake_imap440 import FakeIMAP


def raw_inbound():
    m = EmailMessage(policy=policy.SMTP)
    m['From'] = 'Confrère <confrere@example.test>'
    m['To'] = 'cabinet@example.test'
    m['Subject'] = 'FIMEX - BERTIN : calendrier'
    m['Message-ID'] = '<orig-1@example.test>'
    m['Date'] = 'Fri, 02 Oct 2026 08:00:00 +0200'
    m.set_content('Bonjour Maître, voici le calendrier de procédure.')
    return m.as_bytes()


def raw_draft(attach=False):
    src = BytesParser(policy=policy.default).parsebytes(raw_inbound())
    from datetime import datetime, timezone
    mail = Mail('1', '7', 'INBOX', set(), datetime.now(timezone.utc), src)
    cfg = {'from_name': 'Camille EXEMPLE', 'from_address': 'cabinet@example.test', 'signature': 'Camille EXEMPLE'}
    draft = make_draft(mail, cfg, 'Cher Confrère,\n\nBien reçu.', 'a' * 64)
    if attach:
        draft.add_attachment(b'%PDF-1.4 test', maintype='application', subtype='pdf', filename='pièce.pdf')
    return draft.as_bytes(policy=policy.SMTP)


class DraftTests(unittest.TestCase):
    def setUp(self):
        FakeIMAP.reset()
        self.tmp = tempfile.TemporaryDirectory()
        secret = Path(self.tmp.name) / 's'
        secret.write_text('x')
        secret.chmod(0o600)
        self.cfg = {'host': 'imap.test', 'username': 'u', 'password_file': str(secret), 'inbox': 'INBOX',
                    'sent': 'Sent', 'drafts': 'Drafts'}
        p = patch('agent.mailbox.imaplib.IMAP4_SSL', FakeIMAP)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        FakeIMAP.add('INBOX', raw_inbound())
        self.uid = FakeIMAP.add('Drafts', raw_draft(), ['\\Draft'])

    def test_list_and_read_with_source(self):
        listing = drafts440.list_drafts(self.cfg)
        self.assertEqual(len(listing['items']), 1)
        self.assertTrue(listing['items'][0]['agent'])
        self.assertTrue(listing['items'][0]['subject'].startswith('Re: FIMEX'))
        draft = drafts440.get_draft(self.cfg, str(self.uid), listing['uidvalidity'])
        self.assertIn('Bien reçu', draft['body'])
        self.assertEqual(draft['source']['sender'], 'confrere@example.test')
        self.assertTrue(draft['editable'])
        self.assertEqual(len(draft['revision']), 32)

    def edit(self, draft, **changes):
        data = {'uid': draft['uid'], 'uidvalidity': draft['uidvalidity'], 'revision': draft['revision'],
                'to': draft['to'], 'cc': draft['cc'], 'bcc': '', 'subject': draft['subject'], 'body': draft['body']}
        data.update(changes)
        return data

    def test_save_replaces_previous_version_after_readback(self):
        draft = drafts440.get_draft(self.cfg, str(self.uid))
        result = drafts440.save_draft(self.cfg, self.edit(draft, body='Cher Confrère,\n\nBien reçu, merci.\n\nCamille EXEMPLE'))
        self.assertTrue(result['previous_removed'])
        self.assertNotIn(self.uid, FakeIMAP.folders['Drafts'])
        self.assertEqual(len(FakeIMAP.folders['Drafts']), 1)
        saved = BytesParser(policy=policy.default).parsebytes(FakeIMAP.folders['Drafts'][int(result['uid'])]['raw'])
        self.assertIn('merci', saved.get_body().get_content())
        self.assertEqual(saved['Message-ID'], draft['message_id'])
        self.assertEqual(saved['In-Reply-To'], '<orig-1@example.test>')
        self.assertEqual(saved['X-AxiorHub-Draft-Key'].strip(), 'a' * 64)
        self.assertIn('\\Draft', FakeIMAP.folders['Drafts'][int(result['uid'])]['flags'])
        self.assertIn('Bien reçu.', result['original_body'])
        # nothing was sent and the inbox was not modified
        self.assertEqual(len(FakeIMAP.folders['INBOX']), 1)
        self.assertFalse(FakeIMAP.folders['Sent'])

    def test_concurrent_change_is_refused(self):
        draft = drafts440.get_draft(self.cfg, str(self.uid))
        stale = self.edit(draft, revision='0' * 32)
        with self.assertRaises(Stop) as ctx:
            drafts440.save_draft(self.cfg, stale)
        self.assertEqual(str(ctx.exception), 'brouillon_modifie_depuis_ouverture')
        self.assertEqual(len(FakeIMAP.folders['Drafts']), 1)

    def test_uidvalidity_change_is_refused(self):
        draft = drafts440.get_draft(self.cfg, str(self.uid))
        with self.assertRaises(Stop) as ctx:
            drafts440.save_draft(self.cfg, self.edit(draft, uidvalidity='99'))
        self.assertEqual(str(ctx.exception), 'uidvalidity_modifiee')

    def test_attachments_are_preserved_and_header_injection_rejected(self):
        FakeIMAP.reset()
        FakeIMAP.add('INBOX', raw_inbound())
        uid = FakeIMAP.add('Drafts', raw_draft(attach=True), ['\\Draft'])
        draft = drafts440.get_draft(self.cfg, str(uid))
        self.assertEqual(draft['attachments'][0]['filename'], 'pièce.pdf')
        result = drafts440.save_draft(self.cfg, self.edit(draft, body='Texte modifié'))
        saved = BytesParser(policy=policy.default).parsebytes(FakeIMAP.folders['Drafts'][int(result['uid'])]['raw'])
        parts = list(saved.iter_attachments())
        self.assertEqual(parts[0].get_payload(decode=True), b'%PDF-1.4 test')
        with self.assertRaises(Stop):
            drafts440.save_draft(self.cfg, self.edit(drafts440.get_draft(self.cfg, result['uid']),
                                                     subject='x\r\nBcc: attaquant@example.test'))

    def test_recipient_validation(self):
        self.assertEqual(drafts440.parse_recipients('A <a@example.test>; b@example.test'),
                         'A <a@example.test>, b@example.test')
        with self.assertRaises(Stop):
            drafts440.parse_recipients('pas-une-adresse')
        with self.assertRaises(Stop):
            drafts440.parse_recipients(', '.join('u%d@example.test' % i for i in range(40)))

    def test_discard_moves_to_trash(self):
        draft = drafts440.get_draft(self.cfg, str(self.uid))
        with self.assertRaises(Stop):
            drafts440.discard_draft(self.cfg, draft['uid'], draft['uidvalidity'], 'no')
        result = drafts440.discard_draft(self.cfg, draft['uid'], draft['uidvalidity'], 'yes')
        self.assertTrue(result['moved_to_trash'])
        self.assertEqual(len(FakeIMAP.folders['Trash']), 1)
        self.assertFalse(FakeIMAP.folders['Drafts'])

    def test_no_send_or_inbox_write_calls(self):
        draft = drafts440.get_draft(self.cfg, str(self.uid))
        drafts440.save_draft(self.cfg, self.edit(draft, body='autre'))
        writes = [x for x in FakeIMAP.log if x[0] in ('APPEND',) or (x[0] == 'UID' and x[1] in ('STORE', 'EXPUNGE'))]
        self.assertTrue(all(w[0] == 'APPEND' and w[1] == 'Drafts' or w[0] == 'UID' for w in writes))
        self.assertNotIn(('SELECT', 'INBOX'), FakeIMAP.log)


if __name__ == '__main__':
    unittest.main()
