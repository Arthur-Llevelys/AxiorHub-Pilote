"""5.6.108 (lot W) : mesure de fidélité de WordCraft 0.18.1 (aller-retour par un vrai navigateur, Chromium de Playwright, sans réseau).

Pour chaque fichier .docx du jeu (trames de la bibliothèque produites par le moteur existant, modèles Word livrés avec AxiorHub, fichiers
fictifs de ``tests/fixtures_editeur56108.py``) :
  1. ouverture dans WordCraft (mode « page hôte », sous la CSP d'AxiorHub), enregistrement sans modification → ``<nom>.rt0.docx`` ;
  2. petite modification (« [essai] » tapé au début), enregistrement → ``<nom>.rt1.docx`` ;
  3. comparaison avec l'original par ``wordcraft56108.analyser`` / ``controler`` (le contrôle même qui s'affiche à l'avocat).
Un petit serveur local (127.0.0.1, port libre) sert WordCraft et la page d'essai ; chaque requête est journalisée et toute requête du
navigateur vers une autre adresse est comptée (audit réseau : zéro attendu).

Sortie : ``<sortie>/fidelite.json`` (résultats bruts) et un tableau Markdown imprimé (repris dans docs/WORDCRAFT-FIDELITE-5.6.108.md).
Lancement : ``python scripts/fidelite_wordcraft56108.py --sortie <dossier neuf> [--limite N]``. Aucune donnée réelle : trames du produit et
fichiers fictifs seulement.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; "
       "media-src 'self' blob:; manifest-src 'self'; worker-src 'self'; form-action 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
TYPES = {'.wasm': 'application/wasm', '.js': 'text/javascript', '.html': 'text/html; charset=utf-8', '.css': 'text/css'}
PAGE = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Mesure WordCraft</title><link rel="stylesheet" href="essai.css">'
        '<script defer src="essai.js"></script></head><body><canvas id="wordcraft_canvas" data-host="page"></canvas></body></html>')
FEUILLE = 'html,body{margin:0;height:100%}#wordcraft_canvas{width:100%;height:100%;display:block}'
SCRIPT = r"""(function () {
  'use strict';
  var canvas = document.getElementById('wordcraft_canvas');
  var E = window.mesure = { pret: false, echec: '', saves: 0, csp: [], t0: performance.now() };
  window.open = function () { E.ouvertures = (E.ouvertures || 0) + 1; return null; };
  window.addEventListener('securitypolicyviolation', function (ev) { E.csp.push(ev.violatedDirective + ' ' + ev.blockedURI); });
  canvas.addEventListener('wordcraft:ready', function () { E.pret = true; E.tPret = performance.now() - E.t0; });
  canvas.addEventListener('wordcraft:failed', function (ev) { E.echec = String(ev.detail && ev.detail.error); });
  canvas.addEventListener('wordcraft:save', function (ev) {
    var d = ev.detail || {};
    E.dernierNom = d.name; E.saveAs = d.saveAs;
    fetch('recu/' + encodeURIComponent(E.cible || 'sans-nom'), { method: 'POST', body: d.bytes }).then(function () { E.saves += 1; });
  });
  E.ouvrir = function (nom) {
    return fetch('docs/' + encodeURIComponent(nom)).then(function (r) { return r.arrayBuffer(); }).then(function (b) {
      canvas.dispatchEvent(new CustomEvent('wordcraft:open', { detail: { name: nom, bytes: b } }));
      return b.byteLength;
    });
  };
  import('/wc/wordcraft-web-bc9cdbc0b42827ee.js').then(function (m) {
    return m.default({ module_or_path: '/wc/wordcraft-web-bc9cdbc0b42827ee_bg.wasm' });
  }).then(function () { E.tInit = performance.now() - E.t0; }, function (err) { E.echec = 'init ' + err; });
})();
"""


def port_libre():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def serveur(wc, h, journal):
    class G(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _envoyer(self, data, kind):
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            journal.append(('GET', self.path, 0))
            p = self.path.split('?', 1)[0]
            base, rel = (wc, p[4:]) if p.startswith('/wc/') else ((h, p[3:]) if p.startswith('/h/') else (None, ''))
            from urllib.parse import unquote
            rel = unquote(rel)
            if base is None or '..' in rel.split('/'):
                self.send_error(404)
                return
            f = Path(base, *rel.split('/'))
            if not f.is_file():
                self.send_error(404)
                return
            self._envoyer(f.read_bytes(), TYPES.get(f.suffix.lower(), 'application/octet-stream'))

        def do_POST(self):
            from urllib.parse import unquote
            n = int(self.headers.get('Content-Length') or 0)
            data = self.rfile.read(n)
            journal.append(('POST', self.path, len(data)))
            if self.path.startswith('/h/recu/'):
                nom = unquote(self.path[len('/h/recu/'):])
                if '/' not in nom and '\\' not in nom and '..' not in nom:
                    (Path(h) / 'recus' / nom).write_bytes(data)
            self.send_response(204)
            self.end_headers()

    port = port_libre()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), G)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def corpus(dossier, limite=None):
    """Jeu de mesure : {nom: (famille, octets)}."""
    from agent import bibliotheque5639 as B
    import fixtures_editeur56108 as FX
    out = {}
    for ident, t in sorted(B.BUILTIN.items()):
        out['trame-%s.docx' % ident] = ('trame', B.template_docx(t))
        try:   # acte produit par le moteur (valeurs vides : mentions « à compléter »), comme « Préparer » sans modèle Word du cabinet
            paras = B.render(t, B.values_for(t, {}), B.flags_for(t, {}))
            out['acte-%s.docx' % ident] = ('acte', B.build_docx(paras, t['langue'], systeme=t.get('droit', '')))
        except Exception:   # noqa: BLE001 — trame à options obligatoires : la trame seule est mesurée
            pass
    for f in sorted(list((ROOT / 'agent' / 'data' / 'modeles5653').glob('*.docx')) + list((ROOT / 'templates').glob('*.docx')) +
                    [ROOT / 'tests' / 'fixture-modele-fictif.docx']):
        out['modele-%s' % f.name] = ('modele', f.read_bytes())
    for nom, options in FX.JEU.items():
        out[nom + '.docx'] = ('fictif', FX.docx_fictif(*options))
    if limite:
        noms = sorted(out)
        garde = [n for n in noms if not n.startswith(('trame-', 'acte-'))] + [n for n in noms if n.startswith(('trame-', 'acte-'))][:limite]
        out = {n: out[n] for n in garde}
    for nom, (_, data) in out.items():
        (Path(dossier) / 'docs' / nom).write_bytes(data)
    return out


def attendre(page, expr, secondes=60.0, pas=0.15):
    fin = time.time() + secondes
    while time.time() < fin:
        v = page.evaluate(expr)
        if v:
            return v
        time.sleep(pas)
    return None


def etat_doc(page):
    return page.evaluate("wordcraft.docInspect().then(function (d) { return JSON.stringify(d); }, function (e) { return 'ERR ' + e; })")


def mesurer(page, nom, recus, precedent):
    """Ouvre ``nom``, attend le document, enregistre (rt0), tape « [essai] », enregistre (rt1). Rend (état, durée d'ouverture)."""
    t = time.time()
    page.evaluate('mesure.ouvrir(%s)' % json.dumps(nom))
    stable, vu = 0, None
    fin = time.time() + 90
    while time.time() < fin:
        s = etat_doc(page)
        if s and not s.startswith('ERR') and s != precedent and s == vu:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        vu = s
        time.sleep(0.25)
    ouverture = time.time() - t
    resultats = {}
    for etape, action in (('rt0', None), ('rt1', "wordcraft.execute('text.insert', {text: '[essai] '})")):
        if action:
            try:
                page.evaluate(action)
            except Exception as ex:   # noqa: BLE001
                resultats[etape] = 'insertion refusée : %s' % str(ex)[:120]
                continue
            time.sleep(0.3)
        avant = page.evaluate('mesure.saves')
        page.evaluate('mesure.cible = %s' % json.dumps(nom + '.' + etape + '.docx'))
        try:
            page.evaluate("wordcraft.execute('file.save', {})")
        except Exception as ex:   # noqa: BLE001
            resultats[etape] = 'enregistrement refusé : %s' % str(ex)[:120]
            continue
        ok = attendre(page, 'mesure.saves > %d' % avant, 60)
        resultats[etape] = 'ok' if ok and (Path(recus) / (nom + '.' + etape + '.docx')).is_file() else 'aucun enregistrement reçu'
    return resultats, ouverture, vu


def analyser_resultats(dossier, jeu):
    from agent import wordcraft56108 as WC
    lignes = []
    for nom, (famille, data) in sorted(jeu.items()):
        avant = WC.analyser(data)
        ligne = {'nom': nom, 'famille': famille, 'avant': {k: v for k, v in avant.items() if k not in ('textes', 'textes_entetes')}}
        for etape in ('rt0', 'rt1'):
            f = Path(dossier) / 'recus' / (nom + '.' + etape + '.docx')
            if not f.is_file():
                ligne[etape] = {'erreur': 'absent'}
                continue
            try:
                apres = WC.analyser(f.read_bytes())
            except Exception as ex:   # noqa: BLE001
                ligne[etape] = {'erreur': 'illisible : %s' % ex}
                continue
            r = WC.controler(avant, apres)
            ligne[etape] = {'pertes': r['pertes'], 'texte': {k: v for k, v in r['texte'].items() if k != 'details'},
                            'details': r['texte']['details'][:5], 'identique': r['identique'], 'taille': f.stat().st_size,
                            'apres': {k: v for k, v in apres.items() if k not in ('textes', 'textes_entetes')}}
        lignes.append(ligne)
    return lignes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sortie', required=True)
    ap.add_argument('--limite', type=int, default=0)
    ap.add_argument('--wordcraft', default=str(ROOT / 'agent' / 'static' / 'wordcraft-0.18.1'))
    ap.add_argument('--reprendre', default='', help='dossier de travail d’une mesure déjà faite : seule la comparaison est refaite')
    a = ap.parse_args()
    sortie = Path(a.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    if a.reprendre:
        h = Path(a.reprendre)
        familles = {'trame-': 'trame', 'acte-': 'acte', 'modele-': 'modele', 'fictif-': 'fictif'}
        jeu = {f.name: (next(v for k, v in familles.items() if f.name.startswith(k)), f.read_bytes()) for f in sorted((h / 'docs').glob('*.docx'))}
        ancien = json.loads((sortie / 'fidelite.json').read_text(encoding='utf-8')) if (sortie / 'fidelite.json').is_file() else {}
        mesures = {l['nom']: {k: l[k] for k in ('etapes', 'ouverture_s') if k in l} for l in ancien.get('fichiers', [])}
        lignes = analyser_resultats(h, jeu)
        for l in lignes:
            l.update(mesures.get(l['nom'], {}))
        ancien['fichiers'] = lignes
        (sortie / 'fidelite.json').write_text(json.dumps(ancien, ensure_ascii=False, indent=1), encoding='utf-8')
        print('comparaison refaite :', len(lignes), 'fichiers')
        return
    h = Path(tempfile.mkdtemp(prefix='travail-', dir=str(sortie)))
    (h / 'docs').mkdir()
    (h / 'recus').mkdir()
    (h / 'index.html').write_text(PAGE, encoding='utf-8')
    (h / 'essai.css').write_text(FEUILLE, encoding='utf-8')
    (h / 'essai.js').write_text(SCRIPT, encoding='utf-8')
    jeu = corpus(h, a.limite or None)
    journal = []
    srv, port = serveur(a.wordcraft, str(h), journal)
    externes, console = [], []
    resultats = {}
    from playwright.sync_api import sync_playwright
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        page = b.new_page(viewport={'width': 1400, 'height': 900})
        page.on('request', lambda r: externes.append(r.url) if not r.url.startswith('http://127.0.0.1:%d/' % port) else None)
        page.on('console', lambda m: console.append(m.type + ': ' + m.text[:200]) if m.type in ('error', 'warning') else None)
        debut = time.time()
        page.goto('http://127.0.0.1:%d/h/index.html' % port)
        attendre(page, '!!(window.mesure && (mesure.pret || mesure.echec))', 120)
        premier = attendre(page, "typeof window.wordcraft === 'object' && !!window.wordcraft.docInspect", 60)
        demarrage = round(time.time() - debut, 2)
        info = json.loads(page.evaluate('JSON.stringify({pret: mesure.pret, echec: mesure.echec, tPret: mesure.tPret, tInit: mesure.tInit, csp: mesure.csp})'))
        precedent = etat_doc(page)
        for i, nom in enumerate(sorted(jeu)):
            r, ouverture, precedent = mesurer(page, nom, h / 'recus', precedent)
            resultats[nom] = {'etapes': r, 'ouverture_s': round(ouverture, 2)}
            print('%3d/%d %-60s %s %.1fs' % (i + 1, len(jeu), nom, r, ouverture), flush=True)
        page.screenshot(path=str(sortie / 'capture-fin.png'))
        csp = page.evaluate('mesure.csp')
        ouvertures = page.evaluate('mesure.ouvertures || 0')
        b.close()
    srv.shutdown()
    lignes = analyser_resultats(h, jeu)
    for l in lignes:
        l.update(resultats.get(l['nom'], {}))
    poids = sum(Path(a.wordcraft, n).stat().st_size for n in ('wordcraft-web-bc9cdbc0b42827ee.js', 'wordcraft-web-bc9cdbc0b42827ee_bg.wasm',
                                                               'snippets/eframe-d31a8450ea12dfc1/inline0.js'))
    bilan = {'demarrage_s': demarrage, 'navigateur': info, 'csp_violations': csp, 'requetes_externes': externes, 'window_open': ouvertures,
             'requetes_locales': len(journal), 'poids_servi_octets': poids, 'duree_totale_s': round(time.time() - t0, 1),
             'console': console[-40:], 'fichiers': lignes}
    bilan['travail'] = str(h)
    (sortie / 'fidelite.json').write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in bilan.items() if k != 'fichiers'}, ensure_ascii=False, indent=1)[:3000])


if __name__ == '__main__':
    main()
