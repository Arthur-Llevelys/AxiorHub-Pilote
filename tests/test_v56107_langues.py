"""5.6.107 (lot T) : interface en anglais, allemand et espagnol pour les nouveautés 5.6.106 et 5.6.107.

Mécanisme existant, sans second mécanisme : catalogues ``i18n5631`` (clés) et dictionnaire des écrans ``ecrans5632`` (phrase française
relevée → traduction), appliqué à la sortie du serveur et, pour les textes écrits par les scripts, par ``v5634t.js`` (mêmes règles).
- T1 : aucune clé manquante dans les quatre langues ; « % s » et « # » dans le même nombre et le même ordre ;
- T2 : aucun texte français resté dans les écrans nouveaux en anglais, allemand et espagnol (sections du serveur, bandeau du mode Démo,
  textes des scripts v56106-*.js et v56107-demo.js, messages des modules) ;
- T3 : français inchangé à l'octet près (page française jamais retouchée ; échantillon de textes français d'origine) ;
- T4 : libellés courts dans les barres denses (Courriels, rédaction) ;
- T5 : les scripts ne repèrent plus un bouton par son infobulle française seule (sinon, en anglais : boutons non regroupés, Ctrl+K cassé).
Données fictives, aucun réseau. Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import ecrans5632 as E
from agent import i18n5631 as I
from agent.common import private_json
from agent.desk import Desk
import test_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
LANGUES = ('en', 'de', 'es')
SKIP = re.compile(r'<(script|style|textarea|pre|code)\b[^>]*>.*?</\1\s*>', re.S | re.I)
TEXTE = re.compile(r'(?:^|>)([^<>]+)(?=<|$)')
ATTRIBUT = re.compile(r'\b(?:placeholder|title|aria-label|alt|data-confirm)="([^"]*)"')
# mots français fréquents absents de l'anglais, de l'allemand et de l'espagnol (ni « la », « le », « de » : espagnol ; ni « des » : allemand)
FRANCAIS = re.compile(r"\b(?:les|du|une|est|sont|pour|dans|avec|sans|aucun|aucune|vous|votre|vos|cette|ces|puis|été|ou|au|aux|"
                      r"courriels?|messagerie|réglages?|enregistr\w*|brouillons?|pièces?|tâches?|échéances?|rechercher|supprimer|"
                      r"envoyée?s?|fichiers?|modèles?|résumé|démonstration|données|fictives?|jamais|traiter|traitée?s?|proposée?s?|retenir|rejeter|déposer|afficher|écritures?|probantes?|prochaines?|reçue?s?|enregistrée?s?|synchronisée?|relire|propositions|retour|pleine|attente|calcul|validée?s?|classée?s?|préparée?s?|numérotée?s?|actualisée?|modifiée?|vérifiée?|autres|demain|aujourd’hui|cabinet)\b|\b(?:l|d|n|qu|j|s)’\w|\b\d{1,2} h \d{2}\b", re.I)
# noms propres et données fictives qui peuvent figurer tels quels dans une page traduite
PERMIS = ('Cabinet DURAND Avocats', 'AxiorHub', 'avocat.fr', 'A TRAITER', 'CORRESPONDANCES', 'Recherche publique : ')
JS_STR = re.compile(r"'((?:[^'\\\n]|\\.)*)'")
# morceaux de phrases assemblées par les scripts (la phrase complète est au dictionnaire : voir T2_scripts_composes)
MORCEAUX = {'Heure du cabinet', 'Préparation du formulaire', 'éditeur est celui d', 'Failed to fetch'}


def visibles(html):
    html = SKIP.sub('<x>', html)                                       # comme ecrans5632 : un bloc mis à part coupe le texte en deux
    out = [m.group(1) for m in TEXTE.finditer(html)] + [m.group(1) for m in ATTRIBUT.finditer(html)]
    return [re.sub(r'\s+', ' ', t).strip() for t in out if re.search(r'[A-Za-zÀ-ÿ]', t)]


def reste_francais(textes):
    out = []
    for t in textes:
        nu = re.sub(r'\S*example\.test\S*', '', t)                     # adresses d'exemple (donnée, pas texte d'interface)
        for p in PERMIS:
            nu = nu.replace(p, '')
        if FRANCAIS.search(nu):
            out.append(t)
    return out


def litteraux_js(path):
    for no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if re.match(r'^\s*(//|/\*|\*)', line):
            continue
        for m in JS_STR.finditer(line.split(' // ')[0]):
            yield no, m.group(1).replace("\\'", "'")


def texte_visible_js(s):
    """Phrase complète écrite par un script (pas un sélecteur, une clé, ni un morceau assemblé avec une donnée)."""
    if s != s.strip() or not re.search(r'[a-zà-ÿ]{2,} [a-zà-ÿ«]', s) or s in MORCEAUX:
        return False
    if s.endswith(('«', '(', ':', '—', ',')) or s.startswith((')', '»', '.', ',', ':', ';', '·', '#')):
        return False
    return not (re.match(r'^[a-z]', s) or re.search(r'[{}<>=\\]|^\[', s))


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        p = patch('agent.common.HTTP.request', side_effect=AssertionError('réseau'))
        p.start()
        self.addCleanup(p.stop)

    def sections(self):
        """Écrans nouveaux rendus par le serveur (Paramètres : avocat.fr, SearXNG, OCR, densité et logo, images pour l'IA ; bandeau Démo)."""
        from agent import avocatfr56106, demo56107, identite56106, outils_ocr56106, searxng56106, vision56106
        out = {'avocat.fr': avocatfr56106.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', {'axiorhub.config_path': str(self.config)}),
               'searxng': searxng56106.section_html(self.desk),
               'ocr': outils_ocr56106.section_html(self.desk, '/p', 'administrateur'),
               'identite': identite56106.section_html(self.desk, '/p', 'administrateur'),
               'vision': vision56106.reglages_html(self.desk, '/p', 'administrateur')}
        with patch.object(demo56107, 'actif', lambda cfg: True):
            out['bandeau demo'] = demo56107.bandeau_html('/p', {'demo56107': {'racine': str(self.f.base)}})
        for nom, html in out.items():
            self.assertTrue(html and len(html) > 200, nom)
        return out


# --------------------------------------------------------------------------------------------- T1 : clés
class Cles(unittest.TestCase):
    def test_T1_catalogues_i18n_complets_dans_les_quatre_langues(self):
        self.assertEqual(I.check(), [])

    def test_T1_dictionnaires_des_ecrans_memes_cles_et_memes_variables(self):
        tables = {l: E.dictionary(l) for l in LANGUES}
        self.assertEqual(set(tables['en']), set(tables['de']))
        self.assertEqual(set(tables['en']), set(tables['es']))
        for lang, table in tables.items():
            mauvais = [k for k, v in table.items() if re.findall(r'%s|#', k) != re.findall(r'%s|#', v)]
            self.assertEqual(mauvais, [], lang)                       # même nombre ET même ordre (ordre de remplissage des valeurs)
            self.assertEqual([k for k, v in table.items() if not v.strip()], [], lang)

    def test_T1_fichiers_json_valides_sans_doublon(self):
        for lang in LANGUES:
            paires = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'), object_pairs_hook=list)
            cles = [k for k, _ in paires]
            self.assertEqual(len(cles), len(set(cles)), lang)

    def test_T1_contre_exemple_variable_dans_le_mauvais_ordre_detectee(self):
        k, v = 'Largeur : %s (# px)', 'Width (# px): %s'                 # nombre et donnée inversés : valeurs mal placées à l'écran
        self.assertEqual((k.count('%s'), k.count('#')), (v.count('%s'), v.count('#')))   # le seul compte ne verrait rien
        self.assertNotEqual(re.findall(r'%s|#', k), re.findall(r'%s|#', v))


# --------------------------------------------------------------------------------------------- T2 : rien de français dans EN, DE, ES
class Ecrans(Base):
    def test_T2_sections_du_serveur_traduites(self):
        for nom, html in self.sections().items():
            for lang in LANGUES:
                restes = reste_francais(visibles(E.translate_html(html, lang)))
                self.assertEqual(restes, [], '%s (%s)' % (nom, lang))

    def test_T2_contre_exemple_le_detecteur_voit_le_francais(self):
        html = self.sections()['bandeau demo']
        self.assertTrue(reste_francais(visibles(html)))                # page française : le détecteur trouve bien du français
        self.assertIn('Mode démonstration — données fictives', html)

    def test_T2_scripts_nouveaux_toutes_les_phrases_au_dictionnaire(self):
        tables = {l: E.dictionary(l) for l in LANGUES}
        fichiers = sorted(STATIC.glob('v5610[67]*.js'))
        self.assertGreaterEqual(len(fichiers), 9)
        manquants, vus = [], 0
        for path in fichiers:
            for no, s in litteraux_js(path):
                if not texte_visible_js(s):
                    continue
                vus += 1
                for lang, table in tables.items():
                    if E.phrase(s, table) is None:
                        manquants.append('%s:%d (%s) %s' % (path.name, no, lang, s))
        self.assertGreater(vus, 400)
        self.assertEqual(manquants, [])

    def test_T2_scripts_composes(self):
        """Phrases assemblées par les scripts avec un nombre ou une donnée, telles qu'elles s'affichent."""
        echantillon = ('Déplacer ces 3 courriels dans la Corbeille ? Ils restent récupérables dans la messagerie.', '3 marqués lus.',
                       '3 déplacés dans la Corbeille, 2 en échec (déjà déplacé ou supprimé).', 'Rechercher dans : objet, expéditeur',
                       'Trier et filtrer, 2 filtre(s) actif(s)', 'Actualiser la liste (F5) — à jour à 16 h 42',
                       'Envoyé le mardi 17 novembre 2026 à 09 h 00 — Envoyé', 'Programmé le 17/11/2026 à 09 h 00 (heure du cabinet)',
                       'Heure du cabinet (Europe/Paris). Le message reste dans Brouillons jusqu’à son départ.', 'Couleur : Rouge',
                       '2 pièces et 1 image · 1,2 Mo sur 15 Mo · 10 fichiers au plus', 'Brouillon non enregistré (erreur). Nouvel essai dans 30 s.',
                       'Étape 2 sur 8 · Entrée ou → : suivant · ← : précédent · Échap : passer', '☑ Actualiser toutes les 2 min (page visible)',
                       '🏷 Marquer ▾', '● Dictée rapide 0:12 — aucune donnée confidentielle. Cliquez pour arrêter.', 'Conversation (3 messages)',
                       'Résumé par l’IA — 2 courriels', 'Images bloquées (4) : des images extérieures peuvent signaler à l’expéditeur que vous avez lu ce courriel.',
                       'Courriel potentiellement frauduleux — Danger', 'Supprimer le modèle « Relance DURAND » pour tout le cabinet ?')
        for lang in LANGUES:
            table = E.dictionary(lang)
            for s in echantillon:
                got = E.phrase(s, table)
                self.assertIsNotNone(got, (lang, s))
                self.assertEqual(reste_francais([got.replace('DURAND', '')]), [], (lang, s, got))
        self.assertEqual(E.phrase('Envoyé le mardi 17 novembre 2026 à 09 h 00 — Envoyé', E.dictionary('de')),
                         'Gesendet am Dienstag, 17. November 2026 um 09:00 Uhr — Versendet')                  # heure allemande : « 9:30 Uhr »
        self.assertEqual(E.phrase('Déplacer ces 3 courriels dans la Corbeille ? Ils restent récupérables dans la messagerie.', E.dictionary('es')),
                         '¿Mover estos 3 correos a la Papelera? Siguen siendo recuperables en el correo.')

    def test_T2_messages_des_modules_traduits(self):
        import importlib
        tables = {l: E.dictionary(l) for l in LANGUES}
        manquants = []
        for nom in ('avocatfr56106', 'boite56106', 'envoi56106', 'fraude56106', 'identite56106', 'lecteur56106', 'outils_ocr56106',
                    'redaction56106', 'routine_message56106', 'searxng56106', 'vision56106', 'demo56107'):
            mod = importlib.import_module('agent.' + nom)
            for attr in ('TEXTES_PARAMETRES', 'MESSAGES', 'ETATS', 'MOTIFS', 'CONSEILS', 'LIBELLES', 'ACTIONS', 'GENRES', 'ETATS_ELEMENT', 'ETATS_PASSAGE', 'COURTS',
                         'LIBELLES_DENSITE', 'LIBELLES_ENVOI', 'NATURES', 'MODES'):
                valeurs = getattr(mod, attr, None)
                if not isinstance(valeurs, dict):
                    continue
                for v in valeurs.values():
                    v = v % 30 if isinstance(v, str) and '%d' in v else v          # TEXTES_PARAMETRES (lot F2) : nombre de courriels examinés
                    if isinstance(v, str) and re.search(r'[a-zà-ÿ]{3}', v) and v not in ('Tavily', 'SearXNG'):
                        manquants += ['%s.%s (%s) %s' % (nom, attr, l, v) for l, t in tables.items() if E.phrase(v, t) is None]
        self.assertEqual(manquants, [])

    def test_T2_notification_json_traduite(self):
        corps = json.dumps({'message': 'Densité d’affichage enregistrée.', 'ok': True}, ensure_ascii=False)
        self.assertEqual(json.loads(E.apply(corps, 'application/json; charset=utf-8', 'de'))['message'], 'Anzeigedichte gespeichert.')


# --------------------------------------------------------------------------------------------- T3 : français inchangé
class Francais(Base):
    ECHANTILLON = {   # textes français d'origine (5.6.106 / 5.6.107), inchangés à l'octet près dans les sources
        'v56106-liste.js': ('Marquer : lu, non lu, suivi, épinglé, indésirable', 'Trier et filtrer (non lus, suivis, pièces jointes, période)',
                            'Déplacer ce courriel dans la Corbeille ? Il reste récupérable dans la messagerie.', 'Marquer comme non lu'),
        'v56106-redaction.js': ('Déposez les fichiers pour les joindre (une image déposée dans le texte y est insérée)', 'Insérer un lien',
                                'Plus de mise en forme et d’outils', 'Enregistrer la sélection comme modèle'),
        'v56106-envoi.js': ('Options d’envoi', 'Annuler cet envoi ? Le message reste dans Brouillons.'),
        'v56106-lecteur.js': ('Imprimer ce courriel (Ctrl+P) : vue d’impression propre, nouvel onglet', '✨ Résumer avec l’IA'),
        'v56107-demo.js': ('1. Le courriel d’abord', 'Passer la visite', 'Précédent'),
    }

    def test_T3_page_francaise_jamais_retouchee(self):
        for nom, html in self.sections().items():
            self.assertIs(E.translate_html(html, 'fr'), html, nom)
            self.assertIs(E.apply(html, 'text/html; charset=utf-8', 'fr'), html, nom)
        corps = '{"message": "Densité d’affichage enregistrée."}'
        self.assertIs(E.apply(corps, 'application/json', 'fr'), corps)

    def test_T3_textes_francais_d_origine_presents(self):
        for fichier, textes in self.ECHANTILLON.items():
            source = (STATIC / fichier).read_text(encoding='utf-8')
            for t in textes:
                self.assertTrue("'" + t + "'" in source, (fichier, t))
        html = self.sections()['bandeau demo']
        for t in ('Mode démonstration — données fictives', 'Recommencer la démo', 'Visite guidée', 'Quitter'):
            self.assertIn(t, html)

    def test_T3_traduction_des_scripts_neutre_en_francais(self):
        js = (STATIC / 'v5634t.js').read_text(encoding='utf-8')
        # v5634t.js s'arrête en français avant de définir ax5634Translate : tr() et parTitre() rendent alors le texte d'origine
        self.assertLess(js.index("if (lang === 'fr'"), js.index('window.ax5634Translate'))
        red = (STATIC / 'v56106-redaction.js').read_text(encoding='utf-8')
        self.assertIn('function tr(texte) { return window.ax5634Translate ? window.ax5634Translate(texte) : texte; }', red)


# --------------------------------------------------------------------------------------------- T4 : densité
class Densite(unittest.TestCase):
    LIBELLES = ('Marquer ▾', 'Trier, filtrer', 'Plus', 'Dans ▾', 'Récents', 'Anciens', 'Comme lu', 'Comme non lu', 'Déplacer…', 'Supprimer',
                'Archiver', '📂 Dossier client', 'Sous-dossier client', 'Modèles', 'Imprimer', 'Enregistrer sous', 'Conversation', 'Créer une tâche',
                'Résumer avec l’IA', 'Tâche', 'Agenda', 'Routine…', 'Mis en forme', 'Texte', 'Nouveau', 'Liste', 'Lecteur', 'Tout cocher',
                'Suivi (drapeau)', 'Épingler', 'Désépingler', 'Origine', 'Retirer', 'Petite', 'Moyenne', 'Joindre une image', 'Envois programmés',
                'Visite guidée', 'Recommencer la démo', 'Quitter', 'Options d’envoi')

    def test_T4_libelles_des_barres_denses_courts(self):
        for lang in LANGUES:
            table = E.dictionary(lang)
            trop = ['%s → %s' % (k, table[k]) for k in self.LIBELLES if len(table[k]) > len(k) + 4]
            self.assertEqual(trop, [], lang)


# --------------------------------------------------------------------------------------------- T5 : repères indépendants de la langue
class Reperes(unittest.TestCase):
    def test_T5_aucun_bouton_repere_par_une_infobulle_francaise_seule(self):
        for path in sorted(STATIC.glob('v5610[67]*.js')):
            source = path.read_text(encoding='utf-8')
            self.assertEqual(re.findall(r"""\[title="[^"]*[A-Za-zÀ-ÿ]{3}[^"]*"\]|\[title="' \+""", source), [], path.name)
        liste = (STATIC / 'v56106-liste.js').read_text(encoding='utf-8')
        self.assertIn('b.title === titre || b.title === traduit', liste)
        self.assertIn("var lien = parTitre(bar, 'Insérer un lien'); if (lien) lien.click();", (STATIC / 'v56106-redaction.js').read_text(encoding='utf-8'))

    def test_T5_contre_exemple_ancien_selecteur_detecte(self):
        ancien = """var b = $('[title="' + t + '"]', bar);  bar.querySelector('[title="Insérer un lien"]').click();"""
        self.assertEqual(len(re.findall(r"""\[title="[^"]*[A-Za-zÀ-ÿ]{3}[^"]*"\]|\[title="' \+""", ancien)), 2)



# --------------------------------------------------------------------------------------------- T6 : dates et heures au format local
class Dates(unittest.TestCase):
    def test_T6_dates_du_code_au_format_allemand(self):
        de = E.dictionary('de')
        for fr, attendu in (('26/11/2026 09:30', '26.11.2026 09:30 Uhr'), ('17/11 10:02', '17.11. 10:02'), ('26/11/2026', '26.11.2026'),
                            (' 17/11/2026 ', ' 17.11.2026 ')):
            self.assertEqual(E.phrase(fr, de), attendu)
        self.assertEqual(E.phrase('Résumé par l’IA de 2 courriel(s), le 17/11/2026 11:02 — à relire par l’avocat.', de),
                         'KI-Zusammenfassung von 2 E-Mail(s), am 17.11.2026 11:02 Uhr — vom Anwalt zu prüfen.')
        self.assertEqual(E.translate_html('<span>17/11 10:02</span><p>RG 24/01234</p>', 'de'), '<span>17.11. 10:02</span><p>RG 24/01234</p>')
        self.assertEqual(E.translate_html('<p>Montag, 16/11/2026</p><p>(30/11/2026)</p><p>Seite 1/40</p>', 'de'),   # dates au milieu d'un texte
                         '<p>Montag, 16.11.2026</p><p>(30.11.2026)</p><p>Seite 1/40</p>')
        self.assertEqual(E.phrase('Jeudi 19 novembre, 10 h – 10 h 30 : créer', de), 'Donnerstag, 19. November, 10:00 – 10:30 Uhr: anlegen')

    def test_T6_contre_exemple_autres_langues_et_francais_inchanges(self):
        for lang in ('en', 'es'):
            self.assertIsNone(E.phrase('26/11/2026 09:30', E.dictionary(lang)))      # format JJ/MM/AAAA gardé (cohérent avec le reste)
        html = '<span>17/11 10:02</span>'
        self.assertIs(E.translate_html(html, 'fr'), html)
        self.assertIsNone(E.date_locale('17/11 10:02', {}))                               # sans « _dates » : aucune conversion
        js = (STATIC / 'v5634t.js').read_text(encoding='utf-8')
        self.assertIn("table._dates === 'point'", js)                                     # même règle dans le navigateur
        self.assertIn('|| dateLocale(seg)', js)

    def test_T6_heures_locales(self):
        attendu = {'en': 'Today · 9:30', 'de': 'Heute · 9:30 Uhr', 'es': 'Hoy · 9:30'}
        for lang, valeur in attendu.items():
            self.assertEqual(E.phrase('Aujourd’hui · 9 h 30', E.dictionary(lang)), valeur)


if __name__ == '__main__':
    unittest.main()
