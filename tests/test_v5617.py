"""5.6.17 : Kokoro (fichier complet, en-tête de flux réparé), bouton robot sur « Aujourd'hui », routines du cabinet en tête de page."""
import io
from pathlib import Path
import unittest
import wave

from agent.common import Stop
import test_v5614 as t14

ROOT=Path(__file__).resolve().parents[1]


class KokoroWave(unittest.TestCase):
    def _wav(self,frames=240):
        buf=io.BytesIO()
        with wave.open(buf,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x01\x00'*frames)
        return buf.getvalue()

    def test_streamed_wav_with_placeholder_sizes_is_repaired_then_checked(self):
        from agent import voice568
        raw=self._wav();data=raw.index(b'data')
        streamed=b'RIFF'+(0).to_bytes(4,'little')+raw[8:data+4]+(0xFFFFFFFF).to_bytes(4,'little')+raw[data+8:]
        with self.assertRaises(Exception):voice568._parse_wav(streamed)
        self.assertEqual(voice568._wav(streamed),raw);self.assertEqual(voice568._wav(raw),raw)
        zero=b'RIFF'+(0).to_bytes(4,'little')+raw[8:data+4]+(0).to_bytes(4,'little')+raw[data+8:]
        self.assertEqual(voice568._wav(zero),raw)

    def test_truncated_or_fake_audio_is_still_refused(self):
        from agent import voice568
        raw=self._wav()
        with self.assertRaises(Stop):voice568._wav(b'RIFFjunk')
        with self.assertRaises(Stop):voice568._wav(raw[:raw.index(b'data')+8])     # en-tête sans échantillons
        with self.assertRaises(Stop):voice568._wav(b'RIFF'+(0).to_bytes(4,'little')+b'WAVEfmt '+(16).to_bytes(4,'little')+b'\x00'*16)

    def test_kokoro_request_asks_for_a_complete_file(self):
        src=(ROOT/'agent'/'voice568.py').read_text(encoding='utf-8')
        self.assertIn("'response_format':'wav','speed':p['speech_rate'],'stream':False",src)


class TodayLayout(t14.Base):
    def test_routines_frame_sits_above_the_pilot_and_the_robot_button_leads_to_it(self):
        body=self.request('/aujourdhui')['body']
        self.assertLess(body.index('id="c5614-routines"'),body.index('id="c530-routines"'))      # barre compacte, puis le cadre complet
        self.assertLess(body.index('id="c530-routines"'),body.index('id="c530-pilot-slot"'))     # cadre des routines avant le Pilote
        self.assertLess(body.index('id="c530-style"'),body.index('id="c530-pilot-slot"'))
        self.assertLess(body.index('id="c530-pilot-slot"'),body.index('id="c530-review"'))
        self.assertEqual(body.count('id="c530-routines"'),1)
        js=(ROOT/'agent'/'static'/'v567.js').read_text(encoding='utf-8')
        self.assertIn("if (embedded) { dock.scrollIntoView",js);self.assertNotIn("launcher.hidden = true",js)
        css=(ROOT/'agent'/'static'/'v5614.css').read_text(encoding='utf-8')
        self.assertIn('#ws-ai-launcher[hidden]{display:none}',css)


if __name__=='__main__':
    unittest.main()
