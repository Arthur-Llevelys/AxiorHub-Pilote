/* 5.6.96 (lot affichage, U07) : le vrai v5689-navigation.js dans une VM JavaScript, DOM minimal simulé. Aucun navigateur, aucun réseau.
 * Le bouton Retour d'un détail revient par l'historique seulement si l'entrée précédente connue est la même liste, de même origine,
 * ouverte par le même compte et d'où le navigateur vient réellement ; sinon le lien validé par le serveur est suivi (navigation complète).
 * Une page restaurée par le cache du navigateur redemande la session : autre compte ou session fermée -> rechargement.
 * Usage : node navigation5696.cjs /chemin/de/l/arbre */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const arbre = process.argv[2] || path.join(__dirname, '..');
const code = fs.readFileSync(path.join(arbre, 'agent/static/v5689-navigation.js'), 'utf8');
const ORIGINE = 'https://cabinet.example.test';

function page(adresse, {compte = 'c0ffee000001', referrer = '', stockage = new Map(), longueur = 3, session = null} = {}) {
  const ecouteurs = {}, fenetre = {};
  const url = new URL(adresse, ORIGINE);
  const metas = {'axiorhub-prefix': '/agent-courriel', 'axiorhub-csrf': 'jeton-essai', 'axiorhub-details': '/dossier|id', 'axiorhub-compte': compte};
  const doc = {
    referrer, hidden: false, activeElement: null,
    querySelector(sel) { const m = /meta\[name="([^"]+)"\]/.exec(sel); return m && metas[m[1]] !== undefined ? {content: metas[m[1]]} : null; },
    querySelectorAll() { return []; }, getElementById() { return null; },
    addEventListener(nom, fn) { (ecouteurs[nom] = ecouteurs[nom] || []).push(fn); },
  };
  const etat = {retour: 0, recharge: 0, appels: []};
  Object.assign(fenetre, {
    document: doc, URL, Event: class { constructor(t) { this.type = t; } }, scrollY: 0, scrollTo() {},
    location: {href: url.href, origin: url.origin, pathname: url.pathname, search: url.search, reload() { etat.recharge++; }},
    history: {length: longueur, back() { etat.retour++; }},
    sessionStorage: {getItem: k => (stockage.has(k) ? stockage.get(k) : null), setItem: (k, v) => stockage.set(k, String(v)), removeItem: k => stockage.delete(k)},
    performance: {getEntriesByType: () => []},
    addEventListener(nom, fn) { (ecouteurs['w:' + nom] = ecouteurs['w:' + nom] || []).push(fn); },
    setInterval() {},
    fetch(adresse) { etat.appels.push(adresse); return Promise.resolve(session || {ok: true, json: () => Promise.resolve({compte})}); },
  });
  vm.runInNewContext(code, {window: fenetre, URL});
  const lien = href => ({href: new URL(href, ORIGINE).href, target: '', closest(sel) { return sel.indexOf('#ws-main a[href]') >= 0 ? this : null; }});
  const retour = href => ({href: new URL(href, ORIGINE).href, closest(sel) { return sel === '[data-ui89-retour]' ? this : null; }});
  function cliquer(cible) {
    const ev = {target: {closest: s => cible.closest(s)}, defaultPrevented: false, button: 0, preventDefault() { this.defaultPrevented = true; }};
    (ecouteurs.click || []).forEach(fn => fn(ev));
    return ev;
  }
  return {fenetre, etat, stockage, cliquer, lien, retour, ecouteurs};
}

(async () => {
  const resultats = [];
  const verifier = (condition, message) => { if (!condition) throw new Error(message); resultats.push(message); };
  // 1. Liste -> détail -> Retour : même liste, même compte, venu de la liste : retour par l'historique (aucune navigation complète)
  const stockage = new Map();
  const liste = page('/agent-courriel/dossiers?filtre=actifs', {stockage});
  const vers = liste.lien('/agent-courriel/dossier?id=DOS-001');
  liste.cliquer(vers);
  const detail = page(vers.href, {stockage, referrer: ORIGINE + '/agent-courriel/dossiers?filtre=actifs'});
  let ev = detail.cliquer(detail.retour('/agent-courriel/dossiers?filtre=actifs'));
  verifier(ev.defaultPrevented && detail.etat.retour === 1, 'même liste et même compte : retour par l’historique');
  // 2. Autre compte sur le même poste : lien validé (navigation complète)
  const autre = page(vers.href, {stockage, referrer: ORIGINE + '/agent-courriel/dossiers?filtre=actifs', compte: 'c0ffee000002'});
  ev = autre.cliquer(autre.retour('/agent-courriel/dossiers?filtre=actifs'));
  verifier(!ev.defaultPrevented && autre.etat.retour === 0, 'autre compte : lien validé, pas d’historique');
  // 3. Ouvert par un lien direct (le navigateur ne vient pas de la liste) : lien validé
  const direct = page(vers.href, {stockage, referrer: ''});
  ev = direct.cliquer(direct.retour('/agent-courriel/dossiers?filtre=actifs'));
  verifier(!ev.defaultPrevented && direct.etat.retour === 0, 'lien direct : lien validé');
  // 4. Retour vers une autre liste que celle quittée : lien validé
  const ailleurs = page(vers.href, {stockage, referrer: ORIGINE + '/agent-courriel/dossiers?filtre=actifs'});
  ev = ailleurs.cliquer(ailleurs.retour('/agent-courriel/dossiers?filtre=archives'));
  verifier(!ev.defaultPrevented && ailleurs.etat.retour === 0, 'autre liste : lien validé');
  // Page restaurée par le cache du navigateur : session revalidée ; autre compte ou session fermée : rechargement
  const restauree = page('/agent-courriel/dossiers', {session: {ok: true, json: () => Promise.resolve({compte: 'c0ffee000009'})}});
  restauree.ecouteurs['w:pageshow'].forEach(fn => fn({persisted: true}));
  for (let i = 0; i < 6; i++) await Promise.resolve();
  if (restauree.etat.appels.length !== 1 || restauree.etat.recharge !== 1) throw new Error('page restaurée pour un autre compte : pas de rechargement');
  const fermee = page('/agent-courriel/dossiers', {session: {ok: false, json: () => Promise.resolve({})}});
  fermee.ecouteurs['w:pageshow'].forEach(fn => fn({persisted: true}));
  for (let i = 0; i < 6; i++) await Promise.resolve();
  if (fermee.etat.recharge !== 1) throw new Error('session fermée : pas de rechargement');
  const meme = page('/agent-courriel/dossiers');
  meme.ecouteurs['w:pageshow'].forEach(fn => fn({persisted: true}));
  meme.ecouteurs['w:pageshow'].forEach(fn => fn({persisted: false}));
  for (let i = 0; i < 6; i++) await Promise.resolve();
  if (meme.etat.recharge !== 0 || meme.etat.appels.length !== 1) throw new Error('même compte : la page ne doit pas se recharger');
  process.stdout.write('ok ' + resultats.length + ' cas\n' + resultats.join('\n') + '\n');
})().catch(e => { process.stdout.write('ÉCHEC : ' + e.message + '\n'); process.exitCode = 1; });
