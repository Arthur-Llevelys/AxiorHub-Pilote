/* 5.6.108 (lot Q) : « Dicter la réponse » — logique de ``agent/static/v56108-dictee.js`` (le vrai fichier) sous Node, sans navigateur ni micro.
 * Exerce : date et montant posés seulement si la valeur reconnue est valide (même forme que le serveur), sinon refus (affichée pour
 * correction) ; texte ajouté au curseur sans rien effacer, sélection remplacée seulement si elle existe, longueur bornée sans coupe.
 * node tests/dictee56108.cjs <racine du dépôt> ; dernière ligne : « dictee56108 : ok … ».
 */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');

const racine = process.argv[2] || path.resolve(__dirname, '..');
const { normaliser, inserer } = require(path.resolve(racine, 'agent', 'static', 'v56108-dictee.js'));
let n = 0;
const essai = (nom, f) => { f(); n += 1; };

essai('dates dictées valides', () => {
  assert.equal(normaliser('date', 'le 14 janvier 2027'), '2027-01-14');
  assert.equal(normaliser('date', '1er mars 2027.'), '2027-03-01');
  assert.equal(normaliser('date', 'premier août 2026'), '2026-08-01');
  assert.equal(normaliser('date', '14/01/2027'), '2027-01-14');
  assert.equal(normaliser('date', '2027-01-14'), '2027-01-14');
  assert.equal(normaliser('date', '3 de marzo de 2027'), '2027-03-03');
  assert.equal(normaliser('date', '5 June 2027'), '2027-06-05');
});

essai('dates dictées non valides : rien n’est posé', () => {
  for (const t of ['30 février 2027', 'demain', 'mi-janvier', '14 janvier', '31/04/2027', '', 'le 14 brumaire 2027']) assert.equal(normaliser('date', t), null, t);
});

essai('montants dictés valides, même forme que le serveur', () => {
  assert.equal(normaliser('montant', '125 000 euros'), '125000.00');
  assert.equal(normaliser('montant', '125 000,50 €'), '125000.50');
  assert.equal(normaliser('montant', '125.000,5 euros HT'), '125000.50');
  assert.equal(normaliser('montant', '0'), '0.00');
  assert.equal(normaliser('montant', '4000.'), '4000.00');
});

essai('montants dictés non valides : rien n’est posé', () => {
  for (const t of ['cent vingt-cinq mille euros', '1.234', 'environ 3000', '12,345', 'NaN', '-50']) assert.equal(normaliser('montant', t), null, t);
});

essai('texte : tel quel ; vide : rien', () => {
  assert.equal(normaliser('texte', ' Société ZORVAL '), 'Société ZORVAL');
  assert.equal(normaliser('texte', '   '), null);
});

essai('insertion au curseur sans effacer la saisie', () => {
  assert.deepEqual(inserer('Maintenance du site', 11, 11, 'mensuelle', false, 0), { valeur: 'Maintenance mensuelle du site', curseur: 21 });
  assert.equal(inserer('ZORVAL', null, null, 'SAS', false, 0).valeur, 'ZORVAL SAS');                 // sans curseur connu : à la fin
  assert.equal(inserer('', 0, 0, 'KERVILAC', false, 0).valeur, 'KERVILAC');
  assert.equal(inserer('Prix ferme', 5, 10, 'révisable', false, 0).valeur, 'Prix révisable');        // sélection de l'avocat remplacée
  assert.equal(inserer('A', 1, 1, 'ligne 1\nligne 2', false, 0).valeur, 'A ligne 1 ligne 2');        // champ d'une ligne
  assert.equal(inserer('A', 1, 1, 'ligne 1\nligne 2', true, 0).valeur, 'A ligne 1\nligne 2');        // texte long
  assert.equal(inserer('x'.repeat(10), 10, 10, 'trop long', false, 12), null);                       // refusé, jamais coupé
});

console.log('dictee56108 : ok (%d essais)', n);
