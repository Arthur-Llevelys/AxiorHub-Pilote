/* AxiorHub 5.6.108 (lot S) — indicateur de synchronisation des postes : relu toutes les 20 secondes (lecture seule, aucune écriture). */
(function () {
  'use strict';
  if (window.ax108Sync) return;   // chargé par l'en-tête et par la rubrique : une seule relecture
  window.ax108Sync = true;
  var els = Array.prototype.slice.call(document.querySelectorAll('[data-s108-indicateur]'));
  if (!els.length || !window.fetch) return;
  var base = (document.querySelector('script[src*="v56108-sync.js"]') || {}).getAttribute
    ? document.querySelector('script[src*="v56108-sync.js"]').getAttribute('src').replace(/\/static\/v56108-sync\.js.*$/, '') : '';
  var ETATS = ['desactivee', 'seul', 'refusee', 'conflit', 'en_cours', 'absent', 'partiel', 'premier', 'a_jour'];
  function relire() {
    if (document.hidden) return;
    fetch(base + '/api440/m56108/sync/etat', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || ETATS.indexOf(d.etat) < 0 || typeof d.libelle !== 'string') return;
        els.forEach(function (el) {
          ETATS.forEach(function (x) { el.classList.remove('s108-' + x); });
          el.classList.add('s108-' + d.etat);
          el.textContent = window.ax5634Translate ? window.ax5634Translate(d.libelle) : d.libelle;
        });
      })
      .catch(function () { /* hors ligne : l'état affiché reste celui de la page */ });
  }
  setInterval(relire, 20000);
})();
