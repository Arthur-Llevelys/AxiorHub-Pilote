/* AxiorHub 5.6.89 — dossier structuré : décision sur un élément et lecture d’un document. Le serveur décide de tout ; aucun HTML reçu n'est injecté, seuls des textes. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function poster(route, data) {
    return fetch(prefix + '/api440/m5689/' + route, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(data) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); });
  }
  function dire(el, texte) { if (el) el.textContent = texte; }

  document.addEventListener('click', function (ev) {
    var bouton = ev.target.closest && ev.target.closest('.d89-actions button[data-action]');
    if (!bouton) return;
    var zone = bouton.closest('.d89-actions'), section = bouton.closest('.d89-dossier');
    var etat = section && section.querySelector('.d89-etat');
    var action = bouton.getAttribute('data-action');
    var data = { action: action, revision: Number(zone.getAttribute('data-revision')), cle: cle() };
    if (action === 'corriger') {
      var texte = window.prompt('Texte corrigé (une nouvelle révision sera créée, l’ancienne reste consultable) :');
      if (!texte) return;
      data.statement = texte;
    }
    if (action === 'valider' && zone.getAttribute('data-passage') === '1') {
      if (!window.confirm('Avez-vous relu le passage dans sa source ? Valider confirme l’enregistrement, pas la vérité de ce qui est affirmé.')) return;
      data.passage_relu = true;
    }
    bouton.disabled = true;
    poster('assertions/' + encodeURIComponent(zone.getAttribute('data-element')) + '/validation', data).then(function (r) {
      if (!r.ok) { bouton.disabled = false; dire(etat, r.data.message || 'Décision non enregistrée.'); return; }
      dire(etat, 'Décision enregistrée. Rechargez la page pour voir la nouvelle révision.');
    }).catch(function () { bouton.disabled = false; dire(etat, 'Réseau indisponible : rien n’a été confirmé.'); });
  });

  // Lecture d'un document : le serveur importe la version, puis un travail suivi extrait les propositions.
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form.d89-lecture');
    if (!form) return;
    ev.preventDefault();
    var etat = form.querySelector('.d89-etat'), dossier = encodeURIComponent(form.getAttribute('data-matter'));
    dire(etat, 'Import de la version du document.');
    poster('dossiers/' + dossier + '/sources/importer', { path: form.querySelector('select').value }).then(function (r) {
      if (!r.ok) { dire(etat, r.data.message || 'Document non importé.'); return null; }
      return poster('dossiers/' + dossier + '/sources/' + encodeURIComponent(r.data.version_id) + '/extraire', { cle: cle() });
    }).then(function (r) {
      if (r) dire(etat, r.ok ? r.data.message : (r.data.message || 'Lecture non lancée.'));
    }).catch(function () { dire(etat, 'Réseau indisponible : rien n’a été confirmé.'); });
  });
}());
