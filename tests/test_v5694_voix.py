"""5.6.94, lot voix (V01 à V09) : Brindille dans le navigateur, abréviations juridiques lues, repli serveur, Alkove pour la dictée, page Voix.

Données fictives, aucun réseau, aucun modèle d'IA. Les services vocaux du serveur (eSpeak, Kokoro, ElevenLabs) sont remplacés par des faux.
Ce que ces essais ne prouvent pas : le son réellement entendu dans un navigateur (Web Worker, AudioContext, micro, Alkove en temps réel) ;
il relève du banc navigateur (lot parcours, P02h) ou d'une écoute humaine. L'essai du moteur Brindille sous Node (s'il est installé)
vérifie seulement que les fichiers embarqués produisent un signal non vide.
"""
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent import assistant567, config567, lecture5694 as lec, voice568, voix5694, voix_web5694
from agent.common import Stop, private_json
from agent.desk import Desk
import test_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
NBSP, FINE = chr(0xa0), chr(0x202f)
D = lec.developper


def wav_fictif():
    flux = io.BytesIO()
    with wave.open(flux, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b'\x01\x00' * 400)
    return flux.getvalue()


# ================================================================ V04 : lecture des abréviations (module serveur commun)
class V04Nombres(unittest.TestCase):
    def test_V04_nombres_en_toutes_lettres(self):
        attendus = {0: 'zéro', 1: 'un', 16: 'seize', 17: 'dix-sept', 21: 'vingt et un', 22: 'vingt-deux', 70: 'soixante-dix', 71: 'soixante et onze',
                    77: 'soixante-dix-sept', 80: 'quatre-vingts', 81: 'quatre-vingt-un', 91: 'quatre-vingt-onze', 99: 'quatre-vingt-dix-neuf',
                    100: 'cent', 101: 'cent un', 200: 'deux cents', 250: 'deux cent cinquante', 1000: 'mille', 1250: 'mille deux cent cinquante',
                    2000: 'deux mille', 80000: 'quatre-vingt mille', 200000: 'deux cent mille', 1000000: 'un million', 2000000: 'deux millions',
                    200000000: 'deux cents millions', 1000000000: 'un milliard'}
        for n, mots in attendus.items():
            with self.subTest(n=n):
                self.assertEqual(lec.nombre(n), mots)

    def test_V04_ordinaux(self):
        self.assertEqual(lec.ordinal(1), 'premier'); self.assertEqual(lec.ordinal(1, True), 'première')
        for n, mots in {2: 'deuxième', 3: 'troisième', 4: 'quatrième', 5: 'cinquième', 9: 'neuvième', 11: 'onzième', 21: 'vingt et unième',
                        80: 'quatre-vingtième', 100: 'centième', 1000: 'millième'}.items():
            self.assertEqual(lec.ordinal(n), mots)

    def test_V04_montants_en_euros(self):
        self.assertEqual(D('1 250,50 €'), 'mille deux cent cinquante euros cinquante')               # exemple de la feuille de route
        self.assertEqual(D('1' + NBSP + '250,50' + NBSP + '€'), 'mille deux cent cinquante euros cinquante')
        self.assertEqual(D('1' + FINE + '250,50 €'), 'mille deux cent cinquante euros cinquante')
        self.assertEqual(D('1.250,50 €'), 'mille deux cent cinquante euros cinquante')
        self.assertEqual(D('1 €'), 'un euro')
        self.assertEqual(D('12,5 €'), 'douze euros cinquante')
        self.assertEqual(D('0,50 €'), 'cinquante centimes')
        self.assertEqual(D('2 000 000 €'), "deux millions d'euros")
        self.assertEqual(D('3 000 euros'), 'trois mille euros')
        self.assertEqual(D('2,5 M€'), "deux virgule cinq millions d'euros")
        self.assertEqual(D('15 k€'), 'quinze mille euros')
        self.assertEqual(D('une provision de 1 500 € HT'), 'une provision de mille cinq cents euros hors taxes')   # 5.6.95 (L02)

    def test_V04_pourcentages_et_milliers(self):
        self.assertEqual(D('15 %'), 'quinze pour cent')
        self.assertEqual(D('3,5 %'), 'trois virgule cinq pour cent')
        self.assertEqual(D('2,05%'), 'deux virgule zéro cinq pour cent')
        self.assertEqual(D('les 3 000 salariés'), 'les trois mille salariés')

    def test_V04_dates_numeriques(self):
        self.assertEqual(D('12/03/2026'), 'douze mars deux mille vingt-six')                                            # exemple de la feuille de route
        self.assertEqual(D('le 01/02/2026'), 'le premier février deux mille vingt-six')
        self.assertEqual(D('12.03.26'), 'douze mars deux mille vingt-six')
        self.assertEqual(D('2026-03-12'), 'douze mars deux mille vingt-six')
        self.assertEqual(D('31/13/2026'), '31/13/2026')                                              # mois impossible : rien d'inventé

    def test_V04_ordinaux_dans_le_texte(self):
        self.assertEqual(D('le 1er janvier'), 'le premier janvier')
        self.assertEqual(D('la 1re chambre'), 'la première chambre')
        self.assertEqual(D('la 2e chambre'), 'la deuxième chambre')
        self.assertEqual(D('le 3ème alinéa'), 'le troisième alinéa')


