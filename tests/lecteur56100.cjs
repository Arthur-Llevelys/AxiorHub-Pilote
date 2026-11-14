/* 5.6.100 (lot B, R09, V100-03) : simulation Node du contrôleur du lecteur commun ``agent/static/v56100-lecteur.js`` (le vrai fichier).
 * Faux service et fausse interface : aucun navigateur, aucun réseau. Exerce le rejet d'une réponse tardive (liste et onglets), la
 * conservation de la saisie (409, réseau, fermeture, passage au suivant), le rejeu idempotent (même clé), le reçu d'enregistrement,
 * l'état « À vérifier » d'un dépôt, l'enchaînement sur l'élément suivant et le report calculé dans le fuseau du poste.
 * node tests/lecteur56100.cjs <racine du dépôt> ; dernière ligne : « lecteur56100 : ok … ».
 */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');

const racine = process.argv[2] || path.resolve(__dirname, '..');
const { creerLecteur, echeanceReport } = require(path.resolve(racine, 'agent', 'static', 'v56100-lecteur.js'));

const modele = (id, rev) => ({ id, revision: rev || 'r1', corps: 'Texte ' + id, actions: ['enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'] });
function reportee() {
  let res, rej;
  const promesse = new Promise((ok, non) => { res = ok; rej = non; });
  return { promesse, res, rej };
}
const erreur = (status) => Object.assign(new Error('requete_non_confirmee'), { status });

function banc(options = {}) {
  const log = [], commandes = [], appels = [], sections = [];
  let cles = 0;
  const api = {
    detail(id, signal) { appels.push([id, signal]); return options.detail ? options.detail(id) : Promise.resolve(modele(id)); },
    section(id, rev, section, signal) { sections.push([id, rev, section, signal]); return options.section ? options.section(id, section) : Promise.resolve({ texte: 'reçu ' + id }); },
    commande(id, route, cmd) { commandes.push([id, route, cmd]); return options.commande ? options.commande(id, route, cmd) : Promise.resolve({ id, etat: route === 'enregistrer' ? 'enregistre' : 'retenu', revision: 'r2', corps: cmd.corps }); }
  };
  const ui = {
    attendre: id => log.push('attente-' + id), finAttente: () => {}, rendre: d => log.push('projet-' + d.id + '@' + d.revision), annoncer: s => log.push(s),
    retirerContenu: () => log.push('retire'), bloquerCommandes: () => {}, appliquerRecu: () => options.suivant || null,
    aucunSuivant: () => log.push('vide'), fermerEtRestaurer: () => log.push('ferme'), selectionnerOnglet: s => log.push('onglet-' + s),
    rendreSection: (s, d) => log.push('section-' + s + ':' + d.texte)
  };
  const l = creerLecteur({ api, ui, confirmerBrouillon: async (motif) => { log.push('confirmer-' + motif); return options.confirme === true; },
                           nouvelleCle: () => 'cle-' + String(++cles).padStart(16, '0') });
  return { l, log, commandes, appels, sections };
}

const essais = [];
const essai = (nom, f) => essais.push([nom, f]);

essai('R09 : A arrive après B — seule la sélection courante change le panneau', async () => {
  const a = reportee(), b = reportee(), { l, log, appels } = banc({ detail: id => (id === 'A' ? a.promesse : b.promesse) });
  const pa = l.ouvrir('A'), pb = l.ouvrir('B');
  await Promise.resolve();
  assert.equal(appels[0][1].aborted, true);
  b.res(modele('B')); assert.equal(await pb, true);
  a.res(modele('A')); assert.equal(await pa, false);
  assert.deepEqual(log.filter(x => x.startsWith('projet-')), ['projet-B@r1']);
});

