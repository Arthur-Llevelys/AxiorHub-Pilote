/* AxiorHub : logique du plugin EuroOffice sans dépendance à l'éditeur (cadence, miroir, comparaison, préparation des observations). Testée sous Node. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AxOfficeCore = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var K = [];
  (function () {
    var n = 2, count = 0;
    function frac(x) { return ((x - Math.floor(x)) * 4294967296) | 0; }
    while (count < 64) {
      var prime = true;
      for (var i = 2; i * i <= n; i++) if (n % i === 0) { prime = false; break; }
      if (prime) K[count++] = frac(Math.pow(n, 1 / 3));
      n++;
    }
  }());

  function utf8(text) {
    var out = [], s = String(text);
    for (var i = 0; i < s.length; i++) {
      var c = s.codePointAt(i);
      if (c > 0xffff) i++;
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return out;
  }

  function sha256(text) {
    var bytes = utf8(text), len = bytes.length, H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    var hi = Math.floor(len / 536870912), lo = (len << 3) >>> 0;
    bytes.push((hi >>> 24) & 255, (hi >>> 16) & 255, (hi >>> 8) & 255, hi & 255, (lo >>> 24) & 255, (lo >>> 16) & 255, (lo >>> 8) & 255, lo & 255);
    var w = new Array(64);
    function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
    for (var off = 0; off < bytes.length; off += 64) {
      for (var t = 0; t < 16; t++) w[t] = (bytes[off + 4 * t] << 24) | (bytes[off + 4 * t + 1] << 16) | (bytes[off + 4 * t + 2] << 8) | bytes[off + 4 * t + 3];
      for (t = 16; t < 64; t++) {
        var s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3), s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (t = 0; t < 64; t++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25), ch = (e & f) ^ (~e & g), t1 = (h + S1 + ch + K[t] + w[t]) | 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22), maj = (a & b) ^ (a & c) ^ (b & c), t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    return H.map(function (x) { return ('00000000' + (x >>> 0).toString(16)).slice(-8); }).join('');
  }

  function clean(text) { return String(text == null ? '' : text).replace(/[\r\n]+$/, ''); }

  /* Cadence du cahier : deux secondes après la dernière notification, dix secondes au plus pendant une saisie continue. */
  function Pacer(options) {
    this.quiet = options.quiet || 2000;
    this.maxWait = options.maxWait || 10000;
    this.first = 0;
    this.last = 0;
  }
  Pacer.prototype.touch = function (now) { if (!this.first) this.first = now; this.last = now; };
  Pacer.prototype.due = function (now) {
    if (!this.first) return false;
    return now - this.last >= this.quiet || now - this.first >= this.maxWait;
  };
  Pacer.prototype.reset = function () { this.first = 0; this.last = 0; };

  /* Miroir local : blocs, empreintes et blocs modifiés. Un paragraphe n'a d'identifiant que le temps de la session. */
  function Mirror() { this.blocks = {}; this.order = []; this.dirty = {}; this.removed = {}; this.buffer = null; }
  Mirror.prototype.startBuffer = function () { this.buffer = []; };
  Mirror.prototype.note = function (id, text) {
    if (this.buffer) { this.buffer.push([String(id), clean(text)]); return; }
    this.update(String(id), clean(text));
  };
  Mirror.prototype.update = function (id, text) {
    var block = this.blocks[id];
    if (block && block.text === text) return false;
    if (!block) { block = {id: id, kind: 'paragraph', patchable: true}; this.blocks[id] = block; this.order.push(id); }
    block.text = text;
    block.hash = sha256(text);
    this.dirty[id] = true;
    return true;
  };
  /* Instantané complet : les événements reçus pendant la lecture sont rejoués ensuite, aucune saisie n'est perdue (OF04). */
  Mirror.prototype.load = function (rows) {
    var seen = {}, self = this;
    this.order = [];
    rows.forEach(function (row) {
      var id = String(row.id), text = clean(row.text), block = self.blocks[id];
      seen[id] = true;
      self.order.push(id);
      if (!block || block.text !== text || block.kind !== row.kind) {
        self.blocks[id] = {id: id, kind: row.kind || 'paragraph', patchable: row.patchable !== false, text: text, hash: sha256(text)};
        self.dirty[id] = true;
      } else block.patchable = row.patchable !== false;
    });
    Object.keys(this.blocks).forEach(function (id) {
      if (!seen[id]) { delete self.blocks[id]; delete self.dirty[id]; self.removed[id] = true; }
    });
    var late = this.buffer || [];
    this.buffer = null;
    late.forEach(function (item) { self.update(item[0], item[1]); });
  };
  Mirror.prototype.structureHash = function () { return sha256(this.order.join('|')); };
  Mirror.prototype.snapshotHash = function () {
    var self = this;
    return sha256(this.order.map(function (id) { return (self.blocks[id] || {}).hash || ''; }).join('|'));
  };
  /* Lot borné : le reste part au lot suivant ; la coupe n'est jamais silencieuse (``more``). */
  Mirror.prototype.takeBatch = function (maxBlocks, maxChars) {
    var ids = Object.keys(this.dirty), out = [], chars = 0, self = this, more = false;
    ids.forEach(function (id) {
      var block = self.blocks[id];
      if (!block) { delete self.dirty[id]; return; }
      if (out.length >= maxBlocks || chars + block.text.length > maxChars) { more = true; return; }
      chars += block.text.length;
      out.push({block_id: 'P-' + id, semantic_tag: null, kind: block.kind, text: block.text, text_hash: block.hash, patchable: block.patchable, ordinal: self.order.indexOf(id)});
      delete self.dirty[id];
    });
    var removed = Object.keys(this.removed).map(function (id) { return 'P-' + id; });
    this.removed = {};
    return {blocks: out, removed: removed, more: more};
  };
  Mirror.prototype.isDirty = function () { return Object.keys(this.dirty).length > 0 || Object.keys(this.removed).length > 0; };

  /* Origine d'un changement : une commande de l'IA qui vient d'écrire ce bloc n'est pas une saisie de l'avocat (OF18). */
  function Origins(ttl) { this.ttl = ttl || 8000; this.items = []; }
  Origins.prototype.expect = function (commandId, blockIds, now) { this.items.push({id: commandId, blocks: blockIds, until: now + this.ttl}); };
  Origins.prototype.of = function (blockId, now) {
    this.items = this.items.filter(function (x) { return x.until > now; });
    for (var i = 0; i < this.items.length; i++) if (this.items[i].blocks.indexOf(blockId) >= 0) return this.items[i].id;
    return null;
  };

  /* Ack calculé à partir des résultats relus dans l'éditeur : jamais « appliqué » sans relecture ; partiel dit ce qui a réellement changé. */
  function ackStatus(results) {
    var done = results.filter(function (r) { return r.status === 'applied'; }).length;
    if (!results.length) return 'failed';
    if (done === results.length) return 'applied';
    if (done > 0) return 'partial';
    var conflict = results.some(function (r) { return r.status === 'conflict' || r.status === 'mixed_format'; });
    return conflict ? 'conflict' : 'failed';
  }

  return {sha256: sha256, clean: clean, Pacer: Pacer, Mirror: Mirror, Origins: Origins, ackStatus: ackStatus};
}));
