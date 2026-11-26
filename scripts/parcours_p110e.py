"""5.6.110 (lot E) : parcours P110e du banc ``scripts/parcours-navigateur.py`` — GridCraft, PdfCraft et DeckCraft embarqués, dans un vrai
navigateur (Edge sous Windows), avec les VRAIS fichiers embarqués sous ``agent/static/<app>-<version>/`` ; données fictives, aucun réseau.

Pour chaque éditeur (P110e-gridcraft, P110e-pdfcraft, P110e-deckcraft) :
- un document fictif (classeur .xlsx, PDF, présentation .pptx fabriqués par ``tests/fixtures_craft56110.py``) est déposé dans le dossier
  DOS-001 ; l'aperçu du dossier propose « Modifier dans <éditeur> (version d'essai) » (adresse calculée par le serveur) ;
- la page « Modifier dans un éditeur intégré » ouvre l'éditeur dans son cadre (même origine, ``?host=parent``) ; l'éditeur démarre, reçoit le
  document d'AxiorHub et l'affiche (vérifié par l'API de l'application : nom du document, contenu) ;
- le document est modifié dans l'éditeur (API de l'application : cellule, rotation de page, texte d'une diapositive), puis enregistré
  (« Enregistrer » de l'application : Ctrl+S au clavier pour DeckCraft, commande ``file.save`` pour les deux autres) ; AxiorHub reçoit le
  fichier, le contrôle (signature du format, analyse) et montre la comparaison AVANT toute écriture ;
- « Conserver cette version » crée « … (modifié dans <éditeur> …) » À CÔTÉ de l'original, resté octet pour octet identique ; le fichier
  produit est relu (signature du format, contenu modifié présent) ;
- GridCraft : « Exporter en PDF » crée un PDF distinct à côté du classeur (« … (export GridCraft …).pdf »), jamais à la place du classeur ;
- aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc (contrôles du banc), ``window.open`` neutralisé.
L'API ``window.pdfcraft`` n'existe qu'avec ``?control`` (HOSTING.md de PdfCraft) : le parcours l'ajoute à l'adresse du cadre DANS LA PAGE
d'essai (le produit ne l'active pas). Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p110e.py [--navigateur msedge] [--parcours P110e-gridcraft,P110e-pdfcraft,P110e-deckcraft] [--sortie DOSSIER]``.
"""
import io
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
import zipfile

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


def _attendre(cible, expression, secondes=60.0):
    """Attente par ``evaluate`` (la CSP d'AxiorHub refuse l'évaluation de chaînes qu'utilise ``wait_for_function``)."""
    fin = time.time() + secondes
    while time.time() < fin:
        try:
            v = cible.evaluate(expression)
        except Exception:   # noqa: BLE001 — cadre en cours de chargement
            v = None
        if v:
            return v
        getattr(cible, 'wait_for_timeout', lambda ms: time.sleep(ms / 1000))(250)
    return None


def _semer(banc, nom, brut):
    dossier = next(p for p in (banc.cloud / 'Dossiers').iterdir() if p.name.startswith('DOS-001'))
    (dossier / nom).write_bytes(brut)
    return '/Dossiers/%s/%s' % (dossier.name, nom), dossier


def _fixtures():
    sys.path.insert(0, str(ROOT / 'tests'))
    import fixtures_craft56110 as FX
    return FX


