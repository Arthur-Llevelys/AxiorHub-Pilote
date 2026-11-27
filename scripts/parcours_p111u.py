"""5.6.111 (lot U) : parcours P111u du banc ``scripts/parcours-navigateur.py`` — ergonomie, mesures et précontrôle du poste, dans un vrai
navigateur (Edge sous Windows) ; données fictives (DOS-001, DURAND, MARTIN), aucun réseau, aucun service réel (messagerie, stockage et
modèle sont des faux du banc, dans son processus).

P111u-mesures (U02, relevés chiffrés, peu d'exigences : le rapport est écrit dans ``P111u-mesures-<phase>.json``) :
- navigation froide (cache du navigateur vidé par le protocole du navigateur) puis chaude (trois rechargements) des cinq accès de travail
  (Aujourd'hui, Courriels, Dossiers, Agenda, Produire) et d'À relire : premier octet, DOMContentLoaded, chargement, ressources et octets
  transférés, ``Server-Timing`` de la page (authentification, SQLite, IMAP, construction, total), tâches longues (Long Tasks API) et temps
  bloquant, mémoire JavaScript (``performance.memory``), retour arrière (durée, type ``back_forward``) ;
- faux services lents : messagerie IMAP (3 s par connexion) et stockage des dossiers (1,5 s par lecture) ; chaque accès doit s'afficher
  sans les attendre (temps de la page, nombre d'appels pendant l'affichage) ; un travail d'IA lourd (faux modèle à 20 s par réponse,
  exécuté par la file) ne bloque pas les lectures ;
- accessibilité mesurée sur les couleurs RENDUES (thème clair puis sombre) : contraste du texte (AA 4,5:1, 3:1 pour le grand texte),
  cibles de moins de 24 × 24 px (WCAG 2.2 AA 2.5.8 ; exceptions : lien dans une phrase, espacement suffisant, contrôle natif non stylé),
  focus visible au clavier (25 premiers arrêts), défilement horizontal à 1536 × 864 (125 %) et 375 px.
P111u-relecture (U01) : À relire, dix projets relus d'affilée sans perte de texte (édition conservée), commandes de décision visibles sans
  défiler à 1920 × 1080 et 1536 × 864, filtre et position gardés après chaque décision et au retour, « Envoyer » distinct de « Retenir » et
  de « Déposer dans Brouillons » ; aucune requête externe, aucun envoi réel.
P111u-accueil (U01) : Aujourd'hui (ordre bonjour/météo, « Actuellement », À décider à côté de Ma journée), Dossiers (responsable, prochaine
  action, derniers résultats de l'IA, pièces sources), Corporate › Bibliothèque lisible à 375 px sans défilement horizontal.
P111u-precontrole (B6) : Paramètres › Voix, carte « Précontrôle du poste » : ce qui manque est dit avant d'installer (moteur, composant
  Microsoft simulé par un faux moteur qui rend 0xC0000135, micro, lecteur) ; aucun téléchargement, aucun micro réel.
Non inscrit dans ``PARCOURS`` : l'intégrateur l'inscrit. Lancement seul :
``python scripts/parcours_p111u.py [--navigateur msedge] [--parcours P111u-mesures,...] [--phase avant|apres] [--sortie DOSSIER]``.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
NAV = None
MESURES = {}
FICHIER = {}
PHASE = {'nom': 'apres'}
ACCES = (('aujourdhui', '/aujourdhui'), ('courriels', '/courriels'), ('dossiers', '/dossiers'), ('agenda', '/planning'),
         ('produire', '/production'), ('a_relire', '/a-relire'))
PAGES_A11Y = ACCES + (('fiche_dossier', '/dossier?id=DOS-001'), ('bibliotheque', '/corporate?vue=bibliotheque'),
                      ('voix', '/parametres?rubrique=voix'))
SANS_DEFILEMENT = 'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'

# Tâches longues relevées dès le début du document (le script d'essai est posé avant ceux de la page).
INIT_PERF = """(() => { window.__u111 = {longues: []};
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) { window.__u111.longues.push([Math.round(e.startTime), Math.round(e.duration)]); } })
        .observe({type: 'longtask', buffered: true}); } catch (e) { window.__u111.longues = null; } })();"""

PERF_JS = """() => {
  const n = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource');
  const st = {}; (n.serverTiming || []).forEach(x => { st[x.name] = Math.round(x.duration * 10) / 10; if (x.description) { st[x.name + '_n'] = Number(x.description) || x.description; } });
  const lt = (window.__u111 && window.__u111.longues) || [];
  const mem = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1024) : null;
  return {type: n.type, ttfb: Math.round(n.responseStart - n.requestStart), dcl: Math.round(n.domContentLoadedEventEnd), charge: Math.round(n.loadEventEnd),
          html_ko: Math.round((n.encodedBodySize || 0) / 102.4) / 10, ressources: res.length,
          transfert_ko: Math.round((res.reduce((a, r) => a + (r.transferSize || 0), 0) + (n.transferSize || 0)) / 102.4) / 10,
          depuis_cache: res.filter(r => r.transferSize === 0 && r.decodedBodySize > 0).length,
          server_timing: st, taches_longues: lt.length, tbt: lt.reduce((a, x) => a + Math.max(0, x[1] - 50), 0),
          plus_longue: lt.reduce((a, x) => Math.max(a, x[1]), 0), memoire_js_ko: mem,
          volets_fermes: document.querySelectorAll('details:not([open])').length,
          volets_fermes_ko: Math.round([...document.querySelectorAll('details:not([open])')].reduce((a, d) => a + d.innerHTML.length, 0) / 102.4) / 10};
}"""

# Contraste, cibles, focus : couleurs RENDUES (styles calculés, transparence composée sur les fonds parents).
AUDIT_JS = r"""() => {
  const rgba = s => { const m = (s || '').match(/[\d.]+/g) || [0, 0, 0, 0]; return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1]; };
  const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
                     return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const mix = (h, b) => { const a = h[3]; return [h[0] * a + b[0] * (1 - a), h[1] * a + b[1] * (1 - a), h[2] * a + b[2] * (1 - a), 1]; };
  const visible = n => { const r = n.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(n); if (s.visibility === 'hidden' || s.display === 'none') return false;
    if (r.width <= 2 && r.height <= 2) return false; for (let x = n; x; x = x.parentElement) { const t = getComputedStyle(x); if (+t.opacity === 0 || x.hidden) return false;
      if (x.parentElement && x.parentElement.tagName === 'DETAILS' && !x.parentElement.open && x.tagName !== 'SUMMARY') return false; }   // contenu d'un volet fermé : non peint
    return true; };
  const fond = n => { const pile = []; let image = false;
    for (let x = n; x; x = x.parentElement) { const s = getComputedStyle(x); if (s.backgroundImage && s.backgroundImage !== 'none') image = true;
      const c = rgba(s.backgroundColor); if (c[3] > 0) { pile.push(c); if (c[3] >= 1) break; } }
    let b = [255, 255, 255, 1]; for (let i = pile.length - 1; i >= 0; i--) b = mix(pile[i], b); return [b, image]; };
  const nom = n => (n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '')).slice(0, 70);
  const echecs = []; let mesures = 0, images = 0;
  const vus = new Set();
  document.querySelectorAll('body *').forEach(n => {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'svg', 'path'].includes(n.tagName)) return;
    const texte = [...n.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join('').trim();
    if (!texte || !visible(n)) return;
    if (n.closest('[disabled],[aria-disabled="true"],[inert]')) return;            // composant inactif : exempté (1.4.3)
    const s = getComputedStyle(n);
    if (s.clip === 'rect(0px, 0px, 0px, 0px)' || s.clipPath === 'inset(50%)') return;
    const [b, image] = fond(n); if (image) { images++; return; }
    const c = mix(rgba(s.color), b); const l1 = lum(c), l2 = lum(b);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const taille = parseFloat(s.fontSize), gras = (+s.fontWeight || 400) >= 700;
    const seuil = (taille >= 24 || (gras && taille >= 18.66)) ? 3 : 4.5;
    mesures++;
    if (ratio + 0.005 < seuil) { const cle = nom(n) + '|' + s.color + '|' + b.join(',');
      if (!vus.has(cle)) { vus.add(cle); echecs.push({el: nom(n), texte: texte.slice(0, 40), ratio: Math.round(ratio * 100) / 100, seuil: seuil,
                                                       couleur: s.color, fond: 'rgb(' + b.slice(0, 3).map(Math.round).join(',') + ')'}); } }
  });
  // Cibles (2.5.8) : moins de 24 px de large ou de haut, hors exceptions.
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab],[role=link],[role=checkbox],[role=switch],[tabindex]:not([tabindex="-1"])';
  const cibles = [...document.querySelectorAll(sel)].filter(n => visible(n) && !n.closest('[inert]'));
  const boites = cibles.map(n => n.getBoundingClientRect());
  const petites = []; let exceptions = {phrase: 0, espacement: 0, natif: 0};
  cibles.forEach((n, i) => {
    const r = boites[i]; if (r.width >= 23.5 && r.height >= 23.5) return;
    const s = getComputedStyle(n);
    if (n.tagName === 'A' && s.display === 'inline') { const p = n.parentElement; const t = p ? [...p.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join('').trim() : '';
      if (t.length > 8) { exceptions.phrase++; return; } }
    if (n.tagName === 'INPUT' && ['checkbox', 'radio'].includes(n.type) && s.appearance !== 'none' && n.closest('label')) { exceptions.natif++; return; }
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const gene = boites.some((o, j) => { if (j === i || cibles[j].contains(n) || n.contains(cibles[j])) return false;
      const dx = Math.max(o.left - cx, 0, cx - o.right), dy = Math.max(o.top - cy, 0, cy - o.bottom);
      const petiteO = o.width < 23.5 || o.height < 23.5;
      if (petiteO) { const ox = o.left + o.width / 2, oy = o.top + o.height / 2; return Math.hypot(ox - cx, oy - cy) < 24; }
      return Math.hypot(dx, dy) < 12; });
    if (!gene) { exceptions.espacement++; return; }
    petites.push({el: nom(n), texte: (n.textContent || n.getAttribute('aria-label') || n.getAttribute('title') || n.value || '').trim().slice(0, 30),
                  l: Math.round(r.width), h: Math.round(r.height)});
  });
  return {textes_mesures: mesures, contraste_echecs: echecs.length, contraste_exemples: echecs.slice(0, 30), fonds_images: images,
          cibles: cibles.length, cibles_petites: petites.length, cibles_exemples: petites.slice(0, 30), cibles_exceptions: exceptions};
}"""

FOCUS_JS = """() => { const a = document.activeElement; if (!a || a === document.body) return null;
  const s = getComputedStyle(a); const r = a.getBoundingClientRect();
  const contour = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
  const ombre = s.boxShadow && s.boxShadow !== 'none';
  return {el: (a.tagName.toLowerCase() + (a.className && typeof a.className === 'string' ? '.' + a.className.trim().split(/\\s+/)[0] : '')).slice(0, 50),
          visible: !!(contour || ombre), dans_ecran: r.bottom > 0 && r.top < innerHeight}; }"""


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


def _ecrire(nom, valeur):
    MESURES[nom] = valeur
    if FICHIER.get('chemin'):     # écrit à chaque étape : un échec tardif ne perd pas les relevés déjà faits
        Path(FICHIER['chemin']).write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')


def _etape(texte):
    print('    · %s' % texte, flush=True)


def _phase(s, nom, fonction, *args):
    """Une phase de mesure ; si la page du navigateur tombe, l'erreur est notée et une page neuve reprend la suite."""
    _etape(nom)
    try:
        _ecrire(nom, fonction(*args))
    except Exception as ex:   # noqa: BLE001 — relevé partiel plutôt que rien
        _ecrire(nom, {'erreur': str(ex)[:300]})
        if 'crash' in str(ex).lower() or s.page.is_closed():
            p = s.context.new_page()
            p.on('pageerror', s._erreur_js)
            p.on('console', s._console)
            s.page = p
            p.add_init_script(INIT_PERF)


