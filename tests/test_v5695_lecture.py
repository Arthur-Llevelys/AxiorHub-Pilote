"""5.6.95, lot lecture (L01 à L03) : lecture naturelle à voix haute des heures, des dates, des sigles et des abréviations.

Données fictives, aucun réseau, aucun modèle d'IA. On teste le texte que le serveur prépare (``lecture5694.developper``) pour toutes
les voix locales (Brindille dans le navigateur, Kokoro, eSpeak). Ce que ces essais ne prouvent pas : le son réellement entendu ;
une écoute humaine reste nécessaire pour juger l'intonation.
"""
import json
from pathlib import Path
import re
import unittest

from agent import lecture5694 as lec

D = lec.developper
DONNEES = Path(__file__).resolve().parent / 'data' / 'lecture5695' / 'phrases.json'
NBSP = chr(0xa0)
MOIS = '(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)'


# ================================================================ L01 : heures et dates en toutes lettres
class L01Heures(unittest.TestCase):
    def test_L01_heures_simples(self):
        for texte, attendu in (('à 9h30', 'à neuf heures trente'), ('à 9 h 30', 'à neuf heures trente'),
                               ('à 9' + NBSP + 'h' + NBSP + '30', 'à neuf heures trente'), ('à 14h.', 'à quatorze heures.'),
                               ('à 11h00', 'à onze heures'), ('à 17h42', 'à dix-sept heures quarante-deux'), ('à 8H45', 'à huit heures quarante-cinq'),
                               ('à 1h', 'à une heure'), ('à 21h01', 'à vingt et une heures une'), ('à 9h05', 'à neuf heures cinq')):
            with self.subTest(texte=texte):
                self.assertEqual(D(texte), attendu)

    def test_L01_midi_et_minuit(self):
        self.assertEqual(D('à 12h'), 'à midi')
        self.assertEqual(D('à 12h30'), 'à midi trente')
        self.assertEqual(D('à 0h'), 'à minuit')
        self.assertEqual(D('vers 00h15'), 'vers minuit quinze')
        self.assertEqual(D('entre 10h et 12h'), 'entre dix heures et midi')

    def test_L01_plages_horaires(self):
        self.assertEqual(D('de 9h à 10h30'), 'de neuf heures à dix heures trente')
        self.assertEqual(D('le 10/11/2026 9h-10h30'), 'le dix novembre deux mille vingt-six de neuf heures à dix heures trente')
        self.assertEqual(D('de 11h30 à 12h'), 'de onze heures trente à midi')

    def test_L01_dates_en_toutes_lettres(self):
        self.assertEqual(D('le 30 octobre 2026'), 'le trente octobre deux mille vingt-six')
        self.assertEqual(D('le 30/10/2026'), 'le trente octobre deux mille vingt-six')
        self.assertEqual(D('le 01/02/2026'), 'le premier février deux mille vingt-six')
        self.assertEqual(D('2026-03-12'), 'douze mars deux mille vingt-six')
        self.assertEqual(D('le 1er décembre 2026'), 'le premier décembre deux mille vingt-six')
        self.assertEqual(D('avant le 2 juin'), 'avant le deux juin')
        self.assertEqual(D('en octobre 2026'), 'en octobre deux mille vingt-six')
        self.assertEqual(D('du 3 au 5 mai 2027'), 'du trois au cinq mai deux mille vingt-sept')
        self.assertEqual(D('31/13/2026'), '31/13/2026')                                              # mois impossible : rien d'inventé

    def test_L01_phrase_de_l_avocat(self):
        self.assertEqual(D("Me DURAND, l'audience est renvoyée au mercredi 30/10/2026 à 9h30."),
                         "Maître DURAND, l'audience est renvoyée au mercredi trente octobre deux mille vingt-six à neuf heures trente.")

    def test_L01_heures_intactes_dans_url_adresse_reference(self):
        for intact in ('https://agenda.example.test/rdv?h=9h30', 'rdv9h30@example.test', '[PERSONNE_1]'):
            with self.subTest(intact=intact):
                self.assertIn(intact, D('Voir ' + intact + ' à 9h30.'))
                self.assertTrue(D('Voir ' + intact + ' à 9h30.').endswith('à neuf heures trente.'))
        self.assertEqual(D('dossier DOS14h2'), 'dossier DOS14h2')                                     # collé à une référence : intact
        self.assertEqual(D('cote 2024.9h30'), 'cote 2024.9h30')
        self.assertEqual(D('24h/24'), '24h/24')
        self.assertEqual(D('RG 24/01234'), 'erre gé vingt-quatre barre mille deux cent trente-quatre')


