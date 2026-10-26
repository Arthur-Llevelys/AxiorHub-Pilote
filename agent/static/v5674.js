/* AxiorHub 5.6.74 — « Associer un téléphone » : affiche le QR code d'invitation (image), l'adresse et le temps restant.
   Aucun HTML venant du serveur n'est injecté : l'image est une adresse data: et le texte est posé en textContent. */
(function () {
  'use strict';
  if (window.__axiorhub5674) return;
  window.__axiorhub5674 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var timer = null;

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function toast(message, bad) {
    var box = document.getElementById('ax-toast');
    if (!box) { box = el('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); document.body.appendChild(box); }
    var t = el('div', 'ax-toast' + (bad ? ' bad' : ''), message); box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function show(box, d) {
    box.textContent = '';
    box.hidden = false;
    var img = el('img', 'ap74-img'); img.src = d.qr; img.alt = 'QR code d’association (usage unique)'; img.width = 260; img.height = 260;
    box.appendChild(img);
    box.appendChild(el('p', '', 'Scannez ce code avec l’appareil photo du téléphone, puis touchez « Associer » sur la page qui s’ouvre.'));
    var left = el('p', 'ax-muted ap74-left'); box.appendChild(left);
    var link = el('p', 'ap74-link'); link.appendChild(el('span', 'ax-muted', 'Sans appareil photo : ')); link.appendChild(el('code', '', d.adresse));
    box.appendChild(link);
    var end = Date.parse(d.expire);
    if (timer) clearInterval(timer);
    function tick() {
      var s = Math.max(0, Math.round((end - Date.now()) / 1000));
      left.textContent = s ? 'Code valable encore ' + Math.floor(s / 60) + ' min ' + ('0' + (s % 60)).slice(-2) + ' s, une seule fois.'
        : 'Code expiré : cliquez de nouveau sur « Associer un téléphone ».';
      if (!s) { img.style.opacity = '0.25'; clearInterval(timer); timer = null; }
    }
    tick(); timer = setInterval(tick, 1000);
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-ap74="inviter"]');
    if (!b) return;
    var box = document.getElementById('ap74-qr');
    b.disabled = true;
    fetch(prefix + '/api440/m5674/inviter', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: '{}' })
      .then(function (r) { return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }); })
      .then(function (d) { if (d && d.error) throw new Error(d.message || d.error); show(box, d); })
      .catch(function (x) { toast(x.message, true); })
      .then(function () { b.disabled = false; });
  });
})();
