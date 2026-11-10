/* 5.6.96 (V07, lot voix) : simulation Node du vrai lecteur v5694-voix.js et du vrai worker v5694-brindille.js.
 * Faux Worker, faux AudioContext, faux Audio, faux HTTP : aucun navigateur, aucun réseau, aucun moteur vocal réel (le moteur Brindille
 * WASM n'est exercé que si AXIORHUB_ESSAI_BRINDILLE_NODE=1, comme le test 5.6.94 : code tiers embarqué, exécuté seulement sur demande).
 * node tests/lecture5696.cjs <racine du dépôt> ; dernière ligne : « lecture5696 : ok … ».
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL, fileURLToPath } = require('node:url');

const racine = process.argv[2] || path.resolve(__dirname, '..');
const statique = path.join(racine, 'agent', 'static');
const LECTEUR = fs.readFileSync(path.join(statique, 'v5694-voix.js'), 'utf8');
const WORKER = fs.readFileSync(path.join(statique, 'v5694-brindille.js'), 'utf8');
const PHRASE = 'Me DURAND plaide pour la SARL ZORVAL devant le tribunal de Valbrune, RG 24/01234, le 12/03/2026. ';
const attendre = ms => new Promise(r => setTimeout(r, ms));

/* Serveur fictif : ``voix/preparer`` découpe en segments de 240 caractères au plus (comme voix5694.preparer), sans rien couper. */
function segments(texte) {
  const out = []; let tampon = '';
  for (const phrase of texte.split(/(?<=[.!?])\s+/)) {
    if (tampon && (tampon + ' ' + phrase).length > 240) { out.push(tampon); tampon = phrase; } else tampon = (tampon ? tampon + ' ' : '') + phrase;
  }
  if (tampon.trim()) out.push(tampon.trim());
  return out;
}

/* Banc : options.voix = 'brindille' ou 'espeak' ; echecSynth = n° de segment que le faux worker refuse ; echecLecture = n° de segment dont
   la lecture échoue (Brindille : start() refusé ; serveur : erreur de décodage) ; bloque = la lecture ne se termine jamais. */
