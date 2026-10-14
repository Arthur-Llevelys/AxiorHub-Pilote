"""5.6.28 : messagerie (répondre, répondre à tous, transférer, nouveau message mis en forme, Cc/Cci, pièces jointes, archiver,
déplacer, indésirable, corbeille, lu/non lu, suivi, envoi en deux temps) et vue « Tâches » simplifiée."""
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
from pathlib import Path
import re
import time
import unittest
from unittest.mock import patch

from agent.common import Stop
from agent.mailbox import Mail
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]


def _raw(sender, to, subject, body, mid, cc='', attachment=None):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'], m['Message-ID'] = sender, to, subject, mid
    if cc:
        m['Cc'] = cc
    m.set_content(body)
    if attachment:
        m.add_attachment(attachment[1], maintype='application', subtype='pdf', filename=attachment[0])
    return m.as_bytes(policy=policy.SMTP)


class Conn:
    def __init__(self, box):
        self.box, self.selected, self.readonly = box, None, True
        self.capabilities = tuple(box.caps)

    def _n(self, value):
        return str(value).strip('"')

    def select(self, name, readonly=False):
        name = self._n(name)
        if name not in self.box.store:
            return 'NO', [b'missing']
        self.selected, self.readonly = name, readonly
        return 'OK', [b'1']

    def response(self, name):
        return name, [b'7']

    def list(self):
        return 'OK', [b'(\\HasChildren) "." "INBOX"', b'(\\Drafts) "." "Drafts"', b'(\\Sent) "." "Sent"', b'(\\Trash) "." "Trash"',
                      b'(\\Junk) "." "Spam"', b'() "." "INBOX.Clients"'] + [('() "." "%s"' % n).encode() for n in self.box.created]

    def create(self, name):
        self.box.created.append(self._n(name))
        self.box.store.setdefault(self._n(name), {})
        WebBox.log.append(('CREATE', self._n(name)))
        return 'OK', [b'ok']

    def subscribe(self, name):
        return 'OK', [b'ok']

    def append(self, folder, flags, date, raw):
        folder = self._n(folder)
        uid = max([0] + list(self.box.store[folder])) + 1
        self.box.store[folder][uid] = ({f for f in re.findall(r'\\\w+', flags or '')}, raw)
        WebBox.log.append(('APPEND', folder, uid))
        return 'OK', [('[APPENDUID 7 %d] ok' % uid).encode()]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        WebBox.log.append(('UID', cmd) + tuple(self._n(a) for a in args))
        msgs = self.box.store[self.selected]
        if cmd == 'SEARCH':
            crit = list(args[1:])
            out = [str(u) for u, (fl, raw) in sorted(msgs.items()) if '\\Deleted' not in fl]
            if 'HEADER' in crit:
                key, value = crit[crit.index('HEADER') + 1], self._n(crit[crit.index('HEADER') + 2])
                out = [u for u in out if value in str(BytesParser(policy=policy.default).parsebytes(msgs[int(u)][1]).get(key, ''))]
            return 'OK', [' '.join(out).encode()]
        if self.readonly:
            return 'NO', [b'read-only']
        uid = int(args[0])
        if cmd == 'MOVE':
            target = self._n(args[1])
            self.box.store[target][max([0] + list(self.box.store[target])) + 1] = msgs.pop(uid)
        elif cmd == 'COPY':
            target = self._n(args[1])
            fl, raw = msgs[uid]
            self.box.store[target][max([0] + list(self.box.store[target])) + 1] = (set(fl), raw)
        elif cmd == 'STORE':
            flag = re.findall(r'\\\w+', args[2])[0]
            (msgs[uid][0].add if args[1].startswith('+') else msgs[uid][0].discard)(flag)
        elif cmd == 'EXPUNGE':
            if '\\Deleted' in msgs.get(uid, (set(), b''))[0]:
                msgs.pop(uid)
        return 'OK', [b'done']


