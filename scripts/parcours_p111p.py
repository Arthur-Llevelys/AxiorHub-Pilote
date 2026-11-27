"""5.6.111 (lot P) : parcours P111p du banc ``scripts/parcours-navigateur.py`` — préparation supervisée d'un envoi sur un portail, dans un vrai
navigateur (Edge sous Windows) ; données fictives (DOS-001, DURAND, example.test), aucun réseau, aucun portail réel, aucun adaptateur.

P111p-portail :
- un profil de portail fictif est déclaré (adresse et domaine ``portail.example.test``, jamais contactés), un brouillon de conclusions est
  inscrit au registre des livrables et une pièce au registre des documents du dossier ;
- onglet Juridiction du dossier : bloc « Envois sur le portail » avec l'avertissement « AxiorHub ne pilote aucun portail » ; préparation
  (destinataire, objet, texte exact, deux pièces par leur rang) ; le code s'affiche une fois dans une zone d'état ; un code faux donne
  « il reste 4 essais » ; le bon code confirme ; l'envoi est « Confirmé : à transmettre par vous dans le portail » ;
- récapitulatif : texte exact identique, pièces dans l'ordre, téléchargement d'une pièce = octets du registre ; « non connues, à vérifier
  sur le portail » pour les limites ;
- « Enregistrer l'accusé de dépôt » (fichier PDF fictif) : déposé dans le dossier, octet pour octet, état « Accusé de dépôt enregistré » ;
- venue d'« À relire » (adresse avec ``portail_doc``) : la version du brouillon de conclusions reçoit le rang 1 ;
- carte « Relever mes messages procéduraux » : quatre capacités distinctes du profil ;
- 1920 × 1080, 1536 × 864 (125 %), 375 px en thème sombre : aucun défilement horizontal ; aucune erreur JavaScript ni CSP, aucune requête
  externe (contrôles du banc).
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p111p.py [--navigateur msedge] [--sortie DOSSIER]``.
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
MESSAGE = ('Monsieur le Président,\n\nVeuillez trouver ci-joint nos conclusions pour M. DURAND (RG 24/01234).\n\n'
           'Avec nos respectueuses salutations.')


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


def _attendre(p, expression, secondes=15.0):
    fin = time.time() + secondes
    v = None
    while time.time() < fin:
        try:
            v = p.evaluate(expression)
        except Exception:   # noqa: BLE001 — page en cours de chargement
            v = None
        if v:
            return v
        p.wait_for_timeout(200)
    return v


def _semer(banc):
    """Profil fictif, brouillon de conclusions (registre des livrables) et pièce (registre des documents du dossier)."""
    from agent import collecte_portail56109 as C, livrables5689 as liv, modele5651
    dossier = next(p for p in (banc.cloud / 'Dossiers').iterdir() if p.name.startswith('DOS-001'))
    base = '/Dossiers/%s' % dossier.name
    conclusions = b'PK\x03\x04 conclusions fictives DURAND contre MARTIN (parcours P111p)'
    piece = b'%PDF-1.4 piece 1 contrat fictif DURAND (parcours P111p)'
    (dossier / 'PROJETS').mkdir(exist_ok=True)
    (dossier / 'PIECES').mkdir(exist_ok=True)
    (dossier / 'PROJETS' / 'Conclusions DURAND.docx').write_bytes(conclusions)
    (dossier / 'PIECES' / 'Piece 1 - Contrat.pdf').write_bytes(piece)
    sha = hashlib.sha256(conclusions).hexdigest()
    with banc.desk() as d:
        C.ensure_schema(d)
        prof = C.enregistrer_profil(d, 'admin-banc', 'administrateur', {
            'portail': 'ebarreau', 'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Windows 11',
            'navigateur': 'Edge (banc)', 'compte': 'avocat-banc', 'type_compte': 'professionnel'})
        liv.ensure_schema(d)
        item = liv.observe(d, {'service': 'banc_p111p', 'object': 'PROJETS/Conclusions DURAND.docx', 'version': sha[:8], 'matter': 'DOS-001',
                               'kind': 'conclusions', 'title': 'Conclusions DURAND', 'path': base + '/PROJETS/Conclusions DURAND.docx', 'sha256': sha,
                               'size': len(conclusions), 'state': 'verified'})
        d.db.commit()
        row = liv._item(d, item)
        modele5651.ensure_schema(d)
        d.db.execute('INSERT OR REPLACE INTO d51_documents(matter,path,etag,name,updated) VALUES(?,?,?,?,?)',
                     ('DOS-001', base + '/PIECES/Piece 1 - Contrat.pdf', 'etag-banc', 'Piece 1 - Contrat.pdf', '2026-11-26T08:00:00+00:00'))
        d.db.commit()
    return {'profil': prof, 'doc': row['document_id'], 'version': row['version_id'], 'conclusions': conclusions, 'piece': piece, 'dossier': dossier}


def _sans_defilement(p, lieu):
    largeur = p.evaluate('[document.documentElement.scrollWidth, document.documentElement.clientWidth]')
    verifier(largeur[0] <= largeur[1] + 1, '%s : défilement horizontal (%s)' % (lieu, largeur))


def p111p_portail(banc, s):
    semis = _semer(banc)
    s.connecter()
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    page = '/dossier?' + urlencode({'id': 'DOS-001', 'vue': 'juridiction'})
    s.aller(page)
    verifier(p.locator('#p111-portail').count() == 1, 'P111p : bloc « Envois sur le portail » absent de l’onglet Juridiction')
    verifier('AxiorHub ne pilote aucun portail' in p.inner_text('#p111-portail'), 'P111p : avertissement « aucun portail piloté » absent')
    s.propre('P111p onglet Juridiction')
    # préparation
    p.click('#p111-preparer > summary')
    p.fill('#p111-preparer [name=destinataire]', 'Tribunal judiciaire de Valbrune — greffe')
    p.fill('#p111-preparer [name=objet]', 'Conclusions en défense DURAND')
    p.fill('#p111-preparer [name=message]', MESSAGE)
    rang_concl = p.locator('#p111-preparer [data-p111-doc="%s"]' % semis['doc'])
    verifier(rang_concl.count() == 1, 'P111p : brouillon de conclusions non proposé comme pièce')
    rang_concl.fill('1')
    autres = p.locator('#p111-preparer input[data-p111-doc=""]')
    verifier(autres.count() >= 1, 'P111p : pièce du registre du dossier non proposée')
    autres.first.fill('2')
    s.capture(_sortie(banc) / 'P111p-preparation.png')
    with p.expect_response(lambda r: '/m56111/portail/preparer' in r.url, timeout=20000) as rep:
        p.click('#p111-preparer button[type=submit]')
    corps = rep.value.json()
    verifier(rep.value.status == 200 and corps.get('code'), 'P111p : préparation refusée (%s)' % json.dumps(corps, ensure_ascii=False)[:300])
    p.wait_for_selector('[data-p111-code-zone]:not([hidden]) .p111-code', timeout=10000)
    code = p.inner_text('[data-p111-code-zone] .p111-code').strip()
    verifier(code == corps['code'] and len(code) == 6, 'P111p : code affiché différent du code émis')
    s.capture(_sortie(banc) / 'P111p-code.png')
    faux = '%06d' % ((int(code) + 1) % 1_000_000)
    p.fill('[data-p111-code-zone] input[name=code]', faux)
    with p.expect_response(lambda r: '/m56111/portail/confirmer' in r.url, timeout=20000) as rep:
        p.click('[data-p111-code-zone] button[type=submit]')
    verifier(rep.value.status == 422, 'P111p : un code faux n’est pas refusé (HTTP %d)' % rep.value.status)
    p.wait_for_timeout(300)
    s.erreurs[:] = [x for x in s.erreurs if 'status of 422' not in x]   # seul le refus VOULU du code faux est écarté du contrôle de la console
    etat = _attendre(p, "(document.querySelector('[data-p111-code-zone] .m5-status') || {}).textContent || ''")
    verifier('Il reste 4 essai(s)' in (etat or ''), 'P111p : message « il reste 4 essais » absent : %r' % etat)
    p.fill('[data-p111-code-zone] input[name=code]', code)
    with p.expect_navigation(timeout=20000):
        p.click('[data-p111-code-zone] button[type=submit]')
    p.wait_for_load_state('networkidle')
    envoi = p.locator('.p111-envoi').first
    verifier('Confirmé : à transmettre par vous dans le portail' in envoi.inner_text(), 'P111p : état « Confirmé » absent après le code')
    texte = envoi.locator('[data-p111-texte]').input_value()
    verifier(texte == MESSAGE, 'P111p : texte du récapitulatif différent du texte préparé')
    liens = envoi.locator('.p111-liste a')
    verifier(liens.count() == 2, 'P111p : deux pièces attendues dans le récapitulatif (%d)' % liens.count())
    verifier('Conclusions DURAND' in liens.nth(0).inner_text(), 'P111p : ordre des pièces inattendu')
    verifier('non connues, à vérifier sur le portail' in envoi.inner_text(), 'P111p : limites présentées comme connues')
    href = liens.nth(1).get_attribute('href')
    octets = p.evaluate("u => fetch(u, {credentials: 'same-origin'}).then(r => r.arrayBuffer()).then(b => Array.from(new Uint8Array(b)))", href)
    verifier(bytes(octets) == semis['piece'], 'P111p : la pièce téléchargée n’est pas celle du registre')
    s.capture(_sortie(banc) / 'P111p-confirme.png')
    s.propre('P111p confirmation')
    # accusé de dépôt
    accuse = b'%PDF-1.4 accuse de depot fictif (parcours P111p)'
    envoi.locator('input[name=accuse]').set_input_files(files=[{'name': 'accuse-depot.pdf', 'mimeType': 'application/pdf', 'buffer': accuse}])
    with p.expect_response(lambda r: '/m56111/portail/accuse' in r.url, timeout=20000) as rep:
        envoi.locator('.p111-accuse button[type=submit]').click()
    verifier(rep.value.status == 200, 'P111p : accusé refusé (%s)' % rep.value.text()[:300])
    p.wait_for_timeout(1200)
    p.wait_for_load_state('networkidle')
    verifier('Accusé de dépôt enregistré' in p.locator('.p111-envoi').first.inner_text(), 'P111p : état « Accusé de dépôt enregistré » absent')
    deposes = [x for x in semis['dossier'].rglob('*') if x.is_file() and 'Accusé de dépôt' in x.name]
    verifier(len(deposes) == 1 and deposes[0].read_bytes() == accuse, 'P111p : accusé non déposé à l’identique : %s' % [x.name for x in deposes])
    MESURES['accuse'] = str(deposes[0].relative_to(banc.cloud))
    s.capture(_sortie(banc) / 'P111p-accuse.png')
    s.propre('P111p accusé')
    # venue d'« À relire » : présélection de la version du brouillon de conclusions
    s.aller('/dossier?' + urlencode({'id': 'DOS-001', 'vue': 'juridiction', 'portail_doc': semis['doc'], 'portail_version': semis['version']}))
    rang = _attendre(p, "(document.querySelector('[data-p111-doc=\"%s\"]') || {}).value" % semis['doc'])
    verifier(rang == '1' and p.evaluate("document.getElementById('p111-preparer').open"), 'P111p : brouillon d’« À relire » non présélectionné')
    verifier(p.evaluate("document.querySelector('[data-p111-origine]').value") == 'a_relire', 'P111p : origine « À relire » non retenue')
    s.propre('P111p venue d’À relire')
    # écrans : 125 %, 375 px sombre
    p.set_viewport_size({'width': 1536, 'height': 864})
    s.aller(page)
    _sans_defilement(p, 'P111p 1536 × 864')
    p.emulate_media(color_scheme='dark')
    p.set_viewport_size({'width': 375, 'height': 812})
    s.aller(page)
    p.click('#p111-preparer > summary')
    _sans_defilement(p, 'P111p 375 px sombre')
    s.capture(_sortie(banc) / 'P111p-375-sombre.png')
    p.emulate_media(color_scheme='light')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.propre('P111p affichages')
    # capacités du profil (carte de la collecte)
    from agent import web5652
    with banc.desk() as d:
        carte = web5652.routines_block(d, {'owner': 'cabinet'}, '/agent-courriel', role='administrateur', compact=False)
    verifier('g4-capacites' in carte and 'Suivre la transmission faite par l’avocat et son accusé' in carte and 'Préparer un dépôt' in carte
             and 'Qualifier une capacité' in carte, 'P111p : capacités distinctes absentes de la carte de collecte')
    MESURES.update({'code_affiche': True, 'faux_code_refuse': True, 'pieces': 2, 'presélection_a_relire': True})


PARCOURS_P = (('P111p-portail', 'Envoi préparé sur un portail : préparer, code, confirmer, récapitulatif, pièce relue, accusé, À relire, 375 px',
               p111p_portail),)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajouté, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import os
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p111p'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS_P[0][0] for ident, _, _ in NAV.PARCOURS):
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS_P
    code = NAV.lancer(args.navigateur, args.sortie, tuple(i for i, _, _ in PARCOURS_P), continuer=True)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P111p-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
