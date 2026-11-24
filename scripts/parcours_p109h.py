"""5.6.109 (lot H) : parcours P109h du banc ``scripts/parcours-navigateur.py`` (vrai navigateur, version serveur du banc, données fictives,
aucun réseau, aucun modèle réel).

P109h-assistant (F09, F10) :
- 1536 × 864 (1920 × 1080 à 125 %) puis 1920 × 1080 : volet ouvert ; « Historique », « Nouvelle discussion » et « Ouvrir en grand » dans
  l'en-tête ; Instruction, Envoyer et Conversation vocale visibles et non recouverts sans défiler ; Historique ouvert (liste dans la zone
  centrale) : la saisie et Envoyer restent visibles ; un fil s'ouvre dans le volet sans relancer de travail (file inchangée) ;
- vue agrandie ``/assistant?vue=historique&thread=…`` à 1536 × 864 : la zone « Nouvelle instruction » et « Continuer cette discussion »
  visibles sans défiler ; brouillon tapé, page quittée puis retour arrière : brouillon rendu (côté serveur), discussion choisie gardée ;
- aucun défilement horizontal à 375 × 812.
P109h-apparence (F10) :
- Paramètres › Apparence : couleur #183153 enregistrée pour le compte ; la barre latérale porte ``ax-nav-custom`` ; le contraste calculé
  dans le navigateur entre le texte d'un lien du menu et son fond atteint 4,5:1 en clair et en sombre ; « Rétablir » retire la classe.
Aucune erreur JavaScript ni CSP, aucune requête vers une autre adresse que le banc. Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit.
Lancement seul : ``python scripts/parcours_p109h.py [--parcours P109h-assistant,P109h-apparence] [--sortie DOSSIER]``.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= window.innerWidth + 1'
VISIBLE = """(sel) => { const n = Array.from(document.querySelectorAll(sel)).find(x => x.getClientRects().length && x.getBoundingClientRect().width > 0);
  if (!n) return 'absent'; const r = n.getBoundingClientRect(), vv = window.visualViewport || {height: innerHeight, offsetTop: 0, width: innerWidth};
  if (r.top < vv.offsetTop - 1 || r.bottom > vv.offsetTop + vv.height + 1 || r.left < -1 || r.right > vv.width + 1)
    return 'hors de la fenêtre (' + Math.round(r.top) + '-' + Math.round(r.bottom) + ' / ' + Math.round(vv.height) + ')';
  const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return e && (e === n || n.contains(e) || e.contains(n)) ? '' : 'recouvert par ' + (e ? e.tagName + '.' + e.className : 'rien'); }"""
CONTRASTE_MENU = """() => { const a = document.querySelector('#ws-sidebar.ax-nav-custom nav a:not([aria-current])'); if (!a) return null;
  const rgb = c => (c.match(/[\\d.]+/g) || [0, 0, 0]).map(Number);
  const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  let fond = null; for (let x = a; x; x = x.parentElement) { const c = rgb(getComputedStyle(x).backgroundColor); if (c.length < 4 || c[3] > 0.5) { fond = c; break; } }
  const t = lum(rgb(getComputedStyle(a).color)), f = lum(fond || [255, 255, 255]); return (Math.max(t, f) + 0.05) / (Math.min(t, f) + 0.05); }"""


def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _visible(p, sel, lieu):
    etat = p.evaluate(VISIBLE, sel)
    verifier(not etat, '%s : %s %s' % (lieu, sel, etat))


def semer_fil(banc, compte):
    """Un fil attribué au compte du banc, deux messages (demande, réponse), sans travail en file."""
    import secrets
    with banc.desk() as d:
        tid = secrets.token_hex(16)
        quand = d.now()
        d.db.execute('INSERT INTO assistant_threads(id,scope,matter,mail_key,title,created,updated) VALUES(?,?,?,?,?,?,?)',
                     (tid, 'cabinet', 'DOS-001', '', 'Clause de non-concurrence DURAND', quand, quand))
        d.db.execute('INSERT INTO assistant_thread_owners109(thread_id,cabinet_id,owner_id,revision,attribue_le,attribue_par) VALUES(?,?,?,1,?,?)',
                     (tid, 'cabinet', compte, quand, 'banc'))
        for role, texte in (('user', 'Analyse la clause de non-concurrence du contrat DURAND.'), ('assistant', 'Réponse fictive du banc, à relire.')):
            d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES(?,?,?,'done','[]',NULL,?,?)",
                         (tid, role, texte, quand, quand))
        d.db.commit()
    return tid


def p109h_assistant(banc, s):
    p = s.page
    sortie = Path(banc.sortie)
    p.set_viewport_size({'width': 1536, 'height': 864})
    s.connecter()
    compte = getattr(banc, 'compte_actif', None) or 'cabinet'
    tid = semer_fil(banc, compte)
    with banc.desk() as d:
        travaux = d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    for nom, vue in (('125 %', {'width': 1536, 'height': 864}), ('1920 × 1080', {'width': 1920, 'height': 1080})):
        p.set_viewport_size(vue)
        s.aller('/aujourdhui')
        p.click('#ws-ai-launcher')
        p.wait_for_selector('#ws-ai-dock:not([hidden]) #h109-historique')
        if nom != '125 %':
            # retour : le volet rouvert (autre page, même onglet) rend l'historique et le fil laissés ouverts, sans les relancer
            p.wait_for_selector('#h109-panneau:not([hidden]) .h109-fil-volet:not([hidden]) .h109-message')
            _visible(p, '#ws-ai-send', 'fil rendu à la réouverture (%s)' % nom)
            p.click('#h109-panneau .h109-retour')
            p.click('#h109-historique')
        for sel in ('#h109-historique', '#h109-nouvelle', '#h109-grand', '#ws-ai-close', '#ws-ai-question', '#ws-ai-send', '#ws-ai-talk'):
            _visible(p, sel, 'volet fermé sur l’historique (%s)' % nom)
        p.click('#h109-historique')
        p.wait_for_selector('#h109-panneau:not([hidden]) .h109-ligne')
        verifier(not p.is_visible('#h109-panneau .h109-fil-volet'), 'fil encore affiché avec la liste (%s)' % nom)
        for sel in ('#ws-ai-question', '#ws-ai-send', '#h109-historique'):
            _visible(p, sel, 'historique ouvert (%s)' % nom)
        p.click('#h109-panneau .h109-ligne button')
        p.wait_for_selector('#h109-panneau .h109-fil-volet:not([hidden]) .h109-message')
        verifier('Analyse la clause' in p.inner_text('#h109-panneau'), 'fil non affiché dans le volet')
        _visible(p, '#ws-ai-send', 'fil ouvert dans le volet (%s)' % nom)
        p.locator('#ws-ai-dock').screenshot(path=str(sortie / ('P109h-volet-%s.png' % nom.replace(' ', '').replace('%', 'pc').replace('×', 'x'))))
        p.keyboard.press('Escape')
    with banc.desk() as d:
        verifier(d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == travaux, 'une lecture de l’historique a mis un travail en file')
    s.propre('volet : Historique')

    p.set_viewport_size({'width': 1536, 'height': 864})
    s.aller('/assistant?vue=historique&thread=' + tid)
    p.wait_for_selector('[data-h109-detail]')
    for sel in ('#h109-question', '.h109-composeur button', '.h109-choisi'):
        _visible(p, sel, 'vue agrandie (125 %)')
    texte = 'Brouillon fictif : compléter la clause pour MARTIN.'
    p.fill('#h109-question', texte)
    p.wait_for_timeout(1800)                       # enregistrement différé du brouillon (1,2 s)
    s.aller('/aujourdhui')
    p.go_back()
    p.wait_for_selector('[data-h109-detail]')
    p.wait_for_function("t => document.getElementById('h109-question').value === t", arg=texte, timeout=5000)
    verifier(p.locator('.h109-choisi').count() == 1, 'discussion choisie perdue au retour')
    p.screenshot(path=str(sortie / 'P109h-vue-agrandie-125pc.png'))
    p.set_viewport_size({'width': 375, 'height': 812})
    verifier(p.evaluate(SANS_DEFILEMENT), 'défilement horizontal de la vue agrandie à 375 px')
    s.propre('vue agrandie : brouillon et retour')


def p109h_apparence(banc, s):
    p = s.page
    p.set_viewport_size({'width': 1536, 'height': 864})
    s.connecter()
    s.aller('/parametres?rubrique=apparence')
    p.wait_for_selector('[data-ax109-apparence]')
    p.fill('[data-ax109-hex=clair]', '#183153')
    p.click('[data-ax109-enregistrer]')
    p.wait_for_selector('#ws-sidebar.ax-nav-custom', timeout=8000)
    for theme in ('light', 'dark'):
        p.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        p.wait_for_timeout(150)
        c = p.evaluate(CONTRASTE_MENU)
        verifier(c is not None and c >= 4.5, 'contraste du menu insuffisant en %s : %s' % (theme, c))
    p.screenshot(path=str(Path(banc.sortie) / 'P109h-apparence.png'))
    p.wait_for_selector('[data-ax109-defaut]')
    p.click('[data-ax109-defaut]')
    p.wait_for_function("() => !document.querySelector('#ws-sidebar.ax-nav-custom')", timeout=8000)
    s.propre('apparence : couleur du menu')


PARCOURS = (('P109h-assistant', 'Assistant : Historique dans l’en-tête, saisie visible à 125 %, fil sans relance, brouillon rendu au retour', p109h_assistant),
            ('P109h-apparence', 'Paramètres › Apparence : couleur du menu enregistrée, contraste clair et sombre, rétablir', p109h_apparence))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS`` du banc)."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p109h'))
    parser.add_argument('--navigateur', default='chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS))
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS[0][0] for ident, _, _ in NAV.PARCOURS):   # déjà inscrits par le banc depuis l'intégration
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