class WebBox:
    store, created, caps, log = {}, [], ('IMAP4REV1', 'UIDPLUS', 'MOVE'), []

    @classmethod
    def reset(cls, caps=('IMAP4REV1', 'UIDPLUS', 'MOVE')):
        cls.caps, cls.created, cls.log = caps, [], []
        cls.store = {'INBOX': {}, 'Drafts': {}, 'Sent': {}, 'Trash': {}, 'Spam': {}, 'INBOX.Clients': {}}
        cls.store['INBOX'][1] = (set(), _raw('Client Durand <durand@example.test>', 'Cabinet <cabinet@example.test>', 'Assemblée générale',
                                             'Maître, pouvez-vous me répondre ?', '<a1@x>', cc='Associé <associe@example.test>, cabinet@example.test'))
        cls.store['INBOX'][2] = (set(), _raw('Confrère <martin@avocats.test>', 'cabinet@example.test', 'Pièces', 'Voici les pièces.', '<a2@x>',
                                             attachment=('bordereau.pdf', b'%PDF-1.4 bordereau')))

    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = Conn(self)

    def fetch(self, folder, uid, headers_only=False):
        item = self.store.get(folder, {}).get(int(uid))
        if not item:
            raise Stop('message_imap_disparu')
        return Mail(str(uid), '7', folder, set(item[0]), datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc), BytesParser(policy=policy.default).parsebytes(item[1]))

    def search(self, folder, *crit):
        return [str(u) for u, (fl, _) in sorted(self.store.get(folder, {}).items()) if '\\Deleted' not in fl]

    def close(self):
        pass


