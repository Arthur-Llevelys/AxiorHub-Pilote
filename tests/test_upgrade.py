import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import upgrade


class UpgradeTests(unittest.TestCase):
    def test_settings_preserve_account_and_custom_limits(self):
        before = {'mail': {'host': 'imap.example.test', 'max_body_chars': 150000},
                  'ollama': {'model': 'local', 'num_ctx': 65536, 'max_context_chars': 100000},
                  'mode': 'drafts', 'matters_file': '/registry'}
        after = json.loads(upgrade.updated_config(json.dumps(before).encode()))
        self.assertEqual(after['mail']['host'], before['mail']['host'])
        self.assertEqual(after['mail']['max_body_chars'], before['mail']['max_body_chars'])
        self.assertEqual(after['ollama'], before['ollama'])
        self.assertEqual(after['matters_file'], before['matters_file'])
        self.assertEqual(after['mode'], before['mode'])
        self.assertEqual(after['rag']['embedding_model'],'qwen3-embedding:0.6b')
        self.assertTrue(after['automation']['sync_enabled'])
        self.assertFalse(after['automation']['index_all_enabled'])
        self.assertFalse(after['automation']['daily_digest_enabled'])
        self.assertTrue(after['automation']['reconcile_inbox_enabled'])
        self.assertEqual(after['portfolio']['active_days'],180)
        self.assertEqual(after['portfolio']['archive_days'],730)
        self.assertEqual(after['portfolio']['bootstrap_months'],18)
        self.assertTrue(after['document_projects']['enabled'])
        self.assertEqual(after['document_projects']['approval_minutes'],60)
        self.assertEqual(after['document_projects']['destination_subfolder'],
                         '20_Actes_et_conclusions/90_AxiorHub_Brouillons')
        self.assertEqual(after['nextcloud_documents']['max_generated_file_bytes'],20_000_000)
        self.assertTrue(after['autonomy']['enabled'])
        self.assertTrue(after['autonomy']['automatic_document_previews_enabled'])
        self.assertTrue(after['autonomy']['automatic_internal_files_enabled'])
        self.assertTrue(after['autonomy']['document_control_enabled'])
        self.assertTrue(after['autonomy']['diligence_proposals_enabled'])
        self.assertTrue(after['autonomy']['billing_proposals_enabled'])
        self.assertTrue(after['hearing']['enabled'])
        self.assertEqual(after['hearing']['approval_minutes'],60)
        self.assertTrue(after['word_legal']['clean_and_compared'])
        self.assertEqual(after['word_legal']['tracked_author'],'AxiorHub')
        self.assertTrue(after['orchestrator']['enabled'])
        self.assertTrue(after['orchestrator']['single_notification'])
        self.assertTrue(after['orchestrator']['automatic_mail_drafts_enabled'])
        self.assertTrue(after['orchestrator']['automatic_legal_projects_enabled'])
        self.assertTrue(after['production']['enabled'])
        self.assertEqual(after['production']['max_automatic_attempts'],3)
        self.assertTrue(after['opinions']['numeric_probability_forbidden'])
        self.assertTrue(after['cabinet_pilotage']['enabled'])
        self.assertEqual(after['cabinet_pilotage']['daily_capacity_minutes'],420)
        self.assertEqual(after['hybrid_routing']['mode'],'local')
        self.assertTrue(after['hybrid_routing']['anonymize_external'])
        self.assertTrue(after['hybrid_routing']['escalate_after_local_failure'])
        self.assertEqual(after['hybrid_routing']['excluded_matters'],[])

    def test_only_empty_false_discoveries_are_pruned_and_backed_up(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td);registry=state/'registry-web.json'
            rows=[
                {'id':'GOOD','path':'/CABINET EXEMPLE/01 - Dossiers/GOOD - 20260001','discovered_at':'x','correspondents':[]},
                {'id':'FALSE','path':'/CABINET EXEMPLE/10 - IA/FAUX - 20260002','discovered_at':'x','correspondents':[]},
                {'id':'KEPT','path':'/CABINET EXEMPLE/10 - IA/LIE - 20260003','discovered_at':'x','correspondents':[{'email':'x@example.test','role':'client'}]},
            ]
            registry.write_text(json.dumps(rows))
            count,backup=upgrade.prune_false_discoveries(state,{'nextcloud':{'matter_roots':['/CABINET EXEMPLE/01 - Dossiers']}})
            self.assertEqual(count,1);self.assertTrue(Path(backup).is_file())
            self.assertEqual({x['id'] for x in json.loads(registry.read_text())},{'GOOD','KEPT'})


