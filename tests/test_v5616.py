"""5.6.16 : l'installateur d'interface admet toujours la version active (celle du script)."""
import importlib.util
from pathlib import Path
import unittest

import agent

ROOT=Path(__file__).resolve().parents[1]


class InterfaceInstaller(unittest.TestCase):
    def test_active_version_is_always_accepted_by_the_interface_installer(self):
        src=(ROOT/'install-interface.py').read_text(encoding='utf-8')
        self.assertIn("from agent import __version__ as active_version",src)
        self.assertIn("if BASE.resolve().name not in supported|{active_version}:",src)
        # Les versions précédentes restent listées ; la version active ne dépend plus d'un ajout manuel.
        self.assertIn("'5.6.15'}",src);self.assertNotIn("'"+agent.__version__+"'",src.split('supported=')[1].split('}')[0])

    def test_installer_module_still_loads_without_root(self):
        spec=importlib.util.spec_from_file_location('interface_installer_5616',ROOT/'install-interface.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertTrue(callable(module.main));self.assertIn('/agent-courriel',module.apache_config('courriel.example.com'))


if __name__=='__main__':
    unittest.main()
