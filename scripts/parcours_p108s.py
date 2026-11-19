"""5.6.108 (lot S) : parcours P108s du banc ``scripts/parcours-navigateur.py`` — deux instances AxiorHub locales (deux bancs, deux ports de
bouclage distincts, deux dossiers d'état), synchronisation des postes conduite dans un vrai navigateur. Données fictives, aucun réseau hors de
127.0.0.1, aucun modèle réel.

P108s-relier :
- poste A (Paramètres › Confort et maintenance) : « Créer un espace partagé » (nom du cabinet, personne), puis « Afficher un code » ;
- poste B : « Rejoindre le cabinet », adresse saisie à la main (Wi-Fi qui bloque la découverte) et code de A, « Appairer » ;
- poste A : demande d'adhésion de la personne de B, « Accepter » ;
- poste A : « Dossiers à partager » › « Partager » un dossier fictif ;
- poste B : le dossier apparaît dans « Dossiers » ; l'indicateur de l'en-tête passe à « À jour » sur les deux postes après les échanges ;
- 375 × 812 : rubrique sans défilement horizontal ; aucune erreur JavaScript ni CSP, aucune requête hors du banc.
Particularités du banc (déclarées) : la liaison de chaque poste tourne dans le processus du banc sur 127.0.0.1 (``boucle=True``) et
l'adresse de bouclage est admise pour l'appairage pendant ce parcours seulement (en production, seules les adresses privées le sont) ; les
échanges sont déclenchés par le parcours (le processus « liaison » de ``poste.py`` les ferait toutes les deux secondes).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul : ``python scripts/parcours_p108s.py [--sortie DOSSIER]``.
"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SECTION = '/parametres?rubrique=poste'


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _poste(banc, sess):
    """Le banc devient un poste de travail (la rubrique n'existe que là ; fichier d'authentification relu à chaque requête) et la fenêtre
    s'ouvre comme celle du poste : jeton de lancement à usage unique échangé contre la session locale (``/poste-session``)."""
    from agent.common import private_json
    from agent.poste_session import new_launch_token
    banc.auth['poste'] = True
    private_json(banc.auth_path, banc.auth)
    jeton = new_launch_token(banc.auth_path)
    sess.aller('/poste-session?jeton=' + jeton + '&suite=' + SECTION.replace('?', '%3F').replace('=', '%3D'))


def _liaison(banc):
    from agent import liaison56103 as L
    from agent.common import load_config
    from agent.desk import Desk
    if getattr(banc, 'liaison108', None) is None:
        banc.liaison108 = L.Liaison(lambda: Desk(load_config(str(banc.config_path))), boucle=True).demarrer('127.0.0.1', 0)
        with banc.desk() as d:
            d.setting(L.REGLAGE_PORT, banc.liaison108.port)
    return banc.liaison108


def _echanger(*bancs, tours=2):
    for _ in range(tours):
        for b in bancs:
            _liaison(b).synchroniser(forcer=True)


def _rouvrir(p):
    p.evaluate("() => { const x = document.getElementById('sync56108'); const d = x && x.closest('details'); if (d) d.open = true; }")


def _envoyer(p, motif, clic, quoi):
    """Envoi d'un formulaire de la rubrique (rechargement), puis rubrique dépliée de nouveau (elle est repliée au chargement)."""
    r = _nav().envoyer(p, motif, clic, quoi)
    p.wait_for_selector('#sync56108', state='attached')
    _rouvrir(p)
    return r


def _section(s):
    s.aller(SECTION)
    s.page.wait_for_selector('#sync56108', state='attached')
    s.page.evaluate("() => { const d = document.getElementById('sync56108').closest('details'); if (d) d.open = true; }")   # rubrique repliée
    s.page.wait_for_selector('#sync56108')
    return s.page


