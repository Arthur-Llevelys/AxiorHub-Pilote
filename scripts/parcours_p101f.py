"""5.6.101 : parcours P101f du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P101f (cahier § 6.4, V101-07) « Toutes les fonctions » en familles : le panneau montre au plus 35 entrées (33 familles) ; trois familles
ouvertes depuis le panneau (Produire, Pièces et contentieux, Paramètres) portent leur barre d'onglets avec l'onglet actif ; changement
d'onglet au clavier (flèche droite puis Entrée) ; ancienne adresse ouverte comme depuis un favori (``/playbooks`` : famille « Rédiger ») ;
aucun défilement horizontal à 200 % (960 × 475) ni à 390 px ; un envoi du compteur d'usage part en POST après l'affichage, aucun pendant
le chargement de la page. À enregistrer par l'intégrateur dans ``parcours-navigateur.py`` et ``test_v5694_parcours``.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def p101f_familles(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    envois = []
    p.on('request', lambda r: envois.append((r.method, r.url)) if '/api440/m56101/usage' in r.url else None)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/aujourdhui')
    p.locator('#f5631-toggle').click()
    entrees = p.locator('#f5631-all li[data-f101-famille]:not([hidden])')
    verifier(1 <= entrees.count() <= 35, 'le panneau doit montrer au plus 35 familles : %d' % entrees.count())
    verifier(p.locator('#f5631-all li[data-search-only]:not([hidden])').count() == 0, 'des fonctions isolées sont visibles sans recherche')
    p.fill('#f5631-q', 'bordereau')
    verifier(p.locator('#f5631-all li[data-search-only]:not([hidden])', has_text='Pièces et bordereaux (Pièces et contentieux)').count() == 1,
             'la recherche du panneau ne trouve pas « Pièces et bordereaux (Pièces et contentieux) »')
    s.capture(sortie / 'P101f-panneau.png')
    for famille, attendu in (('produire', '/production'), ('pieces_contentieux', '/pieces'), ('parametres', '/parametres')):
        s.aller('/aujourdhui')
        p.locator('#f5631-toggle').click()
        p.locator('#f5631-all li[data-f101-famille="%s"] a' % famille).first.click()
        try:   # 5.6.101 : attendre la navigation (la page d'origine est déjà chargée)
            p.wait_for_url(lambda u: u.split('?', 1)[0].endswith(attendu), timeout=15000)
        except Exception:   # noqa: BLE001 — le contrôle ci-dessous dit l'adresse obtenue
            pass
        p.wait_for_load_state('domcontentloaded')
        verifier(p.url.split('?', 1)[0].endswith(attendu), 'la famille %s n’ouvre pas %s : %s' % (famille, attendu, p.url))
        barre = p.locator('nav.f101-onglets[data-f101-famille="%s"]' % famille)
        verifier(barre.count() == 1, 'barre de la famille %s absente' % famille)
        verifier(barre.locator('a[aria-current="page"]').count() == 1, 'onglet actif absent dans la famille %s' % famille)
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal sur la famille %s' % famille)
    # clavier : focus sur l'onglet actif de Pièces et contentieux, flèche droite, Entrée → onglet suivant
    s.aller('/pieces')
    p.locator('nav.f101-onglets a[aria-current="page"]').focus()
    p.keyboard.press('ArrowRight')
    cible = p.evaluate('document.activeElement && document.activeElement.getAttribute("href")')
    verifier(bool(cible) and cible.endswith('/contentieux'), 'la flèche droite ne passe pas à l’onglet suivant : %r' % cible)
    p.keyboard.press('Enter')
    try:   # 5.6.101 : attendre la navigation (la page d'origine est déjà chargée)
        p.wait_for_url(lambda u: u.split('?', 1)[0].endswith('/contentieux'), timeout=15000)
    except Exception:   # noqa: BLE001 — le contrôle ci-dessous dit l'adresse obtenue
        pass
    p.wait_for_load_state('domcontentloaded')
    verifier(p.url.split('?', 1)[0].endswith('/contentieux'), 'Entrée n’ouvre pas l’onglet choisi : %s' % p.url)
    # favori : ancienne adresse ouverte directement
    s.aller('/playbooks')
    verifier(p.locator('nav.f101-onglets[data-f101-famille="rediger"] a[aria-current="page"]').count() == 1, '/playbooks sans la barre « Rédiger »')
    p.wait_for_timeout(300)
    verifier(any(m == 'POST' for m, _ in envois) and all(m == 'POST' for m, _ in envois), 'compteur d’usage : envoi absent ou autre que POST : %r' % envois)
    for vue, nom in (({'width': 960, 'height': 475}, '200 %'), ({'width': 390, 'height': 844}, '390 px')):
        p.set_viewport_size(vue)
        for chemin in ('/projets', '/routage-hybride', '/administration/fonctions-peu-utilisees'):
            s.aller(chemin)
            verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à %s sur %s' % (nom, chemin))
        s.capture(sortie / ('P101f-%s.png' % nom.replace(' ', '').replace('%', 'pc')))
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.propre('Toutes les fonctions en familles')
