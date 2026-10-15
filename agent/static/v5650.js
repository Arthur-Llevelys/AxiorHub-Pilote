/* AxiorHub 5.6.50 — messagerie interne : pastille « non lus » du menu, liste des conversations, messages.
   Aucun HTML venant du serveur n'est injecté : tout passe par textContent. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix') || '/agent-courriel', csrf = meta('axiorhub-csrf');
  function answer(r) {
    return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
      if (d && d.error) { var x = new Error(d.message || d.error); x.code = d.error; throw x; }
      return d;
    });
  }
  function get(name) { return fetch(prefix + '/api440/' + name, { credentials: 'same-origin', cache: 'no-store' }).then(answer); }
  function post(name, body) {
    return fetch(prefix + '/api440/' + name, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body || {}) }).then(answer);
  }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return d.toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  var ROLES = { administrateur: 'Administrateur', avocat: 'Avocat', assistant: 'Assistant(e)', agent: 'Agent' };

  /* Pastille du menu (toutes les pages) */
  var navLink = document.querySelector('nav a[href$="/messagerie"]');
  function badge() {
    if (!navLink || document.hidden) return;
    get('m5650/nonlus').then(function (d) {
      var b = navLink.querySelector('.m50-badge');
      if (!d.non_lus) { if (b) b.remove(); return; }
      if (!b) { b = el('span', 'm50-badge'); navLink.appendChild(b); }
      b.textContent = d.non_lus > 99 ? '99+' : String(d.non_lus);
      b.setAttribute('aria-label', d.non_lus + ' message(s) non lu(s)');
    }).catch(function () { /* silencieux : la pastille est facultative */ });
  }
  if (navLink) {
    badge();
    setInterval(badge, 30000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) badge(); });
  }

  var chat = document.getElementById('m50-chat');
  if (!chat) return;
  var me = chat.getAttribute('data-me'), fil = chat.getAttribute('data-fil'), list = document.getElementById('m50-list');

  function renderList() {
    return get('m5650/fils').then(function (d) {
      list.textContent = '';
      if (!d.fils.length) { list.appendChild(el('li', 'm50-muted', 'Aucune conversation pour l’instant.')); return; }
      d.fils.forEach(function (f) {
        var current = String(f.id) === fil;
        var li = el('li', 'm50-item' + (current ? ' m50-current' : ''));
        var a = el('a'); a.href = location.pathname + '?fil=' + f.id;
        if (current) a.setAttribute('aria-current', 'page');
        a.appendChild(el('span', 'm50-item-title', f.titre));
        var kind = { direct: 'Conversation', groupe: 'Groupe' + (f.membres ? ' · ' + f.membres + ' membres' : ''), dossier: 'Fil du dossier' }[f.genre] || '';
        a.appendChild(el('span', 'm50-kind', kind));
        if (f.non_lus) { var n = el('span', 'm50-badge', String(f.non_lus)); n.setAttribute('aria-label', f.non_lus + ' non lu(s)'); a.appendChild(n); }
        if (f.dernier) a.appendChild(el('span', 'm50-last', f.dernier));
        li.appendChild(a); list.appendChild(li);
      });
    }).catch(function (e) { list.textContent = ''; list.appendChild(el('li', 'm50-muted', e.message)); });
  }
  renderList();
  setInterval(function () { if (!document.hidden) renderList(); }, 20000);
  if (!fil) return;

  var box = document.getElementById('m50-messages'), status = document.getElementById('m50-status'), last = 0, first = 0, seen = {}, busyLoad = false;
  var form = document.getElementById('m50-compose'), text = document.getElementById('m50-text');

  function tools(info) {
    var t = document.getElementById('m50-tools'); t.textContent = '';
    if (info.genre === 'dossier') {
      var b = el('button', 'ax-btn ghost', info.suivi ? 'Ne plus suivre' : 'Suivre ce fil'); b.type = 'button';
      b.setAttribute('aria-pressed', info.suivi ? 'true' : 'false');
      b.addEventListener('click', function () {
        post('m5650/suivre', { id: fil, suivre: !info.suivi }).then(function () { reload(); renderList(); badge(); })
          .catch(function (e) { status.textContent = e.message; });
      });
      t.appendChild(b);
      var a = el('a', 'ax-btn ghost', 'Page du dossier'); a.href = prefix + '/dossier?id=' + encodeURIComponent(info.dossier); t.appendChild(a);
    }
    if (info.genre === 'groupe') {
      var inside = {}; info.membres.forEach(function (m) { inside[m.owner] = true; });
      get('m5650/membres').then(function (d) {
        var free = d.membres.filter(function (m) { return !inside[m.owner]; });
        if (!free.length) return;
        var label = el('label', 'm50-add'); label.appendChild(document.createTextNode('Ajouter '));
        var sel = el('select'); free.forEach(function (m) { var o = el('option', null, m.nom); o.value = m.owner; sel.appendChild(o); });
        label.appendChild(sel); t.insertBefore(label, t.firstChild);
        var add = el('button', 'ax-btn ghost', 'Ajouter au groupe'); add.type = 'button';
        add.addEventListener('click', function () {
          post('m5650/groupe/membres', { id: fil, ajouter: [sel.value] }).then(function () { reload(); renderList(); })
            .catch(function (e) { status.textContent = e.message; });
        });
        t.insertBefore(add, label.nextSibling);
      }).catch(function () {});
      var q = el('button', 'ax-btn ghost', 'Quitter le groupe'); q.type = 'button';
      q.addEventListener('click', function () {
        if (!window.confirm('Quitter ce groupe ?')) return;
        post('m5650/groupe/membres', { id: fil, retirer: [me] }).then(function () { location.href = location.pathname; })
          .catch(function (e) { status.textContent = e.message; });
      });
      t.appendChild(q);
    }
  }

  function info(i) {
    document.getElementById('m50-title').textContent = i.titre;
    document.getElementById('m50-people').textContent = i.genre === 'dossier'
      ? 'Visible de tous les membres qui voient ce dossier.'
      : 'Membres : ' + i.membres.map(function (m) { return m.nom; }).join(', ');
    tools(i);
  }

  function item(m) {
    var li = el('li', 'm50-msg' + (m.moi ? ' m50-mine' : '') + (m.genre === 'avis' ? ' m50-notice' : ''));
    li.setAttribute('data-id', String(m.id));
    var head = el('p', 'm50-meta'); head.appendChild(el('strong', null, m.nom));
    head.appendChild(document.createTextNode(' · ' + (ROLES[m.role] || m.role) + ' · ' + when(m.quand)));
    li.appendChild(head);
    li.appendChild(el('p', 'm50-text' + (m.retire ? ' m50-removed' : ''), m.retire ? 'Message retiré par son auteur.' : m.texte));
    if (m.lien) { var a = el('a', 'm50-link', m.lien_libelle || 'Ouvrir'); a.href = prefix + m.lien; li.appendChild(a); }
    if (m.moi && !m.retire && m.genre !== 'avis') {
      var r = el('button', 'm50-remove', 'Retirer'); r.type = 'button';
      r.addEventListener('click', function () {
        if (!window.confirm('Retirer ce message ? Il restera signalé comme retiré.')) return;
        post('m5650/retirer', { message: m.id }).then(function () { reload(); }).catch(function (e) { status.textContent = e.message; });
      });
      li.appendChild(r);
    }
    return li;
  }

  function placeholder() {
    var empty = box.querySelector('.m50-placeholder');
    if (box.querySelector('.m50-msg')) { if (empty) empty.remove(); }
    else if (!empty) box.appendChild(el('li', 'm50-muted m50-placeholder', 'Aucun message : écrivez le premier.'));
  }

  function older(show) {   /* 5.6.51 (audit B04) : « Messages précédents », lot par lot */
    var btn = box.querySelector('.m50-older');
    if (!show) { if (btn) btn.remove(); return; }
    if (btn) return;
    var li = el('li', 'm50-older'); var b = el('button', 'ax-btn ghost', 'Messages précédents'); b.type = 'button';
    b.addEventListener('click', function () {
      b.disabled = true;
      get('m5650/fil?id=' + encodeURIComponent(fil) + '&avant=' + first).then(function (d) {
        var anchor = box.querySelector('.m50-msg'), height = box.scrollHeight;
        d.messages.forEach(function (m) { if (seen[m.id]) return; seen[m.id] = true; box.insertBefore(item(m), anchor); first = Math.min(first || m.id, m.id); });
        box.scrollTop += box.scrollHeight - height;
        older(d.plus_anciens);
      }).catch(function (e) { status.textContent = e.message; }).then(function () { b.disabled = false; });
    });
    li.appendChild(b); box.insertBefore(li, box.firstChild);
  }

  function load(full) {   /* lots successifs sans trou ; une seule requête à la fois */
    if (busyLoad) return Promise.resolve();
    busyLoad = true;
    function step(cursor, isFull) {
      return get('m5650/fil?id=' + encodeURIComponent(fil) + (isFull ? '' : '&apres=' + cursor)).then(function (d) {
        var stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        if (isFull) { box.textContent = ''; last = 0; first = 0; seen = {}; info(d.fil); older(d.plus_anciens); }
        d.messages.forEach(function (m) {
          if (seen[m.id]) return; seen[m.id] = true;
          box.appendChild(item(m)); last = Math.max(last, m.id); first = first ? Math.min(first, m.id) : m.id;
        });
        placeholder();
        if (stick || isFull) box.scrollTop = box.scrollHeight;
        if (d.plus_recents) return step(last, false);
        return d.messages.length;
      });
    }
    return step(last, full).then(function (n) { if (n && !full) { renderList(); badge(); } })
      .catch(function (e) { status.textContent = e.message; })
      .then(function () { busyLoad = false; });
  }
  function reload() { busyLoad = false; return load(true); }
  load(true).then(function () { badge(); renderList(); });
  setInterval(function () { if (!document.hidden) load(false); }, 8000);

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var value = text.value.trim(); if (!value) return;
    var button = form.querySelector('button'); button.disabled = true; status.textContent = 'Envoi…';
    post('m5650/ecrire', { id: fil, texte: value }).then(function (res) {
      text.value = ''; status.textContent = res.mentions && res.mentions.length ? 'Envoyé ; ' + res.mentions.length + ' membre(s) mentionné(s) prévenu(s).' : 'Envoyé.';
      return load(false);
    }).catch(function (e) { status.textContent = e.message; }).then(function () { button.disabled = false; text.focus(); });
  });
  text.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit')); }
  });
})();
