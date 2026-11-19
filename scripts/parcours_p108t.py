"""5.6.108 (lot T) : parcours P108t du banc ``scripts/parcours-navigateur.py`` — Accès mobile (M01) et Assistance métier en cinq parcours
(vrai navigateur, version serveur du banc, données fictives, aucun réseau, aucun modèle).

P108t-mobile :
- ordinateur : ``/mobile`` sans téléphone associé explique sa fonction et mène aux réglages (« Téléphones associés » ouvert à l'ancre) ;
- association réelle par l'invitation du QR code (aperçu du lien sans effet, puis « Associer ») dans un second navigateur au format
  téléphone (375 × 812, tactile) ; retour sur ``/mobile?associe=1`` : bandeau, lecteur commun (même élément et même identifiant que
  « À relire » à l'ordinateur) ;
- téléphone : liste puis élément ouvert, « Retour à la liste » ; clavier virtuel simulé (hauteur 420 px) : Retenir et Écarter restent dans
  la zone visible ; zoom 125 % (300 px de large) : aucun débordement horizontal ;
- ancien lien ``/mobile?item=…`` : l'élément s'ouvre.
P108t-assistance :
- 1920 × 1080 : choix du dossier, cinq entrées et premier projet récent visibles sans défiler ; 1536 × 864 (125 %) : aucun débordement ;
- téléphone : parcours « Effectuer un calcul » (addition de jours), saisie conservée après « Retour aux cinq parcours », bouton du
  formulaire visible clavier ouvert, envoi, état « Reçue » puis « Résultat prêt » après le travail (file exécutée par le banc).
Aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc. Non inscrit dans ``PARCOURS`` : l'intégrateur
l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108t.py``.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
TELEPHONE = {'viewport': {'width': 375, 'height': 812}, 'is_mobile': True, 'has_touch': True, 'device_scale_factor': 2, 'locale': 'fr-FR'}

DEBORDEMENT_JS = """() => { const d = document.documentElement; return d.scrollWidth > d.clientWidth + 1 ? d.scrollWidth + ' > ' + d.clientWidth : ''; }"""
# commande visible : dans la fenêtre (hauteur réduite = clavier virtuel ouvert) et non recouverte par un autre élément
VISIBLE_JS = """(sel) => {
  const n = Array.from(document.querySelectorAll(sel)).find(x => { const r = x.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  if (!n) return 'absent';
  const r = n.getBoundingClientRect();
  if (r.top < 0 || r.bottom > window.innerHeight + 1 || r.left < 0 || r.right > window.innerWidth + 1)
    return 'hors de la fenêtre (' + Math.round(r.top) + '-' + Math.round(r.bottom) + ' / ' + window.innerHeight + ')';
  const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return (t === n || n.contains(t)) ? '' : 'recouvert par ' + (t ? t.tagName + '.' + t.className : '?');
}"""
DANS_LA_FENETRE_JS = """(sel) => { const n = document.querySelector(sel); if (!n) return 'absent';
  const r = n.getBoundingClientRect(); return r.bottom <= window.innerHeight + 1 && r.right <= window.innerWidth + 1 ? '' : Math.round(r.bottom) + ' > ' + window.innerHeight; }"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _sans_debordement(p, lieu):
    d = p.evaluate(DEBORDEMENT_JS)
    verifier(not d, '%s : débordement horizontal (%s)' % (lieu, d))


def _telephone(banc, s):
    """Second navigateur au format téléphone (sans session), surveillé comme le premier."""
    browser = s.context.browser
    ctx = browser.new_context(**TELEPHONE)
    return _nav().Session(banc, ctx)


# ------------------------------------------------------------------------------------------------ Accès mobile
def p108t_mobile(banc, s):
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    s.connecter()
    import parcours_p100 as p100
    p100.semer_propositions(banc, 2)                    # deux réponses à décider dans la file commune « À relire » (fictives)
    p = s.page
    r = s.aller('/mobile')
    verifier(r.status == 200, '/mobile sans téléphone : HTTP %d' % r.status)
    texte = p.inner_text('#ws-main') if p.locator('#ws-main').count() else p.inner_text('body')
    for attendu in ('Accès mobile', 'Aucun téléphone associé', 'Configurer les téléphones associés', 'Ouvrir ma file de relecture'):
        verifier(attendu in texte, '/mobile sans téléphone : « %s » absent' % attendu)
    verifier(not p.locator('[data-ax100-lecteur]').count(), '/mobile sans téléphone : la file est ouverte sans demande')
    s.propre('/mobile (ordinateur, sans téléphone)')
    # Paramètres › Confort et maintenance › Téléphones associés (lien profond : détail ouvert, bouton visible)
    p.locator('a', has_text='Configurer les téléphones associés').first.click()
    p.wait_for_load_state('networkidle')
    p.wait_for_timeout(500)
    verifier('rubrique=poste' in p.url, 'le lien des réglages mène à %s' % p.url)
    bouton = p.locator('[data-ap74="inviter"]')
    verifier(bouton.count() and bouton.first.is_visible(), '« Associer un téléphone » invisible après le lien profond')
    with p.expect_response(lambda x: 'm5674/inviter' in x.url and x.request.method == 'POST', timeout=15000) as rep:
        bouton.first.click()
    adresse = (rep.value.json() or {}).get('adresse', '')
    verifier(adresse and '/connexion/appareil?jeton=' in adresse, 'invitation sans adresse')
    p.wait_for_selector('#ap74-qr:not([hidden]) img, #ap74-qr:not([hidden]) svg', timeout=10000)
    s.propre('réglages : QR code affiché')
    p.screenshot(path=str(sortie / 'P108t-reglages-qr.png'))
    # téléphone : aperçu du lien (rien n'est consommé), puis « Associer »
    t = _telephone(banc, s)
    try:
        tp = t.page
        apercu = tp.goto(adresse, wait_until='load')
        verifier(apercu.status == 200, 'aperçu de l’invitation : HTTP %d' % apercu.status)
        verifier(tp.locator('form.cx48-form button[type=submit]').count(), 'aperçu de l’invitation : bouton « Associer » absent')
        tp.reload(wait_until='load')                  # un second aperçu reste possible : l'ouverture seule ne consomme rien
        verifier(tp.locator('form.cx48-form button[type=submit]').count(), 'invitation consommée par un simple aperçu')
        tp.fill('#ap74-nom', 'Téléphone fictif')
        with tp.expect_navigation():
            tp.click('form.cx48-form button[type=submit]')
        tp.wait_for_load_state('networkidle')
        verifier('/mobile' in tp.url, 'après l’association : %s' % tp.url)
        corps = tp.inner_text('body')
        verifier('Téléphone associé.' in corps, 'bandeau « Téléphone associé » absent après l’association')
        tp.wait_for_selector('[data-ax100-lecteur]', state='attached', timeout=10000)   # à 375 px : la liste d'abord, le lecteur ensuite
        tp.wait_for_selector('nav.ax100-liste li a[data-review-id]', timeout=15000)
        _sans_debordement(tp, '/mobile (375 px)')
        tp.screenshot(path=str(sortie / 'P108t-mobile-liste.png'))
        ident = tp.locator('nav.ax100-liste li a[data-review-id]').first.get_attribute('data-review-id')
        # même élément, même identifiant qu'à l'ordinateur (lecteur commun, aucune copie)
        s.aller('/a-relire')
        p.wait_for_selector('nav.ax100-liste li a[data-review-id]', timeout=15000)
        verifier(p.locator('nav.ax100-liste li a[data-review-id="%s"]' % ident).count() == 1,
                 'l’élément %s du téléphone n’est pas le même dans « À relire »' % ident)
        tp.locator('nav.ax100-liste li a[data-review-id]').first.click()
        tp.wait_for_timeout(1500)
        for sel in ('[data-ax100-lecteur] [data-action="retenir"]', '[data-ax100-lecteur] [data-action="ecarter"]'):
            verifier(tp.locator(sel).count(), 'téléphone : commande %s absente' % sel)
        tp.screenshot(path=str(sortie / 'P108t-mobile-element.png'))
        # clavier virtuel : la zone visible se réduit à 420 px ; Retenir et Écarter restent dans la fenêtre, non recouverts
        tp.set_viewport_size({'width': 375, 'height': 420})
        champ = tp.locator('[data-ax100-lecteur] textarea:visible').first
        if champ.count():
            champ.focus()
        tp.wait_for_timeout(400)
        for sel in ('[data-ax100-lecteur] [data-action="retenir"]', '[data-ax100-lecteur] [data-action="ecarter"]'):
            etat = tp.evaluate(VISIBLE_JS, sel)
            verifier(not etat, 'clavier ouvert : %s %s' % (sel, etat))
        tp.screenshot(path=str(sortie / 'P108t-mobile-clavier.png'))
        # zoom 125 % sur le téléphone (300 px de large) : rien ne sort de l'écran
        tp.set_viewport_size({'width': 300, 'height': 650})
        tp.wait_for_timeout(300)
        _sans_debordement(tp, '/mobile (zoom 125 %)')
        tp.set_viewport_size({'width': 375, 'height': 812})
        retour = tp.locator('[data-ax100-retour]:visible')
        if retour.count():
            retour.first.click()
            tp.wait_for_timeout(500)
            verifier(tp.locator('nav.ax100-liste li a[data-review-id]:visible').count(), '« Retour à la liste » ne montre pas la liste')
        # ancien lien reçu par notification : l'élément s'ouvre
        r = tp.goto(banc.url + '/mobile?item=' + ident, wait_until='load')
        verifier(r.status == 200, 'ancien lien /mobile?item= : HTTP %d' % r.status)
        tp.wait_for_selector('[data-ax100-lecteur] [data-action="retenir"]', state='attached', timeout=10000)
        t.propre('téléphone (/mobile)')
    finally:
        t.context.close()


# ------------------------------------------------------------------------------------------------ Assistance métier
def p108t_assistance(banc, s):
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    from agent import assistance35
    with banc.desk() as d:                             # un projet existant (calcul fictif) pour la liste des projets récents
        assistance35.calculate(d, {'matter': 'DOS-001', 'calculation_type': 'calendar_days', 'start_date': '2026-01-05', 'days': 3,
                                   'source_url': 'https://www.legifrance.gouv.fr/', 'source_reference': 'Référence fictive'})
    s.connecter()
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/assistance-metier')
    verifier(p.locator('.as108-carte').count() == 5, 'cinq entrées attendues')
    for sel in ('#as108-matter', '.as108-cartes li:nth-child(5) .as108-carte', '.as108-recents li:first-child'):
        etat = p.evaluate(DANS_LA_FENETRE_JS, sel)
        verifier(not etat, '1920 × 1080 : %s demande de défiler (%s)' % (sel, etat))
    verifier('VERSION 3.5.0' not in p.content(), 'surtitre de version encore affiché')
    _sans_debordement(p, 'Assistance métier (1920 × 1080)')
    p.screenshot(path=str(sortie / 'P108t-assistance-1920.png'))
    p.set_viewport_size({'width': 1536, 'height': 864})
    s.aller('/assistance-metier?outil=calcul&matter=DOS-001&type=interets')
    _sans_debordement(p, 'Assistance métier (125 %)')
    s.propre('Assistance métier (ordinateur)')
    # téléphone (session propre au téléphone)
    t = _telephone(banc, s)
    try:
        tp = t.page
        t.connecter()
        cible = '/assistance-metier?outil=calcul&matter=DOS-001&type=jours'
        t.aller(cible)
        _sans_debordement(tp, 'Assistance métier (375 px)')
        tp.fill('input[name=start_date]', '2026-02-28')
        tp.fill('input[name=days]', '1')
        tp.fill('input[name=source_url]', 'https://www.legifrance.gouv.fr/')
        tp.fill('input[name=source_reference]', 'Référence fictive à contrôler')
        tp.locator('a', has_text='Retour aux cinq parcours').first.click()
        tp.wait_for_load_state('networkidle')
        t.aller(cible)
        verifier(tp.input_value('input[name=days]') == '1' and tp.input_value('input[name=source_reference]') == 'Référence fictive à contrôler',
                 'saisie perdue après « Retour aux cinq parcours »')
        tp.set_viewport_size({'width': 375, 'height': 420})   # clavier virtuel
        tp.focus('input[name=source_reference]')
        tp.wait_for_timeout(300)
        etat = tp.evaluate(VISIBLE_JS, 'form.as108-form > button')
        verifier(not etat, 'clavier ouvert : bouton « Calculer » %s' % etat)
        tp.set_viewport_size({'width': 300, 'height': 650})
        _sans_debordement(tp, 'Assistance métier (zoom 125 % au téléphone)')
        tp.set_viewport_size({'width': 375, 'height': 812})
        tp.screenshot(path=str(sortie / 'P108t-assistance-avant-envoi.png'), full_page=True)
        with tp.expect_response(lambda x: x.url.endswith('/action') and x.request.method == 'POST', timeout=15000) as envoi:
            tp.locator('form.as108-form > button').first.click()
        verifier(envoi.value.status in (200, 303), 'envoi du calcul : HTTP %d' % envoi.value.status)
        # reçu de l'interface « en direct » (live430) dans le formulaire : demande enregistrée, numéro et état ; la saisie reste en place
        tp.wait_for_selector('form.as108-form .live-receipt', timeout=10000)
        travaux = banc.travaux('calculate35')
        verifier(len(travaux) == 1 and travaux[0]['status'] == 'pending', 'demande attendue en file : %s' % travaux)
        tp.screenshot(path=str(sortie / 'P108t-assistance-recu.png'), full_page=True)
        job = travaux[0]['id']
        tp.goto(banc.url + '/assistance-metier?job=%d&matter=DOS-001&outil=calcul&type=jours' % job, wait_until='load')   # l'état se relit seul toutes les 4 s
        verifier('Reçue, en attente de traitement' in tp.inner_text('body'), 'état « Reçue » absent')
        banc.travailler()
        tp.wait_for_function("() => document.body.innerText.includes('Résultat prêt')", timeout=20000)   # relecture automatique de l'état
        tp.wait_for_load_state('load')
        corps = tp.inner_text('body')
        verifier('Résultat prêt' in corps and 'Date arithmétique : 01/03/2026' in corps, 'résultat absent après le travail')
        verifier('pas une échéance de procédure' in corps, 'la date arithmétique n’est pas distinguée d’un délai')
        verifier(not tp.locator('.as108-resultat pre:visible').count(), 'JSON visible hors du diagnostic avancé')
        _sans_debordement(tp, 'résultat (375 px)')
        tp.screenshot(path=str(sortie / 'P108t-assistance-telephone.png'), full_page=True)
        t.propre('Assistance métier (téléphone)')
    finally:
        t.context.close()


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108t'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default='P108t-mobile,P108t-assistance')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + (('P108t-mobile', 'Accès mobile : explication, association réelle, lecteur commun au téléphone', p108t_mobile),
                                   ('P108t-assistance', 'Assistance métier : cinq entrées, calcul au téléphone, saisie conservée', p108t_assistance))
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
