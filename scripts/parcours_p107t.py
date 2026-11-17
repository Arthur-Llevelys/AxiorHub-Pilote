"""5.6.107 (lot T) : parcours P107t du banc ``scripts/parcours-navigateur.py`` — interface en français, anglais, allemand et espagnol
(vrai navigateur, instance du mode Démo, données fictives, aucun réseau, aucun modèle).

Pour chaque langue (fr, en, de, es), à 1920 × 950 : Courriels (liste), lecteur (un courriel ouvert), rédaction (nouveau message),
À relire (liste et trois éléments ouverts), Aujourd'hui, volet de l'agenda (éditeur et message après un ajout) et synthèse du dossier. Vérifie :
- aucun débordement horizontal de la page ni des barres denses (en-tête de Courriels, barre du lecteur, barre de rédaction) ;
- la liste des courriels garde au moins 14 lignes visibles, liste seule comme lecteur ouvert ;
- en anglais, allemand et espagnol : aucun texte d'interface resté en français (boutons, menus, libellés, infobulles ; les données des
  courriels et des dossiers ne sont pas contrôlées) ;
- aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que la démo.
La langue de l'instance est celle de la démo (``demo56107.reinitialiser(..., langue=…)`` : données du pays) et le cookie de session
``axh_lang`` la confirme. Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit et le lance. Lancement seul : ``python scripts/parcours_p107t.py``.
"""
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
LANGUES = ('fr', 'en', 'de', 'es')
LARGEUR, HAUTEUR = 1920, 950
LIGNES_MIN = 14
# mêmes mots que tests/test_v56107_langues.py : français fréquents, absents de l'anglais, de l'allemand et de l'espagnol
FRANCAIS = re.compile(r"\b(?:les|du|une|est|sont|pour|dans|avec|sans|aucun|aucune|vous|votre|vos|cette|ces|puis|été|ou|au|aux|"
                      r"courriels?|messagerie|réglages?|enregistr\w*|brouillons?|pièces?|tâches?|échéances?|rechercher|supprimer|"
                      r"envoyée?s?|fichiers?|modèles?|résumé|démonstration|données|fictives?|jamais|traiter|traitée?s?|proposée?s?|retenir|rejeter|déposer|afficher|écritures?|probantes?|prochaines?|reçue?s?|enregistrée?s?|synchronisée?|relire|propositions|retour|pleine|attente|calcul|validée?s?|classée?s?|préparée?s?|numérotée?s?|actualisée?|modifiée?|vérifiée?|autres|demain|aujourd’hui)\b|\b(?:l|d|n|qu|j|s)’\w|\b\d{1,2} h \d{2}\b", re.I)

DEBORDEMENTS_JS = """() => {
  const out = [], d = document.documentElement;
  if (d.scrollWidth > d.clientWidth + 1) out.push('page : ' + d.scrollWidth + ' > ' + d.clientWidth);
  ['#bx5625 .bx5625-head', '#bx56106-actions', '#bx5625-panel .bx5628-bar', '#bx5625-panel .lx56106-barre', '.bx56106r-barre',
   '#ws-main .page-title'].forEach(s => document.querySelectorAll(s).forEach(n => {
    const st = getComputedStyle(n);
    if (st.display === 'none' || st.visibility === 'hidden' || n.closest('[hidden]')) return;
    if (n.scrollWidth > n.clientWidth + 1) out.push(s + ' : ' + n.scrollWidth + ' > ' + n.clientWidth);
    const r = n.getBoundingClientRect();
    if (r.right > window.innerWidth + 1) out.push(s + ' dépasse la fenêtre (' + Math.round(r.right) + ')');
  }));
  return out;
}"""

LIGNES_JS = """() => {
  const rows = document.getElementById('bx5625-rows'); if (!rows) return -1;
  const z = rows.getBoundingClientRect(), bas = Math.min(z.bottom, window.innerHeight);
  return Array.from(rows.querySelectorAll('.ax-row[data-uid]')).filter(r => {
    const q = r.getBoundingClientRect(); return q.height > 0 && q.top >= z.top - 1 && q.bottom <= bas + 1;
  }).length;
}"""