def _ouvrir(banc, s, app, chemin, controle=False):
    """Page d'édition, cadre démarré, document reçu ; rend (page, cadre)."""
    p = s.page
    if controle:
        # API de PdfCraft (?control) : ajoutée à l'adresse du cadre dans la page d'essai seulement (script d'essai posé avant le chargement,
        # qui retouche la configuration dès son insertion, avant les scripts différés de la page) ; le produit ne l'active jamais.
        p.add_init_script("""new MutationObserver(function (lot, obs) {
          var n = document.querySelector('[data-c109-config]'); if (!n) { return; }
          var c = JSON.parse(n.getAttribute('data-c109-config')); if (c.cadre.indexOf('&control') < 0) { c.cadre += '&control'; }
          n.setAttribute('data-c109-config', JSON.stringify(c)); obs.disconnect();
        }).observe(document, { childList: true, subtree: true });""")
    s.aller('/editeur/craft?' + urlencode({'app': app, 's': 'nextcloud', 'p': chemin}))
    verifier(p.locator('#c109-titre').count() == 1, 'P110e %s : page de l’éditeur absente' % app)
    verifier('version d’essai' in p.inner_text('#c109-titre'), 'P110e %s : « version d’essai » non affichée' % app)
    debut = time.time()
    with p.expect_response(lambda r: '/m56109/editeurs/seances' in r.url and r.request.method == 'POST', timeout=20000) as rep:
        p.click('[data-c109="ouvrir"]')
    verifier(rep.value.status == 200, 'P110e %s : ouverture de la séance refusée (HTTP %d)' % (app, rep.value.status))
    p.wait_for_selector('[data-c109-zone]:not([hidden])', timeout=15000)
    cadre, fin = None, time.time() + 60
    while cadre is None and time.time() < fin:
        cadre = next((f for f in p.frames if '/editeur/craft/%s-' % app in f.url and '/cadre' in f.url), None)
        p.wait_for_timeout(200)
    verifier(cadre is not None, 'P110e %s : cadre de l’éditeur introuvable (%s)' % (app, [f.url for f in p.frames]))
    pret = _attendre(cadre, "typeof window.%s === 'object' && typeof window.%s.docInspect === 'function'" % (app, app), 120)
    MESURES.setdefault(app, {})['demarrage_s'] = round(time.time() - debut, 1)
    verifier(pret, 'P110e %s : l’éditeur n’a pas démarré (WebGL 2 ou WebGPU absent ?) — état : %s' % (app, p.inner_text('[data-c109-etat]')))
    return p, cadre


