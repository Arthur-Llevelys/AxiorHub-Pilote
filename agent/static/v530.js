/* AxiorHub 5.3.0 — poste de pilotage « Aujourd'hui ».
   Les fragments affichés (fil, colonnes, panneau) sont construits et échappés par le serveur (cockpit530.py) ;
   ce script ne fait qu'appeler l'API et placer ces fragments. Les saisies de l'avocat ne sont jamais injectées en HTML. */
(function () {
  'use strict';
  var root = document.getElementById('c530');
  if (!root) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix') || root.getAttribute('data-prefix') || '', csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var state = { guessed: '', guessedLabel: '', files: [], feed: 'en_cours', timer: null, lastFocus: null };

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = document.createElement('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
    var t = document.createElement('div'); t.className = 'ax-toast' + (bad ? ' bad' : ''); t.textContent = message;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 4500);
  }
  function call(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) { var err = new Error(d.message || d.error); err.code = d.error; throw err; }
      return d;
    });
  }
  function busy(btn, on) { if (btn) { btn.disabled = on; btn.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  function part(name, target) {
    return call('m530/part?name=' + encodeURIComponent(name) + '&prefix=' + encodeURIComponent(prefix)).then(function (d) {
      var el = $(target); if (el && typeof d.html === 'string') el.innerHTML = d.html;
      if (name === 'feed') showFeed(state.feed);
      return d;
    }).catch(function () { /* l'écran reste utilisable : prochaine tentative au cycle suivant */ });
  }
  function refresh() {
    part('header', '#c530-header');
    part('feed', '#c530-feed');
    var th = $('.c530-thread');
    if (th && th.getAttribute('data-waiting') === '1') part('thread', '.c530-thread-wrap');
  }

  // ---------------------------------------------------------------- fil de conversation (enveloppe pour le rafraîchir)
  var thread = $('.c530-thread');
  if (thread && !thread.parentNode.classList.contains('c530-thread-wrap')) {
    var wrap = document.createElement('div'); wrap.className = 'c530-thread-wrap';
    var tools = thread.previousElementSibling;      // 5.6.3 : « Effacer la discussion » est rafraîchi avec le fil
    thread.parentNode.insertBefore(wrap, thread);
    if (tools && tools.classList.contains('c561-thread-tools')) wrap.appendChild(tools);
    wrap.appendChild(thread);
    thread.scrollTop = thread.scrollHeight;
  }

  // ---------------------------------------------------------------- dossier deviné / corrigé
  var text = $('#c530-text'), override = $('#c530-override'), label = $('#c530-dossier-label'), how = $('#c530-dossier-how');
  function effective() { return (override && override.value) || state.guessed; }
  function showDossier() {
    if (override && override.value) {
      label.textContent = override.options[override.selectedIndex].text; how.textContent = 'choisi par vous'; how.className = 'c530-badge chosen';
    } else if (state.guessed) {
      label.textContent = state.guessedLabel; how.textContent = 'deviné par l’agent'; how.className = 'c530-badge guess';
    } else {
      label.textContent = 'à deviner d’après votre demande'; how.textContent = text.value.trim().length > 2 ? 'à préciser' : 'en attente'; how.className = 'c530-badge';
    }
  }
  var guessTimer = null, lastMatter = '';
  function matterChanged() {
    var now = effective();
    if (state.files.length && now !== lastMatter) {
      state.files = []; drawFiles(); toast('Le dossier a changé : ajoutez de nouveau les pièces jointes.', true);
    }
    lastMatter = now;
  }
  if (text) {
    text.addEventListener('input', function () {
      clearTimeout(guessTimer);
      guessTimer = setTimeout(function () {
        call('m530/guess?text=' + encodeURIComponent(text.value.slice(0, 1500))).then(function (g) {
          state.guessed = g.matter || ''; state.guessedLabel = g.label || ''; showDossier(); matterChanged();
        }).catch(function () {});
      }, 450);
    });
    text.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); } });
  }
  if (override) override.addEventListener('change', function () { showDossier(); matterChanged(); });

  // ---------------------------------------------------------------- pièces jointes
  var fileInput = $('#c530-file'), filesBox = $('#c530-files');
  function drawFiles() {
    filesBox.textContent = '';
    state.files.forEach(function (f, i) {
      var chip = document.createElement('span'); chip.className = 'c530-file'; chip.textContent = f.name;
      var x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Retirer ' + f.name);
      x.addEventListener('click', function () { state.files.splice(i, 1); drawFiles(); });
      chip.appendChild(x); filesBox.appendChild(chip);
    });
  }
  $('#c530-attach').addEventListener('click', function () {
    if (!effective()) { toast('Indiquez d’abord le dossier (dans votre phrase ou avec « Corriger ») : les pièces sont rattachées à ce dossier.', true); return; }
    fileInput.click();
  });
  fileInput.addEventListener('change', function () {
    var matter = effective();
    Array.prototype.slice.call(fileInput.files || []).slice(0, 3 - state.files.length).forEach(function (file) {
      toast('Lecture de « ' + file.name + ' »…');
      fetch(prefix + '/assistant/attachment', { method: 'POST', credentials: 'same-origin', body: file,
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf, 'X-Attachment-Name': encodeURIComponent(file.name), 'X-Attachment-Matter': matter, 'X-Attachment-Key': '' }
      }).then(function (r) { return r.json(); }).then(function (res) {
        if (!res.attachment_id) throw new Error(res.message || res.error || 'lecture impossible');
        state.files.push({ id: res.attachment_id, name: res.name }); lastMatter = matter; drawFiles();
      }).catch(function (err) { toast('Pièce non ajoutée : ' + err.message, true); });
    });
    fileInput.value = '';
  });

  // ---------------------------------------------------------------- dictée (passerelle existante de la 3.1 : mode rapide ou Vocal local)
  var mic = $('#c530-mic');
  function dictationButton() { var bar = text && text.nextElementSibling; return bar && bar.classList.contains('dictation-bar') ? $('.micro-button', bar) : null; }
  mic.addEventListener('click', function () {
    var b = dictationButton();
    if (!b) { toast('Dictée indisponible sur ce navigateur ; activez la passerelle Vocal locale dans Outils › Confort.', true); return; }
    b.click();
  });
  var syncMic = function () { var b = dictationButton(); if (b) mic.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'true' : 'false'); };
  setTimeout(function () {
    var b = dictationButton();
    if (b && window.MutationObserver) new MutationObserver(syncMic).observe(b, { attributes: true, attributeFilter: ['aria-pressed'] });
  }, 300);

  // ---------------------------------------------------------------- envoi
  var sendBtn = $('#c530-send');
  function send(extra) {
    var value = extra && extra.text ? extra.text : text.value.trim();
    if (value.length < 3) { toast('Écrivez ou dictez d’abord votre instruction.', true); text.focus(); return; }
    var body = { text: value, matter: extra && extra.matter ? extra.matter : (override.value || ''), attachments: extra ? [] : state.files.map(function (f) { return f.id; }) };
    if (extra && extra.mode) body.mode = extra.mode;
    busy(sendBtn, true);
    call('m530/ask', body).then(function (res) {
      if (!extra) { text.value = ''; state.files = []; drawFiles(); override.value = ''; state.guessed = ''; showDossier(); }
      toast(res.message || 'Instruction transmise.');
      return part('thread', '.c530-thread-wrap').then(function () { var th = $('.c530-thread'); if (th) th.scrollTop = th.scrollHeight; part('feed', '#c530-feed'); part('header', '#c530-header'); });
    }).catch(function (err) { toast(err.message, true); }).then(function () { busy(sendBtn, false); });
  }
  sendBtn.addEventListener('click', function () { send(); });

  // ---------------------------------------------------------------- colonnes : filtres, relance, tâches, routines
  function showFeed(name) {
    state.feed = name;
    $$('#c530-feed [data-feed]').forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-feed') === name ? 'true' : 'false'); });
    $$('#c530-feed [data-status]').forEach(function (l) { l.hidden = l.getAttribute('data-status') !== name; });
  }
  root.addEventListener('click', function (ev) {
    var t = ev.target.closest ? ev.target : null; if (!t) return;
    var f = t.closest('[data-feed]'); if (f) { showFeed(f.getAttribute('data-feed')); return; }
    var r = t.closest('[data-retry]');
    if (r) {
      busy(r, true);
      call('m530/retry', { job: r.getAttribute('data-retry') }).then(function (res) {
        toast(res.message); part('feed', '#c530-feed'); if (r.closest('#c530-drawer')) closeDrawer();
      }).catch(function (err) { toast(err.message, true); busy(r, false); });
      return;
    }
    // 5.6.3 : arrêter une tâche demandée, effacer la discussion, annuler un travail depuis son détail
    var st = t.closest('[data-stop]');
    if (st) {
      busy(st, true);
      call('m530/stop', { ref: st.getAttribute('data-stop') }).then(function (res) { toast(res.message); part('thread', '.c530-thread-wrap'); part('feed', '#c530-feed'); })
        .catch(function (err) { toast(err.message, true); busy(st, false); });
      return;
    }
    var cl = t.closest('[data-clear]');
    if (cl) {
      if (!confirm('Effacer la discussion affichée ? Les travaux en cours, documents et brouillons ne sont pas touchés.')) return;
      busy(cl, true);
      call('m530/clear', {}).then(function (res) { toast(res.message); part('thread', '.c530-thread-wrap'); })
        .catch(function (err) { toast(err.message, true); busy(cl, false); });
      return;
    }
    // 5.6.24 : boutons-icônes — À relire (ignorer, supprimer), Ce que fait l'agent (ignorer un blocage), Ma journée (événements, tâches)
    var ig = t.closest('[data-ignore]');
    if (ig) {
      busy(ig, true);
      call('m530/item/ignore', { item: ig.getAttribute('data-ignore') }).then(function (res) { toast(res.message); part('review', '#c530-review'); })
        .catch(function (err) { toast(err.message, true); busy(ig, false); });
      return;
    }
    var tr = t.closest('[data-trash]');
    if (tr) {
      if (!confirm('Supprimer « ' + (tr.getAttribute('data-name') || 'ce document') + ' » ? Seul un document produit par l’agent et resté inchangé peut l’être ; il part dans la corbeille (restaurable).')) return;
      busy(tr, true);
      call('m530/item/trash', { item: tr.getAttribute('data-trash') }).then(function (res) { toast(res.message); part('review', '#c530-review'); })
        .catch(function (err) { toast(err.message, true); busy(tr, false); });
      return;
    }
    var dm = t.closest('[data-dismiss]');
    if (dm) {
      busy(dm, true);
      call('m530/dismiss', { job: dm.getAttribute('data-dismiss') }).then(function (res) { toast(res.message); part('feed', '#c530-feed'); })
        .catch(function (err) { toast(err.message, true); busy(dm, false); });
      return;
    }
    var add = t.closest('[data-day-add]');
    if (add) { dayForm(add.getAttribute('data-day-add') === 'event' ? 'event/create' : 'task/create', {}, add); return; }
    var ee = t.closest('[data-ev-edit]');
    if (ee) { dayForm('event/edit', { event: ee.getAttribute('data-ev-edit'), title: ee.getAttribute('data-title'), start: ee.getAttribute('data-start'), duration_minutes: ee.getAttribute('data-duration') }, ee); return; }
    var te = t.closest('[data-tk-edit]');
    if (te) { dayForm('task/edit', { task: te.getAttribute('data-tk-edit'), title: te.getAttribute('data-title'), due: te.getAttribute('data-due') }, te); return; }
    var ed = t.closest('[data-ev-del]') || t.closest('[data-tk-del]');
    if (ed) {
      var isEv = ed.hasAttribute('data-ev-del');
      if (!confirm('Supprimer « ' + (ed.getAttribute('data-title') || '') + ' » ?')) return;
      busy(ed, true);
      call(isEv ? 'm530/event/delete' : 'm530/task/delete', isEv ? { event: ed.getAttribute('data-ev-del') } : { task: ed.getAttribute('data-tk-del') })
        .then(function (res) { toast(res.message); part('day', '#c530-day'); }).catch(function (err) { toast(err.message, true); busy(ed, false); });
      return;
    }
    var cj = t.closest('[data-cancel-job]');
    if (cj) {
      if (!confirm('Annuler ce travail ? Un document déjà déposé reste conservé.')) return;
      busy(cj, true);
      call('m530/cancel', { job: cj.getAttribute('data-cancel-job') }).then(function (res) { toast(res.message); closeDrawer(); part('feed', '#c530-feed'); })
        .catch(function (err) { toast(err.message, true); busy(cj, false); });
      return;
    }
    // 5.6.1 : ouvrir dans Nextcloud le fichier produit ou analysé (fenêtre ouverte tout de suite pour ne pas être bloquée)
    var op = t.closest('[data-open-path]');
    if (op) {
      var w = window.open('about:blank', '_blank'); busy(op, true);
      call('m530/open?' + new URLSearchParams({ path: op.getAttribute('data-open-path'), matter: op.getAttribute('data-open-matter') || '' }).toString())
        .then(function (d) { if (w) { w.opener = null; w.location.href = d.url; } else { window.location.href = d.url; } })
        .catch(function (err) { if (w) w.close(); toast(err.message, true); })
        .then(function () { busy(op, false); });
      return;
    }
    // 5.6.1 : détail d'un travail de « Ce que fait l'agent »
    var job = t.closest('[data-job]');
    if (job && !t.closest('a')) { openJob(job.getAttribute('data-job'), job.querySelector('.c561-open') || job); return; }
    var tab = t.closest('[data-tab]');
    if (tab) {
      var k = tab.getAttribute('data-tab');
      $$('#c530-routines [data-tab]').forEach(function (b) { b.setAttribute('aria-selected', b === tab ? 'true' : 'false'); });
      $$('#c530-routines [data-panel]').forEach(function (p) { p.hidden = p.getAttribute('data-panel') !== k; });
      return;
    }
    var prep = t.closest('[data-prepare]');
    if (prep) { busy(prep, true); send({ text: prep.getAttribute('data-prepare'), matter: prep.getAttribute('data-matter'), mode: 'document' }); prep.textContent = 'En préparation…'; return; }
    var item = t.closest('[data-item]');
    if (item && item.classList.contains('c530-item')) { openItem(item.getAttribute('data-item'), item); }
  });
  root.addEventListener('change', function (ev) {
    var box = ev.target.closest && ev.target.closest('[data-task]'); if (!box) return;
    box.disabled = true;
    call('m530/task', { task: box.getAttribute('data-task'), done: box.checked }).then(function (res) { toast(res.message); part('day', '#c530-day'); })
      .catch(function (err) { toast(err.message, true); box.checked = !box.checked; box.disabled = false; });
  });

  // ---------------------------------------------------------------- panneau de relecture
  var drawer = $('#c530-drawer'), content = $('#c530-drawer-content');

  // 5.6.24 : formulaire d'ajout ou de modification (événement, tâche) dans le panneau latéral, construit sans HTML injecté
  function dayForm(route, values, from) {
    var isEvent = route.indexOf('event/') === 0, isNew = /create$/.test(route);
    state.lastFocus = from || null;
    content.textContent = '';
    var form = document.createElement('form'); form.className = 'c530-dayform';
    var h = document.createElement('h2'); h.id = 'c530-drawer-title';
    h.textContent = (isNew ? 'Ajouter ' : 'Modifier ') + (isEvent ? 'un événement' : 'une tâche');
    form.appendChild(h);
    function field(label, name, type, value, extra) {
      var l = document.createElement('label'); l.textContent = label;
      var i = document.createElement('input'); i.name = name; i.type = type; i.value = value || '';
      Object.keys(extra || {}).forEach(function (k) { i.setAttribute(k, extra[k]); });
      l.appendChild(i); form.appendChild(l); return i;
    }
    var title = field('Intitulé', 'title', 'text', values.title, { required: 'required', minlength: '3', maxlength: '200' });
    var start, duration, due;
    if (isEvent) {
      var d = new Date(); d.setMinutes(0, 0, 0); d.setHours(d.getHours() + 1);
      var local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      start = field('Début', 'start', 'datetime-local', values.start || local, { required: 'required' });
      duration = field('Durée (minutes)', 'duration_minutes', 'number', values.duration_minutes || '60', { min: '5', max: '1440', step: '5' });
    } else {
      due = field('Échéance (facultative)', 'due', 'date', values.due || '');
    }
    var ok = document.createElement('button'); ok.type = 'submit'; ok.className = 'ax-btn'; ok.textContent = 'Enregistrer';
    var cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'ax-btn ghost'; cancel.textContent = 'Annuler';
    cancel.addEventListener('click', closeDrawer);
    var row = document.createElement('p'); row.appendChild(ok); row.appendChild(document.createTextNode(' ')); row.appendChild(cancel); form.appendChild(row);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var body = { title: title.value.trim() };
      if (values.event) body.event = values.event;
      if (values.task) body.task = values.task;
      if (isEvent) { body.start = start.value; body.duration_minutes = Number(duration.value || 60); } else { body.due = due.value; }
      busy(ok, true);
      call('m530/' + route, body).then(function (res) { toast(res.message); closeDrawer(); part('day', '#c530-day'); })
        .catch(function (err) { toast(err.message, true); busy(ok, false); });
    });
    content.appendChild(form);
    drawer.hidden = false; title.focus();
  }
  function closeDrawer() { drawer.hidden = true; content.textContent = ''; if (state.lastFocus) state.lastFocus.focus(); }
  function openItem(id, from) {
    state.lastFocus = from || null;
    drawer.hidden = false; content.textContent = 'Ouverture…';
    call('m530/item?id=' + encodeURIComponent(id) + '&prefix=' + encodeURIComponent(prefix)).then(function (d) {
      content.innerHTML = d.html; var c = $('.c530-close', drawer); if (c) c.focus();
    }).catch(function (err) { content.textContent = err.message; });
  }
  function openJob(id, from) {
    state.lastFocus = from || null;
    drawer.hidden = false; content.textContent = 'Ouverture…';
    call('m530/job?id=' + encodeURIComponent(id) + '&prefix=' + encodeURIComponent(prefix)).then(function (d) {
      content.innerHTML = d.html; var c = $('.c530-close', drawer); if (c) c.focus();
    }).catch(function (err) { content.textContent = err.message; });
  }
  drawer.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-close]')) { closeDrawer(); return; }
    var act = ev.target.closest('[data-act]'); if (!act) return;
    var foot = act.closest('[data-item]'), id = foot.getAttribute('data-item'), name = act.getAttribute('data-act');
    var done = function (res) { toast(res.message); closeDrawer(); part('review', '#c530-review'); part('feed', '#c530-feed'); part('thread', '.c530-thread-wrap'); };
    busy(act, true);
    if (name === 'revise') {
      var input = $('#c530-rev', drawer), key = $('input[name=key]', drawer);
      call('m530/revise', { item: id, instruction: input.value, key: key ? key.value : '' }).then(done).catch(function (err) { toast(err.message, true); busy(act, false); });
    } else {
      call('m530/review', { item: id, decision: name }).then(done).catch(function (err) { toast(err.message, true); busy(act, false); });
    }
  });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !drawer.hidden) closeDrawer(); });

  // ---------------------------------------------------------------- actualisation douce (pas quand l'onglet est caché)
  // 5.6.9 : relecture de secours espacée quand le flux en direct est connecté (il déclenche déjà les mises à jour).
  (function tick() { state.timer = setTimeout(function () { if (!document.hidden) refresh(); tick(); }, window.axiorhubLive?.connected ? 60000 : 20000); })();
})();
