"""5.6.107 (lot M) : parcours P107m du banc ``scripts/parcours-navigateur.py`` — mode Démo (vrai navigateur, données fictives, aucun réseau,
aucun modèle).

P107m — la démo telle qu'une personne intéressée la découvre :
- ouvrir la démo (instance créée par ``demo56107.creer``, session ouverte d'office par le jeton de lancement) : bandeau « Mode
  démonstration — données fictives », visite guidée proposée (bulle, Échap la ferme) ;
- lire un courriel (« DURAND c. MARTIN — conclusions adverses reçues ») et voir son dossier (DOS-001 : TJ Valbrune, RG 24/01234, pièces) ;
- ouvrir le projet de réponse préparé (À relire, marqué « Réponse de démonstration ») ;
- résumer deux courriels cochés (« Résumer avec l’IA » ; IA de démonstration, travail exécuté par la file du banc) ;
- voir l'alerte sur le courriel frauduleux (changement de RIB : pastille de la liste et bandeau du lecteur) ;
- « Recommencer la démo » : remise à neuf en un clic, retour sur Aujourd'hui.
Aucune requête vers une autre adresse que la démo (contrôle du banc), aucune erreur JavaScript ni CSP.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p107m.py [--langue en|de|es|fr]``.
5.6.107 (démo multilingue) : ``--langue`` crée la démo dans cette langue ; objets des courriels, dossier, pièces et marque de l'IA sont lus
dans les données de la langue ; les textes d'interface ne sont vérifiés qu'en français (traduction de l'interface : lot T).
"""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
LANGUE = 'fr'
DURAND = 'DURAND c. MARTIN — conclusions adverses reçues'


def donnees(langue):
    """Repères du parcours lus dans les données de la langue (objets des courriels, premier dossier, pièce, marque de l'IA)."""
    from agent import demo56107
    base = demo56107.dossier_donnees(langue)

    def objet(ident):
        texte = next(base.joinpath('courriels').glob('*-%s.eml.txt' % ident)).read_text(encoding='utf-8')
        return re.search(r'^Subject: (.*)$', texte, re.M).group(1).strip()
    cab = json.loads((base / 'cabinet.json').read_text(encoding='utf-8'))
    d = cab['dossiers'][0]
    return {'durand': objet('durand-pieces'), 'zorval': objet('zorval-relance')[:30], 'fraude': objet('fraude-rib')[:18],
            'kervilac': objet('kervilac-loyer')[:30], 'dossier': d['id'], 'titre': d['titre'], 'piece': d['pieces'][1][0].rsplit('.', 1)[0][5:],
            'marque': json.loads((base / 'reponses-ia.json').read_text(encoding='utf-8'))['marque']}
MASQUES_JS = """() => {
  const b = document.getElementById('demo107'); if (!b) return ['bandeau absent'];
  const r = b.getBoundingClientRect(), out = [];
  document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button]').forEach(n => {
    if (b.contains(n) || n.closest('#demo107-bulle')) return;
    const s = getComputedStyle(n), q = n.getBoundingClientRect();
    if (s.visibility === 'hidden' || s.display === 'none' || q.width < 2 || q.height < 2 || q.bottom <= 0 || q.top >= window.innerHeight) return;
    if (q.right > r.left && q.left < r.right && q.bottom > r.top && q.top < r.bottom) out.push((n.innerText || n.value || n.getAttribute('aria-label') || n.tagName).trim().slice(0, 40));
  });
  return out;
}"""


