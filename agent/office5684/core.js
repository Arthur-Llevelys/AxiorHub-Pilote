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

  /* 5.6.89 (B09) : un élément d'observation fait 30 000 caractères au plus. Un paragraphe plus long part en segments ; au-delà de MAX_SEGMENTS, il est signalé et les autres blocs continuent. */
  var SEGMENT = 30000, MAX_SEGMENTS = 40;

  function cut(text, size) {
    var parts = [], at = 0;
    while (at < text.length) {
      var end = Math.min(at + size, text.length), code = text.charCodeAt(end - 1);
      if (end < text.length && code >= 0xd800 && code <= 0xdbff) end -= 1;   /* jamais entre les deux moitiés d'un caractère */
      parts.push(text.slice(at, end));
      at = end;
    }
    return parts;
  }

  /* Miroir local : blocs, empreintes et blocs modifiés. Un paragraphe n'a d'identifiant que le temps de la session.
     5.6.89 (B08) : un lot reste réservé jusqu'à l'accusé du serveur ; rien ne quitte ``dirty`` ni ``removed`` avant. */
  function Mirror() { this.blocks = {}; this.order = []; this.dirty = {}; this.removed = {}; this.buffer = null; this.pending = null; this.progress = {}; this.authors = {}; this.limits = {}; }
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
    delete this.removed[id];
    delete this.authors[id];       /* le texte a changé : l'auteur connu ne vaut plus */
    delete this.limits[id];
    return true;
  };
  /* Ce bloc porte exactement le texte écrit par cette commande ; il part dans un lot à part, jamais avec une saisie de l'avocat (B10). */
  Mirror.prototype.attribute = function (id, commandId) { if (this.dirty[id]) this.authors[id] = commandId; };
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
        delete self.removed[id];
        delete self.authors[id];
        delete self.limits[id];
      } else block.patchable = row.patchable !== false;
    });
    Object.keys(this.blocks).forEach(function (id) {
      if (!seen[id]) { delete self.blocks[id]; delete self.dirty[id]; delete self.progress[id]; delete self.authors[id]; delete self.limits[id]; self.removed[id] = true; }
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
  /* Lot borné : le reste part au lot suivant ; la coupe n'est jamais silencieuse (``more``). Un lot non acquitté est rendu à l'identique, sans rien retirer :
     les saisies survenues entre-temps restent dans ``dirty`` pour le lot suivant. Un lot porte une seule origine : les écritures d'une commande, ou le reste. */
  Mirror.prototype.takeBatch = function (maxBlocks, maxChars) {
    if (this.pending) return this.pending;
    var ids = Object.keys(this.dirty), out = [], taken = [], chars = 0, self = this, more = false, size = Math.min(maxChars, SEGMENT), lead;
    function row(id, block, text, hash) {
      return {block_id: 'P-' + id, semantic_tag: null, kind: block.kind, text: text, text_hash: hash, patchable: block.patchable, ordinal: self.order.indexOf(id)};
    }
    ids.forEach(function (id) {
      var block = self.blocks[id];
      if (!block) { delete self.dirty[id]; return; }
      var author = self.authors[id] || null;
      if (lead === undefined) lead = author;
      if (author !== lead || out.length >= maxBlocks) { more = true; return; }
      if (block.text.length <= size) {
        if (chars + block.text.length > maxChars) { more = true; return; }
        chars += block.text.length;
        out.push(row(id, block, block.text, block.hash));
        taken.push({id: id, hash: block.hash, done: true});
        return;
      }
      var parts = cut(block.text, size);
      if (parts.length > MAX_SEGMENTS) {           /* limite annoncée, jamais une attente : le texte reste au fichier, les autres blocs continuent */
        self.limits[id] = {block_id: 'P-' + id, chars: block.text.length, reason: 'paragraphe_trop_long', sent: false};
        delete self.dirty[id];
        return;
      }
      var state = self.progress[id];
      if (!state || state.hash !== block.hash) state = self.progress[id] = {hash: block.hash, next: 0};
      var index = state.next;
      while (index < parts.length && out.length < maxBlocks && chars + parts[index].length <= maxChars) {
        var item = row(id, block, parts[index], sha256(parts[index]));
        item.segment = {index: index, count: parts.length, total_hash: block.hash, total_chars: block.text.length};
        chars += parts[index].length;
        out.push(item);
        index += 1;
      }
      if (index < parts.length) more = true;
      if (index > state.next) taken.push({id: id, hash: block.hash, next: index, done: index >= parts.length});
    });
    var removedIds = Object.keys(this.removed), limits = Object.keys(this.limits).filter(function (id) { return !self.limits[id].sent; });
    var batch = {blocks: out, removed: removedIds.map(function (id) { return 'P-' + id; }), more: more, command: lead || null, taken: taken, removedIds: removedIds, limitIds: limits,
                 limits: limits.map(function (id) { var l = self.limits[id]; return {block_id: l.block_id, chars: l.chars, reason: l.reason}; })};
    if (out.length || removedIds.length || limits.length) this.pending = batch;
    return batch;
  };
  /* Accusé valide du serveur : seul ce qui n'a pas changé depuis la réservation sort de la liste des blocs à envoyer. */
  Mirror.prototype.ackBatch = function (batch) {
    if (!batch || batch !== this.pending) return false;
    var self = this;
    batch.taken.forEach(function (t) {
      var block = self.blocks[t.id];
      if (!block || block.hash !== t.hash) return;
      if (t.next !== undefined && self.progress[t.id]) self.progress[t.id].next = t.next;
      if (t.done) { delete self.dirty[t.id]; delete self.progress[t.id]; delete self.authors[t.id]; }
    });
    batch.removedIds.forEach(function (id) { if (!self.blocks[id]) delete self.removed[id]; });
    batch.limitIds.forEach(function (id) { if (self.limits[id]) self.limits[id].sent = true; });
    this.pending = null;
    return true;
  };
  /* Refus définitif du serveur : pas de nouvel essai identique ; les blocs sont signalés et attendent un nouveau texte. */
  Mirror.prototype.rejectBatch = function (batch, code) {
    if (!batch || batch !== this.pending) return false;
    var self = this;
    batch.taken.forEach(function (t) {
      var block = self.blocks[t.id];
      if (!block || block.hash !== t.hash) return;
      self.limits[t.id] = {block_id: 'P-' + t.id, chars: block.text.length, reason: String(code || 'observation_refusee').slice(0, 60), sent: false};
      delete self.dirty[t.id]; delete self.progress[t.id]; delete self.authors[t.id];
    });
    batch.removedIds.forEach(function (id) { if (!self.blocks[id]) delete self.removed[id]; });
    this.pending = null;
    return true;
  };
  Mirror.prototype.isDirty = function () { return Object.keys(this.dirty).length > 0 || Object.keys(this.removed).length > 0 || Object.keys(this.limits).some(function (id) { return !this.limits[id].sent; }, this); };

  /* 5.6.89 (B08) : transport des observations. États du protocole : reserved, sent, acknowledged, retry_pending, blocked, reconciled.
     Un événement garde son identifiant et sa séquence jusqu'à l'accusé ; une panne réseau ou serveur le rejoue, un refus définitif ne le rejoue jamais. */
  function Transport(mirror, options) {
    options = options || {};
    this.mirror = mirror;
    this.maxBlocks = options.maxBlocks || 200;
    this.maxChars = options.maxChars || 100000;
    this.base = options.base || 2000;
    this.ceiling = options.ceiling || 60000;
    this.seq = 0; this.inflight = null; this.failures = 0; this.retryAt = 0; this.state = 'queued';
  }
  Transport.prototype.next = function (make) {
    if (this.inflight) return this.inflight.body;
    var batch = this.mirror.takeBatch(this.maxBlocks, this.maxChars);
    if (!batch.blocks.length && !batch.removed.length && !batch.limits.length) return null;
    this.seq += 1;
    this.inflight = {batch: batch, body: make(batch, this.seq)};
    this.state = 'reserved';
    return this.inflight.body;
  };
  Transport.prototype.sent = function () { this.state = 'sent'; };
  Transport.prototype.due = function (now) { return !!this.inflight && this.state === 'retry_pending' && now >= this.retryAt; };
  Transport.prototype.acknowledged = function () {
    var batch = this.inflight.batch, recovered = this.failures > 0;
    this.mirror.ackBatch(batch);
    this.inflight = null; this.failures = 0; this.retryAt = 0;
    this.state = recovered ? 'reconciled' : 'acknowledged';
    return {more: batch.more, recovered: recovered};
  };
  function definitive(status, code) {
    if (code === 'observation_trop_volumineuse') return true;
    return !!status && status >= 400 && status < 500 && status !== 408 && status !== 429;
  }
  Transport.prototype.failed = function (status, code, now) {
    if (!this.inflight) return 'none';
    if (definitive(status, code)) {
      this.mirror.rejectBatch(this.inflight.batch, code);
      this.inflight = null; this.failures = 0; this.state = 'blocked';
      return 'blocked';
    }
    this.failures += 1;
    this.retryAt = now + Math.min(this.ceiling, this.base * Math.pow(2, this.failures - 1));
    this.state = 'retry_pending';
    return 'retry';
  };

  /* Origine d'un changement : une commande de l'IA n'est reconnue qu'à l'empreinte exacte du texte qu'elle vient d'écrire (OF18, B10). Un texte différent, même une seconde après dans
     le même bloc, reste une saisie humaine ou une origine inconnue. Chaque empreinte n'est consommée qu'une fois : annuler puis rétablir n'est pas pris pour l'IA.
     Le délai de vie ne prouve rien : il nettoie seulement les attentes anciennes. ``writes`` : [{block_id, hash}], ``block_id`` nul pour un bloc créé. */
  function Origins(ttl) { this.ttl = ttl || 8000; this.items = []; }
  Origins.prototype.expect = function (commandId, writes, now) {
    var list = (writes || []).map(function (w) { return typeof w === 'string' ? {block_id: w, hash: ''} : {block_id: w.block_id, hash: w.hash}; });
    this.items.push({id: commandId, writes: list, until: now + this.ttl});
  };
  Origins.prototype.of = function (blockId, now, hash) {
    this.items = this.items.filter(function (x) { return x.until > now; });
    if (!hash) return null;
    for (var i = 0; i < this.items.length; i++) {
      var writes = this.items[i].writes;
      for (var k = 0; k < writes.length; k++) {
        if (writes[k].hash === hash && (writes[k].block_id === blockId || writes[k].block_id === null)) { writes.splice(k, 1); return this.items[i].id; }
      }
    }
    return null;
  };
  /* Textes que les opérations doivent produire, calculés avant l'écriture : les notifications de l'éditeur peuvent arriver avant le retour de la commande. */
  function expectedWrites(operations, blocks) {
    var next = {}, out = [];
    operations.forEach(function (op) {
      var key = String(op.anchor.block_id), id = key.replace(/^P-/, '');
      if (op.action === 'replace_text') {
        var current = key in next ? next[key] : (blocks[id] || {}).text;
        if (typeof current === 'string' && current.indexOf(op.anchor.expected_text) >= 0) next[key] = current.replace(op.anchor.expected_text, function () { return op.replacement; });
      } else if (op.action === 'insert_after_block') out.push({block_id: null, hash: sha256(clean(op.replacement))});
    });
    Object.keys(next).forEach(function (key) { out.push({block_id: key, hash: sha256(next[key])}); });
    return out;
  }

  /* Ack calculé à partir des résultats relus dans l'éditeur : jamais « appliqué » sans relecture ; partiel dit ce qui a réellement changé. */
  function ackStatus(results) {
    var done = results.filter(function (r) { return r.status === 'applied'; }).length;
    if (!results.length) return 'failed';
    if (done === results.length) return 'applied';
    if (done > 0) return 'partial';
    var conflict = results.some(function (r) { return r.status === 'conflict' || r.status === 'mixed_format'; });
    return conflict ? 'conflict' : 'failed';
  }

  return {sha256: sha256, clean: clean, Pacer: Pacer, Mirror: Mirror, Transport: Transport, Origins: Origins, expectedWrites: expectedWrites, ackStatus: ackStatus};
}));
