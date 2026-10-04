/* AxiorHub 4.8.0 - progrès de l'agent, autonomie, traçabilité, bouton « Toujours faire comme ça ». */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) return;
    var el = document.createElement('div');
    el.className = 'ax-toast' + (bad ? ' bad' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, bad ? 9000 : 4500);
  }
  function handle(r) {
    return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }
  function post(name, body) {
    return fetch(prefix + '/api440/' + name, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) }).then(handle);
  }
  function get(name, params) {
    var q = Object.keys(params || {}).filter(function (k) { return params[k] !== '' && params[k] != null; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    return fetch(prefix + '/api440/' + name + (q ? '?' + q : ''), { credentials: 'same-origin' }).then(handle);
  }
  function busy(btn, on) { if (btn) { btn.disabled = on; btn.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  function run(btn, promise, done) {
    busy(btn, true);
    return promise.then(function (d) { if (done) done(d); return d; })
      .catch(function (e) { toast(e.message, true); }).then(function () { busy(btn, false); });
  }
  function reload() { window.location.reload(); }
  function two(btn, label, action) {            /* confirmation en deux clics, sans fenêtre modale */
    if (btn.getAttribute('data-armed') === '1') { action(); return; }
    btn.setAttribute('data-armed', '1'); btn.setAttribute('data-label', btn.textContent); btn.textContent = label;
    setTimeout(function () { btn.setAttribute('data-armed', '0'); btn.textContent = btn.getAttribute('data-label'); }, 5000);
  }
  function download(file) {
    var blob = new Blob([file.content], { type: file.format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = file.filename; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  /* ------------------------------------------------------------ page Progrès */
  var lr = $('#l-refresh');
  if (lr) {
    lr.addEventListener('click', function () {
      run(lr, post('learning/refresh', {}), function (d) { $('#l-out').innerHTML = d.html; toast(d.added + ' envoi(s) ajouté(s) au tableau.'); });
    });
    $$('.t-save').forEach(function (b) {
      b.addEventListener('click', function () {
        var card = b.closest('.pg-tone');
        run(b, post('tone/save', { role: card.getAttribute('data-role'), formality: $('.t-formality', card).value, opening: $('.t-opening', card).value,
          closing: $('.t-closing', card).value, max_words: $('.t-max', card).value, directives: $('.t-directives', card).value,
          vouvoiement: $('.t-vous', card).checked }), function () { toast('Profil enregistré.'); });
      });
    });
    $$('.t-reset').forEach(function (b) {
      b.addEventListener('click', function () { run(b, post('tone/reset', { role: b.closest('.pg-tone').getAttribute('data-role') }), reload); });
    });
    $$('.t-adopt').forEach(function (b) {
      b.addEventListener('click', function () {
        var li = b.closest('li');
        run(b, post('tone/adopt', { role: li.getAttribute('data-role'), field: li.getAttribute('data-field'), value: li.getAttribute('data-value') }), reload);
      });
    });
    $('#o-save').addEventListener('click', function () {
      var b = this;
      run(b, post('tone/override', { kind: $('#o-kind').value, value: $('#o-value').value, role: $('#o-role').value }), reload);
    });
    $$('.o-del').forEach(function (b) {
      b.addEventListener('click', function () {
        var li = b.closest('li');
        run(b, post('tone/override', { kind: li.getAttribute('data-kind'), value: li.getAttribute('data-value'), role: '' }), reload);
      });
    });
    $('#d-test').addEventListener('click', function () {
      var b = this;
      run(b, post('tone/detect', { recipients: $('#d-addr').value }), function (d) {
        var out = $('#d-out'); out.textContent = '';
        var p = document.createElement('p');
        p.textContent = d.profile ? 'Profil détecté : ' + d.label + ' (' + d.confidence + (d.mixed ? ', destinataires mixtes : le plus formel est retenu' : '') + ').'
          : 'Aucun profil imposé : l’agent garde son style par défaut.';
        out.appendChild(p);
        var ul = document.createElement('ul');
        (d.reasons || []).forEach(function (r) { var li = document.createElement('li'); li.textContent = r; ul.appendChild(li); });
        out.appendChild(ul);
      });
    });
    $$('.r-susp').forEach(function (b) { b.addEventListener('click', function () { run(b, post('rules/status', { id: b.closest('.pg-rule').getAttribute('data-id'), status: 'suspendue' }), reload); }); });
    $$('.r-res').forEach(function (b) { b.addEventListener('click', function () { run(b, post('rules/status', { id: b.closest('.pg-rule').getAttribute('data-id'), status: 'active' }), reload); }); });
    $$('.r-del').forEach(function (b) {
      b.addEventListener('click', function () {
        two(b, 'Confirmer la suppression', function () { run(b, post('rules/delete', { id: b.closest('.pg-rule').getAttribute('data-id') }), reload); });
      });
    });
    $('#r-create').addEventListener('click', function () {
      var b = this, scope = $('#r-scope').value;
      var value = scope === 'dossier' ? $('#r-matter').value : $('#r-value').value;
      run(b, post('rules/create', { rule_type: $('#r-type').value, value: $('#r-val').value, scope: scope, scope_value: value,
        confirm: $('#r-confirm').checked ? 'yes' : '' }), reload);
    });
  }

  /* ------------------------------------------------------------ page Autonomie */
  $$('.a-save').forEach(function (b) {
    b.addEventListener('click', function () {
      var card = b.closest('.pg-task'), task = card.getAttribute('data-task');
      var chosen = $('input[type=radio]:checked', card);
      if (!chosen) return;
      run(b, post('autonomy/set', { task: task, level: chosen.value, confirm: $('.a-confirm', card).checked ? 'yes' : '' }), reload);
    });
  });
  var ap = $('#a-prudent');
  if (ap) ap.addEventListener('click', function () { run(ap, post('autonomy/prudent', { on: ap.getAttribute('data-on') === '1' }), reload); });
  $$('.a-exec').forEach(function (b) { b.addEventListener('click', function () { run(b, post('autonomy/pending', { id: b.closest('li').getAttribute('data-id'), action: 'execute' }), reload); }); });
  $$('.a-dismiss').forEach(function (b) { b.addEventListener('click', function () { run(b, post('autonomy/pending', { id: b.closest('li').getAttribute('data-id'), action: 'dismiss' }), reload); }); });

  /* ------------------------------------------------------------ page Traçabilité */
  var tv = $('#t-view');
  if (tv) {
    var params = function () { return { matter: $('#t-matter').value, since: $('#t-since').value, until: $('#t-until').value }; };
    tv.addEventListener('click', function () {
      if (!$('#t-matter').value) { toast('Choisissez un dossier.', true); return; }
      run(tv, get('trace/view', params()), function (d) { $('#t-out').innerHTML = d.html; });
    });
    ['json', 'csv'].forEach(function (f) {
      var b = $('#t-' + f);
      b.addEventListener('click', function () {
        if (!$('#t-matter').value) { toast('Choisissez un dossier.', true); return; }
        var q = params(); q.format = f;
        run(b, get('trace/export', q), function (d) { download(d); toast('Export prêt (empreinte ' + d.sha256.slice(0, 12) + '…).'); });
      });
    });
  }

  /* ------------------------------------------------------------ éditeur : « Toujours faire comme ça » */
  var editor = $('#ax-editor');
  if (editor && 'MutationObserver' in window) {
    var attach = function () {
      var body = $('#f-body', editor);
      if (!body || $('#pg-editor', editor)) return;
      var original = body.value;
      var wrap = document.createElement('div');
      wrap.id = 'pg-editor'; wrap.className = 'pg-editor';
      var btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'ax-btn ghost'; btn.textContent = 'Toujours faire comme ça';
      btn.title = 'Transformer ma correction en règle, après confirmation et avec un périmètre limité';
      var out = document.createElement('div'); out.setAttribute('aria-live', 'polite');
      wrap.appendChild(btn); wrap.appendChild(out);
      body.closest('.ax-field').insertAdjacentElement('afterend', wrap);
      btn.addEventListener('click', function () {
        var emails = (($('#f-to') || {}).value || '').match(/[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g) || [];
        run(btn, post('rules/propose', { original: original, corrected: body.value, recipients: emails }), function (d) {
          out.textContent = '';
          if (!d.candidates.length) {
            var p = document.createElement('p'); p.className = 'vf-note';
            p.textContent = 'Aucune différence de forme exploitable (formules, longueur, registre) entre le brouillon d’origine et votre version. Rien n’est proposé.';
            out.appendChild(p); return;
          }
          var list = document.createElement('div'); list.className = 'pg-cands';
          d.candidates.forEach(function (c, i) {
            var card = document.createElement('div'); card.className = 'pg-cand';
            var h = document.createElement('strong'); h.textContent = c.label + (c.from || c.to ? ' : « ' + (c.from || '—') + ' » → « ' + (c.to || '—') + ' »' : '');
            card.appendChild(h);
            var w = document.createElement('p'); w.className = 'pg-warn'; w.textContent = c.warning; card.appendChild(w);
            c.options.forEach(function (o) {
              var label = document.createElement('label'); var r = document.createElement('input');
              r.type = 'radio'; r.name = 'cand-' + i; r.value = o.scope; r.checked = o.scope === c.recommended_scope;
              var t = document.createElement('span'); t.textContent = o.sentence;
              label.appendChild(r); label.appendChild(t); card.appendChild(label);
            });
            var ok = document.createElement('button'); ok.type = 'button'; ok.className = 'ax-btn'; ok.textContent = 'Confirmer cette règle';
            ok.addEventListener('click', function () {
              var chosen = card.querySelector('input[type=radio]:checked'); if (!chosen) return;
              var opt = c.options.filter(function (o) { return o.scope === chosen.value; })[0];
              run(ok, post('rules/create', { rule_type: c.rule_type, value: c.value, scope: opt.scope, scope_value: opt.scope_value,
                confirm: 'yes', origin: 'correction', evidence: c.evidence_count }), function (res) {
                card.textContent = ''; var done = document.createElement('p');
                done.textContent = 'Règle créée : ' + res.sentence + ' Vous pouvez la suspendre ou la supprimer dans Progrès.';
                card.appendChild(done);
              });
            });
            card.appendChild(ok); list.appendChild(card);
          });
          out.appendChild(list);
        });
      });
    };
    new MutationObserver(attach).observe(editor, { childList: true, subtree: true });
    attach();
  }
})();
