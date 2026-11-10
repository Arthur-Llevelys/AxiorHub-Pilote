/* AxiorHub 5.6.96 — Courriels, vue « À traiter avec le Pilote » (lot courriels, C02 / I03).
   Les décisions passent par les formulaires communs (v500.js, v5689-formulaires.js : un seul envoi, erreur près du champ, 409 sans perte de saisie).
   Ici : lecture à la demande du message source, du fil et des pièces (texte brut, via textContent : aucun HTML de courriel, aucune image distante),
   et focus dans le premier champ quand un geste du menu « Suivi » s'ouvre. */
(function () {
  'use strict';
  var racine = document.getElementById('cx96');
  if (!racine) return;
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  function el(tag, cls, texte) { var n = document.createElement(tag); if (cls) n.className = cls; if (texte !== undefined) n.textContent = texte; return n; }
  function lire(route, params) {
    return fetch(prefix + '/api440/m5689/courriels/' + route + '?' + new URLSearchParams(params).toString(), { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok || d.error) { throw new Error(d.message || 'Lecture impossible.'); } return d; }); });
  }
  function quand(iso) { var d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }); }
  function message(fil, m) {
    var bloc = el('section', 'cx96-message'); bloc.setAttribute('data-sens', m.sens);
    bloc.appendChild(el('h3', '', (m.sens === 'envoye' ? 'Envoyé par le cabinet' : 'Reçu') + ' — ' + quand(m.date)));
    bloc.appendChild(el('p', 'cx96-note', 'De : ' + m.de + (m.a ? ' · À : ' + m.a : '') + (m.cc ? ' · Cc : ' + m.cc : '')));
    bloc.appendChild(el('p', '', m.objet));
    bloc.appendChild(el('div', 'cx96-texte', m.texte + (m.tronque ? '\n[…]' : '')));
    (m.pieces || []).forEach(function (p) {
      var ligne = el('p', 'cx96-note'), b = el('button', 'ax-btn ghost', 'Lire « ' + p.name + ' »'), zone = el('div', 'cx96-texte');
      b.type = 'button';
      b.addEventListener('click', function () {
        b.disabled = true; zone.textContent = 'Lecture…';
        lire('piece', { fil: fil, index: p.index }).then(function (d) { zone.textContent = d.indisponible ? d.message : d.text + (d.truncated ? '\n[…]' : ''); })
          .catch(function (e) { zone.textContent = e.message; }).then(function () { b.disabled = false; });
      });
      ligne.appendChild(b); bloc.appendChild(ligne); bloc.appendChild(zone);
    });
    return bloc;
  }
  racine.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-cx96-lire]');
    if (!b) return;
    var genre = b.getAttribute('data-cx96-lire'), fil = b.getAttribute('data-fil');
    var zone = b.parentNode.querySelector('[data-cx96-zone="' + genre + '"]');
    if (!zone) return;
    b.disabled = true; zone.textContent = 'Lecture dans la messagerie…';
    lire(genre === 'fil' ? 'fil' : 'message', { fil: fil }).then(function (d) {
      if (d.indisponible) { zone.textContent = d.message || 'Messagerie indisponible : rien n’a été lu.'; return; }
      zone.textContent = '';
      var liste = genre === 'fil' ? d.messages : [d];
      liste.forEach(function (m) { zone.appendChild(message(fil, m)); });
      if (d.limite) zone.appendChild(el('p', 'cx96-note', 'Fil affiché en partie (' + d.limite.replace(/_/g, ' ') + ').'));
    }).catch(function (e) { zone.textContent = e.message; }).then(function () { b.disabled = false; });
  });
  /* Un geste ouvert : le premier champ à remplir reçoit le focus, au même endroit. */
  racine.addEventListener('toggle', function (ev) {
    var d = ev.target;
    if (!d.classList || !d.classList.contains('cx96-geste') || !d.open) return;
    var champ = d.querySelector('input:not([type=hidden]),select,textarea');
    if (champ) champ.focus();
  }, true);
})();