# ------------------------------------------------------------------------------------------------ données fictives
def _semer(banc):
    """Courriels relevés, dix projets à relire, un livrable : les cinq accès ont de quoi s'afficher."""
    sys.path.insert(0, str(ROOT / 'scripts'))
    nav = _nav()
    nav._semer_courriels96(banc)
    import parcours_p100
    parcours_p100.semer_propositions(banc, 10)
    return banc.semer_livrable()


# ------------------------------------------------------------------------------------------------ U02 : navigation
def _naviguer(s, chemin, page=None, url=None):
    """Une navigation ; ``page`` : page d'un contexte de mesure SANS interception (l'interception de requêtes de Playwright coupe le cache
    HTTP de Chromium : une mesure « chaude » y serait fausse). Attente : événement « load », puis 1,5 s pour les scripts différés."""
    p = page or s.page
    debut = time.perf_counter()
    if page is None:
        rep = s.aller(chemin)
    else:
        rep = p.goto(url + chemin, wait_until='load')
    ms = round((time.perf_counter() - debut) * 1000)
    p.wait_for_timeout(1500 if page is not None else 300)
    m = p.evaluate(PERF_JS)
    m['statut'] = rep.status if rep else None
    m['ms_total_banc'] = ms
    return m


def _mediane(valeurs):
    v = sorted(x for x in valeurs if isinstance(x, (int, float)))
    return v[len(v) // 2] if v else None


def _contexte_mesure(banc, s):
    """Contexte de mesure à part, sans interception (cache HTTP réel) ; les requêtes vers une autre adresse que le banc sont relevées."""
    ctx = s.context.browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='fr-FR')
    externes = []
    hote = banc.origine
    ctx.on('request', lambda r: externes.append(r.url[:200]) if not r.url.startswith((hote, 'data:', 'blob:', 'about:')) else None)
    p = ctx.new_page()
    p.add_init_script(INIT_PERF)
    p.goto(banc.url + '/connexion', wait_until='load')
    p.fill('#cx48-ident', banc.ident)
    p.fill('#cx48-pass', banc.mot_de_passe)
    with p.expect_navigation():
        p.click('form.cx48-form button[type=submit]')
    p.wait_for_load_state('load')
    return ctx, p, externes


