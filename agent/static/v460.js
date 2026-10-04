/* AxiorHub 4.6.0 - cockpit (touches 1 à 5) et décisions sur les faits. Aucun HTML venant du serveur n'est injecté. */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  /* Cockpit : touches 1 à 5 ouvrent l'action principale de la carte. */
  var cards = document.querySelectorAll('[data-ck-main]');
  if (cards.length) {
    document.addEventListener('keydown', function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      var t = ev.target;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      var n = parseInt(ev.key, 10);
      if (n >= 1 && n <= cards.length) { ev.preventDefault(); cards[n - 1].click(); }
    });
  }

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { if (bad) window.alert(message); return; }
    var el = document.createElement('div');
    el.className = 'ax-toast' + (bad ? ' bad' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, bad ? 9000 : 4500);
  }
  function post(name, body) {
    return fetch(prefix + '/api440/' + name, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    }).then(function (r) { return r.json().catch(function () { return { error: 'x', message: 'Réponse inattendue.' }; }); })
      .then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }

  var facts = $('#faits');
  if (!facts) return;
  var matter = facts.getAttribute('data-matter');
  facts.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button');
    if (!btn) return;
    if (btn.id === 'fc-scan') {
      post('fact/scan', { matter: matter }).then(function () {
        $('#fc-scan-result').textContent = 'Recherche lancée : actualisez la page dans un instant.';
      }).catch(function (e) { toast(e.message, true); });
      return;
    }
    var item = btn.closest('.fc-fact');
    if (!item) return;
    var id = item.getAttribute('data-id');
    var done = function (msg) { toast(msg); setTimeout(function () { location.reload(); }, 600); };
    if (btn.classList.contains('fc-validate')) {
      post('fact/validate', { matter: matter, id: id }).then(function () { done('Fait validé.'); }).catch(function (e) { toast(e.message, true); });
    } else if (btn.classList.contains('fc-correct')) {
      var ta = $('.fc-edit', item);
      if (ta.hidden) { ta.hidden = false; ta.focus(); btn.textContent = 'Enregistrer la correction'; return; }
      var text = ta.value.trim();
      if (!text) { toast('Le texte corrigé est vide.', true); return; }
      post('fact/validate', { matter: matter, id: id, text: text }).then(function () { done('Fait corrigé et validé.'); }).catch(function (e) { toast(e.message, true); });
    } else if (btn.classList.contains('fc-reject')) {
      post('fact/reject', { matter: matter, id: id }).then(function () { done('Fait refusé : il ne sera plus utilisé.'); }).catch(function (e) { toast(e.message, true); });
    }
  });
})();
