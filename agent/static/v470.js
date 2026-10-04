/* AxiorHub 4.7.0 - vérification des citations, relecture contradictoire, modèles d'actes. */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) return;
    var el = document.createElement('div');
    el.className = 'ax-toast' + (bad ? ' bad' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, bad ? 9000 : 4500);
  }

  function post(name, body) {
    return fetch(prefix + '/api440/' + name, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }

  function busy(btn, on) { if (btn) { btn.disabled = on; btn.setAttribute('aria-busy', on ? 'true' : 'false'); } }

  /* HTML renvoyé par le serveur : entièrement échappé côté serveur. */
  function show(out, html) {
    out.innerHTML = html;
    var first = out.querySelector('.vf-head');
    if (first) { first.setAttribute('tabindex', '-1'); first.focus({ preventScroll: false }); }
  }

  function wireGoto(out, textarea) {
    out.addEventListener('click', function (ev) {
      var b = ev.target.closest('.vf-goto');
      if (!b || !textarea) return;
      var li = b.closest('.vf-item');
      var s = parseInt(li.getAttribute('data-start'), 10), e = parseInt(li.getAttribute('data-end'), 10);
      textarea.focus();
      try { textarea.setSelectionRange(s, e); } catch (x) { /* ignoré */ }
    });
  }

  /* ----------------------------------------------------------- page Vérifier */
  var text = $('#v-text');
  if (text) {
    var out = $('#v-out');
    wireGoto(out, text);
    var common = function () {
      return { text: text.value, matter: $('#v-matter').value, fact_date: $('#v-date').value };
    };
    var save = function () {
      if ($('#v-matter').value) post('sources/fact_date', { matter: $('#v-matter').value, fact_date: $('#v-date').value }).catch(function () {});
    };
    $('#v-check').addEventListener('click', function () {
      var b = this; busy(b, true); save();
      post('citations/check', common()).then(function (d) { show(out, d.html); }).catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
    $('#v-review').addEventListener('click', function () {
      var b = this; busy(b, true); save();
      var body = common();
      body.opponent_paths = $('#v-opp').value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean);
      post('review/run', body).then(function (d) { show(out, d.html + '<h2>Citations</h2>' + d.citations_html); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
    $('#v-mentions').addEventListener('click', function () {
      var kind = $('#v-kind').value;
      if (!kind) { toast('Choisissez un modèle d’acte.', true); return; }
      var b = this; busy(b, true);
      post('templates/check', { kind: kind, text: text.value }).then(function (d) { show(out, d.html); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
  }

  /* ----------------------------------------------------------- page Sources */
  var sset = $('#s-save');
  if (sset) {
    var sout = $('#s-out');
    sset.addEventListener('click', function () {
      var b = this; busy(b, true);
      post('sources/settings', { enabled: $('#s-enabled').checked, client_id: $('#s-id').value, secret: $('#s-secret').value })
        .then(function () { $('#s-secret').value = ''; toast('Réglages enregistrés.'); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
    $('#s-test').addEventListener('click', function () {
      var b = this; busy(b, true);
      post('sources/test', {}).then(function (d) { show(sout, d.html); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
  }

  /* ----------------------------------------------------------- page Modèles */
  var panel = $('#m-panel');
  if (panel) {
    $('#m-save').addEventListener('click', function () {
      var b = this; busy(b, true);
      var off = [];
      panel.querySelectorAll('.m-on').forEach(function (c) { if (!c.checked) off.push(c.value); });
      post('templates/save', { kind: panel.getAttribute('data-kind'), disabled: off, extra_text: $('#m-extra').value, skeleton: $('#m-skel').value })
        .then(function () { toast('Modèle enregistré.'); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
    });
  }

  /* ----------------------------------------------------------- éditeur de brouillons */
  var editor = $('#ax-editor');
  if (editor && 'MutationObserver' in window) {
    var attach = function () {
      var body = $('#f-body', editor);
      if (!body || $('#vf-draft', editor)) return;
      var wrap = document.createElement('div');
      wrap.id = 'vf-draft';
      wrap.className = 'vf-draft';
      var btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'ax-btn ghost'; btn.textContent = 'Vérifier les citations juridiques';
      var out2 = document.createElement('div');
      out2.setAttribute('aria-live', 'polite');
      wrap.appendChild(btn); wrap.appendChild(out2);
      body.closest('.ax-field').insertAdjacentElement('afterend', wrap);
      wireGoto(out2, body);
      btn.addEventListener('click', function () {
        busy(btn, true);
        post('citations/check', { text: body.value, scope: 'editor' }).then(function (d) { show(out2, d.html); })
          .catch(function (e) { toast(e.message, true); }).then(function () { busy(btn, false); });
      });
    };
    new MutationObserver(attach).observe(editor, { childList: true, subtree: true });
    attach();
  }
})();
