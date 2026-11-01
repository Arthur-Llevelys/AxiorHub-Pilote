/* 5.6.88 : mentions @personne avec autocomplétion, note commune de dossier (conflit visible), réglage de la page Aujourd'hui au clavier et à la souris. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = function () { return meta('axiorhub-prefix') || ''; };
  var el = function (tag, text, cls) { var n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  var get = function (path) { return fetch(prefix() + '/api440/' + path, { credentials: 'same-origin' }).then(function (r) { return r.json(); }); };
  var post = function (path, body) {
    return fetch(prefix() + '/api440/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); });
  };

  /* Autocomplétion : tout champ portant data-mention, y compris créé après le chargement. Flèches, Entrée ou Tab pour choisir, Échap pour fermer. */
  var box = null, field = null, items = [], active = -1, seq = 0;
  function closeBox() { if (box) box.hidden = true; items = []; active = -1; if (field) { field.removeAttribute('aria-activedescendant'); field.setAttribute('aria-expanded', 'false'); } }
  function ensureBox() {
    if (box) return box;
    box = el('ul', undefined, 'n88-mentions'); box.id = 'n88-mentions'; box.setAttribute('role', 'listbox'); box.setAttribute('aria-label', 'Personnes à mentionner'); box.hidden = true;
    document.body.append(box);
    box.addEventListener('mousedown', function (ev) { var li = ev.target.closest('li'); if (li) { ev.preventDefault(); choose(Number(li.getAttribute('data-i'))); } });
    return box;
  }
  function tokenAt(input) {
    var before = input.value.slice(0, input.selectionStart), m = /(?:^|[\s(])@([\w.+\-]{0,40})$/.exec(before);
    return m ? { text: m[1], start: input.selectionStart - m[1].length - 1 } : null;
  }
  function paint() {
    box.replaceChildren();
    items.forEach(function (p, i) {
      var li = el('li', p.nom); li.id = 'n88-m' + i; li.setAttribute('role', 'option'); li.setAttribute('data-i', i); li.setAttribute('aria-selected', String(i === active));
      li.append(el('small', ' @' + p.jeton)); box.append(li);
    });
    if (field) { if (active >= 0) field.setAttribute('aria-activedescendant', 'n88-m' + active); else field.removeAttribute('aria-activedescendant'); }
  }
  function choose(i) {
    var p = items[i], tok = field && tokenAt(field);
    if (!p || !tok) return closeBox();
    var end = field.selectionStart, text = '@' + p.jeton + ' ';
    field.value = field.value.slice(0, tok.start) + text + field.value.slice(end);
    field.selectionStart = field.selectionEnd = tok.start + text.length;
    field.dispatchEvent(new Event('input', { bubbles: true })); closeBox(); field.focus();
  }
  function matterOf(input) { var host = input.closest('[data-matter]'); return input.getAttribute('data-matter') || (host ? host.getAttribute('data-matter') : ''); }
  document.addEventListener('input', function (ev) {
    var input = ev.target;
    if (!input.matches || !input.matches('[data-mention]')) return;
    var tok = tokenAt(input);
    if (!tok) { if (field === input) closeBox(); return; }
    field = input; input.setAttribute('role', 'combobox'); input.setAttribute('aria-controls', 'n88-mentions'); input.setAttribute('aria-autocomplete', 'list');
    var mine = ++seq;
    get('m5688/mentions?q=' + encodeURIComponent(tok.text) + '&matter=' + encodeURIComponent(matterOf(input))).then(function (d) {
      if (mine !== seq || field !== input) return;
      items = d.personnes || []; active = items.length ? 0 : -1; ensureBox(); paint(); box.hidden = !items.length; input.setAttribute('aria-expanded', String(!!items.length));
      if (items.length) { var r = input.getBoundingClientRect(); box.style.left = (window.scrollX + r.left) + 'px'; box.style.top = (window.scrollY + r.bottom + 2) + 'px'; }
    }).catch(closeBox);
  });
  document.addEventListener('keydown', function (ev) {
    if (!box || box.hidden || ev.target !== field) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); active = (active + 1) % items.length; paint(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); active = (active + items.length - 1) % items.length; paint(); }
    else if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); ev.stopPropagation(); choose(active); }
    else if (ev.key === 'Escape') { ev.stopPropagation(); closeBox(); }
  }, true);
  document.addEventListener('focusout', function (ev) { if (ev.target === field) setTimeout(closeBox, 120); });

  /* Note commune : l'écriture part de la version lue ; un conflit garde les deux textes à l'écran. */
  function setupNote() {
    var card = document.getElementById('n88-note');
    if (!card) return;
    var text = document.getElementById('n88-text'), status = document.getElementById('n88-status'), conflict = document.getElementById('n88-conflit'), other = document.getElementById('n88-autre');
    var dirty = false, serverText = '';
    text.addEventListener('input', function () { dirty = true; });
    window.addEventListener('beforeunload', function (ev) { if (dirty) { ev.preventDefault(); ev.returnValue = ''; } });
    document.getElementById('n88-save').addEventListener('click', function (ev) {
      var btn = ev.currentTarget; btn.disabled = true; status.textContent = 'Enregistrement…';
      post('m5688/note/enregistrer', { matter: card.getAttribute('data-matter'), base: Number(card.getAttribute('data-version')), texte: text.value }).then(function (res) {
        btn.disabled = false;
        if (res.status === 409 && res.data.actuelle) {
          var a = res.data.actuelle; card.setAttribute('data-version', a.version); serverText = a.texte; other.textContent = a.texte; conflict.hidden = false;
          status.textContent = 'Non enregistré : ' + a.auteur_nom + ' a écrit la version ' + a.version + ' pendant ce temps.'; return;
        }
        if (res.data.error) { status.textContent = res.data.message || 'Enregistrement impossible.'; return; }
        card.setAttribute('data-version', res.data.version); conflict.hidden = true; dirty = false; status.textContent = res.data.message;
      }).catch(function () { btn.disabled = false; status.textContent = 'Enregistrement impossible : votre texte reste à l’écran.'; });
    });
    document.getElementById('n88-reprendre').addEventListener('click', function () { text.value = serverText; conflict.hidden = true; dirty = false; status.textContent = 'Texte en place repris.'; text.focus(); });
    card.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-n88-version]');
      if (!b) return;
      get('m5688/note/version?matter=' + encodeURIComponent(card.getAttribute('data-matter')) + '&version=' + encodeURIComponent(b.getAttribute('data-n88-version'))).then(function (d) {
        if (typeof d.texte !== 'string') return;
        text.value = d.texte; dirty = true; status.textContent = 'Version ' + d.version + ' reprise : enregistrez pour en faire la version courante.'; text.focus();
      });
    });
  }

  /* Page Aujourd'hui : Monter, Descendre, glisser-déposer. L'ordre part dans le champ caché du formulaire. */
  function setupBoard() {
    var list = document.getElementById('t88-list'), order = document.getElementById('t88-ordre'), status = document.getElementById('t88-status');
    if (!list || !order) return;
    var dragged = null;
    var sync = function () { order.value = Array.prototype.map.call(list.children, function (li) { return li.getAttribute('data-bloc'); }).join(','); };
    list.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-t88-move]');
      if (!b) return;
      var li = b.closest('li'), up = b.getAttribute('data-t88-move') === '-1', to = up ? li.previousElementSibling : li.nextElementSibling;
      if (!to) return;
      list.insertBefore(li, up ? to : to.nextSibling); sync(); b.focus();
      status.textContent = li.textContent.replace(/Monter.*/, '').trim() + ' : position ' + (Array.prototype.indexOf.call(list.children, li) + 1);
    });
    list.addEventListener('dragstart', function (ev) { dragged = ev.target.closest('li'); if (dragged) { ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', dragged.getAttribute('data-bloc')); } });
    list.addEventListener('dragover', function (ev) {
      var over = ev.target.closest('li');
      if (!dragged || !over || over === dragged) return;
      ev.preventDefault();
      var r = over.getBoundingClientRect(); list.insertBefore(dragged, ev.clientY > r.top + r.height / 2 ? over.nextSibling : over);
    });
    list.addEventListener('dragend', function () { dragged = null; sync(); });
  }
  document.addEventListener('DOMContentLoaded', function () { setupNote(); setupBoard(); });
})();
