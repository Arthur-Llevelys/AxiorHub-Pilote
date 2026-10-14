/* AxiorHub 5.6.32 — sources juridiques, contrôle des références, veille, compétences, documents de procédure, dossiers de travail.
   Formulaires « data-m5632 » envoyés en JSON (jeton CSRF) ; le seul HTML inséré est le rapport de contrôle produit par le serveur
   (échappé côté serveur) ; tout le reste passe par textContent. */
(function () {
  'use strict';
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var messages = {};
  try { var raw = document.getElementById('l5631-msg'); if (raw) messages = JSON.parse(raw.textContent || '{}'); } catch (err) { messages = {}; }
  function msg(key, fallback) { return messages['js.5632.' + key] || fallback; }
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }

  function api(route, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
    return fetch(prefix + '/api440/m5632/' + route, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: msg('bad_answer', 'Réponse inattendue du serveur.') }; });
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }

  /* Valeurs d'un formulaire : une case cochée de même nom répétée donne une liste (thèmes, jours, documents). */
  function collect(form) {
    var data = {}, multi = {};
    $$('input[type=checkbox][value]', form).forEach(function (f) { if (f.name && f.getAttribute('value') !== 'on') multi[f.name] = true; });
    $$('input,select,textarea', form).forEach(function (f) {
      if (!f.name || f.type === 'file') return;
      if (multi[f.name]) { data[f.name] = data[f.name] || []; if (f.checked) data[f.name].push(f.value); return; }
      if (f.type === 'checkbox') { data[f.name] = f.checked; return; }
      if (f.type === 'radio' && !f.checked) return;
      data[f.name] = f.value;
    });
    /* texte mis en forme (signature) : nettoyé par le serveur avant enregistrement */
    $$('[data-m5632-html]', form).forEach(function (ed) { data[ed.getAttribute('data-m5632-html')] = ed.innerHTML; });
    return data;
  }

  /* ---- Paramètres › Messagerie : mise en forme de la signature et aperçu de la police. */
  document.addEventListener('mousedown', function (ev) { if (ev.target.closest && ev.target.closest('[data-m5632-cmd]')) ev.preventDefault(); });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-m5632-cmd]');
    if (!b) return;
    var form = b.closest('form'), ed = form && $('[data-m5632-html]', form);
    if (!ed) return;
    ed.focus();
    var cmd = b.getAttribute('data-m5632-cmd');
    if (cmd === 'createLink') {
      var href = window.prompt(msg('link_prompt', 'Adresse du lien (https://… ou mailto:…)'), 'https://');
      if (href && /^(https?:|mailto:)/i.test(href.trim())) document.execCommand('createLink', false, href.trim());
    } else document.execCommand(cmd, false, null);
  });
  document.addEventListener('change', function (ev) {
    var sel = ev.target.closest && ev.target.closest('[data-m5632-font], select[name=size]');
    if (!sel) return;
    var form = sel.closest('form'), ed = form && $('[data-m5632-html]', form);
    if (!ed) return;
    var font = $('[data-m5632-font]', form), size = $('select[name=size]', form);
    var opt = font && font.options[font.selectedIndex];
    if (opt) ed.style.fontFamily = opt.getAttribute('data-font');
    if (size) ed.style.fontSize = size.value + 'pt';
  });
  /* styles posés par CSSOM (la politique de sécurité refuse les attributs style dans le HTML) */
  $$('[data-m5632-style]').forEach(function (n) { n.style.cssText = n.getAttribute('data-m5632-style'); });
  $$('option[data-font]').forEach(function (o) { o.style.fontFamily = o.getAttribute('data-font'); });
  function readFile(input) {
    return new Promise(function (resolve, reject) {
      var file = input && input.files && input.files[0];
      if (!file) { reject(new Error(msg('file_required', 'Choisissez un fichier.'))); return; }
      if (file.size > 5 * 1024 * 1024) { reject(new Error(msg('file_too_big', 'Fichier trop volumineux (5 Mo au plus).'))); return; }
      var reader = new FileReader();
      reader.onload = function () { var s = String(reader.result || ''); resolve({ filename: file.name, content: s.slice(s.indexOf(',') + 1) }); };
      reader.onerror = function () { reject(new Error(msg('file_unreadable', 'Fichier illisible.'))); };
      reader.readAsDataURL(file);
    });
  }
  function say(form, text, bad) {
    var out = $('.f5632-out', form); if (!out) return;
    out.textContent = text || ''; out.classList.toggle('bad', !!bad);
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-m5632]');
    if (!form) return;
    ev.preventDefault();
    var route = form.getAttribute('data-m5632'), btn = ev.submitter || $('button[type=submit]', form);
    var data = collect(form);
    var ready = $('[data-m5632-file]', form) ? readFile($('[data-m5632-file]', form)).then(function (f) { data.filename = f.filename; data.content = f.content; })
                                               : Promise.resolve();
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    say(form, msg('working', 'Traitement en cours…'));
    ready.then(function () { return api(route, data); }).then(function (res) {
      say(form, typeof res.message === 'string' ? res.message : msg('done', 'Enregistré.'));
      if (route === 'controle/run') {
        var box = form.parentNode && $('[data-m5632-report]', form.parentNode);
        if (box) box.innerHTML = res.html || '';   // rapport construit et échappé par le serveur (controle5632.report_html)
      }
      if (route === 'dp/start' || route.indexOf('dp/') === 0) renderMission(form.closest('.d5632') || document, res);
      if (route === 'dossiers/create' && res.skipped && res.skipped.length) {
        say(form, (res.message || '') + ' ' + msg('folders_skipped', 'Non créés :') + ' ' + res.skipped.map(function (s) { return s.name + ' (' + s.error + ')'; }).join(', '), true);
      }
      if (form.hasAttribute('data-reload')) setTimeout(function () { location.reload(); }, 600);
    }).catch(function (e) { say(form, e.message, true); }).then(function () {
      if (btn) { btn.disabled = false; btn.setAttribute('aria-busy', 'false'); }
    });
  });

  /* ---- Veille : « Actualiser » dans le cadre d'Aujourd'hui. */
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-v5632="refresh"]');
    if (!b) return;
    ev.preventDefault(); b.disabled = true;
    api('veille/refresh', {}).then(function (res) { b.textContent = res.message || msg('refreshing', 'Collecte lancée…'); })
      .catch(function (e) { b.textContent = e.message; }).then(function () { setTimeout(function () { b.disabled = false; }, 4000); });
  });

  /* ---- Mon style › Réglages : ajouter ou retirer un dossier de travail (ligne vidée = retirée à l'enregistrement). */
  document.addEventListener('click', function (ev) {
    var add = ev.target.closest && ev.target.closest('[data-s5632-add]');
    if (add) {
      var rows = $('[data-s5632-rows]', add.parentNode), last = rows && $$('[data-s5632-row]', rows).pop();
      if (!last) return;
      var count = $$('[data-s5632-row]', rows).length;
      if (count >= 20) { add.disabled = true; return; }
      var copy = last.cloneNode(true);
      $$('input,select', copy).forEach(function (f) {
        f.name = f.name.replace(/_\d+$/, '_' + count);
        if (f.tagName === 'INPUT') f.value = ''; else f.value = 'travail';
      });
      copy.hidden = false;
      rows.appendChild(copy);
      var first = $('input', copy); if (first) first.focus();
      return;
    }
    var del = ev.target.closest && ev.target.closest('[data-s5632-del]');
    if (del) {
      var row = del.closest('[data-s5632-row]'); if (!row) return;
      var input = $('input', row); if (input) input.value = '';
      row.hidden = true;
    }
  });

  /* ---- Briefing du matin : résumé oral, lu par la synthèse vocale du cabinet (Kokoro, Chatterbox, espeak…), paragraphe par paragraphe. */
  var oral = { audio: null, url: '', gen: 0 };
  function oralStop(box) {
    oral.gen++;
    if (oral.audio) { oral.audio.pause(); oral.audio = null; }
    if (oral.url) { URL.revokeObjectURL(oral.url); oral.url = ''; }
    if (box) { $('[data-o5632=stop]', box).hidden = true; $('[data-o5632=play]', box).hidden = false; }
  }
  function chunks(text) {
    var out = [];
    String(text || '').split(/\n\s*\n/).forEach(function (p) {
      p = p.trim(); if (!p) return;
      while (p.length > 1800) { var cut = p.lastIndexOf('. ', 1800); cut = cut > 400 ? cut + 1 : 1800; out.push(p.slice(0, cut)); p = p.slice(cut).trim(); }
      if (p) out.push(p);
    });
    return out;
  }
  // 5.6.47 : le paragraphe suivant est synthétisé PENDANT la lecture du précédent ; entre les deux, seulement la pause réglée
  // (Paramètres › Synthèse vocale, 500 ms par défaut) au lieu du temps de synthèse (3 à 4 s avec Kokoro).
  function fetchPart(text) {
    var p = fetch(prefix + '/api440/m568/speech', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ text: text }) })
      .then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.message || d.error || msg('oral_voice_error', 'Synthèse vocale indisponible.')); });
        var pause = parseInt(r.headers.get('X-AxiorHub-Pause-Ms') || '500', 10);
        return r.blob().then(function (blob) { return { blob: blob, pause: isNaN(pause) ? 500 : Math.max(0, Math.min(3000, pause)) }; });
      });
    p.catch(function () {});   // erreur traitée quand ce paragraphe est lu
    return p;
  }
  function speak(box, parts, i, gen, pending) {
    if (gen !== oral.gen) return;
    if (i >= parts.length) { oralStop(box); $('.o5632-note', box).textContent = msg('oral_done', 'Lecture terminée.'); return; }
    (pending || fetchPart(parts[i])).then(function (res) {
        if (gen !== oral.gen) return;
        var next = i + 1 < parts.length ? fetchPart(parts[i + 1]) : null;
        if (oral.url) URL.revokeObjectURL(oral.url);
        oral.url = URL.createObjectURL(res.blob);
        oral.audio = new Audio(oral.url);
        oral.audio.onended = function () { setTimeout(function () { speak(box, parts, i + 1, gen, next); }, res.pause); };
        return oral.audio.play();
      }).catch(function (e) { oralStop(box); $('.o5632-note', box).textContent = e.message; });
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-o5632]');
    if (!b) return;
    var box = b.closest('.o5632'), out = $('.o5632-text', box), note = $('.o5632-note', box), action = b.getAttribute('data-o5632');
    if (action === 'show') {
      if (!out.hidden) { out.hidden = true; b.setAttribute('aria-expanded', 'false'); oralStop(box); $('[data-o5632=play]', box).hidden = true; return; }
      b.disabled = true; note.textContent = msg('working', 'Traitement en cours…');
      api('briefing/oral?report=' + encodeURIComponent(box.getAttribute('data-o5632-report') || '')).then(function (res) {
        out.textContent = '';
        String(res.text || '').split(/\n\s*\n/).forEach(function (p) { if (p.trim()) out.appendChild(el('p', '', p.trim())); });
        out.setAttribute('data-text', res.text || '');
        out.hidden = false; b.setAttribute('aria-expanded', 'true');
        $('[data-o5632=play]', box).hidden = false;
        note.textContent = res.method === 'ia' ? msg('oral_ai', 'Résumé rédigé par l’IA à partir du briefing. Contrôle effectué : les nombres repris correspondent au briefing ; les autres affirmations ne sont pas contrôlées automatiquement.')
          : (res.pending ? msg('oral_pending', 'Version provisoire : l’IA rédige une version plus fluide, rouvrez le résumé dans un instant.')
             : (res.notes && res.notes[0]) || msg('oral_rules', 'Résumé établi sans IA à partir du briefing.'));
      }).catch(function (e) { note.textContent = e.message; }).then(function () { b.disabled = false; });
    } else if (action === 'play') {
      oralStop(box);
      var parts = chunks(out.getAttribute('data-text') || out.textContent);
      if (!parts.length) return;
      b.hidden = true; $('[data-o5632=stop]', box).hidden = false; note.textContent = msg('oral_playing', 'Lecture en cours…');
      speak(box, parts, 0, oral.gen);
    } else if (action === 'stop') {
      oralStop(box); note.textContent = '';
    }
  });

  /* ---- Documents de procédure → agenda → brouillons (DP01–DP05). */
  function renderMission(scope, m) {
    var box = $('[data-m5632-mission]', scope);
    if (!box || !m || !m.id) return;
    box.textContent = '';
    box.setAttribute('data-mission', m.id);
    box.setAttribute('data-revision', m.revision);
    (m.warnings || []).forEach(function (w) { box.appendChild(el('p', 'd5632-warn', w)); });
    if (!(m.events || []).length) box.appendChild(el('p', 'd5632-empty', msg('no_dates', 'Aucune date certaine trouvée dans ces documents.')));
    var list = el('ol', 'd5632-events');
    (m.events || []).forEach(function (ev) {
      var li = el('li', 'd5632-event' + (ev.requires_clarification ? ' d5632-check' : ''));
      li.appendChild(el('strong', '', (ev.start || ev.date || '').replace('T', ' ').slice(0, 16) + ' · ' + (ev.title || '')));
      if (ev.citation || ev.quote) li.appendChild(el('blockquote', '', (ev.quote || '') + (ev.citation ? ' — ' + ev.citation : '')));
      if (ev.requires_clarification) li.appendChild(el('p', 'd5632-warn', msg('to_check', 'Date calculée ou incertaine : à contrôler avant inscription.')));
      var targets = el('div', 'd5632-targets');
      (m.targets || []).forEach(function (t) {
        var lab = el('label', 'f5632-check'), cb = el('input');
        cb.type = 'checkbox'; cb.value = t.id; cb.checked = (ev.targets || []).indexOf(t.id) !== -1;
        lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + t.label)); targets.appendChild(lab);
      });
      if (!(m.targets || []).length) targets.appendChild(el('p', 'd5632-warn', msg('no_calendar', 'Aucun agenda autorisé : réglez-les dans Paramètres › Connexions.')));
      li.appendChild(targets);
      var actions = el('div', 'd5632-actions');
      [['add', msg('add', 'Inscrire')], ['ignore', msg('ignore', 'Ignorer')], ['clarify', msg('clarify', 'À préciser')]].forEach(function (a) {
        var b = el('button', 'ax-btn ghost' + (ev.decision === a[0] ? ' on' : ''), a[1]);
        b.type = 'button'; b.setAttribute('aria-pressed', ev.decision === a[0] ? 'true' : 'false');
        b.addEventListener('click', function () {
          var chosen = $$('input[type=checkbox]', targets).filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
          api('dp/decide', { mission: m.id, key: ev.key, action: a[0], targets: chosen, revision: Number(box.getAttribute('data-revision')) })
            .then(function (res) { renderMission(scope, res); }).catch(function (e) { li.appendChild(el('p', 'd5632-warn', e.message)); });
        });
        actions.appendChild(b);
      });
      li.appendChild(actions);
      Object.keys(ev.effects || {}).forEach(function (t) {
        var fx = ev.effects[t];
        li.appendChild(el('p', fx.state === 'done' ? 'd5632-ok' : 'd5632-warn', t + ' : ' + (fx.state === 'done' ? msg('written', 'inscrit et vérifié') : (fx.error || fx.state))));
      });
      list.appendChild(li);
    });
    box.appendChild(list);
    var bar = el('div', 'd5632-actions');
    var apply = el('button', 'ax-btn', msg('apply', 'Inscrire les dates choisies'));
    apply.type = 'button';
    apply.addEventListener('click', function () {
      apply.disabled = true;
      api('dp/apply', { mission: m.id }).then(function (res) { renderMission(scope, res); }).catch(function (e) { box.appendChild(el('p', 'd5632-warn', e.message)); apply.disabled = false; });
    });
    var drafts = el('button', 'ax-btn ghost', msg('drafts', 'Préparer les brouillons de courriel'));
    drafts.type = 'button';
    /* Destinataires saisis par l'avocat (jamais devinés) : un brouillon par adresse renseignée, aucun envoi. */
    var who = el('div', 'f5632-row');
    [['client', msg('client_email', 'Adresse du client')], ['confrere', msg('confrere_email', 'Adresse du confrère')]].forEach(function (r) {
      var lab = el('label', '', r[1]), input = el('input');
      input.type = 'email'; input.setAttribute('data-role', r[0]); input.autocomplete = 'off';
      lab.appendChild(input); who.appendChild(lab);
    });
    box.appendChild(who);
    drafts.addEventListener('click', function () {
      var recipients = $$('input[data-role]', who).filter(function (i) { return i.value.trim(); })
        .map(function (i) { return { role: i.getAttribute('data-role'), email: i.value.trim() }; });
      if (!recipients.length) { box.appendChild(el('p', 'd5632-warn', msg('recipient_required', 'Renseignez au moins une adresse.'))); return; }
      drafts.disabled = true;
      api('dp/drafts', { mission: m.id, recipients: recipients }).then(function (res) {
        var ul = el('ul', 'd5632-drafts');
        (res.drafts || []).forEach(function (d) { ul.appendChild(el('li', '', (d.subject || '') + ' — ' + (d.state === 'imap_verified' ? msg('draft_imap', 'dans vos Brouillons') : msg('draft_local', 'conservé dans AxiorHub')))); });
        box.appendChild(ul);
      }).catch(function (e) { box.appendChild(el('p', 'd5632-warn', e.message)); }).then(function () { drafts.disabled = false; });
    });
    bar.appendChild(apply); bar.appendChild(drafts);
    box.appendChild(bar);
    (m.drafts || []).forEach(function (d) { box.appendChild(el('p', 'd5632-ok', (d.subject || '') + ' — ' + d.state)); });
  }
})();