class V04Juridique(unittest.TestCase):
    def test_V04_articles_alineas_et_codes(self):
        self.assertEqual(D('art. 1240 C. civ.'), 'article 1240 du Code civil')
        self.assertEqual(D('Art. 1er al. 2'), 'Article premier alinéa 2')
        self.assertEqual(D('art. L. 1234-5 C. trav.'), 'article L 1234 tiret 5 du Code du travail')
        self.assertEqual(D('art. 700 CPC'), 'article 700 du Code de procédure civile')
        self.assertEqual(D('art. 145 C. pr. civ.'), 'article 145 du Code de procédure civile')
        self.assertEqual(D('art. L. 110-1 C. com.'), 'article L 110 tiret 1 du Code de commerce')
        self.assertEqual(D('art. 121-3 C. pén.'), 'article 121 tiret 3 du Code pénal')
        self.assertEqual(D('art. 63 CPP'), 'article 63 du Code de procédure pénale')
        self.assertEqual(D('art. L. 212-1 C. consom.'), 'article L 212 tiret 1 du Code de la consommation')
        self.assertEqual(D('art. 39 CGI'), 'article 39 du Code général des impôts')
        self.assertEqual(D('selon le C. civ.'), 'selon le Code civil')
        self.assertEqual(D('art. 1240 du C. civ.'), 'article 1240 du Code civil')                    # « du » déjà présent : pas de doublon
        self.assertEqual(D('art. 1240 et s. C. civ.'), 'article 1240 et suivants Code civil')

    def test_V04_csp_selon_le_contexte(self):
        self.assertEqual(D('art. L. 1110-4 CSP'), 'article L 1110 tiret 4 du Code de la santé publique')
        self.assertEqual(D('au sens du CSP'), 'au sens du Code de la santé publique')
        self.assertEqual(D('une CSP élevée'), 'une cé esse pé élevée')                                # catégorie socioprofessionnelle : épelé

    def test_V04_juridictions(self):
        self.assertEqual(D('Cass. civ. 1re, 12/03/2026'), 'Cour de cassation, première chambre civile, douze mars deux mille vingt-six')
        self.assertEqual(D('Cass. 2e civ.'), 'Cour de cassation, deuxième chambre civile')
        self.assertEqual(D('Civ. 3e, 5 mai 2021'), 'Cour de cassation, troisième chambre civile, cinq mai deux mille vingt et un')
        self.assertEqual(D('Cass. com.'), 'Cour de cassation, chambre commerciale')
        self.assertEqual(D('Cass. soc.'), 'Cour de cassation, chambre sociale')
        self.assertEqual(D('Cass. crim.'), 'Cour de cassation, chambre criminelle')
        self.assertEqual(D('Soc., 3 mars 2021'), 'Cour de cassation, chambre sociale, trois mars deux mille vingt et un')
        self.assertEqual(D('Cass. ass. plén.'), 'Cour de cassation, assemblée plénière')
        self.assertEqual(D('CA Lyon'), "cour d'appel de Lyon")
        self.assertEqual(D('CA de Paris'), "cour d'appel de Paris")
        self.assertEqual(D("CA d'Angers"), "cour d'appel d'Angers")
        self.assertEqual(D('CA Aix-en-Provence'), "cour d'appel d'Aix-en-Provence")
        self.assertEqual(D('TJ Valbrune'), 'tribunal judiciaire de Valbrune')
        self.assertEqual(D('TJ Le Mans'), 'tribunal judiciaire du Mans')
        self.assertEqual(D('le TJ a statué'), 'le tribunal judiciaire a statué')
        self.assertEqual(D('le TC'), 'le tribunal de commerce')
        self.assertEqual(D('le JAF'), 'le juge aux affaires familiales')
        self.assertEqual(D('la CEDH'), "la Cour européenne des droits de l'homme")
        self.assertEqual(D('la CJUE'), "la Cour de justice de l'Union européenne")
        self.assertEqual(D('un CA de 2 M€'), "un CA de deux millions d'euros")                         # « CA » sans ville : chiffre d'affaires, intact

    def test_V04_ce_en_debut_de_phrase_et_en_capitales(self):
        self.assertEqual(D('CE, 3 mars 2021, n° 456789.'), "Conseil d'État, trois mars deux mille vingt et un, numéro 456789.")
        self.assertEqual(D('CE, sect., 3 mars 2021'), "Conseil d'État, section, trois mars deux mille vingt et un")
        self.assertEqual(D('Le CE a jugé'), "Le Conseil d'État a jugé")
        self.assertEqual(D('CE QUI EST JUGÉ'), 'CE QUI EST JUGÉ')                                     # démonstratif d'un titre en capitales
        self.assertEqual(D('CE JUGEMENT'), 'CE JUGEMENT')

    def test_V04_titres_devant_un_nom(self):
        self.assertEqual(D('Me DURAND'), 'Maître DURAND')
        self.assertEqual(D('Mes DURAND et MARTIN'), 'Maîtres DURAND et MARTIN')
        self.assertEqual(D('M. BERNARD'), 'Monsieur BERNARD')
        self.assertEqual(D('MM. BERNARD et KERVILAC'), 'Messieurs BERNARD et KERVILAC')
        self.assertEqual(D('Mme ORLANDEC'), 'Madame ORLANDEC')
        self.assertEqual(D('Mlle Martin'), 'Mademoiselle Martin')
        self.assertEqual(D('M. le Président'), 'Monsieur le Président')
        self.assertEqual(D('Mes chers confrères'), 'Mes chers confrères')                              # possessif, pas « Maîtres »

    def test_V04_m_en_fin_de_phrase_reste_tel_quel(self):
        self.assertEqual(D('Le lot est attribué à M.'), 'Le lot est attribué à M.')
        self.assertEqual(D('Parcelle section M. Le lendemain, la vente fut signée.'), 'Parcelle section M. Le lendemain, la vente fut signée.')
        self.assertEqual(D('Parcelle section M.\nMonsieur BERNARD signe.'), 'Parcelle section M.\nMonsieur BERNARD signe.')

    def test_V04_sigles_epeles_ou_lus_comme_un_mot(self):
        self.assertEqual(D('RG'), 'erre gé')
        self.assertEqual(D('SARL ZORVAL'), 'esse a erre elle ZORVAL')
        self.assertEqual(D('SAS'), 'esse a esse')
        self.assertEqual(D('SCI'), 'esse cé i')
        self.assertEqual(D('URSSAF'), 'Urssaf')
        self.assertEqual(D('RG 24/01234'), 'erre gé vingt-quatre barre mille deux cent trente-quatre')
        self.assertEqual(D('SIREN 000 010 017'), 'Siren zéro zéro zéro zéro un zéro zéro un sept')

    def test_V04_symboles_et_references(self):
        self.assertEqual(D('n° 22-12.345'), 'numéro vingt-deux, douze, trois cent quarante-cinq')     # pourvoi lu par groupe
        self.assertEqual(D('pourvoi 22-12.345'), 'pourvoi vingt-deux, douze, trois cent quarante-cinq')
        self.assertEqual(D('N° 12'), 'Numéro 12')
        self.assertEqual(D('§ 3'), 'paragraphe 3')
        self.assertEqual(D('DURAND c/ MARTIN'), 'DURAND contre MARTIN')
        self.assertEqual(D('p. 12'), 'page 12')
        self.assertEqual(D('cf. supra'), 'voir supra')

    def test_V04_url_adresse_et_marqueurs_intacts(self):
        for intact in ('https://www.example.test/art.1240/C.civ.?n=12/03/2026', 'www.example.test/RG/24-01234', 'contact.CA@example.test',
                       'Me.DURAND@example.test', '[PERSONNE_1]', '[SOCIETE_2]', '[ADRESSE_12]'):
            with self.subTest(intact=intact):
                self.assertIn(intact, D('Voir ' + intact + ' pour la suite.'))
        self.assertEqual(D('M. [PERSONNE_1] c/ [SOCIETE_2]'), 'Monsieur [PERSONNE_1] contre [SOCIETE_2]')
        self.assertEqual(D('Écrire à Me DURAND (durand@example.test) avant le 12/03/2026.'),
                         'Écrire à Maître DURAND (durand@example.test) avant le douze mars deux mille vingt-six.')

    def test_V04_texte_complet_et_relecture_idempotente(self):
        texte = ('Par arrêt du 12/03/2026 (Cass. civ. 1re, n° 22-12.345), la Cour a cassé l’arrêt de la CA Lyon. '
                 'Me DURAND, pour la SARL ZORVAL, réclame 1 250,50 € au titre de l’art. 700 CPC, soit 15 % de plus.')
        lu = D(texte)
        self.assertEqual(lu, 'Par arrêt du douze mars deux mille vingt-six (Cour de cassation, première chambre civile, numéro vingt-deux, douze, trois cent quarante-cinq), '
                             'la Cour a cassé l’arrêt de la cour d\'appel de Lyon. Maître DURAND, pour la esse a erre elle ZORVAL, réclame mille deux '
                             'cent cinquante euros cinquante au titre de l’article 700 du Code de procédure civile, soit quinze pour cent de plus.')
        # le serveur relit le texte déjà préparé (repli) : une seconde passe ne change rien
        self.assertEqual(D(lu), lu)
        self.assertEqual(D(''), '')
        self.assertEqual(D('Texte fictif.'), 'Texte fictif.')

    def test_V04_decoupage_au_fil_de_l_eau(self):
        texte = ' '.join('Phrase numéro %d du paragraphe.' % i for i in range(30)) + '\n\nSecond paragraphe court.'
        paragraphes = lec.phrases(texte)
        self.assertEqual(len(paragraphes), 2)
        self.assertTrue(all(len(m) <= 240 for p in paragraphes for m in p))
        self.assertLessEqual(len(paragraphes[0][0]), 40)                                              # la lecture commence vite
        self.assertEqual(' '.join(paragraphes[0]), re.sub(r'\s+', ' ', texte.split('\n\n')[0]))       # rien de perdu
        self.assertEqual(paragraphes[1], ['Second paragraphe court.'])
        tres_longue = 'mot ' * 200
        self.assertTrue(all(len(m) <= 240 for m in lec.phrases(tres_longue)[0]))
        self.assertEqual(lec.nettoyer('## Titre\n**gras** et `code` [lien](https://example.test)'), 'Titre\ngras et code lien')