class Webmail(t530.Base):
    def setUp(self):
        super().setUp()
        WebBox.reset()
        for c in (self.f.c, self.desk.c):
            c['mail'].update({'inbox': 'INBOX', 'sent': 'Sent', 'drafts': 'Drafts', 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet EXEMPLE',
                              'own_addresses': ['cabinet@example.test']})
        for target in ('agent.mailbox.Mailbox', 'agent.send490.Mailbox', 'agent.drafts440.Mailbox'):
            p = patch(target, WebBox)
            p.start()
            self.addCleanup(p.stop)

    def test_html_is_cleaned_to_a_closed_list_of_tags(self):
        from agent.webmail5628 import clean_html, html_to_text
        dirty = ('<p onclick="x()">Bonjour <b>Maître</b><script>alert(1)</script></p><a href="javascript:alert(1)">piège</a>'
                 '<a href="https://ex.test/a">lien</a><img src=x onerror=y><style>p{}</style><ul><li>un</li></ul>')
        clean = clean_html(dirty)
        self.assertEqual(clean, '<p>Bonjour <b>Maître</b></p><a>piège</a><a href="https://ex.test/a" rel="noopener noreferrer">lien</a><ul><li>un</li></ul>')
        self.assertIn('Bonjour Maître', html_to_text(clean))

    def test_reply_reply_all_and_forward_templates(self):
        from agent import webmail5628 as w
        r = w.template(self.desk, 'repondre', '1')
        self.assertEqual((r['to'], r['cc'], r['subject']), ('Client Durand <durand@example.test>', '', 'Re : Assemblée générale'))
        self.assertIn('<blockquote>Maître, pouvez-vous me répondre ?</blockquote>', r['html'])
        ra = w.template(self.desk, 'repondre_tous', '1')
        self.assertEqual(ra['cc'], 'Associé <associe@example.test>')                       # l'adresse du cabinet n'est pas reprise
        f = w.template(self.desk, 'transferer', '2')
        self.assertEqual((f['to'], f['subject'], [a['name'] for a in f['attachments']]), ('', 'TR : Pièces', ['bordereau.pdf']))
        self.assertIn('Message transféré', f['html'])
        self.assertEqual(w.template(self.desk, 'nouveau')['to'], '')
        with self.assertRaisesRegex(Stop, 'mode_de_redaction_inconnu'):
            w.template(self.desk, 'autre', '1')

    def test_saved_draft_keeps_bcc_formatting_thread_and_attachments(self):
        from agent import webmail5628 as w
        out = w.save(self.desk, {'mode': 'transferer', 'source_uid': '2', 'to': 'tiers@example.test', 'cc': 'associe@example.test', 'bcc': 'archive@example.test',
                                 'subject': 'TR : Pièces', 'html': '<p>Voir <b>ci-joint</b><script>x</script></p>', 'keep': ['0'],
                                 'files': [{'name': 'note.txt', 'type': 'text/plain', 'data': 'Tm90ZQ=='}]})
        flags, raw = WebBox.store['Drafts'][int(out['uid'])]
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertIn('\\Draft', flags)
        self.assertEqual((msg['Bcc'], msg['X-AxiorHub-Origine']), ('archive@example.test', 'webmail5628'))
        self.assertEqual(sorted(p.get_filename() for p in msg.iter_attachments()), ['bordereau.pdf', 'note.txt'])
        html = msg.get_body(preferencelist=('html',)).get_content()
        self.assertIn('<b>ci-joint</b>', html)
        self.assertNotIn('script', html)
        reply = w.save(self.desk, {'mode': 'repondre', 'source_uid': '1', 'to': 'durand@example.test', 'subject': 'Re : AG', 'html': '<p>Bien reçu.</p>',
                                   'draft_uid': out['uid']})
        rmsg = BytesParser(policy=policy.default).parsebytes(WebBox.store['Drafts'][int(reply['uid'])][1])
        self.assertEqual(rmsg['In-Reply-To'], '<a1@x>')
        self.assertNotIn(int(out['uid']), WebBox.store['Drafts'])                                 # brouillon précédent de la rédaction remplacé
        with self.assertRaisesRegex(Stop, 'destinataire'):
            w.save(self.desk, {'mode': 'nouveau', 'to': 'pas une adresse', 'subject': 'x', 'html': 'x'})

    def test_send_is_refused_when_disabled_then_needs_a_confirmation(self):
        from agent import send490, web490
        data = {'mode': 'repondre', 'source_uid': '1', 'to': 'durand@example.test', 'bcc': 'archive@example.test', 'subject': 'Re : AG', 'html': '<p>Bien <b>reçu</b>.</p>'}
        refused = web490.webmail_send(self.desk, data)
        self.assertEqual((refused['token'], refused['reason']), ('', 'envoi_desactive'))
        self.assertIn('Brouillons', refused['message'])
        self.desk.c['mail']['smtp'] = {'host': 'smtp.example.test', 'password_file': '/nonexistent'}
        self.desk.setting('send490:enabled', True)
        ready = web490.webmail_send(self.desk, data)
        self.assertTrue(ready['token'])                                              # message de la messagerie : aucun dossier exigé
        self.assertEqual(ready['summary']['bcc'], 'archive@example.test')
        sent = []
        with patch('agent.send490._smtp_send', side_effect=lambda cfg, message, frm, rcpt: sent.append((message, rcpt))):
            out = send490.confirm(self.desk, ready['token'], now=time.time() + 2)
        self.assertTrue(out['sent'])
        message, recipients = sent[0]
        self.assertEqual(sorted(recipients), ['archive@example.test', 'durand@example.test'])
        self.assertIsNone(message['Bcc'])
        self.assertFalse(any(k.lower().startswith('x-axiorhub') for k in message.keys()))
        self.assertIn('<b>reçu</b>', message.get_body(preferencelist=('html',)).get_content())
        self.assertTrue(WebBox.store['Sent'])

    def test_archive_move_junk_trash_and_flags(self):
        from agent import webmail5628 as w
        out = w.act(self.desk, 'archive', '1')
        self.assertEqual(out['folder'], 'INBOX.Archives' if False else 'Archives')
        self.assertIn(('CREATE', 'Archives'), WebBox.log)
        self.assertEqual(len(WebBox.store['Archives']), 1)
        w.act(self.desk, 'junk', '2')
        self.assertEqual(len(WebBox.store['Spam']), 1)
        w.act(self.desk, 'move', '1', 'Archives', 'INBOX.Clients')
        self.assertEqual(len(WebBox.store['INBOX.Clients']), 1)
        w.act(self.desk, 'trash', '1', 'INBOX.Clients')
        self.assertEqual(len(WebBox.store['Trash']), 1)
        with self.assertRaisesRegex(Stop, 'deja_dans_la_corbeille'):
            w.act(self.desk, 'trash', '1', 'Trash')
        with self.assertRaisesRegex(Stop, 'dossier_messagerie_inconnu'):
            w.act(self.desk, 'move', '1', 'Trash', 'Inventé')
        w.act(self.desk, 'non_lu', '1', 'Trash')
        self.assertIn(('UID', 'STORE', '1', '-FLAGS.SILENT', '(\\Seen)'), WebBox.log)
        with self.assertRaisesRegex(Stop, 'action_inconnue'):
            w.act(self.desk, 'detruire', '1', 'Trash')

    def test_move_without_imap_move_copies_flags_and_expunges(self):
        from agent import webmail5628 as w
        WebBox.reset(caps=('IMAP4REV1', 'UIDPLUS'))
        w.act(self.desk, 'trash', '1')
        cmds = [x[1] for x in WebBox.log if x[0] == 'UID']
        self.assertEqual(cmds[-3:], ['COPY', 'STORE', 'EXPUNGE'])
        self.assertNotIn(1, WebBox.store['INBOX'])
        self.assertEqual(len(WebBox.store['Trash']), 1)

    def test_rights_routes_and_assets(self):
        from agent import webmail5628 as w
        from agent.standalone_auth import allowed
        for route in ('m5628/action', 'm5628/brouillon', 'm5628/envoyer', 'm5628/envoyer/confirmer'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + route))
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + route))
        with patch('agent.web567.actor', return_value=('x', 'assistant')):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                w.api({}, self.desk, {}, '', 'm5628/modele', {'mode': 'nouveau'}, 'GET')
        page = self.request('/courriels', query='vue=boite')['body']
        self.assertIn('/static/v5628.js', page)
        js = (ROOT / 'agent' / 'static' / 'v5628.js').read_text(encoding='utf-8')
        for hook in ('Répondre à tous', 'Transférer', 'Archiver', 'Indésirable', 'Supprimer', 'Cci', 'm5628/envoyer/confirmer', 'execCommand', 'Nouveau message'):
            self.assertIn(hook, js)
        self.assertIn('window.axh5628.toolbar', (ROOT / 'agent' / 'static' / 'v5625.js').read_text(encoding='utf-8'))
        css = (ROOT / 'agent' / 'static' / 'v5628.css').read_text(encoding='utf-8')
        self.assertNotIn('prefers-color-scheme', css)
        self.assertIn('.tk28-item', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))


