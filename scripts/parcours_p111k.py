"""5.6.111 (lot K) : parcours P111k du banc ``scripts/parcours-navigateur.py`` — Conseil : modèles et questionnaires par droit applicable,
dans un vrai navigateur (Edge sous Windows) ; données fictives (DOS-001, DURAND, MARTIN), aucun réseau, aucun modèle d'IA réel.

P111k-matrice : bibliothèque › « Couverture des modèles par droit applicable » — tableau type d'acte × droit × langue, cases « À préparer »
  jamais comblées par un autre droit (CGV consommateurs : France seulement), totaux par droit, légende ; aucune écriture à l'affichage ;
  1920 × 1080, 125 % (1536 × 864) et 375 px sans défilement horizontal de la page ; thème sombre ; tableau atteint au clavier.
P111k-questionnaire : Produire › Rédiger — carte « Préparer un acte avec un modèle et son questionnaire » : bail commercial (droit français)
  ouvert pour DOS-001 ; réponses par sections (Parties, Opération, Économie…), « Je ne sais pas » sur le loyer ; après enregistrement : états
  « Confirmé » et « Inconnu », section Désignation rédigée, section Loyer « À compléter », « Contrôle avant « prêt » » (signataires) ;
  même questionnaire rouvert depuis Produire puis depuis une opération de Cession (un seul exemplaire) ; brouillon Word téléchargé, « document
  rouvert et contrôlé » ; aucune erreur JavaScript ni CSP, aucune requête externe.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p111k.py [--navigateur msedge] [--parcours P111k-matrice,P111k-questionnaire] [--sortie DOSSIER]``.
"""
import json
from pathlib import Path
import sys
import time

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


def _sans_defilement(p):
    return p.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')


def p111k_matrice(banc, s):
    s.connecter()
    p = s.page
    with banc.desk() as d:
        avant = d.db.execute('SELECT COUNT(*) FROM template_versions5684').fetchone()[0] if d.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='template_versions5684'").fetchone() else 0
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/corporate?vue=bibliotheque')
    lien = p.locator('a', has_text='Couverture des modèles par droit applicable')
    verifier(lien.count() >= 1, 'P111k matrice : lien absent de la bibliothèque')
    with p.expect_navigation():
        lien.first.click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('table.b111-table caption').count() == 1, 'P111k matrice : tableau absent')
    lignes = p.locator('table.b111-table tbody tr').count()
    verifier(lignes == 16, 'P111k matrice : %d familles au lieu de 16' % lignes)
    entetes = p.locator('table.b111-table thead th').all_inner_texts()
    MESURES['colonnes'] = [x.replace('\n', ' ') for x in entetes]
    verifier(len(entetes) == 9 and 'DROIT FRANÇAIS' in entetes[1].upper(), 'P111k matrice : colonnes inattendues : %s' % entetes)
    b2c = p.locator('table.b111-table tbody tr', has_text='Conditions générales de vente (consommateurs)')
    cases = b2c.locator('td').all_inner_texts()
    verifier('Conditions générales de vente aux consommateurs' in cases[0] and all(c.strip() == 'À préparer' for c in cases[1:]),
             'P111k matrice : CGV consommateurs comblées hors de France : %s' % cases)
    totaux = {r.split('\t')[0].upper(): r for r in p.locator('table.b111-totaux tbody tr').all_inner_texts()}   # en-têtes en capitales (CSS)
    MESURES['totaux'] = totaux
    attendus = {'DROIT FRANÇAIS': '16 / 16', 'DROIT ANGLAIS (ANGLETERRE ET PAYS DE GALLES)': '7 / 16', 'DROIT ALLEMAND': '7 / 16', 'DROIT ESPAGNOL': '7 / 16',
                'DROIT QUÉBÉCOIS (CANADA)': '4 / 32', 'DROIT DE L’ONTARIO (CANADA)': '4 / 32'}
    verifier(all(v in totaux.get(k, '') for k, v in attendus.items()), 'P111k matrice : totaux inattendus : %s' % totaux)
    verifier(_sans_defilement(p), 'P111k matrice : défilement horizontal de la page à 1920 × 1080')
    s.capture(_sortie(banc) / 'P111k-matrice-1920.png')
    p.set_viewport_size({'width': 1536, 'height': 864})
    verifier(_sans_defilement(p), 'P111k matrice : défilement horizontal de la page à 1536 × 864 (125 %)')
    p.set_viewport_size({'width': 375, 'height': 812})
    verifier(_sans_defilement(p), 'P111k matrice : défilement horizontal de la page à 375 px')
    defile = p.evaluate("(() => { const z = document.querySelector('.b111-defile'); return [z.scrollWidth, z.clientWidth, getComputedStyle(z).overflowX]; })()")
    MESURES['zone_375'] = defile
    verifier(defile[2] == 'auto', 'P111k matrice : le tableau ne défile pas dans sa zone à 375 px')
    s.capture(_sortie(banc) / 'P111k-matrice-375.png')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    p.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    s.capture(_sortie(banc) / 'P111k-matrice-sombre.png')
    p.evaluate("document.documentElement.removeAttribute('data-theme')")
    p.focus('.b111-defile')
    verifier(p.evaluate("document.activeElement.classList.contains('b111-defile')"), 'P111k matrice : zone du tableau non atteignable au clavier')
    with banc.desk() as d:
        apres = d.db.execute('SELECT COUNT(*) FROM template_versions5684').fetchone()[0] if d.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='template_versions5684'").fetchone() else 0
    verifier(apres == avant, 'P111k matrice : l’affichage a écrit dans le registre (%d → %d)' % (avant, apres))
    s.propre('P111k matrice')


