/* 5.6.101 (lot M, M01, M02) : simulation Node du script de mesure ``agent/static/v56101-chrono.js`` (le vrai fichier).
 * Horloge, document, navigateur et réseau simulés : aucun navigateur, aucun réseau. Les événements du lecteur (lot R, contrat
 * ax101:ouvert / ax101:decide) sont simulés ici. Exerce : durée mesurée entre l'ouverture et la décision, durées de plus de 30 minutes
 * écartées, décision sans ouverture ignorée, aucun champ du lecteur recopié hors type et commande, sendBeacon en text/plain avec le jeton
 * CSRF dans le corps, repli fetch avec l'en-tête, et rien d'écouté ni d'envoyé sans data-chrono-actif="1".
 * node tests/chrono56101.cjs <racine du dépôt> ; dernière ligne : « chrono56101 : ok … ».
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const racine = process.argv[2] || path.resolve(__dirname, '..');
const fichier = path.resolve(racine, 'agent', 'static', 'v56101-chrono.js');
const { creerChrono, creerEnvoi, LIMITE_MS } = require(fichier);

function banc() {
  let t = 1000, cles = 0;
  const envois = [];
  const c = creerChrono({ maintenant: () => t, envoyer: m => envois.push(m), nouvelleCle: () => 'cle-' + String(++cles).padStart(16, '0') });
  return { c, envois, avancer: ms => { t += ms; } };
}

const essais = [];
const essai = (nom, f) => essais.push([nom, f]);

essai('M02 : durée entre ouverture et décision, type et commande seulement', () => {
  const b = banc();
  b.c.ouvert({ type: 'courriel', id: 'r-123', objet: 'Renvoi DOS-001' });
  b.avancer(42500);
  const m = b.c.decide({ type: 'courriel', commande: 'retenir', id: 'r-123', objet: 'Renvoi DOS-001', dossier: 'DOS-001' });
  assert.deepEqual(m, { type: 'courriel', commande: 'retenir', duree_ms: 42500, cle: 'cle-0000000000000001' });
  assert.deepEqual(Object.keys(b.envois[0]).sort(), ['cle', 'commande', 'duree_ms', 'type']);
  assert.equal(b.c.enCours(), '');
});

essai('M02 : plus de 30 minutes (élément laissé ouvert) : écarté, rien envoyé', () => {
  const b = banc();
  b.c.ouvert({ type: 'document' });
  b.avancer(LIMITE_MS + 1);
  assert.equal(b.c.decide({ type: 'document', commande: 'valider' }), null);
  b.c.ouvert({ type: 'document' });
  b.avancer(LIMITE_MS);
  assert.equal(b.c.decide({ type: 'document', commande: 'valider' }).duree_ms, LIMITE_MS);   // 30 minutes pile : gardée
  assert.equal(b.envois.length, 1);
});

essai('M02 : décision sans ouverture, type différent, commande ou type inconnus : ignorés', () => {
  const b = banc();
  assert.equal(b.c.decide({ type: 'courriel', commande: 'retenir' }), null);
  b.c.ouvert({ type: 'gmail' }); b.avancer(1000);
  assert.equal(b.c.decide({ type: 'courriel', commande: 'retenir' }), null);           // pas le même élément
  assert.equal(b.c.decide({ type: 'gmail', commande: 'retenir' }), null);              // l'ouverture a été consommée
  b.c.ouvert({ type: 'gmail' }); b.avancer(1000);
  assert.equal(b.c.decide({ type: 'gmail', commande: 'Objet <b>' }), null);
  b.c.ouvert({ type: 'piece_secrete' }); b.avancer(1000);
  assert.equal(b.c.decide({ type: 'piece_secrete', commande: 'retenir' }), null);
  assert.equal(b.envois.length, 0);
});

essai('M02 : nouvel élément ouvert sans décision : le chronomètre repart', () => {
  const b = banc();
  b.c.ouvert({ type: 'note_ia' }); b.avancer(5000);
  b.c.ouvert({ type: 'note_ia' }); b.avancer(2000);
  assert.equal(b.c.decide({ type: 'note_ia', commande: 'rejeter' }).duree_ms, 2000);
});

essai('V101-11 : dix décisions, dix mesures, clés toutes différentes', () => {
  const b = banc();
  for (let i = 1; i <= 10; i++) { b.c.ouvert({ type: 'courriel' }); b.avancer(i * 1000); b.c.decide({ type: 'courriel', commande: 'retenir' }); }
  assert.deepEqual(b.envois.map(m => m.duree_ms), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(s => s * 1000));
  assert.equal(new Set(b.envois.map(m => m.cle)).size, 10);
});

class FauxBlob { constructor(parts, o) { this.texte = parts.join(''); this.type = o.type; } }

essai('M01 : sendBeacon en POST text/plain, jeton CSRF existant dans le corps', () => {
  const balises = [];
  const envoi = creerEnvoi({ prefix: '/agent-courriel', csrf: 'jeton-essai', Blob: FauxBlob,
                             navigator: { sendBeacon: (url, blob) => { balises.push([url, blob]); return true; } },
                             fetch: () => { throw new Error('fetch ne doit pas servir'); } });
  assert.equal(envoi({ type: 'courriel', commande: 'retenir', duree_ms: 1200, cle: 'cle-0000000000000001' }), 'balise');
  assert.equal(balises[0][0], '/agent-courriel/api440/m56101/chrono/mesure');
  assert.equal(balises[0][1].type, 'text/plain;charset=UTF-8');
  assert.deepEqual(JSON.parse(balises[0][1].texte), { type: 'courriel', commande: 'retenir', duree_ms: 1200, cle: 'cle-0000000000000001', jeton: 'jeton-essai' });
});

essai('M01 : balise refusée : fetch keepalive avec l’en-tête CSRF, sans jeton dans le corps', () => {
  const appels = [];
  const envoi = creerEnvoi({ prefix: '/p', csrf: 'jeton-essai', Blob: FauxBlob, navigator: { sendBeacon: () => false },
                             fetch: (url, o) => { appels.push([url, o]); return Promise.resolve({ ok: true }); } });
  assert.equal(envoi({ type: 'gmail', commande: 'deposer', duree_ms: 3000, cle: 'cle-0000000000000002' }), 'fetch');
  const [url, o] = appels[0];
  assert.equal(url, '/p/api440/m56101/chrono/mesure');
  assert.equal(o.method, 'POST'); assert.equal(o.keepalive, true);
  assert.equal(o.headers['X-CSRF-Token'], 'jeton-essai');
  assert.equal(JSON.parse(o.body).jeton, undefined);
});

/* Le vrai fichier exécuté comme dans une page : faux document qui garde les écouteurs, vraie balise <script> simulée. */
function page(attributs) {
  const ecouteurs = {}, balises = [];
  const metas = { 'axiorhub-prefix': '/agent-courriel', 'axiorhub-csrf': 'jeton-page' };
  const script = { getAttribute: n => (Object.prototype.hasOwnProperty.call(attributs, n) ? attributs[n] : null) };
  let t = 0;
  const document = {
    currentScript: script, readyState: 'complete',
    addEventListener: (nom, f) => { (ecouteurs[nom] = ecouteurs[nom] || []).push(f); },
    querySelector: sel => { const m = /meta\[name="([^"]+)"\]/.exec(sel); return m ? { content: metas[m[1]] || '' } : null; }
  };
  const fenetre = { document, performance: { now: () => t }, navigator: { sendBeacon: (u, b) => { balises.push([u, b]); return true; } } };
  fenetre.window = fenetre;
  const bac = { window: fenetre, document, Blob: FauxBlob, Uint8Array, Math, JSON, Object, String, Date };
  vm.runInNewContext(fs.readFileSync(fichier, 'utf-8'), bac, { filename: 'v56101-chrono.js' });
  const emettre = (nom, detail) => (ecouteurs[nom] || []).forEach(f => f({ type: nom, detail }));
  return { ecouteurs, balises, emettre, avancer: ms => { t += ms; } };
}

