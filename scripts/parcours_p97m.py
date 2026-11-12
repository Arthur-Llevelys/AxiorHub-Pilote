"""5.6.97 (lot documents-mission) : parcours P97m du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau).

Chargé par le banc ; utilise ses outils (``verifier``, ``Echec``, ``docx_fictif``) par le module principal. Le parcours vérifie le résultat métier
(registre, fichiers, versions, demandes) dans la base du banc, pas seulement l'affichage. Aucun modèle n'est appelé : le faux fournisseur compte ses
appels et le parcours exige zéro.

- P97m1 Aujourd'hui : « Dossiers sans prochaine action » ; « Ajouter une prochaine action » retire le dossier de la liste.
- P97m2 Nouveau document : courrier vierge, ouverture, modification, comparaison, version 2 ; conflit entre deux onglets puis copie ; import d'un
  fichier ; « Préparer avec l'IA » IA en pause : demande enregistrée, aucun appel.
- P97m3 Demande de mission : appel, qualification, partie, contrôle des conflits, décision, ouverture du dossier.
"""
from pathlib import Path


def _nav():
    import __main__ as nav
    return nav


def _attendre(p, motif, clic, quoi, navigation=True):
    """Clic d'envoi d'un formulaire du lot : réponse de l'API attendue (et la navigation qui suit si elle est prévue)."""
    nav = _nav()
    from playwright.sync_api import TimeoutError as Delai
    reponse = None
    try:
        if navigation:
            with p.expect_navigation(timeout=15000):
                with p.expect_response(lambda r: motif in r.url and r.request.method == 'POST') as attendu:
                    clic()
                reponse = attendu.value
        else:
            with p.expect_response(lambda r: motif in r.url and r.request.method == 'POST') as attendu:
                clic()
            reponse = attendu.value
    except Delai:
        nav.verifier(reponse is not None and reponse.ok, '%s : refusé (HTTP %s)' % (quoi, reponse.status if reponse else '?'))
        raise nav.Echec('%s : la page n’a pas suivi après l’envoi' % quoi) from None
    p.wait_for_load_state('networkidle')
    return reponse


