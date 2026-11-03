/* 5.6.89 : transport des observations sous Node, sans navigateur ni éditeur : réservation et accusé (B08), segmentation (B09), origine par empreinte (B10), bornes en saisie longue (OP01).
   Le serveur est un faux : il refuse, accepte ou perd la réponse selon le scénario. La dernière ligne de sortie est relue par Python. */
'use strict';
const assert = require('assert');
const Core = require(process.argv[2]);

const STAMP = {at: 1000};

function body(batch, number) {
  return {event_id: 'ev-' + number, client_seq: number, upsert_blocks: batch.blocks, removed_block_ids: batch.removed, limited_blocks: batch.limits, origin: batch.command ? 'ai_command' : 'local_user', command_id: batch.command};
}

/* Faux serveur idempotent : une révision par événement, comme office_bridge5684.observe */
function Server() { this.revision = 0; this.events = {}; this.analyses = 0; }
Server.prototype.post = function (event) {
  if (!this.events[event.event_id]) {
    this.revision += 1;
    this.events[event.event_id] = this.revision;
    if (event.upsert_blocks.length) this.analyses += 1;
  }
  return {revision: this.events[event.event_id]};
};

/* B08 : POST rejeté avant réception : même événement, sans nouvelle frappe */
{
  const m = new Core.Mirror(), t = new Core.Transport(m, {base: 100, ceiling: 1000}), s = new Server();
  m.load([{id: '1', text: 'Clause fictive modifiée'}]);
  const first = t.next(body);
  assert.strictEqual(first.upsert_blocks.length, 1);
  assert.strictEqual(t.failed(undefined, undefined, STAMP.at), 'retry');
  assert.strictEqual(t.state, 'retry_pending');
  assert.ok(m.isDirty());
  m.load([{id: '1', text: 'Clause fictive modifiée'}]);                 /* réconciliation identique : le lot n'est pas perdu */
  assert.strictEqual(t.due(STAMP.at + 50), false);                      /* attente croissante */
  assert.strictEqual(t.due(STAMP.at + 100), true);
  const again = t.next(body);
  assert.strictEqual(again, first);                                     /* même identifiant, même séquence, même contenu */
  s.post(again);
  assert.deepStrictEqual(t.acknowledged(), {more: false, recovered: true});
  assert.strictEqual(t.state, 'reconciled');
  assert.strictEqual(m.isDirty(), false);
  assert.strictEqual(s.revision, 1);
  assert.strictEqual(t.next(body), null);
}

/* B08 : serveur ayant reçu le POST, réponse perdue : pas de double révision ni de double analyse */
{
  const m = new Core.Mirror(), t = new Core.Transport(m, {base: 100}), s = new Server();
  m.load([{id: '1', text: 'Prix fictif'}]);
  const event = t.next(body);
  s.post(event);
  t.failed(undefined, undefined, 0);                                    /* réponse jamais reçue */
  s.post(t.next(body));
  t.acknowledged();
  assert.strictEqual(s.revision, 1);
  assert.strictEqual(s.analyses, 1);
}

/* B08 : une saisie pendant l'envoi et une suppression en attente ne sont pas confondues avec le lot en vol */
{
  const m = new Core.Mirror(), t = new Core.Transport(m);
  m.load([{id: '1', text: 'Un'}, {id: '2', text: 'Deux'}]);
  t.next(body);
  t.acknowledged();
  m.update('1', 'Un corrigé');
  const event = t.next(body);
  assert.strictEqual(event.upsert_blocks[0].text, 'Un corrigé');
  m.update('1', 'Un recorrigé pendant l’envoi');                        /* nouvelle frappe : elle n'appartient pas au lot réservé */
  m.load([{id: '1', text: 'Un recorrigé pendant l’envoi'}]);            /* le bloc 2 disparaît pendant l'envoi */
  t.failed(503, undefined, 0);
  assert.strictEqual(t.next(body), event);
  t.acknowledged();
  assert.ok(m.dirty['1']);                                              /* la saisie récente reste à envoyer */
  const next = t.next(body);
  assert.strictEqual(next.upsert_blocks[0].text, 'Un recorrigé pendant l’envoi');
  assert.deepStrictEqual(next.removed_block_ids, ['P-2']);
  t.acknowledged();
  assert.strictEqual(m.isDirty(), false);
}

/* B08 : une suppression non acquittée survit à l'échec */
{
  const m = new Core.Mirror(), t = new Core.Transport(m);
  m.load([{id: '1', text: 'Un'}, {id: '2', text: 'Deux'}]);
  t.next(body); t.acknowledged();
  m.load([{id: '1', text: 'Un'}]);
  const event = t.next(body);
  assert.deepStrictEqual(event.removed_block_ids, ['P-2']);
  t.failed(undefined, undefined, 0);
  assert.ok(m.isDirty());
  assert.deepStrictEqual(t.next(body).removed_block_ids, ['P-2']);
  t.acknowledged();
  assert.strictEqual(m.isDirty(), false);
}