# ================================================================ V01 : fichiers embarqués
class V01Fichiers(unittest.TestCase):
    def test_V01_empreintes_des_fichiers_embarques(self):
        for chemin, (_, empreinte) in voix5694.FICHIERS.items():
            with self.subTest(chemin=chemin):
                self.assertEqual(hashlib.sha256((STATIC / chemin).read_bytes()).hexdigest(), empreinte)
        self.assertEqual(voix5694.fichiers_presents(), [])
        for dossier in ('brindille', 'alkove'):
            self.assertTrue((STATIC / dossier / 'LICENSE').is_file()); self.assertTrue((STATIC / dossier / 'NOTICE').is_file())

    def test_V01_V07_modeles_alkove_aux_revisions_figees_par_le_paquet(self):
        manifeste = (STATIC / 'alkove' / 'chunk-FC4ZSMUX.js').read_text(encoding='utf-8')
        for cle, (nom, revision, taille, empreinte) in voix5694.ALKOVE_MODELES.items():
            bloc = re.search(cle + r':\{name:"' + re.escape(nom) + r'",file:"([^"]+)",bytes:(\d+),sha256:"([0-9a-f]{64})",revision:"([0-9a-f]{40})"\}', manifeste)
            self.assertIsNotNone(bloc, cle)
            self.assertEqual((bloc.group(2), bloc.group(3), bloc.group(4)), (str(taille), empreinte, revision))
            fichier = STATIC / 'alkove' / 'models' / bloc.group(1)
            self.assertEqual(fichier.stat().st_size, taille)
            self.assertEqual(hashlib.sha256(fichier.read_bytes()).hexdigest(), empreinte)
        self.assertEqual(json.loads((STATIC / 'alkove' / 'package.json').read_text(encoding='utf-8'))['version'], voix5694.ALKOVE_VERSION)
        moteur = re.search(r'name:"alkove-engine",file:"alkove-engine.wasm",bytes:(\d+),sha256:"([0-9a-f]{64})"', manifeste)
        self.assertEqual(moteur.group(2), voix5694.FICHIERS['alkove/alkove-engine.wasm'][1])

    def test_V01_declares_dans_static_avec_le_bon_type(self):
        from agent.web440 import STATIC as declares
        attendus = {'.wasm': 'application/wasm', '.alk': 'application/octet-stream', '.bin': 'application/octet-stream', '.json': 'application/json',
                    '.js': 'text/javascript'}
        for chemin in voix5694.FICHIERS:
            suffixe = Path(chemin).suffix
            self.assertEqual(declares['/static/' + chemin], attendus.get(suffixe, 'text/plain'), chemin)
        for nom in ('v5694-voix.js', 'v5694-brindille.js', 'v5694-voix-page.js', 'v5694-voix.css'):
            self.assertIn('/static/' + nom, declares)
            self.assertTrue((STATIC / nom).is_file())

    def test_V01_aucune_adresse_externe_dans_les_scripts_servis(self):
        # Les scripts du lot et le chargeur Brindille ne contiennent aucune adresse absolue : tout vient d'AxiorHub.
        for nom in ('v5694-voix.js', 'v5694-brindille.js', 'v5694-voix-page.js', 'brindille/tts.js'):
            texte = (STATIC / nom).read_text(encoding='utf-8')
            self.assertEqual(re.findall(r'https?://|//cdn|huggingface', texte), [], nom)


