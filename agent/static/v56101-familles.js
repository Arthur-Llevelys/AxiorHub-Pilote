/* AxiorHub 5.6.101 (lot F) : barres d'onglets des familles au clavier, compteur d'usage local (POST après affichage, jamais pendant
   le chargement de la page par le serveur), réglage et effacement du compteur (Paramètres › Administration › Fonctions peu utilisées). */
(function () {
  'use strict';
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  var csrf = meta('axiorhub-csrf');
  var messages = {};
  try { var raw = document.getElementById('l5631-msg'); if (raw) messages = JSON.parse(raw.textContent || '{}'); } catch (err) { messages = {}; }
  function msg(key, fallback) { return messages[key] || fallback; }

  /* ---- F02 : dans une barre de famille, flèches gauche et droite, Début et Fin déplacent le focus d'un onglet à l'autre ;
     Entrée ouvre l'onglet (lien ordinaire). Tab sort de la barre comme d'habitude. */
  Array.prototype.forEach.call(document.querySelectorAll('nav.f101-onglets'), function (nav) {
    nav.addEventListener('keydown', function (ev) {
      var links = Array.prototype.slice.call(nav.querySelectorAll('a')), i = links.indexOf(document.activeElement), j = -1;
      if (i < 0 || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      if (ev.key === 'ArrowRight') j = (i + 1) % links.length;
      else if (ev.key === 'ArrowLeft') j = (i - 1 + links.length) % links.length;
      else if (ev.key === 'Home') j = 0;
      else if (ev.key === 'End') j = links.length - 1;
      if (j >= 0) { ev.preventDefault(); links[j].focus(); }
    });
  });

  /* ---- F05 : une ouverture de fonction, signalée une fois par affichage. Seul l'identifiant de la fonction part (ni compte, ni
     adresse, ni contenu) ; la clé aléatoire évite de compter deux fois un envoi rejoué. sendBeacon ne pose pas d'en-tête : le jeton
     CSRF est dans le corps, contrôlé par le serveur avec l'origine. */
  var usage = document.getElementById('f101-usage');
  if (usage && csrf) {
    var cle = '';
    try { var octets = new Uint8Array(16); window.crypto.getRandomValues(octets); cle = Array.prototype.map.call(octets, function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); } catch (err) { cle = ''; }
    var corps = JSON.stringify({ fonction: usage.getAttribute('data-fonction'), csrf: csrf, cle: cle });
    var url = usage.getAttribute('data-url'), parti = false;
    try { if (navigator.sendBeacon) parti = navigator.sendBeacon(url, new Blob([corps], { type: 'text/plain;charset=UTF-8' })); } catch (err) { parti = false; }
    if (!parti && window.fetch) {
      try {
        fetch(url, { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: corps })
          .catch(function () { /* compteur facultatif : jamais d'erreur affichée */ });
      } catch (err) { /* idem */ }
    }
  }

  /* ---- Réglage et effacement du compteur (administrateur ; le serveur contrôle le rôle, la version et le jeton). */
  function poster(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body) }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { data._status = r.status; return data; });
    });
  }
  function dire(texte) { var zone = document.getElementById('f101-etat'); if (zone) zone.textContent = texte; }
  function apres(data) {
    if (data._status === 200) { window.location.reload(); return; }
    dire(data._status === 409 ? msg('js.f101.conflit', 'Le réglage a été modifié entre-temps : rechargez la page.') : (data.message || msg('js.f101.erreur', 'Action refusée.')));
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-f101-reglage]'), function (bouton) {
    bouton.addEventListener('click', function () {
      bouton.disabled = true;
      poster(bouton.getAttribute('data-url'), { actif: bouton.getAttribute('data-f101-reglage') === '1', version: Number(bouton.getAttribute('data-version') || 0) })
        .then(apres, function () { dire(msg('js.f101.erreur', 'Action refusée.')); }).then(function () { bouton.disabled = false; });
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-f101-effacer]'), function (bouton) {
    bouton.addEventListener('click', function () {
      if (!window.confirm(bouton.getAttribute('data-confirmer') || '')) return;
      bouton.disabled = true;
      poster(bouton.getAttribute('data-url'), { confirmer: true })
        .then(apres, function () { dire(msg('js.f101.erreur', 'Action refusée.')); }).then(function () { bouton.disabled = false; });
    });
  });
})();
