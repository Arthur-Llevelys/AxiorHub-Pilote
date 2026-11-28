"""5.6.112 : parcours P112 du banc ``scripts/parcours-navigateur.py`` dans un vrai navigateur ; données fictives (DOS-001, DURAND,
MARTIN), aucun réseau, aucun service réel.

P112s-survol : chaque genre de bouton, onglet et lien des pages principales est survolé (thème clair puis sombre) et le contraste du texte
est mesuré sur les couleurs RENDUES pendant le survol (AA 4,5:1, 3:1 pour le grand texte). Avant la 5.6.112, des boutons à bordure bleue
prenaient au survol un fond bleu sous un texte bleu foncé. Le relevé est écrit dans ``P112s-survol.json`` ; le parcours échoue s'il reste
un survol illisible.
Lancement seul : ``python scripts/parcours_p112.py [--navigateur msedge] [--parcours P112s-survol] [--sortie DOSSIER]``.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
NAV = None
RELEVES = {}

PAGES_SURVOL = ('/aujourdhui', '/a-relire', '/courriels', '/courriels?vue=boite', '/courriels?vue=assistante', '/messagerie', '/dossiers',
                '/dossier?id=DOS-001', '/planning', '/planning?vue=taches', '/production', '/recherche', '/corporate?vue=bibliotheque',
                '/parametres', '/parametres?rubrique=voix', '/fonctions', '/conversations-ia')

# Repère un élément de chaque genre (balise, classes, état) : survoler cent lignes identiques n'apprend rien de plus.
REPERER_JS = r"""() => {
  const visible = n => { const r = n.getBoundingClientRect(); if (r.width < 4 || r.height < 4) return false;
    for (let x = n; x; x = x.parentElement) { const t = getComputedStyle(x); if (t.display === 'none' || t.visibility === 'hidden' || +t.opacity === 0 || x.hidden) return false;
      if (x.parentElement && x.parentElement.tagName === 'DETAILS' && !x.parentElement.open && x.tagName !== 'SUMMARY') return false; }
    return true; };
  const texte = n => (n.innerText || '').trim();
  const vus = new Set(); const choisis = [];
  document.querySelectorAll('a[href],button,summary,[role=button],[role=tab],label.ax-btn').forEach(n => {
    if (choisis.length >= 70 || !visible(n) || !texte(n) || n.closest('[disabled],[aria-disabled="true"],[inert]')) return;
    const cls = (typeof n.className === 'string' ? n.className.trim().split(/\s+/).sort().join('.') : '');
    const parent = n.parentElement ? (n.parentElement.tagName + '.' + (typeof n.parentElement.className === 'string' ? n.parentElement.className.trim().split(/\s+/)[0] : '')) : '';
    const sig = n.tagName + '|' + cls + '|' + parent + '|' + (n.getAttribute('aria-current') || '') + '|' + (n.getAttribute('aria-selected') || '');
    if (vus.has(sig)) return; vus.add(sig);
    n.setAttribute('data-s112', String(choisis.length)); choisis.push(sig);
  });
  return choisis.length;
}"""

CONTRASTE_JS = r"""(n) => {
  const rgba = s => { const m = (s || '').match(/[\d.]+/g) || [0, 0, 0, 0]; return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1]; };
  const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
                     return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const mix = (h, b) => { const a = h[3]; return [h[0] * a + b[0] * (1 - a), h[1] * a + b[1] * (1 - a), h[2] * a + b[2] * (1 - a), 1]; };
  const fond = x0 => { const pile = []; let image = false;
    for (let x = x0; x; x = x.parentElement) { const s = getComputedStyle(x); if (s.backgroundImage && s.backgroundImage !== 'none') image = true;
      const c = rgba(s.backgroundColor); if (c[3] > 0) { pile.push(c); if (c[3] >= 1) break; } }
    let b = [255, 255, 255, 1]; for (let i = pile.length - 1; i >= 0; i--) b = mix(pile[i], b); return [b, image]; };
  // Porteur du texte : l'élément lui-même s'il a du texte propre, sinon son premier descendant qui en a.
  let t = n;
  const propre = x => [...x.childNodes].some(c => c.nodeType === 3 && c.textContent.trim());
  if (!propre(n)) { const w = document.createTreeWalker(n, NodeFilter.SHOW_ELEMENT); while (w.nextNode()) { if (propre(w.currentNode)) { t = w.currentNode; break; } } }
  const s = getComputedStyle(t); const [b, image] = fond(t);
  const c = mix(rgba(s.color), b); const l1 = lum(c), l2 = lum(b);
  const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  const taille = parseFloat(s.fontSize), gras = (+s.fontWeight || 400) >= 700;
  const nom = (n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (typeof n.className === 'string' && n.className.trim() ? '.' + n.className.trim().split(/\s+/).slice(0, 3).join('.') : '')
               + (n.parentElement && typeof n.parentElement.className === 'string' && n.parentElement.className.trim() ? ' (dans .' + n.parentElement.className.trim().split(/\s+/)[0] + ')' : '')).slice(0, 110);
  return {el: nom, texte: (n.innerText || '').trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100,
          seuil: (taille >= 24 || (gras && taille >= 18.66)) ? 3 : 4.5, couleur: s.color,
          fond: 'rgb(' + b.slice(0, 3).map(Math.round).join(',') + ')', image: image};
}"""

def _nav():
    if NAV is not None:
        return NAV
    import __main__ as nav
    return nav


def verifier(condition, message):
    _nav().verifier(condition, message)


def _etape(texte):
    print('    · %s' % texte, flush=True)


def mesurer_survol(banc, s, pages=PAGES_SURVOL):
    """Survole un élément de chaque genre par page et par thème ; rend les survols illisibles (et le contraste au repos, pour comparer)."""
    sys.path.insert(0, str(ROOT / 'scripts'))
    import parcours_p111u as u
    u.NAV = _nav()
    ctx, p, externes = u._contexte_mesure(banc, s)
    erreurs = []
    p.on('pageerror', lambda ex: erreurs.append(str(ex)[:200]))
    echecs, mesures = [], 0
    vus = set()
    for theme in ('light', 'dark'):
        p.emulate_media(color_scheme=theme, reduced_motion='reduce')
        for chemin in pages:
            _etape('survol %s %s' % (theme, chemin))
            p.goto(banc.url + chemin, wait_until='load')
            p.wait_for_timeout(400)
            n = p.evaluate(REPERER_JS)
            for i in range(n):
                loc = p.locator('[data-s112="%d"]' % i).first
                try:
                    p.mouse.move(1, 1)
                    repos = loc.evaluate(CONTRASTE_JS)
                    loc.hover(timeout=1500, force=True)
                    p.wait_for_timeout(260)   # transitions (le style injecté est refusé par la CSP des pages, à juste titre)
                    survol = loc.evaluate(CONTRASTE_JS)
                except Exception:   # noqa: BLE001 — élément recouvert ou retiré par la page : ignoré
                    continue
                mesures += 1
                if survol['image'] or survol['ratio'] + 0.005 >= survol['seuil']:
                    continue
                cle = (theme, survol['el'], survol['couleur'], survol['fond'])
                if cle in vus:
                    continue
                vus.add(cle)
                echecs.append(dict(survol, theme=theme, page=chemin, ratio_repos=repos['ratio']))
    p.emulate_media(color_scheme='light')
    ctx.close()
    return {'survols_mesures': mesures, 'echecs': len(echecs), 'exemples': echecs[:80], '_erreurs_js': erreurs[:5],
            '_requetes_externes': externes[:5]}


def p112s_survol(banc, s):
    banc.semer_livrable()
    r = mesurer_survol(banc, s)
    RELEVES['survol'] = r
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / 'P112s-survol.json').write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')
    for x in r['exemples'][:12]:
        print('      survol illisible : %(theme)s %(page)s %(el)s « %(texte)s » %(ratio)s:1 (repos %(ratio_repos)s:1) %(couleur)s sur %(fond)s' % x,
              flush=True)
    verifier(r['survols_mesures'] > 100, 'P112s : trop peu de survols mesurés (%s)' % r['survols_mesures'])
    verifier(not r['_requetes_externes'], 'P112s : requête hors du banc : %s' % r['_requetes_externes'][:1])
    verifier(r['echecs'] == 0, 'P112s : %d survol(s) illisible(s), par exemple %s' % (r['echecs'], r['exemples'][:2]))


def p112c_openwebui(banc, s):
    """Essai réel (hors recette automatique) : une vraie Open WebUI servie en HTTPS (adresse et clé d'API fictives données par
    l'environnement : ``AXIORHUB_ESSAI_OPENWEBUI``, ``AXIORHUB_ESSAI_OPENWEBUI_CLE``, ``AXIORHUB_ESSAI_OPENWEBUI_COMPTE`` « courriel:mot de passe »
    d'un compte fictif ; ``AXIORHUB_ESSAI_CA`` : certificat d'essai auto-signé). Onglet « Conversation IA » du dossier : conversation créée par le
    serveur d'AxiorHub, ouverte dans le cadre, retrouvée à la réouverture ; page « Conversations IA » sur l'instance."""
    import urllib.request
    base = os.environ.get('AXIORHUB_ESSAI_OPENWEBUI', '').rstrip('/')
    cle = os.environ.get('AXIORHUB_ESSAI_OPENWEBUI_CLE', '')
    courriel, _, passe = os.environ.get('AXIORHUB_ESSAI_OPENWEBUI_COMPTE', ':').partition(':')
    verifier(bool(base and cle and courriel), 'P112c : instance Open WebUI d’essai non indiquée (variables AXIORHUB_ESSAI_OPENWEBUI…)')
    ca = os.environ.get('AXIORHUB_ESSAI_CA', '')
    if ca:   # certificat d'essai (auto-signé) reconnu par CE processus seulement : Python sous Windows ignore SSL_CERT_FILE
        import ssl
        origine_ctx = ssl.create_default_context

        def avec_ca(*a, **k):   # seule autorité : un certificat homonyme du magasin Windows ferait échouer la vérification
            return origine_ctx(cafile=ca)
        ssl.create_default_context = avec_ca
        ssl._create_default_https_context = avec_ca   # urllib et http.client sans contexte explicite
    sys.path.insert(0, str(ROOT / 'scripts'))
    import parcours_p111u as u
    u.NAV = _nav()
    with banc.desk() as d:
        d.setting('workstation:url:openwebui', base + '/')

    def conversations():
        req = urllib.request.Request(base + '/api/v1/chats/?page=1', headers={'Authorization': 'Bearer ' + cle})
        with urllib.request.urlopen(req, timeout=20) as r:
            return [x for x in json.loads(r.read()) if str(x.get('title', '')).startswith('Dossier DOS-001')]
    avant = len(conversations())
    # contexte propre à l'essai : certificat auto-signé de l'instance accepté (dans ce navigateur d'essai seulement)
    ctx = s.context.browser.new_context(viewport={'width': 1600, 'height': 1000}, locale='fr-FR', ignore_https_errors=True)
    externes = []
    ctx.on('request', lambda r: externes.append(r.url[:200]) if not r.url.startswith((banc.origine, 'data:', 'blob:', 'about:')) else None)
    p = ctx.new_page()
    p.goto(banc.url + '/connexion', wait_until='load')
    p.fill('#cx48-ident', banc.ident)
    p.fill('#cx48-pass', banc.mot_de_passe)
    with p.expect_navigation():
        p.click('form.cx48-form button[type=submit]')
    erreurs, console = [], []
    p.on('pageerror', lambda ex: erreurs.append(str(ex)[:200]))
    p.on('console', lambda m: console.append((m.type, m.text[:200])))
    sortie = Path(getattr(banc, 'sortie', '.') or '.')
    _etape('page Conversations IA : instance dans le cadre')
    p.goto(banc.url + '/conversations-ia', wait_until='load')
    verifier(p.get_attribute('[data-cv112-cadre]', 'src') == base + '/', 'P112c : le cadre ne vise pas l’instance')
    _etape('clé d’API enregistrée (vérifiée par Open WebUI)')
    p.fill('.cv112-cle input[name=cle]', cle)
    with p.expect_navigation(timeout=30000):
        p.click('.cv112-cle form[data-api$="/cle"] button[type=submit]')
    p.wait_for_load_state('load')
    verifier('Votre clé d’API Open WebUI est enregistrée' in (p.text_content('.cv112-cle') or ''), 'P112c : clé non enregistrée')   # volet replié une fois la clé posée
    _etape('onglet du dossier : conversation créée et ouverte dans le cadre')
    p.goto(banc.url + '/dossier?id=DOS-001&vue=conversation', wait_until='load')
    p.wait_for_function("() => (document.querySelector('[data-cv112-cadre]').getAttribute('src') || '').includes('/c/')", timeout=30000)
    src = p.get_attribute('[data-cv112-cadre]', 'src')
    verifier(src.startswith(base + '/c/'), 'P112c : adresse du cadre inattendue : %s' % src)
    verifier('créée' in p.inner_text('[data-cv112-etat]'), 'P112c : état de l’ouverture : %s' % p.inner_text('[data-cv112-etat]'))
    verifier(len(conversations()) == avant + 1, 'P112c : la conversation du dossier n’a pas été créée dans Open WebUI')
    cadre = p.frame_locator('[data-cv112-cadre]')
    if passe:
        _etape('connexion à Open WebUI dans le cadre (compte fictif)')
        try:
            cadre.locator('input[name=email], input[autocomplete=email], input[type=email]').first.fill(courriel, timeout=30000)
            cadre.locator('input[type=password]').first.fill(passe)
            cadre.locator('input[type=password]').first.press('Enter')
        except Exception as ex:   # noqa: BLE001 — déjà connecté dans ce navigateur : rien à saisir
            _etape('pas de page de connexion (%s)' % str(ex)[:80])
    p.wait_for_timeout(15000)
    fr = next((f for f in p.frames if f.url.startswith(base)), None)
    verifier(fr is not None, 'P112c : aucune page d’Open WebUI dans le cadre')
    texte = fr.evaluate('() => document.body.innerText') if fr else ''
    p.screenshot(path=str(sortie / 'P112c-onglet-dossier.png'))
    if 'Dossier DOS-001' not in texte:
        print('      cadre :', fr.url if fr else None, '|', ' / '.join(texte[:300].splitlines()), flush=True)
        for x in console[-15:]:
            print('      console :', x, flush=True)
    verifier('Dossier DOS-001' in texte, 'P112c : la conversation du dossier n’apparaît pas dans le cadre')
    _etape('réouverture : même conversation, aucune nouvelle')
    p.goto(banc.url + '/dossier?id=DOS-001&vue=conversation', wait_until='load')
    p.wait_for_function("() => (document.querySelector('[data-cv112-cadre]').getAttribute('src') || '').includes('/c/')", timeout=30000)
    verifier(p.get_attribute('[data-cv112-cadre]', 'src') == src, 'P112c : une autre conversation a été ouverte')
    verifier(len(conversations()) == avant + 1, 'P112c : une conversation en double a été créée')
    p.goto(banc.url + '/conversations-ia', wait_until='load')
    p.wait_for_timeout(5000)
    p.screenshot(path=str(sortie / 'P112c-page.png'))
    hors = [x for x in externes if not x.startswith(base)]
    verifier(not hors, 'P112c : requête vers une autre adresse que le banc et l’instance : %s' % hors[:3])
    verifier(not erreurs, 'P112c : erreur JavaScript : %s' % erreurs[:2])
    ctx.close()


PARCOURS_112 = (('P112s-survol', 'Survol des boutons, onglets et liens lisible (thèmes clair et sombre)', p112s_survol),
                ('P112c-openwebui', 'Conversations IA : vraie Open WebUI dans le cadre, conversation propre au dossier (essai réel, hors recette)',
                 p112c_openwebui))


def main(argv=None):
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p112'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--parcours', default='P112s-survol')
    args = parser.parse_args(argv)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    connus = {ident for ident, _, _ in NAV.PARCOURS}
    NAV.PARCOURS = NAV.PARCOURS + tuple(x for x in PARCOURS_112 if x[0] not in connus)
    return NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)


if __name__ == '__main__':
    sys.exit(main())
