"""5.6.19 : Pilote simplifié (Envoyer, plus de chips ni de dictée en double, Effacer), fenêtre flottante, briefing lu depuis le briefing du matin."""
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import assistant567, ui_helpers, voice568
import test_v5614 as t14

ROOT = Path(__file__).resolve().parents[1]


class Dock(unittest.TestCase):
    def test_dock_has_one_send_button_no_chips_no_duplicate_dictation_and_a_clear_button(self):
        html = ui_helpers.assistant_dock('/agent-courriel', 'csrf', [], '/aujourdhui', True)
        self.assertNotIn('data-ai-chip', html); self.assertNotIn('ws-ai-chips', html)
        self.assertNotIn('id="ws-ai-dictate"', html)                       # la dictée est celle sous l'instruction (v310)
        self.assertEqual(html.count('id="ws-ai-send"'), 1); self.assertIn('Envoyer l’instruction', html)
        self.assertLess(html.index('id="ws-ai-send"'), html.index('Options de la mission'))   # envoi visible avant les options
        self.assertIn('id="ws-ai-clear"', html); self.assertIn('Effacer la conversation', html)
        self.assertIn('id="ws-ai-talk"', html); self.assertIn('Écouter le briefing', html)
        self.assertNotIn('Démarrer la mission', html); self.assertNotIn('Confier une autre mission', html)
        js = (ROOT / 'agent' / 'static' / 'v567.js').read_text(encoding='utf-8')
        self.assertNotIn('Confier une autre mission', js); self.assertNotIn('Démarrer une nouvelle mission', js)
        self.assertIn("#ws-ai-clear')?.addEventListener", js); self.assertIn("#ws-ai-dictate')?.addEventListener", js)
        self.assertIn("axiorhub567-wide:", js)                                # fenêtre large mémorisée
        css = (ROOT / 'agent' / 'static' / 'v5614.css').read_text(encoding='utf-8')
        self.assertIn('.ws-ai-send{', css); self.assertIn('#ws-ai-clear{', css)


class Today(t14.Base):
    def test_today_no_longer_embeds_the_pilot_and_keeps_the_robot_launcher(self):
        body = self.request('/aujourdhui')['body']
        self.assertNotIn('id="c530-pilot-slot"', body)
        self.assertIn('data-legacy-composer hidden', body)
        self.assertIn('id="ws-ai-launcher"', body); self.assertIn('id="ws-ai-dock"', body)
        self.assertLess(body.index('id="c530-routines"'), body.index('id="c530-review"'))


class SpokenBriefing(t14.Base):
    MARKDOWN = ("# BRIEFING DU MATIN – 08/10/2026\n\n**Aujourd’hui**\n\n- **09:00 – 10:00** : Audience TJ Lyon – DOSSIER A / SOCIETE B.\n"
                "- 10:00 – 12:00 : Réunion [client](https://exemple.test).\n\n*(Rubriques vides omises)*\n")

    def test_markdown_briefing_becomes_readable_speech(self):
        text = voice568.spoken_briefing_text(self.MARKDOWN)
        self.assertIn('BRIEFING DU MATIN', text); self.assertNotIn('#', text); self.assertNotIn('**', text)
        self.assertIn('9 heures à 10 heures : Audience TJ Lyon', text); self.assertIn('10 heures à 12 heures : Réunion client.', text)
        self.assertNotIn('https://', text); self.assertNotIn('Rubriques vides', text)
        self.assertTrue(all(line.endswith(('.', ':', '!', '?')) for line in text.splitlines()))

    def test_briefing_reads_the_morning_routine_unless_discreet(self):
        assistant567.save_profile(self.desk, {'discreet': False}, 'cabinet')
        with patch('agent.routines520.latest', return_value={'text': self.MARKDOWN, 'notes': []}):
            out = voice568.briefing(self.desk, 'cabinet')
        self.assertEqual(out['source'], 'routine_briefing'); self.assertIn('9 heures à 10 heures : Audience TJ Lyon', out['text'])
        assistant567.save_profile(self.desk, {'discreet': True}, 'cabinet')
        with patch('agent.routines520.latest', return_value={'text': self.MARKDOWN, 'notes': []}):
            out = voice568.briefing(self.desk, 'cabinet')
        self.assertEqual(out['source'], 'instantane_anonymise'); self.assertNotIn('SOCIETE B', out['text'])
        assistant567.save_profile(self.desk, {'discreet': False}, 'cabinet')
        with patch('agent.routines520.latest', return_value=None):
            self.assertEqual(voice568.briefing(self.desk, 'cabinet')['source'], 'instantane')

    def test_discreet_mode_is_opt_in(self):
        self.assertFalse(assistant567.profile(self.desk, 'nouveau-compte')['discreet'])


if __name__ == '__main__':
    unittest.main()
