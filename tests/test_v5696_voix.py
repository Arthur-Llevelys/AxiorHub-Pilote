"""5.6.96, lot voix (V01 à V07) : file de lecture sans troncature, états fiables, interruption par choix, diagnostic, profil
« Français posé » (voix par défaut décidée par le cabinet le 9 novembre 2026), avatar 2D facultatif, recette Node.

Données fictives, aucun réseau, aucun modèle d'IA, aucun moteur vocal réel. Ce que ces essais ne prouvent pas : le son réellement
entendu dans un navigateur (Edge, Firefox, casque et haut-parleur, 15, 45 et 120 secondes) ; il relève d'une écoute humaine, déclarée
non faite. La simulation Node (``tests/lecture5696.cjs``) exerce le vrai lecteur ``v5694-voix.js`` et le vrai worker ``v5694-brindille.js``
avec de faux Worker, AudioContext, Audio et HTTP ; le moteur Brindille WASM n'y tourne que sur demande (AXIORHUB_ESSAI_BRINDILLE_NODE=1).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from agent import assistant567, avatar5696, voice568, voix5694, voix_web5694
from agent.common import Stop
import test_v5694_voix as socle

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
HERE = Path(__file__).resolve().parent
COMPTE = 'claire@example.test'
AUTRE = 'camille@example.test'
PHRASE = 'Me DURAND plaide pour la SARL ZORVAL devant le tribunal de Valbrune le 12/03/2026, RG 24/01234, au titre de l’art. 700 CPC. '
POSEE = {'pitch': -2, 'intonation': 0.9, 'treble': -2, 'breath': -1}
NEUTRE = {'pitch': 0, 'intonation': 1, 'treble': 0, 'breath': 0}


def texte_long(n):
    """Texte fictif d'au moins ``n`` caractères, terminé par une phrase repère."""
    return (PHRASE * (n // len(PHRASE) + 1))[:n].rstrip() + ' Dernière phrase repère du texte.'


def segments(out):
    return [m for p in out['paragraphs'] for m in p]


class Bureau(socle.Bureau):
    """Bureau fictif : base locale, aucun service réel (fixtures de la 5.6.94)."""


class Application(socle.Application):
    """Pile web complète avec un compte administrateur fictif (fixtures de la 5.6.94)."""

    def page(self, chemin='/aujourdhui', requete=''):
        r = self.requete(chemin, requete=requete)
        self.assertTrue(r['status'].startswith('200'), r['status'])
        return r['body'].decode('utf-8')

    def profil(self):
        return json.loads(self.requete('/api440/m567/profile')['body'])


# ================================================================ V01 : file de lecture unique, jamais de troncature silencieuse
class V01File(Bureau):
    def test_V01_neuf_mille_caracteres_prepares_en_entier(self):
        texte = texte_long(9000)
        out = voix5694.preparer(self.desk, texte, COMPTE)
        morceaux = segments(out)
        self.assertGreater(len(morceaux), 30)
        self.assertTrue(all(0 < len(m) <= voix5694.PLAFOND_SEGMENT + 1 for m in morceaux))         # sous le plafond du moteur (2 000)
        self.assertTrue(morceaux[-1].endswith('Dernière phrase repère du texte.'))                    # la fin est dans la file
        self.assertEqual(out['segments'], len(morceaux))
        self.assertIsNone(out['limite'])                                                            # aucun plafond atteint
        self.assertEqual(out['characters'], len(texte))

    def test_V01_plafond_explicite_annonce_jamais_silencieux(self):
        texte = texte_long(voix5694.PLAFOND_LECTURE + 2000)
        out = voix5694.preparer(self.desk, texte, COMPTE)
        self.assertEqual((out['limite']['plafond'], out['limite']['caracteres']), (voix5694.PLAFOND_LECTURE, len(texte)))
        self.assertLessEqual(out['limite']['lus'], voix5694.PLAFOND_LECTURE)
        self.assertNotIn('Dernière phrase repère', ' '.join(segments(out)))
        self.desk.c['speech568'] = {'provider': 'elevenlabs'}                                         # voix externe : plafond plus bas, annoncé
        out = voix5694.preparer(self.desk, texte_long(9000), COMPTE)
        self.assertEqual(out['limite']['plafond'], voix5694.PLAFOND_EXTERNE)
        self.assertLessEqual(len(' '.join(segments(out))), voix5694.PLAFOND_EXTERNE)

    def test_V01_texte_vide_refuse_sans_audit(self):
        for texte in ('', '   '):
            with self.assertRaisesRegex(Stop, 'texte_lecture_vide'):
                voix5694.preparer(self.desk, texte, COMPTE)
        self.assertEqual(self.audits(), [])


class V01Route(Application):
    def test_V01_la_route_ne_tronque_plus_a_7000(self):
        texte = texte_long(9000)
        r = self.requete('/api440/m5689/voix/preparer', 'POST', {'text': texte, 'matter': 'DOS-001'})
        self.assertTrue(r['status'].startswith('200'), r['body'][:300])
        out = json.loads(r['body'])
        self.assertTrue(out['paragraphs'][-1][-1].endswith('Dernière phrase repère du texte.'))
        self.assertEqual(out['characters'], len(texte))
        self.assertIsNone(out['limite'])


# ================================================================ V03 : interruption par choix du compte
class V03Interruption(Bureau):
    def test_V03_interrompre_en_parlant_desactive_par_defaut_et_par_compte(self):
        self.assertIs(assistant567.profile(self.desk, COMPTE)['interruption_vocale'], False)
        self.assertIs(voix5694.etat(self.desk, COMPTE)['interruption_vocale'], False)
        assistant567.save_profile(self.desk, {'interruption_vocale': True}, COMPTE)
        self.assertIs(voix5694.etat(self.desk, COMPTE)['interruption_vocale'], True)
        self.assertIs(assistant567.profile(self.desk, AUTRE)['interruption_vocale'], False)          # réglage personnel
        with self.assertRaises(Stop):
            assistant567.save_profile(self.desk, {'interruption_vocale': 'oui'}, COMPTE)
        self.assertIs(assistant567.profile(self.desk, COMPTE)['interruption_vocale'], True)


# ================================================================ V04 : repli du serveur remonté à l'écran
class V04Diagnostic(Bureau):
    def test_V04_repli_espeak_remonte_avec_sa_cause_sans_le_texte(self):
        self.desk.c['speech568'] = {'provider': 'kokoro', 'url': 'http://127.0.0.1:8880'}
        self.assertIsNone(voix5694.etat(self.desk, COMPTE)['last_fallback'])
        with patch('agent.voice568.synthesize', side_effect=Stop('service_vocal_injoignable')), \
                patch('agent.assistant567.speech', return_value=socle.wav_fictif()):
            voice568.speech(self.desk, 'Texte fictif pour Me DURAND.', COMPTE)
        repli = voix5694.etat(self.desk, COMPTE)['last_fallback']
        self.assertEqual((repli['provider'], repli['requested'], repli['reason']), ('espeak', 'kokoro', 'service_vocal_injoignable'))
        self.assertTrue(repli['at']); self.assertTrue(repli['message'])
        self.assertNotIn('DURAND', json.dumps(repli))
        self.assertIsNone(voix5694.etat(self.desk, AUTRE)['last_fallback'])                         # par compte


class V04Page(Application):
    def test_V04_page_voix_affiche_la_voix_de_secours_utilisee(self):
        section = lambda: re.search(r'<section class="v94-voix".*?</section>', self.page('/parametres', 'rubrique=voix'), re.S).group(0)
        self.assertNotIn('Voix de secours utilisée', section())
        from agent.desk import Desk
        desk = Desk(self.f.c)
        try:   # compte de la requête authentifiée de ce banc (administrateur fictif, sans en-tête de compte : « cabinet »)
            desk.setting('voice568:last_fallback:cabinet', {'at': '2026-11-09T10:00:00+00:00', 'reason': 'service_vocal_injoignable',
                                                            'provider': 'espeak', 'requested': 'kokoro'})
        finally:
            desk.db.close()
        self.assertEqual(json.loads(self.requete('/api440/m5689/voix/etat')['body'])['last_fallback']['requested'], 'kokoro')
        self.assertRegex(section(), r'Voix de secours utilisée \(eSpeak NG\)[^<]*[Ss]ervice vocal injoignable[^<]*voix demandée : Kokoro')


# ================================================================ V05 : profil « Français posé » (défaut du cabinet), liste fermée
class V05Profil(Bureau):
    def test_V05_francais_pose_par_defaut_pour_un_compte_qui_n_a_jamais_choisi(self):
        self.assertEqual(voix5694.PROFIL_DEFAUT, 'posee')
        self.assertEqual(set(voix5694.PROFILS), {'posee', 'originale'})
        self.assertEqual(assistant567.profile(self.desk, COMPTE)['voix_brindille'], 'posee')        # installation neuve
        out = voix5694.preparer(self.desk, 'Texte fictif.', COMPTE)
        self.assertEqual((out['profil'], out['voix']), ('posee', POSEE))
        # compte existant (profil enregistré avant la 5.6.96, sans choix de profil) : défaut posé
        self.desk.setting('assistant567:profile:' + AUTRE, {**assistant567.DEFAULTS, 'speech_rate': 1.2})
        self.assertEqual(voix5694.preparer(self.desk, 'Texte fictif.', AUTRE)['profil'], 'posee')
        # enregistrer une autre préférence ne fige pas le profil : la clé n'est pas écrite tant que rien n'est choisi
        assistant567.save_profile(self.desk, {'speech_rate': 1.3}, AUTRE)
        self.assertNotIn('voix_brindille', self.desk.settings('assistant567:profile:' + AUTRE))
        self.assertEqual(assistant567.profile(self.desk, AUTRE)['voix_brindille'], 'posee')

    def test_V05_choix_explicite_conserve(self):
        assistant567.save_profile(self.desk, {'voix_brindille': 'originale'}, COMPTE)
        assistant567.save_profile(self.desk, {'speech_rate': 1.1, 'avatar': 'aucun'}, COMPTE)       # autre réglage ensuite : choix conservé
        self.assertEqual(self.desk.settings('assistant567:profile:' + COMPTE)['voix_brindille'], 'originale')
        out = voix5694.preparer(self.desk, 'Texte fictif.', COMPTE)
        self.assertEqual((out['profil'], out['voix']), ('originale', NEUTRE))                        # valeurs neutres réappliquées
        with patch.object(voix5694, 'PROFIL_DEFAUT', 'originale'):                                   # un défaut futur ne change pas un choix
            self.assertEqual(assistant567.profile(self.desk, COMPTE)['voix_brindille'], 'originale')
        self.assertEqual(assistant567.profile(self.desk, AUTRE)['voix_brindille'], 'posee')

    def test_V05_profil_autorise_seulement(self):
        for valeur in ('xyz', '', 'POSEE'):
            with self.assertRaisesRegex(Stop, 'profil_voix_invalide'):
                voix5694.preparer(self.desk, 'Texte fictif.', COMPTE, profil=valeur)
            with self.assertRaises(Stop):
                assistant567.save_profile(self.desk, {'voix_brindille': valeur}, COMPTE)
        self.desk.setting('assistant567:profile:' + COMPTE, {'voix_brindille': 'inconnu'})           # valeur retirée : défaut, jamais transmise
        self.assertEqual(voix5694.preparer(self.desk, 'Texte fictif.', COMPTE)['profil'], 'posee')
        self.assertEqual(self.audits()[-1]['profil'], 'posee')

    def test_V05_essai_d_ecoute_sans_enregistrer_et_sans_synthese_serveur(self):
        with patch('agent.assistant567.speech') as espeak, patch('agent.voice568.HTTP') as http, patch('agent.common.HTTP') as http2:
            essai = voix5694.preparer(self.desk, 'Texte fictif.', COMPTE, profil='originale')
        espeak.assert_not_called(); http.assert_not_called(); http2.assert_not_called()
        self.assertEqual((essai['profil'], essai['voix'], essai['browser']), ('originale', NEUTRE, True))
        self.assertIsNone(self.desk.settings('assistant567:profile:' + COMPTE))                       # rien d'enregistré
        self.assertEqual(set(voix5694.FOURNISSEURS), {'brindille', 'kokoro', 'espeak', 'chatterbox', 'elevenlabs'})   # aucune voix ajoutée
        self.assertFalse([p for p in (STATIC / 'brindille' / 'model').iterdir() if p.name not in {
            'tts.alk', 'lex.alk', 'fr.dict.bin', 'tts.params.bin', 'tts.json'}])                     # aucun second modèle


class V05Route(Application):
    def test_V05_profil_inconnu_refuse_par_la_route(self):
        r = self.requete('/api440/m5689/voix/preparer', 'POST', {'text': 'Texte fictif.', 'profil': 'xyz'})
        self.assertTrue(r['status'].startswith('400'), r['status'])
        self.assertEqual(json.loads(r['body'])['error'], 'profil_voix_invalide')
        r = self.requete('/api440/m5689/voix/preparer', 'POST', {'text': 'Texte fictif.', 'profil': 'originale'})
        self.assertEqual(json.loads(r['body'])['voix'], NEUTRE)
        r = self.requete('/api440/m5689/voix/preparer', 'POST', {'text': 'Texte fictif.'})
        self.assertEqual(json.loads(r['body'])['voix'], POSEE)                                       # défaut du compte

    def test_V05_page_voix_propose_le_profil_hors_du_formulaire_des_preferences(self):
        page = self.page('/parametres', 'rubrique=voix')
        section = re.search(r'<section class="v94-voix".*?</section>', page, re.S).group(0)
        choix = re.search(r'<select name="voix_brindille"[^>]*>(.*?)</select>', section, re.S).group(1)
        self.assertEqual(re.findall(r'value="([a-z_]+)"', choix), ['posee', 'originale'])
        self.assertRegex(choix, r'value="posee" selected')
        formulaire = re.search(r'<form[^>]*data-api="m567/profile".*?</form>', section, re.S).group(0)
        self.assertNotIn('voix_brindille', formulaire)                       # enregistrer la vitesse ne fige pas le profil par défaut
        self.assertRegex(formulaire, r'name="interruption_vocale"(?! checked)')
        r = self.requete('/api440/m567/profile', 'POST', {'voix_brindille': 'originale'})            # « Définir par défaut »
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(self.profil()['voix_brindille'], 'originale')
        choix = re.search(r'<select name="voix_brindille"[^>]*>(.*?)</select>', self.page('/parametres', 'rubrique=voix'), re.S).group(1)
        self.assertRegex(choix, r'value="originale" selected')
        page = self.page('/parametres', 'rubrique=voix')
        for cle in ('voix_brindille', 'interruption_vocale', 'avatar', 'speech_rate'):
            self.assertEqual(page.count('name="%s"' % cle), 1, cle)


# ================================================================ V06 : avatar 2D facultatif
class V06Avatar(Bureau):
    def test_V06_valeurs_d_avatar_et_defaut_sans_portrait(self):
        self.assertEqual(assistant567.profile(self.desk, COMPTE)['avatar'], 'icone')                  # valeur historique conservée
        self.assertEqual(voix5694.etat(self.desk, COMPTE)['avatar'], 'aucun')
        self.assertFalse(avatar5696.illustre(self.desk.c, COMPTE))
        self.assertEqual(avatar5696.script('/p', self.desk.c, COMPTE), '')
        for valeur in ('aucun', 'portrait_a', 'portrait_b', 'icone'):
            assistant567.save_profile(self.desk, {'avatar': valeur}, COMPTE)
            self.assertEqual(avatar5696.illustre(self.desk.c, COMPTE), valeur in voix5694.PORTRAITS)
        with self.assertRaises(Stop):
            assistant567.save_profile(self.desk, {'avatar': 'photo'}, COMPTE)
        self.assertFalse(avatar5696.illustre(self.desk.c, AUTRE))
        self.assertFalse(avatar5696.illustre({'state_dir': str(Path(self.desk.c['state_dir']) / 'absent')}, COMPTE))   # base absente : rien
        self.assertIn('/static/v5696-avatar.js', avatar5696.script('/p', self.desk.c, AUTRE, toujours=True))


class V06Page(Application):
    def test_V06_aucun_script_avatar_quand_l_option_est_a_aucun(self):
        self.assertNotIn(self.profil()['avatar'], voix5694.PORTRAITS)
        page = self.page()
        self.assertNotIn('v5696-avatar', page)                                                       # module non déclaré : aucune requête
        self.assertRegex(page, r'<script defer src="[^"]*/static/v5694-voix\.js\?v=[^"]+"></script>')
        with patch.object(avatar5696, 'illustre', return_value=True):                                # mutation : la garde contournée, il apparaît
            self.assertIn('v5696-avatar.js', self.page())
        r = self.requete('/api440/m567/profile', 'POST', {'avatar': 'portrait_a'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        page = self.page()
        self.assertRegex(page, r'<script defer src="[^"]*/static/v5696-avatar\.js\?v=[^"]+"></script>')
        self.assertEqual(page.count('v5696-avatar.js'), 1)
        self.assertEqual(self.page('/parametres', 'rubrique=voix').count('v5696-avatar.js'), 1)      # pas de doublon sur la page Voix
        self.requete('/api440/m567/profile', 'POST', {'avatar': 'aucun'})
        self.assertNotIn('v5696-avatar.js', self.page())
        page = self.page('/parametres', 'rubrique=voix')                                             # aperçu sur la page de réglage
        self.assertEqual(page.count('v5696-avatar.js'), 1)
        choix = re.search(r'<select name="avatar"[^>]*>(.*?)</select>', page, re.S).group(1)
        self.assertEqual(re.findall(r'value="([a-z_]+)"', choix), ['aucun', 'portrait_a', 'portrait_b'])
        self.assertRegex(choix, r'value="aucun" selected')

    def test_V06_module_servi_sans_media_ni_adresse_externe(self):
        r = self.requete('/static/v5696-avatar.js', requete='v=9.9.99')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        self.assertEqual(r['headers']['Content-Type'], 'text/javascript; charset=utf-8')
        script = (STATIC / 'v5696-avatar.js').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'https?://(?!www\.w3\.org/2000/svg)|data:|\.png|\.jpe?g|\.webp|<img|getUserMedia|MediaRecorder|fetch\(|XMLHttpRequest|WebSocket', script), [])
        for cle in voix5694.PORTRAITS:
            self.assertIn(cle, script)


# ================================================================ V07 : recette Node (lecteur, worker, détecteur d'interruption)
class V07Node(unittest.TestCase):
    def test_V01_V02_V03_V05_V07_simulation_node_du_lecteur(self):
        node = shutil.which('node')
        if not node:
            raise unittest.SkipTest('Node absent : simulation du lecteur non exercée')
        env = {k: v for k, v in os.environ.items() if k != 'AXIORHUB_ESSAI_BRINDILLE_NODE'}
        out = subprocess.run([node, str(HERE / 'lecture5696.cjs'), str(ROOT)], capture_output=True, text=True, encoding='utf-8', timeout=300,
                             env={**env, 'NODE_NO_WARNINGS': '1'})
        self.assertEqual(out.returncode, 0, (out.stdout[-2000:], out.stderr[-3000:]))
        self.assertTrue(out.stdout.strip().splitlines()[-1].startswith('lecture5696 : ok'), out.stdout[-2000:])

    def test_V04_V07_scripts_sans_adresse_externe_ni_texte_journalise(self):
        for nom in ('v5694-voix.js', 'v5694-brindille.js', 'v568.js', 'v5696-avatar.js', 'v5694-voix-page.js'):
            texte = (STATIC / nom).read_text(encoding='utf-8')
            self.assertEqual(re.findall(r'https?://(?!www\.w3\.org/2000/svg)|//cdn|huggingface', texte), [], nom)
            self.assertNotRegex(texte, r'console\.(?:log|debug|info|warn)\([^)]*\b(?:text|texte|phrase|pcm|blob)\b', nom)


class V09Livraison(unittest.TestCase):
    def test_V06_declarations_statiques_et_feuille_de_style(self):
        from agent.web440 import STATIC as declares
        self.assertEqual(declares.get('/static/v5696-avatar.js'), 'text/javascript')
        self.assertIn('.v96-portrait', (STATIC / 'app520.css').read_text(encoding='utf-8'))          # feuille régénérée (scripts/build-css.py)

    def test_V05_V06_textes_dans_les_quatre_langues(self):
        from agent import i18n5631
        cles = [k for k in i18n5631.catalog('fr') if k.startswith(('voix5694.', 'js.5694.'))]
        for cle in ('voix5694.profil_posee', 'voix5694.avatar_portrait_a', 'voix5694.interruption', 'voix5694.secours_utilise', 'js.5694.segment_echec'):
            self.assertIn(cle, cles)
        for langue in ('en', 'de', 'es'):
            catalogue = i18n5631.catalog(langue)
            self.assertEqual([k for k in cles if not catalogue.get(k)], [], langue)


if __name__ == '__main__':
    unittest.main()