function banc(options = {}) {
  const journal = {preparer: [], synth: [], serveur: [], joues: [], notes: [], etat: 0};
  let numeroSynth = 0, numeroJoue = 0;
  const profilVoix = options.profil === 'originale' ? {pitch: 0, intonation: 1, treble: 0, breath: 0} : {pitch: -2, intonation: 0.9, treble: -2, breath: -1};
  class FauxWorker {
    constructor() { setTimeout(() => this.onmessage && this.onmessage({data: {type: 'ready'}}), 0); }
    postMessage(m) {
      if (m.type !== 'speak') return;
      numeroSynth++;
      journal.synth.push({text: m.text, voix: m.voix, rang: numeroSynth});
      const rang = numeroSynth;
      setTimeout(() => {
        if (options.echecSynth && rang === options.echecSynth) this.onmessage({data: {type: 'error', id: m.id, message: 'synthèse impossible (faux worker)'}});
        else this.onmessage({data: {type: 'audio', id: m.id, pcm: new Float32Array(2205).fill(0.2), sampleRate: 22050}});
      }, 1);
    }
    terminate() {}
  }
  class FauxContexte {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
    resume() { this.state = 'running'; return Promise.resolve(); }
    suspend() { this.state = 'suspended'; return Promise.resolve(); }
    createBuffer(c, n, rate) { return {duration: n / rate, copyToChannel() {}}; }
    createAnalyser() { return {fftSize: 256, connect() {}, disconnect() {}, getFloatTimeDomainData(a) { a.fill(0.1); }}; }
    createBufferSource() {
      const node = {connect() {}, disconnect() {}, stop() { node.arrete = true; }, onended: null,
        start() {
          numeroJoue++;
          journal.joues.push({rang: numeroJoue, texte: journal.synth[numeroJoue - 1] ? journal.synth[numeroJoue - 1].text : ''});
          if (options.echecLecture && numeroJoue === options.echecLecture) throw new Error('lecture refusée par le faux contexte');
          if (!options.bloque) setTimeout(() => { if (!node.arrete && node.onended) node.onended(); }, 2);
        }};
      return node;
    }
  }
  class FauxAudio {
    constructor() { this.currentTime = 0; }
    pause() {}
    play() {
      numeroJoue++;
      journal.joues.push({rang: numeroJoue, texte: journal.serveur[numeroJoue - 1] || ''});
      const rang = numeroJoue;
      setTimeout(() => {
        if (options.echecLecture && rang === options.echecLecture) { if (this.onerror) this.onerror(); }
        else if (!options.bloque && this.onended) this.onended();
      }, 2);
      return Promise.resolve();
    }
  }
  const reponse = (valeur, ok = true) => ({ok, status: ok ? 200 : 400, json: async () => valeur, blob: async () => ({size: 64})});
  const fetch = async (url, init = {}) => {
    if (init.signal && init.signal.aborted) throw Object.assign(new Error('arrêt'), {name: 'AbortError'});
    if (url.endsWith('/api440/m5689/voix/preparer')) {
      const corps = JSON.parse(init.body);
      journal.preparer.push(corps);
      return reponse({provider: options.voix || 'brindille', browser: (options.voix || 'brindille') === 'brindille', fallback: 'espeak',
        paragraphs: [segments(corps.text)], rate: 1, pause_ms: 0, profil: options.profil || 'posee', voix: profilVoix, limite: null});
    }
    if (url.endsWith('/api440/m5689/voix/etat')) { journal.etat++; return reponse({last_fallback: null}); }
    if (url.endsWith('/api440/m568/speech')) { journal.serveur.push(JSON.parse(init.body).text); return reponse(null); }
    throw new Error('adresse inattendue : ' + url);
  };
  const console_ = {info() {}, log() {}, warn() {}, error() {}};
  const window = {AudioContext: FauxContexte};
  const env = {window, document: {currentScript: null, querySelector: () => null}, location: {href: 'https://cabinet.example.test/'},
    performance, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, console: console_, fetch, Worker: FauxWorker,
    WebAssembly: {}, Audio: FauxAudio, Float32Array, Promise, JSON, Math, Date, Object, Array, Number, String, Error, Map, Set,
    URL: Object.assign(function (u, b) { return new URL(u, b); }, {createObjectURL: () => 'blob:faux', revokeObjectURL() {}}),
    MediaStream: class {}, navigator: {}};
  env.window.window = env.window;
  vm.runInNewContext(LECTEUR, env);
  const voix = env.window.axiorhubVoix;
  return {voix, journal, note: t => journal.notes.push(t)};
}

const lus = b => b.journal.joues.map(j => j.texte);

async function v01_lecture_longue() {
  const texte = PHRASE.repeat(Math.ceil(9000 / PHRASE.length)) + 'Dernière phrase repère du texte.';
  assert(texte.length > 9000);
  const b = banc();
  const r = await b.voix.speak(texte, {note: b.note});
  assert.equal(b.journal.preparer.length, 1);
  assert.equal(b.journal.preparer[0].text, texte.trim(), 'texte entier transmis au préparateur, sans coupe');
  assert.equal(r.statut, 'termine'); assert.equal(r.stopped, false);
  assert.equal(r.segments, segments(texte.trim()).length); assert.equal(r.segment, r.segments);
  assert.equal(b.journal.synth.length, r.segments, 'tous les segments transmis au moteur');
  assert.equal(b.journal.joues.length, r.segments, 'tous les segments joués');
  assert.match(lus(b).at(-1), /Dernière phrase repère du texte\.$/);
  assert.equal(b.journal.notes.at(-1), 'Lecture terminée.');
  const diag = JSON.stringify(b.voix.diagnostic());
  assert(!/DURAND|ZORVAL|Valbrune/.test(diag), 'le diagnostic ne garde aucun texte');
  assert.match(diag, /"moteur_effectif":"brindille"/);
  return r.segments;
}

