/* AxiorHub 5.6.112 — Conversations IA : ouvre dans le cadre la conversation Open WebUI propre au dossier.
   Le serveur d'AxiorHub retrouve ou crée la conversation (route m56112/conversations/ouvrir, dossier relu, clé d'API de la personne
   jamais envoyée au navigateur) et rend l'adresse à ouvrir ; la page s'affiche sans attendre Open WebUI. Seule une adresse de la même
   origine que l'instance affichée par le serveur est acceptée. Tout texte venu du serveur passe par textContent. */
(function () {
  'use strict';
  var zone = document.querySelector('[data-cv112-dossier]');
  if (!zone || zone.__cv112) return;
  zone.__cv112 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var cadre = zone.querySelector('[data-cv112-cadre]'), etat = zone.querySelector('[data-cv112-etat]');
  var onglet = zone.querySelector('[data-cv112-onglet]');
  var origine = function (u) { try { return new URL(u, location.href).origin; } catch (e) { return ''; } };
  var attendue = onglet ? origine(onglet.getAttribute('href')) : '';
  var dire = function (t) { if (etat) etat.textContent = t; };
  var ouvrir = function (url) {
    if (!url || !attendue || origine(url) !== attendue) { dire('Adresse d’Open WebUI inattendue : rien n’a été ouvert.'); return; }
    if (cadre) cadre.setAttribute('src', url);
    if (onglet) onglet.setAttribute('href', url);
  };
  fetch(prefix + '/api440/m56112/conversations/ouvrir', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify({ dossier: zone.getAttribute('data-cv112-dossier') || '' })
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j || {} }; });
  }).then(function (x) {
    dire(typeof x.j.message === 'string' ? x.j.message : (x.ok ? '' : 'Ouverture impossible.'));
    if (x.ok) ouvrir(x.j.url);
  }).catch(function () { dire('AxiorHub ne répond pas : rechargez la page.'); });
})();
