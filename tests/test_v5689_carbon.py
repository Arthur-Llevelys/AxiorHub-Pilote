"""5.6.89, lot Carbon : jetons, drapeaux, accueil, retour contextuel, FormAdapter (audit 5.6.88, chap. 10 à 12, 14 à 17, 23).

Tests sans navigateur : logique, HTML rendu par l'application réelle, et banc Node du FormAdapter (``formulaires5689.cjs``). Largeurs, clavier, contrastes mesurés et impression se vérifient dans un
vrai navigateur : ce fichier en contrôle les conditions nécessaires, il ne les prouve pas."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import quote

import test_v5648 as base

from agent import accueil5689, cockpit530, fonctions5631, livrables5689 as liv, shell501, standalone_auth, tableau5688, ui_components5689 as uc, ui_drapeaux5689 as dr, ui_navigation5689 as nav, ui_tokens5689 as tok, web5689
from agent.ui_icones5689 import ICONES

STATIC = Path(cockpit530.__file__).parent / 'static'
HERE = Path(__file__).parent
ROOT = HERE.parent
P = '/agent-courriel'
MID = 'DOS-001'
TITRE_LONG = 'Conclusions récapitulatives d’appel incident après renvoi de cassation et jonction des procédures devant la chambre commerciale de Valbrune, version annotée pour relecture'


class Identifiants(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids += [v for k, v in attrs if k == 'id' and v]


def identifiants(html):
    p = Identifiants()
    p.feed(html)
    return p.ids


def document(desk, ident, kind='conclusions', title='Conclusions en défense', matter=MID, state='verified', review='to_review', jours=0):
    from datetime import datetime, timedelta, timezone
    liv.ensure_schema(desk)
    stamp = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    desk.db.execute('INSERT INTO prepared_items5689(id,document_id,version_id,version_number,matter_id,kind,title,source_service,source_object_id,source_version,production_state,lifecycle,review_state,source_at,created,updated) '
                    "VALUES(?,?,?,2,?,?,?,'essai',?,'1',?,'current',?,?,?,?)", (ident, 'doc-' + ident, 'v-' + ident, matter, kind, title, ident, state, review, stamp, stamp, stamp))
    desk.db.commit()


class Base(base.Base):
    def page(self, path='/aujourdhui', query=''):
        r = self.call(path, query=query)
        self.assertTrue(r['status'].startswith('200'), (path, r['status']))
        return r['body'].decode('utf-8')


class RetourContextuel(unittest.TestCase):
    MAUVAIS = ('https://evil.example.test/', 'http://evil.example.test', '//evil.example.test', '///evil.example.test', '/\\evil.example.test', '\\\\evil.example.test', 'javascript:alert(1)', 'JaVaScRiPt:alert(1)',
               ' javascript:alert(1)', 'data:text/html,x', '/agent-courriel/\tx', '/agent-courriel/x\ny', '/agent-courriel/x\r\nSet-Cookie: a=b', '/agent-courriel/%0d%0aSet-Cookie:a', '/agent-courriel/%09x', '/agent-courriel/%00',
               '/agent-courriel//evil.example.test', '/agent-courriel/%2f%2fevil.example.test', '/agent-courriel/%2F%2Fevil.example.test', '/agent-courriel/%252f%252fevil.example.test', '/agent-courriel/%5cevil.example.test',
               '/agent-courriel/%255cevil.example.test', '/agent-courriel/../../evil', '/agent-courriel/%2e%2e/%2e%2e/evil', '/agent-courriel/./x', '/agent-courriel/／evil', '/agent-courriel/%EF%BC%8Fevil.example.test',
               '/autre-application/x', 'aujourdhui', '', '/agent-courriel/' + 'x' * 700, '/agent-courriel/deconnexion', '/agent-courriel/api440/m530/stop', '/agent-courriel/static/app520.css',
               '/agent-courriel/courriels?token=abc', '/agent-courriel/courriels?csrf=abc', '/agent-courriel/courriels?a=1&password=abc', '/agent-courriel/courriels?Access_Token=abc', '/agent-courriel/x?u=javascript:alert(1)',
               '/agent-courriel/%25252f%25252fevil')
    BONS = ('/agent-courriel/aujourdhui', '/agent-courriel/courriels?vue=boite&q=calendrier%20DURAND&page=3', '/agent-courriel/livrables?nature=contrat&curseur=abc', '/agent-courriel/dossier?id=DOS-001&onglet=pieces')

    def test_UX03_retour_refuse_chaque_redirection_ouverte(self):
        for valeur in self.MAUVAIS:
            self.assertEqual(nav.valider_retour(valeur, P), '', repr(valeur))
        self.assertEqual(nav.valider_retour(None, P), '')
        self.assertEqual(nav.valider_retour(['/agent-courriel/x'], P), '')

    def test_UX03_retour_accepte_les_adresses_de_l_application(self):
        for valeur in self.BONS:
            self.assertEqual(nav.valider_retour(valeur, P), valeur)
        self.assertEqual(nav.valider_retour('/aujourdhui', ''), '/aujourdhui')          # sans préfixe de déploiement

    def test_UX03_mutation_chaque_garde_fou_compte(self):
        """Le test échoue si un contrôle disparaît : chaque mutation laisse passer au moins une valeur dangereuse."""
        with patch.object(nav, '_douteux', lambda texte: False):
            passe = [v for v in self.MAUVAIS if nav.valider_retour(v, P)]
        self.assertIn('/agent-courriel/%2f%2fevil.example.test', passe)
        self.assertIn('/agent-courriel/%5cevil.example.test', passe)
        self.assertIn('/agent-courriel/../../evil', passe)
        with patch.object(nav, 'EXCLUS', re.compile('^$')):
            self.assertEqual(nav.valider_retour('/agent-courriel/deconnexion', P), '/agent-courriel/deconnexion')
        with patch.object(nav, 'PARAMS_INTERDITS', re.compile('^$')):
            self.assertTrue(nav.valider_retour('/agent-courriel/courriels?token=abc', P))
        origine = nav.valider_retour
        with patch.object(nav, 'TAILLE_MAX', 10 ** 6):
            self.assertTrue(origine('/agent-courriel/' + 'x' * 700, P))

    def test_UX03_repli_de_chaque_detail_sans_contexte(self):
        self.assertEqual(nav.retour(P, '/mail', {'key': 'k'}), (P + '/courriels', 'Retour aux courriels'))
        self.assertEqual(nav.retour(P, '/dossier', {'id': 'x'}), (P + '/dossiers', 'Retour aux dossiers'))
        self.assertEqual(nav.retour(P, '/livrables/abc', {}), (P + '/livrables', 'Retour aux documents'))
        self.assertEqual(nav.retour(P, '/facturation', {'document': 'd'}), (P + '/facturation', 'Retour à la facturation'))
        self.assertIsNone(nav.retour(P, '/facturation', {}))                             # la liste elle-même : pas de retour
        self.assertIsNone(nav.retour(P, '/aujourdhui', {}))

    def test_UX02_retour_garde_filtre_recherche_et_page_de_la_liste(self):
        cible = P + '/courriels?vue=boite&q=calendrier&page=3'
        self.assertEqual(nav.retour(P, '/mail', {'key': 'k', 'return_to': cible}), (cible, 'Retour aux courriels'))
        self.assertEqual(nav.retour(P, '/mail', {'key': 'k', 'return_to': P + '/aujourdhui'})[1], 'Retour à Aujourd’hui')
        self.assertEqual(nav.retour(P, '/mail', {'key': 'k', 'return_to': P + '/dossier?id=DOS-001'})[1], 'Retour au dossier')


class Jetons(unittest.TestCase):
    PAIRES = (('text-primary', 'background'), ('text-primary', 'layer-01'), ('text-primary', 'layer-02'), ('text-secondary', 'background'), ('text-secondary', 'layer-01'), ('link-primary', 'background'), ('link-primary', 'layer-01'),
              ('support-success', 'layer-01'), ('text-error', 'layer-01'), ('ui-ia', 'layer-01'), ('ui-attention-texte', 'layer-01'), ('ui-attention-texte', 'ui-attention-doux'), ('text-primary', 'ui-erreur-doux'), ('text-primary', 'ui-succes-doux'),
              ('text-primary', 'ui-ia-doux'), ('text-primary', 'ui-action-doux'), ('interactive', 'ui-action-doux'), ('ui-nav-texte', 'ui-nav'), ('ui-nav-texte', 'ui-nav-actif'), ('ui-sur-action', 'interactive'), ('ui-sur-action', 'ui-ia'))

    def test_UX08_contrastes_du_texte_en_clair_et_en_sombre(self):
        for theme in (tok.CLAIR, tok.SOMBRE):
            table = tok.jetons(theme)
            for texte, fond in self.PAIRES:
                self.assertGreaterEqual(tok.contraste(table[texte], table[fond]), 4.5, (theme, texte, fond))

    def test_UX08_charte_du_chapitre_16(self):
        clair = tok.jetons(tok.CLAIR)
        self.assertEqual((clair['background'], clair['layer-01'], clair['text-primary'], clair['text-secondary'], clair['interactive'], clair['ui-ia'], clair['support-success'], clair['support-error']),
                         ('#f4f4f4', '#ffffff', '#161616', '#525252', '#0f62fe', '#6929c4', '#198038', '#da1e28'))
        self.assertEqual(tok.jetons(tok.SOMBRE)['background'], '#161616')

    def test_UX08_les_variables_existantes_suivent_le_theme(self):
        feuille = tok.feuille()
        clair = re.search(r'\nbody\[data-carbon\]\{(.*?)\}', feuille, re.S).group(1)
        sombre = re.search(r':root\[data-theme=dark\] body\[data-carbon\]\{(.*?)\}', feuille, re.S).group(1)
        for variable in ('--ws-paper', '--ws-surface', '--ws-ink', '--ws-muted', '--ws-accent', '--ws-border', '--ax-bg', '--ax-card', '--ax-ink', '--ax-mute', '--ax-line', '--ax-accent', '--ax-accent-ink'):
            self.assertRegex(clair, re.escape(variable) + r':#')
            self.assertRegex(sombre, re.escape(variable) + r':#')
        self.assertIn('--ws-paper:#f4f4f4', clair)
        self.assertIn('--ws-paper:#161616', sombre)
        self.assertIn('--surface:#262626', sombre)      # alias calculés sur la racine : repris sur <body>

    def test_UX08_chaque_jeton_lu_par_les_feuilles_existe(self):
        table = tok.jetons(tok.CLAIR)
        lus = set(re.findall(r'var\(--cds-([a-z0-9-]+)', (STATIC / 'v5689-carbon.css').read_text(encoding='utf-8')))
        self.assertFalse(lus - set(table), lus - set(table))

    def test_UX08_feuille_generee_a_jour(self):
        self.assertEqual((STATIC / 'v5689-carbon-jetons.css').read_text(encoding='utf-8'), tok.feuille(), 'python scripts/build-css.py')

    def test_UX08_accents_5688_repris_avec_controle_de_contraste(self):
        from agent.tableau5688 import ACCENTS
        feuille = tok.feuille()
        for nom in ACCENTS:
            if nom:
                self.assertTrue(tok.accent_conforme(nom), nom)
                self.assertIn('[data-accent=%s]' % nom, feuille)
        illisible = {**ACCENTS, 'jaune': ('Jaune', ('#f1c21b', '#d2a106', '#fcf4d6'), ('#f1c21b', '#fddc69', '#302400'))}
        with patch.object(tableau5688, 'ACCENTS', illisible):
            self.assertFalse(tok.accent_conforme('jaune'))
            self.assertNotIn('[data-accent=jaune]', tok.feuille())       # jamais émise : la personne garde le bleu


class Composants(unittest.TestCase):
    def test_UX09_icone_decorative_ignoree_icone_seule_nommee(self):
        self.assertIn('aria-hidden="true"', uc.icone('accueil'))
        self.assertNotIn('aria-label', uc.icone('accueil'))
        nommee = uc.icone('fermer', 'Fermer le panneau')
        self.assertIn('role="img"', nommee)
        self.assertIn('aria-label="Fermer le panneau"', nommee)
        self.assertNotIn('aria-hidden', nommee)

    def test_UX09_icones_verifiees_dans_le_paquet_verrouille(self):
        lock = json.loads((ROOT / 'scripts/carbon5689/package-lock.json').read_text(encoding='utf-8'))
        paquets = lock['packages']
        self.assertEqual(paquets['node_modules/@carbon/web-components']['version'], '2.66.0')
        self.assertEqual(paquets['node_modules/@carbon/web-components']['license'], 'Apache-2.0')
        self.assertFalse([k for k in paquets if k.endswith('/carbon-components')])
        attendus = {'accueil', 'recherche', 'courriel', 'dossier', 'document', 'agenda', 'tache', 'facture', 'societe', 'ia', 'ok', 'alerte', 'retour', 'modifier', 'telecharger', 'pause', 'stop'}
        self.assertFalse(attendus - set(ICONES))
        for nom, svg in ICONES.items():
            self.assertRegex(svg, r'^<(path|polygon|circle|rect|g)', nom)
            self.assertNotRegex(svg, '[-]')

    def test_UX09_bibliotheques_embarquees_sans_ressource_externe(self):
        manifeste = json.loads((ROOT / 'scripts/carbon5689/manifeste.json').read_text(encoding='utf-8'))
        import hashlib
        for chemin, empreinte in manifeste['sorties'].items():
            self.assertEqual(hashlib.sha256((ROOT / chemin).read_bytes()).hexdigest(), empreinte, chemin)
        texte = (STATIC / 'carbon5682.js').read_text(encoding='utf-8')
        adresses = set(re.findall(r'https?://[a-zA-Z0-9./-]+', texte)) - {'http://www.w3.org/2000/svg'}
        self.assertFalse(adresses, adresses)
        for nom in ('v5689-formulaires.js', 'v5689-navigation.js', 'v5689-carbon.css', 'v5689-carbon-jetons.css'):
            self.assertNotRegex((STATIC / nom).read_text(encoding='utf-8'), r'https?://')

    def test_UX08_cinq_etats_distincts(self):
        vus = {g: uc.etat_html(g, 'Titre') for g in uc.ETATS}
        self.assertEqual(len({re.search(r'data-etat="(\w+)"', h).group(1) for h in vus.values()}), 5)
        self.assertIn('role="alert"', vus['erreur'])
        for genre, html in vus.items():
            self.assertIn('Titre', html, genre)      # libellé écrit, pas la seule couleur

    def test_FM01_erreur_de_champ_liee_et_aide_decrite(self):
        html = uc.texte('nom', 'Nom', aide='Tel qu’il figure au dossier', requis=True)
        ident = re.search(r'aria-describedby="([^"]+)"', html).group(1)
        self.assertIn('id="%s"' % ident, html)
        self.assertIn(' required', html)

    def test_FM01_rendu_natif_complet_sans_composant(self):
        """Alternative native : le serveur ne rend aucun champ qui dépendrait d'un composant chargé."""
        html = uc.formulaire('m5689/x', uc.case('a', 'A', True) + uc.radios('r', 'R', (('1', 'Un'), ('2', 'Deux')), '2') + uc.liste('l', 'L', (('a', 'A'),), 'a') + uc.texte('t', 'T') + uc.zone_texte('z', 'Z'))
        self.assertNotIn('<cds-', html)
        for champ in ('type="checkbox" name="a" checked', 'type="radio" name="r" value="2" checked', '<select name="l"', 'name="t"', '<textarea name="z"'):
            self.assertIn(champ, html)

    def test_FM01_filtres_visibles_a_zero_ou_inconnus(self):
        html = uc.filtres('Nature', (('a', 'Tout', 0), ('b', 'Documents', None)), 'a', 'x')
        self.assertIn('Tout <span class="ui89-compte">0</span>', html)
        self.assertIn('à vérifier', html)              # jamais un faux zéro
        self.assertEqual(html.count('role="tab"'), 2)

    def test_AI_point_d_accroche_sans_effet_tant_que_le_drapeau_est_eteint(self):
        self.assertIn('hidden', uc.action_ia_html('dossiers', allume=False))