class Application(unittest.TestCase):
    """Toute la pile web (authentification, droits, CSRF, en-têtes) avec un compte administrateur fictif."""
    ORIGIN = 'https://cabinet.example.test'

    def setUp(self):
        from agent.web import App
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        sel = 'aa' * 16
        self.secret = 'password-test-only-long'
        private_json(self.auth, {'username': 'admin', 'salt': sel, 'hash': hashlib.scrypt(self.secret.encode(), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex(),
                                 'origin': self.ORIGIN, 'prefix': '/agent-courriel', 'csrf': 'test-csrf'})
        self.app = App(str(self.config), str(self.auth))

    def requete(self, chemin, methode='GET', corps=None, requete='', csrf='test-csrf'):
        donnees = json.dumps(corps or {}).encode()
        env = {'REQUEST_METHOD': methode, 'PATH_INFO': '/agent-courriel' + chemin, 'QUERY_STRING': requete, 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(donnees), 'CONTENT_LENGTH': str(len(donnees)),
               'CONTENT_TYPE': 'application/json', 'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.secret).encode()).decode(),
               'HTTP_ORIGIN': self.ORIGIN, 'HTTP_X_CSRF_TOKEN': csrf}
        sortie = {}

        def debut(statut, entetes):
            sortie.update(status=statut, headers=dict(entetes))
        sortie['body'] = b''.join(self.app(env, debut))
        return sortie

    def cfg(self):
        return json.loads(self.config.read_text(encoding='utf-8'))


class V01Service(Application):
    def test_V01_fichiers_servis_a_l_identique_avec_cache_long(self):
        for chemin in ('brindille/tts.wasm', 'brindille/model/tts.alk', 'brindille/model/fr.dict.bin', 'brindille/model/tts.json', 'brindille/tts.js',
                       'alkove/alkove-engine.wasm', 'alkove/models/alkove-cue-1-preview.alk'):
            with self.subTest(chemin=chemin):
                r = self.requete('/static/' + chemin, requete='v=9.9.99')
                self.assertTrue(r['status'].startswith('200'), r['status'])
                self.assertEqual(r['body'], (STATIC / chemin).read_bytes())
                attendu = voix5694.FICHIERS[chemin][0]
                self.assertEqual(r['headers']['Content-Type'], attendu + ('' if attendu in voix5694.BINAIRES else '; charset=utf-8'))
                self.assertEqual(r['headers']['Cache-Control'], 'private, max-age=31536000, immutable')
        sans_version = self.requete('/static/brindille/tts.wasm')
        self.assertEqual(sans_version['headers']['Cache-Control'], 'no-store')                       # sans empreinte : jamais de cache périmé

    def test_V01_seules_les_adresses_declarees_sont_servies(self):
        for chemin in ('/static/brindille/README.md', '/static/alkove/package.json', '/static/brindille/../web.py', '/static/alkove/cli.js'):
            with self.subTest(chemin=chemin):
                self.assertFalse(self.requete(chemin)['status'].startswith('200'))


# ================================================================ V02 : politique de sécurité du contenu
class V02CSP(Application):
    CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; "
           "media-src 'self' blob:; manifest-src 'self'; worker-src 'self'; form-action 'self'; frame-src 'self' https://ai.example.com; frame-ancestors 'none'; "
           "base-uri 'none'")   # frame-src : Open WebUI d'exemple, comme avant la 5.6.94

    def test_V02_en_tete_exact_des_pages_et_des_scripts(self):
        for chemin, requete in (('/aujourdhui', ''), ('/parametres', 'rubrique=voix'), ('/static/v5694-brindille.js', 'v=9.9.99')):
            with self.subTest(chemin=chemin):
                r = self.requete(chemin, requete=requete)
                self.assertTrue(r['status'].startswith('200'), r['status'])
                self.assertEqual(r['headers']['Content-Security-Policy'], self.CSP)

    def test_V02_jamais_unsafe_eval_ni_unsafe_inline_pour_les_scripts(self):
        for chemin in ('/aujourdhui', '/connexion-inexistante'):
            csp = self.requete(chemin)['headers'].get('Content-Security-Policy', '')
            script = re.search(r"script-src ([^;]*)", csp)
            if script:
                jetons = script.group(1).split()
                self.assertNotIn("'unsafe-eval'", jetons); self.assertNotIn("'unsafe-inline'", jetons)
        sans_auth = self.app({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel/aujourdhui', 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test',
                              'wsgi.url_scheme': 'https', 'wsgi.input': io.BytesIO(b'')}, lambda s, h: setattr(self, '_entetes', dict(h)))
        b''.join(sans_auth)
        self.assertIn("script-src 'self' 'wasm-unsafe-eval';", self._entetes['Content-Security-Policy'])   # en-tête de base (ligne 726)

    def test_V02_scripts_de_page_differes_sans_javascript_en_ligne(self):
        page = self.requete('/parametres', requete='rubrique=voix')['body'].decode('utf-8')
        for nom in ('v5694-voix.js', 'v5694-voix-page.js'):
            self.assertRegex(page, r'<script defer src="[^"]*/static/' + re.escape(nom) + r'\?v=[^"]+"></script>')
        for balise in re.findall(r'<script\b[^>]*>', page):
            self.assertTrue('defer' in balise or 'type="application/json"' in balise, balise)


