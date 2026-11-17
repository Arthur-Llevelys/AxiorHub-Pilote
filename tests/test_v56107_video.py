"""5.6.107 (lot V) : vidéo de démonstration de 90 secondes — texte de la voix, minutage, sous-titres, voix assemblée, montage ffmpeg,
calque injecté et tournage (fonctions pures ; ni navigateur, ni synthèse vocale, ni ffmpeg, ni réseau).

Exigences : V1 texte de la voix (longueur, une phrase par plan, sans promesse ni chiffre), V2 minutage cohérent avec le scénario, V3 voix
assemblée au début réel de chaque plan, V4 montage qui refuse proprement l'absence de ffmpeg (et commandes MP4/WebM/affiche attendues),
V5 sous-titres SRT et VTT bien formés, V6 tournage sans requête extérieure et calque compatible avec la CSP du produit, V7 données
fictives seulement, V8 notice WordPress. Chaque garde-fou a son contre-exemple (mutation en mémoire, jamais de code laissé altéré).
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from video56107 import montage as mt   # noqa: E402
from video56107 import texte as tx     # noqa: E402
from video56107 import voix as vx      # noqa: E402

DOSSIER = ROOT / 'scripts' / 'video56107'
NOMS_FICTIFS = {'DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC'}


def charger_cli():
    spec = importlib.util.spec_from_file_location('video_demo_cli', str(ROOT / 'scripts' / 'video-demo.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def wav(chemin, secondes, taux=22050, valeur=1000, canaux=1):
    with wave.open(str(chemin), 'wb') as w:
        w.setnchannels(canaux)
        w.setsampwidth(2)
        w.setframerate(taux)
        w.writeframes(int(valeur).to_bytes(2, 'little', signed=True) * canaux * int(secondes * taux))


class Texte(unittest.TestCase):
    def setUp(self):
        self.voix, self.scen = tx.lire_voix(), tx.scenario()

    def test_V1_texte_present_et_dans_la_longueur_visee(self):
        self.assertTrue((DOSSIER / 'voix.txt').is_file())
        total = sum(tx.compter_mots(p['texte']) for p in self.voix)
        # visé : 220 à 240 mots ; texte validé par l'avocat le 17/11/2026 (plan 4 remplacé) : 217 mots, tolérance basse 210
        self.assertGreaterEqual(total, 210)
        self.assertLessEqual(total, 240)
        self.assertEqual((tx.MOTS_MIN, tx.MOTS_MAX), (210, 240))
        self.assertEqual(tx.controler(self.voix, self.scen['plans']), [])

    def test_V1_plan_4_valide_par_l_avocat_sans_envoi(self):
        plan4 = next(p for p in self.voix if p['id'] == 4)
        self.assertEqual(plan4['texte'], "Pour ce courriel, l'IA a déjà préparé un projet de réponse, rangé dans À relire : vous le relisez, "
                                         "vous corrigez un mot si nécessaire, puis vous décidez : rien ne part sans vous.")
        scen4 = next(p for p in self.scen['plans'] if p['id'] == 4)
        self.assertEqual(scen4['legende'], "Projet de réponse de l'IA : relu, corrigé, retenu")
        tout = ' '.join(p['texte'] for p in self.voix) + json.dumps(self.scen, ensure_ascii=False)
        for interdit in ('envoy', 'annuler', 'dix secondes', 'compte à rebours'):
            self.assertNotIn(interdit, tout.lower())
        from video56107 import film
        src = Path(film.__file__).read_text(encoding='utf-8')
        self.assertNotIn('data-demo-envoyer', src)          # plus d'envoi filmé : relecture, correction d'un mot, validation
        self.assertIn('[data-action="retenir"]', src)       # le bouton d'une proposition (décision du 17/11/2026)
        self.assertNotIn('masqué par', ' '.join(r for p in self._minutage_fr() for r in p.get('remarques', [])))

    def _minutage_fr(self):
        f = ROOT.parent / 'video-5.6.107' / 'minutage.json'     # dernier tournage, s'il existe sur ce poste (hors dépôt)
        return json.loads(f.read_text(encoding='utf-8'))['plans'] if f.is_file() else []

    def test_V1_une_phrase_par_plan_sans_chiffre_ni_promesse(self):
        for p in self.voix:
            self.assertEqual(len(tx.phrases(p['texte'])), 1, p['texte'])
            self.assertTrue(p['texte'].endswith('.'), p['id'])
            self.assertIsNone(re.search(r'\d', p['texte']), 'chiffre écrit dans la voix (plan %d)' % p['id'])
            self.assertIsNone(re.search(r'(?i)garanti|certitude|sans risque|aucun risque|toujours|infaillible|conforme à la loi|100 ?%',
                                        p['texte']), 'promesse dans la voix (plan %d)' % p['id'])
        self.assertIn('axiorhub.com', self.voix[-1]['texte'])

    def test_V1_controle_detecte_les_ecarts(self):
        # contre-exemples : texte trop long, deux phrases, plan muet, durée différente du scénario
        long = [dict(p, texte=p['texte'] + ' Et encore quelques mots pour dépasser.') for p in self.voix]
        self.assertTrue(any('phrases' in a for a in tx.controler(long, self.scen['plans'])))
        court = [dict(p, texte='Texte bref.') for p in self.voix]
        self.assertTrue(any('mots' in a for a in tx.controler(court, self.scen['plans'])))
        muet = [dict(p) for p in self.voix]
        muet[2]['texte'] = ''
        self.assertTrue(any('aucun texte' in a for a in tx.controler(muet, self.scen['plans'])))
        decale = [dict(p) for p in self.voix]
        decale[0]['duree_visee'] = 9
        self.assertTrue(any('durée visée' in a for a in tx.controler(decale, self.scen['plans'])))
        self.assertTrue(any('différents' in a for a in tx.controler(self.voix[:-1], self.scen['plans'])))

    def test_V1_lecture_du_format(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'voix.txt'
            f.write_text('# commentaire\n\n[1] Ouverture | 4,5 s\nPremière ligne\nsuite du plan.\n[2] Fin | 3 s\nFin.\n', encoding='utf-8')
            plans = tx.lire_voix(f)
            self.assertEqual([(p['id'], p['duree_visee'], p['texte']) for p in plans],
                             [(1, 4.5, 'Première ligne suite du plan.'), (2, 3.0, 'Fin.')])
            f.write_text('Texte orphelin\n[1] A | 2 s\nX.\n', encoding='utf-8')
            with self.assertRaises(tx.ErreurTexte):
                tx.lire_voix(f)

    def test_V1_prononciation_mots_entiers(self):
        table = tx.langue('fr')['prononciation']
        parle = tx.texte_parle("L'IA d'AxiorHub sur axiorhub.com et le RPVA ; MAIA reste.", table)
        # réglée à l'oreille sur les phonèmes de Kokoro : sigles épelés lettre à lettre, sans points (les points font des pauses)
        self.assertIn("L'I A", parle)
        self.assertIn("d'Axior Hub", parle)
        self.assertIn('Axior Hub point com', parle)
        self.assertIn('R P V A', parle)
        self.assertIn('MAIA', parle)                       # « IA » à l'intérieur d'un mot : inchangé


class Minutage(unittest.TestCase):
    def setUp(self):
        self.voix, self.scen = tx.lire_voix(), tx.scenario()

    def test_V2_huit_plans_dans_l_ordre_du_scenario(self):
        noms = [p['nom'] for p in self.scen['plans']]
        self.assertEqual(noms, ['aujourdhui', 'courriel', 'dossier', 'reponse', 'resume', 'fraude', 'rpva', 'fin'])
        self.assertEqual([p['duree_visee'] for p in self.scen['plans']], [5, 15, 12, 15, 10, 8, 10, 8])
        self.assertEqual([p['duree_visee'] for p in self.voix], [5, 15, 12, 15, 10, 8, 10, 8])
        from video56107 import film
        self.assertEqual(set(noms), set(film.PLANS))
        fin = self.scen['plans'][-1]['fin']
        self.assertEqual((fin['devise'], fin['adresse']), ('Votre cabinet, vos données, sur votre serveur', 'axiorhub.com'))

    def test_V2_duree_jamais_sous_la_visee_et_assez_pour_la_voix(self):
        r = tx.reglages()['voix']
        mesures = {p['id']: 3.0 if p['id'] % 2 else 14.5 for p in self.voix}
        m = tx.minutage(self.voix, mesures)
        debut = 0.0
        for p in m:
            self.assertGreaterEqual(p['duree'], p['duree_visee'])
            self.assertGreaterEqual(p['duree'] + 1e-6, r['avance_s'] + mesures[p['id']] + r['marge_s'])
            self.assertAlmostEqual(p['debut'], debut, places=2)
            self.assertFalse(p['estime'])
            debut += p['duree']
        estime = tx.minutage(self.voix, None)
        self.assertTrue(all(p['estime'] for p in estime))
        total = sum(p['duree'] for p in estime)
        self.assertGreater(total, 80)                      # « environ 90 secondes »
        self.assertLess(total, 100)

    def test_V2_voix_mesuree_partielle(self):
        m = tx.minutage(self.voix, {'1': 2.0})             # clés JSON (texte) acceptées ; plans sans mesure : estimés
        self.assertFalse(m[0]['estime'])
        self.assertTrue(m[1]['estime'])
        self.assertEqual(m[0]['duree'], 5.0)


class SousTitres(unittest.TestCase):
    def plans_reels(self):
        m = tx.minutage(tx.lire_voix(), {p['id']: 0.8 * p['duree_visee'] for p in tx.lire_voix()})
        return [{'id': p['id'], 'debut': p['debut'], 'duree': p['duree'], 'voix_s': p['voix_s']} for p in m]

    def test_V5_srt_et_vtt_bien_formes(self):
        reels = self.plans_reels()
        repl = tx.sous_titres(tx.lire_voix(), reels)
        self.assertGreaterEqual(len(repl), 8)
        self.assertEqual(tx.verifier_srt(tx.srt(repl)), len(repl))
        self.assertEqual(tx.verifier_vtt(tx.vtt(repl)), len(repl))
        self.assertTrue(tx.vtt(repl).startswith('WEBVTT\n\n1\n00:00:00.'))
        self.assertRegex(tx.srt(repl), r'^1\n00:00:00,\d{3} --> 00:00:\d\d,\d{3}\n')
        # chaque réplique reste dans son plan ; tout le texte de la voix est sous-titré, mot pour mot
        bornes = {p['id']: (p['debut'], p['debut'] + p['duree']) for p in reels}
        for a, b, texte in repl:
            self.assertTrue(any(d - 1e-6 <= a < b <= f + 1e-6 for d, f in bornes.values()), (a, b, texte))
            for ligne in texte.split('\n'):
                self.assertLessEqual(len(ligne), 42)
                self.assertFalse(ligne.startswith((':', ';', '!', '?')), ligne)
        self.assertEqual(' '.join(t.replace('\n', ' ') for _, _, t in repl).split(),
                         ' '.join(p['texte'] for p in tx.lire_voix()).split())

    def test_V5_verification_refuse_les_fichiers_mal_formes(self):
        bon = tx.srt([(0.0, 1.5, 'Bonjour.'), (1.5, 3.0, 'Suite.')])
        self.assertEqual(tx.verifier_srt(bon), 2)
        self.assertEqual(tx.verifier_srt('﻿' + bon.replace('\n', '\r\n')), 2)   # BOM et fins de ligne Windows tolérés
        for mauvais in (bon.replace('00:00:01,500 -->', '00:00:01.500 -->'),          # séparateur VTT dans un SRT
                        bon.replace('2\n', '3\n', 1),                                   # numérotation
                        tx.srt([(2.0, 1.0, 'À rebours.')]),                             # fin avant le début
                        tx.srt([(0.0, 2.0, 'A.'), (1.0, 3.0, 'Chevauche.')]),           # chevauchement
                        tx.srt([(0.0, 2.0, 'une\ndeux\ntrois')]),                       # trois lignes
                        ''):
            with self.assertRaises(tx.ErreurTexte, msg=repr(mauvais[:60])):
                tx.verifier_srt(mauvais)
        vtt = tx.vtt([(0.0, 1.5, 'Bonjour.')])
        self.assertEqual(tx.verifier_vtt(vtt), 1)
        for mauvais in (vtt.replace('WEBVTT', 'WEBVT'), vtt.replace('00:00:01.500', '00:00:01,500'), 'WEBVTT\n\n'):
            with self.assertRaises(tx.ErreurTexte):
                tx.verifier_vtt(mauvais)

    def test_V5_repliques_equilibrees(self):
        texte = tx.lire_voix()[1]['texte']
        repl = tx.repliques(texte)
        self.assertEqual(len(repl), 3)
        suites = [r.split()[0].lower() for r in repl[1:]]
        for r, suite in zip(repl, suites + ['']):   # coupure après une ponctuation ou juste avant une liaison (« et », « puis »…)
            self.assertTrue(r.endswith((',', ':', '.')) or suite in tx.LIAISONS, (r, suite))
        for plan in tx.lire_voix():                  # mêmes règles pour toute la voix
            repl = tx.repliques(plan['texte'])
            suites = [r.split()[0].lower() for r in repl[1:]]
            mauvaises = [(r, s) for r, s in zip(repl, suites) if not (r.endswith((',', ':', '.')) or s in tx.LIAISONS)]
            self.assertLessEqual(len(mauvaises), 1, (plan['id'], mauvaises))
        self.assertEqual(tx.repliques('Court.'), ['Court.'])


class Voix(unittest.TestCase):
    def test_V3_assemblage_au_debut_reel_de_chaque_plan(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav(d / 'a.wav', 1.0, valeur=1000)
            wav(d / 'b.wav', 0.5, valeur=2000)
            sortie = d / 'tout.wav'
            duree = vx.assembler({1: d / 'a.wav', 2: d / 'b.wav'}, {1: 0.0, 2: 2.0}, 4.0, sortie, avance=0.25)
            self.assertAlmostEqual(duree, 4.0, places=3)
            self.assertAlmostEqual(vx.duree_wav(sortie), 4.0, places=3)
            with wave.open(str(sortie), 'rb') as w:
                taux, brut = w.getframerate(), w.readframes(w.getnframes())

            def ech(t):
                i = int(t * taux) * 2
                return int.from_bytes(brut[i:i + 2], 'little', signed=True)
            self.assertEqual(ech(0.1), 0)            # silence avant la voix du plan 1 (avance)
            self.assertEqual(ech(0.3), 1000)         # voix du plan 1
            self.assertEqual(ech(1.6), 0)            # silence entre les plans
            self.assertEqual(ech(2.4), 2000)         # voix du plan 2 à 2,0 + 0,25 s
            self.assertEqual(ech(3.5), 0)
            # une voix qui déborde n'écrase jamais la suivante : elle est décalée après elle, et la durée allongée
            duree = vx.assembler({1: d / 'a.wav', 2: d / 'b.wav'}, {1: 0.0, 2: 0.5}, 1.0, sortie)
            self.assertAlmostEqual(duree, 1.5, places=3)

    def test_V3_bords_silence_de_tete_et_de_queue_sans_couper_le_son(self):
        # défaut signalé le 17/11/2026 (« premier mot coupé ») : chaque phrase commence après 250 ms de silence et finit par 150 ms
        import array
        taux = 24000
        for avant_s in (0.0, 0.02, 0.6):                 # voix collée au début (attaque abrupte), presque collée, trop de silence
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / 'p.wav'
                son = array.array('h', [0] * int(avant_s * taux)
                                  + [(-1) ** i * (200 if i < 240 else 6000) for i in range(int(0.8 * taux))]   # attaque faible puis voix
                                  + [0] * int(0.4 * taux))
                with wave.open(str(f), 'wb') as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(taux)
                    w.writeframes(son.tobytes())
                vx.bords(f, 0.25, 0.15, 0.01)
                with wave.open(str(f)) as w:
                    sortie = array.array('h', w.readframes(w.getnframes()))
                debut, fin = vx.debut_fin_son(sortie, 64)
                self.assertGreaterEqual(debut, int(0.25 * taux), avant_s)              # au moins 250 ms de silence de tête
                # voix collée au début du fichier : le fondu de 10 ms porte sur l'attaque (seul cas où il touche le son)
                self.assertLessEqual(debut, int(0.25 * taux) + (int(0.01 * taux) if avant_s < 0.03 else 1), avant_s)
                self.assertGreaterEqual(len(sortie) - 1 - fin, int(0.15 * taux) - 1)  # 150 ms de silence de queue
                energie = lambda e: sum(abs(x) for x in e)   # noqa: E731
                if avant_s >= 0.03:
                    self.assertEqual(energie(sortie), energie(son), avant_s)          # rien du son n'est retiré ni atténué
                else:
                    self.assertGreaterEqual(energie(sortie), 0.99 * energie(son), avant_s)

    def test_V3_silence_de_tete_dans_les_fichiers_produits(self):
        # sur le poste de production : chaque WAV de plan et la piste assemblée ont le silence de tête aux instants attendus
        import array
        sortie = ROOT.parent / 'video-5.6.107'
        regl = tx.reglages()['voix']
        for dossier, minutage in (('voix', 'minutage.json'),):
            durees, mt_f = sortie / dossier / 'durees.json', sortie / minutage
            if not (durees.is_file() and mt_f.is_file() and (sortie / dossier / 'voix-complete.wav').is_file()):
                self.skipTest('sorties de la vidéo absentes sur ce poste')
            d = json.loads(durees.read_text(encoding='utf-8'))
            if 'bords' not in d:
                self.skipTest('voix produite avant la correction des bords')
            for ident, chemin in d['fichiers'].items():
                with wave.open(chemin) as w:
                    taux, ech = w.getframerate(), array.array('h', w.readframes(w.getnframes()))
                debut, fin = vx.debut_fin_son(ech, max(64, int(0.01 * max(abs(x) for x in ech))))
                self.assertAlmostEqual(debut / taux, regl['tete_s'], delta=0.002, msg='plan %s' % ident)
                self.assertGreaterEqual((len(ech) - 1 - fin) / taux, regl['queue_s'] - 0.002)
            r = json.loads(mt_f.read_text(encoding='utf-8'))
            with wave.open(str(sortie / dossier / 'voix-complete.wav')) as w:
                taux, piste = w.getframerate(), array.array('h', w.readframes(w.getnframes()))
            for p in r['plans']:
                t = p['debut'] - r['debut_premier_plan_s']                 # changement de plan à l'image
                debut_voix = t + regl['avance_s'] + regl['tete_s']
                avant = piste[int(t * taux):int((debut_voix - 0.005) * taux)]
                self.assertTrue(avant and max(abs(x) for x in avant) < 64, 'plan %d : son avant l’instant attendu' % p['id'])
                apres = piste[int((debut_voix - 0.005) * taux):int((debut_voix + 0.4) * taux)]
                self.assertGreaterEqual(max(abs(x) for x in apres), 500, 'plan %d : pas de voix à l’instant attendu' % p['id'])

    def test_V3_formats_differents_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav(d / 'a.wav', 0.2, taux=22050)
            wav(d / 'b.wav', 0.2, taux=16000)
            with self.assertRaises(vx.ErreurVoix):
                vx.assembler({1: d / 'a.wav', 2: d / 'b.wav'}, {1: 0, 2: 1}, 2, d / 'x.wav')

    def test_V3_synthese_locale_seulement(self):
        src = ''.join((DOSSIER / n).read_text(encoding='utf-8') for n in ('voix.py', 'synthese-windows.ps1', 'synthese-kokoro.py'))
        self.assertIn('System.Speech', src)
        self.assertEqual(tx.langue('fr')['voix']['voix'], 'ff_siwis')
        self.assertIn('espeak-ng', src)
        for interdit in ('http://', 'https://', 'urlopen', 'Invoke-WebRequest', 'requests', 'Azure', 'Cognitive'):
            self.assertNotIn(interdit, src)
        self.assertTrue(all(ord(c) < 128 for c in (DOSSIER / 'synthese-windows.ps1').read_text(encoding='utf-8')),
                        'le script PowerShell doit rester en ASCII (lu en ANSI par PowerShell 5.1)')
        with self.assertRaises(vx.ErreurVoix):
            vx.synthetiser(tx.lire_voix()[:1], tempfile.mkdtemp(prefix='axh-v56107-'), choix='aucun')

    def test_V3_kokoro_hors_ligne_avant_tout_import(self):
        src = (DOSSIER / 'synthese-kokoro.py').read_text(encoding='utf-8')
        hors_ligne = src.index("os.environ['HF_HUB_OFFLINE'] = '1'")
        self.assertLess(hors_ligne, src.index('from kokoro import'))
        self.assertLess(src.index("os.environ['TRANSFORMERS_OFFLINE'] = '1'"), src.index('from kokoro import'))
        self.assertIn("voice=fichiers['voix']", src)       # voix lue dans le dossier local, jamais par son nom (téléchargement)
        self.assertIn("HF_HUB_OFFLINE='1'", (DOSSIER / 'voix.py').read_text(encoding='utf-8'))
        # contre-exemple : sans la ligne hors ligne, la vérification ci-dessus échoue
        mute = src.replace("os.environ['HF_HUB_OFFLINE'] = '1'\n", '')
        with self.assertRaises(ValueError):
            mute.index("os.environ['HF_HUB_OFFLINE'] = '1'")

    def test_V3_moteur_kokoro_par_defaut_si_modele_present(self):
        regl = tx.reglages()
        with tempfile.TemporaryDirectory() as d:
            env = {'KOKORO_MODELE': d}
            self.assertEqual(vx.dossier_kokoro(regl, env), Path(d))
            self.assertFalse(vx.kokoro_pret(regl, env))         # modèle incomplet : jamais choisi, jamais téléchargé
            (Path(d) / 'voices').mkdir()
            for f in ('config.json', 'kokoro-v1_0.pth', 'voices/ff_siwis.pt'):
                (Path(d) / f).write_bytes(b'x')
            installe = importlib.util.find_spec('kokoro') is not None
            self.assertEqual(vx.kokoro_pret(regl, env), installe)
            (Path(d) / 'voices' / 'ff_siwis.pt').unlink()
            self.assertFalse(vx.kokoro_pret(regl, env))
        self.assertEqual(vx.dossier_kokoro(regl, {}), (ROOT / regl['voix']['kokoro']['modele']).resolve())
        ancien = os.environ.get('KOKORO_MODELE')
        os.environ['KOKORO_MODELE'] = tempfile.mkdtemp(prefix='axh-v56107-vide-')
        try:
            with self.assertRaises(vx.ErreurVoix) as ctx:
                vx.synthetiser(tx.lire_voix()[:1], tempfile.mkdtemp(prefix='axh-v56107-'), choix='kokoro')
            self.assertIn('KOKORO_MODELE', str(ctx.exception))
            self.assertNotEqual(vx.moteur(), 'kokoro')
        finally:
            if ancien is None:
                os.environ.pop('KOKORO_MODELE', None)
            else:
                os.environ['KOKORO_MODELE'] = ancien


class FauxResultat:
    def __init__(self, code=0, sortie='', erreur=''):
        self.returncode, self.stdout, self.stderr = code, sortie, erreur


ENCODEURS = ''.join(' V....D %-20s x\n' % e for e in ('libx264', 'libvpx-vp9')) + ''.join(' A....D %-20s x\n' % e for e in ('aac', 'libopus'))


class Montage(unittest.TestCase):
    def test_V4_absence_de_ffmpeg_refusee_proprement(self):
        appels = []
        executer = lambda *a, **k: appels.append(a) or FauxResultat(0, ENCODEURS)   # noqa: E731
        with self.assertRaises(mt.FfmpegAbsent) as ctx:
            mt.trouver_ffmpeg({}, executer=executer)
        self.assertTrue(str(ctx.exception).startswith('ffmpeg est introuvable'))
        self.assertIn('FFMPEG', str(ctx.exception))
        self.assertIn('accord du cabinet', str(ctx.exception))
        with self.assertRaises(mt.FfmpegAbsent):
            mt.trouver_ffmpeg({'FFMPEG': '   '}, executer=executer)
        with self.assertRaises(mt.FfmpegAbsent) as ctx:
            mt.trouver_ffmpeg({'FFMPEG': str(ROOT / 'introuvable' / 'ffmpeg.exe')}, executer=executer)
        self.assertIn('n’existe pas', str(ctx.exception))
        self.assertEqual(appels, [])                                   # rien n'est exécuté sans ffmpeg

    def test_V4_ffmpeg_incomplet_ou_en_panne_refuse(self):
        faux = str(Path(__file__))                                     # un fichier existant tient lieu de ffmpeg (jamais exécuté)
        with self.assertRaises(mt.FfmpegAbsent) as ctx:
            mt.trouver_ffmpeg({'FFMPEG': faux}, executer=lambda *a, **k: FauxResultat(0, ' V....D libx264  x\n'))
        self.assertIn('libvpx-vp9', str(ctx.exception))
        with self.assertRaises(mt.FfmpegAbsent):
            mt.trouver_ffmpeg({'FFMPEG': faux}, executer=lambda *a, **k: FauxResultat(1, ''))

        def plante(*a, **k):
            raise OSError('format exécutable invalide')
        with self.assertRaises(mt.FfmpegAbsent):
            mt.trouver_ffmpeg({'FFMPEG': faux}, executer=plante)
        self.assertEqual(mt.trouver_ffmpeg({'FFMPEG': '"%s"' % faux}, executer=lambda *a, **k: FauxResultat(0, ENCODEURS)), faux)

    def test_V4_mutation_garde_ffmpeg_absent(self):
        # contre-exemple : sans la première garde, le message n'est plus le message clair attendu (le test ci-dessus échouerait)
        src = Path(mt.__file__).read_text(encoding='utf-8')
        garde = "    if not chemin:\n        raise FfmpegAbsent(MESSAGE_ABSENT)\n"
        self.assertIn(garde, src)
        mute = types.ModuleType('montage_mute')
        exec(compile(src.replace(garde, ''), 'montage_mute', 'exec'), mute.__dict__)
        try:
            mute.trouver_ffmpeg({}, executer=lambda *a, **k: FauxResultat(0, ENCODEURS))
            message = ''
        except mute.FfmpegAbsent as ex:
            message = str(ex)
        self.assertFalse(message.startswith('ffmpeg est introuvable'))

    def test_V4_commande_de_montage_sans_ffmpeg_code_3_et_rien_produit(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != 'FFMPEG'}
            env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'video-demo.py'), 'montage', '--sortie', d], env=env,
                               capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn('ffmpeg est introuvable', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            produits = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(produits, ['LISEZMOI.txt'])              # ni MP4, ni WebM, ni affiche

    def test_V4_commandes_mp4_webm_affiche(self):
        regl = tx.reglages()
        liste = mt.commandes('ffmpeg', 'brut.webm', 'voix.wav', 2.297, 92.73, 'sortie', 'axiorhub-demo-90s', regl)
        self.assertEqual([l for l, _ in liste], ['MP4 H.264 + AAC', 'WebM VP9 + Opus', 'affiche PNG (première image)'])
        mp4, webm, affiche = (a for _, a in liste)
        for argv in (mp4, webm):
            self.assertEqual(argv[argv.index('-ss') + 1], '2.297')       # début du premier plan
            self.assertEqual(argv[argv.index('-t') + 1], '92.730')       # durée totale des plans
            self.assertIn('0:v:0', argv)
            self.assertIn('1:a:0', argv)
            self.assertIn('fps=30,scale=1920:1080:flags=lanczos,format=yuv420p', argv)
        self.assertEqual(mp4[mp4.index('-c:v') + 1], 'libx264')
        self.assertEqual(mp4[mp4.index('-pix_fmt') + 1], 'yuv420p')
        self.assertEqual(mp4[mp4.index('-c:a') + 1], 'aac')
        self.assertEqual(mp4[mp4.index('-movflags') + 1], '+faststart')
        self.assertTrue(mp4[-1].endswith('axiorhub-demo-90s.mp4'))
        self.assertEqual(webm[webm.index('-c:v') + 1], 'libvpx-vp9')
        self.assertEqual(webm[webm.index('-c:a') + 1], 'libopus')
        self.assertTrue(webm[-1].endswith('axiorhub-demo-90s.webm'))
        self.assertTrue(affiche[affiche.index('-i') + 1].endswith('axiorhub-demo-90s.mp4'))
        self.assertEqual(affiche[affiche.index('-frames:v') + 1], '1')
        self.assertTrue(affiche[-1].endswith('affiche.png'))
        muette = mt.commandes('ffmpeg', 'brut.webm', None, 0, 10, 'sortie', 'x', regl)
        self.assertIn('-an', muette[0][1])
        self.assertNotIn('aac', muette[0][1])

    def test_V4_montage_s_arrete_a_la_premiere_erreur(self):
        faits = []

        def executer(argv, **k):
            faits.append(argv[-1])
            return FauxResultat(1, '', 'Unknown encoder')
        with self.assertRaises(RuntimeError) as ctx:
            mt.monter('ffmpeg', [('A', ['ffmpeg', 'a.mp4']), ('B', ['ffmpeg', 'b.webm'])], journal=lambda *_: None, executer=executer)
        self.assertIn('Unknown encoder', str(ctx.exception))
        self.assertEqual(faits, ['a.mp4'])
        with tempfile.TemporaryDirectory() as d:          # code 0 mais aucun fichier : refusé (jamais de succès sans preuve)
            with self.assertRaises(RuntimeError):
                mt.monter('ffmpeg', [('A', ['ffmpeg', str(Path(d) / 'vide.mp4')])], journal=lambda *_: None,
                          executer=lambda *a, **k: FauxResultat(0))


class FauxRoute:
    def __init__(self, url):
        self.request = types.SimpleNamespace(url=url)
        self.fait = None

    def continue_(self):
        self.fait = 'continue'

    def abort(self):
        self.fait = 'abort'


class Tournage(unittest.TestCase):
    URLS = (('http://127.0.0.1:8123/agent-courriel/aujourdhui', 'continue'), ('data:image/png;base64,AA', 'continue'),
            ('blob:http://127.0.0.1:8123/x', 'continue'), ('https://cdn.example.test/lib.js', 'abort'),
            ('http://127.0.0.1:9999/autre', 'abort'), ('http://127.0.0.1:8123.example.test/x', 'abort'),
            ('ws://127.0.0.1:8123/flux', 'abort'), ('https://axiorhub.com/', 'abort'))

    def verifier_filtre(self, fabrique):
        bloquees = []
        filtre = fabrique('http://127.0.0.1:8123', bloquees)
        for url, attendu in self.URLS:
            r = FauxRoute(url)
            filtre(r)
            self.assertEqual(r.fait, attendu, url)
        self.assertEqual(len(bloquees), sum(1 for _, a in self.URLS if a == 'abort'))

    def test_V6_aucune_requete_exterieure_pendant_le_tournage(self):
        from video56107 import film
        self.verifier_filtre(film.filtre_requetes)
        src = Path(film.__file__).read_text(encoding='utf-8')
        self.assertIn("ctx.route('**/*', filtre_requetes(source.origine, bloquees))", src)
        self.assertEqual(set(re.findall(r'https?://[^\s\'"%]+', src)) - {'http://127.0.0.1:'}, set())

    def test_V6_mutation_filtre_ouvert(self):
        # contre-exemple : un filtre qui laisse tout passer fait échouer la vérification
        from video56107 import film
        src = Path(film.__file__).read_text(encoding='utf-8')
        debut, fin = src.index('def filtre_requetes('), src.index('    return filtre\n') + len('    return filtre\n')
        mute = {}
        exec(compile(src[debut:fin].replace("        bloquees.append(u[:200])\n        return route.abort()",
                                            "        return route.continue_()"), 'film_mute', 'exec'), mute)
        with self.assertRaises(AssertionError):
            self.verifier_filtre(mute['filtre_requetes'])

    def test_V6_calque_compatible_csp_et_sans_reseau(self):
        js = (DOSSIER / 'calque.js').read_text(encoding='utf-8').replace("'http://www.w3.org/2000/svg'", 'NS_SVG')   # espace de noms, pas une requête
        for interdit in ("setAttribute('style'", 'innerHTML', 'insertAdjacentHTML', "createElement('style')", 'fetch(', 'XMLHttpRequest',
                         'sendBeacon', 'WebSocket', 'eval(', 'localStorage', 'http://', 'https://'):
            self.assertNotIn(interdit, js)
        self.assertIn('window.top !== window', js)          # jamais dans les cadres (lecteur isolé)
        self.assertIn('textContent', js)                    # légendes en texte, jamais en HTML
        for f in ('legende', 'placer', 'onde', 'carton'):
            self.assertIn(f + ':', js)

    def test_V6_cibles_et_variantes(self):
        from video56107 import film
        self.assertEqual(film.alternatives(['a', 'b']), [['a', 'b']])
        self.assertEqual(film.alternatives([['a'], ['b', 'c']]), [['a'], ['b', 'c']])
        self.assertEqual(film.libelle([['a'], ['b', 'c']]), 'a ou b + c')
        for plan in tx.scenario()['plans']:
            if plan['nom'] in ('courriel', 'fraude', 'rpva'):
                self.assertTrue(all(film.alternatives(plan['cibles']['courriel'])), plan['nom'])
        mots = film.alternatives(next(p for p in tx.scenario()['plans'] if p['nom'] == 'reponse')['cibles']['mot'])
        self.assertTrue(all(len(m) == 2 and m[0] != m[1] for m in mots))

    def test_V6_cli_verifier_sans_navigateur(self):
        cli = charger_cli()
        tampon = io.StringIO()
        sortie, sys.stdout = sys.stdout, tampon
        try:
            code = cli.main(['verifier', '--sortie', tempfile.mkdtemp(prefix='axh-v56107-')])
        finally:
            sys.stdout = sortie
        self.assertEqual(code, 0, tampon.getvalue())
        self.assertIn('8 plans', tampon.getvalue())


class Donnees(unittest.TestCase):
    def test_V7_courriels_du_banc_fictifs(self):
        from video56107 import film
        from zoneinfo import ZoneInfo
        t = datetime(2026, 11, 16, 9, 30, tzinfo=ZoneInfo('Europe/Paris'))
        courriels = film.courriels_fictifs(t)
        self.assertGreaterEqual(len(courriels), 8)
        sujets = []
        for brut, _lu, quand in courriels:
            m = BytesParser(policy=policy.default).parsebytes(brut)
            sujets.append(str(m['Subject']))
            for _nom, adresse in getaddresses([str(m[h]) for h in ('From', 'To', 'Reply-To') if m[h]]):
                self.assertTrue(adresse.endswith('example.test'), adresse)
            self.assertLessEqual(quand, t)
            self.assertIsNone(re.search(r'\b(?:\d{2} ){4}\d{2}\b|FR\d{2}', m.get_body(('plain',)).get_content()))
        self.assertTrue(any('RPVA' in s for s in sujets) and any('coordonnées bancaires' in s for s in sujets))
        jour = film.date_audience(t)
        self.assertEqual(jour.weekday(), 1)
        self.assertGreaterEqual((jour - t.date()).days, 21)
        self.assertEqual(film.date_longue(datetime(2026, 12, 8).date()), 'mardi 8 décembre 2026')

    def test_V7_noms_et_domaines_fictifs_dans_les_fichiers_du_lot(self):
        textes = [p.read_text(encoding='utf-8') for p in sorted(DOSSIER.iterdir()) if p.suffix in ('.py', '.json', '.txt', '.js', '.ps1')]
        textes.append((ROOT / 'scripts' / 'video-demo.py').read_text(encoding='utf-8'))
        tout = '\n'.join(textes)
        for adresse in re.findall(r'[\w.+-]+@([\w.-]+)', tout):
            self.assertTrue(adresse.endswith('example.test'), adresse)
        majuscules = set(re.findall(r'\b(?:M\.|Me|Mme) ([A-ZÉ]{3,})\b', tout))
        self.assertTrue(majuscules <= NOMS_FICTIFS, majuscules - NOMS_FICTIFS)
        self.assertIsNone(re.search(r'\b(?:10|172|192)\.\d+\.\d+\.\d+\b', tout))    # jamais d'adresse IP privée réelle


class Notice(unittest.TestCase):
    def test_V8_lisezmoi_wordpress_et_serveur_du_cabinet(self):
        t = (DOSSIER / 'LISEZMOI.txt').read_text(encoding='utf-8')
        for attendu in ('bloc « Vidéo »', 'Lecture automatique', 'Muet', 'Pistes de texte', '{NOM}.vtt', 'affiche.png', 'playsinline',
                        'serveur du cabinet', 'text/vtt', 'Range', 'fictives'):
            self.assertIn(attendu, t)
        with tempfile.TemporaryDirectory() as d:
            cli = charger_cli()
            cli.ecrire_lisezmoi(Path(d))
            ecrit = (Path(d) / 'LISEZMOI.txt').read_text(encoding='utf-8')
            self.assertIn('axiorhub-demo-90s.mp4', ecrit)
            self.assertNotIn('{NOM}', ecrit)
            self.assertNotIn('{DUREE}', ecrit)
        self.assertIn('{NOM}.en.mp4', t)                  # versions anglaise, allemande, espagnole


class Langues(unittest.TestCase):
    TERMES = {'en': ('matter', 'review queue'), 'de': ('Akte', 'Mandant'), 'es': ('expediente',)}
    SERVICES = ('RPVA', 'CE-File', 'beA', 'LexNET', 'e-barreau')

    def test_V9_textes_de_la_voix_par_langue(self):
        scen = tx.scenario()['plans']
        for code in ('en', 'de', 'es'):
            with self.subTest(langue=code):
                plans = tx.lire_voix(code=code)
                self.assertEqual(tx.chemin_voix(code).name, 'voix.%s.txt' % code)
                self.assertEqual(tx.controler(plans, scen, tx.langue(code)['mots']), [])
                tout = ' '.join(p['texte'] for p in plans)
                for p in plans:
                    self.assertEqual(len(tx.phrases(p['texte'])), 1, (code, p['id']))
                    self.assertIsNone(re.search(r'\d', p['texte']), (code, p['id']))
                self.assertTrue(plans[-1]['texte'].endswith('axiorhub.com.'), code)
                for terme in self.TERMES[code]:
                    self.assertIn(terme, tout)
                for service in self.SERVICES:                 # message de la juridiction : formulation générique, aucun service nommé
                    self.assertNotIn(service, tout)
                self.assertIsNone(re.search(r'(?i)guarante|garantiert|garantiza|100 ?%|always|immer|siempre', tout))
        self.assertEqual(tx.chemin_voix('fr').name, 'voix.txt')
        with self.assertRaises(tx.ErreurTexte):
            tx.langue('it')

    def test_V9_legendes_fin_et_cibles_traduites(self):
        for code in ('en', 'de', 'es'):
            for plan in tx.scenario()['plans']:
                p = tx.plan_en(plan, code)
                if plan['nom'] == 'fin':
                    self.assertEqual(p['fin']['adresse'], 'axiorhub.com')
                    self.assertNotEqual(p['fin']['devise'], plan['fin']['devise'])
                else:
                    self.assertNotEqual(p['legende'], plan['legende'], (code, plan['nom']))
                    self.assertLessEqual(len(p['legende']), 55, (code, plan['nom']))   # tient dans le bandeau de légende
            dossier = tx.plan_en(next(x for x in tx.scenario()['plans'] if x['nom'] == 'dossier'), code)
            self.assertEqual(len(dossier['cibles']['survol']), 3)
        fr = tx.plan_en(tx.scenario()['plans'][0], 'fr')
        self.assertEqual(fr, tx.scenario()['plans'][0])          # français : scénario inchangé

    def test_V9_noms_des_fichiers_par_langue(self):
        cli = charger_cli()
        s = Path('sortie')
        fr, en = cli.fichiers(s, 'fr'), cli.fichiers(s, 'en')
        self.assertEqual((fr['mp4'].name, fr['vtt'].name, fr['affiche'], fr['minutage'].name, fr['voix'].name, fr['brut'].name),
                         ('axiorhub-demo-90s.mp4', 'axiorhub-demo-90s.vtt', 'affiche.png', 'minutage.json', 'voix', 'axiorhub-demo-brut.webm'))
        self.assertEqual((en['mp4'].name, en['srt'].name, en['affiche'], en['minutage'].name, en['voix'].name, en['brut'].name),
                         ('axiorhub-demo-90s.en.mp4', 'axiorhub-demo-90s.en.srt', 'affiche.en.png', 'minutage.en.json', 'voix-en',
                          'axiorhub-demo-brut.en.webm'))
        liste = mt.commandes('ffmpeg', 'b.webm', None, 0, 10, 'sortie', en['nom'], tx.reglages(), affiche=en['affiche'])
        self.assertTrue(liste[0][1][-1].endswith('axiorhub-demo-90s.en.mp4'))
        self.assertTrue(liste[2][1][-1].endswith('affiche.en.png'))

    def test_V9_moteurs_par_langue(self):
        self.assertEqual([tx.langue(c)['voix']['moteur'] for c in ('fr', 'en', 'de', 'es')], ['kokoro', 'kokoro', 'piper', 'kokoro'])
        self.assertEqual([tx.langue(c)['voix'].get('voix') for c in ('fr', 'en', 'es')], ['ff_siwis', 'af_heart', 'ef_dora'])
        self.assertEqual([tx.langue(c)['voix'].get('lang_code') for c in ('fr', 'en', 'es')], ['f', 'a', 'e'])
        self.assertEqual(tx.langue('en')['voix']['repli'], [])      # jamais une autre voix que celle de l'avocat sans --moteur
        cmd = vx.commande_piper('piper.exe', 'de.onnx', vitesse=1.25)
        self.assertEqual(cmd, ['piper.exe', '-m', 'de.onnx', '--output_raw', '-q', '--length_scale', '0.800'])
        self.assertNotIn('-f', cmd)        # son brut sur la sortie standard, WAV écrit par voix.py (format maîtrisé)
        with tempfile.TemporaryDirectory() as d:
            env = {'PIPER': str(Path(d) / 'piper.exe'), 'PIPER_VOIX': d}
            self.assertFalse(vx.piper_pret(None, env, 'de'))
            (Path(d) / 'piper.exe').write_bytes(b'x')
            (Path(d) / 'de_DE-thorsten-high.onnx').write_bytes(b'x')
            self.assertFalse(vx.piper_pret(None, env, 'de'))         # configuration .onnx.json absente
            (Path(d) / 'de_DE-thorsten-high.onnx.json').write_text('{}', encoding='utf-8')
            self.assertTrue(vx.piper_pret(None, env, 'de'))
        src = (DOSSIER / 'voix.py').read_text(encoding='utf-8')
        self.assertIn("input=t['texte'].encode('utf-8')", src)     # texte par l'entrée standard, jamais dans la ligne de commande

    def test_V9_cibles_presentes_dans_les_donnees_de_demo(self):
        # cibles relevées dans les données du lot M : chaque objet visé existe dans la boîte de la langue, chaque mot à corriger dans le
        # projet de réponse filmé, et le dossier filmé est le premier dossier de la langue
        donnees = ROOT / 'agent' / 'demo56107_donnees'
        for code, prefixe in (('en', 'MAT'), ('de', 'AKT'), ('es', 'EXP')):
            if not (donnees / code).is_dir():
                self.skipTest('données de démo du lot M absentes')
            with self.subTest(langue=code):
                objets = []
                for f in sorted((donnees / code / 'courriels').iterdir()):
                    m = re.search(r'^Subject: (.*)$', f.read_text(encoding='utf-8'), re.M)
                    objets.append(m.group(1) if m else '')
                cab = json.loads((donnees / code / 'cabinet.json').read_text(encoding='utf-8'))
                plans = {p['nom']: tx.plan_en(p, code) for p in tx.scenario()['plans']}

                def present(variantes):
                    from video56107 import film
                    return any(all(any(mot.lower() in o.lower() for o in objets) for mot in v) for v in film.alternatives(variantes))
                for nom in ('courriel', 'fraude', 'rpva'):
                    self.assertTrue(present(plans[nom]['cibles']['courriel']), (code, nom))
                for variantes in plans['resume']['cibles']['courriels']:
                    self.assertTrue(present(variantes), (code, variantes))
                mid = re.search(r'id=([^&]+)', plans['dossier']['page']).group(1)
                self.assertEqual(mid, cab['dossiers'][0]['id'])
                self.assertTrue(mid.startswith(prefixe + '-'))
                projet = next(p for p in cab['a_relire'] if p['dossier'] == mid)
                ancien, nouveau = plans['reponse']['cibles']['mot'][0]
                self.assertRegex(projet['corps'], r'(?<!\w)%s(?!\w)' % re.escape(ancien))
                self.assertNotEqual(ancien, nouveau)

    def test_V9_dossier_filme_lu_sur_le_bandeau(self):
        from video56107 import film

        class Page:
            def __init__(self, valeur):
                self.valeur = valeur

            def get_attribute(self, selecteur, nom, timeout=0):
                if self.valeur is None:
                    raise TimeoutError('pas de bandeau')
                return self.valeur
        pl = types.SimpleNamespace(p=Page('AKT-001'), langue='de')
        self.assertEqual(film.dossier_filme(pl), 'AKT-001')            # repère data-demo107-dossier du lot M
        pl = types.SimpleNamespace(p=Page(None), langue='es')
        self.assertEqual(film.dossier_filme(pl), 'EXP-001')            # sinon : adresse du plan dans la langue
        pl = types.SimpleNamespace(p=Page(None), langue='fr')
        self.assertEqual(film.dossier_filme(pl), 'DOS-001')

    def test_V9_affiche_allegee_pour_les_readme(self):
        cli = charger_cli()
        self.assertEqual(cli.fichiers(Path('s'), 'fr')['affiche_readme'].name, 'affiche-readme.jpg')
        self.assertEqual(cli.fichiers(Path('s'), 'de')['affiche_readme'].name, 'affiche-readme.de.jpg')
        cmd = mt.commande_affiche_allegee('ffmpeg', 'a.png', 'a.jpg', 5)
        self.assertIn('scale=1280:-2:flags=lanczos', cmd)
        self.assertEqual(cmd[cmd.index('-q:v') + 1], '5')
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d) / 'affiche-readme.jpg'
            essais = []

            def executer(argv, **k):     # faux ffmpeg : 400 Ko aux deux meilleures qualités, puis 200 Ko
                q = int(argv[argv.index('-q:v') + 1])
                essais.append(q)
                jpg.write_bytes(b'x' * (400 * 1024 if q < 5 else 200 * 1024))
                return FauxResultat(0)
            self.assertEqual(mt.affiche_allegee('ffmpeg', 'a.png', jpg, executer=executer), 200 * 1024)
            self.assertEqual(essais, [3, 4, 5])                     # meilleure qualité sous 250 Ko

            def trop_lourd(argv, **k):
                jpg.write_bytes(b'x' * 300 * 1024)
                return FauxResultat(0)
            with self.assertRaises(RuntimeError):                    # jamais au-dessus de 250 Ko sans le dire
                mt.affiche_allegee('ffmpeg', 'a.png', jpg, executer=trop_lourd)

    def test_V9_navigateur_dans_la_langue_de_la_video(self):
        from video56107 import film
        lancement, contexte = film.options_langue('de-DE')
        self.assertEqual(lancement, {'args': ['--lang=de-DE']})              # champs de date natifs au format allemand
        self.assertEqual(contexte, {'locale': 'de-DE', 'extra_http_headers': {'Accept-Language': 'de-DE,de;q=0.9'}})
        self.assertEqual([tx.langue(c)['locale'] for c in ('fr', 'en', 'de', 'es')], ['fr-FR', 'en-GB', 'de-DE', 'es-ES'])
        src = Path(film.__file__).read_text(encoding='utf-8')
        self.assertIn("lancer_navigateur(pw, regl, voir, conf['locale'])", src)
        self.assertIn("**options_langue(conf['locale'])[1]", src)

    def test_V9_anglais_jamais_de_telechargement_spacy(self):
        src = (DOSSIER / 'synthese-kokoro.py').read_text(encoding='utf-8')
        self.assertIn('spacy.cli.download = refuser', src)
        self.assertIn("spacy.util.is_package('en_core_web_sm')", src)
        self.assertLess(src.index('spacy.cli.download = refuser'), src.index('from kokoro import'))

    def test_V9_langue_refusee_sans_donnees_de_demo(self):
        from unittest.mock import patch
        import agent
        from video56107 import film
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(film.LangueIndisponible):
                film.Source(d, 'banc', 'de')                            # banc : données françaises seulement

            def creer(dossier_cible, graine=None, origine='', prefixe=''):   # mode Démo sans langue
                raise AssertionError('creer ne doit pas être appelé')
            faux = types.SimpleNamespace(creer=creer, commandes=lambda *a: {}, jeton_de_session=lambda *a: '')
            with patch.dict(sys.modules, {'agent.demo56107': faux}), patch.object(agent, 'demo56107', faux, create=True):
                with self.assertRaises(film.LangueIndisponible):
                    film.Source(d, 'auto', 'es')                        # jamais de repli sur le banc français


if __name__ == '__main__':
    unittest.main()
