/* AxiorHub 5.6.70 — recherche dans les listes de dossiers, « Traiter avec l'IA » plusieurs courriels, documents Nextcloud joints aux
   courriels, lien de visioconférence, période de l'export comptable. Aucun HTML venant du serveur n'est injecté : tout est construit ici. */
(function () {
  'use strict';
  if (window.__axiorhub5670) return;
  window.__axiorhub5670 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 6000);
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
  function fold(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }

  // ---------------------------------------------------------------- 1. recherche dans les listes de dossiers
  var MATTERISH = /matter|dossier|client/i;
  function searchable(sel) {
    if (sel.dataset.c5670 || sel.multiple || sel.options.length < 12) return false;
    if (sel.hasAttribute('data-c5670-search')) return true;
    return MATTERISH.test(sel.name || '') || MATTERISH.test(sel.id || '') || MATTERISH.test(sel.getAttribute('aria-label') || '');
  }
  function enhance(sel) {
    sel.dataset.c5670 = '1';
    var input = el('input', { type: 'search', 'class': 'c5670-find', placeholder: 'Rechercher un dossier…', 'aria-label': 'Rechercher un dossier dans la liste' });
    var go = el('button', { type: 'button', 'class': 'ax-btn ghost c5670-go', text: 'Rechercher', title: 'Choisir le premier dossier correspondant' });
    var count = el('span', { 'class': 'c5670-count ax-muted', 'aria-live': 'polite' });
    var bar = el('span', { 'class': 'c5670-searchbar' }, [input, go, count]);
    sel.parentNode.insertBefore(bar, sel);
    function filter() {
      var q = fold(input.value.trim()), first = null, n = 0;
      Array.prototype.forEach.call(sel.options, function (o) {
        var keep = !q || !o.value || fold(o.textContent).indexOf(q) >= 0;
        o.hidden = !keep;
        if (keep && o.value && !o.value.startsWith('__')) { n += 1; if (!first) first = o; }
      });
      count.textContent = q ? n + ' dossier(s)' : '';
      return first;
    }
    function pick() {
      var first = filter();
      if (!first) { toast('Aucun dossier ne correspond à « ' + input.value.trim() + ' ».', true); return; }
      sel.value = first.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      sel.focus();
    }
    input.addEventListener('input', filter);
    input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); pick(); } });
    go.addEventListener('click', pick);
  }
  function scan(root) { $$('select', root).forEach(function (s) { if (searchable(s)) enhance(s); }); }

  // ---------------------------------------------------------------- 2. « Traiter avec l'IA » plusieurs courriels
  var waits = 0;
  function inboxBatch() {
    var head = $('#bx5625 .bx5625-head'), rows = $('#bx5625-rows');
    if (!head || !rows || head.querySelector('.c5670-traiter')) return;
    if (!head.querySelector('.bx5628-new') && waits < 20) { waits += 1; setTimeout(inboxBatch, 150); return; }   // à côté de « Nouveau message »
    var btn = el('button', { type: 'button', 'class': 'ax-btn c5670-traiter', text: '✨ Traiter avec l’IA', disabled: 'disabled',
                             title: 'Préparer une réponse pour chaque courriel coché (dossier connu, sinon réponse sans dossier) ; rien n’est envoyé' });
    var all = el('button', { type: 'button', 'class': 'ax-btn ghost c5670-all', text: 'Tout cocher' });
    var newBtn = head.querySelector('.bx5628-new');
    head.insertBefore(all, newBtn ? newBtn.nextSibling : head.firstChild);
    head.insertBefore(btn, all);
    function picked() { return $$('.c5670-pick input:checked', rows); }
    function refresh() {
      var n = picked().length;
      btn.disabled = !n; btn.textContent = n ? '✨ Traiter avec l’IA (' + n + ')' : '✨ Traiter avec l’IA';
    }
    function decorate() {
      $$('.ax-row[data-uid]', rows).forEach(function (row) {
        if (row.previousElementSibling && row.previousElementSibling.classList.contains('c5670-pick')) return;
        var cb = el('input', { type: 'checkbox', 'data-uid': row.getAttribute('data-uid'), 'aria-label': 'Choisir ce courriel' });
        cb.addEventListener('change', refresh);
        var wrap = el('label', { 'class': 'c5670-pick', title: 'Choisir ce courriel' }, [cb]);
        row.parentNode.insertBefore(wrap, row);
      });
      refresh();
    }
    new MutationObserver(decorate).observe(rows, { childList: true });
    decorate();
    all.addEventListener('click', function () {
      var boxes = $$('.c5670-pick input', rows), on = boxes.some(function (b) { return !b.checked; });
      boxes.forEach(function (b) { b.checked = on; }); all.textContent = on ? 'Tout décocher' : 'Tout cocher'; refresh();
    });
    btn.addEventListener('click', function () {
      var ctx = window.axh5625Ctx ? window.axh5625Ctx() : {};
      var items = picked().map(function (b) { return { uid: b.getAttribute('data-uid'), dossier: ctx.folder || '' }; });
      if (!items.length) return;
      if (items.length > 25) { toast('25 courriels au plus par lot.', true); return; }
      busy(btn, true);
      api('m5670/courriels/traiter', { items: items }).then(function (r) {
        toast(r.message); picked().forEach(function (b) { b.checked = false; }); refresh();
      }).catch(function (e) { toast(e.message, true); }).then(function () { busy(btn, false); refresh(); });
    });
  }
  function matterBatch() {
    $$('[data-c5670-batch]').forEach(function (bar) {
      if (bar.dataset.ready) return; bar.dataset.ready = '1';
      var card = bar.parentNode, btn = $('[data-c5670-traiter]', bar), all = $('[data-c5670-all]', bar);
      var boxes = function () { return $$('[data-c5670-key]', card); };
      var refresh = function () {
        var n = boxes().filter(function (b) { return b.checked; }).length;
        btn.disabled = !n; btn.textContent = n ? '✨ Traiter avec l’IA (' + n + ')' : '✨ Traiter avec l’IA';
      };
      boxes().forEach(function (b) { b.addEventListener('change', refresh); });
      if (all) all.addEventListener('change', function () { boxes().forEach(function (b) { b.checked = all.checked; }); refresh(); });
      btn.addEventListener('click', function () {
        var items = boxes().filter(function (b) { return b.checked; }).map(function (b) { return { key: b.getAttribute('data-c5670-key') }; });
        if (items.length > 25) { toast('25 courriels au plus par lot.', true); return; }
        busy(btn, true);
        api('m5670/courriels/traiter', { items: items }).then(function (r) { toast(r.message); boxes().forEach(function (b) { b.checked = false; }); })
          .catch(function (e) { toast(e.message, true); }).then(function () { busy(btn, false); refresh(); });
      });
    });
  }

  // ---------------------------------------------------------------- 3. documents Nextcloud joints à un courriel
  function pickNextcloud(onPick) {
    var dlg = el('div', { 'class': 'c5670-picker', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Joindre des documents Nextcloud' });
    var where = el('p', { 'class': 'c5670-where ax-muted' });
    var q = el('input', { type: 'search', placeholder: 'Rechercher un document par son nom…', 'aria-label': 'Rechercher un document' });
    var find = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Rechercher' });
    var list = el('ul', { 'class': 'c5670-files' });
    var close = el('button', { type: 'button', 'class': 'ax-btn', text: 'Terminé' });
    dlg.appendChild(el('div', { 'class': 'c5670-picker-box' }, [el('h3', { text: 'Joindre depuis Nextcloud' }), el('div', { 'class': 'c5670-row' }, [q, find]), where, list,
      el('p', { 'class': 'ax-muted', text: 'Cliquez sur un document pour le joindre (10 fichiers et 15 Mo au plus). Il est relu au moment de l’enregistrement.' }), close]));
    document.body.appendChild(dlg);
    function show(entries, path, parent) {
      list.textContent = '';
      where.textContent = path ? 'Dossier : ' + path : 'Espaces du cabinet';
      if (parent) {
        var up = el('button', { type: 'button', 'class': 'c5670-entry', text: '⬑ Dossier parent' });
        up.addEventListener('click', function () { open(parent); }); list.appendChild(el('li', {}, [up]));
      }
      if (!entries.length) list.appendChild(el('li', { 'class': 'ax-muted', text: 'Aucun document.' }));
      entries.forEach(function (x) {
        var b = el('button', { type: 'button', 'class': 'c5670-entry', text: (x.dossier ? '📁 ' : '📄 ') + x.nom + (x.taille ? ' · ' + Math.max(1, Math.round(x.taille / 1024)) + ' Ko' : '') });
        b.addEventListener('click', function () {
          if (x.dossier) { open(x.chemin); return; }
          onPick({ chemin: x.chemin, nom: x.nom }); toast('Joint : ' + x.nom);
        });
        list.appendChild(el('li', {}, [b]));
      });
    }
    function open(path) {
      list.textContent = ''; list.appendChild(el('li', { 'class': 'ax-muted', text: 'Lecture…' }));
      api('m5670/fichiers/parcourir?chemin=' + encodeURIComponent(path || '')).then(function (d) { show(d.entrees || [], d.chemin, d.parent); })
        .catch(function (e) { list.textContent = ''; list.appendChild(el('li', { 'class': 'ax-alert', text: e.message })); });
    }
    function search() {
      if (q.value.trim().length < 2) { open(''); return; }
      api('m5670/fichiers/chercher?q=' + encodeURIComponent(q.value.trim())).then(function (d) { show(d.resultats || [], '', ''); where.textContent = 'Résultats de la recherche'; })
        .catch(function (e) { toast(e.message, true); });
    }
    find.addEventListener('click', search);
    q.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); search(); } });
    close.addEventListener('click', function () { dlg.remove(); });
    dlg.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') dlg.remove(); });
    open('');
    q.focus();
  }
  function visioLink() { return api('m5670/visio/lien', {}).then(function (r) { return r.url; }); }

  // ---------------------------------------------------------------- 4. export comptable : période choisie
  function exportPeriod() {
    $$('[data-ef70-export]').forEach(function (box) {
      var btn = $('.m5-export', box);
      var update = function () {
        var du = $('[name=du]', box).value, au = $('[name=au]', box).value, pdf = $('[name=pdf]', box).checked ? '1' : '';
        btn.setAttribute('data-api', 'm5670/efacture/export?du=' + encodeURIComponent(du) + '&au=' + encodeURIComponent(au) + '&pdf=' + pdf);
      };
      $$('input', box).forEach(function (i) { i.addEventListener('change', update); });
      update();
    });
  }

  window.axh5670 = { pickNextcloud: pickNextcloud, visioLink: visioLink, toast: toast };
  function boot() {
    scan(document); inboxBatch(); matterBatch(); exportPeriod();
    var pending = false;
    new MutationObserver(function () {
      if (pending) return; pending = true;
      setTimeout(function () { pending = false; scan(document); inboxBatch(); }, 150);
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
