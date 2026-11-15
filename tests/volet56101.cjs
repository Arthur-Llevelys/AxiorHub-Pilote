/* 5.6.101 (lot V) : logique pure du volet (corps des actions, contrôles, voisin, bascule de format), sous Node, sans navigateur. */
'use strict';
const assert = require('assert');
const v = require('../agent/static/v56101-volet.js');

// barre identique pour toutes les natures, dans cet ordre
assert.deepStrictEqual(v.ACTIONS, ['valider', 'modifier', 'instruction', 'ecarter']);

// clé d'idempotence : forme acceptée par les services (16 à 80 caractères [A-Za-z0-9_-]), tirée à chaque fois
const k1 = v.nouvelleCle(), k2 = v.nouvelleCle();
assert.match(k1, /^[A-Za-z0-9_-]{16,80}$/);
assert.notStrictEqual(k1, k2);
assert.strictEqual(v.nouvelleCle(() => new Array(16).fill(255)), 'v101-' + 'ff'.repeat(16));

// corps : champs fixes du serveur, clé, texte / consigne / motif / report ; jamais d'autre champ
const retenir = { api: 'm56100/relecture/p-1/decision', corps: { action: 'retenir', revision: 'r1' }, texte: 'corps' };
assert.deepStrictEqual(v.corpsAction(retenir, { texte: 'Bonjour' }, 0, 'cle-0000000000000001'),
  { action: 'retenir', revision: 'r1', cle: 'cle-0000000000000001', corps: 'Bonjour' });
const corriger = { corps: { revision: 'r2' }, champ: 'instruction' };
assert.deepStrictEqual(v.corpsAction(corriger, { consigne: '  porter   le prix  ' }, 0, 'k'), { revision: 'r2', cle: 'k', instruction: 'porter le prix' });
const rejeter = { corps: { action: 'rejeter', revision: 'n1' }, motif: 'motif' };
assert.strictEqual(v.corpsAction(rejeter, { motif: ' doublon ' }, 0, 'k').motif, 'doublon');
const reporter = { corps: { action: 'reporter', revision: 'r' }, jusqu_a: 86400 };
assert.strictEqual(v.corpsAction(reporter, {}, 1000 * 1000, 'k').jusqu_a, 1000 + 86400);
const relancer = { corps: { key: 'a'.repeat(64) }, champ: 'instruction', choix: { nom: 'fournisseur', options: [['local', 'Local']] } };
assert.strictEqual(v.corpsAction(relancer, { consigne: 'plus court', choix: 'local' }, 0, 'k').fournisseur, 'local');
const correction = { corps: { id: 'ech-1', version: 3 } };
assert.deepStrictEqual(v.corpsAction(correction, { date: '2030-01-02', motif: 'erreur de calcul' }, 0, 'k'),
  { id: 'ech-1', version: 3, cle: 'k', new_due: '2030-01-02', reason: 'erreur de calcul' });

// contrôles : mêmes bornes que les services
assert.strictEqual(v.controle(retenir, { texte: '  ' }), 'texte_requis');
assert.strictEqual(v.controle(corriger, { consigne: 'ok' }), 'consigne_courte');
assert.strictEqual(v.controle(corriger, { consigne: 'x'.repeat(2001) }), 'consigne_courte');
assert.strictEqual(v.controle(corriger, { consigne: 'Porter le prix' }), '');
assert.strictEqual(v.controle(rejeter, { motif: 'a' }), 'motif_requis');
assert.strictEqual(v.controle(correction, { date: '02/01/2030', motif: 'motif valable' }), 'texte_requis');
assert.strictEqual(v.controle(correction, { date: '2030-01-02', motif: '' }), 'motif_requis');

// voisin dans l'ordre des lignes visibles, sans doublon
const cles = ['echeance:a', 'file:b', 'file:b', 'document:c'];
assert.strictEqual(v.voisin(cles, 'file:b', 1), 'document:c');
assert.strictEqual(v.voisin(cles, 'file:b', -1), 'echeance:a');
assert.strictEqual(v.voisin(cles, 'document:c', 1), null);
assert.strictEqual(v.voisin(cles, 'echeance:a', -1), null);
assert.strictEqual(v.voisin(cles, 'absent', 1), 'echeance:a');

// bascule HTML / texte brut : seul le paramètre « format » change
assert.strictEqual(v.avecFormat('/p/api440/m5689/accueil/courriel?id=p-1&quoi=envoi&format=html', 'texte'), '/p/api440/m5689/accueil/courriel?id=p-1&quoi=envoi&format=texte');
assert.strictEqual(v.avecFormat('/x?format=texte&id=1', 'html'), '/x?format=html&id=1');

// état d'un bouton
assert.strictEqual(v.etatBouton({ etat: 'actif' }), 'actif');
assert.strictEqual(v.etatBouton({ etat: 'office' }), 'actif');
assert.strictEqual(v.etatBouton({ etat: 'lien', href: '/x' }), 'lien');
assert.strictEqual(v.etatBouton({ etat: 'indisponible' }), 'indisponible');
assert.strictEqual(v.etatBouton(undefined), 'indisponible');
console.log('volet56101 : ok');