async function v05_profil_transmis_au_worker() {
  for (const profil of ['posee', 'originale']) {
    const b = banc({profil});
    await b.voix.speak(PHRASE.repeat(5), {note: b.note});
    const attendu = profil === 'posee' ? {pitch: -2, intonation: 0.9, treble: -2, breath: -1} : {pitch: 0, intonation: 1, treble: 0, breath: 0};
    assert(b.journal.synth.length > 1);
    b.journal.synth.forEach(s => assert.deepEqual({...s.voix}, attendu, 'même timbre pour tous les segments du tour'));
  }
}

async function v02_erreur_annoncee_sans_termine() {
  const b = banc({voix: 'espeak', echecLecture: 1});
  let erreur = null;
  try { await b.voix.speak('Première phrase fictive. Deuxième phrase fictive.', {note: b.note}); } catch (e) { erreur = e; }
  assert(erreur, 'une erreur de décodage rejette la lecture');
  assert.equal(erreur.resultat.statut, 'echec_segment'); assert.equal(erreur.resultat.segment, 1);
  assert.match(erreur.message, /segment 1 sur 1/);
  assert(!b.journal.notes.some(n => /terminée/.test(n)), 'aucun « terminé » après une erreur');
}

async function v02_reprise_au_segment_3() {
  const texte = Array.from({length: 6}, (_, i) => 'Phrase fictive numéro ' + (i + 1) + ' ' + 'du dossier DOS-001 à lire en entier. '.repeat(5)).join('');
  const b = banc({echecLecture: 3});
  let erreur = null;
  try { await b.voix.speak(texte, {note: b.note}); } catch (e) { erreur = e; }
  assert(erreur); assert.equal(erreur.resultat.segment, 3); assert.equal(erreur.resultat.statut, 'echec_segment');
  assert(!b.journal.notes.some(n => /terminée/.test(n)));
  const total = erreur.resultat.segments;
  const avant = b.journal.joues.length, synthAvant = b.journal.synth.length;
  const r = await b.voix.speak(texte, {note: b.note, depuis: 3});
  assert.equal(r.statut, 'termine'); assert.equal(r.depuis, 3);
  assert.equal(b.journal.preparer.length, 1, 'la reprise ne reprépare pas le même texte');
  const repris = b.journal.synth.slice(synthAvant).map(s => s.text);
  assert.equal(repris[0], segments(texte.trim())[2], 'la reprise commence au segment 3');
  assert.equal(b.journal.joues.length - avant, total - 2);
}

async function v03_arret_sans_segment_suivant() {
  const b = banc({bloque: true});
  const texte = PHRASE.repeat(20);
  const lecture = b.voix.speak(texte, {note: b.note});
  while (b.journal.joues.length < 1) await attendre(1);
  assert(b.journal.synth.length <= 2, 'lecture anticipée : un segment d’avance au plus');
  b.voix.stop();
  const r = await lecture;
  assert.equal(r.statut, 'interrompu_utilisateur'); assert.equal(r.stopped, true);
  await attendre(30);
  assert.equal(b.journal.joues.length, 1, 'aucun segment suivant lancé après l’arrêt');
  assert(b.journal.synth.length <= 2);
  assert(!b.voix.speaking());
  const c = banc({bloque: true});
  const l2 = c.voix.speak(texte, {note: c.note});
  while (c.journal.joues.length < 1) await attendre(1);
  c.voix.stop('voix');
  assert.equal((await l2).statut, 'interrompu_voix');
}

async function v02_repli_au_segment_non_joue() {
  const texte = PHRASE.repeat(8);
  const b = banc({echecSynth: 2});
  const r = await b.voix.speak(texte, {note: b.note});
  const morceaux = segments(texte.trim());
  assert.equal(r.statut, 'termine'); assert.equal(r.fallback, true); assert.equal(r.provider, 'espeak');
  assert.equal(b.journal.serveur[0], morceaux[1], 'le repli local repart au segment 2, pas au début');
  assert.equal(b.journal.serveur.length, morceaux.length - 1, 'le segment 1 déjà joué par Brindille n’est pas relu');
  assert(b.journal.notes.some(n => /segment 2/.test(n) && /faux worker/.test(n)), 'le motif du repli est annoncé');
  assert.match(b.journal.notes.at(-1), /voix de secours du serveur.*Motif : synthèse impossible/);
}