essai('R09 : erreur de A après le succès de B — aucun message ni effacement', async () => {
  const a = reportee(), { l, log } = banc({ detail: id => (id === 'A' ? a.promesse : Promise.resolve(modele('B'))) });
  const pa = l.ouvrir('A'); assert.equal(await l.ouvrir('B'), true);
  a.rej(erreur(404)); assert.equal(await pa, false);
  assert.ok(!log.includes('retire') && !log.includes('introuvable'));
  assert.equal(l.projet().id, 'B');
});

essai('R09 : onglet secondaire tardif ignoré après changement de sélection', async () => {
  const s = reportee(), { l, log } = banc({ section: () => s.promesse });
  await l.ouvrir('A');
  const ps = l.voirSection('message');
  await l.ouvrir('B', { forcer: true });
  s.res({ texte: 'ancien' }); await ps;
  assert.ok(!log.some(x => x.startsWith('section-')));
});

essai('R09 : 409 conserve la saisie et le nouvel essai réutilise la clé', async () => {
  const { l, log, commandes } = banc({ commande: async () => { throw erreur(409); } });
  await l.ouvrir('A'); l.saisir('Mon texte');
  assert.equal(await l.decider('retenir'), false); assert.equal(await l.decider('retenir'), false);
  assert.equal(l.sale(), true);
  assert.equal(commandes[0][2].corps, 'Mon texte');
  assert.equal(commandes[0][2].cle, commandes[1][2].cle);
  assert.equal(log.filter(x => x === 'version_perimee').length, 2);
});

essai('R09 : coupure réseau pendant la décision — texte conservé, même clé, puis succès sans doublon', async () => {
  let n = 0;
  const { l, commandes } = banc({ commande: async (id, route, cmd) => { if (++n === 1) throw new TypeError('réseau'); return { id, etat: 'retenu' }; } });
  await l.ouvrir('A'); l.saisir('Relu');
  assert.equal(await l.decider('retenir'), false);
  assert.equal(l.sale(), true);
  assert.equal(await l.decider('retenir'), true);
  assert.equal(commandes[0][2].cle, commandes[1][2].cle);
});

essai('R02 : une saisie modifiée n’est jamais écrasée par Écarter, Fermer ou le changement de ligne', async () => {
  const { l, log, commandes } = banc({ suivant: 'B' });
  await l.ouvrir('A'); l.saisir('Saisie');
  assert.equal(await l.decider('ecarter'), false);
  assert.equal(await l.fermer(), false);
  assert.equal(await l.ouvrir('B'), false);
  assert.equal(commandes.length, 0);
  assert.equal(l.projet().id, 'A');
  assert.ok(log.includes('confirmer-changer'));
  assert.equal(await l.decider('retenir'), true);              // Retenir envoie le texte relu : aucune perte
  assert.equal(commandes[0][2].corps, 'Saisie');
  assert.equal(commandes[0][2].revision, 'r1');
  assert.ok(log.includes('projet-B@r1'));                       // élément suivant ouvert sans « Retour »
});

essai('P1 : dépôt incertain — le projet reste affiché, rien n’est retiré', async () => {
  const { l, log } = banc({ commande: async (id) => ({ id, etat: 'a_verifier' }) });
  await l.ouvrir('A');
  assert.equal(await l.deposer(), false);
  assert.ok(log.includes('a_verifier'));
  assert.ok(!log.includes('retire'));
  assert.equal(l.projet().id, 'A');
});

essai('P1 : Déposer est une action distincte qui envoie le texte relu, jamais « envoyer »', async () => {
  const { l, commandes, log } = banc({ commande: async (id) => ({ id, etat: 'depose' }) });
  await l.ouvrir('A'); l.saisir('Texte relu');
  assert.equal(await l.deposer(), true);
  assert.deepEqual(commandes.map(c => c[1]), ['deposer']);
  assert.equal(commandes[0][2].corps, 'Texte relu');
  assert.ok(log.includes('vide'));                               // fin de file
});

