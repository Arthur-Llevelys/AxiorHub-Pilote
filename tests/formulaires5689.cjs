// Banc du FormAdapter 5.6.89 (FM01, FM02), sans navigateur : petit DOM fictif, vrais fichiers v5689-formulaires.js et v500.js. Rend un rapport JSON lu par tests/test_v5689_carbon.py.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const STATIC = path.join(__dirname, '..', 'agent', 'static');

function correspond(el, sel) {
  return sel.split(',').some(part => {
    const m = part.trim();
    const tag = /^[a-z][a-z0-9-]*/i.exec(m);
    if (tag && el.tagName.toLowerCase() !== tag[0].toLowerCase()) return false;
    for (const c of m.matchAll(/\.([\w-]+)/g)) if (!el.classList.contains(c[1])) return false;
    for (const c of m.matchAll(/#([\w-]+)/g)) if (el.id !== c[1]) return false;
    for (const c of m.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)) {
      if (!el.hasAttribute(c[1])) return false;
      if (c[2] !== undefined && el.getAttribute(c[1]) !== c[2]) return false;
    }
    if (/:checked/.test(m) && !el.checked) return false;
    return !!(tag || /[.#[]/.test(m));
  });
}
class Noeud {
  constructor(tag, doc) { this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.attrs = {}; this.ownerDocument = doc; this.textContent = ''; this.listeners = {}; }
  get id() { return this.attrs.id || ''; } set id(v) { this.attrs.id = v; }
  get className() { return this.attrs.class || ''; } set className(v) { this.attrs.class = v; }
  get classList() { const n = this; return { contains: c => n.className.split(/\s+/).includes(c), add: c => { n.className = (n.className + ' ' + c).trim(); } }; }
  get type() { return this.attrs.type || (this.tagName === 'INPUT' ? 'text' : ''); }
  get name() { return this.attrs.name || ''; }
  get required() { return 'required' in this.attrs; }
  get multiple() { return 'multiple' in this.attrs; }
  get disabled() { return 'disabled' in this.attrs; } set disabled(v) { if (v) this.attrs.disabled = ''; else delete this.attrs.disabled; }
  hasAttribute(n) { return n in this.attrs; }
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  removeAttribute(n) { delete this.attrs[n]; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; }
  get nextSibling() { const s = this.parentNode.children; return s[s.indexOf(this) + 1] || null; }
  closest(sel) { for (let n = this; n && n.tagName; n = n.parentNode) if (correspond(n, sel)) return n; return null; }
  matches(sel) { return correspond(this, sel); }
  querySelectorAll(sel) { const out = []; const f = n => n.children.forEach(c => { if (correspond(c, sel)) out.push(c); f(c); }); f(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() { this.ownerDocument.focused = this; }
  textes() { return [this.textContent, ...this.children.map(c => c.textes())].join(' '); }
}
function html(doc, parent, tag, attrs, props) {
  const n = new Noeud(tag, doc);
  Object.entries(attrs || {}).forEach(([k, v]) => { n.attrs[k] = v; });
  Object.assign(n, props || {});
  parent.appendChild(n);
  return n;
}
function monde(reponse) {
  const doc = new Noeud('html', null); doc.ownerDocument = doc; doc.documentElement = doc;
  doc.createElement = t => new Noeud(t, doc);
  doc.getElementById = i => doc.querySelector('#' + i);
  const head = html(doc, doc, 'head');
  html(doc, head, 'meta', { name: 'axiorhub-prefix', content: '/app' }); html(doc, head, 'meta', { name: 'axiorhub-csrf', content: 'jeton' });
  // correspondance simple pour meta[name="..."] : seul l'attribut name est lu
  const appels = [];
  const fenetre = { document: doc, setTimeout: () => 0, clearTimeout() {}, confirm: () => true, location: { pathname: '/p', reload() { appels.push('reload'); } }, Promise, FileReader: class { readAsDataURL(f) { this.result = 'data:' + f.contenu; this.onload(); } },
    atob, URLSearchParams, Event: class { constructor(t) { this.type = t; } }, fetch(url, opts) { appels.push({ url, body: JSON.parse(opts.body) }); return reponse(appels.length); } };
  fenetre.window = fenetre; fenetre.globalThis = fenetre;
  const ctx = vm.createContext(fenetre);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'v5689-formulaires.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'v500.js'), 'utf8'), ctx);
  return { doc, fenetre, appels, toasts: () => doc.querySelectorAll('.ax-toast').map(t => [t.className, t.textContent]) };
}
function formulaire(m) {
  const body = html(m.doc, m.doc, 'body');
  html(m.doc, body, 'div', { id: 'ax-toast' });
  const form = html(m.doc, body, 'form', { class: 'm5-form', 'data-api': 'essai/route' });
  const champ = (attrs, props, tag) => { const lab = html(m.doc, form, 'label', { class: 'ui89-champ' }); return html(m.doc, lab, tag || 'input', attrs, props); };
  return { body, form, champ };
}
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); await new Promise(r => setImmediate(r)); };
function soumettre(m, form) {
  const ev = { target: form, preventDefault() { ev.bloque = true; }, submitter: form.querySelector('button') };
  (m.doc.listeners.submit || []).forEach(f => f(ev));
  return ev;
}
const R = {};
const ok = corps => () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(corps) });

(async () => {
  // FM01 : lecture de chaque nature de champ
  let m = monde(ok({ message: 'Fait.' }));
  let f = formulaire(m);
  f.champ({ type: 'checkbox', name: 'coche' }, { checked: true });
  f.champ({ type: 'checkbox', name: 'decoche' }, { checked: false });
  f.champ({ type: 'radio', name: 'r', value: 'a' }, { value: 'a', checked: false });
  f.champ({ type: 'radio', name: 'r', value: 'b' }, { value: 'b', checked: true });
  f.champ({ type: 'radio', name: 'sans', value: 'x' }, { value: 'x', checked: false });
  f.champ({ name: 'liste' }, { value: 'deux' }, 'select');
  f.champ({ name: 'multi', multiple: '' }, { options: [{ value: 'a', selected: true }, { value: 'b', selected: false }, { value: 'c', selected: true }] }, 'select');
  f.champ({ name: 'texte' }, { value: 'bonjour' });
  f.champ({ name: 'zone' }, { value: 'long texte' }, 'textarea');
  f.champ({ name: 'eteint', disabled: '' }, { value: 'à ne pas envoyer' });
  f.champ({ type: 'file', name: 'piece' }, { files: [] });
  const groupe = html(m.doc, f.form, 'fieldset', { disabled: '' }); html(m.doc, groupe, 'input', { name: 'dans_groupe' }, { value: 'non' });
  // composants Carbon nommés, lus directement : Shadow DOM hors de portée d'un querySelector
  html(m.doc, f.form, 'cds-checkbox', { name: 'cds_faux' }, { checked: false });
  html(m.doc, f.form, 'cds-checkbox', { name: 'cds_vrai' }, { checked: true });
  html(m.doc, f.form, 'cds-select', { name: 'cds_liste' }, { value: 'v2' });
  html(m.doc, f.form, 'cds-text-input', { name: 'cds_texte' }, { value: 't' });
  html(m.doc, f.form, 'cds-radio-button-group', { name: 'cds_radio' }, { value: 'b' });
  html(m.doc, f.form, 'cds-checkbox', { name: 'cds_eteint', disabled: '' }, { checked: true });
  // composant adjoint : jamais lu en double, le natif fait foi
  const adj = html(m.doc, f.form, 'cds-checkbox', { 'data-ui89-adjoint': '', name: 'adjoint' }, { checked: true });
  R.collect = m.fenetre.ax89Form.collect(f.form);
  void adj;

  // fichiers : seulement sur demande, trop lourd refusé avant l'envoi
  m = monde(ok({})); f = formulaire(m);
  f.champ({ type: 'file', name: 'demande', 'data-envoi-fichier': '' }, { files: [{ name: 'a.txt', type: 'text/plain', size: 4, contenu: 'QUJD' }] });
  R.fichier = await m.fenetre.ax89Form.fichiers(f.form, {});
  m = monde(ok({})); f = formulaire(m);
  f.champ({ type: 'file', name: 'gros', 'data-envoi-fichier': '' }, { files: [{ name: 'b.bin', type: '', size: 7 * 1024 * 1024, contenu: '' }] });
  R.trop_lourd = await m.fenetre.ax89Form.fichiers(f.form, {}).then(() => 'envoye', e => [e.message, e.champ]);

  // required : champ vide, radio sans choix, composant Carbon ; erreur près du champ, aucun envoi
  m = monde(ok({ message: 'Fait.' })); f = formulaire(m);
  const requis = f.champ({ name: 'nom', required: '' }, { value: '' });
  f.champ({ type: 'radio', name: 'choix', value: 'a', required: '' }, { value: 'a', checked: false });
  html(m.doc, f.form, 'cds-select', { name: 'carbon', required: '' }, { value: '' });
  html(m.doc, f.form, 'p', { class: 'm5-status' });
  html(m.doc, f.form, 'button', { type: 'submit' });
  soumettre(m, f.form); await tick();
  R.requis = { appels: m.appels.length, invalide: requis.getAttribute('aria-invalid'), decrit: !!requis.getAttribute('aria-describedby'), erreurs: f.form.querySelectorAll('.ui89-erreur').length, frere: requis.parentNode.nextSibling.className };

  // FM02 : deux soumissions, un seul effet ; le bouton revient après la réponse
  let libere = null;
  m = monde(() => new Promise(r => { libere = () => r({ ok: true, status: 200, json: () => Promise.resolve({ message: 'Enregistré.' }) }); })); f = formulaire(m);
  const bouton = html(m.doc, f.form, 'button', { type: 'submit' });
  soumettre(m, f.form); await tick();
  const pendant = bouton.disabled;
  soumettre(m, f.form); await tick();
  const avant = m.appels.length;
  libere(); await tick();
  R.double = { appels: avant, occupe: pendant, apres: bouton.disabled, verrou: f.form.getAttribute('data-ui89-envoi'), succes: m.toasts().filter(t => !/bad/.test(t[0])).length };

  // FM02 : erreur HTTP sans corps d'erreur, corps illisible, erreur métier sur un champ : jamais de succès
  for (const [cle, reponse] of Object.entries({
    http_vide: () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }),
    http_html: () => Promise.resolve({ ok: false, status: 502, json: () => Promise.reject(new Error('html')) }),
    metier: () => Promise.resolve({ ok: false, status: 400, json: () => Promise.resolve({ error: 'x', message: 'Valeur refusée.', field: 'nom' }) }),
    global: () => Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ error: 'y', message: 'Conflit de version.' }) }),
  })) {
    m = monde(reponse); f = formulaire(m);
    const champ = f.champ({ name: 'nom' }, { value: 'x' });
    html(m.doc, f.form, 'p', { class: 'm5-status' });
    const b = html(m.doc, f.form, 'button', { type: 'submit' });
    soumettre(m, f.form); await tick();
    R[cle] = { toasts: m.toasts(), invalide: champ.getAttribute('aria-invalid'), erreur: (f.form.querySelector('.ui89-erreur') || { textContent: '' }).textContent, bouton: b.disabled, verrou: f.form.getAttribute('data-ui89-envoi') };
  }

  // seconde tentative possible après une erreur
  let n = 0;
  m = monde(() => { n += 1; return n === 1 ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }) : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ message: 'Fait.' }) }); }); f = formulaire(m);
  html(m.doc, f.form, 'button', { type: 'submit' });
  soumettre(m, f.form); await tick(); soumettre(m, f.form); await tick();
  R.reprise = { appels: m.appels.length, erreurs: f.form.querySelectorAll('.ui89-erreur').length, toasts: m.toasts().map(t => t[1]) };

  console.log(JSON.stringify(R));
})().catch(e => { console.error(e); process.exit(1); });
