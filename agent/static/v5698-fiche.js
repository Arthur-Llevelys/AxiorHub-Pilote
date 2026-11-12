/* AxiorHub 5.6.98 — fiche du dossier : sous-onglets « Par type » / « Par répertoire » au clavier (flèches, Début, Fin ; un seul arrêt de
   tabulation), confirmation avant le retrait d'une partie ou d'une instance. Les envois restent ceux de v5697-dossiers.js (JSON, CSRF, clé
   d'idempotence). Aucun HTML venant du serveur n'est injecté ; aucun appel au modèle. Sans script, les sous-onglets restent des liens. */
(function () {
  'use strict';
  if (window.axh5698Fiche) return;
  window.axh5698Fiche = true;

  function onglets(liste) {
    var tabs = Array.prototype.slice.call(liste.querySelectorAll('[role="tab"]'));
    if (!tabs.length) return;
    var actif = tabs.filter(function (t) { return t.getAttribute('aria-selected') === 'true'; })[0] || tabs[0];
    tabs.forEach(function (t) { t.setAttribute('tabindex', t === actif ? '0' : '-1'); });
    liste.addEventListener('keydown', function (ev) {
      var i = tabs.indexOf(document.activeElement);
      if (i < 0) return;
      var cible = null;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') cible = tabs[(i + 1) % tabs.length];
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') cible = tabs[(i - 1 + tabs.length) % tabs.length];
      else if (ev.key === 'Home') cible = tabs[0];
      else if (ev.key === 'End') cible = tabs[tabs.length - 1];
      else if (ev.key === ' ' || ev.key === 'Spacebar') { ev.preventDefault(); document.activeElement.click(); return; }
      if (!cible) return;
      ev.preventDefault();
      tabs.forEach(function (t) { t.setAttribute('tabindex', t === cible ? '0' : '-1'); });
      cible.focus();
    });
  }

  function demarrer() {
    Array.prototype.forEach.call(document.querySelectorAll('.d98-sousonglets[role="tablist"]'), onglets);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer); else demarrer();

  /* Retrait : confirmation avant l'envoi (phase de capture : avant le gestionnaire commun des formulaires d97). */
  document.addEventListener('submit', function (ev) {
    var form = ev.target && ev.target.closest && ev.target.closest('form[data-d98-confirmer]');
    if (!form) return;
    if (!window.confirm(form.getAttribute('data-d98-confirmer'))) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }
  }, true);
})();
