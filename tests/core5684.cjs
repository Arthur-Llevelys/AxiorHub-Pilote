/* 5.6.84 : logique du plugin sous Node : empreinte, cadence, miroir (OF04, OF06), lots bornés, origine des écritures (OF18), accusés. La dernière ligne de sortie est comparée à Python. */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const Core = require(process.argv[2]);

for (const text of ['', 'abc', 'Prix fixé à 12 €', 'x'.repeat(1000)]) assert.strictEqual(Core.sha256(text), crypto.createHash('sha256').update(text, 'utf8').digest('hex'));
assert.strictEqual(Core.clean('Ligne\r\n'), 'Ligne');
assert.strictEqual(Core.clean(null), '');

/* cadence : deux secondes de calme, dix secondes au plus en saisie continue */
const pacer = new Core.Pacer({quiet: 2000, maxWait: 10000});
assert.strictEqual(pacer.due(0), false);
pacer.touch(1000);
assert.strictEqual(pacer.due(2500), false);
assert.strictEqual(pacer.due(3000), true);
for (let t = 1500; t < 11000; t += 500) pacer.touch(t);
assert.strictEqual(pacer.due(11000), true);
pacer.reset();
assert.strictEqual(pacer.due(99999), false);

/* OF04 : saisie pendant l'instantané initial, rien n'est perdu */
const m = new Core.Mirror();
m.startBuffer();
m.note('1', 'pendant la lecture');
assert.strictEqual(Object.keys(m.blocks).length, 0);
m.load([{id: '1', text: 'avant'}, {id: '2', text: 'second'}]);
assert.strictEqual(m.blocks['1'].text, 'pendant la lecture');
assert.ok(m.isDirty());
let batch = m.takeBatch(200, 100000);
assert.deepStrictEqual(batch.blocks.map(b => b.block_id), ['P-1', 'P-2']);
assert.strictEqual(batch.blocks[0].text_hash, Core.sha256('pendant la lecture'));
assert.strictEqual(m.isDirty(), false);

/* même texte : aucun envoi ; retour à l'identique après annulation : envoyé */
assert.strictEqual(m.update('1', 'pendant la lecture'), false);
assert.strictEqual(m.update('1', 'corrigé'), true);
assert.strictEqual(m.update('1', 'pendant la lecture'), true);
m.takeBatch(200, 100000);

/* OF06 : paragraphe disparu du document, identifiant retiré, pas d'ancre fantôme */
const hash = m.snapshotHash();
m.load([{id: '1', text: 'pendant la lecture'}]);
batch = m.takeBatch(200, 100000);
assert.deepStrictEqual(batch.removed, ['P-2']);
assert.notStrictEqual(m.snapshotHash(), hash);
assert.strictEqual(m.blocks['2'], undefined);

/* OF08 : un bloc qui change de nature est renvoyé */
m.load([{id: '1', text: 'pendant la lecture', kind: 'table_cell'}]);
assert.ok(m.isDirty());
m.takeBatch(200, 100000);

/* lot borné : la suite part au lot suivant, sans coupe silencieuse */
const big = new Core.Mirror();
big.load(Array.from({length: 5}, (_, i) => ({id: String(i), text: 'x'.repeat(40)})));
let first = big.takeBatch(2, 100000);
assert.strictEqual(first.blocks.length, 2);
assert.strictEqual(first.more, true);
first = big.takeBatch(200, 50);
assert.strictEqual(first.blocks.length, 1);
assert.strictEqual(first.more, true);
first = big.takeBatch(200, 100000);
assert.strictEqual(first.blocks.length, 2);
assert.strictEqual(first.more, false);

/* OF18 : écriture de l'IA reconnue un court instant seulement */
const origins = new Core.Origins(8000);
origins.expect('cmd', ['P-1'], 1000);
assert.strictEqual(origins.of('P-1', 2000), 'cmd');
assert.strictEqual(origins.of('P-2', 2000), null);
assert.strictEqual(origins.of('P-1', 9001), null);

/* accusé : jamais « appliqué » sans relecture */
assert.strictEqual(Core.ackStatus([]), 'failed');
assert.strictEqual(Core.ackStatus([{status: 'applied'}, {status: 'applied'}]), 'applied');
assert.strictEqual(Core.ackStatus([{status: 'applied'}, {status: 'conflict'}]), 'partial');
assert.strictEqual(Core.ackStatus([{status: 'mixed_format'}]), 'conflict');
assert.strictEqual(Core.ackStatus([{status: 'error'}]), 'failed');

console.log(Core.sha256('Prix fixé à 12 €, soit 𝟏𝟐.'));