def mesurer_navigation(banc, s, cdp):
    ctx, p, externes = _contexte_mesure(banc, s)
    cdp = ctx.new_cdp_session(p)
    out = {}
    for cle, chemin in ACCES:
        _etape('navigation %s' % chemin)
        cdp.send('Network.clearBrowserCache')
        froide = _naviguer(s, chemin, p, banc.url)
        chaudes = [_naviguer(s, chemin, p, banc.url) for _ in range(3)]
        out[cle] = {'froide': froide, 'chaude': {k: _mediane([c[k] for c in chaudes]) for k in ('ttfb', 'dcl', 'charge', 'ressources', 'transfert_ko',
                                                                                              'depuis_cache', 'tbt', 'taches_longues', 'memoire_js_ko', 'ms_total_banc')},
                    'chaude_server_timing': chaudes[-1]['server_timing']}
    # retour arrière : Dossiers → Aujourd'hui → retour
    retours = []
    for _ in range(3):
        p.goto(banc.url + '/dossiers', wait_until='load')
        p.goto(banc.url + '/aujourdhui', wait_until='load')
        debut = time.perf_counter()
        p.go_back(wait_until='load')
        ms = round((time.perf_counter() - debut) * 1000)
        etat = p.evaluate("() => ({type: (performance.getEntriesByType('navigation')[0] || {}).type, url: location.pathname})")
        retours.append({'ms': ms, **etat})
    out['retour_arriere'] = {'ms_mediane': _mediane([r['ms'] for r in retours]), 'essais': retours}
    out['requetes_externes'] = externes[:5]
    ctx.close()
    verifier(not externes, 'P111u navigation : requête vers une autre adresse que le banc : %s' % externes[:3])
    return out


