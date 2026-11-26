/* AxiorHub 5.6.110 (lot G) — Google sur un poste.
   - « Copier » : copie l'adresse de retour du poste (calculée par le serveur, jamais par la page).
   - Après « Connecter Gmail » ou « Autoriser Google Calendar » : si la page Google s'est ouverte dans le navigateur du système
     (fenêtre Ubuntu), cette fenêtre attend le retour déposé par Google (lecture seule, sa propre session), puis ouvre la route qui
     termine la connexion avec tous les contrôles du serveur. Sous Windows, la fenêtre part chez Google : rien ne tourne ici.
   Adresses lues dans les attributs posés par le serveur ; aucune donnée envoyée. Dix minutes au plus, comme la demande elle-même. */
(function () {
  'use strict';
  var minuterie = null;

  function zones() { return Array.prototype.slice.call(document.querySelectorAll('.g110-poste[data-g110-attente][data-g110-terminer]')); }
  function dire(z, texte) { var s = z.querySelector('.g110-statut'); if (s) { s.textContent = texte; } }

  function verifier(z) {
    return fetch(z.getAttribute('data-g110-attente'), { credentials: 'same-origin', cache: 'no-store', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) {
        if (j && j.etat === 'recu') {
          if (minuterie) { window.clearInterval(minuterie); minuterie = null; }
          dire(z, 'Réponse de Google reçue : fin de la connexion…');
          window.location.assign(z.getAttribute('data-g110-terminer'));
          return true;
        }
        return false;
      })
      .catch(function () { return false; });
  }

  function attendre(z) {
    if (minuterie) { return; }
    var fin = Date.now() + 10 * 60 * 1000;
    dire(z, 'En attente de la réponse de Google…');
    minuterie = window.setInterval(function () {
      if (Date.now() > fin) {
        window.clearInterval(minuterie); minuterie = null;
        dire(z, 'Pas de réponse de Google depuis dix minutes : recommencez la connexion.');
        return;
      }
      verifier(z);
    }, 3000);
  }

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-g110-copier]');
    if (b) {
      var code = document.getElementById(b.getAttribute('data-g110-copier'));
      if (!code) { return; }
      var fini = function () { b.textContent = 'Copié'; window.setTimeout(function () { b.textContent = 'Copier'; }, 2000); };
      var choisir = function () {
        var r = document.createRange(); r.selectNodeContents(code);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(code.textContent).then(fini, choisir); } else { choisir(); }
      return;
    }
    zones().forEach(function (z) {
      var section = z.closest('section') || document;
      var declencheur = ev.target.closest && ev.target.closest(z.getAttribute('data-g110-declencheur') || 'form[data-cx-oauth] button[type=submit]');
      if (declencheur && section.contains(declencheur)) { attendre(z); }
    });
  }, true);

  window.addEventListener('focus', function () { zones().forEach(function (z) { if (minuterie) { verifier(z); } }); });
}());
