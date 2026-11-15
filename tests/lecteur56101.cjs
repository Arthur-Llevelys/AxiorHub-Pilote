/* 5.6.101 (lot R, R01-R05, V101-09) : simulation Node du contrôleur du lecteur commun ``agent/static/v56100-lecteur.js`` (le vrai fichier).
 * Faux service, fausse interface, faux émetteur : aucun navigateur, aucun réseau. Exerce le contrat lecteur → mesure (``ax101:ouvert``,
 * ``ax101:decide`` : type et commande seulement, jamais d'identifiant ni de texte), dix décisions de quatre types à la suite sans
 * rechargement (ouverture automatique du suivant, fin annoncée), le rejet avec motif, la correction demandée à l'IA (l'élément reste ouvert)
 * et la décision d'une proposition en conflit (rien n'est rouvert ni écrasé).
 * node tests/lecteur56101.cjs <racine du dépôt> ; dernière ligne : « lecteur56101 : ok … ».
 */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');

const racine = process.argv[2] || path.resolve(__dirname, '..');
const { creerLecteur } = require(path.resolve(racine, 'agent', 'static', 'v56100-lecteur.js'));

const ACTIONS = {
  proposition: ['enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'], imap: ['enregistrer', 'retenir', 'ecarter', 'reporter'],
  gmail: ['enregistrer', 'retenir', 'ecarter', 'reporter', 'deposer'], document: ['valider', 'ecarter', 'corriger', 'reporter'],
  note_ia: ['valider', 'rejeter', 'enregistrer', 'reporter']
};
const TYPES = { p: 'proposition', i: 'imap', g: 'gmail', d: 'document', n: 'note_ia' };
const modele = (id, rev) => ({ id, type: TYPES[id[0]], revision: rev || 'r1', corps: 'Texte confidentiel ' + id, sujet: 'Objet secret ' + id,
                               actions: ACTIONS[TYPES[id[0]]] });
const erreur = (status, code) => Object.assign(new Error('requete_non_confirmee'), { status, code });

function banc(options = {}) {
  const log = [], commandes = [], evenements = [];
  let cles = 0;
  const file = (options.file || []).slice();
  const api = {
    detail(id) { return Promise.resolve(modele(id, options.revisions && options.revisions[id])); },
    section(id, rev, section) { return Promise.resolve({ section, propositions: [] }); },
    commande(id, route, cmd) {
      commandes.push([id, route, cmd]);
      if (options.commande) return options.commande(id, route, cmd);
      const etat = { retenir: 'retenu', valider: 'retenu', ecarter: 'ecarte', rejeter: 'ecarte', reporter: 'reporte' }[cmd.action] || (route === 'deposer' ? 'depose' : 'enregistre');
      return Promise.resolve({ id, etat, revision: 'r2', message: 'Message serveur' });
    }
  };
  const ui = {
    attendre: () => {}, finAttente: () => {}, rendre: d => log.push('rendu-' + d.id), annoncer: (code, texte) => log.push(code + (texte ? ':' + texte : '')),
    retirerContenu: () => log.push('retire'), bloquerCommandes: () => {}, selectionnerOnglet: s => log.push('onglet-' + s), rendreSection: s => log.push('section-' + s),
    appliquerRecu: () => file.shift() || null, aucunSuivant: () => log.push('fin'), fermerEtRestaurer: () => log.push('ferme')
  };
  const l = creerLecteur({ api, ui, confirmerBrouillon: async () => true, nouvelleCle: () => 'cle-' + String(++cles).padStart(16, '0'),
                           emettre: (nom, detail) => evenements.push([nom, detail]) });
  return { l, log, commandes, evenements };
}

const essais = [];
const essai = (nom, f) => essais.push([nom, f]);

essai('contrat : ax101:ouvert à l’affichage, type du contrat seulement', async () => {
  const { l, evenements } = banc();
  for (const id of ['p-1', 'i-1', 'g-1', 'd-1', 'n-1']) assert.equal(await l.ouvrir(id, { forcer: true }), true);
  assert.deepEqual(evenements, [['ax101:ouvert', { type: 'courriel' }], ['ax101:ouvert', { type: 'courriel' }], ['ax101:ouvert', { type: 'gmail' }],
                                ['ax101:ouvert', { type: 'document' }], ['ax101:ouvert', { type: 'note_ia' }]]);
});

essai('contrat : ax101:decide après le reçu, jamais d’identifiant ni de texte', async () => {
  const { l, evenements } = banc();
  await l.ouvrir('d-7');
  assert.equal(await l.decider('valider'), true);
  const decide = evenements.filter(e => e[0] === 'ax101:decide');
  assert.deepEqual(decide, [['ax101:decide', { type: 'document', commande: 'valider' }]]);
  const texte = JSON.stringify(evenements);
  assert.ok(!/d-7|secret|confidentiel|cle-/.test(texte), texte);
  for (const [, detail] of evenements) assert.ok(Object.keys(detail).every(k => k === 'type' || k === 'commande'));
});