# ------------------------------------------------------------------------------------------------ U02 : faux services lents
class _Lent:
    """Enveloppe d'une fonction du banc : pause, et compte des appels faits pendant qu'une page est demandée."""

    def __init__(self, fonction, pause):
        self.fonction, self.pause, self.appels, self.verrou = fonction, pause, 0, threading.Lock()

    def __call__(self, *a, **kw):
        with self.verrou:
            self.appels += 1
        time.sleep(self.pause)
        return self.fonction(*a, **kw)


def mesurer_services_lents(banc, s):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import parcours_p106l
    from agent import dav
    serveur = parcours_p106l.preparer_messagerie(banc, n=40)
    from agent import mailbox as mbx
    imap = _Lent(mbx.connect, 3.0)
    mbx.connect = imap
    enveloppes = {}
    for nom in ('list_folder', 'inventory', 'inventory_page', 'stat', 'download', 'folder_etag'):
        if hasattr(dav.LocalFolder, nom):
            enveloppes[nom] = getattr(dav.LocalFolder, nom)
    stockage = _Lent(lambda f, *a, **kw: f(*a, **kw), 1.5)

    def envelopper(nom, origine):
        def f(self, *a, **kw):
            return stockage(origine, self, *a, **kw)
        return f
    for nom, origine in enveloppes.items():
        setattr(dav.LocalFolder, nom, envelopper(nom, origine))
    out = {}
    ctx, page, externes = _contexte_mesure(banc, s)
    try:
        for cle, chemin in ACCES:
            imap.appels = stockage.appels = 0
            m = _naviguer(s, chemin, page, banc.url)
            m['statut_dom'] = page.evaluate("() => (document.querySelector('#ws-main') || document.body).innerText.length")
            out[cle] = {'ms_total_banc': m['ms_total_banc'], 'ttfb': m['ttfb'], 'server_timing': m['server_timing'],
                        'appels_imap_pendant_la_page': imap.appels, 'lectures_stockage_pendant_la_page': stockage.appels}
            # contenu local affiché sans attendre : la zone principale porte du texte
            out[cle]['texte_principal'] = m['statut_dom']
            out[cle]['charge'] = m['charge']
            out[cle]['imap_dans_la_page'] = m['server_timing'].get('imap')
    finally:
        ctx.close()
        for nom, origine in enveloppes.items():
            setattr(dav.LocalFolder, nom, origine)
        parcours_p106l.restaurer_messagerie(banc)
        del serveur
    return out


def mesurer_ia_lourde(banc, s, doc):
    """Faux modèle à 20 s par réponse, travail exécuté par la file dans un fil du banc ; pendant ce temps, lectures des cinq accès."""
    nav = _nav()
    origine = banc.modele.reponse

    def lente(corps):
        time.sleep(20.0)
        return origine(corps)
    banc.modele.reponse = lente
    out = {}
    try:
        nav._proposition(s, doc)
        ctx, page, externes = _contexte_mesure(banc, s)
        fil = threading.Thread(target=banc.travailler, daemon=True)
        debut = time.perf_counter()
        fil.start()
        time.sleep(1.0)
        for cle, chemin in ACCES:
            m = _naviguer(s, chemin, page, banc.url)
            out[cle] = {'ms_total_banc': m['ms_total_banc'], 'ttfb': m['ttfb'], 'charge': m['charge'], 'server_total': m['server_timing'].get('total'),
                        'pendant_travail': fil.is_alive()}
        ctx.close()
        fil.join(90)
        out['duree_travail_s'] = round(time.perf_counter() - debut, 1)
        out['appels_modele'] = banc.modele.nombre()
    finally:
        banc.modele.reponse = origine
    return out


# ------------------------------------------------------------------------------------------------ U02 : accessibilité
def _focus(p, arrets=25):
    p.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    sans, vus = [], 0
    for _ in range(arrets):
        p.keyboard.press('Tab')
        f = p.evaluate(FOCUS_JS)
        if not f:
            continue
        vus += 1
        if not f['visible']:
            sans.append(f['el'])
    return {'arrets': vus, 'sans_focus_visible': len(sans), 'exemples': sorted(set(sans))[:10]}


def mesurer_accessibilite(banc, s, pages=PAGES_A11Y):
    ctx, p, externes = _contexte_mesure(banc, s)
    erreurs = []
    p.on('pageerror', lambda ex: erreurs.append(str(ex)[:200]))

    class Aller:
        @staticmethod
        def aller(chemin):
            p.goto(banc.url + chemin, wait_until='load')
            p.wait_for_timeout(700)
    out = {}
    s = Aller
    for theme in ('light', 'dark'):
        p.emulate_media(color_scheme=theme)
        p.set_viewport_size({'width': 1920, 'height': 1080})
        for cle, chemin in pages:
            _etape('accessibilité %s %s' % (theme, chemin))
            s.aller(chemin)
            p.wait_for_timeout(250)
            a = p.evaluate(AUDIT_JS)
            if theme == 'light':
                a['focus'] = _focus(p)
            out.setdefault(cle, {})[theme] = a
    p.emulate_media(color_scheme='light')
    for cle, chemin in pages:
        res = {}
        for nom, (l, h) in (('1536x864', (1536, 864)), ('375', (375, 812))):
            p.set_viewport_size({'width': l, 'height': h})
            s.aller(chemin)
            p.wait_for_timeout(200)
            res[nom] = p.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        out[cle]['largeurs'] = res
    ctx.close()
    out['_erreurs_js'] = erreurs[:5]
    out['_requetes_externes'] = externes[:5]
    return out


