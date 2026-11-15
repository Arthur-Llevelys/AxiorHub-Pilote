"""5.6.102 : parcours P102m du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, faux modèle du banc).

- P102m (lot M) Messagerie instantanée › Pilote : dans un groupe (Pilote présent d'office), « Résumer » et le menu « … » (« Retirer Pilote »)
  sont dans l'en-tête ; « @pilote prépare… » envoyé : l'indicateur « Pilote prépare une réponse… » s'affiche, aucun appel au modèle pendant la
  requête ; le travailleur du banc exécute la demande (une génération du faux modèle, sortie structurée) ; la réponse de Pilote apparaît,
  distincte, avec « Projet de courriel » et « Ouvrir à droite ». La préparation du projet (``docrequest520``) est simulée par le banc : le
  fichier Word fictif est écrit et inscrit au registre comme le ferait la fin du travail de rédaction. « Ouvrir à droite » : volet de
  relecture 5.6.101 ancré à droite à 1920 × 1080, quatre boutons visibles sans défiler ; à 390 px, fenêtre flottante, sans défilement
  horizontal (hors menu principal : son débordement éventuel est affiché comme NOTE, il relève du lot N). Captures : volet ancré (1920) et
  fenêtre flottante (390).

Enregistrement : ``parcours-navigateur.py`` (après P101v) et ``test_v5694_parcours``.
"""
import hashlib
import json
from pathlib import Path
import time

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
DEBORDE = """(horsMenu) => Array.from(document.querySelectorAll('body *')).filter(x => { const r = x.getBoundingClientRect();
  return r.width > 0 && r.right > innerWidth + 1 && !(horsMenu && x.closest('nav.ws-primary')); })
  .slice(0, 6).map(x => (x.id || (typeof x.className === 'string' ? x.className : '') || x.tagName) + ' « ' + (x.textContent || '').trim().slice(0, 30) + ' » :' + Math.round(x.getBoundingClientRect().right))"""
REPONSE = {'reponse': 'Je prépare le projet de courriel au confrère ; il sera dans « À relire ».',
           'actions': [{'type': 'preparer_courriel', 'titre': 'Demande de renvoi', 'consigne': 'Courriel au confrère pour solliciter un renvoi de l’audience.'}]}


def _nav():
    import __main__ as nav
    return nav


def _reponse_pilote(original):
    def repondre(corps):
        messages = corps.get('messages') or []
        if messages and 'Tu es « Pilote »' in str(messages[0].get('content') or ''):
            return json.dumps(REPONSE, ensure_ascii=False)
        if messages and 'résumé chronologique' in str(messages[0].get('content') or ''):
            return json.dumps({'resume': 'Résumé fictif : demande de renvoi préparée par Pilote.'}, ensure_ascii=False)
        return original(corps)
    return repondre


def semer_groupe(banc):
    from agent import messagerie5650 as mx, pilote_messagerie56102 as pm
    with banc.desk() as d:
        mx.ensure_schema(d)
        pm.ensure_schema(d)
        tid = mx.create_group(d, 'cabinet', 'Pôle contentieux', ['avocate@example.test'], {'avocate@example.test': {'name': 'Me DURAND', 'role': 'avocat'}})
        present = pm.present(d, tid)
    _nav().verifier(present, 'Pilote absent d’un nouveau groupe')
    return tid


def terminer_projet(banc):
    """Fin simulée de la rédaction : le projet de courriel demandé par Pilote devient un fichier Word inscrit au registre."""
    from agent import livrables5689
    nav = _nav()
    m = nav.MATTERS[0]
    chemin = '/Dossiers/%s - %s c. %s/CORRESPONDANCES/Demande de renvoi.docx' % (m[0], m[1], m[2])
    brut = nav.docx_fictif('Projet de courriel au confrère.', 'Nous sollicitons le renvoi de l’audience.', '[À COMPLÉTER : date souhaitée]')
    fichier = banc.cloud / chemin.lstrip('/')
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(brut)
    sha = hashlib.sha256(brut).hexdigest()
    maintenant = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    with banc.desk() as d:
        row = d.db.execute("SELECT id FROM docreq520 WHERE kind='courriel' ORDER BY created DESC LIMIT 1").fetchone()
        nav.verifier(row, 'Pilote n’a pas demandé de projet de courriel')
        d.db.execute("UPDATE docreq520 SET status='cree', matter=?, path=?, result=?, updated=? WHERE id=?",
                     (m[0], chemin, json.dumps({'title': 'Demande de renvoi', 'sha256': sha, 'readback_sha256': sha}), maintenant, row[0]))
        d.db.commit()
        livrables5689.synchroniser(d, force=True)
        doc = d.db.execute('SELECT document_id FROM prepared_items5689 WHERE path=? ORDER BY version_number DESC LIMIT 1', (chemin,)).fetchone()
    nav.verifier(doc, 'le projet de courriel n’a pas été inscrit au registre')
    return doc[0]


