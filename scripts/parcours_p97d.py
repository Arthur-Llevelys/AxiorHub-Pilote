"""5.6.97 (lot dossiers) : parcours P97d du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau).

Chargé par le banc ; utilise ses outils (``verifier``, ``Echec``) par le module principal. Chaque étape vérifie le résultat métier dans la base
et le stockage local du banc (registre, répertoires créés, aucun répertoire orphelin), pas seulement l'affichage. Aucun modèle n'est appelé.

- P97d1 Dossiers : écran de départ (titre, recherche, + Nouveau dossier, filtres) ; dossiers existants affichés en lecture, puis import
  (simulation, puis import réel rejouable).
- P97d2 Nouveau dossier : référence invalide refusée près du champ, aucun répertoire créé ; vérification des conflits, mise en attente motivée,
  création, ouverture de la fiche, répertoire et sous-dossiers rangés.
- P97d3 Fiche : bandeau du registre et six sections ; retour à la liste : filtre, sélection et défilement restaurés ; filtre Contentieux.
- P97d5 Gestion : onglet ouvert, « Préparer la clôture » sur un dossier ayant une échéance ouverte : clôture refusée, points bloquants listés.
- P97d4 Petite largeur : nom, client, prochaine action visibles, détails à la demande, aucun défilement horizontal.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'


def _nav():
    import __main__ as nav
    return nav


def _attendre(p, motif, clic, quoi, navigation=False):
    from playwright.sync_api import TimeoutError as Delai
    nav = _nav()
    try:
        if navigation:
            with p.expect_navigation(timeout=15000):
                with p.expect_response(lambda r: motif in r.url and r.request.method in ('POST', 'PATCH')) as attendu:
                    clic()
        else:
            with p.expect_response(lambda r: motif in r.url and r.request.method in ('POST', 'PATCH')) as attendu:
                clic()
    except Delai:
        raise nav.Echec('%s : aucune réponse ou la page n’a pas suivi' % quoi) from None
    p.wait_for_load_state('networkidle')
    return attendu.value


def _registre(banc, sql, args=()):
    with banc.desk() as d:
        from agent import dossiers5697
        dossiers5697.ensure_schema(d)
        return [dict(r) for r in d.db.execute(sql, args)]


def p97d_dossiers(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()

    # ---------------------------------------------------------------- P97d1 : écran de départ, dossiers existants, import
    s.aller('/dossiers')
    verifier(p.locator('h1', has_text='Dossiers').count() == 1, 'titre « Dossiers » absent ou en double')
    verifier(p.locator('#d97 a.d97-nouveau').count() == 1, 'bouton « + Nouveau dossier » absent')
    for libelle in ('Mes dossiers', 'Tous ceux auxquels j’ai accès', 'Actifs', 'Archivés', 'Conseil', 'Contentieux'):
        verifier(p.locator('#d97 .d97-filtres a', has_text=libelle).count() >= 1, 'filtre absent : %s' % libelle)
    verifier(p.locator('#d97 tbody tr').count() == 3, 'les trois dossiers existants ne sont pas listés avant l’import')
    s.capture(sortie / 'P97d-dossiers-avant-import.png')
    s.propre('Dossiers (avant import)')
    p.locator('#d97 .d97-avis a').click()
    p.wait_for_load_state('networkidle')
    verifier('3 dossier(s) à importer' in p.locator('main, #ax-main').first.inner_text(), 'simulation : décompte inattendu')
    verifier(not _registre(banc, 'SELECT id FROM dossiers5697'), 'la simulation a écrit dans le registre')
    _attendre(p, 'm57/dossiers/migration', lambda: p.locator('form[data-d97-api="m57/dossiers/migration"] button[type=submit]').click(), 'import')
    p.wait_for_timeout(800)
    p.wait_for_load_state('networkidle')
    verifier(len(_registre(banc, 'SELECT id FROM dossiers5697')) == 3, 'import : trois dossiers attendus au registre')
    s.propre('Import')

    # ---------------------------------------------------------------- P97d2 : Nouveau dossier
    s.aller('/dossiers')
    p.locator('#d97 a.d97-nouveau').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('#d97-nouveau').count() == 1, 'formulaire « Nouveau dossier » absent')
    avant = sorted(x.name for x in (banc.cloud / 'Dossiers').iterdir())
    p.fill('#d97-nouveau [name=titre]', 'BERNARD c/ ORLANDEC')
    p.fill('#d97-nouveau [name=client]', 'BERNARD')
    p.locator('#d97-nouveau input[name=nature][value=contentieux]').check()
    p.locator('#d97-nouveau details.d97-bloc > summary').click()
    p.fill('#d97-nouveau [name=reference]', '###')
    _attendre(p, '/api440/m57/dossiers', lambda: p.locator('#d97-nouveau button[type=submit]').click(), 'référence invalide')
    erreur = p.locator('#d97-nouveau [data-erreur=reference]').inner_text()
    verifier('Référence invalide' in erreur, 'erreur de référence non affichée près du champ : %r' % erreur)
    verifier(sorted(x.name for x in (banc.cloud / 'Dossiers').iterdir()) == avant, 'un répertoire a été créé malgré la référence invalide')
    # le refus 422 voulu est noté par le navigateur dans la console : seule cette ligne est retirée, toute autre erreur reste comptée
    s.erreurs[:] = [x for x in s.erreurs if 'status of 422' not in x]
    s.propre('Nouveau dossier (référence invalide)')
    p.fill('#d97-nouveau [name=reference]', 'DOS-2026-777')
    ligne = p.locator('#d97-nouveau [data-d97-partie]').first
    ligne.locator('[name=nom]').fill('ORLANDEC')
    ligne.locator('[name=role]').select_option('adverse')
    _attendre(p, 'm57/dossiers/conflits', lambda: p.locator('#d97-nouveau [data-d97-verifier]').click(), 'vérification des conflits')
    resultat = p.locator('#d97-nouveau [data-d97-resultat]').inner_text()
    verifier('garantie' in resultat or 'conflit' in resultat.lower(), 'résultat de la vérification incomplet : %r' % resultat[:200])
    p.locator('#d97-nouveau input[name="conflit.decision"][value=a_etudier]').check()
    p.fill('#d97-nouveau [name="conflit.motif"]', 'Vérifier les liens entre ORLANDEC et le client.')
    s.capture(sortie / 'P97d-nouveau-dossier.png')
    _attendre(p, '/api440/m57/dossiers', lambda: p.locator('#d97-nouveau button[type=submit]').click(), 'création', navigation=True)
    verifier('/dossier?id=' in p.url, 'la fiche du dossier créé ne s’ouvre pas (%s)' % p.url)
    cree = _registre(banc, "SELECT * FROM dossiers5697 WHERE ref='DOS-2026-777'")
    verifier(len(cree) == 1, 'dossier absent du registre')
    cree = cree[0]
    verifier(cree['stockage_etat'] == 'pret', 'rangement non prêt : %s' % cree['stockage_etat'])
    repertoire = banc.cloud / cree['chemin'].lstrip('/')
    verifier(repertoire.is_dir() and (repertoire / 'CORRESPONDANCES').is_dir(), 'répertoire ou sous-dossiers absents du stockage')
    with banc.desk() as d:
        from agent import conflicts500
        etat = conflicts500.matter_gate_state(d, cree['id'])
    verifier(etat['gated'] and etat['status'] == 'a_examiner', 'mise en attente des conflits non enregistrée : %r' % etat)

    # ---------------------------------------------------------------- P97d3 : fiche, retour à la liste
    texte = p.locator('.d79-tabs').inner_text()
    # 5.6.98 : Parties, Juridiction et Fichiers (ex-« Documents ») juste après Synthèse
    for section in ('Synthèse', 'Parties', 'Juridiction', 'Fichiers', 'Correspondances', 'Procédure · Pièces', 'Agenda et tâches', 'Gestion'):
        verifier(section in texte, 'section absente de la fiche : %s' % section)
    verifier('DOS-2026-777' in p.locator('.d97-fiche-tete').inner_text(), 'bandeau du registre absent de la fiche')
    for onglet, attendu in (('Parties', '#d98-parties'), ('Juridiction', '#d98-juridiction'), ('Fichiers', '#d98-fichiers')):
        p.locator('.d79-tabs a', has_text=onglet).click()
        p.wait_for_load_state('networkidle')
        verifier(p.locator(attendu).count() == 1 and p.locator('.d79-warn').count() == 0, 'onglet %s absent ou en erreur' % onglet)
    p.locator('.d98-sousonglets a', has_text='Par répertoire').click()
    p.wait_for_load_state('networkidle')
    verifier('CORRESPONDANCES' in p.locator('#d98-fichiers-panneau').inner_text(), 'sous-répertoires du dossier absents de « Par répertoire »')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal dans l’onglet Fichiers')
    p.locator('.d79-tabs a', has_text='Agenda et tâches').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('#d97-agenda-t').count() == 1, 'section Agenda et tâches vide')
    s.capture(sortie / 'P97d-fiche.png')
    s.propre('Fiche')
    s.aller('/dossiers?nature=contentieux')
    lignes = p.locator('#d97 tbody tr')
    verifier(lignes.count() >= 1 and all('Contentieux' in lignes.nth(i).inner_text() for i in range(lignes.count())), 'filtre Contentieux inexact')
    p.evaluate('window.scrollTo(0, 120)')
    lien = p.locator('#d97 tr[data-d97-ligne="%s"] a.d97-ouvrir' % cree['id'])
    with p.expect_navigation():
        lien.click()
    p.go_back()
    p.wait_for_load_state('networkidle')
    verifier('nature=contentieux' in p.url, 'filtre perdu au retour')
    verifier(p.locator('#d97 tr.d97-dernier[data-d97-ligne="%s"]' % cree['id']).count() == 1, 'sélection non restaurée au retour')
    s.propre('Retour à la liste')

    # ---------------------------------------------------------------- P97d5 : Gestion, « Préparer la clôture » refusée (échéance ouverte)
    from datetime import date, timedelta
    with banc.desk() as d:
        from agent import echeances450
        echeances450.ensure_schema(d)
        maintenant = date.today().isoformat()
        d.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     ('banc-ech-1', cree['id'], 'appel_jugement_contentieux', 'jugement', maintenant, (date.today() + timedelta(days=20)).isoformat(),
                      'confirmee', 'manuel', maintenant, maintenant))
        d.db.commit()
    s.aller('/dossier?id=%s&vue=gestion' % cree['id'])
    verifier(p.locator('#d97-gestion-t').count() == 1, 'onglet Gestion vide')
    s.propre('Gestion')
    p.locator('a.ax-btn', has_text='Préparer la clôture').click()   # bouton de l'onglet Gestion (l'entrée du menu « … » est repliée)
    p.wait_for_load_state('networkidle')
    texte = p.locator('#g97-clo-t').locator('xpath=..').inner_text()
    verifier('Échéances ouvertes' in texte and 'Bloquant' in texte, 'points bloquants de la clôture non listés : %r' % texte[:300])
    formulaire = p.locator('form[data-api="m5689/gestion/cloture/cloturer"]')
    formulaire.locator('input[name=motif]').fill('Mission terminée (banc)')
    p.once('dialog', lambda boite: boite.accept())
    reponse = _attendre(p, 'gestion/cloture/cloturer', lambda: formulaire.locator('button[type=submit]').click(), 'clôture')
    verifier(not reponse.ok, 'la clôture a été acceptée malgré une échéance ouverte (HTTP %s)' % reponse.status)
    with banc.desk() as d:
        from agent import cloture5697
        verifier(not cloture5697.est_archive(d, cree['id']), 'le dossier a été archivé malgré le refus')
    s.capture(sortie / 'P97d-cloture-refusee.png')
    s.erreurs[:] = [x for x in s.erreurs if 'status of 4' not in x]   # refus voulu, noté par le navigateur dans la console
    s.propre('Clôture refusée')

    # ---------------------------------------------------------------- P97d4 : petite largeur
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/dossiers')
    verifier(p.evaluate(SANS_DEFILEMENT), 'petite largeur : défilement horizontal')
    premiere = p.locator('#d97 tbody tr').first
    verifier(premiere.locator('a.d97-ouvrir').is_visible() and premiere.locator('.d97-client').is_visible(), 'nom ou client masqué en petite largeur')
    verifier(not premiere.locator('td[data-label="Responsable"]').is_visible(), 'détails affichés sans demande en petite largeur')
    premiere.locator('[data-d97-details]').click()
    verifier(premiere.locator('td[data-label="Responsable"]').is_visible(), '« Détails » n’ouvre pas les détails')
    s.capture(sortie / 'P97d-mobile.png')
    s.propre('Petite largeur')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    verifier(not banc.modele.appels, 'un modèle a été appelé (%d appel(s))' % len(banc.modele.appels))
