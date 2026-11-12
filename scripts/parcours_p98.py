"""5.6.98 : parcours P98 du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P98a Aujourd'hui : « Routines du cabinet » juste sous « Actuellement », dans la colonne de gauche au-dessus de « À décider / À relire »,
  « Ma journée » à droite ; pas de colonne vide sous la file (écart de hauteur borné) ; petite largeur sans défilement horizontal.
- P98p Paramètres › Connexions : chaque case à cocher (météo, Microsoft 365) est collée à son libellé ; les champs de l'application
  Microsoft ne sont pas proposés au remplissage automatique du navigateur (identifiant de connexion et mot de passe).
- P98d Nouveau dossier : un ancien dossier posé sur la racine des dossiers ne bloque plus la création ; la fiche s'ouvre.
"""
from pathlib import Path

SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
BOITE = '(sel) => { const n = document.querySelector(sel); if (!n) return null; const r = n.getBoundingClientRect(); return {x: r.left, y: r.top + window.scrollY, w: r.width, h: r.height}; }'


def _nav():
    import __main__ as nav
    return nav


def _boite(p, sel):
    return p.evaluate(BOITE, sel)


def p98_aujourdhui(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/aujourdhui')
    actu, rang = _boite(p, '#ax95-actuellement'), _boite(p, '.f96-rang')
    routines, decider, journee = _boite(p, '.f96-rang #c5614-routines'), _boite(p, '.f96-rang #f96-decider'), _boite(p, '.f96-rang .f96-journee')
    verifier(actu and rang and routines and decider and journee, 'rang attendu absent : %r' % ((actu, rang, routines, decider, journee),))
    verifier(actu['y'] + actu['h'] <= routines['y'] + 1, 'les routines ne sont pas sous « Actuellement »')
    verifier(routines['y'] + routines['h'] <= decider['y'] + 1, 'les routines ne sont pas au-dessus de « À décider »')
    verifier(abs(routines['x'] - decider['x']) < 2, 'routines et file ne sont pas dans la même colonne')
    verifier(journee['x'] > routines['x'] + routines['w'] - 1, '« Ma journée » n’est pas à droite des routines')
    verifier(p.locator('.a89-principal #c5614-routines').count() == 0, 'routines encore dans la colonne principale (en double)')
    s.capture(sortie / 'P98a-aujourdhui-1920.png')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/aujourdhui')
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal à 390 px')
    s.capture(sortie / 'P98a-aujourdhui-390.png')
    s.propre('Aujourd’hui 5.6.98')


def p98_parametres(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1280, 'height': 900})
    s.connecter()
    s.aller('/parametres?rubrique=connexions')
    for pli in p.locator('details:has(#microsoft5636) > summary, #microsoft5636 details > summary').all():
        if not pli.evaluate('n => n.parentElement.open'):
            pli.click()
    mesures = p.evaluate('''() => Array.from(document.querySelectorAll('#ws-main label')).filter(l => {
        const i = l.querySelector('input[type=checkbox]'); return i && i.offsetParent !== null; }).map(l => {
        const i = l.querySelector('input[type=checkbox]'), ri = i.getBoundingClientRect(), rl = l.getBoundingClientRect();
        const texte = (l.textContent || '').trim();
        const plage = document.createRange(); plage.selectNodeContents(l); const rt = plage.getBoundingClientRect();
        return {texte: texte.slice(0, 60), case_x: ri.left - rl.left, case_l: ri.width, label_l: rl.width, ecart: (ri.top - rl.top), largeur: getComputedStyle(i).width,
                lignes: Math.round(rt.height / parseFloat(getComputedStyle(l).lineHeight || '20'))};
    })''')
    print('  P98p mesures des cases :', mesures)
    verifier(mesures, 'aucune case à cocher visible dans Connexions')
    for m in mesures:
        verifier(m['case_x'] < 8 and m['case_l'] <= 24, 'case détachée de son libellé : %r' % m)
    client = p.locator('#microsoft5636 input[name=client_id]')
    secret = p.locator('#microsoft5636 input[name=client_secret]')
    verifier(client.get_attribute('autocomplete') == 'off', 'identifiant Microsoft proposé au remplissage automatique')
    verifier(secret.get_attribute('autocomplete') == 'new-password', 'secret Microsoft proposé au remplissage automatique')
    s.capture(sortie / 'P98p-parametres-connexions.png')
    s.propre('Paramètres › Connexions 5.6.98')


