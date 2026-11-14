/* 5.6.100 (lot F, R08 / V100-08) : le vrai v5689-navigation.js dans une VM JavaScript, DOM minimal simulé. Aucun navigateur, aucun réseau.
 * Retour contextuel : ancre stable de la ligne, ligne disparue -> la suivante, liste chargée après la page (annonce « ax100:liste-prete »),
 * défilement d'une zone, et page restaurée par le cache du navigateur : contenu masqué pendant la revalidation de la session.
 * Usage : node navigation56100.cjs /chemin/de/l/arbre */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const arbre = process.argv[2] || path.join(__dirname, '..');
const code = fs.readFileSync(path.join(arbre, 'agent/static/v5689-navigation.js'), 'utf8');
const ORIGINE = 'https://cabinet.example.test';

function element(attrs = {}, href = '') {
  const el = {
    attrs: {...attrs}, id: attrs.id || '', href: href ? new URL(href, ORIGINE).href : '', target: '', scrollTop: 0, hidden: false, isConnected: true, focusCount: 0,
    getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; },
    setAttribute(n, v) { this.attrs[n] = String(v); }, removeAttribute(n) { delete this.attrs[n]; }, hasAttribute(n) { return n in this.attrs; },
    matches(sel) { return sel.indexOf('a[href]') >= 0 && !!this.href; },
    querySelector() { return null; },
    closest(sel) {
      if (sel.indexOf('#ws-main a[href]') >= 0 && this.href) return this;
      if (sel === '[data-ax100-ancre]' && 'data-ax100-ancre' in this.attrs) return this;
      return null;
    },
    focus() { this.focusCount++; page.actif = this; },
  };
  return el;
}
let page = {};

function monter(adresse, {stockage = new Map(), liens = [], zones = [], compte = 'c0ffee000001', session = null, navType = ''} = {}) {
  const ecouteurs = {}, fenetre = {}, minuteries = [];
  const url = new URL(adresse, ORIGINE);
  const metas = {'axiorhub-prefix': '/agent-courriel', 'axiorhub-csrf': 'jeton-essai', 'axiorhub-details': '/dossier|id', 'axiorhub-compte': compte};
  const main = element({id: 'ws-main'});
  const etat = {recharge: 0, appels: [], liens, zones, main};
  const doc = {
    referrer: '', hidden: false, activeElement: null,
    querySelector(sel) { const m = /meta\[name="([^"]+)"\]/.exec(sel); return m && metas[m[1]] !== undefined ? {content: metas[m[1]]} : null; },
    querySelectorAll(sel) {
      if (sel === '#ws-main a[href]') return etat.liens.slice();
      if (sel === '#ws-main [data-ax100-ancre]') return etat.liens.filter(l => 'data-ax100-ancre' in l.attrs);
      if (sel.indexOf('[data-ax100-zone]') >= 0) return etat.zones.slice();
      return [];
    },
    getElementById(id) { return id === 'ws-main' ? main : null; },
    addEventListener(nom, fn) { (ecouteurs[nom] = ecouteurs[nom] || []).push(fn); },
    removeEventListener(nom, fn) { ecouteurs[nom] = (ecouteurs[nom] || []).filter(f => f !== fn); },
    dispatchEvent(ev) { (ecouteurs[ev.type] || []).slice().forEach(fn => fn(ev)); return true; },
  };
  Object.assign(fenetre, {
    document: doc, URL, Event: class { constructor(t) { this.type = t; } }, scrollY: 0, scrollTo(x, y) { fenetre.scrollY = y; },
    location: {href: url.href, origin: url.origin, pathname: url.pathname, search: url.search, reload() { etat.recharge++; }},
    history: {length: 3, back() {}},
    sessionStorage: {getItem: k => (stockage.has(k) ? stockage.get(k) : null), setItem: (k, v) => stockage.set(k, String(v)), removeItem: k => stockage.delete(k)},
    performance: {getEntriesByType: () => (navType ? [{type: navType}] : [])},
    addEventListener(nom, fn) { (ecouteurs['w:' + nom] = ecouteurs['w:' + nom] || []).push(fn); },
    setInterval() {}, setTimeout(fn) { minuteries.push(fn); return minuteries.length; }, clearTimeout() {},
    fetch(adresse) { etat.appels.push(adresse); return Promise.resolve(session || {ok: true, json: () => Promise.resolve({compte})}); },
  });
  vm.runInNewContext(code, {window: fenetre, URL});
  function cliquer(cible) {
    const ev = {target: {closest: s => cible.closest(s)}, defaultPrevented: false, button: 0, preventDefault() { this.defaultPrevented = true; }};
    (ecouteurs.click || []).forEach(fn => fn(ev));
    return ev;
  }
  const afficher = persisted => (ecouteurs['w:pageshow'] || []).forEach(fn => fn({persisted}));
  return {fenetre, etat, stockage, cliquer, afficher, ecouteurs, doc};
}

