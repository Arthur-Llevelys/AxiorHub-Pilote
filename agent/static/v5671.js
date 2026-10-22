/* AxiorHub 5.6.71 — formulaires des réponses préparées (déposer, écarter) et du réglage « sans dossier ».
   Tout formulaire portant data-c5671="<route>" est envoyé en JSON à /api440/<route> ; data-confirm demande confirmation,
   data-reload recharge la page après succès. Aucun HTML venant du serveur n'est injecté. */
(function () {
  'use strict';
  if (window.__axiorhub5671) return;
  window.__axiorhub5671 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');

  function toast(message, bad) {
    var box = document.getElementById('ax-toast');
    if (!box) { box = document.createElement('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
    var t = document.createElement('div');
    t.className = 'ax-toast' + (bad ? ' bad' : ''); t.textContent = message;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 6000);
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-c5671]');
    if (!form) return;
    ev.preventDefault();
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    var body = {};
    Array.prototype.forEach.call(form.elements, function (f) { if (f.name) body[f.name] = f.value; });
    var button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    fetch(prefix + '/api440/' + form.dataset.c5671, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) throw new Error(d.message || d.error);
      toast((d && d.message) || 'Enregistré.');
      if (form.hasAttribute('data-reload')) setTimeout(function () { location.reload(); }, 900);
    }).catch(function (err) {
      toast(err.message || 'Action impossible.', true);
    }).then(function () {
      if (button) { button.disabled = false; button.setAttribute('aria-busy', 'false'); }
    });
  }, true);
})();