def p110e_gridcraft(banc, s):
    FX = _fixtures()
    brut = FX.xlsx_fictif()
    chemin, dossier = _semer(banc, 'Tableau des créances DURAND.xlsx', brut)
    s.connecter()
    p = s.page
    apercu = p.evaluate("u => fetch(u, {credentials: 'same-origin'}).then(r => r.json())",
                        banc.url + '/dossier/apercu?' + urlencode({'matter': 'DOS-001', 'path': chemin}))
    verifier(apercu.get('craft_libelle') == 'Modifier dans GridCraft (version d’essai)' and '/editeur/craft?' in apercu.get('craft_url', ''),
             'P110e gridcraft : l’aperçu ne propose pas GridCraft : %s' % {k: apercu.get(k) for k in ('craft_url', 'craft_libelle', 'error')})
    p, cadre = _ouvrir(banc, s, 'gridcraft', chemin)
    vu = _attendre(cadre, "gridcraft.docInspect().then(d => d.title === 'Tableau des créances DURAND.xlsx' && d.sheets[0].usedRange)", 60)
    verifier(vu == 'A1:B4', 'P110e gridcraft : classeur d’AxiorHub non ouvert (%s)' % vu)
    verifier(cadre.evaluate("gridcraft.execute('cell.get', {cell: 'B2'}).then(c => String(c.value))") == '1200', 'P110e gridcraft : cellule B2 inattendue')
    verifier(cadre.evaluate("window.open('https://example.test/') === null"), 'P110e gridcraft : window.open n’est pas neutralisé dans le cadre')
    _attendre(p, "document.querySelector('[data-c109-etat]').textContent.indexOf('Document ouvert') >= 0", 10)
    s.capture(_sortie(banc) / 'P110e-gridcraft-ouvert.png')
    avant = sorted(x.name for x in dossier.iterdir())
    cadre.evaluate("gridcraft.execute('cell.set', {cell: 'B2', input: '1500'})")
    p.wait_for_timeout(300)
    with p.expect_response(lambda r: '/candidats' in r.url and r.request.method == 'POST', timeout=30000) as rep:
        cadre.evaluate("gridcraft.execute('file.save', {})")
    verifier(rep.value.status == 200, 'P110e gridcraft : enregistrement refusé (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-c109-controle]:not([hidden])', timeout=15000)
    rapport = p.inner_text('[data-c109-rapport]')
    MESURES['gridcraft']['rapport'] = rapport[:300]
    verifier('1 feuille(s) modifiée(s)' in rapport and 'Formules' not in rapport, 'P110e gridcraft : contrôle inattendu : %s' % rapport[:300])
    verifier(sorted(x.name for x in dossier.iterdir()) == avant, 'P110e gridcraft : un fichier a été écrit avant « Conserver »')
    s.capture(_sortie(banc) / 'P110e-gridcraft-controle.png')
    # export PDF : fichier distinct à côté du classeur
    with p.expect_response(lambda r: '/exports' in r.url and r.request.method == 'POST', timeout=30000) as rep:
        cadre.evaluate("gridcraft.execute('file.exportPdf', {})")
    verifier(rep.value.status == 200 and rep.value.json().get('etat') == 'verifie', 'P110e gridcraft : export PDF refusé (%s)' % rep.value.text()[:200])
    exports = [x for x in dossier.iterdir() if '(export GridCraft ' in x.name]
    verifier(len(exports) == 1 and exports[0].suffix == '.pdf' and exports[0].read_bytes().startswith(b'%PDF-'),
             'P110e gridcraft : export PDF absent ou invalide : %s' % [x.name for x in exports])
    verifier((dossier / 'Tableau des créances DURAND.xlsx').read_bytes() == brut, 'P110e gridcraft : l’export a touché le classeur')
    with p.expect_response(lambda r: '/decision' in r.url, timeout=30000) as rep:
        p.click('[data-c109="garder"]')
    verifier(rep.value.status == 200, 'P110e gridcraft : « Conserver » refusé (HTTP %d)' % rep.value.status)
    nouveaux = [x for x in dossier.iterdir() if x.name not in avant and '(modifié dans GridCraft ' in x.name]
    verifier(len(nouveaux) == 1 and nouveaux[0].suffix == '.xlsx', 'P110e gridcraft : nouvelle version absente : %s' % [x.name for x in dossier.iterdir()])
    verifier((dossier / 'Tableau des créances DURAND.xlsx').read_bytes() == brut, 'P110e gridcraft : l’original a changé')
    data = nouveaux[0].read_bytes()
    verifier(data.startswith(b'PK\x03\x04'), 'P110e gridcraft : la nouvelle version n’est pas un classeur')
    sys.path.insert(0, str(ROOT))
    from agent import craft56109 as C
    analyse = C.analyser_xlsx(data)
    verifier('1500' in analyse['textes'][0] and analyse['formules'] == 1, 'P110e gridcraft : modification absente de la version gardée : %s' % analyse)
    MESURES['gridcraft'].update({'version': nouveaux[0].name, 'export': exports[0].name, 'textes': analyse['textes'][0][:120]})
    s.propre('gridcraft')
    s.capture(_sortie(banc) / 'P110e-gridcraft-garde.png')