class ForcedAIReply(unittest.TestCase):
    """« Répondre avec l'IA » : une demande de l'avocat passe outre les filtres automatiques et vise les destinataires d'un message du cabinet."""

    def _mail(self, sender, to, headers=None, flags=()):
        m = EmailMessage()
        m['From'], m['To'], m['Subject'], m['Message-ID'] = sender, to, 'Lettre d’information', '<n1@x>'
        for k, v in (headers or {}).items():
            m[k] = v
        m.set_content('Bonjour')
        return Mail('5', '7', 'INBOX', set(flags), datetime(2026, 10, 9, tzinfo=timezone.utc), m)

    def test_manual_request_skips_automatic_filters_but_not_deleted_messages(self):
        from agent.mailbox import Mailbox
        cfg = {'own_addresses': ['cabinet@example.test'], 'from_address': 'cabinet@example.test', 'sent': 'Sent'}
        news = self._mail('Lettre <news@media.test>', 'cabinet@example.test', {'List-Unsubscribe': '<mailto:u@x>'}, ('\\Seen',))
        box = Mailbox.__new__(Mailbox)
        box.cfg = cfg
        box.fetch = lambda folder, uid, headers_only=False: news
        box.existing_draft = lambda mail, mid=None: False
        box.thread = lambda mail: []
        box.search = lambda *a: []
        self.assertEqual(box.preflight(news, '<d@x>'), 'deja_lu')
        self.assertEqual(box.preflight(news, '<d@x>', manual=True), '')
        deleted = self._mail('client@example.test', 'cabinet@example.test', flags=('\\Deleted',))
        box.fetch = lambda folder, uid, headers_only=False: deleted
        self.assertEqual(box.preflight(deleted, '<d@x>', manual=True), 'marque_supprime')

    def test_reply_to_a_cabinet_message_goes_to_its_recipients(self):
        from agent.mailbox import make_draft
        cfg = {'from_name': 'Cabinet', 'from_address': 'cabinet@example.test', 'signature': '--'}
        own = self._mail('Cabinet <cabinet@example.test>', 'romero@avocats.test')
        draft = make_draft(own, cfg, 'Texte', 'k1', to='romero@avocats.test')
        self.assertEqual(draft['To'], 'romero@avocats.test')
        with self.assertRaisesRegex(Stop, 'reponse_a_soi_meme_refusee'):   # 5.6.43 (§ 21.2) : jamais de brouillon adressé au cabinet
            make_draft(own, cfg, 'Texte', 'k2')
        src = (ROOT / 'agent' / 'intelligence.py').read_text(encoding='utf-8')
        self.assertIn("manual_key=digest(key+'|manual-v15'+(('|'+str(manual.get('at',''))) if manual else ''))", src)   # chaque relance = nouveau brouillon
        self.assertIn("if not manual and existing and existing[0] in ('drafted'", src)