def actions_libres(p, lieu):
    """Le bandeau ne recouvre aucune action visible (bouton, lien, champ)."""
    masques = p.evaluate(MASQUES_JS)
    verifier(not masques, '%s : le bandeau recouvre %s' % (lieu, masques))


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def banc_demo(Banc):
    """Banc dont l'installation fictive est une instance de démo (API publique du lot M) au lieu de la configuration du banc."""
    class BancDemo(Banc):
        def preparer(self):
            from agent import demo56107
            from agent.common import load_config
            self.racine = Path(demo56107.creer(Path(self.base) / 'demo', graine=107, langue=LANGUE)).parent
            os.environ.update(demo56107.environnement(self.racine))
            self.config_path = self.racine / 'config.json'
            self.auth_path = self.racine / 'ui-auth.json'
            self.auth = json.loads(self.auth_path.read_text(encoding='utf-8'))
            self.config = load_config(str(self.config_path))
            self.cloud = self.racine / 'stockage'

        def arreter(self):
            from agent import demo56107
            try:
                super().arreter()
            finally:
                demo56107.desarmer()
                for cle in ('AXIORHUB_DATA_DIR', 'AXIORHUB_AUTH_STATE', 'AXIORHUB_DEMO'):
                    os.environ.pop(cle, None)
    return BancDemo


def _ligne(p, texte):
    return p.locator('#bx5625-rows .ax-row[data-uid]', has_text=texte).first


def _attendre_liste(p):
    p.wait_for_selector('#bx5625-rows .ax-row[data-uid]', timeout=15000)
    p.wait_for_timeout(300)


