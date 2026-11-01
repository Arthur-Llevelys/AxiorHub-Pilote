/* AxiorHub 5.6.88 — Paramètres : suivi de la sauvegarde et de l'export, envoi par morceaux d'un export à vérifier.
   Les formulaires passent par le traitement commun (m5-form). Aucun HTML venant du serveur n'est injecté. */
(function () {
  'use strict';
  if (window.__axiorhub5688rev) return;
  window.__axiorhub5688rev = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');

  function json(route, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
    return fetch(prefix + '/api440/rev5688/' + route, opts).then(lire);
  }
  function lire(r) {
    return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
      if (d && d.error) throw new Error(d.message || d.error);
      return d;
    });
  }

  // Suivi : seulement quand la page montre un travail en cours ; recharge à la fin pour afficher le résultat.
  function suivre() {
    var attente = document.querySelector('[data-rev88-attente], [data-rev88-export]');
    var controle = window.__axiorhub5688controle;
    if (!attente && !controle) return;
    var timer = setInterval(function () {
      if (document.hidden) return;
      json('etat').then(function (d) {
        var actifs = d.exports.filter(function (x) { return x.statut === 'en_cours' || x.statut === 'en_attente'; });
        actifs.forEach(function (x) {
          var ligne = document.querySelector('[data-rev88-export="' + x.id + '"]');
          if (!ligne) return;
          ligne.querySelector('[data-rev88-etape]').textContent = x.etape || 'En attente';
          ligne.querySelector('progress').value = x.progression;
        });
        if (!actifs.length && !d.sauvegarde_en_cours && !d.controles_en_attente) { clearInterval(timer); location.reload(); }
      }).catch(function () { clearInterval(timer); });
    }, 4000);
  }

  // Vérification d'un export : le proxy du serveur limite la taille d'une requête, le fichier part donc par morceaux.
  function envoyer(zone) {
    var champ = zone.querySelector('[data-rev88-fichier]'), statut = zone.querySelector('[data-rev88-statut]'), bouton = zone.querySelector('[data-rev88-envoyer]');
    var fichier = champ.files && champ.files[0];
    if (!fichier) { statut.textContent = 'Choisissez d’abord un fichier ZIP.'; return; }
    bouton.disabled = true;
    json('depot/ouvrir', { taille: fichier.size }).then(function (depot) {
      var position = 0, numero = 0;
      function suite() {
        if (position >= fichier.size) return null;
        var morceau = fichier.slice(position, position + depot.morceau);
        return fetch(prefix + '/api440/rev5688/depot/morceau?id=' + encodeURIComponent(depot.id) + '&n=' + numero, {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf }, body: morceau
        }).then(lire).then(function () {
          position += morceau.size; numero += 1;
          statut.textContent = 'Envoi : ' + Math.floor(position * 100 / fichier.size) + ' %';
          return suite();
        });
      }
      return Promise.resolve(suite()).then(function () { return json('depot/verifier', { id: depot.id }); });
    }).then(function (res) {
      statut.textContent = res.message || 'Contrôle mis en file.';
      window.__axiorhub5688controle = true;
      suivre();
    }).catch(function (err) {
      statut.textContent = err.message || 'Envoi impossible.';
    }).then(function () { bouton.disabled = false; });
  }

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-rev88-envoyer]');
    if (b) envoyer(b.closest('[data-rev88-depot]'));
  });
  suivre();
})();