def p97m_documents_mission(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    s.connecter()
    sortie = Path(banc.sortie)

    # ---------------------------------------------------------------- P97m1 : Aujourd'hui, deux listes
    s.aller('/aujourdhui')
    s.propre('Aujourd’hui')
    p.wait_for_selector('#m97-suivi')
    titre = p.locator('#m97-t-sans_action').inner_text()
    verifier('Dossiers sans prochaine action' in titre and '3' in titre, 'liste « sans prochaine action » : %r' % titre)
    verifier(p.locator('#m97-suivi a.m97-nouveau-doc').count() == 1, 'bouton « Nouveau document » absent d’Aujourd’hui')
    p.locator('#m97-suivi').screenshot(path=str(sortie / 'P97m-aujourdhui.png'))
    champ = p.locator('#m97-a-DOS-001')
    champ.fill('Appeler le client DURAND')
    _attendre(p, 'portefeuille/action', lambda: champ.press('Enter'), 'ajout de la prochaine action')
    titre = p.locator('#m97-t-sans_action').inner_text()
    verifier('2' in titre and p.locator('#m97-a-DOS-001').count() == 0, 'DOS-001 encore sans prochaine action : %r' % titre)
    with banc.desk() as d:
        row = d.db.execute('SELECT next_action_type FROM portefeuille5697 WHERE dossier_id=?', ('DOS-001',)).fetchone()
    verifier(row and row[0] == 'tache', 'prochaine action non enregistrée')
    s.propre('ajout d’une prochaine action')

    # ---------------------------------------------------------------- P97m2 : Nouveau document
    s.aller('/nouveau-document?dossier=DOS-001')
    s.propre('Nouveau document')
    p.select_option('#m97-type', 'courrier')
    p.fill('#m97-titre-doc', 'Courrier banc MARTIN')
    p.fill('input[name="valeurs.destinataire"]', 'Maître MARTIN')
    verifier(p.locator('[data-m97-rappel-texte]').inner_text().startswith('Ouvrez l’aperçu'), 'rappel du type absent')
    p.screenshot(path=str(sortie / 'P97m-nouveau-document.png'), full_page=True)
    _attendre(p, 'creation/creer', lambda: p.click('#m97-nouveau button[type=submit]'), 'création du document vierge')
    verifier('/document?id=' in p.url, 'pas de fiche après la création : %s' % p.url)
    texte = p.input_value('#m97-texte')
    verifier('Destinataire : Maître MARTIN' in texte and '[À COMPLÉTER : objet]' in texte, 'texte du document vierge : %r' % texte[:200])
    url_doc = p.url
    p.fill('#m97-texte', texte.replace('[À COMPLÉTER : objet]', 'Relance amiable'))
    p.click('[data-m97-comparer]')
    p.wait_for_selector('[data-m97-diff] li.m97-ajout')
    verifier('Objet : Relance amiable' in p.locator('[data-m97-diff]').inner_text(), 'comparaison sans l’ajout')
    _attendre(p, 'creation/enregistrer', lambda: p.click('.m97-editeur button[type=submit]'), 'enregistrement de la version 2')
    verifier('Version 2' in p.locator('.m97-meta').inner_text(), 'version 2 non affichée')
    # conflit entre deux onglets ouverts sur la même version
    autre = s.context.new_page()
    autre.goto(url_doc, wait_until='load')
    autre.wait_for_load_state('networkidle')
    p.fill('#m97-texte', p.input_value('#m97-texte') + '\nTexte de l’onglet A')
    _attendre(p, 'creation/enregistrer', lambda: p.click('.m97-editeur button[type=submit]'), 'version 3 (onglet A)')
    autre.fill('#m97-texte', autre.input_value('#m97-texte') + '\nTexte de l’onglet B')
    _attendre(autre, 'creation/enregistrer', lambda: autre.click('.m97-editeur button[type=submit]'), 'enregistrement de l’onglet B', navigation=False)
    autre.wait_for_selector('[data-m97-conflit-actions]:not([hidden])')
    message = autre.locator('.m97-editeur [data-m97-statut]').inner_text()
    verifier('a changé depuis votre ouverture' in message and 'n’a pas été enregistré' in message, 'conflit illisible : %r' % message)
    verifier('Texte de l’onglet B' in autre.input_value('#m97-texte'), 'le texte de l’onglet B a été perdu')
    autre.screenshot(path=str(sortie / 'P97m-conflit.png'), full_page=True)
    with autre.expect_navigation(timeout=15000):
        autre.click('[data-m97-copie]')
    autre.wait_for_load_state('networkidle')
    verifier('Texte de l’onglet B' in autre.input_value('#m97-texte'), 'la copie ne garde pas le texte de l’onglet B')
    autre.close()
    with banc.desk() as d:
        versions = d.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE title='Courrier banc MARTIN'").fetchone()[0]
    verifier(versions == 3, 'versions au registre : %d (3 attendues, rien d’écrasé)' % versions)
    # import d'un fichier
    s.aller('/nouveau-document?dossier=DOS-002')
    p.check('input[name=mode][value=import]')
    p.set_input_files('#m97-fichier', files=[{'name': 'Contrat importé MARTIN.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                               'buffer': nav.docx_fictif('Contrat importé.', 'Article premier.')}])
    _attendre(p, 'creation/importer', lambda: p.click('#m97-nouveau button[type=submit]'), 'import du fichier')
    verifier('Contrat importé.' in p.input_value('#m97-texte'), 'fichier importé illisible')
    # préparation avec l'IA, IA en pause
    from agent.arret5643 import PAUSE_GLOBAL
    with banc.desk() as d:
        d.setting(PAUSE_GLOBAL, True)
    s.aller('/nouveau-document?dossier=DOS-001')
    p.select_option('#m97-type', 'conclusions')
    p.check('input[name=mode][value=ia]')
    p.fill('#m97-consigne', 'Conclusions en réponse, bail commercial DURAND')
    with p.expect_response(lambda r: 'creation/creer' in r.url and r.request.method == 'POST') as attendu:
        p.click('#m97-nouveau button[type=submit]')
    verifier(attendu.value.ok, 'demande à l’IA refusée')
    p.wait_for_function("document.querySelector('#m97-nouveau [data-m97-statut]').textContent.indexOf('pause') >= 0")
    verifier(not banc.modele.appels, 'un modèle a été appelé (%d appels)' % len(banc.modele.appels))
    s.propre('Nouveau document')

    # ---------------------------------------------------------------- P97m3 : demande de mission
    s.aller('/demandes?nouvelle=1&origine=appel')
    s.propre('Nouvelle demande')
    p.fill('#m97-objet', 'Bail commercial KERVILAC')
    p.select_option('#m97-role', 'client')
    _attendre(p, 'demandes/creer', lambda: p.click('form[data-m97-api="demandes/creer"] button[type=submit]'), 'création de la demande')
    verifier('/demandes?id=' in p.url, 'pas de fiche de demande : %s' % p.url)
    p.fill('#m97-cn', 'KERVILAC')
    p.select_option('#m97-ty', 'conseil')
    p.fill('#m97-pn0', 'ORLANDEC')
    p.select_option('#m97-pr0', 'adverse')
    _attendre(p, 'demandes/modifier', lambda: p.click('form[data-m97-api="demandes/modifier"] button[type=submit]'), 'qualification')
    _attendre(p, 'demandes/conflits', lambda: p.click('form[data-m97-api="demandes/conflits"] button[type=submit]'), 'vérification des conflits')
    verifier('Contrôle valide' in p.locator('.m97-controle').inner_text(), 'contrôle non affiché')
    p.fill('#m97-motif', 'Rapport examiné : aucun conflit retenu.')
    _attendre(p, 'demandes/decider', lambda: p.click('form[data-m97-api="demandes/decider"] button[value=accepter]'), 'décision')
    verifier('Acceptée' in p.locator('.m97-etat').inner_text(), 'demande non acceptée')
    p.screenshot(path=str(sortie / 'P97m-demande-acceptee.png'), full_page=True)
    _attendre(p, 'demandes/convertir', lambda: p.click('form[data-m97-api="demandes/convertir"] button[type=submit]'), 'ouverture du dossier')
    verifier('/dossier?id=' in p.url, 'pas de dossier après la conversion : %s' % p.url)
    with banc.desk() as d:
        n = d.db.execute("SELECT COUNT(*) FROM demande_mission5697 WHERE etat='convertie' AND dossier_id IS NOT NULL").fetchone()[0]
    verifier(n == 1, 'demande non convertie')
    verifier(not banc.modele.appels, 'un modèle a été appelé pendant le parcours')
    s.propre('ouverture du dossier')
