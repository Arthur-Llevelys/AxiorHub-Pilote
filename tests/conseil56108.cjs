/* 5.6.108 (lot K) : logique pure de v56108-conseil.js (question de jugement, visibilité de l'option), sous Node, sans navigateur.
   Lit les exemples sur l'entrée standard (JSON : liste de textes) et écrit les verdicts de ``estJugement`` : le test Python compare avec
   ``conseil56108.est_question_de_jugement`` (même motif des deux côtés). */
'use strict';
const assert = require('assert');
const k = require('../agent/static/v56108-conseil.js');

const pret = { pret: true, peut_lancer: true };
assert.strictEqual(k.visible(pret, 'DOS-001', 'Faut-il assigner ZORVAL ?', true), true);
assert.strictEqual(k.visible(pret, 'DOS-001', 'Résume ce courriel.', true), false);         // pas une question de jugement : option absente
assert.strictEqual(k.visible(pret, 'DOS-001', 'Résume ce courriel.', false), true);         // demande de consultation : jugement par nature
assert.strictEqual(k.visible(pret, '', 'Faut-il assigner ?', true), false);                 // sans dossier : absente
assert.strictEqual(k.visible({ pret: false, peut_lancer: true }, 'DOS-001', 'Quelle stratégie ?', true), false);   // conseil non configuré
assert.strictEqual(k.visible({ pret: true, peut_lancer: false }, 'DOS-001', 'Quelle stratégie ?', true), false);   // rôle qui ne peut pas lancer
assert.strictEqual(k.visible(null, 'DOS-001', 'Quelle stratégie ?', true), false);
assert.strictEqual(k.sansAccents('Stratégie À ÉVALUER’'), "strategie a evaluer'");

let brut = '';
process.stdin.on('data', (d) => { brut += d; });
process.stdin.on('end', () => {
  const textes = JSON.parse(brut || '[]');
  process.stdout.write(JSON.stringify(textes.map(k.estJugement)));
});
