/* 5.6.82 : recherche rapide partout (Ctrl+K), état de la liste conservé au retour, transfert de la connexion ChatGPT. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = function () { return meta('axiorhub-prefix') || ''; };
  var panel = null, timer = null, back = null, seq = 0;

  function build() {
    panel = document.createElement('div');
    panel.id = 'm82-quick'; panel.className = 'm82-quick'; panel.hidden = true;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-label', 'Recherche rapide');
    var box = document.createElement('div'); box.className = 'm82-quick-box';
    var input = document.createElement('input');
    input.type = 'search'; input.id = 'm82-quick-q'; input.placeholder = 'Dossier, client, fonction…'; input.autocomplete = 'off';
    input.setAttribute('aria-label', 'Rechercher un dossier ou une fonction'); input.setAttribute('aria-controls', 'm82-quick-list');
    var list = document.createElement('ul'); list.id = 'm82-quick-list'; list.setAttribute('aria-label', 'Résultats');
    var status = document.createElement('p'); status.className = 'm82-quick-status'; status.setAttribute('role', 'status');
    var more = document.createElement('a'); more.className = 'm82-quick-more'; more.textContent = 'Rechercher dans les pièces et les courriels';
    more.href = prefix() + '/rechercher';
    box.append(input, status, list, more); panel.append(box); document.body.append(panel);
    panel.addEventListener('click', function (ev) { if (ev.target === panel) closePanel(); });
    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { run(input.value); }, 140); });
    input.addEventListener('keydown', function (ev) {
      var links = Array.prototype.slice.call(list.querySelectorAll('a'));
      if (ev.key === 'Enter' && links.length) { ev.preventDefault(); links[0].click(); }
      if (ev.key === 'ArrowDown' && links.length) { ev.preventDefault(); links[0].focus(); }
    });
    list.addEventListener('keydown', function (ev) {
      var links = Array.prototype.slice.call(list.querySelectorAll('a')), i = links.indexOf(document.activeElement);
      if (ev.key === 'ArrowDown' && links[i + 1]) { ev.preventDefault(); links[i + 1].focus(); }
      if (ev.key === 'ArrowUp') { ev.preventDefault(); (links[i - 1] || input).focus(); }
    });
  }

  function run(value) {
    var list = document.getElementById('m82-quick-list'), status = panel.querySelector('.m82-quick-status'), mine = ++seq;
    if (value.trim().length < 2) { list.replaceChildren(); status.textContent = ''; return; }
    fetch(prefix() + '/api440/q5682/recherche?q=' + encodeURIComponent(value), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : { resultats: [] }; })
      .then(function (data) {
        if (mine !== seq) return;
        list.replaceChildren();
        (data.resultats || []).forEach(function (r) {
          var li = document.createElement('li'), a = document.createElement('a'), t = document.createElement('strong'), k = document.createElement('small');
          a.href = prefix() + r.url; t.textContent = r.titre; k.textContent = r.type + (r.detail ? ' · ' + r.detail : '');
          a.append(t, k); li.append(a); list.append(li);
        });
        status.textContent = (data.resultats || []).length ? (data.resultats.length + ' résultat(s)') : 'Aucun résultat.';
      }).catch(function () { if (mine === seq) status.textContent = 'Recherche indisponible.'; });
  }

  function openPanel() {
    if (!panel) build();
    back = document.activeElement; panel.hidden = false;
    var input = document.getElementById('m82-quick-q'); input.value = ''; run(''); input.focus();
  }
  function closePanel() { if (!panel || panel.hidden) return; panel.hidden = true; if (back && back.focus) back.focus(); }

  document.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && (ev.key === 'k' || ev.key === 'K')) { ev.preventDefault(); if (panel && !panel.hidden) closePanel(); else openPanel(); }
    if (ev.key === 'Escape') closePanel();
  });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('[data-m82-quick]');
    if (a && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey) { ev.preventDefault(); openPanel(); }
  });

  /* Liste des dossiers et autres listes filtrées : filtres, position et sélection retrouvés au retour (préférence de vue, sans donnée métier). */
  function viewKey() { return 'm82-vue:' + location.pathname + location.search; }
  function saveView() {
    try {
      var values = {};
      document.querySelectorAll('#ax-main input[type=search], #ax-main select[data-filter], #ax-main input[data-filter]').forEach(function (f, i) { values[f.id || f.name || i] = f.value; });
      sessionStorage.setItem(viewKey(), JSON.stringify({ y: window.scrollY, values: values }));
    } catch (e) { /* navigation privée : sans conséquence */ }
  }
  function restoreView() {
    var nav = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    if (!nav || nav.type !== 'back_forward') return;
    try {
      var saved = JSON.parse(sessionStorage.getItem(viewKey()) || 'null');
      if (!saved) return;
      document.querySelectorAll('#ax-main input[type=search], #ax-main select[data-filter], #ax-main input[data-filter]').forEach(function (f, i) {
        var v = saved.values[f.id || f.name || i];
        if (v !== undefined && v !== f.value) { f.value = v; f.dispatchEvent(new Event('input', { bubbles: true })); }
      });
      window.scrollTo(0, saved.y || 0);
    } catch (e) { /* idem */ }
  }
  window.addEventListener('pagehide', saveView);
  window.addEventListener('pageshow', restoreView);

  function el(tag, text, cls) { var n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
  function say(text) { var box = document.getElementById('ax-toast'); if (box) { box.textContent = text; } }
  function versioned(name) {
    var link = document.querySelector('link[href*="/static/"]'), found = link ? /[?&]v=([^&]+)/.exec(link.href) : null;
    return prefix() + '/static/' + name + (found ? '?v=' + found[1] : '');
  }

  /* Composants Carbon : chargés seulement sur les pages qui en affichent (fichier local, sans ressource externe). */
  document.addEventListener('DOMContentLoaded', function () {
    if (document.querySelector('cds-tag, cds-inline-notification')) {
      var s = document.createElement('script'); s.src = versioned('carbon5682.js'); s.defer = true; document.head.append(s);
    }
  });

  /* Lecteur PDF.js : page citée ouverte directement ; en cas d'échec, le lecteur du navigateur reprend. */
  var pdfLib = null;
  function loadPdf() {
    if (pdfLib) return pdfLib;
    pdfLib = new Promise(function (ok, ko) {
      var s = document.createElement('script'); s.src = versioned('pdfjs5682.js');
      s.onload = function () { window.pdfjsLib.GlobalWorkerOptions.workerSrc = versioned('pdf.worker5682.js'); ok(window.pdfjsLib); };
      s.onerror = ko; document.head.append(s);
    });
    return pdfLib;
  }
  window.ax82Pdf = function (body, v, page) {
    var box = el('div', undefined, 'm82-pdf'), bar = el('div', undefined, 'm82-pdf-bar'), view = el('div', undefined, 'm82-pdf-page');
    var prev = el('button', 'Page précédente', 'ax-btn ghost'), next = el('button', 'Page suivante', 'ax-btn ghost'), less = el('button', 'Réduire', 'ax-btn ghost'), more = el('button', 'Agrandir', 'ax-btn ghost');
    var num = document.createElement('input'), total = el('span'), canvas = document.createElement('canvas');
    [prev, next, less, more].forEach(function (b) { b.type = 'button'; });
    num.type = 'number'; num.min = 1; num.setAttribute('aria-label', 'Numéro de page'); total.setAttribute('role', 'status');
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', 'Page du document : ' + (v.title || ''));
    bar.append(prev, num, total, next, less, more); view.append(canvas); box.append(bar, view); body.append(box);
    var scale = 1.2, doc = null, task = null, current = page || 1;
    function fallback() {
      box.remove();
      var frame = document.createElement('iframe'); frame.src = v.url + (page ? '#page=' + page : ''); frame.title = 'Aperçu : ' + (v.title || ''); frame.className = 'd79-frame'; body.append(frame);
    }
    function render() {
      current = Math.min(Math.max(1, current), doc.numPages); num.value = current; total.textContent = 'sur ' + doc.numPages;
      prev.disabled = current <= 1; next.disabled = current >= doc.numPages;
      doc.getPage(current).then(function (p) {
        var vp = p.getViewport({ scale: scale }); canvas.width = Math.floor(vp.width); canvas.height = Math.floor(vp.height);
        if (task) task.cancel();
        task = p.render({ canvas: canvas, canvasContext: canvas.getContext('2d'), viewport: vp }); return task.promise;
      }).catch(function (err) { if (!err || err.name !== 'RenderingCancelledException') fallback(); });
    }
    prev.onclick = function () { current -= 1; render(); }; next.onclick = function () { current += 1; render(); };
    less.onclick = function () { scale = Math.max(.5, scale - .2); render(); }; more.onclick = function () { scale = Math.min(3, scale + .2); render(); };
    num.onchange = function () { current = Number(num.value) || 1; render(); };
    loadPdf().then(function (lib) { return lib.getDocument({ url: v.url, isEvalSupported: false, withCredentials: true }).promise; })
      .then(function (d) { doc = d; render(); }).catch(fallback);
    return true;
  };

  /* Liste et document : largeur réglable à la souris ou au clavier, retrouvée à la prochaine ouverture. */
  function setupSplit() {
    var layout = document.querySelector('.d79-layout'), pane = document.getElementById('d79-preview');
    if (!layout || !pane || document.querySelector('.m82-split')) return;
    var bar = el('button', undefined, 'm82-split'); bar.type = 'button'; bar.setAttribute('role', 'separator'); bar.setAttribute('aria-orientation', 'vertical');
    bar.setAttribute('aria-label', 'Largeur du document : flèches gauche et droite'); bar.hidden = true;
    pane.before(bar);
    function apply(px) {
      var width = Math.min(Math.max(320, px), layout.clientWidth - 320);
      layout.style.setProperty('--m82-pane', width + 'px'); bar.setAttribute('aria-valuenow', Math.round(width));
      try { localStorage.setItem('m82-pane', String(width)); } catch (e) { /* préférence facultative */ }
    }
    try { var saved = Number(localStorage.getItem('m82-pane')); if (saved) layout.style.setProperty('--m82-pane', saved + 'px'); } catch (e) { /* idem */ }
    new MutationObserver(function () { bar.hidden = pane.hidden; }).observe(pane, { attributes: true, attributeFilter: ['hidden'] });
    bar.addEventListener('pointerdown', function (ev) {
      bar.setPointerCapture(ev.pointerId);
      var move = function (m) { apply(layout.getBoundingClientRect().right - m.clientX); };
      var up = function () { bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); };
      bar.addEventListener('pointermove', move); bar.addEventListener('pointerup', up);
    });
    bar.addEventListener('keydown', function (ev) {
      var step = ev.key === 'ArrowLeft' ? 40 : (ev.key === 'ArrowRight' ? -40 : 0);
      if (step) { ev.preventDefault(); apply(pane.getBoundingClientRect().width + step); }
    });
  }

  /* File À décider / À relire : l'état du serveur fait foi ; une erreur laisse la carte et sa référence visibles (le toast la signale). */
  window.ax82CardDone = function (card, res) {
    var message = res && typeof res.message === 'string' ? res.message : 'Enregistré.';
    card.classList.add('m82-done'); card.append(el('p', message, 'm82-done-note'));
    var active = document.querySelector('.q51-tabs [aria-current] .q51-n, .q51-n');
    if (active && Number(active.textContent) > 0) active.textContent = String(Number(active.textContent) - 1);
    say(message);
    return true;
  };

  /* Assistant : un seul lanceur flottant, conversation vocale dans la barre du haut, dossier de la demande en clair. */
  function setupAssistant() {
    var live = document.getElementById('ws-ai-live'), actions = document.querySelector('.ws-topbar-actions');
    if (live && actions) actions.prepend(live);
    var select = document.getElementById('ws-ai-matter'), info = document.getElementById('m82-active');
    if (select && info) {
      var update = function () {
        var o = select.options[select.selectedIndex];
        info.textContent = o && o.value ? 'Dossier de cette demande : ' + o.textContent : 'Aucun dossier : la demande porte sur tout le cabinet.';
      };
      select.addEventListener('change', update); update();
    }
    var dock = document.getElementById('ws-ai-dock');
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && dock && !dock.hidden && !(panel && !panel.hidden)) { var c = document.getElementById('ws-ai-close'); if (c) c.click(); var l = document.getElementById('ws-ai-launcher'); if (l) l.focus(); }
    });
  }

  /* Chronologie : frise au-dessus de la liste. Mois ou année seuls : une plage, jamais un jour inventé. La liste reste la vue accessible. */
  var GROUPS = [['tout', 'Tout'], ['faits', 'Faits'], ['procedure', 'Procédure'], ['correspondances', 'Correspondances'], ['audiences', 'Audiences']];
  function groupOf(li) {
    var nature = (li.getAttribute('data-nature') || '').toLowerCase();
    if (/audience|plaidoirie/.test(nature)) return 'audiences';
    if (/courrier|courriel|correspond|lettre/.test(nature)) return 'correspondances';
    return li.classList.contains('d51-ev-litige') ? 'faits' : 'procedure';
  }
  function span(li) {
    var d = li.getAttribute('data-date') || '', p = li.getAttribute('data-precision') || 'jour', fr = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(d);
    if (fr) d = fr[3] + '-' + fr[2] + '-' + fr[1];
    var y = Number(d.slice(0, 4)), m = Number(d.slice(5, 7)) || 1;
    if (!y) return null;
    if (d.length === 4) p = 'annee';
    if (d.length === 7) p = 'mois';
    if (p === 'annee') return [Date.UTC(y, 0, 1), Date.UTC(y + 1, 0, 1)];
    if (p === 'mois') return [Date.UTC(y, m - 1, 1), Date.UTC(y, m, 1)];
    var t = Date.parse(d.slice(0, 10) + 'T00:00:00Z');
    return isNaN(t) ? null : [t, t];
  }
  function setupChrono() {
    var list = document.querySelector('ol.d51-chrono');
    if (!list) return;
    var items = Array.prototype.slice.call(list.querySelectorAll('li[data-date]')).filter(function (x) { return x.getAttribute('data-date'); });
    if (items.length < 2) return;
    var box = el('section', undefined, 'm82-frise'), tools = el('div', undefined, 'm82-frise-bar'), axis = el('div', undefined, 'm82-axis'), track = el('div', undefined, 'm82-track');
    box.setAttribute('aria-label', 'Frise chronologique'); axis.append(track); box.append(tools, axis);
    var zoom = 1, chosen = 'tout';
    function draw() {
      var shown = items.filter(function (li) { return chosen === 'tout' || groupOf(li) === chosen; });
      items.forEach(function (li) { li.hidden = shown.indexOf(li) < 0; });
      track.replaceChildren(); track.style.width = (zoom * 100) + '%';
      var spans = shown.map(span).filter(Boolean);
      if (!spans.length) return;
      var lo = Math.min.apply(null, spans.map(function (s) { return s[0]; })), hi = Math.max.apply(null, spans.map(function (s) { return s[1]; })), range = Math.max(hi - lo, 864e5);
      var pos = function (t) { return ((t - lo) / range) * 100; };
      for (var y = new Date(lo).getUTCFullYear(); y <= new Date(hi).getUTCFullYear(); y += 1) {
        var left = pos(Date.UTC(y, 0, 1));
        var tick = el('span', String(y), 'm82-year'); tick.style.left = Math.max(left, 0) + '%'; track.append(tick);
      }
      shown.forEach(function (li) {
        var s = span(li); if (!s) return;
        var mark = el('button', undefined, 'm82-mark'); mark.type = 'button'; mark.setAttribute('data-precision', s[1] > s[0] ? 'mois' : (li.getAttribute('data-precision') === 'jour' ? 'jour' : 'mois'));   // creux : date à confirmer
        mark.style.left = pos(s[0]) + '%'; if (s[1] > s[0]) mark.style.width = Math.max((s[1] - s[0]) / range * 100, .8) + '%';
        var text = li.textContent.trim().replace(/\s+/g, ' ').slice(0, 120); mark.title = text; mark.setAttribute('aria-label', text);
        mark.onclick = function () { list.querySelectorAll('.m82-hit').forEach(function (n) { n.classList.remove('m82-hit'); }); li.classList.add('m82-hit'); li.scrollIntoView({ block: 'center' }); };
        track.append(mark);
      });
    }
    GROUPS.forEach(function (g) {
      var b = el('button', g[1], 'ax-btn ghost'); b.type = 'button'; b.setAttribute('aria-pressed', String(g[0] === chosen));
      b.onclick = function () { chosen = g[0]; tools.querySelectorAll('[aria-pressed]').forEach(function (x) { x.setAttribute('aria-pressed', 'false'); }); b.setAttribute('aria-pressed', 'true'); draw(); };
      tools.append(b);
    });
    var minus = el('button', 'Vue d’ensemble', 'ax-btn ghost'), plus = el('button', 'Zoomer', 'ax-btn ghost');
    minus.type = plus.type = 'button'; minus.onclick = function () { zoom = Math.max(1, zoom / 2); draw(); }; plus.onclick = function () { zoom = Math.min(16, zoom * 2); draw(); };
    tools.append(plus, minus);
    list.before(box); draw();
  }

  /* Bordereau : glisser-déposer en plus de Monter et Descendre ; chaque déplacement s'annule. L'ordre est enregistré par identifiants stables. */
  function setupOrder() {
    var table = document.querySelector('.p5-table'), undo = document.getElementById('m82-undo');
    if (!table || !undo) return;
    var body = table.querySelector('tbody'), dragged = null, history = [];
    var keys = function () { return Array.prototype.map.call(body.querySelectorAll('tr[data-key]'), function (r) { return r.getAttribute('data-key'); }); };
    var refresh = function () { var box = table.querySelector('.p5-inc'); if (box) box.dispatchEvent(new Event('change', { bubbles: true })); };
    var remember = function () { history.push(keys()); undo.disabled = false; };
    body.querySelectorAll('tr[data-key]').forEach(function (r) { r.draggable = true; });
    table.addEventListener('click', function (ev) { if (ev.target.closest('.p5-up,.p5-down')) { history.push(keys()); undo.disabled = false; } }, true);
    body.addEventListener('dragstart', function (ev) { dragged = ev.target.closest('tr[data-key]'); if (dragged) { remember(); dragged.classList.add('m82-drag'); ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', dragged.getAttribute('data-key')); } });
    body.addEventListener('dragover', function (ev) {
      var over = ev.target.closest('tr[data-key]');
      if (!dragged || !over || over === dragged) return;
      ev.preventDefault();
      var box = over.getBoundingClientRect(); body.insertBefore(dragged, ev.clientY > box.top + box.height / 2 ? over.nextSibling : over);
    });
    body.addEventListener('dragend', function () { if (dragged) dragged.classList.remove('m82-drag'); dragged = null; refresh(); });
    undo.addEventListener('click', function () {
      var last = history.pop(); if (!last) return;
      last.forEach(function (k) { var r = body.querySelector('tr[data-key="' + CSS.escape(k) + '"]'); if (r) body.append(r); });
      undo.disabled = !history.length; refresh(); say('Déplacement annulé.');
    });
  }

  document.addEventListener('DOMContentLoaded', function () { setupSplit(); setupAssistant(); setupChrono(); setupOrder(); });

  /* Paquet de transfert : affiché sur place, jamais dans une adresse ni dans le stockage du navigateur. */
  document.addEventListener('submit', function (ev) {
    if (!ev.target.matches || !ev.target.matches('#m82-export')) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    var form = ev.target, status = form.querySelector('.m5-status');
    fetch(prefix() + '/api440/m5682/session/export', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') },
      body: JSON.stringify({ phrase: form.elements.phrase.value }) })
      .then(function (r) { return r.json(); }).then(function (data) {
        if (!data.paquet) { status.textContent = data.message || 'Transfert impossible.'; return; }
        var area = document.createElement('textarea'); area.readOnly = true; area.rows = 4; area.value = data.paquet; area.setAttribute('aria-label', 'Paquet chiffré à coller sur le serveur');
        form.append(area); status.textContent = data.message + ' Copiez ce paquet sur le serveur, puis fermez cette page.'; form.elements.phrase.value = '';
      }).catch(function () { status.textContent = 'Transfert impossible.'; });
  }, true);
})();
