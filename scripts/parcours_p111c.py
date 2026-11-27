"""5.6.111 (lot C) : parcours P111c du banc ``scripts/parcours-navigateur.py`` — indicateur d'activité htmx (L02), dans un vrai navigateur
(Edge sous Windows) ; données fictives, aucun réseau.

P111c-indicateur :
- la page de l'application porte un seul ``#ax-occupe`` (``role="status"``), caché au repos, et sa zone principale ``hx-indicator`` ;
- la configuration htmx lue dans la page est du JSON valide et garde ``allowEval:false``, ``allowScriptTags:false``,
  ``selfRequestsOnly:true``, ``includeIndicatorStyles:false`` ; aucune feuille ``<style>`` n'est injectée par htmx ;
- une VRAIE requête htmx d'un formulaire de la zone principale (``hx-post`` de ``live430.enhance_forms``) : pendant le trajet, l'indicateur est
  affiché (« Mise à jour… », style de la feuille unique) ; la réponse est retenue par le banc puis rendue en ERREUR (HTTP 500, aucune action
  réelle exécutée) : l'indicateur disparaît ; même contrôle avec une réponse réussie ; thème sombre et 375 px (indicateur dans l'écran) ;
- aucune erreur JavaScript ni CSP hors de la réponse 500 voulue, aucune requête externe.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p111c.py [--navigateur msedge] [--sortie DOSSIER]``.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}
PAGES = ('/aujourdhui', '/parametres', '/courriels', '/dossiers', '/facturation', '/missions', '/')


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _sortie(banc):
    d = Path(getattr(banc, 'sortie', '.') or '.')
    d.mkdir(parents=True, exist_ok=True)
    return d


def _etat(p):
    return p.evaluate("""() => { const n = document.getElementById('ax-occupe'); if (!n) { return null; }
      const s = getComputedStyle(n), r = n.getBoundingClientRect();
      return {display: s.display, texte: n.textContent, role: n.getAttribute('role'), classes: n.className,
              gauche: r.left, droite: r.right, largeur: innerWidth}; }""")


def _attendre(p, condition, secondes=10.0):
    fin = time.time() + secondes
    while time.time() < fin:
        e = _etat(p)
        if condition(e):
            return e
        p.wait_for_timeout(50)
    return _etat(p)


def _formulaire(s, p):
    """Première page qui a un formulaire htmx (``hx-post``) dans la zone principale ; rend le chemin de la page."""
    for chemin in PAGES:
        s.aller(chemin)
        if p.locator('#ws-main form[hx-post]:not([data-confirm])').count():
            return chemin
    return None


def _requete(s, p, statut, lieu):
    """Soumet le formulaire ; la réponse est retenue (indicateur contrôlé pendant le trajet), puis rendue avec ``statut``."""
    retenues = []

    def retenir(route):
        if route.request.method == 'POST':
            retenues.append(route)
        else:
            route.continue_()
    p.route('**/action', retenir)
    try:
        debut = time.time()
        p.evaluate("() => document.querySelector('#ws-main form[hx-post]:not([data-confirm])').requestSubmit()")
        while not retenues and time.time() - debut < 10:
            p.wait_for_timeout(50)
        verifier(retenues, 'P111c %s : la requête htmx du formulaire n’est pas partie' % lieu)
        pendant = _attendre(p, lambda e: e and e['display'] != 'none', 5)
        MESURES.setdefault(lieu, {})['pendant'] = pendant
        verifier(pendant and pendant['display'] == 'block' and 'htmx-request' in pendant['classes'] and 'Mise à jour' in pendant['texte'],
                 'P111c %s : indicateur non affiché pendant la requête : %s' % (lieu, pendant))
        verifier(pendant['gauche'] >= 0 and pendant['droite'] <= pendant['largeur'], 'P111c %s : indicateur hors de l’écran : %s' % (lieu, pendant))
        s.capture(_sortie(s.banc) / ('P111c-%s-pendant.png' % lieu))
        retenues[0].fulfill(status=statut, content_type='application/json', body=json.dumps({'error': 'essai_banc', 'message': 'Réponse simulée du banc.'}))
        apres = _attendre(p, lambda e: e and e['display'] == 'none', 10)
        MESURES[lieu]['apres'] = apres
        MESURES[lieu]['ms'] = round((time.time() - debut) * 1000)
        verifier(apres and apres['display'] == 'none' and 'htmx-request' not in apres['classes'],
                 'P111c %s : indicateur encore affiché après la réponse %d : %s' % (lieu, statut, apres))
    finally:
        p.unroute('**/action', retenir)


def p111c_indicateur(banc, s):
    s.connecter()
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    chemin = _formulaire(s, p)
    verifier(chemin, 'P111c : aucune page du banc n’a de formulaire htmx dans la zone principale (%s)' % ', '.join(PAGES))
    MESURES['page'] = chemin
    s.propre('P111c chargement')
    repos = _etat(p)
    verifier(repos and repos['display'] == 'none' and repos['role'] == 'status', 'P111c : indicateur absent ou visible au repos : %s' % repos)
    verifier(p.locator('#ax-occupe').count() == 1, 'P111c : plusieurs indicateurs')
    verifier(p.get_attribute('#ws-main', 'hx-indicator') == 'closest *, #ax-occupe', 'P111c : zone principale sans hx-indicator')
    config = json.loads(p.get_attribute('meta[name="htmx-config"]', 'content'))
    verifier(config == {'allowEval': False, 'allowScriptTags': False, 'includeIndicatorStyles': False, 'selfRequestsOnly': True},
             'P111c : configuration htmx inattendue : %s' % config)
    verifier(p.evaluate("() => typeof htmx === 'object' && htmx.config.includeIndicatorStyles === false && htmx.config.allowEval === false"),
             'P111c : configuration htmx non appliquée dans la page')
    styles = p.evaluate("() => Array.from(document.querySelectorAll('style')).map(x => x.textContent.slice(0, 80))")
    verifier(not any('htmx-indicator' in x for x in styles), 'P111c : styles injectés par htmx : %s' % styles)
    # 1) réponse en erreur : l'indicateur disparaît
    _requete(s, p, 500, 'erreur')
    s.erreurs[:] = [x for x in s.erreurs if 'status of 500' not in x and '500' not in x]   # réponse 500 voulue (la console la note)
    s.propre('P111c après erreur')
    # 2) réponse réussie
    s.aller(chemin)
    _requete(s, p, 200, 'reussite')
    s.propre('P111c après réussite')
    # 3) thème sombre et 375 px : indicateur lisible et dans l'écran
    p.emulate_media(color_scheme='dark')
    p.set_viewport_size({'width': 375, 'height': 812})
    s.aller(chemin)
    largeur = p.evaluate('() => [document.documentElement.scrollWidth, innerWidth]')
    MESURES['largeur_375'] = largeur
    _requete(s, p, 500, 'sombre-375')
    s.erreurs[:] = [x for x in s.erreurs if '500' not in x]
    s.propre('P111c 375 px')
    p.emulate_media(color_scheme='light')
    p.set_viewport_size({'width': 1920, 'height': 1080})


PARCOURS_C = (('P111c-indicateur', 'Indicateur d’activité htmx : affiché pendant une vraie requête, retiré après erreur et après réussite, '
               'JSON htmx valide, aucun style injecté, thème sombre, 375 px', p111c_indicateur),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import os
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p111c'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_C))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS_C[0][0] for ident, _, _ in NAV.PARCOURS):
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS_C
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P111c-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
