"""5.6.106 (lot L) : parcours P106l du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

P106l — page « Courriels » en vrai client de messagerie, avec une fausse messagerie IMAP dans le processus du banc (``tests/faux_imap56106.py``) :
- densité (demande insistante de l'avocat), mesurée à 100 % de zoom : au moins 14 courriels entièrement visibles à 1920 × 950 et 9 à
  1366 × 768, la liste commence à moins de 160 px du haut de la zone de contenu, la page entière ne défile pas, texte de liste à 14 px au
  moins, objet en gras pour les non lus, étiquette d'état sur une ligne ;
- actions groupées : cocher trois courriels, « Marquer lu », puis « Supprimer » (vers la Corbeille) : résultat honnête affiché, messages
  réellement déplacés dans la fausse messagerie ; drapeau de suivi visible ;
- dossiers : créer un dossier, glisser un courriel dessus ; sous-dossier au nom d'un dossier client sous « Dossiers clients » ;
- raccourcis clavier : « ? » ouvre l'aide, J/K changent de courriel, « / » va à la recherche ;
- 390 px : liste et lecteur en onglets, aucun défilement horizontal ; zoom 200 % (960 × 475 CSS) : aucun défilement horizontal.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p106l.py [--avant]``
(``--avant`` : mesures seulement, sans exigence, pour comparer avec la présentation précédente).
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}

SANS_DEFILEMENT_HORIZONTAL = 'document.scrollingElement.scrollWidth <= window.innerWidth + 1'
MESURE_JS = """() => {
  const vh = window.innerHeight, rows = document.getElementById('bx5625-rows');
  const main = document.getElementById('ws-main');
  const lignes = Array.from(document.querySelectorAll('#bx5625-rows .ax-row[data-uid]'));
  const zone = rows ? rows.getBoundingClientRect() : {top: 0, bottom: vh};
  const haut = Math.max(0, zone.top), bas = Math.min(vh, zone.bottom);
  let visibles = 0;
  lignes.forEach(l => { const r = l.getBoundingClientRect(); if (r.height > 0 && r.top >= haut - 1 && r.bottom <= bas + 1) visibles++; });
  const premiere = lignes.length ? lignes[0].getBoundingClientRect() : null;
  const st = premiere ? getComputedStyle(lignes[0]) : null;
  const objet = premiere ? lignes[0].querySelector('strong, .bx56106-objet') : null;
  return {visibles: visibles, lignes: lignes.length,
          debut_liste: premiere ? Math.round(premiere.top - main.getBoundingClientRect().top) : -1,
          hauteur_ligne: premiere ? Math.round(premiere.height) : 0,
          police: objet ? parseFloat(getComputedStyle(objet).fontSize) : (st ? parseFloat(st.fontSize) : 0),
          defilement_page: document.scrollingElement.scrollHeight > vh + 1,
          defilement_horizontal: document.scrollingElement.scrollWidth > window.innerWidth + 1};
}"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def preparer_messagerie(banc, n=60):
    """Messagerie du banc : configuration activée (compte fictif), faux serveur IMAP partagé, ``agent.mailbox.connect`` remplacé dans le
    processus du banc (le serveur ``waitress`` tourne dans ce processus). Rend le faux serveur."""
    sys.path.insert(0, str(ROOT / 'tests'))
    from faux_imap56106 import Serveur, message
    from agent import mailbox as mbx
    from agent.common import private_json
    serveur = Serveur(separateur='/')
    serveur.semer(n)
    serveur.ajouter('Junk', message(900, objet='Offre promotionnelle fictive'), set())
    serveur.ajouter('INBOX', message(950, objet='Note interne fictive', corps='Mot temoin MOTCORPS106 dans le texte seulement.'), {'\\Seen'})
    secret = Path(banc.base) / 'donnees' / 'imap.secret'
    secret.write_text('mot-de-passe-fictif-du-banc', encoding='utf-8')
    banc.config['mail'].update({'enabled': True, 'username': 'cabinet', 'host': 'mail.example.test', 'password_file': str(secret),
                                'trash': 'Trash'})
    private_json(banc.config_path, banc.config)
    banc._connect_origine = mbx.connect
    mbx.connect = serveur.connexion
    return serveur


def restaurer_messagerie(banc):
    from agent import mailbox as mbx
    if getattr(banc, '_connect_origine', None) is not None:
        mbx.connect = banc._connect_origine


def attendre_liste(p):
    p.wait_for_selector('#bx5625-rows .ax-row[data-uid]', timeout=15000)
    p.wait_for_timeout(400)


