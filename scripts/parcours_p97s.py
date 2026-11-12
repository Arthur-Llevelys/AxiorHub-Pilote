"""5.6.97 (lot sources) : parcours P97s du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau).

- Paramètres › Recherche juridique et veille à 1920 × 1080 : cartes groupées par zone (France, Union européenne, Allemagne, Espagne,
  Angleterre et pays de Galles, Canada fédéral, Québec, entreprises), fiche « À vérifier » sans aucun formulaire, liens officiels dans un
  autre onglet ;
- adaptateur intégré « Droit fédéral allemand » sur un index chargé depuis l'échantillon écrit à la main : enregistrer l'accord,
  « Tester la connexion », « Activer » ; l'état réel s'affiche après rechargement ; aucune requête hors du banc ;
- guide d'installation, étape Sources : mêmes cartes et mêmes liens, « Configurer plus tard » présent ;
- petite largeur : pas de défilement horizontal.
"""
from pathlib import Path


def _nav():
    import __main__ as nav
    return nav


def _liens(p):
    return p.evaluate("""() => Array.from(document.querySelectorAll('article[data-provider] a')).map(a =>
        [a.closest('article').dataset.provider, a.getAttribute('href'), a.getAttribute('target'), a.getAttribute('rel')]).sort()""")


def p97s_sources(banc, s):
    nav = _nav()
    verifier = nav.verifier
    from agent import guide5631 as g, mcp_internationaux5697 as mi
    from agent.sources5632 import _index_racine
    racine = Path(__file__).resolve().parent.parent / 'tests' / 'fixtures' / 'sources5697'
    with banc.desk() as d:
        mi.importer(_index_racine(d), 'de_bund', [('bgb', (racine / 'de-bgb.xml').read_bytes())], 'echantillon')
        session = g.start(d, 'cabinet', ['recherches'])
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/parametres?rubrique=recherche')
    zones = p.evaluate("() => Array.from(document.querySelectorAll('section.s5697-zone')).map(x => x.dataset.zone)")
    verifier(zones == ['FR', 'UE', 'DE-BUND', 'ES-ETAT', 'GB-EAW', 'CA-FED', 'CA-QC', 'ENTREPRISES'], 'zones des sources : %s' % zones)
    a_verifier = p.locator('article[data-provider="openeu_auto"]')
    verifier(a_verifier.locator('form').count() == 0 and 'À vérifier' in a_verifier.inner_text(), 'la fiche OpenEU Auto propose une connexion')
    liens = _liens(p)
    verifier(liens and all(h.startswith('https://') and t == '_blank' and r == 'noopener noreferrer' for _, h, t, r in liens), 'lien officiel mal ouvert')
    carte = p.locator('article[data-provider="de_bund"]')
    verifier('Adaptateur AxiorHub' in carte.inner_text() and 'Index local du' in carte.inner_text(), 'carte de l’adaptateur allemand incomplète')
    if getattr(banc, 'sortie', None):
        carte.screenshot(path=str(Path(banc.sortie) / 'P97s-adaptateur.png'))
    s.propre('Paramètres › sources')
    carte.locator('form[data-m5632="admin/sources/connect"] input[name=allow_external]').check()
    nav.envoyer(p, 'admin/sources/connect', lambda: carte.locator('form[data-m5632="admin/sources/connect"] button[type=submit]').click(), 'enregistrer')
    carte = p.locator('article[data-provider="de_bund"]')
    test = carte.locator('form[data-m5632="admin/sources/verify"]').first
    nav.envoyer(p, 'admin/sources/verify', lambda: test.locator('button[type=submit]').click(), 'tester')
    carte = p.locator('article[data-provider="de_bund"]')
    verifier('connexion réussie' in carte.inner_text(), 'le dernier test n’est pas affiché')
    nav.envoyer(p, 'admin/sources/enable', lambda: carte.locator('form[data-m5632="admin/sources/enable"] button[type=submit]').click(), 'activer')
    verifier('activée' in p.locator('article[data-provider="de_bund"] h3').inner_text(), 'l’adaptateur n’apparaît pas activé')
    s.propre('activation de l’adaptateur')
    liens_parametres = _liens(p)
    s.aller('/guide-installation?session=%s&etape=sources' % session['id'])
    verifier(_liens(p) == liens_parametres, 'le guide et les Paramètres n’ont pas les mêmes liens')
    verifier(p.locator('[data-action="later"][data-option="sources"]').count() == 1, '« Configurer plus tard » absent du guide')
    s.propre('guide › sources')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/parametres?rubrique=recherche')
    verifier(p.evaluate(nav.SANS_DEFILEMENT_HORIZONTAL96), 'petite largeur : défilement horizontal')
    s.propre('mobile')
