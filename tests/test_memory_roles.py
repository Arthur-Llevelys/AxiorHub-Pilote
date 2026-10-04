import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

from agent.common import digest, private_json
from agent.memory import SentMemory, scope_detail
import test_memory
import test_agent as fixtures


class RoleMemoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_memory.MemoryTests(methodName='test_seed_scoped_sent_message')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.memory = self.fixture.memory
        self.sent = self.fixture.sent
        self.matter = self.f.matters[0]
        self.matter['correspondents'] += [
            {'email':'opponent@example.test','role':'confrere_adverse'},
            {'email':'opponent2@example.test','role':'confrere_adverse'},
            {'email':'expert@example.test','role':'tiers'},
            {'email':'prospect@example.test','role':'prospect'}]

    def test_each_role_keeps_only_its_own_text(self):
        targets = ['client@example.test','opponent@example.test','expert@example.test','prospect@example.test']
        for i, target in enumerate(targets):
            sent = self.sent(str(100+i), target)
            sent.msg.set_content('Exemple confidentiel de cette audience seulement : '+target)
            self.assertEqual(self.memory.ingest(sent,self.f.matters,[]),'sent_example')
        for target in targets:
            samples = self.memory.examples(self.matter,[target])
            self.assertEqual(len(samples),1)
            self.assertIn(target,samples[0]['final_sent_text'])
            for other in set(targets)-{target}:
                self.assertNotIn(other,samples[0]['final_sent_text'])
        self.assertEqual(self.memory.status()['examples_by_role'],
                         {'client':1,'confrere_adverse':1,'tiers':1,'prospect':1})

    def test_role_change_invalidates_old_example_even_for_same_address(self):
        self.memory.ingest(self.sent(),self.f.matters,[])
        self.matter['correspondents'][0]['role']='confrere_adverse'
        self.assertEqual(self.memory.examples(self.matter,['client@example.test']),[])

    def test_mixed_client_and_opponent_excluded(self):
        sent=self.sent();sent.msg['Cc']='opponent@example.test'
        self.assertEqual(self.memory.ingest(sent,self.f.matters,[]),'scope_excluded')
        self.assertEqual(scope_detail(sent,self.f.matters,{'cabinet@example.test'})[2],
                         'dossier_ambigu_ou_roles_mixtes')

    def test_group_of_opponents_is_separate_from_single_opponent(self):
        sent=self.sent(to='opponent@example.test');sent.msg['Cc']='opponent2@example.test'
        self.assertEqual(self.memory.ingest(sent,self.f.matters,[]),'sent_example')
        self.assertEqual(self.memory.examples(self.matter,['opponent@example.test']),[])
        self.assertEqual(len(self.memory.examples(self.matter,['opponent@example.test','opponent2@example.test'])),1)

    def test_conflicting_roles_in_registry_excluded(self):
        self.matter['correspondents'].append({'email':'client@example.test','role':'tiers'})
        self.assertEqual(self.memory.ingest(self.sent(),self.f.matters,[]),'scope_excluded')

    def test_rescan_old_policy_and_diagnostics_then_incremental(self):
        sent=self.sent(to='opponent@example.test')
        box=self.f.box;box.search=lambda *args:[sent.uid];box.fetch=lambda *a,**kw:sent
        old_epoch=digest(json.dumps([self.f.matters,self.f.c['mail']['own_addresses']],sort_keys=True))
        key=digest('|'.join([self.memory.account(),'Sent',box.validity,old_epoch,sent.uid]))
        self.memory.db.execute('INSERT INTO seen VALUES (?)',(key,));self.memory.db.commit()
        result=self.memory.collect(box,self.f.matters)
        self.assertEqual(result['stored'],1)
        self.assertEqual(result['reasons'],{'sent_example':1})
        self.assertEqual(self.memory.status()['last_learning']['stored'],1)
        self.assertEqual(self.memory.collect(box,self.f.matters)['examined'],0)

    def test_unknown_recipient_has_explicit_diagnostic(self):
        sent=self.sent(to='unknown@example.test')
        box=self.f.box;box.search=lambda *args:[sent.uid];box.fetch=lambda *a,**kw:sent
        result=self.memory.collect(box,self.f.matters)
        self.assertEqual(result['reasons'],{'correspondant_non_associe':1})
        self.assertEqual(result['stored'],0)

    def test_same_role_other_matter_cannot_access(self):
        self.memory.ingest(self.sent(to='opponent@example.test'),self.f.matters,[])
        other=copy.deepcopy(self.matter);other['id']='DOS-OTHER'
        self.assertEqual(self.memory.examples(other,['opponent@example.test']),[])

    def test_automatic_run_uses_sent_correction_on_next_draft(self):
        self.f.engine.process(fixtures.mail())
        sent=self.sent();sent.msg['In-Reply-To']='<mail-1@example.test>'
        next_mail=fixtures.mail(uid='2')
        box=self.f.box;box.inputs={next_mail.uid:next_mail}
        box.search=lambda *args:[sent.uid]
        box.fetch=lambda folder,uid,headers_only=False:sent if folder=='Sent' else next_mail
        result=self.f.engine.run()
        self.assertEqual(result['learning']['matched'],1)
        payload=[p for stage,p in self.f.model.calls if stage=='compose'][-1]
        samples=[s for s in payload['sources'] if s['kind']=='sent_example']
        self.assertEqual(len(samples),1)
        self.assertTrue(samples[0]['earlier_ai_draft'])
        self.assertIn('Pouvez-vous préciser',samples[0]['final_sent_text'])
        self.assertEqual(len(box.appended),2)

    def test_opponent_memory_reaches_appointment_draft_without_client_memory(self):
        self.memory.ingest(self.sent(),self.f.matters,[])
        self.memory.ingest(self.sent('101','opponent@example.test'),self.f.matters,[])
        self.f.model.intent='appointment'
        private_json(self.f.matter_file,self.f.matters)
        self.f.engine.matters=self.f.matters
        key=self.f.engine.process(fixtures.mail(sender='opponent@example.test'))
        self.assertEqual(self.f.status(key),'drafted')
        payload=dict(self.f.model.calls)['compose']
        samples=[s for s in payload['sources'] if s['kind']=='sent_example']
        self.assertEqual(len(samples),1)
        self.assertEqual(samples[0]['recipient_role'],'confrere_adverse')
        self.assertEqual(self.f.dav.downloads,0)

    def test_new_memory_is_invisible_to_legacy_table_on_rollback(self):
        self.memory.db.execute('CREATE TABLE examples (key TEXT PRIMARY KEY, matter TEXT, recipients TEXT, sent_at TEXT, body TEXT, draft TEXT, stats TEXT, provenance TEXT, account TEXT)')
        self.memory.ingest(self.sent(to='opponent@example.test'),self.f.matters,[])
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM examples').fetchone()[0],0)

    def test_migration_retains_client_binding_and_forget_affects_both_tables(self):
        db=self.memory.db
        db.execute('CREATE TABLE examples (key TEXT PRIMARY KEY, matter TEXT, recipients TEXT, sent_at TEXT, body TEXT, draft TEXT, stats TEXT, provenance TEXT, account TEXT)')
        key='a'*64
        db.execute('INSERT INTO examples VALUES (?,?,?,?,?,?,?,?,?)',
                   (key,self.matter['id']+'@'+digest(self.matter['path']),json.dumps(['client@example.test']),
                    datetime.now(timezone.utc).isoformat(),'Réponse client historique confidentielle','','{}','sent_example',self.memory.account()))
        db.commit()
        migrated=SentMemory(self.f.c)
        self.assertEqual(len(migrated.examples(self.matter,['client@example.test'])),1)
        self.matter['correspondents'][0]['role']='confrere_adverse'
        self.assertEqual(migrated.examples(self.matter,['client@example.test']),[])
        migrated.forget(key)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM examples').fetchone()[0],0)
        self.assertEqual(SentMemory(self.f.c).status()['examples'],0)

    def test_role_changed_after_draft_does_not_pair_old_private_draft(self):
        self.f.engine.process(fixtures.mail())
        sent=self.sent();sent.msg['In-Reply-To']='<mail-1@example.test>'
        self.matter['correspondents'][0]['role']='confrere_adverse'
        self.assertEqual(self.memory.ingest(sent,self.f.matters,self.memory.proposals()),'sent_example')
        self.assertEqual(self.memory.examples(self.matter,['client@example.test'])[0]['earlier_ai_draft'],'')

    def test_changed_path_after_draft_does_not_pair(self):
        self.f.engine.process(fixtures.mail())
        sent=self.sent();sent.msg['In-Reply-To']='<mail-1@example.test>'
        self.matter['path']='/Dossiers/OTHER'
        self.assertEqual(self.memory.ingest(sent,self.f.matters,self.memory.proposals()),'sent_example')
