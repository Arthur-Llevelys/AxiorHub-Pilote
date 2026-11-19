"""5.6.108 (lot W) : parcours P108w du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, données fictives, aucun réseau, aucun modèle).

- P108w-wordcraft : un .docx fictif du dossier DOS-001 (tableau, numérotation, note, en-tête) ouvert depuis la page « Modifier dans l'éditeur
  intégré » ; WordCraft démarre dans son cadre (même origine, CSP inchangée), reçoit le document d'AxiorHub ; « [parcours] » est tapé puis le
  document enregistré dans l'éditeur ; le contrôle s'affiche (un paragraphe modifié, aucune perte) AVANT toute écriture ; « Garder cette
  version » crée « … (modifié dans WordCraft …).docx » à côté de l'original, qui reste octet pour octet identique. Aucune requête externe,
  aucune erreur JavaScript ni CSP (contrôles du banc).
- P108w-garde : un .docx fictif avec un champ de date : avertissement de la garde, « Ouvrir dans WordCraft » désactivé tant que la case
  « J'ai compris » n'est pas cochée ; aucune écriture tant que l'on ne fait rien.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p108w.py``.
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


def _semer(banc, nom, *options):
    sys.path.insert(0, str(ROOT / 'tests'))
    import fixtures_editeur56108 as FX
    dossier = next(p for p in (banc.cloud / 'Dossiers').iterdir() if p.name.startswith('DOS-001'))
    brut = FX.docx_fictif(*options)
    (dossier / nom).write_bytes(brut)
    return '/Dossiers/%s/%s' % (dossier.name, nom), dossier, brut


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
        getattr(cible, 'wait_for_timeout', lambda ms: time.sleep(ms / 1000))(200)
    return None


def p108w_wordcraft(banc, s):
    chemin, dossier, brut = _semer(banc, 'Conclusions fictives DURAND.docx', 'tableau', 'numerotation', 'notes', 'entetes', 'mise_en_forme')
    s.connecter()
    p = s.page
    s.aller('/editeur/wordcraft?' + urlencode({'s': 'nextcloud', 'p': chemin}))
    verifier(p.locator('#w108-titre').count() == 1, 'P108w : page de l’éditeur intégré absente')
    verifier('version d’essai' in p.inner_text('.w108-doc'), 'P108w : « version d’essai » non affichée')
    verifier(p.locator('.w108-constats li').count() >= 3, 'P108w : la garde ne liste pas les éléments repérés')
    verifier(p.locator('[data-w108="ouvrir"]').is_enabled(), 'P108w : « Ouvrir dans WordCraft » désactivé pour un document sans risque')
    s.capture(_sortie(banc) / 'P108w-garde-ok.png')
    debut = time.time()
    journal = MESURES.setdefault('reseau_cadre', [])
    p.on('request', lambda r: journal.append('%.2f demande %s' % (time.time() - debut, r.url.split('/agent-courriel')[-1][:90])) if '/static/' not in r.url else None)
    p.on('requestfinished', lambda r: journal.append('%.2f fini %s' % (time.time() - debut, r.url.split('/agent-courriel')[-1][:90])) if '/static/' not in r.url else None)
    p.on('requestfailed', lambda r: journal.append('%.2f échec %s %s' % (time.time() - debut, r.url.split('/agent-courriel')[-1][:90], r.failure)) if 'wordcraft' in r.url else None)
    p.click('[data-w108="ouvrir"]')
    p.wait_for_selector('[data-w108-zone]:not([hidden])', timeout=15000)
    cadre = None
    fin = time.time() + 60
    while cadre is None and time.time() < fin:
        cadre = next((f for f in p.frames if '/editeur/wordcraft/cadre' in f.url), None)
        p.wait_for_timeout(200)  # jamais time.sleep : le banc filtre chaque requête en Python (context.route), traité seulement pendant un appel
    MESURES['attente_cadre_s'] = round(time.time() - debut, 2)
    verifier(cadre is not None, 'P108w : cadre de l’éditeur introuvable (%s ; src=%s ; réseau=%s)' % ([f.url for f in p.frames], p.evaluate(
        "document.querySelector('[data-w108-iframe]').getAttribute('src')"), journal))
    pret = _attendre(cadre, "typeof window.wordcraft === 'object' && !!window.wordcraft.docInspect", 90)
    MESURES['demarrage_s'] = round(time.time() - debut, 2)
    verifier(pret, 'P108w : WordCraft n’a pas démarré (WebGL 2 ou WebGPU absent ?)')
    texte = _attendre(cadre, "wordcraft.docInspect().then(function (d) { var t = JSON.stringify(d); return t.indexOf('Conclusions pour M. DURAND') >= 0 ? t.length : 0; })", 60)
    MESURES['ouverture_document_s'] = round(time.time() - debut, 2)
    verifier(texte, 'P108w : le document d’AxiorHub n’a pas été ouvert dans WordCraft')
    verifier(cadre.evaluate("window.open('https://example.test/') === null"), 'P108w : window.open n’est pas neutralisé dans le cadre')
    s.capture(_sortie(banc) / 'P108w-wordcraft-ouvert.png')
    avant = sorted(x.name for x in dossier.iterdir())
    cadre.evaluate("wordcraft.execute('text.insert', {text: '[parcours] '})")
    p.wait_for_timeout(300)
    with p.expect_response(lambda r: '/m56108/editeur/enregistrer' in r.url and r.request.method == 'POST', timeout=30000) as rep:
        cadre.evaluate("wordcraft.execute('file.save', {})")
    verifier(rep.value.status == 200, 'P108w : enregistrement refusé (HTTP %d)' % rep.value.status)
    p.wait_for_selector('[data-w108-controle]:not([hidden])', timeout=15000)
    rapport = p.inner_text('[data-w108-rapport]')
    MESURES['rapport'] = rapport[:400]
    verifier('1 paragraphe(s) modifié(s), 0 ajouté(s), 0 supprimé(s)' in rapport, 'P108w : contrôle inattendu : %s' % rapport[:300])
    verifier('Pertes ou changements de structure' not in rapport, 'P108w : perte signalée sur un aller-retour simple : %s' % rapport[:300])
    verifier(sorted(x.name for x in dossier.iterdir()) == avant, 'P108w : un fichier a été écrit avant « Garder »')
    s.capture(_sortie(banc) / 'P108w-controle.png')
    with p.expect_response(lambda r: '/m56108/editeur/decider' in r.url, timeout=30000) as rep:
        p.click('[data-w108="garder"]')
    verifier(rep.value.status == 200, 'P108w : « Garder » refusé (HTTP %d)' % rep.value.status)
    nouveaux = [x for x in dossier.iterdir() if x.name not in avant]
    verifier(len(nouveaux) == 1 and '(modifié dans WordCraft ' in nouveaux[0].name, 'P108w : nouvelle version absente : %s' % [x.name for x in nouveaux])
    verifier((dossier / 'Conclusions fictives DURAND.docx').read_bytes() == brut, 'P108w : l’original a changé')
    import zipfile
    import io
    with zipfile.ZipFile(io.BytesIO(nouveaux[0].read_bytes())) as z:
        verifier(b'[parcours] ' in z.read('word/document.xml'), 'P108w : la modification n’est pas dans la nouvelle version')
    etat = _attendre(p, "document.querySelector('[data-w108-etat]').textContent.indexOf('Nouvelle version enregistrée') >= 0", 10)
    verifier(etat, 'P108w : message de réussite absent')
    MESURES['nouvelle_version'] = nouveaux[0].name
    MESURES['sha_original_intact'] = hashlib.sha256(brut).hexdigest()[:12]
    s.propre('wordcraft')
    s.capture(_sortie(banc) / 'P108w-garde-version.png')


def p108w_garde(banc, s):
    chemin, dossier, brut = _semer(banc, 'Courrier date fictive.docx', 'champs')
    s.connecter()
    p = s.page
    s.aller('/editeur/wordcraft?' + urlencode({'s': 'nextcloud', 'p': chemin}))
    verifier(p.locator('.w108-avertissement').count() == 1, 'P108w : avertissement de la garde absent (champ de date)')
    verifier('November' in p.inner_text('.w108-constats'), 'P108w : la garde ne dit pas ce que WordCraft abîme')
    bouton = p.locator('[data-w108="ouvrir"]')
    verifier(bouton.is_disabled(), 'P108w : ouverture possible sans confirmation d’un document à risque')
    p.check('[data-w108-accepte]')
    verifier(bouton.is_enabled(), 'P108w : la case « J’ai compris » ne permet pas d’ouvrir')
    verifier((dossier / 'Courrier date fictive.docx').read_bytes() == brut and len(list(dossier.iterdir())) == 1, 'P108w : écriture sans action')
    s.propre('garde')
    s.capture(_sortie(banc) / 'P108w-garde-risque.png')


PARCOURS_W = (('P108w-wordcraft', 'Éditeur intégré WordCraft : ouverture, enregistrement, contrôle, nouvelle version à côté de l’original', p108w_wordcraft),
              ('P108w-garde', 'Éditeur intégré : garde avant ouverture d’un document à risque (champ de date)', p108w_garde))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p108w'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_W))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    NAV.PARCOURS = NAV.PARCOURS + PARCOURS_W
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x))
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P108w-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
