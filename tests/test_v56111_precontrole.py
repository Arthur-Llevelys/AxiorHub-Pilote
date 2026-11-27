"""5.6.111 (lot U, B6) : précontrôle du poste (dictée whisper.cpp, lecteur et aperçu).

Faux exécutables et codes de sortie simulés (``_lancer_essai`` remplacée) : Windows sans composant Microsoft Visual C++ (0xC0000135, aussi
reçu signé), Ubuntu avec libgomp1 introuvable (127) et jamais déclarée absente sans ce code, moteur qui démarre, délai dépassé, autre code ;
WebKitGTK présent ou absent (application Ubuntu), LibreOffice présent ou absent ; aucun processus lancé à l'affichage, aucun téléchargement,
aucun micro réel ; routes (rôles, CSRF, 404), carte de Paramètres › Voix, traductions et script. Données fictives, aucun réseau.
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from agent import dictee56110 as D, portable   # noqa: E402
from agent import web568, web_rules568   # noqa: E402,F401 — liés à web567.actor par son nom : chargés avant tout remplacement
from agent import precontrole56111 as P   # noqa: E402

ROOT = TESTS.parent
AUTH = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}


class FauxLanceur:
    """Remplace ``P._lancer_essai`` : note les appels, rend le code voulu (``None`` = délai dépassé)."""

    def __init__(self, code=0):
        self.code, self.appels = code, []

    def __call__(self, args, cwd, delai=P.DELAI):
        self.appels.append((list(args), str(cwd), delai))
        return self.code


def env_de(corps=b'{}', csrf='jeton', methode='POST'):
    return {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': AUTH['origin'], 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='axh-lotu-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.moteur = self.tmp / 'Poste été' / 'dictee'
        self.moteur.mkdir(parents=True)
        for nom in ('whisper-cli.exe', 'whisper-cli'):
            (self.moteur / nom).write_bytes(b'faux moteur')
        self.etat = self.tmp / 'état'
        self.etat.mkdir()
        with sqlite3.connect(self.etat / 'desk.sqlite3') as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        db.close()
        self.cfg = {'state_dir': str(self.etat), 'audio': {}}
        self.lanceur = FauxLanceur(0)
        for p in (patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.moteur)}), patch.object(P, '_lancer_essai', self.lanceur),
                  patch.object(P, '_webkit_present', lambda: True), patch.object(P, '_convertisseur', lambda: '/usr/bin/libreoffice'),
                  patch.dict(P._DERNIER, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def par_id(self, liste):
        return {x['id']: x for x in liste}


class Lancement(_Env):
    def test_U_B6_windows_sans_visual_cpp_code_0xC0000135(self):
        for code in (0xC0000135, -1073741515):            # même valeur reçue non signée ou signée (Windows)
            self.lanceur.code = code
            with patch.object(portable, 'WINDOWS', True):
                x = self.par_id(P.verifier(self.cfg)['controles'])['lancement']
            self.assertEqual(x['etat'], 'manque')
            self.assertIn('Visual C++', x['message'])
            self.assertIn('0xC0000135', x['message'])
            self.assertEqual(x['lien'], D.VC_REDIST)
            self.assertTrue(x['lien'].startswith('https://aka.ms/'))
            self.assertGreaterEqual(len(x['marche']), 3)
        args, cwd, delai = self.lanceur.appels[0]
        self.assertEqual(args[1:], ['-h'])                    # aide seulement : aucun modèle, aucun son
        self.assertTrue(args[0].endswith('whisper-cli.exe'))
        self.assertEqual(Path(cwd), self.moteur)
        self.assertLessEqual(delai, 30)

    def test_U_B6_ubuntu_libgomp1_seulement_sur_le_code_127(self):
        with patch.object(portable, 'WINDOWS', False):
            self.lanceur.code = 0
            liste = P.verifier(self.cfg)['controles']
            texte = json.dumps(liste, ensure_ascii=False)
            self.assertNotIn('introuvable', texte)
            self.assertEqual(self.par_id(liste)['lancement']['etat'], 'ok')
            self.assertIn('fournie par le paquet du poste', self.par_id(liste)['moteur']['message'])   # jamais déclarée absente
            self.assertTrue(all(x['etat'] != 'manque' or x['id'] not in ('moteur', 'lancement') for x in liste))
            self.lanceur.code = 127
            x = self.par_id(P.verifier(self.cfg)['controles'])['lancement']
        self.assertEqual(x['etat'], 'manque')
        self.assertIn('libgomp1', x['message'])
        self.assertIn('code 127', x['message'])
        self.assertIn('sudo apt-get install -y libgomp1', x['marche'][0])
        self.assertTrue(self.lanceur.appels[-1][0][0].endswith('whisper-cli'))

    def test_U_B6_code_127_sous_windows_n_est_pas_libgomp(self):
        self.lanceur.code = 127
        with patch.object(portable, 'WINDOWS', True):
            x = self.par_id(P.verifier(self.cfg)['controles'])['lancement']
        self.assertEqual(x['etat'], 'a_verifier')
        self.assertNotIn('libgomp1', json.dumps(x, ensure_ascii=False))

    def test_U_B6_delai_et_autre_code(self):
        self.lanceur.code = None
        x = self.par_id(P.verifier(self.cfg)['controles'])['lancement']
        self.assertEqual((x['etat'], x['message']), ('a_verifier', P.TEXTES['delai']))
        self.lanceur.code = 3
        x = self.par_id(P.verifier(self.cfg)['controles'])['lancement']
        self.assertEqual(x['etat'], 'a_verifier')
        self.assertIn('code 3', x['message'])

    def test_U_B6_moteur_present_et_demarre(self):
        r = P.verifier(self.cfg)
        x = self.par_id(r['controles'])
        self.assertEqual(x['moteur']['etat'], 'ok')
        self.assertEqual(x['lancement']['etat'], 'ok')
        self.assertEqual(x['modele']['etat'], 'manque')     # aucun modèle installé : dit, pas téléchargé
        self.assertIn('carte « Dictée sur ce poste »', x['modele']['marche'][0])

    def test_U_B6_moteur_absent_sans_lancement(self):
        with patch.dict(os.environ, {'AXIORHUB_DICTEE': str(self.tmp / 'rien')}):
            liste = P.verifier(self.cfg)['controles']
        x = self.par_id(liste)
        self.assertEqual(x['moteur']['etat'], 'manque')
        self.assertEqual(x['moteur']['message'], D.TEXTES['variable'])
        self.assertNotIn('lancement', x)
        self.assertEqual(self.lanceur.appels, [])

    def test_U_B6_affichage_ne_lance_rien_et_garde_le_dernier_resultat(self):
        liste = self.par_id(P.controles(self.cfg))
        self.assertEqual(self.lanceur.appels, [])
        self.assertEqual(liste['lancement']['etat'], 'a_verifier')
        self.lanceur.code = 0xC0000135
        with patch.object(portable, 'WINDOWS', True):
            P.verifier(self.cfg)
            n = len(self.lanceur.appels)
            liste = self.par_id(P.controles(self.cfg))
        self.assertEqual(len(self.lanceur.appels), n)           # l'affichage relit le dernier résultat, ne relance pas
        self.assertEqual(liste['lancement']['etat'], 'manque')
        with sqlite3.connect(self.etat / 'desk.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM settings').fetchone()[0], 0)   # rien écrit en base
        db.close()


class LecteurEtFenetre(_Env):
    def test_U_B6_libreoffice_absent_marche_selon_le_systeme(self):
        with patch.object(P, '_convertisseur', lambda: None):
            with patch.object(portable, 'WINDOWS', True):
                w = self.par_id(P.controles(self.cfg))['convertisseur']
            with patch.object(portable, 'WINDOWS', False):
                l = self.par_id(P.controles(self.cfg))['convertisseur']
        self.assertEqual((w['etat'], l['etat']), ('manque', 'manque'))
        self.assertIn('libreoffice.org', w['marche'][0])
        self.assertIn('sudo apt-get install -y libreoffice-writer', l['marche'][0])
        self.assertEqual(self.par_id(P.controles(self.cfg))['convertisseur']['etat'], 'ok')

    def test_U_B6_webkitgtk_application_ubuntu(self):
        with patch.object(portable, 'WINDOWS', False), patch.object(P, '_mode', lambda c: 'poste_linux'):
            self.assertEqual(self.par_id(P.controles(self.cfg))['fenetre']['etat'], 'a_verifier')   # pas de processus à l'affichage
            with patch.object(P, '_webkit_present', lambda: False):
                x = self.par_id(P.verifier(self.cfg)['controles'])['fenetre']
            self.assertEqual(x['etat'], 'manque')
            self.assertIn('gir1.2-webkit2-4.1', x['marche'][0])
            self.assertEqual(self.par_id(P.controles(self.cfg))['fenetre']['etat'], 'manque')
            x = self.par_id(P.verifier(self.cfg)['controles'])['fenetre']
            self.assertEqual(x['etat'], 'ok')
        with patch.object(portable, 'WINDOWS', True):
            self.assertEqual(self.par_id(P.controles(self.cfg))['fenetre']['etat'], 'non_concerne')
        with patch.object(portable, 'WINDOWS', False), patch.object(P, '_mode', lambda c: 'serveur'):
            self.assertEqual(self.par_id(P.controles(self.cfg))['fenetre']['etat'], 'non_concerne')

    def test_U_B6_micro_lu_par_le_navigateur(self):
        x = self.par_id(P.controles(self.cfg))['micro']
        self.assertEqual(x['etat'], 'navigateur')
        js = (ROOT / 'agent' / 'static' / 'v56111-precontrole.js').read_text(encoding='utf-8')
        self.assertIn("navigator.permissions.query({ name: 'microphone' })", js)
        self.assertIn('isSecureContext', js)
        self.assertNotIn('getUserMedia(', js)                   # le micro n'est jamais ouvert par le précontrôle
        self.assertNotIn('fetch(', js)


class Routes(_Env):
    def setUp(self):
        super().setUp()

        class Bureau:
            c = self.cfg
        self.d = Bureau()

    def api(self, nom, role='administrateur', methode='POST', csrf='jeton'):
        with patch('agent.web567.actor', return_value=('compte', role)):
            out = P.api(env_de(b'{}', csrf, methode), self.d, AUTH, '', P.PREFIXE + nom, {}, methode)
        return out['status'], json.loads(out['body']), out

    def test_U_B6_roles_csrf_et_route_inconnue(self):
        code, corps, out = self.api('verifier', role='avocat')
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'role_insuffisant'))
        self.assertEqual(self.lanceur.appels, [])
        code, corps, _ = self.api('verifier', csrf='faux')
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'origine_ou_csrf_refuse'))
        self.assertEqual(self.lanceur.appels, [])
        code, corps, _ = self.api('inconnue')
        self.assertEqual(code, '404 Not Found')
        code, corps, out = self.api('etat', role='assistant', methode='GET')
        self.assertEqual(code, '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(self.lanceur.appels, [])
        code, corps, _ = self.api('verifier')
        self.assertEqual(code, '200 OK')
        self.assertEqual(len(self.lanceur.appels), 1)
        self.assertIn('Vérification faite', corps['message'])

    def test_U_B6_controle_central_des_routes(self):
        from agent import standalone_auth
        self.assertTrue(P.allowed('administrateur', 'POST', 'm56111/precontrole/verifier'))
        self.assertFalse(P.allowed('avocat', 'POST', 'm56111/precontrole/verifier'))
        self.assertTrue(P.allowed('assistant', 'GET', 'm56111/precontrole/etat'))
        self.assertFalse(P.allowed('administrateur', 'GET', 'm56111/precontrole/autre'))
        source = (ROOT / 'agent' / 'standalone_auth.py').read_text(encoding='utf-8')
        self.assertIn("m56111/precontrole/", source)
        web440 = (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8')
        self.assertIn("m56111/precontrole/", web440)
        self.assertIn("'/static/v56111-precontrole.js'", web440)
        self.assertTrue(callable(getattr(standalone_auth, 'allowed', None)))


class Ecran(_Env):
    def setUp(self):
        super().setUp()

        class Bureau:
            c = self.cfg
        self.d = Bureau()

    def test_U_B6_carte_administrateur_et_autres_roles(self):
        with patch.object(portable, 'WINDOWS', True):
            P.verifier(self.cfg)
            h = P.section_html(self.d, '/p', 'administrateur')
        self.assertIn('id="precontrole56111"', h)
        self.assertIn('data-api="m56111/precontrole/verifier"', h)
        self.assertIn('Dernière vérification', h)
        self.assertIn('/p/static/v56111-precontrole.js', h)
        self.assertIn('data-u111-micro-etat', h)
        h = P.section_html(self.d, '/p', 'avocat')
        self.assertNotIn('data-api="m56111/precontrole/verifier"', h)
        self.assertIn(P.TEXTES['admin_seul'], h)
        self.assertNotIn('<script>', h)

    def test_U_B6_carte_msvc_lien_officiel(self):
        self.lanceur.code = 0xC0000135
        with patch.object(portable, 'WINDOWS', True):
            P.verifier(self.cfg)
            h = P.section_html(self.d, '/p', 'administrateur')
        self.assertIn('href="%s"' % D.VC_REDIST, h)
        self.assertIn('rel="noopener noreferrer"', h)
        self.assertIn('u111-pc-manque', h)

    def test_U_B6_carte_dans_parametres_voix_avant_la_dictee(self):
        source = (ROOT / 'agent' / 'parametres5621.py').read_text(encoding='utf-8')
        self.assertIn('precontrole56111 import section_html', source)
        self.assertLess(source.index('precontrole56111 import section_html'), source.index('dictee56110 import section_html as _dictee110'))

    def test_U_B6_textes_traduits_en_de_es(self):
        phrases = set(P.TEXTES.values()) | set(P.ETATS.values()) | set(P.MESSAGES.values())
        phrases = {re.sub(r'\d+', '#', p) for p in phrases if '%s' not in p}
        js = (ROOT / 'agent' / 'static' / 'v56111-precontrole.js').read_text(encoding='utf-8')
        phrases |= {re.sub(r'\d+', '#', p) for p in re.findall(r"[a-z]+: '([^']+)'", js) if ' ' in p}   # phrases (pas le nom de l'autorisation)
        phrases |= {'Vérification faite : # élément(s) manquant(s), détail ci-dessous.', 'Vérification faite : rien ne manque.'}
        for langue in ('en', 'de', 'es'):
            table = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % langue)).read_text(encoding='utf-8'))
            manquantes = sorted(p for p in phrases if p not in table)
            self.assertEqual(manquantes, [], langue)
        modeles = [p for p in list(P.TEXTES.values()) if '%s' in p]
        for langue in ('en', 'de', 'es'):
            table = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % langue)).read_text(encoding='utf-8'))
            for m in modeles:
                self.assertIn(m, table, langue)
                self.assertEqual(table[m].count('%s'), m.count('%s'))


if __name__ == '__main__':
    unittest.main()
