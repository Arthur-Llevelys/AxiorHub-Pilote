"""5.6.73 : navigateur supervisé (poste). Valeurs entièrement fictives (dossier DOS-001, sites exemple.fr / example.test)."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

import test_v5652 as base
from agent import cdp5673, nav5673 as nav, navigateur5673 as nv, web5673
from agent.common import Stop

MID = base.MID
OWNER = 'avocat@example.test'


# ---------------------------------------------------------------------------------------------------- adresses, sites, conséquences
class Regles(unittest.TestCase):
    def test_adresses_refusees(self):
        self.assertEqual(nav.navigation_refusal('https://www.infogreffe.fr/recherche'), '')
        for bad in ('http://localhost:8080/', 'http://127.0.0.1/', 'http://192.168.1.20/', 'http://nas.local/', 'http://10.0.0.1/',
                    'http://169.254.169.254/latest', 'http://intranet/'):
            self.assertEqual(nav.navigation_refusal(bad), 'adresse_du_poste_ou_du_reseau_local', bad)
        self.assertEqual(nav.navigation_refusal('file:///C:/secret.txt'), 'adresse_non_web')
        self.assertEqual(nav.navigation_refusal('https://nom:mdp@exemple.fr/'), 'adresse_avec_identifiants')

    def test_sites_autorises_et_sous_domaines(self):
        self.assertTrue(nav.site_allowed('https://www.infogreffe.fr/x', ['infogreffe.fr']))
        self.assertTrue(nav.site_allowed('https://portail.justice.fr/x', ['justice.fr']))
        self.assertFalse(nav.site_allowed('https://faux-justice.fr/x', ['justice.fr']))
        self.assertFalse(nav.site_allowed('https://exemple.fr/', nv.DEFAULT_SITES))

    def test_conseq_francaises_et_juridiques(self):
        page = nav.PageFacts(url='https://www.exemple.fr/demarche')
        for label, kind in (('Payer', 'pay'), ('Valider le paiement', 'pay'), ('Valider le dépôt', 'send'), ('Transmettre', 'send'),
                            ('Déposer les conclusions', 'send'), ('Signer électroniquement', 'send'), ('Notifier', 'send'),
                            ('Supprimer', 'delete'), ('Annuler la demande', 'delete'), ('Modifier mon mot de passe', 'account')):
            c = nav.classify('click', label, 'button', page)
            self.assertIsNotNone(c, label)
            self.assertEqual(c.kind, kind, label)
        for label in ('Rechercher', 'Consulter', 'Suivant', 'Afficher le détail', 'Télécharger'):
            self.assertIsNone(nav.classify('click', label, 'button', page), label)
        self.assertIsNone(nav.classify('click', 'Déposer une demande', 'link', page))          # un lien ouvre une page
        self.assertIsNone(nav.classify('click', 'Réserver', 'button', page))                    # sans montant affiché
        self.assertEqual(nav.classify('click', 'Réserver', 'button', nav.PageFacts(url=page.url, shows_price=True)).kind, 'book')
        self.assertEqual(nav.classify('click', 'Continuer', 'button', nav.PageFacts(url=page.url, card_fields=True)).kind, 'pay')
        self.assertEqual(nav.classify('click', 'Enregistrer', 'button', nav.PageFacts(url='https://exemple.fr/mon-compte/parametres')).kind, 'account')
        self.assertIsNone(nav.classify('submit', 'Nom', 'textbox', nav.PageFacts(url=page.url, search_form=True)))
        self.assertEqual(nav.classify('submit', 'Objet', 'textbox', nav.PageFacts(url=page.url, form_submit_label='Envoyer')).kind, 'send')
        self.assertIn('exemple.fr', nav.classify('click', 'Payer', 'button', page).question())

    def test_plan_de_page_reperes_stables(self):
        def ax(nodes):
            return [{'nodeId': str(i), 'parentId': p, 'role': {'value': r}, 'name': {'value': n}, 'backendDOMNodeId': b,
                     'childIds': [str(c) for c in kids], **({'value': {'value': v}} if v else {}), 'properties': props}
                    for i, p, r, n, b, kids, v, props in nodes]
        first = ax([(0, None, 'RootWebArea', 'Page', 1, [1, 2, 3, 4], '', []),
                    (1, '0', 'heading', 'Recherche', 2, [], '', [{'name': 'level', 'value': {'value': 1}}]),
                    (2, '0', 'textbox', 'Nom', 3, [], 'DURAND', []),
                    (3, '0', 'textbox', 'Mot de passe', 4, [], '•••', [{'name': 'protected', 'value': {'value': True}}]),
                    (4, '0', 'button', 'Rechercher', 5, [5], '', []),
                    (5, '4', 'StaticText', 'Rechercher', 6, [6], '', []),
                    (6, '5', 'InlineTextBox', 'Rechercher', 7, [], '', [])])
        refs = nav.RefMap()
        text, n = nav.outline(first, refs)
        self.assertEqual(n, 3)
        self.assertIn('- heading "Recherche" (niveau=1)', text)
        self.assertIn('textbox "Nom" (valeur="DURAND") [ref=e1]', text)
        self.assertIn('textbox "Mot de passe" [ref=e2]', text)          # jamais la valeur d'un champ protégé
        self.assertNotIn('•', text)
        self.assertEqual(text.count('Rechercher'), 1)                    # le libellé du bouton n'est pas répété
        again = ax([(0, None, 'RootWebArea', 'Page', 10, [1, 4], '', []), (1, '0', 'textbox', 'Nom', 30, [], '', []),
                    (4, '0', 'button', 'Rechercher', 50, [], '', [])])
        text2, _ = nav.outline(again, refs)
        self.assertIn('button "Rechercher" [ref=e3]', text2)             # même contrôle, même repère après un nouveau rendu
        self.assertEqual(refs.backend('e3'), 50)
        self.assertIsNone(refs.backend('e2'))                            # disparu de la page


# ---------------------------------------------------------------------------------------------------- séance (page et modèle simulés)
class FakeCDP:
    closed = False

    def __init__(self):
        self.queue = []

    def drain(self, method=None):
        out, self.queue = self.queue, []
        return out


class FakeBrowser:
    def __init__(self, profile, downloads_dir=''):
        self.downloads = Path(downloads_dir)
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.cdp = FakeCDP()

    def start(self):
        return self

    def alive(self):
        return True

    def stop(self):
        pass


class FakeTab:
    """Page fictive : un bouton « Valider le dépôt » (e1), un champ mot de passe (e2), un champ de recherche (e3)."""
    log = []

    def __init__(self, cdp):
        self.refs = type('Refs', (nav.RefMap,), {'reset': lambda self: None})()   # page fictive inchangée : mêmes repères
        self.refs.assign([('button', 'Valider le dépôt', 11), ('textbox', 'Mot de passe', 12), ('textbox', 'Nom', 13)])
        self.url = 'https://www.infogreffe.fr/recherche'

    def info(self):
        return self.url, 'Recherche'

    def plan(self):
        self.refs.assign([('button', 'Valider le dépôt', 11), ('textbox', 'Mot de passe', 12), ('textbox', 'Nom', 13)])
        return '- button "Valider le dépôt" [ref=e1]\n- textbox "Mot de passe" [ref=e2]\n- textbox "Nom" [ref=e3]', 3

    def facts(self, ref):
        return {'secret': 'password' if ref == 'e2' else '', 'url': self.url, 'title': 'Recherche'}

    def click(self, ref):
        FakeTab.log.append(('click', ref))

    def type(self, ref, text, submit=False):
        FakeTab.log.append(('type', ref, text))

    def choose(self, ref, label):
        FakeTab.log.append(('choose', ref, label))

    def targets(self):
        return set()

    def follow_new_tab(self, known):
        return False

    def navigate(self, url):
        self.url = url
        FakeTab.log.append(('navigate', url))

    def settle(self, seconds=0, previous=None):
        pass

    def text(self, limit=0):
        return 'Texte fictif de la page'

    def mhtml(self):
        return 'MIME-Version: 1.0'

    def scroll(self, d):
        pass

    def back(self):
        pass


class Seance(base.Base):
    def setUp(self):
        super().setUp()
        FakeTab.log = []
        self.mgr = nv.Manager(self.desk.c, browser_factory=FakeBrowser)
        p = patch('agent.navigateur5673.Tab', FakeTab)
        p.start()
        self.addCleanup(p.stop)

    def _script(self, actions):
        it = iter(actions)
        return patch.object(nv.Manager, '_ask_model', lambda self, desk, s, page, last: next(it))

    def _wait_state(self, states, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = self.mgr.session
            if s is not None and s.state in states:
                return s
            time.sleep(0.05)
        self.fail('état attendu non atteint : %s (actuel %s)' % (states, self.mgr.session.state if self.mgr.session else None))

    def test_validation_avant_depot_puis_resume(self):
        with self._script([{'action': 'cliquer', 'ref': 'e1', 'raison': 'déposer'}, {'action': 'terminer', 'raison': 'fin', 'resume': 'Dépôt fait.'}]):
            out = self.mgr.start(OWNER, 'Déposer la demande fictive', '')
            s = self._wait_state(('validation',))
            self.assertEqual(s.pending['kind'], 'consequence')
            self.assertIn('Envoyer ou déposer', s.pending['question'])
            self.assertEqual(FakeTab.log, [])                                  # rien n'est cliqué avant l'accord
            self.mgr.decide(out['id'], 'autoriser')
            self._wait_state(('termine',))
        self.assertEqual(FakeTab.log, [('click', 'e1')])
        self.assertEqual(self.mgr.session.summary, 'Dépôt fait.')

    def test_refus_de_l_avocat(self):
        with self._script([{'action': 'cliquer', 'ref': 'e1', 'raison': 'déposer'}, {'action': 'terminer', 'raison': 'refusé', 'resume': 'Non déposé.'}]):
            out = self.mgr.start(OWNER, 'Déposer la demande fictive', '')
            self._wait_state(('validation',))
            self.mgr.decide(out['id'], 'refuser')
            self._wait_state(('termine',))
        self.assertEqual(FakeTab.log, [])

    def test_mot_de_passe_jamais_saisi_et_historique_sans_texte(self):
        with self._script([{'action': 'saisir', 'ref': 'e2', 'texte': 'secret-fictif', 'raison': 'connexion'},
                           {'action': 'saisir', 'ref': 'e3', 'texte': 'SOCIETE FICTIVE', 'valider': False, 'raison': 'nom'},
                           {'action': 'terminer', 'raison': 'fin', 'resume': 'ok'}]):
            out = self.mgr.start(OWNER, 'Chercher la société fictive', '')
            s = self._wait_state(('attente_avocat',))
            self.assertIn('Saisie protégée', s.pending['question'])
            self.mgr.decide(out['id'], 'reprendre')
            self._wait_state(('termine',))
        self.assertEqual(FakeTab.log, [('type', 'e3', 'SOCIETE FICTIVE')])   # jamais le mot de passe
        row = self.desk.db.execute('SELECT steps FROM nav5673_sessions WHERE id=?', (out['id'],)).fetchone()[0]
        self.assertNotIn('SOCIETE FICTIVE', row)                             # l'historique ne garde pas ce qui a été saisi
        self.assertNotIn('secret-fictif', row)

    def test_site_hors_liste_puis_toujours(self):
        with self._script([{'action': 'naviguer', 'url': 'https://www.exemple.fr/annuaire?q=1', 'raison': 'source'},
                           {'action': 'naviguer', 'url': 'http://192.168.1.10/', 'raison': 'interne'},
                           {'action': 'terminer', 'raison': 'fin', 'resume': 'ok'}]):
            out = self.mgr.start(OWNER, 'Consulter un annuaire fictif', '')
            s = self._wait_state(('site_a_autoriser',))
            self.assertEqual(s.pending['site'], 'exemple.fr')
            self.mgr.decide(out['id'], 'toujours')
            self._wait_state(('termine',))
        self.assertEqual(FakeTab.log, [('navigate', 'https://www.exemple.fr/annuaire?q=1')])   # le réseau local jamais
        self.assertIn('exemple.fr', nv.sites(self.desk))
        steps = json.loads(self.desk.db.execute('SELECT steps FROM nav5673_sessions WHERE id=?', (out['id'],)).fetchone()[0])
        self.assertTrue(all('?' not in x['u'] for x in steps))                 # adresses sans paramètres

    def test_telechargement_range_dans_le_dossier(self):
        stored = []

        class Dav:
            def ensure_folder(self, path, boundary):
                return path

            def put_file(self, path, data, kind='application/octet-stream'):
                stored.append((path, data))
                return path
        with self._script([{'action': 'attendre', 'raison': 'téléchargement'}, {'action': 'terminer', 'raison': 'fin', 'resume': 'Extrait récupéré.'}]), \
                patch('agent.fichiers5670.client', return_value=Dav()):
            self.mgr._browser()
            browser = self.mgr.browser
            (browser.downloads / 'guid-1').write_bytes(b'%PDF-1.4 extrait fictif')
            browser.cdp.queue = [{'method': 'Browser.downloadWillBegin', 'params': {'guid': 'guid-1', 'suggestedFilename': 'extrait.pdf'}},
                                 {'method': 'Browser.downloadProgress', 'params': {'guid': 'guid-1', 'state': 'completed'}}]
            self.mgr.start(OWNER, 'Récupérer l’extrait fictif', MID)
            self._wait_state(('termine',))
        self.assertEqual(len(stored), 1)
        self.assertIn('/' + nv.FOLDER + '/', stored[0][0])
        self.assertTrue(stored[0][0].endswith('extrait.pdf'))
        self.assertEqual(self.mgr.session.files[0]['dossier'], MID)

    def test_verification_anti_robots_faite_par_l_avocat(self):
        calls = []
        titles = iter(['Vérification de sécurité en cours', 'Recherche'])
        with patch.object(FakeTab, 'info', lambda tab: (tab.url, next(titles, 'Recherche'))), \
                patch.object(nv.Manager, '_ask_model', lambda self, desk, s, page, last: calls.append(last) or
                             {'action': 'terminer', 'raison': 'fin', 'resume': 'ok'}):
            out = self.mgr.start(OWNER, 'Consulter un registre fictif', '')
            s = self._wait_state(('attente_avocat',))
            self.assertIn('Vérification de sécurité', s.pending['question'])
            self.assertEqual(calls, [])                                         # jamais soumise au modèle
            self.mgr.decide(out['id'], 'reprendre')
            self._wait_state(('termine',))
        self.assertEqual(calls, ['L’avocat a fait la vérification de sécurité du site.'])
        self.assertTrue(nv.challenge('', '- heading "Just a moment..."'))
        self.assertFalse(nv.challenge('Registre', '- button "Rechercher"'))

    def test_une_seance_a_la_fois_et_arret(self):
        with self._script([{'action': 'demander', 'texte': 'Connectez-vous', 'raison': 'connexion'}]):
            out = self.mgr.start(OWNER, 'Tâche fictive longue', '')
            self._wait_state(('attente_avocat',))
            with self.assertRaisesRegex(Stop, 'une_seance_est_deja_en_cours'):
                self.mgr.start(OWNER, 'Autre tâche fictive', '')
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))
        with self.assertRaisesRegex(Stop, 'tache_trop_courte'):
            self.mgr.start(OWNER, 'ok', '')


# ---------------------------------------------------------------------------------------------------- routes, page, câblage
class Routes(base.Base):
    def test_droits_et_poste_seulement(self):
        self.assertTrue(web5673.allowed('assistant', 'lancer'))
        self.assertFalse(web5673.allowed('assistant', 'sites'))
        self.assertTrue(web5673.allowed('avocat', 'sites'))
        from agent import standalone_auth
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5673/lancer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5673/sites'))
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': ''}):
            self.assertFalse(nv.available())
            html = web5673.page_html(self.desk, '/p', {}, {})
        self.assertIn('pour Windows ou Ubuntu', html)
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': '/tmp/poste'}), patch('agent.cdp5673.find_browser', return_value='/opt/chromium'):
            self.assertTrue(nv.available())
            html = web5673.page_html(self.desk, '/p', {}, {})
        self.assertIn('data-n5673="lancer"', html)
        self.assertIn('Sites autorisés', html)
        # poste réel : ui-auth.json porte « poste », sans variable d'environnement
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': ''}), patch('agent.cdp5673.find_browser', return_value='/opt/chromium'):
            self.assertTrue(nv.available({'poste': True, 'csrf': 'x'}))
            self.assertFalse(nv.available({'csrf': 'x'}))
            self.assertIn('data-n5673="lancer"', web5673.page_html(self.desk, '/p', {}, {}, {'poste': True}))

    def test_liste_des_sites(self):
        out = nv.save_sites(self.desk, OWNER, 'https://www.infogreffe.fr/x\njustice.fr, exemple.fr')
        self.assertEqual(out['sites'], ['infogreffe.fr', 'justice.fr', 'exemple.fr'])
        for bad in ('192.168.1.1', 'localhost', 'intranet'):
            with self.assertRaisesRegex(Stop, 'site_invalide'):
                nv.save_sites(self.desk, OWNER, bad)

    def test_cablage_et_licences(self):
        root = Path(nv.__file__).resolve().parents[1]
        from agent import pages5636, fonctions5631, web440, shell501
        self.assertIn('/navigateur', pages5636.PAGES)
        self.assertTrue(any(x[0] == 'navigateur' for x in fonctions5631.FUNCTIONS) if hasattr(fonctions5631, 'FUNCTIONS') else True)
        self.assertIn('/static/v5673.js', web440.STATIC)
        self.assertIn('v5673.css', shell501.BUNDLE)
        for lang in ('fr', 'en', 'de', 'es'):
            data = json.loads((root / ('agent/locales/%s.json' % lang)).read_text(encoding='utf-8'))
            self.assertIn('fn.navigateur.label', data)
        self.assertIn('Apache License', (root / 'LICENSES/Apache-2.0.txt').read_text(encoding='utf-8'))
        self.assertIn('Row-Bot', (root / 'NOTICE-row-bot').read_text(encoding='utf-8'))
        self.assertIn('Row-Bot', (root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8'))
        self.assertIn('Apache 2.0', (root / 'agent/nav5673.py').read_text(encoding='utf-8')[:1200])
        sbom = json.loads((root / 'SBOM.cdx.json').read_text(encoding='utf-8'))
        self.assertTrue(any(c['name'] == 'row-bot' for c in sbom['components']))


# ---------------------------------------------------------------------------------------------------- navigateur réel (si présent)
PAGE = ('<!doctype html><html lang="fr"><head><title>Essai</title></head><body><h1>Essai</h1>'
        '<label>Nom <input id="q"></label><label>Mot de passe <input type="password"></label>'
        '<button onclick="document.getElementById(\'out\').textContent=\'ok\'">Afficher</button><p id="out">-</p></body></html>')


@unittest.skipUnless(cdp5673.find_browser(), 'aucun navigateur Chromium sur cette machine')
class NavigateurReel(unittest.TestCase):
    def test_plan_clic_saisie_et_saisie_protegee(self):
        tmp = tempfile.mkdtemp()
        b = cdp5673.Browser(tmp + '/profil', downloads_dir=tmp + '/dl', headless=True).start()
        try:
            tab = nv.Tab(b.cdp)
            tab.navigate('data:text/html;charset=utf-8,' + urllib.parse.quote(PAGE))
            tab.settle(8)
            text, n = tab.plan()
            self.assertGreaterEqual(n, 3)
            refs = {tab.refs.describe('e%d' % i)[1]: 'e%d' % i for i in range(1, n + 1)}
            tab.click(refs['Afficher'])
            time.sleep(0.4)
            self.assertEqual(tab.evaluate("document.getElementById('out').textContent"), 'ok')
            tab.type(refs['Nom'], 'SOCIETE FICTIVE')
            self.assertEqual(tab.evaluate("document.getElementById('q').value"), 'SOCIETE FICTIVE')
            self.assertEqual(tab.facts(refs['Mot de passe'])['secret'], 'password')
        finally:
            b.stop()


if __name__ == '__main__':
    unittest.main()
