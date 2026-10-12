/* AxiorHub 5.6.36 — volet du complément Word. Le document n'est lu qu'au clic ; toute modification est insérée en modification suivie. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (id) { return document.getElementById(id); };
  var inWord = false, analysed = [];

  function status(text, bad) { var s = $('w-status'); s.textContent = text || ''; s.className = bad ? 'bad' : ''; }
  function api(route, body) {
    var opts = body === undefined ? { credentials: 'same-origin', headers: { Accept: 'application/json' } } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
      body: JSON.stringify(body) };
    return fetch(prefix + '/api440/m5636/' + route, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur (reconnectez-vous à AxiorHub).' }; });
    }).then(function (d) { if (d && d.error) { throw new Error(d.message || d.error); } return d; });
  }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') { n.textContent = attrs[k]; } else { n.setAttribute(k, attrs[k]); } });
    (kids || []).forEach(function (c) { if (c) { n.appendChild(c); } });
    return n;
  }
  function word(fn) {
    if (!inWord) { return Promise.reject(new Error('Disponible seulement dans Word.')); }
    return window.Word.run(function (ctx) {
      try { ctx.document.changeTrackingMode = window.Word.ChangeTrackingMode.trackAll; } catch (e) { /* WordApi 1.4 absent : suivi à activer à la main */ }
      return fn(ctx);
    });
  }
  function norm(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }

  // ---------------------------------------------------------------- playbook
  function readParagraphs() {
    return word(function (ctx) {
      var ps = ctx.document.body.paragraphs; ps.load('items/text');
      return ctx.sync().then(function () { return ps.items.map(function (p) { return p.text; }); });
    });
  }
  function applyEdit(x, row) {
    return word(function (ctx) {
      var ps = ctx.document.body.paragraphs; ps.load('items/text');
      return ctx.sync().then(function () {
        var p = ps.items[x.paragraph];
        if (!p || norm(p.text) !== norm(x.original)) { throw new Error('Le paragraphe a changé depuis l’analyse : relancez l’analyse.'); }
        if (x.operation === 'replace') { p.insertText(x.text, 'Replace'); }
        else if (x.operation === 'insert_after') { p.insertParagraph(x.text, 'After'); }
        else { p.delete(); }
        return ctx.sync();
      });
    }).then(function () { row.classList.add('done'); row.querySelector('button').disabled = true; status('Modification insérée (suivie).'); });
  }
  function renderEdits(edits) {
    var box = $('w-edits'); box.textContent = '';
    if (!edits.length) { box.appendChild(el('p', { 'class': 'w-note', text: 'Aucun écart avec le playbook.' })); return; }
    edits.slice().sort(function (a, b) { return b.paragraph - a.paragraph; }).forEach(function (x) {
      var btn = el('button', { type: 'button', 'class': 'w-btn', text: 'Appliquer' });
      var row = el('div', { 'class': 'w-edit' }, [el('strong', { text: '§ ' + (x.paragraph + 1) + ' — ' + (x.clause || 'clause') }),
        el('p', { 'class': 'w-note', text: x.justification }), el('p', { text: x.operation === 'delete' ? '(suppression)' : x.text }), btn]);
      btn.addEventListener('click', function () { applyEdit(x, row).catch(function (e) { status(e.message, true); }); });
      box.appendChild(row);
    });
    var all = el('button', { type: 'button', 'class': 'w-btn', text: 'Tout appliquer' });
    all.addEventListener('click', function () {
      var rows = Array.prototype.slice.call(box.querySelectorAll('.w-edit:not(.done) button'));
      rows.reduce(function (p, b) { return p.then(function () { b.click(); return new Promise(function (ok) { setTimeout(ok, 400); }); }); }, Promise.resolve());
    });
    box.insertBefore(all, box.firstChild);
  }
  function analyse() {
    var pb = $('w-playbook').value;
    if (!pb) { status('Choisissez un playbook (Outils IA › Playbooks).', true); return; }
    status('Lecture du document…');
    readParagraphs().then(function (paragraphs) {
      analysed = paragraphs; status('Analyse par l’IA du cabinet…');
      return api('outils/playbook/propositions', { playbook: pb, matter: $('w-matter').value, paragraphs: paragraphs });
    }).then(function (r) { renderEdits(r.edits || []); status((r.edits || []).length + ' écart(s) proposé(s).'); })
      .catch(function (e) { status(e.message, true); });
  }

  // ---------------------------------------------------------------- mémos
  var timer = null;
  function searchMemos() {
    api('outils/memos?q=' + encodeURIComponent($('w-q').value)).then(function (r) {
      var box = $('w-memos'); box.textContent = '';
      (r.memos || []).slice(0, 20).forEach(function (m) {
        var ins = el('button', { type: 'button', 'class': 'w-btn', text: 'Insérer' });
        ins.addEventListener('click', function () {
          var text = m.body + (m.sources && m.sources.length ? '\n\nSources :\n' + m.sources.join('\n') : '');
          word(function (ctx) { ctx.document.getSelection().insertText(text, 'Replace'); return ctx.sync(); })
            .then(function () { status('Mémo inséré.'); }).catch(function (e) { status(e.message, true); });
        });
        box.appendChild(el('div', { 'class': 'w-edit' }, [el('strong', { text: m.title }), el('p', { 'class': 'w-note', text: m.tags || '' }), ins]));
      });
      if (!(r.memos || []).length) { box.appendChild(el('p', { 'class': 'w-note', text: 'Aucun mémo validé ne correspond.' })); }
    }).catch(function (e) { status(e.message, true); });
  }

  // ---------------------------------------------------------------- reformulation
  function rewrite() {
    status('Lecture de la sélection…');
    word(function (ctx) { var s = ctx.document.getSelection(); s.load('text'); return ctx.sync().then(function () { return s.text; }); })
      .then(function (text) {
        if (!norm(text)) { throw new Error('Sélectionnez d’abord un passage dans le document.'); }
        status('Reformulation…');
        return api('office/reformuler', { text: text, instruction: $('w-instr').value, matter: $('w-matter').value }).then(function (r) { return [text, r]; });
      }).then(function (pair) {
        var r = pair[1], box = $('w-proposal'); box.textContent = '';
        var use = el('button', { type: 'button', 'class': 'w-btn', text: 'Remplacer la sélection (suivi)' });
        use.addEventListener('click', function () {
          word(function (ctx) {
            var s = ctx.document.getSelection(); s.load('text');
            return ctx.sync().then(function () {
              if (norm(s.text) !== norm(pair[0])) { throw new Error('La sélection a changé : sélectionnez de nouveau le passage.'); }
              s.insertText(r.text, 'Replace'); return ctx.sync();
            });
          }).then(function () { status('Passage remplacé (suivi).'); }).catch(function (e) { status(e.message, true); });
        });
        box.appendChild(el('p', { text: r.text }));
        if (r.note) { box.appendChild(el('p', { 'class': 'w-note', text: r.note })); }
        box.appendChild(use);
        status('Proposition prête.');
      }).catch(function (e) { status(e.message, true); });
  }

  function boot() {
    api('office/contexte').then(function (c) {
      c.matters.forEach(function (m) { $('w-matter').appendChild(el('option', { value: m.id, text: m.label })); });
      if (!c.playbooks.length) { $('w-playbook').appendChild(el('option', { value: '', text: 'Aucun playbook (Outils IA › Playbooks)' })); }
      c.playbooks.forEach(function (p) { $('w-playbook').appendChild(el('option', { value: p.id, text: p.name })); });
    }).catch(function (e) { status(e.message, true); });
    $('w-analyse').addEventListener('click', analyse);
    $('w-rewrite').addEventListener('click', rewrite);
    $('w-q').addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(searchMemos, 300); });
    searchMemos();
  }
  if (window.Office && window.Office.onReady) {
    window.Office.onReady(function (info) { inWord = !!(info && info.host === window.Office.HostType.Word); $('w-outside').hidden = inWord; boot(); });
  } else {
    document.addEventListener('DOMContentLoaded', function () { $('w-outside').hidden = false; boot(); });
  }
})();