def p102m_pilote(banc, s):
    from parcours_p101v import BARRE_VISIBLE
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie) if getattr(banc, 'sortie', None) else None
    tid = semer_groupe(banc)
    banc.modele.reponse = _reponse_pilote(banc.modele.reponse)
    avant = banc.modele.nombre()
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/messagerie?fil=%d' % tid)
    p.wait_for_selector('#m50-messages li.m50-msg[data-genre="systeme"], #m50-messages .m50-placeholder', timeout=10000)
    verifier(p.locator('#p102-outils [data-p102-resume]').is_visible(), '« Résumé de la discussion » absent de l’en-tête')
    p.locator('#p102-outils summary').click()
    verifier(p.locator('#p102-outils [data-p102-presence]').inner_text().strip() == 'Retirer Pilote', 'menu « … » sans « Retirer Pilote »')
    p.locator('#p102-outils summary').click()

    # 1. « @pilote » : indicateur, aucun appel au modèle pendant la requête
    p.fill('#m50-text', '@pilote prépare le courriel au confrère pour demander le renvoi')
    p.click('#m50-compose button[type=submit]')
    p.wait_for_selector('#p102-attente:not([hidden])', timeout=10000)
    verifier('Pilote prépare' in p.locator('#p102-attente').inner_text(), 'indicateur « Pilote prépare une réponse… » absent')
    verifier(banc.modele.nombre() == avant, 'le modèle a été appelé pendant la requête web')
    verifier(len(banc.travaux('pilote_messagerie56102')) == 1, 'aucun travail de Pilote en file')

    # 2. le travailleur exécute la demande : une génération, réponse de Pilote distincte, projet annoncé
    banc.travailler(limite=1)
    verifier(banc.modele.nombre() == avant + 1, '%d appel(s) au modèle au lieu d’un' % (banc.modele.nombre() - avant))
    terminer_projet(banc)
    p.wait_for_selector('#m50-messages li.m50-ia', timeout=20000)
    p.wait_for_selector('#m50-messages li.m50-ia [data-p102-ouvrir]', timeout=20000)
    verifier(p.locator('#p102-attente').is_hidden(), 'indicateur encore affiché après la réponse')
    texte = p.locator('#m50-messages li.m50-ia').last.inner_text()
    verifier('Projet de courriel' in texte and 'rien ne sera envoyé' in texte, 'réponse de Pilote incomplète : %r' % texte[:300])

    # 3. « Ouvrir à droite » : volet ancré à 1920 × 1080, quatre boutons visibles sans défiler
    p.locator('#m50-messages li.m50-ia [data-p102-ouvrir]').last.click()
    p.wait_for_selector('dialog.f96-fenetre[open]:not([aria-busy])', timeout=15000)
    p.wait_for_selector('dialog.f96-fenetre[open] .v101-pied:not([hidden])', timeout=15000)
    verifier(p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 1, 'le volet n’est pas ancré à droite à 1920 px')
    boite = p.locator('dialog.f96-fenetre[open]').bounding_box()
    verifier(boite['x'] > 1920 * 0.45 and abs(boite['x'] + boite['width'] - 1920) < 2, 'volet pas à droite : %r' % boite)
    verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'quatre boutons hors de la vue (1920 × 1080) : %s' % p.evaluate(BARRE_VISIBLE))
    libelles = [x.strip() for x in p.locator('dialog.f96-fenetre[open] .v101-pied [data-v101-action]').all_inner_texts()]
    verifier(len(libelles) == 4, 'barre de décision : %r' % libelles)
    verifier(p.locator('#m50-compose textarea').is_visible(), 'la discussion n’est plus visible à côté du volet')
    if sortie:
        p.screenshot(path=str(sortie / 'P102m-volet-1920.png'))

    # 4. 390 px : fenêtre flottante, sans défilement horizontal
    p.set_viewport_size({'width': 390, 'height': 844})
    p.wait_for_timeout(400)
    verifier(p.locator('dialog.f96-fenetre[open]').count() == 1, 'le volet s’est fermé au passage à 390 px')
    verifier(p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 0, 'le volet reste ancré à 390 px')
    # le menu principal (lot N : libellé « Messagerie instantanée » de l'entrée active) n'est pas de ce lot : signalé, pas exigé ici
    menu = p.evaluate(DEBORDE, False)
    if menu and not p.evaluate(DEBORDE, True):
        print('    NOTE P102m : le menu principal déborde à 390 px sur /messagerie : %r' % menu)
    verifier(not p.evaluate(DEBORDE, True), 'défilement horizontal à 390 px : %r' % p.evaluate(DEBORDE, True))
    verifier(p.evaluate(BARRE_VISIBLE) == 'ok', 'quatre boutons hors de la vue (390 px) : %s' % p.evaluate(BARRE_VISIBLE))
    if sortie:
        p.screenshot(path=str(sortie / 'P102m-flottant-390.png'))
    p.locator('dialog.f96-fenetre[open] .f96-fenetre-fermer').click()
    verifier(p.locator('dialog.f96-fenetre[open]').count() == 0, 'Fermer ne ferme pas la fenêtre')
    verifier(not p.evaluate(DEBORDE, True), 'défilement horizontal à 390 px (discussion) : %r' % p.evaluate(DEBORDE, True))
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.propre('P102m')
    verifier(banc.modele.nombre() == avant + 1, 'appel au modèle en trop pendant l’ouverture du volet (%d)' % (banc.modele.nombre() - avant))

    # 5. « Résumé de la discussion » : volet de droite, mise à jour en file (jamais pendant la requête), rien posté dans la discussion
    reponses = p.locator('#m50-messages li.m50-ia').count()
    p.locator('#p102-outils [data-p102-resume]').click()
    p.wait_for_selector('dialog.f96-fenetre[open] .p102-resume', timeout=15000)
    verifier(p.locator('dialog.f96-fenetre.v101-ancre[open]').count() == 1, 'résumé : volet pas ancré à droite')
    verifier(banc.modele.nombre() == avant + 1, 'le modèle a été appelé pendant la demande de résumé')
    verifier(p.locator('dialog.f96-fenetre[open] .p102-resume-actions button').inner_text().strip() == 'Arrêter le suivi', 'bouton « Arrêter le suivi » absent')
    banc.travailler(limite=5)
    p.locator('dialog.f96-fenetre[open] .f96-fenetre-fermer').click()
    p.locator('#p102-outils [data-p102-resume]').click()
    p.wait_for_selector('dialog.f96-fenetre[open] .p102-resume-texte', timeout=15000)
    verifier('Résumé fictif' in p.locator('dialog.f96-fenetre[open] .p102-resume-texte').inner_text(), 'texte du résumé absent du volet')
    verifier('Mis à jour à' in p.locator('dialog.f96-fenetre[open] .f96-fenetre-dossier').inner_text(), 'horodatage du résumé absent')
    if sortie:
        p.screenshot(path=str(sortie / 'P102m-resume-1920.png'))
    p.locator('dialog.f96-fenetre[open] .p102-resume-actions button').click()
    p.wait_for_function("() => { const b = document.querySelector('dialog.f96-fenetre[open] .p102-resume-actions button'); return b && b.textContent.trim() === 'Reprendre le suivi'; }", timeout=10000)
    verifier(p.locator('#m50-messages li.m50-ia').count() == reponses, 'le résumé a été posté dans la discussion')
    p.locator('dialog.f96-fenetre[open] .f96-fenetre-fermer').click()
    s.propre('P102m résumé')
