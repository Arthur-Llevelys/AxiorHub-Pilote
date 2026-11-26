"""5.6.110 (lot S) : parcours P110s du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau,
**faux Docker** : aucun vrai conteneur n'est créé par ce parcours ; l'essai contre le vrai Docker est décrit dans le compte rendu).

P110s-documents :
- le banc est une version serveur : la carte n'y apparaît pas (vérifié d'abord) ; le parcours déclare ensuite le banc « poste »
  (``documents56110.est_poste`` remplacée) et remplace ``_lancer`` (faux Docker en mémoire), ``_http`` (/healthcheck),
  ``_port_libre`` et ``office440.diagnostic`` (test en quatre étapes réussi) ;
- Paramètres › Documents et procédure : carte « Serveur de documents sur ce poste » en tête de la section 1, Docker prêt, image
  déjà présente (« rien à télécharger »), bouton « Installer le serveur de documents sur ce poste » ;
- clic : étapes suivies par ``v56110-documents.js`` (état lu sans appel à Docker), page rechargée à la fin, serveur « en marche et
  prêt », réglages de la section 1 remplis seuls (``http://127.0.0.1:8080``, adresse de retour ``host.docker.internal``) ;
- « Arrêter », puis « Désinstaller » (confirmation acceptée) : conteneur retiré du faux Docker, réglages vidés ;
- thèmes clair et sombre, aucun défilement horizontal à 375 × 812 ; aucune erreur JavaScript ni CSP, aucune requête externe.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit.
Lancement seul : ``python scripts/parcours_p110s.py [--navigateur msedge] [--sortie DOSSIER]``.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
CARTE = '#documents56110'


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


class FauxDocker:
    """Moteur Docker en mémoire : un conteneur au plus, image présente, ports libres ; /healthcheck répond au troisième essai."""

    def __init__(self, image):
        self.image, self.conteneur, self.appels, self.essais = image, None, [], 0

    def __call__(self, args, delai):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args) and delai > 0
        a = args[1:]
        self.appels.append(a)
        if a[0] == 'version':
            return 0, '29.8.2\n', ''
        if a[:2] == ['image', 'inspect']:
            return 0, 'sha256:fictif\n', ''
        if a[:2] == ['container', 'inspect']:
            c = self.conteneur
            if not c:
                return 1, '[]', 'Error response from daemon: No such container: ' + a[2]
            return 0, json.dumps([{'Id': c['id'], 'Config': {'Labels': {'axiorhub': 'documents'}}, 'State': {'Running': c['en_marche']},
                                   'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(c['port'])}]}}}]), ''
        if a[0] == 'run':
            assert a[-1] == self.image and '--pull' in a and 'never' in a, a
            self.conteneur = {'id': 'b' * 64, 'en_marche': True, 'port': int(a[a.index('-p') + 1].split(':')[1])}
            return 0, 'b' * 64 + '\n', ''
        if a[0] in ('start', 'stop', 'rm') and self.conteneur and a[-1] == self.conteneur['id']:
            if a[0] == 'rm':
                self.conteneur = None
            else:
                self.conteneur['en_marche'] = a[0] == 'start'
            return 0, '', ''
        return 1, '', 'commande inattendue'

    def http(self, url, delai):
        self.essais += 1
        c = self.conteneur
        if c and c['en_marche'] and url == 'http://127.0.0.1:%d/healthcheck' % c['port'] and self.essais >= 3:
            return 200, b'true'
        return 0, b''


def attendre(p, condition, delai):
    """Texte de la carte relu jusqu'à ce que la condition soit vraie (la page se recharge pendant l'attente) ; échec au bout du délai."""
    import time
    fin, texte = time.monotonic() + delai, ''
    while time.monotonic() < fin:
        try:
            texte = p.inner_text(CARTE, timeout=2000)
            if condition(texte):
                return texte
        except Exception:   # noqa: BLE001 — rechargement en cours : on relit
            pass
        time.sleep(0.3)
    verifier(False, 'attente dépassée ; carte : %s' % texte[:400])


def p110s_documents(banc, s):
    from unittest.mock import patch
    import time
    from agent import documents56110 as D
    p = s.page
    p.on('dialog', lambda d: d.accept())
    p.set_viewport_size({'width': 1366, 'height': 900})
    s.connecter()
    s.aller('/parametres?rubrique=documents')
    verifier(p.locator(CARTE).count() == 0, 'version serveur : la carte ne doit pas apparaître')
    s.propre('version serveur : pas de carte')

    faux = FauxDocker(D.IMAGE)
    suivi = {'etapes': False}
    checks = [{'name': n, 'ok': True, 'detail': '', 'fix': ''} for n in ('Serveur joignable (healthcheck)', 'Bibliothèque api.js',
              'Découverte des formats (discovery)', 'Intégration dans cette page (iframe)', 'Secret JWT et API de commande',
              'Le serveur de documents joint AxiorHub')]
    with patch.object(D, 'est_poste', lambda auth: True), patch.object(D, '_lancer', faux), patch.object(D, '_http', faux.http), \
            patch.object(D, 'executable', lambda: '/opt/banc/docker'), patch.object(D, '_port_libre', lambda port: True), \
            patch.object(D, '_pause', lambda sec: time.sleep(1)), patch('agent.office440.diagnostic', lambda desk, auth, probe=None, reach=False: [dict(c) for c in checks]):
        s.aller('/parametres?rubrique=documents')
        p.wait_for_selector(CARTE, state='attached')
        if not p.locator(CARTE).is_visible():
            p.click('details:has(%s) > summary' % CARTE)
        texte = p.inner_text(CARTE)
        (Path(banc.sortie) / 'P110s-carte.html').write_text(p.inner_html(CARTE), encoding='utf-8')
        verifier('Docker : prêt.' in texte and 'Serveur de documents : non installé.' in texte, 'carte : état inattendu (%s)' % texte[:300])
        verifier('rien à télécharger' in texte, 'carte : image présente non annoncée')
        ordre = p.evaluate("""() => { const c = document.getElementById('documents56110'), m = document.getElementById('of-engine');
          const sec = c && c.parentElement && c.parentElement.closest('section'), h = sec && sec.querySelector('h2');
          return !!(c && m && h && /1 · Modifier les documents/.test(h.textContent) && (c.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING)); }""")
        verifier(ordre, 'la carte n’est pas en tête de la section « 1 · Modifier les documents dans l’interface »')
        p.locator(CARTE).screenshot(path=str(Path(banc.sortie) / 'P110s-carte-avant.png'))
        s.propre('carte avant installation')

        p.click('%s form[data-api="m56110/documents/installer"] button[type=submit]' % CARTE)
        vu = attendre(p, lambda t: 'Installation en cours' in t or 'Installation terminée' in t, 30)
        if 'Installation en cours' in vu:
            suivi['etapes'] = True
            try:
                p.locator(CARTE).screenshot(path=str(Path(banc.sortie) / 'P110s-carte-etapes.png'))
            except Exception:   # noqa: BLE001 — page rechargée pendant la capture : sans effet sur le contrôle
                pass
        attendre(p, lambda t: 'Installation terminée' in t, 60)
        p.wait_for_load_state('networkidle')
        p.wait_for_selector(CARTE, state='attached')
        texte = p.inner_text(CARTE)
        verifier('Serveur de documents : en marche et prêt.' in texte, 'après installation : %s' % texte[:400])
        verifier('Installation terminée.' in texte and 'Dernier test : toutes les étapes sont cochées.' in texte, 'fin non annoncée : %s' % texte[:400])
        verifier(p.input_value('#of-server') == 'http://127.0.0.1:8080', 'adresse du serveur non remplie : %r' % p.input_value('#of-server'))
        verifier(p.input_value('#of-callback').startswith('http://host.docker.internal:'), 'adresse de retour non remplie')
        verifier(p.input_value('#of-engine') == 'euro-office', 'moteur non réglé')
        p.locator(CARTE).screenshot(path=str(Path(banc.sortie) / 'P110s-carte-installe.png'))
        s.propre('installation (faux Docker)')

        p.click('%s form[data-api="m56110/documents/arreter"] button[type=submit]' % CARTE)
        attendre(p, lambda t: 'installé, arrêté' in t, 30)
        p.wait_for_load_state('networkidle')
        p.click('%s form[data-api="m56110/documents/desinstaller"] button[type=submit]' % CARTE)
        attendre(p, lambda t: 'Désinstallation terminée' in t, 60)
        p.wait_for_load_state('networkidle')
        texte = p.inner_text(CARTE)
        verifier('Serveur de documents : non installé.' in texte, 'après désinstallation : %s' % texte[:400])
        verifier(faux.conteneur is None, 'le faux conteneur n’a pas été retiré')
        verifier(p.input_value('#of-server') == '', 'réglages non vidés après désinstallation')
        s.propre('arrêt et désinstallation')

        for theme in ('light', 'dark'):
            p.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
            p.wait_for_timeout(100)
            p.locator(CARTE).screenshot(path=str(Path(banc.sortie) / ('P110s-carte-%s.png' % theme)))
        p.set_viewport_size({'width': 375, 'height': 812})
        p.wait_for_timeout(200)
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal de Paramètres › Documents à 375 px')
        p.locator(CARTE).screenshot(path=str(Path(banc.sortie) / 'P110s-carte-375.png'))
        s.propre('375 px, thèmes clair et sombre')
        verifier(not any(a[0] == 'pull' for a in faux.appels), 'image présente : aucun téléchargement attendu')


PARCOURS = (('P110s-documents', 'Serveur de documents sur ce poste : carte, installation, arrêt, désinstallation (faux Docker), 375 px', p110s_documents),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p110s'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS[0][0] for ident, _, _ in NAV.PARCOURS):   # déjà inscrit par le banc depuis l'intégration
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
