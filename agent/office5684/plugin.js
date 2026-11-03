/* AxiorHub : plugin EuroOffice d'arrière-plan. Il observe et applique ; il ne décide de rien. Le chargeur remplace plugins.js : le moteur vient de l'éditeur (plugin_init),
   accepté d'une seule origine. Le panneau est dans la page AxiorHub, à côté de l'éditeur. */
(function () {
  'use strict';
  var meta = document.documentElement;
  var editorOrigin = meta.getAttribute('data-editor-origin') || '';
  var bridge = meta.getAttribute('data-bridge') || '';
  var GUID = meta.getAttribute('data-guid') || '';
  var Core = window.AxOfficeCore;
  window.Asc = window.Asc || {};
  window.Asc.plugin = window.Asc.plugin || {};
  window.Asc.scope = window.Asc.scope || {};
  var plugin = window.Asc.plugin;
  plugin.guid = GUID;
  plugin.tr = function (x) { return x; };
  plugin._initInternal = false;
  window.Asc.supportOrigins = {};
  plugin.attachEvent = function (name, fn) { plugin._events = plugin._events || {}; plugin._events[name] = fn; };
  plugin.onEvent = function (name, value) { if (plugin._events && plugin._events[name]) plugin._events[name].call(plugin, value); };
  plugin.attachEditorEvent = function (name, fn) {
    plugin['event_' + name] = fn.bind(plugin);
    window.parent.postMessage(JSON.stringify({guid: GUID, type: 'attachEvent', name: name}), editorOrigin);
  };

  function onMessage(event) {
    if (event.source !== window.parent || event.origin !== editorOrigin) return;         /* SE04 : origine et fenêtre, jamais d'origine universelle */
    if (window.plugin_onMessage) { window.plugin_onMessage(event); return; }
    if (typeof event.data !== 'string' || !plugin._initInternal) return;
    var data;
    try { data = JSON.parse(event.data); } catch (e) { return; }
    if (data.type === 'plugin_init') { window.Asc.supportOrigins[event.origin] = true; (0, eval)(data.data); }
  }
  window.addEventListener('message', onMessage, false);
  window.addEventListener('load', function () {
    fetch('./config.json', {credentials: 'omit'}).then(function (r) { return r.json(); }).then(function (cfg) {
      Object.keys(cfg).forEach(function (k) { plugin[k] = cfg[k]; });
      plugin._initInternal = true;
      window.parent.postMessage(JSON.stringify({type: 'initialize', guid: GUID}), editorOrigin);
    });
  });

  var FAST = 3000, POLL = 3000, RECONCILE = 30000, LEASE = 10000, MAX_BLOCKS = 200, MAX_CHARS = 100000;
  var mirror = new Core.Mirror(), pacer = new Core.Pacer({quiet: 2000, maxWait: 10000}), origins = new Core.Origins(8000), transport = new Core.Transport(mirror, {maxBlocks: MAX_BLOCKS, maxChars: MAX_CHARS});
  var token = '', session = '', documentId = '', clientId = 'c' + Math.random().toString(16).slice(2, 12), cursor = 0, revision = 0, analysisDue = false, analysisTried = 0;
  var epoch = 0, holder = false, ready = false, busy = false, sentAt = 0, lastSnapshot = 0, modifiedSilently = false, lastEvent = 0, applying = false;

  function api(route, body) {
    var options = {method: body === undefined ? 'GET' : 'POST', credentials: 'omit', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}};
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(bridge + route, options).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) { var err = new Error(j.error || 'erreur'); err.status = r.status; err.code = j.error; throw err; }
        return j;
      });
    });
  }

  /* Lecture bornée, exécutée dans l'éditeur. Ce texte part tel quel vers le SDK : aucune donnée de modèle n'y entre. */
  function readStructure() {
    var d = Api.GetDocument(), rows = [], seen = {};
    function add(p, kind, patchable) {
      var id = String(p.GetInternalId());
      if (seen[id]) return;
      seen[id] = true;
      rows.push({id: id, text: p.GetText(), kind: kind, patchable: patchable});
    }
    var n = d.GetElementsCount();
    for (var i = 0; i < n; i++) {
      var el = d.GetElement(i), t = el.GetClassType();
      if (t === 'paragraph') add(el, 'paragraph', true);
      else if (t === 'table') {
        for (var r = 0, rc = el.GetRowsCount(); r < rc; r++) {
          var row = el.GetRow(r);
          for (var c = 0, cc = row.GetCellsCount(); c < cc; c++) {
            var content = row.GetCell(c).GetContent();
            for (var k = 0, kc = content.GetElementsCount(); k < kc; k++) { var inner = content.GetElement(k); if (inner.GetClassType() === 'paragraph') add(inner, 'table_cell', true); }
          }
        }
      }
    }
    var cover = {paragraphes: true, tableaux: true, entetes: false, notes: false, revisions: d.IsTrackRevisions()};
    try {
      var sections = d.GetSections();
      for (var s = 0; s < sections.length; s++) {
        var h = sections[s].GetHeader('default', false), f = sections[s].GetFooter('default', false);
        if (h) for (var a = 0; a < h.GetElementsCount(); a++) add(h.GetElement(a), 'header', false);
        if (f) for (var b = 0; b < f.GetElementsCount(); b++) add(f.GetElement(b), 'footer', false);
      }
      cover.entetes = true;
    } catch (e) { cover.entetes = false; }
    try { d.GetFootnotesFirstParagraphs().forEach(function (p) { add(p, 'footnote', false); }); cover.notes = true; } catch (e2) { cover.notes = false; }
    return {rows: rows, cover: cover};
  }

  /* Écriture conditionnelle. Mesures sur EuroOffice 9.3.7 : recherche exacte, ajout, nouvelle recherche, suppression ; jamais AddText puis Delete sur la même plage (le
     paragraphe entier serait effacé), jamais GetRange par position (décalée à chaque mise en forme). Toutes les cibles sont contrôlées avant la première écriture. */
  function applyPatch() {
    var c = Asc.scope.cmd, d = Api.GetDocument(), all = d.GetAllParagraphs(), byId = {}, out = [], i;
    function clean(t) { return String(t == null ? '' : t).replace(/[\r\n]+$/, ''); }
    for (i = 0; i < all.length; i++) byId[String(all[i].GetInternalId())] = all[i];
    function sameRun(p, text) {
      for (var r = 0, n = p.GetElementsCount(); r < n; r++) { var el = p.GetElement(r); if (el.GetClassType && el.GetClassType() === 'run' && el.GetText().indexOf(text) >= 0) return true; }
      return false;
    }
    var plan = [];
    for (i = 0; i < c.operations.length; i++) {
      var op = c.operations[i], id = String(op.anchor.block_id).replace(/^P-/, ''), p = byId[id], full = p ? clean(p.GetText()) : null, want = op.anchor.expected_text;
      if (!p) { out.push({op_id: op.op_id, status: 'conflict', code: 'bloc_absent'}); continue; }
      if (op.action === 'replace_text') {
        var hits = p.Search(want, true);
        if (full.indexOf(want) < 0) { out.push({op_id: op.op_id, status: 'conflict', code: 'texte_change'}); continue; }
        if (!hits || hits.length !== 1) { out.push({op_id: op.op_id, status: 'conflict', code: 'ancre_ambigue'}); continue; }
        if (!sameRun(p, want)) { out.push({op_id: op.op_id, status: 'mixed_format', code: 'mise_en_forme_mixte'}); continue; }
      } else if (full !== want) { out.push({op_id: op.op_id, status: 'conflict', code: 'texte_change'}); continue; }
      plan.push({op: op, p: p, id: id});
    }
    if (out.length) return {results: out.concat(plan.map(function (x) { return {op_id: x.op.op_id, status: 'not_applied'}; })), texts: {}};
    var texts = {};
    plan.forEach(function (x) {
      if (x.op.action === 'replace_text') {
        var first = x.p.Search(x.op.anchor.expected_text, true);
        first[0].AddText(x.op.replacement, 'after');
        x.p.Search(x.op.anchor.expected_text, true)[0].Delete();
      } else if (x.op.action === 'insert_after_block') {
        var np = Api.CreateParagraph();
        np.AddText(x.op.replacement);
        x.p.InsertParagraph(np, 'after', true);
      } else if (x.op.action === 'add_comment') {
        x.p.GetRange().AddComment(x.op.replacement, 'AxiorHub');
      }
      texts[x.id] = clean(x.p.GetText());
      out.push({op_id: x.op.op_id, status: 'applied'});
    });
    return {results: out, texts: texts};
  }

  function snapshot() {
    return new Promise(function (resolve) {
      mirror.startBuffer();
      window.Asc.plugin.callCommand(readStructure, false, false, function (res) {
        if (res && res.rows) { mirror.load(res.rows); plugin.cover = res.cover; } else mirror.load(Object.keys(mirror.blocks).map(function (k) { return mirror.blocks[k]; }));
        lastSnapshot = Date.now();
        resolve();
      });
    });
  }

  /* 5.6.89 (B08) : le corps d'un événement est figé à sa réservation ; un nouvel essai renvoie le même identifiant et la même séquence, le serveur acquitte sans nouvelle révision. */
  function send(origin) {
    if (!ready || busy) return Promise.resolve();
    var body = transport.next(function (batch, number) {
      return {event_id: session + '-' + clientId + '-' + number, document_id: documentId, session_id: session, client_id: clientId, client_seq: number, base_revision: revision, observed_at: new Date().toISOString(),
              origin: batch.command ? 'ai_command' : (origin || 'unknown'), command_id: batch.command, upsert_blocks: batch.blocks, removed_block_ids: batch.removed, limited_blocks: batch.limits,
              structure_hash: mirror.structureHash(), snapshot_hash: mirror.snapshotHash(), coverage: plugin.cover || null};
    });
    if (!body) return Promise.resolve();
    busy = true;
    transport.sent();
    sentAt = Date.now();
    return api('/observations', body).then(function (r) {
      var done = transport.acknowledged();
      revision = r.revision;
      busy = false;
      analysisDue = analysisDue || !!r.analysable;
      if (r.reconcile || done.recovered) {                        /* après une coupure : miroir réconcilié, puis analyse de la révision courante */
        if (done.recovered) analysisDue = true;
        return snapshot().then(function () { return send('unknown'); }).then(analyse);
      }
      if (done.more) return send(origin);
      return analyse();
    }).catch(function (err) {
      busy = false;
      if (err.status === 401 || err.status === 403) { stopAll(); return; }
      transport.failed(err.status, err.code, Date.now());
    });
  }

  function analyse() {
    if (!analysisDue) return null;
    analysisTried = Date.now();
    return api('/analyse', {}).then(function () { analysisDue = false; }).catch(function () {});
  }

  function flush() {
    var now = Date.now();
    if (transport.due(now)) { send(); return; }                   /* relance du même événement, sans nouvelle frappe */
    if (analysisDue && !busy && !transport.inflight && now - analysisTried > 30000) analyse();
    if (!mirror.isDirty() || !pacer.due(now)) return;
    pacer.reset();
    send('local_user');
  }

  /* 5.6.89 (B10) : un texte identique à l'écriture attendue d'une commande vient d'elle ; tout autre texte reste une saisie à analyser. */
  function observeText(id, raw) {
    var text = Core.clean(raw), known = mirror.blocks[id];
    if (known && known.text === text) return null;
    var command = origins.of('P-' + id, Date.now(), Core.sha256(text));
    mirror.note(id, text);
    if (command) mirror.attribute(id, command);
    return command;
  }

  function onParagraph(data) {
    if (!data || data.paragraphId === undefined) return;
    lastEvent = Date.now();
    modifiedSilently = false;
    if (observeText(String(data.paragraphId), data.text)) { send('local_user'); return; }
    pacer.touch(lastEvent);
  }

  function renewLease() {
    return api('/lease', {}).then(function (r) { holder = !!r.held; epoch = r.epoch || 0; }).catch(function () { holder = false; });
  }

  function execute(commandId) {
    applying = true;
    return api('/commands/' + commandId + '/claim', {epoch: epoch}).then(function (patch) {
      return new Promise(function (resolve) {
        window.Asc.scope.cmd = {operations: patch.operations};
        origins.expect(commandId, Core.expectedWrites(patch.operations, mirror.blocks), Date.now());
        window.Asc.plugin.callCommand(applyPatch, false, true, function (res) {
          res = res || {results: []};
          Object.keys(res.texts || {}).forEach(function (id) { observeText(id, res.texts[id]); });
          var ack = {command_id: commandId, session_id: session, executor_epoch: epoch, status: Core.ackStatus(res.results), applied_operation_ids: res.results.filter(function (r) { return r.status === 'applied'; }).map(function (r) { return r.op_id; }),
                     snapshot_hash_after: mirror.snapshotHash(), error_code: (res.results.filter(function (r) { return r.code; })[0] || {}).code || null, acknowledged_at: new Date().toISOString()};
          api('/commands/' + commandId + '/ack', ack).then(function () { return send('local_user'); }).then(resolve, resolve);
        });
      });
    }).catch(function () {}).then(function () { applying = false; });
  }

  function poll() {
    return api('/events?after=' + cursor).then(function (r) {
      cursor = r.cursor;
      if (r.paused || r.cancelled || !r.commands.length || applying) return null;
      return renewLease().then(function () { return holder ? execute(r.commands[0]) : null; });
    }).catch(function (err) { if (err.status === 401 || err.status === 403) stopAll(); });
  }

  var timers = [];
  function stopAll() { timers.forEach(clearInterval); timers = []; ready = false; }

  plugin.init = function () {
    var options = (plugin.info && plugin.info.options) || {};
    var mine = options.token ? options : (options[GUID] || options.all || {});     /* l'éditeur remet déjà les options de ce plugin */
    token = mine.token || '';
    session = mine.session_id || '';
    documentId = mine.document_id || '';
    if (!token) return;
    plugin.attachEditorEvent('onParagraphText', onParagraph);        /* abonné AVANT l'instantané : aucune saisie perdue pendant la lecture */
    plugin.attachEditorEvent('onDocumentStateChange', function (modified) { if (modified && Date.now() - lastEvent > FAST) modifiedSilently = true; });
    snapshot().then(function () {
      ready = true;
      return send('unknown');
    }).then(function () {
      timers.push(setInterval(flush, 500));
      timers.push(setInterval(poll, POLL));
      timers.push(setInterval(function () { if (holder) renewLease(); }, LEASE));
      timers.push(setInterval(function () {                          /* document modifié sans notification, ou réconciliation plafonnée */
        var now = Date.now();
        if (applying) return;
        if ((modifiedSilently && now - lastSnapshot > FAST) || now - lastSnapshot > RECONCILE) { snapshot().then(function () { modifiedSilently = false; if (mirror.isDirty()) pacer.touch(Date.now()); }); }
      }, FAST));
      timers.push(setInterval(function () { api('/renew', {}).then(function (r) { token = r.token; }).catch(function () {}); }, 1800000));
    });
  };
  plugin.button = function () {};
  window.addEventListener('beforeunload', function () { if (token && navigator.sendBeacon) { /* la fermeture réelle vient du callback serveur */ } });
}());
