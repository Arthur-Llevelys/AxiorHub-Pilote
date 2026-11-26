/* AxiorHub 5.6.110 (lot S) — carte « Serveur de documents sur ce poste » (Paramètres › Documents et procédure).
   Les boutons passent par le traitement commun des formulaires (m5-form). Ce script ne fait qu'une chose : suivre une installation ou
   une désinstallation en cours (état des étapes lu toutes les 2 s, sans appel à Docker), montrer l'état de chaque étape (libellés déjà
   écrits et traduits par le serveur, seulement montrés ou masqués), puis recharger la page à la fin. Aucun secret ne passe par ici. */
(function () {
  'use strict';
  var carte = document.getElementById('documents56110');
  if (!carte) return;
  var zone = carte.querySelector('[data-ds110-tache][data-en-cours]');
  if (!zone) return;
  var meta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = meta ? meta.content : '';
  var echecs = 0;

  function montrer(etapes) {
    (etapes || []).forEach(function (x) {
      var li = zone.querySelector('li[data-etape="' + String(x.id).replace(/[^a-z_]/g, '') + '"]');
      if (!li) return;
      li.setAttribute('data-etat', x.etat);
      Array.prototype.forEach.call(li.querySelectorAll('[data-pour]'), function (s) {
        s.hidden = s.getAttribute('data-pour') !== x.etat;
      });
    });
  }
  function suivre() {
    fetch(prefix + '/api440/m56110/documents/etat?tache=1', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('http_' + r.status);
      return r.json();
    }).then(function (d) {
      echecs = 0;
      var t = (d && d.tache) || {};
      montrer(t.etapes);
      if (!t.en_cours) { window.location.reload(); return; }
      window.setTimeout(suivre, 2000);
    }).catch(function () {
      if (++echecs < 30) window.setTimeout(suivre, 4000);
    });
  }
  window.setTimeout(suivre, 2000);
})();