def p111u_mesures(banc, s):
    doc = _semer(banc)
    s.connecter()
    p = s.page
    p.add_init_script(INIT_PERF)
    p.set_viewport_size({'width': 1920, 'height': 1080})
    cdp = s.context.new_cdp_session(p)
    _ecrire('machine', {'systeme': sys.platform, 'processeurs': os.cpu_count(), 'navigateur': s.context.browser.browser_type.name + ' ' + s.context.browser.version,
                        'fenetre': '1920x1080', 'serveur': 'waitress 8 fils, 127.0.0.1, banc local'})
    _phase(s, 'navigation', mesurer_navigation, banc, s, cdp)
    s.propre('P111u navigation')
    _phase(s, 'services_lents', mesurer_services_lents, banc, s)
    s.erreurs.clear()          # erreurs attendues éventuelles d'une messagerie lente (fragments en délai) : relevées dans le rapport
    _phase(s, 'ia_lourde', mesurer_ia_lourde, banc, s, doc)
    s.erreurs.clear()
    _phase(s, 'accessibilite', mesurer_accessibilite, banc, s)
    s.erreurs.clear()


# ------------------------------------------------------------------------------------------------ U01 : relecture
BARRE_DANS_LA_VUE = """() => { const b = document.querySelector('[data-ax100-actions]'); if (!b) return null; const r = b.getBoundingClientRect();
  const boutons = [...b.querySelectorAll('[data-action]')].filter(x => !x.hidden && x.getBoundingClientRect().width > 0);
  const couverts = boutons.filter(x => { const q = x.getBoundingClientRect(); const e = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2);
    return q.bottom > innerHeight + 1 || q.top < 0 || !e || !x.contains(e); }).map(x => x.getAttribute('data-action'));
  return {haut: Math.round(r.top), bas: Math.round(r.bottom), h: innerHeight, defile_page: Math.round(document.scrollingElement.scrollTop), couverts: couverts,
          boutons: boutons.map(x => x.getAttribute('data-action'))}; }"""
ETAT_LISTE = """(z) => { const f = document.querySelector('[data-ax100-filtre][aria-current]');
  return {url: location.search, pos: document.querySelector(z).scrollTop, filtre: f ? f.getAttribute('data-ax100-filtre') : '',
          lignes: document.querySelectorAll('[data-ax100-lignes] li[data-review-id]').length}; }"""


def p111u_relecture(banc, s):
    """Dix projets relus d'affilée (édition à chaque fois), filtre « Propositions » et position de la liste gardés, barre visible à 1920 × 1080
    et 1536 × 864, « Envoyer… » distinct ; retour arrière avec le même filtre ; aucun texte perdu (relu en base)."""
    sys.path.insert(0, str(ROOT / 'scripts'))
    import parcours_p100
    parcours_p100.semer_propositions(banc, 30, dossiers={i: ('DOS-002' if i % 3 == 0 else 'DOS-001') for i in range(30)})
    p = s.page
    s.connecter()
    out = {}
    for l, h in ((1920, 1080), (1536, 864)):
        p.set_viewport_size({'width': l, 'height': h})
        s.aller('/a-relire?source=proposition')
        p.locator('[data-ax100-lignes] li[data-review-id] a').nth(3).click()
        p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value.length > 20 && !t.readOnly; }", timeout=10000)
        m = p.evaluate(BARRE_DANS_LA_VUE)
        out['barre_%dx%d' % (l, h)] = m
        verifier(m and m['bas'] <= m['h'] + 1 and m['haut'] >= 0 and m['defile_page'] == 0 and not m['couverts'],
                 'P111u relecture : commandes de décision hors de la vue à %d × %d : %s' % (l, h, m))
        verifier({'enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'} <= set(m['boutons']), 'P111u relecture : commandes absentes : %s' % m['boutons'])
        s.capture(_sortie(banc) / ('P111u-relecture-%d.png' % l))
    # effets distincts : Retenir, Déposer dans Brouillons (sans envoi), Envoyer… (après dépôt, sur le brouillon)
    note = p.locator('[data-u111-envoi-note]')
    verifier(note.count() == 1 and 'Déposer dans Brouillons' in note.inner_text(), 'P111u relecture : chemin d’envoi non visible dans le lecteur')
    libelles = {a: p.locator('[data-ax100-actions] [data-action="%s"]' % a).inner_text().strip() for a in ('retenir', 'deposer')}
    out['libelles'] = libelles
    verifier(libelles['deposer'] == 'Déposer dans Brouillons (sans envoi)' and libelles['retenir'] == 'Retenir', 'P111u relecture : libellés inattendus : %s' % libelles)
    # dix décisions d'affilée, filtre et position gardés
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/a-relire?source=proposition')
    zone = '[data-ax100-zone-liste]'
    p.evaluate("(z) => { const n = document.querySelector(z); n.scrollTop = Math.min(n.scrollHeight, 400); }", zone)
    p.wait_for_timeout(200)
    position = p.evaluate("(z) => document.querySelector(z).scrollTop", zone)
    visible = p.evaluate("""(z) => { const n = document.querySelector(z), r = n.getBoundingClientRect();
      return [...n.querySelectorAll('li[data-review-id]')].findIndex(li => li.getBoundingClientRect().top >= r.top); }""", zone)
    p.locator('[data-ax100-lignes] li[data-review-id] a').nth(max(0, visible)).click()
    navigations = p.evaluate("performance.getEntriesByType('navigation').length")
    ajouts, journal = [], []
    for n in range(10):
        p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); const b = document.querySelector('[data-ax100-actions] [data-action=\"retenir\"]');"
                            " return t && t.value.length > 20 && !t.readOnly && b && !b.disabled; }", timeout=10000)
        ident = p.evaluate("document.querySelector('[data-ax100-lecteur]').getAttribute('data-id')")
        sujet = p.locator('[data-ax100-sujet]').first.inner_text()
        z = p.locator('[data-ax100-texte]')
        z.click()
        z.press('Control+End')
        texte = ' Relu par l’avocat, passage %d.' % (n + 1)
        z.type(texte)
        ajouts.append((ident, texte.strip()))
        p.locator('[data-ax100-actions] [data-action="retenir"]').click()
        if n < 9:
            p.wait_for_function("(s) => { const h = document.querySelector('[data-ax100-sujet]'); return h && h.textContent.trim() && h.textContent !== s; }",
                                arg=sujet, timeout=10000)
        else:
            p.wait_for_timeout(800)
        etat = p.evaluate(ETAT_LISTE, zone)
        journal.append(etat)
        verifier('source=proposition' in etat['url'] and etat['filtre'] == 'proposition', 'P111u relecture : filtre perdu après la décision %d : %s' % (n + 1, etat))
        verifier(etat['pos'] > 0 or position == 0, 'P111u relecture : position de la liste revenue en haut après la décision %d : %s' % (n + 1, etat))
    verifier(p.evaluate("performance.getEntriesByType('navigation').length") == navigations, 'P111u relecture : page rechargée entre deux décisions')
    out['position_initiale'] = position
    out['journal'] = journal
    with banc.desk() as d:
        perdus = []
        for ident, texte in ajouts:
            row = d.db.execute('SELECT mail_key FROM file56100_items WHERE id=?', (ident,)).fetchone()
            data = d.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (row[0],)).fetchone() if row else None
            if not data or texte not in json.loads(data[0]).get('result', {}).get('body', ''):
                perdus.append(ident)
        restants = d.db.execute("SELECT COUNT(*) FROM file56100_items WHERE etat='a_relire' AND kind='proposition'").fetchone()[0]
    out['textes_perdus'] = perdus
    out['restants'] = restants
    verifier(not perdus, 'P111u relecture : texte perdu pour %s' % perdus)
    verifier(restants == 20, 'P111u relecture : %d propositions encore à relire (20 attendues)' % restants)
    # retour : autre page puis Précédent
    s.aller('/aujourdhui')
    p.go_back(wait_until='load')
    p.wait_for_load_state('networkidle')
    p.wait_for_timeout(500)
    retour = p.evaluate(ETAT_LISTE, zone)
    out['retour_arriere'] = retour
    verifier(retour['pos'] > 0, 'P111u relecture : position de la liste perdue au retour : %s' % retour)
    verifier('source=proposition' in retour['url'] and retour['filtre'] == 'proposition' and retour['lignes'] == 20, 'P111u relecture : retour sans le filtre : %s' % retour)
    s.propre('P111u relecture')
    _ecrire('relecture', out)


