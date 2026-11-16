/* AxiorHub 5.6.103 (lot L) — liaison entre postes dans la Messagerie instantanée : présence des postes reliés, messages en attente
   de remise (poste éteint nommé), pièce jointe (25 Mo au plus), présence de ce poste. Aucun HTML venant du serveur : tout passe par
   textContent. Lecture seule hors des deux actions explicites (joindre un fichier, changer sa présence). */
(function () {
  'use strict';
  var box = document.getElementById('l103-fil');
  if (!box) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix') || '/agent-courriel', csrf = meta('axiorhub-csrf');
  var fil = box.getAttribute('data-fil'), max = parseInt(box.getAttribute('data-max') || '0', 10) || 26214400;
  if (!fil) return;
  var PRESENCES = { en_ligne: 'en ligne', absent: 'absent', hors_ligne: 'hors ligne' };
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function answer(r) {
    return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
      if (!r.ok || (d && d.error)) { var x = new Error((d && d.message) || 'Le serveur a refusé la demande : rien n’a été enregistré.'); throw x; }
      return d;
    });
  }
  function get(name) { return fetch(prefix + '/api440/m56103/liaison/' + name, { credentials: 'same-origin', cache: 'no-store' }).then(answer); }
  function cle() {
    var a = new Uint8Array(18); (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function when(iso) {
    var d = new Date(iso); if (isNaN(d.getTime())) return '';
    return d.toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  var status = el('p', 'l103-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  var liste = el('ul', 'l103-postes'), attente = el('p', 'l103-attente');
  var outils = el('div', 'l103-outils');
  var label = el('label', 'ax-btn ghost l103-joindre', 'Joindre un fichier');
  var input = document.createElement('input'); input.type = 'file'; input.className = 'l103-fichier';
  label.appendChild(input);
  var presence = el('button', 'ax-btn ghost', 'Me signaler absent'); presence.type = 'button';
  outils.appendChild(label); outils.appendChild(presence);
  box.appendChild(liste); box.appendChild(attente); box.appendChild(outils); box.appendChild(status);
  var moiAbsent = false;

  function rendre(d) {
    if (!d.lie) { box.hidden = true; return; }
    box.hidden = false;
    liste.textContent = '';
    var eteints = [];
    d.postes.forEach(function (p) {
      var li = el('li', 'l103-poste');
      li.appendChild(el('strong', null, p.nom));
      var txt = PRESENCES[p.presence] || p.presence;
      if (p.presence === 'hors_ligne' && p.vu_le) txt += ' (vu le ' + when(p.vu_le) + ')';
      if (!p.appaire) txt = 'non appairé avec ce poste : rien ne lui est remis';
      li.appendChild(el('span', 'l103-presence l103-' + (p.appaire ? p.presence : 'hors_ligne'), ' · ' + txt));
      if (p.refus) li.appendChild(el('span', 'l103-refus', ' · un envoi a été refusé par ce poste (' + p.refus + ')'));
      liste.appendChild(li);
      if (p.attente && p.appaire) eteints.push(p.nom + ' (' + p.attente + ')');
    });
    attente.textContent = eteints.length
      ? 'En attente de remise : ' + eteints.join(', ') + '. Ces messages seront remis quand le poste concerné sera allumé et relié.'
      : '';
    attente.hidden = !eteints.length;
  }
  function charger() { return get('fil?id=' + encodeURIComponent(fil)).then(rendre).catch(function () { /* bandeau facultatif */ }); }
  function etat() {
    return get('etat').then(function (d) {
      moiAbsent = d.presence === 'absent';
      presence.textContent = moiAbsent ? 'Me signaler en ligne' : 'Me signaler absent';
    }).catch(function () { /* facultatif */ });
  }
  charger(); etat();
  setInterval(function () { if (!document.hidden) charger(); }, 10000);

  presence.addEventListener('click', function () {
    presence.disabled = true;
    fetch(prefix + '/api440/m56103/liaison/presence', { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ etat: moiAbsent ? 'en_ligne' : 'absent' }) })
      .then(answer).then(function (r) { status.textContent = r.message; return etat(); })
      .catch(function (e) { status.textContent = e.message; }).then(function () { presence.disabled = false; });
  });

  input.addEventListener('change', function () {
    var f = input.files && input.files[0]; if (!f) return;
    if (f.size > max) { status.textContent = 'Fichier trop volumineux : 25 Mo au plus par pièce jointe.'; input.value = ''; return; }
    if (!f.size) { status.textContent = 'Fichier vide : rien n’a été joint.'; input.value = ''; return; }
    status.textContent = 'Envoi de la pièce jointe…'; input.disabled = true;
    fetch(prefix + '/api440/m56103/liaison/piece?fil=' + encodeURIComponent(fil), { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf, 'X-Nom-Fichier': encodeURIComponent(f.name), 'X-Cle-Idempotence': cle() },
      body: f }).then(answer).then(function (r) {
        status.textContent = r.message;
        if (window.AX5650 && window.AX5650.recharger) window.AX5650.recharger();
        return charger();
      }).catch(function (e) { status.textContent = e.message; }).then(function () { input.disabled = false; input.value = ''; });
  });
})();