def p108s_relier(banc, s):
    nav = _nav()
    from agent import liaison56103 as L
    sortie = Path(getattr(banc, 'sortie', tempfile.gettempdir()))
    verifier_adresse = L.verifier_adresse
    L.verifier_adresse = lambda texte, boucle=False: verifier_adresse(texte, True)   # banc seulement : 127.0.0.1 admis pour l'appairage
    b = nav.Banc(tempfile.mkdtemp(prefix='axh-parcours-b-'))
    ctx_b = None
    try:
        b.sortie = sortie
        b.preparer()
        b.demarrer()
        ctx_b = s.context.browser.new_context(viewport={'width': 1366, 'height': 900}, locale='fr-FR')
        sb = nav.Session(b, ctx_b)
        for banc_, sess in ((banc, s), (b, sb)):
            _poste(banc_, sess)
        # ---------------------------------------------------------------- A : créer l'espace
        p = _section(s)
        verifier(p.locator('#sync56108 >> text=Créer un espace partagé').count() >= 1, 'A : « Créer un espace partagé » absent')
        verifier(p.locator('header .s108-pastille').count() == 0, 'A : indicateur dans l’en-tête alors que le poste travaille seul')
        form = p.locator('form[data-api="m56108/sync/creer"]')
        form.locator('input[name=nom]').fill('Cabinet Valbrune')
        form.locator('input[name=personne]').fill('Me DURAND')
        _envoyer(p, 'm56108/sync/creer', lambda: form.locator('button[type=submit]').click(), 'créer l’espace')
        s.capture(sortie / 'P108s-1-espace-cree.png')
        _envoyer(p, 'm56108/sync/code', lambda: p.locator('form[data-api="m56108/sync/code"] button').first.click(), 'afficher un code')
        code = p.locator('.s108-code strong').inner_text().strip()
        verifier(len(code.replace('-', '')) == 8, 'A : code d’appairage illisible')
        _liaison(banc)
        s.capture(sortie / 'P108s-2-code.png')
        s.propre('poste A : espace et code')
        # ---------------------------------------------------------------- B : rejoindre, adresse saisie à la main
        pb = _section(sb)
        f = pb.locator('form[data-api="m56108/sync/rejoindre"]')
        f.locator('input[name=personne]').fill('Me MARTIN')
        _envoyer(pb, 'm56108/sync/rejoindre', lambda: f.locator('button[type=submit]').click(), 'rejoindre le cabinet')
        manuel = pb.locator('form[data-api="m56108/sync/appairer"]').last
        manuel.locator('input[name=adresse]').fill('127.0.0.1')
        manuel.locator('input[name=port]').fill(str(banc.liaison108.port))
        manuel.locator('input[name=code]').fill(code)
        _envoyer(pb, 'm56108/sync/appairer', lambda: manuel.locator('button[type=submit]').click(), 'appairer B avec A')
        _liaison(b)
        _echanger(b)
        sb.capture(sortie / 'P108s-3-rejoindre.png')
        sb.propre('poste B : rejoindre et appairer')
        # ---------------------------------------------------------------- A : accepter l'adhésion, partager un dossier
        p = _section(s)
        verifier(p.locator('#sync56108 >> text=Me MARTIN').count() >= 1, 'A : la demande d’adhésion de Me MARTIN n’apparaît pas')
        s.capture(sortie / 'P108s-4-adhesion.png')
        _envoyer(p, 'm56108/sync/adhesion/accepter', lambda: p.locator('form[data-api="m56108/sync/adhesion/accepter"] button').first.click(),
                    'accepter l’adhésion')
        _echanger(b)
        from agent import dossiers5697 as D
        from agent.acces5697 import Acteur
        with banc.desk() as d:
            D.creer(d, Acteur('cabinet', 'administrateur'), {'titre': 'Bail commercial ZORVAL', 'reference': 'ZOR-990', 'client': 'ZORVAL'}, 'cle-parcours-p108s')
        p = _section(s)
        ligne = p.locator('#sync56108 li', has_text='ZOR-990')
        verifier(ligne.count() == 1, 'A : le dossier fictif n’est pas proposé au partage')
        _envoyer(p, 'm56108/sync/partager', lambda: ligne.locator('button[type=submit]').click(), 'partager le dossier')
        _echanger(b, banc, tours=3)
        # ---------------------------------------------------------------- B : le dossier apparaît ; indicateurs
        sb.aller('/dossiers')
        verifier(sb.page.locator('text=Bail commercial ZORVAL').count() >= 1, 'B : le dossier partagé n’apparaît pas dans Dossiers')
        sb.capture(sortie / 'P108s-5-dossier-recu.png')
        for nom, sess in (('A', s), ('B', sb)):
            pg = _section(sess)
            pastille = pg.locator('header .s108-pastille')
            verifier(pastille.count() == 1, '%s : indicateur absent de l’en-tête' % nom)
            verifier(pastille.inner_text().strip() == 'À jour', '%s : indicateur « %s » au lieu de « À jour »' % (nom, pastille.inner_text().strip()))
            sess.capture(sortie / ('P108s-6-indicateur-%s.png' % nom))
            pg.set_viewport_size({'width': 375, 'height': 812})
            verifier(pg.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), '%s : défilement horizontal à 375 px' % nom)
            sess.capture(sortie / ('P108s-7-mobile-%s.png' % nom))
            pg.set_viewport_size({'width': 1366, 'height': 900})
            sess.propre('poste %s : indicateur' % nom)
    finally:
        L.verifier_adresse = verifier_adresse
        for x in (banc, b):
            if getattr(x, 'liaison108', None) is not None:
                x.liaison108.arreter()
        if ctx_b is not None:
            ctx_b.close()
        b.arreter()


PARCOURS = (('P108s-relier', 'Synchronisation des postes : créer, code, rejoindre, accepter, partager, dossier reçu, indicateur', p108s_relier),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108s'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, ('P108s-relier',), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
