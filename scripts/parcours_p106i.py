"""5.6.106 (lot I) : parcours P106i du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P106i — images et pages de PDF pour les modèles capables de voir :
- volet de l'assistant à 1920 × 950 : bouton « 🖼 Joindre une image » dans la rangée d'envoi, sans faire passer « Envoyer » hors de l'écran ;
  sans dossier choisi, le clic dit qu'il faut d'abord un dossier (aucune boîte vide) ; Échap ferme la boîte, focus rendu ;
- avec le dossier fictif du banc : la boîte s'ouvre, liste les PDF et images connus (ou dit qu'il n'y en a pas), « Fermer » rend le focus ;
- Paramètres › Modèles d'IA : section « Images pour les modèles capables de voir » (avertissement « Le masquage n'est pas parfait »,
  journal des images transmises, sans image) ;
- 390 px : aucun défilement horizontal (volet ouvert et Paramètres).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def p106i_images(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/dossiers')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    bouton = p.locator('#ws-ai-dock .v106i-joindre')
    verifier(bouton.count() == 1, 'bouton « Joindre une image » absent du volet')
    verifier(p.evaluate('(() => { const r = document.getElementById("ws-ai-send").getBoundingClientRect(); return r.bottom <= innerHeight && r.right <= innerWidth; })()'),
             '« Envoyer l’instruction » sort de l’écran avec le bouton d’image')
    p.select_option('#ws-ai-matter', '')
    bouton.click()
    p.wait_for_timeout(300)
    verifier('dossier' in p.locator('#ws-ai-dock .v106i-avis').inner_text(), 'sans dossier : le besoin d’un dossier n’est pas dit')
    verifier(not p.locator('#ws-ai-dock dialog.v106i-dialog[open]').count(), 'sans dossier : boîte ouverte à vide')
    options = [o for o in p.locator('#ws-ai-matter option').evaluate_all('xs => xs.map(x => x.value)') if o]
    if options:
        p.select_option('#ws-ai-matter', options[0])
        bouton.click()
        p.wait_for_selector('#ws-ai-dock dialog.v106i-dialog[open]')
        p.wait_for_timeout(800)
        texte = p.locator('#ws-ai-dock dialog.v106i-dialog').inner_text()
        verifier('masquage' in texte.lower() or 'masquée' in texte.lower(), 'la boîte ne dit pas que l’image est masquée avant un envoi externe')
        s.capture(sortie / 'P106i-joindre-1920.png')
        p.locator('#ws-ai-dock dialog.v106i-dialog button:has-text("Fermer")').click()
        verifier(p.evaluate('document.activeElement && document.activeElement.classList.contains("v106i-joindre")'), 'focus non rendu au bouton après « Fermer »')
    s.propre('Volet de l’assistant : joindre une image')
    s.aller('/parametres?rubrique=ia')
    section = p.locator('#vision56106')
    verifier(section.count() == 1, 'section « Images pour les modèles capables de voir » absente')
    repli = p.locator('details:has(#vision56106) > summary')
    if repli.count() and not section.is_visible():
        repli.first.click()                    # section repliée par défaut dans Paramètres : la déplier comme l'utilisateur
    verifier(section.is_visible(), 'section des images non dépliable')
    verifier('pas parfait' in section.inner_text(), 'avertissement sur les limites du masquage absent')
    verifier('data:image' not in p.content(), 'une image apparaît dans le journal des transmissions')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 1920 px (Paramètres › IA)')
    s.capture(sortie / 'P106i-parametres-1920.png')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/dossiers')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (volet ouvert)')
    s.capture(sortie / 'P106i-volet-390.png')
    s.aller('/parametres?rubrique=ia')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px (Paramètres › IA)')
    s.propre('Images pour les modèles capables de voir à 390 px')
