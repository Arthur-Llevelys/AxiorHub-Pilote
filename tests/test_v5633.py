"""5.6.33 : correctif de mise à jour — la 5.6.32 refusait de mettre à jour une 5.6.31 (« Cette mise à jour directe exige une version
précédente comprise entre 3.5.0 et 5.6.32 ») : la 5.6.31 avait disparu de la liste des versions acceptées par upgrade.py.

Ces contrôles s'exécutent sur tous les systèmes (le test de mise à jour sur système de fichiers n'est lancé qu'en root sous Linux)."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _chain():
    src = (ROOT / 'upgrade.py').read_text(encoding='utf-8')
    line = next(l for l in src.splitlines() if l.startswith('SUPPORTED_PREVIOUS = '))
    version = re.search(r"^VERSION = '([^']+)'", src, re.M).group(1)
    return ast.literal_eval(line.split('=', 1)[1].strip()), version


def _key(v):
    return tuple(int(x) for x in v.split('.'))


class UpgradeChain(unittest.TestCase):
    def test_previous_release_is_accepted_and_current_is_not(self):
        chain, version = _chain()
        from agent import __version__
        self.assertEqual(version, __version__)
        self.assertNotIn(version, chain)
        self.assertEqual(len(chain), len(set(chain)))
        self.assertEqual(list(chain), sorted(chain, key=_key))                       # ordre croissant (message « entre … et … »)
        major, minor, patch = _key(version)
        self.assertIn('%d.%d.%d' % (major, minor, patch - 1), chain)                 # la version immédiatement précédente
        self.assertIn('5.6.31', chain)                                               # celle qui manquait en 5.6.32
        self.assertIn('5.6.6', chain)                                                # archive cumulative installable depuis 5.6.6

    def test_every_published_5_6_release_is_accepted(self):
        chain, version = _chain()
        last = _key(version)[2]
        missing = ['5.6.%d' % n for n in range(0, last) if '5.6.%d' % n not in chain]
        self.assertEqual(missing, [])

    def test_web_installer_accepts_the_same_previous_release(self):
        chain, version = _chain()
        src = (ROOT / 'install-interface.py').read_text(encoding='utf-8')
        supported = ast.literal_eval(re.search(r"supported=(\{[^}]*\})", src).group(1))
        self.assertIn(chain[-1], supported)


if __name__ == '__main__':
    unittest.main()
