"""5.6.108, lot A : Aujourd'hui et À relire (disposition, continuité, volet de l'assistant, reçu des instructions).

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001…, example.test). Aucun réseau, aucun modèle (classe ``Cabinet`` de ``test_v5695_aujourdhui`` :
tout appel au modèle fait échouer). Ce qui exige un vrai navigateur (premier écran à 1920 × 1080, 125 %, 375 px, clavier ouvert, focus rendu,
brouillon restitué, reçu affiché, aucun doublon) relève du parcours ``scripts/parcours_p108a.py`` ; ces tests vérifient le serveur, les feuilles
et la logique des scripts (sous Node quand il est présent).

Exigences : A1-A2 disposition (Actuellement en tête, rang 2/3–1/3 sur grand écran, dossier lisible), A3 aucune valeur connue remplacée par un
zéro et panne dite avec sa reprise, A4 pas de recherche répétée, A6 continuité de « À relire », A7 volet de l'assistant (brouillon, Envoyer
toujours atteignable), A8 reçu et étape d'une instruction.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_v56100_accueil as t100
from agent import accueil56100 as a100, accueil5689, accueil_web5696 as acc, interface56100

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'agent' / 'static'


def lire(nom):
    return (STATIC / nom).read_text(encoding='utf-8')


class Aujourdhui(t100.Base):
    def test_A4_une_seule_recherche_avec_la_nouvelle_presentation(self):
        self.echeance('ech-ok', 'confirmee', 0)
        html = self.page()
        self.assertIn('class="c530 a89 f100"', html)
        self.assertNotIn('a89-recherche', html)                    # la barre du haut porte « Rechercher dans le cabinet » (Ctrl+K)
        self.assertIn('data-a89-pilote', html)                     # la commande IA de l'en-tête reste
        interface56100.regler_cabinet(self.d, False)               # contre-exemple : l'ancienne présentation garde ses deux commandes
        self.assertIn('a89-recherche', self.page())

    def test_A4_commande_de_l_entete(self):
        self.assertIn('a89-recherche', accueil5689.commande_html('/p'))
        sans = accueil5689.commande_html('/p', recherche=False)
        self.assertNotIn('a89-recherche', sans)
        self.assertIn('Confier une mission', sans)

    def test_A1_actuellement_en_tete_puis_rang_puis_travail(self):
        self.echeance('ech-ok', 'confirmee', 0)
        html = self.page()
        reperes = ('id="ax95-actuellement"', 'class="f96-rang"', 'id="f96-decider"', 'data-f100-journee', 'id="a89-travail"')
        positions = [html.index(x) for x in reperes]
        self.assertEqual(positions, sorted(positions))

    def test_A3_rang_en_panne_dit_avec_sa_reprise(self):
        with patch('agent.file5651.items', side_effect=RuntimeError('fictif')):
            html, _n, complet = acc.lignes_html(self.d, '/p', 'cabinet', 'administrateur')
        self.assertFalse(complet)
        self.assertIn('Une source n’a pas pu être lue', html)
        self.assertIn('<a href="/p/aujourdhui">Réessayer</a>', html)
        self.assertNotIn('Rien à décider', html)                   # jamais un zéro

    def test_A3_agenda_en_panne_dit_avec_sa_reprise(self):
        with patch('agent.agenda_lecture5697.evenements_en_ligne', side_effect=RuntimeError('fictif')):
            html = a100.agenda_html(self.d, '/p', 'cabinet', 'administrateur')
        self.assertIn('ce n’est pas un agenda vide', html)
        self.assertIn('Réessayer', html)
        self.assertIn('/p/planning?vue=agenda', html)
        self.assertNotIn('Rien à l’agenda', html)

    def test_A3_vue_file_en_panne_dite_avec_sa_reprise(self):
        with patch('agent.file5651.items', side_effect=RuntimeError('fictif')):
            html = a100.vue_file_html(self.d, '/p', 'cabinet', 'administrateur', 'decision', 1)
        self.assertIn('ce n’est pas un zéro', html)
        self.assertIn('Réessayer</a>', html)
        self.assertIn('/p/aujourdhui?vue=file&amp;file=decision', html)
        self.assertNotIn('Rien dans cette catégorie', html)

    def test_A3_echeances_du_jour_en_panne(self):
        with patch.object(acc, '_echeances', side_effect=RuntimeError('fictif')):
            html = acc.echeances_du_jour_html(self.d, '/p', 'cabinet', 'administrateur')
        self.assertIn('source indisponible', html)
        self.assertIn('/p/echeances', html)
        self.assertNotIn('Aucune échéance', html)


class Feuilles(unittest.TestCase):
    def test_A2_dossier_lisible_et_rang_deux_colonnes_sur_grand_ecran(self):
        css = lire('v56108-accueil.css')
        regle = re.search(r'\.f100 \.f96-ligne-dossier[^{]*\{([^}]*)\}', css)
        self.assertIsNotNone(regle)
        self.assertIn('--cds-text-primary', regle.group(1))       # contraste du texte principal, pas du texte secondaire
        self.assertRegex(regle.group(1), r'font-size: var\(--ui-taille-texte, \.9375rem\)')
        self.assertIn('@media (max-width: 79.99rem)', css)        # une colonne sous 80rem (125 %, tablette, mobile)
        self.assertIn('.f100-agenda .f100-ev time { width: auto; }', css)

    def test_A7_envoi_colle_au_bas_du_composeur(self):
        css = lire('v56108-accueil.css')
        self.assertRegex(css, r'\.ws-ai-sendrow \{[^}]*position: sticky;')
        self.assertRegex(css, r'\.ws-ai-sendrow \{[^}]*bottom: 0;')
        self.assertIn('min-height: 44px', css)                     # cibles tactiles au téléphone

    def test_A_feuille_et_script_declares_et_paquet_a_jour(self):
        from agent import shell501, web440
        self.assertIn('v56108-accueil.css', shell501.BUNDLE)
        self.assertEqual(web440.STATIC.get('/static/v56108-accueil.js'), 'text/javascript')
        self.assertEqual(web440.STATIC.get('/static/v56108-accueil.css'), 'text/css')
        self.assertIn(lire('v56108-accueil.css').rstrip(), lire('app520.css'))
        from agent import ui_helpers
        source = (ROOT / 'agent' / 'ui_helpers.py').read_text(encoding='utf-8')
        self.assertLess(source.index("/static/v567.js"), source.index("/static/v56108-accueil.js"))
        self.assertTrue(hasattr(ui_helpers, 'enhance'))

    def test_A_aucun_html_injecte_par_le_script_hors_reponse_du_serveur(self):
        js = lire('v56108-accueil.js')
        self.assertEqual(js.count('innerHTML'), 1)                 # le seul : HTML d'« Actuellement » échappé par le serveur, dans un modèle
        self.assertIn('modele.innerHTML = html', js)
        self.assertNotIn('eval(', js)
        self.assertNotIn('document.write', js)


class Scripts(unittest.TestCase):
    def test_A8_node_logique_du_recu_et_de_la_derniere_valeur(self):
        if not shutil.which('node'):
            self.skipTest('Node absent')
        r = subprocess.run(['node', str(ROOT / 'tests' / 'accueil56108.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_A8_toutes_les_etapes_du_serveur_ont_une_etape_ecrite(self):
        """Chaque code d'état du panneau (``missions567.PANNEAU``) est rangé dans l'une des cinq étapes : un code ajouté sans étape échoue ici."""
        from agent import missions567
        js = lire('v56108-accueil.js')
        table = re.search(r'var PAR_CODE = \{([^}]*)\}', js).group(1)
        codes = dict(re.findall(r'(\w+): \'(\w+)\'', table))
        for etat, (code, _libelle) in missions567.PANNEAU.items():
            self.assertIn(code, codes, (etat, code))
            self.assertIn(codes[code], ('recue', 'attente', 'en_cours', 'resultat', 'echec'))
        etats = dict(re.findall(r'(\w+): \'(\w+)\'', re.search(r'var PAR_ETAT = \{([^}]*)\}', js).group(1)))
        for etat in missions567.PANNEAU:
            self.assertIn(etat, etats, etat)

    def test_A8_recu_branche_sur_les_cartes_et_jamais_avant_la_reponse(self):
        js = lire('v567.js')
        carte = js[js.index('function card(m)'):js.index('function choixDossier')]
        self.assertIn('window.AX108accueil?.recu(m, c);', carte)
        provisoire = js[js.index('function carteProvisoire'):js.index('function afficherProvisoire')]
        self.assertIn('window.AX108accueil?.recuProvisoire(code, c);', provisoire)
        envoyer = js[js.index('async function envoyer(mode)'):js.index('function afficherIncertain')]
        recu = envoyer.index("dispatchEvent(new CustomEvent('ax108:recu'")
        self.assertLess(envoyer.index("await api('mission/create', payload)"), recu)        # seulement après la réponse du serveur
        self.assertLess(recu, envoyer.index('} catch (e) {'))                               # jamais dans le chemin d'erreur
        acc_js = lire('v56108-accueil.js')
        provisoires = re.search(r'var SANS_RECU = \{(.*?)\};', acc_js, re.S).group(1)
        self.assertNotIn('Reçu n°', provisoires)                   # aucune carte d'avant le reçu ne porte de numéro

    def test_A7_brouillon_efface_au_recu_seulement(self):
        js = lire('v5696-affichage.js')
        self.assertNotRegex(js, r"send\.addEventListener\('click', function \(\) \{ ecrire\(")   # 5.6.96 : effacé dès le clic (refus, coupure : perdu)
        self.assertIn("dock.addEventListener('ax108:recu'", js)

    def test_A3_actualisation_par_fusion(self):
        js = lire('v5695-aujourdhui.js')
        self.assertIn('window.AX108accueil.fusionnerActuellement(zone, v.html', js)

    def test_A6_relecture_restitue_le_defilement_a_la_fermeture(self):
        js = lire('v56100-lecteur.js')
        self.assertIn('etat.defilement = { zone:', js)
        debut = js.index('        var cachee = ')
        fermer = js[debut:js.index('      reessayer: function (s) { lecteur.voirSection(s); }', debut)]
        self.assertIn('etat.defilement : null', fermer)              # restitué seulement si la liste était masquée pendant la lecture
        self.assertIn('racine.scrollTo(d.x, d.y)', fermer)
        self.assertIn('li.focus({ preventScroll: !!d })', fermer)


if __name__ == '__main__':
    unittest.main()