def _cabinet_message_reply(self):
    from agent import boite5625
    WebBox.store['INBOX'][3] = (set(), _raw('Cabinet <cabinet@example.test>', 'Confrère <romero@avocats.test>', 'Salle de réunion', 'Bonjour', '<s3@x>',
                                            cc='cabinet@example.test, associe@example.test'))
    out = boite5625.request_reply(self.desk, '3', '', 'Relance', without_matter=True)
    from agent.desk import report_for
    self.assertEqual(report_for(self.desk.c, out['key'])['reply_recipients'], ['romero@avocats.test', 'associe@example.test'])
    js = (ROOT / 'agent' / 'static' / 'v5625.js').read_text(encoding='utf-8')
    self.assertIn('Répondre avec l’IA', js)
    self.assertNotIn('Demander une réponse à l’agent', js)
    self.assertIn('Répondre avec l’IA', (ROOT / 'agent' / 'static' / 'v5628.js').read_text(encoding='utf-8'))


Webmail.test_request_on_cabinet_message_targets_external_recipients = _cabinet_message_reply

class Tasks(t530.Base):
    def test_tasks_are_grouped_by_due_date_with_add_form_beside(self):
        from datetime import date, timedelta
        from agent.improvements36 import create_task
        today = date.today()
        create_task(self.desk, 'Relancer le confrère', '20240101001', (today - timedelta(days=2)).isoformat())
        create_task(self.desk, 'Préparer l’audience', '', (today + timedelta(days=3)).isoformat())
        create_task(self.desk, 'Classer les pièces', '', '')
        page = self.request('/planning', query='vue=taches')['body']
        for text in ('Mes tâches', 'En retard', 'Cette semaine', 'Sans échéance', 'Ajouter une tâche', 'Ajouter la tâche', 'Marquer terminée', 'Supprimer',
                     'Modifier ou programmer', 'Programmer dans l’agenda', 'en retard de 2 j', 'tk28-side', '/dossier?id=20240101001'):
            self.assertIn(text, page)
        self.assertLess(page.index('Relancer le confrère'), page.index('Préparer l’audience'))
        late = self.request('/planning', query='vue=taches&filtre=retard')['body']
        self.assertIn('Relancer le confrère', late)
        self.assertNotIn('Classer les pièces', late)
        only = self.request('/planning', query='vue=taches&dossier=20240101001')['body']
        self.assertNotIn('Préparer l’audience', only)


if __name__ == '__main__':
    unittest.main()