# ================================================================ V03 : fournisseur « brindille »
class Bureau(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    def audits(self):
        return [json.loads(r[0]) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='synthese_vocale' ORDER BY id")]


class V03Fournisseur(Bureau):
    def test_V03_brindille_par_defaut_et_choix_du_cabinet_conserve(self):
        self.assertEqual(voix5694.fournisseur({}), 'brindille')
        self.assertEqual(voix5694.fournisseur({'speech568': {}}), 'brindille')
        self.assertEqual(voix5694.fournisseur({'speech568': {'provider': ''}}), 'brindille')
        for choisi in ('kokoro', 'espeak', 'chatterbox', 'elevenlabs'):
            self.assertEqual(voix5694.fournisseur({'speech568': {'provider': choisi}}), choisi)
        champ = config567.INDEX['speech568.provider']
        self.assertEqual(champ[3], 'brindille')                                                       # installation neuve
        self.assertEqual(config567._validate('voice_provider', 'brindille', 'speech568.provider'), 'brindille')
        with self.assertRaises(Stop):
            config567._validate('voice_provider', 'clonage', 'speech568.provider')
        bootstrap = (ROOT / 'docker' / 'bootstrap.py').read_text(encoding='utf-8')
        self.assertIn("'AXIORHUB_SPEECH_PROVIDER','brindille'", bootstrap)

    def test_V03_le_serveur_prepare_audite_et_ne_synthetise_rien(self):
        with patch('agent.assistant567.speech') as espeak, patch('agent.voice568.HTTP') as http, patch('agent.common.HTTP') as http2:
            texte = 'Me DURAND plaide l’art. 1240 C. civ. le 12/03/2026.'
            out = voix5694.preparer(self.desk, texte, 'avocat@example.test', 'DOS-001')
        espeak.assert_not_called(); http.assert_not_called(); http2.assert_not_called()
        self.assertTrue(out['browser']); self.assertEqual(out['provider'], 'brindille'); self.assertFalse(out['external'])
        self.assertIsNone(out['transmitted_text'])
        self.assertEqual(out['paragraphs'], [['Maître DURAND plaide l’article 1240 du Code civil le douze mars deux mille vingt-six.']])
        self.assertEqual(out['fallback'], 'espeak')
        self.assertFalse((Path(self.desk.c['state_dir']) / 'voice568-cache').exists())               # aucun fichier audio produit
        audit = self.audits()
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]['provider'], audit[0]['characters'], audit[0]['external']), ('brindille', len(texte), False))
        self.assertNotIn('DURAND', json.dumps(audit))                                                 # l'audit ne garde pas le texte

    def test_V03_droits_et_limites(self):
        assistant567.save_profile(self.desk, {'speech_enabled': False}, 'avocat@example.test')
        with self.assertRaisesRegex(Stop, 'lecture_vocale_desactivee'):
            voix5694.preparer(self.desk, 'Texte fictif.', 'avocat@example.test')
        for texte in ('', ' '):
            with self.assertRaisesRegex(Stop, 'texte_lecture_vide'):   # 5.6.96 (V01) : texte vide refusé ; le refus « trop_long » à 7 000 est retiré
                voix5694.preparer(self.desk, texte)
        self.assertEqual(self.audits(), [])
        # 5.6.96 (V01) : au-delà de 7 000 caractères, le texte est lu en entier (plafond explicite annoncé : test_v5696_voix)
        self.assertEqual(voix5694.preparer(self.desk, 'x' * 7001)['characters'], 7001)

    def test_V03_voix_du_serveur_choisie_rien_n_est_lu_dans_le_navigateur(self):
        self.desk.c['speech568'] = {'provider': 'kokoro', 'url': 'http://127.0.0.1:8880'}
        out = voix5694.preparer(self.desk, 'Me DURAND, art. 1240.')
        self.assertFalse(out['browser']); self.assertEqual(out['provider'], 'kokoro')
        self.assertEqual(self.audits(), [])                                                           # audit à la synthèse, comme avant

    def test_V03_V04_voix_locales_du_serveur_lisent_le_texte_developpe(self):
        self.desk.c['speech568'] = {'provider': 'espeak'}
        with patch('agent.assistant567.speech', return_value=wav_fictif()) as espeak:
            voice568.speech(self.desk, 'Me DURAND, art. 1240 C. civ.')
        self.assertEqual(espeak.call_args.args[1], 'Maître DURAND, article 1240 du Code civil')
        self.desk.c['speech568'] = {'provider': 'kokoro', 'url': 'http://127.0.0.1:8880', 'fallback_local': False}
        with patch('agent.voice568.HTTP') as http:
            http.return_value.base = 'http://127.0.0.1:8880'
            http.return_value.headers = {}
            http.return_value.request.return_value = wav_fictif()
            voice568.speech(self.desk, 'Cass. civ. 1re')
        envoye = json.loads(http.return_value.request.call_args.args[2])
        self.assertEqual(envoye['input'], 'Cour de cassation, première chambre civile')

    def test_V03_chemin_elevenlabs_inchange(self):
        self.desk.c['speech568'] = {'provider': 'elevenlabs', 'external_allowed': True, 'voice': 'voixfictive01', 'api_key_file': '',
                                    'usd_per_1000_chars': '1', 'per_request_usd': '1', 'monthly_usd': '10', 'fallback_local': False}
        self.desk.c['hybrid_routing'] = {'mode': 'manual', 'external_client_data_approved': True, 'allowed_purposes': ['assistant'], 'excluded_matters': []}
        out = voix5694.preparer(self.desk, 'Voir art. 1240 C. civ.')
        self.assertEqual(out['paragraphs'], [['Voir art. 1240 C. civ.']])                             # ni développé ni nettoyé
        with patch('agent.voice568.HTTP') as http, patch('agent.voice568.read_secret', return_value='cle-fictive'):
            http.return_value.base = 'https://api.elevenlabs.io'
            http.return_value.headers = {}
            http.return_value.request.return_value = b'\x00\x00' * 100
            voice568.synthesize(self.desk, 'Voir art. 1240 C. civ.')
        self.assertEqual(json.loads(http.return_value.request.call_args.args[2])['text'], 'Voir art. 1240 C. civ.')

    def test_V03_aucun_nouveau_fournisseur_externe(self):
        self.assertEqual(set(voix5694.FOURNISSEURS) - {'espeak', 'kokoro', 'chatterbox', 'elevenlabs'}, {'brindille'})
        r = voice568.preview(self.desk, 'Texte fictif.')
        self.assertEqual((r['provider'], r['external'], r['browser'], r['transmitted_text']), ('brindille', False, True, None))