@unittest.skipUnless(shutil.which('node'), 'node absent')
class FormAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([shutil.which('node'), str(HERE / 'formulaires5689.cjs')], capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert out.returncode == 0, out.stderr
        cls.r = json.loads(out.stdout)

    def test_FM01_case_decochee_radio_liste_et_composants_carbon(self):
        c = self.r['collect']
        self.assertIs(c['coche'], True)
        self.assertIs(c['decoche'], False)            # faux explicite, pas une clé absente
        self.assertEqual(c['r'], 'b')
        self.assertNotIn('sans', c)
        self.assertEqual((c['liste'], c['multi'], c['texte'], c['zone']), ('deux', ['a', 'c'], 'bonjour', 'long texte'))
        self.assertIs(c['cds_faux'], False)
        self.assertIs(c['cds_vrai'], True)
        self.assertEqual((c['cds_liste'], c['cds_texte'], c['cds_radio']), ('v2', 't', 'b'))

    def test_FM01_champ_desactive_et_composant_adjoint_ne_partent_pas(self):
        c = self.r['collect']
        for absent in ('eteint', 'dans_groupe', 'cds_eteint', 'adjoint', 'piece'):
            self.assertNotIn(absent, c)

    def test_FM01_fichier_transmis_et_trop_lourd_refuse(self):
        self.assertEqual(self.r['fichier']['demande'], {'nom': 'a.txt', 'type': 'text/plain', 'taille': 4, 'contenu': 'data:QUJD'})
        self.assertEqual(self.r['trop_lourd'][1], 'gros')

    def test_FM01_champs_requis_erreur_pres_du_champ_sans_envoi(self):
        r = self.r['requis']
        self.assertEqual(r['appels'], 0)
        self.assertEqual(r['erreurs'], 3)                    # texte, radio sans choix, composant Carbon
        self.assertEqual(r['invalide'], 'true')
        self.assertTrue(r['decrit'])
        self.assertEqual(r['frere'], 'ui89-erreur')          # juste après le champ

    def test_FM02_deux_soumissions_un_seul_effet(self):
        r = self.r['double']
        self.assertEqual(r['appels'], 1)
        self.assertTrue(r['occupe'])
        self.assertFalse(r['apres'])
        self.assertIsNone(r['verrou'])
        self.assertEqual(r['succes'], 1)

    def test_FM02_erreur_http_jamais_un_succes(self):
        for cas in ('http_vide', 'http_html', 'metier', 'global'):
            r = self.r[cas]
            self.assertTrue(r['toasts'], cas)
            self.assertTrue(all('bad' in t[0] for t in r['toasts']), (cas, r['toasts']))
            self.assertTrue(r['erreur'], cas)
            self.assertFalse(r['bouton'], cas)            # le bouton revient : l'erreur se corrige
            self.assertIsNone(r['verrou'], cas)

    def test_FM02_erreur_serveur_pres_du_champ_designe(self):
        self.assertEqual(self.r['metier']['invalide'], 'true')
        self.assertEqual(self.r['metier']['erreur'], 'Valeur refusée.')
        self.assertIsNone(self.r['global']['invalide'])   # sans champ connu : message d'ensemble
        self.assertEqual(self.r['global']['erreur'], 'Conflit de version.')

    def test_FM02_nouvelle_tentative_apres_erreur_efface_le_message(self):
        r = self.r['reprise']
        self.assertEqual(r['appels'], 2)
        self.assertEqual(r['erreurs'], 0)
        self.assertEqual(r['toasts'][-1], 'Fait.')

    def test_FM02_mutation_le_controle_http_compte(self):
        """Sans la règle « réponse non ok », un corps vide sur erreur 500 serait affiché comme un succès."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / 'tests').mkdir()
            (racine / 'agent' / 'static').mkdir(parents=True)
            shutil.copyfile(HERE / 'formulaires5689.cjs', racine / 'tests' / 'formulaires5689.cjs')
            shutil.copyfile(STATIC / 'v5689-formulaires.js', racine / 'agent/static/v5689-formulaires.js')
            source = (STATIC / 'v500.js').read_text(encoding='utf-8')
            self.assertIn('if (r.ok === false && !(d && d.error))', source)
            (racine / 'agent/static/v500.js').write_text(source.replace('if (r.ok === false && !(d && d.error))', 'if (false)'), encoding='utf-8')
            out = subprocess.run([shutil.which('node'), str(racine / 'tests/formulaires5689.cjs')], capture_output=True, text=True, encoding='utf-8', timeout=120)
        r = json.loads(out.stdout)['http_vide']
        self.assertFalse(all('bad' in t[0] for t in r['toasts']))


class Drapeaux(Base):
    def test_OP04_defauts_d_une_mise_a_jour(self):
        self.assertEqual(dr.lire(self.desk), {'theme': True, 'accueil': True, 'actions_ia': True})
        self.assertTrue(dr.etat(self.desk)['registre'])

    def test_OP04_actions_ia_reglables_case_absente_vaut_eteint(self):
        self.assertFalse(dr.enregistrer(self.desk, {'theme': True, 'accueil': True})['drapeaux']['actions_ia'])
        self.assertFalse(dr.lire(self.desk)['actions_ia'])
        self.assertTrue(dr.enregistrer(self.desk, {'theme': True, 'accueil': True, 'actions_ia': True})['drapeaux']['actions_ia'])
        self.assertTrue(dr.lire(self.desk)['actions_ia'])

    def test_OP04_reglage_reserve_a_l_administrateur(self):
        for role, attendu in (('administrateur', True), ('avocat', False), ('assistant', False)):
            self.assertEqual(standalone_auth.allowed(role, 'POST', '/api440/m5689/ui/drapeaux'), attendu, role)
        self.assertTrue(standalone_auth.allowed('assistant', 'GET', '/api440/m5689/ui/drapeaux'))
        self.assertFalse(web5689.allowed('avocat', 'ui/inconnue'))

    def test_OP04_mutation_la_route_refait_le_controle_de_role(self):
        from agent import ui_web5689
        with patch('agent.web567.actor', return_value=('assistant@example.test', 'assistant')), patch('agent.web440.check_post', return_value={'theme': False}):
            r = ui_web5689.api({}, self.desk, {}, P, 'm5689/ui/drapeaux', {}, 'POST')
        self.assertIn('403', str(r))
        self.assertTrue(dr.lire(self.desk)['theme'])

    def test_OP04_route_reelle_enregistre_et_refuse_sans_csrf(self):
        r = self.call('/api440/m5689/ui/drapeaux', 'POST', {'theme': False, 'accueil': True}, csrf=False)
        self.assertFalse(r['status'].startswith('200'), r['status'])
        self.assertTrue(dr.lire(self.desk)['theme'])
        r = self.call('/api440/m5689/ui/drapeaux', 'POST', {'theme': False, 'accueil': True})
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertFalse(dr.lire(self.desk)['theme'])

    def test_OP04_desactiver_rend_l_ancienne_interface_sans_rien_perdre(self):
        document(self.desk, 'a1', title='Conclusions pour ZORVAL')
        self.desk.db.execute("INSERT INTO settings VALUES('decisions5614:deferred', '{\"m:1\": \"2999-01-01\"}')")
        self.desk.db.commit()
        avant = self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0]
        nouveau = self.page()
        self.assertIn('data-carbon="1"', nouveau)
        self.assertRegex(nouveau, r'<html lang="\w+">')     # la racine reste telle quelle
        self.assertIn('id="a89-travail"', nouveau)
        dr.enregistrer(self.desk, {'theme': False, 'accueil': False})
        ancien = self.page()
        self.assertNotIn('data-carbon', ancien)
        self.assertNotIn('id="a89-travail"', ancien)
        self.assertIn('<div class="c530-cols c5657-cols">', ancien)
        self.assertIn('ws-nav-icon" aria-hidden="true">◎', ancien)     # signes d'origine
        for chemin in ('/livrables', '/dossiers', '/parametres', '/production', '/livrables/doc-a1'):
            self.page(chemin)                                            # services et pages restent servis
        self.assertIn('Conclusions pour ZORVAL', self.page('/livrables'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM prepared_items5689').fetchone()[0], avant)
        self.assertEqual(self.desk.settings('decisions5614:deferred'), {'m:1': '2999-01-01'})
        dr.enregistrer(self.desk, {'theme': True, 'accueil': True})     # retour : tout est là
        self.assertIn('Conclusions pour ZORVAL', self.page())

    def test_OP04_accueil_et_theme_sont_independants(self):
        dr.enregistrer(self.desk, {'theme': False, 'accueil': True})
        html = self.page()
        self.assertNotIn('data-carbon', html)
        self.assertIn('id="a89-travail"', html)
        dr.enregistrer(self.desk, {'theme': True, 'accueil': False})
        html = self.page()
        self.assertIn('data-carbon="1"', html)
        self.assertNotIn('id="a89-travail"', html)

    def test_OP04_reglages_visibles_dans_parametres_et_anciennes_vues_rangees(self):
        html = self.page('/parametres', 'rubrique=interface')
        self.assertIn('id="interface5689"', html)
        self.assertIn('data-api="m5689/ui/drapeaux"', html)
        self.assertRegex(html, r'name="theme" checked')
        self.assertRegex(html, r'name="actions_ia" checked')
        self.assertIn('vue=essentiel', html)
        self.assertIn('vue=cockpit', html)
        self.assertNotIn('Ancien cockpit', self.page())                 # plus de concurrence sur l'accueil
        for vue in ('vue=essentiel', 'vue=cockpit'):
            self.page('/aujourdhui', vue)                                # adresses conservées


class Accueil(Base):
    def test_UX05_long_briefing_livrables_d_abord_rapport_a_la_demande(self):
        from agent import routines520
        document(self.desk, 'b1', title='Assignation KERVILAC')
        corps = 'Point du jour sur les dossiers. ' * 400
        with patch.object(routines520, 'latest', lambda desk, kind: {'id': 'r1', 'created': self.desk.now(), 'text': corps, 'notes': []} if kind == 'briefing' else None):
            html = self.page()
        self.assertLess(html.index('id="a89-travail"'), html.index('id="c5614-routines"'))
        self.assertLess(html.index('Assignation KERVILAC'), html.index('Point du jour sur les dossiers'))
        # 5.6.96 (I01, décision du cabinet) : la file « À décider / À relire » monte dans le rang sous « Actuellement », avant Travail préparé et les routines
        self.assertLess(html.index('id="c5656-file"'), html.index('id="a89-travail"'))
        panneau = html[html.index('<dialog class="ui89-panneau" id="a89-rapports"'):]
        self.assertIn('Point du jour sur les dossiers', panneau[:panneau.index('</dialog>')])     # compte rendu dans un panneau fermé
        self.assertNotRegex(html, r'<dialog[^>]* open')

    def test_UX05_resume_court_du_briefing_sans_inventer(self):
        from agent import routines520
        self.assertEqual(accueil5689.resume_briefing(self.desk), '')           # rien de produit : rien d'affiché
        with patch.object(routines520, 'latest', lambda desk, kind: {'text': '**Point du jour.** ' + 'Audience DURAND à préparer. ' * 50}):
            html = accueil5689.resume_briefing(self.desk)
        self.assertLess(len(html), 500)
        self.assertIn('Point du jour.', html)
        self.assertNotIn('**', html)
        self.assertIn('data-tab-open="briefing"', html)

    def test_UX05_routines_et_file_dans_la_meme_colonne_journee_a_cote(self):
        # 5.6.96 (I01) : ordre par défaut : file et journée dans le rang sous « Actuellement » (2 parts / 1 part), le reste inchangé
        html = self.page()
        rang = html[html.index('class="f96-rang"'):html.index('class="a89-layout"')]
        principal = html[html.index('class="a89-principal"'):html.index('class="a89-cote"')]
        cote = html[html.index('class="a89-cote"'):]
        for ident in ('id="a89-travail"', 'id="c5614-routines"'):
            self.assertIn(ident, principal)
        for ident in ('id="c5656-file"', 'id="c530-day"'):
            self.assertIn(ident, rang)
            self.assertNotIn(ident, principal + cote)
        for ident in ('id="t88-matin"', 'id="c530-feed"'):
            self.assertIn(ident, cote)
            self.assertNotIn(ident, principal)
        # ordre choisi par la personne : disposition 5.6.89 conservée (file dans la colonne principale, journée à côté)
        from agent import tableau5688
        ordre = list(tableau5688.IDS)
        ordre.remove('routines')
        tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': ordre + ['routines'], 'accent': ''})
        self.desk.db.commit()
        html = self.page()
        self.assertNotIn('class="f96-rang"', html)
        principal = html[html.index('class="a89-principal"'):html.index('class="a89-cote"')]
        self.assertIn('id="c5656-file"', principal)
        self.assertIn('id="c530-day"', html[html.index('class="a89-cote"'):])

    def test_UX05_commandes_de_routine_conservees_dans_le_menu_secondaire(self):
        from agent import web5652
        complet = web5652.routines_block(self.desk, {'owner': 'cabinet'}, '', 'administrateur')
        compact = web5652.routines_block(self.desk, {'owner': 'cabinet'}, '', 'administrateur', compact=True)
        for commande in ('m5652/routine/activer', 'Modifier', 'Lancer', 'Voir'):
            self.assertEqual(compact.count(commande), complet.count(commande), commande)

    def test_UX05_motif_long_replie(self):
        from agent import routines5652 as rt, web5652
        long = 'motif technique ' * 60
        data = rt.listing(self.desk, 'cabinet', 'administrateur')
        data['routines'][0]['last'] = {'state': 'failed', 'local_label': '02/11', 'result': {'impossible': [long]}}
        with patch.object(rt, 'listing', lambda *a: data):
            html = web5652.routines_block(self.desk, {'owner': 'cabinet'}, '', 'administrateur', compact=True)
        self.assertRegex(html, r'<details class="r52-motif"><summary>Motif</summary><p>motif technique')
        self.assertNotIn(long.strip(), html[:html.index('<details class="r52-motif">')])

    def test_UX05_filtres_documents_visibles_a_zero(self):
        html = accueil5689.travail_html(self.desk, '')
        for cle in ('tout', 'documents', 'courriels', 'notes'):
            self.assertIn('data-filtre="%s"' % cle, html)
            self.assertIn('data-filtre-panneau="%s"' % cle, html)
        self.assertIn('Documents <span class="ui89-compte">0</span>', html)
        self.assertIn('data-etat="vide"', html)

    def test_UX05_un_filtre_par_famille(self):
        document(self.desk, 'c1', 'contrat', 'Contrat ORLANDEC')
        document(self.desk, 'c2', 'courriel', 'Réponse à Maître BERNARD')
        document(self.desk, 'c3', 'note', 'Note sur les pièces')
        html = accueil5689.travail_html(self.desk, '')
        panneau = lambda cle: re.search(r'data-filtre-panneau="%s".*?(?=<div role="tabpanel"|$)' % cle, html, re.S).group(0)
        self.assertIn('Contrat ORLANDEC', panneau('documents'))
        self.assertNotIn('Réponse à Maître BERNARD', panneau('documents'))
        self.assertIn('Réponse à Maître BERNARD', panneau('courriels'))
        self.assertIn('Note sur les pièces', panneau('notes'))
        for titre in ('Contrat ORLANDEC', 'Réponse à Maître BERNARD', 'Note sur les pièces'):
            self.assertIn(titre, panneau('tout'))

    def test_UX05_etats_en_cours_et_ancien(self):
        document(self.desk, 'd1', 'contrat', 'Contrat en cours', state='preparing')
        document(self.desk, 'd2', 'contrat', 'Bail ancien', jours=50)
        html = accueil5689.travail_html(self.desk, '')
        ligne = lambda titre: next(x for x in re.findall(r'<li class="a89-ligne".*?</li>', html, re.S) if titre in x)
        self.assertIn('En préparation', ligne('Contrat en cours'))
        self.assertIn('Ancien', ligne('Bail ancien'))
        self.assertIn('1 document(s) de plus de 30 jours', html)

    def test_UX12_source_indisponible_pas_de_faux_zero(self):
        lecture = lambda *a, **k: {'items': [], 'counts': {'par_nature': {k: 0 for k in liv.NATURES}, 'a_relire_anciens': 0}, 'complet': False, 'sources': [{'id': 's', 'label': 'Conclusions', 'state': 'unavailable', 'message': 'Table illisible', 'last_ok': ''}]}
        with patch.object(liv, 'projection', lecture):
            html = accueil5689.travail_html(self.desk, '')
        self.assertIn('Source indisponible : liste incomplète', html)
        self.assertIn('à vérifier', html)
        self.assertNotIn('<span class="ui89-compte">0</span>', html)
        self.assertNotIn('Toutes les sources ont répondu', html)

    def test_UX12_registre_illisible_est_une_erreur_pas_une_liste_vide(self):
        with patch.object(liv, 'projection', side_effect=RuntimeError('panne')):
            html = accueil5689.travail_html(self.desk, '')
        self.assertIn('data-etat="erreur"', html)
        self.assertIn('ce n’est pas un zéro', html)

    def test_UX10_nom_long_complet_et_commande_nommee(self):
        document(self.desk, 'e1', title=TITRE_LONG)
        html = accueil5689.travail_html(self.desk, '')
        self.assertIn('>%s</a>' % TITRE_LONG, html)                                       # jamais tronqué
        self.assertIn('aria-label="Ouvrir : %s"' % TITRE_LONG, html)                      # commande non ambiguë au lecteur d'écran

    def test_B12_une_seule_carte_du_matin_et_ids_uniques(self):
        document(self.desk, 'f1')
        html = self.page()
        self.assertEqual(html.count('id="t88-matin"'), 1)
        ids = identifiants(html)
        doubles = {i for i in ids if ids.count(i) > 1}
        self.assertFalse(doubles, doubles)

    def test_B12_lien_du_briefing_vise_l_unique_carte_du_matin(self):
        html = self.page()
        self.assertIn('href="#t88-matin"', html)
        self.assertEqual(html.count('id="t88-matin"'), 1)
        panneau = html[html.index('<dialog class="ui89-panneau" id="a89-rapports"'):]
        self.assertNotIn('id="t88-matin"', panneau[:panneau.index('</dialog>')])     # la carte n'est pas recopiée dans le panneau

    def test_UX05_incidents_techniques_separes_du_travail(self):
        from agent import reversibilite5688
        with patch.object(reversibilite5688, 'banner_html', lambda desk, prefix: '<p class="bandeau-essai">Sauvegarde absente</p>'):
            html = self.page()
        self.assertIn('Incidents techniques', html)
        self.assertLess(html.index('id="a89-travail"'), html.index('Sauvegarde absente'))
        self.assertLess(html.index('class="a89-cote"'), html.index('Sauvegarde absente'))

    def test_UX05_niveaux_d_alerte_ecrits(self):
        from agent import file5651
        rows = [{'class': 'contradiction', 'priority': 'haute', 'priority_reason': 'Échéance dans deux jours', 'consequence': '', 'title': 'Date contradictoire', 'matter': '', 'matter_name': 'DOS-001', 'key': 'k1', 'revision': 1, 'href': '', 'choices': ()},
                {'class': 'projet', 'priority': 'normale', 'priority_reason': '', 'consequence': '', 'title': 'Projet de bail', 'matter': '', 'matter_name': 'DOS-001', 'key': 'k2', 'revision': 1, 'href': '', 'choices': ()},
                {'class': 'echec_agent', 'priority': 'normale', 'priority_reason': '', 'consequence': '', 'title': 'Analyse interrompue', 'matter': '', 'matter_name': 'DOS-001', 'key': 'k3', 'revision': 1, 'href': '', 'choices': ()}]
        html = file5651._today_html(self.desk, '', rows, rows, lambda it: '<li>%s %s</li>' % (it['title'], it['priority_reason']), '', '', True, 8)
        self.assertRegex(html, r'data-niveau="decision">.*Décision métier')
        self.assertRegex(html, r'data-niveau="relecture">.*Relecture')
        self.assertIn('data-niveau="incident">Incident technique', html)
        self.assertIn('Échéance dans deux jours', html)      # la priorité est expliquée

    def test_OP02_actualisation_de_la_zone_ne_rend_que_le_travail(self):
        document(self.desk, 'g1', title='Note ZORVAL')
        r = self.call('/api440/m530/part', query='name=travail&prefix=' + quote(P, safe=''))
        html = json.loads(r['body'])['html']
        self.assertIn('Note ZORVAL', html)
        self.assertNotIn('id="a89-travail"', html)          # la carte et son jeton restent en place

    def test_UX11_jeton_change_quand_un_livrable_est_cree(self):
        avant = liv.revision(self.desk)
        document(self.desk, 'h1')
        self.assertNotEqual(liv.revision(self.desk), avant)

    def test_T88_personnalisations_reprises(self):
        for qui in ('cabinet', 'admin'):
            tableau5688.enregistrer(self.desk, qui, {'accent': 'vert', 'ordre': ['veille', 'file', 'routines'], 'masques': ['style']})
        html = self.page()
        self.assertIn('data-accent="vert"', html)
        self.assertNotIn('id="c530-style"', html)
        self.assertLess(html.index('id="c5632-veille"'), html.index('id="c5656-file"'))
        self.assertLess(html.index('id="c5656-file"'), html.index('id="c5614-routines"'))
        self.assertIn('id="t88-panel"', html)
        self.assertIn('1 bloc(s) masqué(s)', html)

    def test_T88_bloc_masque_absent_journee_masquee(self):
        for qui in ('cabinet', 'admin'):
            tableau5688.enregistrer(self.desk, qui, {'accent': '', 'ordre': [], 'masques': ['journee', 'routines']})
        html = self.page()
        self.assertNotIn('id="c530-day"', html)
        self.assertNotIn('id="c5614-routines"', html)
        self.assertIn('id="c5656-file"', html)

    def test_OP04_incident_de_rendu_rend_l_ancienne_page(self):
        with patch.object(accueil5689, 'page', side_effect=RuntimeError('panne')):
            html = self.page()
        self.assertIn('<div class="c530-cols c5657-cols">', html)
        self.assertNotIn('id="a89-travail"', html)

    def test_UX04_partiel_menu_et_registre_inchanges(self):
        self.assertGreaterEqual(len(fonctions5631.FUNCTIONS), 85)
        self.assertEqual([p for p, *_ in shell501.PRIMARY], ['/aujourdhui', '/courriels', '/dossiers', '/planning', '/production'])


class Enveloppe(Base):
    def test_UX03_page_reelle_valeur_externe_donne_le_repli(self):
        document(self.desk, 'r1')
        html = self.page('/livrables/doc-r1', 'return_to=' + quote('https://evil.example.test/x', safe=''))
        self.assertNotIn('evil.example.test', html)
        self.assertRegex(html, r'href="%s/livrables"[^>]*data-ui89-retour' % P)

    def test_UX03_page_reelle_javascript_et_oblique_refuses(self):
        document(self.desk, 'r2')
        for valeur in ('javascript:alert(1)', '//evil.example.test', P + '/%2f%2fevil.example.test'):
            html = self.page('/livrables/doc-r2', 'return_to=' + quote(valeur, safe=''))
            self.assertNotIn('evil.example.test', html.split('data-ui89-retour')[0][-300:])
            self.assertNotIn('javascript:alert', html)

    def test_LIV01_retour_de_la_visionneuse_vers_la_liste_filtree(self):
        document(self.desk, 'r3')
        cible = P + '/livrables?nature=contrat&curseur=abc'
        html = self.page('/livrables/doc-r3', 'return_to=' + quote(cible, safe=''))
        self.assertIn('href="%s"' % cible.replace('&', '&amp;'), html)
        self.assertIn('Retour aux documents', html)
        self.page('/livrables/doc-r3')                       # lien direct : repli, page servie

    def test_UX03_contentieux_retour_commun(self):
        args = (nav.valider_retour(P + '/dossier?id=DOS-001', P), nav.libelle_source(P + '/dossier?id=DOS-001', P))
        self.assertEqual(args, (P + '/dossier?id=DOS-001', 'Retour au dossier'))
        self.assertEqual(nav.libelle_source(P + '/contentieux?dossier=DOS-001', P), 'Retour au contentieux')
        self.assertIn('/contentieux|', nav.details_json())
        self.assertIsNotNone(nav.retour(P, '/contentieux', {}))

    def test_LIV01_ancien_parametre_retour_accepte(self):
        document(self.desk, 'r4')
        html = self.page('/livrables/doc-r4', 'retour=' + quote(P + '/dossier?id=DOS-001', safe=''))
        self.assertIn('Retour au dossier', html)

    def test_UX02_lien_de_l_accueil_porte_son_retour(self):
        document(self.desk, 'r5')
        html = accueil5689.travail_html(self.desk, P)
        self.assertIn('/livrables/doc-r5?return_to=%2Fagent-courriel%2Faujourdhui', html)

    def test_UX09_menu_en_svg_locaux_noms_accessibles(self):
        html = self.page('/dossiers')
        nav_html = html[html.index('class="ws-primary"'):html.index('id="f5631-all"')]
        self.assertEqual(nav_html.count('class="ws-nav-icon"'), nav_html.count('<svg'))
        self.assertRegex(nav_html, r'aria-label="Dossiers"')
        self.assertNotRegex(html[html.index('<header class="ws-topbar">'):html.index('</header>')], '[-]')

    def test_UX07_barre_du_haut_garde_ses_noms_accessibles(self):
        html = self.page('/dossiers')
        barre = html[html.index('<header class="ws-topbar">'):html.index('</header>')]
        for ident in ('ws-sidebar-toggle', 'ws-theme-toggle'):
            bouton = re.search(r'<button[^>]*id="%s"[^>]*>' % ident, barre).group(0)
            self.assertIn('aria-label="', bouton)

    def test_UX09_feuille_d_impression_masque_commandes_et_assistant(self):
        css = (STATIC / 'v5689-carbon.css').read_text(encoding='utf-8')
        impression = css[css.index('@media print'):]
        masques = impression[:impression.index('{', impression.index('{') + 1)]
        for selecteur in ('#ws-sidebar', '.ws-topbar', '#ws-ai-dock', '#ws-ai-launcher', '#ws-live-bar', '.ax-btn', 'dialog'):
            self.assertIn(selecteur, masques)
        self.assertRegex(impression, r'#ws-main \* \{ max-height: none !important; overflow: visible !important; \}')

    def test_UX06_viewport_et_colonnes_repliables(self):
        html = self.page()
        self.assertIn('name="viewport" content="width=device-width,initial-scale=1"', html)
        css = (STATIC / 'v5689-carbon.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'@media \(max-width: [\d.]+rem\) \{ \.a89-layout \{ grid-template-columns: minmax\(0, 1fr\); \}')

    def test_UX07_panneau_natif_fermeture_et_focus(self):
        html = self.page()
        self.assertRegex(html, r'<dialog class="ui89-panneau"[^>]*aria-labelledby="c530-t-routines"')
        self.assertIn('data-ui89-ferme', html)

    def test_HO22_selecteur_de_jours_hidden_preserve(self):
        css = (STATIC / 'v5689-carbon.css').read_text(encoding='utf-8')
        self.assertNotRegex(css, r'\[data-jours\](?!\[hidden\])[^{]*\{[^}]*display')     # aucune règle ne rend visible un sélecteur masqué
        self.assertRegex(css, r'\[data-jours\]\[hidden\][^{]*(,[^{]*)?\{ display: none; \}')
        self.assertIn('[hidden] { display: none !important; }', css)
        html = self.page('/parametres', 'rubrique=horaires')
        self.assertRegex(html, r'data-jours role="group"[^>]*hidden')

    def test_HO22_page_rendue_sous_le_theme_champs_natifs_au_clavier(self):
        html = self.page('/parametres', 'rubrique=horaires')
        self.assertIn('data-carbon="1"', html)
        self.assertIn('data-plage', html)
        self.assertNotIn('<cds-text-input', html)


if __name__ == '__main__':
    unittest.main()
