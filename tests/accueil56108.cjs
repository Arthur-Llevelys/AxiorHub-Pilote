/* 5.6.108 (lot A) : logique pure de v56108-accueil.js (dernière valeur connue d'« Actuellement », reçu des demandes), sous Node, sans navigateur. */
'use strict';
const assert = require('assert');
const a = require('../agent/static/v56108-accueil.js');

// « Actuellement » : une source muette ne remplace pas une valeur connue
const ok = (n) => ({ etat: 'ok', nombre: String(n), garde: '' });
assert.strictEqual(a.aGarder(ok(6), { etat: 'panne', nombre: '–', garde: '' }), true);
assert.strictEqual(a.aGarder(ok(6), { etat: 'retard', nombre: '–', garde: '' }), true);
assert.strictEqual(a.aGarder(ok(6), { etat: 'partiel', nombre: '0', garde: '' }), true);        // liste partielle annoncée à zéro : faux zéro
assert.strictEqual(a.aGarder(ok(6), { etat: 'partiel', nombre: '4', garde: '' }), false);       // partielle mais chiffrée : valeur nouvelle montrée
assert.strictEqual(a.aGarder(ok(6), { etat: 'ok', nombre: '0', garde: '' }), false);            // vrai zéro d'une source qui répond
assert.strictEqual(a.aGarder(ok(6), { etat: 'vide', nombre: '0', garde: '' }), false);
assert.strictEqual(a.aGarder({ etat: 'vide', nombre: '0', garde: '' }, { etat: 'panne', nombre: '–', garde: '' }), true);   // zéro connu gardé aussi
assert.strictEqual(a.aGarder({ etat: 'panne', nombre: '–', garde: '' }, { etat: 'panne', nombre: '–', garde: '' }), false); // rien de connu
assert.strictEqual(a.aGarder({ etat: 'partiel', nombre: '0', garde: '' }, { etat: 'panne', nombre: '–', garde: '' }), false);
assert.strictEqual(a.aGarder({ etat: 'panne', nombre: '6', garde: '16 h 41' }, { etat: 'panne', nombre: '–', garde: '' }), true); // déjà gardée
assert.strictEqual(a.aGarder(null, { etat: 'panne', nombre: '–', garde: '' }), false);
assert.strictEqual(a.aGarder(ok(6), null), false);

// étapes : cinq, écrites ; code du panneau d'abord, état de la mission ensuite ; inconnu : rien d'affirmé
assert.deepStrictEqual(a.ETAPES.map(e => e[0]), ['recue', 'attente', 'en_cours', 'resultat', 'echec']);
const attendu = { recue: 'recue', attente: 'attente', choix_requis: 'attente', suspendue: 'attente', plan_propose: 'attente', en_cours: 'en_cours',
                  reponse_prete: 'resultat', projet_a_relire: 'resultat', sans_production: 'resultat', interrompue: 'echec', annulee: 'echec', refusee: 'echec' };
for (const [code, etape] of Object.entries(attendu)) assert.strictEqual(a.etape({ panneau: { code } }), etape, code);
assert.strictEqual(a.etape({ state: 'running' }), 'en_cours');
assert.strictEqual(a.etape({ state: 'error', panneau: { code: 'refusee' } }), 'echec');
assert.strictEqual(a.etape({ state: 'inconnu' }), '');
assert.strictEqual(a.etape(null), '');
assert.strictEqual(a.libelleEtape('resultat'), 'Résultat');
assert.strictEqual(a.libelleEtape('echec'), 'Échec');

// numéro du reçu : tiré de l'identifiant enregistré par le serveur, jamais inventé
assert.strictEqual(a.numero('0123456789abcdef0123456789abcdef'), '0123-4567');
assert.strictEqual(a.numero('ABCDEF0123456789'), 'ABCD-EF01');
assert.strictEqual(a.numero(''), '');
assert.strictEqual(a.numero('p-123'), '');
assert.strictEqual(a.numero('0123456789abcde'), '');                 // trop court
assert.strictEqual(a.numero('<img src=x>0123456789abcdef'), '');
console.log('accueil56108.cjs : ok');
