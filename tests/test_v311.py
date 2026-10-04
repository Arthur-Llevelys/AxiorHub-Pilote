"""3.1.1: test IMAP readback and never repeat uncertain APPEND."""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import json
import unittest

from agent.common import Stop
from agent.engine import Engine
from agent.mailbox import Mailbox, make_draft
from agent.workstation import why_nothing
from test_agent import EngineTests, mail
from agent.desk import Desk


class StoredMailbox(Mailbox):
    """Use the production verification code with an in-memory IMAP store."""
    def __init__(self):
        self.cfg={'drafts':'INBOX.Drafts'}
        self.saved=[]
    def search(self,folder,*criteria):
        if folder != self.cfg['drafts']: raise AssertionError(folder)
        return [str(i+1) for i,m in enumerate(self.saved)
                if m.mid == criteria[-1].strip('"')]
    def fetch(self,folder,uid,headers_only=False):
        return self.saved[int(uid)-1]


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        f=EngineTests('test_observation_has_no_mail_write')
        f.setUp();self.addCleanup(f.doCleanups)
        self.f=f
        self.source=mail(flags={'\\Seen'})
        f.c['mail']['process_seen_recent']=True
        self.draft=make_draft(self.source,f.c['mail'],'Bonjour, je reviendrai vers vous.','a'*64)
        self.box=StoredMailbox()

    def saved(self, *, flags={'\\Draft'}, change=None):
        raw=self.draft.as_bytes(policy=policy.SMTP)
        message=BytesParser(policy=policy.default).parsebytes(raw)
        if change:message[change[0]]=change[1]
        from agent.mailbox import Mail
        self.box.saved.append(Mail(str(len(self.box.saved)+1),'23','INBOX.Drafts',set(flags),
                                   datetime.now(timezone.utc),message))

    def test_saved_draft_is_read_back_with_correct_uid_and_content(self):
        self.saved()
        result=self.box.verify_draft(self.draft)
        self.assertEqual((result['folder'],result['uid'],result['uidvalidity']),
                         ('INBOX.Drafts','1','23'))
        self.assertTrue(self.box.find_own_draft(self.draft['Message-ID']))

    def test_folded_private_key_preserves_exact_identity_and_rejects_duplicates(self):
        self.saved()
        saved=self.box.saved[0].msg
        self.assertEqual(str(saved.get('X-AxiorHub-Draft-Key')).strip(), 'a'*64)
        self.assertTrue(self.box.find_own_draft(self.draft['Message-ID']))
        saved.replace_header('X-AxiorHub-Draft-Key','a'*32+' '+'a'*32)
        with self.assertRaisesRegex(Stop,'brouillon_contenu_non_conforme'):
            self.box.verify_draft(self.draft)
        saved.replace_header('X-AxiorHub-Draft-Key','a'*64)
        saved['X-AxiorHub-Draft-Key']='a'*64
        with self.assertRaisesRegex(Stop,'brouillon_contenu_non_conforme'):
            self.box.verify_draft(self.draft)

    def test_missing_wrong_flag_wrong_recipient_and_duplicates_are_not_success(self):
        with self.assertRaisesRegex(Stop,'brouillon_non_retrouve'):
            self.box.verify_draft(self.draft)
        self.saved(flags=set())
        with self.assertRaisesRegex(Stop,'brouillon_contenu_non_conforme'):
            self.box.verify_draft(self.draft)
        self.box.saved.clear();self.saved(change=('Cc','adverse@example.test'))
        with self.assertRaisesRegex(Stop,'brouillon_contenu_non_conforme'):
            self.box.verify_draft(self.draft)
        self.saved()
        with self.assertRaisesRegex(Stop,'plusieurs_brouillons_meme_identifiant'):
            self.box.verify_draft(self.draft)

    def test_uncertain_append_remains_reserved_without_retry(self):
        f=self.f
        f.box.fail_append=True
        key=f.engine.process(mail())
        self.assertEqual(f.engine.state.get(key)[0],'append_uncertain')
        f.box.fail_append=False
        f.engine.process(mail())
        self.assertEqual(f.engine.state.get(key)[0],'append_uncertain')
        self.assertEqual(f.box.appended,[])
        info=why_nothing(Desk(f.c))
        self.assertEqual(info['uncertain_drafts'],1)
        self.assertTrue(info['recent_mail_issues'])
        self.assertEqual(info['verified_drafts_24h'],0)

    def test_generated_draft_report_is_verified_before_success(self):
        f=self.f
        key=f.engine.process(mail())
        self.assertEqual(f.engine.state.get(key)[0],'drafted')
        report=json.loads((f.state_dir/'reports'/(key+'.json')).read_text())
        self.assertEqual(report['reason'],'brouillon_imap_verifie')


if __name__=='__main__':unittest.main()
