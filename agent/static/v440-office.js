/* AxiorHub 4.4.0 - chargeur de l'éditeur de documents (ONLYOFFICE / Euro-Office). */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var boot = JSON.parse(document.getElementById('ax-office-boot').textContent);
  var state = document.getElementById('ax-docstate');
  var help = document.getElementById('ax-office-help');

  function say(text) { state.textContent = text; }
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

  var timer = setTimeout(function () {
    say('L’éditeur tarde à répondre.');
    showHelp('L’éditeur ne s’affiche pas ?', [
      'Le plus souvent, le serveur de documents refuse d’être affiché dans cette page (frame-ancestors) ou ne parvient pas à joindre cette interface (adresse de retour).',
      'Cliquez sur « Un problème ? » pour lancer le diagnostic : il indique la correction exacte.'
    ]);
  }, 25000);

  get('office/config', { path: boot.path, mode: boot.mode }).then(function (d) {
    if (d.error) { clearTimeout(timer); say(''); showHelp('Ouverture impossible', [d.message || d.error]); return; }
    var script = document.createElement('script');
    script.src = d.server_url + '/web-apps/apps/api/documents/api.js';
    script.onerror = function () {
      clearTimeout(timer); say('Serveur de documents injoignable.'); diagnose();
    };
    script.onload = function () {
      var cfg = d.config;
      cfg.events = {
        onAppReady: function () { clearTimeout(timer); say(d.editable ? 'Prêt — vos modifications sont enregistrées automatiquement dans Nextcloud.' : 'Lecture seule.'); },
        onDocumentStateChange: function (ev) { say(ev.data ? 'Modification en cours…' : 'Enregistré dans Nextcloud.'); },
        onError: function (ev) { clearTimeout(timer); say('Erreur de l’éditeur : ' + ((ev && ev.data && (ev.data.errorDescription || ev.data.errorCode)) || 'inconnue')); diagnose(); }
      };
      try { window.axEditor = new window.DocsAPI.DocEditor('ax-office', cfg); }
      catch (err) { say('Initialisation impossible.'); diagnose(); }
    };
    document.head.appendChild(script);
  }).catch(function () { clearTimeout(timer); say('Ouverture impossible.'); });
})();
