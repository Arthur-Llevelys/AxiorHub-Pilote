/* AxiorHub 5.6.31 — menu « Toutes les fonctions » (recherche, clavier), choix de langue, guide d'installation et guide d'utilisation. */
(function () {
  'use strict';
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  var csrf = meta('axiorhub-csrf');
  var messages = {};
  try { var raw = document.getElementById('l5631-msg'); if (raw) messages = JSON.parse(raw.textContent || '{}'); } catch (err) { messages = {}; }
  function msg(key, fallback) { return messages[key] || fallback; }
  function fold(text) { return (text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body || {}) }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { data._status = r.status; return data; });
    });
  }

  /* ---- Toutes les fonctions : recherche par nom, besoin ou ancien nom ; Échap ferme et rend le focus au bouton (NV03). */
  var panel = document.getElementById('f5631-all');
  if (panel) {
    var input = document.getElementById('f5631-q'), toggle = document.getElementById('f5631-toggle'), empty = document.getElementById('f5631-empty');
    var filter = function () {
      var q = fold(input.value.trim()), shown = 0;
      panel.querySelectorAll('li[data-search]').forEach(function (li) {
        var only = li.hasAttribute('data-search-only');   // 5.6.43 : sous-sections visibles seulement dans les résultats de recherche
        var ok = q ? fold(li.getAttribute('data-search')).indexOf(q) !== -1 : !only;
        li.hidden = !ok; if (ok) shown++;
      });
      panel.querySelectorAll('.f5631-group').forEach(function (g) { g.hidden = !g.querySelector('li[data-search]:not([hidden])'); });
      if (empty) empty.hidden = shown > 0;
    };
    if (input) input.addEventListener('input', filter);
    panel.addEventListener('toggle', function () { if (panel.open && input) input.focus(); });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && panel.open) { panel.open = false; if (toggle) toggle.focus(); }
    });
  }

  /* ---- Langue : préférence de l'utilisateur connecté (et cookie de session), puis rechargement de la page dans la nouvelle langue. */
  var lang = document.getElementById('l5631-lang');
  if (lang) {
    lang.addEventListener('change', function () {
      post(lang.getAttribute('data-url'), { lang: lang.value }).then(function (data) {
        if (data._status === 200) window.location.reload();
      });
    });
  }

  /* ---- Guide d'utilisation : recherche dans les fiches. */
  var hq = document.getElementById('h5631-q');
  if (hq) {
    var hfilter = function () {
      var q = fold(hq.value.trim()), shown = 0;
      document.querySelectorAll('.h5631-group li[data-search]').forEach(function (li) {
        var ok = !q || fold(li.getAttribute('data-search')).indexOf(q) !== -1; li.hidden = !ok; if (ok) shown++;
      });
      document.querySelectorAll('.h5631-group').forEach(function (g) { g.hidden = !g.querySelector('li[data-search]:not([hidden])'); });
      var he = document.getElementById('h5631-empty'); if (he) he.hidden = shown > 0;
    };
    hq.addEventListener('input', hfilter); if (hq.value) hfilter();
  }

  /* ---- Guide d'installation. */
  var root = document.getElementById('g5631');
  if (!root) return;
  var api = root.getAttribute('data-api'), pageUrl = root.getAttribute('data-page');
  var status = root.querySelector('.g5631-status');
  function say(text, bad) { if (status) { status.textContent = text || ''; status.classList.toggle('bad', !!bad); } }
  function go(session, step) { window.location.href = pageUrl + '?session=' + encodeURIComponent(session) + (step ? '&etape=' + encodeURIComponent(step) : ''); }

  root.querySelectorAll('.g5631-start').forEach(function (b) {
    b.addEventListener('click', function () {
      say(msg('js.working', 'Enregistrement…'));
      post(api + 'start', { mode: b.getAttribute('data-mode') || 'nouveau' }).then(function (data) {
        if (data.id) go(data.id, data.step); else say(data.message || msg('js.error', 'Action non effectuée.'), true);
      });
    });
  });
  var later = root.querySelector('.g5631-later');
  if (later) later.addEventListener('click', function () {
    post(api + 'later', {}).then(function (data) { if (data.done) window.location.href = pageUrl.replace(/\/guide-installation$/, '/aujourdhui'); else say(data.message, true); });
  });

  var form = root.querySelector('.g5631-form');
  function values() {
    var out = {};
    if (!form) return out;
    form.querySelectorAll('input, select, textarea').forEach(function (el) {
      if (!el.name) return;
      if (el.type === 'checkbox') {
        if (el.name === 'features' || el.name === 'domaines') { out[el.name] = out[el.name] || []; if (el.checked) out[el.name].push(el.value); }
        else out[el.name] = el.checked;
      } else if (el.type === 'radio') { if (el.checked) out[el.name] = el.value; }
      else if (el.type === 'password') { if (el.value) out[el.name] = el.value; }
      else out[el.name] = el.value;
    });
    return out;
  }
  function showErrors(errors) {
    root.querySelectorAll('.g5631-error').forEach(function (s) { s.hidden = true; s.textContent = ''; });
    var first = null;
    Object.keys(errors || {}).forEach(function (name) {
      var el = form && form.querySelector('[name="' + name + '"]');
      var holder = el ? document.getElementById(el.id + '-err') : null;
      if (holder) { holder.textContent = errors[name].message || errors[name].code; holder.hidden = false; if (!first) first = el; }
    });
    if (first) first.focus();
  }
  function step(action) {
    say(msg('js.working', 'Enregistrement…'));
    post(api + 'step', { id: root.getAttribute('data-session'), revision: parseInt(root.getAttribute('data-revision'), 10),
      step: root.getAttribute('data-step'), values: values(), action: action }).then(function (data) {
      if (data.errors && Object.keys(data.errors).length) {
        root.setAttribute('data-revision', data.revision); showErrors(data.errors); say(msg('js.fix_fields', 'Corrigez les champs signalés.'), true); return;
      }
      if (data.id) { if (action === 'save') { root.setAttribute('data-revision', data.revision); say(msg('js.saved', 'Enregistré. Vous pouvez reprendre plus tard.')); } else go(data.id, data.step); }
      else say(data.message || msg('js.error', 'Action non effectuée.'), true);
    });
  }
  root.querySelectorAll('[data-action]').forEach(function (b) {
    b.addEventListener('click', function () {
      var action = b.getAttribute('data-action');
      if (action === 'cancel') {
        if (!window.confirm(msg('js.confirm_cancel', 'Abandonner ce guide ? Les réglages déjà enregistrés dans Paramètres sont conservés.'))) return;
        post(api + 'cancel', { id: root.getAttribute('data-session') }).then(function () { window.location.href = pageUrl; });
        return;
      }
      if (action === 'apply') {
        say(msg('js.working', 'Enregistrement…'));
        post(api + 'apply', { id: root.getAttribute('data-session'), revision: parseInt(root.getAttribute('data-revision'), 10) }).then(function (data) {
          if (data.conflict) { say(msg('js.conflict', 'Un autre écran a modifié ces réglages : ') + (data.fields || []).join(', '), true); return; }
          if (data.applied) { window.location.href = pageUrl.replace(/\/guide-installation$/, '/aujourdhui'); return; }
          say(data.message || msg('js.error', 'Action non effectuée.'), true);
        });
        return;
      }
      step(action);
    });
  });
  root.querySelectorAll('.g5631-test-btn').forEach(function (b) {
    b.addEventListener('click', function () {
      var connector = b.getAttribute('data-connector');
      var box = root.querySelector('.g5631-proof[data-connector="' + connector + '"]');
      say(msg('js.testing', 'Vérification en cours…'));
      b.disabled = true;
      /* Les champs de l'étape sont d'abord conservés (sans changer d'écran), puis la configuration candidate est testée. */
      post(api + 'step', { id: root.getAttribute('data-session'), revision: parseInt(root.getAttribute('data-revision'), 10),
        step: root.getAttribute('data-step'), values: values(), action: 'save' }).then(function (saved) {
        if (saved.revision) root.setAttribute('data-revision', saved.revision);
        if (saved.errors && Object.keys(saved.errors).length) { showErrors(saved.errors); b.disabled = false; say(msg('js.fix_fields', 'Corrigez les champs signalés.'), true); return null; }
        return post(api + 'test', { id: root.getAttribute('data-session'), connector: connector, capability: b.getAttribute('data-capability') || '' });
      }).then(function (data) {
        b.disabled = false;
        if (!data) return;
        if (!data.results) { say(data.message || msg('js.error', 'Action non effectuée.'), true); return; }
        if (box) {
          var ul = document.createElement('ul'); ul.className = 'g5631-proofs';
          data.results.forEach(function (r) {
            var li = document.createElement('li'); li.setAttribute('data-status', r.status);
            var strong = document.createElement('strong'); strong.textContent = msg('js.cap.' + connector + '.' + r.capability, r.capability);
            li.appendChild(strong); li.appendChild(document.createTextNode(' — ' + msg('js.status.' + r.status, r.status)));
            li.appendChild(document.createElement('br'));
            var small = document.createElement('small'); small.textContent = r.message; li.appendChild(small);
            ul.appendChild(li);
          });
          box.innerHTML = ''; box.appendChild(ul);
        }
        if (data.session && data.session.revision) root.setAttribute('data-revision', data.session.revision);
        var bad = data.results.some(function (r) { return r.status !== 'available'; });
        say(bad ? msg('js.test_partial', 'Vérification terminée : voyez le détail ci-dessus.') : msg('js.test_ok', 'Vérification réussie.'), bad);
      });
    });
  });
})();
