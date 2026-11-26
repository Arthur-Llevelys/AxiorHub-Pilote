/* AxiorHub 5.6.110 (lot D) — carte « Dictée sur ce poste » (Paramètres › Voix).
   Les boutons passent par le traitement commun des formulaires (m5-form). Ce script ne fait que deux choses : afficher la taille du
   modèle coché dans le bouton d'installation, et suivre un téléchargement en cours (état lu toutes les 1,5 s, barre de progression
   annoncée par aria-live, page rechargée à la fin). Aucun texte dicté ne passe par ici. */
(function () {
  'use strict';
  var carte = document.getElementById('dictee56110');
  if (!carte) return;
  var meta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = meta ? meta.content : '';

  function tailles() {
    var coche = carte.querySelector('input[data-d110-modele]:checked');
    Array.prototype.forEach.call(carte.querySelectorAll('[data-d110-taille]'), function (s) {
      s.hidden = !coche || s.getAttribute('data-d110-taille') !== coche.value;
    });
  }
  carte.addEventListener('change', function (ev) {
    if (ev.target && ev.target.hasAttribute && ev.target.hasAttribute('data-d110-modele')) tailles();
  });

  var zone = carte.querySelector('[data-d110-progression][data-en-cours]');
  if (!zone) return;
  var barre = zone.querySelector('progress'), texte = zone.querySelector('#d110-progression-texte'), echecs = 0;
  function suivre() {
    fetch(prefix + '/api440/m56110/dictee/etat', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('http_' + r.status);
      return r.json();
    }).then(function (d) {
      echecs = 0;
      var t = (d && d.telechargement) || {};
      if (!t.en_cours) { window.location.reload(); return; }
      if (barre && t.attendus) { barre.max = t.attendus; barre.value = t.recus || 0; }
      if (texte && typeof t.texte === 'string') texte.textContent = t.texte;
      window.setTimeout(suivre, 1500);
    }).catch(function () {
      if (++echecs < 20) window.setTimeout(suivre, 3000);
    });
  }
  window.setTimeout(suivre, 1500);
})();