# ================================================================ V06 : repli sur la voix du serveur
class V06Repli(Bureau):
    def test_V06_repli_serveur_espeak_par_defaut(self):
        with patch('agent.assistant567.speech', return_value=wav_fictif()) as espeak:
            raw = voice568.speech(self.desk, 'Me DURAND.')
        self.assertTrue(raw.startswith(b'RIFF'))
        self.assertEqual(espeak.call_args.args[1], 'Maître DURAND.')
        self.assertEqual(self.audits()[-1]['provider'], 'espeak'); self.assertEqual(self.audits()[-1]['requested'], 'brindille')

    def test_V06_repli_kokoro_configure(self):
        self.desk.c['speech568'] = {'provider': 'brindille', 'fallback_provider': 'kokoro', 'url': 'http://127.0.0.1:8880', 'fallback_local': False}
        with patch('agent.voice568.HTTP') as http:
            http.return_value.base = 'http://127.0.0.1:8880'
            http.return_value.headers = {}
            http.return_value.request.return_value = wav_fictif()
            voice568.speech(self.desk, 'Texte fictif.')
        self.assertTrue(http.call_args.kwargs['local_only'])

    def repli_reste_local(self):
        for valeur in ('elevenlabs', 'inconnu', '', None):
            if voix5694.repli({'speech568': {'fallback_provider': valeur}}) not in ('espeak', 'kokoro', 'chatterbox'):
                return False
        return True

    def test_V06_la_voix_de_repli_n_est_jamais_externe(self):
        self.assertTrue(self.repli_reste_local())
        with self.assertRaisesRegex(Stop, 'voix_repli_invalide'):
            config567._validate('voice_fallback', 'elevenlabs', 'speech568.fallback_provider')
        self.assertEqual(config567._validate('voice_fallback', 'kokoro', 'speech568.fallback_provider'), 'kokoro')
        with patch.object(voix5694, 'REPLIS', voix5694.REPLIS + ('elevenlabs',)):                     # mutation : le garde-fou retiré est détecté
            self.assertFalse(self.repli_reste_local())

    def test_V06_jamais_de_silence_sans_explication(self):
        with patch('agent.portable.which', return_value=None):
            with self.assertRaisesRegex(Stop, 'synthese_locale_indisponible_installer_espeak_ng'):
                voice568.speech(self.desk, 'Texte fictif.')
        diag = voice568.diagnostic(self.desk)
        self.assertEqual(diag['provider'], 'brindille'); self.assertTrue(diag['ok']); self.assertIn('navigateur', diag['message'])


# ================================================================ V07 : Alkove
class V07Alkove(Bureau):
    def test_V07_chargeur_lit_les_modeles_depuis_axiorhub_seulement(self):
        def adresses(valeur):
            if isinstance(valeur, dict):
                return [x for v in valeur.values() for x in adresses(v)]
            return [valeur]
        for adresse in adresses(voix5694.ALKOVE_CONFIG):
            self.assertTrue(adresse.startswith('/static/alkove/'), adresse)
            self.assertNotRegex(adresse, r'^(?:[a-z]+:)?//|huggingface')
            self.assertTrue((STATIC / adresse[len('/static/'):]).exists() or adresse.endswith('/'), adresse)
        script = (STATIC / 'v5694-voix.js').read_text(encoding='utf-8')
        bloc = re.search(r'const ALKOVE = \{(.*?)\n  \};', script, re.S).group(1)
        chemins = re.findall(r"(?:prefix \+ '|asset\(')([^']+)'", bloc)
        self.assertEqual(sorted(chemins), sorted(['/static/alkove/models/', '/static/alkove/', 'alkove/alkove-engine.wasm',
                                                   'alkove/models/alkove-focus-1-preview.alk', 'alkove/models/alkove-cue-1-preview.alk']))
        self.assertNotRegex(bloc, r'https?:|//|huggingface')
        modeles = {'/static/' + c for c in chemins if c.endswith(('.alk', '.wasm'))}
        self.assertEqual(modeles, set(voix5694.ALKOVE_CONFIG['models'].values()))

    def test_V07_reglages_personnels_actives_par_defaut(self):
        profil = assistant567.profile(self.desk, 'avocat@example.test')
        self.assertTrue(profil['voice_isolation']); self.assertTrue(profil['stop_on_silence'])
        assistant567.save_profile(self.desk, {'voice_isolation': False}, 'avocat@example.test')
        profil = assistant567.profile(self.desk, 'avocat@example.test')
        self.assertFalse(profil['voice_isolation']); self.assertTrue(profil['stop_on_silence'])
        self.assertTrue(assistant567.profile(self.desk, 'autre@example.test')['voice_isolation'])     # réglage personnel
        with self.assertRaises(Stop):
            assistant567.save_profile(self.desk, {'stop_on_silence': 'oui'}, 'avocat@example.test')
        etat = voix5694.etat(self.desk, 'avocat@example.test')
        self.assertEqual((etat['voice_isolation'], etat['stop_on_silence'], etat['alkove_version']), (False, True, '0.4.2'))

    def test_V07_vitesse_conservee_si_le_formulaire_ne_l_envoie_pas(self):
        assistant567.save_profile(self.desk, {'speech_rate': 1.3}, 'avocat@example.test')
        assistant567.save_profile(self.desk, {'name': 'Pilote', 'voice_isolation': True}, 'avocat@example.test')
        self.assertEqual(assistant567.profile(self.desk, 'avocat@example.test')['speech_rate'], 1.3)