/* chap. 20 : un refus définitif n'est jamais rejoué à l'identique ; une limite est annoncée au serveur et les autres blocs repartent */
{
  const m = new Core.Mirror(), t = new Core.Transport(m);
  m.load([{id: '1', text: 'Refusé'}]);
  t.next(body);
  assert.strictEqual(t.failed(422, 'observation_invalide', 0), 'blocked');
  assert.strictEqual(t.state, 'blocked');
  assert.strictEqual(t.inflight, null);
  const report = t.next(body);
  assert.deepStrictEqual(report.limited_blocks, [{block_id: 'P-1', chars: 6, reason: 'observation_invalide'}]);
  assert.strictEqual(report.upsert_blocks.length, 0);
  t.acknowledged();
  assert.strictEqual(t.next(body), null);                               /* plus rien à renvoyer tant que le texte ne change pas */
  m.update('1', 'Corrigé');
  assert.strictEqual(t.next(body).upsert_blocks.length, 1);
}

/* B09 : long paragraphe segmenté, réassemblable, avec une seule ancre ; les autres blocs passent */
{
  const m = new Core.Mirror(), t = new Core.Transport(m, {maxBlocks: 200, maxChars: 100000});
  const long = 'x'.repeat(100001);
  m.load([{id: '1', text: long}, {id: '2', text: 'Court'}]);
  const received = [];
  let lots = 0, event;
  while ((event = t.next(body))) {
    lots += 1;
    assert.ok(lots < 10, 'attente infinie');
    received.push(...event.upsert_blocks);
    t.acknowledged();
  }
  const parts = received.filter(b => b.segment);
  assert.ok(parts.length >= 4);
  assert.strictEqual(parts.map(b => b.text).join(''), long);
  assert.ok(parts.every(b => b.block_id === 'P-1' && b.segment.total_hash === Core.sha256(long) && b.segment.count === parts.length && b.text.length <= 30000 && b.text_hash === Core.sha256(b.text)));
  assert.deepStrictEqual(parts.map(b => b.segment.index), parts.map((_, i) => i));
  assert.ok(received.some(b => b.block_id === 'P-2' && b.text === 'Court'));
  assert.strictEqual(m.isDirty(), false);
}

/* B09 : un paragraphe au-delà de la limite produit une limite visible, jamais une attente ; le reste continue ; les parties déjà reçues ne repartent pas */
{
  const m = new Core.Mirror(), t = new Core.Transport(m);
  m.load([{id: '1', text: 'y'.repeat(1300000)}, {id: '2', text: 'Court'}]);
  const event = t.next(body);
  assert.deepStrictEqual(event.limited_blocks, [{block_id: 'P-1', chars: 1300000, reason: 'paragraphe_trop_long'}]);
  assert.deepStrictEqual(event.upsert_blocks.map(b => b.block_id), ['P-2']);
  t.acknowledged();
  assert.strictEqual(m.isDirty(), false);
}
{
  const m = new Core.Mirror(), t = new Core.Transport(m, {maxBlocks: 1, maxChars: 100000});
  m.load([{id: '1', text: 'z'.repeat(70000)}]);
  const a = t.next(body);
  t.failed(503, undefined, 0);
  assert.strictEqual(t.next(body), a);                                  /* segment 0 rejoué à l'identique */
  t.acknowledged();
  const b = t.next(body);
  assert.strictEqual(b.upsert_blocks[0].segment.index, 1);              /* reprise au segment suivant */
  t.acknowledged();
  m.update('1', 'w'.repeat(70000));                                     /* le texte change : tout l'envoi repart, sous la nouvelle empreinte */
  const c = t.next(body);
  assert.strictEqual(c.upsert_blocks[0].segment.index, 0);
}

/* B09 : jamais de coupe entre les deux moitiés d'un caractère */
{
  const m = new Core.Mirror(), t = new Core.Transport(m, {maxChars: 100000});
  m.load([{id: '1', text: 'a'.repeat(29999) + '𝟏'.repeat(20000)}]);
  let event;
  const texts = [];
  while ((event = t.next(body))) { texts.push(...event.upsert_blocks.map(b => b.text)); t.acknowledged(); }
  assert.ok(texts.every(x => !/[\ud800-\udbff]$/.test(x) && !/^[\udc00-\udfff]/.test(x)));
  assert.strictEqual(texts.join(''), 'a'.repeat(29999) + '𝟏'.repeat(20000));
}

