/* AxiorHub 5.6.101 (lot P, P01-P04) — blocs différés d'Aujourd'hui.
   Chaque attente [data-p101-fragment] est remplie par GET /api440/m56101/aujourdhui/fragment?nom=… (lecture seule, mêmes droits que la page) :
   - au dépli quand elle est dans un <details> fermé ; à l'ouverture du panneau des comptes rendus quand elle est dans un <dialog> fermé ;
   - juste après l'affichage quand elle est visible (cartes de « Travail préparé », file en bloc visible).
   P04 : un fragment chargé est gardé pour l'onglet (sessionStorage, par compte et par préfixe, dix minutes) ; au retour par le bouton
   Précédent, il est remis tel quel et son pli rouvert, sans nouvelle requête. Une panne est dite (texte), jamais un vide présenté comme complet.
   Le HTML vient du serveur, construit et échappé par les mêmes fonctions que la page complète ; les messages passent par textContent. */
(function () {
  'use strict';
  var doc = document, root = window;
  if (root.__axiorhub56101p) return;
  root.__axiorhub56101p = true;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), compte = meta('axiorhub-compte') || 'cabinet';
  var messages = {};
  try { messages = JSON.parse((doc.getElementById('p101-messages') || {}).textContent || '{}'); } catch (e) { messages = {}; }
  function msg(k, defaut) { return messages[k] || defaut; }
  var DUREE = 10 * 60 * 1000;

  function cle(zone) { return 'ax101:' + compte + ':' + prefix + ':' + zone.getAttribute('data-p101-fragment') + ':' + (zone.getAttribute('data-p101-params') || ''); }
  function garder(zone, html) { try { root.sessionStorage.setItem(cle(zone), JSON.stringify({ t: Date.now(), html: html })); } catch (e) { /* navigation privée */ } }
  function repris(zone) {
    try {
      var v = JSON.parse(root.sessionStorage.getItem(cle(zone)) || 'null');
      return v && typeof v.html === 'string' && Date.now() - v.t < DUREE ? v.html : null;
    } catch (e) { return null; }
  }
  function retourArriere() {
    try { var n = root.performance.getEntriesByType('navigation')[0]; return !!n && n.type === 'back_forward'; } catch (e) { return false; }
  }

  function apres(zone) {
    var nom = zone.getAttribute('data-p101-fragment');
    if (nom === 'routine' && typeof root.ax5652Init === 'function') root.ax5652Init(zone);       // clé de demande, choix des dossiers
    if (nom === 'travail') {                                                                        // filtre choisi gardé pour l'onglet
      var garde = null;
      try { garde = root.sessionStorage.getItem('ui89-filtre:' + root.location.pathname); } catch (e) { garde = null; }
      var b = garde && zone.querySelector('[role=tab][data-filtre="' + garde.replace(/[^a-z]/g, '') + '"]');
      if (b && b.getAttribute('aria-selected') !== 'true') b.click();
    }
    doc.dispatchEvent(new CustomEvent('ax101:fragment', { detail: { nom: nom } }));
  }
  function poser(zone, html) {
    zone.innerHTML = html;
    zone.setAttribute('data-p101-etat', 'charge');
    zone.removeAttribute('aria-busy');
    if (!zone.textContent.trim() && !zone.querySelector('form, input, button')) {
      var vide = doc.createElement('p'); vide.className = 'p101-attente'; vide.textContent = msg('vide', 'Rien à afficher pour l’instant.'); zone.append(vide);
    }
    apres(zone);
  }

  function charger(zone) {
    var etat = zone.getAttribute('data-p101-etat');
    if (etat === 'charge' || etat === 'encours') return Promise.resolve();
    zone.setAttribute('data-p101-etat', 'encours');
    zone.setAttribute('aria-busy', 'true');
    var attente = zone.querySelector('.p101-attente');
    if (attente && attente.firstChild && attente.firstChild.nodeType === 3) attente.firstChild.textContent = msg('chargement', 'Chargement…') + ' ';
    var params = zone.getAttribute('data-p101-params');
    var adresse = prefix + '/api440/m56101/aujourdhui/fragment?nom=' + encodeURIComponent(zone.getAttribute('data-p101-fragment')) + (params ? '&' + params : '');
    return root.fetch(adresse, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok || v.error) throw new Error(v.message || v.error || ('HTTP ' + r.status)); return v; }); })
      .then(function (v) { poser(zone, v.html || ''); garder(zone, v.html || ''); })
      .catch(function (e) {
        zone.setAttribute('data-p101-etat', 'panne');
        zone.removeAttribute('aria-busy');
        if (attente && attente.firstChild && attente.firstChild.nodeType === 3) attente.firstChild.textContent = msg('panne', 'Chargement impossible pour l’instant.') + ' (' + e.message + ') ';
      });
  }

  function fermeAuDessus(zone) {
    for (var n = zone.parentElement; n; n = n.parentElement) {
      if ((n.tagName === 'DETAILS' || n.tagName === 'DIALOG') && !n.open) return n;
    }
    return null;
  }
  function chargerDans(conteneur) {
    var faits = [];
    Array.prototype.forEach.call(conteneur.querySelectorAll('[data-p101-fragment]'), function (z) { if (!fermeAuDessus(z)) faits.push(charger(z)); });
    return Promise.all(faits);
  }

  // ouverture d'un pli : ses attentes visibles sont chargées
  doc.addEventListener('toggle', function (ev) {
    var pli = ev.target;
    if (pli instanceof HTMLDetailsElement && pli.open) chargerDans(pli);
  }, true);
  // « Lire le compte rendu », « Voir » : le panneau des comptes rendus s'ouvre ; son contenu est chargé puis l'onglet demandé est montré
  doc.addEventListener('click', function (ev) {
    var vue = ev.target.closest && ev.target.closest('[data-tab-open]');
    var rapports = doc.getElementById('a89-rapports');
    if (!vue || !rapports) return;
    var zones = rapports.querySelectorAll('[data-p101-fragment]:not([data-p101-etat="charge"])');
    if (!zones.length) return;
    var onglet = vue.getAttribute('data-tab-open');
    Promise.all(Array.prototype.map.call(zones, charger)).then(function () {
      var b = rapports.querySelector('[data-tab="' + String(onglet).replace(/[^a-z0-9_-]/gi, '') + '"]');
      if (b) b.click();
    });
  }, true);

  function demarrer() {
    var arriere = retourArriere();
    Array.prototype.forEach.call(doc.querySelectorAll('[data-p101-fragment]'), function (z) {
      var html = arriere ? repris(z) : null;
      if (html !== null) {                                  // P04 : retour par Précédent, rien n'est redemandé
        poser(z, html);
        for (var pli = fermeAuDessus(z); pli && pli.tagName === 'DETAILS'; pli = fermeAuDessus(pli)) pli.open = true;   // plis rouverts comme avant
        return;
      }
      if (!fermeAuDessus(z)) charger(z);
    });
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})();
