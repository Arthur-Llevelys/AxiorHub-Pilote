"""5.6.110 (lot D, partie D1) : dictée sur chaque poste (whisper.cpp lancé une fois par dictée).

Faux moteur (succès, modèle illisible, composant Microsoft manquant, délai dépassé, sortie absente), noms relatifs en ASCII et ``cwd``,
dossier de travail supprimé dans tous les cas, WAV refusés (en-tête, fréquence, stéréo, trop long), silence sans lancer le moteur,
phrases inventées filtrées, verrou, téléchargement par un faux transport (taille fausse, empreinte fausse, redirection refusée, reprise,
aucun reste), droits des routes, activation et drapeau audio, chemin de la passerelle inchangé quand le poste n'est pas actif, messages,
carte de l'écran, traductions, guide. Aucun réseau, aucun vrai moteur, aucun micro ; données fictives (DURAND, MARTIN, DOS-001).
"""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from agent import dictee56110 as D, portable   # noqa: E402
from agent import web568, web_rules568   # noqa: E402,F401 — liés à web567.actor par son nom : chargés avant tout remplacement de cette fonction
from agent.common import Stop   # noqa: E402

ROOT = TESTS.parent
CONTENU = b'modele fictif de dictee ' * 400


def meta(contenu=CONTENU, nom='Rapide', fichier='ggml-base-q5_1.bin', conseil='pour un poste modeste'):
    return {'fichier': fichier, 'octets': len(contenu), 'sha256': hashlib.sha256(contenu).hexdigest(), 'nom': nom,
            'conseil': conseil, 'mesure': '2,4 s'}


def wav_de(pcm, taux=16000, canaux=1, bits=16, fmt=1):
    octets_s = taux * canaux * bits // 8
    return (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, fmt, canaux, taux, octets_s, canaux * bits // 8, bits)
            + b'data' + struct.pack('<I', len(pcm)) + pcm)


