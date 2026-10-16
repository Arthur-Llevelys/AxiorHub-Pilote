/* AxiorHub 5.6.52 — routines (modèles, dossiers choisis, aperçu des prochains passages, clé de demande) et connexions API */
(function () {
  'use strict';
  if (window.__axiorhub5652) { return; }
  window.__axiorhub5652 = true;
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function key() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function collect(form) {
    var data = {};
    form.querySelectorAll('input,select,textarea').forEach(function (f) {
      if (!f.name || f.type === 'file') { return; }
      if (f.type === 'checkbox') { data[f.name] = f.checked; return; }
      if (f.type === 'radio' && !f.checked) { return; }
      data[f.name] = f.value;
    });
    return data;
  }
  function init(root) {
    root.querySelectorAll('input[data-r52-key]').forEach(function (i) { if (!i.value) { i.value = key(); } });
    root.querySelectorAll('select[data-r52-matters]').forEach(function (sel) {
      var hidden = sel.form && sel.form.querySelector('input[type=hidden][name=dossiers]');
      if (!hidden || sel.dataset.r52Bound) { return; }
      sel.dataset.r52Bound = '1';
      sel.addEventListener('change', function () {
        hidden.value = Array.prototype.filter.call(sel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }).join('\n');
      });
    });
    root.querySelectorAll('select[data-r52-template]').forEach(function (sel) {
      if (sel.dataset.r52Bound) { return; }
      sel.dataset.r52Bound = '1';
      sel.addEventListener('change', function () {
        var opt = sel.options[sel.selectedIndex], form = sel.form, raw = opt && opt.getAttribute('data-r52');
        if (!raw || !form) { return; }
        var t = JSON.parse(raw), s = t.schedule || {};
        var set = function (name, value) { var f = form.querySelector('[name="' + name + '"]'); if (f && value !== undefined) { f.value = value; } };
        set('title', t.title); set('task_type', t.task_type); set('instruction', t.instruction || ''); set('freq', s.freq); set('time', s.time);
        var days = s.freq === 'quotidien' ? '1234567' : (s.days || '');
        form.querySelectorAll('input[name^="day_"]').forEach(function (c) { c.checked = days.indexOf(c.name.slice(4)) >= 0; });
      });
    });
  }
  // Connexions : « Connecter mon compte » ouvre la page d'autorisation du fournisseur (adresse https renvoyée par le serveur)
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-cx-oauth]');
    if (!form) { return; }
    ev.preventDefault();
    ev.stopImmediatePropagation();
    var status = form.querySelector('[role=status]'), btn = form.querySelector('button[type=submit]');
    if (btn) { btn.disabled = true; }
    fetch(prefix + '/api440/' + form.getAttribute('data-api'), {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(collect(form))})
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error); } return v; }); })
      .then(function (v) {
        if (typeof v.oauth_url === 'string' && /^https:\/\//.test(v.oauth_url)) { window.location.assign(v.oauth_url); return; }
        throw new Error('Adresse d’autorisation invalide.');
      })
      .catch(function (e) { if (status) { status.textContent = e.message; } if (btn) { btn.disabled = false; } });
  }, true);
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest && ev.target.closest('[data-r52-preview]');
    if (!btn) { return; }
    var form = btn.form, out = form && form.querySelector('.r52-preview');
    if (!form) { return; }
    btn.disabled = true;
    fetch(prefix + '/api440/m5652/routine/apercu', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(collect(form))})
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error); } return v; }); })
      .then(function (v) { if (out) { out.textContent = 'Prochains passages : ' + v.message; } })
      .catch(function (e) { if (out) { out.textContent = e.message; } })
      .then(function () { btn.disabled = false; });
  });
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', function () { init(document); }); } else { init(document); }
}());
