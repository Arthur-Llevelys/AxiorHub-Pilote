"""5.6.15 : pont de configuration runtime préservé et rétabli, refus lisibles à l'enregistrement, erreurs internes en JSON."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import config567, runtime_config567, web
from agent.common import Stop, private_json
from agent.web440 import human
import test_v510 as t510


def _symlinks_available(root):
    try:
        (root/'probe-link').symlink_to(root/'probe');return True
    except (OSError, NotImplementedError):
        return False


class Bridge(unittest.TestCase):
    def test_private_json_writes_through_the_symlink_and_keeps_it(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            if not _symlinks_available(root):self.skipTest('liens symboliques indisponibles sur ce poste')
            target=root/'runtime'/'config.json';target.parent.mkdir();target.write_text('{"a": 1}',encoding='utf-8')
            link=root/'etc'/'config.json';link.parent.mkdir();link.symlink_to(target)
            private_json(link,{'a':2})
            self.assertTrue(link.is_symlink());self.assertEqual(link.resolve(),target.resolve())
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'a':2})
            self.assertFalse((root/'etc'/'config.json.tmp').exists())

    def test_choose_keeps_the_newest_readable_copy_and_names_the_other(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);src=root/'config.json';dst=root/'runtime.json'
            src.write_text('{"v": "etc"}');dst.write_text('{"v": "runtime"}')
            os.utime(src,(2000,2000));os.utime(dst,(1000,1000))
            data,discarded=runtime_config567.choose(src,dst)
            self.assertEqual(json.loads(data),{'v':'etc'});self.assertEqual(discarded,dst)
            os.utime(dst,(3000,3000))
            data,discarded=runtime_config567.choose(src,dst)
            self.assertEqual(json.loads(data),{'v':'runtime'});self.assertEqual(discarded,src)
            dst.write_text('{"v": "etc"}');self.assertIsNone(runtime_config567.choose(src,dst)[1])
            dst.write_text('pas du json');os.utime(dst,(4000,4000))
            data,discarded=runtime_config567.choose(src,dst)
            self.assertEqual(json.loads(data),{'v':'etc'});self.assertEqual(discarded,dst)

    @unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'migration systemd réservée à root')
    def test_enable_restores_the_link_when_etc_holds_a_plain_file_again(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state';state.mkdir();etc=Path(root)/'etc';etc.mkdir(mode=0o750)
            source=etc/'config.json';source.write_text(json.dumps({'state_dir':str(state),'v':'runtime'}))
            target=runtime_config567.enable(source,state,os.getuid(),os.getgid())
            # « axiorhub-mail mode » d'une version précédente remplaçait le lien par un fichier ordinaire plus récent.
            source.unlink();source.write_text(json.dumps({'state_dir':str(state),'v':'etc'}))
            os.utime(target,(1000,1000))
            self.assertEqual(runtime_config567.enable(source,state,os.getuid(),os.getgid()),target)
            self.assertTrue(source.is_symlink());self.assertEqual(json.loads(target.read_text())['v'],'etc')
            self.assertTrue(list(target.parent.glob('config-remplace-*.json')));self.assertTrue(list((etc/'backups').glob('config-fichier-*.json')))
            self.assertEqual(target.stat().st_mode & 0o777,0o600)


class SaveRefusals(t510.Base):
    def test_unwritable_configuration_is_a_readable_refusal_not_an_html_page(self):
        env={'axiorhub.config_path':str(self.config)};before=self.config.read_bytes()
        with patch.object(config567.tempfile,'mkstemp',side_effect=PermissionError(13,'Permission denied')):
            with self.assertRaisesRegex(Stop,'configuration_non_inscriptible'):
                config567.save(self.desk,{'revision':0,'values':{'speech568.voice':'ff_siwis'}},env)
        with patch.object(config567.tempfile,'mkstemp',side_effect=OSError(30,'Read-only file system')):
            with self.assertRaisesRegex(Stop,'configuration_non_inscriptible'):
                config567.save(self.desk,{'revision':0,'values':{'speech568.voice':'ff_siwis'}},env)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertIn('install-interface.py',human('configuration_non_inscriptible'))

    def test_other_errors_still_propagate(self):
        env={'axiorhub.config_path':str(self.config)}
        with patch.object(config567.tempfile,'mkstemp',side_effect=OSError(28,'No space left on device')):
            with self.assertRaises(OSError):config567.save(self.desk,{'revision':0,'values':{'speech568.voice':'ff_siwis'}},env)


class InternalErrors(unittest.TestCase):
    def test_api_routes_answer_json_and_pages_keep_html(self):
        import contextlib, io
        journal=io.StringIO()
        try:
            raise RuntimeError('synthetic')
        except RuntimeError:
            with contextlib.redirect_stderr(journal):
                kind,body=web.internal_error({'PATH_INFO':'/agent-courriel/api440/m567/config/save'},'text/html; charset=utf-8')
            self.assertTrue(kind.startswith('application/json'))
            self.assertEqual(json.loads(body)['error'],'interface_indisponible');self.assertIn('journalctl',json.loads(body)['message'])
            self.assertNotIn('synthetic',body);self.assertIn('synthetic',journal.getvalue())   # la trace va au journal, pas au navigateur
            with contextlib.redirect_stderr(journal):
                kind,body=web.internal_error({'PATH_INFO':'/agent-courriel/parametres/connexions'},'text/html; charset=utf-8')
            self.assertTrue(kind.startswith('text/html'));self.assertIn('Interface indisponible',body)

    def test_every_fetch_route_is_recognised_as_json_even_before_its_kind_is_set(self):
        for path in ('/agent-courriel/api440/m567/config/save','/agent-courriel/dictation','/agent-courriel/api/v1/jobs/12',
                     '/agent-courriel/live/snapshot','/agent-courriel/templates/upload','/agent-courriel/assistant/attachment',
                     '/agent-courriel/hearing/upload','/agent-courriel/extensions/upload'):
            self.assertTrue(web.wants_json({'PATH_INFO':path},'text/html; charset=utf-8'),path)
        for path in ('/agent-courriel/parametres/connexions','/agent-courriel/action','/agent-courriel/'):
            self.assertFalse(web.wants_json({'PATH_INFO':path},'text/html; charset=utf-8'),path)
        self.assertTrue(web.wants_json({'PATH_INFO':'/agent-courriel/'},'application/json; charset=utf-8'))
        self.assertIn('configuration_non_inscriptible',web.REASONS)


if __name__=='__main__':
    unittest.main()
