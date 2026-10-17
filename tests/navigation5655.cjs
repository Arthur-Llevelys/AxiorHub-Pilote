/* Test unitaire du vrai v500.js dans une VM JavaScript, DOM minimal simulé.
 * Aucun navigateur ni service externe. node test_navigation5655.cjs /chemin/source
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const src = process.argv[2] || path.join(__dirname, 'sources/axiorhub-mail-agent-5.6.55');
const code = fs.readFileSync(path.join(src, 'agent/static/v500.js'), 'utf8');

async function trial(reload) {
  const handlers = {};
  const loc = {pathname: '/agent-courriel/facturation', href: '', reload() { this.reloaded = true; }};
  const btn = {getAttribute() { return null; }, setAttribute() {}};
  const fields = [{name: 'type', type: 'radio', checked: true, value: 'facture', closest() { return null; }}];
  const form = {
    closest() { return form; }, querySelector() { return btn; }, querySelectorAll() { return fields; },
    hasAttribute(k) { return k === 'data-reload' && reload; },
    getAttribute(k) { return k === 'data-api' ? 'm5653/document/preparer' : null; }
  };
  const document = {querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener(name, fn) { handlers[name] = fn; }};
  const ctx = {document, window: {}, location: loc, setTimeout(fn) { fn(); },
    fetch() { return Promise.resolve({json: () => Promise.resolve({document: 'docfictif', goto: '?document=docfictif', message: 'Facture préparée en brouillon.'})}); }};
  vm.runInNewContext(code, ctx);
  handlers.submit({target: form, submitter: btn, preventDefault() {}});
  for (let i = 0; i < 12; i++) await Promise.resolve();
  return {attribut_data_reload: reload, destination: loc.href, bouton_reactive: btn.disabled === false};
}
(async () => {
  const original = await trial(false);
  const correction = await trial(true);
  const evidence = {original, controle_attribut_corrige: correction};
  fs.writeFileSync(path.join(process.env.AXH_NAV_OUT || __dirname, 'navigation-5655.json'), JSON.stringify(evidence, null, 2) + '\n');
  process.stdout.write(JSON.stringify(evidence, null, 2) + '\n');
  if (correction.destination !== '/agent-courriel/facturation?document=docfictif') throw new Error('Harnais invalide');
  process.exitCode = original.destination ? 0 : 1;
})();
