/* AxiorHub 5.6.34 — sélecteurs de dossier et de document, formulaires « data-m34-form » (JSON + jeton CSRF), Annuler et Détail des
   demandes, Alertes du cabinet, agenda local (import, export, sauvegarde), profil du modèle local.
   Aucun HTML n'est inséré : tout le contenu dynamique passe par textContent ; aucun style en ligne (CSP style-src 'self'). */
(function () {
  'use strict';
  if (window.__axiorhub5634) return;
  window.__axiorhub5634 = true;
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var messages = {};
  try { var raw = document.getElementById('l5631-msg'); if (raw) messages = JSON.parse(raw.textContent || '{}'); } catch (err) { messages = {}; }
  function msg(key, fallback) { return messages['js.5634.' + key] || fallback; }
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function fold(v) { return String(v || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim(); }

  function call(route, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/m5634/' + route, opts).then(function (r) {
      if (r.status === 401) throw new Error(msg('session', 'Votre session a expiré : reconnectez-vous.'));
      return r.json().catch(function () { return { error: 'reponse_invalide', message: msg('invalid', 'Réponse inattendue du serveur.') }; })
        .then(function (d) { if (!r.ok || (d && d.error)) throw new Error((d && (d.message || d.error)) || msg('refused', 'Demande refusée.')); return d; });
    });
  }
  function status(node, text, bad) {
    var box = node && (node.matches && node.matches('.m34-status') ? node : $('.m34-status', node.closest('form, section, article, div') || document));
    if (box) { box.textContent = text; box.classList.toggle('bad', !!bad); }
  }

  // ---------------------------------------------------------------- sélecteur de dossier (recherche en direct, sans accents)
  $$('[data-m34-matter-q]').forEach(function (input) {
    var form = input.closest('form'), list = $('[data-m34-results]', form), timer = null;
    var target = form.getAttribute('data-m34-target') || form.getAttribute('action');
    function extra() { var o = {}; $$('input[type=hidden]', form).forEach(function (h) { if (h.value) o[h.name] = h.value; }); return o; }
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = input.value.trim();
        list.textContent = '';
        if (q.length < 2) return;
        call('dossiers?' + new URLSearchParams({ q: q }).toString()).then(function (d) {
          list.textContent = '';
          d.results.forEach(function (r) {
            var li = document.createElement('li'), a = document.createElement('a'), small = document.createElement('small');
            var params = extra(); params.matter = r.id;
            a.href = target + '?' + new URLSearchParams(params).toString(); a.textContent = r.label;
            small.textContent = r.client ? ' ' + r.client : '';
            li.appendChild(a); li.appendChild(small); list.appendChild(li);
          });
          if (!d.results.length) { var li = document.createElement('li'); li.className = 'm34-muted'; li.textContent = msg('no_matter', 'Aucun dossier ne correspond.'); list.appendChild(li); }
          if (d.total > d.results.length) { var more = document.createElement('li'); more.className = 'm34-muted'; more.textContent = d.total + ' ' + msg('refine', 'dossiers : précisez la recherche.'); list.appendChild(more); }
        }).catch(function (e) { status(form, e.message, true); });
      }, 250);
    });
  });

  // ---------------------------------------------------------------- filtre des documents
  $$('[data-m34-filter]').forEach(function (input) {
    var box = input.closest('[data-m34-docs]');
    input.addEventListener('input', function () {
      var v = fold(input.value);
      $$('li[data-m34-doc]', box).forEach(function (li) { li.hidden = !!v && li.getAttribute('data-m34-doc').indexOf(v) < 0; });
    });
  });

  // ---------------------------------------------------------------- Audiences › Préparer : téléversement d'écritures (U01)
  $$('[data-m34-upload]').forEach(function (input) {
    var box = input.closest('[data-m34-upload-matter]');
    var form = $('form[data-m34-form="audience/preparer"]');
    if (!box || !form) return;
    input.addEventListener('change', function () {
      var file = input.files && input.files[0], side = input.getAttribute('data-m34-upload');
      var out = $('[data-m34-upload-status="' + side + '"]', box);
      if (!file) return;
      if (!/\.(docx|pdf)$/i.test(file.name) || !file.size || file.size > 20000000) { status(out, msg('upload_format', 'Fichier DOCX ou PDF de 20 Mo maximum requis.'), true); return; }
      input.disabled = true; status(out, msg('upload_running', 'Vérification et dépôt dans le dossier en cours…'));
      fetch(prefix + '/hearing/upload', { method: 'POST', credentials: 'same-origin', body: file,
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf, 'X-Hearing-Name': encodeURIComponent(file.name),
                   'X-Hearing-Matter': box.getAttribute('data-m34-upload-matter'), 'X-Hearing-Side': side } })
        .then(function (r) { return r.json().then(function (d) { if (!r.ok || !d.path) throw new Error(d.message || d.error || 'depot_impossible'); return d; }); })
        .then(function (d) {
          var name = side === 'ours' ? 'ours' : 'opponents', fieldset = null;
          $$('[data-m34-docs] input[name="' + name + '"]', form).forEach(function (x) { fieldset = fieldset || x.closest('[data-m34-docs]'); });
          fieldset = fieldset || $$('[data-m34-docs]', form)[side === 'ours' ? 0 : 1];
          var li = document.createElement('li'), label = document.createElement('label'), radio = document.createElement('input'), strong = document.createElement('strong');
          li.setAttribute('data-m34-doc', fold(file.name));
          radio.type = side === 'ours' ? 'radio' : 'checkbox'; radio.name = name; radio.value = 'televerse:' + d.path; radio.checked = true;
          radio.setAttribute('aria-label', file.name);
          strong.textContent = ' ' + file.name + ' ';
          label.appendChild(radio); label.appendChild(strong);
          li.appendChild(label);
          var ul = fieldset && $('ul', fieldset);
          if (ul) { $$('li.m34-muted', ul).forEach(function (x) { x.remove(); }); ul.insertBefore(li, ul.firstChild); }
          status(out, msg('upload_done', 'Dépôt vérifié, document coché : ') + file.name + ' · ' + d.text_characters + ' ' + msg('upload_chars', 'caractères lisibles.'));
        })
        .catch(function (err) { status(out, msg('upload_failed', 'Dépôt impossible : ') + err.message, true); })
        .then(function () { input.disabled = false; input.value = ''; });
    });
  });

  // ---------------------------------------------------------------- formulaires JSON
  function collect(form) {
    var data = {}, route = form.getAttribute('data-m34-form');
    if (form.getAttribute('data-matter')) data.matter = form.getAttribute('data-matter');
    $$('input, select, textarea', form).forEach(function (f) {
      if (!f.name || f.type === 'file' || f.type === 'search') return;
      if (f.type === 'checkbox') {
        if (['reminders', 'providers', 'opponents', 'extra'].indexOf(f.name) >= 0) { data[f.name] = data[f.name] || []; if (f.checked) data[f.name].push(f.name === 'reminders' ? Number(f.value) : f.value); }
        else data[f.name] = f.checked;
        return;
      }
      if (f.type === 'radio') { if (f.checked) { data[f.name] = f.value; if (f.getAttribute('data-version')) data[f.name === 'ours' ? 'ours_version' : 'version'] = f.getAttribute('data-version'); } return; }
      if (f.value !== '') data[f.name] = f.value;
    });
    if (route === 'recherche' && !data.providers) data.providers = [];
    return data;
  }
  $$('form[data-m34-form]').forEach(function (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var route = form.getAttribute('data-m34-form'), button = $('button[type=submit]', form);
      if (button && button.disabled) return;
      if (button) button.disabled = true;
      var data = collect(form);
      var file = $('input[type=file]', form);
      var send = function () {
        status(form, msg('sending', 'Envoi…'));
        return call(route, data).then(function (r) {
          status(form, r.message || msg('saved', 'Enregistré.'));
          if (route === 'agenda/creer' || route === 'agenda/importer' || route.indexOf('modeles/') === 0) setTimeout(function () { location.reload(); }, 900);
        }).catch(function (e) { status(form, e.message, true); }).then(function () { if (button) button.disabled = false; });
      };
      if (file && file.files && file.files[0]) {
        if (file.files[0].size > 8000000) { status(form, msg('too_big', 'Fichier trop volumineux (8 Mo au plus).'), true); if (button) button.disabled = false; return; }
        var reader = new FileReader();
        reader.onload = function () { data.ics = String(reader.result || ''); data.label = file.files[0].name.replace(/\.ics$/i, ''); send(); };
        reader.onerror = function () { status(form, msg('read_error', 'Lecture du fichier impossible.'), true); if (button) button.disabled = false; };
        reader.readAsText(file.files[0]);
      } else send();
    });
  });

  // ---------------------------------------------------------------- commandes simples (boutons)
  $$('[data-m34-post]').forEach(function (b) {
    b.addEventListener('click', function () {
      b.disabled = true;
      call(b.getAttribute('data-m34-post'), { matter: b.getAttribute('data-matter') || '' })
        .then(function (r) { status(b, r.message); }).catch(function (e) { status(b, e.message, true); b.disabled = false; });
    });
  });

  // ---------------------------------------------------------------- Détail et Annuler (page de détail)
  $$('[data-m34-cancel]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.getAttribute('data-confirm') && !window.confirm(b.getAttribute('data-confirm'))) return;
      b.disabled = true;
      call('decision/annuler', { type: b.getAttribute('data-m34-cancel'), id: b.getAttribute('data-id') }).then(function (r) {
        status(b, r.message);
        b.textContent = (r.state === 'annulation_demandee' || r.state === 'cancel_requested') ? msg('stop_requested', 'Arrêt demandé') : msg('cancelled', 'Demande annulée');
      }).catch(function (e) { status(b, e.message, true); b.disabled = false; });
    });
  });
  // retour depuis un détail : le focus revient au bouton d'origine dans Aujourd'hui
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a[data-m34-detail]');
    if (a) { try { sessionStorage.setItem('m34-detail', a.getAttribute('href')); } catch (err) { /* stockage indisponible */ } }
  });
  try {
    var back = sessionStorage.getItem('m34-detail');
    if (back && /\/aujourdhui/.test(location.pathname)) {
      sessionStorage.removeItem('m34-detail');
      var origin = $('a[data-m34-detail][href="' + back.replace(/"/g, '') + '"]');
      if (origin) { var panel = $('#c5614-panel'); if (panel && panel.hidden) { var open = $('#c5614-open'); if (open) open.click(); } origin.focus(); }
    }
  } catch (err) { /* stockage indisponible : aucun retour de focus */ }

  // ---------------------------------------------------------------- Alertes du cabinet
  $$('[data-m34-alert]').forEach(function (b) {
    b.addEventListener('click', function () {
      var card = b.closest('[data-m34-signal]');
      b.disabled = true;
      call('alerte', { signal: card.getAttribute('data-m34-signal'), action: b.getAttribute('data-m34-alert'), hours: 24 }).then(function (r) {
        status($('.m34-status', card), r.message);
        setTimeout(function () { location.reload(); }, 1200);
      }).catch(function (e) { status($('.m34-status', card), e.message, true); b.disabled = false; });
    });
  });

  // ---------------------------------------------------------------- agenda local
  var agenda = $('[data-m34-agenda]');
  if (agenda) {
    $$('[data-m34-agenda-cancel]', agenda).forEach(function (b) {
      b.addEventListener('click', function () {
        if (!window.confirm(b.getAttribute('data-confirm'))) return;
        b.disabled = true;
        call('agenda/annuler', { id: b.closest('[data-m34-event]').getAttribute('data-m34-event') })
          .then(function (r) { b.closest('li').textContent = r.message; }).catch(function (e) { window.alert(e.message); b.disabled = false; });
      });
    });
    var exp = $('[data-m34-agenda-export]', agenda);
    if (exp) exp.addEventListener('click', function () {
      call('agenda/export').then(function (r) {
        var url = URL.createObjectURL(new Blob([r.ics], { type: 'text/calendar' })), a = document.createElement('a');
        a.href = url; a.download = 'agenda-axiorhub.ics'; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      }).catch(function (e) { window.alert(e.message); });
    });
    var bak = $('[data-m34-agenda-backup]', agenda);
    if (bak) bak.addEventListener('click', function () {
      bak.disabled = true;
      call('agenda/sauvegarder', {}).then(function (r) { bak.textContent = r.message; }).catch(function (e) { window.alert(e.message); bak.disabled = false; });
    });
  }

  // ---------------------------------------------------------------- profil du modèle local
  $$('[data-m34-profile]').forEach(function (b) {
    b.addEventListener('click', function () {
      var action = b.getAttribute('data-m34-profile'), box = b.closest('[data-m34-profile-panel]');
      b.disabled = true;
      status($('.m34-status', box), action === 'tester' ? msg('testing', 'Test en cours (aucune pièce transmise)…') : msg('sending', 'Envoi…'));
      call('modeles/' + action, {}).then(function (r) {
        status($('.m34-status', box), r.message + (r.incompatibilites && r.incompatibilites.length ? ' — ' + r.incompatibilites.join(' ') : ''));
        if (action !== 'tester') setTimeout(function () { location.reload(); }, 900);
      }).catch(function (e) { status($('.m34-status', box), e.message, true); }).then(function () { b.disabled = false; });
    });
  });
})();
