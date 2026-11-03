"""5.6.93 : un document Word composé deux fois avec le même texte donne les mêmes octets, quelle que soit l'heure."""
import unittest
from unittest.mock import patch

from agent.word_legal import _simple_docx


class Stabilite(unittest.TestCase):
    def test_meme_texte_memes_octets_a_deux_moments(self):
        """Avant : l'heure du moment entrait dans l'archive ; republier un acte identique créait une nouvelle version."""
        with patch('time.time', lambda: 1_800_000_000.0):
            premier = _simple_docx('Conclusions DURAND', ['Par ces motifs'])
        with patch('time.time', lambda: 1_900_000_000.0):
            second = _simple_docx('Conclusions DURAND', ['Par ces motifs'])
        self.assertEqual(premier, second)

    def test_un_texte_different_change_les_octets(self):
        self.assertNotEqual(_simple_docx('Conclusions DURAND', ['A']), _simple_docx('Conclusions DURAND', ['B']))


if __name__ == '__main__':
    unittest.main()