async function v03_bruit_ne_coupe_pas_sans_choix() {
  const b = banc();
  let choisi = false;
  const d = b.voix.detecteurInterruption(() => choisi, 0.09, 4);
  for (let i = 0; i < 100; i++) assert.equal(d.trame(0.5), false, 'interruption désactivée : le bruit ne coupe jamais');
  choisi = true;
  assert.deepEqual([0.5, 0.5, 0.01, 0.5, 0.5, 0.5].map(x => d.trame(x)), [false, false, false, false, false, false], 'pics isolés ignorés');
  assert.equal(d.trame(0.5), true, '4 trames stables au-dessus du seuil : interruption');
}

/* Worker réel (v5694-brindille.js) avec un faux moteur : aucune coupe muette, profil appliqué AVANT la synthèse. */
async function v05_worker_applique_le_profil() {
  const appels = [], messages = [];
  class FauxTts {
    static async load() { return new FauxTts(); }
    constructor() { this.sampleRate = 22050; this.sizes = {}; }
    voice(v) { appels.push(['voice', v]); }
    speak(t) { appels.push(['speak', t.length]); return new Float32Array(10); }
  }
  const self = {location: {href: 'https://cabinet.example.test/agent-courriel/static/v5694-brindille.js', origin: 'https://cabinet.example.test'},
    postMessage: m => messages.push(m)};
  const code = WORKER.replace('await import(', 'await __import(');
  assert.notEqual(code, WORKER);
  new Function('self', '__import', 'URL', 'fetch', code)(self, async () => ({Tts: FauxTts}), URL, async () => { throw new Error('aucun réseau'); });
  await self.onmessage({data: {type: 'init', base: '/agent-courriel/static/brindille/', version: '9.9.99'}});
  assert.equal(messages.at(-1).type, 'ready');
  await self.onmessage({data: {type: 'speak', id: 1, text: 'Phrase fictive.', speed: 1, voix: {pitch: -2, intonation: 0.9, treble: -2, breath: -1}}});
  await self.onmessage({data: {type: 'speak', id: 2, text: 'Autre phrase fictive.', speed: 1, voix: {pitch: -2, intonation: 0.9, treble: -2, breath: -1}}});
  await self.onmessage({data: {type: 'speak', id: 3, text: 'Retour à l’originale.', speed: 1, voix: {pitch: 0, intonation: 1, treble: 0, breath: 0}}});
  assert.deepEqual(appels.map(a => a[0]), ['voice', 'speak', 'speak', 'voice', 'speak'], 'profil appliqué avant la synthèse, une fois par changement');
  assert.deepEqual(appels[0][1], {pitch: -2, intonation: 0.9, treble: -2, breath: -1});
  assert.deepEqual(appels[3][1], {pitch: 0, intonation: 1, treble: 0, breath: 0}, 'valeurs neutres réappliquées pour l’originale');
  const avant = appels.length;
  await self.onmessage({data: {type: 'speak', id: 4, text: 'x'.repeat(2500), speed: 1}});
  assert.equal(messages.at(-1).type, 'error'); assert.match(messages.at(-1).message, /trop long \(2500/);
  assert.equal(appels.slice(avant).filter(a => a[0] === 'speak').length, 0, 'aucune synthèse d’un segment coupé en silence');
}

/* Volet vocal réel (v568.js) dans un DOM minimal simulé : il se charge sans erreur, ses options suivent le profil du compte, et le module
   d'avatar n'est demandé que si un portrait est choisi (« aucun » : aucune balise, aucune requête). */
class Elt {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.dataset = {}; this.attrs = {}; this.hidden = false; this.textContent = '';
    this.ecoutes = {}; this.style = {}; this.classList = {toggle() {}, add() {}, remove() {}}; this.options = []; this.value = ''; this.checked = false; this.parentNode = null; }
  append(...k) { k.forEach(c => { if (c && typeof c === 'object') { c.parentNode = this; if (c.tagName === 'OPTION') { this.options.push(c); if (this.options.length === 1) this.value = c.value; } } this.children.push(c); }); }
  prepend(...k) { this.children.unshift(...k); }
  after(...k) { const p = this.parentNode; if (!p) return; p.children.splice(p.children.indexOf(this) + 1, 0, ...k); k.forEach(c => { c.parentNode = p; }); }
  replaceChildren(...k) { this.children = []; this.append(...k); }
  setAttribute(k, v) { this.attrs[k] = String(v); } getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(t, f) { (this.ecoutes[t] = this.ecoutes[t] || []).push(f); }
  emettre(t) { (this.ecoutes[t] || []).forEach(f => f({target: this})); }
  querySelector() { return null; } querySelectorAll() { return []; } closest() { return null; } contains() { return false; }
  focus() {} scrollTo() {} scrollIntoView() {} get isConnected() { return true; }
}
function tous(n, f, out = []) { if (n && typeof n === 'object') { if (f(n)) out.push(n); (n.children || []).forEach(c => tous(c, f, out)); } return out; }
async function voletVocal(profil) {
  const requetes = [], head = new Elt('head'), body = new Elt('body');
  const document = {head, body, hidden: false, currentScript: {src: 'https://cabinet.example.test/p/static/v568.js?v=9.9.99'}, activeElement: null,
    createElement: t => new Elt(t), addEventListener() {}, querySelectorAll: () => [],
    querySelector: s => (s === '#ws-ai-config' ? {textContent: JSON.stringify({mission_api: 'm567/missions', prefix: '/p', csrf: 'jeton-fictif'})} : null)};
  const fetch = async (url, init = {}) => { requetes.push({url, init}); return {ok: true, json: async () => (url.endsWith('/m567/profile') ? Object.assign({}, profil, JSON.parse(init.body || '{}')) : {})}; };
  const sansAttente = f => (...a) => { const t = f(...a); if (t && t.unref) t.unref(); return t; };
  const window = {matchMedia: () => ({matches: false, addEventListener() {}}), axiorhubProfile567: Promise.resolve(profil), addEventListener() {}};
  window.window = window;
  vm.runInNewContext(fs.readFileSync(path.join(statique, 'v568.js'), 'utf8'), {window, document, fetch, location: {href: 'https://cabinet.example.test/p/aujourdhui'},
    Option: function (t, v) { const o = new Elt('option'); o.textContent = t; o.value = v; return o; }, URL, JSON, Promise, Math, Date, Object, Array, String, Number, Set, Map, Error,
    setTimeout: sansAttente(setTimeout), setInterval: sansAttente(setInterval), clearTimeout, clearInterval, crypto: {randomUUID: () => '00000000-0000-4000-8000-000000000000'},
    navigator: {}, console: {info() {}, log() {}, warn() {}, error() {}}});
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await attendre(5);
  return {window, head, body, requetes};
}
async function v06_volet_avatar_charge_seulement_si_choisi() {
  const aucun = await voletVocal({name: 'Pilote', avatar: 'icone', voix_brindille: 'posee', interruption_vocale: false});
  assert.equal(tous(aucun.head, n => n.tagName === 'SCRIPT').length, 0, 'aucun portrait : aucun module d’avatar demandé');
  assert.equal(aucun.requetes.filter(r => /avatar/.test(r.url)).length, 0);
  const selects = tous(aucun.body, n => n.tagName === 'SELECT');
  const choixVoix = selects.find(s => s.options.some(o => o.value === 'posee')), choixAvatar = selects.find(s => s.options.some(o => o.value === 'portrait_a'));
  assert(choixVoix && choixAvatar, 'options Voix et Avatar présentes dans le volet');
  assert.equal(choixVoix.value, 'posee'); assert.equal(choixAvatar.value, 'aucun');
  const caseInterruption = tous(aucun.body, n => n.tagName === 'INPUT' && n.type === 'checkbox')[0];
  assert.equal(caseInterruption.checked, false, 'Interrompre en parlant : désactivé par défaut');
  const avecPortrait = await voletVocal({name: 'Pilote', avatar: 'portrait_b', voix_brindille: 'originale', interruption_vocale: false});
  const scripts = tous(avecPortrait.head, n => n.tagName === 'SCRIPT');
  assert.equal(scripts.length, 1); assert.equal(scripts[0].src, '/p/static/v5696-avatar.js?v=9.9.99');
  avecPortrait.window.axiorhubAvatar = {creer: cle => ({element: Object.assign(new Elt('figure'), {cle}), bouche() {}})};
  scripts[0].onload();
  await attendre(5);
  const zone = tous(avecPortrait.body, n => n.className === 'v96-portrait-zone')[0];
  assert.equal(zone.hidden, false); assert.equal(zone.children[0].cle, 'portrait_b');
  choixAvatar.value = 'portrait_a'; choixAvatar.emettre('change');
  await attendre(5);
  const envoi = aucun.requetes.find(r => r.url === '/p/api440/m567/profile' && r.init.method === 'POST');
  assert.deepEqual(JSON.parse(envoi.init.body), {avatar: 'portrait_a'}, 'le choix est enregistré dans le profil du compte');
  assert.equal(envoi.init.headers['X-CSRF-Token'], 'jeton-fictif');
}