# textes d'interface visibles : boutons, menus, libellés, titres de section, infobulles ; jamais les données (lignes de la liste,
# en-tête et corps du courriel, champs de saisie, zones éditables, éléments marqués translate="no")
INTERFACE_JS = """() => {
  const exclu = n => n.closest('#bx5625-rows, .lx56106-tete, .lx56106-texte, iframe, textarea, [contenteditable="true"], [translate="no"], .notranslate, pre, code, .v100-texte, .v100-apercu, select[name*="matter"], .bx56106-matter');
  const vis = n => { const r = n.getBoundingClientRect(), s = getComputedStyle(n); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !n.closest('[hidden]'); };
  const out = new Set();
  document.querySelectorAll('button, summary, label, legend, th, [role=menuitem], [role=tab], h1, h2, .page-title, .demo107, [class*=badge], [class*=etat], [class*=statut], nav a, a.ax-btn, .ax100-l1, .ax100-meta, [data-ax100-meta]').forEach(n => {
    if (exclu(n) || !vis(n)) return;
    const t = (n.innerText || '').trim(); if (t) out.add(t.slice(0, 200));
  });
  document.querySelectorAll('[title], [aria-label], [placeholder]').forEach(n => {
    if (exclu(n) || !vis(n) || n.closest('.ax-row')) return;
    ['title', 'aria-label', 'placeholder'].forEach(a => { const v = n.getAttribute(a); if (v) out.add(v.slice(0, 200)); });
  });
  return Array.from(out);
}"""