# ------------------------------------------------------------------------------------------------ U01 : accueil, dossiers, bibliothèque
def p111u_accueil(banc, s):
    nav = _nav()
    nav._accueil96(banc, meteo=False)
    sys.path.insert(0, str(ROOT / 'scripts'))
    import parcours_p100
    parcours_p100.semer_propositions(banc, 3, debut=80)
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/aujourdhui')
    out = {}
    ordre = p.evaluate("""() => ['#f96-bandeau', '#ax95-actuellement', '.f96-rang', '#a89-travail'].map(s => { const n = document.querySelector(s);
        return n ? Math.round(n.getBoundingClientRect().top + scrollY) : null; })""")
    out['ordre_y'] = ordre
    verifier(all(v is not None for v in ordre) and ordre == sorted(ordre), 'P111u accueil : ordre bonjour, Actuellement, rang, travaux inattendu : %s' % ordre)
    g, j = p.locator('.f96-rang > .f98-gauche').bounding_box(), p.locator('.f96-journee').bounding_box()
    out['decider_journee'] = [g, j]
    verifier(g and j and abs(g['y'] - j['y']) < 40 and j['x'] > g['x'] + g['width'] - 2, 'P111u accueil : À décider n’est pas à côté de Ma journée')
    act = p.locator('#ax95-actuellement').bounding_box()
    out['actuellement_hauteur'] = act['height']
    verifier(act['height'] < 260, 'P111u accueil : « Actuellement » n’est pas compact (%d px)' % act['height'])
    distinct = p.evaluate("""() => { const v = document.querySelector('#f96-lignes-zone .f100-a-verifier a, #f96-lignes-zone a[data-f96-cle="banc-ech-verifier"]'),
        c = document.querySelector('.f96-echeances a');
        if (!v || !c) return null; const a = getComputedStyle(v), b = getComputedStyle(c);
        return {verifier: [a.color, a.backgroundColor, a.fontStyle, a.textDecorationStyle, v.textContent.trim().slice(0, 60)],
                confirmee: [b.color, b.backgroundColor, b.fontStyle, b.textDecorationStyle, c.textContent.trim().slice(0, 60)]}; }""")
    out['dates'] = distinct
    verifier(distinct and distinct['verifier'][:4] != distinct['confirmee'][:4] and 'vérifier' in distinct['verifier'][4],
             'P111u accueil : date proposée non distincte : %s' % distinct)
    # un clic ouvre le lecteur commun ; la fermeture rend la position
    s.aller('/aujourdhui?vue=file')
    lien = p.locator('#f100-file a[data-f96-action="reponse"]').first
    if lien.count():
        lien.scroll_into_view_if_needed()
        avant = p.evaluate('scrollY')
        lien.click()
        p.wait_for_selector('dialog.f96-fenetre[open]', timeout=15000)
        p.keyboard.press('Escape')
        p.wait_for_timeout(500)
        apres = p.evaluate('scrollY')
        out['position_fermeture'] = [avant, apres, p.locator('dialog.f96-fenetre[open]').count()]
        verifier(abs(apres - avant) < 4, 'P111u accueil : position perdue à la fermeture du lecteur : %s → %s' % (avant, apres))
    s.propre('P111u Aujourd’hui')
    # Dossiers : liste et fiche exposent responsable, prochaine action, résultats de l'IA, pièces sources
    s.aller('/dossiers')
    entetes = p.locator('table.d97-table thead th').all_inner_texts()
    out['colonnes_dossiers'] = entetes
    verifier(any('RESPONSABLE' in x.upper() for x in entetes) and any('PROCHAINE ACTION' in x.upper() for x in entetes), 'P111u dossiers : colonnes absentes : %s' % entetes)
    s.aller('/dossier?id=DOS-001')
    fiche = p.locator('#ws-main').text_content()          # texte brut (les intitulés peuvent être mis en capitales par la feuille)
    out['fiche'] = {k: (k in fiche) for k in ('Responsable', 'Prochaine action', 'Derniers résultats de l’IA', 'Pièces sources')}
    verifier(all(out['fiche'].values()), 'P111u dossiers : fiche incomplète : %s' % out['fiche'])
    s.propre('P111u Dossiers')
    # Corporate › Bibliothèque à 375 px
    for l, h in ((375, 812), (1536, 864), (1920, 1080)):
        p.set_viewport_size({'width': l, 'height': h})
        s.aller('/corporate?vue=bibliotheque')
        m = p.evaluate("""() => { const main = document.querySelector('.c5636-main'), it = document.querySelector('.b5639-item');
            const deb = [...document.querySelectorAll('#ws-main *')].filter(n => { const r = n.getBoundingClientRect(); return r.width && r.right > innerWidth + 1; })
              .slice(0, 5).map(n => n.tagName.toLowerCase() + '.' + String(n.className).split(' ')[0] + ':' + Math.round(n.getBoundingClientRect().right));
            return {page: [document.documentElement.scrollWidth, document.documentElement.clientWidth], principal: main ? Math.round(main.getBoundingClientRect().width) : null,
                    carte: it ? Math.round(it.getBoundingClientRect().width) : null, debordent: deb}; }""")
        out['bibliotheque_%d' % l] = m
        verifier(m['page'][0] <= m['page'][1] + 1 and not m['debordent'], 'P111u bibliothèque : défilement horizontal à %d px : %s' % (l, m))
        verifier(l > 700 or (m['carte'] and m['principal'] and m['carte'] >= 0.85 * m['principal']), 'P111u bibliothèque : colonne étroite à %d px : %s' % (l, m))
        if l == 375:
            s.capture(_sortie(banc) / 'P111u-bibliotheque-375.png')
    # matrice de couverture (lot K) : à 375 px, cartes lisibles, aucune colonne étroite, rien à faire défiler de côté
    p.set_viewport_size({'width': 375, 'height': 812})
    s.aller('/corporate?vue=bibliotheque&matrice=1')
    m = p.evaluate("""() => { const z = document.querySelector('.b111-defile'), t = document.querySelector('.b111-table');
        const cases = [...document.querySelectorAll('.b111-table tbody td')].map(n => Math.round(n.getBoundingClientRect().width));
        const col = document.querySelector('.b111-table .u111-col');
        return {zone: z ? [z.scrollWidth, z.clientWidth, getComputedStyle(z).overflowX] : null, case_min: Math.min(...cases), case_max: Math.max(...cases),
                etiquette: col ? [getComputedStyle(col).display, col.textContent] : null, page: [document.documentElement.scrollWidth, document.documentElement.clientWidth]}; }""")
    out['matrice_375'] = m
    verifier(m['zone'] and m['zone'][0] <= m['zone'][1] + 1 and m['page'][0] <= m['page'][1] + 1, 'P111u matrice : défilement horizontal à 375 px : %s' % m)
    verifier(m['case_min'] >= 250 and m['etiquette'] and m['etiquette'][0] == 'block' and '·' in m['etiquette'][1], 'P111u matrice : colonne étroite à 375 px : %s' % m)
    s.capture(_sortie(banc) / 'P111u-matrice-375.png')
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.aller('/corporate?vue=bibliotheque&matrice=1')
    verifier(p.evaluate("getComputedStyle(document.querySelector('.b111-table .u111-col')).display") == 'none', 'P111u matrice : étiquettes visibles à 1920 px')
    s.propre('P111u bibliothèque')
    _ecrire('accueil', out)