/* Moteur réel (WASM embarqué), seulement sur demande : les deux profils donnent deux signaux valides et différents. */
async function v05_moteur_reel() {
  if (process.env.AXIORHUB_ESSAI_BRINDILLE_NODE !== '1') return 'non exercé (AXIORHUB_ESSAI_BRINDILLE_NODE=1 pour l’exercer)';
  const dir = path.join(statique, 'brindille');
  const {Tts} = await import(pathToFileURL(path.join(dir, 'tts.js')).href);
  const tts = await Tts.load(pathToFileURL(dir + '/').href, async url => { const b = fs.readFileSync(fileURLToPath(url)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); });
  const phrase = 'Maître Durand plaide devant le tribunal de Valbrune le douze mars deux mille vingt-six.';
  tts.voice({}); const a = tts.speak(phrase, 1);
  tts.voice({pitch: -2, intonation: 0.9, treble: -2, breath: -1}); const b = tts.speak(phrase, 1);
  assert.equal(tts.sampleRate, 22050);
  assert(a.length > 22050 && b.length > 22050);
  assert(a.some(x => Math.abs(x) > 0.01) && b.some(x => Math.abs(x) > 0.01));
  assert(a.length !== b.length || a.some((x, i) => x !== b[i]), 'le profil posé change le signal');
  return 'exercé : originale ' + a.length + ' échantillons, posée ' + b.length;
}