@unittest.skipUnless(os.geteuid() == 0, 'Installation root simulée dans un dossier temporaire')
class UpgradeFilesystemTests(unittest.TestCase):
    def test_upgrade_from_5624_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.24'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5623_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.23'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5622_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.22'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5621_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.21'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5620_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.20'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5619_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.19'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5618_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.18'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5617_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.17'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5616_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.16'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5615_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.15'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5614_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.14'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5613_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.13'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5612_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.12'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5611_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.11'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_5610_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.10'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_569_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.9'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_568_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.8'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_567_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.7'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_430_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.3.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
        self.assertEqual(self.config.read_bytes(),self.before)
    def test_upgrade_from_440_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.4.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_450_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.5.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_460_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.6.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_470_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.7.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_490_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.9.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_565_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.5'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_564_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.4'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_563_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.3'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_562_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.2'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_561_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_560_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.6.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_550_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.5.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_540_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.4.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_530_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.3.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_521_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.2.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_520_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.2.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_510_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.1.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_501_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.0.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_500_is_supported_and_reversible(self):
        later=self.base/'releases'/'5.0.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_upgrade_from_480_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.8.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
    def test_database_backup_contains_committed_wal_pages_and_is_private(self):
        path=self.state/'desk.sqlite3';db=sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE fixture(value TEXT)')
        db.execute('INSERT INTO fixture VALUES(?)',('committed fixture',));db.commit()
        self.addCleanup(db.close);self.apply()
        receipt=json.loads((self.base/'upgrade-5.6.25.json').read_text())
        backup=Path(receipt['database_backups'][0]);copy=sqlite3.connect(backup)
        try:self.assertEqual(copy.execute('SELECT value FROM fixture').fetchone()[0],'committed fixture')
        finally:copy.close()
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)

    def test_upgrade_from_420_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.2.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
        self.assertEqual(self.config.read_bytes(),self.before)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root/'opt'
        self.old = self.base/'releases'/'3.6.1'
        self.old.mkdir(parents=True)
        self.source = root/'package'
        self.source.mkdir()
        for folder, text in [(self.old, 'original'), (self.source, 'correctif')]:
            (folder/'code.py').write_text(text)
            (folder/'MANIFEST.sha256').write_text(hashlib.sha256(text.encode()).hexdigest()+'  code.py\n')
        (self.base/'current').symlink_to(self.old)
        self.config = root/'config.json'
        self.before = json.dumps({'mode': 'observe', 'mail': {'max_body_chars': 100000},
                                  'ollama': {'num_ctx': 49152, 'max_context_chars': 90000}}).encode()
        self.config.write_bytes(self.before)
        self.config.chmod(0o640)
        self.state = root/'state'
        self.state.mkdir()
        (self.state/'documents.sqlite3').write_bytes(b'index intact')
        (root/'matters.json').write_bytes(b'registre intact')

    def apply(self):
        upgrade.upgrade(self.source, self.base, self.config, self.state)

    def test_round_trip_preserves_state_and_permissions(self):
        self.apply()
        self.assertEqual((self.base/'current').resolve().name, '5.6.25')
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(json.loads(self.config.read_bytes())['mail']['max_body_chars'], 100000)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.apply()  # Idempotent; original backup is retained.
        upgrade.rollback(self.base, self.config, self.state)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual((self.base/'current').resolve(), self.old)
        self.assertEqual((self.state/'documents.sqlite3').read_bytes(), b'index intact')
        self.assertEqual((self.config.parent/'matters.json').read_bytes(), b'registre intact')

    def test_upgrade_from_351_rolls_back_to_same_release(self):
        later=self.base/'releases'/'3.5.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink()
        (self.base/'current').symlink_to(later)
        self.apply()
        self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)
        self.assertEqual(self.config.read_bytes(),self.before)

    def test_upgrade_from_381_is_supported_and_reversible(self):
        later=self.base/'releases'/'3.8.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_390_is_supported_and_reversible(self):
        later=self.base/'releases'/'3.9.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_391_is_supported_and_reversible(self):
        later=self.base/'releases'/'3.9.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_392_is_supported_and_reversible(self):
        later=self.base/'releases'/'3.9.2'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_393_is_supported_and_reversible(self):
        later=self.base/'releases'/'3.9.3'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_400_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.0.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_410_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.1.0'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_upgrade_from_411_is_supported_and_reversible(self):
        later=self.base/'releases'/'4.1.1'
        shutil.copytree(self.old,later)
        (self.base/'current').unlink();(self.base/'current').symlink_to(later)
        self.apply();self.assertEqual((self.base/'current').resolve().name,'5.6.25')
        upgrade.rollback(self.base,self.config,self.state)
        self.assertEqual((self.base/'current').resolve(),later)

    def test_modified_old_release_is_refused(self):
        (self.old/'code.py').write_text('changed')
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual((self.base/'current').resolve(), self.old)

    def test_busy_worker_blocks_upgrade(self):
        with upgrade.locked(self.state):
            with self.assertRaisesRegex(RuntimeError, 'traitement est en cours'):
                self.apply()
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_changed_config_survives_rollback(self):
        self.apply()
        self.config.write_bytes(self.before+b' ')
        upgrade.rollback(self.base, self.config, self.state)
        self.assertEqual(self.config.read_bytes(),self.before+b' ')

    def test_service_quiescence_holds_processing_lock_and_preserves_auth(self):
        auth=self.config.parent/'ui-auth.json';auth.write_bytes(b'credentials unchanged')
        called=[]
        def quiesce():
            with self.assertRaises(RuntimeError):
                with upgrade.locked(self.state):pass
            called.append(True)
        upgrade.upgrade(self.source,self.base,self.config,self.state,quiesce=quiesce)
        self.assertEqual(called,[True])
        self.assertEqual(auth.read_bytes(),b'credentials unchanged')
        upgraded=json.loads(self.config.read_bytes())
        self.assertEqual(upgraded['mail']['max_body_chars'],json.loads(self.before)['mail']['max_body_chars'])
        self.assertEqual(self.config.read_bytes(),self.before)
        self.assertEqual(upgraded['ollama'],json.loads(self.before)['ollama'])
        self.assertEqual(upgraded['mode'],'observe')


if __name__ == '__main__':
    unittest.main()