def p110e_pdfcraft(banc, s):
    FX = _fixtures()
    brut = FX.pdf_fictif()
    chemin, dossier = _semer(banc, 'Bordereau fictif DURAND.pdf', brut)
    s.connecter()
    p = s.page
    apercu = p.evaluate("u => fetch(u, {credentials: 'same-origin'}).then(r => r.json())",
                        banc.url + '/dossier/apercu?' + urlencode({'matter': 'DOS-001', 'path': chemin}))
    verifier(apercu.get('craft_libelle') == 'Modifier dans PdfCraft (version d’essai)', 'P110e pdfcraft : l’aperçu ne propose pas PdfCraft : %s' % apercu.get('craft_libelle'))
    p, cadre = _ouvrir(banc, s, 'pdfcraft', chemin, controle=True)
    vu = _attendre(cadre, "pdfcraft.docInspect().then(d => (d.documents || []).map(x => x.name + ':' + x.pages).join(','))", 60)
    verifier(vu == 'Bordereau fictif DURAND.pdf:1', 'P110e pdfcraft : PDF d’AxiorHub non ouvert (%s)' % vu)
    verifier(cadre.evaluate("window.open('https://example.test/') === null"), 'P110e pdfcraft : window.open n’est pas neutralisé')
    s.capture(_sortie(banc) / 'P110e-pdfcraft-ouvert.png')
    avant = sorted(x.name for x in dossier.iterdir())
    cadre.evaluate("pdfcraft.execute('page.rotate', {})")
    p.wait_for_timeout(300)
    with p.expect_response(lambda r: '/candidats' in r.url and r.request.method == 'POST', timeout=30000) as rep:
        cadre.evaluate("pdfcraft.execute('file.save', {})")
    verifier(rep.value.status == 200, 'P110e pdfcraft : enregistrement refusé (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-c109-controle]:not([hidden])', timeout=15000)
    rapport = p.inner_text('[data-c109-rapport]')
    MESURES['pdfcraft']['rapport'] = rapport[:300]
    verifier('Aucune perte' in rapport, 'P110e pdfcraft : contrôle inattendu : %s' % rapport[:300])
    verifier(sorted(x.name for x in dossier.iterdir()) == avant, 'P110e pdfcraft : un fichier a été écrit avant « Conserver »')
    s.capture(_sortie(banc) / 'P110e-pdfcraft-controle.png')
    with p.expect_response(lambda r: '/decision' in r.url, timeout=30000) as rep:
        p.click('[data-c109="garder"]')
    verifier(rep.value.status == 200, 'P110e pdfcraft : « Conserver » refusé (HTTP %d)' % rep.value.status)
    nouveaux = [x for x in dossier.iterdir() if x.name not in avant and '(modifié dans PdfCraft ' in x.name]
    verifier(len(nouveaux) == 1 and nouveaux[0].suffix == '.pdf', 'P110e pdfcraft : nouvelle version absente : %s' % [x.name for x in dossier.iterdir()])
    verifier((dossier / 'Bordereau fictif DURAND.pdf').read_bytes() == brut, 'P110e pdfcraft : l’original a changé')
    data = nouveaux[0].read_bytes()
    verifier(data.startswith(b'%PDF-'), 'P110e pdfcraft : la nouvelle version n’est pas un PDF')
    sys.path.insert(0, str(ROOT))
    from agent._vendor.pypdf import PdfReader
    page = PdfReader(io.BytesIO(data)).pages[0]
    verifier(int(page.get('/Rotate') or 0) == 90 and 'DURAND' in (page.extract_text() or ''), 'P110e pdfcraft : rotation ou texte absent de la version gardée')
    MESURES['pdfcraft'].update({'version': nouveaux[0].name, 'rotation': int(page.get('/Rotate') or 0)})
    # Reconnaissance de texte : la fenêtre « Recognize text » charge les deux modèles, servis par AxiorHub (aucune autre adresse)
    modeles = []
    p.on('response', lambda r: modeles.append((r.url.rsplit('/', 2)[-2:], r.status)) if r.url.endswith('.onnx') else None)
    cadre.evaluate("pdfcraft.execute('ocr.recognize', {})")
    _attendre(p, "true", 1)
    fin = time.time() + 45
    while len(modeles) < 2 and time.time() < fin:
        p.wait_for_timeout(300)
    MESURES['pdfcraft']['modeles_ocr'] = modeles
    verifier(sorted(m for m, _ in modeles) == [['models', 'text-detection.onnx'], ['models', 'text-recognition.onnx']] and all(st == 200 for _, st in modeles),
             'P110e pdfcraft : modèles de reconnaissance de texte non chargés depuis AxiorHub : %s' % modeles)
    s.capture(_sortie(banc) / 'P110e-pdfcraft-ocr.png')
    s.propre('pdfcraft')
    s.capture(_sortie(banc) / 'P110e-pdfcraft-garde.png')


