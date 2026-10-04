/* AxiorHub 5.5.0 — validation des habitudes de style (page « Mon style » et carte du poste de pilotage). Texte saisi envoyé tel quel en JSON. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function toast(message, bad) {
    var box = document.getElementById('ax-toast');
    if (!box) { box = document.createElement('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); document.body.appendChild(box); }
    var t = document.createElement('div'); t.className = 'ax-toast' + (bad ? ' bad' : ''); t.textContent = message;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 4500);
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-habit]'); if (!b) return;
    var id = b.getAttribute('data-habit'), action = b.getAttribute('data-action');
    var area = document.getElementById('s550-t-' + id);
    b.disabled = true;
    fetch(prefix + '/api440/m550/habit', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ id: id, action: action, text: area ? area.value : '' }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.message || d.error);
        toast(d.message);
        var card = b.closest('.s550-habit'); if (card) card.remove();
        setTimeout(function () { location.reload(); }, 900);
      }).catch(function (err) { toast(err.message, true); b.disabled = false; });
  });
})();