# ------------------------------------------------------------------------------------------------ B6 : précontrôle
def _deplier(p):
    p.evaluate("() => { const c = document.querySelector('#precontrole56111'); const d = c && c.closest('details'); if (d) d.open = true; }")


def p111u_precontrole(banc, s):
    from agent import precontrole56111 as P
    moteur = Path(banc.base) / 'moteur' / 'dictee'
    moteur.mkdir(parents=True)
    for nom in ('whisper-cli.exe', 'whisper-cli'):
        (moteur / nom).write_bytes(b'faux moteur du banc')
    ancien_env, ancien = os.environ.get('AXIORHUB_DICTEE'), P._lancer_essai
    appels = []

    def faux(args, cwd, delai=P.DELAI):
        appels.append(list(args))
        return 0xC0000135 if os.name == 'nt' else 127      # composant Microsoft (Windows) ou libgomp1 (Ubuntu) simulés
    os.environ['AXIORHUB_DICTEE'] = str(moteur)
    P._lancer_essai = faux
    out = {}
    try:
        p = s.page
        p.set_viewport_size({'width': 1920, 'height': 1080})
        s.connecter()
        s.aller('/parametres?rubrique=voix')
        carte = p.locator('#precontrole56111')
        verifier(carte.count() == 1, 'P111u précontrôle : carte absente de Paramètres › Voix')
        _deplier(p)
        verifier(not appels, 'P111u précontrôle : le moteur a été lancé à l’affichage')
        p.wait_for_function("() => { const z = document.querySelector('[data-u111-micro-etat]'); return z && z.textContent.indexOf('Lecture') !== 0; }", timeout=5000)
        out['micro'] = p.inner_text('[data-u111-micro-etat]')
        with p.expect_response(lambda r: 'm56111/precontrole/verifier' in r.url, timeout=15000) as rep:
            carte.locator('button[type=submit]').click()
        verifier(rep.value.status == 200, 'P111u précontrôle : vérification refusée (%d)' % rep.value.status)
        p.wait_for_timeout(1500)                       # rechargement de la page par le traitement commun des formulaires (data-reload)
        p.wait_for_load_state('networkidle')
        _deplier(p)
        texte = p.inner_text('#precontrole56111')
        out['texte'] = texte[:1500]
        out['appels'] = appels
        verifier(len(appels) == 1 and appels[0][-1] == '-h', 'P111u précontrôle : lancement inattendu : %s' % appels)
        if os.name == 'nt':
            verifier('Visual C++' in texte and '0xC0000135' in texte, 'P111u précontrôle : composant Microsoft non signalé : %s' % texte[:400])
            verifier(p.locator('#precontrole56111 a[href^="https://aka.ms/"]').count() == 1, 'P111u précontrôle : lien officiel absent')
        else:
            verifier('libgomp1' in texte and 'code 127' in texte, 'P111u précontrôle : libgomp1 non signalée')
        verifier('non installé' in texte, 'P111u précontrôle : modèle absent non signalé')
        s.capture(_sortie(banc) / 'P111u-precontrole-1920.png')
        p.emulate_media(color_scheme='dark')
        p.set_viewport_size({'width': 375, 'height': 812})
        s.aller('/parametres?rubrique=voix')
        _deplier(p)
        verifier(p.evaluate(SANS_DEFILEMENT), 'P111u précontrôle : défilement horizontal à 375 px')
        s.capture(_sortie(banc) / 'P111u-precontrole-375-sombre.png')
        p.emulate_media(color_scheme='light')
        p.set_viewport_size({'width': 1920, 'height': 1080})
        s.propre('P111u précontrôle')
    finally:
        P._lancer_essai = ancien
        if ancien_env is None:
            os.environ.pop('AXIORHUB_DICTEE', None)
        else:
            os.environ['AXIORHUB_DICTEE'] = ancien_env
    _ecrire('precontrole', out)