class DemoServie:
    """Instance de démo servie par ses propres processus (API publique du lot M : ``creer``, ``commandes``, ``jeton_de_session``) :
    interface ``web.py serve`` et travailleur ``web.py worker`` sur un port libre, dans un dossier temporaire ; mêmes attributs que le banc
    pour ``Session`` (``origine``, ``url``) et pour les parcours (``racine``, ``travailler``, ``sortie``)."""

    def __init__(self, langue='fr', sortie=None):
        self.langue, self.sortie = langue, sortie
        self.base = self.racine = None
        self.procs, self.journaux = [], []

    def demarrer(self, delai=90):
        import socket
        import subprocess
        import tempfile
        import time
        import urllib.error
        import urllib.request
        from agent import demo56107
        self.base = Path(tempfile.mkdtemp(prefix='axh-p107-demo-'))
        with socket.socket() as sonde:
            sonde.bind(('127.0.0.1', 0))
            port = sonde.getsockname()[1]
        self.origine = 'http://127.0.0.1:%d' % port
        self.url = self.origine + demo56107.PREFIXE_WEB
        self.racine = Path(demo56107.creer(self.base / 'demo', graine=107, origine=self.origine, langue=self.langue)).parent
        cmd = demo56107.commandes(self.racine, port)
        env = {**os.environ, **cmd['env']}
        extra = {'start_new_session': True} if os.name == 'posix' else {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        for nom in ('interface', 'travailleur'):
            journal = open(self.base / (nom + '.log'), 'ab')
            self.journaux.append(journal)
            self.procs.append(subprocess.Popen(cmd[nom], cwd=cmd['cwd'], env=env, stdout=journal, stderr=subprocess.STDOUT, **extra))
        limite = time.monotonic() + delai
        while time.monotonic() < limite:
            if self.procs[0].poll() is not None:
                raise RuntimeError('interface de la démo arrêtée (voir %s)' % (self.base / 'interface.log'))
            try:
                urllib.request.urlopen(self.url + '/', timeout=2).close()
                return self
            except urllib.error.HTTPError:
                return self                                  # 401, 303… : le service répond
            except OSError:
                time.sleep(0.5)
        raise RuntimeError('interface de la démo injoignable après %d s' % delai)

    def travailler(self, limite=20):
        """Le travailleur de la démo exécute la file lui-même (processus dédié) : rien à faire ici."""
        return 0

    def arreter(self):
        import shutil
        import time
        from agent.portable import kill_tree
        for proc in self.procs:
            if proc.poll() is None:
                if os.name == 'posix':
                    import signal
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except OSError:
                        pass
                else:
                    kill_tree(proc.pid)
        for proc in self.procs:
            try:
                proc.wait(15)
            except Exception:   # noqa: BLE001 — arrêt forcé ci-dessous
                proc.kill()
        for journal in self.journaux:
            journal.close()
        if self.base is not None:
            for _ in range(10):                              # Windows : fichiers encore ouverts quelques instants après l'arrêt
                shutil.rmtree(self.base, ignore_errors=True)
                if not self.base.exists():
                    break
                time.sleep(0.5)


def _est_demo(banc):
    from agent import demo56107
    racine = getattr(banc, 'racine', None)
    return racine is not None and demo56107._lire_marqueur(racine) is not None


class avec_demo:
    """Aide réutilisable (lots M et T) : ``with avec_demo(banc, s, langue) as (banc_demo, session):``.

    Banc de démo reçu (lancement seul de ce script) : réutilisé tel quel. Banc ordinaire (banc général ``parcours-navigateur.py``, cabinet
    sans démo) : une instance de démo est créée dans un dossier temporaire, servie par son interface et son travailleur sur un port libre,
    avec un contexte de navigateur et une ``Session`` à elle (même contrôle des requêtes externes, des erreurs JavaScript et de la CSP) ;
    tout est arrêté et nettoyé à la sortie, même en cas d'échec."""

    def __init__(self, banc, s, langue=None):
        self.banc, self.s, self.langue = banc, s, langue or LANGUE
        self.demo = self.contexte = None

    def __enter__(self):
        if _est_demo(self.banc):
            return self.banc, self.s
        try:
            self.demo = DemoServie(self.langue, getattr(self.banc, 'sortie', None)).demarrer()
            self.contexte = self.s.context.browser.new_context(viewport={'width': 1366, 'height': 900}, accept_downloads=True,
                                                               locale={'fr': 'fr-FR', 'en': 'en-GB', 'de': 'de-DE', 'es': 'es-ES'}[self.langue])
            return self.demo, _nav().Session(self.demo, self.contexte)
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        try:
            if self.contexte is not None:
                self.contexte.close()
        finally:
            if self.demo is not None:
                self.demo.arreter()
        return False


def p107m_demo(banc, s):
    """Parcours P107m : banc de démo (lancement seul) ou banc ordinaire du banc général (démo servie à part par ``avec_demo``)."""
    with avec_demo(banc, s) as (banc_demo, session):
        _p107m(banc_demo, session)


def _p107m(banc, s):
    from agent import demo56107
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    x = donnees(LANGUE)
    fr = LANGUE == 'fr'
    verifier(demo56107.langue_de(banc.racine) == LANGUE, 'démo créée dans une autre langue que %s' % LANGUE)
    p.set_viewport_size({'width': 1440, 'height': 900})
    # 1. ouvrir la démo : session ouverte d'office, bandeau, visite proposée
    s.aller(demo56107.jeton_de_session(banc.racine, '/courriels'))
    verifier('/courriels' in p.url, 'la démo ne s’ouvre pas sur Courriels : %s' % p.url)
    verifier(p.locator('#demo107').is_visible(), 'bandeau du mode Démo absent')
    verifier(p.locator('#demo107').get_attribute('data-demo107-dossier') == x['dossier'], 'premier dossier de la langue absent du bandeau')
    if fr:
        verifier('Mode démonstration — données fictives' in p.locator('#demo107').inner_text(), 'texte du bandeau absent')
    p.wait_for_selector('#demo107-bulle', timeout=5000)
    if fr:
        verifier('Le courriel d’abord' in p.locator('#demo107-bulle').inner_text(), 'première étape de la visite absente')
    p.screenshot(path=str(sortie / 'P107m-visite.png'))
    p.keyboard.press('Escape')
    verifier(p.locator('#demo107-bulle').count() == 0, 'Échap ne ferme pas la visite guidée')
    # 2. lire un courriel et voir son dossier
    _attendre_liste(p)
    _ligne(p, x['durand']).click()
    p.wait_for_selector('#bx5625-panel .lx56106', timeout=10000)
    panneau = p.locator('#bx5625-panel').inner_text()
    verifier('DURAND' in panneau, 'courriel non affiché dans le lecteur')
    verifier(x['dossier'] in panneau or x['titre'] in panneau, 'le courriel n’est pas rattaché à son dossier')
    p.screenshot(path=str(sortie / 'P107m-courriel.png'))
    s.propre('lecture du courriel')
    s.aller('/dossier?id=' + x['dossier'])
    texte = p.locator('#ws-main').inner_text()
    verifier('DURAND' in texte and 'MARTIN' in texte, 'dossier DURAND c. MARTIN absent')
    s.aller('/dossier?id=%s&vue=pieces' % x['dossier'])
    verifier(x['piece'] in p.locator('#ws-main').inner_text(), 'pièces du dossier absentes (%s)' % x['piece'])
    p.screenshot(path=str(sortie / 'P107m-dossier.png'))
    s.propre('dossier')
    # 3. projet de réponse préparé (À relire)
    s.aller('/a-relire')
    verifier(x['marque'] in p.locator('#ws-main').inner_text() or p.locator('#ws-main :text("DURAND")').count() > 0,
             'projet de réponse de démonstration absent d’À relire')
    p.screenshot(path=str(sortie / 'P107m-a-relire.png'))
    s.propre('À relire')
    # 3 ter. « Message reçu » d'une proposition : son courriel ; note de l'IA : seulement ses onglets, aucune requête en échec
    echecs = []
    ecoute = lambda r: echecs.append('%d %s' % (r.status, r.url.split('/agent-courriel')[-1][:100])) if r.status >= 400 else None   # noqa: E731
    p.on('response', ecoute)
    visibles = ("() => Array.from(document.querySelectorAll('#ws-main .ax100-lecteur [data-section]'))"
                ".filter(b => b.offsetParent && getComputedStyle(b).display !== 'none').map(b => b.getAttribute('data-section'))")
    for ident in p.evaluate("() => Array.from(document.querySelectorAll('#ws-main li[data-review-id]')).map(n => n.getAttribute('data-review-id'))"):
        p.locator('#ws-main li[data-review-id="%s"] a' % ident).first.click()
        p.wait_for_timeout(700)
        onglets = p.evaluate(visibles)
        if ident.startswith('n-'):
            verifier(onglets == ['projet', 'sources'], 'note de l’IA : onglets visibles %s (seulement Note et Sources attendus)' % onglets)
        for o in onglets:
            p.locator('#ws-main .ax100-lecteur [data-section="%s"]' % o).first.click()
            p.wait_for_timeout(600)
            if ident.startswith('p-') and o == 'message':
                texte = p.locator('#ws-main .ax100-lecteur').inner_text()
                verifier('n’est plus localisé' not in texte and 'can no longer be located' not in texte,
                         'proposition %s : le courriel d’origine n’est pas retrouvé' % ident)
                p.screenshot(path=str(sortie / 'P107m-message-recu.png'))
    p.remove_listener('response', ecoute)
    verifier(not echecs, 'À relire : requêtes en échec %s' % echecs)
    s.propre('À relire : sections')
    # 3 bis. le bandeau ne recouvre aucune action (retour des lots H et V : barre de décision d'À relire masquée à 1920 × 1080)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/a-relire')
    p.wait_for_timeout(800)
    actions_libres(p, 'À relire (note ouverte), 1920 × 1080')
    p.locator('#ws-main').get_by_text(x['kervilac']).first.click()
    p.wait_for_timeout(800)
    actions_libres(p, 'À relire (projet de réponse ouvert), 1920 × 1080')
    p.screenshot(path=str(sortie / 'P107m-a-relire-1920.png'))
    s.aller('/courriels')
    _attendre_liste(p)
    _ligne(p, x['durand']).click()
    p.wait_for_selector('#bx5625-panel .lx56106', timeout=10000)
    actions_libres(p, 'Courriels (lecteur), 1920 × 1080')
    p.locator('#ws-main button.bx5628-new').first.click()
    p.wait_for_timeout(1200)
    actions_libres(p, 'rédaction d’un courriel, 1920 × 1080')
    p.screenshot(path=str(sortie / 'P107m-redaction-1920.png'))
    for chemin in ('/aujourdhui', '/dossier?id=' + x['dossier'], '/planning', '/fichiers'):
        s.aller(chemin)
        p.wait_for_timeout(500)
        actions_libres(p, chemin + ', 1920 × 1080')
    p.set_viewport_size({'width': 1440, 'height': 900})
    s.propre('bandeau sans recouvrement')
    # 4. résumer deux courriels cochés
    s.aller('/courriels')
    _attendre_liste(p)
    for texte in (x['durand'], x['zorval']):
        uid = _ligne(p, texte).get_attribute('data-uid')
        p.locator('#bx5625-rows .c5670-pick input[data-uid="%s"]' % uid).check()
    bouton = p.locator('#bx5625 button:has-text("Résumer avec l’IA")') if fr else p.locator('#bx5625 .lx56106-v button', has_text='✨')
    p.wait_for_timeout(300)
    verifier(bouton.count() == 1 and bouton.is_visible(), '« Résumer avec l’IA » absent')
    with p.expect_response(lambda r: '/m56106/lecteur/resumer' in r.url):
        bouton.click()
    banc.travailler()
    p.wait_for_selector('#bx5625-panel .lx56106-synthese', timeout=20000)
    verifier(x['marque'] in p.locator('#bx5625-panel .lx56106-synthese').inner_text(), 'résumé non marqué « %s »' % x['marque'])
    p.screenshot(path=str(sortie / 'P107m-resume.png'))
    (p.locator('#bx5625-panel .lx56106-resume-tete button:has-text("Fermer")') if fr else p.locator('#bx5625-panel .lx56106-resume-tete button').last).click()
    s.propre('résumé de deux courriels')
    # 5. alerte sur le courriel frauduleux
    cochees = p.locator('#bx5625-rows .c5670-pick input:checked')
    while cochees.count():
        cochees.first.uncheck()
    ligne = _ligne(p, x['fraude'])
    verifier(ligne.locator('.fr106-pastille').count() == 1, 'pastille « frauduleux » absente de la liste')
    ligne.click()
    p.wait_for_selector('#bx5625-panel .fr106-bandeau', timeout=10000)
    verifier(p.locator('#bx5625-panel .fr106-bandeau').is_visible(), 'bandeau d’alerte absent du lecteur')
    p.screenshot(path=str(sortie / 'P107m-fraude.png'))
    s.propre('alerte de fraude')
    # 6. recommencer la démo
    avant = json.loads((banc.racine / demo56107.MARQUEUR).read_text(encoding='utf-8'))
    with p.expect_navigation(timeout=60000):
        p.locator('#demo107 [data-demo107-recommencer]').click()
    p.wait_for_load_state('networkidle')
    apres = json.loads((banc.racine / demo56107.MARQUEUR).read_text(encoding='utf-8'))
    verifier(apres.get('reinitialise') and apres.get('reinitialise') != avant.get('reinitialise'), 'la démo n’a pas été remise à neuf')
    verifier('/aujourdhui' in p.url, 'retour sur Aujourd’hui attendu après « Recommencer »')
    verifier(p.locator('#demo107').is_visible(), 'bandeau absent après « Recommencer »')
    p.screenshot(path=str(sortie / 'P107m-recommencer.png'))
    s.propre('recommencer la démo')


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p107m'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--langue', choices=('fr', 'en', 'de', 'es'), default='fr', help='langue de la démo (données et interface)')
    args = parser.parse_args(argv)
    global LANGUE
    LANGUE = args.langue
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.Banc = banc_demo(NAV.Banc)
    # le banc inscrit déjà P107m (module ``parcours_p107m`` importé, langue française) : cette entrée-ci, avec la langue demandée, la remplace
    NAV.PARCOURS = tuple(x for x in NAV.PARCOURS if x[0] != 'P107m') + (
        ('P107m', 'Mode Démo : courriel, dossier, projet de réponse, résumé, alerte de fraude, recommencer', p107m_demo),)
    return NAV.lancer(args.navigateur, str(Path(args.sortie) / LANGUE) if LANGUE != 'fr' else args.sortie, ('P107m',))


if __name__ == '__main__':
    sys.exit(main())