# ================================================================ L02 : sigles et abréviations
class L02Sigles(unittest.TestCase):
    def test_L02_sigles_epeles(self):
        self.assertEqual(D('par RPVA'), 'par erre pé vé a')
        self.assertEqual(D('la CPAM'), 'la cé pé a emme')

    def test_L02_portalis(self):
        self.assertEqual(D('via PORTALIS'), 'via Portalis')
        self.assertEqual(D('sur Portalis'), 'sur Portalis')

    def test_L02_cpce_apres_article_sinon_epele(self):
        self.assertEqual(D("l'art. R. 121-1 du CPCE"), "l'article R 121 tiret 1 du Code des procédures civiles d'exécution")
        self.assertEqual(D('art. L. 111-2 CPCE'), "article L 111 tiret 2 du Code des procédures civiles d'exécution")
        self.assertEqual(D('un CPCE annoté'), 'un cé pé cé eu annoté')

    def test_L02_sigles_developpes(self):
        self.assertEqual(D("selon l'ILC"), "selon l'indice des loyers commerciaux")
        self.assertEqual(D('La DGFiP a émis'), 'La Direction générale des finances publiques a émis')
        self.assertEqual(D('1 800 € HT'), 'mille huit cents euros hors taxes')
        self.assertEqual(D('2 160 € TTC'), 'deux mille cent soixante euros toutes taxes comprises')

    def test_L02_chambres(self):
        self.assertEqual(D('3e ch. civ.'), 'troisième chambre civile')
        self.assertEqual(D('la chambre civ.'), 'la chambre civile')
        self.assertEqual(D('ch. com.'), 'chambre commerciale')
        self.assertEqual(D('ch. soc.'), 'chambre sociale')
        self.assertEqual(D('ch. crim.'), 'chambre criminelle')
        self.assertEqual(D("L'audience de la 1re ch. civ. de la Cass."), "L'audience de la première chambre civile de la Cour de cassation")
        self.assertEqual(D('5e et 6e ch. réunies'), 'cinquième et sixième chambres réunies')

    def test_L02_conseil_constitutionnel(self):
        self.assertEqual(D('Le Conseil const., décision'), 'Le Conseil constitutionnel, décision')

    def test_L02_articles_au_pluriel(self):
        self.assertEqual(D('Les art. 1103 et 1104 C. civ.'), 'Les articles 1103 et 1104 du Code civil')
        self.assertEqual(D('art. 1103, 1104 et 1105'), 'articles 1103, 1104 et 1105')
        self.assertEqual(D('les art. L. 1234-5 et L. 1234-9 C. trav.'), 'les articles L 1234 tiret 5 et L 1234 tiret 9 du Code du travail')
        self.assertEqual(D('art. 1240 à 1245-1'), 'articles 1240 à 1245 tiret 1')
        self.assertEqual(D('entre 3 et 6 mois'), 'entre 3 et 6 mois')                                 # hors liste d'articles : intact
        self.assertEqual(D("l'art. 908 CPC, soit"), "l'article 908 du Code de procédure civile, soit")      # un seul article : singulier

    def test_L02_numeros_au_pluriel(self):
        self.assertEqual(D('les pièces nos 12 à 18'), 'les pièces numéros 12 à 18')
        self.assertEqual(D('nos clients'), 'nos clients')                                             # possessif intact

    def test_L02_pourvois_lus_de_facon_coherente(self):
        self.assertEqual(D('n° 22-12.345, publié'), 'numéro vingt-deux, douze, trois cent quarante-cinq, publié')
        self.assertEqual(D('n° 23-10.001 : la clause'), 'numéro vingt-trois, dix, zéro zéro un : la clause')
        self.assertEqual(D('pourvoi n° 22-12.345.'), 'pourvoi numéro vingt-deux, douze, trois cent quarante-cinq.')

    def test_L02_kbis(self):
        self.assertEqual(D('Le K-bis de la société'), 'Le K bis de la société')
        self.assertEqual(D('un Kbis récent'), 'un K bis récent')

    def test_L02_titres(self):
        self.assertEqual(D('Mes DURAND et ZORVAL'), 'Maîtres DURAND et ZORVAL')
        self.assertEqual(D('MM. DURAND et ZORVAL'), 'Messieurs DURAND et ZORVAL')
        self.assertEqual(D('Mme MARTIN'), 'Madame MARTIN')
        self.assertEqual(D('rubrique « Mes affaires »'), 'rubrique « Mes affaires »')


