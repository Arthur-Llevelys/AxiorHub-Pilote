"""5.6.106 (lot P) : parcours P106p du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P106p — Adresse @avocat.fr (CNB) dans Paramètres › Connexions, à 1920 × 950 puis à 390 px :
- carte « Adresse @avocat.fr (CNB) » présente et placée avant les autres choix de messagerie (Gmail, Microsoft), juste après la carte
  « Mise en service » qui reste la première section, seule ouverte ;
- réglages du CNB en lecture seule (imaps.avocat.fr 993, smtps.avocat.fr 465, SSL/TLS) ; réglages avancés repliés ;
- champ « Mot de passe applicatif » masqué ; lien vers auth.avocat.fr/deviceaccounts ouvert dans un nouvel onglet ;
- champ d'adresse modifiable ;
- aucun défilement horizontal.
« Tester » n'est jamais cliqué ici : le banc n'a pas de faux serveur avocat.fr et aucune connexion réelle n'est permise (les essais IMAP et
SMTP sont couverts par ``tests/test_v56106_avocatfr.py`` avec de faux serveurs). Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def p106p_avocatfr(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/parametres?rubrique=connexions')
    carte = p.locator('#avocatfr56106')
    verifier(carte.count() == 1, 'carte « Adresse @avocat.fr (CNB) » absente')
    ordre = p.evaluate("(() => { const a = document.getElementById('avocatfr56106'), b = document.getElementById('gmail56103') || document.getElementById('microsoft56103');"
                       " return !b || !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); })()")
    verifier(ordre, 'la carte avocat.fr n’est pas en tête des choix de messagerie')
    repli = p.locator('details:has(#avocatfr56106) > summary')
    formulaire = carte.locator('form.af106-form')
    if repli.count() and formulaire.count() and not formulaire.is_visible():
        repli.first.click()                    # carte repliée par défaut dans Paramètres : la déplier comme l'utilisateur
    texte = carte.inner_text()
    for attendu in ('imaps.avocat.fr', '993', 'smtps.avocat.fr', '465', 'SSL/TLS'):
        verifier(attendu in texte, 'réglage du CNB absent : %s' % attendu)
    verifier(formulaire.locator('input[name="mot_de_passe"][type="password"]').count() == 1, 'le mot de passe applicatif n’est pas un champ masqué')
    lien = carte.locator('a[href="https://auth.avocat.fr/deviceaccounts"]')
    verifier(lien.count() == 1 and lien.get_attribute('target') == '_blank' and 'noopener' in (lien.get_attribute('rel') or ''),
             'lien vers auth.avocat.fr/deviceaccounts absent ou sans nouvel onglet')
    avances = carte.locator('details.af106-avances')
    verifier(avances.count() == 1 and avances.get_attribute('open') is None, 'réglages avancés absents ou dépliés par défaut')
    adresse = formulaire.locator('input[name="adresse"]')
    verifier(adresse.count() == 1 and adresse.is_editable(), 'adresse non modifiable')
    adresse.fill('claire.durand' + '@' + 'avocat.fr')   # assemblée à l'exécution (aucune adresse littérale)
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 1920 px')
    s.capture(sortie / 'P106p-avocatfr-1920.png')
    s.propre('Adresse @avocat.fr')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/parametres?rubrique=connexions')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (Paramètres › Connexions, carte avocat.fr)')
    s.capture(sortie / 'P106p-avocatfr-390.png')
    s.propre('Adresse @avocat.fr à 390 px')