def decocher(p):
    """Décoche tous les courriels, un par un comme l'utilisateur."""
    cochees = p.locator('#bx5625-rows .c5670-pick input:checked')
    while cochees.count():
        cochees.first.uncheck()


def mesurer(p, largeur, hauteur):
    p.set_viewport_size({'width': largeur, 'height': hauteur})
    p.wait_for_timeout(500)
    return p.evaluate(MESURE_JS)


def mesures_densite(banc, s, exiger=True):
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    out = {}
    for largeur, hauteur, minimum in ((1920, 950, 14), (1366, 768, 9)):
        p.set_viewport_size({'width': largeur, 'height': hauteur})
        s.aller('/courriels')
        attendre_liste(p)
        m = mesurer(p, largeur, hauteur)
        out['%dx%d' % (largeur, hauteur)] = m
        p.screenshot(path=str(sortie / ('P106l-courriels-%d.png' % largeur)))
        if exiger:
            verifier(m['visibles'] >= minimum, '%d × %d : %d courriels entièrement visibles (au moins %d attendus)' % (largeur, hauteur, m['visibles'], minimum))
            verifier(0 <= m['debut_liste'] < 160, '%d × %d : la liste commence à %d px du haut de la zone de contenu' % (largeur, hauteur, m['debut_liste']))
            verifier(not m['defilement_page'], '%d × %d : la page entière défile' % (largeur, hauteur))
            verifier(not m['defilement_horizontal'], '%d × %d : défilement horizontal' % (largeur, hauteur))
            verifier(m['police'] >= 14, '%d × %d : texte de la liste à %.1f px' % (largeur, hauteur, m['police']))
    if exiger:   # contraste AA (4,5:1) du texte de la liste, en clair puis en sombre
        for theme in ('light', 'dark'):
            p.evaluate("(t) => { document.documentElement.setAttribute('data-theme', t); }", theme)
            p.wait_for_timeout(200)
            c = p.evaluate(CONTRASTE_JS)
            out['contraste_' + theme] = c
            for nom, valeur in c.items():
                verifier(valeur >= 4.5, 'contraste %s insuffisant (%s) : %.2f' % (nom, theme, valeur))
            if theme == 'dark':
                p.screenshot(path=str(sortie / 'P106l-courriels-sombre.png'))
        p.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    MESURES.update(out)
    return out


CONTRASTE_JS = """() => {
  const rgb = (s) => (s.match(/[\\d.]+/g) || [0, 0, 0, 0]).map(Number);
  const lum = (c) => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
                       return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const fond = (n) => { while (n) { const c = rgb(getComputedStyle(n).backgroundColor); if (c.length < 4 || c[3] > 0.5) return c; n = n.parentElement; } return [255, 255, 255, 1]; };
  const out = {};
  [['expediteur', '.bx56106-de'], ['date', '.bx56106-date'], ['objet', '.bx56106-objet'], ['etat', '.bx56106-etat']].forEach(([nom, sel]) => {
    const n = document.querySelector('#bx5625-rows .ax-row[data-uid]:not(.on) ' + sel); if (!n) return;
    const a = lum(rgb(getComputedStyle(n).color)), b = lum(fond(n));
    out[nom] = Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100;
  });
  return out;
}"""


def p106l_courriels(banc, s):
    serveur = preparer_messagerie(banc)
    try:
        s.connecter()
        mesures_densite(banc, s, exiger=not getattr(banc, 'avant', False))
        s.propre('densité de la page Courriels')
        if getattr(banc, 'avant', False):
            return
        _fonctions(banc, s, serveur)
    finally:
        restaurer_messagerie(banc)


