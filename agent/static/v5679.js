/* AxiorHub 5.6.79 — page Dossier : aperçu des documents dans le volet de droite.
   Un clic sur un document (ou sur une source « /documents/edit?path=… ») ouvre l'aperçu au lieu de quitter la page ; Ctrl/Cmd+clic
   garde le comportement du navigateur. Le HTML d'aperçu est produit et échappé par le serveur ; tout le reste passe par textContent. */
(function () {
  'use strict';
  var root = document.querySelector('.d79[data-d79-matter]');
  var pane = document.getElementById('d79-preview');
  if (!root || !pane) return;
  var prefixMeta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = prefixMeta ? prefixMeta.getAttribute('content') : '';
  var matter = root.getAttribute('data-d79-matter');
  var layout = root.querySelector('.d79-layout');
  var title = pane.querySelector('[data-d79-title]'), body = pane.querySelector('[data-d79-body]'), note = pane.querySelector('[data-d79-note]');
  var openLink = pane.querySelector('[data-d79-open]'), dl = pane.querySelector('[data-d79-dl]'), close = pane.querySelector('[data-d79-close]');
  var current = '', lastTrigger = null, seq = 0;

  function pathOf(a) {
    if (a.hasAttribute('data-d79-doc')) return a.getAttribute('data-d79-doc');
    try {
      var u = new URL(a.href, location.href);
      if (u.origin === location.origin && /\/documents\/edit$/.test(u.pathname)) return u.searchParams.get('path') || '';
    } catch (err) { return ''; }
    return '';
  }
  function show(open) {
    pane.hidden = !open;
    if (layout) layout.classList.toggle('d79-with-preview', open);
    document.documentElement.classList.toggle('d79-preview-open', open);
  }
  function reset() {
    body.replaceChildren();
    note.hidden = true; note.textContent = '';
    openLink.hidden = true; openLink.removeAttribute('href');
  }
  function load(path, trigger) {
    var mine = ++seq;
    current = path; lastTrigger = trigger || null;
    root.querySelectorAll('.d79-active-doc').forEach(function (n) { n.classList.remove('d79-active-doc'); });
    if (trigger) trigger.classList.add('d79-active-doc');
    reset();
    title.textContent = path.split('/').pop();
    var wait = document.createElement('p'); wait.className = 'ax-muted'; wait.textContent = 'Chargement de l’aperçu…'; body.append(wait);
    show(true);
    var q = 'matter=' + encodeURIComponent(matter) + '&path=' + encodeURIComponent(path);
    fetch(prefix + '/dossier/apercu?' + q, { credentials: 'same-origin' }).then(function (r) {
      return r.json().then(function (v) { if (!r.ok) throw new Error(v.message || v.error || 'Aperçu indisponible.'); return v; });
    }).then(function (v) {
      if (mine !== seq) return;
      body.replaceChildren();
      title.textContent = v.title || title.textContent;
      dl.href = v.download_url || '#';
      if (v.open_url) { openLink.href = v.open_url; openLink.hidden = false; }
      if (v.note) { note.textContent = v.note; note.hidden = false; }
      if (v.kind === 'pdf') {
        var frame = document.createElement('iframe'); frame.src = v.url; frame.title = 'Aperçu : ' + (v.title || ''); frame.className = 'd79-frame';
        body.append(frame);
      } else if (v.kind === 'image') {
        var img = document.createElement('img'); img.src = v.url; img.alt = v.title || ''; img.className = 'd79-img'; body.append(img);
      } else if (v.kind === 'html') {
        var doc = document.createElement('article'); doc.className = 'd79-docview'; doc.innerHTML = v.html; body.append(doc);   // HTML échappé par le serveur
      } else {
        var p = document.createElement('p'); p.className = 'ax-muted'; p.textContent = v.note || 'Aperçu indisponible.'; body.append(p);
      }
      body.scrollTop = 0;
    }).catch(function (err) {
      if (mine !== seq) return;
      body.replaceChildren();
      var p = document.createElement('p'); p.className = 'd79-warn'; p.textContent = err.message; body.append(p);
    });
  }
  root.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a');
    if (!a || !root.contains(a) || pane.contains(a) || ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    var path = pathOf(a);
    if (!path) return;
    ev.preventDefault();
    if (path === current && !pane.hidden) { show(false); current = ''; return; }
    load(path, a);
  });
  close.addEventListener('click', function () { show(false); current = ''; if (lastTrigger) lastTrigger.focus(); });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !pane.hidden && !document.querySelector('#ws-ai-dock:not([hidden])')) { show(false); current = ''; if (lastTrigger) lastTrigger.focus(); }
  });
})();
