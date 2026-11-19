/* 5.6.108 (lot T) : Assistance métier — saisie conservée dans l'onglet (retour, fermeture du parcours, réponse lente), bouton désactivé
   pendant l'envoi (le reçu idempotent du serveur couvre aussi un double envoi), état d'une demande en attente relu toutes les 4 s.
   Aucune donnée ne quitte le navigateur ; le stockage de session peut être indisponible (navigation privée) : rien ne casse. */
(function () {
  'use strict';
  var PREFIXE = 'as108:';
  var EXCLUS = { csrf: 1, recu: 1, action: 1 };

  function lire(cle) {
    try { return JSON.parse(window.sessionStorage.getItem(PREFIXE + cle) || 'null'); } catch (e) { return null; }
  }
  function ecrire(cle, valeur) {
    try { window.sessionStorage.setItem(PREFIXE + cle, JSON.stringify(valeur)); } catch (e) { /* stockage indisponible */ }
  }

  function champs(form) {
    return Array.prototype.filter.call(form.elements, function (c) {
      return c.name && !EXCLUS[c.name] && c.type !== 'hidden' && c.type !== 'submit' && c.type !== 'button' && c.type !== 'file';
    });
  }

  function conserver(form) {
    var cle = form.getAttribute('data-as108-saisie');
    var sauve = lire(cle);
    if (sauve && typeof sauve === 'object') {
      champs(form).forEach(function (c) {
        if (!Object.prototype.hasOwnProperty.call(sauve, c.name)) return;
        if (c.tagName === 'SELECT') {
          if (Array.prototype.some.call(c.options, function (o) { return o.value === sauve[c.name]; })) c.value = sauve[c.name];
        } else if (!c.value || c.defaultValue === c.value) {
          c.value = sauve[c.name];
        }
      });
    }
    var noter = function () {
      var valeur = {};
      champs(form).forEach(function (c) { valeur[c.name] = c.value; });
      ecrire(cle, valeur);
    };
    form.addEventListener('input', noter);
    form.addEventListener('change', noter);
    form.addEventListener('submit', function () {
      noter();
      var bouton = form.querySelector('button:not([type=button])');
      if (bouton) {
        // désactivé après le départ de l'envoi (un bouton désactivé avant l'envoi retirerait la soumission)
        window.setTimeout(function () { bouton.disabled = true; form.setAttribute('aria-busy', 'true'); }, 0);
      }
    });
  }

  function attendre() {
    var etat = document.querySelector('[data-as108-attente]');
    if (!etat) {
      try { window.sessionStorage.removeItem(PREFIXE + 'relectures'); } catch (e) { /* rien */ }
      return;
    }
    var n = Number(lire('relectures') || 0);
    if (n >= 45) return;   // trois minutes au plus : ensuite, le bouton « Actualiser l'état » reste disponible
    ecrire('relectures', n + 1);
    window.setTimeout(function () {
      // pas de rechargement pendant une saisie en cours dans un autre formulaire de la page
      var actif = document.activeElement;
      if (actif && /^(TEXTAREA|INPUT|SELECT)$/.test(actif.tagName)) { attendre(); return; }
      window.location.reload();
    }, 4000);
  }

  function demarrer() {
    Array.prototype.forEach.call(document.querySelectorAll('form[data-as108-saisie]'), conserver);
    attendre();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer);
  else demarrer();
}());