def parole(secondes=1.0, amplitude=8000):
    """Son fabriqué (sinusoïde de 440 Hz) : assez d'énergie pour ne pas être un silence."""
    n = int(16000 * secondes)
    return b''.join(struct.pack('<h', int(amplitude * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(n))


class FauxMoteur:
    """Remplace ``D._lancer`` : contrôle les noms relatifs et le ``cwd``, écrit ``<sortie>.txt`` comme whisper-cli (CRLF)."""

    def __init__(self, code=0, texte='Maître DURAND, je vous confirme la réception.\r\nL’audience du dossier DOS-001 est fixée.\r\n', sortie=True, panne=None):
        self.code, self.texte, self.sortie, self.panne, self.appels = code, texte, sortie, panne, []

    def __call__(self, args, cwd, delai):
        a, cwd = list(args), Path(cwd)
        self.appels.append({'args': a, 'cwd': cwd, 'delai': delai})
        m, f, of = a[a.index('-m') + 1], a[a.index('-f') + 1], a[a.index('-of') + 1]
        self.modele_lu = (cwd / m).is_file()
        self.wav = (cwd / f).read_bytes()
        self.travail = (cwd / f).parent
        if self.panne is not None:
            raise self.panne
        if self.sortie and self.code == 0:
            (cwd / (of + '.txt')).write_bytes(self.texte.encode('utf-8'))
        return self.code


class _Env(unittest.TestCase):
    """Moteur simulé (dossier désigné par AXIORHUB_DICTEE), dossier d'état accentué, modèles minuscules, états du module remis en place."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='axh-lotd-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.moteur = self.tmp / 'Moteur été' / 'dictee'
        self.moteur.mkdir(parents=True)
        for nom in ('whisper-cli.exe', 'whisper-cli'):
            (self.moteur / nom).write_bytes(b'faux moteur')
        self.etat = self.tmp / 'Jérôme' / 'état'
        self.etat.mkdir(parents=True)
        with sqlite3.connect(self.etat / 'desk.sqlite3') as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        self.cfg = {'state_dir': str(self.etat), 'audio': {}}
        self.racine = self.etat                                          # dossier d'état où sont rangés les modèles
        self.modeles = {'rapide': meta(), 'equilibre': meta(CONTENU + b'e', 'Équilibré', 'ggml-small-q5_1.bin', 'conseillé'),
                        'precis': meta(CONTENU + b'pp', 'Précis', 'ggml-large-v3-turbo-q5_0.bin', 'lent sans processeur puissant')}
        for p in (patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.moteur)}), patch.dict(D.MODELES, self.modeles),
                  patch.dict(D._TELE, {'en_cours': False, 'modele': '', 'recus': 0, 'attendus': 0, 'erreur': '', 'state_dir': None, 'fil': None}),
                  patch.dict(D._DERNIERE, {'erreur': ''})):
            p.start()
            self.addCleanup(p.stop)

    def regler(self, **valeur):
        v = dict({'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1}, **valeur)
        with sqlite3.connect(self.etat / 'desk.sqlite3') as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (D.KEY, json.dumps(v)))
        db.close()

    def installer(self, ident='rapide', contenu=None):
        p = D.chemin_modele(str(self.racine), ident)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contenu if contenu is not None else (CONTENU + {'rapide': b'', 'equilibre': b'e', 'precis': b'pp'}[ident]))
        return p

    def restes(self):
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob('*') if p.name.endswith('.part') or p.parent.name == 'travail')


# ================================================================================================ moteur
class Moteur(_Env):
    def test_D01_dossier_par_variable_et_jamais_le_path(self):
        self.assertEqual(D.dossier_moteur(), self.moteur)
        self.assertTrue(D.executable().startswith(str(self.moteur)))
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'absent')}), patch('shutil.which', lambda *a, **k: '/usr/bin/whisper-cli'):
            self.assertIsNone(D.dossier_moteur())
            self.assertIsNone(D.executable())
            self.assertEqual(D.raison_absence({}), 'variable')

    def test_D01_windows_empaquete_dossier_dictee_a_cote_de_l_executable(self):
        app = self.tmp / 'Application été'
        (app / 'dictee').mkdir(parents=True)
        (app / 'dictee' / 'whisper-cli.exe').write_bytes(b'x')
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', True), patch.object(portable, 'frozen', lambda: True), \
                patch.object(sys, 'executable', str(app / 'AxiorHub.exe')):
            self.assertEqual(D.executable(), str((app / 'dictee' / 'whisper-cli.exe').resolve()))
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', True), patch.object(portable, 'frozen', lambda: False):
            self.assertIsNone(D.dossier_moteur())
            self.assertEqual(D.raison_absence({}), 'hors_paquet')
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', True), patch.object(portable, 'frozen', lambda: True), \
                patch.object(sys, 'executable', str(self.tmp / 'ailleurs' / 'AxiorHub.exe')):
            self.assertIsNone(D.executable())
            self.assertEqual(D.raison_absence({}), 'paquet_sans_moteur')

    def test_D01_linux_chemin_fixe(self):
        lib = self.tmp / 'usr-lib'
        lib.mkdir()
        (lib / 'whisper-cli').write_bytes(b'x')
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', False), patch.object(D, 'LINUX', str(lib)):
            self.assertEqual(D.executable(), str(lib / 'whisper-cli'))
        self.assertEqual(D.LINUX, '/usr/lib/axiorhub-pilote/dictee')
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', False), patch.object(D, 'LINUX', str(self.tmp / 'rien')):
            self.assertIsNone(D.executable())
            self.assertEqual(D.raison_absence({'_path': '/etc/axiorhub-mail-agent/config.json'}), 'serveur')
            with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': str(self.tmp)}):
                self.assertEqual(D.raison_absence({}), 'paquet_sans_moteur')

    def test_D01_fils_entre_2_et_8(self):
        for coeurs, attendu in ((1, 2), (4, 2), (8, 4), (16, 8), (64, 8), (None, 2)):
            with patch('os.cpu_count', lambda c=coeurs: c):
                self.assertEqual(D.fils(), attendu)


# ================================================================================================ transcription
class Transcription(_Env):
    def setUp(self):
        super().setUp()
        self.regler()
        self.installer()

    def test_D01_succes_noms_relatifs_ascii_cwd_et_travail_supprime(self):
        faux = FauxMoteur()
        with patch.object(D, '_lancer', faux):
            texte = D.transcrire(self.cfg, wav_de(parole()))
        self.assertEqual(texte, 'Maître DURAND, je vous confirme la réception. L’audience du dossier DOS-001 est fixée.')
        appel = faux.appels[0]
        a = appel['args']
        self.assertEqual(a[0], D.executable())
        self.assertEqual(appel['cwd'], self.etat / 'dictee56110')
        for x in a[1:]:
            self.assertTrue(x.isascii(), x)                              # aucun caractère accentué passé au moteur (profil « Jérôme »)
            self.assertFalse(os.path.isabs(x), x)
        self.assertEqual(a[a.index('-m') + 1], 'modeles/ggml-base-q5_1.bin')
        self.assertRegex(a[a.index('-f') + 1], r'^travail/[0-9a-f]{16}/d\.wav$')
        self.assertEqual(a[a.index('-of') + 1], a[a.index('-f') + 1][:-4])
        self.assertEqual(a[a.index('-l') + 1], 'fr')
        for drapeau in ('-nt', '-np', '-otxt'):
            self.assertIn(drapeau, a)
        self.assertTrue(2 <= int(a[a.index('-t') + 1]) <= 8)
        self.assertEqual(appel['delai'], 180)
        self.assertTrue(faux.modele_lu)
        self.assertEqual(faux.wav[:4], b'RIFF')
        self.assertFalse(faux.travail.exists())                         # dossier de travail supprimé après la dictée
        self.assertEqual(self.restes(), [])

    def test_D01_langue_et_delai_reglables(self):
        self.regler(langue='auto')
        faux = FauxMoteur()
        with patch.object(D, '_lancer', faux):
            D.transcrire(dict(self.cfg, audio={'dictation_timeout_seconds': 45}), wav_de(parole()))
        a = faux.appels[0]['args']
        self.assertEqual(a[a.index('-l') + 1], 'auto')
        self.assertEqual(faux.appels[0]['delai'], 45)

    def test_D01_echecs_du_moteur_lisibles_et_travail_supprime(self):
        cas = [(FauxMoteur(code=3), 'modele_illisible'), (FauxMoteur(code=3221225781), 'composant_microsoft_absent'),
               (FauxMoteur(code=-1073741515), 'composant_microsoft_absent'), (FauxMoteur(code=3221226505), 'moteur_en_echec'),
               (FauxMoteur(code=1), 'moteur_en_echec'), (FauxMoteur(sortie=False), 'dictee_poste_sortie_absente'),
               (FauxMoteur(panne=Stop('dictee_poste_delai_depasse')), 'dictee_poste_delai_depasse')]
        for faux, code in cas:
            with self.subTest(code=code, rc=faux.code), patch.object(D, '_lancer', faux):
                with self.assertRaises(Stop) as ex:
                    D.transcrire(self.cfg, wav_de(parole()))
                self.assertEqual(str(ex.exception), code)
                self.assertFalse(faux.travail.exists())
                self.assertEqual(D._DERNIERE['erreur'], code)
                self.assertFalse(D._VERROU.locked())                    # verrou rendu même en cas d'erreur
        self.assertEqual(self.restes(), [])
        with patch.object(portable, 'WINDOWS', False):
            self.assertEqual(D._code_erreur(127), 'bibliotheque_systeme_absente')
        with patch.object(portable, 'WINDOWS', True):
            self.assertEqual(D._code_erreur(127), 'moteur_en_echec')
        with patch.object(D, '_lancer', FauxMoteur()):
            D.transcrire(self.cfg, wav_de(parole()))
        self.assertEqual(D._DERNIERE['erreur'], '')                      # une réussite efface la dernière erreur

    def test_D01_linux_sans_libgomp1_message_lisible(self):
        with patch.object(portable, 'WINDOWS', False), patch.object(D, '_lancer', FauxMoteur(code=127)):
            with self.assertRaisesRegex(Stop, '^bibliotheque_systeme_absente$'):
                D.transcrire(self.cfg, wav_de(parole()))
        self.assertIn('sudo apt-get install -y libgomp1', D.MESSAGES['bibliotheque_systeme_absente'])

    def test_D01_modele_depose_a_la_main_empreinte_controlee_une_fois(self):
        calculs = []
        vrai = D._empreinte_fichier

        def compte(p):
            calculs.append(p)
            return vrai(p)
        with patch.object(D, '_empreinte_fichier', compte), patch.object(D, '_lancer', FauxMoteur()):
            D.transcrire(self.cfg, wav_de(parole()))
            D.transcrire(self.cfg, wav_de(parole()))
            D.verifier(self.cfg)
        self.assertEqual(len(calculs), 1)                                 # calculé une fois, gardé avec taille et date du fichier
        self.assertTrue((self.etat / 'dictee56110' / 'controles.json').is_file())
        faux = CONTENU[:-1] + b'Z'                                        # bonne taille, empreinte fausse
        p = self.installer('rapide', faux)
        t = time.time() + 5
        os.utime(p, (t, t))
        moteur = FauxMoteur()
        with patch.object(D, '_empreinte_fichier', compte), patch.object(D, '_lancer', moteur):
            for _ in range(2):
                with self.assertRaisesRegex(Stop, '^modele_empreinte_incorrecte$'):
                    D.transcrire(self.cfg, wav_de(parole()))
        self.assertEqual(len(calculs), 2)                                 # fichier changé : un seul nouveau calcul
        self.assertEqual(moteur.appels, [])                               # moteur jamais lancé sur un fichier refusé
        self.assertFalse(D.active(self.cfg))
        self.assertTrue(D.etat(self.cfg)['modeles'][0]['empreinte_refusee'])
        self.assertIn('empreinte', D.MESSAGES['modele_empreinte_incorrecte'])
        D.retirer(str(self.etat), 'rapide')
        self.assertNotIn('ggml-base-q5_1.bin', json.loads((self.etat / 'dictee56110' / 'controles.json').read_text(encoding='utf-8')))

    def test_D01_wav_refuses_sans_lancer_le_moteur(self):
        pcm = parole(0.2)
        cas = [(b'OggS' + bytes(60), 'dictee_wav_invalide'), (b'RIFF\0\0\0\0WAVE', 'dictee_wav_invalide'),
               (wav_de(pcm)[:30], 'dictee_wav_invalide'), (wav_de(pcm, taux=44100), 'dictee_wav_format_refuse'),
               (wav_de(pcm + pcm, canaux=2), 'dictee_wav_format_refuse'), (wav_de(pcm, bits=8), 'dictee_wav_format_refuse'),
               (wav_de(pcm, fmt=3), 'dictee_wav_format_refuse'), (wav_de(bytes(2 * (600 * 16000 + 1))), 'dictee_trop_longue')]
        faux = FauxMoteur()
        for raw, code in cas:
            with self.subTest(code=code, debut=raw[:12]), patch.object(D, '_lancer', faux):
                with self.assertRaisesRegex(Stop, '^%s$' % code):
                    D.transcrire(self.cfg, raw)
        self.assertEqual(faux.appels, [])
        self.assertEqual(len(D.lire_wav(wav_de(bytes(2 * 600 * 16000)))), 2 * 600 * 16000)   # 10 minutes tout juste : accepté
        self.assertLessEqual(len(wav_de(bytes(2 * 600 * 16000))), D.MAX_OCTETS)

    def test_D01_silence_texte_vide_sans_moteur(self):
        faux = FauxMoteur()
        bruit = b''.join(struct.pack('<h', (i * 37) % 120 - 60) for i in range(16000))   # souffle faible
        with patch.object(D, '_lancer', faux):
            for raw in (wav_de(b''), wav_de(bytes(32000)), wav_de(bruit)):
                self.assertEqual(D.transcrire(self.cfg, raw), '')
        self.assertEqual(faux.appels, [])
        court = bytes(2 * 16000) + parole(0.1) + bytes(2 * 16000)       # une syllabe entre deux silences : pas un silence
        self.assertFalse(D.silence(court))
        self.assertNotRegex((ROOT / 'agent' / 'dictee56110.py').read_text(encoding='utf-8'), r'import audioop|audioop[.]')

    def test_D01_phrases_inventees_filtrees_seulement_seules(self):
        for invente in ('Sous-titres réalisés par la communauté d\'Amara.org', ' Sous-titres réalisés par la communauté d’Amara.org\r\n',
                        '[BLANK_AUDIO]', '[BLANK_AUDIO]\r\n[BLANK_AUDIO]', 'Thanks for watching!', '(musique)', '...'):
            with self.subTest(invente=invente), patch.object(D, '_lancer', FauxMoteur(texte=invente)):
                self.assertEqual(D.transcrire(self.cfg, wav_de(parole())), '')
        vrai = 'Maître MARTIN cite la communauté d’Amara.org dans ses conclusions.'
        with patch.object(D, '_lancer', FauxMoteur(texte=vrai + '\r\n[BLANK_AUDIO]')):
            self.assertEqual(D.transcrire(self.cfg, wav_de(parole())), vrai)

    def test_D01_une_transcription_a_la_fois(self):
        faux = FauxMoteur()
        self.assertTrue(D._VERROU.acquire(blocking=False))
        try:
            with patch.object(D, '_lancer', faux), self.assertRaisesRegex(Stop, 'transcription_en_cours'):
                D.transcrire(self.cfg, wav_de(parole()))
        finally:
            D._VERROU.release()
        self.assertEqual(faux.appels, [])
        self.assertEqual(D.STATUTS['transcription_en_cours'], '409 Conflict')

    def test_D01_modele_ou_moteur_absent(self):
        D.chemin_modele(str(self.etat), 'rapide').write_bytes(CONTENU[:-1])   # taille fausse : modèle non installé
        with patch.object(D, '_lancer', FauxMoteur()), self.assertRaisesRegex(Stop, 'modele_non_installe'):
            D.verifier(self.cfg)
        self.installer()
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}), self.assertRaisesRegex(Stop, 'moteur_absent'):
            D.verifier(self.cfg)

    def test_D01_verifier_une_seconde_de_silence_sans_filtre_de_silence(self):
        faux = FauxMoteur(texte='Sous-titres réalisés par la communauté d’Amara.org\r\n')
        with patch.object(D, '_lancer', faux):
            out = D.verifier(self.cfg)
        self.assertTrue(out['ok'])
        self.assertTrue(out['texte_vide'])
        self.assertRegex(out['message'], r'^Moteur vérifié : réponse en \d+,\d s\.$')
        pcm = D.lire_wav(faux.wav)
        self.assertEqual(len(pcm), 32000)                                 # une seconde à 16 kHz, 16 bits
        self.assertEqual(set(pcm), {0})

    def test_D06_dicter_wav_seulement(self):
        with patch.object(D, '_lancer', FauxMoteur()):
            out = D.dicter(self.cfg, wav_de(parole()), 'audio/wav')
        self.assertEqual(set(out), {'text', 'submitted'})
        self.assertFalse(out['submitted'])
        for ct in ('audio/webm', 'audio/ogg', 'audio/mp4', ''):
            with self.assertRaisesRegex(Stop, 'dictee_poste_format_non_wav'):
                D.dicter(self.cfg, b'webm', ct)
        with self.assertRaisesRegex(Stop, 'dictee_trop_volumineuse'):
            D.dicter(self.cfg, b'', 'audio/wav')
        self.assertIn('WAV', D.MESSAGES['dictee_poste_format_non_wav'])

    def test_D01_lancer_tue_le_processus_a_l_echeance(self):
        """Pas de vrai moteur : un Python qui attend, arrêté par portable.kill_tree au bout d'une seconde."""
        pid = self.tmp / 'pid.txt'
        code = 'import os,time;open(%r,"w").write(str(os.getpid()));time.sleep(60)' % str(pid)
        debut = time.monotonic()
        with self.assertRaisesRegex(Stop, 'dictee_poste_delai_depasse'):
            D._lancer([sys.executable, '-c', code], self.tmp, 2)
        self.assertLess(time.monotonic() - debut, 30)
        if pid.is_file():
            for _ in range(50):
                if not portable.pid_alive(int(pid.read_text())):
                    break
                time.sleep(0.1)
            self.assertFalse(portable.pid_alive(int(pid.read_text())))
        self.assertEqual(D._lancer([sys.executable, '-c', 'raise SystemExit(3)'], self.tmp, 30), 3)


# ================================================================================================ activation, drapeau, passerelle
class Activation(_Env):
    def test_D01_active_seulement_si_reglage_moteur_et_modele(self):
        self.assertFalse(D.active(self.cfg))                              # aucun réglage
        self.regler()
        self.assertFalse(D.active(self.cfg))                              # modèle absent
        self.installer()
        self.assertTrue(D.active(self.cfg))
        self.regler(moteur='')
        self.assertFalse(D.active(self.cfg))
        self.regler(modele='equilibre')
        self.assertFalse(D.active(self.cfg))                              # modèle choisi absent
        self.regler()
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}):
            self.assertFalse(D.active(self.cfg))
        self.assertFalse(D.active({}))
        self.assertFalse(D.active(None))
        self.assertEqual(D.reglages_de(str(self.tmp / 'nulle-part')), D._defaut())

    def test_D01_reglage_lu_sans_ecrire(self):
        self.regler(modele='inconnu', langue='xx', version=-3)
        r = D.reglages_de(str(self.etat))
        self.assertEqual((r['modele'], r['langue'], r['version']), ('equilibre', 'fr', 0))
        avant = (self.etat / 'desk.sqlite3').stat().st_mtime_ns
        D.reglages_de(str(self.etat))
        self.assertEqual((self.etat / 'desk.sqlite3').stat().st_mtime_ns, avant)

    def test_D01_drapeau_audio(self):
        self.assertFalse(D.audio_actif(self.cfg))
        self.assertTrue(D.audio_actif(dict(self.cfg, audio={'enabled': True})))
        self.regler()
        self.installer()
        self.assertTrue(D.audio_actif(self.cfg))                          # vrai même si audio.enabled est faux
        from agent import shell501
        with patch.object(shell501, 'context', lambda: self.cfg):
            self.assertTrue(D.active_contexte())
        with patch.object(shell501, 'context', lambda: None):
            self.assertFalse(D.active_contexte())
        src = (ROOT / 'agent' / 'shell501.py').read_text(encoding='utf-8')
        self.assertIn('audio = _audio56110(cfg)', src)
        self.assertIn('audio_actif(c)', (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8'))

    def test_D01_enhance_balise_et_script_avant_les_autres(self):
        from agent import shell501, ui_helpers
        page = '<!doctype html><html><head><title>Essai</title></head><body><main></main></body></html>'
        with patch.object(shell501, 'context', lambda: self.cfg):
            inactif = ui_helpers.enhance(page, '/p', 'jeton', False)
            self.regler()
            self.installer()
            actif = ui_helpers.enhance(page, '/p', 'jeton', False)
        self.assertIn('<meta name="axiorhub-audio" content="disabled">', inactif)
        self.assertNotIn('v56110-dictee.js', inactif)
        self.assertIn('<meta name="axiorhub-audio" content="enabled">', actif)
        self.assertLess(actif.index('/static/v56110-dictee.js'), actif.index('/static/v5680.js'))
        self.assertLess(actif.index('/static/v56110-dictee.js'), actif.index('/static/v310.js'))

    def test_D07_chemin_de_la_passerelle_inchange_sans_dictee_du_poste(self):
        from agent import audio

        class Client:
            appels = []
            base = 'http://127.0.0.1:9011'

            def __init__(self, *a, **k):
                pass

            def request(self, *a, **k):
                Client.appels.append(a)
                return json.dumps({'text': 'Texte fictif de la passerelle.'}).encode()
        config = dict(self.cfg, audio={'enabled': True, 'bridge_token_file': 'jeton-fictif'})
        faux = FauxMoteur()
        for reglage in (None, {'moteur': ''}, {'moteur': 'poste'}):          # pas de réglage, désactivé, choisi mais modèle absent
            if reglage is not None:
                self.regler(**reglage)
            with self.subTest(reglage=reglage), patch('agent.audio.HTTP', Client), patch('agent.audio.read_secret', return_value='jeton-fictif'), \
                    patch.object(D, '_lancer', faux):
                self.assertEqual(audio.dictate(config, b'son', 'audio/webm'), {'text': 'Texte fictif de la passerelle.', 'submitted': False})
        self.assertEqual(len(Client.appels), 3)
        self.assertEqual(faux.appels, [])
        with self.assertRaisesRegex(Stop, 'dictee_locale_non_configuree'):
            audio.dictate(self.cfg, b'son', 'audio/webm')
        self.installer()                                                  # dictée du poste active : la passerelle n'est plus appelée
        with patch('agent.audio.HTTP', Client), patch.object(D, '_lancer', faux):
            out = audio.dictate(config, wav_de(parole()), 'audio/wav')
            with self.assertRaisesRegex(Stop, 'dictee_poste_format_non_wav'):
                audio.dictate(config, b'son', 'audio/webm')
        self.assertTrue(out['text'].startswith('Maître DURAND'))
        self.assertEqual(len(Client.appels), 3)
        self.assertEqual(len(faux.appels), 1)

    def test_D07_limite_de_la_route_dictation(self):
        src = (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8')
        self.assertIn("if not 0<length<=(_max56110 if _poste56110(cfg) else 8000000):raise Stop('dictee_trop_volumineuse')", src)
        self.assertEqual(D.MAX_OCTETS, 20_000_000)
        self.assertIn('max_request_body_size=20_100_000', src)            # le serveur accepte un corps de 20 Mo


# ================================================================================================ téléchargement
class FauxTransport:
    def __init__(self, contenu=CONTENU, panne=None):
        self.contenu, self.panne, self.urls = contenu, panne, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.panne:
            raise self.panne
        return io.BytesIO(self.contenu)


class Telechargement(_Env):
    def test_D02_bonne_empreinte_gardee_sans_reste(self):
        t, vus = FauxTransport(), []
        out = D.telecharger(str(self.etat), 'rapide', t, vus.append)
        with patch.object(D, '_empreinte_fichier', side_effect=AssertionError('second calcul')):
            D.controler(str(self.etat), 'rapide')                         # vérifiée au téléchargement : pas de second calcul
        self.assertEqual(t.urls, ['https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base-q5_1.bin'])
        self.assertEqual(out, {'octets': len(CONTENU), 'sha256': hashlib.sha256(CONTENU).hexdigest()})
        self.assertEqual(D.chemin_modele(str(self.etat), 'rapide').read_bytes(), CONTENU)
        self.assertEqual(vus[-1], len(CONTENU))
        self.assertTrue(D.modele_installe(str(self.etat), 'rapide'))
        self.assertEqual(self.restes(), [])

    def test_D02_taille_ou_empreinte_fausse_rien_garde(self):
        for nom, transport in (('altéré', FauxTransport(CONTENU[:-1] + b'X')), ('court', FauxTransport(CONTENU[:-10])),
                               ('long', FauxTransport(CONTENU + b'en plus')), ('panne', FauxTransport(panne=OSError('réseau'))),
                               ('redirection', FauxTransport(panne=Stop('redirection_refusee')))):
            with self.subTest(nom):
                with self.assertRaises(Stop) as ex:
                    D.telecharger(str(self.etat), 'rapide', transport)
                self.assertIn(str(ex.exception), ('empreinte_incorrecte', 'telechargement_impossible', 'redirection_refusee'))
                self.assertFalse(D.chemin_modele(str(self.etat), 'rapide').exists())
                self.assertEqual(self.restes(), [])
        with patch.dict(D.MODELES, {'rapide': dict(meta(), sha256='0' * 64)}), self.assertRaisesRegex(Stop, 'empreinte_incorrecte'):
            D.telecharger(str(self.etat), 'rapide', FauxTransport())
        self.assertEqual(self.restes(), [])

    def test_D02_espace_insuffisant_et_modele_inconnu_sans_appel(self):
        t = FauxTransport()
        Usage = type('Usage', (), {'free': 10})
        with patch('agent.dictee56110.shutil.disk_usage', lambda p: Usage()), self.assertRaisesRegex(Stop, 'espace_insuffisant'):
            D.telecharger(str(self.etat), 'rapide', t)
        with self.assertRaisesRegex(Stop, 'modele_inconnu'):
            D.telecharger(str(self.etat), 'enorme', t)
        self.assertEqual(t.urls, [])

    def test_D02_hotes_permis(self):
        for url in ('https://huggingface.co/ggerganov/whisper.cpp/resolve/x/f.bin', 'https://us.aws.cdn.hf.co/xet/f', 'https://cdn-lfs.huggingface.co/x',
                    'https://huggingface.co:443/x'):
            self.assertTrue(D.hote_permis(url), url)
        for url in ('http://huggingface.co/x', 'https://huggingface.co.example.test/x', 'https://exemple-hf.co/x', 'https://hf.co.example.test/x',
                    'https://' + 'zorval' + '@' + 'huggingface.co/x', 'https://huggingface.co:8443/x', 'file:///C:/x', 'ftp://huggingface.co/x'):
            self.assertFalse(D.hote_permis(url), url)
        with self.assertRaisesRegex(Stop, 'redirection_refusee'):
            D._ouvrir('https://example.test/f.bin', 5)                    # refusé avant toute connexion

    def test_D02_redirection_hors_liste_refusee(self):
        """Le gestionnaire de redirection de ``_ouvrir`` refuse un hôte hors liste (faux opener, aucune connexion)."""
        import urllib.request
        vu = {}

        def faux_build_opener(*gestionnaires):
            vu['g'] = gestionnaires[0]

            class Ouvreur:
                def open(self, req, timeout):
                    return io.BytesIO(b'')
            return Ouvreur()
        with patch.object(urllib.request, 'build_opener', faux_build_opener):
            D._ouvrir(D.URL % (D.REVISION, 'ggml-base-q5_1.bin'), 5)
        g = vu['g']()
        req = urllib.request.Request('https://huggingface.co/x')
        with self.assertRaisesRegex(Stop, 'redirection_refusee'):
            g.redirect_request(req, None, 302, 'Found', {}, 'https://example.test/f.bin')
        self.assertIsNotNone(g.redirect_request(req, None, 302, 'Found', {}, 'https://us.aws.cdn.hf.co/f.bin'))

    def test_D02_reprise_apres_redemarrage_restes_anciens_retires(self):
        modeles = self.etat / 'dictee56110' / 'modeles'
        travail = self.etat / 'dictee56110' / 'travail'
        modeles.mkdir(parents=True)
        (travail / 'ancien').mkdir(parents=True)
        (travail / 'recent').mkdir(parents=True)
        vieux, neuf = modeles / '.ggml-base-q5_1.bin.aaaa.part', modeles / '.ggml-base-q5_1.bin.bbbb.part'
        vieux.write_bytes(b'x')
        neuf.write_bytes(b'x')
        ancien = time.time() - 7200
        for p in (vieux, travail / 'ancien'):
            os.utime(p, (ancien, ancien))
        D.configurer(str(self.etat))
        self.assertFalse(vieux.exists())
        self.assertFalse((travail / 'ancien').exists())
        self.assertTrue(neuf.exists())                                     # en cours d'écriture par un autre processus : jamais touché
        self.assertTrue((travail / 'recent').exists())
        D.configurer(None)
        D.configurer(str(self.tmp / 'inexistant'))

    def test_D02_un_seul_telechargement_en_arriere_plan(self):
        t = FauxTransport()
        with patch.object(D, '_ouvrir', t), patch('agent.desk.Desk', side_effect=RuntimeError('pas de base dans cet essai')):
            fil = D.lancer_telechargement(self.cfg, 'rapide')
            fil.join(10)
        self.assertFalse(D._TELE['en_cours'])
        self.assertEqual(D._TELE['erreur'], '')
        self.assertTrue(D.modele_installe(str(self.etat), 'rapide'))
        self.assertFalse(D._VERROU_TELE.locked())
        self.assertTrue(D._VERROU_TELE.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(Stop, 'telechargement_en_cours'):
                D.lancer_telechargement(self.cfg, 'equilibre')
        finally:
            D._VERROU_TELE.release()
        with patch.object(D, '_ouvrir', FauxTransport(b'faux')), patch('agent.desk.Desk', side_effect=RuntimeError('pas de base')):
            D.lancer_telechargement(self.cfg, 'equilibre').join(10)
        s = D.etat(self.cfg)
        self.assertEqual(s['telechargement']['erreur'], 'empreinte_incorrecte')
        self.assertIn('rien n’a été gardé', s['telechargement']['message'])
        self.assertFalse(D.modele_installe(str(self.etat), 'equilibre'))
        self.assertEqual(self.restes(), [])

    def test_D02_etat_pendant_le_telechargement(self):
        D._TELE.update({'en_cours': True, 'modele': 'rapide', 'recus': 12_300_000, 'attendus': 190_085_487, 'state_dir': str(self.etat)})
        t = D.etat(self.cfg)['telechargement']
        self.assertEqual((t['en_cours'], t['recus'], t['attendus']), (True, 12_300_000, 190_085_487))
        self.assertEqual(t['texte'], 'Téléchargement : 12,3 Mo sur 190,1 Mo')
        self.assertFalse(D.etat({'state_dir': str(self.tmp)})['telechargement']['en_cours'])   # autre cabinet : rien de mélangé
        self.installer()
        with self.assertRaisesRegex(Stop, 'telechargement_en_cours'):
            D.retirer(str(self.etat), 'rapide')

    def test_D02_retrait_et_sortie_reseau_declaree(self):
        self.installer()
        D.retirer(str(self.etat), 'rapide')
        self.assertFalse(D.chemin_modele(str(self.etat), 'rapide').exists())
        with self.assertRaisesRegex(Stop, 'modele_non_installe'):
            D.retirer(str(self.etat), 'rapide')
        src = (ROOT / 'agent' / 'dictee56110.py').read_text(encoding='utf-8')
        self.assertIn('EGRESS_GUARD', src.split('"""')[1])                 # comportement face au garde de sortie dit dans l'en-tête
        self.assertNotIn('from .common import HTTP', src)
        vus = []
        with patch('agent.passerelle5689.compter', lambda *a: vus.append(a)):
            D.telecharger(str(self.etat), 'rapide', FauxTransport())
        self.assertEqual(vus, [(str(self.etat), 'huggingface.co', 'telechargement_modele', 'admis')])

    def test_D02_empreintes_des_modeles_reels(self):
        src = (ROOT / 'agent' / 'dictee56110.py').read_text(encoding='utf-8')
        for f, o, h in (('ggml-base-q5_1.bin', 59707625, '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898'),
                        ('ggml-small-q5_1.bin', 190085487, 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb'),
                        ('ggml-large-v3-turbo-q5_0.bin', 574041195, '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2')):
            self.assertIn("'fichier': '%s', 'octets': %d," % (f, o), src)
            self.assertIn("'%s'" % h, src)
        self.assertEqual(D.REVISION, '5359861c739e955e79d9a303bcbc70fb988958b1')
        self.assertEqual(D.DEFAUT_MODELE, 'equilibre')


# ================================================================================================ routes, droits, idempotence, audit
def env_de(corps=b'', csrf='jeton', methode='POST'):
    return {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}


AUTH = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}


class _Banc(_Env):
    def setUp(self):
        super().setUp()
        import test_agent as fixtures
        from agent.desk import Desk
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.state = self.d.c['state_dir']
        self.racine = Path(self.state)
        self.transport = FauxTransport()
        p = patch.object(D, '_ouvrir', self.transport)
        p.start()
        self.addCleanup(p.stop)

    def api(self, action, data=None, role='administrateur', owner='admin', csrf='jeton', methode='POST'):
        corps = json.dumps(data or {}).encode()
        with patch('agent.web567.actor', return_value=(owner, role)):
            out = D.api(env_de(corps, csrf, methode), self.d, AUTH, '', D.PREFIXE + action, {}, methode)
        return out['status'], json.loads(out['body']), out

    def audits(self, action):
        return [json.loads(r[0]) for r in self.d.db.execute('SELECT data FROM audit WHERE action=?', (action,))]

    def attendre(self):
        fil = D._TELE.get('fil')
        if fil is not None:
            fil.join(15)


class Routes(_Banc):
    def test_D03_avocat_refuse_meme_si_le_controle_central_laisse_passer(self):
        code, corps, out = self.api('installer', {'modele': 'rapide', 'version': 0, 'cle': 'cle-essai-0001'}, role='avocat', owner='avocat1')
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'role_insuffisant'))
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(self.transport.urls, [])
        code, corps, _ = self.api('verifier', {}, role='assistant', owner='assistant1')
        self.assertEqual(code, '403 Forbidden')

    def test_D03_installer_en_arriere_plan_active_audit_idempotence_409(self):
        data = {'modele': 'rapide', 'langue': 'fr', 'version': 0, 'cle': 'cle-install-01'}
        code, corps, _ = self.api('installer', data)
        self.assertEqual(code, '200 OK', corps)
        self.attendre()
        self.assertEqual(corps['version'], 1)
        self.assertEqual(len(self.transport.urls), 1)
        self.assertTrue(D.active(self.d.c))
        self.assertEqual(self.audits('dictee_poste_installation_demandee')[0]['modele'], 'rapide')
        fin = self.audits('dictee_poste_modele_installe')
        self.assertEqual(fin[0]['sha256'], hashlib.sha256(CONTENU).hexdigest())
        self.assertEqual(fin[0]['revision'], D.REVISION)
        code2, corps2, _ = self.api('installer', data)                     # même clé, même demande : réponse gardée, rien de rejoué
        self.assertEqual((code2, corps2), (code, corps))
        self.assertEqual(len(self.transport.urls), 1)
        code, corps, _ = self.api('installer', dict(data, modele='precis'))
        self.assertEqual((code, corps['error']), ('409 Conflict', 'cle_reutilisee'))
        code, corps, _ = self.api('reglage', {'langue': 'en', 'version': 0, 'cle': 'cle-reglage-01'})
        self.assertEqual((code, corps['error']), ('409 Conflict', 'version_perimee'))
        code, corps, _ = self.api('reglage', {'langue': 'en', 'version': 1, 'cle': 'cle-reglage-01'})
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(D.reglages_de(self.state)['langue'], 'en')
        code, corps, _ = self.api('reglage', {'moteur': '', 'version': 2, 'cle': 'cle-reglage-02'})
        self.assertEqual((code, corps['message']), ('200 OK', 'Dictée sur ce poste désactivée.'))
        self.assertFalse(D.active(self.d.c))
        code, corps, _ = self.api('installer', {'modele': 'rapide', 'version': 3, 'cle': 'cle-install-02'})   # déjà installé : activé, rien téléchargé
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(len(self.transport.urls), 1)
        self.assertTrue(D.active(self.d.c))
        code, corps, _ = self.api('retirer', {'modele': 'rapide', 'version': 4, 'cle': 'cle-retrait-01'})
        self.assertEqual(code, '200 OK', corps)
        self.assertFalse(D.modele_installe(self.state, 'rapide'))
        self.assertEqual(D.reglages_de(self.state)['moteur'], '')           # modèle choisi retiré : dictée du poste désactivée
        self.assertEqual(self.audits('dictee_poste_modele_retire')[0]['modele'], 'rapide')

    def test_D03_refus_lisibles(self):
        cas = [({'modele': 'enorme', 'version': 0, 'cle': 'cle-refus-0001'}, 'installer', 'modele_inconnu', '400 Bad Request'),
               ({'modele': 'rapide', 'langue': 'it', 'version': 0, 'cle': 'cle-refus-0002'}, 'installer', 'langue_inconnue', '400 Bad Request'),
               ({'moteur': 'poste', 'version': 0, 'cle': 'cle-refus-0003'}, 'reglage', 'modele_non_installe', '400 Bad Request'),
               ({'modele': 'precis', 'version': 0, 'cle': 'cle-refus-0004'}, 'retirer', 'modele_non_installe', '400 Bad Request'),
               ({'modele': 'rapide', 'version': 0}, 'installer', 'cle_requise', '400 Bad Request')]
        for data, action, erreur, statut in cas:
            with self.subTest(erreur):
                code, corps, _ = self.api(action, data)
                self.assertEqual((code, corps['error']), (statut, erreur))
                self.assertEqual(corps['message'], D.MESSAGES[erreur])
        code, corps, _ = self.api('installer', {'modele': 'rapide', 'version': 0, 'cle': 'cle-refus-0005'}, csrf='faux')
        self.assertEqual(code, '403 Forbidden')
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}):
            code, corps, _ = self.api('installer', {'modele': 'rapide', 'version': 0, 'cle': 'cle-refus-0006'})
        self.assertEqual((code, corps['error']), ('503 Service Unavailable', 'moteur_absent'))
        self.assertEqual(self.transport.urls, [])
        code, corps, _ = self.api('inconnu', {})
        self.assertEqual(code, '404 Not Found')
        self.assertEqual(D.reglages_de(self.state)['version'], 0)

    def test_D03_echec_du_telechargement_journalise_sans_activer(self):
        self.transport.contenu = b'faux'
        code, corps, _ = self.api('installer', {'modele': 'rapide', 'version': 0, 'cle': 'cle-echec-001'})
        self.assertEqual(code, '200 OK')
        self.attendre()
        self.assertEqual(self.audits('dictee_poste_telechargement_refuse')[0]['motif'], 'empreinte_incorrecte')
        self.assertFalse(D.active(self.d.c))
        code, corps, _ = self.api('etat', methode='GET')
        self.assertEqual(corps['telechargement']['erreur'], 'empreinte_incorrecte')

    def test_D03_verifier_et_etat(self):
        self.installer('rapide', CONTENU)
        self.d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        faux = FauxMoteur(texte='[BLANK_AUDIO]\r\n')
        with patch.object(D, '_lancer', faux):
            code, corps, _ = self.api('verifier', {'modele': 'rapide'})
        self.assertEqual(code, '200 OK', corps)
        self.assertTrue(corps['message'].startswith('Moteur vérifié : réponse en '))
        self.assertTrue(corps['noreload'])
        self.assertEqual(self.audits('dictee_poste_verification')[0]['resultat'], 'ok')
        with patch.object(D, '_lancer', FauxMoteur(code=3221225781)):
            code, corps, _ = self.api('verifier', {})
        self.assertEqual((code, corps['error']), ('503 Service Unavailable', 'composant_microsoft_absent'))
        self.assertEqual(self.audits('dictee_poste_verification')[1]['resultat'], 'composant_microsoft_absent')
        code, corps, _ = self.api('etat', methode='GET', role='avocat', owner='avocat1')
        self.assertEqual(code, '200 OK')
        self.assertTrue(corps['moteur']['present'])
        self.assertNotIn('dossier', corps['moteur'])                       # chemin du poste réservé à l'administrateur
        self.assertEqual(corps['derniere_erreur'], 'composant_microsoft_absent')
        code, corps, _ = self.api('etat', methode='GET')
        self.assertIn('dossier', corps['moteur'])

    def test_D03_aucun_texte_dicte_dans_l_audit_ni_les_reglages(self):
        secret = 'Texte dicté confidentiel DURAND contre MARTIN'
        self.installer('rapide', CONTENU)
        self.d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        with patch.object(D, '_lancer', FauxMoteur(texte=secret)):
            self.assertEqual(D.dicter(self.d.c, wav_de(parole()), 'audio/wav')['text'], secret)
            self.api('verifier', {})
        tout = json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM audit')], ensure_ascii=False)
        tout += json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM settings')], ensure_ascii=False)
        self.assertNotIn('confidentiel', tout)
        self.assertEqual(list((Path(self.state) / 'dictee56110' / 'travail').iterdir()), [])


