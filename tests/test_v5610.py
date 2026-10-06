"""5.6.10 : accueil téléphonique — annonce IA/Twilio complète et modifiable, touche 0 = accueil par touches, traçabilité Twilio."""
from pathlib import Path
import unittest

from agent.common import Stop
import test_v569 as t569

ROOT = Path(__file__).resolve().parents[1]


class Notice(t569.ReceptionBase):
    def test_default_notice_states_ai_twilio_no_recording_no_advice_and_key_zero(self):
        from agent import reception567
        first = self.phone({'CallSid': 'CA5000000005', 'From': '0612345678'})
        body = first['body']
        for fragment in ('intelligence artificielle', 'Twilio', 'aucun enregistrement audio', 'Aucun conseil juridique', 'tapez 0', 'horaires'):
            self.assertIn(fragment, body)
        self.assertIn('input="speech dtmf"', body)
        self.assertEqual(reception567.notice(self.desk.c['reception']), reception567.DEFAULT_NOTICE)

    def test_notice_is_configurable_as_lines_under_the_lawyer_s_responsibility(self):
        from agent import config567, reception567
        self.desk.c['reception']['speech_notice'] = ['Cabinet Exemple : accueil automatisé avec IA.', 'Transcription par Twilio ; rien n’est enregistré.', 'Tapez 0 pour le clavier.']
        self.assertEqual(reception567.notice(self.desk.c['reception']),
                         'Cabinet Exemple : accueil automatisé avec IA. Transcription par Twilio ; rien n’est enregistré. Tapez 0 pour le clavier.')
        body = self.phone({'CallSid': 'CA6000000006', 'From': '0612345678'})['body']
        self.assertIn('Cabinet Exemple : accueil automatisé avec IA.', body);self.assertNotIn(reception567.DEFAULT_NOTICE[:40], body)
        self.assertIn('reception.speech_notice', [f[0] for f in config567.CATALOG] if hasattr(config567, 'CATALOG') else
                      (ROOT / 'agent' / 'config567.py').read_text(encoding='utf-8'))
        self.assertEqual(reception567.notice({'speech_notice': ['', '  ']}), reception567.DEFAULT_NOTICE)

    def test_key_zero_switches_to_keypad_without_speech_recognition(self):
        second = self.phone({'CallSid': 'CA7000000007', 'From': '0612345678', 'Digits': '0'}, 'step=1')
        self.assertIn('input="dtmf"', second['body']);self.assertNotIn('speech', second['body'])
        self.assertIn('action="https://agent.example.test/reception567/twilio"', second['body'])
        third = self.phone({'CallSid': 'CA7000000007', 'From': '0612345678', 'Digits': '1'})     # suite du clavier, sans paramètre
        self.assertIn('<Hangup/>', third['body'])
        row = self.desk.db.execute("SELECT title,text FROM reception_v567 r JOIN tasks t ON t.id=r.task_id").fetchone()
        self.assertIn('Rappel administratif', row['title']);self.assertNotIn('Twilio', row['text'])   # aucune transcription utilisée

    def test_speech_task_keeps_twilio_traceability(self):
        self.phone({'CallSid': 'CA8000000008', 'From': '0612345678', 'SpeechResult': 'Madame Martin, un rappel'}, 'step=2&intent=callback')
        text = self.desk.db.execute('SELECT text FROM reception_v567').fetchone()['text']
        self.assertIn('accueil automatisé avec IA', text);self.assertIn('transcription Twilio, non vérifiée', text)


if __name__ == '__main__':
    unittest.main()
