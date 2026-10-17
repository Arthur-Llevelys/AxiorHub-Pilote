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
    if (!fileInput && route !== 'm5653/document/transmettre' && route !== 'm5653/client/siren') { return; }
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
        if (route === 'm5653/client/siren') { candidates(form, v); return; }
        if (form.hasAttribute('data-reload')) { window.setTimeout(function () { window.location.reload(); }, 600); }
      })
      .catch(function (e) { status(form, e.message); })
      .then(function () { if (btn) { btn.disabled = false; } });
  }, true);
  /* 5.6.56 (audit 5.6.55, § 7.2) : résultats de l'annuaire présentés un par un ; « Utiliser cette entreprise » remplit la fiche du
     client sans l'enregistrer (les autres saisies sont conservées) ; c'est l'enregistrement par l'avocat qui confirme. */
  function candidates(form, v) {
    var box = form.querySelector('.b56-candidates');
    if (!box) { return; }
    box.textContent = '';
    var rows = v.candidats || [];
    if (!rows.length) { box.textContent = 'Aucun résultat : saisissez les informations du client à la main.'; return; }
    var list = document.createElement('ul');
    list.className = 'b56-candidate-list';
    rows.forEach(function (r) {
      var li = document.createElement('li');
      var title = document.createElement('strong');
      title.textContent = (r.nom || '(sans nom)') + (r.siren ? ' — SIREN ' + r.siren : '');
      var info = document.createElement('span');
      info.className = 'b53-note';
      info.textContent = [r.siege, r.etat === 'C' ? 'cessée' : (r.etat === 'A' ? 'active' : r.etat), v.source, v.date ? 'lu le ' + v.date : '']
        .filter(function (x) { return x; }).join(' · ');
      var use = document.createElement('button');
      use.type = 'button';
      use.className = 'ax-btn ghost';
      use.textContent = 'Utiliser cette entreprise';
      use.addEventListener('click', function () {
        var holder = form.closest('details') || document;
        var target = holder.querySelector('form[data-api="m5653/client/enregistrer"]');
        if (!target) { return; }
        var set = function (name, value) {
          var f = target.querySelector('[name="' + name + '"]');
          if (f && value) { f.value = value; }
        };
        set('nom', r.nom); set('siren', r.siren); set('adresse_ligne1', r.adresse_ligne1); set('code_postal', r.code_postal); set('ville', r.ville);
        set('country', 'FR');
        var src = target.querySelector('input[name="source"]');
        if (!src) { src = document.createElement('input'); src.type = 'hidden'; src.name = 'source'; target.appendChild(src); }
        src.value = (v.source || 'annuaire public') + (v.date ? ', lu le ' + v.date : '') + ' — choisi par l’avocat';
        status(form, 'Fiche remplie avec « ' + (r.nom || '') + ' » : vérifiez puis cliquez sur « Enregistrer le client ».');
        var first = target.querySelector('[name="nom"]');
        if (first) { first.focus(); }
      });
      li.appendChild(title); li.appendChild(document.createTextNode(' ')); li.appendChild(info); li.appendChild(use);
      list.appendChild(li);
    });
    box.appendChild(list);
  }
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