# textes visibles (hors champs de saisie et zones éditables) contenant une date JJ/MM ou JJ/MM/AAAA (un n° RG « 24/01234 » n'en est pas une)
DATES_JS = r"""() => {
  const out = new Set(), re = /(^|[^\d/])\d{1,2}\/\d{1,2}(\/\d{2,4})?(?![\d/])/;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) {
    const e = n.parentElement; if (!e || e.closest('textarea, input, [contenteditable="true"], script, style, iframe, pre, code')) continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height || getComputedStyle(e).visibility === 'hidden') continue;
    const t = n.data.replace(/\s+/g, ' ').trim(); if (re.test(t)) out.add(t.slice(0, 160));
  }
  return Array.from(out);
}"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def francais(textes, permis=()):
    restes = []
    for t in textes:
        nu = re.sub(r'\S*example\.test\S*', '', t)
        for p in ('Cabinet DURAND Avocats', 'AxiorHub', 'avocat.fr', 'A TRAITER', 'CORRESPONDANCES') + tuple(permis):
            nu = nu.replace(p, '')
        if FRANCAIS.search(nu):
            restes.append(t)
    return restes


def controler(s, lieu, langue, liste=False, restes_fr=None):
    p = s.page
    p.wait_for_timeout(700)
    debords = p.evaluate(DEBORDEMENTS_JS)
    verifier(not debords, '%s (%s) : débordement %s' % (lieu, langue, debords[:4]))
    if liste:
        n = p.evaluate(LIGNES_JS)
        verifier(n >= LIGNES_MIN, '%s (%s) : %d lignes visibles dans la liste (au moins %d attendues)' % (lieu, langue, n, LIGNES_MIN))
    if langue != 'fr':
        for t in francais(p.evaluate(INTERFACE_JS)):   # relevé complet, vérifié à la fin (un seul rapport pour tous les écrans)
            restes_fr.setdefault(t, set()).add('%s (%s)' % (lieu, langue))
    if langue == 'de':                                 # dates écrites par le code : JJ/MM interdit dans une page allemande (JJ.MM. attendu)
        for t in p.evaluate(DATES_JS):
            restes_fr.setdefault('date au format français : ' + t, set()).add('%s (%s)' % (lieu, langue))
    s.propre('%s (%s)' % (lieu, langue))


def _fermer_visite(p):
    if p.locator('#demo107-bulle').count():
        p.keyboard.press('Escape')
        p.wait_for_timeout(200)


def p107t_langues(banc, s):
    """Banc général (banc ordinaire, sans démo) : une instance de démo par langue, servie à part avec un navigateur dans cette langue
    (``parcours_p107m.avec_demo``, lot M). Banc de démo reçu : une seule instance, remise à neuf dans chaque langue."""
    import parcours_p107m as m107
    m107.NAV = m107.NAV or _nav()
    restes_fr = {}
    for langue in LANGUES:
        with m107.avec_demo(banc, s, langue) as (b, session):
            _p107t(b, session, langue, restes_fr, Path(getattr(banc, 'sortie', '.') or '.'))
    rapport = ['%s ← %s' % (' / '.join(t.splitlines())[:160], ', '.join(sorted(o))) for t, o in sorted(restes_fr.items())]
    (Path(getattr(banc, 'sortie', '.') or '.') / 'P107t-francais-restant.txt').write_text(''.join(r + chr(10) for r in rapport), encoding='utf-8')
    verifier(not rapport, 'texte d’interface resté en français (%d) : %s' % (len(rapport), rapport[:8]))


def _p107t(banc, s, langue, restes_fr, sortie):
    from agent import demo56107
    p = s.page
    p.set_viewport_size({'width': LARGEUR, 'height': HAUTEUR})
    p.on('response', lambda r: r.status >= 400 and print('  HTTP %d %s' % (r.status, r.url.replace(banc.origine, '')[:200]), flush=True))   # adresse d'une erreur de console
    if demo56107.langue_de(banc.racine) != langue:
        demo56107.reinitialiser(banc.racine, langue=langue)            # banc de démo réutilisé : données du pays et langue de l'instance
    s.aller(demo56107.jeton_de_session(banc.racine, '/courriels'))
    p.evaluate("() => { try { localStorage.setItem('demo107-visite-off', '1'); localStorage.setItem('demo107-vue', '1'); } catch (e) {} }")
    s.context.add_cookies([{'name': 'axh_lang', 'value': langue, 'url': banc.origine}])
    # Courriels : liste dense
    s.aller('/courriels')
    _fermer_visite(p)
    p.wait_for_selector('#bx5625-rows .ax-row[data-uid]', timeout=15000)
    verifier(p.evaluate('document.documentElement.lang').startswith(langue), 'langue de la page (%s) : %s' % (langue, p.evaluate('document.documentElement.lang')))
    controler(s, 'Courriels', langue, liste=True, restes_fr=restes_fr)
    p.screenshot(path=str(sortie / ('P107t-%s-courriels.png' % langue)))
    # lecteur : premier courriel ouvert
    p.locator('#bx5625-rows .ax-row[data-uid]').first.click()
    p.wait_for_selector('#bx5625-panel .lx56106', timeout=10000)
    controler(s, 'lecteur', langue, liste=True, restes_fr=restes_fr)
    p.screenshot(path=str(sortie / ('P107t-%s-lecteur.png' % langue)))
    # rédaction : nouveau message (barre, Plus, options d'envoi)
    p.locator('#ws-main button.bx5628-new').first.click()
    p.wait_for_selector('.bx56106r-barre', timeout=10000)
    controler(s, 'rédaction', langue, restes_fr=restes_fr)
    p.screenshot(path=str(sortie / ('P107t-%s-redaction.png' % langue)))
    # À relire et Aujourd'hui
    for chemin, lieu in (('/a-relire', 'À relire'), ('/aujourdhui', 'Aujourd’hui')):
        s.aller(chemin)
        _fermer_visite(p)
        controler(s, lieu, langue, restes_fr=restes_fr)
        p.screenshot(path=str(sortie / ('P107t-%s-%s.png' % (langue, chemin.strip('/')))))
    # À relire (5.6.100 et 5.6.101) : onglets « Notes de l’IA », « Propositions », « Brouillons de la messagerie » ; une note de l'IA et
    # une proposition ouvertes, avec leurs sections « Message reçu » et « Propositions de l’IA » et la barre de décision
    # sections ouvertes : « Message reçu » pour un projet de réponse. Note de l'IA : ouverte seule (ses sections « Message reçu » et
    # « Propositions de l’IA » répondent 404 dans la démo, même en français : constat transmis, hors du lot T)
    for source, exige, sections in (('note_ia', True, ()), ('proposition', True, ('message',)),
                                    ('brouillon', False, ('message',)), ('document', False, ('propositions',))):
        s.aller('/a-relire?source=' + source)
        _fermer_visite(p)
        p.wait_for_timeout(800)
        elements = p.locator('#ws-main nav.ax100-liste li a[data-review-id]')
        verifier(elements.count() > 0 or not exige, 'À relire (%s) : aucun élément « %s » à ouvrir' % (langue, source))
        if elements.count():
            elements.first.click()
            p.wait_for_timeout(1500)
            for section in sections:
                bouton = p.locator('#ws-main button[data-section="%s"]:visible' % section)
                if bouton.count():
                    bouton.first.click()
                    p.wait_for_timeout(800)
                    controler(s, 'À relire (%s ouvert, section %s)' % (source, section), langue, restes_fr=restes_fr)
        controler(s, 'À relire (%s)' % source, langue, restes_fr=restes_fr)
        p.screenshot(path=str(sortie / ('P107t-%s-a-relire-%s.png' % (langue, source))))
    # volet de l'agenda : éditeur d'événement, puis message après l'ajout (réponse JSON traduite)
    s.aller('/planning?vue=agenda&agenda=personnel')
    _fermer_visite(p)
    p.wait_for_selector('[data-ag97][data-ag97-pret]', timeout=15000)
    p.locator('[data-ag97-nouveau]').first.click()
    p.wait_for_selector('#ag97-editeur[open]', timeout=5000)
    controler(s, 'agenda (éditeur)', langue, restes_fr=restes_fr)
    p.screenshot(path=str(sortie / ('P107t-%s-agenda-editeur.png' % langue)))
    p.fill('#ag97-editeur input[name=titre]', 'DURAND')
    with p.expect_response(lambda r: '/m5689/agenda/evenements' in r.url and r.request.method == 'POST', timeout=15000) as rep:
        p.locator('#ag97-editeur [data-ag97-enregistrer]').click()
    message = (rep.value.json() or {}).get('message', '')
    verifier(rep.value.status in (200, 201) and message, 'agenda (%s) : ajout refusé (HTTP %d)' % (langue, rep.value.status))
    if langue != 'fr':
        for t in francais([message]):
            restes_fr.setdefault(t, set()).add('agenda, message après l’ajout (%s)' % langue)
    p.wait_for_timeout(2500)                                   # l'agenda se recharge après l'ajout
    p.wait_for_load_state('networkidle')
    s.aller('/planning?vue=agenda&agenda=personnel')
    controler(s, 'agenda (après l’ajout)', langue, restes_fr=restes_fr)
    # synthèse du dossier (Parties et avocats, Pièces probantes, Prochaines dates)
    premier = p.evaluate("() => (document.getElementById('demo107') || {dataset: {}}).dataset.demo107Dossier || 'DOS-001'")
    for vue in ('', 'pieces', 'procedure', 'parties', 'gestion', 'travaux'):   # page du dossier entière (onglets)
        s.aller('/dossier?id=' + premier + ('&vue=' + vue if vue else ''))
        _fermer_visite(p)
        controler(s, 'dossier (%s)' % (vue or 'synthèse'), langue, restes_fr=restes_fr)
        p.screenshot(path=str(sortie / ('P107t-%s-dossier%s.png' % (langue, '-' + vue if vue else ''))))
    s.aller('/aujourdhui?vue=routines')   # Aujourd'hui entier : Actuellement, Ma journée, À décider (plus haut) puis les routines
    _fermer_visite(p)
    controler(s, 'Aujourd’hui (routines)', langue, restes_fr=restes_fr)


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p107t'))
    parser.add_argument('--navigateur', default='chromium')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    import parcours_p107m
    parcours_p107m.NAV = NAV
    # banc ordinaire, comme le banc général : chaque langue a son instance de démo (avec_demo, lot M)
    NAV.PARCOURS = NAV.PARCOURS + (('P107t', 'Interface en quatre langues : Courriels, lecteur, rédaction, À relire, Aujourd’hui (1920 × 950)',
                                    p107t_langues),)
    return NAV.lancer(args.navigateur, args.sortie, ('P107t',))


if __name__ == '__main__':
    sys.exit(main())