PARCOURS_U = (('P111u-mesures', 'Mesures : navigation froide/chaude, Server-Timing, tâches longues, mémoire, retour arrière, services lents, '
               'IA lourde, contrastes rendus, cibles, focus, 125 % et 375 px', p111u_mesures),
              ('P111u-relecture', 'À relire : dix projets d’affilée sans perte de texte, filtre et position gardés, barre visible à 1920 et 125 %, '
               'Envoyer distinct', p111u_relecture),
              ('P111u-accueil', 'Aujourd’hui (ordre, lecteur), Dossiers (responsable, action, IA, pièces), Bibliothèque à 375 px', p111u_accueil),
              ('P111u-precontrole', 'Précontrôle du poste : moteur, Visual C++ ou libgomp1 simulés, micro, lecteur ; 375 px sombre', p111u_precontrole))


def main(argv=None):
    """Lancement seul (sans inscription dans ``PARCOURS``) : banc chargé comme module, parcours ajoutés, mesures écrites dans la sortie."""
    global NAV
    import argparse
    import importlib.util
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours-p111u'))
    parser.add_argument('--navigateur', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--parcours', default=','.join(i for i, _, _ in PARCOURS_U))
    parser.add_argument('--phase', default='apres')
    args = parser.parse_args(argv)
    PHASE['nom'] = args.phase
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    FICHIER['chemin'] = str(Path(args.sortie) / ('P111u-mesures-%s.json' % args.phase))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(ROOT / 'scripts' / 'parcours-navigateur.py'))
    NAV = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NAV)
    if not any(ident == PARCOURS_U[0][0] for ident, _, _ in NAV.PARCOURS):
        NAV.PARCOURS = NAV.PARCOURS + PARCOURS_U
    code = NAV.lancer(args.navigateur, args.sortie, tuple(x for x in args.parcours.split(',') if x), continuer=True)
    Path(args.sortie).mkdir(parents=True, exist_ok=True)
    (Path(args.sortie) / ('P111u-mesures-%s.json' % args.phase)).write_text(json.dumps(MESURES, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in MESURES.items() if k != 'accessibilite'}, ensure_ascii=False, indent=1)[:6000])
    return code


if __name__ == '__main__':
    sys.exit(main())
