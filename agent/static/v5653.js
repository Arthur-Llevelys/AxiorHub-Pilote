/* AxiorHub 5.6.53 — facturation et honoraires : envoi des fichiers (modèle Word, logo, bibliothèque), calcul sans enregistrement,
   ouverture de la fiche de transmission. Les autres formulaires passent par le traitement commun (m5-form). */
(function () {
  'use strict';
  if (window.__axiorhub5653) { return; }
  window.__axiorhub5653 = true;
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function collect(form) {
    var data = {};
    form.querySelectorAll('input,select,textarea').forEach(function (f) {
      if (!f.name || f.type === 'file' || f.disabled) { return; }
      if (f.type === 'checkbox') { data[f.name] = f.checked; return; }
      if (f.type === 'radio' && !f.checked) { return; }
      data[f.name] = f.value;
    });
    return data;
  }
  function post(route, data) {
    return fetch(prefix + '/api440/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)})
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error); } return v; }); });
  }
  function status(form, text) {
    var s = form.querySelector('[role=status]');
    if (s) { s.textContent = text; }
  }
  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || '')); };
      reader.onerror = function () { reject(new Error('Fichier illisible.')); };
      reader.readAsDataURL(file);
    });
  }
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form.b53-form');
    if (!form) { return; }
    var fileInput = form.querySelector('input[type=file][data-b53-file]');
    var route = (form.getAttribute('data-api') || '').trim();
    if (!fileInput && route !== 'm5653/document/transmettre') { return; }
    ev.preventDefault();
    ev.stopImmediatePropagation();
    var btn = form.querySelector('button[type=submit]');
    if (btn) { btn.disabled = true; }
    var data = collect(form);
    var ready = Promise.resolve(data);
    if (fileInput) {
      var file = fileInput.files && fileInput.files[0];
      if (!file) { status(form, 'Choisissez un fichier.'); if (btn) { btn.disabled = false; } return; }
      if (file.size > 6 * 1024 * 1024) { status(form, 'Fichier trop lourd.'); if (btn) { btn.disabled = false; } return; }
      ready = readFile(file).then(function (url) { data.contenu = url; data.nom = file.name; return data; });
    }
    status(form, 'Envoi…');
    ready.then(function (payload) { return post(route, payload); })
      .then(function (v) {
        status(form, v.message || 'Fait.');
        if (v.operation && route === 'm5653/document/transmettre') { window.location.assign(prefix + '/operation?id=' + encodeURIComponent(v.operation)); return; }
        if (form.hasAttribute('data-reload')) { window.setTimeout(function () { window.location.reload(); }, 600); }
      })
      .catch(function (e) { status(form, e.message); })
      .then(function () { if (btn) { btn.disabled = false; } });
  }, true);
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest && ev.target.closest('[data-b53-calc]');
    if (!btn) { return; }
    var form = btn.form;
    if (!form) { return; }
    btn.disabled = true;
    status(form, 'Calcul…');
    post('m5653/estimation/calculer', collect(form))
      .then(function (v) { status(form, v.message); })
      .catch(function (e) { status(form, e.message); })
      .then(function () { btn.disabled = false; });
  });
}());