essai('contrat : aucun ax101:decide si le serveur refuse ou si le dépôt reste à vérifier', async () => {
  const refus = banc({ commande: async () => { throw erreur(409, 'version_perimee'); } });
  await refus.l.ouvrir('g-1'); assert.equal(await refus.l.decider('ecarter'), false);
  assert.equal(refus.evenements.filter(e => e[0] === 'ax101:decide').length, 0);
  const doute = banc({ commande: async (id) => ({ id, etat: 'a_verifier', revision: 'r1', message: 'Dépôt incertain' }) });
  await doute.l.ouvrir('g-1'); assert.equal(await doute.l.deposer(), false);
  assert.equal(doute.evenements.filter(e => e[0] === 'ax101:decide').length, 0);
  assert.ok(doute.log.includes('a_verifier:Dépôt incertain'));
});

essai('V101-09 : dix éléments de quatre types traités à la suite sans rechargement, fin annoncée', async () => {
  const ordre = ['p-1', 'd-1', 'n-1', 'g-1', 'p-2', 'd-2', 'n-2', 'g-2', 'i-1', 'd-3'];
  const { l, log, commandes, evenements } = banc({ file: ordre.slice(1) });
  await l.ouvrir(ordre[0]);
  const geste = { proposition: 'retenir', imap: 'ecarter', gmail: 'retenir', document: 'valider', note_ia: 'rejeter' };
  for (let i = 0; i < ordre.length; i++) {
    const type = TYPES[l.projet().id[0]];
    assert.equal(l.projet().id, ordre[i]);                                      // le suivant s'est ouvert seul
    assert.equal(await l.decider(geste[type], null, { motif: 'Motif fictif' }), true);
  }
  assert.equal(commandes.length, 10);
  assert.equal(log[log.length - 1].startsWith('fin') || log.includes('fin'), true);
  assert.equal(evenements.filter(e => e[0] === 'ax101:decide').length, 10);
  assert.equal(evenements.filter(e => e[0] === 'ax101:ouvert').length, 10);
  assert.deepEqual([...new Set(evenements.map(e => e[1].type))].sort(), ['courriel', 'document', 'gmail', 'note_ia']);
  assert.equal(commandes.find(c => c[2].action === 'rejeter')[2].motif, 'Motif fictif');
});

essai('R03 : rejeter sans motif n’appelle pas le serveur', async () => {
  const { l, commandes, log } = banc();
  await l.ouvrir('n-1');
  assert.equal(await l.decider('rejeter', null, { motif: ' ' }), false);
  assert.equal(commandes.length, 0);
  assert.ok(log.includes('motif_requis'));
});

essai('R02 : correction demandée, l’élément reste ouvert et la clé est rejouée à l’identique', async () => {
  let n = 0;
  const { l, commandes, log, evenements } = banc({ commande: async (id, route) => {
    if (route === 'corriger' && ++n === 1) throw erreur(503, 'file_attente_pleine');
    return { id, etat: 'correction_demandee', revision: 'r1', message: 'Correction demandée' };
  } });
  await l.ouvrir('d-1');
  assert.equal(await l.corriger('Porter le prix à deux mille euros.', 'Le prix'), false);
  assert.equal(await l.corriger('Porter le prix à deux mille euros.', 'Le prix'), true);
  assert.equal(commandes[0][2].cle, commandes[1][2].cle);
  assert.equal(commandes[1][1], 'corriger');
  assert.equal(l.projet().id, 'd-1');
  assert.ok(log.includes('section-propositions'));
  assert.equal(evenements.filter(e => e[0] === 'ax101:decide').length, 0);
  assert.equal(await l.corriger('ab'), false);
});

essai('R02 : proposition en conflit — message, rien n’est rouvert ; appliquée — nouvelle version rouverte sans nouvel ax101:ouvert', async () => {
  const conflit = banc({ commande: async () => { throw Object.assign(erreur(409, 'conflit_edition_concurrente'), { message: 'Rien n’a été écrasé.' }); } });
  await conflit.l.ouvrir('d-1');
  assert.equal(await conflit.l.deciderProposition('prop1', 'accepter'), false);
  assert.ok(conflit.log.includes('conflit_edition_concurrente:Rien n’a été écrasé.'));
  assert.equal(conflit.log.filter(x => x === 'rendu-d-1').length, 1);
  const ok = banc({ commande: async (id) => ({ id, etat: 'appliquee', revision: 'r9', message: 'Appliquée' }) });
  await ok.l.ouvrir('d-1');
  assert.equal(await ok.l.deciderProposition('prop1', 'accepter'), true);
  assert.equal(ok.log.filter(x => x === 'rendu-d-1').length, 2);
  assert.equal(ok.evenements.filter(e => e[0] === 'ax101:ouvert').length, 1);
});

(async () => {
  let ok = 0;
  for (const [nom, f] of essais) {
    try { await f(); ok++; console.log('ok   ' + nom); } catch (err) { console.log('ÉCHEC ' + nom + '\n' + (err && err.stack || err)); }
  }
  console.log('lecteur56101 : ok ' + ok + '/' + essais.length);
  process.exit(ok === essais.length ? 0 : 1);
})();
