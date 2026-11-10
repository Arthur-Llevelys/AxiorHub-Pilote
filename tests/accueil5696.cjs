/* 5.6.96 (lot accueil, F02, I01) : heure et salutation du bandeau d'Aujourd'hui, avec le vrai v5696-accueil.js chargé sous Node (aucun navigateur).
 * Vérifie : seuils de la salutation dans le fuseau du compte, changement de minute, passage de minuit (la date change sans recharger la page),
 * prénom absent (formule seule), délai jusqu'à la minute suivante. Dates relatives au jour du test. node tests/accueil5696.cjs
 */
const path = require('node:path');
const assert = require('node:assert');
const f = require(path.join(__dirname, '..', 'agent', 'static', 'v5696-accueil.js'));

const M = { 'formule.matin': 'Bonjour', 'formule.apres_midi': 'Bon après-midi', 'formule.soir': 'Bonsoir', salut: '{formule} {prenom}',
  phrase: '{salut}, nous sommes le {date}, il est {heure}.', heure: '{h} h {mm}' };
const aujourdhui = new Date();
/* Instant UTC qui tombe à hh:mm à Paris aujourd'hui (le décalage est lu par Intl, jamais supposé). */
function aParis(h, mm) {
  const base = Date.UTC(aujourdhui.getUTCFullYear(), aujourdhui.getUTCMonth(), aujourdhui.getUTCDate(), h, mm);
  const l = f.heureLocale(new Date(base), 'Europe/Paris');
  const decale = (l.h * 60 + parseInt(l.mm, 10)) - (h * 60 + mm);
  return new Date(base - decale * 60000);
}
const r = {};
const reglage = { fuseau: 'Europe/Paris', lang: 'fr', prenom: 'Claire' };

[[4, 'soir'], [5, 'matin'], [11, 'matin'], [12, 'apres_midi'], [17, 'apres_midi'], [18, 'soir'], [23, 'soir']].forEach(([h, mot]) => assert.strictEqual(f.formule(h), mot, 'seuil ' + h));
r.seuils = true;

const matin = f.texteBandeau(aParis(9, 5), reglage, M);
assert.match(matin.texte, /^Bonjour Claire, nous sommes le .+, il est 9 h 05\.$/);
const minute = f.texteBandeau(new Date(aParis(9, 5).getTime() + 60000), reglage, M);
assert.match(minute.texte, /il est 9 h 06\.$/);
r.changement_de_minute = matin.texte !== minute.texte;

const avant = f.texteBandeau(aParis(23, 59), reglage, M);
const apres = f.texteBandeau(new Date(aParis(23, 59).getTime() + 60000), reglage, M);
assert.match(avant.texte, /^Bonsoir Claire/);
assert.match(apres.texte, /il est 0 h 00\.$/);
r.minuit_change_la_date = avant.jour !== apres.jour && avant.texte.split(', il est')[0] !== apres.texte.split(', il est')[0];
assert.ok(r.minuit_change_la_date);

const tokyo = f.texteBandeau(aParis(9, 5), { fuseau: 'Asia/Tokyo', lang: 'fr', prenom: '' }, M);
assert.ok(!tokyo.texte.includes('Claire'));
assert.match(tokyo.texte, /^(Bonjour|Bon après-midi|Bonsoir), nous sommes le /);
r.fuseau_du_compte = tokyo.datetime !== matin.datetime;

assert.strictEqual(f.prochainDelai(0), 60050);
assert.strictEqual(f.prochainDelai(59990), 60);
r.delai_minute = true;

process.stdout.write(JSON.stringify(r, null, 1) + '\n');