const ligne = n => element({'data-ax100-ancre': 'DOS-00' + n}, '/agent-courriel/dossier?id=DOS-00' + n);

(async () => {
  const resultats = [];
  const verifier = (condition, message) => { if (!condition) throw new Error(message); resultats.push(message); };
  const LISTE = '/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc';

  // 1. Liste -> détail : ancre, suivantes et défilement de zone mémorisés ; l'adresse de retour garde filtre, tri et curseur
  const stockage = new Map();
  const zone = element({'data-ax100-zone': '', id: 'liste'}); zone.scrollTop = 340;
  const liens = [1, 2, 3, 4].map(ligne);
  const liste = monter(LISTE, {stockage, liens, zones: [zone]});
  liste.fenetre.scrollY = 120;
  liste.cliquer(liens[1]);
  const contexte = JSON.parse(stockage.get('ui89-etat:/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc'));
  verifier(contexte.ancre === 'DOS-002' && contexte.suivantes[0].ancre === 'DOS-003' && contexte.zones.liste === 340 && contexte.y === 120,
           'ancre stable, lignes suivantes et défilement de la zone mémorisés');
  verifier(new URL(liens[1].href, ORIGINE).searchParams.get('return_to') === '/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc',
           'adresse de retour : filtre, tri et curseur conservés');
  verifier(!JSON.stringify(contexte).includes('Dossier') && !JSON.stringify(contexte).includes('titre'), 'aucun titre dans le contexte mémorisé');

  // 2. Retour, ligne ouverte encore présente : même ligne focalisée, position et zone restaurées
  stockage.set('ui89-restaurer', '/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc');
  const zone2 = element({'data-ax100-zone': '', id: 'liste'});
  const retour = monter(LISTE, {stockage, liens: [1, 2, 3, 4].map(ligne), zones: [zone2]});
  retour.afficher(false);
  verifier(page.actif && page.actif.attrs['data-ax100-ancre'] === 'DOS-002' && retour.fenetre.scrollY === 120 && zone2.scrollTop === 340,
           'retour : ligne, position de la page et de la zone restaurées');

  // 3. Ligne disparue entre-temps (dossier clos, filtre) : la suivante reçoit le focus, signalée
  stockage.set('ui89-restaurer', '/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc');
  const sans2 = monter(LISTE, {stockage, liens: [1, 3, 4].map(ligne)});
  sans2.afficher(false);
  verifier(page.actif && page.actif.attrs['data-ax100-ancre'] === 'DOS-003' && 'data-ax100-retrouve' in page.actif.attrs, 'ligne disparue : la suivante est choisie');

  // 4. Liste chargée après la page : focus remis quand la liste annonce qu'elle est prête
  stockage.set('ui89-restaurer', '/agent-courriel/dossiers?filtre=actifs&tri=nom&curseur=abc');
  page.actif = null;
  const differee = monter(LISTE, {stockage, liens: []});
  differee.afficher(false);
  verifier(page.actif === null, 'liste pas encore chargée : aucun focus au hasard');
  differee.etat.liens = [1, 2, 3].map(ligne);
  differee.fenetre.AX100.navigation.listePrete();
  verifier(page.actif && page.actif.attrs['data-ax100-ancre'] === 'DOS-002', 'liste chargée ensuite : la ligne ouverte reçoit le focus');

  // 5. Page restaurée par le cache du navigateur : contenu masqué pendant la vérification ; même compte -> visible ; autre -> rechargement
  const meme = monter(LISTE);
  meme.afficher(true);
  verifier(meme.etat.main.hidden === true, 'cache du navigateur : contenu privé masqué avant la revalidation');
  for (let i = 0; i < 6; i++) await Promise.resolve();
  verifier(meme.etat.main.hidden === false && meme.etat.recharge === 0, 'même compte : contenu rendu sans rechargement');
  const autre = monter(LISTE, {session: {ok: true, json: () => Promise.resolve({compte: 'c0ffee000009'})}});
  autre.afficher(true);
  for (let i = 0; i < 6; i++) await Promise.resolve();
  verifier(autre.etat.main.hidden === true && autre.etat.recharge === 1, 'autre compte : écran laissé vide et page rechargée');
  const fermee = monter(LISTE, {session: {ok: false, json: () => Promise.resolve({})}});
  fermee.afficher(true);
  for (let i = 0; i < 6; i++) await Promise.resolve();
  verifier(fermee.etat.main.hidden === true && fermee.etat.recharge === 1, 'session fermée : écran laissé vide et page rechargée');
  process.stdout.write('ok ' + resultats.length + ' cas\n' + resultats.join('\n') + '\n');
})().catch(e => { process.stdout.write('ÉCHEC : ' + e.message + '\n'); process.exitCode = 1; });
