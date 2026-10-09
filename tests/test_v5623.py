"""5.6.23 : guide « poste Ubuntu en dix minutes » livré avec ses captures et lié depuis le README."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Guide(unittest.TestCase):
    def test_guide_is_complete_and_its_captures_exist(self):
        guide = (ROOT / 'docs' / 'GUIDE-POSTE-10-MINUTES.md').read_text(encoding='utf-8')
        for needle in ('sudo apt install ./axiorhub-pilote-poste_', 'ollama pull qwen3:8b', 'systemctl --user enable --now axiorhub-pilote',
                       'axiorhub-pilote --print-password', '~/.local/share/axiorhub-pilote', 'Rien n’est envoyé ni signé', 'données fictives'):
            self.assertIn(needle, guide, needle)
        images = re.findall(r'!\[[^\]]*\]\((captures/[^)]+)\)', guide)
        self.assertEqual(len(images), 5)
        for image in images:
            path = ROOT / 'docs' / image
            self.assertTrue(path.is_file(), image)
            self.assertTrue(path.read_bytes().startswith(b'\x89PNG'), image)
            self.assertLess(path.stat().st_size, 600_000, image)
        listing = set(json.loads((ROOT / 'RELEASE-FILES.json').read_text(encoding='utf-8')))
        self.assertIn('docs/GUIDE-POSTE-10-MINUTES.md', listing)
        for image in images:
            self.assertIn('docs/' + image, listing)
        self.assertIn('docs/GUIDE-POSTE-10-MINUTES.md', (ROOT / 'README.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
