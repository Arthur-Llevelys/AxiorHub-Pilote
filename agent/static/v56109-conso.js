/* AxiorHub 5.6.109 (lot M, F18) — section « Consommation et coûts » de Paramètres › Modèles d'IA.
   - Filtres (période, fournisseur, modèle, canal, fonction, dossier, mission, utilisateur) : la vue est relue par
     ``GET m5689/budget/consommation/vue`` (fragment rendu et échappé par le serveur, mêmes droits et même calcul que la page) ; le lien
     « Exporter (CSV) » suit les filtres. Les filtres sont gardés pour le retour sur la page (sessionStorage).
   - « Prix et tokens », « Prix », « Tokens » : attribut ``data-affichage`` de la section (colonnes masquées par la feuille de style) ;
     la préférence reste dans le navigateur. Ce choix ne change rien à ce que le serveur enregistre pour les plafonds.
   - Détail des appels : chargé à l'ouverture du pli, paginé par les boutons ``data-m109-page``.
   Des lectures seulement : aucune requête d'écriture, aucun appel de modèle, aucune interrogation d'un fournisseur.
   Fonctions pures exportées pour Node. */
(function (racine) {
  'use strict';

  /* ------------------------------------------------------------------ fonctions pures */
  var CHAMPS = ['periode', 'debut', 'fin', 'fournisseur', 'modele', 'canal', 'fonction', 'dossier', 'mission', 'utilisateur'];
  var AFFICHAGES = ['tout', 'prix', 'tokens'];
  /* Paramètres de la requête : champs vides omis ; les dates ne partent que pour une période personnalisée ; ``page`` ouvre le détail. */
  function requete(valeurs, page) {
    var parts = [];
    CHAMPS.forEach(function (nom) {
      var v = String((valeurs || {})[nom] == null ? '' : valeurs[nom]).trim();
      if (!v) return;
      if ((nom === 'debut' || nom === 'fin') && (valeurs.periode || 'mois') !== 'perso') return;
      parts.push(encodeURIComponent(nom) + '=' + encodeURIComponent(v));
    });
    if (page) { parts.push('detail=1'); parts.push('page=' + encodeURIComponent(String(Math.max(1, parseInt(page, 10) || 1)))); }
    return parts.join('&');
  }
  function affichage(valeur) { return AFFICHAGES.indexOf(valeur) >= 0 ? valeur : 'tout'; }

  var api = { requete: requete, affichage: affichage, CHAMPS: CHAMPS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  /* ------------------------------------------------------------------ page */
  var doc = document;
  function garde(stock, cle, valeur) {
    try { if (valeur === undefined) return stock.getItem(cle); stock.setItem(cle, valeur); } catch (e) { /* stockage refusé : la page reste utilisable */ }
    return null;
  }
  function meta(nom) { var m = doc.querySelector('meta[name="' + nom + '"]'); return m ? m.content : ''; }
  function monter(section) {
    var route = section.getAttribute('data-m109');           /* nom de la route, sans préfixe : « m5689/budget/consommation » */
    var form = section.querySelector('[data-m109-filtres]');
    var vue = section.querySelector('[data-m109-vue]');
    var statut = section.querySelector('[data-m109-statut]');
    var lien = section.querySelector('[data-m109-export]');
    if (!route || !form || !vue) return;
    var base = meta('axiorhub-prefix') + '/api440/' + route;
    var enCours = 0;

    function lire() {
      var v = {};
      CHAMPS.forEach(function (nom) { var c = form.elements[nom]; if (c) v[nom] = c.value; });
      return v;
    }
    function dire(texte) { if (statut) statut.textContent = texte || ''; }
    function dates() {
      var perso = (form.elements.periode || {}).value === 'perso';
      Array.prototype.forEach.call(form.querySelectorAll('[data-m109-perso]'), function (n) { n.hidden = !perso; });
    }
    function charger(page) {
      var valeurs = lire(), moi = ++enCours;
      vue.setAttribute('aria-busy', 'true');
      dire('');
      fetch(base + '/vue?' + requete(valeurs, page), { credentials: 'same-origin', headers: { 'Accept': 'text/html' } }).then(function (r) {
        return r.text().then(function (texte) { return { ok: r.ok, texte: texte }; });
      }).then(function (reponse) {
        if (moi !== enCours) return;                       /* une demande plus récente a remplacé celle-ci */
        vue.removeAttribute('aria-busy');
        if (!reponse.ok) {
          var message = 'Relevé indisponible pour le moment.';
          try { message = JSON.parse(reponse.texte).message || message; } catch (e) { /* réponse non JSON : message par défaut */ }
          dire(message);
          return;
        }
        vue.innerHTML = reponse.texte;                     /* fragment rendu et échappé par le serveur */
        if (lien) lien.setAttribute('href', base + '/export?' + requete(valeurs));
        garde(racine.sessionStorage, 'm109-filtres', JSON.stringify(valeurs));
      }, function () {
        if (moi !== enCours) return;
        vue.removeAttribute('aria-busy');
        dire('Connexion au serveur interrompue.');
      });
    }

    form.addEventListener('submit', function (e) { e.preventDefault(); charger(0); });
    if (form.elements.periode) form.elements.periode.addEventListener('change', dates);
    vue.addEventListener('click', function (e) {
      var cible = e.target && e.target.closest ? e.target.closest('[data-m109-page]') : null;
      if (!cible) return;
      e.preventDefault();
      charger(cible.getAttribute('data-m109-page'));
    });
    vue.addEventListener('toggle', function (e) {            /* détail chargé à l'ouverture du pli, une seule fois par vue */
      var pli = e.target;
      if (pli && pli.hasAttribute && pli.hasAttribute('data-m109-detail') && pli.open && !pli.hasAttribute('data-m109-charge')) charger(1);
    }, true);
    Array.prototype.forEach.call(section.querySelectorAll('input[name="m109-affichage"]'), function (radio) {
      radio.addEventListener('change', function () {
        if (!radio.checked) return;
        section.setAttribute('data-affichage', affichage(radio.value));
        garde(racine.localStorage, 'm109-affichage', affichage(radio.value));
      });
    });

    var choix = affichage(garde(racine.localStorage, 'm109-affichage'));
    section.setAttribute('data-affichage', choix);
    Array.prototype.forEach.call(section.querySelectorAll('input[name="m109-affichage"]'), function (radio) { radio.checked = radio.value === choix; });
    var anciens = null;
    try { anciens = JSON.parse(garde(racine.sessionStorage, 'm109-filtres') || 'null'); } catch (e) { anciens = null; }
    if (anciens && requete(anciens) !== requete(lire())) {   /* retour sur la page : mêmes filtres, même vue */
      CHAMPS.forEach(function (nom) { var c = form.elements[nom]; if (c && anciens[nom] != null) c.value = anciens[nom]; });
      dates();
      charger(0);
    } else {
      dates();
    }
  }
  function demarrer() { Array.prototype.forEach.call(doc.querySelectorAll('[data-m109]'), monter); }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})(typeof window !== 'undefined' ? window : globalThis);