essai('R09 : Enregistrer attend le reçu, puis la décision part sur la version confirmée', async () => {
  const p = reportee();
  const { l, commandes } = banc({ commande: (id, route, cmd) => (route === 'enregistrer' ? p.promesse : Promise.resolve({ id, etat: 'retenu' })) });
  await l.ouvrir('A');
  l.saisir('À conserver');
  const sauvegarde = l.enregistrer();
  assert.equal(l.sale(), true);
  assert.equal(await l.fermer(), false);
  p.res({ id: 'A', revision: 'r2', corps: 'À conserver', etat: 'enregistre' });
  assert.equal(await sauvegarde, true);
  assert.equal(l.sale(), false);
  assert.equal(await l.decider('retenir'), true);
  assert.equal(commandes[1][2].revision, 'r2');
});

essai('R09 : reçu d’enregistrement incomplet — la saisie reste « non enregistrée »', async () => {
  const { l, log } = banc({ commande: async () => ({ etat: 'enregistre' }) });
  await l.ouvrir('A'); l.saisir('Texte');
  assert.equal(await l.enregistrer(), false);
  assert.equal(l.sale(), true);
  assert.ok(log.includes('sauvegarde_non_confirmee'));
});

essai('R04 : 404 à la décision (droits retirés) — contenu retiré de l’écran', async () => {
  const { l, log } = banc({ commande: async () => { throw erreur(404); } });
  await l.ouvrir('A');
  assert.equal(await l.decider('ecarter'), false);
  assert.ok(log.includes('retire') && log.includes('introuvable'));
  assert.equal(l.projet(), null);
});

essai('V100-03 : dix décisions à la suite sans navigation ni « Retour »', async () => {
  const ids = Array.from({ length: 10 }, (_, i) => 'P' + i);
  let suivant = 1;
  const b = banc();
  b.l = creerLecteur({ api: { detail: async id => modele(id), section: async () => ({}), commande: async (id) => ({ id, etat: 'retenu' }) },
    ui: { attendre() {}, finAttente() {}, rendre: d => b.log.push('projet-' + d.id), annoncer() {}, retirerContenu() {}, bloquerCommandes() {},
          appliquerRecu: () => (suivant < ids.length ? ids[suivant++] : null), aucunSuivant: () => b.log.push('vide'), fermerEtRestaurer() {},
          selectionnerOnglet() {}, rendreSection() {} },
    confirmerBrouillon: async () => false, nouvelleCle: () => 'cle-0000000000000000' + Math.random().toString(16).slice(2) });
  await b.l.ouvrir(ids[0]);
  for (let i = 0; i < 10; i++) assert.equal(await b.l.decider('retenir'), true);
  assert.deepEqual(b.log.filter(x => x.startsWith('projet-')), ids.map(i => 'projet-' + i));
  assert.equal(b.log[b.log.length - 1], 'vide');
});

essai('P1 : report calculé dans le fuseau du poste (demain 9 h, lundi 9 h)', async () => {
  const mercredi = new Date(2026, 10, 11, 16, 30).getTime();      // mercredi 11 novembre 2026, 16 h 30 (heure locale du poste)
  const demain = new Date(echeanceReport('demain', mercredi) * 1000);
  assert.deepEqual([demain.getDate(), demain.getHours(), demain.getMinutes()], [12, 9, 0]);
  const lundi = new Date(echeanceReport('lundi', mercredi) * 1000);
  assert.deepEqual([lundi.getDay(), lundi.getDate(), lundi.getHours()], [1, 16, 9]);
  const apresMidi = echeanceReport('apres_midi', mercredi) * 1000;
  assert.ok(apresMidi > mercredi);
});

(async () => {
  let ok = 0;
  for (const [nom, f] of essais) {
    try { await f(); ok++; } catch (err) { console.error('ÉCHEC : ' + nom + '\n' + (err && err.stack || err)); process.exitCode = 1; }
  }
  console.log('lecteur56100 : ' + (process.exitCode ? 'échec' : 'ok') + ' ' + ok + '/' + essais.length);
})();