def _fonctions(banc, s, serveur):
    """Actions groupées, dossiers, glisser, raccourcis, petite largeur et zoom 200 %."""
    p = s.page
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.aller('/courriels')
    attendre_liste(p)
    # drapeau de suivi visible dans la liste ; non lus en gras
    verifier(p.locator('#bx5625-rows .ax-row[data-uid] .bx56106-suivi').count() >= 1, 'drapeau de suivi absent de la liste')
    gras = p.evaluate("() => { const r = document.querySelector('#bx5625-rows .ax-row.bx5625-unseen .bx56106-objet'); return r ? getComputedStyle(r).fontWeight : '0'; }")
    verifier(int(gras) >= 700, 'objet d’un courriel non lu non mis en gras (%s)' % gras)
    etiquette = p.locator('#bx5625-rows .ax-row[data-uid] .bx56106-etat').first
    if etiquette.count():
        verifier(etiquette.bounding_box()['height'] <= 24, 'étiquette d’état sur plusieurs lignes')
    # la barre d'actions apparaît dès qu'un courriel est coché, sur la ligne de « Tout cocher »
    barre = p.locator('#bx56106-actions')
    verifier(barre.count() == 1 and not barre.is_visible(), 'la barre d’actions groupées est visible sans sélection')
    cases = p.locator('#bx5625-rows .c5670-pick input')
    for i in range(3):
        cases.nth(i).check()
    verifier(barre.is_visible(), 'la barre d’actions groupées n’apparaît pas après avoir coché')
    tout = p.locator('.c5670-all')
    verifier(abs(tout.bounding_box()['y'] - barre.bounding_box()['y']) < 30, 'la barre d’actions n’est pas sur la ligne de « Tout cocher »')
    verifier(p.locator('#bx56106-actions .bx56106-actions-v[data-lot="V"]').count() == 1, 'emplacement du lot V absent')
    choisis = p.evaluate('() => window.axh56106liste.selection().map(x => x.uid)')
    verifier(len(choisis) == 3, 'window.axh56106liste.selection() ne rend pas les trois courriels cochés')
    # menu « Marquer » : clavier (flèches, Entrée, Échap), rôles ARIA, entrées sans effet grisées
    marquer = barre.locator('.bx56106-marquer-btn')
    verifier(marquer.get_attribute('aria-haspopup') == 'menu', 'bouton « Marquer » sans aria-haspopup="menu"')
    marquer.focus()
    p.keyboard.press('ArrowDown')
    menu = barre.locator('[role=menu].bx56106-marquer-menu')
    verifier(menu.is_visible() and marquer.get_attribute('aria-expanded') == 'true', 'le menu « Marquer » ne s’ouvre pas au clavier')
    verifier(menu.locator('[role=menuitem]').count() == 9, 'entrées du menu « Marquer » incomplètes')
    verifier(p.evaluate("document.activeElement.getAttribute('role')") == 'menuitem', 'le focus n’entre pas dans le menu')
    p.keyboard.press('ArrowDown')
    p.keyboard.press('Escape')
    verifier(not menu.is_visible() and p.evaluate("document.activeElement.classList.contains('bx56106-marquer-btn')"), 'Échap ne referme pas le menu sur son bouton')
    marquer.click()
    verifier(menu.locator('[data-action="not_junk"]').get_attribute('aria-disabled') == 'true', '« Comme non indésirable » non grisé hors Indésirables')
    with p.expect_response(lambda r: '/m56106/boite/actions' in r.url and r.request.method == 'POST'):
        menu.locator('[data-action="lu"]').click()
    p.wait_for_timeout(800)
    lus = [u for u in choisis if '\\Seen' in serveur.boites['INBOX']['uids'][int(u)][0]]
    verifier(len(lus) == 3, 'marquer lu : %d sur 3 dans la messagerie' % len(lus))
    attendre_liste(p)
    verifier(sorted(p.evaluate('() => window.axh56106liste.selection().map(x => x.uid)')) == sorted(choisis), 'la sélection n’est pas gardée après l’action')
    marquer.click()
    verifier(menu.locator('[data-action="lu"]').get_attribute('aria-disabled') == 'true', '« Comme lu » non grisé pour des courriels déjà lus')
    p.keyboard.press('Escape')
    # épingler (menu « Marquer ») : en tête de liste avec l'icône ; « Actualiser » garde la sélection et donne l'heure
    cible_ep = p.evaluate("() => document.querySelectorAll('#bx5625-rows .ax-row[data-uid]')[5].getAttribute('data-uid')")
    decocher(p)
    p.locator('#bx5625-rows .c5670-pick input[data-uid="%s"]' % cible_ep).check()
    marquer.click()
    with p.expect_response(lambda r: '/m56106/boite/actions' in r.url and r.request.method == 'POST'):
        menu.locator('[data-action="epingler"]').click()
    p.wait_for_timeout(1200)
    attendre_liste(p)
    premiere = p.locator('#bx5625-rows .ax-row[data-uid]').first
    verifier(premiere.get_attribute('data-uid') == cible_ep and 'bx56106-epinglee' in (premiere.get_attribute('class') or ''), 'le courriel épinglé n’est pas en tête')
    verifier(premiere.locator('.bx56106-epingler').inner_text().strip() == '📌', 'icône d’épingle absente')
    verifier(not [c for c in serveur.journal if c[:2] == ('UID', 'STORE') and 'Flagged' in ' '.join(c)][3:], 'épingler a posé un drapeau IMAP')
    with p.expect_response(lambda r: '/m5625/boite/liste' in r.url):
        p.locator('[data-bx56106="actualiser"]').click()
    attendre_liste(p)
    verifier('à jour à' in p.locator('#bx5625-pages .bx56106-heure').inner_text(), 'heure de la mise à jour absente')
    verifier(p.locator('#bx5625-rows .c5670-pick input[data-uid="%s"]' % cible_ep).is_checked(), '« Actualiser » perd la sélection')
    premiere.hover()
    with p.expect_response(lambda r: '/m56106/boite/actions' in r.url):
        premiere.locator('.bx56106-epingler').click()            # désépingler au survol, sans ouvrir le courriel
    p.wait_for_timeout(1200)
    verifier(p.locator('#bx5625-rows .ax-row.bx56106-epinglee').count() == 0, 'désépingler au survol sans effet')
    decocher(p)
    s.propre('menu « Marquer », épingles, Actualiser')
    # « Rechercher dans » : Corps coché (choix mémorisé), recherche faite par le serveur
    p.locator('details.bx56106-dans > summary').click()
    with p.expect_response(lambda r: '/m56106/boite/preferences' in r.url):
        p.locator('details.bx56106-dans input[value="corps"]').check()
    verifier('plus lente' in p.locator('details.bx56106-dans').inner_text(), 'avertissement sur la recherche dans le corps absent')
    p.locator('details.bx56106-dans > summary').click()
    p.locator('.bx5625-search input[name=q]').fill('MOTCORPS106')
    with p.expect_response(lambda r: '/m5625/boite/liste' in r.url and 'dans=' in r.url):
        p.locator('.bx5625-search input[name=q]').press('Enter')
    attendre_liste(p)
    verifier(p.locator('#bx5625-rows .ax-row[data-uid]').count() == 1, 'recherche dans le corps : résultat inattendu')
    verifier(any(c[:2] == ('UID', 'SEARCH') and 'BODY' in c for c in serveur.journal), 'la recherche dans le corps n’est pas faite par le serveur')
    s.aller('/courriels')
    attendre_liste(p)
    verifier(p.locator('details.bx56106-dans input[value="corps"]').is_checked(), '« Rechercher dans » non mémorisé')
    s.propre('Rechercher dans')
    # « Tout le dossier comme lu » : confirmation avec le nombre
    p.once('dialog', lambda d: (verifier('non lus' in d.message, 'confirmation sans le nombre : %s' % d.message), d.accept()))
    p.locator('#bx5625-rows .c5670-pick input').first.check()
    marquer.click()
    with p.expect_response(lambda r: '/m56106/boite/dossier/tout-lu' in r.url and r.status == 200):
        menu.locator('[data-action="tout_lu"]').click()
    p.wait_for_timeout(800)
    verifier(all('\\Seen' in e[0] for e in serveur.boites['INBOX']['uids'].values()), '« Tout le dossier comme lu » incomplet')
    attendre_liste(p)
    decocher(p)
    cases = p.locator('#bx5625-rows .c5670-pick input')
    for i in range(3):
        cases.nth(i).check()
    choisis = p.evaluate('() => window.axh56106liste.selection().map(x => x.uid)')
    s.propre('Tout le dossier comme lu')
    cases = p.locator('#bx5625-rows .c5670-pick input')
    for i in range(3):
        cases.nth(i).check()
    choisis = p.evaluate('() => window.axh56106liste.selection().map(x => x.uid)')
    p.once('dialog', lambda d: d.accept())
    with p.expect_response(lambda r: '/m56106/boite/actions' in r.url and r.request.method == 'POST') as rep:
        barre.locator('[data-action="trash"]').click()
    corps = rep.value.json()
    verifier(corps.get('reussis') == 3, 'suppression groupée : résultat inattendu %s' % corps.get('message'))
    verifier(all(int(u) not in serveur.boites['INBOX']['uids'] for u in choisis), 'les courriels supprimés sont restés dans la boîte de réception')
    verifier(len(serveur.boites['Trash']['uids']) >= 3, 'les courriels supprimés ne sont pas dans la Corbeille')
    verifier('3 ' in (p.locator('#ax-toast').inner_text() if p.locator('#ax-toast').count() else ''), 'résultat de l’action non annoncé')
    s.propre('actions groupées')
    # dossier créé puis courriel glissé dessus
    p.once('dialog', lambda d: d.accept('Expertises'))
    with p.expect_response(lambda r: '/m56106/boite/dossier/creer' in r.url):
        p.locator('[data-bx56106="nouveau-dossier"]').click()
    p.wait_for_selector('.bx5625-folder[title="Expertises"]', timeout=10000)
    verifier('Expertises' in serveur.boites, 'dossier non créé dans la messagerie')
    attendre_liste(p)
    ligne = p.locator('#bx5625-rows .ax-row[data-uid]').first
    uid = ligne.get_attribute('data-uid')
    with p.expect_response(lambda r: '/m56106/boite/actions' in r.url):
        ligne.drag_to(p.locator('.bx5625-folder[title="Expertises"]'))
    p.wait_for_timeout(500)
    verifier(int(uid) not in serveur.boites['INBOX']['uids'] and len(serveur.boites['Expertises']['uids']) == 1, 'le courriel glissé n’a pas été déplacé')
    s.propre('dossier créé et glisser-déposer')
    # raccourcis : « ? » (aide), J (suivant), « / » (recherche)
    attendre_liste(p)
    p.locator('body').press('?')
    verifier(p.locator('#bx56106-aide').is_visible(), '« ? » n’ouvre pas l’aide des raccourcis')
    p.keyboard.press('Escape')
    p.locator('#bx5625-rows .ax-row[data-uid]').first.click()
    p.wait_for_timeout(400)
    premier = p.evaluate("() => document.querySelector('#bx5625-rows .ax-row.on').getAttribute('data-uid')")
    p.wait_for_selector('#bx5625-panel .bx5628-bar .bx56106-marquer-btn', timeout=10000)
    verifier(not p.locator('#bx5625-panel .bx5628-bar [title="Marquer comme non lu"]').is_visible(), 'lecteur : « Non lu » séparé encore affiché')
    p.locator('#bx5625-panel .bx5628-bar .bx56106-marquer-btn').click()
    verifier(p.locator('#bx5625-panel [role=menu] [data-action="epingler"]').is_visible(), 'lecteur : « Épingler » absent du menu « Marquer »')
    p.keyboard.press('Escape')
    p.locator('#bx5625-rows .ax-row.on').focus()
    p.locator('#bx5625-rows .ax-row.on').press('j')
    p.wait_for_timeout(400)
    second = p.evaluate("() => document.querySelector('#bx5625-rows .ax-row.on').getAttribute('data-uid')")
    verifier(premier != second, 'J ne passe pas au courriel suivant')
    p.keyboard.press('/')
    verifier(p.evaluate("document.activeElement && document.activeElement.name === 'q'"), '« / » ne place pas le curseur dans la recherche')
    p.screenshot(path=str(sortie / 'P106l-courriels-actions.png'))
    s.propre('raccourcis')
    # 390 px : onglets Liste / Lecteur ; zoom 200 % (960 × 475 CSS)
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/courriels')
    attendre_liste(p)
    verifier(p.locator('.bx56106-onglets').is_visible(), '390 px : onglets Liste / Lecteur absents')
    verifier(p.evaluate(SANS_DEFILEMENT_HORIZONTAL), '390 px : défilement horizontal')
    p.locator('#bx5625-rows .ax-row[data-uid]').first.click()
    p.wait_for_timeout(500)
    verifier(p.locator('#bx5625-panel').is_visible() and not p.locator('#bx5625-rows').is_visible(), '390 px : le lecteur ne remplace pas la liste')
    p.screenshot(path=str(sortie / 'P106l-courriels-390.png'))
    p.set_viewport_size({'width': 960, 'height': 475})
    s.aller('/courriels')
    attendre_liste(p)
    verifier(p.evaluate(SANS_DEFILEMENT_HORIZONTAL), 'zoom 200 % : défilement horizontal')
    s.propre('390 px et zoom 200 %')


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--avant', action='store_true')
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p106l'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--tolerer', action='append', default=[], help='motif de console toléré (lancement seul : script d’un autre lot pas encore livré)')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    Banc = NAV.Banc

    class BancP106(Banc):
        avant = args.avant
    NAV.Banc = BancP106
    NAV.CONSOLE_IGNOREE = tuple(NAV.CONSOLE_IGNOREE) + tuple(args.tolerer)
    NAV.PARCOURS = NAV.PARCOURS + (('P106l', 'Courriels : densité, actions groupées, dossiers, raccourcis, 390 px', p106l_courriels),)
    code = NAV.lancer(args.navigateur, args.sortie, ('P106l',))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / ('P106l-mesures%s.json' % ('-avant' if args.avant else ''))).write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
