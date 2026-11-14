"""5.6.94, lot parcours : banc navigateur (P01), recette GitHub (P03) et défauts révélés par les parcours (P05).

Le banc lui-même (``scripts/parcours-navigateur.py``) est exercé ici SANS navigateur : vraie application WSGI derrière un vrai ``waitress`` sur
127.0.0.1, vraie page de connexion, vrai faux fournisseur de modèle en bouclage, vraie file exécutée par ``Desk.work_once``. Les parcours dans
le navigateur ne tournent que si Playwright est installé ET ``AXIORHUB_PARCOURS=1`` : sans Playwright, la suite ``unittest`` ne dépend de rien.
Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, example.test). Aucun réseau hors de 127.0.0.1, aucun modèle réel.
"""
from html.parser import HTMLParser
import http.client
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import unittest
import unittest.mock
from urllib.parse import urlencode

from agent import taches5628
from agent.desk import Desk
import test_agent

ROOT = Path(__file__).resolve().parent.parent
BANC = ROOT / 'scripts' / 'parcours-navigateur.py'


def banc_module():
    spec = importlib.util.spec_from_file_location('parcours_navigateur5694', BANC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Attributs(HTMLParser):
    """Relevé des attributs de gestionnaire d'événement (``onclick``…) et des formulaires avec leurs boutons d'envoi."""

    def __init__(self):
        super().__init__()
        self.gestionnaires, self.formulaires, self._form = [], [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.gestionnaires += ['%s[%s]' % (tag, k) for k in attrs if k.startswith('on')]
        if tag == 'form':
            self._form = {'classe': attrs.get('class', ''), 'envoi': False, 'champs': []}
            self.formulaires.append(self._form)
        elif self._form is not None and tag == 'button' and attrs.get('type', 'submit') == 'submit':
            self._form['envoi'] = True
        elif self._form is not None and tag in ('select', 'input'):
            self._form['champs'].append(attrs.get('name', ''))

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form = None


class P05Taches(unittest.TestCase):
    """Défaut révélé par P02g : sur Agenda › Mes tâches, le filtre par dossier reposait sur ``onchange="this.form.submit()"``, bloqué par la CSP
    (``script-src 'self'``) ; le bouton de repli était dans ``<noscript>``, donc invisible : choisir un dossier ne filtrait rien."""

    def setUp(self):
        self.f = test_agent.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    def rendre(self, args=None):
        link = lambda href, label, *a, **kw: '<a href="%s">%s</a>' % (href, label)
        form = lambda action, label, *a, extra='', **kw: '<form method="post"><input type="hidden" name="action" value="%s">%s<button>%s</button></form>' % (action, extra, label)
        url = lambda path, **kw: '/p' + path + ('?' + urlencode(kw) if kw else '')
        return taches5628.page(self.desk, link, form, url, args or {'vue': 'taches'})

    def test_P05_filtre_par_dossier_utilisable_sous_la_csp(self):
        lecteur = Attributs()
        lecteur.feed(self.rendre())
        self.assertEqual(lecteur.gestionnaires, [])        # tout gestionnaire en ligne est bloqué par la CSP de la page
        filtre = [f for f in lecteur.formulaires if 'tk28-filter' in f['classe']]
        self.assertEqual(len(filtre), 1)
        self.assertIn('dossier', filtre[0]['champs'])
        self.assertTrue(filtre[0]['envoi'])               # le choix du dossier s'applique par un bouton visible, sans script

    def test_P05_le_filtre_choisi_reste_applique(self):
        self.desk.db.execute("INSERT INTO tasks(id,matter,title,due,status) VALUES('t-banc-1','DOS-001','Relancer le confrère',NULL,'open')")
        self.desk.db.commit()
        html = self.rendre({'vue': 'taches', 'dossier': 'AUTRE'})
        self.assertNotIn('Relancer le confrère', html)
        self.assertIn('Relancer le confrère', self.rendre({'vue': 'taches', 'dossier': 'DOS-001'}))


# Petit DOM fictif : un formulaire Carbon avec une liste, une case et des boutons radio, passés au vrai ``adjoindre`` de v5689-formulaires.js.
ADJOINDRE_JS = r"""
const api = require(process.argv[2]);
const crees = [];
function noeud(tag) {
  const n = {tagName: tag.toUpperCase(), attrs: {}, classes: new Set(), enfants: [], ecoutes: {}, value: '', options: [], style: {},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, f) { (this.ecoutes[t] = this.ecoutes[t] || []).push(f); }, appendChild(c) { this.enfants.push(c); c.parentNode = this; return c; },
    after(c) { crees.push('apres:' + c.tagName.toLowerCase()); }, closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    classList: {add: c => n.classes.add(c), contains: c => n.classes.has(c), remove: c => n.classes.delete(c)}};
  return n;
}
const doc = {createElement(t) { crees.push(t); return noeud(t); }, addEventListener() {}};
const form = noeud('form'); form.ownerDocument = doc;
const select = noeud('select'); select.options = [{value: 'a', textContent: 'A'}, {value: 'b', textContent: 'B'}]; select.value = 'a';
const bloc = noeud('label'); bloc.attrs['data-champ'] = 'liste'; bloc.querySelector = s => (s === 'select' ? select : null);
form.querySelectorAll = () => [bloc];
api.adjoindre(form);
process.stdout.write(JSON.stringify({crees, natif_masque: bloc.classes.has('ui89-natif-bloc') || select.hasAttribute('hidden') || select.classes.has('ui89-natif')}));
"""


class P05Carbon(unittest.TestCase):
    """Défaut révélé par P02h (page Voix) : le FormAdapter 5.6.89 remplaçait chaque liste native par ``cds-select`` ; le gabarit de ce composant
    (Carbon 2.66.0, ``carbon5682.js``) pose ``style="--cds-show-before: …"`` dans son ombre, refusé par la CSP (``style-src 'self'``) : une
    violation CSP par liste à l'affichage. La liste native, déjà au thème Carbon par la feuille de style, reste en place."""

    def test_P05_liste_native_gardee_aucun_cds_select_sous_la_csp(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('Node absent')
        self.assertIn('style="--${"cds"}-show-before', (ROOT / 'agent' / 'static' / 'carbon5682.js').read_text(encoding='utf-8'))   # cause : gabarit de cds-select
        with tempfile.TemporaryDirectory() as t:
            script = Path(t) / 'adjoindre.cjs'
            script.write_text(ADJOINDRE_JS, encoding='utf-8')
            out = subprocess.run([node, str(script), str(ROOT / 'agent' / 'static' / 'v5689-formulaires.js')], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        resultat = json.loads(out.stdout)
        self.assertNotIn('cds-select', resultat['crees'])
        self.assertFalse(resultat['natif_masque'])            # la liste native reste visible et seule à être lue


class P01Banc(unittest.TestCase):
    """Le banc sans navigateur : installation fictive, vrai serveur, vraie connexion, faux modèle et file à la demande."""

    @classmethod
    def setUpClass(cls):
        try:
            import waitress  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('waitress absent de cet interpréteur Python')
        cls.m = banc_module()

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.t.cleanup)
        from sqlite_cleanup import close_sqlite_under
        self.addCleanup(close_sqlite_under, self.t.name)
        saved = {k: os.environ.get(k) for k in ('AXIORHUB_DATA_DIR', 'AXIORHUB_AUTH_STATE')}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        logging.getLogger('waitress').setLevel(logging.CRITICAL)
        self.banc = self.m.Banc(self.t.name)
        self.banc.preparer()
        self.banc.demarrer()
        self.addCleanup(self.banc.arreter)

    def http(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.banc.port, timeout=20)
        try:
            c.request(method, self.m.PREFIX + path, body, headers or {})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def connexion(self):
        form = urlencode({'ident': self.banc.ident, 'password': self.banc.mot_de_passe, 'suite': self.m.PREFIX + '/aujourdhui'})
        status, headers, _ = self.http('POST', '/connexion', form, {'Content-Type': 'application/x-www-form-urlencoded', 'Origin': self.banc.origine})
        self.assertEqual(status, 303)
        cookie = re.search(r'axh_session=([^;]+)', headers.get('Set-Cookie', ''))
        self.assertTrue(cookie)
        return 'axh_session=' + cookie.group(1)

    def test_P01_serveur_reel_sur_127_0_0_1_et_connexion_par_la_page(self):
        self.assertEqual(self.banc.serveur.effective_host, '127.0.0.1')
        status, headers, _ = self.http('GET', '/aujourdhui')
        self.assertEqual(status, 303)                         # sans session : page de connexion, pas de fenêtre Basic
        self.assertIn('/connexion', headers['Location'])
        status, _, body = self.http('GET', '/aujourdhui', headers={'Cookie': self.connexion()})
        self.assertEqual(status, 200)
        self.assertIn('id="a92-pause"', body.decode('utf-8'))
        bad = urlencode({'ident': self.banc.ident, 'password': 'faux-' + self.banc.mot_de_passe})
        self.assertEqual(self.http('POST', '/connexion', bad, {'Content-Type': 'application/x-www-form-urlencoded', 'Origin': self.banc.origine})[0], 401)

    def test_P01_mot_de_passe_jamais_ecrit_en_clair_ni_identifiant_dans_un_journal(self):
        self.connexion()
        self.banc.semer_livrable()
        self.banc.travailler()
        secret = self.banc.mot_de_passe.encode()
        for path in Path(self.t.name).rglob('*'):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(secret, raw, path.name)
                if path.suffix in ('.log', '.txt', '.jsonl'):
                    self.assertNotIn(self.banc.ident.encode(), raw, path.name)
        auth = json.loads(Path(self.banc.auth_path).read_text(encoding='utf-8'))
        self.assertNotIn('password', auth)                    # seule l'empreinte scrypt est conservée

    def test_P01_faux_modele_compte_ses_appels_et_la_file_ne_tourne_qu_a_la_demande(self):
        doc = self.banc.semer_livrable()
        with self.banc.desk() as d:
            row = __import__('agent.livrables5689', fromlist=['charger']).charger(d, doc['document_id'])
        cookie = self.connexion()
        csrf = self.banc.auth['csrf']
        status, _, body = self.http('POST', '/api440/m5689/livrables/%s/modifier-ia' % doc['document_id'],
                                    json.dumps({'cle': 'banc-cle-0001', 'instruction': 'Porter le prix à deux mille euros', 'version_id': row['version_id'], 'sha256': row['sha256']}),
                                    {'Cookie': cookie, 'Origin': self.banc.origine, 'X-CSRF-Token': csrf, 'Content-Type': 'application/json'})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(self.banc.modele.nombre(), 0)       # aucun travailleur de fond : rien ne part tant que le banc n'exécute pas la file
        self.assertEqual([j['status'] for j in self.banc.travaux('modifier_livrable5689')], ['pending'])
        self.assertEqual(self.banc.travailler(), 1)
        self.assertEqual(self.banc.modele.nombre(), 1)
        with self.banc.desk() as d:
            state = d.db.execute('SELECT state FROM document_patches5689 WHERE document_id=?', (doc['document_id'],)).fetchone()[0]
        self.assertEqual(state, 'proposee')                   # la réponse fictive est une vraie proposition de modification

    def test_P01_panne_simulee_du_fournisseur_comptee_une_fois(self):
        self.banc.modele.pannes = 1
        c = http.client.HTTPConnection('127.0.0.1', int(self.banc.modele.url.rsplit(':', 1)[1]), timeout=5)
        c.request('POST', '/api/chat', json.dumps({'messages': []}), {'Content-Type': 'application/json'})
        self.assertEqual(c.getresponse().status, 503)
        c.close()
        c = http.client.HTTPConnection('127.0.0.1', int(self.banc.modele.url.rsplit(':', 1)[1]), timeout=5)
        c.request('POST', '/api/chat', json.dumps({'messages': []}), {'Content-Type': 'application/json'})
        self.assertEqual(c.getresponse().status, 200)
        c.close()
        self.assertEqual(self.banc.modele.nombre(), 2)

    def test_P01_document_seme_au_registre_avec_son_fichier(self):
        doc = self.banc.semer_livrable()
        cookie = self.connexion()
        status, headers, body = self.http('GET', '/api440/m5689/livrables/%s/telecharger' % doc['document_id'], headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertEqual(body, doc['octets'])


class P01Lancement(unittest.TestCase):
    def test_P01_sans_playwright_code_de_sortie_2_et_aucun_parcours(self):
        m = banc_module()
        import builtins
        real = builtins.__import__

        def absent(name, *a, **kw):
            if name.startswith('playwright'):
                raise ImportError(name)
            return real(name, *a, **kw)
        with tempfile.TemporaryDirectory() as t, unittest.mock.patch('builtins.__import__', absent):
            self.assertEqual(m.lancer('chromium', t), 2)
            self.assertEqual(list(Path(t).iterdir()), [])

    def test_P01_parcours_annonces_tous_presents(self):
        m = banc_module()
        self.assertEqual([p[0] for p in m.PARCOURS], ['P02a', 'P02b', 'P02c', 'P02d', 'P02e', 'P02f', 'P02g', 'P02h', 'P95a', 'P95b',
                                                      'P96k', 'P96F1', 'P96F2', 'P96c', 'P96a', 'P96b', 'P96v',
                                                      'P97f', 'P97g', 'P97m', 'P97c', 'P97s', 'P97d', 'P97a', 'P98a', 'P98p', 'P98d', 'P99a', 'P100r', 'P100d', 'P100p'])   # 5.6.99 : bandeau des routines ; 5.6.98 : Aujourd'hui, Paramètres, Nouveau dossier ; 5.6.97 : lots   # 5.6.95 : lot aujourdhui ; 5.6.96 : parcours des lots (ordre d'ajout)


class P03Recette(unittest.TestCase):
    """Travail « navigateur » de la recette GitHub : versions figées, Chromium avec ses dépendances, captures en artefact en cas d'échec."""

    def setUp(self):
        self.text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')
        start = self.text.index('\n  navigateur:')
        nxt = re.search(r'\n  [a-z][a-z0-9-]*:\n', self.text[start + 5:])
        self.job = self.text[start: start + 5 + nxt.start()] if nxt else self.text[start:]

    def test_P03_versions_figees_et_chromium(self):
        for package in ('playwright', 'waitress', 'cryptography'):
            self.assertRegex(self.job, r'%s==\d+\.\d+(\.\d+)?' % package)
        self.assertIn('python -m playwright install --with-deps chromium', self.job)
        self.assertIn('scripts/parcours-navigateur.py --navigateur chromium', self.job)
        self.assertIn('runs-on: ubuntu-', self.job)

    def test_P03_captures_envoyees_seulement_en_cas_d_echec(self):
        upload = self.job[self.job.index('actions/upload-artifact') - 200:]
        self.assertIn('if: failure()', upload)

    def test_P03_autres_travaux_inchanges(self):
        for job in ('tests', 'nextcloud-app-unit', 'migrations-root', 'windows'):
            self.assertIn('\n  %s:\n' % job, self.text)
        self.assertEqual(self.text.count('playwright'), self.job.count('playwright'))   # Playwright n'entre dans aucun autre travail


@unittest.skipUnless(os.environ.get('AXIORHUB_PARCOURS') == '1', 'parcours navigateur : AXIORHUB_PARCOURS=1 et Playwright requis')
class P04Navigateur(unittest.TestCase):
    def test_P04_parcours_dans_le_navigateur(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest('Playwright absent')
        m = banc_module()
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(m.lancer(os.environ.get('AXIORHUB_PARCOURS_NAVIGATEUR', 'chromium'), t), 0, (Path(t) / 'resultats.json').read_text(encoding='utf-8')
                             if (Path(t) / 'resultats.json').exists() else '')


if __name__ == '__main__':
    unittest.main()
