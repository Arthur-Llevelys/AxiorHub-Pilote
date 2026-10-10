"""Fresh, cumulative and bounded-release installation tests for AxiorHub 5.0.1."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

if os.name == 'nt':   # 5.6.25 : installateur du serveur Linux (comptes système pwd/grp) ; le poste Windows a son propre installateur
    raise unittest.SkipTest('installateur serveur Linux : sans objet sous Windows')

import installer


def package(root):
    source=root/'package';deploy=source/'deploy';deploy.mkdir(parents=True)
    files={'deploy/axiorhub-mail':'#!/bin/sh\n',
      **{'deploy/'+name:'[Unit]\nDescription=test\n' for name in installer.CORE_UNITS}}
    for name,content in files.items():(source/name).write_text(content)
    (source/'MANIFEST.sha256').write_text(''.join(
      hashlib.sha256((source/name).read_bytes()).hexdigest()+'  '+name+'\n'
      for name in sorted(files)))
    return source


def verified_release(path):
    path.mkdir(parents=True);(path/'code.py').write_text(path.name)
    (path/'MANIFEST.sha256').write_text(
      hashlib.sha256((path/'code.py').read_bytes()).hexdigest()+'  code.py\n')


@unittest.skipUnless(os.geteuid()==0,'Le test de propriétaires exige root')
class StandaloneInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.source=package(root)
        self.base=root/'opt';self.config=root/'etc';self.state=root/'state'
        self.launcher=root/'bin'/'axiorhub-mail';self.systemd=root/'systemd'

    def test_one_archive_provisions_a_fresh_server_without_fake_secrets(self):
        release=installer.provision_files(self.source,self.base,self.config,self.state,
          self.launcher,self.systemd,os.getuid(),os.getgid(),os.getuid(),os.getgid())
        self.assertEqual(release.name,'5.6.26')
        self.assertEqual((self.base/'current').resolve(),release)
        self.assertTrue(self.launcher.is_file())
        self.assertEqual({x.name for x in self.systemd.iterdir()},set(installer.CORE_UNITS))
        self.assertFalse((self.config/'config.json').exists())
        self.assertEqual(list((self.config/'secrets').iterdir()),[])
        self.assertEqual(self.state.stat().st_mode & 0o777,0o700)

    def test_partial_install_is_refused_without_overwrite(self):
        self.config.mkdir();(self.config/'config.json').write_text('{}')
        self.assertEqual(installer.installation_state(self.base,self.config),'partial')
        with self.assertRaisesRegex(RuntimeError,'partielle'):
            installer.provision_files(self.source,self.base,self.config,self.state,
              self.launcher,self.systemd,os.getuid(),os.getgid(),os.getuid(),os.getgid())

    def test_unmanifested_file_is_refused(self):
        (self.source/'injected.sh').write_text('#!/bin/sh\n')
        with self.assertRaisesRegex(RuntimeError,'hors manifeste'):
            installer.provision_files(self.source,self.base,self.config,self.state,
              self.launcher,self.systemd,os.getuid(),os.getgid(),os.getuid(),os.getgid())

    def test_cleanup_preserves_active_and_rollback_release(self):
        releases=self.base/'releases'
        for version in ('3.9.3','4.0.0','4.1.0','5.6.26'):
            verified_release(releases/version)
        (self.base/'current').symlink_to(releases/'5.6.26')
        (self.base/'upgrade-5.6.26.json').write_text(json.dumps({
          'previous_release':str(releases/'4.1.0')}))
        result=installer.prune_old_releases(self.base,keep=2)
        self.assertEqual(set(result['removed']),{'3.9.3','4.0.0'})
        self.assertTrue((releases/'5.6.26').is_dir())
        self.assertTrue((releases/'4.1.0').is_dir())


if __name__=='__main__':unittest.main()