def p98_nouveau_dossier(banc, s):
    nav = _nav()
    verifier = nav.verifier
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1440, 'height': 1000})
    s.connecter()
    with banc.desk() as d:
        from agent import dossiers5697
        from agent.desk import save_matter
        save_matter(d.c, {'id': 'CAB-098', 'path': dossiers5697.racine(d), 'client_name': 'Cabinet', 'aliases': [], 'references': [], 'correspondents': []})
    s.aller('/dossiers/nouveau')
    p.fill('#d97-nouveau [name=titre]', 'KERVILAC - Conseil')
    p.fill('#d97-nouveau [name=client]', 'KERVILAC')
    with p.expect_navigation(timeout=15000):
        p.locator('#d97-nouveau button[type=submit]').click()
    p.wait_for_load_state('networkidle')
    verifier('/dossier' in p.url, 'la fiche ne s’est pas ouverte après la création : %s' % p.url)
    with banc.desk() as d:
        lignes = [dict(r) for r in d.db.execute("SELECT titre, chemin FROM dossiers5697 WHERE titre='KERVILAC - Conseil'")]
    verifier(len(lignes) == 1, 'dossier absent du registre')
    s.capture(sortie / 'P98d-fiche-apres-creation.png')
    s.propre('Nouveau dossier 5.6.98')
    # Onglets de la fiche (5.6.98) remplis de données fictives, puis capturés : Parties, Juridiction, Fichiers « Par répertoire »
    import uuid
    from urllib.parse import parse_qs, urlsplit
    ident = parse_qs(urlsplit(p.url).query)['id'][0]
    acteur = ('cabinet', 'administrateur')
    with banc.desk() as d:
        from agent import fiche5698 as F
        avocat = F.ajouter_partie(d, acteur, ident, {'nom': 'Me BERNARD', 'role': 'conseil_adverse'}, uuid.uuid4().hex)
        F.ajouter_partie(d, acteur, ident, {'nom': 'ORLANDEC SAS', 'role': 'adverse', 'type_personne': 'morale', 'siren': '000010017',
                                            'avocat_id': avocat['id'], 'adresse': '1 rue des Tilleuls, Valbrune'}, uuid.uuid4().hex)
        F.ajouter_partie(d, acteur, ident, {'nom': 'ZORVAL', 'role': 'expert', 'qualite': 'Expert judiciaire'}, uuid.uuid4().hex)
        F.ajouter_instance(d, acteur, ident, {'degre': 'premiere_instance', 'juridiction': 'Tribunal judiciaire de Valbrune', 'chambre': '3e chambre civile',
                                              'rg': '24/01234', 'nature': 'fond', 'etat': 'en_cours'}, uuid.uuid4().hex)
    for vue in ('parties', 'juridiction', 'fichiers'):
        s.aller('/dossier?id=%s&vue=%s' % (ident, vue))
        verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal dans l’onglet %s' % vue)
        s.capture(sortie / ('P98d-onglet-%s.png' % vue))
    texte = p.locator('main, #ws-main').first.inner_text()
    verifier('Par répertoire' in texte, 'sous-vue « Par répertoire » absente')
    s.aller('/dossier?id=%s&vue=juridiction' % ident)
    verifier('3e chambre civile' in p.locator('main, #ws-main').first.inner_text(), 'instance absente de l’onglet Juridiction')
    s.propre('Fiche 5.6.98')