/* Chaque scénario est borné dans le temps (un lecteur qui ne rend jamais la main échoue) ; tous sont exécutés, les échecs listés. */
const borne = (nom, f) => Promise.race([f(), attendre(20000).then(() => { throw new Error(nom + ' : la lecture ne rend pas la main en 20 s'); })]);
(async () => {
  const scenarios = [v01_lecture_longue, v05_profil_transmis_au_worker, v02_erreur_annoncee_sans_termine, v02_reprise_au_segment_3,
    v03_arret_sans_segment_suivant, v02_repli_au_segment_non_joue, v03_bruit_ne_coupe_pas_sans_choix, v05_worker_applique_le_profil,
    v06_volet_avatar_charge_seulement_si_choisi];
  const echecs = [], sorties = {};
  for (const f of scenarios) {
    try { sorties[f.name] = await borne(f.name, f); } catch (e) { echecs.push(f.name + ' : ' + (e && e.message || e)); }
  }
  if (echecs.length) { echecs.forEach(x => console.error(x)); console.log('lecture5696 : ÉCHEC (' + echecs.length + ' scénario(s))'); process.exit(1); }
  const moteur = await v05_moteur_reel();
  console.log('lecture5696 : ok (9 000 caractères en ' + sorties.v01_lecture_longue + ' segments, dernier joué ; erreur annoncée ; reprise au segment 3 ; '
    + 'arrêt ; repli ; bruit sans choix ; profil au worker ; moteur réel ' + moteur + ')');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