# ================================================================ L03 : cas ambigus et passage des 50 phrases
class L03Mesure(unittest.TestCase):
    def test_L03_douze_heures_de_retard(self):
        self.assertEqual(D('12h de retard'), 'douze heures de retard')
        self.assertEqual(D('sous 48h'), 'sous quarante-huit heures')

    def test_L03_numero_de_facture_pas_une_annee(self):
        lu = D('la facture n° 2026-041')
        self.assertEqual(lu, 'la facture numéro deux mille vingt-six tiret zéro quarante et un')
        self.assertNotRegex(lu, MOIS)
        self.assertEqual(D('décision n° 2025-1001 QPC'), 'décision numéro deux mille vingt-cinq tiret mille un question prioritaire de constitutionnalité')

    def test_L03_reference_dossier(self):
        self.assertEqual(D('le dossier DOS-001'), 'le dossier DOS zéro zéro un')

    def test_L03_relecture_idempotente(self):
        texte = "Me DURAND : audience le 30/10/2026 de 9h à 10h30, n° 22-12.345, 1 800 € HT, ILC, ch. civ., Les art. 1103 et 1104."
        lu = D(texte)
        self.assertEqual(D(lu), lu)

    def test_L03_cinquante_phrases_sans_chiffre_lu_de_travers(self):
        phrases = json.loads(DONNEES.read_text(encoding='utf-8'))
        self.assertEqual(len(phrases), 50)
        noms = {'DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC', 'DOS', 'II'}
        for p in phrases:
            lu = D(p['texte'])
            with self.subTest(n=p['n'], lu=lu):
                self.assertNotRegex(lu, r'\d\s?[hH](?!\w)|\d[hH]\d')                    # heure en chiffres
                self.assertNotRegex(lu, r'\d[/.]\d')                                        # date numérique, pourvoi, rôle
                self.assertNotRegex(lu, r'\d-\d')                                           # tiret lu « moins »
                self.assertNotRegex(lu, r'\d' + '\\s' + MOIS + '|' + MOIS + r'\s\d')        # jour ou année en chiffres
                self.assertNotRegex(lu, r'\d(?:er|re|ère|e|ème)\b')                         # ordinal en chiffres
                self.assertNotRegex(lu, r'[°§€%]|\b(?:art|ch|civ|com|soc|crim|const|Cass|al)\.|\bnos\s\d|K-bis')
                restes = set(re.findall(r'\b[A-Z][A-Za-z]*[A-Z]+[a-z]*\b', lu)) - noms   # sigle ni épelé ni développé
                self.assertEqual(restes, set())
        lus = {p['n']: D(p['texte']) for p in phrases}
        self.assertIn('vendredi trente octobre deux mille vingt-six à neuf heures trente', lus[1])
        self.assertIn('de neuf heures à dix heures trente, puis visioconférence à onze heures', lus[47])
        self.assertIn('numéro vingt-deux, douze, trois cent quarante-cinq', lus[24])
        self.assertIn('numéro vingt-trois, dix, zéro zéro un', lus[25])
        self.assertIn('Code des procédures civiles', lus[18])


if __name__ == '__main__':
    unittest.main()
