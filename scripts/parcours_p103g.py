"""5.6.103 (lot G) : parcours P103g du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P103g — Comptes Google et Microsoft de la mise en service, dans Paramètres › Connexions, à 1920 × 950 puis à 390 px :
- carte « Mise en service : comptes Google et Microsoft » : deux étapes avec leur état réel ; Microsoft sans application intégrée affiché
  « Pas encore disponible » (jamais une erreur) ;
- « Passer cette étape » (Google) : après rechargement, état « Passée » et bouton « Reprendre cette étape » ; « Reprendre » : retour à « À faire » ;
- carte « Compte Google (Gmail) » : méthode 1 ouverte, champ du mot de passe masqué ; un mot de passe habituel collé par erreur est refusé
  avant toute connexion (message « 16 caractères ») ; méthode 2 : étapes de l'application interne Workspace et URI de retour proposée ;
- aucun défilement horizontal.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def p103g_comptes(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/parametres?rubrique=connexions')
    carte = p.locator('#mise56103')
    verifier(carte.count() == 1, 'carte « Mise en service : comptes Google et Microsoft » absente')
    google = carte.locator('li[data-etape="google"]')
    microsoft = carte.locator('li[data-etape="microsoft"]')
    verifier(google.count() == 1 and microsoft.count() == 1, 'les deux étapes facultatives ne sont pas listées')
    etat_ms = microsoft.get_attribute('data-etat')
    verifier(etat_ms in ('indisponible', 'a_faire', 'passee', 'faite', 'a_refaire'), 'état Microsoft inattendu : %s' % etat_ms)
    if etat_ms == 'indisponible':
        verifier('Pas encore disponible' in microsoft.inner_text(), 'Microsoft sans application intégrée : « Pas encore disponible » absent')
    s.capture(sortie / 'P103g-connexions-1920.png')
    # Passer puis reprendre l'étape Google
    if google.get_attribute('data-etat') in ('a_faire', 'a_refaire'):
        google.locator('button:has-text("Passer cette étape")').click()
        p.wait_for_load_state('load')
        p.wait_for_timeout(900)
        s.aller('/parametres?rubrique=connexions')
        google = p.locator('#mise56103 li[data-etape="google"]')
        if google.get_attribute('data-etat') == 'passee':
            verifier(google.locator('button:has-text("Reprendre cette étape")').count() == 1, 'étape passée sans bouton « Reprendre »')
            google.locator('button:has-text("Reprendre cette étape")').click()
            p.wait_for_timeout(900)
            s.aller('/parametres?rubrique=connexions')
            google = p.locator('#mise56103 li[data-etape="google"]')
            verifier(google.get_attribute('data-etat') in ('a_faire', 'a_refaire'), 'l’étape reprise n’est pas revenue à son état réel')
        else:
            verifier(False, 'après « Passer », état Google : %s' % google.get_attribute('data-etat'))
    # Carte Gmail simple : mot de passe masqué, refus avant toute connexion
    gmail = p.locator('#gmail56103')
    verifier(gmail.count() == 1, 'carte « Compte Google (Gmail) » absente')
    formulaire = gmail.locator('form.gs103-imap')
    repli = p.locator('details:has(#gmail56103) > summary')
    if repli.count() and not formulaire.is_visible():
        repli.first.click()                    # carte repliée par défaut dans Paramètres : la déplier comme l'utilisateur
    verifier(formulaire.locator('input[name="mot_de_passe"][type="password"]').count() == 1, 'le mot de passe d’application n’est pas un champ masqué')
    formulaire.locator('input[name="adresse"]').fill('durand.cabinet@example.test')
    formulaire.locator('input[name="mot_de_passe"]').fill('MotDePasseHabituel2026!')
    formulaire.locator('button[data-api="m56103/gmail/tester"]').click()
    p.wait_for_timeout(1200)
    texte = formulaire.inner_text() + ' ' + (p.locator('#ax-toast').inner_text() if p.locator('#ax-toast').count() else '')
    verifier('16 caractères' in texte, 'mot de passe habituel non refusé en clair')
    verifier('MotDePasseHabituel2026!' not in p.content(), 'le mot de passe saisi réapparaît dans la page')
    gmail.locator('summary:has-text("Méthode 2")').click()
    verifier('Interne' in gmail.inner_text() and '/oauth/google/callback' in gmail.inner_text(), 'étapes de l’application interne absentes')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 1920 px')
    s.propre('Comptes Google et Microsoft')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/parametres?rubrique=connexions')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (Paramètres › Connexions)')
    s.capture(sortie / 'P103g-connexions-390.png')
    s.propre('Comptes Google et Microsoft à 390 px')
