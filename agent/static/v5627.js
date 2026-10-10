/* AxiorHub 5.6.27 — page Dossier : préparation de la fiche, corrections de l'avocat, instructions à l'agent, filtre des pièces.
   Le seul HTML injecté est celui du fil d'échanges, rendu et échappé par le serveur (même rendu que la page). */
(function () {
  'use strict';
  if (window.__axiorhub5627) return;
  window.__axiorhub5627 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var root = $('.d27');
  if (!root) return;
  var matter = root.getAttribute('data-d27-matter');

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = document.createElement('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
    var t = document.createElement('div'); t.className = 'ax-toast' + (bad ? ' bad' : ''); t.textContent = message;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  var q = function (o) { return new URLSearchParams(o).toString(); };

  // ------------------------------------------------------------ fiche : préparation et suivi
  var watching = false;
  function watch(version) {
    if (watching) return; watching = true;
    var tries = 0;
    var timer = setInterval(function () {
      if (++tries > 120) { clearInterval(timer); watching = false; return; }
      api('m5627/etat?' + q({ matter: matter })).then(function (d) {
        if (!d.pending) { clearInterval(timer); watching = false; if (d.version !== version) toast('Fiche du dossier mise à jour.'); location.reload(); }
      }).catch(function () {});
    }, 6000);
  }
  $$('[data-d27-prepare]').forEach(function (b) {
    b.addEventListener('click', function () {
      busy(b, true);
      api('m5627/etat?' + q({ matter: matter })).then(function (s) {
        return api('m5627/fiche/preparer', { matter: matter }).then(function (r) { toast(r.message); watch(s.version); });
      }).catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });
  if ($('.d27-status[data-d27-waiting]')) api('m5627/etat?' + q({ matter: matter })).then(function (s) { watch(s.version); }).catch(function () {});

  // ------------------------------------------------------------ corrections de l'avocat
  $$('[data-d27-edit]').forEach(function (b) {
    b.addEventListener('click', function () {
      var form = $('form.d27-edit[data-d27-section="' + b.getAttribute('data-d27-edit') + '"]');
      if (!form) return;
      form.hidden = !form.hidden;
      if (!form.hidden) $('textarea', form).focus();
    });
  });
  $$('form.d27-edit').forEach(function (form) {
    $('[data-d27-cancel]', form).addEventListener('click', function () { form.hidden = true; });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var save = $('button[type=submit]', form); busy(save, true);
      api('m5627/fiche/corriger', { matter: matter, section: form.getAttribute('data-d27-section'), text: $('textarea', form).value })
        .then(function (r) { toast(r.message); setTimeout(function () { location.reload(); }, 600); })
        .catch(function (e) { toast(e.message, true); busy(save, false); });
    });
  });

  // ------------------------------------------------------------ travailler avec l'agent
  var thread = $('#d27-thread');
  var polling = null;
  function refresh() {
    if (!thread) return Promise.resolve(false);
    return api('m5627/fil?' + q({ matter: matter, n: thread.closest('.d27-agent') ? 8 : 30 })).then(function (d) {
      thread.innerHTML = d.html; thread.scrollTop = thread.scrollHeight; return d.waiting;
    });
  }
  function follow() {
    if (polling) return;
    var tries = 0;
    polling = setInterval(function () {
      refresh().then(function (waiting) { if (!waiting || ++tries > 80) { clearInterval(polling); polling = null; } }).catch(function () {});
    }, 6000);
  }
  if (thread) { thread.scrollTop = thread.scrollHeight; if (thread.hasAttribute('data-d27-waiting')) follow(); }
  var ask = $('#d27-ask');
  if (ask) {
    $$('.d27-chip', ask).forEach(function (chip) {
      chip.addEventListener('click', function () {
        ask.text.value = chip.getAttribute('data-d27-text'); ask.kind.value = chip.getAttribute('data-d27-kind'); ask.text.focus();
      });
    });
    ask.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var text = ask.text.value.trim();
      if (text.length < 3) { ask.text.focus(); toast('Écrivez votre demande à l’agent.', true); return; }
      var send = $('button[type=submit]', ask); busy(send, true);
      api('m5627/agent', { matter: matter, text: text, kind: ask.kind.value }).then(function (r) {
        toast(r.message); ask.text.value = ''; return refresh().then(follow);
      }).catch(function (e) { toast(e.message, true); }).then(function () { busy(send, false); });
    });
  }

  // ------------------------------------------------------------ pièces : filtre
  $$('[data-d27-filter]').forEach(function (input) {
    var list = $(input.getAttribute('data-d27-filter'));
    input.addEventListener('input', function () {
      var v = input.value.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
      $$('li[data-d27-search]', list).forEach(function (li) { li.hidden = v && li.getAttribute('data-d27-search').indexOf(v) < 0; });
    });
  });
})();
