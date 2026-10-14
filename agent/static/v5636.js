/* 5.6.36 : barre d'activité repliable (repliée par défaut), fenêtre du Pilote centrée, demandes écartables, missions annulables,
   points à vérifier, nouvel essai automatique d'une lecture quand la base est occupée. Aucune donnée n'est envoyée ailleurs. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
  var prefix = meta('axiorhub-prefix') || '/agent-courriel', csrf = meta('axiorhub-csrf');
  var store = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* stockage indisponible : réglage non mémorisé */ } } };
  var T = function (key, fr) { return window.ax5634Translate ? window.ax5634Translate(fr) : fr; };

  function post(route, body) {
    return fetch(prefix + '/api440/' + route, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { throw new Error(j.message || j.error || ('HTTP ' + r.status)); } return j; }); });
  }

  // --- 1. lecture réessayée quand la base est momentanément occupée (GET seulement : une action n'est jamais rejouée)
  if (window.fetch && !window.fetch.__ax5636) {
    var nativeFetch = window.fetch.bind(window);
    var wrapped = function (input, init) {
      var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      var attempt = function (n) {
        return nativeFetch(input, init).then(function (resp) {
          if (resp.status !== 503 || method !== 'GET' || n >= 3) { return resp; }
          return resp.clone().json().then(function (j) {
            if (!j || j.error !== 'base_occupee') { return resp; }
            return new Promise(function (ok) { setTimeout(ok, 1500 * (n + 1)); }).then(function () { return attempt(n + 1); });
          }, function () { return resp; });
        });
      };
      return attempt(0);
    };
    wrapped.__ax5636 = true;
    window.fetch = wrapped;
  }

  // --- 2. barre d'activité et suivi des engagements : repliables, repliés par défaut
  function setupBar() {
    var bar = document.getElementById('ws-live-bar');
    if (!bar || bar.querySelector('.s5636-fold')) { return; }
    var fold = document.createElement('button');
    fold.type = 'button';
    fold.className = 's5636-fold';
    var apply = function (open) {
      document.documentElement.classList.toggle('s5636-bar-open', open);
      document.documentElement.classList.toggle('s5636-bar-closed', !open);
      fold.setAttribute('aria-expanded', open ? 'true' : 'false');
      fold.title = open ? T('', 'Replier la barre d’activité') : T('', 'Afficher la barre d’activité');
      fold.textContent = open ? '▾' : '●';
      fold.setAttribute('aria-label', fold.title);
    };
    fold.addEventListener('click', function () {
      var open = !document.documentElement.classList.contains('s5636-bar-open');
      store.set('ax5636-bar', open ? 'open' : 'closed');
      apply(open);
    });
    bar.insertBefore(fold, bar.firstChild);
    apply(store.get('ax5636-bar') === 'open');
  }

  // --- 3. fenêtre du Pilote centrée avec fond assombri ; Échap ou clic sur le fond la referme
  function setupDock() {
    var dock = document.getElementById('ws-ai-dock');
    if (!dock || dock.classList.contains('ws-ai-dock--embedded') || document.querySelector('.s5636-backdrop')) { return; }
    var shade = document.createElement('div');
    shade.className = 's5636-backdrop';
    shade.hidden = true;
    shade.setAttribute('aria-hidden', 'true');
    document.body.appendChild(shade);
    var close = function () { var b = document.getElementById('ws-ai-close'); if (b) { b.click(); } };
    shade.addEventListener('click', close);
    var sync = function () {
      var open = !dock.hidden && !dock.classList.contains('ws-ai-dock--embedded');
      shade.hidden = !open;
      document.documentElement.classList.toggle('s5636-dock-open', open);
    };
    new MutationObserver(sync).observe(dock, { attributes: true, attributeFilter: ['hidden', 'class'] });
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !dock.hidden) { close(); } });
    sync();
  }

  // --- 4. boutons « Écarter » (activité), missions, points à vérifier
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-s5636-dismiss]');
    if (b) {
      ev.preventDefault();
      b.disabled = true;
      post('m5636/travail/ecarter', { job: b.getAttribute('data-s5636-dismiss') }).then(function (r) {
        var art = b.closest('.live-job'); if (art) { art.remove(); }
        document.body.dispatchEvent(new Event('live-refresh'));
      }).catch(function (e) { b.disabled = false; b.title = e.message; });
      return;
    }
    var pj = ev.target.closest('[data-s5636-pj]');
    if (pj) {
      ev.preventDefault();
      pj.disabled = true;
      var out = pj.parentElement.querySelector('.s5636-pj-out');
      var sel = pj.getAttribute('data-matter-select') ? document.querySelector(pj.getAttribute('data-matter-select')) : null;
      var chosen = sel && sel.value && sel.value.indexOf('__') !== 0 ? sel.value : (pj.getAttribute('data-matter') || '');
      post('m5636/pieces/ranger-pj', { key: pj.getAttribute('data-s5636-pj'), matter: chosen, uid: pj.getAttribute('data-uid') || '',
                                       folder: pj.getAttribute('data-folder') || '' }).then(function (r) {
        if (out) { out.textContent = r.message || ''; }
      }).catch(function (e) { pj.disabled = false; if (out) { out.textContent = e.message; } });
      return;
    }
    var ms = ev.target.closest('[data-ms5636]');
    if (ms) {   // agenda Microsoft 365 : connexion (page Microsoft), actualisation, déconnexion
      ev.preventDefault();
      ms.disabled = true;
      post('m5636/microsoft/' + ms.getAttribute('data-ms5636'), {}).then(function (r) {
        if (r.url) { location.href = r.url; return; }
        window.alert(r.message || 'OK'); location.reload();
      }).catch(function (e) { ms.disabled = false; window.alert(e.message); });
      return;
    }
    var plan = ev.target.closest('[data-s5636-plan]');
    if (plan) {
      ev.preventDefault();
      plan.disabled = true;
      var out2 = plan.parentElement.querySelector('.s5636-pj-out');
      post('m5636/initiatives/proposer', { key: plan.getAttribute('data-s5636-plan'), uid: plan.getAttribute('data-uid') || '',
                                           folder: plan.getAttribute('data-folder') || '' }).then(function (r) {
        if (out2) {
          out2.textContent = (r.message || '') + ' ';
          var a = document.createElement('a'); a.href = prefix + '/initiatives' + (r.id ? '#i-' + r.id : ''); a.textContent = T('', 'Ouvrir les initiatives');
          out2.appendChild(a);
        }
      }).catch(function (e) { plan.disabled = false; if (out2) { out2.textContent = e.message; } });
      return;
    }
    var m = ev.target.closest('[data-mission5636]');
    if (m) {
      ev.preventDefault();
      var card = m.closest('[data-mission-id]'), what = m.getAttribute('data-mission5636');
      if (!card) { return; }
      if (m.getAttribute('data-confirm') && !window.confirm(m.getAttribute('data-confirm'))) { return; }
      m.disabled = true;
      post('m5636/mission/' + what, { id: card.getAttribute('data-mission-id') }).then(function () {
        if (what === 'retirer') { card.remove(); } else { location.reload(); }
      }).catch(function (e) { m.disabled = false; window.alert(e.message); });
      return;
    }
    var rep = ev.target.closest('[data-s5636-report]');
    if (rep) {
      ev.preventDefault();
      var area = rep.parentElement.querySelector('.s5636-report');
      rep.disabled = true;
      fetch(prefix + '/api440/m5636/signalement', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
        area.value = j.text || j.message || '';
        area.hidden = false; area.focus(); area.select();
        try { document.execCommand('copy'); rep.textContent = T('', 'Texte copié : collez-le dans votre ticket'); } catch (e) { /* copie manuelle */ }
      }).catch(function (e) { area.hidden = false; area.value = e.message; }).then(function () { rep.disabled = false; });
      return;
    }
    var o = ev.target.closest('[data-s5636-open]');
    if (o) {
      var d = document.getElementById('s5636-checks');
      if (d) { ev.preventDefault(); d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }
  });

  // --- 5. Paramètres › Messagerie › Envoi depuis AxiorHub
  document.addEventListener('change', function (ev) {
    var box = ev.target.closest('[data-envoi5636], [data-envoi5636-matter]');
    if (!box) { return; }
    var status = document.querySelector('[data-envoi5636-status]');
    var say = function (text, bad) { if (status) { status.textContent = text; status.className = 'e5636-status' + (bad ? ' bad' : ''); } };
    var undo = function () { if (box.type === 'checkbox') { box.checked = !box.checked; } };
    var what = box.getAttribute('data-envoi5636'), call;
    if (what === 'enabled') {
      if (box.checked && !window.confirm(T('', 'Autoriser l’envoi des messages quand vous cliquez sur « Envoyer » ? L’agent n’envoie jamais rien de lui-même.'))) { undo(); return; }
      call = post('send/settings', box.checked ? { enabled: true, confirm: 'yes' } : { enabled: false });
    } else if (what === 'scope') {
      var all = box.value === 'all';
      if (all && !window.confirm(T('', 'Autoriser l’envoi pour tous les dossiers ?'))) { location.reload(); return; }
      call = post('send/options', all ? { all_matters: true, confirm: 'yes' } : { all_matters: false });
    } else if (what === 'confirm_each') {
      if (!box.checked && !window.confirm(T('', 'Envoyer sans seconde confirmation ? Le message partira dès votre clic sur « Envoyer », après les contrôles d’AxiorHub.'))) { undo(); return; }
      call = post('send/options', { confirm_each: box.checked });
    } else {
      call = post('send/matter', { matter: box.getAttribute('data-envoi5636-matter'), enabled: box.checked, confirm: 'yes' });
    }
    box.disabled = true;
    call.then(function () { say(T('', 'Réglage enregistré.')); if (what === 'enabled' || what === 'scope') { setTimeout(function () { location.reload(); }, 500); } })
      .catch(function (e) { undo(); say(e.message, true); }).then(function () { box.disabled = false; });
  });

  // --- 6. dossiers Nextcloud choisis en cliquant (réglages : racines, dossiers d'arrivée…)
  function fieldValues(key) {
    var area = document.querySelector('textarea[data-config567="' + key + '"]');
    return { area: area, list: area ? area.value.split('\n').map(function (x) { return x.trim(); }).filter(Boolean) : [] };
  }
  function renderChosen(key) {
    var box = document.querySelector('[data-nc5636-field="' + key + '"] .nc5636-chosen'), v = fieldValues(key).list;
    if (!box) { return; }
    box.textContent = '';
    if (!v.length) { var none = document.createElement('li'); none.className = 'ax-muted nc5636-none'; none.textContent = T('', 'Aucun dossier choisi.'); box.appendChild(none); }
    v.forEach(function (path) {
      var li = document.createElement('li'), s = document.createElement('span'), x = document.createElement('button');
      s.textContent = '📁 ' + path; x.type = 'button'; x.className = 'ghost'; x.textContent = '×';
      x.setAttribute('data-nc5636-remove', path); x.setAttribute('aria-label', T('', 'Retirer') + ' ' + path);
      li.appendChild(s); li.appendChild(x); box.appendChild(li);
    });
  }
  function setValues(key, list) {
    var f = fieldValues(key);
    if (!f.area) { return; }
    f.area.value = list.join('\n');
    f.area.dispatchEvent(new Event('input', { bubbles: true }));
    renderChosen(key);
  }
  function openPicker(key) {
    var dlg = document.createElement('dialog'), where = document.createElement('p'), list = document.createElement('ul'), bar = document.createElement('div');
    dlg.className = 'nc5636-dialog'; where.className = 'nc5636-where'; list.className = 'nc5636-dirs'; bar.className = 'nc5636-bar';
    var up = document.createElement('button'), pick = document.createElement('button'), cancel = document.createElement('button');
    up.type = pick.type = cancel.type = 'button';
    up.className = cancel.className = 'ghost';
    up.textContent = T('', '↑ Dossier parent'); pick.textContent = T('', 'Choisir ce dossier'); cancel.textContent = T('', 'Annuler');
    var h = document.createElement('h2'); h.textContent = T('', 'Choisir un dossier Nextcloud');
    bar.appendChild(up); bar.appendChild(pick); bar.appendChild(cancel);
    dlg.appendChild(h); dlg.appendChild(where); dlg.appendChild(list); dlg.appendChild(bar);
    document.body.appendChild(dlg);
    var current = '/', parent = '';
    var close = function () { try { dlg.close(); } catch (e) { /* déjà fermé */ } dlg.remove(); };
    var load = function (path) {
      where.textContent = T('', 'Lecture…'); list.textContent = '';
      fetch(prefix + '/api440/m5636/nextcloud/dossiers?path=' + encodeURIComponent(path), { credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw new Error(j.message || j.error); } return j; }); })
        .then(function (r) {
          current = r.path; parent = r.parent; up.disabled = !parent; where.textContent = '📁 ' + r.path;
          if (!r.dirs.length) { var li = document.createElement('li'); li.className = 'ax-muted'; li.textContent = T('', 'Aucun sous-dossier.'); list.appendChild(li); }
          r.dirs.forEach(function (d) {
            var li = document.createElement('li'), b = document.createElement('button');
            b.type = 'button'; b.className = 'nc5636-dir'; b.textContent = '📁 ' + d.name;
            b.addEventListener('click', function () { load(d.path); });
            li.appendChild(b); list.appendChild(li);
          });
        }).catch(function (e) { where.textContent = e.message; });
    };
    up.addEventListener('click', function () { if (parent) { load(parent); } });
    cancel.addEventListener('click', close);
    dlg.addEventListener('cancel', function () { setTimeout(close, 0); });
    pick.addEventListener('click', function () {
      var v = fieldValues(key).list;
      if (v.indexOf(current) < 0) { v.push(current); }
      setValues(key, v); close();
    });
    if (dlg.showModal) { dlg.showModal(); } else { dlg.setAttribute('open', 'open'); }
    load('/');
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-nc5636-browse]');
    if (b) { ev.preventDefault(); openPicker(b.getAttribute('data-nc5636-browse')); return; }
    var x = ev.target.closest('[data-nc5636-remove]');
    if (x) {
      ev.preventDefault();
      var key = x.closest('[data-nc5636-field]').getAttribute('data-nc5636-field'), path = x.getAttribute('data-nc5636-remove');
      setValues(key, fieldValues(key).list.filter(function (p) { return p !== path; }));
    }
  });
  document.addEventListener('input', function (ev) {
    var area = ev.target.closest && ev.target.closest('.nc5636-raw textarea');
    if (area && ev.isTrusted) { renderChosen(area.getAttribute('data-config567')); }
  });

  // --- 6. outils d'un courriel dans la boîte de réception : ranger les pièces jointes, proposer un plan d'actions
  window.axh5636 = window.axh5636 || {};
  window.axh5636.mailTools = function (m) {
    var box = document.createElement('p'); box.className = 's5636-pj';
    var out = document.createElement('span'); out.className = 's5636-pj-out'; out.setAttribute('role', 'status');
    if (m.attachments && m.attachments.length) {
      var pj = document.createElement('button'); pj.type = 'button'; pj.className = 'ax-btn ghost';
      pj.textContent = T('', '📎 Ranger les pièces jointes dans le dossier');
      pj.setAttribute('data-s5636-pj', m.key || ''); pj.setAttribute('data-uid', m.uid || ''); pj.setAttribute('data-folder', m.folder || '');
      pj.setAttribute('data-matter-select', '#bx5625-matter');
      box.appendChild(pj); box.appendChild(document.createTextNode(' '));
    }
    var plan = document.createElement('button'); plan.type = 'button'; plan.className = 'ax-btn ghost';
    plan.textContent = T('', '🧭 Proposer un plan d’actions');
    plan.setAttribute('data-s5636-plan', m.key || ''); plan.setAttribute('data-uid', m.uid || ''); plan.setAttribute('data-folder', m.folder || '');
    box.appendChild(plan); box.appendChild(document.createTextNode(' ')); box.appendChild(out);
    return box;
  };

  // --- 7. Corporate : modèle Word choisi dans Nextcloud ou importé depuis le poste
  function openFilePicker(input, msg) {
    var dlg = document.createElement('dialog'), where = document.createElement('p'), list = document.createElement('ul'), bar = document.createElement('div');
    dlg.className = 'nc5636-dialog'; where.className = 'nc5636-where'; list.className = 'nc5636-dirs'; bar.className = 'nc5636-bar';
    var up = document.createElement('button'), cancel = document.createElement('button');
    up.type = cancel.type = 'button'; up.className = cancel.className = 'ghost';
    up.textContent = T('', '↑ Dossier parent'); cancel.textContent = T('', 'Annuler');
    var h = document.createElement('h2'); h.textContent = T('', 'Choisir un modèle Word dans Nextcloud');
    bar.appendChild(up); bar.appendChild(cancel);
    dlg.appendChild(h); dlg.appendChild(where); dlg.appendChild(list); dlg.appendChild(bar);
    document.body.appendChild(dlg);
    var parent = '';
    var close = function () { try { dlg.close(); } catch (e) { /* déjà fermé */ } dlg.remove(); };
    var item = function (label, cls, onClick) {
      var li = document.createElement('li'), b = document.createElement('button');
      b.type = 'button'; b.className = 'nc5636-dir' + (cls ? ' ' + cls : ''); b.textContent = label;
      b.addEventListener('click', onClick); li.appendChild(b); list.appendChild(li);
    };
    var load = function (path) {
      where.textContent = T('', 'Lecture…'); list.textContent = '';
      fetch(prefix + '/api440/m5636/nextcloud/dossiers?fichiers=docx&path=' + encodeURIComponent(path), { credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw new Error(j.message || j.error); } return j; }); })
        .then(function (r) {
          parent = r.parent; up.disabled = !parent; where.textContent = '📁 ' + r.path;
          r.dirs.forEach(function (d) { item('📁 ' + d.name, '', function () { load(d.path); }); });
          (r.files || []).forEach(function (f) {
            item('📄 ' + f.name, 'c5636-file', function () { input.value = f.path; if (msg) { msg.textContent = T('', 'Modèle choisi : cliquez « Enregistrer ».'); } close(); });
          });
          if (!r.dirs.length && !(r.files || []).length) {
            var li = document.createElement('li'); li.className = 'ax-muted'; li.textContent = T('', 'Aucun dossier ni modèle Word ici.'); list.appendChild(li);
          }
        }).catch(function (e) { where.textContent = e.message; });
    };
    up.addEventListener('click', function () { if (parent) { load(parent); } });
    cancel.addEventListener('click', close);
    dlg.addEventListener('cancel', function () { setTimeout(close, 0); });
    if (dlg.showModal) { dlg.showModal(); } else { dlg.setAttribute('open', 'open'); }
    var start = input.value && input.value.charAt(0) === '/' ? input.value.replace(/\/[^\/]*$/, '') || '/' : '/';
    load(start);
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-c5636-pick]');
    if (!b) { return; }
    ev.preventDefault();
    var box = b.closest('.c5636-tplpick');
    openFilePicker(box.querySelector('input[name=path]'), box.querySelector('.c5636-tplmsg'));
  });
  document.addEventListener('change', function (ev) {
    var inp = ev.target;
    if (!inp.matches || !inp.matches('[data-c5636-upload]') || !inp.files || !inp.files[0]) { return; }
    var box = inp.closest('.c5636-tplpick'), msg = box.querySelector('.c5636-tplmsg'), f = inp.files[0];
    if (!/\.docx$/i.test(f.name)) { msg.textContent = T('', 'Choisissez un fichier Word au format .docx.'); return; }
    if (f.size > 8000000) { msg.textContent = T('', 'Ce fichier est vide ou dépasse 8 Mo.'); return; }
    var rd = new FileReader();
    rd.onload = function () {
      msg.textContent = T('', 'Import…');
      post('m5636/corporate/import', { filename: f.name, data: String(rd.result || '') }).then(function (r) {
        box.querySelector('input[name=path]').value = r.source;
        msg.textContent = r.message || '';
      }).catch(function (e) { msg.textContent = e.message; });
    };
    rd.readAsDataURL(f);
    inp.value = '';
  });

  function boot() { setupBar(); setupDock(); }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }
  // la barre et le Pilote peuvent être insérés après le chargement (pages enrichies)
  setTimeout(boot, 800);
})();

/* 5.6.43 (§ 22) : « Recharger » Talk — confirmation (un appel peut être interrompu), jamais de rechargement automatique. */
(function () {
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-talk5643-reload]');
    if (!b) { return; }
    if (!window.confirm(b.getAttribute('data-confirm') || 'Recharger ?')) { return; }
    var f = document.querySelector('.talk5643-frame');
    if (f) { f.src = f.src; }
  });
})();
