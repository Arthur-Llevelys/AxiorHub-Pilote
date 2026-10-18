/* AxiorHub 5.6.51 — page Dossier : identifiant de commande de mission (double clic = même mission), fichiers choisis, attente d'une relecture */
(function () {
  'use strict';
  function key() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function init() {
    document.querySelectorAll('input[data-mi51-key]').forEach(function (i) { if (!i.value) { i.value = key(); } });
    document.querySelectorAll('select[data-mi51-files]').forEach(function (sel) {
      var hidden = sel.form && sel.form.querySelector('input[type=hidden][name=fichiers]');
      if (!hidden) { return; }
      var sync = function () {
        hidden.value = Array.prototype.filter.call(sel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }).join('\n');
      };
      sel.addEventListener('change', sync);
      sync();
    });
    if (document.querySelector('[data-d51-waiting]')) {   // propositions en cours de génération : la page se met à jour
      setTimeout(function () { if (!document.querySelector('form.m5-form textarea:focus, form.m5-form input:focus')) { location.reload(); } }, 15000);
    }
  }
  // 5.6.62 : « Ouvrir dans Nextcloud » partout (page Dossier, point client…) ; Aujourd'hui garde son propre gestionnaire (v530.js)
  document.addEventListener('click', function (ev) {
    var op = ev.target.closest && ev.target.closest('[data-open-path]');
    if (!op || op.closest('#c530')) { return; }
    ev.preventDefault();
    var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
    var w = window.open('about:blank', '_blank');
    var qs = new URLSearchParams({ path: op.getAttribute('data-open-path'), matter: op.getAttribute('data-open-matter') || '' }).toString();
    fetch(meta('axiorhub-prefix') + '/api440/m530/open?' + qs, { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error || 'Ouverture impossible.'); } return v; }); })
      .then(function (d) { if (w) { w.opener = null; w.location.href = d.url; } else { window.location.href = d.url; } })
      .catch(function (err) { if (w) { w.close(); } window.alert(err.message); });
  });
  // 5.6.62 : « Continuer avec ChatGPT » — onglet ouvert au clic (jamais bloqué), puis page officielle d'OpenAI
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-c5662-open]');
    if (!form) { return; }
    ev.preventDefault();
    var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
    var status = form.querySelector('.m5-status'), w = window.open('about:blank', '_blank');
    fetch(meta('axiorhub-prefix') + '/api440/m5662/chatgpt/connecter', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: '{}' })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error || 'Connexion impossible.'); } return v; }); })
      .then(function (d) {
        if (w) { w.opener = null; w.location.href = d.url; } else { window.location.href = d.url; }
        if (status) { status.textContent = d.message; }
        var paste = document.querySelector('#chatgpt5662 details:last-of-type');
        if (d.mode === 'serveur' && paste) { paste.open = true; }
      })
      .catch(function (err) { if (w) { w.close(); } if (status) { status.textContent = err.message; } });
  });
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
}());
