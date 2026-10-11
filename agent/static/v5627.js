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
  var messages = {};
  try { var rawMsg = document.getElementById('l5631-msg'); if (rawMsg) messages = JSON.parse(rawMsg.textContent || '{}'); } catch (err) { messages = {}; }
  function msg(key, fallback) { return messages['js.5634.' + key] || fallback; }   // 5.6.34 : messages traduits

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
  // 5.6.34 (B08) : suivi d'un travail précis ; statut HTTP et format contrôlés ; reprises réseau bornées (backoff) ; au-delà de
  // 12 minutes ou après une perte de session, le suivi s'interrompt AVEC une explication et les boutons redeviennent utilisables.
  var watching = false, watchTimer = null, LIMIT = 12 * 60 * 1000;
  function statusBox() {
    var box = $('.d27-status[data-d34-watch]') || $('.d27-status[data-d27-waiting]');
    if (!box) {
      box = document.createElement('p'); box.className = 'd27-status'; box.setAttribute('role', 'status');
      var card = $('.d27-summary') || root; card.insertBefore(box, card.querySelector('h2') ? card.querySelector('h2').parentNode.nextSibling : card.firstChild);
    }
    box.setAttribute('data-d34-watch', ''); return box;
  }
  function release() { $$('[data-d27-prepare]').forEach(function (b) { busy(b, false); }); }
  function poll() {
    return fetch(prefix + '/api440/m5627/etat?' + q({ matter: matter }), { credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401 || r.status === 403) throw { kind: 'auth' };
      if (r.status >= 500) throw { kind: 'server' };
      if ((r.headers.get('Content-Type') || '').indexOf('application/json') < 0) throw { kind: 'format' };
      return r.json().catch(function () { throw { kind: 'format' }; });
    });
  }
  function watch(version) {
    if (watching) return; watching = true;
    var started = Date.now(), delay = 6000, failures = 0, box = statusBox();
    box.classList.remove('bad'); box.textContent = '⏳ ' + msg('watch_running', 'Préparation en cours') + '…';
    function interrupt(message) {
      watching = false; clearTimeout(watchTimer); release();
      box.classList.add('bad'); box.textContent = message + ' ';
      var resume = document.createElement('button'); resume.type = 'button'; resume.className = 'ax-btn ghost'; resume.textContent = msg('watch_resume', 'Reprendre le suivi');
      resume.addEventListener('click', function () { watch(version); });
      var see = document.createElement('a'); see.className = 'ax-btn ghost'; see.textContent = msg('watch_see', 'Voir le travail');
      see.href = prefix + '/dossier?' + q({ id: matter, vue: 'travaux' });
      box.appendChild(resume); box.appendChild(see);
    }
    function finish(d) {
      watching = false; clearTimeout(watchTimer); release();
      if (d.state === 'failed') { box.classList.add('bad'); box.textContent = msg('watch_failed', 'La préparation a échoué : ') + (d.message || 'erreur') + msg('watch_kept', ' La dernière fiche valable reste affichée.'); return; }
      if (d.state === 'cancelled') { box.textContent = msg('watch_cancelled', 'Préparation annulée.'); return; }
      if (d.version !== version) {
        box.textContent = msg('watch_new', 'Nouvelle version de la fiche disponible : ') + 'v' + d.version + '. ';
        var open = document.createElement('button'); open.type = 'button'; open.className = 'ax-btn'; open.textContent = msg('watch_open', 'Ouvrir la nouvelle version');
        open.addEventListener('click', function () {
          var dirty = $$('form.d27-edit:not([hidden]) textarea, #d27-text').some(function (t) { return t.value.trim() && t.value !== t.defaultValue; });
          if (dirty && !window.confirm(msg('watch_dirty', 'Une saisie est en cours : ouvrir la nouvelle version la fera perdre. Continuer ?'))) return;
          location.reload();
        });
        box.appendChild(open);
      } else box.textContent = msg('watch_done', 'Préparation terminée.');
    }
    function tick() {
      poll().then(function (d) {
        failures = 0; delay = 6000;
        if (!d.pending) return finish(d);
        box.classList.remove('bad');
        box.textContent = '⏳ ' + (d.state === 'running' ? msg('watch_running', 'Préparation en cours') : msg('watch_queued', 'Préparation en file d’attente')) + (d.step ? ' : ' + d.step : '') + '…';
        if (Date.now() - started > LIMIT) return interrupt(msg('watch_interrupted', 'Le suivi est interrompu ; le travail peut continuer sur le serveur.'));
        watchTimer = setTimeout(tick, delay);
      }).catch(function (err) {
        if (err && err.kind === 'auth') return interrupt(msg('watch_session', 'Votre session a expiré : reconnectez-vous. Le suivi est interrompu ; le travail peut continuer sur le serveur.'));
        failures += 1; delay = Math.min(60000, delay * 2);
        if (failures > 5 || Date.now() - started > LIMIT) return interrupt(msg('watch_interrupted', 'Le suivi est interrompu ; le travail peut continuer sur le serveur.'));
        box.classList.add('bad');
        box.textContent = (err && err.kind === 'server' ? msg('watch_server', 'Le serveur rencontre une erreur') : err && err.kind === 'format' ? msg('watch_format', 'Réponse inattendue du serveur') : msg('watch_offline', 'Connexion perdue')) +
          msg('watch_retry', ' : nouvel essai dans # s.').replace('#', String(Math.round(delay / 1000)));
        watchTimer = setTimeout(tick, delay);
      });
    }
    tick();
  }
  $$('[data-d27-prepare]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;
      busy(b, true);
      api('m5627/etat?' + q({ matter: matter })).then(function (s) {
        return api('m5627/fiche/preparer', { matter: matter }).then(function (r) { toast(r.message); watch(s.version); });
      }).catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });
  if ($('.d27-status[data-d27-waiting]')) api('m5627/etat?' + q({ matter: matter })).then(function (s) { watch(s.version); }).catch(function () {});

  // 5.6.34 (B04, B09) : correction face à une proposition de l'agent ; rapprochement de parties sur confirmation
  $$('[data-d34-correction]').forEach(function (b) {
    b.addEventListener('click', function () {
      busy(b, true);
      api('m5627/fiche/correction', { matter: matter, section: b.getAttribute('data-section'), action: b.getAttribute('data-d34-correction'), id: b.getAttribute('data-id') || '' })
        .then(function (r) { toast(r.message); setTimeout(function () { location.reload(); }, 600); })
        .catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });
  $$('[data-d34-merge]').forEach(function (b) {
    b.addEventListener('click', function () {
      busy(b, true);
      api('m5627/fiche/rapprocher', { matter: matter, a: b.getAttribute('data-a'), b: b.getAttribute('data-b'), decision: b.getAttribute('data-d34-merge') })
        .then(function (r) { toast(r.message); b.closest('li').remove(); })
        .catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });

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
      api('m5627/fiche/corriger', { matter: matter, section: form.getAttribute('data-d27-section'), text: $('textarea', form).value, revision: form.getAttribute('data-d34-revision') })
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

  // ------------------------------------------------------------ 5.6.29 : pause de l'IA, consignes, relances suspendues
  $$('[data-d27-pause]').forEach(function (b) {
    b.addEventListener('click', function () {
      var on = b.getAttribute('data-d27-pause') === '1';
      if (on && !window.confirm('Mettre l’IA en pause sur ce dossier ? Les travaux automatiques en file seront annulés ; vos propres demandes restent possibles.')) return;
      busy(b, true);
      api('m5627/pause', { matter: matter, on: on ? '1' : '0' }).then(function (r) { toast(r.message); setTimeout(function () { location.reload(); }, 700); })
        .catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });
  $$('form[data-d27-consignes]').forEach(function (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var save = $('button[type=submit]', form); busy(save, true);
      api('m5627/consignes', { matter: matter, text: $('textarea', form).value }).then(function (r) { toast(r.message); })
        .catch(function (e) { toast(e.message, true); }).then(function () { busy(save, false); });
    });
  });
  $$('[data-d27-reset]').forEach(function (b) {
    b.addEventListener('click', function () {
      busy(b, true);
      api('m5627/reautoriser', { matter: matter, kind: b.getAttribute('data-d27-reset') }).then(function (r) { toast(r.message); b.closest('li').remove(); })
        .catch(function (e) { toast(e.message, true); busy(b, false); });
    });
  });

  // ------------------------------------------------------------ pièces : filtre
  $$('[data-d27-filter]').forEach(function (input) {
    var list = $(input.getAttribute('data-d27-filter'));
    input.addEventListener('input', function () {
      var v = input.value.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
      $$('li[data-d27-search]', list).forEach(function (li) { li.hidden = v && li.getAttribute('data-d27-search').indexOf(v) < 0; });
    });
  });
})();
