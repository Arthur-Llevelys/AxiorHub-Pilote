"""5.6.108 (lot F) : parcours P108f du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P108f-onglet : onglet Fichiers du dossier DOS-001 (« Par répertoire ») : barre « Nouveau document » / « Ajouter un fichier » ; un PDF à champs
  ajouté par le sélecteur de fichiers, un texte par glisser-déposer (événement ``drop`` avec ses fichiers) ; les deux apparaissent dans la liste.
- P108f-remplir : « Remplir ce formulaire » : champs détectés à côté de l'aperçu PDF.js, propositions de la fiche (« Utiliser toutes les
  propositions »), case, choix unique, liste ; copie « … (rempli le …).pdf » déposée, original identique à l'octet près.
- P108f-outils : boîte à outils sur un PDF de deux pages : surlignage et note tracés sur l'aperçu, copie annotée ; page 2 retirée ; tampon
  « Pièce n° 12 » ; caviardage réel du nom fictif ZORVAL (si Poppler et Tesseract sont présents : ``AXIORHUB_WIN_OCR`` sous Windows) ;
  chaque fois un nouveau fichier, original intact. Aucune requête externe, aucune erreur JavaScript ni CSP (contrôles du banc).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108f.py``.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _sortie(banc):
    d = Path(getattr(banc, 'sortie', '.') or '.')
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fixtures():
    sys.path.insert(0, str(ROOT / 'tests'))
    import test_v56108_fichiers_dossier as FX
    return FX


def _dossier(banc):
    return next(p for p in (banc.cloud / 'Dossiers').iterdir() if p.name.startswith('DOS-001'))


def _semer(banc, nom, brut):
    d = _dossier(banc)
    (d / nom).write_bytes(brut)
    return '/Dossiers/%s/%s' % (d.name, nom), d


def _attendre(p, expression, secondes=30.0):
    fin = time.time() + secondes
    while time.time() < fin:
        try:
            v = p.evaluate(expression)
        except Exception:   # noqa: BLE001 — page en cours de chargement
            v = None
        if v:
            return v
        time.sleep(0.25)
    return None


def p108f_onglet(banc, s):
    FX = _fixtures()
    s.connecter()
    p = s.page
    d = _dossier(banc)
    s.aller('/dossier?' + urlencode({'id': 'DOS-001', 'vue': 'fichiers', 'sous': 'repertoire'}))
    verifier(p.locator('[data-f108-barre] .m97-nouveau-doc').count() == 1, 'P108f : « Nouveau document » absent de l’onglet Fichiers')
    verifier('dossier=DOS-001' in (p.get_attribute('[data-f108-barre] .m97-nouveau-doc', 'href') or ''), 'P108f : « Nouveau document » sans le dossier prérempli')
    verifier(p.locator('label[for=f108-fichiers]').inner_text().strip() == 'Ajouter un fichier', 'P108f : bouton « Ajouter un fichier » absent')
    s.capture(_sortie(banc) / 'P108f-onglet-fichiers.png')
    with p.expect_navigation(timeout=20000):                         # la vue « Par répertoire » se recharge après l'ajout
        with p.expect_response(lambda r: '/m56108/fichiers-dossier/ajouter' in r.url, timeout=20000) as rep:
            p.set_input_files('#f108-fichiers', files=[{'name': 'Formulaire fictif.pdf', 'mimeType': 'application/pdf', 'buffer': FX.pdf_formulaire()}])
        verifier(rep.value.status == 200, 'P108f : ajout refusé (HTTP %d)' % rep.value.status)
        verifier(_attendre(p, "document.querySelector('[data-f108-statut]').textContent.indexOf('1 fichier(s) ajouté(s) sur 1') >= 0", 5),
                 'P108f : compte rendu de l’ajout absent')
        s.capture(_sortie(banc) / 'P108f-ajout.png')
    p.wait_for_load_state('networkidle')
    verifier((d / 'Formulaire fictif.pdf').is_file(), 'P108f : fichier ajouté absent du dossier')
    with p.expect_navigation(timeout=20000):
        with p.expect_response(lambda r: '/m56108/fichiers-dossier/ajouter' in r.url, timeout=20000) as rep:
            p.evaluate("""() => {
                const dt = new DataTransfer();
                dt.items.add(new File(['Note fictive pour DURAND'], 'Note deposee.txt', {type: 'text/plain'}));
                const cible = document.querySelector('[data-f108-depot]');
                cible.dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true, cancelable: true}));
                cible.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
            }""")
        verifier(rep.value.status == 200, 'P108f : glisser-déposer refusé (HTTP %d)' % rep.value.status)
        verifier(_attendre(p, "!!document.querySelector('.f108-envois .f108-ok')", 5), 'P108f : glisser-déposer sans compte rendu')
        s.capture(_sortie(banc) / 'P108f-depot.png')
    p.wait_for_load_state('networkidle')
    verifier((d / 'Note deposee.txt').read_text(encoding='utf-8') == 'Note fictive pour DURAND', 'P108f : fichier déposé absent ou différent')
    s.capture(_sortie(banc) / 'P108f-liste-apres-ajout.png')
    verifier(p.locator('.d98-arbre >> text=Formulaire fictif.pdf').count() >= 1, 'P108f : fichier ajouté absent de la liste « Par répertoire »')
    s.propre('onglet Fichiers')


def p108f_remplir(banc, s):
    FX = _fixtures()
    brut = FX.pdf_formulaire()
    chemin, d = _semer(banc, 'Formulaire a remplir.pdf', brut)
    s.connecter()
    p = s.page
    s.aller('/dossier/remplir-pdf?' + urlencode({'d': 'DOS-001', 'p': chemin}))
    verifier(p.locator('[data-f108-champ]').count() >= 10, 'P108f : champs du PDF non détectés')
    verifier(_attendre(p, "(() => { const c = document.querySelector('.f108-apercu canvas'); return c && c.width > 100; })()", 20), 'P108f : aperçu PDF.js absent')
    verifier('Nom du client' in p.inner_text('.f108-form'), 'P108f : libellé retrouvé absent')
    p.click('[data-f108-tout]')
    verifier(p.input_value('[data-f108-champ="a1"]') == 'DURAND', 'P108f : proposition « client » non reprise')
    p.check('[data-f108-champ="c1"]')
    p.check('[data-f108-champ="r1"] input[value="Defendeur"]')
    p.select_option('[data-f108-champ="j1"]', 'Valbrune')
    p.fill('[data-f108-champ="a5"]', 'Observation fictive\nsur deux lignes')
    time.sleep(0.8)
    s.capture(_sortie(banc) / 'P108f-remplir.png')
    avant = sorted(x.name for x in d.iterdir())
    with p.expect_response(lambda r: '/m56108/fichiers-dossier/remplir' in r.url, timeout=30000) as rep:
        p.click('.f108-form button[type=submit]')
    verifier(rep.value.status == 200, 'P108f : remplissage refusé (HTTP %d : %s)' % (rep.value.status, rep.value.text()[:300]))
    verifier(_attendre(p, "document.querySelector('.f108-form [data-f108-statut]').textContent.indexOf('Copie remplie enregistrée') >= 0", 10), 'P108f : message de réussite absent')
    nouveaux = [x for x in d.iterdir() if x.name not in avant]
    verifier(len(nouveaux) == 1 and '(rempli le ' in nouveaux[0].name, 'P108f : copie remplie absente : %s' % [x.name for x in nouveaux])
    verifier((d / 'Formulaire a remplir.pdf').read_bytes() == brut, 'P108f : l’original a changé')
    lu = FX.champs_lus(nouveaux[0].read_bytes())
    verifier(lu.get('a1') == 'DURAND' and str(lu.get('r1')) == '/Defendeur' and lu.get('j1') == 'Valbrune', 'P108f : valeurs absentes de la copie : %s' % lu)
    MESURES['copie_remplie'] = nouveaux[0].name
    s.capture(_sortie(banc) / 'P108f-remplir-enregistre.png')
    s.propre('remplir')
    p.set_viewport_size({'width': 390, 'height': 844})
    s.aller('/dossier/remplir-pdf?' + urlencode({'d': 'DOS-001', 'p': chemin}))
    largeur = p.evaluate('document.documentElement.scrollWidth')
    verifier(largeur <= 400, 'P108f : défilement horizontal à 390 px (%d px)' % largeur)
    s.capture(_sortie(banc) / 'P108f-remplir-390.png')
    p.set_viewport_size({'width': 1440, 'height': 950})


def _glisser(p, x0, y0, x1, y1):
    boite = p.locator('[data-f108-calque]').bounding_box()
    p.mouse.move(boite['x'] + boite['width'] * x0, boite['y'] + boite['height'] * y0)
    p.mouse.down()
    p.mouse.move(boite['x'] + boite['width'] * x1, boite['y'] + boite['height'] * y1, steps=6)
    p.mouse.up()


def _operation(p, op, attente=60000):
    with p.expect_response(lambda r: '/m56108/fichiers-dossier/' + op in r.url, timeout=attente) as rep:
        p.click('[data-f108-op="%s"]' % op)
    verifier(rep.value.status == 200, 'P108f : %s refusé (HTTP %d : %s)' % (op, rep.value.status, rep.value.text()[:300]))
    return rep.value.json()


def p108f_outils(banc, s):
    FX = _fixtures()
    brut = FX.pdf_a_caviarder()
    chemin, d = _semer(banc, 'Courrier ZORVAL.pdf', brut)
    s.connecter()
    p = s.page
    s.aller('/dossier/outils-pdf?' + urlencode({'d': 'DOS-001', 'p': chemin}))
    verifier(_attendre(p, "(() => { const c = document.querySelector('[data-f108-canvas]'); return c && c.width > 100; })()", 20), 'P108f : la page n’est pas dessinée par PDF.js')
    p.check('input[name="f108-outil"][value="surligner"]')
    _glisser(p, 0.1, 0.12, 0.6, 0.16)
    p.check('input[name="f108-outil"][value="texte"]')
    p.fill('[data-f108-note]', 'À vérifier avec le client')
    _glisser(p, 0.55, 0.25, 0.9, 0.33)
    verifier(p.locator('[data-f108-traces] li').count() == 2, 'P108f : les deux annotations ne sont pas listées')
    s.capture(_sortie(banc) / 'P108f-annoter.png')
    out = _operation(p, 'annoter')
    verifier('(annoté le ' in out['nom'], 'P108f : copie annotée mal nommée : %s' % out.get('nom'))
    p.click('#f108-onglet-pages')
    p.uncheck('[data-f108-page="2"] [data-f108-garder]')
    out = _operation(p, 'organiser')
    verifier('1 page(s) supprimée(s)' in out['message'], 'P108f : page non retirée : %s' % out.get('message'))
    p.click('#f108-onglet-numeroter')
    p.fill('[data-f108-num-piece]', '12')
    out = _operation(p, 'numeroter')
    verifier('pièce n° 12' in out['nom'], 'P108f : copie numérotée mal nommée : %s' % out.get('nom'))
    s.capture(_sortie(banc) / 'P108f-numeroter.png')
    p.click('#f108-onglet-caviarder')
    from agent import outils_pdf56108 as O
    if all(O.outils_locaux().values()):
        p.fill('[data-f108-termes]', 'ZORVAL')
        p.check('[data-f108-zone-active]')
        p.click('[data-f108-suiv]')
        time.sleep(0.8)
        _glisser(p, 0.1, 0.1, 0.4, 0.15)
        s.capture(_sortie(banc) / 'P108f-caviarder-zone.png')
        debut = time.time()
        out = _operation(p, 'caviarder', 240000)
        MESURES['caviardage_s'] = round(time.time() - debut, 1)
        verifier('Contrôle réussi' in out['message'], 'P108f : caviardage sans contrôle réussi : %s' % out.get('message'))
        copie = (d / out['nom']).read_bytes()
        verifier(b'ZORVAL' not in copie and not any('zorval' in t.lower() for t in FX.textes(copie)), 'P108f : ZORVAL subsiste dans la copie caviardée')
        MESURES['caviardage'] = out['nom']
        s.capture(_sortie(banc) / 'P108f-caviarder.png')
    else:
        verifier(p.locator('[data-f108-op="caviarder"][disabled]').count() == 1, 'P108f : caviardage proposé sans les outils')
        MESURES['caviardage'] = 'outils absents : bouton désactivé, consigne affichée'
        s.capture(_sortie(banc) / 'P108f-caviarder-outils-absents.png')
    verifier((d / 'Courrier ZORVAL.pdf').read_bytes() == brut, 'P108f : l’original a changé')
    MESURES['fichiers'] = sorted(x.name for x in d.iterdir())
    s.propre('outils PDF')


PARCOURS_F = (('P108f-onglet', 'Onglet Fichiers du dossier : Nouveau document, Ajouter un fichier, glisser-déposer', p108f_onglet),
              ('P108f-remplir', 'Remplir un PDF à champs : propositions, copie remplie, original intact, 390 px', p108f_remplir),
              ('P108f-outils', 'Boîte à outils PDF : annoter, retirer une page, numéroter, caviarder', p108f_outils))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108f'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_F))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + PARCOURS_F
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P108f-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