# ================================================================ V08 : page Voix et routes
class V08Page(Application):
    def page(self, langue=None):
        if langue:
            from agent import i18n5631
            with patch.object(i18n5631, 'current', return_value=langue):
                return self.requete('/parametres', requete='rubrique=voix')['body'].decode('utf-8')
        return self.requete('/parametres', requete='rubrique=voix')['body'].decode('utf-8')

    def test_V08_page_voix_complete_en_carbon(self):
        page = self.page()
        section = re.search(r'<section class="v94-voix".*?</section>', page, re.S).group(0)
        self.assertIn('data-carbon', section)
        self.assertRegex(section, r'<form class="m5-form ui89-form v94-choix" data-api="m5689/voix/reglages" data-carbon data-reload="1"')
        choix = re.search(r'<select name="fournisseur"[^>]*>(.*?)</select>', section, re.S).group(1)
        self.assertEqual(re.findall(r'value="([a-z]+)"', choix), ['brindille', 'kokoro', 'espeak', 'chatterbox', 'elevenlabs'])
        self.assertRegex(choix, r'value="brindille" selected')
        self.assertIn('id="v94-essai-texte"', section); self.assertIn('data-v94-ecouter', section); self.assertIn('data-v94-arreter', section)
        self.assertRegex(section, r'<input type="range" id="v94-vitesse" name="speech_rate" min="0.7" max="1.5" step="0.1" value="1.0"')
        self.assertRegex(section, r'name="voice_isolation" checked'); self.assertRegex(section, r'name="stop_on_silence" checked')
        self.assertIn('Kokoro-FastAPI', section)                                                     # guide Kokoro repris
        licences = re.sub(r'<[^>]+>', '', re.search(r'<p data-v94-licences>(.*?)</p>', section, re.S).group(1))
        self.assertEqual(licences, 'Voix Estelle de Kyutai, licence CC BY 4.0 ; dictionnaire de prononciation dérivé du Montreal Forced Aligner, '
                                   'CC BY 4.0 ; moteur Alkove, Apache 2.0.')
        for lien in ('https://creativecommons.org/licenses/by/4.0/', 'https://github.com/kyutai-labs/pocket-tts', 'https://github.com/MontrealCorpusTools/mfa-models',
                     'https://github.com/alkove-ai/alkove', '/agent-courriel/static/alkove/LICENSE', '/agent-courriel/static/brindille/NOTICE'):
            self.assertIn('href="' + lien + '"', section)
        # aucun réglage perdu, aucun réglage en double sur la page
        self.assertEqual(page.count('data-config567="speech568.provider"'), 1)
        self.assertEqual(page.count('name="speech_rate"'), 1)
        for repere in ('id="connections567-form"', 'id="assistant567-preferences"', 'Voix et conversation rapide', 'id="voix"', 'data-config567="audio.enabled"',
                       'data-config567="speech568.url"', 'data-connector567="voice"'):
            self.assertIn(repere, page)

    def test_V08_textes_dans_les_quatre_langues(self):
        from agent import i18n5631
        cles = [k for k in i18n5631.catalog('fr') if k.startswith(('voix5694.', 'js.5694.'))]
        self.assertGreater(len(cles), 50)
        for langue in ('en', 'de', 'es'):
            catalogue = i18n5631.catalog(langue)
            self.assertEqual([k for k in cles if not catalogue.get(k)], [], langue)
        self.assertIn('Reading voice and dictation', self.page('en'))
        self.assertIn('Vorlesestimme und Diktat', self.page('de'))

    def test_V08_choix_de_la_voix_enregistre_puis_409_si_page_perimee(self):
        revision = int(self.cfg().get('config_revision567', 0))
        r = self.requete('/api440/m5689/voix/reglages', 'POST', {'fournisseur': 'kokoro', 'repli': 'espeak', 'revision': revision})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual((self.cfg()['speech568']['provider'], self.cfg()['speech568']['fallback_provider']), ('kokoro', 'espeak'))
        perimee = self.requete('/api440/m5689/voix/reglages', 'POST', {'fournisseur': 'brindille', 'repli': 'espeak', 'revision': revision})
        self.assertTrue(perimee['status'].startswith('409'), perimee['status'])
        self.assertEqual(self.cfg()['speech568']['provider'], 'kokoro')                              # rien d'écrasé
        refus = self.requete('/api440/m5689/voix/reglages', 'POST', {'fournisseur': 'brindille', 'repli': 'elevenlabs', 'revision': revision + 1})
        self.assertTrue(refus['status'].startswith('400')); self.assertEqual(json.loads(refus['body'])['error'], 'voix_repli_invalide')
        csrf = self.requete('/api440/m5689/voix/reglages', 'POST', {'fournisseur': 'brindille', 'repli': 'espeak', 'revision': revision + 1}, csrf='mauvais')
        self.assertFalse(csrf['status'].startswith('200')); self.assertEqual(self.cfg()['speech568']['provider'], 'kokoro')

    def test_V08_preparer_par_la_pile_complete(self):
        r = self.requete('/api440/m5689/voix/preparer', 'POST', {'text': 'Me DURAND, art. 1240 C. civ.', 'matter': 'DOS-001'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(json.loads(r['body'])['paragraphs'], [['Maître DURAND, article 1240 du Code civil']])
        etat = json.loads(self.requete('/api440/m5689/voix/etat')['body'])
        self.assertEqual((etat['provider'], etat['fallback']), ('brindille', 'espeak'))


class V08Droits(Bureau):
    ORIGIN = 'https://cabinet.example.test'
    AUTH = {'origin': ORIGIN, 'csrf': 'jeton-csrf-fictif'}

    def appel(self, route, corps, role, compte='avocat@example.test'):
        donnees = json.dumps(corps).encode()
        env = {'REQUEST_METHOD': 'POST', 'wsgi.input': io.BytesIO(donnees), 'CONTENT_LENGTH': str(len(donnees)), 'CONTENT_TYPE': 'application/json',
               'HTTP_ORIGIN': self.ORIGIN, 'HTTP_X_CSRF_TOKEN': 'jeton-csrf-fictif', 'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role}
        out = voix_web5694.api(env, self.desk, self.AUTH, '/agent-courriel', 'm5689/' + route, {}, 'POST')
        return out['status'], json.loads(out['body'])

    def test_V08_seul_l_administrateur_choisit_la_voix(self):
        from agent import standalone_auth, membres5650
        for role in ('avocat', 'assistant'):
            self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/m5689/voix/reglages'))
            statut, sortie = self.appel('voix/reglages', {'fournisseur': 'kokoro', 'repli': 'espeak', 'revision': 0}, role)
            self.assertTrue(statut.startswith('403'), statut); self.assertEqual(sortie['error'], 'role_insuffisant')
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m5689/voix/preparer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/voix/preparer'))   # comme m568/speech
        self.assertEqual(membres5650.category('POST', '/api440/m5689/voix/reglages'), 'reglages')
        with patch.object(voix_web5694, 'allowed', lambda role, route: True):                        # mutation : sans le contrôle, l'avocat écrirait
            statut, _ = self.appel('voix/reglages', {'fournisseur': 'kokoro', 'repli': 'espeak', 'revision': 0}, 'avocat')
            self.assertFalse(statut.startswith('403'))

    def test_V08_dossier_reserve_refuse(self):
        from agent import membres5650
        with patch.object(membres5650, 'hidden_for', return_value={'DOS-001'}):
            statut, sortie = self.appel('voix/preparer', {'text': 'Texte fictif.', 'matter': 'DOS-001'}, 'avocat')
        self.assertTrue(statut.startswith('404')); self.assertEqual(sortie['error'], 'dossier_absent')
        self.assertEqual(self.audits(), [])
        statut, sortie = self.appel('voix/preparer', {'text': 'Texte fictif.', 'matter': ''}, 'avocat')
        self.assertTrue(statut.startswith('200')); self.assertTrue(sortie['browser'])


# ================================================================ V09 : mentions et exigences négatives
class V09Mentions(unittest.TestCase):
    def test_V09_notices_tierces(self):
        texte = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        section = texte[texte.index('## Voix dans le navigateur (5.6.94)'):]
        for mention in ('Brindille', voix5694.BRINDILLE_REVISION, 'Alkove', '0.4.2', 'Lex', 'Estelle', 'Kyutai', 'CC BY 4.0', 'Montreal Forced Aligner',
                        'AuroraTTS-100K', 'Unicode License v3', 'GTCRN', 'Apache-2.0', voix5694.ALKOVE_MODELES['focus'][1], voix5694.ALKOVE_MODELES['cue'][1]):
            self.assertIn(mention, section)

    def test_V09_aucune_dependance_python_nouvelle(self):
        permis = {'re', 'hashlib', 'pathlib', 'html', 'json'}
        for nom in ('lecture5694.py', 'voix5694.py', 'voix_web5694.py'):
            arbre = ast.parse((ROOT / 'agent' / nom).read_text(encoding='utf-8'))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    self.assertTrue(all(a.name.split('.')[0] in permis for a in noeud.names), (nom, [a.name for a in noeud.names]))
                elif isinstance(noeud, ast.ImportFrom) and noeud.level == 0:
                    self.assertIn(noeud.module.split('.')[0], permis, nom)

    def test_V09_aucun_clonage_de_voix(self):
        # Brindille n'a qu'une voix ; aucune route ni aucun réglage n'accepte un échantillon de voix à imiter.
        self.assertEqual(json.loads((STATIC / 'brindille' / 'model' / 'tts.json').read_text(encoding='utf-8'))['sr'], 22050)
        script = (STATIC / 'v5694-voix.js').read_text(encoding='utf-8')
        self.assertNotRegex(script, r'learn\s*:\s*true|voiceprint|enroll')


@unittest.skipUnless(shutil.which('node') and os.environ.get('AXIORHUB_ESSAI_BRINDILLE_NODE') == '1',
                     'essai du moteur Brindille sous Node : à la demande (AXIORHUB_ESSAI_BRINDILLE_NODE=1, Node installé)')
class V05Moteur(unittest.TestCase):
    """Le moteur embarqué produit un signal non vide pour un texte préparé (Node, mêmes fichiers que le navigateur, lus sur disque).
    Il exécute le code tiers embarqué (``tts.js``, ``tts.wasm``) : il ne tourne donc que sur demande explicite, jamais dans la recette par défaut."""

    def test_V05_brindille_produit_un_son_non_vide(self):
        with tempfile.TemporaryDirectory() as dossier:
            essai = Path(dossier) / 'essai.mjs'
            essai.write_text(
                "import { readFile } from 'node:fs/promises';\n"
                "import { pathToFileURL, fileURLToPath } from 'node:url';\n"
                "const base = pathToFileURL(process.argv[2]).href;\n"
                "const { Tts } = await import(base + 'tts.js');\n"
                "const tts = await Tts.load(base, async url => { const b = await readFile(fileURLToPath(url)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); });\n"
                "const pcm = tts.speak(process.argv[3], 1);\n"
                "let crete = 0; for (const x of pcm) crete = Math.max(crete, Math.abs(x));\n"
                "console.log(JSON.stringify({ echantillons: pcm.length, frequence: tts.sampleRate, crete }));\n", encoding='utf-8')
            texte = lec.developper('Me DURAND plaide devant la CA Lyon le 12/03/2026.')
            sortie = subprocess.run(['node', str(essai), str(STATIC / 'brindille') + '/', texte], capture_output=True, text=True, timeout=120)
            self.assertEqual(sortie.returncode, 0, sortie.stderr[-800:])
            mesure = json.loads(sortie.stdout.strip().splitlines()[-1])
            self.assertEqual(mesure['frequence'], 22050)
            self.assertGreater(mesure['echantillons'], 22050)                                         # plus d'une seconde de parole
            self.assertGreater(mesure['crete'], 0.01)


if __name__ == '__main__':
    unittest.main()