# ================================================================================================ écran, messages, traductions, guide
class Ecran(_Banc):
    def section(self, role='administrateur'):
        return D.section_html(self.d, '/p', role)

    def test_D04_moteur_absent_explique_sans_bouton(self):
        for valeur, raison in ((str(self.tmp / 'rien'), 'variable'),):
            with patch.dict(os.environ, {'AXIORHUB_DICTEE': valeur}):
                html = self.section()
            self.assertIn('Dictée sur ce poste', html)
            self.assertIn(D.TEXTES[raison], html)
            self.assertNotIn('data-api', html)
            self.assertNotIn('<button', html)
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': ''}), patch.object(portable, 'WINDOWS', False), patch.object(D, 'LINUX', str(self.tmp / 'rien')):
            html = self.section()
        self.assertIn('serveur ou une image Docker', html)
        self.assertNotIn('data-api', html)

    def test_D04_carte_administrateur(self):
        html = self.section()
        self.assertIn('id="dictee56110"', html)
        self.assertIn('data-api="m56110/dictee/installer"', html)
        self.assertEqual(html.count('type="radio" name="modele"'), 3)
        self.assertIn('value="equilibre" checked', html)                  # modèle conseillé par défaut
        self.assertIn('Installer la dictée sur ce poste', html)
        self.assertIn('huggingface.co', html)
        self.assertIn('Ordre de grandeur sur un poste récent', html)
        self.assertIn('Moteur de dictée : fourni avec cette installation.', html)
        self.assertIn('Dictée sur ce poste : inactive.', html)
        self.assertNotIn('d110-essai', html)                               # zone d'essai seulement quand la dictée est active
        self.assertNotIn('m56110/dictee/verifier', html)
        self.installer('rapide', CONTENU)
        self.d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        html = self.section()
        self.assertIn('Dictée sur ce poste : active.', html)
        for api in ('m56110/dictee/verifier', 'm56110/dictee/reglage', 'm56110/dictee/retirer'):
            self.assertIn('data-api="%s"' % api, html)
        self.assertIn('<label for="d110-essai">', html)
        self.assertIn('<textarea id="d110-essai"', html)
        self.assertNotIn('name=', html.split('<textarea id="d110-essai"')[1].split('>')[0])   # rien n'est envoyé
        self.assertIn('data-confirm=', html)
        avocat = self.section('avocat')
        self.assertNotIn('data-api', avocat)
        self.assertIn('Seul l’administrateur', avocat)

    def test_D04_composant_microsoft_et_progression(self):
        D._DERNIERE['erreur'] = 'composant_microsoft_absent'
        html = self.section()
        self.assertIn('href="https://aka.ms/vs/17/release/vc_redist.x64.exe"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        D._DERNIERE['erreur'] = ''
        D._TELE.update({'en_cours': True, 'modele': 'equilibre', 'recus': 1_000_000, 'attendus': 2_000_000, 'state_dir': self.state})
        html = self.section()
        self.assertIn('aria-live="polite"', html)
        self.assertIn('<progress max="2000000" value="1000000"', html)
        self.assertIn('data-en-cours="1"', html)
        self.assertIn(' disabled>Installer la dictée sur ce poste', html)

    def test_D05_messages_corriges(self):
        from agent import config567
        src = (ROOT / 'agent' / 'config567.py').read_text(encoding='utf-8')
        self.assertNotIn('image 5.6.8', src)
        self.assertNotIn('Installer espeak-ng', src)
        for present in (True, False):
            with patch.object(portable, 'which', lambda nom, p=present: '/usr/bin/espeak-ng' if p else None):
                out = config567.test(self.d, {'connector': 'voice'})
            self.assertEqual(out['ok'], present)
            self.assertIn('Voix de secours', out['message'])
            self.assertIn('Dictée sur ce poste', out['message'])
            self.assertIn('dictation_poste', out)
        libelles = {c[0]: c[1] for c in config567.CATALOG} if hasattr(config567, 'CATALOG') else {}
        self.assertIn('ancienne passerelle', src)
        if libelles:
            self.assertIn('ancienne passerelle', libelles['audio.enabled'])
        self.assertIn("('voice','la voix de secours (lecture à voix haute)')", (ROOT / 'agent' / 'web567.py').read_text(encoding='utf-8'))

    def test_D05_mise_en_service_en_mode_poste(self):
        from agent import readiness569

        def controle():
            with patch('agent.config567.test', lambda desk, data: {'ok': True, 'message': 'ok', 'writes': 0}):
                return {x['key']: x for x in readiness569.run(self.d)['checks']}['dictation']
        self.d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        c = controle()
        self.assertFalse(c['ok'])
        self.assertIn('Installer la dictée sur ce poste', c['fix'])
        self.installer('rapide', CONTENU)
        c = controle()
        self.assertIsNone(c['ok'])
        self.assertIn('Vérifier le moteur', c['fix'])
        D._DERNIERE['erreur'] = 'modele_illisible'
        self.assertFalse(controle()['ok'])
        D._DERNIERE['erreur'] = ''
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}):
            c = controle()
        self.assertFalse(c['ok'])
        for x in (c['message'], c['fix']):
            self.assertNotIn('systemctl', x)
        self.assertNotIn('install-vocal-bridge', (ROOT / 'agent' / 'readiness569.py').read_text(encoding='utf-8'))

    def test_D07_traductions_completes(self):
        import test_v56107_langues as langues
        from agent import config567, ecrans5632 as E
        tables = {l: E.dictionary(l) for l in langues.LANGUES}
        textes = [v for v in D.MESSAGES.values()] + [v for v in D.TEXTES.values() if '%s' not in v] + list(D.LANGUES.values())
        textes += list(config567.VOIX_SECOURS.values())
        for ident, m in D.MODELES.items():
            textes += ['Dictée sur ce poste activée (modèle %s).' % m['nom'], 'Modèle retiré du poste : %s.' % m['nom'], m['conseil']]
        textes += ['Moteur vérifié : réponse en 1,2 s.', 'Téléchargement : 12,3 Mo sur 190,1 Mo']
        manquants = ['%s (%s)' % (t, l) for t in textes for l, table in tables.items() if E.phrase(t, table) is None]
        self.assertEqual(manquants, [])
        pages = [self.section(), self.section('avocat')]
        self.installer('rapide', CONTENU)
        self.d.setting(D.KEY, {'moteur': 'poste', 'modele': 'rapide', 'langue': 'fr', 'version': 1})
        D._DERNIERE['erreur'] = 'composant_microsoft_absent'
        pages.append(self.section())
        D._DERNIERE['erreur'] = ''
        D._TELE.update({'en_cours': True, 'modele': 'equilibre', 'recus': 1_000_000, 'attendus': 2_000_000, 'state_dir': self.state})
        pages.append(self.section())
        D._TELE.update({'en_cours': False, 'erreur': 'empreinte_incorrecte'})
        pages.append(self.section())
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}):
            pages.append(self.section())
        for html in pages:
            for lang in langues.LANGUES:
                self.assertEqual(langues.reste_francais(langues.visibles(E.translate_html(html, lang))), [], lang)
            self.assertEqual(E.translate_html(html, 'fr'), html)


