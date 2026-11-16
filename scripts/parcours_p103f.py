"""5.6.103 : parcours P103f du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P103f (lot F) « Fichiers » : la page s'ouvre depuis « Toutes les fonctions » (famille « Pièces et contentieux ») ; deux volets côte à côte
à 1920 px, chacun avec sa barre d'actions et sa liste chargée (jamais « Chargement… » qui reste, jamais un faux dossier vide si le stockage
répond mal : message d'état lisible) ; nouvel onglet dans le volet 1 ; filtre instantané ; à 390 px les volets deviennent deux onglets
(« Volet 1 », « Volet 2 ») au clavier (flèche droite) ; aucun défilement horizontal à 390 px ni à 200 % (960 × 475) ; aucune requête
d'écriture pendant l'affichage (seulement des GET vers ``/api440/m56103/fichiers/``). Si le banc n'a aucun stockage configuré, la page doit
le dire (lien vers le guide d'installation) et le parcours s'arrête là. À enregistrer par l'intégrateur dans ``parcours-navigateur.py``.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def p103f_fichiers(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    appels = []
    p.on('request', lambda r: appels.append((r.method, r.url)) if '/api440/m56103/fichiers/' in r.url else None)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/aujourdhui')
    p.locator('#f5631-toggle').click()
    p.fill('#f5631-q', 'explorateur')
    lien = p.locator('#f5631-all li[data-search-only]:not([hidden]) a[href$="/fichiers"]')
    verifier(lien.count() >= 1, 'la recherche du panneau ne trouve pas « Fichiers »')
    s.aller('/fichiers')
    verifier(p.locator('nav.f101-onglets[data-f101-famille="pieces_contentieux"] a[aria-current="page"]').count() == 1,
             'barre de la famille « Pièces et contentieux » absente ou sans onglet actif')
    if p.locator('.f103-alerte').count():
        verifier('guide-installation' in (p.locator('.f103-alerte a').first.get_attribute('href') or ''), 'stockage absent sans lien vers le guide')
        s.capture(sortie / 'P103f-sans-stockage.png')
        s.propre('Fichiers (aucun stockage sur le banc)')
        return
    volets = p.locator('.f103-volet')
    verifier(volets.count() == 2, 'deux volets attendus : %d' % volets.count())
    for n in range(2):
        etat = volets.nth(n).locator('[data-f103-etat]')
        try:
            p.wait_for_function('(el) => el.textContent && el.textContent.trim() !== "Chargement…"', arg=etat.element_handle(), timeout=15000)
        except Exception:   # noqa: BLE001 — le contrôle ci-dessous dit l'état obtenu
            pass
        verifier(etat.inner_text().strip() not in ('', 'Chargement…'), 'volet %d : liste jamais chargée (%r)' % (n + 1, etat.inner_text()))
    verifier(volets.nth(0).locator('[data-f103="renommer"]').count() == 1, 'bouton « Renommer par lot » absent')
    avant = volets.nth(0).locator('[data-f103-onglet]').count()
    volets.nth(0).locator('[data-f103="onglet"]').click()
    verifier(volets.nth(0).locator('[data-f103-onglet]').count() == avant + 1, 'le nouvel onglet n’apparaît pas')
    lignes = volets.nth(0).locator('[data-f103-lignes] tr')
    if lignes.count():
        volets.nth(0).locator('[data-f103-filtre]').fill('zzzz-aucun-nom')
        verifier(volets.nth(0).locator('[data-f103-lignes] tr[data-chemin]').count() == 0, 'le filtre instantané ne masque rien')
        volets.nth(0).locator('[data-f103-filtre]').fill('')
    s.capture(sortie / 'P103f-deux-volets.png')
    verifier(all(m == 'GET' for m, _ in appels), 'requête autre que GET pendant l’affichage : %r' % [a for a in appels if a[0] != 'GET'])
    for vue, nom in (({'width': 390, 'height': 844}, '390 px'), ({'width': 960, 'height': 475}, '200 %')):
        p.set_viewport_size(vue)
        s.aller('/fichiers')
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à %s' % nom)
        if nom == '390 px':
            verifier(p.locator('.f103-bascule').is_visible(), 'à 390 px, les volets ne deviennent pas des onglets')
            verifier(p.locator('#f103-volet-1').is_hidden(), 'à 390 px, le volet 2 reste affiché à côté du volet 1')
            p.locator('[data-f103-bascule="0"]').focus()
            p.keyboard.press('ArrowRight')
            verifier(p.locator('#f103-volet-1').is_visible() and p.locator('#f103-volet-0').is_hidden(), 'la flèche droite ne montre pas le volet 2')
        s.capture(sortie / ('P103f-%s.png' % nom.replace(' ', '').replace('%', 'pc')))
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.propre('Fichiers : deux volets, onglets, filtre, 390 px')
