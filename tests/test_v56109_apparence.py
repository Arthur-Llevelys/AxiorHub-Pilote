"""5.6.109, lot H (F10) : navigation lisible et assistant accessible — Paramètres › Apparence (couleur du menu de gauche calculée et validée
par contraste, modes clair et sombre, réinitialisation, préférence par compte), épingle « Discussions IA », Historique dans l'en-tête de
l'assistant sans déplacer le composeur, retour qui restaure filtre, position et brouillon.

Calculs sRGB : même formule côté serveur (``apparence56109``) et navigateur (``v56109-apparence.js``, exécuté sous Node quand il est présent).
Ce qui exige un vrai navigateur (1920 × 1080 à 125 %, focus, clavier, contraste à l'écran) n'est PAS vérifié ici : voir le compte rendu.
"""
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from agent import apparence56109 as A, shell501
from agent.common import Stop
from test_v56109_historique import ADMIN, Base, DURAND, MARTIN, PREFIX

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'agent' / 'static'


def couleurs():
    for r in range(0, 256, 17):
        for g in range(0, 256, 17):
            for b in range(0, 256, 17):
                yield '#%02x%02x%02x' % (r, g, b)


class Calculs(unittest.TestCase):
    def test_F10_formule_srgb_et_seuil_sans_arrondi(self):
        self.assertEqual(A.contraste('#000000', '#ffffff'), 21)
        self.assertEqual(A.contraste('#123456', '#123456'), 1)
        self.assertLess(A.contraste('#777777', '#ffffff'), 4.5)
        self.assertGreater(A.contraste('#767676', '#ffffff'), 4.5)

    def test_F10_palette_lisible_sur_4096_couleurs(self):
        for c in couleurs():
            p = A.palette(c)
            for fond, avant in ((p['fond'], p['texte']), (p['fond'], p['secondaire']), (p['survol'], p['texteSurvol']), (p['actif'], p['texteActif'])):
                self.assertGreaterEqual(A.contraste(fond, avant), 4.5, (c, fond, avant))
                self.assertGreaterEqual(max(A.contraste(fond, p['focusInterieur']), A.contraste(fond, p['focusExterieur'])), 3)
            self.assertGreaterEqual(A.contraste(p['actif'], p['marqueur']), 3)
            A.controle_palette(c)

    def test_F10_entrees_refusees_avant_toute_modification(self):
        for v in ('red', '#fff', '#ffffff00', '#123456;display:none', 'url(x)', None, 4, 'linear-gradient(#000,#fff)', ' #123456'):
            with self.assertRaises(Stop):
                A.normaliser_hex(v)
        for valeur in ({'mode': 'nuit'}, {'mode': 'dark', 'autre': 1}, {'pin_ai_history': 'oui'}, {'sidebar': {'light': 'red'}},
                       {'sidebar': {'same_for_both': 'non'}}, {'sidebar': {'light': '#123456', 'css': 'x'}}, []):
            with self.assertRaises(Stop):
                A.valider(valeur)
        v = A.variables('#F0F0F0')
        self.assertEqual(v['--ax-nav-fg'], '#000000')
        self.assertFalse(any(re.search('danger|success|button|page', k) for k in v))

    def test_F10_controle_serveur_refuse_une_palette_insuffisante(self):
        """Garde-fou serveur : même si le calcul du navigateur était contourné ou fautif, une palette sous les seuils est refusée."""
        vraie = A.palette
        mauvaise = lambda c: {**vraie(c), 'texte': '#777777', 'secondaire': '#777777'}
        with self.assertRaisesRegex(Stop, 'contraste_insuffisant'):
            A.controle_palette('#ffffff', mauvaise)
        with patch.object(A, 'palette', mauvaise), self.assertRaisesRegex(Stop, 'contraste_insuffisant'):
            A.valider({'sidebar': {'same_for_both': True, 'light': '#ffffff'}})

    @unittest.skipUnless(shutil.which('node'), 'Node absent : parité navigateur non vérifiée ici')
    def test_F10_meme_calcul_dans_le_navigateur(self):
        script = ("globalThis.window=globalThis;globalThis.document={readyState:'complete',querySelector(){return null},getElementById(){return null},"
                  "documentElement:{dataset:{}},addEventListener(){}};require(process.argv[1]);const out={};for(const c of JSON.parse(process.argv[2]))"
                  "{out[c]=window.AX109.variables(c)}let refus=0;for(const c of ['red','#fff','#12345678','url(x)']){try{window.AX109.normaliserHex(c)}catch(e){refus++}}"
                  "console.log(JSON.stringify({out,refus}))")
        echantillon = list(couleurs())[::7] + ['#183153', '#161616', '#f4f4f4', '#808080', '#767676']
        r = subprocess.run(['node', '-e', script, str(STATIC / 'v56109-apparence.js'), json.dumps(echantillon)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data['refus'], 4)
        for c in echantillon:
            self.assertEqual(data['out'][c], A.variables(c), c)


class Preference(Base):
    def cle(self, n=[0]):
        n[0] += 1
        return 'cle-ax109-%05d' % n[0]

    def test_F10_preference_par_compte_revision_et_reinitialisation(self):
        self.assertEqual(A.lire(self.d, DURAND[0])['sidebar']['light'], None)       # premier lancement : rendu actuel conservé
        v = {'mode': 'dark', 'sidebar': {'same_for_both': False, 'light': '#F4F4F4', 'dark': '#183153'}, 'pin_ai_history': True}
        out = A.enregistrer(self.d, DURAND, v, 0, self.cle())['preference']
        self.assertEqual((out['revision'], out['sidebar']['light'], out['sidebar']['dark']), (1, '#f4f4f4', '#183153'))
        self.assertEqual(A.lire(self.d, MARTIN[0])['sidebar']['light'], None)        # la préférence de l'un ne change rien pour l'autre
        with self.assertRaisesRegex(Stop, 'conflit_de_revision'):                   # second poste avec l'ancienne révision
            A.enregistrer(self.d, DURAND, {**v, 'mode': 'light'}, 0, self.cle())
        out = A.enregistrer(self.d, DURAND, {'sidebar': {'same_for_both': True, 'light': '#1f4d3a', 'dark': '#ffffff'}}, 1, self.cle())['preference']
        self.assertEqual(out['sidebar']['dark'], '#1f4d3a')                         # même couleur dans les deux modes
        neuve = A.reinitialiser(self.d, DURAND, 2, self.cle())['preference']
        self.assertEqual((neuve['sidebar']['light'], neuve['sidebar']['dark'], neuve['revision']), (None, None, 3))
        with self.assertRaisesRegex(Stop, 'conflit_de_revision'):
            A.reinitialiser(self.d, DURAND, 2, self.cle())

    def test_F10_valeur_abimee_rendu_du_produit(self):
        self.d.setting(A._cle(DURAND[0]), {'mode': 'dark', 'sidebar': {'light': 'url(javascript:x)'}, 'revision': 4})
        p = A.lire(self.d, DURAND[0])
        self.assertEqual((p['sidebar']['light'], p['mode']), (None, 'system'))

    def test_F10_barre_laterale_attributs_valides_et_epingle(self):
        cfg = self.d.c
        self.assertEqual(A.attributs_sidebar(cfg, DURAND[0]), '')
        A.enregistrer(self.d, DURAND, {'mode': 'system', 'sidebar': {'same_for_both': False, 'light': '#f4f4f4', 'dark': '#161616'}, 'pin_ai_history': True}, 0, self.cle())
        attrs = A.attributs_sidebar(cfg, DURAND[0])
        self.assertIn('data-ax109-nav-light="#f4f4f4"', attrs)
        self.assertIn('data-ax109-nav-dark="#161616"', attrs)
        self.assertNotIn('style', attrs)
        self.assertEqual(A.attributs_sidebar(cfg, MARTIN[0]), '')
        self.assertTrue(A.epingle(cfg, DURAND[0]))
        from agent import i18n5631
        shell501.set_context(cfg, {}, '/aujourdhui')
        i18n5631._ctx.owner = DURAND[0]
        self.addCleanup(setattr, i18n5631._ctx, 'owner', '')
        html = shell501.sidebar_html(PREFIX, '/aujourdhui')
        self.assertIn('<aside id="ws-sidebar" data-ax109-rev="1"', html)
        self.assertIn(PREFIX + '/assistant?vue=historique', html)
        self.assertIn('v56109-apparence.js', html)
        self.assertIn('v56109-historique.js', html)
        i18n5631._ctx.owner = MARTIN[0]
        html = shell501.sidebar_html(PREFIX, '/aujourdhui')
        self.assertIn('<aside id="ws-sidebar">', html)
        self.assertNotIn('/assistant?vue=historique', html)

    def test_F10_section_parametres_et_routes(self):
        html = A.section_html(self.d, PREFIX, DURAND[0])
        for libelle in ('Couleur du menu de gauche', 'Utiliser la même couleur dans les deux modes', 'Épingler « Discussions IA »', 'Rétablir les couleurs par défaut',
                        'Annuler', 'Enregistrer', 'Aperçu (non enregistré)'):
            self.assertIn(libelle, html)
        self.assertNotIn('style=', html)                    # CSP : aucun style en ligne (pastilles et aperçu posés par CSSOM)
        self.assertNotIn('<script>', html)
        from agent import parametres5621
        self.assertIn('apparence', parametres5621.BUILDERS)
        self.assertEqual(parametres5621.rubrique_for('/parametres', {'rubrique': 'apparence'}), 'apparence')
        raw = json.dumps({'valeur': {'mode': 'light', 'sidebar': {'same_for_both': True, 'light': '#183153'}}, 'revision': 0, 'cle': self.cle()}).encode()
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': 'assistant', 'HTTP_X_AXIORHUB_USER': 'bernard@example.test'}
        out = A.api(env, self.d, self.auth, PREFIX, A.PREFIXE + 'enregistrer', {}, 'POST')
        self.assertEqual(out['status'], '200 OK')
        self.assertEqual(dict(out['headers'])['Cache-Control'], 'private, no-store')
        self.assertEqual(A.lire(self.d, 'bernard@example.test')['sidebar']['light'], '#183153')
        env['wsgi.input'] = io.BytesIO(raw)
        env['HTTP_X_CSRF_TOKEN'] = 'mauvais'
        self.assertEqual(A.api(env, self.d, self.auth, PREFIX, A.PREFIXE + 'enregistrer', {}, 'POST')['status'], '403 Forbidden')
        raw = json.dumps({'valeur': {'sidebar': {'light': 'red'}}, 'revision': 1, 'cle': self.cle()}).encode()
        env.update({'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)), 'HTTP_X_CSRF_TOKEN': 'test-csrf'})
        self.assertEqual(A.api(env, self.d, self.auth, PREFIX, A.PREFIXE + 'enregistrer', {}, 'POST')['status'], '422 Unprocessable Entity')


class Interface(unittest.TestCase):
    """Contrôles statiques des feuilles et scripts (le rendu réel à 1920 × 1080 et 125 % reste à vérifier dans un vrai navigateur)."""

    def test_F10_feuilles_dans_le_bundle_apres_carbon(self):
        self.assertLess(shell501.BUNDLE.index('v5689-carbon.css'), shell501.BUNDLE.index('v56109-apparence.css'))
        bundle = (STATIC / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('#ws-sidebar.ax-nav-custom', bundle)
        self.assertIn('.h109-tete-bouton', bundle)
        css = (STATIC / 'v56109-apparence.css').read_text(encoding='utf-8')
        self.assertIn('forced-colors', css)
        self.assertNotRegex(css, r'(?m)^\s*(nav|a)\s*\{')      # aucune règle globale sur tous les menus ou tous les liens

    def test_F10_historique_dans_l_en_tete_sans_deplacer_le_composeur(self):
        js = (STATIC / 'v56109-historique.js').read_text(encoding='utf-8')
        self.assertIn("tete.insertBefore(barre, avant)", js)             # en-tête : Historique, Nouvelle discussion, Ouvrir en grand
        self.assertIn("corps.insertBefore(panneau, corps.firstChild)", js)  # zone centrale défilante ; le composeur n'est pas touché
        self.assertNotIn('ws-ai-composeur', js)
        self.assertNotIn('localStorage.', js)                            # aucun contenu de discussion dans localStorage
        self.assertNotIn('innerHTML', js)
        css = (STATIC / 'v56109-historique.css').read_text(encoding='utf-8')
        self.assertIn('@media (max-height: 900px)', css)                 # 1536 × 864 px CSS (1920 × 1080 à 125 %)
        self.assertIn('min-height: 0', css)

    def test_F10_retour_restaure_filtre_position_et_brouillon(self):
        js = (STATIC / 'v56109-historique.js').read_text(encoding='utf-8')
        for attendu in ("addEventListener('pagehide'", 'window.scrollTo(0, sauve.y', "obtenir('brouillon?", "envoyer('brouillon'", "keepalive"):
            self.assertIn(attendu, js)
        for nom in ('v56109-historique.js', 'v56109-discussions.js', 'v56109-apparence.js'):
            self.assertNotIn('eval(', (STATIC / nom).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