# ================================================================================================ contrôles sans base
class Statique(unittest.TestCase):
    def test_D03_droits_declares(self):
        for action in D.ECRITURES:
            self.assertTrue(D.allowed('administrateur', 'POST', D.PREFIXE + action))
            for role in ('avocat', 'assistant', 'client'):
                self.assertFalse(D.allowed(role, 'POST', D.PREFIXE + action))
            self.assertFalse(D.allowed('administrateur', 'GET', D.PREFIXE + action))
        self.assertTrue(D.allowed('avocat', 'GET', D.PREFIXE + 'etat'))
        self.assertFalse(D.allowed('administrateur', 'POST', D.PREFIXE + 'etat'))
        self.assertFalse(D.allowed('administrateur', 'POST', D.PREFIXE + 'autre'))
        from agent import standalone_auth
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56110/dictee/installer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56110/dictee/verifier'))
        self.assertTrue(standalone_auth.allowed('avocat', 'GET', '/api440/m56110/dictee/etat'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56110/dictee/installer'))
        self.assertIn("name.startswith('m56110/dictee/')", (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8'))

    def test_D04_place_dans_parametres_voix(self):
        src = (ROOT / 'agent' / 'parametres5621.py').read_text(encoding='utf-8')
        voix = src[src.index('def _voix('):src.index('def _routines(')]
        self.assertLess(voix.index('dictee56110 import section_html'), voix.index("heading='Moteurs de voix"))
        self.assertIn('v56110-dictee-carte.js', voix)
        from agent import web440
        for f in ('/static/v56110-dictee.js', '/static/v56110-dictee-carte.js', '/static/v56110-dictee.css'):
            self.assertIn(f, web440.STATIC)
            self.assertTrue((ROOT / 'agent' / f.lstrip('/')).is_file())
        from agent.shell501 import BUNDLE
        self.assertIn('v56110-dictee.css', BUNDLE)
        self.assertIn('/* ==== v56110-dictee.css ==== */', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_D05_erreurs_lisibles(self):
        from agent import erreurs5634
        from agent.web import REASONS
        for code in ('composant_microsoft_absent', 'modele_illisible', 'dictee_poste_format_non_wav', 'dictee_wav_format_refuse',
                     'dictee_poste_delai_depasse', 'dictee_trop_longue', 'moteur_absent'):
            self.assertEqual(erreurs5634.explain(code), D.MESSAGES[code])
            self.assertIn(code, REASONS)
        self.assertEqual(REASONS['transcription_en_cours'], D.MESSAGES['transcription_en_cours'])

    def test_D06_plafonds_des_enregistrements_en_mode_poste(self):
        js = {n: (ROOT / 'agent' / 'static' / n).read_text(encoding='utf-8') for n in ('v310.js', 'v56106-redaction.js', 'v56110-dictee.js')}
        self.assertIn('dureeMax: 590000', js['v56110-dictee.js'])
        self.assertLessEqual(590, D.MAX_SECONDES)
        self.assertLess(44 + 590 * 16000 * 2, D.MAX_OCTETS)               # 9 min 50 s en WAV : sous la limite de la route
        self.assertLess(590 * 64000 / 8, 8_000_000)                        # enregistrement opus à 64 kbit/s : sous le plafond des scripts
        avert = 'Encore 30 secondes : l’enregistrement s’arrêtera à 10 minutes.'
        for nom in ('v310.js', 'v56106-redaction.js'):
            self.assertIn('window.ax110Dictee ? window.ax110Dictee.dureeMax' if nom != 'v310.js' else 'window.ax110Dictee?window.ax110Dictee.dureeMax:60000', js[nom])
            self.assertIn(avert, js[nom])
        from agent import ecrans5632 as E
        for lang in ('en', 'de', 'es'):
            self.assertIsNotNone(E.phrase(avert, E.dictionary(lang)), lang)

    def test_D07_guide_en_ligne(self):
        from agent import aide5631
        self.assertEqual(aide5631.check(), [])
        for lang, mot in (('fr', 'Installer la dictée sur ce poste'), ('en', 'Install dictation on this computer'),
                          ('de', 'Diktat auf diesem Arbeitsplatz installieren'), ('es', 'Instalar el dictado en este equipo')):
            fiche = json.loads((ROOT / 'agent' / 'aide' / ('%s.json' % lang)).read_text(encoding='utf-8'))['mise_en_service']
            self.assertTrue(any(mot in s for s in fiche['steps']), lang)
            self.assertTrue(3 <= len(fiche['steps']) <= 7)

    def test_D07_scripts_sans_texte_a_traduire_et_conversion_limitee(self):
        import test_v56107_langues as langues
        for nom in ('v56110-dictee.js', 'v56110-dictee-carte.js'):
            visibles = [s for _, s in langues.litteraux_js(ROOT / 'agent' / 'static' / nom) if langues.texte_visible_js(s)]
            self.assertEqual(visibles, [], nom)                            # textes visibles fournis par le serveur (déjà traduits)
        js = (ROOT / 'agent' / 'static' / 'v56110-dictee.js').read_text(encoding='utf-8')
        for attendu in ("prefix + '/dictation'", 'OfflineAudioContext', 'decodeAudioData', "'audio/wav'", 'startRendering', "'RIFF'", "'WAVE'",
                        "!== 'POST'", 'instanceof Blob', 'origine.apply(racine, arguments)'):
            self.assertIn(attendu, js)
        for interdit in ('innerHTML', 'eval(', 'localStorage', 'sessionStorage', 'console.log'):
            self.assertNotIn(interdit, js)
        self.assertNotIn('innerHTML', (ROOT / 'agent' / 'static' / 'v56110-dictee-carte.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
