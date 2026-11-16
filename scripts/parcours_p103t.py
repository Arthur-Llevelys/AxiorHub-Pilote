"""5.6.103 (lot T) : parcours P103t du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, faux modèle du banc).

P103t — Messagerie instantanée › « Tableau blanc » (Excalidraw embarqué), à 1920 × 1080 puis à 390 px :
- dans un groupe, « Tableau blanc » ouvre le volet de droite (ancré à 1920 px) : Excalidraw se charge depuis AxiorHub seulement (polices
  comprises ; toute requête externe fait échouer le banc), barre d'outils en français ;
- un rectangle dessiné part au serveur (m56103/tableau/envoyer) : la scène lue par l'API compte un élément, version 1 ;
- « @pilote dessine l'organigramme… » : aucun appel au modèle pendant la requête ; le travailleur exécute la demande (une génération du
  faux modèle, Mermaid « flowchart ») ; le volet affiche la proposition (aperçu converti dans le navigateur) avec « Ajouter au tableau » et
  « Annuler » ; « Ajouter au tableau » : la scène commune contient les éléments du schéma, la proposition disparaît ;
- 390 px : fenêtre flottante, sans défilement horizontal.
Rapports CSP attendus et filtrés ici seulement pendant la conversion Mermaid (styles en ligne du rendu caché de Mermaid, bloqués par
``style-src 'self'`` sans effet sur le résultat) ; toute autre erreur fait échouer le parcours. Non inscrit dans ``PARCOURS`` : l'intégrateur
l'inscrit et le lance.
"""
import json
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
MERMAID = 'flowchart TD\n  A["Associé DURAND"] --> B["Collaboratrice MARTIN"]\n  A --> C["Assistant BERNARD"]'


def _nav():
    import __main__ as nav
    return nav


def _reponse_dessin(original):
    def repondre(corps):
        messages = corps.get('messages') or []
        if messages and 'DESSINER' in str(messages[0].get('content') or ''):
            return json.dumps({'mermaid': MERMAID, 'titre': 'Organigramme du pôle', 'reponse': 'Voici une proposition.'}, ensure_ascii=False)
        return original(corps)
    return repondre


def _scene(banc, tid):
    from agent import tableau56103 as tb
    with banc.desk() as d:
        return tb.lire_scene(d, 'cabinet', tid)


def p103t_tableau(banc, s):
    from parcours_p102m import semer_groupe
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie) if getattr(banc, 'sortie', None) else None
    tid = semer_groupe(banc)
    banc.modele.reponse = _reponse_dessin(banc.modele.reponse)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/messagerie?fil=%d' % tid)
    bouton = p.locator('[data-t103-ouvrir]')
    verifier(bouton.count() == 1, 'bouton « Tableau blanc » absent de l’en-tête')
    bouton.click()
    p.wait_for_selector('dialog.t103-fenetre[open] .excalidraw .App-toolbar', timeout=30000)
    verifier(p.locator('dialog.t103-fenetre.v101-ancre[open]').count() == 1, 'le tableau blanc n’est pas ancré à droite à 1920 px')
    verifier(p.locator('dialog.t103-fenetre [data-testid="toolbar-rectangle"]').count() == 1, 'outil rectangle absent')
    s.propre('Ouverture du tableau blanc')

    # 1. un rectangle dessiné part au serveur
    p.locator('dialog.t103-fenetre label:has([data-testid="toolbar-rectangle"])').click()   # l’icône de l’outil, comme la souris de l’avocat (le bouton radio est masqué)
    zone = p.locator('dialog.t103-fenetre .excalidraw canvas.interactive').bounding_box()
    cx, cy = zone['x'] + zone['width'] * 0.55, zone['y'] + zone['height'] * 0.45   # centre de la zone : le panneau des propriétés occupe la gauche
    p.mouse.move(cx, cy)
    p.mouse.down()
    p.mouse.move(cx + 120, cy + 80, steps=6)
    p.mouse.up()
    p.wait_for_timeout(1500)
    lu = _scene(banc, tid)
    verifier([e['type'] for e in lu['elements']] == ['rectangle'] and lu['version'] >= 1, 'rectangle non enregistré : %r' % lu['elements'][:2])
    s.propre('Rectangle dessiné')
    if sortie:
        p.screenshot(path=str(sortie / 'P103t-tableau-1920.png'))

    # 2. « @pilote dessine » : travail en file, proposition, « Ajouter au tableau »
    avant = banc.modele.nombre()
    p.fill('#m50-text', '@pilote dessine l’organigramme du pôle contentieux')
    p.click('#m50-compose button[type=submit]')
    p.wait_for_timeout(800)
    verifier(banc.modele.nombre() == avant, 'le modèle a été appelé pendant la requête web')
    verifier(len(banc.travaux('tableau56103_dessin')) == 1, 'aucun travail de dessin en file')
    banc.travailler(limite=1)
    verifier(banc.modele.nombre() == avant + 1, '%d appel(s) au modèle au lieu d’un' % (banc.modele.nombre() - avant))
    p.wait_for_selector('dialog.t103-fenetre .t103-proposition button:has-text("Ajouter au tableau")', timeout=30000)
    s.erreurs[:] = [x for x in s.erreurs if not ('Content Security Policy' in x and ('style-src' in x or 'style' in x))]   # rendu caché de Mermaid
    verifier('Organigramme du pôle' in p.locator('dialog.t103-fenetre .t103-proposition').inner_text(), 'titre de la proposition absent')
    verifier(len(_scene(banc, tid)['elements']) == 1, 'l’aperçu est entré dans la scène commune sans « Ajouter au tableau »')
    p.locator('dialog.t103-fenetre .t103-proposition button:has-text("Ajouter au tableau")').click()
    p.wait_for_selector('dialog.t103-fenetre .t103-proposition', state='hidden', timeout=15000)
    lu = _scene(banc, tid)
    types = sorted({e['type'] for e in lu['elements']})
    verifier(len(lu['elements']) >= 6 and 'arrow' in types and 'text' in types, 'schéma non ajouté : %r' % types)
    verifier(lu['propositions'] == [], 'la proposition reste ouverte après « Ajouter au tableau »')
    s.propre('Proposition de Pilote ajoutée')

    # 3. 390 px : fenêtre flottante, sans défilement horizontal
    p.set_viewport_size({'width': 390, 'height': 844})
    p.wait_for_timeout(500)
    verifier(p.locator('dialog.t103-fenetre.v101-ancre[open]').count() == 0, 'le tableau reste ancré à 390 px')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px')
    if sortie:
        p.screenshot(path=str(sortie / 'P103t-tableau-390.png'))
    s.propre('Tableau blanc à 390 px')