def _ouvrir_depuis(p, s, chemin, modele):
    s.aller(chemin)
    carte = p.locator('section.q111-ouverture').first
    verifier(carte.count() == 1, 'P111k : carte d’ouverture absente de %s' % chemin)
    carte.locator('select[name=dossier]').select_option('DOS-001')
    carte.locator('select[name=modele]').select_option(modele)
    with p.expect_navigation(timeout=20000):
        carte.locator('button[type=submit]').click()
    p.wait_for_load_state('networkidle')
    verifier('questionnaire=' in p.url, 'P111k : le questionnaire ne s’est pas ouvert depuis %s (%s)' % (chemin, p.url))
    return p.url.split('questionnaire=')[1].split('&')[0]


def p111k_questionnaire(banc, s):
    s.connecter()
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    qid = _ouvrir_depuis(p, s, '/studio?matter=DOS-001', 'fr_bail_commercial')
    MESURES['questionnaire'] = qid
    verifier(p.locator('[data-q108]').count() == 1, 'P111k : formulaire de l’acte absent')
    groupes = p.locator('details.q108-groupe > summary').all_inner_texts()
    MESURES['sections'] = groupes
    verifier(any(g.startswith('Économie') for g in groupes) and any(g.startswith('Pouvoirs') for g in groupes), 'P111k : sections inattendues : %s' % groupes)
    p.evaluate("document.querySelectorAll('details.q108-groupe').forEach(d => d.open = true)")
    for champ, valeur in (('bailleur', 'DURAND SCI (fictive)'), ('preneur', 'MARTIN SARL (fictive)'), ('locaux', 'Local fictif de 80 m², rez-de-chaussée'),
                          ('destination', 'Commerce de détail'), ('date_effet', '2027-01-01')):
        p.fill('#q108-' + champ, valeur)
    p.click('[data-q108-champ="loyer"] .q108-statut[data-statut="inconnu"]')
    verifier(p.get_attribute('[data-q108-champ="loyer"] .q108-statut[data-statut="inconnu"]', 'aria-pressed') == 'true', 'P111k : « Je ne sais pas » non marqué')
    with p.expect_navigation(timeout=20000):
        p.click('.q108-form button[type=submit]')
    p.wait_for_load_state('networkidle')
    etat = {c: [x.strip() for x in p.locator('[data-q108-champ="%s"] .q111-etat' % c).all_text_contents()] for c in ('bailleur', 'loyer')}
    MESURES['etats'] = etat
    verifier(etat['bailleur'] == ['Confirmé'] and etat['loyer'] == ['Inconnu'], 'P111k : états inattendus : %s' % etat)
    sections = {}
    for el in p.locator('article.q108-section').all():
        sections[el.get_attribute('data-section')] = el.get_attribute('data-etat')
    MESURES['apercu'] = sections
    verifier(sections.get('art-designation') == 'redigee' and sections.get('art-loyer') == 'a_completer', 'P111k : sections inattendues : %s' % sections)
    controle = p.locator('.q111-controles').inner_text()
    verifier('Signataires et pouvoirs' in controle, 'P111k : contrôle avant « prêt » sans les signataires : %s' % controle[:200])
    s.capture(_sortie(banc) / 'P111k-questionnaire.png')
    with p.expect_download(timeout=30000) as tele:
        p.click('.q108-telecharger')
    nom = tele.value.suggested_filename
    fin = time.time() + 10
    while time.time() < fin and 'rouvert' not in p.inner_text('.q108-etat'):
        p.wait_for_timeout(200)
    message = p.inner_text('.q108-etat')
    MESURES['telechargement'] = {'fichier': nom, 'message': message}
    verifier(nom.endswith('.docx') and 'brouillon partiel' in nom, 'P111k : brouillon Word inattendu : %s' % nom)
    verifier('Document rouvert et contrôlé' in message, 'P111k : contrôle du document non signalé : %s' % message)
    # même questionnaire depuis Produire, puis depuis une opération de Cession (un seul exemplaire)
    verifier(_ouvrir_depuis(p, s, '/studio?matter=DOS-001', 'fr_bail_commercial') == qid, 'P111k : Produire a ouvert un second questionnaire')
    with banc.desk() as d:
        from agent import cessions5684
        op = cessions5684.create(d, {'type': 'droit_bail', 'matter': 'DOS-001', 'nom': 'Cession fictive du droit au bail', 'idem': 'p111k-cession'}, 'banc')
    verifier(_ouvrir_depuis(p, s, '/cessions?type=bail&operation=%s&vue=documents' % op['id'], 'fr_bail_commercial') == qid,
             'P111k : Cession a ouvert un second questionnaire')
    with banc.desk() as d:
        n = d.db.execute("SELECT COUNT(*) FROM questionnaires56108 WHERE modele='fr_bail_commercial' AND matter='DOS-001'").fetchone()[0]
    verifier(n == 1, 'P111k : %d questionnaires pour le même dossier et la même version' % n)
    p.set_viewport_size({'width': 375, 'height': 812})
    s.aller('/corporate?vue=bibliotheque&questionnaire=' + qid)
    verifier(_sans_defilement(p), 'P111k : défilement horizontal du questionnaire à 375 px')
    s.capture(_sortie(banc) / 'P111k-questionnaire-375.png')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.propre('P111k questionnaire')


PARCOURS = (('P111k-matrice', 'Conseil : matrice de couverture par droit et langue, « À préparer », 1920/125 %/375 px, sombre, clavier', p111k_matrice),
            ('P111k-questionnaire', 'Conseil : questionnaire de bail commercial ouvert depuis Produire et Cession (un seul), états, sections, Word contrôlé',
             p111k_questionnaire))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import os
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p111k'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS[0][0] for ident, _, _ in NAV.PARCOURS):
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / 'P111k-mesures.json').write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(MESURES, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
