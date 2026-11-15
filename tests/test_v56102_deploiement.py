"""5.6.102 : mise en place du dossier app-<version> robuste à un verrou passager (antivirus) pendant l'installation Windows."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

from sqlite_cleanup import TempDir

ROOT = Path(__file__).resolve().parents[1]


class RenameRetries(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from windows import deploiement
        self.d = deploiement
        self.tmp = TempDir(); self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AXIORHUB_WIN_LOCALAPPDATA': str(base / 'local'), 'AXIORHUB_WIN_MENU': str(base / 'menu'),
                                           'AXIORHUB_WIN_BUREAU': str(base / 'bureau'), 'AXIORHUB_WIN_DEMARRAGE': str(base / 'demarrage')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.archive = base / 'app.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('AxiorHub Pilote/AxiorHub Pilote.exe', b'MZ factice')
            z.writestr('AxiorHub Pilote/_internal/agent/__init__.py', b"__version__='9.9.1'\n")
        self.root = self.d.install_root()
        self.d.install(self.archive, '9.9.0', shortcuts=False, registry=False)   # version déjà installée
        self.real_replace = os.replace

    def _locked(self, winerror=None):
        if winerror is None:
            return PermissionError(13, 'Accès refusé (factice)')
        exc = OSError(13, 'Le fichier est utilisé par un autre processus (factice)')
        exc.winerror = winerror
        return exc

    def test_two_transient_locks_then_success(self):
        failures = [self._locked(), self._locked(winerror=32)]
        calls = []

        def flaky_replace(src, dst):
            calls.append(Path(dst).name)
            if failures:
                raise failures.pop(0)
            return self.real_replace(src, dst)
        with patch.object(self.d.os, 'replace', side_effect=flaky_replace), patch.object(self.d.time, 'sleep') as sleep:
            exe = self.d.install(self.archive, '9.9.1', shortcuts=False, registry=False)
        self.assertEqual(calls, ['app-9.9.1'] * 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(self.d.RENAME_DELAYS[:2]))   # attente croissante
        self.assertTrue(exe.is_file()); self.assertEqual(exe.parent.name, 'app-9.9.1')
        self.assertEqual(sorted(x.name for x in self.root.glob('app-*')), ['app-9.9.1'])
        self.assertEqual((self.root / 'version.txt').read_text(encoding='utf-8').strip(), '9.9.1')
        self.assertEqual(list(self.root.glob('.installation-*')), [])

    def test_persistent_lock_leaves_the_installed_version_untouched(self):
        old_exe = self.root / 'app-9.9.0' / 'AxiorHub Pilote.exe'
        before = old_exe.read_bytes()
        with patch.object(self.d.os, 'replace', side_effect=self._locked()) as replace, \
                patch.object(self.d.time, 'sleep') as sleep:
            with self.assertRaises(self.d.InstallationBloquee) as ctx:
                self.d.install(self.archive, '9.9.1', shortcuts=False, registry=False)
        message = str(ctx.exception)
        self.assertIn('app-9.9.1', message); self.assertIn('La version déjà installée n’a pas été modifiée', message)
        self.assertIn('relancez l’installation', message)
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)
        self.assertEqual(replace.call_count, len(self.d.RENAME_DELAYS) + 1)
        self.assertEqual(sleep.call_count, len(self.d.RENAME_DELAYS))
        self.assertLessEqual(sum(self.d.RENAME_DELAYS), 10.5)
        self.assertEqual(sorted(x.name for x in self.root.glob('app-*')), ['app-9.9.0'])           # ancienne version en place
        self.assertEqual(old_exe.read_bytes(), before)
        self.assertEqual((self.root / 'version.txt').read_text(encoding='utf-8').strip(), '9.9.0')
        self.assertEqual(list(self.root.glob('.installation-*')), [])                             # dossier temporaire retiré

    def test_other_errors_are_not_retried(self):
        with patch.object(self.d.os, 'replace', side_effect=FileNotFoundError(2, 'introuvable (factice)')) as replace, \
                patch.object(self.d.time, 'sleep') as sleep:
            with self.assertRaises(FileNotFoundError):
                self.d.install(self.archive, '9.9.1', shortcuts=False, registry=False)
        self.assertEqual(replace.call_count, 1); sleep.assert_not_called()
        self.assertEqual((self.root / 'version.txt').read_text(encoding='utf-8').strip(), '9.9.0')

    def test_locked_previous_copy_of_the_same_version_is_retried(self):
        real_rmtree = self.d.shutil.rmtree
        failures = [self._locked()]

        def flaky_rmtree(path, *a, **k):
            if Path(path).name == 'app-9.9.0' and failures:
                raise failures.pop(0)
            return real_rmtree(path, *a, **k)
        with patch.object(self.d.shutil, 'rmtree', side_effect=flaky_rmtree), patch.object(self.d.time, 'sleep') as sleep:
            exe = self.d.install(self.archive, '9.9.0', shortcuts=False, registry=False)   # réinstallation de la même version
        self.assertEqual(sleep.call_count, 1); self.assertTrue(exe.is_file())


if __name__ == '__main__':
    unittest.main()
