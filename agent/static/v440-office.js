/* AxiorHub 4.4.0 - chargeur de l'éditeur de documents (ONLYOFFICE / Euro-Office). */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var boot = JSON.parse(document.getElementById('ax-office-boot').textContent);
  var state = document.getElementById('ax-docstate');
  var help = document.getElementById('ax-office-help');

  function say(text) { state.textContent = text; }
  /* 5.6.101 (lot V) : dans le cadre du volet d'Aujourd'hui, l'état de l'éditeur est signalé à la page d'AxiorHub qui l'affiche (même origine
     seulement) : « pret » une fois l'éditeur affiché, « echec » sinon ; la page passe alors à l'aperçu simplifié. */
  function signaler(etat) {
    if (!boot.volet || window.parent === window) return;
    try { window.parent.postMessage({ ax101office: etat, mode: boot.mode }, window.location.origin); } catch (e) { /* page parente fermée */ }
  }
  function get(name, params) {
    return fetch(prefix + '/api440/' + name + (params ? '?' + new URLSearchParams(params) : ''), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); });
  }
  function showHelp(title, lines) {
    help.hidden = false; help.textContent = '';
    var h = document.createElement('h2'); h.textContent = title; help.appendChild(h);
    lines.forEach(function (l) {
      var p = document.createElement('p'); p.textContent = l; help.appendChild(p);
    });
  }
  function diagnose() {
    say('Diagnostic en cours…');
    get('office/diagnostic').then(function (d) {
      help.hidden = false; help.textContent = '';
      var h = document.createElement('h2'); h.textContent = 'Diagnostic de l’éditeur'; help.appendChild(h);
      (d.checks || []).forEach(function (c) {
        var row = document.createElement('div'); row.className = 'ax-check-row ' + (c.ok ? 'ok' : 'ko');
        var s = document.createElement('strong'); s.textContent = (c.ok ? '✔ ' : '✖ ') + c.name; row.appendChild(s);
        var t = document.createElement('span'); t.textContent = c.detail || ''; row.appendChild(t);
        if (!c.ok && c.fix) { var f = document.createElement('em'); f.textContent = 'Que faire : ' + c.fix; row.appendChild(f); }
        help.appendChild(row);
      });
      say('Diagnostic terminé.');
    }).catch(function () { say('Diagnostic impossible.'); });
  }
  document.getElementById('ax-diag').addEventListener('click', diagnose);
  if (boot.volet) diagnose = function () { signaler('echec'); };   // 5.6.101 (lot V) : pas de diagnostic dans le cadre ; la page parente se replie

  var timer = setTimeout(function () {
    say('L’éditeur tarde à répondre.');
    showHelp('L’éditeur ne s’affiche pas ?', [
      'Le plus souvent, le serveur de documents refuse d’être affiché dans cette page (frame-ancestors) ou ne parvient pas à joindre cette interface (adresse de retour).',
      'Cliquez sur « Un problème ? » pour lancer le diagnostic : il indique la correction exacte.'
    ]);
  }, 25000);

  (boot.config_api ? get(boot.config_api) : get('office/config', { path: boot.path, mode: boot.mode })).then(function (d) {   // 5.6.101 (lot V) : configuration contrôlée du volet
    if (d.error) { clearTimeout(timer); say(''); showHelp('Ouverture impossible', [d.message || d.error]); signaler('echec'); return; }
    var script = document.createElement('script');
    script.src = d.server_url + '/web-apps/apps/api/documents/api.js';
    script.onerror = function () {
      clearTimeout(timer); say('Serveur de documents injoignable.'); diagnose();
    };
    script.onload = function () {
      var cfg = d.config;
      if (d.document_id) { window.axOfficeOpened = d.document_id; window.dispatchEvent(new CustomEvent('ax-office-opened', {detail: {document_id: d.document_id}})); }   // 5.6.84 : panneau de suivi
      cfg.events = {
        onAppReady: function () { clearTimeout(timer); say(d.editable ? 'Prêt — vos modifications sont enregistrées automatiquement dans Nextcloud.' : 'Lecture seule.'); signaler('pret'); },
        onDocumentStateChange: function (ev) { say(ev.data ? 'Modification en cours…' : 'Enregistré dans Nextcloud.'); },
        onError: function (ev) { clearTimeout(timer); say('Erreur de l’éditeur : ' + ((ev && ev.data && (ev.data.errorDescription || ev.data.errorCode)) || 'inconnue')); diagnose(); }
      };
      try { window.axEditor = new window.DocsAPI.DocEditor('ax-office', cfg); }
      catch (err) { say('Initialisation impossible.'); diagnose(); }
    };
    document.head.appendChild(script);
  }).catch(function () { clearTimeout(timer); say('Ouverture impossible.'); signaler('echec'); });
})();