/* B10 : correction manuelle 500 ms après l'IA : pas une écriture de l'IA ; texte exact : l'IA, une seule fois ; annuler puis rétablir n'est plus attribué */
{
  const before = 'Prix fictif : 40 000 euros', after = 'Prix fictif : 45 000 euros', manual = 'Prix fictif : 46 000 euros';
  const blocks = {1: {text: before}};
  const operations = [{op_id: 'op-1', action: 'replace_text', anchor: {block_id: 'P-1', expected_text: '40 000', expected_hash: ''}, replacement: '45 000'},
                      {op_id: 'op-2', action: 'insert_after_block', anchor: {block_id: 'P-1', expected_text: before}, replacement: 'Clause nouvelle.\n'},
                      {op_id: 'op-3', action: 'add_comment', anchor: {block_id: 'P-1', expected_text: before}, replacement: 'À relire'}];
  const writes = Core.expectedWrites(operations, blocks);
  assert.ok(writes.some(w => w.block_id === 'P-1' && w.hash === Core.sha256(after)));
  assert.ok(writes.some(w => w.block_id === null && w.hash === Core.sha256('Clause nouvelle.')));
  assert.strictEqual(writes.length, 2);                                 /* un commentaire ne change aucun texte */
  const o = new Core.Origins(8000);
  o.expect('cmd', writes, 1000);
  assert.strictEqual(o.of('P-1', 1500, Core.sha256(manual)), null);
  assert.strictEqual(o.of('P-1', 1500), null);
  assert.strictEqual(o.of('P-1', 1600, Core.sha256(after)), 'cmd');
  assert.strictEqual(o.of('P-9', 1650, Core.sha256('Clause nouvelle.')), 'cmd');
  assert.strictEqual(o.of('P-1', 1700, Core.sha256(before)), null);     /* annulation par l'avocat */
  assert.strictEqual(o.of('P-1', 1800, Core.sha256(after)), null);      /* rétablissement : l'empreinte a déjà servi */
  o.expect('ancienne', ['P-1'], 1000);                                  /* attente sans empreinte : aucune preuve d'auteur, quelle que soit l'heure */
  assert.strictEqual(o.of('P-1', 1100, Core.sha256(after)), null);
  o.expect('périmée', [{block_id: 'P-1', hash: Core.sha256(after)}], 1000);
  assert.strictEqual(o.of('P-1', 9001, Core.sha256(after)), null);      /* le délai ne sert qu'au nettoyage */
  assert.strictEqual(o.items.length, 0);
}

/* B10 : une saisie humaine en attente ne part pas sous l'origine d'une commande */
{
  const m = new Core.Mirror(), t = new Core.Transport(m);
  m.load([{id: '1', text: 'Un'}, {id: '2', text: 'Deux'}]);
  t.next(body); t.acknowledged();
  m.update('2', 'Deux, corrigé par l’avocat');
  m.update('1', 'Un, écrit par la commande');
  m.attribute('1', 'cmd');
  const seen = [];
  let event;
  while ((event = t.next(body))) { seen.push([event.origin, event.command_id, event.upsert_blocks.map(b => b.block_id)]); t.acknowledged(); }
  assert.deepStrictEqual(seen.sort((a, b) => a[0].localeCompare(b[0])), [['ai_command', 'cmd', ['P-1']], ['local_user', null, ['P-2']]]);
  m.update('1', 'Écrit par la commande');
  m.attribute('1', 'cmd');
  m.update('1', 'Puis corrigé à la main');                              /* le texte a changé : l'attribution tombe */
  event = t.next(body);
  assert.strictEqual(event.origin, 'local_user');
}

/* OP01 : dix minutes de saisie, une frappe par seconde, réponse lente : un seul lot en vol, mémoire bornée, aucune révision perdue */
{
  const m = new Core.Mirror(), t = new Core.Transport(m), s = new Server();
  m.load(Array.from({length: 50}, (_, i) => ({id: String(i), text: 'Paragraphe ' + i})));
  t.next(body); s.post(t.inflight.body); t.acknowledged();
  let longest = 0;
  for (let second = 1; second <= 600; second += 1) {
    m.update(String(second % 50), 'Paragraphe modifié ' + second);
    longest = Math.max(longest, Object.keys(m.dirty).length);
    if (second % 7 === 0) {                                             /* réponse du serveur toutes les sept secondes */
      if (t.inflight) { s.post(t.inflight.body); t.acknowledged(); }
      t.next(body);
    }
  }
  while (t.inflight || m.isDirty()) { if (!t.inflight) t.next(body); s.post(t.inflight.body); t.acknowledged(); }
  assert.ok(longest <= 50);                                             /* par paragraphe, pas par frappe */
  assert.strictEqual(m.isDirty(), false);
  assert.ok(Object.keys(s.events).length < 100);
}

console.log('transport-ok');