essai('M01 : page avec data-chrono-actif="1" : écoute sur document et une balise par décision', () => {
  const p = page({ 'data-chrono-actif': '1' });
  assert.deepEqual(Object.keys(p.ecouteurs).sort(), ['ax101:decide', 'ax101:ouvert']);
  p.emettre('ax101:ouvert', { type: 'document' }); p.avancer(8000);
  p.emettre('ax101:decide', { type: 'document', commande: 'valider' });
  assert.equal(p.balises.length, 1);
  const corps = JSON.parse(p.balises[0][1].texte);
  assert.equal(corps.duree_ms, 8000); assert.equal(corps.jeton, 'jeton-page'); assert.match(corps.cle, /^[0-9a-f]{32}$/);
});

essai('M01 : sans data-chrono-actif="1" (mesure désactivée) : rien écouté, rien envoyé', () => {
  for (const attr of [{}, { 'data-chrono-actif': '0' }]) {
    const p = page(attr);
    assert.deepEqual(p.ecouteurs, {});
    p.emettre('ax101:ouvert', { type: 'courriel' }); p.avancer(1000);
    p.emettre('ax101:decide', { type: 'courriel', commande: 'retenir' });
    assert.equal(p.balises.length, 0);
  }
});

(async () => {
  let ok = 0;
  for (const [nom, f] of essais) {
    try { await f(); ok++; } catch (err) { console.error('ÉCHEC : ' + nom + '\n' + (err && err.stack || err)); process.exitCode = 1; }
  }
  console.log('chrono56101 : ' + (process.exitCode ? 'échec' : 'ok') + ' ' + ok + '/' + essais.length);
})();