def p110e_deckcraft(banc, s):
    FX = _fixtures()
    brut = FX.pptx_fictif()
    chemin, dossier = _semer(banc, 'Restitution MARTIN.pptx', brut)
    s.connecter()
    p = s.page
    apercu = p.evaluate("u => fetch(u, {credentials: 'same-origin'}).then(r => r.json())",
                        banc.url + '/dossier/apercu?' + urlencode({'matter': 'DOS-001', 'path': chemin}))
    verifier(apercu.get('craft_libelle') == 'Modifier dans DeckCraft (version d’essai)', 'P110e deckcraft : l’aperçu ne propose pas DeckCraft : %s' % apercu.get('craft_libelle'))
    p, cadre = _ouvrir(banc, s, 'deckcraft', chemin)
    vu = _attendre(cadre, "deckcraft.docInspect().then(d => d.path + ':' + d.slides.length + ':' + d.slides[0].title)", 60)
    verifier(vu == 'Restitution MARTIN.pptx:1:Restitution fictive', 'P110e deckcraft : présentation d’AxiorHub non ouverte (%s)' % vu)
    verifier(cadre.evaluate("window.open('https://example.test/') === null"), 'P110e deckcraft : window.open n’est pas neutralisé')
    s.capture(_sortie(banc) / 'P110e-deckcraft-ouvert.png')
    avant = sorted(x.name for x in dossier.iterdir())
    verifier(cadre.evaluate("deckcraft.execute('text.replace', {text: 'MARTIN', with: 'MARTIN [parcours]'}).then(r => r.replaced)") == 1,
             'P110e deckcraft : texte non remplacé')
    p.wait_for_timeout(300)
    # Enregistrer comme l'avocat : clic dans l'éditeur puis Ctrl+S au clavier (aucun appel à l'API pour l'enregistrement)
    boite = p.locator('[data-c109-iframe]').bounding_box()
    p.mouse.click(boite['x'] + boite['width'] * 0.5, boite['y'] + boite['height'] * 0.6)
    p.wait_for_timeout(300)
    with p.expect_response(lambda r: '/candidats' in r.url and r.request.method == 'POST', timeout=30000) as rep:
        p.keyboard.press('Control+s')
    verifier(rep.value.status == 200, 'P110e deckcraft : enregistrement (Ctrl+S) refusé (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-c109-controle]:not([hidden])', timeout=15000)
    rapport = p.inner_text('[data-c109-rapport]')
    MESURES['deckcraft']['rapport'] = rapport[:300]
    verifier('1 diapositive(s) modifiée(s)' in rapport, 'P110e deckcraft : contrôle inattendu : %s' % rapport[:300])
    verifier(sorted(x.name for x in dossier.iterdir()) == avant, 'P110e deckcraft : un fichier a été écrit avant « Conserver »')
    s.capture(_sortie(banc) / 'P110e-deckcraft-controle.png')
    with p.expect_response(lambda r: '/decision' in r.url, timeout=30000) as rep:
        p.click('[data-c109="garder"]')
    verifier(rep.value.status == 200, 'P110e deckcraft : « Conserver » refusé (HTTP %d)' % rep.value.status)
    nouveaux = [x for x in dossier.iterdir() if x.name not in avant and '(modifié dans DeckCraft ' in x.name]
    verifier(len(nouveaux) == 1 and nouveaux[0].suffix == '.pptx', 'P110e deckcraft : nouvelle version absente : %s' % [x.name for x in dossier.iterdir()])
    verifier((dossier / 'Restitution MARTIN.pptx').read_bytes() == brut, 'P110e deckcraft : l’original a changé')
    data = nouveaux[0].read_bytes()
    verifier(data.startswith(b'PK\x03\x04'), 'P110e deckcraft : la nouvelle version n’est pas une présentation')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        verifier(b'MARTIN [parcours]' in z.read('ppt/slides/slide1.xml'), 'P110e deckcraft : la modification n’est pas dans la version gardée')
    MESURES['deckcraft'].update({'version': nouveaux[0].name})
    s.propre('deckcraft')
    s.capture(_sortie(banc) / 'P110e-deckcraft-garde.png')


PARCOURS_E = (('P110e-gridcraft', 'GridCraft embarqué : classeur ouvert, modifié, enregistré, contrôlé, gardé à côté ; export PDF distinct', p110e_gridcraft),
              ('P110e-pdfcraft', 'PdfCraft embarqué : PDF ouvert, page tournée, enregistré, contrôlé, gardé à côté de l’original', p110e_pdfcraft),
              ('P110e-deckcraft', 'DeckCraft embarqué : présentation ouverte, texte modifié, Ctrl+S, contrôlée, gardée à côté', p110e_deckcraft))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import os
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p110e'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_E))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS_E[0][0] for ident, _, _ in NAV.PARCOURS):
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS_E
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P110e-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
