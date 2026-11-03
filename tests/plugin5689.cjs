/* 5.6.89 : plugin.js exécuté sous Node avec un éditeur et un serveur simulés : frappe, panne réseau, réponse perdue, écriture de l'IA puis correction manuelle.
   Aucun EuroOffice réel : cet essai prouve le câblage du plugin, pas le comportement de l'éditeur. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dir = process.argv[2];
const clock = {t: 1_000_000};
class FakeDate extends Date {
  constructor(...args) { if (args.length) super(...args); else super(clock.t); }
  static now() { return clock.t; }
}
const timers = [];
const posts = [];
const server = {mode: 'ok', revision: 0, seen: {}, events: {cursor: 0, commands: []}, analyses: 0, patch: null};

function reply(status, body) { return Promise.resolve({ok: status < 400, status, json: () => Promise.resolve(body)}); }

function fetchFake(url, options) {
  if (String(url).endsWith('config.json')) return reply(200, {});
  const route = String(url).replace(/^.*\/office\/bridge/, '');
  const body = options && options.body ? JSON.parse(options.body) : undefined;
  if (route === '/observations') {
    posts.push(body);
    if (server.mode === 'down') return Promise.reject(new TypeError('réseau coupé'));
    if (server.mode === 'refused') return reply(422, {error: 'observation_invalide'});
    const known = server.seen[body.event_id];
    if (!known) { server.revision += 1; server.seen[body.event_id] = server.revision; }
    if (server.mode === 'lost') { server.mode = 'ok'; return Promise.reject(new TypeError('réponse perdue')); }
    return reply(200, {revision: server.seen[body.event_id], duplicate: !!known, reconcile: false, analysable: body.origin !== 'ai_command' && body.upsert_blocks.length > 0});
  }
  if (route === '/analyse') { server.analyses += 1; return reply(200, {state: 'lancee'}); }
  if (route.startsWith('/events')) return reply(200, server.events);
  if (route === '/lease') return reply(200, {held: true, epoch: 1});
  if (/\/commands\/[a-f0-9]+\/claim$/.test(route)) return reply(200, server.patch);
  if (/\/commands\/[a-f0-9]+\/ack$/.test(route)) return reply(200, {state: 'applied'});
  return reply(404, {error: 'route_inconnue'});
}

const attributes = {'data-editor-origin': 'https://office.example.test', 'data-bridge': 'https://cabinet.example.test/office/bridge', 'data-guid': 'asc.{essai}'};
const sandbox = {document: {documentElement: {getAttribute: k => attributes[k] || ''}}, fetch: fetchFake, setInterval: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearInterval: () => {},
                 navigator: {}, console, Date: FakeDate, addEventListener: () => {}, parent: {postMessage: () => {}}, Promise, Math, JSON, Object, String};
sandbox.window = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(dir, 'core.js'), 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(dir, 'plugin.js'), 'utf8'), sandbox);

const plugin = sandbox.Asc.plugin;
const document_ = {'1': 'Prix fictif : 100 000 euros', '2': 'Clause finale.'};
const cover = {paragraphes: true, tableaux: true, entetes: true, notes: true, revisions: false};
plugin.callCommand = (fn, close, recalc, done) => {
  if (fn.name === 'applyPatch') { done(plugin.applied(sandbox.Asc.scope.cmd)); return; }
  done({rows: Object.keys(document_).map(id => ({id, text: document_[id], kind: 'paragraph', patchable: true})), cover});
};
plugin.attachEditorEvent = (name, fn) => { plugin['event_' + name] = fn; };
plugin.info = {options: {token: 'jeton-fictif', session_id: 'S', document_id: 'D'}};

const settle = async () => { for (let i = 0; i < 30; i += 1) await new Promise(r => setImmediate(r)); };
const timer = ms => timers.find(t => t.ms === ms).fn;
const type = (id, text) => { document_[id] = text; plugin.event_onParagraphText({paragraphId: id, text}); };

(async () => {
  plugin.init();
  await settle();
  assert.strictEqual(posts.length, 1);
  assert.strictEqual(posts[0].upsert_blocks.length, 2);
  const flush = timer(500), poll = timer(3000);
  posts.length = 0;
  const initial = server.analyses;

  /* B08 : POST rejeté avant réception, puis reprise sans nouvelle frappe */
  server.mode = 'down';
  type('1', 'Prix fictif : 120 000 euros');
  clock.t += 2100; flush(); await settle();
  assert.strictEqual(posts.length, 1);
  const lost = posts[0];
  clock.t += 500; flush(); await settle();
  assert.strictEqual(posts.length, 1);                                 /* attente croissante, pas de rafale */
  server.mode = 'ok';
  clock.t += 2000; flush(); await settle();
  assert.strictEqual(posts[1].event_id, lost.event_id);
  assert.strictEqual(posts[1].client_seq, lost.client_seq);
  assert.deepStrictEqual(posts[1].upsert_blocks, lost.upsert_blocks);
  assert.strictEqual(server.analyses, initial + 1);                    /* reprise : analyse de la révision courante */

  /* B08 : serveur ayant reçu le POST, réponse perdue : un seul numéro de révision */
  posts.length = 0;
  const revision = server.revision;
  server.mode = 'lost';
  type('1', 'Prix fictif : 130 000 euros');
  clock.t += 2100; flush(); await settle();
  clock.t += 2100; flush(); await settle();
  assert.strictEqual(posts.length, 2);
  assert.strictEqual(posts[0].event_id, posts[1].event_id);
  assert.strictEqual(server.revision, revision + 1);

  /* chap. 20 : un refus définitif n'est pas rejoué */
  posts.length = 0;
  server.mode = 'refused';
  type('2', 'Clause refusée.');
  clock.t += 2100; flush(); await settle();
  assert.strictEqual(posts.length, 1);
  server.mode = 'ok';
  clock.t += 120000; flush(); await settle();
  assert.ok(posts.every(p => p.upsert_blocks.length === 0 || p === posts[0]));

  /* B10 : écriture exacte de l'IA, puis correction manuelle dans le même paragraphe, 500 ms après */
  posts.length = 0;
  const command = 'a'.repeat(32);
  server.events = {cursor: 1, commands: [command]};
  server.patch = {operations: [{op_id: 'op-1', action: 'replace_text', anchor: {block_id: 'P-1', expected_text: '130 000'}, replacement: '135 000'}]};
  plugin.applied = () => {
    type('1', 'Prix fictif : 135 000 euros');                          /* notification de l'éditeur avant le retour de la commande */
    return {results: [{op_id: 'op-1', status: 'applied'}], texts: {1: 'Prix fictif : 135 000 euros'}};
  };
  poll(); await settle();
  server.events = {cursor: 1, commands: []};
  const ai = posts.filter(p => p.origin === 'ai_command');
  assert.strictEqual(ai.length, 1);
  assert.strictEqual(ai[0].command_id, command);
  assert.strictEqual(ai[0].upsert_blocks[0].text, 'Prix fictif : 135 000 euros');
  posts.length = 0;
  clock.t += 500;
  type('1', 'Prix fictif : 136 000 euros');
  clock.t += 2100; flush(); await settle();
  assert.strictEqual(posts.length, 1);
  assert.strictEqual(posts[0].origin, 'local_user');
  assert.strictEqual(posts[0].command_id, null);

  console.log('plugin-ok');
})().catch(error => { console.error(error); process.exit(1); });
